"""What would 2024 have looked like if the 2026 early electorate had voted it?

THE QUESTION
------------
Not "who wins in 2026". Not "how accurate was 2024". This asks one thing:

    the specific people who have returned ballots so far in 2026 -- if exactly
    that set of voters had turned out in 2024, and every group had voted the way
    it actually voted in 2024, what would 2024 have produced?

The answer is a COMPOSITIONAL statement in points of presidential margin: "the
early electorate so far is worth N points more Republican than the one it is
being compared with." It isolates WHO IS TURNING OUT from HOW THEY VOTE, and it
does so by freezing behaviour at 2024. That freeze is the definition, not a
defect -- but it means this number says NOTHING about persuasion. If every group
votes five points more Republican in 2026 than in 2024, this model cannot see it
and will not move.

THE TRAP, AND WHAT THIS MODULE DOES ABOUT IT
--------------------------------------------
`estimate.py` already publishes `lean_vs_baseline`, a sentence of the form "the
counties that have returned ballots so far are 1.7 points more Democratic than
Wisconsin as a whole in 2024." That comparison is nearly empty, because early
voters are a SELF-SELECTED SUBSET. Of course they differ from everybody. Setting
2026's early electorate against 2024's FULL electorate would mostly re-measure
"early voters are not everyone", which nobody needed a model to learn.

So the comparison here is LIKE FOR LIKE and only like for like:

    the 2026 early electorate  vs  the 2024 early electorate
    at the same DAYS TO ELECTION, never the same calendar date

That is the only version in which a difference means something changed. A state
with no 2024 early series to compare against gets NO COUNTERFACTUAL -- not a
fallback to the full-state comparison, not a zero. `output/ev_state_daily.csv`
and `output/counties/*.csv` carry the 2022 and 2024 backfills precisely so this
comparison is possible where it is possible, and it is possible in fewer places
than one would like.

WHAT IT MEASURES, DIMENSION BY DIMENSION
----------------------------------------
A composition is a set of weights over groups. Valuing one in 2024 presidential
points needs each group's 2024 presidential margin. There is exactly one
dimension for which this repo holds that number, exactly and verifiably:

  county    `data/baseline/county_results_2024.csv`, certified county returns.
            Weighting the full 2024 electorate by it reproduces each state's
            certified two-party margin to a hundredth of a point (there is a
            test). So the arithmetic here is an IDENTITY, and the only thing in
            question is whether county geography can see compositional change.

  party     ~30 states register voters by party and several publish the party
            registration of returned ballots. That is a COUNT, and the shift in
            it between cycles is a measured fact -- but converting registration
            points into presidential margin points needs to know how registered
            Democrats actually voted in 2024, which this repo does not have and
            which cannot be responsibly guessed (Kentucky's registration split
            sits ten points Democratic of its presidential split). So the party
            shift is published in its OWN unit, in its own column, and is NEVER
            folded into `shift_pp`.

  age/race/sex
            `output/demo/*.csv` has these for GA, MD, MI, NC and SC. Group-level
            2024 presidential behaviour by demographic is not in this repo and
            WAS NOT SOURCED for it. Saying so is the honest option; inventing a
            citation is not one. These dimensions are published as a total-
            variation composition distance -- how far the mix moved, in points,
            with no claim about which way that cuts -- and are likewise never
            folded into `shift_pp`.

`dims_used` says which dimensions are inside the headline number. Today, in every
row this module can write, it reads `county`.

THE MEASURED ANSWER, BEFORE ANYTHING ELSE
-----------------------------------------
`python -m ev counterfactual --validate` runs the identical machinery on the 2024
early electorate against the 2022 one, and scores it against the only
compositional ground truth that exists: the party registration those same states
reported for those same ballots. Four state-cycles qualify (KY, MD, ME, NC).

    mean absolute error, mature days   11.2 points of margin
    the same for the "nothing changed" null   11.1 points
    what county geography buys        -0.1 points

It does not beat the null. Fitting a scale factor to it leave-one-state-out
makes it worse still, and the fitted scale flips sign between folds (+4.1, -1.6).
The mechanism is the one `docs/party-estimate.md` already found: every tracked
state publishes every one of its counties, so the ballot weights are close to
proportional to county size and the weighted mean is arithmetically pinned near
the state's own last result. County geography moves about a point across a
window while the composition it is standing in for moves twelve.

Read docs/counterfactual.md before putting any of this on a page. The
recommendation there is that the modelled margin should not ship, and that the
party-registration shift -- a count, not a model -- should.
"""

from __future__ import annotations

import csv
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Iterator, Sequence

from . import publish
from .calendar import CYCLES, election_date
from .estimate import Baseline, CountyLean, county_count, load_baseline

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]

#: The published file. A module constant, not a parameter: like `estimate.py`,
#: this model's output has exactly one legal destination.
COUNTERFACTUAL_FILENAME = "counterfactual.csv"

#: The cycle whose VOTING BEHAVIOUR is held fixed, and whose county returns are
#: the weights. Changing this would change the question, not the answer.
BEHAVIOUR_CYCLE = 2024

#: Which cycle each target cycle is compared against. The reference is always the
#: previous comparable early electorate, never the full electorate -- see the
#: trap above. 2022 has no predecessor in this repo, so it is never a target.
REFERENCE_CYCLE: dict[int, int] = {2026: 2024, 2024: 2022}

METHOD = "pres2024-county-composition-diff"
SOURCE_NAME = "electindex-counterfactual/pres2024-county-returns"

#: Cross-cycle comparison aligns on DAYS TO ELECTION, never on calendar date --
#: Election Day moves (Nov 8 2022, Nov 5 2024, Nov 3 2026), so a date join
#: silently compares two different points of two different campaigns. Two series
#: rarely land on the same day-out, so the match allows this much slack and takes
#: the closest day inside it. The same three days `regress.py` uses, for the same
#: reason; it is deliberately narrow, because the composition of returned ballots
#: moves enormously across a window.
DTE_MATCH_TOLERANCE = 3

