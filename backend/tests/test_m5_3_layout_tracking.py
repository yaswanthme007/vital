"""M5.3: layout tracking -- transform estimation, the safety gates that
decide whether a transform is trustworthy, fail-closed ROI withholding,
reference-frame persistence, and live camera/WS wiring.

Follows the same pattern as test_m5_2_calibration.py: real modules, real DB
(conftest.py's temp-file SQLite), no mocked pipeline internals. Synthetic
images are used where a KNOWN transform is needed so the assertion has a
right answer to check against; the real-data measurements live in
app/eval/m5_3_tracking_eval.py and are reported in
docs/M5_3_LAYOUT_TRACKING_REPORT.md.
"""

import io
import json
import os

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.db import repo
from app.db.session import SessionLocal
from app.main import app
from app.models.calibration import CalibrationFieldMeta, CalibrationProfile, NormalizedBox
from app.pipeline.calibrated_roi import make_extractor, reference_pixel_boxes
from app.pipeline.layout_tracker import (
    MAX_ROTATION_DEG,
    MAX_SCALE,
    MIN_INLIERS,
    LayoutTracker,
    TrackingStatus,
    check_transformed_rois,
    transform_box,
)
from app.sources.camera import CameraSource

client = TestClient(app)

REF_W, REF_H = 900, 600


def _textured_frame(w: int = REF_W, h: int = REF_H, seed: int = 7) -> np.ndarray:
    """A synthetic 'monitor': lots of stable, high-contrast structure so ORB
    has real corners to anchor on. Deterministic for a given seed so a test
    asserting on inlier counts cannot flake."""
    rng = np.random.default_rng(seed)
    img = np.full((h, w, 3), 18, np.uint8)
    for i in range(28):
        x, y = int(rng.integers(20, w - 120)), int(rng.integers(20, h - 90))
        cw, ch = int(rng.integers(40, 110)), int(rng.integers(30, 80))
        colour = tuple(int(c) for c in rng.integers(90, 255, size=3))
        cv2.rectangle(img, (x, y), (x + cw, y + ch), colour, 2)
        cv2.putText(img, f"L{i:02d}", (x + 4, y + ch - 6), cv2.FONT_HERSHEY_SIMPLEX,
                    0.5, colour, 1, cv2.LINE_AA)
    return img


def _warp(img: np.ndarray, scale: float = 1.0, rotation: float = 0.0,
          tx: float = 0.0, ty: float = 0.0) -> np.ndarray:
    h, w = img.shape[:2]
    M = cv2.getRotationMatrix2D((w / 2.0, h / 2.0), rotation, scale)
    M[0, 2] += tx
    M[1, 2] += ty
    return cv2.warpAffine(img, M, (w, h), flags=cv2.INTER_LINEAR)


def _profile(boxes=None, **overrides) -> CalibrationProfile:
    defaults = dict(
        id="m5_3-test",
        reference_width=REF_W,
        reference_height=REF_H,
        roi_boxes=boxes or {
            "hr": NormalizedBox(x=0.10, y=0.10, w=0.14, h=0.12),
            "spo2": NormalizedBox(x=0.60, y=0.55, w=0.14, h=0.12),
        },
        created_at=0,
        updated_at=0,
    )
    defaults.update(overrides)
    return CalibrationProfile(**defaults)


def _tracker(reference: np.ndarray, profile: CalibrationProfile) -> LayoutTracker:
    return LayoutTracker.from_reference_image(
        reference, exclude_boxes=list(reference_pixel_boxes(profile).values())
    )


# ─── transform estimation ───────────────────────────────────────────────────


def test_identical_frame_tracks_as_identity():
    ref = _textured_frame()
    result = _tracker(ref, _profile()).track(ref.copy())
    assert result.status is TrackingStatus.OK
    assert result.n_inliers >= MIN_INLIERS
    assert result.scale == pytest.approx(1.0, abs=0.01)
    assert result.rotation_deg == pytest.approx(0.0, abs=0.5)


@pytest.mark.parametrize("scale,rotation", [(1.25, 0.0), (0.8, 0.0), (1.0, 7.0), (1.2, -6.0)])
def test_recovers_known_similarity_transform(scale, rotation):
    """Tracking must return the RIGHT geometry, not merely some geometry."""
    ref = _textured_frame()
    result = _tracker(ref, _profile()).track(_warp(ref, scale=scale, rotation=rotation))
    assert result.status is TrackingStatus.OK
    assert result.scale == pytest.approx(scale, abs=0.05)
    # getRotationMatrix2D is counter-clockwise-positive; the recovered angle
    # (atan2 on the matrix) reads clockwise-positive, hence the magnitude test.
    assert abs(result.rotation_deg) == pytest.approx(abs(rotation), abs=1.5)


