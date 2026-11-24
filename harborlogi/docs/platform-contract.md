# 플랫폼 연결 기준

전체 책임 구분은 [Harbor와 ROSY의 책임](responsibilities.md)과 [ADR0028](adr/0028-control-units-device-owners-and-hosts.md)을 따른다. Harbor 업무·R/셀의 원장/admission·CORE/OMX 최종 실행·공통 서비스·호스트는 별개다. 이 문서의 고정 플랫폼 SHA·API 버전·당시 시험 근거는 이번 책임 안내 갱신으로 바꾸지 않는다. ARM 증거·재검사의 직계 자식 제한과 주행 위임/셀 ARM 승인 구분도 위 책임 안내에서 확인한다.

## 연결 규약

Harbor와 ROSY가 서로 닿는 길과 지켜야 할 규칙을 한곳에 적는다. 근거와 배경은 각 ADR에 있고, 이 절과 ADR이 다르면 이 절을 고친 뒤 ADR을 맞춘다. 2026-11-14에 양쪽 코드와 현장 응답을 대조해 정했다.

### 통신 경로

| 경로 | 방향 | 전송 | 신원 확인 | 권한 |
|---|---|---|---|---|
| A. 업무 요청 | Harbor 서버 → 통합관제 R의 Fleet REST | https, 사이트 CA로 검증 | service principal 하나의 Bearer 토큰 | R이 `service` 역할로 제한. Harbor는 제안과 조회만 한다 |
| B. 화면 | 브라우저 → Harbor | https(자체 프록시), HSTS·Secure 쿠키 | Harbor 세션 | Harbor 그룹 viewer·operator·field·admin |
| C. 사람 로그인 | 브라우저 ↔ Rosy Account, Harbor 서버 → Account | https, Account 인증서로 검증 | authorization code + PKCE | Account의 `harbor.*` 권한 네 개 |
| D. 셀 실행 | R → 셀 | ROSY 내부(D-965) | ROSY 소유 | Harbor는 셀을 부르지 않는다 |
| E. 역방향 | ROSY → Harbor | 없음 | 없음 | ROSY는 Harbor를 부르지 않는다 |

A와 C는 주소가 서로 다를 수 있다. Fleet 주소는 `HARBOR_FLEET_ORIGIN`, Account 주소는 `HARBOR_ROSY_ACCOUNT_FILE`의 `issuer`가 정본이다.

### 규칙

전송과 신뢰

1. Harbor가 R과 Account를 부를 때는 https만 쓴다. 평문 http는 같은 PC 안의 시험(loopback)에서만 허용한다. redirect는 따라가지 않는다.
2. 인증서와 호스트 이름 검증을 끄지 않는다. 주소는 인증서에 적힌 이름으로 설정하고 IP 주소로 적지 않는다. 사설 CA는 `SSL_CERT_FILE` 또는 `HARBOR_FLEET_CA_FILE`로 더한다.

기계 신원(경로 A)

3. Harbor 서버는 R이 발급한 service principal 하나(96자 이하)의 Bearer 토큰만 쓴다. 토큰은 서버 환경 변수에만 두고 DB·화면·기록에 남기지 않는다. 실행·제안 경로는 사람의 로그인 토큰으로 R을 부르지 않는다(단, 읽기 전용 탑뷰 프록시는 ADR 0037에 따라 로그인한 사용자의 Rosy 계정 토큰을 서버에서 전달하며 브라우저에 노출하지 않는다).
4. 실행 경로는 R을 부를 때마다 먼저 `GET /api/fleet/session`으로 `principal_id`가 설정값과 같고 `role`이 `service`인지 확인한다. 다르면 요청을 보내지 않는다.
5. Harbor는 작업을 제안하고 결과를 읽기만 한다. 승인(admission)은 ROSY가 하고, 셀 API는 직접 부르지 않는다.

REST 요청(경로 A)

