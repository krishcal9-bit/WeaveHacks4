import type { RealtimeView } from "@/lib/council";
import type { RealtimeSession } from "@/lib/types";

const OPENAI_REALTIME_URL = "https://api.openai.com/v1/realtime";

const COUNCIL_VOICE_TOOLS = [
  {
    type: "function",
    name: "submit_decision_to_council",
    description:
      "Send a finance decision question to the live Atlas AI council debate. Call this when the operator asks the council to decide something.",
    parameters: {
      type: "object",
      properties: {
        decision: {
          type: "string",
          description: "The decision question for Treasury, FP&A, Risk, Procurement, and the CFO.",
        },
      },
      required: ["decision"],
    },
  },
] as const;

export type RealtimeVoiceCallbacks = {
  onStatus: (view: RealtimeView) => void;
  onTranscript?: (text: string, role: "user" | "assistant") => void;
  onSubmitDecision?: (decision: string) => void | Promise<void>;
};

export type RealtimeVoiceHandle = {
  stop: () => void;
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

function sendSessionUpdate(channel: RTCDataChannel, session: RealtimeSession) {
  channel.send(
    JSON.stringify({
      type: "session.update",
      session: {
        type: "realtime",
        output_modalities: ["audio"],
        instructions:
          "You are the Atlas council operator voice. Keep replies short. When the operator states a decision for the finance council to debate, call submit_decision_to_council with their exact question. Otherwise answer briefly about how the council works.",
        audio: {
          input: {
            transcription: { model: "whisper-1" },
            turn_detection: { type: "semantic_vad", eagerness: "medium", interrupt_response: true },
          },
          output: { voice: session.voice },
        },
        tools: COUNCIL_VOICE_TOOLS,
        tool_choice: "auto",
      },
    }),
  );
}

function parseRealtimeEvent(raw: string): Record<string, unknown> | null {
  try {
    const payload = JSON.parse(raw) as Record<string, unknown>;
    return payload && typeof payload === "object" ? payload : null;
  } catch {
    return null;
  }
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
  const media = await navigator.mediaDevices.getUserMedia({ audio: true });
  const [track] = media.getAudioTracks();
  if (!track) throw new Error("Microphone track unavailable");

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

    if (type === "conversation.item.input_audio_transcription.completed") {
      const transcript = String((payload.transcript as string | undefined) ?? "").trim();
      if (transcript) {
        callbacks.onTranscript?.(transcript, "user");
        callbacks.onStatus({
          status: "connected",
          detail: `Heard: ${transcript}`,
          model: session.model,
          voice: session.voice,
        });
      }
      return;
    }

    if (
      type === "response.output_audio_transcript.done" ||
      type === "response.audio_transcript.done" ||
      type === "response.output_text.done"
    ) {
      const transcript = String((payload.transcript as string | undefined) ?? (payload.text as string | undefined) ?? "").trim();
      if (transcript) callbacks.onTranscript?.(transcript, "assistant");
      return;
    }

    if (type === "response.function_call_arguments.done") {
      const name = String(payload.name ?? "");
      if (name !== "submit_decision_to_council") return;
      try {
        const args = JSON.parse(String(payload.arguments ?? "{}")) as { decision?: string };
        const decision = args.decision?.trim();
        if (decision) void callbacks.onSubmitDecision?.(decision);
      } catch {
        // ignore malformed tool args
      }
    }
  };

  const offer = await peer.createOffer();
  await peer.setLocalDescription(offer);

  const sdpRes = await fetch(`${OPENAI_REALTIME_URL}?model=${encodeURIComponent(session.model)}`, {
    method: "POST",
    body: offer.sdp ?? "",
    headers: {
      Authorization: `Bearer ${session.client_secret}`,
      "Content-Type": "application/sdp",
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

  callbacks.onStatus({
    status: "connected",
    detail: `Voice live — speak a decision for the council`,
    model: session.model,
    voice: session.voice,
  });

  if (!sessionReady) {
    dataChannel.send(
      JSON.stringify({
        type: "response.create",
        response: {
          output_modalities: ["audio"],
          instructions: "Briefly greet the operator and ask what decision the council should debate.",
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

  return { stop };
}
