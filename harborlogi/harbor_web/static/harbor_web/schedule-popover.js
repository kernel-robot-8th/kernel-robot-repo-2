import {
  TIERS,
  clampTier,
  computePopoverPosition,
  nextPopoverState,
} from './reorder-model.mjs';

const TIER_NAMES = new Map(TIERS.map(t => [t.group, t.name]));

export function createEl(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === 'text') node.textContent = v;
    else if (k.startsWith('on') && typeof v === 'function') node.addEventListener(k.slice(2).toLowerCase(), v);
    else node.setAttribute(k, v === true ? '' : String(v));
  }
  for (const child of Array.isArray(children) ? children : [children]) {
    if (child !== null && child !== undefined) node.append(child);
  }
  return node;
}

export function readCardData(li) {
  const tier = clampTier(li.dataset.tier);
  return {
    cardId: li.dataset.cardId || li.dataset.order || '',
    orderId: li.dataset.order || null,
    done: li.dataset.done === '1',
    running: li.dataset.running === '1',
    tier,
    tierName: li.dataset.tierName || TIER_NAMES.get(tier) || '',
    kind: li.dataset.kind || 'OUTBOUND',
    kindLabel: li.dataset.kindLabel || (li.dataset.kind === 'INBOUND' ? '입고' : '반출'),
    code: li.dataset.code || '',
    state: li.dataset.state || '',
    time: li.dataset.time || '',
    shipper: li.dataset.shipper || '',
    voyage: li.dataset.voyage || '',
    cutoff: li.dataset.cutoff || '',
    cutoffIso: li.dataset.cutoffIso || '',
    eta: li.dataset.eta || '',
    etaLate: li.dataset.etaLate === '1',
    planSummary: li.dataset.planSummary || '',
    href: li.dataset.href || '',
    actionLabel: li.dataset.actionLabel || '',
    actionHref: li.dataset.actionHref || '',
    node: li,
  };
}

