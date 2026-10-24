# 앱 구성·데이터·연결 계약 상세설계

상태: 제안. 현재 domain/장부/HTTP client를 재사용하는 웹 계층의 설계이며 새 서버·인증·DB migration은 구현하지 않았다. 외부 계약 정본은 플랫폼 API reference, Harbor 소비 기준은 [platform-contract.md](../platform-contract.md)다.

## 1. 프로세스와 배포 경계

권장 구성은 **브라우저 → Harbor 웹 서버 → 기존 Harbor domain/장부 → Fleet HTTP**다. PC와 태블릿은 같은 Harbor 서버의 서로 다른 task 화면을 사용한다. browser에서 Fleet 서비스 토큰·관측자 자격·장치 접속 정보를 보유하지 않는다.

| 구성 | 책임 | 현재/제안 |
|---|---|---|
| PC/태블릿 웹 | 표시·사용자 입력·현재 검토 버전 전송 | 신규 |
| Harbor 웹 경계 | 세션·업무 범위·CSRF·입력 제한·read model·명령 처리·감사 | 신규 |
| application service | 계획 고정, 현재 단계 제안, 상태 대조, 업무 근거 적용 | 기존 모듈 조합 위에 신규 조립 |
| model/outbound/process | 업무 제약·계획·물리 binding | 기존. 양하 계획과 다단계 운영은 별도 확장 |
| workflow/transport/departure | 각각 업무/이송 의도/S 퇴피 확인 장부 | 기존. 통합 트랜잭션·회차 결속·사용자 감사는 미구현 |
| rosy_http | typed body 구성·HTTP 제안/조회 | 기존. 승인/관측/재무장 클라이언트로 확장하지 않음 |
| 업무 관측 어댑터 | 인증된 원본 관측을 업무 ID·revision·현재 attempt와 결속 | 미구현 |
| Fleet/CORE/owner/World | 승인·실행·자원·물리 사실·최종 명령 권한 | 별도 제품 |

초기는 서버가 HTML과 작은 동작 스크립트를 제공하는 구성을 권장한다. SPA·별도 실시간 gateway·메시지 broker를 선행 도입할 근거는 아직 없다. framework·인증 제품·배포 host는 미정이며 domain의 표준 라이브러리 범위를 자동으로 바꾸지 않는다. 운영 배포는 TLS·인증·현장 수용 이후다.

## 2. 장부 수명과 동시성

`ARCH-01` `BusinessLedger.__init__`는 SUBMITTING을 HOLD로 만드는 재시작 복구를 수행한다. HTTP 요청마다 생성하지 않는다. app 시작 시 복구와 준비 상태 점검을 거친 하나의 장부 service를 관리한다.

`ARCH-02` `BusinessLedger._reads`는 인메모리이며 fresh reconciliation과 confirm을 결속한다. 첫 웹 조립은 단일 프로세스와 업무별 직렬 명령 처리로 제한한다. 여러 worker·여러 replica·rolling restart를 허용하기 전 fresh-read proof의 영속화/세대·동시성 시험이 필요하다. SQLite 연결만 공유하면 해결된다고 가정하지 않는다.

`ARCH-03` 기존 `refresh()`는 row_version과 근거 대조 상태를 변경한다. 일반 GET 화면 조회·자동 polling에서 무조건 호출하지 않는다. 순수 read model 조회와 **실행 상태 대조 명령**을 구분하고, 대조가 업무 상태를 바꾸는 경우 감사를 남긴다.

`ARCH-04` 명령은 사용자·업무 scope·기대 row_version/plan revision을 검사한다. 제안/근거 적용의 최종 시점에 다시 검사한다. 프로세스 내부의 업무별 직렬화는 앱 장부 보호용이며 Fleet의 arm/zone/object claim을 대신하지 않는다.

장부는 Git 밖의 승인된 절대 경로에 보관한다. 웹 세션·확인 회차 등 새 저장 구조를 도입할 때는 versioned migration·원본 백업·복구 시험을 둔다. 기존 파일 삭제·열 이름 재사용·state 초기화로 전환하지 않는다.

## 3. UI를 위한 read model 제안

새 read model은 현재 플랫폼 응답에 이미 있는 필드라고 가정하지 않는다. Harbor가 출처를 보존하며 조립한다.