#: A day is "mature" once this share of its series' eventual early vote is in.
#: Before that the returns are almost all mail and their composition is nothing
#: like the eventual early electorate's; scoring there would make every method
#: look terrible and would tell you nothing about any of them. Same threshold and
#: same reasoning as `estimate.MATURE_FRACTION`.
MATURE_FRACTION = 0.25

#: Below this many ballots on either side of the comparison, the day is thin
#: enough that its composition is noise. The row is still written -- the ballots
#: are real -- but `confidence` is "low". North Carolina 2026 today is EIGHT
#: ballots in five counties; a shift computed on that is arithmetic, not
#: information.
THIN_BALLOTS = 50_000

#: Coverage thresholds under which confidence drops to "low".
MIN_COVERAGE = 0.85
MIN_COUNTY_FRACTION = 0.85

#: The measured error of this method, in percentage points of margin, applied as
#: a flat half-width because it is structural rather than sampling noise -- it
#: does NOT shrink as more ballots come in.
#:
#: It is the mean absolute distance, over mature days and averaged across the
#: four state-cycles that can be scored at all, between the compositional shift
#: this model reports and the shift the same states' own reported party
#: registration says actually happened (11.2 points; rounded up). That is not a
#: like-for-like unit -- a registration point is not a presidential point -- and
#: it is the closest thing to a measurement that exists. Read it as "the size of
#: the compositional change that county geography does not see", because that is
#: what it is: the geography moves about a point and the registration moves
#: twelve.
#:
#: `test_model_error_matches_the_measured_validation` refits it from output/ and
#: fails if the data moves away from it.
MODEL_ERROR_PP = 11.5

#: With complete coverage on both sides the band's half-width is exactly
#: MODEL_ERROR_PP, so the confidence threshold has to sit strictly above it or it
#: flips on floating-point noise. Two points of headroom means "the ballots we
#: cannot see could move this by more than the model's own error".
MAX_BAND_HALF_WIDTH = MODEL_ERROR_PP + 2.0

#: The bar this feature has to clear to be worth publishing as a margin, in
#: points of accuracy over the "composition has not changed at all" null. One
#: point, the same bar `regress.py` sets. It is not met. `format_validation`
#: prints the verdict rather than leaving it to prose.
MIN_GAIN = 1.0

COUNTERFACTUAL_COLUMNS = [
    "cycle", "state", "date", "days_to_election",
    "reference_cycle", "reference_date", "reference_days_to_election",
    # The headline. `implied_margin_2024` = `actual_margin_2024` + `shift_pp`.
    "implied_margin_2024", "actual_margin_2024", "shift_pp",
    "shift_lo", "shift_hi",
    # The two compositions the shift is a difference of, published so the
    # subtraction is checkable rather than taken on trust.
    "composition_margin", "reference_composition_margin",
    # WHICH DIMENSIONS ARE INSIDE THE HEADLINE, and which merely exist. The
    # distance between these two columns is the honest summary of this feature.
    "dims_used", "dims_reported",
    "counties_used", "counties_total", "county_coverage",
    "reference_counties_used", "reference_county_coverage",
    "ballots", "reference_ballots",
    # Reported, never folded in: a party REGISTRATION point is not a
    # presidential margin point, and this repo holds nothing that converts one
    # into the other.
    "party_margin_shift_pp", "party_coverage", "reference_party_coverage",
    # Total-variation distance between the two cycles' demographic mixes, in
    # points. "How far the mix moved", with no claim about which way it cuts.
    "age_shift_tv", "race_shift_tv", "sex_shift_tv", "demo_coverage",
    "behaviour_cycle", "confidence", "method", "source_name", "retrieved_at",
]

COUNTERFACTUAL_KEY = ("cycle", "state", "date")

#: Columns that may never appear in this table. Naming them here rather than
#: importing schema.py's contract is the point: this is the assertion that a
#: model output cannot be mistaken for a count.
FORBIDDEN_COLUMNS = frozenset({"party_dem", "party_rep", "party_oth", "party_npa"})

#: Demographic dimensions carried as composition-distance columns, in the order
#: their columns appear. Kept local rather than imported from schema.py so that
#: adding a dimension there does not silently add a column here.
DEMO_DIMENSIONS = ("age", "race", "sex")

_DP = 4


class CounterfactualError(RuntimeError):
    """Raised rather than guessing. There is no fallback below a model."""


def _utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _num(cell: str | None) -> int | None:
    """A blank cell is None, never 0 -- THE BLANK RULE, on the read side."""
    raw = (cell or "").strip()
    if not raw:
        return None
    try:
        return int(float(raw))
    except ValueError:
        return None


def _pp(value: float | None, places: int = _DP) -> str:
    """Percentage points to `places` decimals. None writes an empty cell."""
    return "" if value is None else f"{round(value, places):.{places}f}"


# --------------------------------------------------------------------------
# The one dimension that can be priced
# --------------------------------------------------------------------------
def county_margin(lean: CountyLean) -> float:
    """A county's 2024 presidential two-party margin, D positive, in points.

    Two-party because the baseline is two-party; the third-party remainder is
    2-3% nationally and excluding it from both sides of a DIFFERENCE removes it
    almost exactly.
    """
    return (lean.votes_dem - lean.votes_rep) / lean.two_party * 100


