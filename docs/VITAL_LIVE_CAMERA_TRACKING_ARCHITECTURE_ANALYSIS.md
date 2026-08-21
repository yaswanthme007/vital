# VITAL — Continuous Camera Observation: Architecture Analysis (pre-M5.7)

Written during the M5.6 manual rehearsal, when calibrating and starting a case
produced what looked like a single frozen vital-sign value instead of a live,
continuously-updating patient timeline. This document is the trace behind M5.7
(`docs/M5_7_CONTINUOUS_CAMERA_OBSERVATION.md`) — what the architecture actually did,
why, and the smallest correct fix. All findings below were verified against the
running backend and the actual `backend/vital.db` on the machine where the
rehearsal happened, not inferred from reading code alone.

## The headline

**The continuous backend pipeline already existed and already ran on every pushed
frame.** `CameraSource → read_frame → calibrated_roi + LayoutTracker → OCR →
crop-integrity → reconcile → WebSocket → alerts → SQLite` was wired end-to-end
before this investigation started. Nothing needed to be built from scratch. Two
specific defects made it *behave* as if calibration verification was the only
thing the product could do.

## Current architecture / data flow (as built, pre-M5.7)

```
BROWSER (SurgeryPage only)                    BACKEND
────────────────────────────                  ─────────────────────────────────────
useCameraStreaming (1 Hz setInterval)
  useCameraCapture.captureFrameBlob()
  api.pushFrame(sessionId, blob)  ──POST──►  /api/pipeline/push-frame/{channel}
                                               frame_queue.push_frame()   [latest-wins]
                                                       │
useVitalsSimulation                                    ▼
  new WebSocket(?source=camera)  ◄──WS──►    /ws/vitals/{session_id}
                                               _camera_roi_extractor()
                                                 repo.get_active_calibration_profile()
                                                 LayoutTracker.from_reference_image()  [once/conn]
                                               CameraSource.stream()
                                                 poll seq → asyncio.to_thread(_read)
                                                   read_frame(img, roi_extractor, crop_integrity)
                                                     calibrated_roi → tracker.track()
                                                       not locked → WITHHOLD ALL FIELDS
                                                     OCR per ROI → per-vital confidence
                                               send_loop()
                                                 reconcile(raw, conf, confirmed_state, crop_suspicious)
                                                   range → jump → confidence gate
                                                   accept → advance confirmed
                                                   reject → HOLD last confirmed / DEFAULT_BASELINE
                                                 ├─► send_json {type:'reading', reading, confidence}
                                                 ├─► _persist_reading()   ← EVERY TICK, held values included
                                                 ├─► check_alerts(reading) → _persist_alert()
                                                 └─► _persist_flagged() per flagged group
vitalsStore.updateVitals()
  current + history[360]  (IN MEMORY ONLY — lost on refresh)
VitalsGrid / WaveformsPanel / CameraOverlay
```

Measured cadence from `backend/vital.db` (session `SESSION-1787206375853-9n6j`):
ticks at 11:43:01, :04, :07, :11, :14, :18 — **~3.0–3.6 s apart**.
`CameraSource.stream()` sleeps `interval=1.0 s` *and* `read_frame` costs ~2.4 s per
frame at 1280×720. The OCR cost is the real throttle; the loop was nowhere near
saturating.

## Defect 1 — the navigation loop

`CalibrationPage.tsx`'s `onStartCase={() => navigate('/start')}` routed
unconditionally to the New Case patient-info form, **even when a session was
already active**. Frame pushing and the camera WebSocket both lived inside
`SurgeryPage`; while the operator was on `/calibration` or `/start`, zero frames
were pushed and no WS was open. This is the exact loop the rehearsal hit: enter
patient info → camera → draw boxes → verify → save → **New Case form again**.

## Defect 2 — persisted rows were `reconcile()`'s display output

`send_loop` called `_persist_reading(session_factory, session_id, reading, ...)`
where `reading` is the reconciled *display* value — for a held field, the prior
confirmed value (or `DEFAULT_BASELINE`), repeated every tick regardless of whether
anything was genuinely re-confirmed. The captured rehearsal session
(`SESSION-1787206375853-9n6j`) wrote exactly 6 rows, byte-identical:

