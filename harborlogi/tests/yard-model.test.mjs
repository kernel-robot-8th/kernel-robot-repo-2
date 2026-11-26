import assert from 'node:assert/strict';
import {cloneBoxes, transit, validatePlacement, moveBox, dropTargetFromWorld} from '../harbor_web/static/harbor_web/yard-model.mjs';

const areas = [{id:'yard',x:0,y:0,rows:3,columns:4,levels:3},
  {id:'stand',x:6,y:0,rows:3,columns:1,levels:2}];
const box = (id, overrides={}) => ({id,area:'yard',row:1,column:1,level:1,
  width:1,depth:1,x:0,y:0,z:0,orientation:0,size:'1×1',role:'cargo',label:id,
  positions:['source-bay'],footprint_revision:7,...overrides});
const place = (overrides={}) => ({area:'yard',row:2,column:2,level:1,orientation:0,...overrides});
const source = [box('A')];
const before = structuredClone(source);
const moved = moveBox(areas,source,'A',place());
assert.equal(moved.valid,true);
assert.deepEqual([moved.boxes[0].x,moved.boxes[0].y,moved.boxes[0].z],[1,1,0]);
assert.deepEqual(moved.boxes[0].positions,['yard · 2행 2열 1층']);
assert.equal(moved.boxes[0].footprint_revision,7);
moved.boxes[0].positions.push('local');
assert.deepEqual(source,before);
const copy=cloneBoxes(source); copy[0].positions.push('copy'); assert.deepEqual(source,before);

function rejected(boxes, id, placement) {
  const snapshot=structuredClone(boxes);
  const result=moveBox(areas,boxes,id,placement);
  assert.equal(result.valid,false,JSON.stringify(placement));
  assert(result.reason.length>0);
  assert.deepEqual(result.boxes,snapshot);
  assert.notEqual(result.boxes,boxes);
  if(result.boxes[0]) result.boxes[0].positions.push('rejected-copy');
  assert.deepEqual(boxes,snapshot);
}
for (const changes of [{row:0},{column:5},{level:4},{row:1.2},{column:NaN},
  {level:Infinity},{orientation:45},{area:'missing'}]) rejected(source,'A',place(changes));
rejected(source,'missing',place());
rejected(source,'A',place({level:2}));
rejected([box('A'),box('B',{row:2,column:2,x:1,y:1})],'A',place());
const stack=[box('A'),box('B',{level:2,z:1})];
assert.equal(validatePlacement(areas,stack).valid,true);
rejected(stack,'A',place());
const partial=[box('base'),box('top',{width:2,size:'2×1',level:2,z:1})];
assert.equal(validatePlacement(areas,partial).valid,false);
const supported=[box('base'),box('base2',{column:2,x:1}),partial[1]];
assert.equal(validatePlacement(areas,supported).valid,true);
for(const length of [2,3]) {
  const long=[box('long',{width:length,size:`${length}×1`})];
  rejected(long,'long',place({area:'stand',row:1,column:1}));
  const rotated=moveBox(areas,long,'long',place({area:'stand',row:1,column:1,orientation:90}));
  assert.equal(rotated.valid,true);
  assert.deepEqual([rotated.boxes[0].width,rotated.boxes[0].depth],[1,length]);
  const back=moveBox(areas,rotated.boxes,'long',place());
  assert.equal(back.valid,true);
  assert.deepEqual([back.boxes[0].width,back.boxes[0].depth],[length,1]);
}
assert.equal(validatePlacement(areas,[box('A',{width:NaN})]).valid,false);
assert.equal(validatePlacement(areas,[box('A',{row:1.5})]).valid,false);
assert.equal(validatePlacement(areas,[box('A'),box('A',{column:2})]).valid,false);
for (const coordinates of ['x','y','z']) {
  for (const value of [NaN,Infinity,-Infinity,0.5,1]) {
    const inconsistent=[box('A',{[coordinates]:value})];
    assert.equal(validatePlacement(areas,inconsistent).valid,false,`${coordinates}=${value}`);
    rejected(inconsistent,'A',place());
  }
}
for (const shape of [{orientation:0,width:1,depth:2},{orientation:90,width:2,depth:1}]) {
  const inconsistent=[box('A',shape)];
  assert.equal(validatePlacement(areas,inconsistent).valid,false);
  rejected(inconsistent,'A',place());
}
assert.equal(validatePlacement(areas,[box('A',{orientation:90,width:1,depth:3})]).valid,true);
assert.equal(validatePlacement(areas,[box('A',{area:'stand',x:6})]).valid,true);
const frozen=[Object.freeze({...box('frozen'),positions:Object.freeze(['source-bay'])})];
Object.freeze(frozen);
assert.equal(moveBox(areas,frozen,'frozen',place()).valid,true);
const from=box('A',{width:2,size:'2×1'}),to={...from,x:6,y:2,z:1,width:1,depth:2,orientation:90};
assert.deepEqual(transit(from,to,0,4),from);
assert.deepEqual(transit(from,to,1,4),to);
assert.equal(transit(from,to,.2,4).x,0);
assert.equal(transit(from,to,.5,4).z,4);
assert.equal(transit(from,to,.8,4).x,6);
assert.equal(transit(from,to,.5,4).width,2);
assert.equal(transit(from,to,.5,4).orientation,0);
assert.deepEqual(transit(from,from,.5,4),from);
const animated=transit(from,to,.5,4);
animated.positions.push('local-animation');
assert.deepEqual(from.positions,['source-bay']);
assert.deepEqual(to.positions,['source-bay']);

