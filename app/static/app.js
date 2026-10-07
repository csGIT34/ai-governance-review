// Response editors autosave: forms marked data-autosave="<url>" POST their fields
// ~1.2s after typing stops, immediately on select changes, and on blur. Saves are
// serialized per form and carry the version the editor loaded; a 409 means someone
// else saved first - we stop autosaving that form and keep the user's text.
(function () {
  const DEBOUNCE_MS = 1200;
  const editors = [];

  function autosize(el) {
    el.style.height = "auto";
    el.style.height = Math.max(el.scrollHeight + 2, 70) + "px";
  }

  function setup(form) {
    const state = form.querySelector(".save-state");
    const ed = { form, dirty: false, inflight: null, timer: null, stopped: false };
    const show = (text, cls) => { if (state) { state.textContent = text; state.className = "save-state " + (cls || ""); } };

    ed.save = async function () {
      clearTimeout(ed.timer); ed.timer = null;
      if (ed.inflight) { await ed.inflight; }
      if (!ed.dirty || ed.stopped) return;
      ed.dirty = false;
      show("Saving…");
      ed.inflight = fetch(form.dataset.autosave, {
        method: "POST", body: new FormData(form), headers: { "Accept": "application/json" },
      }).then(async (r) => {
        const data = await r.json().catch(() => ({}));
        if (r.ok) {
          form.elements.version.value = data.version;
          const badge = form.querySelector("[data-review-state]");
          if (badge && data.review_state) { badge.textContent = data.review_state; badge.className = "badge " + data.review_state; }
          show("Saved " + new Date().toLocaleTimeString(), "ok");
        } else if (r.status === 409) {
          ed.stopped = true;
          form.classList.add("conflict");
          const who = data.current && data.current.updated_by ? " by " + data.current.updated_by : "";
          show("Not saved: changed" + who + " while you were editing. Your text is still here - copy it, then reload the page.", "err");
        } else if (r.status === 423 || r.status === 403) {
          ed.stopped = true;
          show("Not saved: " + (data.message || r.statusText), "err");
        } else {
          ed.dirty = true;
          show("Not saved: " + (data.message || ("error " + r.status)) + " - will retry on next edit.", "err");
        }
      }).catch((e) => {
        ed.dirty = true;
        show("Not saved (offline?): " + e.message + " - will retry on next edit.", "err");
      }).finally(() => { ed.inflight = null; });
      return ed.inflight;
    };

    form.addEventListener("input", (e) => {
      if (e.target.tagName === "TEXTAREA") autosize(e.target);
      if (e.target.matches("textarea, input[type=text]")) {
        ed.dirty = true; show("Editing…");
        clearTimeout(ed.timer); ed.timer = setTimeout(ed.save, DEBOUNCE_MS);
      }
    });
    form.addEventListener("change", (e) => {
      if (e.target.tagName === "SELECT" || e.target.type === "checkbox") {
        e.target.className = e.target.className.replace(/\bs-\S+/g, "") + " s-" + e.target.value;
      }
      ed.dirty = true; ed.save();  // selects/checkboxes, and textareas on blur
    });
    form.addEventListener("submit", (e) => { e.preventDefault(); ed.dirty = true; ed.save(); });
    form.querySelectorAll("textarea").forEach(autosize);
    editors.push(ed);
  }

  const pending = () => editors.some((ed) => !ed.stopped && (ed.dirty || ed.inflight));
  const flushAll = () => Promise.all(editors.map((ed) => ed.save()));

  document.querySelectorAll("form[data-autosave]").forEach(setup);

  // Any other form on the page (evidence, sign-off, upload...) waits for autosaves first.
  document.addEventListener("submit", async (e) => {
    const f = e.target;
    if (f.matches("form[data-autosave]") || !pending()) return;
    e.preventDefault();
    await flushAll();
    f.requestSubmit(e.submitter);
  });

  // Ctrl/Cmd+S saves everything now.
  document.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === "s") { e.preventDefault(); editors.forEach((ed) => { ed.dirty = ed.dirty || !!ed.timer; }); flushAll(); }
  });

  window.addEventListener("beforeunload", (e) => {
    if (pending()) { flushAll(); e.preventDefault(); e.returnValue = ""; }
  });

  // Buttons/forms with data-confirm ask first (text comes from a data attribute, never
  // interpolated into JS, so user-entered titles can't inject script).
  document.addEventListener("click", (e) => {
    const el = e.target.closest("[data-confirm]");
    if (el && !window.confirm(el.dataset.confirm)) e.preventDefault();
  });

  // Lazy-load history panels.
  document.querySelectorAll("details[data-history]").forEach((d) => {
    d.addEventListener("toggle", async () => {
      if (!d.open || d.dataset.loaded) return;
      d.dataset.loaded = "1";
      const r = await fetch(d.dataset.history, { headers: { "Accept": "text/html" } });
      d.querySelector(".history-body").innerHTML = await r.text();
    });
  });
})();
