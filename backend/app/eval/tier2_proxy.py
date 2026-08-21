"""M1 SYNTHETIC/PROXY dataset generator -- NOT the real-monitor benchmark.

Renders a "foreign-palette" monitor frame standing in for an unseen real
device: deliberately different colours, font, layout, and box positions than
simulator/render/*'s VITAL_COLORS/Consolas/fixed-grid conventions, wrapped in
a bezel + room background so app.pipeline.detect.detect_screen() has an
actual quad to find (not a screen-filling frame). This reproduces the shape
of the foreign-palette proxy TIER2_RECOGNITION_SPIKE.md section 03 describes
(same task: six labelled fields + one waveform-like decoy) -- that spike's
own script wasn't committed to the repo, so this is a fresh, from-scratch
implementation of the same idea, not a reuse of prior code.

Text colour is deliberately near-greyscale (low saturation) on most frames:
real clinical monitors commonly render every numeral in ONE colour (often
white/pale green), not VITAL's per-field rainbow -- and low saturation
pixels fail app.pipeline.roi's SAT_MIN=100 gate by construction, which is
exactly the real-world failure this M1 milestone investigates (see
TIER2_RECOGNITION_SPIKE.md section 02). A couple of mildly-saturated tones
are mixed in so the proxy isn't a strawman.

THIS IS NOT REAL-MONITOR DATA. Its results must always be reported under a
"SYNTHETIC / PROXY RESULTS" heading, never as real-monitor generalization
-- see app.eval.tier2_benchmark and TIER2_M1_BENCHMARK_REPORT.md.

Output shape matches simulator.generate's sample_XXXX.png/.json convention
exactly (id/values/rois/layout/augmentLevel/augmentations/timestamp), so
app.eval.harness.load_dataset() and app.eval.tier2_common's IoU/warp helpers
work against it unmodified.
"""

import json
import os
import random
import sys
import time
from typing import Dict, List, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from simulator.randomize.augment import augment_frame  # noqa: E402

VITALS = ("hr", "spo2", "nibp", "etco2", "temp", "rr")

CANVAS_W, CANVAS_H = 1280, 800

