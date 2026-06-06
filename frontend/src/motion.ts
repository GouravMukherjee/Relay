// Shared Framer Motion vocabulary so every view animates consistently:
// scroll-triggered fades, staggered reveals, and smooth hover/press transitions.

import type { Transition, Variants } from "framer-motion";

// A refined ease — quick out, gentle settle. Used for all fades/reveals.
export const easeOut: [number, number, number, number] = [0.22, 0.61, 0.36, 1];

export const spring: Transition = { type: "spring", stiffness: 380, damping: 30 };
export const springSoft: Transition = { type: "spring", stiffness: 260, damping: 26 };

// ── Reveal variants ───────────────────────────────────────────────────────────
export const fadeUp: Variants = {
  hidden: { opacity: 0, y: 18 },
  show: { opacity: 1, y: 0, transition: { duration: 0.5, ease: easeOut } },
};

export const fadeIn: Variants = {
  hidden: { opacity: 0 },
  show: { opacity: 1, transition: { duration: 0.45, ease: easeOut } },
};

// Parent that staggers its children's `show` state.
export const staggerParent = (stagger = 0.07, delay = 0.04): Variants => ({
  hidden: {},
  show: { transition: { staggerChildren: stagger, delayChildren: delay } },
});

// A child of a stagger parent (or standalone reveal).
export const item: Variants = {
  hidden: { opacity: 0, y: 14 },
  show: { opacity: 1, y: 0, transition: { duration: 0.42, ease: easeOut } },
};

export const itemLeft: Variants = {
  hidden: { opacity: 0, x: -16 },
  show: { opacity: 1, x: 0, transition: { duration: 0.42, ease: easeOut } },
};

// Viewport config for scroll-triggered reveals (fire once, slightly early).
export const inView = { once: true, amount: 0.2 as const, margin: "0px 0px -8% 0px" };

// ── Hover / press presets (spread onto any motion element) ────────────────────
export const hoverLift = {
  whileHover: { y: -3, transition: spring },
  whileTap: { scale: 0.99 },
};

export const hoverCard = {
  whileHover: { y: -4, transition: spring },
};

export const pressable = {
  whileHover: { scale: 1.025, transition: { duration: 0.15 } },
  whileTap: { scale: 0.96 },
};

export const iconHover = {
  whileHover: { scale: 1.12, transition: { duration: 0.15 } },
  whileTap: { scale: 0.9 },
};
