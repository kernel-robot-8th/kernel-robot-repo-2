"""Read-only context for the yard page: open queue schedule, in-memory 3D previews, move arrows, and outbound chain 3D."""
from dataclasses import asdict, replace
from datetime import timedelta
import json
from types import SimpleNamespace

from django.utils import timezone

from harbor.process import anchor_address
from harbor.model import OutboundRequest, YARD_ARMS
from harbor.outbound import OutboundProposal, plan_outbound
from harbor.placement import plan_placement
from harbor.spatial import CargoShape, Placement, plan_rearrangement, plan_spatial
from harbor_web import services
from harbor_web.board import _is_empty_rearrange
from harbor_web.models import Audit, CargoFootprint, CellPlanDispatch, Command, Order, Plan
from harbor_web.order_queue import completed_order_ids, outbound_lanes
from harbor_web.plan_service import DECIDE_ACTION, _installed_arm_candidates, _outbound_schedule
from harbor_web.site_config import move_seconds_estimate
from harbor_web.tier_simulation import tier_recommendations
from harbor_web.work_hub import format_eta
from harbor_web.work_state import work_state
from harbor_web.workspace import _yard_stages, spatial_stages
from harbor_web.yard_occupancy import occupancy_map
from harbor_web.yard_rows import place_context, placement_text, route_text
from harbor_web.yard_scene import attach_cargo_metadata, cargo_scene_metadata, present_stage, scene_for
from harbor_web.yard_view import spatial_yard_view

KIND_LABELS = {'OUTBOUND': '반출', 'INBOUND': '입고', 'REARRANGE': '재배치'}
ACTIVE_COMMAND_STATES = ('DISPATCHING', 'RECONCILED', 'PREPARED')
ACTIVE_CELL_PLAN_STATES = ('SUBMITTING', 'PARTIALLY_ADMITTED', 'QUEUED', 'RUNNING')
PREVIEW_COMPUTE_LIMIT = 8

SHORT_HOLD_REASONS = {
    'INBOUND_ALREADY_OBSERVED': '이미 야드에 있음',
    'NO_LEGAL_PLACEMENT': '놓을 자리 없음',
    'PLACEMENT_SEARCH_LIMIT_REACHED': '자리 계산 한도 초과',
    'OBSERVATION_CARGO_LIMIT_REACHED': '화물 수 한도 초과',
    'INCOMING_FOOTPRINT_REQUIRED': '화물 크기 미등록',
    'SPATIAL_OBSERVATION_REQUIRED': '공간 배치 현황 필요',
    'NO_LEGAL_SPATIAL_PLAN': '임시·복귀 경로 없음',
    'SPATIAL_SEARCH_LIMIT_REACHED': '경로 계산 한도 초과',
    'PRIORITY_ORDER_BLOCKED': '앞선 반출 대기',
    'INBOUND_PLANNER_NOT_AVAILABLE': '입고 계산 불가',
    'OBSERVATION_NOT_CURRENT': '현황 유효 시간 초과',
    'PHYSICAL_HOLDING_REQUIRES_RECONCILIATION': '들고 있는 화물 확인 필요',
    'OUTBOUND_NOT_OBSERVED': '야드에 화물 없음',
    'OUTBOUND_NOT_IN_A_BAY': '보관 칸에 없음',
    'OUTBOUND_ALREADY_ON_STAND_REQUIRES_EVIDENCE': '반출대 완료 확인 필요',
    'OUTBOUND_STAND_FULL': '반출대 가득 참',
    'OUTBOUND_MOVE_UNAVAILABLE': '반출 이동 불가',
    'NO_LEGAL_TEMPORARY_BAY': '임시 칸 없음',
    'ARM_REACH_UNVERIFIED': '팔 도달 미확인',
    'NO_ARM_REACHES_MOVE': '도달 가능한 팔 없음',
    'NO_REARRANGEMENT_GAIN': '재배치 이득 없음',
}


def _short_hold_reason(code):
    return SHORT_HOLD_REASONS.get(code, '확인 필요')


def _arrows_from_moves(display_moves):
    return [
        dict(
            number=move['number'],
            cargo=move['cargo_id'],
            phase=move['phase_label'],
            source=move['source_place'] if move.get('spatial') else f"{move['source']} {move['source_level']}층",
            destination=move['destination_place'] if move.get('spatial') else f"{move['destination']} {move['destination_level']}층",
            arm=move.get('primary_arm', ''),
        )
        for move in display_moves
    ]


