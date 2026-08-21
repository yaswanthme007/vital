// M5.7.3: Calibration's Verify step distinguishes two different things that
// are easy to conflate:
//
//   A. OCR confidence/stability -- burst_verify.py's own verdict about
//      whether a value reproduced cleanly and confidently across several
//      independent frames (backend/app/pipeline/burst_verify.py).
//   B. Operator confirmation -- the human, physically looking at the
//      monitor, explicitly clicking "this is what the box shows".
//
// Pre-M5.7.3, the Verify step's Confirm button was gated on (A) alone --
// `reading[vital] === null` (never set unless burst verification judged the
// value fully stable, by burst_verify.py's own explicit "never invent a
// value" design) disabled Confirm outright, even when the pipeline had
// produced a plausible, visually-checkable candidate. burst.bestGuessValue
// was already computed and sent by the backend for exactly this purpose
// (see backend/app/pipeline/burst_verify.py's FieldBurstResult docstring)
// -- the frontend simply never consumed it to gate confirmation, only to
// build an explanatory message.
//
// During CALIBRATION -- not the live monitoring/reconciliation path, which
// this file has nothing to do with and does not change -- the operator's
// own eyes are the ground truth for the box they're looking at; OCR only
// needs to have produced *a* candidate for the operator to check it
// against the screen. A field only blocks confirmation when there is truly
// no candidate to look at at all.
//
// getFieldVerifyState is the single place that turns a burst-verification
// result into "what does the operator see, and can they confirm it" -- a
// pure function with no backend calls and no side effects, so it (and the
// small helpers below it) are covered directly by unit tests
// (fieldVerifyState.test.ts) rather than only indirectly through a mounted
// CalibrationPage.

import type { CalibrationFieldMeta, CalibrationVerifyResult, VitalKey } from '@/types/calibration';

export type FieldVerifyTone = 'stable' | 'recovered' | 'unstable' | 'suspicious' | 'none';

export interface FieldVerifyState {
  /** What to show/confirm -- the machine-trusted stable value, OR (only
   *  during calibration, never on the live path) an unstable-but-plausible
   *  or suspicious candidate the operator can still visually verify. Null
   *  only when OCR produced no candidate at all -- never invented. */
  displayValue: string | null;
  /** Whether the Confirm control should be enabled. False ONLY when there
   *  is truly nothing for the operator to look at and confirm. */
  confirmable: boolean;
  tone: FieldVerifyTone;
  /** Explanatory copy shown under the row; null when there's nothing extra
   *  to say (the plain "-" / diagnostics fallback covers it instead). */
  message: string | null;
}

/** Mirrors backend/app/api/calibration.py's _format_display_value: NIBP
 * renders as "sys/dia", every other vital as a plain number string. */
export function formatVerifyValue(vital: VitalKey, result: CalibrationVerifyResult | null): string | null {
  if (!result) return null;
  if (vital === 'nibp') {
    const { nibpSystolic, nibpDiastolic } = result.reading;
    return nibpSystolic != null && nibpDiastolic != null ? `${nibpSystolic}/${nibpDiastolic}` : null;
  }
  const raw = result.reading[vital as 'hr' | 'spo2' | 'etco2' | 'temp' | 'rr'];
  return raw != null ? String(raw) : null;
}

