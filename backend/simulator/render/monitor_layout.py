from simulator.render.compact import IMAGE_H as COMPACT_H
from simulator.render.compact import IMAGE_W as COMPACT_W
from simulator.render.compact import render_compact
from simulator.render.grid import IMAGE_H as GRID_H
from simulator.render.grid import IMAGE_W as GRID_W
from simulator.render.grid import render_grid
from simulator.render.sidebar import IMAGE_H as SIDEBAR_H
from simulator.render.sidebar import IMAGE_W as SIDEBAR_W
from simulator.render.sidebar import render_sidebar

# Registry of pluggable layouts. Each renderer has signature
# (reading: dict, out_path: str) -> dict and returns the S1 label contract:
# {"values": {...}, "rois": {vital: [x, y, w, h], ...}}.
LAYOUTS = {
    "grid": render_grid,
    "sidebar": render_sidebar,
    "compact": render_compact,
}

LAYOUT_SIZES = {
    "grid": (GRID_W, GRID_H),
    "sidebar": (SIDEBAR_W, SIDEBAR_H),
    "compact": (COMPACT_W, COMPACT_H),
}

# Backward-compatible exports: S1's default layout was the grid.
IMAGE_W = GRID_W
IMAGE_H = GRID_H


def render_monitor(reading: dict, out_path: str, layout: str = "grid") -> dict:
    """Draw one synthetic anaesthesia-monitor frame to out_path using `layout`.

    reading follows the frontend VitalReading shape (camelCase):
    hr, spo2, nibpSystolic, nibpDiastolic, nibpMean, etco2, temp, rr, timestamp.

    Returns {"values": <reading copy>, "rois": {vital: [x, y, w, h], ...}}.
    """
    try:
        renderer = LAYOUTS[layout]
    except KeyError:
        raise ValueError(f"Unknown layout '{layout}'. Choose from {sorted(LAYOUTS)}.") from None
    return renderer(reading, out_path)
