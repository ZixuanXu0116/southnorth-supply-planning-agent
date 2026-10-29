"""Case service: every read and write of the case store goes through here.

The service enforces the state machine (``model.TRANSITIONS``), the actor and role allowed per event,
the payload schemas, site scope, commercial masking, the intake agent's case binding and exchange cap,
and the outward-field check on outgoing supplier text. Agents and the human CLI (the stand-in for the web
app's buttons) call the same methods with different actors.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

import polars as pl

from snm_data.governance import ROLES
from snm_data.planning.demand import DemandModel
from snm_data.planning.engine import expedite_freight, surcharge_pct
from snm_data.planning.params import PlanningParams

from . import model
from .evaluate import Evaluation, OptionSpec, Receipt, evaluate
from .planning import PlanningData
from .store import Attachment, CaseStore, Event, Message, Proposal

MAX_INTAKE_MESSAGES = 6
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")


class CaseError(Exception):
    """A refused action; ``code`` is stable for the agents (and the evaluation)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Actor:
    kind: str  # engine | planner_agent | intake_agent | human | system
    role: str  # purchasing, a logistics role, engine, intake, system
    run_id: str | None = None
    case_binding: str | None = None  # the intake agent's one case


@dataclass
class CaseView:
    case_id: str
    plant: str
    material: str
    state: str
    path: str  # agent | human
    snapshot: dict[str, Any]
    snapshot_seq: int
    events: list[Event]
    cleared: bool = False  # the latest engine run no longer flags the case (condition_cleared last)

    @property
    def is_transfer(self) -> bool:
        return self.snapshot.get("lever") == "transfer"


def _now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _normalize_number(token: str) -> str:
    token = token.replace(",", "")
    if "." in token:
        token = token.rstrip("0").rstrip(".")
    return token.lstrip("0") or "0"


def numbers_in(text: str) -> set[str]:
    return {_normalize_number(token) for token in _NUMBER.findall(text)}


def allowed_numbers(*values: Any) -> set[str]:
    out: set[str] = set()
    for value in values:
        if value is None:
            continue
        if isinstance(value, float):
            text = f"{value:.3f}"
        else:
            text = value.isoformat() if isinstance(value, date) else str(value)
        out |= numbers_in(text)
        for part in re.split(r"[-/ ]", text):
            if part.isdigit():
                out.add(part.lstrip("0") or "0")
    return out


XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
SUPPLIER_OPEN_STATES = frozenset({"sent", "responded", "recommended"})  # a supplier may still answer
MANDATE_VALIDITY_DAYS = 3  # days purchasing needs to decide on an in-mandate proposal (record 004)
MANDATE_MAX_EXCHANGES = 6  # exchanges with a supplier before the intake agent escalates (PROPOSAL §5.5)
EXPEDITE_LEVERS = ("expedite", "expedited_new_order")  # levers that buy speed with surcharge and freight
# How proposals are ranked for purchasing (decision record 007, the owner, 2026-10-20): where the case's
# demand model prices the stock-out risk, by expected total cost (the option's cost plus its expected
# line-down), with the proposals inside the mandate and, for C parts, those that miss only the arrival date
# competing on it, then the others by the number of mandate fields missed and expected total cost. Without a
# priced risk, rule (a) of 2026-10-19: inside the mandate first, then by fields missed, then by total cost.
RANKING_RULE = "expected_cost"
FALLBACK_RULE = "mandate_first"
# Parts whose late offers compete on expected cost (record 007): A and B parts keep the arrival check.
LATE_OFFERS_FOR = frozenset({"C"})


