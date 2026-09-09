document.querySelectorAll('[data-promote]').forEach(button => {
  button.addEventListener('click', () => document.getElementById(`promote-${button.dataset.promote}`).showModal());
});
document.querySelectorAll('[data-close-dialog]').forEach(button => {
  button.addEventListener('click', () => button.closest('dialog').close());
});
document.querySelector('[data-pending-callsign]')?.showModal();
document.getElementById('personnel-search')?.addEventListener('input', event => {
  const query = event.target.value.toLowerCase().trim();
  let count = 0;
  document.querySelectorAll('[data-personnel-row]').forEach(row => {
    const text = [...row.cells].slice(0, 4).map(cell => cell.textContent).join(' ').toLowerCase();
    row.hidden = !text.includes(query);
    if (!row.hidden) count++;
  });
  document.getElementById('personnel-empty').hidden = count > 0;
});
const clock = document.querySelector('[data-mdt-clock]');
function updateTerminalClock() {
  if (clock) clock.textContent = new Date().toLocaleString([], {month:'short',day:'2-digit',hour:'2-digit',minute:'2-digit'});
}
updateTerminalClock();
setInterval(updateTerminalClock, 30000);
const accountToggle = document.querySelector('[data-account-toggle]');
const accountDropdown = document.getElementById('mdt-account-dropdown');
function setAccountMenu(open, restoreFocus = false) {
  if (!accountToggle || !accountDropdown) return;
  accountToggle.setAttribute('aria-expanded', String(open));
  accountDropdown.hidden = !open;
  if (restoreFocus) accountToggle.focus();
}
accountToggle?.addEventListener('click', () => setAccountMenu(accountDropdown.hidden));
accountToggle?.addEventListener('keydown', event => {
  if (event.key === 'ArrowDown') {
    event.preventDefault();
    setAccountMenu(true);
    accountDropdown.querySelector('a')?.focus();
  }
});
document.addEventListener('click', event => {
  if (!event.target.closest('.mdt-account-menu')) setAccountMenu(false);
});
document.addEventListener('keydown', event => {
  if (event.key === 'Escape' && accountDropdown && !accountDropdown.hidden) setAccountMenu(false, true);
});
document.querySelector('.mdt-account-menu')?.addEventListener('focusout', event => {
  if (!event.currentTarget.contains(event.relatedTarget)) setAccountMenu(false);
});
accountDropdown?.querySelector('a')?.addEventListener('click', () => setAccountMenu(false, true));
window.addEventListener('blur', () => setAccountMenu(false));
document.querySelectorAll('form[action^="/hub/"]').forEach(form => {
  const input = document.createElement('input');
  input.type = 'hidden'; input.name = 'mdt_return'; input.value = '1'; form.append(input);
});
document.querySelector('[data-dashboard-frame]')?.addEventListener('load', event => {
  const frame = event.target;
  try {
    if (frame.contentWindow.location.pathname === '/hub') {
      window.location.href = '/mdt';
      return;
    }
    if (frame.contentWindow.location.pathname === '/supervisor/login') {
      window.location.href = '/mdt/login';
      return;
    }
    const doc = frame.contentDocument;
    // Shared panels keep their website links in the Hub, but not inside the MDT.
    doc.querySelectorAll('a[href]').forEach(link => {
      const destination = new URL(link.href, window.location.origin);
      if (destination.origin !== window.location.origin) return;
      if (destination.pathname === '/') link.remove();
      if (destination.pathname === '/hub') link.href = '/mdt';
      if (destination.pathname === '/supervisor/login') {
        link.href = '/mdt/login';
        link.target = '_top';
      }
    });
    if (!doc.querySelector('[data-mdt-theme]')) {
      const style = doc.createElement('link'); style.rel = 'stylesheet';
      style.href = document.querySelector('link[data-mdt-theme]').href;
      style.dataset.mdtTheme = ''; doc.head.append(style);
      doc.body.classList.add('mdt-embedded');
    }
  } catch (_) { /* External links retain their own navigation. */ }
});
