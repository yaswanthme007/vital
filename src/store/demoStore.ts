import { create } from 'zustand';
import type { VitalReading } from '@/types/vitals';

export type ScenarioId =
  | 'normal'
  | 'hypotension'
  | 'tachycardia'
  | 'desaturation'
  | 'hypercapnia'
  | 'camera_glare';

export interface DemoScenario {
  id: ScenarioId;
  label: string;
  description: string;
  color: string;
  icon: string;
  vitals: Omit<VitalReading, 'timestamp'>;
  ocrConfidenceDeg?: number; // 0–1, how much to degrade OCR confidence display
}

export const DEMO_SCENARIOS: DemoScenario[] = [
  {
    id: 'normal',
    label: 'Normal Surgery',
    description: 'Stable vitals, all within range',
    color: '#00FF88',
    icon: '✓',
    vitals: { hr: 72, spo2: 98, nibpSystolic: 120, nibpDiastolic: 78, nibpMean: 92, etco2: 38, temp: 36.8, rr: 14 },
  },
  {
    id: 'hypotension',
    label: 'Hypotension',
    description: 'BP critically low — active alert',
    color: '#FF4757',
    icon: '↓',
    vitals: { hr: 108, spo2: 96, nibpSystolic: 78, nibpDiastolic: 44, nibpMean: 55, etco2: 33, temp: 36.4, rr: 20 },
  },
  {
    id: 'tachycardia',
    label: 'High Heart Rate',
    description: 'HR > 120, warning threshold crossed',
    color: '#FF9500',
    icon: '↑',
    vitals: { hr: 132, spo2: 97, nibpSystolic: 145, nibpDiastolic: 90, nibpMean: 108, etco2: 42, temp: 37.3, rr: 22 },
  },
  {
    id: 'desaturation',
    label: 'Low SpO₂',
    description: 'SpO₂ 88% — critical desaturation',
    color: '#00D4FF',
    icon: '⊗',
    vitals: { hr: 96, spo2: 88, nibpSystolic: 116, nibpDiastolic: 74, nibpMean: 88, etco2: 35, temp: 36.9, rr: 24 },
  },
  {
    id: 'hypercapnia',
    label: 'High EtCO₂',
    description: 'EtCO₂ 62 — hypoventilation',
    color: '#FFD600',
    icon: '△',
    vitals: { hr: 86, spo2: 95, nibpSystolic: 130, nibpDiastolic: 84, nibpMean: 99, etco2: 62, temp: 37.1, rr: 7 },
  },
  {
    id: 'camera_glare',
    label: 'Camera Glare',
    description: 'OCR confidence degraded — reflections',
    color: '#BF5AF2',
    icon: '◎',
    vitals: { hr: 74, spo2: 97, nibpSystolic: 122, nibpDiastolic: 80, nibpMean: 94, etco2: 38, temp: 36.7, rr: 14 },
    ocrConfidenceDeg: 0.4,
  },
];

interface DemoState {
  active: boolean;
  scenarioId: ScenarioId | null;
  activate: (id: ScenarioId) => void;
  deactivate: () => void;
}

export const useDemoStore = create<DemoState>((set) => ({
  active: false,
  scenarioId: null,
  activate: (id) => set({ active: true, scenarioId: id }),
  deactivate: () => set({ active: false, scenarioId: null }),
}));
