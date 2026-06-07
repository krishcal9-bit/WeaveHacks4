"use client";

import { useSyncExternalStore } from "react";

/** True only on the client — server snapshot is always false to match SSR HTML. */
export function useMounted() {
  return useSyncExternalStore(
    () => () => {},
    () => true,
    () => false,
  );
}

/** Avoid SSR/client text mismatches when live health resolves before hydration. */
export function useDeferredHealthReady(healthReady: boolean) {
  const mounted = useMounted();
  return mounted && healthReady;
}
