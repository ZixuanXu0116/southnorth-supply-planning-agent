"""Engine parameters (PROPOSAL.md section 14 defaults). Values live here and in an optional YAML file, never
in the projection code; the digest of the effective parameters is stamped on every output row."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml

ENGINE_VERSION = "1.1.0"


@dataclass(frozen=True)
class PlanningParams:
    horizon_days: int = 91
    # Expected receipt of an open purchase order: agreed date plus the vendor's median lateness, capped.
    lateness_cap_days: int = 14
    lateness_window_days: int = 365
    # Minimum expedite lead time when the vendor has no condition sheet: a share of the planned delivery
    # time, never below the floor.
    min_expedite_fraction: float = 0.5
    min_expedite_floor_days: int = 3
    # Planning scope (decision record 003): the plants the engine plans, each with its main warehouse (the
    # issue point for manufacturing) and its overflow hubs (cheaper storage with no demand of their own,
    # pulled from when the main warehouse is short).
    plants: tuple[str, ...] = ("US10",)
    main_locations: dict[str, tuple[str, ...]] = field(default_factory=lambda: {"US10": ("1000",)})
    overflow_locations: dict[str, tuple[str, ...]] = field(default_factory=lambda: {"US10": ("2001",)})
    # Days a transfer between an overflow hub and its main warehouse takes, by overflow location.
    lane_lead_days: dict[str, int] = field(default_factory=lambda: {"2001": 0})
    # Demand is the manufacturing department's (component reservations, planned orders, and the forecast
    # rate of staging to the production supply area). Customer deliveries and sales orders in the data are
    # legacy flows outside the planning scope unless this is set.
    include_customer_demand: bool = False
    # The rate that fills the weeks the explicit requirements do not cover: "trailing" (the trailing
    # ``forecast_window_days`` rate) or "sba" (the demand model's average). The point forecast decides when
    # a case opens; the trailing rate catches 2 of 6 realized emergencies in the replay, the SBA average none
    # (it smooths away the recent bursts that precede them), so it stays (engine 1.1.0, record 002 phase 3).
    # The demand model prices the stock-out risk either way.
    forecast_model: str = "trailing"
    forecast_window_days: int = 28
    sba_weeks: int = 26
    sba_alpha: float = 0.15
    # Stock-out risk (record 002 phase 3): simulated demand paths per case; the service target per
    # criticality (the highest acceptable stock-out probability is one minus the target: A 1 %, B 3 %,
    # C 10 %, record 002 as amended on 2026-10-19); and the calibration curve per demand-frequency segment, as
    # pairs of (the model's stock-out probability, the share of backtest windows that ran out at that level),
    # fitted on the first half of the backtest's days and rounded up; on the second half no segment ran out
    # more often (evals/reports/forecast-accuracy-small.md). The segment without demand in 13 weeks did not
    # qualify and has no curve.
    risk_paths: int = 2000
    service_target: dict[str, float] = field(default_factory=lambda: {"A": 0.99, "B": 0.97, "C": 0.90})
    risk_calibration: dict[str, tuple[tuple[float, float], ...]] = field(
        default_factory=lambda: {
            "1-3 weeks": ((0.01, 0.051), (0.02, 0.069), (0.03, 0.089), (0.05, 0.111), (0.10, 0.140)),
            "4-6 weeks": ((0.01, 0.060), (0.02, 0.072), (0.03, 0.079), (0.05, 0.095), (0.10, 0.126)),
            "7-9 weeks": ((0.01, 0.054), (0.02, 0.081), (0.03, 0.099), (0.05, 0.148), (0.10, 0.206)),
            "10-13 weeks": ((0.01, 0.073), (0.02, 0.121), (0.03, 0.157), (0.05, 0.157), (0.10, 0.217)),
        }
    )
    excess_weeks: int = 8
    # Triage (agent-eligible cases).
    class_a_share: float = 0.70
    class_b_share: float = 0.90
    tail_po_count_max: int = 12
    case_value_max_usd: float = 25_000.0
    effort_rounds_min: float = 3.0
    effort_days_min: float = 5.0
    # Cost model fallbacks when raw_ext.cost_parameter is absent.
    line_down_cost: dict[str, float] = field(default_factory=lambda: {"A": 5000.0, "B": 1000.0, "C": 200.0})
    holding_rate_annual: float = 0.20
    transfer_cost_fixed: float = 40.0
    transfer_cost_per_unit: float = 0.5
    order_cost: float = 75.0
    expedite_freight_default: float = 150.0
    surcharge_default: dict[str, float] = field(
        default_factory=lambda: {"lt7": 10.0, "7_13": 15.0, "ge14": 25.0}
    )
    cny_per_usd: float = 7.1

    @classmethod
    def load(cls, path: Path | None = None) -> PlanningParams:
        """Defaults, overridden by the keys of a YAML file when one is given."""
        if path is None:
            return cls()
        overrides = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        unknown = sorted(set(overrides) - set(cls.__dataclass_fields__))
        if unknown:
            raise ValueError(f"unknown planning parameters {unknown}")
        return cls(**overrides)

    def digest(self) -> str:
        payload = json.dumps(asdict(self), sort_keys=True, default=list)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
