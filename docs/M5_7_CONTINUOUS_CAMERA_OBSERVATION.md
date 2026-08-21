# M5.7 — Continuous Camera Observation

**VITAL continuously observes the anaesthesia monitor through the camera for the
duration of an active operation and records only confirmed camera-derived
observations into the case timeline.** Calibration verification is configuration
metadata — it proves the six ROIs and OCR configuration work — and is never treated
as patient history.

This is a correction to the existing product architecture, not a new research
milestone: every subsystem below already existed and already ran continuously (see
`docs/VITAL_LIVE_CAMERA_TRACKING_ARCHITECTURE_ANALYSIS.md`). The work is one
out-param on `reconcile()`, a persistence-cadence gate in `send_loop`, two nullable
columns, one GET route, and frontend wiring — it opens no new evaluation question
and needed no new dataset.

## The invariant

```
Calibrate once → Start operation → camera continuously observes
→ values continuously accumulate → End operation → complete timeline in Archive
```

There is no operator action that produces a reading. "Verify" exists only inside
Calibration, to prove the six ROIs map to the intended monitor fields. Once the
operation starts: camera on, OCR continuous, tracking continuous, reconciliation
continuous, ledger live, persistence continuous, Archive accumulating — until the
operator explicitly ends it.

## Core change: LIVE STATE vs OBSERVED TIMELINE

| | LIVE STATE | OBSERVED TIMELINE |
|---|---|---|
| What | what the UI displays | what the camera genuinely confirmed |
| Held values | allowed, marked stale | never |
| Cadence | every processed frame | confirmed-only, ≥1 s or on-change |
| Transport | WebSocket `reading` envelope | SQLite `vital_readings` |
| Consumers | vital cards, camera status | ledger, Archive, Review, PDF |

`reconcile()` already computed per-field accept/hold/baseline and discarded it.
M5.7 surfaces it via an in-place output dict — the same pattern this codebase
already used for `temporal_state` and `crop_integrity`, chosen because every
existing call site unpacks exactly 3 return values. The 3-value contract is
unchanged; every call site that doesn't pass `field_status` is byte-for-byte
identical to before this parameter existed.

## Backend changes

- **`app/validation/reconcile.py`** — `field_status: Optional[Dict[str, str]] = None`
  out-param, filled `'confirmed' | 'held' | 'baseline'` from the existing
  accept/reason branches. No logic change.
- **`app/ws/vitals.py`** — `_PersistenceGate` (fires on the first tick, then on
  ≥1000 ms elapsed OR any confirmed value changed); `send_loop` now:
  - sends `fieldStatus` + `lastConfirmedAt` on every `reading` envelope (additive),
  - persists only confirmed fields, gated, **for `source_tag == 'camera'` only**,
  - evaluates alerts against the confirmed subset only, for **every** source.

  Non-camera sources (`synthetic`/`pipeline`/omitted `source_tag`) persist exactly
  as before M5.7 — the full reconciled reading, every tick, no gate. This is a
  deliberate scoping decision: the defect was specifically that camera-sourced rows
  could silently be fabricated held/baseline values; camera is the only source a
  real operator's laptop drives; and frozen milestone evidence (M4.3 Test H, M4.5
  Config B, M5.4's own temporal-corroboration persistence test) already asserts on
  the exact pre-M5.7 shape for synthetic/replay sources.
- **`app/db/models.py`** — `VitalReadingRow.source` (`'camera'|'synthetic'|'replay'`,
  nullable) and `.field_status` (JSON, nullable). `Base.metadata.create_all` adds
  tables but not columns, so **`app/main.py`** gained an idempotent
  `_add_missing_columns()` guard (`ALTER TABLE … ADD COLUMN`), verified against a
  copy of the pre-M5.7 production `vital.db`.
- **`app/db/repo.py`** — `save_reading`/`list_readings` carry `source`/
  `field_status`; `list_readings` gained `since_ms`/`limit`; `end_session` and
  `chart/assemble.build_chart` prefer `source == 'camera'` rows for their summary,
  falling back to all rows when a session has none tagged (covers synthetic
  sessions and any pre-M5.7 row); a new `_observation_stats()` computes Archive's
  real `ObservationStats` (`readingsCount`, `confirmedObservations`,
  `avgConfidence`, `source`) on read.
