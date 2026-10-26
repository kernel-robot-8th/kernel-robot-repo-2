import assert from 'node:assert/strict';
import {DraftSession} from '../harbor_web/static/harbor_web/yard-draft.mjs';
import {moveBox} from '../harbor_web/static/harbor_web/yard-model.mjs';

const areas = [{id:'yard',x:0,y:0,rows:3,columns:3,levels:3,addresses:{'2,1,1':'Y21','2,2,1':'Y22'}},
  {id:'temp',x:5,y:0,rows:1,columns:3,levels:2}];
const base = [{id:'wide',area:'yard',row:1,column:1,level:1,width:2,depth:1,orientation:0,
  x:0,y:0,z:0,size:'2×1',positions:['Y11','Y12'],footprint_revision:1},
  {id:'top',area:'yard',row:1,column:1,level:2,width:1,depth:1,orientation:0,
    x:0,y:0,z:1,size:'1×1',positions:['Y11-L2'],footprint_revision:2}];
const pinned = structuredClone(base);
const placement = (area,row,column,level=1) => ({area,row,column,level,orientation:0});
const session = new DraftSession(areas,base);
assert.equal(session.move('wide',placement('yard',2,1)).valid,false);
assert.equal(session.cursor,0);
assert.equal(session.move('top',placement('temp',1,1)).valid,true);
assert.equal(session.move('wide',placement('yard',2,1)).valid,true);
assert.deepEqual(session.boxes.find(b=>b.id==='wide').positions,['Y21','Y22']);
assert.equal(session.changes.length,2);
assert.equal(session.undo(),true);
assert.equal(session.changes.length,1);
assert.equal(session.redo(),true);
session.undo();
assert.equal(session.move('wide',placement('yard',3,1)).valid,true);
assert.equal(session.redo(),false);
assert.equal(session.frames.length,3);
const second = moveBox(areas,base,'top',placement('temp',1,2)).boxes;
const final = moveBox(areas,second,'wide',placement('yard',2,1)).boxes;
const recommended = [base,second,final];
const loaded = new DraftSession(areas,base);
assert.equal(loaded.load(recommended).valid,true);
assert.equal(loaded.cursor,2);
loaded.undo(); assert.equal(loaded.boxes.find(b=>b.id==='wide').row,1);
loaded.redo(); assert.equal(loaded.boxes.find(b=>b.id==='wide').row,2);
const prior = structuredClone(loaded.frames);
assert.equal(loaded.load([base,final]).valid,false,'Simultaneous unsupported shortcut must be rejected');
const corrupted = structuredClone(recommended); corrupted[2][0].footprint_revision=99;
assert.equal(loaded.load(corrupted).valid,false);
assert.deepEqual(loaded.frames,prior,'Rejected import is atomic');
assert.deepEqual(base,pinned);
loaded.boxes[0].positions.push('local');
assert.deepEqual(base,pinned);
console.log('Draft history: validated moves, pinned footprint, atomic plan load, undo/redo branch, canonical addresses and source immutability passed');
