"""
Deterministic smoke checks for the strategic-planning digital twin.

These validate the *scenario math* in src/planning.py, src/playbooks.py, and
src/stress_tests.py against the live Redis system of record — no LLM calls, so the
checks are fully reproducible. Run either way:

    uv run --directory agent python -m tests.test_planning      # standalone runner
    uv run --directory agent pytest tests/test_planning.py      # if pytest present

Requires Redis Stack to be seeded (scripts/seed-live.sh or
`uv run --directory agent python -m src.data.seed`).
"""

from __future__ import annotations

from src import planning as PL
from src import playbooks as PB
from src import stress_tests as ST

TOL = 2.0  # rounding tolerance in dollars / months


def _company() -> dict:
    co = PL.load_company()
    assert co.get("mrr"), "Redis company record missing — seed Redis first."
    return co


# --------------------------------------------------------------------------- #
def test_recompute_matches_system_of_record() -> None:
    """The cash/burn/runway formulas reproduce the stored system of record."""
    co = _company()
    cur = PL.recompute_current_metrics(co)
    stored_nb = float(co["monthly_net_burn"])
    assert abs(cur["net_burn"] - stored_nb) / stored_nb < 0.01, (cur, stored_nb)
    if co.get("runway_months") is not None:
        assert abs(cur["runway_months"] - float(co["runway_months"])) <= 0.3, cur


def test_flat_projection_reproduces_current_burn() -> None:
    """With no growth, no churn, and no actions, month 1 == the current snapshot."""
    co = _company()
    vals = PL.assumption_values(co, {"pipeline_conversion": 0.0, "logo_churn_mom": 0.0})
    inputs = PL.compile_steps([], vals)
    rows, _ = PL.project(co, inputs, 3)
    cur = PL.recompute_current_metrics(co)
    assert rows[0].mrr == round(float(co["mrr"])), rows[0].mrr
    assert abs(rows[0].net_burn - cur["net_burn"]) <= TOL, (rows[0].net_burn, cur)
    # Runway at the *start* of month 1 (cash now / net burn) == the current snapshot.
    start_runway = rows[0].cash_begin / rows[0].net_burn
    assert abs(start_runway - cur["runway_months"]) <= 0.3, (start_runway, cur)


def test_cash_and_mrr_identities_hold() -> None:
    """Row-level accounting identities hold across the whole projection."""
    co = _company()
    plan = PL.build_plan(co, title="identity-check", horizon_months=12)
    rows = plan.projection
    assert len(rows) == 12
    prev_cash = float(co["cash_on_hand"])
    prev_mrr = float(co["mrr"])
    for r in rows:
        # cash_end = cash_begin - net_burn - one_time + financing
        expect_cash = r.cash_begin - r.net_burn - r.one_time_cost + r.financing_inflow
        assert abs(r.cash_end - expect_cash) <= TOL, (r.month, r.cash_end, expect_cash)
        assert abs(r.cash_begin - prev_cash) <= TOL, (r.month, r.cash_begin, prev_cash)
        # mrr_t = mrr_{t-1} - churned + new
        expect_mrr = prev_mrr - r.churned_mrr + r.new_mrr
        assert abs(r.mrr - expect_mrr) <= TOL, (r.month, r.mrr, expect_mrr)
        # net_burn = cogs + opex - revenue ; gross_burn = cogs + opex
        assert abs(r.gross_burn - (r.cogs + r.opex)) <= TOL, r.month
        assert abs(r.net_burn - (r.gross_burn - r.revenue)) <= TOL, r.month
        assert abs(r.arr - r.mrr * 12) <= 12, (r.month, r.arr, r.mrr)  # arr from unrounded mrr
        prev_cash, prev_mrr = r.cash_end, r.mrr


def test_plan_is_deterministic() -> None:
    """Same inputs → identical projection (no randomness in the engine)."""
    co = _company()
    a = PL.build_plan(co, title="d1", horizon_months=12)
    b = PL.build_plan(co, title="d2", horizon_months=12)
    assert [r.model_dump() for r in a.projection] == [r.model_dump() for r in b.projection]
    assert a.summary == b.summary


def test_all_playbooks_complete() -> None:
    """Every playbook yields the seven required components and a 12-row projection."""
    co = _company()
    assert len(PB.PLAYBOOKS) == 7, list(PB.PLAYBOOKS)
    for pid in PB.PLAYBOOKS:
        plan = PB.build_playbook_plan(co, pid, horizon_months=12)
        assert plan.assumptions, f"{pid}: no assumptions"
        assert plan.steps, f"{pid}: no required actions"
        assert plan.summary, f"{pid}: no expected impact"
        assert plan.milestones, f"{pid}: no milestones"
        assert plan.risks, f"{pid}: no risks"
        assert plan.monitoring_triggers, f"{pid}: no monitoring triggers"
        assert len(plan.projection) == 12, f"{pid}: wrong horizon"
        # policy_blockers is a list (possibly empty) of typed conflicts
        assert isinstance(plan.policy_blockers, list)


