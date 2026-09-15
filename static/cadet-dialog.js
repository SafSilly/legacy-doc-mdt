(() => {
  const frame = document.querySelector('[data-dashboard-frame]');
  if (!frame) {
    if (parent === window) return;
    const notify = () => parent.postMessage({type:'cadet-dialog'}, location.origin);
    addEventListener('hashchange', notify);
    document.addEventListener('click', () => setTimeout(notify, 0));
    notify();
    return;
  }
  let active = false;
  function sync() {
    const doc = frame.contentDocument;
    if (!doc?.body) return;
    const target = doc.getElementById(decodeURIComponent(frame.contentWindow.location.hash.slice(1)));
    const open = !!target?.matches('.doc-modal[id^="cadet-"],.doc-modal[id^="archived-cadet-"]');
    if (open && !active) {
      const bounds = frame.getBoundingClientRect();
      doc.body.style.setProperty('--panel-left', `${bounds.left}px`);
      doc.body.style.setProperty('--panel-top', `${bounds.top}px`);
      doc.body.style.setProperty('--panel-width', `${bounds.width}px`);
    }
    if (open && target.parentElement !== doc.body) doc.body.append(target);
    frame.classList.toggle('cadet-dialog-frame', open);
    doc.body.classList.toggle('cadet-dialog-page', open);
    document.body.classList.toggle('cadet-dialog-host', open);
    active = open;
  }
  addEventListener('message', e => {
    if(e.origin === location.origin && e.source === frame.contentWindow && e.data?.type === 'cadet-dialog') sync();
  });
  frame.addEventListener('load', () => {
    frame.contentWindow.addEventListener('hashchange', sync);
    frame.contentDocument.addEventListener('keydown', e => {
      if(e.key === 'Escape' && active) frame.contentDocument.querySelector('.doc-modal:target .doc-modal-backdrop')?.click();
    });
    sync();
  });
})();
