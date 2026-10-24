# Harbor 웹 제품 완성 계획

상태: **v0.2 독립 검토 승인·구현 진행**, 2026-10-25. 독립 architecture/critic이 round 2에서 승인했다. 이는 계획의 승인이고 구현·운영 검증 결과는 아니다. 이번 문서는 사용자 요청으로 설정한 활성 목표의 설계·구현·수용 기준이다. `fbbdc42`의 합성 계획/조회 앱을 출발점으로 한다. 사용자 확정은 기존 이미지/브랜드, PC 중심+태블릿 보조 브라우저 앱, 조치 필요 첫 화면, 팀 자체 계정·역할 권한이다. framework·데이터 설계는 아래 기술 판단안이며 사용자 발언으로 재구성하지 않는다.

## 1. 완성의 정의

Harbor는 화물과 업무 요청을 관리하고, 관측 재고와 변경된 반출 지시를 기준으로 재취급/반출/복귀를 계획하며, ROSY에 실행을 인계하고 완료 근거·이력·비교 결과를 대조하는 웹 제품이다. 전체 기능을 synthetic 정적 카드로 채우거나 버튼 클릭을 실제 완료로 바꾸지 않는다.

웹 완성은 실제 계정/권한, 영속 업무 데이터, 검토 가능한 계획, 지원된 공개 API의 제안/조회/중단 인계, 근거 대조·이력, 측정 데이터의 비교, 복구와 배포 구성을 구현하고 검증하는 것이다. 실제 카메라/로봇·설치 보정·외부 서비스 활성화와 현장 수용은 별도 게이트다. 미지원 외부 계약은 정확한 상태와 후속 소유자를 남긴다. 해당 계약이 없어 실행할 수 없는 기능을 '운영 가능'으로 표시하거나 전체 UR/SR 수용 완료라고 부르지 않는다.

현재 Confluence 요구사항의 상태·버전·전체 추적은 비공개 작업 기록에 보존한다. 공개 문서는 기존 제품 책임과 구현의 소유권/수용 기준을 기록하며 팀 원문 표·실제 운영값·계정/토큰을 복사하지 않는다.

## 2. 디자인 철학과 정보 구조

**변경된 반출 지시를 끝까지 정리하고, 다음 판단의 근거가 보이는 관제.**

읽는 순서는 대상/요청→현재 관측→계획→실행과 원본 근거→업무 완료다. 관측/예상/실행·완료를 같은 색/상태로 합치지 않는다. 실무에서 반복하는 입력·비교·인계·복귀를 우선하고 긴 내부 ID와 digest는 펼침/복사 가능한 보조 정보로 둔다.

전역 영역은 **운영 현황 / 화물·요청 / 재고·반출 / 작업 / 기록·검증**의 제품 구조를 기준으로 검토한다. 장비 전체 운영·안전 제어는 Fleet 소유이며 Harbor에는 영향 업무와 자원 참조·인계만 둔다. 설정/계정은 보조 영역이다. 권한에 따라 실제 가능한 기능만 제공하되 숨김이 서버 권한 검사를 대신하지 않는다.

- PC: 안정적인 전역 탐색, 필터가 있는 실제 데이터 표, 선택한 업무/화물의 상세와 근거. 실제 입력/자료량으로 밀도를 정한다.
- 업무 상세: 개요/계획/실행·근거/기록. 원래 업무·계획 revision·보기와 복귀 맥락 유지.
- 계획 검토의 핵심 표현: 같은 대상으로 정렬한 **관측–계획–실행/근거**. source/시각/revision·부족 조건을 같은 위치에서 대조한다.
- 태블릿: 배정 확인 목록→대상/회차 확인→해당 확인 한 가지. PC의 모든 표·설정을 축소 복제하지 않는다.
- 기존124개 토큰과 브랜드는 출발점이다. 전체 페이지/실제 표/폼에 일관되게 적용하고 사용하지 않는 장식·반복 경고를 줄인다. 의미가 필요한 오류·불명·오래됨·권한·HOLD는 숨기지 않는다.

