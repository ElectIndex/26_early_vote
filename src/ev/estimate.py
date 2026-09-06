"""A MODELLED party split for states that publish none — kept in its own table.

WHY THIS FILE IS DANGEROUS
--------------------------
Nine of the states we track (GA, IL, MI, OH, SC, TN, TX, VA, WI) do not register
voters by party at all, so no party breakdown of their early ballots exists
anywhere. Arizona is a tenth case and a different one: Arizona DOES register by
party, its Secretary of State's daily table simply omits it. For all ten the
tracker can say how many ballots came back but not who returned them.

This module estimates the partisan composition of those returns from the only
geography we have -- county-level early-vote counts -- weighted by each county's
2024 presidential two-party vote. That is a model output, and a model output that
looks like a count WILL be quoted as a count. So:

  * It is published to `output/party_estimate.csv` and NOWHERE else. It must
    never be written into `party_dem`/`party_rep`/`party_npa`/`party_oth` in
    `ev_state_daily.csv` or the county files. Those columns mean "the state
    reported this", and that guarantee is the thing the whole pipeline rests on.
    `write()` below can only address `party_estimate.csv`; there is a test.

  * Every row carries its own uncertainty band and its own coverage share. An
    estimate covering 30% of a state's ballots is a different object from one
    covering 95%, and the CSV has to say which it is.

  * A state with no usable county data produces NO ROW. Not a 50/50 default,
    not a zero. The same instinct as THE BLANK RULE in schema.py: absence of
    data is not a value.

WHAT IT MEASURES, AND WHAT IT DOES NOT
--------------------------------------
`est_dem_share` is the 2024 presidential two-party Democratic share OF THE PLACES
whose ballots are in so far. It is a statement about geography, not about voters:
"if everyone who has voted early so far voted exactly the way their county did in
2024, the early vote would be X% Democratic."

It is NOT "X% of early ballots were cast by Democrats". Measured against the six
states that do report party registration, that reading is wrong by a mean of
5.5 points and by as much as 12 (Pennsylvania 2024). Worse, because every state
we track publishes every one of its counties, the county weights are close to
proportional to county size, so the estimate is arithmetically pinned within
about two points of the state's own 2024 result -- it moves 0.2 to 1.7 points
across an entire early-vote window while the real party mix of the early
electorate moves twenty or thirty. The full numbers are in
docs/party-estimate.md, including the comparison against simply quoting the
state's 2024 result and calling it a day, which this method beats by under one
point.

Read that document before putting any of this on a page.
"""

from __future__ import annotations

import csv
import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator, Sequence

from . import publish
from .adapters import _fips
from .calendar import days_to_election

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]

#: Certified 2024 county presidential returns, vendored into this repo so the
#: estimate runs on a bare CI checkout. Copied byte-for-byte on 2026-09-06 from
#: the ElectIndex forecast model's own county file
#: (`forecasts/usa/data/county_results_2024.csv`, sha256
#: 03accfddddb0bd11741119e9b85262031bd89f8ec59cec165d8db094dc29f254). Its state
#: aggregates reproduce the certified 2024 presidential totals exactly -- GA
#: 2,548,017 D / 2,663,117 R, NC 2,715,375 / 2,898,423, PA 3,423,042 /
#: 3,543,308, TX 4,835,250 / 6,393,597 -- and its FIPS set matches the census
#: county list for every state we track. See docs/party-estimate.md.
BASELINE_PATH = ROOT / "data" / "baseline" / "county_results_2024.csv"

BASELINE_COLUMNS = frozenset({"county_fips", "state", "votes_dem", "votes_rep"})

#: The published file. Deliberately a module constant and not a parameter: this
#: model's output has exactly one legal destination.
ESTIMATE_FILENAME = "party_estimate.csv"

#: Identifies the algorithm in every row, so a row written under an older
#: version is recognisable after the fact.
METHOD = "pres2024-2party-county-weighted"

#: Identifies the WEIGHTS, separately from the algorithm.
SOURCE_NAME = "electindex-estimate/pres2024-county-returns"

