"use client";

import { Database } from "lucide-react";
import type { Tone } from "@/lib/council";
import type { RedisActivity } from "@/lib/types";
import { EmptyState, Panel, RailRow } from "./primitives";

function redisKindTone(kind?: string): Tone {
  switch ((kind ?? "").toLowerCase()) {
    case "warning":
      return "warning";
    case "error":
      return "risk";
    case "stream":
    case "pubsub":
    case "eval":
      return "positive";
    case "json":
    case "search":
    case "vector":
    case "tool":
      return "info";
    default:
      return "neutral";
  }
}

export function RedisActivityRail({ activity }: { activity: RedisActivity[] }) {
  const rows = [...activity].slice(-40).reverse();
  return (
    <Panel icon={Database} visualIcon="memory" eyebrow="System of record" title="Redis activity" count={activity.length} scroll>
      {rows.length === 0 ? (
        <EmptyState icon={Database} visualIcon="memory">RedisJSON, RediSearch, vector RAG, streams, and pub/sub writes appear here.</EmptyState>
      ) : (
        <div className="space-y-2">
          {rows.map((item, index) => (
            <RailRow
              key={`${item.label ?? item.kind}-${index}`}
              tone={redisKindTone(item.kind)}
              title={item.label ?? item.kind ?? "Redis"}
              detail={item.detail}
              meta={typeof item.at === "string" ? item.at : undefined}
            />
          ))}
        </div>
      )}
    </Panel>
  );
}
