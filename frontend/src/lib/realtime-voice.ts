import type { RealtimeView } from "@/lib/council";
import type { RealtimeSession, VoiceTranscriptEntry } from "@/lib/types";

const OPENAI_REALTIME_CALLS_URL = "https://api.openai.com/v1/realtime/calls";

// Fallback tool surface (the backend now sends the authoritative list, grounded
// in the live company data, via the minted session — these mirror it for safety).
const COUNCIL_VOICE_TOOLS = [
  {
    type: "function",
    name: "get_company_overview",
    description:
      "Get the operator's company identity, key financials, and a summary of the data/files they have uploaded. Call this whenever asked what the company is, what they do, their financial position/runway, or what data is available.",
    parameters: { type: "object", properties: {}, required: [] },
  },
  {
    type: "function",
    name: "search_company_knowledge",
    description:
      "Semantic search over the operator's uploaded documents/files and the company's finance policies and past board decisions. Use to answer specific questions grounded in the operator's own data.",
    parameters: {
      type: "object",
      properties: {
        query: {
          type: "string",
          description: "What to look up, phrased as a natural-language search query.",
        },
      },
      required: ["query"],
    },
  },
  {
    type: "function",
    name: "submit_decision_to_council",
    description:
      "Send a finance decision question to the live Atlas AI council debate. Call only when the operator clearly asks the council to decide something.",
    parameters: {
      type: "object",
      properties: {
        decision: {
          type: "string",
          description:
            "The decision question for Treasury, FP&A, Risk, Procurement, and the CFO — one clear sentence, ideally ending with a question mark.",
        },
      },
      required: ["decision"],
    },
  },
] as const;

const VOICE_KNOWLEDGE_TOOLS = new Set(["get_company_overview", "search_company_knowledge"]);

export type VoiceTranscriptUpdate = {
  id: string;
  role: VoiceTranscriptEntry["role"];
  text: string;
  final: boolean;
};

export type RealtimeVoiceCallbacks = {
  onStatus: (view: RealtimeView) => void;
  onTranscript?: (update: VoiceTranscriptUpdate) => void;
  onSubmitDecision?: (decision: string) => void | Promise<void>;
};

export type RealtimeVoiceHandle = {
  stop: () => void;
  setMicMuted: (muted: boolean) => void;
  isMicMuted: () => boolean;
};

function waitForPeerConnection(peer: RTCPeerConnection, timeoutMs = 15_000): Promise<void> {
  if (peer.connectionState === "connected") return Promise.resolve();

  return new Promise((resolve, reject) => {
    const timeout = window.setTimeout(() => {
      reject(new Error(`Realtime peer connection timed out (${peer.connectionState})`));
    }, timeoutMs);

    const onChange = () => {
      if (peer.connectionState === "connected") {
        window.clearTimeout(timeout);
        peer.removeEventListener("connectionstatechange", onChange);
        resolve();
      }
      if (["failed", "closed"].includes(peer.connectionState)) {
        window.clearTimeout(timeout);
        peer.removeEventListener("connectionstatechange", onChange);
        reject(new Error(`Realtime peer connection ${peer.connectionState}`));
      }
    };

    peer.addEventListener("connectionstatechange", onChange);
  });
}

function waitForDataChannel(channel: RTCDataChannel, timeoutMs = 10_000): Promise<void> {
  if (channel.readyState === "open") return Promise.resolve();

  return new Promise((resolve, reject) => {
    const timeout = window.setTimeout(() => {
      reject(new Error("Realtime data channel open timed out"));
    }, timeoutMs);

    channel.addEventListener(
      "open",
      () => {
        window.clearTimeout(timeout);
        resolve();
      },
      { once: true },
    );
    channel.addEventListener(
      "error",
      () => {
        window.clearTimeout(timeout);
        reject(new Error("Realtime data channel error"));
      },
      { once: true },
    );
  });
}

// Fallback only — the backend sends instructions grounded in the live company
// brief (identity, financials, uploaded files) with every minted session.
const COUNCIL_VOICE_INSTRUCTIONS =
  "You are Atlas Voice in the AI finance Decision Room, connected to the operator's live company data. " +
  "Keep replies short and wait for the operator to finish speaking before you respond. " +
  "You are connected to the company's data — never say you lack access. Use your tools: " +
  "call get_company_overview for 'what is my company about', what they do, financials, or what data is available; " +
  "call search_company_knowledge to answer specific questions grounded in their uploaded files and policies; " +
  "call submit_decision_to_council only when the operator clearly asks the council to decide something " +
  "(vendor renewals, hiring, capex, security blockers, pricing, financing). " +
  "If intent is ambiguous, ask one short clarifying question. Never invent numbers — ground answers in tool results.";

