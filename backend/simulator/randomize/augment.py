import random
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np
from PIL import Image

Roi = List[int]  # [x, y, w, h]

# Canonical application order: warp geometry first, then simulate camera/lighting
# artifacts, with occlusion last (as if something is blocking part of the view
# at the moment of capture).
EFFECT_ORDER = ["perspective", "glare", "dim", "blur", "noise", "occlusion"]

_PROBABILITY = {
    "none": {},
    "light": {"perspective": 0.15, "glare": 0.25, "dim": 0.25, "blur": 0.35, "noise": 0.50, "occlusion": 0.10},
    "heavy": {"perspective": 0.65, "glare": 0.55, "dim": 0.50, "blur": 0.55, "noise": 0.75, "occlusion": 0.35},
    "random": {"perspective": 0.50, "glare": 0.50, "dim": 0.50, "blur": 0.50, "noise": 0.50, "occlusion": 0.35},
}

_SEVERITY_RANGE = {
    "none": (0.0, 0.0),
    "light": (0.15, 0.45),
    "heavy": (0.55, 1.0),
    "random": (0.15, 1.0),
}


# ─── ROI <-> geometry helpers ────────────────────────────────────────────────


def _roi_to_corners(roi: Roi) -> np.ndarray:
    x, y, w, h = roi
    return np.array(
        [[x, y], [x + w, y], [x + w, y + h], [x, y + h]],
        dtype=np.float32,
    )


