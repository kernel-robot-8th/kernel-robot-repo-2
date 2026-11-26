"""Read-only exceptions from existing operations facts and field assignments."""
from datetime import timedelta
import math
import os
from pathlib import Path

from django.conf import settings
from django.db.models import Max
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from harbor.departure import DepartureLedger
from harbor_web import access
from harbor_web.auth import require_roles, roles
from harbor_web.intake_pages import intake_alert_items
from harbor_web.models import Audit, CellPlanDispatch, DepartureIntent, FieldEpisode, Observation
from harbor_web.operations import operations_context
from harbor_web.page_helpers import selected_observation
from harbor_web.work_state import work_state

DEFAULT_RELEASE_OVERDUE_MINUTES = 10.0


def release_overdue_minutes() -> float:
    raw = os.environ.get('HARBOR_RELEASE_OVERDUE_MINUTES')
    if raw in (None, ''):
        raw = getattr(settings, 'HARBOR_RELEASE_OVERDUE_MINUTES', None)
    if raw in (None, '') or isinstance(raw, bool):
        return DEFAULT_RELEASE_OVERDUE_MINUTES
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_RELEASE_OVERDUE_MINUTES
    return value if math.isfinite(value) and value > 0 else DEFAULT_RELEASE_OVERDUE_MINUTES


def _has_release_permit(episode):
    if DepartureIntent.objects.filter(episode=episode).exclude(release_event_id='').exists():
        return True
    path = os.environ.get('HARBOR_LEDGER_PATH', '').strip()
    if not path or not Path(path).exists():
        return False
    return DepartureLedger(path).has_permit(episode.vehicle_id, since=episode.created_at)


ATTENTION_TEXT = {
    '관측 없음': ('야드 현황이 없습니다', '야드 연결 상태를 확인해야 합니다.', '야드 보기'),
    '관측 유효 시간 밖': ('야드 현황을 새로 받아야 합니다', '야드 현황이 오래돼 이 계획은 쓸 수 없습니다.', '현황 선택'),
    '같은 출처에 새 관측 있음': ('새 야드 현황 있음', '', '최신으로 보기'),
    'HOLD 계획': ('이 일은 지금 못 합니다', '', '이유 보기'),
    '계획 다시 짜야 함': ('계획 다시 짜야 함', '계획을 짠 뒤 순서나 야드가 바뀌었습니다.', '계획 다시 짜기'),
}
# Short category shown on each alert chip ("야드 현황 1건") instead of a repeated "처리할 것".
ATTENTION_KIND = {'관측 없음': '야드 현황', '관측 유효 시간 밖': '야드 현황', '같은 출처에 새 관측 있음': '야드 현황',
                  'HOLD 계획': '작업 막힘', '계획 다시 짜야 함': '계획', '인식 불일치': '인식 불일치'}
COMMAND_TEXT = {
    'UNKNOWN': 'ROSY 응답을 확인하지 못했습니다. 자동으로 다시 보내지 않습니다.',
    'BLOCKED': '설치 조건이나 야드 현황, 복구 조건을 확인해야 합니다.',
    'CANCEL_PENDING': '취소 결과와 실제 정지 여부를 확인해야 합니다.',
}
YARD_FOLLOW_SKIP_REASONS = {
    'NEWER_OBSERVATION_EXISTS': '더 새로운 야드 현황이 이미 있음',
    'BASE_OBSERVATION_UNAVAILABLE': '기준 야드 현황 형식이 맞지 않음',
    'NO_PLANNED_STEPS': '적용할 이동 단계가 없음',
    'INVALID_SPATIAL_CONDITION': '설치 기준 형식이 맞지 않음',
    'INVALID_STEP': '이동 단계 정보가 올바르지 않음',
    'STEP_CARGO_UNKNOWN': '입고 화물 정보를 알 수 없음',
    'CARGO_ALREADY_PLACED': '화물이 이미 야드에 배치돼 있음',
    'INVALID_SRC_SLOT': '출발 위치 주소가 올바르지 않음',
    'INVALID_DST_SLOT': '도착 위치 주소가 올바르지 않음',
    'SRC_SLOT_MISMATCH': '출발 칸의 화물이 계획과 다름',
    'SRC_SLOT_EMPTY': '출발 칸이 비어 있음',
    'DST_SLOT_OCCUPIED': '도착 칸이 이미 차 있음',
    'CARGO_FOOTPRINT_MISSING': '화물 크기 정보가 없음',
    'OBSERVATION_APPLY_FAILED': '야드 현황 저장에 실패함',
    'UNEXPECTED_ERROR': '자동 반영 중 예상치 못한 오류 발생',
}


