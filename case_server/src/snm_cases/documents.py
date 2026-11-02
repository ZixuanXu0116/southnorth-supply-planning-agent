"""Supplier documents other than the request sheet: a PDF quote, a screenshot, the supplier's own Excel
(change card change-20261102-supplier-documents).

Three parts, all deterministic:

- ``read_document``: what an extractor reads from a file: the text layer (a PDF's text, an Excel's cells) and
  the page images (a PDF's first pages rendered, or the image itself).
- ``rules_extract``: the code-only extractor (labels and table headers, no model), the control arm.
- ``check_draft``: the checks every draft goes through before purchasing sees it, whichever extractor made it:
  formats, the evidence against the text layer, the currency, split quantities, missing fields, and text that
  addresses the reader with instructions.

A draft is never a proposal: purchasing confirms or corrects it on the case page, and only then is it
recorded (``CaseService.confirm_extraction``).
"""

from __future__ import annotations

import io
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from openpyxl import load_workbook

MAX_PAGES = 3  # pages of a PDF an extractor sees
RENDER_SCALE = 2.0  # 144 dpi
MAX_IMAGE_SIDE = 2000  # the API scales larger images down anyway
KINDS = ("expedite", "new_order", "partial", "alternative_part", "decline")
FIELDS = (
    "kind", "quantity", "unit_price", "currency", "surcharge_pct", "surcharge_amount", "freight_amount",
    "ship_date", "arrival_date", "valid_until", "split_deliveries", "conditions",
)  # fmt: skip
NUMBER_FIELDS = ("quantity", "unit_price", "surcharge_pct", "surcharge_amount", "freight_amount")
DATE_FIELDS = ("ship_date", "arrival_date", "valid_until")
USD = {"USD", "US$", "$", "US DOLLAR", "US DOLLARS"}
# Text that addresses the reader (a person or a model) with instructions: shown to purchasing, never followed.
INSTRUCTION = re.compile(
    r"(?i)\b(ignore (?:all |any |your |the )?(?:previous |prior |above )?instructions|"
    r"(?:ai|language model|assistant|automated) (?:system|systems|reader|agent)s?\b.{0,40}"
    r"\b(?:must|should|record|accept|set)|"
    r"system prompt|disregard (?:the|your) (?:rules|instructions))"
)


@dataclass
class DocumentView:
    kind: str  # pdf | image | xlsx
    text: str  # the text layer: a PDF's text, an Excel's cells; empty for an image or a scanned PDF
    images: list[tuple[str, bytes]] = field(default_factory=list)  # (media type, bytes) per page or image
    pages: int = 0
    problems: list[str] = field(default_factory=list)


def kind_of(content: bytes) -> str | None:
    """The document kind from the file's first bytes (never from its name)."""
    if content.startswith(b"%PDF"):
        return "pdf"
    if content.startswith(b"\x89PNG\r\n\x1a\n") or content.startswith(b"\xff\xd8\xff"):
        return "image"
    if content.startswith(b"PK\x03\x04"):
        return "xlsx"
    return None


def _png(image: Any) -> bytes:
    if max(image.size) > MAX_IMAGE_SIDE:
        image.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
    out = io.BytesIO()
    image.convert("RGB").save(out, format="PNG", optimize=True)
    return out.getvalue()


def read_document(content: bytes) -> DocumentView:
    kind = kind_of(content)
    if kind == "pdf":
        import pypdfium2

        pdf = pypdfium2.PdfDocument(content)
        texts, images = [], []
        for index in range(min(len(pdf), MAX_PAGES)):
            page = pdf[index]
            texts.append(page.get_textpage().get_text_range())
            images.append(("image/png", _png(page.render(scale=RENDER_SCALE).to_pil())))
        problems = (
            [f"only the first {MAX_PAGES} of {len(pdf)} pages are read"] if len(pdf) > MAX_PAGES else []
        )
        text = "\n".join(texts).replace("\r\n", "\n").strip()
        return DocumentView("pdf", text, images, len(pdf), problems)
    if kind == "image":
        from PIL import Image

        image = Image.open(io.BytesIO(content))
        return DocumentView("image", "", [("image/png", _png(image))], 1)
    if kind == "xlsx":
        return DocumentView("xlsx", xlsx_text(content), [], 1)
    return DocumentView("unknown", "", [], 0, ["not a PDF, PNG, JPEG, or Excel file"])


def xlsx_text(content: bytes) -> str:
    """Every non-empty row of every sheet, cells separated by ' | ', with the sheet and row number."""
    book = load_workbook(io.BytesIO(content), data_only=True, read_only=True)
    lines = []
    for sheet in book.worksheets:
        for number, row in enumerate(sheet.iter_rows(values_only=True), start=1):
            cells = [
                "" if v is None else (v.date().isoformat() if isinstance(v, datetime) else str(v))
                for v in row
            ]
            while cells and not cells[-1]:
                cells.pop()
            if any(cells):
                lines.append(f"{sheet.title} row {number}: " + " | ".join(cells))
    return "\n".join(lines)


