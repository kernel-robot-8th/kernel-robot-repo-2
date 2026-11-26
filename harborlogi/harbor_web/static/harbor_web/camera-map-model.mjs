/** Pure coordinate transforms, visibility rules, and camera selection for the Yard Camera <-> 3D Map. */

const DEFAULT_PLANE = Object.freeze({
  x_min: -1.37,
  x_max: 1.37,
  y_min: -0.76,
  y_max: 0.76,
  pixels_per_meter: 400,
  image: [1096, 608],
});

export function normalizePlane(raw = DEFAULT_PLANE) {
  const src = raw && typeof raw === 'object' ? raw : DEFAULT_PLANE;
  const xMin = Number(src.x_min);
  const xMax = Number(src.x_max);
  const yMin = Number(src.y_min);
  const yMax = Number(src.y_max);
  const ppm = Number(src.pixels_per_meter);
  const valid =
    Number.isFinite(xMin)
    && Number.isFinite(xMax)
    && Number.isFinite(yMin)
    && Number.isFinite(yMax)
    && Number.isFinite(ppm)
    && xMin < xMax
    && yMin < yMax
    && ppm > 0;
  if (!valid) {
    if (src === DEFAULT_PLANE) throw new Error('Invalid default plane');
    return normalizePlane(DEFAULT_PLANE);
  }
  const widthM = xMax - xMin;
  const heightM = yMax - yMin;
  const imageW =
    Array.isArray(src.image) && Number.isFinite(Number(src.image[0])) && Number(src.image[0]) > 0
      ? Math.round(Number(src.image[0]))
      : Math.round(widthM * ppm);
  const imageH =
    Array.isArray(src.image) && Number.isFinite(Number(src.image[1])) && Number(src.image[1]) > 0
      ? Math.round(Number(src.image[1]))
      : Math.round(heightM * ppm);
  return {
    x_min: xMin,
    x_max: xMax,
    y_min: yMin,
    y_max: yMax,
    pixels_per_meter: ppm,
    width_m: widthM,
    height_m: heightM,
    center_x: (xMin + xMax) / 2,
    center_y: (yMin + yMax) / 2,
    image_w: imageW,
    image_h: imageH,
  };
}

/** Parse X-Frame-Plane header ("x_min,y_min,x_max,y_max,ppm"); falls back cleanly if absent or invalid. */
export function parseFramePlane(headerValue, fallbackPlane = DEFAULT_PLANE) {
  const fallback = normalizePlane(fallbackPlane);
  if (typeof headerValue !== 'string' || !headerValue.trim()) return fallback;
  const parts = headerValue.split(',').map(part => Number(part.trim()));
  if (parts.length !== 5 || parts.some(v => !Number.isFinite(v))) return fallback;
  const [xMin, yMin, xMax, yMax, ppm] = parts;
  if (!(xMin < xMax && yMin < yMax && ppm > 0)) return fallback;
  return normalizePlane({
    x_min: xMin,
    x_max: xMax,
    y_min: yMin,
    y_max: yMax,
    pixels_per_meter: ppm,
  });
}

/** Convert birdview plane pixels (u, v) to ROSY map plane meters (x, y; image top = +y). */
export function planePxToRosyMeters(u, v, plane) {
  const p = normalizePlane(plane);
  return {
    x: p.x_min + u / p.pixels_per_meter,
    y: p.y_max - v / p.pixels_per_meter,
  };
}

/** Convert ROSY map plane meters (x, y) to birdview plane pixels [u, v]. */
export function rosyMetersToPlanePx(x, y, plane) {
  const p = normalizePlane(plane);
  return [
    (x - p.x_min) * p.pixels_per_meter,
    (p.y_max - y) * p.pixels_per_meter,
  ];
}

/** Convert ROSY map plane meters (x, y) to Three.js world ground coordinates {x, z} (image up = -Z). */
export function rosyMetersToWorld(x, y) {
  return {x, z: -y};
}

/** Convert birdview plane pixels (u, v) directly to Three.js world ground coordinates {x, z}. */
export function planePxToWorld(u, v, plane) {
  const m = planePxToRosyMeters(u, v, plane);
  return rosyMetersToWorld(m.x, m.y);
}

/** Convert Three.js world ground coordinates (wx, wz) to birdview plane pixels [u, v]. */
export function worldToPlanePx(worldX, worldZ, plane) {
  return rosyMetersToPlanePx(worldX, -worldZ, plane);
}

