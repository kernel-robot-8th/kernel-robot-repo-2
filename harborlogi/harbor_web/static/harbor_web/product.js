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