#: Empirical, not statistical. The mean absolute distance between this estimate
#: and the reported party-registration split of the same state's early ballots,
#: across every validation day on which at least a quarter of that series' final
#: early vote was in (9.8 points; see `validate()` and docs/party-estimate.md).
#: Rounded to 10 points and applied as a flat half-width, because the error is
#: dominated by a structural mismatch that does not shrink with sample size.
MODEL_ERROR = 0.10

#: Below this many ballots the day is thin enough that the composition of the
#: returned ballots is nothing like the composition of the eventual early vote.
#: A row is still written -- the ballots are real -- but confidence is "low".
THIN_BALLOTS = 50_000

#: Coverage thresholds under which confidence drops to "low".
MIN_COVERAGE = 0.85
MIN_COUNTY_FRACTION = 0.85

#: With complete coverage the band's half-width is exactly MODEL_ERROR, so the
#: threshold has to sit strictly above it -- a bare `> MODEL_ERROR` flips on
#: floating-point noise and hands identical states different confidences. Two
#: points of headroom means "the counties we cannot see could move this by more
#: than the model's own error", which is the condition worth flagging.
MAX_BAND_HALF_WIDTH = MODEL_ERROR + 0.02

ESTIMATE_COLUMNS = [
    "cycle", "state", "date", "days_to_election",
    "est_dem_share", "est_rep_share", "est_margin",
    "est_dem_lo", "est_dem_hi", "est_margin_lo", "est_margin_hi",
    "method", "confidence",
    "counties_used", "counties_total", "coverage_share", "coverage_electorate",
    "ballots_used", "baseline_dem_share", "lean_vs_baseline",
    "state_has_party_reg", "source_name", "retrieved_at",
]

ESTIMATE_KEY = ("cycle", "state", "date")

#: Columns that may never appear in this table. Reading them off schema.py's
#: reported-party contract would be neater, but naming them here is the point:
#: this is the assertion that a model output cannot be mistaken for a count.
FORBIDDEN_COLUMNS = frozenset({"party_dem", "party_rep", "party_oth", "party_npa"})


class EstimateError(RuntimeError):
    """Raised rather than guessing. There is no fallback below a model."""


def _utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _num(cell: str | None) -> int | None:
    """A blank cell is None, never 0 -- the same rule the ingest side runs on."""
    raw = (cell or "").strip()
    if not raw:
        return None
    try:
        return int(float(raw))
    except ValueError:
        return None


def _pct(value: float | None, places: int = 4) -> str:
    return "" if value is None else f"{round(value, places):.{places}f}"


# --------------------------------------------------------------------------
# County partisan baseline
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class CountyLean:
    """One county's 2024 presidential two-party split."""

    fips: str
    state: str
    votes_dem: int
    votes_rep: int

    @property
    def two_party(self) -> int:
        return self.votes_dem + self.votes_rep

    @property
    def dem_share(self) -> float:
        return self.votes_dem / self.two_party


class Baseline:
    """The county weights, indexed by 5-digit FIPS.

    Counties are keyed by FIPS and never by name -- the vendored file labels
    Georgia's 13121 "Campbell" rather than "Fulton", which a name join would
    either drop or, worse, mis-map.
    """

    def __init__(self, counties: dict[str, CountyLean]) -> None:
        self._counties = counties
        self._by_state: dict[str, list[CountyLean]] = defaultdict(list)
        for lean in counties.values():
            self._by_state[lean.state].append(lean)

    def __len__(self) -> int:
        return len(self._counties)

    def __contains__(self, fips: str) -> bool:
        return fips in self._counties

    def get(self, fips: str) -> CountyLean | None:
        return self._counties.get(fips)

    def counties(self, state: str) -> list[CountyLean]:
        return self._by_state.get(state.upper(), [])

    def state_dem_share(self, state: str) -> float | None:
        """The state's own 2024 two-party Democratic share, all voters.

        This is the number the county-weighted estimate is pinned near, and the
        null model every row is scored against.
        """
        rows = self.counties(state)
        if not rows:
            return None
        dem = sum(r.votes_dem for r in rows)
        two = sum(r.two_party for r in rows)
        return dem / two if two else None


