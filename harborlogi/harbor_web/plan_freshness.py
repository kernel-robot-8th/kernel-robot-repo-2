"""Shared checks for changed orders, queue order, and yard observations."""
from django.utils import timezone
from django.db.models import Exists, Q

from harbor_web.models import IntakeOccurrence, Observation
from harbor_web.order_queue import queue_digest
from harbor_web.site_config import constraints


def plan_changes(order, plan, *, current_queue_digest=None):
    if not plan:
        return []
    changes = []
    if current_queue_digest is None:
        current_queue_digest = queue_digest()
    if order.version != plan.order_version or plan.queue_digest != current_queue_digest:
        changes.append(dict(code='PLAN_QUEUE_CHANGED', message='이 계획을 짠 뒤 요청 내용이나 반출 순서가 바뀌었습니다. 지금 순서로 다시 짜세요.'))
    scoped = Exists(IntakeOccurrence.objects.filter(order_id=order.pk))
    later_receipt = (Q(observed_at__gt=plan.observation.observed_at)
                     | Q(observed_at=plan.observation.observed_at, pk__gt=plan.observation_id))
    later_revision = ((~scoped & Q(pk__gt=plan.observation_id))
                      | (scoped & Q(provenance__in=('DECLARED', 'PORT_EVENT')) & later_receipt))
    if Observation.objects.filter(later_revision, source_id=plan.observation.source_id).exists():
        changes.append(dict(code='OBSERVATION_REVISION_CHANGED', message='야드 현황이 새로 들어왔습니다. 아래 그림은 이전 현황 기준입니다.'))
    age = timezone.now() - plan.observation.observed_at
    if not timezone.timedelta(0) <= age <= constraints(plan.condition.payload).max_observation_age:
        changes.append(dict(code='OBSERVATION_NOT_FRESH', message='야드 현황이 오래됐습니다. 새 현황을 받아야 계획할 수 있습니다.'))
    return changes
