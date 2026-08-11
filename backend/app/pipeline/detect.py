from typing import Optional

import cv2
import numpy as np

from app.pipeline.types import ScreenDetectionResult

# Many of our synthetic frames (and plenty of tight real photos) are already
# screen-filling with no bezel — a genuine quad won't always exist. We only
# accept a candidate if it covers a large share of the frame, biasing toward
# "the whole screen" rather than an inner panel/box.
MIN_AREA_FRAC = 0.5


def _order_quad_points(pts: np.ndarray) -> np.ndarray:
    """Order 4 points as [top-left, top-right, bottom-right, bottom-left]."""
    rect = np.zeros((4, 2), dtype=np.float32)
    s = pts.sum(axis=1)
    rect[0] = pts[np.argmin(s)]
    rect[2] = pts[np.argmax(s)]
    diff = np.diff(pts, axis=1).reshape(-1)
    rect[1] = pts[np.argmin(diff)]
    rect[3] = pts[np.argmax(diff)]
    return rect


def _find_screen_quad(img: np.ndarray, min_area_frac: float) -> Optional[np.ndarray]:
    h, w = img.shape[:2]
    total_area = h * w

    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY) if img.ndim == 3 else img
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blurred, 50, 150)
    edges = cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=2)

    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    contours = sorted(contours, key=cv2.contourArea, reverse=True)

    for contour in contours[:15]:
        area = cv2.contourArea(contour)
        if area < total_area * min_area_frac:
            break  # sorted descending — nothing bigger remains

        peri = cv2.arcLength(contour, True)
        approx = cv2.approxPolyDP(contour, 0.02 * peri, True)
        if len(approx) == 4 and cv2.isContourConvex(approx):
            return approx.reshape(4, 2).astype(np.float32)

    return None


def detect_screen(img: np.ndarray, min_area_frac: float = MIN_AREA_FRAC) -> ScreenDetectionResult:
    """Find the largest screen-like quadrilateral in `img` (RGB uint8 ndarray)
    and rectify it to a flat, axis-aligned rectangle via a homography.

    If no confident quad is found, returns the image unchanged with
    detected=False rather than guessing.
    """
    quad = _find_screen_quad(img, min_area_frac)
    if quad is None:
        return ScreenDetectionResult(image=img, detected=False, homography=None, corners=None)

    ordered = _order_quad_points(quad)
    tl, tr, br, bl = ordered

    dst_w = int(max(np.linalg.norm(tr - tl), np.linalg.norm(br - bl)))
    dst_h = int(max(np.linalg.norm(bl - tl), np.linalg.norm(br - tr)))
    dst_w = max(dst_w, 10)
    dst_h = max(dst_h, 10)

    dst = np.array([[0, 0], [dst_w - 1, 0], [dst_w - 1, dst_h - 1], [0, dst_h - 1]], dtype=np.float32)
    matrix = cv2.getPerspectiveTransform(ordered, dst)
    rectified = cv2.warpPerspective(img, matrix, (dst_w, dst_h))

    return ScreenDetectionResult(image=rectified, detected=True, homography=matrix, corners=ordered)
