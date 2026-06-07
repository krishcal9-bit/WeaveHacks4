"""
Idempotent seed for the orchestration namespace (``atlas:orch:*``).

Standalone:  ``uv run --directory agent python -m src.orchestration.seed``

Wired into the main ``seed()`` behind ``ATLAS_ORCHESTRATOR`` (default off) so the
core demo seed stays byte-for-byte unchanged. Seeds:
  * baseline TOPOLOGIES (fixed ``seed-*`` ids → reseed overwrites) the eval/promotion
    gate can A/B and the Conductor can start from;
  * a few EPISODIC MEMORY records (past decisions + outcomes) so the very first live
    debate already has precedent to recall (uses live OpenAI embeddings — no fakes).
"""

from src.orchestration import conductor as CONDUCTOR
from src.orchestration import models as M
from src.orchestration import store as STORE


def _baseline_topologies() -> list[M.Topology]:
    # (name, decision_type, roles, rounds, red_team, loops, threshold)
    specs = [
        ("balanced-committee", "general", ["cfo", "treasury", "fpna", "risk", "procurement"], 2, True, False, 0.75),
        ("lean-fast", "vendor_contract", ["cfo", "treasury", "procurement"], 1, False, False, 0.70),
        ("deep-mna", "acquisition", ["cfo", "treasury", "fpna", "risk", "mna", "legal", "tax", "hedging"], 4, True, True, 0.80),
        ("risk-heavy", "compliance", ["cfo", "risk", "legal", "treasury"], 3, True, True, 0.78),
    ]
    topologies: list[M.Topology] = []
    for name, decision_type, roles, rounds, red_team, loops, threshold in specs:
        plan = M.ConductorPlan(
            topology_name=name,
            decision_type=decision_type,
            seats=[
                M.SeatPlan(role=r, is_specialist=(r in CONDUCTOR.SPECIALIST_ROLES), rationale="seeded baseline seat")
                for r in roles
            ],
            rounds=rounds,
            fan_out=True,
            allow_loops=loops,
            requires_red_team=red_team,
            convergence_threshold=threshold,
            stop_conditions=["weighted agreement >= threshold", "max rounds reached", "red-team satisfied"],
            rationale=f"Seeded {name} baseline topology for the {decision_type} class.",
        )
        topology = CONDUCTOR.plan_to_topology(plan)
        topology.id = f"seed-{name}"  # fixed id → reseed overwrites (idempotent)
        topologies.append(topology)
    return topologies


def _episodic_records() -> list[M.EpisodicMemoryRecord]:
    return [
        M.EpisodicMemoryRecord(
            id="seed-mem-hiring", company_id="northwind",
            decision="Hire 6 account executives to accelerate ARR growth.",
            decision_type="hiring", recommendation="CONDITIONAL", outcome="Hired 4 in tranches; ARR +18% over two quarters.",
            confidence=70, key_metrics=["burn multiple 2.0x", "runway 14 months"],
            lessons=["Stage hiring to keep the burn multiple under the 2.0x board ceiling."],
        ),
        M.EpisodicMemoryRecord(
            id="seed-mem-vendor", company_id="northwind",
            decision="Renew the observability vendor on a 2-year prepaid contract.",
            decision_type="vendor_contract", recommendation="CONDITIONAL", outcome="Negotiated 12% discount for annual prepay; approved.",
            confidence=76, key_metrics=["$180k/yr", "12% prepay discount"],
            lessons=["Trade multi-year commitment for price only when runway comfortably exceeds the term."],
        ),
        M.EpisodicMemoryRecord(
            id="seed-mem-acq", company_id="northwind",
            decision="Acquire a small competitor for cash + equity.",
            decision_type="acquisition", recommendation="DEFER", outcome="Deferred; cash position too thin for the cash component.",
            confidence=64, key_metrics=["cash $4.2M", "deal cash component $4.8M"],
            lessons=["Never let a deal's cash component approach total cash on hand without committed financing."],
        ),
    ]


def seed_orchestration() -> dict:
    """Idempotent. Returns a small summary of what was seeded."""
    migrated = STORE.run_migrations()
    topologies = 0
    for topology in _baseline_topologies():
        STORE.save_topology(topology)
        topologies += 1
    memory = 0
    for record in _episodic_records():
        STORE.remember(record)  # live OpenAI embeddings
        memory += 1
    STORE.ensure_bus_group()
    return {"migrated": migrated, "topologies": topologies, "memory": memory}


if __name__ == "__main__":
    from src.env import load_env

    load_env()
    print("seeding orchestration namespace (atlas:orch:*)…")
    print(seed_orchestration())
