import assert from 'node:assert/strict';
import {
  DEFAULT_MOVE_SECONDS_ESTIMATE,
  actionBarState,
  applyReasonText,
  assignTiers,
  buildLaneStrings,
  canMove,
  computePopoverPosition,
  computeScheduleEtas,
  formatEtaHHMM,
  frontIndex,
  isScheduleChanged,
  minAllowedTier,
  moveItem,
  moveStep,
  moveToBack,
  moveToFront,
  moveToTier,
  nextPopoverState,
  shiftTier,
} from '../harbor_web/static/harbor_web/reorder-model.mjs';

const sample = [
  {id: '10', version: 2, tier: 1, kind: 'OUTBOUND', code: 'C9', running: true},
  {id: '11', version: 1, tier: 2, kind: 'OUTBOUND', code: 'C5', running: false},
  {id: '12', version: 3, tier: 3, kind: 'INBOUND', code: 'C2', running: false},
];

assert.equal(frontIndex(sample), 1);
assert.equal(minAllowedTier(sample), 1);
assert.deepEqual(moveItem(sample, '12', frontIndex(sample)).map(x => x.code), ['C9', 'C2', 'C5']);
assert.deepEqual(moveItem(sample, '10', 2).map(x => x.code), ['C9', 'C5', 'C2']);  // running cannot move
assert.deepEqual(moveItem(sample, '12', 0).map(x => x.code), ['C9', 'C2', 'C5']);  // clamped after running

const reordered = moveItem(sample, '12', frontIndex(sample));
assert.deepEqual(assignTiers(reordered).map(x => [x.code, x.tier]), [['C9', 1], ['C2', 3], ['C5', 3]]);
assert.deepEqual(buildLaneStrings(reordered), ['FIXED|10:2', 'FIXED|', 'FIXED|12:3,11:1']);

// << < > >> movement across tiers and running boundary
const mixed = [
  {id: '1', version: 1, tier: 1, kind: 'OUTBOUND', code: 'C1', running: false},
  {id: '2', version: 1, tier: 1, kind: 'INBOUND', code: 'C5', running: false},
  {id: '3', version: 1, tier: 2, kind: 'OUTBOUND', code: 'C4', running: false},
  {id: '4', version: 1, tier: 3, kind: 'INBOUND', code: 'C7', running: false},
];

// << moves C7 to the very front (tier 1, index 0)
const front = moveToFront(mixed, '4');
assert.deepEqual(front.map(x => [x.code, x.tier]), [['C7', 1], ['C1', 1], ['C5', 1], ['C4', 2]]);
assert.deepEqual(buildLaneStrings(front), ['FIXED|4:1,1:1,2:1', 'FIXED|3:1', 'FIXED|']);

// >> moves C1 to the very back (tier 3, last index)
const back = moveToBack(mixed, '1');
assert.deepEqual(back.map(x => [x.code, x.tier]), [['C5', 1], ['C4', 2], ['C7', 3], ['C1', 3]]);

// < crosses tier boundary from tier 2 to tier 1 and stays monotonic
const stepLeft = moveStep(mixed, '3', -1);
assert.deepEqual(stepLeft.map(x => [x.code, x.tier]), [['C1', 1], ['C4', 1], ['C5', 1], ['C7', 3]]);

// > crosses tier boundary from tier 1 to tier 2
const stepRight = moveStep(mixed, '2', 1);
assert.deepEqual(stepRight.map(x => [x.code, x.tier]), [['C1', 1], ['C4', 2], ['C5', 2], ['C7', 3]]);

