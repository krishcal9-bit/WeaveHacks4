import type { Transition, Variants } from "motion/react";

/*
  Atlas motion vocabulary
  - reveal: one-time entrance and small content swaps.
  - route: directional page changes between primary app tabs.
  - feedback: command/result/status changes that need to feel immediate.
  - continuous: live system pulses, shimmers, and voice/Redis activity.
  - hover: never changes layout. Prefer y/opacity/shadow over scale on text
    controls so neighboring buttons do not visually collide.
  - reduced: opacity-only or instant transitions, with no loops.
*/
export const motionDuration = {
  instant: 0.01,
  fast: 0.16,
  quick: 0.2,
  normal: 0.28,
  reveal: 0.36,
  routeEnter: 0.26,
  routeExit: 0.18,
  emphasis: 0.52,
  count: 0.9,
} as const;

export const motionDelay = {
  child: 0.04,
  childFast: 0.025,
  route: 0.04,
} as const;

export const EASE_OUT_EXPO: Transition["ease"] = [0.22, 1, 0.36, 1];
export const EASE_IN_OUT: Transition["ease"] = [0.45, 0, 0.22, 1];

export const transitionFadeFast: Transition = { duration: motionDuration.fast, ease: EASE_OUT_EXPO };
export const transitionFade: Transition = { duration: motionDuration.quick, ease: EASE_OUT_EXPO };
export const transitionReveal: Transition = { duration: motionDuration.reveal, ease: EASE_OUT_EXPO };
export const transitionEmphasis: Transition = { duration: motionDuration.emphasis, ease: EASE_OUT_EXPO };
export const transitionReduced: Transition = { duration: motionDuration.instant };

export const springSnappy = { type: "spring" as const, stiffness: 420, damping: 34, mass: 0.85 };
export const springSoft = { type: "spring" as const, stiffness: 260, damping: 28, mass: 0.9 };
export const springBar = { type: "spring" as const, stiffness: 140, damping: 22, mass: 0.8 };

export const hoverLift = { y: -2 } as const;
export const hoverLiftStrong = { y: -3 } as const;
export const hoverNudge = { x: 2 } as const;
export const pressTap = { scale: 0.985 } as const;
export const pressSubtle = { scale: 0.995 } as const;

export function staggerDelay(index: number, step: number = motionDelay.child, max: number = 0.18): number {
  return Math.min(index * step, max);
}

export function whenReduced(reduced: boolean, transition: Transition): Transition {
  return reduced ? transitionReduced : transition;
}

export const fade: Variants = {
  hidden: { opacity: 0 },
  show: { opacity: 1, transition: transitionFade },
};

export const fadeUp: Variants = {
  hidden: { opacity: 0, y: 14 },
  show: {
    opacity: 1,
    y: 0,
    transition: transitionReveal,
  },
};

/* No `filter: blur()` in any variant: animating blur forces full repaints per
   frame, which is exactly what made the live Decision Room lag. Transform and
   opacity only. */
export const fadeDown: Variants = {
  hidden: { opacity: 0, y: -14 },
  show: {
    opacity: 1,
    y: 0,
    transition: transitionReveal,
  },
};

export const scaleIn: Variants = {
  hidden: { opacity: 0, scale: 0.94 },
  show: {
    opacity: 1,
    scale: 1,
    transition: transitionEmphasis,
  },
};

export const staggerContainer: Variants = {
  hidden: {},
  show: {
    transition: { staggerChildren: 0.07, delayChildren: motionDelay.child },
  },
};

export const staggerFast: Variants = {
  hidden: {},
  show: {
    transition: { staggerChildren: motionDelay.child, delayChildren: 0.02 },
  },
};

export const transcriptTurn: Variants = {
  hidden: { opacity: 0, y: 10, x: -8, scale: 0.985 },
  show: {
    opacity: 1,
    y: 0,
    x: 0,
    scale: 1,
    transition: transitionReveal,
  },
};

export const councilOrb: Variants = {
  hidden: { opacity: 0, scale: 0.82 },
  show: {
    opacity: 1,
    scale: 1,
    transition: { type: "spring", stiffness: 280, damping: 24, mass: 0.9 },
  },
};

export const influenceReveal: Variants = {
  hidden: { opacity: 0, y: 8, scale: 0.98 },
  show: {
    opacity: 1,
    y: 0,
    scale: 1,
    transition: transitionReveal,
  },
};

export const ROUTE_ENTER: Transition = { duration: motionDuration.routeEnter, ease: EASE_OUT_EXPO };
export const ROUTE_EXIT: Transition = { duration: motionDuration.routeExit, ease: EASE_IN_OUT };
const ROUTE_OFFSET = 14;

export function tabSlideVariants(direction: number): Variants {
  const x = direction === 0 ? 0 : direction > 0 ? ROUTE_OFFSET : -ROUTE_OFFSET;
  return {
    initial: { opacity: 0, x, y: 2, scale: 0.998, zIndex: 1 },
    animate: {
      opacity: 1,
      x: 0,
      y: 0,
      scale: 1,
      zIndex: 1,
      transition: ROUTE_ENTER,
    },
    exit: {
      opacity: 0,
      x: -x * 0.45,
      y: 0,
      scale: 0.999,
      zIndex: 0,
      transition: ROUTE_EXIT,
    },
  };
}

export const landingPage: Variants = {
  initial: { opacity: 0, y: -4, scale: 0.999, zIndex: 1 },
  animate: { opacity: 1, y: 0, scale: 1, zIndex: 1, transition: ROUTE_ENTER },
  exit: {
    opacity: 0,
    y: -6,
    scale: 0.999,
    zIndex: 0,
    transition: ROUTE_EXIT,
  },
};

export const appFromLanding: Variants = {
  initial: { opacity: 0, y: 12, scale: 0.998, zIndex: 1 },
  animate: {
    opacity: 1,
    y: 0,
    scale: 1,
    zIndex: 1,
    transition: ROUTE_ENTER,
  },
  exit: {
    opacity: 0,
    y: -4,
    scale: 0.999,
    zIndex: 0,
    transition: ROUTE_EXIT,
  },
};

export const pageDefault: Variants = {
  initial: { opacity: 0, y: 6, scale: 0.999, zIndex: 1 },
  animate: {
    opacity: 1,
    y: 0,
    scale: 1,
    zIndex: 1,
    transition: ROUTE_ENTER,
  },
  exit: {
    opacity: 0,
    y: -3,
    scale: 0.999,
    zIndex: 0,
    transition: ROUTE_EXIT,
  },
};

export const reducedVariants: Variants = {
  initial: { opacity: 0, x: 0, y: 0, scale: 1 },
  animate: { opacity: 1, x: 0, y: 0, scale: 1, transition: transitionReduced },
  exit: { opacity: 0, x: 0, y: 0, scale: 1, transition: transitionReduced },
};

export function tabIndex(pathname: string): number {
  if (pathname.startsWith("/overview")) return 0;
  if (pathname.startsWith("/dashboard")) return 1;
  if (pathname.startsWith("/decisions")) return 2;
  if (pathname.startsWith("/activity")) return 3;
  if (pathname.startsWith("/department")) return 4;
  if (pathname.startsWith("/settings")) return 5;
  return -1;
}

export function isAppTab(pathname: string): boolean {
  return tabIndex(pathname) >= 0;
}
