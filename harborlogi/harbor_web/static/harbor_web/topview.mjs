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

export function frameStatus(status, capturedAt) {
  if (status === 404) return '그림이 오래됨';
  if (status !== 200) return 'ROSY 연결 안 됨';
  const captured = parseCapturedAt(capturedAt);
  if (!captured) return '';
  const time = [captured.getHours(), captured.getMinutes(), captured.getSeconds()]
    .map(value => String(value).padStart(2, '0')).join(':');
  return '찍은 시각 ' + time;
}

const FRAME_ROTATIONS = [0, 90, 180, 270];

function applyRotation(image, header) {
  // An absent or invalid header resets to the CSS default (rotate(180deg)).
  const trimmed = typeof header === 'string' ? header.trim() : '';
  const deg = trimmed !== '' ? Number(trimmed) : NaN;
  image.style.transform = trimmed && FRAME_ROTATIONS.includes(deg) ? `rotate(${deg}deg)` : '';
}

export function mountTopview(panel) {
  const image = panel.querySelector('[data-topview-image]');
  const statusText = panel.querySelector('[data-topview-status]');
  const select = panel.querySelector('[data-topview-source]');
  let timer, request, imageUrl;

  function clearImage() {
    image.hidden = true;
    image.removeAttribute('src');
    if (imageUrl) URL.revokeObjectURL(imageUrl);
    imageUrl = null;
  }

  function setStatus(text) {
    if (statusText.textContent !== text) statusText.textContent = text;
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
      const response = await fetch(select?.value || panel.dataset.topviewUrl, {
        cache: 'no-store', signal: current.signal, credentials: 'same-origin',
      });
      if (request !== current) return;
      status = response.status;
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
        } else {
          const previousUrl = imageUrl;
          imageUrl = nextUrl;
          image.src = nextUrl;
          if (image.hidden) image.hidden = false;
          applyRotation(image, response.headers.get('X-Frame-Rotation-Deg'));
          if (previousUrl) URL.revokeObjectURL(previousUrl);
        }
      } else {
        if (status === 200) status = 502;
        clearImage();
      }
      setStatus(frameStatus(status, response.headers.get('X-Frame-Captured-At')));
    } catch (_) {
      if (request !== current) return;
      status = 0;
      clearImage();
      setStatus(frameStatus(status));
    } finally {
      if (request === current) {
        request = null;
        const delay = pollDelay(status, document.hidden);
        if (delay !== null) timer = setTimeout(refresh, delay);
      }
    }
  }

  function restart() {
    stop();
    refresh();
  }

  function onChange() {
    clearImage();
    restart();
  }

  function onPageHide() {
    stop();
    clearImage();
  }

  function onPageShow(event) {
    if (event.persisted) restart();
  }

  select?.addEventListener('change', onChange);
  document.addEventListener('visibilitychange', restart);
  window.addEventListener('pagehide', onPageHide);
  window.addEventListener('pageshow', onPageShow);
  refresh();
  return () => {
    stop();
    clearImage();
    select?.removeEventListener('change', onChange);
    document.removeEventListener('visibilitychange', restart);
    window.removeEventListener('pagehide', onPageHide);
    window.removeEventListener('pageshow', onPageShow);
  };
}

if (typeof document !== 'undefined') {
  document.querySelectorAll('[data-topview-url]').forEach(mountTopview);
}