// Free plane dragging: floor coordinate -> cell snap, top empty level, invalid red cell判定.
const dragBoxes = [
  box('A', {row:1, column:1, level:1, x:0, y:0, z:0}),
  box('B', {row:2, column:3, level:1, x:2, y:1, z:0}),
  box('C', {row:2, column:3, level:2, x:2, y:1, z:1}),
  box('D', {row:3, column:4, level:1, x:3, y:2, z:0}),
  box('E', {row:3, column:4, level:2, x:3, y:2, z:1}),
  box('F', {row:3, column:4, level:3, x:3, y:2, z:2}),
];
// Empty cell -> level 1, valid.
const emptyDrop = dropTargetFromWorld(areas, dragBoxes, 'A', {x:1.4, y:2.6});
assert.deepEqual(emptyDrop.placement, {area:'yard', row:3, column:2, level:1, orientation:0});
assert.equal(emptyDrop.valid, true);
assert.equal(emptyDrop.z, 0);
// Occupied 2-high cell -> auto stacks on level 3, valid.
const stackDrop = dropTargetFromWorld(areas, dragBoxes, 'A', {x:2.5, y:1.5});
assert.deepEqual(stackDrop.placement, {area:'yard', row:2, column:3, level:3, orientation:0});
assert.equal(stackDrop.valid, true);
assert.equal(stackDrop.z, 2);
// Full 3-high cell -> level 4 > levels, invalid.
const fullDrop = dropTargetFromWorld(areas, dragBoxes, 'A', {x:3.5, y:2.5});
assert.deepEqual(fullDrop.placement, {area:'yard', row:3, column:4, level:4, orientation:0});
assert.equal(fullDrop.valid, false);
assert.ok(fullDrop.reason.length > 0);
assert.equal(fullDrop.visualZ, 3);
// Buried cargo B (under C) cannot move to an empty cell.
const buriedDrop = dropTargetFromWorld(areas, dragBoxes, 'B', {x:1.5, y:0.5});
assert.equal(buriedDrop.valid, false);
// Stand area drop -> valid when empty, invalid when full.
const standDrop = dropTargetFromWorld(areas, dragBoxes, 'A', {x:6.4, y:1.7});
assert.deepEqual(standDrop.placement, {area:'stand', row:2, column:1, level:1, orientation:0});
assert.equal(standDrop.valid, true);
// Out of bounds gap or NaN -> null.
assert.equal(dropTargetFromWorld(areas, dragBoxes, 'A', {x:5.2, y:1.0}), null);
assert.equal(dropTargetFromWorld(areas, dragBoxes, 'A', {x:NaN, y:0}), null);

