"""Generation profiles."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime


@dataclass(frozen=True)
class Profile:
    name: str
    start: date
    ewm_as_of: datetime  # hourly EWM replication: events up to this instant are extracted
    ecc_as_of: datetime  # nightly ECC extraction: events up to this instant are extracted
    scale: float  # multiplier on full-scale daily volumes
    n_materials: int
    n_customers: int
    min_operators_per_site: int
    seed: int = 20260924


# Nightly ECC extraction at 00:00 America/Chicago (05:00 UTC in September);
# EWM is replicated hourly, so it is ~9 hours fresher than ECC at extraction time.
_ECC_AS_OF = datetime(2026, 9, 21, 5, 0, tzinfo=UTC)
_EWM_AS_OF = datetime(2026, 9, 21, 14, 0, tzinfo=UTC)
_FY25_START = date(2024, 10, 28)

PROFILES: dict[str, Profile] = {
    "test": Profile("test", date(2026, 8, 31), _EWM_AS_OF, _ECC_AS_OF, 0.01, 150, 20, 8),
    "small": Profile("small", _FY25_START, _EWM_AS_OF, _ECC_AS_OF, 0.02, 600, 40, 12),
    "full": Profile("full", _FY25_START, _EWM_AS_OF, _ECC_AS_OF, 1.0, 12000, 180, 0),
}

# Full-scale outbound delivery lines per working day, before seasonality.
BASE_LINES_PER_DAY = {"RR": 600, "SZ": 5000, "DG": 2000}
