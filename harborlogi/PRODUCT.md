# Harbor 제품 맥락

<!-- impeccable:product-schema 1 -->

상태: 2026-10-25 기준. 기존 결정과 현재 구현, 이번 UI 설계의 제안을 구분한다. 상세설계는 [설계 문서 지도](docs/design/README.md)를 따른다.

## Platform

web

사용자 확인(2026-10-25): 관제 PC 중심 + 현장 태블릿 보조, 브라우저 웹앱. 첫 구현은 Python 표준 WSGI + HTML/CSS/JavaScript의 로컬 조회 앱이다. [실행 안내](docs/web-workbench.md). 운영 framework·배포·로그인과 LAN 태블릿 연결은 미확정이다.

## Users

관제에서 항만 업무 계획·진행·예외를 다루고 현장에서 필요한 사실을 확인한다. 세부 업무 책임 역할과 접근 권한은 [역할 설계안](docs/design/01-roles-and-workflows.md)의 제안이며, 실제 인원·계정·겸임 정책은 미확정이다.

## Product Purpose

HarborLogi 프로젝트의 업무 앱 Harbor는 양하 이후 장치와 재취급을 동반한 반출을 계획하고 업무 완료 근거를 대조한다. ROSY Platform이 공통 실행·자원 배정·장치 owner를 소유한다. 용어 정본은 [CONTEXT.md](CONTEXT.md)다.

## Operating Context

공급암 S는 외부 운영이고 Harbor가 명령하지 않는다. 적층암 A·B는 ROSY 제어 대상이며 작업마다 역할을 정한다. Pinky 이송 차량은 후보 풀에서 고르고 Fleet이 실제 배정한다. 컨베이어는 용량1의 한 방향 상시 동작 구간이다. 근거는 ADR0005와 현재 site/model 코드이며 실제 설치 수용은 별도다.

## Capabilities and Constraints

- 현재는 Python CLI·계획 로직·SQLite 업무 장부·표준 HTTP 클라이언트다. 웹 서버·로그인·RBAC·운영 UI는 아직 없다.
- 계획은 실행 허가가 아니며, Action 성공과 물리 receipt도 업무 완료 전체를 뜻하지 않는다.
- 업무 완료는 반출대 내려놓기, 재취급 화물의 유효 위치 복귀, 관련 팔 퇴피를 모두 대조한다.
- 공급암 S 퇴피 확인은 Pinky의 출발 업무 조건이다. 사람이 확인하는 초기 방식과 로봇의 실행·안전 허가는 구별한다.
- Harbor는 Fleet HTTP만 소비한다. 플랫폼 Python·DB·ROS·장치 명령·자동 승인·자동 재무장을 추가하지 않는다.
- 현재 단일 중립 ARM HTTP와 TRIP/Cell 호환 시험은 simulation 근거다. 다단계 운영 전환·인증된 관측 어댑터·실물 수용은 미완료다.

## Brand Commitments

Harbor/HarborLogi 이름과 컨테이너 H 아이콘을 유지한다. 기존 브랜드 자산은 네이비·청록·오렌지·아이보리를 사용한다. 상세 색상·서체·화면 디자인 시스템은 아직 확정하지 않았다. 컨셉 이미지를 실제 장비 사진이나 실측 배치로 표시하지 않는다.

## Evidence on Hand

[ADR0005](docs/adr/0005-harbor-rosy-responsibility-map.md), [ADR0006](docs/adr/0006-neutral-arm-transfer-binding.md), [플랫폼 소비 기준](docs/platform-contract.md), [관측 수용 조건](docs/observation-acceptance.md), [중립 ARM HTTP 검증](docs/validation/2026-10-24-neutral-arm-http.md), [브랜드 자산](assets/brand/README.md). 운영 화면·실사용자 UX 시험·인증된 실제 관측은 확보하지 않았다.

## Product Principles

1. 업무·로봇 수행·관측 근거의 상태를 따로 보여 준다.
2. 근거가 없거나 오래되면 미확인으로 남긴다.
3. 사용자 확인, Fleet 승인, 로봇의 최종 실행 허가를 구별한다.
4. 불명 응답·연결 단절 뒤에는 원래 요청을 대조하며 자동 재전송하지 않는다.

## Open Decisions

운영 framework·배포/인증 방식, 실제 사용자 조직과 권한, 운영 관측 전달 계약, Fleet의 ARM 승인 화면, 실제 장치/화면 크기·현장 사용성은 미확정이다. 다음 설계 문서의 추천안을 사용자 결정으로 기록하지 않는다.
