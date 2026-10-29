"""Time-zone conversion and SAP-style formatting helpers (vectorized)."""

from __future__ import annotations

import numpy as np
import polars as pl

US = np.timedelta64(1, "us")
MINUTE = np.timedelta64(60_000_000, "us")
HOUR = 60 * MINUTE
DAY = 24 * HOUR


def minutes(values: np.ndarray | float) -> np.ndarray:
    """Convert (fractional) minutes to timedelta64[us]."""
    return (np.asarray(values, dtype=float) * 60_000_000).astype("int64").astype("timedelta64[us]")


def _series(values: np.ndarray) -> pl.Series:
    return pl.Series(values.astype("datetime64[us]"), dtype=pl.Datetime("us"))


def local_to_utc(local: np.ndarray, tz: str) -> np.ndarray:
    """Naive site-local datetime64[us] -> naive UTC datetime64[us]."""
    series = _series(local).dt.replace_time_zone(tz, ambiguous="earliest", non_existent="raise")
    return series.dt.convert_time_zone("UTC").dt.replace_time_zone(None).to_numpy()


def utc_to_local(utc: np.ndarray, tz: str) -> np.ndarray:
    """Naive UTC datetime64[us] -> naive site-local datetime64[us]."""
    series = _series(utc).dt.replace_time_zone("UTC")
    return series.dt.convert_time_zone(tz).dt.replace_time_zone(None).to_numpy()


def dec15(values: np.ndarray) -> pl.Series:
    """DEC15 timestamps (YYYYMMDDhhmmss) as Int64; NaT becomes 0 (SAP initial value)."""
    series = _series(values).dt.strftime("%Y%m%d%H%M%S").cast(pl.Int64)
    return series.fill_null(0)


def dats(values: np.ndarray) -> pl.Series:
    """DATS strings (YYYYMMDD); NaT becomes '00000000'."""
    return _series(values).dt.strftime("%Y%m%d").fill_null("00000000")


def tims(values: np.ndarray) -> pl.Series:
    """TIMS strings (HHMMSS); NaT becomes '000000'."""
    return _series(values).dt.strftime("%H%M%S").fill_null("000000")


def zfill(numbers: np.ndarray, width: int) -> pl.Series:
    return pl.Series(np.asarray(numbers, dtype="int64")).cast(pl.String).str.zfill(width)


def guids(rng: np.random.Generator, count: int) -> list[str]:
    """RAW16 GUIDs rendered as 32 uppercase hexadecimal characters."""
    raw = rng.bytes(16 * count)
    return [raw[offset : offset + 16].hex().upper() for offset in range(0, 16 * count, 16)]