// Slot site (3x6 yard + temp_a + temp_b + stand, 1x3 containers, slotCells=3).
const slotAreas = [
  {id:'yard', x:0, y:0, rows:3, columns:6, levels:3},
  {id:'temp_a', x:0, y:-4.2, rows:3, columns:3, levels:3},
  {id:'temp_b', x:0, y:4.2, rows:3, columns:3, levels:3},
  {id:'stand', x:7.2, y:-4.2, rows:1, columns:3, levels:1},
];
const slotBox = (id, area, row, column, level) => {
  const a = slotAreas.find(item => item.id === area);
  return box(id, {area, row, column, level, width:3, depth:1, size:'3×1', x:a.x + column - 1, y:a.y + row - 1, z:level - 1});
};
const slotBoxes = [
  slotBox('C-1', 'yard', 1, 1, 1),
  slotBox('C-2', 'yard', 1, 1, 2),
  slotBox('C-9', 'yard', 1, 1, 3),
  slotBox('C-12', 'yard', 2, 4, 1),
  slotBox('C-13', 'yard', 3, 1, 1),
  slotBox('C-14', 'yard', 3, 1, 2),
  slotBox('C-15', 'yard', 3, 1, 3),
];
// Diagonal move C-9 from (1,1,3) to (2,4) -> snaps to column 4, level 2 above C-12.
const diagDrop = dropTargetFromWorld(slotAreas, slotBoxes, 'C-9', {x:4.6, y:1.5}, {slotCells:3});
assert.deepEqual(diagDrop.placement, {area:'yard', row:2, column:4, level:2, orientation:0});
assert.equal(diagDrop.valid, true);
// Drop onto temp_a lane 2, temp_b lane 3, stand -> all valid at level 1.
const tempADrop = dropTargetFromWorld(slotAreas, slotBoxes, 'C-9', {x:1.5, y:-2.7}, {slotCells:3});
assert.deepEqual(tempADrop.placement, {area:'temp_a', row:2, column:1, level:1, orientation:0});
assert.equal(tempADrop.valid, true);
const tempBDrop = dropTargetFromWorld(slotAreas, slotBoxes, 'C-9', {x:0.4, y:6.4}, {slotCells:3});
assert.deepEqual(tempBDrop.placement, {area:'temp_b', row:3, column:1, level:1, orientation:0});
assert.equal(tempBDrop.valid, true);
const slotStandDrop = dropTargetFromWorld(slotAreas, slotBoxes, 'C-9', {x:8.5, y:-3.7}, {slotCells:3});
assert.deepEqual(slotStandDrop.placement, {area:'stand', row:1, column:1, level:1, orientation:0});
assert.equal(slotStandDrop.valid, true);
// Full 3-high slot (3,1) -> invalid.
const slotFullDrop = dropTargetFromWorld(slotAreas, slotBoxes, 'C-9', {x:1.5, y:2.5}, {slotCells:3});
assert.equal(slotFullDrop.valid, false);
assert.equal(slotFullDrop.placement.level, 4);
// Occupied stand (levels=1) -> invalid.
const occupiedStandDrop = dropTargetFromWorld(
  slotAreas,
  [...slotBoxes, slotBox('S-1', 'stand', 1, 1, 1)],
  'C-9',
  {x:8.5, y:-3.7},
  {slotCells:3},
);
assert.equal(occupiedStandDrop.valid, false);
assert.equal(occupiedStandDrop.placement.level, 2);

console.log('Yard draft bounds, occupancy, support, rotation, immutability, free drag and animation checks passed');

// motionPath: sampled lift-move-lower arc used for the 3D route line.
import {motionPath} from '../harbor_web/static/harbor_web/yard-model.mjs';
{
  const from = {id:'M', x:0, y:0, z:0, width:1, depth:1};
  const to = {id:'M', x:4, y:2, z:1, width:1, depth:1};
  const path = motionPath(from, to, 3.6, 24);
  assert.equal(path.length, 25);
  assert.deepEqual(path[0], {x:0, y:0, z:0});
  assert.deepEqual(path.at(-1), {x:4, y:2, z:1});
  assert.equal(path[12].z, 3.6);
  assert.ok(path.every(point => point.z <= 3.6 + 1e-9));
  assert.deepEqual(motionPath(null, to, 3), []);
  assert.equal(motionPath(from, to, 3, 1).length, 3);
  console.log('motionPath arc checks passed');
}
