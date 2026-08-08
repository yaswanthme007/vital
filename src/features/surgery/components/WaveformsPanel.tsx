import { useMemo } from 'react';
import { WaveformChart } from '@/components/vitals/WaveformChart';
import { ecgPoint, plethPoint, capnoPoint } from '@/lib/waveformGenerators';
import { useVitalsStore } from '@/store/vitalsStore';
import { CameraOverlay } from './CameraOverlay';

// ─── Waveform row wrapper ─────────────────────────────────────────────────────

interface WaveRowProps {
  children: React.ReactNode;
  last?: boolean;
}

function WaveRow({ children, last }: WaveRowProps) {
  return (
    <div
      className="flex-1 relative min-h-0"
      style={{
        borderBottom: last ? 'none' : '1px solid #E2E8F0',
      }}
    >
      {children}
    </div>
  );
}

// ─── Inline scale labels ──────────────────────────────────────────────────────

interface ScaleProps {
  color: string;
  topLabel: string;
  midLabel?: string;
  bottomLabel?: string;
}

function ScaleTicks({ color, topLabel, midLabel, bottomLabel }: ScaleProps) {
  return (
    <div className="absolute right-2 inset-y-0 flex flex-col justify-between pointer-events-none z-10 py-2">
      <span className="font-mono text-[8px] opacity-25" style={{ color }}>{topLabel}</span>
      {midLabel && <span className="font-mono text-[8px] opacity-15" style={{ color }}>{midLabel}</span>}
      <span className="font-mono text-[8px] opacity-25" style={{ color }}>{bottomLabel}</span>
    </div>
  );
}

// ─── Panel ────────────────────────────────────────────────────────────────────

export function WaveformsPanel() {
  const current = useVitalsStore(s => s.current);

  const hrCps  = useMemo(() => (current?.hr  ?? 72) / 60, [current?.hr]);
  const rrCps  = useMemo(() => (current?.rr  ?? 14) / 60, [current?.rr]);
  const spoCps = useMemo(() => (current?.hr  ?? 72) / 60, [current?.hr]);

  const hrVal   = current?.hr   !== undefined ? `${current.hr}`   : '—';
  const spo2Val = current?.spo2 !== undefined ? `${current.spo2}` : '—';
  const etco2Val= current?.etco2!== undefined ? `${current.etco2}`: '—';

  return (
    <div className="flex flex-col h-full relative" style={{ background: '#FAFBFC' }}>

      {/* ── ECG II ──────────────────────────────────────────────────────── */}
      <WaveRow>
        <WaveformChart
          label="ECG II"
          color="#16A34A"
          generator={ecgPoint}
          cyclesPerSecond={hrCps}
          yMin={-0.45}
          yMax={1.4}
          windowSeconds={8}
          gridClass="waveform-grid-hr"
          glowClass="waveform-hr"
          numericValue={hrVal}
          unit="bpm"
        />
        <ScaleTicks color="#16A34A" topLabel="+1mV" midLabel="0" bottomLabel="−0.5mV" />
      </WaveRow>

      {/* ── PLETH SpO₂ ──────────────────────────────────────────────────── */}
      <WaveRow>
        <WaveformChart
          label="PLETH SpO₂"
          color="#0284C7"
          generator={plethPoint}
          cyclesPerSecond={spoCps}
          yMin={-0.1}
          yMax={1.3}
          windowSeconds={8}
          gridClass="waveform-grid-spo2"
          glowClass="waveform-spo2"
          numericValue={spo2Val}
          unit="%"
        />
        <ScaleTicks color="#0284C7" topLabel="100%" bottomLabel="0%" />
      </WaveRow>

      {/* ── CAPNO EtCO₂ ─────────────────────────────────────────────────── */}
      <WaveRow last>
        <WaveformChart
          label="CAPNO EtCO₂"
          color="#D97706"
          generator={capnoPoint}
          cyclesPerSecond={rrCps}
          yMin={-0.05}
          yMax={1.3}
          windowSeconds={10}
          gridClass="waveform-grid-etco2"
          glowClass="waveform-etco2"
          numericValue={etco2Val}
          unit="mmHg"
        />
        <ScaleTicks color="#D97706" topLabel="60" midLabel="30" bottomLabel="0" />

        {/* Camera / AI overlay — bottom-right of last wave row */}
        <CameraOverlay />
      </WaveRow>
    </div>
  );
}
