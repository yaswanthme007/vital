> ⚠️ **SUPERSEDED — ARCHIVED 2026-08-19.** This document is retained only as a
> historical record of what was measured and decided at the time. It does **not**
> describe the current architecture, and several of its conclusions and
> recommendations were later shown to be wrong. Do not use it as guidance.
>
> **Current documentation:** [`docs/ARCHITECTURE.md`](../ARCHITECTURE.md) ·
> [`docs/ROADMAP.md`](../ROADMAP.md) · [`docs/EVIDENCE.md`](../EVIDENCE.md)
> See [`docs/archive/README.md`](README.md) for what specifically was superseded.

---

# VITAL — Full Project Status Summary (for external AI context)

**Purpose of this document:** This is a complete, standalone context dump of the VITAL project — what it is, its architecture, everything that has been fixed across two debugging/hardening passes, what is now verified working, what is still fake/broken/incomplete, and what remains to be built. It is written so that someone (or some AI) with **zero prior access to this codebase** can understand the full picture and pick up work correctly. A separate document ("VITAL — Complete Project Documentation") covering the backend/OCR pipeline in more depth is attached alongside this one — treat that as authoritative for backend pipeline internals, and this document as authoritative for **everything that has happened since then** (frontend wiring bugs found and fixed via live browser testing).

Read this whole thing before proposing changes — several bugs described here look small in isolation but are load-bearing for each other (see the "Sign & Lock could never be reached" chain in particular).

---

## 1. What VITAL Is

VITAL is a computer-vision system that reads an anaesthesia monitor's display and turns it into a digitised, medico-legal record automatically, in real time — without a clinician manually transcribing numbers onto paper. Point a camera at the monitor, VITAL reads the vitals via OCR, validates them, and builds a signable, auditable, PDF-exportable chart as the case happens.

**Two halves:**
- **Frontend** — React 19 + TypeScript (Vite): case setup, camera calibration, live monitoring dashboard, review/sign-off, session archive, OCR pipeline inspector (debug view).
- **Backend** — FastAPI + OpenCV + Tesseract, runs fully offline: the CV pipeline, session/vitals persistence (SQLite), validation, alerting, PDF chart generation.

Runs on an offline edge box by design: no external services, no calls out at runtime. SQLite on local disk, Tesseract as a local binary. Docker image builds once (needs internet only at build time to fetch packages) — **note: on the current dev machine, Docker is not installed**, so the backend has been run directly via a local Python venv (`backend/.venv`) with `uvicorn app.main:app`. This is functionally equivalent for development/testing purposes; the Dockerized path (`docker compose up`) has not been re-verified in this session.

### Tech stack
- **Frontend:** React 19, TypeScript, Vite, Zustand (state), Tailwind, Framer Motion, uPlot/Recharts (waveforms), React Router, TanStack Query (Archive page's data fetching)
- **Backend:** FastAPI (REST + WebSocket), OpenCV, Tesseract OCR (`pytesseract`), SQLAlchemy + SQLite, ReportLab (PDF), torch/onnx/onnxruntime (present as dependencies for a future Tier-2 model — not yet wired to anything, see `onnx_engine.py` stub)

### Architecture
```
Frontend (Vite/React, :5173)  ⇄  Backend (FastAPI, :8000)
  Live Monitor                    REST: sessions, drug log,
  Calibration                     flagged/audit corrections,
  Review & Sign-off                chart, sign, report.pdf
  Archive                         WebSocket: streamed vitals
  OCR Pipeline Inspector          Pipeline: screen detect →
                                    colour-ROI extract →
                                    Tesseract OCR →
                                    validation/reconciliation
                                   SQLite (sessions, readings,
                                    drug events, audit trail,
                                    flagged readings)
                                   ReportLab (PDF generation)
```

### The OCR pipeline (colour-gated, not generic OCR)
1. **Screen detection** (`detect.py`) — Canny edge detection finds a screen-like quadrilateral ≥50% of frame, perspective-corrects it. Falls back to the unwarped frame if no confident quad found.
2. **Colour-ROI extraction** (`roi.py`) — every pixel checked against six exact target colours (HR `#00FF88` green, SpO₂ `#00D4FF` cyan, NIBP `#FF4757` red, EtCO₂ `#FFD600` yellow, Temp `#FF9500` orange, RR `#BF5AF2` purple), tolerance on the hue wheel at high saturation/brightness. Non-matching pixels are invisible to the system.
3. **OCR** (`ocr.py`) — Tesseract reads just the isolated colour region (PSM 8, digit whitelist; NIBP gets a two-line "sys/dia" split parser).
4. **Validation** (`app/validation/reconcile.py`) — every reading checked for physiological plausibility; implausible/low-confidence reads are flagged for human review instead of silently accepted.

This is an honest **Tier-1** scope: provably correct for a screen rendering in VITAL's own known colour palette, not generalized to arbitrary real-world monitors (different manufacturers use different colour conventions, some don't colour-code numerals at all; a Tier-2 trained model is scoped but not built — `onnx_engine.py` is a stub only).

