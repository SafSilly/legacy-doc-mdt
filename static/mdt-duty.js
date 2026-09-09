// Display/navigation only. All elapsed time and week boundaries come from Flask.
document.querySelectorAll('[data-duty-navigation]').forEach(link => {
  link.addEventListener('click', event => {
    event.stopPropagation();
  });
});
document.querySelectorAll('[data-duty-correction]').forEach(form => {
  form.addEventListener('submit', async event => {
    event.preventDefault();
    const button = form.querySelector('button[type="submit"]');
    const message = document.querySelector('[data-duty-message]');
    button.disabled = true;
    message.textContent = '';
    try {
      const response = await fetch(form.action, {method:'POST', body:new FormData(form), headers:{Accept:'application/json'}});
      if (!response.ok) {
        const raw = await response.text();
        const parsed = new DOMParser().parseFromString(raw, 'text/html');
        throw new Error(parsed.querySelector('p')?.textContent || `Correction failed (${response.status}).`);
      }
      window.location.reload();
    } catch (error) {
      message.textContent = error.message;
      button.disabled = false;
    }
  });
});
