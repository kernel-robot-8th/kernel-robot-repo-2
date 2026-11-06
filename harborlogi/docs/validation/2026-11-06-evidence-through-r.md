# 2026-11-06 R을 거친 물리 근거·재확인과 기준 플랫폼 갱신

목적: Harbor의 업무 완료 판정에 쓰는 물리 근거·재확인 호출이 통합관제 R을 거쳐 동작하는지 확인하고, 기준 플랫폼을 그 변경이 든 공개 main으로 옮긴다.

| 항목 | 값 |
|---|---|
| 호스트 | 모델 PC, 야간 시험과 같은 venv |
| Harbor | `10e84946acebe0faafc3485b27fcf2f27131cfa9` |
| 플랫폼 | `647223ec39b8d68b5ad724faf7f070e30aa0f283` (공개 main, API reference v1.361, 중계는 v1.356) |

## 찾은 빈틈

기존 `--arm-via-r`은 작업 제안·조회·receipt만 지나갔다. 그래서 R이 근거를 중계하지 않아도 통과했다. `tests/integration/arm_http.py`에 `physical_evidence`, `request_recheck`, `recheck_read` 호출을 더했다.

재확인 증명은 새 실행 시도가 아니므로, 하네스의 사후 검사는 `RECHECK`를 빼고 비교한다. 시도 1회는 제출 횟수(`submits != 1`) 검사가 그대로 지킨다.

## 결과

| 실행 | 플랫폼 | 결과 |
|---|---|---|
| `--arm-via-r` 1회 (시험 추가 후, 이전 기준) | `ed455ea` (v1.355) | 실패. HTTP 404. 시험이 빈틈을 잡는 것을 확인했다 |
| `--arm-via-r` 5회 | `647223e` | 5/5 통과. 증명 `PRE_DISPATCH`·`POST_ACTION`·`RECHECK` |
| `--arm-only` 1회 | `647223e` | 통과 |
| `--full-flow` 1회 | `647223e` | 통과. 제출 3회 |

## 이 결과가 말하지 않는 것

- 합성 owner와 시뮬레이션 Fleet의 결과다. 실물 팔과 현장 수용이 아니다.
- 재확인은 요청·조회가 R을 거쳐 관측자까지 가는 것을 확인했다. 재확인 `CONFIRMED` 근거로 업무를 끝내는 흐름은 한 Fleet `--full-flow`에서만 확인했고, R을 거친 구성에서는 확인하지 않았다.
- 운영 R은 아직 `480de9e`다. 다른 작업의 계정 DB 전환 때문에 자동 갱신이 보류돼 있어, 현장 Harbor의 ARM 경로는 닫혀 있다. CELL-A도 `2ba97ac`로 고정돼 있어 갱신이 따로 필요하다.
