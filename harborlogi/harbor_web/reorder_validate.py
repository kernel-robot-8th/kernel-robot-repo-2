"""Read-only POST /outbound/validate/ endpoint: simulate a candidate outbound or mixed schedule without saving."""
import json

from django.db.models import OuterRef, Subquery
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from harbor.sequencing import ScheduleEntry, evaluate_schedule
from harbor.site_recognition import placement_to_site_ref
from harbor.spatial import CargoShape
from harbor_web.auth import require_roles
from harbor_web.command_models import ACTIVE_COMMAND_STATES
from harbor_web.models import Audit, CargoFootprint, Command, Order, Plan
from harbor_web.order_queue import (
    RUNNING_CELL_PLAN_STATES,
    RUNNING_COMMAND_STATES,
    TIERS,
    _closed_sequence_top,
    open_outbound_orders,
    open_schedule_orders,
    parse_entries,
    parse_lanes,
    running_order_ids,
    tier_of,
)
from harbor_web.page_helpers import selected_observation
from harbor_web.plan_service import DECIDE_ACTION, _outbound_schedule
from harbor_web.presentation import reason_text
from harbor_web.site_config import spatial_policy, spatial_snapshot
from harbor_web.yard_rows import place_context, placement_text

_SHORT_HOLD_REASONS = {
    'NO_LEGAL_PLACEMENT': '빈 자리 없음',
    'NO_LEGAL_SPATIAL_PLAN': '반출 경로 없음',
    'NO_LEGAL_TEMPORARY_BAY': '임시 공간 없음',
    'INCOMING_FOOTPRINT_REQUIRED': '화물 크기 없음',
    'INBOUND_ALREADY_OBSERVED': '이미 야드에 있음',
    'OUTBOUND_NOT_OBSERVED': '야드에 없음',
    'OUTBOUND_NOT_IN_A_BAY': '보관 칸에 없음',
    'OUTBOUND_ALREADY_ON_STAND_REQUIRES_EVIDENCE': '반출대 확인 필요',
    'OUTBOUND_STAND_FULL': '반출대 가득 참',
    'OUTBOUND_MOVE_UNAVAILABLE': '반출 경로 없음',
    'OUTBOUND_BEFORE_INBOUND': '입고 전 반출 불가',
    'OBSERVATION_NOT_CURRENT': '야드 기록 오래됨',
    'PHYSICAL_HOLDING_REQUIRES_RECONCILIATION': '집게 화물 확인 필요',
    'PLACEMENT_SEARCH_LIMIT_REACHED': '배치 탐색 한도 초과',
    'SPATIAL_SEARCH_LIMIT_REACHED': '경로 탐색 한도 초과',
    'OBSERVATION_CARGO_LIMIT_REACHED': '관측 화물 한도 초과',
    'ARM_REACH_UNVERIFIED': '장비 도달 미확인',
    'NO_ARM_REACHES_MOVE': '장비 도달 불가',
    'PRIORITY_ORDER_BLOCKED': '앞선 반출 대기',
}


def short_hold_reason(reason_code, kind=''):
    if reason_code in _SHORT_HOLD_REASONS:
        return _SHORT_HOLD_REASONS[reason_code]
    return '배치 불가' if kind == 'INBOUND' else '계산 보류'


