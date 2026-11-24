"""Compile shipper & voyage deadline rules into Harbor's 3-tier outbound queue without forking planners."""
from datetime import datetime, timezone as dt_timezone

from django.db import transaction
from django.utils import timezone

from harbor.sequencing import rank_retrieval_orders
from harbor_web.coordination import serialized
from harbor_web.models import Audit, Observation
from harbor_web.order_queue import TIERS, open_outbound_orders, outbound_lanes, reorder_outbound, tier_of
from harbor_web.plan_service import _outbound_schedule
from harbor_web.service_common import _role
from harbor_web.site_config import spatial_policy, spatial_snapshot

_FAR_FUTURE = datetime(9999, 12, 31, tzinfo=dt_timezone.utc)


def _cutoff_tier(order, now, *, urgent_hours, priority_hours):
    if order.cutoff_at is None:
        return None, ''
    hours = (order.cutoff_at - now).total_seconds() / 3600.0
    voyage = order.voyage_code or '지정 배편'
    if hours <= urgent_hours:
        return 1, f'{voyage} 마감 임박'
    if hours <= priority_hours:
        return 2, f'{voyage} 마감 우선'
    return 3, f'{voyage} 여유 있음'


def _order_bucket_and_reason(order, now, *, urgent_hours, priority_hours):
    cut_tier, cut_note = _cutoff_tier(order, now, urgent_hours=urgent_hours, priority_hours=priority_hours)
    shipper_tier = order.cargo.shipper_tier if order.cargo_id else None
    shipper_name = (order.cargo.shipper_name if order.cargo_id else '') or '화주'
    candidates = [t for t in (cut_tier, shipper_tier) if t is not None]
    if not candidates:
        return tier_of(order.priority_group), '기존 우선순위 유지'
    target_group = min(candidates)
    reasons = []
    if cut_tier == target_group and cut_note:
        reasons.append(cut_note)
    if shipper_tier == target_group:
        label = {1: '긴급 화주', 2: '우선 화주', 3: '일반 화주'}.get(shipper_tier, '')
        reasons.append(f'{shipper_name}({label})')
    return target_group, ' · '.join(reasons)


def _sort_within_tier_with_rehandle(orders, *, current_snapshot, policy, base_schedule, group, now):
    """Preserve (cutoff, shipper, voyage) order and optimize placed sub-runs within identical voyage+cutoff buckets."""
    ordered = sorted(
        orders,
        key=lambda o: (
            0 if o.cutoff_at else 1,
            o.cutoff_at or _FAR_FUTURE,
            (o.cargo.shipper_tier if o.cargo_id and o.cargo.shipper_tier else 9),
            o.voyage_code or '~',
            o.pk,
        ),
    )
    if current_snapshot is None or policy is None or len(ordered) < 2:
        return ordered
    placed = {p.cargo_id for p in current_snapshot.placements}
    if not any(o.cargo.code in placed for o in ordered):
        return ordered
    result = []
    idx = 0
    while idx < len(ordered):
        voy = ordered[idx].voyage_code
        cut_tier = (ordered[idx].cutoff_at.replace(minute=0, second=0, microsecond=0) if ordered[idx].cutoff_at else None)
        end = idx + 1
        while (
            end < len(ordered)
            and ordered[end].voyage_code == voy
            and (ordered[end].cutoff_at.replace(minute=0, second=0, microsecond=0) if ordered[end].cutoff_at else None) == cut_tier
        ):
            end += 1
        chunk = ordered[idx:end]
        placed_chunk = {o.cargo.code: o for o in chunk if o.cargo.code in placed}
        if len(placed_chunk) >= 2:
            try:
                ranked = rank_retrieval_orders(
                    current_snapshot, policy, tuple(placed_chunk.keys()),
                    now=now, schedule=base_schedule, group=group,
                )
                best = ranked[0]
                if best.feasible and best.final is not None:
                    current_snapshot = best.final
                placed_order = [placed_chunk[code] for code in best.order]
                unplaced = [o for o in chunk if o.cargo.code not in placed]
                result.extend(placed_order + unplaced)
                idx = end
                continue
            except ValueError:
                pass
        result.extend(chunk)
        idx = end
    return result


def propose_outbound_schedule(orders=None, *, now=None, observation=None, urgent_hours=4, priority_hours=12):
    """Compute a proposed 3-tier outbound schedule from cutoff urgency, shipper tier, and rehandle simulation."""
    now = now or timezone.now()
    open_orders = (
        list(orders)
        if orders is not None
        else list(open_outbound_orders().select_related('cargo').order_by('priority_group', 'sequence', 'pk'))
    )
    buckets = {group: [] for group, _ in TIERS}
    reasons = {}
    for order in open_orders:
        group, reason = _order_bucket_and_reason(order, now, urgent_hours=urgent_hours, priority_hours=priority_hours)
        buckets[group].append(order)
        reasons[order.pk] = reason

    obs = observation or Observation.objects.select_related('condition').order_by('-pk').first()
    snapshot, policy = None, None
    if obs is not None and isinstance(obs.payload, dict) and obs.payload.get('schema') == 'grid-placement-v1':
        try:
            snapshot = spatial_snapshot(obs)
            policy = spatial_policy(obs.condition.payload)
        except Exception:
            snapshot, policy = None, None
    base_schedule = _outbound_schedule()

    lanes = []
    preview_rows = []
    changed_count = 0
    for group, tier_name in TIERS:
        sorted_orders = _sort_within_tier_with_rehandle(
            buckets[group],
            current_snapshot=snapshot,
            policy=policy,
            base_schedule=base_schedule,
            group=group,
            now=now,
        )
        lanes.append(dict(fixed=True, orders=[(o.pk, o.version) for o in sorted_orders]))
        for pos, order in enumerate(sorted_orders, start=1):
            changed = (order.priority_group != group or order.sequence != pos)
            if changed:
                changed_count += 1
            preview_rows.append(dict(
                order=order,
                tier_group=group,
                tier_name=tier_name,
                position=pos,
                reason=reasons[order.pk],
                changed=changed,
            ))
    return dict(lanes=lanes, preview_rows=preview_rows, changed_count=changed_count)


@serialized
@transaction.atomic
def apply_proposed_schedule(actor, *, now=None, observation=None, urgent_hours=4, priority_hours=12):
    """Apply the computed shipper/voyage schedule atomically through reorder_outbound()."""
    _role(actor, 'operator')
    proposal = propose_outbound_schedule(
        now=now, observation=observation, urgent_hours=urgent_hours, priority_hours=priority_hours
    )
    if not proposal['preview_rows']:
        return [], proposal
    changed = reorder_outbound(actor, lanes=proposal['lanes'])
    if changed:
        Audit.objects.create(
            actor=actor,
            action='order.schedule.apply',
            object_type='Order',
            object_id='outbound-queue',
            detail={
                'changed_ids': [o.pk for o in changed],
                'count': len(changed),
            },
        )
    return changed, proposal
