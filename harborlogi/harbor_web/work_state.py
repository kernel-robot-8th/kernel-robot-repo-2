"""Read-only work labels and schedule counts; plans never prove execution."""
from django.db.models import CharField, Min, OuterRef, Subquery
from django.db.models.functions import Cast
from django.utils import timezone

from harbor_web.models import Audit, Command, Order
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


def work_state(command=None, plan=None, *, next_command=None, next_plan=None):
    """Label one work item, optionally naming the wait after a completed item.

    Pass the current Command/latest Plan, and the actual next item if one exists.
    Cancelled execution leaves the registered work waiting; it is never completion.
    This is presentation only, not permission to execute a waiting plan.
    """
    if command is not None:
        label = COMMAND_STATES.get(command.state, '처리할 것')
    elif plan is not None:
        label = {'READY': '대기', 'HOLD': '처리할 것'}.get(
            plan.result.get('status'), '처리할 것')
    else:
        label = '대기'
    if (label == '완료' and (next_command is not None or next_plan is not None)
            and work_state(next_command, next_plan) == '대기'):
        return '완료 · 다음 작업 대기 중'
    return label


def day_progress(*, now=None):
    """Today's completions / (today's completions + all unfinished Orders).

    Count each registered work once, including rearrangement. Use the first
    command.completed audit timestamp, as operations._throughput does, rather
    than mutable updated_at. A completion without an audit has no known date.
    """
    now = timezone.now() if now is None else now
    start = timezone.localtime(now).replace(hour=0, minute=0, second=0, microsecond=0)
    first_completion = Audit.objects.filter(
        action='command.completed', object_type='Command',
        object_id=Cast(OuterRef('pk'), CharField()),
    ).order_by('created_at').values('created_at')[:1]
    completed = (Command.objects.filter(state='COMPLETED')
        .annotate(completed_at=Subquery(first_completion))
        .values('order_id').annotate(first_completed_at=Min('completed_at'))
        .filter(first_completed_at__gte=start, first_completed_at__lte=now).count())
    unfinished = Order.objects.exclude(pk__in=completed_order_ids()).count()
    return {'completed': completed, 'total': completed + unfinished}


def shown_state(state, command):
    """Name the problem the way the alert chips do, instead of a generic '처리할 것'."""
    if state != '처리할 것':
        return state
    return '작업 확인' if command is not None else '작업 막힘'
