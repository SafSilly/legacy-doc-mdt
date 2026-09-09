(() => {
  const liveFormSelector = [
    "form.custom-question-form",
    "form.account-detail-form",
    "form.doc-phase-form",
    "form.doc-checklist-form",
    "form.doc-note-compose",
    "form.doc-operation-form",
    "form.division-training-form",
    "form.dashboard-modal-form",
    "form[data-add-cadet-form]",
  ].join(",");

  const independentlyManagedSelector = [
    "[data-live-request-action]",
    "[data-timestamp-control]",
    "[data-timestamp-end]",
    "[data-timestamp-edit]",
    "[data-live-list-action]",
    "[data-application-vote]",
    "[data-application-override]",
    "[data-managed-timestamp-start]",
    "[data-managed-timestamp-stop]",
  ].join(",");

  function actionLabel(form, submitter) {
    const source = `${submitter?.textContent || ""} ${form.getAttribute("action") || ""}`.toLowerCase();
    if (source.includes("create")) return "Creating...";
    if (source.includes("add")) return "Adding...";
    if (source.includes("update")) return "Updating...";
    if (source.includes("submit")) return "Submitting...";
    return "Saving...";
  }

  function showProgress(message) {
    document.querySelector("[data-live-action-overlay]")?.remove();
    const overlay = document.createElement("div");
    overlay.className = "live-action-overlay";
    overlay.dataset.liveActionOverlay = "true";
    overlay.innerHTML = `<div><i aria-hidden="true"></i><strong>${message}</strong><span>Updating this panel</span></div>`;
    document.body.append(overlay);
  }

  function hideProgress() {
    document.querySelector("[data-live-action-overlay]")?.remove();
  }

  async function submitLiveForm(form) {
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 15000);
    try {
      return await fetch(form.action || window.location.href, {
        method: "POST",
        body: new FormData(form),
        headers: { "X-Requested-With": "live-panel" },
        signal: controller.signal,
      });
    } finally {
      window.clearTimeout(timeout);
    }
  }

  document.addEventListener("submit", async (event) => {
    const form = event.target.closest(liveFormSelector);
    if (!form || form.matches(independentlyManagedSelector) || form.method.toLowerCase() !== "post") return;
    if (!form.reportValidity()) return;
    event.preventDefault();
    if (form.dataset.liveSubmitting === "true") return;
    form.dataset.liveSubmitting = "true";

    const submitter = event.submitter || form.querySelector("button[type='submit'], input[type='submit']");
    const originalText = submitter?.textContent || "";
    const pendingText = actionLabel(form, submitter);
    if (submitter) {
      submitter.disabled = true;
      submitter.classList.add("timestamp-action-pending");
      if (submitter.tagName === "BUTTON") submitter.textContent = pendingText;
    }
    showProgress(pendingText);

    try {
      const response = await submitLiveForm(form);
      if (!response.ok) throw new Error(`Request failed (${response.status})`);
      const next = new URL(response.headers.get("X-Live-Redirect") || response.url, window.location.origin);
      if (document.body.classList.contains("embedded-panel-body")) next.searchParams.set("embed", "1");
      const activeTab = document.querySelector('.tab-shell > input[type="radio"]:checked')?.id;
      if (!next.hash && activeTab) next.hash = activeTab;
      window.setTimeout(() => {
        hideProgress();
        window.location.replace(next.toString());
      }, 60);
    } catch (error) {
      form.dataset.liveSubmitting = "false";
      if (submitter) {
        submitter.disabled = false;
        submitter.classList.remove("timestamp-action-pending");
        if (submitter.tagName === "BUTTON") submitter.textContent = originalText;
      }
      hideProgress();
      const message = document.createElement("div");
      message.className = "flash error live-action-error";
      message.textContent = "That change could not be saved. Please try again.";
      form.prepend(message);
      window.setTimeout(() => message.remove(), 4000);
    }
  });
})();
