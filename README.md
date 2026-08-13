# VITAL — Intelligent Anaesthesia Documentation

**Computer vision that reads an anaesthesia monitor and turns it into a
digitised, medico-legal record — live, offline, no manual charting.**

VITAL points a camera at a patient monitor, reads the vital signs directly
off the screen, and streams them into a clinical dashboard — building an
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
- [The OCR Pipeline](#the-ocr-pipeline)
- [Project Structure](#project-structure)
- [Testing](#testing)
- [Known Limitations & Roadmap](#known-limitations--roadmap)

---

## Overview

During surgery, an anaesthetist's vitals record is still, in most operating
theatres, a paper chart filled in by hand every few minutes while watching a
patient monitor. VITAL replaces that transcription step: a camera reads the
monitor's own colour-coded display, digitises each vital sign in real time,
flags anything physiologically implausible for human review, and produces a
signed, tamper-evident PDF chart at the end of the case — all running
entirely offline, with no patient data ever leaving the device.

## Key Features

- **Camera-based vitals capture** — reads HR, SpO₂, NIBP, EtCO₂, temperature,
  and respiratory rate directly from a monitor's display.
- **Live clinical dashboard** — real-time waveforms (ECG, pleth, capnography)
  and vitals tiles, with configurable alarm thresholds and audible/visual
  alerts on out-of-range readings.
- **Camera calibration workflow** — a guided setup (connect camera → detect
  monitor boundary → correct perspective → map vital regions → verify OCR
  accuracy) run once per physical monitor.
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
                                     │  Pipeline: screen detect →      │
                                     │  colour-ROI extract → Tesseract  │
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
3. **Live Monitor** — real-time vitals and waveforms stream in; alerts fire
   automatically on out-of-range readings.
4. **Review & Sign-off** — confirm or correct any flagged readings, then
   sign the case to lock it as an immutable, tamper-evident record.
5. **Archive** — find any past session and export its signed PDF chart.

`OCR Debug` (in the top navigation) is a step-by-step inspector of the
pipeline itself — capture → preprocess → detect → warp → extract → OCR →
validate → output — useful for understanding or demonstrating how a frame is
actually processed.

## The OCR Pipeline

VITAL's vision pipeline finds each vital sign by colour, not generic text
recognition: HR, SpO₂, NIBP, and EtCO₂ are each rendered in an exact,
known colour, and the pipeline isolates pixels matching that colour before
running Tesseract on just that region. Preprocessing automatically detects
whether the source is a dark monitor screen or a light dashboard and
normalises polarity accordingly, so it isn't limited to one visual style of
source.

This is a deliberate, honestly-scoped **Tier-1** approach: it is provably
correct for any screen rendering in VITAL's known colour palette (verified
directly against the API — a rendered test frame reads back at 90%+
confidence across every vital), but it does not yet generalise to arbitrary
real-world monitors from other manufacturers, which use different colour
conventions. A **Tier-2** path — a trained model generalising across monitor
brands and lighting conditions — is scoped as a deliberate next step, not
pretended to already exist (`backend/app/pipeline/onnx_engine.py`).

## Project Structure

```
vital/
├── src/                        # Frontend (React + TypeScript)
│   ├── features/                # Route-level pages
│   │   ├── landing/
│   │   ├── start/                # New Case
│   │   ├── calibration/
│   │   ├── surgery/               # Live Monitor
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

- **Colour-dependent OCR** — the pipeline currently requires the source
  screen to render vitals in VITAL's known colour palette; it does not yet
  read arbitrary real-world monitors with different colour conventions.
  Planned: per-installation colour calibration, then a trained model
  (`onnx_engine.py`) for cross-manufacturer generalisation.
- **Live Monitor is currently demo-data driven** — the real-time dashboard
  is fed by a synthetic vitals generator (or Demo Mode's scripted scenarios)
  rather than a continuous live-camera feed; the camera→OCR round trip is
  proven via Calibration's single-frame Verify step and the direct API test
  above, not yet wired as a continuous streaming source.
- **Single-language OCR** — Tesseract is configured for plain digits only.
