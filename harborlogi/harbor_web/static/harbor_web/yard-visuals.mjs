/** Pure presentation rules for 3D container colors, priority nameplates, overlap layout, and camera framing. */

export const DEFAULT_FIT_PADDING = 0.96;
export const FOCUS_FIT_PADDING = 1.08;
export const DEFAULT_CAMERA_VIEW = 'front';
export const VIEW_DIRECTIONS = Object.freeze({
  front: Object.freeze({x: 0, y: 0.65, z: 1}),
  iso: Object.freeze({x: 1, y: 1.05, z: 1.3}),
  top: Object.freeze({x: 0, y: 1, z: 0.001}),
});

/** Return true iff no other box in `boxes` sits above `box` over an overlapping footprint. */
export function isTopOfStack(box, boxes = []) {
  if (!box) return false;
  const bx0 = box.x;
  const bx1 = box.x + (box.width || 1);
  const by0 = box.y;
  const by1 = box.y + (box.depth || 1);
  const bz = box.z ?? 0;
  return !(boxes || []).some(other => {
    if (!other || other.id === box.id) return false;
    const oz = other.z ?? 0;
    if (oz <= bz) return false;
    const ox0 = other.x;
    const ox1 = other.x + (other.width || 1);
    const oy0 = other.y;
    const oy1 = other.y + (other.depth || 1);
    return ox0 < bx1 && bx0 < ox1 && oy0 < by1 && by0 < oy1;
  });
}

/**
 * Compute flat top-face nameplate placement for a 3D container:
 * - Parallel to the container roof (rotX = -PI/2), resting just above the roof ribs.
 * - Oriented along the container's long axis (rotZ = PI/2 when depth > width, else 0).
 * - Visible only when no other container is stacked on top.
 */
export function topNameplateSpec(box, boxes = [], {bodyHeight = 0.82, roofOffset = 0.055} = {}) {
  const bw = box?.width || 1;
  const bd = box?.depth || 1;
  const w = Math.max(0.2, bw - 0.1);
  const d = Math.max(0.2, bd - 0.1);
  const longAlongZ = d > w;
  const longSide = longAlongZ ? d : w;
  const shortSide = longAlongZ ? w : d;
  const maxLen = longSide > shortSide * 1.2 ? longSide * 0.68 : longSide * 0.84;
  const width = Math.min(shortSide * 0.74, maxLen * 0.42);
  const length = Math.min(maxLen, width * 2.75);
  return {
    visible: isTopOfStack(box, boxes),
    x: bw / 2,
    y: bodyHeight + roofOffset,
    z: bd / 2,
    length,
    width,
    rotX: -Math.PI / 2,
    rotZ: longAlongZ ? Math.PI / 2 : 0,
    longAxis: longAlongZ ? 'z' : 'x',
  };
}

/** Paint the container body with its physical color (or neutral stored/muted token when unset); role never overwrites body color. */
export function resolveContainerBodyColor(box, palette = {}) {
  const key = typeof box?.color === 'string' ? box.color.trim().toLowerCase() : '';
  if (key && palette?.cargo?.[key]) return palette.cargo[key];
  return palette?.stored || palette?.tierNeutral || palette?.muted || '';
}

/** Resolve nameplate priority tier: open order priority_group first, then shipper_tier, else neutral. */
export function resolvePriorityTier(box) {
  const orderGroup = box?.priority_group;
  if (Number.isInteger(orderGroup) && orderGroup >= 1) {
    return orderGroup === 1 ? 'urgent' : orderGroup === 2 ? 'priority' : 'normal';
  }
  const shipperTier = box?.shipper_tier;
  if (Number.isInteger(shipperTier) && shipperTier >= 1) {
    return shipperTier === 1 ? 'urgent' : shipperTier === 2 ? 'priority' : 'normal';
  }
  if (['urgent', 'priority', 'normal', 'neutral'].includes(box?.priority_tier)) {
    return box.priority_tier;
  }
  return 'neutral';
}

/** Return nameplate fill, text, and border token colors for a container based on its priority tier. */
export function badgeColorsForBox(box, palette = {}) {
  const tier = resolvePriorityTier(box);
  const neutral = palette.tierNeutral || palette.muted || '';
  const fg = palette.white || neutral;
  const border = palette.line || neutral;
  if (tier === 'urgent') {
    return {tier, bg: palette.tierUrgent || palette.danger || neutral, fg, border: fg};
  }
  if (tier === 'priority') {
    return {tier, bg: palette.tierPriority || palette.blocker || neutral, fg, border: palette.cream || fg};
  }
  if (tier === 'normal') {
    return {tier, bg: palette.tierNormal || palette.target || neutral, fg, border};
  }
  return {tier: 'neutral', bg: neutral, fg, border};
}