```
11:43:01  hr=75  spo2=98  nibp=120/78  etco2=38  temp=37  rr=0   conf=45.3  ai_low
11:43:04  hr=75  spo2=98  nibp=120/78  etco2=38  temp=37  rr=0   conf=13.8  ai_low
…×6, identical
```

Those are `reconcile.DEFAULT_BASELINE` verbatim. The matching `flagged_readings`
rows explain why: `hr: OCR could not read a value this tick`,
`spo2: 71 is an implausible jump from the last confirmed 98`,
`spo2: 2 is outside the physiologically plausible range`,
`nibpSystolic/Diastolic: unreadable`. **Not one field was ever confirmed that
session** — yet six clinically-plausible, complete rows were written to
`vital_readings`, and `repo.end_session`'s summary honestly averaged them into
"Avg HR 75.234…". Garbage in, honest arithmetic out.

## Answering the original architecture questions

| Q | Finding |
|---|---|
| **A.** Where does calibration verification OCR go? | `POST /api/calibration/verify` → `read_frame()` → returned to the browser only. **No DB write, no `reconcile()`, no alerts.** Operator confirmation writes `CalibrationFieldMeta.verified/verifiedValue/verifiedConfidence` onto the *profile*, which that model's own docstring says is never consulted by any confidence gate. Already correct pre-M5.7 — only the navigation handoff needed fixing. |
| **B.** Is verification persisted as a vital reading? | No. |
| **C.** Where are live readings generated? | `CameraSource._read()` → `send_loop()`. |
| **D.** Are live readings persisted? | Yes — every tick, unconditionally (Defect 2). |
| **E.** Does the WS stream continuously? | Yes — one `reading` envelope per processed frame, plus `alert`/`flagged`, plus the M5.3 `tracking` lock envelope. |
| **F.** Does the frontend receive continuous readings? | Yes, and renders them correctly — but never stored them durably; `vitalsStore.history` was memory-only and nothing hydrated it from the backend. |
| **G.** Every frame or one frame? | Every frame; `frame_queue` is latest-wins so `CameraSource` never falls behind. |
| **H.** Archive real or mock? | Session list + `vital_summary`: real arithmetic over (pre-M5.7, fabricated) rows. The "AI Processing" panel (`'3,847 frames' / '94.2%'`) was a hardcoded fixture, identical for every session. |
| **I.** What does the schema already support? | `vital_readings` already had a per-session FK, a `(session_id, timestamp)` index, 8 nullable numeric fields, `timestamp`, `confidence`, `provenance`, `per_vital_confidence` — already a time-series table, just missing a *source* tag and a *confirmed* marker. |
| **J.** What's missing for a genuine live timeline? | (1) a confirmed/held distinction at the persistence boundary — `reconcile()` computed it internally and discarded it; (2) an HTTP read path — `repo.list_readings` existed but nothing exposed it; (3) a durable frontend ledger; (4) stale/held affordance in the UI; (5) alerts gated on confirmed values, not display values; (6) the navigation fix; (7) a `source` tag so camera/synthetic/demo rows could never be mixed. |

## What M5.7 changed

See `docs/M5_7_CONTINUOUS_CAMERA_OBSERVATION.md` for the implementation. In one
sentence: `reconcile()` gained an in-place `field_status` out-param (confirmed /
held / baseline, same pattern as `temporal_state`/`crop_integrity`), `send_loop`
now persists only confirmed camera fields on a ≥1s-or-changed cadence
(`_PersistenceGate`), `vital_readings` gained nullable `source`/`field_status`
columns, `GET /api/sessions/{id}/readings` exposes the timeline, and the frontend
gained a live observation ledger, held/stale vital cards, and an app-root camera/WS
owner so navigation no longer pauses observation. Every M5.1–M5.6 safety decision
— confidence gate, range/jump validation, crop integrity, motion withholding,
calibration verification, `TEMPORAL_CORROBORATION` off, camera/Demo Mode isolation
— is unchanged.
