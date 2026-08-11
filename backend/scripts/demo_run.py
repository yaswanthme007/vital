"""Orchestrates the whole Stage-2 demo against an already-running container
(`docker compose up -d`) in one command: waits for /health, seeds a
populated session, prints its chart, signs it, downloads the signed PDF,
and starts a second session for the live WS view -- printing a
"DEMO READY" banner with everything a presenter needs.

Deliberately stdlib-only (urllib, not requests/httpx) so it runs on a bare
presenter laptop with nothing installed but Python + Docker -- no venv, no
`pip install`, no curl/PowerShell quoting to get wrong live on stage. The one
non-HTTP step (seeding) shells out to `docker compose exec`, reusing
scripts/seed_demo.py's already-tested logic instead of duplicating it.

Usage:
    python scripts/demo_run.py
    python scripts/demo_run.py --base-url http://localhost:8000
    python scripts/demo_run.py --session-id SESSION-...   # skip re-seeding, sign an existing one

Run from the project root (same directory as docker-compose.yml).
"""

import argparse
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

HEALTH_TIMEOUT_S = 30
HEALTH_POLL_INTERVAL_S = 1
DEMO_AUTHOR = "Dr. Priya Sharma"
PDF_PATH = "demo_report.pdf"


class DemoError(Exception):
    """Any expected failure mode (container down, HTTP error, docker exec
    failure, bad response shape). main()'s step runner catches only this --
    anything else is an actual bug and still gets a short message, never a
    raw traceback in front of an audience."""


# ─── HTTP (stdlib only) ─────────────────────────────────────────────────


def _open(req: urllib.request.Request, timeout: int = 10):
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors="replace")
        try:
            detail = json.loads(raw).get("detail", raw)
        except json.JSONDecodeError:
            detail = raw
        raise DemoError(f"HTTP {e.code} from {req.full_url}: {detail}")
    except urllib.error.URLError as e:
        raise DemoError(f"Could not reach {req.full_url}: {e.reason}")
    except TimeoutError:
        raise DemoError(f"Request to {req.full_url} timed out")


def http_get_json(url: str) -> dict:
    with _open(urllib.request.Request(url, method="GET")) as resp:
        return json.loads(resp.read().decode())


def http_get_bytes(url: str) -> bytes:
    with _open(urllib.request.Request(url, method="GET")) as resp:
        return resp.read()


def http_post_json(url: str, body: dict) -> dict:
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="POST", headers={"Content-Type": "application/json"})
    with _open(req) as resp:
        return json.loads(resp.read().decode())


# ─── steps ──────────────────────────────────────────────────────────────


def wait_for_health(base_url: str) -> None:
    deadline = time.time() + HEALTH_TIMEOUT_S
    last_error = "no attempts made"
    while time.time() < deadline:
        try:
            body = http_get_json(f"{base_url}/health")
            if body.get("status") == "ok":
                return
            last_error = f"unexpected body: {body}"
        except Exception as e:  # keep polling through connection-refused etc.
            last_error = str(e)
        time.sleep(HEALTH_POLL_INTERVAL_S)
    raise DemoError(
        f"Backend at {base_url} never became healthy within {HEALTH_TIMEOUT_S}s (last error: {last_error}).\n"
        "    Is the container running? Try: docker compose up -d, then docker compose ps"
    )