def _transform_points(points: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    pts = points.reshape(-1, 1, 2).astype(np.float32)
    transformed = cv2.perspectiveTransform(pts, matrix)
    return transformed.reshape(-1, 2)


def _corners_to_roi(corners: np.ndarray, width: int, height: int) -> Roi:
    xs = corners[:, 0]
    ys = corners[:, 1]
    x0 = float(np.clip(np.min(xs), 0, width))
    y0 = float(np.clip(np.min(ys), 0, height))
    x1 = float(np.clip(np.max(xs), 0, width))
    y1 = float(np.clip(np.max(ys), 0, height))
    return [int(round(x0)), int(round(y0)), int(round(max(0.0, x1 - x0))), int(round(max(0.0, y1 - y0)))]


def _fully_covers_any_roi(box: Tuple[int, int, int, int], rois: Dict[str, Roi]) -> bool:
    bx, by, bw, bh = box
    for roi in rois.values():
        rx, ry, rw, rh = roi
        if rw <= 0 or rh <= 0:
            continue
        if bx <= rx and by <= ry and bx + bw >= rx + rw and by + bh >= ry + rh:
            return True
    return False


def _motion_blur_kernel(size: int, angle_deg: float) -> np.ndarray:
    kernel = np.zeros((size, size), dtype=np.float32)
    kernel[size // 2, :] = 1.0
    center = (size / 2 - 0.5, size / 2 - 0.5)
    rot = cv2.getRotationMatrix2D(center, angle_deg, 1.0)
    kernel = cv2.warpAffine(kernel, rot, (size, size))
    total = kernel.sum()
    if total > 0:
        kernel /= total
    return kernel


# ─── Individual effects ──────────────────────────────────────────────────────
# Each takes (img: np.ndarray RGB uint8, ..., rng, severity in [0, 1]) and
# returns (new_img, meta). Only apply_perspective also transforms ROIs, since
# it's the only geometric effect.


def apply_perspective(
    img: np.ndarray, rois: Dict[str, Roi], rng: random.Random, severity: float = 1.0
) -> Tuple[np.ndarray, Dict[str, Roi], dict]:
    h, w = img.shape[:2]
    max_shift = severity * 0.10 * min(w, h)

    src = np.array([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]], dtype=np.float32)
    jitter = np.array(
        [[rng.uniform(-max_shift, max_shift), rng.uniform(-max_shift, max_shift)] for _ in range(4)],
        dtype=np.float32,
    )
    dst = src + jitter

    matrix = cv2.getPerspectiveTransform(src, dst)
    warped = cv2.warpPerspective(img, matrix, (w, h), borderMode=cv2.BORDER_REPLICATE)

    new_rois: Dict[str, Roi] = {}
    for vital, roi in rois.items():
        corners = _transform_points(_roi_to_corners(roi), matrix)
        new_rois[vital] = _corners_to_roi(corners, w, h)

    meta = {"type": "perspective", "max_shift_px": round(max_shift, 2), "matrix": matrix.tolist()}
    return warped, new_rois, meta


def apply_glare(img: np.ndarray, rng: random.Random, severity: float = 1.0) -> Tuple[np.ndarray, dict]:
    h, w = img.shape[:2]
    cx = rng.uniform(0, w)
    cy = rng.uniform(0, h)
    radius = rng.uniform(0.12, 0.42) * min(w, h)
    intensity = 30 + rng.uniform(20, 130) * severity

    yy, xx = np.mgrid[0:h, 0:w]
    dist = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
    falloff = np.clip(1 - dist / radius, 0, 1) ** 2
    glare = (falloff * intensity).astype(np.float32)

    out = np.clip(img.astype(np.float32) + glare[..., None], 0, 255).astype(np.uint8)
    meta = {
        "type": "glare",
        "center": [round(cx, 1), round(cy, 1)],
        "radius_px": round(radius, 1),
        "intensity": round(intensity, 1),
    }
    return out, meta


def apply_dim(img: np.ndarray, rng: random.Random, severity: float = 1.0) -> Tuple[np.ndarray, dict]:
    brightness = -rng.uniform(15, 95) * severity
    contrast = 1 - rng.uniform(0.1, 0.55) * severity

    out = np.clip(img.astype(np.float32) * contrast + brightness, 0, 255).astype(np.uint8)
    meta = {"type": "dim", "brightness": round(brightness, 1), "contrast": round(contrast, 2)}
    return out, meta


def apply_blur(img: np.ndarray, rng: random.Random, severity: float = 1.0) -> Tuple[np.ndarray, dict]:
    max_k = 3 + int(round(severity * 8))
    k = rng.randrange(3, max_k + 1, 2)

    if rng.random() < 0.4:
        angle = rng.uniform(0, 360)
        kernel = _motion_blur_kernel(k, angle)
        out = cv2.filter2D(img, -1, kernel)
        return out, {"type": "motion_blur", "kernel": k, "angle_deg": round(angle, 1)}

    out = cv2.GaussianBlur(img, (k, k), 0)
    return out, {"type": "gaussian_blur", "kernel": k}


def apply_noise(img: np.ndarray, rng: random.Random, severity: float = 1.0) -> Tuple[np.ndarray, dict]:
    sigma = 2 + rng.uniform(2, 20) * severity
    np_seed = rng.randint(0, 2**32 - 1)
    npr = np.random.default_rng(np_seed)
    noise = npr.normal(0, sigma, img.shape)
    noisy = np.clip(img.astype(np.float32) + noise, 0, 255).astype(np.uint8)

    quality = int(max(20, 92 - rng.uniform(20, 70) * severity))
    # Channel order doesn't matter here — we only want authentic block/DCT
    # compression artifacts, not colorimetric accuracy.
    ok, encoded = cv2.imencode(".jpg", noisy, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    compressed = cv2.imdecode(encoded, cv2.IMREAD_COLOR) if ok else None
    if compressed is None:
        compressed = noisy

    meta = {"type": "noise", "sigma": round(sigma, 2), "jpeg_quality": quality}
    return compressed, meta


def apply_occlusion(
    img: np.ndarray, rois: Dict[str, Roi], rng: random.Random, severity: float = 1.0
) -> Tuple[np.ndarray, dict]:
    h, w = img.shape[:2]
    frac = rng.uniform(0.05, 0.08 + 0.09 * severity)
    box_w = max(6, int(frac * w))
    box_h = max(6, int(frac * h))

    x0, y0 = 0, 0
    for _ in range(25):
        cand_x = rng.randint(0, max(0, w - box_w))
        cand_y = rng.randint(0, max(0, h - box_h))
        if not _fully_covers_any_roi((cand_x, cand_y, box_w, box_h), rois):
            x0, y0 = cand_x, cand_y
            break
    else:
        box_w, box_h = max(6, box_w // 2), max(6, box_h // 2)
        x0 = rng.randint(0, max(0, w - box_w))
        y0 = rng.randint(0, max(0, h - box_h))

    color = rng.choice([(10, 10, 10), (25, 25, 25), (60, 60, 65), (0, 0, 0)])
    out = img.copy()
    cv2.rectangle(out, (x0, y0), (x0 + box_w, y0 + box_h), color, thickness=-1)
    meta = {"type": "occlusion", "box": [x0, y0, box_w, box_h]}
    return out, meta


# ─── Composable pipeline ─────────────────────────────────────────────────────


def augment_frame(
    image: Image.Image, rois: Dict[str, Roi], level: str = "random", seed: Optional[int] = None
) -> Tuple[Image.Image, Dict[str, Roi], List[dict]]:
    """Apply a randomized, composable set of camera/lighting effects to `image`.

    Any geometric effect (currently just perspective warp) is applied to the
    ROI boxes with the exact same transform, so `rois` stays correct for the
    returned image. Returns (augmented_image, augmented_rois, applied_effects).
    """
    if level not in _PROBABILITY:
        raise ValueError(f"Unknown augmentation level '{level}'. Choose from {sorted(_PROBABILITY)}.")

    rng = random.Random(seed)
    img = np.array(image.convert("RGB"))
    current_rois: Dict[str, Roi] = {k: list(v) for k, v in rois.items()}
    applied: List[dict] = []

    if level == "none":
        return Image.fromarray(img), current_rois, applied

    probs = _PROBABILITY[level]
    selected = [name for name in EFFECT_ORDER if rng.random() < probs[name]]
    if not selected:
        # Guarantee at least one effect at any non-"none" level.
        selected = [rng.choice(EFFECT_ORDER)]

    lo, hi = _SEVERITY_RANGE[level]

    for name in selected:
        severity = rng.uniform(lo, hi)
        if name == "perspective":
            img, current_rois, meta = apply_perspective(img, current_rois, rng, severity)
        elif name == "glare":
            img, meta = apply_glare(img, rng, severity)
        elif name == "dim":
            img, meta = apply_dim(img, rng, severity)
        elif name == "blur":
            img, meta = apply_blur(img, rng, severity)
        elif name == "noise":
            img, meta = apply_noise(img, rng, severity)
        elif name == "occlusion":
            img, meta = apply_occlusion(img, current_rois, rng, severity)
        applied.append(meta)

    return Image.fromarray(img), current_rois, applied


def augment_sample(
    image: Image.Image, label: dict, level: str = "random", seed: Optional[int] = None
) -> Tuple[Image.Image, dict]:
    """Convenience wrapper for label dicts shaped {"values": {...}, "rois": {...}}.

    Returns (augmented_image, new_label) where new_label carries the SAME
    values (augmentation never touches ground-truth vitals), POST-transform
    rois, and the list of effects actually applied.
    """
    augmented_image, augmented_rois, applied = augment_frame(image, label["rois"], level=level, seed=seed)
    new_label = dict(label)
    new_label["rois"] = augmented_rois
    new_label["augmentations"] = applied
    new_label["augmentLevel"] = level
    return augmented_image, new_label
