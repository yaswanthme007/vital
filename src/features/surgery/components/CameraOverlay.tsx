import { useState, useEffect } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { Camera, Wifi } from 'lucide-react';
import { clamp } from '@/lib/utils';

export function CameraOverlay() {
  const [conf, setConf] = useState(93);
  const [detecting, setDetecting] = useState('SpO₂');
  const [expanded, setExpanded] = useState(false);

  const vitals = ['HR', 'SpO₂', 'NIBP', 'EtCO₂', 'Temp', 'RR'];

  useEffect(() => {
    const confId = setInterval(() => {
      setConf(c => Math.round(clamp(c + (Math.random() - 0.48) * 3, 82, 99)));
    }, 2200);
    const detectId = setInterval(() => {
      setDetecting(vitals[Math.floor(Math.random() * vitals.length)]);
    }, 1800);
    return () => { clearInterval(confId); clearInterval(detectId); };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const barColor = conf >= 90 ? '#16A34A' : conf >= 78 ? '#D97706' : '#DC2626';

  return (
    <motion.div
      className="absolute bottom-4 right-4 z-20 cursor-pointer"
      onClick={() => setExpanded(e => !e)}
      whileHover={{ scale: 1.02 }}
    >
      <div
        className="rounded-xl overflow-hidden"
        style={{
          background: '#FFFFFF',
          border: '1px solid #E2E8F0',
          backdropFilter: 'blur(12px)',
          boxShadow: '0 4px 16px rgba(0,0,0,0.08)',
          minWidth: 160,
        }}
      >
        {/* Header row */}
        <div className="flex items-center gap-2 px-3 py-2" style={{ borderBottom: '1px solid #E2E8F0' }}>
          <Camera size={11} className="text-clinical-primary flex-shrink-0" />
          <span className="font-display text-[10px] font-bold uppercase tracking-widest text-slate-500 flex-1">
            AI Vision
          </span>
          {/* Live dot */}
          <motion.div
            className="w-1.5 h-1.5 rounded-full flex-shrink-0"
            style={{ backgroundColor: '#16A34A' }}
            animate={{ opacity: [1, 0.2, 1] }}
            transition={{ repeat: Infinity, duration: 1.4 }}
          />
        </div>

        {/* Confidence */}
        <div className="px-3 py-2">
          <div className="flex items-center justify-between mb-1.5">
            <span className="font-display text-[9px] uppercase tracking-[0.18em] text-slate-400">Confidence</span>
            <motion.span
              key={conf}
              className="font-mono text-[10px] font-bold"
              style={{ color: barColor }}
              initial={{ opacity: 0.5 }} animate={{ opacity: 1 }}
            >
              {conf}%
            </motion.span>
          </div>
          <div className="h-1 rounded-full overflow-hidden" style={{ backgroundColor: '#F1F5F9' }}>
            <motion.div
              className="h-full rounded-full"
              style={{ backgroundColor: barColor }}
              animate={{ width: `${conf}%` }}
              transition={{ duration: 0.5, ease: [0.16, 1, 0.3, 1] }}
            />
          </div>
        </div>

        {/* Detecting */}
        <AnimatePresence mode="wait">
          <motion.div
            key={detecting}
            className="px-3 pb-2 flex items-center gap-1.5"
            initial={{ opacity: 0, y: 3 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.2 }}
          >
            <Wifi size={9} className="text-slate-400" />
            <span className="font-display text-[9px] text-slate-400">
              Reading: <span className="text-slate-600">{detecting}</span>
            </span>
          </motion.div>
        </AnimatePresence>
      </div>
    </motion.div>
  );
}
