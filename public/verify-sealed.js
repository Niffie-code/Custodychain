/* CustodyChain — verify-sealed.js
 *
 * Mode 2 of the check screen: compare an uploaded copy against a sealed
 * exhibit. This is additive. app.js keeps running the original two-file
 * compare exactly as before; this file only shows/hides the two panels and
 * drives the sealed path.
 */
(function () {
  const { $, api, escapeHtml, formatTime, setupDropzone,
          showGlobalError, clearGlobalError } = window.CC;

  let copyFile = null;
  let loaded = false;

  function setMode(mode) {
    const sealed = mode === "sealed";
    $("adhocMode").style.display = sealed ? "none" : "block";
    $("sealedMode").style.display = sealed ? "block" : "none";
    $("modeAdhocBtn").classList.toggle("is-selected", !sealed);
    $("modeSealedBtn").classList.toggle("is-selected", sealed);
    $("modeAdhocBtn").setAttribute("aria-selected", String(!sealed));
    $("modeSealedBtn").setAttribute("aria-selected", String(sealed));
    clearGlobalError();
    if (sealed && !loaded) loadExhibits();
  }

  $("modeAdhocBtn").addEventListener("click", () => setMode("adhoc"));
  $("modeSealedBtn").addEventListener("click", () => setMode("sealed"));

  async function loadExhibits() {
    const select = $("sealedExhibit");
    try {
      const items = await api("/api/exhibits");
      loaded = true;
      select.innerHTML = "";
      if (!items.length) {
        select.innerHTML = `<option value="">No exhibits sealed yet</option>`;
        return;
      }
      select.innerHTML = `<option value="">Choose an exhibit…</option>`;
      items.forEach((it) => {
        const opt = document.createElement("option");
        opt.value = it.exhibit_id;
        opt.textContent = `${it.exhibit_id} · ${it.original_filename} · case ${it.case_id}`;
        select.appendChild(opt);
      });
    } catch (err) {
      select.innerHTML = `<option value="">Could not load exhibits</option>`;
      showGlobalError(err.message || "We could not load the sealed exhibits.");
    }
  }

  const dropzone = setupDropzone("dropSealedCopy", "fileSealedCopy", "errorSealedCopy", (file) => {
    copyFile = file;
    updateReady();
  });

  function updateReady() {
    const ready = !!(copyFile && $("sealedExhibit").value);
    $("sealedSubmitBtn").disabled = !ready;
    $("sealedHint").style.display = ready ? "none" : "block";
  }

  $("sealedExhibit").addEventListener("change", updateReady);

  $("sealedSubmitBtn").addEventListener("click", async () => {
    const exhibitId = $("sealedExhibit").value;
    if (!copyFile || !exhibitId) return;
    clearGlobalError();

    const btn = $("sealedSubmitBtn");
    const label = btn.textContent;
    btn.disabled = true;
    btn.textContent = "Checking…";

    try {
      const form = new FormData();
      form.append("file", copyFile);
      form.append("actor_name", "Web visitor");
      form.append("actor_role", "Other");
      const result = await api(`/api/exhibits/${encodeURIComponent(exhibitId)}/check`,
                               { method: "POST", body: form });
      renderSealedResult(result);
    } catch (err) {
      showGlobalError(err.message || "We could not check that copy.");
    } finally {
      btn.disabled = false;
      btn.textContent = label;
      updateReady();
    }
  });

  function renderSealedResult(result) {
    const tier = result.verdict === "green" ? "verified"
               : result.verdict === "amber" ? "caution" : "altered";
    const title = result.match ? "Unchanged since collection" : "Changed since collection";
    const icon = result.match
      ? `<path d="M12 2L20 6V11C20 16 16.5 20.2 12 22C7.5 20.2 4 16 4 11V6L12 2Z" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/><path d="M8.5 12L11 14.5L15.5 9.5" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>`
      : result.verdict === "amber"
        ? `<path d="M12 3L22 20H2L12 3Z" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/><path d="M12 10V14.5M12 17V17.1" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>`
        : `<circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="1.8"/><path d="M9 9L15 15M15 9L9 15" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>`;

    const similarity = (result.similarity_pct !== null && result.similarity_pct !== undefined)
      ? `<p class="seal-caption">Content similarity: ${escapeHtml(String(result.similarity_pct))}%.
         This explains the difference; the seal decides the result.</p>`
      : "";

    $("sealedResult").innerHTML = `
      <div class="verdict-banner verdict-banner--${tier}" style="margin-top:24px;">
        <div class="verdict-banner__icon"><svg viewBox="0 0 24 24" fill="none">${icon}</svg></div>
        <div>
          <h2>${escapeHtml(title)}</h2>
          <p>${escapeHtml(result.exhibit_id)} · ${escapeHtml(result.copy_filename)}</p>
        </div>
      </div>
      <p class="result-explanation">${escapeHtml(result.summary)}</p>
      ${similarity}
      <div class="tech-details is-open" style="margin-top:18px;">
        <div class="tech-details__body" style="border-top:none;">
          <div class="tech-details__row">
            <div class="tech-details__row-label">Sealed exhibit, SHA-256</div>
            <div>${escapeHtml(result.sealed_sha256)}</div>
          </div>
          <div class="tech-details__row">
            <div class="tech-details__row-label">The copy that was checked, SHA-256</div>
            <div>${escapeHtml(result.copy_sha256)}</div>
          </div>
        </div>
      </div>
      <div class="result-actions">
        <a class="btn btn--secondary" href="exhibit.html?id=${encodeURIComponent(result.exhibit_id)}">Open this exhibit</a>
      </div>`;
  }

  updateReady();
})();
