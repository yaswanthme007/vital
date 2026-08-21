// Mirrors backend/app/models/calibration.py exactly (CamelModel serializes
// camelCase) -- the frontend's own copy of that wire shape, same pattern
// session.ts already follows for Session/SessionFormData.

export type VitalKey = 'hr' | 'spo2' | 'nibp' | 'etco2' | 'temp' | 'rr';

export const VITAL_KEYS: VitalKey[] = ['hr', 'spo2', 'nibp', 'etco2', 'temp', 'rr'];

// Normalized [0,1] box, relative to the frame it was drawn against -- see
// backend/app/models/calibration.py's NormalizedBox docstring for why.
export interface NormalizedBox {
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface CalibrationFieldMeta {
  verified: boolean;
  verifiedValue?: string | null;
  verifiedConfidence?: number | null;
}

export interface CalibrationProfile {
  id: string;
  theatreId?: string | null;
  cameraId?: string | null;
  layoutId: string;
  version: number;
  referenceWidth: number;
  referenceHeight: number;
  roiBoxes: Partial<Record<VitalKey, NormalizedBox>>;
  fieldMeta: Partial<Record<VitalKey, CalibrationFieldMeta>>;
  createdAt: number;
  updatedAt: number;
  isActive: boolean;
  // M5.3. True once the profile carries the reference frame its boxes were
  // drawn against, which is what enables per-frame layout tracking. False on
  // every profile saved before M5.3 -- those keep working, untracked.
  hasReferenceFrame: boolean;
  referenceFrameSha256?: string | null;
}

// M5.3: per-frame tracking state, added to the WS 'reading' envelope by
// backend/app/ws/vitals.py. Absent entirely when tracking is not running, so
// the UI can distinguish "not tracking" from "tracking but unlocked" instead
// of showing a misleading unlocked badge for a pipeline that never tracks.
export interface TrackingState {
  enabled: boolean;
  locked: boolean;
  status: string;
  inliers?: number;
  scale?: number;
  rotationDeg?: number;
  reasons?: string[];
}

export interface CalibrationVerifyResult {
  reading: {
    hr: number | null;
    spo2: number | null;
    nibpSystolic: number | null;
    nibpDiastolic: number | null;
    nibpMean: number | null;
    etco2: number | null;
    temp: number | null;
    rr: number | null;
  };
  confidence: Partial<Record<VitalKey, number>>;
  frameWidth: number;
  frameHeight: number;
  // M5.7.1: raw OCR text per field, so a field that read as `null` can be
  // explained to the operator (crop found nothing vs. found text that didn't
  // parse) instead of just showing "-". Absent (or a field mapping to null)
  // means the ROI resolved to no crop at all.
  diagnostics?: Partial<Record<VitalKey, { rawText: string; matchedText: string | null } | null>>;
  // M5.7.2, additive: present only on POST /api/calibration/verify-burst's
  // response (see backend/app/pipeline/burst_verify.py) -- absent entirely
  // on the single-frame /verify response above, so any code that doesn't
  // know about bursts keeps working against the exact same
  // reading/confidence/diagnostics shape it always has.
  burst?: {
    frameCount: number;
    field: Partial<Record<VitalKey, CalibrationBurstFieldMeta>>;
  };
}

// M5.7.2: per-field burst-verification metadata. `stable` is what actually
// gates whether `reading[vital]` is non-null (see burst_verify.py's
// FieldBurstResult -- an unstable field's `value` is ALWAYS null, never an
// invented/forced guess); bestGuess* is surfaced purely so the UI can
// explain what the closest attempt looked like when it wasn't good enough.
export interface CalibrationBurstFieldMeta {
  agreement: number; // 0-100, % of valid samples that agreed on the reported/best-guess value
  sampleCount: number;
  frameCount: number;
  stable: boolean;
  // M5.7.2 hotfix: true when `stable` was reached via the corroborated-
  // recovery tier (a strong majority of captures agreed, at a lower but
  // still evidence-backed confidence floor) rather than every agreeing
  // sample independently clearing the full confidence bar -- lets the UI
  // say "one noisy frame was discarded" honestly instead of implying every
  // capture was equally clean and confident.
  recovered: boolean;
  // Only meaningful when stable is false -- why the field didn't verify,
  // so the UI can tell "hold the camera steady and try again" apart from
  // "the box geometry itself looks wrong, redraw it" instead of always
  // suggesting a redraw. See backend/app/pipeline/burst_verify.py's
  // FieldBurstResult.unstable_reason docstring for exact semantics.
  unstableReason: 'no_reading' | 'low_agreement' | 'geometry' | 'low_confidence' | null;
  usedVariant: boolean; // true if the adaptive-preprocessing second opinion was consulted
  bestGuessValue: string | null; // already display-formatted (e.g. "74" or "120/78"), even when NOT stable
  bestGuessAgreement: number;
}