def composition_margin(
    ballots: dict[str, int], baseline: Baseline
) -> tuple[float, int, int] | None:
    """Σ ballots_c · margin_c / Σ ballots_c, over counties we can weight.

    "If everyone who has returned a ballot so far voted exactly the way their
    county did in 2024, this is the margin they would produce."

    Returns (margin_points, counties_used, ballots_used), or None when nothing is
    weightable -- which is the whole point: no data means no row, never a zero
    shift. A county reporting a genuine 0 counts as used and contributes zero
    weight; a county reporting nothing at all is simply absent from `ballots`.
    A FIPS with no baseline is dropped, never guessed at.
    """
    numerator = 0.0
    total = 0
    used = 0
    for fips, count in ballots.items():
        lean = baseline.get(fips)
        if lean is None or count is None or count < 0:
            continue
        numerator += count * county_margin(lean)
        total += count
        used += 1
    if used == 0 or total <= 0:
        return None
    return numerator / total, used, total


def state_actual_margin(state: str, baseline: Baseline) -> float | None:
    """The state's certified 2024 presidential two-party margin, in points.

    Deliberately derived by aggregating the SAME county file the weights come
    from, rather than read out of `output/results_state.csv`. That makes
    `composition_margin(full 2024 electorate) == state_actual_margin` an exact
    identity rather than an approximate agreement between two sources, which is
    what lets the shift be read as a pure difference in composition. The two
    agree anyway -- to 0.014 points in every state this tracker follows, and to
    0.104 in Alaska, which it does not; `test_baseline_reproduces_the_certified_margin`
    pins that.
    """
    share = baseline.state_dem_share(state)
    return None if share is None else (2 * share - 1) * 100


# --------------------------------------------------------------------------
# Reading the published tables back
# --------------------------------------------------------------------------
def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


@dataclass
class DayIndex:
    """One state-cycle's early-vote curve, keyed by DAYS TO ELECTION.

    Days-to-election and not date, because that is the only axis on which two
    cycles are comparable at all. Post-election days are dropped: a row dated
    after the polls closed counts mail that arrived late, so matching this
    cycle's Election Day against last cycle's canvass would compare a live figure
    to a certified one and call the difference composition.
    """

    cycle: int
    state: str
    #: days_to_election -> {5-digit FIPS: cumulative ballots}
    counties: dict[int, dict[str, int]] = field(default_factory=dict)
    #: days_to_election -> the state's own published row for that day
    state_rows: dict[int, dict[str, str]] = field(default_factory=dict)
    #: days_to_election -> {dimension: {bucket: ballots}}
    demo: dict[int, dict[str, dict[str, int]]] = field(default_factory=dict)

    def day_of(self, dte: int) -> date:
        return election_date(self.cycle) - timedelta(days=dte)

    def at(self, dte: int, tolerance: int = DTE_MATCH_TOLERANCE) -> int | None:
        """The day-out closest to `dte` that has county ballots, within slack."""
        best, best_gap = None, tolerance + 1
        for day in self.counties:
            gap = abs(day - dte)
            if gap < best_gap:
                best, best_gap = day, gap
        return best

    def final_ballots(self) -> int:
        """The largest county-ballot sum this series ever reached."""
        return max((sum(v for v in b.values() if v is not None)
                    for b in self.counties.values()), default=0)


def read_series(out_dir: Path, state: str) -> dict[int, DayIndex]:
    """Every cycle's curve for one state, from the published CSVs.

    A blank `ballots_total` is skipped, not read as zero.
    """
    out_dir = Path(out_dir)
    state = state.upper()
    index: dict[int, DayIndex] = {}

    def slot(cycle: int) -> DayIndex:
        return index.setdefault(cycle, DayIndex(cycle=cycle, state=state))

    for row in _read_csv(out_dir / "counties" / f"{state.lower()}.csv"):
        cycle, dte = _num(row.get("cycle")), _num(row.get("days_to_election"))
        fips = (row.get("county_fips") or "").strip()
        total = _num(row.get("ballots_total"))
        if cycle is None or dte is None or dte < 0 or total is None or len(fips) != 5:
            continue
        slot(cycle).counties.setdefault(dte, {})[fips] = total

    for row in _read_csv(out_dir / "ev_state_daily.csv"):
        if (row.get("state") or "").strip().upper() != state:
            continue
        cycle, dte = _num(row.get("cycle")), _num(row.get("days_to_election"))
        if cycle is None or dte is None or dte < 0:
            continue
        slot(cycle).state_rows[dte] = row

    for row in _read_csv(out_dir / "demo" / f"{state.lower()}.csv"):
        cycle, dte = _num(row.get("cycle")), _num(row.get("days_to_election"))
        dimension = (row.get("dimension") or "").strip()
        bucket = (row.get("bucket") or "").strip()
        total = _num(row.get("ballots_total"))
        if cycle is None or dte is None or dte < 0 or not dimension or not bucket:
            continue
        if total is None:
            continue
        slot(cycle).demo.setdefault(dte, {}).setdefault(dimension, {})[bucket] = total

    return index


def available_states(out_dir: Path) -> list[str]:
    county_dir = Path(out_dir) / "counties"
    if not county_dir.is_dir():
        return []
    return sorted(p.stem.upper() for p in county_dir.glob("*.csv"))


# --------------------------------------------------------------------------
# The dimensions that are reported but not priced
# --------------------------------------------------------------------------
def party_margin(row: dict[str, str] | None) -> float | None:
    """(D - R) / (D + R) of the party-registered ballots returned, in points.

    A REGISTRATION margin. It is not a vote margin and is never treated as one.
    """
    if not row:
        return None
    dem, rep = _num(row.get("party_dem")), _num(row.get("party_rep"))
    if dem is None or rep is None or dem + rep <= 0:
        return None
    return (dem - rep) / (dem + rep) * 100


