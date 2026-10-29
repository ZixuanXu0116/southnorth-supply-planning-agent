"""Move confirmed picks later without breaking goods issue or wave completion.

Scenario and trap generators (AutoStore A1 queues, late RFID-era confirmations) shift pick confirmations
after the outbound flow was generated. Each shift is capped by the delivery's slack: every moved pick stays at
least ``GI_STAGING_MIN`` minutes before the delivery's first goods issue (or before the EWM extraction for
deliveries not yet shipped), so ECC documents stay valid.
"""

from __future__ import annotations

import numpy as np
import polars as pl

from .timeutil import MINUTE, minutes

GI_STAGING_MIN = 20.0


def goods_issue_slack(
    affected: pl.DataFrame, movable: pl.DataFrame, deliveries: pl.DataFrame, ewm_as_of: np.datetime64
) -> pl.DataFrame:
    """``affected`` (``row``, ``delivery_id``) sorted by ``row``, with the allowed shift in ``slack_min``.

    ``movable`` holds (``delivery_id``, ``confirmed_utc``) of every pick of the group that may move; the
    slack is measured from the group's last pick, so no moved pick can pass the limit.
    """
    last_pick = movable.group_by("delivery_id").agg(pl.col("confirmed_utc").max().alias("last_pick"))
    affected = (
        affected.join(last_pick, on="delivery_id")
        .join(deliveries.select("delivery_id", "pgi_utc"), on="delivery_id", how="left")
        .sort("row")  # joins do not keep row order; draws must follow a stable order
    )
    pgi = affected.get_column("pgi_utc").to_numpy()
    last = affected.get_column("last_pick").to_numpy()
    limit = np.where(np.isnat(pgi), ewm_as_of - MINUTE, pgi - minutes(GI_STAGING_MIN))
    return affected.with_columns(pl.Series("slack_min", np.maximum((limit - last) / MINUTE, 0.0)))


def advance_wave_completion(
    ordim_c: pl.DataFrame, wavehdr: pl.DataFrame, moved: pl.DataFrame
) -> pl.DataFrame:
    """Recompute ``PICK_COMP_DT`` of the waves of the moved tasks (``row``) from their confirmed tasks."""
    waves = ordim_c.join(moved.select("row"), on="row", how="semi").select("lgnum", "wave").unique()
    completion = (
        ordim_c.filter(pl.col("tostat") == "C")
        .join(waves, on=["lgnum", "wave"], how="semi")
        .group_by("lgnum", "wave")
        .agg(pl.col("confirmed_at").max().alias("new_pick_comp_dt"))
    )
    return (
        wavehdr.join(completion, on=["lgnum", "wave"], how="left")
        .with_columns(
            pl.when((pl.col("pick_comp_dt") > 0) & pl.col("new_pick_comp_dt").is_not_null())
            .then(pl.col("new_pick_comp_dt"))
            .otherwise(pl.col("pick_comp_dt"))
            .alias("pick_comp_dt")
        )
        .drop("new_pick_comp_dt")
        .sort(["lgnum", "wave"])
    )
