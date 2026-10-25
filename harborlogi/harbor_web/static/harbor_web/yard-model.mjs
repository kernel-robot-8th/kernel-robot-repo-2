/** Browser-local logical placement only; no physical stability or path guarantee. */
export const cloneBoxes = boxes => structuredClone(boxes);

export function transit(from, to, progress, ceiling) {
  const p = Math.max(0, Math.min(1, progress));
  if (p === 0) return structuredClone(from);
  if (p === 1 || (from.x === to.x && from.y === to.y && from.z === to.z)) return structuredClone(to);
  const ease = t => t * t * (3 - 2 * t);
  const horizontal = ease(Math.max(0, Math.min(1, (p - .25) / .5)));
  const z = p < .25 ? from.z + (ceiling - from.z) * ease(p / .25)
    : p > .75 ? ceiling + (to.z - ceiling) * ease((p - .75) / .25) : ceiling;
  return {...structuredClone(from), x:from.x + (to.x - from.x) * horizontal,
    y:from.y + (to.y - from.y) * horizontal, z};
}

const positiveInteger = value => Number.isInteger(value) && value > 0;
const fail = reason => ({valid:false, reason});
const key = (area, row, column, level) => JSON.stringify([area,row,column,level]);

export function validatePlacement(areas, boxes) {
  const byArea = new Map();
  for (const area of areas) {
    if (!area.id || byArea.has(area.id) || ![area.x,area.y].every(Number.isFinite)
        || ![area.rows,area.columns,area.levels].every(positiveInteger)) {
      return fail('구역 정보가 올바르지 않습니다.');
    }
    byArea.set(area.id,area);
  }
  const occupied = new Map(), ids = new Set();
  for (const box of boxes) {
    if (!box.id || ids.has(box.id)) return fail('화물 ID가 없거나 중복되었습니다.');
    ids.add(box.id);
    const area = byArea.get(box.area);
    if (!area) return fail(`${box.id}: 구역을 찾을 수 없습니다.`);
    if (![box.row,box.column,box.level,box.width,box.depth].every(positiveInteger)) {
      return fail(`${box.id}: 행·열·층·점유 크기는 1 이상의 정수여야 합니다.`);
    }
    if (![box.x,box.y,box.z].every(Number.isFinite)
        || box.x !== area.x + box.column - 1 || box.y !== area.y + box.row - 1 || box.z !== box.level - 1) {
      return fail(`${box.id}: 표시 좌표가 구역·행·열·층의 위치와 일치하지 않습니다.`);
    }
    if (![0,90].includes(box.orientation) || Math.min(box.width,box.depth) !== 1
        || Math.max(box.width,box.depth) > 3 || (box.orientation === 0 && box.depth !== 1)
        || (box.orientation === 90 && box.width !== 1)) return fail(`${box.id}: 방향 또는 점유 크기가 올바르지 않습니다.`);
    if (box.row + box.depth - 1 > area.rows || box.column + box.width - 1 > area.columns
        || box.level > area.levels) return fail(`${box.id}: 전체 점유 면적이 구역 경계를 벗어납니다.`);
    for (let row=box.row; row<box.row+box.depth; row++) {
      for (let column=box.column; column<box.column+box.width; column++) {
        const cell=key(box.area,row,column,box.level);
        if (occupied.has(cell)) return fail(`${box.id}: ${occupied.get(cell)} 화물과 점유 면적이 겹칩니다.`);
        occupied.set(cell,box.id);
      }
    }
  }
  for (const box of boxes) {
    if (box.level === 1) continue;
    for (let row=box.row; row<box.row+box.depth; row++) {
      for (let column=box.column; column<box.column+box.width; column++) {
        if (!occupied.has(key(box.area,row,column,box.level-1))) {
          return fail(`${box.id}: 아래층이 전체 점유 면적을 지지하지 않습니다.`);
        }
      }
    }
  }
  return {valid:true,reason:''};
}

export function moveBox(areas, boxes, id, placement) {
  const original = cloneBoxes(boxes);
  const reject = reason => ({valid:false,reason,boxes:original});
  const index = boxes.findIndex(box => box.id === id);
  if (index < 0) return reject('이동할 화물을 찾을 수 없습니다.');
  const initial = validatePlacement(areas,boxes);
  if (!initial.valid) return reject(initial.reason);
  const area = areas.find(item => item.id === placement.area);
  if (!area) return reject('이동할 구역을 찾을 수 없습니다.');
  if (![placement.row,placement.column,placement.level].every(positiveInteger)) {
    return reject('행·열·층은 1 이상의 정수여야 합니다.');
  }
  if (![0,90].includes(placement.orientation)) return reject('방향은 0도 또는 90도로 지정해야 합니다.');
  const draft = cloneBoxes(boxes), length = Math.max(boxes[index].width,boxes[index].depth);
  draft[index] = {...draft[index], area:area.id,row:placement.row,column:placement.column,level:placement.level,
    orientation:placement.orientation,width:placement.orientation === 90 ? 1 : length,
    depth:placement.orientation === 90 ? length : 1,
    x:area.x+placement.column-1,y:area.y+placement.row-1,z:placement.level-1};
  const result = validatePlacement(areas,draft);
  return result.valid ? {...result,boxes:draft} : reject(result.reason);
}

export function placementFromWorld(areas, box, {x,y,z}) {
  if (![x,y,z].every(Number.isFinite)) return null;
  for (const area of areas) {
    const column=Math.round(x-area.x)+1, row=Math.round(y-area.y)+1;
    if (column >= 1 && column <= area.columns && row >= 1 && row <= area.rows) {
      return {area:area.id,row,column,level:Math.round(z)+1,orientation:box.orientation};
    }
  }
  return null;
}
