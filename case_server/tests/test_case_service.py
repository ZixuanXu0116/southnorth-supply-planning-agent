"""Case store and case service: state machine, roles, masking, outward check, evaluation, engine sync."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import polars as pl
import pytest

from snm_cases.evaluate import OptionSpec, Receipt, evaluate
from snm_cases.service import Actor, CaseError, CaseService, allowed_numbers, numbers_in
from snm_cases.store import DatabricksCaseStore, Event, LocalCaseStore, StoreConflict

AS_OF = date(2026, 9, 21)
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
        "case_id": "C-US10-40000231-SH-20260925",
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
        "vendor": "0000100007",
        "vendor_name": "Sakuragawa Ceramics",
        "supplier_class": "C",
        "triage_class": "agent_eligible",
        "triage_reasons": "tail supplier",
        "alternatives": json.dumps(
            [{"lever": "new_order", "coverage_date": "2026-11-09", "qty": 10.0, "lead_days": 49}]
        ),
        "alternative_costs_usd": json.dumps({"expedite": 450.0, "new_order": 75.0}),
        "rationale": "Projected stock falls below zero on 2026-09-25; gap 10.",
        "engine_version": "0.1.0",
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
        # 4 on hand, 1 per day of demand; the open order of 20 arrives on day 14 (2026-10-05), so the stock is
        # below zero from day 5 (2026-09-26) to day 13 (2026-10-04).
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
        # Sakuragawa's terms make the engine's expedite of the fixture consistent: 2.7 % of 20 x 5000/9 USD
        # plus 150 USD freight is the 450 USD lever cost.
        return pl.DataFrame(
            {
                "vendor": ["0000100007", "0000100012"],
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
def service(tmp_path):
    svc = CaseService(LocalCaseStore(tmp_path / "store"), FakePlanning([case_row()]), clock=Clock())
    svc.sync_engine_run("plan-1")
    return svc


CASE = "C-US10-40000231-SH-20260925"
PURCHASING = Actor("human", "purchasing")
PLANNER = Actor("planner_agent", "purchasing", run_id="run-1")
INTAKE = Actor("intake_agent", "intake", run_id="run-2", case_binding=CASE)
PORTAL = Actor("system", "supplier_portal")
REQUEST = "Please confirm whether purchase order 4500001234 item 00010 (20 pieces) can arrive by 2026-09-26."


def test_engine_sync_opens_updates_and_clears(tmp_path):
    planning = FakePlanning([case_row()])
    svc = CaseService(LocalCaseStore(tmp_path / "s"), planning, clock=Clock())
    assert svc.sync_engine_run() == {"opened": 1, "updated": 0, "unchanged": 0, "cleared": 0}
    assert svc.sync_engine_run()["unchanged"] == 1
    planning.rows = [case_row(case_id="C-US10-40000231-SH-20260927", trigger_date=date(2026, 9, 27))]
    assert svc.sync_engine_run()["updated"] == 1
    assert [v.case_id for v in svc.all_views()] == [CASE]  # the identifier keeps the first trigger date
    planning.rows = [case_row(material="40000999", case_id="C-US10-40000999-SH-20260925")]
    counts = svc.sync_engine_run()
    assert counts["opened"] == 1 and counts["cleared"] == 1
    assert svc.sync_engine_run()["cleared"] == 0  # cleared once per run
    # Engine 1.1.0: a case whose snapshot has no demand model takes it once, then stays unchanged.
    model = json.dumps({"model": "sba", "probability": 0.5, "sizes": [4.0], "frequency": "1-3 weeks"})
    planning.rows = [case_row(material="40000999", case_id="C-US10-40000999-SH-20260925", demand_model=model)]
    assert svc.sync_engine_run()["updated"] == 1 and svc.sync_engine_run()["unchanged"] == 1
    # A case closed in a simulation while the engine still sees the condition: a new case, suffixed id.
    reopened = "C-US10-40000999-SH-20260925"
    svc.append(Actor("human", "purchasing"), reopened, "case_cancelled", {"reason": "simulated"})
    assert svc.sync_engine_run()["opened"] == 1 and svc.view(f"{reopened}-2").state == "new"


def test_full_agent_flow_to_closed_case(service):
    service.draft_supplier_request(PLANNER, CASE, "expedite", REQUEST)
    with pytest.raises(CaseError) as error:
        service.record_message(INTAKE, CASE, "Hello", "outbound", "intake_agent")
    assert error.value.code == "INVALID_STATE"  # nothing goes out before purchasing sends
    service.append(PURCHASING, CASE, "request_sent", {"channel": "portal", "handled_by": "intake_agent"})
    preview = service.explain_case(PLANNER, CASE)["supplier_request"]
    assert preview["quantity"] == 20.0 and preview["po_number"] == "4500001234"
    request = service.intake_request(INTAKE)
    assert request["outward"]["po_number"] == "4500001234" and "value_usd" not in json.dumps(request)
    service.record_message(
        PORTAL, CASE, "We can ship 20 pcs to arrive 2026-09-30 with 15% surcharge.", "inbound", "supplier"
    )
    service.record_message(
        INTAKE, CASE, "Thank you. Could 20 pieces arrive by 2026-09-26 instead?", "outbound", "intake_agent"
    )
    proposal = service.record_proposal(
        INTAKE,
        CASE,
        [
            {
                "option_id": 1,
                "kind": "expedite",
                "quantity": 12,
                "arrival_date": "2026-09-30",
                "surcharge_pct": 15,
            },
            {
                "option_id": 2,
                "kind": "partial",
                "split_deliveries": [
                    {"quantity": 10, "arrival_date": "2026-09-24"},
                    {"quantity": 10, "arrival_date": "2026-09-30"},
                ],
            },
        ],
    )
    first, second = proposal.payload["proposal_ids"]
    evaluations = {o: service.evaluate_option(PLANNER, CASE, o) for o in (first, second, "expedite")}
    # Expedited to 2026-09-30 the order still leaves 2026-09-26 to 09-29 short; the split covers every day.
    assert evaluations[first]["days_below_zero"] == 4 and evaluations[second]["days_below_zero"] == 0
    assert evaluations["expedite"]["days_below_zero"] == 0
    with pytest.raises(CaseError) as error:
        service.propose_recommendation(
            PLANNER, CASE, [{"option": first, "evaluation_id": "E-wrong", "rank": 1}], first, "x" * 30
        )
    assert error.value.code == "UNTRACED_OPTION"
    ranked = [
        {"option": o, "evaluation_id": evaluations[o]["evaluation_id"], "rank": i + 1}
        for i, o in enumerate((second, first))
    ]
    service.propose_recommendation(
        PLANNER, CASE, ranked, second, "The split delivery covers the gap at the lowest cost."
    )
    service.request_decision(PLANNER, CASE)
    with pytest.raises(CaseError) as error:
        service.append(PLANNER, CASE, "decision_recorded", {"choice": second})
    assert error.value.code == "ACTOR_NOT_ALLOWED"  # agents never decide
    with pytest.raises(CaseError) as error:
        service.append(Actor("human", "site_manager_rr"), CASE, "decision_recorded", {"choice": second})
    assert error.value.code == "ROLE_NOT_ALLOWED"  # supplier decisions belong to purchasing
    service.append(PURCHASING, CASE, "decision_recorded", {"choice": second, "rationale_text": "Agreed."})
    handoff = service.draft_handoff(PLANNER, CASE)
    assert handoff.facts["quantity"] == 20.0 and handoff.facts["arrival_date"] == "2026-09-30"
    # The split reschedules the open order: logistics gets the order number and both deliveries.
    assert handoff.facts["po_number"] == "4500001234" and len(handoff.facts["deliveries"]) == 2
    service.append(PURCHASING, CASE, "handed_off", {"handoff_seq": handoff.seq})
    service.append(
        Actor("human", "site_manager_rr"),
        CASE,
        "receipt_confirmed",
        {"received_qty": 20, "received_date": "2026-09-30"},
    )
    assert service.view(CASE).state == "closed"
    assert [e.seq for e in service.store.events(CASE)] == list(range(1, len(service.store.events(CASE)) + 1))


def test_outward_check_blocks_internal_figures_and_other_suppliers(service):
    with pytest.raises(CaseError) as error:
        service.draft_supplier_request(PLANNER, CASE, "expedite", REQUEST + " We only have 4 left in stock.")
    assert error.value.code == "OUTWARD_CHECK"
    with pytest.raises(CaseError) as error:
        service.draft_supplier_request(
            PLANNER, CASE, "expedite", REQUEST + " Oriel Motion Robotics can do better."
        )
    assert error.value.code == "OUTWARD_CHECK"
    assert numbers_in("PO 4500001234, 12.0 pcs") == {"4500001234", "12"}
    assert "25" in allowed_numbers(date(2026, 9, 25))


def test_intake_agent_scope_and_exchange_cap(service, tmp_path):
    service.draft_supplier_request(PLANNER, CASE, "expedite", REQUEST)
    service.append(PURCHASING, CASE, "request_sent", {"channel": "portal", "handled_by": "intake_agent"})
    other = Actor("intake_agent", "intake", case_binding="C-CN10-1-SH-20260925")
    with pytest.raises(CaseError) as error:
        service.escalate(other, CASE, "scope", "The supplier asked for our stock levels.")
    assert error.value.code == "OUT_OF_SCOPE"
    for _ in range(6):
        service.record_message(
            INTAKE, CASE, "Could 20 pieces arrive by 2026-09-26?", "outbound", "intake_agent"
        )
    with pytest.raises(CaseError) as error:
        service.record_message(
            INTAKE, CASE, "Could 20 pieces arrive by 2026-09-26?", "outbound", "intake_agent"
        )
    assert error.value.code == "EXCHANGE_CAP"
    service.escalate(INTAKE, CASE, "exchange_cap", "Six messages without an answer; back to purchasing.")
    assert service.view(CASE).path == "human"
    with pytest.raises(CaseError) as error:
        service.record_proposal(INTAKE, CASE, [{"option_id": 1, "kind": "decline"}])
    assert error.value.code == "NOT_AGENT_CASE"


def test_masking_and_site_scope(service):
    purchasing = service.get_case(PURCHASING, CASE)
    analyst = service.get_case(Actor("human", "ops_analyst_global"), CASE)
    assert purchasing["engine"]["value_usd"] == 5000.0 and "messages" in purchasing
    assert "value_usd" not in analyst["engine"] and "messages" not in analyst
    with pytest.raises(CaseError) as error:
        service.get_case(Actor("human", "site_manager_cn"), CASE)
    assert error.value.code == "OUT_OF_SCOPE"
    assert service.list_cases(Actor("human", "site_manager_cn"))["total"] == 0
    assert (
        service.evaluate_option(Actor("human", "ops_analyst_global"), CASE, "new_order")["total_cost"] is None
    )


def test_transfer_cases_are_decided_by_logistics(tmp_path):
    row = case_row(
        lever="transfer",
        lever_qty=10.0,
        lever_detail=json.dumps({"storage_location": "2001", "lead_days": 0}),
        coverage_date=date(2026, 9, 21),
        triage_class="human_handled",
    )
    svc = CaseService(LocalCaseStore(tmp_path / "t"), FakePlanning([row]), clock=Clock())
    svc.sync_engine_run()
    logistics = Actor("human", "site_manager_rr")
    svc.prepare_transfer(Actor("planner_agent", "site_manager_rr"), CASE)
    with pytest.raises(CaseError) as error:
        svc.append(PURCHASING, CASE, "decision_recorded", {"choice": "transfer"})
    assert error.value.code == "ROLE_NOT_ALLOWED"
    svc.append(logistics, CASE, "decision_recorded", {"choice": "transfer"})
    assert svc.draft_handoff(Actor("planner_agent", "site_manager_rr"), CASE).facts["from_location"] == "2001"


def test_evaluation_arithmetic():
    position = FakePlanning([]).position("m", "p")
    # Without the option the projection is negative from day 5 (2026-09-26) to day 13 (2026-10-04): 9 days.
    base = evaluate("C", 1, "shortage", "B", position, OptionSpec("none", "decline", ()), COSTS, 100.0)
    assert (
        base.days_below_zero == 9
        and base.stockout_cost == 9000.0
        and base.first_short_day == date(2026, 9, 26)
    )
    # Expedite the open order of 20 from 2026-10-05 to 2026-09-24 with a 10 % surcharge and 150 freight.
    spec = OptionSpec(
        "x",
        "expedite",
        (Receipt(20, date(2026, 9, 24)),),
        (Receipt(20, date(2026, 10, 5)),),
        None,
        10.0,
        None,
        150.0,
    )
    result = evaluate("C", 1, "shortage", "B", position, spec, COSTS, 100.0)
    assert result.days_below_zero == 0 and result.covers_gap
    assert result.surcharge_cost == 200.0 and result.total_cost == 350.0
    assert (
        result.evaluation_id == evaluate("C", 1, "shortage", "B", position, spec, COSTS, 100.0).evaluation_id
    )
    # An expedited open order keeps its purchase-order price: a quoted unit price adds no price difference
    # (it only sets the base of the surcharge percent).
    restated = evaluate("C", 1, "shortage", "B", position, replace(spec, unit_price=110.0), COSTS, 100.0)
    assert restated.price_difference_cost == 0.0 and restated.surcharge_cost == 220.0
    # New supply at a quoted 110 against the reference price of 100: 20 pieces cost 200 more.
    quoted = OptionSpec(
        "q", "new_order", (Receipt(20, date(2026, 9, 24)),), (), 110.0, None, None, 80.0, 75.0
    )
    priced = evaluate("C", 1, "shortage", "B", position, quoted, COSTS, 100.0)
    assert priced.price_difference_cost == 200.0
    assert priced.total_cost == round(200.0 + 80.0 + 75.0 + priced.holding_cost, 2)


def test_doing_nothing_is_priced_with_its_line_down_cost(service):
    # The fixture's projection is below zero for 9 days; criticality B costs 1,000 USD per day.
    result = service.evaluate_option(PURCHASING, CASE, "no_action")
    assert result["days_below_zero"] == 9 and result["stockout_cost"] == 9000.0
    assert result["fixed_cost"] == 0.0 and result["freight_cost"] == 0.0


def test_local_store_refuses_out_of_order_events(tmp_path):
    store = LocalCaseStore(tmp_path / "x")
    event = Event(
        "C", 2, "note_added", "new", "US10", "1", "human", "purchasing", {}, {}, None, datetime.now(UTC)
    )
    with pytest.raises(StoreConflict):
        store.append(event)


class FakeCursor:
    def __init__(self, log, last):
        self.log, self.last, self.description = log, last, [("last",)]

    def execute(self, sql, parameters=None):
        self.log.append((sql, parameters))

    def fetchall(self):
        return [(self.last,)]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FakeConnection(FakeCursor):
    def cursor(self):
        return FakeCursor(self.log, self.last)

    def close(self):
        self.log.append(("close", None))


def test_databricks_store_checks_the_sequence_and_inserts_with_parameters():
    log = []
    store = DatabricksCaseStore(lambda: FakeConnection(log, 3), "snm")
    event = Event(
        "C",
        4,
        "note_added",
        "new",
        "US10",
        "1",
        "human",
        "purchasing",
        {"a": 1},
        {"a": 1},
        None,
        datetime(2026, 9, 21, tzinfo=UTC),
    )
    store.append(event)
    inserts = [entry for entry in log if entry[0].startswith("INSERT")]
    assert len(inserts) == 1 and "`snm`.`cases`.`case_event`" in inserts[0][0]
    assert inserts[0][1]["facts"] == '{"a": 1}' and ":case_id" in inserts[0][0]
    with pytest.raises(StoreConflict):
        store.append(Event(**{**event.__dict__, "seq": 9}))


def test_mcp_profiles_have_disjoint_tools_and_no_human_actions(service):
    import asyncio

    from snm_cases.server import build_intake, build_planner

    planner = {tool.name for tool in asyncio.run(build_planner(service, PLANNER).list_tools())}
    intake = {tool.name for tool in asyncio.run(build_intake(service, INTAKE).list_tools())}
    assert intake == {"get_request", "record_message", "record_proposal", "escalate"}
    assert {
        "get_case",
        "explain_case",
        "evaluate_option",
        "rank_options",
        "draft_supplier_request",
        "propose_recommendation",
    } <= planner
    assert not planner & intake
    human_only = {"request_sent", "decision_recorded", "send_request", "record_decision", "case_cancelled"}
    assert not (planner | intake) & human_only
    # A refused write surfaces as a structured tool error with a stable code.
    from mcp.server.mcpserver.exceptions import ToolError

    with pytest.raises(ToolError) as error:
        asyncio.run(build_planner(service, PLANNER).call_tool("draft_handoff", {"case_id": CASE}))
    assert "INVALID_STATE" in str(error.value) or "NO_DECISION" in str(error.value)


def test_a_partial_answer_on_an_expedite_case_reschedules_the_open_order(service):
    service.draft_supplier_request(PLANNER, CASE, "expedite", REQUEST)
    service.append(PURCHASING, CASE, "request_sent", {"channel": "portal", "handled_by": "intake_agent"})
    event = service.record_proposal(
        PURCHASING,
        CASE,
        [
            {
                "option_id": 1,
                "kind": "partial",
                "split_deliveries": [
                    {"quantity": 10, "arrival_date": "2026-09-24"},
                    {"quantity": 10, "arrival_date": "2026-10-05"},
                ],
            }
        ],
    )
    result = service.evaluate_option(PURCHASING, CASE, event.payload["proposal_ids"][0])
    # The order of 20 due 2026-10-05 now brings 10 on 09-24 and 10 on its old date: 4 - d + 10 stays above
    # zero until day 14, so nothing is short; no new order is placed, so there is no order cost.
    assert result["days_below_zero"] == 0 and result["fixed_cost"] == 0.0
    assert result["quantity"] == 20.0


def test_an_expedite_offered_without_an_open_order_needs_a_new_order(tmp_path):
    svc = _one_case(
        tmp_path,
        lever="new_order",
        lever_qty=9.0,
        lever_detail=json.dumps({"lead_days": 4}),
        coverage_date=date(2026, 9, 25),
        lever_cost_usd=75.0,
        alternatives=json.dumps([]),
        alternative_costs_usd=json.dumps({"new_order": 75.0}),
    )
    svc.draft_supplier_request(PURCHASING, CASE, "quote", "Please quote 9 pieces of material 40000231.")
    svc.append(PURCHASING, CASE, "request_sent", {"channel": "portal", "handled_by": "human"})
    event = svc.record_proposal(
        PURCHASING, CASE, [{"option_id": 1, "kind": "expedite", "quantity": 9, "arrival_date": "2026-09-23"}]
    )
    result = svc.evaluate_option(PURCHASING, CASE, event.payload["proposal_ids"][0])
    assert result["fixed_cost"] == 75.0


# ------------------------------------------------------------------------------------ records 004 and 005


def _sent(service):
    service.draft_supplier_request(PLANNER, CASE, "expedite", REQUEST)
    service.append(PURCHASING, CASE, "request_sent", {"channel": "portal", "handled_by": "human"})


def test_mandate_defaults_are_the_suppliers_terms_when_speed_is_needed(service):
    defaults = service.mandate_defaults(PURCHASING, CASE)
    mandate = defaults["mandate"]
    # Arrive by the trigger date, between the gap and the open order; the lever is an expedite, so the
    # ceilings are the terms the engine priced it with: Sakuragawa's 2.7 % for 11 days gained and 150 freight.
    assert mandate["latest_arrival_date"] == "2026-09-26"
    assert (mandate["quantity_min"], mandate["quantity_max"]) == (9.0, 20.0)
    assert mandate["surcharge_pct_max"] == 2.7 and mandate["freight_max_usd"] == 150.0
    assert mandate["allowed_kinds"] == ["expedite", "partial", "alternative_part"]
    assert mandate["min_validity_days"] == 3 and mandate["max_exchanges"] == 6
    assert defaults["source"] == "engine_default" and "2.7 %" in defaults["basis"]
    with pytest.raises(CaseError, match="outside role site_manager_cn"):
        service.mandate_defaults(Actor("human", "site_manager_cn"), CASE)


def _one_case(tmp_path, planning_class=None, **overrides):
    svc = CaseService(
        LocalCaseStore(tmp_path / "one"),
        (planning_class or FakePlanning)([case_row(**overrides)]),
        clock=Clock(),
    )
    svc.sync_engine_run()
    return svc


def test_mandate_allows_no_surcharge_when_no_speed_is_needed(tmp_path):
    # A new order at the planned delivery time arrives 2026-09-25, before the trigger date: the expedited
    # new order is an alternative nobody needs, so the default accepts no surcharge and no expedite freight.
    in_time = dict(
        lever="new_order",
        lever_qty=9.0,
        lever_detail=json.dumps({"lead_days": 4}),
        coverage_date=date(2026, 9, 25),
        lever_cost_usd=75.0,
        alternatives=json.dumps(
            [
                {
                    "lever": "expedited_new_order",
                    "coverage_date": "2026-09-23",
                    "qty": 9.0,
                    "lead_days": 2,
                    "days_gained": 2,
                }
            ]
        ),
        alternative_costs_usd=json.dumps({"new_order": 75.0, "expedited_new_order": 425.0}),
    )
    defaults = _one_case(tmp_path / "a", **in_time).mandate_defaults(PURCHASING, CASE)
    mandate = defaults["mandate"]
    assert (mandate["surcharge_pct_max"], mandate["freight_max_usd"]) == (0.0, 0.0)
    assert mandate["allowed_kinds"] == ["new_order", "partial", "alternative_part"]
    assert "no surcharge and no expedite freight are needed" in defaults["basis"]
    # A warning: stock stays above zero, so no expedite is recommended (record 001) even when late.
    warning = {**in_time, "condition": "warning", "covers_in_time": False, "coverage_date": date(2026, 10, 1)}
    defaults = _one_case(tmp_path / "b", **warning).mandate_defaults(PURCHASING, CASE)
    assert (defaults["mandate"]["surcharge_pct_max"], defaults["mandate"]["freight_max_usd"]) == (0.0, 0.0)
    assert "record 001" in defaults["basis"]


class OwnFreight(FakePlanning):
    """Sakuragawa ships expedites for 90 USD, below the company default of 150."""

    def suppliers(self):
        return super().suppliers().with_columns(pl.Series("expedite_freight_usd", [90.0, None]))


def test_mandate_and_engine_option_use_the_suppliers_own_freight(tmp_path):
    # Only the expedited new order arrives in time; 16 days gained is Sakuragawa's 14-or-more tier (6 %).
    # The engine's cost: 75 order cost + 6 % of 9 x 555.56 (300.00) + 90 freight = 465.00.
    svc = _one_case(
        tmp_path,
        OwnFreight,
        lever="expedited_new_order",
        lever_qty=9.0,
        lever_detail=json.dumps({"lead_days": 3, "days_gained": 16}),
        lever_cost_usd=465.0,
        alternatives=json.dumps(
            [{"lever": "new_order", "coverage_date": "2026-10-10", "qty": 9.0, "lead_days": 19}]
        ),
        alternative_costs_usd=json.dumps({"expedited_new_order": 465.0, "new_order": 75.0}),
    )
    defaults = svc.mandate_defaults(PURCHASING, CASE)
    assert (defaults["mandate"]["surcharge_pct_max"], defaults["mandate"]["freight_max_usd"]) == (6.0, 90.0)
    assert "6.0 %" in defaults["basis"] and "90.00 USD expedite freight" in defaults["basis"]
    option = svc.evaluate_option(PURCHASING, CASE, "expedited_new_order")
    assert (option["surcharge_cost"], option["freight_cost"], option["fixed_cost"]) == (300.0, 90.0, 75.0)
    assert option["price_difference_cost"] == 0.0


def test_proposals_are_ranked_by_the_purchasing_rule(service):
    service.draft_supplier_request(PLANNER, CASE, "expedite", REQUEST)
    defaults = service.mandate_defaults(PURCHASING, CASE)
    service.set_mandate(PURCHASING, CASE, defaults["mandate"], "engine_default", defaults["basis"])
    service.append(PURCHASING, CASE, "request_sent", {"channel": "portal", "handled_by": "human"})
    on_time = {
        "quantity": 20,
        "arrival_date": "2026-09-25",
        "freight_amount": 100,
        "valid_until": "2026-09-28",
    }
    event = service.record_proposal(
        PURCHASING,
        CASE,
        [
            {"option_id": 1, "kind": "expedite", "surcharge_pct": 2, **on_time},  # inside the mandate
            {"option_id": 2, "kind": "expedite", "surcharge_pct": 5, **on_time},  # misses the surcharge
            # The cheapest: no surcharge, no freight, but a kind outside the mandate and a short validity.
            {
                "option_id": 3,
                "kind": "new_order",
                **{**on_time, "freight_amount": 0, "valid_until": "2026-09-22"},
            },
        ],
    )
    p1, p2, p3 = event.payload["proposal_ids"]
    ranking = service.rank_options(PURCHASING, CASE)
    assert ranking["rule"] == "mandate_first"
    assert [(row["option"], row["missed"]) for row in ranking["ranked"]] == [
        (p1, []),
        (p2, ["surcharge"]),
        (p3, ["kind", "validity"]),
    ]
    assert ranking["ranked"][2]["total_cost"] < ranking["ranked"][0]["total_cost"]  # cost comes after misses
    assert [row["option"] for row in ranking["reference"]] == ["expedite", "new_order", "no_action"]

    # A recommendation must submit the proposals in that order (planner v4); the order is stored with it.
    def ranked(order):
        return [
            {
                "option": o,
                "evaluation_id": service.evaluate_option(PLANNER, CASE, o)["evaluation_id"],
                "rank": r,
            }
            for r, o in enumerate(order, start=1)
        ]

    with pytest.raises(CaseError, match="purchasing's order"):
        service.propose_recommendation(
            PLANNER, CASE, ranked([p3, p1, p2]), p1, "The offer inside the mandate."
        )
    with pytest.raises(CaseError, match="purchasing's order"):  # every ranked proposal must be there
        service.propose_recommendation(PLANNER, CASE, ranked([p1, p2]), p1, "The offer inside the mandate.")
    recorded = service.propose_recommendation(
        PLANNER, CASE, ranked([p1, p2, p3, "no_action"]), p1, "The offer inside the mandate wins."
    )
    assert recorded.payload["ranking_rule"] == "mandate_first"
    assert recorded.payload["server_ranking"] == [p1, p2, p3]


# Demand of 14 a week, every week, is the fixture's firm demand of 2 a day: every simulated path is the
# projection, so the risk figures are exact.
EXACT_MODEL = json.dumps(
    {"model": "sba", "weeks": 26, "alpha": 0.15, "rate_per_week": 14.0, "probability": 1.0, "sizes": [14.0],
     "frequency": "10-13 weeks", "service_target": 0.90, "calibration": [[0.1, 0.14]]}
)  # fmt: skip


@pytest.mark.parametrize("criticality", ["C", "B"])
def test_a_c_parts_late_offer_competes_on_expected_cost(tmp_path, criticality):
    """Record 007. P-1 arrives on 2026-09-25 inside the mandate with a 2.5 % surcharge (277.78 USD on 20 at
    5000/9); P-2 has none but arrives on 2026-09-27, a day after the latest arrival: the stock is below zero
    on 2026-09-26 only, one expected day of line-down. For a C part (200 USD a day) P-2's expected total, 200,
    beats P-1's 277.78 and it ranks first; for a B part the arrival check stays and P-1 ranks first."""
    row = case_row(criticality=criticality, demand_model=EXACT_MODEL)
    svc = CaseService(LocalCaseStore(tmp_path / "store"), FakePlanning([row]), clock=Clock())
    svc.sync_engine_run("plan-1")
    svc.draft_supplier_request(PLANNER, CASE, "expedite", REQUEST)
    defaults = svc.mandate_defaults(PURCHASING, CASE)
    svc.set_mandate(PURCHASING, CASE, defaults["mandate"], "engine_default", defaults["basis"])
    svc.append(PURCHASING, CASE, "request_sent", {"channel": "portal", "handled_by": "human"})
    common = {"kind": "expedite", "quantity": 20, "freight_amount": 0, "valid_until": "2026-09-28"}
    p1, p2 = svc.record_proposal(
        PURCHASING,
        CASE,
        [
            {"option_id": 1, **common, "arrival_date": "2026-09-25", "surcharge_pct": 2.5},
            {"option_id": 2, **common, "arrival_date": "2026-09-27", "surcharge_pct": 0},
        ],
    ).payload["proposal_ids"]
    ranking = svc.rank_options(PURCHASING, CASE)
    assert ranking["rule"] == "expected_cost"
    rows = {row["option"]: row for row in ranking["ranked"]}
    assert rows[p1]["expected_total_cost"] == pytest.approx(277.78, abs=0.01)
    late_cost = 200.0 if criticality == "C" else 1000.0
    assert rows[p2]["expected_total_cost"] == pytest.approx(late_cost)
    assert rows[p2]["missed"] == ["arrival"] and rows[p2]["stockout_risk"] == "100.00%"
    order = [row["option"] for row in ranking["ranked"]]
    if criticality == "C":
        assert order == [p2, p1] and rows[p2]["late_priced_by_risk"]
        assert svc.recommended_proposal(CASE) == p2
    else:
        assert order == [p1, p2] and not rows[p2]["late_priced_by_risk"]
        assert svc.recommended_proposal(CASE) == p1


