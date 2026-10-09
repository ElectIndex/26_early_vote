"""The 2024 thought experiment, one congressional district at a time.

`counterfactual.py` asks: if the people who have returned ballots so far had
been the 2024 electorate, and everyone had voted the way their county did in
2024, what margin would the state have produced? This module asks the same
question of each House district, and it is a COMPANION table, not a model of
its own: a district gets a row for exactly the state-days the state got one,
inherits that row's confidence and its band, and is built from the same county
ballots the state row was built from.

WHAT CHANGES AT DISTRICT LEVEL, AND WHAT DOES NOT
-------------------------------------------------
The state row weights counties by their ballots and values each county at its
certified 2024 margin. A district is not made of whole counties -- Wake County
is three districts -- so the unit here is the county x district SEGMENT from
`data/crosswalk/county_cd_weights.csv`: the piece of each county inside each
district, carrying that piece's 2024 presidential vote from the block-level
sums behind the House forecast. A county's early ballots are split across its
segments by `weight` (its 2024 vote share in each), each segment is valued at
its OWN 2024 margin, and the district's composition margin is the
ballot-weighted mean over its segments:

    Σ_s ballots_c(s) · w_s · margin_s  /  Σ_s ballots_c(s) · w_s

Two things follow from that construction and both are deliberate:

* Within one county the split is PROPORTIONAL, so this cannot see a county's
  early voters coming disproportionately from one of its districts. What it
  sees is the mix of COUNTIES feeding each district -- which is the same thing
  the state row sees, one level down, and no more.
* The identity still holds: weight every segment by its own 2024 vote and the
  district's composition margin is its actual 2024 margin. There is a test.

THE BAND IS THE STATE'S. Nothing here has been scored -- there is no district-
level ground truth to score it against, where the state row at least has
party registration -- so a district row carries its state row's own band
half-width, widened to contain zero on an early day exactly as the state's is.
A district cannot be better measured than the state it sits in, and this says
so by construction rather than by inventing a narrower number.

The state row's refusals all carry through, because a district row exists
only where a state row does: no like-for-like 2024 day, no row; partial
geography, no row; stub reference curve, no row. The one refusal added here
is a district none of whose counties has reported, which produces no row for
that district on that day rather than a zero.

Read docs/counterfactual.md before putting any of this on a page. Everything
it says about the state figure applies here with less evidence, not more.
"""

from __future__ import annotations

import csv
import logging
from collections import defaultdict
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Sequence

from . import publish
from .counterfactual import (
    COUNTERFACTUAL_FILENAME, DayIndex, FORBIDDEN_COLUMNS, CounterfactualError,
    _num, _pp, _read_csv, _utcnow, read_series,
)
from .districts import WEIGHTS_PATH

log = logging.getLogger(__name__)

FILENAME = "counterfactual_districts.csv"
METHOD = "pres2024-cd-segment-composition-diff"
SOURCE_NAME = "electindex-counterfactual/pres2024-cd-segments"

COLUMNS = [
    "cycle", "state", "cd_code", "date", "days_to_election",
    "reference_cycle", "reference_date", "reference_days_to_election",
    "implied_margin_2024", "actual_margin_2024", "shift_pp",
    "shift_lo", "shift_hi",
    "composition_margin", "reference_composition_margin",
    # Counties feeding the district on each side, against the number that
    # touch it at all. A district with one of its six counties in is thin.
    "counties_used", "counties_total",
    "reference_counties_used",
    # The district's share of the state's ballots on each side -- apportioned,
    # like everything here -- so a reader can see how much of the state's
    # reading this district is.
    "ballots", "reference_ballots",
    # Carried from the state row: the gate that admitted it and the label.
    "completeness", "reference_completeness",
    "behaviour_cycle", "confidence", "method", "source_name", "retrieved_at",
]

KEY = ("cycle", "state", "cd_code", "date")


@dataclass(frozen=True)
class Segment:
    """One county's piece inside one district, with that piece's 2024 vote."""

    fips: str
    weight: float
    votes_dem: int
    votes_rep: int

    @property
    def two_party(self) -> int:
        return self.votes_dem + self.votes_rep

    @property
    def margin(self) -> float:
        """Two-party, D positive, in points -- `counterfactual.county_margin`'s unit."""
        return (self.votes_dem - self.votes_rep) / self.two_party * 100


Segments = dict[str, dict[str, list[Segment]]]


@lru_cache(maxsize=4)
def _load(path: str) -> Segments:
    out: Segments = defaultdict(lambda: defaultdict(list))
    with Path(path).open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            out[row["state"].upper()][row["cd_code"].upper()].append(Segment(
                fips=row["county_fips"], weight=float(row["weight"]),
                votes_dem=int(row.get("votes_dem") or 0),
                votes_rep=int(row.get("votes_rep") or 0),
            ))
    return {st: dict(cds) for st, cds in out.items()}


def load_segments(path: Path | str | None = None) -> Segments:
    """{state: {cd_code: [Segment, ...]}} from the vendored crosswalk."""
    return _load(str(path or WEIGHTS_PATH))


def district_actual(segments: Sequence[Segment]) -> float | None:
    """The district's 2024 two-party margin, from its segments' own votes."""
    dem = sum(s.votes_dem for s in segments)
    rep = sum(s.votes_rep for s in segments)
    if dem + rep <= 0:
        return None
    return (dem - rep) / (dem + rep) * 100


