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
| `ledger.csv` | `ledger` | Bank/card-style transaction export with raw descriptors, card charges, refunds, split transactions, fees, payroll summaries, intercompany transfers, unknown vendors, and uncategorized spend. |
| `invoices.csv` | `invoices` | AP invoices with inconsistent vendor names, partial payments, overdue and disputed invoices, missing due dates, missing PO numbers, EUR/GBP rows, line descriptions, contract-vs-invoice amount drift, a credit, and one malformed amount. |
| `vendor_export.json` | `vendor_export` | Procurement export with mixed date formats, vendor-name drift, auto-renew metadata, and a new $250K commitment needing board notification. |
| `crm_opportunities.csv` | `crm_opportunities` | Pipeline with slipped close dates, stage aging, stale activity, owner changes, probability overrides, duplicate account aliases, renewal/new/expansion mix, weighted-vs-unweighted ARR gaps, and mixed probability/date formats. |
| `headcount_plan.csv` | `headcount_plan` | HRIS/planning export with planned, open, and filled roles; start-date slippage; fully loaded cost; department mapping drift; contractors; backfills; partial approvals; and unplanned headcount. |
| `security_evidence.json` | `security_evidence` | SOC 2 / AI governance evidence with stale dates, missing evidence, and revenue-blocking controls. |
| `board_policies.json` | `board_policy` | Machine-checkable thresholds mirroring the seeded board constraints. |

The values are deliberately messy but still realistic. Atlas should normalize
common export weirdness, preserve provenance, and produce explainable
discrepancies instead of assuming the uploaded finance files are pristine.

## Scenario-specific messy packs

`agent/src/data/demo_scenarios.py` adds Redis-seeded decision examples for:
Datadog renewal, security blocker, hiring plan, bridge financing, vendor
consolidation, pricing change, and pipeline shortfall. Each pack includes a
council-ready decision prompt plus messy rows from at least three source
categories (for example ContractVault, PayablesDesk, CloudLedger, PipelineHub,
PeopleRoster, TrustVault, BoardPortal). These packs power `/api/demo/scenarios`
and the Decisions-page selector; they are inspectable demo artifacts and do not
overwrite uploaded connector data.
