# Repository Guidelines

## Project Structure & Module Organization

Atlas is split into a live Python agent service and a Next.js frontend. `agent/` contains the FastAPI/LangGraph backend: `main.py` starts the AG-UI server, `src/agent.py` defines the finance-debate graph, `src/redis_layer.py` owns Redis access, `src/tools.py` holds grounded finance tools, and `src/data/seed.py` seeds Northwind Robotics data. `frontend/` contains the Next.js 16 app: routes live in `frontend/src/app/`, shared UI in `frontend/src/components/`, and API/type helpers in `frontend/src/lib/`. Operational scripts live in `scripts/`.

## Build, Test, and Development Commands

- `scripts/live-setup.sh`: repeatable full setup; starts Redis Stack, installs dependencies, runs preflight checks, and seeds live data.
- `scripts/dev-live.sh`: starts the FastAPI agent on `:8123` and the UI on `:3000`.
- `scripts/live-preflight.sh`: validates live environment, sponsor DNS, and Redis Stack readiness.
- `scripts/seed-live.sh`: loads Redis data and embeddings using live credentials.
- `npm --prefix frontend run lint`: runs Next.js/TypeScript ESLint.
- `npm --prefix frontend run build`: builds the frontend.
- `uv run --directory agent python main.py`: starts only the agent service.

## Coding Style & Naming Conventions

Use TypeScript strict mode in the frontend, path aliases via `@/*`, and kebab-case route/file names such as `runway-chart.tsx`. React components and exported types use PascalCase; helpers use camelCase. Python code follows standard 4-space indentation, snake_case functions, and typed Pydantic models. Keep Weave spans and Redis operations explicit and readable; avoid hiding sponsor-critical behavior behind broad abstractions.

## Testing Guidelines

No dedicated test runner is currently configured. Before handing off changes, run the strongest available checks: `npm --prefix frontend run lint`, `npm --prefix frontend run build`, and `scripts/live-preflight.sh` when live services are involved. If adding tests, place Python tests under `agent/tests/` as `test_*.py` and frontend tests beside the feature as `*.test.tsx`.

## Commit & Pull Request Guidelines

The short history uses descriptive, title-case commit subjects, for example `Build Atlas — AI finance department (WeaveHacks 4)`. Keep commits focused and mention the touched surface (`agent`, `frontend`, or `scripts`) when useful. PRs should include a concise summary, verification commands, linked issues if any, and screenshots or trace links for UI/agent behavior.

## Security & Configuration Tips

Atlas is a strict live sponsor demo. Do not add mocked LLM output, fake Weave traces, browser-only fallbacks, or hard-coded sponsor responses. Keep root `.env` as the local secret source and never commit or print `OPENAI_API_KEY`, `WANDB_API_KEY`, `REDIS_URL`, or derived secret values.