export function getFieldVerifyState(vital: VitalKey, result: CalibrationVerifyResult): FieldVerifyState {
  const stableValue = formatVerifyValue(vital, result);
  const burst = result.burst?.field[vital];

  if (stableValue !== null) {
    // burst_verify.py only ever populates reading[vital] once a value
    // cleared the full-confidence or corroborated-recovery tier -- this
    // branch is the pre-existing "OCR itself trusts this" case, unchanged.
    const agreeingCount = burst ? Math.round((burst.agreement / 100) * burst.sampleCount) : 0;
    const noisyCount = burst ? Math.max(0, burst.sampleCount - agreeingCount) : 0;
    const variantNote = burst?.usedVariant ? ' (second-opinion preprocessing helped)' : '';
    return {
      displayValue: stableValue,
      confirmable: true,
      tone: burst?.recovered ? 'recovered' : 'stable',
      message: !burst
        ? null
        : burst.recovered
          ? `Mostly stable — ${Math.round(burst.agreement)}% of ${burst.sampleCount} captures agreed on ${stableValue}. ` +
            `${noisyCount === 1 ? 'One noisy frame was' : `${noisyCount} noisy frames were`} discarded.${variantNote}`
          : `Stable — ${Math.round(burst.agreement)}% of ${burst.sampleCount} captures agreed.${variantNote}`,
    };
  }

  if (!burst) {
    // Legacy single-frame /verify shape carries no burst metadata to show a
    // candidate from -- nothing in the shipped UI calls that endpoint, but
    // keep its old honest "no candidate, can't confirm" behavior rather
    // than guessing at one.
    return { displayValue: null, confirmable: false, tone: 'none', message: null };
  }

  if (burst.bestGuessValue == null) {
    // No valid OCR sample at all across the whole burst -- nothing for the
    // operator to look at, so Confirm stays disabled. Never invent a value.
    return {
      displayValue: null,
      confirmable: false,
      tone: 'none',
      message: 'No readable value detected. Adjust lighting/box/camera and retry.',
    };
  }

  if (burst.unstableReason === 'geometry') {
    // crop_integrity flagged residual content on every agreeing sample --
    // burst_verify.py's signature of a clipped/truncated digit run. Still
    // show the exact candidate and still allow confirmation (the operator
    // may be looking straight at a monitor where it's obviously correct),
    // but warn hard and never auto-confirm it ourselves.
    return {
      displayValue: burst.bestGuessValue,
      confirmable: true,
      tone: 'suspicious',
      message:
        `Possible OCR/crop artifact detected — closest read was "${burst.bestGuessValue}", but the crop may be ` +
        'clipped or contain extra characters. Compare carefully against the monitor before confirming.',
    };
  }

  // 'low_agreement' or 'low_confidence': burst verification found a
  // plausible, clean-enough candidate that just didn't clear either
  // stability tier (not enough frames agreed, or confidence was too low).
  // That is a judgment about MACHINE trust, not about whether the value is
  // actually correct -- an operator looking at the live monitor can settle
  // that directly, so the candidate is shown and confirmable.
  return {
    displayValue: burst.bestGuessValue,
    confirmable: true,
    tone: 'unstable',
    message:
      `Detected with limited confidence — ${Math.round(burst.bestGuessAgreement)}% of ${burst.sampleCount} ` +
      `captures agreed on "${burst.bestGuessValue}". Visually verify against the monitor before confirming.`,
  };
}

/** Builds the CalibrationFieldMeta a Confirm click writes into calibration
 * state. `verified: true` means exactly, and only, "the operator visually
 * confirmed this candidate during calibration" (see
 * backend/app/models/calibration.py's CalibrationFieldMeta docstring) --
 * NEVER "OCR independently reached the production confidence threshold".
 * The confidence carried alongside is diagnostic context only (what OCR's
 * own mean confidence was), not a claim that the value is machine-trusted;
 * `verified` is the only thing any gate (Save, or a future consumer) may
 * treat as trust, and it is set here strictly from the operator's click,
 * never from `state.tone`. */
export function buildFieldMeta(
  vital: VitalKey,
  result: CalibrationVerifyResult,
  confirmed: boolean,
): CalibrationFieldMeta {
  const state = getFieldVerifyState(vital, result);
  const confidence = result.confidence[vital] ?? null;
  return { verified: confirmed, verifiedValue: state.displayValue, verifiedConfidence: confidence };
}

/** Save is disabled until every drawn field has been explicitly operator-
 * confirmed -- unchanged gating rule, just factored out so it's testable
 * without mounting CalibrationPage. */
export function allDrawnFieldsConfirmed(
  drawnVitals: VitalKey[],
  fieldMeta: Partial<Record<VitalKey, { verified: boolean }>>,
): boolean {
  return drawnVitals.length > 0 && drawnVitals.every((v) => fieldMeta[v]?.verified);
}
