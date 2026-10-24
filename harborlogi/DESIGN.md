---
name: Harbor
description: Local read-only workbench for Harbor business records and ROSY execution evidence.
colors:
  navy: "#102e40"
  ink: "#183748"
  muted: "#526775"
  teal: "#116974"
  teal-soft: "#e2efed"
  orange: "#b84413"
  orange-soft: "#fff0e4"
  paper: "#f5f2eb"
  white: "#ffffff"
  line: "#d6dedc"
  focus: "#a53709"
  warning-ink: "#81320f"
  control-border: "#83959c"
  success-ink: "#226345"
  success-soft: "#e5f2e9"
  danger-ink: "#a52c31"
  danger-soft: "#fcebec"
  unknown-soft: "#e8edef"
typography:
  headline:
    fontFamily: '"Segoe UI", "Malgun Gothic", "Apple SD Gothic Neo", "Noto Sans KR", sans-serif'
    fontSize: "1.75rem"
    fontWeight: 700
    lineHeight: 1.3
    letterSpacing: "-.02em"
  title:
    fontFamily: '"Segoe UI", "Malgun Gothic", "Apple SD Gothic Neo", "Noto Sans KR", sans-serif'
    fontSize: "1.125rem"
    fontWeight: 700
    lineHeight: 1.4
  body:
    fontFamily: '"Segoe UI", "Malgun Gothic", "Apple SD Gothic Neo", "Noto Sans KR", sans-serif'
    fontSize: "1rem"
    fontWeight: 400
    lineHeight: 1.6
  label:
    fontFamily: '"Segoe UI", "Malgun Gothic", "Apple SD Gothic Neo", "Noto Sans KR", sans-serif'
    fontSize: "0.875rem"
    fontWeight: 600
    lineHeight: 1.6
rounded:
  small: "4px"
  control: "6px"
  panel: "8px"
spacing:
  "1": "0.25rem"
  "2": "0.5rem"
  "3": "0.75rem"
  "4": "1rem"
  "6": "1.5rem"
  "8": "2rem"
  "12": "3rem"
  "16": "4rem"
components:
  button-primary:
    backgroundColor: "{colors.teal}"
    textColor: "{colors.white}"
    rounded: "{rounded.control}"
    padding: "0.5rem 1rem"
    height: "44px"
  button-primary-hover:
    backgroundColor: "{colors.navy}"
    textColor: "{colors.white}"
  button-quiet:
    backgroundColor: "{colors.white}"
    textColor: "{colors.teal}"
    rounded: "{rounded.control}"
    padding: "0.5rem 1rem"
  input-search:
    backgroundColor: "{colors.white}"
    textColor: "{colors.ink}"
    rounded: "{rounded.control}"
    padding: "0.5rem 0.75rem"
  navigation-current:
    backgroundColor: "{colors.teal-soft}"
    textColor: "{colors.teal}"
    rounded: "{rounded.control}"
    padding: "0.75rem"
  task-current:
    backgroundColor: "{colors.teal-soft}"
    textColor: "{colors.ink}"
    rounded: "{rounded.small}"
    padding: "1rem 0.75rem"
  tab-current:
    textColor: "{colors.teal}"
    height: "48px"
  badge-hold:
    backgroundColor: "{colors.orange-soft}"
    textColor: "{colors.warning-ink}"
    rounded: "{rounded.small}"
    padding: "0.25rem 0.5rem"
  next-action:
    backgroundColor: "{colors.teal-soft}"
    textColor: "{colors.ink}"
    rounded: "{rounded.panel}"
    padding: "1.5rem"

  planning-bay:
    backgroundColor: "{colors.white}"
    textColor: "{colors.ink}"
    rounded: "{rounded.control}"
    padding: "0.75rem"

---

# Design System: Harbor

## Overview

**Creative North Star: "현재 업무와 다음 판단이 분명한 관제"**

Harbor는 사용자가 맡은 화물의 진행, 막힌 이유, 필요한 근거와 다음 행동을 판단하는 업무 도구다. 기존 네이비·청록·오렌지·아이보리와 컨테이너 H 아이콘을 유지한다. 밝고 차분한 작업면에서 선택과 다음 확인을 강조하고 상태에는 읽을 수 있는 문구를 붙인다.

