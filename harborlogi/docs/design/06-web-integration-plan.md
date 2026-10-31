# 브랜드 적용 웹 앱과 ROSY 조회 연동 구현 계획

상태: 사용자 상세설계 승인 후 첫 구현 범위. 기준 `ddd8eaa`. 기존 격리 워크트리의 `feat/harbor-web-integration`에서 진행한다.

**목표:** 기존 Harbor 이미지·테마를 사용하는 작업 목록/상세 웹 앱에서 기존 업무 장부와 원래 ROSY ARM job·receipt를 조회하고 Fleet 콘솔로 인계한다.

**구성:** Python 3.10 표준 WSGI 서버 + 자체 HTML/CSS/JavaScript. 기존 `RosyClient`와 `ArmTransferBinding` 검증을 재사용한다. 플랫폼 import·새 의존성·플랫폼 API 변경은 없다. 이는 S1 및 S2의 로컬 조회 구현이며 S2 운영 인증 수용 완료가 아니다.

**설계:** [화면](02-ui-ux.md), [경계](03-application-and-contracts.md), [Fleet 인계](05-fleet-handoff.md).

## 구현 선택과 제한

- 기존 네이비·청록·오렌지·아이보리와 H 아이콘, GitHub 배너를 재사용한다. 컨셉 이미지 표기를 유지한다. 작업 상세를 중심에 두고 목록을 곁에 둔다.
- 새 웹 framework 없이 현재 표준 라이브러리 기반 앱을 확장한다. 개발 서버는 loopback으로만 연다. LAN 태블릿 공개는 로그인·scope·TLS·배포 경계 검토 뒤 별도 단계다. 화면 자체는 태블릿 폭에서도 검증한다.
- 데모는 명시적 `--demo` 모드이며 합성 데이터임을 표시한다. 실제 모드와 자동 혼합하거나 연결 실패 시 데모로 대체하지 않는다.
- 실제 모드는 기존 SQLite를 `mode=ro`로 읽는다. `BusinessLedger` 생성/refresh/prepare/confirm을 웹 조회에 호출하지 않는다. 파일 생성·복구·마이그레이션을 수행하지 않는다.
- Fleet token은 환경 변수에서 서버만 읽는다. browser에는 허용된 표시 필드만 전달한다. Fleet 콘솔은 사전 설정한 HTTPS origin 또는 loopback origin이며 mission ID와 별도로 연다. 임의 deep link·승인·TRIP·관측 POST는 제공하지 않는다.
- 상세를 열 때 원래 mission/key/binding을 대조한다. receipt는 확정 job에서만 읽고 scope/fence를 검증한다. 불명/거절/연결 실패는 업무 완료와 분리해 표시한다. 조회 시각은 원본 관측 시각이 아니다.

## 작업과 검증

- [x] `tests/test_web.py`: 파일 불변성, constructor 복구 미실행, GET-only Fleet 경로, 잘못된 mission/binding/receipt, 오류 비밀값 비노출, Host/외부 요청/쓰기 차단 시험을 먼저 실패시킨다.
- [x] `harbor/web.py`: 읽기 전용 장부 adapter, 표시 projection, WSGI 경계, CLI를 구현한다. 호출 전후 업무 버전이 달라지면 다시 열기를 요구한다.
- [x] `harbor/web_assets/`: PC/태블릿 작업 목록·상세, 단계 목록, 근거 분리, 연결 오류/빈 장부/데모 상태, Fleet 인계를 구현한다.
- [x] 실제 HTTP를 쓰는 합성 Fleet 서버와 브라우저에서 조회 흐름을 확인한다. 합성 HTTP 검증과 실제 ROSY 서버·장치 수용을 구분한다.
- [x] 전체 Harbor unittest, PC/태블릿 렌더와 포커스·오류·오버플로 검사, 민감정보 검사를 수행한다. 전체 접근성 수용을 뜻하지 않는다.
- [x] `DESIGN.md`, 실행 안내와 [검증 범위](../validation/2026-10-25-web-workbench.md)를 기록한다. 남은 GAP과 실제 운영 수용은 완료로 표시하지 않는다.

## 집중 확인

1. 조회가 SUBMITTING을 HOLD로 바꾸거나 row_version을 증가시키지 않아야 한다.
2. Fleet가 다른 job이나 오래된 receipt를 주면 성공으로 표시하지 않아야 한다.
3. 응답 오류에 token·endpoint·원본 예외가 browser로 나오지 않아야 한다.
4. 긴 ID/한글/빈 장부/연결 실패/768px 태블릿에서도 대상과 다음 행동을 읽을 수 있어야 한다.
5. GOAL_CONFIRMED나 receipt를 업무 COMPLETED로 바꾸지 않아야 한다.