def load_baseline(path: Path | str | None = None) -> Baseline:
    """Read the county partisan baseline.

    Raises rather than returning a partial table: a silently short baseline
    would drop counties from the weighting and bias every estimate toward the
    counties that happened to survive.
    """
    path = Path(path or BASELINE_PATH)
    if not path.exists():
        raise EstimateError(
            f"no county baseline at {path}. It is vendored in this repo at "
            f"data/baseline/county_results_2024.csv; see docs/party-estimate.md."
        )
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        missing = BASELINE_COLUMNS - set(reader.fieldnames or [])
        if missing:
            raise EstimateError(f"{path.name} is missing columns {sorted(missing)}")
        counties: dict[str, CountyLean] = {}
        for row in reader:
            fips = (row.get("county_fips") or "").strip()
            dem, rep = _num(row.get("votes_dem")), _num(row.get("votes_rep"))
            if len(fips) != 5 or dem is None or rep is None or dem + rep <= 0:
                continue
            counties[fips] = CountyLean(
                fips=fips, state=(row.get("state") or "").strip().upper(),
                votes_dem=dem, votes_rep=rep,
            )
    if not counties:
        raise EstimateError(f"{path.name} produced no usable county rows")
    return Baseline(counties)


_COUNTY_COUNT: dict[str, int] = {}


def county_count(state: str) -> int | None:
    """How many counties (or county equivalents) the state has, per the census.

    Virginia's 133 includes its independent cities; that is the number the state
    reports against, so it is the right denominator for `counties_total`.
    """
    state = state.upper()
    if state not in _COUNTY_COUNT:
        table = _fips.CENSUS_COUNTIES.get(state)
        _COUNTY_COUNT[state] = len(table) if table else 0
    return _COUNTY_COUNT[state] or None


# --------------------------------------------------------------------------
# The estimate
# --------------------------------------------------------------------------
@dataclass
class PartyEstimate:
    """One modelled state-day. Every field a reader needs to distrust it."""

    cycle: int
    state: str
    day: date
    est_dem_share: float
    est_dem_lo: float
    est_dem_hi: float
    counties_used: int
    counties_total: int | None
    ballots_used: int
    coverage_share: float | None
    coverage_electorate: float | None
    baseline_dem_share: float | None
    state_has_party_reg: bool | None = None
    method: str = METHOD
    source_name: str = SOURCE_NAME
    retrieved_at: str = ""

    def key(self) -> tuple:
        return (int(self.cycle), self.state.upper(), self.day.isoformat())

    @property
    def est_margin(self) -> float:
        """Democratic minus Republican, two-party. Positive is a D lead."""
        return 2 * self.est_dem_share - 1

    @property
    def lean_vs_baseline(self) -> float | None:
        """The only quantity here the geography actually measures.

        How much more Democratic (2024 presidential terms) the counties that
        have returned ballots are than the state as a whole. Typically under two
        points, which is the honest size of the signal.
        """
        if self.baseline_dem_share is None:
            return None
        return self.est_dem_share - self.baseline_dem_share

    @property
    def confidence(self) -> str:
        """Never "high". See docs/party-estimate.md.

        The band already carries the numeric uncertainty; this label is about
        whether the INPUTS are complete enough for the band to mean anything.
        No amount of coverage repairs the structural assumption underneath, so
        the top of this scale is "medium".
        """
        thin = self.ballots_used < THIN_BALLOTS
        short_coverage = (
            self.coverage_share is not None and self.coverage_share < MIN_COVERAGE
        )
        short_counties = (
            self.counties_total is not None
            and self.counties_used < MIN_COUNTY_FRACTION * self.counties_total
        )
        wide = (self.est_dem_hi - self.est_dem_lo) / 2 > MAX_BAND_HALF_WIDTH
        return "low" if (thin or short_coverage or short_counties or wide) else "medium"

    def to_dict(self) -> dict[str, str]:
        return {
            "cycle": str(int(self.cycle)),
            "state": self.state.upper(),
            "date": self.day.isoformat(),
            "days_to_election": str(days_to_election(self.cycle, self.day)),
            "est_dem_share": _pct(self.est_dem_share),
            "est_rep_share": _pct(1 - self.est_dem_share),
            "est_margin": _pct(self.est_margin),
            "est_dem_lo": _pct(self.est_dem_lo),
            "est_dem_hi": _pct(self.est_dem_hi),
            "est_margin_lo": _pct(2 * self.est_dem_lo - 1),
            "est_margin_hi": _pct(2 * self.est_dem_hi - 1),
            "method": self.method,
            "confidence": self.confidence,
            "counties_used": str(self.counties_used),
            "counties_total": "" if self.counties_total is None else str(self.counties_total),
            "coverage_share": _pct(self.coverage_share),
            "coverage_electorate": _pct(self.coverage_electorate),
            "ballots_used": str(self.ballots_used),
            "baseline_dem_share": _pct(self.baseline_dem_share),
            "lean_vs_baseline": _pct(self.lean_vs_baseline),
            "state_has_party_reg": (
                "" if self.state_has_party_reg is None
                else ("true" if self.state_has_party_reg else "false")
            ),
            "source_name": self.source_name,
            "retrieved_at": self.retrieved_at or _utcnow(),
        }


