# Atlas AG-UI Command-and-Control Layer

This document is the handoff for the CopilotKit / AG-UI **command orchestration layer** — the
contract that turns the one-way debate stream into a bidirectional channel an operator (or the
CopilotKit agent itself) can use to *steer the live finance council*.

It covers the command schema, the new `DebateState` fields, the API surface, the frontend
integration, and the compatibility assumptions made while several workers edited the repo in
parallel.

---

## 1. Architecture (how a command stays live)

```
 operator panel / CopilotKit action
        │  POST /api/command  (typed OperatorCommand)
        ▼
 agent/src/api.py            ── thin FastAPI route
        │
        ▼
 agent/src/council_commands.py  ── dispatch_command():
        │   • validate (shape + target role)
        │   • strict-live gate  → require_live_ready()
        │   • execute LIVE: Redis tools (compute_runway / list_vendors /
        │     search_finance_policies / company JSON) and, for clarify /
        │     route / challenge, a real gpt-5.5 @weave.op model call
        │   • record → atlas:stream:commands  + atlas:dashboard pub/sub
        │   • persist → atlas:command_state:<room>  (RedisJSON)
        ▼
 agent/src/agui_commands.py  ── protocol + Redis-backed command-state store
        │   merge_command_state() folds the eight command keys into every
        ▼   LangGraph _emit_patch and node return
 DebateState (CopilotKitState)  ── streams to useCoAgent over AG-UI
        ▲
        │  setState() mirror of the dispatcher's authoritative response
 frontend/src/components/council-command-panel.tsx
```

Two surfacing paths keep the panel correct in every situation, **without the browser ever
fabricating a result**:

1. **While a debate runs** – each graph node folds the live command-state (read from Redis) into
   its `_emit_patch`/return via `merge_command_state`, so commands issued mid-debate stream back
   over the same `useCoAgent` channel.
2. **Idle / immediately on click** – the `/api/command` response returns the server-authoritative
   command-state, which the page mirrors into the shared coagent state with `setState`. A 6 s
   `/api/command/state` poll (only while idle) keeps the panel fresh after reloads.

The council also *reads* operator directives: `agent_focus`, `pinned_evidence`, and
`requested_scenario` are injected into the analyst / debate / synthesis prompts
(`_command_focus_prompt`), so a pin or a routed question genuinely shapes the live reasoning.

---

## 2. Command schema

`POST /api/command` body (`OperatorCommand`):

```jsonc
{
  "type": "<command type>",      // required
  "agent": "treasury",            // required for agent-targeted commands
  "room": "northwind",            // optional; defaults to the single demo room
  "source": "panel" | "copilot",  // optional provenance tag
  "payload": { ... }              // per-type, see below
}
```

| type             | targets agent | payload                                                                 | live execution |
|------------------|:-------------:|-------------------------------------------------------------------------|----------------|
| `clarify`        | yes           | `{ question, context:{ decision, position? } }`                         | gpt-5.5 grounded reply |
| `route_question` | yes           | `{ question, context:{ decision, position? } }`                         | gpt-5.5 grounded reply |
| `challenge_claim`| yes           | `{ point \| claim, context:{ decision, position? } }`                   | gpt-5.5 defend/revise (returns `revised_stance`) |
| `scenario_fork`  | no            | `{ label?, extra_monthly_spend?, one_time_cost?, added_monthly_revenue? }` | `compute_runway` on live cash record |
| `compare_options`| no            | `{ options: [ {label?, ...scenario params} ] }` (2–4)                   | `compute_runway` per option |
| `pin_evidence`   | no            | `{ kind: policy\|vendor\|financial\|custom, query?, ref?, note? }`      | RediSearch / RedisJSON / vector RAG resolve |
| `pause_phase`    | no            | `{ phase?, reason? }`                                                    | sets cooperative pause flag |
| `resume_phase`   | no            | `{ phase?, reason? }`                                                    | clears pause flag |
| `export_memo`    | no            | `{}`                                                                     | assembles board memo from `atlas:debate:latest` |

Known target roles: `cfo, treasury, fpna, risk, procurement, reliability`.

### Response envelope (`CommandResult`)

```jsonc
{
  "status": "executed" | "rejected" | "failed",
  "reason": "missing_input" | "missing_context" | "invalid_command" |
            "capability_unavailable" | "not_found" | "not_live" | "execution_error" | null,
  "message": "human-readable explanation",
  "result":  { ... },          // the substantive output (reply / impact / pins / memo)
  "command": { "id", "type", "agent" },
  "stream_id": "<atlas:stream:commands id>",
  "state":   { ...full CommandState (eight keys)... },
  "room":    "northwind"
}
```

Rejections and failures are **returned in the envelope** (HTTP 200, or 503 for `not_live`) so the UI
can explain them — they are never silent and never faked.

### Rejection rules (validated server-side)

- unknown `type` → `invalid_command`
- agent-targeted command missing/unknown `agent` → `invalid_command`
- `clarify`/`route`/`challenge` with no `context.decision` → `missing_context`
- `scenario_fork` with all-zero params → `missing_input`
- `compare_options` with < 2 options → `missing_input`
- `pin_evidence` policy/custom with no query/note → `missing_input`; no match → `not_found`
- `export_memo` before a completed ruling → `missing_context`
- any command while strict-live preflight is red → `not_live`
- a missing capability adapter → `capability_unavailable` (safe no-op, never pretends success)

---

## 3. New `DebateState` fields

Eight keys were added to `DebateState`, appended to `STREAM_STATE_KEYS`, and mirrored in
`frontend/src/lib/types.ts`. The single source of truth for the names is
`agent/src/agui_commands.py::COMMAND_STATE_KEYS`.

