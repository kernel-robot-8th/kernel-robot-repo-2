const SVG_NS = 'http://www.w3.org/2000/svg';
const FRAME_ROTATIONS = [0, 90, 180, 270];
const DEFAULT_ROTATION_DEG = 180;
// Yard cameras look at the cell from the conveyor side; Harbor shows the yard plan turned 180° from that.
const YARD_CAMERA_SOURCES = ['top', 'omx_overhead'];
// The side and arm S cameras show the scene as captured: never turned (an absent header means 0°, not the yard default).
const SIDE_CAMERA_SOURCES = ['side', 'omx_side'];
const ARM_S_CAMERA_SOURCES = ['arm_s_top', 'omx_arm_s_top'];

export function displayRotationDeg(header, cameraId) {
  const trimmed = typeof header === 'string' ? header.trim() : '';
  const deg = trimmed !== '' ? Number(trimmed) : NaN;
  const valid = Boolean(trimmed) && FRAME_ROTATIONS.includes(deg);
  if (SIDE_CAMERA_SOURCES.includes(cameraId) || ARM_S_CAMERA_SOURCES.includes(cameraId)) return valid ? deg : 0;
  if (!valid) return DEFAULT_ROTATION_DEG;
  return YARD_CAMERA_SOURCES.includes(cameraId) ? (deg + 180) % 360 : deg;
}

function parseCapturedAt(capturedAt) {
  if (!capturedAt) return null;
  const text = String(capturedAt).trim();
  if (!text) return null;
  let date;
  if (/^\d+(\.\d+)?$/.test(text)) {
    const numeric = Number(text);
    date = new Date(numeric < 1e12 ? numeric * 1000 : numeric);
  } else {
    date = new Date(text);
  }
  return Number.isNaN(date.getTime()) ? null : date;
}

function decodeImage(url) {
  if (typeof Image !== 'function') return Promise.resolve(true);
  const preloader = new Image();
  preloader.src = url;
  if (typeof preloader.decode === 'function') {
    return preloader.decode().then(() => true, () => false);
  }
  return new Promise(resolve => {
    preloader.onload = () => resolve(true);
    preloader.onerror = () => resolve(false);
  });
}

export function pollDelay(status, hidden) {
  return hidden ? null : status === 200 ? 1000 : 3000;
}

export function frameStatus(status, capturedAt, rectified = null) {
  if (status === 404) return '그림이 오래됨';
  if (status !== 200) return 'ROSY 연결 안 됨';
  const modePrefix = rectified === 'map-overlay' ? '실제 환경 + 좌표' : rectified === 'map' ? '버드뷰 평면' : '';
  const captured = parseCapturedAt(capturedAt);
  if (!captured) return modePrefix;
  const time = [captured.getHours(), captured.getMinutes(), captured.getSeconds()]
    .map(value => String(value).padStart(2, '0')).join(':');
  return modePrefix ? `${modePrefix} · 찍은 시각 ${time}` : '찍은 시각 ' + time;
}

export function emptyFrameMessage(status, cameraId = '') {
  if (status !== 404) return '';
  const name = ARM_S_CAMERA_SOURCES.includes(cameraId)
    ? 'S암 카메라'
    : YARD_CAMERA_SOURCES.includes(cameraId)
      ? '야드 카메라'
      : SIDE_CAMERA_SOURCES.includes(cameraId)
        ? '사이드 카메라'
        : '카메라';
  return `${name} 영상 없음 (ROSY 송출 확인)`;
}

export function resolveRotationDeg(header) {
  const trimmed = typeof header === 'string' ? header.trim() : '';
  const deg = trimmed !== '' ? Number(trimmed) : NaN;
  return trimmed && FRAME_ROTATIONS.includes(deg) ? deg : DEFAULT_ROTATION_DEG;
}

function applyRotation(image, header, cameraId) {
  // An absent or invalid header keeps the CSS default (rotate(180deg)).
  const deg = displayRotationDeg(header, cameraId);
  image.style.transform = deg === DEFAULT_ROTATION_DEG ? '' : `rotate(${deg}deg)`;
  return deg;
}

export function rotatedImageSize(sourceSize, rotationDeg = DEFAULT_ROTATION_DEG) {
  const [srcW, srcH] = sourceSize;
  const deg = FRAME_ROTATIONS.includes(rotationDeg) ? rotationDeg : DEFAULT_ROTATION_DEG;
  return deg === 90 || deg === 270 ? [srcH, srcW] : [srcW, srcH];
}

