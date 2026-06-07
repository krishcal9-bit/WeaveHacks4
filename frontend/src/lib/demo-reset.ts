import type { DemoResetResponse } from "@/lib/types";

export const DEMO_RESET_EVENT = "atlas:demo-reset";

export function broadcastDemoReset(detail?: DemoResetResponse) {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new CustomEvent<DemoResetResponse | undefined>(DEMO_RESET_EVENT, { detail }));
}

export function onDemoReset(handler: (detail?: DemoResetResponse) => void) {
  if (typeof window === "undefined") return () => undefined;

  const listener = (event: Event) => {
    handler((event as CustomEvent<DemoResetResponse | undefined>).detail);
  };
  window.addEventListener(DEMO_RESET_EVENT, listener);
  return () => window.removeEventListener(DEMO_RESET_EVENT, listener);
}
