"""Seeds a fully-populated demo session: ~30 synthetic vitals readings
(pushed through the same reconcile() validation pipeline app/ws/vitals.py
uses, not raw inserts) spread across a simulated ~15-minute case, 3 drug
doses at realistic points in that timeline, and a note — so the chart/PDF
endpoints have real, multi-row data instead of the empty/near-empty demo
session that made an early PDF sample look sparse.

Usage (inside the running container, same DATABASE_URL as the server):

    docker compose exec backend python scripts/seed_demo.py

Or on the host, against a server started with a matching DATABASE_URL:

    DATABASE_URL=sqlite:///./vital.db python scripts/seed_demo.py
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db import repo  # noqa: E402
from app.db.models import SessionNoteRow, SessionRow  # noqa: E402
from app.db.session import Base, SessionLocal, engine  # noqa: E402
import app.db.models  # noqa: E402,F401 -- registers tables on Base.metadata
from app.models.session import SessionFormData  # noqa: E402
from app.sources.replay import ReplaySource  # noqa: E402
from app.validation.reconcile import initial_confirmed_state, reconcile  # noqa: E402

READING_COUNT = 30
READING_SPACING_MS = 30_000  # one reading every 30 simulated seconds
CASE_DURATION_MS = READING_SPACING_MS * (READING_COUNT - 1) + 30_000  # ~15 simulated minutes

DRUGS = [
    # (data, administered_at offset from case start, in ms)
    ({"drugName": "Propofol", "dose": 150, "unit": "mg", "route": "IV", "entryMethod": "quick_preset"}, 60_000),
    ({"drugName": "Fentanyl", "dose": 100, "unit": "mcg", "route": "IV", "entryMethod": "quick_preset"}, CASE_DURATION_MS // 2),
    ({"drugName": "Rocuronium", "dose": 50, "unit": "mg", "route": "IV", "entryMethod": "quick_preset"}, 90_000),
]


def _mean_confidence(per_vital_confidence: dict) -> float:
    """Same reduction app/ws/vitals.py's _mean_confidence() does — duplicated
    rather than imported since that one is a route-module private helper."""
    values = list(per_vital_confidence.values())
    return sum(values) / len(values) if values else 0.0


async def _collect_frames(count: int) -> list:
    """ReplaySource.stream() runs on a real-time interval for live WS
    playback; interval=0 collapses that to "as fast as asyncio will
    schedule it" so seeding doesn't block for real wall-clock seconds. The
    physiological values (random-walked from a clinically-normal baseline)
    are realistic; only their real-wall-clock timestamps are — deliberately
    — overwritten afterward to simulate a spread-out case (see main())."""
    source = ReplaySource(mode="synthetic", interval=0)
    frames = []
    async for frame in source.stream():
        frames.append(frame)
        if len(frames) >= count:
            break
    return frames


def main() -> None:
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    session = repo.create_session(
        db,
        SessionFormData(
            patientId="DEMO-PT-1",
            patientAge=52,
            patientWeight=78,
            asa=2,
            procedure="Laparoscopic Cholecystectomy",
            anesthetist="Dr. Priya Sharma",
        ),
    )
    start = int(session.start_time)
    print(f"Created session {session.id}")

    frames = asyncio.run(_collect_frames(READING_COUNT))
    confirmed_state = initial_confirmed_state(start)
    for i, frame in enumerate(frames):
        # Collected near-instantly (see _collect_frames), so real timestamps
        # would all land within the same millisecond — spread them across a
        # simulated case instead, which is what makes the periodic chart
        # (5-min rows by default) actually populate multiple rows.
        frame.reading["timestamp"] = start + i * READING_SPACING_MS
        reading, confirmed_state, _flagged = reconcile(frame.reading, frame.per_vital_confidence, confirmed_state)
        repo.save_reading(
            db,
            session.id,
            reading,
            confidence=_mean_confidence(frame.per_vital_confidence),
            provenance=frame.provenance,
            per_vital_confidence=frame.per_vital_confidence,
        )
    print(f"Persisted {len(frames)} readings across a simulated {CASE_DURATION_MS // 60_000}-minute case")

    for drug_data, offset_ms in DRUGS:
        event = repo.save_drug_event(
            db, session.id, {**drug_data, "enteredBy": session.anesthetist, "administeredAt": start + offset_ms}
        )
        print(f"Logged {event.drug_name} {event.dose}{event.unit} (cumulative {event.cumulative_dose}{event.unit})")

    repo.add_note(db, session.id, "Demo session seeded by scripts/seed_demo.py", "observation")
    repo.end_session(db, session.id)

    # end_session() stamps end_time/vital_summary_duration_min from the real
    # wall clock (the whole seed run above took well under a second) — patch
    # both to match the simulated case length so the chart's time window and
    # the archived session's duration agree with the readings actually in it.
    end_time = start + CASE_DURATION_MS
    row = db.get(SessionRow, session.id)
    row.end_time = end_time
    row.vital_summary_duration_min = CASE_DURATION_MS / 60_000.0
    note_row = db.query(SessionNoteRow).filter(SessionNoteRow.session_id == session.id).first()
    note_row.timestamp = end_time - 30_000
    db.commit()
    db.close()

    host = os.environ.get("SEED_DEMO_HOST", "http://localhost:8000")
    print()
    print(f"Session {session.id} is ended and ready to sign. Try:")
    print(f"  Chart:  curl {host}/api/sessions/{session.id}/chart")
    print(
        f"  Sign:   curl -X POST {host}/api/sessions/{session.id}/sign "
        '-H "Content-Type: application/json" '
        '-d \'{"author": "Dr. Priya Sharma", "signatureMethod": "pin"}\''
    )
    print(f"  PDF (after signing): curl {host}/api/sessions/{session.id}/report.pdf -o report.pdf")
    print()
    print("For a live streaming demo (a separate, still-active session), connect:")
    print("  ws://localhost:8000/ws/vitals/demo-live?source=synthetic")


if __name__ == "__main__":
    main()
