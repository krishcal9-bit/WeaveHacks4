"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useCoAgent, useCopilotAction, useCopilotChat } from "@copilotkit/react-core";
import { MessageRole, TextMessage } from "@copilotkit/runtime-client-gql";
import { api } from "@/lib/api";
import { ROSTER_BY_ID } from "@/lib/agents";
import {
  buildTimeline,
  getCurrentPhaseLabel,
  getSponsorRows,
  latestSpeakerId,
  NODE_TO_AGENT,
  type HealthPayload,
  type HealthView,
  type RealtimeView,
} from "@/lib/council";
import type {
  CommandResult,
  CommandState,
  DebateState,
  OperatorCommand,
  RealtimeSession,
} from "@/lib/types";
import { AgentInspector } from "@/components/decision-room/agent-inspector";
import { CouncilCommandPanel } from "@/components/council-command-panel";
import { AgentMatrix } from "@/components/decision-room/agent-matrix";
import { BoardMemo, ScenarioImpactCard } from "@/components/decision-room/board-memo";
import { CommandConsole } from "@/components/decision-room/command-console";
import { CouncilHeader, CouncilStatusBar, PreflightPanel } from "@/components/decision-room/council-chrome";
import { EvidenceDrawer } from "@/components/decision-room/evidence-drawer";
import { ReliabilityPanel } from "@/components/decision-room/reliability-panel";
import { SponsorEventRail, RedisActivityRail } from "@/components/decision-room/activity-rails";
import { SponsorHealthPanel } from "@/components/decision-room/sponsor-health";
import { TranscriptStream } from "@/components/decision-room/transcript-stream";

const AGENT_BASE = process.env.NEXT_PUBLIC_AGENT_URL || "http://localhost:8123";

