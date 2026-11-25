import assert from 'node:assert/strict';
import {
  pollDelay,
  frameStatus,
  mountTopview,
  resolveRotationDeg,
  displayRotationDeg,
  projectPoint,
  projectRoi,
  shouldShowOverlay,
  buildOverlayItems,
} from '../harbor_web/static/harbor_web/topview.mjs';

assert.equal(pollDelay(200, false), 1000);
for (const status of [0, 404, 502, 503]) {
  assert.equal(pollDelay(status, false), 3000);
  assert.equal(pollDelay(status, true), null);
}
assert.equal(pollDelay(200, true), null);
assert.equal(frameStatus(404), '그림이 오래됨');
for (const status of [0, 502, 503]) assert.equal(frameStatus(status), 'ROSY 연결 안 됨');
assert.equal(frameStatus(200), '');
assert.equal(frameStatus(200, 'invalid'), '');
const capturedDate = new Date(2026, 10, 21, 9, 8, 7);
const captured = capturedDate.toISOString();
const capturedEpoch = capturedDate.getTime() / 1000;
assert.equal(frameStatus(200, captured), '찍은 시각 09:08:07');
assert.equal(frameStatus(200, String(capturedEpoch)), '찍은 시각 09:08:07');
assert.equal(frameStatus(200, `${capturedEpoch}.410025`), '찍은 시각 09:08:07');
assert.equal(frameStatus(200, captured, 'map-overlay'), '실제 환경 + 좌표 · 찍은 시각 09:08:07');
assert.equal(frameStatus(200, null, 'map-overlay'), '실제 환경 + 좌표');
assert.equal(frameStatus(200, captured, 'map'), '버드뷰 평면 · 찍은 시각 09:08:07');
assert.equal(frameStatus(502, captured), 'ROSY 연결 안 됨');
console.log('Topview: normal polling, retry, visibility pause and status text passed');

// 브라우저가 없는 Node에서도 실제 비동기 갱신과 취소·오래된 응답 무시를 확인한다.
const saved = Object.fromEntries(['document', 'window', 'fetch', 'setTimeout', 'clearTimeout', 'Image']
  .map(key => [key, globalThis[key]]));
const revoke = URL.revokeObjectURL;
const revoked = [];
const timers = new Map();
const calls = [];
let nextTimer = 0;
let respond = () => Promise.resolve(new Response('jpeg', {
  headers: {'Content-Type': 'image/jpeg', 'X-Frame-Captured-At': captured},
}));
const image = {hidden: true, style: {}, removeAttribute(name) { delete this[name]; }};
const statusText = {textContent: ''};
const select = new EventTarget();
select.value = '/topview/cam-one/frame/';
const nodes = {'[data-topview-image]': image, '[data-topview-status]': statusText,
  '[data-topview-source]': select};
