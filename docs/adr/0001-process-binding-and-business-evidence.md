# 공정 문서 binding과 업무 완료 근거

사용자가 승인한 도메인 중립 플랫폼 구조를 구현한다. 항만 앱은 회수 제안의 각 이동을 명시적으로 설치된 공정 문서 ID/digest와 연결하고, Fleet의 기존 cell-app HTTP 계약으로 compile/propose한다. 공정의 기하·장치 검증은 Fleet/owner가 수행한다. 앱이 임의 공정이나 장치 명령을 생성하는 방법은 채택하지 않는다.

실행 접수·동작 결과·물리 관측·업무 완료를 구분한다. 업무 장부는 SQLite에 요청 내용과 계획 revision, 현재 단계·연결된 mission/attempt, 확인한 근거 ID를 보존한다. 실패·중단·불명 POST 후에는 자동 실행 대신 HOLD로 남기고 원장과 재대조한다. 같은 업무 요청은 중복 실행하지 않고 다른 내용의 같은 키는 거절한다.

각 단계의 완료는 현재 mission/attempt·계획 revision과 일치하고 독립된 관측 출처·관측 시각이 있는 근거를 요구한다. 최종 완료는 목표 S 하역·방해 화물 복구·관련 팔 퇴피까지 요구한다. 이 앱의 근거 값은 내부 판단 입력이며 ROSY 공개 goal-evidence wire 타입이나 신뢰할 수 있는 관측 producer를 새로 정의하지 않는다. 실제 producer의 인증과 설치 검증이 준비되지 않으면 운영 연결은 HOLD다.

Pinky 후보는 수동 운영 제외를 적용해 Fleet에 제시한다. Fleet만 lease/claim으로 배정한다. 기존 simulation profile·named operator·한 대씩 이동·수동 충전 조건을 유지한다. 다른 backend나 범용 workflow 엔진은 이번 구현에 추가하지 않는다.
