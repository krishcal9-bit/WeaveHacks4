"""
Atlas orchestration engine (``ATLAS_ORCHESTRATOR``, default OFF).

A deep, opt-in agent-orchestration layer that takes the finance committee beyond
the fixed linear graph in ``src/agent.py``: a Conductor plans the debate topology
per decision, a dynamic roster seats specialists on demand, a multi-round debate
detects convergence and survives an adversarial red-team seat, positions are
reconciled by reliability-weighted voting (with minority reports), and the whole
run is durable/branchable/replayable in Redis and evaluatable in W&B Weave.

ISOLATION CONTRACT (this whole package is new + self-contained):
  * Owns the ``atlas:orch:*`` Redis subtree ONLY (see ``namespace.py``).
  * Builds on the *stable public API* of ``src.redis_layer`` and reuses
    ``ROSTER``/``Position``/``llm`` from ``src.agent`` via LAZY imports, so this
    package imports cleanly offline and never races a sibling editing those files.
  * Activated only behind the ``ATLAS_ORCHESTRATOR`` env flag; with the flag off
    the live demo graph is byte-for-byte unchanged.

Submodules are imported explicitly by consumers (kept out of ``__init__`` so the
package has no heavy import-time dependencies).
"""

from __future__ import annotations

__all__ = ["namespace"]

# Package schema/version marker (independent of the Redis schema version in
# namespace.py); bump on breaking changes to the orchestration module surface.
__version__ = "0.1.0"
