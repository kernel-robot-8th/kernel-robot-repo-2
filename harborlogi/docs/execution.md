# 공정 제안과 업무 장부 사용법

필요한 플랫폼 계약은 D-836, API reference v1.292다. 후보 운송은 simulation profile의 기존 named operator 접수다. 기존 단일 `robot_id` 호출은 플랫폼에서 계속 지원한다.

1. `plan_retrieval`로 관측 revision에 결속한 순수 회수 제안을 만든다.
2. 설치자가 각 `(cargo_id, source, destination)` 이동에 대해 검증한 `ProcessBinding`을 제공한다. recipe/cell ID와 digest, workcell/instance ID, 설치된 process artifact digest가 필요하다. 누락·중복 연결은 `bind_plan`이 거절한다. 이 binding은 물리 도달/충돌 검증을 대신하지 않는다.
3. `BusinessLedger(path, evidence_sources=(... ,))`와 `prepare(key, revision, bound_moves, target_id=..., supply_slot=...)`로 업무 의도를 기록한다. 같은 key·내용은 중복 생성하지 않고 다른 내용은 거절한다. 장부 경로는 운영 데이터 위치로 정한다.
4. `RosyClient(base_url, token)`을 주입해 `ledger.propose(key, client)`를 호출한다. 문서/공정 digest와 단일 transfer preview를 확인한 뒤 기존 `/cell-app/proposals`에 제안한다. 자동 승인하지 않는다. compiler artifact가 중간에 바뀌거나 응답이 불명확하면 HOLD다. POST를 자동 재전송하지 않는다.
5. 별도 운영자가 기존 Fleet 절차로 검토·승인한다. `ledger.refresh(key, client, now=...)`는 현재 cell job의 status/attempt를 읽는다. `ACTION_SUCCEEDED`만으로는 진행할 수 없다.
6. 설치된 독립 관측 producer가 제공한 `MoveEvidence`를 검증한 후 `confirm(key, evidence, now=...)`에 전달한다. mission/attempt·계획 revision·cargo/destination·팔 퇴피·출처·시각이 맞고 Fleet goal도 확인돼야 다음 제안을 만들 수 있다. 관측 ID는 한 번만 소비한다. refresh와 관측의 기본 유효 기간은 각각 5초다.
7. 마지막 방해 화물 복구 뒤에도 `AWAITING_UNLOAD`다. 목표 회수 단계의 mission/attempt와 결속된 하역·관련 팔 퇴피 근거로 `finish`해야 COMPLETED가 된다.

`MoveEvidence.revision`은 승인할 업무 계획의 기준 revision이다. 실제 관측 producer의 원본 revision·인증을 대체하는 ROSY wire schema가 아니다. 현재 인증된 생산자에서 이 값으로 변환하는 운영 어댑터는 없으며, 실물 적용 전 그 출처·원본 시각·identity 대응을 검증해야 한다.

확인한 기존 producer의 한계와 필수 필드별 책임, 운영 어댑터 구현·수용의 선행 조건은 [물리 관측 수용 조건](observation-acceptance.md)에 있다. 관절 상태·그리퍼 OPEN·Fleet goal 확인만으로 화물 도착·팔 퇴피·하역을 만들어내지 않는다.

재시작 후 기존 WAITING 단계는 새 `refresh` 전에는 확인할 수 없다. 미응답 SUBMITTING은 HOLD로 복구하며 늦은 응답이 와도 HOLD를 되돌리지 않는다. 원장의 같은 request key로 재대조한 뒤 운영자가 판단해야 한다. 자동 복구·물리 점유 해제·진행 중 조작 takeover는 없다.

장부의 각 변경은 증가하는 `row_version`에 결속된다. compile·조회 HTTP 응답을 받은 뒤에도 처음 읽은 버전·단계·상태가 같아야 적용한다. 다른 호출자가 새 단계·시도·완료로 진행했다면 늦은 응답을 거절하고 최신 상태를 다시 읽어야 한다. 기존 장부에는 이 버전을 기본값 0으로 추가하며 업무 상태를 보존한다.

후보 운송은 수동 제외를 적용한 후보를 `client.trip(candidates, steps, request_key=...)`로 전달한다. Fleet만 현재 lease/claim을 확보한다. 동일 key의 재전송은 기존 작업을 읽고, 새 의도에는 새 key가 필요하다. 운송 요청과 화물 인계 procedure를 자동 조합하는 commissioning은 아직 없다.

