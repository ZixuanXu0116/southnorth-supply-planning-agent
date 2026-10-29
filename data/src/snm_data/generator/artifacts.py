"""Data artifacts injected after generation (scenario A6: duplicated EWM replication of one day)."""

from __future__ import annotations

import polars as pl

from .scenarios import A6_DATE, A6_LGNUM

DUPLICATE_TANUM_START = 900_000_000_001


def duplicate_replication_day(ordim_c: pl.DataFrame) -> pl.DataFrame:
    """Append a second copy of every task confirmed at ``A6_LGNUM`` on ``A6_DATE`` (site-local date).

    The copies carry new task numbers from a separate range; every other field repeats the original.
    """
    day = A6_DATE.strftime("%Y%m%d")
    originals = ordim_c.filter(
        (pl.col("lgnum") == A6_LGNUM) & (pl.col("confirmed_at_wh").cast(pl.String).str.slice(0, 8) == day)
    ).sort("tanum")
    copies = originals.with_columns(
        (pl.int_range(pl.len()) + DUPLICATE_TANUM_START).cast(pl.String).str.zfill(12).alias("tanum")
    )
    return pl.concat([ordim_c, copies])
