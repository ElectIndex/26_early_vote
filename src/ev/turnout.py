"""Projecting each state's final TURNOUT from its early-vote pace, and its score.

WHAT THIS ASKS
--------------
"Ballots returned so far" and "ballots cast in total" are the same quantity
measured at two times. So unlike the three models this repo has already declined,
the thing being predicted here is a COUNT rather than a preference, and the
relationship is mechanical rather than behavioural:

    fitted_fraction(state, days_out)  =  the share of that state's FINAL turnout
                                         that had been cast by that day out

Fit it on a cycle where both ends are known -- the early series in
`ev_state_daily.csv` and the certified total in `results_state.csv` -- and then
invert it:

    projected_turnout  =  ballots so far  /  fitted_fraction(state, days_out)

Per state, never a national curve. Colorado has 90% of its vote in before
Election Day and New York has almost none, so a pooled curve is not a model of
anything.

THE DECOMPOSITION, WHICH IS WHERE THE ANSWER LIVES
--------------------------------------------------
The fitted fraction is a product of two things that behave completely
differently, and this module publishes both so the reader can see which half is
which:

    fitted_fraction(d)  =  coverage(d)  x  early_share

    coverage(d)     the SHAPE: ballots in by day d as a share of that cycle's
                    completed early vote. Measured, it transfers across cycles
                    well -- a mean of 5.4 points of the early vote (2.3 in Maine,
                    8.2 in Tennessee) between 2022 and 2024, across the five
                    states with a usable curve in both.

    early_share     the LEVEL: the completed early vote as a share of total
                    turnout. Measured, it does NOT transfer. It rose by 27% to
                    72% RELATIVE between 2022 and 2024 in the four states where
                    both can be computed.

The projection is `ballots / (coverage x early_share)`, so its relative error is
exactly `early_share(this cycle) / early_share(reference cycle) - 1`. The half
that does not transfer is the whole of the error. See `docs/turnout.md`.

THE NULLS, WHICH ARE THE WHOLE TEST
------------------------------------
    null: prior turnout    "this state's turnout will equal its last comparable
                            election's turnout." Costs nothing, needs no data.

    null: uniform ratio    "the country's turnout moved by X this cycle; apply X
                            to every state." Computed LEAVE-ONE-STATE-OUT, so it
                            never sees the state it is predicting.

The second null exists for the reason `docs/regression.md` gives: the first is
easy to beat for the wrong reason, because any method that knows roughly how a
cycle differs from the last one beats "assume nothing changed" without the early
vote contributing anything. A method that does not beat BOTH has measured nothing
about the early vote. This one beats neither. See `validate` and the docs.

Note that the model itself has NO pooled parameter -- the fitted fraction is the
state's own reference curve and nothing else -- so leave-one-state-out changes it
not at all. It is the nulls that are computed leave-one-state-out, which is the
generous direction.

WHERE OUTPUT GOES
-----------------
`output/turnout.csv`, one row per state per day, and nowhere else. Never into
`ev_state_daily.csv`'s reported columns; `write()` can address one filename and
there is a test that hashes an output tree to prove it.

BLANKS
------
THE BLANK RULE from `schema.py`, on the read and the write side. A state with no
comparable reference cycle produces NO ROW -- not a projection equal to last
time, not a zero. `early_share` is blank, never zero, wherever the two published
totals do not both exist. Every refusal is counted and named by the command.
"""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterator, Sequence

from . import publish, results
from .calendar import days_to_election

log = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Published contract
# --------------------------------------------------------------------------
TURNOUT_FILENAME = "turnout.csv"

#: The only file this module may write. A constant so the test that hashes an
#: output/ tree before and after a run has something to assert against.
WRITABLE = (TURNOUT_FILENAME,)

TURNOUT_COLUMNS = [
    "cycle", "state", "date", "days_to_election",
    "ballots_so_far",
    "reference_cycle", "reference_date", "reference_days_to_election",
    "reference_kind", "reference_ballots", "reference_early_final",
    "reference_turnout", "reference_office",
    # The two halves of the fitted fraction, published apart. See the docstring.
    "coverage", "reference_early_share", "fitted_fraction",
    "projected_turnout", "projected_lo", "projected_hi",
    "projected_final_early",
    # The null's prediction, on the row, so the comparison is checkable here.
    "null_turnout", "projected_vs_null",
    # Filled only for a cycle that already has a certified result, which makes
    # every checkable row say how wrong it was.
    "actual_turnout", "error_pct", "null_error_pct",
    "dims_used", "dims_reported",
    "reference_days", "reference_complete", "day_gap",
    "confidence", "method", "source_name", "retrieved_at",
]

TURNOUT_KEY = ("cycle", "state", "date")

#: Columns this table may never carry. Naming them here rather than importing
#: schema's list is the point: this is the assertion that a projection cannot be
#: mistaken for a reported count.
FORBIDDEN_COLUMNS = frozenset({
    "ballots_total", "ballots_new", "mail_requested", "mail_returned",
    "inperson", "party_dem", "party_rep", "party_oth", "party_npa",
})

#: Identifies the arithmetic in every row, so a row written by an older version
#: is recognisable after the fact.
METHOD = "ballots-over-reference-fraction"

SOURCE_NAME = "electindex-turnout/reference-curve"

#: What went INTO the fitted fraction. One dimension: the statewide cumulative
#: ballot count, on the days-to-election axis. `dims_reported` names what the
#: state publishes beside it, and the distance between the two columns is an
#: honest summary of how little of the data this model uses.
DIMS_USED = "ballots_total|days_to_election"

_DP = 6


# --------------------------------------------------------------------------
# Constants, and the reasoning for each
# --------------------------------------------------------------------------
#: Cycles with both an early-vote series and a certified statewide result.
FIT_CYCLES = (2022, 2024)

#: How far a cycle's comparable predecessor sits. A midterm's comparable
#: election is the previous midterm and a presidential year's is the previous
#: presidential year -- FOUR years, never two. 2022 and 2024 differ in turnout
#: level by about 45%, so a two-year reference is not a reference, it is a
#: different kind of election. `validate` deliberately uses the two-year pair
#: because it is the only out-of-sample test that exists; `build` never does.
COMPARABLE_OFFSET = 4

