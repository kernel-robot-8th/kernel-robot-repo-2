# 반출 업무 작업 공간 검증

2026-10-25. 구현 소스: `ba7b794e7a8fd4274326da6582c38d503680ee97`. [설계](../design/14-outbound-workspace.md), [실행 계획](../superpowers/plans/2026-10-25-outbound-workspace.md).

## 확인한 결과

`/outbound/`에서 지시된 순서와 다음 판단을 보고 업무를 연다. 반출 요청 상세와 고정된 계획 상세가 같은 작업 공간에서 원본 관측, 재취급/반출/복귀, 단계별 예상 배치, 현재 판단, 원래 실행, 완료 조건, 해당 업무의 지시 이력을 제공한다. 기존 입력/계획 POST와 명시적 역할·CSRF·revision 검사는 유지한다. 기존 실행 서비스와 Fleet 계약은 변경하지 않았다.

| 확인 | 결과 | 근거 |
|---|---|---|
| Django 제품 시험 | **84개 통과**, 실패/skip0 | `python -m django test harbor_web.tests --settings=harbor_web.settings --verbosity 0` in test environment; `runs/outbound-workspace-django-final.log` |
| 신규 작업 공간 회귀 | 12개 포함 | `harbor_web/tests/test_workspace.py`: 관측 보존, 3단계 예상, 불명 원래 실행 우선, 과거 지시/새 원본/유효시간, 보유 중 화물 HOLD, 권한, GET 무변경, 감사 격리, 입력 보존/409, receipt와 완료 구분 |
| 기존 도메인 시험 | Ran117, **실행115 통과 / skip2** | `python -m unittest discover -s tests -v`; `runs/outbound-workspace-domain.log`. skip은 실제 Fleet 통합/선택 의존성 검사를 수행하지 않았다는 의미 |
| 제품 브라우저 | **통과** · 1440/1280/768/390 | `python tools/integration/product_web_browser_check.py --output runs/outbound-workspace-release`; `runs/outbound-workspace-release/run-d7b1fdd0881243128d80a093843546b8/report.json` |
| 브라우저 실제 동작 | 키보드 단계 선택, 마지막 예상의 대상 반출/방해 화물 복귀, 원본 불변, 목록 검색/업무 진입, JS 없는 조회 | 위 report의 6개 기능 묶음. 화물/요청/계획 저장, 현장 배정/확인/재배정·권한/409, 측정·비교, 관리 계정 추가, 전체 메뉴/로그아웃도 포함 |
| 화면 품질 | 네 폭에서 페이지 넘침·JS/console/HTTP/리소스 오류0 | 위 report와 desktop/mobile/tablet 캡처를 실제로 열어 확인. 좁은 화면은 판단→야드→계획 입력 순서. anchor는 상단 바 높이를 고려 |
| 토큰/배포 자산 | 기존147개 토큰 생성 일치, whitespace 검사 통과, wheel에 새 모듈/템플릿3개 포함 | `python tools/design_tokens.py --check`, `git diff --check`, wheel archive 검사 |

로컬 합성 미리보기도 별도로 로그인하여 확인했다. A가 C2를 임시 칸으로, B가 C1을 반출대로, A가 C2를 원래 칸으로 옮기는 계획이며, 단계 선택은 실제 장부/관측을 바꾸지 않는다. 이 역할 예시는 실물 도달성·양팔 성능·동시 수행의 검증이 아니다. `.impeccable/review/outbound-workspace-{desktop,expected,tablet,mobile}.png`와 `outbound-queue.png`는 로컬 시각 증거이며 운영 데이터와 함께 공개하지 않는다.

로컬 병합 뒤 실제 미리보기 브라우저에서도 합성 요청의 우선순위를 변경했다. 지시 v1→v2 저장, 기존 계획의 재검토 안내, 새 v2 계획 생성과 기존 v1 원문 보존을 확인했고 최종 캡처를 갱신했다. 병합된 실행 코드·템플릿·도구·토큰은 위 구현 소스와 동일함을 Git diff로 확인했다.

## 독립 검토에서 수정한 사항

1. 다른 계획을 열 때 완료 조건의 팔·재취급 화물이 표시 중 계획에서 유래하던 문제. 실제로 서로 다른 A/C2와 B/C3 계획으로 실패를 재현하고, 원래 Command의 Plan에서 완료 대상을 가져오도록 수정했다. 실행 영역에도 그 계획을 명시한다.
2. 다른 요청의 변경이나 새 관측 때문에 무효가 된 계획이 반출 목록에서 ‘검토됨’으로 남던 문제. 목록·첫 화면·상세가 같은 `plan_changes()`를 사용한다. 원래 실행이 불명인 경우에는 그 실행 대조를 우선한다.

두 회귀 시험의 RED를 확인한 뒤 수정했고 GREEN 및 전체 Django 시험을 확인했다. 독립 재검토에서 두 Important 해결 및 새로운 중대 회귀 없음, Ship 판정을 받았다. 최종 브라우저 검사는 그 후 통과했다.

## 운영에 남은 계약과 수용

이 기록은 반출 업무 웹과 지원된 인계 경계의 검증이다. 전체 UR/SR 수용이나 운영 실행 완성이 아니다.

- 관측 producer/World 연동: 인증된 현재 재고·카메라 출처/시각/revision·설치 기준의 전달이 필요하다. 수동 등록을 인증 관측으로 승격하지 않는다.
- 플랫폼 계약/설치 담당: 모든 이동 단계의 검증된 설치 binding, 실제 compile/도달 검증, Fleet 승인 신원과 연결이 필요하다.
- 업무 근거 producer/Harbor: 대상 반출대 배치·재취급 복귀·관련 팔의 측정 위치 및 claim 해제를 원래 mission/attempt와 대조하는 실제 연결이 필요하다. 현재 웹은 이를 확인한 척하거나 다음 단계를 강제로 열지 않는다.
- 입고: 실제 입고 planner와 회차/관측 연결, Fleet named-operator 이송 인계 계약이 남아 있다. 반출 planner로 입고를 대신하지 않는다.
- 설치/운영 담당: LAN/TLS·실제 계정 역할·현장 UAT와 장치 검증이 필요하다.

이번에 실제 Fleet HTTP·로봇·카메라·물리 시뮬레이션은 다시 실행하지 않았다. 브라우저 report의 `actual_fleet=false`를 유지한다. 기존 단일 ARM HTTP 검증 기록을 전체 다단계 실행이나 현장 수용으로 확장하지 않는다.

CE: **해당 없음** — 독립 코드 검토로 찾은 표시/원본 결속 문제는 코드와 회귀 시험이 직접 설명한다. 장치·운영 장애나 문서에 없는 환경 성질을 해결한 기록은 아니다.
