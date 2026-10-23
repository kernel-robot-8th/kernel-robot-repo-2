# 물리 관측 연동의 수용 조건

Port는 실제 Fleet HTTP를 소비하지만, 물리 관측을 `MoveEvidence`로 변환하는 운영 어댑터는 아직 없다. 이 문서는 그 어댑터의 입력·소유·수용 조건이며, 관측 wire schema나 장치 활성화 계약을 새로 정의하지 않는다.

## 현재 생산자와 소비자의 차이

조사 소스는 Port `2f365ea`, 플랫폼 `6b7dd876289ecb5ffe121eb983a90b36f15c9eba`다. 플랫폼 내부 경로는 근거이며 Port runtime import 대상이 아니다.

| 기존 근거 | 확인할 수 있는 것 | Port에서 대신 주장할 수 없는 것 |
|---|---|---|
| `core_common.observation.ObservationSnapshot`, World `api/observation.py` | 출처·원본 시각·좌표계·보정 revision·근거 참조 | 특정 화물의 도착, 팔 퇴피, 하역 완료. VALID는 물리 사실의 참 판정이 아니다. |
| World `state/projections/arm_state.py:ArmState` | 관측된 관절·그리퍼 값과 rig/calibration revision | 물체 상태. 현재 `object_state`는 `unobserved`만 허용하며 그리퍼 열림만으로 `arms_clear`를 만들 수 없다. |
| `core_common.protocol.cell_goal_evidence.CellGoalEvidence` | `sim_model_pose` 평가와 작업·시도·공정 digest·물체 pose | 운영 카메라 관측·실물 하역. simulation 사실을 실물 증거로 변환하지 않는다. |
| `rosy.execution.site.goal_evidence.GoalEvidence`와 Fleet evidence service | 제한된 object/destination predicate, producer·observation·action/attempt 결속과 그리퍼 OPEN | Port의 팔 전체 퇴피와 최종 하역. Fleet `GOAL_CONFIRMED`만으로 업무 완료를 만들지 않는다. |
| Port `harbor/workflow.py:BusinessLedger.refresh/_validate/confirm/finish` | 최신 Fleet mission/attempt·업무 revision·허용 출처·설정된 freshness(기본 5초)·한 번만 소비한 관측의 대조 | 관측 내용의 센서 진실성 또는 producer 인증. source_id 허용 목록만으로 인증이 성립하지 않는다. |

검토한 계약에는 Port 필수 물리 사실 전체를 제공하는 운영 producer와 인증된 전달 경로가 없다. 일부 필드의 이름을 바꿔 붙이는 어댑터로 이 결손을 해결할 수 없다.

## 책임과 필드 대응

| MoveEvidence 값 | 어댑터가 받아야 하는 근거·검사 | 책임 |
|---|---|---|
| `evidence_id`, `source_id`, `observed_at` | 인증된 producer identity, 원본 observation ID·시각·revision/digest의 보존과 검증. 수신 시각으로 원본 시각을 대체하지 않는다. | 관측 producer·설치 통합 담당 |
| `mission_id`, `attempt_id` | Fleet의 현재 job과 해당 step의 실제 attempt. 재시도·재접수 뒤 과거 관측은 거절한다. | Fleet 실행 장부 + Port 대조 |
| `revision` | 승인된 Port 업무 계획 revision과 원본 관측 revision의 명시적 대응. 서로 다른 revision을 하나로 간주하지 않는다. | Port 업무 모델·어댑터 |
| `cargo_id`, `destination` | 실제 물체 identity와 관측한 목적지 predicate. 설치자가 검증한 업무 ID·공정 ID·좌표/보정 대응이 필요하다. | 관측 producer·설치 통합 담당 |
| `arms_clear` | 관련 팔 모두가 승인된 퇴피 조건을 만족한다는 현재 관측. action 성공·정지·그리퍼 OPEN으로 추정하지 않는다. | 팔 owner의 관측 + 설치된 퇴피 평가 |
| `unloaded` | 최종 목표 화물의 실제 하역 근거. target mission/attempt와 결속돼야 한다. | 하역 관측 producer + Port finish 대조 |

Port는 공개 계약의 인증된 입력을 소비해야 한다. World·CORE 내부 Python/DB/ROS를 import하는 구현이나 앱이 producer credentials를 임의 생성하는 구현은 채택하지 않는다. 최종 장치 명령과 admission 소유권은 기존 owner/Fleet에 남는다.

## 운영 어댑터 구현 전 필요한 산출물

1. 설치 통합 담당이 원본 관측 transport·인증·producer 등록·시계·revision 계약을 확정한다. producer는 화물 identity/도착·관련 팔 퇴피·최종 하역 각각의 독립 근거를 제공해야 한다.
2. Port 담당이 해당 계약의 고정 버전·공개 payload를 업무 ID·계획 revision과 대응시키고, 원본 근거 ID/시각/revision/digest를 보존하는 어댑터를 구현한다. 모호한 값은 HOLD 또는 거절하며 자동 진행시키지 않는다.
3. 계약 시험에서 위조 출처, 누락 필드, 미래/오래된 시각, 재사용 ID, 이전 attempt/revision, 다른 화물/목적지, 팔 퇴피 없음, 하역 없음이 업무 진행·COMPLETED를 만들지 못함을 검증한다. 정상 예도 실제 인증 경로를 통과해야 한다.
4. 실물 담당이 해당 설치의 물체 관측·보정·퇴피·하역을 확인하고 별도 수용 기록을 남긴다. HTTP·simulation 시험은 이 수용을 대신하지 않는다.

현재 상태는 **실제 HTTP 계약 검증 완료, 물리 관측 producer·운영 어댑터·실물 수용 미완료**다. 공유 모듈 통합 완료와 물리 작업 완료를 같은 상태로 표시하지 않는다.
