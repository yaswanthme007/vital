# VITAL — Stage-2 Demo Runbook

Follow this verbatim. Every command is copy-pasteable; nothing here needs
editing except the WS URL `demo_run.py` prints at the end.

## One-time setup (do this before you're on stage)

```bash
docker compose build
```

Confirm it built:

```bash
docker images | grep vital-backend
```

You should see `vital-backend  latest  ...`. Do this well ahead of time —
it needs internet to fetch packages once; nothing after this point does.

## The live sequence

Run these three steps, in order, once you're in front of the audience.

### 1. Bring the box up

```bash
docker compose up -d
docker compose ps
```

Wait for the `STATUS` column to say **`healthy`** (not just "Up") — usually
5-15 seconds.

> **Say:** "This is the whole backend running in one container, fully
> offline — no cloud, no external API calls. It's a Docker box you could
> hand to a hospital IT department and it would run on their network with
> zero internet access."

### 2. Run the demo driver

```bash
python scripts/demo_run.py
```

This does everything in one shot: waits for health, seeds a realistic
15-minute case (30 vitals readings, 3 drug doses, a note), prints the
periodic chart, signs it, downloads the signed PDF to `./demo_report.pdf`,
and starts a second live session — printing a **DEMO READY** banner with its
WS URL at the end. Watch the ✓ marks scroll — if anything fails you'll get
a plain-English reason and a fix, never a raw error.

> **Say:** "That session was streamed through the same OCR-confidence
> pipeline as a real monitor feed — each reading gets validated, held or
> flagged if the confidence is low, before it's ever persisted. It's now
> signed, which makes it immutable: not one field of that record can change
> again — the backend enforces that at the database layer, not just in the
> UI."

### 3. Show the two outputs

**The signed PDF** — open `./demo_report.pdf` (double-click it, or `start
demo_report.pdf` / `open demo_report.pdf`). Scroll through: header, periodic
vitals table, drug log, the combined correction/audit history, and the
signature block at the end.

> **Say:** "This is the actual medico-legal artifact — audit-grade,
> clinician-confirmed record. Every drug dose, every correction anyone ever
> made to a flagged reading, and the signature itself are all in here in one
> chronological trail. This isn't a UI mockup — it's generated server-side
> from the same signed, locked database row."

**The live stream** — copy the `Live WS URL` line from the DEMO READY
banner and run:

```bash
python smoke_ws.py "ws://localhost:8000/ws/vitals/<LIVE_SESSION_ID>?source=synthetic"
```

Vitals readings will scroll on screen roughly once a second. Let it run for
a few seconds, then Ctrl+C to stop.

> **Say:** "This is the live path — right now, this is a synthetic monitor
> feed, but this exact WebSocket is what a real camera-and-OCR feed streams
> through in production: read the screen, validate it, stream it live, and
> when the case ends, the anaesthetist signs it into the record you just
> saw."

## If something goes wrong mid-demo

- **A step fails partway through**: don't re-run the whole thing from
  scratch — `demo_run.py` printed the session id before it failed. Re-run
  with `--session-id` to skip re-seeding:
  ```bash
  python scripts/demo_run.py --session-id SESSION-...
  ```
- **Nothing works and you're out of time**: skip straight to opening a
  previously-generated `demo_report.pdf` from a dry run (you did a dry run
  before going on stage, per the setup section) and narrate from that while
  troubleshooting in the background.

## Troubleshooting

**Port 8000 already in use**
```
Error: bind: address already in use
```
Something else is using port 8000 (a stray previous container, or another
service). Find and stop it:
```bash
docker compose ps -a          # any stopped/stray vital-backend containers?
docker compose down           # stop this project's containers
# still stuck? find whatever else is on :8000 and stop it, then retry
```

**Container never goes "healthy"**
```bash
docker compose logs backend --tail 50
```
Look for a traceback near the bottom. If it's the very first run and you
skipped the one-time setup, run `docker compose build` first. If it looks
like a stale image, rebuild clean:
```bash
docker compose build --no-cache
```

**`demo_run.py` says "Backend at ... never became healthy"**
The container isn't up (or isn't done starting). Run `docker compose ps` —
if `STATUS` isn't `healthy`, wait a few seconds and re-run `demo_run.py`;
it doesn't matter how many times you run it, seeding always creates a fresh
session.

**`demo_run.py` says "`docker` CLI not found"**
You're running it from an environment without Docker on `PATH` — run it
from the same terminal/shell where `docker compose up -d` worked.

**Need a clean slate (wipe all demo data and start over)**
```bash
docker compose down -v    # -v also removes the persisted SQLite volume
docker compose up -d
python scripts/demo_run.py
```

**WS stream shows nothing / hangs**
Check the URL was copied exactly as printed (session ids are
timestamp-based and unique per run — an old URL from a previous run won't
work after `down -v`). Re-run `demo_run.py` to get a fresh one.
