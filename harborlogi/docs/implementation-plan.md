# 항만 앱 실행 연결 계획

**Goal:** 반출 계획을 공정 문서 proposal로 연결하고, 업무 장부가 현재 시도와 신선한 관측 근거를 대조하도록 한다.
**Spec:** [앱 ADR](adr/0001-process-binding-and-business-evidence.md), 플랫폼 D-836.
**Execution:** 기존 승인 범위에서 `superpowers:executing-plans`로 순차 구현한다.

1. `tests/test_execution.py`에 binding 누락/변경·이동 순서·단일 단계 제안·접수와 물리 완료 구분·시도/revision/freshness·중복·재시작 시험을 작성하고 RED를 확인한다.
2. `harbor/process.py`: 이동 의미별 exact binding과 공정 문서 요청을 만든다. 누락은 전체 거절이다. 기하를 계산하지 않는다.
3. `harbor/rosy_http.py`: 표준 라이브러리 HTTP로 compile/propose/read/candidate-trip 요청을 소비한다. POST의 자동 retry·admit·rearm은 없다. 오류/불명 결과는 호출자에게 돌려준다.
4. `harbor/workflow.py`: SQLite 업무 장부와 상태 전이. Fleet 원장 복제 없이 현재 proposal ID와 결과 참조를 기록한다. 승인/진행/물리 확인은 명시적 입력이며 자동 관측 생성은 없다.
5. 모든 앱 시험과 합성 HTTP 계약 시험을 실행한다. 실제 Fleet 서버 시험은 플랫폼 원격 결과와 구분한다. 최종 검토를 한 번 수행하고 Important 이상은 실패 재현 시험으로 고친다.

위험: 공정 binding의 물리 의미는 설치자가 검증해야 한다. API 지원·인증된 관측 producer·현장 수용이 없는 상태의 순수/합성 성공은 실제 운용 허가가 아니다.