export function createPopoverController(board, options = {}) {
  const getControls = options.getControls || null;
  const getPlanSummary = options.getPlanSummary || null;
  const getEta = options.getEta || null;
  const onChange = options.onChange || null;

  let openCardId = null;
  const popover = createEl('div', {
    class: 'wf-popover',
    role: 'dialog',
    'aria-label': '작업 상세',
    hidden: true,
  });
  document.body.append(popover);

  const findNode = cardId => (cardId ? board.querySelector(`.wf-node[data-card-id="${cardId}"]`) : null);

  function syncCardStates() {
    board.querySelectorAll('.wf-node[data-card-id]').forEach(li => {
      const isOpen = openCardId !== null && li.dataset.cardId === openCardId;
      li.classList.toggle('is-selected', isOpen);
      const card = li.querySelector('.wf-card');
      if (card) card.setAttribute('aria-expanded', String(isOpen));
    });
  }

  function position() {
    if (!openCardId || popover.hidden) return;
    const li = findNode(openCardId);
    const card = li?.querySelector('.wf-card');
    if (!card) {
      close();
      return;
    }
    const anchorRect = card.getBoundingClientRect();
    const popRect = popover.getBoundingClientRect();
    const pos = computePopoverPosition(
      anchorRect,
      {width: popRect.width, height: popRect.height},
      {width: window.innerWidth, height: window.innerHeight},
    );
    popover.dataset.align = pos.align;
    popover.dataset.placement = pos.placement;
    popover.style.left = `${pos.left}px`;
    popover.style.top = `${pos.top}px`;
  }

  function renderContent() {
    syncCardStates();
    if (!openCardId) {
      popover.hidden = true;
      popover.replaceChildren();
      return;
    }
    const li = findNode(openCardId);
    if (!li) {
      openCardId = null;
      popover.hidden = true;
      popover.replaceChildren();
      return;
    }
    const data = readCardData(li);
    const rows = [];
    const addRow = (label, value, ddClass = null) => {
      if (!value) return;
      rows.push(createEl('dt', {text: label}));
      rows.push(createEl('dd', {class: ddClass, text: value}));
    };
    if (data.state && data.state !== '대기') addRow('상태', data.state);
    if (data.time) addRow('완료 시각', data.time);
    if (!data.done) {
      const etaInfo = getEta ? getEta(data) : {etaText: data.eta, late: data.etaLate};
      if (etaInfo?.etaText) {
        addRow('시각', etaInfo.etaText, etaInfo.late ? 'wf-eta-late' : null);
      }
    }
    addRow('우선 단계', data.done ? data.tierName : (TIER_NAMES.get(data.tier) || data.tierName));
    addRow('화주', data.shipper);
    addRow('항차', data.voyage);
    addRow('마감', data.cutoff);
    const summary = getPlanSummary ? getPlanSummary(data) : data.planSummary;
    addRow('계획 요약', summary);

    const head = createEl('div', {class: 'wf-pop-head'}, [
      createEl('div', {class: 'wf-pop-title'}, [
        createEl('strong', {text: data.code}),
        createEl('span', {class: `wf-kind wf-kind-${data.kind.toLowerCase()}`, text: data.kindLabel}),
      ]),
      createEl('button', {
        type: 'button',
        class: 'quiet wf-pop-close',
        'aria-label': '닫기',
        onClick: event => {
          event.stopPropagation();
          close({restoreFocus: true});
        },
      }, '×'),
    ]);

    const body = createEl('dl', {class: 'wf-pop-meta'}, rows);
    const footChildren = [];
    if (data.actionLabel && data.actionHref) {
      footChildren.push(createEl('a', {
        class: 'button quiet wf-pop-action',
        'data-pop-action': '',
        href: data.actionHref,
        text: data.actionLabel,
      }));
    }
    if (data.href) {
      footChildren.push(createEl('a', {class: 'wf-pop-link', href: data.href, text: '자세히'}));
    }
    const controls = getControls ? getControls(data) : null;
    if (controls) footChildren.push(controls);
    const foot = footChildren.length ? createEl('div', {class: 'wf-pop-foot'}, footChildren) : null;

    popover.replaceChildren(head, body, ...(foot ? [foot] : []));
    popover.hidden = false;
    position();
  }

  function dispatch(event, {restoreFocus = false} = {}) {
    const prevId = openCardId;
    openCardId = nextPopoverState(openCardId, event);
    renderContent();
    if (restoreFocus && prevId && !openCardId) {
      findNode(prevId)?.querySelector('.wf-card')?.focus();
    }
    if (onChange) onChange(openCardId);
    return openCardId;
  }

  function close({restoreFocus = false} = {}) {
    return dispatch({type: 'close'}, {restoreFocus});
  }

  function onDocumentClick(event) {
    if (!openCardId) return;
    if (popover.contains(event.target)) return;
    if (event.target.closest?.('.wf-card')) return;
    dispatch({type: 'outsideClick'});
  }

  function onKeyDown(event) {
    if (event.key === 'Escape' && openCardId) {
      event.preventDefault();
      dispatch({type: 'escape'}, {restoreFocus: true});
    }
  }

  function onViewportChange() {
    if (openCardId) position();
  }

  document.addEventListener('click', onDocumentClick);
  window.addEventListener('keydown', onKeyDown);
  window.addEventListener('resize', onViewportChange);
  window.addEventListener('scroll', onViewportChange, true);

  return {
    element: popover,
    getOpenId: () => openCardId,
    dispatch,
    close,
    refresh: renderContent,
    position,
    focusControl: action => {
      if (action) {
        const btn = popover.querySelector(`[data-move="${action}"]`);
        if (btn && !btn.disabled) {
          btn.focus();
          return true;
        }
      }
      return false;
    },
  };
}

function mountReadOnlyBoard(container) {
  const board = container.querySelector('#wk-flow');
  if (!board) return;
  const controller = createPopoverController(board);
  board.querySelectorAll('.wf-node[data-card-id]').forEach(li => {
    const card = li.querySelector('.wf-card');
    if (!card) return;
    const cardId = li.dataset.cardId;
    card.addEventListener('click', () => {
      controller.dispatch({type: 'cardClick', id: cardId});
    });
    card.addEventListener('keydown', event => {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        controller.dispatch({type: 'cardClick', id: cardId});
      }
    });
  });
}

if (typeof document !== 'undefined') {
  document.querySelectorAll('[data-schedule-viewer]').forEach(mountReadOnlyBoard);
}
