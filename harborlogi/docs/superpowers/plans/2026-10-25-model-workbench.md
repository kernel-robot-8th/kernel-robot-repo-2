# Modular container workbench

- [x] Pin and vendor official Three.js + controls/license; use self-hosted ESM without CDN or inline scripts.
- [x] Pure scene/draft module: immutable source, full footprint/grid/support validation, rigid animation math; focused Node tests.
- [x] Renderer module: real meshes/lighting, orbit/pan/zoom, picking/focus/isolate, transform gizmo, lifecycle/context fallback.
- [x] Controller/templates: shared stage playback, distinct local draft edit/restore, responsive viewer/inspector/fullscreen, accessible input alternatives.
- [x] Run backend/Node and actual PC/tablet/mobile browser acceptance, inspect screenshots, independent review; document CE/evidence and local main integration/preview.

Ownership: model worker owns yard-model.mjs and its Node tests; renderer worker owns yard-renderer.js. Root owns vendor, controller, HTML/CSS, browser checks and docs. Not alone; preserve other workers' edits. No ROSY, domain plan or persisted data mutation.

Code20b9f62 plus verified multi-pointer regression fix. Django131 passed; domain129 passed/2integration skips; Node model checks passed; real WebGL/gizmo/draft/touch/fullscreen/context-loss browser acceptance and prior product suite passed at4 widths. Wheel includes engine/controls/license. Source remains immutable; virtual drafts have no save/execute path. See docs/validation/2026-10-25-model-workbench.md. CE: no separate learning; source/tests capture prevention. Physical stability/robot motion remain outside this work.