세부 화면은 실제 클릭/입력 가능한 브라우저 시안과 함께 검토한다. 성공한 임의 화면 한 장으로 제품 전체를 승인하지 않는다.

## 3. 실행 방식 비교와 선택안

| 방식 | 이점 | 비용/판단 |
|---|---|---|
| 현 stdlib WSGI/SPA 확장 | domain/의존성 유지 | 인증/세션/CSRF/권한/DB migration/폼/배포를 직접 만들어야 함. 제품 완성의 최소 변경이 아님 |
| **Django 모듈형 단일 앱 + server templates/작은 JS** | 기존 Python domain 재사용, 기본 auth/session/permission/CSRF/migration/form 활용, 독립 배포 | 웹 의존성 추가와 영속 모델/기존 장부 경계 설계가 필요. 추천안 |
| FastAPI + 별도 SPA | API/프런트 독립 개발에 적합 | 별도 UI 빌드·인증/권한/세션·두 배포면과 추가 연결 관리가 필요. 현재 단일 제품/팀 계정 목표에는 비용이 큼 |

Django5.2 LTS와 Waitress를 웹 extra로 추가하는 안을 우선 검토한다. 기존 CLI/domain/unittest의 표준 라이브러리 실행은 유지한다. Django 기본 auth를 사용하고 비밀번호/세션 서명을 직접 구현하지 않는다. 로그인 시도 제한은 검증된 구성요소 또는 명시적으로 검증한 배포 경계로 처리하며 무제한 로그인 시도를 방치하지 않는다. React/Celery/Kafka/다중 tenant·일반 plugin 구조는 이 목표에 추가하지 않는다.

