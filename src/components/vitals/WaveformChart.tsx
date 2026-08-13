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

    const animate = (t: number) => {
      // A transient throw here (e.g. a chart library hiccup mid-resize)
      // must never permanently kill the loop — always reschedule.
      try {
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
      } catch (err) {
        console.error('Waveform animation frame failed, continuing', err);
      } finally {
        rafRef.current = requestAnimationFrame(animate);
      }
    };

    // Measuring clientWidth/clientHeight synchronously at mount is a race:
    // this page fades in via Framer Motion and the surrounding flex layout
    // may not have settled its final size yet, so a size read here can lock
    // in a stale/zero box that never gets corrected — this was the original
    // bug. ResizeObserver's first callback normally reports the container's
    // real settled size shortly after, which is the standard fix... but it
    // turned out to still leave the chart stuck blank until something else
    // (e.g. opening DevTools) forced a resize, meaning the initial callback
    // isn't reliably arriving with a non-zero box in this layout on its own.
    // So this doesn't rely on any single trigger: init() is attempted
    // immediately, on every ResizeObserver callback, AND on a short rAF
    // poll for the first ~2s after mount — whichever fires first with a
    // real size wins, and the rest are no-ops via the `plotRef.current`
    // guard. Once initialized it behaves exactly as before.
    let pollId = 0;
    let pollFrames = 0;

    const init = (width: number, height: number) => {
      if (plotRef.current || width === 0 || height === 0 || !canvasWrap.current) return false;

      const opts: uPlot.Options = {
        width,
        height,
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
      rafRef.current = requestAnimationFrame(animate);
      return true;
    };

    const poll = () => {
      if (plotRef.current || !canvasWrap.current || pollFrames > 120) return; // ~2s at 60fps
      pollFrames += 1;
      const rect = canvasWrap.current.getBoundingClientRect();
      if (!init(rect.width, rect.height)) {
        pollId = requestAnimationFrame(poll);
      }
    };

    const initialRect = canvasWrap.current.getBoundingClientRect();
    if (!init(initialRect.width, initialRect.height)) {
      pollId = requestAnimationFrame(poll);
    }

    const ro = new ResizeObserver((entries) => {
      const box = entries[0]?.contentRect;
      if (!box || box.width === 0 || box.height === 0) return;
      if (!init(box.width, box.height)) {
        plotRef.current?.setSize({ width: box.width, height: box.height });
      }
    });
    ro.observe(canvasWrap.current);

    return () => {
      cancelAnimationFrame(rafRef.current);
      cancelAnimationFrame(pollId);
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
