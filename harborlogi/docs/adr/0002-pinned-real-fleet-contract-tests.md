# 0002 — 고정 소스의 실제 Fleet HTTP 계약 시험

Status: Accepted for implementation, 2026-10-22. 사용자 구현 지시, 플랫폼 D-841의 앱 계약 검증 경계를 따른다. 실물·관측 producer·현장 수용은 아니다.

Harbor 런타임은 기존 `harbor.rosy_http.RosyClient`로 공개 HTTP만 소비한다. 새 서버 구현을 복사하는 대신 시험 도구만 고정 Vault의 기존 `tools/integration/real_fleet.py` 시뮬레이션 조립을 로드한다. 플랫폼·Vault는 전체 SHA와 깨끗한 소스를 확인하고 Harbor 앱 시험은 별도 프로세스에서 URL·임시 합성 자격만 받는다. Vault는 개발 시험 의존성이며 Harbor 배포 의존성이 아니다.

`tests/integration`은 기본 시험에서 명시적 skip이다. `HARBOR_INTEGRATION=1`이면 설정 누락은 실패다. 명시 실행은 실제 compile/proposal/job 조회, 후보 요청의 idempotency·본문 충돌·busy·viewer 거절을 확인한다. 플랫폼·앱·하네스 SHA와 결과를 함께 보존한다. 가짜 장치·합성 producer의 성공은 실물 완료로 승격하지 않는다.

검증: `tests/test_integration_setup.py`는 잘못된 SHA·변경된 소스를 거절한다. 실제 HTTP는 `tests/integration/test_real_fleet_http.py`를 개발 도구로 실행한다. 실제 시험 PC 전달·의존성이 없으면 실행 인프라 blocker로 기록한다. 독립 관측 producer를 Harbor `MoveEvidence`로 변환하는 운영 어댑터는 여전히 후속이다.

2026-10-22 업무 대조 확장: 합성 하네스가 만든 별도 승인자 자격은 시험 actor만 사용한다. 실제 Fleet 목표 확인과 Harbor의 업무 관측은 별개 조건으로 검증한다. 새 장부가 이전 조회를 승계하지 않는지, 잘못된 출처·시도·화물·시각·퇴피 관측을 거절하는지, 별도 하역 전 완료되지 않는지 확인한다. 직접 승인 HTTP는 개발 시험에만 있고 Harbor 런타임의 공개 클라이언트·권한은 바꾸지 않는다. 시험 origin은 숫자형 loopback HTTP로 제한한다. 합성 관측을 인증된 운영 생산자의 증거로 표시하지 않는다.
