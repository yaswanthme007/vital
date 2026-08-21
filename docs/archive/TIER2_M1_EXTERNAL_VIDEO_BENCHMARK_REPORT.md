> ⚠️ **SUPERSEDED — ARCHIVED 2026-08-19.** This document is retained only as a
> historical record of what was measured and decided at the time. It does **not**
> describe the current architecture, and several of its conclusions and
> recommendations were later shown to be wrong. Do not use it as guidance.
>
> **Current documentation:** [`docs/ARCHITECTURE.md`](../ARCHITECTURE.md) ·
> [`docs/ROADMAP.md`](../ROADMAP.md) · [`docs/EVIDENCE.md`](../EVIDENCE.md)
> See [`docs/archive/README.md`](README.md) for what specifically was superseded.

---

# M1 Benchmark Results — EXTERNAL REAL-WORLD MONITOR VIDEO
### NOT physical-monitor validation. Screenshots of a YouTube video playing a recorded/simulated anaesthesia monitor feed.

Command run, exactly as specified, against the validated 52-image/199-box annotation set ([`TIER2_M1_ANNOTATION_VALIDATION.md`](TIER2_M1_ANNOTATION_VALIDATION.md)):

```
python -m app.eval.tier2_benchmark --dataset app/eval/tier2_data/external_monitor_video --label "EXTERNAL REAL-WORLD MONITOR VIDEO"
```

**Confirmed before running:** `tier2_benchmark.py` only ever calls `load_dataset()` (read) and `Image.open()` (read) against this dataset — its only writes are to `tier2_m1_report/*.json/.txt` and `tier2_m1_report/debug/*.png`, both new paths under the dataset dir. No `sample_XXXX.json` annotation file was touched. Diffed the 52 annotation files' mtimes against the run: unchanged.

**Kept fully separate from the 40-image synthetic/foreign-palette proxy** — that dataset lives at `app/eval/tier2_data/proxy_full/`, untouched this run, reported separately in `TIER2_M1_BENCHMARK_REPORT.md`. Nothing below should be read as proxy data, and nothing in the proxy report should be read as real-world-video data.

No CNN training. No production code touched (`read_frame()`, `roi.py`, OCR, ONNX, reconciliation, persistence, WebSocket, `CameraSource`, frontend all untouched this run).

---

## Headline numbers

| Method | HR | SpO2 | NIBP | EtCO2 | Temp | RR | **Overall** |
|---|---:|---:|---:|---:|---:|---:|---:|
| Tier-1 colour | 0.0% | 0.0% | 41.2% | 0.0% | 0.0% | 0.0% | **3.5%** |
| Adaptive threshold | 58.1% | 85.2% | 0.0% | 17.6% | 100.0% | 90.7% | **71.4%** |
| Canny+contour | 39.5% | 11.1% | 0.0% | 0.0% | 100.0% | 30.2% | **42.7%** |