export default function DecisionsPage() {
  const [input, setInput] = useState("");
  const [health, setHealth] = useState<HealthView>({ status: "loading", refreshing: true });
  const [selectedAgentId, setSelectedAgentId] = useState<string | null>(null);
  const [nowLabel, setNowLabel] = useState("");
  const [realtime, setRealtime] = useState<RealtimeView>({ status: "idle", detail: "Realtime 2 voice idle" });

  const realtimePeerRef = useRef<RTCPeerConnection | null>(null);
  const realtimeStreamRef = useRef<MediaStream | null>(null);
  const realtimeAudioRef = useRef<HTMLAudioElement | null>(null);
  const realtimeDataRef = useRef<RTCDataChannel | null>(null);

  const { state, setState, running, nodeName } = useCoAgent<DebateState>({ name: "finance_department" });
  const { appendMessage } = useCopilotChat();

  // Defensive reads — every field is optional and may arrive incrementally.
  const transcript = useMemo(() => state?.transcript ?? [], [state?.transcript]);
  const agentStatuses = state?.agent_statuses ?? [];
  const observabilityEvents = state?.observability_events ?? [];
  const traceSummary = state?.trace_summary;
  const redisActivity = state?.redis_activity ?? [];
  const recommendation = state?.recommendation;
  const reliabilityScores = state?.reliability_scores ?? [];
  const learningReport = state?.learning_report;
  const commands = state?.commands;
  const decision = state?.decision;
  const companyName = state?.context?.financials?.name ?? "the company";

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

  const timeline = useMemo(
    () =>
      buildTimeline({
        health,
        healthReady,
        nodeName,
        phase: state?.phase,
        recommendation,
        running,
        transcript,
      }),
    [health, healthReady, nodeName, state?.phase, recommendation, running, transcript],
  );

  const sponsorRows = useMemo(() => getSponsorRows(health), [health]);

  // Resolve the inspected seat: explicit selection → active roster seat → last speaker → CFO.
  const activeAgentId = NODE_TO_AGENT[nodeName ?? ""];
  const activeRosterId = activeAgentId && ROSTER_BY_ID[activeAgentId] ? activeAgentId : undefined;
  const candidateId = selectedAgentId ?? (running ? activeRosterId : undefined) ?? latestSpeakerId(transcript) ?? "cfo";
  const selectedMember = ROSTER_BY_ID[candidateId] ?? ROSTER_BY_ID.cfo;
  const selectedScore = reliabilityScores.find((score) => score.agent_id === selectedMember.id);
  const selectedStatus = agentStatuses.find((status) => status.id === selectedMember.id);

  // ----------------------------------------------------------------------- //
  // Health polling (every 15s) — locks submissions until strict-live green.
  // ----------------------------------------------------------------------- //
  const loadHealth = useCallback(async () => {
    setHealth((prev) => ({
      ...prev,
      status: prev.data || prev.error ? prev.status : "loading",
      refreshing: true,
    }));

    try {
      const res = await fetch(`${AGENT_BASE}/api/health`, { cache: "no-store" });
      const data = (await res.json().catch(() => null)) as HealthPayload | null;
      if (!data) throw new Error(`/api/health -> ${res.status}`);
      setHealth({ status: data.ready ? "ready" : "blocked", data, refreshing: false });
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

  // ----------------------------------------------------------------------- //
  // OpenAI Realtime 2 voice (WebRTC) — gated behind strict-live preflight.
  // ----------------------------------------------------------------------- //
  const stopRealtime = useCallback(() => {
    realtimeDataRef.current?.close();
    realtimePeerRef.current?.close();
    realtimeStreamRef.current?.getTracks().forEach((track) => track.stop());
    if (realtimeAudioRef.current) realtimeAudioRef.current.srcObject = null;
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
      const sessionRes = await fetch(`${AGENT_BASE}/api/realtime/session`, { method: "POST", cache: "no-store" });
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
            prev.status === "connected" ? { ...prev, status: "idle", detail: "Realtime voice disconnected" } : prev,
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
              instructions: "Greet the operator and ask which council agent they want to inspect or question.",
            },
          }),
        );
      };
      dataChannel.onmessage = (event) => {
        try {
          const payload = JSON.parse(String(event.data)) as { type?: string; transcript?: string; text?: string };
          if (payload.type?.includes("transcript") && (payload.transcript || payload.text)) {
            setRealtime((prev) => ({ ...prev, detail: payload.transcript || payload.text || prev.detail }));
          }
        } catch {
          // Ignore non-JSON realtime control frames.
        }
      };
      realtimeDataRef.current = dataChannel;

      const offer = await peer.createOffer();
      await peer.setLocalDescription(offer);
      const sdpRes = await fetch(`https://api.openai.com/v1/realtime?model=${encodeURIComponent(session.model)}`, {
        method: "POST",
        body: offer.sdp ?? "",
        headers: { Authorization: `Bearer ${session.client_secret}`, "Content-Type": "application/sdp" },
      });
      if (!sdpRes.ok) throw new Error(`Realtime SDP exchange failed: ${sdpRes.status}`);
      await peer.setRemoteDescription({ type: "answer", sdp: await sdpRes.text() });
      realtimePeerRef.current = peer;
    } catch (err) {
      stopRealtime();
      setRealtime({ status: "blocked", detail: err instanceof Error ? err.message : String(err) });
    }
  }, [healthReady, stopRealtime]);

  // ----------------------------------------------------------------------- //
  // Submission — strict-gated, streams via CopilotKit / AG-UI.
  // ----------------------------------------------------------------------- //
  const submit = useCallback(
    async (text: string) => {
      const content = text.trim();
      if (!content || running || !healthReady) return;
      setInput("");
      await appendMessage(new TextMessage({ role: MessageRole.User, content }));
    },
    [appendMessage, healthReady, running],
  );

  // ----------------------------------------------------------------------- //
  // AG-UI command-and-control — operator steering of the live council.
  // Commands execute server-side (council_commands.dispatch_command); the
  // authoritative result is mirrored into the shared coagent state so the panel
  // updates immediately, and the same eight keys stream back through DebateState
  // while a debate is running.
  // ----------------------------------------------------------------------- //
  const commandState: CommandState = useMemo(
    () => ({
      command_queue: state?.command_queue ?? [],
      active_command: state?.active_command ?? {},
      pinned_evidence: state?.pinned_evidence ?? [],
      requested_scenario: state?.requested_scenario ?? {},
      agent_focus: state?.agent_focus ?? {},
      phase_controls: state?.phase_controls ?? { paused: false },
      export_status: state?.export_status ?? { ready: false },
      command_audit_log: state?.command_audit_log ?? [],
    }),
    [
      state?.command_queue,
      state?.active_command,
      state?.pinned_evidence,
      state?.requested_scenario,
      state?.agent_focus,
      state?.phase_controls,
      state?.export_status,
      state?.command_audit_log,
    ],
  );

  const dispatchCommand = useCallback(
    async (command: OperatorCommand): Promise<CommandResult | undefined> => {
      try {
        const result = await api.command(command);
        if (result?.state) {
          // Mirror the server-authoritative command state into the shared agent
          // state (never fabricated — this is the dispatcher's own response).
          setState((prev) => ({ ...(prev ?? {}), ...result.state }) as DebateState);
        }
        return result;
      } catch (err) {
        console.error("Council command dispatch failed", err);
        return undefined;
      }
    },
    [setState],
  );

  // Keep the panel fresh from the server when idle (e.g. after a reload, or a
  // command issued elsewhere). Skipped while a run streams its own state.
  useEffect(() => {
    if (running) return;
    let cancelled = false;
    const sync = async () => {
      try {
        const snapshot = await api.commandState();
        if (cancelled || !snapshot?.state) return;
        setState((prev) => ({ ...(prev ?? {}), ...snapshot.state }) as DebateState);
      } catch {
        // best-effort; the panel still works from streamed state.
      }
    };
    const interval = window.setInterval(sync, 6000);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, [running, setState]);

  // Frontend actions: let the CopilotKit agent (chat / Realtime voice) drive the
  // same server-side command dispatcher. Handlers only transport — no business
  // logic runs in the browser.
  useCopilotAction({
    name: "askCouncilToClarify",
    description:
      "Ask a finance council role to clarify its position on the decision under review (roles: cfo, treasury, fpna, risk, procurement).",
    parameters: [
      { name: "agent", type: "string", description: "council role id", required: true },
      { name: "question", type: "string", description: "what to clarify", required: true },
    ],
    handler: async ({ agent, question }) => {
      const result = await dispatchCommand({
        type: "clarify",
        agent: String(agent),
        payload: { question: String(question), context: { decision: decision ?? "" } },
        source: "copilot",
      });
      return result?.message ?? "Clarify command dispatched.";
    },
  });

  useCopilotAction({
    name: "challengeCouncilClaim",
    description: "Challenge a specific finance council role to defend or revise a claim with figures.",
    parameters: [
      { name: "agent", type: "string", description: "council role id", required: true },
      { name: "point", type: "string", description: "the claim to challenge", required: true },
    ],
    handler: async ({ agent, point }) => {
      const result = await dispatchCommand({
        type: "challenge_claim",
        agent: String(agent),
        payload: { point: String(point), context: { decision: decision ?? "" } },
        source: "copilot",
      });
      return result?.message ?? "Challenge command dispatched.";
    },
  });

  useCopilotAction({
    name: "requestScenarioFork",
    description:
      "Project the company runway under a what-if scenario by adjusting monthly spend, one-time cost, and/or added monthly revenue.",
    parameters: [
      { name: "label", type: "string", description: "short scenario label", required: false },
      { name: "extra_monthly_spend", type: "number", description: "incremental recurring monthly cost", required: false },
      { name: "one_time_cost", type: "number", description: "upfront one-time cost", required: false },
      { name: "added_monthly_revenue", type: "number", description: "incremental monthly revenue", required: false },
    ],
    handler: async ({ label, extra_monthly_spend, one_time_cost, added_monthly_revenue }) => {
      const result = await dispatchCommand({
        type: "scenario_fork",
        payload: {
          label: label ? String(label) : "Scenario",
          extra_monthly_spend: Number(extra_monthly_spend ?? 0),
          one_time_cost: Number(one_time_cost ?? 0),
          added_monthly_revenue: Number(added_monthly_revenue ?? 0),
        },
        source: "copilot",
      });
      return result?.message ?? "Scenario command dispatched.";
    },
  });

  useCopilotAction({
    name: "pinCouncilEvidence",
    description:
      "Pin supporting evidence to the board record. kind is one of policy, vendor, financial, or custom.",
    parameters: [
      { name: "kind", type: "string", description: "policy|vendor|financial|custom", required: true },
      { name: "query", type: "string", description: "search text, field name, or note", required: true },
    ],
    handler: async ({ kind, query }) => {
      const text = String(query);
      const result = await dispatchCommand({
        type: "pin_evidence",
        payload: {
          kind: String(kind) as "policy" | "vendor" | "financial" | "custom",
          query: text,
          note: text,
          ref: text,
        },
        source: "copilot",
      });
      return result?.message ?? "Pin command dispatched.";
    },
  });

  useCopilotAction({
    name: "exportBoardMemo",
    description: "Assemble and export the board-ready memo once the council has issued a ruling.",
    parameters: [],
    handler: async () => {
      const result = await dispatchCommand({ type: "export_memo", payload: {}, source: "copilot" });
      return result?.message ?? "Export command dispatched.";
    },
  });

  return (
    <main className="flex min-h-full flex-col bg-background">
      <CouncilStatusBar
        healthReady={healthReady}
        learningReport={learningReport}
        nowLabel={nowLabel}
        reliabilityScores={reliabilityScores}
        sponsorRows={sponsorRows}
      />

      <CouncilHeader
        currentPhase={currentPhase}
        decision={decision}
        health={health}
        healthReady={healthReady}
        nodeName={nodeName}
        running={running}
        steps={timeline}
      />

      <div className="space-y-2 p-2 lg:p-3">
        {!healthReady && <PreflightPanel health={health} onRefresh={loadHealth} />}

        <CommandConsole
          input={input}
          onInput={setInput}
          onSubmit={submit}
          running={running}
          healthReady={healthReady}
          started={started}
          realtime={realtime}
          onStartRealtime={startRealtime}
          onStopRealtime={stopRealtime}
          commands={commands}
        />

        <div className="grid items-start gap-2 xl:grid-cols-[minmax(0,1fr)_minmax(340px,400px)]">
          <div className="min-w-0 space-y-2">
            <AgentMatrix
              agentStatuses={agentStatuses}
              healthReady={healthReady}
              nodeName={nodeName}
              onSelectAgent={setSelectedAgentId}
              recommendation={recommendation}
              reliabilityScores={reliabilityScores}
              running={running}
              selectedAgentId={selectedMember.id}
              started={started}
              transcript={transcript}
            />

            <TranscriptStream
              transcript={transcript}
              recommendation={recommendation}
              running={running}
              nodeName={nodeName}
              healthReady={healthReady}
              started={started}
            />

            <AgentInspector
              member={selectedMember}
              agentStatus={selectedStatus}
              reliabilityScore={selectedScore}
              transcript={transcript}
              recommendation={recommendation}
              redisActivity={redisActivity}
              learningReport={learningReport}
              nodeName={nodeName}
              running={running}
              healthReady={healthReady}
              started={started}
            />

            <EvidenceDrawer context={state?.context} started={started} />
          </div>

          <aside className="min-w-0 space-y-2">
            <CouncilCommandPanel
              healthReady={healthReady}
              running={running}
              decision={decision}
              recommendation={recommendation}
              transcript={transcript}
              commandState={commandState}
              dispatch={dispatchCommand}
            />
            <BoardMemo
              recommendation={recommendation}
              decision={decision}
              companyName={companyName}
              reliabilityScores={reliabilityScores}
              running={running}
              healthReady={healthReady}
              started={started}
            />
            <ScenarioImpactCard impact={recommendation?.impact} />
            <ReliabilityPanel
              reliabilityScores={reliabilityScores}
              learningReport={learningReport}
              traceSummary={traceSummary}
              running={running}
              started={started}
            />
            <SponsorHealthPanel sponsorRows={sponsorRows} health={health} />
            <SponsorEventRail events={observabilityEvents} />
            <RedisActivityRail activity={redisActivity} />
          </aside>
        </div>
      </div>
    </main>
  );
}
