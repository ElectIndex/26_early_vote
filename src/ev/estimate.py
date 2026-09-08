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
`est_dem_share` has two parts, and the second one is the reason this file is not
just a restatement of the last election:

  geography     the 2024 presidential two-party Democratic share OF THE PLACES
                whose ballots are in so far -- "if everyone who has voted early
                so far voted exactly the way their county did in 2024, the early
                vote would be X% Democratic."

  mail          plus a correction for the fact that the people who return a MAIL
                ballot are not a random draw from their county. Where mail is a
                minority channel that a voter has to ask for, the people who ask
                lean sharply Democratic; where mail has already reached most of
                the electorate (Colorado) there is nobody left for it to select,
                and the correction goes to nothing. See `mail_selection`.

The geography-only version of this model was measured at 6.9 points of mean
absolute error against the states that DO report party, and beat "quote the
state's own 2024 result and stop" by 0.7 points -- which is to say it was mostly
laundering a known election result through today's ballot counts. Adding the mail
term takes that to 3.1 points out of sample and a gain of 4.4 over the same null.
Pennsylvania, the worst state in the old table at 15.5, is 3.2.

It is still NOT "X% of early ballots were cast by Democrats". The remaining error
is dominated by a gap this model cannot see and does not try to: a state's party
REGISTRATION is not its VOTE (Kentucky is full of registered Democrats who vote
Republican), and the states this model actually publishes for do not register by
party at all. The full numbers, the states it gets worse rather than better, and
the one assumption that would sink it in 2026 are all in docs/party-estimate.md.

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
#: version is recognisable after the fact. The suffix names the second term:
#: rows written before it carry the bare `pres2024-2party-county-weighted`.
METHOD = "pres2024-2party-county-weighted+mail-selection"

#: Identifies the WEIGHTS, separately from the algorithm.
SOURCE_NAME = "electindex-estimate/pres2024-county-returns"

#: How much more Democratic a mail ballot is than the county it came from, at the
#: limit where mail has reached none of the electorate, in share points. Fitted
#: by `fit_mail_selection` on every state-cycle in output/ that reports party AND
#: has county rows; see docs/party-estimate.md for the leave-one-state-out score.
#: `test_fitted_constants_still_match_the_data` refits and fails if the data
#: moves away from these.
#:
#: 0.366 / 0.406 / 0.382 / 0.332 / 0.288 on earlier panels; 0.340 since a
#: five-state backfill (DE, LA, MN, NY, OK, OR, WI) took the panel to 18 series.
#: Delaware 2022 is what moved it: a 9-county-day series the model beats
#: geography on by 7.1 points. Note
#: what that fold did: the model beats geography there by 11.9 points (6.2
#: against 18.1), the second-largest gain in the panel, and pulling the decay
#: down to reach it helped Pennsylvania too -- PA 2024 went 3.8 to 1.7. Now that
#: neither an
#: out-of-reach series nor a universal vote-by-mail state trains it. Colorado's
#: days were all mail_share ~1.00 with no selection behind them, which is exactly
#: the shape that inflates this. PA 2022 is scored but no longer fitted on
#: (`within_reach`), and the readers stop at Election Day. Note what this does and
#: does not do: it changes what 2026 PUBLISHES, and it changes no number in the
#: validation table, because every fold there refits on its own eleven or twelve
#: series regardless. A constant cannot improve its own score.
MAIL_SELECTION = 0.340

#: How fast that advantage decays as mail reaches more of the electorate. Fitted
#: on the same panel over a 1.00-8.00 grid; every leave-one-state-out fold picks
#: 4.00-6.50, so the shape is not one state's idea. A high power is what
#: separates Pennsylvania (mail reaches 29% of the electorate and its mail voters
#: are 12 points more Democratic than their counties) from Colorado (mail reaches
#: everybody, so there is nobody left for it to select and the correction is
#: under a point).
#:
#: 5.00, 5.75, 4.75, 3.50; 4.25 on the thirteen series that can now teach it -- Iowa
#: 2022 is a long shallow curve and it wants a gentler decay than the panel had
#: settled on. See
#: UNIVERSAL_VBM. Historically: 4.75 while Pennsylvania 2022 was training
#: it -- PA 2022 alone asks for 1.00, the shallowest the grid allows, because in
#: that cycle mail stayed Democratic however far it reached. That is the 2020-22
#: mail regime talking, not a shape 2026 will repeat, and `within_reach` is why
#: it no longer sets this constant. 5.75 on the corrected panel.
MAIL_DECAY = 4.25

#: The correction never exceeds this, in share points. The largest gap between a
#: state's reported party split and its geography on any day this model was
#: fitted on is 18.7 points -- Pennsylvania, 13 October 2024, mail-only, with
#: mail having reached 7% of the electorate. Past 20 points the term is
#: extrapolating beyond anything it has ever been measured against, which in the
#: opening days of a window it otherwise does freely: mail reach is then near
#: zero and the raw formula asks for 36 points. Capping there is worth 2.3 points
#: of error across all days and cuts the estimate's travel across a window from
#: 28 points to 19.
MAX_ADJUSTMENT = 0.20

#: A midterm electorate is smaller than the presidential one the baseline
#: measures, so the same number of mail ballots reaches more of it. The national
#: ratio of ballots cast in 2022 to 2024. The result barely depends on it: the
#: out-of-sample gain runs 3.4 to 3.7 points across the whole range 0.50 to 1.00.
MIDTERM_TURNOUT = 0.73

#: States that mail a ballot to EVERY registered voter without being asked.
#:
#: ⚠️ THE MAIL TERM MUST NOT FIRE HERE, AND IT WAS FIRING AT THE CAP. The term is
#: `alpha * mail_share * (1 - reach) ** decay`, and it prices a CHOICE: asking for
#: a mail ballot is a Democratic act, and it stops meaning anything once everyone
#: is mailed one whether they want it or not. In a universal vote-by-mail state
#: nobody asks, so `mail_share` is ~1.00 by law rather than by selection and
#: carries no information about the voter at all.
#:
#: `reach` was supposed to catch this and cannot, because it divides ballots
#: RETURNED by the electorate. On day one of Colorado's window every voter
#: already HAS a ballot, but few have sent it back, so reach reads 0.08 and the
#: term fires at its 20-point ceiling. Colorado 2022 opened with the model
#: asserting a 20-point Democratic skew that does not exist: its estimate
#: travelled 10.8 points across the window while the reported split moved 0.6.
#:
#: Ballots SENT would be the right denominator and is not available -- Colorado,
#: North Carolina and Pennsylvania 2022 publish no `mail_requested` at all, which
#: is the same "only where the data is richest" trap that has now disqualified
#: three other candidate terms. Whether a state mails everyone a ballot is not a
#: daily measurement, though. It is a fact about the state, and it is knowable.
#:
#: Measured leave-one-state-out: excluding these states from the term AND from
#: the fit is worth +0.39 pp overall, and takes Colorado 2022 from 6.49 to 2.26
#: and Colorado 2024 from 4.89 to 3.23. That is below MIN_GAIN, and MIN_GAIN is
#: the bar for adding a DIMENSION; this is the removal of a term from states
#: where it was never meaningful. The score is the confirmation, not the argument.
#:
#: Vermont mails every voter for GENERAL elections, which is the only kind this
#: tracker follows.
UNIVERSAL_VBM = frozenset({"CO", "CA", "DC", "HI", "NV", "OR", "UT", "VT", "WA"})