| 묶음 | 필요한 필드/관계 | 정본 |
|---|---|---|
| 업무 | business_id/request_key, 종류, 화물, 현재 step, 계획/관측 revision, row_version, 업무 상태 | Harbor |
| 물리 연결 | 단계별 binding 종류·버전, object/source/target refs, 설치 검토 상태 | Harbor의 검증된 설치 대응 + 플랫폼 계약 |
| 실행 연결 | mission_id, action/attempt(확보된 경우), request/proposal digest, Fleet 상태, 확인 시각 | Fleet. 로컬 receipt/장부에 원래 identity 보존 |
| 근거 | observation/evidence ID, 인증된 source, 원본 시각, 수신 시각, 품질, 대상·revision·attempt 대응 | 원본 producer + Harbor 대조 |
| 표시 상태 | 승인 대기/수행 중/근거 부족/불명, 차단 이유, 담당 책임 | 위 정본의 파생 표현. 상태 이름을 섞지 않음 |
| 조작 가능성 | 허용 조작, 부족한 조건, 현재 version, 환경/수용 수준 | Harbor 서버의 현재 권한·조건 검사 |

보여 줄 수 없는 필드는 null/미확인으로 남긴다. job 응답에 없는 action ID를 mission ID로 채우지 않는다. 관측 revision, 업무 plan revision, SQLite row_version, authority epoch/generation은 서로 다른 값이다.

## 4. 현재 외부 계약과 UI의 연결

아래 `/{mission_id}`는 해당 행의 jobs 경로 뒤에 붙는다. 모든 경로는 실제 구현/계약에 대한 기준이며 Harbor 웹 endpoint를 뜻하지 않는다.

| 기능 | 현재 ROSY surface | UI/서버의 처리 |
|---|---|---|
| ARM 제안 | `POST /api/fleet/arm-transfers/jobs` | request_key + workcell/instance/object/source/target만. 내구 의도 보존 뒤 전송 |
| ARM 대조 | `GET /api/fleet/arm-transfers/jobs?request_key=…`, `GET /{mission_id}` | 원래 principal/key/body/mission 결속 대조. 새 key로 재시도하지 않음 |
| ARM 승인 | `POST /{mission_id}/admit` | Fleet named operator가 담당. Harbor server가 대리 호출하지 않음 |
| 물리 receipt | `GET /{mission_id}/physical-receipt` | 원래 job/참조/policy/fence 대조. pending code는 정상 대기 |
| ARM 취소 | `POST /{mission_id}/cancel` | 기존 계약은 있으나 현재 RosyClient 메서드는 없음. 업무 권한·유실 대조 설계 후 연결. ACK는 정지 아님 |
| 원본 물리 관측 | 전용 observation-context/observations | 독립 producer의 surface. Harbor browser/BFF 자격에 추가하지 않음 |
| TRIP | `POST /api/fleet/trip-jobs`, ID/key GET | ARM 승인 대기와 같은 의미로 취급하지 않음. 현재 operator 자격·명시 확인을 앱 서비스 토큰으로 우회하지 않음 |
| Cell 호환 | cell-app compile/proposal, cell-jobs GET | 기존 binding 흐름 유지. ARM과 같은 body/digest로 변환하지 않음 |

Harbor user가 Fleet을 열 때는 설정된 콘솔과 job ID를 전달하는 인계 UX부터 제공한다. 현재 neutral ARM의 전용 승인 화면·검증된 deep link가 구현돼 있다는 근거는 없다. 이 화면이 운영 필수이면 플랫폼이 기존 GET/admit을 소비하는 UI를 먼저 구현·수용해야 한다.

Fleet 서비스 principal은 Harbor 서버의 기계 신원이다. 실제 사람 신원은 별도의 세션·감사에 남긴다. 서비스 토큰으로 모든 사람을 Fleet operator처럼 행동시키지 않는다. 기존 Account/위임 기능을 사용할지는 해당 계약의 Harbor 지원 범위를 확인한 뒤 결정한다.

## 5. Harbor 내부 조작 계약 초안

새 endpoint URL을 확정하지 않고 application operation을 먼저 고정한다. 웹 구현 때 아래 의미에 맞춰 Harbor 소유 route를 설계하고 version/오류를 문서화한다. 이것은 ROSY 신규 API 등록안이 아니다.

| operation | 입력의 핵심 | 결과·금지 |
|---|---|---|
| `list_business`, `read_business`, `read_inventory` | session scope, filter/cursor, business_id | 순수 조회, 원본 revision/시각 보존 |
| `prepare_plan` | 화물·업무 요청·expected inventory revision | 결정적 계획 또는 구체 HOLD. 실행 없음 |
| `accept_plan` | business_id, expected plan/row version | 검토 기준 고정. 승인·실행 허가 아님 |
| `propose_current_arm_step` | business_id, expected version, 동일 의도 key | durable SUBMITTING 후 단1회 POST, 응답 불명은 HOLD |
| `reconcile_current_execution` | business_id, expected version | 원래 key/mission 조회·동일성 대조. 자동 resend 없음 |
| `apply_business_evidence` | 원본 evidence refs, expected version | 인증된 관측 재검사 후 기존 confirm/finish. boolean 완료 입력 없음 |
| `record_supply_clearance` | 현재 차량/공급 인계 회차, expected version, 확인 내용 | actor/source는 세션/등록 경계에서 결정. 조건 결손 시 거절 |
| `request_cancel` | business_id, expected version, 제한된 reason | 계약·권한이 준비된 경우에만 취소 요청 기록. 자원 해제 약속 없음 |

