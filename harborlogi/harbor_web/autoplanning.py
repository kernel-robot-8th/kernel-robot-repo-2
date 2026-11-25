"""Bounded event-driven recalculation of explicitly source-bound requests."""
import hashlib
import json

from django.db.models import Q
from django.utils import timezone
from harbor.port_events import parse_slot_ref
from harbor_web.command_models import ACTIVE_CELL_PLAN_DISPATCH_STATES, ACTIVE_COMMAND_STATES, CellPlanDispatch
from harbor_web.models import Command, CargoFootprint, Observation, Order, Plan
from harbor_web.order_queue import queue_digest
from harbor_web.plan_service import _calculate_plan, placement_decision
from harbor_web.site_config import constraints, map_areas


def unreconciled_cell_dispatches(observation):
    """Cell work can still own space until its outcome and the affected yard are observed."""
    terminal = ('COMPLETED', 'CANCELLED', 'ABORTED')
    outstanding = CellPlanDispatch.objects.filter(state__in=(*ACTIVE_CELL_PLAN_DISPATCH_STATES, *terminal))
    payload = observation.payload
    if (observation.provenance in ('DECLARED', 'SYNTHETIC') and observation.observed_at <= timezone.now()
            and payload.get('schema') == 'grid-placement-v1'
            and {a.area_id for a in map_areas(observation.condition.payload)} <= set(payload['observed_areas'])):
        same_source = Q(observation__provenance=observation.provenance, observation__source_id=observation.source_id)
        if observation.provenance == 'DECLARED':
            same_source |= Q(observation__provenance='PORT_EVENT')
        outstanding = outstanding.exclude(same_source, ~Q(observation_id=observation.pk),
            state__in=terminal, updated_at__lt=observation.observed_at,
            observation__condition_id=observation.condition_id,
        )
    return outstanding


def placement_cells(recommendation, incoming):
    from harbor.spatial import CargoShape, Placement
    shape, placement = CargoShape(**incoming), Placement(**recommendation)
    return frozenset((placement.area_id,
        placement.row + (n if placement.orientation == 90 else 0),
        placement.column + (n if placement.orientation == 0 else 0), placement.level)
        for n in range(shape.length_cells))


def placement_reservations(order, observation):
    """Sent destinations take precedence over earlier automatic recommendations."""
    binding = order.planning_observation if order.planning_observation_id else observation
    seen = {item['cargo_id'] for item in observation.payload.get('placements', [])}
    cells, references = set(), []

    def reserve(plan, recommendation, incoming):
        cells.update(placement_cells(recommendation, incoming))
        references.append({'plan_id': plan.pk, 'recommendation': recommendation, 'incoming_shape': incoming})

    sent = unreconciled_cell_dispatches(observation).filter(order__kind='INBOUND', dispatch_mode='HARBOR_PLAN',
        observation__condition_id=observation.condition_id, plan__isnull=False).exclude(order=order)
    for dispatch in sent.select_related('plan', 'order__cargo').order_by('pk'):
        plan = dispatch.plan
        incoming = plan.result.get('incoming_shape')
        if not incoming or plan.result.get('plan_kind') != 'inbound-placement':
            continue
        code = dispatch.order.cargo.code
        for step in dispatch.request_payload.get('steps', []):
            if step.get('cargo_id') != code or step.get('src_site') != 'belt':
                continue
            area, row, col, level = parse_slot_ref(step['dst_site'])
            decision = placement_decision(plan)
            anchor = dict(area_id=area, row=row, column=col, level=level)
            candidate = plan.result['recommendation']
            if decision and decision.created_at <= dispatch.created_at:
                chosen = decision.detail['recommendation']
                if all(chosen.get(field) == value for field, value in anchor.items()):
                    candidate = chosen
            reserve(plan, dict(candidate, **anchor), incoming)
    earlier = Order.objects.filter(auto_replan=True, kind='INBOUND', pk__lt=order.pk,
        planning_observation__source_id=binding.source_id,
        planning_observation__condition_id=binding.condition_id).exclude(
            pk__in=sent.values('order_id')).exclude(cell_plan_dispatches__state='COMPLETED').select_related('cargo')
    for request in earlier.order_by('pk'):
        if request.cargo.code in seen or Command.objects.filter(order=request, state__in=ACTIVE_COMMAND_STATES).exists():
            continue
        plan = Plan.objects.filter(order=request).select_related('observation').order_by('-pk').first()
        if (plan is None or plan.result.get('status') != 'READY'
                or plan.result.get('plan_kind') != 'inbound-placement'
                or plan.observation.source_id != observation.source_id
                or plan.condition_id != observation.condition_id):
            continue
        decision = placement_decision(plan)
        recommendation = decision.detail['recommendation'] if decision else plan.result.get('recommendation')
        incoming = plan.result.get('incoming_shape')
        if recommendation is None or incoming is None:
            continue
        reserve(plan, recommendation, incoming)
    return frozenset(cells), references


def planning_digest(order, observation, *, now=None):
    current = timezone.now() if now is None else now
    binding = order.planning_observation if order.planning_observation_id else observation
    age = constraints(observation.condition.payload).max_observation_age
    elapsed = (current - observation.observed_at).total_seconds()
    payload = {'queue': queue_digest(), 'observation': observation.pk,
        'binding': [binding.source_id, binding.condition_id], 'condition': observation.condition_id,
        'policy': observation.condition.payload,
        'reservations': placement_reservations(order, observation)[1] if order.kind == 'INBOUND' else [],
        'footprints': list(CargoFootprint.objects.order_by('pk').values_list('pk', 'cargo_id', 'length_cells')),
        'fresh': 0 <= elapsed <= age.total_seconds(),
        'commands': list(Command.objects.order_by('pk').values_list('pk', 'state'))}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def replan_enabled(actor, trigger, *, source_id=None):
    # Called inside each event's serialized atomic transaction; never calls a public mutator.
    for order in Order.objects.filter(auto_replan=True).select_related('cargo', 'planning_observation').order_by('pk'):
        binding = order.planning_observation
        if binding is None or (source_id is not None and binding.source_id != source_id):
            continue
        if (Command.objects.filter(order=order, state__in=ACTIVE_COMMAND_STATES).exists()
                or CellPlanDispatch.objects.filter(order=order, state='COMPLETED').exists()):
            continue
        observation = Observation.objects.filter(source_id=binding.source_id).select_related('condition').latest('pk')
        if unreconciled_cell_dispatches(observation).filter(order=order).exists():
            continue
        current = timezone.now()
        digest = planning_digest(order, observation, now=current)
        previous = Plan.objects.filter(order=order).order_by('-pk').first()
        if previous and previous.result.get('input_digest') == digest:
            continue
        reason = None
        if observation.condition_id != binding.condition_id:
            reason = 'SOURCE_CONDITION_CHANGED'
        elif Command.objects.exclude(order=order).filter(state__in=ACTIVE_COMMAND_STATES).exists():
            # Command states do not prove physical completion or release of reserved space.
            reason = 'OTHER_COMMAND_REQUIRES_RECONCILIATION'
        _calculate_plan(actor, order, observation, now=current, trigger=trigger,
            input_digest=digest, hold_reason=reason)
