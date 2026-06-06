import type {
  CompanyFinancials,
  DecisionEvent,
  ObservabilitySnapshot,
  RosterMember,
  SponsorHealth,
  Vendor,
} from "./types";

const BASE = process.env.NEXT_PUBLIC_AGENT_URL || "http://localhost:8123";

async function getJSON<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`${path} → ${res.status}`);
  return res.json() as Promise<T>;
}

export const api = {
  company: () => getJSON<CompanyFinancials>("/api/company"),
  vendors: () => getJSON<Vendor[]>("/api/vendors"),
  decisions: () => getJSON<DecisionEvent[]>("/api/decisions"),
  roster: () => getJSON<RosterMember[]>("/api/roster"),
  health: () => getJSON<SponsorHealth>("/api/health"),
  observability: () => getJSON<ObservabilitySnapshot>("/api/observability"),
};
