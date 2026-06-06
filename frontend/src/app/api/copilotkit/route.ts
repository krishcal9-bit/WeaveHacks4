import {
  CopilotRuntime,
  ExperimentalEmptyAdapter,
  copilotRuntimeNextJSAppRouterEndpoint,
} from "@copilotkit/runtime";
import { LangGraphHttpAgent } from "@copilotkit/runtime/langgraph";
import { NextRequest } from "next/server";

// The CopilotKit runtime proxies browser requests to the Python LangGraph
// agent (FastAPI, AG-UI protocol) running on PORT 8123. We use the empty
// service adapter because all reasoning happens inside the LangGraph agent.
const serviceAdapter = new ExperimentalEmptyAdapter();

const runtime = new CopilotRuntime({
  agents: {
    finance_department: new LangGraphHttpAgent({
      url: process.env.AGENT_URL || "http://localhost:8123",
    }),
  },
});

export const POST = async (req: NextRequest) => {
  const { handleRequest } = copilotRuntimeNextJSAppRouterEndpoint({
    runtime,
    serviceAdapter,
    endpoint: "/api/copilotkit",
  });

  return handleRequest(req);
};
