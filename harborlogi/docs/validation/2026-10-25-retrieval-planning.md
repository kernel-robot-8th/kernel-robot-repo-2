# 재고·반출 계획 페이지 검증

2026-10-25, 기준 `4440b0f`, 구현 브랜치 `feat/harbor-retrieval-planning`. 사용자 요청에 따라 제품의 결과·입력·완료 조건과 현재 기능을 대조했다. 팀 요구사항 정본의 버전·ID·상태와 전체 추적 표는 비공개 작업 기록에 보존했고 원문을 공개 저장소에 복사하지 않았다. 정본 편집·우선순위 변경·전체 요구사항 수용 판정은 하지 않았다.

## 구현 범위

`/planning`의 합성 단일 반출 계획 검토다. 기존 `plan_outbound`를 서버에서 호출하고, `YardSnapshot`의 관측 배치와 별도 계산한 예상 배치를 분리한다. 재취급/반출/복귀와 주/대체 팔은 계획상의 역할이다. 고정 합성 시각·source·revision·합성 도달 표를 표시하며 실제 관측이나 실행 허가로 다루지 않는다.

`GET /api/planning`은 demo에서만 계산한다. 실제 모드는 재고 source가 없으므로503이며 메뉴·합성 입력을 숨긴다. 직접 열기·연결 실패에도 합성 화물이나 성공 계획을 대체 제공하지 않는다. POST·외부 Host/원격 요청은 기존 경계에서 차단한다. 기존 업무 장부와 Fleet 클라이언트에는 쓰기를 추가하지 않았다.

## 확인한 결과

- 전체 unittest: `python -m unittest discover -s tests -v`, Ran108 tests, OK(skipped=1), exit0. 실제 Fleet 선택 suite skip은 통과가 아니다.
- 새 adapter/API 시험3개: 실제 planner의3단계 및 단일 반출, 결정성·원래 관측 보존, 공간/관측 HOLD의 무부분계획·무예상완료, 미관측 대상, invalid/blank/duplicate query400, live503·무fallback, POST405·remote403.
- 브라우저: Edge headless, 1440×1000/1280×800/768×1024/390×844. 기존 탐색·탭·복귀·필터·특수 ID 회귀와 계획 페이지를 함께 검증했다. 네 폭의 주 흐름에서 page errors0, request failures0, 가로 넘침0.
- C1을 고르면 재취급C2→반출C1→복귀C2의3개 계획 이동, 위의 C2를 고르면 반출1개가 계산됐다. 원래 관측은 바뀌지 않고 예상 결과만 달라졌다.
- 임시 공간 부족과 오래된 합성 관측은 각각 HOLD와 구체 이유를 표시하며 이동/예상 배치를 비웠다. 입력 변경 시 이전 결과를 즉시 숨겼다. 빈 입력은 C1로 바꾸지 않고 오류로 유지했다.
- live source 없음 모사에서는 메뉴·form·결과가 숨겨지고 관측 칸을 만들지 않았다. 실패는 빈 재고로 표시하지 않았다.
- 성공 이후 다른 입력으로 계산하다가503이 발생해도 제출한 cargo/scenario를 URL에 먼저 보존하여 새로고침에서 같은 입력을 복구했다. 브라우저에서 실패를 재현한 뒤 수정하고 회귀 검사를 추가했다.
- 독립 코드 검토: 관측 불변·HOLD·live 차단·generation guard에 blocking finding 없음. URL 입력 보존의 지적은 해소 확인.
- 독립 시각 검토: 합성 preview 범위 ship. 모바일 HOLD 이유가 아래에 있던 배치를 결과 요약→관측/예상→이동 순서로 보강했고, 재캡처에서 해당 항목 해소를 확인했다. 전체 요구사항 수용이나 실물 검증의 판정은 아니다.
- 별도 wheel 설치 후 소스 밖에서 실제 `/api/planning` adapter가 HTTP200, READY, REHANDLE/OUTBOUND/RESTORE를 반환했다. 새 모듈과 기존 생성 CSS의 배포 포함을 확인했다.

브라우저 재현: `python tools/integration/web_browser_check.py --url http://127.0.0.1:8050 --output runs/retrieval-planning-review`. 먼저 `python -m harbor.web --demo --port 8050`을 실행한다. 이 세션의 같은 코드8051 서버에서도 검증했다. 캡처와 summary는 Git 제외 `harborlogi/runs/retrieval-planning-review/`에 둔다.

## 남은 기능과 수용

출고 지시/우선순위·동순위 정책 접수/집행, 화물 기본 정보 등록, 실제 재고 관측 adapter, 입고/적층 전체 흐름, 승인·중단·복구·전 구간 이력, 비교 계측은 이번 구현의 완료 항목이 아니다. 이 작은 합성 도달 배치는 해법 비교나 실제 양팔 효과의 근거가 아니다. 실제 추가 이송·사람 개입·재취급 횟수·처리 시간·개선율을 생성하지 않았다.

합성5초 유효기간과 고정 시각은 fixture 조건이며 운영 SLA나 실제 카메라 최신성을 뜻하지 않는다. 운영 인증·LAN 태블릿·실제 ROSY/장치 명시 재검증·실물 수용은 별도다. 이전 고정 Fleet HTTP 근거는 그 소스의 검증이며 새 화면의 전체 수용으로 확대하지 않는다.

CE: 해당 없음 — 판단과 URL 보존 수정은 최종 코드·회귀 검사·이 기록으로 복원할 수 있다. 운영 장애나 새 장치 성질을 해결한 작업이 아니다.
