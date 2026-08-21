> ⚠️ **SUPERSEDED — ARCHIVED 2026-08-19.** This document is retained only as a
> historical record of what was measured and decided at the time. It does **not**
> describe the current architecture, and several of its conclusions and
> recommendations were later shown to be wrong. Do not use it as guidance.
>
> **Current documentation:** [`docs/ARCHITECTURE.md`](../ARCHITECTURE.md) ·
> [`docs/ROADMAP.md`](../ROADMAP.md) · [`docs/EVIDENCE.md`](../EVIDENCE.md)
> See [`docs/archive/README.md`](README.md) for what specifically was superseded.

---

# M1 — External real-world monitor video: ingest + annotation prep

Continuation of Milestone 1 ([`TIER2_M1_BENCHMARK_REPORT.md`](TIER2_M1_BENCHMARK_REPORT.md)), covering dataset ingestion, contact sheet, and annotation tooling for the newly-provided image set. **The benchmark has NOT been run against this dataset — see item 13.**

**Provenance label, used everywhere this dataset is referenced: `EXTERNAL REAL-WORLD MONITOR VIDEO`.** These are screenshots of a YouTube video playing a recorded/simulated anaesthesia monitor feed, captured by the user — not photos of a physical device. Never call this "physical monitor" evidence.

---

### 1. Number of source images found

