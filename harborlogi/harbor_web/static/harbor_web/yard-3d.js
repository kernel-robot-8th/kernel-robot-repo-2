import {mountLinkedGrid, selectionFor, atPosition, slotLabel, topBoxes} from './yard-selection.mjs';
import {cloneBoxes, transit, moveBox, dropTargetFromWorld} from './yard-model.mjs';
import {DraftSession} from './yard-draft.mjs';
import {applyYardView, bindUpcomingPlans, stageButtonSpecs} from './yard-inline.mjs';
import {describeMoveText, formatBoxInfo, resolvePriorityTier} from './yard-visuals.mjs';
import {playbackDefaults, playButtonState, stepButtonState, stepTarget} from './yard-playback.mjs';

const DEFAULT_PROGRESS_TEXT = '재생하면 화물별 출발→도착을 보여 줍니다.';

function fallback(figure) {
  const preview = figure.closest('[data-yard-preview]');
  if (document.fullscreenElement === figure) document.exitFullscreen().catch(() => {});
  figure.dataset.ready = 'false'; figure.dataset.renderer = 'unavailable';
  figure.hidden = true;
  preview.querySelector('[data-yard-linked-flat]')?.remove();
  preview.classList.remove('yard-linked');
  preview.querySelector('[data-yard-flat]').hidden = false;
  preview.querySelector('[data-yard-views]').hidden = true;
  preview.querySelector('[data-yard-edit-tools]').hidden = true;
  preview.querySelector('[data-yard-3d-fallback]').hidden = false;
}