# ----------------------------------------------------------------------------------- values

_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1
)}  # fmt: skip


def parse_date(text: str) -> date | None:
    """ISO, day.month.year, and month-name dates; an all-numeric date with slashes is ambiguous (None)."""
    text = text.strip()
    if m := re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", text):
        return _date(int(m[1]), int(m[2]), int(m[3]))
    if m := re.fullmatch(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", text):
        return _date(int(m[3]), int(m[2]), int(m[1]))
    if m := re.fullmatch(r"([A-Za-z]{3})[a-z]*\.? (\d{1,2}),? (\d{4})", text):
        month = _MONTHS.get(m[1].lower())
        return _date(int(m[3]), month, int(m[2])) if month else None
    if m := re.fullmatch(r"(\d{1,2}) ([A-Za-z]{3})[a-z]*\.? (\d{4})", text):
        month = _MONTHS.get(m[2].lower())
        return _date(int(m[3]), month, int(m[1])) if month else None
    return None


def _date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def parse_number(text: str) -> float | None:
    cleaned = re.sub(r"(?i)usd|us\$|eur|€|\$|pcs|pieces|%", "", str(text)).replace(",", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return None


def field_value(
    value: Any = None, evidence: str = "", certain: bool = False, note: str = ""
) -> dict[str, Any]:
    return {"value": value, "evidence": evidence, "certain": certain, "note": note}


def empty_option(option_id: int) -> dict[str, Any]:
    return {"option_id": option_id, "fields": {name: field_value() for name in FIELDS}}


# ----------------------------------------------------------------------------------- the code-only extractor

_DATE = (
    r"(\d{4}-\d{1,2}-\d{1,2}|\d{1,2}\.\d{1,2}\.\d{4}|[A-Z][a-z]{2,8}\.? \d{1,2},? \d{4}"
    r"|\d{1,2} [A-Z][a-z]{2,8}\.? \d{4})"
)
_AMOUNT = r"(?:USD|US\$|EUR|€|\$)?\s*([\d,]+(?:\.\d+)?)"
LABELS: dict[str, str] = {
    "quantity": r"(?i)\b(?:quantity|qty)\b\s*[:=]?\s*([\d,]+(?:\.\d+)?)",
    "unit_price": r"(?i)\b(?:unit price|price per (?:piece|pc|unit)|price/pc|price)\b\s*[:=]?\s*" + _AMOUNT,
    "surcharge_pct": r"(?i)\b(?:surcharge|express fee|expedite fee)\b\s*[:=]?\s*([\d.]+)\s*%",
    "surcharge_amount": r"(?i)\b(?:surcharge|express fee|expedite fee)\b\s*[:=]?\s*(?:USD|US\$|\$)\s*"
    r"([\d,]+(?:\.\d+)?)",
    "freight_amount": r"(?i)\bfreight\b\s*[:=]?\s*" + _AMOUNT,
    "ship_date": r"(?i)\b(?:ship date|shipping date|ships? on)\b\s*[:=]?\s*" + _DATE,
    "arrival_date": r"(?i)\b(?:arrival(?: date)?|delivery(?: date)?|arrives?(?: on)?|delivered by)\b"
    + r"\s*[:=]?\s*"
    + _DATE,
    "valid_until": r"(?i)\b(?:valid until|valid till|validity|offer valid until|expires)\b\s*[:=]?\s*"
    + _DATE,
}
HEADERS: dict[str, str] = {
    "kind": r"(?i)^(kind|type|offer type)$",
    "quantity": r"(?i)^(qty|quantity|pcs|pieces)",
    "unit_price": r"(?i)(unit price|price)",
    "surcharge_pct": r"(?i)surcharge.*%|%.*surcharge",
    "freight_amount": r"(?i)freight",
    "ship_date": r"(?i)ship",
    "arrival_date": r"(?i)(arrival|delivery)",
    "valid_until": r"(?i)valid",
}
_KIND_WORDS = {
    "decline": r"(?i)\b(cannot|can not|unable to)\s+(supply|offer|quote|deliver)|\bdeclin",
    "partial": r"(?i)\b(partial|split|in two lots|two deliveries)\b",
    "expedite": r"(?i)\bexpedit",
    "alternative_part": r"(?i)\b(alternative part|substitute|equivalent part)\b",
}


def _kind_from(text: str) -> str:
    for kind, pattern in _KIND_WORDS.items():
        if re.search(pattern, text):
            return kind
    return "new_order"


def _labeled(segment: str, option_id: int) -> dict[str, Any]:
    option = empty_option(option_id)
    fields = option["fields"]
    fields["kind"] = field_value(_kind_from(segment), "", True)
    for name, pattern in LABELS.items():
        if (m := re.search(pattern, segment)) is None:
            continue
        raw = m.group(1)
        value = parse_date(raw) if name in DATE_FIELDS else parse_number(raw)
        if value is not None:
            fields[name] = field_value(
                value.isoformat() if isinstance(value, date) else value, m.group(0), True
            )
    currency = re.search(r"\b(USD|EUR|GBP|JPY|CNY)\b|€", segment)
    if currency:
        fields["currency"] = field_value(
            "EUR" if currency.group(0) == "€" else currency.group(0), currency.group(0), True
        )
    return option


def _table(lines: list[str]) -> list[dict[str, Any]] | None:
    """Rows under a header line that names at least three known columns (cells split by '|' or tabs)."""
    for index, line in enumerate(lines):
        cells = [c.strip() for c in re.split(r"\s*\|\s*|\t", re.sub(r"^.*? row \d+: ", "", line))]
        mapping = {}
        for position, cell in enumerate(cells):
            for name, pattern in HEADERS.items():
                if name not in mapping and re.search(pattern, cell):
                    mapping[name] = position
                    break
        if len(mapping) < 3:
            continue
        options = []
        for row in lines[index + 1 :]:
            values = [c.strip() for c in re.split(r"\s*\|\s*|\t", re.sub(r"^.*? row \d+: ", "", row))]
            if len(values) < len(cells) - 2 or not any(values):
                break
            option = empty_option(len(options) + 1)
            fields = option["fields"]
            for name, position in mapping.items():
                raw = values[position] if position < len(values) else ""
                if not raw:
                    continue
                if name == "kind":
                    kind = raw.strip().lower().replace(" ", "_")
                    value = kind if kind in KINDS else _kind_from(raw)
                elif name in DATE_FIELDS:
                    parsed = parse_date(raw)
                    value = parsed.isoformat() if parsed else None
                else:
                    value = parse_number(raw)
                if value is not None:
                    fields[name] = field_value(value, raw, True)
            if fields["kind"]["value"] is None:
                fields["kind"] = field_value(_kind_from(row), "", True)
            options.append(option)
        return options or None
    return None


def rules_extract(view: DocumentView) -> dict[str, Any]:
    """The control arm: labels and table headers in the text layer; nothing without a text layer."""
    if not view.text.strip():
        return {
            "options": [],
            "instructions_found": [],
            "problems": ["no text layer: the code-only extractor cannot read an image or a scanned PDF"],
        }
    # A PDF table's text layer wraps cells: rejoin dates broken after a hyphen ("2026-11-" / "19").
    text = re.sub(r"(\d{4}-\d{1,2}-)\s*\n\s*(\d{1,2})\b", r"\1\2", view.text)
    if re.search(_KIND_WORDS["decline"], text):
        option = empty_option(1)
        option["fields"]["kind"] = field_value("decline", "", True)
        return {"options": [option], "instructions_found": [], "problems": []}
    lines = [line for line in text.splitlines() if line.strip()]
    table = _table(lines)
    if table:
        return {"options": table, "instructions_found": [], "problems": []}
    parts = re.split(r"(?im)^\s*(?:option|offer|alternative)\s+\d+\b.*$", text)
    segments = [p for p in parts[1:] if p.strip()] if len(parts) > 1 else [text]
    return {
        "options": [_labeled(segment, n) for n, segment in enumerate(segments, start=1)],
        "instructions_found": [],
        "problems": [],
    }


# ----------------------------------------------------------------------------------- checks on every draft


def _normal(text: str) -> str:
    """Text for the evidence check without any whitespace: a PDF table's text layer wraps cells mid-value
    ("2026-" / "11-24"), while an extractor quotes the value as printed."""
    return re.sub(r"\s+", "", str(text)).lower()


def _flag(entry: dict[str, Any], note: str) -> None:
    entry["certain"] = False
    entry["note"] = f"{entry['note']}; {note}".strip("; ") if entry.get("note") else note


def check_draft(draft: dict[str, Any], view: DocumentView) -> dict[str, Any]:
    """The same checks for every extractor; a failed check marks the field for purchasing, never drops it."""
    out = json.loads(json.dumps(draft, default=str))
    out.setdefault("instructions_found", [])
    out.setdefault("problems", [])
    out["checks"] = []
    text = _normal(view.text)
    for match in INSTRUCTION.finditer(view.text):
        line = next((ln for ln in view.text.splitlines() if match.group(0) in ln), match.group(0)).strip()
        if line not in out["instructions_found"]:
            out["instructions_found"].append(line)
    for option in out.get("options", []):
        fields = option.setdefault("fields", {})
        for name in FIELDS:
            entry = fields.setdefault(name, field_value())
            for key, default in (("evidence", ""), ("certain", False), ("note", "")):
                entry.setdefault(key, default)
        _check_formats(fields, out["checks"], option["option_id"])
        if text:
            for name, entry in fields.items():
                evidence = _normal(entry.get("evidence") or "")
                if entry["value"] is not None and entry["certain"] and evidence and evidence not in text:
                    _flag(entry, "the evidence is not in the document's text")
                    out["checks"].append(f"option {option['option_id']} {name}: evidence not in the text")
        currency = fields["currency"]["value"]
        if currency is not None and str(currency).strip().upper() not in USD:
            _flag(fields["currency"], "the case is priced in USD")
            if fields["unit_price"]["value"] is not None:
                _flag(fields["unit_price"], f"quoted in {currency}")
        kind = fields["kind"]["value"]
        splits = fields["split_deliveries"]["value"] or []
        if splits:
            total = sum(float(s.get("quantity") or 0) for s in splits)
            if (
                fields["quantity"]["value"] is not None
                and abs(total - float(fields["quantity"]["value"])) > 1e-6
            ):
                _flag(fields["split_deliveries"], f"the parts add up to {total:g}, not the quantity")
            last = max(str(s.get("arrival_date")) for s in splits)
            if fields["arrival_date"]["value"] not in (None, last):
                _flag(fields["arrival_date"], f"the last part arrives {last}")
        pct, amount = fields["surcharge_pct"]["value"], fields["surcharge_amount"]["value"]
        # A proposal has one surcharge form: a zero next to the other ("surcharge: none") is dropped.
        if pct is not None and amount is not None and 0 in (float(pct), float(amount)):
            zero = "surcharge_amount" if float(amount) == 0 else "surcharge_pct"
            fields[zero]["value"] = None
            pct, amount = fields["surcharge_pct"]["value"], fields["surcharge_amount"]["value"]
        price, quantity = fields["unit_price"]["value"], fields["quantity"]["value"]
        if None not in (pct, amount, price, quantity):
            expected = float(pct) / 100 * float(price) * float(quantity)
            if abs(expected - float(amount)) > max(1.0, 0.01 * expected):
                _flag(fields["surcharge_amount"], f"{pct}% of the line value is {expected:,.2f}")
        if kind != "decline":
            for name in ("quantity", "unit_price", "arrival_date"):
                if fields[name]["value"] is None and not (name == "arrival_date" and splits):
                    _flag(fields[name], "missing: purchasing fills it in")
    return out


def _check_formats(fields: dict[str, dict[str, Any]], checks: list[str], option_id: int) -> None:
    kind = fields["kind"]
    if kind["value"] is not None and kind["value"] not in KINDS:
        _flag(kind, f"not a known kind: {kind['value']}")
        kind["value"] = None
    if kind["value"] is None:
        _flag(kind, "missing: purchasing chooses the kind")
    for name in NUMBER_FIELDS:
        entry = fields[name]
        if entry["value"] is not None and not isinstance(entry["value"], int | float):
            number = parse_number(entry["value"])
            if number is None:
                checks.append(f"option {option_id} {name}: not a number")
                _flag(entry, f"not a number: {entry['value']}")
            entry["value"] = number
    for name in DATE_FIELDS:
        entry = fields[name]
        if entry["value"] is not None:
            parsed = parse_date(str(entry["value"]))
            if parsed is None:
                checks.append(f"option {option_id} {name}: not a date")
                _flag(entry, f"not a date: {entry['value']}")
            entry["value"] = parsed.isoformat() if parsed else None
    splits = fields["split_deliveries"]
    if splits["value"] is not None:
        valid = []
        for part in splits["value"] if isinstance(splits["value"], list) else []:
            when = parse_date(str(part.get("arrival_date", "")))
            qty = parse_number(part.get("quantity", ""))
            if when is None or qty is None:
                _flag(splits, "a part has no valid quantity or date")
                continue
            valid.append({"quantity": qty, "arrival_date": when.isoformat()})
        splits["value"] = valid or None


def draft_to_options(draft: dict[str, Any]) -> list[dict[str, Any]]:
    """The values of a (confirmed) draft as SupplierProposal dictionaries."""
    options = []
    for option in draft.get("options", []):
        fields = option["fields"]
        row: dict[str, Any] = {"option_id": option["option_id"], "kind": fields["kind"]["value"]}
        for name in (
            "quantity",
            "unit_price",
            "surcharge_pct",
            "surcharge_amount",
            "freight_amount",
            *DATE_FIELDS,
        ):
            if fields[name]["value"] is not None:
                row[name] = fields[name]["value"]
        if fields["split_deliveries"]["value"]:
            row["split_deliveries"] = fields["split_deliveries"]["value"]
        if fields["conditions"]["value"]:
            row["conditions"] = str(fields["conditions"]["value"])[:500]
        options.append(row)
    return options
