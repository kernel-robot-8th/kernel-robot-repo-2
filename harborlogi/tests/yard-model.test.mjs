import assert from 'node:assert/strict';
import {cloneBoxes, transit, validatePlacement, moveBox, placementFromWorld} from '../harbor_web/static/harbor_web/yard-model.mjs';

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
assert.deepEqual(moved.boxes[0].positions,['source-bay']);
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
assert.deepEqual(placementFromWorld(areas,source[0],{x:6.1,y:1.2,z:0}),
  {area:'stand',row:2,column:1,level:1,orientation:0});
assert.equal(placementFromWorld(areas,source[0],{x:5,y:0,z:0}),null);
assert.equal(placementFromWorld(areas,source[0],{x:NaN,y:0,z:0}),null);
const edge=placementFromWorld(areas,box('wide',{width:2}),{x:3,y:0,z:0});
assert.equal(edge.column,4);
rejected([box('wide',{width:2})],'wide',edge);
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
console.log('Yard draft bounds, occupancy, support, rotation, immutability and animation checks passed');
