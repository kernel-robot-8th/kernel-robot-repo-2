# Harbor–Fleet–ROSY 협력과 신뢰 상세설계

2026-10-27. 결정 정본: [ADR0013](../adr/0013-independent-business-execution-and-platform.md), [ADR0015](../adr/0015-service-identity-and-independent-integration.md). API는 기존 플랫폼 공개 계약을 소비하며 이 문서는 wire 계약을 새로 정의하지 않는다.

## 각자가 아는 것과 몰라도 되는 것

| 소유자 | 판단·데이터 정본 | 협력 경계 | 몰라도 되는 내부 책임 |
|---|---|---|---|
| Harbor UI/application service | 사람 역할·사용자 결정·확정 범위·감사 | 앱 내부 업무 명령 | Fleet 토큰·ROS·팔 제어 |
| Harbor 업무 실행기 | 목표·단계 의존·재계획·업무 완료 | 앱 소유 제출/대조/취소 port | HTTP 경로·ROSY 클래스·device 재시도 |
| Harbor 연동 어댑터 | 설치 참조 대응과 원본 실행 참조 | Fleet 공개 인증·요청·조회·receipt | Fleet DB·lease 구현·ROS topic |
| Fleet | 서비스 인증·creator scope·admission·claim·실행 원장 | 도메인 중립 요청·상태·결과 | 반출 우선순위·방해 화물·재적재 업무 규칙 |
| World/인증된 producer | 계약별 관측 사실·출처·시각 | 인증된 공개 관측/측정 계약 | Harbor 업무 완료 판단 |
| CORE/장치 owner | 최종 동작·안전·정지·장치 상태 | Fleet/owner의 수용된 장치 계약 | Harbor 화면·고객·업무 순번 |

모두 서로를 전혀 모르는 구조가 아니라 **공개 계약만 알아도 교체 가능한 구조**다. 공유 DB·ORM·Python import·내부 이벤트 버스를 연결 다리로 사용하지 않는다. Fleet은 Harbor에 계획 전체를 요청하거나 사업 규칙을 실행하지 않아도 중립 작업을 수행할 수 있다.

## 인증과 권한의 네 주체

| 주체 | 자격 소유 | 허용되는 판단 | 금지되는 승격 |
|---|---|---|---|
| Harbor 사람 | Harbor 계정·역할·세션 | 업무 결정·확정·현장 확인 | Harbor admin을 Fleet operator로 취급 |
| Harbor 기계 서비스 | Fleet 발급 전용 principal/role, Harbor 비밀 저장 | 지원된 제안·자기 작업 조회/취소 | 사람 쿠키/비밀번호 전달, 자기 승인 |
| Fleet 운영자 | Fleet 별도 로그인·역할 | 현행 정책이 요구하는 독립 승인 | 서비스 토큰을 사람 로그인으로 사용 |
| 독립 관측 producer | 별도 expiring observer 자격 | 계약에 맞는 원본 측정 제출 | app/operator bearer로 물리 근거 생성 |

현재 사람 인증은 각 팀 계정이다. 향후 공통 SSO는 같은 IdP를 신뢰할 수 있지만 서로 다른 audience와 로컬 권한을 유지해야 한다. 지금은 IdP·OAuth client credentials·delegated-user token·자동 logout 연동이 없으며 구현된 것처럼 표시하지 않는다. Fleet은 사용자 확정 이력을 신뢰해 안전/자원 검사를 생략하지 않는다.

## 실제 통신 순서

1. Harbor 사용자가 앱에서 목표·revision을 확정한다. 앱은 역할/CSRF·고정 원본·기존 실행 대응을 확인하고 안정적인 의도/key를 보존한다. UI는 Fleet 비밀을 받지 않는다.
2. Harbor 서버 어댑터가 같은 자격으로 `GET /api/fleet/session`에서 `{principal_id, role}`를 확인한다. 설정 principal과 `service`가 다르면 해당 조회/쓰기 요청을 보내지 않는다. 이 API는 현재 `console_routes.py`가 제공한다. 별도 `/api/fleet/auth/session`의 관리 세션 확장 필드를 필수라고 가정하지 않는다.
3. 기존 `POST /api/fleet/arm-transfers/jobs`에 `request_key, workcell_id, instance_id, object_ref, source, target`를 보낸다. 앱 ID는 앱 내부 매핑이고 object/source/target은 검증된 설치 참조다. Fleet이 인증·creator scope·설치·정책을 다시 검증한다.
4. 현행 ARM은 다른 named operator의 `POST /jobs/{mission_id}/admit`가 필요하다. Harbor 서비스는 이 경로를 호출하지 않는다. admission과 실제 시작·수행 성공은 구분한다.
5. Harbor는 공개 `GET /jobs?request_key=…`, `/jobs/{mission_id}`와 필요 시 `/physical-receipt`로 상태/근거를 받는다. 각 호출에도 신원 확인을 적용하고, 원래 creator·request key·transfer body digest·mission·attempt/generation·receipt를 기존 계약대로 검증한다. 요청 digest와 physical execution-grant digest는 다른 값이다.
6. 독립 producer는 별도 `X-Physical-Observation-Token`으로 원본 측정을 Fleet 계약에 제출한다. Harbor 서비스는 그 자격을 가지지 않는다. Harbor가 사용하는 업무 배치/퇴피 근거 producer의 인증·신선도·회차 결속은 별도로 수용해야 한다.
7. Harbor 실행기가 검증된 물리 결과와 업무 근거를 대조해 다음 단계를 고른다. Fleet이 Harbor 업무 완료를 판정하지 않고 Harbor도 receipt 하나로 전체 목표 완료를 만들지 않는다. 북향 소통은 현재 공개 조회이며 callback 인입 서버가 필요하지 않다.