#: Empirical, not statistical, and applied as a flat half-width because the error
#: is structural rather than sampling noise. Measured leave-one-state-out on days
#: with at least THIN_BALLOTS ballots in, the model is off by a mean of 3.85
#: points per state-cycle, and the worst is 13.4. Five points sits above that
#: deliberately, because the mail term assumes a DIRECTION (mail voters lean
#: Democratic) that 2022 and 2024 both support and that 2026 need not repeat. It
#: was 10 points when the model was geography alone.
#:
#: ⚠️ IT WENT 5 -> 6 -> 5 IN ONE DAY AND THE ROUND TRIP IS THE POINT. 5 was
#: chosen as "above the measured 3.4" and then nothing refitted it for months.
#: Pennsylvania 2022 arriving took the measurement to 4.4 and the headroom
#: silently became a rounding error, so the band went to 6. Then `within_reach`
#: established that PA 2022 cannot TRAIN a capped model, the twelve reachable
#: series stopped paying for it, and the measurement came back to 3.85.
#:
#: Both moves were measurements. Neither was a preference, and the difference is
#: `test_model_error_is_refitted_from_the_panel` -- which did not exist for the
#: first of them, which is exactly why the band had been wrong for months.
#:
#: ⚠️ AND IT COVERS A 2026 THAT RESEMBLES 2024. On the 2024 folds alone -- the
#: cycle whose mail regime 2026 actually shares -- the measured error is 3.16.
#: On the 2022 folds it is 4.97. If 2026 turns out to look like 2022 instead,
#: this band is too narrow, and PA 2022 sitting in the panel at 13.4 is the
#: standing reminder of how much too narrow.
#:
#: `ev.adapters.az.MODEL_ERROR` is a copy of this, kept because the ingest path
#: must never import this module; `test_model_error_tracks_estimate` guards it.
#: ⚠️ THE RULE, WRITTEN DOWN SO THIS STOPS BEING A JUDGEMENT CALL. This constant
#: has moved 10 -> 5 -> 6 -> 5 -> 6 and every move was measured, but "sits above
#: the mean deliberately" is not a rule, it is a preference wearing one. It is:
#:
#:     MODEL_ERROR = the measured panel mean, plus ~1.5 points of headroom for
#:     the mail term's DIRECTION assumption, rounded to the nearest point.
#:
#: The headroom is not slack. The term assumes mail voters lean Democratic --
#: which 2020, 2022 and 2024 all support and which 2026 need not repeat -- and if
#: that assumption inverts, the error is the term's whole magnitude rather than
#: its residual. A band equal to the mean has no allowance for that.
#:
#: 6 because the panel measures 4.62 (4.62 + 1.5 = 6.1). At 5 the headroom was
#: 0.4, which is a rounding error rather than an allowance. Expect this to move
#: again: the panel gained three series today and each hard one lifts the mean.
MODEL_ERROR = 0.06

#: Below this many ballots the day is thin enough that the composition of the
#: returned ballots is nothing like the composition of the eventual early vote.
#: A row is still written -- the ballots are real -- but confidence is "low".
THIN_BALLOTS = 50_000

#: ...and the band has to say so too, because "low confidence" is a word and the
#: band is the number. The error is almost entirely a function of how much is in:
#: leave-one-state-out, across every validation day,
#:
#:     under 10,000 ballots      mean |error| 17.3 points
#:     10,000 - 50,000           mean |error|  5.1
#:     50,000 - 250,000          mean |error|  4.6
#:     over 250,000              mean |error|  2.6
#:
#: 15 points is the measured mean below THIN_BALLOTS. A South Carolina series
#: that stops eighteen days out at 17,000 all-mail ballots then reads 63% +/-15
#: -- 48 to 78, which is the honest way to say "this is a handful of the most
#: eager mail voters in the state and we do not know" -- instead of 63% +/-5.
THIN_MODEL_ERROR = 0.15

#: Coverage thresholds under which confidence drops to "low".
MIN_COVERAGE = 0.85
MIN_COUNTY_FRACTION = 0.85

