# ADR 0033: ROSY 출발 허락을 기본으로, 사람 현장 비움 확인을 대비책으로 쓴다

날짜: 2026-11-20

상태: 제안.

대체: [ADR 0029: Harbor는 반입 화물을 ROSY 항만 사건 4개로 받는다](0029-port-events-and-intake-identity.md)의 결과 절 중 「release_permitted로만 출발 허용」 부분. 다른 결정과 Fleet 승인 경계는 유지한다.

## 배경

ROSY `vehicle.release_permitted`는 이미 장부에 저장되지만, 웹 출발 준비는 사람 CLEAR 확인만 사용했다. ROSY 사건을 기본 허락으로 연결하고, 사건이 안 오거나 다르거나 확인 못 할 때 사람 확인으로 대비한다.

## 결정

1. 기본 출발 허락은 해당 차량의 ROSY `vehicle.release_permitted`다. `DepartureLedger.authorize`가 아직 쓰지 않은 허락 중 최신 하나를 소비한다. 사람 현장 비움 확인(CLEAR) 경로는 언제나 비상 대비책으로 열어 둔다. 기존 확인자·회차·버전·5분 신선도 검증과 멱등성은 유지한다.
2. `since`는 해당 차량의 현장 회차(`FieldEpisode.created_at`) 생성 시각이다. 이전 방문에서 남은 허락을 쓰지 않기 위한 임시 기준이다. ROSY가 차량별 도착 사건을 제공하면 그 시각으로 바꾼다. **새 계약 필요(ADR 대상)**이며 이 결정에서 API 경로나 필드를 만들지 않는다.
3. `intake_loop.tick`은 항만 사건 폴링 뒤 **차량별 최신 회차**(비`REASSIGNED` 회차 중 pk 최대) 하나만 자동 준비 대상으로 삼는다. 그 회차가 이미 준비됐거나 끝난 상태면 그 차량을 건너뛰고, 옛 회차에는 허락을 자동 귀속하지 않는다. 옛 회차는 사람 경로로만 준비한다. `REASSIGNED`는 닫힌 이전 회차라 제외한다. `OPEN`, `CONFIRMED`, `MISMATCH`, `UNABLE`은 ROSY 허락으로 준비할 수 있다. 장부 경로가 없거나 쓸 수 있는 허락이 없으면 자동 경로는 아무것도 하지 않는다. 기존 목적지 설정이 없으면 틱도 기존처럼 동작하지 않는다.
4. 출발 준비에는 `confirmation` 또는 `release_event_id` 중 정확히 하나만 저장한다. 데이터베이스 CheckConstraint로 둘 다 비거나 둘 다 찬 기록을 거절한다. 허락 소비와 출발 준비 저장을 먼저 마친 뒤 별도 savepoint 안에서 기존 `prepare_trip`을 호출한다. 이송 준비 실패는 로그를 남기고 출발 준비를 유지하며, 다음 틱이 같은 키로 멱등 재시도한다. 회차별 오류도 로그를 남기고 다른 차량 처리를 계속한다. 자동 준비의 작성자·actor와 감사 actor는 모두 시스템(`null`)으로 기록한다.
5. 감사 action은 두 경로 모두 `departure.prepare`다. detail은 자동 경로에서 `authorized_by: rosy_release`, `release_event_id`, 사람 경로에서 `authorized_by: field_confirmation`, `confirmation_id`를 담는다.
6. 자동으로 준비된 회차는 「ROSY 허락으로 출발 준비됨」으로 표시한다. 자동 준비된 회차의 사람 출발 버튼은 숨긴다. 이전 화면에서 사람이 출발 준비를 요청하면 기존 ROSY intent를 반환하고 「ROSY 허락으로 이미 출발 준비됨」을 표시한다. Fleet API 필드는 추가하지 않고 실제 동작 승인 권한도 바꾸지 않는다.

## 결과

- ROSY 허락이 오면 사람 확인 없이 출발 준비와 기존 이송 준비가 기록된다. 다음 틱은 이미 준비된 회차를 다시 만들지 않는다.
- ROSY 허락이 없으면 현장 담당자의 유효한 CLEAR 확인으로 관제 담당자가 기존처럼 준비할 수 있다.
- 허락 SQLite 장부와 웹 데이터베이스는 별도 트랜잭션이다. 허락 소비 직후 프로세스가 중단되거나 웹 저장이 실패하면 자동 재사용하지 않으며, 사람 CLEAR 대비 경로로 복구한다. intent 저장 이후 이송 준비 실패는 다음 틱에서 자동 재시도한다. 두 저장소의 원자적 복구는 이 결정에 포함하지 않는다.
