import {
  DEFAULT_MOVE_SECONDS_ESTIMATE,
  TIERS,
  actionBarState,
  applyReasonText,
  buildLaneStrings,
  canMove,
  clampTier,
  computeScheduleEtas,
  minAllowedTier,
  moveStep,
  moveToBack,
  moveToFront,
  moveToTier,
  shiftTier,
} from './reorder-model.mjs';
import {createEl, createPopoverController} from './schedule-popover.js';

const UNDO_KEY = 'harbor:work-reorder-undo';
const DRAG_THRESHOLD_PX = 5;
const TIER_NAMES = new Map(TIERS.map(t => [t.group, t.name]));

function mountReorderEditor(editor) {
  const board = editor.querySelector('#wk-flow');
  const actions = editor.querySelector('#wk-out-actions');
  const previewLine = editor.querySelector('#wk-drag-preview');
  const resultBox = editor.querySelector('#wk-result');
  const form = editor.querySelector('form[data-reorder-form]');
  if (!board || !actions || !resultBox || !form) return;

  const validateUrl = editor.dataset.validateUrl;
  const reorderUrl = editor.dataset.reorderUrl;
  const csrf = form.querySelector('input[name="csrfmiddlewaretoken"]')?.value || '';
  const tierSections = new Map(
    [...board.querySelectorAll('[data-wf-tier]')].map(sec => [Number(sec.dataset.wfTier), sec])
  );
  const tierLists = new Map(
    [...board.querySelectorAll('[data-wf-list]')].map(ol => [Number(ol.dataset.wfList), ol])
  );

  const readItems = () => [...board.querySelectorAll('[data-order]')].map(li => ({
    id: li.dataset.order,
    cardId: li.dataset.cardId || li.dataset.order,
    version: Number(li.dataset.version),
    tier: clampTier(li.dataset.tier),
    kind: li.dataset.kind || 'OUTBOUND',
    code: li.dataset.code,
    running: li.dataset.running === '1',
    simBlocked: li.classList.contains('is-sim-blocked'),
    cutoffIso: li.dataset.cutoffIso || '',
    initialEta: li.dataset.eta || '',
    initialEtaLate: li.dataset.etaLate === '1',
    node: li,
  }));

  const etaBaseMs = Date.parse(board.dataset.etaBase || '');
  const moveSecondsEstimate = Number(board.dataset.moveSeconds) || DEFAULT_MOVE_SECONDS_ESTIMATE;
  const initialItems = readItems();
  let draft = initialItems.map(item => ({...item}));
  let validation = null;
  let validationEtas = null;
  let busy = false;
  let suppressClick = false;

  const readUndo = () => {
    try {
      const raw = sessionStorage.getItem(UNDO_KEY);
      return raw ? JSON.parse(raw) : null;
    } catch (_) {
      return null;
    }
  };
  const writeUndo = value => {
    try {
      if (value) sessionStorage.setItem(UNDO_KEY, JSON.stringify(value));
      else sessionStorage.removeItem(UNDO_KEY);
    } catch (_) {}
  };

  function kindLabel(kind) {
    return kind === 'INBOUND' ? '입고' : '반출';
  }

  function laneFormData(items, fixedFlags = [true, true, true]) {
    const fd = new FormData();
    if (csrf) fd.append('csrfmiddlewaretoken', csrf);
    fd.append('scope', 'schedule');
    for (const value of buildLaneStrings(items, fixedFlags)) {
      fd.append('lane', value);
    }
    return fd;
  }

  async function postValidate(items) {
    const resp = await fetch(validateUrl, {
      method: 'POST',
      body: laneFormData(items),
      credentials: 'same-origin',
      headers: {'X-CSRFToken': csrf},
    });
    const data = await resp.json().catch(() => ({ok: false, reasons: ['순서 검증 응답을 읽지 못했습니다.']}));
    return {status: resp.status, data};
  }

  async function postApply(items, previousIds = null) {
    const resp = await fetch(reorderUrl, {
      method: 'POST',
      body: laneFormData(items),
      credentials: 'same-origin',
      headers: {'X-CSRFToken': csrf},
    });
    if (!resp.ok) {
      return {ok: false, status: resp.status};
    }
    writeUndo(previousIds ? {ids: previousIds} : null);
    window.location.reload();
    return {ok: true};
  }

  function renderControls(item) {
    const bounds = canMove(draft, item.id);
    const defs = [
      {action: 'front', label: '<<', aria: `${item.code} 맨 앞으로`, title: '맨 앞으로', disabled: !bounds.front},
      {action: 'prev', label: '<', aria: `${item.code} 한 칸 앞으로`, title: '한 칸 앞으로', disabled: !bounds.prev},
      {action: 'next', label: '>', aria: `${item.code} 한 칸 뒤로`, title: '한 칸 뒤로', disabled: !bounds.next},
      {action: 'back', label: '>>', aria: `${item.code} 맨 뒤로`, title: '맨 뒤로', disabled: !bounds.back},
    ];
    return createEl('div', {class: 'wf-card-controls', role: 'group', 'aria-label': `${item.code} 순서 이동`}, defs.map(def =>
      createEl('button', {
        type: 'button',
        class: 'quiet wf-ctl-btn',
        'data-move': def.action,
        'aria-label': def.aria,
        title: def.title,
        disabled: busy || def.disabled,
        onClick: event => {
          event.stopPropagation();
          applyMove(def.action, item.id, def.action);
        },
      }, def.label)
    ));
  }

  const popover = createPopoverController(board, {
    getPlanSummary: data => {
      if (validation && Array.isArray(validation.items) && data.orderId) {
        const step = validation.items.find(s => String(s.id) === String(data.orderId));
        if (step) {
          if (step.ok) {
            const parts = [];
            if (typeof step.moves === 'number' && (step.kind === 'OUTBOUND' || step.moves > 0)) {
              parts.push(`이동 ${step.moves}회`);
            }
            if (step.kind === 'OUTBOUND' && typeof step.rehandles === 'number') {
              parts.push(`재취급 ${step.rehandles}회`);
            }
            if (step.place) parts.push(step.place);
            if (parts.length) return parts.join(' · ');
          } else if (step.reason) {
            return step.reason;
          }
        } else if (validation.items.length) {
          return '';
        }
      }
      return data.planSummary;
    },
    getEta: data => {
      if (validationEtas && data.orderId && validationEtas.has(String(data.orderId))) {
        return validationEtas.get(String(data.orderId));
      }
      return {etaText: data.eta, late: data.etaLate};
    },
    getControls: data => {
      if (data.done || data.running || !data.orderId) return null;
      const item = draft.find(entry => entry.id === data.orderId);
      return item ? renderControls(item) : null;
    },
  });

  function focusAfterMove(id, buttonAction = null) {
    if (buttonAction && popover.focusControl(buttonAction)) return;
    board.querySelector(`[data-order="${id}"] .wf-card`)?.focus();
  }

  function applyMove(action, id, focusButton = null) {
    if (busy) return;
    if (action === 'front') draft = moveToFront(draft, id);
    else if (action === 'prev') draft = moveStep(draft, id, -1);
    else if (action === 'next') draft = moveStep(draft, id, 1);
    else if (action === 'back') draft = moveToBack(draft, id);
    else if (action === 'tierUp') draft = shiftTier(draft, id, -1);
    else if (action === 'tierDown') draft = shiftTier(draft, id, 1);
    else return;
    validation = null;
    render();
    if (popover.getOpenId() === id) {
      popover.dispatch({type: 'move', id});
    }
    focusAfterMove(id, focusButton);
  }

  function reorderTierNodes(items) {
    const counts = new Map([[1, 0], [2, 0], [3, 0]]);
    items.forEach((item, index) => {
      const tier = clampTier(item.tier);
      counts.set(tier, (counts.get(tier) || 0) + 1);
      item.node.dataset.tier = String(tier);
      item.node.dataset.tierName = TIER_NAMES.get(tier) || '';
      const seq = item.node.querySelector('[data-reorder-seq]');
      if (seq) seq.textContent = String(index + 1);
      const list = tierLists.get(tier);
      if (list) list.append(item.node);
    });
    for (const [tier, sec] of tierSections.entries()) {
      const count = counts.get(tier) || 0;
      const badge = sec.querySelector('[data-wf-count]');
      if (badge) badge.textContent = `${count}건`;
    }
  }

  function computeDropTarget(clientX, clientY, draggedId) {
    const floorTier = minAllowedTier(draft);
    let chosenTier = floorTier;
    let bestDist = Infinity;

    for (const [tier, sec] of tierSections.entries()) {
      const rect = sec.getBoundingClientRect();
      const clampedY = Math.max(rect.top, Math.min(clientY, rect.bottom));
      const dist = Math.abs(clientY - clampedY);
      if (dist < bestDist) {
        bestDist = dist;
        chosenTier = tier;
      }
    }
    chosenTier = Math.max(floorTier, chosenTier);

    const list = tierLists.get(chosenTier);
    const siblings = [...list.querySelectorAll('.wf-node[data-order]')].filter(
      n => n.dataset.order !== draggedId
    );
    let minPos = 0;
    siblings.forEach((node, idx) => {
      if (node.dataset.running === '1') minPos = idx + 1;
    });

    let insertIndex = siblings.length;
    for (let i = 0; i < siblings.length; i += 1) {
      const r = siblings[i].getBoundingClientRect();
      const sameRow = clientY >= r.top - 12 && clientY <= r.bottom + 12;
      if (clientY < r.top - 12) {
        insertIndex = i;
        break;
      }
      if (sameRow && clientX < r.left + r.width / 2) {
        insertIndex = i;
        break;
      }
    }
    insertIndex = Math.max(minPos, insertIndex);
    return {targetTier: chosenTier, insertIndex, siblings, list};
  }

  function wireDrag(card, item) {
    card.onpointerdown = event => {
      if (busy || item.running || event.button > 0 || event.target.closest('button, a')) return;
      const pointerId = event.pointerId;
      const startX = event.clientX;
      const startY = event.clientY;
      let started = false;
      let target = null;
      let ghost = null;
      let marker = null;

      const cleanup = () => {
        window.removeEventListener('pointermove', onMove);
        window.removeEventListener('pointerup', onUp);
        window.removeEventListener('pointercancel', onCancel);
        window.removeEventListener('keydown', onEscape);
        item.node.classList.remove('is-dragging');
        board.classList.remove('is-dragging-active');
        tierSections.forEach(s => s.classList.remove('is-drop-tier'));
        marker?.remove();
        ghost?.remove();
        if (previewLine) {
          previewLine.hidden = true;
          previewLine.textContent = '';
        }
      };

      const onMove = moveEvent => {
        if (moveEvent.pointerId !== pointerId) return;
        if (!started) {
          const dist = Math.hypot(moveEvent.clientX - startX, moveEvent.clientY - startY);
          if (dist < DRAG_THRESHOLD_PX) return;
          started = true;
          suppressClick = true;
          popover.dispatch({type: 'dragStart'});
          item.node.classList.add('is-dragging');
          board.classList.add('is-dragging-active');
          ghost = createEl('div', {class: 'wf-drag-ghost', 'aria-hidden': 'true'}, [
            createEl('strong', {text: `${item.code} ${kindLabel(item.kind)}`}),
            createEl('span', {class: 'wf-drag-ghost-tier', text: ''}),
          ]);
          document.body.append(ghost);
          marker = createEl('li', {class: 'wf-drop-marker', 'aria-hidden': 'true'}, [
            createEl('span', {class: 'wf-drop-line'}),
            createEl('span', {class: 'wf-drop-pill', text: `${item.code} 여기에 놓기`}),
          ]);
        }

        ghost.style.left = `${moveEvent.clientX + 14}px`;
        ghost.style.top = `${moveEvent.clientY - 18}px`;

        const {targetTier, insertIndex, siblings, list} = computeDropTarget(
          moveEvent.clientX,
          moveEvent.clientY,
          item.id,
        );
        target = {targetTier, insertIndex};

        tierSections.forEach((sec, tier) => sec.classList.toggle('is-drop-tier', tier === targetTier));
        const tierName = TIER_NAMES.get(targetTier) || '';
        const destText = `${tierName} ${insertIndex + 1}번째`;
        const ghostTier = ghost.querySelector('.wf-drag-ghost-tier');
        if (ghostTier) ghostTier.textContent = `→ ${destText}`;
        const pill = marker.querySelector('.wf-drop-pill');
        if (pill) pill.textContent = destText;
        if (previewLine) {
          previewLine.hidden = false;
          previewLine.textContent = `${item.code} (${kindLabel(item.kind)}) → ${destText}`;
        }

        const refNode = siblings[insertIndex] || null;
        if (marker.parentElement !== list || marker.nextElementSibling !== refNode) {
          list.insertBefore(marker, refNode);
        }
      };

      const onUp = upEvent => {
        if (upEvent.pointerId !== pointerId) return;
        const committed = started ? target : null;
        const wasStarted = started;
        cleanup();
        if (wasStarted) {
          setTimeout(() => { suppressClick = false; }, 0);
        }
        if (committed) {
          draft = moveToTier(draft, item.id, committed.targetTier, committed.insertIndex);
          validation = null;
          render();
          focusAfterMove(item.id);
        }
      };

      const onCancel = cancelEvent => {
        if (cancelEvent && cancelEvent.pointerId !== undefined && cancelEvent.pointerId !== pointerId) return;
        const wasStarted = started;
        cleanup();
        if (wasStarted) {
          setTimeout(() => { suppressClick = false; }, 0);
        }
      };

      const onEscape = keyEvent => {
        if (keyEvent.key === 'Escape') {
          onCancel();
        }
      };

      window.addEventListener('pointermove', onMove);
      window.addEventListener('pointerup', onUp);
      window.addEventListener('pointercancel', onCancel);
      window.addEventListener('keydown', onEscape);
    };
  }

  async function runValidate() {
    if (busy) return;
    busy = true;
    render();
    const {data} = await postValidate(draft);
    validation = data;
    busy = false;
    render();
  }

  async function runApply() {
    if (busy || !validation?.ok) return;
    busy = true;
    render();
    const recheck = await postValidate(draft);
    validation = recheck.data;
    if (!validation.ok) {
      busy = false;
      render();
      return;
    }
    const previousIds = initialItems.map(item => ({id: item.id, tier: item.tier}));
    const applied = await postApply(draft, previousIds);
    if (!applied.ok) {
      validation = {ok: false, reasons: ['작업 대기열이 바뀌었습니다. 새로고침 후 다시 시도하세요.']};
      busy = false;
      render();
    }
  }

  async function runUndo() {
    const saved = readUndo();
    if (busy || !saved?.ids) return;
    const byId = new Map(initialItems.map(item => [item.id, item]));
    const restored = saved.ids.map(entry => {
      const cur = byId.get(entry.id);
      return cur ? {...cur, tier: clampTier(entry.tier)} : null;
    }).filter(Boolean);
    if (restored.length !== initialItems.length) {
      writeUndo(null);
      render();
      return;
    }
    busy = true;
    render();
    const check = await postValidate(restored);
    if (!check.data.ok) {
      writeUndo(null);
      draft = restored;
      validation = check.data;
      busy = false;
      render();
      return;
    }
    const done = await postApply(restored, null);
    if (!done.ok) {
      busy = false;
      render();
    }
  }

  // Wire static click/keyboard popover triggers on completed cards (read-only).
  board.querySelectorAll('.wf-node[data-done="1"]').forEach(li => {
    const card = li.querySelector('.wf-card');
    if (!card) return;
    const cardId = li.dataset.cardId;
    card.addEventListener('click', () => {
      popover.dispatch({type: 'cardClick', id: cardId});
    });
    card.addEventListener('keydown', event => {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        popover.dispatch({type: 'cardClick', id: cardId});
      }
    });
  });

  function render() {
    board.classList.add('is-edit');
    reorderTierNodes(draft);

    const bar = actionBarState(initialItems, draft, validation, busy);
    if (!bar.dirty) validation = null;

    const stepById = new Map();
    if (bar.dirty && validation && Array.isArray(validation.items)) {
      for (const step of validation.items) {
        if (step.id !== undefined) stepById.set(String(step.id), step);
      }
    }

    const hasValidation = Boolean(bar.dirty && validation && Array.isArray(validation.items));
    validationEtas = hasValidation
      ? computeScheduleEtas(
        draft.map(item => {
          const step = stepById.get(item.id);
          return {
            id: item.id,
            kind: item.kind,
            ok: Boolean(step && step.ok),
            moves: step ? step.moves : 0,
            cutoffIso: item.cutoffIso,
          };
        }),
        {baseTimeMs: etaBaseMs, moveSecondsEstimate},
      )
      : null;

    draft.forEach((item, index) => {
      const li = item.node;
      const card = li.querySelector('.wf-card');
      card?.querySelectorAll('.wf-step-place').forEach(node => node.remove());

      const etaNode = card?.querySelector('[data-wf-eta]');
      if (etaNode) {
        const etaEntry = validationEtas ? validationEtas.get(String(item.id)) : null;
        const etaText = etaEntry ? etaEntry.etaText : item.initialEta;
        const etaLate = etaEntry ? etaEntry.late : item.initialEtaLate;
        etaNode.textContent = etaText;
        etaNode.classList.toggle('wf-eta-late', Boolean(etaLate));
      }

      const step = stepById.get(item.id);
      li.classList.toggle('is-step-fail', Boolean(step && !step.ok));
      li.classList.toggle('is-sim-blocked', !hasValidation && Boolean(item.simBlocked));
      const basePlace = card?.querySelector('.wf-place');
      if (basePlace) {
        basePlace.hidden = hasValidation;
      }
      if (card && hasValidation) {
        if (step && step.ok && step.kind === 'INBOUND' && step.place) {
          card.append(createEl('span', {class: 'wf-step-place', text: `배정 ${step.place}`}));
        } else if (step && step.ok && step.kind === 'OUTBOUND') {
          const moveText = step.rehandles > 0
            ? `이동 ${step.moves}회 · 재취급 ${step.rehandles}회`
            : `이동 ${step.moves}회`;
          card.append(createEl('span', {class: 'wf-step-place', text: moveText}));
        } else if (step && !step.ok) {
          card.append(createEl('span', {
            class: 'wf-step-place wf-place-blocked',
            text: step.short_reason || '계산 보류',
          }));
        }
      }

      if (!card) return;
      card.setAttribute('tabindex', '0');
      card.setAttribute(
        'aria-label',
        `${item.code} ${kindLabel(item.kind)} · ${TIER_NAMES.get(clampTier(item.tier)) || ''} ${index + 1}번째`
      );
      card.onclick = event => {
        if (suppressClick || event.target.closest('a, button')) return;
        popover.dispatch({type: 'cardClick', id: item.cardId});
      };
      card.onkeydown = event => {
        if (event.target.closest('a, button')) return;
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          popover.dispatch({type: 'cardClick', id: item.cardId});
        } else if (!item.running && event.key === 'ArrowLeft') {
          event.preventDefault();
          applyMove('prev', item.id);
        } else if (!item.running && event.key === 'ArrowRight') {
          event.preventDefault();
          applyMove('next', item.id);
        } else if (!item.running && event.key === 'Home') {
          event.preventDefault();
          applyMove('front', item.id);
        } else if (!item.running && event.key === 'End') {
          event.preventDefault();
          applyMove('back', item.id);
        } else if (!item.running && event.key === 'ArrowUp') {
          event.preventDefault();
          applyMove('tierUp', item.id);
        } else if (!item.running && event.key === 'ArrowDown') {
          event.preventDefault();
          applyMove('tierDown', item.id);
        }
      };

      if (!item.running) {
        wireDrag(card, item);
      } else {
        card.onpointerdown = null;
      }
    });

    actions.replaceChildren();
    if (bar.dirty) {
      actions.hidden = false;
      actions.append(createEl('button', {
        type: 'button',
        id: 'wk-validate',
        disabled: bar.validateDisabled,
        onClick: runValidate,
      }, busy ? '검증 중…' : '이 순서로 할 수 있는지 확인'));
      actions.append(createEl('button', {
        type: 'button',
        id: 'wk-apply',
        disabled: bar.applyDisabled,
        'aria-describedby': validation ? 'wk-apply-reason' : null,
        onClick: runApply,
      }, '적용'));
      actions.append(createEl('button', {
        type: 'button',
        id: 'wk-cancel',
        class: 'quiet',
        disabled: bar.cancelDisabled,
        onClick: () => {
          draft = initialItems.map(item => ({...item}));
          validation = null;
          popover.dispatch({type: 'cancel'});
          render();
        },
      }, '취소'));
    } else if (readUndo()) {
      actions.hidden = false;
      actions.append(createEl('span', {class: 'helptext', text: '적용함 ·'}));
      actions.append(createEl('button', {
        type: 'button',
        class: 'quiet',
        disabled: busy,
        onClick: runUndo,
      }, '되돌리기'));
    } else {
      actions.hidden = true;
    }

    resultBox.replaceChildren();
    if (bar.dirty && validation) {
      const ok = Boolean(validation.ok);
      let summary = validation.current && validation.current.ok
        ? `이 순서로는 처리할 수 없음 (기존: 재취급 ${validation.current.rehandles}회 · 이동 ${validation.current.moves}회)`
        : '이 순서로는 처리할 수 없음';
      if (ok) {
        if (validation.current && validation.current.ok && validation.current.moves !== null) {
          summary = `재취급 ${validation.current.rehandles}→${validation.rehandles}회 · 이동 ${validation.current.moves}→${validation.moves}회`;
        } else if (validation.current && !validation.current.ok) {
          summary = `재취급 ${validation.rehandles}회 · 이동 ${validation.moves}회 (기존 순서 불가 → 가능)`;
        } else {
          summary = `재취급 ${validation.rehandles}회 · 이동 ${validation.moves}회`;
        }
      }
      const inboundPlaces = ok && Array.isArray(validation.items)
        ? validation.items.filter(step => step.kind === 'INBOUND' && step.ok && step.place)
        : [];
      const panel = createEl('div', {id: 'wk-result-box', class: `wk-result ${ok ? 'is-good' : 'is-critical'}`}, [
        createEl('div', {class: 'wk-result-row'}, [
          createEl('span', {class: `badge ${ok ? 'READY' : 'HOLD'}`, text: ok ? '가능' : '불가'}),
          createEl('span', {text: summary}),
        ]),
        inboundPlaces.length
          ? createEl('div', {class: 'wf-result-places'}, inboundPlaces.map(step =>
            createEl('span', {class: 'wf-result-place-chip', text: `${step.code} → ${step.place}`})
          ))
          : null,
        !ok && validation.reasons?.length
          ? createEl('ul', {class: 'helptext'}, validation.reasons.map(msg => createEl('li', {text: msg})))
          : null,
        createEl('span', {id: 'wk-apply-reason', class: 'helptext', text: applyReasonText(validation)}),
      ]);
      resultBox.append(panel);
    }

    popover.refresh();
  }

  render();
}

if (typeof document !== 'undefined') {
  document.querySelectorAll('[data-reorder-editor]').forEach(mountReorderEditor);
}