def test_mandate_is_set_by_purchasing_and_masked_for_logistics(service):
    defaults = service.mandate_defaults(PURCHASING, CASE)
    with pytest.raises(CaseError, match="not allowed in state new"):  # not before the request is prepared
        service.set_mandate(PURCHASING, CASE, defaults["mandate"], "engine_default", defaults["basis"])
    service.draft_supplier_request(PLANNER, CASE, "expedite", REQUEST)
    with pytest.raises(CaseError, match="cannot record mandate_set"):
        service.set_mandate(Actor("human", "site_manager_rr"), CASE, defaults["mandate"], "purchasing")
    edited = {**defaults["mandate"], "surcharge_pct_max": 5.0}
    event = service.set_mandate(PURCHASING, CASE, edited, "purchasing", "purchasing raised the ceiling")
    assert event.event_type == "mandate_set" and event.state_after == "prepared"
    assert "surcharge_pct_max" not in event.facts["mandate"]  # commercial: masked in facts
    assert service.get_case(PURCHASING, CASE)["mandate"]["surcharge_pct_max"] == 5.0
    masked = service.get_case(Actor("human", "site_manager_rr"), CASE)["mandate"]
    assert masked["latest_arrival_date"] == "2026-09-26" and "surcharge_pct_max" not in masked


