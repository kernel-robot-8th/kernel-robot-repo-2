/** Pure reorder/validate/popover helpers shared by browser scripts and tests/reorder.test.mjs. */

export const TIERS = [
  {group: 1, name: '긴급'},
  {group: 2, name: '우선'},
  {group: 3, name: '일반'},
];

export function clampTier(tier) {
  return Math.max(1, Math.min(3, Number(tier) || 1));
}

export function frontIndex(items) {
  let lastRunning = -1;
  items.forEach((item, index) => {
    if (item.running) lastRunning = index;
  });
  return lastRunning + 1;
}

export function minAllowedTier(items) {
  let floor = 1;
  for (const item of items) {
    if (item.running) {
      floor = Math.max(floor, clampTier(item.tier));
    }
  }
  return floor;
}

export function moveItem(items, id, targetIndex) {
  const index = items.findIndex(item => item.id === id);
  if (index === -1 || items[index].running) return items.slice();
  const minIndex = frontIndex(items);
  const clamped = Math.max(minIndex, Math.min(targetIndex, items.length - 1));
  const next = items.slice();
  const [item] = next.splice(index, 1);
  next.splice(clamped, 0, item);
  return next;
}

export function canMove(items, id) {
  const index = items.findIndex(item => item.id === id);
  if (index === -1 || items[index].running) {
    return {front: false, prev: false, next: false, back: false};
  }
  const minIdx = frontIndex(items);
  const canEarlier = index > minIdx;
  const canLater = index < items.length - 1;
  return {front: canEarlier, prev: canEarlier, next: canLater, back: canLater};
}

export function moveStep(items, id, delta) {
  const index = items.findIndex(item => item.id === id);
  if (index === -1 || items[index].running || !delta) return items.slice();
  const minIdx = frontIndex(items);
  const minTier = minAllowedTier(items);

  if (delta < 0) {
    if (index <= minIdx) return items.slice();
    const targetTier = Math.max(minTier, clampTier(items[index - 1].tier));
    const next = items.slice();
    const [moved] = next.splice(index, 1);
    next.splice(index - 1, 0, {...moved, tier: targetTier});
    return next;
  }

  if (index >= items.length - 1) return items.slice();
  const targetTier = clampTier(items[index + 1].tier);
  const next = items.slice();
  const [moved] = next.splice(index, 1);
  next.splice(index + 1, 0, {...moved, tier: targetTier});
  return next;
}

export function moveToFront(items, id) {
  const index = items.findIndex(item => item.id === id);
  if (index === -1 || items[index].running) return items.slice();
  const minIdx = frontIndex(items);
  const minTier = minAllowedTier(items);
  if (index <= minIdx) return items.slice();
  const targetTier = Math.max(minTier, clampTier(items[minIdx].tier));
  const next = items.slice();
  const [moved] = next.splice(index, 1);
  next.splice(minIdx, 0, {...moved, tier: targetTier});
  return next;
}

export function moveToBack(items, id) {
  const index = items.findIndex(item => item.id === id);
  if (index === -1 || items[index].running) return items.slice();
  if (index >= items.length - 1) return items.slice();
  const targetTier = clampTier(items[items.length - 1].tier);
  const next = items.slice();
  const [moved] = next.splice(index, 1);
  next.push({...moved, tier: targetTier});
  return next;
}

export function moveToTier(items, id, targetTier, insertIndexInTier) {
  const index = items.findIndex(item => item.id === id);
  if (index === -1 || items[index].running) return items.slice();
  const minTier = minAllowedTier(items);
  const tier = Math.max(minTier, clampTier(targetTier));
  const moved = {...items[index], tier};
  const buckets = [[], [], []];
  for (const entry of items) {
    if (entry.id === id) continue;
    buckets[clampTier(entry.tier) - 1].push(entry);
  }
  const bucket = buckets[tier - 1];
  const minPos = frontIndex(bucket);
  const rawPos = insertIndexInTier === undefined ? bucket.length : Number(insertIndexInTier);
  const pos = Math.max(minPos, Math.min(Number.isFinite(rawPos) ? rawPos : bucket.length, bucket.length));
  bucket.splice(pos, 0, moved);
  return [...buckets[0], ...buckets[1], ...buckets[2]];
}

export function shiftTier(items, id, delta) {
  const index = items.findIndex(item => item.id === id);
  if (index === -1 || items[index].running || !delta) return items.slice();
  const currentTier = clampTier(items[index].tier);
  const minTier = minAllowedTier(items);
  const nextTier = Math.max(minTier, Math.min(3, currentTier + (delta < 0 ? -1 : 1)));
  if (nextTier === currentTier) return items.slice();
  const targetCount = items.filter(entry => entry.id !== id && clampTier(entry.tier) === nextTier).length;
  return moveToTier(items, id, nextTier, nextTier < currentTier ? targetCount : 0);
}

/** Keep tier numbers monotonic (1 <= tier <= 3) so outbound_reorder preserves the exact flat sequence. */
export function assignTiers(items) {
  let floor = 1;
  return items.map(item => {
    const tier = Math.max(floor, Math.min(3, item.tier || 1));
    floor = tier;
    return {...item, tier};
  });
}

export function buildLaneStrings(items, fixedFlags = [true, true, true]) {
  const buckets = [[], [], []];
  for (const item of assignTiers(items)) {
    buckets[item.tier - 1].push(`${item.id}:${item.version}`);
  }
  return buckets.map((entries, index) => `${fixedFlags[index] ? 'FIXED' : 'ANY'}|${entries.join(',')}`);
}

