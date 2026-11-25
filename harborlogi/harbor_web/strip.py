"""Global status strip context processor (WP05); read-only with O(1) bounded queries."""
from datetime import timedelta

from django.db.models import Max, Subquery
from django.utils import timezone

from harbor_web import access, executor, fleet_connection, services
from harbor_web.auth import roles
from harbor_web.fleet_connection import (
    _fleet_live_summary,
    cached_fleet_state,
    clear_strip_fleet_cache,
    record_fleet_state,
)
from harbor_web.models import Audit, CellPlanDispatch, Command, FieldEpisode, Observation, Order, Plan
from harbor_web.order_queue import completed_order_ids
from harbor_web.work_state import day_progress, work_state

KIND_LABELS = {'INBOUND': '입고', 'OUTBOUND': '반출', 'REARRANGE': '재배치'}
STATE_TONES = {'진행 중': 'info', '완료': 'good', '완료 · 다음 작업 대기 중': 'good', '처리할 것': 'attention', '대기': 'neutral'}


def _job_title(order):
    kind = KIND_LABELS.get(order.kind, order.kind)
    if order.cargo_id:
        return f'{order.cargo.code} {kind}'
    return '자동 재배치' if order.kind == 'REARRANGE' else kind


def _selected_or_latest_observation(request):
    raw = request.GET.get('observation', '')
    if raw and raw.isascii() and raw.isdigit():
        obs = Observation.objects.select_related('condition').filter(pk=raw).first()
        if obs is not None:
            return obs
    return Observation.objects.select_related('condition').order_by('-pk').first()


def _unmatched_completed_count(commands):
    latest = {}
    for command in commands:
        if command.state == 'COMPLETED':
            latest.setdefault(command.order_id, command)
    count = 0
    for command in latest.values():
        if command.order.kind != 'OUTBOUND':
            continue
        outbound_id = command.binding.get('outbound_id')
        if not any(move.get('cargo_id') == outbound_id for move in command.binding.get('moves', [])):
            count += 1
    return count


def _alert_count(request, *, held, can_read, observation, stale, superseded, commands, now, cell_plans=()):
    total = 0
    if can_read:
        if observation is None:
            total += 1
        else:
            if stale:
                total += 1
            if superseded:
                total += 1
            commanded_ids = {c.order_id for c in commands} | {cp.order_id for cp in cell_plans}
            latest_ids = Plan.objects.values('order_id').annotate(latest_id=Max('pk')).values('latest_id')
            plans = list(
                Plan.objects.filter(
                    pk__in=Subquery(latest_ids),
                    observation=observation,
                    condition=observation.condition,
                )
                .exclude(order_id__in=commanded_ids)
                .select_related('order')
            )
            if plans:
                digest = services.queue_digest()
                hold = excluded = 0
                for plan in plans:
                    status = plan.result.get('status') if isinstance(plan.result, dict) else None
                    included = (
                        status == 'READY'
                        and not stale
                        and not superseded
                        and plan.order_version == plan.order.version
                        and plan.queue_digest == digest
                    )
                    if status == 'HOLD':
                        hold += 1
                    elif not included:
                        excluded += 1
                total += hold
                if excluded and not (stale or superseded):
                    total += excluded
        total += sum(1 for c in commands if work_state(c) == '처리할 것')
        total += sum(1 for cp in cell_plans if work_state(cell_plan_dispatch=cp) == '처리할 것')
        total += _unmatched_completed_count(commands)
        from harbor_web.intake_pages import read_holds
        total += len(read_holds()[0])
        from harbor_web.site_recognition_view import cached_site_recognition_mismatch_count
        total += cached_site_recognition_mismatch_count(request, observation)

    if held & {'operator', 'admin'} or can_read:
        cached = getattr(request, '_open_field_count', None)
        open_episodes = cached if cached is not None else FieldEpisode.objects.filter(state='OPEN').count()
    else:
        open_episodes = FieldEpisode.objects.filter(state='OPEN', assigned_to=request.user).count()
    return total + open_episodes


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


def _done_href(done_obj, *, can_read):
    if not can_read:
        return None
    if isinstance(done_obj, CellPlanDispatch):
        return f'/plans/{done_obj.plan_id}/' if done_obj.plan_id else f'/requests/{done_obj.order_id}/'
    return f'/work/{done_obj.pk}/'


