# M5.8 — Real-camera observation: reading what the monitor actually shows

**VITAL is not supposed to produce plausible vital signs. It is supposed to
read the numbers that are physically on the anaesthesia monitor in front of
the camera, and to record nothing else.** This pass audited the real demo
recording end to end, found four independent ways that invariant was being
broken, and fixed each at its cause.

The evidence throughout is the demo laptop's own `backend/vital.db`: nine
1280×720 calibration reference frames photographed off the physical monitor
with the built-in webcam, the operator's own ROI boxes drawn on them, and 62
persisted camera rows from `SESSION-1787247886294-cv6z`.

---

## What was actually happening

Physical monitor at the time of the recording: **HR 89 · SpO₂ 98 · NIBP
150/80 (103) · EtCO₂ 36 · Temp 98.6 °F · RR 12.**

VITAL's workspace at the same moment: **HR 75 · SpO₂ 98 · NIBP 151/80 (103) ·
EtCO₂ 4 · Temp 36.8 · RR 14**, with HR, Temp and RR captioned
*"Held · last confirmed 22:51:42"*. The Observation Ledger contained rows for
`EtCO₂ 4`, `RR 42`, `SpO₂ 92/94/96/97/99` and `NIBP 151/80` — none of which
were ever on the screen.

Four separate defects produced that, and they compound:

| # | Defect | Where |
|---|---|---|
| 1 | The value parsed from a crop was frequently one of the field's own **alarm-limit labels**, or a label spliced onto the reading | `app/pipeline/ocr.py` |
| 2 | The camera path **seeded every field with `DEFAULT_BASELINE`** and then relabelled it `held` | `app/validation/reconcile.py`, `app/ws/vitals.py` |
| 3 | **One frame** clearing a 70 % confidence gate was enough to write a permanent observation | `app/validation/reconcile.py` |
| 4 | A truncated read (`"34"` → `"4"`) is invisible to the existing crop-integrity check | `app/validation/crop_integrity.py` |

---

## Root cause 1 — the reading was competing with its own alarm limits

An operator draws an ROI around a field's **display slot**, as the product
instructs. On this monitor — on every monitor — that slot also contains the
field's alarm limits, because the monitor draws them there:

```
EtCO₂ slot:  "65"  "25"   34    "inCO₂ 4"      RR slot:  "30"  "8"   12
HR slot:    "130"  "50"   88                 SpO₂ slot: "100" "92"   98
```

Every PSM this file routed to (8 "single word", 10 "single character", 6
"uniform block") returns all of that as **one** recognized text blob.
`_joined_text_and_confidence` concatenated it with no separator and
`_read_scalar` mined the **first digit run**:

```
"65 25 34 4"      → "234"   (and, on other frames, "4")
"30 8 12"         → "42" / "420"
"130 50 88"       → "9" / "3" / "94"
"101.0 79.0 98.6" → "986" / "8.6"
```

`EtCO₂ = 4` and `RR = 42` in the demo ledger are literally the alarm-limit
text. This is the single root cause behind every "the ledger recorded a
value that was never on the monitor" case in the recording.

### The fix: read the dominant row

`--psm 11` ("sparse text") returns each visually separate fragment as its own
token **with its own bounding box**. `dominant_row_tokens()` keeps only the
tokens belonging to the **tallest digit-bearing row** and the value is parsed
from those alone.

Glyph height is the monitor's own encoding of "this is the reading, those are
its limits" — measured on the real frames, primary digits are 75–160 px tall
after preprocessing while every label token is 16–40 px. Selecting on
**geometry** is also the safe kind of selection: it never looks at what the
digits *say*, so it cannot prefer a more "plausible" number.

Two supporting details, both measured rather than assumed:

- **Quiet zone widened 20 px → 30 px** (`_QUIET_ZONE_PAD`). PSM 11's layout
  analysis returns *nothing at all* for a crop whose glyphs fill it. Swept
  across three datasets; 30 is where the simulator reads 15/15 correctly and
  the real frames are insensitive (see the constant's own comment for the
  full table).
- **A guarded single-character fallback.** PSM 11 also returns nothing for a
  crop holding one isolated glyph — which is exactly what a monitor shows
  during asystole (HR 0) or apnoea (RR 4), both present in Dataset A. PSM 10
  is consulted only then, and its answer is accepted only if the token's own
  bounding box covers ≥ 80 % of the crop's ink (`_covers_the_ink`). That
  restriction is what keeps PSM 10's damaging behaviour out: on Dataset B it
  reads a genuine "40" as "4", and a truncation like that never covers the
  ink.