export function isScheduleChanged(initialItems, draftItems) {
  if (!Array.isArray(initialItems) || !Array.isArray(draftItems)) return false;
  if (initialItems.length !== draftItems.length) return true;
  for (let i = 0; i < initialItems.length; i += 1) {
    if (
      String(initialItems[i].id) !== String(draftItems[i].id) ||
      clampTier(initialItems[i].tier) !== clampTier(draftItems[i].tier)
    ) {
      return true;
    }
  }
  return false;
}

export function actionBarState(initialItems, draftItems, validation = null, busy = false) {
  const dirty = isScheduleChanged(initialItems, draftItems);
  return {
    dirty,
    showValidate: dirty,
    showApply: dirty,
    showCancel: dirty,
    applyDisabled: !dirty || busy || !Boolean(validation && validation.ok),
    validateDisabled: !dirty || busy,
    cancelDisabled: !dirty || busy,
  };
}

/**
 * Compute viewport-clamped fixed coordinates for a card detail popover.
 * Prefers below the card left-aligned; flips right-aligned near right edge and above near bottom edge.
 */
export function computePopoverPosition(anchorRect, popoverSize, viewport, options = {}) {
  const gap = options.gap ?? 8;
  const margin = options.margin ?? 12;
  const vw = Math.max(0, Number(viewport.width) || 0);
  const vh = Math.max(0, Number(viewport.height) || 0);
  const pw = Math.max(0, Number(popoverSize.width) || 0);
  const ph = Math.max(0, Number(popoverSize.height) || 0);

  let align = 'left';
  let left = anchorRect.left;
  if (left + pw > vw - margin) {
    align = 'right';
    left = anchorRect.right - pw;
  }
  const maxLeft = Math.max(margin, vw - pw - margin);
  left = Math.max(margin, Math.min(left, maxLeft));

  let placement = 'bottom';
  let top = anchorRect.bottom + gap;
  if (top + ph > vh - margin) {
    const aboveTop = anchorRect.top - ph - gap;
    const spaceAbove = anchorRect.top - margin;
    const spaceBelow = vh - anchorRect.bottom - margin;
    if (aboveTop >= margin || spaceAbove > spaceBelow) {
      placement = 'top';
      top = aboveTop;
    }
  }
  const maxTop = Math.max(margin, vh - ph - margin);
  top = Math.max(margin, Math.min(top, maxTop));

  return {
    left: Math.round(left),
    top: Math.round(top),
    align,
    placement,
  };
}

/** Pure open/close state transitions for the card detail popover. */
export function nextPopoverState(currentOpenId, event) {
  if (!event || !event.type) return currentOpenId;
  if (event.type === 'cardClick') {
    const id = event.id ?? null;
    return currentOpenId === id ? null : id;
  }
  if (event.type === 'move') {
    return event.id ?? currentOpenId;
  }
  if (
    event.type === 'escape' ||
    event.type === 'outsideClick' ||
    event.type === 'close' ||
    event.type === 'dragStart' ||
    event.type === 'cancel'
  ) {
    return null;
  }
  return currentOpenId;
}

export function applyReasonText(validation) {
  if (!validation) return '';
  if (validation.ok) return '순서 검증 통과 · 실행은 ROSY Fleet에서 승인한 뒤 시작됩니다';
  const first = Array.isArray(validation.reasons) && validation.reasons.length
    ? validation.reasons[0]
    : '이 순서로는 처리할 수 없습니다.';
  return `적용 불가: ${first}`;
}

export const DEFAULT_MOVE_SECONDS_ESTIMATE = 60;

const KST_HHMM_FORMATTER = new Intl.DateTimeFormat('ko-KR', {
  hour: '2-digit',
  minute: '2-digit',
  hour12: false,
  timeZone: 'Asia/Seoul',
});

export function formatEtaHHMM(ms) {
  if (!Number.isFinite(ms)) return '—';
  const date = new Date(ms);
  if (Number.isNaN(date.getTime())) return '—';
  const formatted = KST_HHMM_FORMATTER.format(date);
  return formatted.startsWith('24:') ? `00:${formatted.slice(3)}` : formatted;
}

/**
 * Compute per-task ETA (baseTimeMs + priorMoves * moveSecondsEstimate) for up to `limit` simulated tasks.
 * Uncomputed tasks (index >= limit or !ok) receive '—'. Tasks past cutoffMs are flagged late=true.
 */
export function computeScheduleEtas(
  items,
  {baseTimeMs, moveSecondsEstimate = DEFAULT_MOVE_SECONDS_ESTIMATE, limit = 8} = {},
) {
  const results = new Map();
  const moveSec = Number.isFinite(Number(moveSecondsEstimate)) && Number(moveSecondsEstimate) > 0
    ? Number(moveSecondsEstimate)
    : DEFAULT_MOVE_SECONDS_ESTIMATE;
  const baseMs = Number(baseTimeMs);
  const hasBase = Number.isFinite(baseMs);
  let cumulativeMoves = 0;

  (items || []).forEach((item, index) => {
    const id = String(item.id);
    if (!hasBase || index >= limit || !item.ok) {
      results.set(id, {id, etaMs: null, etaText: '—', late: false});
      return;
    }
    const etaMs = baseMs + cumulativeMoves * moveSec * 1000;
    const cutoffMs = Number.isFinite(item.cutoffMs)
      ? item.cutoffMs
      : (item.cutoffIso ? Date.parse(item.cutoffIso) : NaN);
    const late = Number.isFinite(cutoffMs) && etaMs > cutoffMs;
    results.set(id, {
      id,
      etaMs,
      etaText: formatEtaHHMM(etaMs),
      late,
    });
    const rawMoves = Number(item.moves) || 0;
    const stepMoves = item.kind === 'INBOUND' ? Math.max(1, rawMoves) : Math.max(0, rawMoves);
    cumulativeMoves += stepMoves;
  });
  return results;
}