6. 모든 쓰기 요청은 본문에 `request_key`를 넣고, 보내기 전에 Harbor 장부에 그 키와 내용을 기록한다. 같은 키로 다른 내용을 보내지 않는다.
7. 쓰기 POST는 키마다 한 번만 보낸다. 응답을 못 받았거나 오류가 나면 결과를 모르는 상태로 두고 다시 보내지 않는다. 같은 키의 `GET ?request_key=` 조회로만 결과를 맞춘다. HTTP 단위 재시도는 없고, 조회는 명령마다 5초에서 300초까지 간격을 늘리며 반복한다.
8. Fleet API에는 버전 번호 응답이 없다. 호환 여부는 기능마다 읽기 전용 조회로 판정한다. 없는 키에 R이 자기 `code`를 붙여 거절하면 그 기능이 있는 것이고, `code` 없는 404와 503은 없는 것이다. 있다고 확인된 기능만 켠다.
9. 오류 본문은 `{"detail": {"code": "..."}}` 모양이다. 401과 403은 신원 문제이므로 재시도하지 않고 연결 확인으로 보낸다. 404는 화물이나 작업이 없다는 근거로 쓰지 않는다.
10. `port-events`의 cursor는 뜻을 해석하지 않고 Fleet 연결마다 저장해 그대로 돌려보낸다. 응답은 `events`·`cursor`·`complete` 세 값만 받는다. 사건 하나라도 받아들이지 못하면 cursor를 올리지 않는다. R이 한 번도 답하기 전에는 화물을 분실로 판정하지 않는다.
11. R에 `port-events` 경로가 없으면 연결 확인 결과에 「R 항만 이벤트 조회 경로 설치」가 미충족으로 보여야 한다. 조용히 넘기지 않는다.

사람 로그인(경로 C)

12. Rosy 계정 로그인은 authorization code, S256 PKCE, `state` 대조로만 한다. 토큰은 Harbor 서버가 Account에서 직접 받고 URL에 싣지 않는다. 운영 `issuer`와 `callback`은 https다. Harbor는 토큰 서명을 따로 검증하지 않으므로 브라우저를 거쳐 온 토큰은 믿지 않는다.
13. Harbor가 받는 권한은 `harbor.view`·`harbor.operate`·`harbor.field`·`harbor.admin` 네 개뿐이다. 하나도 없으면 로그인을 거절하고 토큰을 회수한다. 10초마다 Account에 다시 확인한다. Account가 답하지 않으면 마지막 토큰의 만료 시각까지만 유지한다. 로컬 Harbor 로그인은 복구용으로 항상 남긴다.
14. Harbor와 Account가 도는 PC는 시계를 NTP로 맞춘다. ROSY 쪽 토큰 검증은 발급 시각을 10초 여유로 검사하고, Harbor는 만료 시각만 자기 시계로 본다.
15. Rosy 계정 로그인을 켜려면 Harbor 화면의 인증서를 운영자 브라우저가 신뢰해야 한다. HSTS가 켜져 있어 브라우저 경고를 넘길 수 없다.

방향

16. ROSY는 Harbor를 부르지 않는다. 두 제품은 경로 A와 C로만 이어진다.

### 이중 모드 OMX 셀 계획 · 2단계 반출 Pinky 이송 계약 (2026-11-24)

- **이중 모드 셀 계획 (`/api/fleet/pipeline/cell-plans`)**:
  - **모드 1 (`mode="harbor_plan"`, `order_source="harbor"`)**: Harbor가 계산한 `steps[]` 이동 순서와 `wire_ref` 좌표를 그대로 보낸다. R은 물리·선행적재·티어 합법성을 fail-closed 검증해 거부(HTTP 422)하거나 `PROPOSED` 태스크 체인을 생성한다.
  - **모드 2 (`mode="outbound"|"rehandle"|"goal"`, `order_source="rosy_r"`)**: Harbor는 `steps` 없이 목표 지시와 현재 배치를 보내고, R이 이동 순서·재취급·슬롯 좌표를 자율 산출한다.
  - **규칙 4·5·6·7·8·9 준수**: `CellPlanDispatch`는 `_default_client()`에서 `expected_principal`과 `expected_role='service'`를 함께 검증한다(규칙 4). POST 전 `state='SUBMITTING'`과 `request_key`·`request_payload`를 먼저 기록하고(규칙 6), 응답 유실/일시 오류 시 `UNCERTAIN`으로 보존하며 절대 재전송하지 않는다(규칙 7). 복구는 `GET /api/fleet/pipeline/cell-plans?request_key=<key>`로만 수행하며, 미등록 키에 대해 R이 `{"detail": {"code": "UNKNOWN_CELL_PLAN"}}` HTTP 404를 돌려줄 때만 `NOT_FOUND`로 판정한다(규칙 8·9). 승인(`admit`)은 ROSY 소유다(규칙 5).
  - **사전 모의(`/dry-run`), 취소(`/cancel`), 중단 사유 및 타입 스키마**: `POST /api/fleet/pipeline/cell-plans/dry-run`은 원장을 바꾸지 않는 모의 계획이며, `POST /api/fleet/pipeline/cell-plans/{plan_id}/cancel`은 대기 단계를 취소한다. 응답의 `hold_reason`·단계별 `last_refusal`·`cancel_reason`을 `CellPlanDispatch.reason`에 반영한다. ROSY 측은 `contracts/foundation/core_common/protocol/cell_plans.py`(Pydantic v2), Harbor 측은 `harbor/cell_plan.py`·`harbor/fleet_types.py`(frozen dataclass)로 양방향 와이어 계약을 타입 검증한다.
