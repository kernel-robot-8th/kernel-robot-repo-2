# 3D container view implementation

- [x] Backend: serialize existing configured stage geometry only; tests for atomic footprints/orientation/unconfigured fallback; wire workspace/inventory.
- [x] Frontend: bounded Canvas cuboids/camera/hit selection, accessible cargo list, explicit step playback, 2D switch/noJS/reduced-motion/failure recovery. Keep current stage buttons as the shared source of selected step.
- [x] Verify: geometry tests + Django + actual four-width browser/motion/selection/fallback/immutability; visually inspect desktop/mobile; independent review.
- [x] Document actual scope/evidence and CE, commit/local main integration, refresh isolated preview and show it.

Ownership: backend worker owns yard_scene.py, workspace.py, views.py and new backend tests. Root owns templates/assets/browser checks/docs. Existing unrelated edits must be preserved. No ROSY code/device/API mutation.

Implemented code a045143. Django131 passed, domain129 passed/2integration skips, Node math checks passed, both authenticated browser suites passed at4 widths. Actual playback/stop/manual interruption, projected box selection, reduced-motion/noJS/Canvas fallback and original-data immutability were verified. See docs/validation/2026-10-25-container-3d.md. CE: no separate learning; code/tests explain the behavior. Real geometry/robot trajectories remain outside this visualization.
