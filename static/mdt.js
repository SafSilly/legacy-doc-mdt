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

// Evaluation lives above the whole terminal, preserving the selected panel.
const evaluationTrigger = document.querySelector('[data-evaluation-popup]');
if (evaluationTrigger) {
  const popup = document.createElement('dialog');
  popup.className = 'mdt-evaluation-popup';
  popup.setAttribute('aria-label', 'FTO Evaluation Form');
  document.body.append(popup);
  popup.addEventListener('close', () => evaluationTrigger.focus());
  popup.addEventListener('click', event => { if (event.target === popup) { const r=popup.getBoundingClientRect(); if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom) popup.close(); } });
  evaluationTrigger.addEventListener('click', async event => {
    event.preventDefault();
    popup.innerHTML = '<div class="evaluation-loading"><button type="button" data-popup-close aria-label="Close">×</button><p role="status">Loading evaluation…</p></div>';
    popup.querySelector('[data-popup-close]').onclick = () => popup.close();
    popup.showModal();
    try {
      const response = await fetch(evaluationTrigger.dataset.panelUrl);
      if (!response.ok) throw Error('Unable to load the form. Close and try again.');
      const page = new DOMParser().parseFromString(await response.text(), 'text/html');
      const card = page.querySelector('.cadet-evaluation-modal');
      if (!card) throw Error('Your session may have expired. Refresh the MDT and try again.');
      popup.replaceChildren(card);
      card.removeAttribute('role');
      const close = card.querySelector('header a');
      close.onclick = e => { e.preventDefault(); popup.close(); };
      const form = card.querySelector('form');
      card.querySelector('header h2').textContent = 'Training Evaluation';
      card.querySelector('header p').textContent = 'DOC / FIELD TRAINING PROGRAM';
      const captions = ['Training officer', 'Teaching quality', 'Session helpfulness', 'Overall training', 'Additional notes'];
      form.querySelectorAll('label > span').forEach((label, index) => {
        label.textContent = captions[index] || label.textContent;
      });
      form.querySelectorAll('select').forEach((select, index) => {
        select.options[0].textContent = index === 0 ? 'Select training officer' : 'Select rating (1–10)';
      });
      form.querySelector('textarea').placeholder = 'Feedback, suggestions, or concerns about your training…';
      const actions = document.createElement('div'); actions.className = 'evaluation-actions';
      const cancel = document.createElement('button'); cancel.type = 'button'; cancel.className = 'secondary'; cancel.textContent = 'Cancel'; cancel.onclick = () => popup.close();
      actions.append(cancel, form.querySelector('[type=submit]')); form.append(actions);

      const message = document.createElement('p'); message.setAttribute('role','status'); form.append(message);
      form.addEventListener('submit', async e => {
        e.preventDefault(); const submit = form.querySelector('[type=submit]'); submit.disabled=true;
        message.textContent='Submitting…';
        try {
          const result=await fetch(form.action,{method:'POST',body:new FormData(form)});
          const updated=new DOMParser().parseFromString(await result.text(),'text/html');
          const flashes=[...updated.querySelectorAll('.flash')];
          const success=flashes.find(item=>item.classList.contains('success') && item.textContent.includes('FTO evaluation submitted'));
          if (!result.ok || !success) throw Error(flashes.find(item=>item.classList.contains('error'))?.textContent || 'Submission could not be confirmed. Check before trying again.');
          message.textContent=success.textContent; form.querySelectorAll('input,select,textarea').forEach(field=>field.disabled=true);
          submit.textContent='Submitted';
        } catch(error) {message.textContent=error.message;submit.disabled=false;}
      });
      if(popup.open) card.querySelector('select')?.focus();
    } catch(error) {popup.querySelector('[role=status]').textContent=error.message;}
  });
}

const timestampTrigger = document.querySelector('[data-timestamp-popup]');
if(timestampTrigger){
  const popup=document.createElement('dialog');popup.className='mdt-timestamp-popup';popup.setAttribute('aria-label','Training Timestamps');document.body.append(popup);
  const frame=document.createElement('iframe');frame.title='Training Timestamps';popup.append(frame);
  timestampTrigger.addEventListener('click',event=>{event.preventDefault();const url=new URL(timestampTrigger.dataset.panelUrl,location.origin);url.searchParams.set('popup','timestamp');frame.src=url.href;popup.showModal();});
  popup.addEventListener('close',()=>{frame.removeAttribute('src');timestampTrigger.focus();});
  popup.addEventListener('click',event=>{if(event.target===popup)popup.close();});
  window.addEventListener('message',event=>{if(event.origin===location.origin&&event.source===frame.contentWindow&&event.data?.type==='mdt-close-timestamp')popup.close();});
}
