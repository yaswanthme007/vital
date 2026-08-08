import { useState, useEffect } from 'react';
import { formatDuration } from '@/lib/utils';

export function useSessionTimer(startTime: number | null | undefined): string {
  const [elapsed, setElapsed] = useState('00:00:00');

  useEffect(() => {
    if (!startTime) { setElapsed('00:00:00'); return; }

    const tick = () => setElapsed(formatDuration(Date.now() - startTime));
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [startTime]);

  return elapsed;
}

export function useClock(): string {
  const [time, setTime] = useState('');

  useEffect(() => {
    const tick = () => {
      setTime(new Date().toLocaleTimeString('en-IN', {
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
        hour12: false,
      }));
    };
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, []);

  return time;
}
