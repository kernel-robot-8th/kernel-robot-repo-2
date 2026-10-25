# Operator journeys and ROSY pipeline implementation plan

> Use superpowers:subagent-driven-development. Root integrates and reviews task outputs; independent ownership is below.

**Goal:** Make each page actionable and preserve context through Harbor/Fleet without changing business/execution authority.

**Spec:** `docs/design/17-operator-page-flow.md`, `18-harbor-rosy-pipeline.md`.

**Constraints:** Django/147tokens/currentlogo/roles/spatial model stay. No automatic admit, device access, invented receipt/business proof, live-authority promotion, or unsupported deep-link claim. Preserve historical source and existing UNKNOWN intent.

- [ ] Context worker: views/field_pages/record_pages/forms + tests only. Preserve observation/cargo/order scopes, handle expiry/renewal, correct footprint initial, scoped records and actionable inbound home rows.
- [ ] Pipeline worker: new pipeline/handoff read model, commands safe read metadata, planning_pages, work/work_detail/pipeline templates + tests only. Labels/ownership/primary action, current source facts, supported UI links and connection summary.
- [ ] Fleet worker: continue existing appropriate clean app-neutral-actions worktree. Known neutral ARM lookup/explicit independent admission through existing APIs in Fleet UI; no server authority/API change. Committed remote tests/browser and source/known-failure evidence required. Don't touch other sessions' root changes.
- [ ] Root: all other Harbor page templates, selected-source workbench, workflow help/connection page, shared styling, diagrams/docs, browser and cross-app verification.
- [ ] Independently review task and integrated contracts/evidence; fix material findings with regression tests.
- [ ] Run Harbor domain/Django/tokens/browser/package checks and committed ROSY remote suites; document actual pins/deployment/gates and CE, commit/local integrate within existing authorization, show the running preview.

## Review focus

1. Selecting an old/invalid observation never silently selects a different source; historical plan stays pinned.
2. A stale CLEAR cannot present a doomed departure action or become a fresh fact by button press.
3. Same numeric object IDs do not mix records; field-only users retain their own scope.
4. Known ARM job admission uses independent Fleet identity/current digest/generation and no POST on read or retry.
5. Configuration and health/session success do not imply compatible ARM API, approved execution or business completion.

## Ledger

- Source trace established that the verified neutral API feature pin is not shared main; old Fleet UI cannot admit that job type. Implement the missing UI at Fleet's owner instead of mislabeling Harbor's old root link.
- ROSY tree count13 including main. Reuse the clean, appropriate `.worktrees/app-neutral-actions` continuation; no new slot. Shared-main untracked aff1.log and an unrelated solution document remain untouched.