def _parse_request_lanes(request):
    content_type = request.content_type or ''
    if 'application/json' in content_type:
        try:
            body = json.loads(request.body.decode('utf-8') or '{}')
        except (UnicodeDecodeError, ValueError):
            return None, ''
        scope = body.get('scope', '') if isinstance(body, dict) else ''
        if isinstance(body, dict) and isinstance(body.get('lanes'), list):
            if all(isinstance(item, str) for item in body['lanes']):
                return parse_lanes(body['lanes']), scope
            lanes = []
            for item in body['lanes']:
                if not isinstance(item, dict) or not isinstance(item.get('orders'), list):
                    return None, scope
                parsed = []
                for entry in item['orders']:
                    if not isinstance(entry, dict):
                        return None, scope
                    oid, ver = entry.get('id'), entry.get('version')
                    if type(oid) is not int or type(ver) is not int:
                        return None, scope
                    parsed.append((oid, ver))
                lanes.append(dict(fixed=bool(item.get('fixed', True)), orders=parsed))
            return (lanes or None), scope
        if isinstance(body, dict) and isinstance(body.get('order'), list):
            entries = parse_entries(','.join(str(x) for x in body['order']))
            if entries is None:
                return None, scope
            return [dict(fixed=True, orders=entries), dict(fixed=True, orders=[]), dict(fixed=True, orders=[])], scope
        return None, scope
    scope = request.POST.get('scope', '')
    if 'lane' in request.POST:
        return parse_lanes(request.POST.getlist('lane')), scope
    if 'order' in request.POST:
        entries = parse_entries(request.POST.get('order', ''))
        if entries is not None:
            return [dict(fixed=True, orders=entries), dict(fixed=True, orders=[]), dict(fixed=True, orders=[])], scope
    return None, scope


def _response(*, ok, reasons=(), moves=None, rehandles=None, items=(), missing=(), current=None, status=200):
    return JsonResponse(
        dict(
            ok=ok,
            reasons=list(reasons),
            moves=moves,
            rehandles=rehandles,
            items=list(items),
            missing=list(missing),
            current=current,
        ),
        status=status,
    )


def _latest_footprints(orders):
    cargo_ids = {order.cargo_id for order in orders if order.cargo_id}
    if not cargo_ids:
        return {}
    footprints = {}
    for fp in CargoFootprint.objects.filter(cargo_id__in=cargo_ids).order_by('pk'):
        footprints[fp.cargo_id] = fp
    return footprints


def _placement_label(recommendation, condition_payload=None):
    if recommendation is None:
        return ''
    if isinstance(recommendation, dict):
        area_id = recommendation.get('area_id') or recommendation.get('area')
        row = recommendation.get('row')
        column = recommendation.get('column')
        level = recommendation.get('level')
        orientation = recommendation.get('orientation', 0)
    else:
        area_id = recommendation.area_id
        row = recommendation.row
        column = recommendation.column
        level = recommendation.level
        orientation = getattr(recommendation, 'orientation', 0)
    try:
        return placement_to_site_ref(area_id, int(row), int(column), int(level))
    except (TypeError, ValueError):
        if condition_payload is None:
            return ''
        labels, slot = place_context(condition_payload)
        return placement_text(
            dict(
                area_id=area_id,
                row=row,
                column=column,
                level=level,
                orientation=orientation,
            ),
            labels,
            slot,
        )


def _build_proposed_schedule(lanes, open_orders, base_schedule):
    """Build outbound rank dict matching what reorder_outbound/reorder_schedule would persist."""
    proposed = dict(base_schedule)
    for (group, _), lane in zip(TIERS, lanes):
        base = _closed_sequence_top(group)
        for pos, (oid, _) in enumerate(lane['orders'], start=1):
            order = open_orders[oid]
            if order.kind == 'OUTBOUND':
                proposed[order.cargo.code] = (group, base + pos if lane['fixed'] else None)
    return proposed


def _recommendation_cells(recommendation, length_cells):
    area_id = recommendation['area_id']
    row = recommendation['row']
    column = recommendation['column']
    level = recommendation['level']
    orientation = recommendation['orientation']
    return frozenset(
        (
            area_id,
            row + (n if orientation == 90 else 0),
            column + (n if orientation == 0 else 0),
            level,
        )
        for n in range(length_cells)
    )


