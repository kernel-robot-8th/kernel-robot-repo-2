import assert from 'node:assert/strict';
import {transposeArea, transposeBox, transposePoint} from '../harbor_web/static/harbor_web/yard-display.mjs';
import {slotLabel} from '../harbor_web/static/harbor_web/yard-selection.mjs';

const areas = [
  {id:'yard', label:'야드', x:0, y:0, rows:3, columns:6, levels:3},
  {id:'temp_a', label:'임시 판 A', x:0, y:-4.2, rows:3, columns:3, levels:3},
  {id:'temp_b', label:'임시 판 B', x:0, y:4.2, rows:3, columns:3, levels:3},
  {id:'stand', label:'반출대', x:7.2, y:-4.2, rows:1, columns:3, levels:1},
];
const original = structuredClone(areas);
const display = areas.map(transposeArea);
assert.deepEqual([display[0].rows, display[0].columns], [6, 3]);
assert.ok(display[1].x + display[1].columns <= display[0].x);
assert.ok(display[2].x >= display[0].x + display[0].columns);
assert.equal(display[3].x, display[1].x);
assert.ok(display[3].y > display[0].y + display[0].rows);
for (const [row, column, wantX, wantY, wantLabel] of [
  [1, 1, 0, 0, '1행 1열 2층'], [2, 1, 1, 0, '1행 2열 2층'], [3, 1, 2, 0, '1행 3열 2층'],
  [1, 4, 0, 3, '2행 1열 2층'], [2, 4, 1, 3, '2행 2열 2층'], [3, 4, 2, 3, '2행 3열 2층'],
]) {
  const box = {id:'K', area:'yard', row, column, level:2, x:column-1, y:row-1, z:1, width:3, depth:1};
  const shown = transposeBox(box);
  assert.deepEqual([shown.x, shown.y, shown.width, shown.depth], [wantX, wantY, 1, 3]);
  assert.equal(slotLabel(areas, shown, 3), wantLabel);
  assert.deepEqual(transposeBox(shown), box);
  assert.deepEqual(transposePoint(shown), {x:box.x, y:box.y, z:box.z});
}
for (const row of [1, 2, 3]) {
  const box = {area:'temp_a', row, column:1, level:1, x:0, y:-4.2+row-1, z:0, width:3, depth:1};
  const shown = transposeBox(box);
  assert.equal(shown.x, -4.2+row-1);
  assert.equal(slotLabel(areas, shown, 3), `임시 판 A ${row}레인 1층`);
}
assert.deepEqual(areas, original);
console.log('Display transpose: all six slots, horizontal lanes, stand, labels and edit round trips passed');
