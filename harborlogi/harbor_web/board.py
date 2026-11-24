"""Read-only v4.8 situation board context; plans never substitute for execution."""
from copy import deepcopy

from django.db.models import CharField, Min, OuterRef, Subquery
from django.db.models.functions import Cast
from django.utils import timezone

from harbor_web import access, executor
from harbor_web.alert_pages import alerts_context
from harbor_web.auth import roles
from harbor_web.models import Audit, CellPlanDispatch, Command, Order, Plan
from harbor_web.order_queue import completed_order_ids
from harbor_web.work_state import day_progress, order_completion_times, work_state, shown_state
from harbor_web.yard_rows import (
    DEFAULT_SLOT,
    place_context,
    place_label,
    placement_text,
    route_text as format_route_text,
    slot_key,
)

KIND_LABELS = {'INBOUND': '입고', 'OUTBOUND': '반출', 'REARRANGE': '재배치'}
STATE_TONES = {
    '진행 중': 'info',
    '완료': 'good',
    '완료 · 다음 작업 대기 중': 'good',
    '처리할 것': 'attention',
    '대기': 'neutral',
}


def _job_title(order):
    kind = KIND_LABELS.get(order.kind, order.kind)
    if order.cargo_id:
        return f'{order.cargo.code} {kind}'
    return '자동 재배치' if order.kind == 'REARRANGE' else kind


def _is_empty_rearrange(order, *, command=None, plan=None):
    """True when a REARRANGE order produced no moves (HOLD or empty move list)."""
    if order.kind != 'REARRANGE':
        return False
    if command is None:
        if plan is None:
            return False
        result = plan.result if isinstance(plan.result, dict) else {}
        return result.get('status') != 'READY' or not result.get('moves')
    if command.state == 'COMPLETED':
        plan_result = command.plan.result if (command.plan_id and isinstance(command.plan.result, dict)) else {}
        binding = command.binding if isinstance(command.binding, dict) else {}
        return not plan_result.get('moves') and not binding.get('moves')
    if command.state == 'CANCELLED':
        target_plan = plan or (command.plan if command.plan_id else None)
        if target_plan is None:
            return False
        result = target_plan.result if isinstance(target_plan.result, dict) else {}
        return result.get('status') != 'READY' or not result.get('moves')
    plan_result = command.plan.result if (command.plan_id and isinstance(command.plan.result, dict)) else {}
    binding = command.binding if isinstance(command.binding, dict) else {}
    return not plan_result.get('moves') and not binding.get('moves')


def _latest_completed_today(commands, *, start, now, cell_plans=()):
    completed_cmds = {str(c.pk): c for c in commands if c.state == 'COMPLETED'}
    completed_cps = {str(cp.pk): cp for cp in cell_plans if cp.state == 'COMPLETED'}
    if not completed_cmds and not completed_cps:
        return None
    best_at = None
    best_obj = None
    for action, obj_type, pool in (
        ('command.completed', 'Command', completed_cmds),
        ('cell_plan.completed', 'CellPlanDispatch', completed_cps),
    ):
        if not pool:
            continue
        row = (
            Audit.objects.filter(
                action=action,
                object_type=obj_type,
                object_id__in=pool,
                created_at__gte=start,
                created_at__lte=now,
            )
            .order_by('-created_at', '-pk')
            .values('object_id', 'created_at')
            .first()
        )
        if row and (best_at is None or row['created_at'] > best_at):
            best_at = row['created_at']
            best_obj = pool.get(row['object_id'])
    return best_obj


def _done_job_href(done_obj, *, can_read):
    if not can_read:
        return None
    if isinstance(done_obj, CellPlanDispatch):
        return f'/plans/{done_obj.plan_id}/' if done_obj.plan_id else f'/requests/{done_obj.order_id}/'
    return f'/work/{done_obj.pk}/'


def _today_counts(now, *, open_orders, completed_commands, latest_commands, latest_plans):
    start = timezone.localtime(now).replace(hour=0, minute=0, second=0, microsecond=0)
    earliest = order_completion_times()
    today_ids = [oid for oid, at in earliest.items() if start <= at <= now]
    order_kinds = dict(Order.objects.filter(pk__in=today_ids).values_list('pk', 'kind'))
    completed = {'INBOUND': 0, 'OUTBOUND': 0, 'REARRANGE': 0}
    for oid in today_ids:
        kind = order_kinds.get(oid)
        if kind not in completed:
            continue
        if kind == 'REARRANGE':
            cmd = completed_commands.get(oid)
            if cmd is not None and _is_empty_rearrange(cmd.order, command=cmd, plan=cmd.plan):
                continue
        completed[kind] += 1

    pending = {'INBOUND': 0, 'OUTBOUND': 0, 'REARRANGE': 0}
    for order in open_orders:
        if order.kind not in pending:
            continue
        if _is_empty_rearrange(
            order,
            command=latest_commands.get(order.pk),
            plan=latest_plans.get(order.pk),
        ):
            continue
        pending[order.kind] += 1

    return [
        dict(
            kind=kind,
            label=KIND_LABELS[kind],
            completed=completed[kind],
            pending=pending[kind],
        )
        for kind in ('INBOUND', 'OUTBOUND', 'REARRANGE')
    ]