업무 식별→현재 판단→진행→근거→추적 정보의 순서로 읽게 한다. 전역 탐색, 업무 목록의 검색·보기, 한 업무의 탭을 구분한다. Harbor 업무 상태, ROSY 실행, physical receipt와 업무 완료 근거는 각각 이름 붙이고 미확인과 조회 실패를 드러낸다.

**Key Characteristics:**

- 조치 필요 중심의 운영 현황과 별도 업무 목록.
- 전역 메뉴·업무 선택·업무 탭의 세 층 탐색.
- 상태 색과 문구, 출처와 조회 범위를 함께 표시.

**적용 상태와 정본:** 이 문서는 로컬 조회 앱에 구현된 디자인의 추출 기록이다. [design/tokens.json](design/tokens.json)의 124개 primitive→semantic→component 토큰이 런타임 값의 정본이며, [생성 도구](tools/design_tokens.py)가 [tokens.css](harbor/web_assets/tokens.css)를 만든다. Frontmatter는 DESIGN.md 도구를 위한 현재 값의 기록이며 별도 편집 원본이 아니다. 토큰을 변경하면 생성 CSS와 이 기록을 함께 갱신한다. Sidecar는 확장 메타데이터와 현재 CSS 변수에 연결한 패널 예제를 담는다.

- [디자인 철학](docs/design/07-design-foundation.md), [네비게이션](docs/design/08-navigation.md), [토큰·패턴 계약](docs/design/09-tokens-and-patterns.md): 제품 기준과 후속 범위.
- [페이지 구현 계획](docs/design/10-navigation-pages-plan.md), [재고·반출 계획 페이지](docs/design/11-retrieval-planning-page.md), [PRODUCT.md](PRODUCT.md), [브랜드 자산](assets/brand/README.md): 맥락과 범위.
- 구현 근거: `harbor/web_assets/{index.html,app.css,app.js,tokens.css}`.

현재 구현은 운영 현황·업무·도움말과 데모 전용 재고·반출 계획을 제공하는 로컬 읽기 전용 앱이다. 데모와 실제 Fleet 조회 어댑터의 경계는 유지한다. 운영 인증·RBAC·업무 쓰기·LAN 태블릿 배포·실물 수용은 이 디자인 기록의 완료 범위에 포함하지 않는다.

## Colors

### Primary

청록은 링크·주요 조회 조작·현재 선택을, 연한 청록은 선택 면을 나타낸다. 네이비는 전역 탐색의 배경과 주요 버튼 hover에 사용한다. 기존 브랜드 색상을 유지한다.

### Secondary

오렌지는 브랜드의 일부다. 보류·대조 필요와 데모 맥락은 연한 오렌지와 warning-ink를 사용한다. 데모 표식은 장애 심각도 표시가 아니다. 성공·위험·미확인 의미 토큰을 제공하며 실제 badge는 info·warning·success·unknown 변형을 사용한다. danger 토큰의 존재를 장치 안전 알람 구현으로 해석하지 않는다.

### Neutral

아이보리는 페이지 바탕, 흰색은 목록·입력·보조 헤더 면이다. ink는 주본문, muted는 보조 설명, line은 구분선, control-border는 입력과 보조 버튼 경계다. 밝은 면의 키보드 포커스는 focus, 네이비 면에서는 paper를 사용한다. PNG에서 새 색상을 추출하거나 패널 미리보기를 런타임 토큰으로 복사하지 않는다.

## Typography

운영체제의 한글 가능 sans stack을 사용하며 원격 폰트를 내려받지 않는다. 본문은 body, 페이지·업무 제목은 headline, 섹션 제목은 title, 조작과 보조 설명은 label을 사용한다. Metadata는 primitive-font-caption(0.75rem)이다. 본문 line-height는 body-line, 제목은 실제 CSS의 heading-line 또는 섹션 1.4를 사용한다. 이전 화면의 body15·headline30과 모바일 제목 축소 규칙은 적용하지 않는다.

문단의 읽기 폭은 read-measure(72ch)로 제한한다. 긴 ID는 줄바꿈하고 fact 값과 단계 번호는 tabular numerals로 정렬한다. 핵심 상태와 실패 이유를 metadata 크기로 압축하지 않는다.