실제 HTTP는 HTTPS 또는 loopback HTTP를 사용한다. 토큰·실제 장치 주소는 외부 설정으로 공급한다. 인증 헤더를 다른 URL로 전달하지 않도록 redirect와 POST retry를 사용하지 않는다.

## 장부 보관

`BusinessLedger`에는 설치자가 지정한 저장소 밖 절대 경로를 전달한다. 장부는 업무 의도·관측 판정을 담는 앱 상태이며 Fleet의 실행 원장과 별도로 보관한다. 다음 예시는 플랫폼 저장소를 복제하지 않은 환경에서도 사용할 수 있다. 이 작업 공간의 실제 배치에는 `Rosy/rosy-data/app-state/port/`의 절대 경로를 지정한다.

```python
from pathlib import Path
from harbor.workflow import BusinessLedger

ledger_path = Path.home() / "rosy-data" / "app-state" / "harbor" / "business.sqlite3"
ledger_path.parent.mkdir(parents=True, exist_ok=True)
# 실제 설치에서 검증한 관측 출처 ID로 교체한다.
ledger = BusinessLedger(ledger_path, evidence_sources=("synthetic-observer",))
```

경로와 출처는 설치 예시이며 실제 서버·로봇에 연결하거나 실행을 승인하지 않는다. 플랫폼의 `ROSY_DATA_ROOT`는 이 독립 앱의 장부 경로를 자동 선택하지 않는다. 상대 경로를 전달하면 현재 작업 디렉터리가 기준이므로 실행 위치가 달라질 때 다른 장부를 여는 실수를 피해야 한다.

가동 중인 장부는 DB 파일만 복사하지 말고 SQLite online backup API 등 일관된 snapshot 방식으로 보관한다. 읽기 전용 사본의 무결성을 확인하고, 앱을 재시작하기 전 정본 경로와 미확정 요청/HOLD를 대조한다. 장부 복원이나 파일 이동을 로봇 점유 해제·자동 재전송 근거로 삼지 않는다.

## 실제 Fleet 계약 시험

`python -m unittest discover -s tests -v`의 통합 skip은 실제 서버 미검증을 뜻한다. 시험 PC에 깨끗한 고정 플랫폼 소스를 준비하고, 해당 플랫폼의 기존 시험 의존성을 설치한 별도 환경에서 다음 개발 도구를 실행한다.

```text
python -m tools.integration.real_fleet_http --platform-root <플랫폼-root> --platform-sha <전체-40자리-SHA>
```

도구는 Harbor의 시뮬레이션 하네스(`tools/integration/fleet_harness.py`, [ADR-0003](adr/0003-own-fleet-contract-harness.md))로 고정 플랫폼 Fleet을 loopback에 띄우고 임시 장부·합성 operator/viewer 자격으로 `tests/integration`을 별도 프로세스에서 실행한 뒤 서버를 종료한다. 서버 환경만 플랫폼 시험 코드를 import한다. Harbor 런타임 wheel에는 `tools`가 포함되지 않는다. 일반 운영 Fleet·실물 장치에 대한 도구가 아니다.

독립 실행에서는 `HARBOR_INTEGRATION=1`과 도구가 만든 임시 `HARBOR_INTEGRATION_CONFIG`를 함께 공급한다. 명시 모드에서 설정 누락·연결 오류는 실패이며 skip을 성공 증거로 삼지 않는다. 플랫폼·Harbor 전체 SHA 두 개와 exit code를 함께 기록한다. fixture의 합성 관측은 업무 완료의 운영 증거를 제공하지 않는다.

업무 대조 시험에서는 별도 시험 actor가 Fleet의 기명 승인자로 승인한다. Harbor의 런타임 클라이언트에 승인 기능을 추가하지 않는다. 실제 `GOAL_CONFIRMED` 뒤에도 Harbor 장부가 WAITING에 남는지, 잘못된 관측을 거절하는지, 재시작 후 새 조회가 필요한지, 인계 뒤에도 별도 하역 관측 전에는 AWAITING_UNLOAD인지 확인한다. 관측 값은 시험에 명시한 합성 데이터이며 인증된 현장 관측을 읽는 어댑터가 아니다. [실행 결과와 제한](validation/2026-10-22-business-reconciliation.md)을 따른다.
