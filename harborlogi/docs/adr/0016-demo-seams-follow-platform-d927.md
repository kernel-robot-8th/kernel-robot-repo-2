# ADR 0016: 11/21 시연 경계는 플랫폼 D-927을 따른다

날짜: 2026-10-30
상태: 수락. 경계 참조 결정이다. 전체 흐름 회귀 구현·운영 수용은 후속이다.

## 배경

첫 흐름(반출 전 변경 지시 → 재취급 화물 처리 → 이송 → S 내려놓기 → 재취급 화물 정리·팔 퇴피)을 11/21까지 공개 계약만으로 시뮬에서 끝까지 돌린다. 경계와 시뮬·자동화 방식은 플랫폼이 소유하므로 rosy-platform ADR **D-927**(로컬 main 착지, 게시 전)에 정했다. 이 ADR은 Harbor 쪽에서 지킬 것만 적고 내용을 복제하지 않는다.

## 결정

1. 시연 기간 Harbor는 D-927 결정 1의 동결 계약(TRIP 조회 v1.298, ARM v1.312, 물리 근거 조회 v1.330)만 소비한다. 새 경로가 필요하면 플랫폼에 계약 변경으로 요청한다.
2. 물리 기하·팔·zone의 정본은 플랫폼 installation profile이다. Harbor binding은 그 ID와 digest만 참조한다([ADR 0005](0005-harbor-rosy-responsibility-map.md) 결정 4 유지).
3. `tools/integration/arm_fleet_harness.py`의 가짜 owner와 고정 scene 관측·승인 스레드는 Harbor 단위·계약 시험에만 쓴다. 시연과 매일 밤 회귀의 owner·관측·승인은 D-927 결정 3~5의 플랫폼 구성(MuJoCo OMX, Gazebo Pinky, 독립 시뮬 관측 제출기, 시험 operator 승인 스크립트)이다.
4. `tools/integration/real_fleet_http.py`에 한 Fleet으로 ①~⑤를 잇는 전체 흐름 모드를 추가하고 단계별 결과와 처음 실패한 단계를 남긴다(D-927 결정 2·7).
5. [ADR 0013](0013-independent-business-execution-and-platform.md)·[ADR 0015](0015-service-identity-and-independent-integration.md)의 책임 분리와 서비스 신원은 바꾸지 않는다. 승인은 Harbor가 하지 않는다.

## 검증

전체 흐름 모드가 양 끝 SHA 고정으로 ①~⑤를 통과하고, Harbor 정책 하나를 바꾼 실행에서 rosy-platform diff가 0이다. 시뮬 결과는 실물·현장 수용이 아니다.