- **`app/chart/assemble.py`** — `_nearest_reading` (one nearest ROW) replaced with
  per-field nearest-non-null, because M5.7 rows are sparse by design; a single
  "nearest row" could have `hr` set and `spo2` null even when a closer SpO₂
  observation existed one row away.
- **`app/api/sessions.py`** — `GET /api/sessions/{id}/readings?since=&limit=`, the
  OBSERVED TIMELINE read path. Works for a completed session too (Archive/Review).

No existing safety rule was touched: confidence gate, range/jump validation, crop
integrity, motion-withhold (`calibrated_roi._withhold_all()`), calibration
verification, layout tracking, `TEMPORAL_CORROBORATION` default off, camera/Demo
Mode isolation are all unchanged. The M5.6 camera-nudge/confidently-wrong finding
is not addressed here — the existing tracking-failure withhold already covers gross
motion and needed no new work in this pass.

## Frontend changes

- **`src/features/operation/`** (was `src/features/surgery/`) — `OperationPage`
  (was `SurgeryPage`), route `/operation` (`/surgery` redirects). New
  `CameraFeedPanel` (the visible camera feed + calibrated ROI overlay + layout-lock
  indicator, replacing the old off-screen-clipped `<video>`) and `LedgerPanel` (the
  live observation ledger). `OperationHeader` (was `SurgeryHeader`). The old
  `WaveformsPanel` (synthetic ECG/pleth/capnography derived from `current.hr`,
  representing no camera-observed data) is deleted, along with the now-unused
  `WaveformChart.tsx`/`waveformGenerators.ts`.
- **`src/features/operation/CameraCaptureController.tsx`** (new) — owns
  `useCameraStreaming`, mounted once at the app root (`src/App.tsx`'s `AppShell`,
  above `<Routes>`) so the capture/push loop survives navigation to Review/Archive
  instead of being torn down and reopened by whichever page happens to be mounted.
  `useVitalsSimulation()` moved to the same app-root owner for the same reason.
- **`src/store/cameraStreamStore.ts`** — gained `mediaStream`, published by
  `useCameraStreaming` so any page can attach the *same* stream to its own visible
  `<video>` without a second `getUserMedia` call.
- **`src/store/vitalsStore.ts`** — `fieldStatus`, `lastConfirmedAt`,
  `observations[]` (append-only, confirmed-fields-only), `appendObservationFromTick`,
  `hydrateObservations` (merges `GET /readings` with anything already appended
  live, keyed on timestamp). Alert-checking now runs on a confirmed-only view via
  `confirmedFieldsOnly` — a held field can no longer re-raise an alert every tick.
- **`src/lib/ledger.ts`** (new) — `deriveLedger`, the pure projection from a row
  list to "one entry per field per genuine value change." Used identically for live
  appends and for `GET /readings` hydration, and reused by Review's Vitals Timeline
  tab — one projection, one source of truth, no second write path.
- **`src/components/vitals/VitalCard.tsx`** / `VitalsGrid.tsx` — `fieldStatus`/
  `lastConfirmedAt` props render `✓ Confirmed hh:mm:ss` vs `⏸ Held · last confirmed
  hh:mm:ss`; a held value is never styled as fresh. NIBP's card aggregates its 3
  underlying fields (confirmed if any of the three was confirmed this tick).
- **`src/features/calibration/CalibrationPage.tsx`** — `onStartCase` now
  `navigate(activeSession ? '/operation' : '/start')` — the exact bug from the
  rehearsal.
- **`src/store/sessionStore.ts`** — `zustand/persist` for
  `{activeSession, cameraMode, cameraSourceMode}`; `rehydrateActiveSession()`
  re-confirms against `GET /api/sessions/{id}` before any camera/WS owner acts on
  a reload-restored session. The MediaStream itself cannot survive a reload (the
  browser drops it) — the workspace surfaces "Reconnect camera" and resumes
  against the *same* session once granted.
- **`src/features/archive/ArchivePage.tsx`** — replaced the hardcoded
  `'3,847 frames' / '94.2%' / '4 items'` (two separate panels) with
  `session.observationStats` + real `flaggedCount`.
- **`src/features/review/ReviewPage.tsx`** — new "Vitals Timeline" tab, real
  `GET /readings` data through the same `deriveLedger`.
- **`src/components/layout/TopNav.tsx`** — "Live Monitor" (always shown) replaced
  with "Active Operation" (shown only while a session is active — the way back
  into the workspace, not a standalone dashboard).

## Testing