def party_coverage(row: dict[str, str] | None) -> float | None:
    """What share of the day's ballots carry any party label at all.

    The sum of whichever party buckets the state reported over its own headline.
    A state that reports D and R but not unaffiliated scores below 1.0, which is
    honest: the ballots in the gap have a party we were not told.
    """
    if not row:
        return None
    total = _num(row.get("ballots_total"))
    if not total or total <= 0:
        return None
    buckets = [_num(row.get(c)) for c in ("party_dem", "party_rep", "party_oth", "party_npa")]
    named = [b for b in buckets if b is not None]
    if not named:
        return None
    return min(1.0, sum(named) / total)


def composition_distance(now: dict[str, int], ref: dict[str, int]) -> float | None:
    """Total-variation distance between two bucket mixes, in points.

    Half the sum of absolute differences in share, so it runs 0 (identical) to
    100 (disjoint). It says HOW FAR the mix moved and deliberately says nothing
    about which way that cuts, because pricing a demographic bucket needs
    group-level 2024 presidential behaviour that this repo does not have.

    Returns None unless BOTH cycles report the same bucket vocabulary. A bucket
    present in one cycle and absent in the other is "not reported", not zero --
    THE BLANK RULE -- and treating it as zero would manufacture a shift out of a
    schema change.
    """
    if not now or not ref or set(now) != set(ref):
        return None
    now_total, ref_total = sum(now.values()), sum(ref.values())
    if now_total <= 0 or ref_total <= 0:
        return None
    return 50.0 * sum(
        abs(now[b] / now_total - ref[b] / ref_total) for b in now
    )


def demo_coverage(
    demo: dict[str, dict[str, int]] | None, state_row: dict[str, str] | None
) -> float | None:
    """Ballots accounted for by the demographic table, over the state headline.

    Every dimension partitions the same ballots, so the largest dimension total
    is the right numerator; a dimension with an unreported bucket would otherwise
    drag the number down for a reason that has nothing to do with coverage.
    """
    if not demo or not state_row:
        return None
    total = _num(state_row.get("ballots_total"))
    if not total or total <= 0:
        return None
    biggest = max((sum(b.values()) for b in demo.values()), default=0)
    if biggest <= 0:
        return None
    return min(1.0, biggest / total)


# --------------------------------------------------------------------------
# The band
# --------------------------------------------------------------------------
def coverage_bound(
    margin: float,
    coverage: float | None,
    missing: Sequence[CountyLean],
    state_counties: Sequence[CountyLean],
) -> tuple[float, float]:
    """A hard bound on what the ballots we cannot see could do to `margin`.

    The published figure is a mean over the covered fraction f; the full-early
    figure is f·margin + (1-f)·(whatever the uncovered ballots are), and those
    ballots have to come from real counties with real margins. The bound runs
    from the most Democratic to the most Republican county still unaccounted for.
    It collapses to zero at complete coverage. A bound, not a confidence
    interval. Same construction as `estimate.coverage_band`, on margins rather
    than shares.
    """
    if coverage is None or coverage >= 1.0:
        return margin, margin
    pool = list(missing) or list(state_counties)
    if not pool:
        return -100.0, 100.0
    gap = 1.0 - coverage
    margins = [county_margin(c) for c in pool]
    low = margin * coverage + gap * min(margins)
    high = margin * coverage + gap * max(margins)
    return min(low, high), max(low, high)


# --------------------------------------------------------------------------
# One row
# --------------------------------------------------------------------------
@dataclass
class Counterfactual:
    """One state-day's like-for-like compositional shift. Every field a reader
    needs in order to distrust it."""

    cycle: int
    state: str
    day: date
    days_to_election: int
    reference_cycle: int
    reference_day: date
    reference_days_to_election: int
    composition_margin: float
    reference_composition_margin: float
    actual_margin_2024: float
    shift_lo: float
    shift_hi: float
    counties_used: int
    counties_total: int | None
    county_coverage: float | None
    reference_counties_used: int
    reference_county_coverage: float | None
    ballots: int
    reference_ballots: int
    dims_used: tuple[str, ...] = ("county",)
    dims_reported: tuple[str, ...] = ("county",)
    party_margin_shift_pp: float | None = None
    party_coverage: float | None = None
    reference_party_coverage: float | None = None
    demo_shift_tv: dict[str, float] = field(default_factory=dict)
    demo_coverage: float | None = None
    method: str = METHOD
    source_name: str = SOURCE_NAME
    retrieved_at: str = ""

    def key(self) -> tuple:
        return (int(self.cycle), self.state.upper(), self.day.isoformat())

    @property
    def shift_pp(self) -> float:
        """The whole feature, in one number.

        Points of 2024 presidential margin, Democratic positive. Positive means
        the ballots returned so far come from a composition that would have
        produced a MORE Democratic 2024 than the ballots returned at the same
        point last cycle did.
        """
        return self.composition_margin - self.reference_composition_margin

    @property
    def implied_margin_2024(self) -> float:
        """The 2024 margin this composition implies.

        Anchored on the certified result and moved by the like-for-like shift,
        NOT set equal to `composition_margin`. The raw composition margin of a
        self-selected early electorate differs from the state's result mostly
        because early voters are not everyone, which is the comparison this
        module exists to refuse to make. Both raw figures are published beside
        this one so the anchoring is visible rather than implied.
        """
        return self.actual_margin_2024 + self.shift_pp

    @property
    def confidence(self) -> str:
        """Never "high", and there is a test that says so.

        The band already carries the numeric uncertainty. This label is about
        whether the INPUTS are complete enough for the band to mean anything --
        and no amount of coverage repairs the finding that county geography does
        not beat the no-change null, so the top of this scale is "medium".
        """
        thin = min(self.ballots, self.reference_ballots) < THIN_BALLOTS
        short_coverage = any(
            c is not None and c < MIN_COVERAGE
            for c in (self.county_coverage, self.reference_county_coverage)
        )
        short_counties = (
            self.counties_total is not None
            and min(self.counties_used, self.reference_counties_used)
            < MIN_COUNTY_FRACTION * self.counties_total
        )
        wide = (self.shift_hi - self.shift_lo) / 2 > MAX_BAND_HALF_WIDTH
        return "low" if (thin or short_coverage or short_counties or wide) else "medium"

    def to_dict(self) -> dict[str, str]:
        row = {
            "cycle": str(int(self.cycle)),
            "state": self.state.upper(),
            "date": self.day.isoformat(),
            "days_to_election": str(int(self.days_to_election)),
            "reference_cycle": str(int(self.reference_cycle)),
            "reference_date": self.reference_day.isoformat(),
            "reference_days_to_election": str(int(self.reference_days_to_election)),
            "implied_margin_2024": _pp(self.implied_margin_2024),
            "actual_margin_2024": _pp(self.actual_margin_2024),
            "shift_pp": _pp(self.shift_pp),
            "shift_lo": _pp(self.shift_lo),
            "shift_hi": _pp(self.shift_hi),
            "composition_margin": _pp(self.composition_margin),
            "reference_composition_margin": _pp(self.reference_composition_margin),
            "dims_used": ";".join(self.dims_used),
            "dims_reported": ";".join(self.dims_reported),
            "counties_used": str(self.counties_used),
            "counties_total": "" if self.counties_total is None else str(self.counties_total),
            "county_coverage": _pp(self.county_coverage),
            "reference_counties_used": str(self.reference_counties_used),
            "reference_county_coverage": _pp(self.reference_county_coverage),
            "ballots": str(self.ballots),
            "reference_ballots": str(self.reference_ballots),
            "party_margin_shift_pp": _pp(self.party_margin_shift_pp),
            "party_coverage": _pp(self.party_coverage),
            "reference_party_coverage": _pp(self.reference_party_coverage),
            "demo_coverage": _pp(self.demo_coverage),
            "behaviour_cycle": str(BEHAVIOUR_CYCLE),
            "confidence": self.confidence,
            "method": self.method,
            "source_name": self.source_name,
            "retrieved_at": self.retrieved_at or _utcnow(),
        }
        for dimension in DEMO_DIMENSIONS:
            row[f"{dimension}_shift_tv"] = _pp(self.demo_shift_tv.get(dimension))
        return row