/** Expand validated site-3d-layout.json plates + conveyor belt into 3D cell descriptors. */
export function buildCellsFromLayout(layout) {
  const plane = normalizePlane(layout?.plane);
  const cells = [];
  const plates = layout?.plates || {};
  for (const [zone, plate] of Object.entries(plates)) {
    const [w, h, d] = plate.size_m;
    const rows = plate.rows || [];
    const cols = plate.cols || [];
    const tiers = Number(plate.tiers || 1);
    rows.forEach((row, ri) => {
      cols.forEach((col, si) => {
        const world = planePxToWorld(plate.u_by_row[ri], plate.v_by_slot[si], plane);
        for (let tier = 1; tier <= tiers; tier += 1) {
          cells.push({
            ref: `${zone}-r${row}-c${col}-l${tier}`,
            zone,
            row,
            col,
            tier,
            pos: {x: world.x, y: h * (tier - 0.5), z: world.z},
            size: {w, h, d},
          });
        }
      });
    });
  }
  if (layout?.conveyor?.belt) {
    const belt = layout.conveyor.belt;
    const [w, h, d] = belt.size_m;
    const world = planePxToWorld(belt.uv[0], belt.uv[1], plane);
    cells.push({
      ref: 'belt',
      zone: 'belt',
      row: 1,
      col: 1,
      tier: 1,
      pos: {x: world.x, y: h * 0.5, z: world.z},
      size: {w, h, d},
    });
  }
  return cells;
}

/** Infer zone name from a canonical wire_ref when zone is not passed explicitly. */
export function zoneFromSiteRef(ref) {
  if (typeof ref !== 'string') return '';
  if (ref === 'belt' || ref === 'belt-in') return 'belt';
  if (ref.startsWith('not_places:')) return 'not_places';
  const match = ref.match(/^([a-z][a-z0-9_]*)-r\d+-c\d+-l\d+$/);
  return match ? match[1] : '';
}

/** Determine whether a canonical cell ref is visible ('yes'), occluded ('hidden'), or out of view ('no'). */
export function cellVisibility(camKey, ref, zone = null) {
  const z = zone || zoneFromSiteRef(ref);
  if (camKey === 'arms') {
    if (ref === 'belt-in' || ref === 'not_places:vehicle_tray') return 'yes';
    return 'no';
  }
  if (camKey === 'top' || camKey === 'bird') {
    if (z === 'yard' || z === 'temp_a' || z === 'temp_b' || z === 'stand' || ref === 'belt') return 'yes';
    return 'no';
  }
  if (camKey === 'side') {
    if (z === 'yard') return /-c1-/.test(ref) ? 'yes' : 'hidden';
    return 'no';
  }
  return 'no';
}

/** Partition cells into visible ('yes'), occluded ('hidden'), and out-of-view ('no') lists for a camera. */
export function visibleCellsForCamera(camKey, cells) {
  const result = {yes: [], hidden: [], no: []};
  for (const cell of cells || []) {
    const ref = typeof cell === 'string' ? cell : cell.ref;
    const zone = typeof cell === 'string' ? null : cell.zone;
    const status = cellVisibility(camKey, ref, zone);
    result[status].push(ref);
  }
  return result;
}

/** Pick the best camera to inspect a given site_ref (used by the ROSY recognition "카메라로 확인" button). */
export function pickCameraForSite(siteRef, cameras = []) {
  const list = Array.isArray(cameras) && cameras.length
    ? cameras
    : [
      {key: 'top', available: false},
      {key: 'side', available: false},
      {key: 'bird', available: false},
      {key: 'arms', available: false},
    ];
  const byKey = new Map(list.map(c => [c.key, c]));
  const sideSeesDirectly = cellVisibility('side', siteRef) === 'yes';
  const order = sideSeesDirectly ? ['side', 'top', 'bird', 'arms'] : ['top', 'bird', 'side', 'arms'];

  // Prefer an available camera that directly sees the cell.
  for (const key of order) {
    const cam = byKey.get(key);
    if (cam && cam.available && cellVisibility(key, siteRef) === 'yes') return key;
  }
  // Otherwise pick the best canonical camera that sees the cell (even if stream is offline).
  for (const key of order) {
    if (byKey.has(key) && cellVisibility(key, siteRef) === 'yes') return key;
  }
  return byKey.has('top') ? 'top' : (list[0]?.key || 'top');
}

/** Pick the front visible wire_ref for a sideview tier box (e.g. yard c1, temp_a r1, temp_b r3). */
export function sideOverlayPrimaryRef(wireRefs) {
  if (!Array.isArray(wireRefs) || !wireRefs.length) return '';
  return wireRefs.find(ref => cellVisibility('side', ref) === 'yes') || wireRefs[0];
}

