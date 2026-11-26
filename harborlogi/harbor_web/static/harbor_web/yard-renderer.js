import {transposeArea, transposeBox, transposePoint} from './yard-display.mjs';
import {motionPath} from './yard-model.mjs';
import {
  DEFAULT_CAMERA_VIEW,
  DEFAULT_FIT_PADDING,
  FOCUS_FIT_PADDING,
  VIEW_DIRECTIONS,
  badgeColorsForBox,
  computeFitDistance,
  resolvePriorityTier,
  topNameplateSpec,
} from './yard-visuals.mjs';
import * as THREE from './vendor/three-0.186.1/three.module.js';
import {OrbitControls} from './vendor/three-0.186.1/OrbitControls.js';

export function bindDragInteraction({
  canvas,
  win = typeof window !== 'undefined' ? window : null,
  orbit,
  isEditable,
  getSelected,
  pickHitBox,
  getStartPosition,
  restorePosition,
  resetOutline,
  updatePreview,
  clearDragVisuals,
  onSelect,
  onDrop,
  invalidate,
}) {
  const activePointers = new Set();
  let pointer = null, multiPointer = false, drag = null;

  function endDrag(apply) {
    if (!drag) return;
    const finished = drag;
    drag = null;
    orbit.enabled = true;
    clearDragVisuals?.();
    resetOutline?.(finished.id);
    if (finished.active) {
      if (apply) {
        onDrop?.(finished.id, finished.target);
      } else {
        restorePosition?.(finished.id, finished.startPos);
      }
      invalidate?.();
    } else if (apply) {
      onSelect?.(finished.id);
    }
  }
  function cancelDrag() {
    if (!drag) return;
    const pointerId = drag.pointerId;
    activePointers.delete(pointerId);
    if (!activePointers.size) multiPointer = false;
    endDrag(false);
    try { canvas.releasePointerCapture?.(pointerId); } catch (_) {}
    pointer = null;
  }
  function pointerDown(event) {
    if (event.button !== 0) return;
    activePointers.add(event.pointerId);
    if (activePointers.size > 1) {
      multiPointer = true;
      cancelDrag();
    }
    if (multiPointer) { pointer = null; return; }
    if (isEditable()) {
      const hitId = pickHitBox(event);
      if (hitId) {
        orbit.enabled = false;
        drag = {
          id: hitId,
          pointerId: event.pointerId,
          startX: event.clientX,
          startY: event.clientY,
          active: false,
          target: null,
          startPos: getStartPosition(hitId),
        };
        try { canvas.setPointerCapture?.(event.pointerId); } catch (_) {}
        pointer = null;
        return;
      }
    }
    pointer = {x: event.clientX, y: event.clientY, id: event.pointerId, moved: false};
  }
  function pointerMove(event) {
    if (drag && drag.pointerId === event.pointerId) {
      if (!drag.active && Math.hypot(drag.startX - event.clientX, drag.startY - event.clientY) > 4) {
        drag.active = true;
        canvas.dataset.dragging = 'true';
        if (getSelected?.() !== drag.id) onSelect?.(drag.id);
      }
      if (drag.active) {
        const resolved = updatePreview?.(drag.id, event);
        if (resolved !== undefined) drag.target = resolved;
      }
      return;
    }
    if (pointer?.id === event.pointerId && Math.hypot(pointer.x - event.clientX, pointer.y - event.clientY) > 5)
      pointer.moved = true;
  }
  function pointerCancel(event) {
    activePointers.delete(event.pointerId);
    if (!activePointers.size) multiPointer = false;
    if (drag && drag.pointerId === event.pointerId) {
      endDrag(false);
      try { canvas.releasePointerCapture?.(event.pointerId); } catch (_) {}
    }
    pointer = null;
  }
  function lostPointerCapture(event) {
    if (drag && drag.pointerId === event.pointerId) {
      activePointers.delete(event.pointerId);
      if (!activePointers.size) multiPointer = false;
      endDrag(false);
      pointer = null;
    }
  }
  function pointerUp(event) {
    activePointers.delete(event.pointerId);
    const suppressPick = multiPointer;
    if (!activePointers.size) multiPointer = false;
    if (drag && drag.pointerId === event.pointerId) {
      endDrag(!suppressPick);
      try { canvas.releasePointerCapture?.(event.pointerId); } catch (_) {}
      return;
    }
    if (suppressPick) { pointer = null; return; }
    if (!pointer || pointer.id !== event.pointerId) return;
    const start = pointer; pointer = null;
    if (start.moved || Math.hypot(start.x - event.clientX, start.y - event.clientY) > 5) return;
    onSelect?.(pickHitBox(event));
  }
  function windowKeyDown(event) {
    if (event.key === 'Escape' && drag) {
      event.preventDefault?.();
      cancelDrag();
    }
  }
  function windowBlur() {
    activePointers.clear();
    multiPointer = false;
    pointer = null;
    if (drag) endDrag(false);
  }
  canvas.addEventListener('pointerdown', pointerDown, {capture: true});
  canvas.addEventListener('pointermove', pointerMove);
  canvas.addEventListener('pointercancel', pointerCancel);
  canvas.addEventListener('lostpointercapture', lostPointerCapture);
  canvas.addEventListener('pointerup', pointerUp);
  win?.addEventListener('keydown', windowKeyDown);
  win?.addEventListener('blur', windowBlur);
  return {
    endDrag,
    isDragging: () => Boolean(drag),
    dispose() {
      endDrag(false);
      canvas.removeEventListener('pointerdown', pointerDown, {capture: true});
      canvas.removeEventListener('pointermove', pointerMove);
      canvas.removeEventListener('pointercancel', pointerCancel);
      canvas.removeEventListener('lostpointercapture', lostPointerCapture);
      canvas.removeEventListener('pointerup', pointerUp);
      win?.removeEventListener('keydown', windowKeyDown);
      win?.removeEventListener('blur', windowBlur);
    },
  };
}

