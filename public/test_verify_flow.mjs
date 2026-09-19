import { JSDOM } from "jsdom";
import fs from "node:fs";

const html = fs.readFileSync("./verify.html", "utf-8");
const dom = new JSDOM(html, { url: "http://localhost/", runScripts: "dangerously", pretendToBeVisual: true });
const { window } = dom;

process.on("unhandledRejection", (r) => console.error("UNHANDLED REJECTION:", r));
window.addEventListener("error", (e) => console.error("WINDOW ERROR:", e.error || e.message));

window.fetch = fetch;
window.FormData = FormData;
window.File = File;
window.CUSTODYCHAIN_API = "http://127.0.0.1:8000";

function setFiles(input, fileList) {
  Object.defineProperty(input, "files", { value: fileList, writable: false, configurable: true });
}
function makeFile(name, content, type) {
  return new File([content], name, { type: type || "text/plain" });
}

const appJs = fs.readFileSync("./app.js", "utf-8");
const scriptEl = window.document.createElement("script");
scriptEl.textContent = appJs;
window.document.body.appendChild(scriptEl);
await new Promise((r) => setTimeout(r, 200));

function log(label, cond) {
  console.log(`${cond ? "PASS" : "FAIL"} — ${label}`);
  if (!cond) process.exitCode = 1;
}
const d = window.document;

// ================= TEST 1: text file with real changes =================
setFiles(d.getElementById("fileOriginal"), [makeFile("doc.txt", "Line one.\nLine two.\nLine three.\nLine four.")]);
d.getElementById("fileOriginal").dispatchEvent(new window.Event("change", { bubbles: true }));
setFiles(d.getElementById("fileCompare"), [makeFile("doc.txt", "Line one.\nLine two edited.\nLine three.\nLine four.\nLine five added.")]);
d.getElementById("fileCompare").dispatchEvent(new window.Event("change", { bubbles: true }));
await new Promise((r) => setTimeout(r, 100));

log("submit button enabled once both files chosen", !d.getElementById("submitBtn").disabled);

d.getElementById("submitBtn").dispatchEvent(new window.Event("click", { bubbles: true }));
await new Promise((r) => setTimeout(r, 1500));

log("moved to result state", d.getElementById("state-result").classList.contains("is-active"));
log("verdict banner shows a tier class", /verdict-banner--(verified|caution|altered)/.test(d.getElementById("verdictBanner").className));
const verdictTitle = d.getElementById("verdictTitle").textContent;
log(`verdict title set (got "${verdictTitle}")`, verdictTitle.length > 0);
log("verdict title contains no hyphen", !verdictTitle.includes("-"));
log("explanation populated", d.getElementById("resultExplanation").textContent.length > 10);

const diffHtml = d.getElementById("diffContainer").innerHTML;
log("diff view rendered", diffHtml.includes("diff-line"));
log("diff uses word tags, not raw hyphen prefixes", diffHtml.includes("Added") || diffHtml.includes("Removed"));
log("diff container text has no leading hyphen artifacts", !/>-\s/.test(diffHtml));

const hashOriginal = d.getElementById("hashOriginal").textContent;
const hashCompare = d.getElementById("hashCompare").textContent;
log(`original hash looks like sha256 (${hashOriginal.slice(0, 12)}...)`, /^[0-9a-f]{64}$/.test(hashOriginal));
log(`compare hash looks like sha256 (${hashCompare.slice(0, 12)}...)`, /^[0-9a-f]{64}$/.test(hashCompare));
log("hashes differ (files were not identical)", hashOriginal !== hashCompare);

// technical details toggle
d.getElementById("techDetailsToggle").dispatchEvent(new window.Event("click", { bubbles: true }));
log("technical details opens", d.getElementById("techDetails").classList.contains("is-open"));

// ================= TEST 2: download report produces a real PDF =================
let downloadedOk = false;
const originalCreateObjectURL = window.URL.createObjectURL;
window.URL.createObjectURL = (blob) => { downloadedOk = blob && blob.size > 500; return "blob:mock"; };
window.URL.revokeObjectURL = () => {};

d.getElementById("downloadReportBtn").dispatchEvent(new window.Event("click", { bubbles: true }));
await new Promise((r) => setTimeout(r, 800));
log("report download produced a real, non trivial blob", downloadedOk);
window.URL.createObjectURL = originalCreateObjectURL;

// ================= TEST 3: start over resets state =================
d.getElementById("startOverBtn").dispatchEvent(new window.Event("click", { bubbles: true }));
await new Promise((r) => setTimeout(r, 100));
log("start over returns to upload state", d.getElementById("state-upload").classList.contains("is-active"));
log("start over disables submit again", d.getElementById("submitBtn").disabled);

