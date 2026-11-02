"""Outbound order queue: create, reorder, and digest the queue that plans are computed against."""
import hashlib
import json

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from harbor_web.coordination import serialized
from harbor_web.models import Order, Audit
from harbor_web.service_common import Conflict, _role


def _queue_policy(order):
    if order.kind != 'OUTBOUND':
        return
    peers = Order.objects.filter(kind='OUTBOUND', priority_group=order.priority_group).exclude(pk=order.pk)
    if peers.exclude(tie_policy=order.tie_policy).exists():
        raise ValidationError('All members of a priority group must use the same policy.')
    if order.tie_policy == 'FIXED' and peers.filter(sequence=order.sequence).exists():
        raise ValidationError('Fixed group sequence must be unique.')


def request_snapshot(order):
    return dict(id=order.pk, cargo_id=order.cargo_id, cargo_code=order.cargo.code if order.cargo_id else None, kind=order.kind,
        priority_group=order.priority_group, sequence=order.sequence, tie_policy=order.tie_policy,
        note=order.note, version=order.version)


def queue_snapshot():
    return [request_snapshot(order) for order in Order.objects.exclude(kind='REARRANGE').select_related('cargo').order_by('pk')]


@serialized
@transaction.atomic
def create_order(actor, *, cargo, kind, priority_group, sequence, tie_policy, note=''):
    _role(actor, 'operator')
    obj = Order(created_by=actor, cargo=cargo, kind=kind, priority_group=priority_group,
                sequence=sequence, tie_policy=tie_policy, note=note)
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
def update_order(actor, *, order_id, expected_version, priority_group, sequence, tie_policy, note=''):
    _role(actor, 'operator')
    obj = Order.objects.get(pk=order_id)
    if obj.version != expected_version:
        raise Conflict('Order changed; reload before editing.')
    before = request_snapshot(obj)
    for key, value in dict(priority_group=priority_group, sequence=sequence, tie_policy=tie_policy, note=note).items():
        setattr(obj, key, value)
    obj.version += 1
    obj.full_clean()
    _queue_policy(obj)
    updated = Order.objects.filter(pk=order_id, version=expected_version).update(
        priority_group=obj.priority_group, sequence=obj.sequence, tie_policy=obj.tie_policy,
        note=obj.note, version=obj.version, updated_at=timezone.now())
    if not updated:
        raise Conflict('Order changed; reload before editing.')
    Audit.objects.create(actor=actor, action='order.update', object_type='Order', object_id=str(obj.pk),
                         detail={'before': before, 'after': request_snapshot(obj)})
    obj.refresh_from_db()
    from harbor_web.autoplanning import replan_enabled
    replan_enabled(actor, 'order.update')
    return obj


def queue_digest():
    # REARRANGE orders are not queue entries: they must not invalidate other plans.
    rows = list(Order.objects.exclude(kind='REARRANGE').order_by('pk').values('id', 'cargo_id', 'kind', 'priority_group', 'sequence', 'tie_policy', 'note', 'version'))
    return hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