def test_playbook_directional_effects() -> None:
    """Playbooks move the right levers vs. the base operating plan."""
    co = _company()
    base = PL.build_plan(co, title="base", horizon_months=12).summary
    bridge = PB.build_playbook_plan(co, "financing_bridge", horizon_months=12).summary
    vendors = PB.build_playbook_plan(co, "renegotiate_vendors", horizon_months=12).summary
    efficiency = PB.build_playbook_plan(co, "growth_to_efficiency", horizon_months=12).summary

    # A bridge injects cash → strictly higher minimum cash than doing nothing.
    assert bridge["min_cash"] > base["min_cash"], (bridge["min_cash"], base["min_cash"])
    # Vendor savings reduce burn → higher minimum cash.
    assert vendors["min_cash"] > base["min_cash"], (vendors["min_cash"], base["min_cash"])
    # Trading growth for efficiency → lower ending ARR than the growth base.
    assert efficiency["ending_arr"] < base["ending_arr"], (efficiency["ending_arr"], base["ending_arr"])


def test_stress_test_deterministic() -> None:
    """Monte Carlo is reproducible for a fixed seed and varies across seeds."""
    co = _company()
    a = ST.run_stress_test(co, trials=200, seed=42, persist=False)
    b = ST.run_stress_test(co, trials=200, seed=42, persist=False)
    c = ST.run_stress_test(co, trials=200, seed=7, persist=False)
    assert a.metrics["min_cash"] == b.metrics["min_cash"]
    assert a.prob_runway_breach == b.prob_runway_breach
    assert a.metrics["min_cash"]["p50"] != c.metrics["min_cash"]["p50"]
    for prob in (a.prob_runway_breach, a.prob_cash_negative, a.prob_below_cash_buffer):
        assert 0.0 <= prob <= 1.0


def test_sensitivity_directions() -> None:
    """One-variable sweeps move the output in the economically correct direction."""
    co = _company()
    churn = ST.run_sensitivity(co, "churn", horizon_months=12)
    conv = ST.run_sensitivity(co, "conversion", horizon_months=12)
    margin = ST.run_sensitivity(co, "gross_margin", horizon_months=12)
    savings = ST.run_sensitivity(co, "vendor_savings", horizon_months=12)
    assert churn.direction == "decreases", churn.direction          # more churn → less cash
    assert conv.direction == "increases", conv.direction            # more conversion → more cash
    assert margin.direction == "increases", margin.direction        # higher margin → more cash
    assert savings.direction == "increases", savings.direction      # more savings → more cash

    suite = ST.sensitivity_suite(co, horizon_months=12)
    assert len(suite["results"]) == 6
    assert suite["most_sensitive"]
    # ranking is sorted by swing descending
    swings = [r["swing"] or 0 for r in suite["ranking"]]
    assert swings == sorted(swings, reverse=True)


def test_portfolio_recommends_a_set() -> None:
    """compare_playbooks ranks all candidates and recommends a weighted portfolio."""
    co = _company()
    ids = ["extend_runway", "financing_bridge", "renegotiate_vendors", "unblock_enterprise"]
    portfolio, plans = PB.compare_playbooks(co, ids, "Runway is tight with enterprise deals pending", persist=False)
    assert set(portfolio.ranking) == set(ids)
    assert len(portfolio.candidates) == len(ids)
    assert portfolio.recommended_portfolio, "no portfolio recommended"
    roles = {p["role"] for p in portfolio.recommended_portfolio}
    assert "primary" in roles
    weight_sum = sum(p["weight"] for p in portfolio.recommended_portfolio)
    assert abs(weight_sum - 1.0) <= 0.01, weight_sum
    # every candidate is scored on all six criteria
    for c in portfolio.candidates:
        assert set(c["score_breakdown"]) == set(PB.PORTFOLIO_WEIGHTS)


def test_policy_blockers_fire_on_breach() -> None:
    """A plan that drives runway below the guardrail surfaces a runway blocker."""
    co = _company()
    # Aggressive: hold high growth but stack expensive, non-revenue spend.
    steps = [
        PL.PlaybookStep(order=1, action="Large discretionary spend", owner="treasury", kind="spend",
                        start_month_index=1, financial_effect={"monthly_cost_delta": 200_000.0}),
    ]
    plan = PL.build_plan(co, title="breach-check", horizon_months=12, steps=steps)
    policies = {b["policy"] for b in plan.policy_blockers}
    assert any("Runway" in p or "cash" in p.lower() for p in policies), policies
    # The $2.4M annualized commitment trips the board-notification threshold.
    assert any("Board notification" in p for p in policies), policies


def test_persistence_round_trip() -> None:
    """Plans persist to Redis with provenance and come back via get/list."""
    co = _company()
    plan = PL.build_plan(co, title="persist-check", horizon_months=6)
    pid = PL.save_plan(plan)
    fetched = PL.get_plan(pid)
    assert fetched and fetched["id"] == pid
    assert fetched["provenance"]["deterministic"] is True
    assert fetched["calc_metadata"]["model"].startswith("deterministic-projection")
    assert pid in {c["id"] for c in PL.list_plans(limit=50)}


ALL_TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]


def main() -> int:
    passed = 0
    for fn in ALL_TESTS:
        fn()
        print(f"  ✓ {fn.__name__}")
        passed += 1
    print(f"\n{passed}/{len(ALL_TESTS)} deterministic planning smoke checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