---

## 2. Hardening Pass #1 (prior session — already done, verified, do not re-litigate)

Everything below was found and fixed in an earlier session, confirmed working at the time. Treat as a stable baseline unless something in Section 3/4 below says otherwise.

**Navigation & layout:**
- StartPage scroll trap on step 2 (6 ASA options pushed "Begin Monitoring" off-screen) — fixed with `overflow-y-auto`.
- OCR Debug page's Auto-play controls pushed off-screen by an SVG's intrinsic aspect ratio — fixed with `min-h-0` at three levels of the flex chain.
- Demo Mode widget was a floating overlay permanently covering other pages' buttons — converted to a dropdown docked in the nav bar (TopNav / SurgeryHeader).
- Live Monitor had no way to navigate to Calibration/Archive/OCR Debug — added icon buttons to `SurgeryHeader`.
- Deleted dead code: `Sidebar.tsx` (a second, never-mounted nav implementation).
- `SurgeryHeader` overflow at narrow widths — fixed with `overflow-x-auto`.

**Silent-failure / error handling:**
- Every backend-calling action (Start/Pause/Resume/End Session, Add Note, both Demo Mode entry points) failed **completely silently** on backend-unreachable — root cause of "I clicked Begin Monitoring and nothing happened." Fixed by wiring `useToastStore`/`useToast()` into every call site with actionable messages ("Couldn't reach the backend — check it's running").
- Demo Mode used to mark itself "active" before confirming the backend call succeeded — fixed to only activate on confirmed success (see `DemoMode.tsx` `handleActivate`).

**Live vitals / waveforms:**
- Demo Mode and the backend's synthetic WebSocket used to race on the same `useVitalsStore`, producing incoherent mixed readings — fixed by having `useVitalsSimulation` (the WS hook) stop writing (and stop even connecting) while Demo Mode is active. **This isolation is still intact after all Section 3/4 changes — verified.**
- Alerts used to only come from the backend's WS, so Demo Mode's scripted scenarios never triggered real alerts — fixed by porting `check_alerts()` thresholds to `src/lib/alertRules.ts`, run centrally in `vitalsStore.updateVitals()` with the same 30s throttle as the backend. **Still intact and untouched.**
- Waveform charts (ECG/PLETH/CAPNO) sometimes never animated due to a stale zero-size measurement at mount — fixed with a three-layer init (immediate try, ResizeObserver retry, ~2s poll fallback) plus try/finally around the render loop. **Untouched in this pass.**
- WebSocket had no reconnect logic — fixed with auto-reconnect on `onclose`, exponential backoff (1s→2s→4s, capped 10s), reset on successful reconnect. **Untouched and confirmed still present** (`hooks/useVitalsSimulation.ts`).

**OCR pipeline (backend):**
- Colour-system mismatch: the real dashboard (`VitalsGrid.tsx`) used muted clinical colours, completely different from the neon values the pipeline expects — this is why a screenshot of VITAL's own dashboard scored 7–23% OCR confidence. Documented as an intentional two-colour-system split (dashboard = human-readable clinical colours; `design-system/tokens.ts`'s neon values = what the OCR pipeline actually looks for on a *real physical monitor screen*, not on VITAL's own UI).
- Background-polarity bug in `_preprocess()` (`ocr.py`) — always assumed dark background/bright text; fixed to determine polarity from majority pixel count. Verified: HR confidence jumped from single digits to 96% on a light background, no regression on dark (91–96%).

**Networking / testability:**
- LAN access fixed: `vite.config.ts` → `server: { host: true }`; `src/lib/api.ts` → `API_BASE` derived from `window.location.hostname` (not hardcoded `localhost`); backend CORS widened with `allow_origin_regex` covering private RFC 1918 ranges on port 5173 only (see `backend/app/main.py`).
- Added `getDisplayMedia()` tab-share capture to Calibration as an alternative to a physical camera — lets Calibration capture another browser tab's actual rendered pixels directly.

---

## 3. Session 2 — Live Browser Verification (found 5 confirmed-broken things)

Because the previous session's fixes were plausible-looking but never actually exercised against a **running** app, a full Playwright-driven browser test was done: real Chromium, real clicks, real Network/WS tab inspection, independent `curl` verification against the backend — not code reading, not inference. This found the following, all **confirmed broken with hard evidence** (WS payload logs, curl output, screenshots):