**52**, not 50. Source folder `C:\Users\Admin\Desktop\Sample imgs\Test\` contains `1.jpg` .. `52.jpg`. (Top-level `Sample imgs\` folder itself holds only the `Test` subfolder — the images are one level down.) Nothing in the source folder was modified, moved, or deleted.

### 2. Number successfully imported

**52 / 52.** All readable, zero corrupted/unreadable files (each opened and `.verify()`-checked with PIL).

### 3. File extensions

Source: `.jpg` (JPEG) for all 52. Imported as **both** `.jpg` (byte-for-byte copy of the original) and `.png` (re-encoded, needed because `app.eval.harness.load_dataset()` hardcodes a `.png` sibling per `sample_XXXX.json` — see item 6 for why both exist).

### 4. Image dimensions

All 52 images: **2712×1220, RGB** — identical resolution across the entire set (single video source, no resizing).

### 5. Duplicate / near-duplicate findings

- **Exact duplicates (MD5): zero.** No two files are byte-identical.
- **Near-duplicates:** all 52 images share **one continuous, unchanging camera framing** — this is a single video's screenshots, not 52 independent captures, so there is **zero variation in camera angle, distance, or monitor position** anywhere in this set (unlike the physical-capture diversity `real_monitor/README.md` asks for). An 8×8 average-hash clustering found 7 groups of visually-similar frames (2–18 images each) — expected for a mostly-static video feed where only digits/waveforms tick between frames, not a data-quality problem. Pixel-level mean-abs-difference between even the closest pairs stayed >0.3/255, i.e. no frame is a re-saved copy of another. Full breakdown recorded in `manifest.json`'s `near_duplicate_clusters_note`.
- **Practical effect on M1:** this dataset alone cannot test the robustness-by-condition split (glare/blur/perspective/etc. — Phase 7 of the original M1 brief) the way the physical-capture procedure was designed to. It's a strong test of *value/state* diversity (see contact sheet — HR ranges from 0 to 183 across frames, several alarm states, dashed/missing readings) but not of camera-condition diversity.

### 6. Whether all images show an anaesthesia monitor / six vitals visible

Yes to both, visually confirmed on multiple full-resolution frames (not assumed from thumbnails). All six target vitals are present on this UI, though two use the device's own naming rather than VITAL's generic names — **`temp`↔`Tperi`** and **`rr`↔`awRR`** — mapped explicitly in `ANNOTATION_GUIDE.md`. See item 9 for the layout structure that governs which number is the actual ground truth per vital.

### 7. Whether there is meaningful variation

Yes, in **vital state**: values swing widely (e.g. HR frames include 0, 24, 60, 80, 85–183) and several frames show explicit alarm states (`EXTREME BRADY`, dashed/unmeasured `NIBP`/`awRR`/`etCO2`), which is good stress-test material for candidate generation on a genuinely different UI. No, in **camera condition** — see item 5.

---

### 8. Dataset directory

```
backend/app/eval/tier2_data/external_monitor_video/
  sample_0001.jpg .. sample_0052.jpg   (original bytes, untouched copies)
  sample_0001.png .. sample_0052.png   (re-encoded — required by load_dataset()'s hardcoded .png lookup; documented conversion, not a silent one)
  manifest.json
  source_filename_mapping.json         (sample_NNNN -> original N.jpg, for traceability back to the source folder)
  contact_sheet.png
  ANNOTATION_GUIDE.md
```

No existing dataset was overwritten — this is a new directory.

### 9. Dataset manifest

`backend/app/eval/tier2_data/external_monitor_video/manifest.json`. Key fields: `"dataset": "external_monitor_video"`, `"source_type": "external_real_world_monitor_video"`, `"physical_monitor_capture": false`, `"count": 52`, `"count_requested_by_user": 50`, plus the image-format/conversion note, near-duplicate note, vitals-visible breakdown, and an `annotation_caveat` field documenting this monitor's display structure (see next item) so it isn't rediscovered from scratch later.

**That structure, confirmed directly on full-resolution frames (e.g. `sample_0005`):** every vital renders as *label → dim small two-line alarm-limit stack (mostly constant across frames) → one bold, large, bright current-value number*. Ground truth is the bold large number only — never the dim limit stack. Several frames show placeholder dashes (`--`) instead of a number for a given vital (e.g. `etCO2`, `awRR` are frequently unmeasured in this recording); those are treated as "not visible" and omitted, per the existing convention.

### 10. Contact sheet path

`backend/app/eval/tier2_data/external_monitor_video/contact_sheet.png` — all 52 imported frames, 8×7 grid, each thumbnail labeled with its `sample_XXXX` id. Visually reviewed: monitor clearly visible in every tile, no capture artifacts (letterboxing/watermarks/UI chrome from the video player) beyond the monitor UI itself, wide value/alarm-state variation, zero identical-looking stuck frames.

### 11. Annotation format

`backend/app/eval/tier2_data/external_monitor_video/ANNOTATION_GUIDE.md` — same `{"id", "rois": {vital: [x,y,w,h]}, "conditions", "notes"}` shape as `real_monitor/README.md` (reused, not reinvented, so existing IoU/eval tooling needs zero changes), plus this dataset's specific vital→on-screen-label mapping and the current-value-vs-alarm-limit disambiguation from item 9. NIBP stays one block (sys/dia + mean line together), matching every other convention already established this milestone.

### 12. Annotation tool location + launch command

**Location:** `backend/app/eval/tier2_annotate.py` — a tiny local HTTP server (Python stdlib `http.server` only, no new dependency) serving one self-contained HTML/JS page. Built fresh because this repo's venv uses `opencv-python-headless` (confirmed: `cv2.namedWindow()` raises "function not implemented, rebuild with GTK/Cocoa support" here), so a cv2-window-based tool would not run in this environment.

**Launch command:**
```
cd backend
python -m app.eval.tier2_annotate --dataset app/eval/tier2_data/external_monitor_video
```
Opens `http://127.0.0.1:8765/` in a browser automatically (add `--no-browser` to skip that, `--port` to change it).

**Smoke-tested this session** (server start, page load, image list, GET/POST annotation round-trip, image byte-serving) — the one test annotation written during that check was deleted afterward; the dataset currently has **zero** `sample_XXXX.json` files, confirmed by directory listing.

### 13. What you need to do manually to finish annotations

1. Run the launch command above.
2. In the browser tab: pick an image from the left sidebar (or Prev/Next), select a vital (buttons or keys 1–6), click-drag a tight box around **only the bold current-value number** for that vital (see item 9 — skip the dim alarm-limit stack), repeat for each visible vital, optionally fill in `conditions`/`notes`, then **Save** (button or Ctrl+S). A checkmark appears next to saved images in the sidebar.
3. Skip any vital that shows `--` (dashes) instead of a number on a given frame — leave it unboxed rather than guessing.
4. Repeat for as many of the 52 as you want scored — the benchmark tool reads whatever `sample_XXXX.json` files exist; it doesn't require all 52 annotated at once, but more coverage gives a more trustworthy recall number.
5. Tell me when you're done (or want a partial run) and I'll run:
   ```
   python -m app.eval.tier2_benchmark --dataset app/eval/tier2_data/external_monitor_video --label "EXTERNAL REAL-WORLD MONITOR VIDEO"
   ```

### Confirmation: benchmark has NOT been run

No `tier2_m1_report/` directory exists under `external_monitor_video/`, and `tier2_benchmark.py` was not invoked against this dataset this session (only against the earlier synthetic/proxy set, already reported in `TIER2_M1_BENCHMARK_REPORT.md`). Tier-1, adaptive-threshold, and Canny were **not** run here. The Tier-2 CNN was **not** trained. `read_frame()`, `roi.py`, OCR, ONNX inference, reconciliation, persistence, the WebSocket, `CameraSource`, and the frontend were **not** touched. 190 existing backend tests still pass. No git commits or tags were made.
