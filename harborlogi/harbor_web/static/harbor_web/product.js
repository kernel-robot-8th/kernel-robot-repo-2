'use strict';
document.documentElement.classList.add('js');
const toggle = document.getElementById('nav-toggle');
const navigation = document.getElementById('navigation');
if (toggle && navigation) {
  toggle.hidden = false;
  const close = () => { toggle.setAttribute('aria-expanded', 'false'); toggle.textContent = '메뉴 열기'; document.body.classList.remove('nav-open'); };
  toggle.addEventListener('click', () => {
    const open = toggle.getAttribute('aria-expanded') !== 'true';
    toggle.setAttribute('aria-expanded', String(open));
    toggle.textContent = open ? '메뉴 닫기' : '메뉴 열기';
    document.body.classList.toggle('nav-open', open);
    if (open) navigation.querySelector('a')?.focus();
  });
  document.addEventListener('keydown', event => { if (event.key === 'Escape' && document.body.classList.contains('nav-open')) { close(); toggle.focus(); } });
}

const density = document.querySelector('[data-nav-density]');
const navTooltip = document.getElementById('nav-tooltip');
if (density && navigation && navTooltip) {
  let compact = true, active = null, hideTimer = 0;
  const desktop = matchMedia('(min-width: 901px)');
  try { compact = localStorage.getItem('harbor.nav.compact') !== 'false'; } catch (_) {}
  const setDensity = () => {
    document.body.classList.toggle('nav-compact', compact);
    density.setAttribute('aria-expanded', String(!compact));
    density.setAttribute('aria-label', compact ? '메뉴 이름 펼치기' : '메뉴 이름 접기');
  };
  const hideTip = () => {
    clearTimeout(hideTimer); navTooltip.hidden = true;
    active?.removeAttribute('aria-describedby'); active = null;
  };
  const showTip = link => {
    hideTip();
    if (!desktop.matches) return;
    active = link; navTooltip.textContent = link.dataset.navTip; navTooltip.hidden = false;
    link.setAttribute('aria-describedby', 'nav-tooltip');
    const rect = link.getBoundingClientRect(), tip = navTooltip.getBoundingClientRect();
    navTooltip.style.left = Math.max(12, Math.min(rect.right + 12, innerWidth - tip.width - 12)) + 'px';
    navTooltip.style.top = Math.max(12, Math.min(rect.top + (rect.height - tip.height) / 2, innerHeight - tip.height - 12)) + 'px';
  };
  const deferHide = () => {
    clearTimeout(hideTimer);
    hideTimer = setTimeout(() => { if (document.activeElement !== active) hideTip(); }, 120);
  };
  navigation.querySelectorAll('[data-nav-tip]').forEach(link => {
    link.removeAttribute('title');
    link.addEventListener('pointerenter', () => showTip(link));
    link.addEventListener('focus', () => showTip(link));
    link.addEventListener('pointerleave', deferHide);
    link.addEventListener('blur', deferHide);
    link.addEventListener('click', hideTip);
  });
  navTooltip.addEventListener('pointerenter', () => clearTimeout(hideTimer));
  navTooltip.addEventListener('pointerleave', deferHide);
  document.addEventListener('keydown', event => { if (event.key === 'Escape') hideTip(); });
  navigation.addEventListener('scroll', hideTip);
  window.addEventListener('resize', hideTip);
  density.addEventListener('click', () => {
    compact = !compact; setDensity(); hideTip();
    try { localStorage.setItem('harbor.nav.compact', String(compact)); } catch (_) {}
  });
  density.hidden = false; setDensity();
}
document.querySelectorAll('form[data-command]').forEach(form => {
  form.addEventListener('submit', event => {
    if (form.dataset.pending) { event.preventDefault(); return; }
    form.dataset.pending = 'true';
    const button = form.querySelector('button[type="submit"]');
    if (button) { button.dataset.label = button.textContent; button.disabled = true; button.textContent = '처리 중…'; }
  });
});
window.addEventListener('pageshow', () => {
  document.querySelectorAll('form[data-pending]').forEach(form => {
    delete form.dataset.pending;
    const button = form.querySelector('button[data-label]');
    if (button) { button.disabled = false; button.textContent = button.dataset.label; delete button.dataset.label; }
  });
});

document.querySelectorAll('[data-yard-preview]').forEach(preview => {
  const controls = [...preview.querySelectorAll('[data-yard-stage]')];
  const panels = [...preview.querySelectorAll('[data-yard-panel]')];
  const status = preview.querySelector('[data-yard-status]');
  if (!controls.length || !panels.length) return;
  const select = selected => {
    controls.forEach(button => button.setAttribute('aria-pressed', String(button === selected)));
    panels.forEach(panel => { panel.hidden = panel.id !== selected.getAttribute('aria-controls'); });
    if (status) status.textContent = selected.dataset.yardStage === '0'
      ? '계획 기준 관측 · 저장된 원본 배치입니다.'
      : `${selected.textContent.trim()} 후 예상 배치 · 실제 관측·실행 상태는 바뀌지 않습니다.`;
    preview.dispatchEvent(new CustomEvent('yardstagechange', {detail: {index: selected.dataset.yardStage}}));
  };
  controls.forEach(button => {
    button.hidden = false;
    button.addEventListener('click', () => select(button));
  });
  select(controls[0]);
});

document.querySelectorAll('[data-yard-preview]').forEach(preview => {
  const controls = [...preview.querySelectorAll('[data-yard-level]')];
  const layers = [...preview.querySelectorAll('[data-yard-layer]')];
  if (!controls.length || !layers.length) return;
  const areaOf = element => element.closest('[data-yard-grid]').dataset.gridArea;
  const selectLevel = (level, area) => {
    controls.filter(button => areaOf(button) === area).forEach(button => button.setAttribute('aria-pressed', String(button.dataset.yardLevel === level)));
    layers.filter(layer => areaOf(layer) === area).forEach(layer => { layer.hidden = layer.dataset.yardLayer !== level; });
    [...preview.querySelectorAll('[data-layer-status]')].filter(status => areaOf(status) === area).forEach(status => {
      status.textContent = `현재 ${level}층 · 칸의 행·열 주소와 원본 관측은 유지됩니다. 1층은 바닥층입니다.`;
    });
  };
  controls.forEach(button => {
    button.hidden = false;
    button.addEventListener('click', () => selectLevel(button.dataset.yardLevel, areaOf(button)));
  });
  new Set(controls.map(areaOf)).forEach(area => selectLevel('1', area));
});
