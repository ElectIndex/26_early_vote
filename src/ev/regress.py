"""Does the early vote predict the result? A regression, and its honest score.

WHAT THIS ASKS
--------------
The daily tables say how many ballots came back and, where a state registers by
party, who returned them. `results_state.csv` says what happened. This module
fits the join: a least-squares regression from early-vote features to the
outcome, on 2022 and 2024, and then -- the only part that matters -- measures it
OUT OF SAMPLE against the null model a reader already has for free.

THE NULL MODEL, WHICH IS THE WHOLE TEST
---------------------------------------
    "Quote this state's last comparable result and stop."

That is `swing = 0`: predict that Ohio's 2022 Senate margin equals Ohio's 2016
Senate margin. It costs nothing, it needs no data, and it is what a regression
has to beat to be worth a pixel. `docs/party-estimate.md` is the cautionary
example: a model that looked reasonable and bought 0.79 points over exactly this
kind of constant, and was recommended against on that basis. The same standard
applies here and the answer is in `docs/regression.md`.

A SECOND NULL, because the first is easy to beat for the wrong reason
--------------------------------------------------------------------
    "The country swung X this year; apply X to every state."

Any regression fitted on one cycle learns that cycle's national swing in its
INTERCEPT, and would then beat `swing = 0` without the early vote contributing
anything at all. So every spec is also scored against an intercept-only fit on
the same rows. A specification that does not beat BOTH nulls has measured
nothing about the early vote.

THE TARGET IS THE SWING, NOT THE LEVEL
--------------------------------------
Regressing the state's margin on anything at all produces an enormous R^2,
because states differ from each other far more than early votes differ from each
other -- the fit is reading "this is Wyoming" and reporting it as insight.
Differencing against the same state's own last comparable election removes that
state effect, and it makes the null model above exactly the constant `0`, which
is the honest thing to be measured against. `target = margin - prior_margin`,
both in percentage points of (dem_share - rep_share), D positive.

THE PRIOR MARGIN comes from the same MEDSL files `results.py` already reads,
parsed for the cycle before: president is four years back, Senate six (the same
seat class). `results_state.csv` itself carries only 2022 and 2024, so the
anchor cannot come from it -- but nothing here re-publishes those older cycles,
it only reads them. See `prior_margins`.

WHERE OUTPUT GOES
-----------------
`output/regression.csv` (one row per term per specification: coefficient,
standard error, n, R^2, and the out-of-sample errors) and
`output/regression_fit.csv` (one row per state: predicted, actual, residual,
and every candidate feature's value, blank where the state does not report it).
Never into `ev_state_daily.csv`, never into `results_state.csv`. A model output
that lands in a column meaning "the state reported this" is the one mistake this
pipeline cannot recover from; `write()` can only address the two files above and
there is a test that says so.

BLANKS
------
THE BLANK RULE from `schema.py`, applied on the read side and then again on the
write side. A state that does not report party registration has no
`ev_party_margin` -- not a zero -- and is dropped from any specification that
uses it rather than imputed to the mean. A cycle whose series never reached the
end of early voting has no `early_share_of_total`, because the numerator would
be a partial count divided by a final denominator, which is not a share of
anything, and a series whose last day is weeks from the election has no usable
snapshot at all. Every dropped case is counted and named in the run's output.
"""

from __future__ import annotations

import csv
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence

from . import publish, results

log = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Published contract
# --------------------------------------------------------------------------
REGRESSION_FILENAME = "regression.csv"
FIT_FILENAME = "regression_fit.csv"

#: The only two files this module may write. Named as constants so the test that
#: hashes an output/ tree before and after a run has something to assert against.
WRITABLE = (REGRESSION_FILENAME, FIT_FILENAME)

REGRESSION_COLUMNS = [
    "spec", "target", "cycles", "n", "term",
    "coef", "std_error", "t_stat",
    "r2", "adj_r2", "rmse_in_sample", "mae_in_sample",
    "mae_loo", "mae_null_prior", "mae_null_uniform",
    "gain_vs_null_prior", "gain_vs_null_uniform",
    "beats_null", "method", "source_name", "retrieved_at",
]

REGRESSION_KEY = ("spec", "term")

FIT_COLUMNS = [
    "spec", "cycle", "state", "office", "days_to_election",
    "target", "actual_margin", "prior_margin", "prior_cycle",
    "predicted", "residual", "predicted_loo", "residual_loo",
    "null_predicted", "null_residual",
    # Every candidate feature, whether or not this spec used it. Blank means the
    # state does not report it; see THE BLANK RULE in the docstring.
    "early_share_of_total", "ev_party_margin", "mail_share",
    "ev_party_margin_swing", "mail_share_swing", "pace_vs_prior",
    "ev_ballots", "source_tier", "source_name", "retrieved_at",
]

FIT_KEY = ("spec", "cycle", "state", "office")

#: Identifies the arithmetic in every row, so a row written by an older version
#: is recognisable after the fact.
METHOD = "ols-swing-vs-prior-comparable"

SOURCE_NAME = "electindex-regress/ols"

