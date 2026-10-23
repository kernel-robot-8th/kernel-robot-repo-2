# 함께 작업하기 — HarborLogi와 ROSY

HarborLogi는 두 저장소가 함께 돈다. 이 문서는 팀원이 무엇을 어디서 고치고, 둘을 어떻게 이어서 확인하는지 정한다.

| 저장소 | 무엇 | 바꾸는 방법 |
|---|---|---|
| 이 저장소 `harborlogi/` | Harbor 앱 — 회수 순위, 방해 화물 처리 순서, 업무 장부, 완료 판정 | 이 문서의 절차 (fork → PR) |
| [rosy-platform](https://github.com/robotics-team-1213/rosy-platform) | ROSY — 작업 접수·자원 배정, 관측, 로봇 실행·정지, 장치 드라이버 | 그 저장소의 `AGENTS.md`·ADR 절차 |

Harbor는 ROSY 코드를 import하지 않고 Fleet HTTP API만 쓴다. 그래서 두 저장소의 변경은 서로의 코드를 직접 고치지 않고, **공개 API 계약**으로만 만난다.

## 1. 어디서 고칠지 정하기

| 하려는 일 | 고칠 곳 |
|---|---|
| 회수 순서·방해 화물을 둘 위치·운송 후보 고르기 규칙 | 이 저장소 `harborlogi/harbor/` |
| 업무 장부, 완료 판정, HOLD 조건 | 이 저장소 `harborlogi/harbor/workflow.py` |
| Pinky 주행, OMX 팔 동작, 카메라·비전, 충전·도킹 | rosy-platform |
| Fleet 작업 접수·승인 화면, 자원 점유, 정지 | rosy-platform |
| Harbor가 쓰고 싶은데 ROSY에 없는 API·필드 | rosy-platform에 계약 변경을 먼저 제안 (아래 4절) |

판단이 안 서면 "이 규칙이 항만 업무에만 의미가 있나?"를 본다. 항만에만 의미가 있으면 Harbor, 다른 앱(예: 보관고)도 쓸 공통 실행·안전 규칙이면 ROSY다.

## 2. 준비

- Python 3.10 이상. Harbor만 다룰 때는 이것으로 충분하다.
- ROSY까지 띄울 때는 Ubuntu 24.04 + ROS 2 Jazzy와 rosy-platform의 [개발 가이드](https://github.com/robotics-team-1213/rosy-platform/blob/main/docs/reference/developer-guide.md)를 따른다.
- 작업은 팀 조직의 fork [robotics-team-1213/harborlogi](https://github.com/robotics-team-1213/harborlogi)에서 한다. 팀원 모두 이 fork에 쓰기 권한이 있으므로 개인 fork를 만들지 않는다. 이 fork를 `origin`, 원본 저장소 kernel-robot-8th/kernel-robot-repo-2를 `upstream`으로 둔다.

```bash
git clone https://github.com/robotics-team-1213/harborlogi.git
cd harborlogi
git remote add upstream https://github.com/kernel-robot-8th/kernel-robot-repo-2.git
```

## 3. Harbor를 고치는 흐름

1. 최신 `upstream/main`에서 브랜치를 만든다(`git fetch upstream` 후 `git switch -c <브랜치> upstream/main`). 이름은 `<type>/<주제>`이고 type은 `feat`·`fix`·`refactor`·`docs` 중 하나다. Jira 이슈가 있으면 `feat/kan-12-retrieval-hold`처럼 번호를 넣는다.
2. `harborlogi/` 안에서 고치고, 바꾼 동작에는 시험을 함께 넣는다.
3. `harborlogi/`에서 시험을 돌린다. 실패가 있으면 PR을 열지 않는다.

   ```bash
   cd harborlogi
   python -m unittest discover -s tests -v
   ```

4. 브랜치를 `origin`(팀 fork)에 push하고 원본 저장소 `main`으로 PR을 연다(`gh pr create -R kernel-robot-8th/kernel-robot-repo-2 --head robotics-team-1213:<브랜치>`). PR에는 무엇을·왜 바꿨는지와 돌린 시험 결과를 적는다. 같은 브랜치를 다른 팀원도 이어서 고칠 수 있다.
5. CI(Python 3.10·3.12 unittest)가 통과하고 팀원 한 명 이상이 확인한 뒤 병합한다. 바꾼 사람이 아닌 사람이 확인하는 것을 기본으로 한다.
6. 병합한 뒤 팀 fork의 `main`을 원본과 맞추고(`gh repo sync robotics-team-1213/harborlogi`) 작업 브랜치를 지운다. 팀 fork의 `main`에는 직접 커밋하지 않는다.

Jira 이슈가 있으면 키를 브랜치(`feat/kan-63-retrieval-rank`), 커밋 끝(`(KAN-63)`), PR 제목 맨 앞(`[KAN-63]`)에 넣는다. 팀 fork는 GitHub for Jira 앱에 연결해서, fork에 push한 브랜치·커밋이 Jira 이슈 화면에 보이게 한다. 원본 저장소는 앱에 연결돼 있지 않아 원본에 연 PR은 Jira에 보이지 않으므로, PR 링크를 Jira 이슈에 직접 남긴다. 커밋 메시지로 Jira 상태를 바꾸는 스마트 커밋은 쓰지 않는다(공개 저장소라 커밋 이메일을 Jira 계정에 맞추지 않는다).

GitHub Actions의 action은 커밋 SHA로 고정해서 쓴다. 이 과정 조직의 Actions 정책을 팀원은 볼 수 없어서, 버전 태그 대신 SHA를 쓰는 쪽이 안전하다.

## 4. ROSY에 없는 것이 필요할 때

Harbor에서 쓰고 싶은 API·필드·동작이 ROSY에 없으면:

1. Harbor 쪽에서 우회하지 않는다. ROSY 내부 import, 범용 실행 endpoint, 직접 장치 명령, 자동 승인은 쓰지 않는다.
2. 필요한 것을 적어 rosy-platform 담당에게 계약 변경으로 요청한다(그 저장소의 ADR 절차).
3. ROSY에 반영되면 [harborlogi/docs/platform-contract.md](harborlogi/docs/platform-contract.md)의 기준 플랫폼 커밋과 API 버전을 새 값으로 고치고, 5절의 Fleet 계약 시험을 다시 돌린다.

반대로 ROSY의 Fleet API를 바꾸는 사람은 Harbor가 쓰는 경로(`harborlogi/harbor/rosy_http.py`)가 영향을 받는지 확인하고 이 저장소에 알린다.

## 5. 둘을 이어서 확인하기

확인은 세 단계로 나눈다. 앞 단계가 통과해야 다음 단계로 간다.

| 단계 | 무엇을 확인하나 | 어떻게 | 누가 할 수 있나 |
|---|---|---|---|
| 1. Harbor 단독 | 회수 제안·장부·HOLD 규칙 | `cd harborlogi && python -m unittest discover -s tests -v`, `python -m harbor` | 누구나 |
| 2. Fleet 계약 시험 | 실제 Fleet HTTP와 업무 대조 (시뮬레이션 서버) | [실제 Fleet 계약 시험](harborlogi/docs/execution.md#실제-fleet-계약-시험)의 `tools.integration.real_fleet_http` | 팀 시험 PC (고정된 rosy-platform 소스와 그 시험 의존성 필요) |
| 3. 실물 | 실제 Pinky·OMX로 회수 흐름 | 아직 하지 않음. [물리 관측 수용 조건](harborlogi/docs/observation-acceptance.md)을 먼저 채운다 | 장치 담당과 함께 |

2단계를 돌리면 플랫폼·Harbor의 전체 커밋 번호와 결과를 `harborlogi/docs/validation/<날짜>-<주제>.md`에 남긴다. 1단계의 `skipped=1`은 2단계를 하지 않았다는 뜻이지 통과가 아니다.

실제 연결에서 Harbor는 작업을 **제안만** 한다. 승인은 운영자가 Fleet 화면에서 한다. Harbor가 로봇을 움직였다고 보지 않고, 관측 근거가 맞아야 다음 단계로 간다.

## 6. 연결 설정과 비밀값

- Fleet 주소는 HTTPS 또는 같은 PC의 loopback HTTP만 쓴다. 토큰은 Fleet 사용자 설정에서 받은 Bearer 토큰을 실행할 때 외부 설정으로 넣는다.
- 토큰·실제 IP·계정·장치 주소·운영 장부(SQLite)·원본 데이터는 이 저장소에 커밋하지 않는다. 이 저장소는 공개다.
- 장부는 저장소 밖 절대 경로에 둔다([장부 보관](harborlogi/docs/execution.md#장부-보관)).

## 7. 결정과 기록

| 무엇 | 어디에 |
|---|---|
| 팀 결정 (주제·범위·이름·저장소 구조) | 팀 Confluence 결정 로그와 ADR |
| Harbor 내부 설계 결정 | [harborlogi/docs/adr/](harborlogi/docs/adr/) |
| ROSY 계약·동작 결정 | rosy-platform `docs/adr/` |
| 할 일·담당·기한 | Jira KAN |
| 검증 결과 | [harborlogi/docs/validation/](harborlogi/docs/validation/) |

Confluence와 Jira는 팀 내부 도구라 이 공개 저장소에 주소를 적지 않는다. 날짜가 붙은 검증 기록은 당시 내용 그대로 두고, 바뀐 사실은 새 기록으로 남긴다.