1. **NIBP "Measure" button** — clicking it once made the entire button (`aria-label="Measure blood pressure"`) **disappear permanently**, zero recovery for the rest of the session. Root cause: `VitalsGrid.tsx` passed `onAction={nibpMeasuring ? undefined : triggerNibp}` — when `nibpMeasuring` became true, `onAction` became `undefined`, and `VitalCard.tsx`'s action-button block is gated on `{onAction && (...)}`, so the whole block unmounted instead of showing a disabled "Measuring…" state.

2. **Confidence badges were 100% fabricated, even for real sessions.** Over a 30s window on a real backend session (`source=synthetic`, not Demo Mode): every WS `reading` frame's `confidence` field was flat `{"hr":100,"spo2":100,...}` for all 29 frames, while the DOM badges independently wobbled (HR 92–95%, SpO₂ 92–99%, NIBP 85–92%, etc.) — clearly a client-side random walk (`useSimulatedConfidence()` in `VitalsGrid.tsx`) running **unconditionally regardless of session source**, never consuming `msg.confidence` from the WS payload at all.

3. **Review & Sign-off showed fake mock data regardless of the real session.** Opened a real, ~1-minute-old session; backend confirmed via curl `GET /api/sessions/{id}/flagged → []`, `/audit → []`, `flaggedCount: 0`. UI showed "4 pending review" (2 critical, 2 warning) — the same hardcoded `INITIAL_FLAGGED` array every single time, including a "captured frame" SVG with the patient ID **hardcoded to `"PT-2024-001"`** regardless of the actual session's patient ID (`ReviewPage.tsx`'s `MonitorContent` component).

4. **Sign & Lock Record was permanently unreachable.** It's gated on the fake pending-review count (`allResolved = pendingItems.length === 0`) reaching zero, but `Dismiss`/`Apply Correction` clicks fired **zero network requests** (confirmed twice) and the count got stuck at 3/4 after the first dismiss — meaning the button could never actually be exercised through the UI in any session, ever. Force-removing the HTML `disabled` attribute via DOM manipulation and clicking anyway still fired zero network calls (React reasserts its controlled `disabled` prop on the next render — not a separate hidden guard, just React doing its job against a manually-mutated DOM). Independently confirmed the **backend half was already solid**: direct `curl POST /end` → 200 "completed", `POST /sign` → 200 with `signedAt`/`signedBy`/`pdfUrl` populated, `GET /report.pdf` → 200, valid `%PDF-1.4` file. 100% a frontend wiring problem.

5. **Download PDF / Print did nothing.** Archive's "Download PDF" button (`ArchivePage.tsx`) opened a fully client-rendered **fake PDF preview modal** (`PdfPreview` component) with hardcoded stats ("Total frames processed: 3,847", "OCR confidence (avg): 94.2%", "Human corrections: 3" — none of it real), whose own internal "Print"/"Download" buttons had **no `onClick` handlers at all**. Review's locked-state dialog (`PdfDialog` in `ReviewPage.tsx`) had a "Download PDF" button with no handler either, and no Print button at all.

---

## 4. Session 3 — Fixes Applied and Re-Verified (all confirmed via live Playwright testing again)

All five items above are now fixed. Every fix below was re-verified the same way it was found: driving the actual running app, inspecting real Network/WS traffic, independently curling the backend. Not "should work now" — actually observed working.

### Fix 1 — NIBP Measure button
**Files:** `src/features/surgery/components/VitalsGrid.tsx`, `src/store/vitalsStore.ts`, `src/hooks/useVitalsSimulation.ts`