def test_tracking_is_deterministic_across_repeated_calls():
    ref = _textured_frame()
    tracker = _tracker(ref, _profile())
    frame = _warp(ref, scale=1.1, rotation=3.0)
    a, b = tracker.track(frame), tracker.track(frame)
    assert a.status is b.status
    assert a.n_inliers == b.n_inliers
    assert np.array_equal(a.transform, b.transform)


# ─── the safety gates ───────────────────────────────────────────────────────


def test_unrelated_scene_is_rejected_not_fitted():
    """The core safety property: a frame that does not show the calibrated
    monitor must never yield a transform."""
    tracker = _tracker(_textured_frame(seed=1), _profile())
    result = tracker.track(_textured_frame(seed=99))
    assert not result.ok
    assert result.status in (TrackingStatus.LOW_INLIER_COUNT, TrackingStatus.LOW_FEATURE_MATCHES,
                             TrackingStatus.ESTIMATION_FAILED, TrackingStatus.INVALID_SCALE,
                             TrackingStatus.INVALID_ROTATION, TrackingStatus.INVALID_TRANSLATION)
    assert result.transform is None
    assert result.reject_reasons


def test_featureless_frame_reports_no_features():
    tracker = _tracker(_textured_frame(), _profile())
    result = tracker.track(np.zeros((REF_H, REF_W, 3), np.uint8))
    assert result.status is TrackingStatus.NO_FRAME_FEATURES
    assert result.transform is None


def test_pure_noise_is_rejected():
    rng = np.random.default_rng(3)
    tracker = _tracker(_textured_frame(), _profile())
    result = tracker.track((rng.random((REF_H, REF_W, 3)) * 255).astype(np.uint8))
    assert not result.ok


def test_extreme_scale_is_rejected():
    ref = _textured_frame()
    result = _tracker(ref, _profile()).track(_warp(ref, scale=MAX_SCALE + 2.0))
    assert not result.ok
    assert result.status in (TrackingStatus.INVALID_SCALE, TrackingStatus.LOW_INLIER_COUNT,
                             TrackingStatus.LOW_FEATURE_MATCHES)


def test_extreme_rotation_is_rejected():
    ref = _textured_frame()
    result = _tracker(ref, _profile()).track(_warp(ref, rotation=MAX_ROTATION_DEG + 60.0))
    assert not result.ok
    assert result.status in (TrackingStatus.INVALID_ROTATION, TrackingStatus.LOW_INLIER_COUNT)


def test_reference_with_no_features_never_tracks():
    tracker = LayoutTracker.from_reference_image(np.zeros((REF_H, REF_W, 3), np.uint8))
    result = tracker.track(_textured_frame())
    assert result.status is TrackingStatus.NO_REFERENCE_FEATURES
    assert result.transform is None


def test_calibrated_roi_regions_are_masked_out_of_the_reference():
    """ROADMAP.md M5.3: anchor on the monitor's static chrome, never the
    digits -- templating the changing digits tracked WORSE than not tracking."""
    ref = _textured_frame()
    profile = _profile(boxes={"hr": NormalizedBox(x=0.0, y=0.0, w=0.9, h=0.9)})
    masked = _tracker(ref, profile)
    unmasked = LayoutTracker.from_reference_image(ref)
    assert masked.n_reference_keypoints < unmasked.n_reference_keypoints


# ─── transformed-ROI geometry gates ─────────────────────────────────────────


def test_transformed_roi_leaving_the_frame_is_rejected():
    ref_boxes = {"hr": (100.0, 100.0, 120.0, 80.0)}
    moved = {"hr": (-115.0, 100.0, 120.0, 80.0)}  # almost entirely off-frame
    assert check_transformed_rois(moved, ref_boxes, REF_W, REF_H)


def test_transformed_rois_overlapping_each_other_are_rejected():
    ref_boxes = {"hr": (100.0, 100.0, 120.0, 80.0), "spo2": (400.0, 100.0, 120.0, 80.0)}
    collapsed = {"hr": (100.0, 100.0, 120.0, 80.0), "spo2": (105.0, 100.0, 120.0, 80.0)}
    assert check_transformed_rois(collapsed, ref_boxes, REF_W, REF_H)


def test_transformed_roi_with_implausible_area_change_is_rejected():
    ref_boxes = {"hr": (100.0, 100.0, 120.0, 80.0)}
    ballooned = {"hr": (100.0, 100.0, 480.0, 320.0)}  # 16x the calibrated area
    assert check_transformed_rois(ballooned, ref_boxes, REF_W, REF_H)