# --------------------------------------------------------------------------
# The comparison
# --------------------------------------------------------------------------
def compare_day(
    state: str,
    now: DayIndex,
    reference: DayIndex,
    dte: int,
    baseline: Baseline,
    *,
    tolerance: int = DTE_MATCH_TOLERANCE,
    retrieved_at: str | None = None,
) -> Counterfactual | None:
    """One state-day against the same state at the same days-out last cycle.

    Returns None -- never a zero shift -- when there is no matching reference
    day, when either side has nothing weightable, or when the state has no 2024
    county baseline. That is the rule the whole feature turns on: a state without
    the data produces no row.
    """
    state = state.upper()
    actual = state_actual_margin(state, baseline)
    if actual is None:
        return None
    ref_dte = reference.at(dte, tolerance)
    if ref_dte is None:
        return None

    here = composition_margin(now.counties.get(dte, {}), baseline)
    there = composition_margin(reference.counties.get(ref_dte, {}), baseline)
    if here is None or there is None:
        return None
    margin, used, ballots = here
    ref_margin, ref_used, ref_ballots = there

    state_counties = baseline.counties(state)
    now_row = now.state_rows.get(dte)
    ref_row = reference.state_rows.get(ref_dte)

    def coverage(row: dict[str, str] | None, counted: int) -> float | None:
        """Ballots weighted over the state's OWN headline for that day.

        The state's figure is the honest denominator: a state can publish a
        statewide total its county file does not yet add up to (late mail not yet
        attributed to a county is the usual reason). Falls back to the county
        sum, which then reads 1.0.
        """
        headline = _num((row or {}).get("ballots_total"))
        denominator = headline if headline else counted
        if not denominator or denominator <= 0:
            return None
        if headline and counted > headline * 1.02:
            log.warning("%s %s: county sum %d exceeds statewide %d",
                        state, dte, counted, headline)
        return min(1.0, counted / denominator)

    cover_now = coverage(now_row, ballots)
    cover_ref = coverage(ref_row, ref_ballots)

    seen_now = {f for f in now.counties.get(dte, {}) if f in baseline}
    seen_ref = {f for f in reference.counties.get(ref_dte, {}) if f in baseline}
    lo_now, hi_now = coverage_bound(
        margin, cover_now, [c for c in state_counties if c.fips not in seen_now],
        state_counties)
    lo_ref, hi_ref = coverage_bound(
        ref_margin, cover_ref, [c for c in state_counties if c.fips not in seen_ref],
        state_counties)
    # Interval arithmetic on a difference: the shift is widest when this cycle
    # sits at one end of its bound and the reference at the other.
    lo = (lo_now - hi_ref) - MODEL_ERROR_PP
    hi = (hi_now - lo_ref) + MODEL_ERROR_PP

    # Dimensions that exist but are NOT priced.
    reported = ["county"]
    now_party, ref_party = party_margin(now_row), party_margin(ref_row)
    party_shift = (
        None if now_party is None or ref_party is None else now_party - ref_party
    )
    if party_shift is not None:
        reported.append("party")

    demo_shift: dict[str, float] = {}
    now_demo = now.demo.get(dte, {})
    ref_demo = reference.demo.get(ref_dte, {})
    for dimension in DEMO_DIMENSIONS:
        distance = composition_distance(
            now_demo.get(dimension, {}), ref_demo.get(dimension, {})
        )
        if distance is not None:
            demo_shift[dimension] = distance
            reported.append(dimension)

    return Counterfactual(
        cycle=now.cycle, state=state,
        day=now.day_of(dte), days_to_election=dte,
        reference_cycle=reference.cycle,
        reference_day=reference.day_of(ref_dte),
        reference_days_to_election=ref_dte,
        composition_margin=margin,
        reference_composition_margin=ref_margin,
        actual_margin_2024=actual,
        shift_lo=lo, shift_hi=hi,
        counties_used=used, counties_total=county_count(state),
        county_coverage=cover_now,
        reference_counties_used=ref_used,
        reference_county_coverage=cover_ref,
        ballots=ballots, reference_ballots=ref_ballots,
        dims_used=("county",),
        dims_reported=tuple(reported),
        party_margin_shift_pp=party_shift,
        party_coverage=party_coverage(now_row),
        reference_party_coverage=party_coverage(ref_row),
        demo_shift_tv=demo_shift,
        demo_coverage=demo_coverage(now_demo, now_row),
        retrieved_at=retrieved_at or _utcnow(),
    )