export function projectPoint(point, sourceSize, displaySize, rotationDeg = DEFAULT_ROTATION_DEG) {
  const [x, y] = point;
  const [srcW, srcH] = sourceSize;
  const [dispW, dispH] = displaySize;
  const deg = FRAME_ROTATIONS.includes(rotationDeg) ? rotationDeg : DEFAULT_ROTATION_DEG;
  let rx, ry, rotW, rotH;
  if (deg === 0) {
    rx = x; ry = y; rotW = srcW; rotH = srcH;
  } else if (deg === 90) {
    rx = srcH - y; ry = x; rotW = srcH; rotH = srcW;
  } else if (deg === 270) {
    rx = y; ry = srcW - x; rotW = srcH; rotH = srcW;
  } else {
    rx = srcW - x; ry = srcH - y; rotW = srcW; rotH = srcH;
  }
  return {
    x: (rx / rotW) * dispW,
    y: (ry / rotH) * dispH,
  };
}

export function projectRoi(roi, sourceSize, displaySize, rotationDeg = DEFAULT_ROTATION_DEG) {
  const [x1, y1, x2, y2] = roi;
  const p1 = projectPoint([x1, y1], sourceSize, displaySize, rotationDeg);
  const p2 = projectPoint([x2, y2], sourceSize, displaySize, rotationDeg);
  const x = Math.min(p1.x, p2.x);
  const y = Math.min(p1.y, p2.y);
  const width = Math.abs(p2.x - p1.x);
  const height = Math.abs(p2.y - p1.y);
  return {
    x,
    y,
    width,
    height,
    cx: x + width / 2,
    cy: y + height / 2,
  };
}

export function projectPolygon(polygon, sourceSize, displaySize, rotationDeg = DEFAULT_ROTATION_DEG) {
  const points = polygon.map(vertex => projectPoint(vertex, sourceSize, displaySize, rotationDeg));
  const count = points.length || 1;
  const cx = points.reduce((sum, pt) => sum + pt.x, 0) / count;
  const cy = points.reduce((sum, pt) => sum + pt.y, 0) / count;
  const pointsAttr = points.map(pt => `${pt.x.toFixed(1)},${pt.y.toFixed(1)}`).join(' ');
  return {points, cx, cy, pointsAttr};
}

