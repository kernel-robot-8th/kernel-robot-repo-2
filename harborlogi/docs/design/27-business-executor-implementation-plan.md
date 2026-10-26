# 업무 실행기 착수 계획 — 설계24의 단계별 구현

2026-10-26. 결정: [ADR0012](../adr/0012-user-confirmed-command-dispatch.md)·[ADR0013](../adr/0013-independent-business-execution-and-platform.md). 상세설계: [설계24](24-business-execution-boundary.md), 연결: [설계25](25-fleet-connection-bridge.md). 상태: **착수 계획(제안)**. 이 문서는 새 결정을 만들지 않고 설계24를 착수 가능한 단계로 나눈다. 단계 순서와 세부 범위는 구현 중 검증 결과로 조정할 수 있다.

## 현재 구현 지점 (2026-10-26 main 기준)

| 설계24 요소 | 현재 상태 | 위치 |
|---|---|---|
| 사용자 확정 → 즉시 제출 | 구현됨(ADR0012). 재요청은 기존 Command를 열고 UNKNOWN 재전송 없음 | `harbor_web/commands.py` |
| ARM 중립 제안·조회·취소·물리 receipt | 구현됨(ADR0006, API v1.312) | `harbor/rosy_http.py` |
| 중립 trip 조회(request_key 대조) | 구현됨(API v1.298) | `harbor/rosy_http.py` |
| 단계 장부·근거 대조(`refresh`/`confirm`/`finish`) | 구현됨. 그러나 웹가 이 호출을 잇지 않음 | `harbor/workflow.py` |
| 업무 근거 출처 | **미설정 placeholder** | `harbor_web/commands.py` (`web-unconfigured-placement`/`web-unconfigured-retreat`) |
| 상시 업무 실행기·내구 의도 복구 | 없음 | — |
| 다칸 배치 실행 binding | 미수용(ADR0008은 점유 모델만) | — |
| ARM 정책 admission(별도 승인 없는 진행) | ROSY 계약 결손, 앱이 활성화하지 않음 | ADR0012 완료조건 참조 |
| 도달 검증 | 설치자 표(`arm_reach`) 잔존. 플랫폼 사전검사 전환 미완료 | `harbor/model.py`, `harbor/outbound.py` |

## 단계 구성

각 단계는 독립 PR로 착지한다. 한 단계의 시험이 통과하기 전에 다음 단계를 열지 않는다. 모든 단계에서 기존 규칙을 유지한다: ROSY 내부 import 금지, 자동 승인 금지, 원래 request key 대조, timeout·ACK로 완료·정지·점유 해제를 확정하지 않음([ADR0013](../adr/0013-independent-business-execution-and-platform.md) 결합 제한 규칙 1–8).

### P0 — 착수 기준 정리 (문서·환경)

| 항목 | 내용 |
|---|---|
| 할 일 | ① 작업 브랜치를 최신 `upstream/main`에서 만들고 미병합 로컬 커밋(`a60ac79`, ADR0004 정렬)의 내용을 main 최신본과 대조해 반영 여부를 결정한다. ② 기준 플랫폼 핀(ARM v1.312, trip v1.298, 기준 커밋)과 [검증 기록](../validation/)의 양 끝 SHA가 아직 유효한지 확인하고 하네스(`tools/integration/fleet_harness.py`)를 같은 기준으로 다시 돌려 둔다. ③ 본 문서의 단계별 수용기준을 팀 검토로 확정한다. |
| 시험 | 변동 없음. 문서·환경 확인만. |
| 하지 않는 것 | 코드 변경, 새 API 소비. |

### P1 — 업무 실행기 최소 루프 (합성 근거)

| 항목 | 내용 |
|---|---|
| 할 일 | 확정 transaction이 실행 의도(계획 ID/digest·원본·확정 범위)를 지속 저장하고, 실행기가 의존 단계 완료·revision·관측·권한·설치 대응을 확인해 다음 단계를 제출·대조한다. 초기 구현은 기존 Harbor 서비스 lifecycle 안의 단일 실행기(제한된 polling, 설정된 조회 간격·동시 업무 수·backoff)로, 별도 프로세스·queue·DB를 만들지 않는다(설계24 배치 규칙). 웹 GET·새로고침은 읽기만 하게 한다. `BusinessLedger.confirm/finish`의 호출 주체가 실행기가 된다(근거는 아직 합성). |
| 코드 위치 | `harbor_web/`에 실행기 서비스 추가, `harbor/workflow.py` 재사용, WriterLease·restore 차단 유지. |
| 시험 | 화면 종료 뒤 3단계 합성 업무 진행, 동일 이벤트/조회 반복의 단일 전이, 전송 전후 crash와 재시작 복구, timeout 뒤 원래 key 대조(새 key 재전송 없음), 부분 완료 후 다음 단계. |
| 수용 기준 | 설계24 「검증과 현재 결손」의 실행기 항목. Fleet 조회만으로 다음 단계가 열리지 않음을 시험으로 고정. |
| 하지 않는 것 | 근거 출처 실연결(P2), 재계획 구조 변경(P3), 다칸·정책 admission(P4). |

