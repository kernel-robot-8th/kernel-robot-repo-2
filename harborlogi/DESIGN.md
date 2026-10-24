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
typography:
  headline:
    fontFamily: '"Segoe UI", "Malgun Gothic", sans-serif'
    fontSize: "30px"
    fontWeight: 700
    lineHeight: 1.3
    letterSpacing: "-.025em"
  title:
    fontFamily: '"Segoe UI", "Malgun Gothic", sans-serif'
    fontSize: "18px"
    fontWeight: 700
    lineHeight: 1.5
  body:
    fontFamily: '"Segoe UI", "Malgun Gothic", sans-serif'
    fontSize: "15px"
    fontWeight: 400
    lineHeight: 1.65
  label:
    fontFamily: '"Segoe UI", "Malgun Gothic", sans-serif'
    fontSize: "12px"
    fontWeight: 600
    lineHeight: 1.65
rounded:
  badge: "4px"
  input: "5px"
  button: "6px"
  panel: "8px"
spacing:
  compact: "8px"
  control-y: "9px"
  control-x: "15px"
  inset: "18px"
  section: "24px"
  column: "40px"
components:
  button-primary:
    backgroundColor: "{colors.teal}"
    textColor: "{colors.white}"
    rounded: "{rounded.button}"
    padding: "9px 15px"
  button-primary-hover:
    backgroundColor: "{colors.navy}"
    textColor: "{colors.white}"
  button-quiet:
    backgroundColor: "transparent"
    textColor: "{colors.teal}"
    rounded: "{rounded.button}"
    padding: "9px 15px"
  input-search:
    backgroundColor: "{colors.white}"
    textColor: "{colors.ink}"
    rounded: "{rounded.input}"
    padding: "9px 12px"
  task-current:
    backgroundColor: "{colors.teal-soft}"
    textColor: "{colors.ink}"
    rounded: "{rounded.badge}"
    padding: "18px 12px"
  badge-hold:
    backgroundColor: "{colors.orange-soft}"
    textColor: "#81320f"
    rounded: "{rounded.badge}"
    padding: "3px 9px"
  next-action:
    backgroundColor: "{colors.teal-soft}"
    textColor: "{colors.ink}"
    rounded: "{rounded.panel}"
    padding: "20px 24px"
---

# Design System: Harbor

## Overview

**디자인 철학: 현재 업무와 다음 판단이 분명한 관제.**

Harbor는 사용자가 맡은 화물의 진행, 막힌 이유, 필요한 근거와 다음 행동을 판단하는 업무 도구다. 로봇의 모든 수치를 나열하는 조종판으로 구성하지 않는다. 사용자 확인에 따라 PC 첫 화면은 **조치 필요 중심**으로 설계한다. 전체 업무는 별도 목록에서 찾고 같은 업무 상세로 연결한다.

업무 식별→현재 판단→진행→근거→추적 정보의 순서로 읽게 한다. 전역 탐색, 업무 목록의 선택/필터, 한 업무의 탭을 서로 다른 층으로 둔다. 업무 상태·Fleet 실행·physical receipt·업무 완료 근거를 따로 표시하고 미확인과 조회 실패도 명시한다. 태블릿에서는 같은 업무 원본을 사용하되 배정된 확인과 복귀에 집중한다.

기존 네이비·청록·오렌지·아이보리와 H 아이콘을 유지한다. 브랜드 이미지는 제품 식별과 소개에 쓰고 실제 실행 상태를 대신하지 않는다. 긴 시간 읽는 작업면은 밝고 차분하게 유지하며 중요한 상태는 색과 문구를 함께 쓴다.

**적용 상태와 정본:** 이 파일의 frontmatter 및 아래 시각 치수는 현재 로컬 조회 앱에서 추출한 기록이다. `.impeccable/design.json`도 같은 구현 기록이다. 다음 전역 네비게이션과 의미별 토큰은 아직 runtime에 적용하지 않았다.

- [디자인 철학](docs/design/07-design-foundation.md): 판단 기준과 정보 위계.
- [네비게이션](docs/design/08-navigation.md): 조치 필요 첫 화면, 전역 메뉴, 업무 탭, PC/태블릿 이동.
- [토큰·패턴 계약](docs/design/09-tokens-and-patterns.md), [제안 토큰](design/tokens.proposed.json): 다음 구현의 3단계 토큰과 상태 규칙.
- 현재 구현 근거: [PRODUCT.md](PRODUCT.md), [브랜드 자산](assets/brand/README.md), [첫 구현](docs/design/06-web-integration-plan.md), `harbor/web_assets/{index.html,app.css,app.js}`.

