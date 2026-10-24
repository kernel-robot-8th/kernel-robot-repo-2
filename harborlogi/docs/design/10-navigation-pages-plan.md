# 운영 현황·업무 네비게이션 실제 페이지 구현

상태: 사용자 구현 요청, 2026-10-25. 기존 디자인·연동 변경은 최신 origin/main을 통합하고 전체 unittest 후 로컬 main에 fast-forward 병합했다. 외부 main push/PR 병합과 구분한다. 현재 작업은 `feat/harbor-navigation-pages`다.

목표는 승인된 디자인 철학과 토큰으로 조치 필요 첫 화면, 업무 목록, 업무 내부 탭, PC/태블릿 네비게이션과 복귀를 구현하고 실행 화면을 보여 주는 것이다. 기존 Fleet GET/읽기 전용 장부 경계는 유지한다.

## 범위와 선택

- `design/tokens.proposed.json`을 `design/tokens.json`으로 승격하고 정적 CSS 생성 도구를 둔다. 현재 화면에서 사용하는 의미별 변수의 정본은 JSON, `tokens.css`는 생성물이다. 기존 이미지 원본은 그대로 둔다.
- `harbor/web.py`: `/overview`, `/work`, 업무/계획/실행 route에서 같은 shell을 제공. 조회 API와 권한 경계는 보존한다.
- `harbor/web_assets/`: 전역 메뉴 운영 현황/업무, 도출 가능한 조치 필요 업무, 목록·검색·보기, 개요/계획/실행·근거의 직접 URL, 뒤로 가기, 좁은 화면 메뉴 drawer. 아직 데이터 계약이 없는 재고·장비·전체 감사 메뉴는 개방하지 않는다.
- 조치 필요는 장부의 SUBMITTING/HOLD/AWAITING_FINAL_EVIDENCE로 판정한다. WAITING 전체를 오류로 분류하거나 없는 Fleet 이유·신선도·담당자·알람을 만들지 않는다.
- 데모를 실제 운영 데이터로 전환하지 않는다. 기존 ROSY job/receipt 조회·mission 복사·Fleet 콘솔 인계는 실행·근거 탭에서 유지한다.
- 런타임 framework/의존성 추가, 인증·업무 쓰기·로봇 제어는 이 화면 구현에 포함하지 않는다.

## 구현·검증 순서

- [x] route·생성 토큰의 유효성/재생성 일치 시험을 먼저 실패시킨다. 조치 분류는 실제 브라우저에서 WAITING 제외와3개 대상 확인.
- [x] 토큰 CSS 생성, shell·운영 현황·업무 목록/상세·탭·복귀를 구현한다.
- [x] 브라우저 검사를 새 흐름으로 갱신하고 PC1440/1280, 태블릿768, 모바일390에서 조치→업무→탭→뒤로 가기·drawer·긴/특수 ID·오류를 검증한다.
- [x] 전체 unittest와 wheel 자산 검사를 수행한다. 기존 실제 Fleet 고정 소스 시험은 새 코드의 증거로 재사용하지 않는다. Fleet 소비 동작을 바꾸지 않았다.
- [x] 실제 렌더를 독립 검토하고 문서·DESIGN.md·sidecar를 갱신한다. 실행 URL과 화면을 제공한다. [검증 기록](../validation/2026-10-25-navigation-pages.md)을 따른다.

실패/빈 데이터 구분, 숫자·원본 시각 미합성, unknown 유지, 키보드 포커스와 뒤로 가기 복구, 불필요한 이중 접수 방지가 검토의 우선 조건이다.