def weighted_share(
    ballots: dict[str, int], baseline: Baseline
) -> tuple[float, int, int] | None:
    """Σ ballots_c · dem_share_c / Σ ballots_c over counties we can weight.

    Returns (share, counties_used, ballots_used), or None when nothing is
    weightable -- which is the whole point: no data means no row, never a 50/50.
    A county reporting a genuine 0 counts as used and contributes zero weight;
    a county reporting nothing at all is simply absent from `ballots`.
    """
    numerator = 0.0
    total = 0
    used = 0
    for fips, count in ballots.items():
        lean = baseline.get(fips)
        if lean is None or count is None or count < 0:
            continue
        numerator += count * lean.dem_share
        total += count
        used += 1
    if used == 0 or total <= 0:
        return None
    return numerator / total, used, total


def coverage_band(
    share: float,
    coverage: float | None,
    missing: Sequence[CountyLean],
    state_counties: Sequence[CountyLean],
) -> tuple[float, float]:
    """A hard bound on what the ballots we cannot see could do to `share`.

    The published share is the mean over the covered fraction f. The full-state
    share is f·share + (1−f)·(whatever the uncovered ballots are), and the
    uncovered ballots have to come from somewhere real -- so the widest they can
    push the answer is to the most Democratic and the most Republican county
    still available to them. That is a bound, not a confidence interval, and it
    collapses to zero when coverage is complete.
    """
    if coverage is None or coverage >= 1.0:
        return share, share
    pool = list(missing) or list(state_counties)
    if not pool:
        return 0.0, 1.0
    gap = 1.0 - coverage
    low = share * coverage + gap * min(c.dem_share for c in pool)
    high = share * coverage + gap * max(c.dem_share for c in pool)
    return min(low, high), max(low, high)


