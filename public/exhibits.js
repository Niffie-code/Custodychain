/* CustodyChain — exhibits.js : list, search, and the offline sync controls. */
(function () {
  const { $, API_BASE, api, escapeHtml, formatBytes, formatTime,
          showGlobalError, clearGlobalError, chainChip, checkChip } = window.CC;

  let searchTimer = null;

  async function load(query) {
    const container = $("listContainer");
    try {
      const path = query && query.trim()
        ? "/api/exhibits?q=" + encodeURIComponent(query.trim())
        : "/api/exhibits";
      const items = await api(path);
      render(items, query);
    } catch (err) {
      container.innerHTML = "";
      showGlobalError(err.message || "We could not load the exhibits.");
    }
  }

  function render(items, query) {
    const container = $("listContainer");
    if (!items.length) {
      container.innerHTML = query && query.trim()
        ? `<p class="empty-note">No exhibits match “${escapeHtml(query)}”.</p>`
        : `<p class="empty-note">No exhibits sealed yet. Seal one to get started.</p>`;
      return;
    }

    const list = document.createElement("div");
    list.className = "exhibit-list";

    items.forEach((it) => {
      const a = document.createElement("a");
      a.className = "exhibit-card";
      a.href = "exhibit.html?id=" + encodeURIComponent(it.exhibit_id);
      a.innerHTML = `
        <div class="exhibit-card__top">
          <span class="exhibit-card__id">${escapeHtml(it.exhibit_id)}</span>
          <span>${chainChip(it.chain_ok)} ${checkChip(it.latest_check)}</span>
        </div>
        <div class="exhibit-card__case">Case ${escapeHtml(it.case_id)}</div>
        <div class="exhibit-card__name">${escapeHtml(it.original_filename)}</div>
        ${it.description ? `<div class="exhibit-card__desc">${escapeHtml(it.description)}</div>` : ""}
        <div class="exhibit-card__meta">
          <span>Collected by ${escapeHtml(it.collected_by)}</span>
          <span>${escapeHtml(formatTime(it.collected_at))}</span>
          <span>${escapeHtml(formatBytes(it.filesize))}</span>
          <span>${it.event_count} custody ${it.event_count === 1 ? "entry" : "entries"}</span>
        </div>`;
      list.appendChild(a);
    });

    container.innerHTML = "";
    container.appendChild(list);
  }

  $("searchInput").addEventListener("input", (e) => {
    clearGlobalError();
    clearTimeout(searchTimer);
    const value = e.target.value;
    searchTimer = setTimeout(() => load(value), 220);
  });

  // ---------------------------------------------------------------
  // Offline sync
  // ---------------------------------------------------------------
  $("exportBtn").addEventListener("click", async () => {
    const btn = $("exportBtn");
    const label = btn.textContent;
    btn.disabled = true;
    btn.textContent = "Preparing…";
    try {
      const res = await fetch(API_BASE + "/api/sync/export");
      if (!res.ok) throw new Error("We could not export the records.");
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "custodychain_sync.json";
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      $("syncNote").textContent = "Records exported. Keep this file to load onto another machine.";
    } catch (err) {
      showGlobalError(err.message || "We could not export the records.");
    } finally {
      btn.disabled = false;
      btn.textContent = label;
    }
  });

  $("importBtn").addEventListener("click", () => $("importFile").click());

  $("importFile").addEventListener("change", async () => {
    const file = $("importFile").files[0];
    if (!file) return;
    clearGlobalError();
    const btn = $("importBtn");
    const label = btn.textContent;
    btn.disabled = true;
    btn.textContent = "Importing…";
    try {
      const text = await file.text();
      let payload;
      try {
        payload = JSON.parse(text);
      } catch (e) {
        throw new Error("That file is not a CustodyChain export.");
      }
      const result = await api("/api/sync/import", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const skipped = (result.skipped_exhibits || []).length;
      $("syncNote").textContent =
        `Imported ${result.imported_exhibits} exhibit(s) and ${result.imported_events} custody entry/entries.`
        + (skipped ? ` ${skipped} exhibit(s) were skipped because their entries did not form an intact chain.` : "");
      load($("searchInput").value);
    } catch (err) {
      showGlobalError(err.message || "We could not import that file.");
    } finally {
      btn.disabled = false;
      btn.textContent = label;
      $("importFile").value = "";
    }
  });

  load("");
})();
