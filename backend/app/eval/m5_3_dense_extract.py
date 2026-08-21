"""M5.3 Stage 1: dense sequential frame extraction from the ORIGINAL source
recording, with provenance verified against the existing frozen dataset.

WHY THIS SCRIPT EXISTS. docs/EVIDENCE.md sec 8 and sec 10, and
docs/M5_2_REAL_CALIBRATION_REPORT.md sec 11, all record the same blocking
data gap: Dataset A (52 frames) and Dataset B (17 frames) are SPARSE STILLS,
not continuous video, and therefore "cannot validate a temporal/tracking
model". M5.3 is the milestone that needs exactly that validation, so the
dense frames have to come from the original recordings rather than from any
synthetic manufacture of motion.

WHAT THIS SCRIPT WILL NOT DO. It never fabricates frames, never interpolates
between stills, and never treats a synthetically warped still as temporal
evidence. If the source video cannot be obtained, or can be obtained but does
NOT verifiably match the existing dataset, it says so and stops -- an
unverified video is not evidence about the monitor the frozen ground truth
describes.

PIPELINE
    1. probe/download the source video (yt-dlp), record provenance
    2. VERIFY CORRESPONDENCE: ORB-match every existing frozen sample against
       the video's own frames. This is the gate -- extraction does not run
       unless the existing dataset is demonstrably a capture OF this video.
    3. CHARACTERIZE MOTION across the whole video with an explicit, committed
       criterion, so segment selection is a documented measurement rather
       than a favourable hand-pick.
    4. EXTRACT dense sequential frames at a fixed interval, preserving
       chronological order and per-frame provenance (video sha256, frame
       index, presentation timestamp).

HOLDOUT DISCIPLINE (docs/ROADMAP.md "Data"). The frozen 17-frame Dataset B
and 52-frame Dataset A are read-only here and are NOT modified. Dense frames
are a DIFFERENT SPLIT OF THE SAME RECORDING and must always be reported as
such -- never as an independent monitor or an independent dataset.

Usage:
    python -m app.eval.m5_3_dense_extract              # full run
    python -m app.eval.m5_3_dense_extract --probe-only # metadata + match gate only
"""

import argparse
import hashlib
import json
import os
from dataclasses import asdict, dataclass
from typing import List, Optional, Tuple

import cv2
import numpy as np
from PIL import Image

# --- source recordings ---------------------------------------------------
# Supplied by the project owner as the origin of the two frozen datasets.
#
# PROVENANCE OF THE VIDEO FILES THEMSELVES. Automated retrieval of both URLs
# from this machine FAILED (Dataset A: "This video is not available";
# Dataset B: HTTP 403/416 across every yt-dlp player client, one of which
# reported the video as DRM protected). The project owner supplied both
# files directly instead, and `local_path` below points at them. The URLs are
# retained as the cited origin of each recording, and download_source() still
# works if a future environment can fetch them -- but the local file is
# always preferred, so this script is reproducible without network access.
LOCAL_VIDEO_DIR = "C:/Users/Admin/Desktop/Video samples"

SOURCES = {
    "A": {
        "url": "https://www.youtube.com/watch?v=AEpiKOsTYDc",
        "local_path": os.path.join(LOCAL_VIDEO_DIR, "Anesthesia Scenario.mp4"),
        "dataset_dir": "app/eval/tier2_data/external_monitor_video",
        "note": "Dataset A control. Phase 0 measured ZERO camera motion across all "
                "52 frozen frames (max |translation| 0.1px, max |scale-1| 0.0000), so "
                "this recording is expected to exercise a tracker only as a no-op "
                "-- which is itself a required safety result (a tracker must not "
                "invent motion where there is none).",
    },
    "B": {
        "url": "https://www.youtube.com/watch?v=V3mJMeFUh68",
        "local_path": os.path.join(LOCAL_VIDEO_DIR, "GE CARESCAPE B650 Anesthesia Patient Monitor.mp4"),
        "dataset_dir": "app/eval/tier2_data/external_monitor_B",
        "note": "Dataset B. Phase 0 measured REAL framing changes across the 17 frozen "
                "frames (scale 0.51-1.89, translation up to 2019px). This is the "
                "recording M5.3's temporal validation depends on.",
    },
}

