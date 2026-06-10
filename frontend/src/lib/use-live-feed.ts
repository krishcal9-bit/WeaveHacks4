"use client";

import { useEffect, useRef } from "react";
import { agentBase } from "@/lib/agent-base";

export type LiveFeedEvent = { kind: string; payload: Record<string, unknown> };

/*
  Subscribe to the agent's SSE bridge (GET /api/live → Redis pub/sub
  `atlas:dashboard`). The browser's EventSource auto-reconnects; the handler
  ref keeps the subscription stable across re-renders so we never churn
  connections during streaming UI updates.
*/
export function useLiveFeed(kinds: readonly string[], onEvent: (event: LiveFeedEvent) => void): void {
  const handlerRef = useRef(onEvent);
  useEffect(() => {
    handlerRef.current = onEvent;
  }, [onEvent]);

  const kindsKey = kinds.join(",");

  useEffect(() => {
    if (typeof window === "undefined" || typeof EventSource === "undefined") return;
    const source = new EventSource(`${agentBase()}/api/live`);
    const listeners = kindsKey
      .split(",")
      .filter(Boolean)
      .map((kind) => {
        const listener = (event: MessageEvent) => {
          let payload: Record<string, unknown> = {};
          try {
            payload = JSON.parse(String(event.data)) as Record<string, unknown>;
          } catch {
            payload = {};
          }
          handlerRef.current({ kind, payload });
        };
        source.addEventListener(kind, listener);
        return { kind, listener };
      });

    return () => {
      for (const { kind, listener } of listeners) source.removeEventListener(kind, listener);
      source.close();
    };
  }, [kindsKey]);
}
