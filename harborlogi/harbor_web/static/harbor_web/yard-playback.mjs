/** Pure playback-control rules for the 3D yard viewer (pause, step, reduced motion). No DOM access. */

/** Animation and camera-follow defaults; prefers-reduced-motion turns both off. */
export function playbackDefaults(reducedMotion) {
  const reduced = Boolean(reducedMotion);
  return {animate: !reduced, follow: !reduced, cameraTween: false};
}

/** Clamp `current + delta` into [0, total - 1]; null when the step would not move. */
export function stepTarget(current, delta, total) {
  const count = Number.isInteger(total) ? total : 0;
  if (count < 2 || !Number.isFinite(delta) || delta === 0) return null;
  const from = Math.max(0, Math.min(count - 1, Number.isInteger(current) ? current : 0));
  const next = Math.max(0, Math.min(count - 1, from + Math.sign(delta) * Math.max(1, Math.abs(Math.trunc(delta)))));
  return next === from ? null : next;
}

/** Disabled state for the previous/next step buttons. */
export function stepButtonState({replayable = false, index = 0, total = 0} = {}) {
  if (!replayable || total < 2) return {prevDisabled: true, nextDisabled: true};
  return {prevDisabled: index <= 0, nextDisabled: index >= total - 1};
}

/** Label and aria-pressed for the play/pause button. */
export function playButtonState({active = false, paused = false, draft = false, planReplay = false} = {}) {
  if (active) return {label: paused ? '계속 재생' : '일시정지', pressed: !paused};
  return {label: draft || !planReplay ? '가상 변경 재생' : '계획 재생', pressed: false};
}