def _load_external_reservation_entries(orders, observation):
    """Batch-load auto_replan inbound reservations matching placement_reservations() in constant queries."""
    inbound_orders = [order for order in orders if order.kind == 'INBOUND']
    if not inbound_orders or observation is None:
        return []
    bindings = {}
    for order in inbound_orders:
        binding = order.planning_observation if order.planning_observation_id else observation
        bindings[order.pk] = (binding.source_id, binding.condition_id)
    max_pk = max(order.pk for order in inbound_orders)
    source_ids = {key[0] for key in bindings.values()}
    condition_ids = {key[1] for key in bindings.values()}
    candidates = list(
        Order.objects.filter(
            auto_replan=True,
            kind='INBOUND',
            pk__lt=max_pk,
            planning_observation__source_id__in=source_ids,
            planning_observation__condition_id__in=condition_ids,
        ).select_related('cargo', 'planning_observation').order_by('pk')
    )
    if not candidates:
        return []
    seen = {item['cargo_id'] for item in observation.payload.get('placements', [])}
    candidates = [req for req in candidates if req.cargo.code not in seen]
    if not candidates:
        return []
    active_ids = set(
        Command.objects.filter(
            order_id__in=[req.pk for req in candidates],
            state__in=ACTIVE_COMMAND_STATES,
        ).values_list('order_id', flat=True)
    )
    candidates = [req for req in candidates if req.pk not in active_ids]
    if not candidates:
        return []
    latest_plan_sq = Plan.objects.filter(order_id=OuterRef('order_id')).order_by('-pk').values('pk')[:1]
    latest_plans = {
        plan.order_id: plan
        for plan in Plan.objects.filter(
            order_id__in=[req.pk for req in candidates],
            pk=Subquery(latest_plan_sq),
        ).select_related('observation')
    }
    valid_plans = {}
    for req in candidates:
        plan = latest_plans.get(req.pk)
        if (plan is None or plan.result.get('status') != 'READY'
                or plan.result.get('plan_kind') != 'inbound-placement'
                or plan.observation.source_id != observation.source_id
                or plan.condition_id != observation.condition_id):
            continue
        valid_plans[req.pk] = plan
    if not valid_plans:
        return []
    decisions = {}
    for audit in Audit.objects.filter(
        action=DECIDE_ACTION,
        object_type='Plan',
        object_id__in=[str(plan.pk) for plan in valid_plans.values()],
    ).order_by('pk'):
        decisions.setdefault(audit.object_id, audit)
    entries = []
    for req in candidates:
        plan = valid_plans.get(req.pk)
        if plan is None:
            continue
        decision = decisions.get(str(plan.pk))
        recommendation = decision.detail['recommendation'] if decision else plan.result.get('recommendation')
        incoming = plan.result.get('incoming_shape')
        if recommendation is None or incoming is None:
            continue
        cells = _recommendation_cells(recommendation, incoming.get('length_cells', 0))
        entries.append((
            req.pk,
            req.planning_observation.source_id,
            req.planning_observation.condition_id,
            incoming.get('cargo_id'),
            cells,
        ))
    return entries


def _external_reserved_cells_from_entries(order, observation, reservation_entries, *, exclude_codes):
    if not reservation_entries or observation is None:
        return frozenset()
    binding = order.planning_observation if order.planning_observation_id else observation
    cells = set()
    for req_pk, src_id, cond_id, cargo_code, entry_cells in reservation_entries:
        if req_pk >= order.pk:
            break
        if src_id != binding.source_id or cond_id != binding.condition_id:
            continue
        if cargo_code in exclude_codes:
            continue
        cells.update(entry_cells)
    return frozenset(cells)