#: Percentage points. A regression has to beat the null by at least this much
#: out of sample before it is worth putting in front of a reader. Set at one
#: point of margin because that is roughly the width of the thing a reader can
#: act on, and because docs/party-estimate.md declined to ship a model that
#: bought 0.79 -- using a softer bar here than the one already applied to a
#: sibling feature would be arguing backwards from the answer.
MIN_GAIN = 1.0

#: Cycles with both an early-vote series and a published result.
FIT_CYCLES = (2022, 2024)

#: How far from Election Day a state's last early-vote row may sit and still be
#: read as a snapshot of that state's early electorate.
#:
#: The composition of returned ballots MOVES, and it moves hugely: North
#: Carolina's 2024 returns were 82% registered-Democratic at 45 days out and 49%
#: at the close (docs/party-estimate.md). So a party mix measured eleven days out
#: in South Dakota is not the same object as one measured on Election Day in
#: Ohio, and putting them in the same cross-section is comparing two different
#: points of two different campaigns. Seven days is the widest slack that still
#: covers every state whose early-vote window genuinely CLOSES before Election
#: Day -- Maryland at five days out and Kentucky, Texas and Tennessee at four or
#: five are at their own finish line, not truncated. Anything further out is a
#: series our archive stopped following, and its features are left blank.
SNAPSHOT_MAX_DTE = 7

#: Cross-cycle features are matched at the SAME days-to-election, never the same
#: date -- Election Day moves (Nov 8 2022, Nov 5 2024, Nov 3 2026). Two series
#: rarely land on exactly the same day-out, so the match allows this much slack
#: and takes the closest day within it.
DTE_MATCH_TOLERANCE = 3

_DP = 4


class NotEnoughData(RuntimeError):
    """Fewer rows than parameters, or a singular design. Never a silent fudge."""


class Collinear(NotEnoughData):
    """X'X could not be inverted: two predictors carry the same information."""


