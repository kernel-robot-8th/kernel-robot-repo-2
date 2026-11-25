import assert from 'node:assert/strict';
import {bindDragInteraction} from '../harbor_web/static/harbor_web/yard-renderer.js';

function createEventTarget() {
  const listeners = new Map();
  return {
    dataset: {},
    captured: new Set(),
    released: [],
    addEventListener(type, fn) {
      if (!listeners.has(type)) listeners.set(type, new Set());
      listeners.get(type).add(fn);
    },
    removeEventListener(type, fn) {
      listeners.get(type)?.delete(fn);
    },
    dispatch(type, event = {}) {
      for (const fn of [...(listeners.get(type) || [])]) fn(event);
    },
    listenerCount(type) {
      return listeners.get(type)?.size || 0;
    },
    setPointerCapture(id) {
      this.captured.add(id);
    },
    releasePointerCapture(id) {
      this.released.push(id);
      if (this.captured.delete(id)) {
        this.dispatch('lostpointercapture', {pointerId: id});
      }
    },
  };
}

function setup() {
  const canvas = createEventTarget();
  const win = createEventTarget();
  const orbit = {enabled: true};
  let editable = true;
  let selected = '';
  const drops = [];
  const restored = [];
  let cleared = 0;
  let invalidated = 0;
  const targetObj = {valid: true, placement: {area: 'yard', row: 2, column: 4, level: 2, orientation: 0}};
  const startPos = {x: 0, y: 2, z: 0};

  const interaction = bindDragInteraction({
    canvas,
    win,
    orbit,
    isEditable: () => editable,
    getSelected: () => selected,
    pickHitBox: () => 'C-9',
    getStartPosition: () => ({...startPos}),
    restorePosition: (id, pos) => restored.push({id, pos}),
    resetOutline: () => {},
    updatePreview: () => targetObj,
    clearDragVisuals: () => {
      cleared += 1;
      delete canvas.dataset.dragging;
    },
    onSelect: id => { selected = id; },
    onDrop: (id, target) => drops.push({id, target}),
    invalidate: () => { invalidated += 1; },
  });

  function beginDrag(pointerId = 1) {
    canvas.dispatch('pointerdown', {button: 0, pointerId, clientX: 100, clientY: 100});
    assert.equal(orbit.enabled, false);
    assert.equal(interaction.isDragging(), true);
    canvas.dispatch('pointermove', {pointerId, clientX: 140, clientY: 130});
    assert.equal(canvas.dataset.dragging, 'true');
  }

  return {canvas, win, orbit, drops, restored, beginDrag, interaction, getCleared: () => cleared, getInvalidated: () => invalidated, targetObj, startPos};
}

// 1. Escape cancels active drag, restores orbit.enabled and start position, and does not drop.
{
  const ctx = setup();
  ctx.beginDrag(1);
  let prevented = false;
  ctx.win.dispatch('keydown', {key: 'Escape', preventDefault: () => { prevented = true; }});
  assert.equal(prevented, true);
  assert.equal(ctx.orbit.enabled, true);
  assert.equal(ctx.interaction.isDragging(), false);
  assert.equal(ctx.drops.length, 0);
  assert.deepEqual(ctx.restored, [{id: 'C-9', pos: ctx.startPos}]);
  assert.equal(ctx.canvas.dataset.dragging, undefined);
  ctx.interaction.dispose();
}

// 2. window blur cancels active drag and restores orbit.enabled.
{
  const ctx = setup();
  ctx.beginDrag(2);
  ctx.win.dispatch('blur', {});
  assert.equal(ctx.orbit.enabled, true);
  assert.equal(ctx.interaction.isDragging(), false);
  assert.equal(ctx.drops.length, 0);
  assert.deepEqual(ctx.restored, [{id: 'C-9', pos: ctx.startPos}]);
  ctx.interaction.dispose();
}

// 3. Unexpected lostpointercapture cancels active drag and restores orbit.enabled.
{
  const ctx = setup();
  ctx.beginDrag(3);
  ctx.canvas.dispatch('lostpointercapture', {pointerId: 3});
  assert.equal(ctx.orbit.enabled, true);
  assert.equal(ctx.interaction.isDragging(), false);
  assert.equal(ctx.drops.length, 0);
  assert.deepEqual(ctx.restored, [{id: 'C-9', pos: ctx.startPos}]);
  ctx.interaction.dispose();
}

// 4. Normal pointerup applies drop even when releasePointerCapture fires lostpointercapture synchronously.
{
  const ctx = setup();
  ctx.beginDrag(4);
  ctx.canvas.dispatch('pointerup', {pointerId: 4, clientX: 140, clientY: 130});
  assert.equal(ctx.orbit.enabled, true);
  assert.equal(ctx.interaction.isDragging(), false);
  assert.equal(ctx.drops.length, 1);
  assert.deepEqual(ctx.drops[0], {id: 'C-9', target: ctx.targetObj});
  assert.equal(ctx.restored.length, 0);
  ctx.interaction.dispose();
}

// 5. dispose() removes all canvas and window listeners cleanly.
{
  const ctx = setup();
  ctx.interaction.dispose();
  for (const type of ['pointerdown', 'pointermove', 'pointercancel', 'lostpointercapture', 'pointerup']) {
    assert.equal(ctx.canvas.listenerCount(type), 0, `canvas ${type} listener leaked`);
  }
  for (const type of ['keydown', 'blur']) {
    assert.equal(ctx.win.listenerCount(type), 0, `window ${type} listener leaked`);
  }
}

console.log('Yard renderer drag cancel (Escape, window blur, lostpointercapture, normal drop, dispose) checks passed');
