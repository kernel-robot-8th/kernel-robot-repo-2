import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import {
  DEFAULT_FIT_PADDING,
  badgeColorsForBox,
  computeFitDistance,
  formatBoxInfo,
  layoutNameplates,
  resolveContainerBodyColor,
  resolvePriorityTier,
} from '../harbor_web/static/harbor_web/yard-visuals.mjs';

const palette = {
  target: '#112c3f',
  blocker: '#fe6d08',
  stored: '#2cbbb7',
  danger: '#b72d43',
  tierUrgent: '#b72d43',
  tierPriority: '#b94d00',
  tierNormal: '#112c3f',
  tierNeutral: '#506878',
  cream: '#fcf1d9',
  line: '#d7e3e9',
  white: '#ffffff',
  muted: '#506878',
  cargo: {
    orange: '#e86c1f',
    pink: '#d95b8c',
    yellow: '#e3b522',
    sky: '#3ea0d8',
  },
};

test('nameplate priority color uses open order priority_group first, then shipper_tier, then neutral', () => {
  // Open order priority_group takes precedence over shipper_tier.
  assert.equal(resolvePriorityTier({priority_group: 1, shipper_tier: 3}), 'urgent');
  assert.equal(resolvePriorityTier({priority_group: 2, shipper_tier: 1}), 'priority');
  assert.equal(resolvePriorityTier({priority_group: 3, shipper_tier: 1}), 'normal');
  assert.equal(resolvePriorityTier({priority_group: 5, shipper_tier: 1}), 'normal');

  // Falls back to shipper_tier when there is no open order.
  assert.equal(resolvePriorityTier({priority_group: null, shipper_tier: 1}), 'urgent');
  assert.equal(resolvePriorityTier({priority_group: null, shipper_tier: 2}), 'priority');
  assert.equal(resolvePriorityTier({priority_group: null, shipper_tier: 3}), 'normal');

  // Falls back to neutral when neither is present.
  assert.equal(resolvePriorityTier({priority_group: null, shipper_tier: null}), 'neutral');
  assert.equal(resolvePriorityTier({}), 'neutral');

  const urgent = badgeColorsForBox({priority_group: 1}, palette);
  const priority = badgeColorsForBox({priority_group: 2}, palette);
  const normal = badgeColorsForBox({priority_group: 3}, palette);
  const neutral = badgeColorsForBox({}, palette);

  assert.equal(urgent.bg, palette.tierUrgent);
  assert.equal(priority.bg, palette.tierPriority);
  assert.equal(normal.bg, palette.tierNormal);
  assert.equal(neutral.bg, palette.tierNeutral);
  assert.equal(new Set([urgent.bg, priority.bg, normal.bg, neutral.bg]).size, 4);
});

test('container body color uses cargo physical color or neutral and is never overwritten by target/blocker role', () => {
  assert.equal(resolveContainerBodyColor({color: 'orange', role: 'target'}, palette), palette.cargo.orange);
  assert.equal(resolveContainerBodyColor({color: 'pink', role: 'blocker'}, palette), palette.cargo.pink);
  assert.equal(resolveContainerBodyColor({color: 'yellow', role: 'stored'}, palette), palette.cargo.yellow);
  assert.equal(resolveContainerBodyColor({color: 'sky', role: 'target'}, palette), palette.cargo.sky);
  assert.equal(resolveContainerBodyColor({color: '', role: 'target'}, palette), palette.stored);
  assert.equal(resolveContainerBodyColor({color: 'unknown', role: 'blocker'}, palette), palette.stored);
});

