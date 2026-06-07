"use client";

import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { useState, type ReactNode } from "react";
import { useMounted } from "@/lib/use-mounted";
import {
  appFromLanding,
  isAppTab,
  landingPage,
  pageDefault,
  reducedVariants,
  tabIndex,
  tabSlideVariants,
} from "@/components/motion/variants";

function resolveVariants(pathname: string, prev: string, reduced: boolean) {
  if (reduced) return reducedVariants;

  if (pathname === "/") return landingPage;

  const fromLanding = prev === "/" && pathname !== "/";
  if (fromLanding) return appFromLanding;

  const prevTab = tabIndex(prev);
  const nextTab = tabIndex(pathname);
  if (isAppTab(prev) && isAppTab(pathname) && prevTab >= 0 && nextTab >= 0 && prev !== pathname) {
    const direction = nextTab > prevTab ? 1 : nextTab < prevTab ? -1 : 0;
    return tabSlideVariants(direction);
  }

  return pageDefault;
}

type RouteState = { current: string; previous: string };

export function PageTransition({ pathname, children }: { pathname: string; children: ReactNode }) {
  const mounted = useMounted();
  const reduced = useReducedMotion();
  const [route, setRoute] = useState<RouteState>({ current: pathname, previous: pathname });

  if (route.current !== pathname) {
    setRoute({ previous: route.current, current: pathname });
  }

  const shouldReduce = reduced ?? false;
  const variants = resolveVariants(route.current, route.previous, shouldReduce);

  if (!mounted) {
    return <div className="min-h-full w-full">{children}</div>;
  }

  return (
    <div
      className="relative grid min-h-full w-full overflow-x-clip"
      data-route-transition
      data-route-current={route.current}
      data-route-previous={route.previous}
    >
      <AnimatePresence mode="sync" initial={false}>
        <motion.div
          key={route.current}
          className="min-h-full w-full"
          style={{
            gridArea: "1 / 1",
            transformOrigin: "50% 18%",
            willChange: shouldReduce ? "auto" : "opacity, transform",
          }}
          variants={variants}
          initial="initial"
          animate="animate"
          exit="exit"
        >
          {children}
        </motion.div>
      </AnimatePresence>
    </div>
  );
}