def _order_title(order):
    if order.title:
        return order.title
    if order.cargo_id:
        return f'{order.cargo.code} {KIND_LABELS.get(order.kind, order.kind)}'
    return '자동 재배치'


def _command_priority(command, cell_plan=None):
    if command is not None and command.state in ACTIVE_COMMAND_STATES:
        rank = 0 if command.state in ('DISPATCHING', 'RECONCILED') else 1
        return (rank, -command.pk)
    if cell_plan is not None and cell_plan.state in ACTIVE_CELL_PLAN_STATES:
        return (0, -cell_plan.pk)
    return (99, 0)


def _ordered_open_orders(open_orders, commands, cell_plans, latest_commands, latest_plans):
    """Order open jobs identically to the main control board (current active job first, then queue order)."""
    pending = [
        order for order in open_orders
        if not _is_empty_rearrange(
            order,
            command=latest_commands.get(order.pk),
            plan=latest_plans.get(order.pk),
        )
    ]
    if not pending:
        return []
    pending_ids = {order.pk for order in pending}
    running = next((c for c in commands if c.order_id in pending_ids and work_state(c) == '진행 중'), None)
    attention = next((c for c in commands if c.order_id in pending_ids and work_state(c) == '처리할 것'), None)
    active_cmd = running or attention
    active_cp = None
    if active_cmd is None:
        running_cp = next(
            (cp for cp in cell_plans if cp.order_id in pending_ids and work_state(cell_plan_dispatch=cp) == '진행 중'),
            None,
        )
        attention_cp = next(
            (cp for cp in cell_plans if cp.order_id in pending_ids and work_state(cell_plan_dispatch=cp) == '처리할 것'),
            None,
        )
        active_cp = running_cp or attention_cp
    active_order_id = active_cmd.order_id if active_cmd is not None else (active_cp.order_id if active_cp is not None else None)
    if active_order_id is None:
        return pending
    first = next((order for order in pending if order.pk == active_order_id), None)
    if first is None:
        return pending
    return [first, *(order for order in pending if order.pk != active_order_id)]


def _saved_auto_inbound_reservations(observation):
    """Bulk-read saved automatic inbound reservations once per page load (avoids N+1 queries)."""
    auto_orders = list(
        Order.objects.filter(
            auto_replan=True,
            kind='INBOUND',
            planning_observation__source_id=observation.source_id,
            planning_observation__condition_id=observation.condition_id,
        )
        .select_related('cargo')
        .order_by('pk')
    )
    if not auto_orders:
        return {}
    seen = {item['cargo_id'] for item in observation.payload.get('placements', [])}
    candidate_ids = [order.pk for order in auto_orders if order.cargo.code not in seen]
    if not candidate_ids:
        return {}
    active_ids = set(
        Command.objects.filter(order_id__in=candidate_ids, state__in=ACTIVE_COMMAND_STATES)
        .values_list('order_id', flat=True)
    )
    eligible_ids = [oid for oid in candidate_ids if oid not in active_ids]
    if not eligible_ids:
        return {}
    latest_auto_plans = {}
    for plan in (
        Plan.objects.filter(order_id__in=eligible_ids)
        .select_related('observation')
        .order_by('-pk')
    ):
        latest_auto_plans.setdefault(plan.order_id, plan)
    valid_plans = {
        oid: plan
        for oid, plan in latest_auto_plans.items()
        if (
            plan.result.get('status') == 'READY'
            and plan.result.get('plan_kind') == 'inbound-placement'
            and plan.observation.source_id == observation.source_id
            and plan.condition_id == observation.condition_id
        )
    }
    if not valid_plans:
        return {}
    decisions = {}
    for audit in (
        Audit.objects.filter(
            action=DECIDE_ACTION,
            object_type='Plan',
            object_id__in=[str(plan.pk) for plan in valid_plans.values()],
        )
        .order_by('pk')
    ):
        decisions.setdefault(audit.object_id, audit)
    reservations = {}
    for oid, plan in valid_plans.items():
        decision = decisions.get(str(plan.pk))
        recommendation = decision.detail['recommendation'] if decision else plan.result.get('recommendation')
        incoming = plan.result.get('incoming_shape')
        if recommendation is None or incoming is None:
            continue
        try:
            shape = CargoShape(**incoming)
            placement = Placement(**recommendation)
        except (TypeError, ValueError):
            continue
        reservations[oid] = frozenset(
            (
                placement.area_id,
                placement.row + (n if placement.orientation == 90 else 0),
                placement.column + (n if placement.orientation == 0 else 0),
                placement.level,
            )
            for n in range(shape.length_cells)
        )
    return reservations


