from __future__ import annotations

from fastapi.testclient import TestClient

from src.api import router
from src.data.demo_scenarios import demo_scenarios, scenario_branch_specs, seed_demo_scenarios


REQUIRED_SCENARIOS = {
    "datadog-renewal",
    "security-blocker",
    "hiring-plan",
    "bridge-financing",
    "vendor-consolidation",
    "pricing-change",
    "pipeline-shortfall",
}


def test_demo_scenario_packs_cover_required_business_cases() -> None:
    scenarios = demo_scenarios()
    by_id = {scenario["id"]: scenario for scenario in scenarios}

    assert REQUIRED_SCENARIOS <= set(by_id)
    for scenario_id in REQUIRED_SCENARIOS:
        scenario = by_id[scenario_id]
        assert scenario["decision_prompt"]
        assert scenario["branch_id"].startswith("demo-")
        assert len(scenario["sources"]) >= 3
        assert len(set(scenario["source_types"])) >= 3
        assert scenario["messy_input_count"] >= 3
        assert scenario["expected_council_focus"]
        for source in scenario["sources"]:
            assert source["source_type"]
            assert source["source_system"]
            assert source["record_count"] == len(source["records"])
            assert source["messy_fields"]


def test_demo_scenario_packs_seed_to_redis_and_api() -> None:
    summary = seed_demo_scenarios(verbose=False)
    assert summary["scenario_packs"] >= 7

    client = TestClient(router)
    payload = client.get("/api/demo/scenarios").json()
    assert payload["count"] >= 7
    by_id = {scenario["id"]: scenario for scenario in payload["scenarios"]}
    assert REQUIRED_SCENARIOS <= set(by_id)
    assert by_id["datadog-renewal"]["sources"][0]["record_count"] >= 1

    detail = client.get("/api/demo/scenarios/demo-security-blocker").json()
    assert detail["id"] == "security-blocker"
    assert "BP-6" in str(detail)


def test_scenario_engine_branch_specs_match_selector_catalog() -> None:
    specs = scenario_branch_specs()
    branch_ids = {scenario_id for scenario_id, *_ in specs}
    assert {
        "demo-datadog-renewal",
        "demo-security-blocker",
        "demo-hiring-plan",
        "demo-bridge-financing",
        "demo-vendor-consolidation",
        "demo-pricing-change",
        "demo-pipeline-shortfall",
    } <= branch_ids
    for scenario_id, title, changes, description, tags in specs:
        assert title
        assert changes
        assert description
        assert tags
        assert scenario_id.startswith("demo-")