class CaseService:
    def __init__(
        self, store: CaseStore, planning: PlanningData, clock: Callable[[], datetime] = _now
    ) -> None:
        self.store = store
        self.planning = planning
        self.clock = clock

    # ----------------------------------------------------------------------------------- state

    def view(self, case_id: str) -> CaseView:
        events = self.store.events(case_id)
        if not events:
            raise CaseError("UNKNOWN_CASE", f"No case {case_id}.")
        return self._view(events)

    @staticmethod
    def _view(events: list[Event]) -> CaseView:
        snapshot, snapshot_seq, path, cleared = {}, 0, "human", False
        for event in events:
            if event.event_type in ("case_opened", "case_updated"):
                snapshot, snapshot_seq = event.payload or event.facts, event.seq
                path = "agent" if snapshot.get("triage_class") == "agent_eligible" else "human"
                cleared = False
            elif event.event_type == "condition_cleared":
                cleared = True
            elif event.event_type == "triage_overridden" or event.event_type == "escalated":
                path = event.facts.get("path", "human")
        last = events[-1]
        return CaseView(
            last.case_id, last.plant, last.material, last.state_after, path, snapshot, snapshot_seq, events,
            cleared,
        )  # fmt: skip

    def all_views(self) -> list[CaseView]:
        by_case: dict[str, list[Event]] = {}
        for event in self.store.events():
            by_case.setdefault(event.case_id, []).append(event)
        return [self._view(events) for events in by_case.values()]

    # ----------------------------------------------------------------------------------- append

    def _check_role(self, actor: Actor, event_type: str, view: CaseView | None, plant: str) -> None:
        if actor.kind in ("engine", "system"):
            return
        scope = model.ROLE_PLANTS.get(actor.role)
        if scope and plant not in scope:
            raise CaseError("OUT_OF_SCOPE", f"Role {actor.role} cannot act on plant {plant}.")
        if actor.kind == "intake_agent":
            if view is None or actor.case_binding != view.case_id:
                raise CaseError(
                    "OUT_OF_SCOPE", "The intake agent can act only on the case it was started for."
                )
            if view.path != "agent":
                raise CaseError(
                    "NOT_AGENT_CASE", "This case is handled by purchasing, not by the intake agent."
                )
            return
        transfer = view is not None and view.is_transfer
        logistics = actor.role in model.LOGISTICS_ROLES
        purchasing = actor.role == model.PURCHASING
        allowed = {
            "request_drafted": purchasing,
            "transfer_prepared": logistics or purchasing,
            "triage_overridden": purchasing,
            "request_sent": purchasing,
            "mandate_set": purchasing,
            "attachment_recorded": purchasing,
            "message_recorded": purchasing,
            "proposal_recorded": purchasing,
            "recommendation_proposed": purchasing or (transfer and logistics),
            "decision_requested": purchasing or (transfer and logistics),
            "decision_recorded": logistics if transfer else purchasing,
            "handoff_drafted": purchasing or (transfer and logistics),
            "handed_off": purchasing or (transfer and logistics),
            "receipt_confirmed": logistics,
            "case_cancelled": logistics if transfer else purchasing,
            "note_added": purchasing or logistics,
        }.get(event_type, False)
        if not allowed:
            raise CaseError("ROLE_NOT_ALLOWED", f"Role {actor.role} cannot record {event_type} on this case.")

    def append(
        self,
        actor: Actor,
        case_id: str,
        event_type: str,
        payload: dict[str, Any],
        *,
        plant: str | None = None,
        material: str | None = None,
        messages: list[Message] | None = None,
        proposals: list[Proposal] | None = None,
        attachments: list[Attachment] | None = None,
    ) -> Event:
        transition = model.TRANSITIONS.get(event_type)
        if transition is None:
            raise CaseError("UNKNOWN_EVENT", f"Unknown event type {event_type}.")
        if actor.kind not in transition.actors:
            raise CaseError("ACTOR_NOT_ALLOWED", f"{actor.kind} cannot record {event_type}.")
        events = self.store.events(case_id)
        view = self._view(events) if events else None
        if view is None:
            if event_type != "case_opened" or plant is None or material is None:
                raise CaseError("UNKNOWN_CASE", f"No case {case_id}.")
            state = None
        else:
            if event_type == "case_opened":
                raise CaseError("CASE_EXISTS", f"Case {case_id} exists.")
            plant, material, state = view.plant, view.material, view.state
            if state not in transition.from_states:
                raise CaseError(
                    "INVALID_STATE",
                    f"{event_type} is not allowed in state {state} (allowed: "
                    f"{', '.join(sorted(transition.from_states))}).",
                )
        self._check_role(actor, event_type, view, plant)
        try:
            validated = model.PAYLOADS[event_type].model_validate(payload)
        except Exception as error:  # pydantic ValidationError
            raise CaseError("INVALID_PAYLOAD", f"{event_type}: {error}") from error
        data = json.loads(validated.model_dump_json())
        facts = model.facts_of(data)
        if event_type in ("triage_overridden",):
            facts["path"] = data["path"]
        if event_type == "escalated":
            facts["path"] = "human"
        if event_type == "handed_off" and state == "prepared" and not (view and view.is_transfer):
            raise CaseError(
                "INVALID_STATE", "Only a prepared transfer case is handed off without a decision."
            )
        event = Event(
            case_id=case_id,
            seq=(events[-1].seq + 1) if events else 1,
            event_type=event_type,
            state_after=transition.to_state or state,
            plant=plant,
            material=material,
            actor_kind=actor.kind,
            actor_role=actor.role,
            facts=facts,
            payload=data,
            run_id=actor.run_id,
            created_at=self.clock(),
        )
        self.store.append(event, messages or [], proposals or [], attachments or [])
        return event

    # ----------------------------------------------------------------------------------- reads

    def _visible(self, actor: Actor, view: CaseView) -> bool:
        scope = model.ROLE_PLANTS.get(actor.role)
        return scope is None or view.plant in scope

    def _cost_access(self, actor: Actor) -> bool:
        role = ROLES.get(actor.role)
        return bool(role and role.cost_access)

    def _snapshot_for(self, actor: Actor, snapshot: dict[str, Any]) -> dict[str, Any]:
        return snapshot if self._cost_access(actor) else model.facts_of(snapshot)

    def list_cases(
        self,
        actor: Actor,
        state: str | None = None,
        condition: str | None = None,
        triage_class: str | None = None,
        plant: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        rows = []
        for view in sorted(self.all_views(), key=lambda v: (v.snapshot.get("trigger_date", ""), v.case_id)):
            if not self._visible(actor, view):
                continue
            if (state and view.state != state) or (plant and view.plant != plant):
                continue
            if condition and view.snapshot.get("condition") != condition:
                continue
            if triage_class and view.snapshot.get("triage_class") != triage_class:
                continue
            snap = view.snapshot
            rows.append(
                {
                    "case_id": view.case_id,
                    "state": view.state,
                    "path": view.path,
                    "plant": view.plant,
                    "material": view.material,
                    "description": snap.get("description"),
                    "condition": snap.get("condition"),
                    "trigger_date": snap.get("trigger_date"),
                    "gap_qty": snap.get("gap_qty"),
                    "lever": snap.get("lever"),
                    "covers_in_time": snap.get("covers_in_time"),
                    "triage_class": snap.get("triage_class"),
                    "vendor_name": snap.get("vendor_name"),
                    "cleared": view.cleared,
                }
            )
        return {"total": len(rows), "cases": rows[:limit], "truncated": len(rows) > limit}

    def get_case(self, actor: Actor, case_id: str) -> dict[str, Any]:
        view = self.view(case_id)
        if not self._visible(actor, view):
            raise CaseError("OUT_OF_SCOPE", f"Case {case_id} is outside role {actor.role}.")
        cost = self._cost_access(actor)
        proposals = [self._proposal_out(p, cost) for p in self.store.proposals(case_id)]
        out: dict[str, Any] = {
            "case_id": case_id,
            "state": view.state,
            "path": view.path,
            "plant": view.plant,
            "material": view.material,
            "engine": self._snapshot_for(actor, view.snapshot),
            "events": [
                {
                    "seq": e.seq,
                    "event_type": e.event_type,
                    "state_after": e.state_after,
                    "actor": f"{e.actor_kind}/{e.actor_role}",
                    "at": e.created_at.isoformat(),
                    "detail": e.payload if cost else e.facts,
                }
                for e in view.events
                if e.event_type not in ("case_opened", "case_updated")
            ],
            "proposals": proposals,
            "mandate": self._mandate_out(view, cost),
            "attachments": [self._attachment_out(a) for a in self.store.attachments(case_id)],
            "allowed_next": self._allowed_next(view),
        }
        if cost:
            out["messages"] = [
                {
                    "message_id": m.message_id,
                    "direction": m.direction,
                    "author_kind": m.author_kind,
                    "body": m.body,
                }
                for m in self.store.messages(case_id)
            ]
        return out

    @staticmethod
    def _allowed_next(view: CaseView) -> list[str]:
        return sorted(
            name
            for name, transition in model.TRANSITIONS.items()
            if view.state in transition.from_states
            and name not in ("case_opened", "case_updated", "condition_cleared")
        )

    @staticmethod
    def _attachment_out(attachment: Attachment) -> dict[str, Any]:
        return {
            "attachment_id": attachment.attachment_id,
            "direction": attachment.direction,
            "file_name": attachment.file_name,
            "content_type": attachment.content_type,
            "size_bytes": attachment.size_bytes,
            "sha256": attachment.sha256,
            "message_id": attachment.message_id,
            "parsed_options": attachment.parsed_options,
            "uploaded_by": attachment.uploaded_by,
            "at": attachment.created_at.isoformat(),
        }

    def _mandate_out(self, view: CaseView, cost_access: bool) -> dict[str, Any] | None:
        mandate = self.mandate_of(view)
        if mandate is None:
            return None
        data = json.loads(mandate.model_dump_json())
        return data if cost_access else model.facts_of(data)

    @staticmethod
    def _proposal_out(proposal: Proposal, cost_access: bool) -> dict[str, Any]:
        out = {
            "proposal_id": proposal.proposal_id,
            "option_id": proposal.option_id,
            "kind": proposal.kind,
            "within_mandate": json.loads(proposal.within_mandate) if proposal.within_mandate else None,
            "quantity": proposal.quantity,
            "ship_date": proposal.ship_date.isoformat() if proposal.ship_date else None,
            "arrival_date": proposal.arrival_date.isoformat() if proposal.arrival_date else None,
            "valid_until": proposal.valid_until.isoformat() if proposal.valid_until else None,
            "split_deliveries": json.loads(proposal.split_deliveries) if proposal.split_deliveries else [],
            "recorded_by": proposal.recorded_by,
        }
        if cost_access:
            out |= {
                "unit_price": proposal.unit_price,
                "surcharge_pct": proposal.surcharge_pct,
                "surcharge_amount": proposal.surcharge_amount,
                "freight_amount": proposal.freight_amount,
                "conditions": proposal.conditions,
            }
        return out

    # ----------------------------------------------------------------------------------- mandate (record 004)

    @staticmethod
    def mandate_of(view: CaseView) -> model.Mandate | None:
        """The mandate in force: the last mandate_set event of the case."""
        for event in reversed(view.events):
            if event.event_type == "mandate_set":
                return model.Mandate.model_validate(event.payload["mandate"])
        return None

    def mandate_defaults(self, actor: Actor, case_id: str) -> dict[str, Any]:
        """The engine's proposal for the mandate (record 004, amended 2026-10-18): arrive by the trigger date,
        between the gap and the engine's lot, three days of validity, the kinds the case allows. Surcharge
        and expedite freight only when speed is needed: when the engine's lever is an expedite, or nothing
        else arrives in time on a shortage, the ceilings are the supplier's own terms the engine priced that
        expedite with; when a normal order arrives in time, or on a warning (record 001), none."""
        view = self.view(case_id)
        if not self._visible(actor, view):
            raise CaseError("OUT_OF_SCOPE", f"Case {case_id} is outside role {actor.role}.")
        snap = view.snapshot
        if view.is_transfer or snap.get("condition") in ("pull", "excess"):
            raise CaseError("NO_REQUEST_NEEDED", "Transfer cases have no supplier mandate.")
        gap = float(snap.get("gap_qty") or 0.0)
        quantity_min = float(math.ceil(gap - 1e-9))
        quantity_max = max(quantity_min, float(math.ceil(float(snap.get("lever_qty") or 0.0) - 1e-9)))
        line_value = self._price_usd(snap) * quantity_max
        trigger = date.fromisoformat(str(snap["trigger_date"]))
        lever = str(snap.get("lever"))
        levers = self._levers(snap)
        lever_costs = {k: float(v) for k, v in (snap.get("alternative_costs_usd") or {}).items()}
        if snap.get("lever") and snap.get("lever_cost_usd") is not None:
            lever_costs.setdefault(lever, float(snap["lever_cost_usd"]))
        shortage = snap.get("condition") == "shortage"
        speed = shortage and (lever in EXPEDITE_LEVERS or not snap.get("covers_in_time"))
        expedites = [name for name in EXPEDITE_LEVERS if name in levers and name in lever_costs]
        basis_lever = None
        if speed and expedites:
            basis_lever = lever if lever in EXPEDITE_LEVERS else min(expedites, key=lever_costs.__getitem__)
        if basis_lever is not None:
            pct, freight = self._expedite_terms(snap, levers[basis_lever])
            pct = round(pct, 1)
            basis = (
                f"Speed is needed: the engine's {basis_lever} costs {lever_costs[basis_lever]:,.2f} USD with "
                f"this supplier's usual terms, a surcharge of {pct:.1f} % (on a line value of "
                f"{line_value:,.2f} USD) and {freight:,.2f} USD expedite freight; the ceilings are those "
                "terms."
            )
        else:
            pct, freight = 0.0, 0.0
            if not shortage:
                basis = (
                    "Stock stays above zero (a warning), so no expedite is recommended (record 001): "
                    "no surcharge and no expedite freight."
                )
            elif speed:
                basis = (
                    f"Nothing arrives by {trigger.isoformat()} and the engine found no expedite option: no "
                    "surcharge and no expedite freight; edit the ceilings if the supplier offers one."
                )
            else:
                basis = (
                    f"The engine's {lever} arrives {snap.get('coverage_date')}, by the trigger date "
                    f"{trigger.isoformat()}: no surcharge and no expedite freight are needed."
                )
        kinds = ["expedite" if lever == "expedite" else "new_order", "partial", "alternative_part"]
        mandate = model.Mandate(
            latest_arrival_date=trigger,
            quantity_min=quantity_min,
            quantity_max=quantity_max,
            surcharge_pct_max=pct,
            freight_max_usd=freight,
            min_validity_days=MANDATE_VALIDITY_DAYS,
            allowed_kinds=kinds,
            max_exchanges=MANDATE_MAX_EXCHANGES,
        )
        return {"mandate": json.loads(mandate.model_dump_json()), "source": "engine_default", "basis": basis}

    def set_mandate(
        self, actor: Actor, case_id: str, mandate: dict[str, Any], source: str, basis: str = ""
    ) -> Event:
        return self.append(
            actor, case_id, "mandate_set", {"mandate": mandate, "source": source, "basis": basis}
        )

    def within_mandate(
        self, mandate: model.Mandate, option: model.SupplierProposal, line_value: float, today: date
    ) -> dict[str, bool]:
        """Per-field comparison of a proposal with the mandate (record 004); ``all`` is the verdict. On time
        means at least the mandate's minimum quantity arrives by its latest date (a split whose first part is
        early but whose needed quantity comes late is not on time)."""
        deliveries = [(s.quantity, s.arrival_date) for s in option.split_deliveries] or (
            [(option.quantity or 0.0, option.arrival_date)] if option.arrival_date else []
        )
        on_time = sum(qty for qty, arrival in deliveries if arrival <= mandate.latest_arrival_date)
        quantity = sum(s.quantity for s in option.split_deliveries) or (option.quantity or 0.0)
        if option.surcharge_pct is not None:
            surcharge_pct = option.surcharge_pct
        elif option.surcharge_amount is not None and line_value > 0:
            surcharge_pct = option.surcharge_amount / line_value * 100.0
        else:
            surcharge_pct = 0.0
        flags = {
            "arrival": bool(deliveries) and on_time >= mandate.quantity_min - 1e-9,
            "quantity": mandate.quantity_min <= quantity <= mandate.quantity_max,
            "surcharge": surcharge_pct <= mandate.surcharge_pct_max + 1e-9,
            "freight": (option.freight_amount or 0.0) <= mandate.freight_max_usd + 1e-9,
            "validity": option.valid_until is None
            or option.valid_until >= today + timedelta(days=mandate.min_validity_days),
            "kind": option.kind in mandate.allowed_kinds,
        }
        flags["all"] = all(flags.values())
        return flags

    # ----------------------------------------------------------------------------------- attachments (005)

    def record_attachment(
        self,
        actor: Actor,
        case_id: str,
        *,
        file_name: str,
        content: bytes,
        content_type: str,
        direction: str,
        message_id: str | None = None,
        parsed_options: int = 0,
    ) -> Event:
        view = self.view(case_id)
        seq = view.events[-1].seq + 1
        attachment_id = f"A-{seq}-{case_id}"
        storage_path = self.store.save_file(case_id, attachment_id, file_name, content)
        row = Attachment(
            attachment_id,
            case_id,
            view.plant,
            self._vendor(view),
            message_id,
            direction,
            file_name,
            content_type,
            len(content),
            hashlib.sha256(content).hexdigest(),
            storage_path,
            parsed_options,
            "intake_agent"
            if actor.kind == "intake_agent"
            else ("supplier" if actor.kind == "system" else "human"),
            self.clock(),
        )
        payload = {
            "attachment_id": attachment_id,
            "direction": direction,
            "file_name": file_name,
            "content_type": content_type,
            "size_bytes": len(content),
            "sha256": row.sha256,
            "storage_path": storage_path,
            "message_id": message_id,
            "parsed_options": parsed_options,
        }
        return self.append(actor, case_id, "attachment_recorded", payload, attachments=[row])

    def read_attachment(self, actor: Actor, case_id: str, attachment_id: str) -> tuple[Attachment, bytes]:
        view = self.view(case_id)
        if not self._visible(actor, view):
            raise CaseError("OUT_OF_SCOPE", f"Case {case_id} is outside role {actor.role}.")
        for attachment in self.store.attachments(case_id):
            if attachment.attachment_id == attachment_id:
                return attachment, self.store.read_file(attachment.storage_path)
        raise CaseError("UNKNOWN_ATTACHMENT", f"No attachment {attachment_id} on case {case_id}.")

    # ----------------------------------------------------------------------------------- system steps (005)

    def hand_off(self, run_id: str | None, case_id: str) -> Event:
        """Publish a decided order case or a prepared transfer case to logistics (the lakehouse is the
        hand-off; logistics has no page)."""
        view = self.view(case_id)
        handoffs = sum(1 for e in view.events if e.event_type == "handed_off")
        return self.append(
            Actor("system", "system", run_id), case_id, "handed_off", {"handoff_seq": handoffs + 1}
        )

    def confirm_receipt(
        self, run_id: str | None, case_id: str, received_qty: float, received_date: date
    ) -> Event:
        """Close a handed-off case once the data shows the receipt or the transfer."""
        return self.append(
            Actor("system", "system", run_id),
            case_id,
            "receipt_confirmed",
            {"received_qty": received_qty, "received_date": received_date.isoformat()},
        )

    # ----------------------------------------------------------------------------------- purchasing (005)

    def default_request_text(self, case_id: str, request_type: str) -> str:
        """A request text built from the outward fields only (it passes the outgoing-text check)."""
        outward = self.outward_fields(self.view(case_id), request_type)
        quantity = f"{outward.quantity:g}"
        if request_type == "expedite":
            return (
                f"Please confirm whether purchase order {outward.po_number} item {outward.po_item} for "
                f"material {outward.material} ({outward.description}) can arrive at plant "
                f"{outward.ship_to_plant} by {outward.needed_date.isoformat()}, and quote any surcharge, "
                "freight, and validity. Partial "
                "deliveries are welcome; the attached sheet has one row per option."
            )
        return (
            f"Please quote {quantity} pieces of material {outward.material} ({outward.description}) for "
            f"delivery to plant {outward.ship_to_plant} by {outward.needed_date.isoformat()}: price, "
            "ship date, arrival date, surcharge and freight if any, and how long the offer is valid. "
            "Partial deliveries "
            "are welcome; the attached sheet has one row per option."
        )

    def send_request(
        self,
        actor: Actor,
        case_id: str,
        mandate: dict[str, Any],
        source: str,
        basis: str = "",
        sheet: tuple[str, bytes] | None = None,
    ) -> list[Event]:
        """Purchasing approves the drafted request: the mandate, the request sheet, the send, and the
        request text as the first outbound message (records 004 and 005)."""
        view = self.view(case_id)
        draft = next((e for e in reversed(view.events) if e.event_type == "request_drafted"), None)
        if draft is None or view.state != "prepared":
            raise CaseError("NO_REQUEST", "Only a prepared case with a drafted request can be sent.")
        events = [self.set_mandate(actor, case_id, mandate, source, basis)]
        if sheet is not None:
            events.append(
                self.record_attachment(
                    actor,
                    case_id,
                    file_name=sheet[0],
                    content=sheet[1],
                    content_type=XLSX,
                    direction="outbound",
                )
            )
        events.append(
            self.append(actor, case_id, "request_sent", {"channel": "portal", "handled_by": "human"})
        )
        events.append(self.record_message(actor, case_id, draft.payload["message"], "outbound", "human"))
        return events

    def decision_choices(self, case_id: str) -> list[str]:
        view = self.view(case_id)
        snap = view.snapshot
        levers = [snap.get("lever")] + [a.get("lever") for a in snap.get("alternatives") or []]
        proposals = [p.proposal_id for p in self.store.proposals(case_id) if p.kind != "decline"]
        return [*proposals, *[lever for lever in levers if lever], "no_action"]

    def decide(
        self, actor: Actor, case_id: str, choice: str, rationale: str = "", run_id: str | None = None
    ) -> list[Event]:
        """A person records the decision; unless it is no action, the system publishes it to logistics."""
        if choice not in self.decision_choices(case_id):
            raise CaseError("UNKNOWN_OPTION", f"{choice} is not an option of case {case_id}.")
        recommended = self.recommended_choice(case_id)
        if choice != recommended and not rationale.strip():
            raise CaseError(
                "RATIONALE_REQUIRED",
                f"Say in one line why you choose {choice}"
                + (f" over the recommended {recommended}." if recommended else " (nothing is recommended)."),
            )
        events = [
            self.append(actor, case_id, "decision_recorded", {"choice": choice, "rationale_text": rationale})
        ]
        if choice != "no_action":
            events.append(self.hand_off(run_id, case_id))
        return events

    def recommended_choice(self, case_id: str) -> str | None:
        """What a decision without a reason may choose (the owner, 2026-10-20): the recommended proposal once
        proposals exist (None when none is recommended), else the engine's lever."""
        if any(p.kind != "decline" for p in self.store.proposals(case_id)):
            return self.recommended_proposal(case_id)
        return str(self.view(case_id).snapshot.get("lever") or "") or None

    def _ranking(self, view: CaseView) -> tuple[str, list[tuple[Evaluation, list[str], bool]]]:
        """The rule in force for the case and its proposals in that rule's order: (evaluation, mandate fields
        missed, eligible). Eligible proposals compete on cost and come first: those inside the mandate and,
        under the expected-cost rule, a C part's proposals that miss only the arrival date (record 007)."""
        rows = []
        for proposal in self.store.proposals(view.case_id):
            if proposal.kind == "decline":
                continue
            evaluation = self._evaluate(view, proposal.proposal_id)
            flags = json.loads(proposal.within_mandate) if proposal.within_mandate else {}
            rows.append((evaluation, sorted(key for key, ok in flags.items() if key != "all" and not ok)))
        priced = bool(rows) and all(e.expected_line_down_cost is not None for e, _ in rows)
        rule = RANKING_RULE if priced else FALLBACK_RULE
        late_ok = rule == RANKING_RULE and str(view.snapshot.get("criticality") or "C") in LATE_OFFERS_FOR
        ranked = []
        for evaluation, missed in rows:
            eligible = not missed or (late_ok and missed == ["arrival"])
            cost = expected_total(evaluation) if rule == RANKING_RULE else evaluation.total_cost
            key = (not eligible, 0 if eligible else len(missed), cost, evaluation.option)
            ranked.append((key, (evaluation, missed, eligible)))
        ranked.sort(key=lambda item: item[0])
        return rule, [row for _, row in ranked]

    def rank_options(self, actor: Actor, case_id: str) -> dict[str, Any]:
        """The proposals in purchasing's order (``_ranking``, record 007), each with its evaluation, the
        mandate fields it misses, and whether it competes on cost; the engine's levers and doing nothing are
        reference rows, not ranked among quotes."""
        view = self.view(case_id)
        if not self._visible(actor, view):
            raise CaseError("OUT_OF_SCOPE", f"Case {case_id} is outside role {actor.role}.")
        access = self._cost_access(actor)
        rule, ranking = self._ranking(view)
        reference = [
            self._evaluate(view, choice).public(access)
            for choice in self.decision_choices(case_id)
            if not choice.startswith("P-")
        ]
        rows = []
        for rank, (evaluation, missed, eligible) in enumerate(ranking, start=1):
            row = {"rank": rank, **evaluation.public(access), "missed": missed}
            row["late_priced_by_risk"] = eligible and missed == ["arrival"]
            row["expected_total_cost"] = round(expected_total(evaluation), 2) if access else None
            rows.append(row)
        return {"rule": rule, "ranked": rows, "reference": reference}

    def recommended_proposal(self, case_id: str) -> str | None:
        """The rule's first proposal when it competes on cost (record 007; record 004 answer 4 before it);
        None without one."""
        _, ranking = self._ranking(self.view(case_id))
        if ranking and ranking[0][2]:
            return ranking[0][0].option
        return None

    def publish_transfer(self, run_id: str | None, case_id: str) -> list[Event]:
        """A transfer case (pull or excess) as the engine recommends it, published to logistics."""
        system = Actor("system", "system", run_id)
        events = [self.prepare_transfer(system, case_id, "published to logistics from the engine's lever")]
        events.append(self.hand_off(run_id, case_id))
        return events

    # ----------------------------------------------------------------------------------- supplier portal

    @staticmethod
    def supplier_actor(vendor: str) -> Actor:
        return Actor("system", f"supplier_{vendor}")

    def _supplier_case(
        self, vendor: str, case_id: str, view: CaseView | None = None
    ) -> tuple[CaseView, Event]:
        view = view or self.view(case_id)
        draft = next((e for e in reversed(view.events) if e.event_type == "request_drafted"), None)
        sent = any(e.event_type == "request_sent" for e in view.events)
        if self._vendor(view) != vendor or draft is None or not sent:
            raise CaseError("OUT_OF_SCOPE", "This request is not addressed to your company.")
        return view, draft

    def open_requests_by_vendor(self) -> dict[str, int]:
        """How many sent requests still wait for each vendor (one pass over the store)."""
        counts: dict[str, int] = {}
        for view in self.all_views():
            if view.state in SUPPLIER_OPEN_STATES and any(
                e.event_type == "request_sent" for e in view.events
            ):
                counts[self._vendor(view)] = counts.get(self._vendor(view), 0) + 1
        return counts

    def supplier_requests(self, vendor: str) -> list[dict[str, Any]]:
        """The vendor's requests, outward fields only."""
        rows = []
        for view in self.all_views():
            try:
                _, draft = self._supplier_case(vendor, view.case_id, view)
            except CaseError:
                continue
            outward = draft.payload["outward"]
            sent = next(e for e in view.events if e.event_type == "request_sent")
            rows.append(
                {
                    "case_id": view.case_id,
                    "status": "open" if view.state in SUPPLIER_OPEN_STATES else "closed",
                    "request_type": outward["request_type"],
                    "material": outward["material"],
                    "description": outward["description"],
                    "quantity": outward["quantity"],
                    "needed_date": outward["needed_date"],
                    "sent_at": sent.created_at.isoformat(),
                }
            )
        return sorted(rows, key=lambda r: (r["status"] != "open", r["needed_date"], r["case_id"]))

    def supplier_view(self, vendor: str, case_id: str) -> dict[str, Any]:
        """Everything the supplier may see of one request: the outward fields, the request text, the
        exchanged messages and files, and the options it gave (never the mandate or its flags)."""
        view, draft = self._supplier_case(vendor, case_id)
        return {
            "case_id": case_id,
            "status": "open" if view.state in SUPPLIER_OPEN_STATES else "closed",
            "outward": draft.payload["outward"],
            "request": draft.payload["message"],
            "messages": [
                {
                    "message_id": m.message_id,
                    "from": "you" if m.author_kind == "supplier" else "SouthNorth Solutions purchasing",
                    "body": m.body,
                    "at": m.created_at.isoformat(),
                }
                for m in self.store.messages(case_id)
            ],
            "options": [
                {
                    "option_id": p.option_id,
                    "kind": p.kind,
                    "quantity": p.quantity,
                    "ship_date": p.ship_date.isoformat() if p.ship_date else None,
                    "arrival_date": p.arrival_date.isoformat() if p.arrival_date else None,
                    "split_deliveries": json.loads(p.split_deliveries) if p.split_deliveries else [],
                    "unit_price": p.unit_price,
                    "surcharge_pct": p.surcharge_pct,
                    "surcharge_amount": p.surcharge_amount,
                    "freight_amount": p.freight_amount,
                    "valid_until": p.valid_until.isoformat() if p.valid_until else None,
                    "conditions": p.conditions,
                    "at": p.created_at.isoformat(),
                }
                for p in self.store.proposals(case_id)
            ],
            "attachments": [
                {
                    "attachment_id": a.attachment_id,
                    "direction": a.direction,
                    "file_name": a.file_name,
                    "at": a.created_at.isoformat(),
                }
                for a in self.store.attachments(case_id)
            ],
        }

    def supplier_file(self, vendor: str, case_id: str, attachment_id: str) -> tuple[Attachment, bytes]:
        self._supplier_case(vendor, case_id)
        for attachment in self.store.attachments(case_id):
            if attachment.attachment_id == attachment_id:
                return attachment, self.store.read_file(attachment.storage_path)
        raise CaseError("UNKNOWN_ATTACHMENT", f"No attachment {attachment_id} on this request.")

    def supplier_reply(
        self,
        vendor: str,
        case_id: str,
        body: str,
        options: list[dict[str, Any]],
        file: tuple[str, bytes, str] | None = None,
    ) -> list[Event]:
        """A supplier's answer through the portal: the message (untrusted text, kept verbatim), the file it
        uploaded, and the options it entered or the file held, as proposals compared with the mandate."""
        view, _ = self._supplier_case(vendor, case_id)
        if view.state not in SUPPLIER_OPEN_STATES:
            raise CaseError("REQUEST_CLOSED", "This request is closed; no more answers are recorded.")
        if not body.strip() and not options and file is None:
            raise CaseError("EMPTY_REPLY", "Write a message, enter an option, or upload the sheet.")
        for option in options:  # validate before anything is written
            try:
                model.SupplierProposal.model_validate(option)
            except Exception as error:
                raise CaseError("INVALID_PAYLOAD", f"option {option.get('option_id')}: {error}") from error
        actor = self.supplier_actor(vendor)
        placeholder = "(answer in the attached sheet)" if file is not None else "(options entered)"
        text = body.strip() or placeholder
        message = self.record_message(actor, case_id, text, "inbound", "supplier")
        events = [message]
        if file is not None:
            events.append(
                self.record_attachment(
                    actor,
                    case_id,
                    file_name=file[0],
                    content=file[1],
                    content_type=file[2],
                    direction="inbound",
                    message_id=message.payload["message_id"],
                    parsed_options=len(options),
                )
            )
        if options:
            events.append(
                self.record_proposal(actor, case_id, options, source_message_id=message.payload["message_id"])
            )
        return events

    def explain_case(self, actor: Actor, case_id: str) -> dict[str, Any]:
        view = self.view(case_id)
        if not self._visible(actor, view):
            raise CaseError("OUT_OF_SCOPE", f"Case {case_id} is outside role {actor.role}.")
        snap = view.snapshot
        position = self.planning.position(view.material, view.plant)
        rows = position.select(
            "day", "projected_qty", "firm_demand_qty", "forecast_qty", "receipt_qty"
        ).to_dicts()
        trigger = str(snap.get("trigger_date"))
        window = [r for r in rows if str(r["day"]) >= trigger][:7]
        return {
            "case_id": case_id,
            "rationale": snap.get("rationale"),
            "condition": snap.get("condition"),
            "trigger_date": trigger,
            "gap_qty": snap.get("gap_qty"),
            "recommended_lever": snap.get("lever"),
            "lever_detail": snap.get("lever_detail"),
            "coverage_date": snap.get("coverage_date"),
            "covers_in_time": snap.get("covers_in_time"),
            "days_short": snap.get("days_short"),
            "alternatives": snap.get("alternatives"),
            "triage": {"class": snap.get("triage_class"), "reasons": snap.get("triage_reasons")},
            "projection_from_trigger": [
                {key: (value.isoformat() if isinstance(value, date) else value) for key, value in r.items()}
                for r in window
            ],
            "min_projected_qty": float(position.get_column("projected_qty").min())
            if position.height
            else None,
            "engine": {"version": snap.get("engine_version"), "params_digest": snap.get("params_digest")},
            # The exact fields a supplier request may contain (the only figures allowed in its text).
            "supplier_request": self._request_preview(view),
        }

    def _request_preview(self, view: CaseView) -> dict[str, Any] | None:
        lever = view.snapshot.get("lever")
        if view.snapshot.get("condition") in ("excess", "pull") or lever in (
            "transfer",
            "move_to_overflow",
            "hold",
        ):
            return None
        request_type = "expedite" if lever == "expedite" else "quote"
        return json.loads(self.outward_fields(view, request_type).model_dump_json())

    # ----------------------------------------------------------------------------------- evaluation

    def _price_usd(self, snap: dict[str, Any]) -> float:
        value, gap = snap.get("value_usd"), snap.get("gap_qty")
        if value is not None and gap:
            return float(value) / float(gap)
        return 0.0

    def option_spec(self, view: CaseView, option: str) -> OptionSpec:
        """An engine lever (by name) or a recorded proposal (by proposal_id) as receipts and terms."""
        snap = view.snapshot
        costs = self.planning.costs()
        if option.startswith("P-"):
            proposal = next((p for p in self.store.proposals(view.case_id) if p.proposal_id == option), None)
            if proposal is None:
                raise CaseError("UNKNOWN_OPTION", f"No proposal {option} on this case.")
            receipts: list[Receipt] = []
            if proposal.split_deliveries:
                receipts = [
                    Receipt(float(s["quantity"]), date.fromisoformat(s["arrival_date"]))
                    for s in json.loads(proposal.split_deliveries)
                ]
            elif proposal.kind != "decline" and proposal.quantity and proposal.arrival_date:
                receipts = [Receipt(proposal.quantity, proposal.arrival_date)]
            detail = snap.get("lever_detail") or {}
            # On an expedite case the supplier's expedite or partial answer reschedules the open order: its
            # original receipt leaves the projection. Anything else is new supply with its own order cost.
            reschedules = (
                snap.get("lever") == "expedite"
                and proposal.kind in ("expedite", "partial")
                and snap.get("coverage_date") is not None
            )
            removes: tuple[Receipt, ...] = ()
            if reschedules:
                expected = date.fromisoformat(str(snap["coverage_date"])) + timedelta(
                    days=int(detail.get("days_gained", 0))
                )
                removes = (Receipt(sum(r.quantity for r in receipts), expected),)
            # Anything delivered that does not reschedule the case's open order needs a new purchase order,
            # an expedite offered on a case without one included.
            new_supply = proposal.kind != "decline" and not reschedules
            fixed = costs["order_cost"] if new_supply else 0.0
            return OptionSpec(
                option,
                proposal.kind,
                tuple(receipts),
                removes,
                proposal.unit_price,
                proposal.surcharge_pct,
                proposal.surcharge_amount,
                proposal.freight_amount,
                fixed,
            )
        if option == "no_action":
            # Doing nothing: the projection as it is, with the line-down cost of every day below zero.
            return OptionSpec(option, option, ())
        lever = self._levers(snap).get(option)
        if lever is None or not lever.get("coverage_date"):
            raise CaseError("UNKNOWN_OPTION", f"{option} is neither a proposal id nor a lever of this case.")
        arrival = date.fromisoformat(str(lever["coverage_date"]))
        qty = float(lever["qty"])
        lever_costs = snap.get("alternative_costs_usd") or {}
        if option == "expedite":
            expected = arrival + timedelta(days=int(lever.get("days_gained", 0)))
            total = float(lever_costs.get("expedite", 0.0))
            _, freight = self._expedite_terms(snap, lever)
            freight = min(freight, total)
            return OptionSpec(
                option,
                option,
                (Receipt(qty, arrival),),
                (Receipt(qty, expected),),
                surcharge_amount=round(total - freight, 2),
                freight_amount=freight,
            )
        if option == "transfer":
            fixed = costs["transfer_cost_fixed"] + costs["transfer_cost_per_unit"] * qty
            return OptionSpec(option, option, (Receipt(qty, arrival),), (), None, None, None, None, fixed)
        if option == "move_to_overflow":
            # Stock leaves the main warehouse for the overflow hub: the pooled projection is unchanged.
            fixed = costs["transfer_cost_fixed"] + costs["transfer_cost_per_unit"] * qty
            return OptionSpec(option, option, (), (), None, None, None, None, fixed)
        total = float(lever_costs.get(option, costs["order_cost"]))
        if option == "expedited_new_order":
            # The engine's lump sum, split into the order cost, the supplier's expedite freight, and the
            # surcharge it assumed (the remainder).
            _, freight = self._expedite_terms(snap, lever)
            fixed = min(float(costs["order_cost"]), total)
            freight = min(freight, total - fixed)
            return OptionSpec(
                option,
                option,
                (Receipt(qty, arrival),),
                surcharge_amount=round(total - fixed - freight, 2),
                freight_amount=freight,
                fixed_cost=fixed,
            )
        return OptionSpec(option, option, (Receipt(qty, arrival),), (), None, None, None, None, total)

    @staticmethod
    def _levers(snap: dict[str, Any]) -> dict[str, dict[str, Any]]:
        """The engine's chosen lever and its alternatives, by name, with coverage date, quantity, detail."""
        levers = {
            snap.get("lever"): {
                "lever": snap.get("lever"),
                "coverage_date": snap.get("coverage_date"),
                "qty": snap.get("lever_qty"),
                **(snap.get("lever_detail") or {}),
            }
        }
        for alternative in snap.get("alternatives") or []:
            levers.setdefault(alternative["lever"], alternative)
        return levers

    def _vendor_terms(self, snap: dict[str, Any]) -> dict[str, Any] | None:
        """The case supplier's expedite terms from the engine's supplier profile; None means the engine's
        defaults (no profile row or no terms)."""
        suppliers = self.planning.suppliers()
        vendor = snap.get("vendor")
        if not vendor or not suppliers.height or "surcharge_pct_ge14" not in suppliers.columns:
            return None
        rows = suppliers.filter(pl.col("vendor") == str(vendor)).to_dicts()
        if not rows or rows[0].get("surcharge_pct_ge14") is None:
            return None
        return rows[0]

    def _expedite_terms(self, snap: dict[str, Any], lever: dict[str, Any]) -> tuple[float, float]:
        """The surcharge percent and the freight the engine priced an expedite lever with."""
        vendor = self._vendor_terms(snap)
        pct = surcharge_pct(vendor, int(lever.get("days_gained") or 0), PlanningParams())
        return pct, expedite_freight(vendor, self.planning.costs())

    def evaluate_option(self, actor: Actor, case_id: str, option: str) -> dict[str, Any]:
        view = self.view(case_id)
        if not self._visible(actor, view):
            raise CaseError("OUT_OF_SCOPE", f"Case {case_id} is outside role {actor.role}.")
        result = self._evaluate(view, option)
        return result.public(self._cost_access(actor))

    def _evaluate(self, view: CaseView, option: str) -> Evaluation:
        spec = self.option_spec(view, option)
        snap = view.snapshot
        return evaluate(
            view.case_id,
            view.snapshot_seq,
            str(snap.get("condition")),
            snap.get("criticality"),
            self.planning.position(view.material, view.plant),
            spec,
            self.planning.costs(),
            self._price_usd(snap),
            trigger=date.fromisoformat(str(snap["trigger_date"])),
            model=DemandModel.from_json(snap.get("demand_model")),
        )

    # ----------------------------------------------------------------------------------- planner agent

    def outward_fields(self, view: CaseView, request_type: str) -> model.OutwardFields:
        snap = view.snapshot
        detail = snap.get("lever_detail") or {}
        if request_type == "expedite" and snap.get("lever") != "expedite":
            raise CaseError(
                "NO_OPEN_ORDER", "The engine found no open purchase order to expedite on this case."
            )
        # Suppliers deliver whole units: the requested quantity is the gap rounded up.
        quantity = float(
            math.ceil(float(snap["lever_qty"] if request_type == "expedite" else snap["gap_qty"]) - 1e-9)
        )
        return model.OutwardFields(
            case_id=view.case_id,
            request_type=request_type,
            material=view.material,
            description=str(snap.get("description", "")),
            quantity=round(quantity, 3),
            needed_date=date.fromisoformat(str(snap["trigger_date"])),
            ship_to_plant=view.plant,
            po_number=detail.get("ebeln") if request_type == "expedite" else None,
            po_item=detail.get("ebelp") if request_type == "expedite" else None,
        )

    def check_outgoing_text(self, view: CaseView, text: str, outward: model.OutwardFields) -> None:
        """Outgoing supplier text may only contain the outward fields' numbers and the supplier's own
        figures, and no other supplier's name (PROPOSAL §7.1)."""
        allowed = allowed_numbers(*outward.model_dump().values())
        for message in self.store.messages(view.case_id):
            if message.direction == "inbound":
                allowed |= numbers_in(message.body)
        leaked = sorted(numbers_in(text) - allowed)
        if leaked:
            raise CaseError(
                "OUTWARD_CHECK",
                f"The text contains figures that are not outward fields of the case: {', '.join(leaked)}.",
            )
        own = view.snapshot.get("vendor")
        suppliers = self.planning.suppliers()
        if suppliers.height:
            others = suppliers.filter(pl.col("vendor") != own).get_column("name").to_list()
            named = [name for name in others if name and name.lower() in text.lower()]
            if named:
                raise CaseError("OUTWARD_CHECK", f"The text names other suppliers: {', '.join(named)}.")

    def draft_supplier_request(self, actor: Actor, case_id: str, request_type: str, message: str) -> Event:
        view = self.view(case_id)
        if view.snapshot.get("condition") == "excess":
            raise CaseError("NO_REQUEST_NEEDED", "Excess cases need no supplier request.")
        outward = self.outward_fields(view, request_type)
        self.check_outgoing_text(view, message, outward)
        return self.append(
            actor, case_id, "request_drafted", {"outward": outward.model_dump(), "message": message}
        )

    def prepare_transfer(self, actor: Actor, case_id: str, note: str = "") -> Event:
        view = self.view(case_id)
        snap = view.snapshot
        candidates = [
            {
                "lever": snap.get("lever"),
                "coverage_date": snap.get("coverage_date"),
                "qty": snap.get("lever_qty"),
                **(snap.get("lever_detail") or {}),
            }
        ]
        candidates += snap.get("alternatives") or []
        transfer = next((c for c in candidates if c.get("lever") in ("transfer", "move_to_overflow")), None)
        if transfer is None:
            raise CaseError(
                "NO_TRANSFER",
                "The engine recommends no move between the main warehouse and an overflow hub for this case.",
            )
        payload = {
            "from_location": transfer.get("from_location") or transfer["storage_location"],
            "quantity": float(transfer["qty"]),
            "arrival_date": str(transfer["coverage_date"]),
            "to_location": transfer.get("to_location"),
            "note": note,
        }
        return self.append(actor, case_id, "transfer_prepared", payload)

    def propose_recommendation(
        self, actor: Actor, case_id: str, options: list[dict[str, Any]], recommended: str, rationale_text: str
    ) -> Event:
        view = self.view(case_id)
        for option in options:
            expected = self._evaluate(view, str(option.get("option"))).evaluation_id
            if option.get("evaluation_id") != expected:
                raise CaseError(
                    "UNTRACED_OPTION",
                    f"Option {option.get('option')}: cite the evaluation_id returned by evaluate_option "
                    f"for this case ({expected}).",
                )
        order = [row["option"] for row in self.rank_options(actor, case_id)["ranked"]]
        # Planner v4 (adopted 2026-10-19): the proposals are submitted in purchasing's order; engine options
        # may follow them, declines are not ranked.
        ranked = sorted(options, key=lambda option: int(option.get("rank") or 0))
        submitted = [str(o.get("option")) for o in ranked if str(o.get("option")) in set(order)]
        if submitted != order:
            raise CaseError(
                "UNRANKED",
                f"Submit the proposals in purchasing's order (rank_options): {', '.join(order)}.",
            )
        return self.append(
            actor,
            case_id,
            "recommendation_proposed",
            {
                "options": options,
                "recommended": recommended,
                "rationale_text": rationale_text,
                "ranking_rule": self._ranking(self.view(case_id))[0],
                "server_ranking": order,
            },
        )

    def request_decision(self, actor: Actor, case_id: str, note: str = "") -> Event:
        return self.append(actor, case_id, "decision_requested", {"note": note})

    def draft_handoff(self, actor: Actor, case_id: str) -> Event:
        view = self.view(case_id)
        decision = next((e for e in reversed(view.events) if e.event_type == "decision_recorded"), None)
        if decision is None:
            raise CaseError("NO_DECISION", "No decision is recorded on this case.")
        choice = decision.payload.get("choice") or decision.facts.get("choice")
        if choice == "no_action":
            raise CaseError("NO_HANDOFF", "The decision was no action; there is nothing to hand off.")
        spec = self.option_spec(view, choice)
        snap = view.snapshot
        detail = snap.get("lever_detail") or {}
        quantity = sum(r.quantity for r in spec.receipts)
        arrival = max((r.arrival_date for r in spec.receipts), default=None)
        on_open_order = bool(spec.removes) or spec.kind == "expedite"
        payload = {
            "material": view.material,
            "plant": view.plant,
            "quantity": round(quantity, 3),
            "arrival_date": arrival.isoformat() if arrival else None,
            "deliveries": [
                {"quantity": r.quantity, "arrival_date": r.arrival_date.isoformat()} for r in spec.receipts
            ]
            if len(spec.receipts) > 1
            else [],
            "lever": spec.kind,
            "po_number": detail.get("ebeln") if on_open_order else None,
            "po_item": detail.get("ebelp") if on_open_order else None,
            "vendor": snap.get("vendor") if spec.kind != "transfer" else None,
            "from_location": detail.get("storage_location") if spec.kind == "transfer" else None,
        }
        return self.append(actor, case_id, "handoff_drafted", payload)

    def add_note(self, actor: Actor, case_id: str, text: str) -> Event:
        return self.append(actor, case_id, "note_added", {"text": text})

    # ----------------------------------------------------------------------------------- intake agent

    def intake_request(self, actor: Actor) -> dict[str, Any]:
        """What the intake agent may know: the drafted request's outward fields and the thread."""
        view = self.view(str(actor.case_binding))
        draft = next((e for e in reversed(view.events) if e.event_type == "request_drafted"), None)
        if draft is None:
            raise CaseError("NO_REQUEST", "No request was drafted for this case.")
        return {
            "outward": draft.payload["outward"],
            "approved_request": draft.payload["message"],
            "state": view.state,
            "thread": [
                {
                    "message_id": m.message_id,
                    "direction": m.direction,
                    "author_kind": m.author_kind,
                    "body": m.body,
                }
                for m in self.store.messages(view.case_id)
            ],
            "messages_left": MAX_INTAKE_MESSAGES - self._intake_messages(view.case_id),
        }

    def _intake_messages(self, case_id: str) -> int:
        return sum(1 for m in self.store.messages(case_id) if m.author_kind == "intake_agent")

    def _vendor(self, view: CaseView) -> str:
        return str(view.snapshot.get("vendor") or "")

    def record_message(
        self, actor: Actor, case_id: str, body: str, direction: str, author_kind: str, channel: str = "portal"
    ) -> Event:
        view = self.view(case_id)
        if author_kind == "intake_agent":
            if self._intake_messages(case_id) >= MAX_INTAKE_MESSAGES:
                raise CaseError(
                    "EXCHANGE_CAP", "The exchange cap is reached; escalate the case to purchasing."
                )
            draft = next((e for e in reversed(view.events) if e.event_type == "request_drafted"), None)
            if draft is None:
                raise CaseError("NO_REQUEST", "No request was drafted for this case.")
            self.check_outgoing_text(view, body, model.OutwardFields(**draft.payload["outward"]))
        seq = view.events[-1].seq + 1
        message_id = f"M-{seq}-{case_id}"
        message = Message(
            message_id,
            case_id,
            view.plant,
            self._vendor(view),
            direction,
            author_kind,
            channel,
            body,
            self.clock(),
        )
        payload = {
            "message_id": message_id,
            "direction": direction,
            "author_kind": author_kind,
            "channel": channel,
            "body": body,
        }
        return self.append(actor, case_id, "message_recorded", payload, messages=[message])

    def record_proposal(
        self, actor: Actor, case_id: str, options: list[dict[str, Any]], source_message_id: str | None = None
    ) -> Event:
        view = self.view(case_id)
        try:
            parsed = [
                model.SupplierProposal.model_validate({**o, "source_message_id": source_message_id})
                for o in options
            ]
        except Exception as error:
            raise CaseError("INVALID_PAYLOAD", f"proposal: {error}") from error
        seq = view.events[-1].seq + 1
        recorded_by = {"intake_agent": "intake_agent", "system": "supplier"}.get(actor.kind, "human")
        mandate = self.mandate_of(view)
        line_value = self._price_usd(view.snapshot) * (mandate.quantity_max if mandate else 0.0)
        today = self.clock().date()
        rows, ids = [], []
        for option in parsed:
            if mandate is not None:
                option.within_mandate = self.within_mandate(mandate, option, line_value, today)
            proposal_id = f"P-{option.option_id}-{seq}-{case_id}"
            ids.append(proposal_id)
            rows.append(
                Proposal(
                    proposal_id,
                    case_id,
                    view.plant,
                    self._vendor(view),
                    option.option_id,
                    option.kind,
                    option.quantity,
                    option.ship_date,
                    option.arrival_date,
                    option.unit_price,
                    option.surcharge_pct,
                    option.surcharge_amount,
                    option.freight_amount,
                    option.valid_until,
                    json.dumps([json.loads(s.model_dump_json()) for s in option.split_deliveries])
                    if option.split_deliveries
                    else None,
                    option.conditions or None,
                    source_message_id,
                    recorded_by,
                    self.clock(),
                    json.dumps(option.within_mandate) if option.within_mandate else None,
                )
            )
        payload = {"proposal_ids": ids, "options": [json.loads(o.model_dump_json()) for o in parsed]}
        return self.append(actor, case_id, "proposal_recorded", payload, proposals=rows)

    def escalate(self, actor: Actor, case_id: str, reason: str, summary: str) -> Event:
        return self.append(actor, case_id, "escalated", {"reason": reason, "summary": summary})

    # ----------------------------------------------------------------------------------- engine sync

    def sync_engine_run(self, run_id: str | None = None) -> dict[str, int]:
        """Open, update, or clear cases from the latest engine run (one open case per plant, material, and
        condition; its identifier keeps the first trigger date)."""
        engine = Actor("engine", "engine", run_id)
        open_cases = {}
        known = set()
        for view in self.all_views():
            known.add(view.case_id)
            if view.state not in model.TERMINAL:
                open_cases[(view.plant, view.material, view.snapshot.get("condition"))] = view
        counts = {"opened": 0, "updated": 0, "unchanged": 0, "cleared": 0}
        seen = set()
        for row in self.planning.cases().iter_rows(named=True):
            snapshot = snapshot_from_row(row)
            key = (row["plant"], row["material"], row["condition"])
            seen.add(key)
            view = open_cases.get(key)
            if view is None:
                # A condition that persists after its case closed (in a simulation, a receipt the data never
                # showed) opens a new case: the engine's identifier with a running suffix.
                case_id, number = row["case_id"], 1
                while case_id in known:
                    number += 1
                    case_id = f"{row['case_id']}-{number}"
                known.add(case_id)
                self.append(
                    engine,
                    case_id,
                    "case_opened",
                    snapshot,
                    plant=row["plant"],
                    material=row["material"],
                )
                counts["opened"] += 1
            elif _material_change(view.snapshot, snapshot):
                self.append(engine, view.case_id, "case_updated", snapshot)
                counts["updated"] += 1
            else:
                counts["unchanged"] += 1
        as_of = None
        cases = self.planning.cases()
        if cases.height:
            as_of = str(cases.get_column("as_of_date")[0])
        for key, view in open_cases.items():
            if key in seen or as_of is None:
                continue
            last = next((e for e in reversed(view.events) if e.event_type == "condition_cleared"), None)
            if last is None or str(last.facts.get("as_of_date")) != as_of:
                self.append(engine, view.case_id, "condition_cleared", {"as_of_date": as_of})
                counts["cleared"] += 1
        return counts


_SNAPSHOT_FIELDS = tuple(model.EngineSnapshot.model_fields)


def expected_total(evaluation: Evaluation) -> float:
    """An option's cost with its expected line-down in place of the projection's (record 002 phase 4); the
    projection's total where the risk is not priced."""
    if evaluation.expected_line_down_cost is None:
        return evaluation.total_cost
    return evaluation.total_cost - evaluation.stockout_cost + evaluation.expected_line_down_cost


def snapshot_from_row(row: dict[str, Any]) -> dict[str, Any]:
    def as_json(value: Any, default: Any) -> Any:
        if value is None or value == "":
            return default
        return json.loads(value) if isinstance(value, str) else value

    snapshot = {key: row.get(key) for key in _SNAPSHOT_FIELDS if key in row}
    snapshot["engine_case_id"] = row["case_id"]
    snapshot["description"] = row.get("description") or ""
    snapshot["lever_detail"] = as_json(row.get("lever_detail"), {})
    snapshot["alternatives"] = as_json(row.get("alternatives"), [])
    snapshot["alternative_costs_usd"] = as_json(row.get("alternative_costs_usd"), {})
    snapshot["demand_model"] = as_json(row.get("demand_model"), None)
    for key in ("gap_qty", "lever_qty", "value_usd", "lever_cost_usd", "stockout_cost_usd"):
        if snapshot.get(key) is not None:
            snapshot[key] = float(snapshot[key])
    for key in ("as_of_date", "trigger_date", "coverage_date"):
        if snapshot.get(key) is not None:
            snapshot[key] = str(snapshot[key])[:10]
    return json.loads(model.EngineSnapshot.model_validate(snapshot).model_dump_json())


def _material_change(old: dict[str, Any], new: dict[str, Any]) -> bool:
    keys = (
        "trigger_date",
        "gap_qty",
        "lever",
        "coverage_date",
        "covers_in_time",
        "triage_class",
        "lever_qty",
    )
    # Engine 1.1.0 adds the demand model: a snapshot without one takes the first run that has it.
    gains_model = old.get("demand_model") is None and new.get("demand_model") is not None
    return gains_model or any(old.get(key) != new.get(key) for key in keys)