#: How far the reference series' last reported day may sit from Election Day and
#: still count as a COMPLETED early vote. Same value and same reasoning as
#: `regress.SNAPSHOT_MAX_DTE`: Maryland at five days out and Kentucky, Texas and
#: Tennessee at four or five are at their own finish line, because their windows
#: genuinely close before Election Day. South Carolina 2022, which stops
#: eighteen days out at 16,975 ballots, is a series our archive stopped
#: following, and using it as a denominator would understate that state's early
#: vote by two orders of magnitude and inflate every projection built on it.
SNAPSHOT_MAX_DTE = 7

#: Cross-cycle days are matched by DAYS TO ELECTION, never by calendar date --
#: Election Day moves (Nov 8 2022, Nov 5 2024, Nov 3 2026). Two series rarely
#: land on the same day out, so the match allows slack and takes the closest day
#: inside it.
#:
#: ONE day, not the three `regress.py` allows, and the difference is deliberate.
#: regress matches a COMPOSITION, which drifts a point or two a day. This matches
#: a LEVEL, and a state's cumulative early vote routinely grows 20-40% in a day
#: once in-person voting opens -- so a three-day mismatch is a 60% error in the
#: denominator before the model has done anything. Kentucky is the worked case in
#: docs/turnout.md: against a single-day 2022 reference, a three-day tolerance
#: makes its projection travel 653% across six days.
DTE_MATCH_TOLERANCE = 1

#: A row is produced only once this share of the reference cycle's early vote was
#: in at the matched day. Below it the division is not a projection: North
#: Carolina's 2022 curve is 17 ballots at 60 days out, so a 2026 count divided by
#: that fraction multiplies eight ballots by a five-figure factor and prints a
#: turnout. A day under this floor is data we have; it is not an answer.
MIN_COVERAGE = 0.25

#: The band's half-width, as a FRACTION OF THE PROJECTION. Empirical, not
#: statistical: it is the mean absolute relative error measured by `validate` on
#: the only out-of-sample test this repo can run (fit on 2022, test on 2024, four
#: states, days past MIN_COVERAGE). It does not shrink as ballots come in,
#: because the error is not sampling noise -- it is the early-vote share of
#: turnout moving between two elections, and that is a fixed property of the pair
#: of cycles, not of how much has been counted.
#:
#: It is a CROSS-TYPE measurement standing in for a same-type one, because this
#: repo holds no same-type pair to measure. Saying so is the honest option;
#: quoting a tighter band nobody has measured is not one.
#:
#: `test_model_error_matches_the_measured_validation` refits it from output/ and
#: fails if the data moves away from it.
MODEL_ERROR = 0.55

#: How far the refit may move before that test fails. Wide, because it is four
#: states: a single backfilled state can shift the mean by ten points, and a
#: tolerance tighter than the sample can support would be theatre.
MODEL_ERROR_TOLERANCE = 0.10

#: ...and the reference-side one, for the case the band cannot see: a reference
#: series with only a handful of days cannot be matched precisely, so its
#: denominator is a different point of the window from the one being projected.
#: Added, not combined in quadrature -- this feature is easy enough to misread
#: that the conservative arithmetic is the right one.
DAY_GAP_ERROR = 0.30

#: Fewer reference days than this and the curve is a point, not a curve.
MIN_REFERENCE_DAYS = 4

#: Percent of turnout. A projection has to beat BOTH nulls by at least this much
#: out of sample before it is worth putting in front of a reader. One point,
#: matching `regress.MIN_GAIN` and `counterfactual.MIN_GAIN` -- using a softer
#: bar here than the one already applied to three sibling features would be
#: arguing backwards from the answer.
MIN_GAIN = 1.0

#: With a perfect day match the half-width is exactly MODEL_ERROR, so the
#: confidence threshold has to sit strictly above it or it flips on floating
#: point noise.
MAX_BAND_HALF_WIDTH = MODEL_ERROR + 0.02

KIND_PRESIDENTIAL = "presidential"
KIND_MIDTERM = "midterm"

REFERENCE_SAME_TYPE = "same-type"
REFERENCE_CROSS_TYPE = "cross-type"


class TurnoutError(RuntimeError):
    """Raised rather than guessing. There is no fallback below a projection."""


def _utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _int(cell: str | None) -> int | None:
    """A blank cell is None, never 0 -- THE BLANK RULE on the read side."""
    raw = (cell or "").strip()
    if not raw:
        return None
    try:
        return int(float(raw))
    except ValueError:
        return None


def _cell(value: float | None, places: int = _DP) -> str:
    if value is None:
        return ""
    text = f"{round(value, places):.{places}f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def _count(value: float | None) -> str:
    return "" if value is None else str(int(round(value)))


def election_kind(cycle: int) -> str:
    """Presidential years are the ones divisible by four."""
    return KIND_PRESIDENTIAL if int(cycle) % 4 == 0 else KIND_MIDTERM


def comparable_cycle(cycle: int) -> int:
    """The last election of the SAME kind. 2022 for 2026, 2020 for 2024."""
    return int(cycle) - COMPARABLE_OFFSET


def reference_kind(target: int, reference: int) -> str:
    return (REFERENCE_SAME_TYPE
            if election_kind(target) == election_kind(reference)
            else REFERENCE_CROSS_TYPE)


