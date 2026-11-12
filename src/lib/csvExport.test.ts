// VITAL · src/lib/csvExport.test.ts
import { describe, it, expect } from 'vitest';
import { csvCell, observationsToCsv, csvFilename, CSV_HEADER } from '@/lib/csvExport';
import type { VitalObservationRow } from '@/types/vitals';

function row(timestamp: number, over: Partial<VitalObservationRow> = {}): VitalObservationRow {
  return {
    hr: null, spo2: null, nibpSystolic: null, nibpDiastolic: null, nibpMean: null,
    etco2: null, temp: null, rr: null, timestamp,
    confidence: null, provenance: null, perVitalConfidence: null, source: 'camera', fieldStatus: null,
    ...over,
  };
}

describe('csvCell', () => {
  it('leaves plain values alone and blanks null', () => {
    expect(csvCell(72)).toBe('72');
    expect(csvCell(null)).toBe('');
    expect(csvCell(undefined)).toBe('');
  });

  it('quotes commas, quotes and newlines', () => {
    expect(csvCell('a,b')).toBe('"a,b"');
    expect(csvCell('say "hi"')).toBe('"say ""hi"""');
    expect(csvCell('x\ny')).toBe('"x\ny"');
  });

  it('defuses spreadsheet formulas in strings but not negative numbers', () => {
    expect(csvCell('=SUM(A1)')).toBe("'=SUM(A1)");
    expect(csvCell(-3)).toBe('-3');
  });
});

describe('observationsToCsv', () => {
  it('writes a header, sorts by time and keeps unconfirmed fields empty', () => {
    const t0 = Date.UTC(2026, 10, 12, 9, 0, 0);
    const csv = observationsToCsv([
      row(t0 + 1000, { hr: 80, temp: 36.6, confidence: 91.26 }),
      row(t0, { hr: 78, spo2: 99 }),
    ]);
    const lines = csv.trimEnd().split('\r\n');
    expect(lines[0]).toBe(CSV_HEADER.join(','));
    expect(lines).toHaveLength(3);
    expect(lines[1]).toBe(`2026-11-12T09:00:00.000Z,${t0},78,99,,,,,,,,camera`);
    expect(lines[2]).toBe(`2026-11-12T09:00:01.000Z,${t0 + 1000},80,,,,,,36.6,,91.3,camera`);
  });

  it('returns just the header for an empty session', () => {
    expect(observationsToCsv([])).toBe(CSV_HEADER.join(',') + '\r\n');
  });
});

describe('csvFilename', () => {
  it('sanitises the patient id', () => {
    const name = csvFilename('PT 00/42', new Date(2026, 10, 12, 14, 5).getTime());
    expect(name).toBe('vital_PT_00_42_2026-11-12_1405.csv');
  });

  it('falls back when the id has no safe characters', () => {
    expect(csvFilename('///', new Date(2026, 0, 2, 3, 4).getTime())).toBe('vital_session_2026-01-02_0304.csv');
  });
});
