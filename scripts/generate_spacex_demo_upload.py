#!/usr/bin/env python3
"""One-off generator for demo_uploads/spacex/ — mirrors other demo pack structure."""

from __future__ import annotations

import csv
import json
from datetime import date, timedelta
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "demo_uploads" / "spacex"
PREFIX = "sx"
CODE = "SX"
COMPANY = "SpaceX"
EXPORT_BATCH = f"{CODE}-FINOPS-2026-Q2-CLOSE"
EXPORTED_AT = "2026-06-30T18:42:00Z"
SOURCE_NOTE = (
    "Demo operating export grounded in publicly reported SpaceX facts "
    "(founded 2002, Hawthorne CA, launch services, Starlink, Falcon 9, Starship). "
    "Line items are illustrative internal finance records, not proprietary filings."
)
PRODUCT_LINE = "orbital launch and Starlink broadband services"

DEPARTMENTS = [
    "Launch Operations",
    "Starlink",
    "Starship Program",
    "Propulsion",
    "Avionics",
    "Mission Assurance",
    "Ground Systems",
    "Finance",
    "People Operations",
    "Government Relations",
]

VENDORS = [
    ("Panasonic Energy", "Starlink Hardware", "Starlink", 4200000, 350000),
    ("Amazon Web Services", "Cloud Infrastructure", "Ground Systems", 2800000, 233333.33),
    ("Honeywell Aerospace", "Avionics Components", "Avionics", 1950000, 162500),
    ("L3Harris Technologies", "Communications", "Starlink", 1680000, 140000),
    ("Ball Aerospace", "Satellite Payloads", "Starlink", 2400000, 200000),
    ("ViaSat Ground Network", "Ground Segment", "Ground Systems", 890000, 74166.67),
    ("Microsoft Azure", "Cloud Infrastructure", "Ground Systems", 1200000, 100000),
    ("Deloitte Aerospace Advisory", "Professional Services", "Finance", 650000, 54166.67),
    ("Kirkland & Ellis LLP", "Legal", "Finance", 980000, 81666.67),
    ("Marsh Aerospace Insurance", "Insurance", "Mission Assurance", 3200000, 266666.67),
    ("Jacobs Engineering", "Facilities", "Launch Operations", 1100000, 91666.67),
    ("AECOM Launch Pad Services", "Facilities", "Launch Operations", 1450000, 120833.33),
    ("Ansys Simulation", "Engineering Software", "Propulsion", 420000, 35000),
    ("Siemens PLM", "Engineering Software", "Starship Program", 380000, 31666.67),
    ("Salesforce Government Cloud", "CRM", "Government Relations", 290000, 24166.67),
    ("Workday HCM", "HR Systems", "People Operations", 510000, 42500),
    ("Okta Identity", "Identity", "Mission Assurance", 180000, 15000),
    ("CrowdStrike Falcon", "Security", "Mission Assurance", 240000, 20000),
    ("Splunk Enterprise", "Observability", "Ground Systems", 320000, 26666.67),
    ("Datadog Infrastructure", "Observability", "Starlink", 275000, 22916.67),
    ("Flex Ltd Manufacturing", "Contract Manufacturing", "Starlink", 5600000, 466666.67),
    ("Precision Castparts", "Materials", "Propulsion", 3100000, 258333.33),
    ("ATI Titanium", "Materials", "Starship Program", 2700000, 225000),
    ("Spaceport America Ops", "Launch Range", "Launch Operations", 780000, 65000),
    ("Cape Canaveral Support Services", "Launch Range", "Launch Operations", 920000, 76666.67),
    ("FCC Spectrum Licensing", "Regulatory", "Starlink", 450000, 37500),
    ("FAA Launch Licensing", "Regulatory", "Launch Operations", 380000, 31666.67),
    ("LinkedIn Recruiting", "Recruiting", "People Operations", 420000, 35000),
    ("Glassdoor Employer Brand", "Recruiting", "People Operations", 95000, 7916.67),
    ("Concur Travel", "Travel", "Finance", 160000, 13333.33),
    ("American Express Corporate", "Travel", "Finance", 220000, 18333.33),
    ("Deloitte Tax", "Tax", "Finance", 340000, 28333.33),
    ("Ernst & Young Audit", "Audit", "Finance", 890000, 74166.67),
    ("Palantir Foundry", "Data Platform", "Starlink", 680000, 56666.67),
    ("Snowflake Analytics", "Data Platform", "Starlink", 520000, 43333.33),
    ("ServiceNow ITSM", "IT Operations", "Ground Systems", 410000, 34166.67),
    ("Atlassian Jira", "Engineering Tools", "Propulsion", 190000, 15833.33),
    ("GitHub Enterprise", "Engineering Tools", "Avionics", 210000, 17500),
    ("Ansys HFSS", "Simulation", "Avionics", 155000, 12916.67),
    ("Keysight Test Equipment", "Test Equipment", "Mission Assurance", 480000, 40000),
    ("National Instruments", "Test Equipment", "Mission Assurance", 360000, 30000),
    ("Blue Origin Launch Pad Lease", "Facilities", "Launch Operations", 1200000, 100000),
    ("Brownsville Port Authority", "Facilities", "Starship Program", 240000, 20000),
    ("Starlink Dish Logistics", "Logistics", "Starlink", 720000, 60000),
    ("FedEx Critical Parts", "Logistics", "Launch Operations", 380000, 31666.67),
    ("UPS Supply Chain", "Logistics", "Propulsion", 290000, 24166.67),
    ("Clean Harbors Waste", "Environmental", "Launch Operations", 175000, 14583.33),
    ("Aon Cyber Insurance", "Insurance", "Mission Assurance", 560000, 46666.67),
    ("Zscaler Zero Trust", "Security", "Mission Assurance", 195000, 16250),
    ("Palo Alto Networks", "Security", "Ground Systems", 280000, 23333.33),
    ("DocuSign Enterprise", "Legal Tech", "Finance", 85000, 7083.33),
    ("Iron Mountain Records", "Compliance", "Finance", 120000, 10000),
    ("Gartner Research", "Research", "Finance", 95000, 7916.67),
    ("Space Foundation Membership", "Industry", "Government Relations", 45000, 3750),
    ("Satellite Industry Association", "Industry", "Government Relations", 38000, 3166.67),
    ("Panasonic Energy Starlink", "Starlink Hardware", "Starlink", 4100000, 341666.67),
    ("AWS GovCloud", "Cloud Infrastructure", "Government Relations", 1900000, 158333.33),
    ("Microsoft Starlink Partnership", "Cloud Infrastructure", "Starlink", 1350000, 112500),
]

