# Atlas — Autonomous Finance Operations

## Project Overview

Atlas is an AI **finance department** for startups built for the **WeaveHacks 4 — Multi-Agent Orchestration** hackathon. It allows users to pose financial decisions (e.g., vendor renewals, hiring, capital commitments) to a committee of role-based agents (Treasury, FP&A, Risk & Audit, Procurement). These agents analyze the decision against real company numbers, debate it, and a CFO agent issues a quantified recommendation with runway/burn impact.

### Core Technologies
*   **Frontend:** Next.js 16, CopilotKit, Tailwind v4, Recharts.
*   **Backend:** Python (managed via `uv`), FastAPI, LangGraph, W&B Weave, OpenAI.
*   **Database/Memory:** Redis Stack (RedisJSON, RediSearch, vector RAG, Streams, Pub/Sub).

### Architecture
*   **Browser:** Next.js app using CopilotKit (`useCoAgent` / `sendMessage`).
*   **Proxy Route:** Next.js API at `/api/copilotkit` (CopilotRuntime → LangGraphHttpAgent).
*   **Agent Service:** FastAPI + LangGraph agent running on port 8123 (AG-UI). This service executes the multi-agent debate graph, traces node execution via Weave, and interacts heavily with Redis for context and persistence.

## Building and Running

**Prerequisites:** Node.js 18+, `uv`, Docker Desktop.

1.  **Environment Setup:**
    The project relies on a root `.env` file for all live credentials.
    ```bash
    cp agent/.env.example .env
    # Fill in OPENAI_API_KEY, WANDB_API_KEY, REDIS_URL, etc.
    ```

2.  **Automated Setup:**
    Run the repeatable setup script to start Redis via Docker, install dependencies, run preflight checks, and seed data. Docker Desktop must be running.
    ```bash
    scripts/live-setup.sh
    ```

3.  **Start the Application:**
    Start both the FastAPI backend and Next.js frontend concurrently.
    ```bash
    scripts/dev-live.sh
    ```
    *   UI: `http://localhost:3000`
    *   Agent: `http://localhost:8123`

*(Note: Manual execution of the individual scripts in the `scripts/` directory is also supported if the root `.env` is exported into the shell first).*

## Development Conventions

*   **Strict Live-Only Contract:** Atlas is a live sponsor integration demo. **DO NOT** use mocked LLM outputs, fake Weave traces, a non-Stack Redis server, browser-only fallbacks, or hard-coded responses.
*   **Secrets Management:** The root `.env` is the single source of truth for secrets. **NEVER** print, log, or commit secret values (e.g., `OPENAI_API_KEY`, `WANDB_API_KEY`).
*   **Tracing:** Every agent turn and model call must be traced using W&B Weave (`weave.init()` and `@weave.op` decorators for nodes like `intake`, `debate_round`, `cfo_synthesis`, etc.).
*   **Redis Requirement:** The demo heavily depends on Redis Stack features. Ensure `redis/redis-stack-server:latest` is used for local development.
*   **CopilotKit Alignment:** The frontend Next.js app proxies to the backend. Ensure `AGENT_URL` and `NEXT_PUBLIC_AGENT_URL` in your environment correctly point to the backend port.
*   **Next.js 16 Warnings:** The frontend uses Next.js 16, which may have breaking changes compared to older versions. Refer to local documentation in `node_modules/next/dist/docs/` when writing Next.js code.
*   **Workflow Truth:** Rely on the `scripts/` directory and existing `.cursor/rules/` for canonical setup and operational workflows.
