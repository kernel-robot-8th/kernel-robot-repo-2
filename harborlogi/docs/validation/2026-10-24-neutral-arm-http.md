# 중립 ARM 작업과 물리 receipt의 독립 HTTP 소비

플랫폼 source commit `bb38fdcdd0140873e4e9d0133662982c1d6a30e0`, Harbor source commit `93e71eceb310b40c573029efb49b574a834f9eff`를 깨끗한 checkout으로 고정했다. 이후 문서 commit은 이 시험 소스와 구분한다. API reference는 v1.312다.

팀 시험 PC에서 실제 Fleet 서버를 loopback HTTP로 시작했다. Harbor child는 플랫폼 Python·DB·ROS를 import하거나 접근하지 않고 표준 라이브러리 HTTP adapter로 요청한다. 별도 stdlib 소비자도 같은 physical receipt를 읽었다. child의 `sys.modules`에 `fleet`, `core_common`, `omx_adapter`, `rosy`가 없음을 검사했다.

승인자와 관측자 자격은 Harbor child 설정에 전달하지 않았다. 승인자는 공개 admit API를 사용한다. 관측자는 공개 context를 조회하고 PRE_DISPATCH/POST_ACTION raw facts를 제출한다. Fleet이 독립 monitor로 기존 Action을 Get한다. 장치 대역은 실제 Unix 소켓과 SDK framing으로 연결한 synthetic owner다. ARM 모드에서는 `rosy_cell` import를 불가능하게 두고 공정 compiler·recipe/cell 문서 없이 실행했다.

| 명시 실행 | 결과 |
|---|---|
| `python -m tools.integration.real_fleet_http … --arm-only` | 1 test, exit0, skip0 |
| 같은 ARM launcher의 파일 직접 실행 | 1 test, exit0, skip0 |
| 기존 Cell compile/proposal/job·후보 권한·업무 관측 | 3 tests, exit0, skip0 |
| 기존 중립 TRIP 유실 응답·재시작 대조·도착 | 1 test, exit0, skip0 |

두 ARM 실행은 같은 시나리오의 서로 다른 진입점 검증이며 서로 다른 업무 시나리오 2개로 합산하지 않는다. 각각 UDS SubmitAction1회, GetAction1회, PRE/POST proof 각각1개였다. 작업의 물리 상태는 GOAL_CONFIRMED가 되었지만 Harbor 업무 원장은 WAITING을 유지했다. 별도 배치·퇴피 근거 없이 confirm을 호출하면 거절하며 자동 재제안도 거절했다. 다른 principal의 receipt 읽기는403이다.

초기 Harbor commit `97d1941e15baefe2ff6905d1617b7b791c69ee16`은 synthetic owner에 IPC `handle_connection`이 없어 실제 Unix 서버가 실패했다. native framing을 재사용한 commit `39ae8ff9275e50d3f2d7f67ef6e7c3e4170791b5`에서 ARM이 통과했다. 같은 소스의 module 진입은 import 경로 문제로 실패했으며 commit `2dbc2bbdaa4d118e5d6f2222e6ae8c819e19b7ee`에서 수정했다. 최종 source에서는 두 진입점과 공정 패키지 부재를 다시 확인했다.

하네스의 bounding box·영 속도·물체 자세는 합성 fixture다. 이 결과는 인증·HTTP/UDS framing·원장·점유 종료·소비자 분리의 근거이며 물리 시뮬레이터·센서 보정·native actuator·실물 안전 수용의 근거가 아니다. 플랫폼 FULL 회귀는 별도 플랫폼 기록이 소유한다. 운영 producer, 실패·취소 후 물리 정리와 명시적 복구·재승인, 현장 설치 검증은 후속이다.

CE: 해당 없음 — framing/import 수정과 권한 경계는 최종 코드·명시 통합 시험·ADR 0006에서 복원할 수 있다. 운영 장애나 장치 특성을 해결한 기록이 아니다.
