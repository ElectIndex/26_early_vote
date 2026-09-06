"""Canonical record shapes and the CSV column contract.

Four long-format tables, all keyed by `cycle` so 2022/2024/2026 live in the same
shape and the frontend pivots instead of joining.

THE BLANK RULE: a count of `None` writes an empty cell and means "the state did
not report this". It is NOT zero. Georgia reports no party registration because
Georgia has no party registration; writing 0 there would render as "zero Democrats
have voted". Every consumer must treat blank and 0 as different, so every
producer must too -- adapters return None, never 0, for an unreported field.
"""

from __future__ import annotations

import csv
import dataclasses
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator, Sequence

from .calendar import days_to_election

# --------------------------------------------------------------------------
# Provenance tiers. Lower is better; publish.py keeps the lowest per key.
# --------------------------------------------------------------------------
TIER_SCRAPER = 1     # our own adapter against the state's own file
TIER_CIVIC = 2       # civicAPI's national early-vote API (statewide + counties)
TIER_AGGREGATOR = 3  # a third-party aggregator (statewide only)
TIER_MANUAL = 4      # hand-entered statewide total

TIER_LABELS = {
    TIER_SCRAPER: "state",
    TIER_CIVIC: "civicapi",
    TIER_AGGREGATOR: "aggregator",
    TIER_MANUAL: "manual",
}


def _utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class Provenance:
    """Where a row came from. Carried on every row, never inferred later."""

    tier: int
    name: str
    retrieved_at: str = field(default_factory=_utcnow)

    def __post_init__(self) -> None:
        if self.tier not in TIER_LABELS:
            raise ValueError(f"unknown source tier {self.tier!r}")


@dataclass
class StateDay:
    """One state's cumulative early-vote position on one day."""

    cycle: int
    state: str
    day: date
    ballots_total: int | None = None
    ballots_new: int | None = None
    mail_requested: int | None = None
    mail_returned: int | None = None
    inperson: int | None = None
    party_dem: int | None = None
    party_rep: int | None = None
    party_oth: int | None = None
    party_npa: int | None = None
    restated: int = 0
    provenance: Provenance | None = None

    KEY: tuple[str, ...] = ()

    def key(self) -> tuple:
        return (int(self.cycle), self.state.upper(), self.day.isoformat())


@dataclass
class CountyDay:
    """One county's cumulative position on one day."""

    cycle: int
    state: str
    county_fips: str
    day: date
    county_name: str = ""
    ballots_total: int | None = None
    ballots_new: int | None = None
    mail_returned: int | None = None
    inperson: int | None = None
    party_dem: int | None = None
    party_rep: int | None = None
    party_oth: int | None = None
    party_npa: int | None = None
    provenance: Provenance | None = None

    def key(self) -> tuple:
        return (int(self.cycle), self.state.upper(), self.county_fips, self.day.isoformat())


@dataclass
class TownDay:
    """One municipality's cumulative position on one day.

    New England runs elections by TOWN -- Maine's absentee file is keyed by
    municipality and carries no county at all -- and the strong-MCD states
    (Michigan, Wisconsin, Minnesota, Pennsylvania and the rest) report by
    township or jurisdiction alongside their counties. This is that unit.

    `town_geoid` is the 10-digit Census county-subdivision GEOID, never a name:
    "Lincoln" is two different Maine places in two different counties, and
    Wisconsin has 420 names that are simultaneously a town and a city or
    village. Use `adapters._towns.lookup` to get one.

    The county is NOT a separate input. A cousub GEOID is state(2) + county(3)
    + cousub(5), so `county_fips` is a slice of the key and is derived at write
    time -- which is what makes a town->county rollup exact rather than an
    inferred crosswalk, and makes it impossible to file a town under the wrong
    county.
    """

    cycle: int
    state: str
    town_geoid: str
    day: date
    town_name: str = ""
    ballots_total: int | None = None
    ballots_new: int | None = None
    mail_returned: int | None = None
    inperson: int | None = None
    party_dem: int | None = None
    party_rep: int | None = None
    party_oth: int | None = None
    party_npa: int | None = None
    provenance: Provenance | None = None

    def __post_init__(self) -> None:
        geoid = str(self.town_geoid).strip()
        if len(geoid) != 10 or not geoid.isdigit():
            raise ValueError(
                f"TownDay needs a 10-digit county-subdivision GEOID, got "
                f"{self.town_geoid!r} -- towns are keyed by GEOID, never by name"
            )
        self.town_geoid = geoid

    @property
    def county_fips(self) -> str:
        """The county this town is in, read straight out of its GEOID."""
        return self.town_geoid[:5]

    def key(self) -> tuple:
        return (int(self.cycle), self.state.upper(), self.town_geoid, self.day.isoformat())


@dataclass
class DemoDay:
    """Ballots cast within one demographic bucket on one day.

    `dimension` is one of DEMO_DIMENSIONS; `bucket` is an already-normalized label
    from normalize.py (adapters must not invent their own spellings).
    """

    cycle: int
    state: str
    day: date
    dimension: str
    bucket: str
    ballots_total: int | None = None
    provenance: Provenance | None = None

    def key(self) -> tuple:
        return (
            int(self.cycle),
            self.state.upper(),
            self.day.isoformat(),
            self.dimension,
            self.bucket,
        )


