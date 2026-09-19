# CustodyChain — public verify flow

A landing page and self serve file comparison tool for journalists, fact
checkers, civic organizations, and everyday citizens. This sits alongside
the existing officer and judicial frontend (`../frontend`) and shares the
same backend, extended only with new, additive endpoints.

## What was and was not touched

Per the brief this was built against, the constraint was strict: do not
touch, refactor, or rename any existing endpoint, route handler, hashing
function, or verification logic. Here is exactly what that meant in
practice.

**Not touched, at all:**
- `backend/app/hashing.py` — zero changes.
- `backend/app/database.py` — zero changes, same schema.
- `backend/app/metadata.py` — zero changes, only imported and called
  (read only) from the new analysis module.
- Every existing endpoint in `backend/app/main.py`
  (`/evidence/upload`, `/evidence/{id}/verify`, `/evidence/{id}/custody`,
  `/evidence/{id}/report`, `/merkle/root`, `/sync/offline-queue`, and so
  on) — same request and response shape as before. A regression check
  (upload then verify) was re run against the live server after adding
  the new router and passed identically.

**Added, as new files:**
- `backend/app/analysis.py` — similarity scoring and diffing for text,
  image, video, and audio. Pure functions, no database access.
- `backend/app/report_pdf.py` — renders a PDF from a plain dict of
  already computed values. Computes nothing new.
- `backend/app/analysis_routes.py` — the two new endpoints,
  `POST /analysis/compare` and `POST /analysis/report`, as a separate
  `APIRouter`.

**The only edit to an existing file:** two lines in `main.py`, an import
and one `app.include_router(analysis_router)` call, placed after the
existing startup handler. No existing route definition was touched.

## How the public flow uses the existing endpoints

The brief describes checking whether a file has changed compared to "an
original." The existing backend already models exactly this as a two
step flow: register a file, then verify a second file against it. The
public flow calls that same pair of endpoints under the hood:

1. `POST /evidence/upload` registers the original file. The public flow
   fills the required `case_id` and `officer_id` fields with generic
   placeholder values (`"PUBLIC VERIFY"`, `"web"`) since a citizen
   verifying a photo does not have a badge number, but the endpoint
   itself is called exactly as it already exists.
2. `POST /evidence/{id}/verify` compares the second file against the
   first. Its `AUTHENTIC` and `TAMPERED` result becomes the exact match
   or not exact match half of the verdict.
3. `POST /analysis/compare` (new) supplies the similarity score and the
   diff, so a `TAMPERED` result can be shown as "94 percent similar,
   looks like recompression" instead of a bare failure.

## Verdict logic

- Hashes match exactly -> **Verified**, one hundred percent, green.
- Hashes do not match, but similarity score is 85 percent or higher ->
  **Caution**, amber. The threshold is a judgment call, not a
  cryptographic fact, and is easy to find and adjust in `app.js`
  (`SIMILARITY_CAUTION_THRESHOLD`).
- Similarity score below 85 percent -> **Altered**, red.

## Honest limits on the video and audio views

Real frame or sample level timestamps would require decoding the actual
video or audio stream, which was out of scope here. Instead, both
compare the raw file bytes in equal sized segments and report each
differing segment's position as an estimated percentage through the
file. This is a real, deterministic comparison of the actual bytes, not
fabricated data, but it is a stand in for a decoded timeline, not a
measurement of one, and the UI says so directly under the timeline and
waveform views rather than implying more precision than the method
actually has.

## Copy rule

No hyphens appear anywhere in the rendered text of either page, including
button labels, error messages, and the diff view (which uses "Added" and
"Removed" tags rather than the usual plus and minus prefixed lines, since
a leading minus sign is itself a hyphen character). This was checked with
a script that scans every string literal and text node across
`index.html`, `verify.html`, `app.js`, and `landing.js`, separately from
class names, CSS variables, and SVG attributes, which do use hyphens as
normal, since those are code, not copy.

## Running it

```bash
# Backend (same as before, now with the two new endpoints included)
cd backend
pip install -r requirements.txt --break-system-packages
uvicorn app.main:app --reload --port 8000

# Public frontend
cd public
python3 -m http.server 5500
# open http://127.0.0.1:5500/index.html
```

If the API is not on `127.0.0.1:8000`, set it before either page's
scripts run:
```html
<script>window.CUSTODYCHAIN_API = "http://your-host:8000";</script>
```

**Test suite:** `cd public && npm install && npm test` drives the real
upload, compare, image diff, exact match, oversized file, and blocked
file type paths against a running backend, plus confirms the download
report button produces a real, non trivial PDF blob. All twenty five
checks pass against a freshly started backend.

## Bugs found and fixed after the first pass

Two things looked fine on paper but produced wrong or misleading output
under real testing, which is why everything above was re verified rather
than taken on faith.

**Single line text scored zero percent similar when it should not have.**
The first version diffed at the line level. A one line file (a headline,
a JSON blob, a caption) is one line no matter how small the edit, so any
change at all made the two "lines" completely different and the score
collapsed to zero, even for a one word edit. Fixed by tokenizing into
words and whitespace runs instead of lines, so a small edit inside a
single line now scores correctly, for example a one word change in one
sentence now reads as roughly seventy percent similar instead of zero,
and a genuinely different document still reads as low. Multi line
documents still diff sensibly, since word tokens include the line break
characters and line level structure falls out of that.

**A deliberate, localized image edit was being described as recompression.**
The similarity score averages pixel differences across the whole image,
so painting a solid rectangle over ten to fifteen percent of an otherwise
identical picture still averaged out to ninety plus percent similar,
which the original message threshold read as "consistent with
recompression." That is backwards: real recompression spreads low
magnitude noise across the entire frame, while a targeted edit is exactly
the opposite, a concentrated, high magnitude change in a small area.
Fixed by using the overlay's own region flags, not the overall
percentage, to decide the message: zero flagged regions now means
recompression style wording, and any flagged region, regardless of how
small the overall percentage impact is, gets called out directly as a
localized change instead.

## An edge case that does not map onto this design

The brief asks for a "no reference hash available" state. That scenario
fits a design where a file is registered once and other people verify
against it later by reference. This flow instead asks for both files at
once, every time, so there is never a case of a reference existing but
being unavailable, there is only "you have not added a second file yet,"
which is a normal incomplete form, not a failure state. A hint under the
compare button covers that ordinary case. If the reference lookup model
is actually wanted, that is a real design change worth its own pass
rather than a cosmetic patch here.
