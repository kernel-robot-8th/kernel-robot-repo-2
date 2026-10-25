# Harbor–ROSY 실행 인계 파이프라인

[편집 가능한 책임·인계 흐름도](harbor-rosy-pipeline.drawio). 페이지별 사용자 결과는 [17](17-operator-page-flow.md)을 따른다.

2026-10-25. 아래는 실제 producer/consumer를 대조한 구현 기준이다. Harbor의 업무 의미와 Fleet/CORE/owner의 실행·안전 권한은 분리한다. 실제 장치/인증된 업무 근거의 수용은 별도다.

## 기준과 알려진 결손

확인한 중립 ARM producer는 ROSY `bb38fdcdd0140873e4e9d0133662982c1d6a30e0`이며 feature `f4a934657ff72fd3d274860c8e6bb524292f6e77`의 관련 server/protocol/web 경로와 같다. 조사한 ROSY shared main `46f7a88240740095ec0aa89ac27226297406dd65`에는 해당 중립 ARM 경로가 없다. 최신 main이라는 이유로 설치 계약을 같다고 보지 않는다.

기존 Fleet work 화면은 Task/Mission/TRIP/Cell을 읽으며 중립 ARM을 목록에 넣지 않고 다른 admit API를 호출한다. 승인 API의 존재가 승인 UI의 존재를 뜻하지 않는다. 이번 작업은 Fleet 소유 화면에 알려진 ARM mission 조회·독립 승인·수행 상태 표시를 연결한다. 상세 physical receipt 조회·검증은 Harbor 원본 대조가 맡는다. Harbor에 승인 메서드나 Fleet 자격증명을 넘기지 않는다.

기존 API에는 물리 observer와 receipt가 시뮬레이션 프로필로 이미 있다. 없다고 기록한 문서는 바로잡는다. 아직 없는 것은 운영 관측과 Harbor 업무용 배치/관련 팔 퇴피 근거의 인증된 연결, 다칸 설치 binding과 실물 수용이다. `/healthz` 성공은 liveness, `/api/fleet/session`은 인증 신원만 입증하며 중립 ARM/다칸 지원을 입증하지 않는다. 공개 버전/capability 협상 GET은 확인되지 않았다.

## 각 단계의 소유자와 실제 호출

| 단계 | 소유자·동작 | 결과/다음 판단 |
|---|---|---|
| 지시·원본·계획 | Harbor 자체 계정/권한, 불변 지시·관측·크기·계획 | 계획 READY는 실행 허가 아님 |
| 인계 준비 | Harbor durable intent·원래 key·설치 binding 검증 | Command PREPARED; 같은 Order의 원래 실행 우회 금지 |
| ARM 제안 | Harbor proposer: `POST /api/fleet/arm-transfers/jobs` | Fleet PROPOSED. 입력이 같은 key 재요청은 기존 원본, 바뀌면 충돌 |
| 원래 제안 조회 | caller-scoped `GET /api/fleet/arm-transfers/jobs?request_key=...`, 또는 허용된 job ID GET | 정확한 transfer/digest/key/mission/principal 대조 |
| 독립 승인 | Fleet named operator: job `POST /admit` | 본인 제안 self-admit 거절. proposal digest·dispatch generation을 대조. UI가 자동으로 승인하지 않음 |
| 수행·물리 관측 | Fleet/CORE/OMX owner + 별도 observer | PRE_DISPATCH/POST_ACTION 관측은 독립 observer 자격증명이며 Harbor에 주지 않음 |
| 수행/receipt 대조 | Harbor job GET, GOAL_CONFIRMED에서 job `GET /physical-receipt` | ACTION_SUCCEEDED와 물리 폐쇄를 구별. receipt는 전체 grant digest이며 transfer body digest와 같다고 비교하지 않음 |
| 업무 완료 | Harbor 업무 장부 + 인증된 배치/복귀/관련 팔 퇴피 근거 | receipt 하나로 finish하지 않음. 근거가 없으면 HOLD/대조 필요 |
| 취소·복구 | Harbor 이유와 함께 job `POST /cancel`, 원래 key GET으로 대조 | 준비된 physical attempt 전에는 취소409 가능. ACK는 실제 정지/점유 해제 아님. 자동 새 key/재무장 없음 |