- **반출대(`stand`) → 2단계 Pinky 이송 (`OutboundDispatch`)**:
  - 완료된 `OUTBOUND` `Command` 또는 `stand` 목적지의 완료된 `CellPlanDispatch`는 단일 2단계 `TripIntent`로 처리한다: Leg 1(`S_OUT` 도킹, `object_ref=""`) → 현장/운영자 적재 확인(`LOADED`, `confirmed_by`) → 고정 차량 Leg 2(`S_EXIT` 출구 이동, `object_ref=""`).
  - `executor.tick`은 `cell_plan_dispatch.tick` → `outbound_loop.tick` 순으로 실행하며, `HARBOR_OUTBOUND_AUTO_DISPATCH=1`일 때 완료된 반출 `CellPlanDispatch`에서도 1차 `S_OUT` 배차를 자동 준비한다. 반출대(`stand_zone_id`)당 활성 배차는 1건으로 제한한다.

#### 구역·좌표·정지선 1:1 매핑 규약 (ROSY ↔ Harbor)

ROSY 정본(`shared/web/cell-site-map.json`, 37셀·13슬롯)과 Harbor `Condition`/`cell_plan`/`outbound` 간 좌표·차량 정지선은 아래 1:1 매핑을 따른다.

| 구역 (`zone_id`) | 형태 (`rows×cols×tiers`) | `slot_cells` | `wire_ref` (슬롯 시작점만 허용) | 담당 암 (`arm_id`) | 비고 |
| --- | --- | --- | --- | --- | --- |
| `yard` | `3×6×3` | `3` | `yard-r{1..3}-c{1\|4}-l{1..3}` (`slot_id`: `yard:r{1..3}-s{1\|2}`) | `arm_a`, `arm_b` | `hold 1`=`cols 1..3`(`c1`), `hold 2`=`cols 4..6`(`c4`). 구명칭 `ship-h1`/`ship-h2` 금지 |
| `temp_a` | `3×3×3` | `3` | `temp_a-r{1..3}-c1-l{1..3}` (`slot_id`: `temp_a:r{1..3}-s1`) | `arm_a` | `c2`, `c3` 직접 지정 금지 |
| `temp_b` | `3×3×3` | `3` | `temp_b-r{1..3}-c1-l{1..3}` (`slot_id`: `temp_b:r{1..3}-s1`) | `arm_b` | `c2`, `c3` 직접 지정 금지 |
| `stand` | `1×3×1` | `3` | `stand-r1-c1-l1` (`slot_id`: `stand:r1-s1`) | `arm_a` | 단일 1층 슬롯 (`arm_b`·`l2+` 금지) |

- **차량 정지선 (`rosy.port-site-places.v1`)**:
  - `S1`=`inbound_load`, `S2`/`S4`/`S5`=`wait`, `S3`=`intake_dropoff`, `C1..C3`=`charge`, `S_OUT`=`outbound_pickup`.
  - `outbound_handoffs[*].vehicle_exit_stop_id`는 전용 출구(`outbound_exit`)여야 하며, 입하 정지선 `S1`(`inbound_load`) 겸용 시 `OUTBOUND_EXIT_ROLE_MISMATCH`로 경고한다.
  - `not_places`(`belt`, `supply_s`, `ship_stern`, `ship_bow`, `vehicle_tray`)에는 차량 정지선을 생성하지 않는다.
- **사전 검증 및 라이브 대시보드**:
  - `harbor.cell_site_map`(`canonical_cell_wire_refs`, `canonical_cell_slot_specs`, `validate_canonical_cell_placement`, `validate_canonical_wire_ref`)과 `harbor.cell_plan`(`observation_site_state`, `build_harbor_plan_spec`, `build_rosy_directive_spec`)은 비정규 좌표·슬롯 비시작 열(`c2`/`c3`/`c5`/`c6`)·구역 담당 암 불일치·13개 `slot_id` 외 `goal` 키·최대 단수(`max_tier`) 초과를 송신 전 차단한다.
  - `/inventory/`는 활성 `Condition`이 없거나 ROSY 정본(`cell_zones`, `outbound_handoffs`)과 어긋날 때 `compare_condition_with_rosy` 결과를 표시한다.


