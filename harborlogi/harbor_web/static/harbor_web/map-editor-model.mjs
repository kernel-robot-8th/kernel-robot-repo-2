export function resizeCells(cells, rows, columns) {
  if (![rows,columns].every(n => Number.isInteger(n) && n >= 1 && n <= 12)) throw new Error('행·열은 1~12 사이 정수로 입력하세요.');
  const retained = cells.filter(c => c.row <= rows && c.column <= columns);
  const used = new Set(retained.map(c => c.bay_id));
  return Array.from({length:rows},(_,r) => Array.from({length:columns},(_,c) => {
    const row=r+1, column=c+1, existing=retained.find(v => v.row===row && v.column===column);
    if (existing) return {...existing};
    let bay_id=`YARD-R${row}C${column}`, suffix=1;
    while (used.has(bay_id)) bay_id=`YARD-R${row}C${column}-${suffix++}`;
    used.add(bay_id);
    return {bay_id,row,column};
  })).flat();
}

export function addArea(map) {
  if (map.areas.length >= 16) throw new Error('구역은 최대 16개까지 편집할 수 있습니다.');
  let number=1;
  while (map.areas.some(a=>a.id===`storage-${number}`)) number++;
  const x=Math.max(...map.areas.map(a=>a.x+a.columns))+2;
  return {areas:[...map.areas.map(a=>structuredClone(a)),{id:`storage-${number}`,label:`추가 보관 ${number}`,
    role:'storage',rows:3,columns:3,levels:3,x,y:0,cells:[]}]};
}

export function sceneAreas(map) {
  return map.areas.map(({id,label,rows,columns,levels,x,y})=>({id,label,rows,columns,levels,x,y}));
}
