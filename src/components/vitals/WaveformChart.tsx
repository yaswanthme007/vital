import { useEffect, useRef } from 'react';
import uPlot from 'uplot';
import { cn } from '@/lib/utils';

type WaveformFn = (phase: number) => number;

interface WaveformChartProps {
  label: string;
  color: string;
  generator: WaveformFn;
  cyclesPerSecond: number;
  yMin?: number;
  yMax?: number;
  windowSeconds?: number;
  gridClass?: string;
  glowClass?: string;
  numericValue?: string;
  unit?: string;
}

const SAMPLE_RATE = 200;

export function WaveformChart({
  label,
  color,
  generator,
  cyclesPerSecond,
  yMin = -0.4,
  yMax = 1.35,
  windowSeconds = 8,
  gridClass,
  glowClass,
  numericValue,
  unit,
}: WaveformChartProps) {
  const outerRef    = useRef<HTMLDivElement>(null);
  const canvasWrap  = useRef<HTMLDivElement>(null);
  const plotRef     = useRef<uPlot | null>(null);
  const phaseRef    = useRef(0);
  const rafRef      = useRef(0);
  const lastTRef    = useRef(0);
  const writePosRef = useRef(0);
  const bufferRef   = useRef<number[]>([]);

  // Mutable refs so RAF loop picks up changes without re-running the effect
  const genRef = useRef(generator);
  const cpsRef = useRef(cyclesPerSecond);
  useEffect(() => { genRef.current = generator; },        [generator]);
  useEffect(() => { cpsRef.current = cyclesPerSecond; }, [cyclesPerSecond]);

  const totalSamples = windowSeconds * SAMPLE_RATE;

  useEffect(() => {
    if (!canvasWrap.current) return;

    bufferRef.current   = new Array(totalSamples).fill(0);
    writePosRef.current = 0;
    phaseRef.current    = 0;
    lastTRef.current    = 0;

    const times = Array.from({ length: totalSamples }, (_, i) => i / SAMPLE_RATE);

    const opts: uPlot.Options = {
      width:  canvasWrap.current.clientWidth  || 400,
      height: canvasWrap.current.clientHeight || 120,
      cursor: { show: false },
      legend: { show: false },
      scales: {
        x: { time: false, auto: false, range: () => [0, windowSeconds] },
        y: { auto: false, range: () => [yMin, yMax] },
      },
      axes: [{ show: false }, { show: false }],
      series: [
        {},
        { stroke: color, width: 2.2, spanGaps: false },
      ],
      padding: [2, 2, 2, 2],
      pxAlign: false,
    };

    plotRef.current = new uPlot(opts, [times, [...bufferRef.current]], canvasWrap.current);

    const animate = (t: number) => {
      if (lastTRef.current === 0) { lastTRef.current = t; }
      const dt = Math.min((t - lastTRef.current) / 1000, 0.08);
      lastTRef.current = t;

      const samples = Math.max(1, Math.round(dt * SAMPLE_RATE));
      const dPhase  = (cpsRef.current * dt) / samples;

      for (let i = 0; i < samples; i++) {
        phaseRef.current = (phaseRef.current + dPhase) % 1;
        bufferRef.current[writePosRef.current] = genRef.current(phaseRef.current);
        writePosRef.current = (writePosRef.current + 1) % totalSamples;
      }

      const wp = writePosRef.current;
      const reordered = new Array(totalSamples);
      for (let i = 0; i < totalSamples; i++) {
        reordered[i] = bufferRef.current[(wp + i) % totalSamples];
      }
      // Gap at write head — simulates CRT sweep
      const gap = 6;
      for (let i = totalSamples - gap; i < totalSamples; i++) reordered[i] = NaN;

      plotRef.current?.setData([times, reordered], false);
      rafRef.current = requestAnimationFrame(animate);
    };

    rafRef.current = requestAnimationFrame(animate);

    const ro = new ResizeObserver(() => {
      if (!canvasWrap.current || !plotRef.current) return;
      plotRef.current.setSize({
        width:  canvasWrap.current.clientWidth  || 400,
        height: canvasWrap.current.clientHeight || 120,
      });
    });
    ro.observe(canvasWrap.current);

    return () => {
      cancelAnimationFrame(rafRef.current);
      ro.disconnect();
      plotRef.current?.destroy();
      plotRef.current = null;
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [color, yMin, yMax, windowSeconds, totalSamples]);

  return (
    <div ref={outerRef} className="relative w-full h-full overflow-hidden">
      {/* Grid */}
      <div className={cn('absolute inset-0 pointer-events-none', gridClass)} />

      {/* Canvas */}
      <div ref={canvasWrap} className={cn('absolute inset-0 overflow-hidden', glowClass)} />

      {/* Label + value overlay */}
      <div className="absolute top-1.5 left-3 flex items-baseline gap-2 pointer-events-none z-10">
        <span
          className="font-display text-vital-xs font-semibold uppercase tracking-[0.15em] opacity-70"
          style={{ color }}
        >
          {label}
        </span>
        {numericValue && (
          <span className="font-mono text-vital-sm font-normal opacity-90" style={{ color }}>
            {numericValue}
            {unit && <span className="text-vital-xs ml-0.5 opacity-60">{unit}</span>}
          </span>
        )}
      </div>
    </div>
  );
}
