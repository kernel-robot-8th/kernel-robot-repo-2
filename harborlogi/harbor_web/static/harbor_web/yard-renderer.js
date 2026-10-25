import * as THREE from './vendor/three-0.186.1/three.module.js';
import {OrbitControls} from './vendor/three-0.186.1/OrbitControls.js';
import {TransformControls} from './vendor/three-0.186.1/TransformControls.js';

export function createRenderer(canvas, areas, {onSelect, onTransform, onError, colors = {}, showVolumes = false} = {}) {
  const context = canvas.getContext('webgl2', {antialias: true, preserveDrawingBuffer: true});
  if (!context) throw new Error('WebGL2 is unavailable');
  const palette = {target: '#112c3f', blocker: '#fe6d08', stored: '#2cbbb7',
    line: '#d7e3e9', text: '#17364b', muted: '#506878', floor: '#ffffff',
    white: '#ffffff', canvas: '#f4f8fb', ...Object.fromEntries(Object.entries(colors).filter(([, v]) => v))};
  const renderer = new THREE.WebGLRenderer({canvas, context, antialias: true, preserveDrawingBuffer: true});
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(palette.canvas);
  const camera = new THREE.PerspectiveCamera(40, 1, .05, 1000);
  camera.position.set(12, 13, 17);
  const orbit = new OrbitControls(camera, canvas);
  orbit.enableDamping = true;
  orbit.dampingFactor = .15;
  orbit.minDistance = .4;
  orbit.maxDistance = 300;
  orbit.maxPolarAngle = Math.PI * .49;
  const transform = new TransformControls(camera, canvas);
  transform.setMode('translate');
  transform.setTranslationSnap(1);
  transform.setSize(.85);
  scene.add(transform.getHelper());
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
  const materials = Object.fromEntries(['target', 'blocker', 'stored'].map(role =>
    [role, keep(new THREE.MeshStandardMaterial({color: palette[role], roughness: .6, metalness: .25}))]));
  const metal = keep(new THREE.MeshStandardMaterial({color: palette.muted, roughness: .5, metalness: .55}));
  const edgeMaterial = keep(new THREE.LineBasicMaterial({color: palette.text, transparent: true, opacity: .55}));
  const selectedMaterial = keep(new THREE.LineBasicMaterial({color: palette.blocker}));
  const floorMaterial = keep(new THREE.MeshStandardMaterial({color: palette.floor, roughness: 1}));
  const gridMaterial = keep(new THREE.LineBasicMaterial({color: palette.line}));
  const models = new Map();
  const activePointers = new Set();
  let selected = null, isolated = null, editable = false, fitted = false;
  let disposed = false, failed = false, raf = 0, pointer = null, multiPointer = false,
    draggedGizmo = false, revision = 0, renderRevision = 0;
  let width = 0, height = 0;

  function invalidate() {
    if (!disposed && !failed && !raf) raf = requestAnimationFrame(draw);
  }
  function draw() {
    raf = 0;
    if (disposed || failed) return;
    const changed = orbit.update();
    renderer.render(scene, camera);
    canvas.dataset.renderRevision = String(++renderRevision);
    if (changed) invalidate();
  }
  function cameraChanged() {
    canvas.dataset.viewRevision = String(++revision);
    invalidate();
  }
  orbit.addEventListener('change', cameraChanged);
  transform.addEventListener('change', invalidate);
  transform.addEventListener('dragging-changed', event => {
    orbit.enabled = !event.value;
    if (event.value) draggedGizmo = true;
  });
  transform.addEventListener('mouseUp', () => {
    if (transform.object && editable) {
      const {x, y, z} = transform.object.position;
      onTransform?.(transform.object.userData.id, {x, y: z, z: y});
    }
  });

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

  function makeBox(box) {
    const group = new THREE.Group();
    group.userData.id = box.id;
    const w = box.width - .1, d = box.depth - .1, h = .82;
    const material = materials[box.role] || materials.stored;
    const body = new THREE.Mesh(cube, material);
    body.position.set(box.width / 2, h / 2 + .03, box.depth / 2);
    body.scale.set(w, h, d);
    body.castShadow = true; body.receiveShadow = true;
    group.add(body);
    const outline = new THREE.LineSegments(edges, edgeMaterial);
    outline.position.copy(body.position); outline.scale.copy(body.scale);
    group.add(outline);
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
    const text = label(box.id, palette.white, palette[box.role] || palette.stored);
    for (const face of ['front', 'back', 'top']) {
      const top = face === 'top';
      const sign = new THREE.Mesh(plane, text.material);
      sign.scale.set(Math.min(w * .8, 1.2), Math.min(d * .5, .3), 1);
      sign.position.set(box.width / 2, top ? h + .055 : .46,
        top ? box.depth / 2 : face === 'front' ? box.depth - .018 : .018);
      sign.rotation.set(top ? -Math.PI / 2 : 0, face === 'back' ? Math.PI : 0, 0);
      group.add(sign);
    }
    scene.add(group);
    return {group, body, outline, ribs, posts, text, signature: `${box.width}/${box.depth}/${box.role}`};
  }
  function removeBox(model) {
    scene.remove(model.group);
    model.ribs.dispose(); model.posts.dispose();
    model.text.texture.dispose(); model.text.material.dispose();
  }
  function syncSelection() {
    transform.detach();
    models.forEach((model, id) => {
      model.group.visible = isolated === null || id === isolated;
      model.outline.material = id === selected ? selectedMaterial : edgeMaterial;
    });
    const model = models.get(selected);
    if (editable && model?.group.visible) transform.attach(model.group);
    invalidate();
  }
  function setBoxes(boxes) {
    const ids = new Set(boxes.map(box => box.id));
    transform.detach();
    models.forEach((model, id) => { if (!ids.has(id)) { removeBox(model); models.delete(id); } });
    boxes.forEach(box => {
      let model = models.get(box.id);
      if (model && model.signature !== `${box.width}/${box.depth}/${box.role}`) {
        removeBox(model); models.delete(box.id); model = null;
      }
      if (!model) { model = makeBox(box); models.set(box.id, model); }
      model.group.position.set(box.x, box.z, box.y);
    });
    canvas.dataset.boxCount = String(boxes.length);
    syncSelection();
    if (!fitted) { fit(); fitted = true; }
  }
  function bounds(id) {
    const result = new THREE.Box3();
    if (id && models.has(id)) result.expandByObject(models.get(id).group);
    else {
      areas.forEach(a => {
        result.expandByPoint(new THREE.Vector3(a.x, 0, a.y));
        result.expandByPoint(new THREE.Vector3(a.x + a.columns, .5, a.y + a.rows + .8));
      });
      models.forEach(model => { if (model.group.visible) result.expandByObject(model.group); });
    }
    if (result.isEmpty()) result.set(new THREE.Vector3(0, 0, 0), new THREE.Vector3(1, 1, 1));
    return result;
  }
  function fit(id) {
    const box = bounds(id), center = box.getCenter(new THREE.Vector3());
    const direction = camera.position.clone().sub(orbit.target).normalize();
    const right = new THREE.Vector3().crossVectors(camera.up, direction).normalize();
    const up = new THREE.Vector3().crossVectors(direction, right).normalize();
    const tanY = Math.tan(THREE.MathUtils.degToRad(camera.fov / 2));
    const tanX = tanY * camera.aspect;
    let distance = orbit.minDistance;
    // Fit each perspective corner; a bounding sphere wastes space on long yard slabs.
    for (const x of [box.min.x, box.max.x]) for (const y of [box.min.y, box.max.y])
      for (const z of [box.min.z, box.max.z]) {
        const corner = new THREE.Vector3(x, y, z).sub(center);
        const depth = corner.dot(direction);
        distance = Math.max(distance, depth + Math.abs(corner.dot(right)) * 1.12 / tanX,
          depth + Math.abs(corner.dot(up)) * 1.12 / tanY, depth + camera.near * 2);
      }
    orbit.target.copy(center);
    camera.position.copy(center).addScaledVector(direction, distance);
    orbit.update(); cameraChanged();
  }
  function view(name) {
    const direction = name === 'top' ? new THREE.Vector3(0, 1, .001)
      : name === 'front' ? new THREE.Vector3(0, .08, 1) : new THREE.Vector3(1, 1.05, 1.3);
    const distance = camera.position.distanceTo(orbit.target);
    camera.position.copy(orbit.target).addScaledVector(direction.normalize(), distance);
    orbit.update(); cameraChanged();
  }
  function zoom(factor) {
    if (!Number.isFinite(factor) || factor <= 0) return;
    const offset = camera.position.clone().sub(orbit.target);
    offset.setLength(THREE.MathUtils.clamp(offset.length() / factor, orbit.minDistance, orbit.maxDistance));
    camera.position.copy(orbit.target).add(offset); orbit.update(); cameraChanged();
  }
  const raycaster = new THREE.Raycaster();
  function pointerDown(event) {
    if (event.button !== 0) return;
    activePointers.add(event.pointerId);
    if (activePointers.size > 1) multiPointer = true;
    if (multiPointer) { pointer = null; return; }
    pointer = {x: event.clientX, y: event.clientY, id: event.pointerId, moved: false};
    draggedGizmo = transform.dragging || !!transform.axis;
  }
  function pointerMove(event) {
    if (pointer?.id === event.pointerId && Math.hypot(pointer.x - event.clientX, pointer.y - event.clientY) > 5)
      pointer.moved = true;
  }
  function pointerCancel(event) {
    activePointers.delete(event.pointerId);
    if (!activePointers.size) multiPointer = false;
    pointer = null;
  }
  function pointerUp(event) {
    activePointers.delete(event.pointerId);
    const suppressPick = multiPointer;
    if (!activePointers.size) multiPointer = false;
    if (suppressPick) { pointer = null; return; }
    if (!pointer || pointer.id !== event.pointerId) return;
    const start = pointer; pointer = null;
    if (draggedGizmo || start.moved || Math.hypot(start.x - event.clientX, start.y - event.clientY) > 5) return;
    const rect = canvas.getBoundingClientRect();
    raycaster.setFromCamera(new THREE.Vector2((event.clientX - rect.left) / rect.width * 2 - 1,
      -(event.clientY - rect.top) / rect.height * 2 + 1), camera);
    const visible = [...models.values()].filter(m => m.group.visible).map(m => m.body);
    const hit = raycaster.intersectObjects(visible, false)[0];
    onSelect?.(hit ? hit.object.parent.userData.id : null);
  }
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
    }
  }
  function resize() {
    const rect = canvas.getBoundingClientRect();
    if (!rect.width || !rect.height || disposed || failed || (rect.width === width && rect.height === height)) return;
    width = rect.width; height = rect.height;
    renderer.setSize(rect.width, rect.height, false);
    camera.aspect = rect.width / rect.height; camera.updateProjectionMatrix();
    // Resizing clears the drawing buffer; refill it before the browser paints.
    cancelAnimationFrame(raf); raf = 0;
    draw();
  }
  function contextLost(event) {
    event.preventDefault(); failed = true;
    cancelAnimationFrame(raf); raf = 0;
    onError?.(new Error('WebGL context lost'));
  }
  canvas.addEventListener('pointerdown', pointerDown);
  canvas.addEventListener('pointermove', pointerMove);
  canvas.addEventListener('pointercancel', pointerCancel);
  canvas.addEventListener('pointerup', pointerUp);
  canvas.addEventListener('keydown', keyDown);
  canvas.addEventListener('webglcontextlost', contextLost);
  const observer = new ResizeObserver(resize);
  observer.observe(canvas);
  resize();
  return {
    setBoxes,
    select(id) { selected = id; syncSelection(); },
    setMode(mode) { orbit.mouseButtons.LEFT = mode === 'pan' ? THREE.MOUSE.PAN : THREE.MOUSE.ROTATE; },
    setEditable(value) { editable = !!value; syncSelection(); },
    fit, view, zoom,
    isolate(id) { isolated = id || null; syncSelection(); },
    resize,
    dispose() {
      if (disposed) return;
      disposed = true; cancelAnimationFrame(raf); observer.disconnect();
      canvas.removeEventListener('pointerdown', pointerDown);
      canvas.removeEventListener('pointermove', pointerMove);
      canvas.removeEventListener('pointercancel', pointerCancel);
      canvas.removeEventListener('pointerup', pointerUp);
      canvas.removeEventListener('keydown', keyDown);
      canvas.removeEventListener('webglcontextlost', contextLost);
      orbit.dispose(); transform.dispose();
      models.forEach(removeBox); models.clear();
      resources.forEach(resource => resource.dispose());
      light.shadow.dispose(); renderer.dispose();
    },
  };
}