TRIP 생성 `POST /api/fleet/trip-jobs`는 명명 운영자가 실행을 시작하는 호출이다. Harbor의 공급 인계 확인·DepartureIntent는 차량/화물/회차·직접 확인의 출발 전제만 저장한다. 실제 TRIP를 접수했다고 표시하거나 서비스 계정으로 사람을 대신하지 않는다. 컨베이어/S 제어를 추가하지 않는다.

## 화면 계약

Harbor `pipeline.py`는 source/plan/command/검증된 fact로만 read model을 만든다. GET에서 네트워크 probe·ledger refresh·DB write를 하지 않는다. stage는 `key,label,owner,status,note,href,action`을 가진다. 제출/승인/수행/물리 근거/업무 완료를 별도로 읽고, 불명·취소 대기는 원래 요청 대조를 주 조작으로 한다. 실제 조회 시각은 CommandFact의 read_at을 사용하며 페이지 렌더 시각으로 바꾸지 않는다.

`fleet_links(origin, mission_id=None, robot_id=None, arm_console_enabled=False)`는 검증된 origin을 대상으로만 링크를 만든다. 기존 일반 링크는 `/console?view=work`, 차량은 `/console?robot=<encoded id>`다. 새 Fleet 구현의 `arm_transfer=<encoded mission>` query만 알려진 ARM 제안을 연다. 이는 기존 source에 없던 새 UI 계약이므로 배포 확인 플래그 없이 승인 화면이라고 약속하지 않는다. flag는 설치 선언이며 API/물리 수용의 증거가 아니다. Harbor는 입장·동작 API 대신 링크로 인계한다.

Fleet ARM 화면은 Fleet 자신의 로그인/세션·권한을 쓴다. 알려진 mission의 GET은 할 수 있지만, 조회·페이지 진입으로 POST하지 않는다. 명시적 독립 승인 버튼은 서버 snapshot의 expected proposal digest/generation만 전송하며 source/target/actor를 사용자가 바꾸게 하지 않는다. stale/self/권한/오프라인 실패는 그대로 표시하고 자동 재전송하지 않는다. 물리 receipt와 Harbor 업무 완료를 합치지 않는다.

PROPOSED job의 `dispatch_generation`은 null일 수 있다. 승인 화면은 기존 `GET /api/fleet/dispatch-control`에서 현재 generation을 읽고, job의 `proposal_digest`와 함께 승인 fence로 전송한다. 그 사이 generation이 바뀌면 서버 충돌을 표시하고 재조회·재검토한다. Harbor가 이 승인 payload를 구성하지 않는다.

`connection_summary()`는 설정 유무·검토 필요·현재 지원 gate와 안전한 링크만 반환한다. token/비밀번호/실제 ledger 경로·원본 binding 내용을 템플릿에 넣지 않는다. 설정 있음·authenticated session·실제 job 대조·물리/업무 근거·현장 수용을 별도 단계로 표시한다. API path/digest는 지원 담당용 접힌 참조이며 일반 업무 버튼 이름을 구현 용어로 만들지 않는다.

## 검증과 종료 기준

페이지별 맥락/권한/만료/상태별 주 조작·기록 scope, 새 Fleet ARM UI의 read-only 진입/self-admit 거절·독립 named operator 승인·고정 digest/generation·receipt 대조를 확인한다. Harbor 시험과 브라우저는 로컬 독립 앱에서, ROSY pytest/브라우저는 커밋 SHA의 공식 원격 test-PC 워크플로로 실행한다. synthetic owner를 실제 장치나 전 업무 완료로 표시하지 않는다. 미실행/skip·계약 부재·현장 미수용을 따로 기록한다.
