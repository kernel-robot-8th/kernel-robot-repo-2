"""Read-only work hub built from registered orders and saved execution records."""
from django.db.models import CharField, Exists, OuterRef, Q, Subquery
from django.db.models.functions import Cast
from django.utils import timezone

from harbor_web.models import Audit, CellPlanDispatch, Command, Order, Plan
from harbor_web.order_queue import completed_order_ids
from harbor_web.rearrangement import current_authorization
from harbor_web.page_helpers import page
from harbor_web.work_state import work_state, shown_state
from harbor_web.yard_rows import place_context, placement_text


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
    ).select_related('cargo').annotate(
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
            kind={'INBOUND': '입고', 'OUTBOUND': '반출', 'REARRANGE': '재배치'}[order.kind],
            state=shown_state(state, command, cell_plan_dispatch=cp),
            tone={'진행 중': 'running', '완료': 'done', '처리할 것': 'attention'}.get(state, 'waiting'),
            href=href)
        if recorded_at:
            past.append(row)
        else:
            row['place'] = None
            # Automatic mode computes placements itself; no manual "calculate" link there.
            row['auto'] = auto_run or order.auto_replan
            row['place_hint'] = '자동 계산 대기' if row['auto'] else '자리 결정 대기'
            if plan and plan.order_version != order.version:
                row['place_hint'] = '요청 바뀜 · 다시 계산'
            elif plan and plan.result.get('status') == 'HOLD':
                row['place_hint'] = '보류 · 요청 확인'
            if (order.kind == 'INBOUND' and plan and plan.order_version == order.version
                    and plan.result.get('status') == 'READY'
                    and plan.result.get('plan_kind') == 'inbound-placement'
                    and plan.result.get('recommendation')):
                labels, slot = place_context(plan.condition.payload)
                row['place'] = placement_text(plan.result['recommendation'], labels, slot)
            pending.append(row)

    past.sort(key=lambda row: (row['recorded_at'], row['order'].pk))
    upcoming = sorted(pending, key=lambda row: row['state'] != '진행 중')
    next_row = next((row for row in upcoming if row['state'] != '진행 중'), None)
    if next_row is not None:
        next_row['is_next'] = True
    if past and upcoming and upcoming[0]['state'] == '대기':
        past[-1]['state'] = '완료 · 다음 작업 대기 중'
    return dict(timeline=past + upcoming,
        outbound=[row for row in pending if row['order'].kind == 'OUTBOUND'],
        inbound=[row for row in pending if row['order'].kind == 'INBOUND'],
        rearrangements=[row for row in pending if row['order'].kind == 'REARRANGE'],
        authorization=current_authorization(now))


def work_hub(request):
    return page(request, 'work.html', section='work', **hub_context())