def _utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _num(cell: str | None) -> float | None:
    """A blank cell is None, never 0 -- THE BLANK RULE on the read side."""
    raw = (cell or "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _cell(value: float | None, places: int = _DP) -> str:
    if value is None:
        return ""
    text = f"{round(value, places):.{places}f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


# --------------------------------------------------------------------------
# Least squares, written out rather than imported
# --------------------------------------------------------------------------
# No numpy, no statsmodels: n here is a few dozen rows and half a dozen columns,
# and a dependency that exists only to invert a 3x3 matrix is a dependency this
# repo would then have to install on every CI run. The arithmetic below is the
# textbook normal equations,
#
#     beta      = (X'X)^-1 X'y
#     resid     = y - X beta
#     sigma^2   = resid'resid / (n - k)
#     var(beta) = sigma^2 (X'X)^-1        -> std_error = sqrt(diag)
#     R^2       = 1 - resid'resid / TSS
#
# with X's first column a column of ones (the intercept). Normal equations
# square the condition number, which is exactly the wrong thing to do with
# collinear predictors -- so `_invert` refuses a singular or near-singular
# matrix outright rather than returning a confident answer built on noise, and
# the callers keep to one or two predictors for the same reason.
# --------------------------------------------------------------------------

#: Pivot below this fraction of the matrix's largest element is treated as zero.
_SINGULAR_TOL = 1e-10


def _invert(matrix: list[list[float]]) -> list[list[float]]:
    """Gauss-Jordan with partial pivoting. Raises on a singular matrix."""
    k = len(matrix)
    scale = max((abs(v) for row in matrix for v in row), default=0.0) or 1.0
    aug = [list(row) + [1.0 if i == j else 0.0 for j in range(k)]
           for i, row in enumerate(matrix)]

    for col in range(k):
        pivot_row = max(range(col, k), key=lambda r: abs(aug[r][col]))
        if abs(aug[pivot_row][col]) < _SINGULAR_TOL * scale:
            raise Collinear(
                f"the {k}-column design is singular at column {col}; two "
                "predictors carry the same information"
            )
        aug[col], aug[pivot_row] = aug[pivot_row], aug[col]
        pivot = aug[col][col]
        aug[col] = [v / pivot for v in aug[col]]
        for row in range(k):
            if row == col:
                continue
            factor = aug[row][col]
            if factor:
                aug[row] = [a - factor * b for a, b in zip(aug[row], aug[col])]
    return [row[k:] for row in aug]


@dataclass
class Fit:
    """One ordinary-least-squares fit. Everything a reader needs to audit it."""

    terms: list[str]
    coef: list[float]
    std_error: list[float]
    n: int
    r2: float | None
    adj_r2: float | None
    rss: float
    fitted: list[float]
    residuals: list[float]

    @property
    def rmse(self) -> float:
        return math.sqrt(self.rss / self.n) if self.n else 0.0

    @property
    def mae(self) -> float:
        return sum(abs(r) for r in self.residuals) / self.n if self.n else 0.0

    def t_stats(self) -> list[float | None]:
        return [c / s if s else None for c, s in zip(self.coef, self.std_error)]

    def predict(self, x: Sequence[float]) -> float:
        return sum(b * v for b, v in zip(self.coef, x))


def ols(design: Sequence[Sequence[float]], y: Sequence[float],
        terms: Sequence[str]) -> Fit:
    """Fit `y` on `design` (which must already carry its intercept column)."""
    n = len(design)
    if n != len(y):
        raise ValueError(f"{n} design rows but {len(y)} responses")
    k = len(terms)
    if any(len(row) != k for row in design):
        raise ValueError("design rows do not all have one entry per term")
    if n <= k:
        raise NotEnoughData(f"{n} rows cannot fit {k} parameters")

    xtx = [[sum(design[i][p] * design[i][q] for i in range(n)) for q in range(k)]
           for p in range(k)]
    xty = [sum(design[i][p] * y[i] for i in range(n)) for p in range(k)]
    inv = _invert(xtx)
    coef = [sum(inv[p][q] * xty[q] for q in range(k)) for p in range(k)]

    fitted = [sum(coef[p] * design[i][p] for p in range(k)) for i in range(n)]
    residuals = [y[i] - fitted[i] for i in range(n)]
    rss = sum(r * r for r in residuals)

    mean_y = sum(y) / n
    tss = sum((v - mean_y) ** 2 for v in y)
    dof = n - k
    sigma2 = rss / dof
    std_error = [math.sqrt(max(sigma2 * inv[p][p], 0.0)) for p in range(k)]

    r2 = 1.0 - rss / tss if tss > 0 else None
    adj = (1.0 - (1.0 - r2) * (n - 1) / dof) if (r2 is not None and dof > 0) else None

    return Fit(terms=list(terms), coef=coef, std_error=std_error, n=n,
               r2=r2, adj_r2=adj, rss=rss, fitted=fitted, residuals=residuals)


def leave_one_out(design: Sequence[Sequence[float]], y: Sequence[float],
                  terms: Sequence[str]) -> list[float | None]:
    """Refit n times, each time holding one row out, and predict that row.

    This is the number that matters. With thirty-odd states and collinear
    predictors an in-sample R^2 can be made to look like anything; the
    prediction for a state the fit has never seen cannot. `None` where the
    reduced sample cannot support the fit at all -- a gap, not a zero.
    """
    n = len(design)
    out: list[float | None] = []
    for i in range(n):
        sub_x = [row for j, row in enumerate(design) if j != i]
        sub_y = [v for j, v in enumerate(y) if j != i]
        try:
            fit = ols(sub_x, sub_y, terms)
        except NotEnoughData:
            out.append(None)
            continue
        out.append(fit.predict(design[i]))
    return out


def mean_abs(values: Iterable[float | None]) -> float | None:
    present = [abs(v) for v in values if v is not None]
    return sum(present) / len(present) if present else None


# --------------------------------------------------------------------------
# The prior-cycle anchor
# --------------------------------------------------------------------------
#: How far back the comparable election is, per office. A Senate seat is up
#: every six years, so a state's last comparable Senate result is the same
#: class's previous election -- 2018 for 2024, 2016 for 2022. President is four.
#: Where a state ran a special instead, `results.parse` has already preferred the
#: regular race, so the anchor is the same seat class even when it is not
#: literally the same seat; that caveat is in docs/regression.md.
PRIOR_OFFSET = {
    results.OFFICE_PRESIDENT: 4,
    results.OFFICE_SENATE: 6,
    results.OFFICE_GOVERNOR: 4,
    results.OFFICE_HOUSE: 2,
}


def prior_cycle(cycle: int, office: str) -> int | None:
    offset = PRIOR_OFFSET.get(office)
    return cycle - offset if offset else None


def prior_margins(cycles: Iterable[int], *, use_cache: bool = True
                  ) -> dict[tuple[str, int], dict[str, float]]:
    """{(office, cycle): {state: margin}} for the anchor cycles.

    Read through `results.parse`, the same parser and the same two MEDSL files
    `ev results` publishes 2022 and 2024 from -- so the anchor obeys the same
    party mapping, the same stage/mode refusals and the same blank rule. Nothing
    is written back: `results_state.csv` carries 2022 and 2024 and keeps doing so.
    """
    wanted = sorted({int(c) for c in cycles})
    out: dict[tuple[str, int], dict[str, float]] = {}
    for office, source in results.SOURCES.items():
        try:
            body = results.fetch(source, use_cache=use_cache)
        except Exception as exc:  # noqa: BLE001 - a missing anchor is a gap
            log.warning("regress: no %s anchor available: %s", office, exc)
            continue
        for cycle in wanted:
            try:
                rows = results.parse(body, source, cycle)
            except Exception as exc:  # noqa: BLE001
                log.warning("regress: %s %s did not parse: %s", office, cycle, exc)
                continue
            table = {r.state: r.margin for r in rows if r.margin is not None}
            if table:
                out[(office, cycle)] = table
    return out


# --------------------------------------------------------------------------
# Early-vote features
# --------------------------------------------------------------------------
@dataclass
class Series:
    """One state-cycle's early-vote curve, already keyed by days-to-election."""

    cycle: int
    state: str
    #: days_to_election -> the published row. Positive is before Election Day.
    days: dict[int, dict[str, str]] = field(default_factory=dict)

    @property
    def final_dte(self) -> int | None:
        """The last day (smallest non-negative days-out) that reported a total.

        Non-negative only: a post-election row is a certified restatement that
        includes mail counted after Election Day, which is not the thing the
        tracker shows a reader in October.
        """
        candidates = [d for d, row in self.days.items()
                      if d >= 0 and _num(row.get("ballots_total")) is not None]
        return min(candidates) if candidates else None

    @property
    def usable_snapshot(self) -> bool:
        """Is this series' last day close enough to the election to compare?

        A series that stops eighteen days out (South Carolina 2022) or in July
        (Arizona's archived page) still holds real ballots -- it just holds them
        at a point in the campaign no other state in the cross-section is at.
        See SNAPSHOT_MAX_DTE.
        """
        dte = self.final_dte
        return dte is not None and dte <= SNAPSHOT_MAX_DTE

    def at(self, dte: int, tolerance: int = DTE_MATCH_TOLERANCE
           ) -> dict[str, str] | None:
        """The row closest to `dte` days out, within `tolerance` days.

        Cross-cycle comparison aligns HERE and only here. Election Day moves --
        Nov 8 2022, Nov 5 2024, Nov 3 2026 -- so a calendar-date join silently
        compares different points of two campaigns.

        Pre-election days only, for the same reason `final_dte` uses them: a
        post-election row counts mail that arrived after the polls closed, so
        matching this cycle's Election Day against last cycle's canvass would
        compare a live figure to a certified one and call the difference pace.
        """
        best, best_gap = None, tolerance + 1
        for day, row in self.days.items():
            if day < 0:
                continue
            gap = abs(day - dte)
            if gap < best_gap and _num(row.get("ballots_total")) is not None:
                best, best_gap = row, gap
        return best

    def final_row(self) -> dict[str, str] | None:
        """The snapshot this state contributes, or None if it is too far out."""
        if not self.usable_snapshot:
            return None
        return self.days.get(self.final_dte)


def two_party_margin(row: dict[str, str] | None) -> float | None:
    """(D - R) / (D + R) in points, from the state's OWN reported registration.

    Blank -- not zero -- wherever the state reports no party registration, which
    is nine of the states this tracker follows plus Arizona. That is the same
    distinction `results.py` draws for Alaska 2022 and it matters for the same
    reason: 0 would say the returned ballots split evenly.
    """
    if row is None:
        return None
    dem, rep = _num(row.get("party_dem")), _num(row.get("party_rep"))
    if dem is None or rep is None or dem + rep <= 0:
        return None
    return 100.0 * (dem - rep) / (dem + rep)


#: How far the two method columns may fall short of the row's own headline
#: total before the split is refused. A state that reports 297,034 mail ballots,
#: `inperson = 0` and a headline of 4,520,768 -- North Carolina's own 2024 file,
#: whose one-stop early votes are not in its `inperson` column -- has not
#: reported a method split at all, and 100% mail would be a confident wrong
#: number rather than a gap.
METHOD_SPLIT_TOLERANCE = 0.02


def mail_share(row: dict[str, str] | None) -> float | None:
    """Mail as a share of returned early ballots, in points.

    Blank unless BOTH methods are reported and together they account for the
    row's own headline total. Two of the three failure modes here publish a
    plausible-looking number from an incomplete split, which is worse than a
    blank: see METHOD_SPLIT_TOLERANCE.
    """
    if row is None:
        return None
    mail, inperson = _num(row.get("mail_returned")), _num(row.get("inperson"))
    if mail is None or inperson is None or mail + inperson <= 0:
        return None
    total = _num(row.get("ballots_total"))
    if total and abs(mail + inperson - total) > METHOD_SPLIT_TOLERANCE * total:
        return None
    return 100.0 * mail / (mail + inperson)


def read_series(out_dir: Path) -> dict[tuple[int, str], Series]:
    """Load `ev_state_daily.csv` into one Series per (cycle, state)."""
    path = Path(out_dir) / "ev_state_daily.csv"
    if not path.exists():
        return {}
    table: dict[tuple[int, str], Series] = {}
    with path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            try:
                cycle = int(row["cycle"])
                dte = int(row["days_to_election"])
            except (KeyError, TypeError, ValueError):
                continue
            state = (row.get("state") or "").strip().upper()
            if not state:
                continue
            series = table.setdefault((cycle, state), Series(cycle, state))
            prior = series.days.get(dte)
            # Two rows on the same days-out can only happen across a restatement;
            # keep the one reporting more ballots, never the sum.
            if prior is None or (_num(row.get("ballots_total")) or -1) > (
                    _num(prior.get("ballots_total")) or -1):
                series.days[dte] = row
    return table


def read_results(out_dir: Path) -> list[dict[str, str]]:
    path = Path(out_dir) / "results_state.csv"
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


# --------------------------------------------------------------------------
# One fit case
# --------------------------------------------------------------------------
#: Every candidate predictor, with the story that justifies it. A kitchen sink
#: fits better and means less; these are here because each one is a sentence a
#: reader can check, and the specifications below use one or two at a time.
FEATURES = {
    "early_share_of_total":
        "the state's completed early vote as a share of the votes eventually "
        "cast in this race",
    "ev_party_margin":
        "D minus R as a share of the party-registered ballots returned "
        "(blank where the state does not register by party)",
    "mail_share":
        "mail as a share of the early ballots returned",
    "ev_party_margin_swing":
        "ev_party_margin minus the same state's figure at the same "
        "days-to-election in the previous cycle",
    "mail_share_swing":
        "mail_share minus the same state's figure at the same days-to-election "
        "in the previous cycle",
    "pace_vs_prior":
        "early ballots returned as a percentage of the same state's count at "
        "the same days-to-election in the previous cycle, minus 100",
}


@dataclass
class Case:
    """One (cycle, state, office) the regression can be fitted on or scored at."""

    cycle: int
    state: str
    office: str
    actual_margin: float
    prior_margin: float
    prior_cycle: int
    days_to_election: int
    features: dict[str, float | None]
    ev_ballots: float | None = None
    source_tier: str = ""
    source_name: str = ""

    @property
    def target(self) -> float:
        """Swing in margin against the last comparable election, in points."""
        return self.actual_margin - self.prior_margin

    def key(self) -> tuple:
        return (self.cycle, self.state, self.office)

    def has(self, names: Sequence[str]) -> bool:
        return all(self.features.get(n) is not None for n in names)


def build_cases(out_dir: Path, *, cycles: Sequence[int] | None = None,
                states: Sequence[str] | None = None,
                anchors: dict[tuple[str, int], dict[str, float]] | None = None,
                ) -> tuple[list[Case], list[str]]:
    """Assemble every state-cycle-office that has BOTH a curve and an outcome.

    Returns (cases, skipped) where `skipped` names what was dropped and why --
    a dropped case is data we do not have, and saying which is the difference
    between a small sample and a silently biased one.
    """
    out_dir = Path(out_dir)
    wanted_cycles = {int(c) for c in (cycles or FIT_CYCLES)}
    wanted_states = {s.upper() for s in states} if states else None

    series = read_series(out_dir)
    # The COMPLETED early vote per past cycle, exactly as `ev results` computes
    # it for `results_state.csv`: a hand-entered official figure from
    # ev_state_meta.csv, else publish.derive_prior_finals, which answers only for
    # a series that actually reached Election Day. Reused rather than
    # re-derived -- a second definition of "final" in this repo is a second
    # number for a reader to reconcile, and this one is already tested.
    finals = results.early_totals(out_dir)
    if anchors is None:
        needed = {prior_cycle(c, o) for c in wanted_cycles for o in results.OFFICES}
        anchors = prior_margins([c for c in needed if c])

    cases: list[Case] = []
    skipped: list[str] = []

    for row in read_results(out_dir):
        try:
            cycle = int(row["cycle"])
        except (KeyError, TypeError, ValueError):
            continue
        if cycle not in wanted_cycles:
            continue
        state = (row.get("state") or "").strip().upper()
        office = (row.get("office") or "").strip()
        if wanted_states and state not in wanted_states:
            continue

        margin = _num(row.get("margin"))
        if margin is None:
            # Alaska 2022: MEDSL gives no party for any candidate, so there is
            # no margin. Missing, not zero, and not imputable.
            skipped.append(f"{cycle} {state} {office}: no published margin")
            continue

        back = prior_cycle(cycle, office)
        anchor = anchors.get((office, back), {}).get(state) if back else None
        if anchor is None:
            skipped.append(f"{cycle} {state} {office}: no {back} result to swing from")
            continue

        curve = series.get((cycle, state))
        if curve is None or curve.final_dte is None:
            skipped.append(f"{cycle} {state} {office}: no early-vote series")
            continue

        cases.append(_case(
            row, cycle, state, office, margin, anchor, back, curve,
            series.get((prior_cycle_for_ev(cycle), state)),
            finals.get((str(cycle), state)),
        ))

    cases.sort(key=lambda c: (c.cycle, c.office, c.state))
    return cases, skipped


def prior_cycle_for_ev(cycle: int) -> int:
    """The cycle whose early-vote curve this one is compared against.

    Always the previous general election -- 2022 for 2024, 2024 for 2026 --
    because that is the only other curve the tracker holds. It is deliberately
    NOT the same-office anchor (six years back for the Senate): there is no 2018
    early-vote data anywhere in this repo, and pretending otherwise would put a
    blank where a reader would read a measurement.
    """
    return cycle - 2


def _case(row: dict[str, str], cycle: int, state: str, office: str,
          margin: float, anchor: float, back: int,
          curve: Series, prior_curve: Series | None,
          final_ev: int | None) -> Case:
    final = curve.final_row()
    final_dte = curve.final_dte if curve.final_dte is not None else 0
    total_votes = _num(row.get("total_votes"))

    feats: dict[str, float | None] = {name: None for name in FEATURES}
    # `early_share_of_total` is published by `ev results` too, from the same
    # numerator. Prefer the published cell when it is there; otherwise derive it
    # from the same finals table, so the two can never disagree.
    #
    # Gated on the snapshot rule as well, because `publish.derive_prior_finals`
    # asks only whether the series has a row DATED Election Day, not whether that
    # row reported anything. South Carolina 2022 has such a row with a blank
    # total and stops counting eighteen days out at 16,975 ballots, which would
    # publish a 1.0% "early share" for a state that early-voted in the hundreds
    # of thousands. A number that wrong is worse than a blank.
    if curve.usable_snapshot:
        published = _num(row.get("early_share_of_total"))
        if published is not None:
            feats["early_share_of_total"] = published
        elif final_ev is not None and total_votes:
            feats["early_share_of_total"] = 100.0 * final_ev / total_votes
    feats["ev_party_margin"] = two_party_margin(final)
    feats["mail_share"] = mail_share(final)

    if prior_curve is not None and final is not None:
        # Aligned at the SAME days-to-election, never the same date.
        matched = prior_curve.at(final_dte)
        prior_party = two_party_margin(matched)
        if prior_party is not None and feats["ev_party_margin"] is not None:
            feats["ev_party_margin_swing"] = feats["ev_party_margin"] - prior_party
        prior_mail = mail_share(matched)
        if prior_mail is not None and feats["mail_share"] is not None:
            feats["mail_share_swing"] = feats["mail_share"] - prior_mail
        prior_total = _num((matched or {}).get("ballots_total"))
        now_total = _num((final or {}).get("ballots_total"))
        if prior_total and now_total is not None:
            feats["pace_vs_prior"] = 100.0 * now_total / prior_total - 100.0

    return Case(
        cycle=cycle, state=state, office=office,
        actual_margin=margin, prior_margin=anchor, prior_cycle=back,
        days_to_election=final_dte, features=feats,
        ev_ballots=float(final_ev) if final_ev is not None else None,
        source_tier=(final or {}).get("source_tier", ""),
        source_name=(final or {}).get("source_name", ""),
    )


# --------------------------------------------------------------------------
# Specifications
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Spec:
    """One regression to fit. Deliberately one or two predictors, never a sink."""

    name: str
    predictors: tuple[str, ...]
    story: str


SPECS: tuple[Spec, ...] = (
    Spec("uniform", (),
         "intercept only: the country swung X, apply it everywhere. The second "
         "null, not a finding."),
    Spec("share", ("early_share_of_total",),
         "states where more of the electorate voted early swung further."),
    Spec("party", ("ev_party_margin",),
         "the party registration of the ballots actually returned."),
    Spec("party_swing", ("ev_party_margin_swing",),
         "the CHANGE in that registration mix against the same state at the "
         "same days-out last cycle -- the honest form of the party signal."),
    Spec("mail", ("mail_share",),
         "how much of the early vote came by mail."),
    Spec("mail_swing", ("mail_share_swing",),
         "the change in the mail share against the same state last cycle."),
    Spec("pace", ("pace_vs_prior",),
         "early ballots against the same state's count at the same days-out "
         "last cycle."),
    Spec("party_and_share", ("ev_party_margin", "early_share_of_total"),
         "the registration mix, adjusted for how much of the vote is in."),
)


@dataclass
class Score:
    """One specification, fitted and then measured against both nulls."""

    spec: Spec
    fit: Fit
    cases: list[Case]
    loo: list[float | None]
    mae_null_prior: float
    mae_null_uniform: float | None
    cycles: tuple[int, ...]

    @property
    def mae_loo(self) -> float | None:
        return mean_abs([c.target - p for c, p in zip(self.cases, self.loo)
                         if p is not None])

    @property
    def gain_vs_null_prior(self) -> float | None:
        loo = self.mae_loo
        return None if loo is None else self.mae_null_prior - loo

    @property
    def gain_vs_null_uniform(self) -> float | None:
        loo = self.mae_loo
        if loo is None or self.mae_null_uniform is None:
            return None
        return self.mae_null_uniform - loo

    @property
    def beats_null(self) -> bool:
        """Both nulls, by MIN_GAIN, out of sample. Anything less is not a finding."""
        a, b = self.gain_vs_null_prior, self.gain_vs_null_uniform
        if a is None or a < MIN_GAIN:
            return False
        # A spec with no early-vote predictor cannot "beat" the uniform null; it
        # IS the uniform null.
        if not self.spec.predictors:
            return False
        return b is not None and b >= MIN_GAIN


def design_for(spec: Spec, cases: Sequence[Case]) -> tuple[list[list[float]], list[str]]:
    terms = ["intercept", *spec.predictors]
    design = [[1.0] + [float(c.features[name]) for name in spec.predictors]
              for c in cases]
    return design, terms


def uniform_null_predictions(cases: Sequence[Case]) -> list[float]:
    """Leave-one-out mean swing: "the country swung X" without peeking at self.

    Fitted the same way the model is, so the comparison is like for like -- an
    in-sample mean would flatter the null and make the model look worse, which
    is the wrong direction to be generous in.
    """
    n = len(cases)
    if n < 2:
        return [0.0] * n
    total = sum(c.target for c in cases)
    return [(total - c.target) / (n - 1) for c in cases]


def score(spec: Spec, cases: Sequence[Case]) -> Score | None:
    """Fit one specification on the cases that carry all its predictors."""
    usable = [c for c in cases if c.has(spec.predictors)]
    design, terms = design_for(spec, usable)
    try:
        fit = ols(design, [c.target for c in usable], terms)
    except NotEnoughData as exc:
        log.info("regress: %s not fitted (%s)", spec.name, exc)
        return None

    loo = leave_one_out(design, [c.target for c in usable], terms)
    null_prior = mean_abs([c.target for c in usable]) or 0.0
    uniform = uniform_null_predictions(usable)
    null_uniform = mean_abs([c.target - p for c, p in zip(usable, uniform)])

    return Score(spec=spec, fit=fit, cases=list(usable), loo=loo,
                 mae_null_prior=null_prior, mae_null_uniform=null_uniform,
                 cycles=tuple(sorted({c.cycle for c in usable})))


def cross_cycle(spec: Spec, cases: Sequence[Case], fit_on: int, test_on: int
                ) -> tuple[float, float, int] | None:
    """Fit on one cycle, predict the other. Returns (model MAE, null MAE, n).

    The strongest out-of-sample test available and the one a reader in October
    2026 actually cares about, because 2026 is a cycle the model has not seen.
    Returns None when either side is too thin to support it -- which, with this
    much history, is the usual answer.
    """
    train = [c for c in cases if c.cycle == fit_on and c.has(spec.predictors)]
    test = [c for c in cases if c.cycle == test_on and c.has(spec.predictors)]
    if not test:
        return None
    design, terms = design_for(spec, train)
    try:
        fit = ols(design, [c.target for c in train], terms)
    except NotEnoughData:
        return None
    test_design, _ = design_for(spec, test)
    errors = [c.target - fit.predict(x) for c, x in zip(test, test_design)]
    return (mean_abs(errors) or 0.0, mean_abs([c.target for c in test]) or 0.0, len(test))


# --------------------------------------------------------------------------
# Publish
# --------------------------------------------------------------------------
def regression_rows(scores: Sequence[Score]) -> list[dict[str, str]]:
    stamp = _utcnow()
    rows: list[dict[str, str]] = []
    for s in scores:
        shared = {
            "spec": s.spec.name,
            "target": "margin_swing_vs_prior_comparable",
            "cycles": "|".join(str(c) for c in s.cycles),
            "n": str(s.fit.n),
            "r2": _cell(s.fit.r2),
            "adj_r2": _cell(s.fit.adj_r2),
            "rmse_in_sample": _cell(s.fit.rmse),
            "mae_in_sample": _cell(s.fit.mae),
            "mae_loo": _cell(s.mae_loo),
            "mae_null_prior": _cell(s.mae_null_prior),
            "mae_null_uniform": _cell(s.mae_null_uniform),
            "gain_vs_null_prior": _cell(s.gain_vs_null_prior),
            "gain_vs_null_uniform": _cell(s.gain_vs_null_uniform),
            "beats_null": "true" if s.beats_null else "false",
            "method": METHOD,
            "source_name": SOURCE_NAME,
            "retrieved_at": stamp,
        }
        for term, coef, se, t in zip(s.fit.terms, s.fit.coef, s.fit.std_error,
                                     s.fit.t_stats()):
            rows.append({**shared, "term": term, "coef": _cell(coef),
                         "std_error": _cell(se), "t_stat": _cell(t)})
    return rows


def fit_rows(scores: Sequence[Score]) -> list[dict[str, str]]:
    stamp = _utcnow()
    rows: list[dict[str, str]] = []
    for s in scores:
        uniform = uniform_null_predictions(s.cases)
        for case, fitted, loo, _null in zip(s.cases, s.fit.fitted, s.loo, uniform):
            rows.append({
                "spec": s.spec.name,
                "cycle": str(case.cycle),
                "state": case.state,
                "office": case.office,
                "days_to_election": str(case.days_to_election),
                "target": _cell(case.target),
                "actual_margin": _cell(case.actual_margin),
                "prior_margin": _cell(case.prior_margin),
                "prior_cycle": str(case.prior_cycle),
                "predicted": _cell(fitted),
                "residual": _cell(case.target - fitted),
                "predicted_loo": _cell(loo),
                "residual_loo": _cell(None if loo is None else case.target - loo),
                # The null is "quote the prior result", i.e. a swing of zero.
                "null_predicted": "0",
                "null_residual": _cell(case.target),
                **{name: _cell(case.features.get(name)) for name in FEATURES},
                "ev_ballots": _cell(case.ev_ballots, 0),
                "source_tier": case.source_tier,
                "source_name": case.source_name,
                "retrieved_at": stamp,
            })
    return rows


def write(out_dir: Path, scores: Sequence[Score]) -> dict:
    """Write both tables. This function can address no other file.

    Same shape as `results.publish_results`: a keyed replace plus publish.py's
    atomic write, because these rows carry no `source_tier` for the ladder merge
    to reason about and their natural order is not publish.py's shared sort key.
    """
    out_dir = Path(out_dir)
    info = {}
    for filename, columns, key, rows in (
        (REGRESSION_FILENAME, REGRESSION_COLUMNS, REGRESSION_KEY,
         regression_rows(scores)),
        (FIT_FILENAME, FIT_COLUMNS, FIT_KEY, fit_rows(scores)),
    ):
        if filename not in WRITABLE:
            # Not reachable from here, and that is the point: the guarantee is
            # stated in code so a future edit that adds a destination has to
            # add it to WRITABLE, where the test is looking.
            raise RuntimeError(f"regress may not write {filename}")
        path = out_dir / filename
        ordered = sorted(rows, key=lambda r: tuple(r.get(c, "") for c in key))
        publish._atomic_write(path, columns, ordered)
        info[filename] = len(ordered)
        log.info("%s: %d rows", filename, len(ordered))
    return info


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------
def format_scores(scores: Sequence[Score], skipped: Sequence[str] = ()) -> list[str]:
    """The table `python -m ev regress` prints. Percentage points throughout."""
    lines = [
        "specification          n    R^2  in-MAE  LOO-MAE   null:prior  "
        "null:uniform   gain:prior  gain:uniform  ships",
        "-" * 112,
    ]
    for s in sorted(scores, key=lambda s: s.spec.name):
        def cell(v, width=8, places=2):
            return f"{v:>{width}.{places}f}" if v is not None else " " * (width - 1) + "-"
        lines.append(
            f"{s.spec.name:<20} {s.fit.n:>3} {cell(s.fit.r2, 6, 3)} "
            f"{cell(s.fit.mae, 7)} {cell(s.mae_loo)} {cell(s.mae_null_prior, 12)} "
            f"{cell(s.mae_null_uniform, 13)} {cell(s.gain_vs_null_prior, 12)} "
            f"{cell(s.gain_vs_null_uniform, 13)}  "
            f"{'YES' if s.beats_null else 'no'}"
        )
    lines.append("")
    lines.append(f"All errors are mean absolute error in PERCENTAGE POINTS of margin "
                 f"(dem_share - rep_share). LOO-MAE is out of sample. A spec ships "
                 f"only if it beats BOTH nulls by {MIN_GAIN:.1f} point(s).")
    if skipped:
        lines.append("")
        lines.append(f"dropped {len(skipped)} state-cycle-office row(s):")
        for reason in skipped:
            lines.append(f"  {reason}")
    return lines


# --------------------------------------------------------------------------
# Build
# --------------------------------------------------------------------------
def build(out_dir: Path, *, cycles: Sequence[int] | None = None,
          states: Sequence[str] | None = None,
          specs: Sequence[Spec] | None = None,
          use_cache: bool = True) -> tuple[list[Score], list[Case], list[str]]:
    wanted_cycles = list(cycles or FIT_CYCLES)
    needed = {prior_cycle(c, o) for c in wanted_cycles for o in results.OFFICES}
    anchors = prior_margins([c for c in needed if c], use_cache=use_cache)
    cases, skipped = build_cases(out_dir, cycles=wanted_cycles, states=states,
                                 anchors=anchors)
    scores = [s for s in (score(spec, cases) for spec in (specs or SPECS))
              if s is not None]
    return scores, cases, skipped


def cmd_regress(args) -> int:
    """`python -m ev regress` -- static analysis over history, not the ingest walk.

    Deliberately its own subcommand for the same reason `estimate` is: the
    scheduled job must not be able to publish a model number, and `ev.regress`
    is imported inside this function so `ingest` never loads it.
    """
    from .cli import OUTPUT_DIR

    out_dir = Path(args.output or OUTPUT_DIR)
    scores, cases, skipped = build(
        out_dir,
        cycles=args.cycle,
        states=args.state,
        use_cache=not args.refresh,
    )

    for line in format_scores(scores, skipped if args.verbose else ()):
        print(line)

    if args.cross_cycle:
        print("")
        print("fit on one cycle, predict the other (percentage points):")
        for spec in SPECS:
            for a, b in ((2022, 2024), (2024, 2022)):
                got = cross_cycle(spec, cases, a, b)
                if got is None:
                    continue
                model, null, n = got
                print(f"  {spec.name:<20} fit {a} -> test {b}  n={n:<3} "
                      f"model {model:6.2f}  null {null:6.2f}  "
                      f"gain {null - model:+6.2f}")

    shipping = [s for s in scores if s.beats_null]
    print("")
    if shipping:
        print("SHIPPABLE: " + ", ".join(s.spec.name for s in shipping)
              + " -- read docs/regression.md before putting it on a page.")
    else:
        print("NOTHING SHIPS: no specification beats both nulls out of sample by "
              f"{MIN_GAIN:.1f} point(s). See docs/regression.md.")

    if args.dry_run:
        print(f"DRY RUN  {len(cases)} case(s), {len(scores)} spec(s), nothing written")
        return 0
    if not scores:
        # Writing empty files would replace an earlier real fit with nothing.
        print("no fittable specification; regression.csv left untouched")
        return 0

    info = write(out_dir, scores)
    print(f"regression.csv: {info[REGRESSION_FILENAME]} rows; "
          f"regression_fit.csv: {info[FIT_FILENAME]} rows")
    return 0
