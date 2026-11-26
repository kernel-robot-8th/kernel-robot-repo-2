import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import {playbackDefaults, playButtonState, stepButtonState, stepTarget} from '../harbor_web/static/harbor_web/yard-playback.mjs';

test('reduced motion turns animation and camera follow off; camera never tweens', () => {
  assert.deepEqual(playbackDefaults(false), {animate: true, follow: true, cameraTween: false});
  assert.deepEqual(playbackDefaults(true), {animate: false, follow: false, cameraTween: false});
});

test('step target clamps to the frame range and reports no-op steps as null', () => {
  assert.equal(stepTarget(0, 1, 7), 1);
  assert.equal(stepTarget(3, -1, 7), 2);
  assert.equal(stepTarget(0, -1, 7), null);
  assert.equal(stepTarget(6, 1, 7), null);
  assert.equal(stepTarget(0, 1, 1), null);
  assert.equal(stepTarget(2, 0, 7), null);
  assert.equal(stepTarget(9, -1, 7), 5);
});

test('previous step is disabled at frame 0 and next step at the last frame', () => {
  assert.deepEqual(stepButtonState({replayable: true, index: 0, total: 7}), {prevDisabled: true, nextDisabled: false});
  assert.deepEqual(stepButtonState({replayable: true, index: 3, total: 7}), {prevDisabled: false, nextDisabled: false});
  assert.deepEqual(stepButtonState({replayable: true, index: 6, total: 7}), {prevDisabled: false, nextDisabled: true});
  assert.deepEqual(stepButtonState({replayable: false, index: 3, total: 7}), {prevDisabled: true, nextDisabled: true});
  assert.deepEqual(stepButtonState({replayable: true, index: 0, total: 1}), {prevDisabled: true, nextDisabled: true});
});

test('play button toggles between play, pause, and resume labels', () => {
  assert.deepEqual(playButtonState({planReplay: true}), {label: '계획 재생', pressed: false});
  assert.deepEqual(playButtonState({draft: true, planReplay: true}), {label: '가상 변경 재생', pressed: false});
  assert.deepEqual(playButtonState({active: true, planReplay: true}), {label: '일시정지', pressed: true});
  assert.deepEqual(playButtonState({active: true, paused: true, planReplay: true}), {label: '계속 재생', pressed: false});
});

test('viewer template has one help panel, a progress block, step buttons, and no duplicate disclaimer', () => {
  const html = fs.readFileSync(new URL('../harbor_web/templates/yard_3d.html', import.meta.url), 'utf8');
  assert.equal(html.match(/조작 방법/g).length, 1);
  assert.match(html, /<progress max="100"/);
  assert.match(html, /data-scene-route/);
  assert.match(html, /data-scene-phase/);
  assert.match(html, /data-scene-step="-1"/);
  assert.match(html, /data-scene-step="1"/);
  assert.match(html, /data-scene-stop/);
  assert.match(html, /data-scene-follow/);
  assert.doesNotMatch(html, /실측 모델 아님/);
  assert.doesNotMatch(html, /컨테이너 모델 뷰어/);
  assert.doesNotMatch(html, /data-scene-action="orbit"/);
  assert.doesNotMatch(html, /style="/);
  assert.doesNotMatch(html, /예상/);
});

test('viewer wires reduced motion, pause, and step keys through the playback rules', () => {
  const source = fs.readFileSync(new URL('../harbor_web/static/harbor_web/yard-3d.js', import.meta.url), 'utf8');
  assert.match(source, /playbackDefaults\(reduced\.matches\)/);
  assert.match(source, /stepTarget\(/);
  assert.match(source, /function pausePlayback/);
  assert.match(source, /key === '\['/);
  assert.match(source, /key === '\]'/);
  assert.match(source, /renderer\.setMotion\(null\)/);
  const renderer = fs.readFileSync(new URL('../harbor_web/static/harbor_web/yard-renderer.js', import.meta.url), 'utf8');
  assert.match(renderer, /onPlaybackKey\?\.\(event\.key\)/);
  assert.match(renderer, /canvas\.dataset\.motionId/);
  assert.doesNotMatch(renderer, /cdn|unpkg|jsdelivr/i);
});
