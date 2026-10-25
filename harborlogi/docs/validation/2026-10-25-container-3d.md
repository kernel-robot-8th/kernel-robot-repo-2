# 컨테이너3D 배치·계획 순서 재생 검증

2026-10-25. 코드 `a0451433250e8dc5268232d88bd31d358216971f`. [설계](../design/19-container-3d-view.md). 기존 stage의 원본·예상 배치를 Canvas의 입체 박스로 투영한다. 서버/도메인/ROSY의 실행 상태와 권한은 변경하지 않았다.

## 확인한 동작

- 행·열·층·colspan/rowspan·원본 크기 revision을 사용한다.1×1·2×1·3×1은 각각 박스 하나이며90도 방향도 원본을 보존한다. 지도 없는 과거 원본은3D를 합성하지 않는다.
- 기본3D 보기에서 드래그/방향키 회전, 확대·축소, 정면/위에서/입체 보기, 박스의 투영 면 클릭과 native 선택 목록으로 ID·크기·모든 점유 주소를 확인한다. 가려진 박스도 목록에서 선택할 수 있다.
- 명시한 계획 재생이 기존 단계 버튼과 동기화돼 재취급→반출→복귀를 설명한다. 들어올리기/수평 이동/내려놓기는 실제 로봇 궤적이 아니다. 자동 시작하지 않으며 재생 중지와 수동 단계 변경을 확인했다. 중지는 선택 단계의 예상 배치로 정지한다.
- 단계별 평면도로 전환할 수 있고 JS/Canvas 미지원 때 원래 평면도를 유지한다. 동작 줄이기에서는 보간 없이 해당 단계로 바뀐다. 선택/카메라/재생은 DB·Fleet 요청·업무 완료를 만들지 않는다.

## 실제 검사

| 검사 | 결과 |
|---|---|
| Django 전체 |131개 통과. 신규 scene5개에 다칸 정체성·방향·주소·보조 구역·모든 frame·deepcopy·구형 fallback·DB payload 불변 포함 |
| 기존 도메인 |131수집,129실행 통과/2통합skip. 이 skip을 실제 Fleet 실행으로 표시하지 않음 |
| JS 수학/선택 |`node tests/yard-3d.test.cjs` 통과. 회전 투영·z축·직사각형 판별·고정 footprint·lift/travel/lower·0/1 endpoint 검증 |
|3D 브라우저 |`multicell_browser_check.py --output runs/container-3d-acceptance`:1440/1280/768/390 모두 통과.6개 화물·박스 면 클릭·선택 상세·키보드 회전·전체 재생·중지·수동 중단·평면도·source 불변·90도/HOLD·noJS·reduced-motion·Canvas fallback 확인. 오류/넘침0 |
| 기존 제품 브라우저 |`product_web_browser_check.py --output runs/container-3d-product-browser`:같은4개 폭 통과. 기본3D→평면 전환, 기존 현장/계정/요청/측정·권한·복귀 흐름, JS/console/HTTP/실패 요청/넘침0 |
| 자산/정적 검사 |Node 구문 검사,147개 토큰 CSS 일치, migration drift 없음, `git diff --check` 통과 |

원시 브라우저 report는 Git 제외 `runs/container-3d-acceptance/run-99dbc02f54cd44d193ab8fc71b63537f/report.json`, `runs/container-3d-product-browser/run-69ac6b7d6c9b4bac874619829bc8a792/report.json`이다. 최종 PC/모바일 캡처를 직접 열어 확인했다. 첫 캡처의 과도한 여백을 고쳐 현재 박스와 이동 중 박스에 맞춰 framing하고 면에 ID를 표시했다. 프레임 데이터 자체는 바꾸지 않았다.

초기 시험의 string형 Playwright 대기는 엄격 CSP의 unsafe-eval 차단에 걸렸다. 함수형 predicate로 고치고 CSP를 완화하지 않았다. 새 기본3D 때문에 기존 평면 검사는 명시적으로 평면 전환을 한 뒤 실행한다. 기존 검사를 삭제하지 않았다. 프런트엔드 독립 검토는 원본/방향 보존, 선택·단계 동기화, 재생 중단·reduced motion·fallback·서버 쓰기 없음에서 차단 문제를 찾지 못했다.

## 범위

합성 논리 배치의 조회·계획 설명이다. 보조 구역의 표시 간격·박스 면의 음영·들어 올리는 높이는 화면 표현이며 물리 좌표/충돌/도달/실제 팔 경로를 수용한 것이 아니다. 실물·ROSY HTTP/장치 시험은 이번에 실행하지 않았다. 다칸 실행 binding과 인증된 관측/업무 근거 gate는 유지한다.

CE: **해당 없음** — 원인과 예방을 scene serializer·투영 함수·실제 브라우저 회귀에서 확인할 수 있고 별도 숨은 장치/운영 지식은 발견하지 않았다.