# --------------------------------------------------------------------------
# The two inputs
# --------------------------------------------------------------------------
@dataclass
class Curve:
    """One state-cycle's cumulative early vote, keyed by days to election."""

    cycle: int
    state: str
    #: days_to_election -> (date, ballots). Positive is before Election Day.
    days: dict[int, tuple[str, int]] = field(default_factory=dict)

    @property
    def final_dte(self) -> int | None:
        """The last day (smallest non-negative days-out) that reported a total.

        Non-negative only: a post-election row is a certified restatement that
        includes mail counted after Election Day, which is a different object
        from the live figure this projection is built on.
        """
        candidates = [d for d in self.days if d >= 0]
        return min(candidates) if candidates else None

    @property
    def complete(self) -> bool:
        """Did this series follow the state to the end of its own window?

        The gate on using it as a DENOMINATOR. See SNAPSHOT_MAX_DTE.
        """
        dte = self.final_dte
        return dte is not None and dte <= SNAPSHOT_MAX_DTE

    @property
    def final_early(self) -> int | None:
        dte = self.final_dte
        return self.days[dte][1] if dte is not None else None

    @property
    def n_days(self) -> int:
        return sum(1 for d in self.days if d >= 0)

    def at(self, dte: int, tolerance: int = DTE_MATCH_TOLERANCE
           ) -> tuple[int, str, int] | None:
        """(days_out, date, ballots) closest to `dte`, within `tolerance` days.

        Cross-cycle comparison aligns HERE and only here. Pre-election days
        only, for the same reason `final_dte` uses them.
        """
        best: tuple[int, str, int] | None = None
        best_gap = tolerance + 1
        for day, (iso, ballots) in self.days.items():
            if day < 0:
                continue
            gap = abs(day - dte)
            if gap < best_gap:
                best, best_gap = (day, iso, ballots), gap
        return best


def read_curves(out_dir: Path) -> dict[tuple[int, str], Curve]:
    """Load `ev_state_daily.csv` into one Curve per (cycle, state)."""
    path = Path(out_dir) / "ev_state_daily.csv"
    if not path.exists():
        return {}
    table: dict[tuple[int, str], Curve] = {}
    with path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            ballots = _int(row.get("ballots_total"))
            if ballots is None:
                # Blank is "the state did not report", not zero. A blank day is
                # simply absent from the curve.
                continue
            try:
                cycle = int(row["cycle"])
                dte = int(row["days_to_election"])
            except (KeyError, TypeError, ValueError):
                continue
            state = (row.get("state") or "").strip().upper()
            day = (row.get("date") or "").strip()
            if not state or not day:
                continue
            curve = table.setdefault((cycle, state), Curve(cycle, state))
            prior = curve.days.get(dte)
            # Two rows on the same days-out can only happen across a restatement;
            # keep the one reporting more ballots, never the sum.
            if prior is None or ballots > prior[1]:
                curve.days[dte] = (day, ballots)
    return table


def read_turnout(out_dir: Path) -> dict[tuple[int, str], tuple[int, str]]:
    """{(cycle, state): (total_votes, office)} from `results_state.csv`.

    THE DENOMINATOR, AND ITS ONE HONEST CAVEAT. `total_votes` is the votes cast
    in ONE RACE, not the ballots cast in the election: a voter who skips the
    Senate line is in the second number and not the first. The gap is roughly
    1-2% for president and larger down-ballot, and it is the best denominator
    this repo holds -- `ev_state_meta.csv`'s `turnout_2022_total` and
    `turnout_2024_total` columns exist and are empty in every state.
    Presidential is preferred over Senate where both exist, because it is the
    top of the ticket and has the smallest undervote.
    """
    path = Path(out_dir) / "results_state.csv"
    if not path.exists():
        return {}
    table: dict[tuple[int, str], tuple[int, str]] = {}
    with path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            votes = _int(row.get("total_votes"))
            if votes is None or votes <= 0:
                continue
            try:
                cycle = int(row["cycle"])
            except (KeyError, TypeError, ValueError):
                continue
            state = (row.get("state") or "").strip().upper()
            office = (row.get("office") or "").strip()
            if not state:
                continue
            key = (cycle, state)
            if key not in table or office == results.OFFICE_PRESIDENT:
                table[key] = (votes, office)
    return table


def read_dims(out_dir: Path) -> dict[str, str]:
    """`dims_available` per state from `ev_state_meta.csv`. Read, never written."""
    path = Path(out_dir) / "ev_state_meta.csv"
    if not path.exists():
        return {}
    out: dict[str, str] = {}
    with path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            state = (row.get("state") or "").strip().upper()
            if state:
                out[state] = (row.get("dims_available") or "").strip()
    return out


# --------------------------------------------------------------------------
# The reference: one state's fitted curve from a past cycle
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Reference:
    """A completed state-cycle: its curve, its early total, its turnout.

    This IS the fitted model. There is no pooling and no smoothing -- the fitted
    fraction at day d is the state's own reported position on that day out,
    divided by its own certified turnout.
    """

    curve: Curve
    early_final: int
    turnout: int
    office: str

    @property
    def state(self) -> str:
        return self.curve.state

    @property
    def cycle(self) -> int:
        return self.curve.cycle

    @property
    def early_share(self) -> float | None:
        """The completed early vote as a share of total turnout. THE LEVEL.

        Blank rather than zero wherever either published total is missing, which
        is what `read_turnout` and `early_finals` already guarantee by refusing
        to answer -- so this property never sees a zero denominator.
        """
        return self.early_final / self.turnout if self.turnout else None

    def matched(self, dte: int) -> tuple[int, str, int] | None:
        return self.curve.at(dte)

    def fraction(self, dte: int) -> tuple[float, float, int, str, int] | None:
        """(fitted_fraction, coverage, matched_dte, matched_date, ballots).

        `coverage` is the SHAPE -- the share of this reference cycle's own early
        vote that was in by that day. `fitted_fraction` is that multiplied by the
        LEVEL. None when there is no day inside the tolerance.
        """
        got = self.matched(dte)
        if got is None or not self.early_final:
            return None
        ref_dte, ref_date, ref_ballots = got
        coverage = ref_ballots / self.early_final
        share = self.early_share
        if share is None:
            return None
        return coverage * share, coverage, ref_dte, ref_date, ref_ballots


