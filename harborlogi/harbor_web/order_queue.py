"""Outbound order queue: create, reorder, and digest the queue that plans are computed against."""
import hashlib
import json

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Exists, Max, OuterRef
from django.utils import timezone

from harbor_web.coordination import serialized
from harbor_web.models import Audit, CellPlanDispatch, Command, Order
from harbor_web.service_common import Conflict, _role


TIERS = ((1, '긴급'), (2, '우선'), (3, '일반'))  # priority_group 1·2·3 (KAN-116); smaller leaves first
RUNNING_COMMAND_STATES = frozenset({'DISPATCHING', 'RECONCILED'})
RUNNING_CELL_PLAN_STATES = frozenset({'SUBMITTING', 'PARTIALLY_ADMITTED', 'QUEUED', 'RUNNING'})


def running_order_ids(order_ids):
    """Order pks that currently have an in-progress Command or CellPlanDispatch."""
    if not order_ids:
        return set()
    return set(
        Command.objects.filter(order_id__in=order_ids, state__in=RUNNING_COMMAND_STATES).values_list('order_id', flat=True)
    ) | set(
        CellPlanDispatch.objects.filter(
            order_id__in=order_ids, state__in=RUNNING_CELL_PLAN_STATES
        ).values_list('order_id', flat=True)
    )


def tier_of(group):
    """Groups above 3 predate the three tiers; they read and move as 일반."""
    return min(group, len(TIERS))


def _queue_policy(order):
    if order.kind != 'OUTBOUND':
        return
    peers = Order.objects.filter(kind='OUTBOUND', priority_group=order.priority_group).exclude(pk=order.pk)
    # A finished order has left the queue, so only open peers must share the tier's policy.
    if peers.filter(pk__in=open_outbound_orders()).exclude(tie_policy=order.tie_policy).exists():
        raise ValidationError('All members of a priority group must use the same policy.')
    if order.tie_policy == 'FIXED' and peers.filter(sequence=order.sequence).exists():
        raise ValidationError('Fixed group sequence must be unique.')


def request_snapshot(order):
    return dict(id=order.pk, cargo_id=order.cargo_id, cargo_code=order.cargo.code if order.cargo_id else None, kind=order.kind,
        priority_group=order.priority_group, sequence=order.sequence, tie_policy=order.tie_policy,
        title=order.title, voyage_code=order.voyage_code,
        cutoff_at=order.cutoff_at.isoformat() if order.cutoff_at else None,
        note=order.note, version=order.version)


def queue_snapshot():
    return [request_snapshot(order) for order in Order.objects.exclude(kind='REARRANGE').select_related('cargo').order_by('pk')]


@serialized
@transaction.atomic
def create_order(actor, *, cargo, kind, priority_group, sequence, tie_policy, note='', title='', voyage_code='', cutoff_at=None):
    _role(actor, 'operator')
    obj = Order(created_by=actor, cargo=cargo, kind=kind, priority_group=priority_group,
                sequence=sequence, tie_policy=tie_policy, note=note, title=title,
                voyage_code=voyage_code or '', cutoff_at=cutoff_at)
    _queue_policy(obj)
    obj.full_clean()
    obj.save()
    Audit.objects.create(actor=actor, action='order.create', object_type='Order', object_id=str(obj.pk),
                         detail={'before': None, 'after': request_snapshot(obj)})
    from harbor_web.autoplanning import replan_enabled
    replan_enabled(actor, 'order.create')
    return obj


