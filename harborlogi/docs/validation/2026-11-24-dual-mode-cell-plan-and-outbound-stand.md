# 2026-11-24 이중 모드 OMX 셀 계획·2단계 반출 Pinky 이송 실제 Fleet 계약 검증

목적: `rosy-platform#644`·`harborlogi#116`·`harborlogi#117`·`harborlogi#118`이 결합된 최종 상태에서, 실제 ROSY Fleet HTTP 시뮬레이션 서버를 상대로 이중 모드 셀 계획, 타입 계약 검증, 취소/사유 전파, 반입 사건(S1), 이송 제안(S2), 2단계 반출 Pinky 이송(`S_OUT -> S_EXIT`)이 종단 간 동작하는지 확인한다.

| 항목 | 값 |
|---|---|
| 호스트 | 팀 시험 PC (`ai`, venv `/home/ai/rosy-test/venvs/9b470ff1ae3990a6/bin/python`) |
| Harbor | `83d2e30a39102776bba29fffbeb1da7c784a48be` (`feat/dual-mode-cell-plan-dispatch` + `main` 병합본) |
| 플랫폼 | `3eb1afa2003f1561129d3233bd25f8033748ae57` (`rosy-platform#644`, `main` 병합 `12b39c94bfb5a6f36e5e5dbea2a3cfb08b5b6834`) |

## 검증 범위

- **읽기/타입 계약 (`harbor/fleet_types.py`, `harbor/cell_plan.py`, `harbor/rosy_http.py`)**: `session_typed`, `site_places_typed`, `cell_sites_typed`, `port_events_typed`, `preview_cell_plan_typed`, `propose_cell_plan_typed`, `cancel_cell_plan_typed`
- **이중 모드 셀 계획 (`test_z_dual_mode_cell_plan_harbor_and_rosy_over_real_http`)**:
  - 미등록 `request_key`에 대한 `404 UNKNOWN_CELL_PLAN` capability probe (규칙 8)
  - 모드 1 (`mode="harbor_plan"`, `order_source="harbor"`): 3단계 순서/좌표(`temp_a-r1-c1-l1 -> stand-r1-c1-l1 -> yard-r1-c1-l1`) 제안·`?request_key=` 복구·선행 적재 위반 시 `422 BLOCKED_SOURCE_CARGO` 차단·운영자 `admit` 후 `QUEUED`
  - 모드 2 (`mode="outbound"`, `order_source="rosy_r"`): 단순 목표 지시 -> R 자율 재취급/반출/복귀 3단계 산출·`?request_key=` 복구·운영자 `admit` 후 `QUEUED`
  - 원장 무변경 `/dry-run` 모의, `/cancel` 호출 시 타 proposer 계획 `403 CELL_PLAN_CANCEL_FORBIDDEN` 격리, 자사 계획 취소(`CANCELLED`, `hold_reason`) 및 중복 취소 `409 CELL_PLAN_NOT_CANCELLABLE`
- **2단계 반출 Pinky 이송 (`test_z_outbound_stand_two_leg_pickup_and_exit_over_real_http`)**: 미검증 상태 fail-closed 확인 -> `S_OUT`/`S_EXIT` free ring 커미셔닝 후 Leg 1(`S_OUT` 도킹)·Leg 2(`S_EXIT` 출구 이동) `GOAL_CONFIRMED`
- **S1/S2/규약 회귀**: `test_connection_rules_against_the_real_fleet`, `test_lost_response_recovery_and_arrival_without_process_documents`, `test_port_events_roundtrip_over_real_http`, `test_site_places_and_cell_sites_against_the_real_fleet`, `test_trip_proposal_d908_and_uncertain_recovery_over_real_http`

## 결과

| 실행 | 범위 | 결과 |
|---|---|---|
| `python -m tools.integration.real_fleet_http --transport-only` (`ai`) | 실제 ROSY Fleet HTTP 계약 E2E | `Ran 7 tests in 7.639s` — `OK` |
| `python -m unittest discover -s tests -v` (로컬) | Harbor 도메인/경계/계약 단위 시험 | `Ran 373 tests in 31.156s` — `OK (skipped=4)` (`RealFleetHttpTests` 미설정 제외) |
| `python -m django test harbor_web.tests --settings=harbor_web.settings` (로컬) | 웹 디스패치·UI·용어·마이그레이션 시험 | `Ran 592 tests in 99.683s` — `OK` |
| `makemigrations --check --dry-run` / `tools/design_tokens.py --check` | 스키마·CSS 토큰 정합성 | `No changes detected` / `Token CSS matches source` |

## 이 결과가 말하지 않는 것

- `tools.integration.fleet_harness.RealFleet` 시뮬레이션 서버(`transport_only=True`)와 하네스 테스트 토큰 기반 결과이며, 실물 Pinky·OMX 팔·카메라 장치 및 현장 운영 수용 시험이 아니다.
- `admit` 및 현장/콘솔 승인 단계는 하네스의 `operator`/`approver` 테스트 토큰으로 대행했으며, 운영 환경의 ROSY Account·R 서버 설정 검증(이슈 #112)을 대체하지 않는다.

## `main` ↔ `main` 재검증

| 항목 | 값 |
|---|---|
| Harbor `main` | `80dbb62aa96ef26c153a27fef489d1c957b4be43` |
| ROSY `main` | `27314fc34ef560d6f00e054ddbdea37045862b14` |
| 실행 (`ai`) | `python -m tools.integration.real_fleet_http --transport-only` |
| 결과 | `Ran 7 tests in 7.074s` — `OK` |