def seed_session() -> str:
    try:
        result = subprocess.run(
            ["docker", "compose", "exec", "-T", "backend", "python", "scripts/seed_demo.py"],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except FileNotFoundError:
        raise DemoError("`docker` CLI not found on PATH. Install Docker Desktop / Docker Engine and retry.")
    except subprocess.TimeoutExpired:
        raise DemoError("Seeding timed out after 60s inside the container. Try: docker compose ps")

    if result.returncode != 0:
        tail = (result.stderr or result.stdout).strip()[-800:]
        raise DemoError(
            "Seeding failed inside the container:\n    " + tail.replace("\n", "\n    ") + "\n"
            "    Is the container up and healthy? Try: docker compose ps"
        )

    match = re.search(r"Created session (\S+)", result.stdout)
    if not match:
        raise DemoError("Seed script ran but didn't print a session id. Output was:\n    " + result.stdout[-800:])
    return match.group(1)


def fetch_chart(base_url: str, session_id: str) -> dict:
    return http_get_json(f"{base_url}/api/sessions/{session_id}/chart")


def sign_session(base_url: str, session_id: str) -> dict:
    return http_post_json(
        f"{base_url}/api/sessions/{session_id}/sign",
        {"author": DEMO_AUTHOR, "signatureMethod": "pin"},
    )


def download_pdf(base_url: str, session_id: str, out_path: str) -> int:
    data = http_get_bytes(f"{base_url}/api/sessions/{session_id}/report.pdf")
    if not data.startswith(b"%PDF-"):
        raise DemoError("Downloaded file doesn't look like a PDF (missing the %PDF- header).")
    with open(out_path, "wb") as f:
        f.write(data)
    return len(data)


def start_live_session(base_url: str) -> str:
    session = http_post_json(
        f"{base_url}/api/sessions",
        {"patientId": "DEMO-LIVE", "procedure": "Live Demo Stream", "anesthetist": DEMO_AUTHOR},
    )
    return session["id"]


# ─── presentation ───────────────────────────────────────────────────────


def _fmt_ts(ms) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%H:%M:%S")


def _fmt_event(e: dict) -> str:
    if e["type"] == "drug":
        return f"{e['drugName']} {e['dose']:g}{e['unit']}"
    if e["type"] == "note":
        return f"note: {e['text']}"
    if e["type"] == "alert":
        return f"alert: {e['message']}"
    return e["type"]


def print_chart_summary(chart: dict) -> None:
    rows = chart["rows"]
    print(f"    {len(rows)} periodic rows (every {chart['intervalMinutes']:g} min)")
    for row in rows:
        vitals = (
            f"HR {row['hr']}  SpO2 {row['spo2']}  NIBP {row['nibpSystolic']}/{row['nibpDiastolic']}  "
            f"EtCO2 {row['etco2']}  Temp {row['temp']}  RR {row['rr']}"
        )
        events = ", ".join(_fmt_event(e) for e in row["events"]) or "-"
        print(f"      {_fmt_ts(row['timestamp'])}  {vitals}  |  events: {events}")
    vs = chart["vitalSummary"]
    print(
        f"    summary: avg HR {vs['avgHr']:.0f}  min SpO2 {vs['minSpo2']:.0f}  "
        f"avg EtCO2 {vs['avgEtco2']:.0f}  duration {vs['durationMin']:.1f} min"
    )


def print_banner(signed_session_id: str, pdf_path: str, pdf_size: int, live_session_id: str, ws_url: str) -> None:
    print()
    print("=" * 64)
    print("  DEMO READY")
    print("=" * 64)
    print(f"  Signed session : {signed_session_id}")
    print(f"  Signed PDF     : {pdf_path}  ({pdf_size:,} bytes)")
    print(f"  Live session   : {live_session_id}")
    print(f"  Live WS URL    : {ws_url}")
    print(f'  Watch it live  : python smoke_ws.py "{ws_url}"')
    print("=" * 64)


# ─── driver ─────────────────────────────────────────────────────────────


def run_step(label: str, fn):
    try:
        result = fn()
    except DemoError as e:
        print(f"✗ {label}")
        print(f"    {e}")
        sys.exit(1)
    except Exception as e:  # never let a bug show a raw traceback on stage
        print(f"✗ {label}")
        print(f"    Unexpected error: {e}")
        sys.exit(1)
    print(f"✓ {label}")
    return result


def main() -> None:
    # Some Windows consoles default to a codepage that can't encode ✓/✗;
    # UTF-8 with replacement keeps the demo running even there.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", default="http://localhost:8000", help="Backend base URL (default: %(default)s)")
    parser.add_argument("--session-id", default=None, help="Skip seeding; sign this already-seeded session instead")
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    ws_base = base_url.replace("http://", "ws://").replace("https://", "wss://")

    print(f"VITAL demo driver -- target: {base_url}")
    print()

    run_step("Backend health check", lambda: wait_for_health(base_url))

    if args.session_id:
        session_id = args.session_id
        print(f"✓ Using existing session {session_id} (--session-id given, skipped seeding)")
    else:
        session_id = run_step("Seeding demo session (30 readings, 3 drugs, a note)", seed_session)

    chart = run_step(f"Fetching chart for {session_id}", lambda: fetch_chart(base_url, session_id))
    print_chart_summary(chart)

    run_step(f"Signing session {session_id}", lambda: sign_session(base_url, session_id))

    pdf_size = run_step("Downloading signed PDF", lambda: download_pdf(base_url, session_id, PDF_PATH))

    live_session_id = run_step("Starting a live demo session", lambda: start_live_session(base_url))
    ws_url = f"{ws_base}/ws/vitals/{live_session_id}?source=synthetic"

    print_banner(session_id, PDF_PATH, pdf_size, live_session_id, ws_url)


if __name__ == "__main__":
    main()