def _yard_follow_skip_alert_items():
    skips = list(
        Audit.objects.filter(action='yard.follow_skipped', object_type='CellPlanDispatch')
        .order_by('-created_at', '-pk')[:20]
    )
    if not skips:
        return []
    by_dispatch_id = {}
    for entry in skips:
        if entry.object_id not in by_dispatch_id and entry.object_id.isdigit():
            by_dispatch_id[entry.object_id] = entry
    if not by_dispatch_id:
        return []
    dispatch_ids = [int(k) for k in by_dispatch_id]
    dispatches = {
        d.pk: d
        for d in CellPlanDispatch.objects.select_related('order__cargo', 'observation')
        .filter(pk__in=dispatch_ids)
    }
    if not dispatches:
        return []
    revs = [f'cell-plan-{pk}' for pk in dispatches]
    followed = set(
        Observation.objects.filter(source_revision__in=revs).values_list('source_id', 'source_revision')
    )
    condition_ids = {d.observation.condition_id for d in dispatches.values()}
    latest_obs_at = dict(
        Observation.objects.filter(condition_id__in=condition_ids)
        .values('condition_id')
        .annotate(latest=Max('created_at'))
        .values_list('condition_id', 'latest')
    )
    items = []
    for key, entry in by_dispatch_id.items():
        dispatch = dispatches.get(int(key))
        if dispatch is None:
            continue
        if (dispatch.observation.source_id, f'cell-plan-{dispatch.pk}') in followed:
            continue
        latest_at = latest_obs_at.get(dispatch.observation.condition_id)
        if latest_at is not None and latest_at > entry.created_at:
            continue
        reason_code = (entry.detail or {}).get('reason', '') if isinstance(entry.detail, dict) else ''
        reason_label = YARD_FOLLOW_SKIP_REASONS.get(reason_code, '자동 반영 조건이 맞지 않음')
        cargo = dispatch.order.cargo.code if dispatch.order.cargo_id else '자동 재배치'
        items.append(dict(
            kind='야드 현황',
            title=f'야드 갱신 못 함 · {reason_label}',
            detail=f'{cargo} · 완료된 셀 작업 결과를 야드 현황에 자동 반영하지 못했습니다. 야드 현황을 확인하세요.',
            count=1,
            href='/inventory/',
            action='야드 보기',
        ))
    return items


def alerts_context(request, *, observation=None, operations=None):
    held = roles(request.user)
    can_read = bool(held & access.READ)
    alerts = []
    if can_read:
        if operations is None:
            if observation is None:
                observation = selected_observation(request, default_latest=True)
            operations = operations_context(observation)
        else:
            observation = operations.get('observation')
        for item in operations['attention']:
            href = item['href']
            if href.startswith('/work/'):
                # Work state owns problem classification; dispatching alone is normal progress.
                if not item['label'].startswith('완료 인계 · '):
                    continue
                title, detail, action = ('완료 기록 확인 필요',
                    '완료 기록에서 반출 이동을 찾지 못했습니다. 작업 기록을 살펴보세요.', '작업 보기')
                kind = '완료 기록'
            else:
                title, detail, action = ATTENTION_TEXT.get(item['label'],
                    ('업무 상태 확인 필요', '현재 업무 상태를 살펴보세요.', '상세 보기'))
                kind = ATTENTION_KIND.get(item['label'], '업무')
            if item['label'] == 'HOLD 계획':
                detail = item['reason']
            if item['label'] == '같은 출처에 새 관측 있음':
                href = '/'
            if href.startswith('#'):
                href = f'/?observation={observation.pk}{href}'
            alerts.append(dict(kind=kind, title=title, detail=detail, count=item['count'], href=href, action=action))
        for row in operations['handoffs']:
            command = row['command']
            if work_state(command) != '처리할 것':
                continue
            cargo = command.order.cargo.code if command.order.cargo_id else '자동 재배치'
            alerts.append(dict(kind='작업 확인', title=f'{cargo} · 작업 확인 필요',
                detail=COMMAND_TEXT.get(command.state, '작업 상태를 확인해야 합니다.'),
                count=1, href=f'/work/{command.pk}/', action='작업 보기'))
    can_open_field = bool(held & {'operator', 'admin', 'field'})
    cached_episodes = getattr(request, '_open_field_episodes', None)
    if (held & {'operator', 'admin'} or can_read) and cached_episodes is not None:
        episodes = cached_episodes
        if not held & {'operator', 'admin'}:
            can_open_field = False
    else:
        episodes = FieldEpisode.objects.filter(state='OPEN').select_related('order__cargo').order_by('created_at', 'pk')
        if not held & {'operator', 'admin'}:
            if can_read:
                can_open_field = False  # Readers can see the alert, but field details require assignment.
            else:
                episodes = episodes.filter(assigned_to=request.user)
    now = timezone.now()
    overdue_after = timedelta(minutes=release_overdue_minutes())
    for episode in episodes:
        cargo = episode.order.cargo.code if episode.order.cargo_id else '자동 재배치'
        age = now - episode.created_at
        if age >= overdue_after and not _has_release_permit(episode):
            title = f'{cargo} · ROSY 사건이 안 옴 → 비상 적재면 확인'
            detail = f'차량 {episode.vehicle_id} · {int(age.total_seconds() // 60)}분 경과'
        else:
            title = f'{cargo} · 적재면 확인 대기'
            detail = f'차량 {episode.vehicle_id}'
        alerts.append(dict(kind='현장 확인', title=title, detail=detail, count=1,
            href=f'/field/{episode.pk}/' if can_open_field else None, action='배정 보기'))
    intake_status = ''
    if can_read:
        intake_items, intake_status = intake_alert_items(can_read)
        alerts.extend(dict(item, kind='반입 확인') for item in intake_items)
        from harbor_web.site_recognition_view import site_recognition_alert_items
        alerts.extend(site_recognition_alert_items(
            request,
            observation=observation,
            can_open_field=bool(held & {'operator', 'admin', 'field'}),
            kind=ATTENTION_KIND['인식 불일치'],
        ))
        alerts.extend(_yard_follow_skip_alert_items())
    return dict(alerts=alerts, alert_total=sum(item['count'] for item in alerts), intake_status=intake_status)


@require_roles(*access.EVERYONE)
@require_http_methods(['GET'])
def alerts(request):
    return render(request, 'alerts.html', dict(section='alerts', **alerts_context(request)))