VIDEO_DIR = "app/eval/tier2_data/_source_video"
REPORT_DIR = "app/eval/tier2_data/m5_3_report"

# Prefer a standalone mp4 video stream: no audio, no muxing, therefore no
# system ffmpeg dependency (this machine has none -- cv2.VideoCapture decodes
# the result directly). Highest resolution first, since the frozen samples
# are 2712x1220 upscales of this same content and detail matters for OCR.
FORMAT_SELECTOR = "bv*[ext=mp4][height<=1080]/b[ext=mp4]"

# --- correspondence-verification parameters ------------------------------
# A frozen sample is accepted as "found in this video" only if ORB+RANSAC
# recovers a similarity transform onto some video frame with at least this
# many inliers. Calibrated against the Phase 0 negative controls, where a
# genuinely unrelated image pair (different monitor / noise / heavy blur)
# produced only 3-11 inliers.
MATCH_MIN_INLIERS = 25
# Coarse pass over the whole video at this stride, then a fine pass around
# the best hit. Keeps a 220s video to a few hundred detections.
COARSE_STRIDE_S = 1.0
FINE_WINDOW_S = 1.5
FINE_STRIDE_S = 1.0 / 15.0
# Everything is matched at this working resolution. Phase 0 measured that
# downscaling RAISES inlier counts on hard pairs (25 -> 76) while cutting
# latency ~4x, because it suppresses high-frequency spurious features.
WORK_MAX_DIM = 960


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _to_work_gray(img: np.ndarray) -> Tuple[np.ndarray, float]:
    """Grayscale + downscale to WORK_MAX_DIM. Returns (image, scale_applied)."""
    if img.ndim == 3:
        img = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    h, w = img.shape[:2]
    longest = max(h, w)
    if longest <= WORK_MAX_DIM:
        return img, 1.0
    scale = WORK_MAX_DIM / longest
    return cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA), scale


def _orb() -> "cv2.ORB":
    return cv2.ORB_create(nfeatures=2000)


def _match_inliers(desc_a, kp_a, desc_b, kp_b) -> Tuple[int, Optional[np.ndarray]]:
    """Returns (inlier count, 2x3 similarity a->b) or (0, None)."""
    if desc_a is None or desc_b is None or len(desc_a) < 4 or len(desc_b) < 4:
        return 0, None
    bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
    matches = bf.match(desc_a, desc_b)
    if len(matches) < 4:
        return 0, None
    src = np.float32([kp_a[m.queryIdx].pt for m in matches]).reshape(-1, 1, 2)
    dst = np.float32([kp_b[m.trainIdx].pt for m in matches]).reshape(-1, 1, 2)
    M, inl = cv2.estimateAffinePartial2D(
        src, dst, method=cv2.RANSAC, ransacReprojThreshold=5.0, maxIters=5000, confidence=0.995
    )
    if M is None or inl is None:
        return 0, None
    return int(inl.sum()), M


# --- 1. download + provenance --------------------------------------------


def probe_source(url: str) -> dict:
    import yt_dlp

    with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True}) as ydl:
        info = ydl.extract_info(url, download=False)
    return {
        "url": url,
        "title": info.get("title"),
        "uploader": info.get("uploader"),
        "duration_s": info.get("duration"),
        "upload_date": info.get("upload_date"),
        "video_id": info.get("id"),
    }