CUSTOMERS = [
    "NASA Commercial Crew",
    "NASA Artemis HLS",
    "NASA CRS Resupply",
    "US Space Force NSSL",
    "National Reconnaissance Office",
    "SES Global",
    "Eutelsat",
    "OneWeb",
    "Intuitive Machines",
    "AST SpaceMobile",
    "Telesat Lightspeed",
    "EchoStar Hughes",
    "US Air Force Research Lab",
    "European Space Agency",
    "JAXA",
    "Planet Labs",
    "Maxar Technologies",
    "Spire Global",
    "Capella Space",
    "BlackSky",
    "US Coast Guard Starlink",
    "US Army Starlink",
    "Royal Caribbean Starlink",
    "Hawaiian Airlines Starlink",
    "Philippines DICT Starlink",
]

PIPELINE_STAGES = [
    "Prospecting",
    "Technical validation",
    "Legal",
    "Security review",
    "Procurement",
    "Closed won",
    "Verbal commit",
    "Qualification",
]

PIPELINE_OWNERS = [
    "Gwynne Shotwell",
    "Tim Hughes",
    "Bret Johnsen",
    "Mark Juncosa",
    "Lee Rosen",
    "Kathy Lueders",
]

LOCATIONS = [
    "Hawthorne CA",
    "Boca Chica TX",
    "Cape Canaveral FL",
    "Vandenberg CA",
    "Redmond WA",
    "Austin TX",
    "Seattle WA",
    "Washington DC",
]

