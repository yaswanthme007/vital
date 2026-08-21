import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { motion, AnimatePresence } from 'framer-motion';
import { Activity, ChevronRight, User, Stethoscope, FileText, ArrowLeft, Camera } from 'lucide-react';
import { useSessionStore } from '@/store/sessionStore';
import { useToast } from '@/store/toastStore';
import { cn } from '@/lib/utils';
import type { SessionFormData, AsaClass } from '@/types/session';

const ASA_OPTIONS: { cls: AsaClass; label: string; color: string }[] = [
  { cls: 1, label: 'I — Healthy patient',                  color: '#16A34A' },
  { cls: 2, label: 'II — Mild systemic disease',           color: '#65A30D' },
  { cls: 3, label: 'III — Severe systemic disease',        color: '#D97706' },
  { cls: 4, label: 'IV — Life-threatening disease',        color: '#EA580C' },
  { cls: 5, label: 'V — Moribund patient',                 color: '#DC2626' },
  { cls: 6, label: 'VI — Brain-dead organ donor',         color: '#9F1239' },
];

const PROCEDURES = [
  'Laparoscopic Cholecystectomy',
  'Total Knee Replacement',
  'Appendectomy',
  'Caesarean Section',
  'Coronary Artery Bypass',
  'Craniotomy',
  'Spinal Fusion',
  'Hernia Repair',
  'Hysterectomy',
  'Thyroidectomy',
  'Other',
];

function FormField({
  label, icon, error, required, children, hint,
}: {
  label: string; icon?: React.ReactNode; error?: string; required?: boolean;
  children: React.ReactNode; hint?: string;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <label className="flex items-center gap-1.5 font-display text-sm font-semibold text-slate-700">
        {icon && <span className="text-slate-400">{icon}</span>}
        {label}
        {required && <span className="text-red-500 ml-0.5">*</span>}
        {error && (
          <span className="ml-auto font-normal text-xs text-red-500">{error}</span>
        )}
      </label>
      {children}
      {hint && !error && (
        <span className="font-display text-xs text-slate-400">{hint}</span>
      )}
    </div>
  );
}