def early_finals(out_dir: Path) -> dict[tuple[int, str], int]:
    """Each state's COMPLETED early vote per past cycle.

    `results.early_totals` first -- a hand-entered official figure from
    `ev_state_meta.csv`, else `publish.derive_prior_finals` -- so this module
    cannot invent a second definition of "final" for a reader to reconcile.

    Where that answers nothing, the series' own last reported day is used, but
    only when the series is COMPLETE by SNAPSHOT_MAX_DTE. `derive_prior_finals`
    requires a row dated Election Day, which is stricter than it needs to be for
    the four states whose early-vote window genuinely closes days earlier --
    Maryland at five days out, Kentucky, Texas and Tennessee at four or five --
    and excluding them would throw away half of the only midterm this repo has.
    South Carolina 2022, which stops eighteen days out, is refused by both.
    """
    finals: dict[tuple[int, str], int] = {}
    for (cycle, state), value in results.early_totals(Path(out_dir)).items():
        try:
            finals[(int(cycle), state.upper())] = int(value)
        except (TypeError, ValueError):
            continue
    return finals


def build_references(out_dir: Path,
                     curves: dict[tuple[int, str], Curve] | None = None,
                     turnout: dict[tuple[int, str], tuple[int, str]] | None = None,
                     ) -> tuple[dict[tuple[int, str], Reference], list[str]]:
    """Every (cycle, state) that can serve as a fitted curve. And why the rest cannot."""
    out_dir = Path(out_dir)
    curves = read_curves(out_dir) if curves is None else curves
    turnout = read_turnout(out_dir) if turnout is None else turnout
    published = early_finals(out_dir)

    refs: dict[tuple[int, str], Reference] = {}
    refused: list[str] = []
    for key, curve in sorted(curves.items()):
        cycle, state = key
        if cycle not in FIT_CYCLES:
            continue
        if not curve.complete:
            refused.append(
                f"{cycle} {state}: series stops {curve.final_dte} days out, so its "
                f"last figure is not a completed early vote"
            )
            continue
        final = published.get(key)
        if final is None:
            final = curve.final_early
        total = turnout.get(key)
        if total is None:
            refused.append(
                f"{cycle} {state}: no certified statewide total (no {cycle} "
                f"president or Senate race in results_state.csv)"
            )
            continue
        if final is None or final <= 0:
            refused.append(f"{cycle} {state}: no completed early-vote total")
            continue
        refs[key] = Reference(curve=curve, early_final=final,
                              turnout=total[0], office=total[1])
    return refs, refused


# --------------------------------------------------------------------------
# One projection
# --------------------------------------------------------------------------
@dataclass
class Projection:
    """One state-day's projected final turnout. Every field needed to distrust it."""

    cycle: int
    state: str
    day: date
    ballots_so_far: int
    reference: Reference
    reference_dte: int
    reference_date: str
    reference_ballots: int
    coverage: float
    fitted_fraction: float
    day_gap: int
    dims_reported: str = ""
    actual_turnout: int | None = None
    method: str = METHOD
    source_name: str = SOURCE_NAME
    retrieved_at: str = ""

    def key(self) -> tuple:
        return (int(self.cycle), self.state.upper(), self.day.isoformat())

    # -- the projection -------------------------------------------------
    @property
    def projected_turnout(self) -> float:
        return self.ballots_so_far / self.fitted_fraction

    @property
    def projected_final_early(self) -> float:
        """The half of the arithmetic that does transfer, published on its own.

        `ballots / coverage` is a projection of this state's COMPLETED EARLY
        VOTE, and it uses only the shape of the reference curve. The turnout
        figure is this number divided by the reference cycle's early share,
        which is the half that does not transfer.
        """
        return self.ballots_so_far / self.coverage

    @property
    def half_width(self) -> float:
        """Relative half-width of the band. See MODEL_ERROR and DAY_GAP_ERROR."""
        return MODEL_ERROR + (DAY_GAP_ERROR if self.day_gap else 0.0)

    # The band is MULTIPLICATIVE, not additive, because the error is a ratio:
    # the projection is `ballots / fitted_fraction`, so getting the fraction
    # wrong by a factor scales the answer by that factor. A symmetric +/-x% band
    # would be too tight on the high side and too loose on the low side of the
    # same misjudgement. It is a band on a ratio, so it is symmetric in the ratio.
    @property
    def projected_lo(self) -> float:
        return self.projected_turnout / (1.0 + self.half_width)

    @property
    def projected_hi(self) -> float:
        return self.projected_turnout * (1.0 + self.half_width)

    def band_contains(self, value: int | float | None) -> bool | None:
        """Does the published interval contain `value`? None when unknowable."""
        if not value:
            return None
        return self.projected_lo <= value <= self.projected_hi

    @property
    def null_turnout(self) -> int:
        """The null's prediction: this state's reference-cycle turnout."""
        return self.reference.turnout

    @property
    def projected_vs_null(self) -> float:
        return 100.0 * (self.projected_turnout / self.null_turnout - 1.0)

    @property
    def error_pct(self) -> float | None:
        """Signed error as a percent of ACTUAL turnout. Blank until certified."""
        if not self.actual_turnout:
            return None
        return 100.0 * (self.projected_turnout / self.actual_turnout - 1.0)

    @property
    def null_error_pct(self) -> float | None:
        if not self.actual_turnout:
            return None
        return 100.0 * (self.null_turnout / self.actual_turnout - 1.0)

    @property
    def reference_kind(self) -> str:
        return reference_kind(self.cycle, self.reference.cycle)

    @property
    def confidence(self) -> str:
        """Never "high", and there is a test that says so.

        The band carries the numeric uncertainty; this label is about whether the
        INPUTS are complete enough for the band to mean anything. No amount of
        coverage repairs a method that does not beat its null, so the top of this
        scale is "medium" -- the same ceiling `estimate.py` and
        `counterfactual.py` set, for the same reason.
        """
        thin_reference = self.reference.curve.n_days < MIN_REFERENCE_DAYS
        cross = self.reference_kind == REFERENCE_CROSS_TYPE
        wide = self.half_width > MAX_BAND_HALF_WIDTH
        low_coverage = self.coverage < MIN_COVERAGE
        if thin_reference or cross or wide or low_coverage or self.day_gap:
            return "low"
        return "medium"

    def to_dict(self) -> dict[str, str]:
        ref = self.reference
        return {
            "cycle": str(int(self.cycle)),
            "state": self.state.upper(),
            "date": self.day.isoformat(),
            "days_to_election": str(days_to_election(self.cycle, self.day)),
            "ballots_so_far": str(int(self.ballots_so_far)),
            "reference_cycle": str(int(ref.cycle)),
            "reference_date": self.reference_date,
            "reference_days_to_election": str(int(self.reference_dte)),
            "reference_kind": self.reference_kind,
            "reference_ballots": str(int(self.reference_ballots)),
            "reference_early_final": str(int(ref.early_final)),
            "reference_turnout": str(int(ref.turnout)),
            "reference_office": ref.office,
            "coverage": _cell(self.coverage),
            "reference_early_share": _cell(ref.early_share),
            "fitted_fraction": _cell(self.fitted_fraction),
            "projected_turnout": _count(self.projected_turnout),
            "projected_lo": _count(self.projected_lo),
            "projected_hi": _count(self.projected_hi),
            "projected_final_early": _count(self.projected_final_early),
            "null_turnout": str(int(self.null_turnout)),
            "projected_vs_null": _cell(self.projected_vs_null, 4),
            "actual_turnout": _count(self.actual_turnout),
            "error_pct": _cell(self.error_pct, 4),
            "null_error_pct": _cell(self.null_error_pct, 4),
            "dims_used": DIMS_USED,
            "dims_reported": self.dims_reported,
            "reference_days": str(int(ref.curve.n_days)),
            "reference_complete": "true" if ref.curve.complete else "false",
            "day_gap": str(int(self.day_gap)),
            "confidence": self.confidence,
            "method": self.method,
            "source_name": self.source_name,
            "retrieved_at": self.retrieved_at or _utcnow(),
        }