def district_composition(
    ballots: dict[str, int], segments: Sequence[Segment]
) -> tuple[float, int, float] | None:
    """Σ ballots·w·margin / Σ ballots·w over the segments whose county reported.

    Returns (margin_points, counties_used, ballots_used) or None when no
    county of the district has reported -- no row, never a zero. A county
    reporting a genuine 0 is used and contributes no weight, exactly as it
    does in `counterfactual.composition_margin`.
    """
    numerator = 0.0
    total = 0.0
    used: set[str] = set()
    for seg in segments:
        count = ballots.get(seg.fips)
        if count is None or count < 0 or seg.two_party <= 0:
            continue
        w = count * seg.weight
        numerator += w * seg.margin
        total += w
        used.add(seg.fips)
    if not used or total <= 0:
        return None
    return numerator / total, len(used), total


def compare_district(
    state_row: dict[str, str], now: DayIndex, reference: DayIndex,
    segments: Segments, *, retrieved_at: str | None = None,
) -> list[dict[str, str]]:
    """Every district of one state on one state-day that has a state row.

    `state_row` is a published `counterfactual.csv` row (or `to_dict()` of a
    Counterfactual); its days, confidence and band are the authority here.
    """
    state = (state_row.get("state") or "").upper()
    dte = _num(state_row.get("days_to_election"))
    ref_dte = _num(state_row.get("reference_days_to_election"))
    lo_s, hi_s = _float(state_row.get("shift_lo")), _float(state_row.get("shift_hi"))
    shift_s = _float(state_row.get("shift_pp"))
    if dte is None or ref_dte is None or lo_s is None or hi_s is None or shift_s is None:
        return []
    half = max(hi_s - shift_s, shift_s - lo_s)
    early = (state_row.get("confidence") or "").strip() == "early"
    now_ballots = now.counties.get(dte, {})
    ref_ballots = reference.counties.get(ref_dte, {})
    stamp = retrieved_at or state_row.get("retrieved_at") or _utcnow()

    out: list[dict[str, str]] = []
    for cd, segs in sorted(segments.get(state, {}).items()):
        here = district_composition(now_ballots, segs)
        there = district_composition(ref_ballots, segs)
        actual = district_actual(segs)
        if here is None or there is None or actual is None:
            continue
        margin, used, ballots = here
        ref_margin, ref_used, ref_ballots_n = there
        shift = margin - ref_margin
        lo, hi = shift - half, shift + half
        if early:
            # The floor rule the state row follows: an early band always
            # contains "nothing changed".
            lo, hi = min(lo, 0.0), max(hi, 0.0)
        out.append({
            "cycle": state_row["cycle"], "state": state, "cd_code": cd,
            "date": state_row["date"], "days_to_election": str(dte),
            "reference_cycle": state_row.get("reference_cycle", ""),
            "reference_date": state_row.get("reference_date", ""),
            "reference_days_to_election": str(ref_dte),
            "implied_margin_2024": _pp(actual + shift),
            "actual_margin_2024": _pp(actual),
            "shift_pp": _pp(shift), "shift_lo": _pp(lo), "shift_hi": _pp(hi),
            "composition_margin": _pp(margin),
            "reference_composition_margin": _pp(ref_margin),
            "counties_used": str(used),
            "counties_total": str(len({s.fips for s in segs})),
            "reference_counties_used": str(ref_used),
            "ballots": str(int(round(ballots))),
            "reference_ballots": str(int(round(ref_ballots_n))),
            "completeness": state_row.get("completeness", ""),
            "reference_completeness": state_row.get("reference_completeness", ""),
            "behaviour_cycle": state_row.get("behaviour_cycle", ""),
            "confidence": state_row.get("confidence", ""),
            "method": METHOD, "source_name": SOURCE_NAME, "retrieved_at": stamp,
        })
    return out


def _float(cell: str | None) -> float | None:
    cell = (cell or "").strip()
    try:
        return float(cell) if cell else None
    except ValueError:
        return None


def build(
    out_dir: Path, state_rows: Iterable[dict[str, str]] | None = None, *,
    states: Iterable[str] | None = None, segments: Segments | None = None,
    retrieved_at: str | None = None,
) -> list[dict[str, str]]:
    """District rows for every state row given (default: the published table)."""
    out_dir = Path(out_dir)
    segments = segments or load_segments()
    wanted = {s.upper() for s in states} if states else None
    if state_rows is None:
        state_rows = _read_csv(out_dir / COUNTERFACTUAL_FILENAME)
    by_state: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in state_rows:
        st = (row.get("state") or "").upper()
        if st and (not wanted or st in wanted):
            by_state[st].append(row)

    out: list[dict[str, str]] = []
    for state in sorted(by_state):
        if state not in segments:
            log.info("%s: no district segments; no district counterfactual", state)
            continue
        series = read_series(out_dir, state)
        for row in by_state[state]:
            cycle, ref_cycle = _num(row.get("cycle")), _num(row.get("reference_cycle"))
            now = series.get(cycle) if cycle is not None else None
            reference = series.get(ref_cycle) if ref_cycle is not None else None
            if now is None or reference is None:
                continue
            out.extend(compare_district(row, now, reference, segments,
                                        retrieved_at=retrieved_at))
    return out


def write(out_dir: Path, rows: Sequence[dict[str, str]], *, rebuild: bool = False) -> dict:
    """Publish to output/counterfactual_districts.csv and nowhere else."""
    forbidden = FORBIDDEN_COLUMNS & set(COLUMNS)
    if forbidden:
        raise CounterfactualError(
            f"{FILENAME} must never carry reported-party columns: {sorted(forbidden)}")
    return publish.publish_table(
        Path(out_dir) / FILENAME, COLUMNS, KEY, list(rows),
        guard=False, replace=rebuild,
    )
