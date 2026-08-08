// ─── Design Tokens ────────────────────────────────────────────────────────────
// Single source of truth. All Tailwind classes are derived from these values.

export const vitals = {
  hr:    { color: '#00FF88', dim: '#00994D', bg: 'rgba(0,255,136,0.08)',  glow: 'rgba(0,255,136,0.18)',  label: 'HR',    fullLabel: 'Heart Rate'   },
  spo2:  { color: '#00D4FF', dim: '#007A99', bg: 'rgba(0,212,255,0.08)',  glow: 'rgba(0,212,255,0.18)',  label: 'SpO₂',  fullLabel: 'Oxygen Sat.'  },
  nibp:  { color: '#FF4757', dim: '#99141F', bg: 'rgba(255,71,87,0.08)',  glow: 'rgba(255,71,87,0.18)',  label: 'NIBP',  fullLabel: 'Blood Press.' },
  etco2: { color: '#FFD600', dim: '#997F00', bg: 'rgba(255,214,0,0.08)',  glow: 'rgba(255,214,0,0.18)',  label: 'EtCO₂', fullLabel: 'End-tidal CO₂' },
  temp:  { color: '#FF9500', dim: '#995900', bg: 'rgba(255,149,0,0.08)',  glow: 'rgba(255,149,0,0.18)',  label: 'Temp',  fullLabel: 'Temperature'  },
  rr:    { color: '#BF5AF2', dim: '#6B2699', bg: 'rgba(191,90,242,0.08)', glow: 'rgba(191,90,242,0.18)', label: 'RR',    fullLabel: 'Resp. Rate'   },
} as const;

export type VitalKey = keyof typeof vitals;

export const semantic = {
  critical: { color: '#FF3B30', bg: 'rgba(255,59,48,0.1)',   border: 'rgba(255,59,48,0.4)',  text: '#FF6B62',  icon: '⊗' },
  warning:  { color: '#FF9500', bg: 'rgba(255,149,0,0.1)',  border: 'rgba(255,149,0,0.4)',  text: '#FFB340',  icon: '△' },
  info:     { color: '#32ADE6', bg: 'rgba(50,173,230,0.1)', border: 'rgba(50,173,230,0.4)', text: '#5AC8FA',  icon: 'ℹ' },
  success:  { color: '#30D158', bg: 'rgba(48,209,88,0.1)',  border: 'rgba(48,209,88,0.4)',  text: '#34C759',  icon: '✓' },
  neutral:  { color: '#7A90AA', bg: 'rgba(122,144,170,0.08)', border: 'rgba(122,144,170,0.2)', text: '#7A90AA', icon: '·' },
} as const;

export type SemanticKey = keyof typeof semantic;

export const surface = {
  bg:           '#070D1A',
  surface:      '#0C1625',
  card:         '#101C2E',
  cardHover:    '#142135',
  border:       '#182B42',
  borderBright: '#1E3A56',
  borderFocus:  '#32ADE6',
} as const;

export const textColors = {
  primary:   '#E8F1FF',
  secondary: '#7A90AA',
  muted:     '#3D5570',
  disabled:  '#1E3A56',
  inverse:   '#070D1A',
} as const;

export const lightMode = {
  bg:          '#F0F4F8',
  surface:     '#FFFFFF',
  card:        '#FFFFFF',
  elevated:    '#F8FAFC',
  border:      '#E2E8F0',
  borderBright:'#CBD5E1',
  text: {
    primary:   '#0F172A',
    secondary: '#475569',
    muted:     '#94A3B8',
  },
} as const;

// Motion constants
export const duration = {
  fast:    120,
  normal:  200,
  slow:    350,
} as const;

export const ease = {
  out:    [0, 0, 0.2, 1]      as const,
  in:     [0.4, 0, 1, 1]     as const,
  inOut:  [0.4, 0, 0.2, 1]   as const,
  spring: [0.16, 1, 0.3, 1]  as const,
} as const;

// Type helpers
export type Severity = 'critical' | 'warning' | 'info' | 'success' | 'neutral';
