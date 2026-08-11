from typing import Dict, List, Tuple

from PIL import Image, ImageDraw

from simulator.render.common import (
    BG_COLOR,
    PANEL_BORDER,
    PANEL_COLOR,
    draw_vital_value,
    load_font,
    save_image,
)

# "compact" layout: a denser 2x3 arrangement with a different vital order and
# a smaller canvas/font scale — visibly distinct from "grid" (3x2) and
# "sidebar" (single stacked column).

IMAGE_W = 640
IMAGE_H = 480
MARGIN = 12
COLS = 2
ROWS = 3

GRID_ORDER = [
    ["spo2", "hr"],
    ["rr", "nibp"],
    ["temp", "etco2"],
]


def _box_grid() -> Dict[str, Tuple[int, int, int, int]]:
    box_w = (IMAGE_W - MARGIN * (COLS + 1)) // COLS
    box_h = (IMAGE_H - MARGIN * (ROWS + 1)) // ROWS
    boxes: Dict[str, Tuple[int, int, int, int]] = {}
    for row_idx, row in enumerate(GRID_ORDER):
        for col_idx, vital in enumerate(row):
            bx = MARGIN + col_idx * (box_w + MARGIN)
            by = MARGIN + row_idx * (box_h + MARGIN)
            boxes[vital] = (bx, by, box_w, box_h)
    return boxes


def render_compact(reading: dict, out_path: str) -> dict:
    img = Image.new("RGB", (IMAGE_W, IMAGE_H), BG_COLOR)
    draw = ImageDraw.Draw(img)

    label_font = load_font(12)
    value_font = load_font(38)
    mean_font = load_font(17)
    unit_font = load_font(10)

    boxes = _box_grid()
    rois: Dict[str, List[int]] = {}

    for vital, box in boxes.items():
        bx, by, bw, bh = box
        draw.rectangle([bx, by, bx + bw, by + bh], fill=PANEL_COLOR, outline=PANEL_BORDER, width=1)
        rois[vital] = draw_vital_value(
            draw, box, vital, reading,
            value_font=value_font, mean_font=mean_font,
            label_font=label_font, unit_font=unit_font,
            pad=10,
            value_top_offset=30 if vital == "nibp" else 34,
        )

    save_image(img, out_path)
    return {"values": dict(reading), "rois": rois}