function buildTurnDetection() {
  return {
    type: "semantic_vad" as const,
    eagerness: "low" as const,
    create_response: true,
    interrupt_response: false,
  };
}

function buildRealtimeSessionConfig(session: RealtimeSession) {
  // Prefer the backend's live, company-grounded instructions + tool surface
  // (minted per-session from the Redis system of record). Fall back to the local
  // constants only if the backend did not supply them.
  const instructions =
    typeof session.instructions === "string" && session.instructions.trim()
      ? session.instructions
      : COUNCIL_VOICE_INSTRUCTIONS;
  const tools =
    Array.isArray(session.tools) && session.tools.length > 0
      ? (session.tools as unknown[])
      : COUNCIL_VOICE_TOOLS;
  return {
    type: "realtime",
    model: session.model,
    output_modalities: ["audio"],
    instructions,
    audio: {
      input: {
        transcription: { model: "whisper-1" },
        noise_reduction: { type: "far_field" },
        turn_detection: buildTurnDetection(),
      },
      output: { voice: session.voice },
    },
    tools,
    tool_choice: "auto",
  };
}

// Execute a knowledge tool call against the live backend (proxied to FastAPI),
// returning a JSON string the realtime model speaks from. Grounded — never faked.
async function executeVoiceTool(
  agentBase: string,
  name: string,
  args: Record<string, unknown>,
): Promise<string> {
  try {
    if (name === "get_company_overview") {
      const res = await fetch(`${agentBase}/api/realtime/context`, { cache: "no-store" });
      const data = (await res.json().catch(() => null)) as unknown;
      if (!res.ok || !data) {
        return JSON.stringify({ error: `Company context unavailable (${res.status}).` });
      }
      return JSON.stringify(data);
    }
    if (name === "search_company_knowledge") {
      const query = typeof args.query === "string" ? args.query : "";
      const res = await fetch(`${agentBase}/api/realtime/lookup`, {
        method: "POST",
        cache: "no-store",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query }),
      });
      const data = (await res.json().catch(() => null)) as unknown;
      if (!res.ok || !data) {
        return JSON.stringify({ error: `Knowledge lookup unavailable (${res.status}).` });
      }
      return JSON.stringify(data);
    }
  } catch (err) {
    return JSON.stringify({ error: err instanceof Error ? err.message : String(err) });
  }
  return JSON.stringify({ error: `Unknown tool: ${name}` });
}

// Return a function-call result to the model and ask it to respond (speak).
function sendToolOutput(channel: RTCDataChannel, callId: string, output: string) {
  channel.send(
    JSON.stringify({
      type: "conversation.item.create",
      item: { type: "function_call_output", call_id: callId, output },
    }),
  );
  channel.send(JSON.stringify({ type: "response.create" }));
}

function sendSessionUpdate(channel: RTCDataChannel, session: RealtimeSession) {
  channel.send(
    JSON.stringify({
      type: "session.update",
      session: buildRealtimeSessionConfig(session),
    }),
  );
}

async function waitForIceGathering(peer: RTCPeerConnection, timeoutMs = 5_000): Promise<void> {
  if (peer.iceGatheringState === "complete") return;

  await new Promise<void>((resolve) => {
    const timeout = window.setTimeout(() => {
      peer.removeEventListener("icegatheringstatechange", onChange);
      resolve();
    }, timeoutMs);

    const onChange = () => {
      if (peer.iceGatheringState === "complete") {
        window.clearTimeout(timeout);
        peer.removeEventListener("icegatheringstatechange", onChange);
        resolve();
      }
    };

    peer.addEventListener("icegatheringstatechange", onChange);
  });
}

function parseRealtimeEvent(raw: string): Record<string, unknown> | null {
  try {
    const payload = JSON.parse(raw) as Record<string, unknown>;
    return payload && typeof payload === "object" ? payload : null;
  } catch {
    return null;
  }
}

function connectedStatus(session: RealtimeSession, detail: string, extras?: Partial<RealtimeView>): RealtimeView {
  return {
    status: "connected",
    detail,
    model: session.model,
    voice: session.voice,
    micMuted: extras?.micMuted,
    listening: extras?.listening,
    speaking: extras?.speaking,
    processing: extras?.processing,
  };
}

