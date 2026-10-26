# 실행 binding 확장 — 다칸 배치와 팔 배정

2026-10-27. 결정: [ADR0008](../adr/0008-multicell-cargo-occupancy.md)(다칸 실행 차단 유지), [ADR0005](../adr/0005-harbor-rosy-responsibility-map.md) 결정 3·4(팔 배정·도달 정보). 상위 계획: [설계27 P4](27-business-executor-implementation-plan.md). 상태: **상세설계(제안)** — 두 게이트(다칸 실행 binding, 팔 배정 원본)를 여는 구조를 먼저 정한다.

## 현재 차단

| 게이트 | 위치 | 이유 |
|---|---|---|
| `SPATIAL_EXECUTION_BINDINGS_REQUIRED` | `capability()` — `plan.result['model'] == 'grid-placement-v1'` | 공간 계획에 대응하는 검증된 설치 binding이 없다(ADR0008: legacy ARM binding 재사용 금지) |
| `SPATIAL_OBSERVATION_REQUIRED` | `capability()` — 화물 점유 `length_cells > 1` | 같은 이유로 다칸 화물이 legacy bays 관측에 있어도 실행 차단 |
| `ARM_REACH_UNVERIFIED` / `NO_ARM_REACHES_MOVE` | `plan_outbound` — 설치자 `arm_reach` 표 | (칸×팔) 도달 원본이 임시 수단(ADR0005 결정 4). v1.299 preflight는 시뮬레이션 전용(비시뮬레이션 503 `ARM_PREFLIGHT_CLOSED`)이라 운영 원본이 못 된다(설계27 P4 기록) |

## 제안 1 — 다칸 입고 배치 binding

새 binding 값 `SpatialPlacementBinding`:

- 업무 키: `(cargo_id, footprint_revision, 대상 배치 정규화 튜플)` — 배치 튜플은 `Placement`의 `(area_id, row, column, level, orientation)` 그대로. 입고의 출발은 공급 작업대 고정 앵커 하나다.
- 물리 값: `(workcell_id, instance_id, object_ref, source_ref, target_ref)` — 플랫폼 ARM 계약의 다섯 물리 필드와 동일 모양.
- 일치 검사: 계획의 `recommendation`·`footprint_revision`과 전부 일치(방향 포함)할 때만 binding을 채택한다. 하나라도 다르면 게이트를 유지하고 `SPATIAL_EXECUTION_BINDINGS_REQUIRED`로 알린다.
- 장부 연결: `execution_kind`는 `ARM_TRANSFER` 그대로다(플랫폼은 물리 참조만 본다). `move_arms`는 아래 팔 대응에서, `required_completion`은 기존 `inbound_placed_at_target`·`arms_retreated`를 유지한다.
- 완료 근거: `PlacementEvidence.location`은 배치 주소 문자열(정규화 튜플 직렬화)로 대조한다. [물리 관측 수용 조건](../observation-acceptance.md)의 대응 표에 이 한 줄을 더한다.

## 제안 2 — 팔 배정과 도달의 원본

- binding을 **이동당 하나**에서 **(이동, 팔)당 하나**로 설치한다. 주 팔·대체 팔은 설치 대응의 순서로 정해지고 `plan_outbound`는 (칸×팔) 표 대신 "이 이동의 binding 후보 집합"으로 배정한다. `arm_reach` 표는 이 대응이 검증·설치되는 순간 폐지한다.
- 운영 배포의 도달 원본은 **검증된 설치 대응**이다. v1.299 preflight는 시뮬레이션 전용이므로 운영 원본이 될 수 없고, 설치 대응 검증(설계24의 검토 단계)을 보조하는 조회로만 쓴다. preflight를 소비하려면 `catalog_digest` 발급 절차와 함께 별도 변경으로 열고, 그때 기준 플랫폼 핀을 v1.299 이상으로 올려 하네스로 양 끝을 확인한다.

## 열린 질문 (구현 착수 전 설치 통합 담당 확정)

1. 다칸 `target_ref`의 설치 명명 규약 — 앵커 칸 주소인지 전체 점유 영역의 digest 참조인지.
2. `instance_id` → 암 A/B 대응(퇴피 근거 검증용)의 정본을 어디에 둘지 — binding 파일 안인지 설치 문서인지.
3. preflight `catalog_digest`의 발급·갱신 절차.

## 수용 시험 (구현의 완료 기준)

1. 다칸 입고 계획이 일치하는 `SpatialPlacementBinding`이 있을 때만 실행 게이트가 열리고, footprint·방향·주소 불일치는 거부한다.
2. 다칸 실행이 ARM 계약으로 제안·조회·물리 receipt를 그대로 통과하고, 완료 근거가 배치 주소로 대조된다.
3. (이동, 팔) 대응에서 주 팔·대체 배정이 정해지고, 후보가 없으면 `HOLD`다. legacy 1×1 경로 회귀는 그대로 통과한다.
4. `arm_reach` 표가 없는 구성에서 legacy 계획은 여전히 `ARM_REACH_UNVERIFIED`로 보류된다(표 폐지는 대응 설치와 같은 변경에서만).

## 하지 않는 것

- 임의 회전·하중·실물 안전성·현장 수용(ADR0008 범위 밖 유지).
- legacy ARM binding으로 다칸 검증 범위 넓히기(ADR0008 금지).
- preflight를 운영 도달 원본으로 승격하거나 비시뮬레이션 배포에서 호출.