def _move_details(order, *, command=None, plan=None):
    """Extract display move fields and mini-yard slot keys without recomputing."""
    effective_plan = (
        command.plan
        if (command is not None and command.state != 'CANCELLED' and command.plan_id)
        else plan
    )
    default_cargo = order.cargo.code if order.cargo_id else None
    empty = dict(
        cargo=default_cargo,
        arm=None,
        source=None,
        destination=None,
        route_text='',
        origin_key=None,
        dest_key=None,
        move_cargo=None,
    )
    if effective_plan is None or not isinstance(effective_plan.result, dict):
        return empty
    if effective_plan.result.get('status') != 'READY':
        return empty

    spatial = effective_plan.observation.payload.get('schema') == 'grid-placement-v1'
    labels, slot = place_context(effective_plan.condition.payload) if spatial else ({}, None)
    moves = effective_plan.result.get('moves') or []
    if moves:
        move = moves[0]
        move_cargo = move.get('cargo_id') or default_cargo
        arm = move.get('primary_arm')
        if 'source_placement' in move or 'destination_placement' in move:
            src_p = move.get('source_placement')
            dst_p = move.get('destination_placement')
            source = place_label(src_p, labels, slot) if src_p else '—'
            destination = place_label(dst_p, labels, slot) if dst_p else '—'
            route = (
                format_route_text(move, labels, slot)
                if (src_p and dst_p)
                else f'{source} → {destination}'
            )
            origin_key = slot_key(src_p, slot or DEFAULT_SLOT) if src_p else None
            dest_key = slot_key(dst_p, slot or DEFAULT_SLOT) if dst_p else None
        else:
            source = move.get('source', '—')
            destination = move.get('destination', '—')
            route = f'{source} → {destination}'
            origin_key = f"bay-{move['source']}" if move.get('source') else None
            dest_key = f"bay-{move['destination']}" if move.get('destination') else None
        return dict(
            cargo=move_cargo,
            arm=arm,
            source=source,
            destination=destination,
            route_text=route,
            origin_key=origin_key,
            dest_key=dest_key,
            move_cargo=move_cargo,
        )

    rec = effective_plan.result.get('recommendation')
    if isinstance(rec, dict):
        cargo = rec.get('cargo_id') or default_cargo
        source = place_label(None)
        destination = placement_text(rec, labels, slot)
        return dict(
            cargo=cargo,
            arm=None,
            source=source,
            destination=destination,
            route_text=f'{source} → {destination}',
            origin_key=None,
            dest_key=slot_key(rec, slot or DEFAULT_SLOT),
            move_cargo=cargo,
        )
    return empty


def _item_href(order, *, command=None, plan=None, obs_query='', can_read=True):
    if not can_read:
        return None
    if command is not None and command.state != 'CANCELLED':
        return f'/work/{command.pk}/'
    if plan is not None:
        return f'/plans/{plan.pk}/{obs_query}'
    return f'/requests/{order.pk}/{obs_query}'


def _mini_yard(yard, *, origin_key=None, dest_key=None, ghost_cargo=None):
    if yard is None:
        return None
    mini = deepcopy(yard)
    mini.pop('dimensions', None)
    highlight = {k for k in (origin_key, dest_key) if k}
    for area in [mini['yard'], *mini.get('left', []), *mini.get('right', []), *mini.get('below', [])]:
        for lane in area['lanes']:
            for slot in lane['slots']:
                if slot['key'] in highlight:
                    slot['mini_move'] = True
                if dest_key and slot['key'] == dest_key and ghost_cargo:
                    existing = {
                        code.strip()
                        for entry in slot['stack']
                        for code in entry['cargo'].split(',')
                    }
                    if ghost_cargo not in existing:
                        slot['ghost_cargo'] = ghost_cargo
    return mini