def estimate_day(
    cycle: int,
    state: str,
    day: date,
    ballots: dict[str, int],
    baseline: Baseline,
    *,
    statewide_ballots: int | None = None,
    has_party_reg: bool | None = None,
    retrieved_at: str | None = None,
) -> PartyEstimate | None:
    """One state-day, or None if it cannot be estimated at all.

    `ballots` is {5-digit FIPS: cumulative early ballots}. `statewide_ballots`
    is the state's OWN reported total for that day when it publishes one; it is
    the honest denominator for coverage, because a state can report a statewide
    figure that its county file does not yet add up to (late mail not yet
    attributed to a county is the usual reason).
    """
    state = state.upper()
    result = weighted_share(ballots, baseline)
    if result is None:
        return None
    share, used, ballots_used = result

    state_counties = baseline.counties(state)
    denominator = statewide_ballots if statewide_ballots else ballots_used
    coverage = min(1.0, ballots_used / denominator) if denominator > 0 else None
    if statewide_ballots and ballots_used > statewide_ballots * 1.02:
        # Counties summing above the state's own headline is a restatement
        # mismatch, not extra coverage. Log it; do not let it read as >100%.
        log.warning("%s %s: county sum %d exceeds statewide %d",
                    state, day, ballots_used, statewide_ballots)

    seen = {f for f in ballots if f in baseline}
    missing = [c for c in state_counties if c.fips not in seen]
    lo, hi = coverage_band(share, coverage, missing, state_counties)
    lo = max(0.0, lo - MODEL_ERROR)
    hi = min(1.0, hi + MODEL_ERROR)

    electorate = sum(c.two_party for c in state_counties)
    covered_electorate = sum(c.two_party for c in state_counties if c.fips in seen)

    return PartyEstimate(
        cycle=int(cycle), state=state, day=day,
        est_dem_share=share, est_dem_lo=lo, est_dem_hi=hi,
        counties_used=used, counties_total=county_count(state),
        ballots_used=ballots_used,
        coverage_share=coverage,
        coverage_electorate=(covered_electorate / electorate) if electorate else None,
        baseline_dem_share=baseline.state_dem_share(state),
        state_has_party_reg=has_party_reg,
        retrieved_at=retrieved_at or _utcnow(),
    )


# --------------------------------------------------------------------------
# Reading the published tables back
# --------------------------------------------------------------------------
def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def read_county_ballots(out_dir: Path, state: str) -> dict[tuple[int, str], dict[str, int]]:
    """{(cycle, date): {fips: ballots}} from output/counties/<st>.csv.

    A blank `ballots_total` is skipped, not read as zero.
    """
    rows = _read_csv(Path(out_dir) / "counties" / f"{state.lower()}.csv")
    days: dict[tuple[int, str], dict[str, int]] = defaultdict(dict)
    for row in rows:
        cycle = _num(row.get("cycle"))
        fips = (row.get("county_fips") or "").strip()
        total = _num(row.get("ballots_total"))
        day = (row.get("date") or "").strip()
        if cycle is None or total is None or len(fips) != 5 or not day:
            continue
        days[(cycle, day)][fips] = total
    return days


def read_state_daily(out_dir: Path) -> dict[tuple[int, str, str], dict[str, str]]:
    rows = _read_csv(Path(out_dir) / "ev_state_daily.csv")
    out: dict[tuple[int, str, str], dict[str, str]] = {}
    for row in rows:
        cycle = _num(row.get("cycle"))
        state = (row.get("state") or "").strip().upper()
        day = (row.get("date") or "").strip()
        if cycle is None or not state or not day:
            continue
        out[(cycle, state, day)] = row
    return out


def read_party_reg_flags(meta_path: Path) -> dict[str, bool]:
    """`has_party_reg` per state from data/meta/states.csv.

    Arizona reads `true` here and still gets an estimate, because Arizona
    registers by party and its Secretary of State's daily table simply omits it.
    The flag is carried into every row so the CSV itself says whether a real
    party split exists somewhere for that state -- for AZ the right fix is to go
    and get it, not to model it.
    """
    flags: dict[str, bool] = {}
    for row in _read_csv(Path(meta_path)):
        state = (row.get("state") or "").strip().upper()
        raw = (row.get("has_party_reg") or "").strip().lower()
        if state and raw in ("true", "false"):
            flags[state] = raw == "true"
    return flags


def reports_party(row: dict[str, str] | None) -> bool:
    """True if the state itself published a party breakdown that day."""
    if not row:
        return False
    return any((row.get(col) or "").strip() != "" for col in ("party_dem", "party_rep"))


