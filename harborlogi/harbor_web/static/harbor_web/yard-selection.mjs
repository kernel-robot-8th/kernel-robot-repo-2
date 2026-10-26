/** Logical selection shared by the two browser-local views. */
export function selectionFor(box) {
  if (!box) return null;
  const coveredPositions = [];
  for (let row = box.row; row < box.row + box.depth; row++)
    for (let column = box.column; column < box.column + box.width; column++)
      coveredPositions.push({area:box.area, row, column, level:box.level});
  return {cargoId:box.id, area:box.area, row:box.row, column:box.column,
    level:box.level, orientation:box.orientation, coveredPositions};
}

export function atPosition(boxes, position) {
  return boxes.find(box => box.area === position.area && box.level === position.level &&
    position.row >= box.row && position.row < box.row + box.depth &&
    position.column >= box.column && position.column < box.column + box.width);
}

export function mountLinkedGrid(preview, scene, onSelect, inspectDestination = () => null) {
  const original = preview.querySelector('[data-yard-flat]');
  const grid = document.createElement('div');
  grid.dataset.yardLinkedFlat = ''; grid.className = 'linked-flat';
  original.after(grid);
  const levels = new Map(scene.areas.map(area => [area.id, 1]));
  let selected = null, boxes = [], mode = '';
  const address = (area, row, column, level) => area.addresses?.[`${row},${column},${level}`] ||
    `${area.id} · ${row}행 ${column}열 ${level}층`;
  function render() {
    grid.replaceChildren();
    const heading = document.createElement('h3');
    heading.textContent = mode === 'map-definition' ? '맵 정의 · 배치 미관측' :
      mode === 'local-draft' ? '로컬 가상 배치 · 최종 배치 · 서버에 저장되지 않음' :
      mode === 'original' ? '원본 관측 · 층별 평면' : '계획 후 예상 · 층별 평면';
    grid.append(heading); grid.dataset.source = mode;
    if (mode === 'local-draft') {
      const bar = document.createElement('div'); bar.className = 'linked-edit-bar';
      const label = document.createElement('label'); label.textContent = '이동할 화물';
      const picker = document.createElement('select'); picker.dataset.linkedCargo = '';
      picker.add(new Option('화물 선택',''));
      boxes.forEach(box=>picker.add(new Option(`${box.id} · ${box.size}`,box.id)));
      picker.value = selected?.cargoId || '';
      picker.addEventListener('change',()=>{
        const box = boxes.find(box=>box.id===picker.value);
        if (!box) { picker.value = selected?.cargoId || ''; return; }
        onSelect(selectionFor(box));
        grid.querySelector('[data-linked-cargo]')?.focus({preventScroll:true});
      });
      const feedback = document.createElement('p'); feedback.dataset.linkedFeedback = '';
      feedback.setAttribute('role','status');
      feedback.textContent = selected?.cargoId ? `${selected.cargoId} · 목적 칸을 누르면 이동합니다. ‘이동 불가’ 칸은 이유를 확인합니다.` : '먼저 이동할 화물을 선택하세요.';
      label.append(picker); bar.append(label,feedback); grid.append(bar);
    }
    for (const area of scene.areas) {
      const section = document.createElement('section'); section.dataset.gridArea = area.id;
      const title = document.createElement('h4'); title.textContent = `${area.label} · ${area.rows}행 × ${area.columns}열 × ${area.levels}층`;
      const controls = document.createElement('div'); controls.className = 'layer-controls';
      for (let level = 1; level <= area.levels; level++) {
        const button = document.createElement('button'); button.type = 'button'; button.className = 'quiet';
        button.textContent = `${level}층${level === 1 ? ' · 바닥층' : ''}`;
        button.dataset.linkedLevel = level; button.setAttribute('aria-pressed', String(levels.get(area.id) === level));
        button.addEventListener('click', () => { levels.set(area.id, level); render();
          grid.querySelector(`[data-grid-area="${CSS.escape(area.id)}"] [data-linked-level="${level}"]`).focus(); });
        controls.append(button);
      }
      const table = document.createElement('table'); table.className = 'yard-plane';
      const caption = table.createCaption(); caption.textContent = `${levels.get(area.id)}층 · 1행 위쪽 / 1열 왼쪽`;
      const head = table.createTHead().insertRow(); head.append(document.createElement('th'));
      for (let column = 1; column <= area.columns; column++) {
        const th = document.createElement('th'); th.scope = 'col'; th.textContent = `${column}열`; head.append(th);
      }
      const body = table.createTBody();
      for (let row = 1; row <= area.rows; row++) {
        const tr = body.insertRow(), th = document.createElement('th'); th.scope = 'row'; th.textContent = `${row}행`; tr.append(th);
        for (let column = 1; column <= area.columns; column++) {
          const position = {area:area.id, row, column, level:levels.get(area.id)};
          const box = atPosition(boxes, position);
          if (box && (box.row !== row || box.column !== column)) continue;
          const td = tr.insertCell(); td.className = `grid-cell ${box?.role || 'vacant'}`;
          td.colSpan = box?.width || 1; td.rowSpan = box?.depth || 1;
          const button = document.createElement('button'); button.type = 'button'; button.className = 'linked-cell';
          const identity = box ? selectionFor(box) : {...position, cargoId:null, orientation:null, coveredPositions:[position]};
          button.dataset.cargoId = box?.id || ''; button.dataset.selection = JSON.stringify(identity);
          button.dataset.destination = `${row},${column}`;
          const hint = !box ? inspectDestination(position) : null;
          if (hint) {
            button.dataset.dropState = hint.valid ? 'valid' : 'invalid';
            button.title = hint.valid ? '선택 화물을 이 칸으로 이동' : hint.reason;
          }
          const active = selected && (selected.cargoId ? selected.cargoId === box?.id : !box &&
            ['area','row','column','level'].every(key => position[key] === selected[key]));
          button.setAttribute('aria-pressed', String(Boolean(active)));
          const label = document.createElement('strong');
          label.textContent = box ? `${box.id} · ${box.size} · ${box.orientation}도` : hint ? hint.valid ? '여기로 이동' : '이동 불가' : mode === 'map-definition' ? '미관측 위치' : '빈 위치';
          const coordinates = document.createElement('span');
          coordinates.textContent = identity.coveredPositions.map(p => address(area, p.row, p.column, p.level)).join(' / ');
          button.append(label, coordinates);
          button.addEventListener('click', () => {
            onSelect(identity);
            const target = [...grid.querySelectorAll('[data-selection]')].find(node => {
              const value = JSON.parse(node.dataset.selection);
              return identity.cargoId ? value.cargoId === identity.cargoId :
                ['area','row','column','level'].every(key => value[key] === identity[key]);
            });
            target?.focus({preventScroll:true});
          }); td.append(button);
        }
      }
      const wrap = document.createElement('div'); wrap.className = 'linked-plane-scroll'; wrap.append(table);
      section.append(title, controls, wrap); grid.append(section);
    }
  }
  return {
    grid,
    feedback(text) { const node = grid.querySelector('[data-linked-feedback]'); if (node) node.textContent = text; },
    update(values, nextMode, value) {
      boxes = values; mode = nextMode; selected = value; original.hidden = true;
      if (value) levels.set(value.area, value.level);
      preview.dataset.selection = JSON.stringify(value); render();
    },
  };
}
