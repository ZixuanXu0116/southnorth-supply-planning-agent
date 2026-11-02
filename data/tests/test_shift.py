"""Moving a planning run to another day (snm-data shift-dates)."""

from __future__ import annotations

from datetime import date

import polars as pl

from snm_data.planning.shift import shift_frame, shift_text


def test_text_dates_and_case_ids_move():
    assert shift_text("arrives 2026-10-12, by 2026-11-24", 42) == "arrives 2026-11-23, by 2027-01-05"
    assert shift_text("C-US10-40000410-SH-20261124", 42) == "C-US10-40000410-SH-20270105"
    assert shift_text("C-US10-40000410-SH-20261124-2", 1) == "C-US10-40000410-SH-20261125-2"
    assert (
        shift_text('[{"coverage_date": "2026-09-27", "qty": 5}]', 3)
        == '[{"coverage_date": "2026-09-30", "qty": 5}]'
    )
    assert shift_text("material 40000410, 20 pieces", 42) == "material 40000410, 20 pieces"
    assert shift_text(None, 5) is None


def test_date_columns_move_and_other_columns_stay():
    frame = pl.DataFrame(
        {
            "as_of_date": [date(2026, 9, 21)],
            "trigger_date": [date(2026, 11, 24)],
            "case_id": ["C-US10-40000410-SH-20261124"],
            "gap_qty": [5.0],
            "material": ["40000410"],
        }
    )
    moved = shift_frame(frame, 42).row(0, named=True)
    assert moved["as_of_date"] == date(2026, 11, 2) and moved["trigger_date"] == date(2027, 1, 5)
    assert moved["case_id"] == "C-US10-40000410-SH-20270105"
    assert moved["gap_qty"] == 5.0 and moved["material"] == "40000410"
