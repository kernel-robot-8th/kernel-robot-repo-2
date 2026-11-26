"""Read-only work hub built from registered orders and saved execution records."""
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db.models import CharField, Exists, OuterRef, Q, Subquery
from django.db.models.functions import Cast
from django.utils import timezone

from harbor_web.models import Audit, CellPlanDispatch, Command, Observation, Order, Plan
from harbor_web.order_queue import TIERS, completed_order_ids, tier_of
from harbor_web.plan_service import _outbound_schedule
from harbor_web.presentation import reason_text
from harbor_web.rearrangement import current_authorization
from harbor_web.page_helpers import page
from harbor_web.reorder_validate import _latest_footprints, _placement_label, _simulate_orders, short_hold_reason
from harbor_web.site_config import move_seconds_estimate, spatial_policy, spatial_snapshot
from harbor_web.work_state import work_state, shown_state

SIMULATION_LIMIT = None
_OBSERVATION_UNSET = object()


def format_eta(eta_at):
    if eta_at is None:
        return ''
    return timezone.localtime(eta_at).strftime('%H:%M')


_format_eta = format_eta


def _apply_schedule_etas(schedule, *, base_time, move_seconds, step_moves_by_id):
    """Attach per-task ETA and cutoff overrun flag using already-computed move counts (no DB writes)."""
    for row in schedule:
        row['eta_at'] = None
        row['eta_text'] = format_eta(None)
        row['eta_iso'] = ''
        row['eta_late'] = False
    ordered = sorted(schedule, key=lambda row: row['state'] != '진행 중')
    if SIMULATION_LIMIT is not None:
        ordered = ordered[:SIMULATION_LIMIT]
    cumulative_moves = 0
    for row in ordered:
        if row.get('sim_status') != 'READY':
            continue
        eta_at = base_time + timedelta(seconds=cumulative_moves * move_seconds)
        row['eta_at'] = eta_at
        row['eta_text'] = format_eta(eta_at)
        row['eta_iso'] = timezone.localtime(eta_at).isoformat()
        cutoff = row['order'].cutoff_at
        row['eta_late'] = bool(cutoff is not None and eta_at > cutoff)
        cumulative_moves += step_moves_by_id.get(row['order'].pk, 0)