def download_source(url: str, out_dir: str) -> str:
    import yt_dlp

    os.makedirs(out_dir, exist_ok=True)
    opts = {
        "format": FORMAT_SELECTOR,
        "outtmpl": os.path.join(out_dir, "%(id)s.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        path = ydl.prepare_filename(info)
    if not os.path.exists(path):  # extension can differ from the template guess
        stem = os.path.splitext(path)[0]
        for cand in (stem + ".mp4", stem + ".webm", stem + ".mkv"):
            if os.path.exists(cand):
                return cand
        raise FileNotFoundError(f"yt-dlp reported success but no file was found at {path}")
    return path


def video_metadata(path: str) -> dict:
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise RuntimeError(f"cv2.VideoCapture could not open {path}")
    meta = {
        "path": path,
        "file_bytes": os.path.getsize(path),
        "sha256": _sha256_file(path),
        "fps": cap.get(cv2.CAP_PROP_FPS),
        "frame_count": int(cap.get(cv2.CAP_PROP_FRAME_COUNT)),
        "width": int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
        "height": int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
    }
    meta["duration_s"] = meta["frame_count"] / meta["fps"] if meta["fps"] else None
    cap.release()
    return meta


# --- 2. correspondence verification (the gate) ---------------------------


def _frozen_samples(dataset_dir: str) -> List[Tuple[str, str]]:
    out = []
    for name in sorted(os.listdir(dataset_dir)):
        if name.startswith("sample_") and name.endswith(".png"):
            out.append((name[: -len(".png")], os.path.join(dataset_dir, name)))
    return out


def verify_correspondence(video_path: str, dataset_dir: str, meta: dict) -> dict:
    """Locates every frozen sample inside the video. This is a GATE, not a
    diagnostic: if the frozen dataset is not demonstrably a capture of this
    video, extraction must not proceed, because dense frames from an
    unrelated recording say nothing about the monitor the frozen ground
    truth describes."""
    orb = _orb()
    samples = _frozen_samples(dataset_dir)
    print(f"  verifying {len(samples)} frozen samples against the video ...")

    sample_feats = {}
    for sid, path in samples:
        gray, _ = _to_work_gray(np.array(Image.open(path).convert("RGB")))
        sample_feats[sid] = orb.detectAndCompute(gray, None)

    fps = meta["fps"]
    total = meta["frame_count"]
    cap = cv2.VideoCapture(video_path)

    def feats_at(frame_idx: int):
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
        ok, bgr = cap.read()
        if not ok:
            return None, None
        gray, _ = _to_work_gray(cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY))
        return orb.detectAndCompute(gray, None)

    coarse_step = max(1, int(round(COARSE_STRIDE_S * fps)))
    coarse_feats = {}
    for i in range(0, total, coarse_step):
        kp, desc = feats_at(i)
        if desc is not None:
            coarse_feats[i] = (kp, desc)

    results = []
    for sid, _ in samples:
        kp_s, desc_s = sample_feats[sid]
        best_n, best_i, best_M = 0, None, None
        for i, (kp_v, desc_v) in coarse_feats.items():
            n, M = _match_inliers(desc_s, kp_s, desc_v, kp_v)
            if n > best_n:
                best_n, best_i, best_M = n, i, M
        if best_i is not None:
            lo = max(0, int(best_i - FINE_WINDOW_S * fps))
            hi = min(total - 1, int(best_i + FINE_WINDOW_S * fps))
            step = max(1, int(round(FINE_STRIDE_S * fps)))
            for i in range(lo, hi + 1, step):
                kp_v, desc_v = feats_at(i)
                if desc_v is None:
                    continue
                n, M = _match_inliers(desc_s, kp_s, desc_v, kp_v)
                if n > best_n:
                    best_n, best_i, best_M = n, i, M
        ts = (best_i / fps) if best_i is not None else None
        results.append({
            "sample_id": sid,
            "best_inliers": best_n,
            "video_frame_index": best_i,
            "video_timestamp_s": ts,
            "matched": best_n >= MATCH_MIN_INLIERS,
            "scale": float(np.hypot(best_M[0, 0], best_M[1, 0])) if best_M is not None else None,
        })
        verdict = "MATCH" if best_n >= MATCH_MIN_INLIERS else "no match"
        print(f"    {sid}: inliers={best_n:5d}  frame={best_i}  "
              f"t={(ts if ts is not None else float('nan')):7.2f}s  {verdict}")
    cap.release()

    matched = [r for r in results if r["matched"]]
    return {
        "min_inliers_required": MATCH_MIN_INLIERS,
        "n_samples": len(results),
        "n_matched": len(matched),
        "match_rate": len(matched) / len(results) if results else 0.0,
        "matched_time_span_s": (
            [min(r["video_timestamp_s"] for r in matched), max(r["video_timestamp_s"] for r in matched)]
            if matched else None
        ),
        "per_sample": results,
    }


# --- 3. motion characterization ------------------------------------------


@dataclass
class MotionSample:
    frame_index: int
    timestamp_s: float
    n_matches: int
    n_inliers: int
    scale: float
    rotation_deg: float
    translation_px: float


def characterize_motion(video_path: str, meta: dict, stride_s: float = 0.5) -> List[dict]:
    """Consecutive-sample similarity transform magnitude across the WHOLE
    video. Reported for every sampled pair -- this is what makes segment
    selection a documented measurement instead of a hand-pick."""
    orb = _orb()
    cap = cv2.VideoCapture(video_path)
    fps, total = meta["fps"], meta["frame_count"]
    step = max(1, int(round(stride_s * fps)))
    prev = None
    out: List[dict] = []
    for idx in range(0, total, step):
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ok, bgr = cap.read()
        if not ok:
            continue
        gray, _ = _to_work_gray(cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY))
        kp, desc = orb.detectAndCompute(gray, None)
        if prev is not None and desc is not None and prev[1] is not None:
            pkp, pdesc = prev
            bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
            matches = bf.match(pdesc, desc)
            n_in, sc, rot, tr = 0, 1.0, 0.0, 0.0
            if len(matches) >= 4:
                src = np.float32([pkp[m.queryIdx].pt for m in matches]).reshape(-1, 1, 2)
                dst = np.float32([kp[m.trainIdx].pt for m in matches]).reshape(-1, 1, 2)
                M, inl = cv2.estimateAffinePartial2D(
                    src, dst, method=cv2.RANSAC, ransacReprojThreshold=5.0, maxIters=5000, confidence=0.995
                )
                if M is not None:
                    n_in = int(inl.sum())
                    sc = float(np.hypot(M[0, 0], M[1, 0]))
                    rot = float(np.degrees(np.arctan2(M[1, 0], M[0, 0])))
                    tr = float(np.hypot(M[0, 2], M[1, 2]))
            out.append(asdict(MotionSample(idx, idx / fps, len(matches), n_in, sc, rot, tr)))
        prev = (kp, desc)
    cap.release()
    return out