def model_error(ballots_used: int | float) -> float:
    """The band's flat half-width for a day with this many ballots in.

    Not a constant, because the error is not one. See THIN_MODEL_ERROR.
    """
    return MODEL_ERROR if ballots_used >= THIN_BALLOTS else THIN_MODEL_ERROR


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
    # The model shown in its two parts, so a reader can see which half of the
    # answer is last election and which half is this one's mail.
    "geo_dem_share", "mail_adjustment", "mail_share", "mail_reach",
    # How much of this figure was COUNTED rather than modelled. `estimate_basis`
    # is "model" (nothing counted, the normal case), "blend" or "reported"; the
    # band is MODEL_ERROR scaled by `modelled_fraction`. Note that `method`
    # above is the algorithm's name and has been that since this table's first
    # row -- these three are about provenance, not arithmetic.
    "measured_fraction", "modelled_fraction", "estimate_basis",
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

    def state_two_party(self, state: str) -> int | None:
        """Two-party votes cast statewide in 2024. The electorate's SIZE.

        Used as the denominator of "how much of this state has the mail channel
        already reached", which is what tells Pennsylvania's self-selected mail
        electorate apart from Colorado's universal one. Two-party rather than
        total votes so that any baseline file satisfying BASELINE_COLUMNS can
        answer it -- the third-party remainder is 2-3% and is absorbed by the
        fitted coefficients either way.
        """
        rows = self.counties(state)
        if not rows:
            return None
        return sum(r.two_party for r in rows) or None


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
# The mail-selection correction
# --------------------------------------------------------------------------
# THE FINDING THIS IMPLEMENTS
# ---------------------------
# County geography cannot see who chose to vote early, and that is what sank the
# geography-only model. But the tracker publishes one thing that CAN see it: how
# many of the returned ballots came by mail.
#
# Across every state-cycle we can score, the ballots already back are more
# Democratic than their counties, and the size of that gap tracks the mail
# channel almost exactly:
#
#   North Carolina 2024   mail-only phase, 20+ points more D than its counties;
#                         the day in-person opens the gap collapses to 4, and it
#                         finishes at 0.7.
#   Kentucky 2024         +22 while mail-only, +10 once in-person is running.
#   Pennsylvania 2024     mail-only from start to finish, so the gap never
#                         collapses: +12 at the close. This is the state the old
#                         model was worst on, and this is why.
#   Colorado 2024         all-mail, and the gap is NEGATIVE (-3.7). Where every
#                         voter is mailed a ballot, mail is not a choice and
#                         selects nobody.
#
# Colorado is the case that fixes the functional form. The correction cannot be
# "mail ballots are Democratic"; it has to be "ASKING for a mail ballot is
# Democratic, and it stops meaning anything once mail has reached everybody". So
# the term decays in `mail_reach` -- mail ballots returned as a share of the
# state's electorate -- and decays fast:
#
#     adjustment = MAIL_SELECTION * mail_share * (1 - mail_reach) ** MAIL_DECAY
#
# Both constants are fitted (`fit_mail_selection`), and every number quoted for
# them in docs/party-estimate.md is LEAVE-ONE-STATE-OUT: the fold that scores
# Pennsylvania has never seen Pennsylvania. That protocol is not decoration. The
# sibling effort in docs/regression.md found seven specifications that each
# appeared to beat "quote the last result" by one to two points and were every
# one of them worse than nothing once measured against the right null.
#
# WHAT THIS TERM ASSUMES, AND WHEN IT WILL BE WRONG
# -------------------------------------------------
# It assumes a DIRECTION: that the voters who ask for a mail ballot lean
# Democratic. That was true in 2020, 2022 and 2024, and it is a fact about a
# particular decade of American politics rather than about arithmetic. If
# Republican mail voting keeps rising and the sign flips in 2026, this term will
# be confidently wrong in exactly the states it currently rescues, and it will be
# wrong by more than the geography-only model ever was. MODEL_ERROR is set above
# the measured mean partly for that reason, and `validate()` re-derives the whole
# thing from output/ on demand, so the day the sign flips is a command, not an
# argument.
# --------------------------------------------------------------------------
def method_split(
    ballots_total: int | float | None,
    mail_returned: int | float | None,
    inperson: int | float | None,
) -> tuple[float, float] | None:
    """(mail, in-person) of the ballots returned so far, or None if unreported.

    The state's own mail figure is believed and EVERYTHING ELSE in its headline
    is treated as in-person. That order matters and is not interchangeable:
    North Carolina reports 297,034 mail, `inperson = 0` and a headline of
    4,520,768 -- its one-stop votes are simply not in that column -- so reading
    `inperson` first would make North Carolina a 100% mail state and hand it the
    largest correction in the table instead of the smallest.

    Where only `inperson` is reported the mail side is whatever the headline has
    left over, which for Maryland is exactly zero: Maryland's tracked early vote
    is its in-person centres and its mail ballots are a separate figure it does
    not publish here. Zero mail then means zero correction, which is right.

    Neither column reported is None, not zero -- THE BLANK RULE. The caller then
    falls back to the geography-only estimate rather than assuming a split.
    """
    if ballots_total is None or ballots_total <= 0:
        return None
    total = float(ballots_total)
    if mail_returned is not None:
        mail = max(0.0, min(total, float(mail_returned)))
    elif inperson is not None:
        mail = max(0.0, total - float(inperson))
    else:
        return None
    return mail, total - mail


def expected_electorate(cycle: int, state: str, baseline: Baseline) -> float | None:
    """How many voters this state is expected to turn out in `cycle`.

    The baseline measures a presidential electorate. A midterm one is smaller, so
    the same number of mail ballots reaches more of it; MIDTERM_TURNOUT carries
    that. Presidential years are the ones divisible by four.
    """
    two_party = baseline.state_two_party(state)
    if not two_party:
        return None
    return two_party * (1.0 if int(cycle) % 4 == 0 else MIDTERM_TURNOUT)


def mail_selection(
    mail: float,
    inperson: float,
    electorate: float | None,
    *,
    alpha: float = MAIL_SELECTION,
    decay: float = MAIL_DECAY,
    cap: float = MAX_ADJUSTMENT,
) -> float | None:
    """How much more Democratic the returned ballots are than their geography.

    In share points, added to the county-weighted number. `None` when it cannot
    be computed at all -- no electorate, no ballots -- which leaves the caller on
    the geography-only estimate rather than on a guess.
    """
    if not electorate or electorate <= 0:
        return None
    total = mail + inperson
    if total <= 0:
        return None
    reach = min(1.0, max(0.0, mail / electorate))
    return min(cap, alpha * (mail / total) * (1.0 - reach) ** decay)


#: The grid `fit_mail_selection` searches for MAIL_DECAY. Wide enough to contain
#: a linear decay at one end and a near-step function at the other; the fit lands
#: at 5.0 on the full panel and 4.00-6.50 across leave-one-state-out folds.
DECAY_GRID = tuple(i / 4 for i in range(4, 33))


@dataclass(frozen=True)
class Observation:
    """One scoreable state-day: what the model saw and what actually happened.

    `truth` is the state's OWN reported two-party registration share of the
    ballots returned that day -- the only ground truth that exists for any of
    this. `geo` is the geography-only estimate for the same day.
    """

    cycle: int
    state: str
    day: str
    geo: float
    truth: float
    ballots: float
    mail: float | None
    inperson: float | None
    electorate: float | None
    baseline_dem_share: float

    def selection_input(self, decay: float) -> float | None:
        """The regressor: `mail_share * (1 - mail_reach) ** decay`, UNCAPPED.

        Uncapped because this is what `fit_mail_selection` solves alpha against,
        and a cap inside the regressor would make that closed form a lie. The cap
        belongs to prediction, where it is what stops the term extrapolating.
        """
        if self.mail is None or self.inperson is None:
            return None
        return mail_selection(self.mail, self.inperson, self.electorate,
                              alpha=1.0, decay=decay, cap=float("inf"))

    def predict(self, alpha: float, decay: float) -> float:
        # Universal vote-by-mail: mail_share is ~1.00 by law, not by choice, so
        # there is no selection for the term to price. See UNIVERSAL_VBM.
        if self.state in UNIVERSAL_VBM:
            return self.geo
        if self.mail is None or self.inperson is None:
            return self.geo
        adjustment = mail_selection(self.mail, self.inperson, self.electorate,
                                    alpha=alpha, decay=decay)
        if adjustment is None:
            return self.geo
        return min(1.0, max(0.0, self.geo + adjustment))