@serialized
@transaction.atomic
def update_order(actor, *, order_id, expected_version, priority_group, sequence=None, tie_policy, note='', title=None,
                 voyage_code=None, cutoff_at=None, clear_cutoff=False):
    _role(actor, 'operator')
    obj = Order.objects.get(pk=order_id)
    if obj.version != expected_version:
        raise Conflict('Order changed; reload before editing.')
    before = request_snapshot(obj)
    tier_changed = priority_group != obj.priority_group
    if sequence is None:
        sequence = back_of_tier(priority_group, obj.kind)[1] if tier_changed else obj.sequence
    elif (tier_changed and obj.kind == 'OUTBOUND' and tie_policy == 'FIXED'
            and Order.objects.filter(kind='OUTBOUND', priority_group=priority_group, sequence=sequence).exclude(pk=obj.pk).exists()):
        # Changing tier with a reused sequence from the previous tier places the order at the back of the new tier.
        sequence = back_of_tier(priority_group, obj.kind)[1]
    for key, value in dict(priority_group=priority_group, sequence=sequence, tie_policy=tie_policy, note=note).items():
        setattr(obj, key, value)
    if title is not None:  # None keeps the current title for callers that predate titles
        obj.title = title
    if voyage_code is not None:
        obj.voyage_code = voyage_code
    if clear_cutoff:
        obj.cutoff_at = None
    elif cutoff_at is not None:
        obj.cutoff_at = cutoff_at
    obj.version += 1
    obj.full_clean()
    _queue_policy(obj)
    updated = Order.objects.filter(pk=order_id, version=expected_version).update(
        priority_group=obj.priority_group, sequence=obj.sequence, tie_policy=obj.tie_policy,
        title=obj.title, voyage_code=obj.voyage_code, cutoff_at=obj.cutoff_at,
        note=obj.note, version=obj.version, updated_at=timezone.now())
    if not updated:
        raise Conflict('Order changed; reload before editing.')
    Audit.objects.create(actor=actor, action='order.update', object_type='Order', object_id=str(obj.pk),
                         detail={'before': before, 'after': request_snapshot(obj)})
    obj.refresh_from_db()
    from harbor_web.autoplanning import replan_enabled
    replan_enabled(actor, 'order.update')
    return obj


def outbound_lanes():
    """The three tiers in order with their open outbound orders by (group, sequence); empty tiers stay."""
    lanes = [dict(group=group, name=name, fixed=True, orders=[]) for group, name in TIERS]
    for order in open_outbound_orders().select_related('cargo').order_by('priority_group', 'sequence', 'pk'):
        lanes[tier_of(order.priority_group) - 1]['orders'].append(order)
    for lane in lanes:
        lane['fixed'] = all(order.tie_policy == 'FIXED' for order in lane['orders'])
    return lanes


def back_of_tier(group, kind='OUTBOUND'):
    """(group, sequence) that puts a new order last in its tier."""
    top = Order.objects.filter(kind=kind, priority_group=group).aggregate(top=Max('sequence'))['top'] or 0
    return group, top + 1


def _closed_sequence_top(group):
    """Finished fixed orders keep their (group, sequence) slot; renumbering starts above it."""
    return Order.objects.filter(kind='OUTBOUND', priority_group=group, tie_policy='FIXED').exclude(
        pk__in=open_outbound_orders()).aggregate(top=Max('sequence'))['top'] or 0


def _reorder_lanes(actor, *, lanes, queryset, conflict_msg):
    _role(actor, 'operator')
    if len(lanes) != len(TIERS):
        raise Conflict('The queue has three tiers.')
    entries = [(order_id, version) for lane in lanes for order_id, version in lane['orders']]
    ids = [order_id for order_id, _ in entries]
    current = {order.pk: order for order in queryset.select_related('cargo').select_for_update()}
    if len(set(ids)) != len(ids) or set(ids) != set(current):
        raise Conflict(conflict_msg)
    if any(current[order_id].version != version for order_id, version in entries):
        raise Conflict('Order changed; reload before reordering.')
    running = running_order_ids(ids)
    if running:
        seen_non_running = False
        for order_id in ids:
            if order_id in running:
                if seen_non_running:
                    raise ValidationError('실행 중인 작업보다 앞에 둘 수 없습니다.')
            else:
                seen_non_running = True
    target = {}
    for (group, _), lane in zip(TIERS, lanes):
        policy, base = 'FIXED' if lane['fixed'] else 'ANY', _closed_sequence_top(group)
        for position, (order_id, _) in enumerate(lane['orders'], start=1):
            target[order_id] = (group, base + position, policy)
    changed = [order for order_id, order in current.items()
               if (order.priority_group, order.sequence, order.tie_policy) != target[order_id]]
    if not changed:
        return []
    # Park changed orders above every group first: the fixed-sequence constraint is checked per row.
    park = max(Order.objects.filter(kind__in=('OUTBOUND', 'INBOUND')).aggregate(top=Max('priority_group'))['top'] or 0, len(TIERS)) + 1
    for offset, order in enumerate(changed):
        Order.objects.filter(pk=order.pk).update(priority_group=park + offset, sequence=1, tie_policy='FIXED')
    befores = {order.pk: request_snapshot(order) for order in changed}
    now = timezone.now()
    for order in changed:
        order.priority_group, order.sequence, order.tie_policy = target[order.pk]
        order.version += 1
        order.full_clean()
        if not Order.objects.filter(pk=order.pk, version=order.version - 1).update(priority_group=order.priority_group,
                sequence=order.sequence, tie_policy=order.tie_policy, version=order.version, updated_at=now):
            raise Conflict('Order changed; reload before reordering.')
    for order in changed:
        _queue_policy(order)
        Audit.objects.create(actor=actor, action='order.update', object_type='Order', object_id=str(order.pk),
                             detail={'before': befores[order.pk], 'after': request_snapshot(order)})
    from harbor_web.autoplanning import replan_enabled
    replan_enabled(actor, 'order.update')
    return changed


