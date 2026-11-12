import type { VitalField, VitalObservationRow } from '@/types/vitals';
import { VITAL_FIELDS } from '@/lib/ledger';

// Raw observed-timeline export: one CSV line per persisted reading row from
// GET /api/sessions/{id}/readings — the same rows Review/Archive summarise.
// A field that was not confirmed on a tick stays an EMPTY cell, never a
// zero or a carried-forward value (see ledger.ts for why null means
// "not confirmed this tick").

const FIELD_HEADERS: Record<VitalField, string> = {
  hr: 'hr_bpm',
  spo2: 'spo2_pct',
  nibpSystolic: 'nibp_sys_mmHg',
  nibpDiastolic: 'nibp_dia_mmHg',
  nibpMean: 'nibp_map_mmHg',
  etco2: 'etco2_mmHg',
  temp: 'temp_c',
  rr: 'rr_per_min',
};

export const CSV_HEADER = ['timestamp_iso', 'epoch_ms', ...VITAL_FIELDS.map((f) => FIELD_HEADERS[f]), 'confidence', 'source'];

/** RFC 4180 quoting, plus a guard against spreadsheet formula injection. */
export function csvCell(value: string | number | null | undefined): string {
  if (value == null) return '';
  let s = String(value);
  if (typeof value === 'string' && /^[=+\-@\t\r]/.test(s)) s = `'${s}`;
  return /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

export function observationsToCsv(rows: VitalObservationRow[]): string {
  const lines = [CSV_HEADER.join(',')];
  const sorted = [...rows].sort((a, b) => a.timestamp - b.timestamp);
  for (const row of sorted) {
    lines.push([
      csvCell(new Date(row.timestamp).toISOString()),
      csvCell(row.timestamp),
      ...VITAL_FIELDS.map((f) => csvCell(row[f])),
      csvCell(row.confidence == null ? null : Math.round(row.confidence * 10) / 10),
      csvCell(row.source),
    ].join(','));
  }
  return lines.join('\r\n') + '\r\n';
}

/** e.g. "vital_PT-0042_2026-11-12_1405.csv" — filesystem-safe on every OS. */
export function csvFilename(patientId: string, startTime: number): string {
  const d = new Date(startTime);
  const pad = (n: number) => String(n).padStart(2, '0');
  const stamp = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}_${pad(d.getHours())}${pad(d.getMinutes())}`;
  const safeId = patientId.replace(/[^A-Za-z0-9_-]+/g, '_').replace(/^_+|_+$/g, '') || 'session';
  return `vital_${safeId}_${stamp}.csv`;
}

/** Triggers a browser download. The BOM makes Excel read "°C"/"SpO₂" as UTF-8. */
export function downloadCsv(filename: string, csv: string): void {
  const blob = new Blob(['﻿', csv], { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}