export function extractCameraId(url) {
  if (typeof url !== 'string') return '';
  const match = url.match(/\/topview\/([^/?#]+)\/frame\/?/);
  if (!match) return '';
  try {
    return decodeURIComponent(match[1]);
  } catch (_) {
    return match[1];
  }
}

export function shouldShowOverlay({sitePixels, cameraId, viewMode, rectified, enabled, imageVisible}) {
  return Boolean(
    sitePixels
    && sitePixels.source_id
    && cameraId === sitePixels.source_id
    && viewMode === 'raw'
    && rectified === null
    && enabled
    && imageVisible,
  );
}

export function buildOverlayItems(sitePixels, displaySize, rotationDeg = DEFAULT_ROTATION_DEG) {
  if (!sitePixels || !Array.isArray(sitePixels.image) || !Array.isArray(sitePixels.points)) {
    return {slots: [], picks: [], pending: []};
  }
  const sourceSize = sitePixels.image;
  const slots = [];
  const picks = [];
  const pending = [];
  for (const entry of sitePixels.points) {
    const shortLabel = entry.short_label || entry.label || entry.site;
    const base = {
      site: entry.site,
      label: entry.label || entry.site,
      shortLabel,
      zone: entry.zone || 'yard',
    };
    if (entry.kind === 'pick') {
      if (Array.isArray(entry.point) && entry.point.length === 2) {
        picks.push({...base, point: projectPoint(entry.point, sourceSize, displaySize, rotationDeg)});
      } else {
        pending.push(base);
      }
      continue;
    }
    if (Array.isArray(entry.polygon) && entry.polygon.length >= 3) {
      slots.push({...base, polygon: projectPolygon(entry.polygon, sourceSize, displaySize, rotationDeg)});
    } else if (Array.isArray(entry.roi) && entry.roi.length === 4) {
      slots.push({...base, rect: projectRoi(entry.roi, sourceSize, displaySize, rotationDeg)});
    } else {
      pending.push(base);
    }
  }
  return {slots, picks, pending};
}

// data-topview-points is a list of overlay documents (one per camera); a single object is accepted too.
function parseSitePixels(raw) {
  if (!raw) return [];
  try {
    const parsed = JSON.parse(raw);
    const list = Array.isArray(parsed) ? parsed : [parsed];
    return list.filter(doc => doc && typeof doc === 'object' && doc.source_id);
  } catch (_) {
    return [];
  }
}

function setElementHidden(el, hidden) {
  if (!el) return;
  const flag = Boolean(hidden);
  if (typeof el.toggleAttribute === 'function') {
    el.toggleAttribute('hidden', flag);
  } else if (typeof el.setAttribute === 'function' && typeof el.removeAttribute === 'function') {
    if (flag) el.setAttribute('hidden', '');
    else el.removeAttribute('hidden');
  }
  el.hidden = flag;
}

function svgEl(tag, attrs = {}) {
  const el = document.createElementNS(SVG_NS, tag);
  for (const [key, value] of Object.entries(attrs)) {
    el.setAttribute(key, String(value));
  }
  return el;
}

function estimateLabelWidth(text) {
  let width = 10;
  for (const ch of text) {
    width += ch.codePointAt(0) >= 0x1100 ? 11.5 : 6.2;
  }
  return Math.max(26, width);
}

function addLabel(group, text, cx, cy, boundsW, boundsH) {
  const estWidth = estimateLabelWidth(text);
  const pillH = 16;
  const clampedX = Math.max(estWidth / 2 + 2, Math.min(boundsW - estWidth / 2 - 2, cx));
  const clampedY = Math.max(pillH / 2 + 2, Math.min(boundsH - pillH / 2 - 2, cy));
  group.appendChild(svgEl('rect', {
    class: 'topview-label-bg',
    x: (clampedX - estWidth / 2).toFixed(1),
    y: (clampedY - pillH / 2).toFixed(1),
    width: estWidth.toFixed(1),
    height: pillH,
    rx: 3,
  }));
  const label = svgEl('text', {
    class: 'topview-label',
    x: clampedX.toFixed(1),
    y: clampedY.toFixed(1),
    'text-anchor': 'middle',
    'dominant-baseline': 'central',
  });
  label.textContent = text;
  group.appendChild(label);
}

export function mountTopview(panel) {
  const image = panel.querySelector('[data-topview-image]');
  const emptyBox = panel.querySelector('[data-topview-empty]');
  const statusText = panel.querySelector('[data-topview-status]');
  const select = panel.querySelector('[data-topview-source]');
  const modeSelect = panel.querySelector('[data-topview-mode]');
  const orientationText = panel.querySelector('[data-topview-orientation]');
  const overlay = panel.querySelector('[data-topview-overlay]');
  const pointsControl = panel.querySelector('[data-topview-points-control]');
  const pointsToggle = panel.querySelector('[data-topview-points-toggle]');
  const pointsMeta = panel.querySelector('[data-topview-points-meta]');
  const sitePixelsList = parseSitePixels(panel.dataset?.topviewPoints);
  const pixelsFor = cameraId => sitePixelsList.find(doc => doc.source_id === cameraId) || null;
  const pendingTexts = typeof panel.querySelectorAll === 'function'
    ? [...panel.querySelectorAll('[data-topview-points-pending]')] : [];
  const defaultOrientation = orientationText?.textContent ?? '';
  let timer, request, imageUrl;
  let currentRotationDeg = DEFAULT_ROTATION_DEG;
  let currentRectified = null;
  // #141 hops to the next camera on missing frames, unless the running job chose this one (inbound -> S arm).
  let findLiveCamera = Boolean(select?.options?.length > 1) && panel.dataset?.topviewJobCamera === undefined;
  let missingFrames = 0;

  function currentCameraId() {
    if (select) {
      const selectedOption = select.selectedOptions?.[0]
        || (select.options ? [...select.options].find(opt => opt.value === select.value) : null);
      if (selectedOption?.dataset?.cameraId) return selectedOption.dataset.cameraId;
      return extractCameraId(select.value);
    }
    return panel.dataset?.topviewCamera || extractCameraId(panel.dataset?.topviewUrl || '');
  }

  function currentViewMode() {
    return modeSelect?.value === 'raw' ? 'raw' : 'auto';
  }

  function requestUrl() {
    const base = select?.value || panel.dataset.topviewUrl;
    if (currentViewMode() === 'raw') {
      return `${base}${base.includes('?') ? '&' : '?'}view=raw`;
    }
    return base;
  }

  function renderOverlay() {
    const cameraId = currentCameraId();
    const sitePixels = pixelsFor(cameraId);
    const viewMode = currentViewMode();
    const controlsEligible = Boolean(
      sitePixels
      && viewMode === 'raw'
      && currentRectified === null,
    );
    const enabled = pointsToggle ? Boolean(pointsToggle.checked) : true;
    setElementHidden(pointsControl, !controlsEligible);
    setElementHidden(pointsMeta, !(controlsEligible && enabled));
    for (const text of pendingTexts) setElementHidden(text, text.dataset?.pointsSource !== cameraId);
    if (!overlay) return;
    const visible = shouldShowOverlay({
      sitePixels,
      cameraId,
      viewMode,
      rectified: currentRectified,
      enabled,
      imageVisible: !image.hidden,
    });
    if (!visible) {
      setElementHidden(overlay, true);
      if (typeof overlay.replaceChildren === 'function') overlay.replaceChildren();
      return;
    }
    if (typeof document.createElementNS !== 'function') {
      setElementHidden(overlay, false);
      return;
    }
    const [rotW, rotH] = rotatedImageSize(sitePixels.image, currentRotationDeg);
    const dispW = image.clientWidth > 0 ? image.clientWidth : rotW;
    const dispH = image.clientHeight > 0 ? image.clientHeight : rotH;
    overlay.setAttribute('viewBox', `0 0 ${dispW} ${dispH}`);
    const {slots, picks} = buildOverlayItems(sitePixels, [dispW, dispH], currentRotationDeg);
    const nodes = [];
    for (const slot of slots) {
      const group = svgEl('g', {
        class: `topview-slot topview-zone-${slot.zone}`,
        'data-site': slot.site,
      });
      if (slot.polygon) {
        group.appendChild(svgEl('polygon', {points: slot.polygon.pointsAttr}));
        addLabel(group, slot.shortLabel, slot.polygon.cx, slot.polygon.cy, dispW, dispH);
      } else {
        group.appendChild(svgEl('rect', {
          x: slot.rect.x.toFixed(1),
          y: slot.rect.y.toFixed(1),
          width: slot.rect.width.toFixed(1),
          height: slot.rect.height.toFixed(1),
          rx: 3,
        }));
        addLabel(group, slot.shortLabel, slot.rect.cx, slot.rect.cy, dispW, dispH);
      }
      const title = svgEl('title');
      title.textContent = `${slot.label} (${slot.site})`;
      group.appendChild(title);
      nodes.push(group);
    }
    for (const pick of picks) {
      const group = svgEl('g', {
        class: `topview-pick topview-zone-${pick.zone}`,
        'data-site': pick.site,
      });
      const px = pick.point.x;
      const py = pick.point.y;
      group.appendChild(svgEl('line', {x1: (px - 10).toFixed(1), y1: py.toFixed(1), x2: (px + 10).toFixed(1), y2: py.toFixed(1)}));
      group.appendChild(svgEl('line', {x1: px.toFixed(1), y1: (py - 10).toFixed(1), x2: px.toFixed(1), y2: (py + 10).toFixed(1)}));
      group.appendChild(svgEl('circle', {cx: px.toFixed(1), cy: py.toFixed(1), r: 5}));
      addLabel(group, pick.shortLabel, px, py - 16, dispW, dispH);
      const title = svgEl('title');
      title.textContent = `${pick.label} (${pick.site})`;
      group.appendChild(title);
      nodes.push(group);
    }
    overlay.replaceChildren(...nodes);
    setElementHidden(overlay, false);
  }

  function clearImage() {
    image.hidden = true;
    image.removeAttribute('src');
    if (imageUrl) URL.revokeObjectURL(imageUrl);
    imageUrl = null;
    renderOverlay();
  }

  function setStatus(text) {
    if (statusText.textContent !== text) statusText.textContent = text;
  }

  function setEmpty(text) {
    if (!emptyBox) return;
    if (emptyBox.textContent !== text) emptyBox.textContent = text;
    setElementHidden(emptyBox, !text);
  }

  function stop() {
    clearTimeout(timer);
    request?.abort();
    request = null;
  }

  async function refresh() {
    if (document.hidden) return;
    const current = new AbortController();
    request = current;
    let status = 0;
    try {
      const response = await fetch(requestUrl(), {
        cache: 'no-store', signal: current.signal, credentials: 'same-origin',
      });
      if (request !== current) return;
      status = response.status;
      const rawRectified = response.headers.get('X-Frame-Rectified');
      currentRectified = rawRectified === 'map-overlay' || rawRectified === 'map' ? rawRectified : null;
      if (response.ok && response.headers.get('Content-Type')?.split(';')[0] === 'image/jpeg') {
        const blob = await response.blob();
        if (request !== current) return;
        const nextUrl = URL.createObjectURL(blob);
        const decoded = await decodeImage(nextUrl);
        if (request !== current) {
          URL.revokeObjectURL(nextUrl);
          return;
        }
        if (!decoded) {
          URL.revokeObjectURL(nextUrl);
          status = 502;
          clearImage();
          setEmpty('');
        } else {
          const previousUrl = imageUrl;
          imageUrl = nextUrl;
          image.src = nextUrl;
          if (image.hidden) image.hidden = false;
          setEmpty('');
          currentRotationDeg = applyRotation(image, response.headers.get('X-Frame-Rotation-Deg'), currentCameraId());
          if (previousUrl) URL.revokeObjectURL(previousUrl);
          renderOverlay();
        }
      } else {
        if (status === 200) status = 502;
        clearImage();
        setEmpty(emptyFrameMessage(status, currentCameraId()));
      }
      if (orientationText && defaultOrientation) {
        const camId = currentCameraId();
        orientationText.textContent = SIDE_CAMERA_SOURCES.includes(camId)
          ? '옆 카메라 원본 방향 (컨테이너가 쌓이는 모습)'
          : ARM_S_CAMERA_SOURCES.includes(camId)
            ? 'S암 탑뷰 (Pinky 짐칸 · 컨베이어 투입 방향)'
            : currentRectified === 'map-overlay'
              ? '버드뷰 실제 환경 + 좌표 (지도 +X 오른쪽 · +Y 위)'
              : currentRectified === 'map'
                ? '버드뷰 평면 (지도 +X 오른쪽 · +Y 위)'
                : defaultOrientation;
      }
      setStatus(frameStatus(status, response.headers.get('X-Frame-Captured-At'), currentRectified));
    } catch (_) {
      if (request !== current) return;
      status = 0;
      currentRectified = null;
      clearImage();
      setEmpty('');
      setStatus(frameStatus(status));
    } finally {
      if (request === current) {
        request = null;
        if (status === 200) findLiveCamera = false;
        missingFrames = status === 404 ? missingFrames + 1 : 0;
        if (findLiveCamera && missingFrames >= 2 && select.selectedIndex < select.options.length - 1) {
          select.selectedIndex += 1;
          missingFrames = 0;
          changeSource();
        } else {
          const delay = pollDelay(status, document.hidden);
          if (delay !== null) timer = setTimeout(refresh, delay);
        }
      }
    }
  }

  function restart() {
    stop();
    refresh();
  }

  function changeSource() {
    if (modeSelect && sitePixelsList.length) {
      modeSelect.value = pixelsFor(currentCameraId()) ? 'raw' : 'auto';
    }
    currentRectified = null;
    currentRotationDeg = DEFAULT_ROTATION_DEG;
    clearImage();
    setEmpty('');
    restart();
  }

  function onSourceChange() {
    findLiveCamera = false;
    changeSource();
  }

  function onModeChange() {
    findLiveCamera = false;
    currentRectified = null;
    clearImage();
    setEmpty('');
    restart();
  }

  function onPageHide() {
    stop();
    clearImage();
    setEmpty('');
  }

  function onPageShow(event) {
    if (event.persisted) restart();
  }

  let resizeObserver = null;
  if (typeof ResizeObserver === 'function' && overlay && image) {
    try {
      resizeObserver = new ResizeObserver(renderOverlay);
      resizeObserver.observe(image);
    } catch (_) {
      resizeObserver = null;
    }
  }

  select?.addEventListener('change', onSourceChange);
  modeSelect?.addEventListener('change', onModeChange);
  pointsToggle?.addEventListener('change', renderOverlay);
  image?.addEventListener?.('load', renderOverlay);
  document.addEventListener('visibilitychange', restart);
  window.addEventListener('pagehide', onPageHide);
  window.addEventListener('pageshow', onPageShow);
  window.addEventListener('resize', renderOverlay);
  renderOverlay();
  refresh();
  return () => {
    stop();
    clearImage();
    setEmpty('');
    resizeObserver?.disconnect();
    select?.removeEventListener('change', onSourceChange);
    modeSelect?.removeEventListener('change', onModeChange);
    pointsToggle?.removeEventListener('change', renderOverlay);
    image?.removeEventListener?.('load', renderOverlay);
    document.removeEventListener('visibilitychange', restart);
    window.removeEventListener('pagehide', onPageHide);
    window.removeEventListener('pageshow', onPageShow);
    window.removeEventListener('resize', renderOverlay);
  };
}

if (typeof document !== 'undefined') {
  document.querySelectorAll('[data-topview-url]').forEach(mountTopview);
}