# --------------------------------------------------------------------------
# The run
# --------------------------------------------------------------------------
def build(
    out_dir: Path,
    baseline: Baseline,
    *,
    states: Iterable[str] | None = None,
    cycles: Iterable[int] | None = None,
    meta_path: Path | None = None,
    force: bool = False,
) -> list[PartyEstimate]:
    """Estimate every state-day that has county ballots and no reported party.

    The "no reported party" test is made against the published data, not against
    a hardcoded list of states: the moment a state starts reporting party, its
    estimate stops being produced, which is the behaviour you want from a
    stopgap. `force=True` estimates anyway, which is how `validate()` scores the
    method against states that do report.
    """
    out_dir = Path(out_dir)
    county_dir = out_dir / "counties"
    available = sorted(p.stem.upper() for p in county_dir.glob("*.csv")) if county_dir.is_dir() else []
    wanted = {s.upper() for s in states} if states else None
    cycle_filter = {int(c) for c in cycles} if cycles else None

    statewide = read_state_daily(out_dir)
    flags = read_party_reg_flags(meta_path) if meta_path else {}
    stamp = _utcnow()

    rows: list[PartyEstimate] = []
    for state in available:
        if wanted and state not in wanted:
            continue
        if not baseline.counties(state):
            log.warning("%s: no county baseline; skipped", state)
            continue
        for (cycle, day), ballots in sorted(read_county_ballots(out_dir, state).items()):
            if cycle_filter and cycle not in cycle_filter:
                continue
            reported = statewide.get((cycle, state, day))
            if reports_party(reported) and not force:
                continue
            estimate = estimate_day(
                cycle, state, date.fromisoformat(day), ballots, baseline,
                statewide_ballots=_num((reported or {}).get("ballots_total")),
                has_party_reg=flags.get(state),
                retrieved_at=stamp,
            )
            if estimate is not None:
                rows.append(estimate)
    return rows


def write(out_dir: Path, rows: Sequence[PartyEstimate]) -> dict:
    """Publish to output/party_estimate.csv and nowhere else.

    The destination is not a parameter and the column list cannot contain a
    reported-party column. Both are asserted here rather than left to review,
    because the one failure this module must make impossible is a model number
    landing in a column that means "the state said so".

    Rows are merged by (cycle, state, date), so a state that starts reporting
    party mid-window keeps the estimates already written for the days when it
    did not. That is the honest record -- on those days there was no reported
    party -- but a consumer plotting one state's series has to expect the
    estimate to stop rather than to continue alongside the real numbers.
    """
    forbidden = FORBIDDEN_COLUMNS & set(ESTIMATE_COLUMNS)
    if forbidden:
        raise EstimateError(
            f"party_estimate.csv must never carry reported-party columns: {sorted(forbidden)}"
        )
    path = Path(out_dir) / ESTIMATE_FILENAME
    return publish.publish_table(
        path, ESTIMATE_COLUMNS, ESTIMATE_KEY,
        [r.to_dict() for r in rows],
        # The content-loss guard exists to catch a truncated DOWNLOAD. This table
        # is a pure function of data already on disk, so a rerun that produces a
        # different row is a corrected model, not a truncated fetch.
        guard=False,
    )


# --------------------------------------------------------------------------
# Ground truth: score the method against states that DO report party
# --------------------------------------------------------------------------
@dataclass
class Validation:
    """How far the estimate landed from a state's own reported party split."""

    cycle: int
    state: str
    days: int
    final_error: float           # signed, percentage points, on Dem two-party share
    mean_abs_error: float        # over mature days only
    max_abs_error: float
    null_mean_abs_error: float   # same days, quoting the state's 2024 result instead
    est_range: float             # how far the estimate moved, points
    truth_range: float           # how far the truth moved, points
    final_estimate: float
    final_truth: float
    baseline: float

    @property
    def gain(self) -> float:
        """Points of accuracy the county weighting buys over the null model."""
        return self.null_mean_abs_error - self.mean_abs_error


#: A day is "mature" once this share of the series' eventual early vote is in.
#: Before that the returned ballots are almost all mail, whose party mix is
#: nothing like the eventual electorate's, and every method looks terrible.
MATURE_FRACTION = 0.25