test('yard-visuals contains no hardcoded hex fallbacks and falls back to neutral design token when missing', () => {
  const source = fs.readFileSync(new URL('../harbor_web/static/harbor_web/yard-visuals.mjs', import.meta.url), 'utf8');
  assert.equal(/#[0-9a-f]{3,8}/i.test(source), false, 'yard-visuals.mjs must not contain hardcoded hex colors');

  const neutralOnly = {tierNeutral: 'var(--primitive-color-muted)', white: 'var(--primitive-color-white)', line: 'var(--semantic-border-divider)'};
  assert.equal(resolveContainerBodyColor({color: 'pink'}, neutralOnly), neutralOnly.tierNeutral);
  assert.equal(badgeColorsForBox({priority_group: 1}, neutralOnly).bg, neutralOnly.tierNeutral);
  assert.equal(badgeColorsForBox({priority_group: 2}, neutralOnly).bg, neutralOnly.tierNeutral);
  assert.equal(badgeColorsForBox({priority_group: 3}, neutralOnly).bg, neutralOnly.tierNeutral);
  assert.equal(badgeColorsForBox({}, neutralOnly).bg, neutralOnly.tierNeutral);
});

test('default camera fit distance is strictly closer than legacy 1.12x union-AABB framing', () => {
  // Transposed 3x6 site areas: yard + temp_a + temp_b + stand.
  const areas = [
    {x: 0, y: 0, columns: 3, rows: 6},
    {x: -4.2, y: 0, columns: 3, rows: 3},
    {x: 4.2, y: 0, columns: 3, rows: 3},
    {x: -4.2, y: 7.2, columns: 1, rows: 3},
  ];
  const realPoints = [];
  for (const a of areas) {
    for (const x of [a.x, a.x + a.columns]) {
      for (const z of [a.y, a.y + a.rows + 0.35]) {
        realPoints.push({x, y: 0, z}, {x, y: 0.4, z});
      }
    }
  }
  // Add a 3-high yard stack.
  for (const x of [0, 1]) for (const y of [0, 3]) for (const z of [0, 3]) realPoints.push({x, y, z});

  const minX = Math.min(...realPoints.map(p => p.x));
  const maxX = Math.max(...realPoints.map(p => p.x));
  const minY = Math.min(...realPoints.map(p => p.y));
  const maxY = Math.max(...realPoints.map(p => p.y));
  const minZ = Math.min(...realPoints.map(p => p.z));
  const maxZ = Math.max(...realPoints.map(p => p.z));
  const center = {x: (minX + maxX) / 2, y: (minY + maxY) / 2, z: (minZ + maxZ) / 2};

  // Legacy union AABB (all 8 corners with +0.8 sign margin and 1.12 padding).
  const legacyCorners = [];
  for (const x of [minX, maxX]) for (const y of [minY, maxY]) for (const z of [minZ, maxZ + 0.45]) {
    legacyCorners.push({x, y, z});
  }

  // Camera basis matching default isometric position (12, 13, 17).
  const len = Math.hypot(12, 13, 17);
  const direction = {x: 12 / len, y: 13 / len, z: 17 / len};
  // up=(0,1,0) x direction
  const rRaw = {x: direction.z, y: 0, z: -direction.x};
  const rLen = Math.hypot(rRaw.x, rRaw.y, rRaw.z);
  const right = {x: rRaw.x / rLen, y: 0, z: rRaw.z / rLen};
  const up = {
    x: direction.y * right.z - direction.z * right.y,
    y: direction.z * right.x - direction.x * right.z,
    z: direction.x * right.y - direction.y * right.x,
  };

  const legacyDistance = computeFitDistance(legacyCorners, center, direction, right, up, 40, 1.6, {padding: 1.12});
  const tighterDistance = computeFitDistance(realPoints, center, direction, right, up, 40, 1.6, {padding: DEFAULT_FIT_PADDING});

  assert.ok(tighterDistance > 0.4);
  assert.ok(
    tighterDistance < legacyDistance * 0.82,
    `Expected tighter fit (${tighterDistance.toFixed(2)}) < 82% of legacy (${legacyDistance.toFixed(2)})`,
  );
});

test('layoutNameplates shrinks overlapping badges and keeps selected/target visible', () => {
  const layout = layoutNameplates([
    {id: 'C-1', x: 200, y: 200, width: 64, height: 24, priority: 3, depth: 0.5},
    {id: 'C-2', x: 222, y: 204, width: 64, height: 24, priority: 0, depth: 0.6},
    {id: 'C-3', x: 201, y: 201, width: 64, height: 24, priority: 0, depth: 0.7},
    {id: 'C-4', x: 420, y: 200, width: 64, height: 24, priority: 0, depth: 0.6},
  ]);
  assert.equal(layout.get('C-1').visible, true);
  assert.equal(layout.get('C-1').scale, 1);
  assert.equal(layout.get('C-4').visible, true);
  assert.equal(layout.get('C-4').scale, 1);
  // Overlapping neighbor shrinks; completely occluded lower-priority label hides.
  assert.ok(layout.get('C-2').scale < 1);
  assert.equal(layout.get('C-3').visible, false);
});

test('formatBoxInfo appends color confirmation status, tier, shipper, destination, voyage, and cutoff', () => {
  const confirmed = formatBoxInfo(
    {
      id: 'C-1', label: '반출 대상', color_label: '분홍', color_confirmed: true,
      priority_label: '긴급', shipper: '한진특송', destination: 'V-BUSAN-01 선적',
      voyage: 'V-BUSAN-01', cutoff: '11-26 04:18',
    },
    {where: '1행 1열 1층', rehandle: false},
  );
  assert.ok(confirmed.includes('C-1'));
  assert.ok(confirmed.includes('분홍'));
  assert.ok(!confirmed.includes('색 확인 전'));
  assert.ok(confirmed.includes('긴급'));
  assert.ok(confirmed.includes('한진특송'));
  assert.ok(confirmed.includes('V-BUSAN-01'));
  assert.ok(confirmed.includes('마감 11-26 04:18'));

  const unconfirmed = formatBoxInfo(
    {
      id: 'C-14', label: '보관 화물', color_label: '하늘', color_confirmed: false,
      priority_label: '우선', shipper: '한진특송', destination: 'V-BUSAN-01 선적',
      voyage: 'V-BUSAN-01', cutoff: '11-26 06:18',
    },
    {where: '3행 1열 2층', rehandle: false},
  );
  assert.ok(unconfirmed.includes('하늘(색 확인 전)'));
});