// ================= TEST 4: identical files -> exact match =================
const sameContent = "Identical content for both files, nothing should differ at all.";
setFiles(d.getElementById("fileOriginal"), [makeFile("same.txt", sameContent)]);
d.getElementById("fileOriginal").dispatchEvent(new window.Event("change", { bubbles: true }));
setFiles(d.getElementById("fileCompare"), [makeFile("same.txt", sameContent)]);
d.getElementById("fileCompare").dispatchEvent(new window.Event("change", { bubbles: true }));
await new Promise((r) => setTimeout(r, 100));
d.getElementById("submitBtn").dispatchEvent(new window.Event("click", { bubbles: true }));
await new Promise((r) => setTimeout(r, 1500));
log("identical files verdict is verified tier", d.getElementById("verdictBanner").className.includes("verdict-banner--verified"));
log(`identical files score shows 100% (got "${d.getElementById("scoreRingLabel").textContent}")`, d.getElementById("scoreRingLabel").textContent === "100%");

// reset for next tests
d.getElementById("startOverBtn").dispatchEvent(new window.Event("click", { bubbles: true }));
await new Promise((r) => setTimeout(r, 100));

// ================= TEST 5: edge case — file too large (client side) =================
const bigFile = makeFile("huge.txt", "x".repeat(200), "text/plain");
Object.defineProperty(bigFile, "size", { value: 60 * 1024 * 1024 }); // pretend 60MB
setFiles(d.getElementById("fileOriginal"), [bigFile]);
d.getElementById("fileOriginal").dispatchEvent(new window.Event("change", { bubbles: true }));
await new Promise((r) => setTimeout(r, 100));
const errorText = d.getElementById("errorOriginal").textContent;
log(`oversized file shows inline error (got "${errorText}")`, errorText.toLowerCase().includes("large"));
log("oversized file error has no hyphen", !errorText.includes("-"));
log("submit stays disabled with only one valid file", d.getElementById("submitBtn").disabled);

// ================= TEST 6: edge case — blocked file type =================
d.getElementById("startOverBtn").dispatchEvent(new window.Event("click", { bubbles: true }));
await new Promise((r) => setTimeout(r, 100));
setFiles(d.getElementById("fileOriginal"), [makeFile("malware.exe", "fake binary", "application/octet-stream")]);
d.getElementById("fileOriginal").dispatchEvent(new window.Event("change", { bubbles: true }));
await new Promise((r) => setTimeout(r, 100));
const typeErrorText = d.getElementById("errorOriginal").textContent;
log(`blocked extension shows inline error (got "${typeErrorText}")`, typeErrorText.toLowerCase().includes("not supported"));
log("blocked type error has no hyphen", !typeErrorText.includes("-"));

// ================= TEST 7: image comparison end to end =================
d.getElementById("startOverBtn").dispatchEvent(new window.Event("click", { bubbles: true }));
await new Promise((r) => setTimeout(r, 100));

// Build two tiny real PNGs with an actual pixel difference, via a 1x1-per-cell approach isn't
// straightforward without canvas in jsdom, so we ship two pre-made PNGs from disk instead.
import { execSync } from "node:child_process";
fs.mkdirSync("./test_assets", { recursive: true });
execSync(`python3 -c "
from PIL import Image, ImageDraw
img = Image.new('RGB', (200,200), (240,240,245))
d = ImageDraw.Draw(img)
d.ellipse([30,30,170,170], fill=(60,120,220))
img.save('test_assets/img_a.png')
img2 = img.copy()
d2 = ImageDraw.Draw(img2)
d2.rectangle([80,80,120,120], fill=(220,40,40))
img2.save('test_assets/img_b.png')
"`);

const imgA = fs.readFileSync("./test_assets/img_a.png");
const imgB = fs.readFileSync("./test_assets/img_b.png");
setFiles(d.getElementById("fileOriginal"), [new File([imgA], "img_a.png", { type: "image/png" })]);
d.getElementById("fileOriginal").dispatchEvent(new window.Event("change", { bubbles: true }));
setFiles(d.getElementById("fileCompare"), [new File([imgB], "img_b.png", { type: "image/png" })]);
d.getElementById("fileCompare").dispatchEvent(new window.Event("change", { bubbles: true }));
await new Promise((r) => setTimeout(r, 100));
d.getElementById("submitBtn").dispatchEvent(new window.Event("click", { bubbles: true }));
await new Promise((r) => setTimeout(r, 1500));

log("image result reached", d.getElementById("state-result").classList.contains("is-active"));
const imgDiffHtml = d.getElementById("diffContainer").innerHTML;
log("image overlay rendered", imgDiffHtml.includes("<img") && imgDiffHtml.includes("base64"));
log("metadata comparison table rendered", imgDiffHtml.includes("meta-table"));

console.log("\nDone.");
process.exit(process.exitCode || 0);