- `VitalsGrid.tsx`: `onAction` is now unconditionally `triggerNibp` (never `undefined`) — the button block never unmounts. `VitalCard.tsx` already correctly rendered a disabled "Measuring…" state via `actionBusy`; it just needed to actually be reached.
- `vitalsStore.ts`: added `currentConfidence: Record<VitalType, number> | null` state + `setConfidence()` action (see Fix 5).
- `useVitalsSimulation.ts`: added a non-React `useVitalsStore.subscribe()` listener inside the WS-owning effect that fires the moment `nibpMeasuring` flips `false → true`, sending a real `{"type":"trigger_nibp"}` WS frame (the backend's `receive_loop` in `app/ws/vitals.py` already listened for this — it was just never sent). `nibpMeasuring` now resets to `false` on the **next real `reading` message** received after the trigger (a genuine backend-driven signal), not a client-side timer.

**Verified:** clicked twice in the same session without reload. Each click: DOM shows `aria-label="Measuring blood pressure"`, `disabled=true`, text "Measuring…"; a real `trigger_nibp` WS frame is sent; ~1–2s later (next reading) it resets to `aria-label="Measure · 0m ago blood pressure"`, enabled again, ready for the next click.

### Fix 2 — Review & Sign-off real data
**Files:** `src/features/review/ReviewPage.tsx` (large rewrite), `src/lib/api.ts`

- Deleted `INITIAL_FLAGGED`, `MOCK_TIMELINE`, `INITIAL_AUDIT`, `INITIAL_EVENTS` entirely.
- `api.ts`: added `getFlagged(sessionId)`, `getAudit(sessionId)`, `correctFlagged(flaggedId, correctedValue, author?)`, `dismissFlagged(flaggedId, author?)` — hitting the backend's existing (already-correct) endpoints: `GET /api/sessions/{id}/flagged`, `GET /api/sessions/{id}/audit`, `POST /api/flagged/{id}/correct`, `POST /api/flagged/{id}/dismiss`.
- `ReviewPage.tsx`: fetches real flagged + audit data on mount / session-id change, with a loading state. `handleCorrect`/`handleDismiss` are now real `async` calls with optimistic UI update + rollback + toast on failure, then re-fetch the real audit trail.
- Case Timeline and Event Markers tabs (which have no dedicated backend endpoint of their own) are now derived from the session's real `notes` array (the same data `SurgeryPage`'s quick-mark buttons persist via `api.addNote`) instead of a hardcoded fixture — see `notesToTimeline()` / `notesToEventMarkers()` helpers in `ReviewPage.tsx`.
- Fixed the hardcoded `"PT-2024-001"` in `MonitorContent`'s SVG header text — now interpolates the real session's patient ID + ASA class via a new `patientLabel` prop threaded through `FrameViewer`.
- Added an explicit empty-state message in the flagged-readings sidebar ("No flagged readings — this session's OCR reads all came back within confidence thresholds") instead of silently rendering nothing.

**Verified:** opened a fresh real session. `GET /flagged` and `/audit` fire for real (visible in Network tab), both return `[]`. UI shows "All readings reviewed", the empty-state message, and 0/0 progress — not the old 4 canned items. Independently re-curled `/flagged` for the same session ID afterward: still `[]`, matching exactly.

### Fix 3 — Sign & Lock gating + real signing
**Files:** `src/features/review/ReviewPage.tsx`, `src/components/layout/TopNav.tsx`, `src/store/sessionStore.ts`

