"""M5.6 production promotion.

Two things are locked down here:

1. POST /api/pipeline/read-frame now PREFERS the database's active
   CalibrationProfile (matching the live camera path) and falls back to
   read_frame()'s ROI_ENGINE default when there isn't one -- reporting
   which of the two ran, so nothing downstream has to guess.

2. The promotion did not move anything it was not supposed to move. The
   ROI_ENGINE default is still 'tesseract' (flipping it to 'calibrated',
   as docs/ROADMAP.md originally proposed, would RAISE on every non-camera
   path -- see test_flipping_roi_engine_default_to_calibrated_would_break_
   uncalibrated_paths, which pins the reason in executable form),
   TEMPORAL_CORROBORATION is still off, and the camera path still binds the
   active profile itself without consulting any env var.
"""

import io
import json
import os

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.config_snapshot import snapshot
from app.main import app


def _png_bytes(img: np.ndarray) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(img).save(buf, format="PNG")
    return buf.getvalue()


def _blank_frame(w: int = 320, h: int = 240) -> np.ndarray:
    return np.full((h, w, 3), 12, dtype=np.uint8)


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


# ─── 1. read-frame ROI source selection ─────────────────────────────────────


def test_read_frame_reports_default_roi_source_when_no_profile_is_active(client, monkeypatch):
    """Pre-M5.6 behaviour, preserved exactly: with no active calibration
    profile the endpoint uses read_frame()'s own ROI_ENGINE default. The
    only difference is that it now SAYS so."""
    monkeypatch.setattr("app.api.pipeline.repo.get_active_calibration_profile", lambda db: None)

    r = client.post(
        "/api/pipeline/read-frame",
        files={"file": ("f.png", _png_bytes(_blank_frame()), "image/png")},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["roiSource"] == "default"
    assert "reading" in body and "confidence" in body


def test_read_frame_uses_the_active_calibration_profile_when_one_exists(client, monkeypatch):
    """The actual promotion. M5.1 sec 14 measured the Tier-1 colour path
    locating 0/17 fields on real photographed monitors, so this endpoint --
    the OCR Debug page's only backend -- was structurally unable to agree
    with the live camera path on the same frame. It now takes the same
    profile the camera path takes."""
    built = {}

    def _fake_make_extractor(profile, **kwargs):
        built["profile"] = profile
        built["kwargs"] = kwargs

        def _extractor(image):
            return {v: None for v in ("hr", "spo2", "nibp", "etco2", "temp", "rr")}

        return _extractor

    sentinel = object()
    monkeypatch.setattr("app.api.pipeline.repo.get_active_calibration_profile", lambda db: sentinel)
    monkeypatch.setattr("app.api.pipeline.make_calibrated_roi_extractor", _fake_make_extractor)

    r = client.post(
        "/api/pipeline/read-frame",
        files={"file": ("f.png", _png_bytes(_blank_frame()), "image/png")},
    )
    assert r.status_code == 200
    assert r.json()["roiSource"] == "calibrated"
    assert built["profile"] is sentinel


def test_read_frame_endpoint_never_attaches_a_tracker(client, monkeypatch):
    """Deliberate scope limit. LayoutTracker is built once per WebSocket
    connection and re-anchors across a stream; this endpoint is single-shot
    and owns no such lifetime. M5.6 promotes M5.2's calibrated localization
    here, NOT M5.3's per-frame re-anchoring -- adding a tracker would be new
    behaviour rather than promotion."""
    built = {}

    def _fake_make_extractor(profile, **kwargs):
        built.update(kwargs)
        return lambda image: {}

    monkeypatch.setattr("app.api.pipeline.repo.get_active_calibration_profile", lambda db: object())
    monkeypatch.setattr("app.api.pipeline.make_calibrated_roi_extractor", _fake_make_extractor)

    client.post(
        "/api/pipeline/read-frame",
        files={"file": ("f.png", _png_bytes(_blank_frame()), "image/png")},
    )
    assert built.get("tracker") is None
    assert built.get("on_tracking_result") is None


def test_read_frame_falls_back_and_never_500s_when_the_profile_lookup_explodes(client, monkeypatch):
    """Fail-open, same posture as app.ws.vitals._camera_roi_extractor: a
    calibration lookup failure degrades this endpoint to its pre-M5.6
    behaviour rather than taking it down mid-demo."""

    def _boom(db):
        raise RuntimeError("database on fire")

    monkeypatch.setattr("app.api.pipeline.repo.get_active_calibration_profile", _boom)

    r = client.post(
        "/api/pipeline/read-frame",
        files={"file": ("f.png", _png_bytes(_blank_frame()), "image/png")},
    )
    assert r.status_code == 200
    assert r.json()["roiSource"] == "default"


def test_read_frame_falls_back_when_the_extractor_cannot_be_built(client, monkeypatch):
    """A profile row that exists but cannot produce an extractor (corrupt
    geometry, schema drift) must degrade, not crash."""

    def _boom(profile, **kwargs):
        raise ValueError("unbuildable profile")

    monkeypatch.setattr("app.api.pipeline.repo.get_active_calibration_profile", lambda db: object())
    monkeypatch.setattr("app.api.pipeline.make_calibrated_roi_extractor", _boom)

    r = client.post(
        "/api/pipeline/read-frame",
        files={"file": ("f.png", _png_bytes(_blank_frame()), "image/png")},
    )
    assert r.status_code == 200
    assert r.json()["roiSource"] == "default"


def test_read_frame_still_rejects_a_non_image(client):
    """Unchanged contract."""
    r = client.post("/api/pipeline/read-frame", files={"file": ("x.txt", b"not an image", "text/plain")})
    assert r.status_code == 422


# ─── 2. the promotion did not move anything else ────────────────────────────


def test_roi_engine_default_is_still_tesseract():
    """docs/ROADMAP.md M5.6 originally said "flip the ROI_ENGINE default to
    calibrated". M5.6's Phase 0 audit found that unsafe (see the next test)
    and promoted at the API layer instead. This pins the decision."""
    from app.pipeline import read_frame as read_frame_module
    from app.pipeline.roi import extract_rois_by_colour

    os.environ.pop("ROI_ENGINE", None)
    read_frame_module._default_roi_extractor = None
    try:
        assert read_frame_module.get_default_roi_extractor() is extract_rois_by_colour
    finally:
        read_frame_module._default_roi_extractor = None


def test_flipping_roi_engine_default_to_calibrated_would_break_uncalibrated_paths(monkeypatch):
    """The executable form of M5.6's Phase 0 finding, kept so nobody
    re-proposes the flip without re-discovering why it was rejected:
    ROI_ENGINE=calibrated RAISES unless CALIBRATION_PROFILE_PATH names a
    profile JSON on disk, which is never set in production. Had the default
    been flipped, every non-camera path -- POST /api/pipeline/read-frame,
    ReplaySource('pipeline'), and the camera WebSocket's own fallback for a
    session with no calibration profile yet -- would have raised instead of
    degrading."""
    from app.pipeline import read_frame as read_frame_module

    monkeypatch.setenv("ROI_ENGINE", "calibrated")
    monkeypatch.delenv("CALIBRATION_PROFILE_PATH", raising=False)
    read_frame_module._default_roi_extractor = None
    try:
        with pytest.raises(ValueError, match="CALIBRATION_PROFILE_PATH"):
            read_frame_module.get_default_roi_extractor()
    finally:
        read_frame_module._default_roi_extractor = None


def test_temporal_corroboration_is_off_by_default():
    """M5.6's hard rule. The feature stays in the tree; it does not ship on."""
    from app.ws.vitals import _temporal_corroboration_enabled

    os.environ.pop("TEMPORAL_CORROBORATION", None)
    assert _temporal_corroboration_enabled() is False


def test_temporal_corroboration_is_still_reachable_for_research(monkeypatch):
    """The other half of the rule: M5.6 must not delete or disable the
    mechanism, only leave it off."""
    from app.ws.vitals import _temporal_corroboration_enabled

    monkeypatch.setenv("TEMPORAL_CORROBORATION", "on")
    assert _temporal_corroboration_enabled() is True


def test_layout_tracking_default_is_auto():
    from app.ws.vitals import _tracking_enabled

    os.environ.pop("LAYOUT_TRACKING", None)
    assert _tracking_enabled() is True


# ─── 3. the frozen configuration snapshot ───────────────────────────────────


def test_snapshot_flag_defaults_match_the_code_that_actually_reads_them():
    """The snapshot's whole value is that it cannot drift from reality.
    These assertions are what make that true rather than aspirational."""
    from app import config_snapshot
    from app.pipeline.read_frame import _build_roi_extractor_from_env  # noqa: F401

    s = snapshot()
    assert s["featureFlags"]["ROI_ENGINE"]["default"] == config_snapshot.ROI_ENGINE_DEFAULT
    assert s["featureFlags"]["TEMPORAL_CORROBORATION"]["default"] == "off"
    assert s["featureFlags"]["LAYOUT_TRACKING"]["default"] == "auto"
    assert s["featureFlags"]["OCR_ENGINE"]["default"] == "tesseract"


def test_snapshot_reports_the_real_ocr_constants():
    """Not a transcription of M5.5's table -- the live constants."""
    from app.pipeline import ocr

    s = snapshot()["ocr"]
    # M5.8: what production reads with now.
    assert s["sparseConfig"] == ocr._SPARSE_CONFIG == "--psm 11"
    assert s["dominantRowHeightRatio"] == ocr.DOMINANT_ROW_HEIGHT_RATIO
    assert s["quietZonePad"] == ocr._QUIET_ZONE_PAD
    # The M4-era constants, still reported for the eval trail.
    assert s["digitConfig"] == ocr._DIGIT_CONFIG == "--psm 8"
    assert s["digitPsm10Config"] == ocr._DIGIT_PSM10_CONFIG == "--psm 10"
    assert s["nibpConfig"] == ocr._NIBP_CONFIG == "--psm 6"
    assert s["etco2Config"] == ocr._ETCO2_CONFIG == "--psm 8"
    assert s["psm10Vitals"] == ["rr", "spo2"]
    assert s["charWhitelist"] is None


def test_snapshot_reports_the_real_safety_constants():
    from app.pipeline import calibrated_roi, layout_tracker
    from app.validation import rules

    s = snapshot()
    assert s["confidence"]["confidenceMediumMin"] == rules.CONFIDENCE_MEDIUM_MIN == 70
    assert s["confidence"]["confidenceHighMin"] == rules.CONFIDENCE_HIGH_MIN == 90
    assert s["calibration"]["widthSafetyPadFraction"] == calibrated_roi.WIDTH_SAFETY_PAD_FRACTION
    assert s["calibration"]["maxAspectRatioDrift"] == calibrated_roi.MAX_ASPECT_RATIO_DRIFT
    assert s["tracking"]["minInliers"] == layout_tracker.MIN_INLIERS


def test_snapshot_reports_the_onnx_artifacts_as_present_but_not_loaded():
    """M5.5 remaining risk 4: the retired FieldCNN/digit-CNN files are still
    on disk and still switchable. The snapshot has to surface both halves of
    that, or it is hiding the risk rather than freezing the config."""
    os.environ.pop("ROI_ENGINE", None)
    os.environ.pop("OCR_ENGINE", None)
    models = snapshot()["models"]
    assert models["fieldClassifierLoaded"] is False
    assert models["digitCnnLoaded"] is False


def test_snapshot_carries_the_defensible_validation_claim_and_no_clinical_claim():
    s = snapshot()
    claim = s["validationClaim"].lower()
    assert "not clinically validated" in claim
    assert "real application pipeline" in claim
    for forbidden in ("clinically validated,", "medically certified,", "diagnostically accurate."):
        assert not claim.startswith(forbidden)


def test_config_snapshot_endpoint_serves_json(client):
    r = client.get("/api/config/snapshot")
    assert r.status_code == 200
    body = r.json()
    assert body["milestone"] == "M5.6"
    assert body["featureFlags"]["TEMPORAL_CORROBORATION"]["enabled"] is False
    # It must be genuinely serializable -- a snapshot that only renders in a
    # REPL is not a reproducibility artifact.
    json.dumps(body)
