# Harbor 제품 맥락

<!-- impeccable:product-schema 1 -->

상태: 2026-10-25 기준. 기존 결정과 현재 구현, 이번 UI 설계의 제안을 구분한다. 상세설계는 [설계 문서 지도](docs/design/README.md)를 따른다.

## Platform

web

사용자 확인(2026-10-25): 관제 PC 중심 + 현장 태블릿 보조 브라우저 웹앱, 팀 자체 계정과 역할 권한. 새 제품 웹은 Django 기반 서버 템플릿과 영속 업무 데이터로 구현한다. [제품 웹 실행 안내](docs/product-web.md). 기존 표준 WSGI 조회 preview는 별도로 유지한다. 실제 LAN/TLS 설치와 현장 수용은 후속이다.

## Users

관제에서 항만 업무 계획·진행·예외를 다루고 현장에서 필요한 사실을 확인한다. 사용자 확정(2026-10-25): PC 첫 화면은 조치가 필요한 업무와 막힌 이유를 먼저 보여 준다. 로컬 앱에 운영 현황→업무 목록→개요/계획/실행·근거의 탐색을 적용했다. 실제 인원·계정·겸임 정책은 [역할 설계안](docs/design/01-roles-and-workflows.md)의 미확정 사항이며 담당자를 합성하지 않는다.

## Product Purpose

HarborLogi 프로젝트의 업무 앱 Harbor는 양하 이후 장치와 재취급을 동반한 반출을 계획하고 업무 완료 근거를 대조한다. ROSY Platform이 공통 실행·자원 배정·장치 owner를 소유한다. 용어 정본은 [CONTEXT.md](CONTEXT.md)다.

화면의 기준은 반출 지시가 바뀌었을 때 목표 화물을 꺼내고 재취급 화물을 정리한 결과다. 입고·화물 식별, 관측 재고, 반출 지시, 계획, 실행 인계, 완료 근거, 이력·비교의 흐름으로 기능을 대조한다. `/outbound/`에서 업무를 열면 야드·단계별 예상 배치·현재 판단·원래 실행·완료 조건·지시 이력을 하나의 작업 공간에서 대조한다. [반출 작업 공간](docs/design/14-outbound-workspace.md)의 구현은 운영 producer나 실물 수용의 완성을 뜻하지 않는다.

현재 논리 야드는 [지정된3×3×3 맵](config/yard-layout.json)으로9개 칸 Y11~Y33, 각3층의27개 위치를 갖는다. 위치 주소는 Y11-L1처럼 칸 ID와 층으로 구별한다. 1행은 화면 위쪽·1열은 왼쪽·1층은 바닥층이다. 화면은 관측에 고정된 설치 기준의 행·열 대응을 따르며 ID나 배열 순서로 배치를 추측하지 않는다. [상세 계약](docs/design/15-configured-yard-grid.md). 물리 좌표·도달 검증은 플랫폼/설치의 별도 책임이다.

## Operating Context

공급암 S는 외부 운영이고 Harbor가 명령하지 않는다. 적층암 A·B는 ROSY 제어 대상이며 작업마다 역할을 정한다. Pinky 이송 차량은 후보 풀에서 고르고 Fleet이 실제 배정한다. 컨베이어는 용량1의 한 방향 상시 동작 구간이다. 근거는 ADR0005와 현재 site/model 코드이며 실제 설치 수용은 별도다.

## Capabilities and Constraints

- Python domain·계획·기존 SQLite 업무 장부·Fleet HTTP client를 유지하고 Django 계정/명시적 역할·화물/요청·관측 원본·불변 계획·실행 intent·현장 회차·감사/비교를 구현한다. 최종 수용은 실제 검증 기록과 남은 계약 게이트를 따른다.
- 저장한 요청 우선순위 그룹/허용 순서와 관측 revision으로 기존 `plan_outbound`를 호출한다. 실제 인증 재고/업무 근거 producer·입고 planner와 모든 단계의 설치 binding은 남은 운영 게이트다. fixture 비교를 현장 성능 수용으로 표시하지 않는다.
- 계획은 실행 허가가 아니며, Action 성공과 물리 receipt도 업무 완료 전체를 뜻하지 않는다.
- 업무 완료는 반출대 내려놓기, 재취급 화물의 유효 위치 복귀, 관련 팔 퇴피를 모두 대조한다.
- 공급암 S 퇴피 확인은 Pinky의 출발 업무 조건이다. 사람이 확인하는 초기 방식과 로봇의 실행·안전 허가는 구별한다.
- Harbor는 Fleet HTTP만 소비한다. 플랫폼 Python·DB·ROS·장치 명령·자동 승인·자동 재무장을 추가하지 않는다.
- 현재 단일 중립 ARM HTTP와 TRIP/Cell 호환 시험은 simulation 근거다. 다단계 운영 전환·인증된 관측 어댑터·실물 수용은 미완료다.

## Brand Commitments

Harbor/HarborLogi 이름과 컨테이너 H 아이콘을 유지한다. 기존 브랜드 자산은 네이비·청록·오렌지·아이보리를 사용한다. 화면은 기존 Operate 시각 체계와 채택된 147개 토큰을 계승하며 DESIGN.md에 기록한다. 컨셉 이미지를 실제 장비 사진이나 실측 배치로 표시하지 않는다.

[디자인 철학](docs/design/07-design-foundation.md)은 ‘현재 업무와 다음 판단이 분명한 관제’다. [의미별 토큰 정본](design/tokens.json)에서 CSS를 생성하고 로컬 운영 현황·업무·사용 안내에 적용한다. [DESIGN.md](DESIGN.md)는 구현 기록이다. 인증된 제품 웹은 화물·요청, 재고 원본·계획, 작업, 현장 확인, 기록·검증, 관리자 설정을 구현했다. 장비 운영은 Fleet 소유다. 최종 수용은 진행 중이며 실제 운영 관측 producer, 모든 이동 단계의 설치 binding, 현장 UAT와 LAN/TLS 설치 검증이 남았다.

## Evidence on Hand

[ADR0005](docs/adr/0005-harbor-rosy-responsibility-map.md), [ADR0006](docs/adr/0006-neutral-arm-transfer-binding.md), [플랫폼 소비 기준](docs/platform-contract.md), [관측 수용 조건](docs/observation-acceptance.md), [중립 ARM HTTP 검증](docs/validation/2026-10-24-neutral-arm-http.md), [브랜드 자산](assets/brand/README.md). 제품 화면과 브라우저 검증 자료를 확보했다. 실제 사용자 수용과 인증된 운영 관측은 남은 게이트다.

## Product Principles

PC 진입의 기준은 사용자 확정대로 조치가 필요한 업무와 막힌 이유다. 전체 업무 탐색은 별도 목록에서 같은 업무 원본으로 이어진다.

1. 업무·로봇 수행·관측 근거의 상태를 따로 보여 준다.
2. 근거가 없거나 오래되면 미확인으로 남긴다.
3. 사용자 확인, Fleet 승인, 로봇의 최종 실행 허가를 구별한다.
4. 불명 응답·연결 단절 뒤에는 원래 요청을 대조하며 자동 재전송하지 않는다.

## Open Decisions

팀 자체 계정·역할 방식은 확정됐고 기술 계층은 ADR0007을 따른다. 실제 사용자 조직/겸임, 운영 관측 전달 계약, Fleet ARM 승인 화면과 신원 대응, 현장 네트워크/TLS·장치 수용은 남았다. 기술 추천과 실제 사용자 결정을 구분한다.
