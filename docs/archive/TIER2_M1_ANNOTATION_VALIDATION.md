> ⚠️ **SUPERSEDED — ARCHIVED 2026-08-19.** This document is retained only as a
> historical record of what was measured and decided at the time. It does **not**
> describe the current architecture, and several of its conclusions and
> recommendations were later shown to be wrong. Do not use it as guidance.
>
> **Current documentation:** [`docs/ARCHITECTURE.md`](../ARCHITECTURE.md) ·
> [`docs/ROADMAP.md`](../ROADMAP.md) · [`docs/EVIDENCE.md`](../EVIDENCE.md)
> See [`docs/archive/README.md`](README.md) for what specifically was superseded.

---

# M1 Annotation Validation Report

Dataset: `backend/app/eval/tier2_data/external_monitor_video/` — **EXTERNAL REAL-WORLD MONITOR VIDEO** (YouTube-recorded/simulated anaesthesia monitor screenshots, not a physical-device capture). Validation only — the benchmark was **not** run (confirmed at the end).

---

## Dataset completeness

| Check | Result |
|---|---:|
| Image samples (`sample_*.png`) | **52** |
| Annotation files (`sample_*.json`) | **52** |
| Images missing a JSON | **0** |
| Orphan JSONs (no matching image) | **0** |
| JSON files that failed to parse | **0** |
| Images that failed to open/verify | **0** |
| Annotation files with an empty `rois` (0 boxes) | **0** |

Exact 1:1 pairing across all 52 samples, confirmed via set difference on filenames (not just counting).

## Schema validation

**PASS — zero invalid annotations.** Checked all 52 JSON files, every box in every file:

- Vital labels used: only `hr`, `spo2`, `nibp`, `etco2`, `temp`, `rr` appear anywhere — zero unrecognized labels.
- Every box is a 4-element `[x, y, w, h]` list.
- `x >= 0`, `y >= 0`, `w > 0`, `h > 0` — true for every box.
- `x + w <= image_width` and `y + h <= image_height` (checked against each image's *actual* opened dimensions, 2712×1220 for all 52, not assumed) — true for every box.

No individual issues to list — the "report every invalid annotation individually" instruction has nothing to report against.

## Missing values

`--` (dashed/placeholder) vitals are represented by **omitting that vital's key from `rois`** — the convention `ANNOTATION_GUIDE.md` specifies, and the one already used by `real_monitor/README.md` and the synthetic-proxy tooling. Not treated as invalid.

| Vital | Present | Missing (of 52) |
|---|---:|---:|
| hr | 43 | 9 |
| spo2 | 27 | 25 |
| nibp | 17 | 35 |
| etco2 | 17 | 35 |
| temp | 52 | 0 |
| rr | 43 | 9 |

`temp` (`Tperi` on this monitor) is the only vital that shows a live value on every single frame — matches direct visual inspection (every checked frame displays `98.6`). `nibp`/`etco2` being absent on the majority of frames matches this monitor's actual behavior in the recording — NIBP only updates once a minute and etCO2 frequently shows dashes — verified directly, not assumed (see Visual sanity check below).

## Class distribution

| Vital | Annotated boxes |
|---|---:|
| HR | 43 |
| SpO2 | 27 |
| NIBP | 17 |
| EtCO2 | 17 |
| Temp | 52 |
| RR | 43 |

**Samples with all six vitals present: 11 / 52.**

## Bounding-box statistics

All in pixels, on the full 2712×1220 image; area-fraction range shown to catch "covers most of the screen" outliers.

| Vital | n | width min/max/mean | height min/max/mean | area-fraction range |
|---|---:|---|---|---|
| hr | 43 | 105 / 360 / 234.4 | 157 / 244 / 190.5 | 0.0058 – 0.0229 |
| spo2 | 27 | 204 / 353 / 237.6 | 152 / 202 / 174.7 | 0.0096 – 0.0198 |
| nibp | 17 | 222 / 257 / 234.5 | 143 / 177 / 161.2 | 0.0103 – 0.0130 |
| etco2 | 17 | 161 / 208 / 183.0 | 127 / 163 / 141.1 | 0.0067 – 0.0085 |
| temp | 52 | 213 / 298 / 250.6 | 165 / 224 / 194.7 | 0.0123 – 0.0189 |
| rr | 43 | 105 / 230 / 156.0 | 165 / 212 / 184.8 | 0.0057 – 0.0136 |

**No box exceeds 2.3% of frame area** — nothing close to "covers most of the screen." No box is a single-digit pixel count either.

**Outlier check, exploiting a property unique to this dataset:** because all 52 frames share one static, unmoving camera framing (§5 of the ingest report), each vital's box position should barely move between samples — only its *width* should vary, tracking digit count (e.g. HR `"0"` vs HR `"183"`). Computed each vital's median `(x, y)` position and flagged any box whose `x`/`y` deviated >80px from that median, or whose `w`/`h` fell far outside the per-vital median range.

**Zero flags across all 199 annotated boxes** (43+27+17+17+52+43). Every box's position sits within a tight cluster for its vital (e.g. HR's `x` ranges 1818–1878, a 60px spread over 43 samples on a 2712px-wide image); every width outlier that *does* exist (e.g. HR ranging 105px→360px) tracks a real, visually-confirmed digit-count change (`"0"` at 105px vs `"183"` at 360px — see Visual sanity check), not an annotation error. This is a strong, independent (non-visual) signal that the manual annotation is self-consistent.

