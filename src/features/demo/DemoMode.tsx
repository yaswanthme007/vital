import { useState, useEffect, useRef } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { useNavigate } from 'react-router-dom';
import { Zap, X, ChevronDown, ChevronUp, Play, Square } from 'lucide-react';
import { useDemoStore, DEMO_SCENARIOS, type ScenarioId } from '@/store/demoStore';
import { useVitalsStore } from '@/store/vitalsStore';
import { useSessionStore } from '@/store/sessionStore';
import { useToast } from '@/store/toastStore';
import { cn } from '@/lib/utils';
import { randomWalk } from '@/lib/utils';

// DemoMode injects scenario vitals into the store every 1.5s,
// overriding the simulation's output. When active and no session
// exists, it auto-starts a demo session.

const DEMO_PATIENT = {
  patientId: 'PT-DEMO-001',
  patientAge: 52,
  patientWeight: 75,
  asa: 2 as const,
  procedure: 'Demo Surgery',
  anesthetist: 'Dr. Demo User',
};

export function DemoMode() {
  const [expanded, setExpanded] = useState(false);
  const { active, scenarioId, activate, deactivate } = useDemoStore();
  const { updateVitals } = useVitalsStore();
  const { activeSession, startSession, endSession } = useSessionStore();
  const { toast } = useToast();
  const navigate = useNavigate();
  const ivRef = useRef<ReturnType<typeof setInterval> | undefined>(undefined);
  const prevRef = useRef<Parameters<typeof updateVitals>[0] | null>(null);

  const activeScenario = DEMO_SCENARIOS.find((s) => s.id === scenarioId) ?? null;

  // When a scenario is activated: start session if needed, navigate, begin injection.
  // `activate(id)` only fires on success — otherwise the nav bar would show
  // "DEMO — <scenario>" as running even though no session ever started.
  const handleActivate = async (id: ScenarioId) => {
    try {
      if (!activeSession) {
        await startSession(DEMO_PATIENT);
      }
      activate(id);
      navigate('/surgery');
    } catch (err) {
      console.error('Failed to start demo session', err);
      toast.error('Could not start demo', {
        description: "Couldn't reach the backend — check it's running (docker compose up) and try again.",
      });
    }
  };

  const handleDeactivate = () => {
    deactivate();
    clearInterval(ivRef.current);
    prevRef.current = null;
  };

  // Vital injection loop
  useEffect(() => {
    clearInterval(ivRef.current);
    if (!active || !activeScenario) return;

    const targets = activeScenario.vitals;

    const tick = () => {
      const prev = prevRef.current;
      const next = {
        hr:           prev ? randomWalk(prev.hr,           1.5, targets.hr - 8,           targets.hr + 8,           targets.hr,           0.3) : targets.hr,
        spo2:         prev ? randomWalk(prev.spo2,         0.3, targets.spo2 - 3,          targets.spo2 + 1,          targets.spo2,          0.4) : targets.spo2,
        nibpSystolic: prev ? randomWalk(prev.nibpSystolic, 2,   targets.nibpSystolic - 6,  targets.nibpSystolic + 6,  targets.nibpSystolic,  0.25) : targets.nibpSystolic,
        nibpDiastolic: prev ? randomWalk(prev.nibpDiastolic, 1.5, targets.nibpDiastolic - 4, targets.nibpDiastolic + 4, targets.nibpDiastolic, 0.25) : targets.nibpDiastolic,
        nibpMean:     prev ? randomWalk(prev.nibpMean,     1,   targets.nibpMean - 3,       targets.nibpMean + 3,       targets.nibpMean,      0.3) : targets.nibpMean,
        etco2:        prev ? randomWalk(prev.etco2,        0.8, targets.etco2 - 5,          targets.etco2 + 5,          targets.etco2,          0.3) : targets.etco2,
        temp:         prev ? randomWalk(prev.temp,         0.05, targets.temp - 0.3,         targets.temp + 0.3,         targets.temp,          0.15) : targets.temp,
        rr:           prev ? randomWalk(prev.rr,           0.5, targets.rr - 2,             targets.rr + 2,             targets.rr,            0.25) : targets.rr,
        timestamp:    Date.now(),
      };
      // Round values
      const rounded = {
        ...next,
        hr:            Math.round(next.hr),
        spo2:          Math.round(next.spo2),
        nibpSystolic:  Math.round(next.nibpSystolic),
        nibpDiastolic: Math.round(next.nibpDiastolic),
        nibpMean:      Math.round(next.nibpMean),
        etco2:         Math.round(next.etco2 * 10) / 10,
        temp:          Math.round(next.temp * 10) / 10,
        rr:            Math.round(next.rr),
      };
      prevRef.current = rounded;
      updateVitals(rounded);
    };

    tick();
    ivRef.current = setInterval(tick, 1200);
    return () => clearInterval(ivRef.current);
  }, [active, scenarioId]);

  // Clean up on unmount
  useEffect(() => () => clearInterval(ivRef.current), []);

  return (
    <div className="relative flex-shrink-0">
      {/* Toggle button */}
      <motion.button
        onClick={() => setExpanded(v => !v)}
        className={cn(
          'flex items-center gap-2 px-3 py-1.5 rounded-xl border font-display text-vital-xs font-semibold transition-all duration-200',
          active
            ? 'bg-amber-400 border-amber-400 text-white shadow-[0_4px_16px_rgba(245,158,11,0.3)]'
            : 'bg-white border-slate-200 text-slate-500 hover:text-slate-700 hover:border-slate-300'
        )}
        whileHover={{ scale: 1.04 }}
        whileTap={{ scale: 0.96 }}
        transition={{ duration: 0.1 }}
        aria-expanded={expanded}
        aria-label="Demo mode controls"
      >
        <Zap size={14} className={active ? 'text-white' : 'text-amber-400'} />
        {active ? (
          <span>DEMO — {activeScenario?.label}</span>
        ) : (
          <span>Demo Mode</span>
        )}
        {expanded ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
      </motion.button>

      {/* Expanded panel */}
      <AnimatePresence>
        {expanded && (
          <motion.div
            className="absolute top-full right-0 mt-2 w-72 rounded-2xl overflow-hidden z-50"
            style={{
              background: '#FFFFFF',
              border: '1px solid #E2E8F0',
              boxShadow: '0 8px 32px rgba(0,0,0,0.14)',
            }}
            initial={{ opacity: 0, scale: 0.94, y: -8 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.94, y: -8 }}
            transition={{ duration: 0.2, ease: [0.16, 1, 0.3, 1] }}
          >
            {/* Panel header */}
            <div className="flex items-center justify-between px-4 py-3 border-b border-slate-100">
              <div className="flex items-center gap-2">
                <Zap size={14} className="text-amber-400" />
                <span className="font-display text-vital-xs font-semibold text-slate-700">Demo Mode</span>
              </div>
              {active && (
                <button
                  onClick={handleDeactivate}
                  className="flex items-center gap-1 px-2 py-1 rounded-lg bg-red-50 border border-red-200 text-red-600 font-display text-[10px] hover:bg-red-100 transition-colors"
                >
                  <Square size={10} /> Stop
                </button>
              )}
            </div>

            {/* Active scenario banner */}
            {activeScenario && (
              <motion.div
                key={activeScenario.id}
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                className="mx-3 mt-3 rounded-xl px-3 py-2.5 flex items-center gap-2.5"
                style={{ backgroundColor: `${activeScenario.color}15`, border: `1px solid ${activeScenario.color}30` }}
              >
                <div className="w-6 h-6 rounded-full flex items-center justify-center text-xs font-bold" style={{ backgroundColor: activeScenario.color, color: '#000' }}>
                  {activeScenario.icon}
                </div>
                <div>
                  <div className="font-display text-vital-xs font-semibold" style={{ color: activeScenario.color }}>
                    {activeScenario.label}
                  </div>
                  <div className="font-display text-[10px] text-slate-400">{activeScenario.description}</div>
                </div>
                <div className="ml-auto">
                  <span className="relative flex w-2 h-2">
                    <span className="animate-ping absolute inline-flex h-full w-full rounded-full opacity-75" style={{ backgroundColor: activeScenario.color }} />
                    <span className="relative inline-flex rounded-full w-2 h-2" style={{ backgroundColor: activeScenario.color }} />
                  </span>
                </div>
              </motion.div>
            )}

            {/* Scenario list */}
            <div className="p-3 space-y-1">
              <div className="font-display text-[10px] text-slate-400 uppercase tracking-wider px-1 mb-2">
                {active ? 'Switch scenario' : 'Select scenario'}
              </div>
              {DEMO_SCENARIOS.map((scenario) => {
                const isActive = active && scenarioId === scenario.id;
                return (
                  <button
                    key={scenario.id}
                    onClick={() => handleActivate(scenario.id)}
                    className={cn(
                      'w-full flex items-center gap-3 px-3 py-2.5 rounded-xl border text-left transition-all duration-150',
                      isActive
                        ? 'border-transparent'
                        : 'border-transparent hover:bg-slate-50 hover:border-slate-200'
                    )}
                    style={isActive ? { backgroundColor: `${scenario.color}10`, border: `1px solid ${scenario.color}30` } : {}}
                  >
                    <div
                      className="w-7 h-7 rounded-full flex items-center justify-center text-xs font-bold flex-shrink-0"
                      style={{ backgroundColor: `${scenario.color}15`, color: scenario.color, border: `1px solid ${scenario.color}30` }}
                    >
                      {scenario.icon}
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="font-display text-vital-xs font-medium text-slate-700 flex items-center gap-1.5">
                        {scenario.label}
                        {isActive && (
                          <span className="relative flex w-1.5 h-1.5">
                            <span className="animate-ping absolute inline-flex h-full w-full rounded-full opacity-75" style={{ backgroundColor: scenario.color }} />
                            <span className="relative inline-flex rounded-full w-1.5 h-1.5" style={{ backgroundColor: scenario.color }} />
                          </span>
                        )}
                      </div>
                      <div className="font-display text-[10px] text-slate-400 truncate">{scenario.description}</div>
                    </div>
                    {!isActive && <Play size={12} className="text-slate-400 flex-shrink-0" />}
                  </button>
                );
              })}
            </div>

            {/* Footer */}
            <div className="px-4 pb-3">
              <p className="font-display text-[10px] text-slate-300 text-center">
                All data simulated · No real patient data
              </p>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
