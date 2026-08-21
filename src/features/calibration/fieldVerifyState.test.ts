// Regression tests for the Calibration Verify-step Confirm-gating fix
// (M5.7.3). See fieldVerifyState.ts's module docstring for the underlying
// design: OCR confidence/stability (what burst_verify.py measured) must
// never be conflated with operator confirmation (an explicit click) -- a
// low-confidence or unstable OCR candidate should still be shown and
// confirmable, but must not become "verified" without that click.

import { describe, expect, it } from 'vitest';
import { allDrawnFieldsConfirmed, buildFieldMeta, getFieldVerifyState } from './fieldVerifyState';
import type { CalibrationBurstFieldMeta, CalibrationVerifyResult, VitalKey } from '@/types/calibration';

// Minimal, fully-populated CalibrationVerifyResult fixture -- only the
// fields getFieldVerifyState actually reads are varied per test.
function makeResult(overrides: {
  hr?: number | null;
  confidence?: number;
  burstField?: CalibrationBurstFieldMeta;
}): CalibrationVerifyResult {
  return {
    reading: {
      hr: overrides.hr ?? null,
      spo2: null, nibpSystolic: null, nibpDiastolic: null, nibpMean: null,
      etco2: null, temp: null, rr: null,
    },
    confidence: { hr: overrides.confidence ?? 0 },
    frameWidth: 1280,
    frameHeight: 720,
    diagnostics: { hr: { rawText: '90', matchedText: '90' } },
    burst: overrides.burstField
      ? { frameCount: 5, field: { hr: overrides.burstField } }
      : undefined,
  };
}

describe('getFieldVerifyState', () => {
  it('1. stable high-confidence candidate -> confirmable, tone stable', () => {
    const result = makeResult({
      hr: 90,
      confidence: 92,
      burstField: {
        agreement: 100, sampleCount: 5, frameCount: 5, stable: true, recovered: false,
        unstableReason: null, usedVariant: false, bestGuessValue: '90', bestGuessAgreement: 100,
      },
    });
    const state = getFieldVerifyState('hr', result);
    expect(state.displayValue).toBe('90');
    expect(state.confirmable).toBe(true);
    expect(state.tone).toBe('stable');
  });

  it('2. low-confidence but valid candidate -> confirmable, tone unstable', () => {
    const result = makeResult({
      hr: null,
      confidence: 50,
      burstField: {
        agreement: 60, sampleCount: 5, frameCount: 5, stable: false, recovered: false,
        unstableReason: 'low_confidence', usedVariant: false, bestGuessValue: '90', bestGuessAgreement: 60,
      },
    });
    const state = getFieldVerifyState('hr', result);
    expect(state.displayValue).toBe('90');
    expect(state.confirmable).toBe(true);
    expect(state.tone).toBe('unstable');
    expect(state.message).toMatch(/limited confidence/i);
  });

  it('3. unstable but valid candidate (low agreement) -> confirmable, tone unstable', () => {
    const result = makeResult({
      hr: null,
      confidence: 75,
      burstField: {
        agreement: 40, sampleCount: 5, frameCount: 5, stable: false, recovered: false,
        unstableReason: 'low_agreement', usedVariant: false, bestGuessValue: '90', bestGuessAgreement: 40,
      },
    });
    const state = getFieldVerifyState('hr', result);
    expect(state.displayValue).toBe('90');
    expect(state.confirmable).toBe(true);
    expect(state.tone).toBe('unstable');
  });

  it('4. no candidate at all -> confirm disabled', () => {
    const result = makeResult({
      hr: null,
      confidence: 0,
      burstField: {
        agreement: 0, sampleCount: 0, frameCount: 5, stable: false, recovered: false,
        unstableReason: 'no_reading', usedVariant: false, bestGuessValue: null, bestGuessAgreement: 0,
      },
    });
    const state = getFieldVerifyState('hr', result);
    expect(state.displayValue).toBeNull();
    expect(state.confirmable).toBe(false);
    expect(state.tone).toBe('none');
  });

  it('5. suspicious/residual candidate -> shown with warning, still confirmable', () => {
    const result = makeResult({
      hr: null,
      confidence: 88,
      burstField: {
        agreement: 100, sampleCount: 5, frameCount: 5, stable: false, recovered: false,
        unstableReason: 'geometry', usedVariant: false, bestGuessValue: '8', bestGuessAgreement: 100,
      },
    });
    const state = getFieldVerifyState('hr', result);
    expect(state.displayValue).toBe('8');
    expect(state.confirmable).toBe(true);
    expect(state.tone).toBe('suspicious');
    expect(state.message).toMatch(/artifact/i);
  });

  it('recovered stable candidate still reports as confirmable/green (pre-existing behavior unaffected)', () => {
    const result = makeResult({
      hr: 90,
      confidence: 58,
      burstField: {
        agreement: 80, sampleCount: 5, frameCount: 5, stable: true, recovered: true,
        unstableReason: null, usedVariant: false, bestGuessValue: '90', bestGuessAgreement: 80,
      },
    });
    const state = getFieldVerifyState('hr', result);
    expect(state.confirmable).toBe(true);
    expect(state.tone).toBe('recovered');
  });
});

describe('buildFieldMeta', () => {
  it('6. clicking Confirm marks the field operator-verified, carrying the shown candidate value', () => {
    const result = makeResult({
      hr: null,
      confidence: 50,
      burstField: {
        agreement: 60, sampleCount: 5, frameCount: 5, stable: false, recovered: false,
        unstableReason: 'low_confidence', usedVariant: false, bestGuessValue: '90', bestGuessAgreement: 60,
      },
    });
    const meta = buildFieldMeta('hr', result, true);
    expect(meta.verified).toBe(true);
    expect(meta.verifiedValue).toBe('90');
    expect(meta.verifiedConfidence).toBe(50);
  });

  it('un-confirming does not fabricate verified:true', () => {
    const result = makeResult({ hr: 90, confidence: 92, burstField: {
      agreement: 100, sampleCount: 5, frameCount: 5, stable: true, recovered: false,
      unstableReason: null, usedVariant: false, bestGuessValue: '90', bestGuessAgreement: 100,
    } });
    const meta = buildFieldMeta('hr', result, false);
    expect(meta.verified).toBe(false);
  });
});

describe('allDrawnFieldsConfirmed', () => {
  it('7. Save stays blocked until every drawn field is explicitly confirmed', () => {
    const drawn: VitalKey[] = ['hr', 'spo2'];
    expect(allDrawnFieldsConfirmed(drawn, {})).toBe(false);
    expect(allDrawnFieldsConfirmed(drawn, { hr: { verified: true } })).toBe(false);
    expect(allDrawnFieldsConfirmed(drawn, { hr: { verified: true }, spo2: { verified: false } })).toBe(false);
    expect(allDrawnFieldsConfirmed(drawn, { hr: { verified: true }, spo2: { verified: true } })).toBe(true);
  });

  it('no drawn fields never counts as confirmed (nothing to confirm != confirmed)', () => {
    expect(allDrawnFieldsConfirmed([], {})).toBe(false);
  });
});
