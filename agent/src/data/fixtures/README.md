# Acme demo operating-data fixtures

These files are **opt-in demo operating data** for the Acme Corp seeded company —
not live connector results. They exist so the finance-operations connectors and
reconciliation engine can be exercised end-to-end without a real enterprise
export on hand.

When imported, provenance honestly records `origin: "acme-demo-fixture"` and the
real file path + SHA-256 checksum, so nobody mistakes them for a live external
feed. They are **never auto-loaded**: you must explicitly opt in with either

```bash
uv run --directory agent python -m src.integrations.cli import --demo
```

or by pointing a connector env var at one of these files (e.g.
`ATLAS_INVOICES_FILE=agent/src/data/fixtures/invoices.csv`).

| File | Connector | Notes |
| --- | --- | --- |
| `ledger.csv` | `ledger` | General-ledger cash transactions (payroll, SaaS, revenue). |
| `invoices.csv` | `invoices` | AP invoices; includes a Datadog overspend and one unmatched (shadow) vendor. |
| `vendor_export.json` | `vendor_export` | Procurement export incl. a new $250K commitment needing board notification. |
| `crm_opportunities.csv` | `crm_opportunities` | Pipeline weighted above the seeded forecast assumption. |
| `headcount_plan.csv` | `headcount_plan` | Actuals incl. an over-plan Engineering team and an unplanned Marketing hire. |
| `security_evidence.json` | `security_evidence` | SOC 2 controls incl. a revenue-blocking evidence gap. |
| `board_policies.json` | `board_policy` | Machine-checkable thresholds mirroring the seeded board constraints. |

The values are deliberately consistent with `agent/src/data/seed.py` so
reconciliation produces explainable discrepancies rather than noise.