def build(
    out_dir: Path,
    baseline: Baseline,
    *,
    states: Iterable[str] | None = None,
    cycles: Iterable[int] | None = None,
    tolerance: int = DTE_MATCH_TOLERANCE,
) -> list[Counterfactual]:
    """Every state-day that has a like-for-like reference to compare against.

    A state with no early series in the reference cycle produces NOTHING -- not a
    comparison against the full 2024 electorate, which would measure "early
    voters are not everyone", and not a zero. That is the difference between this
    table and `estimate.py`'s `lean_vs_baseline`.
    """
    out_dir = Path(out_dir)
    wanted = {s.upper() for s in states} if states else None
    cycle_filter = (
        {int(c) for c in cycles} if cycles else set(REFERENCE_CYCLE)
    )
    stamp = _utcnow()

    rows: list[Counterfactual] = []
    for state in available_states(out_dir):
        if wanted and state not in wanted:
            continue
        if not baseline.counties(state):
            log.warning("%s: no county baseline; skipped", state)
            continue
        series = read_series(out_dir, state)
        for cycle in sorted(cycle_filter):
            reference_cycle = REFERENCE_CYCLE.get(cycle)
            now = series.get(cycle)
            reference = series.get(reference_cycle) if reference_cycle else None
            if now is None or reference is None or not reference.counties:
                continue
            for dte in sorted(now.counties, reverse=True):
                row = compare_day(state, now, reference, dte, baseline,
                                  tolerance=tolerance, retrieved_at=stamp)
                if row is not None:
                    rows.append(row)
    return rows


def write(out_dir: Path, rows: Sequence[Counterfactual]) -> dict:
    """Publish to output/counterfactual.csv and nowhere else.

    The destination is not a parameter and the column list cannot contain a
    reported-party column. Both are asserted here rather than left to review,
    for the same reason `estimate.write` asserts them: the one failure this
    module must make impossible is a model number landing in a column that means
    "the state said so".
    """
    forbidden = FORBIDDEN_COLUMNS & set(COUNTERFACTUAL_COLUMNS)
    if forbidden:
        raise CounterfactualError(
            "counterfactual.csv must never carry reported-party columns: "
            f"{sorted(forbidden)}"
        )
    path = Path(out_dir) / COUNTERFACTUAL_FILENAME
    return publish.publish_table(
        path, COUNTERFACTUAL_COLUMNS, COUNTERFACTUAL_KEY,
        [r.to_dict() for r in rows],
        # The content-loss guard exists to catch a truncated DOWNLOAD. This table
        # is a pure function of data already on disk, so a rerun producing a
        # different row is a corrected model, not a truncated fetch.
        guard=False,
    )


# --------------------------------------------------------------------------
# Ground truth
# --------------------------------------------------------------------------
# WHAT CAN POSSIBLY BE VALIDATED HERE, AND WHAT CANNOT
# ---------------------------------------------------
# There is no observed "compositional margin shift" anywhere in the world to
# check this against. Nobody publishes what the 2024 early electorate would have
# done in 2024 if it had been the whole electorate; the counterfactual is
# counterfactual. So the honest question is narrower and answerable:
#
#     WHEN THE COMPOSITION OF AN EARLY ELECTORATE DEMONSTRABLY CHANGED BETWEEN
#     TWO CYCLES, DOES THIS METHOD SEE IT?
#
# The states that report the party registration of their returned ballots give a
# directly measured compositional change for exactly the electorates being
# compared: (D-R registration of 2024's early ballots at day d) minus (the same
# for 2022's). That is the truth column. County geography is the predictor. The
# two are never both -- a party-registration shift is never fed into `shift_pp`,
# which is what keeps this test non-circular.
#
# The unit is not identical: a registration point is not a presidential margin
# point, and Kentucky's registration sits ten points Democratic of its
# presidential vote (docs/party-estimate.md). `estimate.validate` makes the same
# compromise for the same reason -- it is the only ground truth that exists, and
# it is the comparison a reader will make. Two things stop the unit gap from
# being an escape hatch:
#
#   * The null is the same object in the same unit. "The composition has not
#     changed" predicts zero, and zero is unit-free, so the COMPARISON between
#     method and null is fair even if neither error is in a perfect unit.
#   * `fit_scale` searches, leave-one-state-out, for the multiplier that would
#     convert one unit into the other. If a unit gap were all that was wrong, a
#     fitted scale would fix it. It does not: the fitted scale flips sign between
#     folds and every fold scores worse than the null.
# --------------------------------------------------------------------------
@dataclass
class ScoredDay:
    """One day on which a compositional change can be both modelled and measured."""

    cycle: int
    state: str
    days_to_election: int
    shift: float          # what county geography says moved, points
    truth: float          # what reported party registration says moved, points
    ballots: float
    reference_ballots: float