export function createRenderer(canvas, areas, {onSelect, onResolveDrop, onDrop, onError, onPlaybackKey, onUserCameraStart, colors = {}, showVolumes = false, transpose = false} = {}) {
  if (transpose) areas = areas.map(transposeArea);
  const context = canvas.getContext('webgl2', {antialias: true, preserveDrawingBuffer: true});
  if (!context) throw new Error('WebGL2 is unavailable');
  const cargoPalette = Object.fromEntries(Object.entries(colors.cargo || {}).filter(([, v]) => v));
  const palette = {
    target: '#112c3f', blocker: '#fe6d08', stored: '#2cbbb7', danger: '#b72d43',
    tierUrgent: '#b72d43', tierPriority: '#b94d00', tierNormal: '#112c3f', tierNeutral: '#506878',
    cream: '#fcf1d9', line: '#d7e3e9', text: '#17364b', muted: '#506878', floor: '#ffffff',
    white: '#ffffff', canvas: '#f4f8fb',
    ...Object.fromEntries(Object.entries(colors).filter(([k, v]) => k !== 'cargo' && v)),
    cargo: cargoPalette,
  };
  const renderer = new THREE.WebGLRenderer({canvas, context, antialias: true, preserveDrawingBuffer: true});
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(palette.canvas);
  const camera = new THREE.PerspectiveCamera(40, 1, .05, 1000);
  const defaultDir = VIEW_DIRECTIONS[DEFAULT_CAMERA_VIEW] || VIEW_DIRECTIONS.front;
  camera.position.set(defaultDir.x * 20, defaultDir.y * 20, defaultDir.z * 20);
  canvas.dataset.cameraView = DEFAULT_CAMERA_VIEW;
  const orbit = new OrbitControls(camera, canvas);
  orbit.enableDamping = true;
  orbit.dampingFactor = .15;
  orbit.minDistance = .4;
  orbit.maxDistance = 300;
  orbit.maxPolarAngle = Math.PI * .49;
  scene.add(new THREE.HemisphereLight(palette.white, palette.muted, 2.2));
  const light = new THREE.DirectionalLight(palette.white, 3);
  light.position.set(-8, 18, 10);
  light.castShadow = true;
  light.shadow.mapSize.set(1024, 1024);
  const minX = Math.min(...areas.map(a => a.x)), minY = Math.min(...areas.map(a => a.y));
  const maxX = Math.max(...areas.map(a => a.x + a.columns)), maxY = Math.max(...areas.map(a => a.y + a.rows));
  const extent = Math.max(10, maxX - minX, maxY - minY);
  camera.far = Math.max(1000, extent * 30);
  camera.updateProjectionMatrix();
  orbit.maxDistance = Math.max(300, extent * 10);
  Object.assign(light.shadow.camera, {left: -extent, right: extent, top: extent, bottom: -extent, far: extent * 5});
  light.position.set((minX + maxX) / 2 - extent, extent * 2, (minY + maxY) / 2 + extent);
  light.target.position.set((minX + maxX) / 2, 0, (minY + maxY) / 2);
  light.shadow.bias = -.0003;
  scene.add(light, light.target);
  const resources = new Set();
  const keep = resource => { resources.add(resource); return resource; };
  const cube = keep(new THREE.BoxGeometry(1, 1, 1));
  const plane = keep(new THREE.PlaneGeometry(1, 1));
  const edges = keep(new THREE.EdgesGeometry(cube));
  const cargoMaterials = Object.fromEntries(Object.entries(palette.cargo).map(([name, hex]) =>
    [name, keep(new THREE.MeshStandardMaterial({color: hex, roughness: .52, metalness: .18}))]));
  const neutralMaterial = keep(new THREE.MeshStandardMaterial({color: palette.stored, roughness: .58, metalness: .22}));
  const targetFrameMaterial = keep(new THREE.MeshBasicMaterial({color: palette.target}));
  const blockerFrameMaterial = keep(new THREE.MeshBasicMaterial({color: palette.blocker}));
  const metal = keep(new THREE.MeshStandardMaterial({color: palette.muted, roughness: .5, metalness: .55}));
  const edgeMaterial = keep(new THREE.LineBasicMaterial({color: palette.text, transparent: true, opacity: .45}));
  const targetOutlineMaterial = keep(new THREE.LineBasicMaterial({color: palette.target}));
  const blockerOutlineMaterial = keep(new THREE.LineDashedMaterial({color: palette.blocker, dashSize: .14, gapSize: .08}));
  const selectedMaterial = keep(new THREE.LineBasicMaterial({color: palette.blocker}));
  const motionMaterial = keep(new THREE.LineBasicMaterial({color: palette.target}));
  const dangerMaterial = keep(new THREE.LineBasicMaterial({color: palette.danger}));
  const validDropFill = keep(new THREE.MeshBasicMaterial({color: palette.stored, transparent: true, opacity: .28, depthWrite: false}));
  const invalidDropFill = keep(new THREE.MeshBasicMaterial({color: palette.danger, transparent: true, opacity: .38, depthWrite: false}));
  const validDropEdge = keep(new THREE.LineBasicMaterial({color: palette.stored}));
  const floorMaterial = keep(new THREE.MeshStandardMaterial({color: palette.floor, roughness: 1}));
  const gridMaterial = keep(new THREE.LineBasicMaterial({color: palette.line}));
  const markerMaterial = keep(new THREE.LineDashedMaterial({color:palette.blocker, dashSize:.12, gapSize:.07}));
  const locationMarker = new THREE.LineSegments(edges, markerMaterial);
  locationMarker.computeLineDistances(); locationMarker.scale.set(.94, .94, .94);
  locationMarker.visible = false; scene.add(locationMarker);
  const dropPreview = new THREE.Group();
  const dropFillMesh = new THREE.Mesh(cube, validDropFill);
  const dropEdgeMesh = new THREE.LineSegments(edges, validDropEdge);
  dropPreview.add(dropFillMesh, dropEdgeMesh);
  dropPreview.visible = false;
  scene.add(dropPreview);
  const motionLineGeometry = keep(new THREE.BufferGeometry());
  const motionLineMaterial = keep(new THREE.LineBasicMaterial({color: palette.target}));
  const motionLine = new THREE.Line(motionLineGeometry, motionLineMaterial);
  motionLine.visible = false;
  scene.add(motionLine);
  const groundPlane = new THREE.Plane(new THREE.Vector3(0, 1, 0), 0);
  const planeHit = new THREE.Vector3();
  const models = new Map();
  let selected = null, isolated = null, editable = false, fitted = false, motionId = '';
  let disposed = false, failed = false, raf = 0, revision = 0, renderRevision = 0;
  let width = 0, height = 0;

  function logicalPoint({x, y, z}) {
    const point = {x, y:z, z:y};
    return transpose ? transposePoint(point) : point;
  }
  function invalidate() {
    if (!disposed && !failed && !raf) raf = requestAnimationFrame(draw);
  }
  function syncNameplates() {
    const visibleBoxes = [];
    models.forEach((model, id) => {
      if (model.group.visible && model.box) visibleBoxes.push(model.box);
    });
    models.forEach((model, id) => {
      if (!model.group.visible || !model.box) {
        model.nameplate.visible = false;
        return;
      }
      const spec = topNameplateSpec(model.box, visibleBoxes);
      model.nameplate.visible = spec.visible;
      model.nameplate.position.set(spec.x, spec.y, spec.z);
      model.nameplate.rotation.set(spec.rotX, 0, spec.rotZ);
      model.nameplate.scale.set(spec.length, spec.width, 1);
    });
  }

  function draw() {
    raf = 0;
    if (disposed || failed) return;
    const changed = orbit.update();
    renderer.render(scene, camera);
    canvas.dataset.renderedPositions = JSON.stringify(Object.fromEntries([...models].map(([id, model]) =>
      [id, logicalPoint(model.group.position)])));
    canvas.dataset.renderRevision = String(++renderRevision);
    if (changed) invalidate();
  }
  function cameraChanged() {
    canvas.dataset.viewRevision = String(++revision);
    invalidate();
  }
  orbit.addEventListener('change', cameraChanged);
  orbit.addEventListener('start', () => onUserCameraStart?.());

  function label(text, foreground, background) {
    const surface = document.createElement('canvas');
    surface.width = 512; surface.height = 128;
    const ctx = surface.getContext('2d');
    ctx.fillStyle = background; ctx.fillRect(0, 0, 512, 128);
    ctx.fillStyle = foreground; ctx.font = 'bold 64px "Segoe UI", sans-serif';
    ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    ctx.fillText(String(text), 256, 67, 480);
    const texture = new THREE.CanvasTexture(surface);
    texture.colorSpace = THREE.SRGBColorSpace;
    const material = new THREE.MeshBasicMaterial({map: texture, side: THREE.DoubleSide});
    return {texture, material};
  }
  areas.forEach(area => {
    const floor = new THREE.Mesh(cube, floorMaterial);
    floor.position.set(area.x + area.columns / 2, -.11, area.y + area.rows / 2);
    floor.scale.set(area.columns, .12, area.rows);
    floor.receiveShadow = true;
    scene.add(floor);
    const points = [];
    for (let x = 0; x <= area.columns; x++) points.push(area.x + x, -.045, area.y, area.x + x, -.045, area.y + area.rows);
    for (let y = 0; y <= area.rows; y++) points.push(area.x, -.045, area.y + y, area.x + area.columns, -.045, area.y + y);
    const geometry = keep(new THREE.BufferGeometry());
    geometry.setAttribute('position', new THREE.Float32BufferAttribute(points, 3));
    scene.add(new THREE.LineSegments(geometry, gridMaterial));
    if (showVolumes) {
      const volume = new THREE.LineSegments(edges, edgeMaterial);
      volume.scale.set(area.columns, area.levels, area.rows);
      volume.position.set(area.x + area.columns / 2, area.levels / 2, area.y + area.rows / 2);
      scene.add(volume);
    }
    const text = label(area.label || area.id, palette.text, palette.canvas);
    keep(text.texture); keep(text.material);
    const sign = new THREE.Mesh(plane, text.material);
    sign.rotation.x = -Math.PI / 2;
    sign.scale.set(Math.min(area.columns, 3), .65, 1);
    sign.position.set(area.x + area.columns / 2, -.02, area.y + area.rows + .5);
    scene.add(sign);
  });

  function makeNameplate(text, {bg, fg, border}) {
    const surface = document.createElement('canvas');
    surface.width = 256; surface.height = 96;
    const ctx = surface.getContext('2d');
    const pad = 8, radius = 24, rw = surface.width - pad * 2, rh = surface.height - pad * 2;
    ctx.beginPath();
    ctx.moveTo(pad + radius, pad);
    ctx.lineTo(pad + rw - radius, pad);
    ctx.quadraticCurveTo(pad + rw, pad, pad + rw, pad + radius);
    ctx.lineTo(pad + rw, pad + rh - radius);
    ctx.quadraticCurveTo(pad + rw, pad + rh, pad + rw - radius, pad + rh);
    ctx.lineTo(pad + radius, pad + rh);
    ctx.quadraticCurveTo(pad, pad + rh, pad, pad + rh - radius);
    ctx.lineTo(pad, pad + radius);
    ctx.quadraticCurveTo(pad, pad, pad + radius, pad);
    ctx.closePath();
    ctx.fillStyle = bg;
    ctx.fill();
    ctx.lineWidth = 6;
    ctx.strokeStyle = border;
    ctx.stroke();
    ctx.fillStyle = fg;
    ctx.font = 'bold 52px "Segoe UI", "Malgun Gothic", sans-serif';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(String(text), surface.width / 2, surface.height / 2 + 2, rw - 20);
    const texture = new THREE.CanvasTexture(surface);
    texture.colorSpace = THREE.SRGBColorSpace;
    const material = new THREE.MeshBasicMaterial({
      map: texture,
      transparent: true,
      depthTest: true,
      depthWrite: false,
      polygonOffset: true,
      polygonOffsetFactor: -1,
      polygonOffsetUnits: -1,
    });
    const mesh = new THREE.Mesh(plane, material);
    mesh.renderOrder = 5;
    return {mesh, texture, material};
  }

  function makeRoleFrame(w, h, d, width, depth, role) {
    if (role !== 'target' && role !== 'blocker') return null;
    const t = 0.05;
    const cx = width / 2, cy = h / 2 + 0.03, cz = depth / 2;
    const hw = w / 2 + 0.015, hh = h / 2 + 0.015, hd = d / 2 + 0.015;
    const bars = [
      [cx, cy + hh, cz - hd, w + 0.04, t, t],
      [cx, cy + hh, cz + hd, w + 0.04, t, t],
      [cx - hw, cy + hh, cz, t, t, d + 0.04],
      [cx + hw, cy + hh, cz, t, t, d + 0.04],
      [cx, cy - hh, cz - hd, w + 0.04, t, t],
      [cx, cy - hh, cz + hd, w + 0.04, t, t],
      [cx - hw, cy - hh, cz, t, t, d + 0.04],
      [cx + hw, cy - hh, cz, t, t, d + 0.04],
      [cx - hw, cy, cz - hd, t, h + 0.03, t],
      [cx + hw, cy, cz - hd, t, h + 0.03, t],
      [cx - hw, cy, cz + hd, t, h + 0.03, t],
      [cx + hw, cy, cz + hd, t, h + 0.03, t],
    ];
    const mesh = new THREE.InstancedMesh(cube, role === 'target' ? targetFrameMaterial : blockerFrameMaterial, bars.length);
    const matrix = new THREE.Matrix4(), quaternion = new THREE.Quaternion();
    bars.forEach(([x, y, z, sx, sy, sz], i) => {
      matrix.compose(new THREE.Vector3(x, y, z), quaternion, new THREE.Vector3(sx, sy, sz));
      mesh.setMatrixAt(i, matrix);
    });
    return mesh;
  }

  function boxSignature(box) {
    return `${box.width}/${box.depth}/${box.role}/${box.color || ''}/${resolvePriorityTier(box)}`;
  }

  function makeBox(box) {
    const group = new THREE.Group();
    group.userData.id = box.id;
    const w = box.width - .1, d = box.depth - .1, h = .82;
    const colorKey = typeof box.color === 'string' ? box.color.trim().toLowerCase() : '';
    const material = cargoMaterials[colorKey] || neutralMaterial;
    const body = new THREE.Mesh(cube, material);
    body.position.set(box.width / 2, h / 2 + .03, box.depth / 2);
    body.scale.set(w, h, d);
    body.castShadow = true; body.receiveShadow = true;
    group.add(body);
    const baseOutlineMaterial = box.role === 'target' ? targetOutlineMaterial
      : box.role === 'blocker' ? blockerOutlineMaterial : edgeMaterial;
    const outline = new THREE.LineSegments(edges, baseOutlineMaterial);
    outline.position.copy(body.position); outline.scale.copy(body.scale);
    if (box.role === 'blocker') outline.computeLineDistances();
    group.add(outline);
    const roleFrame = makeRoleFrame(w, h, d, box.width, box.depth, box.role);
    if (roleFrame) group.add(roleFrame);
    const pieces = [];
    for (let x = .16; x < w; x += .16) {
      for (const z of [.045, box.depth - .045]) pieces.push([x + .05, h / 2 + .03, z, .035, h - .08, .035]);
      pieces.push([x + .05, h + .035, box.depth / 2, .035, .025, d]);
    }
    for (let z = .16; z < d; z += .16)
      for (const x of [.045, box.width - .045]) pieces.push([x, h / 2 + .03, z + .05, .035, h - .08, .035]);
    const ribs = new THREE.InstancedMesh(cube, material, pieces.length);
    const matrix = new THREE.Matrix4(), quaternion = new THREE.Quaternion();
    pieces.forEach(([x, y, z, sx, sy, sz], i) => {
      matrix.compose(new THREE.Vector3(x, y, z), quaternion, new THREE.Vector3(sx, sy, sz));
      ribs.setMatrixAt(i, matrix);
    });
    ribs.castShadow = true;
    group.add(ribs);
    const posts = new THREE.InstancedMesh(cube, metal, 4);
    let postIndex = 0;
    for (const x of [.06, box.width - .06]) for (const z of [.06, box.depth - .06]) {
      matrix.compose(new THREE.Vector3(x, h / 2 + .03, z), quaternion, new THREE.Vector3(.055, h, .055));
      posts.setMatrixAt(postIndex++, matrix);
    }
    group.add(posts);
    const badge = makeNameplate(box.id, badgeColorsForBox(box, palette));
    const initSpec = topNameplateSpec(box, [box]);
    badge.mesh.position.set(initSpec.x, initSpec.y, initSpec.z);
    badge.mesh.rotation.set(initSpec.rotX, 0, initSpec.rotZ);
    badge.mesh.scale.set(initSpec.length, initSpec.width, 1);
    group.add(badge.mesh);
    scene.add(group);
    return {
      group, body, outline, baseOutlineMaterial, roleFrame, ribs, posts,
      nameplate: badge.mesh, badge, role: box.role, box,
      displayWidth: box.width, displayDepth: box.depth,
      signature: boxSignature(box),
    };
  }
  function removeBox(model) {
    scene.remove(model.group);
    model.ribs.dispose(); model.posts.dispose();
    model.roleFrame?.dispose();
    model.badge.texture.dispose(); model.badge.material.dispose();
  }
  function clearDragVisuals() {
    dropPreview.visible = false;
    delete canvas.dataset.dragging;
    delete canvas.dataset.dropValid;
    delete canvas.dataset.dropTarget;
  }
  function syncSelection() {
    models.forEach((model, id) => {
      model.group.visible = isolated === null || id === isolated;
      model.outline.material = id === selected ? selectedMaterial : id === motionId ? motionMaterial : model.baseOutlineMaterial;
    });
    syncNameplates();
    invalidate();
  }
  function setMotion(spec) {
    if (!spec || !spec.to) {
      motionId = '';
      canvas.dataset.motionId = '';
      motionLine.visible = false;
      if (!dragInteraction.isDragging()) {
        dropPreview.visible = false;
      }
      syncSelection();
      return;
    }
    motionId = spec.id || spec.to.id || '';
    canvas.dataset.motionId = motionId;
    const shownTo = transpose ? transposeBox(spec.to) : spec.to;
    dropPreview.position.set(shownTo.x + (shownTo.width || 1) / 2, (shownTo.z ?? 0) + .44, shownTo.y + (shownTo.depth || 1) / 2);
    dropPreview.scale.set(shownTo.width || 1, .88, shownTo.depth || 1);
    dropFillMesh.material = validDropFill;
    dropEdgeMesh.material = validDropEdge;
    dropPreview.visible = true;
    if (spec.from) {
      const ceiling = Number.isFinite(spec.ceiling) ? spec.ceiling : Math.max(...areas.map(a => a.levels)) + .6;
      const samples = motionPath(spec.from, spec.to, ceiling, 24).map(pt => {
        const shown = transpose
          ? transposeBox({x: pt.x, y: pt.y, z: pt.z, width: spec.to.width || 1, depth: spec.to.depth || 1})
          : {x: pt.x, y: pt.y, z: pt.z, width: spec.to.width || 1, depth: spec.to.depth || 1};
        return new THREE.Vector3(
          shown.x + (shown.width || 1) / 2,
          (shown.z ?? 0) + .44,
          shown.y + (shown.depth || 1) / 2,
        );
      });
      motionLineGeometry.setFromPoints(samples);
      motionLine.visible = samples.length >= 2;
    } else {
      motionLine.visible = false;
    }
    syncSelection();
  }
  function setBoxes(boxes) {
    if (transpose) boxes = boxes.map(transposeBox);
    const ids = new Set(boxes.map(box => box.id));
    models.forEach((model, id) => { if (!ids.has(id)) { removeBox(model); models.delete(id); } });
    boxes.forEach(box => {
      let model = models.get(box.id);
      if (model && model.signature !== boxSignature(box)) {
        removeBox(model); models.delete(box.id); model = null;
      }
      if (!model) { model = makeBox(box); models.set(box.id, model); }
      model.box = box;
      model.group.position.set(box.x, box.z, box.y);
    });
    canvas.dataset.boxCount = String(boxes.length);
    syncSelection();
    if (!fitted) { fit(); fitted = true; }
  }
  function fitPoints(id) {
    if (id && typeof id === 'object' && Array.isArray(id.points)) {
      return id.points;
    }
    const points = [];
    if (id === '@location' && locationMarker.visible) {
      const box = new THREE.Box3().expandByObject(locationMarker);
      for (const x of [box.min.x, box.max.x]) for (const y of [box.min.y, box.max.y]) for (const z of [box.min.z, box.max.z])
        points.push({x, y, z});
      return points;
    }
    if (id && models.has(id)) {
      const box = new THREE.Box3().expandByObject(models.get(id).body);
      for (const x of [box.min.x, box.max.x]) for (const y of [box.min.y, box.max.y]) for (const z of [box.min.z, box.max.z])
        points.push({x, y, z});
      return points;
    }
    const yardAreaId = (areas.find(a => a.id === 'yard') || areas[0])?.id;
    const hasBox = areaId => [...models.values()].some(m => m.group.visible && m.box?.area === areaId);
    const activeAreas = showVolumes ? areas : areas.filter(a => a.id === yardAreaId || hasBox(a.id));
    (activeAreas.length ? activeAreas : areas).forEach(a => {
      for (const x of [a.x, a.x + a.columns]) {
        for (const z of [a.y, a.y + a.rows + .35]) {
          points.push({x, y: 0, z});
          points.push({x, y: .4, z});
        }
      }
    });
    models.forEach(model => {
      if (!model.group.visible) return;
      const gx = model.group.position.x, gy = model.group.position.y, gz = model.group.position.z;
      for (const x of [gx, gx + model.displayWidth]) {
        for (const y of [gy, gy + 1.05]) {
          for (const z of [gz, gz + model.displayDepth]) {
            points.push({x, y, z});
          }
        }
      }
    });
    if (!points.length) {
      points.push({x: 0, y: 0, z: 0}, {x: 1, y: 1, z: 1});
    }
    return points;
  }
  function fitTo(rawPoints, {padding = FOCUS_FIT_PADDING, logical = false} = {}) {
    if (!Array.isArray(rawPoints) || !rawPoints.length) return;
    const points = logical
      ? rawPoints.map(p => {
          const t = transpose ? transposePoint({x: p.x, y: p.z, z: p.y}) : {x: p.x, y: p.z, z: p.y};
          return {x: t.x, y: p.y, z: t.z};
        })
      : rawPoints;
    const box = new THREE.Box3();
    points.forEach(p => box.expandByPoint(new THREE.Vector3(p.x, p.y, p.z)));
    const center = box.getCenter(new THREE.Vector3());
    const frontVec = VIEW_DIRECTIONS[DEFAULT_CAMERA_VIEW] || VIEW_DIRECTIONS.front;
    const direction = camera.position.clone().sub(orbit.target).normalize();
    if (direction.lengthSq() < 1e-6) direction.set(frontVec.x, frontVec.y, frontVec.z).normalize();
    const right = new THREE.Vector3().crossVectors(camera.up, direction).normalize();
    const up = new THREE.Vector3().crossVectors(direction, right).normalize();
    const distance = computeFitDistance(
      points,
      center,
      direction,
      right,
      up,
      camera.fov,
      camera.aspect,
      {padding, minDistance: orbit.minDistance, near: camera.near},
    );
    orbit.target.copy(center);
    camera.position.copy(center).addScaledVector(direction, distance);
    canvas.dataset.fitDistance = String(Number(distance.toFixed(3)));
    orbit.update(); cameraChanged();
  }
  function fitBoxBounds(boxSpec) {
    if (!boxSpec) return [];
    const shown = transpose ? transposeBox(boxSpec) : boxSpec;
    const w = shown.width || 1, d = shown.depth || 1, h = 1.05;
    const pts = [];
    for (const x of [shown.x, shown.x + w]) {
      for (const y of [shown.z ?? 0, (shown.z ?? 0) + h]) {
        for (const z of [shown.y, shown.y + d]) {
          pts.push({x, y, z});
        }
      }
    }
    return pts;
  }
  function fit(id) {
    const points = fitPoints(id);
    const box = new THREE.Box3();
    points.forEach(p => box.expandByPoint(new THREE.Vector3(p.x, p.y, p.z)));
    const center = box.getCenter(new THREE.Vector3());
    const frontVec = VIEW_DIRECTIONS[DEFAULT_CAMERA_VIEW] || VIEW_DIRECTIONS.front;
    const direction = id
      ? camera.position.clone().sub(orbit.target).normalize()
      : new THREE.Vector3(frontVec.x, frontVec.y, frontVec.z).normalize();
    if (direction.lengthSq() < 1e-6) direction.set(frontVec.x, frontVec.y, frontVec.z).normalize();
    const right = new THREE.Vector3().crossVectors(camera.up, direction).normalize();
    const up = new THREE.Vector3().crossVectors(direction, right).normalize();
    const distance = computeFitDistance(
      points,
      center,
      direction,
      right,
      up,
      camera.fov,
      camera.aspect,
      {padding: id ? FOCUS_FIT_PADDING : DEFAULT_FIT_PADDING, minDistance: orbit.minDistance, near: camera.near},
    );
    orbit.target.copy(center);
    camera.position.copy(center).addScaledVector(direction, distance);
    if (!id) canvas.dataset.cameraView = DEFAULT_CAMERA_VIEW;
    canvas.dataset.fitDistance = String(Number(distance.toFixed(3)));
    orbit.update(); cameraChanged();
  }
  function view(name) {
    const preset = VIEW_DIRECTIONS[name] || VIEW_DIRECTIONS.iso;
    const direction = new THREE.Vector3(preset.x, preset.y, preset.z).normalize();
    const distance = camera.position.distanceTo(orbit.target);
    camera.position.copy(orbit.target).addScaledVector(direction, distance);
    canvas.dataset.cameraView = VIEW_DIRECTIONS[name] ? name : 'iso';
    orbit.update(); cameraChanged();
  }
  function zoom(factor) {
    if (!Number.isFinite(factor) || factor <= 0) return;
    const offset = camera.position.clone().sub(orbit.target);
    offset.setLength(THREE.MathUtils.clamp(offset.length() / factor, orbit.minDistance, orbit.maxDistance));
    camera.position.copy(orbit.target).add(offset); orbit.update(); cameraChanged();
  }
  const raycaster = new THREE.Raycaster();
  function setRayFromEvent(event) {
    const rect = canvas.getBoundingClientRect();
    if (!rect.width || !rect.height) return false;
    raycaster.setFromCamera(new THREE.Vector2((event.clientX - rect.left) / rect.width * 2 - 1,
      -(event.clientY - rect.top) / rect.height * 2 + 1), camera);
    return true;
  }
  function pickHitBox(event) {
    if (!setRayFromEvent(event)) return null;
    const visible = [...models.values()].filter(m => m.group.visible).map(m => m.body);
    const hit = raycaster.intersectObjects(visible, false)[0];
    return hit ? hit.object.parent.userData.id : null;
  }
  function updateDragPreview(id, event) {
    if (!setRayFromEvent(event)) return undefined;
    if (!raycaster.ray.intersectPlane(groundPlane, planeHit)) return undefined;
    const model = models.get(id);
    if (!model) return undefined;
    const logical = logicalPoint(planeHit);
    const target = onResolveDrop?.(id, logical) || null;
    if (target) {
      const shown = transpose
        ? transposeBox({x: target.x, y: target.y, z: target.visualZ, width: target.width, depth: target.depth})
        : {x: target.x, y: target.y, z: target.visualZ, width: target.width, depth: target.depth};
      model.group.position.set(shown.x, shown.z, shown.y);
      dropPreview.position.set(shown.x + shown.width / 2, shown.z + .44, shown.y + shown.depth / 2);
      dropPreview.scale.set(shown.width, .88, shown.depth);
      dropFillMesh.material = target.valid ? validDropFill : invalidDropFill;
      dropEdgeMesh.material = target.valid ? validDropEdge : dangerMaterial;
      model.outline.material = target.valid ? selectedMaterial : dangerMaterial;
      dropPreview.visible = true;
      canvas.dataset.dropValid = String(target.valid);
      canvas.dataset.dropTarget = JSON.stringify(target.placement);
    } else {
      model.group.position.set(planeHit.x - model.displayWidth / 2, .15, planeHit.z - model.displayDepth / 2);
      model.outline.material = dangerMaterial;
      dropPreview.visible = false;
      canvas.dataset.dropValid = 'false';
      canvas.dataset.dropTarget = '';
    }
    invalidate();
    return target;
  }
  const dragInteraction = bindDragInteraction({
    canvas,
    win: window,
    orbit,
    isEditable: () => editable,
    getSelected: () => selected,
    pickHitBox,
    getStartPosition: id => models.get(id).group.position.clone(),
    restorePosition: (id, pos) => {
      const model = models.get(id);
      if (model) model.group.position.copy(pos);
    },
    resetOutline: id => {
      const model = models.get(id);
      if (model) model.outline.material = id === selected ? selectedMaterial : model.baseOutlineMaterial;
    },
    updatePreview: updateDragPreview,
    clearDragVisuals,
    onSelect,
    onDrop,
    invalidate,
  });
  function keyDown(event) {
    const directions = {ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, 1], ArrowDown: [0, -1]};
    if (directions[event.key]) {
      event.preventDefault();
      const [x, y] = directions[event.key];
      if (event.shiftKey) {
        const offset = camera.position.clone().sub(orbit.target);
        const spherical = new THREE.Spherical().setFromVector3(offset);
        spherical.theta += x * .12; spherical.phi = THREE.MathUtils.clamp(spherical.phi - y * .12, .01, orbit.maxPolarAngle);
        camera.position.copy(orbit.target).add(new THREE.Vector3().setFromSpherical(spherical));
      } else {
        const step = camera.position.distanceTo(orbit.target) * .04;
        const delta = new THREE.Vector3().setFromMatrixColumn(camera.matrix, 0).multiplyScalar(x * step)
          .addScaledVector(new THREE.Vector3().setFromMatrixColumn(camera.matrix, 1), y * step);
        camera.position.add(delta); orbit.target.add(delta);
      }
      orbit.update(); cameraChanged();
    } else if (['+', '=', '-', 'Home'].includes(event.key)) {
      event.preventDefault();
      if (event.key === 'Home') fit(); else zoom(event.key === '-' ? 1 / 1.2 : 1.2);
    } else if ([' ', '[', ']'].includes(event.key)) {
      event.preventDefault();
      onPlaybackKey?.(event.key);
    }
  }
  function resize() {
    const rect = canvas.getBoundingClientRect();
    if (!rect.width || !rect.height || disposed || failed || (rect.width === width && rect.height === height)) return;
    const wasHidden = width === 0 && height === 0;
    width = rect.width; height = rect.height;
    renderer.setSize(rect.width, rect.height, false);
    camera.aspect = rect.width / rect.height; camera.updateProjectionMatrix();
    if (wasHidden && fitted) fit();
    // Resizing clears the drawing buffer; refill it before the browser paints.
    cancelAnimationFrame(raf); raf = 0;
    draw();
  }
  function contextLost(event) {
    event.preventDefault(); failed = true;
    cancelAnimationFrame(raf); raf = 0;
    onError?.(new Error('WebGL context lost'));
  }
  canvas.addEventListener('keydown', keyDown);
  canvas.addEventListener('webglcontextlost', contextLost);
  const observer = new ResizeObserver(resize);
  observer.observe(canvas);
  resize();
  return {
    setBoxes,
    select(id) { selected = id; syncSelection(); },
    setMode(mode) { orbit.mouseButtons.LEFT = mode === 'pan' ? THREE.MOUSE.PAN : THREE.MOUSE.ROTATE; },
    setEditable(value) { editable = !!value; if (!editable) dragInteraction.endDrag(false); syncSelection(); },
    selectLocation(position) {
      const area = areas.find(area => area.id === position?.area);
      locationMarker.visible = Boolean(area);
      if (area) locationMarker.position.set(area.x + (transpose ? position.row : position.column) - .5,
        position.level - .5, area.y + (transpose ? position.column : position.row) - .5);
      canvas.dataset.locationMarker = area ? JSON.stringify(position) : '';
      invalidate();
    },
    fitLocation() { fit('@location'); },
    fit, fitTo, fitBoxBounds, setMotion, view, zoom,
    isolate(id) { isolated = id || null; syncSelection(); },
    resize,
    dispose() {
      if (disposed) return;
      disposed = true; dragInteraction.dispose(); cancelAnimationFrame(raf); observer.disconnect();
      canvas.removeEventListener('keydown', keyDown);
      canvas.removeEventListener('webglcontextlost', contextLost);
      orbit.dispose();
      models.forEach(removeBox); models.clear();
      resources.forEach(resource => resource.dispose());
      light.shadow.dispose(); renderer.dispose();
    },
  };
}
