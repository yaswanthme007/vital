# VITAL — Camera Demo Runbook

The judge-facing demo: point a camera at an anaesthesia monitor, calibrate it
once, and stream real camera-derived vitals with per-field confidence, layout
tracking, alerts and a persisted record.

Rehearsed end-to-end in M5.6 against a real Chrome, a real `getUserMedia`
stream and the real backend — see
[`../docs/M5_6_FINAL_PRODUCTION_PROMOTION_REPORT.md`](../docs/M5_6_FINAL_PRODUCTION_PROMOTION_REPORT.md).
**Machine-paced, the whole flow completes in ~17 seconds.** Budget 3-4 minutes
narrating it at human pace.

The synthetic-feed + signed-PDF demo in [`DEMO.md`](DEMO.md) is the fallback if
anything here fails: it needs no camera and no calibration.

---

## Before you are on stage

```bash
# 1. backend
cd backend
.venv/Scripts/python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000

# 2. frontend (second terminal, from the repo root)
npm run dev
```

Then, **while you still have internet**, open `http://localhost:5173` once so
Chrome caches the two Google Fonts the UI uses. The app works without them; it
just looks different.

Confirm the backend is in the configuration that was validated:

```bash
curl -s http://localhost:8000/api/config/snapshot
```

Check three things in that output — this is the frozen M5.6 configuration:

| Field | Must read |
|---|---|
| `featureFlags.TEMPORAL_CORROBORATION.enabled` | `false` |
| `featureFlags.LAYOUT_TRACKING.enabled` | `true` |
| `models.fieldClassifierLoaded` / `digitCnnLoaded` | `false` / `false` |

**What you point the camera at.** Any anaesthesia monitor showing numbers. If
you do not have a physical monitor, the most reliable option is *Share a
Tab/Screen Instead* on the Calibration screen, pointed at a second browser tab
showing a monitor image — no camera, no lighting, no focus problems.

---

## The flow

### 1. Calibration → Camera

Open **Calibration**. Click **Connect Camera** (or *Share a Tab/Screen
Instead*). The badge turns **CAMERA ACTIVE** and the preview goes live.

> **Say:** "VITAL doesn't need an integration with the monitor. It watches the
> screen, the way an anaesthetist does."

### 2. Draw the six regions

Pick a vital on the left, then drag a box around it in the video. Repeat for
all six. The live crop preview updates as you draw.

**Draw the field's display SLOT, not today's digits.** This is the single
most important operator action in the product. A box drawn tight around a
2-digit `74` clips a later 3-digit `145` to `14` — measured, and the direct
cause of the only unresolved confidently-wrong class in the evidence base.

> **Say:** "This is human-in-the-loop by design. The operator tells it where to
> look once; nothing is trained, nothing is guessed."

### 3. Verify

Click **Run Verification**. Every drawn box is OCR'd by the real pipeline and
shown with its value and confidence. Tick **Confirm this is right** on each.

**Save stays disabled until every drawn field is confirmed.** Show that.

> **Say:** "It will not let me save a calibration nobody has checked."

### 4. Save → Start the case

Click **Save Profile**. The Complete screen reports the profile, the number of
regions, average verified confidence, and whether **layout tracking** is on.

Click **Start a Case with This Profile**, fill the form, **Begin Monitoring**.

> ⚠️ **Do not reload the browser during the demo.** The calibration profile
> lives in the database and survives, but this tab's camera mode does not — a
> reloaded tab silently falls back to synthetic vitals. The Live Monitor will
> say `Synthetic` in that case; if you see it, re-run Calibration.

### 5. Live Monitor

Within a few seconds the vitals populate from the camera. Point out:

- **Capture ON** in the header — real frames going up at ~1 Hz.
- **Per-field confidence bars** under each vital.
- **AI Vision · Layout locked** — the tracker is holding the monitor.

> **Say:** "Every number carries the confidence it was read at. Nothing is
> shown as fact that the system isn't willing to stand behind."

### 6. The refusal (this is the strongest moment — use it)

Find a field showing a low (red) confidence bar. On most monitors that is HR.

> **Say:** "It read this field correctly — 74 — but only at 67% confidence,
> under our 70% gate. So it refuses to confirm it, holds the last trusted
> value, and files it for review. A system that admits what it can't see is
> worth more clinically than one with a higher accuracy number."

Open **Review** to show the flagged entry with its actual confidence.

### 7. A critical vital

Change the monitor to a critical value (SpO₂ ≤ 90 is the most reliable — it
reads at 82-94% confidence). The vital card goes red, a **CRITICAL** alert
appears in the footer and the header alert count increments. It is persisted,
not just displayed.

### 8. End the case

**Review** → **End**. The case moves to sign-off; the record is in the
database with every reading, confidence and alert.

### 9. Optional closer — Demo Mode

**Demo Mode** in the top bar runs scripted scenarios with no camera at all.
Verified in M5.6 to push **zero** camera frames and open **zero** camera
WebSockets, so it is a safe fallback mid-demo.

---

## What NOT to do on stage

**Do not nudge or move the camera to show off tracking.** The tracker itself
holds lock (100% in M5.6's stress test), but OCR reading a motion-resampled
crop misreads the digit `8` as `3` — `38`→`33`, `98`→`93` — at 71-90%
confidence, which clears the confirmation gate. Measured: 38 confidently-wrong
confirmations across a 101-frame nudge. **EtCO₂ starts failing at about 7px of
movement.** See §13 of the M5.6 report.

If you want to show tracking, say it instead: the lock badge stays green and
the tracker reports scale/rotation. Do not invite a judge to compare the
on-screen numbers to the monitor while the camera is moving.

---

## If something goes wrong

| Symptom | Do this |
|---|---|
| Live Monitor says `Synthetic` | The tab lost camera mode (usually a reload). Re-run Calibration and start a new case. |
| `Couldn't send camera frame` toast | Backend is down. Restart uvicorn; the WebSocket reconnects on its own. |
| Camera won't connect | Use *Share a Tab/Screen Instead* — no device permissions involved. |
| Verify shows wrong values | The boxes are wrong. Go **Back**, redraw the offending field wider, re-verify. Never save an unconfirmed field. |
| Nothing reads at all | Check the monitor fills enough of the frame. Below ~640×360 effective resolution, OCR confidence rarely clears the 70 gate and everything is held. |
| Total loss of confidence | Fall back to [`DEMO.md`](DEMO.md)'s synthetic + signed-PDF runbook. It needs nothing you have just been fighting with. |

---

## What you may and may not claim

**May:** "Validated on the available real-world monitor video datasets and the
real application pipeline."

**May not:** clinically validated · medically certified · approved for patient
care · diagnostically accurate.

It is a technical demonstration and prototype. The known limitations are in
§15 of the M5.6 report — including that all real camera-motion evidence is one
54-second recording of one monitor, and that no physical-webcam demo has ever
been run by this project.
