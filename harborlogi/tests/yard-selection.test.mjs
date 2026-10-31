import assert from 'node:assert/strict';
import {selectionFor, atPosition} from '../harbor_web/static/harbor_web/yard-selection.mjs';

const boxes = [{id:'wide', area:'yard', row:2, column:1, level:3, orientation:0, width:3, depth:1},
  {id:'rotated', area:'temp', row:1, column:2, level:1, orientation:90, width:1, depth:2}];
const original = structuredClone(boxes);
assert.equal(atPosition(boxes, {area:'yard', row:2, column:3, level:3}).id, 'wide');
assert.equal(atPosition(boxes, {area:'yard', row:2, column:3, level:2}), undefined);
assert.equal(atPosition(boxes, {area:'temp', row:2, column:2, level:1}).id, 'rotated');
assert.equal(atPosition(boxes, {area:'yard', row:1, column:2, level:1}), undefined);
const selection = selectionFor(boxes[1]);
assert.deepEqual(selection, {cargoId:'rotated', area:'temp', row:1, column:2, level:1, orientation:90,
  coveredPositions:[{area:'temp', row:1, column:2, level:1}, {area:'temp', row:2, column:2, level:1}]});
selection.coveredPositions[0].row = 99;
assert.deepEqual(boxes, original);
assert.equal(selectionFor(null), null);
console.log('Logical linked selection: covered cells, levels, areas, rotations and source immutability passed');