def _apply_schedule_simulation(
    schedule,
    *,
    now,
    base_time,
    observation=_OBSERVATION_UNSET,
    snapshot=None,
    outbound_ranks=None,
    reservation_entries=None,
):
    """Simulate pending schedule entries in memory without writing to DB."""
    move_sec = move_seconds_estimate()
    for row in schedule:
        row['card_place'] = row['place']
        row['card_place_kind'] = 'place' if row['place'] else ''
        row['sim_status'] = None
        row['eta_at'] = None
        row['eta_text'] = format_eta(None)
        row['eta_iso'] = ''
        row['eta_late'] = False
    if not schedule:
        return move_sec
    if observation is _OBSERVATION_UNSET:
        observation = Observation.objects.select_related('condition').order_by('-pk').first()
    if observation is None or not isinstance(observation.payload, dict) or observation.payload.get('schema') != 'grid-placement-v1':
        return move_sec
    move_sec = move_seconds_estimate(observation.condition.payload)
    try:
        resolved_snapshot = snapshot if snapshot is not None else spatial_snapshot(observation)
        policy = spatial_policy(observation.condition.payload)
    except (ValueError, ValidationError):
        return move_sec
    ordered_schedule = sorted(schedule, key=lambda row: row['state'] != '진행 중')
    window = ordered_schedule if SIMULATION_LIMIT is None else ordered_schedule[:SIMULATION_LIMIT]
    window_orders = [row['order'] for row in window]
    inbound_orders = [order for order in window_orders if order.kind == 'INBOUND']
    footprints = _latest_footprints(inbound_orders)
    ranks = _outbound_schedule() if outbound_ranks is None else outbound_ranks
    try:
        result = _simulate_orders(
            resolved_snapshot,
            policy,
            window_orders,
            footprints=footprints,
            now=now,
            schedule=ranks,
            observation=observation,
            reservation_entries=reservation_entries,
        )
    except (ValueError, ValidationError):
        return move_sec
    step_by_id = {item['id']: item for item in result['items']}
    missing_codes = set(result['missing'])
    step_moves_by_id = {}
    for row in window:
        order = row['order']
        step = step_by_id.get(order.pk)
        if step is not None:
            if step['ok']:
                row['sim_status'] = 'READY'
                step_moves_by_id[order.pk] = (step['moves'] or 1) if order.kind == 'INBOUND' else step['moves']
                if order.kind == 'INBOUND':
                    row['card_place'] = step['place']
                    row['card_place_kind'] = 'place'
                    summary_parts = []
                    if step['moves'] > 0:
                        summary_parts.append(f"이동 {step['moves']}회")
                    if step['place']:
                        summary_parts.append(step['place'])
                    row['plan_summary'] = ' · '.join(summary_parts)
                else:
                    row['card_place'] = (
                        f"이동 {step['moves']}회 · 재취급 {step['rehandles']}회"
                        if step['rehandles'] > 0
                        else f"이동 {step['moves']}회"
                    )
                    row['card_place_kind'] = 'moves'
                    summary_parts = [f"이동 {step['moves']}회", f"재취급 {step['rehandles']}회"]
                    dest = order.cargo.destination if order.cargo_id else ''
                    if dest:
                        summary_parts.append(dest)
                    row['plan_summary'] = ' · '.join(summary_parts)
            else:
                row['sim_status'] = 'HOLD'
                short = step.get('short_reason') or short_hold_reason(step['reason_code'], order.kind)
                row['card_place'] = short
                row['card_place_kind'] = 'blocked'
                detail = (
                    reason_text(step['reason_code'])
                    if step['reason_code'] and step['reason_code'] != 'OUTBOUND_BEFORE_INBOUND'
                    else step['reason']
                )
                row['plan_summary'] = f'보류 · {detail}'
        elif order.kind == 'OUTBOUND' and order.cargo_id and order.cargo.code in missing_codes:
            row['sim_status'] = 'HOLD'
            row['card_place'] = short_hold_reason('OUTBOUND_NOT_OBSERVED', 'OUTBOUND')
            row['card_place_kind'] = 'blocked'
            row['plan_summary'] = f"보류 · {reason_text('OUTBOUND_NOT_OBSERVED')}"
        else:
            row['sim_status'] = 'UNCOMPUTED'
            row['card_place'] = ''
            row['card_place_kind'] = 'uncomputed'
            row['plan_summary'] = ''
    tail = [] if SIMULATION_LIMIT is None else ordered_schedule[SIMULATION_LIMIT:]
    for row in tail:
        row['sim_status'] = 'UNCOMPUTED'
        row['card_place'] = ''
        row['card_place_kind'] = 'uncomputed'
        row['plan_summary'] = ''
    _apply_schedule_etas(schedule, base_time=base_time, move_seconds=move_sec, step_moves_by_id=step_moves_by_id)
    return move_sec


def schedule_etas_for_orders(
    orders,
    *,
    observation,
    now,
    base_time,
    running_order_id=None,
    snapshot=None,
    outbound_ranks=None,
    reservation_entries=None,
):
    """Compute per-order ETAs reusing the work hub schedule simulation without DB writes."""
    rows = [
        dict(
            order=order,
            place=None,
            state='진행 중' if (running_order_id is not None and order.pk == running_order_id) else '대기',
        )
        for order in orders
        if order.kind in ('OUTBOUND', 'INBOUND')
    ]
    _apply_schedule_simulation(
        rows,
        now=now,
        base_time=base_time,
        observation=observation,
        snapshot=snapshot,
        outbound_ranks=outbound_ranks,
        reservation_entries=reservation_entries,
    )
    return {
        row['order'].pk: dict(
            eta_at=row['eta_at'],
            eta_text=row['eta_text'],
            eta_iso=row['eta_iso'],
            eta_late=row['eta_late'],
        )
        for row in rows
    }


