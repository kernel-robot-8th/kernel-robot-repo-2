# HarborLogi outbound workspace implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Connect outbound instructions, observed yard, rehandling/outbound/restore plan, original ROSY execution and evidence within one task workspace.

**Architecture:** Add a read-only presentation module over existing immutable Plan/Observation and Command detail. Reuse order POST handlers and command services. Keep original request/plan/work URLs and add an outbound queue.

**Tech Stack:** Existing Django templates, Python, progressive JavaScript, committed HarborLogi tokens.

**Spec:** `docs/design/14-outbound-workspace.md`.

## Global constraints

- No new dependencies, Fleet endpoints, producer identities or execution authority.
- Preserve explicit roles, CSRF, version checks, immutable snapshots and one command per order.
- Preview stages are expected placements, never observations or business completion.
- Existing logo palette and semantic tokens remain authoritative.

## Review focus

1. Unknown original execution stays visible after a newer plan exists.
2. Historical plan and current priority/source are clearly distinct.
3. Empty/HOLD/stale/held cargo layouts remain usable without partial executable plans.
4. GET and stage selection perform no network commands or database writes.
5. Viewer/field permissions and unrelated audit isolation hold through new routes.

## Task 1: Read model and server behavior

Files: new `harbor_web/workspace.py`, `harbor_web/tests/test_workspace.py`; existing `views.py`, `planning_pages.py`, `urls_product.py`.

Interface: `workspace_context(order, plan=None, priority_form=None, plan_form=None) -> dict` returns pinned source, stage layouts, current plan differences, original command/detail, completion requirements and scoped audit. `outbound_queue(request)` is GET-only and paginated.

- [x] Add tests for three-stage buried retrieval, unknown original command/new plan, historical revisions, stale/new source, HOLD/held cargo, permissions, GET immutability and scoped history; watch them fail.
- [x] Implement the read model and integrate the existing request/plan views without replacing their POST services.
- [x] Run targeted workspace and existing product/command tests; commit the coherent workspace with Task 2.

## Task 2: Task workspace and progressive yard preview

Files: new workspace/yard/queue template partials; existing base/request/plan/work/overview templates, `product.css`, `product.js`.

Interface: `[data-yard-stage]` buttons control uniquely identified expected layout panels; `aria-pressed` and live status reflect selection. No automatic submission, polling or synthetic execution progress.

- [x] Render task header, anchored work sections, observed/expected yard, phase sequence, decision panel, existing action forms and scoped audit using Task 1 context.
- [x] Keep no-JS content available; use text labels in addition to color; preserve keyboard and mobile/tablet access.
- [x] Browser-check actual preview switching, planning/priority updates, original command links, viewer/field restrictions and 1440/1280/768/390 widths.

## Task 3: Verify, review and publish local preview

Files: browser check and validation record; `PRODUCT.md`, `DESIGN.md`, design index and surface record as needed.

- [x] Run full domain unittest, product Django tests, token check and whitespace check once changes stabilize.
- [x] Inspect desktop/tablet/mobile screenshots; correct observed issues.
- [x] Obtain one fresh-context whole-branch review and address material findings with regression checks.
- [ ] Record supported behavior and remaining operational contracts without claiming full UR/SR or field acceptance. Evaluate CE gate.
- [ ] Commit, locally integrate as previously authorized, and restart the isolated loopback preview for the user.

## Execution ledger

- Ruling: Continue the existing approved Django/brand work in its clean isolated worktree on `feat/outbound-workspace`; do not reset to remote and lose already integrated local product work.
- Ruling: User approved the immediately preceding concrete workspace proposal; do not repeat design permission questions already settled by this request.
- Review: Two Important findings reproduced as failing tests: original execution versus displayed-plan completion subjects; queue/home freshness. Both fixed and independently confirmed. Final workspace suite12, Django suite84, domain117(skip2), browser four widths and no-JS passed. Execution services and Fleet contracts were not expanded.