// canMove disables << / < at frontIndex and > / >> at end, and all for running
assert.deepEqual(canMove(sample, '10'), {front: false, prev: false, next: false, back: false});
assert.deepEqual(canMove(sample, '11'), {front: false, prev: false, next: true, back: true});
assert.deepEqual(canMove(sample, '12'), {front: true, prev: true, next: false, back: false});
// shiftTier promotes/demotes across tier bands (ArrowUp / ArrowDown) while honoring minAllowedTier
const promotedToTier1 = shiftTier(sample, '11', -1);
assert.deepEqual(promotedToTier1.map(x => [x.code, x.tier]), [['C9', 1], ['C5', 1], ['C2', 3]]);
const demotedToTier3 = shiftTier(
  [{id: '1', version: 1, tier: 2, code: 'A', running: false}],
  '1',
  1,
);
assert.deepEqual(demotedToTier3.map(x => [x.code, x.tier]), [['A', 3]]);

// Drag/drop into another tier band (including empty tier and explicit insertion index)
const droppedIntoTier1 = moveToTier(mixed, '4', 1, 1);
assert.deepEqual(droppedIntoTier1.map(x => [x.code, x.tier]), [['C1', 1], ['C7', 1], ['C5', 1], ['C4', 2]]);

const withEmptyTier2 = [
  {id: '1', version: 1, tier: 1, code: 'C1', running: false},
  {id: '4', version: 1, tier: 3, code: 'C7', running: false},
];
const droppedIntoEmpty = moveToTier(withEmptyTier2, '4', 2, 0);
assert.deepEqual(droppedIntoEmpty.map(x => [x.code, x.tier]), [['C1', 1], ['C7', 2]]);
assert.deepEqual(buildLaneStrings(droppedIntoEmpty), ['FIXED|1:1', 'FIXED|4:1', 'FIXED|']);

// Running item in tier 2 prevents dropping non-running item into tier 1 or before running item in tier 2
const runningTier2 = [
  {id: '20', version: 1, tier: 2, code: 'R2', running: true},
  {id: '21', version: 1, tier: 2, code: 'A2', running: false},
  {id: '22', version: 1, tier: 3, code: 'B3', running: false},
];
assert.equal(minAllowedTier(runningTier2), 2);
const clampedDrop = moveToTier(runningTier2, '22', 1, 0);
assert.deepEqual(clampedDrop.map(x => [x.code, x.tier]), [['R2', 2], ['B3', 2], ['A2', 2]]);
const clampedFront = moveToFront(runningTier2, '22');
assert.deepEqual(clampedFront.map(x => [x.code, x.tier]), [['R2', 2], ['B3', 2], ['A2', 2]]);

assert.equal(applyReasonText({ok: true}), '순서 검증 통과 · 실행은 ROSY Fleet에서 승인한 뒤 시작됩니다');
assert.equal(applyReasonText({ok: false, reasons: ['실행 중인 반출 작업보다 앞에 둘 수 없습니다.']}),
  '적용 불가: 실행 중인 반출 작업보다 앞에 둘 수 없습니다.');
assert.equal(applyReasonText({ok: false, reasons: []}), '적용 불가: 이 순서로는 처리할 수 없습니다.');

// Action bar shows validate/apply/cancel ONLY when order or tier actually changed
assert.equal(isScheduleChanged(mixed, mixed.map(x => ({...x}))), false);
assert.deepEqual(actionBarState(mixed, mixed.map(x => ({...x}))), {
  dirty: false,
  showValidate: false,
  showApply: false,
  showCancel: false,
  applyDisabled: true,
  validateDisabled: true,
  cancelDisabled: true,
});
const movedMixed = moveStep(mixed, '3', -1);
assert.equal(isScheduleChanged(mixed, movedMixed), true);
assert.deepEqual(actionBarState(mixed, movedMixed, null, false), {
  dirty: true,
  showValidate: true,
  showApply: true,
  showCancel: true,
  applyDisabled: true,
  validateDisabled: false,
  cancelDisabled: false,
});
assert.equal(actionBarState(mixed, movedMixed, {ok: true}, false).applyDisabled, false);
assert.equal(actionBarState(mixed, movedMixed, {ok: false, reasons: ['불가']}, false).applyDisabled, true);
// Moving back to original order & tier hides the action buttons again
const restoredMixed = moveToTier(movedMixed, '3', 2, 0);
assert.equal(isScheduleChanged(mixed, restoredMixed), false);
assert.equal(actionBarState(mixed, restoredMixed).showValidate, false);

