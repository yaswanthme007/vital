import { NavLink, useNavigate } from 'react-router-dom';
import { motion, AnimatePresence } from 'framer-motion';
import { Activity, BarChart2, Settings2, Archive, Cpu, Zap, Power, AlertTriangle } from 'lucide-react';
import { cn } from '@/lib/utils';
import { useSessionStore } from '@/store/sessionStore';
import { useAlertStore } from '@/store/alertStore';
import { useVitalsStore } from '@/store/vitalsStore';
import { useDemoStore } from '@/store/demoStore';

const NAV_LINKS = [
  { to: '/surgery',     icon: Activity,  label: 'Live Monitor'       },
  { to: '/review',      icon: BarChart2,  label: 'Review & Sign-off'  },
  { to: '/archive',     icon: Archive,    label: 'Archive'            },
  { to: '/calibration', icon: Settings2,  label: 'Calibration'       },
  { to: '/ocr-debug',   icon: Cpu,        label: 'OCR Debug'          },
];

export function TopNav() {
  const { activeSession, endSession } = useSessionStore();
  const { active } = useAlertStore();
  const { clearHistory } = useVitalsStore();
  const { active: demoActive } = useDemoStore();
  const navigate = useNavigate();

  const criticalCount = active.filter(a => a.severity === 'critical' && !a.acknowledged).length;

  const handleEndSession = () => {
    clearHistory();
    endSession();
    navigate('/landing');
  };

  return (
    <nav
      className="flex items-center h-14 bg-white border-b border-clinical-border flex-shrink-0 px-4 gap-1"
      aria-label="Main navigation"
      style={{ boxShadow: '0 1px 0 #E2E8F0' }}
    >
      {/* Logo */}
      <NavLink
        to="/landing"
        className="flex items-center gap-2.5 mr-6 group"
        aria-label="VITAL — Home"
      >
        <motion.div
          className="w-8 h-8 rounded-xl flex items-center justify-center flex-shrink-0"
          style={{ background: 'linear-gradient(135deg, #1D4ED8 0%, #7C3AED 100%)' }}
          whileHover={{ scale: 1.08 }}
          transition={{ duration: 0.15 }}
        >
          <Activity size={16} className="text-white" />
        </motion.div>
        <span className="font-heading font-bold text-base text-clinical-text tracking-tight">VITAL</span>
      </NavLink>

      {/* Nav links */}
      <div className="flex items-center gap-0.5 flex-1">
        {NAV_LINKS.map(({ to, icon: Icon, label }) => (
          <NavLink key={to} to={to}>
            {({ isActive }) => (
              <motion.div
                className={cn(
                  'flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-display font-medium transition-colors',
                  isActive
                    ? 'bg-clinical-primary-bg text-clinical-primary'
                    : 'text-clinical-text-2 hover:text-clinical-text hover:bg-slate-50',
                )}
                whileHover={{ scale: 1.02 }}
                whileTap={{ scale: 0.98 }}
                transition={{ duration: 0.12 }}
                aria-current={isActive ? 'page' : undefined}
              >
                <Icon size={14} aria-hidden="true" />
                {label}
              </motion.div>
            )}
          </NavLink>
        ))}
      </div>

      {/* Right side: alerts, demo, session, end */}
      <div className="flex items-center gap-2">
        {/* Critical alert badge */}
        <AnimatePresence>
          {criticalCount > 0 && (
            <motion.div
              initial={{ opacity: 0, scale: 0.85 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0, scale: 0.85 }}
              className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-red-50 border border-red-200 animate-glow-critical"
            >
              <AlertTriangle size={12} className="text-red-600" />
              <span className="font-display text-xs font-semibold text-red-600">{criticalCount} Critical</span>
            </motion.div>
          )}
        </AnimatePresence>

        {/* Demo mode indicator */}
        <AnimatePresence>
          {demoActive && (
            <motion.div
              initial={{ opacity: 0, scale: 0.85 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0, scale: 0.85 }}
              className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg border"
              style={{ background: 'rgba(234,179,8,0.08)', borderColor: 'rgba(234,179,8,0.35)' }}
              role="status"
              aria-label="Demo mode active"
            >
              <motion.div
                animate={{ scale: [1, 1.3, 1] }}
                transition={{ repeat: Infinity, duration: 1.4 }}
              >
                <Zap size={12} className="text-yellow-500" />
              </motion.div>
              <span className="font-display text-xs font-semibold text-yellow-600">DEMO</span>
            </motion.div>
          )}
        </AnimatePresence>

        {/* Session info + end button */}
        {activeSession && (
          <div className="flex items-center gap-2 pl-2 border-l border-clinical-border">
            <div className="text-right">
              <div className="font-display text-xs font-semibold text-clinical-text leading-tight">
                {activeSession.patient.id}
              </div>
              <div className="font-display text-[10px] text-clinical-text-2 truncate max-w-[140px]">
                {activeSession.procedure}
              </div>
            </div>
            <motion.button
              onClick={handleEndSession}
              className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg font-display text-xs font-medium
                         text-red-500 hover:bg-red-50 hover:text-red-600 border border-transparent hover:border-red-200
                         transition-colors"
              whileHover={{ scale: 1.03 }}
              whileTap={{ scale: 0.97 }}
              aria-label="End current session"
            >
              <Power size={12} />
              End
            </motion.button>
          </div>
        )}
      </div>
    </nav>
  );
}