def test_proposals_are_compared_with_the_mandate(service):
    service.draft_supplier_request(PLANNER, CASE, "expedite", REQUEST)
    defaults = service.mandate_defaults(PURCHASING, CASE)
    service.set_mandate(PURCHASING, CASE, defaults["mandate"], "engine_default")
    service.append(PURCHASING, CASE, "request_sent", {"channel": "portal", "handled_by": "human"})
    inside = {
        "option_id": 1,
        "kind": "expedite",
        "quantity": 10,
        "arrival_date": "2026-09-25",
        "surcharge_pct": 2.0,
        "freight_amount": 100.0,
        "valid_until": "2026-09-30",
    }
    outside = {
        "option_id": 2,
        "kind": "expedite",
        "quantity": 10,
        "arrival_date": "2026-09-28",
        "surcharge_pct": 5.0,
        "freight_amount": 100.0,
        "valid_until": "2026-09-22",
    }
    event = service.record_proposal(PURCHASING, CASE, [inside, outside])
    flags = {o["option_id"]: o["within_mandate"] for o in event.payload["options"]}
    assert flags[1]["all"] and all(flags[1].values())
    assert not flags[2]["all"]
    assert not flags[2]["arrival"] and not flags[2]["surcharge"] and not flags[2]["validity"]
    assert flags[2]["quantity"] and flags[2]["freight"] and flags[2]["kind"]
    stored = {p["option_id"]: p["within_mandate"] for p in service.get_case(PURCHASING, CASE)["proposals"]}
    assert stored == flags