export async function mountYardViewer(figure) {
  const {createRenderer} = await import('./yard-renderer.js');
  const preview = figure.closest('[data-yard-preview]');
  let scene = JSON.parse(document.getElementById(figure.dataset.scene).textContent);
  const canvas = figure.querySelector('canvas');
  const views = preview.querySelector('[data-yard-views]');
  const stagesHost = preview.querySelector('[data-yard-stages]');
  const statusEl = preview.querySelector('[data-yard-status]');
  let controls = [...preview.querySelectorAll('[data-yard-stage]')];
  const cargo = figure.querySelector('[data-scene-cargo]');
  const info = figure.querySelector('[data-scene-info]');
  const stepHeading = figure.querySelector('strong[data-scene-step]');
  const play = figure.querySelector('[data-scene-play]');
  const stopBtn = figure.querySelector('[data-scene-stop]');
  const prevStepBtn = figure.querySelector('button[data-scene-step="-1"]');
  const nextStepBtn = figure.querySelector('button[data-scene-step="1"]');
  const motion = figure.querySelector('[data-scene-motion]');
  const speed = figure.querySelector('[data-scene-speed]');
  const animateMotion = figure.querySelector('[data-scene-animate]');
  const followMotion = figure.querySelector('[data-scene-follow]');
  const progress = figure.querySelector('[data-scene-progress]');
  const progressRoute = progress?.querySelector('[data-scene-route]');
  const progressBar = progress?.querySelector('progress');
  const progressPhase = progress?.querySelector('[data-scene-phase]');
  const tierLegend = figure.querySelector('[data-scene-tier-legend]') || figure.querySelector('.scene-tier-legend');
  const editor = figure.querySelector('[data-scene-editor]');
  const editTools = preview.querySelector('[data-yard-edit-tools]');
  const editButton = editTools.querySelector('[data-scene-action="edit"]');
  const loadPlan = editTools.querySelector('[data-scene-load-plan]');
  const undo = editTools.querySelector('[data-scene-undo]');
  const redo = editTools.querySelector('[data-scene-redo]');
  const draftState = figure.querySelector('[data-scene-draft]');
  const error = figure.querySelector('[data-scene-error]');
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  const applyMotionDefaults = () => {
    const defaults = playbackDefaults(reduced.matches);
    animateMotion.checked = defaults.animate;
    if (followMotion) followMotion.checked = defaults.follow;
  };
  applyMotionDefaults();
  const events = new AbortController();
  const on = (node, type, fn) => node && node.addEventListener(type, fn, {signal: events.signal});
  const palette = getComputedStyle(document.documentElement);
  const token = name => palette.getPropertyValue(name).trim();
  const colorsFor = targetScene => {
    const cargoNames = Array.isArray(targetScene?.cargo_colors) ? targetScene.cargo_colors : [];
    return {
      target: token('--primitive-color-navy'), blocker: token('--primitive-color-orange'),
      stored: token('--primitive-color-teal'), danger: token('--primitive-color-danger-ink'),
      tierUrgent: token('--primitive-color-danger-ink'), tierPriority: token('--primitive-color-orange-dark'),
      tierNormal: token('--primitive-color-navy'), tierNeutral: token('--primitive-color-muted'),
      cream: token('--primitive-color-cream'),
      cargo: Object.fromEntries(cargoNames.map(name => [name, token(`--semantic-cargo-${name}`)])),
      line: token('--semantic-border-divider'),
      text: token('--semantic-text-primary'), muted: token('--semantic-text-secondary'),
      floor: token('--semantic-surface-panel'), white: token('--primitive-color-white'),
      canvas: token('--semantic-surface-canvas'),
    };
  };
  let index = Number(controls.find(b => b.getAttribute('aria-pressed') === 'true')?.dataset.yardStage || 0);
  let location = null, linked = null;
  let selected = '', current = cloneBoxes(scene.frames[index].boxes), draft = null;
  let playing = false, paused = false, followSuspended = false, previewActive = false;
  let internalStep = false, isolated = false, timer = 0, dwellTimer = 0, raf = 0, animation = null, lastPhase = '';
  let destroyed = false, draftHistory = [], replayIndex = 0, session = null;
  const slotCells = () => scene.slot_cells || 0;
  const place = box => slotCells() ? slotLabel(scene.areas, box, slotCells()) : box.positions.join(' / ');
  const locateBox = box => {
    if (!box) return '';
    return slotCells() ? slotLabel(scene.areas, box, slotCells()) : `${box.area} ${box.row}행 ${box.column}열 ${box.level}층`;
  };
  const isPlanReplay = () => scene.frames.length > 1 && controls.length === scene.frames.length;
  const totalMoves = () => draft ? Math.max(0, draftHistory.length - 1) : Math.max(0, scene.frames.length - 1);

  function syncTierLegend() {
    if (!tierLegend) return;
    const hasPriorityTier = (scene.frames || []).some(frame =>
      (frame.boxes || []).some(box => resolvePriorityTier(box) !== 'neutral'),
    );
    tierLegend.hidden = !hasPriorityTier;
  }

  function syncStepButtons() {
    const isMap = scene.kind === 'map-definition';
    if (stopBtn) {
      stopBtn.hidden = isMap;
      stopBtn.disabled = !(playing || paused || animation);
    }
    if (!prevStepBtn || !nextStepBtn) return;
    prevStepBtn.hidden = isMap;
    nextStepBtn.hidden = isMap;
    const state = draft
      ? stepButtonState({replayable: true, index: replayIndex, total: draftHistory.length})
      : stepButtonState({replayable: isPlanReplay(), index, total: scene.frames.length});
    prevStepBtn.disabled = state.prevDisabled;
    nextStepBtn.disabled = state.nextDisabled;
  }

  function playIdle() {
    play.hidden = scene.kind === 'map-definition';
    play.disabled = draft ? draftHistory.length < 2 : !isPlanReplay();
    const state = playButtonState({active: playing || Boolean(animation), paused, draft: Boolean(draft), planReplay: isPlanReplay()});
    play.textContent = state.label;
    play.setAttribute('aria-pressed', String(state.pressed));
    syncStepButtons();
  }

  function resetProgressCaption({stopped = false} = {}) {
    lastPhase = '';
    if (!progress) return;
    if (stopped) {
      progress.dataset.phase = '중지';
      progress.dataset.progress = '0';
      if (progressRoute) progressRoute.textContent = draft ? '로컬 가상 변경 재생 중지' : DEFAULT_PROGRESS_TEXT;
      if (progressBar) progressBar.value = 0;
      if (progressPhase) {
        progressPhase.hidden = false;
        progressPhase.textContent = '중지';
      }
      return;
    }
    delete progress.dataset.phase;
    delete progress.dataset.progress;
    if (progressRoute) progressRoute.textContent = DEFAULT_PROGRESS_TEXT;
    if (progressBar) progressBar.value = 0;
    if (progressPhase) {
      progressPhase.hidden = true;
      progressPhase.textContent = '';
    }
  }

  function clearPreview() {
    if (!previewActive) return;
    previewActive = false;
    renderer?.setMotion(null);
    if (stepHeading && index === 0 && !draft) {
      stepHeading.textContent = scene.frames[0]?.label || '현재 야드 배치';
    }
    resetProgressCaption();
  }

  const ceiling = () => Math.max(...scene.areas.map(a => a.levels)) + .6;
  const makeRenderer = () => createRenderer(canvas, scene.areas, {
    showVolumes: scene.kind === 'map-definition', transpose: Boolean(slotCells()),
    colors: colorsFor(scene), onSelect: id => { clearPreview(); select(id); },
    onPlaybackKey: key => {
      clearPreview();
      if (key === ' ') play?.click();
      else if (key === '[') stepBy(-1);
      else if (key === ']') stepBy(1);
    },
    onUserCameraStart: () => {
      clearPreview();
      if (playing || animation) followSuspended = true;
    },
    onResolveDrop: (id, point) => {
      if (!draft || playing) return null;
      const target = dropTargetFromWorld(scene.areas, draft, id, point, {slotCells: slotCells()});
      error.textContent = target ? (target.valid ? '' : target.reason) : '구역 안의 칸으로 옮겨주세요.';
      return target;
    },
    onDrop: (id, target) => {
      if (!draft || playing) return;
      if (target?.valid && target.placement) applyDraft(id, target.placement);
      else { error.textContent = target?.reason || '구역 안의 칸으로 옮겨주세요.'; renderer.setBoxes(draft); }
    },
    onError: () => { destroy(); fallback(figure); },
  });
  let currentAreasKey = JSON.stringify(scene.areas);
  let renderer = makeRenderer();

  function stop({showStopped = false} = {}) {
    const wasActive = playing || paused || Boolean(animation);
    if (draft && animation) {
      progress.dataset.phase = '중지 · 최종 가상 배치'; progress.dataset.progress = '100';
      if (progressRoute) progressRoute.textContent = '로컬 가상 변경 재생 중지 · 최종 가상 배치 복원 · 서버에 저장되지 않음';
      if (progressBar) progressBar.value = 100;
      if (progressPhase) { progressPhase.hidden = false; progressPhase.textContent = '중지'; }
    } else if (showStopped || wasActive) {
      resetProgressCaption({stopped: true});
    }
    playing = false; paused = false; followSuspended = false; previewActive = false;
    clearTimeout(timer); clearTimeout(dwellTimer); cancelAnimationFrame(raf); raf = 0; animation = null;
    canvas.dataset.moving = 'false';
    renderer?.setMotion(null);
    playIdle();
    editButton.disabled = scene.kind === 'map-definition';
    if (draft && !destroyed) { renderer?.setEditable(true); editor.querySelector('fieldset').disabled = !selected; paint(draft); }
    updateDraftState();
  }

  function pausePlayback() {
    if (!playing && !animation) return;
    paused = true;
    clearTimeout(timer);
    cancelAnimationFrame(raf);
    raf = 0;
    canvas.dataset.moving = 'false';
    playIdle();
    status('일시정지');
  }

  function resumePlayback() {
    if (!paused) return;
    paused = false;
    playIdle();
    if (animation) {
      animation.last = performance.now();
      const seg = animation.segments[animation.segment];
      canvas.dataset.moving = String(Boolean(seg?.from && !seg?.instant));
      raf = requestAnimationFrame(tick);
    } else if (playing) {
      advance(120);
    }
  }

  function status(text) { motion.textContent = text; }
  function boxes() { return draft || scene.frames[index].boxes; }
  function changedCount() {
    return session ? session.changes.length : 0;
  }
  function updateDraftState() {
    draftState.hidden = !draft;
    draftState.textContent = draft ? '가상 배치 · 변경 ' + changedCount() + '개 · 서버에 저장되지 않음' : '';
    figure.dataset.editing = String(Boolean(draft));
    figure.dataset.draftChanges = String(changedCount());
    undo.disabled = !session || !session.cursor || playing;
    redo.disabled = !session || session.cursor === session.history.length - 1 || playing;
    loadPlan.disabled = Boolean(draft) || playing;
    editTools.querySelector('[data-scene-change-panel]').hidden = !draft;
    editTools.querySelector('[data-scene-change-count]').textContent = String(changedCount());
    const changes = editTools.querySelector('[data-scene-changes]');
    changes.replaceChildren();
    for (const {before,after} of session?.changes || []) {
      const item = document.createElement('li');
      item.textContent = `${after.id} · ${place(before)} → ${place(after)}`;
      changes.append(item);
    }
    editTools.querySelector('[data-scene-edit-help]').textContent = draft ?
      `${selected || '화물 미선택'} · 3D에서 원하는 칸으로 끌거나 평면에서 ${slotCells() ? '‘놓기’를' : '‘여기로 이동’을'} 누르세요.` :
      `화물을 선택하고 가상 배치 편집을 시작하세요.${isPlanReplay() ? ' 저장된 계획 결과도 불러와 수정할 수 있습니다.' : ''}`;
    syncStepButtons();
  }
  const selection = () => selectionFor(boxes().find(b => b.id === selected)) || location;
  const mode = () => scene.kind === 'map-definition' ? 'map-definition' : draft ? 'local-draft' : index ? 'predicted' : 'original';
  function syncLinked() {
    linked?.update(boxes(), mode(), selection(), scene.frames[index].label);
    renderer.selectLocation(location);
    preview.dispatchEvent(new CustomEvent('yardselectionchange', {detail:{selection:selection(), source:mode(), index}}));
  }
  function details() {
    const box = boxes().find(b => b.id === selected);
    const base = scene.frames[index].boxes.find(b => b.id === selected);
    cargo.value = box ? box.id : '';
    const sc = slotCells();
    const where = box && (sc ? (draft ? '가상: ' + slotLabel(scene.areas, box, sc) + ' · 기준: ' + slotLabel(scene.areas, base, sc) : slotLabel(scene.areas, box, sc))
      : box.size + ' · ' + box.orientation + '도 · ' + (draft ? '가상: ' + box.area + ' / ' + box.row + '행 ' + box.column + '열 ' + box.level + '층 · 기준: ' + base.positions.join(' / ') : box.positions.join(' / ')));
    info.textContent = box ? formatBoxInfo(box, {where, rehandle: !draft && Boolean(scene.rehandle?.includes(box.id))})
      : location ? `${scene.kind === 'map-definition' ? '미관측 위치 · 점유 확인 안 됨' : sc ? '빈 자리' : '빈 위치'} · ${sc ? slotLabel(scene.areas, location, sc) : `${location.area} · ${location.row}행 ${location.column}열 ${location.level}층`} · 화물 아님`
        : `박스나 화물 목록을 선택해 ${sc ? '' : '크기·'}위치를 확인하세요.`;
    if (location && scene.kind === 'map-definition') status(info.textContent);
    for (const name of ['area','row','column','level','orientation']) editor.elements[name].value = box ? box[name] : '';
    editor.querySelector('fieldset').disabled = !box || playing;
    figure.querySelector('[data-scene-action="focus"]').disabled = !box;
    figure.querySelector('[data-scene-action="isolate"]').disabled = !box;
    updateDraftState();
  }
  function select(id) {
    location = null;
    selected = id || ''; renderer.select(selected);
    if (!selected && isolated) {
      isolated = false; figure.querySelector('[data-scene-action="isolate"]').setAttribute('aria-pressed', 'false'); renderer.isolate(null);
    }
    if (isolated) renderer.isolate(selected || null);
    details(); syncLinked();
  }
  function fillCargo() {
    const sc = slotCells();
    cargo.replaceChildren(new Option('화물 선택', ''));
    boxes().forEach(box => cargo.add(new Option(box.id + (sc ? '' : ' · ' + box.size) + ' · ' + box.label, box.id)));
    details();
  }
  function paint(values) {
    current = cloneBoxes(values); renderer.setBoxes(current);
    canvas.dataset.boxCount = String(current.length); canvas.dataset.frame = String(index);
  }
  function applyDraft(id, placement) {
    if (playing) return;
    const result = session.move(id, placement);
    error.textContent = result.valid ? '' : result.reason;
    if (result.valid) {
      draftHistory = session.frames;
      replayIndex = draftHistory.length - 1;
      draft = cloneBoxes(session.boxes); playIdle(); status(id + ' 가상 위치 변경 · 논리 점유·지지 검사 통과');
    }
    paint(draft);
    if (result.valid) select(id); else details();
    linked?.feedback(result.valid ? `${id} 이동 완료 · ${place(draft.find(box=>box.id===id))} · 되돌리기로 복원할 수 있습니다.` : result.reason);
  }
  function lockDraft(value) {
    controls.forEach(button => { button.disabled = value; });
    playIdle();
    editButton.setAttribute('aria-pressed', String(value));
    editButton.textContent = value ? '원본 복원·편집 끝내기' : '가상 배치 편집';
    editor.hidden = !value; renderer.setEditable(value);
    if (preview.hasAttribute('data-yard-upcoming-preview')) {
      const sidebar = figure.querySelector('.yard-edit-sidebar');
      if (value) {
        sidebar.prepend(editTools);
        sidebar.append(linked.grid);
      } else {
        figure.before(editTools);
        preview.querySelector('[data-yard-flat]').after(linked.grid);
      }
    }
  }
  function toggleEdit(usePlan = false) {
    stop(); cancelAnimationFrame(raf); animation = null; canvas.dataset.moving = 'false';
    if (draft) {
      draft = null; session = null; draftHistory = []; replayIndex = 0; lockDraft(false); paint(scene.frames[index].boxes);
      resetProgressCaption();
      status('가상 변경을 버리고 선택한 기준 배치를 복원했습니다.');
    } else {
      if (usePlan) controls[0]?.click();
      const next = new DraftSession(scene.areas,scene.frames[index].boxes);
      if (usePlan) {
        const result = next.load(scene.frames.map(frame=>frame.boxes));
        if (!result.valid) { error.textContent = result.reason; return; }
      }
      session = next; draft = cloneBoxes(session.boxes); draftHistory = session.frames; replayIndex = draftHistory.length - 1;
      lockDraft(true); paint(draft);
      const free = topBoxes(draft);
      if (!free.some(box => box.id === selected)) selected = (free.find(box => box.role === 'target') || free[0])?.id || '';
      status(usePlan ? '저장된 이동 순서와 결과를 불러왔습니다. 되돌리거나 이어서 편집하세요.' : '3D에서 박스를 끌어 옮기거나 평면·좌표로 배치하세요.');
    }
    error.textContent = ''; fillCargo(); select(selected);
    showFlat(flatView);
    if (draft && inline) figure.querySelector('.model-layout').scrollIntoView({block: 'start'});
  }
  function advance(delay = 550) {
    if (!playing || paused) return;
    timer = setTimeout(() => {
      if (!playing || paused || document.hidden || figure.hidden) return;
      if (draft) {
        if (replayIndex >= draftHistory.length - 1) { stop(); status('로컬 가상 변경 재생 완료 · 서버에 저장되지 않음'); return; }
        startTransition(draftHistory[++replayIndex]);
        return;
      }
      if (index === scene.frames.length - 1) { stop(); status('계획 재생 완료 · 실제 실행 상태는 바뀌지 않았습니다.'); return; }
      internalStep = true; controls[index + 1].click(); internalStep = false;
    }, delay);
  }
  function describeMove(segment, t) {
    const frame = draft ? {index: replayIndex, label: '가상 배치', move: {number: replayIndex}} : scene.frames[index];
    const desc = describeMoveText(frame, segment, t, {
      total: totalMoves(),
      locate: locateBox,
      draft: Boolean(draft),
    });
    if (stepHeading) stepHeading.textContent = desc.step;
    Object.assign(progress.dataset, {
      cargo: desc.cargo,
      source: desc.source,
      destination: desc.destination,
      phase: desc.phase,
      progress: String(desc.percent),
      mode: draft ? 'local-draft' : 'plan',
    });
    if (progressBar) progressBar.value = desc.percent;
    if (progressRoute && progressPhase) {
      if (desc.phase !== lastPhase || t === 0 || t === 1) {
        lastPhase = desc.phase;
        progressRoute.textContent = desc.route;
        progressPhase.hidden = false;
        progressPhase.textContent = desc.phase;
      }
    } else {
      progress.textContent = `${desc.route} · ${desc.phase} · ${desc.percent}%`;
    }
  }
  function frameSegmentCamera(segment) {
    if (!followMotion?.checked || followSuspended || !segment?.to) return;
    const fromPts = segment.from ? renderer.fitBoxBounds(segment.from) : [];
    const toPts = renderer.fitBoxBounds(segment.to);
    const pts = [...fromPts, ...toPts];
    if (segment.from) {
      const midLog = {
        x: (segment.from.x + segment.to.x) / 2,
        y: (segment.from.y + segment.to.y) / 2,
        z: ceiling(),
        width: segment.to.width || 1,
        depth: segment.to.depth || 1,
      };
      pts.push(...renderer.fitBoxBounds(midLog));
    }
    if (pts.length) renderer.fitTo(pts);
  }
  function applySegmentVisuals(segment) {
    if (!segment?.to) {
      renderer.setMotion(null);
      return;
    }
    renderer.setMotion({
      id: segment.to.id,
      from: segment.from || null,
      to: segment.to,
      ceiling: ceiling(),
    });
    frameSegmentCamera(segment);
  }
  function startTransition(target) {
    clearPreview();
    clearTimeout(dwellTimer);
    const from = new Map(current.map(box => [box.id, box]));
    const moved = target.filter(to => {
      const initial = from.get(to.id);
      return !initial || ['x','y','z','orientation'].some(key => initial[key] !== to[key]);
    });
    const segments = [], working = cloneBoxes(target.filter(to => from.has(to.id)).map(to => from.get(to.id)));
    for (const to of moved) {
      const initial = from.get(to.id);
      if (!initial) working.push(cloneBoxes([to])[0]);
      segments.push({from:initial, to, boxes:cloneBoxes(working),
        instant:initial && ['x','y','z'].every(key => initial[key] === to[key])});
      working[working.findIndex(box => box.id === to.id)] = cloneBoxes([to])[0];
    }
    if (!segments.length || !animateMotion.checked || figure.hidden || document.hidden) {
      paint(target); canvas.dataset.moving = 'false';
      if (segments.length) {
        const lastSeg = segments.at(-1);
        describeMove(lastSeg, 1);
        applySegmentVisuals(lastSeg);
        if (playing) {
          const dwellMs = !animateMotion.checked ? 4500 / Number(speed.value) : 550;
          dwellTimer = setTimeout(() => {
            if (!playing && !animation) renderer?.setMotion(null);
          }, dwellMs);
        }
      } else {
        renderer.setMotion(null);
      }
      status(draft ? '로컬 가상 배치 비교 · 즉시 전환' : scene.frames[index].label + ' · 단계 배치');
      syncStepButtons();
      advance(!animateMotion.checked ? 4500 / Number(speed.value) : 550); return;
    }
    animation = {segments, segment:0, target, elapsed:0, last:performance.now()};
    playIdle();
    canvas.dataset.moving = String(Boolean(segments[0].from && !segments[0].instant));
    applySegmentVisuals(segments[0]);
    describeMove(segments[0], 0);
    status(scene.frames[index]?.label || '배치 이동 설명');
    raf = requestAnimationFrame(tick);
  }
  function tick(now) {
    raf = 0;
    if (!animation || destroyed) return;
    if (paused) {
      animation.last = now;
      return;
    }
    animation.elapsed += (now - animation.last) * Number(speed.value); animation.last = now;
    const segment = animation.segments[animation.segment];
    const t = segment.instant ? 1 : Math.min(1, animation.elapsed / 4500);
    paint(segment.boxes.map(box => box.id === segment.to.id && segment.from ? transit(segment.from, segment.to, t, ceiling()) : box));
    describeMove(segment, t);
    if (t < 1) raf = requestAnimationFrame(tick);
    else if (++animation.segment < animation.segments.length) {
      animation.elapsed = 0;
      const next = animation.segments[animation.segment];
      canvas.dataset.moving = String(Boolean(next.from && !next.instant));
      applySegmentVisuals(next);
      describeMove(next, 0); raf = requestAnimationFrame(tick);
    } else {
      paint(animation.target); animation = null; canvas.dataset.moving = 'false';
      renderer.setMotion(null);
      if (!playing) playIdle();
      status(draft ? '로컬 가상 변경 · 서버에 저장되지 않음' : scene.frames[index].label + ' · 단계 배치'); advance();
    }
  }
  function selectFrame(next, animate = true) {
    if (draft || !scene.frames[next]) return;
    clearPreview();
    if (!internalStep) stop();
    cancelAnimationFrame(raf); raf = 0; animation = null;
    index = next;
    if (stagesHost) {
      controls.forEach((button, idx) => button.setAttribute('aria-pressed', String(idx === next)));
    }
    if (selected && !boxes().some(box => box.id === selected)) selected = '';
    if (location) { const occupant = atPosition(boxes(), location); if (occupant) { selected = occupant.id; location = null; } }
    renderer.select(selected); fillCargo(); syncLinked();
    if (stepHeading) stepHeading.textContent = scene.frames[index].label;
    syncStepButtons();
    if (animate && next) startTransition(scene.frames[index].boxes);
    else {
      canvas.dataset.moving = 'false'; renderer.setMotion(null); paint(scene.frames[index].boxes);
      if (next === 0) resetProgressCaption();
      else {
        const prevBoxes = scene.frames[next - 1]?.boxes || [];
        const fromMap = new Map(prevBoxes.map(b => [b.id, b]));
        const movedTo = scene.frames[next].boxes.find(to => {
          const init = fromMap.get(to.id);
          return !init || ['x','y','z','orientation'].some(k => init[k] !== to[k]);
        });
        if (movedTo) describeMove({from: fromMap.get(movedTo.id), to: movedTo}, 1);
      }
      status(scene.kind === 'map-definition' ? scene.frames[index].label : next ? scene.frames[index].label + ' · 단계 배치' : '원본 관측 배치'); advance();
    }
  }
  function stepBy(delta) {
    clearPreview();
    if (draft) {
      const nextIdx = stepTarget(replayIndex, delta, draftHistory.length);
      if (nextIdx === null) return;
      stop();
      replayIndex = nextIdx;
      if (nextIdx === 0) {
        paint(draftHistory[0]);
        resetProgressCaption();
        syncStepButtons();
      } else {
        startTransition(draftHistory[nextIdx]);
      }
      return;
    }
    if (!isPlanReplay()) return;
    const nextIdx = stepTarget(index, delta, scene.frames.length);
    if (nextIdx === null) return;
    stop();
    if (controls[nextIdx]) controls[nextIdx].click();
    else selectFrame(nextIdx, true);
  }
  const desktop = matchMedia('(min-width: 1200px)');
  const inline = preview.hasAttribute('data-yard-upcoming-preview');
  let flatView = inline;
  function showFlat(value) {
    flatView = value;
    if (inline || !desktop.matches) {
      stop();
      if (!draft) selectFrame(index, false);
    }
    applyYardView(preview, {flat: value, desktop: desktop.matches, editing: Boolean(draft)});
    if (!figure.hidden) renderer.resize();
  }
  if (!inline) on(desktop, 'change', () => showFlat(flatView));
  const makeLinked = () => mountLinkedGrid(preview, scene, value => {
    clearPreview();
    if (animation || playing) { stop(); selectFrame(index, false); }
    if (draft && selected && !value.cargoId) {
      const box = draft.find(item=>item.id===selected);
      applyDraft(selected,{...value,orientation:box.orientation});
      return;
    }
    if (value.cargoId) { error.textContent = ''; select(value.cargoId); renderer.fit(value.cargoId); }
    else {
      selected = ''; location = value; renderer.select(null); renderer.isolate(null);
      isolated = false; figure.querySelector('[data-scene-action="isolate"]').setAttribute('aria-pressed', 'false');
      details(); syncLinked(); renderer.fitLocation(value);
    }
  }, position => draft && selected && !playing ? moveBox(scene.areas,draft,selected,
    {...position,orientation:draft.find(box=>box.id===selected).orientation}) : null);
  linked = makeLinked();
  function syncAreaOptions() {
    editor.elements.area.replaceChildren();
    scene.areas.forEach(area => editor.elements.area.add(new Option(area.label + ' · ' + area.id, area.id)));
  }
  syncAreaOptions();
  function syncDynamicStages() {
    if (!stagesHost) {
      if (statusEl) statusEl.hidden = controls.length <= 1;
      return;
    }
    const specs = stageButtonSpecs(scene);
    if (specs.length) {
      stagesHost.replaceChildren(...specs.map(spec => {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'quiet';
        button.dataset.yardStage = String(spec.index);
        button.setAttribute('aria-pressed', String(spec.pressed));
        button.textContent = spec.label;
        button.addEventListener('click', () => {
          if (draft) return;
          controls.forEach(item => item.setAttribute('aria-pressed', String(item === button)));
          if (statusEl) {
            statusEl.textContent = spec.index === 0
              ? '관측한 배치 · 저장된 원본 배치입니다.'
              : `${spec.label} 후 배치 · 실제 관측·실행 상태는 바뀌지 않습니다.`;
          }
          selectFrame(spec.index, true);
        });
        return button;
      }));
      stagesHost.hidden = false;
      if (statusEl) {
        statusEl.hidden = false;
        statusEl.textContent = '관측한 배치 · 단계를 선택하면 계획 위치만 바뀝니다.';
      }
    } else {
      stagesHost.replaceChildren();
      stagesHost.hidden = true;
      if (statusEl) {
        statusEl.hidden = true;
        statusEl.textContent = '';
      }
    }
    controls = [...preview.querySelectorAll('[data-yard-stage]')];
  }
  function startPlayback() {
    if (draft || !isPlanReplay()) return;
    clearPreview();
    if (figure.hidden) showFlat(false);
    playing = true; paused = false; followSuspended = false;
    playIdle();
    if (index === scene.frames.length - 1) { internalStep = true; controls[0].click(); internalStep = false; }
    else if (!animation) advance();
  }
  function applyInitialPreview() {
    if (!scene?.frames || scene.frames.length < 2) return;
    const frame0 = scene.frames[0];
    const frame1 = scene.frames[1];
    const move = frame1.move;
    const fromMap = new Map((frame0.boxes || []).map(b => [b.id, b]));
    const targetId = move?.cargo_id || move?.cargo || '';
    const toBox = (frame1.boxes || []).find(b => (targetId ? b.id === targetId : !fromMap.get(b.id) || ['x','y','z'].some(k => fromMap.get(b.id)[k] !== b[k])));
    if (!toBox) return;
    const fromBox = fromMap.get(toBox.id) || null;
    previewActive = true;
    renderer.setMotion({id: toBox.id, from: fromBox, to: toBox, ceiling: ceiling()});
    const cargoCode = move?.cargo_id || move?.cargo || toBox.id;
    const phaseLabel = move?.phase_label || move?.phase || frame1.phase || '';
    const arm = move?.primary_arm || move?.arm || '';
    const parts = ['지금', `${cargoCode}${phaseLabel ? ` ${phaseLabel}` : ''}`];
    if (arm) parts.push(`팔 ${arm}`);
    if (stepHeading) stepHeading.textContent = parts.join(' · ');
  }
  function setScene(nextScene, {autoplay = false, preview: showPreview = false} = {}) {
    if (destroyed || !nextScene?.frames?.length) return;
    if (draft) {
      draft = null; session = null; draftHistory = []; replayIndex = 0; lockDraft(false);
    }
    stop(); cancelAnimationFrame(raf); raf = 0; animation = null;
    scene = nextScene;
    index = 0; selected = ''; location = null; isolated = false;
    figure.querySelector('[data-scene-action="isolate"]').setAttribute('aria-pressed', 'false');
    error.textContent = '';
    const nextAreasKey = JSON.stringify(scene.areas);
    if (nextAreasKey !== currentAreasKey) {
      currentAreasKey = nextAreasKey;
      renderer?.dispose();
      renderer = makeRenderer();
    }
    renderer.select(null); renderer.isolate(null); renderer.selectLocation(null); renderer.setMotion(null);
    syncAreaOptions();
    syncDynamicStages();
    syncTierLegend();
    linked?.grid?.remove();
    linked = makeLinked();
    figure.classList.toggle('map-definition', scene.kind === 'map-definition');
    figure.querySelector('.model-inspector').hidden = scene.kind === 'map-definition';
    editTools.hidden = scene.kind === 'map-definition';
    editButton.disabled = scene.kind === 'map-definition';
    loadPlan.hidden = scene.kind === 'outbound-chain' || !isPlanReplay();
    playIdle();
    if (inline) flatView = !autoplay && !showPreview;
    else if ((autoplay || showPreview) && figure.hidden) showFlat(false);
    selectFrame(0, false);
    showFlat(flatView);
    renderer.resize();
    renderer.fit();
    if (showPreview && !autoplay) {
      applyInitialPreview();
    }
    if (inline && !matchMedia('(min-width: 1100px)').matches && autoplay) {
      (autoplay ? canvas : preview).scrollIntoView({block: 'start'});
    }
    if (autoplay && isPlanReplay()) startPlayback();
  }
  on(editor, 'submit', event => {
    event.preventDefault();
    if (!draft || !selected || playing) return;
    applyDraft(selected, {area: editor.elements.area.value, row: Number(editor.elements.row.value),
      column: Number(editor.elements.column.value), level: Number(editor.elements.level.value),
      orientation: Number(editor.elements.orientation.value)});
  });
  on(cargo, 'change', () => { clearPreview(); error.textContent = ''; select(cargo.value); });
  on(editButton, 'click', () => { clearPreview(); toggleEdit(); });
  on(loadPlan, 'click', () => { clearPreview(); if (!draft && scene.frames.length > 1) toggleEdit(true); });
  for (const [button,action] of [[undo,'undo'],[redo,'redo']]) on(button,'click',()=>{
    if (!session || playing) return;
    stop(); session[action](); draft = cloneBoxes(session.boxes); draftHistory = session.frames; replayIndex = draftHistory.length - 1;
    playIdle(); error.textContent = '';
    paint(draft); select(selected); status(action === 'undo' ? '직전 이동을 되돌렸습니다.' : '이동을 다시 적용했습니다.');
  });
  on(play, 'click', () => {
    clearPreview();
    if (playing || animation) {
      if (paused) resumePlayback();
      else pausePlayback();
      return;
    }
    if (draft) {
      playing = true; paused = false; followSuspended = false;
      playIdle();
      replayIndex = 0; paint(draftHistory[0]); renderer.setEditable(false);
      editor.querySelector('fieldset').disabled = true; editButton.disabled = true; updateDraftState(); advance(); return;
    }
    startPlayback();
  });
  on(stopBtn, 'click', () => {
    clearPreview();
    stop({showStopped: true});
    if (!draft) selectFrame(index, false);
    resetProgressCaption({stopped: true});
    status('재생 중지');
  });
  on(prevStepBtn, 'click', () => stepBy(-1));
  on(nextStepBtn, 'click', () => stepBy(1));
  figure.querySelectorAll('[data-scene-camera]').forEach(button => on(button, 'click', () => { clearPreview(); renderer.view(button.dataset.sceneCamera); }));
  figure.querySelectorAll('[data-scene-zoom]').forEach(button => on(button, 'click', () => { clearPreview(); renderer.zoom(Number(button.dataset.sceneZoom) > 0 ? 1.22 : .82); }));
  views.querySelectorAll('[data-yard-view]').forEach(button => on(button, 'click', () => { clearPreview(); showFlat(button.dataset.yardView === 'flat'); }));
  figure.querySelectorAll('[data-scene-action]').forEach(button => on(button, 'click', async () => {
    clearPreview();
    const action = button.dataset.sceneAction;
    if (action === 'focus') renderer.fit(selected);
    if (action === 'fit') renderer.fit();
    if (action === 'isolate') {
      isolated = !isolated; button.setAttribute('aria-pressed', String(isolated)); renderer.isolate(isolated ? selected : null);
    }
    if (action === 'orbit' || action === 'pan') {
      renderer.setMode(action);
      ['orbit','pan'].forEach(mode => figure.querySelector('[data-scene-action="' + mode + '"]')?.setAttribute('aria-pressed', String(mode === action)));
    }
    if (action === 'fullscreen') {
      try { if (document.fullscreenElement === figure) await document.exitFullscreen(); else await figure.requestFullscreen(); }
      catch (_) { error.textContent = '브라우저에서 전체 화면을 허용하지 않습니다.'; }
    }
  }));
  on(document, 'fullscreenchange', () => { renderer.resize(); figure.querySelector('[data-scene-action="fullscreen"]').setAttribute('aria-pressed', String(document.fullscreenElement === figure)); });
  on(preview, 'yardstagechange', event => selectFrame(Number(event.detail.index), event.detail.animate !== false));
  on(document, 'visibilitychange', () => { if (document.hidden) { stop(); if (!draft) selectFrame(index, false); } });
  on(animateMotion, 'change', () => { clearPreview(); stop(); if (!draft) selectFrame(index, false); });
  on(followMotion, 'change', () => { followSuspended = false; });
  on(reduced, 'change', () => {
    applyMotionDefaults();
    stop();
    if (!draft) selectFrame(index, false);
  });
  on(window, 'pagehide', event => { stop(); if (!draft) selectFrame(index, false); if (!event.persisted) destroy(); });
  on(window, 'pageshow', () => renderer.resize());
  on(canvas, 'webglcontextlost', event => { event.preventDefault(); destroy(); fallback(figure); });
  function destroy() {
    if (destroyed) return;
    destroyed = true; stop(); cancelAnimationFrame(raf); events.abort();
    if (preview.classList.contains('is-editing')) {
      lockDraft(false);
      preview.classList.remove('is-editing');
    }
    controls.forEach(button => { button.disabled = false; }); renderer?.dispose();
  }
  syncTierLegend();
  if (statusEl && !stagesHost) {
    statusEl.hidden = controls.length <= 1;
  }
  playIdle();
  if (scene.kind === 'map-definition') {
    figure.classList.add('map-definition');
    figure.querySelector('.model-inspector').hidden = true;
    editButton.disabled = true;
    if (stepHeading) stepHeading.textContent = scene.frames[index].label;
    status(scene.frames[index].label);
  }
  editTools.hidden = scene.kind === 'map-definition';
  loadPlan.hidden = scene.kind === 'outbound-chain' || !isPlanReplay();
  figure.querySelector('[data-scene-action="fullscreen"]').hidden = !document.fullscreenEnabled;
  preview.classList.add('yard-linked');
  paint(current); fillCargo(); syncLinked(); views.hidden = false; showFlat(flatView);
  canvas.dataset.moving = 'false'; figure.dataset.ready = 'true'; figure.dataset.renderer = 'webgl'; renderer.resize(); renderer.fit();
  return {destroy, setScene};
}

if (typeof document !== 'undefined') {
  const viewerPromises = new WeakMap();
  document.querySelectorAll('[data-yard-3d]').forEach(figure => {
    viewerPromises.set(
      figure,
      mountYardViewer(figure).catch(() => {
        fallback(figure);
        return null;
      }),
    );
  });
  bindUpcomingPlans(document, {
    getViewer: () => {
      const figure = document.querySelector('[data-yard-3d]');
      return figure ? viewerPromises.get(figure) || null : null;
    },
  });
}