TEAMS = [
    "Engineering",
    "Launch Operations",
    "Starlink",
    "Starship Program",
    "Mission Assurance",
    "Finance",
    "People Operations",
    "Government Relations",
]

ROLES = [
    "Propulsion Engineer",
    "Avionics Engineer",
    "Mission Manager",
    "Launch Director",
    "Starlink Network Engineer",
    "Starship Welding Specialist",
    "Flight Software Engineer",
    "Ground Systems Engineer",
    "Finance Manager",
    "Security Engineer",
]

MANAGERS = [
    "Elon Musk",
    "Gwynne Shotwell",
    "Mark Juncosa",
    "Lee Rosen",
    "Tim Hughes",
]

SECURITY_FRAMEWORKS = ["SOC 2", "ISO 27001", "NIST 800-53", "ITAR", "FAR", "Internal Trust"]
SECURITY_STATUSES = ["satisfied", "in_progress", "gap"]
SECURITY_OWNERS = ["Mission Assurance", "IT", "Engineering", "Compliance", "Operations", "Security"]

BOARD_POLICIES = [
    ("Runway floor", "finance", "Maintain at least eighteen months of runway unless the board approves a Starship acceleration exception.", "runway_floor_months", 18, "months", "Board"),
    ("Vendor commitment notification", "procurement", "New vendor commitments at or above this annual value require board notification before signature.", "vendor_commitment_board_notification", 5000000, "usd_per_year", "CFO"),
    ("Competitive renewal review", "procurement", "Contracts above this annual value need competitive review before renewal.", "vendor_competitive_review_value", 2500000, "usd_per_year", "Controller"),
    ("Renewal review window", "procurement", "Competitive review must begin this many days before renewal.", "vendor_competitive_review_days", 90, "days", "Security Lead"),
    ("ITAR compliance priority", "security", "Export-controlled launch and satellite work must maintain ITAR evidence before revenue recognition on government contracts.", "itar_compliance_priority", 1, "boolean", "Board"),
    ("Headcount funding basis", "people", "New roles must map to signed launch manifest revenue, Starlink subscriber growth, or documented Starship milestone savings.", "headcount_funding_basis", 1, "boolean", "CFO"),
    ("Falcon manifest approval", "revenue", "Management must retain evidence for SpaceX Falcon manifest changes before committing spend or forecast changes.", "", 25000000, "usd", "CFO"),
    ("Starlink capacity exception", "revenue", "Management must retain evidence for SpaceX Starlink capacity expansion before committing spend or forecast changes.", "", "", "", "Controller"),
    ("Starship capex memo", "finance", "Management must retain evidence for SpaceX Starship capital expenditure before committing spend or forecast changes.", "", "", "", "Security Lead"),
    ("Launch range safety review", "security", "Management must retain evidence for SpaceX launch range safety review before committing spend or forecast changes.", "", 15000000, "usd", "People Ops"),
    ("NASA contract modification", "procurement", "Management must retain evidence for SpaceX NASA contract modifications before committing spend or forecast changes.", "", "", "", "CFO"),
    ("Headcount exception memo", "people", "Management must retain evidence for SpaceX headcount exceptions before committing spend or forecast changes.", "", "", "", "Controller"),
    ("Budget variance notice", "finance", "Management must retain evidence for SpaceX budget variance before committing spend or forecast changes.", "", 5000000, "usd", "Security Lead"),
    ("Enterprise security review", "security", "Management must retain evidence for SpaceX enterprise security review before committing spend or forecast changes.", "", "", "", "People Ops"),
    ("Revenue forecast update", "revenue", "Management must retain evidence for SpaceX forecast changes before committing spend or forecast changes.", "", "", "", "CFO"),
    ("ITAR visitor access", "security", "Management must retain evidence for SpaceX ITAR-controlled facility access before committing spend or forecast changes.", "", 10000000, "usd", "Controller"),
    ("Material contract exception", "procurement", "Management must retain evidence for SpaceX material contract changes before committing spend or forecast changes.", "", "", "", "Security Lead"),
    ("Hiring exception memo", "people", "Management must retain evidence for SpaceX headcount exceptions before committing spend or forecast changes.", "", "", "", "People Ops"),
]


