import assert from 'node:assert/strict';
import {
  actionBarState,
  applyReasonText,
  assignTiers,
  buildLaneStrings,
  canMove,
  computePopoverPosition,
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

console.log('Reorder model: moves, dirty action bar, popover positioning & open/close rules passed');