def _current_job(*, commands, can_read, now, cell_plans=()):
    running = next((c for c in commands if work_state(c) == '진행 중'), None)
    if running is not None:
        return dict(
            title=_job_title(running.order),
            state='진행 중',
            tone=STATE_TONES['진행 중'],
            href=f'/work/{running.pk}/' if can_read else None,
        )
    running_cp = next((cp for cp in cell_plans if work_state(cell_plan_dispatch=cp) == '진행 중'), None)
    if running_cp is not None:
        return dict(
            title=_job_title(running_cp.order),
            state='진행 중',
            tone=STATE_TONES['진행 중'],
            href=_done_href(running_cp, can_read=can_read),
        )
    attention = next((c for c in commands if work_state(c) == '처리할 것'), None)
    if attention is not None:
        return dict(
            title=_job_title(attention.order),
            state='처리할 것',
            tone=STATE_TONES['처리할 것'],
            href=f'/work/{attention.pk}/' if can_read else None,
        )
    attention_cp = next((cp for cp in cell_plans if work_state(cell_plan_dispatch=cp) == '처리할 것'), None)
    if attention_cp is not None:
        return dict(
            title=_job_title(attention_cp.order),
            state='처리할 것',
            tone=STATE_TONES['처리할 것'],
            href=_done_href(attention_cp, can_read=can_read),
        )

    start = timezone.localtime(now).replace(hour=0, minute=0, second=0, microsecond=0)
    next_order = (
        Order.objects.exclude(pk__in=completed_order_ids())
        .select_related('cargo')
        .order_by('priority_group', 'sequence', 'pk')
        .first()
    )
    if next_order is not None:
        next_command = next((c for c in commands if c.order_id == next_order.pk), None)
        next_cp = None if next_command is not None else next((cp for cp in cell_plans if cp.order_id == next_order.pk), None)
        next_plan = Plan.objects.filter(order=next_order).order_by('-pk').first()
        safe_plan = next_plan if (next_plan is None or isinstance(next_plan.result, dict)) else Plan(result={})
        next_state = work_state(next_command, safe_plan, cell_plan_dispatch=next_cp)
        if next_state == '대기':
            done_today = _latest_completed_today(commands, start=start, now=now, cell_plans=cell_plans)
            if done_today is not None:
                done_cmd = done_today if isinstance(done_today, Command) else None
                done_cp = done_today if isinstance(done_today, CellPlanDispatch) else None
                state = (
                    work_state(done_cmd, next_command=next_command, next_plan=next_plan, cell_plan_dispatch=done_cp, next_cell_plan=next_cp)
                    if (next_command is not None or next_cp is not None or next_plan is not None)
                    else '완료 · 다음 작업 대기 중'
                )
                return dict(
                    title=_job_title(done_today.order),
                    state=state,
                    tone=STATE_TONES.get(state, 'neutral'),
                    href=_done_href(done_today, can_read=can_read),
                )
        href = None
        if can_read:
            if next_command is not None:
                href = f'/work/{next_command.pk}/'
            elif next_plan is not None:
                href = f'/plans/{next_plan.pk}/'
            else:
                href = f'/requests/{next_order.pk}/'
        return dict(
            title=_job_title(next_order),
            state=next_state,
            tone=STATE_TONES.get(next_state, 'neutral'),
            href=href,
        )

    done_today = _latest_completed_today(commands, start=start, now=now, cell_plans=cell_plans)
    if done_today is not None:
        return dict(
            title=_job_title(done_today.order),
            state='완료',
            tone=STATE_TONES['완료'],
            href=_done_href(done_today, can_read=can_read),
        )
    return None


def status_strip(request):
    user = getattr(request, 'user', None)
    if user is None or not user.is_authenticated or not user.is_active or user.pk is None:
        return {}
    now = timezone.now()
    held = roles(user)
    can_read = bool(held & access.READ)
    observation = _selected_or_latest_observation(request) if can_read else Observation.objects.order_by('-pk').first()
    stale = superseded = False
    if observation is not None and can_read:
        age = now - observation.observed_at
        stale = not timedelta(0) <= age <= services.constraints(observation.condition.payload).max_observation_age
        superseded = Observation.objects.filter(source_id=observation.source_id, pk__gt=observation.pk).exists()
    elif observation is not None:
        age = now - observation.observed_at
        stale = age < timedelta(0)

    commands = list(Command.objects.select_related('order__cargo').order_by('-pk'))
    cell_plans = list(CellPlanDispatch.objects.select_related('order__cargo').order_by('-pk'))
    alert_count = _alert_count(
        request,
        held=held,
        can_read=can_read,
        observation=observation,
        stale=stale,
        superseded=superseded,
        commands=commands,
        now=now,
        cell_plans=cell_plans,
    )
    progress = day_progress(now=now)
    current_job = _current_job(commands=commands, can_read=can_read, now=now, cell_plans=cell_plans)
    yard_updated = timezone.localtime(observation.observed_at).strftime('%H:%M') if observation is not None else None
    exec_status = executor.status()
    equipment_label, equipment_tone, estop_note = _fleet_live_summary()
    return dict(
        status_strip=dict(
            alert_count=alert_count,
            drive_label=f'처리할 것 {alert_count}건' if alert_count else ('자동 운전 중' if exec_status['enabled'] else exec_status['label']),
            drive_tone='critical' if alert_count else exec_status['tone'],
            day_progress=progress,
            current_job=current_job,
            yard_updated=yard_updated,
            yard_stale=stale,
            equipment_label=equipment_label,
            equipment_tone=equipment_tone,
            executor=exec_status,
            executor_label=exec_status['label'],
            executor_tone=exec_status['tone'],
            estop_note=estop_note,
        )
    )
