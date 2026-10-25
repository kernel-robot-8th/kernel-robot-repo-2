# 지정된3×3×3 야드 검증

2026-10-25. 구현 소스 `465497b20791f8bace50051935b51fe12ee92395`. [설계](../design/15-configured-yard-grid.md), [맵 정본](../../config/yard-layout.json).

## 결과

기본 논리 맵을9개 칸 Y11~Y33과 칸별3층, 총27개 위치로 정의했다. Y11-L1 등의 주소를 사용하며 1행은 화면 위쪽·1열은 왼쪽·1층은 바닥층이다. 사용자가 기존 설정이 없으므로 작성을 요청한 논리 맵이며 물리 좌표나 로봇 보정을 지정하지 않는다.

설치 기준의 불변 `yard_layout`과 관측을 사용해 재고/반출 계획에서 층별3×3 평면을 표시한다. 칸 ID 철자나 관측 배열 순서가 화면 위치를 결정하지 않는다. 현재 반출 지시와 계획 단계에 연결된 원본 설정을 유지한다. 맵 밖 임시 칸/반출대는27개 위치와 구별한다. 기존 좌표 없는 관측은 목록으로 보존하고 임의 맵으로 바꾸지 않는다.

| 확인 | 결과 / 증거 |
|---|---|
| Django | **90개 통과**, skip0. `python -m django test harbor_web.tests --settings=harbor_web.settings --verbosity 0`; test 환경에서 실행. 로컬 로그 `runs/configured-yard-django-final.log` |
| 도메인 | Ran121, **실행119 통과·skip2**. `python -m unittest discover -s tests -v`; `runs/configured-yard-domain.log`. 실제 Fleet/선택 의존성 검사는 수행하지 않은 skip이며 통과가 아님 |
| 새 검증 | `tests/test_yard_layout.py`:9개 좌표·ID·범위·타입·용량·전체 관측 검증. `harbor_web/tests/test_yard_grid.py`:모든 단계27개 주소·출발/도착 층·원본 누락 거절·보조 위치 중첩 거절·비사전식 좌표 매핑·legacy 보존 |
| 지도 변경 후 이전 계획 | `test_same_source_with_changed_installation_invalidates_original_plan`의 RED 확인 후 GREEN. 같은 출처의 새 관측은 Condition 변경 여부와 관계없이 이전 계획의 표시/제출 gate를 무효화함. 기존 계획의 좌표는 재해석하지 않음 |
| 브라우저 | **1440/1280/768/390 통과**, JS/console/HTTP/요청 오류0·페이지 넘침0. `python tools/integration/product_web_browser_check.py --output runs/configured-yard-release` |
| 브라우저 증거 | `runs/configured-yard-release/run-37e7c4ec4d2a4607be712d0d87e3ffaa/report.json`. 각 층9개, 세 층27개, 키보드 층 전환, 계획 단계 변경 후 선택 층 유지, 목표 반출·재취급 복귀 예상, 원래 관측 불변. JS 없는 조회에서는4개 단계×3개 층을 모두 읽음 |
| 미리보기 시각 확인 | `.impeccable/review/grid-desktop.png`, `grid-layer3.png`, `grid-mobile.png`를 열어 확인. `Y33-L3`의 C7을 실제 브라우저에서 확인. 화물/관측과 example의 이동·도달 조건은 합성 자료로 표시 |
| 자산/패키지 |147개 토큰의 생성 CSS 일치, whitespace 검사 통과, wheel에 `harbor/layout.py`, `harbor_web/yard_view.py`, grid/stack 템플릿 포함 |

독립 코드 검토에서 운영 코드의 좌표/원본 결속/권한/legacy/27개 위치/실행 경계 문제는 발견되지 않았다. 브라우저 검사에서 예상 단계 확인 후 원본 단계로 돌아온 상태를 여전히 예상으로 검사하던 assertion을 찾았다. 예상 단계에서 예상 문구를, 원본 단계에서 원본 문구를 각각 검사하도록 수정한 뒤 전체 브라우저 검사가 통과했다.

## 운영 범위

논리 맵 설정과 웹 검증이다. 실제 칸의 미터 좌표·A/B 도달성·물리 보정·설치 binding·카메라/인증 producer·현장 UAT는 포함하지 않는다. 기존 실행 승인·완료 근거 gate와 source provenance는 유지한다. `config/yard-grid.example.json`은 제한된 합성 이동/도달 예시이며27개 위치 전체의 실제 도달성을 보장하지 않는다. `actual_fleet=false`이며 이번에 실제 Fleet HTTP·로봇·물리 시뮬레이션은 실행하지 않았다.

CE: **해당 없음** — 새 논리 주소/표현 구현과 직접 재현하는 회귀 시험이다. 장치/운영 환경의 비자명한 장애 해결 기록은 아니다.