# Proportional UI fonts -- distinct from common.py's monospace candidates
# (Consolas/Courier), standing in for a real device's own UI font.
_FONT_CANDIDATES = [
    r"C:\Windows\Fonts\segoeui.ttf",
    r"C:\Windows\Fonts\arial.ttf",
    r"C:\Windows\Fonts\verdana.ttf",
    r"C:\Windows\Fonts\tahoma.ttf",
    r"C:\Windows\Fonts\calibri.ttf",
    r"C:\Windows\Fonts\georgia.ttf",
    "DejaVuSans.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]

# (background, text) pairs. Mostly low-saturation (near-grey) text, a couple
# mildly saturated -- see module docstring.
_PALETTES: List[Tuple[Tuple[int, int, int], Tuple[int, int, int]]] = [
    ((8, 8, 10), (235, 235, 235)),      # near-black bg, near-white text
    ((10, 12, 10), (210, 235, 210)),    # dark bg, pale green text (low sat)
    ((250, 250, 248), (20, 20, 20)),    # light bg, near-black text (inverted polarity)
    ((6, 6, 8), (225, 225, 235)),       # near-black bg, pale blue-white text
    ((12, 10, 6), (230, 210, 160)),     # dark bg, pale amber text (mildly saturated)
    ((4, 4, 4), (255, 255, 255)),       # pure black bg, pure white text
]

VITAL_UNITS = {"hr": "bpm", "spo2": "%", "nibp": "mmHg", "etco2": "mmHg", "temp": "C", "rr": "/min"}


def _load_font(rng: random.Random, size: int) -> ImageFont.ImageFont:
    path = rng.choice(_FONT_CANDIDATES)
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        try:
            return ImageFont.load_default(size=size)
        except TypeError:
            return ImageFont.load_default()


def _sample_reading(rng: random.Random) -> dict:
    return {
        "hr": rng.randint(55, 130),
        "spo2": rng.randint(90, 100),
        "nibpSystolic": rng.randint(95, 150),
        "nibpDiastolic": rng.randint(55, 95),
        "nibpMean": rng.randint(70, 110),
        "etco2": rng.randint(28, 45),
        "temp": round(rng.uniform(35.5, 38.5), 1),
        "rr": rng.randint(8, 22),
        "timestamp": int(time.time() * 1000),
    }


def _format_value(vital: str, reading: dict) -> str:
    if vital == "nibp":
        return f"{reading['nibpSystolic']}/{reading['nibpDiastolic']}"
    if vital == "temp":
        return f"{reading['temp']:.1f}"
    return f"{reading[vital]}"


def _draw_decoy_waveform(draw: ImageDraw.ImageDraw, box: Tuple[int, int, int, int], color: Tuple[int, int, int], rng: random.Random) -> None:
    """A thin, sparse squiggly trace -- same false-positive bait roi.py's own
    comments describe (waveform traces share glyph colour/luminance but are
    much less dense than solid strokes); tests whether a candidate generator
    incorrectly proposes it as a vital-value box."""
    x, y, w, h = box
    points = []
    n = 40
    for i in range(n):
        px = x + int(w * i / (n - 1))
        py = y + h // 2 + int((h // 2 - 4) * rng.uniform(-1, 1) * (0.3 + 0.7 * rng.random()))
        points.append((px, py))
    draw.line(points, fill=color, width=2)


def render_proxy_frame(out_path: str, seed: int) -> dict:
    """Renders one proxy frame + returns the S1-shaped label dict
    ({"values", "rois"}) with box geometry in the ORIGINAL (pre-augmentation,
    pre-bezel-crop) canvas coordinate space."""
    rng = random.Random(seed)
    reading = _sample_reading(rng)
    bg, fg = rng.choice(_PALETTES)

    room_shade = rng.randint(150, 210)
    canvas = Image.new("RGB", (CANVAS_W, CANVAS_H), (room_shade, room_shade, room_shade + rng.randint(-10, 10)))
    draw = ImageDraw.Draw(canvas)

    # Bezel + screen inset -- gives detect_screen() an actual quad to find,
    # per the spike's "rendered bezel + room background" recommendation.
    margin = rng.randint(60, 140)
    bezel_thick = rng.randint(14, 34)
    screen_x0, screen_y0 = margin, margin
    screen_x1, screen_y1 = CANVAS_W - margin, CANVAS_H - margin
    draw.rectangle([screen_x0 - bezel_thick, screen_y0 - bezel_thick, screen_x1 + bezel_thick, screen_y1 + bezel_thick], fill=(30, 30, 32))
    draw.rectangle([screen_x0, screen_y0, screen_x1, screen_y1], fill=bg)

    screen_w = screen_x1 - screen_x0
    screen_h = screen_y1 - screen_y0

    # 2x3 grid, randomized per-frame cell padding/jitter -- deliberately NOT
    # VITAL's fixed layout constants.
    cols, rows = 3, 2
    cell_w = screen_w / cols
    cell_h = screen_h / rows
    order = list(VITALS)
    rng.shuffle(order)

    value_font_size = rng.randint(34, 52)
    unit_font_size = max(10, value_font_size // 3)
    value_font = _load_font(rng, value_font_size)
    unit_font = _load_font(rng, unit_font_size)

    rois: Dict[str, List[int]] = {}
    for idx, vital in enumerate(order):
        col, row = idx % cols, idx // cols
        cell_x0 = screen_x0 + col * cell_w
        cell_y0 = screen_y0 + row * cell_h
        pad_x = rng.uniform(0.08, 0.22) * cell_w
        pad_y = rng.uniform(0.15, 0.35) * cell_h
        vx = cell_x0 + pad_x + rng.uniform(-6, 6)
        vy = cell_y0 + pad_y + rng.uniform(-6, 6)

        text = _format_value(vital, reading)
        draw.text((vx, vy), text, font=value_font, fill=fg)
        bbox = draw.textbbox((vx, vy), text, font=value_font)
        rois[vital] = [int(bbox[0]), int(bbox[1]), int(bbox[2] - bbox[0]), int(bbox[3] - bbox[1])]

        unit_y = bbox[3] + 4
        draw.text((vx, unit_y), VITAL_UNITS[vital], font=unit_font, fill=fg)

    # Decoy waveform strip along the bottom of the screen.
    decoy_box = (int(screen_x0 + 0.05 * screen_w), int(screen_y1 - 0.16 * screen_h), int(0.9 * screen_w), int(0.10 * screen_h))
    _draw_decoy_waveform(draw, decoy_box, fg, rng)

    canvas.save(out_path)
    return {"values": reading, "rois": rois}


def generate_proxy_dataset(out_dir: str, count: int, augment: str = "random", seed: int = 0) -> List[dict]:
    """Writes sample_0000.png/.json .. sample_{count-1}.png/.json under
    out_dir, in the exact shape app.eval.harness.load_dataset() reads."""
    os.makedirs(out_dir, exist_ok=True)
    written = []
    for i in range(count):
        sample_seed = seed * 100_000 + i
        sample_id = f"sample_{i:04d}"
        png_path = os.path.join(out_dir, f"{sample_id}.png")
        json_path = os.path.join(out_dir, f"{sample_id}.json")

        label = render_proxy_frame(png_path, seed=sample_seed)

        if augment == "none":
            augmentations: List[dict] = []
        else:
            image = Image.open(png_path).convert("RGB")
            augmented_image, augmented_rois, applied = augment_frame(image, label["rois"], level=augment, seed=sample_seed)
            augmented_image.save(png_path)
            label = {"values": label["values"], "rois": augmented_rois}
            augmentations = applied

        record = {
            "id": sample_id,
            "values": label["values"],
            "rois": label["rois"],
            "layout": "proxy_foreign_palette",
            "augmentLevel": augment,
            "augmentations": augmentations,
            "timestamp": label["values"]["timestamp"],
        }
        with open(json_path, "w") as f:
            json.dump(record, f, indent=2)
        written.append(record)
    return written


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", required=True, help="Output directory for sample_*.png/.json")
    parser.add_argument("--count", type=int, default=24)
    parser.add_argument("--augment", default="random", choices=["none", "light", "heavy", "random"])
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()

    written = generate_proxy_dataset(args.out, args.count, augment=args.augment, seed=args.seed)
    print(f"Wrote {len(written)} proxy samples to {args.out}")


if __name__ == "__main__":
    main()
