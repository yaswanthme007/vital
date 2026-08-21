# VITAL — Intelligent Anaesthesia Documentation

**Computer vision that reads an anaesthesia monitor and turns it into a
digitised, medico-legal record — live, offline, no manual charting.**

VITAL points a camera at any patient monitor, is shown once where each vital
lives, then reads those vital signs directly off the screen, and streams them into a clinical dashboard — building an
auditable, signable, PDF-exportable anaesthesia record automatically instead
of relying on a clinician to transcribe numbers onto paper every few
minutes.

---

## Table of Contents

- [Overview](#overview)
- [Key Features](#key-features)
- [Architecture](#architecture)
- [Tech Stack](#tech-stack)
- [Getting Started](#getting-started)
- [Application Flow](#application-flow)
- [The Recognition Pipeline](#the-recognition-pipeline)
- [Project Structure](#project-structure)
- [Testing](#testing)
- [Known Limitations & Roadmap](#known-limitations--roadmap)
- [Documentation](#documentation)

---

## Overview

During surgery, an anaesthetist's vitals record is still, in most operating
theatres, a paper chart filled in by hand every few minutes while watching a
patient monitor. VITAL replaces that transcription step: a camera reads the
monitor's own display, digitises each vital sign in real time,
flags anything physiologically implausible for human review, and produces a
signed, tamper-evident PDF chart at the end of the case — all running
entirely offline, with no patient data ever leaving the device.

## Key Features

- **Camera-based vitals capture** — reads HR, SpO₂, NIBP, EtCO₂, temperature,
  and respiratory rate directly from a monitor's display.
- **Active Operation workspace** — the camera feed, six vitals tiles with
  confirmed/held status, and a live observation ledger update continuously for the
  whole case, with configurable alarm thresholds and audible/visual alerts on
  out-of-range CONFIRMED readings. **(M5.7)** — see
  [`docs/M5_7_CONTINUOUS_CAMERA_OBSERVATION.md`](docs/M5_7_CONTINUOUS_CAMERA_OBSERVATION.md).
- **Camera calibration workflow** — a guided ~15-second setup (connect camera →
  mark screen corners → draw each vital's display region → verify each reads
  correctly) run once per physical monitor. This is what makes VITAL work on any
  manufacturer's monitor rather than one known colour palette.
- **Review & sign-off** — every low-confidence OCR read is queued for human
  confirmation or correction, with the source frame shown alongside the
  reading, before a case can be signed.
- **Tamper-evident records** — once signed, a session is immutable; every
  correction is preserved in an append-only audit trail, not overwritten.
- **Automatic PDF generation** — a complete medico-legal chart (vitals table,
  drug log, event timeline, correction history, signature) generated on
  sign-off.
- **Session archive** — searchable history of every past case, with PDF
  export.
- **Fully offline** — no external services, no network calls at runtime;
  SQLite on local disk, Tesseract as a local OCR binary.
- **Demo Mode** — six pre-built clinical scenarios (normal case, hypotension,
  tachycardia, desaturation, hypercapnia, camera glare) for live
  demonstration without a physical monitor.

## Architecture

```
┌─────────────────────────┐         ┌──────────────────────────────┐
│   Frontend (Vite/React)  │  HTTP   │      Backend (FastAPI)         │
│                          │◄───────►│                                │
│  Live Monitor            │   WS    │  REST: sessions, drug log,      │
│  Calibration             │◄───────►│  flagged/audit corrections,     │
│  Review & Sign-off       │         │  chart, sign                    │
│  Archive                 │         │  WebSocket: streamed vitals      │
│  OCR Pipeline Inspector  │         │  (synthetic or replayed OCR)     │
└─────────────────────────┘         │                                │
                                     │  Pipeline: calibrated ROI +      │
                                     │  layout tracking → Tesseract     │
                                     │  OCR → validation/reconciliation │
                                     │                                │
                                     │  SQLite (sessions, readings,     │
                                     │  drug events, audit trail)       │
                                     │  ReportLab (PDF generation)      │
                                     └──────────────────────────────┘
```

The backend is designed to run on an offline edge box: no external services,
no calls out, SQLite on local disk, Tesseract as a local binary. Every
reading is run through a deterministic validation layer
(`app/validation/reconcile.py`) before persisting, so a bad OCR read gets
held or flagged rather than trusted outright.

## Tech Stack

**Frontend**
- React 19 + TypeScript, built with Vite
- Zustand for state management
- Tailwind CSS + a small custom design system
- Framer Motion for animation
- uPlot for live waveform rendering
- React Router

**Backend**
- FastAPI (REST + WebSocket)
- OpenCV + Tesseract OCR for the vision pipeline
- SQLAlchemy + SQLite
- ReportLab for PDF generation
- Docker / Docker Compose for deployment

## Getting Started

### Prerequisites

- Node.js 18+ and npm
- Docker + Docker Compose (for the backend)

### Frontend

```bash
npm install
npm run dev
```

The app runs at `http://localhost:5173`.

### Backend

```bash
cd backend
docker compose build
docker compose up -d

curl http://localhost:8000/health          # {"status":"ok"}
docker compose ps                          # STATUS should read "healthy" within ~15s
```

Records persist in the `vital_data` named volume across restarts.
`docker compose down -v` wipes it; `docker compose down` (no `-v`) keeps it.

Seed a fully-populated demo session directly inside the container:

```bash
docker compose exec backend python scripts/seed_demo.py
```

See [`backend/README.md`](backend/README.md) for the full backend setup
(local non-Docker install, Tesseract system dependency, running tests, the
synthetic-data simulator, and the OCR accuracy eval harness).

### Testing on a second device

Calibration's camera-verification step can capture from a physical camera
*or* directly from another browser tab/window via screen sharing — useful
for testing the OCR pipeline against VITAL's own Live Monitor screen without
needing a second physical device. To reach the dev server from a phone on
the same WiFi network, start Vite with `--host` (already the default in
`vite.config.ts`) and open `http://<your-LAN-IP>:5173` on the other device.

## Application Flow

1. **New Case** — enter patient ID, procedure, anaesthetist, and ASA class.
2. **Calibration** *(one-time per physical monitor)* — connect the camera,
   detect the monitor boundary, correct for perspective, map each vital's
   display region, and verify OCR accuracy against a live frame.
3. **Active Operation** — the camera stays on and continuously observes the
   monitor for the whole case; vitals update and the observation ledger fills in
   as each field is genuinely confirmed. Alerts fire automatically on out-of-range
   CONFIRMED readings, never on a held/stale value.
4. **Review & Sign-off** — confirm or correct any flagged readings, then
   sign the case to lock it as an immutable, tamper-evident record.
5. **Archive** — find any past session and export its signed PDF chart.

`OCR Debug` (in the top navigation) is a step-by-step inspector of the
pipeline itself — capture → preprocess → detect → warp → extract → OCR →
validate → output — useful for understanding or demonstrating how a frame is
actually processed.

## The Recognition Pipeline

VITAL locates each vital sign from a **calibration profile** — a one-time, ~15-second
setup per physical monitor in which the operator marks the screen's four corners and
draws a box around each vital's display slot, then confirms that each box reads
correctly. During a case those boxes are re-anchored every frame by tracking the
monitor's static on-screen chrome, so the reading survives camera drift.

```
CALIBRATE (once)   corners -> homography · boxes -> ROIs · verify -> operator sign-off
LIVE (1 Hz)        frame -> track layout -> apply ROIs -> OCR -> confidence -> reconcile
```

This is a deliberate design choice, not a shortfall. What distinguishes one vital
field from another on a monitor is **position and the printed label beside it** —
information a cropped, colour-stripped image simply does not contain. An earlier
architecture that tried to infer field identity from crop appearance alone was
measured at 4.3% on an unseen monitor, and was *more* confident when wrong than when
right. Being told the layout once, by a clinician, is both more accurate and more
defensible: every region was seen, verified, and signed off before the case started.

Calibration makes VITAL **monitor-agnostic after setup** rather than universally
monitor-agnostic without it — and it requires no training data, runs on CPU, and
stays fully offline.

Full rationale and the measurements behind it: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md),
[`docs/EVIDENCE.md`](docs/EVIDENCE.md).

## Project Structure

```
vital/
├── src/                        # Frontend (React + TypeScript)
│   ├── features/                # Route-level pages
│   │   ├── landing/
│   │   ├── start/                # New Case
│   │   ├── calibration/
│   │   ├── operation/              # Active Operation (M5.7; was surgery/)
│   │   ├── review/                # Review & Sign-off
│   │   ├── archive/
│   │   ├── ocr-debug/             # Pipeline inspector
│   │   └── demo/                  # Demo Mode
│   ├── components/               # Shared UI (layout, vitals widgets)
│   ├── design-system/            # Tokens + base components
│   ├── store/                    # Zustand stores
│   ├── hooks/
│   └── lib/                      # API client, alert rules, utils
├── backend/
│   ├── app/
│   │   ├── api/                  # REST routes
│   │   ├── ws/                   # WebSocket vitals stream
│   │   ├── pipeline/              # detect → ROI → OCR
│   │   ├── validation/            # reconciliation / plausibility checks
│   │   ├── alerts/                # threshold rules
│   │   ├── chart/                 # PDF generation
│   │   └── db/                    # SQLAlchemy models + repo
│   ├── simulator/                 # Synthetic training/eval data generator
│   ├── tests/
│   └── scripts/                   # seed_demo.py, demo_run.py
└── vite.config.ts
```

## Testing

**Frontend**

```bash
npx tsc --noEmit
```

**Backend**

```bash
cd backend
pytest tests/ simulator/tests/
```

**End-to-end pipeline smoke test** (sessions, vitals streaming, drug log,
sign, PDF generation):

```bash
cd backend
python scripts/demo_run.py
```

**Direct OCR pipeline verification** (no UI, no camera — proves the
detect → rectify → colour-ROI → OCR round trip against a known frame):

```bash
docker compose exec backend python simulator/generate.py --seed 1 --out-dir /tmp/testframe
docker compose cp backend:/tmp/testframe/<generated-file>.png ./testframe.png
curl -X POST http://localhost:8000/api/pipeline/read-frame -F "file=@testframe.png"
```

## Known Limitations & Roadmap

- **Calibration is required per monitor.** VITAL reads any monitor after a ~15-second
  setup; it does not read an arbitrary monitor with zero setup. See
  [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for why that is the right trade.
- **Calibration must be redone if the camera is moved substantially.** Small drift is
  tracked automatically; a large move drops the tracking lock, at which point VITAL
  holds the last confirmed values and asks for recalibration rather than reading a
  wrong region.
- **The Active Operation workspace runs on the continuous live-camera feed once a
  case is calibrated for camera mode** — synthetic vitals and Demo Mode's scripted
  scenarios remain available for UI development/demonstration without hardware, and
  are isolated from camera-derived history: neither is ever written to a session's
  real observation timeline. See
  [`docs/M5_7_CONTINUOUS_CAMERA_OBSERVATION.md`](docs/M5_7_CONTINUOUS_CAMERA_OBSERVATION.md).
- **Single-language OCR.** Tesseract is configured for digits only.
- **Not clinically validated.** No claim is made about clinical deployment safety on
  any monitor.

**Active roadmap:** [`docs/ROADMAP.md`](docs/ROADMAP.md) — M5.1 (OCR confidence) ->
M5.2 (real calibration) -> M5.3 (layout tracking) -> benchmark -> promotion.

## Documentation

| Document | What it covers |
|---|---|
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Current and target recognition architecture, confidence model, safety posture |
| [`docs/ROADMAP.md`](docs/ROADMAP.md) | Milestones M5.1 to demo-ready, acceptance criteria, demo script |
| [`docs/EVIDENCE.md`](docs/EVIDENCE.md) | Every measurement the architecture decision rests on |
| [`docs/archive/`](docs/archive/) | Superseded M1-M5 milestone reports, retained as an audit trail. **Not current guidance.** |
| [`backend/README.md`](backend/README.md) | Backend setup, tests, simulator, eval harness |
