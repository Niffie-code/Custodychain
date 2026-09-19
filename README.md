# CustodyChain — ICSC 2026 Track H: Media & Civic Trust

**Proving digital evidence has not been changed.**

CustodyChain is an evidence locker with a compare tool. It answers one
question: *from the moment this file was collected, who held it, what did
they do, and are the bytes still the same?*

---

## What this proves, and what it does not

**It proves:** the file has not changed since it was sealed at collection, and
the record of who held it has not been edited, reordered, or thinned out.

**It does not prove:** that the file was true before collection.

> This system proves the file was not changed after it was sealed at
> collection. It does not prove the file was true before collection.

That sentence appears on the intake screen, on every exhibit, and in every
court report. It is the honest limit of any custody system, and hiding it
would make the rest of the product less trustworthy, not more.

**It also does not:** scrape social media, use a blockchain, treat a
similarity percentage as proof, or store real personal data. Seeds are
entirely synthetic.

### Why a hash chain and not a blockchain

A single evidence locker has one writer and one custodian. The property we
need is "you cannot edit history without it being obvious", which a hash
chain gives us outright. Distributed consensus solves a problem — mutually
distrusting writers — that this deployment does not have, so we don't pay
for it.

### Why the seal decides, and similarity only explains

SHA-256 is authoritative. If the hashes differ, the copy is **not** the
sealed file, at any similarity score. The diff engine runs *after* a seal
mismatch to explain what changed. A mismatch is never reported as a match —
there is a test that asserts exactly this across every similarity value.

---

## The four jobs

**A. Seal an exhibit** (`seal.html`) — case ID, exhibit ID (auto-generated as
`CC-YYYY-#####` if blank), who collected it and in what role, where, when,
and a short description. The file is stream-hashed with SHA-256 as it is
written, so file size is bounded by disk, not RAM. Creates the first custody
event, `COLLECTED`, pointing at a genesis hash of 64 zeros.

**B. Record a handoff** (`exhibit.html`) — `COLLECTED`, `COPIED`,
`TRANSFERRED`, `RECEIVED`, `VIEWED`, `ANALYSED`, `EXPORTED`, `RETURNED`,
`SEAL_CHECKED`, `NOTE`. Every event stores `prev_event_hash` and an
`event_hash` over the canonical JSON of its own content. Break any link and
the trail shows a red banner naming the exact step that failed.

**C. Check a copy** (`verify.html`) — two modes. *Compare two files* is the
original ad-hoc comparer, unchanged. *Check against a sealed exhibit* compares
an uploaded copy to the seal, then explains any mismatch with the existing
diff engine.

**D. Court report** (`/api/exhibits/{id}/report.pdf`) — a two-page PDF for a
magistrate. Chain status Intact/Broken, latest check Unchanged/Changed, the
seal number, the full custody table, a plain-English summary, and the limit
sentence.

---

## Run it locally

```bash
cd backend
python -m venv venv && source venv/bin/activate    # Windows: venv\Scripts\activate
pip install -r requirements.txt
python seed_demo.py                                 # 4 synthetic exhibits, 3 cases
uvicorn app.main:app --reload --port 8000
```

Then serve the frontend (any static server, no build step):

```bash
cd public
python -m http.server 5500
```

Open <http://127.0.0.1:5500/index.html>.

### Tests

```bash
cd backend
python tests/test_chain.py      # 19 tests: sealing + chain integrity
python tests/test_api_flow.py   # 45 tests: full seal -> handoff -> check -> report -> sync
```

`test_api_flow.py` stubs the FastAPI surface so it runs without a server.

---

## The tamper demo, in three minutes

1. `python seed_demo.py`, start the API, open `exhibits.html`.
2. Open any exhibit. Note the **seal number** and that the chain reads
   **intact**.
3. Under **Demonstration**, click **Check an honest copy** -> green,
   *Unchanged since collection*.
4. Click **Check a changed copy** -> red, *Changed since collection*. Exactly
   one byte was altered; the two hashes are shown side by side. The sealed
   original is never touched.
5. Click **Download court report** — the verdict now reads **Changed**, and
   both seal checks appear in the custody table.

To prove the *log* is tamper-evident as well as the file, edit it behind the
API's back:

```bash
sqlite3 backend/custodychain.db \
  "UPDATE custody_events SET to_person='Someone Else' WHERE action='TRANSFERRED';"
```

Reload the exhibit: a red banner names the exact step that was edited. The
hash chain catches an edit made directly in the database, because integrity
comes from the chain, not from database permissions.

---

## Offline and power cuts

Everything runs on localhost: intake, hashing, custody events, compare, and
PDF generation all work with no network. If power drops after a seal is
written, the record is already on disk.

- `GET /api/sync/export` — download all exhibits and events as JSON.
- `POST /api/sync/import` — merge a bundle into another machine.

The merge is idempotent (matched by `event_hash`) and refuses a group whose
events would not form an intact chain, so a corrupted bundle can never leave
a half-written trail behind. This is deliberately not a cloud live-sync
product.

---

## Architecture

```
backend/            FastAPI + SQLite
  app/hashing.py         streaming SHA-256/BLAKE2b, chain + Merkle helpers  [unchanged]
  app/analysis.py        text/image/video/audio diff engine                 [unchanged]
  app/database.py        original evidence + custody_log tables             [unchanged]
  app/custody.py         exhibits/custody_events/checks, seal + chain logic [new]
  app/custody_routes.py  /api/* seal, events, check, tamper, report, sync   [new]
  app/report_pdf.py      compare report [unchanged] + court report [added]
  seed_demo.py           synthetic exhibits                                 [new]
  tests/                 chain + API flow tests                             [new]

public/             HTML/CSS/JS, no build step
  styles.css, verify.css, app.js, landing.js    [unchanged — the design system]
  custody.css, custody.js                       [new, composed from existing tokens]
  seal.html/.js, exhibits.html/.js, exhibit.html/.js, verify-sealed.js   [new]
```

### Data model

`exhibits` (public `exhibit_id`, case, collector, place, time, filename,
size, `sha256`, storage path) - `custody_events` (actor, role, action,
from/to, note, `occurred_at`, `prev_event_hash`, `event_hash`,
`payload_json`) - `checks` (copy filename and hash, match, similarity,
verdict, summary).

Events hash this payload, with stable key order and no whitespace variance:

```json
{"exhibit_id","actor_name","actor_role","action","from_person",
 "to_person","note","occurred_at","prev_event_hash"}
```

Blank strings are normalised to `null` before hashing, so "field omitted" and
"field submitted blank" cannot produce two different hashes for one event.

### Retention

Sealed exhibits are kept (check-a-copy and the tamper demo need them).
Files submitted to the ad-hoc comparer, and the *copy* side of any seal
check, are hashed in flight and never written to the exhibit store — only
the hash and the verdict survive the request. See the policy note at the top
of `app/custody.py`.

---

## UI lock

The existing visual system was treated as fixed. `styles.css`, `verify.css`,
`app.js`, `landing.js`, `analysis.py`, `hashing.py`, `database.py`,
`metadata.py`, and `analysis_routes.py` are **byte-for-byte unchanged**. New
screens reuse the existing tokens, components, and interaction patterns; the
only hex values in new files are `#fff` and the existing logo mark's
`#3654FF`. No new framework, font, or icon set. See `UI_LOCK.md` for the full
inventory, written before any feature work.

The previous README is preserved as `README_original_prehackathon.md.bak`.
