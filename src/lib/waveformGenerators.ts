// All generators return values in [0, 1] unless noted.
// phase: 0–1, represents position within one cycle.

export function ecgPoint(phase: number): number {
  const p = ((phase % 1) + 1) % 1;

  // P wave
  if (p >= 0.04 && p < 0.20) {
    const t = (p - 0.04) / 0.16;
    return 0.18 * Math.sin(Math.PI * t);
  }
  // PR interval
  if (p >= 0.20 && p < 0.33) return 0;
  // Q dip
  if (p >= 0.33 && p < 0.37) {
    const t = (p - 0.33) / 0.04;
    return -0.12 * Math.sin(Math.PI * t);
  }
  // R spike (ascending)
  if (p >= 0.37 && p < 0.415) {
    const t = (p - 0.37) / 0.045;
    return t;
  }
  // R spike (descending)
  if (p >= 0.415 && p < 0.46) {
    const t = (p - 0.415) / 0.045;
    return 1 - t;
  }
  // S dip
  if (p >= 0.46 && p < 0.50) {
    const t = (p - 0.46) / 0.04;
    return -0.28 * Math.sin(Math.PI * t);
  }
  // ST segment
  if (p >= 0.50 && p < 0.60) return 0.01;
  // T wave
  if (p >= 0.60 && p < 0.82) {
    const t = (p - 0.60) / 0.22;
    return 0.33 * Math.sin(Math.PI * t);
  }
  return 0;
}

export function plethPoint(phase: number): number {
  const p = ((phase % 1) + 1) % 1;

  if (p < 0.26) {
    // Rapid systolic upstroke
    const t = p / 0.26;
    return Math.pow(Math.sin((Math.PI / 2) * t), 0.7);
  } else if (p < 0.44) {
    // Systolic decline toward dicrotic notch
    const t = (p - 0.26) / 0.18;
    return 1 - 0.15 * t;
  } else if (p < 0.52) {
    // Dicrotic notch
    const t = (p - 0.44) / 0.08;
    return 0.85 - 0.12 * Math.sin(Math.PI * t);
  } else if (p < 0.66) {
    // Diastolic hump
    const t = (p - 0.52) / 0.14;
    return 0.85 - 0.09 * t;
  } else {
    // Diastolic descent
    const t = (p - 0.66) / 0.34;
    return 0.76 * Math.pow(1 - t, 1.6);
  }
}

export function capnoPoint(phase: number): number {
  const p = ((phase % 1) + 1) % 1;

  if (p < 0.32) {
    // Inspiration — baseline (near zero)
    return 0.02;
  } else if (p < 0.44) {
    // Expiratory upstroke (Phase II)
    const t = (p - 0.32) / 0.12;
    return 0.02 + 0.96 * (1 - Math.exp(-6 * t));
  } else if (p < 0.82) {
    // Alveolar plateau (Phase III) — slight upslope
    const t = (p - 0.44) / 0.38;
    return 0.96 + 0.04 * t;
  } else {
    // Inspiratory downstroke (Phase IV)
    const t = (p - 0.82) / 0.18;
    return 1.0 - t;
  }
}

export function addNoise(value: number, amplitude = 0.015): number {
  return value + (Math.random() - 0.5) * amplitude;
}