def project_day(cycle: int, state: str, day: date, ballots: int,
                reference: Reference, dte: int, *,
                dims_reported: str = "", actual_turnout: int | None = None,
                retrieved_at: str | None = None) -> Projection | None:
    """One state-day, or None when the reference cannot answer for that day.

    None -- not a fallback, not a zero. A day the reference curve does not reach,
    or reaches with under MIN_COVERAGE of its own early vote in, is a day this
    method has nothing to say about.
    """
    if ballots <= 0:
        # A day on which nobody has voted yet is a real, reported zero, and the
        # arithmetic would happily return a projected turnout of zero. That is
        # not a projection of no turnout; it is no answer. Florida's first two
        # 2026 rows are exactly this.
        return None
    got = reference.fraction(dte)
    if got is None:
        return None
    fitted, coverage, ref_dte, ref_date, ref_ballots = got
    if coverage < MIN_COVERAGE or fitted <= 0:
        return None
    return Projection(
        cycle=int(cycle), state=state.upper(), day=day,
        ballots_so_far=int(ballots), reference=reference,
        reference_dte=ref_dte, reference_date=ref_date,
        reference_ballots=ref_ballots,
        coverage=coverage, fitted_fraction=fitted,
        day_gap=abs(ref_dte - dte),
        dims_reported=dims_reported, actual_turnout=actual_turnout,
        retrieved_at=retrieved_at or _utcnow(),
    )


# --------------------------------------------------------------------------
# The run
# --------------------------------------------------------------------------
def build(out_dir: Path, *, cycles: Sequence[int] | None = None,
          states: Sequence[str] | None = None,
          cross_type: bool = False) -> tuple[list[Projection], list[str]]:
    """Project every state-day that has a comparable reference cycle.

    THE RULE THAT DECIDES WHICH REFERENCE IS ALLOWED, and it is the one thing in
    this module that stops a measured-wrong number reaching a reader as a
    forecast:

      * A cycle still IN PROGRESS -- no certified result of its own -- may only
        be projected from the previous election of the SAME KIND: 2022 for 2026.
        A midterm projected off a presidential curve is not a weaker answer, it
        is a wrong one, and `validate` puts a number on how wrong (54% of
        turnout). Where no same-type reference exists there is NO ROW, not a
        fallback and not a zero.

      * A cycle that is already CERTIFIED may use the nearest available
        reference, because such a row is a retrospective audit rather than a
        projection: it carries `actual_turnout` and `error_pct` in the same row,
        so it says how wrong it was on its own face. This is what fills
        `output/turnout.csv` today, and every one of those rows is labelled
        `reference_kind = cross-type` and `confidence = low`.

    `cross_type=True` lifts the first rule. It exists so the measurement can be
    reproduced, it is off by default, and docs/turnout.md says why.

    Returns (projections, refused) where `refused` names every state-day that
    was dropped and why. A dropped case is data we do not have, and saying which
    is the difference between a small sample and a silently biased one.
    """
    out_dir = Path(out_dir)
    curves = read_curves(out_dir)
    turnout = read_turnout(out_dir)
    dims = read_dims(out_dir)
    references, refused = build_references(out_dir, curves, turnout)

    wanted_cycles = {int(c) for c in cycles} if cycles else None
    wanted_states = {s.upper() for s in states} if states else None
    stamp = _utcnow()

    rows: list[Projection] = []
    for (cycle, state), curve in sorted(curves.items()):
        if wanted_cycles and cycle not in wanted_cycles:
            continue
        if wanted_states and state not in wanted_states:
            continue

        actual = turnout.get((cycle, state))
        reference = references.get((comparable_cycle(cycle), state))
        if reference is None and (cross_type or actual is not None):
            reference = references.get((cycle - 2, state))
        if reference is None:
            refused.append(
                f"{cycle} {state}: no {comparable_cycle(cycle)} reference curve "
                f"with a certified total; a {election_kind(cycle)} in progress "
                f"cannot be projected off a {election_kind(cycle - 2)} curve"
            )
            continue

        for dte in sorted((d for d in curve.days if d >= 0), reverse=True):
            iso, ballots = curve.days[dte]
            got = project_day(
                cycle, state, date.fromisoformat(iso), ballots, reference, dte,
                dims_reported=dims.get(state, ""),
                actual_turnout=actual[0] if actual else None,
                retrieved_at=stamp,
            )
            if got is not None:
                rows.append(got)
    rows.sort(key=lambda r: r.key())
    return rows, refused


