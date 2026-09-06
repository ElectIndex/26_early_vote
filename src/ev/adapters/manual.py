"""Tier 3: hand-entered statewide totals.

The floor of every ladder. When a state publishes only a PDF, or a press release,
or nothing machine-readable at all, a human types the number into
data/manual/<cycle>_statewide.csv and it appears on the site with a "manual"
provenance badge. Deliberately dumb -- no network, no parsing, no surprises.

    cycle,state,date,ballots_total,mail_returned,inperson,mail_requested,note

Blank cells stay blank (see THE BLANK RULE in schema.py); do not type 0 to mean
"not reported".
"""

from __future__ import annotations

import csv
from datetime import date, datetime
from pathlib import Path

from ..schema import TIER_MANUAL, StateDay
from .base import Adapter, FetchResult, NotYetPublished, SchemaDrift

REQUIRED_COLUMNS = {"cycle", "state", "date", "ballots_total"}
OPTIONAL_INT_COLUMNS = ("mail_returned", "inperson", "mail_requested",
                        "party_dem", "party_rep", "party_oth", "party_npa")

DATA_DIR = Path(__file__).resolve().parents[3] / "data" / "manual"


def _int(raw: str | None) -> int | None:
    if raw is None:
        return None
    text = raw.strip().replace(",", "")
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError as exc:
        raise SchemaDrift(f"manual file: {raw!r} is not a number") from exc


class ManualAdapter(Adapter):
    name = "manual"
    tier = TIER_MANUAL

    def __init__(self, state: str | None = None, data_dir: Path | None = None) -> None:
        super().__init__(state)
        self.data_dir = data_dir or DATA_DIR

    def _path(self, cycle: int) -> Path:
        return self.data_dir / f"{cycle}_statewide.csv"

    def fetch(self, cycle: int, as_of: date) -> FetchResult:
        path = self._path(cycle)
        if not path.exists():
            raise NotYetPublished(f"no manual file at {path}")

        with path.open(newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            missing = REQUIRED_COLUMNS - set(reader.fieldnames or [])
            if missing:
                raise SchemaDrift(f"{path.name} is missing columns: {sorted(missing)}")
            rows = [r for r in reader
                    if (r.get("state") or "").strip().upper() == self.state
                    and (r.get("cycle") or "").strip() == str(cycle)]

        if not rows:
            raise NotYetPublished(f"no manual rows for {self.state} in {cycle}")

        out: list[StateDay] = []
        for row in rows:
            try:
                day = datetime.strptime(row["date"].strip(), "%Y-%m-%d").date()
            except ValueError as exc:
                raise SchemaDrift(f"{path.name}: bad date {row['date']!r}") from exc
            if day > as_of:
                continue  # a future-dated hand entry is not published yet
            out.append(StateDay(
                cycle=cycle, state=self.state, day=day,
                ballots_total=_int(row.get("ballots_total")),
                **{col: _int(row.get(col)) for col in OPTIONAL_INT_COLUMNS},
            ))

        if not out:
            raise NotYetPublished(f"no manual rows for {self.state} on or before {as_of}")
        return FetchResult(state_rows=out)

    def fetch_history(self, cycle: int) -> FetchResult:
        """Hand-entered history is just the same file for a past cycle."""
        return self.fetch(cycle, date(cycle, 12, 31))
