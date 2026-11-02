"""The purchasing case page and the supplier page (decision record 005).

A thin layer over ``snm_cases.service``: every button is one service call, so the state machine, the roles,
the masks, and the outgoing-text checks decide what is allowed, not the page. Logistics has no page (it
reads the lakehouse). In local mode the person chooses a role to play (purchasing or one supplier); every
POST is written to ``<runs>/access.jsonl`` with the role, the action, the case, and the outcome.

The supplier templates receive only ``CaseService.supplier_view`` and ``supplier_requests`` output, never
a case: the outward fields, the request text, the exchanged messages and files, and the supplier's own
options.
"""

from __future__ import annotations

import json
import os
import re
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

from snm_cases import documents, excel, model
from snm_cases.service import XLSX, Actor, CaseError, CaseService, CaseView
from snm_cases.store import Event

TEMPLATES = Path(__file__).parent / "templates"
ROLE_COOKIE = "snm_role"
PURCHASING = "purchasing"
SUPPLIER_PREFIX = "supplier_"
ORDER_CONDITIONS = ("shortage", "warning")
TRANSFER_CONDITIONS = ("pull", "excess")
MAX_UPLOAD_BYTES = 8_000_000  # a screenshot or a scanned PDF
PLANNER_TASKS = ("prepare", "evaluate")  # the planner's tasks the case page offers


@dataclass
class WebSettings:
    mode: str = "local"  # local | lakehouse
    runs_dir: Path = Path("runs/web")
    store_dir: Path | None = None
    planning_dir: Path | None = None
    harness_cli: Path | None = None  # harness/dist/cli/case.js; None hides the planner buttons
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def planner_available(self) -> bool:
        if self.harness_cli is None or not self.harness_cli.exists():
            return False
        if self.mode == "lakehouse":
            # A deployed app: the harness's case server writes to the cases schema (SNM_CASE_BACKEND).
            return os.environ.get("SNM_CASE_BACKEND") == "databricks"
        return self.store_dir is not None and self.planning_dir is not None


class Audit:
    """One JSON line per action: who (role), what (route and action), on which case, and the outcome."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, **entry: Any) -> None:
        entry = {"at": datetime.now(UTC).isoformat(timespec="seconds"), **entry}
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, default=str, sort_keys=True) + "\n")


PlannerRunner = Callable[[WebSettings, str, str, str, str], dict[str, Any]]
# (settings, file content, file name, the request's outward fields) -> checked draft, method, cost, run
ExtractorRunner = Callable[[WebSettings, bytes, str, dict[str, Any]], dict[str, Any]]


def _redirect(url: str, notice: str | None = None, error: str | None = None) -> RedirectResponse:
    query = {key: value for key, value in (("notice", notice), ("error", error)) if value}
    target = f"{url}?{urllib.parse.urlencode(query)}" if query else url
    return RedirectResponse(target, status_code=303)


def _float(value: str | None) -> float | None:
    if value is None or str(value).strip() == "":
        return None
    return float(str(value).replace(",", "").strip())


def _pct(value: float | None) -> str:
    """A probability (0 to 1) as a percentage with two decimals, as the evaluations give it."""
    return "" if value is None else f"{float(value) * 100:.2f}%"


def option_rows(
    options: list[dict[str, Any]], ranking: dict[str, Any] | None, proposals: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """The case's options in purchasing's order (record 004 rule, ``rank_options``): ranked proposals first,
    then the engine's levers and doing nothing as reference rows. Each row carries its arrivals, the mandate
    fields it misses, the option's own cost (without line-down), and the expected total: the option's cost
    plus the expected line-down where the case's demand model prices it, else plus the projection's
    line-down."""
    by_option = {option["option"]: option for option in options}
    by_proposal = {proposal["proposal_id"]: proposal for proposal in proposals}
    order = [row["option"] for row in ranking["ranked"]] if ranking else []
    late = (
        {row["option"] for row in ranking["ranked"] if row.get("late_priced_by_risk")} if ranking else set()
    )
    ordered = [(rank, by_option[name]) for rank, name in enumerate(order, start=1) if name in by_option]
    ordered += [(None, option) for option in options if option["option"] not in order]
    rows = []
    for rank, option in ordered:
        proposal = by_proposal.get(option["option"])
        flags = (proposal or {}).get("within_mandate") or None
        splits = (proposal or {}).get("split_deliveries") or []
        arrivals = [(s["quantity"], s["arrival_date"]) for s in splits]
        if not arrivals and option.get("coverage_date"):
            arrivals = [(option.get("quantity"), option["coverage_date"])]
        cost = None
        if option.get("total_cost") is not None:
            cost = option["total_cost"] - (option.get("stockout_cost") or 0.0)
        expected = option.get("expected_line_down_cost")
        line_down = expected if expected is not None else option.get("stockout_cost")
        rows.append(
            {
                **option,
                "rank": rank,
                "arrivals": arrivals,
                "missed": None
                if flags is None
                else [k for k, v in sorted(flags.items()) if k != "all" and not v],
                "late_priced_by_risk": option["option"] in late,
                "option_cost": cost,
                "line_down": line_down,
                "expected_total": None if cost is None or line_down is None else cost + line_down,
                # What purchasing would pay: the goods at the offered price in place of the price difference.
                "expected_spend": None
                if cost is None or line_down is None or option.get("purchase_value") is None
                else cost
                + line_down
                + option["purchase_value"]
                - (option.get("price_difference_cost") or 0.0),
            }
        )
    return rows


def _facts_line(service: CaseService, actor: Actor, case_id: str, recommended: str) -> str | None:
    try:
        return service.facts_line(actor, case_id, recommended)
    except CaseError:
        return None


def _receipt_default(service: CaseService, view: CaseView) -> dict[str, Any]:
    """What the simulated receipt form starts with: the decided option's receipts, else the engine lever."""
    snap = view.snapshot
    engine_date = snap.get("coverage_date") or snap.get("trigger_date")
    fallback = {"quantity": snap.get("lever_qty"), "date": engine_date}
    decision = next((e for e in reversed(view.events) if e.event_type == "decision_recorded"), None)
    choice = decision.payload.get("choice") if decision else None
    if not choice or choice == "no_action":
        return fallback
    try:
        receipts = service.option_spec(view, choice).receipts
    except CaseError:
        return fallback
    if not receipts:
        return fallback
    last = max(r.arrival_date for r in receipts)
    return {"quantity": sum(r.quantity for r in receipts), "date": last.isoformat()}


