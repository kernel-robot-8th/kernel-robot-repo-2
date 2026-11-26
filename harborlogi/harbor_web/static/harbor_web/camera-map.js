import * as THREE from './vendor/three-0.186.1/three.module.js';
import {OrbitControls} from './vendor/three-0.186.1/OrbitControls.js';
import {
  buildCellsFromLayout,
  cellVisibility,
  containerTopNameplateLayout,
  isTopContainerInStack,
  normalizePlane,
  parseFramePlane,
  pickCameraForSite,
  planePxToWorld,
  resolveContainerVisual,
  resolvePriorityBadgeColors,
  rosyMetersToPlanePx,
  rosyMetersToWorld,
  sideOverlayPrimaryRef,
  transformOverlayPoint,
  transformOverlayPolygon,
  transformOverlayRoi,
  worldToPlanePx,
} from './camera-map-model.mjs';

const SVG_NS = 'http://www.w3.org/2000/svg';
const VIS_TXT = {yes: '보임', hidden: '가려짐', no: '범위 밖'};
const FRAME_POLL_MS = 3000;

function readTokenPalette() {
  const style = getComputedStyle(document.documentElement);
  const tok = name => style.getPropertyValue(name).trim();
  return {
    navy: tok('--primitive-color-navy'),
    ink: tok('--primitive-color-ink'),
    muted: tok('--primitive-color-muted'),
    teal: tok('--primitive-color-teal-dark'),
    orange: tok('--primitive-color-orange'),
    danger: tok('--primitive-color-danger-ink'),
    tierUrgent: tok('--primitive-color-danger-ink'),
    tierPriority: tok('--primitive-color-orange-dark'),
    tierNormal: tok('--primitive-color-navy'),
    tierNeutral: tok('--primitive-color-muted'),
    cream: tok('--primitive-color-cream'),
    slate: tok('--primitive-color-control-border'),
    slateDark: tok('--primitive-color-muted'),
    line: tok('--primitive-color-line'),
    unknownSoft: tok('--primitive-color-unknown-soft'),
    canvas: tok('--semantic-surface-canvas'),
    white: tok('--primitive-color-white'),
    cargo: {
      orange: tok('--semantic-cargo-orange'),
      pink: tok('--semantic-cargo-pink'),
      yellow: tok('--semantic-cargo-yellow'),
      sky: tok('--semantic-cargo-sky'),
    },
  };
}

function hexToRgba(hex, alpha) {
  const clean = (hex || '').replace(/^#/, '');
  if (clean.length !== 6) return `rgba(0, 0, 0, ${alpha})`;
  const r = parseInt(clean.slice(0, 2), 16);
  const g = parseInt(clean.slice(2, 4), 16);
  const b = parseInt(clean.slice(4, 6), 16);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

function svgEl(tag, attrs, parent) {
  const el = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs || {})) el.setAttribute(k, String(v));
  if (parent) parent.appendChild(el);
  return el;
}

function V3(x, y, z) {
  return new THREE.Vector3(x, y, z);
}

