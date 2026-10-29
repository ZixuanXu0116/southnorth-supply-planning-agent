"""The purchasing and supplier pages (decision record 005): role gating, the full role-play flow, the
outward-only supplier view, escaping of untrusted text, the Excel exchange, and the audit log."""

from __future__ import annotations

import io
import json
import re
from datetime import UTC, date, datetime, timedelta

import polars as pl
import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from snm_cases import excel
from snm_cases.service import Actor, CaseService
from snm_cases.store import LocalCaseStore
from snm_web.app import WebSettings, create_app

AS_OF = date(2026, 9, 21)
CASE = "C-US10-40000231-SH-20260925"
TRANSFER = "C-US10-40000231-PU-20260925"
VENDOR = "0000100007"
OTHER = "0000100012"
COSTS = {
    "line_down_cost_a": 5000.0,
    "line_down_cost_b": 1000.0,
    "line_down_cost_c": 200.0,
    "holding_rate_annual": 0.2,
    "transfer_cost_fixed": 40.0,
    "transfer_cost_per_unit": 0.5,
    "order_cost": 75.0,
    "expedite_freight_default": 150.0,
}


def case_row(**overrides):
    row = {
        "case_id": CASE,
        "as_of_date": AS_OF,
        "material": "40000231",
        "description": "VALVE, GATE, KF40",
        "plant": "US10",
        "site": "Round Rock",
        "criticality": "B",
        "condition": "shortage",
        "trigger_date": date(2026, 9, 26),
        "gap_qty": 9.0,
        "value_usd": 5000.0,
        "lever": "expedite",
        "lever_qty": 20.0,
        "lever_detail": json.dumps({"ebeln": "4500001234", "ebelp": "00010", "days_gained": 11}),
        "coverage_date": date(2026, 9, 24),
        "covers_in_time": True,
        "days_short": 0,
        "lever_cost_usd": 450.0,
        "stockout_cost_usd": 0.0,
        "vendor": VENDOR,
        "vendor_name": "Sakuragawa Ceramics",
        "supplier_class": "C",
        "triage_class": "agent_eligible",
        "triage_reasons": "tail supplier",
        "alternatives": json.dumps(
            [{"lever": "new_order", "coverage_date": "2026-11-09", "qty": 10.0, "lead_days": 49}]
        ),
        "alternative_costs_usd": json.dumps({"expedite": 450.0, "new_order": 75.0}),
        "rationale": "Projected stock falls below zero on 2026-09-25; gap 10.",
        "engine_version": "1.0.0",
        "params_digest": "abc",
    }
    row.update(overrides)
    return row


class FakePlanning:
    def __init__(self, rows):
        self.rows = rows

    def cases(self):
        return pl.DataFrame(self.rows)

    def position(self, material, plant):
        days = [AS_OF + timedelta(days=d) for d in range(0, 21)]
        projected = [4 - d + (20 if d >= 14 else 0) for d in range(0, 21)]
        return pl.DataFrame(
            {
                "day": days,
                "projected_qty": [float(v) for v in projected],
                "safety_stock_qty": [2.0] * 21,
                "max_stock_qty": [40.0] * 21,
                "firm_demand_qty": [2.0] * 21,
                "forecast_qty": [0.0] * 21,
                "planned_order_qty": [0.0] * 21,
                "receipt_qty": [20.0 if d == 14 else 0.0 for d in range(21)],
            }
        )

    def suppliers(self):
        # The vendor's terms make the fixture's 450 USD expedite consistent with the engine (2.7 % plus 150).
        return pl.DataFrame(
            {
                "vendor": [VENDOR, OTHER],
                "name": ["Sakuragawa Ceramics", "Oriel Motion Robotics"],
                "surcharge_pct_lt7": [4.0, None],
                "surcharge_pct_7_13": [2.7, None],
                "surcharge_pct_ge14": [6.0, None],
                "expedite_freight_usd": [150.0, None],
            }
        )

    def costs(self):
        return COSTS


