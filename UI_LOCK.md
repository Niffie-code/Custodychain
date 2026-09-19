# UI_LOCK.md — CustodyChain visual + UX inventory

Written before any feature work, per rebuild brief §0. This is the contract:
new screens must look like they always belonged to this site. Nothing in
this file is changed by the rebuild — it documents what already exists so
new work can clone it exactly.

## 1. File inventory (frontend)

- `public/index.html` — marketing landing page (hero, how-it-works, trust, features, footer)
- `public/verify.html` — the two-file "Check a copy" app (upload → processing → result states)
- `public/styles.css` — global tokens, nav, hero, cards, buttons, footer
- `public/verify.css` — the verify-app-specific states (dropzones, processing steps, verdict banner, score ring, diff views, tech details)
- `public/app.js` — verify-app logic (dropzone handling, state machine, result rendering, report download)
- `public/landing.js` — landing-page-only interactivity (nav shadow on scroll, hero preview click-through)

Backend: FastAPI + SQLite, `backend/app/{main,database,hashing,metadata,analysis,analysis_routes,report_pdf}.py`.

## 2. Design tokens (CSS variables — `:root` in styles.css) — DO NOT CHANGE

```
--brand: #3654ff        --brand-dark: #2a42d6      --brand-tint: #eef1ff
--ink: #101828           --body: #475467             --muted: #667085
--border: #e4e7ec        --surface: #ffffff          --section-tint: #f5f7ff

--verified: #12b76a      --verified-dark: #0c7a43    --verified-tint: #e7f9f0   (GREEN — used for "match / good" states)
--caution: #f79009       --caution-dark: #b5560a     --caution-tint: #fef3e2   (AMBER — used for "minor difference" states)
--altered: #f04438       --altered-dark: #b3261e     --altered-tint: #fee7e5   (RED — used for "tampered / mismatch" states)

--font-display: "Space Grotesk", "Inter", sans-serif   (headings)
--font-ui: "Inter", -apple-system, BlinkMacSystemFont, sans-serif   (body)
--font-mono: "IBM Plex Mono", Menlo, monospace          (hashes, code-like values)

--radius-sm: 8px   --radius-md: 14px   --radius-lg: 22px
--shadow-card: 0 1px 2px rgba(16,24,40,.04), 0 4px 16px rgba(16,24,40,.06)
--shadow-lift: 0 8px 24px rgba(54,84,255,.16)
```

These map directly onto the brief's "green / amber / red verdict colours already
used" — reused as-is for Seal/Check/Report verdicts. No fifth colour is introduced.

## 3. Reusable components/classes (to clone, not reinvent)

- **Nav**: `.nav` (sticky, blurred) → `.wrap.nav__inner` → `.nav__logo` (shield-check SVG mark, unchanged) + `.nav__links`
- **Buttons**: `.btn`, `.btn--primary`, `.btn--secondary`, `.btn--lg`, `.btn--block`, `:disabled` state
- **Cards/sections**: `.wrap`, `.section`, `.section--tint`, `.section-head`, `.eyebrow`
- **App shell** (verify.html pattern): `.app-shell`, `.app-header`, `.state` / `.state.is-active` (JS-driven state machine, fade-up animation)
- **Dropzone**: `.dropzone`, `.dropzone.is-dragover`, `.dropzone.has-file`, `.dropzone__icon/__text/__hint/__filename`
- **Processing steps**: `.processing__step`, `.is-active`, `.is-done` (pulsing dot → checkmark)
- **Verdict banner**: `.verdict-banner--verified|caution|altered` (green/amber/red backgrounds+text, shield/warning/x icon)
- **Score ring**: `.score-ring`, animated SVG stroke-dashoffset
- **Diff/table views**: `.diff-text`/`.diff-line--*`, `.meta-table`, `.timeline-scrubber`, `.waveform`
- **Tech details collapsible**: `.tech-details`, `.tech-details__toggle`, `.is-open`
- **Error banner**: `.error-banner` (red, icon + text)
- **Field error**: `.field-error.is-visible`

## 4. Interaction/UX patterns to reuse verbatim

- State machine via `.state`/`.state.is-active` classList toggling + `fade-up` keyframe (0.35s ease)
- Dropzone: click-to-browse, drag/dragover/dragleave/drop handling, per-field inline error text
- Processing: sequential step activation with `setProcessingStep(n, "active"|"done")`
- Button disabled state until required inputs present
- `escapeHtml()` / `formatBytes()` utility pattern for any new dynamic text
- Toggle-open technical/collapsible panels via a single class flip
- Report download: `fetch` → blob → temporary `<a download>` click, restore button label in `finally`

## 5. What is explicitly NOT changing

No new color tokens, no new font, no new icon set (inline stroke SVGs, `currentColor`,
`stroke-width="1.6–1.8"`, rounded joins — matched by any new icon), no Tailwind/shadcn,
no changed nav/footer structure. New screens (Seal, Handoff, Exhibit detail, Tamper
demo) are built by cloning the `.app-shell` / `.dropzone` / `.state` / card patterns
above and changing only labels, fields, and copy.