def hub_context(*, now=None):
    now = timezone.now() if now is None else now
    start = timezone.localtime(now).replace(hour=0, minute=0, second=0, microsecond=0)
    today_cmd_audits = Audit.objects.filter(action='command.completed', object_type='Command',
        created_at__gte=start, created_at__lte=now).values('object_id')
    today_cp_audits = Audit.objects.filter(action='cell_plan.completed', object_type='CellPlanDispatch',
        created_at__gte=start, created_at__lte=now).values('object_id')
    today_cmd_exists = Command.objects.filter(order_id=OuterRef('pk'), state='COMPLETED').annotate(
        audit_id=Cast('pk', CharField())).filter(audit_id__in=Subquery(today_cmd_audits))
    today_cp_exists = CellPlanDispatch.objects.filter(order_id=OuterRef('pk'), state='COMPLETED').annotate(
        audit_id=Cast('pk', CharField())).filter(audit_id__in=Subquery(today_cp_audits))
    latest_plan = Plan.objects.filter(order_id=OuterRef('pk')).order_by('-pk')
    latest_cp = CellPlanDispatch.objects.filter(order_id=OuterRef('pk')).order_by('-pk')
    orders = list(Order.objects.filter(
        ~Q(pk__in=completed_order_ids()) | Exists(today_cmd_exists) | Exists(today_cp_exists)
    ).select_related('cargo', 'planning_observation').annotate(
        latest_plan_id=Subquery(latest_plan.values('pk')[:1]),
        latest_cp_id=Subquery(latest_cp.values('pk')[:1]),
    ).order_by('priority_group', 'sequence', 'pk'))
    latest_command = Command.objects.filter(order_id=OuterRef('order_id')).order_by('-pk')
    first_completion = Audit.objects.filter(action='command.completed', object_type='Command',
        object_id=Cast(OuterRef('pk'), CharField())).order_by('created_at').values('created_at')[:1]
    cp_completion = Audit.objects.filter(action='cell_plan.completed', object_type='CellPlanDispatch',
        object_id=Cast(OuterRef('pk'), CharField())).order_by('created_at').values('created_at')[:1]
    commands, completed = {}, {}
    order_ids = [o.pk for o in orders]
    for command in Command.objects.filter(order_id__in=order_ids).filter(
            Q(pk=Subquery(latest_command.values('pk')[:1])) | Q(state='COMPLETED')
    ).annotate(completed_at=Subquery(first_completion)).order_by('pk'):
        commands[command.order_id] = command
        if command.state == 'COMPLETED':
            previous = completed.get(command.order_id)
            if (previous is None or (command.completed_at is not None and
                    (previous.completed_at is None or command.completed_at < previous.completed_at))):
                completed[command.order_id] = command
    cell_plans, cp_completed = {}, {}
    if any(o.latest_cp_id is not None for o in orders):
        for cp in CellPlanDispatch.objects.filter(order_id__in=order_ids).annotate(
            completed_at=Subquery(cp_completion)
        ).order_by('pk'):
            cell_plans[cp.order_id] = cp
            if cp.state == 'COMPLETED' and cp.order_id not in completed:
                previous = cp_completed.get(cp.order_id)
                if (previous is None or (cp.completed_at is not None and
                        (previous.completed_at is None or cp.completed_at < previous.completed_at))):
                    cp_completed[cp.order_id] = cp

    done_map = {**cp_completed, **completed}
    orders = [order for order in orders if order.pk not in done_map or (
        done_map[order.pk].completed_at is not None and start <= done_map[order.pk].completed_at <= now)]
    plans = {p.pk: p for p in Plan.objects.filter(
        pk__in=[o.latest_plan_id for o in orders if o.latest_plan_id]).select_related('condition')}
    from harbor_web.executor import status as executor_status
    auto_run = executor_status()['enabled']
    past, pending = [], []
    for order in orders:
        done_obj = done_map.get(order.pk)
        command = done_obj if isinstance(done_obj, Command) else commands.get(order.pk)
        cp = done_obj if isinstance(done_obj, CellPlanDispatch) else (None if command is not None else cell_plans.get(order.pk))
        plan = plans.get(order.latest_plan_id)
        recorded_at = done_obj.completed_at if done_obj is not None else None
        state = work_state(command, plan, cell_plan_dispatch=cp)
        href = f'/work/{command.pk}/' if command else (
            f'/plans/{cp.plan_id}/' if (cp and cp.plan_id) else (f'/plans/{plan.pk}/' if plan else f'/requests/{order.pk}/')
        )
        row = dict(order=order, command=command, cell_plan_dispatch=cp, plan=plan, recorded_at=recorded_at,
            done=bool(recorded_at),
            tier=tier_of(order.priority_group),
            tier_name=order.tier_label,
            kind={'INBOUND': '입고', 'OUTBOUND': '반출', 'REARRANGE': '재배치'}[order.kind],
            state=shown_state(state, command, cell_plan_dispatch=cp),
            tone={'진행 중': 'running', '완료': 'done', '처리할 것': 'attention'}.get(state, 'waiting'),
            shipper=order.cargo.shipper_name if order.cargo_id else '',
            voyage=order.voyage_code,
            cutoff_text=timezone.localtime(order.cutoff_at).strftime('%m-%d %H:%M') if order.cutoff_at else '',
            cutoff_iso=timezone.localtime(order.cutoff_at).isoformat() if order.cutoff_at else '',
            href=href)
        row['place'] = None
        if (order.kind == 'INBOUND' and plan and plan.order_version == order.version
                and plan.result.get('status') == 'READY'
                and plan.result.get('plan_kind') == 'inbound-placement'
                and plan.result.get('recommendation')):
            row['place'] = _placement_label(plan.result['recommendation'], plan.condition.payload)
        summary_parts = []
        if plan and plan.order_version == order.version and plan.result.get('status') == 'READY':
            moves = plan.result.get('moves') or []
            if order.kind == 'OUTBOUND' and moves:
                rehandles = next((i for i, m in enumerate(moves) if m.get('cargo_id') == order.cargo.code), 0)
                summary_parts.extend((f'이동 {len(moves)}회', f'재취급 {rehandles}회'))
            elif moves:
                summary_parts.append(f'이동 {len(moves)}회')
        dest = row['place'] or (order.cargo.destination if order.cargo_id else '')
        if dest:
            summary_parts.append(dest)
        row['plan_summary'] = ' · '.join(summary_parts)
        row['action_label'] = ''
        row['action_href'] = ''
        if recorded_at:
            past.append(row)
        else:
            # Automatic mode computes placements itself; no manual "calculate" button there.
            row['auto'] = auto_run or order.auto_replan
            if order.kind == 'INBOUND':
                if row['place']:
                    row['action_label'] = '자리 결정하기'
                    row['action_href'] = f'/plans/{plan.pk}/'
                elif not row['auto']:
                    row['action_label'] = '자리 계산하기'
                    row['action_href'] = f'/requests/{order.pk}/'
            pending.append(row)

    past.sort(key=lambda row: (row['recorded_at'], row['order'].pk))
    upcoming = sorted(pending, key=lambda row: row['state'] != '진행 중')
    next_row = next((row for row in upcoming if row['state'] != '진행 중'), None)
    if next_row is not None:
        next_row['is_next'] = True
    if past and upcoming and upcoming[0]['state'] == '대기':
        past[-1]['state'] = '완료 · 다음 작업 대기 중'
    running_row = next((row for row in upcoming if row['state'] == '진행 중'), None)
    if running_row is not None and running_row['command'] is not None:
        eta_base = running_row['command'].created_at
    elif running_row is not None and running_row['cell_plan_dispatch'] is not None:
        eta_base = running_row['cell_plan_dispatch'].created_at
    else:
        eta_base = now
    schedule = [row for row in pending if row['order'].kind in ('OUTBOUND', 'INBOUND')]
    for idx, row in enumerate(schedule, start=1):
        row['seq_num'] = idx
    move_sec = _apply_schedule_simulation(schedule, now=now, base_time=eta_base)
    schedule_tiers = [
        dict(group=group, name=name, rows=[row for row in schedule if row['tier'] == group])
        for group, name in TIERS
    ]
    return dict(timeline=past + upcoming,
        done_rows=past,
        schedule=schedule,
        schedule_tiers=schedule_tiers,
        eta_base_iso=timezone.localtime(eta_base).isoformat(),
        move_seconds_estimate=int(move_sec) if float(move_sec).is_integer() else move_sec,
        outbound=[row for row in pending if row['order'].kind == 'OUTBOUND'],
        rearrangements=[row for row in pending if row['order'].kind == 'REARRANGE'],
        authorization=current_authorization(now))


def work_hub(request):
    return page(request, 'work.html', section='work', **hub_context())