class Clock:
    def __init__(self):
        self.now = datetime(2026, 9, 21, 9, 0, tzinfo=UTC)

    def __call__(self):
        self.now += timedelta(minutes=1)
        return self.now


@pytest.fixture
def env(tmp_path):
    transfer = case_row(
        case_id=TRANSFER,
        condition="pull",
        lever="transfer",
        lever_qty=9.0,
        lever_detail=json.dumps({"from_location": "2001", "to_location": "1000", "lead_days": 0}),
        alternatives="[]",
        alternative_costs_usd=json.dumps({"transfer": 44.5}),
        triage_class="human_handled",
        triage_reasons="pull from the overflow hub decided by logistics",
        coverage_date=AS_OF,
    )
    service = CaseService(
        LocalCaseStore(tmp_path / "store"), FakePlanning([case_row(), transfer]), clock=Clock()
    )
    service.sync_engine_run("plan-1")
    cli = tmp_path / "case.js"
    cli.write_text("// stand-in")
    calls = []

    def planner(settings, case_id, task, role, as_of):
        calls.append((case_id, task, role, as_of))
        return {
            "status": "written",
            "written": {"tool": "draft_supplier_request"},
            "usage": {"cost_usd": 0.04},
        }

    settings = WebSettings(
        mode="local",
        runs_dir=tmp_path / "runs",
        store_dir=tmp_path / "store",
        planning_dir=tmp_path,
        harness_cli=cli,
    )
    client = TestClient(create_app(service, settings, planner=planner))
    return client, service, settings, calls


def play(client, role):
    response = client.post("/role", data={"role": role}, follow_redirects=False)
    assert response.status_code == 303


def text_of(html: str) -> str:
    return re.sub(r"<[^>]+>", " ", html)


def fill(sheet: bytes, rows: list[dict]) -> bytes:
    book = load_workbook(io.BytesIO(sheet))
    table = book[excel.SHEET]
    keys = [key for key, _ in excel.COLUMNS]
    for index, row in enumerate(rows, start=1):
        for column, key in enumerate(keys, start=1):
            if key in row:
                table.cell(row=excel.TABLE_ROW + index, column=column, value=row[key])
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def send_request(client, service):
    play(client, "purchasing")
    client.post(
        f"/cases/{CASE}/draft",
        data={"request_type": "expedite", "message": service.default_request_text(CASE, "expedite")},
    )
    page = client.get(f"/cases/{CASE}").text
    assert "Approve and send" in page and 'name="surcharge_pct_max" value="2.7"' in page
    mandate = service.mandate_defaults(Actor("human", "purchasing"), CASE)["mandate"]
    form = {key: str(value) for key, value in mandate.items() if key != "allowed_kinds"}
    form["allowed_kinds"] = mandate["allowed_kinds"]
    response = client.post(f"/cases/{CASE}/send", data=form, follow_redirects=False)
    assert "Recorded" in response.headers["location"]


def test_pages_require_the_right_role(env):
    client, *_ = env
    assert "This page is for purchasing" in client.get("/cases").text
    play(client, f"supplier_{VENDOR}")
    assert "This page is for purchasing" in client.get(f"/cases/{CASE}").text
    play(client, "purchasing")
    assert "This page is for a supplier" in client.get("/supplier").text
    assert "Unknown role" in client.post("/role", data={"role": "site_manager_rr"}).text  # logistics: no page


def test_queue_and_case_page(env):
    client, *_ = env
    play(client, "purchasing")
    queue = client.get("/cases").text
    assert CASE in queue and TRANSFER not in queue  # order cases by default
    assert TRANSFER in client.get("/cases?scope=transfers").text
    page = client.get(f"/cases/{CASE}").text
    assert "Projected stock falls below zero" in page and "Let the planner agent prepare it" in page
    assert "4500001234" in page  # the default request text names the open order
    # Doing nothing is priced too: 9 days below zero at 1,000 USD for criticality B.
    assert "the cost of not acting" in page and "9,000.00" in page


