---
name: concurrent-workstreams
description: Multiple agents edit the Atlas repo at once; namespace ownership + don't-clobber rules
metadata:
  type: project
---

As of 2026-06-06, the Atlas (WeaveHacks4) working tree has 3+ concurrent agent workstreams editing shared files. Re-read any shared file immediately before editing; prefer new modules + additive edits over rewrites. `redis_layer.py` changed twice mid-edit during one session.

**Redis namespace ownership (do not cross):**
- Connectors workstream → `atlas:source:*`, `atlas:dataset:*`, `atlas:reconciliation:latest`, `atlas:stream:reconciliation`. Modules: `agent/src/integrations/`, `weave_eval.py`, `agui_commands.py`, `structured_models.py`.
- Governance workstream → `atlas:govpolicy:*`, `atlas:approval:*`, `atlas:obligation:*`, `atlas:approval_matrix:northwind`, `atlas:idx:govpolicies|approvals|obligations`, `atlas:stream:audit`. Added directly into `redis_layer.py` (constants + `ensure_governance_indices`, `search_json_index`, `list_json`, `delete_key`, private `_index_definition`).
- Goal 3 (this workstream) → `atlas:department:*`, `atlas:invoice:*`, `atlas:po:*`, `atlas:contract:*`, `atlas:arr:*`, `atlas:scenario:*`, `atlas:knowledge:*`, `atlas:idx:{departments,invoices,purchase_orders,contracts,scenarios,knowledge}`, `atlas:stream:scenarios`, `atlas:meta:financial_*`. Modules: `redis_models.py`, `redis_store.py`, `scenario_engine.py`.

**Why:** approval records/thresholds overlap governance; ceded approvals to them and satisfy "approval thresholds" via the `board_policy` block in the company doc that the scenario engine reads.

**How to apply:** Don't touch `redis_layer.py` or `health.py` (governance/connectors are editing them); build on `redis_layer`'s stable public API only (`client/set_json/get_json/keys/append_event/read_events/publish/embed_texts/to_bytes/cache_set/cache_get/NS/EMBED_DIM`). Expose Goal-3 validation via a new `/api/redis-map` endpoint + preflight script, not via edits to the contended `_redis_status`. See [[atlas-live-only-contract]].