DEMO_DIMENSIONS = ("age", "race", "sex")

STATE_DAILY_COLUMNS = [
    "cycle", "state", "date", "days_to_election",
    "ballots_total", "ballots_new",
    "mail_requested", "mail_returned", "inperson",
    "party_dem", "party_rep", "party_oth", "party_npa",
    "restated", "source_tier", "source_name", "retrieved_at",
]

COUNTY_DAILY_COLUMNS = [
    "cycle", "state", "county_fips", "county_name", "date", "days_to_election",
    "ballots_total", "ballots_new", "mail_returned", "inperson",
    "party_dem", "party_rep", "party_oth", "party_npa",
    "source_tier", "source_name", "retrieved_at",
]

TOWN_DAILY_COLUMNS = [
    "cycle", "state", "town_geoid", "town_name", "county_fips",
    "date", "days_to_election",
    "ballots_total", "ballots_new", "mail_returned", "inperson",
    "party_dem", "party_rep", "party_oth", "party_npa",
    "source_tier", "source_name", "retrieved_at",
]

DEMO_DAILY_COLUMNS = [
    "cycle", "state", "date", "days_to_election",
    "dimension", "bucket", "ballots_total",
    "source_tier", "source_name", "retrieved_at",
]

# Key columns per table, used by publish.py to merge without re-deriving them.
STATE_KEY = ("cycle", "state", "date")
COUNTY_KEY = ("cycle", "state", "county_fips", "date")
TOWN_KEY = ("cycle", "state", "town_geoid", "date")
DEMO_KEY = ("cycle", "state", "date", "dimension", "bucket")


def _cell(value: int | None) -> str:
    """None -> empty cell. See THE BLANK RULE above."""
    return "" if value is None else str(int(value))


def _prov(row) -> Provenance:
    if row.provenance is None:
        raise ValueError(f"{type(row).__name__} written without provenance: {row.key()}")
    return row.provenance


def state_row_to_dict(row: StateDay) -> dict[str, str]:
    p = _prov(row)
    return {
        "cycle": str(int(row.cycle)),
        "state": row.state.upper(),
        "date": row.day.isoformat(),
        "days_to_election": str(days_to_election(row.cycle, row.day)),
        "ballots_total": _cell(row.ballots_total),
        "ballots_new": _cell(row.ballots_new),
        "mail_requested": _cell(row.mail_requested),
        "mail_returned": _cell(row.mail_returned),
        "inperson": _cell(row.inperson),
        "party_dem": _cell(row.party_dem),
        "party_rep": _cell(row.party_rep),
        "party_oth": _cell(row.party_oth),
        "party_npa": _cell(row.party_npa),
        "restated": str(int(row.restated)),
        "source_tier": str(p.tier),
        "source_name": p.name,
        "retrieved_at": p.retrieved_at,
    }


def county_row_to_dict(row: CountyDay) -> dict[str, str]:
    p = _prov(row)
    return {
        "cycle": str(int(row.cycle)),
        "state": row.state.upper(),
        "county_fips": row.county_fips,
        "county_name": row.county_name,
        "date": row.day.isoformat(),
        "days_to_election": str(days_to_election(row.cycle, row.day)),
        "ballots_total": _cell(row.ballots_total),
        "ballots_new": _cell(row.ballots_new),
        "mail_returned": _cell(row.mail_returned),
        "inperson": _cell(row.inperson),
        "party_dem": _cell(row.party_dem),
        "party_rep": _cell(row.party_rep),
        "party_oth": _cell(row.party_oth),
        "party_npa": _cell(row.party_npa),
        "source_tier": str(p.tier),
        "source_name": p.name,
        "retrieved_at": p.retrieved_at,
    }


def town_row_to_dict(row: TownDay) -> dict[str, str]:
    p = _prov(row)
    return {
        "cycle": str(int(row.cycle)),
        "state": row.state.upper(),
        "town_geoid": row.town_geoid,
        "town_name": row.town_name,
        # Derived from the GEOID, never taken from the caller. See TownDay.
        "county_fips": row.county_fips,
        "date": row.day.isoformat(),
        "days_to_election": str(days_to_election(row.cycle, row.day)),
        "ballots_total": _cell(row.ballots_total),
        "ballots_new": _cell(row.ballots_new),
        "mail_returned": _cell(row.mail_returned),
        "inperson": _cell(row.inperson),
        "party_dem": _cell(row.party_dem),
        "party_rep": _cell(row.party_rep),
        "party_oth": _cell(row.party_oth),
        "party_npa": _cell(row.party_npa),
        "source_tier": str(p.tier),
        "source_name": p.name,
        "retrieved_at": p.retrieved_at,
    }


def demo_row_to_dict(row: DemoDay) -> dict[str, str]:
    p = _prov(row)
    if row.dimension not in DEMO_DIMENSIONS:
        raise ValueError(f"unknown demographic dimension {row.dimension!r}")
    return {
        "cycle": str(int(row.cycle)),
        "state": row.state.upper(),
        "date": row.day.isoformat(),
        "days_to_election": str(days_to_election(row.cycle, row.day)),
        "dimension": row.dimension,
        "bucket": row.bucket,
        "ballots_total": _cell(row.ballots_total),
        "source_tier": str(p.tier),
        "source_name": p.name,
        "retrieved_at": p.retrieved_at,
    }