/** Project [x0, y0, x1, y1] ROI into SVG coordinates, applying 180-degree flip when configured. */
export function transformOverlayRoi(roi, imageSize, flip = false) {
  const [x0, y0, x1, y1] = roi;
  const [vw, vh] = imageSize;
  const x = flip ? vw - x1 : x0;
  const y = flip ? vh - y1 : y0;
  return {x, y, width: x1 - x0, height: y1 - y0};
}

/** Project [x, y] pick point into SVG coordinates, applying 180-degree flip when configured. */
export function transformOverlayPoint(point, imageSize, flip = false) {
  const [px, py] = point;
  const [vw, vh] = imageSize;
  return {
    x: flip ? vw - px : px,
    y: flip ? vh - py : py,
  };
}

/** Project [[x, y], ...] polygon into SVG coordinates, applying 180-degree flip when configured. */
export function transformOverlayPolygon(polygon, imageSize, flip = false) {
  const points = polygon.map(vertex => transformOverlayPoint(vertex, imageSize, flip));
  const count = points.length || 1;
  const cx = points.reduce((sum, pt) => sum + pt.x, 0) / count;
  const cy = points.reduce((sum, pt) => sum + pt.y, 0) / count;
  const pointsAttr = points.map(pt => `${pt.x.toFixed(1)},${pt.y.toFixed(1)}`).join(' ');
  return {points, cx, cy, pointsAttr};
}

/** Return true iff no other occupied container sits on a higher tier of the same (zone, row, col) stack. */
export function isTopContainerInStack(siteRef, containers = {}) {
  if (typeof siteRef !== 'string') return false;
  const match = siteRef.match(/^([a-z][a-z0-9_]*-r\d+-c\d+)-l(\d+)$/);
  if (!match) return true;
  const prefix = `${match[1]}-l`;
  const tier = Number(match[2]);
  for (const otherRef of Object.keys(containers || {})) {
    if (otherRef === siteRef || !otherRef.startsWith(prefix)) continue;
    const otherTier = Number(otherRef.slice(prefix.length));
    if (Number.isFinite(otherTier) && otherTier > tier) return false;
  }
  return true;
}

/** Compute flat top-face nameplate geometry for a 3D container box (parallel to top face, text along long axis). */
export function containerTopNameplateLayout({w, h, d}) {
  const longAlongZ = d > w;
  const longSide = longAlongZ ? d : w;
  const shortSide = longAlongZ ? w : d;
  const maxLen = longSide > shortSide * 1.2 ? longSide * 0.72 : longSide * 0.84;
  const width = Math.min(shortSide * 0.76, maxLen * 0.42);
  const length = Math.min(maxLen, width * 2.75);
  return {
    y: h / 2 + 0.0015,
    length,
    width,
    rotX: -Math.PI / 2,
    rotZ: longAlongZ ? Math.PI / 2 : 0,
    longAxis: longAlongZ ? 'z' : 'x',
  };
}

/** Resolve priority-tier badge colors (urgent / priority / normal / neutral) from design-token palette. */
export function resolvePriorityBadgeColors(container, palette = {}) {
  const tier = ['urgent', 'priority', 'normal', 'neutral'].includes(container?.priority_tier)
    ? container.priority_tier
    : 'neutral';
  const neutral = palette.tierNeutral || palette.muted || '';
  const fg = palette.white || neutral;
  const border = palette.line || neutral;
  if (tier === 'urgent') {
    return {tier, bg: palette.tierUrgent || palette.danger || neutral, fg, border: fg};
  }
  if (tier === 'priority') {
    return {tier, bg: palette.tierPriority || palette.orange || neutral, fg, border: palette.cream || fg};
  }
  if (tier === 'normal') {
    return {tier, bg: palette.tierNormal || palette.navy || neutral, fg, border};
  }
  return {tier: 'neutral', bg: neutral, fg, border};
}

/** Resolve 3D container face fill and text color from Cargo.color using design-token palette. */
export function resolveContainerVisual(container, palette = {}) {
  const colorKey = typeof container?.color === 'string' ? container.color.trim().toLowerCase() : '';
  const cargoFill = colorKey && palette?.cargo?.[colorKey] ? palette.cargo[colorKey] : '';
  const fill = cargoFill || palette.teal || '';
  const fg = colorKey === 'yellow' ? (palette.navy || palette.ink || '') : (palette.white || '');
  return {fill, fg};
}
