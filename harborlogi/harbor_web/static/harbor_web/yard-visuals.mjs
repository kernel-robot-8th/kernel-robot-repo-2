/** Pure presentation rules for 3D container colors, priority nameplates, overlap layout, and camera framing. */

export const DEFAULT_FIT_PADDING = 0.96;
export const FOCUS_FIT_PADDING = 1.08;

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