NIBP keeps its own path — its crop legitimately holds two readings
(`150/80` and the mean `(103)`) plus, on this monitor, an auto-interval
**history line** whose digits are just as real as the current reading's.
Height cannot separate those (the parenthesised mean is often *taller*).
Structure can: the current reading is the **last** row containing a
`NN/NN` pattern, and the mean is the nearest bare digit run **below** it.
The ink-projection line split (`_split_text_lines`, in this file since M1)
is kept ahead of the token-box grouping, because each catches a merge the
other misses.

### Measured effect

Same operator boxes, same production `WIDTH_SAFETY_PAD_FRACTION`, scored
against human-transcribed ground truth:

| dataset | before | after |
|---|---|---|
| **Real camera frames** (53 fields, `app/eval/tier2_data/real_camera/`) | 54.7 % correct, **32.1 % wrong**, 13.2 % no-read | **77.4 % correct, 9.4 % wrong**, 13.2 % no-read |
| Dataset A (193 fields) | 42.0 % correct, **53.4 % wrong**, 4.7 % no-read | **47.2 % correct, 39.4 % wrong**, 13.5 % no-read |
| Dataset B (49 fields) | 36.7 % correct, 4.1 % wrong, 59.2 % no-read | 18.4 % correct, **0.0 % wrong**, 81.6 % no-read |

**The wrong rate — the safety-critical number — falls on all three.** The
correct rate rises on the two that matter most and falls on Dataset B, which
is disclosed honestly below rather than buried.

Correct reads also arrive at far higher Tesseract confidence (typically
90–96 rather than 8–55), because the token being scored is now the value
alone. That is what makes the downstream confidence gate meaningful again
rather than a coin flip.

Two of the three remaining wrong reads on the real frames are ROI boxes that
physically **clip a digit** (frame `6q6o`'s SpO₂ box cuts the leading "9" off
"94", so the crop genuinely contains "4"); no reader can recover a digit that
was never in the crop. That is what the operator's Verify step is for.

---

## Root cause 2 — the camera path invented a starting value

`initial_confirmed_state()` seeded **every** field from `DEFAULT_BASELINE`
(HR 75, SpO₂ 98, NIBP 120/78/92, EtCO₂ 38, Temp 36.8, RR 14) on the first
tick. Nothing displayed those as "baseline" for long, because the *next*
tick found a prior value and reported `held` — so a clinically-plausible
fabrication was captioned *"Held · last confirmed 22:51:42"* for the rest of
the case, indistinguishable from something the camera had genuinely seen.
The `'baseline'` status was effectively unreachable dead code.

**Fix.** `reconcile(allow_baseline=False)` — used only by camera-sourced
connections — reports `None` with a new field status `'unknown'` for a field
that has never been confirmed, and writes nothing into `last_confirmed`, so
the next tick reaches the same branch rather than "holding" a value no
camera ever read. `VitalReading`'s numeric fields became nullable across the
wire and the frontend; a card with nothing observed renders an em dash and
*"Waiting for camera"*, never "Held". Synthetic/replay sources are unchanged
— they are not claiming to observe a physical monitor, and frozen milestone
evidence asserts on their exact shape.

---

## Root cause 3 — one frame was enough to write history

The live rule was: range OK, jump OK, and **this tick's** OCR confidence
≥ 70 → confirmed, persisted, permanent. Against a real webcam that is close
to a coin flip. In the demo recording the HR crop's confidence wandered
between 0 and 76 **frame to frame while the monitor did not change at all**,
and the rows that happened to clear 70 recorded SpO₂ as 92, 94, 96, 97 and 99
for a monitor showing a steady 98 — plus EtCO₂ 4 at 83 % confidence.

Raising the bar does not fix that (the wrong reads were often the confident
ones) and lowering it obviously does not either. The missing ingredient is
not a better threshold on one frame; it is more than one frame.

**Fix — `app/validation/live_corroboration.py`.** A bounded 5-sample window
per field. A value is accepted only when all of:

1. it is what **this** tick read — a stale majority can never confirm itself
   once the display moves on;
