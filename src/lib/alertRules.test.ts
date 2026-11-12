import { describe, it, expect } from 'vitest';
import { checkAlerts } from '@/lib/alertRules';

// Same cases as backend/tests/test_alert_rules_nibp_rr.py — the two rule
// sets must stay in lock-step.
const normal = {
  hr: 72, spo2: 98, etco2: 38, temp: 36.8,
  nibpSystolic: 120, nibpDiastolic: 80, nibpMean: 93, rr: 14,
};

const only = (reading: Partial<typeof normal>, vital: string) =>
  checkAlerts({ ...normal, ...reading }).filter((a) => a.vitalType === vital);

describe('checkAlerts — baseline', () => {
  it('raises nothing for a fully normal reading', () => {
    expect(checkAlerts(normal)).toEqual([]);
  });

  it('skips fields that are absent or null', () => {
    expect(checkAlerts({})).toEqual([]);
    expect(checkAlerts({ nibpMean: null, nibpSystolic: null, rr: null })).toEqual([]);
  });
});

describe('checkAlerts — NIBP', () => {
  it('MAP <= 55 is critical', () => {
    expect(only({ nibpMean: 55 }, 'nibp')).toEqual([
      { vitalType: 'nibp', severity: 'critical', message: 'MAP CRITICALLY LOW', value: 55, unit: 'mmHg' },
    ]);
  });

  it('MAP <= 65 is a hypotension warning', () => {
    expect(only({ nibpMean: 65 }, 'nibp')).toEqual([
      { vitalType: 'nibp', severity: 'warning', message: 'Hypotension (MAP)', value: 65, unit: 'mmHg' },
    ]);
  });

  it('falls back to systolic when MAP is not confirmed', () => {
    expect(only({ nibpMean: undefined, nibpSystolic: 70 }, 'nibp')[0].message).toBe('Systolic BP CRITICALLY LOW');
    expect(only({ nibpMean: undefined, nibpSystolic: 90 }, 'nibp')[0].message).toBe('Hypotension (Systolic)');
  });

  it('critical systolic outranks a MAP warning', () => {
    const [a] = only({ nibpMean: 60, nibpSystolic: 68 }, 'nibp');
    expect(a.severity).toBe('critical');
    expect(a.value).toBe(68);
  });

  it('flags hypertension on systolic', () => {
    expect(only({ nibpSystolic: 160 }, 'nibp')[0]).toMatchObject({ severity: 'warning', message: 'Hypertension' });
    expect(only({ nibpSystolic: 180 }, 'nibp')[0]).toMatchObject({ severity: 'critical', message: 'Systolic BP CRITICALLY HIGH' });
  });

  it('never raises more than one NIBP alert per check', () => {
    expect(only({ nibpMean: 40, nibpSystolic: 50 }, 'nibp')).toHaveLength(1);
  });
});

describe('checkAlerts — RR', () => {
  it.each([
    [30, 'critical', 'Resp Rate HIGH'],
    [24, 'warning', 'Resp Rate Elevated'],
    [8, 'warning', 'Resp Rate Low'],
    [5, 'critical', 'Resp Rate CRITICALLY LOW'],
    [0, 'critical', 'Resp Rate CRITICALLY LOW'],
  ])('rr=%s -> %s "%s"', (rr, severity, message) => {
    expect(only({ rr }, 'rr')).toEqual([{ vitalType: 'rr', severity, message, value: rr, unit: '/min' }]);
  });

  it('does not alert inside the normal band', () => {
    expect(only({ rr: 9 }, 'rr')).toEqual([]);
    expect(only({ rr: 23 }, 'rr')).toEqual([]);
  });
});
