"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useCoAgent, useCopilotChat } from "@copilotkit/react-core";
import { MessageRole, TextMessage } from "@copilotkit/runtime-client-gql";
import {
  Activity,
  AlertTriangle,
  ArrowUp,
  BarChart3,
  Bell,
  Clock,
  Database,
  ExternalLink,
  Landmark,
  Loader2,
  Mic,
  MicOff,
  Radio,
  Scale,
  ServerCog,
  ShieldAlert,
  ShieldCheck,
  ShoppingCart,
  Sparkles,
  Terminal,
  Vault,
  XCircle,
} from "lucide-react";
import type {
  AgentStatus,
  DebateState,
  LearningReport,
  ObservabilityEvent,
  RedisActivity,
  RealtimeSession,
  ReliabilityScore,
  RosterMember,
  TraceSummary,
  TranscriptTurn,
} from "@/lib/types";
import { decisionStyle, resolveMember, ROSTER, ROSTER_BY_ID, STANCE_STYLE } from "@/lib/agents";
import { Monogram, Pill, SectionTitle } from "@/components/ui";
import { fmtMonths, fmtSignedMonths } from "@/lib/format";

const AGENT_BASE = process.env.NEXT_PUBLIC_AGENT_URL || "http://localhost:8123";

const NODE_LABEL: Record<string, string> = {
  intake: "Convening the committee",
  treasury: "Treasury is forming its position",
  fpna: "FP&A is forming its position",
  risk: "Risk & Audit is forming its position",
  procurement: "Procurement is forming its position",
  debate: "Committee cross-examination",
  synthesis: "The CFO is deliberating",
  reliability: "Reliability auditor is scoring the council",
  reliability_auditor: "Reliability auditor is scoring the council",
  persist: "Recording the decision",
};

const NODE_TO_AGENT: Record<string, string> = {
  intake: "cfo",
  treasury: "treasury",
  fpna: "fpna",
  risk: "risk",
  procurement: "procurement",
  synthesis: "cfo",
  reliability: "reliability",
  reliability_auditor: "reliability",
  persist: "cfo",
};

const PHASE_LABEL: Record<string, string> = {
  analysis: "Functional analysis",
  debate: "Cross-examination",
  synthesis: "CFO synthesis",
  reliability: "Reliability eval",
  done: "Decision recorded",
};

const SPONSOR_DEFAULTS = [
  { id: "weave", label: "W&B Weave", detail: "Trace readiness pending", icon: Sparkles },
  { id: "openai", label: "OpenAI", detail: "Model readiness pending", icon: Activity },
  { id: "redis", label: "Redis", detail: "Stack readiness pending", icon: Database },
  { id: "copilotkit", label: "CopilotKit", detail: "AG-UI bridge pending", icon: Radio },
  { id: "cursor", label: "Cursor", detail: "Workflow rules pending", icon: Terminal },
];

const AGENT_ICONS = {
  cfo: Landmark,
  treasury: Vault,
  fpna: BarChart3,
  risk: ShieldCheck,
  procurement: ShoppingCart,
  reliability: Activity,
} as const;

type HealthStatus = "loading" | "ready" | "blocked" | "unavailable";
type SponsorStatus = "ready" | "blocked" | "checking";

type HealthCheck = {
  id?: string;
  label: string;
  ready: boolean;
  detail?: string;
  error?: string | null;
  url?: string | null;
  checks?: HealthCheck[];
  capabilities?: string[];
  realtime?: {
    model?: string;
    reasoning_effort?: string;
    voice?: string;
    endpoint?: string;
  };
  sandbox?: {
    configured?: boolean;
    id?: string | null;
    url?: string | null;
    detail?: string;
  };
  modules?: Record<string, string>;
  indices?: Record<string, Record<string, unknown>>;
  streams?: Record<string, Record<string, unknown>>;
  model?: string;
  reasoning_effort?: string;
  verbosity?: string;
};

type HealthPayload = {
  ready: boolean;
  mode?: string;
  blockers?: string[];
  env?: HealthCheck[];
  sponsors?: HealthCheck[];
  weave?: {
    configured?: boolean;
    initialized?: boolean;
    project?: string;
    entity?: string;
    error?: string | null;
    url?: string | null;
  };
};

type HealthView = {
  status: HealthStatus;
  data?: HealthPayload;
  error?: string;
  refreshing?: boolean;
};

type SponsorView = {
  id: string;
  label: string;
  detail: string;
  error?: string | null;
  url?: string | null;
  status: SponsorStatus;
  checks?: HealthCheck[];
  capabilities?: string[];
  realtime?: HealthCheck["realtime"];
  sandbox?: {
    configured?: boolean;
    id?: string | null;
    url?: string | null;
    detail?: string;
  };
  modules?: Record<string, string>;
  indices?: Record<string, Record<string, unknown>>;
  streams?: Record<string, Record<string, unknown>>;
  model?: string;
  reasoning_effort?: string;
  verbosity?: string;
  icon: (typeof SPONSOR_DEFAULTS)[number]["icon"];
};

type TimelineStatus = "complete" | "active" | "pending" | "blocked";
type RealtimeStatus = "idle" | "connecting" | "connected" | "blocked";
type RealtimeView = {
  status: RealtimeStatus;
  detail: string;
  model?: string;
  voice?: string;
};

