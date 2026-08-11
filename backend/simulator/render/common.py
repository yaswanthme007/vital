import os
from typing import Dict, List, Tuple

from PIL import ImageDraw, ImageFont

# Shared visual conventions for every layout (frontend ReviewPage.tsx VITAL_CFG).

BG_COLOR = (6, 10, 18)
PANEL_COLOR = (10, 21, 37)
PANEL_BORDER = (24, 43, 66)
LABEL_COLOR = (61, 85, 112)

VITAL_COLORS = {
    "hr": (0, 255, 136),
    "spo2": (0, 212, 255),
    "nibp": (255, 71, 87),
    "etco2": (255, 214, 0),
    "temp": (255, 149, 0),
    "rr": (191, 90, 242),
}

VITAL_LABELS = {
    "hr": "HR",
    "spo2": "SpO2",
    "nibp": "NIBP",
    "etco2": "EtCO2",
    "temp": "TEMP",
    "rr": "RR",
}

VITAL_UNITS = {
    "hr": "bpm",
    "spo2": "%",
    "nibp": "mmHg",
    "etco2": "mmHg",
    "temp": "C",
    "rr": "/min",
}

ALL_VITALS = ("hr", "spo2", "nibp", "etco2", "temp", "rr")

_FONT_CANDIDATES_MONO = [
    "consola.ttf",
    r"C:\Windows\Fonts\consola.ttf",
    "Consolas.ttf",
    "DejaVuSansMono.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "cour.ttf",
    r"C:\Windows\Fonts\cour.ttf",
    "/usr/share/fonts/truetype/msttcorefonts/Courier_New.ttf",
]


def load_font(size: int) -> ImageFont.ImageFont:
    for path in _FONT_CANDIDATES_MONO:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def format_value(vital: str, value: float) -> str:
    if vital == "temp":
        return f"{value:.1f}"
    return f"{round(value)}"


def save_image(img, out_path: str) -> None:
    out_dir = os.path.dirname(os.path.abspath(out_path))
    os.makedirs(out_dir, exist_ok=True)
    img.save(out_path)


def draw_vital_value(
    draw: ImageDraw.ImageDraw,
    box: Tuple[int, int, int, int],
    vital: str,
    reading: dict,
    value_font: ImageFont.ImageFont,
    mean_font: ImageFont.ImageFont,
    label_font: ImageFont.ImageFont = None,
    unit_font: ImageFont.ImageFont = None,
    pad: int = 14,
    value_top_offset: int = 40,
) -> List[int]:
    """Draw one vital's label + value (+ mean, for NIBP) + unit inside `box`
    (bx, by, bw, bh). Returns the ROI [x, y, w, h] tightly bounding the value
    text — this is the ground-truth box, deliberately excluding the label/unit."""
    bx, by, bw, bh = box
    color = VITAL_COLORS[vital]

    if label_font is not None:
        draw.text((bx + pad, by + 8), VITAL_LABELS[vital], font=label_font, fill=LABEL_COLOR)

    value_top = by + value_top_offset

    if vital == "nibp":
        main_text = f"{round(reading['nibpSystolic'])}/{round(reading['nibpDiastolic'])}"
        mean_text = f"{round(reading['nibpMean'])}"

        main_pos = (bx + pad, value_top)
        draw.text(main_pos, main_text, font=value_font, fill=color)
        main_bbox = draw.textbbox(main_pos, main_text, font=value_font)

        mean_pos = (bx + pad, main_bbox[3] + 2)
        draw.text(mean_pos, mean_text, font=mean_font, fill=color)
        mean_bbox = draw.textbbox(mean_pos, mean_text, font=mean_font)

        roi = [
            bx + pad,
            main_bbox[1],
            max(main_bbox[2], mean_bbox[2]) - (bx + pad),
            mean_bbox[3] - main_bbox[1],
        ]
    else:
        value_text = format_value(vital, reading[vital])
        value_pos = (bx + pad, value_top)
        draw.text(value_pos, value_text, font=value_font, fill=color)
        value_bbox = draw.textbbox(value_pos, value_text, font=value_font)
        roi = [
            value_bbox[0],
            value_bbox[1],
            value_bbox[2] - value_bbox[0],
            value_bbox[3] - value_bbox[1],
        ]

    if unit_font is not None:
        draw.text((bx + pad, by + bh - 20), VITAL_UNITS[vital], font=unit_font, fill=LABEL_COLOR)

    return [int(v) for v in roi]
