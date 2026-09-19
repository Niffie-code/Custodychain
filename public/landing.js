(function () {
  const nav = document.querySelector(".nav");
  if (nav) {
    window.addEventListener("scroll", () => {
      nav.style.boxShadow = window.scrollY > 8 ? "0 1px 0 rgba(16,24,40,0.04)" : "none";
    });
  }

  // The hero preview is a decorative illustration of the real upload flow,
  // so clicking it should take a visitor straight into that flow rather
  // than doing nothing.
  const previewDrop = document.querySelector(".preview-drop");
  if (previewDrop) {
    previewDrop.style.cursor = "pointer";
    previewDrop.addEventListener("click", () => {
      window.location.href = "verify.html";
    });
  }
})();
