# 재계획과 취소의 상태 구조

2026-10-27. 결정: [ADR0013](../adr/0013-independent-business-execution-and-platform.md). 상위 계획: [설계31 P3](31-business-executor-implementation-plan.md). 상태: **상세설계(제안)** — 구현 전 확정 사항과 열린 질문을 적는다. 실행기(P1)와 근거 어댑터(P2)는 이미 착지했다.

## 현재 제약

- `Command.order`가 OneToOne이라 주문당 명령이 영구히 하나다. 재계획으로 새 계획을 착수하려면 이전 명령을 지워야 하는데 Command 이력은 삭제 불가다(설계24 지적).
- 취소는 `CANCEL_PENDING`에 머문다. 종착 상태가 없어 "취소 완료"를 기록할 자리가 없다.
- 불명(`UNKNOWN`)·원래 key 대조·backoff는 P1 실행기가 이미 처리하고 이 설계가 바꾸지 않는다.

## 제안 구조

### 1. 주문당 명령 이력

- `Command.order`를 OneToOneField에서 ForeignKey(`related_name='commands'`)로 바꾼다.
- 같은 주문에 **활성** 명령(상태 `DISPATCHING`, `RECONCILED`, `UNKNOWN`, `CANCEL_PENDING`)은 하나로 제한한다: `UniqueConstraint(fields=('order',), condition=Q(state__in=활성집합), name='one_active_command_per_order')`.
- 종착 상태(`COMPLETED`, `BLOCKED`, `CANCELLED`)에 이른 명령은 이력으로 남는다. 역방향 접근 `order.command`를 쓰는 코드는 `order.commands.filter(상태 활성)` 조회로 바꾼다(영향 목록은 구현 커밋에서 점검).

### 2. 취소 종착 상태

- `CANCELLED`를 더한다. **전환은 운영자 확정으로만** 일어난다: `CANCEL_PENDING` 상태에서 운영자가 Fleet 콘솔에서 실제 정지·자원 해제를 사람이 확인한 뒤 Harbor의 종결 조치(`close_cancelled`)로 종착을 기록한다. 실행기·대조가 자동으로 종착을 만들지 않는다.
- 근거(2026-10-27 플랫폼 API reference v1.312 확인): ARM 작업 상태는 `PROPOSED`/`READY`/`RUNNING`/`ACTION_SUCCEEDED`/`GOAL_CONFIRMED`/`HOLD`뿐이고 취소 종착 상태가 없으며, "cancel과 HOLD는 점유를 유지하고 자동 재개되지 않는다"고 명시돼 있다. 취소 ACK는 정지·해제 증명이 아니므로(같은 문서), 앱이 조회 결과만으로 종찰을 만들 근거가 계약에 없다.
- 종결 조치는 취소 요청·사유·확정자를 감사와 함께 남기고, 남은 미제출 단계를 영구히 닫는다. 이미 종착인 명령은 어떤 조치로도 바뀌지 않는다.
- 플랫폼이 나중에 취소 종착·해제 근거를 공개 계약에 추가하면, 그 계약을 소비하는 별도 변경에서 자동 대조 전환을 다시 검토한다(이 설계가 미리 열지 않는다).

### 3. 재계획 흐름

- `prepare_command`의 "기존 주문 실행은 재조정 필요" 거절을 **종착 검사**로 바꾼다: 이전 명령이 종착일 때만 새 계획의 Command를 만든다. 활성 명령이 있으면 거절은 유지된다(이미 제출한 작업은 원래 계획·식별자에 고정).
- 미제출 단계가 남은 활성 명령을 대체하려면 먼저 취소를 종착까지 진행한다. "부분 완료 후 재계획"은 이 순서를 그대로 따른다.
- 새 Command는 이전 명령을 `supersedes`(null 허용 FK)로 참조해 실행 이력을 잇는다. 계획 revision·관측 원본은 Command 생성 시 각각 고정한다(기존 불변 식별 규칙 유지).
- 사용자 확정 범위 밖의 목표 변경은 기존대로 새 확정을 받는다(ADR0012). 자동 재계획 제안(ADR0010)과 실행 착수를 구분한다.

## 수용 시험 (구현의 완료 기준)

1. 1단계만 확인(부분 완료)된 활성 명령에서 다른 계획 착수가 거절된다.
2. 취소 → 운영자가 Fleet 콘솔에서 정지·해제를 확인 → `close_cancelled` 확정 → `CANCELLED` 종착 → 같은 주문의 새 계획 Command 착수(`supersedes` 참조), 이전 식별자·이력 불변.
3. `CANCEL_PENDING`에서 운영자 확정 전에는 종착 전환도 재전송도 없고, `close_cancelled`는 `CANCEL_PENDING` 외의 상태를 거절한다.
4. 종착 명령이 여럿인 주문의 화면·조회가 이력을 보존한다.
5. P1·P2 회귀(3단계 진행·원래 key 복구·근거 결속)가 그대로 통과한다.

## 하지 않는 것

- 병렬 업무·공동 계획 활성화(설계24: 별도 검증 전 비활성).
- 취소 ACK만으로 자원 해제·재무장을 여는 어떤 경로.
- Fleet 계약에 없는 취소 상태 값을 앱이 추측해 종착을 만드는 구현.