def create_app(
    service: CaseService,
    settings: WebSettings,
    planner: PlannerRunner | None = None,
    extractor: ExtractorRunner | None = None,
) -> FastAPI:
    app = FastAPI(title="SouthNorth Solutions cases", docs_url=None, redoc_url=None, openapi_url=None)
    templates = Jinja2Templates(directory=str(TEMPLATES))
    templates.env.filters["money"] = lambda v: "" if v is None else f"{float(v):,.2f}"
    templates.env.filters["qty"] = lambda v: "" if v is None else f"{float(v):g}"
    templates.env.filters["num"] = lambda v: "" if v is None else f"{round(float(v), 2):g}"
    templates.env.filters["pct"] = _pct
    # A proposal id ends with its case id (P-1-7-C-US10-...); on the case's own page the prefix is enough.
    templates.env.filters["short_id"] = lambda v: re.sub(r"-C-[A-Z0-9]+-\d+-[A-Z]+-\d{8}(-\d+)?$", "", str(v))
    audit = Audit(settings.runs_dir / "access.jsonl")
    buyer = Actor("human", PURCHASING)
    vendors: dict[str, str] = {}

    def vendor_names() -> dict[str, str]:
        if not vendors:
            frame = service.planning.suppliers()
            for row in frame.iter_rows(named=True):
                vendors[str(row["vendor"])] = str(row.get("name") or row["vendor"])
        return vendors

    def role_of(request: Request) -> str | None:
        role = request.cookies.get(ROLE_COOKIE)
        if role == PURCHASING:
            return role
        if role and role.startswith(SUPPLIER_PREFIX) and role[len(SUPPLIER_PREFIX) :] in vendor_names():
            return role
        return None

    def page(request: Request, name: str, **context: Any) -> HTMLResponse:
        role = role_of(request)
        context |= {
            "role": role,
            "role_label": _role_label(role, vendor_names()),
            "mode": settings.mode,
            "notice": request.query_params.get("notice"),
            "error": request.query_params.get("error"),
        }
        return templates.TemplateResponse(request, name, context)

    def act(
        request: Request,
        action: str,
        case_id: str | None,
        back: str,
        run: Callable[[], list[Event]],
        notice: str | None = None,
    ) -> RedirectResponse:
        role = role_of(request)
        try:
            events = run()
        except CaseError as error:
            audit.write(
                role=role,
                action=action,
                path=request.url.path,
                case_id=case_id,
                outcome="refused",
                error=f"{error.code}: {error}",
            )
            return _redirect(back, error=str(error))
        except ValueError as error:
            audit.write(
                role=role,
                action=action,
                path=request.url.path,
                case_id=case_id,
                outcome="refused",
                error=f"INVALID_INPUT: {error}",
            )
            return _redirect(back, error=f"Invalid input: {error}")
        types = [event.event_type for event in events]
        audit.write(
            role=role,
            action=action,
            path=request.url.path,
            case_id=case_id,
            outcome="ok",
            events=[f"{e.seq}:{e.event_type}" for e in events],
        )
        return _redirect(back, notice=notice or (f"Recorded: {', '.join(types)}." if types else "Done."))

    def forbidden(request: Request, expected: str) -> HTMLResponse:
        return page(request, "role.html", roles=_role_choices(service, vendor_names()), required=expected)

    # ------------------------------------------------------------------------------------------- roles

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request) -> Response:
        role = role_of(request)
        if role == PURCHASING:
            return RedirectResponse("/cases", status_code=303)
        if role:
            return RedirectResponse("/supplier", status_code=303)
        return RedirectResponse("/role", status_code=303)

    @app.get("/role", response_class=HTMLResponse)
    def choose_role(request: Request) -> HTMLResponse:
        return page(request, "role.html", roles=_role_choices(service, vendor_names()), required=None)

    @app.post("/role")
    def set_role(request: Request, role: str = Form(...)) -> Response:
        valid = role == PURCHASING or (
            role.startswith(SUPPLIER_PREFIX) and role[len(SUPPLIER_PREFIX) :] in vendor_names()
        )
        if not valid:
            return _redirect("/role", error=f"Unknown role {role}.")
        audit.write(role=role, action="role_selected", path="/role", case_id=None, outcome="ok")
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(ROLE_COOKIE, role, httponly=True, samesite="lax")
        return response

    # ------------------------------------------------------------------------------------------- purchasing

    @app.get("/cases", response_class=HTMLResponse)
    def cases(
        request: Request,
        scope: str = "orders",
        state: str = "",
        condition: str = "",
        triage: str = "",
        cleared: bool = False,
    ) -> HTMLResponse:
        if role_of(request) != PURCHASING:
            return forbidden(request, PURCHASING)
        listing = service.list_cases(
            buyer, state=state or None, condition=condition or None, triage_class=triage or None, limit=10_000
        )
        rows = listing["cases"]
        stale = sum(1 for r in rows if r["cleared"])
        if not cleared:
            rows = [r for r in rows if not r["cleared"]]
        if scope == "orders":
            rows = [r for r in rows if r["condition"] in ORDER_CONDITIONS]
        elif scope == "transfers":
            rows = [r for r in rows if r["condition"] in TRANSFER_CONDITIONS]
        counts: dict[str, int] = {}
        for row in rows:
            counts[row["state"]] = counts.get(row["state"], 0) + 1
        return page(
            request,
            "cases.html",
            rows=rows,
            counts=counts,
            scope=scope,
            state=state,
            condition=condition,
            triage=triage,
            states=model.STATES,
            cleared=cleared,
            stale=stale,
        )

    @app.get("/cases/{case_id}", response_class=HTMLResponse)
    def case_page(request: Request, case_id: str) -> HTMLResponse:
        if role_of(request) != PURCHASING:
            return forbidden(request, PURCHASING)
        try:
            case = service.get_case(buyer, case_id)
        except CaseError as error:
            return page(request, "message.html", title="Unknown case", text=str(error))
        explanation = service.explain_case(buyer, case_id)
        view = service.view(case_id)
        is_transfer = case["engine"].get("condition") in TRANSFER_CONDITIONS
        options = []
        offers = [p["proposal_id"] for p in case["proposals"] if p.get("kind") != "decline"]
        for choice in [*offers, *service.reference_options(case_id)]:
            try:
                options.append(service.evaluate_option(buyer, case_id, choice))
            except CaseError:
                continue
        flags = {p["proposal_id"]: p.get("within_mandate") for p in case["proposals"]}
        ranking = service.rank_options(buyer, case_id) if case["proposals"] else None
        rows = option_rows(options, ranking, case["proposals"])
        draft = next((e for e in reversed(view.events) if e.event_type == "request_drafted"), None)
        defaults, request_types, default_text = None, [], {}
        if not is_transfer:
            request_types = ["expedite", "quote"] if case["engine"].get("lever") == "expedite" else ["quote"]
            if view.state == "new":
                default_text = {t: service.default_request_text(case_id, t) for t in request_types}
            if view.state in ("prepared", "sent", "responded"):
                defaults = service.mandate_defaults(buyer, case_id)
        mandate = case.get("mandate") or (defaults["mandate"] if defaults else None)
        receipt = _receipt_default(service, view) if view.state == "handed_off" else None
        proposed = next((e for e in reversed(view.events) if e.event_type == "recommendation_proposed"), None)
        planner_rec = None
        if proposed is not None and ranking is not None:
            submitted = [o["option"] for o in sorted(proposed.payload["options"], key=lambda o: o["rank"])]
            server_order = [row["option"] for row in ranking["ranked"]]
            planner_rec = {
                **proposed.payload,
                # The planner's own order of the proposals, when it differs from the purchasing rule's.
                "submitted_order": [o for o in submitted if o.startswith("P-")]
                if [o for o in submitted if o.startswith("P-")] != server_order
                else None,
                "by_agent": proposed.actor_kind == "planner_agent",
                # Written by the case server with the event; recomputed for one written before it existed.
                "facts_line": proposed.payload.get("facts_line")
                or _facts_line(service, buyer, case_id, str(proposed.payload["recommended"])),
                # Offers recorded after it make the recommendation out of date.
                "stale": any(
                    e.event_type == "proposal_recorded" and e.seq > proposed.seq for e in view.events
                ),
                "run_id": proposed.run_id,
                "at": proposed.created_at.strftime("%Y-%m-%d %H:%M"),
            }
        events = [e for e in case["events"]]
        # The planner's last step, when it is a note (nothing needed, or a failure after two refused writes):
        # shown on its own card, since the history table hides it.
        last = next((e for e in reversed(view.events) if e.actor_kind == "planner_agent"), None)
        planner_note = (
            {
                "text": last.payload.get("text"),
                "at": last.created_at.strftime("%Y-%m-%d %H:%M"),
                "run_id": last.run_id,
            }
            if last is not None and last.event_type == "note_added"
            else None
        )
        return page(
            request,
            "case.html",
            case=case,
            explanation=explanation,
            options=options,
            rows=rows,
            ranking_rule=ranking["rule"] if ranking else None,
            risk_shown=any(row["stockout_risk"] is not None for row in rows),
            evaluations={o["option"]: o for o in options},
            flags=flags,
            recommended=service.recommended_proposal(case_id) if case["proposals"] else None,
            choices=service.decision_choices(case_id),
            decision_default=None if is_transfer else service.recommended_choice(case_id),
            draft=draft.payload if draft else None,
            extraction_drafts=_drafts_for_page(service, case_id),
            draft_fields=DRAFT_FIELDS,
            defaults=defaults,
            mandate=mandate,
            mandate_kinds=model.PROPOSAL_KINDS,
            request_types=request_types,
            default_text=default_text,
            is_transfer=is_transfer,
            events=events,
            receipt=receipt,
            planner_rec=planner_rec,
            planner_note=planner_note,
            planner_available=settings.planner_available,
            vendor_name=vendor_names().get(
                str(case["engine"].get("vendor")), case["engine"].get("vendor_name")
            ),
        )

    @app.post("/cases/{case_id}/draft")
    def draft(
        request: Request, case_id: str, request_type: str = Form(...), message: str = Form(...)
    ) -> Response:
        if role_of(request) != PURCHASING:
            return forbidden(request, PURCHASING)
        back = f"/cases/{case_id}"
        return act(
            request,
            "draft_request",
            case_id,
            back,
            lambda: [service.draft_supplier_request(buyer, case_id, request_type, message.strip())],
        )

    @app.post("/cases/{case_id}/planner")
    def run_planner(request: Request, case_id: str, task: str = Form("prepare")) -> Response:
        if role_of(request) != PURCHASING:
            return forbidden(request, PURCHASING)
        back = f"/cases/{case_id}"
        if task not in PLANNER_TASKS:
            return _redirect(back, error=f"Unknown planner task {task}.")
        if planner is None or not settings.planner_available:
            return _redirect(back, error="The planner agent runs only in local mode with the harness built.")
        as_of = str(service.view(case_id).snapshot.get("as_of_date"))
        result = planner(settings, case_id, task, PURCHASING, as_of)
        written = result.get("written") or {}
        outcome = "ok" if result.get("status") == "written" else str(result.get("status"))
        audit.write(
            role=PURCHASING,
            action=f"planner_{task}",
            path=request.url.path,
            case_id=case_id,
            outcome=outcome,
            run=result.get("trace_path"),
            tool=written.get("tool"),
            cost_usd=(result.get("usage") or {}).get("cost_usd"),
            error=result.get("error"),
        )
        if result.get("status") == "error":
            return _redirect(back, error=f"Planner run failed: {result.get('error')}")
        if result.get("status") == "refused":
            return _redirect(
                back, error=f"Planner ({task}): its write was refused twice; see the planner note."
            )
        text = f"Planner ({task}): {written.get('tool') or result.get('status')}."
        return _redirect(back, notice=text)

    @app.post("/cases/{case_id}/send")
    async def send(request: Request, case_id: str) -> Response:
        if role_of(request) != PURCHASING:
            return forbidden(request, PURCHASING)
        form = await request.form()
        back = f"/cases/{case_id}"

        def run() -> list[Event]:
            defaults = service.mandate_defaults(buyer, case_id)
            mandate = {
                "latest_arrival_date": str(form["latest_arrival_date"]),
                "quantity_min": _float(str(form["quantity_min"])),
                "quantity_max": _float(str(form["quantity_max"])),
                "surcharge_pct_max": _float(str(form["surcharge_pct_max"])),
                "freight_max_usd": _float(str(form["freight_max_usd"])),
                "min_validity_days": int(str(form["min_validity_days"])),
                "allowed_kinds": [str(k) for k in form.getlist("allowed_kinds")],
                "max_exchanges": int(str(form["max_exchanges"])),
            }
            validated = json.loads(model.Mandate.model_validate(mandate).model_dump_json())
            edited = validated != defaults["mandate"]
            basis = defaults["basis"] + (" Edited by purchasing at approval." if edited else "")
            view = service.view(case_id)
            draft = next(e for e in reversed(view.events) if e.event_type == "request_drafted")
            sheet = excel.request_sheet(draft.payload["outward"], draft.payload["message"])
            return service.send_request(
                buyer,
                case_id,
                validated,
                "purchasing" if edited else "engine_default",
                basis,
                (f"request-{case_id}.xlsx", sheet),
            )

        return act(request, "approve_and_send", case_id, back, run)

    @app.post("/cases/{case_id}/message")
    def message(request: Request, case_id: str, body: str = Form(...)) -> Response:
        if role_of(request) != PURCHASING:
            return forbidden(request, PURCHASING)
        back = f"/cases/{case_id}"
        return act(
            request,
            "message_to_supplier",
            case_id,
            back,
            lambda: [service.record_message(buyer, case_id, body.strip(), "outbound", "human")],
        )

    @app.post("/cases/{case_id}/decide")
    def decide(
        request: Request, case_id: str, choice: str = Form(...), rationale: str = Form("")
    ) -> Response:
        if role_of(request) != PURCHASING:
            return forbidden(request, PURCHASING)
        back = f"/cases/{case_id}"
        return act(
            request,
            "decide",
            case_id,
            back,
            lambda: service.decide(buyer, case_id, choice, rationale.strip(), run_id="web"),
        )

    @app.post("/cases/{case_id}/cancel")
    def cancel(request: Request, case_id: str, reason: str = Form(...)) -> Response:
        if role_of(request) != PURCHASING:
            return forbidden(request, PURCHASING)
        back = f"/cases/{case_id}"
        return act(
            request,
            "cancel",
            case_id,
            back,
            lambda: [service.append(buyer, case_id, "case_cancelled", {"reason": reason.strip()})],
        )

    @app.post("/cases/{case_id}/triage")
    def triage(request: Request, case_id: str, path: str = Form(...), reason: str = Form(...)) -> Response:
        if role_of(request) != PURCHASING:
            return forbidden(request, PURCHASING)
        back = f"/cases/{case_id}"
        payload = {"path": path, "reason": reason.strip()}
        return act(
            request,
            "override_triage",
            case_id,
            back,
            lambda: [service.append(buyer, case_id, "triage_overridden", payload)],
        )

    @app.post("/cases/{case_id}/note")
    def note(request: Request, case_id: str, text: str = Form(...)) -> Response:
        if role_of(request) != PURCHASING:
            return forbidden(request, PURCHASING)
        back = f"/cases/{case_id}"
        return act(request, "note", case_id, back, lambda: [service.add_note(buyer, case_id, text)])

    @app.get("/cases/{case_id}/files/{attachment_id}")
    def case_file(request: Request, case_id: str, attachment_id: str) -> Response:
        if role_of(request) != PURCHASING:
            return forbidden(request, PURCHASING)
        try:
            attachment, content = service.read_attachment(buyer, case_id, attachment_id)
        except CaseError as error:
            return _redirect(f"/cases/{case_id}", error=str(error))
        return _download(attachment.file_name, attachment.content_type, content)

    # Simulation stand-ins for what the data and the scheduled job do (local mode only): publishing a
    # transfer case to logistics and the receipt that closes a handed-off case.

    @app.post("/cases/{case_id}/simulate/publish-transfer")
    def publish_transfer(request: Request, case_id: str) -> Response:
        if role_of(request) != PURCHASING or settings.mode != "local":
            return forbidden(request, PURCHASING)
        back = f"/cases/{case_id}"
        return act(
            request,
            "simulate_publish_transfer",
            case_id,
            back,
            lambda: service.publish_transfer("web-simulation", case_id),
        )

    @app.post("/cases/{case_id}/simulate/receipt")
    def receipt(
        request: Request, case_id: str, quantity: str = Form(...), received: str = Form(...)
    ) -> Response:
        if role_of(request) != PURCHASING or settings.mode != "local":
            return forbidden(request, PURCHASING)
        back = f"/cases/{case_id}"
        return act(
            request,
            "simulate_receipt",
            case_id,
            back,
            lambda: [
                service.confirm_receipt(
                    "web-simulation", case_id, float(_float(quantity) or 0.0), date.fromisoformat(received)
                )
            ],
        )

    # ------------------------------------------------------------------------------------------- supplier

    def vendor_of(request: Request) -> str | None:
        role = role_of(request)
        return role[len(SUPPLIER_PREFIX) :] if role and role.startswith(SUPPLIER_PREFIX) else None

    @app.get("/supplier", response_class=HTMLResponse)
    def supplier_home(request: Request) -> HTMLResponse:
        vendor = vendor_of(request)
        if vendor is None:
            return forbidden(request, "a supplier")
        return page(
            request,
            "supplier_list.html",
            requests=service.supplier_requests(vendor),
            company=vendor_names().get(vendor, vendor),
        )

    @app.get("/supplier/{case_id}", response_class=HTMLResponse)
    def supplier_request(request: Request, case_id: str) -> HTMLResponse:
        vendor = vendor_of(request)
        if vendor is None:
            return forbidden(request, "a supplier")
        try:
            view = service.supplier_view(vendor, case_id)
        except CaseError as error:
            return page(request, "message.html", title="Request not found", text=str(error))
        return page(
            request,
            "supplier_request.html",
            request_view=view,
            kinds=excel.KINDS,
            company=vendor_names().get(vendor, vendor),
        )

    @app.get("/supplier/{case_id}/files/{attachment_id}")
    def supplier_file(request: Request, case_id: str, attachment_id: str) -> Response:
        vendor = vendor_of(request)
        if vendor is None:
            return forbidden(request, "a supplier")
        try:
            attachment, content = service.supplier_file(vendor, case_id, attachment_id)
        except CaseError as error:
            return _redirect(f"/supplier/{case_id}", error=str(error))
        return _download(attachment.file_name, attachment.content_type, content)

    def _supplier_document(
        vendor: str, case_id: str, body: str, document: tuple[str, bytes, str]
    ) -> list[Event]:
        events = service.supplier_reply(vendor, case_id, body, [], document)
        attachment = next(e for e in events if e.event_type == "attachment_recorded")
        view = service.view(case_id)
        sent = next((e for e in reversed(view.events) if e.event_type == "request_drafted"), None)
        outward = (sent.payload.get("outward") if sent else None) or {"material": view.material}
        run = (extractor or _no_extractor)(settings, document[1], document[0], outward)
        events.append(
            service.record_extraction(
                case_id,
                attachment.payload["attachment_id"],
                run["draft"],
                run["method"],
                run.get("cost_usd", 0.0),
                run.get("run_id"),
            )
        )
        return events

    @app.post("/cases/{case_id}/drafts/{seq}/confirm")
    async def confirm_draft(request: Request, case_id: str, seq: int) -> Response:
        if role_of(request) != PURCHASING:
            return forbidden(request, PURCHASING)
        form = await request.form()
        back = f"/cases/{case_id}"
        draft = next((e for e in service.extraction_drafts(case_id) if e.seq == seq), None)
        if draft is None:
            return _redirect(back, error="That draft is already recorded or does not exist.")
        try:
            options = _confirmed_options(form, draft.payload.get("options", []))
        except ValueError as error:
            return _redirect(back, error=f"Not recorded: {error}")
        return act(
            request,
            "confirm_draft",
            case_id,
            back,
            lambda: [service.confirm_extraction(buyer, case_id, seq, options)],
        )

    @app.post("/supplier/{case_id}/reply")
    async def supplier_reply(
        request: Request, case_id: str, body: str = Form(""), sheet: UploadFile | None = File(None)
    ) -> Response:
        vendor = vendor_of(request)
        if vendor is None:
            return forbidden(request, "a supplier")
        form = await request.form()
        back = f"/supplier/{case_id}"
        role = role_of(request)
        options: list[dict[str, Any]] = []
        file: tuple[str, bytes, str] | None = None
        if sheet is not None and sheet.filename:
            content = await sheet.read(MAX_UPLOAD_BYTES + 1)
            if len(content) > MAX_UPLOAD_BYTES:
                return _redirect(back, error="The file is larger than 8 MB.")
            kind = documents.kind_of(content)
            if kind is None:
                audit.write(
                    role=role,
                    action="supplier_reply",
                    path=request.url.path,
                    case_id=case_id,
                    outcome="refused",
                    error="FILE: not a PDF, PNG, JPEG, or Excel workbook",
                )
                return _redirect(back, error="Upload a PDF, a PNG or JPEG image, or an Excel workbook.")
            if not (kind == "xlsx" and excel.is_request_sheet(content)):
                # A supplier's own document: kept as sent, read into a draft, confirmed by purchasing.
                document = (
                    Path(sheet.filename).name,
                    content,
                    sheet.content_type or "application/octet-stream",
                )
                return act(
                    request,
                    "supplier_document",
                    case_id,
                    back,
                    lambda: _supplier_document(vendor, case_id, body, document),
                    notice="Thank you: your document was received; purchasing will confirm the offers in it.",
                )
            parsed, problems = excel.parse_reply(content, case_id)
            if problems:
                audit.write(
                    role=role,
                    action="supplier_reply",
                    path=request.url.path,
                    case_id=case_id,
                    outcome="refused",
                    error="SHEET: " + " | ".join(problems),
                )
                return _redirect(back, error="The sheet was not recorded: " + " ".join(problems))
            options += parsed
            file = (Path(sheet.filename).name, content, sheet.content_type or XLSX)
        entered = _entered_option(form, len(options) + 1)
        if entered:
            options.append(entered)
        return act(
            request,
            "supplier_reply",
            case_id,
            back,
            lambda: service.supplier_reply(vendor, case_id, body, options, file),
        )

    return app