현재 화면은 장부와 Fleet HTTP를 읽는 기능이 있지만 전체 앱의 네비게이션·인증·쓰기·현장 수용을 완성한 제품은 아니다. 제안 토큰은 채택·CSS 적용·렌더 검증 후 구현 기록으로 갱신한다.

## Colors

### Primary

Teal identifies links, primary controls, current steps and selected tasks. Soft teal supplies their supporting surfaces. Navy anchors the header, active filters and primary-button hover.

### Secondary

Orange belongs to the existing brand palette. The interface uses soft orange with dark warm text for hold, error and demo context; it does not use the saturated orange token as a general action fill. The shared focus token supplies the visible keyboard outline.

### Neutral

Paper is the page canvas; white is the task-list and input surface. Ink carries main text, muted carries secondary explanations, and line separates sections. Use the frontmatter values rather than sampling the PNG assets. Sidecar tonal ramps are synthesized panel previews; they are not additional implemented color tokens.

## Typography

Operate UI uses the system Korean-capable sans stack in frontmatter, without a downloaded font. Body copy has a generous line height and paragraphs are limited to 72ch. The work-detail headline uses the headline token and becomes 26px below the 700px breakpoint. The sidebar heading and wordmark are 24px; section titles are 18px. Secondary copy is generally 13px, while evidence metadata ranges from 11px to 12px. IDs wrap; fact values and step numbers use tabular numerals.

## Layout

The implemented workspace is a 320px task list and a flexible detail column. This list is not global application navigation. Main content is capped at 1440px with 40px 42px 32px padding. Plan/execution and evidence use two columns with 40px gaps. The proposed navigation shell and its different widths belong to the linked navigation and token specifications until implemented.

At widths up to 1100px, the rail becomes 270px, main padding becomes 32px 24px, and detail/evidence sections stack with 28px gaps. Up to 700px, the rail sits above details, main padding becomes 28px 18px, and the task list scrolls within 250px. At 1600px and above, the rail becomes 350px and main padding becomes 48px 64px. These are implemented breakpoints, not evidence of acceptance on a deployed tablet.

## Elevation & Depth

Surfaces are flat. Background tones, thin dividers and the execution section's 2px navy top rule establish hierarchy. No shadows or animated transitions are defined. Keyboard focus uses a 3px outline with a 3px offset.

## Shapes

Controls and status surfaces use small corners from the frontmatter. Step numbers are circular, while the existing brand icon has a 9px display radius. The next-check panel and welcome banner use the panel radius. Preserve the container H artwork rather than redrawing it.

## Components

Primary buttons perform local reads or link to the configured Fleet console. Quiet buttons refresh the task list or copy the original mission ID. Both have a 44px minimum height. Disabled buttons use reduced opacity and a wait cursor. Inputs retain visible labels and a 44px minimum height.

Task navigation uses full-width text buttons with a teal outline and pale fill on hover or current selection. Status filters expose their selected state through `aria-pressed`; current tasks expose `aria-current`. Hold badges use warm text and pale orange. The next-check panel is a tonal section, not a floating card.

Keep the skip link, focus outlines, live status text, explicit error alerts, and focus transfer to the selected work heading. Preserve the distinction between loading, unavailable, mismatched, unverified and completed records.

The header serves `assets/brand/harbor-app-icon-20261023.png` through `/assets/icon.png`; the welcome illustration serves `assets/brand/harborlogi-github-banner-20261023.png` through `/assets/banner.png`. Keep both existing images unchanged and retain the concept-image caption. The illustration is not measured equipment or execution evidence.

## Do's and Don'ts

- Do keep business state, ROSY execution and receipt evidence separately labeled.
- Do pair status color with readable text and preserve keyboard focus.
- Do reuse the existing Harbor name, container H icon and concept banner.
- Don't present demo data or concept artwork as device evidence.
- Don't imply that a read, an Action success or a receipt authorizes execution or completes all business checks.
- Don't present this local workbench as production login, RBAC or physical acceptance.
