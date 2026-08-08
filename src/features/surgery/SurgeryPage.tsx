import { useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { motion } from 'framer-motion';
import { SurgeryHeader }  from './components/SurgeryHeader';
import { WaveformsPanel } from './components/WaveformsPanel';
import { VitalsGrid }     from './components/VitalsGrid';
import { AlertsFooter }   from './components/AlertsFooter';
import { DemoMode }       from '@/features/demo/DemoMode';
import { useSessionStore } from '@/store/sessionStore';
import { useVitalsSimulation } from '@/hooks/useVitalsSimulation';

export function SurgeryPage() {
  const { activeSession } = useSessionStore();
  const navigate = useNavigate();
  useVitalsSimulation();

  useEffect(() => {
    if (!activeSession) navigate('/start', { replace: true });
  }, [activeSession, navigate]);

  if (!activeSession) return null;

  return (
    <motion.div
      className="flex flex-col h-screen w-screen overflow-hidden"
      style={{ background: '#F8FAFC' }}
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.22 }}
    >
      <SurgeryHeader />

      <div className="flex flex-1 overflow-hidden min-h-0">
        {/* Waveforms — takes remaining horizontal space */}
        <div className="flex-1 overflow-hidden min-w-0 border-r border-slate-200">
          <WaveformsPanel />
        </div>
        {/* Vitals panel — fixed width, readable at 2 m */}
        <div className="flex-shrink-0 overflow-hidden" style={{ width: 320 }}>
          <VitalsGrid />
        </div>
      </div>

      <AlertsFooter />
      <DemoMode />
    </motion.div>
  );
}
