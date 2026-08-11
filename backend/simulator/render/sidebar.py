import math
from typing import Dict, List

from PIL import Image, ImageDraw

from simulator.render.common import (
    BG_COLOR,
    LABEL_COLOR,
    PANEL_BORDER,
    PANEL_COLOR,
    VITAL_COLORS,
    draw_vital_value,
    load_font,
    save_image,
)

# "sidebar" layout: waveform panel on the left ~60%, vitals stacked in a single
# column down the right ~40% — mirrors a real bedside monitor. Structurally
# distinct from "grid": different canvas, single-column stack instead of a
# multi-column table, different font scale.

IMAGE_W = 1200
IMAGE_H = 700
MARGIN = 16

LEFT_W = int(IMAGE_W * 0.6)
RIGHT_X0 = LEFT_W + MARGIN
RIGHT_W = IMAGE_W - RIGHT_X0 - MARGIN

ROW_ORDER = ["hr", "spo2", "nibp", "etco2", "temp", "rr"]
ROW_GAP = 8
_available_h = IMAGE_H - 2 * MARGIN
ROW_H = (_available_h - (len(ROW_ORDER) - 1) * ROW_GAP) // len(ROW_ORDER)

_WAVE_PATHS = [
    ("hr", 0.28),
    ("spo2", 0.52),
    ("etco2", 0.76),
]


def _draw_waveform_panel(draw: ImageDraw.ImageDraw) -> None:
    panel_box = [MARGIN, MARGIN, LEFT_W, IMAGE_H - MARGIN]
    draw.rectangle(panel_box, fill=PANEL_COLOR, outline=PANEL_BORDER, width=1)

    label_font = load_font(14)
    draw.text((MARGIN + 14, MARGIN + 10), "WAVEFORM DISPLAY", font=label_font, fill=LABEL_COLOR)

    # Decorative traces only — not part of any ROI / ground truth.
    span_w = LEFT_W - MARGIN - 40
    for vital, y_frac in _WAVE_PATHS:
        color = VITAL_COLORS[vital]
        base_y = MARGIN + int((IMAGE_H - 2 * MARGIN) * y_frac)
        points = []
        for x in range(0, span_w, 4):
            angle = x / 18.0
            y = base_y + int(14 * math.sin(angle) * math.exp(-((x % 220) / 220.0)))
            points.append((MARGIN + 20 + x, y))
        if len(points) > 1:
            draw.line(points, fill=color, width=2)


def render_sidebar(reading: dict, out_path: str) -> dict:
    img = Image.new("RGB", (IMAGE_W, IMAGE_H), BG_COLOR)
    draw = ImageDraw.Draw(img)

    _draw_waveform_panel(draw)

    label_font = load_font(13)
    value_font = load_font(40)
    nibp_value_font = load_font(30)
    mean_font = load_font(15)
    unit_font = load_font(11)

    rois: Dict[str, List[int]] = {}
    y = MARGIN
    for vital in ROW_ORDER:
        box = (RIGHT_X0, y, RIGHT_W, ROW_H)
        bx, by, bw, bh = box
        draw.rectangle([bx, by, bx + bw, by + bh], fill=PANEL_COLOR, outline=PANEL_BORDER, width=1)

        rois[vital] = draw_vital_value(
            draw, box, vital, reading,
            value_font=nibp_value_font if vital == "nibp" else value_font,
            mean_font=mean_font,
            label_font=label_font, unit_font=unit_font,
            value_top_offset=22 if vital == "nibp" else 26,
        )
        y += ROW_H + ROW_GAP

    save_image(img, out_path)
    return {"values": dict(reading), "rois": rois}
