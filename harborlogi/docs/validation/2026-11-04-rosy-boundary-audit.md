# 2026-11-04 Harbor–ROSY 역할 경계와 통신 점검

사용자 요청: "ROSY와 Harbor의 역할이 나뉘고, 그러면서 서로 역할에 따라 통신이 되는 유기적인 부분도 되어 있는지 체크".

대상:
- Harbor: `feat/port-next-steps` 작업 트리.
- ROSY Platform: 로컬 `main` `54e653c4cc`. 이 노트북의 checkout이며, 공개 main과의 차이는 확인하지 않았다.

방법: 코드 읽기와 로컬 시험이다. 실제 Fleet 서버나 시험 PC에는 연결하지 않았다.

## 결과

| 항목 | 판정 | 근거 |
|---|---|---|
| 역할 분리 | 정상 | Harbor 제품 코드는 ROSY 패키지를 import하지 않는다. ROSY의 operations·applications·contracts에 항만 업무어가 없다. 이번에 `tests/test_module_boundaries.py`의 `test_product_code_reaches_rosy_only_over_fleet_http`로 import 금지를 시험으로 고정했다(이전에는 관례) |
| 통신 경로 | 정상(경로·메서드 기준) | Harbor가 쓰는 Fleet 경로는 모두 ROSY main에 있다: `/api/fleet/session`, `cell-app/compile`·`proposals`, `cell-jobs/{id}`, `trip-jobs`(POST·GET·`?request_key`), `arm-transfers/jobs`(POST·GET·`?request_key`·`{id}`·`/physical-receipt`·`/physical-evidence`·`/rechecks`·`/cancel`). 제안 경로는 `operator`·`service`를 허용한다 |
| 요청·응답 필드 | 미확인 | pydantic 스키마와 필드 단위로 대조하지 않았다 |
| 기준 플랫폼 pin | 낡음 | `docs/platform-contract.md`의 기준은 v1.292 커밋이고, 본문은 v1.334까지 소비한다. 그 사이 ARM·cell-app·trip route 파일에 커밋이 있었다. 고정 HTTP로 다시 확인하기 전에는 기준을 바꾸지 않았다 |
| R 경유(ADR 0018) | 부분 | D-965 팔 위임(v1.353–1.355)과 R의 ARM 중계가 main에 있다. R 중계에 `physical-evidence`·`rechecks`가 없어(v1.359 착지 대기) R을 거치면 업무 근거·완료 재관측이 되지 않는다. `cell-app/compile`·`proposals`는 중계 대상이 아니고 `require_named_operator`라 service 토큰으로 되는지 확인하지 못했다 |
| 실행 가능한 시험 | 통과 | `python -m unittest discover -s tests` 통과(실제 Fleet HTTP 1건은 `HARBOR_INTEGRATION` 미설정으로 skip). Django 283개 통과 |

## Harbor가 우회하지 않는 것(ROSY 쪽 일)

- R 중계에 `physical-evidence`·`rechecks` 추가. 이것은 v1.359로 착지 대기 중이다. Harbor가 셀 관제에 직접 붙어 메우는 것은 ADR 0018이 금지한다.
- R로 cell-app compile·proposal을 보낼 때 응답을 확정해야 한다(위임 또는 명시적 거절).

## 확인하지 않은 것

- 고정 양 끝 SHA의 실제 Fleet HTTP 계약 시험(`tests/integration/test_real_fleet_http.py`). 깨끗한 플랫폼 checkout과 시험 PC가 필요하다.
- 운영 R(robttt)에 Harbor 서비스 principal로 실제 호출. 이 점검은 읽기만 했다.