def _simulate_orders(snapshot, policy, orders, *, footprints, now, schedule, observation, reservation_entries=None):
    condition_payload = observation.condition.payload
    if reservation_entries is None:
        reservation_entries = _load_external_reservation_entries(orders, observation)
    placed_initial = {p.cargo_id for p in snapshot.placements}
    inbound_before = set()
    entries = []
    entry_orders = []
    missing = []

    for idx, order in enumerate(orders):
        code = order.cargo.code
        if order.kind == 'INBOUND':
            fp = footprints.get(order.cargo_id)
            shape = CargoShape(code, fp.length_cells, fp.pk) if fp is not None else None
            reserved = _external_reserved_cells_from_entries(
                order, observation, reservation_entries, exclude_codes=inbound_before,
            )
            entries.append(ScheduleEntry('INBOUND', code, shape, reserved_cells=reserved))
            entry_orders.append(order)
            inbound_before.add(code)
            continue

        if code not in placed_initial and code not in inbound_before:
            later_inbound = any(
                later.kind == 'INBOUND' and later.cargo_id == order.cargo_id
                for later in orders[idx + 1:]
            )
            if later_inbound:
                msg = f'{code} (반출): 입고 전에 반출할 수 없습니다.'
                return dict(
                    feasible=False,
                    evaluated_any=True,
                    total_moves=0,
                    rehandles=0,
                    items=[dict(
                        id=order.pk, code=code, kind='OUTBOUND', ok=False,
                        moves=0, rehandles=0, place='', reason=msg,
                        short_reason=short_hold_reason('OUTBOUND_BEFORE_INBOUND', 'OUTBOUND'),
                        reason_code='OUTBOUND_BEFORE_INBOUND',
                    )],
                    reasons=[msg],
                    missing=missing,
                )
            missing.append(code)
            continue

        entries.append(ScheduleEntry('OUTBOUND', code))
        entry_orders.append(order)

    if not entries:
        return dict(
            feasible=False,
            evaluated_any=False,
            total_moves=0,
            rehandles=0,
            items=[],
            reasons=[],
            missing=missing,
        )

    scenario = evaluate_schedule(snapshot, policy, entries, now=now, schedule=schedule)
    items = []
    reasons = []
    for order, step in zip(entry_orders, scenario.steps):
        step_ok = step.status == 'READY'
        if step_ok:
            message = ''
            short_reason = ''
        else:
            short_reason = short_hold_reason(step.reason, step.kind)
            if step.kind == 'INBOUND':
                message = f'{step.cargo_id} (입고): {reason_text(step.reason)}'
            else:
                message = f'{step.cargo_id}: {reason_text(step.reason)}'
        if message:
            reasons.append(message)
        place = _placement_label(step.recommendation, condition_payload) if (step_ok and step.kind == 'INBOUND') else ''
        items.append(dict(
            id=order.pk,
            code=step.cargo_id,
            kind=step.kind,
            ok=step_ok,
            moves=step.move_count,
            rehandles=step.rehandle_count,
            place=place,
            reason=message,
            short_reason=short_reason,
            reason_code='' if step_ok else step.reason,
        ))

    return dict(
        feasible=scenario.feasible,
        evaluated_any=True,
        total_moves=scenario.total_moves,
        rehandles=scenario.rehandles,
        items=items,
        reasons=reasons,
        missing=missing,
    )


