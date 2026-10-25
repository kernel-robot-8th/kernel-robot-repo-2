import {cloneBoxes, transit, moveBox, placementFromWorld} from './yard-model.mjs';

function fallback(figure) {
  const preview = figure.closest('[data-yard-preview]');
  if (document.fullscreenElement === figure) document.exitFullscreen().catch(() => {});
  figure.dataset.ready = 'false'; figure.dataset.renderer = 'unavailable';
  figure.hidden = true;
  preview.querySelector('[data-yard-flat]').hidden = false;
  preview.querySelector('[data-yard-views]').hidden = true;
  preview.querySelector('[data-yard-3d-fallback]').hidden = false;
}

export async function mountYardViewer(figure) {
  const {createRenderer} = await import('./yard-renderer.js');
  const preview = figure.closest('[data-yard-preview]');
  const scene = JSON.parse(document.getElementById(figure.dataset.scene).textContent);
  const canvas = figure.querySelector('canvas');
  const flat = preview.querySelector('[data-yard-flat]');
  const views = preview.querySelector('[data-yard-views]');
  const controls = [...preview.querySelectorAll('[data-yard-stage]')];
  const cargo = figure.querySelector('[data-scene-cargo]');
  const info = figure.querySelector('[data-scene-info]');
  const play = figure.querySelector('[data-scene-play]');
  const motion = figure.querySelector('[data-scene-motion]');
  const editor = figure.querySelector('[data-scene-editor]');
  const editButton = figure.querySelector('[data-scene-action="edit"]');
  const draftState = figure.querySelector('[data-scene-draft]');
  const error = figure.querySelector('[data-scene-error]');
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  const events = new AbortController();
  const on = (node, type, fn) => node.addEventListener(type, fn, {signal: events.signal});
  const palette = getComputedStyle(document.documentElement);
  const token = name => palette.getPropertyValue(name).trim();
  const colors = {target: token('--primitive-color-navy'), blocker: token('--primitive-color-orange'),
    stored: token('--primitive-color-teal'), line: token('--semantic-border-divider'),
    text: token('--semantic-text-primary'), muted: token('--semantic-text-secondary'),
    floor: token('--semantic-surface-panel'), white: token('--primitive-color-white'),
    canvas: token('--semantic-surface-canvas')};
  let index = Number(controls.find(b => b.getAttribute('aria-pressed') === 'true')?.dataset.yardStage || 0);
  let selected = '', current = cloneBoxes(scene.frames[index].boxes), draft = null;
  let playing = false, internalStep = false, isolated = false, timer = 0, raf = 0, animation = null;
  let destroyed = false;
  const ceiling = Math.max(...scene.areas.map(a => a.levels)) + .6;
  let renderer = null;
  renderer = createRenderer(canvas, scene.areas, {
    colors, onSelect: id => select(id), onTransform: (id, position) => {
      if (!draft) return;
      const box = draft.find(b => b.id === id);
      const placement = placementFromWorld(scene.areas, box, position);
      if (placement) applyDraft(id, placement);
      else { error.textContent = '구역 안의 격자 위치로 옮겨주세요.'; renderer.setBoxes(draft); }
    }, onError: () => { destroy(); fallback(figure); },
  });
  function stop() {
    playing = false; clearTimeout(timer);
    play.textContent = '계획 재생'; play.setAttribute('aria-pressed', 'false');
  }
  function status(text) { motion.textContent = text; }
  function boxes() { return draft || scene.frames[index].boxes; }
  function changedCount() {
    return draft ? draft.filter(box => {
      const original = scene.frames[index].boxes.find(b => b.id === box.id);
      return ['area','row','column','level','orientation'].some(key => box[key] !== original[key]);
    }).length : 0;
  }
  function updateDraftState() {
    draftState.hidden = !draft;
    draftState.textContent = draft ? '가상 배치 · 변경 ' + changedCount() + '개 · 서버에 저장되지 않음' : '';
    figure.dataset.editing = String(Boolean(draft));
    figure.dataset.draftChanges = String(changedCount());
  }
  function details() {
    const box = boxes().find(b => b.id === selected);
    const base = scene.frames[index].boxes.find(b => b.id === selected);
    cargo.value = box ? box.id : '';
    info.textContent = box ? box.id + ' · ' + box.label + ' · ' + box.size + ' · ' + box.orientation + '도 · ' +
      (draft ? '가상: ' + box.area + ' / ' + box.row + '행 ' + box.column + '열 ' + box.level + '층 · 기준: ' + base.positions.join(' / ') : box.positions.join(' / '))
      : '박스나 화물 목록을 선택해 크기·위치를 확인하세요.';
    for (const name of ['area','row','column','level','orientation']) editor.elements[name].value = box ? box[name] : '';
    editor.querySelector('fieldset').disabled = !box;
    figure.querySelector('[data-scene-action="focus"]').disabled = !box;
    figure.querySelector('[data-scene-action="isolate"]').disabled = !box;
    updateDraftState();
  }
  function select(id) {
    selected = id || ''; renderer.select(selected);
    if (!selected && isolated) {
      isolated = false; figure.querySelector('[data-scene-action="isolate"]').setAttribute('aria-pressed', 'false'); renderer.isolate(null);
    }
    if (isolated) renderer.isolate(selected || null);
    details();
  }
  function fillCargo() {
    cargo.replaceChildren(new Option('화물 선택', ''));
    boxes().forEach(box => cargo.add(new Option(box.id + ' · ' + box.size + ' · ' + box.label, box.id)));
    details();
  }
  function paint(values) {
    current = cloneBoxes(values); renderer.setBoxes(current);
    canvas.dataset.boxCount = String(current.length); canvas.dataset.frame = String(index);
  }
  function applyDraft(id, placement) {
    const result = moveBox(scene.areas, draft, id, placement);
    error.textContent = result.valid ? '' : result.reason;
    if (result.valid) {
      draft = result.boxes; status(id + ' 가상 위치 변경 · 논리 점유·지지 검사 통과');
    }
    paint(draft); select(id);
  }
  function lockDraft(value) {
    controls.forEach(button => { button.disabled = value; });
    play.disabled = value;
    views.querySelector('[data-yard-view="flat"]').disabled = value;
    editButton.setAttribute('aria-pressed', String(value));
    editButton.textContent = value ? '원본 복원·편집 끝내기' : '가상 배치 편집';
    editor.hidden = !value; renderer.setEditable(value);
  }
  function toggleEdit() {
    stop(); cancelAnimationFrame(raf); animation = null; canvas.dataset.moving = 'false';
    if (draft) {
      draft = null; lockDraft(false); paint(scene.frames[index].boxes);
      status('가상 변경을 버리고 선택한 기준 배치를 복원했습니다.');
    } else {
      draft = cloneBoxes(scene.frames[index].boxes); lockDraft(true); paint(draft);
      status('박스를 선택하고 이동 핸들을 드래그하거나 아래 위치를 입력하세요.');
    }
    error.textContent = ''; fillCargo(); select(selected);
  }
  function advance() {
    if (!playing) return;
    if (index === scene.frames.length - 1) { stop(); status('계획 재생 완료 · 실제 실행 상태는 바뀌지 않았습니다.'); return; }
    timer = setTimeout(() => {
      if (!playing || document.hidden || figure.hidden) return;
      internalStep = true; controls[index + 1].click(); internalStep = false;
    }, reduced.matches ? 1000 : 550);
  }
  function tick(now) {
    raf = 0;
    if (!animation || destroyed) return;
    const t = Math.min(1, (now - animation.started) / 1100);
    paint(animation.to.map(to => transit(animation.from.get(to.id) || to, to, t, ceiling)));
    if (t < 1) raf = requestAnimationFrame(tick);
    else { animation = null; canvas.dataset.moving = 'false'; status(scene.frames[index].label + ' · 예상 배치'); advance(); }
  }
  function selectFrame(next, animate = true) {
    if (draft || !scene.frames[next]) return;
    if (!internalStep) stop();
    cancelAnimationFrame(raf); raf = 0;
    const from = new Map(current.map(b => [b.id, {...b}]));
    index = next; fillCargo();
    figure.querySelector('[data-scene-step]').textContent = scene.frames[index].label;
    if (animate && next && !reduced.matches && !figure.hidden && !document.hidden) {
      animation = {from, to: scene.frames[index].boxes, started: performance.now()};
      canvas.dataset.moving = 'true';
      status('박스 이동 설명 중 · 실제 로봇 경로 아님'); raf = requestAnimationFrame(tick);
    } else {
      animation = null; canvas.dataset.moving = 'false'; paint(scene.frames[index].boxes);
      status(next ? scene.frames[index].label + ' · 예상 배치' : '원본 관측 배치'); advance();
    }
  }
  function showFlat(value) {
    if (draft) return;
    stop(); selectFrame(index, false);
    figure.hidden = value; flat.hidden = !value;
    views.querySelectorAll('[data-yard-view]').forEach(button => button.setAttribute('aria-pressed', String((button.dataset.yardView === 'flat') === value)));
    if (!value) renderer.resize();
  }
  scene.areas.forEach(area => editor.elements.area.add(new Option(area.label + ' · ' + area.id, area.id)));
  on(editor, 'submit', event => {
    event.preventDefault();
    if (!draft || !selected) return;
    applyDraft(selected, {area: editor.elements.area.value, row: Number(editor.elements.row.value),
      column: Number(editor.elements.column.value), level: Number(editor.elements.level.value),
      orientation: Number(editor.elements.orientation.value)});
  });
  on(cargo, 'change', () => { error.textContent = ''; select(cargo.value); });
  on(play, 'click', () => {
    if (playing) { stop(); selectFrame(index, false); return; }
    playing = true; play.textContent = '재생 중지'; play.setAttribute('aria-pressed', 'true');
    if (index === scene.frames.length - 1) { internalStep = true; controls[0].click(); internalStep = false; }
    else if (!animation) advance();
  });
  figure.querySelectorAll('[data-scene-camera]').forEach(button => on(button, 'click', () => renderer.view(button.dataset.sceneCamera)));
  figure.querySelectorAll('[data-scene-zoom]').forEach(button => on(button, 'click', () => renderer.zoom(Number(button.dataset.sceneZoom) > 0 ? 1.22 : .82)));
  views.querySelectorAll('[data-yard-view]').forEach(button => on(button, 'click', () => showFlat(button.dataset.yardView === 'flat')));
  figure.querySelectorAll('[data-scene-action]').forEach(button => on(button, 'click', async () => {
    const action = button.dataset.sceneAction;
    if (action === 'edit') toggleEdit();
    if (action === 'focus') renderer.fit(selected);
    if (action === 'fit') renderer.fit();
    if (action === 'isolate') {
      isolated = !isolated; button.setAttribute('aria-pressed', String(isolated)); renderer.isolate(isolated ? selected : null);
    }
    if (action === 'orbit' || action === 'pan') {
      renderer.setMode(action);
      ['orbit','pan'].forEach(mode => figure.querySelector('[data-scene-action="' + mode + '"]').setAttribute('aria-pressed', String(mode === action)));
    }
    if (action === 'fullscreen') {
      try { if (document.fullscreenElement === figure) await document.exitFullscreen(); else await figure.requestFullscreen(); }
      catch (_) { error.textContent = '브라우저에서 전체 화면을 허용하지 않습니다.'; }
    }
  }));
  on(document, 'fullscreenchange', () => { renderer.resize(); figure.querySelector('[data-scene-action="fullscreen"]').setAttribute('aria-pressed', String(document.fullscreenElement === figure)); });
  on(preview, 'yardstagechange', event => selectFrame(Number(event.detail.index)));
  on(document, 'visibilitychange', () => { if (document.hidden) { stop(); if (!draft) selectFrame(index, false); } });
  on(reduced, 'change', () => { stop(); if (!draft) selectFrame(index, false); });
  on(window, 'pagehide', event => { stop(); if (!event.persisted) destroy(); });
  on(window, 'pageshow', () => renderer.resize());
  on(canvas, 'webglcontextlost', event => { event.preventDefault(); destroy(); fallback(figure); });
  function destroy() {
    if (destroyed) return;
    destroyed = true; stop(); cancelAnimationFrame(raf); events.abort();
    controls.forEach(button => { button.disabled = false; }); renderer?.dispose();
  }
  play.hidden = scene.frames.length < 2 || controls.length !== scene.frames.length;
  figure.querySelector('[data-scene-action="fullscreen"]').hidden = !document.fullscreenEnabled;
  paint(current); fillCargo(); views.hidden = false; figure.hidden = false; flat.hidden = true;
  canvas.dataset.moving = 'false'; figure.dataset.ready = 'true'; figure.dataset.renderer = 'webgl'; renderer.resize(); renderer.fit();
  return {destroy};
}

if (typeof document !== 'undefined') document.querySelectorAll('[data-yard-3d]').forEach(figure => {
  mountYardViewer(figure).catch(() => fallback(figure));
});