브라우저에서 source_id, approved_by, grant, claim, generation을 임의로 정하게 하지 않는다. 인가·nonce/CSRF·동일 key 충돌·중복 클릭·버전 충돌은 서버가 검사한다. 재인증과 네트워크 재연결은 과거 쓰기 요청의 자동 재시도 조건이 아니다.

## 6. 추가 데이터와 감사 제안

| 대상 | 기존/신규 | 설계 조건 |
|---|---|---|
| business/evidence/transport/clearance 장부 | 기존 | 원본과 현재 state 의미 유지. 각 장부의 파일/트랜잭션 경계를 실제로 확인 |
| 사람 session·역할/업무 scope | 신규 | 구현·프로비저닝 미정. secret/token을 감사 payload나 UI에 저장하지 않음 |
| 공급 인계 회차 supply handoff episode | 신규 | 차량·화물·공급 도착/하차·퇴피 확인·그 차량의 출발 의도에 결속. 다른 회차 재사용 불가 |
| 업무 명령 감사 | 신규 | actor, business/key, expected/result version, 원본 refs, 결과/오류, 서버 수신 시각 보존 |
| evidence inbox/대응 | 신규 | 인증 producer와 원본 ID 중복 방지, immutable 내용, 시도·버전 대응, 거절 사유 |
| 설치 기준/수용 수준 | 신규 조립 | 검증된 binding/config revision·scope·적용 기간과 실제 수용 근거. 화면 토글로 운영 승격 금지 |

로그에는 토큰·운영 연결 주소·원본 민감 데이터 대신 허용된 참조와 결과만 남긴다. 보존 기간·열람 권한·백업/RPO/RTO는 운영 담당과 결정해야 하며 미정 숫자를 확정 SLA로 기록하지 않는다.

## 7. 선행 결손과 계약 요청 목록

| ID | 실제 결손 | 해결 소유자 | 개방을 막는 기능 |
|---|---|---|---|
| GAP-01 | Harbor 웹 로그인·scope·CSRF·감사 없음 | Harbor/인증 담당 | 모든 운영 쓰기 |
| GAP-02 | `_reads` 인메모리, constructor 복구와 refresh side effect | Harbor | 다중 worker·요청별 장부 생성·무조건 GET refresh |
| GAP-03 | `SupplyClearance`에 공급 인계 회차·화물·요청 결속 없음; Departure/Transport 별도 처리 | Harbor + Fleet 계약 협의 | 실제 현장 확인으로 TRIP 출발 허가 |
| GAP-04 | 인증된 업무 관측→Placement/Retreat 운영 어댑터 없음 | 관측/설치 + Harbor | 실제 업무 confirm/finish |
| GAP-05 | receipt와 기존 RetreatEvidence의 joint/zone 요구가 일대일 대응하지 않음 | 플랫폼 사실 계약 + Harbor 어댑터 | receipt만으로 관련 팔 퇴피 근거 합성 |
| GAP-06 | neutral ARM 승인 UI/deep link 수용 미확인 | Fleet UI 담당 | 운영자의 검증된 화면 승인 |
| GAP-07 | TRIP operator 신원 전달/승인 의미와 Harbor service 신원이 다름 | 플랫폼 계약 + Harbor | 웹 서버의 통합 “출발/실행” 버튼 |
| GAP-08 | 다중 물리 binding·순차 단계·새 관측과 관련 팔 전체 근거 운영 대조 미완료 | Harbor + 설치/플랫폼 | 전체 반출 원클릭 실행 |
| GAP-09 | 서로 다른 실제 사람의 승인 관계 미정 | 운영/인증 + 양쪽 앱 | “2인 확인 완료”라는 사람 수준 보장 |

GAP-03의 확인을 먼저 소비하고 TRIP 요청만 실패하는 경우를 숨기지 않는다. 현재 구조를 임의의 분산 트랜잭션으로 부르지 않고, 같은 앱 장부에 내구 intent와 소비 관계를 남기는 방법·재시작 대조를 설계해야 한다. 관련 ROSY 계약이 부족하면 먼저 정식 변경을 요청한다.
