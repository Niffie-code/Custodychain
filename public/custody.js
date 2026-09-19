/* CustodyChain — custody.js
 *
 * Shared helpers for the evidence-locker screens.
 *
 * UI LOCK: the dropzone behaviour, state machine, escapeHtml, and formatBytes
 * below are the same patterns already used in app.js, lifted so the new
 * screens behave identically rather than inventing a second set of
 * interactions. app.js itself is not modified by this file.
 */
window.CC = (function () {
  const API_BASE = window.CUSTODYCHAIN_API || "http://127.0.0.1:8000";
  const MAX_BYTES = 50 * 1024 * 1024;
  const BLOCKED_EXTENSIONS = [".exe", ".apk", ".dll", ".bat", ".sh", ".msi"];

  const $ = (id) => document.getElementById(id);

  function escapeHtml(str) {
    return String(str == null ? "" : str).replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
  }

  function formatBytes(bytes) {
    if (bytes == null) return "";
    if (bytes < 1024) return bytes + " B";
    if (bytes < 1024 * 1024) return Math.round(bytes / 1024) + " KB";
    return (bytes / (1024 * 1024)).toFixed(1) + " MB";
  }

  function formatTime(iso) {
    if (!iso) return "";
    const d = new Date(iso);
    if (isNaN(d.getTime())) return String(iso);
    return d.toLocaleString(undefined, {
      day: "2-digit", month: "short", year: "numeric",
      hour: "2-digit", minute: "2-digit",
    });
  }

  /* Plain-language names for the stored action vocabulary. The database keeps
     the machine word; people read these. */
  const ACTION_WORDS = {
    COLLECTED: "Collected",
    COPIED: "Made a copy",
    TRANSFERRED: "Handed over",
    RECEIVED: "Received",
    VIEWED: "Viewed",
    ANALYSED: "Examined",
    EXPORTED: "Exported",
    RETURNED: "Returned",
    SEAL_CHECKED: "Checked the seal",
    NOTE: "Note",
  };
  function actionWord(action) {
    return ACTION_WORDS[action] || String(action || "");
  }

  function groupHex(value, size) {
    const v = String(value || "");
    const out = [];
    for (let i = 0; i < v.length; i += (size || 16)) out.push(v.slice(i, i + (size || 16)));
    return out.join(" ");
  }

  // ---------------------------------------------------------------
  // State machine — same class flipping as app.js
  // ---------------------------------------------------------------
  function makeStates(names) {
    return function showState(name) {
      names.forEach((s) => {
        const el = $("state-" + s);
        if (el) el.classList.toggle("is-active", s === name);
      });
    };
  }

  function showGlobalError(message) {
    const text = $("globalErrorText");
    const box = $("globalError");
    if (!text || !box) return;
    text.textContent = message;
    box.style.display = "flex";
  }
  function clearGlobalError() {
    const box = $("globalError");
    if (box) box.style.display = "none";
  }

  // ---------------------------------------------------------------
  // Processing steps — same helper as app.js
  // ---------------------------------------------------------------
  function setProcessingStep(stepNumber, mode) {
    const el = document.querySelector(`.processing__step[data-step="${stepNumber}"]`);
    if (!el) return;
    el.classList.toggle("is-active", mode === "active");
    el.classList.toggle("is-done", mode === "done");
  }
  function resetProcessingSteps() {
    document.querySelectorAll(".processing__step")
      .forEach((el) => el.classList.remove("is-active", "is-done"));
  }
  function sleep(ms) { return new Promise((r) => setTimeout(r, ms)); }

  // ---------------------------------------------------------------
  // Dropzone — identical validation and drag handling to app.js
  // ---------------------------------------------------------------
  function validateFile(file) {
    const ext = "." + (file.name.split(".").pop() || "").toLowerCase();
    if (BLOCKED_EXTENSIONS.includes(ext)) {
      return "This file type is not supported here. Try a document, image, video, or audio file instead.";
    }
    if (file.size > MAX_BYTES) {
      return "This file is larger than we can process right now. Try a file under 50 MB.";
    }
    if (file.size === 0) return "This file appears to be empty.";
    return null;
  }

  function setupDropzone(zoneId, inputId, errorId, onChange) {
    const zone = $(zoneId);
    const input = $(inputId);
    const errorEl = $(errorId);
    if (!zone || !input) return { reset: () => {} };

    const textEl = zone.querySelector(".dropzone__text");
    const hintEl = zone.querySelector(".dropzone__hint");
    const originalText = textEl ? textEl.textContent : "";
    const originalHint = hintEl ? hintEl.textContent : "";

    function setError(msg) {
      if (!errorEl) return;
      errorEl.textContent = msg || "";
      errorEl.classList.toggle("is-visible", !!msg);
    }

    function reset() {
      zone.classList.remove("has-file");
      if (textEl) textEl.textContent = originalText;
      if (hintEl) hintEl.textContent = originalHint;
      input.value = "";
      setError(null);
      onChange(null);
    }

    function acceptFile(file) {
      const err = validateFile(file);
      if (err) {
        setError(err);
        zone.classList.remove("has-file");
        if (textEl) textEl.textContent = originalText;
        if (hintEl) hintEl.textContent = originalHint;
        onChange(null);
        return;
      }
      setError(null);
      zone.classList.add("has-file");
      if (textEl) textEl.innerHTML = `<span class="dropzone__filename">${escapeHtml(file.name)}</span>`;
      if (hintEl) hintEl.textContent = formatBytes(file.size);
      onChange(file);
    }

    zone.addEventListener("click", () => input.click());
    zone.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); input.click(); }
    });
    input.addEventListener("change", () => {
      if (input.files[0]) acceptFile(input.files[0]);
    });
    ["dragenter", "dragover"].forEach((evt) => {
      zone.addEventListener(evt, (e) => { e.preventDefault(); zone.classList.add("is-dragover"); });
    });
    ["dragleave", "drop"].forEach((evt) => {
      zone.addEventListener(evt, (e) => { e.preventDefault(); zone.classList.remove("is-dragover"); });
    });
    zone.addEventListener("drop", (e) => {
      const dropped = e.dataTransfer.files[0];
      if (dropped) acceptFile(dropped);
    });

    return { reset };
  }

  // ---------------------------------------------------------------
  // API
  // ---------------------------------------------------------------
  async function api(path, options) {
    const res = await fetch(API_BASE + path, options || {});
    if (!res.ok) {
      let detail = "";
      try {
        const body = await res.json();
        detail = body.detail || "";
      } catch (e) { /* non-JSON error body */ }
      throw new Error(detail || friendlyStatus(res.status));
    }
    return res.json();
  }

  function friendlyStatus(status) {
    if (status === 404) return "We could not find that exhibit.";
    if (status === 409) return "That conflicts with something already recorded.";
    if (status >= 500) return "The evidence locker is not responding. Is the backend running?";
    return "Something went wrong. Please try again.";
  }

  // Shared chip markup for chain and check status.
  function chainChip(ok) {
    return ok
      ? `<span class="chip chip--verified">${tickIcon()} Chain intact</span>`
      : `<span class="chip chip--altered">${crossIcon()} Chain broken</span>`;
  }
  function checkChip(check) {
    if (!check) return `<span class="chip chip--neutral">Not yet checked</span>`;
    if (check.match) return `<span class="chip chip--verified">${tickIcon()} Unchanged</span>`;
    const cls = check.verdict === "amber" ? "chip--caution" : "chip--altered";
    return `<span class="chip ${cls}">${crossIcon()} Changed</span>`;
  }
  function tickIcon() {
    return `<svg viewBox="0 0 24 24" fill="none"><path d="M5 12.5L10 17.5L19 7" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
  }
  function crossIcon() {
    return `<svg viewBox="0 0 24 24" fill="none"><path d="M7 7L17 17M17 7L7 17" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"/></svg>`;
  }

  // Local datetime-local value -> ISO, treating the input as local time.
  function localInputToIso(value) {
    if (!value) return "";
    const d = new Date(value);
    return isNaN(d.getTime()) ? "" : d.toISOString();
  }
  function nowForInput() {
    const d = new Date();
    const pad = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }

  return {
    API_BASE, $, escapeHtml, formatBytes, formatTime, actionWord, groupHex,
    makeStates, showGlobalError, clearGlobalError,
    setProcessingStep, resetProcessingSteps, sleep,
    setupDropzone, validateFile, api,
    chainChip, checkChip, tickIcon, crossIcon,
    localInputToIso, nowForInput,
  };
})();