def fit_mail_selection(
    series: Iterable[Sequence[Observation]],
    *,
    decay_grid: Sequence[float] = DECAY_GRID,
) -> tuple[float, float] | None:
    """Fit (MAIL_SELECTION, MAIL_DECAY) on completed series. Weighted least squares.

    `series` is a collection of state-cycle series, NOT a flat list of days, and
    each series is weighted to count once. Pennsylvania contributes 70 days and
    Colorado 5; pooling them by day would fit Pennsylvania and call it a model.

    `decay` is searched on a grid and `alpha` solved in closed form at each point,
    which is exact for the one linear parameter and honest about the one that is
    not. Returns None rather than a number when nothing is fittable.
    """
    rows: list[tuple[Observation, float]] = []
    for one in series:
        usable = [o for o in one if o.selection_input(1.0) is not None]
        if not usable:
            continue
        weight = 1.0 / len(usable)
        rows.extend((o, weight) for o in usable)
    if len(rows) < 2:
        return None

    best: tuple[float, float, float] | None = None
    for decay in decay_grid:
        num = den = 0.0
        for obs, weight in rows:
            x = obs.selection_input(decay)
            num += weight * x * (obs.truth - obs.geo)
            den += weight * x * x
        if den <= 0:
            continue
        alpha = num / den
        rss = sum(
            w * (o.truth - o.geo - alpha * o.selection_input(decay)) ** 2
            for o, w in rows
        )
        if best is None or rss < best[0]:
            best = (rss, alpha, decay)
    return (best[1], best[2]) if best else None


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
    #: The county-weighted 2024 presidential share before the mail term, and the
    #: mail term itself. Published separately so a reader can see how much of the
    #: answer is last election and how much is this one's returned ballots.
    geo_dem_share: float | None = None
    mail_adjustment: float | None = None
    mail_share: float | None = None
    mail_reach: float | None = None
    #: The share of these ballots whose party was actually COUNTED rather than
    #: modelled. 0.0 for every state today: the nine no-registration states have
    #: no such count anywhere, and Arizona -- which does register by party -- has
    #: no county recorder that publishes one. It exists because the arithmetic for
    #: a partial count is settled (`ev.adapters.az.blend_party_share`) and turning
    #: it on later should be data, not a rewrite. At 0.0 every row is exactly what
    #: it was before the field existed.
    measured_fraction: float = 0.0
    measured_dem_share: float | None = None
    state_has_party_reg: bool | None = None
    method: str = METHOD
    source_name: str = SOURCE_NAME
    retrieved_at: str = ""

    def key(self) -> tuple:
        return (int(self.cycle), self.state.upper(), self.day.isoformat())

    @property
    def modelled_fraction(self) -> float:
        return 1.0 - self.measured_fraction

    @property
    def estimate_basis(self) -> str:
        """Whether this row is counted, part-counted or wholly modelled."""
        if self.measured_fraction >= 1.0:
            return "reported"
        if self.measured_fraction <= 0.0:
            return "model"
        return "blend"

    @property
    def est_margin(self) -> float:
        """Democratic minus Republican, two-party. Positive is a D lead."""
        return 2 * self.est_dem_share - 1

    @property
    def lean_vs_baseline(self) -> float | None:
        """The quantity the geography by itself actually measures.

        How much more Democratic (2024 presidential terms) the counties that
        have returned ballots are than the state as a whole. Typically under two
        points, which is the honest size of the geographic signal, and the one
        sentence docs/party-estimate.md was ever willing to defend on its own.

        Measured on `geo_dem_share`, deliberately NOT on the published estimate:
        the mail term is a statement about voters, and folding it in here would
        turn a checkable fact about turnout geography into a model output
        wearing the same label.
        """
        base = self.geo_dem_share if self.geo_dem_share is not None else self.est_dem_share
        if self.baseline_dem_share is None:
            return None
        return base - self.baseline_dem_share

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
            "geo_dem_share": _pct(self.geo_dem_share),
            "mail_adjustment": _pct(self.mail_adjustment),
            "mail_share": _pct(self.mail_share),
            "mail_reach": _pct(self.mail_reach),
            "measured_fraction": _pct(self.measured_fraction),
            "modelled_fraction": _pct(self.modelled_fraction),
            "estimate_basis": self.estimate_basis,
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
    mail_returned: int | None = None,
    inperson: int | None = None,
    has_party_reg: bool | None = None,
    measured_dem: int | None = None,
    measured_rep: int | None = None,
    measured_fraction: float = 0.0,
    retrieved_at: str | None = None,
) -> PartyEstimate | None:
    """One state-day, or None if it cannot be estimated at all.

    `ballots` is {5-digit FIPS: cumulative early ballots}. `statewide_ballots`
    is the state's OWN reported total for that day when it publishes one; it is
    the honest denominator for coverage, because a state can report a statewide
    figure that its county file does not yet add up to (late mail not yet
    attributed to a county is the usual reason).

    `mail_returned` / `inperson` are the state's own method split for that day.
    Both blank means no mail term and the answer is the geography-only estimate,
    which is what the whole model was before this term existed -- a state that
    does not say how its ballots arrived does not get guessed at.

    `measured_dem` / `measured_rep` / `measured_fraction` are for the case where
    part of a state's ballots have a REAL party split (some counties publish one
    even where the state does not). Nothing supplies them today. At the default
    `measured_fraction=0.0` this row is exactly the model.
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

    # The mail term. It is a statement about the STATE's returned ballots, so it
    # uses the state's own headline where there is one -- a county file that is
    # still short would otherwise understate how far mail has reached and hand
    # the state a bigger correction than it has earned.
    geo = share
    split = method_split(statewide_ballots or ballots_used, mail_returned, inperson)
    electorate = expected_electorate(cycle, state, baseline)
    adjustment = mail_share = mail_reach = None
    if split is not None:
        mail, in_person = split
        total = mail + in_person
        mail_share = (mail / total) if total > 0 else None
        mail_reach = (min(1.0, mail / electorate) if electorate else None)
        adjustment = mail_selection(mail, in_person, electorate)
    if adjustment is not None:
        share = min(1.0, max(0.0, geo + adjustment))

    seen = {f for f in ballots if f in baseline}
    missing = [c for c in state_counties if c.fips not in seen]
    lo, hi = coverage_band(geo, coverage, missing, state_counties)
    if adjustment is not None:
        # The bound is on the geography of the ballots we cannot see; the mail
        # term applies to the same ballots either way, so it shifts both ends.
        lo, hi = lo + adjustment, hi + adjustment

    measured_share = None
    two_party = (measured_dem or 0) + (measured_rep or 0)
    if measured_fraction > 0 and two_party and measured_dem is not None:
        measured_share = measured_dem / two_party
        share = measured_fraction * measured_share + (1 - measured_fraction) * share
        lo = measured_fraction * measured_share + (1 - measured_fraction) * lo
        hi = measured_fraction * measured_share + (1 - measured_fraction) * hi
    else:
        measured_fraction = 0.0

    # The band's flat half-width is the model's own error, applied only to the
    # part of the figure the model is responsible for. At measured_fraction=0
    # that is the whole of it, which is every row today.
    half = model_error(ballots_used) * (1.0 - measured_fraction)
    lo, hi = min(lo, hi) - half, max(lo, hi) + half
    # Clamped to [0, 1] at BOTH ends, and around the point estimate. A share
    # cannot be negative and cannot exceed one, and a band that does not contain
    # its own centre is worse than no band.
    lo = min(max(0.0, lo), share)
    hi = max(min(1.0, hi), share)

    electorate_votes = sum(c.two_party for c in state_counties)
    covered = sum(c.two_party for c in state_counties if c.fips in seen)

    return PartyEstimate(
        cycle=int(cycle), state=state, day=day,
        est_dem_share=share, est_dem_lo=lo, est_dem_hi=hi,
        counties_used=used, counties_total=county_count(state),
        ballots_used=ballots_used,
        coverage_share=coverage,
        coverage_electorate=(covered / electorate_votes) if electorate_votes else None,
        baseline_dem_share=baseline.state_dem_share(state),
        geo_dem_share=geo,
        mail_adjustment=adjustment,
        mail_share=mail_share,
        mail_reach=mail_reach,
        measured_fraction=measured_fraction,
        measured_dem_share=measured_share,
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

    ⚠️ NOTHING AFTER ELECTION DAY. `counterfactual.read_series` has filtered
    `days_to_election < 0` since it was written and this module did not, which
    is a straightforward bug rather than a difference of opinion: five
    state-cycles keep filing after their own election as late mail is processed,
    and what those rows contain is not early voting.

    Colorado 2024 is the case that shows the size of it. Colorado votes almost
    entirely by mail, so its post-election row is effectively the FULL COUNT --
    3,276,257 ballots against 1,731,171 cast early. `mature_days()` normalises by
    the series' own maximum, so that one row was inflating Colorado's maturity
    denominator by 89% and pushing genuinely mature days below the threshold,
    and `series[-1]` -- the "final" estimate and the final truth this model
    reports and `within_reach` tests -- was a post-election number.
    """
    rows = _read_csv(Path(out_dir) / "counties" / f"{state.lower()}.csv")
    days: dict[tuple[int, str], dict[str, int]] = defaultdict(dict)
    for row in rows:
        cycle = _num(row.get("cycle"))
        dte = _num(row.get("days_to_election"))
        fips = (row.get("county_fips") or "").strip()
        total = _num(row.get("ballots_total"))
        day = (row.get("date") or "").strip()
        if cycle is None or total is None or len(fips) != 5 or not day:
            continue
        if dte is not None and dte < 0:
            continue
        days[(cycle, day)][fips] = total
    return days


