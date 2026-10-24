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

**Creative North Star: "HarborLogi 작업 관제"**

The implemented workbench carries the existing HarborLogi navy, teal, orange and ivory identity into a Korean operation interface. Dense task facts sit on quiet surfaces; the current task and next check receive the clearest emphasis.

The local read-only interface separates Harbor business state, ROSY execution and physical receipt evidence. Demo data is visibly identified. This document records the implementation, not production authentication, field usability acceptance or physical device acceptance.

**Key Characteristics:**

- Persistent brand header and adjacent task list.
- Separate business, execution and evidence sections.
- Text labels accompany state colors.

Sources: [PRODUCT.md](PRODUCT.md), [brand assets](assets/brand/README.md), [implementation plan](docs/design/06-web-integration-plan.md), and `harbor/web_assets/{index.html,app.css,app.js}`. The CSS is the implementation source; frontmatter records its reusable values. Earlier product and brand notes that describe the web UI as absent predate this implementation.

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

The default workspace is a 320px task rail and a flexible detail column. Main content is capped at 1440px with 40px 42px 32px padding. Plan/execution and evidence use two columns with 40px gaps.

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
