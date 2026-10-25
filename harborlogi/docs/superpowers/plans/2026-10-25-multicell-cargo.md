# Multi-cell cargo implementation plan

> **For agentic workers:** Use superpowers:subagent-driven-development with the ownership and interface contract below. Root owns integration and reviews each task before completion.

**Goal:** Implement atomic multi-cell cargo occupancy and retrieval while preserving Django, historical data, explicit permissions and ROSY execution boundaries.

**Spec:** `docs/design/16-multicell-cargo.md` is the shared exact interface contract.

**Architecture:** New stdlib spatial model/planner, immutable footprint revisions and versioned observation adapter, shared spatial presentation in existing Django. Legacy records remain compatible. No framework migration or new physical API authority.

## Ownership and tasks

- [x] Requirements worker: read and preserve current private Confluence UR/SR/detail-design pages, apply narrow multi-cell updates with existing IDs/traceability/status, version-check/read back. Preserve private originals/evidence outside public repo.
- [x] Domain worker: `harbor/spatial.py`, `tests/test_spatial.py` only. Implement exact DTOs/planner from spec, RED/GREEN tests for whole-footprint collision/support/rehandling/restoration/finite search and atomic counts. No Django or platform code.
- [x] Web worker: footprint model/migration, forms/services/commands/planning pages/tests only as specifically assigned. Consume the domain interface, preserve legacy actions and gates. Tests precede changes; data must not be backfilled as declared facts.
- [x] Root: workspace/yard presentation, templates/styles/JS, synthetic preview, browser regression, public docs and integration. Display one entity across cells; preserve layer selection and original execution/plan/source lineage.
- [x] Run domain/Django/browser and package/asset checks, review integrated branch, resolve material findings with regression tests.
- [x] Verify UR/SR readback and traceability, record real evidence and remaining physical gates, evaluate CE, commit/local integrate and serve the updated isolated preview.

## Review focus

1. A large cargo spanning multiple cells remains one entity/action/count.
2. Moving a support cargo requires clearing all dependent overlapping cargo, counted once.
3. Restoration cannot force floating or overlapping original placements after target removal.
4. Shape revisions and full observed areas remain pinned; new declarations cannot rewrite historical source.
5. Legacy1×1 binding cannot activate a new multi-cell plan or bypass an existing UNKNOWN command.

## Scope rulings

- User explicitly retained Django after the initial goal wording mentioned FastAPI; the FastAPI clause is superseded and will not be implemented.
- Geometry here is logical unit occupancy. Robot calibration/reach, live producer, actual load stability and field acceptance remain separately verified contracts.
- Planner preserves declared orientation. Automatic reorientation is not added.
- Held cargo may lack a declared shape revision; placed cargo must always have one. Holdings force HOLD and are never given a guessed latest shape.
- Reviews: domain and data tasks independently approved; integrated branch Ship. A nonblocking inbound HOLD source-display gap was reproduced RED and fixed by the shared spatial view; final107-test Django run GREEN. Domain131(skip2), legacy and spatial browser4width runs passed. UI also verifies90degree rowspan plus fit-HOLD with no partial plan.
- Evidence: `docs/validation/2026-10-25-multicell-cargo.md`. CE not applicable. Preserve this existing checkout and isolated preview; local integration only, no remote publication or actual-device execution.