@dataclass
class Validation:
    """How far the modelled compositional shift landed from the measured one."""

    cycle: int
    state: str
    days: int
    final_shift: float
    final_truth: float
    final_error: float
    mean_abs_error: float
    null_mean_abs_error: float
    mean_abs_shift: float
    mean_abs_truth: float
    correlation: float | None
    sign_agreement: float
    #: The multiplier fitted WITHOUT this state, and what it scores with it.
    fitted_scale: float | None = None
    scaled_mean_abs_error: float | None = None
    held_out: bool = True

    @property
    def gain(self) -> float:
        """Points of accuracy the geography buys over "nothing changed"."""
        return self.null_mean_abs_error - self.mean_abs_error

    @property
    def scaled_gain(self) -> float | None:
        if self.scaled_mean_abs_error is None:
            return None
        return self.null_mean_abs_error - self.scaled_mean_abs_error


def mature_days(
    index: DayIndex, fraction: float = MATURE_FRACTION
) -> set[int]:
    """The days-out on which at least `fraction` of the series' vote was in.

    Before that the returns are mail-dominated and their composition is nothing
    like the eventual early electorate's; every method looks terrible there and
    the comparison says nothing about any of them. The last day is the floor, so
    a series is never scored on nothing.
    """
    final = index.final_ballots() or 1
    ripe = {
        dte for dte, ballots in index.counties.items()
        if sum(v for v in ballots.values() if v is not None) / final >= fraction
    }
    if ripe:
        return ripe
    return {min(index.counties)} if index.counties else set()


def score_panel(
    out_dir: Path,
    baseline: Baseline,
    *,
    states: Iterable[str] | None = None,
    tolerance: int = DTE_MATCH_TOLERANCE,
) -> dict[tuple[int, str], list[ScoredDay]]:
    """Every state-day where the shift can be modelled AND measured.

    Keyed by (cycle, state) because the SERIES is the unit: Maine contributes 121
    days and Kentucky 3, and pooling them by day would score Maine and call it a
    method. `estimate.fit_mail_selection` makes the same choice for the same
    reason.
    """
    out_dir = Path(out_dir)
    wanted = {s.upper() for s in states} if states else None
    panel: dict[tuple[int, str], list[ScoredDay]] = defaultdict(list)

    for state in available_states(out_dir):
        if wanted and state not in wanted:
            continue
        if not baseline.counties(state):
            continue
        series = read_series(out_dir, state)
        for cycle, reference_cycle in sorted(REFERENCE_CYCLE.items()):
            now, reference = series.get(cycle), series.get(reference_cycle)
            if now is None or reference is None or not reference.counties:
                continue
            ripe_now, ripe_ref = mature_days(now), mature_days(reference)
            for dte in sorted(now.counties, reverse=True):
                if dte not in ripe_now:
                    continue
                ref_dte = reference.at(dte, tolerance)
                if ref_dte is None or ref_dte not in ripe_ref:
                    continue
                here = composition_margin(now.counties[dte], baseline)
                there = composition_margin(reference.counties[ref_dte], baseline)
                if here is None or there is None:
                    continue
                truth_now = party_margin(now.state_rows.get(dte))
                truth_ref = party_margin(reference.state_rows.get(ref_dte))
                if truth_now is None or truth_ref is None:
                    continue
                panel[(cycle, state)].append(ScoredDay(
                    cycle=cycle, state=state, days_to_election=dte,
                    shift=here[0] - there[0],
                    truth=truth_now - truth_ref,
                    ballots=float(here[2]), reference_ballots=float(there[2]),
                ))
    for series_days in panel.values():
        series_days.sort(key=lambda d: -d.days_to_election)
    return dict(panel)


def fit_scale(series: Iterable[Sequence[ScoredDay]]) -> float | None:
    """The multiplier k minimising Σ (truth - k·shift)², series-weighted.

    Weighted least squares through the origin: each state-cycle counts once, not
    once per day. There is no intercept, because an intercept would be a constant
    national shift and the null already owns that.

    This exists to give the method its best possible case. If the only thing
    wrong with county geography were that its unit is presidential points and the
    truth's is registration points, a fitted k would absorb the difference.
    Returns None rather than a number when nothing is fittable.
    """
    numerator = denominator = 0.0
    for one in series:
        if not one:
            continue
        weight = 1.0 / len(one)
        for day in one:
            numerator += weight * day.shift * day.truth
            denominator += weight * day.shift * day.shift
    return numerator / denominator if denominator > 0 else None


