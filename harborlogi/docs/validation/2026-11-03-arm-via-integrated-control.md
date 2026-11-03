# 통합관제 R을 거친 ARM 시험 (`--arm-via-r`)

2026-11-03. ADR0018 공백 닫힘 조건 3(Harbor 통합 시험이 R 경로로 돈다)의 첫 실행 기록이다. 실물·운영 수용 기록은 아니다.

## 검증한 소스

- Harbor: `935a9be919b6ff0f1f0e2ccfb1085b7235bfe9f3` 이후 `--arm-via-r` 수정 포함 브랜치 `feat/arm-via-r-harness`.
- ROSY Platform: `e95c2658b29cb73f6fb00f8b7a263491444e9514`(API v1.356, 로컬 main 착지 전 묶음 브랜치). 공개 `origin/main`에는 아직 없다.
- 시험 호스트: AI PC. 두 저장소를 git bundle로 옮겨 clean checkout으로 실행했다. 하네스의 전체 SHA·clean 검사를 통과했다.

## 구성

- R: ARM 설치 없는 Fleet. 관제 단위 설정과 R의 relay만 있다.
- 셀: 기존 `ArmFleet`(합성 owner, 독립 observer, 시험 운영자). 제안은 R의 service principal(`control-unit:R`)로 생긴다.
- Harbor: R의 origin·토큰만 받는다. 시험은 기존 `tests/integration/arm_http.py` 그대로다.

## 결과

| 실행 | 결과 |
|---|---|
| `--arm-via-r` 8회 | 7회 통과(owner submit 1, 관측 PRE_DISPATCH·POST_ACTION, 업무 장부 WAITING 유지, receipt `AT_TARGET_AND_CLEAR`, 다른 principal 403). 1회 HOLD |
| `--arm-only` 3회(기준) | 3회 통과 |

통과한 실행에서 Harbor 업무 장부는 R이 보여 준 `proposal_principal_id`(Harbor 자신)로 결속했고, 셀 운영자는 셀에서만 승인했다.

## 실행 중 고친 것

- 하네스가 `fleet.tokens['approver']`를 모든 모드에서 읽었다. R에는 승인자가 없으므로 선택 값으로 바꿨다.
- 셀 시험 운영자가 `control-unit:R` 자격으로 ARM 경로를 조회했다. 관제 단위 자격은 `/api/fleet/control-unit`에만 닿으므로(`CONTROL_UNIT_CREDENTIAL_SCOPE_REQUIRED`) 셀 운영자가 위임 목록에서 결속된 작업을 찾도록 바꿨다. 플랫폼 경계는 그대로다.
- `TMPDIR`를 긴 run 경로로 두면 합성 owner의 UNIX socket 경로가 길이 한도를 넘는다. 기본 `/tmp`로 실행했다.

## 미해결

- 1회 HOLD: owner submit 0, 관측 PRE_DISPATCH만 있는 상태에서 셀 작업이 HOLD가 됐다. 같은 조건의 수동 재현 2회와 시험 7회에서는 다시 나오지 않았다. 원인은 확인하지 못했다. 다음 확인은 실패 시 셀 작업의 `reason`을 기록하도록 하네스 출력에 더하는 것이다.
- 플랫폼 v1.356이 공개 `origin/main`에 게시된 뒤 같은 시험을 공개 SHA로 다시 돌린다.
- 현장 R↔CELL-A TLS 연결은 플랫폼 D-960 수용 조건이며 이 기록 범위 밖이다.
