# ADR 0006 — 업무 이동을 중립 ARM_TRANSFER에 연결

상태: 채택. 2026-10-24. 실제 Fleet HTTP 통합 검증은 진행 중이다.

ROSY D-856과 API reference v1.312를 따른다. Harbor의 출고·재배치 계획은 화물과 업무 위치를 사용한다. ROSY는 설치된 물체·출발·도착 참조에 대한 로봇 실행과 물리 사실을 소유한다.

`ArmTransferBinding`이 업무 이동과 설치된 물리 참조를 연결한다. 제안 body에는 `request_key`, `workcell_id`, `instance_id`, `object_ref`, `source`, `target`만 보낸다. Harbor의 화물 이름·출고 목적·팔 역할·recipe/cell 문서를 이 body에 넣지 않는다. 기존 `ProcessBinding` 경로는 유지하며 하나의 업무 계획에는 같은 종류의 binding을 사용한다.

Harbor는 제안과 조회만 수행한다. 두 번째 운영자의 승인은 Fleet에 남는다. 독립 관측자가 제출하는 측정값과 자격 증명은 Harbor가 만들거나 보유하지 않는다. 실행 추적도 Fleet이 담당한다. 응답 유실·재시작은 기존 업무 원장의 HOLD 규칙을 유지하며 자동 재제안하지 않는다.

`ACTION_SUCCEEDED`는 업무 완료 근거가 아니다. `GOAL_CONFIRMED` 작업의 별도 물리 receipt를 조회하고, 원래 물리 참조·정책·authority epoch·generation과 대조한 뒤 현재 단계의 실행 확인으로 사용한다. 물리 receipt의 request digest는 전체 grant의 해시다. 제안 body 해시와 같다고 가정하지 않는다.

배치·퇴피 근거의 출처·최신성 검증, 다음 업무 단계 진행, 최종 출고 완료는 계속 Harbor의 `BusinessLedger`가 처리한다. ROSY의 물리 receipt만으로 `COMPLETED`를 기록하지 않는다. 기존 Cell 흐름도 그대로 검증한다.

첫 검증 범위는 가짜 장치를 사용하는 독립 프로세스 HTTP simulation이다. 실물 장치·운영 관측 producer·현장 수용을 의미하지 않는다.
