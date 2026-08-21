import { describe, expect, it } from 'vitest';
import { deriveLedger } from '@/lib/ledger';
import { getAlarmSeverity, DEFAULT_ALARM_LIMITS } from '@/types/vitals';
import type { VitalObservationRow } from '@/types/vitals';

function row(overrides: Partial<VitalObservationRow> & { timestamp: number }): VitalObservationRow {
  return {
    hr: null, spo2: null, nibpSystolic: null, nibpDiastolic: null, nibpMean: null,
    etco2: null, temp: null, rr: null, confidence: null, provenance: null,
    perVitalConfidence: null, source: 'camera', fieldStatus: null, ...overrides,
  };
}

describe('deriveLedger', () => {
  it('records one entry per genuine value change, not one per row', () => {
    const entries = deriveLedger([
      row({ timestamp: 1, hr: 89 }),
      row({ timestamp: 2, hr: 89 }),
      row({ timestamp: 3, hr: 90 }),
    ]);
    expect(entries.map((e) => [e.field, e.value, e.timestamp])).toEqual([
      ['hr', 89, 1],
      ['hr', 90, 3],
    ]);
  });

  it('never creates an entry for a field that was not observed', () => {
    // M5.8: a field the camera has not confirmed is null on the row (and
    // renders as an em dash on the card). It must contribute nothing to the
    // patient's observed history -- the ledger is evidence, not a display.
    expect(deriveLedger([row({ timestamp: 1 }), row({ timestamp: 2 })])).toEqual([]);
  });

  it('does not re-record a value that was merely re-confirmed after a gap', () => {
    const entries = deriveLedger([
      row({ timestamp: 1, spo2: 98 }),
      row({ timestamp: 2 }), // spo2 unreadable this tick -- absent, not zero
      row({ timestamp: 3, spo2: 98 }),
    ]);
    expect(entries).toHaveLength(1);
  });
});

describe('getAlarmSeverity', () => {
  it('reports normal for a value that has never been observed', () => {
    // M5.8: VitalReading's fields became nullable so a camera session can
    // honestly say "not observed yet". A card with nothing on it must never
    // be styled as a critical alarm -- null is absence of evidence, and
    // every numeric comparison against it would otherwise read as 0.
    expect(getAlarmSeverity('hr', null, DEFAULT_ALARM_LIMITS)).toBe('normal');
    expect(getAlarmSeverity('spo2', undefined, DEFAULT_ALARM_LIMITS)).toBe('normal');
    expect(getAlarmSeverity('hr', 0, DEFAULT_ALARM_LIMITS)).toBe('critical');
  });
});