def read_state_daily(out_dir: Path) -> dict[tuple[int, str, str], dict[str, str]]:
    """Same Election Day cut-off as `read_county_ballots`, for the same reason."""
    rows = _read_csv(Path(out_dir) / "ev_state_daily.csv")
    out: dict[tuple[int, str, str], dict[str, str]] = {}
    for row in rows:
        cycle = _num(row.get("cycle"))
        state = (row.get("state") or "").strip().upper()
        day = (row.get("date") or "").strip()
        dte = _num(row.get("days_to_election"))
        if cycle is None or not state or not day:
            continue
        if dte is not None and dte < 0:
            continue
        out[(cycle, state, day)] = row
    return out


def read_party_reg_flags(meta_path: Path) -> dict[str, bool]:
    """`has_party_reg` per state from data/meta/states.csv.

    Arizona reads `true` here and still gets an estimate, because Arizona
    registers by party and its Secretary of State's daily table simply omits it.
    The flag is carried into every row so the CSV itself says whether a real
    party split exists somewhere for that state.

    ⚠️ THAT FLAG IS NOT A REASON TO WITHHOLD THE MODEL, and it was read as one
    for months. The page's gate refused to draw any estimate for a state whose
    flag was true, on the argument that Arizona's real figure exists at its
    county recorders and should be fetched instead. It does exist and it cannot
    be fetched: `az.py`'s RECORDERS list is empty because the recorders serve
    their data from an assigned secure folder. A gate waiting on a number that
    cannot arrive is a permanent blank, not caution.

    The question that decides whether to model is whether the SOURCE reports a
    split, which is a different question, and Arizona is the state that separates
    them. Reported still beats modelled wherever a report exists.
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
                # The method split comes from the state's OWN row and nowhere
                # else. Summing the county file would look equivalent and is
                # not: under partial county coverage the sum understates how far
                # mail has reached and inflates the correction.
                mail_returned=_num((reported or {}).get("mail_returned")),
                inperson=_num((reported or {}).get("inperson")),
                has_party_reg=flags.get(state),
                retrieved_at=stamp,
            )
            if estimate is not None:
                rows.append(estimate)
    return rows


def write(out_dir: Path, rows: Sequence[PartyEstimate], *, rebuild: bool = False) -> dict:
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
        # ...and for the same reason a FULL rebuild replaces rather than merges.
        # A merge cannot forget: a row this model stops producing has nothing to
        # replace it and outlives the change that retired it -- which is exactly
        # what happened when counterfactual.py learned to refuse immature days
        # and 182 of its rows stayed in the published file. `rebuild` is only
        # true when the caller asked for every state and every cycle; a filtered
        # run knows only part of the table and may only merge into it.
        replace=rebuild,
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
    geo_mean_abs_error: float    # same days, geography only -- the model before this
    est_range: float             # how far the estimate moved, points
    truth_range: float           # how far the truth moved, points
    final_estimate: float
    final_truth: float
    baseline: float
    #: The (MAIL_SELECTION, MAIL_DECAY) this series was scored with. Fitted
    #: WITHOUT this state, so the number beside it is out of sample.
    fitted: tuple[float, float] | None = None
    #: False when there was no other state to fit on and the shipped constants
    #: were used instead. Then the row is in-sample and says so.
    held_out: bool = True
    #: False for a series too thin to be a fold. THE ROW IS STILL PRINTED --
    #: hiding what the tracker looks like in September would be its own
    #: dishonesty -- but it is not one twelfth of the headline error. See
    #: `validate` for why the two are different questions.
    scored: bool = True
    #: True when this fold's mail share falls inside the range spanned by the
    #: states the page actually estimates. Conditions on an INPUT, so unlike
    #: `in_range` it can be reported without selecting for a quiet answer.
    #: See `resembles_what_we_publish`.
    resembles_published: bool = False
    #: False when the answer this series ended on sits further from its counties
    #: than `MAX_ADJUSTMENT` lets the model move, so part of its error is
    #: structural at EVERY parameter value. Shown, and never averaged in --
    #: averaging it prices the cap rather than the method. See `within_reach`.
    in_range: bool = True
    #: The most Democratic the mail channel COULD be at this series' final reach
    #: if it drew only the state's most Democratic counties, in order. Points of
    #: Dem two-party share, or None where there is no mail term to bound. It says
    #: WHICH KIND of out-of-range a series is: past a constant someone chose, or
    #: past everything county geography can express. See `geography_ceiling`.
    geo_ceiling: float | None = None

    @property
    def gain(self) -> float:
        """Points of accuracy this model buys over quoting the 2024 result."""
        return self.null_mean_abs_error - self.mean_abs_error

    @property
    def gain_vs_geography(self) -> float:
        """Points it buys over the geography-only model this one replaced."""
        return self.geo_mean_abs_error - self.mean_abs_error


#: A day is "mature" once this share of the series' eventual early vote is in.
#: Before that the returned ballots are almost all mail, whose party mix is
#: nothing like the eventual electorate's, and every method looks terrible.
MATURE_FRACTION = 0.25


def observations(
    out_dir: Path, baseline: Baseline, *, states: Iterable[str] | None = None
) -> dict[tuple[int, str], list[Observation]]:
    """Every state-day in output/ that has county ballots AND a reported party.

    The panel both `fit_mail_selection` and `validate` run on. Keyed by
    (cycle, state) because a series is the unit: Pennsylvania is 70 days and
    Colorado is 5, and pooling them by day would fit Pennsylvania.
    """
    out_dir = Path(out_dir)
    county_dir = out_dir / "counties"
    available = sorted(p.stem.upper() for p in county_dir.glob("*.csv")) if county_dir.is_dir() else []
    wanted = {s.upper() for s in states} if states else None
    statewide = read_state_daily(out_dir)

    panel: dict[tuple[int, str], list[Observation]] = defaultdict(list)
    for state in available:
        if wanted and state not in wanted:
            continue
        state_base = baseline.state_dem_share(state)
        if state_base is None:
            continue
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
            split = method_split(total, _num(reported.get("mail_returned")),
                                 _num(reported.get("inperson")))
            panel[(cycle, state)].append(Observation(
                cycle=cycle, state=state, day=day,
                geo=weighted[0], truth=dem / (dem + rep), ballots=float(total),
                mail=split[0] if split else None,
                inperson=split[1] if split else None,
                electorate=expected_electorate(cycle, state, baseline),
                baseline_dem_share=state_base,
            ))
    for series in panel.values():
        series.sort(key=lambda o: o.day)
    return dict(panel)


def geography_ceiling(state: str, reach: float, baseline: Baseline) -> float | None:
    """The most Democratic a channel that has reached `reach` of the state COULD be.

    Sort the state's counties from most to least Democratic and take the top
    `reach` of its 2024 two-party electorate. That slice's own Democratic share
    is the ceiling of anything the geography term can express at that reach: it
    is what the model would say if every ballot returned so far had come from
    the most Democratic counties in the state, in order, and from nowhere else.
    It falls to the statewide share as `reach` goes to 1, because by then there
    is nowhere left to be selective.

    ⚠️ THIS IS A DIAGNOSTIC AND DELIBERATELY NOT THE CAP, and both alternatives
    were measured leave-one-state-out on the fifteen-series panel:

      * as the cap (`min(k * (ceiling - geo), ...)` in place of MAX_ADJUSTMENT)
        it scores 5.53 against the shipped 4.62 -- far worse, because it is
        generous exactly where reach is small and the model is least trustworthy
        (North Carolina's 5%-of-the-electorate mail channel gets a 29-point
        allowance) and tight where the model is fine.
      * as the `within_reach` rule in place of MAX_ADJUSTMENT it scores 4.81
        against 4.62, because it also excludes Maine 2022 from training, and
        Maine 2022 teaches the term more than its own residual costs.

    What it is for is saying, in the units of the model's own weights, HOW a
    series left the model's range -- whether the answer merely exceeded a
    constant someone chose, or exceeded everything county geography can say.
    Pennsylvania is the case that needs the distinction; see `within_reach`.
    """
    counties = sorted(baseline.counties(state), key=lambda c: -c.dem_share)
    total = sum(c.two_party for c in counties)
    if not counties or total <= 0 or reach <= 0:
        return None
    target = min(1.0, reach) * total
    taken = dem = 0.0
    for c in counties:
        if taken + c.two_party >= target:
            share = (target - taken) / c.two_party
            dem += share * c.votes_dem
            taken = target
            break
        taken += c.two_party
        dem += c.votes_dem
    return dem / taken if taken > 0 else None


def within_reach(series: Sequence[Observation]) -> bool:
    """Can this model's CAPPED correction reach where this series ended up?

    A training series is only informative about `MAIL_SELECTION` and `MAIL_DECAY`
    if the answer it demands is one the model is allowed to give. The fit solves
    alpha on the UNCAPPED regressor (see `Observation.selection_input`) while
    prediction clips at `MAX_ADJUSTMENT`, which is harmless for a series inside
    the cap and incoherent for one outside it: least squares keeps enlarging
    alpha to reach a target that prediction will clip away, and every OTHER
    state pays for the overshoot.

    ⚠️ THE SERIES THIS EXCLUDES TODAY IS PENNSYLVANIA 2022, AND THE REASON IS NOT
    THAT IT SCORES BADLY. Its early electorate finished 25.3 points from its
    counties; the model may move 20. No value of alpha reaches it, so the whole
    of its residual is structural, and fitting on it moves the constants for the
    twelve series that ARE reachable. Measured leave-one-state-out, removing it
    from TRAINING (not from scoring) takes the panel from 4.09 to 3.85, and the
    2024 folds -- the cycle 2026 actually resembles -- from 3.61 to 3.16.

    Why PA 2022 and nothing else: it is the only all-mail state in the cycle when
    mail voting was at its most party-polarised, so it is the one series where
    `mail_share` is pinned at 1.00 while the mail electorate was 76.5% Democratic
    against counties saying 49.1. That regime ended -- PA's own mail electorate
    was 62.7% Democratic by 2024, a 27.6-point normalisation as Republicans came
    back to mail voting -- which is why the series cannot train a model aimed at
    2026 and why it is still SCORED, loudly, as what this model does when the
    world moves outside its range.

    Measured on the final mature day, which is the fully-formed early electorate
    and the least noisy point in the series. `docs/party-estimate.md` records the
    alternatives that were measured against this one.
    """
    if not series:
        return False
    last = series[-1]
    return abs(last.truth - last.geo) <= MAX_ADJUSTMENT


def mature_days(series: Sequence[Observation]) -> list[Observation]:
    """The days on which at least MATURE_FRACTION of the eventual vote was in.

    Before that the returns are mail-dominated and every method looks terrible;
    scoring on them would flatter this model, which is a mail model. The last day
    is the floor, so a series is never scored on nothing.
    """
    final_volume = max((o.ballots for o in series), default=0) or 1
    ripe = [o for o in series if o.ballots / final_volume >= MATURE_FRACTION]
    return ripe or list(series[-1:])


def mail_share_of(series: Sequence[Observation]) -> float | None:
    """The mail share of the early vote on a series' last day, or None."""
    if not series:
        return None
    last = series[-1]
    if last.mail is None or last.inperson is None:
        return None
    total = last.mail + last.inperson
    return None if total <= 0 else last.mail / total