@require_roles('operator')
@require_http_methods(['POST'])
def outbound_validate(request):
    lanes, scope = _parse_request_lanes(request)
    if lanes is None or len(lanes) != len(TIERS):
        return _response(ok=False, reasons=['작업 순서 형식이 올바르지 않습니다.'], status=400)

    submitted = [entry for lane in lanes for entry in lane['orders']]
    submitted_ids = [oid for oid, _ in submitted]
    if len(submitted_ids) != len(set(submitted_ids)):
        return _response(ok=False, reasons=['작업 대기열이 바뀌었습니다. 최신 순서를 다시 불러와 검증하세요.'], status=409)

    outbound_orders = {
        order.pk: order
        for order in open_outbound_orders().select_related('cargo', 'planning_observation').order_by('priority_group', 'sequence', 'pk')
    }
    schedule_orders = {
        order.pk: order
        for order in open_schedule_orders().select_related('cargo', 'planning_observation').order_by('priority_group', 'sequence', 'pk')
    }
    submitted_set = set(submitted_ids)
    if scope == 'schedule':
        if submitted_set != set(schedule_orders):
            return _response(ok=False, reasons=['작업 대기열이 바뀌었습니다. 최신 순서를 다시 불러와 검증하세요.'], status=409)
        open_orders = schedule_orders
        is_mixed = any(o.kind == 'INBOUND' for o in open_orders.values())
    elif submitted_set == set(schedule_orders) and (submitted_set != set(outbound_orders) or not outbound_orders):
        open_orders = schedule_orders
        is_mixed = any(o.kind == 'INBOUND' for o in open_orders.values())
    elif submitted_set == set(outbound_orders):
        open_orders = outbound_orders
        is_mixed = False
    else:
        return _response(ok=False, reasons=['반출 대기열이 바뀌었습니다. 최신 순서를 다시 불러와 검증하세요.'], status=409)

    for oid, version in submitted:
        if open_orders[oid].version != version:
            return _response(ok=False, reasons=['반출 대기열이 바뀌었습니다. 최신 순서를 다시 불러와 검증하세요.'], status=409)

    running_ids = running_order_ids(open_orders)
    seen_non_running = False
    for oid in submitted_ids:
        if oid in running_ids:
            if seen_non_running:
                msg = (
                    '실행 중인 작업보다 앞에 둘 수 없습니다.'
                    if is_mixed
                    else '실행 중인 반출 작업보다 앞에 둘 수 없습니다.'
                )
                return _response(ok=False, reasons=[msg])
        else:
            seen_non_running = True

    observation = selected_observation(request, default_latest=True)
    if observation is None:
        return _response(ok=False, reasons=['야드 현황이 없어 순서를 검증할 수 없습니다.'])
    if observation.payload.get('schema') != 'grid-placement-v1':
        return _response(ok=False, reasons=['공간 배치 야드 현황이 없어 순서를 검증할 수 없습니다.'])

    snapshot = spatial_snapshot(observation)
    policy = spatial_policy(observation.condition.payload)
    submitted_orders = [open_orders[oid] for oid in submitted_ids]
    footprints = _latest_footprints(submitted_orders)
    now = timezone.now()

    submitted_outbound_codes = {
        order.cargo.code for order in submitted_orders if order.kind == 'OUTBOUND'
    }
    base_schedule = {
        code: rank
        for code, rank in _outbound_schedule().items()
        if code not in submitted_outbound_codes
    }
    proposed_schedule = _build_proposed_schedule(lanes, open_orders, base_schedule)

    try:
        result = _simulate_orders(
            snapshot,
            policy,
            submitted_orders,
            footprints=footprints,
            now=now,
            schedule=proposed_schedule,
            observation=observation,
        )
    except ValueError:
        return _response(ok=False, reasons=['설치 조건을 확인할 수 없어 순서를 검증할 수 없습니다.'])

    if not result['evaluated_any']:
        return _response(
            ok=False,
            reasons=['현재 야드 현황에 있는 반출 화물이 없습니다.'],
            missing=result['missing'],
        )

    current_orders = list(open_orders.values())
    current_summary = None
    if current_orders:
        cur_schedule = dict(base_schedule)
        for order in current_orders:
            if order.kind == 'OUTBOUND':
                cur_schedule[order.cargo.code] = (
                    tier_of(order.priority_group),
                    order.sequence if order.tie_policy == 'FIXED' else None,
                )
        try:
            cur_result = _simulate_orders(
                snapshot,
                policy,
                current_orders,
                footprints=footprints,
                now=now,
                schedule=cur_schedule,
                observation=observation,
            )
            if cur_result['evaluated_any']:
                current_summary = dict(
                    ok=cur_result['feasible'],
                    moves=cur_result['total_moves'],
                    rehandles=cur_result['rehandles'],
                )
        except ValueError:
            current_summary = None

    return _response(
        ok=result['feasible'],
        reasons=result['reasons'],
        moves=result['total_moves'],
        rehandles=result['rehandles'],
        items=result['items'],
        missing=result['missing'],
        current=current_summary,
    )
