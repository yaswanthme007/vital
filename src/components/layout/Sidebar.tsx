import { NavLink, useNavigate } from 'react-router-dom';
import { motion } from 'framer-motion';
import { Activity, BarChart2, Settings2, Archive, Power, Cpu, Zap } from 'lucide-react';
import { cn } from '@/lib/utils';
import { useSessionStore } from '@/store/sessionStore';
import { useAlertStore } from '@/store/alertStore';
import { useVitalsStore } from '@/store/vitalsStore';
import { useDemoStore } from '@/store/demoStore';

const navItems = [
  { to: '/surgery',     icon: Activity,  label: 'Live Monitor' },
  { to: '/review',      icon: BarChart2,  label: 'Review & Sign-off' },
  { to: '/calibration', icon: Settings2,  label: 'Calibration' },
  { to: '/archive',     icon: Archive,    label: 'Session Archive' },
  { to: '/ocr-debug',   icon: Cpu,        label: 'OCR Pipeline Debug' },
];

export function Sidebar() {
  const { activeSession, endSession } = useSessionStore();
  const { active } = useAlertStore();
  const { clearHistory } = useVitalsStore();
  const { active: demoActive } = useDemoStore();
  const navigate = useNavigate();

  const criticalCount = active.filter((a) => a.severity === 'critical' && !a.acknowledged).length;
  const warningCount  = active.filter((a) => a.severity === 'warning'  && !a.acknowledged).length;

  const handleEndSession = () => {
    clearHistory();
    endSession();
    navigate('/start');
  };

  return (
    <aside className="flex flex-col items-center w-14 bg-monitor-surface border-r border-monitor-border py-3 gap-1 flex-shrink-0"
      aria-label="Application sidebar">
      {/* Logo */}
      <div className="mb-3 flex flex-col items-center" aria-hidden="true">
        <motion.div
          className="w-8 h-8 rounded-xl bg-gradient-to-br from-[#00FF88] to-[#00A65A] flex items-center justify-center"
          whileHover={{ scale: 1.08 }}
          transition={{ duration: 0.12 }}
        >
          <Activity size={16} className="text-black" />
        </motion.div>
      </div>

      <nav className="flex-1 flex flex-col gap-0.5 w-full px-1.5" aria-label="Main navigation">
        {navItems.map(({ to, icon: Icon, label }) => (
          <NavLink key={to} to={to}>
            {({ isActive }) => (
              <motion.div
                className={cn(
                  'relative flex items-center justify-center w-full h-10 rounded-xl',
                  'transition-colors duration-150 cursor-pointer group',
                  isActive
                    ? 'bg-white/8 text-[#E8F1FF]'
                    : 'text-[#3D5570] hover:text-[#7A90AA] hover:bg-white/5'
                )}
                whileHover={{ scale: 1.05 }}
                whileTap={{ scale: 0.95 }}
                transition={{ duration: 0.12 }}
                title={label}
                aria-label={label}
                aria-current={isActive ? 'page' : undefined}
              >
                <Icon size={18} aria-hidden="true" />
                {/* Active indicator */}
                {isActive && (
                  <motion.div
                    className="absolute left-0 top-2 bottom-2 w-0.5 rounded-r bg-[#32ADE6]"
                    layoutId="sidebar-indicator"
                    transition={{ duration: 0.2, ease: [0.16, 1, 0.3, 1] }}
                    aria-hidden="true"
                  />
                )}
                {/* Tooltip */}
                <div className="absolute left-full ml-3 px-2.5 py-1.5 rounded-lg bg-monitor-card border border-monitor-border
                                text-[#E8F1FF] font-display text-vital-xs whitespace-nowrap
                                opacity-0 group-hover:opacity-100 pointer-events-none transition-opacity
                                shadow-[0_4px_16px_rgba(0,0,0,0.5)] z-50"
                  aria-hidden="true">
                  {label}
                </div>
              </motion.div>
            )}
          </NavLink>
        ))}
      </nav>

      {/* Demo mode indicator */}
      {demoActive && (
        <motion.div
          initial={{ scale: 0, opacity: 0 }}
          animate={{ scale: 1, opacity: 1 }}
          className="relative mb-1"
          title="Demo Mode Active"
          aria-label="Demo mode is active"
          role="status"
        >
          <div className="w-8 h-8 rounded-xl bg-[rgba(255,214,0,0.15)] border border-[rgba(255,214,0,0.4)] flex items-center justify-center">
            <Zap size={14} className="text-[#FFD600]" aria-hidden="true" />
          </div>
          <span className="absolute -top-1 -right-1 w-2.5 h-2.5 rounded-full bg-[#FFD600] border-2 border-monitor-surface" aria-hidden="true">
            <span className="absolute inset-0 rounded-full bg-[#FFD600] animate-ping opacity-50" />
          </span>
        </motion.div>
      )}

      {/* Alert indicator */}
      {(criticalCount > 0 || warningCount > 0) && (
        <div className="relative mb-1"
          role="status"
          aria-label={criticalCount > 0
            ? `${criticalCount} critical alert${criticalCount > 1 ? 's' : ''}`
            : `${warningCount} warning${warningCount > 1 ? 's' : ''}`
          }>
          <div className={cn(
            'w-6 h-6 rounded-full flex items-center justify-center text-[10px] font-display font-bold',
            criticalCount > 0 ? 'bg-[#FF3B30] text-white animate-pulse-critical' : 'bg-[#FF9500] text-black'
          )} aria-hidden="true">
            {criticalCount || warningCount}
          </div>
        </div>
      )}

      {/* End session */}
      {activeSession && (
        <motion.button
          onClick={handleEndSession}
          className="w-10 h-10 rounded-xl flex items-center justify-center
                     text-[#FF3B30] hover:bg-[rgba(255,59,48,0.12)] transition-colors"
          whileHover={{ scale: 1.08 }}
          whileTap={{ scale: 0.92 }}
          title="End Session"
          aria-label="End current session"
        >
          <Power size={18} aria-hidden="true" />
        </motion.button>
      )}
    </aside>
  );
}
