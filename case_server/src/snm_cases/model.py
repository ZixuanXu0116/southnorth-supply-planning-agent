"""Case states, event types, the state machine, actor permissions, and payload schemas.

Every change to a case is one append-only event. An event type fixes which actors may write it, from which
states, and the state after it; its payload is validated against a schema. Prices, surcharges, costs, and
message text are commercial: they live only in ``payload``; ``facts`` repeats the rest so that logistics
roles can read quantities, dates, and purchase orders without them.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

STATES = (
    "new", "prepared", "sent", "responded", "recommended", "decided", "handed_off", "closed", "cancelled",
)  # fmt: skip
TERMINAL = frozenset({"closed", "cancelled"})
OPEN = frozenset(STATES) - TERMINAL
ACTOR_KINDS = ("engine", "planner_agent", "intake_agent", "human", "system")

PURCHASING = "purchasing"
LOGISTICS_ROLES = frozenset({"ops_analyst_global", "site_manager_rr", "site_manager_cn"})
ROLE_PLANTS = {"site_manager_rr": ("US10",), "site_manager_cn": ("CN10",)}
SUPPLIER_LEVERS = frozenset({"expedite", "new_order", "expedited_new_order"})

# Payload keys that are commercial (masked for roles without cost access); everything else is a fact.
COMMERCIAL_KEYS = frozenset(
    {
        "value_usd",
        "lever_cost_usd",
        "stockout_cost_usd",
        "alternative_costs_usd",
        "unit_price",
        "surcharge_pct",
        "surcharge_amount",
        "freight_amount",
        "conditions",
        "message",
        "body",
        "summary",
        "rationale_text",
        "cost",
        "costs",
        "surcharge_pct_max",
        "freight_max_usd",
        "basis",
    }
)


@dataclass(frozen=True)
class Transition:
    actors: frozenset[str]
    from_states: frozenset[str]  # empty for case_opened (no case yet)
    to_state: str | None  # None keeps the state


def _t(actors: set[str], states: set[str] | frozenset[str], to: str | None) -> Transition:
    return Transition(frozenset(actors), frozenset(states), to)


TRANSITIONS: dict[str, Transition] = {
    "case_opened": _t({"engine"}, set(), "new"),
    "case_updated": _t({"engine"}, OPEN, None),
    "condition_cleared": _t({"engine"}, OPEN, None),
    "request_drafted": _t({"planner_agent", "human"}, {"new", "prepared"}, "prepared"),
    # A transfer recommendation is published to logistics by the system (record 005: no logistics page).
    "transfer_prepared": _t({"planner_agent", "human", "system"}, {"new", "prepared"}, "prepared"),
    "triage_overridden": _t({"human"}, OPEN, None),
    "request_sent": _t({"human"}, {"prepared"}, "sent"),
    # A recommendation is advice: purchasing may still negotiate, and a new offer reopens the evaluation.
    "message_recorded": _t({"intake_agent", "human", "system"}, {"sent", "responded", "recommended"}, None),
    # The supplier portal (system) records the options a supplier entered or uploaded (record 005).
    "proposal_recorded": _t(
        {"intake_agent", "human", "system"}, {"sent", "responded", "recommended"}, "responded"
    ),
    "escalated": _t({"intake_agent"}, {"sent", "responded", "recommended"}, None),
    "recommendation_proposed": _t({"planner_agent"}, {"prepared", "responded", "recommended"}, "recommended"),
    "decision_requested": _t({"planner_agent"}, {"recommended"}, None),
    "decision_recorded": _t({"human"}, {"new", "prepared", "sent", "responded", "recommended"}, "decided"),
    "handoff_drafted": _t({"planner_agent"}, {"decided"}, None),
    # A decided order case, or a prepared transfer case, is published to logistics in the lakehouse (record
    # 005: logistics has no page); the system appends the hand-off and, once the data shows the receipt or
    # the transfer, the receipt.
    "handed_off": _t({"human", "system"}, {"decided", "prepared"}, "handed_off"),
    "receipt_confirmed": _t({"human", "system"}, {"handed_off"}, "closed"),
    # Record 004: purchasing's mandate for the supplier exchange; record 005: files exchanged with suppliers.
    "mandate_set": _t({"human"}, {"prepared", "sent", "responded"}, None),
    "attachment_recorded": _t(
        {"human", "intake_agent", "system"}, {"prepared", "sent", "responded", "recommended"}, None
    ),
    "case_cancelled": _t({"human"}, OPEN, "cancelled"),
    "note_added": _t({"planner_agent", "human"}, set(STATES), None),
}
EVENT_TYPES = tuple(TRANSITIONS)


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ------------------------------------------------------------------------------------------- payloads


class EngineSnapshot(Strict):
    """The engine's view of the case on one run (case_opened, case_updated)."""

    engine_case_id: str
    as_of_date: date
    site: str
    description: str
    criticality: str | None = None
    condition: Literal["shortage", "warning", "pull", "excess"]
    trigger_date: date
    gap_qty: float
    lever: str
    lever_qty: float
    lever_detail: dict[str, Any] = Field(default_factory=dict)
    coverage_date: date | None = None
    covers_in_time: bool
    days_short: int
    vendor: str | None = None
    vendor_name: str | None = None
    supplier_class: str | None = None
    triage_class: str
    triage_reasons: str
    alternatives: list[dict[str, Any]] = Field(default_factory=list)
    rationale: str
    value_usd: float | None = None
    lever_cost_usd: float | None = None
    stockout_cost_usd: float | None = None
    alternative_costs_usd: dict[str, float] = Field(default_factory=dict)
    # Engine 1.1.0 (record 002): the demand model and the stock-out risk of doing nothing by the trigger date.
    demand_model: dict[str, Any] | None = None
    stockout_risk_no_action: float | None = None
    engine_version: str
    params_digest: str