def test_sane_transformed_rois_pass_the_geometry_gates():
    ref_boxes = {"hr": (100.0, 100.0, 120.0, 80.0), "spo2": (400.0, 300.0, 120.0, 80.0)}
    shifted = {"hr": (140.0, 130.0, 132.0, 88.0), "spo2": (440.0, 330.0, 132.0, 88.0)}
    assert check_transformed_rois(shifted, ref_boxes, REF_W, REF_H) == []


def test_transform_box_maps_corners_to_an_axis_aligned_bbox():
    M = np.array([[2.0, 0.0, 10.0], [0.0, 2.0, 20.0]], dtype=np.float32)
    assert transform_box((5.0, 5.0, 10.0, 10.0), M) == pytest.approx((20.0, 30.0, 20.0, 20.0))


# ─── fail-closed integration with calibrated_roi ────────────────────────────


def test_tracking_success_moves_the_crop_with_the_layout():
    ref = _textured_frame()
    profile = _profile()
    extractor = make_extractor(profile, tracker=_tracker(ref, profile))

    static_boxes = {v: r.box for v, r in make_extractor(profile)(ref).items()}
    tracked = extractor(_warp(ref, tx=60.0, ty=40.0))
    assert all(r is not None for r in tracked.values())
    # The box must have MOVED with the layout, not stayed put.
    assert tracked["hr"].box != static_boxes["hr"]
    assert tracked["hr"].box[0] == pytest.approx(static_boxes["hr"][0] + 60, abs=8)
    assert tracked["hr"].box[1] == pytest.approx(static_boxes["hr"][1] + 40, abs=8)
    assert tracked["hr"].engine == "calibrated_tracked"


def test_tracking_failure_withholds_every_field():
    """Fail closed. A failed tracker must never fall back to the stale static
    box, and must never crop from an unverified region."""
    profile = _profile()
    extractor = make_extractor(profile, tracker=_tracker(_textured_frame(seed=1), profile))
    rng = np.random.default_rng(11)
    result = extractor((rng.random((REF_H, REF_W, 3)) * 255).astype(np.uint8))
    assert set(result) == set(profile.roi_boxes)
    assert all(v is None for v in result.values())


def test_untracked_extractor_is_unchanged_m5_2_behaviour():
    """The rollback path: with no tracker, geometry is M5.2's static mapping."""
    profile = _profile()
    ref = _textured_frame()
    static = make_extractor(profile)(ref)
    assert all(r is not None for r in static.values())
    assert static["hr"].engine == "calibrated"
    assert static["hr"].box == (
        int(0.10 * REF_W), int(0.10 * REF_H), int(0.14 * REF_W), int(0.12 * REF_H)
    )