def board_context(request, *, observation, operations, now=None):
    """Build v4.8 situation board context for `/` without mutating database or operations."""
    now = timezone.now() if now is None else now
    start = timezone.localtime(now).replace(hour=0, minute=0, second=0, microsecond=0)
    can_read = bool(roles(request.user) & access.READ)
    obs_query = f'?observation={observation.pk}' if observation is not None else ''

    alert_data = alerts_context(request, observation=observation, operations=operations)
    alerts = alert_data['alerts']
    alert_total = alert_data['alert_total']

    progress = day_progress(now=now)
    progress = dict(progress, remaining=max(0, progress['total'] - progress['completed']))

    commands = list(
        Command.objects.select_related(
            'order__cargo',
            'plan__observation__condition',
            'plan__condition',
        ).order_by('-pk')
    )
    cell_plans = list(
        CellPlanDispatch.objects.select_related(
            'order__cargo',
            'plan__observation__condition',
            'plan__condition',
        ).order_by('-pk')
    )
    latest_commands = {}
    completed_commands = {}
    for cmd in commands:
        latest_commands.setdefault(cmd.order_id, cmd)
        if cmd.state == 'COMPLETED':
            completed_commands.setdefault(cmd.order_id, cmd)
    latest_cell_plans = {}
    for cp in cell_plans:
        latest_cell_plans.setdefault(cp.order_id, cp)

    open_orders = list(
        Order.objects.exclude(pk__in=completed_order_ids())
        .select_related('cargo')
        .order_by('priority_group', 'sequence', 'pk')
    )
    open_ids = [order.pk for order in open_orders]

    obs_plans = {}
    if observation is not None and open_ids:
        for plan in (
            Plan.objects.filter(
                order_id__in=open_ids,
                observation=observation,
                condition=observation.condition,
            )
            .select_related('order__cargo', 'observation__condition', 'condition')
            .order_by('pk')
        ):
            obs_plans[plan.order_id] = plan

    rearrange_ids = [o.pk for o in open_orders if o.kind == 'REARRANGE' and o.pk not in obs_plans]
    fallback_plans = dict(obs_plans)
    if rearrange_ids:
        for plan in (
            Plan.objects.filter(order_id__in=rearrange_ids)
            .select_related('order__cargo', 'observation__condition', 'condition')
            .order_by('pk')
        ):
            fallback_plans[plan.order_id] = plan

    counts = _today_counts(
        now,
        open_orders=open_orders,
        completed_commands=completed_commands,
        latest_commands=latest_commands,
        latest_plans=fallback_plans,
    )

    pending_orders = [
        order
        for order in open_orders
        if not _is_empty_rearrange(
            order,
            command=latest_commands.get(order.pk),
            plan=fallback_plans.get(order.pk),
        )
    ]

    running = next((c for c in commands if work_state(c) == '진행 중'), None)
    attention = next((c for c in commands if work_state(c) == '처리할 것'), None)
    active_cmd = running or attention
    active_cp = None
    if active_cmd is None:
        running_cp = next((cp for cp in cell_plans if work_state(cell_plan_dispatch=cp) == '진행 중'), None)
        attention_cp = next((cp for cp in cell_plans if work_state(cell_plan_dispatch=cp) == '처리할 것'), None)
        active_cp = running_cp or attention_cp

    current_job = None
    mini_move = None
    if active_cmd is not None:
        state = work_state(active_cmd)
        details = _move_details(active_cmd.order, command=active_cmd)
        current_job = dict(
            title=_job_title(active_cmd.order),
            state=shown_state(state, active_cmd),
            tone=STATE_TONES.get(state, 'neutral'),
            href=f'/work/{active_cmd.pk}/' if can_read else None,
            arm=details['arm'],
            cargo=details['cargo'],
            source=details['source'],
            destination=details['destination'],
        )
        mini_move = details
        upcoming_orders = [o for o in pending_orders if o.pk != active_cmd.order_id]
    elif active_cp is not None:
        state = work_state(cell_plan_dispatch=active_cp)
        details = _move_details(active_cp.order, plan=active_cp.plan)
        current_job = dict(
            title=_job_title(active_cp.order),
            state=shown_state(state, None, cell_plan_dispatch=active_cp),
            tone=STATE_TONES.get(state, 'neutral'),
            href=_done_job_href(active_cp, can_read=can_read),
            arm=details['arm'],
            cargo=details['cargo'],
            source=details['source'],
            destination=details['destination'],
        )
        mini_move = details
        upcoming_orders = [o for o in pending_orders if o.pk != active_cp.order_id]
    elif pending_orders:
        first_order = pending_orders[0]
        first_cmd = latest_commands.get(first_order.pk)
        first_cp = None if first_cmd is not None else latest_cell_plans.get(first_order.pk)
        first_plan = obs_plans.get(first_order.pk)
        safe_plan = (
            first_plan
            if (first_plan is None or isinstance(first_plan.result, dict))
            else Plan(result={})
        )
        first_state = work_state(first_cmd, safe_plan, cell_plan_dispatch=first_cp)
        done_today = _latest_completed_today(commands, start=start, now=now, cell_plans=cell_plans)
        if first_state == '대기' and done_today is not None:
            done_cmd = done_today if isinstance(done_today, Command) else None
            done_cp = done_today if isinstance(done_today, CellPlanDispatch) else None
            state = (
                work_state(done_cmd, next_command=first_cmd, next_plan=first_plan, cell_plan_dispatch=done_cp, next_cell_plan=first_cp)
                if (first_cmd is not None or first_cp is not None or first_plan is not None)
                else '완료 · 다음 작업 대기 중'
            )
            details = _move_details(done_today.order, command=done_cmd, plan=done_cp.plan if done_cp else None)
            current_job = dict(
                title=_job_title(done_today.order),
                state=state,
                tone=STATE_TONES.get(state, 'good'),
                href=_done_job_href(done_today, can_read=can_read),
                arm=details['arm'],
                cargo=details['cargo'],
                source=details['source'],
                destination=details['destination'],
            )
            upcoming_orders = pending_orders
            mini_move = _move_details(first_order, command=first_cmd, plan=first_plan)
        else:
            details = _move_details(first_order, command=first_cmd, plan=first_plan)
            current_job = dict(
                title=_job_title(first_order),
                state=shown_state(first_state, first_cmd, cell_plan_dispatch=first_cp),
                tone=STATE_TONES.get(first_state, 'neutral'),
                href=_item_href(
                    first_order,
                    command=first_cmd,
                    plan=first_plan,
                    obs_query=obs_query,
                    can_read=can_read,
                ),
                arm=details['arm'],
                cargo=details['cargo'],
                source=details['source'],
                destination=details['destination'],
            )
            mini_move = details
            upcoming_orders = pending_orders[1:]
    else:
        done_today = _latest_completed_today(commands, start=start, now=now, cell_plans=cell_plans)
        if done_today is not None:
            done_cmd = done_today if isinstance(done_today, Command) else None
            done_cp = done_today if isinstance(done_today, CellPlanDispatch) else None
            details = _move_details(done_today.order, command=done_cmd, plan=done_cp.plan if done_cp else None)
            current_job = dict(
                title=_job_title(done_today.order),
                state='완료',
                tone=STATE_TONES['완료'],
                href=_done_job_href(done_today, can_read=can_read),
                arm=details['arm'],
                cargo=details['cargo'],
                source=details['source'],
                destination=details['destination'],
            )
        upcoming_orders = []

    next_jobs = []
    for order in upcoming_orders[:5]:
        cmd = latest_commands.get(order.pk)
        cp = None if cmd is not None else latest_cell_plans.get(order.pk)
        plan = obs_plans.get(order.pk)
        safe_plan = (
            plan if (plan is None or isinstance(plan.result, dict)) else Plan(result={})
        )
        state = work_state(cmd, safe_plan, cell_plan_dispatch=cp)
        details = _move_details(order, command=cmd, plan=plan)
        next_jobs.append(
            dict(
                kind_label=KIND_LABELS.get(order.kind, order.kind),
                title=_job_title(order),
                state=shown_state(state, cmd, cell_plan_dispatch=cp),
                tone=STATE_TONES.get(state, 'neutral'),
                href=_item_href(
                    order,
                    command=cmd,
                    plan=plan,
                    obs_query=obs_query,
                    can_read=can_read,
                ),
                arm=details['arm'],
                route_text=details['route_text'],
            )
        )

    mini_yard = _mini_yard(
        operations.get('yard'),
        origin_key=mini_move['origin_key'] if mini_move else None,
        dest_key=mini_move['dest_key'] if mini_move else None,
        ghost_cargo=mini_move['move_cargo'] if mini_move else None,
    )

    exec_status = executor.status()
    drive_label = '자동 운전 중' if exec_status['enabled'] else exec_status['label']
    return dict(
        drive_label=f'{drive_label} · 처리할 것 {alert_total}건' if alert_total else drive_label,
        drive_tone='attention' if alert_total else exec_status['tone'],
        day_progress=progress,
        counts=counts,
        alerts=alerts,
        alert_total=alert_total,
        current_job=current_job,
        next_jobs=next_jobs,
        mini_yard=mini_yard,
    )
