# Fleet 승인·운영 화면과의 교차 앱 인계

상태: **플랫폼 담당과 검토할 설계 요청안**. 이 문서는 Harbor 측 요구와 인계 조건이다. Fleet UI나 새 ROSY 계약이 구현·등록됐다는 뜻이 아니다. 플랫폼 구현·API 확장은 소유 저장소의 ADR/API 절차를 따른다.

## 1. 첫 인계의 범위

Harbor는 승인할 작업의 mission ID를 제공하고 사용자는 별도 Fleet 세션에서 작업을 검토한다. 자격 증명을 URL·clipboard·browser storage로 전달하지 않는다. initial 인계는 설정된 Fleet 콘솔 열기 + ID 복사이며, 검증되지 않은 상세 deep link를 만들지 않는다.

최소 Fleet 화면은 **ID로 작업 열기 → physical 제안 검토 → 승인 → 현재 상태/점유 대조**다. 현재 physical 작업을 모두 나열하는 승인 inbox 계약은 이 설계에서 확인하지 못했다. 처음부터 기존 `/api/fleet/work`가 이 목록을 제공한다고 가정하지 않는다. 목록이 필요하면 권한 범위·조회/페이지/필터 계약을 별도로 검토한다.

## 2. 화면 요청

| ID | 역할과 핵심 작업 | 화면 구성 | 성공/거절 후 |
|---|---|---|---|
| FL-01 작업 열기 | Fleet 운영자가 전달된 ID 대조 | mission ID 입력/참조, 현재 로그인 신원, 환경, 조회 결과 | 미존재/권한 부족을 구별하되 타 scope 내용 노출 없음 |
| FL-02 ARM 승인 검토 | 승인자가 원래 제안과 현재 허가 조건 검토 | 물리 object/source/target, proposer, policy digest, 현재 job 상태, authority epoch/generation, 승인/거절 이유 | 현재 snapshot으로 승인. stale·self principal·미지원·점유 충돌은 거절 |
| FL-03 수행·점유 상세 | 운영자가 수행/불명·자원 점유 대조 | job과 native Action/attempt, owner/capability 상태, claim, stop 상태, physical receipt를 구분 | Action 성공·취소 ACK와 점유 해제를 같은 상태로 그리지 않음 |
| FL-04 정지·복구 인계 | 별도 권한의 운영자가 실제 상황 판단 | 기존 Fleet 안전 경로와 현장 수단, 현재 불명 상태·해결 책임 | 자동 재무장·원장 삭제·새 key 재접수로 복구하지 않음 |

FL-02의 주요 버튼 문구는 **팔 작업 승인**이다. “반출 완료”나 “하역 승인” 같은 Harbor 업무 의미를 넣지 않는다. 승인 이후 독립 물리 관측과 실행 조건이 맞으면 동작이 시작될 수 있음을 알려 준다. 서버가 추가 사용자 시작 클릭을 기다린다는 계약은 없다.

## 3. 현재 계약으로 확보되는 데이터와 부족한 데이터

| 데이터 | 현재 지원 근거 | 설계 주의 |
|---|---|---|
| mission, proposer, request key/body digest, proposal digest, transfer refs, policy digest, job 상태/reason | ARM JobSnapshot | 업무 화물 이름/공정 의미를 필수로 요구하지 않음 |
| 승인 principal, authority epoch/generation | 같은 snapshot | 사람 신원 독립성까지 증명하지 않음 |
| 승인 요청의 expected proposal digest/generation | `POST jobs/{mission_id}/admit` | 화면에서 읽은 기준을 사용하고 서버의 현재 검사에 따름 |
| 별도 physical receipt의 action/attempt·scope·policy/fence·사실 상태 | physical-receipt GET | receipt 전의 action/attempt가 같은 GET에 있다고 가정하지 않음 |
| owner 현재 capability·원본 측정·claim/stop 상세 | Fleet 소유의 기존 서비스/설치 검토 필요 | 이 세부 값 전부가 현재 ARM 외부 JobSnapshot에 있다는 근거 없음. 필요한 UI 조회는 플랫폼에서 설계 |
| 앱 화물 ID·계획·배치/복귀·전체 업무 완료 | Harbor | Fleet에서 추정/복제하지 않고 Harbor의 원래 업무로 돌아가 확인 |

원본 발생 시각을 제공하지 않는 필드에는 “현재 화면 조회 시각”을 별도로 표기한다. 조회 시각을 원본 관측 시각으로 표시하지 않는다. 승인용 정보가 부족하면 숨겨진 기본값으로 승인하지 않는다.

## 4. 시퀀스와 예외

1. Harbor가 원래 principal/key/body로 제안하고 mission ID를 보존한다. UI는 “제안 접수”를 표시한다.
2. 사용자가 Fleet에 자신의 신원으로 접근한다. 운영자가 해당 ID로 현재 job을 읽는다.
3. 승인 화면은 최신 제안 digest와 generation으로 기존 admit 요청을 만든다. stale 기준·proposer 자기 승인·disabled fence는 서버가 거절한다.
4. 응답 유실 시 재로그인/재클릭으로 새 작업을 만들지 않는다. 같은 job의 현재 승인·상태를 먼저 대조한다.
5. Harbor로 돌아오면 원래 key/mission을 조회한다. 브라우저의 “승인 성공” callback이나 URL parameter로 업무 상태를 직접 갱신하지 않는다.
6. native 결과와 physical receipt, Harbor의 배치/복귀/퇴피 근거가 각각 확인되어야 한다. Fleet 승인 화면은 Harbor 업무 완료 버튼이 아니다.

다른 사용자·다른 사이트·이전 environment의 작업을 열면 권한/환경 불일치로 차단한다. 인계 중 Harbor 세션이 끝나도 Fleet 승인 결과를 취소했다고 표시하지 않고 각 앱이 자신의 현재 원장을 다시 읽는다.

## 5. 플랫폼에 전달할 작업 묶음

- FL-01/02의 실제 ARM 조회·승인 UI와 적절한 named operator guard.
- FL-03의 데이터 공급 경로/권한/원본 시각 검토. 필요하다면 계약 변경을 선행하고 API 버전을 임의로 가정하지 않기.
- 사이트·환경·mission ID를 결속하는 최소 deep link 규약 검토. 임의 외부 URL redirect·token query 금지.
- 승인 전후·self principal·서비스 계정·권한 부족·stale generation·owner 불가·응답 유실·취소/점유 불명의 화면/서버 시험.
- 실제로 서로 다른 사람을 요구할 때 Harbor 제안 actor와 Fleet 승인 신원의 대응·감사 정책 확정.

이 검토가 끝날 때까지 Harbor에는 승인 가능한 것처럼 보이는 버튼을 추가하지 않는다. 명령 기능을 제외한 ID 대조·화면 인계부터 구현할 수 있다.