def test_proposals_without_a_mandate_carry_no_flags(service):
    _sent(service)
    event = service.record_proposal(
        PURCHASING, CASE, [{"option_id": 1, "kind": "expedite", "quantity": 10, "arrival_date": "2026-09-25"}]
    )
    assert event.payload["options"][0]["within_mandate"] is None
    assert service.store.proposals(CASE)[0].within_mandate is None


def test_attachments_are_stored_hashed_and_listed(service, tmp_path):
    service.draft_supplier_request(PLANNER, CASE, "expedite", REQUEST)
    sheet = b"PK\x03\x04 not really a workbook"
    outbound = service.record_attachment(
        PURCHASING,
        CASE,
        file_name="request.xlsx",
        content=sheet,
        direction="outbound",
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    assert outbound.event_type == "attachment_recorded" and outbound.state_after == "prepared"
    service.append(PURCHASING, CASE, "request_sent", {"channel": "portal", "handled_by": "human"})
    inbound = service.record_attachment(
        PORTAL,
        CASE,
        file_name="../reply.xlsx",
        content=b"reply",
        direction="inbound",
        content_type="application/octet-stream",
        parsed_options=2,
    )
    rows = service.store.attachments(CASE)
    assert [r.direction for r in rows] == ["outbound", "inbound"]
    assert rows[0].uploaded_by == "human" and rows[1].uploaded_by == "supplier"
    assert rows[1].file_name == "../reply.xlsx" and rows[1].storage_path.endswith("-reply.xlsx")
    attachment, content = service.read_attachment(PURCHASING, CASE, inbound.payload["attachment_id"])
    assert content == b"reply" and attachment.sha256 == hashlib.sha256(b"reply").hexdigest()
    listed = service.get_case(PURCHASING, CASE)["attachments"]
    assert [a["attachment_id"] for a in listed] == [r.attachment_id for r in rows]
    assert listed[1]["parsed_options"] == 2 and listed[0]["size_bytes"] == len(sheet)
    with pytest.raises(CaseError, match="No attachment"):
        service.read_attachment(PURCHASING, CASE, "A-99-" + CASE)


def test_decided_order_case_is_handed_off_by_the_system_and_closed_by_the_data(service):
    _sent(service)
    service.record_proposal(
        PURCHASING, CASE, [{"option_id": 1, "kind": "expedite", "quantity": 20, "arrival_date": "2026-09-25"}]
    )
    service.append(PURCHASING, CASE, "decision_recorded", {"choice": "P-1-4-" + CASE, "rationale_text": "ok"})
    handed = service.hand_off("web-1", CASE)
    assert handed.actor_kind == "system" and handed.state_after == "handed_off"
    assert handed.payload == {"handoff_seq": 1}
    closed = service.confirm_receipt("sync-1", CASE, 20.0, date(2026, 9, 25))
    assert closed.state_after == "closed" and closed.actor_role == "system"


def test_prepared_transfer_case_is_handed_off_without_a_decision(tmp_path):
    transfer = case_row(
        case_id="C-US10-40000231-PU-20260925",
        condition="pull",
        lever="transfer",
        lever_qty=9.0,
        lever_detail=json.dumps({"from_location": "2001", "to_location": "1000", "lead_days": 0}),
        alternatives="[]",
        alternative_costs_usd=json.dumps({"transfer": 44.5}),
        triage_class="human_handled",
        triage_reasons="pull from the overflow hub decided by logistics",
    )
    svc = CaseService(LocalCaseStore(tmp_path / "s"), FakePlanning([case_row(), transfer]), clock=Clock())
    svc.sync_engine_run("plan-1")
    with pytest.raises(CaseError, match="no supplier mandate"):
        svc.mandate_defaults(PURCHASING, transfer["case_id"])
    events = svc.publish_transfer("web-1", transfer["case_id"])
    assert [(e.event_type, e.actor_kind) for e in events] == [
        ("transfer_prepared", "system"),
        ("handed_off", "system"),
    ]
    assert events[0].payload["from_location"] == "2001" and events[0].payload["to_location"] == "1000"
    # An order case that is only prepared is not handed off: it needs a decision first.
    svc.draft_supplier_request(PLANNER, CASE, "expedite", REQUEST)
    with pytest.raises(CaseError, match=r"needs a decision|Only a prepared transfer"):
        svc.hand_off("web-1", CASE)


# ------------------------------------------------------------------------- supplier portal and Excel (005)

VENDOR = "0000100007"


def fill_sheet(sheet: bytes, rows: list[dict]) -> bytes:
    import io

    from openpyxl import load_workbook

    from snm_cases import excel

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


def _send_with_sheet(service) -> bytes:
    from snm_cases import excel

    text = service.default_request_text(CASE, "expedite")
    service.draft_supplier_request(PURCHASING, CASE, "expedite", text)  # passes the outgoing-text check
    defaults = service.mandate_defaults(PURCHASING, CASE)
    sheet = excel.request_sheet(service.outward_fields(service.view(CASE), "expedite"), text)
    events = service.send_request(
        PURCHASING, CASE, defaults["mandate"], "engine_default", defaults["basis"], ("request.xlsx", sheet)
    )
    assert [e.event_type for e in events] == [
        "mandate_set",
        "attachment_recorded",
        "request_sent",
        "message_recorded",
    ]
    return sheet


def test_purchasing_sends_mandate_sheet_and_request_text(service):
    _send_with_sheet(service)
    view = service.view(CASE)
    assert view.state == "sent"
    [message] = service.store.messages(CASE)
    assert message.direction == "outbound" and message.author_kind == "human"
    assert "4500001234" in message.body and "2026-09-26" in message.body
    assert service.store.attachments(CASE)[0].direction == "outbound"
    with pytest.raises(CaseError, match="Only a prepared case"):
        service.send_request(
            PURCHASING, CASE, service.mandate_defaults(PURCHASING, CASE)["mandate"], "purchasing"
        )


def test_supplier_sees_outward_fields_only(service):
    _send_with_sheet(service)
    assert [r["case_id"] for r in service.supplier_requests(VENDOR)] == [CASE]
    assert service.supplier_requests("0000100012") == []
    with pytest.raises(CaseError, match="not addressed to your company"):
        service.supplier_view("0000100012", CASE)
    view = service.supplier_view(VENDOR, CASE)
    assert set(view) == {"case_id", "status", "outward", "request", "messages", "options", "attachments"}
    assert set(view["outward"]) == set(model_outward_keys())
    text = json.dumps(view)
    for internal in ("value_usd", "surcharge_pct_max", "within_mandate", "rationale", "triage", "Oriel",
                     "agent_eligible", "5000.0", "555.56", "basis"):  # fmt: skip
        assert internal not in text, internal


def model_outward_keys():
    from snm_cases.model import OutwardFields

    return OutwardFields.model_fields


def test_supplier_answers_with_the_sheet_and_purchasing_decides(service):
    from snm_cases import excel
    from snm_cases.service import XLSX

    sheet = _send_with_sheet(service)
    attachment_id = service.supplier_view(VENDOR, CASE)["attachments"][0]["attachment_id"]
    _, downloaded = service.supplier_file(VENDOR, CASE, attachment_id)
    assert downloaded == sheet
    filled = fill_sheet(downloaded, [
        {"kind": "expedite", "quantity": 20, "arrival_date": "2026-09-25", "surcharge_pct": 2,
         "freight_amount": 120, "valid_until": "2026-09-28"},
        {"kind": "partial", "split_deliveries": "5@2026-09-24; 15@2026-09-30", "surcharge_pct": 4,
         "valid_until": "2026-09-28", "conditions": "Second part by sea."},
    ])  # fmt: skip
    options, problems = excel.parse_reply(filled, CASE)
    assert problems == [] and [o["kind"] for o in options] == ["expedite", "partial"]
    events = service.supplier_reply(VENDOR, CASE, "", options, ("reply.xlsx", filled, XLSX))
    assert [e.event_type for e in events] == ["message_recorded", "attachment_recorded", "proposal_recorded"]
    assert service.store.messages(CASE)[-1].body == "(answer in the attached sheet)"
    proposals = service.store.proposals(CASE)
    assert [p.recorded_by for p in proposals] == ["supplier", "supplier"]
    assert [json.loads(p.within_mandate)["all"] for p in proposals] == [True, False]
    second = json.loads(proposals[1].within_mandate)
    assert not second["surcharge"] and not second["arrival"]  # only 5 of the needed 9 arrive by 2026-09-26
    assert service.store.attachments(CASE)[-1].parsed_options == 2
    assert "within_mandate" not in json.dumps(service.supplier_view(VENDOR, CASE))
    assert service.recommended_proposal(CASE) == proposals[0].proposal_id
    with pytest.raises(CaseError, match="not an option"):
        service.decide(PURCHASING, CASE, "P-9-9-" + CASE)
    # Anything but the recommended option needs a one-line reason (the owner, 2026-10-20).
    with pytest.raises(CaseError) as error:
        service.decide(PURCHASING, CASE, proposals[1].proposal_id, "  ")
    assert error.value.code == "RATIONALE_REQUIRED" and proposals[0].proposal_id in str(error.value)
    events = service.decide(PURCHASING, CASE, proposals[0].proposal_id, "inside the mandate", run_id="web-1")
    assert [e.event_type for e in events] == ["decision_recorded", "handed_off"]
    assert events[1].actor_kind == "system"
    with pytest.raises(CaseError, match="closed"):
        service.supplier_reply(VENDOR, CASE, "one more thing", [])


def test_supplier_reply_is_validated_before_anything_is_written(service):
    _send_with_sheet(service)
    before = len(service.store.events(CASE))
    with pytest.raises(CaseError, match="option 1"):
        service.supplier_reply(VENDOR, CASE, "offer", [{"option_id": 1, "kind": "expedite"}])
    with pytest.raises(CaseError, match="Write a message"):
        service.supplier_reply(VENDOR, CASE, "  ", [])
    assert len(service.store.events(CASE)) == before


def test_excel_parser_reports_problems_per_row(service):
    from snm_cases import excel

    sheet = excel.request_sheet(service.outward_fields(service.view(CASE), "expedite"), "Request text.")
    filled = fill_sheet(sheet, [
        {"kind": "expedit", "quantity": 20, "arrival_date": "2026-09-25"},
        {"kind": "partial", "split_deliveries": "5@2026-09-24; 15@2026-09-30"},
        {"kind": "expedite", "quantity": 20, "arrival_date": "25.09.2026"},
    ])  # fmt: skip
    options, problems = excel.parse_reply(filled, CASE)
    assert [o["option_id"] for o in options] == [2]
    assert options[0]["split_deliveries"] == [
        {"quantity": 5.0, "arrival_date": "2026-09-24"}, {"quantity": 15.0, "arrival_date": "2026-09-30"}
    ]  # fmt: skip
    assert problems == [
        "Row 14: kind 'expedit' is not one of expedite, new_order, partial, alternative_part, decline",
        "Row 16: arrival_date '25.09.2026' is not a date (YYYY-MM-DD)",
    ]
    assert excel.parse_reply(filled, "C-OTHER")[1][0].startswith("This sheet belongs to request")
    assert excel.parse_reply(b"not a workbook", CASE) == ([], ["The file is not an .xlsx workbook."])
    assert excel.parse_reply(sheet, CASE) == ([], ["The option table is empty: fill at least one row."])


def test_evaluation_accepts_days_as_text_from_the_lakehouse(service):
    """The lakehouse returns supply_position.day as a string; proposals must still be priced."""
    original = service.planning.position

    def as_text(material, plant):
        frame = original(material, plant)
        return frame.with_columns(pl.col("day").cast(pl.String))

    service.planning.position = as_text
    _sent(service)
    event = service.record_proposal(
        PURCHASING, CASE, [{"option_id": 1, "kind": "expedite", "quantity": 20, "arrival_date": "2026-09-25"}]
    )
    result = service.evaluate_option(PURCHASING, CASE, event.payload["proposal_ids"][0])
    assert result["coverage_date"] == "2026-09-25" and result["days_below_zero"] == 0