## Layout

데스크톱은 desktop 토큰 이상(1280px)에서 전역 메뉴 navigation(200px), 업무 영역의 work-list(280px), 유연한 상세 열을 둔다. Topbar는 topbar(64px) 최소 높이이며 상단에 고정한다. 운영 현황과 도움말은 각각 최대 1440px와 1152px이다. 전역 메뉴는 업무 목록과 별도다. 재고·반출 계획은 최대 1440px에서 상태·이유 요약을 두 열 전체 너비로 먼저 보여 준다. 아래에는 왼쪽 관측·예상 배치, 오른쪽 이동 순서·필요 완료 조건을 두고 desktop 미만에서는 같은 순서로 한 열에 쌓는다. 계획 입력은 대상·합성 조건·미리보기 버튼의 세 열이며 mobile에서는 한 열이다. 논리 장치 칸은 각 영역 안에서 세 열을 유지한다.

Tablet 토큰 이상부터 desktop 미만(768–1279px)은 drawer 탐색과 목록→상세의 개별 화면을 사용한다. 그 아래는 mobile 한 열이며 근거·도움말 열도 쌓인다. JavaScript가 생성 CSS의 breakpoint 변수를 읽어 body의 layout 상태를 정하고 CSS가 배치한다. 화면 폭은 사용자 역할이나 권한을 바꾸지 않는다.

Desktop의 페이지 기본 inset은 space-8이며 mobile은 가로 space-4·세로 space-6을 사용한다. 간격 scale은 frontmatter의 원본 단계를 따른다. 조작 최소 높이는 control(44px), 좁은 화면의 button/input/select는 touch-control(48px), 업무 행은 row(56px)·touch-row(64px)다. 긴 내용은 최소 높이 이상으로 늘어난다.

## Elevation & Depth

작업면은 평면이며 그림자는 없다. 배경 톤, 구분선과 간격으로 위계를 만든다. Sticky 탐색·topbar와 drawer/backdrop은 primitive-layer 단계로 분리한다. 키보드 포커스는 focus-width와 focus-offset을 사용한다.

Drawer는 navigation duration과 ease 토큰으로 전환하고 reduced motion에서는 reduced 토큰으로 줄인다. Feedback duration은 토큰으로 제공되지만 별도 버튼 애니메이션은 현재 CSS에 적용되지 않았다. 추정 로봇 움직임이나 진행률을 애니메이션으로 만들지 않는다.

## Shapes

상태·업무 행에는 small, 입력·버튼·전역 메뉴 항목에는 control, 안내 패널·컨셉 배너에는 panel 모서리를 사용한다. 단계 번호는 원형이다. 면·제목·간격·구분선을 우선하며 모든 정보를 같은 카드로 감싸지 않는다. 컨테이너 H 원본을 다시 그리지 않는다.

## Components

### Buttons and inputs

주요 버튼은 설정된 Fleet 콘솔로 명시적으로 인계하거나 데모 반출 계획을 미리 계산한다. 보조 버튼은 장부 새로고침·ROSY 상태 재조회·원래 mission ID 복사를 제공한다. Hover는 선택/행동 의미 토큰을, focus는 공통 outline을 사용한다. 비활성 버튼은 disabled 면과 보조 텍스트·wait cursor를 사용한다. 검색과 보기 select는 보이는 label, 조작 최소 높이와 입력 경계를 유지한다.

### Navigation and work tabs

전역 메뉴에는 운영 현황(`/overview`)과 업무(`/work`)를 열고 사용 안내(`/help`)는 하단에 둔다. 데모 장부 조회가 가능할 때만 재고·반출 계획(`/planning`)을 추가한다. 실제 모드에는 계획 메뉴와 입력 폼을 숨기며 직접 접근 시 실제 재고 source 미설정 오류를 보여 준다. `/api/planning`은 이때 503을 반환하고 합성 재고로 대체하지 않는다. 별도 실제 재고·장비·기록의 빈 메뉴를 추가하지 않는다. 전역 현재 영역은 `aria-current=page`, 선택 업무는 `aria-current=location`, 업무 탭은 `aria-selected`로 구별한다.