def test_case_page_shows_the_stock_out_risk_of_each_option(tmp_path):
    """A demand model with demand of 14 every week (probability 1) equals the fixture's firm demand of 2 a
    day, so every simulated path is the projection: doing nothing runs out on the trigger date (100 %, 9 days
    below zero at 1,000 USD), the expedite to 2026-09-24 never does (0 %)."""
    model = {
        "model": "sba", "weeks": 26, "alpha": 0.15, "rate_per_week": 14.0, "probability": 1.0,
        "sizes": [14.0], "frequency": "10-13 weeks", "service_target": 0.98, "calibration": [[0.1, 0.14]],
    }  # fmt: skip
    row = case_row(demand_model=json.dumps(model), stockout_risk_no_action=1.0)
    service = CaseService(LocalCaseStore(tmp_path / "store"), FakePlanning([row]), clock=Clock())
    service.sync_engine_run("plan-1")
    buyer = Actor("human", "purchasing")
    assert service.evaluate_option(buyer, CASE, "no_action")["stockout_risk"] == "100.00%"
    settings = WebSettings(mode="local", runs_dir=tmp_path / "runs", store_dir=tmp_path / "store",
                           planning_dir=tmp_path, harness_cli=tmp_path / "none.js")  # fmt: skip
    client = TestClient(create_app(service, settings))
    play(client, "purchasing")
    page = client.get(f"/cases/{CASE}").text
    assert "100.00% chance of running out by 2026-09-26" in page
    assert "Stock-out risk" in page and "Expected total" in page and "Expected line-down" in page
    rows = {
        cells[0].split()[0]: cells
        for cells in (
            [text_of(cell).strip() for cell in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
            for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", page.split("Cost breakdown")[0], re.S)
        )
        if len(cells) == 6  # no proposals yet: no rank and mandate columns
    }
    assert rows["Do"][2] == "100.00%" and rows["Do"][4] == "9,000.00"
    assert rows["expedite"][2] == "0.00%" and rows["expedite"][4] == "0.00"
    assert "the class B target is at most 2%" in " ".join(text_of(page).split())


def test_full_role_play_flow(env):
    client, service, settings, _ = env
    send_request(client, service)
    assert service.view(CASE).state == "sent"
    # The supplier sees its request, outward fields only.
    play(client, f"supplier_{VENDOR}")
    assert CASE in client.get("/supplier").text
    page = client.get(f"/supplier/{CASE}").text
    visible = text_of(page)
    for internal in (
        "5,000.00",
        "Projected stock",
        "tail supplier",
        "Oriel",
        "agent_eligible",
        "2.7 %",
        "mandate",
        "Value at risk",
        "expedite costs",
        "within",
    ):
        assert internal not in visible, internal
    link = re.search(rf'href="(/supplier/{CASE}/files/[^"]+)"', page).group(1)
    sheet = client.get(link).content
    filled = fill(
        sheet,
        [
            {
                "kind": "expedite",
                "quantity": 20,
                "arrival_date": "2026-09-25",
                "surcharge_pct": 2,
                "freight_amount": 120,
                "valid_until": "2026-09-28",
            },
            {
                "kind": "expedite",
                "quantity": 20,
                "arrival_date": "2026-09-24",
                "surcharge_pct": 6,
                "valid_until": "2026-09-28",
                "split_deliveries": "10@2026-09-24; 10@2026-09-27",
            },
        ],
    )
    response = client.post(
        f"/supplier/{CASE}/reply",
        data={"body": "Two options, see the sheet."},
        files={"sheet": ("reply.xlsx", filled, excel_type())},
        follow_redirects=False,
    )
    assert "Recorded" in response.headers["location"], response.headers["location"]
    assert "10 on 2026-09-27" in client.get(f"/supplier/{CASE}").text
    # Purchasing sees the proposals compared with the mandate and decides.
    play(client, "purchasing")
    page = client.get(f"/cases/{CASE}").text
    assert "in mandate" in page and '<span class="tag no">' in page and "recommended" in page  # fields missed
    recommended = service.recommended_proposal(CASE)
    client.post(f"/cases/{CASE}/decide", data={"choice": recommended, "rationale": "Inside the mandate."})
    assert service.view(CASE).state == "handed_off"
    # The receipt form starts from the decided proposal, not the engine lever.
    page = client.get(f"/cases/{CASE}").text
    assert 'name="received" value="2026-09-25"' in page and 'name="quantity" value="20"' in page
    client.post(f"/cases/{CASE}/simulate/receipt", data={"quantity": "20", "received": "2026-09-25"})
    assert service.view(CASE).state == "closed"
    events = [(e.event_type, e.actor_kind) for e in service.store.events(CASE)]
    assert ("decision_recorded", "human") in events and ("handed_off", "system") in events
    assert [a.direction for a in service.store.attachments(CASE)] == ["outbound", "inbound"]
    play(client, f"supplier_{VENDOR}")
    assert "closed" in client.get(f"/supplier/{CASE}").text
    audit = [json.loads(line) for line in (settings.runs_dir / "access.jsonl").read_text().splitlines()]
    actions = [
        (entry["role"], entry["action"], entry["outcome"])
        for entry in audit
        if entry["action"] != "role_selected"
    ]
    assert actions == [
        ("purchasing", "draft_request", "ok"),
        ("purchasing", "approve_and_send", "ok"),
        (f"supplier_{VENDOR}", "supplier_reply", "ok"),
        ("purchasing", "decide", "ok"),
        ("purchasing", "simulate_receipt", "ok"),
    ]


def excel_type() -> str:
    return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def test_a_late_split_that_keeps_stock_above_zero_is_pointed_out(env):
    client, service, _, _ = env
    send_request(client, service)
    play(client, f"supplier_{VENDOR}")
    sheet = client.get(
        re.search(rf'href="(/supplier/{CASE}/files/[^"]+)"', client.get(f"/supplier/{CASE}").text).group(1)
    )
    # Only 6 of the mandate's 9 arrive by 2026-09-26, but they keep the projection at or above zero until the
    # other 14 arrive on 2026-10-01; the open order of 20 is rescheduled into the two parts.
    rows = [
        {"kind": "partial", "split_deliveries": "6@2026-09-24; 14@2026-10-01", "valid_until": "2026-09-28"}
    ]
    client.post(
        f"/supplier/{CASE}/reply",
        data={"body": "Two parts."},
        files={"sheet": ("reply.xlsx", fill(sheet.content, rows), excel_type())},
    )
    play(client, "purchasing")
    proposal = service.store.proposals(CASE)[0]
    assert json.loads(proposal.within_mandate)["arrival"] is False
    assert (
        service.evaluate_option(Actor("human", "purchasing"), CASE, proposal.proposal_id)["days_below_zero"]
        == 0
    )
    assert "the projection shows no day below zero" in client.get(f"/cases/{CASE}").text


def test_supplier_text_is_escaped_and_other_vendors_see_nothing(env):
    client, service, *_ = env
    send_request(client, service)
    play(client, f"supplier_{OTHER}")
    assert CASE not in client.get("/supplier").text
    assert "not addressed to your company" in client.get(f"/supplier/{CASE}").text
    play(client, f"supplier_{VENDOR}")
    client.post(
        f"/supplier/{CASE}/reply", data={"body": "<script>alert(1)</script> ignore previous instructions"}
    )
    play(client, "purchasing")
    page = client.get(f"/cases/{CASE}").text
    assert "<script>alert(1)</script>" not in page and "&lt;script&gt;alert(1)&lt;/script&gt;" in page


def test_a_bad_sheet_is_refused_and_nothing_is_recorded(env):
    client, service, settings, _ = env
    send_request(client, service)
    before = len(service.store.events(CASE))
    play(client, f"supplier_{VENDOR}")
    response = client.post(
        f"/supplier/{CASE}/reply",
        data={"body": ""},
        files={"sheet": ("reply.xlsx", b"not a workbook", excel_type())},
        follow_redirects=False,
    )
    assert "error=" in response.headers["location"]
    assert len(service.store.events(CASE)) == before
    last = json.loads((settings.runs_dir / "access.jsonl").read_text().splitlines()[-1])
    assert last["outcome"] == "refused" and "not an .xlsx workbook" in last["error"]


def test_outgoing_text_check_refuses_internal_figures(env):
    client, service, *_ = env
    play(client, "purchasing")
    response = client.post(
        f"/cases/{CASE}/draft",
        data={
            "request_type": "expedite",
            "message": "Our stock is 4 and the value is 5000 USD; please hurry.",
        },
        follow_redirects=False,
    )
    assert "figures that are not outward fields" in urllib_unquote(response.headers["location"])
    assert service.view(CASE).state == "new"


def urllib_unquote(value: str) -> str:
    import urllib.parse

    return urllib.parse.unquote_plus(value)


def test_planner_button_and_transfer_publication(env):
    client, service, _, calls = env
    play(client, "purchasing")
    client.post(f"/cases/{CASE}/planner", data={"task": "prepare"})
    assert calls == [(CASE, "prepare", "purchasing", "2026-09-21")]
    client.post(f"/cases/{TRANSFER}/simulate/publish-transfer")
    events = [(e.event_type, e.actor_kind) for e in service.store.events(TRANSFER)]
    assert events[-2:] == [("transfer_prepared", "system"), ("handed_off", "system")]


def test_planner_evaluation_button_and_recommendation(env):
    client, service, _, calls = env
    send_request(client, service)
    play(client, f"supplier_{VENDOR}")
    client.post(
        f"/supplier/{CASE}/reply",
        data={"body": "One option.", "kind": "expedite", "quantity": "20", "arrival_date": "2026-09-25",
              "surcharge_pct": "2", "valid_until": "2026-09-28"},
    )  # fmt: skip
    play(client, "purchasing")
    assert "Let the planner agent evaluate the proposals" in client.get(f"/cases/{CASE}").text
    client.post(f"/cases/{CASE}/planner", data={"task": "evaluate"})
    assert calls[-1][:2] == (CASE, "evaluate")
    # What the planner writes (the stand-in above does not run the harness).
    proposal = service.store.proposals(CASE)[0].proposal_id
    planner = Actor("planner_agent", "purchasing", run_id="run-7")
    options = [
        {
            "option": o,
            "evaluation_id": service.evaluate_option(planner, CASE, o)["evaluation_id"],
            "rank": rank,
        }
        for rank, o in enumerate([proposal, "expedite"], start=1)
    ]
    rationale = "Recommend the offer of the supplier: it covers on time and costs least of the options."
    service.propose_recommendation(planner, CASE, options, proposal, rationale)
    page = client.get(f"/cases/{CASE}").text
    assert "Planner recommendation" in page and "run run-7" in page and rationale in page
    assert "planner's choice" in page and "evaluate the proposals" not in page  # asked once per answer
    assert "engine estimate" in page and "Do nothing" in page  # reference rows, not ranked
    assert '<tr class="ref">' in page and '<td class="num">1</td>' in page
    assert "submitted another order" not in page  # the planner's order matches the purchasing rule here
    assert (
        client.post(f"/cases/{CASE}/planner", data={"task": "handoff"}, follow_redirects=False).status_code
        == 303
    )
    assert calls[-1][1] == "evaluate"  # an unknown task never reaches the harness
    # The recommendation is advice: purchasing can still counter-offer and the supplier can answer again.
    assert "Message to the supplier" in page
    client.post(f"/cases/{CASE}/message", data={"body": "Could you waive the freight?"})
    play(client, f"supplier_{VENDOR}")
    client.post(
        f"/supplier/{CASE}/reply",
        data={"body": "Freight waived.", "kind": "expedite", "quantity": "20", "arrival_date": "2026-09-25",
              "surcharge_pct": "2", "valid_until": "2026-09-28"},
    )  # fmt: skip
    assert service.view(CASE).state == "responded" and len(service.store.proposals(CASE)) == 2
    play(client, "purchasing")
    page = client.get(f"/cases/{CASE}").text
    assert "New proposals arrived after this recommendation" in page and "evaluate the proposals" in page


def test_cases_the_latest_run_no_longer_flags_are_hidden_by_default(env):
    client, service, *_ = env
    service.planning.rows = [service.planning.rows[1]]  # the next engine run flags only the transfer case
    service.sync_engine_run("plan-2")
    play(client, "purchasing")
    assert CASE not in client.get("/cases").text
    shown = client.get("/cases?cleared=true").text
    assert CASE in shown and "no longer flagged" in shown


def test_the_planner_names_missing_settings_instead_of_failing(tmp_path, monkeypatch):
    """Started without the settings file loaded, the planner button says which variables are missing and how
    to load them, without running the harness (and without reading any value)."""
    from snm_web.agents import run_planner

    monkeypatch.delenv("SNM_MODEL_MAIN", raising=False)
    monkeypatch.setenv("SNM_ANTHROPIC_API_KEY", "set")
    cli = tmp_path / "case.js"
    cli.write_text("// stand-in")
    settings = WebSettings(mode="local", runs_dir=tmp_path / "runs", store_dir=tmp_path / "store",
                           planning_dir=tmp_path, harness_cli=cli)  # fmt: skip
    result = run_planner(settings, CASE, "evaluate", "purchasing", "2026-09-21")
    assert result["status"] == "error"
    assert (
        result["error"].startswith("SNM_MODEL_MAIN not set") and "source ~/.config/snm/env" in result["error"]
    )


def test_no_proposal_inside_the_mandate_is_explained(tmp_path):
    """When every proposal misses a field the rule does not price, nothing is marked recommended and the page
    says why; a reference row within the service target is shown as such."""
    model = {
        "model": "sba", "weeks": 26, "alpha": 0.15, "rate_per_week": 14.0, "probability": 1.0,
        "sizes": [14.0], "frequency": "10-13 weeks", "service_target": 0.90, "calibration": [[0.1, 0.14]],
    }  # fmt: skip
    row = case_row(criticality="C", demand_model=json.dumps(model), stockout_risk_no_action=1.0)
    service = CaseService(LocalCaseStore(tmp_path / "store"), FakePlanning([row]), clock=Clock())
    service.sync_engine_run("plan-1")
    settings = WebSettings(mode="local", runs_dir=tmp_path / "runs", store_dir=tmp_path / "store",
                           planning_dir=tmp_path, harness_cli=tmp_path / "none.js")  # fmt: skip
    client = TestClient(create_app(service, settings))
    send_request(client, service)
    play(client, f"supplier_{VENDOR}")
    client.post(
        f"/supplier/{CASE}/reply",
        data={"body": "One option.", "kind": "expedite", "quantity": "20", "arrival_date": "2026-09-25",
              "surcharge_pct": "30", "valid_until": "2026-09-28"},
    )  # fmt: skip
    play(client, "purchasing")
    page = client.get(f"/cases/{CASE}").text
    assert "None is marked recommended" in " ".join(text_of(page).split())
    assert 'class="tag rec">recommended' not in page
    assert '<td class="num good">0.00%</td>' in page  # the engine's expedite, within the target