def rollup(rows: Sequence[Projection], cycle: int,
           tracked: Sequence[str] | None = None) -> dict | None:
    """A national total over the states that QUALIFY, never a partial sum.

    Returns the latest day's projection summed across states, alongside how many
    states that is out of how many are tracked and what share of the reference
    cycle's turnout they represent. A partial sum presented as a national figure
    is the specific dishonesty this function exists to make impossible: the
    caller gets the coverage numbers in the same dict as the total, and the
    command prints them together.
    """
    latest: dict[str, Projection] = {}
    for row in rows:
        if row.cycle != int(cycle):
            continue
        prior = latest.get(row.state)
        if prior is None or row.day > prior.day:
            latest[row.state] = row
    if not latest:
        return None
    included = sorted(latest)
    reference_sum = sum(r.reference.turnout for r in latest.values())
    return {
        "cycle": int(cycle),
        "states": included,
        "states_included": len(included),
        "states_tracked": len(tracked) if tracked else None,
        "projected_turnout": sum(r.projected_turnout for r in latest.values()),
        "projected_lo": sum(r.projected_lo for r in latest.values()),
        "projected_hi": sum(r.projected_hi for r in latest.values()),
        "reference_turnout": reference_sum,
        "ballots_so_far": sum(r.ballots_so_far for r in latest.values()),
    }


# --------------------------------------------------------------------------
# Publish
# --------------------------------------------------------------------------
def write(out_dir: Path, rows: Sequence[Projection]) -> dict:
    """Publish to output/turnout.csv and nowhere else.

    The destination is not a parameter and the column list cannot contain a
    reported column from `ev_state_daily.csv`. Both are asserted here rather than
    left to review, because the one failure this module must make impossible is a
    projection landing in a column that means "the state counted this".
    """
    forbidden = FORBIDDEN_COLUMNS & set(TURNOUT_COLUMNS)
    if forbidden:
        raise TurnoutError(
            f"turnout.csv must never carry a reported count column: {sorted(forbidden)}"
        )
    if TURNOUT_FILENAME not in WRITABLE:
        # Not reachable, and that is the point: the guarantee is stated in code
        # so a future edit that adds a destination has to add it to WRITABLE,
        # where the test is looking.
        raise TurnoutError(f"turnout may not write {TURNOUT_FILENAME}")

    path = Path(out_dir) / TURNOUT_FILENAME
    ordered = sorted((r.to_dict() for r in rows),
                     key=lambda r: tuple(r.get(c, "") for c in TURNOUT_KEY))
    return publish.publish_table(
        path, TURNOUT_COLUMNS, TURNOUT_KEY, ordered,
        # The content-loss guard exists to catch a truncated DOWNLOAD. This table
        # is a pure function of data already on disk, so a rerun that produces a
        # different row is a corrected model, not a truncated fetch.
        guard=False,
    )


# --------------------------------------------------------------------------
# The score: the only out-of-sample test this repo can run
# --------------------------------------------------------------------------
@dataclass
class Validation:
    """One state, projected across a whole window by a curve from another cycle."""

    fit_cycle: int
    test_cycle: int
    state: str
    days: int
    mae_model: float          # percent of actual turnout
    mae_null_prior: float
    mae_null_uniform: float
    last_dte: int
    last_projected: float
    actual: int
    null: int
    reference_early_share: float
    actual_early_share: float | None
    projection_travel: float  # percent, max/min across the scored window
    shape_mae: float | None   # points of the early vote
    reference_days: int = 0
    #: Share of this state's scored days whose published interval contains the
    #: certified turnout. An interval that does not cover is not a caveat on the
    #: number, it is a failure of the number.
    band_coverage: float = 0.0

    @property
    def share_drift(self) -> float | None:
        """The whole of the model's error, in one number.

        The projection is `ballots / (coverage x early_share)`, so its relative
        error is exactly the ratio of the two cycles' early shares.
        """
        if not self.actual_early_share or not self.reference_early_share:
            return None
        return self.actual_early_share / self.reference_early_share

    @property
    def gain_vs_prior(self) -> float:
        return self.mae_null_prior - self.mae_model

    @property
    def gain_vs_uniform(self) -> float:
        return self.mae_null_uniform - self.mae_model


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def shape_agreement(a: Curve, b: Curve, *, floor: float = MIN_COVERAGE
                    ) -> float | None:
    """Mean |g_a(d) - g_b(d)| in POINTS of the early vote, matched by days out.

    `g` is the normalised accumulation curve: ballots in by day d as a share of
    that cycle's own completed early vote. This is the SHAPE, with the level
    divided out, and it is the half of the model that transfers. None when the
    two series never overlap past `floor`.
    """
    fa, fb = a.final_early, b.final_early
    if not fa or not fb:
        return None
    gaps = []
    for dte in sorted((d for d in b.days if d >= 0), reverse=True):
        got = a.at(dte)
        if got is None:
            continue
        gb = b.days[dte][1] / fb
        ga = got[2] / fa
        if gb < floor:
            continue
        gaps.append(abs(ga - gb) * 100.0)
    return _mean(gaps) if gaps else None


@dataclass(frozen=True)
class ShapeTransfer:
    """How well one state's normalised accumulation curve survives a cycle."""

    state: str
    fit_cycle: int
    test_cycle: int
    days: int
    mean_gap: float   # points of the early vote
    max_gap: float


