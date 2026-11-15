# 경로·필드가 같아도 ROSY의 새 거절 조건이 실제 Fleet 시험을 멈춘다

2026-11-14. Harbor main과 현재 ROSY의 연동을 점검하다 찾았다. 코드를 읽은 검토자 셋은 모두 "v1.361 이후 Harbor 경로에 호환 깨짐 없음"이라고 결론 냈고, 실제 Fleet 시험을 돌린 쪽만 실패를 찾았다.

## 증상

- 모델 PC 야간 작업 `rosy-harbor-nightly`가 2026-11-07부터 매일 ③ 이송 단계에서 실패했다. 11-02~11-06은 통과였다. 7일 동안 아무도 보지 않았다.
- `python -m tools.integration.real_fleet_http --transport-only`가 기준 `647223ec`(v1.361)에서는 통과하고 그 뒤 ROSY에서는 이송이 `HOLD`로 끝났다. 이유는 `TRIP_BATTERY_UNCONFIRMED`(`detail.reason: unreadable`)였다.
- 배터리만 고치면 같은 시험이 이번에는 `reason: map_frame`으로 멈췄다. 첫 원인이 둘째 원인을 가리고 있었다.

## 원인

ROSY가 이송에 근거를 더 요구하도록 두 번 바뀌었고, 둘 다 경로·요청·응답 필드는 그대로였다.

1. v1.365(`72615a94b9`, 2026-11-07): 이송 시작 때 로봇의 fresh 배터리 근거가 없으면 거절한다.
2. v1.383(`7b89fdcf6f`, D-1010, 2026-11-10): 자세 포트가 자세와 목표의 좌표계를 밝히지 않으면 자유 구간 목표를 보내지 않는다.

ROSY 변경 이력은 둘을 "거절 코드 추가"로만 적었고 Breaking 표시가 없다. Harbor 하네스(`tools/integration/fleet_harness.py`)의 가짜 로봇은 배터리 근거도, 좌표계 선언도 주지 않았다. ROSY는 같은 커밋에서 자기 시험 fake와 Gazebo 시뮬 로봇을 고쳤지만 Harbor 하네스는 다른 저장소라 따라오지 않았다.

Harbor 제품 코드와 ROSY 게이트는 틀리지 않았다. 낡은 것은 시험 fixture다.

## 해결

하네스 로봇이 ROSY 자체 fake가 쓰는 값을 그대로 쓴다. `fakes.fresh_power_health()`가 있으면 배터리 근거로 넣고(옛 기준에는 없으므로 `hasattr`로 확인), `Drive`에 `poses_in_robot_frame = True`를 둔다.

## 검증

이 노트북에서 `real_fleet_http` 기본(Cell)과 `--transport-only`를 세 기준에 돌려 모두 통과했다: `647223ec`(v1.361), 공개 main `8dcdd033f8`(v1.375), 로컬 main `9cfa94bea5`(v1.394). 수정 전에는 뒤의 둘에서 이송이 실패했다.

확인하지 못한 것: `--arm-only`·`--full-flow`는 Linux 전용(SO_PEERCRED)이라 여기서 돌리지 못했다. 병합 뒤 첫 야간 실행이 ③을 통과하는지 봐야 한다. 실물 Pinky가 현장에서 5초 안의 fresh 배터리 근거와 맞는 지도 ID를 올리는지도 확인하지 않았다.

## 다음에 적용할 조건

- ROSY 기준 커밋을 올리거나 "호환된다"고 판단할 때 경로·필드 비교로 끝내지 않는다. `real_fleet_http`를 새 기준에 실제로 돌린다.
- 야간 작업이 실패하면 첫 `hold`의 `reason`을 먼저 읽는다. 하나를 고친 뒤 다음 이유가 나올 수 있으니 통과할 때까지 다시 돌린다.
- 야간 실패는 지금 아무에게도 알리지 않는다. 결과를 보는 사람이나 알림을 정하기 전에는 주기적으로 `systemctl --user status rosy-harbor-nightly`를 확인한다.
- Harbor 제품 코드는 Fleet 이송의 `HOLD` 이유를 화면에 옮기지 않는다(`harbor_web/intake_loop.py`). 입고·이송 상태를 보여 주는 화면도 아직 없다. 현장에서 같은 보류가 나면 화물이 `EXPECTED`에 이유 없이 계속 남는다(만료는 `TRIP_ARRIVED`·`STORING`만 본다). 어디에 보일지는 팀 결정이 필요한 별도 작업이다.