export async function connectRealtimeVoice({
  agentBase,
  audioEl,
  callbacks,
}: {
  agentBase: string;
  audioEl: HTMLAudioElement;
  callbacks: RealtimeVoiceCallbacks;
}): Promise<RealtimeVoiceHandle> {
  if (typeof navigator === "undefined" || !navigator.mediaDevices?.getUserMedia) {
    throw new Error("This browser does not expose microphone capture.");
  }

  callbacks.onStatus({ status: "connecting", detail: "Minting OpenAI Realtime session..." });

  const sessionRes = await fetch(`${agentBase}/api/realtime/session`, { method: "POST", cache: "no-store" });
  const session = (await sessionRes.json().catch(() => null)) as RealtimeSession | null;
  if (!sessionRes.ok || !session?.client_secret) {
    throw new Error(sessionRes.status === 503 ? "Realtime session blocked by preflight" : `Realtime session failed (${sessionRes.status})`);
  }

  callbacks.onStatus({
    status: "connecting",
    detail: `Opening microphone on ${session.model}...`,
    model: session.model,
    voice: session.voice,
  });

  const peer = new RTCPeerConnection();
  const media = await navigator.mediaDevices.getUserMedia({
    audio: {
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
    },
  });
  const [track] = media.getAudioTracks();
  if (!track) throw new Error("Microphone track unavailable");

  let micMuted = false;
  peer.addTrack(track, media);

  peer.ontrack = (event) => {
    const [stream] = event.streams;
    if (!stream) return;
    audioEl.srcObject = stream;
    audioEl.muted = false;
    void audioEl.play().catch((err) => {
      callbacks.onStatus({
        status: "blocked",
        detail: err instanceof Error ? `Speaker playback blocked: ${err.message}` : "Speaker playback blocked",
        model: session.model,
        voice: session.voice,
      });
    });
  };

  const dataChannel = peer.createDataChannel("oai-events");
  let sessionReady = false;
  let listening = false;
  const userBuffers = new Map<string, string>();
  const assistantBuffers = new Map<string, string>();

  const pushStatus = (detail: string, extras?: Partial<RealtimeView>) => {
    callbacks.onStatus(connectedStatus(session, detail, { micMuted, listening, ...extras }));
  };

  const pushTranscript = (update: VoiceTranscriptUpdate) => {
    if (!update.text.trim()) return;
    callbacks.onTranscript?.(update);
  };

  dataChannel.onmessage = (event) => {
    const payload = parseRealtimeEvent(String(event.data));
    if (!payload) return;

    const type = String(payload.type ?? "");

    if (type === "session.created" || type === "session.updated") {
      sessionReady = true;
      return;
    }

    if (type === "error") {
      const message =
        (payload.error as { message?: string } | undefined)?.message ??
        (payload.message as string | undefined) ??
        "Realtime voice error";
      callbacks.onStatus({
        status: "blocked",
        detail: message,
        model: session.model,
        voice: session.voice,
      });
      return;
    }

    if (type === "input_audio_buffer.speech_started") {
      listening = true;
      pushStatus(micMuted ? "Mic muted — unmute to speak" : "Listening...", { listening: true, processing: false, speaking: false });
      return;
    }

    if (type === "input_audio_buffer.speech_stopped") {
      listening = false;
      pushStatus(micMuted ? "Mic muted" : "Processing speech...", { listening: false, processing: true, speaking: false });
      return;
    }

    if (type === "conversation.item.input_audio_transcription.delta") {
      const itemId = String(payload.item_id ?? "");
      const delta = String(payload.delta ?? "");
      if (!itemId || !delta) return;
      const next = `${userBuffers.get(itemId) ?? ""}${delta}`;
      userBuffers.set(itemId, next);
      pushTranscript({
        id: `user:${itemId}`,
        role: "user",
        text: next,
        final: false,
      });
      return;
    }

    if (type === "conversation.item.input_audio_transcription.completed") {
      const itemId = String(payload.item_id ?? "");
      const transcript = String((payload.transcript as string | undefined) ?? userBuffers.get(itemId) ?? "").trim();
      if (!transcript) return;
      if (itemId) userBuffers.delete(itemId);
      pushTranscript({
        id: `user:${itemId || transcript}`,
        role: "user",
        text: transcript,
        final: true,
      });
      pushStatus(micMuted ? "Mic muted" : "Voice live - speak a decision for the council", {
        listening: false,
        processing: false,
        speaking: false,
      });
      return;
    }

    if (
      type === "response.output_audio_transcript.delta" ||
      type === "response.audio_transcript.delta"
    ) {
      const responseId = String(payload.response_id ?? payload.item_id ?? "");
      const delta = String(payload.delta ?? "");
      if (!responseId || !delta) return;
      const next = `${assistantBuffers.get(responseId) ?? ""}${delta}`;
      assistantBuffers.set(responseId, next);
      pushStatus("Atlas is speaking...", { listening: false, processing: false, speaking: true });
      pushTranscript({
        id: `assistant:${responseId}`,
        role: "assistant",
        text: next,
        final: false,
      });
      return;
    }

    if (
      type === "response.output_audio_transcript.done" ||
      type === "response.audio_transcript.done" ||
      type === "response.output_text.done"
    ) {
      const responseId = String(payload.response_id ?? payload.item_id ?? "");
      const transcript = String(
        (payload.transcript as string | undefined) ??
          (payload.text as string | undefined) ??
          assistantBuffers.get(responseId) ??
          "",
      ).trim();
      if (transcript) {
        pushTranscript({
          id: `assistant:${responseId || transcript}`,
          role: "assistant",
          text: transcript,
          final: true,
        });
      }
      if (responseId) assistantBuffers.delete(responseId);
      pushStatus(micMuted ? "Mic muted" : "Voice live - speak a decision for the council", {
        listening: false,
        processing: false,
        speaking: false,
      });
      return;
    }

    if (type === "response.function_call_arguments.done") {
      const name = String(payload.name ?? "");
      const callId = String(payload.call_id ?? "");
      let args: Record<string, unknown> = {};
      try {
        args = JSON.parse(String(payload.arguments ?? "{}")) as Record<string, unknown>;
      } catch {
        args = {};
      }

      if (name === "submit_decision_to_council") {
        const decision = typeof args.decision === "string" ? args.decision.trim() : "";
        if (decision) void callbacks.onSubmitDecision?.(decision);
        if (callId) {
          sendToolOutput(
            dataChannel,
            callId,
            JSON.stringify({
              status: decision ? "submitted" : "no_decision",
              note: decision
                ? "The decision was sent to the live council. Briefly confirm to the operator that the council is now debating it."
                : "No clear decision was provided; ask the operator to restate it.",
            }),
          );
        }
        return;
      }

      if (VOICE_KNOWLEDGE_TOOLS.has(name)) {
        if (!callId) return;
        pushStatus("Atlas is checking your company data...", {
          listening: false,
          processing: true,
          speaking: false,
        });
        void executeVoiceTool(agentBase, name, args)
          .then((output) => sendToolOutput(dataChannel, callId, output))
          .catch((err) =>
            sendToolOutput(
              dataChannel,
              callId,
              JSON.stringify({ error: err instanceof Error ? err.message : String(err) }),
            ),
          );
        return;
      }
    }
  };

  const offer = await peer.createOffer();
  await peer.setLocalDescription(offer);
  await waitForIceGathering(peer);

  const formData = new FormData();
  formData.set("sdp", peer.localDescription?.sdp ?? offer.sdp ?? "");
  formData.set("session", JSON.stringify(buildRealtimeSessionConfig(session)));

  const sdpRes = await fetch(OPENAI_REALTIME_CALLS_URL, {
    method: "POST",
    body: formData,
    headers: {
      Authorization: `Bearer ${session.client_secret}`,
    },
  });

  if (!sdpRes.ok) {
    const detail = (await sdpRes.text().catch(() => "")).slice(0, 240);
    throw new Error(`Realtime SDP exchange failed (${sdpRes.status})${detail ? `: ${detail}` : ""}`);
  }

  await peer.setRemoteDescription({ type: "answer", sdp: await sdpRes.text() });
  await waitForPeerConnection(peer);
  await waitForDataChannel(dataChannel);
  sendSessionUpdate(dataChannel, session);

  pushStatus("Voice live - speak a decision for the council", { listening: false, processing: false, speaking: false });

  if (!sessionReady) {
    dataChannel.send(
      JSON.stringify({
        type: "response.create",
        response: {
          output_modalities: ["audio"],
          instructions:
            "Greet the operator in one short sentence and let them know you can answer questions " +
            "about their company and uploaded data, or take a decision to the council.",
        },
      }),
    );
  }

  const stop = () => {
    dataChannel.close();
    peer.close();
    media.getTracks().forEach((micTrack) => micTrack.stop());
    audioEl.srcObject = null;
  };

  const setMicMuted = (muted: boolean) => {
    micMuted = muted;
    track.enabled = !muted;
    pushStatus(
      muted
        ? "Mic muted - tap Voice to unmute"
        : listening
          ? "Listening..."
          : "Voice live - speak a decision for the council",
      { micMuted: muted, listening: !muted && listening, processing: false, speaking: false },
    );
  };

  peer.addEventListener("connectionstatechange", () => {
    if (["failed", "disconnected", "closed"].includes(peer.connectionState)) {
      callbacks.onStatus({
        status: "idle",
        detail: "Realtime voice disconnected",
        model: session.model,
        voice: session.voice,
      });
    }
  });

  return {
    stop,
    setMicMuted,
    isMicMuted: () => micMuted,
  };
}