n=52 images, IoU≥0.3 (same threshold and rationale as the proxy report — TIER2_RECOGNITION_SPIKE.md §03/§08's own established convention for candidate-generation recall).

**Candidate counts and false positives per image:**

| Method | Avg candidates/image | Avg false positives/image |
|---|---:|---:|
| Adaptive threshold | 27.9 | **22.9** |
| Canny+contour | 22.3 | **20.3** |

**Screen detection: 0.0% (0/52).** `detect_screen()` found no quadrilateral on any of the 52 frames — see "Failure mode 1" below. Both candidate generators still ran (on the unrectified original frame, the documented fallback), and ground truth was compared in the same unwarped coordinate space (no homography existed to warp through) — the recall numbers above are still valid, methodologically sound comparisons.

**Latency** (n=152 for the four candidate/tier1 stages: 52 full-dataset runs + 5×20 dedicated repeats; rectify-only is n=0 — see below):

| Stage | Mean | Median | p95 |
|---|---:|---:|---:|
| Screen quad-find | 19.8ms | 18.3ms | 28.1ms |
| Perspective correction (rectify-only) | n/a | n/a | n/a |
| Tier-1 colour ROI | 788.1ms | 747.0ms | 1038.0ms |
| Adaptive-threshold candidates | 510.4ms | 481.8ms | 626.8ms |
| Canny+contour candidates | 17.0ms | 15.5ms | 24.8ms |

Rectify-only latency is `n/a` (not zero, not omitted) because it's only measurable when a quad was actually found — with 0/52 quads found, `_rectify_only_timing()` correctly returned no samples rather than fabricating a number for a code path that never ran on this data.

---

## What's actually happening (from the debug overlays, not just the numbers)

Six debug overlays generated: `app/eval/tier2_data/external_monitor_video/tier2_m1_report/debug/debug_sample_000{1-6}.png` (green = ground truth, cyan = adaptive-threshold, magenta = Canny). `debug_sample_0001.png` in particular makes both failure modes below directly visible, not just inferable from statistics.

### Failure mode 1 — screen detection finds nothing on this content

`detect_screen()`'s Canny-quad-finder returned no candidate on all 52 frames. Visually, these screenshots frame the monitor UI edge-to-edge with black letterboxing on the sides — there's no bezel-to-background contrast edge for Canny to lock onto (both the letterbox and the monitor's own background are black), and the monitor content itself doesn't form a clean rectangle distinct from its surroundings the way the synthetic proxy's rendered bezel did. This is a genuine content-type gap, not a bug: `detect_screen()` is doing exactly what it's designed to do (require a confident quad, fall back to the unrectified frame otherwise) — it just never got one here. Downstream, this means candidate generation ran on a frame that still contains the full monitor UI chrome (header bar, alarm banners, button toolbar) that a rectified crop might otherwise exclude, which feeds directly into failure mode 3.

### Failure mode 2 — NIBP: candidates find it, but split across the two-line block

Tier-1 got NIBP right 41.2% of the time (see "one accidental bright spot" below) but **both candidate generators scored 0.0% on NIBP** despite visibly boxing the right area (`debug_sample_0001.png`: cyan boxes sit right on `150/80`/`(103)`) — inspecting the raw per-sample data explains why: adaptive-threshold's best IoU against the NIBP ground truth sits at 0.22–0.26 across the first several samples, consistently just under the 0.3 threshold, not near zero. That pattern means a candidate is finding one line of the two-line NIBP block (`150/80` **or** `(103)`, not both merged into one box) — the dilation kernel isn't bridging the vertical gap between them on this monitor's actual line-spacing/font, unlike the guaranteed-adjacent construction in the synthetic proxy. This is a fixable *parameter* problem (dilation kernel sizing for this specific line-gap), not a fundamental one — but it's exactly the kind of thing a synthetic proxy alone would never have surfaced, which is the whole point of this milestone.

### Failure mode 3 — false positives are large and visually obvious on real UI chrome

22–23 false-positive candidates per image, confirmed by eye in `debug_sample_0001.png`: cyan/magenta boxes wrap the header bar's date/patient-type text, both alarm banner bars (drawn as solid-fill rectangles), the entire ECG waveform as one giant box, the "Pleth"/"CO2" waveform panel labels, the etCO2 capnograph waveform's individual peaks (Canny, several small boxes), scale numbers (`50`, `25`), and toolbar icons/labels along the bottom. None of this is present in comparable density on the synthetic proxy (which has a near-empty background plus one decoy waveform) — a real monitor's UI is simply much busier. This is not disqualifying on its own (the (not-yet-built) field classifier's `not-a-vital` reject class exists precisely to absorb over-proposal — TIER2_RECOGNITION_SPIKE.md §06/§11), but 20+ FP/image is a meaningfully higher bar for that classifier to clear than the proxy's ~0.5–1.0/image, and is now a measured number rather than a guess.

### One accidental bright spot — Tier-1 on NIBP (41.2%, everything else 0%)

Tier-1's overall 3.5% recall is driven entirely by NIBP (41.2%); every other vital is a clean 0%. This monitor happens to render NIBP in a red close enough to VITAL's own `nibp` hue (`(255, 71, 87)`) to occasionally clear `roi.py`'s hue-distance gate — a coincidence of one shared convention (red-for-blood-pressure is common across monitor vendors), not evidence Tier-1 generalizes. Every other vital's colour (green HR, yellow SpO2, white/pale etCO2 and RR, green Temp) misses Tier-1's fixed 6-hue palette entirely, reproducing the original bug report on 5 of 6 vitals cleanly.

---

## Comparison to the synthetic/proxy results (context, not a merge)

| | Tier-1 | Adaptive threshold | Canny+contour |
|---|---:|---:|---:|
| Synthetic/proxy (n=40, `TIER2_M1_BENCHMARK_REPORT.md`) | 0.0% | 100.0% | 92.1% |
| External real-world video (n=52, this report) | 3.5% | 71.4% | 42.7% |

Both candidate generators score meaningfully lower on real content than on the proxy — expected, and exactly why this milestone insisted on a real-content measurement before any training investment. The *direction* of the finding from M1's proxy phase still holds (candidate generation clearly beats Tier-1 colour ROI — 71.4%/42.7% vs. 3.5%), but the *magnitude* doesn't transfer 1:1, and the specific failure modes above (NIBP line-merging, UI-chrome false positives, screen-detection blind spot on edge-to-edge framing) are new information the proxy could never have produced.

---

## Decision-relevant notes (not a new decision gate — that stays with the user per the running M1 process)

- Adaptive-threshold remains the stronger of the two generators on real content too (71.4% vs. 42.7%), consistent with the proxy result.
- Temp is the one vital both generators nail perfectly on both datasets (100%/100% here, 100%/100% on proxy) — it's also the vital that's *always* present in this dataset (52/52), so this is a clean, well-supported number, not a small-sample fluke.
- EtCO2 is weak everywhere it's tested (17.6%/0.0% here on n=17 GT boxes; 85.0%/100.0% on the proxy — the *proxy* number for etco2 was actually fine, so this specific weakness looks real-content-specific, not a generator-wide issue).
- The false-positive volume (20+/image) and the NIBP merge gap are both concrete, fixable-looking targets if candidate generation continues past M1 — not evidence the whole approach is wrong (Tier-1 is still comprehensively beaten), but not a clean "ship it" either.
- This dataset's single unmoving camera framing (flagged in the ingest/validation reports) means these numbers say nothing about robustness to glare/blur/perspective/distance — only about a second, real, differently-styled monitor UI at one fixed framing.

---

## Files

- Report JSON/text: `backend/app/eval/tier2_data/external_monitor_video/tier2_m1_report/tier2_m1_report.json` / `.txt`
- Debug overlays: `backend/app/eval/tier2_data/external_monitor_video/tier2_m1_report/debug/debug_sample_000{1-6}.png`

Stopping here as instructed — no further phases run, no CNN training, no production changes, nothing committed or tagged.