DRAFT_FIELDS = (
    ("kind", "Kind"), ("quantity", "Qty"), ("unit_price", "Unit price (USD)"),
    ("surcharge_pct", "Surcharge %"), ("surcharge_amount", "Surcharge"), ("freight_amount", "Freight"),
    ("ship_date", "Ship"), ("arrival_date", "Arrival"), ("valid_until", "Valid until"),
    ("split_deliveries", "Split (qty@date; ...)"),
    ("conditions", "Conditions"),
)  # fmt: skip


def _no_extractor(
    settings: WebSettings, content: bytes, file_name: str, request: dict[str, Any]
) -> dict[str, Any]:
    from snm_cases.documents import check_draft, read_document, rules_extract

    view = read_document(content)
    return {
        "draft": check_draft(rules_extract(view), view),
        "method": "rules",
        "cost_usd": 0.0,
        "run_id": None,
    }


def _split_text(value: Any) -> str:
    return "; ".join(f"{float(p['quantity']):g}@{p['arrival_date']}" for p in value or [])


def _drafts_for_page(service: CaseService, case_id: str) -> list[dict[str, Any]]:
    """The open drafts with each field as the form shows it."""
    names = {a.attachment_id: a.file_name for a in service.store.attachments(case_id)}
    out = []
    for event in service.extraction_drafts(case_id):
        payload = event.payload
        options = []
        for option in payload.get("options", []):
            cells = {}
            for name, _ in DRAFT_FIELDS:
                entry = option["fields"].get(name) or {}
                value = entry.get("value")
                shown = _split_text(value) if name == "split_deliveries" else ("" if value is None else value)
                cells[name] = {**entry, "shown": shown}
            currency = (option["fields"].get("currency") or {}).get("value")
            options.append({"option_id": option["option_id"], "cells": cells, "currency": currency})
        out.append(
            {
                **payload,
                "seq": event.seq,
                "file_name": names.get(payload["attachment_id"], ""),
                "rows": options,
            }
        )
    return out


