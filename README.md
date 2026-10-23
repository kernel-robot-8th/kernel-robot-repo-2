# HarborLogi

커널아카데미 로보틱스 심화 캠프 주말 야간반 1기 2팀 프로젝트.

회수 순위가 바뀌어도 덜 파내는 소형 컨테이너 야드. 이미 쌓인 컨테이너의 회수 순위가 운송 작업계획 변경으로 바뀌었을 때, 아래에 묻힌 목표를 꺼내며 생기는 추가 취급과 출고 준비 지연을 줄인다. 장비는 OMX-F 3대(S 공급·회수, A·B 야드 적층·반출)와 Pinky 3대(운송 풀), 작업물은 규격 컨테이너 모형이다.

| 구성 | 맡는 일 | 위치 |
|---|---|---|
| Harbor (이 저장소) | 컨테이너·슬롯·적층 관계, 회수 순위와 변경 지시, 방해 화물 처리, 업무 장부 | `harbor/` |
| ROSY Platform | 작업 접수·자원 배정, 관측, 실행 근거, 장치 실행·정지 권한 | [robotics-team-1213/rosy-platform](https://github.com/robotics-team-1213/rosy-platform) |

Harbor는 ROSY 코드를 가져오지 않고 Fleet HTTP API로 제안·조회만 한다. 이 저장소에 있던 ROSY 소스 스냅샷(`rosy/`, PR #1, 기준 커밋 `38f0c452e410fca6c2c8fc72f45c3c250751d58f`)은 ROSY와 앱을 분리하면서 제거했다. 그 제출본은 이 저장소의 Git 이력(커밋 `f126acd`)과 ROSY 원본 저장소에서 볼 수 있다.

## Harbor

항만의 업무 판단을 소유하는 독립 Python 앱이다. ROSY Platform은 실행·자원 admission·관측·장치 권한을 소유한다. 회수 정책을 설치된 공정 문서에 연결하고, Fleet에 제안한 뒤 관측 근거를 대조하는 업무 장부를 제공한다. 자동 admit·rearm·직접 장치 명령은 없다.

```bash
python -m unittest discover -s tests -v
python -m harbor
```

Python 3.10 이상, 런타임 외부 의존성 없음. 필요하면 `python -m pip install -e .`로 설치한다.

GitHub Actions는 push·PR·수동 실행 시 Ubuntu에서 Python 3.10·3.12로 위 unittest 전체를 실행한다. 플랫폼 checkout이나 운영 토큰·데이터를 요구하지 않는다. action은 커밋 SHA로 고정한다.

운영 장부·원본 데이터·토큰·개인 자료는 소스 저장소 밖에 둔다. 이 저장소는 공개 저장소다. 라이선스는 [Apache-2.0](LICENSE)이다. `.gitignore`는 흔한 로컬 상태와 설정 파일의 실수 추가를 줄이지만, 기존 추적 파일·Git 이력·임의 이름의 개인정보를 검사하지 않는다. 게시 전에 추가할 파일과 이력을 별도로 검토한다. 외부 장부 경로 예시는 [업무 흐름 사용법](docs/execution.md#장부-보관)을 따른다.

예제는 `target` 위의 `blocker`를 임시 위치로 옮기고, 목표를 공급 쪽으로 회수한 뒤 방해 화물을 원래 적층으로 돌리는 제안을 출력한다. `READY`는 관측 기준의 업무 제안이다. 실제 경로·팔 자세·공유 구역·장치 능력·승인·에너지 검증은 공정과 ROSY가 수행해야 한다. S 하역·방해 화물 정리·팔 퇴피의 실제 근거가 있어야 업무 완료를 판정할 수 있다.

| 모듈 | 책임 |
|---|---|
| `harbor/model.py` | 적층·관측 시각·물리 보유·계획 기준 버전의 불변 값 |
| `harbor/retrieval.py` | 방해 화물 처리·회수·복구 순서의 결정적 제안 |
| `harbor/site.py` | S 1대, A/B 2대, 운송 풀 3대, 충전 한 자리, 직렬 이동 제약 |
| `harbor/availability.py` | 수동 충전·대기·적재·정비·불명 장치의 후보 제외 |
| `harbor/process.py` | 각 화물 이동을 설치된 공정 문서 ID·digest·compiler artifact에 연결 |
| `harbor/rosy_http.py` | 기존 Fleet compile/proposal/job 조회 및 후보 운송 요청의 HTTP 소비 |
| `harbor/workflow.py` | 중복 요청 방지·불명 접수 HOLD·현재 시도/관측 대조의 SQLite 업무 장부 |

슬롯 목록은 아래층부터 위층 순서다. 적층의 하중·형상·충돌은 이 재고 모델의 범위에 포함되지 않는다. `transfers`는 업무 계획에서 허용한 방향이며 실제 검증·실행 capability를 생성하지 않는다. 오래된 관측·버전 불일치·보유 불명·자리 부족은 부분 실행안 없이 HOLD가 된다. 현재 보유 중인 화물이 하나라도 있으면 재관측과 대조가 먼저다.

[설치 예제](config/site.example.json)는 합성 ID와 개념 위치다. 특정 Pinky 번호에 입고·출고·충전 역할을 고정하지 않는다. 수동 `available`은 앱의 제외 조건 해제일 뿐 실제 가용성·자동 충전 확인이 아니다. 운영 상태는 호출 시 명시적으로 전달하고 누락 차량은 제외한다.

[플랫폼 계약과 다음 작업](docs/platform-contract.md), [검증 기록](docs/validation/2026-10-21.md)을 따른다. 기존 Vault는 독립 앱으로 유지한다.

실행 연결은 [구현 ADR](docs/adr/0001-process-binding-and-business-evidence.md)과 [업무 흐름 사용법](docs/execution.md)을 따른다. 동작 확인에는 설치자가 검증한 binding과 관측 출처, 지원 Fleet 버전이 필요하다. 기본 예제는 합성 재고 미리보기다.

추가 구현과 검토·시험 결과는 [실행 계약 검증 기록](docs/validation/2026-10-21-execution.md)에 기록한다.