class ConditionCleared(Strict):
    as_of_date: date


class OutwardFields(Strict):
    """Everything a supplier may learn about a case (PROPOSAL §5.5)."""

    case_id: str
    request_type: Literal["expedite", "quote"]
    material: str
    description: str
    quantity: float
    needed_date: date
    ship_to_plant: str
    po_number: str | None = None
    po_item: str | None = None


class RequestDrafted(Strict):
    outward: OutwardFields
    message: str = Field(min_length=20, max_length=2000)


class TransferPrepared(Strict):
    from_location: str
    quantity: float = Field(gt=0)
    arrival_date: date
    to_location: str | None = None
    note: str = Field(default="", max_length=1000)


class TriageOverridden(Strict):
    path: Literal["agent", "human"]
    reason: str = Field(min_length=3, max_length=500)


class RequestSent(Strict):
    channel: Literal["portal", "email"]
    handled_by: Literal["intake_agent", "human"]


PROPOSAL_KINDS = ("expedite", "new_order", "partial", "alternative_part")
MANDATE_FIELDS = ("arrival", "quantity", "surcharge", "freight", "validity", "kind")


class Mandate(Strict):
    """What purchasing accepts from a supplier on this case (record 004): the envelope the intake agent
    negotiates within and never states. The ceilings are commercial; the date and quantities are outward."""

    latest_arrival_date: date
    quantity_min: float = Field(ge=0)
    quantity_max: float = Field(ge=0)
    surcharge_pct_max: float = Field(ge=0)
    freight_max_usd: float = Field(ge=0)
    min_validity_days: int = Field(default=3, ge=1)
    allowed_kinds: list[Literal["expedite", "new_order", "partial", "alternative_part"]] = Field(min_length=1)
    max_exchanges: int = Field(default=6, ge=1)

    @model_validator(mode="after")
    def _range(self) -> Mandate:
        if self.quantity_max < self.quantity_min:
            raise ValueError("quantity_max must not be below quantity_min")
        return self


class MandateSet(Strict):
    mandate: Mandate
    source: Literal["engine_default", "purchasing"]
    basis: str = Field(default="", max_length=1000)


class MessageRecorded(Strict):
    message_id: str
    direction: Literal["outbound", "inbound"]
    author_kind: Literal["intake_agent", "human", "supplier"]
    channel: Literal["portal", "email"]
    body: str = Field(min_length=1, max_length=4000)


class SplitDelivery(Strict):
    quantity: float = Field(gt=0)
    arrival_date: date


class SupplierProposal(Strict):
    """One option of a supplier's answer (PROPOSAL §5.5)."""

    option_id: int = Field(ge=1)
    kind: Literal["expedite", "new_order", "partial", "alternative_part", "decline"]
    quantity: float | None = Field(default=None, gt=0)
    ship_date: date | None = None
    arrival_date: date | None = None
    unit_price: float | None = Field(default=None, ge=0)
    surcharge_pct: float | None = Field(default=None, ge=0)
    surcharge_amount: float | None = Field(default=None, ge=0)
    freight_amount: float | None = Field(default=None, ge=0)
    valid_until: date | None = None
    split_deliveries: list[SplitDelivery] = Field(default_factory=list)
    conditions: str = Field(default="", max_length=500)
    source_message_id: str | None = None
    within_mandate: dict[str, bool] | None = None  # per MANDATE_FIELDS plus "all", set by the service

    @model_validator(mode="after")
    def _complete(self) -> SupplierProposal:
        incomplete = self.quantity is None or self.arrival_date is None
        if self.kind != "decline" and incomplete and not self.split_deliveries:
            raise ValueError("an option other than decline needs quantity and arrival_date or splits")
        if self.surcharge_pct is not None and self.surcharge_amount is not None:
            raise ValueError("give the surcharge as a percentage or as an amount, not both")
        return self