def published_mail_share_range(out_dir: Path) -> tuple[float, float] | None:
    """The mail-share range spanned by the states this page ACTUALLY estimates.

    Derived from `output/party_estimate.csv` and `ev_state_daily.csv`, never
    hardcoded, so it stays true as states start and stop reporting.
    """
    published = {row.get("state", "").strip().upper()
                 for row in _read_csv(Path(out_dir) / ESTIMATE_FILENAME)}
    published.discard("")
    if not published:
        return None
    best: dict[tuple[str, str], tuple[int, float]] = {}
    for row in _read_csv(Path(out_dir) / "ev_state_daily.csv"):
        state = (row.get("state") or "").strip().upper()
        if state not in published:
            continue
        mail, inperson = _num(row.get("mail_returned")), _num(row.get("inperson"))
        dte = _num(row.get("days_to_election"))
        if mail is None or inperson is None or dte is None or dte < 0:
            continue
        total = mail + inperson
        if total <= 0:
            continue
        key = (state, (row.get("cycle") or "").strip())
        if key not in best or dte < best[key][0]:
            best[key] = (dte, mail / total)
    shares = [v for _, v in best.values()]
    return (min(shares), max(shares)) if shares else None


def resembles_what_we_publish(
    series: Sequence[Observation], span: tuple[float, float] | None
) -> bool:
    """Is this fold structurally like a state the page actually estimates?

    ⚠️ THIS CONDITIONS ON AN INPUT, WHICH IS THE WHOLE POINT, AND IT IS THE ONE
    DIFFERENCE FROM `within_reach`. Mail share is known for Ohio today with no
    party data anywhere; `within_reach` is a function of the ANSWER, so
    filtering a headline on it selects the folds where least happened. This
    selects nothing of the kind, and the panel proves it: the stratum KEEPS
    Kentucky 2024 at 6.5, the second-worst fold inside it, and EXCLUDES
    Pennsylvania 2024 at 1.7, one of the best in the panel.

    What it excludes is a REGIME. Pennsylvania and Iowa are 100% mail and
    Colorado 98%; every state this page estimates runs 3.7% to 62.7%. In an
    all-mail state, asking for a ballot is not a choice a voter made, or -- in
    Pennsylvania's case -- it is the only choice there is, and the selection it
    implies is the largest in the panel. That regime cannot occur in Ohio,
    Texas, Tennessee, South Carolina, Virginia or Wisconsin, so the error it
    produces is not the error a reader of those estimates should expect.

    The headline still averages EVERYTHING -- see `format_validation`. This is
    reported beside it, not instead of it.
    """
    if span is None:
        return False
    share = mail_share_of(series)
    return share is not None and span[0] <= share <= span[1]


