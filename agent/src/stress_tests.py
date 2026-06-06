"""
Atlas — stress testing & sensitivity analysis for strategic plans.

Two deterministic tools on top of the ``planning.py`` projection engine:

  • Monte Carlo-style stress runs (``run_stress_test``): sample the uncertain
    operating dials (churn, pipeline conversion, growth, gross margin) from
    triangular distributions anchored on the real system-of-record values, run the
    projection many times, and report percentile bands plus the probability of
    breaching the runway / cash guardrails. A fixed RNG seed makes every run
    reproducible, so this doubles as a deterministic smoke check.

  • One-variable sensitivity sweeps (``run_sensitivity`` / ``sensitivity_suite``):
    vary a single lever — churn, conversion, gross margin, hiring start date,
    vendor savings, or financing close month — across a range and measure how the
    outputs (minimum cash, lowest runway, ending ARR) move, including a near-base
    elasticity and the swing. ``sensitivity_suite`` ranks which lever the plan is
    most sensitive to.

No model calls here — this is all arithmetic over the projection.
"""

from __future__ import annotations

import math
import random
import time
from typing import Any, Callable

from src import planning as PL
from src.planning import (
    PlaybookStep,
    SensitivityResult,
    StressTest,
    assumption_values,
    compile_steps,
    default_hire_steps,
    project,
)
from src import redis_layer as R

STRESS_PREFIX = f"{R.NS}:stress:"
DEFAULT_TRIALS = 500
DEFAULT_SEED = 42


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _percentile(sorted_vals: list[float], p: float) -> float | None:
    if not sorted_vals:
        return None
    k = (len(sorted_vals) - 1) * p
    lo, hi = math.floor(k), math.ceil(k)
    if lo == hi:
        return round(sorted_vals[int(k)], 2)
    return round(sorted_vals[lo] * (hi - k) + sorted_vals[hi] * (k - lo), 2)


def _bands(values: list[float]) -> dict[str, float | None]:
    s = sorted(values)
    n = len(s)
    return {
        "p5": _percentile(s, 0.05),
        "p25": _percentile(s, 0.25),
        "p50": _percentile(s, 0.50),
        "p75": _percentile(s, 0.75),
        "p95": _percentile(s, 0.95),
        "mean": round(sum(s) / n, 2) if n else None,
        "min": round(s[0], 2) if n else None,
        "max": round(s[-1], 2) if n else None,
    }


def steps_from_plan_doc(doc: dict) -> tuple[list[PlaybookStep], dict[str, float]]:
    """Reconstruct typed steps + assumption overrides from a persisted plan dict."""
    steps = [PlaybookStep(**s) for s in (doc.get("steps") or [])]
    overrides: dict[str, float] = {}
    base_keys = PL._ASSUMPTION_META.keys()
    for a in doc.get("assumptions") or []:
        if a.get("source") in ("playbook", "override") and a.get("key") in base_keys:
            overrides[a["key"]] = float(a.get("value"))
    return steps, overrides


# --------------------------------------------------------------------------- #
# Monte Carlo-style stress test
# --------------------------------------------------------------------------- #
DEFAULT_DISTRIBUTIONS: dict[str, dict[str, float]] = {
    # triangular(low, mode, high) — anchored on the seeded operating values.
    "logo_churn_mom": {"low": 0.012, "mode": 0.018, "high": 0.040},
    "pipeline_conversion": {"low": 0.18, "mode": 0.32, "high": 0.46},
    "mrr_growth_mom": {"low": 0.03, "mode": 0.09, "high": 0.12},
    "gross_margin": {"low": 0.72, "mode": 0.78, "high": 0.82},
}


