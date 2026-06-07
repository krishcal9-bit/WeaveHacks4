import type { Transition, Variants } from "motion/react";

export const EASE_OUT_EXPO: Transition["ease"] = [0.22, 1, 0.36, 1];
export const EASE_IN_OUT: Transition["ease"] = [0.45, 0, 0.22, 1];

export const springSnappy = { type: "spring" as const, stiffness: 420, damping: 34, mass: 0.85 };
export const springSoft = { type: "spring" as const, stiffness: 260, damping: 28, mass: 0.9 };
export const springBar = { type: "spring" as const, stiffness: 140, damping: 22, mass: 0.8 };

export const fade: Variants = {
  hidden: { opacity: 0 },
  show: { opacity: 1, transition: { duration: 0.25 } },
};

export const fadeUp: Variants = {
  hidden: { opacity: 0, y: 18, filter: "blur(6px)" },
  show: {
    opacity: 1,
    y: 0,
    filter: "blur(0px)",
    transition: { duration: 0.45, ease: EASE_OUT_EXPO },
  },
};

export const fadeDown: Variants = {
  hidden: { opacity: 0, y: -14, filter: "blur(4px)" },
  show: {
    opacity: 1,
    y: 0,
    filter: "blur(0px)",
    transition: { duration: 0.4, ease: EASE_OUT_EXPO },
  },
};

export const scaleIn: Variants = {
  hidden: { opacity: 0, scale: 0.94, filter: "blur(8px)" },
  show: {
    opacity: 1,
    scale: 1,
    filter: "blur(0px)",
    transition: { duration: 0.5, ease: EASE_OUT_EXPO },
  },
};

export const staggerContainer: Variants = {
  hidden: {},
  show: {
    transition: { staggerChildren: 0.07, delayChildren: 0.04 },
  },
};

export const staggerFast: Variants = {
  hidden: {},
  show: {
    transition: { staggerChildren: 0.04, delayChildren: 0.02 },
  },
};

export function tabSlideVariants(direction: number): Variants {
  const x = direction === 0 ? 0 : direction > 0 ? 28 : -28;
  return {
    initial: { opacity: 0, x, filter: "blur(5px)" },
    animate: {
      opacity: 1,
      x: 0,
      filter: "blur(0px)",
      transition: { duration: 0.38, ease: EASE_OUT_EXPO },
    },
    exit: {
      opacity: 0,
      x: -x * 0.6,
      filter: "blur(4px)",
      transition: { duration: 0.28, ease: EASE_IN_OUT },
    },
  };
}

export const landingPage: Variants = {
  initial: { opacity: 0 },
  animate: { opacity: 1, transition: { duration: 0.35, ease: EASE_OUT_EXPO } },
  exit: {
    opacity: 0,
    scale: 1.03,
    y: -24,
    filter: "blur(10px)",
    transition: { duration: 0.45, ease: EASE_IN_OUT },
  },
};

export const appFromLanding: Variants = {
  initial: { opacity: 0, y: 32, scale: 0.97, filter: "blur(12px)" },
  animate: {
    opacity: 1,
    y: 0,
    scale: 1,
    filter: "blur(0px)",
    transition: { duration: 0.55, ease: EASE_OUT_EXPO },
  },
  exit: {
    opacity: 0,
    y: -12,
    filter: "blur(6px)",
    transition: { duration: 0.3, ease: EASE_IN_OUT },
  },
};

export const pageDefault: Variants = {
  initial: { opacity: 0, y: 14, filter: "blur(4px)" },
  animate: {
    opacity: 1,
    y: 0,
    filter: "blur(0px)",
    transition: { duration: 0.4, ease: EASE_OUT_EXPO },
  },
  exit: {
    opacity: 0,
    y: -10,
    filter: "blur(3px)",
    transition: { duration: 0.28, ease: EASE_IN_OUT },
  },
};

export const reducedVariants: Variants = {
  initial: { opacity: 0 },
  animate: { opacity: 1, transition: { duration: 0.01 } },
  exit: { opacity: 0, transition: { duration: 0.01 } },
};

export function tabIndex(pathname: string): number {
  if (pathname.startsWith("/dashboard")) return 0;
  if (pathname.startsWith("/decisions")) return 1;
  if (pathname.startsWith("/settings")) return 2;
  if (pathname.startsWith("/activity")) return 3;
  if (pathname.startsWith("/department")) return 4;
  return -1;
}

export function isAppTab(pathname: string): boolean {
  return tabIndex(pathname) >= 0;
}