// Popover positioning: normal, right-edge flip, bottom-edge flip, corner flip, narrow viewport clamp
const vp = {width: 1440, height: 900};
const pop = {width: 320, height: 240};

const normalPos = computePopoverPosition({left: 160, right: 320, top: 200, bottom: 270}, pop, vp);
assert.deepEqual(normalPos, {left: 160, top: 278, align: 'left', placement: 'bottom'});

const rightEdgePos = computePopoverPosition({left: 1240, right: 1410, top: 200, bottom: 270}, pop, vp);
assert.equal(rightEdgePos.align, 'right');
assert.equal(rightEdgePos.placement, 'bottom');
assert.equal(rightEdgePos.left, 1090);
assert.ok(rightEdgePos.left + pop.width <= vp.width - 12);

const bottomEdgePos = computePopoverPosition({left: 200, right: 360, top: 760, bottom: 830}, pop, vp);
assert.equal(bottomEdgePos.align, 'left');
assert.equal(bottomEdgePos.placement, 'top');
assert.equal(bottomEdgePos.top, 512);
assert.ok(bottomEdgePos.top >= 12 && bottomEdgePos.top + pop.height <= vp.height - 12);

const cornerPos = computePopoverPosition({left: 1280, right: 1430, top: 780, bottom: 850}, pop, vp);
assert.equal(cornerPos.align, 'right');
assert.equal(cornerPos.placement, 'top');
assert.ok(cornerPos.left >= 12 && cornerPos.left + pop.width <= vp.width - 12);
assert.ok(cornerPos.top >= 12 && cornerPos.top + pop.height <= vp.height - 12);

const narrowPos = computePopoverPosition({left: 10, right: 150, top: 40, bottom: 100}, {width: 360, height: 200}, {width: 320, height: 500});
assert.equal(narrowPos.left, 12);

// Popover open/close transitions: cardClick toggle/switch, escape, outsideClick, dragStart, cancel, move
let openId = null;
openId = nextPopoverState(openId, {type: 'cardClick', id: '11'});
assert.equal(openId, '11');
openId = nextPopoverState(openId, {type: 'cardClick', id: '12'});
assert.equal(openId, '12');  // clicking another card switches to it
openId = nextPopoverState(openId, {type: 'move', id: '12'});
assert.equal(openId, '12');  // moving via << < > >> keeps popover open on moved card
openId = nextPopoverState(openId, {type: 'cardClick', id: '12'});
assert.equal(openId, null);  // clicking same card closes it
openId = nextPopoverState(openId, {type: 'cardClick', id: '11'});
assert.equal(nextPopoverState(openId, {type: 'escape'}), null);
assert.equal(nextPopoverState(openId, {type: 'outsideClick'}), null);
assert.equal(nextPopoverState(openId, {type: 'dragStart'}), null);
assert.equal(nextPopoverState(openId, {type: 'cancel'}), null);