def validate(
    out_dir: Path, baseline: Baseline, *, states: Iterable[str] | None = None
) -> list[Validation]:
    """Score the estimate against every state-cycle that reports party.

    This is the honest measure of the feature and the reason
    docs/party-estimate.md exists.

    LEAVE ONE STATE OUT. The mail term has two fitted constants, so scoring a
    series with constants fitted on that same series would be marking its own
    homework -- and docs/regression.md is the standing example in this repo of a
    model that looked like it had found something until it was measured against
    the right null. Every series here is scored with constants refitted on the
    OTHER states, and North Carolina 2022 cannot train the fold that scores North
    Carolina 2024. Where there is no other state to fit on (the single-state test
    fixture) the shipped constants are used and `held_out` says so.

    It compares a modelled two-party PRESIDENTIAL share against a reported
    two-party REGISTRATION share, which are not the same object -- Kentucky is
    full of registered Democrats who vote Republican, and that alone is most of
    Kentucky's remaining error. The comparison is still the one a reader will
    make, so it is the one we publish.
    """
    panel = observations(out_dir, baseline, states=states)
    scoreable = {k: v for k, v in panel.items() if len(v) >= 2}
    # A series is fittable only if it ever got past THIN_BALLOTS. North Carolina
    # 2026 is three days and EIGHT ballots, six of them from registered
    # Democrats; as a training series it would count for as much as
    # Pennsylvania's seventy days, so it gets no vote in the constants.
    #
    # ⚠️ AND, SINCE 2026-09-06, NO VOTE IN THE HEADLINE ERROR EITHER -- which is
    # the same sentence, and it took a second look to notice it applies twice.
    # The row was being averaged in as a full fold: it contributed an MAE of
    # 13.6 to a mean of 3.9 across twelve other series, and moved the reported
    # accuracy of this model by nearly a point on the strength of eight ballots.
    #
    # `mature_days()` could not catch it, because it normalises by the SERIES'
    # OWN maximum: all three of those days are 100% of "the eventual vote" when
    # the eventual vote so far is eight. A running series cannot be its own
    # yardstick -- the identical trap counterfactual.py had, found the same week.
    #
    # The row is still PRINTED. "What does this look like in September" and
    # "how accurate is this model" are different questions and only the second
    # one is a mean.
    # ⚠️ TWO SETS, AND CONFLATING THEM IS A REAL BUG I WROTE ONCE. `thick` is
    # every series solid enough to be a measurement, and it is what `scored`
    # means -- what the headline error averages. `trainable` is the subset that
    # can also TEACH the mail term, which is a strictly stronger requirement:
    # a series the capped model cannot reach says nothing about what alpha
    # should be, but it says a great deal about what this model does when the
    # world moves outside its range, and that belongs in the headline.
    #
    # Pennsylvania 2022 is thick and not trainable. It is scored at 13.4 and it
    # fits nothing.
    thick = {
        k: mature_days(v) for k, v in scoreable.items()
        if max(o.ballots for o in v) >= THIN_BALLOTS
    }
    # ...and a universal vote-by-mail state cannot TEACH the term either: its
    # days are all mail_share ~1.00 with no selection behind them, which is
    # exactly the shape that inflates alpha for everyone else. See UNIVERSAL_VBM.
    trainable = {k: v for k, v in thick.items()
                 if within_reach(v) and k[1] not in UNIVERSAL_VBM}

    published_span = published_mail_share_range(out_dir)

    results: list[Validation] = []
    for (cycle, state), series in sorted(scoreable.items()):
        others = [v for k, v in trainable.items() if k[1] != state]
        fitted = fit_mail_selection(others) if others else None
        held_out = fitted is not None
        alpha, decay = fitted or (MAIL_SELECTION, MAIL_DECAY)

        ripe = mature_days(series)
        predicted = [o.predict(alpha, decay) for o in ripe]
        truths = [o.truth for o in ripe]
        errors = [abs(p - t) for p, t in zip(predicted, truths)]
        nulls = [abs(o.baseline_dem_share - o.truth) for o in ripe]
        geo = [abs(o.geo - o.truth) for o in ripe]
        last = series[-1]

        results.append(Validation(
            cycle=cycle, state=state, days=len(series),
            final_error=(last.predict(alpha, decay) - last.truth) * 100,
            mean_abs_error=sum(errors) / len(errors) * 100,
            max_abs_error=max(abs(o.predict(alpha, decay) - o.truth)
                              for o in series) * 100,
            null_mean_abs_error=sum(nulls) / len(nulls) * 100,
            geo_mean_abs_error=sum(geo) / len(geo) * 100,
            est_range=(max(predicted) - min(predicted)) * 100,
            truth_range=(max(truths) - min(truths)) * 100,
            final_estimate=last.predict(alpha, decay) * 100,
            final_truth=last.truth * 100,
            baseline=last.baseline_dem_share * 100,
            fitted=(alpha, decay), held_out=held_out,
            scored=(cycle, state) in thick,
            # ⚠️ FROM `within_reach` DIRECTLY, never from `trainable` membership.
            # They came apart the moment a second reason not to train on a series
            # existed: a universal vote-by-mail state is untrainable but perfectly
            # in range, and deriving this from `trainable` dropped Colorado out of
            # the headline average AND printed the reach explanation against it,
            # which is the wrong reason for the wrong state.
            in_range=(cycle, state) not in thick or within_reach(thick[(cycle, state)]),
            resembles_published=resembles_what_we_publish(series, published_span),
            geo_ceiling=(
                None if last.mail is None or not last.electorate
                else (lambda c: None if c is None else c * 100)(
                    geography_ceiling(state, min(1.0, last.mail / last.electorate),
                                      baseline))
            ),
        ))
    return results