def _correlation(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    n = len(xs)
    if n < 2:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = sum((x - mx) ** 2 for x in xs) ** 0.5
    vy = sum((y - my) ** 2 for y in ys) ** 0.5
    return cov / (vx * vy) if vx > 0 and vy > 0 else None


def validate(
    out_dir: Path,
    baseline: Baseline,
    *,
    states: Iterable[str] | None = None,
    tolerance: int = DTE_MATCH_TOLERANCE,
) -> list[Validation]:
    """Score the method against the only measured composition change there is.

    LEAVE ONE STATE OUT for the fitted scale, because a scale fitted on the same
    series it scores is marking its own homework, and `docs/regression.md` is the
    standing example in this repo of seven specifications that looked like they
    had found something until they were measured against the right null.
    """
    panel = score_panel(out_dir, baseline, states=states, tolerance=tolerance)
    results: list[Validation] = []
    for (cycle, state), days in sorted(panel.items()):
        if not days:
            continue
        others = [v for k, v in panel.items() if k[1] != state]
        scale = fit_scale(others) if others else None
        shifts = [d.shift for d in days]
        truths = [d.truth for d in days]
        errors = [abs(s - t) for s, t in zip(shifts, truths)]
        nulls = [abs(t) for t in truths]
        n = len(days)
        last = min(days, key=lambda d: d.days_to_election)
        results.append(Validation(
            cycle=cycle, state=state, days=n,
            final_shift=last.shift, final_truth=last.truth,
            final_error=last.shift - last.truth,
            mean_abs_error=sum(errors) / n,
            null_mean_abs_error=sum(nulls) / n,
            mean_abs_shift=sum(abs(s) for s in shifts) / n,
            mean_abs_truth=sum(nulls) / n,
            correlation=_correlation(shifts, truths),
            sign_agreement=sum(
                1 for s, t in zip(shifts, truths) if (s > 0) == (t > 0)
            ) / n,
            fitted_scale=scale,
            scaled_mean_abs_error=(
                None if scale is None
                else sum(abs(scale * s - t) for s, t in zip(shifts, truths)) / n
            ),
            held_out=scale is not None,
        ))
    return results


def format_validation(results: Sequence[Validation]) -> Iterator[str]:
    yield (f"{'cycle':>5s} {'st':3s} {'days':>4s} {'shift':>7s} {'truth':>7s} "
           f"{'final':>7s} {'MAE':>6s} {'null':>6s} {'gain':>6s} "
           f"{'|shift|':>7s} {'|truth|':>7s} {'r':>6s} {'sign':>5s} "
           f"{'k':>6s} {'k-MAE':>6s} {'k-gain':>7s}")
    for r in sorted(results, key=lambda r: (r.state, r.cycle)):
        yield (f"{r.cycle:5d} {r.state:3s} {r.days:4d} {r.final_shift:+7.2f} "
               f"{r.final_truth:+7.2f} {r.final_error:+7.2f} "
               f"{r.mean_abs_error:6.2f} {r.null_mean_abs_error:6.2f} "
               f"{r.gain:+6.2f} {r.mean_abs_shift:7.2f} {r.mean_abs_truth:7.2f} "
               + (f"{r.correlation:+6.2f}" if r.correlation is not None else "   n/a")
               + f" {r.sign_agreement:5.0%} "
               + (f"{r.fitted_scale:+6.2f}" if r.fitted_scale is not None else "   n/a")
               + (f" {r.scaled_mean_abs_error:6.2f}"
                  if r.scaled_mean_abs_error is not None else "    n/a")
               + (f" {r.scaled_gain:+7.2f}" if r.scaled_gain is not None else "     n/a")
               + ("" if r.held_out else "  (in sample: no other state to fit on)"))
    if not results:
        yield ("(no state-cycle in output/ has BOTH a county early series in two "
               "consecutive cycles AND a reported party split to score against)")
        return

    n = len(results)
    mae = sum(r.mean_abs_error for r in results) / n
    null = sum(r.null_mean_abs_error for r in results) / n
    gain = sum(r.gain for r in results) / n
    scaled = [r for r in results if r.scaled_mean_abs_error is not None]
    yield ""
    yield (f"mean MAE = {mae:.2f} pp   null (composition unchanged) = {null:.2f} pp   "
           f"gain = {gain:+.2f} pp")
    yield (f"county geography moves {sum(r.mean_abs_shift for r in results) / n:.2f} pp "
           f"while the composition it stands in for moves "
           f"{sum(r.mean_abs_truth for r in results) / n:.2f} pp")
    if scaled:
        k_gain = sum(r.scaled_gain for r in scaled) / len(scaled)
        yield (f"leave-one-state-out fitted scale: "
               + ", ".join(f"{r.state} k={r.fitted_scale:+.2f}" for r in scaled)
               + f"   mean gain {k_gain:+.2f} pp")
    yield ""
    yield (f"VERDICT: {'SHIPS' if gain >= MIN_GAIN else 'NOTHING SHIPS'} "
           f"(bar is {MIN_GAIN:+.2f} pp of gain over the no-change null; "
           f"measured {gain:+.2f})")
    yield ("columns: shift/truth/final are the LAST matched day; MAE/null over "
           "days with >=25% of each series' early vote in; truth is the change in "
           "the state's OWN reported party REGISTRATION of the same ballots -- a "
           "different unit, which is why k is fitted and reported; sign = share of "
           "days the modelled shift agrees in direction with the measured one.")


def cmd_counterfactual(args) -> int:
    """`python -m ev counterfactual`. Never part of the six-hourly ingest."""
    out_dir = Path(args.output) if args.output else ROOT / "output"
    baseline = load_baseline(args.baseline)

    if args.validate:
        for line in format_validation(validate(out_dir, baseline, states=args.state)):
            print(line)
        return 0

    rows = build(out_dir, baseline, states=args.state,
                 cycles=args.cycle if args.cycle else None)
    by_cycle: dict[int, int] = defaultdict(int)
    for row in rows:
        by_cycle[row.cycle] += 1

    if args.dry_run:
        print(f"DRY RUN  {len(rows)} counterfactual row(s), nothing written")
        for row in rows[-10:]:
            d = row.to_dict()
            print(f"  {d['cycle']} {d['state']} {d['date']} (d-{d['days_to_election']}) "
                  f"vs {d['reference_cycle']} {d['reference_date']}  "
                  f"shift {d['shift_pp']} [{d['shift_lo']}, {d['shift_hi']}]  "
                  f"{d['confidence']}  dims_used={d['dims_used']}")
        return 0

    if not rows:
        print("no like-for-like state-days; counterfactual.csv left untouched")
        return 0

    info = write(out_dir, rows)
    print(f"counterfactual.csv: {info['rows']} rows "
          f"({len(rows)} computed this run: "
          + ", ".join(f"{c} {n}" for c, n in sorted(by_cycle.items())) + ")")
    current = max(CYCLES)
    if not by_cycle.get(current):
        print(f"  NOTE: zero {current} rows. A {current} state-day gets a row only "
              f"when the same state has a {REFERENCE_CYCLE.get(current)} county "
              f"early series at the same days-to-election (±{DTE_MATCH_TOLERANCE}). "
              f"No fallback to the full-state comparison exists, by design.")
    return 0