export default function DecisionsPage() {
  const [input, setInput] = useState("");
  const [health, setHealth] = useState<HealthView>({ status: "loading", refreshing: true });
  const [selectedAgentId, setSelectedAgentId] = useState<string | null>(null);
  const [nowLabel, setNowLabel] = useState("");
  const [realtime, setRealtime] = useState<RealtimeView>({
    status: "idle",
    detail: "Realtime 2 voice idle",
  });
  const realtimePeerRef = useRef<RTCPeerConnection | null>(null);
  const realtimeStreamRef = useRef<MediaStream | null>(null);
  const realtimeAudioRef = useRef<HTMLAudioElement | null>(null);
  const realtimeDataRef = useRef<RTCDataChannel | null>(null);
  const { state, running, nodeName } = useCoAgent<DebateState>({ name: "finance_department" });
  const { appendMessage } = useCopilotChat();

  const transcript: TranscriptTurn[] = state?.transcript ?? [];
  const agentStatuses = state?.agent_statuses ?? [];
  const observabilityEvents = state?.observability_events ?? [];
  const traceSummary = state?.trace_summary;
  const redisActivity = state?.redis_activity ?? [];
  const recommendation = state?.recommendation;
  const reliabilityScores = state?.reliability_scores ?? [];
  const learningReport = state?.learning_report;
  const started = transcript.length > 0 || running;
  const healthReady = health.status === "ready" && health.data?.ready === true;
  const currentPhase = getCurrentPhaseLabel({
    health,
    healthReady,
    nodeName,
    phase: state?.phase,
    recommendation,
    running,
  });

  const loadHealth = useCallback(async () => {
    setHealth((prev) => ({
      ...prev,
      status: prev.data || prev.error ? prev.status : "loading",
      refreshing: true,
    }));

    try {
      const res = await fetch(`${AGENT_BASE}/api/health`, { cache: "no-store" });
      const data = (await res.json().catch(() => null)) as HealthPayload | null;
      if (!data) {
        throw new Error(`/api/health -> ${res.status}`);
      }
      setHealth({
        status: data.ready ? "ready" : "blocked",
        data,
        refreshing: false,
      });
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setHealth({
        status: "unavailable",
        error: message,
        refreshing: false,
        data: {
          ready: false,
          mode: "strict-live",
          blockers: [`Health endpoint unavailable at ${AGENT_BASE}/api/health`, message],
        },
      });
    }
  }, []);

  useEffect(() => {
    const timeout = window.setTimeout(loadHealth, 0);
    const interval = window.setInterval(loadHealth, 15000);
    return () => {
      window.clearTimeout(timeout);
      window.clearInterval(interval);
    };
  }, [loadHealth]);

  useEffect(() => {
    const update = () => {
      setNowLabel(
        new Intl.DateTimeFormat("en-US", {
          hour: "numeric",
          minute: "2-digit",
          second: "2-digit",
          timeZoneName: "short",
        }).format(new Date()),
      );
    };
    update();
    const interval = window.setInterval(update, 1000);
    return () => window.clearInterval(interval);
  }, []);

  const stopRealtime = useCallback(() => {
    realtimeDataRef.current?.close();
    realtimePeerRef.current?.close();
    realtimeStreamRef.current?.getTracks().forEach((track) => track.stop());
    if (realtimeAudioRef.current) {
      realtimeAudioRef.current.srcObject = null;
    }
    realtimeDataRef.current = null;
    realtimePeerRef.current = null;
    realtimeStreamRef.current = null;
    setRealtime((prev) => ({
      status: "idle",
      detail: prev.status === "connected" ? "Realtime 2 voice disconnected" : prev.detail,
      model: prev.model,
      voice: prev.voice,
    }));
  }, []);

  useEffect(() => stopRealtime, [stopRealtime]);

  const startRealtime = useCallback(async () => {
    if (!healthReady) {
      setRealtime({ status: "blocked", detail: "Strict live preflight must pass before voice starts." });
      return;
    }
    if (typeof navigator === "undefined" || !navigator.mediaDevices?.getUserMedia) {
      setRealtime({ status: "blocked", detail: "This browser does not expose microphone capture." });
      return;
    }

    stopRealtime();
    setRealtime({ status: "connecting", detail: "Minting OpenAI Realtime 2 session..." });

    try {
      const sessionRes = await fetch(`${AGENT_BASE}/api/realtime/session`, {
        method: "POST",
        cache: "no-store",
      });
      const session = (await sessionRes.json().catch(() => null)) as RealtimeSession | null;
      if (!sessionRes.ok || !session?.client_secret) {
        throw new Error(`Realtime session failed: ${sessionRes.status}`);
      }

      setRealtime({
        status: "connecting",
        detail: `Connecting microphone to ${session.model}...`,
        model: session.model,
        voice: session.voice,
      });

      const peer = new RTCPeerConnection();
      const audio = new Audio();
      audio.autoplay = true;
      audio.setAttribute("data-atlas-realtime", "true");
      realtimeAudioRef.current = audio;

      peer.ontrack = (event) => {
        const [stream] = event.streams;
        if (stream) {
          audio.srcObject = stream;
          void audio.play().catch(() => undefined);
        }
      };
      peer.onconnectionstatechange = () => {
        if (peer.connectionState === "connected") {
          setRealtime({
            status: "connected",
            detail: `Realtime voice live on ${session.model}`,
            model: session.model,
            voice: session.voice,
          });
        }
        if (["failed", "disconnected", "closed"].includes(peer.connectionState)) {
          setRealtime((prev) =>
            prev.status === "connected"
              ? { ...prev, status: "idle", detail: "Realtime voice disconnected" }
              : prev,
          );
        }
      };

      const media = await navigator.mediaDevices.getUserMedia({ audio: true });
      media.getTracks().forEach((track) => peer.addTrack(track, media));
      realtimeStreamRef.current = media;

      const dataChannel = peer.createDataChannel("oai-events");
      dataChannel.onopen = () => {
        dataChannel.send(
          JSON.stringify({
            type: "response.create",
            response: {
              modalities: ["audio", "text"],
              instructions:
                "Greet the operator and ask which council agent they want to inspect or question.",
            },
          }),
        );
      };
      dataChannel.onmessage = (event) => {
        try {
          const payload = JSON.parse(String(event.data)) as { type?: string; transcript?: string; text?: string };
          if (payload.type?.includes("transcript") && (payload.transcript || payload.text)) {
            setRealtime((prev) => ({
              ...prev,
              detail: payload.transcript || payload.text || prev.detail,
            }));
          }
        } catch {
          // Ignore non-JSON realtime control frames.
        }
      };
      realtimeDataRef.current = dataChannel;

      const offer = await peer.createOffer();
      await peer.setLocalDescription(offer);
      const sdpRes = await fetch(
        `https://api.openai.com/v1/realtime?model=${encodeURIComponent(session.model)}`,
        {
          method: "POST",
          body: offer.sdp ?? "",
          headers: {
            Authorization: `Bearer ${session.client_secret}`,
            "Content-Type": "application/sdp",
          },
        },
      );
      if (!sdpRes.ok) {
        throw new Error(`Realtime SDP exchange failed: ${sdpRes.status}`);
      }
      await peer.setRemoteDescription({ type: "answer", sdp: await sdpRes.text() });
      realtimePeerRef.current = peer;
    } catch (err) {
      stopRealtime();
      setRealtime({
        status: "blocked",
        detail: err instanceof Error ? err.message : String(err),
      });
    }
  }, [healthReady, stopRealtime]);

  async function submit(text: string) {
    const content = text.trim();
    if (!content || running || !healthReady) return;
    setInput("");
    await appendMessage(new TextMessage({ role: MessageRole.User, content }));
  }

  const sponsorRows = useMemo(() => getSponsorRows(health), [health]);

  return (
    <main className="flex min-h-full flex-col bg-background">
      <CouncilTopBar
        healthReady={healthReady}
        learningReport={learningReport}
        nowLabel={nowLabel}
        reliabilityScores={reliabilityScores}
        sponsorRows={sponsorRows}
      />

      <CouncilBriefStrip
        currentPhase={currentPhase}
        decision={state?.decision}
        health={health}
        healthReady={healthReady}
        nodeName={nodeName}
        phase={state?.phase}
        recommendation={recommendation}
        running={running}
        transcript={transcript}
      />

      {!healthReady && (
        <div className="px-2 pt-2">
          <PreflightPanel health={health} onRefresh={loadHealth} />
        </div>
      )}

      <div className="grid flex-1 items-start gap-2 p-2">
        <div className="min-w-0 space-y-2">
          <CouncilStage
            agentStatuses={agentStatuses}
            currentPhase={currentPhase}
            decision={state?.decision}
            events={observabilityEvents}
            healthReady={healthReady}
            input={input}
            nodeName={nodeName}
            onInput={setInput}
            onSelectAgent={setSelectedAgentId}
            onStartRealtime={startRealtime}
            onStopRealtime={stopRealtime}
            onSubmit={submit}
            learningReport={learningReport}
            recommendation={recommendation}
            redisActivity={redisActivity}
            reliabilityScores={reliabilityScores}
            realtime={realtime}
            running={running}
            selectedAgentId={selectedAgentId}
            started={started}
            traceSummary={traceSummary}
            transcript={transcript}
          />

          <LiveEventPanel
            events={observabilityEvents}
            nodeName={nodeName}
            running={running}
            transcript={transcript}
          />
        </div>
      </div>
    </main>
  );
}

function CouncilTopBar({
  healthReady,
  learningReport,
  nowLabel,
  reliabilityScores,
  sponsorRows,
}: {
  healthReady: boolean;
  learningReport?: LearningReport;
  nowLabel: string;
  reliabilityScores: ReliabilityScore[];
  sponsorRows: SponsorView[];
}) {
  const avgReliability = averageReliability(reliabilityScores);
  const weave = sponsorRows.find((row) => row.id === "weave");
  const redis = sponsorRows.find((row) => row.id === "redis");
  return (
    <header id="settings" className="flex min-h-14 items-center justify-between gap-3 border-b border-border bg-surface px-4 lg:px-5">
      <div className="min-w-0">
        <h1 className="truncate text-[18px] font-semibold">AI Council</h1>
      </div>
      <div className="flex min-w-0 items-center gap-3 text-[12px] text-muted-foreground">
        <div className="hidden items-center gap-2 sm:flex">
          <span className={`h-2 w-2 rounded-full ${healthReady ? "bg-positive" : "bg-warning"}`} />
          <span>{healthReady ? "System Healthy" : "Preflight Checking"}</span>
        </div>
        <div className="hidden tabular-nums md:block">{nowLabel}</div>
        <SystemSignal label="W&B" status={weave?.status ?? "checking"} value={learningReport?.eval_dataset ?? weave?.detail ?? "Weave"} />
        <SystemSignal label="Redis" status={redis?.status ?? "checking"} value={redis?.detail ?? "Memory"} />
        <SystemSignal label="Reliability" status={avgReliability ? "ready" : "checking"} value={avgReliability ? `${avgReliability}%` : "Pending"} />
        <span className="inline-flex items-center gap-1.5 rounded-md border border-positive/20 bg-positive-bg px-2.5 py-1 text-[12px] font-semibold text-positive">
          <Radio className="h-3.5 w-3.5" strokeWidth={2.25} />
          Live
        </span>
        <div className="hidden h-6 w-px bg-border md:block" />
        <Bell className="hidden h-4 w-4 md:block" strokeWidth={1.9} />
        <div className="grid h-7 w-7 place-items-center rounded-full bg-info-bg text-[12px] font-semibold text-info">
          AT
        </div>
      </div>
    </header>
  );
}

function SystemSignal({ label, status, value }: { label: string; status: SponsorStatus; value: string }) {
  const tone =
    status === "ready"
      ? "border-positive/20 bg-positive-bg text-positive"
      : status === "blocked"
        ? "border-risk/20 bg-risk-bg text-risk"
        : "border-warning/20 bg-warning-bg text-warning";
  return (
    <span className={`hidden max-w-[190px] items-center gap-1.5 rounded-md border px-2 py-1 text-[11px] font-semibold lg:inline-flex ${tone}`}>
      <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-current" />
      <span>{label}</span>
      <span className="truncate font-medium opacity-75">{value}</span>
    </span>
  );
}