### 규칙별 확인 방법

| 규칙 | 자동 시험 | 실제 Fleet(로컬) | 현장 읽기 전용 점검 |
|---|---|---|---|
| 1·2 | `tests/test_http_execution.py`(평문·redirect 거절) | - | `connection_probe` 1·2 |
| 3·4·5 | `tests/test_service_trust.py`, `harbor_web/tests/test_fleet_connection.py`, `harbor_web/tests/test_cell_plan_dispatch.py` | `transport_http.py` | `connection_probe` 3·4 |
| 6·7 | `tests/test_transport_execution.py`, `tests/test_http_execution.py`, `harbor_web/tests/test_cell_plan_dispatch.py` | `transport_http.py`(응답 유실 뒤 `?request_key=` 복구 포함) | - |
| 8·9·11 | `harbor_web/tests/test_fleet_connection.py`, `harbor_web/tests/test_readiness.py`, `harbor_web/tests/test_cell_plan_dispatch.py` | `transport_http.py` (`UNKNOWN_CELL_PLAN` probe 포함) | `connection_probe` 9·11 |
| 10 | `tests/test_port_events.py` | `transport_http.py`(경로가 있는 플랫폼에서) | - |
| 12·13 | `harbor_web/tests/test_rosy_account.py` | 없음 | `connection_probe` 12·13(로그인 설정 시) |
| 14·15 | 없음(설치 담당자 확인) | - | - |
| 16 | 해당 코드 없음 | - | - |

현장 점검은 실행 중인 Harbor 컨테이너 안에서 돌린다. 조회만 하며 주소와 토큰을 출력하지 않는다.

```
docker exec -i <Harbor 컨테이너> python - < tools/integration/connection_probe.py
```