### P2 — 업무 근거 어댑터 (인증된 출처 연결)

| 항목 | 내용 |
|---|---|
| 할 일 | `harbor_web/commands.py`의 placeholder 출처를 설치 설정의 실제 placement/retreat 출처로 교체하고, 인증된 producer 자료를 현재 업무·시도에 결속하는 어댑터를 구현한다. 원본 ID·시각·revision·digest를 보존하고([물리 관측 수용 조건](../observation-acceptance.md)), ARM 단계는 물리 receipt의 원본 참조·정책·authority epoch·generation 대조(ADR0006: receipt digest는 전체 grant 해시)로 실행 확인을 얻는다. |
| 시험 | 위조 출처·누락 필드·미래/오래된 시각·재사용 ID·이전 attempt/revision·다른 화물/목적지·zone claim 미해제·구역 안 자세·출처 종류 바꿔치기가 업무 진행·COMPLETED를 만들지 못함. 정상 예는 실제 인증 경로 통과. |
| 수용 기준 | `confirm`/`finish`가 실제 출처 근거 없이는 단계를 닫지 않음. 근거 어댑터 시험은 합성 producer로 앱 단독 계층, 고정 양 끝 HTTP 시험은 하네스로. |
| 하지 않는 것 | 운영 producer 자격 발급(설치 통합 담당), 실물 수용(별도 기록). |

### P3 — 재계획·취소·불명 완성

| 항목 | 내용 |
|---|---|
| 할 일 | 미제출 단계 보류·영향 비교·새 계획 revision 구조를 확정한다. 현재 Command가 Order당 하나이고 binding이 불변이므로(설계24 지적), 재계획은 원래 Command를 덮어쓰지 않는 새 실행 의도 구조로 설계한다. 취소 요청 접수(ACK)와 실제 정지·점유 해제를 구분해 후속 단계를 차단하고, 결과 불명을 독립 상태로 둔다. |
| 시험 | 부분 완료 후 재계획, 취소 ACK 뒤 후속 단계 차단, 사용자 확정 범위 밖 목표 변경의 재확대 요구, 불명 상태에서 시간 경과만으로 상태 확정 금지. |
| 수용 기준 | 재계획이 이미 제출된 작업의 원래 식별자를 바꾸지 않음. 상태 enum·migration은 구현 때 확정하고 기존 상태를 재해석해 덮어쓰지 않음. |
| 하지 않는 것 | 병렬 업무·공동 계획 활성화(설계24: 별도 검증 전 비활성). |

### P4 — 확장 게이트 (짝 계약이 열린 뒤)

| 항목 | 내용 |
|---|---|
| 다칸 binding | ADR0008 점유 모델의 실행 binding 수용. P1–P3 실행기가 단일 개체 이동에서 검증된 뒤. |
| ARM 정책 admission | ROSY D-865 확장(팔 단계 포함 범위)이 ADR0012 완료조건을 충족한 뒤 소비. 앱이 정책을 활성화·우회하지 않음. |
| 도달 사전검사 전환 | 설치자 `arm_reach` 표(ADR0005 결정 4의 임시 수단)를 플랫폼 사전검사·ARM capability 결과로 교체. revision이 바뀌면 캐시 폐기. |
| 시험 | 각 항목마다 고정 양 끝 HTTP 시험과 앱 단독 시험을 함께 갱신. 실물·현장 수용은 별도 기록. |

## 검증 계층 (모든 단계 공통)

1. **앱 단독**: `python -m unittest discover -s tests -v` — 합성 근거·어댑터 대역 시험.
2. **고정 양 끝 HTTP**: `tools/integration/fleet_harness.py`로 실제 Fleet 코드를 simulation profile로 띄워 계약·원본 결속·idempotency·승인 대기 검증. 결과·양 끝 SHA를 `docs/validation/<날짜>-<주제>.md`에 기록.
3. **실물·현장**: [물리 관측 수용 조건](../observation-acceptance.md)과 ADR0012 완료조건이 열리기 전까지 열지 않는다. 1·2층 통과를 수용으로 표시하지 않는다.

