/** Browser calls go through the Next.js proxy; server-side uses AGENT_URL directly. */
export function agentBase(): string {
  if (typeof window !== "undefined") return "/agent-proxy";
  return process.env.AGENT_URL || process.env.NEXT_PUBLIC_AGENT_URL || "http://localhost:8123";
}