def _advance_spatial_moves(snapshot, moves, *, remove_cargo_id=None):
    """Advance a spatial snapshot through moves and remove an outbound cargo once retrieved."""
    current = snapshot
    for move in moves:
        destination = Placement(**move['destination_placement'])
        current = current.with_placements(tuple(
            destination if p.cargo_id == move['cargo_id'] else p
            for p in current.placements
        ))
    if remove_cargo_id:
        current = replace(
            current,
            shapes=tuple(shape for shape in current.shapes if shape.cargo_id != remove_cargo_id),
            placements=tuple(placement for placement in current.placements if placement.cargo_id != remove_cargo_id),
        )
    return current


def _preview_plan_result(
    order,
    observation,
    *,
    is_spatial,
    spatial_snap,
    spatial_pol,
    legacy_yard,
    legacy_constraints,
    schedule,
    footprints,
    now,
    arm_candidates,
    reserved_cells=frozenset(),
):
    """In-memory preview calculation without writing Plan or Audit rows."""
    try:
        if order.kind == 'OUTBOUND':
            if is_spatial:
                if spatial_snap is None or spatial_pol is None:
                    return dict(status='HOLD', reason='SPATIAL_OBSERVATION_REQUIRED', moves=[])
                proposal = plan_spatial(
                    spatial_snap,
                    OutboundRequest(order.cargo.code, spatial_snap.revision),
                    spatial_pol,
                    now=now,
                    schedule=schedule,
                )
            else:
                if legacy_yard is None or legacy_constraints is None:
                    return dict(status='HOLD', reason='OUTBOUND_MOVE_UNAVAILABLE', moves=[])
                codes = {order.cargo.code, *(c for b in legacy_yard.bays for c in b.cargo), *(h.cargo_id for h in legacy_yard.holdings)}
                if CargoFootprint.objects.filter(length_cells__gt=1, cargo__code__in=codes).exists():
                    proposal = OutboundProposal('HOLD', 'SPATIAL_OBSERVATION_REQUIRED', legacy_yard.revision)
                else:
                    proposal = plan_outbound(
                        legacy_yard,
                        OutboundRequest(order.cargo.code, legacy_yard.revision),
                        legacy_constraints,
                        now=now,
                        arm_candidates=arm_candidates,
                    )
        elif order.kind == 'INBOUND':
            if not is_spatial or spatial_snap is None or spatial_pol is None:
                return dict(status='HOLD', reason='INBOUND_PLANNER_NOT_AVAILABLE', moves=[])
            footprint = footprints.get(order.cargo_id)
            if footprint is None:
                return dict(status='HOLD', reason='INCOMING_FOOTPRINT_REQUIRED', moves=[])
            incoming = CargoShape(order.cargo.code, footprint.length_cells, footprint.pk)
            proposal = plan_placement(
                spatial_snap, incoming, spatial_pol, schedule, now=now, reserved_cells=reserved_cells,
            )
        elif order.kind == 'REARRANGE':
            if not is_spatial or spatial_snap is None or spatial_pol is None:
                return dict(status='HOLD', reason='SPATIAL_OBSERVATION_REQUIRED', moves=[])
            proposal = plan_rearrangement(
                spatial_snap, spatial_pol, schedule,
                now=now, expected_revision=spatial_snap.revision,
                k=1, max_moves=2, outbound_pending=False, requested=True, pending_inbound=False,
            )
        else:
            return dict(status='HOLD', reason='OUTBOUND_MOVE_UNAVAILABLE', moves=[])
    except ValueError:
        return dict(status='HOLD', reason='SPATIAL_OBSERVATION_REQUIRED', moves=[])
    result = json.loads(json.dumps(asdict(proposal)))
    if order.kind == 'INBOUND' and is_spatial and footprints.get(order.cargo_id):
        fp = footprints[order.cargo_id]
        result['incoming_shape'] = dict(cargo_id=order.cargo.code, length_cells=fp.length_cells, footprint_revision=fp.pk)
    return result