export function StartPage() {
  const { startSession } = useSessionStore();
  const { toast } = useToast();
  const navigate = useNavigate();

  const [form, setForm] = useState<Partial<SessionFormData>>({ patientId: '', procedure: '', anesthetist: '' });
  const [asa, setAsa] = useState<AsaClass | null>(null);
  const [loading, setLoading] = useState(false);
  const [errors, setErrors] = useState<Partial<Record<keyof SessionFormData, string>>>({});
  const [step, setStep] = useState<1 | 2>(1);

  const update = (key: keyof SessionFormData, value: string | number) =>
    setForm(f => ({ ...f, [key]: value }));

  const validateStep1 = () => {
    const e: typeof errors = {};
    if (!form.patientId?.trim())   e.patientId   = 'Required';
    if (!form.procedure?.trim())   e.procedure   = 'Required';
    if (!form.anesthetist?.trim()) e.anesthetist = 'Required';
    setErrors(e);
    return Object.keys(e).length === 0;
  };

  const handleNext = () => {
    if (validateStep1()) setStep(2);
  };

  const handleStart = async () => {
    setLoading(true);
    try {
      await startSession({ ...(form as SessionFormData), asa: asa ?? undefined });
      // M5.7.1: camera calibration is mandatory before Active Operation --
      // see OperationPage's own guard, which redirects back here anyway if
      // this is ever bypassed. Routing straight to /calibration means the
      // operator never has to discover it manually.
      navigate('/calibration');
    } catch (err) {
      console.error('Failed to start session', err);
      toast.error('Could not start the case', {
        description: "Couldn't reach the backend — check it's running (docker compose up) and try again.",
      });
      setLoading(false);
    }
  };

  const inputCls = (err?: string) => cn(
    'w-full h-11 px-4 rounded-xl border font-display text-sm text-slate-800',
    'placeholder-slate-400 bg-white transition-all',
    'focus:outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500',
    err ? 'border-red-400 bg-red-50/30' : 'border-slate-200 hover:border-slate-300',
  );

  return (
    <div className="relative h-screen bg-clinical-bg flex flex-col items-center overflow-y-auto p-6 py-12">
      {/* Background gradient */}
      <div className="absolute inset-0 pointer-events-none overflow-hidden hero-gradient opacity-60" />

      {/* Back to landing */}
      <motion.button
        onClick={() => navigate('/landing')}
        className="absolute top-6 left-6 flex items-center gap-1.5 font-display text-sm text-slate-500 hover:text-slate-700 transition-colors"
        whileHover={{ x: -2 }}
      >
        <ArrowLeft size={15} />
        Back
      </motion.button>

      <motion.div
        className="w-full max-w-lg relative"
        initial={{ opacity: 0, y: 24 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.45, ease: [0.16, 1, 0.3, 1] }}
      >
        {/* Header */}
        <div className="flex flex-col items-center mb-8">
          <motion.div
            className="w-14 h-14 rounded-2xl flex items-center justify-center mb-5"
            style={{
              background: 'linear-gradient(135deg, #1D4ED8 0%, #7C3AED 100%)',
              boxShadow: '0 8px 32px rgba(29,78,216,0.35)',
            }}
            initial={{ scale: 0.8, opacity: 0 }}
            animate={{ scale: 1, opacity: 1 }}
            transition={{ delay: 0.1, duration: 0.4, ease: [0.16, 1, 0.3, 1] }}
          >
            <Activity size={28} className="text-white" />
          </motion.div>
          <h1 className="font-heading font-bold text-3xl text-slate-900 tracking-tight">New Case</h1>
          <p className="font-display text-sm text-slate-500 mt-1">
            VITAL · Smart Anaesthesia Documentation
          </p>

          {/* Step indicators */}
          <div className="flex items-center gap-2 mt-5">
            {[1, 2].map(s => (
              <div key={s} className="flex items-center gap-2">
                <motion.div
                  className="flex items-center justify-center w-7 h-7 rounded-full font-display text-xs font-bold"
                  animate={{
                    background: step >= s ? '#1D4ED8' : '#E2E8F0',
                    color: step >= s ? '#FFFFFF' : '#94A3B8',
                  }}
                  transition={{ duration: 0.25 }}
                >
                  {s}
                </motion.div>
                {s < 2 && <div className={cn('w-10 h-0.5 rounded-full transition-colors duration-300', step > s ? 'bg-blue-500' : 'bg-slate-200')} />}
              </div>
            ))}
          </div>
          <div className="flex gap-16 mt-1.5">
            <span className="font-display text-[10px] text-slate-400">Case Details</span>
            <span className="font-display text-[10px] text-slate-400">Patient Info</span>
          </div>
        </div>

        {/* Form card */}
        <div
          className="rounded-2xl border border-slate-200 bg-white overflow-hidden"
          style={{ boxShadow: '0 8px 32px rgba(0,0,0,0.08)' }}
        >
          <AnimatePresence mode="wait">
            {step === 1 ? (
              <motion.div
                key="step1"
                initial={{ opacity: 0, x: -20 }}
                animate={{ opacity: 1, x: 0 }}
                exit={{ opacity: 0, x: -20 }}
                transition={{ duration: 0.28, ease: [0.16, 1, 0.3, 1] }}
              >
                <div className="px-6 pt-6 pb-5 border-b border-slate-100">
                  <h2 className="font-heading font-bold text-lg text-slate-900">Case Details</h2>
                  <p className="font-display text-sm text-slate-500 mt-0.5">
                    Enter the procedure and responsible clinician
                  </p>
                </div>
                <div className="px-6 py-6 space-y-5">
                  <FormField label="Patient ID" icon={<User size={14} />} required error={errors.patientId}>
                    <input type="text" placeholder="e.g. PT-2024-001"
                      value={form.patientId ?? ''}
                      onChange={e => { update('patientId', e.target.value); setErrors(p => ({ ...p, patientId: undefined })); }}
                      className={inputCls(errors.patientId)} />
                  </FormField>

                  <FormField label="Procedure" icon={<FileText size={14} />} required error={errors.procedure}>
                    <select
                      value={form.procedure ?? ''}
                      onChange={e => { update('procedure', e.target.value); setErrors(p => ({ ...p, procedure: undefined })); }}
                      className={cn(inputCls(errors.procedure), 'cursor-pointer appearance-none')}
                    >
                      <option value="" disabled>Select procedure…</option>
                      {PROCEDURES.map(p => <option key={p} value={p}>{p}</option>)}
                    </select>
                  </FormField>

                  <FormField label="Lead Anaesthetist" icon={<Stethoscope size={14} />} required error={errors.anesthetist}>
                    <input type="text" placeholder="Dr. Full Name"
                      value={form.anesthetist ?? ''}
                      onChange={e => { update('anesthetist', e.target.value); setErrors(p => ({ ...p, anesthetist: undefined })); }}
                      className={inputCls(errors.anesthetist)} />
                  </FormField>
                </div>
                <div className="px-6 pb-6 flex gap-3">
                  <motion.button
                    onClick={() => navigate('/calibration')}
                    className="flex items-center gap-2 px-4 py-2.5 rounded-xl border border-slate-200 font-display text-sm font-medium text-slate-600 hover:bg-slate-50 transition-colors"
                    whileTap={{ scale: 0.97 }}
                  >
                    <Camera size={14} />
                    Setup Camera
                  </motion.button>
                  <motion.button
                    onClick={handleNext}
                    className="flex-1 flex items-center justify-center gap-2 py-2.5 rounded-xl font-display text-sm font-semibold text-white"
                    style={{ background: 'linear-gradient(135deg, #1D4ED8, #1E40AF)' }}
                    whileHover={{ scale: 1.01, boxShadow: '0 4px 16px rgba(29,78,216,0.3)' }}
                    whileTap={{ scale: 0.98 }}
                  >
                    Next
                    <ChevronRight size={15} />
                  </motion.button>
                </div>
              </motion.div>
            ) : (
              <motion.div
                key="step2"
                initial={{ opacity: 0, x: 20 }}
                animate={{ opacity: 1, x: 0 }}
                exit={{ opacity: 0, x: 20 }}
                transition={{ duration: 0.28, ease: [0.16, 1, 0.3, 1] }}
              >
                <div className="px-6 pt-6 pb-5 border-b border-slate-100">
                  <h2 className="font-heading font-bold text-lg text-slate-900">Patient Information</h2>
                  <p className="font-display text-sm text-slate-500 mt-0.5">
                    Optional demographic details
                  </p>
                </div>
                <div className="px-6 py-6 space-y-5">
                  <div className="flex gap-3">
                    <FormField label="Age (yrs)">
                      <input type="number" placeholder="—" min={0} max={120}
                        value={form.patientAge ?? ''}
                        onChange={e => update('patientAge', Number(e.target.value))}
                        className={inputCls()} />
                    </FormField>
                    <FormField label="Weight (kg)">
                      <input type="number" placeholder="—" min={0} max={500}
                        value={form.patientWeight ?? ''}
                        onChange={e => update('patientWeight', Number(e.target.value))}
                        className={inputCls()} />
                    </FormField>
                  </div>

                  {/* ASA */}
                  <div>
                    <label className="font-display text-sm font-semibold text-slate-700 block mb-3">
                      ASA Physical Status
                    </label>
                    <div className="grid grid-cols-2 gap-2">
                      {ASA_OPTIONS.map(({ cls, label, color }) => (
                        <motion.button
                          key={cls}
                          onClick={() => setAsa(cls === asa ? null : cls)}
                          whileHover={{ scale: 1.02 }}
                          whileTap={{ scale: 0.97 }}
                          className="flex items-center gap-2.5 px-3 py-2.5 rounded-xl border text-left transition-all"
                          style={cls === asa ? {
                            background: `${color}12`,
                            borderColor: `${color}60`,
                            color,
                          } : {
                            background: '#FFFFFF',
                            borderColor: '#E2E8F0',
                            color: '#64748B',
                          }}
                        >
                          <div
                            className="w-6 h-6 rounded-full flex items-center justify-center flex-shrink-0 font-heading font-bold text-xs"
                            style={{ background: cls === asa ? color : '#F1F5F9', color: cls === asa ? '#fff' : '#94A3B8' }}
                          >
                            {cls}
                          </div>
                          <span className="font-display text-xs leading-tight">{label}</span>
                        </motion.button>
                      ))}
                    </div>
                  </div>
                </div>
                <div className="px-6 pb-6 flex gap-3">
                  <motion.button
                    onClick={() => setStep(1)}
                    className="flex items-center gap-2 px-4 py-2.5 rounded-xl border border-slate-200 font-display text-sm font-medium text-slate-600 hover:bg-slate-50 transition-colors"
                    whileTap={{ scale: 0.97 }}
                  >
                    <ArrowLeft size={14} />
                    Back
                  </motion.button>
                  <motion.button
                    onClick={handleStart}
                    disabled={loading}
                    className="flex-1 flex items-center justify-center gap-2 py-2.5 rounded-xl font-display text-sm font-semibold text-white disabled:opacity-75"
                    style={{ background: 'linear-gradient(135deg, #16A34A, #15803D)', boxShadow: loading ? 'none' : '0 4px 16px rgba(22,163,74,0.3)' }}
                    whileHover={loading ? {} : { scale: 1.01, boxShadow: '0 6px 20px rgba(22,163,74,0.4)' }}
                    whileTap={loading ? {} : { scale: 0.98 }}
                  >
                    {loading ? (
                      <motion.div animate={{ rotate: 360 }} transition={{ repeat: Infinity, duration: 0.8, ease: 'linear' }}>
                        <Activity size={16} />
                      </motion.div>
                    ) : <Activity size={16} />}
                    {loading ? 'Starting session…' : 'Begin Monitoring'}
                  </motion.button>
                </div>
              </motion.div>
            )}
          </AnimatePresence>
        </div>

        <p className="text-center font-display text-xs text-slate-400 mt-5">
          All data processed offline · No network required · VITAL v1.0.0
        </p>
      </motion.div>
    </div>
  );
}