def test_aspect_drift_guard_still_applies_to_the_untracked_path():
    profile = _profile()
    squashed = _textured_frame(w=REF_W, h=REF_H // 3)
    assert all(v is None for v in make_extractor(profile)(squashed).values())


def test_tracking_observer_is_called_on_success_and_on_failure():
    profile = _profile()
    ref = _textured_frame(seed=1)
    seen = []
    extractor = make_extractor(profile, tracker=_tracker(ref, profile), on_tracking_result=seen.append)
    extractor(ref.copy())
    extractor(_textured_frame(seed=99))
    assert len(seen) == 2
    assert seen[0].ok and not seen[1].ok


def test_a_raising_observer_cannot_break_the_pipeline():
    profile = _profile()
    ref = _textured_frame()

    def _boom(_result):
        raise RuntimeError("observer blew up")

    extractor = make_extractor(profile, tracker=_tracker(ref, profile), on_tracking_result=_boom)
    assert all(r is not None for r in extractor(ref.copy()).values())


# ─── reference-frame persistence ────────────────────────────────────────────


def _png_bytes(img: np.ndarray) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(img).save(buf, format="PNG")
    return buf.getvalue()


def test_reference_frame_roundtrips_through_the_database():
    db = SessionLocal()
    try:
        saved = repo.save_calibration_profile(db, {
            "reference_width": REF_W, "reference_height": REF_H,
            "roi_boxes": {"hr": {"x": 0.1, "y": 0.1, "w": 0.1, "h": 0.1}}, "field_meta": {},
        })
        assert saved.has_reference_frame is False

        payload = _png_bytes(_textured_frame())
        digest = repo.save_calibration_reference_frame(db, saved.id, payload, "image/png", REF_W, REF_H)

        row = repo.get_calibration_reference_frame(db, saved.id)
        assert row is not None and row.image_bytes == payload and row.sha256 == digest
        assert repo.has_calibration_reference_frame(db, saved.id) is True

        reloaded = repo.get_active_calibration_profile(db)
        assert reloaded.has_reference_frame is True
        assert reloaded.reference_frame_sha256 == digest
    finally:
        db.close()


def test_profile_without_reference_frame_reports_no_tracking():
    db = SessionLocal()
    try:
        saved = repo.save_calibration_profile(db, {
            "reference_width": REF_W, "reference_height": REF_H,
            "roi_boxes": {"hr": {"x": 0.1, "y": 0.1, "w": 0.1, "h": 0.1}}, "field_meta": {},
        })
        assert repo.has_calibration_reference_frame(db, saved.id) is False
        assert repo.get_calibration_reference_frame(db, saved.id) is None
    finally:
        db.close()


# ─── API lifecycle ──────────────────────────────────────────────────────────


def _save_profile_via_api() -> dict:
    body = {
        "referenceWidth": REF_W, "referenceHeight": REF_H,
        "roiBoxes": {"hr": {"x": 0.1, "y": 0.1, "w": 0.14, "h": 0.12}},
        "fieldMeta": {"hr": {"verified": True, "verifiedValue": "74", "verifiedConfidence": 95.0}},
    }
    r = client.post("/api/calibration", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def test_attach_and_fetch_reference_frame_via_api():
    _save_profile_via_api()
    payload = _png_bytes(_textured_frame())

    r = client.put("/api/calibration/active/reference-frame",
                   files={"file": ("ref.png", payload, "image/png")})
    assert r.status_code == 200, r.text
    assert r.json()["trackingEnabled"] is True

    assert client.get("/api/calibration/active").json()["hasReferenceFrame"] is True

    got = client.get("/api/calibration/active/reference-frame")
    assert got.status_code == 200 and got.content == payload


def test_reference_frame_of_wrong_dimensions_is_rejected():
    """Phase 3's 'do not accidentally use a different frame'. A frame whose
    size disagrees with the profile's own reference dimensions cannot be the
    frame those normalized boxes were drawn against."""
    _save_profile_via_api()
    wrong = _png_bytes(_textured_frame(w=REF_W // 2, h=REF_H // 2))
    r = client.put("/api/calibration/active/reference-frame",
                   files={"file": ("ref.png", wrong, "image/png")})
    assert r.status_code == 422
    assert "not the frame" in r.text


def test_reference_frame_endpoints_404_without_an_active_profile():
    assert client.put("/api/calibration/active/reference-frame",
                      files={"file": ("r.png", _png_bytes(_textured_frame()), "image/png")}
                      ).status_code == 404
    assert client.get("/api/calibration/active/reference-frame").status_code == 404


def test_reference_frame_missing_on_an_active_profile_404s():
    _save_profile_via_api()
    assert client.get("/api/calibration/active/reference-frame").status_code == 404


# ─── live camera wiring ─────────────────────────────────────────────────────


def test_camera_roi_extractor_builds_a_tracker_when_a_reference_frame_exists():
    from app.ws.vitals import TrackingState, _camera_roi_extractor

    _save_profile_via_api()
    client.put("/api/calibration/active/reference-frame",
               files={"file": ("ref.png", _png_bytes(_textured_frame()), "image/png")})

    state = TrackingState()
    extractor = _camera_roi_extractor(SessionLocal, state)
    assert extractor is not None
    assert state.enabled is True

    extractor(_textured_frame())
    assert state.latest is not None and state.latest.ok
    envelope = state.envelope()
    assert envelope["enabled"] is True and envelope["locked"] is True


def test_camera_roi_extractor_stays_untracked_without_a_reference_frame():
    from app.ws.vitals import TrackingState, _camera_roi_extractor

    _save_profile_via_api()
    state = TrackingState()
    extractor = _camera_roi_extractor(SessionLocal, state)
    assert extractor is not None
    assert state.enabled is False
    assert state.envelope() is None


def test_layout_tracking_env_var_disables_tracking(monkeypatch):
    """The documented rollback lever."""
    from app.ws.vitals import TrackingState, _camera_roi_extractor

    _save_profile_via_api()
    client.put("/api/calibration/active/reference-frame",
               files={"file": ("ref.png", _png_bytes(_textured_frame()), "image/png")})

    monkeypatch.setenv("LAYOUT_TRACKING", "off")
    state = TrackingState()
    assert _camera_roi_extractor(SessionLocal, state) is not None
    assert state.enabled is False


def test_camera_source_accepts_a_tracked_extractor():
    profile = _profile()
    ref = _textured_frame()
    source = CameraSource(channel="c", roi_extractor=make_extractor(profile, tracker=_tracker(ref, profile)))
    assert source.roi_extractor is not None