def _inbound_scene(
    order,
    observation,
    result,
    *,
    spatial_snap,
    spatial_pol,
    labels,
    slot,
    footprints,
    title,
    recommendation=None,
    cargo_meta=None,
):
    """Build a 2-frame belt -> destination 3D scene, arrow, and projected snapshot for one inbound recommendation."""
    recommendation = recommendation or result.get('recommendation')
    if not recommendation or spatial_snap is None:
        return [], None, [], None
    cargo_code = recommendation.get('cargo_id') or (order.cargo.code if order.cargo_id else '')
    if not cargo_code or any(p.cargo_id == cargo_code for p in spatial_snap.placements):
        return [], None, [], None
    incoming_meta = result.get('incoming_shape') or {}
    footprint = footprints.get(order.cargo_id)
    length_cells = incoming_meta.get('length_cells') or (footprint.length_cells if footprint else (slot.size if slot else 1))
    footprint_rev = incoming_meta.get('footprint_revision') or (footprint.pk if footprint else 0)
    try:
        rec_placement = Placement(
            cargo_code,
            recommendation['area_id'],
            int(recommendation['row']),
            int(recommendation['column']),
            int(recommendation['level']),
            int(recommendation.get('orientation', 0)),
        )
        incoming = CargoShape(cargo_code, int(length_cells), int(footprint_rev))
        projected = replace(
            spatial_snap,
            shapes=spatial_snap.shapes + (incoming,),
            placements=spatial_snap.placements + (rec_placement,),
        )
    except (KeyError, TypeError, ValueError):
        return [], None, [], None

    primary_arm = ''
    if spatial_pol is not None:
        yard_bays = {(c.row, c.column): c.bay_id for c in spatial_snap.layout.cells}
        cells = projected.cells(rec_placement)
        locations = (
            {yard_bays[r, c] for _, r, c, _ in cells}
            if rec_placement.area_id == 'yard'
            else {rec_placement.area_id}
        )
        primary_arm = next(
            (arm for arm in YARD_ARMS if all((arm, loc) in spatial_pol.arm_reach for loc in locations)),
            '',
        )

    dst_site = anchor_address(rec_placement.area_id, rec_placement.row, rec_placement.column, rec_placement.level)
    dst_place = placement_text(recommendation, labels, slot)
    display_move = dict(
        number=1,
        cargo_id=cargo_code,
        phase_label='입고',
        source='belt',
        destination=dst_site,
        source_place='반입(컨베이어)',
        destination_place=dst_place,
        primary_arm=primary_arm,
        spatial=True,
        destination_placement=dict(recommendation),
        route=f'반입(컨베이어) → {dst_place}',
    )
    stage0 = present_stage(
        dict(
            spatial_yard_view(spatial_snap, target=cargo_code),
            index=0,
            phase='현재',
            label='현재 야드 배치',
            move=None,
            panel_id=f'yard-in-{order.pk}-0',
        ),
        observation.condition.payload,
    )
    stage1 = present_stage(
        dict(
            spatial_yard_view(projected, target=cargo_code),
            index=1,
            phase='입고',
            label=f'1단계 후 예상 · {cargo_code} 입고',
            move=display_move,
            panel_id=f'yard-in-{order.pk}-1',
        ),
        observation.condition.payload,
    )
    stages = [stage0, stage1]
    scene = scene_for(stages)
    if scene is None:
        return [], None, [], None

    yard_area = next((a for a in scene['areas'] if a['id'] == 'yard'), scene['areas'][0])
    belt_cols = max(slot.size if slot else 1, int(length_cells))
    belt_x = round(min(a['x'] for a in scene['areas']) - belt_cols - 1.2, 2)
    belt_y = yard_area['y'] + (yard_area['rows'] - 1) // 2
    scene['areas'].append(dict(
        id='belt',
        label='반입(컨베이어)',
        rows=1,
        columns=belt_cols,
        levels=1,
        x=belt_x,
        y=belt_y,
        addresses={f'1,{c},1': 'belt' for c in range(1, belt_cols + 1)},
    ))
    scene['frames'][0]['boxes'].append(dict(
        id=cargo_code,
        area='belt',
        row=1,
        column=1,
        level=1,
        width=int(length_cells),
        depth=1,
        x=belt_x,
        y=belt_y,
        z=0,
        role='target',
        label='입고 화물',
        positions=['belt'],
        footprint_revision=int(footprint_rev),
        orientation=0,
        size=f'{int(length_cells)}×1',
    ))
    for box in scene['frames'][1]['boxes']:
        if box['id'] == cargo_code:
            box['label'] = '입고 화물'

    arrows = [dict(
        number=1,
        cargo=cargo_code,
        phase='입고',
        source='반입(컨베이어)',
        destination=dst_place,
        arm=primary_arm,
    )]
    scene['rehandle'] = []
    scene['arrows'] = arrows
    scene['title'] = title
    attach_cargo_metadata(scene, cargo_meta)
    return stages, scene, arrows, projected


