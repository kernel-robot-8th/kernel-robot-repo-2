from django.conf import settings
from harbor_web.auth import roles


def context(request):
    groups = roles(request.user)
    return dict(app_roles=sorted(groups), can_operate='operator' in groups,
                can_admin='admin' in groups, can_field='field' in groups,
                can_read=bool(groups & {'viewer', 'operator', 'admin'}),
                web_mode=settings.MODE)


_REASON_TEXTS = {
    'PLACEMENT_RECOMMENDATION_REQUIRES_EXECUTION_BINDINGS': '반출 순서를 고려한 입고 위치를 계산했습니다. 실제 입고에는 설치 대응과 승인·관측 근거가 필요합니다.',
    'INBOUND_ALREADY_OBSERVED': '이 화물은 이미 관측에 배치되어 있습니다. 새 입고 위치를 중복 생성하지 않습니다.',
    'NO_LEGAL_PLACEMENT': '화물의 전체 크기·지지·도달 조건을 만족하는 보관 위치가 없습니다.',
    'PLACEMENT_SEARCH_LIMIT_REACHED': '배치 후보 검토 한도에 도달했습니다. 위치를 임의로 확정하지 않고 보류합니다.',
    'OBSERVATION_CARGO_LIMIT_REACHED': '관측 화물 수 한도에 도달해 입고 배치를 보류합니다.',
    'SOURCE_CONDITION_CHANGED': '결속한 출처의 설치 기준이 바뀌었습니다. 새 기준의 관측을 직접 선택해 다시 계산하세요.',
    'INCOMING_FOOTPRINT_REQUIRED': '입고 화물의 점유 크기 원본이 필요합니다.',
    'OTHER_COMMAND_REQUIRES_RECONCILIATION': '다른 인계 작업의 실제 실행 상태를 먼저 대조해야 합니다. 자동 계산으로 실행 중인 배치를 추측하지 않습니다.',
    'SPATIAL_OBSERVATION_REQUIRED': '이 화물의 다칸 점유 크기를 확인할 수 있는 공간 관측 원본이 필요합니다.',
    'NO_LEGAL_SPATIAL_PLAN': '전체 점유 면적과 지지 조건을 만족하는 임시 배치·반출·복귀 계획을 찾지 못했습니다.',
    'SPATIAL_SEARCH_LIMIT_REACHED': '공간 계획의 검토 한도에 도달했습니다. 공간이 없다고 단정하지 않고 보류합니다.',
    'PROPOSAL_REQUIRES_ADMISSION_AND_EVIDENCE': '계획을 만들었습니다. 실행 전 설치 대응과 승인·근거 조건을 확인하세요.',
    'PRIORITY_ORDER_BLOCKED': '앞선 반출 지시가 있습니다. 우선순위와 그룹 안 허용 순서를 확인하세요.',
    'INBOUND_PLANNER_NOT_AVAILABLE': '이 원본 형식으로는 입고 배치를 계산할 수 없습니다. 전체 구역·점유 크기가 포함된 공간 관측을 선택하세요. 입력한 요청은 보존됩니다.',
    'REVISION_CONFLICT': '검토한 관측 revision과 현재 기준이 다릅니다.',
    'OBSERVATION_NOT_CURRENT': '관측이 오래되었거나 시각을 확인할 수 없습니다. 최신 원본이 필요합니다.',
    'PHYSICAL_HOLDING_REQUIRES_RECONCILIATION': '들고 있는 화물이 있어 실제 인계 상태 확인이 필요합니다.',
    'OUTBOUND_NOT_OBSERVED': '목표 화물을 관측 원본에서 찾을 수 없습니다.',
    'OUTBOUND_NOT_IN_A_BAY': '목표 화물이 보관 칸에서 확인되지 않았습니다.',
    'OUTBOUND_ALREADY_ON_STAND_REQUIRES_EVIDENCE': '반출대에 있는 화물의 완료 근거를 확인해야 합니다.',
    'OUTBOUND_STAND_FULL': '반출대에 빈자리가 없습니다.',
    'OUTBOUND_MOVE_UNAVAILABLE': '보관 칸에서 반출대로 허용된 이동이 없습니다.',
    'NO_LEGAL_TEMPORARY_BAY': '방해 화물을 둘 유효한 임시 공간이 없습니다.',
    'ARM_REACH_UNVERIFIED': '팔의 도달 가능성 검증 참조가 없습니다.',
    'NO_ARM_REACHES_MOVE': '이 이동을 수행할 수 있는 팔 대응이 없습니다.',
}


def reason_text(code):
    return _REASON_TEXTS.get(code, '원본과 연결 조건을 확인해야 합니다.')
