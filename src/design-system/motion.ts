import type { Variants, Transition } from 'framer-motion';

// ─── Shared transitions ────────────────────────────────────────────────────────

export const tFast:   Transition = { duration: 0.12, ease: [0, 0, 0.2, 1] };
export const tNormal: Transition = { duration: 0.20, ease: [0, 0, 0.2, 1] };
export const tSpring: Transition = { duration: 0.32, ease: [0.16, 1, 0.3, 1] };
export const tBounce: Transition = { type: 'spring', stiffness: 450, damping: 22, mass: 0.8 };

// ─── Reusable variant sets ─────────────────────────────────────────────────────

export const fadeVariants: Variants = {
  hidden:  { opacity: 0 },
  visible: { opacity: 1, transition: tNormal },
  exit:    { opacity: 0, transition: tFast },
};

export const slideUpVariants: Variants = {
  hidden:  { opacity: 0, y: 10 },
  visible: { opacity: 1, y: 0,   transition: tSpring },
  exit:    { opacity: 0, y: 8,   transition: tFast   },
};

export const slideDownVariants: Variants = {
  hidden:  { opacity: 0, y: -10 },
  visible: { opacity: 1, y: 0,    transition: tSpring },
  exit:    { opacity: 0, y: -8,   transition: tFast   },
};

export const scaleVariants: Variants = {
  hidden:  { opacity: 0, scale: 0.94 },
  visible: { opacity: 1, scale: 1,    transition: tSpring },
  exit:    { opacity: 0, scale: 0.96, transition: tFast   },
};

export const slideRightVariants: Variants = {
  hidden:  { opacity: 0, x: '100%' },
  visible: { opacity: 1, x: 0, transition: { ...tSpring, duration: 0.36 } },
  exit:    { opacity: 0, x: '100%', transition: { duration: 0.22, ease: [0.4, 0, 1, 1] } },
};

export const slideLeftVariants: Variants = {
  hidden:  { opacity: 0, x: '-100%' },
  visible: { opacity: 1, x: 0, transition: { ...tSpring, duration: 0.36 } },
  exit:    { opacity: 0, x: '-100%', transition: { duration: 0.22, ease: [0.4, 0, 1, 1] } },
};

export const slideBottomVariants: Variants = {
  hidden:  { opacity: 0, y: '100%' },
  visible: { opacity: 1, y: 0, transition: { ...tSpring, duration: 0.36 } },
  exit:    { opacity: 0, y: '100%', transition: { duration: 0.22, ease: [0.4, 0, 1, 1] } },
};

export const stagger = (delay = 0.06): Variants => ({
  hidden:  {},
  visible: { transition: { staggerChildren: delay, delayChildren: 0.05 } },
});

export const listItemVariants: Variants = {
  hidden:  { opacity: 0, x: -8 },
  visible: { opacity: 1, x: 0, transition: tSpring },
  exit:    { opacity: 0, x: 8, transition: tFast   },
};

// ─── Interactive presets ───────────────────────────────────────────────────────

export const buttonMotion = {
  whileHover: { scale: 1.025 },
  whileTap:   { scale: 0.964 },
  transition: tFast,
};

export const cardMotion = {
  whileHover: { y: -2, transition: tFast },
};

export const iconMotion = {
  whileHover: { rotate: 10, scale: 1.1, transition: tFast },
};

export const numberVariants: Variants = {
  enter:  { opacity: 0, y: -8, filter: 'blur(4px)' },
  center: { opacity: 1, y: 0,  filter: 'blur(0px)', transition: tSpring },
  exit:   { opacity: 0, y: 8,  filter: 'blur(4px)', transition: tFast  },
};
