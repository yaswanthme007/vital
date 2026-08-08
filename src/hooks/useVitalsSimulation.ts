import { useEffect, useRef } from 'react';
import { useVitalsStore } from '@/store/vitalsStore';
import { useAlertStore } from '@/store/alertStore';
import { useSessionStore } from '@/store/sessionStore';
import { randomWalk } from '@/lib/utils';
import type { VitalReading } from '@/types/vitals';
import type { AlertSeverity } from '@/store/alertStore';
import type { VitalType } from '@/types/vitals';

function buildReading(prev: VitalReading | null, elapsed: number): VitalReading {
  const baseHR  = 72 + 6 * Math.sin((elapsed / 900) * Math.PI);
  const baseSpo2 = 98.5;
  const baseEtco2 = 38;

  const hr  = prev ? randomWalk(prev.hr,  1.5, 45, 135, baseHR,   0.08) : baseHR;
  const spo2 = prev ? randomWalk(prev.spo2, 0.4, 88, 100, baseSpo2, 0.12) : baseSpo2;
  const etco2 = prev ? randomWalk(prev.etco2, 0.8, 18, 65, baseEtco2, 0.08) : baseEtco2;

  const systolic  = 118 + 6 * Math.sin((elapsed / 600) * Math.PI) + (Math.random() - 0.5) * 6;
  const diastolic = 74  + 3 * Math.sin((elapsed / 800) * Math.PI) + (Math.random() - 0.5) * 4;
  const nibpMean  = Math.round(diastolic + (systolic - diastolic) / 3);

  const temp = prev ? randomWalk(prev.temp, 0.05, 34, 40, 36.8, 0.02) : 36.8;
  const rr   = prev ? randomWalk(prev.rr,   0.5,  4,  35, 14,   0.06) : 14;

  return {
    hr:           Math.round(hr),
    spo2:         Math.round(spo2),
    nibpSystolic: Math.round(systolic),
    nibpDiastolic: Math.round(diastolic),
    nibpMean,
    etco2:        Math.round(etco2 * 10) / 10,
    temp:         Math.round(temp * 10) / 10,
    rr:           Math.round(rr),
    timestamp:    Date.now(),
  };
}

type AddAlertFn = (a: {
  vitalType: VitalType | 'system';
  severity: AlertSeverity;
  message: string;
  value?: number;
  unit?: string;
}) => void;

function checkAlerts(r: VitalReading, throttle: AddAlertFn) {
  if (r.spo2 <= 90)      throttle({ vitalType: 'spo2', severity: 'critical', message: 'SpO₂ CRITICALLY LOW', value: r.spo2, unit: '%' });
  else if (r.spo2 <= 94) throttle({ vitalType: 'spo2', severity: 'warning',  message: 'SpO₂ Low',           value: r.spo2, unit: '%' });

  if (r.hr >= 130)       throttle({ vitalType: 'hr', severity: 'critical', message: 'Heart Rate HIGH',           value: r.hr, unit: 'bpm' });
  else if (r.hr >= 110)  throttle({ vitalType: 'hr', severity: 'warning',  message: 'Heart Rate Elevated',       value: r.hr, unit: 'bpm' });
  else if (r.hr <= 40)   throttle({ vitalType: 'hr', severity: 'critical', message: 'Heart Rate CRITICALLY LOW', value: r.hr, unit: 'bpm' });
  else if (r.hr <= 50)   throttle({ vitalType: 'hr', severity: 'warning',  message: 'Heart Rate Low',            value: r.hr, unit: 'bpm' });

  if (r.etco2 >= 55)     throttle({ vitalType: 'etco2', severity: 'critical', message: 'EtCO₂ CRITICALLY HIGH', value: r.etco2, unit: 'mmHg' });
  else if (r.etco2 >= 50) throttle({ vitalType: 'etco2', severity: 'warning', message: 'EtCO₂ Elevated',       value: r.etco2, unit: 'mmHg' });

  if (r.temp >= 39.5)    throttle({ vitalType: 'temp', severity: 'critical', message: 'Hyperthermia',     value: r.temp, unit: '°C' });
  else if (r.temp >= 38.5) throttle({ vitalType: 'temp', severity: 'warning', message: 'Fever',            value: r.temp, unit: '°C' });
  else if (r.temp <= 35.5) throttle({ vitalType: 'temp', severity: 'warning', message: 'Hypothermia Risk', value: r.temp, unit: '°C' });
}

export function useVitalsSimulation() {
  const { activeSession } = useSessionStore();
  const { current, updateVitals } = useVitalsStore();
  const { addAlert } = useAlertStore();
  const lastAlertAt = useRef<Record<string, number>>({});
  const currentRef  = useRef(current);

  useEffect(() => { currentRef.current = current; }, [current]);

  useEffect(() => {
    if (!activeSession || activeSession.status !== 'active') return;

    const startTime = activeSession.startTime;

    const id = setInterval(() => {
      const elapsed = (Date.now() - startTime) / 1000;
      const reading = buildReading(currentRef.current, elapsed);
      updateVitals(reading);

      const now = Date.now();
      const throttledAdd: AddAlertFn = (data) => {
        const key = `${data.vitalType}-${data.severity}`;
        if (now - (lastAlertAt.current[key] ?? 0) > 30_000) {
          lastAlertAt.current[key] = now;
          addAlert(data);
        }
      };
      checkAlerts(reading, throttledAdd);
    }, 1000);

    return () => clearInterval(id);
  }, [activeSession?.id, activeSession?.status]);
}
