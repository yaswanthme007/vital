# VITAL Backend

Offline anaesthesia vitals digitizer — reads a photo of a patient monitor and
turns it into structured, validated, signable vitals records.

## Architecture

FastAPI serves a REST API (sessions, drug log, flagged/audit corrections,
chart, sign) and a WebSocket (`/ws/vitals/{session_id}`) that streams either
synthetic vitals or real OCR reads (`app/pipeline`: screen detect → ROI
extract → Tesseract) replayed from a captured dataset; every tick is
run through a deterministic validation layer (`app/validation/reconcile.py`)
before persisting, so a bad OCR read gets held/flagged rather than trusted
outright. Data lands in SQLite via a thin repo layer (`app/db/repo.py`) —
sessions, readings, drug events, and an append-only audit trail. Once a
session is ended and a clinician signs it (`POST /sessions/{id}/sign`), the
session becomes immutable (`assert_not_signed()` guards every mutator) and a
medico-legal PDF (chart + drug log + complete correction history +
signature) is generated with ReportLab. The whole thing is designed to run
on an offline edge box: no external services, no calls out, SQLite on local
disk, Tesseract as a local binary.

## Quickstart (Docker — the intended way to run this)

Requires Docker + Docker Compose. Building the image needs internet once (to
fetch Python/apt packages); the running container makes **no** network calls
at runtime.

```bash
docker compose build
docker compose up -d

curl http://localhost:8000/health          # {"status":"ok"}
docker compose ps                          # STATUS should read "healthy" within ~15s
```

Seed a fully-populated demo session (30 readings across a simulated
15-minute case, 3 drugs, a note) directly inside the container:

```bash
docker compose exec backend python scripts/seed_demo.py
```

This prints a session ID and the exact `curl` commands to fetch its chart,
sign it, and download the signed PDF — see "Generating a populated PDF"
below for what those steps look like end-to-end.

Records persist in the `vital_data` named volume across restarts
(`docker compose restart` / `down` + `up` again); `docker compose down -v`
wipes it.

```bash
docker compose down          # stop, keep data
docker compose down -v       # stop, wipe the volume
```

## Generating a populated PDF

Whether via Docker (`docker compose exec backend ...`) or a local venv, the
sequence is the same — create/seed a session, end it, sign it, download the
PDF:

```bash
python scripts/seed_demo.py                 # prints SESSION_ID and next steps
curl -X POST http://localhost:8000/api/sessions/<SESSION_ID>/sign \
  -H "Content-Type: application/json" \
  -d '{"author": "Dr. Priya Sharma", "signatureMethod": "pin"}'
curl http://localhost:8000/api/sessions/<SESSION_ID>/report.pdf -o report.pdf
```

`scripts/seed_demo.py` already ends the session for you (status
`"completed"`) — only the `sign` step above is left. Signing before
`end`ing, or signing twice, both return `409`.

## WebSocket smoke test

`smoke_ws.py` connects to the vitals WebSocket and pretty-prints each
message as it streams — a throwaway dev tool, not part of the app, run from
the host against a running server (Docker or local):

```bash
pip install websockets   # only needed to run this script standalone
python smoke_ws.py                                              # synthetic demo, default session
python smoke_ws.py "ws://localhost:8000/ws/vitals/demo-live?source=synthetic"
```

## Local (non-Docker) setup

```bash
python -m venv .venv
source .venv/Scripts/activate   # Windows Git Bash; use .venv\Scripts\activate.bat for cmd.exe
pip install -r requirements.txt
```

### System dependency: Tesseract OCR

`app/pipeline/ocr.py`'s `TesseractEngine` uses [pytesseract](https://pypi.org/project/pytesseract/),
which is only a thin wrapper around the **system** Tesseract binary — `pip install pytesseract`
does not install Tesseract itself. Without it, any code path that calls
`TesseractEngine.read_vital()` (including `read_frame.py` and the eval
harness) will raise `pytesseract.TesseractNotFoundError`. The Docker image
installs this via `apt-get`; for local (non-Docker) runs, install it
separately:

- **Windows**: `winget install --id UB-Mannheim.TesseractOCR` (or the
  [UB-Mannheim installer](https://github.com/UB-Mannheim/tesseract/wiki) directly).
  It typically lands in `%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe`
  or `C:\Program Files\Tesseract-OCR\tesseract.exe`.
- **macOS**: `brew install tesseract`
- **Linux**: `apt install tesseract-ocr` (or your distro's equivalent)

`TesseractEngine` auto-detects the binary in this order: an explicit
`tesseract_cmd` argument, the `TESSERACT_CMD` env var, `tesseract` on `PATH`,
then a few common install locations per OS. If none of those find it, set
`TESSERACT_CMD` to the full path of `tesseract`/`tesseract.exe` explicitly.

## Running (non-Docker)

```bash
uvicorn app.main:app --reload
```

## Tests

```bash
pytest tests/ simulator/tests/
```

## Simulator + eval harness

`simulator/generate.py` renders synthetic monitor frames with known
ground-truth labels (see `simulator/generate.py --help`). `app/eval/harness.py`
runs the OCR pipeline (`app/pipeline/detect.py` -> `roi.py` -> `ocr.py`) over
a generated dataset and reports accuracy against that ground truth:

```bash
python simulator/generate.py --id my_dataset --layout random --augment random --count 100 --seed 0
python -m app.eval.harness --dataset simulator/out/my_dataset
```