Backend: **397/397 passing** (`pytest tests/ -q`), including 17 new/extended
`reconcile()` field-status tests, 10 persistence/alert tests (all-held tick writes
nothing, partial confirmation persists only confirmed fields, the cadence gate,
`source` tagging, non-camera persistence unchanged, alerts never fire from a held
value), 5 `GET /readings` tests, and a sparse-row `build_chart` test. Frontend has
no test runner in this repo; `deriveLedger` is written pure for when one is added,
and `tsc --noEmit` / `vite build` are the typecheck/build gates — both clean.

## E2E

`app/eval/tier2_data/m5_7_report/m5_7_e2e_script.py` — real uvicorn subprocess,
real HTTP, real standalone WebSocket client, 90 real frames from
`app/eval/tier2_data/dense_B/` (a genuine GE CARESCAPE B650 recording, byte-verified
against `dense_B_anchors/anchor_004971.png`) pushed continuously over one WS
connection. **21/21 checks passed**: every frame produced a reading; the envelope
carries `fieldStatus`/`lastConfirmedAt`; at least one calibrated field showed
multiple distinct confirmed values live *and* persisted (HR: 86 → 7 across the
run — genuine OCR variation on real footage, not a frozen value); only 8 of 90
ticks were persisted (the confirmed-only gate correctly withheld the rest — real
OCR on unrehearsed footage frequently fails the confidence gate, which is the
safety system working, not a defect); every non-null persisted field was genuinely
`'confirmed'`; `GET /readings` matched SQLite row-for-row; ending the session
stopped persistence and `ArchivedSession.observationStats` matched the real count.

**Physical-webcam validation is not claimed** — this is footage-driven, pushed over
the same `push-frame` endpoint a browser uses, not through an actual `getUserMedia`
camera. The laptop-camera path itself needs the manual run below.

## Observation cadence (measured)

`CameraSource` still polls every 1 s and OCR still costs the dominant share of each
tick (~2.4 s at 1280×720 per earlier milestones) — unchanged by M5.7. The new
`_PersistenceGate` writes at most once per second, immediately on any confirmed
change, and never for an all-held/all-baseline tick. In the E2E run this meant 8
persisted rows across 90 processed ticks — bounded, and correctly sparse rather
than one row per tick regardless of content.

## Remaining limitations

- **B3 (tracked ROI boxes on the wire) was scoped down.** `calibrated_roi.py`'s
  observer callback (`on_tracking_result`) has a fixed one-argument contract relied
  on by 6 eval scripts and 2 test files. Rather than widen that contract, the
  visible ROI overlay draws the profile's own static normalized boxes plus the
  existing `tracking.locked` indicator — visually correct when tracking is locked,
  approximate (not per-frame-tracked) when it briefly isn't. A future pass could
  recompute the transformed boxes client-side from `TrackingResult.transform` +
  `reference_pixel_boxes`, both already public, without touching the observer
  contract at all.
- **The M5.6 camera-nudge/confidently-wrong finding is not re-addressed.** M5.7
  makes the pipeline's existing output *truthful about what it confirmed*; it does
  not change what OCR is willing to confirm.
- **`m5_6_e2e_script.py` (frozen M5.6 evidence) was not rerun.** Reasoning for why
  it remains valid is in `send_loop`'s own `source_tag` docstring: its assertions
  (`readings persisted > 0`, a critical alert persists) hold under M5.7's camera
  gating because the very first tick's confirmed fields are always written.
- **No frontend test runner exists in this repo**, so `deriveLedger` and the new
  store logic are covered by type-checking and manual verification, not unit tests.

## Verification commands

```
cd vital/backend && .venv/Scripts/python.exe -m pytest tests/ -q
cd vital/backend && .venv/Scripts/python.exe app/eval/tier2_data/m5_7_report/m5_7_e2e_script.py
cd vital && npx tsc --noEmit
cd vital && npm run build
```

Manual (real laptop camera, real monitor — not run as part of this pass, described
for the operator to perform): start a case → Calibration → draw/verify/save →
"Start a Case with This Profile" lands directly in Active Operation with the
camera already running → leave it untouched for ≥10 minutes, confirming values
update and the ledger accumulates with no operator action → navigate to
Review/Archive and back, confirming observation did not pause → cover the monitor,
confirming cards show Held and no new rows appear → refresh the browser, confirming
the session and ledger survive and the camera reconnects into the same session →
End Operation → Archive shows the complete real camera-derived history.