def _confirmed_options(form: Any, drafted: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The offers purchasing kept, as it confirmed or corrected them."""
    options = []
    for option in drafted:
        number = option["option_id"]
        if not form.get(f"keep-{number}"):
            continue
        row: dict[str, Any] = {"option_id": number, "kind": str(form.get(f"{number}.kind") or "").strip()}
        for key in ("quantity", "unit_price", "surcharge_pct", "surcharge_amount", "freight_amount"):
            value = _float(str(form.get(f"{number}.{key}") or ""))
            if value is not None:
                row[key] = value
        for key in ("ship_date", "arrival_date", "valid_until", "conditions"):
            value = str(form.get(f"{number}.{key}") or "").strip()
            if value:
                row[key] = value
        splits = str(form.get(f"{number}.split_deliveries") or "").strip()
        if splits:
            row["split_deliveries"] = excel.parse_splits(splits)
        _one_surcharge(row, number)
        try:
            model.SupplierProposal.model_validate(row)
        except ValidationError as error:
            raise ValueError(f"offer {number}: {_reason(error)}") from error
        options.append(row)
    if not options:
        raise ValueError("keep at least one offer")
    return options


def _one_surcharge(row: dict[str, Any], number: int) -> None:
    """A proposal has its surcharge as a percentage or as an amount: a zero next to the other is dropped
    (a quote saying "surcharge: none" reads as 0 % and 0.00); two non-zero values are purchasing's call."""
    pct, amount = row.get("surcharge_pct"), row.get("surcharge_amount")
    if pct is None or amount is None:
        return
    if amount == 0:
        row.pop("surcharge_amount")
    elif pct == 0:
        row.pop("surcharge_pct")
    else:
        raise ValueError(f"offer {number}: keep the surcharge as a percentage or as an amount, not both")


def _reason(error: ValidationError) -> str:
    """The first problem of a validation error in plain words: the field and what is wrong with it."""
    first = error.errors()[0]
    field = ".".join(str(part) for part in first.get("loc", ()))
    message = str(first.get("msg", "invalid")).removeprefix("Value error, ")
    return f"{field}: {message}" if field else message


def _entered_option(form: Any, option_id: int) -> dict[str, Any] | None:
    """The one option a supplier may type into the reply form (the sheet holds up to five)."""
    kind = str(form.get("kind") or "").strip()
    if not kind:
        return None
    option: dict[str, Any] = {"option_id": option_id, "kind": kind}
    for key in ("quantity", "unit_price", "surcharge_pct", "freight_amount"):
        value = _float(str(form.get(key) or ""))
        if value is not None:
            option[key] = value
    for key in ("ship_date", "arrival_date", "valid_until"):
        value = str(form.get(key) or "").strip()
        if value:
            option[key] = value
    conditions = str(form.get("conditions") or "").strip()
    if conditions:
        option["conditions"] = conditions
    return option


def _download(file_name: str, content_type: str, content: bytes) -> Response:
    safe = Path(file_name).name.replace('"', "")
    return Response(
        content, media_type=content_type, headers={"Content-Disposition": f'attachment; filename="{safe}"'}
    )


def _role_label(role: str | None, vendors: dict[str, str]) -> str:
    if role == PURCHASING:
        return "Purchasing"
    if role and role.startswith(SUPPLIER_PREFIX):
        vendor = role[len(SUPPLIER_PREFIX) :]
        return f"Supplier {vendors.get(vendor, vendor)} ({vendor})"
    return "no role"


def _role_choices(service: CaseService, vendors: dict[str, str]) -> list[dict[str, Any]]:
    """Purchasing first, then the suppliers with open requests, then the others."""
    waiting = service.open_requests_by_vendor()
    open_requests = {vendor: waiting.get(vendor, 0) for vendor in vendors}
    suppliers = sorted(vendors.items(), key=lambda item: (-open_requests[item[0]], item[1]))
    return [{"role": PURCHASING, "label": "Purchasing (SouthNorth Solutions)", "open": None}] + [
        {"role": f"{SUPPLIER_PREFIX}{vendor}", "label": f"{name} ({vendor})", "open": open_requests[vendor]}
        for vendor, name in suppliers
    ]