def _json_metadata(connector: str) -> dict:
    return {
        "synthetic": False,
        "fictional_company": COMPANY,
        "connector": connector,
        "export_batch": EXPORT_BATCH,
        "exported_at": EXPORTED_AT,
        "source_note": SOURCE_NOTE,
    }


def generate_vendors() -> list[dict]:
    records = []
    statuses = ["active", "renewal_review", "paused"]
    data_access = ["none", "customer data", "employee data"]
    approval = ["approved", "needs review"]
    notice_days = [30, 45, 60, 75, 90]
    for i, (name, category, owner, annual, monthly) in enumerate(VENDORS, start=1):
        renewal = date(2026, 7, 8) + timedelta(days=(i - 1) * 31)
        records.append(
            {
                "vendor_id": f"{CODE}-VEN-{i:03d}",
                "name": name,
                "category": category,
                "annual_cost": round(annual, 2),
                "monthly_cost": round(monthly, 2),
                "renewal_date": renewal.isoformat(),
                "status": statuses[i % 3] if i <= 3 else "active",
                "owner": owner,
                "termination_notice_days": notice_days[i % len(notice_days)],
                "auto_renew": i % 4 != 0,
                "board_approved": i % 5 == 0,
                "notes": f"{PRODUCT_LINE}; {'new or expanded commitment pending finance review.' if i == 1 else category.lower() + ' services'}",
                "contract_id": f"{CODE}-CT-{2400 + i - 1}",
                "source_system": "ContractVault",
                "data_access": data_access[i % len(data_access)],
                "approval_state": approval[i % len(approval)],
            }
        )
    return records


def generate_ledger(vendors: list[dict]) -> list[dict]:
    rows: list[dict] = []
    txn = 1
    accounts_expense = [
        ("6510 Infrastructure", "Infrastructure"),
        ("6520 Professional Services", "Professional Services"),
        ("6530 Security", "Security"),
        ("6540 Operations", "Operations"),
        ("6550 Software", "Software"),
    ]
    base_date = date(2026, 1, 3)
    for month in range(6):
        month_start = date(2026, month + 1, 1)
        # revenue receipts (5 per month)
        for j in range(5):
            customer = CUSTOMERS[(month * 5 + j) % len(CUSTOMERS)]
            amount = 85000000 + (month * 5 + j) * 4200000
            rows.append(
                {
                    "txn_id": f"{CODE}-GL-{txn:05d}",
                    "date": (month_start + timedelta(days=3 + j * 3)).isoformat(),
                    "account": "4000 Revenue",
                    "description": f"{COMPANY} customer receipt - {customer}",
                    "amount": amount,
                    "currency": "USD",
                    "category": "revenue",
                    "vendor_id": "",
                    "vendor_name": "",
                    "source_system": "CloudLedger",
                    "department": "Revenue",
                    "cost_center": f"{CODE}-REV",
                }
            )
            txn += 1
        # payroll (4 batches)
        payroll = 145000000 + month * 4200000
        for batch in range(4):
            rows.append(
                {
                    "txn_id": f"{CODE}-GL-{txn:05d}",
                    "date": (month_start + timedelta(days=6 + batch * 3)).isoformat(),
                    "account": "6000 Payroll",
                    "description": f"{COMPANY} payroll batch {batch + 1}",
                    "amount": -payroll,
                    "currency": "USD",
                    "category": "payroll",
                    "vendor_id": "",
                    "vendor_name": "",
                    "source_system": "CloudLedger",
                    "department": "People",
                    "cost_center": f"{CODE}-PAY",
                }
            )
            txn += 1
        # vendor payments (50 per month, matching other demo packs)
        for k, vendor in enumerate(vendors[:50]):
            acct, acct_cat = accounts_expense[k % len(accounts_expense)]
            day = 3 + (k % 27)
            rows.append(
                {
                    "txn_id": f"{CODE}-GL-{txn:05d}",
                    "date": (month_start + timedelta(days=day)).isoformat(),
                    "account": acct,
                    "description": f"{vendor['name']} payment for {month_start.strftime('%Y-%m')}",
                    "amount": -round(vendor["monthly_cost"] * (0.96 if month % 2 else 1.0), 2),
                    "currency": "USD",
                    "category": vendor["category"],
                    "vendor_id": vendor["vendor_id"],
                    "vendor_name": vendor["name"],
                    "source_system": "CloudLedger",
                    "department": vendor["owner"],
                    "cost_center": f"{CODE}-{200 + k % 50}",
                }
            )
            txn += 1
    return rows