function mountCameraMap(root) {
  const sourceId = root.dataset.cameraMapSource || 'camera-map-data';
  const scriptNode = document.getElementById(sourceId);
  if (!scriptNode) return;
  let data;
  try {
    data = JSON.parse(scriptNode.textContent);
  } catch (_) {
    return;
  }
  const layout = data.layout;
  const cameras = data.cameras || [];
  const camByKey = Object.fromEntries(cameras.map(c => [c.key, c]));
  const containers = data.containers || {};
  const overlays = data.overlays || {};
  const palette = readTokenPalette();

  const viewHost = root.querySelector('[data-cm-view]');
  const fallbackEl = root.querySelector('[data-cm-fallback]');
  const modeLbl = root.querySelector('[data-cm-mode-label]');
  const frameEl = root.querySelector('[data-cm-frame]');
  const frameTitle = root.querySelector('[data-cm-frame-title]');
  const frameSub = root.querySelector('[data-cm-frame-sub]');
  const infoEl = root.querySelector('[data-cm-info]');
  const noteEl = root.querySelector('[data-cm-note]');
  const btnCam = root.querySelector('[data-cm-action="camera"]');
  const btnOrbit = root.querySelector('[data-cm-action="orbit"]');
  const tgNames = root.querySelector('[data-cm-names]');
  const chipButtons = [...root.querySelectorAll('[data-cm-camera]')];

  const basePlane = normalizePlane(layout.plane);
  let activePlane = basePlane;

  // Build canonical cell map from layout config.
  const cells = new Map();
  for (const c of buildCellsFromLayout(layout)) {
    cells.set(c.ref, {
      ...c,
      pos: V3(c.pos.x, c.pos.y, c.pos.z),
      size: V3(c.size.w, c.size.h, c.size.d),
    });
  }

  // Initialize Three.js renderer with clean WebGL fallback.
  let renderer;
  try {
    renderer = new THREE.WebGLRenderer({antialias: true, preserveDrawingBuffer: true});
  } catch (_) {
    if (fallbackEl) fallbackEl.hidden = false;
    root.dataset.cameraMapReady = '0';
    return;
  }
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  const initialW = Math.max(320, viewHost.clientWidth || 640);
  const initialH = Math.round(initialW * 3 / 4);
  renderer.setSize(initialW, initialH, false);
  viewHost.insertBefore(renderer.domElement, viewHost.firstChild);

  const scene = new THREE.Scene();
  scene.background = new THREE.Color(palette.canvas);
  scene.add(new THREE.HemisphereLight(palette.white, palette.slate, 1.6));
  const sun = new THREE.DirectionalLight(palette.white, 1.0);
  sun.position.set(0.5, 2.0, -0.5);
  scene.add(sun);

  const overview = {
    pos: V3(...layout.overview.pos),
    target: V3(...layout.overview.target),
    fov: layout.overview.fov,
  };
  const view = new THREE.PerspectiveCamera(overview.fov, initialW / initialH, 0.02, 30);
  view.position.copy(overview.pos);
  const controls = new OrbitControls(view, renderer.domElement);
  controls.target.copy(overview.target);
  controls.enableDamping = true;
  controls.update();

  const stdMat = (color, opts = {}) => new THREE.MeshStandardMaterial({
    color,
    roughness: 0.7,
    metalness: 0.1,
    ...opts,
  });

  // Floor plane (hidden until birdview map frame loads) + light grid.
  const floorMat = new THREE.MeshBasicMaterial({color: palette.white});
  const floorMesh = new THREE.Mesh(new THREE.PlaneGeometry(basePlane.width_m, basePlane.height_m), floorMat);
  floorMesh.rotation.x = -Math.PI / 2;
  floorMesh.position.set(basePlane.center_x, 0, -basePlane.center_y);
  floorMesh.visible = false;
  scene.add(floorMesh);

  const grid = new THREE.GridHelper(4, 20, palette.line, palette.unknownSoft);
  grid.position.y = -0.002;
  scene.add(grid);

  let floorTex = null;
  function applyFloorTexture(img, plane) {
    const fc = document.createElement('canvas');
    fc.width = img.naturalWidth || img.width || plane.image_w;
    fc.height = img.naturalHeight || img.height || plane.image_h;
    const g = fc.getContext('2d');
    g.filter = 'saturate(0.4) brightness(1.08) contrast(0.8)';
    g.drawImage(img, 0, 0, fc.width, fc.height);
    g.filter = 'none';
    g.fillStyle = hexToRgba(palette.canvas, 0.3);
    g.fillRect(0, 0, fc.width, fc.height);
    const nextTex = new THREE.CanvasTexture(fc);
    nextTex.colorSpace = THREE.SRGBColorSpace;
    nextTex.anisotropy = 4;
    if (floorTex) floorTex.dispose();
    floorTex = nextTex;
    floorMat.map = floorTex;
    floorMat.needsUpdate = true;
    floorMesh.geometry.dispose();
    floorMesh.geometry = new THREE.PlaneGeometry(plane.width_m, plane.height_m);
    floorMesh.position.set(plane.center_x, 0, -plane.center_y);
    floorMesh.visible = true;
  }

  function makeLabel(text, {bg = palette.navy, fg = palette.white, size = 32, scale = 0.0011, weight = 700} = {}) {
    const cv = document.createElement('canvas');
    const g = cv.getContext('2d');
    g.font = `${weight} ${size}px sans-serif`;
    cv.width = Math.ceil(g.measureText(text).width) + 24;
    cv.height = size + 18;
    g.font = `${weight} ${size}px sans-serif`;
    g.fillStyle = bg;
    g.beginPath();
    g.roundRect(0, 0, cv.width, cv.height, 10);
    g.fill();
    g.fillStyle = fg;
    g.textBaseline = 'middle';
    g.fillText(text, 12, cv.height / 2 + 2);
    const tex = new THREE.CanvasTexture(cv);
    tex.colorSpace = THREE.SRGBColorSpace;
    const sprite = new THREE.Sprite(new THREE.SpriteMaterial({map: tex, depthTest: false, transparent: true}));
    sprite.scale.set(cv.width * scale, cv.height * scale, 1);
    sprite.renderOrder = 10;
    return sprite;
  }

  const zoneTags = [];
  const nameTags = [];
  const zoneBg = hexToRgba(palette.white, 0.88);
  const nameBg = hexToRgba(palette.navy, 0.78);
  function addZoneTag(text, x, y, z) {
    const s = makeLabel(text, {bg: zoneBg, fg: palette.muted, size: 24, weight: 600});
    s.position.set(x, y, z);
    scene.add(s);
    zoneTags.push(s);
    return s;
  }
  function addNameTag(text, x, y, z) {
    const s = makeLabel(text, {bg: nameBg, fg: palette.white, size: 22, weight: 600});
    s.position.set(x, y, z);
    scene.add(s);
    nameTags.push(s);
    return s;
  }

  function faceTex(text, fill, fg) {
    const cv = document.createElement('canvas');
    cv.width = 128;
    cv.height = 128;
    const g = cv.getContext('2d');
    g.fillStyle = fill;
    g.fillRect(0, 0, 128, 128);
    g.strokeStyle = hexToRgba(palette.navy, 0.22);
    g.lineWidth = 8;
    g.strokeRect(0, 0, 128, 128);
    g.fillStyle = fg;
    g.font = '700 44px sans-serif';
    g.textAlign = 'center';
    g.textBaseline = 'middle';
    g.fillText(text, 64, 64);
    const t = new THREE.CanvasTexture(cv);
    t.colorSpace = THREE.SRGBColorSpace;
    return t;
  }

  function topNameplateMesh(container, w, h, d) {
    const colors = resolvePriorityBadgeColors(container, palette);
    const cv = document.createElement('canvas');
    cv.width = 256;
    cv.height = 96;
    const g = cv.getContext('2d');
    const pad = 8;
    const radius = 24;
    const rw = cv.width - pad * 2;
    const rh = cv.height - pad * 2;
    g.beginPath();
    g.roundRect(pad, pad, rw, rh, radius);
    g.fillStyle = colors.bg;
    g.fill();
    g.lineWidth = 6;
    g.strokeStyle = colors.border;
    g.stroke();
    g.fillStyle = colors.fg;
    g.font = '700 52px sans-serif';
    g.textAlign = 'center';
    g.textBaseline = 'middle';
    g.fillText(String(container.code), cv.width / 2, cv.height / 2 + 2, rw - 20);
    const tex = new THREE.CanvasTexture(cv);
    tex.colorSpace = THREE.SRGBColorSpace;
    const spec = containerTopNameplateLayout({w, h, d});
    const mesh = new THREE.Mesh(
      new THREE.PlaneGeometry(spec.length, spec.width),
      new THREE.MeshBasicMaterial({
        map: tex,
        transparent: true,
        depthTest: true,
        depthWrite: false,
        polygonOffset: true,
        polygonOffsetFactor: -1,
        polygonOffsetUnits: -1,
      }),
    );
    mesh.position.set(0, spec.y, 0);
    mesh.rotation.set(spec.rotX, 0, spec.rotZ);
    mesh.renderOrder = 6;
    return mesh;
  }

  function codedBox(container, w, h, d, {showTopNameplate = true} = {}) {
    const vis = resolveContainerVisual(container, palette);
    const face = new THREE.MeshStandardMaterial({map: faceTex(container.code, vis.fill, vis.fg), roughness: 0.7});
    const plain = stdMat(vis.fill);
    const group = new THREE.Group();
    const boxMesh = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), [plain, plain, plain, plain, face, face]);
    group.add(boxMesh);
    if (showTopNameplate) {
      group.add(topNameplateMesh(container, w, h, d));
    }
    return group;
  }

  function outlineFrame(w, h, d, color, thick = 0.005) {
    const group = new THREE.Group();
    const bm = new THREE.MeshBasicMaterial({color});
    const gx = new THREE.BoxGeometry(w + thick, thick, thick);
    const gy = new THREE.BoxGeometry(thick, h + thick, thick);
    const gz = new THREE.BoxGeometry(thick, thick, d + thick);
    const hx = w / 2;
    const hy = h / 2;
    const hz = d / 2;
    for (const sy of [-hy, hy]) {
      for (const sz of [-hz, hz]) {
        const e = new THREE.Mesh(gx, bm);
        e.position.set(0, sy, sz);
        group.add(e);
      }
    }
    for (const sx of [-hx, hx]) {
      for (const sz of [-hz, hz]) {
        const e = new THREE.Mesh(gy, bm);
        e.position.set(sx, 0, sz);
        group.add(e);
      }
    }
    for (const sx of [-hx, hx]) {
      for (const sy of [-hy, hy]) {
        const e = new THREE.Mesh(gz, bm);
        e.position.set(sx, sy, 0);
        group.add(e);
      }
    }
    group.renderOrder = 5;
    return group;
  }

  // Conveyor body.
  {
    const conv = layout.conveyor;
    const pos = planePxToWorld(conv.body_uv[0], conv.body_uv[1], basePlane);
    const [bw, bh, bd] = conv.body_size_m;
    const body = new THREE.Mesh(new THREE.BoxGeometry(bw, bh, bd), stdMat(palette.slate));
    body.position.set(pos.x, conv.body_y_m, pos.z);
    scene.add(body);
  }

  // Occupied containers from the product yard scene.
  for (const [siteRef, container] of Object.entries(containers)) {
    const cell = cells.get(siteRef);
    if (!cell) continue;
    const mesh = codedBox(
      container,
      cell.size.x * 0.86,
      cell.size.y * 0.86,
      cell.size.z * 0.86,
      {showTopNameplate: isTopContainerInStack(siteRef, containers)},
    );
    mesh.position.copy(cell.pos);
    scene.add(mesh);
  }

  // Arms.
  function addArmMesh(x, z) {
    const group = new THREE.Group();
    const base = new THREE.Mesh(new THREE.CylinderGeometry(0.03, 0.035, 0.05, 20), stdMat(palette.navy));
    const post = new THREE.Mesh(new THREE.BoxGeometry(0.025, 0.12, 0.025), stdMat(palette.slateDark));
    base.position.y = 0.025;
    post.position.y = 0.11;
    group.add(base, post);
    group.position.set(x, 0, z);
    scene.add(group);
  }
  for (const [armId, [ax, ay]] of Object.entries(layout.arms)) {
    const p = rosyMetersToWorld(ax, ay);
    addArmMesh(p.x, p.z);
    const labelText = armId.replace('_', ' ').toUpperCase().replace('ARM ', '팔 ');
    addNameTag(labelText, p.x, 0.24, p.z);
  }

  // Vehicle stops.
  for (const [stopId, [sx, sy]] of Object.entries(layout.stops)) {
    const p = rosyMetersToWorld(sx, sy);
    const ring = new THREE.Mesh(
      new THREE.RingGeometry(0.02, 0.03, 24),
      new THREE.MeshBasicMaterial({color: palette.slate, transparent: true, opacity: 0.8, side: THREE.DoubleSide}),
    );
    ring.rotation.x = -Math.PI / 2;
    ring.position.set(p.x, 0.004, p.z);
    scene.add(ring);
    addNameTag(stopId, p.x, 0.07, p.z);
  }

  // Vehicle (Pinky / rosy_42).
  {
    const vp = rosyMetersToWorld(layout.vehicle.xy[0], layout.vehicle.xy[1]);
    const body = new THREE.Mesh(new THREE.BoxGeometry(0.1, 0.07, 0.1), stdMat(palette.navy));
    body.position.set(vp.x, 0.05, vp.z);
    const tray = new THREE.Mesh(new THREE.BoxGeometry(0.07, 0.015, 0.06), stdMat(palette.slateDark));
    tray.position.set(vp.x + 0.02, 0.095, vp.z);
    scene.add(body, tray);
    addNameTag(layout.vehicle.label, vp.x, 0.2, vp.z + 0.05);
  }

  // Belt-in and belt pick beads.
  const beltInPos = planePxToWorld(layout.conveyor['belt-in'].uv[0], layout.conveyor['belt-in'].uv[1], basePlane);
  const beltPickPos = planePxToWorld(layout.conveyor.belt.uv[0], layout.conveyor.belt.uv[1], basePlane);
  {
    const bi = new THREE.Mesh(new THREE.SphereGeometry(0.012), stdMat(palette.slate));
    bi.position.set(beltInPos.x, 0.05, beltInPos.z);
    const bo = new THREE.Mesh(new THREE.SphereGeometry(0.011), stdMat(palette.orange));
    bo.position.set(beltPickPos.x, 0.07, beltPickPos.z);
    scene.add(bi, bo);
    addNameTag('belt-in', beltInPos.x - 0.07, 0.1, beltInPos.z + 0.04);
    addNameTag('belt', beltPickPos.x - 0.1, 0.12, beltPickPos.z - 0.005);
  }

  // Zone labels (5 calm tags).
  for (const item of layout.zone_labels) {
    const p = planePxToWorld(item.uv[0], item.uv[1], basePlane);
    addZoneTag(item.label, p.x, 0.02, p.z);
  }

  // Cell boxes (visibility + hover/focus highlight).
  const stripeTex = (() => {
    const cv = document.createElement('canvas');
    cv.width = 32;
    cv.height = 32;
    const g = cv.getContext('2d');
    g.fillStyle = hexToRgba(palette.unknownSoft, 0.45);
    g.fillRect(0, 0, 32, 32);
    g.strokeStyle = hexToRgba(palette.slate, 0.85);
    g.lineWidth = 4;
    g.beginPath();
    g.moveTo(-4, 20);
    g.lineTo(20, -4);
    g.moveTo(8, 36);
    g.lineTo(36, 8);
    g.moveTo(-4, 36);
    g.lineTo(36, -4);
    g.stroke();
    const t = new THREE.CanvasTexture(cv);
    t.wrapS = THREE.RepeatWrapping;
    t.wrapT = THREE.RepeatWrapping;
    return t;
  })();

  const FILL = {
    idle: new THREE.MeshBasicMaterial({transparent: true, opacity: 0, depthWrite: false}),
    yes: new THREE.MeshBasicMaterial({color: palette.teal, transparent: true, opacity: 0.12, depthWrite: false}),
    hidden: new THREE.MeshBasicMaterial({map: stripeTex, transparent: true, opacity: 0.5, depthWrite: false}),
    hover: new THREE.MeshBasicMaterial({transparent: true, opacity: 0, depthWrite: false}),
  };
  const EDGE = {
    idle: new THREE.LineBasicMaterial({color: palette.navy, transparent: true, opacity: 0.15}),
    yes: new THREE.LineBasicMaterial({color: palette.teal, transparent: true, opacity: 0.7}),
    hidden: new THREE.LineBasicMaterial({color: palette.slate}),
    hover: new THREE.LineBasicMaterial({color: palette.orange}),
  };

  const cellMeshes = [];
  for (const c of cells.values()) {
    const geom = new THREE.BoxGeometry(c.size.x, c.size.y, c.size.z);
    const mesh = new THREE.Mesh(geom, FILL.idle);
    mesh.position.copy(c.pos);
    mesh.userData.ref = c.ref;
    mesh.renderOrder = 2;
    c.edges = new THREE.LineSegments(new THREE.EdgesGeometry(geom), EDGE.idle);
    mesh.add(c.edges);
    c.outline = outlineFrame(c.size.x + 0.002, c.size.y + 0.002, c.size.z + 0.002, palette.orange, 0.005);
    c.outline.visible = false;
    mesh.add(c.outline);
    c.mesh = mesh;
    scene.add(mesh);
    cellMeshes.push(mesh);
  }

  // Camera gizmos: 8-line frustum ONLY for selected camera.
  function frustumLines(cam) {
    const far = [[-1, -1], [1, -1], [1, 1], [-1, 1]].map(([x, y]) => V3(x, y, 1).unproject(cam));
    const pts = [];
    far.forEach((pt, idx) => {
      pts.push(cam.position.clone(), pt, pt, far[(idx + 1) % 4]);
    });
    return new THREE.LineSegments(
      new THREE.BufferGeometry().setFromPoints(pts),
      new THREE.LineBasicMaterial({color: palette.orange, transparent: true, opacity: 0.8}),
    );
  }

  const gizmos = {};
  for (const camCfg of cameras) {
    const pose = camCfg.pose;
    const aspect = camCfg.aspect[0] / camCfg.aspect[1];
    const cam = new THREE.PerspectiveCamera(pose.fov, aspect, 0.02, pose.far);
    cam.position.set(...pose.pos);
    cam.up.set(...pose.up);
    cam.lookAt(...pose.target);
    cam.updateMatrixWorld(true);
    const helper = frustumLines(cam);
    scene.add(helper);
    const body = new THREE.Mesh(new THREE.BoxGeometry(0.05, 0.035, 0.035), stdMat(palette.slate));
    body.position.copy(cam.position);
    body.quaternion.copy(cam.quaternion);
    scene.add(body);
    const tag = makeLabel(camCfg.name, {bg: palette.orange, fg: palette.white, size: 26});
    tag.position.copy(cam.position).add(V3(...camCfg.tag_offset));
    scene.add(tag);
    gizmos[camCfg.key] = {cam, helper, body, tag, aspect, pose};
  }

  // Interactive state.
  let sel = camByKey[data.default_camera] ? data.default_camera : (cameras[0]?.key || 'top');
  let camMode = false;
  let showNames = false;
  let hover = new Set();
  let pinnedRef = null;
  let hoverTag = null;
  let anim = null;
  let currentAspect = 4 / 3;
  let activeFrameAbort = null;
  let activeBlobUrl = null;
  let pollTimer = 0;

  function syncCanvasSize() {
    const w = Math.max(240, viewHost.clientWidth || 640);
    const h = Math.max(180, Math.round(w / currentAspect));
    renderer.setSize(w, h, false);
    view.aspect = currentAspect;
    view.updateProjectionMatrix();
  }

  function setAspectRatio(aspect) {
    currentAspect = aspect;
    viewHost.classList.toggle('is-wide', aspect > 1.5);
    syncCanvasSize();
  }

  const resizeObserver = typeof ResizeObserver === 'function' ? new ResizeObserver(() => syncCanvasSize()) : null;
  if (resizeObserver) resizeObserver.observe(viewHost);

  function applyGizmos() {
    for (const [key, g] of Object.entries(gizmos)) {
      const on = key === sel;
      g.helper.visible = on && !camMode;
      g.body.material.color.set(on ? palette.orange : palette.slate);
      g.body.visible = !(camMode && on);
      g.tag.visible = on && !camMode;
    }
  }

  function refreshCells() {
    zoneTags.forEach(t => { t.visible = !camMode; });
    nameTags.forEach(t => { t.visible = showNames && !camMode; });
    for (const c of cells.values()) {
      const hideTempOnSide = sel === 'side' && (c.zone === 'temp_a' || c.zone === 'temp_b');
      c.mesh.visible = !hideTempOnSide;
      if (hideTempOnSide) continue;
      const v = cellVisibility(sel, c.ref, c.zone);
      const isHover = hover.has(c.ref);
      const state = isHover ? 'hover' : v === 'yes' ? 'yes' : v === 'hidden' ? 'hidden' : 'idle';
      c.mesh.material = state === 'yes' && containers[c.ref] ? FILL.idle : FILL[state];
      c.edges.material = EDGE[state];
      c.edges.visible = !isHover;
      c.outline.visible = isHover;
    }
    frameEl.querySelectorAll('.cm-ov [data-refs]').forEach(el => {
      const refs = (el.dataset.refs || '').split(',');
      el.classList.toggle('on', refs.some(r => hover.has(r)));
    });
  }

  function renderInfo(primary, refs) {
    infoEl.textContent = '';
    if (!primary || !cells.has(primary)) {
      const row = document.createElement('div');
      row.className = 'cm-info-row';
      row.textContent = '3D의 칸 또는 카메라 화면의 박스 위에 마우스를 올려 보세요.';
      infoEl.appendChild(row);
      return;
    }
    const c = cells.get(primary);
    const container = containers[primary];
    const refLine = document.createElement('div');
    refLine.className = 'cm-info-ref';
    refLine.textContent = primary;
    if (refs.length > 1) {
      const extra = document.createElement('span');
      extra.className = 'cm-info-row';
      extra.textContent = ` (같은 자리 ${refs.length}단 묶음)`;
      refLine.appendChild(extra);
    }
    infoEl.appendChild(refLine);

    const detailParts = [
      `구역 ${c.zone}`,
      c.zone === 'belt' ? '집기 지점' : `${c.tier}단 (l${c.tier})`,
    ];
    if (container) {
      let cText = `컨테이너 ${container.code}`;
      if (container.color_label) cText += ` · ${container.color_label}`;
      if (container.priority_label) cText += ` · ${container.priority_label}`;
      detailParts.push(cText);
    } else {
      detailParts.push('비어 있음');
    }
    const detailRow = document.createElement('div');
    detailRow.className = 'cm-info-row';
    detailRow.textContent = detailParts.join(' · ');
    infoEl.appendChild(detailRow);

    const visRow = document.createElement('div');
    visRow.className = 'cm-info-row';
    cameras.forEach((camCfg, idx) => {
      if (idx > 0) visRow.appendChild(document.createTextNode(' · '));
      visRow.appendChild(document.createTextNode(`${camCfg.name} `));
      const v = cellVisibility(camCfg.key, primary, c.zone);
      const span = document.createElement('span');
      span.className = `cm-vis-${v}`;
      span.textContent = VIS_TXT[v];
      visRow.appendChild(span);
    });
    infoEl.appendChild(visRow);
  }

  function setHover(refs, primary) {
    hover = new Set(refs || []);
    refreshCells();
    if (hoverTag) {
      scene.remove(hoverTag);
      hoverTag.material.map.dispose();
      hoverTag.material.dispose();
      hoverTag = null;
    }
    if (primary && cells.has(primary)) {
      const c = cells.get(primary);
      const container = containers[primary];
      const tagText = container
        ? (container.priority_label ? `${container.code} · ${container.priority_label} · ${primary}` : `${container.code} · ${primary}`)
        : primary;
      hoverTag = makeLabel(tagText, {bg: palette.orange, fg: palette.white, size: 26});
      hoverTag.position.copy(c.pos).add(V3(0, c.size.y / 2 + 0.05, 0));
      hoverTag.renderOrder = 20;
      scene.add(hoverTag);
    }
    renderInfo(primary, refs || []);
  }

  function buildOverlaySvg(camKey, camCfg) {
    const isBird = camKey === 'bird';
    const ovDoc = overlays[camKey];
    const vw = isBird ? activePlane.image_w : (ovDoc?.image?.[0] || 640);
    const vh = isBird ? activePlane.image_h : (ovDoc?.image?.[1] || 480);
    const svg = svgEl('svg', {viewBox: `0 0 ${vw} ${vh}`, class: 'cm-ov', 'aria-label': `${camCfg.name} 자리 박스`});
    const addTxt = (text, x, y, cls = '') => {
      const attrs = {x, y, 'text-anchor': 'middle', 'dominant-baseline': 'central'};
      if (cls) attrs.class = cls;
      const t = svgEl('text', attrs, svg);
      t.textContent = text;
    };
    const wire = (node, refs, primary) => {
      node.dataset.refs = refs.join(',');
      node.addEventListener('pointerenter', () => {
        pinnedRef = null;
        setHover(refs, primary || refs[0]);
      });
      node.addEventListener('pointerleave', () => {
        if (pinnedRef) setHover([pinnedRef], pinnedRef);
        else setHover([], null);
      });
    };

    const pendingLabels = [];
    if (camKey === 'top' && ovDoc && Array.isArray(ovDoc.points)) {
      for (const p of ovDoc.points) {
        if (p.kind === 'pick') {
          if (!Array.isArray(p.point)) {
            pendingLabels.push(p.short_label || p.label);
            continue;
          }
          const pt = transformOverlayPoint(p.point, [vw, vh], camCfg.flip);
          wire(svgEl('circle', {cx: pt.x, cy: pt.y, r: 11, class: 'b'}, svg), ['belt'], 'belt');
          addTxt('belt', pt.x, pt.y + 20);
          continue;
        }
        if (Array.isArray(p.polygon) && p.polygon.length >= 3) {
          const poly = transformOverlayPolygon(p.polygon, [vw, vh], camCfg.flip);
          wire(svgEl('polygon', {points: poly.pointsAttr, class: 'b'}, svg), p.wire_refs, p.wire_refs[0]);
          addTxt((p.short_label || p.site).replace(/^Y\s+/, ''), poly.cx, poly.cy);
          continue;
        }
        if (!Array.isArray(p.roi)) {
          pendingLabels.push(p.short_label || p.label);
          continue;
        }
        const rect = transformOverlayRoi(p.roi, [vw, vh], camCfg.flip);
        wire(svgEl('rect', {x: rect.x, y: rect.y, width: rect.width, height: rect.height, class: 'b'}, svg), p.wire_refs, p.wire_refs[0]);
        addTxt((p.short_label || p.site).replace(/^Y\s+/, ''), rect.x + rect.width / 2, rect.y + rect.height / 2);
      }
    } else if (camKey === 'side' && ovDoc && Array.isArray(ovDoc.points)) {
      for (const p of ovDoc.points) {
        if (p.zone === 'temp_a' || p.zone === 'temp_b') continue;
        if (!Array.isArray(p.roi)) {
          pendingLabels.push(p.short_label || p.label);
          continue;
        }
        const [x0, y0, x1, y1] = p.roi;
        const primary = sideOverlayPrimaryRef(p.wire_refs);
        wire(
          svgEl('rect', {x: x0, y: y0, width: x1 - x0, height: y1 - y0, class: 'b'}, svg),
          p.wire_refs,
          primary,
        );
        const occupant = containers[primary];
        const fallbackLabel = primary.replace(/^(yard|temp_a|temp_b)-/, '').replace('-c1', '');
        addTxt(occupant ? occupant.code : fallbackLabel, (x0 + x1) / 2, (y0 + y1) / 2);
      }
    } else if (camKey === 'bird') {
      const seen = new Set();
      const ppm = activePlane.pixels_per_meter;
      for (const c of cells.values()) {
        if (c.zone === 'belt') continue;
        const stackKey = c.ref.replace(/-l\d+$/, '');
        if (seen.has(stackKey)) continue;
        seen.add(stackKey);
        const refs = [...cells.keys()].filter(r => r.startsWith(`${stackKey}-l`));
        const [u, v] = worldToPlanePx(c.pos.x, c.pos.z, activePlane);
        const rw = c.size.x * ppm;
        const rh = c.size.z * ppm;
        wire(svgEl('rect', {x: u - rw / 2, y: v - rh / 2, width: rw, height: rh, class: 'b'}, svg), refs, refs[0]);
      }
      const [bu, bv] = worldToPlanePx(beltPickPos.x, beltPickPos.z, activePlane);
      wire(svgEl('circle', {cx: bu, cy: bv, r: 8, class: 'b'}, svg), ['belt'], 'belt');
      for (const [armId, [ax, ay]] of Object.entries(layout.arms)) {
        const [u, v] = rosyMetersToPlanePx(ax, ay, activePlane);
        svgEl('circle', {cx: u, cy: v, r: 5, class: 'arm'}, svg);
        addTxt(armId, u, v - 10, 'sm');
      }
      const stopsWithVehicle = {...layout.stops, [layout.vehicle.id]: layout.vehicle.xy};
      for (const [stopId, [sx, sy]] of Object.entries(stopsWithVehicle)) {
        const [u, v] = rosyMetersToPlanePx(sx, sy, activePlane);
        svgEl('circle', {cx: u, cy: v, r: 4, class: 'st'}, svg);
        addTxt(stopId, u, v - 9, 'sm');
      }
    } else if (camKey === 'arms' && ovDoc && Array.isArray(ovDoc.points)) {
      for (const p of ovDoc.points) {
        if (Array.isArray(p.point)) {
          const pt = transformOverlayPoint(p.point, [vw, vh], camCfg.flip);
          wire(svgEl('circle', {cx: pt.x, cy: pt.y, r: 10, class: 'b'}, svg), [p.site], p.site);
          addTxt(p.short_label || p.site, pt.x, pt.y + 18);
        } else {
          pendingLabels.push(p.short_label || p.label);
        }
      }
    }
    return {svg, pendingLabels};
  }

  let frameImg = null;
  let framePh = null;
  let frameSvg = null;

  function renderFrameShell(camKey) {
    const camCfg = camByKey[camKey];
    frameEl.textContent = '';
    frameEl.classList.toggle('is-wide', camKey === 'bird');
    frameTitle.textContent = camCfg.name;
    frameSub.textContent = camCfg.source_id;

    const rot = document.createElement('div');
    rot.className = camCfg.flip ? 'cm-rot flip' : 'cm-rot';
    frameImg = document.createElement('img');
    frameImg.alt = `${camCfg.name} 카메라 화면`;
    frameImg.hidden = true;
    rot.appendChild(frameImg);
    frameEl.appendChild(rot);

    framePh = document.createElement('div');
    framePh.className = 'cm-ph';
    framePh.textContent = `${camCfg.name} 영상 없음`;
    frameEl.appendChild(framePh);

    const {svg, pendingLabels} = buildOverlaySvg(camKey, camCfg);
    frameSvg = svg;
    frameEl.appendChild(svg);

    const notes = [];
    if (pendingLabels.length) notes.push(`위치 미정: ${pendingLabels.join(' · ')}`);
    if (!camCfg.available && data.status) notes.push(data.status);
    noteEl.textContent = notes.join(' · ');
    refreshCells();
  }

  function decodeBlobImage(url) {
    return new Promise((resolve, reject) => {
      const img = new Image();
      img.onload = () => resolve(img);
      img.onerror = reject;
      img.src = url;
    });
  }

  async function fetchBirdFloorOnce() {
    const birdCfg = camByKey.bird;
    if (!birdCfg || !birdCfg.url) return;
    try {
      const res = await fetch(birdCfg.url, {cache: 'no-store'});
      if (!res.ok) return;
      const planeHeader = res.headers.get('X-Frame-Plane');
      const nextPlane = parseFramePlane(planeHeader, basePlane);
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      try {
        const img = await decodeBlobImage(url);
        activePlane = nextPlane;
        applyFloorTexture(img, activePlane);
        if (sel === 'bird' && frameSvg) {
          const rebuilt = buildOverlaySvg('bird', birdCfg);
          frameSvg.replaceWith(rebuilt.svg);
          frameSvg = rebuilt.svg;
          refreshCells();
        }
      } finally {
        URL.revokeObjectURL(url);
      }
    } catch (_) {
      // No birdview frame available -> floor keeps light grid only.
    }
  }

  async function loadActiveCameraFrame() {
    clearTimeout(pollTimer);
    if (activeFrameAbort) activeFrameAbort.abort();
    const currentKey = sel;
    const camCfg = camByKey[currentKey];
    if (!camCfg || !camCfg.url) {
      if (frameImg) frameImg.hidden = true;
      if (framePh) framePh.hidden = false;
      return;
    }
    const ctrl = new AbortController();
    activeFrameAbort = ctrl;
    try {
      const res = await fetch(camCfg.url, {cache: 'no-store', signal: ctrl.signal});
      if (sel !== currentKey) return;
      if (!res.ok) {
        if (frameImg) frameImg.hidden = true;
        if (framePh) framePh.hidden = false;
      } else {
        const planeHeader = currentKey === 'bird' ? res.headers.get('X-Frame-Plane') : null;
        const blob = await res.blob();
        if (sel !== currentKey) return;
        const nextUrl = URL.createObjectURL(blob);
        try {
          const decoded = await decodeBlobImage(nextUrl);
          if (sel !== currentKey) {
            URL.revokeObjectURL(nextUrl);
            return;
          }
          if (currentKey === 'bird') {
            const nextPlane = parseFramePlane(planeHeader, basePlane);
            activePlane = nextPlane;
            applyFloorTexture(decoded, activePlane);
            if (frameSvg) {
              const rebuilt = buildOverlaySvg('bird', camCfg);
              frameSvg.replaceWith(rebuilt.svg);
              frameSvg = rebuilt.svg;
            }
          }
          if (activeBlobUrl) URL.revokeObjectURL(activeBlobUrl);
          activeBlobUrl = nextUrl;
          frameImg.src = nextUrl;
          frameImg.hidden = false;
          framePh.hidden = true;
          refreshCells();
        } catch (_) {
          URL.revokeObjectURL(nextUrl);
          frameImg.hidden = true;
          framePh.hidden = false;
        }
      }
    } catch (err) {
      if (err && err.name === 'AbortError') return;
      if (frameImg) frameImg.hidden = true;
      if (framePh) framePh.hidden = false;
    }
    if (!document.hidden && sel === currentKey) {
      pollTimer = setTimeout(loadActiveCameraFrame, FRAME_POLL_MS);
    }
  }

  // Camera view transitions.
  function poseFor(key) {
    const g = gizmos[key];
    const p = g.pose;
    const c = new THREE.PerspectiveCamera();
    c.up.set(...(p.view_up || p.up));
    c.position.set(...p.pos);
    c.lookAt(...p.target);
    return {pos: c.position.clone(), quat: c.quaternion.clone(), fov: p.fov};
  }

  function orbitPose() {
    const c = new THREE.PerspectiveCamera();
    c.position.copy(overview.pos);
    c.lookAt(overview.target);
    return {pos: overview.pos.clone(), quat: c.quaternion.clone(), fov: overview.fov};
  }

  function flyTo(to, done, ms = 700) {
    controls.enabled = false;
    anim = {
      t0: performance.now(),
      ms,
      from: {pos: view.position.clone(), quat: view.quaternion.clone(), fov: view.fov},
      to,
      done,
    };
  }

  function setMode(isCam) {
    camMode = isCam;
    modeLbl.textContent = isCam ? `${camByKey[sel].name} 시점` : '전체 보기';
    applyGizmos();
    refreshCells();
  }

  function selectCamera(key, {keepPinned = false} = {}) {
    if (!camByKey[key]) return;
    sel = key;
    if (!keepPinned) pinnedRef = null;
    chipButtons.forEach(btn => {
      const on = btn.dataset.cmCamera === key;
      btn.classList.toggle('on', on);
      btn.setAttribute('aria-pressed', on ? 'true' : 'false');
    });
    renderFrameShell(key);
    if (pinnedRef && cells.has(pinnedRef)) setHover([pinnedRef], pinnedRef);
    else setHover([], null);
    if (camMode) {
      setMode(true);
      setAspectRatio(gizmos[key].aspect);
      flyTo(poseFor(key));
    }
    applyGizmos();
    loadActiveCameraFrame();
  }

  function toCameraView() {
    setMode(true);
    setAspectRatio(gizmos[sel].aspect);
    flyTo(poseFor(sel));
  }

  function toOrbitView() {
    setMode(false);
    setAspectRatio(4 / 3);
    flyTo(orbitPose(), () => {
      view.up.set(0, 1, 0);
      controls.target.copy(overview.target);
      controls.enabled = true;
      controls.update();
    });
  }

  chipButtons.forEach(btn => {
    btn.addEventListener('click', () => selectCamera(btn.dataset.cmCamera));
  });
  btnCam.addEventListener('click', () => toCameraView());
  btnOrbit.addEventListener('click', () => toOrbitView());
  tgNames.addEventListener('change', e => {
    showNames = Boolean(e.target.checked);
    refreshCells();
  });

  // 3D raycast hover.
  const ray = new THREE.Raycaster();
  const ndc = new THREE.Vector2();
  renderer.domElement.addEventListener('pointermove', e => {
    if (anim) return;
    const rect = renderer.domElement.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    ndc.set(((e.clientX - rect.left) / rect.width) * 2 - 1, -((e.clientY - rect.top) / rect.height) * 2 + 1);
    ray.setFromCamera(ndc, view);
    const hit = ray.intersectObjects(cellMeshes.filter(m => m.visible), false)[0];
    const ref = hit && hit.object.userData.ref;
    if (!ref) {
      if (!pinnedRef && hover.size) setHover([], null);
      return;
    }
    pinnedRef = null;
    if (hover.size === 1 && hover.has(ref)) return;
    setHover([ref], ref);
  });
  renderer.domElement.addEventListener('pointerleave', () => {
    if (pinnedRef) setHover([pinnedRef], pinnedRef);
    else setHover([], null);
  });

  // ROSY recognition mismatch -> "카메라로 확인" focus handler.
  function focusSite(siteRef) {
    if (!siteRef) return;
    const targetCam = pickCameraForSite(siteRef, cameras);
    pinnedRef = cells.has(siteRef) ? siteRef : null;
    selectCamera(targetCam, {keepPinned: true});
    if (pinnedRef) setHover([pinnedRef], pinnedRef);
    root.scrollIntoView({behavior: 'smooth', block: 'start'});
  }

  document.addEventListener('click', e => {
    const btn = e.target.closest('[data-camera-map-focus]');
    if (!btn) return;
    focusSite(btn.dataset.cameraMapFocus);
  });

  document.addEventListener('visibilitychange', () => {
    if (document.hidden) clearTimeout(pollTimer);
    else loadActiveCameraFrame();
  });

  // Render loop.
  function tick(now) {
    if (anim) {
      const t = Math.min(1, (now - anim.t0) / anim.ms);
      const ease = t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2;
      view.position.lerpVectors(anim.from.pos, anim.to.pos, ease);
      view.quaternion.slerpQuaternions(anim.from.quat, anim.to.quat, ease);
      view.fov = anim.from.fov + (anim.to.fov - anim.from.fov) * ease;
      view.updateProjectionMatrix();
      if (t >= 1) {
        const done = anim.done;
        anim = null;
        if (done) done();
      }
    } else if (controls.enabled) {
      controls.update();
    }
    root.dataset.cameraMapBusy = anim ? '1' : '0';
    renderer.render(scene, view);
    requestAnimationFrame(tick);
  }

  selectCamera(sel);
  fetchBirdFloorOnce();
  requestAnimationFrame(tick);
  root.dataset.cameraMapReady = '1';
}

document.querySelectorAll('[data-camera-map]').forEach(mountCameraMap);