def run_stress_test(
    company: dict,
    *,
    name: str = "Operating stress test",
    description: str = "",
    horizon_months: int = PL.DEFAULT_HORIZON,
    trials: int = DEFAULT_TRIALS,
    seed: int = DEFAULT_SEED,
    base_overrides: dict[str, float] | None = None,
    steps: list[PlaybookStep] | None = None,
    distributions: dict[str, dict[str, float]] | None = None,
    persist: bool = True,
) -> StressTest:
    """Sample uncertain dials many times and summarize the distribution of futures.

    Deterministic for a fixed ``seed``. If ``steps`` is omitted the seeded hiring
    plan is used as the operating structure.
    """
    rng = random.Random(seed)
    dists = distributions or DEFAULT_DISTRIBUTIONS
    start_month = PL._start_month(company)
    if steps is None:
        steps = default_hire_steps(company, start_month)

    ending_cash: list[float] = []
    min_cash: list[float] = []
    lowest_runway: list[float] = []
    runway_at_horizon: list[float] = []
    ending_arr: list[float] = []
    breach_months: list[int] = []
    n_runway_breach = n_cash_neg = n_below_buffer = 0

    base_vals = assumption_values(company, base_overrides)
    worst = None  # track the trial with the lowest min-cash

    for _ in range(trials):
        sample = dict(base_vals)
        for key, d in dists.items():
            sample[key] = rng.triangular(d["low"], d["high"], d["mode"])
        inputs = compile_steps(steps, sample)
        rows, summary = project(company, inputs, horizon_months)

        ending_cash.append(summary["ending_cash"])
        min_cash.append(summary["min_cash"])
        ending_arr.append(summary["ending_arr"])
        runway_at_horizon.append(summary["runway_at_horizon"] if summary["runway_at_horizon"] is not None else horizon_months * 3)
        lr = summary["lowest_runway_months"]
        lowest_runway.append(lr if lr is not None else horizon_months * 3)

        if summary["breaches_runway_floor"]:
            n_runway_breach += 1
            first = next((r for r in rows if r.runway_months is not None and r.runway_months < PL.RUNWAY_FLOOR_MONTHS), None)
            if first:
                breach_months.append(first.month_index)
        if summary["goes_insolvent"]:
            n_cash_neg += 1
        if summary["breaches_cash_buffer"]:
            n_below_buffer += 1

        if worst is None or summary["min_cash"] < worst["min_cash"]:
            worst = {"min_cash": summary["min_cash"], "sample": {k: round(sample[k], 4) for k in dists}, "summary": summary}

    # Base (mode) case for reference.
    base_inputs = compile_steps(steps, base_vals)
    _, base_summary = project(company, base_inputs, horizon_months)

    expected_breach_month = None
    if breach_months:
        breach_months.sort()
        med_idx = breach_months[len(breach_months) // 2]
        expected_breach_month = PL.add_months(start_month, med_idx - 1)

    st = StressTest(
        id=f"stress-{time.strftime('%Y%m%d')}-{seed}-{trials}",
        name=name,
        description=description or f"{trials}-trial Monte Carlo over churn, conversion, growth, and margin.",
        trials=trials,
        horizon_months=horizon_months,
        seed=seed,
        distributions=dists,
        metrics={
            "ending_cash": _bands(ending_cash),
            "min_cash": _bands(min_cash),
            "lowest_runway_months": _bands(lowest_runway),
            "runway_at_horizon": _bands(runway_at_horizon),
            "ending_arr": _bands(ending_arr),
        },
        prob_runway_breach=round(n_runway_breach / trials, 3),
        prob_cash_negative=round(n_cash_neg / trials, 3),
        prob_below_cash_buffer=round(n_below_buffer / trials, 3),
        expected_breach_month=expected_breach_month,
        worst_case=worst or {},
        base_case={
            "min_cash": base_summary.get("min_cash"),
            "lowest_runway_months": base_summary.get("lowest_runway_months"),
            "ending_arr": base_summary.get("ending_arr"),
            "runway_at_horizon": base_summary.get("runway_at_horizon"),
        },
        provenance={
            "data_source": PL.COMPANY_KEY,
            "deterministic": True,
            "seed": seed,
            "engine_version": PL.ENGINE_VERSION,
            "method": "triangular Monte Carlo",
            "thresholds": {"runway_floor_months": PL.RUNWAY_FLOOR_MONTHS, "cash_buffer_floor": PL.CASH_BUFFER_FLOOR},
        },
    )
    if persist:
        try:
            R.set_json(f"{STRESS_PREFIX}{st.id}", st.model_dump())
            R.append_event(
                "stress",
                {
                    "stress_id": st.id,
                    "name": st.name,
                    "trials": trials,
                    "prob_runway_breach": st.prob_runway_breach,
                    "prob_cash_negative": st.prob_cash_negative,
                    "source": "planning",
                },
            )
        except Exception:
            pass
    return st


# --------------------------------------------------------------------------- #
# Sensitivity analysis (one variable at a time)
# --------------------------------------------------------------------------- #
class _SensSpec:
    def __init__(
        self,
        variable: str,
        label: str,
        unit: str,
        base: Callable[[dict], float],
        default_points: Callable[[dict], list[float]],
        build: Callable[[dict, float, str], tuple[dict[str, float], list[PlaybookStep]]],
    ):
        self.variable = variable
        self.label = label
        self.unit = unit
        self.base = base
        self.default_points = default_points
        self.build = build


def _assumption_build(key: str) -> Callable[[dict, float, str], tuple[dict[str, float], list[PlaybookStep]]]:
    def build(company: dict, value: float, start_month: str) -> tuple[dict[str, float], list[PlaybookStep]]:
        return {key: value}, default_hire_steps(company, start_month)

    return build


def _hiring_start_build(company: dict, offset: float, start_month: str) -> tuple[dict[str, float], list[PlaybookStep]]:
    steps = default_hire_steps(company, start_month)
    for s in steps:
        s.start_month_index = max(1, int(s.start_month_index + int(offset)))
    return {}, steps


def _vendor_savings_build(company: dict, monthly: float, start_month: str) -> tuple[dict[str, float], list[PlaybookStep]]:
    steps = default_hire_steps(company, start_month)
    steps.append(
        PlaybookStep(
            order=99, action="Vendor renegotiation savings", owner="procurement",
            kind="vendor_savings", start_month_index=2,
            financial_effect={"monthly_cost_delta": float(monthly)},
            detail="Sensitivity sweep on realized monthly vendor savings.",
        )
    )
    return {}, steps


def _financing_close_build(company: dict, close_index: float, start_month: str) -> tuple[dict[str, float], list[PlaybookStep]]:
    steps = default_hire_steps(company, start_month)
    steps.append(
        PlaybookStep(
            order=98, action="Bridge financing", owner="treasury",
            kind="financing", start_month_index=max(1, int(close_index)),
            financial_effect={"financing_amount": 3_000_000.0},
            detail="Sensitivity sweep on the financing close month.",
        )
    )
    return {}, steps


def _sens_specs() -> dict[str, _SensSpec]:
    return {
        "churn": _SensSpec(
            "logo_churn_mom", "Logo churn (monthly)", "ratio_mom",
            lambda c: float(c.get("logo_churn_mom") or 0.018),
            lambda c: [0.010, 0.018, 0.025, 0.035, 0.050],
            _assumption_build("logo_churn_mom"),
        ),
        "conversion": _SensSpec(
            "pipeline_conversion", "Pipeline conversion", "ratio",
            lambda c: PL.BASE_CONVERSION,
            lambda c: [0.20, 0.26, 0.32, 0.40, 0.50],
            _assumption_build("pipeline_conversion"),
        ),
        "gross_margin": _SensSpec(
            "gross_margin", "Gross margin", "ratio",
            lambda c: float(c.get("gross_margin") or 0.78),
            lambda c: [0.70, 0.74, 0.78, 0.82, 0.86],
            _assumption_build("gross_margin"),
        ),
        "hiring_start": _SensSpec(
            "hiring_start_offset", "Hiring start delay", "months",
            lambda c: 0.0,
            lambda c: [0, 1, 2, 3, 4],
            _hiring_start_build,
        ),
        "vendor_savings": _SensSpec(
            "vendor_savings_monthly", "Vendor savings", "usd_month",
            lambda c: 0.0,
            lambda c: [0, 3_000, 6_000, 9_000, 12_000],
            _vendor_savings_build,
        ),
        "financing_close_month": _SensSpec(
            "financing_close_month_index", "Financing close month", "months",
            lambda c: 4.0,
            lambda c: [2, 4, 6, 8, 10],
            _financing_close_build,
        ),
    }


def _output_of(summary: dict[str, Any], metric: str, horizon: int) -> float | None:
    v = summary.get(metric)
    if v is None and metric in ("lowest_runway_months", "runway_at_horizon"):
        # cash-flow positive → effectively unconstrained runway
        return float(horizon * 3)
    return v


def run_sensitivity(
    company: dict,
    variable: str,
    *,
    points: list[float] | None = None,
    horizon_months: int = PL.DEFAULT_HORIZON,
    output_metric: str = "min_cash",
) -> SensitivityResult:
    specs = _sens_specs()
    if variable not in specs:
        raise KeyError(f"Unknown sensitivity variable '{variable}'. Known: {list(specs)}")
    spec = specs[variable]
    start_month = PL._start_month(company)
    base_value = spec.base(company)
    pts = points if points is not None else spec.default_points(company)

    rows_out: list[dict[str, float | None]] = []
    base_output: float | None = None
    for v in pts:
        overrides, steps = spec.build(company, float(v), start_month)
        inputs = compile_steps(steps, assumption_values(company, overrides))
        _, summary = project(company, inputs, horizon_months)
        out = _output_of(summary, output_metric, horizon_months)
        rows_out.append(
            {
                "value": round(float(v), 4),
                "min_cash": summary.get("min_cash"),
                "lowest_runway_months": summary.get("lowest_runway_months"),
                "runway_at_horizon": summary.get("runway_at_horizon"),
                "ending_arr": summary.get("ending_arr"),
                "output": out,
            }
        )
        if abs(float(v) - base_value) < 1e-9:
            base_output = out

    outputs = [r["output"] for r in rows_out if r["output"] is not None]
    swing = round(max(outputs) - min(outputs), 2) if outputs else None

    # direction from first→last output
    direction = ""
    if len(rows_out) >= 2 and rows_out[0]["output"] is not None and rows_out[-1]["output"] is not None:
        diffs = [
            (rows_out[i + 1]["output"] - rows_out[i]["output"])
            for i in range(len(rows_out) - 1)
            if rows_out[i]["output"] is not None and rows_out[i + 1]["output"] is not None
        ]
        if all(d >= 0 for d in diffs):
            direction = "increases"
        elif all(d <= 0 for d in diffs):
            direction = "decreases"
        else:
            direction = "non-monotonic"

    # elasticity near base: use the two points straddling the base value.
    # Denominators use abs() so the sign reflects the true output response even
    # when the base output is negative (e.g. min-cash under an insolvent base).
    elasticity = None
    if base_output not in (None, 0) and base_value:
        below = [r for r in rows_out if r["value"] < base_value and r["output"] is not None]
        above = [r for r in rows_out if r["value"] > base_value and r["output"] is not None]
        if below and above:
            lo, hi = below[-1], above[0]
            d_out = (hi["output"] - lo["output"]) / abs(base_output)
            d_in = (hi["value"] - lo["value"]) / abs(base_value)
            if d_in:
                elasticity = round(d_out / d_in, 3)

    return SensitivityResult(
        variable=spec.variable,
        label=spec.label,
        unit=spec.unit,
        base_value=round(base_value, 4),
        output_metric=output_metric,
        base_output=base_output,
        points=rows_out,
        elasticity=elasticity,
        swing=swing,
        direction=direction,
        note=f"Swept {spec.label} across {len(pts)} points; output = {output_metric}.",
    )


def sensitivity_suite(
    company: dict, *, horizon_months: int = PL.DEFAULT_HORIZON, output_metric: str = "min_cash"
) -> dict[str, Any]:
    """Run all six required sensitivities and rank which lever matters most."""
    results = [
        run_sensitivity(company, var, horizon_months=horizon_months, output_metric=output_metric)
        for var in _sens_specs()
    ]
    ranked = sorted(results, key=lambda r: (r.swing or 0), reverse=True)
    return {
        "output_metric": output_metric,
        "horizon_months": horizon_months,
        "results": [r.model_dump() for r in results],
        "ranking": [{"variable": r.variable, "label": r.label, "swing": r.swing, "elasticity": r.elasticity} for r in ranked],
        "most_sensitive": ranked[0].label if ranked else None,
        "provenance": {"data_source": PL.COMPANY_KEY, "deterministic": True, "engine_version": PL.ENGINE_VERSION},
    }