선택 근거: [Django 인증](https://docs.djangoproject.com/en/5.2/topics/auth/default/), [지원 버전](https://www.djangoproject.com/download/), [배포 점검](https://docs.djangoproject.com/en/5.2/howto/deployment/checklist/), [Waitress](https://docs.pylonsproject.org/projects/waitress/en/latest/). 버전 범위/잠금과 Python 지원은 구현 직전 공식 자료 및 설치 해석 결과로 고정한다. 개발 runserver를 운영 서버로 제시하지 않는다.

## 4. 경계와 데이터 소유

```text
브라우저 → Harbor web(session/권한/CSRF/폼/조회·명령/감사)
             → application services → 기존 Harbor domain
             → 업무 장부 facade → 기존 BusinessLedger/Transport/Departure
             → RosyClient → Fleet 공개 API → CORE/팔 owner

웹 DB: 계정·화물 기본 정보·업무 지시/계획 revision·기준 참조·원본 근거 참조·감사·비교 자료
기존 장부: 실행 의도·중복 방지·현재 mission/attempt·업무 근거 대조의 기존 소유권 유지
플랫폼 DB/ROS/장치: 접근·import하지 않음
```

- 초기 배포 프로필은 **단일 사이트·단일 웹 프로세스**다. DB 제품/worker를 바꿔도 같은 의미라고 가정하지 않는다. replica/다중 writer는 별도 검증 전 금지한다.
- 새 웹 DB는 기존 business SQLite와 분리한다. 원본을 자동 채택/마이그레이션/초기화하지 않는다. 절대 경로·소유자 검사, versioned migration·백업/복원, demo/live DB 혼용 방지를 둔다.
- 기존 장부는 프로세스당 한 service가 소유한다. constructor 복구를 요청마다 실행하지 않는다. 업무별 명령 직렬화·optimistic version 검사와 단일 writer lease를 검토한다. CLI/별도 프로세스의 병행 writer를 허용하는 척하지 않는다.
- `refresh()`는 쓰기다. GET/polling에서 호출하지 않는다. 명시적인 대조 명령 또는 관리 worker가 감사와 함께 수행한다.
- `_reads` fresh proof는 인메모리다. confirm 전에 같은 소유 service에서 원래 job을 새로 대조하고 같은 업무·plan/row version·attempt의 근거만 사용한다. 재시작 후 브라우저의 과거 snapshot을 확인 증거로 쓰지 않는다.
- 두 DB의 변경은 한 transaction이 아니다. 업무 key/body digest·소유 장부 링크를 웹 DB에 먼저 영속화하고 준비/제안을 idempotent하게 조립한다. 중간 실패는 불명/HOLD와 재대조로 복구하고 새 key·삭제·자동 POST 재전송으로 덮지 않는다. 이 조립은 architecture review의 핵심 항목이다.
- 화물 기본 정보는 선언/등록 데이터다. 관측 현재 위치·top 카메라 결과·도킹·퇴피 근거는 producer의 사실이며 일반 사용자/관리자의 입력으로 '관측 확인됨'을 만들지 않는다.
- 실제 evidence provider는 인증된 지원 계약만 소비한다. 현재 receipt가 기존 RetreatEvidence의 모든 joint/zone 필드를 대체하지 않는 결손은 유지한다. fixture는 별도 환경에서 synthetic으로만 공급하고 실제 Fleet/관측에 fallback하지 않는다.

## 5. 제품 기능과 권한

| 영역 | 구현할 결과 | 중요한 게이트 |
|---|---|---|
| 계정·권한 | 로그인/로그아웃, 비밀번호 정책, 역할/비활성, session 만료, 서버측 읽기/명령 권한 | 자체 계정 확정. viewer/operator/field/admin 책임 분리. 앱 admin이 Fleet 실행/재무장 권한을 얻지 않음 |
| 화물·요청 | ID/기본 정보 검증·중복 거절, 입고/반출 요청, 계획/우선순위 그룹과 변경 버전, 상세/검색 | 단위 명시·invalid/미등록 HOLD·동시 변경 거절. 화물 등록이 실제 배치를 만들지 않음 |
| 조건·재고 | site/칸/용량/검증 기준 revision, 관측/예상/신선도, 원본 추적 | reach 표를 compile 사전 검증 완료로 바꾸지 않음. 진행 작업의 기준 덮어쓰기 금지 |
| 반출 계획 | 지시된 순위/동순위 허용 순서 검토, 재취급/반출/복귀, 공간/도달/관측 HOLD, 검토 기준 고정 | 기존 planner 재사용, 부족하면 부분 계획 없음. 계획 READY는 실행 허가 아님 |
| 실행 인계 | durable intent, 현재 ARM 단계 제안, 원래 key 조회/불명, Fleet 인계, 취소 요청/결과 대조 | ARM 제안과 TRIP 즉시 시작 의미 구별. 허용된 API·신원만 사용, 자동 admit/rearm 없음 |
| 현장 확인 | 배정된 차량/화물/공급 인계 회차, S 적재면 퇴피 확인/불일치·확인불가 | 최초 사람 확인과 장치 안전 허가 구별. 회차와 departure 의도 결속, 중복/오래됨/다른 대상 거절 |
| 근거·완료·복구 | 부족한 근거/해결 책임, 배치/복귀/관련 팔 퇴피 대조, 허용된 재개·재시작 대조 | receipt/Action/취소 ACK를 전체 완료/정지로 오인하지 않음. 임의 bool 완료·장부 삭제 금지 |
| 이력·비교 | 화물/업무/plan/mission/attempt/행위자/출처 추적, 같은 조건의 실측 자료 비교 | 계획 이동과 실제 취급 수 구별. 미측정은 null/미측정. synthetic과 measured 혼합·임의 개선율 금지 |

입고/관측/이송의 실제 연결은 현재 공개 API의 producer–consumer 경로를 확인한다. 필요한 필드/신원이 없으면 정식 계약 요청으로 처리하고 app에서 source를 합성해 메우지 않는다. 지원되지 않은 기능을 시각적 버튼만 만들어 완료하지 않는다.

## 6. 구현 단계와 파일 책임

1. **M0 기준 고정:** 기존 preview를 별도 조회 도구로 보존하고 요구사항/코드/API/결손 추적, 아래 capability·권한·명령 프로토콜과 디자인 방향을 review한다. 기존 문서 01/08의 역할·탐색 정본도 이 결정에 맞춘다.
2. **M1 웹 기반:** `harbor_web/`에 Django project/app·settings·URL·auth/roles·production WSGI·migration/환경 분리, 새 DB/legacy path guard를 구성한다. 기존 `harbor/` domain은 framework를 import하지 않는다.
3. **M2 영속 제품 입력:** `harbor_web/models.py`와 application services에 화물·조건 참조·요청·우선순위/계획 revision·감사/행위자 모델을 둔다. migration/recovery/경쟁 테스트를 먼저 작성한다. root CLI와 old preview는 호환 유지한다.
4. **M3 계획/장부 facade:** 기존 model/outbound/process/workflow를 소유 service로 조립하고 명시적 same-key HOLD 복구를 추가한다. 아래 durable command·writer lease·immutable plan·유실/재시작/version conflict·GET 무변경을 검증한다. 새 제품 DB는 업무 입력/감사를 저장하고 기존 장부 상태를 별도 원장처럼 복제하지 않는다.
5. **M4 첫 수직 흐름:** 로그인→화물/요청 저장→관측 기준 고정→계획→설치 binding을 가진 단일 ARM 제안→Fleet 인계→원래 key/receipt 조회를 실제 service와 새 웹 child HTTP로 검증한다. 업무 근거가 없으면 다음 단계/완료가 차단되는 것까지 통과 조건이다. 제품 browser 검사는 기존 합성 preview 검사와 별도로 둔다.
6. **M5 제품 화면 확장:** 공통 shell·조치 필요·화물/요청 표/폼·재고/계획·작업 상세·현장 배정·설정과 공급 인계 회차를 실제 services에 연결한다. TRIP는 Fleet named-operator 인계로 고정하며 앱이 대리 실행하지 않는다. 승인된 tokens를 재사용한다. 없는 입고 planner를 반출 planner로 대신하지 않는다.
7. **M6 근거·이력·비교:** 원본·미측정 상태·사용자 감사와 비교 입력/계측 provenance를 저장하고 표시한다. fixture/demo는 별도 DB/환경으로 유지한다. 측정값이 없는 경우 통계/성과 차트를 그리지 않는다.
8. **M7 수용/배포:** 유닛·Django auth/권한/CSRF·migration/backup/restore·경쟁/유실/재시작·실제 지원 API HTTP와 browser를 실행하고 독립 검토한다. production settings 점검·Waitress/정적 자산·TLS proxy 구성을 재현한다. 외부 게시/실제 장치 활성화 상태는 따로 보고한다.

설계의 파일 경계는 검토 후 실행 계획에서 더 나눈다. 작업자가 동시 편집할 모듈 소유권을 명시하며 기능을 나누어도 role/URL/DTO/토큰의 정본은 하나다. 단일 거대 views.py와 클라이언트 상태 머신으로 제품 전체를 다시 쌓지 않는다.

## 7. 완료 수용 기준

- **WEB-C1:** 계정 생성/로그인/로그아웃/비활성/역할 변경·session/CSRF·허가되지 않은 직접 URL/명령이 서버에서 검증된다. service token/비밀번호는 HTML/JS/URL/로그/공개 설정에 없다.
- **WEB-C2:** 실제 영속 화물/요청/우선순위/조건 버전을 입력·조회하고 duplicate/invalid/stale/version conflict를 구별한다. 재시작 후 보존된다.
- **WEB-C3:** 관측 재고·예상 결과·실행 사실·완료 근거가 구분된다. 미연동·미확인·조회 실패·오래됨이 0/정상/실행 가능으로 바뀌지 않는다.
- **WEB-C4:** 지시/기준을 검토해 재취급→반출→복귀 계획 또는 구체 HOLD를 만든다. 동시 수정은 원래 검토 버전으로 거절한다.
- **WEB-C5:** 준비된 계약에서 명시적 현재 단계 제안·원래 key 대조·Fleet 인계·취소 결과 대조가 실제 HTTP로 검증된다. 응답 유실/중복/재시작에서 추가 물리 작업을 만들지 않는다.
- **WEB-C6:** 현장 확인은 actor/차량/화물/회차/시각/현재 버전에 결속된다. 다른 회차·오래된 확인 재사용과 웹의 재무장/직접 장치 명령을 거절한다.
- **WEB-C7:** 완료/복구는 필요한 원본 근거와 소유 경계에 따른다. 미지원 producer/필드·원본 부족은 남은 gate로 표시하며 UI에서 사실을 합성하지 않는다.
- **WEB-C8:** 전 구간의 웹 입력/계획/제안/대조/사람 개입과 원본 참조를 조회한다. 비교 자료는 조건/출처/실측 여부를 보존하고 누락을 실제0으로 합산하지 않는다.
- **WEB-C9:** PC1440/1280, 태블릿768, 모바일390, 긴 한글/ID, empty/error/stale/403/409/pending, 키보드·drawer·포커스·뒤로 가기, 대량 목록에서 제품 흐름을 검증한다. 접근성은 자동 검사와 실제 조작 범위를 구별한다.
- **WEB-C10:** 새 DB migration·백업/복원·기존 장부 비파괴·생성 token CSS·wheel/static·deployment profile을 검증하고 소스/명령/실행·skip·미검증을 기록한다. 독립 review와 최종 시각 검토/문서화가 끝나야 웹 완성으로 보고한다.

이 C-ID는 이번 웹 목표의 내부 수용 항목이며 팀 UR/SR 번호·우선순위·채택 상태를 변경하지 않는다. API/현장 게이트가 남으면 기능 구현·contract test·운영 가능 상태를 나누어 보고한다.

## 8. 실패·복구와 미결정 검토

필수 실패 시나리오는 두 DB 중간 실패, 실물 제안 전후 응답 유실, 같은 업무의 다른 사용자 수정, source 지연/상충, 잘못된 mission/attempt/fence, 세션 만료/역할 변경, CSRF, 잘못된 파일/원본 데이터, DB 백업/복원 및 프로세스 재시작이다. 복구는 원본 key/버전 대조이며 rollback 명목으로 물리 실행·원장을 지우지 않는다.

architecture/critic은 특히 (1) 기존 장부 facade와 두 DB intent 조립, (2) single-process writer 소유, (3) TRIP 신원·외부 인계, (4) receipt→business evidence 결손, (5) web 완료와 외부 운영 수용 경계를 검토한다. 해결되지 않은 필드는 구현자가 추정하지 않는다. 실제 UI 방향은 현재 브랜드/확정 정보 구조에 근거한 전체 페이지 시안으로 확인한다.

## 9. 동결된 구현 결정과 계약 게이트

### 명령·복구 프로토콜

웹 명령은 actor, plan ID/version, legacy 장부 절대 경로의 식별값, Fleet origin/principal 식별값, 원래 request key, canonical body digest, 상태를 먼저 commit한다. 비밀 credential 자체는 저장하지 않는다. 상태는 `PREPARED → DISPATCHING → RECONCILED` 또는 `UNKNOWN/BLOCKED`다.

- 웹 intent만 저장되거나 ledger prepare 후 종료: 원래 key/digest로 prepare를 재대조한다. `PREPARED`이고 외부 제출을 한 번도 시도하지 않았을 때만 사용자의 명시적 제출이 가능하다.
- 외부 호출 **전** `DISPATCHING`을 commit한다. 그 이후 종료/응답 유실은 제출 여부 불명이며 자동 POST를 금지한다. ledger에 미션이 없어도 원래 단계 key를 조회한다. 명령 상태를 PREPARED로 되돌리지 않는다.
- Fleet 접수 후 ledger adoption/웹 결과 저장 전 종료: 원래 key로만 job을 읽고 binding/digest/mission을 대조해 복구 결과를 기록한다. 현재 단계가 바뀌었거나 미션/신원/원본이 불일치·부재하면 HOLD/UNKNOWN을 유지한다.
- 기존 `BusinessLedger`에 명시적인 ARM HOLD 대조 메서드를 추가한다. 현재 `refresh()`의 WAITING 전제를 우회해 SQL로 상태를 바꾸지 않는다. 복구는 동일 key GET이며 재전송이 아니다. HOLD 해제/단계 진행은 별도 명령과 신선한 완전 근거가 있어야 한다.
- 취소 의도를 먼저 영속화하고 영향 업무/후속 단계를 보류한다. 취소 ACK 후에도 실제 작업이 active인 경우, 응답 불명인 경우, 재시작 후인 경우 모두 자동 진행하지 않는다. 명시적 계속은 원래 실행과 일치하는 최신 근거 및 허용된 상태를 다시 검증한다.

초기 배포는 단일 사이트·단일 Waitress 프로세스다. **OS-backed exclusive writer lease를 장부 생성자/복구보다 먼저 획득**한다. 같은 장부를 여는 두 번째 운영 서버/지원 writer 진입점은 시작 실패해야 한다. 배포 소유 registry 하나가 ledger 수명과 per-business lock을 관리하며 refresh→evidence retrieval→confirm 전체를 직렬화한다. 권한·expected version·변경도 같은 보호 범위에서 확인한다. read-only preview는 읽기만 허용한다. 임의 외부 라이브러리 writer는 지원하지 않으며 운영 중 직접 장부 사용을 금지한다. 백업/복원은 writer를 닫고 일치하는 웹/장부 쌍을 보존·복원한 뒤 외부 key를 대조하고 명령을 연다.

### 실제 capability 범위

| 제품 전이 | 현재 producer/consumer | 이번 구현·검증 | 남는 외부 게이트와 소유자 |
|---|---|---|---|
| 화물·요청·우선순위 입력/계획 | Harbor models/services→기존 `plan_outbound` | 영속 입력, immutable 검토 revision, 실제 계산/보류 | 입고 planner는 별도 Harbor 기능이며 반출 계산으로 대체하지 않음 |
| 운영 재고 수신 | 인증된 운영 producer 계약 없음; `observation-acceptance.md` | 정의된 입력 검증/저장과 source·시각·revision, synthetic 환경 분리 | World/관측 담당: 인증·보정·대응 계약 및 실제 producer 수용. 운영 자동 수신 완료 아님 |
| 단일 ARM proposal/read | Fleet `work/arm_transfer_job_routes.py`, `RosyClient`, `ArmTransferBinding` | 기존 공개 경로 POST/원래 key GET/mission GET, scope·digest 검사 | 설치 담당: 해당 화물/위치의 검증 binding. Fleet 독립 승인 유지 |
| ARM receipt/cancel | Fleet `work/physical_transfer_observer_routes.py` | receipt 원본 대조, `/jobs/{id}/cancel`의 현재 reason-only 요청, 미준비 proposal의 conflict 포함 | ACK는 정지 근거 아님. 같은 scope/principal 유지 |
| 업무 다음 단계/완료 | `BusinessLedger.confirm/finish`, 배치+관련 팔 전부 근거 | 완전 자료의 domain/contract 검사와 결손 차단 | 관측/설치 담당: 인증된 placement·revision 대응·joint/claim/FK 근거. receipt만으로 완료 불가 |
| 다단계 반출 | 각 move의 설치 binding과 위 업무 근거 | 계획·원장·현재 단계만 제안하는 순서/보류 검사 | 모든 move binding과 producer 없이는 live 전체 실행 수용 불가 |
| 차량 TRIP | Fleet `trips/trip_job_routes.py`; named operator 생성 즉시 시작 | Harbor에 요청/인계 기록, Fleet 화면에서 named operator가 실행 | Fleet 담당: 별도 principal 조회/위임 계약 전 앱 자동 제출·교차 조회 불가 |
| S 적재면 확인/출발 | Harbor 현장 episode; 기존 `DepartureLedger` 차량-only 자료는 불충분 | episode/vehicle/cargo/actor/observed time/version/departure intent 결속·한 번 소비 | 실제 TRIP는 Fleet 운영자가 동일 차량/전제 확인 후 실행하는 운영 절차. Fleet server 강제 보장이라고 하지 않음 |

회차 기능은 새 episode/departure-intent API로 명시적 clearance ID를 소비한다. 기존 vehicle-only 자료는 이전 자료로 보존하고 새 회차로 승격하지 않는다. 같은 차량 연속 회차·두 fresh 확인·동시 사용자·오래된 탭·소비와 intent 준비 사이 종료를 시험한다. intent와 소비는 같은 제품 DB transaction으로 묶고, Fleet 인계 시 후보 차량 풀로 제출하지 않는다.

### 역할·화면·수용 보완

- `viewer`는 업무 자료 읽기, `operator`는 업무 입력/계획/현재 ARM 제안·대조/취소 요청 및 현장 배정 생성·변경, `field`는 **자기 배정만 읽기/확인**, `admin`은 계정/역할과 설치 설정이다. admin에 operator/field를 자동 부여하지 않는다. 필요 겸임은 명시적 복수 역할이다. Fleet 승인/복구 권한은 별개다.
- 초기 관리자는 대화형 `createsuperuser`로 발급하며 고정 비밀번호/공개 seed 계정을 만들지 않는다. 관리자는 계정 생성·활성/역할을 관리한다. 비밀번호 변경과 관리자의 초기화 절차, 만료, 비활성/역할 취소가 다음 요청부터 적용되는 것을 WEB-C1에 포함한다. login throttle은 Django auth와 DB-backed django-axes로 구현한다.
- 경로/동작 정본을 별도 실행 명세에 고정하고 기존 01/08에서 참조한다. field 다른 배정의 URL/POST 거절, 배정 변경 후 기존 탭 거절, 로그인 redirect의 외부 URL 거절을 검사한다.
- WEB-C8은 **추가 차량 왕복/이송 수와 사람 개입 수를 주 지표**, 시간을 보조 지표로 검사한다. 동일 초기 배치/입력 정보 가용 시각/조건 revision, 실제 도달 가능한 한 팔 baseline을 맞춘 측정 자료를 비교한다. 유효한 측정 입력을 처리하는 시험과 fixture UI 시험, 현장 성능 주장을 분리한다. 미측정 화면만으로 비교 기능 수용을 대신하지 않는다.
- 필수 제품 browser suite는 새 계정 로그인·영속 쓰기·역할 거절·409·수직 흐름·현장 배정·재시작을 검사하며 설정 누락은 실패다. 기존 `web_browser_check.py`는 합성 preview 회귀로 그대로 남긴다. 실제 Fleet HTTP 검증은 지정된 test PC와 고정 양 끝 commit으로 실행한다.
- v0.1 독립 검토 두 건 모두 REVISE였다. 이 v0.2는 변경된 결정을 다시 검토하는 동결본이며, 계획 검토 승인은 구현/장치 수용 증거가 아니다. 외부 producer/위임 계약이 남아 있으면 해당 부분과 전체 운영 수용은 미완료로 유지한다.

## 10. 구현 결과와 남은 운영 게이트

지원된 웹 범위의 구현·검증을 마쳤다. [최종 검증 기록](../validation/2026-10-25-product-web.md)은 제품 코드 `885a566`, 취소 보류 실제 HTTP 검증 source `82e4ecf`와 platform `bb38fdc`를 고정한다. 웹 시험72개, 표준 suite117개 중115개 수행/2skip, 네 viewport의 실제 계정/업무/현장/비교 동작, 설치 wheel, 독립 코드/디자인 검토와 실제 Fleet HTTP를 구분해 기록했다. 누락된 운영 producer/입고 planner/모든 이동 binding/실물·현장 수용은 §9의 미완료 게이트로 남는다.

한 요청에 하나의 실행 원본, 지시 전후·검토 당시 요청/큐 불변 원본, 최신 관측 revision 검사, writer 소유·취소 불명·짝 복원 대조가 최종 구현에 포함된다. 지원되지 않은 사업 완료나 직접 장치 실행을 버튼으로 대신하지 않는다. 개발 화면은 [제품 웹 안내](../product-web.md)로 실행한다. 외부 원격 main 게시/PR·CI와 현장 운영 완료를 이 웹 구현 결과에 포함하지 않는다.