def outbound_chain_replay(observation, *, allowed_order_ids=None, limit=PREVIEW_COMPUTE_LIMIT, cargo_meta=None):
    """Build one chained multi-frame 3D scene across bounded open OUTBOUND orders (never INBOUND)."""
    if observation is None or observation.payload.get('schema') != 'grid-placement-v1':
        return None
    raw_lanes = outbound_lanes()
    lanes = []
    remaining = limit
    for lane in raw_lanes:
        orders = lane['orders']
        if allowed_order_ids is not None:
            orders = [order for order in orders if order.pk in allowed_order_ids]
        if remaining is not None:
            if remaining <= 0:
                orders = []
            else:
                orders = orders[:remaining]
                remaining -= len(orders)
        lanes.append(dict(lane, orders=orders))
    if not any(lane['orders'] for lane in lanes):
        return None
    try:
        current = services.spatial_snapshot(observation)
        policy = services.spatial_policy(observation.condition.payload)
    except ValueError:
        return None
    recommendations = tier_recommendations(observation, lanes=lanes)
    schedule = _outbound_schedule()
    now = timezone.now()
    labels, slot = place_context(observation.condition.payload)

    stages = [present_stage(
        dict(spatial_yard_view(current), index=0, phase='현재', label='현재 야드 배치', move=None, panel_id='yard-chain-0'),
        observation.condition.payload,
    )]
    arrows = []
    chained_codes = []
    rehandled = set()
    move_number = 0
    ranks = dict(schedule)

    for lane in lanes:
        group = lane['group']
        placed = {placement.cargo_id for placement in current.placements}
        rec = recommendations.get(group)
        if rec is not None:
            best = rec.get('best') or {}
            if not best.get('feasible'):
                break
            raw_steps = best.get('steps') or ()
            tier_codes = [
                getattr(step, 'cargo_id', None) or step['cargo_id']
                for step in raw_steps
                if (getattr(step, 'status', None) or step.get('status')) == 'READY'
            ]
        else:
            tier_codes = [order.cargo.code for order in lane['orders'] if order.cargo.code in placed]
        if not tier_codes:
            continue
        tier_ranks = dict(ranks)
        for pos, code in enumerate(tier_codes):
            tier_ranks[code] = (group, pos)
        tier_failed = False
        for cargo_id in tier_codes:
            if cargo_id not in {placement.cargo_id for placement in current.placements}:
                tier_failed = True
                break
            proposal = plan_spatial(
                current, OutboundRequest(cargo_id, current.revision), policy, now=now, schedule=tier_ranks,
            )
            if proposal.status != 'READY':
                tier_failed = True
                break
            chained_codes.append(cargo_id)
            step_blockers = {m.cargo_id for m in proposal.moves if m.phase_label == '재취급'}
            rehandled.update(step_blockers)
            for move in proposal.moves:
                move_number += 1
                current = current.with_placements(tuple(
                    move.destination_placement if p.cargo_id == move.cargo_id else p
                    for p in current.placements
                ))
                move_dict = json.loads(json.dumps(asdict(move)))
                display = dict(
                    move_dict,
                    number=move_number,
                    source_level=move.source_placement.level,
                    destination_level=move.destination_placement.level,
                    spatial=True,
                    route=route_text(move_dict, labels, slot),
                    source_place=placement_text(move_dict['source_placement'], labels, slot),
                    destination_place=placement_text(move_dict['destination_placement'], labels, slot),
                )
                arrows.append(dict(
                    number=move_number,
                    cargo=move.cargo_id,
                    phase=move.phase_label,
                    source=display['source_place'],
                    destination=display['destination_place'],
                    arm=move.primary_arm,
                ))
                stages.append(present_stage(dict(
                    spatial_yard_view(current, target=cargo_id, blockers=step_blockers),
                    index=move_number,
                    phase=move.phase_label,
                    label=f'{move_number}단계 후 예상 · {cargo_id} {move.phase_label}',
                    move=display,
                    panel_id=f'yard-chain-{move_number}',
                ), observation.condition.payload))
            # Clear the retrieved target from the outbound stand before planning the next container.
            current = replace(
                current,
                shapes=tuple(shape for shape in current.shapes if shape.cargo_id != cargo_id),
                placements=tuple(placement for placement in current.placements if placement.cargo_id != cargo_id),
            )
            tier_ranks.pop(cargo_id, None)
            ranks.pop(cargo_id, None)
        if tier_failed:
            break

    if len(chained_codes) < 2 or move_number == 0:
        return None
    scene = scene_for(stages)
    if scene is None:
        return None
    scene['kind'] = 'outbound-chain'
    scene['rehandle'] = sorted(rehandled)
    scene['arrows'] = arrows
    scene['title'] = '남은 반출 연쇄 3D'
    scene['order'] = chained_codes
    attach_cargo_metadata(scene, cargo_meta)
    return dict(
        key='outbound-chain',
        scene_id='upcoming-scene-outbound-chain',
        title='남은 반출 연쇄 3D',
        kind='OUTBOUND_CHAIN',
        kind_label='반출 연쇄',
        cargo_codes=chained_codes,
        summary=f"{' → '.join(chained_codes)} · 총 {move_number}회 이동",
        stages=stages,
        scene=scene,
        arrows=arrows,
    )