# --- 4. dense extraction -------------------------------------------------


def extract_dense(
    video_path: str, meta: dict, out_dir: str, start_s: float, end_s: float, interval_ms: float
) -> dict:
    """Every frame in [start_s, end_s] at the given interval is written --
    no frame is skipped, dropped, or filtered on how well it tracks. Ids are
    strictly chronological."""
    os.makedirs(out_dir, exist_ok=True)
    cap = cv2.VideoCapture(video_path)
    fps = meta["fps"]
    step = max(1, int(round((interval_ms / 1000.0) * fps)))
    start_idx, end_idx = int(round(start_s * fps)), int(round(end_s * fps))

    frames = []
    n = 0
    for idx in range(start_idx, min(end_idx, meta["frame_count"] - 1) + 1, step):
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ok, bgr = cap.read()
        if not ok:
            continue
        fid = f"frame_{n:06d}"
        Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)).save(os.path.join(out_dir, fid + ".png"))
        frames.append({"id": fid, "video_frame_index": idx, "timestamp_s": idx / fps})
        n += 1
    cap.release()

    manifest = {
        "dataset": os.path.basename(out_dir),
        "provenance": {
            "derived_from": "the ORIGINAL source recording, not from the frozen sparse dataset",
            "video_sha256": meta["sha256"],
            "video_path": meta["path"],
            "video_fps": fps,
            "video_resolution": [meta["width"], meta["height"]],
        },
        "extraction": {
            "start_s": start_s, "end_s": end_s, "interval_ms": interval_ms, "frame_stride": step,
        },
        "holdout_note": (
            "A DIFFERENT SPLIT OF THE SAME RECORDING as the frozen 17-frame Dataset B. "
            "Not an independent monitor and must never be reported as one "
            "(docs/ROADMAP.md 'Holdout discipline')."
        ),
        "n_frames": len(frames),
        "frames": frames,
    }
    with open(os.path.join(out_dir, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=2)
    return manifest


# --- driver --------------------------------------------------------------


def run(dataset: str, probe_only: bool = False) -> dict:
    src = SOURCES[dataset]
    os.makedirs(REPORT_DIR, exist_ok=True)
    record: dict = {"dataset": dataset, "url": src["url"], "note": src["note"]}

    print(f"\n=== Dataset {dataset}: {src['url']}")
    local = src.get("local_path")
    has_local = bool(local) and os.path.exists(local)

    # Remote probe is informational only -- it records what the cited URL
    # still reports about the recording. It is NOT a gate: Dataset A's URL is
    # dead ("This video is not available") while its file is on hand, and the
    # gate that actually matters is correspondence against the frozen dataset.
    try:
        record["probe"] = probe_source(src["url"])
        print(f"  url probe: {record['probe']['title']}  ({record['probe']['duration_s']}s)")
    except Exception as exc:
        record["probe_error"] = f"{type(exc).__name__}: {exc}"
        print(f"  url probe: UNAVAILABLE -- {record['probe_error']}")

    # Operator-supplied file preferred over a network fetch -- see the SOURCES
    # docstring for why automated retrieval is not available on this machine.
    if has_local:
        path = local
        record["acquisition"] = "operator-supplied local file"
    else:
        try:
            path = download_source(src["url"], VIDEO_DIR)
            record["acquisition"] = "yt-dlp download"
        except Exception as exc:
            record["available"] = False
            record["error"] = f"{type(exc).__name__}: {exc}"
            print(f"  NO VIDEO FILE -- {record['error']}")
            print("  -> no dense frames can be produced for this dataset from its source recording.")
            return record

    record["available"] = True
    meta = video_metadata(path)
    record["video"] = meta
    print(f"  source    : {path}  ({record['acquisition']})")
    print(f"  {meta['width']}x{meta['height']} @ {meta['fps']:.3f}fps  {meta['frame_count']} frames"
          f"  {meta['duration_s']:.1f}s  sha256={meta['sha256'][:16]}...")

    record["correspondence"] = verify_correspondence(path, src["dataset_dir"], meta)
    c = record["correspondence"]
    print(f"  correspondence: {c['n_matched']}/{c['n_samples']} frozen samples located "
          f"({c['match_rate'] * 100:.0f}%), span {c['matched_time_span_s']}")
    if c["n_matched"] == 0:
        record["gate"] = "FAILED -- frozen dataset does not correspond to this video; extraction skipped"
        print(f"  GATE {record['gate']}")
        return record
    record["gate"] = "PASSED"

    if probe_only:
        return record

    record["motion"] = characterize_motion(path, meta)
    return record


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe-only", action="store_true")
    ap.add_argument("--datasets", default="B,A")
    args = ap.parse_args()

    os.makedirs(REPORT_DIR, exist_ok=True)
    dest = os.path.join(REPORT_DIR, "m5_3_data_provenance.json")

    # MERGE, never overwrite. Running with --datasets A must not erase the
    # Dataset B record that app.eval.m5_3_dense_annotate (and every downstream
    # eval artifact) depends on for its ground-truth provenance.
    out: dict = {}
    if os.path.exists(dest):
        try:
            with open(dest) as f:
                out = json.load(f)
        except (json.JSONDecodeError, OSError):
            out = {}

    for ds in args.datasets.split(","):
        out[ds.strip()] = run(ds.strip(), probe_only=args.probe_only)

    with open(dest, "w") as f:
        json.dump(out, f, indent=2, default=str)
    print(f"\nWrote {dest}")


if __name__ == "__main__":
    main()
