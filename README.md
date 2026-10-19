# ROSY Platform

커널아카데미 로보틱스 심화 캠프 주말 야간반 1기 2팀 프로젝트.

ROSY는 관측한 세계 상태, 현장 미션 조정, 로봇의 안전한 실행, 오프라인 학습·평가를 연결하는 로봇 플랫폼입니다. 첫 하드웨어는 Raspberry Pi 5 기반 Pinky Pro이며 ROS 2 Jazzy를 사용합니다.

## 소스와 실행

- [ROSY 소스 및 전체 안내](rosy/README.md)
- [설치·빌드·실행 안내](docs/RUNNING.md)
- [구성요소별 검증 상태](rosy/STATUS.md)
- [제출본 검증 기록](docs/VERIFICATION.md)
- [제출 기준과 갱신 방법](docs/SUBMISSION.md)

소스는 `rosy/`를 프로젝트 루트로 사용합니다. 빌드와 실행 명령은 해당 디렉터리에서 수행합니다.

## 주요 구성

| 경로 | 역할 |
|---|---|
| `rosy/middleware/` | CORE API, 장치 연결, 인지와 주행 |
| `rosy/operations/` | Fleet 미션 조정, 관제, World와 Decision |
| `rosy/learning/` | 데이터 관리, 학습과 독립 평가 |
| `rosy/contracts/` | 공통 계약과 인터페이스 |
| `rosy/integrations/` | 로봇과 시뮬레이션 연동 |
| `rosy/deploy/` | 로봇·현장 PC 배포 |

## 출처와 라이선스

[ROSY Platform](https://github.com/robotics-team-1213/rosy-platform)의 공개 커밋 `38f0c452e410fca6c2c8fc72f45c3c250751d58f`를 가져왔습니다. 원본의 [Apache-2.0 라이선스](rosy/LICENSE)와 개별 파일의 저작권·라이선스 고지를 유지합니다. ROSY의 Pinky Pro 파생 이력은 [원본 안내](rosy/README.md#라이선스)에 있습니다.
