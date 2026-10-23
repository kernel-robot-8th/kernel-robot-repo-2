# HarborLogi

커널아카데미 로보틱스 심화 캠프 주말 야간반 1기 2팀 프로젝트.

회수 순위가 바뀌어도 덜 파내는 소형 컨테이너 야드. 이미 쌓인 컨테이너의 회수 순위가 운송 작업계획 변경으로 바뀌었을 때, 아래에 묻힌 목표를 꺼내며 생기는 추가 취급과 출고 준비 지연을 줄인다. 장비는 OMX-F 3대(S 공급·회수, A·B 야드 적층·반출)와 Pinky 3대(운송 풀), 작업물은 규격 컨테이너 모형이다.

| 구성 | 맡는 일 | 위치 |
|---|---|---|
| Harbor 앱 | 컨테이너·슬롯·적층 관계, 회수 순위와 변경 지시, 방해 화물 처리, 업무 장부 | [`harborlogi/`](harborlogi/README.md) |
| ROSY Platform | 작업 접수·자원 배정, 관측, 실행 근거, 장치 실행·정지 권한 | [robotics-team-1213/rosy-platform](https://github.com/robotics-team-1213/rosy-platform) |

Harbor는 ROSY 코드를 가져오지 않고 Fleet HTTP API로 제안·조회만 하는 별도 애플리케이션이다. 설치·실행·시험은 [`harborlogi/README.md`](harborlogi/README.md)를 따른다. CI는 push·PR마다 `harborlogi/`에서 Python 3.10·3.12 unittest를 실행한다.

이 저장소에 있던 ROSY 소스 스냅샷(`rosy/`, PR #1, 기준 커밋 `38f0c452e410fca6c2c8fc72f45c3c250751d58f`)은 ROSY와 앱을 분리하면서 제거했다. 그 제출본은 이 저장소의 Git 이력(커밋 `f126acd`)과 ROSY 원본 저장소에서 볼 수 있다.

라이선스: [Apache-2.0](LICENSE).