// Per-task ETA calculation: baseTime + priorMoves * moveSecondsEstimate, 9th+ -> '—', cutoff late flag
assert.equal(DEFAULT_MOVE_SECONDS_ESTIMATE, 60);
{
  // Explicit UTC timestamps: 01:00Z = 10:00 KST, 01:05Z = 10:05 KST, 01:30Z = 10:30 KST
  const baseMs = Date.parse('2026-11-26T01:00:00Z');
  const cutoffEarlyMs = Date.parse('2026-11-26T01:05:00Z');
  const cutoffSafeMs = Date.parse('2026-11-26T01:30:00Z');
  const steps = [
    {id: '1', kind: 'OUTBOUND', ok: true, moves: 5, cutoffMs: cutoffSafeMs},
    {id: '2', kind: 'INBOUND', ok: true, moves: 0, cutoffMs: cutoffSafeMs},
    {id: '3', kind: 'OUTBOUND', ok: true, moves: 3, cutoffMs: cutoffEarlyMs},
    {id: '4', kind: 'OUTBOUND', ok: false, moves: 0, cutoffMs: cutoffEarlyMs},
    {id: '5', kind: 'OUTBOUND', ok: true, moves: 1, cutoffMs: cutoffSafeMs},
    {id: '6', kind: 'OUTBOUND', ok: true, moves: 1, cutoffMs: cutoffSafeMs},
    {id: '7', kind: 'OUTBOUND', ok: true, moves: 1, cutoffMs: cutoffSafeMs},
    {id: '8', kind: 'OUTBOUND', ok: true, moves: 1, cutoffMs: cutoffSafeMs},
    {id: '9', kind: 'OUTBOUND', ok: true, moves: 1, cutoffMs: cutoffEarlyMs},
  ];
  const etas = computeScheduleEtas(steps, {baseTimeMs: baseMs, moveSecondsEstimate: 60, limit: 8});
  assert.equal(etas.get('1').etaText, '10:00');
  assert.equal(etas.get('1').late, false);
  // After 5 moves * 60s = +5m -> 10:05 KST
  assert.equal(etas.get('2').etaText, '10:05');
  assert.equal(etas.get('2').late, false);
  // INBOUND counts as 1 move -> +1m -> 10:06 KST (> 10:05 cutoff -> late!)
  assert.equal(etas.get('3').etaText, '10:06');
  assert.equal(etas.get('3').late, true);
  // HOLD step -> '—'
  assert.equal(etas.get('4').etaText, '—');
  assert.equal(etas.get('4').late, false);
  // 9th step (index 8 >= limit 8) -> '—'
  assert.equal(etas.get('9').etaText, '—');
  assert.equal(etas.get('9').late, false);
  for (const entry of etas.values()) {
    assert.equal(entry.etaText.includes('예상'), false);
  }

  // Custom moveSecondsEstimate (120s/move): 5 moves = +10m -> 10:10 KST
  const etas120 = computeScheduleEtas(steps.slice(0, 2), {baseTimeMs: baseMs, moveSecondsEstimate: 120});
  assert.equal(etas120.get('2').etaText, '10:10');
  assert.equal(formatEtaHHMM(NaN), '—');

  // formatEtaHHMM always renders KST (Asia/Seoul, UTC+9) regardless of host timezone
  assert.equal(formatEtaHHMM(Date.UTC(2026, 10, 26, 0, 0, 0)), '09:00');
  assert.equal(formatEtaHHMM(Date.UTC(2026, 10, 26, 1, 5, 0)), '10:05');
  assert.equal(formatEtaHHMM(Date.UTC(2026, 10, 26, 14, 59, 0)), '23:59');
  assert.equal(formatEtaHHMM(Date.UTC(2026, 10, 26, 15, 0, 0)), '00:00');
  assert.equal(formatEtaHHMM(Date.UTC(2026, 10, 26, 15, 7, 0)), '00:07');

  // Midnight KST rollover from UTC base time (14:58Z -> 23:58 KST; +2m -> 00:00 KST)
  const rollover = computeScheduleEtas(
    [
      {id: 'a', kind: 'OUTBOUND', ok: true, moves: 2},
      {id: 'b', kind: 'OUTBOUND', ok: true, moves: 1},
    ],
    {baseTimeMs: Date.parse('2026-11-26T14:58:00Z'), moveSecondsEstimate: 60},
  );
  assert.equal(rollover.get('a').etaText, '23:58');
  assert.equal(rollover.get('b').etaText, '00:00');
}

console.log('Reorder model: moves, dirty action bar, popover positioning, open/close & ETA rules passed');
