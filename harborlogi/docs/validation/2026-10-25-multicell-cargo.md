# 다칸 점유 화물 검증

2026-10-25. 구현 소스 `0dde604b097947e34dc17ec147d815f0f4b3b14e`. [ADR0008](../adr/0008-multicell-cargo-occupancy.md), [상세 계약](../design/16-multicell-cargo.md), [실행 계획](../superpowers/plans/2026-10-25-multicell-cargo.md). 사용자 지시에 따라 Django는 유지했다.

## 요구사항과 설계 정본

팀 비공개 Confluence의 UR v14→15, SR v15→16, 상세 설계 v16→17에 다칸 점유·전체 지지·원자적 개체/취급 집계·유효한 복귀·Django 유지를 반영했다. 기존18UR/36SR/13시험의 ID·상태·우선순위·표 열·매크로·이미지/앵커·상위 위치/첨부/메타데이터를 보존했다. UR↔SR 양방향과 SR↔시험/UR 시험 합집합을 저장 후 재조회로 확인했다. 요구사항 전체를 채택/실행/수용 완료로 올리지 않았다.

원문·전후 storage와 버전·변경 검증은 별도 비공개 작업 기록에 보존했으며 공개 저장소로 복사하지 않는다. Confluence 실제 화면 검사는 하지 않았고 이 확인은 storage 재조회 검증이다. 공개 앱의 기능·입력·실행 gate 정본은 위 설계/ADR/코드다.

## 구현과 확인한 결과

| 확인 | 결과 / 근거 |
|---|---|
| Django 전체 | **107개 통과**, skip0. test 환경 `python -m django test harbor_web.tests --settings=harbor_web.settings --verbosity 0`; `runs/multicell-django-release.log`. 이후 구역별 화물/점유 수 보존 assertion을 더해 해당3개 view 시험도 통과 |
| 도메인 전체 | Ran131, **실행129 통과·skip2**. `python -m unittest discover -s tests -v`; `runs/multicell-domain-full.log`. 실제 Fleet/선택 의존성 시험은 수행하지 않은 skip |
| 도메인 핵심 | `tests/test_spatial.py` 10개: 여러 칸에 걸친 상단/전이 의존 화물 중복 제거, 전체 footprint의 도달 참조, 크기·방향·경계·겹침·전체 지지, 유효 복귀/너무 작은 공간/탐색 한도, 부분 이동 없는 HOLD와 원본 불변 |
| 데이터와 이력 | `test_multicell_services.py`: create-only migration, 과거 Cargo 보존·크기 무추정, 불변 footprint revision과 actor/audit/권한, 전체 observed_areas, 화물별 크기 원본 결속, 좌표·겹침·지지 거절, 고정된 source_shapes/projected_placements, legacy 다칸 보류와 실행 차단 |
| Django UI | `test_multicell_view.py`: 한 화물의 colspan/rowspan, 전체 주소 범위, 구역별 화물/점유 수, 원본 보존, 크기 변경/조회/권한, 입고 HOLD 원본 지도 보존 |
| 기존 제품 브라우저 | **1440/1280/768/390 통과**, JS/console/HTTP/리소스 오류0·페이지 넘침0. `runs/multicell-legacy-browser/run-7ad58d0681384d35b58158567fabc779/report.json`; 기존 화물/요청/계획/현장/관리/비교/권한·복귀·로그아웃 흐름 유지 |
| 새 공간 브라우저 | **1440/1280/768/390 통과**, 오류0·넘침0. `python tools/integration/multicell_browser_check.py --output runs/multicell-release`; `runs/multicell-release/run-fac80051369642e78c575c0d2280484e/report.json` |
| 공간 브라우저 실제 동작 | 브라우저 계획 생성,2×1과3×1을 한 개체의 이어진 면으로 표시, C2→C1→C2의3회 화물 이동, 전체 점유 영역의 반출/복귀 예상, 구역별 독립 층 선택·단계 변경 후 유지, JS 없는 조회, 재고의 동일 원본 표현, 새 화물 크기 입력/불변 크기 변경, 원래 관측 불변, 실행 intent 없음 |
|90도와 fit HOLD| 같은 브라우저 검사에서 명시한90도2×1 원본을 단일 rowspan으로 표시.1행×3열 반출대에 그 방향 그대로는 들어가지 않아 HOLD하며 부분 moves/예상 배치를 만들지 않음 |
| 자산·패키지 |147개 토큰 생성 CSS 일치, migration drift 없음, whitespace 검사 통과. wheel에 공간 도메인/0009 migration/크기 입력·지도 템플릿 포함 |

## 독립 검토와 미리보기

도메인과 데이터 과업은 각각 독립 spec/quality 검토를 받았다. 도메인10개, 데이터/명령33개 시험을 검토자가 별도로 실행했다. 전체 통합 검토는 Ship이며, 우선순위가 낮은 입고 HOLD 계획의 공간 원본 지도 누락을 추가로 발견했다. 해당 문제의 RED를 확인한 뒤 공통 공간 표현으로 수정하고107개 Django 시험의 GREEN과 독립 재검토를 확인했다. 입고 planner를 구현한 것으로 표시하지 않는다.

기존 개발 미리보기 DB를 SQLite backup으로 보존한 뒤 create-only migration을 적용했다. 실제 운영 DB/장치에는 접근하지 않았다. 기존 계정·화물·요청·관측·계획은 보존했고, 명시적으로 합성인 미리보기의 크기 선언/새 관측/새 계획만 추가했다. `.impeccable/review/multicell-{desktop,map,expected,mobile}.png`를 캡처했으며 브라우저 검증의 PC/모바일 원본도 실제로 열어 확인했다.

## 범위와 남은 계약

현재 논리 구현은 화물 높이1격자와 원본에 명시한0/90도 방향을 보존한다. 자동 회전, 실제 하중/안정성, 물리 좌표/도달 검증을 추가하지 않았다. 탐색은20,000회 한도이며 한도 도달은 공간 없음과 구별한다.

새 공간 계획의 실제 실행은 `SPATIAL_EXECUTION_BINDINGS_REQUIRED`로 차단한다. 기존1×1 ARM binding을 크기/방향/전체 위치가 검증된 binding으로 간주하지 않는다. 원래 불명 실행을 새 계획/key로 우회할 수 없고 인증된 관측/완료 producer와 배치·복귀·관련 팔 퇴피의 업무 완료 gate도 유지한다. 기존 단일 ARM HTTP 검증을 다칸/다단계 실제 수용으로 확대하지 않는다. 두 브라우저 report는 `actual_fleet=false`이며 이번에 Fleet HTTP·실제 로봇·카메라·물리 시뮬레이션을 실행하지 않았다.

CE: **해당 없음** — 공간 모델·source 결속·표현과 migration 검증은 코드/회귀 시험으로 직접 설명된다. 실물·운영 환경의 비문서화 장애나 추가 교훈을 해결한 작업은 아니다.