2. it was read on ≥ N frames in the window (N = 2 full-confidence tier,
   N = 3 recovery tier);
3. **every** agreeing sample was clean of residual OCR content —
   unconditional, evaluated *before* either confidence check;
4. the agreeing samples' mean confidence clears that tier's floor
   (`CONFIDENCE_MEDIUM_MIN` 70, or `CONFIDENCE_TEMPORAL_FLOOR` 40 for the
   3-frame tier).

Deliberately the same two-tier shape as the calibration-side
`app/pipeline/burst_verify.py`, reusing its constants, so calibration and
live observation judge evidence the same way instead of drifting apart.
Neither constant is new; what is new is that **neither tier can be reached
by a single frame**.

### This is not over-stabilization

Agreement is counted for the **current tick's** value only, and old samples
age out. When the monitor genuinely changes 88 → 90, the two 90s that follow
confirm 90 — the window's earlier 88s do not hold the display hostage
(`test_a_real_change_is_detected_rather_than_smoothed_away` asserts exactly
this, including that the window's *mode* is deliberately not what decides).
A value that oscillates every single frame keeps the last confirmed value on
screen and adds no observation, which is the honest outcome: nothing was
read consistently enough to write down.

### Flagged-row spam

The recording produced **4,374 `FlaggedReading` rows in four minutes**,
essentially all "OCR confidence below threshold" / "could not read a value
this tick" — burying the handful of genuinely reviewable events and making
Archive's flagged count meaningless. Routine hold reasons no longer raise a
review item **on the camera path**; a rejected range, an implausible jump,
or suspicious crop geometry still does.

A recovery-tier acceptance is also worth exactly one review item — but only
when the value genuinely changes. Re-confirming the *same* number every tick
produced 36 flagged rows across 14 frames of the real monitor photograph
(HR 88 at ~65 % mean confidence, SpO₂ 98 at ~50 %, NIBP at ~52 %), which is
the same flooding arriving through the accept branch instead of the hold
branch. It now follows the ledger's own rule: one entry per genuine change.

---

## Root cause 4 — a truncation with nothing left over to notice

`crop_integrity.has_residual_content` catches truncation by comparing the
engine's raw text against the parsed value — the measured signature is a
clipped digit recognized as a **letter fused onto the digit run** ("8g",
"8B"). Dataset A `sample_0010` defeats it completely: the crop plainly
renders "34", PSM 11 returns the single token "4" at **96 % confidence**,
and `raw_text == matched_text == "4"`. There is nothing textual to compare.

**Fix.** `OcrDiagnostics.incomplete_row` — a **geometric** second signal.
After the dominant row is selected, connected-component analysis asks whether
the crop holds full-height ink the recognized tokens do not account for.
Only components as tall as the dominant reading count, which is what stops it
firing on the alarm-limit labels the reader just excluded. Both signals are
consulted through one entry point, `crop_integrity.crop_is_suspicious()`.

The same pass **narrowed** the textual signal in one respect, with its own
measurement: with dominant-row selection, correct reads on the real frames
routinely come back as `"88`, `\?98`, `#34`, `* 150/80`, `98.6|` — the right
value plus one punctuation or unrecognized-glyph artifact where the waveform
or a rule line touches the digits. Flagging those held *every* correct
HR/SpO₂/NIBP read on the real camera. Residual is now compared after
stripping **non-alphanumeric** characters from the ends only: "8g"/"8B" are
still flagged, interior residue is still flagged, and a clipped digit has
never been measured to OCR as punctuation. NIBP additionally drops
digit-free tokens from its row before matching, because the monitor draws the
literal label "Sys." on the reading line.

Measured on the real frames after both changes: **40 correct reads clean, 1
correct read flagged, 3 wrong reads clean, 2 wrong reads flagged** — the two
newly-caught wrong reads include exactly the `sample_0010`-class truncation.

---

## How calibration and live observation now relate

**Calibration is initialization, never ground truth.**

It establishes ROI geometry, the reference frame for layout tracking, and the
operator's confirmation that each box is pointed at the right field. It has
*never* seeded a live value — `field_meta.verified_value` is stored on the
profile and read by nobody in the live path — and after M5.8 there is no
baseline for it to be confused with either. If Verify confirms `HR 90` while
the monitor shows 89, the live path reads 89 on its own frames, corroborates
it, and confirms 89; the calibration number has no mechanism by which to
survive. `test_live_camera_corrects_a_wrong_starting_value` pins this.

The full flow, unchanged in shape and now correct in content:

```
New case → patient details → Calibration (draw ROIs, burst-verify, confirm)
  → Save → Active Operation opens automatically, camera already running
  → every frame: crop → OCR → parse → range/jump → corroborate across frames
  → confirmed values only → LIVE STATE + Observation Ledger + SQLite
  → End Operation → Archive holds the observed timeline
```

**LIVE STATE vs OBSERVED TIMELINE** (M5.7's split, now enforceable):

| | LIVE STATE | OBSERVED TIMELINE |
|---|---|---|
| never observed | em dash, *"Waiting for camera"* | absent |
| held | last confirmed value, *"Held · last confirmed hh:mm:ss"* | **never** |
| confirmed | value, *"Confirmed hh:mm:ss"* | one row, one ledger entry per change |

---

## Per-field summary

| field | how it reads now | notes |
|---|---|---|
| **HR** | dominant row; `130`/`50` alarm limits excluded | 8/9 correct on the real frames, 0 wrong. Single-digit `0` (asystole) via the guarded PSM 10 fallback |
| **SpO₂** | dominant row; `100`/`92` limits excluded | 7/9 correct. The one wrong read is a box that clips the leading digit |
| **NIBP** | ink row split → token row grouping → **last** `NN/NN` row is current; mean is the nearest bare run below | fixes the history line (`151/80 (104)`) being reported as the current reading |
| **EtCO₂** | dominant row; `65`/`25`/`inCO₂ 4` excluded | this is where `EtCO₂ = 4` came from; 6/9 correct, and one of the remaining wrong reads is now caught by the geometric integrity check |
| **Temp** | dominant row; `101.0`/`79.0` limits excluded; decimal preserved | reads `98.6` directly instead of reconstructing it from `986`. Fahrenheit → Celsius by `normalize_temp_celsius`, unchanged |
| **RR** | dominant row; `30`/`8` limits excluded | this is where `RR = 42` came from; 8/9 correct |

---

## Tests

- **Backend: 498 passed, 1 skipped** (`pytest tests/ simulator/tests`) — 463
  before this pass plus 35 new M5.8 regression tests, one per defect above,
  including the exact `12 → 42 → 12` and `HR 90 → 89` scenarios.
- **Frontend: 14 passed** (`npx vitest run`), including new `deriveLedger` /
  `getAlarmSeverity` null-safety tests.
- `npx tsc --noEmit` and `npm run build`: clean.

Tests that encoded a superseded contract were **rewritten, not deleted or
weakened**, each with the reason recorded in its own docstring:
`test_m4_6_production_promotion.py` (per-vital PSM routing → one sparse
config plus a guarded fallback), `test_m4_4_rules_layer.py` (three pinned
EtCO₂ crops — one is now *correct* where it used to pin a known misread),
`test_m5_1_ocr_confidence_restoration.py` (whitelist comparison now pinned to
the preprocessing M5.1 measured it at), `test_camera_source.py`,
`test_persistence.py` and `test_m5_2_calibration.py` (one frame no longer
confirms).

## End-to-end verification

- **Real Chrome, real `getUserMedia`, real OCR, real WebSocket — 30/30
  passed** (`node scripts/m5_7_1_flow_e2e.mjs`), full New Case → Calibration
  → burst verify → automatic Active Operation → continuous tracking →
  reload → End → Archive. All four drawn fields verified at 95–96 %
  confidence with 100 % burst agreement; the Observed Timeline carried this
  fixture's real SpO₂/EtCO₂/RR values; an undrawn field rendered an em dash
  rather than a number.

  Run against a **throwaway stack** (backend on 8001 with its own scratch
  database, vite on 5179) so the live demo instance on 8000/5173 was never
  touched. Two small, default-preserving hooks make that possible:
  `VITE_API_PORT` (frontend) and `VITAL_EXTRA_CORS_ORIGINS` (backend); unset,
  both reproduce the previous behaviour exactly.

  Two stale selectors in that script were also fixed — it was still looking
  for a `Confirm this is right` button that M5.7.3 relabelled to
  `Confirm <value>`, so the script could not have passed as written — and its
  "real OCR reached the UI" witness was rewritten, because it worked by
  finding the one fixture value that differed from `DEFAULT_BASELINE`, and
  there is no baseline to differ from any more.

- **Real footage, real uvicorn subprocess, real WebSocket — 21/21 passed**
  (`app/eval/tier2_data/m5_7_report/m5_7_e2e_script.py`), 90 frames of a GE
  CARESCAPE B650 recording. Persisted rows rose 8 → 29 with the new reader
  while every non-null persisted field remained genuinely `confirmed`.

- **Calibration burst verification** re-measured on the nine real camera
  frames: 66.0 % correct, **5.7 % wrong**, 28.3 % unstable — against
  M5.7.2's shipped 46 % correct / 3.3–4.7 % wrong on Dataset A/B.

- **The demo laptop's own monitor photograph, replayed through the whole live
  path — 9/9 passed**
  (`app/eval/tier2_data/m5_8_report/m5_8_real_camera_live_path.py`). Real
  uvicorn subprocess, real scratch database, real `push-frame` HTTP, real
  `CameraSource` → calibrated ROI → OCR → `reconcile()` → corroboration →
  WebSocket → SQLite, using the **operator's own saved ROI boxes** on the
  1280×720 photograph of the physical monitor. This is the closest offline
  reproduction of the failing demo that exists, and it is now clean:

  | | recorded demo | after M5.8 |
  |---|---|---|
  | live state | HR **75** · SpO₂ 98 · NIBP **151/80** · EtCO₂ **4** · Temp **36.8** · RR **14** | HR **88** · SpO₂ **98** · NIBP **150/80 (103)** · EtCO₂ **34** · Temp **37.0** · RR **12** |
  | monitor truth | HR 88 · SpO₂ 98 · NIBP 150/80 (103) · EtCO₂ 34 · Temp 98.6 °F · RR 12 | *(identical)* |
  | first frame | six baseline numbers, captioned "Held" | every field `null` / `unknown` |
  | observed timeline | `EtCO₂ 4`, `RR 42`, `SpO₂ 92/94/96/97/99`, `NIBP 151/80` | 13 rows, **every value matching the monitor**, nothing else |
  | flagged rows | 4,374 in four minutes | 3 across 14 ticks |

  The script asserts the fabricated values from the recording by name and
  fails if any of them reappears.

**Physical-camera validation is NOT claimed for this pass.** No webcam was
attached to this environment. What *is* real here: the frames, the ROI boxes
and the failure modes all came off the physical demo laptop, and the fixes
are measured against them offline. The live loop itself was exercised through
a fake camera device in a real browser.

---

## Remaining limitations

- **Dataset B's read rate halved** (18/49 → 9/49 correct) while its wrong
  rate went to zero. Those crops are tight, low-contrast, and annotated
  around the digits alone — the shape the dominant-row reader gains nothing
  on and PSM 11's layout analysis handles worst. Production's input is the
  1280×720 photographed-slot shape, where the same change is a large gain.
  Stated plainly because it is a genuine regression on one arm, not a rounding
  error.
- **A mis-drawn ROI is still unrecoverable.** If the box clips a digit, the
  crop does not contain the reading and OCR cannot invent it; when the clipped
  glyph happens to read as a clean digit (`"94"` → `"14"`), no integrity
  signal fires either. The operator's Verify step is the control for this, and
  it now shows a high-confidence, clearly-wrong candidate rather than a vague
  failure.
- **Dataset A's Temp scores 0/51 in both arms.** Pre-existing and untouched
  by this pass; its shared eval profile's Temp box does not land on that
  dataset's digits.
- **A systematic misread that is clean and repeats** can still confirm.
  Corroboration is a defense against independent noise; crop integrity covers
  the truncation class specifically. This is unchanged from M5.4.1 and stated
  in `live_corroboration.py`'s own docstring.
- **The confidence a correct read reports is still occasionally 0** on this
  Tesseract build (observed on SpO₂ in two real frames). Those reads hold
  rather than confirm.

---

## Operator note before the demo

The running backend must be **restarted** to pick these changes up — it was
started without `--reload`:

```
cd vital/backend
.venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

The existing `vital.db` is compatible and needs no migration. Sessions
recorded *before* the restart still contain the fabricated baseline and
mis-parsed rows described above; start a fresh case for the demo.
