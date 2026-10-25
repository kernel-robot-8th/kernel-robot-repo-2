'use strict';
(() => {
  const ease = t => t * t * (3 - 2 * t);
  function project(point, camera) {
    const x = point.x - camera.x, y = point.y - camera.y;
    const across = x * Math.cos(camera.yaw) - y * Math.sin(camera.yaw);
    const away = x * Math.sin(camera.yaw) + y * Math.cos(camera.yaw);
    return {x: across, y: away * Math.sin(camera.pitch) - point.z * Math.cos(camera.pitch),
      depth: away * Math.cos(camera.pitch) + point.z * Math.sin(camera.pitch)};
  }
  function transit(from, to, progress, ceiling) {
    const p = Math.max(0, Math.min(1, progress));
    if (from.x === to.x && from.y === to.y && from.z === to.z) return {...to};
    const horizontal = ease(Math.max(0, Math.min(1, (p - .25) / .5)));
    const z = p < .25 ? from.z + (ceiling - from.z) * ease(p / .25)
      : p > .75 ? ceiling + (to.z - ceiling) * ease((p - .75) / .25) : ceiling;
    return {...to, x: from.x + (to.x - from.x) * horizontal,
      y: from.y + (to.y - from.y) * horizontal, z};
  }
  function inside(point, polygon) {
    let hit = false;
    for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i++) {
      const a = polygon[i], b = polygon[j];
      if ((a.y > point.y) !== (b.y > point.y) &&
          point.x < (b.x - a.x) * (point.y - a.y) / (b.y - a.y) + a.x) hit = !hit;
    }
    return hit;
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = {project, transit, inside};
  if (typeof document === 'undefined') return;

  document.querySelectorAll('[data-yard-3d]').forEach(figure => {
    const preview = figure.closest('[data-yard-preview]');
    const flat = preview.querySelector('[data-yard-flat]');
    const views = preview.querySelector('[data-yard-views]');
    const fallback = preview.querySelector('[data-yard-3d-fallback]');
    try {
      const scene = JSON.parse(document.getElementById(figure.dataset.scene).textContent);
      const canvas = figure.querySelector('canvas');
      const ctx = canvas.getContext('2d');
      if (!ctx || !scene.frames.length || !scene.areas.length) throw new Error('No scene');
      const cargo = figure.querySelector('[data-scene-cargo]');
      const info = figure.querySelector('[data-scene-info]');
      const playButton = figure.querySelector('[data-scene-play]');
      const motion = figure.querySelector('[data-scene-motion]');
      const stepButtons = [...preview.querySelectorAll('[data-yard-stage]')];
      const reduced = window.matchMedia('(prefers-reduced-motion: reduce)');
      const palette = getComputedStyle(document.documentElement);
      const color = name => palette.getPropertyValue(name).trim();
      const colors = {target: color('--primitive-color-navy'), blocker: color('--primitive-color-orange'),
        stored: color('--primitive-color-teal'), line: color('--semantic-border-divider'),
        text: color('--semantic-text-primary'), muted: color('--semantic-text-secondary'),
        floor: color('--semantic-surface-panel'), white: color('--primitive-color-white')};
      const maxX = Math.max(...scene.areas.map(a => a.x + a.columns));
      const maxY = Math.max(...scene.areas.map(a => a.y + a.rows));
      const ceiling = Math.max(...scene.areas.map(a => a.levels)) + .6;
      const camera = {x: maxX / 2, y: maxY / 2, yaw: -.6, pitch: .65};
      let width = 0, height = 0, zoom = 1, index = 0, selected = '';
      let animation = null, raf = 0, timer = 0, playing = false, internalStep = false;
      let current = scene.frames[0].boxes.map(b => ({...b}));
      let facesOnScreen = [], drag = null, isFlat = false;
      const fitPoints = [];
      scene.areas.forEach(area => {
        for (const x of [area.x - .3, area.x + area.columns + .3])
          for (const y of [area.y - .3, area.y + area.rows + .8]) fitPoints.push({x, y, z: 0});
      });
      scene.frames.forEach(frame => frame.boxes.forEach(box => {
        for (const x of [box.x, box.x + box.width]) for (const y of [box.y, box.y + box.depth]) {
          fitPoints.push({x, y, z: box.z + .85});
        }
      }));
      const shade = (hex, factor) => {
        const h = hex.replace('#', '');
        if (!/^[0-9a-f]{6}$/i.test(h)) return hex;
        return '#' + [0, 2, 4].map(i => Math.round(parseInt(h.slice(i, i + 2), 16) * factor)
          .toString(16).padStart(2, '0')).join('');
      };
      const setMotion = text => { motion.textContent = text; };
      function stop() {
        playing = false;
        clearTimeout(timer);
        playButton.textContent = '계획 재생';
        playButton.setAttribute('aria-pressed', 'false');
      }
      function details() {
        const box = scene.frames[index].boxes.find(b => b.id === selected);
        info.textContent = box ? `${box.id} · ${box.label} · ${box.size} · ${box.orientation}도 · ${box.positions.join(' / ')}`
          : '박스나 화물 목록을 선택하면 점유 크기와 위치를 확인할 수 있습니다.';
        cargo.value = box ? box.id : '';
      }
      function fillCargo() {
        cargo.replaceChildren(new Option('화물 선택', ''));
        scene.frames[index].boxes.forEach(box => cargo.add(new Option(`${box.id} · ${box.size} · ${box.label}`, box.id)));
        details();
      }
      function draw() {
        if (!width || !height || isFlat) return;
        const bounds = fitPoints.map(p => project(p, camera));
        if (animation) current.forEach(box => {
          for (const x of [box.x, box.x + box.width]) for (const y of [box.y, box.y + box.depth])
            bounds.push(project({x, y, z: box.z + .85}, camera));
        });
        const left = Math.min(...bounds.map(p => p.x)), right = Math.max(...bounds.map(p => p.x));
        const top = Math.min(...bounds.map(p => p.y)), bottom = Math.max(...bounds.map(p => p.y));
        const scale = Math.min((width - 30) / (right - left), (height - 40) / (bottom - top)) * zoom;
        const point = p => {
          const q = project(p, camera);
          return {x: width / 2 + (q.x - (left + right) / 2) * scale,
            y: height / 2 + (q.y - (top + bottom) / 2) * scale, depth: q.depth};
        };
        const polygon = (points, fill, stroke, line = 1) => {
          ctx.beginPath(); points.forEach((p, i) => i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y));
          ctx.closePath(); ctx.fillStyle = fill; ctx.fill(); ctx.strokeStyle = stroke; ctx.lineWidth = line; ctx.stroke();
        };
        ctx.clearRect(0, 0, width, height);
        ctx.font = '600 12px "Segoe UI", "Malgun Gothic", sans-serif';
        ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
        scene.areas.forEach(area => {
          for (let y = 0; y < area.rows; y++) for (let x = 0; x < area.columns; x++) {
            const corners = [[x, y], [x + 1, y], [x + 1, y + 1], [x, y + 1]];
            polygon(corners.map(([a, b]) => point({x: area.x + a, y: area.y + b, z: -.05})), colors.floor, colors.line);
          }
          const p = point({x: area.x + area.columns / 2, y: area.y + area.rows + .45, z: 0});
          ctx.fillStyle = colors.text; ctx.fillText(area.label, p.x, p.y);
        });
        const faces = [];
        current.forEach(box => {
          const x = box.x + .06, y = box.y + .06, z = box.z + .03;
          const w = box.width - .12, d = box.depth - .12, h = .8;
          const vertices = [[0, 0, 0], [w, 0, 0], [w, d, 0], [0, d, 0],
            [0, 0, h], [w, 0, h], [w, d, h], [0, d, h]].map(([a, b, c]) => point({x: x + a, y: y + b, z: z + c}));
          [[0, 1, 5, 4], [1, 2, 6, 5], [2, 3, 7, 6], [3, 0, 4, 7], [4, 5, 6, 7]].forEach((v, side) => {
            const points = v.map(i => vertices[i]);
            faces.push({box, side, points, depth: points.reduce((n, p) => n + p.depth, 0) / 4});
          });
        });
        faces.sort((a, b) => a.depth - b.depth);
        facesOnScreen = faces;
        faces.forEach(face => {
          const base = colors[face.box.role] || colors.stored;
          polygon(face.points, shade(base, face.side === 4 ? 1 : face.side % 2 ? .78 : .9),
            face.box.id === selected ? colors.text : shade(base, .65), face.box.id === selected ? 2 : .8);
          if (face.side < 4) {
            const [a, b, c, d] = face.points;
            ctx.strokeStyle = colors.white; ctx.globalAlpha = .22; ctx.lineWidth = .7;
            for (let i = 1; i < 8; i++) {
              const t = i / 8;
              ctx.beginPath(); ctx.moveTo(a.x + (b.x - a.x) * t, a.y + (b.y - a.y) * t);
              ctx.lineTo(d.x + (c.x - d.x) * t, d.y + (c.y - d.y) * t); ctx.stroke();
            }
            ctx.globalAlpha = 1;
          }
          {
            const x = face.points.reduce((n, p) => n + p.x, 0) / 4;
            const y = face.points.reduce((n, p) => n + p.y, 0) / 4;
            ctx.fillStyle = face.box.role === 'target' ? colors.white : colors.text;
            ctx.save(); ctx.beginPath(); face.points.forEach((p, i) => i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y)); ctx.closePath(); ctx.clip();
            ctx.fillText(face.box.id, x, y); ctx.restore();
          }
        });
        canvas.dataset.frame = String(index);
        canvas.dataset.boxCount = String(current.length);
      }
      function advance() {
        if (!playing) return;
        if (index === scene.frames.length - 1) { stop(); setMotion('계획 재생 완료 · 실제 실행 상태는 바뀌지 않았습니다.'); return; }
        timer = window.setTimeout(() => {
          if (!playing || isFlat || document.hidden) return;
          internalStep = true; stepButtons[index + 1].click(); internalStep = false;
        }, reduced.matches ? 1000 : 550);
      }
      function tick(now) {
        raf = 0;
        if (!animation) return;
        const t = Math.min(1, (now - animation.started) / 1100);
        current = animation.to.map(to => transit(animation.from.get(to.id) || to, to, t, ceiling));
        draw();
        if (t < 1) raf = requestAnimationFrame(tick);
        else { animation = null; canvas.dataset.moving = 'false'; setMotion(scene.frames[index].label + ' · 예상 배치'); advance(); }
      }
      function selectFrame(next, animate = true) {
        if (!scene.frames[next]) return;
        if (!internalStep) stop();
        cancelAnimationFrame(raf); raf = 0;
        const from = new Map(current.map(box => [box.id, {...box}]));
        index = next; fillCargo();
        figure.querySelector('[data-scene-step]').textContent = scene.frames[index].label;
        if (animate && next && !reduced.matches && !isFlat && !document.hidden) {
          animation = {from, to: scene.frames[index].boxes, started: performance.now()};
          canvas.dataset.moving = 'true'; setMotion('박스 이동 설명 중 · 실제 로봇 경로 아님'); raf = requestAnimationFrame(tick);
        } else {
          animation = null; current = scene.frames[index].boxes.map(box => ({...box}));
          canvas.dataset.moving = 'false'; draw();
          setMotion(next ? scene.frames[index].label + ' · 예상 배치' : '원본 관측 배치'); advance();
        }
      }
      function resize() {
        const rect = canvas.getBoundingClientRect();
        width = rect.width; height = rect.height;
        const ratio = Math.min(window.devicePixelRatio || 1, 2);
        canvas.width = Math.round(width * ratio); canvas.height = Math.round(height * ratio);
        ctx.setTransform(ratio, 0, 0, ratio, 0, 0); draw();
      }
      function showFlat(value) {
        isFlat = value; stop();
        cancelAnimationFrame(raf); animation = null; canvas.dataset.moving = 'false';
        current = scene.frames[index].boxes.map(box => ({...box}));
        figure.hidden = value; flat.hidden = !value;
        views.querySelectorAll('button').forEach(button => button.setAttribute('aria-pressed', String((button.dataset.yardView === 'flat') === value)));
        if (!value) resize();
      }
      views.querySelectorAll('button').forEach(button => button.addEventListener('click', () => showFlat(button.dataset.yardView === 'flat')));
      figure.querySelectorAll('[data-scene-camera]').forEach(button => button.addEventListener('click', () => {
        const view = button.dataset.sceneCamera;
        camera.yaw = view === 'front' ? 0 : -.6; camera.pitch = view === 'top' ? 1.35 : .65;
        zoom = 1; draw();
      }));
      figure.querySelectorAll('[data-scene-zoom]').forEach(button => button.addEventListener('click', () => {
        zoom = Math.max(.7, Math.min(1.8, zoom + Number(button.dataset.sceneZoom))); draw();
      }));
      cargo.addEventListener('change', () => { selected = cargo.value; details(); draw(); });
      canvas.addEventListener('pointerdown', event => {
        if (event.button !== 0) return;
        drag = {x: event.clientX, y: event.clientY, yaw: camera.yaw, pitch: camera.pitch, moved: false};
        canvas.setPointerCapture(event.pointerId);
      });
      canvas.addEventListener('pointermove', event => {
        if (!drag) return;
        const dx = event.clientX - drag.x, dy = event.clientY - drag.y;
        if (Math.abs(dx) + Math.abs(dy) > 4) drag.moved = true;
        if (drag.moved) { camera.yaw = drag.yaw + dx * .009; camera.pitch = Math.max(.25, Math.min(1.4, drag.pitch + dy * .006)); draw(); }
      });
      canvas.addEventListener('pointerup', event => {
        if (drag && !drag.moved) {
          const rect = canvas.getBoundingClientRect();
          const hit = [...facesOnScreen].reverse().find(face => inside({x: event.clientX - rect.left, y: event.clientY - rect.top}, face.points));
          if (hit) { selected = hit.box.id; details(); draw(); }
        }
        drag = null;
      });
      canvas.addEventListener('pointercancel', () => { drag = null; });
      canvas.addEventListener('keydown', event => {
        if (['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', '+', '-'].includes(event.key)) event.preventDefault();
        else return;
        if (event.key === 'ArrowLeft') camera.yaw -= .12;
        if (event.key === 'ArrowRight') camera.yaw += .12;
        if (event.key === 'ArrowUp') camera.pitch = Math.min(1.4, camera.pitch + .1);
        if (event.key === 'ArrowDown') camera.pitch = Math.max(.25, camera.pitch - .1);
        if (event.key === '+') zoom = Math.min(1.8, zoom + .1);
        if (event.key === '-') zoom = Math.max(.7, zoom - .1);
        if (event.key === 'Home') { camera.yaw = -.6; camera.pitch = .65; zoom = 1; }
        draw();
      });
      playButton.hidden = scene.frames.length < 2 || stepButtons.length !== scene.frames.length;
      playButton.addEventListener('click', () => {
        if (playing) { stop(); selectFrame(index, false); return; }
        playing = true; playButton.textContent = '재생 중지'; playButton.setAttribute('aria-pressed', 'true');
        if (index >= scene.frames.length - 1) { internalStep = true; stepButtons[0].click(); internalStep = false; }
        else if (animation) { /* The current explicit transition finishes before the next step. */ }
        else advance();
      });
      preview.addEventListener('yardstagechange', event => selectFrame(Number(event.detail.index)));
      document.addEventListener('visibilitychange', () => { if (document.hidden) { stop(); selectFrame(index, false); } });
      reduced.addEventListener('change', () => { stop(); selectFrame(index, false); });
      fillCargo(); views.hidden = false; showFlat(false);
      figure.dataset.ready = 'true';
      new ResizeObserver(resize).observe(canvas);
    } catch (_) {
      figure.hidden = true; if (flat) flat.hidden = false; if (views) views.hidden = true;
      if (fallback) fallback.hidden = false;
    }
  });
})();