def yard_page_context(observation, *, now=None):
    """Build open queue schedule, in-memory 3D previews (never saving Plans), outbound chain replay, and current move arrows."""
    if observation is None:
        return dict(upcoming=[], active_arrows=[], active_upcoming=None, outbound_chain=None)
    wall_now = timezone.now() if now is None else now
    open_orders = list(
        Order.objects.exclude(pk__in=completed_order_ids())
        .select_related('cargo')
        .order_by('priority_group', 'sequence', 'pk')
    )
    if not open_orders:
        return dict(upcoming=[], active_arrows=[], active_upcoming=None, outbound_chain=None)
    order_ids = [order.pk for order in open_orders]
    latest_plans = {}
    for plan in (
        Plan.objects.filter(
            order_id__in=order_ids,
            observation=observation,
            condition=observation.condition,
        )
        .select_related('order__cargo', 'observation__condition', 'condition')
        .order_by('-pk')
    ):
        latest_plans.setdefault(plan.order_id, plan)
    rearrange_ids = [o.pk for o in open_orders if o.kind == 'REARRANGE' and o.pk not in latest_plans]
    fallback_plans = dict(latest_plans)
    if rearrange_ids:
        for plan in (
            Plan.objects.filter(order_id__in=rearrange_ids)
            .select_related('order__cargo', 'observation__condition', 'condition')
            .order_by('-pk')
        ):
            fallback_plans.setdefault(plan.order_id, plan)

    all_commands = list(
        Command.objects.filter(order_id__in=order_ids)
        .select_related('plan')
        .order_by('-pk')
    )
    latest_commands = {}
    active_commands = {}
    for command in all_commands:
        latest_commands.setdefault(command.order_id, command)
        if command.state not in ('COMPLETED', 'CANCELLED'):
            active_commands.setdefault(command.order_id, command)

    all_cell_plans = list(
        CellPlanDispatch.objects.filter(order_id__in=order_ids)
        .select_related('plan')
        .order_by('-pk')
    )
    active_cell_plans = {}
    for cp in all_cell_plans:
        if cp.state in ACTIVE_CELL_PLAN_STATES:
            active_cell_plans.setdefault(cp.order_id, cp)

    ordered_orders = _ordered_open_orders(open_orders, all_commands, all_cell_plans, latest_commands, fallback_plans)
    if not ordered_orders:
        return dict(upcoming=[], active_arrows=[], active_upcoming=None, outbound_chain=None)

    is_spatial = observation.payload.get('schema') == 'grid-placement-v1'
    labels, slot = place_context(observation.condition.payload) if is_spatial else ({}, None)
    spatial_snap = spatial_pol = legacy_yard = legacy_constraints = None
    if is_spatial:
        try:
            spatial_snap = services.spatial_snapshot(observation)
            spatial_pol = services.spatial_policy(observation.condition.payload)
        except ValueError:
            spatial_snap = spatial_pol = None
    else:
        try:
            legacy_yard = services.snapshot(observation)
            legacy_constraints = services.constraints(observation.condition.payload)
        except ValueError:
            legacy_yard = legacy_constraints = None

    cargo_ids = [o.cargo_id for o in ordered_orders if o.cargo_id]
    footprints = {}
    if cargo_ids:
        for fp in CargoFootprint.objects.filter(cargo_id__in=cargo_ids).order_by('-pk'):
            footprints.setdefault(fp.cargo_id, fp)

    all_codes = {o.cargo.code for o in ordered_orders if o.cargo_id}
    if is_spatial:
        all_codes.update(item['cargo_id'] for item in observation.payload.get('placements', []) if item.get('cargo_id'))
    else:
        for bay in observation.payload.get('bays', []):
            all_codes.update(bay.get('cargo', []))
    cargo_meta = cargo_scene_metadata(all_codes)

    schedule = _outbound_schedule()
    active_schedule = dict(schedule)
    arm_candidates = None if is_spatial else _installed_arm_candidates()
    preview_now = observation.observed_at or timezone.now()
    base_spatial_snap = spatial_snap
    auto_reservations = (
        _saved_auto_inbound_reservations(observation)
        if is_spatial and spatial_snap is not None
        else {}
    )
    saved_decisions = {}
    saved_inbound_plan_ids = [
        str(plan.pk)
        for plan in latest_plans.values()
        if plan.order.kind == 'INBOUND' and plan.result.get('status') == 'READY'
    ]
    if saved_inbound_plan_ids:
        for audit in (
            Audit.objects.filter(
                action=DECIDE_ACTION,
                object_type='Plan',
                object_id__in=saved_inbound_plan_ids,
            )
            .order_by('pk')
        ):
            saved_decisions.setdefault(audit.object_id, audit)

    first_order = ordered_orders[0]
    first_cmd = active_commands.get(first_order.pk)
    first_cp = active_cell_plans.get(first_order.pk)
    if first_cmd is not None and work_state(first_cmd) == '진행 중':
        eta_base = first_cmd.created_at
    elif first_cp is not None and work_state(cell_plan_dispatch=first_cp) == '진행 중':
        eta_base = first_cp.created_at
    else:
        eta_base = wall_now
    move_sec = move_seconds_estimate(observation.condition.payload)
    cumulative_moves = 0

    upcoming = []
    for index, order in enumerate(ordered_orders):
        saved_plan = latest_plans.get(order.pk)
        use_saved = bool(saved_plan and saved_plan.result.get('status') == 'READY')
        plan = saved_plan if use_saved else None
        command = active_commands.get(order.pk)
        cell_plan = active_cell_plans.get(order.pk)
        active = bool(
            (command and command.state in ACTIVE_COMMAND_STATES)
            or (cell_plan and cell_plan.state in ACTIVE_CELL_PLAN_STATES)
        )
        kind_label = KIND_LABELS.get(order.kind, order.kind)
        title = _order_title(order)
        key = f'plan-{plan.pk}' if plan else f'order-{order.pk}'
        scene_id = f'upcoming-scene-{plan.pk}' if plan else f'upcoming-scene-order-{order.pk}'

        stages, scene, arrows, target_label, hold_reason = [], None, [], '', ''
        summary_parts = []
        preview_skipped = index >= PREVIEW_COMPUTE_LIMIT
        result = None

        if not preview_skipped:
            if use_saved:
                result = plan.result
            else:
                order_reserved = (
                    frozenset().union(*(cells for oid, cells in auto_reservations.items() if oid != order.pk))
                    if order.kind == 'INBOUND' and auto_reservations
                    else frozenset()
                )
                result = _preview_plan_result(
                    order,
                    observation,
                    is_spatial=is_spatial,
                    spatial_snap=spatial_snap,
                    spatial_pol=spatial_pol,
                    legacy_yard=legacy_yard,
                    legacy_constraints=legacy_constraints,
                    schedule=active_schedule,
                    footprints=footprints,
                    now=preview_now,
                    arm_candidates=arm_candidates,
                    reserved_cells=order_reserved,
                )

        if preview_skipped:
            if order.kind in ('OUTBOUND', 'INBOUND'):
                summary_parts.append(order.tier_label)
            summary_parts.append('3D 준비 안 됨')
        elif result is None or result.get('status') != 'READY':
            hold_reason = _short_hold_reason((result or {}).get('reason', ''))
        elif order.kind in ('OUTBOUND', 'REARRANGE'):
            if order.kind == 'OUTBOUND':
                summary_parts.append(order.tier_label)
            moves = result.get('moves') or []
            if moves:
                advanced_snap = None
                try:
                    if is_spatial and spatial_snap is not None:
                        stages, display_moves = spatial_stages(order, observation, moves, snapshot=spatial_snap)
                        advanced_snap = _advance_spatial_moves(
                            spatial_snap,
                            moves,
                            remove_cargo_id=order.cargo.code if order.kind == 'OUTBOUND' and order.cargo_id else None,
                        )
                    else:
                        stages, display_moves = _yard_stages(order, plan or SimpleNamespace(result=result), observation)
                except ValueError:
                    stages, display_moves = [], []
                    if use_saved and is_spatial and base_spatial_snap is not None and base_spatial_snap is not spatial_snap:
                        try:
                            stages, display_moves = spatial_stages(order, observation, moves, snapshot=base_spatial_snap)
                        except ValueError:
                            stages, display_moves = [], []
                            hold_reason = '확인 필요'
                    else:
                        hold_reason = '확인 필요'
                if advanced_snap is not None:
                    spatial_snap = advanced_snap
                    if order.kind == 'OUTBOUND' and order.cargo_id:
                        active_schedule.pop(order.cargo.code, None)
                if stages:
                    arrows = _arrows_from_moves(display_moves)
                    occupancy = occupancy_map(stages[0], display_moves)
                    scene = scene_for(stages)
                    if scene:
                        scene['rehandle'] = occupancy['rehandle_cargo'] if occupancy else []
                        scene['arrows'] = arrows
                        scene['title'] = title
                        attach_cargo_metadata(scene, cargo_meta)
                    arms = sorted({a['arm'] for a in arrows if a['arm']})
                    summary_parts.append(f'이동 {len(arrows)}회')
                    if arms:
                        summary_parts.append(f"팔 {', '.join(arms)}")
            else:
                summary_parts.append('이동 없음')
        elif order.kind == 'INBOUND':
            summary_parts.append(order.tier_label)
            decision = saved_decisions.get(str(plan.pk)) if plan is not None else None
            recommendation = (decision.detail.get('recommendation') if decision else None) or result.get('recommendation')
            if recommendation:
                target_label = f'→ {placement_text(recommendation, labels, slot)}'
                stages, scene, arrows, projected = _inbound_scene(
                    order,
                    observation,
                    result,
                    spatial_snap=spatial_snap,
                    spatial_pol=spatial_pol,
                    labels=labels,
                    slot=slot,
                    footprints=footprints,
                    title=title,
                    recommendation=recommendation,
                    cargo_meta=cargo_meta,
                )
                if projected is not None:
                    spatial_snap = projected
                    auto_reservations.pop(order.pk, None)
                elif use_saved and base_spatial_snap is not None and base_spatial_snap is not spatial_snap:
                    stages, scene, arrows, _ = _inbound_scene(
                        order,
                        observation,
                        result,
                        spatial_snap=base_spatial_snap,
                        spatial_pol=spatial_pol,
                        labels=labels,
                        slot=slot,
                        footprints=footprints,
                        title=title,
                        recommendation=recommendation,
                        cargo_meta=cargo_meta,
                    )
            if plan is not None:
                summary_parts.append('자리 결정 완료' if decision else '자리 결정 대기')
            elif arrows and arrows[0].get('arm'):
                summary_parts.append(f"팔 {arrows[0]['arm']}")

        if not preview_skipped and not hold_reason and result is not None and result.get('status') == 'READY':
            eta_at = eta_base + timedelta(seconds=cumulative_moves * move_sec)
            eta_text = format_eta(eta_at)
            eta_late = bool(order.cutoff_at is not None and eta_at > order.cutoff_at)
            step_moves = len(arrows) if arrows else (1 if order.kind == 'INBOUND' else 0)
            cumulative_moves += step_moves
        else:
            eta_at = None
            eta_text = format_eta(None)
            eta_late = False

        upcoming.append(dict(
            key=key,
            order=order,
            plan=plan,
            command=command,
            cell_plan=cell_plan,
            kind=order.kind,
            kind_label=kind_label,
            title=title,
            summary=' · '.join(summary_parts),
            target_label=target_label,
            hold_reason=hold_reason,
            preview_skipped=preview_skipped,
            eta_at=eta_at,
            eta_text=eta_text,
            eta_late=eta_late,
            is_preview=bool(not use_saved and not preview_skipped and scene is not None),
            stages=stages,
            scene=scene,
            scene_id=scene_id,
            arrows=arrows,
            active=active,
        ))

    active_candidates = [item for item in upcoming if item['active'] and item['arrows']]
    active_upcoming = (
        min(active_candidates, key=lambda item: _command_priority(item['command'], item['cell_plan']))
        if active_candidates
        else None
    )
    active_arrows = active_upcoming['arrows'] if active_upcoming else []
    head_outbound_ids = {
        order.pk for order in ordered_orders[:PREVIEW_COMPUTE_LIMIT] if order.kind == 'OUTBOUND'
    }
    outbound_chain = outbound_chain_replay(observation, allowed_order_ids=head_outbound_ids, cargo_meta=cargo_meta)
    return dict(
        upcoming=upcoming,
        active_arrows=active_arrows,
        active_upcoming=active_upcoming,
        outbound_chain=outbound_chain,
    )
