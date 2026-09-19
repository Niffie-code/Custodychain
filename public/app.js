(function () {
  const API_BASE = window.CUSTODYCHAIN_API || "http://127.0.0.1:8000";
  const MAX_BYTES = 50 * 1024 * 1024;
  const BLOCKED_EXTENSIONS = [".exe", ".apk", ".dll", ".bat", ".sh", ".msi"];
  const SIMILARITY_CAUTION_THRESHOLD = 85;

  const $ = (id) => document.getElementById(id);
  const files = { original: null, compare: null };
  let lastResult = null; // populated after a successful compare, used by the report download

  // ---------------------------------------------------------------
  // State machine
  // ---------------------------------------------------------------
  const states = ["upload", "processing", "result"];
  function showState(name) {
    states.forEach((s) => $("state-" + s).classList.toggle("is-active", s === name));
  }
  function showGlobalError(message) {
    $("globalErrorText").textContent = message;
    $("globalError").style.display = "flex";
  }
  function clearGlobalError() {
    $("globalError").style.display = "none";
  }

  // ---------------------------------------------------------------
  // Dropzones
  // ---------------------------------------------------------------
  function validateFile(file) {
    const ext = "." + (file.name.split(".").pop() || "").toLowerCase();
    if (BLOCKED_EXTENSIONS.includes(ext)) {
      return "This file type is not supported here. Try a document, image, video, or audio file instead.";
    }
    if (file.size > MAX_BYTES) {
      return "This file is larger than we can process right now. Try a file under 50 MB.";
    }
    if (file.size === 0) {
      return "This file appears to be empty.";
    }
    return null;
  }

  function setupDropzone(zoneId, inputId, errorId, key) {
    const zone = $(zoneId);
    const input = $(inputId);
    const errorEl = $(errorId);
    const textEl = zone.querySelector(".dropzone__text");
    const hintEl = zone.querySelector(".dropzone__hint");
    const originalText = textEl.textContent;
    const originalHint = hintEl.textContent;

    function setError(msg) {
      errorEl.textContent = msg || "";
      errorEl.classList.toggle("is-visible", !!msg);
    }

    function acceptFile(file) {
      const err = validateFile(file);
      if (err) {
        setError(err);
        files[key] = null;
        zone.classList.remove("has-file");
        textEl.textContent = originalText;
        hintEl.textContent = originalHint;
        updateSubmitState();
        return;
      }
      setError(null);
      files[key] = file;
      zone.classList.add("has-file");
      textEl.innerHTML = `<span class="dropzone__filename">${escapeHtml(file.name)}</span>`;
      hintEl.textContent = formatBytes(file.size);
      updateSubmitState();
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
  }

  function updateSubmitState() {
    const ready = !!(files.original && files.compare);
    $("submitBtn").disabled = !ready;
    const hint = $("uploadHint");
    if (hint) hint.style.display = ready ? "none" : "block";
  }

  setupDropzone("dropOriginal", "fileOriginal", "errorOriginal", "original");
  setupDropzone("dropCompare", "fileCompare", "errorCompare", "compare");

  // ---------------------------------------------------------------
  // Processing step UI
  // ---------------------------------------------------------------
  function setProcessingStep(stepNumber, mode) {
    // mode: "active" | "done"
    const stepEl = document.querySelector(`.processing__step[data-step="${stepNumber}"]`);
    if (!stepEl) return;
    stepEl.classList.toggle("is-active", mode === "active");
    stepEl.classList.toggle("is-done", mode === "done");
  }
  function resetProcessingSteps() {
    document.querySelectorAll(".processing__step").forEach((el) => el.classList.remove("is-active", "is-done"));
  }

  // ---------------------------------------------------------------
  // Submit
  // ---------------------------------------------------------------
  $("submitBtn").addEventListener("click", runVerification);

  async function runVerification() {
    if (!files.original || !files.compare) return;
    clearGlobalError();
    resetProcessingSteps();
    showState("processing");

    try {
      setProcessingStep(1, "active");
      const uploadForm = new FormData();
      uploadForm.append("case_id", "PUBLIC VERIFY");
      uploadForm.append("officer_id", "web");
      uploadForm.append("officer_name", "Web visitor");
      uploadForm.append("file", files.original);
      const uploadRes = await fetch(`${API_BASE}/evidence/upload`, { method: "POST", body: uploadForm });
      if (!uploadRes.ok) throw new Error("We could not read the original file. Try a different file.");
      const uploadData = await uploadRes.json();
      setProcessingStep(1, "done");

      setProcessingStep(2, "active");
      const verifyForm = new FormData();
      verifyForm.append("actor", "Web visitor");
      verifyForm.append("file", files.compare);
      const verifyRes = await fetch(`${API_BASE}/evidence/${uploadData.evidence_id}/verify`, { method: "POST", body: verifyForm });
      if (!verifyRes.ok) throw new Error("We could not compare the two files. Try again.");
      const verifyData = await verifyRes.json();
      setProcessingStep(2, "done");

      setProcessingStep(3, "active");
      const compareForm = new FormData();
      compareForm.append("original", files.original);
      compareForm.append("compare", files.compare);
      const analysisRes = await fetch(`${API_BASE}/analysis/compare`, { method: "POST", body: compareForm });
      if (!analysisRes.ok) throw new Error("We could not analyze the differences between these files.");
      const analysisData = await analysisRes.json();
      setProcessingStep(3, "done");

      setProcessingStep(4, "active");
      await sleep(450);
      setProcessingStep(4, "done");
      await sleep(200);

      renderResult({ uploadData, verifyData, analysisData, originalFile: files.original, compareFile: files.compare });
      showState("result");
    } catch (err) {
      showState("upload");
      showGlobalError(err.message || "Something went wrong while checking these files. Please try again.");
    }
  }

  function sleep(ms) { return new Promise((r) => setTimeout(r, ms)); }

  // ---------------------------------------------------------------
  // Result rendering
  // ---------------------------------------------------------------
  function renderResult({ uploadData, verifyData, analysisData, originalFile, compareFile }) {
    const exactMatch = verifyData.status === "AUTHENTIC";
    const similarity = analysisData.similarity_pct;
    let tier, title, subtitle;

    if (exactMatch) {
      tier = "verified";
      title = "Verified, files match exactly";
      subtitle = "The two files are byte for byte identical.";
    } else if (similarity >= SIMILARITY_CAUTION_THRESHOLD) {
      tier = "caution";
      title = "Minor changes detected";
      subtitle = `${similarity} percent similar to the original.`;
    } else {
      tier = "altered";
      title = "Significant changes detected";
      subtitle = `${similarity} percent similar to the original.`;
    }

    const banner = $("verdictBanner");
    banner.className = "verdict-banner verdict-banner--" + tier;
    $("verdictTitle").textContent = title;
    $("verdictSubtitle").textContent = subtitle;
    $("verdictIcon").innerHTML = verdictIconMarkup(tier);

    const displayScore = exactMatch ? 100 : similarity;
    setScoreRing(displayScore, tier);
    $("resultExplanation").innerHTML = buildExplanation(exactMatch, analysisData);

    $("hashOriginal").textContent = verifyData.original_sha256;
    $("hashCompare").textContent = verifyData.presented_sha256;

    renderDiff(analysisData);

    lastResult = {
      file_name: originalFile.name,
      file_type: compareFile.type || "unknown",
      verified_at: new Date().toISOString(),
      original_hash: verifyData.original_sha256,
      compare_hash: verifyData.presented_sha256,
      similarity_pct: displayScore,
      verdict_tier: tier,
      verdict_label: title,
      explanation: analysisData.explanation,
      extra_notes: analysisData.note ? [analysisData.note] : [],
    };
  }

  function buildExplanation(exactMatch, analysisData) {
    if (exactMatch) {
      return "These two files are cryptographically identical. Every byte matches, so there is no meaningful difference to report.";
    }
    return escapeHtml(analysisData.explanation);
  }

  function verdictIconMarkup(tier) {
    if (tier === "verified") {
      return `<path d="M12 2L20 6V11C20 16 16.5 20.2 12 22C7.5 20.2 4 16 4 11V6L12 2Z" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/><path d="M8.5 12L11 14.5L15.5 9.5" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>`;
    }
    if (tier === "caution") {
      return `<path d="M12 3L22 20H2L12 3Z" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/><path d="M12 10V14.5M12 17V17.1" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>`;
    }
    return `<circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="1.8"/><path d="M9 9L15 15M15 9L9 15" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>`;
  }

  function setScoreRing(pct, tier) {
    const circumference = 2 * Math.PI * 42;
    const fill = $("scoreRingFill");
    fill.style.strokeDasharray = `${circumference}`;
    fill.style.strokeDashoffset = `${circumference}`;
    const color = tier === "verified" ? "var(--verified)" : tier === "caution" ? "var(--caution)" : "var(--altered)";
    fill.style.stroke = color;
    requestAnimationFrame(() => {
      fill.style.strokeDashoffset = `${circumference * (1 - pct / 100)}`;
    });
    $("scoreRingLabel").textContent = Math.round(pct) + "%";
  }

  // ---------------------------------------------------------------
  // Diff views by file kind
  // ---------------------------------------------------------------
  function renderDiff(analysisData) {
    const container = $("diffContainer");
    container.innerHTML = "";

    if (analysisData.kind === "text" && analysisData.diff_ops.length) {
      container.appendChild(buildTextDiff(analysisData.diff_ops));
    } else if (analysisData.kind === "image") {
      container.appendChild(buildImageDiff(analysisData));
    } else if (analysisData.kind === "video") {
      container.appendChild(buildTimeline(analysisData, "video"));
    } else if (analysisData.kind === "audio") {
      container.appendChild(buildWaveform(analysisData));
    } else if (analysisData.timeline_markers && analysisData.timeline_markers.length) {
      container.appendChild(buildTimeline(analysisData, "file"));
    }
  }

  function buildTextDiff(ops) {
    const wrap = document.createElement("div");
    const label = document.createElement("p");
    label.className = "section-label";
    label.textContent = "What changed";
    wrap.appendChild(label);

    const box = document.createElement("div");
    box.className = "diff-text";
    ops.forEach((op) => {
      if (op.kind === "equal") {
        box.appendChild(diffLine(op.original_text, "equal"));
      } else if (op.kind === "insert") {
        box.appendChild(diffLine(op.compare_text, "insert", "Added"));
      } else if (op.kind === "delete") {
        box.appendChild(diffLine(op.original_text, "delete", "Removed"));
      } else if (op.kind === "replace") {
        box.appendChild(diffLine(op.original_text, "replace-old", "Removed"));
        box.appendChild(diffLine(op.compare_text, "replace-new", "Added"));
      }
    });
    wrap.appendChild(box);
    return wrap;
  }
  function diffLine(text, kind, tag) {
    const div = document.createElement("div");
    div.className = "diff-line diff-line--" + kind;
    if (tag) {
      const tagEl = document.createElement("span");
      tagEl.className = "diff-line__tag";
      tagEl.textContent = tag;
      div.appendChild(tagEl);
      div.appendChild(document.createTextNode(" " + text));
    } else {
      div.textContent = text;
    }
    return div;
  }

  function buildImageDiff(analysisData) {
    const wrap = document.createElement("div");

    if (analysisData.overlay_png_base64) {
      const label = document.createElement("p");
      label.className = "section-label";
      label.textContent = "Where it changed";
      wrap.appendChild(label);

      const frame = document.createElement("div");
      frame.className = "image-diff__frame";
      const img = document.createElement("img");
      img.src = "data:image/png;base64," + analysisData.overlay_png_base64;
      img.alt = "The checked image with changed regions highlighted";
      frame.appendChild(img);
      wrap.appendChild(frame);

      const caption = document.createElement("p");
      caption.className = "image-diff__caption";
      caption.textContent = "Highlighted regions show where the two images differ.";
      wrap.appendChild(caption);
    }

    if (analysisData.metadata_comparison && analysisData.metadata_comparison.length) {
      const metaLabel = document.createElement("p");
      metaLabel.className = "section-label";
      metaLabel.textContent = "Metadata comparison";
      wrap.appendChild(metaLabel);

      const table = document.createElement("table");
      table.className = "meta-table";
      table.innerHTML = `<thead><tr><th>Field</th><th>Original</th><th>Checked file</th></tr></thead>`;
      const tbody = document.createElement("tbody");
      analysisData.metadata_comparison.forEach((row) => {
        const tr = document.createElement("tr");
        tr.innerHTML = `<td>${escapeHtml(row.field)}</td><td class="${row.changed ? "changed" : ""}">${escapeHtml(row.original)}</td><td class="${row.changed ? "changed" : ""}">${escapeHtml(row.compare)}</td>`;
        tbody.appendChild(tr);
      });
      table.appendChild(tbody);
      wrap.appendChild(table);
    }
    return wrap;
  }

  function buildTimeline(analysisData, noun) {
    const wrap = document.createElement("div");
    const label = document.createElement("p");
    label.className = "section-label";
    label.textContent = "Where differences were found";
    wrap.appendChild(label);

    const box = document.createElement("div");
    box.className = "timeline-scrubber";
    const track = document.createElement("div");
    track.className = "timeline-track";
    (analysisData.timeline_markers || []).forEach((m) => {
      const marker = document.createElement("div");
      marker.className = "timeline-marker";
      marker.style.left = m.position_pct + "%";
      track.appendChild(marker);
    });
    box.appendChild(track);
    const labels = document.createElement("div");
    labels.className = "timeline-labels";
    labels.innerHTML = "<span>Start of file</span><span>End of file</span>";
    box.appendChild(labels);
    if (analysisData.note) {
      const note = document.createElement("p");
      note.className = "timeline-note";
      note.textContent = analysisData.note;
      box.appendChild(note);
    }
    wrap.appendChild(box);
    return wrap;
  }

  function buildWaveform(analysisData) {
    const wrap = document.createElement("div");
    const label = document.createElement("p");
    label.className = "section-label";
    label.textContent = "Amplitude comparison";
    wrap.appendChild(label);

    const box = document.createElement("div");
    box.className = "waveform";
    const buckets = (analysisData.waveform_profile && analysisData.waveform_profile.buckets) || [];
    const diffPositions = new Set((analysisData.timeline_markers || []).map((m) => Math.floor((m.position_pct / 100) * buckets.length)));
    buckets.forEach((v, i) => {
      const bar = document.createElement("div");
      bar.className = "waveform__bar" + (diffPositions.has(i) ? " is-diff" : "");
      bar.style.height = Math.max(4, v * 90) + "px";
      box.appendChild(bar);
    });
    wrap.appendChild(box);

    if (analysisData.note) {
      const note = document.createElement("p");
      note.className = "timeline-note";
      note.textContent = analysisData.note;
      wrap.appendChild(note);
    }
    return wrap;
  }

  // ---------------------------------------------------------------
  // Technical details toggle
  // ---------------------------------------------------------------
  $("techDetailsToggle").addEventListener("click", () => {
    $("techDetails").classList.toggle("is-open");
  });

  // ---------------------------------------------------------------
  // Download report
  // ---------------------------------------------------------------
  $("downloadReportBtn").addEventListener("click", async () => {
    if (!lastResult) return;
    const btn = $("downloadReportBtn");
    const originalLabel = btn.textContent;
    btn.disabled = true;
    btn.textContent = "Preparing report…";
    try {
      const res = await fetch(`${API_BASE}/analysis/report`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(lastResult),
      });
      if (!res.ok) throw new Error("We could not generate the report. Please try again.");
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `verification_report_${lastResult.file_name}.pdf`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      showGlobalError(err.message || "We could not generate the report. Please try again.");
    } finally {
      btn.disabled = false;
      btn.textContent = originalLabel;
    }
  });

  // ---------------------------------------------------------------
  // Start over
  // ---------------------------------------------------------------
  $("startOverBtn").addEventListener("click", () => {
    files.original = null;
    files.compare = null;
    lastResult = null;
    ["dropOriginal", "dropCompare"].forEach((id) => {
      const zone = $(id);
      zone.classList.remove("has-file");
    });
    document.getElementById("fileOriginal").value = "";
    document.getElementById("fileCompare").value = "";
    resetDropzoneLabels();
    updateSubmitState();
    clearGlobalError();
    showState("upload");
  });

  function resetDropzoneLabels() {
    $("dropOriginal").querySelector(".dropzone__text").textContent = "Drop the original here, or click to browse";
    $("dropOriginal").querySelector(".dropzone__hint").textContent = "The version you trust";
    $("dropCompare").querySelector(".dropzone__text").textContent = "Drop a file here, or click to browse";
    $("dropCompare").querySelector(".dropzone__hint").textContent = "The version you want to check";
    $("errorOriginal").classList.remove("is-visible");
    $("errorCompare").classList.remove("is-visible");
  }

  // ---------------------------------------------------------------
  // Utilities
  // ---------------------------------------------------------------
  function escapeHtml(str) {
    return String(str).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }
  function formatBytes(bytes) {
    if (bytes < 1024) return bytes + " B";
    if (bytes < 1024 * 1024) return Math.round(bytes / 1024) + " KB";
    return (bytes / (1024 * 1024)).toFixed(1) + " MB";
  }
})();
