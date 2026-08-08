import { useState, useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { motion, useInView } from 'framer-motion';
import {
  Activity, Camera, ShieldCheck, FileText, Archive, Cpu,
  ArrowRight, CheckCircle2, Wifi, Lock, Zap, Eye,
  ChevronRight,
} from 'lucide-react';
import { useSessionStore } from '@/store/sessionStore';
import { useDemoStore } from '@/store/demoStore';

// ─── Animated Vital Card ─────────────────────────────────────────────────────

interface LiveVitalCardProps {
  label: string;
  value: number;
  unit: string;
  color: string;
  bgColor: string;
  delta?: number;
  delay?: number;
}

function LiveVitalCard({ label, value, unit, color, bgColor, delta = 0, delay = 0 }: LiveVitalCardProps) {
  const [display, setDisplay] = useState(value);

  useEffect(() => {
    if (delta === 0) return;
    const iv = setInterval(() => {
      setDisplay(v => {
        const next = v + (Math.random() - 0.5) * delta;
        return Math.round(Math.max(value * 0.88, Math.min(value * 1.12, next)));
      });
    }, 2000 + Math.random() * 600);
    return () => clearInterval(iv);
  }, [value, delta]);

  return (
    <motion.div
      initial={{ opacity: 0, y: 18 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay, duration: 0.5, ease: [0.16, 1, 0.3, 1] }}
      className="flex flex-col bg-white rounded-2xl border border-slate-200 overflow-hidden"
      style={{ boxShadow: '0 2px 12px rgba(0,0,0,0.06)' }}
    >
      <div className="h-1 w-full" style={{ background: color }} />
      <div className="px-3.5 py-3">
        <div className="font-display text-[10px] font-semibold uppercase tracking-[0.18em] text-slate-400 mb-1.5">
          {label}
        </div>
        <div className="flex items-end gap-1.5">
          <motion.span
            key={display}
            initial={{ opacity: 0.5, y: -3 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.18 }}
            className="font-mono text-3xl font-bold leading-none tabular-nums"
            style={{ color }}
          >
            {display}
          </motion.span>
          <span className="font-display text-xs text-slate-400 mb-0.5">{unit}</span>
        </div>
        <div className="mt-2 flex items-center gap-1.5">
          <div className="h-0.5 flex-1 rounded-full" style={{ background: bgColor }}>
            <motion.div
              className="h-full rounded-full"
              style={{ background: color }}
              animate={{ width: ['72%', '91%', '78%', '88%'] }}
              transition={{ duration: 6, repeat: Infinity, ease: 'easeInOut' }}
            />
          </div>
          <span className="font-mono text-[9px] font-bold" style={{ color }}>
            AI ✓
          </span>
        </div>
      </div>
    </motion.div>
  );
}

// ─── Feature Card ─────────────────────────────────────────────────────────────

function FeatureCard({
  icon: Icon, color, bg, title, desc, delay = 0,
}: {
  icon: React.ElementType; color: string; bg: string;
  title: string; desc: string; delay?: number;
}) {
  const ref = useRef(null);
  const inView = useInView(ref, { once: true, margin: '-60px' });

  return (
    <motion.div
      ref={ref}
      initial={{ opacity: 0, y: 24 }}
      animate={inView ? { opacity: 1, y: 0 } : {}}
      transition={{ delay, duration: 0.52, ease: [0.16, 1, 0.3, 1] }}
      whileHover={{ y: -5, boxShadow: '0 16px 40px rgba(0,0,0,0.1)' }}
      className="bg-white rounded-2xl border border-slate-200 p-6 cursor-default transition-shadow"
      style={{ boxShadow: '0 1px 4px rgba(0,0,0,0.05)' }}
    >
      <div className="w-11 h-11 rounded-xl flex items-center justify-center mb-4" style={{ background: bg }}>
        <Icon size={22} style={{ color }} />
      </div>
      <h3 className="font-heading font-semibold text-base text-slate-900 mb-2">{title}</h3>
      <p className="font-display text-sm text-slate-500 leading-relaxed">{desc}</p>
    </motion.div>
  );
}

// ─── Data ────────────────────────────────────────────────────────────────────

const VITALS_PREVIEW = [
  { label: 'Heart Rate', value: 74,  unit: 'bpm',  color: '#16A34A', bgColor: '#DCFCE7', delta: 5  },
  { label: 'SpO₂',       value: 98,  unit: '%',    color: '#0284C7', bgColor: '#E0F2FE', delta: 2  },
  { label: 'EtCO₂',      value: 35,  unit: 'mmHg', color: '#D97706', bgColor: '#FEF3C7', delta: 4  },
  { label: 'NIBP Sys',   value: 128, unit: 'mmHg', color: '#DC2626', bgColor: '#FEE2E2', delta: 8  },
  { label: 'Resp Rate',  value: 14,  unit: '/min', color: '#7C3AED', bgColor: '#EDE9FE', delta: 3  },
  { label: 'Temp',       value: 37,  unit: '°C',   color: '#EA580C', bgColor: '#FFEDD5', delta: 0  },
];

const FEATURES = [
  { icon: Camera,       color: '#0284C7', bg: '#E0F2FE', title: 'Camera-Based AI OCR',      desc: 'No proprietary hardware needed. A standard USB camera reads any anaesthesia monitor through computer vision and AI.' },
  { icon: ShieldCheck,  color: '#16A34A', bg: '#DCFCE7', title: 'Medical Validation Engine', desc: 'Physiological rules cross-check every reading. Anomalies are flagged before they reach the record.' },
  { icon: Activity,     color: '#1D4ED8', bg: '#DBEAFE', title: 'Live Surgery Dashboard',    desc: 'Real-time vitals, animated waveforms, confidence scores, and intelligent alert management.' },
  { icon: CheckCircle2, color: '#D97706', bg: '#FEF3C7', title: 'AI-Assisted Review',        desc: 'Anaesthetists review AI-flagged readings with captured frame context before signing off.' },
  { icon: Lock,         color: '#DC2626', bg: '#FEE2E2', title: 'Medico-Legal Records',      desc: 'Signed, locked, and audit-trailed. Every correction logged. Generates a court-admissible PDF.' },
  { icon: Wifi,         color: '#7C3AED', bg: '#EDE9FE', title: '100% Offline',              desc: 'Runs on Jetson Orin, Raspberry Pi, or any mini PC. No internet or cloud dependency.' },
];

const PIPELINE_STEPS = [
  { label: 'USB Camera',    icon: Camera,       color: '#0284C7' },
  { label: 'Detect Screen', icon: Eye,          color: '#7C3AED' },
  { label: 'Warp Correct',  icon: Cpu,          color: '#D97706' },
  { label: 'OCR Vitals',    icon: Activity,     color: '#16A34A' },
  { label: 'Validate',      icon: ShieldCheck,  color: '#DC2626' },
  { label: 'Live Display',  icon: Zap,          color: '#1D4ED8' },
  { label: 'Review & Sign', icon: CheckCircle2, color: '#16A34A' },
  { label: 'PDF Record',    icon: FileText,     color: '#475569' },
];

// ─── Page ────────────────────────────────────────────────────────────────────

export function LandingPage() {
  const navigate = useNavigate();
  const { startSession, activeSession } = useSessionStore();
  const { activate: activateDemo } = useDemoStore();
  const [demoLoading, setDemoLoading] = useState(false);
  const pipelineRef = useRef(null);
  const pipelineInView = useInView(pipelineRef, { once: true, margin: '-80px' });

  const handleStartNew = () => navigate('/start');

  const handleDemo = async () => {
    setDemoLoading(true);
    await new Promise(r => setTimeout(r, 500));
    if (!activeSession) {
      startSession({
        patientId: 'DEMO-001',
        procedure: 'Laparoscopic Cholecystectomy',
        anesthetist: 'Dr. Sarah Chen',
        asa: 2,
      });
    }
    activateDemo('normal');
    navigate('/surgery');
  };

  return (
    <div className="h-screen w-screen overflow-y-auto overflow-x-hidden bg-clinical-bg">

      {/* ── Sticky Navbar ────────────────────────────────────────────────────── */}
      <header className="sticky top-0 z-50 bg-white/90 backdrop-blur-md border-b border-slate-200">
        <div className="max-w-7xl mx-auto px-6 h-16 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <motion.div
              className="w-9 h-9 rounded-xl flex items-center justify-center"
              style={{ background: 'linear-gradient(135deg, #1D4ED8 0%, #7C3AED 100%)' }}
              whileHover={{ scale: 1.08 }}
              transition={{ duration: 0.15 }}
            >
              <Activity size={18} className="text-white" />
            </motion.div>
            <div>
              <span className="font-heading font-bold text-lg text-slate-900 tracking-tight">VITAL</span>
              <span className="font-display text-[10px] text-slate-400 ml-2 hidden sm:inline uppercase tracking-widest">
                Intelligent Anaesthesia Documentation
              </span>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <motion.button
              onClick={() => navigate('/ocr-debug')}
              className="hidden md:flex items-center gap-1.5 px-3 py-1.5 rounded-lg font-display text-sm text-slate-500 hover:text-slate-700 hover:bg-slate-100 transition-colors"
              whileHover={{ scale: 1.02 }}
            >
              <Cpu size={14} />
              OCR Debug
            </motion.button>
            <motion.button
              onClick={() => navigate('/archive')}
              className="hidden md:flex items-center gap-1.5 px-3 py-1.5 rounded-lg font-display text-sm text-slate-500 hover:text-slate-700 hover:bg-slate-100 transition-colors"
              whileHover={{ scale: 1.02 }}
            >
              <Archive size={14} />
              Archive
            </motion.button>
            <motion.button
              onClick={handleStartNew}
              className="flex items-center gap-2 px-4 py-2 rounded-xl font-display text-sm font-semibold text-white"
              style={{ background: 'linear-gradient(135deg, #1D4ED8 0%, #1E40AF 100%)' }}
              whileHover={{ scale: 1.03, boxShadow: '0 4px 16px rgba(29,78,216,0.35)' }}
              whileTap={{ scale: 0.98 }}
            >
              Start New Case
              <ChevronRight size={14} />
            </motion.button>
          </div>
        </div>
      </header>

      {/* ── Hero ─────────────────────────────────────────────────────────────── */}
      <section className="relative hero-gradient overflow-hidden">
        {/* Decorative radial orbs */}
        <div className="absolute top-0 left-1/4 w-[700px] h-[700px] rounded-full pointer-events-none"
          style={{ background: 'radial-gradient(circle, rgba(29,78,216,0.07) 0%, transparent 70%)', transform: 'translate(-30%,-40%)' }} />
        <div className="absolute bottom-0 right-1/4 w-[500px] h-[500px] rounded-full pointer-events-none"
          style={{ background: 'radial-gradient(circle, rgba(124,58,237,0.06) 0%, transparent 70%)', transform: 'translate(30%,30%)' }} />

        <div className="relative max-w-7xl mx-auto px-6 pt-20 pb-16">
          <div className="grid lg:grid-cols-2 gap-14 items-center">

            {/* Left: copy */}
            <div>
              <motion.div
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.45 }}
                className="inline-flex items-center gap-2 px-3.5 py-1.5 rounded-full text-xs font-semibold mb-6 border"
                style={{ background: '#EFF6FF', borderColor: '#BFDBFE', color: '#1D4ED8' }}
              >
                <motion.div
                  className="w-1.5 h-1.5 rounded-full bg-blue-500"
                  animate={{ scale: [1, 1.5, 1] }}
                  transition={{ repeat: Infinity, duration: 2 }}
                />
                AI-Powered · Vendor-Neutral · Offline-First
              </motion.div>

              <motion.h1
                initial={{ opacity: 0, y: 22 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: 0.1, duration: 0.65, ease: [0.16, 1, 0.3, 1] }}
                className="font-heading font-bold text-5xl lg:text-6xl text-slate-900 leading-[1.05] tracking-tight mb-5"
              >
                Every vital sign,
                <br />
                <span className="text-transparent bg-clip-text"
                  style={{ backgroundImage: 'linear-gradient(135deg, #1D4ED8, #7C3AED)' }}>
                  automatically
                </span>
                <br />
                documented.
              </motion.h1>

              <motion.p
                initial={{ opacity: 0, y: 14 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: 0.22, duration: 0.6, ease: [0.16, 1, 0.3, 1] }}
                className="font-display text-lg text-slate-500 leading-relaxed mb-8 max-w-lg"
              >
                VITAL transforms any anaesthesia monitor into an intelligent documentation
                system using a camera and AI — no hardware integration required.
              </motion.p>

              <motion.div
                initial={{ opacity: 0, y: 12 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: 0.32, duration: 0.5 }}
                className="flex flex-wrap gap-3 mb-8"
              >
                <motion.button
                  onClick={handleStartNew}
                  className="flex items-center gap-2.5 px-6 py-3.5 rounded-xl font-display font-semibold text-base text-white"
                  style={{ background: 'linear-gradient(135deg, #1D4ED8, #1E40AF)', boxShadow: '0 4px 20px rgba(29,78,216,0.35)' }}
                  whileHover={{ scale: 1.03, boxShadow: '0 6px 28px rgba(29,78,216,0.45)' }}
                  whileTap={{ scale: 0.97 }}
                >
                  Start New Case
                  <ArrowRight size={18} />
                </motion.button>
                <motion.button
                  onClick={handleDemo}
                  disabled={demoLoading}
                  className="flex items-center gap-2.5 px-6 py-3.5 rounded-xl font-display font-semibold text-base text-slate-700 bg-white border border-slate-200 hover:border-slate-300 hover:bg-slate-50 transition-all disabled:opacity-70"
                  style={{ boxShadow: '0 1px 4px rgba(0,0,0,0.06)' }}
                  whileHover={{ scale: 1.02 }}
                  whileTap={{ scale: 0.97 }}
                >
                  {demoLoading
                    ? <motion.div animate={{ rotate: 360 }} transition={{ repeat: Infinity, duration: 0.8, ease: 'linear' }}><Activity size={16} /></motion.div>
                    : <Zap size={16} className="text-amber-500" />}
                  {demoLoading ? 'Starting…' : 'Live Demo'}
                </motion.button>
              </motion.div>

              <motion.div
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                transition={{ delay: 0.5 }}
                className="flex items-center gap-5 flex-wrap"
              >
                {['No hardware integration', 'Works offline', 'Medico-legal PDF'].map(t => (
                  <div key={t} className="flex items-center gap-1.5">
                    <CheckCircle2 size={13} className="text-emerald-500" />
                    <span className="font-display text-xs text-slate-500">{t}</span>
                  </div>
                ))}
              </motion.div>
            </div>

            {/* Right: live monitor preview */}
            <motion.div
              initial={{ opacity: 0, x: 28 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ delay: 0.25, duration: 0.7, ease: [0.16, 1, 0.3, 1] }}
              className="relative"
            >
              <div
                className="rounded-2xl overflow-hidden border border-slate-200"
                style={{ boxShadow: '0 24px 64px rgba(0,0,0,0.12), 0 4px 16px rgba(0,0,0,0.06)' }}
              >
                {/* Browser-style header */}
                <div className="flex items-center justify-between px-4 py-3 bg-slate-50 border-b border-slate-200">
                  <div className="flex items-center gap-2">
                    <div className="flex gap-1.5">
                      <div className="w-3 h-3 rounded-full bg-red-400" />
                      <div className="w-3 h-3 rounded-full bg-amber-400" />
                      <div className="w-3 h-3 rounded-full bg-emerald-400" />
                    </div>
                    <span className="font-mono text-xs text-slate-400 ml-1">VITAL Monitor — PT-2024-001</span>
                  </div>
                  <div className="flex items-center gap-1.5">
                    <motion.div
                      className="w-1.5 h-1.5 rounded-full bg-red-500"
                      animate={{ opacity: [1, 0.1, 1] }}
                      transition={{ repeat: Infinity, duration: 1.2 }}
                    />
                    <span className="font-mono text-[10px] text-red-500 font-semibold">RECORDING</span>
                  </div>
                </div>
                {/* Vitals grid */}
                <div className="bg-white p-4 grid grid-cols-3 gap-3">
                  {VITALS_PREVIEW.map((v, i) => (
                    <LiveVitalCard key={v.label} {...v} delay={0.4 + i * 0.08} />
                  ))}
                </div>
                {/* Status footer */}
                <div className="px-4 py-2.5 bg-slate-50 border-t border-slate-200 flex items-center justify-between">
                  <span className="font-mono text-[10px] text-slate-400">AI Pipeline: Active · 8 stages running</span>
                  <div className="flex items-center gap-1.5">
                    <div className="w-1.5 h-1.5 rounded-full bg-emerald-500" />
                    <span className="font-mono text-[10px] text-emerald-600 font-semibold">All vitals nominal</span>
                  </div>
                </div>
              </div>

              {/* Floating badge */}
              <motion.div
                initial={{ opacity: 0, scale: 0.8, y: 10 }}
                animate={{ opacity: 1, scale: 1, y: 0 }}
                transition={{ delay: 1, duration: 0.4 }}
                className="absolute -bottom-3 -left-4 flex items-center gap-2 px-3.5 py-2.5 rounded-xl bg-white border border-slate-200"
                style={{ boxShadow: '0 8px 32px rgba(0,0,0,0.1)' }}
              >
                <ShieldCheck size={16} className="text-emerald-500" />
                <div>
                  <div className="font-display text-xs font-semibold text-slate-700">Medico-Legal Record</div>
                  <div className="font-display text-[10px] text-slate-400">Signed · Locked · Audit-trailed</div>
                </div>
              </motion.div>
            </motion.div>
          </div>
        </div>
      </section>

      {/* ── Stats Bar ────────────────────────────────────────────────────────── */}
      <section className="border-y border-slate-200 bg-white">
        <div className="max-w-7xl mx-auto px-6 py-10">
          <div className="grid grid-cols-2 md:grid-cols-4 gap-8">
            {[
              { value: '6+',      label: 'Vital Parameters',  color: '#1D4ED8' },
              { value: '< 100ms', label: 'Reading Interval',  color: '#16A34A' },
              { value: '100%',    label: 'Vendor Neutral',    color: '#7C3AED' },
              { value: 'Offline', label: 'No Cloud Required', color: '#D97706' },
            ].map(({ value, label, color }, i) => (
              <motion.div
                key={label}
                initial={{ opacity: 0, y: 16 }}
                whileInView={{ opacity: 1, y: 0 }}
                viewport={{ once: true }}
                transition={{ delay: i * 0.08, duration: 0.5 }}
                className="text-center"
              >
                <div className="font-heading font-bold text-3xl mb-1" style={{ color }}>{value}</div>
                <div className="font-display text-sm text-slate-500">{label}</div>
              </motion.div>
            ))}
          </div>
        </div>
      </section>

      {/* ── Features Grid ────────────────────────────────────────────────────── */}
      <section className="py-20 bg-clinical-bg">
        <div className="max-w-7xl mx-auto px-6">
          <div className="text-center mb-14">
            <motion.p
              initial={{ opacity: 0 }} whileInView={{ opacity: 1 }} viewport={{ once: true }}
              className="font-display text-sm font-semibold uppercase tracking-[0.2em] text-blue-600 mb-3"
            >
              Core Capabilities
            </motion.p>
            <motion.h2
              initial={{ opacity: 0, y: 16 }} whileInView={{ opacity: 1, y: 0 }} viewport={{ once: true }}
              transition={{ delay: 0.08 }}
              className="font-heading font-bold text-4xl text-slate-900 tracking-tight mb-4"
            >
              Everything you need for<br />accurate anaesthesia records
            </motion.h2>
            <motion.p
              initial={{ opacity: 0 }} whileInView={{ opacity: 1 }} viewport={{ once: true }}
              transition={{ delay: 0.14 }}
              className="font-display text-base text-slate-500 max-w-xl mx-auto"
            >
              VITAL handles detection, validation, review, and documentation — anaesthetists focus on patients.
            </motion.p>
          </div>
          <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-5">
            {FEATURES.map((f, i) => <FeatureCard key={f.title} {...f} delay={i * 0.07} />)}
          </div>
        </div>
      </section>

      {/* ── AI Pipeline ──────────────────────────────────────────────────────── */}
      <section className="py-20 bg-white" ref={pipelineRef}>
        <div className="max-w-7xl mx-auto px-6">
          <div className="text-center mb-14">
            <motion.p
              initial={{ opacity: 0 }} whileInView={{ opacity: 1 }} viewport={{ once: true }}
              className="font-display text-sm font-semibold uppercase tracking-[0.2em] text-purple-600 mb-3"
            >
              How It Works
            </motion.p>
            <motion.h2
              initial={{ opacity: 0, y: 16 }} whileInView={{ opacity: 1, y: 0 }} viewport={{ once: true }}
              transition={{ delay: 0.08 }}
              className="font-heading font-bold text-4xl text-slate-900 tracking-tight mb-4"
            >
              8-stage AI pipeline
            </motion.h2>
            <motion.p
              initial={{ opacity: 0 }} whileInView={{ opacity: 1 }} viewport={{ once: true }}
              transition={{ delay: 0.14 }}
              className="font-display text-base text-slate-500 max-w-lg mx-auto"
            >
              From raw camera frame to validated, signed digital record — entirely automated.
            </motion.p>
          </div>

          <div className="flex flex-wrap justify-center items-center gap-2.5">
            {PIPELINE_STEPS.map((step, i) => (
              <div key={step.label} className="flex items-center gap-2.5">
                <motion.div
                  initial={{ opacity: 0, scale: 0.8 }}
                  animate={pipelineInView ? { opacity: 1, scale: 1 } : {}}
                  transition={{ delay: i * 0.09, duration: 0.4, ease: [0.16, 1, 0.3, 1] }}
                  className="flex items-center gap-2.5 px-4 py-2.5 rounded-xl border bg-white"
                  style={{
                    borderColor: `${step.color}35`,
                    boxShadow: `0 1px 4px ${step.color}10, 0 0 0 1px ${step.color}18`,
                  }}
                >
                  <div className="w-7 h-7 rounded-lg flex items-center justify-center" style={{ background: `${step.color}18` }}>
                    <step.icon size={14} style={{ color: step.color }} />
                  </div>
                  <span className="font-display text-xs font-semibold text-slate-700 whitespace-nowrap">
                    {step.label}
                  </span>
                </motion.div>
                {i < PIPELINE_STEPS.length - 1 && (
                  <motion.div
                    initial={{ opacity: 0 }}
                    animate={pipelineInView ? { opacity: 1 } : {}}
                    transition={{ delay: i * 0.09 + 0.05 }}
                  >
                    <ChevronRight size={14} className="text-slate-300" />
                  </motion.div>
                )}
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ── Final CTA ────────────────────────────────────────────────────────── */}
      <section className="py-20 hero-gradient">
        <div className="max-w-4xl mx-auto px-6 text-center">
          <motion.h2
            initial={{ opacity: 0, y: 20 }} whileInView={{ opacity: 1, y: 0 }} viewport={{ once: true }}
            className="font-heading font-bold text-4xl text-slate-900 tracking-tight mb-4"
          >
            Ready to digitize your anaesthesia records?
          </motion.h2>
          <motion.p
            initial={{ opacity: 0 }} whileInView={{ opacity: 1 }} viewport={{ once: true }}
            transition={{ delay: 0.1 }}
            className="font-display text-lg text-slate-500 mb-10"
          >
            Set up in minutes. Works with any monitor, any hospital, completely offline.
          </motion.p>
          <motion.div
            initial={{ opacity: 0, y: 12 }} whileInView={{ opacity: 1, y: 0 }} viewport={{ once: true }}
            transition={{ delay: 0.18 }}
            className="flex flex-wrap gap-3 justify-center"
          >
            <motion.button
              onClick={handleStartNew}
              className="flex items-center gap-2.5 px-7 py-4 rounded-xl font-display font-semibold text-base text-white"
              style={{ background: 'linear-gradient(135deg, #1D4ED8, #1E40AF)', boxShadow: '0 4px 20px rgba(29,78,216,0.35)' }}
              whileHover={{ scale: 1.03, boxShadow: '0 6px 28px rgba(29,78,216,0.45)' }}
              whileTap={{ scale: 0.97 }}
            >
              Start New Case <ArrowRight size={18} />
            </motion.button>
            <motion.button
              onClick={() => navigate('/calibration')}
              className="flex items-center gap-2.5 px-7 py-4 rounded-xl font-display font-semibold text-base text-slate-700 bg-white border border-slate-200 hover:bg-slate-50 transition-all"
              style={{ boxShadow: '0 1px 4px rgba(0,0,0,0.06)' }}
              whileHover={{ scale: 1.02 }}
              whileTap={{ scale: 0.97 }}
            >
              <Camera size={16} /> Setup Camera
            </motion.button>
          </motion.div>
        </div>
      </section>

      {/* ── Footer ───────────────────────────────────────────────────────────── */}
      <footer className="border-t border-slate-200 bg-white py-8">
        <div className="max-w-7xl mx-auto px-6 flex items-center justify-between flex-wrap gap-4">
          <div className="flex items-center gap-2.5">
            <div className="w-7 h-7 rounded-lg flex items-center justify-center"
              style={{ background: 'linear-gradient(135deg, #1D4ED8, #7C3AED)' }}>
              <Activity size={14} className="text-white" />
            </div>
            <span className="font-heading font-bold text-sm text-slate-700">VITAL</span>
            <span className="font-display text-xs text-slate-400">v1.0.0 · Offline-First Medical AI</span>
          </div>
          <div className="flex items-center gap-5">
            {[
              { label: 'Archive',     path: '/archive' },
              { label: 'Calibration', path: '/calibration' },
              { label: 'OCR Debug',   path: '/ocr-debug' },
            ].map(({ label, path }) => (
              <button key={path} onClick={() => navigate(path)}
                className="font-display text-xs text-slate-400 hover:text-slate-600 transition-colors">
                {label}
              </button>
            ))}
          </div>
        </div>
      </footer>
    </div>
  );
}
