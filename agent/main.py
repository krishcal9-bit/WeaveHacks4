"""
Atlas — AI finance department agent server.

FastAPI app exposing the LangGraph graph over the AG-UI protocol so the
CopilotKit runtime (Next.js /api/copilotkit) can drive it. W&B Weave tracing is
initialized at startup (mandatory for judging) and auto-instruments OpenAI;
@weave.op spans in src/agent.py give named per-node traces.
"""

import os
import warnings

from fastapi import FastAPI
import uvicorn

from src.env import load_env, redact_secrets
from src.health import mark_copilotkit_mounted, set_weave_status

load_env()


def _init_weave() -> None:
    """Start Weave tracing and record strict-live health status."""
    project = os.getenv("WANDB_PROJECT")
    entity = os.getenv("WANDB_ENTITY")
    if not os.getenv("WANDB_API_KEY") or not project:
        set_weave_status(initialized=False, error="WANDB_API_KEY and WANDB_PROJECT must be configured.")
        return
    try:
        import weave

        weave.init(f"{entity}/{project}" if entity else project)
        set_weave_status(initialized=True, error=None)
        print("[atlas] Weave tracing initialized.")
    except Exception as exc:
        msg = f"Weave init failed: {exc.__class__.__name__}: {redact_secrets(exc)}"
        set_weave_status(initialized=False, error=msg)
        print("[atlas] Weave tracing init failed; strict-live preflight will fail.")


_init_weave()

# Imported after Weave init so OpenAI auto-instrumentation is in place.
from src.agent import graph  # noqa: E402
from src.api import router as data_router  # noqa: E402
from copilotkit import LangGraphAGUIAgent  # noqa: E402
from ag_ui_langgraph import add_langgraph_fastapi_endpoint  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402


app = FastAPI(title="Atlas Finance Department Agent")

# Allow the Next.js dev frontend to read dashboard data cross-origin.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(data_router)

add_langgraph_fastapi_endpoint(
    app=app,
    agent=LangGraphAGUIAgent(
        name="finance_department",
        description=(
            "An AI finance department (CFO, Treasury, FP&A, Risk/Audit, Procurement) "
            "that analyzes, debates, and decides on financial decisions."
        ),
        graph=graph,
    ),
    path="/",
)
mark_copilotkit_mounted(path="/", agent_name="finance_department")


def main():
    """Run the uvicorn server."""
    port = int(os.getenv("PORT", "8123"))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)


warnings.filterwarnings("ignore", category=UserWarning, module="pydantic")

if __name__ == "__main__":
    main()