## Visual sanity check

Rendered annotation overlays (green/yellow/red/cyan/orange/purple boxes + vital labels, drawn directly from each `sample_XXXX.json` onto its full-resolution image — read-only, no benchmark code invoked) for all 6 requested categories:

| Category | Sample | Finding |
|---|---|---|
| Early | `sample_0001` | NIBP + Temp boxed tightly; hr/spo2/etco2/rr correctly omitted (all four show only dim limit-stacks/dashes in this frame, no current value) |
| Early, alarm | `sample_0002` | hr/spo2/nibp/rr/temp all present and tight; etco2 correctly omitted (dashes) |
| Middle | `sample_0026` | hr/rr/temp present and tight; spo2/nibp/etco2 correctly omitted — NIBP's `--/--` confirmed visually despite a fresh-attempt readout in the small history log above it (annotator correctly didn't confuse the log with the current value) |
| Late | `sample_0052` | All 5 present vitals tight; NIBP correctly omitted (`--/--`) |
| Alarm state (EXTREME BRADY) | `sample_0022` | HR=0, RR=0, Temp=98.6 boxed correctly; spo2/nibp/etco2 correctly omitted |
| Unusual value (HR=183) | `sample_0041` | Box correctly widens to fit the 3-digit value; NIBP/spo2/etco2 correctly omitted |

Individual overlays: `backend/app/eval/tier2_data/external_monitor_video/validation_overlays/overlay_sample_{0001,0002,0026,0052,0022,0041}.png`. Combined view: `.../validation_overlays/validation_contact_sheet.png`.

**No obvious annotation mistakes found in any of the 6 inspected samples.** Boxes are consistently tight to the bold current-value digits only (never the label, unit, dim alarm-limit stack, or waveform); the "omit when dashed" rule was applied correctly and consistently, including in the trickiest case (`sample_0026`'s NIBP, where a stale-looking history log sits directly above the actual `--/--` current value — the correct region was boxed/omitted, not the nearby log text).

## Verdict

# ANNOTATIONS VALID — READY FOR M1 BENCHMARK

Every completeness, schema, and consistency check passed with zero exceptions; the independent position/size self-consistency check (enabled by this dataset's static framing) found zero outliers; visual inspection across all 6 requested categories (early/middle/late/NIBP-present/NIBP-missing/alarm-state/unusual-value) found no mistakes. No corrections required.

**Note on what this dataset can and can't test (carried over from the ingest report, still true here):** all 52 frames share identical camera framing, so this benchmark run will be a strong test of value/state diversity but not of camera-condition diversity (glare/blur/perspective/etc.) — that gap isn't an annotation defect, it's inherent to a screen-recorded source.

---

The benchmark was **not** run — no `tier2_m1_report/` directory exists yet under `external_monitor_video/`, and `tier2_benchmark.py` was not invoked this session. Tier-1, adaptive-threshold, Canny, and Tier-2 CNN training were all skipped, as instructed. No production code (`read_frame()`, `roi.py`, OCR, ONNX, reconciliation, persistence, WebSocket, `CameraSource`, frontend) was touched. 190 existing backend tests still pass. No git commits or tags were made.

Say the word and I'll run:
```
python -m app.eval.tier2_benchmark --dataset app/eval/tier2_data/external_monitor_video --label "EXTERNAL REAL-WORLD MONITOR VIDEO"
```
