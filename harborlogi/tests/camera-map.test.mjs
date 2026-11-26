import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import {fileURLToPath} from 'node:url';

import {
  buildCellsFromLayout,
  cellVisibility,
  containerTopNameplateLayout,
  isTopContainerInStack,
  normalizePlane,
  parseFramePlane,
  pickCameraForSite,
  planePxToRosyMeters,
  planePxToWorld,
  resolveContainerVisual,
  resolvePriorityBadgeColors,
  rosyMetersToPlanePx,
  rosyMetersToWorld,
  sideOverlayPrimaryRef,
  transformOverlayPoint,
  transformOverlayPolygon,
  transformOverlayRoi,
  visibleCellsForCamera,
  worldToPlanePx,
} from '../harbor_web/static/harbor_web/camera-map-model.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, '..');
const LAYOUT = JSON.parse(fs.readFileSync(path.join(ROOT, 'config/site-3d-layout.json'), 'utf8'));

test('plane pixel <-> ROSY meters <-> Three.js world coordinate conversions round-trip accurately', () => {
  const plane = normalizePlane(LAYOUT.plane);
  assert.equal(plane.image_w, 1096);
  assert.equal(plane.image_h, 608);

  // Center pixel (548, 304) maps to (0, 0) in both ROSY meters and Three.js world.
  const centerM = planePxToRosyMeters(548, 304, plane);
  assert.ok(Math.abs(centerM.x) < 1e-9);
  assert.ok(Math.abs(centerM.y) < 1e-9);
  assert.deepEqual(rosyMetersToPlanePx(0, 0, plane), [548, 304]);

  // Top-left (0, 0) maps to (x_min = -1.37, y_max = +0.76) -> world (-1.37, z = -0.76).
  const tl = planePxToRosyMeters(0, 0, plane);
  assert.ok(Math.abs(tl.x - (-1.37)) < 1e-9);
  assert.ok(Math.abs(tl.y - 0.76) < 1e-9);
  const tlWorld = planePxToWorld(0, 0, plane);
  assert.ok(Math.abs(tlWorld.x - (-1.37)) < 1e-9);
  assert.ok(Math.abs(tlWorld.z - (-0.76)) < 1e-9);

  // Round-trip arbitrary ROSY meter point (arm_a: [0.55, 0.36]).
  const [u, v] = rosyMetersToPlanePx(0.55, 0.36, plane);
  const back = planePxToRosyMeters(u, v, plane);
  assert.ok(Math.abs(back.x - 0.55) < 1e-9);
  assert.ok(Math.abs(back.y - 0.36) < 1e-9);
  const w = rosyMetersToWorld(0.55, 0.36);
  const [u2, v2] = worldToPlanePx(w.x, w.z, plane);
  assert.ok(Math.abs(u2 - u) < 1e-9);
  assert.ok(Math.abs(v2 - v) < 1e-9);
});

test('parseFramePlane prefers valid X-Frame-Plane header and falls back cleanly on invalid input', () => {
  const custom = parseFramePlane('-1.50,-0.80,1.50,0.80,300.0000', LAYOUT.plane);
  assert.equal(custom.x_min, -1.5);
  assert.equal(custom.x_max, 1.5);
  assert.equal(custom.y_min, -0.8);
  assert.equal(custom.y_max, 0.8);
  assert.equal(custom.pixels_per_meter, 300);
  assert.equal(custom.image_w, 900);
  assert.equal(custom.image_h, 480);

  for (const bad of [null, '', '1,2,3', '2,0,-1,1,400', '-1,-1,1,1,0', 'a,b,c,d,e']) {
    const fallback = parseFramePlane(bad, LAYOUT.plane);
    assert.equal(fallback.x_min, -1.37);
    assert.equal(fallback.pixels_per_meter, 400);
  }
});

test('camera visibility partitions all 37 cells + belt accurately per selected camera', () => {
  const cells = buildCellsFromLayout(LAYOUT);
  assert.equal(cells.length, 38); // 18 yard + 9 temp_a + 9 temp_b + 1 stand + 1 belt

  const topVis = visibleCellsForCamera('top', cells);
  assert.equal(topVis.yes.length, 38);
  assert.equal(topVis.hidden.length, 0);
  assert.equal(topVis.no.length, 0);

  const birdVis = visibleCellsForCamera('bird', cells);
  assert.equal(birdVis.yes.length, 38);

  const sideVis = visibleCellsForCamera('side', cells);
  // Visible on side: yard c1 only (3 rows * 3 tiers = 9); temp_a and temp_b are excluded on side view.
  assert.equal(sideVis.yes.length, 9);
  // Hidden on side: yard c4 (9)
  assert.equal(sideVis.hidden.length, 9);
  // Out of view / excluded on side: temp_a (9) + temp_b (9) + stand (1) + belt (1) = 20
  assert.equal(sideVis.no.length, 20);
  assert.equal(cellVisibility('side', 'yard-r1-c1-l2'), 'yes');
  assert.equal(cellVisibility('side', 'yard-r1-c4-l2'), 'hidden');
  assert.equal(cellVisibility('side', 'temp_a-r1-c1-l1'), 'no');
  assert.equal(cellVisibility('side', 'temp_a-r2-c1-l1'), 'no');
  assert.equal(cellVisibility('side', 'temp_b-r3-c1-l1'), 'no');
  assert.equal(cellVisibility('side', 'temp_b-r1-c1-l1'), 'no');
  assert.equal(cellVisibility('side', 'stand-r1-c1-l1'), 'no');

  const armsVis = visibleCellsForCamera('arms', cells);
  assert.equal(armsVis.no.length, 38);
  assert.equal(cellVisibility('arms', 'belt-in'), 'yes');
});