/** Compute perspective camera distance covering real scene points tightly so containers fill the viewport. */
export function computeFitDistance(
  points,
  center,
  direction,
  right,
  up,
  fovDeg,
  aspect,
  {padding = DEFAULT_FIT_PADDING, minDistance = 0.4, near = 0.05} = {},
) {
  const tanY = Math.tan((fovDeg * Math.PI) / 360);
  const tanX = tanY * Math.max(0.1, Number.isFinite(aspect) && aspect > 0 ? aspect : 1);
  let distance = minDistance;
  for (const point of points) {
    const dx = point.x - center.x;
    const dy = point.y - center.y;
    const dz = point.z - center.z;
    const depth = dx * direction.x + dy * direction.y + dz * direction.z;
    const rx = Math.abs(dx * right.x + dy * right.y + dz * right.z);
    const ry = Math.abs(dx * up.x + dy * up.y + dz * up.z);
    distance = Math.max(
      distance,
      depth + (rx * padding) / tanX,
      depth + (ry * padding) / tanY,
      depth + near * 2,
    );
  }
  return distance;
}

function overlapRatio(a, b) {
  const ox = Math.max(0, Math.min(a.right, b.right) - Math.max(a.left, b.left));
  const oy = Math.max(0, Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top));
  const area = ox * oy;
  if (area <= 0) return 0;
  const selfArea = Math.max(1, (a.right - a.left) * (a.bottom - a.top));
  return area / selfArea;
}

/** Shrink or hide overlapping screen-space nameplates while keeping selected/target labels legible. */
export function layoutNameplates(items) {
  const sorted = [...items].sort((a, b) => (b.priority - a.priority) || (a.depth - b.depth));
  const placed = [];
  const results = new Map();
  const scales = [1, 0.78, 0.62];
  for (const item of sorted) {
    let chosenScale = null;
    for (const scale of scales) {
      const halfW = (item.width * scale) / 2 + 2;
      const halfH = (item.height * scale) / 2 + 2;
      const rect = {left: item.x - halfW, right: item.x + halfW, top: item.y - halfH, bottom: item.y + halfH};
      const collides = placed.some(other => overlapRatio(rect, other) > 0.08);
      if (!collides) {
        chosenScale = scale;
        placed.push(rect);
        break;
      }
    }
    if (chosenScale !== null) {
      results.set(item.id, {id: item.id, scale: chosenScale, visible: true});
      continue;
    }
    const minScale = item.priority >= 2 ? 0.78 : 0.56;
    const halfW = (item.width * minScale) / 2;
    const halfH = (item.height * minScale) / 2;
    const rect = {left: item.x - halfW, right: item.x + halfW, top: item.y - halfH, bottom: item.y + halfH};
    const worst = placed.reduce((max, other) => Math.max(max, overlapRatio(rect, other)), 0);
    if (item.priority >= 2 || worst < 0.6) {
      placed.push(rect);
      results.set(item.id, {id: item.id, scale: minScale, visible: true});
    } else {
      results.set(item.id, {id: item.id, scale: minScale, visible: false});
    }
  }
  return results;
}

/** Concise inspector summary for a selected 3D container including color, tier, shipper, destination, voyage, and cutoff. */
export function formatBoxInfo(box, {where = '', rehandle = false} = {}) {
  if (!box) return '';
  const parts = [
    box.id,
    box.label + (rehandle ? ' (재취급 예정 · 계획 기준)' : ''),
    where,
  ];
  if (box.color_label) {
    parts.push(box.color_confirmed ? box.color_label : `${box.color_label}(색 확인 전)`);
  }
  if (box.priority_label) parts.push(box.priority_label);
  if (box.shipper) parts.push(box.shipper);
  if (box.destination) parts.push(box.destination);
  if (box.voyage && box.voyage !== box.destination) parts.push(box.voyage);
  if (box.cutoff) parts.push(`마감 ${box.cutoff}`);
  return parts.filter(Boolean).join(' · ');
}

/** Structured playback caption fields ({step, route, phase, percent}) for one move frame/segment. */
export function describeMoveText(frame, segment, t, {total = 0, locate = box => '', draft = false} = {}) {
  const clamped = Math.max(0, Math.min(1, Number.isFinite(t) ? t : 0));
  const percent = Math.round(clamped * 100);
  const move = frame?.move || null;
  const from = segment?.from || null;
  const to = segment?.to || null;
  const cargo = move?.cargo_id || move?.cargo || to?.id || '';
  const phaseLabel = move?.phase_label || move?.phase || frame?.phase || '';
  const number = move?.number ?? frame?.index ?? 0;

  let step = frame?.label || '현재 야드 배치';
  if (draft && to) {
    step = total > 0 && number > 0
      ? `${number}/${total} · ${cargo} 가상 이동`
      : `${cargo} 가상 이동`;
  } else if (number > 0 && total > 0 && cargo) {
    step = `${number}/${total} · ${cargo}${phaseLabel ? ` ${phaseLabel}` : ''}`;
  } else if (number > 0 && cargo && phaseLabel) {
    step = `${number} · ${cargo} ${phaseLabel}`;
  }

  const source = move?.source_place || (from ? locate(from) : '출발 위치 없음');
  const destination = move?.destination_place || (to ? locate(to) : '');
  const arm = move?.primary_arm || move?.arm || '';
  const routePair = destination ? `${source} → ${destination}` : source;
  const route = arm ? `팔 ${arm} · ${routePair}` : routePair;

  const phase = !from && !move?.source_place
    ? '배치 비교'
    : segment?.instant
      ? '즉시 전환'
      : clamped < 0.25
        ? '들어 올림'
        : clamped < 0.75
          ? '이동'
          : clamped < 1
            ? '내려놓기'
            : '완료';

  return {step, route, phase, percent, source, destination, cargo, arm};
}
