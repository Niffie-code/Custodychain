/* CustodyChain — seal.js : the "seal an exhibit" screen. */
(function () {
  const { $, api, escapeHtml, groupHex, makeStates, showGlobalError, clearGlobalError,
          setProcessingStep, resetProcessingSteps, sleep, setupDropzone,
          localInputToIso, nowForInput } = window.CC;

  const showState = makeStates(["form", "processing", "done"]);
  let chosenFile = null;

  const dropzone = setupDropzone("dropExhibit", "fileExhibit", "errorExhibit", (file) => {
    chosenFile = file;
    updateReady();
  });

  $("collectedAt").value = nowForInput();

  function updateReady() {
    const ready = !!(chosenFile && $("caseId").value.trim() && $("collectedBy").value.trim());
    $("sealBtn").disabled = !ready;
    $("sealHint").style.display = ready ? "none" : "block";
  }

  ["caseId", "collectedBy"].forEach((id) => {
    $(id).addEventListener("input", updateReady);
  });

  $("sealBtn").addEventListener("click", async () => {
    if (!chosenFile) return;
    clearGlobalError();
    resetProcessingSteps();
    showState("processing");

    try {
      setProcessingStep(1, "active");
      const form = new FormData();
      form.append("file", chosenFile);
      form.append("case_id", $("caseId").value.trim());
      form.append("collected_by", $("collectedBy").value.trim());
      form.append("collected_role", $("collectedRole").value);
      form.append("collected_place", $("collectedPlace").value.trim());
      form.append("collected_at", localInputToIso($("collectedAt").value));
      form.append("description", $("description").value.trim());
      form.append("exhibit_id", $("exhibitId").value.trim());

      await sleep(250);
      setProcessingStep(1, "done");
      setProcessingStep(2, "active");

      const data = await api("/api/exhibits/seal", { method: "POST", body: form });

      setProcessingStep(2, "done");
      setProcessingStep(3, "active");
      await sleep(300);
      setProcessingStep(3, "done");
      await sleep(180);

      $("doneSubtitle").textContent =
        `${data.exhibit_id} · ${data.original_filename} · case ${data.case_id}`;
      $("doneSeal").textContent = groupHex(data.seal_number, 16);
      $("viewExhibitBtn").href = "exhibit.html?id=" + encodeURIComponent(data.exhibit_id);
      showState("done");
    } catch (err) {
      showState("form");
      showGlobalError(err.message || "We could not seal that file. Please try again.");
    }
  });

  $("sealAnotherBtn").addEventListener("click", () => {
    chosenFile = null;
    dropzone.reset();
    ["caseId", "exhibitId", "collectedBy", "collectedPlace", "description"]
      .forEach((id) => { $(id).value = ""; });
    $("collectedRole").selectedIndex = 0;
    $("collectedAt").value = nowForInput();
    updateReady();
    clearGlobalError();
    showState("form");
  });

  updateReady();
})();
