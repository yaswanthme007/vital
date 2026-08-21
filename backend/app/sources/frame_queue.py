"""In-process bridge between POST /api/pipeline/push-frame/{channel} (the
browser pushing camera frames) and CameraSource.stream() (the WS send_loop
draining them). Deliberately NOT a persisted/broker-backed queue -- this is
one process, one demo laptop; a plain dict guarded by a lock is the whole
requirement.

"Latest frame wins": each channel holds AT MOST one pending frame. A push
overwrites whatever hasn't been consumed yet rather than buffering, so
CameraSource always reads the freshest frame available instead of falling
behind under camera-vs-OCR speed mismatches.
"""

import threading
from dataclasses import dataclass

_lock = threading.Lock()
_frames: dict = {}
_seq_counters: dict = {}


@dataclass(frozen=True)
class PushedFrame:
    data: bytes
    seq: int


def push_frame(channel: str, data: bytes) -> int:
    """Stores `data` as the latest frame for `channel`. Returns the new
    sequence number (monotonically increasing per channel) so callers can
    detect "is this actually a new frame" without comparing bytes."""
    with _lock:
        seq = _seq_counters.get(channel, 0) + 1
        _seq_counters[channel] = seq
        _frames[channel] = PushedFrame(data=data, seq=seq)
        return seq


def get_latest_frame(channel: str):
    """Returns the channel's PushedFrame, or None if nothing has been pushed
    yet. Does NOT consume/clear it -- CameraSource compares .seq itself so a
    slow-polling consumer can still see the latest frame on its next tick."""
    with _lock:
        return _frames.get(channel)


def clear_channel(channel: str) -> None:
    """Drops any pending frame for `channel` -- called when a WS connection
    using this channel closes, so a stale frame from a finished session
    can't leak into a future session that happens to reuse the id."""
    with _lock:
        _frames.pop(channel, None)
        _seq_counters.pop(channel, None)