| key                  | type   | meaning |
|----------------------|--------|---------|
| `command_queue`      | list   | reserved for queued/pending commands |
| `active_command`     | dict   | the most recent command + status + `result` |
| `pinned_evidence`    | list   | resolved policy/vendor/financial/custom pins |
| `requested_scenario` | dict   | latest fork (`mode:"single"`) or comparison (`mode:"compare"`) + runway impact |
| `agent_focus`        | dict   | clarify/route/challenge target + the grounded reply |
| `phase_controls`     | dict   | `{ paused, phase, reason, updated_at }` |
| `export_status`      | dict   | `{ ready, id, format, title, memo, generated_at }` |
| `command_audit_log`  | list   | append-only `{ id, type, agent, status, summary, at, stream_id }` (kept to 24) |

To stream a *new* command field later, edit it in three places (same rule as the rest of the
state): `COMMAND_STATE_KEYS` (agui_commands), the `DebateState` class (agent.py — it inherits
`STREAM_STATE_KEYS` automatically via the splat), and `DebateState` in `types.ts`.

---

## 4. API surface (added to `agent/src/api.py`)

- `POST /api/command` — dispatch one command (envelope above).
- `GET  /api/command/state?room=` — current `CommandState` (initial load / idle poll).
- `GET  /api/command/types` — the command vocabulary + known roles (introspection).

Frontend client (`frontend/src/lib/api.ts`): `api.command(body)`, `api.commandState(room?)`.
`api.command` keeps the body on HTTP 503 (the `not_live` envelope) and only throws on transport
errors, matching the existing tolerant health-snapshot pattern.

---

## 5. Frontend integration

- **`frontend/src/components/council-command-panel.tsx`** — restrained typed controls: direct an
  agent (clarify / route / challenge), scenario fork + A/B compare, pin evidence, pause/resume,
  export + memo download, plus live result / pins / scenario / audit displays. It only transports;
  no business logic runs in the browser.
- **`frontend/src/app/decisions/page.tsx`** — destructures `setState` from `useCoAgent`
  (`name: "finance_department"` preserved), derives a `CommandState` from the streamed `DebateState`,
  owns `dispatchCommand` (POST → mirror via `setState`), runs the idle poll, registers five
  `useCopilotAction`s (clarify / challenge / scenario fork / pin / export) so the CopilotKit agent
  and Realtime voice can drive the same dispatcher, and renders the panel in the right rail.

---

## 6. Compatibility assumptions

- **`finance_department` agent name preserved** across `agent/main.py`, `layout.tsx`,
  `api/copilotkit/route.ts`, and the `useCoAgent` call — unchanged.
- **`copilotkit_emit_state` version fallback** — `agui_commands.emit_state_compat` mirrors the
  defensive import + `(config, state)` → `(state)` signature fallback already used by `agent._emit`.
  The graph keeps using `agent._emit`; command-state reads/writes never raise into the graph (Redis
  failures degrade to the empty default).
- **Single council room** — command state is scoped by `room` (default `"northwind"`, matching
  `atlas:company:northwind`). The demo is a single shared room; the `room` field exists for forward
  compatibility with a multi-company build. The graph reads/writes the default room.
- **Cooperative, bounded pause** — `pause_phase` sets a flag honored only at the analyst/debate node
  boundaries, capped at `ATLAS_PAUSE_MAX_SECONDS` (default 45 s) and released instantly on resume.
  Synthesis and persistence never pause, so a submitted decision always reaches a ruling.
- **Narrow adapters with safe no-op rejection** — `council_commands._scenario_adapter` /
  `_evidence_adapter` prefer optional `src.scenario_helpers` / `src.evidence_helpers` if a parallel
  worker ships them, and otherwise fall back to the in-repo Redis tools. A genuinely missing
  capability yields `capability_unavailable`, never a fake success.
- **Export reads server truth** — `persist_node` now writes a full `atlas:debate:latest` snapshot, so
  `export_memo` assembles the board memo from Redis (not browser-supplied data); it falls back to a
  `payload.snapshot` only if provided.
- **Parallel-worker coexistence (frontend types)** — a parallel worker's `CouncilCommand` is a
  prompt-suggestion surface; this layer's command type is named **`OperatorCommand`** to avoid
  collision. A missing optional `RedisActivity.at` field (which the agent already emits via
  `_redis_activity`) was added to unblock another worker's activity rail.

---

## 7. Verification performed

- `npm run lint` → 0 errors (pre-existing warnings only).
- `npm run build` (Next.js 16, full TypeScript) → passes.
- Backend byte-compiles and imports cleanly; LangGraph `graph` compiles with the command wrapper.
- Live stack (Redis Stack + OpenAI + Weave + CopilotKit all green via `/api/health`):
  - `scenario_fork` → live `compute_runway` (e.g. runway 10.2 → 9.9 months).
  - `compare_options` → two live scenarios ranked by runway.
  - `pin_evidence` financial + vendor → resolved from RedisJSON / RediSearch.
  - `challenge_claim` → **real grounded gpt-5.5 reply** from Treasury (net-burn / runway / board
    floor figures), `revised_stance: conditional`.
  - All rejection paths (`missing_input`, `invalid_command`, `missing_context`) returned correctly.
  - Command events recorded to `atlas:stream:commands`; `command_state` accumulated pins / scenario /
    focus / audit.
  - `/api/health` confirmed to gate command execution (`not_live` rejection when red).

The full Decision Room browser exercise (submit a decision and watch the eight command keys stream
through `useCoAgent` while pinning / challenging / comparing) runs against the same live endpoints
verified above; submission remains locked until `/api/health` is green.