def shape_transfer(out_dir: Path, *, fit_cycle: int = 2022, test_cycle: int = 2024,
                   states: Sequence[str] | None = None) -> list[ShapeTransfer]:
    """Score the SHAPE alone, across every state with a curve in both cycles.

    Deliberately a wider sample than `validate`: the shape needs no certified
    turnout, only the two curves, so Maine, Tennessee, Texas and Virginia can be
    scored here even though none of them has a 2022 statewide result. That is
    what makes this the better-powered measurement of the half of the model that
    works -- and the contrast with `validate`'s four states is the point.

    A curve with fewer than MIN_REFERENCE_DAYS days is skipped: a single-day
    reference is normalised to 1.0 by construction, so scoring it would report
    the arithmetic rather than the state.
    """
    out_dir = Path(out_dir)
    curves = read_curves(out_dir)
    wanted = {s.upper() for s in states} if states else None

    out: list[ShapeTransfer] = []
    for (cycle, state), curve in sorted(curves.items()):
        if cycle != int(test_cycle) or (wanted and state not in wanted):
            continue
        reference = curves.get((int(fit_cycle), state))
        if reference is None:
            continue
        if not (reference.complete and curve.complete):
            continue
        if min(reference.n_days, curve.n_days) < MIN_REFERENCE_DAYS:
            continue
        fa, fb = reference.final_early, curve.final_early
        if not fa or not fb:
            continue
        gaps = []
        for dte in sorted((d for d in curve.days if d >= 0), reverse=True):
            got = reference.at(dte)
            if got is None:
                continue
            gb = curve.days[dte][1] / fb
            if gb < MIN_COVERAGE:
                continue
            gaps.append(abs(got[2] / fa - gb) * 100.0)
        if gaps:
            out.append(ShapeTransfer(state=state, fit_cycle=int(fit_cycle),
                                     test_cycle=int(test_cycle), days=len(gaps),
                                     mean_gap=_mean(gaps), max_gap=max(gaps)))
    return out


def format_shape_transfer(rows: Sequence[ShapeTransfer]) -> Iterator[str]:
    if not rows:
        yield "(no state has a usable curve in both cycles)"
        return
    yield (f"SHAPE ONLY -- the normalised curve, level divided out "
           f"({rows[0].fit_cycle} vs {rows[0].test_cycle})")
    yield f"{'st':3s} {'days':>4s} {'mean gap':>9s} {'max gap':>8s}"
    for r in rows:
        yield f"{r.state:3s} {r.days:4d} {r.mean_gap:8.1f}p {r.max_gap:7.1f}p"
    yield (f"mean over {len(rows)} state(s): {_mean([r.mean_gap for r in rows]):.1f} "
           f"points of that cycle's early vote")


def validate(out_dir: Path, *, fit_cycle: int = 2022, test_cycle: int = 2024,
             states: Sequence[str] | None = None) -> list[Validation]:
    """Fit the curve on one cycle, project another, score it against both nulls.

    THE ONLY OUT-OF-SAMPLE TEST THAT EXISTS HERE, and it is a cross-type one:
    2022 is a midterm and 2024 is a presidential year, and this repo holds no
    other pair. That mismatch is not a flaw in the test, it is the finding --
    see `Validation.share_drift` and docs/turnout.md.

    The MODEL has no pooled parameter, so leave-one-state-out does not change it.
    The UNIFORM NULL does: its national turnout ratio is refitted without the
    state it is scoring, which is the generous direction.
    """
    out_dir = Path(out_dir)
    curves = read_curves(out_dir)
    turnout = read_turnout(out_dir)
    references, _ = build_references(out_dir, curves, turnout)
    wanted = {s.upper() for s in states} if states else None

    usable: dict[str, list[Projection]] = {}
    for (cycle, state), curve in sorted(curves.items()):
        if cycle != int(test_cycle):
            continue
        if wanted and state not in wanted:
            continue
        reference = references.get((int(fit_cycle), state))
        actual = turnout.get((cycle, state))
        if reference is None or actual is None:
            continue
        rows = []
        for dte in sorted((d for d in curve.days if d >= 0), reverse=True):
            iso, ballots = curve.days[dte]
            got = project_day(cycle, state, date.fromisoformat(iso), ballots,
                              reference, dte, actual_turnout=actual[0])
            if got is not None:
                rows.append(got)
        if rows:
            usable[state] = rows

    # The uniform null's national ratio, leave-one-state-out.
    ratios = {s: rows[0].actual_turnout / rows[0].reference.turnout
              for s, rows in usable.items() if rows[0].actual_turnout}

    out: list[Validation] = []
    for state, rows in sorted(usable.items()):
        others = [r for s, r in ratios.items() if s != state]
        national = _mean(others) if others else _mean(list(ratios.values()))
        actual = rows[0].actual_turnout or 0
        null = rows[0].reference.turnout
        model = [abs(r.error_pct or 0.0) for r in rows]
        prior = [abs(r.null_error_pct or 0.0) for r in rows]
        uniform = [abs(100.0 * (null * national / actual - 1.0)) for _ in rows]
        projections = [r.projected_turnout for r in rows]

        test_curve = curves[(int(test_cycle), state)]
        test_final = test_curve.final_early if test_curve.complete else None
        out.append(Validation(
            fit_cycle=int(fit_cycle), test_cycle=int(test_cycle), state=state,
            days=len(rows),
            mae_model=_mean(model), mae_null_prior=_mean(prior),
            mae_null_uniform=_mean(uniform),
            last_dte=rows[-1].reference_dte if rows else 0,
            last_projected=projections[-1], actual=actual, null=null,
            reference_early_share=rows[0].reference.early_share or 0.0,
            actual_early_share=(test_final / actual) if (test_final and actual) else None,
            projection_travel=(100.0 * (max(projections) / min(projections) - 1.0)
                               if min(projections) > 0 else 0.0),
            shape_mae=shape_agreement(rows[0].reference.curve, test_curve),
            reference_days=rows[0].reference.curve.n_days,
            band_coverage=_mean([1.0 if r.band_contains(actual) else 0.0
                                 for r in rows]),
        ))
    return out


def measured_model_error(scores: Sequence[Validation]) -> float | None:
    """The mean absolute relative error behind MODEL_ERROR, refitted from data."""
    if not scores:
        return None
    return _mean([s.mae_model for s in scores]) / 100.0