def generate_invoices(vendors: list[dict]) -> list[dict]:
    rows = []
    approvers = ["Controller", "VP Operations", "Security Lead", "Revenue Ops", "CFO"]
    statuses = ["approved", "in review"]
    for i in range(240):
        vendor = vendors[i % len(vendors)]
        month = (i % 6) + 1
        issue = date(2026, month, 1) + timedelta(days=(i % 27))
        due = issue + timedelta(days=38)
        rows.append(
            {
                "invoice_id": f"{CODE}-AP-{i + 1:05d}",
                "vendor_name": vendor["name"],
                "vendor_id": vendor["vendor_id"],
                "issue_date": issue.isoformat(),
                "due_date": due.isoformat(),
                "amount": round(vendor["monthly_cost"] * (0.92 + (i % 7) * 0.02), 2),
                "currency": "USD",
                "status": statuses[i % len(statuses)],
                "po_number": "" if i % 11 == 0 else f"{CODE}-PO-{7100 + i:05d}",
                "period": f"2026-{month:02d}",
                "department": vendor["owner"],
                "cost_center": f"{CODE}-{100 + i % 28}",
                "approver": approvers[i % len(approvers)],
                "source_system": "PayablesDesk",
            }
        )
    return rows


def generate_pipeline() -> list[dict]:
    rows = []
    segments = ["enterprise", "mid-market", "strategic", "commercial", "government"]
    forecasts = ["pipeline", "best case", "commit"]
    for i in range(150):
        customer = CUSTOMERS[i % len(CUSTOMERS)]
        stage = PIPELINE_STAGES[i % len(PIPELINE_STAGES)]
        arr = 28000000 + (i % 25) * 2100000
        prob = [0.12, 0.25, 0.45, 0.55, 0.65, 0.75, 0.88, 1.0][i % 8]
        close_month = (i % 6) + 7
        close_day = (i % 28) + 1
        rows.append(
            {
                "opportunity_id": f"{CODE}-OPP-{i + 1:05d}",
                "name": f"{customer} - {PRODUCT_LINE}",
                "account": customer,
                "stage": stage,
                "arr": arr,
                "probability": prob,
                "weighted_arr": round(arr * prob, 2),
                "close_date": date(2026 if close_month <= 12 else 2027, close_month if close_month <= 12 else 1, close_day).isoformat(),
                "owner": PIPELINE_OWNERS[i % len(PIPELINE_OWNERS)],
                "segment": segments[i % len(segments)],
                "forecast_category": forecasts[i % len(forecasts)],
                "security_review_required": "yes" if i % 7 == 0 else "no",
                "source_system": "PipelineHub",
            }
        )
    return rows


