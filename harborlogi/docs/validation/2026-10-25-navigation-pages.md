# 네비게이션 실제 페이지 검증

2026-10-25. 기준은 `b1306e9`이며 `feat/harbor-navigation-pages`에서 구현했다. 기존 디자인·연동 변경은 origin/main 통합과 시험 후 로컬 main에 병합했다. 이번 검증은 다음 페이지 구현의 로컬 증거이며 원격 main 게시·GitHub CI·실물 수용과 구분한다.

## 실제 구현

- `/overview`: 장부의 SUBMITTING/HOLD/AWAITING_FINAL_EVIDENCE만 조치 필요로 분류. WAITING 전체를 오류로 만들지 않는다.
- `/work`: 검색·보기·업무 선택. 한 업무의 개요·계획·실행/근거는 직접 URL, 탭, browser 뒤로 가기로 이어진다.
- PC1280 이상은 전역 메뉴200px + 업무 목록280px + 상세. 좁은 화면은 메뉴 drawer와 목록→상세 단일 화면을 사용한다. JS는 생성 CSS에서 breakpoint를 읽는다.
- `/help`는 기존 컨셉 배너와 Harbor/ROSY의 책임을 설명한다. 데이터·권한이 없는 재고·장비·전체 감사 메뉴는 개방하지 않았다.
- `design/tokens.json`의124개 토큰을 `tools/design_tokens.py`로 CSS 생성. 의미별 색상·type·크기·포커스·상태를 실제 페이지에 적용했다.
- 기존 읽기 전용 SQLite와 RosyClient GET 경계는 유지했다. source 없는 담당·관측 시각·장비 상태·업무 완료 근거는 만들지 않았다.

## 실행한 검사

| 검사 | 결과와 한계 |
|---|---|
| 전체 `python -m unittest discover -s tests -v` | Ran105 tests, OK(skipped=1), exit0. 실제 Fleet 선택 suite는 미설정 skip이며 통과로 계산하지 않음 |
| token 생성 검사 | 소스124개 alias·타입·층 방향·값·순환/충돌 거절, committed CSS와 재생성 일치 |
| 새 화면 route 검사 | shell/static GET가 장부 파일을 변경하거나 Fleet를 호출하지 않음. 미지원 route와 쓰기/원격/Host 거절은 유지 |
| Edge headless 브라우저 | 1440×1000,1280×800,768×1024,390×844의 운영 현황→업무→탭·직접 열기·뒤로 가기·필터 복귀·drawer/Escape/포커스 통과 |
| 브라우저 주 흐름 | 4폭 각각 page errors0, request failures0, 가로 넘침0. computed body16px, heading28px, skip link rgb(17,105,116) |
| 오류·빈 데이터 | 조회 실패의 업무 수는 미확인, 검증된 빈 장부는0으로 구별. 실패 후 검색/필터/재시도에서도 빈 장부를 합성하지 않음 |
| 긴/특수 ID | 긴 화물·위치·mission의 줄바꿈과 넘침 검사. `.`, `..`, slash/backslash 업무 ID의 클릭·탭·검색·새로고침이 같은 원본 ID를 유지 |
| wheel 별도 설치 | 소스 밖에서 운영 현황/업무/계획/도움말·tokens CSS·app CSS/JS·아이콘/배너9개 응답이 모두200 |
| 독립 검토 | 코드 검토의 유효한 ID 호환성 finding 수정 확인. 실제 캡처11개 화면 검토는 로컬 조회 범위 ship, blocking finding 없음 |

브라우저 재현 명령은 `python tools/integration/web_browser_check.py --url http://127.0.0.1:8050 --output runs/navigation-review`다. 데모 서버를 먼저 실행한다. 캡처와 `browser-summary.json`은 Git 제외 `harborlogi/runs/navigation-review/`에 둔다. 이 문서의 명령/화면을 실물 관측 근거로 사용하지 않는다.

## 수정한 문제와 검사 범위

업무 메뉴를 열었을 때 제목으로 포커스를 넘기지 않던 문제를 브라우저 검사로 재현하고 수정했다. 독립 코드 검토에서는 유효한 기존 업무 ID의 dot segment·슬래시가 URL 정규화/WSGI path decoding으로 깨지는 문제를 찾았다. 해당 ID는 query key route로 보존하고 탭·검색·새로고침과 서버 query 조회를 검사했다. 일반 ID의 읽기 쉬운 path는 유지했다. 제어 문자는 실제 BusinessLedger.prepare가 거절하므로 generic identifier만 보고 추가한 finding은 철회됐다.

디자인 detector는 served `/assets/*.css`를 파일 경로로 해석하지 못해 기본 검정색/평평한 type 경고를 냈다. 실제 CSS와4폭의 computed 값·렌더를 확인했으며 detector 전체 통과로 기록하지 않는다. 새 `.impeccable/design.json`은 채택 token과 실제 컴포넌트에서 기록했고 합성 tonal ramp를 runtime 원색으로 복사하지 않았다.

화면 검토의 비차단 후속은 매우 긴 화물 ID가 모바일에서 여러 줄의 제목이 되는 경우다. 현재 전체 값이 읽히고 넘침은 없지만 표시명/전체 값 펼침은 후속 개선 대상으로 남긴다.

## 아직 하지 않은 것

현재 소스의 실제 ROSY 서버 명시 통합·GitHub CI·운영자/실제 태블릿 수용·스크린리더/전체 WCAG/실제 zoom200% 검증은 이번 기록의 통과 항목이 아니다. 이전 고정 Fleet HTTP 연동은 [기존 검증](2026-10-25-web-workbench.md)의 소스와 결과로 보존하며 새 UI의 운영 수용으로 확대하지 않는다. 운영 로그인·scope·쓰기·Fleet 승인 UI·인증된 업무 관측·실물 수용도 남아 있다.

CE: 해당 없음 — 수정 원인과 판단은 최종 코드·브라우저/단위 회귀 검사·이 기록에서 복원할 수 있다. 운영 장애나 새 장치 성질을 해결한 작업이 아니다.