def validate(
    out_dir: Path, baseline: Baseline, *, states: Iterable[str] | None = None
) -> list[Validation]:
    """Score the estimate against every state-cycle that reports party.

    This is the honest measure of the feature and the reason
    docs/party-estimate.md exists. It compares a modelled two-party PRESIDENTIAL
    share against a reported two-party REGISTRATION share, which are not the
    same object -- Kentucky is full of registered Democrats who vote Republican,
    and that alone is most of Kentucky's error. The comparison is still the one
    a reader will make, so it is the one we publish.
    """
    out_dir = Path(out_dir)
    county_dir = out_dir / "counties"
    available = sorted(p.stem.upper() for p in county_dir.glob("*.csv")) if county_dir.is_dir() else []
    wanted = {s.upper() for s in states} if states else None
    statewide = read_state_daily(out_dir)

    results: list[Validation] = []
    for state in available:
        if wanted and state not in wanted:
            continue
        state_base = baseline.state_dem_share(state)
        if state_base is None:
            continue
        by_cycle: dict[int, list[tuple[str, float, float, int]]] = defaultdict(list)
        for (cycle, day), ballots in sorted(read_county_ballots(out_dir, state).items()):
            reported = statewide.get((cycle, state, day))
            if not reports_party(reported):
                continue
            dem, rep = _num(reported.get("party_dem")), _num(reported.get("party_rep"))
            if dem is None or rep is None or dem + rep <= 0:
                continue
            weighted = weighted_share(ballots, baseline)
            if weighted is None:
                continue
            total = _num(reported.get("ballots_total")) or weighted[2]
            by_cycle[cycle].append((day, weighted[0], dem / (dem + rep), total))

        for cycle, series in sorted(by_cycle.items()):
            if len(series) < 2:
                continue
            series.sort()
            final_volume = max(v for _, _, _, v in series) or 1
            mature = [s for s in series if s[3] / final_volume >= MATURE_FRACTION]
            if not mature:
                mature = series[-1:]
            errors = [abs(est - truth) for _, est, truth, _ in mature]
            nulls = [abs(state_base - truth) for _, _, truth, _ in mature]
            _, last_est, last_truth, _ = series[-1]
            ests = [est for _, est, _, _ in mature]
            truths = [truth for _, _, truth, _ in mature]
            results.append(Validation(
                cycle=cycle, state=state, days=len(series),
                final_error=(last_est - last_truth) * 100,
                mean_abs_error=sum(errors) / len(errors) * 100,
                max_abs_error=max(abs(est - truth) for _, est, truth, _ in series) * 100,
                null_mean_abs_error=sum(nulls) / len(nulls) * 100,
                est_range=(max(ests) - min(ests)) * 100,
                truth_range=(max(truths) - min(truths)) * 100,
                final_estimate=last_est * 100,
                final_truth=last_truth * 100,
                baseline=state_base * 100,
            ))
    return results


def format_validation(results: Sequence[Validation]) -> Iterator[str]:
    yield (f"{'cycle':>5s} {'st':3s} {'days':>4s} {'est':>6s} {'truth':>6s} {'base':>6s} "
           f"{'final':>7s} {'MAE':>6s} {'null':>6s} {'gain':>6s} {'moved':>6s} {'truth±':>7s}")
    for r in sorted(results, key=lambda r: (r.state, r.cycle)):
        yield (f"{r.cycle:5d} {r.state:3s} {r.days:4d} {r.final_estimate:6.1f} "
               f"{r.final_truth:6.1f} {r.baseline:6.1f} {r.final_error:+7.1f} "
               f"{r.mean_abs_error:6.1f} {r.null_mean_abs_error:6.1f} {r.gain:+6.1f} "
               f"{r.est_range:6.1f} {r.truth_range:7.1f}")
    if not results:
        yield "(no state-cycle in output/ both reports party and has county rows)"
        return
    n = len(results)
    yield ""
    yield (f"mean |final error| = {sum(abs(r.final_error) for r in results) / n:.1f} pp   "
           f"mean MAE = {sum(r.mean_abs_error for r in results) / n:.1f} pp   "
           f"null model MAE = {sum(r.null_mean_abs_error for r in results) / n:.1f} pp   "
           f"gain = {sum(r.gain for r in results) / n:+.1f} pp")
    yield ("columns: final = signed error on Dem two-party share at the last day; "
           "MAE/null over days with >=25% of the series' final early vote; "
           "moved = how far the estimate travelled across the window; "
           "truth+- = how far the reported party split travelled.")