@serialized
@transaction.atomic
def reorder_outbound(actor, *, lanes):
    """Renumber every open outbound order from the three tier lanes in one revision step.

    ``lanes`` is three ``dict(fixed=bool, orders=[(order_id, expected_version), ...])`` for 긴급 · 우선 · 일반
    and must list each open outbound order exactly once; a tier may be empty. Position j in tier g becomes
    (g, j) above any finished order's slot. Only orders whose group, sequence or tie policy changes get a
    new revision and an audit row.
    """
    return _reorder_lanes(
        actor, lanes=lanes, queryset=open_outbound_orders(),
        conflict_msg='The outbound queue changed; reload before reordering.',
    )


@serialized
@transaction.atomic
def reorder_schedule(actor, *, lanes):
    """Renumber every open inbound and outbound order across the three tier lanes in one revision step."""
    return _reorder_lanes(
        actor, lanes=lanes, queryset=open_schedule_orders(),
        conflict_msg='The schedule queue changed; reload before reordering.',
    )


def _lane_entries():
    return [dict(fixed=lane['fixed'], orders=[(order.pk, order.version) for order in lane['orders']])
            for lane in outbound_lanes()]


@serialized
def register_order(actor, *, make_first=False, **data):
    """Create a request (at the back of its tier unless a sequence is given) and, for an outbound asked to go first,
    move it to the front of its tier in the same transaction. queue_lock first, then the write lock, like every
    other mutating service (review of #44)."""
    with transaction.atomic():
        if data.get('sequence') is None:
            data['priority_group'], data['sequence'] = back_of_tier(data['priority_group'], data['kind'])
        order = create_order(actor, **data)
        if make_first and order.kind == 'OUTBOUND':
            move_outbound_to_front(actor, order)
    return order


def move_outbound_to_front(actor, order):
    """Put one open outbound order first in its own tier; everything else keeps its place."""
    lanes = _lane_entries()
    for lane in lanes:
        lane['orders'] = [entry for entry in lane['orders'] if entry[0] != order.pk]
    lanes[tier_of(order.priority_group) - 1]['orders'].insert(0, (order.pk, order.version))
    return reorder_outbound(actor, lanes=lanes)


def apply_tier_order(actor, *, group, orders):
    """Fix one tier to a chosen retrieval order (a simulated scenario); the other tiers stay as they are."""
    lanes = _lane_entries()
    lanes[tier_of(group) - 1] = dict(fixed=True, orders=list(orders))
    return reorder_outbound(actor, lanes=lanes)


def completed_order_ids():
    """Orders have no close state: a ledger-completed Command or CellPlanDispatch closes one."""
    return Order.objects.filter(
        Exists(Command.objects.filter(order_id=OuterRef('pk'), state='COMPLETED'))
        | Exists(CellPlanDispatch.objects.filter(order_id=OuterRef('pk'), state='COMPLETED'))
    ).values('pk')


def open_outbound_orders():
    return Order.objects.filter(kind='OUTBOUND').exclude(pk__in=completed_order_ids())


def open_schedule_orders():
    return Order.objects.filter(kind__in=('OUTBOUND', 'INBOUND')).exclude(pk__in=completed_order_ids())


def queue_digest():
    # REARRANGE orders are not queue entries: they must not invalidate other plans.
    rows = list(Order.objects.exclude(kind='REARRANGE').order_by('pk').values('id', 'cargo_id', 'kind', 'priority_group', 'sequence', 'tie_policy', 'note', 'version'))
    closed = sorted(
        Order.objects.exclude(kind='REARRANGE')
        .filter(pk__in=completed_order_ids())
        .values_list('pk', flat=True)
    )
    # A completion leaves the queue, so plans made before it must be reviewed; the digest is unchanged until one exists.
    payload = dict(rows=rows, closed=closed) if closed else rows
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