const settle = () => new Promise(resolve => setImmediate(resolve));
function tick() {
  assert.equal(timers.size, 1);
  const [key, timer] = timers.entries().next().value;
  timers.delete(key);
  timer.callback();
}
try {
  globalThis.document = new EventTarget();
  document.hidden = false;
  globalThis.window = new EventTarget();
  globalThis.fetch = (url, options) => { calls.push({url, options}); return respond(); };
  globalThis.setTimeout = (callback, delay) => {
    timers.set(++nextTimer, {callback, delay}); return nextTimer;
  };
  globalThis.clearTimeout = key => timers.delete(key);
  URL.revokeObjectURL = url => { revoked.push(url); revoke(url); };
  mountTopview({querySelector: selector => nodes[selector], dataset: {}});
  window.dispatchEvent(new Event('pageshow'));
  assert.equal(calls.length, 1, '첫 pageshow는 진행 중인 프레임 요청을 복제하지 않는다');
  await settle();
  assert.equal(image.hidden, false);
  assert.equal(statusText.textContent, '찍은 시각 09:08:07');
  const firstImage = image.src;
  assert.equal(timers.values().next().value.delay, 1000);

  respond = () => Promise.resolve(new Response('frame stale', {status: 404}));
  tick(); await settle();
  assert.equal(image.hidden, true);
  assert.equal(statusText.textContent, '그림이 오래됨');
  assert.deepEqual(revoked, [firstImage]);
  assert.equal(timers.values().next().value.delay, 3000);

  respond = () => Promise.reject(new TypeError('network unavailable'));
  tick(); await settle();
  assert.equal(statusText.textContent, 'ROSY 연결 안 됨');
  assert.equal(timers.values().next().value.delay, 3000);

  // X-Frame-Rotation-Deg: a valid degree rotates the image inline; an invalid one resets to the CSS default.
  respond = () => Promise.resolve(new Response('rotated jpeg', {
    headers: {'Content-Type': 'image/jpeg', 'X-Frame-Rotation-Deg': '90'},
  }));
  tick(); await settle();
  assert.equal(image.hidden, false);
  assert.equal(image.style.transform, 'rotate(90deg)');
  respond = () => Promise.resolve(new Response('tilted jpeg', {
    headers: {'Content-Type': 'image/jpeg', 'X-Frame-Rotation-Deg': '45'},
  }));
  tick(); await settle();
  assert.equal(image.style.transform, '');
  respond = () => Promise.resolve(new Response('plain jpeg', {headers: {'Content-Type': 'image/jpeg'}}));
  tick(); await settle();
  assert.equal(image.style.transform, '');
  respond = () => Promise.reject(new TypeError('network unavailable'));
  tick(); await settle();
  assert.equal(image.hidden, true);
  assert.equal(timers.values().next().value.delay, 3000);

  let finish;
  respond = () => new Promise(resolve => { finish = resolve; });
  tick();
  document.hidden = true;
  document.dispatchEvent(new Event('visibilitychange'));
  assert.equal(calls.at(-1).options.signal.aborted, true);
  finish(new Response('late jpeg', {headers: {'Content-Type': 'image/jpeg'}}));
  await settle();
  assert.equal(timers.size, 0);
  assert.equal(image.hidden, true);

  respond = () => Promise.resolve(new Response('jpeg', {headers: {'Content-Type': 'image/jpeg'}}));
  document.hidden = false;
  document.dispatchEvent(new Event('visibilitychange'));
  await settle();
  assert.equal(image.hidden, false);
  const resumedImage = image.src;
  select.value = '/topview/cam-two/frame/';
  select.dispatchEvent(new Event('change'));
  await settle();
  assert.equal(calls.at(-1).url, '/topview/cam-two/frame/');
  assert.ok(revoked.includes(resumedImage));
  assert.equal(timers.size, 1);
  window.dispatchEvent(new Event('pagehide'));
  assert.equal(timers.size, 0);
  assert.equal(image.hidden, true);

  // Offscreen Image.decode() atomic frame swap, mid-decode abort, corrupt JPEG rejection, and full cleanup.
  let hiddenState = true;
  const hiddenTransitions = [];
  const removedAttrs = [];
  const swapImage = {
    get hidden() { return hiddenState; },
    set hidden(value) { hiddenState = value; hiddenTransitions.push(value); },
    style: {},
    removeAttribute(name) { removedAttrs.push(name); delete this[name]; },
  };
  const swapStatus = {textContent: ''};
  const swapSelect = new EventTarget();
  swapSelect.value = '/topview/cam-one/frame/';
  const swapNodes = {
    '[data-topview-image]': swapImage,
    '[data-topview-status]': swapStatus,
    '[data-topview-source]': swapSelect,
  };
  const doc2 = new EventTarget();
  doc2.hidden = false;
  const win2 = new EventTarget();
  globalThis.document = doc2;
  globalThis.window = win2;
  timers.clear();
  calls.length = 0;
  revoked.length = 0;

  let decodeBehavior = () => Promise.resolve();
  let finishDecode;
  globalThis.Image = class FakeImage {
    decode() { return decodeBehavior(); }
  };
  respond = () => Promise.resolve(new Response('jpeg-1', {
    headers: {'Content-Type': 'image/jpeg', 'X-Frame-Captured-At': `${capturedEpoch}.410025`},
  }));
  const cleanup = mountTopview({querySelector: selector => swapNodes[selector], dataset: {}});
  await settle();
  const frame1 = swapImage.src;
  assert.ok(frame1);
  assert.equal(swapImage.hidden, false);
  assert.equal(swapStatus.textContent, '찍은 시각 09:08:07');
  assert.deepEqual(hiddenTransitions, [false]);
  assert.deepEqual(removedAttrs, []);

  // Frame 2 while decode() is pending: frame 1 remains visible & unrevoked.
  decodeBehavior = () => new Promise(resolve => { finishDecode = resolve; });
  respond = () => Promise.resolve(new Response('jpeg-2', {
    headers: {'Content-Type': 'image/jpeg', 'X-Frame-Captured-At': `${capturedEpoch}.900000`},
  }));
  tick();
  await settle();
  assert.equal(swapImage.src, frame1);
  assert.equal(swapImage.hidden, false);
  assert.equal(revoked.length, 0);

  // Frame 2 decode resolves -> atomic swap without hidden=true or removeAttribute('src').
  finishDecode();
  await settle();
  const frame2 = swapImage.src;
  assert.notEqual(frame2, frame1);
  assert.deepEqual(hiddenTransitions, [false]);
  assert.deepEqual(removedAttrs, []);
  assert.deepEqual(revoked, [frame1]);

  // Corrupt JPEG (decode rejects) -> temporary URL revoked, image hidden, status ROSY 연결 안 됨, 3s retry.
  decodeBehavior = () => Promise.reject(new Error('corrupt jpeg'));
  respond = () => Promise.resolve(new Response('bad-jpeg', {
    headers: {'Content-Type': 'image/jpeg', 'X-Frame-Captured-At': `${capturedEpoch}.950000`},
  }));
  tick();
  await settle();
  assert.equal(swapImage.hidden, true);
  assert.equal(swapStatus.textContent, 'ROSY 연결 안 됨');
  assert.equal(revoked.length, 3);
  assert.equal(timers.values().next().value.delay, 3000);

  // Mid-decode abort: tab hidden while decode() pending -> pending URL revoked on settle, active frame preserved.
  decodeBehavior = () => Promise.resolve();
  respond = () => Promise.resolve(new Response('jpeg-3', {headers: {'Content-Type': 'image/jpeg'}}));
  tick();
  await settle();
  assert.equal(swapImage.hidden, false);
  const frame3 = swapImage.src;

  let finishLateDecode;
  decodeBehavior = () => new Promise(resolve => { finishLateDecode = resolve; });
  respond = () => Promise.resolve(new Response('jpeg-4', {headers: {'Content-Type': 'image/jpeg'}}));
  tick();
  await settle();
  const revokedBeforeAbort = revoked.length;
  doc2.hidden = true;
  doc2.dispatchEvent(new Event('visibilitychange'));
  finishLateDecode();
  await settle();
  assert.equal(revoked.length, revokedBeforeAbort + 1);
  assert.equal(swapImage.src, frame3);

  // Returned cleanup removes change, visibilitychange, pagehide, and pageshow listeners.
  const callsBeforeCleanup = calls.length;
  const revokedBeforeCleanup = revoked.length;
  cleanup();
  assert.equal(revoked.length, revokedBeforeCleanup + 1);
  const removedAfterCleanup = removedAttrs.length;
  doc2.hidden = false;
  swapSelect.dispatchEvent(new Event('change'));
  doc2.dispatchEvent(new Event('visibilitychange'));
  win2.dispatchEvent(Object.assign(new Event('pageshow'), {persisted: true}));
  win2.dispatchEvent(new Event('pagehide'));
  assert.equal(calls.length, callsBeforeCleanup);
  assert.equal(removedAttrs.length, removedAfterCleanup);
  assert.equal(timers.size, 0);

  // Mode selector switches between default map_overlay ('auto') and explicit raw ('?view=raw').
  timers.clear();
  calls.length = 0;
  const modeImage = {hidden: true, style: {}, removeAttribute() {}};
  const modeStatus = {textContent: ''};
  const modeOrientation = {textContent: '기본 방향 안내'};
  const modeSelect = new EventTarget();
  modeSelect.value = 'auto';
  const modeNodes = {
    '[data-topview-image]': modeImage,
    '[data-topview-status]': modeStatus,
    '[data-topview-mode]': modeSelect,
    '[data-topview-orientation]': modeOrientation,
  };
  decodeBehavior = () => Promise.resolve();
  respond = () => Promise.resolve(new Response('overlay-jpeg', {
    headers: {
      'Content-Type': 'image/jpeg',
      'X-Frame-Rectified': 'map-overlay',
      'X-Frame-Captured-At': captured,
    },
  }));
  const modeCleanup = mountTopview({
    querySelector: selector => modeNodes[selector],
    dataset: {topviewUrl: '/topview/cam-one/frame/'},
  });
  await settle();
  assert.equal(calls.at(-1).url, '/topview/cam-one/frame/');
  assert.equal(modeStatus.textContent, '실제 환경 + 좌표 · 찍은 시각 09:08:07');
  assert.equal(modeOrientation.textContent, '버드뷰 실제 환경 + 좌표 (지도 +X 오른쪽 · +Y 위)');

  modeSelect.value = 'raw';
  respond = () => Promise.resolve(new Response('raw-jpeg', {
    headers: {'Content-Type': 'image/jpeg', 'X-Frame-Captured-At': captured},
  }));
  modeSelect.dispatchEvent(new Event('change'));
  await settle();
  assert.equal(calls.at(-1).url, '/topview/cam-one/frame/?view=raw');
  assert.equal(modeStatus.textContent, '찍은 시각 09:08:07');
  assert.equal(modeOrientation.textContent, '기본 방향 안내');
  modeCleanup();

  // Overlay coordinate projection: scaling (640x480 -> 320x240) and 0/180-degree rotation alignment.
  assert.equal(resolveRotationDeg(null), 180);
  assert.equal(resolveRotationDeg('invalid'), 180);
  assert.equal(resolveRotationDeg('0'), 0);
  assert.equal(resolveRotationDeg('180'), 180);

  // Belt point [341, 112] on 640x480 rotated 180° -> (299, 368); scaled to 320x240 -> (149.5, 184).
  const belt180 = projectPoint([341, 112], [640, 480], [320, 240], 180);
  assert.equal(belt180.x, 149.5);
  assert.equal(belt180.y, 184);
  const belt0 = projectPoint([341, 112], [640, 480], [320, 240], 0);
  assert.equal(belt0.x, 170.5);
  assert.equal(belt0.y, 56);

  // Stand ROI [410, 30, 570, 125] on 640x480 rotated 180° -> [70, 355, 230, 450]; scaled to 320x240 -> x=35, y=177.5, w=80, h=47.5.
  const stand180 = projectRoi([410, 30, 570, 125], [640, 480], [320, 240], 180);
  assert.deepEqual(stand180, {x: 35, y: 177.5, width: 80, height: 47.5, cx: 75, cy: 201.25});

  const sampleSitePixels = {
    source_id: 'omx_overhead',
    image: [640, 480],
    status: 'planned_unverified',
    points: [
      {site: 'yard:r1-s1', label: '야드 1행 1', short_label: 'Y r1·s1', zone: 'yard', kind: 'slot', roi: [425, 271, 480, 380]},
      {site: 'temp_a:r1-s1', label: '임시 A 1레인', short_label: 'A1', zone: 'temp_a', kind: 'slot', roi: null},
      {site: 'belt', label: '컨베이어(집기)', short_label: '컨베이어', zone: 'belt', kind: 'pick', point: [341, 112]},
    ],
  };
  const items = buildOverlayItems(sampleSitePixels, [320, 240], 180);
  assert.equal(items.slots.length, 1);
  assert.equal(items.slots[0].rect.x, 80);
  assert.equal(items.slots[0].rect.y, 50);
  assert.equal(items.picks.length, 1);
  assert.equal(items.picks[0].point.x, 149.5);
  assert.equal(items.pending.length, 1);
  assert.equal(items.pending[0].shortLabel, 'A1');

  // Visibility predicate: only omx_overhead + raw + unrectified + toggle enabled + image visible.
  assert.equal(shouldShowOverlay({
    sitePixels: sampleSitePixels, cameraId: 'omx_overhead', viewMode: 'raw', rectified: null, enabled: true, imageVisible: true,
  }), true);
  assert.equal(shouldShowOverlay({
    sitePixels: sampleSitePixels, cameraId: 'omx_overhead', viewMode: 'auto', rectified: null, enabled: true, imageVisible: true,
  }), false);
  assert.equal(shouldShowOverlay({
    sitePixels: sampleSitePixels, cameraId: 'omx_overhead', viewMode: 'raw', rectified: 'map-overlay', enabled: true, imageVisible: true,
  }), false);
  assert.equal(shouldShowOverlay({
    sitePixels: sampleSitePixels, cameraId: 'ceiling_north', viewMode: 'raw', rectified: null, enabled: true, imageVisible: true,
  }), false);
  assert.equal(shouldShowOverlay({
    sitePixels: sampleSitePixels, cameraId: 'omx_overhead', viewMode: 'raw', rectified: null, enabled: false, imageVisible: true,
  }), false);

  // SVG overlay element uses toggleAttribute('hidden') because SVGElement has no HTMLElement.hidden reflection.
  timers.clear();
  calls.length = 0;
  const ovImage = {hidden: true, style: {}, clientWidth: 640, clientHeight: 480, removeAttribute() {}};
  const ovStatus = {textContent: ''};
  const ovMode = new EventTarget();
  ovMode.value = 'raw';
  const ovToggle = new EventTarget();
  ovToggle.checked = true;
  const ovControl = {hidden: false};
  const ovMeta = {hidden: false};
  const svgAttrs = new Map([['hidden', '']]);
  const ovSvg = {
    toggleAttribute(name, force) {
      if (force) svgAttrs.set(name, '');
      else svgAttrs.delete(name);
    },
    replaceChildren() {},
  };
  const ovNodes = {
    '[data-topview-image]': ovImage,
    '[data-topview-status]': ovStatus,
    '[data-topview-mode]': ovMode,
    '[data-topview-overlay]': ovSvg,
    '[data-topview-points-control]': ovControl,
    '[data-topview-points-toggle]': ovToggle,
    '[data-topview-points-meta]': ovMeta,
  };
  decodeBehavior = () => Promise.resolve();
  respond = () => Promise.resolve(new Response('raw-jpeg', {
    headers: {'Content-Type': 'image/jpeg', 'X-Frame-Captured-At': captured},
  }));
  const ovCleanup = mountTopview({
    querySelector: selector => ovNodes[selector],
    dataset: {
      topviewUrl: '/topview/omx_overhead/frame/',
      topviewCamera: 'omx_overhead',
      topviewPoints: JSON.stringify(sampleSitePixels),
    },
  });
  await settle();
  assert.equal(calls.at(-1).url, '/topview/omx_overhead/frame/?view=raw');
  assert.equal(svgAttrs.has('hidden'), false, 'SVG overlay hidden attribute must be removed when visible');
  assert.equal(ovMeta.hidden, false);
  ovToggle.checked = false;
  ovToggle.dispatchEvent(new Event('change'));
  assert.equal(svgAttrs.has('hidden'), true, 'SVG overlay hidden attribute must be set when toggled off');
  assert.equal(ovMeta.hidden, true);
  ovCleanup();
} finally {
  Object.assign(globalThis, saved);
  URL.revokeObjectURL = revoke;
}
console.log('Topview lifecycle and overlay projection tests passed');

// Deployed omx_overhead sends X-Frame-Rotation-Deg: 0; Harbor still shows the yard turned 180° (conveyor at the bottom).
assert.equal(displayRotationDeg('0', 'omx_overhead'), 180);
assert.equal(displayRotationDeg('0', 'top'), 180);
assert.equal(displayRotationDeg('90', 'top'), 270);
assert.equal(displayRotationDeg('0', 'ceiling_north'), 0);
assert.equal(displayRotationDeg(null, 'omx_overhead'), 180);