업무 상세는 같은 ID의 개요(`/work/{business_id}`), 계획(`/work/{business_id}/plan`), 실행·근거(`/work/{business_id}/execution`)로 이동한다. 탭 방향키·Home/End와 browser 뒤로 가기를 제공한다. 검색·보기는 URL 맥락으로 전달하며 상세가 필터 밖이어도 현재 상세와 안내를 유지한다. 메뉴 drawer는 닫기·Escape·backdrop, 포커스 가두기와 원래 버튼 복귀를 제공한다. 숨은 배경은 inert로 처리한다.

### Work rows, badges and next check

현재 업무 행은 선택 경계와 연한 면을 함께 사용한다. Badge는 상호작용이 없는 한국어 상태 label이다. 운영 현황은 장부의 SUBMITTING·HOLD·AWAITING_FINAL_EVIDENCE에서 도출 가능한 조치 필요 업무를 보여 주며 WAITING은 별도 대기로 둔다. 담당이 없으면 담당 미지정으로 표시한다. 다음 확인은 현재 판단과 책임을 담는 tonal section이다.

### Synthetic retrieval planning

재고·반출 계획은 단일 목표 화물과 합성 조건을 선택해 기존 서버 planner의 결과를 읽는 화면이다. 입력 변경 시 이전 결과를 숨기고 재계산을 안내한다. 계산 중 버튼을 비활성화하고 실패는 alert와 미확인 안내로 남긴다. 관측 배치와 계획 후 예상 배치를 별도 제목·설명·출처로 분리한다. 합성 고정 시점·source·revision과 합성 팔 도달 표임을 명시한다.

칸의 stack은 위에서 아래로 표시하며 화물 ID·단·대상 설명을 붙인다. 반출 대상은 기존 선택 면, 위의 재취급 대상은 기존 주의 면을 사용한다. 이 도식은 논리 배치이며 실제 위치 지도나 도달성 증거가 아니다. 예상 배치는 ‘관측 아님’으로 표시하고 원래 관측 배치는 바꾸지 않는다.

READY 결과는 재취급→반출→복귀의 단계와 주/대체 팔을 보여 준다. 임시 공간 부족·오래된 관측 등의 HOLD 결과는 이유를 표시하고 부분 이동이나 예상 완료 배치를 만들지 않는다. 필요한 반출대 내려놓기·재취급 화물 복귀·관련 팔 퇴피는 확인 전 조건으로 제시한다. 이 화면은 실행 배정·허가·완료 판정, 순위 지시 접수·입고 등록·실제 관측·양팔 성능 비교 또는 전체 UR/SR 수용을 제공하지 않는다. 기존 124개 토큰만 재사용한다.

### Evidence and brand

실행·근거 탭은 Fleet 실행, physical receipt, Harbor 업무 장부를 분리한다. 원래 ID와 추적 정보는 펼칠 수 있게 보존한다. Loading·빈 목록·연결 실패·불일치·미확인을 구별하며 화면 조회 시각을 원본 관측 시각으로 표시하지 않는다. Skip link·live status·오류 alert·선택 후 제목 focus를 유지한다.

H 아이콘은 `assets/brand/harbor-app-icon-20261023.png`를 `/assets/icon.png`로 제공한다. 도움말은 기존 `assets/brand/harborlogi-github-banner-20261023.png`를 `/assets/banner.png`로 제공하고 컨셉 이미지 caption을 유지한다. 배너는 장치 실측이나 실행 증거가 아니다.

## Do's and Don'ts

- Do keep business state, ROSY execution and receipt evidence separately labeled.
- Do pair status color with readable text and preserve keyboard focus.
- Do reuse the existing Harbor name, container H icon and concept banner.
- Do derive shared CSS values from design/tokens.json and regenerate tokens.css.
- Don't present demo data or concept artwork as device evidence.
- Don't imply that a read, an Action success or a receipt authorizes execution or completes all business checks.
- Don't present this local workbench as production login, RBAC or physical acceptance.
- Don't open operational inventory, equipment or history navigation before their data and access contracts exist.
- Do label synthetic observed stacks and projected plan results separately; keep HOLD free of partial moves or projected completion.