- Sign & Lock's `disabled` condition is now `!session || loadingFlagged || !allResolved || signState !== 'idle'`, where `allResolved = pendingItems.length === 0` against the **real** fetched `flaggedItems` array — so a session with a genuinely empty flagged list is enabled immediately, no longer blocked by fake data that could never reach zero.
- `handleSignConfirm` replaced entirely: was a local `setInterval` fake progress bar that called `api.signSession()` **zero times**. Now it's a real `async` function calling `api.signSession(session.id, session.anesthetist, 'typed')`, with a `signing` boolean driving a real loading state on the confirm button and an indeterminate spinner in the "signing" dialog state (no fabricated percentage — there's no meaningful progress signal for a single POST request). On success → `signState = 'locked'`. On failure → toast with a specific message (detects the backend's `409 "Session must be completed before it can be signed"` and tells the user to click End first; otherwise a generic backend-unreachable message).
- `SignDialog`'s hardcoded "All 4 flagged readings reviewed and resolved" text is now dynamic (`flaggedCount` prop), including a distinct "No flagged readings on this session" message for the zero case.
- **Necessary side-fix (not in the original ask, but a hard blocker for this fix to work at all):** `TopNav.tsx`'s `handleEndSession` called `navigate('/landing')` after ending — but the backend's `POST /sign` requires `status='completed'` (raises `SessionNotCompleted` → 409 otherwise), and the *only* way to reach `completed` status is via End. Landing has no session context and no way back into Review, so the old code made it **structurally impossible** to ever reach a signable state through the UI, for any session, ever. Changed to `navigate('/review')`.
- **Related side-fix:** `sessionStore.ts`'s `endSession()` used to set `activeSession: null` **immediately** (before the API call resolved), while `archivedSessions` only got the ended session appended asynchronously in a `.then()`. Since `ReviewPage` reads `activeSession ?? archivedSessions[0]`, there was a race window where Review had nothing to show. Fixed to optimistically flip `status: 'completed'` on the same session object (matching the existing pause/resume pattern) and only swap to the archived version once the real response lands — so Review's subject never disappears mid-transition.

**Verified end-to-end through the real UI** (not curl-only this time): created a session → Review showed 0 pending → Sign & Lock was enabled **before** ending → clicked End (top nav) → real `POST /end` fired, landed back on `/review` (not bounced to landing) → Sign & Lock still enabled → clicked it → confirm dialog → clicked "Sign & Lock Record" → real `POST /sign` fired → 200 → "Record Signed & Locked" dialog appeared. Independently curled afterward: `signedAt`, `signedBy: "Dr. Verify"`, `signatureMethod: "typed"`, `pdfUrl` all populated on the real DB row; `GET /report.pdf` → HTTP 200, real `%PDF-1.4` file (3129 bytes).

### Fix 4 — Download PDF / Print
**Files:** `src/features/review/ReviewPage.tsx` (`PdfDialog`), `src/features/archive/ArchivePage.tsx`

- `PdfDialog`'s "Download PDF" button now calls `window.open(api.reportPdfUrl(sessionId), '_blank', 'noopener')` — opens the real generated PDF (backend sets no `Content-Disposition` header, so it opens inline in a new tab rather than forcing a save-as; still the real document).
- Added a real **Print** button to `PdfDialog` (didn't exist before at all): fetches the real PDF as a blob, loads it into a hidden `<iframe>`, calls `iframe.contentWindow.print()` once loaded. Errors are toasted.
- `ArchivePage.tsx`: the `DetailPanel`'s "Download PDF" button (the one that was previously dead — opened the fake local `PdfPreview` modal) now has its own `onDownload` prop wired to the same real `window.open(api.reportPdfUrl(...))` pattern, separate from "View Record" which still opens the illustrative preview modal. The fake modal's own internal Print/Download buttons were also wired for consistency (Print → `window.print()`, Download → real PDF URL).

**Verified:** clicked Archive's Download PDF for a just-signed session → a new tab opened, and (captured at the browser-context network level, since Chromium's built-in PDF viewer doesn't expose `page.evaluate`) a real request to `GET /api/sessions/{id}/report.pdf` fired → HTTP 200, `content-type: application/pdf`.

### Fix 5 — Confidence badges use real backend data
**Files:** `src/store/vitalsStore.ts`, `src/hooks/useVitalsSimulation.ts`, `src/features/surgery/components/VitalsGrid.tsx`

- `vitalsStore.ts`: new `currentConfidence: Record<VitalType, number> | null` field + `setConfidence()` action; cleared in `clearHistory()` (called on session start/end).
- `useVitalsSimulation.ts`: `ws.onmessage`'s `reading` handler now also calls `setConfidence(msg.confidence)` — the real per-vital confidence that was always present in the WS payload but previously discarded entirely.
- `VitalsGrid.tsx`: `useSimulatedConfidence()` now takes an `enabled` param and only runs its `setInterval` random walk when Demo Mode is active (`useDemoStore(s => s.active)`). For any real backend session, it reads `currentConfidence` from the store instead. `undefined` (before the first real frame lands) correctly hides each card's confidence bar rather than showing a fabricated number. **Demo Mode's path is untouched by design** — it has no backend session to source real confidence from, so the simulated walk there is correct, not a bug.

**Verified:** logged 21 real WS frames + 11 DOM samples over ~22s on a real session — both flat at 100% the entire time, tracking exactly (previously: WS flat 100%, DOM independently wobbling 85–99%). Separately re-verified Demo Mode is unaffected: activated a demo scenario, confirmed **zero** real backend WS connection opened (isolation from Hardening Pass #1 intact) and confidence badges still show the simulated wobble (93→92→93→94% etc.), exactly as intended.

---

## 5. Current Verified Status Table (as of end of Session 3)

| Area | Status | Notes |
|---|---|---|
| New Case → Live Monitor flow | ✅ Works | Real session creation, real WS stream |
| NIBP Measure button | ✅ Works | Fixed this session — see Fix 1 |
| Confidence badges (real sessions) | ✅ Works | Fixed this session — see Fix 5 |
| Confidence badges (Demo Mode) | ✅ Works (by design) | Simulated, isolated from backend — untouched |
| Review & Sign-off real flagged/audit data | ✅ Works | Fixed this session — see Fix 2 |
| Sign & Lock Record (full flow) | ✅ Works | Fixed this session — see Fix 3 |
| Download PDF (Archive + Review) | ✅ Works | Fixed this session — see Fix 4 |
| Print (Review locked dialog + Archive fake preview) | ✅ Works | New — didn't exist before |
| Toast notifications on backend-call failure | ✅ Works | Hardening Pass #1, untouched |
| Demo Mode / real-WS isolation | ✅ Works | Hardening Pass #1, untouched |
| `alertRules.ts` ported thresholds (Demo Mode) | ✅ Works | Hardening Pass #1, untouched |
| WS reconnect/backoff | ✅ Works | Hardening Pass #1, untouched |
| Waveform three-layer init | ✅ Works | Hardening Pass #1, untouched |
| LAN/CORS config | ✅ Works | Hardening Pass #1, untouched |
| OCR pipeline (colour-gated) — synthetic/simulator input | ✅ Works | 91–96% confidence on known-palette synthetic frames |
| **Camera → live dashboard streaming** | ❌ **Not implemented** | See Section 6 — biggest remaining gap |
| Session persistence across page refresh | ❌ Not implemented | Refreshing mid-session loses all state, redirects to `/start` — Zustand has no persistence layer |
| Calibration page's 4-step wizard | ⚠️ Partially fake | Only "Camera" + "Verify" steps do anything real (call `/api/pipeline/read-frame`); "Detect Monitor / Perspective / Regions / Complete" are decorative timers producing numbers that go nowhere |
| OCR Debug page | ⚠️ Fully fake | 100% scripted `STAGES` array, zero backend calls, despite the layout bug being fixed in Pass #1 |
| Archive session list "✓ Signed" label | ⚠️ Misleading | Hardcoded on every row for any `status=completed` session, regardless of whether it was actually signed via `/sign` (i.e. has a real `signedAt`) — not touched this session |
| Frame Viewer "captured frame" SVG | ⚠️ Illustrative | The waveform shapes and `monitorValues` (`hr: '74'`, etc.) are hardcoded decoration — there's no backend endpoint that stores/serves a real captured frame image. Only the OCR-overlay boxes on top of it use real flagged-item data now (patient ID label was fixed this session; the underlying "monitor" numbers were not) |
| Event Markers tab "Add custom observation" | ⚠️ Local-only | Seeded from real session notes now, but new markers added via this UI don't persist to any backend endpoint (same as before this session — not a regression, just still not real) |

---

## 6. What Still Needs To Be Built (priority-ordered)

### P0 — the core pitch claim isn't wired up yet
**Camera → live dashboard streaming.** This is VITAL's entire premise ("point a camera at the monitor, VITAL reads the vitals") and it is **not currently connected end-to-end**. What exists:
- `CalibrationPage.tsx` already has working video→canvas→`toBlob()` capture code, and a working "Verify" step that POSTs a single frame to `/api/pipeline/read-frame` (proven to work — 91–96% confidence on synthetic frames).
- The backend already has `POST /api/pipeline/push-frame/{session_id}` (accepts frames for camera-sourced streaming) and the WS endpoint already supports `?source=camera` → `CameraSource(channel=session_id, ...)` reading from a frame queue (`app/sources/camera.py`, `app/sources/frame_queue.py`).
- **What's missing:** nothing on the frontend actually calls `push-frame` on a recurring interval during live monitoring. `src/lib/api.ts`'s `vitalsWsUrl()` is hardcoded to `?source=synthetic` always — there's no parameter to request `source=camera`, nothing threads "was a camera set up in Calibration" from `CalibrationPage` through to `SurgeryPage`, and `CameraOverlay` (a component that must already exist somewhere, referenced in the original debugging doc) is not imported anywhere in `SurgeryPage.tsx`.
- **To build:** extract the capture code from `CalibrationPage.tsx` into a reusable hook that runs on a ~1Hz interval and POSTs to `push-frame/{session.id}`; give `vitalsWsUrl()` a `source` param; thread the camera-vs-synthetic choice from Calibration into `SurgeryPage` (likely via `sessionStore` or a new small store); import `CameraOverlay` into `SurgeryPage` so there's a visible feed. Real end-to-end verification requires an actual camera pointed at a screen rendering VITAL's palette (the `getDisplayMedia()` tab-share feature from Pass #1 is the easiest way to test this without physical hardware).

### P1 — meaningful gaps, but not blocking the core story
1. **Session persistence on refresh.** Currently a page refresh always redirects to `/landing`/`/start` regardless of whether a session is actually still active on the backend. Should do an on-mount check (e.g. `GET /api/sessions?status=active` or store the active session ID in `localStorage`/`sessionStorage` and re-fetch `GET /api/sessions/{id}` on mount) instead of unconditionally treating "no in-memory Zustand state" as "no session."
2. **Trim Calibration's fake steps.** Cut "Detect Monitor / Perspective / Regions / Complete" down to just "Camera → Verify" (the only two that do anything real, since `read_frame()` takes no calibration-profile input anyway) — keep the real tab-share capture feature, remove the decorative timer steps around it.
3. **Decide OCR Debug page's fate.** Either pull it from the pitch-facing nav (it's 100% scripted, zero backend calls) or replace it with a simple real panel showing the actual most recent `read_frame()` result.
4. **Archive's "✓ Signed" label.** Should check the session's real `signedAt`/`pdfUrl` fields (already returned by `GET /api/sessions?status=completed` per the session-detail shape seen in Section 7) instead of hardcoding "Signed" for every row.

### P2 — smaller/cosmetic
- Frame Viewer's decorative monitor SVG (`monitorValues` hardcoded numbers, fixed waveform paths) — either wire to the real most-recent reading for that session, or clearly label it as illustrative in the UI copy so it doesn't read as a real captured frame.
- Event Markers tab's "Add custom observation" doesn't persist — either wire it to `api.addNote()` (works even for a completed/archived session? needs checking — `addNote` in `sessionStore.ts` currently requires `activeSession`) or remove the "Add" affordance from Review (it's post-hoc review, not live charting, so a freeform add here may not even be clinically meaningful).

---

## 7. Reference: Key API Endpoints (backend, FastAPI, base `http://<host>:8000`)

```
GET    /health
GET    /docs                                    (Swagger UI)

POST   /api/sessions                            create session
GET    /api/sessions?status=active|completed    list sessions
GET    /api/sessions/{id}                       session detail
POST   /api/sessions/{id}/pause
POST   /api/sessions/{id}/resume
POST   /api/sessions/{id}/end                   -> status: "completed"
POST   /api/sessions/{id}/notes                 body: {text, category}

GET    /api/sessions/{id}/flagged               real flagged readings (was mocked in FE)
GET    /api/sessions/{id}/audit                 real audit trail (was mocked in FE)
POST   /api/flagged/{flagged_id}/correct        body: {correctedValue, author?}
POST   /api/flagged/{flagged_id}/dismiss        body: {author?}
POST   /api/flagged/{flagged_id}/confirm        adopt suggested value as correct

GET    /api/sessions/{id}/chart?interval_minutes=5
POST   /api/sessions/{id}/sign                  body: {author, signatureMethod}
                                                 REQUIRES status == 'completed' (409 otherwise)
GET    /api/sessions/{id}/report.pdf            real PDF, application/pdf, no Content-Disposition

POST   /api/pipeline/read-frame                 multipart file -> single-frame OCR read
POST   /api/pipeline/push-frame/{session_id}    (exists; NOT currently called by frontend — see P0)

WS     /ws/vitals/{session_id}?source=synthetic|camera|replay&dataset=&interval=&seed=
  server -> client: {"type":"reading","reading":{...},"confidence":{hr,spo2,nibp,etco2,temp,rr},"provenance":"..."}
                     {"type":"alert","alert":{...}}
                     {"type":"flagged","flagged":{...}}
                     {"type":"nibp_measuring"}          (ack, not completion)
                     {"type":"alert_acknowledged","id":...}
                     {"type":"error","message":"..."}
  client -> server: {"type":"trigger_nibp"}
                     {"type":"ack_alert","id":"..."}
```

Session detail shape (from a real curl, illustrative):
```json
{
  "id": "SESSION-...", "patient": {"id": "...", "age": null, "weight": null, "asa": null},
  "procedure": "...", "anesthetist": "...", "startTime": 0, "endTime": null,
  "notes": [...], "status": "active|paused|completed",
  "signedAt": null, "archivedAt": null, "interruptedAt": null, "currentOwner": null,
  "signedBy": null, "signatureMethod": null, "pdfUrl": null,
  "vitalsCount": 0, "drugsCount": 0, "eventsCount": 0, "flaggedCount": 0
}
```

---

## 8. Reference: Key Files

**Frontend (`vital/src/`)**
- `App.tsx` — routes. `/`, `/landing`, `/start`, `/surgery` are standalone; `/review`, `/calibration`, `/archive`, `/ocr-debug` share `AppLayout` (top nav). No active-session route guard exists anywhere.
- `store/sessionStore.ts` — session CRUD + toast-on-failure wrapper (`reportApiFailure`).
- `store/vitalsStore.ts` — current reading, history (`MAX_HISTORY=360`), alarm limits, NIBP measuring state, **real confidence** (new).
- `store/demoStore.ts`, `store/toastStore.ts`, `store/alertStore.ts`
- `hooks/useVitalsSimulation.ts` — the WS connection to the backend (opens only when `activeSession.status==='active' && !demoActive`); owns NIBP trigger send + confidence consumption.
- `lib/api.ts` — REST client + `vitalsWsUrl()`.
- `lib/alertRules.ts` — ported backend alert thresholds, used by Demo Mode's path through `vitalsStore.updateVitals`.
- `features/start/StartPage.tsx` — 2-step new-case form.
- `features/surgery/` — `SurgeryPage.tsx`, `SurgeryHeader`, `VitalsGrid.tsx` (vitals tiles), `WaveformChart.tsx`, `CameraOverlay` (exists but not imported anywhere — see P0).
- `features/review/ReviewPage.tsx` — Review & Sign-off (heavily reworked this session).
- `features/archive/ArchivePage.tsx` — session archive (Download PDF wiring fixed; "✓ Signed" label still fake — see Section 6).
- `features/calibration/CalibrationPage.tsx` — camera setup wizard (2 of 4 steps are real).
- `features/ocr-debug/OcrDebugPage.tsx` — fully scripted demo, no backend calls.
- `features/demo/DemoMode.tsx` — Demo Mode UI + vitals injection loop.
- `components/layout/TopNav.tsx`, `AppLayout.tsx` — shared chrome; `TopNav`'s End button fixed this session.
- `components/vitals/VitalCard.tsx` — the tile component (handles busy/disabled states correctly; the bug was always in the caller, not here).
- `design-system/` — `Button.tsx`, `Dialog.tsx`, `ConfidenceBadge.tsx`, `Timeline.tsx`, `Progress.tsx`, `tokens.ts` (neon OCR palette + clinical UI palette both live here).

**Backend (`vital/backend/app/`)**
- `main.py` — FastAPI app, CORS (LAN regex), router registration.
- `api/sessions.py` — session CRUD, pause/resume/end/notes/alarm-limits/alerts.
- `api/flagged.py` — flagged readings confirm/correct/dismiss + list flagged/audit.
- `api/chart.py` — `/chart`, `/sign` (requires completed status), `/report.pdf`.
- `api/pipeline.py` — `/read-frame`, `/push-frame/{session_id}`.
- `api/drugs.py` — drug log.
- `ws/vitals.py` — the WebSocket endpoint; `send_loop` (streams readings) + `receive_loop` (handles `trigger_nibp`/`ack_alert` from client).
- `pipeline/` — `detect.py`, `roi.py`, `ocr.py`; `validation/reconcile.py`.
- `sources/` — `camera.py` (reads from a per-session frame queue fed by `push-frame`), `replay.py` (synthetic/dataset replay), `frame_queue.py`, `onnx_engine.py` (Tier-2 stub, unused).
- `db/models.py`, `db/repo.py`, `db/session.py` — SQLAlchemy models + repo layer + SQLite session factory.
- `alerts/rules.py` — `check_alerts`, `AlertThrottle`, `build_alert` (mirrored in frontend's `alertRules.ts`).
- `chart/assemble.py`, `chart/pdf.py` — chart data assembly + ReportLab PDF generation.
- `models/review.py` — `FlaggedReading`, `AuditEntry` Pydantic models (camelCase JSON via `CamelModel`).

---

## 9. How To Run Locally (as currently set up on this dev machine)

**Backend** (no Docker on this machine — run directly via the pre-built venv):
```
cd backend
./.venv/Scripts/python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000
curl http://localhost:8000/health   # {"status":"ok"}
```
(The documented `docker compose up -d` path should also work if Docker is installed — it was not re-tested this session.)

**Frontend:**
```
npm install    # already done
npm run dev    # http://localhost:5173 (also reachable on LAN IPs per hostname)
```

**Backend tests:** `cd backend && ./.venv/Scripts/python.exe -m pytest tests/ simulator/tests/ -q` → 190 passed (unaffected by this session's frontend-only changes).

**Frontend typecheck/build:** `npx tsc --noEmit` (clean) / `npx vite build` (clean, ~660KB main bundle, single-chunk warning only — not an error).

**Browser-driven verification methodology used this session:** no browser-automation tool was built into the assistant's environment, so Playwright + Chromium were installed standalone into a scratch directory (outside the project, not added to `package.json`) and used to drive the real running app: real form fills, real clicks, real WS frame logging, real Network tab capture, plus independent `curl`/`fetch` calls against the backend to cross-check what the UI displayed against what the database actually contained. This is why the status table above says "verified" rather than "should work."

---

## 10. Summary For Whoever Picks This Up Next

The system's backend is solid — pipeline, persistence, validation, PDF generation, and the sign/lock invariant are all correct and were proven correct via direct `curl` calls independent of any frontend bug. Every bug found and fixed in Sessions 2–3 was a **frontend wiring problem**: mock data left in place instead of real fetches, event handlers left unwired, and one instance of two independently-plausible-looking pieces of code (End's navigation target + the sign endpoint's completed-status requirement) that combined to make an entire feature (signing) completely unreachable despite each half looking fine in isolation. The single biggest remaining gap is that **the camera never actually streams into the live dashboard** — Calibration proves the pipeline works on a single frame, but nothing wires that into the continuous WebSocket-driven Live Monitor experience that the product's whole pitch depends on.
