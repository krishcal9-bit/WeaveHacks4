"use client";

import { useEffect, useRef } from "react";
import { useCoAgent, useCopilotChat } from "@copilotkit/react-core";
import { useCopilotKit } from "@copilotkitnext/react";
import { randomUUID } from "@copilotkit/shared";
import { onDemoReset } from "@/lib/demo-reset";
import type { DebateState } from "@/lib/types";

const AGENT_NAME = "finance_department";

type ResetCopilotRun = () => void;

export function useResetCopilotRun(): ResetCopilotRun {
  const { copilotkit } = useCopilotKit();
  const { setState, stop } = useCoAgent<DebateState>({ name: AGENT_NAME });
  const { reset: resetChat } = useCopilotChat();

  return () => {
    stop?.();
    const agent = copilotkit.getAgent(AGENT_NAME);
    if (agent) {
      agent.abortRun();
      agent.threadId = randomUUID();
      agent.setMessages([]);
      agent.setState({});
    }
    resetChat();
    setState({} as DebateState);
  };
}

export function useDemoResetListener(onReset?: () => void) {
  const resetCopilotRun = useResetCopilotRun();
  const onResetRef = useRef(onReset);

  useEffect(() => {
    onResetRef.current = onReset;
  }, [onReset]);

  useEffect(() => {
    return onDemoReset(() => {
      resetCopilotRun();
      onResetRef.current?.();
    });
  }, [resetCopilotRun]);
}
