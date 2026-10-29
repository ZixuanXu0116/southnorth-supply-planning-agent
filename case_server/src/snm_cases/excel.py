"""The request sheet sent to a supplier and the parser of the sheet it returns (decision record 005).

One worksheet: a header block with the request's outward fields (never anything internal), then a table with
one row per option for the supplier to fill. The parser reads the table back into the proposal schema
(``model.SupplierProposal``) and reports every problem per cell instead of guessing; a sheet for another
request is refused. Nothing in a returned sheet is executed: formulas are read as their cached values only.
"""

from __future__ import annotations

import io
import re
from datetime import date, datetime
from typing import Any

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation
from pydantic import ValidationError

from . import model

SHEET = "Request"
OPTION_ROWS = 5
HEADER = (
    ("Company", "SouthNorth Solutions"),
    ("Request", "case_id"),
    ("Request type", "request_type"),
    ("Material", "material"),
    ("Description", "description"),
    ("Quantity (pieces)", "quantity"),
    ("Needed at plant by", "needed_date"),
    ("Ship to plant", "ship_to_plant"),
    ("Purchase order", "po_number"),
    ("Purchase order item", "po_item"),
)
COLUMNS = (
    ("option", "Option"),
    ("kind", "Kind (expedite, new_order, partial, alternative_part, decline)"),
    ("quantity", "Quantity"),
    ("ship_date", "Ship date (YYYY-MM-DD)"),
    ("arrival_date", "Arrival date (YYYY-MM-DD)"),
    ("unit_price", "Unit price"),
    ("surcharge_pct", "Surcharge %"),
    ("surcharge_amount", "Surcharge amount"),
    ("freight_amount", "Freight amount"),
    ("valid_until", "Offer valid until (YYYY-MM-DD)"),
    ("split_deliveries", "Split deliveries (qty@YYYY-MM-DD; ...)"),
    ("conditions", "Conditions (500 characters at most)"),
)
TABLE_ROW = len(HEADER) + 3  # the column titles; options start on the next row
KINDS = ("expedite", "new_order", "partial", "alternative_part", "decline")
_SPLIT = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*@\s*(\d{4}-\d{2}-\d{2})\s*$")


def request_sheet(outward: model.OutwardFields | dict[str, Any], request_text: str) -> bytes:
    """The .xlsx a supplier downloads with the request: the outward fields and an empty option table."""
    fields = outward.model_dump() if isinstance(outward, model.OutwardFields) else dict(outward)
    book = Workbook()
    sheet = book.active
    sheet.title = SHEET
    bold = Font(bold=True)
    for row, (label, key) in enumerate(HEADER, start=1):
        sheet.cell(row=row, column=1, value=label).font = bold
        value = "SouthNorth Solutions" if key == "SouthNorth Solutions" else fields.get(key)
        if isinstance(value, date):
            value = value.isoformat()
        sheet.cell(row=row, column=2, value="" if value is None else value)
    note = sheet.cell(row=len(HEADER) + 1, column=1, value=request_text)
    note.alignment = Alignment(wrap_text=True, vertical="top")
    sheet.merge_cells(
        start_row=len(HEADER) + 1, start_column=1, end_row=len(HEADER) + 1, end_column=len(COLUMNS)
    )
    sheet.row_dimensions[len(HEADER) + 1].height = 60
    fill = PatternFill("solid", fgColor="DDEBF7")
    for column, (_, title) in enumerate(COLUMNS, start=1):
        cell = sheet.cell(row=TABLE_ROW, column=column, value=title)
        cell.font = bold
        cell.fill = fill
        cell.alignment = Alignment(wrap_text=True)
        sheet.column_dimensions[cell.column_letter].width = 18 if column > 1 else 22
    for option in range(1, OPTION_ROWS + 1):
        sheet.cell(row=TABLE_ROW + option, column=1, value=option)
    kinds = DataValidation(type="list", formula1='"' + ",".join(KINDS) + '"', allow_blank=True)
    sheet.add_data_validation(kinds)
    kinds.add(f"B{TABLE_ROW + 1}:B{TABLE_ROW + OPTION_ROWS}")
    sheet.freeze_panes = sheet.cell(row=TABLE_ROW + 1, column=1)
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def _date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value).strip()[:10])