def format_validation(results: Sequence[Validation]) -> Iterator[str]:
    yield (f"{'cycle':>5s} {'st':3s} {'days':>4s} {'est':>6s} {'truth':>6s} {'base':>6s} "
           f"{'final':>7s} {'MAE':>6s} {'geo':>6s} {'null':>6s} {'v.geo':>6s} "
           f"{'v.null':>6s} {'moved':>6s} {'truth+-':>7s}")
    for r in sorted(results, key=lambda r: (r.state, r.cycle)):
        yield (f"{r.cycle:5d} {r.state:3s} {r.days:4d} {r.final_estimate:6.1f} "
               f"{r.final_truth:6.1f} {r.baseline:6.1f} {r.final_error:+7.1f} "
               f"{r.mean_abs_error:6.1f} {r.geo_mean_abs_error:6.1f} "
               f"{r.null_mean_abs_error:6.1f} {r.gain_vs_geography:+6.1f} "
               f"{r.gain:+6.1f} {r.est_range:6.1f} {r.truth_range:7.1f}"
               + ("" if r.held_out else "  (in sample: no other state to fit on)")
               + ("" if r.scored else
                  f"  (SHOWN, NOT SCORED: peaks at under "
                  f"{THIN_BALLOTS:,} ballots)")
               + ("" if r.in_range or not r.scored else
                  "  (SHOWN, NOT AVERAGED: ends beyond the model's reach)"))
    if not results:
        yield "(no state-cycle in output/ both reports party and has county rows)"
        return
    # ⚠️ TWO REASONS A SERIES IS SHOWN AND NOT AVERAGED, and they are different
    # facts about it. `scored` is thickness -- whether this is a measurement at
    # all. `in_range` is reach -- whether it is a measurement OF THIS MODEL, or
    # whether part of its error is guaranteed by the cap whatever the constants
    # say. The rule was already written for the first and applied to North
    # Carolina 2026. It is the same rule, and it now also covers Pennsylvania
    # 2022, whose early electorate finished 25.3 points from its counties when
    # the model may move 20.
    # ⚠️ THE HEADLINE AVERAGES EVERY SCORED SERIES, AND `in_range` IS A REPORTED
    # DIAGNOSTIC, NEVER A FILTER. It briefly was a filter here, and
    # counterfactual.py talked me out of it with the better argument: reach is a
    # condition on the TRUTH, so filtering on it systematically selects the
    # states where least happened and reports a headline for the quiet half of
    # the panel. That the exclusion is justified per-series -- part of PA 2022's
    # error really is structural at any parameter value -- does not make the
    # SELECTION unbiased, because the same test is what makes a series hard.
    #
    # It also cost 0.74 points of reported error for no change in the model,
    # which is the shape of a number improving because of how it is counted.
    # The two models now answer this the same way; sibling modules disagreeing
    # about one concept is the bug class that produced the worst defects here.
    keep = [r for r in results if r.scored] or list(results)
    n = len(keep)
    yield ""
    thin = [r for r in results if not r.scored]
    out = [r for r in results if r.scored and r.in_range is False]
    if thin:
        yield (f"Averages are over the {n} series this model can be measured on. "
               + ", ".join(f"{r.state} {r.cycle}" for r in thin)
               + " shown above and left out: a series that cannot fit the "
                 f"constants cannot be 1/{n + 1} of their error either.")
    if out:
        yield ("BEYOND THE MODEL'S REACH, and averaged in anyway: "
               + ", ".join(f"{r.state} {r.cycle} at {r.mean_abs_error:.1f} pp"
                           for r in out)
               + ". Their early electorates finished further from their counties "
                 "than the model is allowed to move, so part of that error is "
                 "structural at every parameter value. That is a fact about the "
                 "series and NOT a reason to drop it: the same test that says a "
                 "series is out of reach is the test that says the most happened "
                 "there, so excluding it would report the quiet half of the "
                 "panel. It is in the mean, and it is why the band is not "
                 "narrower than it is.")
        # ...and for the ones that are past county geography ITSELF, say so. A
        # series can leave the model's range in two different ways and only one
        # of them is about MAX_ADJUSTMENT: see `geography_ceiling`.
        beyond = [r for r in out
                  if r.geo_ceiling is not None and r.final_truth > r.geo_ceiling]
        if beyond:
            yield ("...and raising the cap would not reach "
                   + ", ".join(f"{r.state} {r.cycle}" for r in beyond)
                   + " either: "
                   + ", ".join(f"{r.state} {r.cycle} finished at "
                               f"{r.final_truth:.1f} against a ceiling of "
                               f"{r.geo_ceiling:.1f}" for r in beyond)
                   + ". The ceiling is what this model would say if every ballot "
                     "back so far had come from the most Democratic counties in "
                     "the state, in order, and from nowhere else. Finishing above "
                     "it is past everything the county map can express rather "
                     "than past a constant someone chose, so no cap and no "
                     "reparameterisation of the mail term reaches it.")
    scored = keep
    yield (f"mean |final error| = {sum(abs(r.final_error) for r in scored) / n:.1f} pp   "
           f"mean MAE = {sum(r.mean_abs_error for r in scored) / n:.1f} pp   "
           f"geography-only MAE = {sum(r.geo_mean_abs_error for r in scored) / n:.1f} pp   "
           f"null model MAE = {sum(r.null_mean_abs_error for r in scored) / n:.1f} pp")
    yield (f"gain vs geography-only = {sum(r.gain_vs_geography for r in scored) / n:+.1f} pp   "
           f"gain vs null = {sum(r.gain for r in scored) / n:+.1f} pp")
    like = [r for r in scored if r.resembles_published]
    if like and len(like) != len(scored):
        span = ", ".join(f"{r.state} {r.cycle}" for r in like)
        yield (f"AND THE NUMBER A READER OF THIS PAGE ACTUALLY WANTS: "
               f"{sum(r.mean_abs_error for r in like) / len(like):.1f} pp over the "
               f"{len(like)} series structurally like the states this page really "
               f"estimates ({span}). Every state estimated here runs between 4% "
               f"and 63% mail; Pennsylvania and Iowa are 100% and Colorado 98%, "
               f"and an all-mail regime cannot occur in Ohio or Texas. This "
               f"conditions on an INPUT, not on the answer -- it keeps the "
               f"second-worst fold inside it and drops one of the best -- and it "
               f"is reported BESIDE the headline above, never instead of it.")
    if all(r.held_out for r in results):
        yield ("Every row is LEAVE-ONE-STATE-OUT: the constants scoring a state "
               "were fitted without it, and without that state's other cycles.")
    yield ("columns: final = signed error on Dem two-party share at the last day; "
           "MAE/geo/null over days with >=25% of the series' final early vote; "
           "geo = the geography-only model this one replaced; "
           "null = quoting the state's 2024 presidential result; "
           "moved = how far the estimate travelled across the window; "
           "truth+- = how far the reported party split travelled.")