핵심 독립성 시험(매 단계 회귀): Harbor의 재취급 순서 변경이 ROSY 변경을 요구하지 않음, ROSY 공개 계약 유지 시 Harbor 업무 규칙 불변(설계24).

## P0 실행 기록 (2026-10-26)

| 항목 | 결과 |
|---|---|
| P0① 미병합 커밋 `a60ac79` | main 미포함 확인(플랫폼 `application-contract-support.md`가 아직 없는 0004 A11을 참조 중). `docs/adr0004-a11-admission` 브랜치에 cherry-pick(`a8ae82b`)해 PR로 반영 제안. 본체 브랜치는 보존. |
| P0② 하네스 재검증 | Windows 로컬(주입 가짜 장치)로 재실행: ① 셀/TRIP 기준 `584251615d…`(v1.292) 3 tests OK, ② 중립 TRIP `a8a7d6612…`(v1.298) `--transport-only` 1 test OK. 실행: 임시 깨끗한 고정 clone + `python -m tools.integration.real_fleet_http --platform-root <경로> --platform-sha <SHA>`. 중립 ARM `bb38fdcd…`(v1.312, `--arm-only`)과 web-arm은 실제 UDS가 필요해 **팀 시험 PC(Linux)에서 재실행 예정**. 기본 unittest 156 OK(skip 2, 고정 소스 없는 통합 시험). |
| P0③ 팀 검토 | 계획 문서(P27)와 ADR0004 A11 cherry-pick 두 PR로 요청. |

## P1·P2 착지 기록과 P3·P4 상태 (2026-10-27)

| 단계 | 상태 |
|---|---|
| P1 업무 실행기 최소 루프 | 구현 완료([PR #37](https://github.com/kernel-robot-8th/kernel-robot-repo-2/pull/37)): 상시 실행기, `resume_arm`(서류상 HOLD 재개, 원래 key GET 대조), `COMPLETED` 상태, 수용 시험 7건. 구현 중 발견: HOLD에서 WAITING으로 돌아오는 경로가 없어 일시 오류가 영구 멈춤이었음 — `resume_arm`으로 해소. |
| P2 업무 근거 어댑터 | 앱 쪽 구현 완료([PR #38](https://github.com/kernel-robot-8th/kernel-robot-repo-2/pull/38), #37 위 스택): 설치자 출처 설정(`HARBOR_PLACEMENT_SOURCES`·`HARBOR_RETREAT_SOURCES`), `EvidenceSubmission` 원본 보존 intake, 결속·신선·미소모 검사 provider. 운영 producer 전송·자격 계약은 여전히 설치 통합 담당(관측 수용 조건 산출물 1). |
| P3 재계획·취소·불명 | 불명·원래 key 대조·취소 ACK 차단은 P1에 포함. 나머지(주문당 명령 이력, CANCELLED 종착, 재계획 착수 조건)는 [설계28](28-replan-and-cancel-structures.md)([PR #39](https://github.com/kernel-robot-8th/kernel-robot-repo-2/pull/39))로 확정 후 구현. 열린 질문: ARM 취소 뒤 Fleet 원장의 최종 상태 표기. |
| P4 확장 게이트 | 전항목 대기: ARM 정책 admission은 ROSY D-865 확장(팔 단계 포함 범위)이 ADR0012 완료조건을 채운 뒤. 다칸 binding은 ADR0008 실행 binding 설계 필요. 중립 ARM(v1.312) 하네스 재검증은 팀 시험 PC(UDS) 예정. |
| P4 도달 사전검사 전환 (2026-10-27 계약 확인) | 단순 교체 불가. v1.299 preflight는 **시뮬레이션 전용**(비시뮬레이션 배포 503 `ARM_PREFLIGHT_CLOSED`)이고 요청이 물리 참조(`workcell_id`·`instance_id`·`catalog_digest`·`source`·`target`) 단위다. `arm_reach`표의 (칸×팔) 도달 대체에는 팔별 설치 참조·배정 모델 재설계가 필요하고, 운영 배포는 여전히 별도 검증된 도달 원본이 필요하다. 착수 전 전용 설계 노트가 먼저다. |

## ADR0013 대조 결론 (2026-10-26 검토)

ADR0013의 소유 표·결합 제한 규칙·검증 기준은 main 구현(ADR0006 ARM 연결, ADR0012 즉시 제출, `workflow.py` 장부)과 일치하고 보강이 필요하지 않다. 0013·설계24가 남긴 결손(상시 실행기, 인증된 근거 연결)은 이 계획의 P1·P2가 담당한다. 추가로 발견한 이행 항목(설치자 도달 표의 플랫폼 사전검사 전환, ADR0005 결정 4)은 P4로 추적하며 새 ADR을 만들지 않는다.
