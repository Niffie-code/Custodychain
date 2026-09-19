/* CustodyChain — exhibit.js : the exhibit detail screen. */
(function () {
  const { $, API_BASE, api, escapeHtml, formatBytes, formatTime, actionWord, groupHex,
          showGlobalError, clearGlobalError, setupDropzone, chainChip, checkChip,
          localInputToIso, nowForInput } = window.CC;

  const params = new URLSearchParams(window.location.search);
  const exhibitId = params.get("id");
  let checkFile = null;
  let checkDropzone = null;

  if (!exhibitId) {
    $("loadingNote").style.display = "none";
    showGlobalError("No exhibit was specified. Pick one from the exhibits list.");
    return;
  }

  // ---------------------------------------------------------------
  // Load and render
  // ---------------------------------------------------------------
  async function load() {
    try {
      const data = await api("/api/exhibits/" + encodeURIComponent(exhibitId));
      render(data);
      $("loadingNote").style.display = "none";
      $("detailRoot").style.display = "block";
    } catch (err) {
      $("loadingNote").style.display = "none";
      showGlobalError(err.message || "We could not load that exhibit.");
    }
  }

  function render(data) {
    const ex = data.exhibit;

    $("exhibitCase").textContent = "Case " + ex.case_id;
    $("exhibitTitle").textContent = ex.original_filename;
    $("exhibitDescription").textContent = ex.description || "";
    $("chipChain").innerHTML = chainChip(data.chain_ok);
    $("chipCheck").innerHTML = checkChip(data.latest_check);

    if (!data.chain_ok) {
      $("chainBrokenBanner").style.display = "flex";
      $("chainBrokenReason").textContent = " " + (data.chain_reason || "");
    } else {
      $("chainBrokenBanner").style.display = "none";
    }

    $("sealNumber").textContent = groupHex(ex.sha256, 16);
    $("metaExhibitId").textContent = ex.exhibit_id;
    $("metaCaseId").textContent = ex.case_id;
    $("metaFilename").textContent = ex.original_filename;
    $("metaFilesize").textContent = formatBytes(ex.filesize);
    $("metaCollectedBy").textContent = `${ex.collected_by} (${ex.collected_role})`;
    $("metaPlace").textContent = ex.collected_place || "Not recorded";
    $("metaCollectedAt").textContent = formatTime(ex.collected_at);
    $("metaEventCount").textContent = data.events.length;
    $("reportBtn").href = `${API_BASE}/api/exhibits/${encodeURIComponent(ex.exhibit_id)}/report.pdf`;
    $("limitNote").textContent = data.limit_note || "";

    renderTrail(data.events, data.break_index);
    populateActions(data.actions);
  }

  function renderTrail(events, breakIndex) {
    const trail = $("trail");
    trail.innerHTML = "";

    if (!events.length) {
      trail.innerHTML = `<p class="empty-note">No custody entries recorded yet.</p>`;
      return;
    }

    events.forEach((ev, i) => {
      const item = document.createElement("div");
      item.className = "trail__item" + (breakIndex !== null && i >= breakIndex ? " trail__item--break" : "");

      let movement = "";
      if (ev.from_person || ev.to_person) {
        movement = `<div class="trail__who">${escapeHtml(ev.from_person || "—")} → ${escapeHtml(ev.to_person || "—")}</div>`;
      }

      item.innerHTML = `
        <div class="trail__dot"></div>
        <div class="trail__action">${escapeHtml(actionWord(ev.action))}</div>
        <div class="trail__who">${escapeHtml(ev.actor_name)} · ${escapeHtml(ev.actor_role)}</div>
        ${movement}
        <div class="trail__when">${escapeHtml(formatTime(ev.occurred_at))}</div>
        ${ev.note ? `<div class="trail__note">${escapeHtml(ev.note)}</div>` : ""}
        <div class="trail__hash">Entry seal ${escapeHtml(String(ev.event_hash).slice(0, 24))}…</div>`;
      trail.appendChild(item);
    });
  }

  function populateActions(actions) {
    const select = $("eventAction");
    if (select.options.length) return;  // only populate once
    (actions || []).forEach((a) => {
      const opt = document.createElement("option");
      opt.value = a;
      opt.textContent = actionWord(a);
      select.appendChild(opt);
    });
    select.value = "VIEWED";
    toggleTransferFields();
  }

  // ---------------------------------------------------------------
  // Record a handoff
  // ---------------------------------------------------------------
  function toggleTransferFields() {
    const action = $("eventAction").value;
    const needs = action === "TRANSFERRED" || action === "RECEIVED";
    $("fieldFrom").style.display = needs ? "flex" : "none";
    $("fieldTo").style.display = needs ? "flex" : "none";
  }

  $("eventAction").addEventListener("change", toggleTransferFields);
  $("eventWhen").value = nowForInput();

  function setEventError(msg) {
    const el = $("eventError");
    el.textContent = msg || "";
    el.classList.toggle("is-visible", !!msg);
  }

  $("addEventBtn").addEventListener("click", async () => {
    clearGlobalError();
    setEventError("");

    const actor = $("eventActor").value.trim();
    const action = $("eventAction").value;
    if (!actor) {
      setEventError("A name is required for every custody entry.");
      return;
    }
    if ((action === "TRANSFERRED" || action === "RECEIVED")
        && !$("eventFrom").value.trim() && !$("eventTo").value.trim()) {
      setEventError("A transfer or receipt needs at least a 'from' or a 'to' person.");
      return;
    }

    const btn = $("addEventBtn");
    const label = btn.textContent;
    btn.disabled = true;
    btn.textContent = "Adding…";

    try {
      const form = new FormData();
      form.append("actor_name", actor);
      form.append("actor_role", $("eventRole").value);
      form.append("action", action);
      form.append("from_person", $("eventFrom").value.trim());
      form.append("to_person", $("eventTo").value.trim());
      form.append("note", $("eventNote").value.trim());
      form.append("occurred_at", localInputToIso($("eventWhen").value));

      await api(`/api/exhibits/${encodeURIComponent(exhibitId)}/events`,
                { method: "POST", body: form });

      ["eventFrom", "eventTo", "eventNote"].forEach((id) => { $(id).value = ""; });
      $("eventWhen").value = nowForInput();
      await load();
    } catch (err) {
      setEventError(err.message || "We could not add that entry.");
    } finally {
      btn.disabled = false;
      btn.textContent = label;
    }
  });

  // ---------------------------------------------------------------
  // Check a copy
  // ---------------------------------------------------------------
  checkDropzone = setupDropzone("dropCheck", "fileCheck", "errorCheck", (file) => {
    checkFile = file;
    $("runCheckBtn").disabled = !file;
  });

  $("runCheckBtn").addEventListener("click", async () => {
    if (!checkFile) return;
    clearGlobalError();
    const btn = $("runCheckBtn");
    const label = btn.textContent;
    btn.disabled = true;
    btn.textContent = "Checking…";
    try {
      const form = new FormData();
      form.append("file", checkFile);
      form.append("actor_name", $("checkActor").value.trim() || "Unnamed checker");
      form.append("actor_role", $("checkRole").value);
      const result = await api(`/api/exhibits/${encodeURIComponent(exhibitId)}/check`,
                               { method: "POST", body: form });
      renderCheckResult($("checkResult"), result);
      checkFile = null;
      checkDropzone.reset();
      await load();
    } catch (err) {
      showGlobalError(err.message || "We could not check that copy.");
    } finally {
      btn.disabled = false;
      btn.textContent = label;
    }
  });

  // ---------------------------------------------------------------
  // Tamper demo
  // ---------------------------------------------------------------
  async function runDemo(variant, button) {
    clearGlobalError();
    const label = button.textContent;
    button.disabled = true;
    button.textContent = "Checking…";
    try {
      const form = new FormData();
      form.append("variant", variant);
      form.append("actor_name", "Demonstration");
      form.append("actor_role", "Analyst");
      const result = await api(`/api/exhibits/${encodeURIComponent(exhibitId)}/demo-check`,
                               { method: "POST", body: form });
      renderCheckResult($("demoResult"), result);
      await load();
    } catch (err) {
      showGlobalError(err.message || "We could not run the demonstration.");
    } finally {
      button.disabled = false;
      button.textContent = label;
    }
  }

  $("demoHonestBtn").addEventListener("click", (e) => runDemo("honest", e.currentTarget));
  $("demoAlteredBtn").addEventListener("click", (e) => runDemo("altered", e.currentTarget));

  $("tamperDownloadBtn").addEventListener("click", async (e) => {
    const button = e.currentTarget;
    const label = button.textContent;
    button.disabled = true;
    button.textContent = "Preparing…";
    clearGlobalError();
    try {
      await api(`/api/exhibits/${encodeURIComponent(exhibitId)}/tamper-copy`,
                { method: "POST" });
      const url = `${API_BASE}/api/exhibits/${encodeURIComponent(exhibitId)}/tamper-copy/download`;
      const a = document.createElement("a");
      a.href = url;
      document.body.appendChild(a);
      a.click();
      a.remove();
      $("demoResult").innerHTML =
        `<div class="info-note" style="margin-top:16px;">
           <svg viewBox="0 0 24 24" fill="none"><circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="1.8"/><path d="M12 11V16M12 8V8.1" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>
           <div>A changed copy has been downloaded. Upload it above under
           <strong>Check a copy against this seal</strong> to see the seal break.</div>
         </div>`;
    } catch (err) {
      showGlobalError(err.message || "We could not create a demonstration copy.");
    } finally {
      button.disabled = false;
      button.textContent = label;
    }
  });

  // ---------------------------------------------------------------
  // Shared result rendering — reuses the verdict banner treatment from the
  // existing compare screen, so a result looks the same wherever it appears.
  // ---------------------------------------------------------------
  function renderCheckResult(container, result) {
    const tierClass = result.verdict === "green" ? "verified"
                    : result.verdict === "amber" ? "caution" : "altered";
    const title = result.match
      ? "Unchanged since collection"
      : "Changed since collection";

    const icon = result.match
      ? `<path d="M12 2L20 6V11C20 16 16.5 20.2 12 22C7.5 20.2 4 16 4 11V6L12 2Z" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/><path d="M8.5 12L11 14.5L15.5 9.5" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>`
      : result.verdict === "amber"
        ? `<path d="M12 3L22 20H2L12 3Z" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/><path d="M12 10V14.5M12 17V17.1" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>`
        : `<circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="1.8"/><path d="M9 9L15 15M15 9L9 15" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>`;

    const similarityLine = (result.similarity_pct !== null && result.similarity_pct !== undefined)
      ? `<div class="detail-item" style="margin-top:14px;">
           <div class="detail-item__label">Content similarity, for explanation only</div>
           <div class="detail-item__value">${escapeHtml(String(result.similarity_pct))}% similar. The seal, not this number, decides the result.</div>
         </div>`
      : "";

    container.innerHTML = `
      <div class="verdict-banner verdict-banner--${tierClass}" style="margin-top:18px;">
        <div class="verdict-banner__icon"><svg viewBox="0 0 24 24" fill="none">${icon}</svg></div>
        <div>
          <h2>${escapeHtml(title)}</h2>
          <p>${escapeHtml(result.copy_filename)}</p>
        </div>
      </div>
      <p class="result-explanation">${escapeHtml(result.summary)}</p>
      ${similarityLine}
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
      </div>`;
  }

  load();
})();
