# 플랫폼 연결 기준

기준 플랫폼: `584251615d5609a09ebcb52e18af0cbc0c9beaec`, API reference 문서 **v1.292**(후보 운송 요청 포함). 이는 실제 서버 연결 또는 버전 협상을 확인했다는 뜻이 아니다. 정본은 플랫폼 `docs/reference/application-contract-support.md`와 해당 route/schema다.

- 앱 모델·계획·배치·수동 후보 제외는 내부 업무 값이다. ROSY wire 타입·실행 원장·관측 원장을 복제하지 않는다.
- 현재 제안/resolve/admit, mission snapshot/events, 취소/정지는 기존 HTTP 계약을 검토하여 사용할 수 있다. `trip-jobs`는 특정 `robot_id`와 현재 simulation profile에 묶인다.
- 후보 풀의 원자적 배정과 항만 공정의 물리 인계·퇴피·내려놓기 근거가 준비되기 전에는 계획을 실행 요청으로 자동 변환하지 않는다. 범용 실행 endpoint나 플랫폼 내부 import로 우회하지 않는다.
- Vault의 `arm_deferred`/`DEFERRED`는 물리 픽업 근거가 아니다. 이 앱은 Vault 내부 코드를 import하거나 바꾸지 않는다.
- 공통 자원 점유·admission은 Fleet 소유다. 앱에서 available인 차량도 Fleet·owner의 검사와 기존 한 대씩 이동 조건을 통과해야 한다. lease 만료·timeout을 점유 해제 근거로 사용하지 않는다.

첫 구현 뒤 D-836/API v1.292의 후보 요청과 기존 공정 compile/proposal/job 조회를 소비하는 어댑터, SQLite 업무 장부를 추가했다. 자세한 경계는 [실행 연결](execution.md)을 따른다. 고정 실제 Fleet 서버와의 HTTP 통합 및 업무 장부의 관측 대조는 [개발 simulation 검증](validation/2026-10-22-business-reconciliation.md)으로 확인했고, 하네스를 Harbor가 소유한 뒤 같은 기준 커밋으로 다시 확인했다([ADR-0003](adr/0003-own-fleet-contract-harness.md), [재검증 기록](validation/2026-10-24-own-harness.md)). 운영 장부·인증된 관측 producer 연결·장치 수용은 후속이며, 필요한 출처·필드·담당 경계는 [물리 관측 수용 조건](observation-acceptance.md)에 있다. UI·최적화·자동 충전·진행 중 조작 takeover는 구현하지 않았다.

`plan_outbound(..., now=...)`는 평가 시각을 명시해 재생과 시험 결과를 같게 만든다. 제안의 `source_revision`은 승인할 관측 기준이며 예약이 아니다. 실제 실행 전 변경된 관측·계획 버전을 다시 대조해야 한다.