test('pickCameraForSite chooses the best viewing camera for ROSY recognition mismatches', () => {
  const allOnline = [
    {key: 'top', available: true},
    {key: 'side', available: true},
    {key: 'bird', available: true},
    {key: 'arms', available: true},
  ];
  // Front-row yard c1 -> side shows exact tier.
  assert.equal(pickCameraForSite('yard-r1-c1-l2', allOnline), 'side');
  // Temp plates (excluded from sideview), back-row yard c4, and stand -> top sees directly.
  assert.equal(pickCameraForSite('temp_a-r1-c1-l1', allOnline), 'top');
  assert.equal(pickCameraForSite('temp_b-r3-c1-l2', allOnline), 'top');
  assert.equal(pickCameraForSite('yard-r1-c4-l1', allOnline), 'top');
  assert.equal(pickCameraForSite('temp_a-r2-c1-l1', allOnline), 'top');
  assert.equal(pickCameraForSite('stand-r1-c1-l1', allOnline), 'top');

  // If side is offline, front-row falls back to available top.
  const noSide = [
    {key: 'top', available: true},
    {key: 'side', available: false},
    {key: 'bird', available: true},
    {key: 'arms', available: false},
  ];
  assert.equal(pickCameraForSite('yard-r1-c1-l2', noSide), 'top');
  assert.equal(sideOverlayPrimaryRef(['yard-r1-c4-l1', 'yard-r1-c1-l1']), 'yard-r1-c1-l1');
});

test('polygon overlay flip and top-of-stack flat nameplates work accurately', () => {
  const poly = [[521, 376], [559, 370], [595, 476], [552, 479]];
  const flippedPoly = transformOverlayPolygon(poly, [640, 480], true);
  assert.deepEqual(flippedPoly.points[0], {x: 119, y: 104});
  assert.deepEqual(flippedPoly.points[2], {x: 45, y: 4});
  assert.equal(flippedPoly.pointsAttr, '119.0,104.0 81.0,110.0 45.0,4.0 88.0,1.0');

  const unflipped = transformOverlayPolygon(poly, [640, 480], false);
  assert.deepEqual(unflipped.points[0], {x: 521, y: 376});

  // Only top container of a stack gets a top-face nameplate.
  const stack = {
    'yard-r1-c1-l1': {code: 'C-1', priority_tier: 'urgent'},
    'yard-r1-c1-l2': {code: 'C-2', priority_tier: 'normal'},
    'yard-r2-c1-l1': {code: 'C-10', priority_tier: 'priority'},
  };
  assert.equal(isTopContainerInStack('yard-r1-c1-l1', stack), false);
  assert.equal(isTopContainerInStack('yard-r1-c1-l2', stack), true);
  assert.equal(isTopContainerInStack('yard-r2-c1-l1', stack), true);

  // Flat top-face orientation follows the container's long axis.
  const longZ = containerTopNameplateLayout({w: 0.052, h: 0.04, d: 0.13});
  assert.equal(longZ.rotX, -Math.PI / 2);
  assert.equal(longZ.rotZ, Math.PI / 2);
  assert.equal(longZ.longAxis, 'z');
  assert.ok(longZ.y > 0.02);

  const longX = containerTopNameplateLayout({w: 0.13, h: 0.04, d: 0.052});
  assert.equal(longX.rotX, -Math.PI / 2);
  assert.equal(longX.rotZ, 0);
  assert.equal(longX.longAxis, 'x');

  const badgePalette = {
    tierUrgent: 'u',
    tierPriority: 'p',
    tierNormal: 'n',
    tierNeutral: 'm',
    white: 'w',
    cream: 'c',
    line: 'l',
  };
  assert.equal(resolvePriorityBadgeColors({priority_tier: 'urgent'}, badgePalette).bg, 'u');
  assert.equal(resolvePriorityBadgeColors({priority_tier: 'priority'}, badgePalette).bg, 'p');
  assert.equal(resolvePriorityBadgeColors({priority_tier: 'normal'}, badgePalette).bg, 'n');
  assert.equal(resolvePriorityBadgeColors({}, badgePalette).bg, 'm');
});

test('overlay flip and container visuals follow token rules without hardcoded hex colors', () => {
  const flipped = transformOverlayRoi([333, 298, 368, 400], [640, 480], true);
  assert.deepEqual(flipped, {x: 272, y: 80, width: 35, height: 102});
  assert.deepEqual(transformOverlayPoint([311, 125], [640, 480], true), {x: 329, y: 355});

  const palette = {
    teal: '#087c80',
    navy: '#112c3f',
    white: '#ffffff',
    cargo: {orange: '#e86c1f', pink: '#d95b8c', yellow: '#e3b522', sky: '#3ea0d8'},
  };
  assert.deepEqual(resolveContainerVisual({color: 'pink'}, palette), {fill: '#d95b8c', fg: '#ffffff'});
  assert.deepEqual(resolveContainerVisual({color: 'yellow'}, palette), {fill: '#e3b522', fg: '#112c3f'});
  assert.deepEqual(resolveContainerVisual({color: ''}, palette), {fill: '#087c80', fg: '#ffffff'});

  for (const rel of ['harbor_web/static/harbor_web/camera-map-model.mjs', 'harbor_web/static/harbor_web/camera-map.js']) {
    const src = fs.readFileSync(path.join(ROOT, rel), 'utf8');
    assert.equal(/#[0-9a-f]{3,8}\b/i.test(src), false, `${rel} must not contain hardcoded hex colors`);
  }
});
