"""Engine replay: inputs rebuilt for a past day contain nothing from that day on."""

from datetime import date, timedelta
from pathlib import Path

import polars as pl
import pytest

from snm_data.contracts import ContractRegistry
from snm_data.generator.config import PROFILES
from snm_data.generator.run import generate
from snm_data.planning.history import realized_shortages, replay, tables_as_of

CONTRACTS = Path(__file__).resolve().parents[2] / "data_contracts"
AS_OF = date(2026, 9, 14)


@pytest.fixture(scope="module")
def tables() -> dict[str, pl.DataFrame]:
    return generate(PROFILES["test"], ContractRegistry.load(CONTRACTS)).tables


def test_history_hides_the_future(tables) -> None:
    past = tables_as_of(tables, AS_OF)
    cutoff = AS_OF.strftime("%Y%m%d")
    assert (past["raw_sap_ecc.mseg"].get_column("budat_mkpf") < cutoff).all()
    assert (past["raw_sap_ecc.ekko"].get_column("aedat") < cutoff).all()
    assert (past["raw_sap_ecc.likp"].get_column("erdat") < cutoff).all()
    shipped_later = past["raw_sap_ecc.likp"].filter(pl.col("wadat_ist") >= cutoff)
    assert shipped_later.is_empty()  # goods issued on or after the day are still open
    eket = past["raw_sap_ecc.eket"].with_columns(
        pl.col("menge").cast(pl.Float64), pl.col("wemng").cast(pl.Float64)
    )
    assert (eket.get_column("wemng") <= eket.get_column("menge") + 1e-9).all()
    assert (past["raw_sap_ecc.resb"].get_column("bdart") == "SB").all()  # no planned orders for past days
    # Hub stock is the snapshot of the previous day.
    snapshot = tables["raw_ext.inventory_snapshot_daily"].filter(
        (pl.col("snapshot_date") == AS_OF - timedelta(days=1)) & (pl.col("stock_category") == "UNRESTRICTED")
    )
    mard = past["raw_sap_ecc.mard"].join(
        snapshot.select("werks", "lgort", "matnr", pl.col("quantity").cast(pl.Float64).alias("snap")),
        on=["werks", "lgort", "matnr"],
        how="inner",
    )
    assert (
        mard.height > 0
        and (mard.get_column("labst").cast(pl.Float64) - mard.get_column("snap")).abs().max() < 1e-6
    )


def test_replay_scores_against_emergency_orders(tables) -> None:
    realized = realized_shortages(tables, AS_OF - timedelta(days=7), 7)
    assert set(realized.columns) == {"material", "plant", "first_emergency"}
    [day] = replay(tables, [AS_OF - timedelta(days=7)], 7)
    assert day.realized == realized.height
    assert day.hits + day.late <= day.realized and day.hits <= day.predicted


def test_oracle_prices_options_on_realized_demand(tables) -> None:
    from snm_data.planning.oracle import POLICIES, evaluate_day, summarize

    # Stock opens between the main warehouse's minimum and maximum (generator 0.8), so the first cases of the
    # three-week test profile appear in its third week; the realized demand of that week prices them.
    outcomes = evaluate_day(tables, AS_OF, 7)
    assert outcomes, "the engine opens cases on the test profile"
    for outcome in outcomes:
        assert "do_nothing" in outcome.costs
        # The oracle is never worse than any policy's choice.
        assert all(
            outcome.costs[outcome.oracle] <= outcome.costs[choice] for choice in outcome.choices.values()
        )
    summary = summarize(outcomes)
    assert all(summary[policy]["regret"] >= 0 for policy in POLICIES)


def test_risk_aware_policies_by_hand() -> None:
    """Record 002 phase 4. ``risk_target``: the cheapest order lever within the service target, else the
    lowest-risk one; ``expected_cost``: the lowest lever cost plus expected line-down, doing nothing
    included; an unqualified case keeps the engine's lever."""
    from snm_data.planning.oracle import _risk_choices

    case = {"lever": "new_order"}
    options = {
        "do_nothing": ([], 0.0),
        "new_order": ([(30, 5.0)], 75.0),
        "expedited_new_order": ([(5, 5.0)], 900.0),
    }
    ex_ante = {
        "do_nothing": (0.60, False, 1200.0),
        "new_order": (0.20, False, 75.0 + 400.0),
        "expedited_new_order": (0.05, True, 900.0 + 100.0),
    }
    choices = _risk_choices(case, options, ex_ante)
    assert (choices["risk_target"], choices["expected_cost"]) == ("expedited_new_order", "new_order")
    within = {**ex_ante, "new_order": (0.08, True, 75.0 + 160.0)}
    assert _risk_choices(case, options, within)["risk_target"] == "new_order"  # cheaper, also within target
    none_within = {name: (risk, False, cost) for name, (risk, _, cost) in ex_ante.items()}
    assert _risk_choices(case, options, none_within)["risk_target"] == "expedited_new_order"  # lowest risk
    quiet = {**ex_ante, "do_nothing": (0.02, True, 40.0)}
    assert _risk_choices(case, options, quiet)["expected_cost"] == "do_nothing"
    assert set(_risk_choices(case, options, None).values()) == {"new_order"}
    # The guarded variant opens offers outside the levers (the late split) to C parts only.
    split = {**options, "late_split": ([(30, 2.0), (40, 3.0)], 10.0)}
    cheap = {**ex_ante, "late_split": (0.25, False, 10.0 + 300.0)}
    assert (
        _risk_choices({"lever": "new_order", "criticality": "C"}, split, cheap)["expected_cost_guarded"]
        == "late_split"
    )
    b_part = _risk_choices({"lever": "new_order", "criticality": "B"}, split, cheap)
    assert (b_part["expected_cost"], b_part["expected_cost_guarded"]) == ("late_split", "new_order")


def test_the_synthetic_late_split_by_hand() -> None:
    """Half of the engine's new order on its date (day 30), the rest a week after the later of that date and
    the trigger (day 37), 5 % of the case's value (500) below the new order's cost (75)."""
    from datetime import date

    from snm_data.planning.oracle import late_split

    case = {"trigger_date": date(2026, 3, 21), "as_of_date": date(2026, 3, 1), "value_usd": 500.0}
    offer = late_split(case, {"new_order": ([(30, 5.0)], 75.0)}, 0.05)
    assert offer == ([(30, 2.0), (37, 3.0)], 50.0)
    assert late_split(case, {"do_nothing": ([], 0.0)}, 0.05) is None