def _number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, int | float):
        return float(value)
    return float(str(value).strip().replace(",", "").rstrip("%"))


def _splits(value: Any) -> list[dict[str, Any]]:
    if value is None or str(value).strip() == "":
        return []
    out = []
    for part in str(value).split(";"):
        if not part.strip():
            continue
        match = _SPLIT.match(part)
        if match is None:
            raise ValueError(f"'{part.strip()}' is not quantity@YYYY-MM-DD")
        out.append({"quantity": float(match.group(1)), "arrival_date": match.group(2)})
    return out


def _explain(error: ValidationError) -> str:
    """The first problem in the supplier's words: the column and what is wrong with it."""
    first = error.errors()[0]
    column = ".".join(str(part) for part in first.get("loc", ()) if part != "__root__")
    message = str(first.get("msg", "invalid")).removeprefix("Value error, ")
    return f"{column}: {message}" if column else message


def parse_reply(content: bytes, case_id: str) -> tuple[list[dict[str, Any]], list[str]]:
    """Options from a returned sheet, as ``SupplierProposal`` dictionaries, and the problems found (cell and
    message). Rows with no value besides the option number are skipped; nothing is returned when the sheet
    belongs to another request or cannot be read."""
    try:
        book = load_workbook(io.BytesIO(content), data_only=True, read_only=True)
    except Exception:
        return [], ["The file is not an .xlsx workbook."]
    if SHEET not in book.sheetnames:
        return [], [f"The workbook has no sheet named '{SHEET}'; use the sheet attached to the request."]
    sheet = book[SHEET]
    rows = list(sheet.iter_rows(values_only=True))
    labels = {str(row[0]).strip(): row[1] for row in rows[: len(HEADER)] if row and row[0] is not None}
    if str(labels.get("Request", "")).strip() != case_id:
        return [], [f"This sheet belongs to request {labels.get('Request')!s}, not {case_id}."]
    options, problems = [], []
    keys = [key for key, _ in COLUMNS]
    for offset, row in enumerate(rows[TABLE_ROW : TABLE_ROW + OPTION_ROWS + 20], start=TABLE_ROW + 1):
        values = dict(zip(keys, list(row) + [None] * (len(keys) - len(row)), strict=False))
        if all(values[key] in (None, "") for key in keys[1:]):
            continue
        option: dict[str, Any] = {}
        try:
            option["option_id"] = int(_number(values["option"]) or len(options) + 1)
            option["kind"] = str(values["kind"] or "").strip()
            if option["kind"] not in KINDS:
                raise ValueError(f"kind '{option['kind']}' is not one of {', '.join(KINDS)}")
            for key in ("quantity", "unit_price", "surcharge_pct", "surcharge_amount", "freight_amount"):
                try:
                    option[key] = _number(values[key])
                except ValueError:
                    raise ValueError(f"{key} '{values[key]}' is not a number") from None
            for key in ("ship_date", "arrival_date", "valid_until"):
                try:
                    parsed = _date(values[key])
                except ValueError:
                    raise ValueError(f"{key} '{values[key]}' is not a date (YYYY-MM-DD)") from None
                option[key] = parsed.isoformat() if parsed else None
            option["split_deliveries"] = _splits(values["split_deliveries"])
            option["conditions"] = "" if values["conditions"] is None else str(values["conditions"]).strip()
            model.SupplierProposal.model_validate(option)
        except ValidationError as error:
            problems.append(f"Row {offset}: {_explain(error)}")
            continue
        except ValueError as error:
            problems.append(f"Row {offset}: {error}")
            continue
        options.append({key: value for key, value in option.items() if value not in (None, [], "")} | {
            "option_id": option["option_id"], "kind": option["kind"],
        })  # fmt: skip
    if not options and not problems:
        problems.append("The option table is empty: fill at least one row.")
    return options, problems
