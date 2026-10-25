'use strict';
document.documentElement.classList.add('js');
const toggle = document.getElementById('nav-toggle');
const navigation = document.getElementById('navigation');
if (toggle && navigation) {
  toggle.hidden = false;
  const close = () => { toggle.setAttribute('aria-expanded', 'false'); document.body.classList.remove('nav-open'); };
  toggle.addEventListener('click', () => {
    const open = toggle.getAttribute('aria-expanded') !== 'true';
    toggle.setAttribute('aria-expanded', String(open));
    document.body.classList.toggle('nav-open', open);
    if (open) navigation.querySelector('a')?.focus();
  });
  document.addEventListener('keydown', event => { if (event.key === 'Escape' && document.body.classList.contains('nav-open')) { close(); toggle.focus(); } });
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
