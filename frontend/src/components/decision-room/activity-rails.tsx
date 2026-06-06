"use client";

import { Database, Radio } from "lucide-react";
import type { Tone } from "@/lib/council";
import type { ObservabilityEvent, RedisActivity } from "@/lib/types";
import { EmptyState, Panel, RailRow } from "./primitives";

const EVENT_TONES = new Set<Tone>(["positive", "warning", "risk", "info", "neutral"]);

function eventTone(tone?: string): Tone {
  return tone && EVENT_TONES.has(tone as Tone) ? (tone as Tone) : "info";
}

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

export function SponsorEventRail({ events }: { events: ObservabilityEvent[] }) {
  const rows = [...events].slice(-40).reverse();
  return (
    <Panel icon={Radio} eyebrow="Sponsor signals" title="Event rail" count={events.length} scroll>
      {rows.length === 0 ? (
        <EmptyState icon={Radio}>OpenAI, W&B Weave, Redis, and CopilotKit signals stream here during a run.</EmptyState>
      ) : (
        <div className="space-y-2">
          {rows.map((event, index) => (
            <RailRow
              key={event.id ?? event._id ?? `${event.label}-${index}`}
              tone={eventTone(event.tone)}
              title={`${event.sponsor ?? "Atlas"} · ${event.label ?? event.event ?? event.title ?? "Event"}`}
              detail={event.detail ?? event.summary ?? event.status ?? undefined}
              meta={event.at ?? event.timestamp}
            />
          ))}
        </div>
      )}
    </Panel>
  );
}

export function RedisActivityRail({ activity }: { activity: RedisActivity[] }) {
  const rows = [...activity].slice(-40).reverse();
  return (
    <Panel icon={Database} eyebrow="System of record" title="Redis activity" count={activity.length} scroll>
      {rows.length === 0 ? (
        <EmptyState icon={Database}>RedisJSON, RediSearch, vector RAG, streams, and pub/sub writes appear here.</EmptyState>
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
