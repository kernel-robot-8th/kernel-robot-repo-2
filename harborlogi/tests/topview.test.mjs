import assert from 'node:assert/strict';
import {pollDelay, frameStatus, mountTopview} from '../harbor_web/static/harbor_web/topview.mjs';

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
const captured = new Date(2026, 10, 21, 9, 8, 7).toISOString();
assert.equal(frameStatus(200, captured), '찍은 시각 09:08:07');
assert.equal(frameStatus(502, captured), 'ROSY 연결 안 됨');
console.log('Topview: normal polling, retry, visibility pause and status text passed');

// 브라우저가 없는 Node에서도 실제 비동기 갱신과 취소·오래된 응답 무시를 확인한다.
const saved = Object.fromEntries(['document', 'window', 'fetch', 'setTimeout', 'clearTimeout']
  .map(key => [key, globalThis[key]]));
const revoke = URL.revokeObjectURL;
const revoked = [];
const timers = new Map();
const calls = [];
let nextTimer = 0;
let respond = () => Promise.resolve(new Response('jpeg', {
  headers: {'Content-Type': 'image/jpeg', 'X-Frame-Captured-At': captured},
}));
const image = {hidden: true, removeAttribute(name) { delete this[name]; }};
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
} finally {
  Object.assign(globalThis, saved);
  URL.revokeObjectURL = revoke;
}
console.log('Topview lifecycle: image release, retries, in-flight abort, resume and camera change passed');