`jobs`는 모두 `/api/fleet/arm-transfers/jobs` 아래 경로다. 응답 없는 제출은 원래 key로 대조하며 새 key로 재전송하지 않는다. 취소는 `/jobs/{mission_id}/cancel` 요청이며 ACK가 정지·claim 해제를 보장하지 않는다. 오류·불명·관측 부족은 후속 단계 진행을 막는다.

## 설치와 자격 교체

Fleet 담당자가 현재 지원하는 service 발급 도구로 전용 principal/토큰을 발급하고 비밀 관리자 경유로 Harbor 서버에 전달한다. 원문은 URL·브라우저·Django DB·감사·공개 Git/Confluence에 넣지 않는다. Fleet 원장에는 서비스 신원이 남고 Harbor에는 사람 결정과 Command 대응이 남는다. 서로의 계정 DB를 복제하지 않는다.

Harbor 설치 담당자는 HTTPS origin/인증서·허용 주소·principal·서버 토큰·업무 장부·binding을 배포 설정으로 관리한다. TLS 인증서와 DNS를 우회하지 않는다. 검사 결과는 설정+자격 fingerprint에 묶되 실제 호출은 다시 인증한다. 이중 확인은 분산 transaction이 아니며 Fleet의 요청별 검증이 최종이다.

같은 principal의 토큰 회전은 기존 creator scope를 유지하지만 현재 Registry는 변경 뒤 재시작과 원래 실행 대조가 필요하다. principal이나 Fleet origin 교체는 이관이다. 과거 의도를 새 신원으로 다시 제출하거나 DB 값을 덮어쓰지 않는다. 발급·폐기·회전·재시작·원래 요청 조회의 증거를 운영자가 남긴다. 현재 Fleet service 역할에 없는 앱별 scope·만료 정책을 Harbor가 발명하지 않는다.

기존에 사람 operator 자격을 서버 연결에 사용하던 설치는 그대로 rolling upgrade하지 않는다. 강화된 웹 Registry에서는 그 자격의 조회·취소도 거절된다. 기존 버전/원래 Fleet 콘솔에서 원래 실행의 대조·취소/정지·점유와 업무 복구를 완료하고 인계 기록을 보존한 뒤 전용 service로 새 운영을 시작한다. 미해결 실행이 있으면 기존 소유 경로를 유지하고 이관을 보류한다. 사람 principal의 역할을 service로 바꾸거나 Command의 creator/key를 덮어써 해결하지 않는다. 현재 격리된 service 시험을 이 legacy 이관의 운영 수용으로 세지 않는다.

## 구현 상태와 다음 수용

| 항목 | 현재 근거 | 남은 확인 |
|---|---|---|
| Harbor 신원 결속 | 호출 전 expected principal/service 검사와 응답 결속 | 고정 양 끝 최신 실제 HTTP 재검증 |
| Fleet 중립 ARM·service bootstrap | ROSY 로컬 main에 검증된13503545 포함 | 공개 게시/정식 설치 증거 |
| 운영 연결 | 과거221aedf1 운영 기준 조사 기록 | 최신 DNS·자격 발급·설치·원래 요청 대조 |
| 설치 대응/관측 | 논리 맵·크기 원본·검증된 simulation 경로 | production 다칸 binding·인증된 업무 producer |
| 다단계 업무 | 장부·제출/대조와 실행기 설계24 | 지속 실행기/내구 복구·근거 confirm/finish 연결 |
| 실제 장치 | UI/HTTP 합성 검증과 구분 | 장치·현장 수용 |

2026-10-27 점검 시 ROSY PR210은 OPEN/BLOCKED이며 공개 main에 중립 ARM 후보가 반영된 상태로 보지 않는다. 이 표는 시점 기록이고 실제 배포 판단은 최신 서명/설치·호스트 증거로 갱신한다. 상시 실행기와 운영 producer가 없는 상태에서 UI만으로 자동 진행을 켜지 않는다.

## 독립성 수용 기준

- Harbor 업무 순서를 바꿔도 ROSY 소스를 고치지 않는다. ROSY owner 구현을 바꾸어도 공개 계약이 같으면 Harbor 업무 규칙을 고치지 않는다.
- 잘못된 서비스/역할/응답에서 해당 작업 요청0. UI 연결 성공을 캐시해 호출 권한으로 쓰지 않는다. 다른 서비스의 원장 조회·receipt는 Fleet에서도 거절한다.
- 역할/CSRF·service 인증·별도 승인·observer 인증을 각각 검증한다. 동일 principal 자격 교체와 다른 principal 이관을 구분한다.
- timeout·중복 응답·잘못된 body/mission/receipt·취소 ACK·재시작에서 불명을 성공으로 바꾸거나 후속 단계를 열지 않는다.
- Harbor 단독 대역, 실제 Fleet HTTP/합성 장치, 운영 서버 연결, 실물 수용을 각각 기록한다. 미실행·skip을 통과로 세지 않는다.