실제 Fleet 시험은 [실행 연결](execution.md#실제-fleet-계약-시험)의 `--transport-only`다.

2026-11-14 현장 결과: 규칙 1·2·3·4·9 통과. 현장 R은 `port-events`가 없는 판이라 규칙 11은 미충족으로 표시된다. 규칙 15는 같은 날 Harbor 화면 인증서를 공인 인증서로 바꿔 다른 PC에서 검증 오류 없이 열리는 것을 확인했다. Rosy 계정 로그인은 켜기 전이라 규칙 12·13은 확인하지 않았다([남은 단계](../deploy/compose/README.md#rosy-계정-로그인-연결)). Account 로그인 왕복(실제 Account와의 PKCE)은 자동 시험이 아직 없다.

2026-10-29 경계 분리의 코드 소유권과 고정 HTTP·설치 검증은
[완료 기록](validation/2026-10-29-complete-application-boundaries.md)을 따른다.
업무 장부·명령 서비스·화면은 어댑터의 정규화 결과를 소비하며, Fleet wire 검증은
`harbor/fleet_contract.py`와 `execution.py`가 소유한다. 플랫폼 API나 승인 정책 변경은 없다.

사람 로그인·기계 서비스·독립 승인·관측 신뢰의 정본은 [ADR0015](adr/0015-service-identity-and-independent-integration.md)와 [설계27](design/27-independent-integration-and-service-trust.md)이다. Harbor 서버는 전용 service 역할과 고정 principal을 매 API 호출 전에 확인한다. 기존 UI 연결 검사 결과는 실행 권한 캐시가 아니다. 이 보강은 아래의 플랫폼 수용 pin이나 운영 설치 상태를 자동으로 변경하지 않는다.

설정에서 Fleet 주소·신원을 관리하고 실제 신원/ARM 읽기 계약을 확인하는 흐름은 [설계25](design/25-fleet-connection-bridge.md)를 따른다. 설정 성공·조회 API 지원·작업 제안 권한·운영 근거 수용은 별개다. 토큰은 서버 환경에만 보관한다.

업무 운용과 플랫폼 실행의 독립 경계는 [ADR0013](adr/0013-independent-business-execution-and-platform.md)을 따른다. Harbor가 목표·업무 의존 관계·재계획·업무 완료를 소유하며, ROSY가 도메인 중립 작업의 admission·자원·실제 실행을 소유한다. 상시 실행기·업무 근거·ARM 대조의 현재 구현과 남은 수용 조건은 [상세설계24](design/24-business-execution-boundary.md)의 「지속 실행과 복구」와 「검증과 현재 결손」을 따른다.

11/21 시연 기간의 계약 동결·시뮬 owner·관측·승인 자동화는 [ADR 0016](adr/0016-demo-seams-follow-platform-d927.md)과 플랫폼 D-927을 따른다.

Harbor 사용자의 계획 확정은 준비·제출을 한 번에 이어서 수행한다([ADR 0012](adr/0012-user-confirmed-command-dispatch.md)). 제품 목표는 설치된 Fleet 승인 범위 안에서 별도 사람 클릭 없이 admission·실행으로 진행하는 것이다. 현재 ARM 계약은 별도 승인을 요구하며, 이번 앱 변경은 그 정책을 활성화하지 않는다. 아래의 현행 승인 서술을 영구적인 제품 UX 요구로 해석하지 않는다.

페이지/API 인계의 현행 구현과 배포 구분은 [Harbor–ROSY 파이프라인](design/18-harbor-rosy-pipeline.md), 양 끝 SHA와 화면별 검증은 [2026-10-25 기록](validation/2026-10-25-operator-pipeline.md)을 따른다. 새 Fleet ARM 승인 UI는 `/console?view=work&arm_transfer=<encoded mission>`이며 구버전 콘솔에 있다고 가정하지 않는다. wire API 변경은 없고 기존 v1.312 계약을 소비한다.

중립 ARM 경로는 API reference **v1.312**의 ARM 작업 제안·조회와 별도 물리 receipt를 소비한다. [ADR 0006](adr/0006-neutral-arm-transfer-binding.md)의 `ArmTransferBinding`이 Harbor 업무 위치를 설치된 물리 참조로 변환한다. 독립 관측자 인증·측정 제출과 운영자 승인은 Fleet 측에 남는다. [고정 양 끝 SHA 검증](validation/2026-10-24-neutral-arm-http.md)에서 독립 ARM HTTP·UDS와 기존 Cell/TRIP 회귀를 확인했다. 로컬 기본 discovery의 통합 미설정 skip과 명시 실행한 HTTP 결과를 구분한다.

업무 근거는 API reference **v1.330**(D-915)의 `GET …/arm-transfers/jobs/{mission_id}/physical-evidence`와 **v1.334**(D-930)의 `POST …/rechecks`를 소비한다. 단계 근거는 확인된 시도의 원본 물리 근거 바이트에서, 첫 흐름의 최종 완료는 반출 이동 시도의 재관측에서 만든다([physical_evidence.py](../harbor/physical_evidence.py)). 한 Fleet 전체 흐름 검증은 [2026-10-30 기록](validation/2026-10-30-full-flow-one-fleet.md)을 따른다. v1.334는 플랫폼 공개 main에 게시된 뒤 기준 커밋으로 삼는다.

D-856 첫 중립 이송 흐름은 API reference **v1.298**의 `GET /api/fleet/trip-jobs/{mission_id}`와 원본 `request_key` 조회를 요구한다. 기존 TRIP 접수·simulation gate를 유지하며 recipe/cell 없이 수행 사실을 읽는다. 앱 제출 의도·응답 유실 대조는 [실행 연결](execution.md#중립-이송-의도와-수행-조회-d-856-첫-흐름)에 있다. 아래의 기존 Cell 검증 SHA를 새 경로 검증으로 간주하지 않는다. 새 통합은 [고정 양 끝 SHA 검증](validation/2026-10-24-neutral-transport-http.md)으로 확인했다.

D-908 이송 제안은 API reference **v1.329**의 `/api/fleet/trip-proposals`(제안 POST·`request_key` 조회, 시뮬레이션 프로필 전용, 아니면 503)를 소비한다. 서비스 신원이 제안만 하고 승인은 다른 named operator가 한다. 앱 측 규칙은 [이송 제안 경로](execution.md#이송-제안-경로-d-908-api-v1329)에 있다. 시뮬레이션 실제 Fleet HTTP(`--transport-only`) 왕복을 [2026-11-24 기록](validation/2026-11-24-dual-mode-cell-plan-and-outbound-stand.md)에서 확인했다.

ADR 0029의 반입 사건은 R의 `GET /api/fleet/port-events?after=<cursor>&limit=<1..200>`(ROSY D-996)을 소비한다. Harbor는 불투명 cursor를 Fleet 연결마다 저장해 그대로 돌려보내고, 사건 4개를 `harbor/port_events.py`에서 화물별 단조 상태로 바꾼다. 이송 제안에는 선택 필드 `object_ref`(화물 코드)를 싣는다. ROSY `main`에 착지했고 시뮬레이션 실제 Fleet HTTP(`--transport-only`) 왕복을 [2026-11-24 기록](validation/2026-11-24-dual-mode-cell-plan-and-outbound-stand.md)에서 확인했다.

기준 플랫폼: `647223ec39b8d68b5ad724faf7f070e30aa0f283`(공개 main, 2026-11-05), API reference 문서 **v1.361**. R이 물리 근거·재확인을 셀로 중계하는 v1.356을 포함한다. 이 커밋으로 `--full-flow`, `--arm-only`, R을 거친 `--arm-via-r`와 `--full-flow --via-r` 각 5회를 통과했다([2026-11-06 기록](validation/2026-11-06-evidence-through-r.md)). 이후 2026-11-24에는 ROSY `main` `27314fc34ef560d6f00e054ddbdea37045862b14` ↔ Harbor `main` `80dbb62aa96ef26c153a27fef489d1c957b4be43` 사이 `--transport-only` 실제 Fleet HTTP E2E 7/7을 통과했다([2026-11-24 기록](validation/2026-11-24-dual-mode-cell-plan-and-outbound-stand.md)). 이전 기준은 `ed455ea03af48100085c32487ba11a56ce80c085`(v1.355, [2026-11-04 기록](validation/2026-11-04-pinned-full-flow.md))다. 이 기준은 시뮬레이션 Fleet 계약이며, 운영 R의 설치 버전과는 다르다(운영 R은 [경계 점검](validation/2026-11-04-rosy-boundary-audit.md) 참조).

- 앱 모델·계획·배치·수동 후보 제외는 내부 업무 값이다. ROSY wire 타입·실행 원장·관측 원장을 복제하지 않는다.
- 현재 제안/resolve/admit, mission snapshot/events, 취소/정지는 기존 HTTP 계약을 검토하여 사용할 수 있다. `trip-jobs`는 특정 `robot_id`와 현재 simulation profile에 묶인다.
- 후보 풀의 원자적 배정과 항만 공정의 물리 인계·퇴피·내려놓기 근거가 준비되기 전에는 계획을 실행 요청으로 자동 변환하지 않는다. 범용 실행 endpoint나 플랫폼 내부 import로 우회하지 않는다.
- Vault의 `arm_deferred`/`DEFERRED`는 물리 픽업 근거가 아니다. 이 앱은 Vault 내부 코드를 import하거나 바꾸지 않는다.
- 공통 자원 점유·admission은 Fleet 소유다. 앱에서 available인 차량도 Fleet·owner의 검사와 기존 한 대씩 이동 조건을 통과해야 한다. lease 만료·timeout을 점유 해제 근거로 사용하지 않는다.

첫 구현 뒤 D-836/API v1.292의 후보 요청과 기존 공정 compile/proposal/job 조회를 소비하는 어댑터, SQLite 업무 장부를 추가했다. 자세한 경계는 [실행 연결](execution.md)을 따른다. 고정 실제 Fleet 서버와의 HTTP 통합 및 업무 장부의 관측 대조는 [개발 simulation 검증](validation/2026-10-22-business-reconciliation.md)으로 확인했고, 하네스를 Harbor가 소유한 뒤 같은 기준 커밋으로 다시 확인했다([ADR-0003](adr/0003-own-fleet-contract-harness.md), [재검증 기록](validation/2026-10-24-own-harness.md)). 이후 ARM 대조 분리와 고정 HTTP 재검증은 [2026-10-29 기록](validation/2026-10-29-execution-reconciliation.md)을 따른다. 운영 장부·인증된 현장 producer·장치 수용은 이 합성 검증과 구분하며, 필요한 출처·필드·담당 경계는 [물리 관측 수용 조건](observation-acceptance.md)에 있다. 화면의 현재 범위와 실행 설정은 [제품 웹 안내](product-web.md)를 따른다.

`plan_outbound(..., now=...)`는 평가 시각을 명시해 재생과 시험 결과를 같게 만든다. 제안의 `source_revision`은 승인할 관측 기준이며 예약이 아니다. 실제 실행 전 변경된 관측·계획 버전을 다시 대조해야 한다.
