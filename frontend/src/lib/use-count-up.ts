"use client";

import { useEffect, useRef, useState } from "react";

/*
  Animate a numeral toward its target ONCE per value change (requestAnimationFrame,
  cubic ease-out, then stops). Event-driven, not continuous: when data settles,
  the compositor is idle. Honors prefers-reduced-motion by snapping on the first
  frame. All state writes happen inside rAF callbacks (never synchronously in the
  effect body).
*/
export function useCountUp(target: number | null | undefined, durationMs = 900): number | null {
  const [display, setDisplay] = useState<number | null>(target ?? null);
  const fromRef = useRef<number | null>(null);

  useEffect(() => {
    if (target == null || Number.isNaN(target)) {
      fromRef.current = null;
      return;
    }

    const reduced =
      typeof window !== "undefined" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const from = fromRef.current ?? target * 0.55; // first paint sweeps upward
    let raf = 0;
    const start = performance.now();

    const tick = (now: number) => {
      if (reduced || from === target) {
        fromRef.current = target;
        setDisplay(target);
        return;
      }
      const t = Math.min(1, (now - start) / durationMs);
      const eased = 1 - Math.pow(1 - t, 3);
      setDisplay(from + (target - from) * eased);
      if (t < 1) {
        raf = requestAnimationFrame(tick);
      } else {
        fromRef.current = target;
      }
    };

    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [target, durationMs]);

  return target == null || Number.isNaN(target) ? null : display;
}