def generate_headcount() -> list[dict]:
    rows = []
    funding = ["security blocker", "approved plan", "signed revenue", "Starship milestone"]
    statuses = ["open", "planned", "filled"]
    req = 1
    slot = 0
    while len(rows) < 62:
        team = TEAMS[slot % len(TEAMS)]
        team_idx = slot % len(TEAMS)
        slot_num = (slot // len(TEAMS)) + 1
        rows.append(
            {
                "team": team,
                "role": f"{ROLES[(team_idx + slot_num) % len(ROLES)]} {slot_num}",
                "headcount": (slot_num % 3) + 1,
                "monthly_cost": 18500 + slot_num * 1750 + team_idx * 1200,
                "start_month": f"2026-{(slot_num % 6) + 1:02d}",
                "status": statuses[slot_num % len(statuses)],
                "funding_basis": funding[slot_num % len(funding)],
                "requisition_id": f"{CODE}-REQ-{req:04d}",
                "manager": MANAGERS[team_idx % len(MANAGERS)],
                "location": LOCATIONS[(team_idx + slot_num) % len(LOCATIONS)],
                "source_system": "PeopleRoster",
            }
        )
        req += 1
        slot += 1
    return rows


def generate_security() -> list[dict]:
    titles = [
        "ITAR export control ownership",
        "Launch range safety reporting",
        "Starlink subscriber data handling",
        "Falcon vehicle telemetry retention",
        "Starship test site access reviews",
        "NASA contract evidence chain",
        "NSSL mission assurance artifacts",
        "Board reporting cadence",
        "Risk assessment refresh",
        "Access review evidence",
        "Logical access provisioning",
        "Vendor security assessment",
        "Incident response tabletop",
        "Change management evidence",
        "Encryption key rotation",
        "Physical security badge audit",
    ]
    records = []
    for i in range(48):
        framework = SECURITY_FRAMEWORKS[i % len(SECURITY_FRAMEWORKS)]
        status = SECURITY_STATUSES[i % len(SECURITY_STATUSES)]
        blocks = status == "gap"
        records.append(
            {
                "control_id": f"CC{(i % 8) + 1}.{(i % 3) + 1}-{CODE}-{i + 1:03d}",
                "framework": framework,
                "title": titles[i % len(titles)],
                "status": status,
                "owner": SECURITY_OWNERS[i % len(SECURITY_OWNERS)],
                "evidence_date": date(2026, 5 if blocks else 6, (i % 28) + 1).isoformat(),
                "blocks_revenue": blocks,
                "blocked_arr": 85000000 if blocks else "",
                "summary": (
                    f"{titles[i % len(titles)]} evidence is required by a government launch customer security review."
                    if blocks
                    else f"{titles[i % len(titles)]} evidence retained in TrustVault export."
                ),
                "ticket_id": f"{CODE}-TRUST-{900 + i:04d}",
                "artifact_name": f"cc{(i % 8) + 1}-{(i % 3) + 1}-{PREFIX}-evidence.pdf",
                "source_system": "TrustVault",
            }
        )
    return records


def generate_board_policies() -> list[dict]:
    records = []
    for i, (title, category, text, rule, threshold, unit, owner) in enumerate(BOARD_POLICIES, start=1):
        records.append(
            {
                "policy_id": f"{CODE}-BP-{i:03d}",
                "title": title,
                "category": category,
                "text": text.replace("SpaceX", COMPANY),
                "rule": rule,
                "threshold": threshold,
                "unit": unit,
                "owner": owner,
                "effective_date": "2026-01-01",
                "review_cadence": "quarterly",
                "source_system": "BoardPortal",
            }
        )
    return records


def write_csv(path: Path, fieldnames: list[str], rows: list[dict]) -> int:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def write_json(path: Path, connector: str, records: list[dict]) -> int:
    payload = {"metadata": _json_metadata(connector), "records": records}
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return len(records)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    vendors = generate_vendors()
    ledger = generate_ledger(vendors)
    invoices = generate_invoices(vendors)
    pipeline = generate_pipeline()
    headcount = generate_headcount()
    security = generate_security()
    board = generate_board_policies()

    counts = {
        "ledger": write_csv(
            OUT / f"{PREFIX}_CloudLedger_GL_Detail_2026-06-30.csv",
            [
                "txn_id",
                "date",
                "account",
                "description",
                "amount",
                "currency",
                "category",
                "vendor_id",
                "vendor_name",
                "source_system",
                "department",
                "cost_center",
            ],
            ledger,
        ),
        "invoices": write_csv(
            OUT / f"{PREFIX}_PayablesDesk_AP_Aging_Detail_2026-06-30.csv",
            [
                "invoice_id",
                "vendor_name",
                "vendor_id",
                "issue_date",
                "due_date",
                "amount",
                "currency",
                "status",
                "po_number",
                "period",
                "department",
                "cost_center",
                "approver",
                "source_system",
            ],
            invoices,
        ),
        "vendors": write_json(
            OUT / f"{PREFIX}_ContractVault_vendor_register_2026-07.json",
            "vendor_export",
            vendors,
        ),
        "pipeline": write_csv(
            OUT / f"{PREFIX}_PipelineHub_opportunity_pipeline_2026-Q3.csv",
            [
                "opportunity_id",
                "name",
                "account",
                "stage",
                "arr",
                "probability",
                "weighted_arr",
                "close_date",
                "owner",
                "segment",
                "forecast_category",
                "security_review_required",
                "source_system",
            ],
            pipeline,
        ),
        "headcount": write_csv(
            OUT / f"{PREFIX}_PeopleRoster_headcount_plan_2026-H2.csv",
            [
                "team",
                "role",
                "headcount",
                "monthly_cost",
                "start_month",
                "status",
                "funding_basis",
                "requisition_id",
                "manager",
                "location",
                "source_system",
            ],
            headcount,
        ),
        "security": write_json(
            OUT / f"{PREFIX}_TrustVault_security_control_evidence_2026-Q3.json",
            "security_evidence",
            security,
        ),
        "board": write_json(
            OUT / f"{PREFIX}_BoardPortal_board_policy_register_2026.json",
            "board_policy",
            board,
        ),
    }
    total = sum(counts.values())
    readme = f"""# SpaceX Demo Upload Pack

Public-fact-grounded internal export bundle for the Atlas demo. This folder mirrors the finance data-room bundle shape used by other demo upload packs. Files are themed on publicly reported SpaceX profile data (founded 2002, Hawthorne CA, Falcon 9, Starlink, Starship, NASA and commercial launch services) while remaining illustrative operating records rather than proprietary filings.

| File | Upload slot | Records |
| --- | --- | ---: |
| `{PREFIX}_CloudLedger_GL_Detail_2026-06-30.csv` | Ledger | {counts['ledger']} |
| `{PREFIX}_PayablesDesk_AP_Aging_Detail_2026-06-30.csv` | Invoices | {counts['invoices']} |
| `{PREFIX}_ContractVault_vendor_register_2026-07.json` | Vendors | {counts['vendors']} |
| `{PREFIX}_PipelineHub_opportunity_pipeline_2026-Q3.csv` | Sales pipeline | {counts['pipeline']} |
| `{PREFIX}_PeopleRoster_headcount_plan_2026-H2.csv` | Hiring plan | {counts['headcount']} |
| `{PREFIX}_TrustVault_security_control_evidence_2026-Q3.json` | Security notes | {counts['security']} |
| `{PREFIX}_BoardPortal_board_policy_register_2026.json` | Board rules | {counts['board']} |

Total parsed records: {total}.
"""
    (OUT / "README.md").write_text(readme, encoding="utf-8")
    print(f"Wrote {OUT} — {total} records")
    for key, value in counts.items():
        print(f"  {key}: {value}")


if __name__ == "__main__":
    main()