class ProposalRecorded(Strict):
    proposal_ids: list[str]
    options: list[SupplierProposal] = Field(min_length=1)


ESCALATION_REASONS = (
    "surcharge",
    "no_feasible_option",
    "contract_terms",
    "relationship",
    "scope",
    "silence",
    "exchange_cap",
)


class Escalated(Strict):
    reason: Literal[
        "surcharge",
        "no_feasible_option",
        "contract_terms",
        "relationship",
        "scope",
        "silence",
        "exchange_cap",
    ]
    summary: str = Field(min_length=10, max_length=2000)


class RankedOption(Strict):
    option: str  # a proposal_id or an engine lever name
    evaluation_id: str
    rank: int = Field(ge=1)


class RecommendationProposed(Strict):
    options: list[RankedOption] = Field(min_length=1)
    recommended: str
    # The case server's own order of the proposals under the purchasing rule (failure card
    # roleplay-20261019), stored next to the order the planner submitted.
    ranking_rule: str | None = None
    server_ranking: list[str] | None = None
    rationale_text: str = Field(min_length=20, max_length=3000)

    @model_validator(mode="after")
    def _recommended_is_ranked(self) -> RecommendationProposed:
        if self.recommended not in {option.option for option in self.options}:
            raise ValueError("the recommended option must be one of the ranked options")
        return self


class DecisionRequested(Strict):
    note: str = Field(default="", max_length=1000)


class DecisionRecorded(Strict):
    choice: str  # a proposal_id, an engine lever name, or no_action
    rationale_text: str = Field(default="", max_length=2000)


class HandoffDrafted(Strict):
    material: str
    plant: str
    quantity: float
    arrival_date: date | None
    deliveries: list[SplitDelivery] = Field(default_factory=list)
    lever: str
    po_number: str | None = None
    po_item: str | None = None
    vendor: str | None = None
    from_location: str | None = None


class HandedOff(Strict):
    handoff_seq: int = Field(ge=1)


class ReceiptConfirmed(Strict):
    received_qty: float = Field(ge=0)
    received_date: date


class CaseCancelled(Strict):
    reason: str = Field(min_length=3, max_length=500)


class AttachmentRecorded(Strict):
    """A file exchanged with the supplier (the request sheet, or the supplier's filled sheet), stored in the
    case store's file area; the parsed content is recorded as proposals, never executed."""

    attachment_id: str
    direction: Literal["outbound", "inbound"]
    file_name: str = Field(min_length=1, max_length=200)
    content_type: str = Field(min_length=1, max_length=100)
    size_bytes: int = Field(ge=0)
    sha256: str = Field(min_length=64, max_length=64)
    storage_path: str
    message_id: str | None = None
    parsed_options: int = Field(default=0, ge=0)


class NoteAdded(Strict):
    text: str = Field(min_length=1, max_length=4000)

    @field_validator("text")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()


PAYLOADS: dict[str, type[Strict]] = {
    "case_opened": EngineSnapshot,
    "case_updated": EngineSnapshot,
    "condition_cleared": ConditionCleared,
    "request_drafted": RequestDrafted,
    "transfer_prepared": TransferPrepared,
    "triage_overridden": TriageOverridden,
    "request_sent": RequestSent,
    "message_recorded": MessageRecorded,
    "proposal_recorded": ProposalRecorded,
    "escalated": Escalated,
    "recommendation_proposed": RecommendationProposed,
    "decision_requested": DecisionRequested,
    "decision_recorded": DecisionRecorded,
    "handoff_drafted": HandoffDrafted,
    "handed_off": HandedOff,
    "receipt_confirmed": ReceiptConfirmed,
    "case_cancelled": CaseCancelled,
    "note_added": NoteAdded,
    "mandate_set": MandateSet,
    "attachment_recorded": AttachmentRecorded,
}
assert set(PAYLOADS) == set(TRANSITIONS)


def facts_of(payload: Any) -> Any:
    """The payload without commercial keys (recursively)."""
    if isinstance(payload, dict):
        return {key: facts_of(value) for key, value in payload.items() if key not in COMMERCIAL_KEYS}
    if isinstance(payload, list):
        return [facts_of(item) for item in payload]
    return payload
