# 한 Fleet 전체 흐름 모드 첫 실행 (D-927 흐름 E)

2026-10-30. 플랫폼 D-927 결정 2·7과 [ADR 0016](../adr/0016-demo-seams-follow-platform-d927.md) 4항에 따라 `real_fleet_http --full-flow`를 추가하고 처음 실행한 기록이다. 진척 지표는 처음 실패한 단계 번호다.

## 무엇을 바꿨나

- `--full-flow`: simulation Fleet 하나가 TRIP(합성 Pinky)과 ARM(합성 UDS owner)을 함께 서빙하고, 단계를 차례로 실행해 처음 실패에서 멈춘다. `--report`로 단계별 JSON을 남긴다.
- `ArmFleet`을 `install()`(owner 시작과 ARM 설치 인자)과 `attach()`(승인·관측 스레드)로 나눴다. `RealFleet`은 `extra_app`으로 같은 프로세스에 ARM을 설치한다.
- 단계와 Harbor 호출의 대응은 업무 실행기(흐름 A)가 순서를 소유하기 전까지 잠정이다: 0 한 Fleet, ① 계획 고정, ② 재취급 이동(ARM), ③ 이송(TRIP), ④ 반출 이동(ARM), ⑤ 복귀 이동과 퇴피 근거.

## 실행

| 항목 | 값 |
|---|---|
| 호스트 | `remote_pytest --pick sim`이 고른 공식 시험 PC, `nice -n 15 ionice -c3` |
| Harbor | `a72e43b24d219c3048e8f3e65ebf13684e207f0f` (`feat/full-flow-one-fleet`, 미게시) |
| 플랫폼 | 로컬 main `8acadbb82e5ec771f262aabc26d67ab0f82f94c7` (D-927 포함, 미게시) |
| 운반 | [Linux 시험 PC 운반 절차](../solutions/2026-10-27-linux-testpc-shipping.md), 두 checkout `status --porcelain` 0줄 |

## 결과

| 실행 | 결과 |
|---|---|
| `--full-flow` | 0 한 Fleet 통과, ① 통과, **② 실패**: Fleet `GOAL_CONFIRMED`(합성 owner Submit 1·Get 1, 관측 `PRE_DISPATCH`·`POST_ACTION`) 뒤 업무 근거 어댑터 없음 |
| 기본(Cell) | 3실행 통과 |
| `--transport-only` | 1실행 통과 |
| `--arm-only` | 1실행 통과 |
| `--web-arm-only` | 1실행 통과 |
| 기본 unittest | 180실행 통과, skip 1 |

처음 실패한 단계는 ②다. Fleet 쪽 ARM 실행은 한 Fleet 안에서 끝났고, Harbor가 Fleet 물리 근거(v1.330)를 `PlacementEvidence`·`RetreatEvidence`로 바꾸는 어댑터(설계 31 P2)가 없어 업무 확인에서 멈췄다.

## 다음에 막힐 곳 (②를 넘긴 뒤)

- 합성 설치에는 source→target 대응이 하나뿐이고 합성 owner는 성공 때 물체를 target에 둔다. ④·⑤의 두 번째 ARM 이동은 이동별 대응(S4, D-927 결정 6)과 이동마다 장면을 맞추는 owner가 필요하다.
- ④ S 내려놓기의 Harbor 호출은 업무 실행기가 정한다. 지금은 반출 이동으로 대신한다.

## 이 기록이 세지 않는 것

owner·관측·승인은 하네스 합성이다. 플랫폼 시뮬 owner(MuJoCo), `tools/sim/transfer_observer.py`, 매일 밤 실행은 아직 연결하지 않았다. 물리 시뮬·실물·현장 수용의 근거가 아니다.