def format_validation(scores: Sequence[Validation],
                      refused: Sequence[str] = ()) -> Iterator[str]:
    """The table `python -m ev turnout --validate` prints. Percent of turnout."""
    if not scores:
        yield ("(no state can be scored: that needs an early-vote curve in two "
               "cycles AND a certified statewide total in both)")
        for reason in refused:
            yield f"  {reason}"
        return

    fit = scores[0].fit_cycle
    test = scores[0].test_cycle
    yield (f"fit on {fit} ({election_kind(fit)}), project {test} "
           f"({election_kind(test)}) -- {'SAME' if election_kind(fit) == election_kind(test) else 'CROSS'}-TYPE")
    yield (f"{'st':3s} {'days':>4s} {'ref d':>5s} {'ref share':>9s} {'act share':>9s} "
           f"{'drift':>6s} {'model':>7s} {'null:prior':>10s} {'null:unif':>9s} "
           f"{'v.prior':>8s} {'v.unif':>8s} {'travel':>7s} {'in band':>7s}")
    for s in scores:
        drift = f"{s.share_drift:6.3f}" if s.share_drift else "     -"
        yield (f"{s.state:3s} {s.days:4d} {s.reference_days:5d} "
               f"{s.reference_early_share * 100:8.2f}% "
               f"{(s.actual_early_share or 0) * 100:8.2f}% {drift} "
               f"{s.mae_model:7.1f} {s.mae_null_prior:10.1f} {s.mae_null_uniform:9.1f} "
               f"{s.gain_vs_prior:+8.1f} {s.gain_vs_uniform:+8.1f} "
               f"{s.projection_travel:6.0f}% {s.band_coverage * 100:6.0f}%")

    n = len(scores)
    model = _mean([s.mae_model for s in scores])
    prior = _mean([s.mae_null_prior for s in scores])
    uniform = _mean([s.mae_null_uniform for s in scores])
    yield ""
    yield (f"MEAN over {n} state(s):  model {model:.1f}%   "
           f"null:prior {prior:.1f}%   null:uniform {uniform:.1f}%")
    yield (f"gain vs prior turnout = {prior - model:+.1f} pp of turnout   "
           f"gain vs uniform ratio = {uniform - model:+.1f} pp")
    yield (f"published interval contains the certified turnout on "
           f"{_mean([s.band_coverage for s in scores]) * 100:.0f}% of scored days "
           f"at a half-width of {MODEL_ERROR:.0%}")
    shapes = [s.shape_mae for s in scores if s.shape_mae is not None]
    if shapes:
        yield (f"curve SHAPE agreement across the two cycles: {_mean(shapes):.1f} "
               f"points of the early vote (this is the half that transfers)")
    drifts = [s.share_drift for s in scores if s.share_drift]
    if drifts:
        yield (f"early-share LEVEL drift: {min(drifts):.3f} to {max(drifts):.3f} "
               f"({(min(drifts) - 1) * 100:+.0f}% to {(max(drifts) - 1) * 100:+.0f}% "
               f"relative) -- this is the half that does not, and it IS the error")
    ships = (prior - model) >= MIN_GAIN and (uniform - model) >= MIN_GAIN
    yield ""
    yield (f"VERDICT: {'SHIPS' if ships else 'NOTHING SHIPS'} "
           f"(bar is {MIN_GAIN:+.2f}% of turnout against BOTH nulls; measured "
           f"{prior - model:+.1f} and {uniform - model:+.1f})")
    yield ("All errors are mean absolute error as a PERCENT OF ACTUAL TURNOUT, "
           "averaged over days past MIN_COVERAGE, one score per state.")
    if refused:
        yield ""
        yield f"refused {len(refused)} state-cycle(s):"
        for reason in refused:
            yield f"  {reason}"


# --------------------------------------------------------------------------
# Command
# --------------------------------------------------------------------------
def cmd_turnout(args) -> int:
    """`python -m ev turnout` -- static analysis, deliberately not in `ingest`.

    Its own subcommand for the same reason `estimate`, `regress` and
    `counterfactual` are: the six-hourly job must not be able to publish a model
    number, and `ev.turnout` is imported inside the CLI's dispatcher so `ingest`
    never loads it.
    """
    from .cli import OUTPUT_DIR
    from .registry import tracked_states

    out_dir = Path(args.output or OUTPUT_DIR)

    if args.validate:
        directions = [(2022, 2024), (2024, 2022)] if args.both_directions \
            else [(args.fit or 2022, args.test or 2024)]
        for fit, test in directions:
            scores = validate(out_dir, fit_cycle=fit, test_cycle=test,
                              states=args.state)
            _, refused = build_references(out_dir)
            for line in format_validation(scores, refused if args.verbose else ()):
                print(line)
            print("")
            for line in format_shape_transfer(
                    shape_transfer(out_dir, fit_cycle=fit, test_cycle=test,
                                   states=args.state)):
                print(line)
            print("")
        return 0

    rows, refused = build(out_dir, cycles=args.cycle, states=args.state,
                          cross_type=args.cross_type)

    by_cycle: dict[int, int] = {}
    for row in rows:
        by_cycle[row.cycle] = by_cycle.get(row.cycle, 0) + 1
    print(f"turnout: {len(rows)} projected state-day(s) over "
          f"{len({(r.cycle, r.state) for r in rows})} state-cycle(s)")
    for cycle, count in sorted(by_cycle.items()):
        states = sorted({r.state for r in rows if r.cycle == cycle})
        print(f"  {cycle}: {count} row(s), {len(states)} state(s): {' '.join(states)}")

    tracked = tracked_states()
    for cycle in sorted(by_cycle):
        total = rollup([r for r in rows if r.cycle == cycle], cycle, tracked)
        if total is None:
            continue
        print(f"  {cycle} ROLL-UP over the {total['states_included']} qualifying "
              f"state(s) of {total['states_tracked']} tracked -- NOT a national "
              f"total: {total['projected_turnout']:,.0f} "
              f"[{total['projected_lo']:,.0f} - {total['projected_hi']:,.0f}], "
              f"against {total['reference_turnout']:,} in the reference cycle")

    if args.verbose and refused:
        print(f"  refused {len(refused)} case(s):")
        for reason in refused:
            print(f"    {reason}")
    elif refused:
        print(f"  refused {len(refused)} case(s); -v names each one")

    print("")
    print("Read docs/turnout.md before putting any of this on a page: measured "
          "out of sample it does not beat either null.")

    if args.dry_run:
        print(f"DRY RUN  nothing written")
        return 0
    if not rows:
        # Writing an empty file would replace real rows from a previous run with
        # nothing.
        print("no projectable state-day; turnout.csv left untouched")
        return 0

    info = write(out_dir, rows)
    print(f"turnout.csv: {info['rows']} rows ({len(rows)} projected this run)")
    return 0
