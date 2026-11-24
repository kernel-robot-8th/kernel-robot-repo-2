"""Read-only work labels and schedule counts; plans never prove execution."""
from django.db.models import CharField, Exists, Min, OuterRef, Q, Subquery
from django.db.models.functions import Cast
from django.utils import timezone

from harbor_web.models import Audit, CellPlanDispatch, Command, Order
from harbor_web.order_queue import completed_order_ids


COMMAND_STATES = {
    'PREPARED': '대기',
    'DISPATCHING': '진행 중',
    'RECONCILED': '진행 중',
    'COMPLETED': '완료',
    'UNKNOWN': '처리할 것',
    'BLOCKED': '처리할 것',
    'CANCEL_PENDING': '처리할 것',
    'CANCELLED': '대기',
}

CELL_PLAN_STATES = {
    'SUBMITTING': '진행 중',
    'PROPOSED': '대기',
    'PARTIALLY_ADMITTED': '진행 중',
    'QUEUED': '진행 중',
    'RUNNING': '진행 중',
    'COMPLETED': '완료',
    'CANCELLED': '대기',
    'NOT_FOUND': '대기',
    'UNCERTAIN': '처리할 것',
    'PAUSED_HUMAN': '처리할 것',
    'ABORTED': '처리할 것',
}


def work_state(command=None, plan=None, *, next_command=None, next_plan=None, cell_plan_dispatch=None, next_cell_plan=None):
    """Label one work item, optionally naming the wait after a completed item.

    Pass the current Command/CellPlanDispatch/latest Plan, and the actual next item if one exists.
    Cancelled execution leaves the registered work waiting; it is never completion.
    This is presentation only, not permission to execute a waiting plan.
    """
    if command is not None:
        label = COMMAND_STATES.get(command.state, '처리할 것')
    elif cell_plan_dispatch is not None:
        label = CELL_PLAN_STATES.get(cell_plan_dispatch.state, '처리할 것')
    elif plan is not None:
        label = {'READY': '대기', 'HOLD': '처리할 것'}.get(
            plan.result.get('status'), '처리할 것')
    else:
        label = '대기'
    if (label == '완료' and (next_command is not None or next_cell_plan is not None or next_plan is not None)
            and work_state(next_command, next_plan, cell_plan_dispatch=next_cell_plan) == '대기'):
        return '완료 · 다음 작업 대기 중'
    return label


def order_completion_times():
    """Return earliest recorded completion timestamp per Order across Commands and CellPlanDispatches."""
    cmd_first = Audit.objects.filter(
        action='command.completed', object_type='Command',
        object_id=Cast(OuterRef('pk'), CharField()),
    ).order_by('created_at').values('created_at')[:1]
    cp_first = Audit.objects.filter(
        action='cell_plan.completed', object_type='CellPlanDispatch',
        object_id=Cast(OuterRef('pk'), CharField()),
    ).order_by('created_at').values('created_at')[:1]
    earliest = {}
    for model, audit_sub in ((Command, cmd_first), (CellPlanDispatch, cp_first)):
        for row in model.objects.filter(state='COMPLETED').annotate(
            completed_at=Subquery(audit_sub),
        ).values('order_id').annotate(first_completed_at=Min('completed_at')):
            at = row['first_completed_at']
            if at is not None:
                prev = earliest.get(row['order_id'])
                if prev is None or at < prev:
                    earliest[row['order_id']] = at
    return earliest


def day_progress(*, now=None):
    """Today's completions / (today's completions + all unfinished Orders) in constant 2 queries."""
    now = timezone.now() if now is None else now
    start = timezone.localtime(now).replace(hour=0, minute=0, second=0, microsecond=0)
    cmd_first = Audit.objects.filter(
        action='command.completed', object_type='Command',
        object_id=Cast(OuterRef('pk'), CharField()),
    ).order_by('created_at').values('created_at')[:1]
    cp_first = Audit.objects.filter(
        action='cell_plan.completed', object_type='CellPlanDispatch',
        object_id=Cast(OuterRef('pk'), CharField()),
    ).order_by('created_at').values('created_at')[:1]
    cmd_today = Command.objects.filter(order_id=OuterRef('pk'), state='COMPLETED').annotate(
        at=Subquery(cmd_first)).filter(at__gte=start, at__lte=now)
    cp_today = CellPlanDispatch.objects.filter(order_id=OuterRef('pk'), state='COMPLETED').annotate(
        at=Subquery(cp_first)).filter(at__gte=start, at__lte=now)
    cmd_before = Command.objects.filter(order_id=OuterRef('pk'), state='COMPLETED').annotate(
        at=Subquery(cmd_first)).filter(at__lt=start)
    cp_before = CellPlanDispatch.objects.filter(order_id=OuterRef('pk'), state='COMPLETED').annotate(
        at=Subquery(cp_first)).filter(at__lt=start)
    completed = Order.objects.filter(
        (Exists(cmd_today) | Exists(cp_today)) & ~Exists(cmd_before) & ~Exists(cp_before)
    ).count()
    unfinished = Order.objects.exclude(pk__in=completed_order_ids()).count()
    return {'completed': completed, 'total': completed + unfinished}


def shown_state(state, command, *, cell_plan_dispatch=None):
    """Name the problem the way the alert chips do, instead of a generic '처리할 것'."""
    if state != '처리할 것':
        return state
    return '작업 확인' if (command is not None or cell_plan_dispatch is not None) else '작업 막힘'