function CouncilBriefStrip({
  currentPhase,
  health,
  healthReady,
  nodeName,
  phase,
  decision,
  recommendation,
  running,
  transcript,
}: {
  currentPhase: string;
  health: HealthView;
  healthReady: boolean;
  nodeName?: string;
  phase?: string;
  decision?: string;
  recommendation?: DebateState["recommendation"];
  running: boolean;
  transcript: TranscriptTurn[];
}) {
  const progress = buildTimeline({
    health,
    healthReady,
    nodeName,
    phase,
    recommendation,
    running,
    transcript,
  });
  const activeStep = progress.find((step) => step.status === "active")?.label ?? "Ready";

  return (
    <section className="border-b border-border bg-surface px-4 py-4 lg:px-5">
      <div className="grid gap-4 xl:grid-cols-[minmax(280px,1fr)_300px_210px_410px] xl:items-center">
        <div>
          <div className="flex min-w-0 items-center gap-2">
            <h2 className="truncate text-[22px] font-semibold tracking-tight">AI Council Room</h2>
            <span className="inline-flex shrink-0 items-center gap-1 rounded-md border border-positive/20 bg-positive-bg px-2 py-0.5 text-[11px] font-semibold uppercase text-positive">
              <span className="h-1.5 w-1.5 rounded-full bg-positive" />
              Live
            </span>
          </div>
          <p className="mt-1 text-[13px] leading-relaxed text-muted-foreground">
            Autonomous finance council debating a company decision.
          </p>
        </div>

        <div className="flex min-w-0 gap-3 border-l border-border pl-4">
          <div className="min-w-0 flex-1">
            <div className="text-[11px] font-medium text-muted-foreground">Decision Under Debate</div>
            <div className="mt-1 text-[14px] font-semibold leading-snug">
              {decision || "Awaiting live decision command"}
            </div>
          </div>
        </div>

        <div className="border-l border-border pl-4">
          <div className="text-[11px] font-medium text-muted-foreground">Current Phase</div>
          <div className="mt-1 text-[14px] font-semibold text-info">{currentPhase}</div>
          <div className="mt-0.5 text-[11px] text-muted-foreground">
            {running ? NODE_LABEL[nodeName ?? ""] ?? "Streaming" : getHealthLabel(health)}
          </div>
        </div>

        <div className="border-l border-border pl-4">
          <div className="mb-3 flex items-center justify-between text-[11px] font-medium text-muted-foreground">
            <span>Decision Progress</span>
            <span>{activeStep}</span>
          </div>
          <div className="grid grid-cols-5 gap-2">
            {buildReferenceProgress(progress).map((step) => (
              <div key={step.label} className="min-w-0">
                <div
                  className={`mx-auto h-4 w-4 rounded-full border ${
                    step.status === "complete"
                      ? "border-positive bg-positive"
                      : step.status === "active"
                        ? "border-info bg-info"
                        : "border-border-strong bg-surface"
                  }`}
                />
                <div className="mt-1 truncate text-center text-[10px] text-muted-foreground">
                  {step.label}
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}

function PreflightPanel({ health, onRefresh }: { health: HealthView; onRefresh: () => void }) {
  const blockers = health.data?.blockers?.length
    ? health.data.blockers
    : health.error
      ? [health.error]
      : ["Awaiting /api/health from the live agent service."];

  return (
    <section className="rounded-2xl border border-risk/25 bg-risk-bg px-5 py-4 text-risk shadow-sm">
      <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            {health.status === "loading" ? (
              <Loader2 className="h-5 w-5 animate-spin" strokeWidth={2.25} />
            ) : (
              <AlertTriangle className="h-5 w-5" strokeWidth={2.25} />
            )}
            <h2 className="text-[17px] font-semibold">
              {health.status === "loading" ? "Strict live preflight is checking" : "Strict live preflight failed"}
            </h2>
          </div>
          <p className="mt-2 max-w-[860px] text-[13px] leading-relaxed text-risk/85">
            Council submissions are locked until W&B Weave, OpenAI, Redis, CopilotKit, and Cursor
            readiness all report green from the live health endpoint.
          </p>
          <ul className="mt-3 grid gap-1.5 text-[12px] leading-relaxed text-risk/90 md:grid-cols-2">
            {blockers.map((blocker) => (
              <li key={blocker} className="flex min-w-0 gap-2">
                <XCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" strokeWidth={2.25} />
                <span className="break-words">{blocker}</span>
              </li>
            ))}
          </ul>
        </div>
        <button
          type="button"
          onClick={onRefresh}
          className="inline-flex h-10 shrink-0 items-center justify-center gap-2 rounded-lg border border-risk/25 bg-surface px-4 text-[13px] font-semibold text-risk transition-colors hover:bg-risk-bg"
        >
          {health.refreshing ? <Loader2 className="h-4 w-4 animate-spin" /> : <ShieldAlert className="h-4 w-4" />}
          Recheck preflight
        </button>
      </div>
    </section>
  );
}

function CouncilStage({
  agentStatuses,
  currentPhase,
  decision,
  events,
  healthReady,
  input,
  nodeName,
  onInput,
  onSelectAgent,
  onStartRealtime,
  onStopRealtime,
  onSubmit,
  learningReport,
  recommendation,
  redisActivity,
  reliabilityScores,
  realtime,
  running,
  selectedAgentId,
  started,
  traceSummary,
  transcript,
}: {
  agentStatuses: AgentStatus[];
  currentPhase: string;
  decision?: string;
  events: ObservabilityEvent[];
  healthReady: boolean;
  input: string;
  nodeName?: string;
  onInput: (value: string) => void;
  onSelectAgent: (agentId: string) => void;
  onStartRealtime: () => void;
  onStopRealtime: () => void;
  onSubmit: (value: string) => void;
  learningReport?: LearningReport;
  recommendation?: DebateState["recommendation"];
  redisActivity: RedisActivity[];
  reliabilityScores: ReliabilityScore[];
  realtime: RealtimeView;
  running: boolean;
  selectedAgentId: string | null;
  started: boolean;
  traceSummary?: TraceSummary;
  transcript: TranscriptTurn[];
}) {
  const members = [ROSTER_BY_ID.cfo, ...ROSTER.filter((member) => member.id !== "cfo")];
  const latestSpeaker = latestSpeakerId(transcript);
  const activeAgentId = NODE_TO_AGENT[nodeName ?? ""];
  const selectedMember =
    ROSTER_BY_ID[selectedAgentId ?? activeAgentId ?? latestSpeaker ?? "cfo"] ?? ROSTER_BY_ID.cfo;
  const statusById = Object.fromEntries(agentStatuses.map((status) => [status.id, status]));
  const scoreById = Object.fromEntries(reliabilityScores.map((score) => [score.agent_id, score]));

  return (
    <section id="evals" className="overflow-hidden rounded-lg border border-border bg-surface shadow-sm">
      <div className="relative min-h-[760px] border-b border-border p-4 md:p-6">
        <div className="pointer-events-none absolute inset-0 hidden md:block">
          <span className="absolute left-[27%] top-[34%] h-px w-[23%] rotate-[28deg] border-t border-dashed border-border-strong" />
          <span className="absolute right-[25%] top-[34%] h-px w-[23%] rotate-[-28deg] border-t border-dashed border-border-strong" />
          <span className="absolute left-[28%] bottom-[28%] h-px w-[22%] rotate-[-6deg] border-t border-dashed border-border-strong" />
          <span className="absolute right-[25%] bottom-[28%] h-px w-[22%] rotate-[4deg] border-t border-dashed border-border-strong" />
          <span className="absolute left-1/2 top-[25%] h-[120px] border-l border-dashed border-border-strong" />
        </div>

        <div className="grid gap-4 md:hidden">
          {members.map((member) => (
            <AgentNode
              key={member.id}
              agentStatus={statusById[member.id]}
              healthReady={healthReady}
              latestSpeaker={latestSpeaker}
              member={member}
              nodeName={nodeName}
              onSelect={() => onSelectAgent(member.id)}
              recommendation={recommendation}
              reliabilityScore={scoreById[member.id]}
              running={running}
              selected={selectedMember.id === member.id}
              started={started}
              transcript={transcript}
            />
          ))}
        </div>

        <div className="hidden md:block">
          {members.map((member) => (
            <AgentNode
              key={member.id}
              absolute
              agentStatus={statusById[member.id]}
              healthReady={healthReady}
              latestSpeaker={latestSpeaker}
              member={member}
              nodeName={nodeName}
              onSelect={() => onSelectAgent(member.id)}
              recommendation={recommendation}
              reliabilityScore={scoreById[member.id]}
              running={running}
              selected={selectedMember.id === member.id}
              started={started}
              transcript={transcript}
            />
          ))}

          <DecisionThreadPanel
            currentPhase={currentPhase}
            decision={decision}
            healthReady={healthReady}
            input={input}
            onInput={onInput}
            onStartRealtime={onStartRealtime}
            onStopRealtime={onStopRealtime}
            onSubmit={onSubmit}
            recommendation={recommendation}
            realtime={realtime}
            running={running}
            selectedMember={selectedMember}
            started={started}
            transcript={transcript}
          />
        </div>
      </div>

      <AgentDetailPanel
        agentStatus={statusById[selectedMember.id]}
        events={events}
        healthReady={healthReady}
        member={selectedMember}
        nodeName={nodeName}
        learningReport={learningReport}
        recommendation={recommendation}
        redisActivity={redisActivity}
        reliabilityScore={scoreById[selectedMember.id]}
        running={running}
        started={started}
        traceSummary={traceSummary}
        transcript={transcript}
      />

      <div className="grid gap-0 border-t border-border bg-surface-quiet md:grid-cols-4">
        <CouncilStat label="Council Mode" value="Balanced Debate" icon={<Scale className="h-4 w-4" />} />
        <CouncilStat label="Reliability Avg" value={formatReliability(averageReliability(reliabilityScores))} spark />
        <CouncilStat label="Active Tokens" value={formatTelemetry(traceSummary?.total_tokens)} spark />
        <CouncilStat label="Latency (p95)" value={formatTelemetry(traceSummary?.latency_ms, "ms")} spark />
      </div>
    </section>
  );
}

function AgentNode({
  absolute = false,
  agentStatus,
  healthReady,
  latestSpeaker,
  member,
  nodeName,
  onSelect,
  recommendation,
  reliabilityScore,
  running,
  selected,
  started,
  transcript,
}: {
  absolute?: boolean;
  agentStatus?: AgentStatus;
  healthReady: boolean;
  latestSpeaker?: string;
  member: RosterMember;
  nodeName?: string;
  onSelect: () => void;
  recommendation?: DebateState["recommendation"];
  reliabilityScore?: ReliabilityScore;
  running: boolean;
  selected: boolean;
  started: boolean;
  transcript: TranscriptTurn[];
}) {
  const latestTurn = findLatestTurnForMember(member.id, transcript);
  const statusValue = String(agentStatus?.status ?? "").toLowerCase();
  const active =
    healthReady &&
    ((running && NODE_TO_AGENT[nodeName ?? ""] === member.id) ||
      ["thinking", "speaking", "running"].includes(statusValue));
  const status = getAgentStatus({
    active,
    agentStatus,
    healthReady,
    latestSpeaker,
    latestTurn,
    member,
    nodeName,
    started,
  });
  const snippet = getAgentSnippet({ agentStatus, member, turn: latestTurn, recommendation, healthReady, started });
  const stanceLabel = getAgentStanceLabel(member, latestTurn, recommendation);
  const placement = agentPlacement(member.id);
  const Icon = AGENT_ICONS[member.id as keyof typeof AGENT_ICONS] ?? ServerCog;
  const pillClass = agentStatusPillClass({ active, status, memberId: member.id });
  const scoreValue = resolveReliabilityValue(agentStatus, reliabilityScore);
  const scoreStyle = scoreValue
    ? { background: `conic-gradient(${reliabilityColor(scoreValue)} ${scoreValue * 3.6}deg, var(--border) 0deg)` }
    : undefined;

  return (
    <button
      type="button"
      onClick={onSelect}
      aria-controls="agent-uds-panel"
      aria-pressed={selected}
      data-agent-id={member.id}
      className={`group text-left ${
        absolute ? `absolute ${placement} w-[260px]` : ""
      } ${selected ? "z-20" : "z-10"}`}
    >
      <article
        className={`rounded-lg border p-2 transition-all ${
          selected
            ? "border-info/30 bg-info-bg/35 shadow-[0_0_0_3px_rgba(47,91,183,0.10)]"
            : active
              ? "border-transparent bg-transparent"
              : "border-transparent bg-transparent hover:border-border hover:bg-surface"
        }`}
      >
        <div className="flex items-center gap-3">
          <div
            className={`relative grid h-[86px] w-[86px] shrink-0 place-items-center rounded-full border-[3px] p-[3px] ${agentRingClass(member.id, latestTurn, recommendation, active)}`}
            style={scoreStyle}
            title={scoreValue ? `Reliability ${scoreValue}%` : "Reliability pending"}
          >
            <div className="grid h-[56px] w-[56px] place-items-center rounded-full border border-border bg-surface">
              <Icon className="h-8 w-8" strokeWidth={1.9} />
            </div>
            <span className="absolute -bottom-1 -right-1 rounded-full border border-border bg-background px-1.5 py-0.5 text-[10px] font-bold tabular-nums text-foreground shadow-sm">
              {scoreValue ? `${scoreValue}%` : "EVAL"}
            </span>
          </div>
          <div className="min-w-0">
            <div className="text-[17px] font-semibold leading-tight">{member.label}</div>
            <div className={`mt-1 inline-flex rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase ${pillClass}`}>
              {status}
            </div>
            <Waveform active={active} />
          </div>
        </div>
        <div className={`mt-3 text-[12px] font-semibold ${stanceColor(member.id, latestTurn, recommendation)}`}>
          Stance: {stanceLabel}
        </div>
        <p className="mt-2 line-clamp-4 text-[12px] italic leading-relaxed text-foreground">
          “{snippet}”
        </p>
      </article>
    </button>
  );
}

function DecisionThreadPanel({
  currentPhase,
  decision,
  healthReady,
  input,
  onInput,
  onStartRealtime,
  onStopRealtime,
  onSubmit,
  recommendation,
  realtime,
  running,
  selectedMember,
  started,
  transcript,
}: {
  currentPhase: string;
  decision?: string;
  healthReady: boolean;
  input: string;
  onInput: (value: string) => void;
  onStartRealtime: () => void;
  onStopRealtime: () => void;
  onSubmit: (value: string) => void;
  recommendation?: DebateState["recommendation"];
  realtime: RealtimeView;
  running: boolean;
  selectedMember?: RosterMember;
  started: boolean;
  transcript: TranscriptTurn[];
}) {
  const turns = transcript.slice(-4);
  const sendDisabled = running || !healthReady || !input.trim();
  return (
    <div className="absolute left-1/2 top-[48%] w-[300px] -translate-x-1/2 -translate-y-1/2 rounded-lg border border-border bg-surface px-3.5 py-3.5 shadow-sm">
      <div className="text-center text-[12px] font-medium text-muted-foreground">Decision Thread (Live)</div>
      <div className="mt-3 space-y-2">
        {turns.length === 0 ? (
          <p className="text-[12px] leading-relaxed text-muted-foreground">
            {started ? "Waiting for the first live council turn." : "Submit a decision to open the live thread."}
          </p>
        ) : (
          turns.map((turn, index) => (
            <div key={`${turn.agent}-${turn.type}-${index}`} className="text-[12px] leading-relaxed">
              <span className="font-semibold">{resolveThreadLabel(turn)}</span>
              <span className="ml-2 text-[10px] text-subtle-foreground">{turn.type}</span>
              <div className="line-clamp-2 text-muted-foreground">{turn.headline || turn.point || turn.argument}</div>
            </div>
          ))
        )}
      </div>
      <div className="mt-3 border-t border-border pt-3 text-[12px]">
        <div className="font-semibold text-info">{currentPhase}</div>
        <div className="line-clamp-2 text-muted-foreground">{decision || "No decision loaded"}</div>
        {recommendation?.decision && (
          <div className="mt-2 font-semibold text-positive">
            {recommendation.decision} · {recommendation.confidence ?? "--"}%
          </div>
        )}
        {running && <div className="mt-2 text-warning">Live stream active</div>}
      </div>

      <form
        className="mt-3 border-t border-border pt-3"
        onSubmit={(event) => {
          event.preventDefault();
          onSubmit(input);
        }}
      >
        <textarea
          value={input}
          onChange={(event) => onInput(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              onSubmit(input);
            }
          }}
          rows={2}
          disabled={running}
          placeholder={started ? "Ask the council a follow-up..." : "Send a decision to the council..."}
          className="min-h-[58px] w-full resize-none rounded-md border border-border bg-background px-2.5 py-2 text-[12px] leading-relaxed outline-none placeholder:text-subtle-foreground focus:border-border-strong disabled:opacity-50"
        />
        <div className="mt-2 flex items-center gap-2">
          <button
            type="submit"
            disabled={sendDisabled}
            className="inline-flex h-8 flex-1 items-center justify-center gap-1.5 rounded-md bg-accent px-2.5 text-[12px] font-semibold text-accent-foreground transition-opacity disabled:opacity-40"
          >
            {running ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <ArrowUp className="h-3.5 w-3.5" />}
            Send
          </button>
          <button
            type="button"
            onClick={realtime.status === "connected" ? onStopRealtime : onStartRealtime}
            disabled={!healthReady || realtime.status === "connecting"}
            className="inline-flex h-8 items-center justify-center gap-1.5 rounded-md border border-info/20 bg-info-bg px-2.5 text-[12px] font-semibold text-info disabled:opacity-40"
          >
            {realtime.status === "connecting" ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : realtime.status === "connected" ? (
              <MicOff className="h-3.5 w-3.5" />
            ) : (
              <Mic className="h-3.5 w-3.5" />
            )}
            Realtime 2
          </button>
        </div>
        <div className="mt-1.5 line-clamp-2 text-[10px] leading-relaxed text-subtle-foreground">
          {realtime.detail}
        </div>
      </form>

      {selectedMember && (
        <div className="mt-3 truncate text-[12px] font-semibold text-info">
          Viewing {selectedMember.label} details
        </div>
      )}
    </div>
  );
}

function AgentDetailPanel({
  agentStatus,
  events,
  healthReady,
  member,
  nodeName,
  learningReport,
  recommendation,
  redisActivity,
  reliabilityScore,
  running,
  started,
  traceSummary,
  transcript,
}: {
  agentStatus?: AgentStatus;
  events: ObservabilityEvent[];
  healthReady: boolean;
  member: RosterMember;
  nodeName?: string;
  learningReport?: LearningReport;
  recommendation?: DebateState["recommendation"];
  redisActivity: RedisActivity[];
  reliabilityScore?: ReliabilityScore;
  running: boolean;
  started: boolean;
  traceSummary?: TraceSummary;
  transcript: TranscriptTurn[];
}) {
  const turn = findLatestTurnForMember(member.id, transcript);
  const isActive = running && NODE_TO_AGENT[nodeName ?? ""] === member.id;
  const snippet = getAgentSnippet({ agentStatus, member, turn, recommendation, healthReady, started });
  const scoreValue = resolveReliabilityValue(agentStatus, reliabilityScore);
  const agentEvents = events
    .filter((event) => `${event.label ?? ""} ${event.detail ?? ""}`.toLowerCase().includes(member.label.toLowerCase().split(" ")[0].toLowerCase()))
    .slice(-3)
    .reverse();

  return (
    <div id="agent-uds-panel" className="grid gap-0 border-t border-border bg-background lg:grid-cols-[minmax(0,1.1fr)_minmax(260px,0.9fr)]">
      <div className="min-w-0 p-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <SectionTitle>Agent UDS</SectionTitle>
            <h3 className="mt-1 text-[18px] font-semibold">{member.label} detail inspector</h3>
          </div>
          <span className={`rounded-full border px-2.5 py-1 text-[11px] font-semibold ${isActive ? "border-warning/20 bg-warning-bg text-warning" : "border-border bg-surface text-muted-foreground"}`}>
            {isActive ? "Active in graph" : agentStatus?.status ?? "Waiting"}
          </span>
        </div>
        <p className="mt-3 text-[14px] leading-relaxed text-foreground">{snippet}</p>
        <ReliabilityDetail
          agentStatus={agentStatus}
          learningReport={learningReport}
          member={member}
          score={reliabilityScore}
        />
        {turn?.key_points && turn.key_points.length > 0 && (
          <div className="mt-3 grid gap-2 md:grid-cols-2">
            {turn.key_points.slice(0, 4).map((point) => (
              <div key={point} className="rounded-md border border-border bg-surface px-3 py-2 text-[12px] leading-relaxed text-muted-foreground">
                {point}
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="min-w-0 border-t border-border bg-surface p-4 lg:border-l lg:border-t-0">
        <div className="grid grid-cols-2 gap-2">
          <RailMetric label="Node" value={isActive ? NODE_LABEL[nodeName ?? ""] ?? nodeName ?? "Active" : traceSummary?.node ?? "Idle"} />
          <RailMetric label="Trace" value={traceSummary?.status ?? "Waiting"} />
          <RailMetric label="Redis" value={redisActivity.at(-1)?.label ?? "No event"} />
          <RailMetric label="Reliability" value={scoreValue ? `${scoreValue}%` : "Pending"} />
        </div>
        <div className="mt-3 space-y-2">
          {(agentEvents.length ? agentEvents : events.slice(-2).reverse()).map((event, index) => (
            <RailEvent
              key={event.id ?? `${event.label}-${index}`}
              label={`${event.sponsor ?? "Atlas"} · ${event.label ?? "Event"}`}
              detail={event.detail ?? event.summary ?? event.status ?? "Live event recorded"}
              tone={event.tone ?? "info"}
            />
          ))}
        </div>
      </div>
    </div>
  );
}

function ReliabilityDetail({
  agentStatus,
  learningReport,
  member,
  score,
}: {
  agentStatus?: AgentStatus;
  learningReport?: LearningReport;
  member: RosterMember;
  score?: ReliabilityScore;
}) {
  const scoreValue = resolveReliabilityValue(agentStatus, score);
  const dimensions = score?.agent_id
    ? reliabilityDimensionsFromScore(score)
    : agentStatus?.reliability_dimensions;
  const weaknesses = score?.known_weaknesses ?? agentStatus?.known_weaknesses ?? [];
  const promptAdjustment = score?.prompt_adjustment ?? agentStatus?.prompt_adjustment;
  const promotionGate = score?.promotion_gate ?? agentStatus?.promotion_gate ?? learningReport?.promotion_gate;
  const rationale = score?.rationale ?? agentStatus?.reliability_rationale ?? learningReport?.summary;

  return (
    <section className="mt-4 rounded-lg border border-border bg-surface px-3 py-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <SectionTitle>W&B Self-Improvement</SectionTitle>
          <div className="mt-1 text-[14px] font-semibold">Reliability scorecard</div>
        </div>
        <div
          className="rounded-full border px-3 py-1 text-[13px] font-bold tabular-nums"
          style={{
            borderColor: scoreValue ? reliabilityColor(scoreValue) : "var(--border)",
            color: scoreValue ? reliabilityColor(scoreValue) : "var(--muted-foreground)",
          }}
        >
          {scoreValue ? `${scoreValue}%` : "Pending"}
        </div>
      </div>
      <p className="mt-2 text-[12px] leading-relaxed text-muted-foreground">
        {rationale ?? `${member.label} will receive a score after the reliability auditor runs.`}
      </p>

      {dimensions && (
        <div className="mt-3 grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
          {Object.entries(dimensions).map(([key, value]) => (
            <div key={key} className="rounded-md border border-border bg-background px-2.5 py-2">
              <div className="text-[10px] text-subtle-foreground">{formatDimensionLabel(key)}</div>
              <div className="mt-1 text-[15px] font-semibold tabular-nums">{typeof value === "number" ? `${value}%` : "Pending"}</div>
            </div>
          ))}
        </div>
      )}

      {(weaknesses.length > 0 || promptAdjustment || promotionGate || learningReport?.replay_plan?.length) && (
        <div className="mt-3 grid gap-3 md:grid-cols-2">
          <div className="rounded-md border border-border bg-background px-3 py-2">
            <div className="text-[11px] font-semibold text-muted-foreground">Known Weaknesses</div>
            <div className="mt-1 text-[12px] leading-relaxed text-foreground">
              {weaknesses.length ? weaknesses.slice(0, 2).join(" · ") : "No weaknesses recorded yet."}
            </div>
          </div>
          <div className="rounded-md border border-border bg-background px-3 py-2">
            <div className="text-[11px] font-semibold text-muted-foreground">Prompt Adjustment</div>
            <div className="mt-1 line-clamp-3 text-[12px] leading-relaxed text-foreground">
              {promptAdjustment ?? "Replay gate pending."}
            </div>
          </div>
          <div className="rounded-md border border-border bg-background px-3 py-2 md:col-span-2">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="text-[11px] font-semibold text-muted-foreground">Promotion Gate</div>
              {learningReport?.weave_url && (
                <a
                  href={learningReport.weave_url}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex items-center gap-1 text-[11px] font-semibold text-info"
                >
                  W&B Weave
                  <ExternalLink className="h-3 w-3" strokeWidth={2.25} />
                </a>
              )}
            </div>
            <div className="mt-1 text-[12px] leading-relaxed text-foreground">
              {promotionGate ?? "No promotion gate produced yet."}
            </div>
            {learningReport?.replay_plan && learningReport.replay_plan.length > 0 && (
              <div className="mt-2 text-[11px] leading-relaxed text-muted-foreground">
                Replay plan: {learningReport.replay_plan.slice(0, 2).join(" · ")}
              </div>
            )}
          </div>
        </div>
      )}
    </section>
  );
}

function CouncilStat({ label, value, icon, spark = false }: { label: string; value: string; icon?: ReactNode; spark?: boolean }) {
  return (
    <div className="flex min-w-0 items-center gap-3 border-b border-border px-4 py-3 last:border-b-0 md:border-b-0 md:border-r md:last:border-r-0">
      <div className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-surface text-muted-foreground">
        {icon ?? <Activity className="h-4 w-4" />}
      </div>
      <div className="min-w-0 flex-1">
        <div className="text-[11px] text-muted-foreground">{label}</div>
        <div className="truncate text-[12px] font-semibold">{value}</div>
      </div>
      {spark && value !== "Waiting" && <MiniSparkline />}
    </div>
  );
}

function agentPlacement(id: string) {
  switch (id) {
    case "cfo":
      return "left-[4%] top-[9%]";
    case "treasury":
      return "left-1/2 top-[8%] -translate-x-1/2";
    case "fpna":
      return "right-[4%] top-[9%]";
    case "risk":
      return "left-[4%] bottom-[9%]";
    case "procurement":
      return "right-[4%] bottom-[9%]";
    case "reliability":
      return "left-1/2 bottom-[4%] -translate-x-1/2";
    default:
      return "left-4 top-4";
  }
}

function agentRingClass(
  id: string,
  turn?: TranscriptTurn,
  recommendation?: DebateState["recommendation"],
  active?: boolean,
) {
  const stance = getAgentStanceLabel(ROSTER_BY_ID[id] ?? ROSTER_BY_ID.cfo, turn, recommendation).toLowerCase();
  const activeGlow = active ? " shadow-[0_0_0_7px_rgba(24,121,78,0.12)]" : "";
  if (id === "treasury") return `border-info text-info shadow-[0_0_0_6px_rgba(47,91,183,0.10)]${activeGlow}`;
  if (id === "fpna") return `border-warning text-warning shadow-[0_0_0_6px_rgba(181,71,8,0.10)]${activeGlow}`;
  if (id === "risk") return `border-risk text-risk shadow-[0_0_0_6px_rgba(180,35,24,0.10)]${activeGlow}`;
  if (id === "procurement") return `border-positive text-positive shadow-[0_0_0_6px_rgba(24,121,78,0.10)]${activeGlow}`;
  if (id === "reliability") return `border-info text-info shadow-[0_0_0_6px_rgba(47,91,183,0.10)]${activeGlow}`;
  if (stance.includes("oppose") || stance.includes("reject")) {
    return `border-risk text-risk shadow-[0_0_0_6px_rgba(180,35,24,0.10)]${activeGlow}`;
  }
  if (stance.includes("caution") || stance.includes("conditional") || stance.includes("defer")) {
    return `border-warning text-warning shadow-[0_0_0_6px_rgba(181,71,8,0.10)]${activeGlow}`;
  }
  return `border-positive text-positive shadow-[0_0_0_6px_rgba(24,121,78,0.10)]${activeGlow}`;
}

function agentStatusPillClass({
  active,
  memberId,
  status,
}: {
  active: boolean;
  memberId: string;
  status: string;
}) {
  const normalized = status.toLowerCase();
  if (normalized.includes("thinking")) return "bg-violet-100 text-violet-700";
  if (active || normalized.includes("speaking")) return "bg-positive-bg text-positive";
  if (memberId === "risk" || normalized.includes("blocked") || normalized.includes("error")) return "bg-risk-bg text-risk";
  return "bg-info-bg text-info";
}

function stanceColor(
  id: string,
  turn?: TranscriptTurn,
  recommendation?: DebateState["recommendation"],
) {
  const stance = getAgentStanceLabel(ROSTER_BY_ID[id] ?? ROSTER_BY_ID.cfo, turn, recommendation).toLowerCase();
  if (stance.includes("oppose") || stance.includes("reject")) return "text-risk";
  if (stance.includes("caution") || stance.includes("conditional") || stance.includes("defer")) return "text-warning";
  if (stance.includes("support") || stance.includes("approve")) return "text-positive";
  return "text-info";
}

function getAgentStanceLabel(
  member: RosterMember,
  turn?: TranscriptTurn,
  recommendation?: DebateState["recommendation"],
) {
  if (member.id === "cfo" && recommendation?.decision) return recommendation.decision;
  if (member.id === "reliability") return "Auditor";
  if (turn?.stance) {
    if (turn.stance === "conditional") return "Caution";
    return String(turn.stance).toUpperCase();
  }
  return member.id === "cfo" ? "Chair" : "Neutral";
}

function Waveform({ active }: { active: boolean }) {
  return (
    <div className={`mt-2 flex h-4 items-end gap-[3px] ${active ? "text-positive" : "text-info"}`} aria-hidden="true">
      {[4, 9, 13, 7, 15, 6, 11, 5, 10].map((height, index) => (
        <span
          key={`${height}-${index}`}
          className={`w-[2px] rounded-full bg-current ${active ? "animate-pulse" : "opacity-75"}`}
          style={{ height }}
        />
      ))}
    </div>
  );
}

function MiniSparkline() {
  return (
    <div className="hidden h-7 w-12 items-end gap-[2px] text-positive sm:flex" aria-hidden="true">
      {[5, 7, 4, 9, 6, 12, 15, 10, 18, 22, 27].map((height, index) => (
        <span key={`${height}-${index}`} className="w-[2px] rounded-full bg-current/75" style={{ height }} />
      ))}
    </div>
  );
}

function resolveThreadLabel(turn: TranscriptTurn) {
  if (turn.label) return turn.label;
  if (turn.agent && ROSTER_BY_ID[turn.agent]) return ROSTER_BY_ID[turn.agent].label;
  if (turn.from_role) return resolveMember(turn.from_role)?.label ?? turn.from_role;
  return "Council";
}

// eslint-disable-next-line @typescript-eslint/no-unused-vars
function AgentConsole({
  agentStatus,
  featured = false,
  healthReady,
  latestSpeaker,
  member,
  nodeName,
  recommendation,
  running,
  started,
  transcript,
}: {
  agentStatus?: AgentStatus;
  featured?: boolean;
  healthReady: boolean;
  latestSpeaker?: string;
  member: RosterMember;
  nodeName?: string;
  recommendation?: DebateState["recommendation"];
  running: boolean;
  started: boolean;
  transcript: TranscriptTurn[];
}) {
  const latestTurn = findLatestTurnForMember(member.id, transcript);
  const statusValue = String(agentStatus?.status ?? "").toLowerCase();
  const active =
    healthReady &&
    (running && NODE_TO_AGENT[nodeName ?? ""] === member.id ||
      ["thinking", "speaking", "running"].includes(statusValue));
  const status = getAgentStatus({
    active,
    agentStatus,
    healthReady,
    latestSpeaker,
    latestTurn,
    member,
    nodeName,
    started,
  });
  const snippet = getAgentSnippet({ agentStatus, member, turn: latestTurn, recommendation, healthReady, started });
  const statusTone = getAgentStatusTone(status);

  return (
    <article
      className={`min-w-0 rounded-xl border px-4 py-4 transition-colors ${
        active
          ? "border-accent bg-foreground/[0.025]"
          : featured
            ? "border-border-strong bg-surface"
            : "border-border bg-surface"
      }`}
    >
      <div className="flex min-w-0 items-start justify-between gap-4">
        <div className="flex min-w-0 items-start gap-3">
          <Monogram
            text={member.monogram}
            className={`h-12 w-12 text-[14px] ${
              featured ? "bg-accent text-accent-foreground" : "bg-foreground/[0.07] text-foreground"
            }`}
          />
          <div className="min-w-0">
            <div className="text-[18px] font-semibold leading-tight">{member.label}</div>
            <div className="mt-1 text-[12px] font-medium text-subtle-foreground">{member.role}</div>
          </div>
        </div>
        <div className={`inline-flex shrink-0 items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] font-semibold ${statusTone}`}>
          <span className={`h-1.5 w-1.5 rounded-full ${status.includes("Thinking") ? "animate-pulse bg-current" : "bg-current"}`} />
          {status}
        </div>
      </div>

      <div className="mt-4 flex flex-wrap items-center gap-2">
        <StanceBadge member={member} recommendation={recommendation} turn={latestTurn} />
        {agentStatus?.last_update && (
          <span className="inline-flex items-center rounded-full border border-border bg-background px-2 py-0.5 text-[11px] font-medium text-subtle-foreground">
            {agentStatus.last_update}
          </span>
        )}
        {active && (
          <span className="inline-flex items-center gap-1 rounded-full border border-warning/20 bg-warning-bg px-2 py-0.5 text-[11px] font-semibold text-warning">
            <Clock className="h-3 w-3" strokeWidth={2.25} />
            {NODE_LABEL[nodeName ?? ""] ?? "Working"}
          </span>
        )}
      </div>

      <p className={`mt-4 break-words font-semibold leading-snug ${featured ? "text-[20px]" : "text-[17px]"}`}>
        {getAgentHeadline(member, latestTurn, recommendation, agentStatus)}
      </p>
      <p className="mt-2 line-clamp-4 min-h-[72px] break-words text-[14px] leading-relaxed text-muted-foreground">
        {snippet}
      </p>

      {latestTurn?.key_points && latestTurn.key_points.length > 0 && (
        <ul className="mt-3 grid gap-1.5">
          {latestTurn.key_points.slice(0, featured ? 3 : 2).map((point) => (
            <li key={point} className="flex gap-2 text-[12px] leading-relaxed text-muted-foreground">
              <span className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-subtle-foreground" />
              <span className="break-words">{point}</span>
            </li>
          ))}
        </ul>
      )}
    </article>
  );
}

function StanceBadge({
  member,
  recommendation,
  turn,
}: {
  member: RosterMember;
  recommendation?: DebateState["recommendation"];
  turn?: TranscriptTurn;
}) {
  if (member.id === "cfo" && recommendation?.decision) {
    return <Pill className={decisionStyle(recommendation.decision)}>{recommendation.decision}</Pill>;
  }

  if (turn?.stance) {
    const stanceStyle = STANCE_STYLE[turn.stance as keyof typeof STANCE_STYLE];
    return (
      <Pill className={stanceStyle?.cls ?? "border-border text-muted-foreground"}>
        {stanceStyle?.label ?? turn.stance}
      </Pill>
    );
  }

  return (
    <span className="inline-flex items-center rounded-full border border-border bg-background px-2 py-0.5 text-[11px] font-medium text-subtle-foreground">
      {member.id === "cfo" ? "Chair" : "No stance yet"}
    </span>
  );
}

// eslint-disable-next-line @typescript-eslint/no-unused-vars
function DebateFloor({ transcript, running }: { transcript: TranscriptTurn[]; running: boolean }) {
  const rebuttals = transcript.filter((turn) => turn.type === "rebuttal");

  return (
    <section className="rounded-2xl border border-border bg-surface p-5 shadow-sm">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <SectionTitle>Cross-examination floor</SectionTitle>
          <h2 className="mt-1 text-[22px] font-semibold tracking-tight">Live challenge stream</h2>
        </div>
        {running && (
          <div className="inline-flex items-center gap-2 rounded-full border border-warning/20 bg-warning-bg px-3 py-1.5 text-[12px] font-semibold text-warning">
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
            Listening for committee turns
          </div>
        )}
      </div>

      {rebuttals.length === 0 ? (
        <p className="mt-5 rounded-xl border border-dashed border-border bg-background px-4 py-6 text-[14px] leading-relaxed text-muted-foreground">
          No cross-examination has streamed yet. The floor opens after each finance function files
          a live position.
        </p>
      ) : (
        <div className="mt-4 divide-y divide-border rounded-xl border border-border bg-background">
          {rebuttals.map((turn, index) => {
            const from = resolveMember(turn.from_role);
            const to = resolveMember(turn.to_role);
            return (
              <div key={`${turn.from_role}-${turn.to_role}-${index}`} className="grid gap-3 px-4 py-3 md:grid-cols-[240px_1fr]">
                <div className="flex min-w-0 flex-wrap items-center gap-1.5 text-[12px] font-semibold text-muted-foreground">
                  <span className="rounded-md bg-surface px-2 py-1">{from?.label ?? turn.from_role}</span>
                  <span className="text-subtle-foreground">to</span>
                  <span className="rounded-md bg-surface px-2 py-1">{to?.label ?? turn.to_role}</span>
                </div>
                <p className="break-words text-[14px] leading-relaxed text-foreground">{turn.point}</p>
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}

// eslint-disable-next-line @typescript-eslint/no-unused-vars
function ResolutionCommand({ rec }: { rec: NonNullable<DebateState["recommendation"]> }) {
  const impact = rec.impact;

  return (
    <section className="rounded-2xl border border-border bg-surface p-5 shadow-sm">
      <div className="flex flex-col gap-3 border-b border-border pb-4 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <SectionTitle>Committee resolution</SectionTitle>
          <h2 className="mt-1 text-[24px] font-semibold tracking-tight">CFO ruling</h2>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Pill className={`${decisionStyle(rec.decision)} text-[12px]`}>{rec.decision}</Pill>
          {typeof rec.confidence === "number" && (
            <span className="rounded-full border border-border bg-background px-3 py-1 text-[12px] font-semibold text-muted-foreground tabular-nums">
              {rec.confidence}% confidence
            </span>
          )}
        </div>
      </div>

      {rec.rationale && (
        <p className="mt-4 max-w-[1000px] text-[16px] leading-relaxed text-foreground">{rec.rationale}</p>
      )}

      {impact && typeof impact.scenario_runway_months === "number" && (
        <div className="mt-5 grid gap-3 rounded-xl border border-border bg-background p-4 sm:grid-cols-3">
          <Metric label="Runway today" value={fmtMonths(impact.current_runway_months)} />
          <Metric label="After this decision" value={fmtMonths(impact.scenario_runway_months)} />
          <Metric
            label="Runway impact"
            value={fmtSignedMonths(impact.delta_months)}
            tone={(impact.delta_months ?? 0) < 0 ? "risk" : "positive"}
          />
        </div>
      )}

      <div className="mt-5 grid gap-4 md:grid-cols-2">
        {rec.key_risks && rec.key_risks.length > 0 && (
          <ResolutionList title="Key risks" items={rec.key_risks} dot="bg-risk" />
        )}
        {rec.conditions && rec.conditions.length > 0 && (
          <ResolutionList title="Conditions" items={rec.conditions} dot="bg-info" />
        )}
      </div>
    </section>
  );
}

function LiveEventPanel({
  events,
  nodeName,
  running,
  transcript,
}: {
  events: ObservabilityEvent[];
  nodeName?: string;
  running: boolean;
  transcript: TranscriptTurn[];
}) {
  const latestTurns = transcript.slice(-4).map((turn, index) => ({
    id: `${turn.agent ?? turn.from_role ?? "turn"}-${index}`,
    at: turn.agent ? "agent" : "debate",
    label: turn.headline || turn.point || `${resolveThreadLabel(turn)} updated the thread`,
    tone: turn.type === "rebuttal" ? "risk" : "positive",
  }));
  const latestEvents = events.slice(-5).map((event, index) => ({
    id: event.id ?? `${event.label}-${index}`,
    at: event.at ?? "--",
    label: `${event.sponsor ?? "Atlas"}: ${event.label ?? event.event ?? "event"}`,
    tone: event.tone ?? "info",
  }));
  const rows = [...latestTurns, ...latestEvents].slice(-5).reverse();

  return (
    <aside className="rounded-lg border border-border bg-surface p-3 shadow-sm">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h2 className="text-[14px] font-semibold">Event Timeline</h2>
          <div className="text-[11px] text-muted-foreground">(Live)</div>
        </div>
        <span className="inline-flex items-center gap-1 rounded-md border border-border bg-background px-2 py-1 text-[11px] text-muted-foreground">
          Auto-scroll
          <span className={`h-2 w-2 rounded-full ${running ? "animate-pulse bg-info" : "bg-subtle-foreground"}`} />
        </span>
      </div>

      <div className="mt-3 space-y-2">
        <RailEvent
          label={running ? NODE_LABEL[nodeName ?? ""] ?? "Council running" : "Council room ready"}
          detail={running ? "Graph state is streaming through AG-UI." : "Submit a decision to begin a live run."}
          tone={running ? "info" : "positive"}
        />
        {rows.length === 0 ? (
          <p className="rounded-md border border-dashed border-border bg-background px-3 py-6 text-[12px] leading-relaxed text-muted-foreground">
            Live council and sponsor events appear here as the graph runs.
          </p>
        ) : (
          rows.map((row) => (
            <RailEvent key={row.id} label={row.label} detail={row.at} tone={row.tone} />
          ))
        )}
      </div>
    </aside>
  );
}

function RailMetric({
  delta,
  label,
  spark = false,
  value,
  warn = false,
}: {
  delta?: string;
  label: string;
  spark?: boolean;
  value: string;
  warn?: boolean;
}) {
  return (
    <div className="min-w-0 border-l border-border px-2 first:border-l-0">
      <div className="text-[10px] text-muted-foreground">{label}</div>
      <div className="mt-1 break-words text-[16px] font-semibold tabular-nums">{value}</div>
      {spark ? <MiniSparkline /> : delta ? <div className={`mt-1 text-[11px] ${warn ? "text-risk" : "text-positive"}`}>{delta}</div> : null}
    </div>
  );
}

function RailEvent({ label, detail, tone }: { label: string; detail: string; tone?: string }) {
  const dot =
    tone === "positive"
      ? "bg-positive"
      : tone === "warning"
        ? "bg-warning"
        : tone === "risk"
          ? "bg-risk"
          : "bg-info";
  return (
    <div className="flex min-w-0 gap-2">
      <span className={`mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full ${dot}`} />
      <div className="min-w-0">
        <div className="truncate text-[12px] font-semibold">{label}</div>
        <div className="break-words text-[11px] leading-relaxed text-muted-foreground">{detail}</div>
      </div>
    </div>
  );
}

function formatTelemetry(value?: number, unit?: string) {
  if (typeof value !== "number") return "Waiting";
  const formatted = value >= 1000 ? value.toLocaleString() : String(value);
  return unit ? `${formatted}${unit}` : formatted;
}

function reliabilityDimensionsFromScore(score: ReliabilityScore) {
  return {
    outcome_accuracy: score.outcome_accuracy,
    evidence_grounding: score.evidence_grounding,
    forecast_calibration: score.forecast_calibration,
    policy_compliance: score.policy_compliance,
    debate_value: score.debate_value,
    confidence_calibration: score.confidence_calibration,
    trace_quality: score.trace_quality,
  };
}

function resolveReliabilityValue(agentStatus?: AgentStatus, score?: ReliabilityScore) {
  const raw = score?.reliability ?? agentStatus?.reliability_score;
  return typeof raw === "number" && Number.isFinite(raw) ? Math.max(0, Math.min(100, Math.round(raw))) : undefined;
}

function averageReliability(scores: ReliabilityScore[]) {
  if (!scores.length) return undefined;
  return Math.round(scores.reduce((sum, score) => sum + score.reliability, 0) / scores.length);
}

function formatReliability(value?: number) {
  return typeof value === "number" ? `${value}%` : "Pending";
}

function reliabilityColor(value: number) {
  if (value >= 85) return "var(--positive)";
  if (value >= 70) return "var(--info)";
  if (value >= 55) return "var(--warning)";
  return "var(--risk)";
}

function formatDimensionLabel(key: string) {
  return key
    .replace(/_/g, " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

// eslint-disable-next-line @typescript-eslint/no-unused-vars
function EventTimeline({
  health,
  healthReady,
  nodeName,
  phase,
  recommendation,
  running,
  transcript,
}: {
  health: HealthView;
  healthReady: boolean;
  nodeName?: string;
  phase?: string;
  recommendation?: DebateState["recommendation"];
  running: boolean;
  transcript: TranscriptTurn[];
}) {
  const steps = buildTimeline({ health, healthReady, nodeName, phase, recommendation, running, transcript });

  return (
    <section className="rounded-2xl border border-border bg-surface p-4 shadow-sm">
      <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <SectionTitle>Event timeline</SectionTitle>
          <h2 className="mt-1 text-[20px] font-semibold tracking-tight">Graph progress</h2>
        </div>
        <div className="text-[12px] font-medium text-muted-foreground">
          intake - analysts - debate - synthesis - persist
        </div>
      </div>

      <div className="mt-4 overflow-x-auto">
        <div className="grid min-w-[980px] grid-cols-8 gap-2">
          {steps.map((step) => (
            <div key={step.id} className="relative rounded-xl border border-border bg-background px-3 py-3">
              <div className="flex items-center justify-between gap-2">
                <TimelineDot status={step.status} />
                <span className="text-[11px] font-semibold uppercase tracking-[0.08em] text-subtle-foreground">
                  {step.kind}
                </span>
              </div>
              <div className="mt-3 text-[13px] font-semibold leading-snug">{step.label}</div>
              <div className="mt-1 text-[11px] font-medium text-muted-foreground">{timelineLabel(step.status)}</div>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

function TimelineDot({ status }: { status: TimelineStatus }) {
  const cls =
    status === "complete"
      ? "bg-positive text-positive"
      : status === "active"
        ? "animate-pulse bg-warning text-warning"
        : status === "blocked"
          ? "bg-risk text-risk"
          : "bg-border-strong text-subtle-foreground";

  return <span className={`h-2.5 w-2.5 rounded-full ${cls}`} />;
}

function Metric({ label, value, tone }: { label: string; value: string; tone?: "risk" | "positive" }) {
  const cls = tone === "risk" ? "text-risk" : tone === "positive" ? "text-positive" : "text-foreground";
  return (
    <div>
      <div className="text-[11px] font-semibold uppercase tracking-[0.08em] text-subtle-foreground">{label}</div>
      <div className={`mt-1 text-[24px] font-semibold leading-none tabular-nums ${cls}`}>{value}</div>
    </div>
  );
}

function ResolutionList({ title, items, dot }: { title: string; items: string[]; dot: string }) {
  return (
    <div className="rounded-xl border border-border bg-background p-4">
      <div className="text-[11px] font-semibold uppercase tracking-[0.08em] text-subtle-foreground">{title}</div>
      <ul className="mt-3 space-y-2">
        {items.map((item) => (
          <li key={item} className="flex gap-2 text-[13px] leading-relaxed text-muted-foreground">
            <span className={`mt-2 h-1.5 w-1.5 shrink-0 rounded-full ${dot}`} />
            <span className="break-words">{item}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function getCurrentPhaseLabel({
  health,
  healthReady,
  nodeName,
  phase,
  recommendation,
  running,
}: {
  health: HealthView;
  healthReady: boolean;
  nodeName?: string;
  phase?: string;
  recommendation?: DebateState["recommendation"];
  running: boolean;
}) {
  if (!healthReady) {
    return health.status === "loading" ? "Strict preflight checking" : "Strict preflight blocked";
  }
  if (running) return NODE_LABEL[nodeName ?? ""] ?? "Council deliberating";
  if (recommendation?.decision) return "Recommendation issued";
  if (phase) return PHASE_LABEL[phase] ?? phase;
  return "Awaiting decision";
}

function getHealthLabel(health: HealthView) {
  if (health.status === "ready") return "Ready";
  if (health.status === "loading") return "Checking";
  if (health.status === "blocked") return "Blocked";
  return "Unavailable";
}

function getSponsorRows(health: HealthView): SponsorView[] {
  return SPONSOR_DEFAULTS.map((fallback) => {
    const live = health.data?.sponsors?.find((item) => item.id === fallback.id);
    const envOpenAI = fallback.id === "openai" ? health.data?.env?.find((item) => item.id === "openai_api_key") : undefined;
    const source = live ?? envOpenAI;
    const status: SponsorStatus =
      health.status === "loading"
        ? "checking"
        : source
          ? source.ready
            ? "ready"
            : "blocked"
          : health.status === "ready"
            ? "blocked"
            : "blocked";

    return {
      id: fallback.id,
      label: source?.label ?? fallback.label,
      detail: source?.detail ?? fallback.detail,
      error: source?.error,
      url: source?.url,
      status,
      checks: source?.checks,
      capabilities: source?.capabilities,
      realtime: source?.realtime,
      sandbox: source?.sandbox,
      modules: source?.modules,
      indices: source?.indices,
      streams: source?.streams,
      model: source?.model,
      reasoning_effort: source?.reasoning_effort,
      verbosity: source?.verbosity,
      icon: fallback.icon,
    };
  });
}

function latestSpeakerId(transcript: TranscriptTurn[]) {
  for (let index = transcript.length - 1; index >= 0; index -= 1) {
    const turn = transcript[index];
    if (turn.agent && ROSTER_BY_ID[turn.agent]) return turn.agent;
    if (turn.type === "framing" || turn.type === "decision") return "cfo";
  }
  return undefined;
}

function findLatestTurnForMember(memberId: string, transcript: TranscriptTurn[]) {
  for (let index = transcript.length - 1; index >= 0; index -= 1) {
    const turn = transcript[index];
    if (memberId === "cfo" && (turn.agent === "cfo" || turn.type === "framing" || turn.type === "decision")) {
      return turn;
    }
    if (turn.agent === memberId) return turn;
  }
  return undefined;
}

function getAgentStatus({
  active,
  agentStatus,
  healthReady,
  latestSpeaker,
  latestTurn,
  member,
  nodeName,
  started,
}: {
  active: boolean;
  agentStatus?: AgentStatus;
  healthReady: boolean;
  latestSpeaker?: string;
  latestTurn?: TranscriptTurn;
  member: RosterMember;
  nodeName?: string;
  started: boolean;
}) {
  if (!healthReady) return "Preflight blocked";
  const backendStatus = String(agentStatus?.status ?? "").toLowerCase();
  if (backendStatus === "thinking" || backendStatus === "running") return "Thinking";
  if (backendStatus === "speaking") return "Speaking";
  if (backendStatus === "done" || backendStatus === "complete") return "On record";
  if (backendStatus === "error") return "Error";
  if (backendStatus === "warning" || backendStatus === "blocked") return "Blocked";
  if (active) {
    const currentOutput =
      latestTurn &&
      (latestTurn.agent === nodeName ||
        (nodeName === "intake" && latestTurn.type === "framing") ||
        (nodeName === "synthesis" && latestTurn.type === "decision"));
    return currentOutput ? "Speaking" : "Thinking";
  }
  if (latestSpeaker === member.id) return "Last spoke";
  if (latestTurn) return "On record";
  if (started) return "Queued";
  return "Standing by";
}

function getAgentStatusTone(status: string) {
  if (status === "Speaking" || status === "Last spoke") return "border-positive/20 bg-positive-bg text-positive";
  if (status === "Thinking") return "border-warning/20 bg-warning-bg text-warning";
  if (status === "Preflight blocked" || status === "Error") return "border-risk/20 bg-risk-bg text-risk";
  if (status === "Blocked") return "border-warning/20 bg-warning-bg text-warning";
  if (status === "On record") return "border-info/20 bg-info-bg text-info";
  return "border-border bg-background text-muted-foreground";
}

function getAgentHeadline(
  member: RosterMember,
  turn?: TranscriptTurn,
  recommendation?: DebateState["recommendation"],
  agentStatus?: AgentStatus,
) {
  if (member.id === "cfo" && recommendation?.decision) {
    return `${recommendation.decision} at ${recommendation.confidence ?? "--"}% confidence`;
  }
  return agentStatus?.headline || turn?.headline || member.mandate || "Awaiting live council output";
}

function getAgentSnippet({
  agentStatus,
  member,
  turn,
  recommendation,
  healthReady,
  started,
}: {
  agentStatus?: AgentStatus;
  member: RosterMember;
  turn?: TranscriptTurn;
  recommendation?: DebateState["recommendation"];
  healthReady: boolean;
  started: boolean;
}) {
  if (member.id === "cfo" && recommendation?.rationale) return recommendation.rationale;
  if (agentStatus?.detail && agentStatus.detail !== "Awaiting council turn") return agentStatus.detail;
  if (turn?.argument) return turn.argument;
  if (turn?.point) return turn.point;
  if (!healthReady) return "Strict live preflight must pass before this seat can produce a live utterance.";
  if (started) return "Queued in the graph. No live utterance has streamed for this seat yet.";
  return "Ready to join once a decision command is submitted.";
}

function buildTimeline({
  health,
  healthReady,
  nodeName,
  phase,
  recommendation,
  running,
  transcript,
}: {
  health: HealthView;
  healthReady: boolean;
  nodeName?: string;
  phase?: string;
  recommendation?: DebateState["recommendation"];
  running: boolean;
  transcript: TranscriptTurn[];
}) {
  const preflightStatus: TimelineStatus = healthReady ? "complete" : health.status === "loading" ? "active" : "blocked";
  const hasFraming = transcript.some((turn) => turn.type === "framing");
  const hasDebate = transcript.some((turn) => turn.type === "rebuttal");
  const hasReliability = transcript.some((turn) => turn.type === "reliability");
  const hasAgent = (agent: string) => transcript.some((turn) => turn.agent === agent && turn.type === "position");

  return [
    {
      id: "preflight",
      kind: "gate",
      label: "Strict preflight",
      status: preflightStatus,
    },
    {
      id: "intake",
      kind: "node",
      label: "Intake",
      status: timelineStatus({ complete: hasFraming, healthReady, id: "intake", nodeName, running }),
    },
    {
      id: "treasury",
      kind: "agent",
      label: "Treasury",
      status: timelineStatus({ complete: hasAgent("treasury"), healthReady, id: "treasury", nodeName, running }),
    },
    {
      id: "fpna",
      kind: "agent",
      label: "FP&A",
      status: timelineStatus({ complete: hasAgent("fpna"), healthReady, id: "fpna", nodeName, running }),
    },
    {
      id: "risk",
      kind: "agent",
      label: "Risk & Audit",
      status: timelineStatus({ complete: hasAgent("risk"), healthReady, id: "risk", nodeName, running }),
    },
    {
      id: "procurement",
      kind: "agent",
      label: "Procurement",
      status: timelineStatus({ complete: hasAgent("procurement"), healthReady, id: "procurement", nodeName, running }),
    },
    {
      id: "debate",
      kind: "node",
      label: "Cross-exam",
      status: timelineStatus({ complete: hasDebate, healthReady, id: "debate", nodeName, running }),
    },
    {
      id: "synthesis",
      kind: "node",
      label: phase === "done" ? "Persisted" : "CFO ruling",
      status: timelineStatus({
        complete: Boolean(recommendation?.decision) || phase === "done",
        healthReady,
        id: nodeName === "persist" ? "persist" : "synthesis",
        nodeName,
        running,
      }),
    },
    {
      id: "reliability",
      kind: "node",
      label: "Reliability eval",
      status: timelineStatus({
        complete: hasReliability || phase === "done",
        healthReady,
        id: "reliability",
        nodeName,
        running,
      }),
    },
  ] satisfies Array<{ id: string; kind: string; label: string; status: TimelineStatus }>;
}

function buildReferenceProgress(steps: Array<{ id: string; label: string; status: TimelineStatus }>) {
  const byId = Object.fromEntries(steps.map((step) => [step.id, step.status]));
  const analystStatuses = ["treasury", "fpna", "risk", "procurement"].map((id) => byId[id]);
  const analysisStatus: TimelineStatus = analystStatuses.every((status) => status === "complete")
    ? "complete"
    : analystStatuses.some((status) => status === "active")
      ? "active"
      : "pending";

  return [
    { label: "Briefing", status: byId.intake ?? byId.preflight ?? "pending" },
    { label: "Analysis", status: analysisStatus },
    { label: "Debate", status: byId.debate ?? "pending" },
    { label: "Ruling", status: byId.synthesis ?? "pending" },
    { label: "Evals", status: byId.reliability ?? "pending" },
  ] satisfies Array<{ label: string; status: TimelineStatus }>;
}

function timelineStatus({
  complete,
  healthReady,
  id,
  nodeName,
  running,
}: {
  complete: boolean;
  healthReady: boolean;
  id: string;
  nodeName?: string;
  running: boolean;
}) {
  if (complete) return "complete";
  if (!healthReady) return "pending";
  if (running && nodeName === id) return "active";
  return "pending";
}

function timelineLabel(status: TimelineStatus) {
  if (status === "complete") return "Complete";
  if (status === "active") return "Active";
  if (status === "blocked") return "Blocked";
  return "Pending";
}
