import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { motion, AnimatePresence } from 'framer-motion';
import { Camera, Wifi, Activity, BellOff, PauseCircle, PlayCircle, BarChart2, Settings2, Archive, Cpu } from 'lucide-react';
import { useSessionStore } from '@/store/sessionStore';
import { useAlertStore } from '@/store/alertStore';
import { useSessionTimer, useClock } from '@/hooks/useSessionTimer';
import { DemoMode } from '@/features/demo/DemoMode';

export function SurgeryHeader() {
  const { activeSession, pauseSession, resumeSession } = useSessionStore();
  const { active } = useAlertStore();
  const elapsed = useSessionTimer(activeSession?.startTime ?? null);
  const clock   = useClock();
  const navigate = useNavigate();

  const [captureOn, setCaptureOn] = useState(true);

  if (!activeSession) return null;

  const criticalCount = active.filter(a => a.severity === 'critical' && !a.acknowledged).length;
  const warningCount  = active.filter(a => a.severity === 'warning'  && !a.acknowledged).length;
  const isPaused      = activeSession.status === 'paused';

  return (
    <header
      className="flex items-center gap-0 flex-shrink-0 bg-white border-b border-slate-200 overflow-x-auto"
      style={{ height: 56 }}
    >
      {/* Zone 1 — logo + nav back */}
      <div className="flex items-center gap-3 px-4 h-full border-r border-slate-200 flex-shrink-0" style={{ minWidth: 64 }}>
        <motion.button
          onClick={() => navigate('/landing')}
          className="w-9 h-9 rounded-xl flex items-center justify-center transition-colors hover:bg-slate-100"
          style={{ background: 'linear-gradient(135deg, #1D4ED8 0%, #7C3AED 100%)' }}
          whileHover={{ scale: 1.06 }}
          whileTap={{ scale: 0.94 }}
          aria-label="Back to home"
        >
          <Activity size={16} className="text-white" />
        </motion.button>
      </div>

      {/* Zone 2 — Patient + Procedure */}
      <div className="flex items-center gap-3 px-4 h-full border-r border-slate-200 flex-shrink-0" style={{ minWidth: 280 }}>
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="font-mono text-sm font-bold text-slate-900 tracking-tight">
              {activeSession.patient.id}
            </span>
            {activeSession.patient.asa && (
              <span className="font-display text-[9px] font-bold uppercase tracking-wider px-1.5 py-0.5 rounded-md bg-blue-50 border border-blue-200 text-blue-600">
                ASA {activeSession.patient.asa}
              </span>
            )}
          </div>
          <div className="font-display text-[10px] text-slate-400 truncate max-w-[240px]">
            {activeSession.procedure}
          </div>
        </div>
      </div>

      {/* Zone 3 — Anaesthetist */}
      <div className="flex items-center gap-2 px-4 h-full border-r border-slate-200 flex-shrink-0" style={{ minWidth: 160 }}>
        <div
          className="w-7 h-7 rounded-full flex items-center justify-center flex-shrink-0 font-mono text-[9px] font-bold text-white"
          style={{ background: 'linear-gradient(135deg, #7C3AED, #6D28D9)' }}
        >
          {activeSession.anesthetist?.split(' ').map(p => p[0]).join('').slice(0, 2).toUpperCase()}
        </div>
        <div className="min-w-0">
          <div className="font-display text-[10px] text-slate-400 uppercase tracking-wider">Anaesthetist</div>
          <div className="font-display text-[11px] text-slate-600 font-medium truncate">{activeSession.anesthetist}</div>
        </div>
      </div>

      {/* Zone 4 — Timer centre */}
      <div className="flex-1 flex items-center justify-center gap-8 h-full">
        <div className="text-center">
          <motion.div
            className="font-mono text-[22px] font-semibold text-slate-900 tabular-nums leading-none"
            animate={{ opacity: isPaused ? [1, 0.35, 1] : 1 }}
            transition={isPaused ? { repeat: Infinity, duration: 1.6 } : {}}
          >
            {clock}
          </motion.div>
          <div className="font-display text-[8px] text-slate-400 uppercase tracking-widest mt-0.5">Local Time</div>
        </div>

        <div className="h-5 w-px bg-slate-200" />

        <div className="text-center">
          <div className="font-mono text-base text-slate-600 tabular-nums leading-none">{elapsed}</div>
          <div className="font-display text-[8px] text-slate-400 uppercase tracking-widest mt-0.5">Duration</div>
        </div>

        <div className="h-5 w-px bg-slate-200" />

        <AnimatePresence mode="wait">
          {isPaused ? (
            <motion.div key="paused" initial={{ opacity: 0, scale: 0.9 }} animate={{ opacity: 1, scale: 1 }} exit={{ opacity: 0 }}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg font-display text-xs font-semibold uppercase tracking-wider bg-amber-50 border border-amber-200 text-amber-600">
              <PauseCircle size={11} /> Paused
            </motion.div>
          ) : (
            <motion.div key="rec" initial={{ opacity: 0, scale: 0.9 }} animate={{ opacity: 1, scale: 1 }} exit={{ opacity: 0 }}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg font-display text-xs font-semibold uppercase tracking-wider bg-red-50 border border-red-200 text-red-600">
              <motion.div className="w-1.5 h-1.5 rounded-full bg-red-500" animate={{ opacity: [1, 0, 1] }} transition={{ repeat: Infinity, duration: 0.9 }} />
              Recording
            </motion.div>
          )}
        </AnimatePresence>
      </div>

      {/* Zone 5 — Alerts */}
      <div className="flex items-center gap-2 px-4 h-full border-l border-slate-200">
        <AnimatePresence>
          {criticalCount > 0 && (
            <motion.div initial={{ opacity: 0, scale: 0.85 }} animate={{ opacity: 1, scale: 1 }} exit={{ opacity: 0 }}
              className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-red-50 border border-red-200 animate-glow-critical">
              <motion.div className="w-1.5 h-1.5 rounded-full bg-red-500" animate={{ opacity: [1, 0.2, 1] }} transition={{ repeat: Infinity, duration: 0.6 }} />
              <span className="font-display text-[10px] font-bold text-red-600">{criticalCount} Critical</span>
            </motion.div>
          )}
          {warningCount > 0 && (
            <motion.div initial={{ opacity: 0, scale: 0.85 }} animate={{ opacity: 1, scale: 1 }} exit={{ opacity: 0 }}
              className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-amber-50 border border-amber-200">
              <span className="font-display text-[10px] font-semibold text-amber-600">{warningCount} Warning</span>
            </motion.div>
          )}
          {criticalCount === 0 && warningCount === 0 && (
            <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="flex items-center gap-1.5">
              <BellOff size={11} className="text-slate-400" />
              <span className="font-display text-[10px] text-slate-400">No alerts</span>
            </motion.div>
          )}
        </AnimatePresence>
      </div>

      {/* Zone 6 — Controls */}
      <div className="flex items-center gap-2 px-4 h-full border-l border-slate-200 flex-shrink-0">
        {/* Cross-page navigation — Live Monitor has no shared nav bar, so these
            are the only way to reach Calibration / Archive / OCR Debug without
            first detouring through Review. */}
        <div className="flex items-center gap-1 pr-2 mr-1 border-r border-slate-200">
          <motion.button
            onClick={() => navigate('/calibration')}
            className="w-8 h-8 flex items-center justify-center rounded-lg text-slate-400 hover:text-slate-700 hover:bg-slate-100 transition-colors"
            whileHover={{ scale: 1.06 }} whileTap={{ scale: 0.94 }}
            title="Calibration" aria-label="Go to Calibration"
          >
            <Settings2 size={14} />
          </motion.button>
          <motion.button
            onClick={() => navigate('/archive')}
            className="w-8 h-8 flex items-center justify-center rounded-lg text-slate-400 hover:text-slate-700 hover:bg-slate-100 transition-colors"
            whileHover={{ scale: 1.06 }} whileTap={{ scale: 0.94 }}
            title="Archive" aria-label="Go to Session Archive"
          >
            <Archive size={14} />
          </motion.button>
          <motion.button
            onClick={() => navigate('/ocr-debug')}
            className="w-8 h-8 flex items-center justify-center rounded-lg text-slate-400 hover:text-slate-700 hover:bg-slate-100 transition-colors"
            whileHover={{ scale: 1.06 }} whileTap={{ scale: 0.94 }}
            title="OCR Debug" aria-label="Go to OCR Pipeline Debug"
          >
            <Cpu size={14} />
          </motion.button>
        </div>

        <DemoMode />

        {/* Camera */}
        <motion.button
          onClick={() => setCaptureOn(v => !v)}
          className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg border font-display text-xs transition-all"
          style={captureOn
            ? { background: '#F0FDF4', borderColor: '#BBF7D0', color: '#16A34A' }
            : { background: '#F8FAFC', borderColor: '#E2E8F0', color: '#94A3B8' }}
          whileHover={{ scale: 1.03 }} whileTap={{ scale: 0.97 }}
          aria-label={captureOn ? 'Camera active' : 'Camera off'}
        >
          <Camera size={11} />
          {captureOn ? 'Capture ON' : 'Off'}
        </motion.button>

        {/* WiFi */}
        <Wifi size={14} className="text-blue-400" />

        {/* Pause / Resume */}
        <motion.button
          onClick={isPaused ? resumeSession : pauseSession}
          className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg border font-display text-xs text-slate-500 border-slate-200 hover:border-slate-300 hover:bg-slate-50 transition-all"
          whileHover={{ scale: 1.03 }} whileTap={{ scale: 0.97 }}
        >
          {isPaused ? <PlayCircle size={11} /> : <PauseCircle size={11} />}
          {isPaused ? 'Resume' : 'Pause'}
        </motion.button>

        {/* Review button */}
        <motion.button
          onClick={() => navigate('/review')}
          className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg font-display text-xs font-semibold text-white"
          style={{ background: 'linear-gradient(135deg, #1D4ED8, #1E40AF)' }}
          whileHover={{ scale: 1.03, boxShadow: '0 3px 12px rgba(29,78,216,0.3)' }}
          whileTap={{ scale: 0.97 }}
        >
          <BarChart2 size={11} />
          Review
        </motion.button>
      </div>
    </header>
  );
}
