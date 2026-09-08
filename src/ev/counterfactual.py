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
early electorate against the 2022 one -- and, since Pennsylvania's 2020 curve
landed, on the 2022 one against 2020 -- and scores it against the only
compositional ground truth that exists: the party registration those same states
reported for those same ballots. EIGHT state-cycles qualify: CO, FL, KY, MD, ME,
NC and PA on 2024-vs-2022, plus PA alone on 2022-vs-2020.

    mean absolute error, mature days   9.99 points of margin
    the same for the "nothing changed" null   9.27 points
    what county geography buys        -0.73 points

It does not beat the null. Fitting a scale factor to it leave-one-state-out makes
it worse still (-2.10), and the fitted scales disagree by a factor of fifty and in
both signs (-0.65 to +2.56). The mechanism is the one `docs/party-estimate.md`
already found: every tracked state publishes every one of its counties, so the
ballot weights are close to proportional to county size and the weighted mean is
arithmetically pinned near the state's own last result. County geography moves one
and a half points across a window while the composition it is standing in for
moves nine.

`reach_bound` says the same thing without needing any ground truth at all, and
says it harder: |shift_pp| can never exceed the county mix's total-variation
distance times the state's widest-minus-narrowest county margin, and SIX OF THE
EIGHT measured compositional changes are larger than that ceiling. The two inside
it are Colorado, whose registration moved 1.98 points, and PA 2022, which moved
5.57 against a bound of 6.28 -- and on that one the model HAD the room and
reported -3.11 where the truth was +5.57, which is the other half of the finding:
being able to say a thing is not saying it.

`mix_split` is that bound with the inequality replaced by an equality. The truth
column is itself a weighted mean over counties, so it decomposes exactly into the
part the county mix moved and the part that moved between voters of the SAME
county -- and the second part is 84% to 135% of it on every scoreable series,
Pennsylvania 2024 96% (-26.54 of -27.64). The first part is `shift_pp`'s own
construction with the unit gap removed, valued in the registration points the
truth is measured in rather than presidential ones, and it buys -0.05 pp over the
null (+0.30 on the seven folds that preceded PA 2022, and +0.03 there with North
Carolina dropped). So county geography is not measuring the right thing in the
wrong unit. There was nothing in that dimension to measure, and Pennsylvania is
where that is easiest to see: the same voters, in the same places, choosing a
different channel, which is BEHAVIOUR and is exactly what this construction
freezes.

`fit_constant` closes the last open question in the file. The strongest thing ever
measured on this panel was a fitted constant, +3.49 pp leave-one-STATE-out, and
the only objection to it was that the panel held ONE cycle transition so a state
holdout never held the transition out. PA 2020 made a second one, the two point
opposite ways (+5.57 then -27.64), and held out on a cycle the constant scores
-3.35.

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
from typing import Callable, Iterable, Iterator, Sequence

from . import publish
from .calendar import CYCLES, election_date
from .estimate import (
    Baseline,
    CountyLean,
    county_count,
    load_baseline,
    method_split,
)

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
#: trap above.
#:
#: 2022 became a target on 2026-09-07, when `pa.py` recovered Pennsylvania's 2020
#: mail curve. Until then every fold in the panel was 2024-vs-2022 -- ONE cycle
#: transition -- which is the fact that disqualified the fitted constant: leave-
#: one-STATE-out never holds a transition out, so a constant learned from one
#: transition was being scored on itself. PA is the only state that can reach
#: 2020 and every other state simply produces no 2022 row, which is the same
#: refusal a state with no reference series has always got.
#:
#: ⚠️ AND THE SECOND TRANSITION IS THE OPPOSITE OF THE FIRST. PA's early
#: electorate moved +5.57 registration points from 2020 to 2022 and -27.64 from
#: 2022 to 2024. The constant fitted on the seven 2024 folds is -10.22; held out
#: on the 2022 fold it scores 12.62 against a null of 2.48, a gain of -10.14. See
#: `fit_constant`.
REFERENCE_CYCLE: dict[int, int] = {2026: 2024, 2024: 2022, 2022: 2020}

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

#: A day is "mature" once this share of a finished early-vote curve is in.
#: Before that the returns are almost all mail and their composition is nothing
#: like the eventual early electorate's; scoring there would make every method
#: look terrible and would tell you nothing about any of them. Same threshold and
#: same reasoning as `estimate.MATURE_FRACTION`.
#:
#: ⚠️ THE DENOMINATOR IS THE REFERENCE CYCLE'S FINAL EARLY VOTE, never the
#: current series' own. `mature_days()` divides by `final_ballots()` -- the
#: largest the series has EVER reached -- which is correct for a completed cycle
#: and catastrophically wrong for a live one: North Carolina's eight 2026 ballots
#: are 100% of what 2026 has reached so far, so a self-referential rule calls
#: them a mature electorate. The reference cycle's curve is finished, which is
#: exactly why it can serve as the yardstick for both sides. `completeness()` is
#: that measure and `is_comparable()` is the gate; see THE MATURITY GATE below.
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

#: THE MATURITY GATE. A reference curve smaller than this is not an early
#: electorate, it is a stub, and nothing can be measured against it. South
#: Carolina's 2022 county series tops out at 16,975 ballots against 1,579,112 in
#: 2024 -- comparing the two produced thirteen published rows whose "shift"
#: was the difference between a state and a rounding error. Reuses THIN_BALLOTS
#: because it is the same judgement about the same quantity.
MIN_REFERENCE_BALLOTS = THIN_BALLOTS

#: The measured error of this method, in percentage points of margin, applied as
#: a flat half-width because it is structural rather than sampling noise -- it
#: does NOT shrink as more ballots come in.
#:
#: TWO RULES SET IT, and the second has bound since 2026-09-07: it is the measured
#: mean absolute error rounded up to the next half point, and it is NEVER NARROWER
#: THAN THE LARGEST |shift_pp| THIS MODEL PUBLISHES. A band that does not contain
#: the model's own output would be the model making a claim outside its stated
#: error.
#:
#: It is the mean absolute distance, over mature days and averaged across the
#: state-cycles that can be scored at all, between the compositional shift this
#: model reports and the shift the same states' own reported party registration
#: says actually happened (10.39 points, rounded up to the next half point). That
#: is not a like-for-like unit -- a registration point is not a presidential
#: point -- and it is the closest thing to a measurement that exists. Read it as
#: "the size of the compositional change that county geography does not see",
#: because that is what it is: the geography moves one point and the registration
#: moves ten.
#:
#: This is a MEASUREMENT, not a constant, and it moves when the tracker learns
#: more, or when the domain it is measured over is corrected. Its history:
#:
#:   11.5  KY, MD, ME and NC were the only four state-cycles with a county
#:         series AND a party split in two consecutive cycles.
#:   10.5  Florida's 2022 and 2024 county curves came out of the Internet
#:         Archive and made a fifth (measured 10.37).
#:    9.5  THE MATURITY GATE landed (measured 9.09). Scoring and publishing now
#:         share one domain: days at a comparable share of a finished early-vote
#:         curve. The immature days this removed were not hard cases the model
#:         was failing, they were phase differences it was never entitled to
#:         call composition.
#:   12.0  PENNSYLVANIA (measured 11.74). PA 2022 had been refused outright over
#:         0.04% of unreadable party labels, and a 2024 fold cannot exist without
#:         a 2022 reference, so the panel had been fitted with the most volatile
#:         state in the country missing from it. PA's mail electorate moved 27.6
#:         points of margin between 2022 and 2024 -- more than twice the next
#:         largest -- as Republicans who had boycotted mail voting in 2022 came
#:         back to it. County geography sees none of that: the model reports
#:         -1.26 where the registration moved -27.64, and early in the window it
#:         reports +10.48 while the truth is already -12.08, wrong by 22.6 points
#:         AND pointing the wrong way.
#:
#:         ⚠️ THIS IS NOT AN OUTLIER TO BE TRIMMED. 2026 is a midterm and 2022 is
#:         the only midterm reference the panel has; a band fitted without the
#:         hardest midterm case is a band that will be wrong exactly when it is
#:         read. The number got worse because the measurement got honest.
#:   10.5  COLORADO (measured 10.39). `co.fetch_history` had been aborting its
#:         whole walk on one post-election workbook, so CO 2022 did not exist and
#:         CO 2024 had no reference; recovering it added a SEVENTH fold.
#:
#:         ⚠️ READ THIS DROP AS DILUTION, NOT AS THE MODEL IMPROVING. Nothing
#:         about the method changed and no existing fold moved: Colorado's early
#:         electorate simply barely moved between 2022 and 2024 (its registration
#:         shifted 1.98 points against Pennsylvania's 27.64), and a mean over
#:         series falls when an easy series joins. The band's job is unchanged
#:         and Pennsylvania's 24.99-point error is still inside the panel it is
#:         fitted on; +-10.5 does not cover that error and never claimed to. This
#:         is a MEAN absolute error, not a maximum.
#:
#:         Colorado earns its place for a second reason, though: it is the first
#:         and only series this method was arithmetically ABLE to reach -- see
#:         `within_reach` -- and it promptly demonstrated why reach must never be
#:         used to filter the headline.
#:   10.5  PENNSYLVANIA 2020 (measured 9.99, AND THE BAND DID NOT MOVE). `pa.py`
#:         rebuilt PA's 2020 mail curve
#:         from the application-level file, which made 2022 a target cycle and gave
#:         the panel its SECOND CYCLE TRANSITION -- an eighth fold, PA 2022-vs-2020.
#:
#:         ⚠️ THIS IS DILUTION FOR THE SECOND TIME RUNNING, AND HARDER TO READ THAN
#:         THE LAST ONE, BECAUSE THE BAND AND THE VERDICT MOVED IN OPPOSITE
#:         DIRECTIONS. The new fold's own MAE is 7.21, below the panel mean, so the
#:         mean falls. Its GAIN is -4.73, the worst in the panel, and the headline
#:         went -0.15 -> -0.73. Nothing improved. A mean absolute error is a
#:         statement about typical size, not about whether the model is any good,
#:         and these two entries are the clearest demonstration in this file that
#:         the two questions are separate.
#:
#:         The fold earns its place several times over. It is the first series this
#:         method was able to reach whose composition actually MOVED -- 5.57 points
#:         against a bound of 6.28 -- and the model still gets it wrong by 8.68 and
#:         POINTS THE WRONG WAY, agreeing in sign on 7% of its days. And it makes
#:         leave-one-CYCLE-out possible for the first time; see `fit_constant`.
#:
#:         ⚠️ AND THE VALUE STAYS AT 10.5, WHICH IS WHY THIS ENTRY EXISTS. The rule
#:         has always been "the measured mean, rounded up to the next half point",
#:         and there is a second clause that has never bound before and binds now:
#:         THE BAND MAY NEVER BE NARROWER THAN THE LARGEST SHIFT THIS MODEL
#:         PUBLISHES. Pennsylvania publishes +10.48. A band of 10.0 would mean the
#:         model had published a claim outside its own stated error, which is the
#:         one thing docs/counterfactual.md says it has not earned. So 9.99 rounds
#:         to 10.0 and the floor holds it at 10.5.
#:         `test_the_published_table_never_carries_an_immature_row` is where that
#:         floor is asserted, and it is the test that caught this.
#:
#:  11.5   11.39  Iowa's 2022 backfill added a NINTH fold, and it is the first
#:         entry in this history where the band widened because a hard series
#:         arrived rather than because the measurement got honest about an old
#:         one. IA 2024's own MAE is 22.52 against a panel mean of 9.99 without
#:         it: Iowa's early electorate moved 23.12 registration points, the
#:         second largest move in the panel after Pennsylvania's, and county
#:         geography reported -1.32 of it against a reach bound of 4.15. So the
#:         mean rose 9.99 -> 11.39 and the first rule -- the measured mean,
#:         rounded up to the next half point -- puts the band at 11.5. The second
#:         rule does not bind here: the largest shift the table publishes is
#:         still Pennsylvania's +10.48, which 11.5 covers with room.
#:
#:         ⚠️ AND THE GAIN IMPROVED WHILE THE BAND WIDENED, which is the mirror
#:         image of the entry above it and is worth reading beside it. IA's own
#:         gain is +0.86 -- county geography is less wrong there in absolute
#:         terms than the null is -- so the headline moved -0.73 -> -0.55 while
#:         the error grew by a point and a half. Neither number is evidence about
#:         the other, and neither is evidence that anything improved.
#:
#:  11.0   10.62  FOUR BACKFILLS AT ONCE -- Louisiana, Oklahoma, Oregon,
#:         Delaware and Wisconsin -- took the panel from nine folds to THIRTEEN,
#:         and the first rule binds again: 10.62 rounds up to 11.0, and the
#:         second rule's floor is unchanged at Pennsylvania's +10.48, which 11.0
#:         still covers. So the band NARROWS by half a point.
#:
#:         ⚠️ AND THIS IS DILUTION IN ITS PUREST FORM SO FAR, BECAUSE ALL FOUR OF
#:         THE NEW FOLDS ARE A SINGLE MATCHED DAY. Oklahoma publishes one county
#:         snapshot per cycle (day 0), so do Louisiana and -- for its party split
#:         -- Oregon: OK 2022, OK 2024, LA 2024 and OR 2024 are each ONE
#:         day-0 comparison, against Maine's 21 and Pennsylvania's 23. Their
#:         gains are +0.98, +4.23, -0.50 and -0.14, a mean of +1.14 against the
#:         nine-fold panel's -0.55, and that alone moves the headline
#:         -0.55 -> -0.03 and the mean error 11.39 -> 10.62. Nothing about the
#:         method changed and no existing fold moved. See `score_panel` for why
#:         a minimum day count was measured and NOT adopted.
#:
#:         Wisconsin and Delaware add published rows and no folds at all:
#:         Wisconsin does not register voters by party and Delaware's adapter
#:         publishes no party split, so neither has a truth column to be scored
#:         against.
#:
#: The verdict does not move toward shipping at any point: the gain over the
#: no-change null is -0.15 pp on six folds, -0.15 pp on seven, -0.73 pp on eight,
#: -0.55 pp on nine and -0.03 pp on thirteen, and the bar is +1.00.
#:
#: `test_model_error_matches_the_measured_validation` refits it from output/ and
#: fails if the data moves away from it.
MODEL_ERROR_PP = 11.0

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
    # How far through each electorate is, against the reference cycle's finished
    # early vote. The gate that admitted this row is `>= MATURE_FRACTION` on both.
    "completeness", "reference_completeness",
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


def ballots_in(ballots: dict[str, int]) -> int:
    """Ballots on one day, skipping counties that reported nothing.

    THE BLANK RULE on the read side: a county absent from `ballots`, or carrying
    None, has not reported -- it is not a county with zero ballots.
    """
    return sum(v for v in ballots.values() if v is not None)


def mix_distance(
    now: dict[str, int], reference: dict[str, int], baseline: Baseline
) -> float | None:
    """Total-variation distance between two days' COUNTY ballot mixes, 0 to 1.

    Half the sum of absolute differences in each county's share of the day's
    ballots, over the counties that can be weighted at all. It is the same
    statistic `composition_distance` reports for age/race/sex, on the one
    dimension that is priced -- and here it is not published as a distance but
    used to bound what that dimension is arithmetically able to say. See
    `reach_bound`.
    """
    keys = {f for f in set(now) | set(reference) if f in baseline}
    here = sum(v for f, v in now.items() if f in keys and v is not None and v >= 0)
    there = sum(
        v for f, v in reference.items() if f in keys and v is not None and v >= 0
    )
    if here <= 0 or there <= 0:
        return None
    return 0.5 * sum(
        abs(max(now.get(f) or 0, 0) / here - max(reference.get(f) or 0, 0) / there)
        for f in keys
    )


def margin_span(
    now: dict[str, int], reference: dict[str, int], baseline: Baseline
) -> float | None:
    """The most Democratic minus the most Republican county EITHER day can use.

    The support of the comparison, not the state: a county that reported on
    neither day cannot carry weight on either side, so it cannot widen what the
    difference is able to reach.
    """
    margins = [
        county_margin(lean)
        for lean in (baseline.get(f) for f in set(now) | set(reference))
        if lean is not None
    ]
    return (max(margins) - min(margins)) if margins else None


def reach_bound(
    now: dict[str, int], reference: dict[str, int], baseline: Baseline
) -> float | None:
    """The largest |shift_pp| county geography COULD report on this pair of days.

    ⚠️ THIS IS A HARD BOUND, NOT AN ESTIMATE, and it is the counterfactual's
    version of `estimate.within_reach`.

    `shift_pp` is `sum_c (w_now,c - w_ref,c) * margin_c`, where the two weight
    vectors each sum to one. Write `d_c` for the difference; then `sum_c d_c = 0`,
    so subtracting any constant from every margin leaves the sum unchanged. Take
    the midpoint of the county margins and every recentred margin is at most half
    the span in absolute value, which gives

        |shift_pp|  <=  TV(county mix)  x  (widest county margin - narrowest)

    and the bound is TIGHT -- attained when every county gaining share is the most
    Democratic one and every county losing it the most Republican. `mix_distance`
    is the first factor and `margin_span` the second.

    What makes it worth computing: it is a statement about the DIMENSION rather
    than about this method's parameters, it needs no ground truth, and it is
    available live. Given how little the county mix actually moved between two
    cycles, no reweighting of the counties -- no alternative early electorate over
    the same map -- could have produced a compositional margin shift larger than
    this. When the measured compositional change is larger than the bound, part of
    the error is structural at every possible county weighting, exactly the way
    `estimate.within_reach` is structural at every value of MAIL_SELECTION.

    Measured on the published tree it binds on SIX OF THE EIGHT scoreable
    state-cycles and on 65 of their 98 days.

    ⚠️ AND IT IS A BOUND ON THE MODEL'S MAGNITUDE, NEVER ON ITS ACCURACY. Two
    series sit inside their own bound. One is Colorado, where nothing happened.
    The other is PA 2022, where 5.57 points happened against a bound of 6.28 --
    the county mix had the room -- and the model reported -3.11, the opposite
    direction, for the worst gain in the panel. Refusing to publish where the
    bound is small was measured for the same reason and is worse still: it keeps
    the days where the mix moved most, which early in a window is phase rather
    than composition, and on this panel it keeps PA 2024's +10.48 and scores
    -3.15. See `within_reach` and docs/counterfactual.md.
    """
    distance = mix_distance(now, reference, baseline)
    span = margin_span(now, reference, baseline)
    if distance is None or span is None:
        return None
    return distance * span


def mix_split(
    now: dict[str, tuple[int, int]], reference: dict[str, tuple[int, int]]
) -> tuple[float, float] | None:
    """Split the MEASURED compositional change into between- and within-county.

    ⚠️ THIS IS THE REACH BOUND'S EXACT TWIN, AND IT IS TIGHTER. `reach_bound` says
    how far county geography COULD have moved at the worst case; this says how far
    it ACTUALLY did, in the truth's own unit, with no inequality and no slack.

    The truth column is the state's reported party registration of returned
    ballots, `(D-R)/(D+R)`. Write a county's two-party registration base as
    `n_c = d_c + r_c` and its registration margin as `m_c = 100(d_c-r_c)/n_c`;
    then the statewide figure is itself a weighted mean, `M = Σ w_c m_c` with
    `w_c = n_c / Σ n`, and the measured change decomposes EXACTLY:

        M_now - M_ref  =  Σ (w_now,c - w_ref,c) · m_ref,c     BETWEEN counties
                       +  Σ w_now,c · (m_now,c - m_ref,c)     WITHIN counties

    The first term is `shift_pp`'s own construction -- the same county mix change,
    against the same counties -- valued in REGISTRATION points instead of
    presidential ones. So it is the county dimension with the unit gap removed:
    if county geography were measuring the right thing in the wrong unit, this
    term would carry the answer. The second term is everything that happened
    inside counties, which no county-level model of any kind can see.

    ⚠️ AND ON THE PUBLISHED TREE THE SECOND TERM IS THE WHOLE THING. On the final
    matched day it is 84% (NC) to 135% (PA 2022) of the measured change --
    Pennsylvania 2024 96.0%, -26.54 of -27.64. The between-county term never
    exceeds 1.98 points in any fold, against measured changes of 1.98 to 27.64.
    Scored as a predictor of the truth it buys -0.05 pp over the no-change null on
    the eight folds; on the seven that preceded PA 2022 it bought +0.30, and even
    that was carried by one state -- dropping North Carolina left +0.03.

    PA 2022 is worth reading on its own: between -1.98, within +7.55, measured
    +5.57. THE COUNTY MIX MOVED THE WRONG WAY, which is why `shift_pp` reports
    -3.11 there. Small and blind is one failure mode; small, blind and
    anticorrelated is the one that produces a -4.73. See docs/counterfactual.md.

    Returns (between, within) over the counties BOTH days reported a party split
    for, or None when there are none. A county with a party split on one side only
    is dropped rather than guessed at -- THE BLANK RULE -- which is also what
    keeps the identity exact: both terms are over one common support.
    """
    shared = set(now) & set(reference)
    if not shared:
        return None

    def parts(reg: dict[str, tuple[int, int]]) -> tuple[dict[str, float], dict[str, float]] | None:
        base = {f: reg[f][0] + reg[f][1] for f in shared}
        total = sum(base.values())
        if total <= 0:
            return None
        return (
            {f: base[f] / total for f in shared},
            {f: 100.0 * (reg[f][0] - reg[f][1]) / base[f] for f in shared},
        )

    here, there = parts(now), parts(reference)
    if here is None or there is None:
        return None
    w_now, m_now = here
    w_ref, m_ref = there
    between = sum((w_now[f] - w_ref[f]) * m_ref[f] for f in shared)
    within = sum(w_now[f] * (m_now[f] - m_ref[f]) for f in shared)
    return between, within


# --------------------------------------------------------------------------
# THE DIMENSIONS THAT VARY *INSIDE* A COUNTY
#
# `mix_split` proves the county dimension is empty: 84% to 135% of every measured
# compositional change happened between voters of the SAME county. That is a
# statement about counties, and the obvious next question is whether anything
# this tracker collects that varies WITHIN a county can see what counties cannot.
# There are three candidates, and all three were measured on 2026-09-07.
#
#   METHOD (mail vs in-person).  Collected everywhere -- `mail_returned` and
#   `inperson` are on every state row -- and it is the mechanism the Pennsylvania
#   finding names. `method_mix_distance` measures how far it moved and the answer
#   kills it: THE METHOD MIX IS FROZEN IN EXACTLY THE FOLDS THE FINDING LIVES IN.
#   Pennsylvania has no early in-person voting AT ALL -- `pa.py` writes `inperson
#   = None` and the law is why -- so its observed early electorate is 100% mail in
#   2020, 2022 and 2024 and the mix moves 0.00 points. Maryland reaches the same
#   place for a lesser reason: its published file is the in-person centres and its
#   mail file is served as a corrupt zip, so `mail_returned` is NOT REPORTED and
#   the split lands at 0.000 mail on both sides. Three of the eight folds have no
#   method dimension to look at, and PA 2024's -27.64 is two of them -- the channel
#   switch that produced it was FROM ELECTION DAY, which this tracker does not
#   observe and never will.
#
#   AGE / RACE / SEX.  `output/demo/*.csv`, and the coverage is thinner than the
#   method split: over the eight scoreable folds it is age and race in North
#   Carolina alone and sex in NC and Maryland. GA and MI hold one 2024 day each
#   with no reference cycle; South Carolina has no party registration to be
#   scored against and its 2022 curve is the 16,975-ballot stub the maturity gate
#   already refuses. `DemoDay` also carries NO party split, and no vendored table
#   in this repo prices a demographic band, so the between-band term cannot be
#   computed at all -- only bounded. See `required_span`.
#
#   SUB-COUNTY GEOGRAPHY.  `output/towns/*.csv` is Maine and only Maine, and it
#   is the one within-county dimension that carries the truth's own unit, so the
#   decomposition is EXACT there. `nested_split` is that measurement and it is
#   the most decisive of the three: 463 towns inside 16 counties -- a partition
#   twenty-nine times finer, in the most town-fragmented state in the country --
#   move the geographic share of Maine's -13.99 from 5.2% to 11.9%. The other
#   87% happened inside individual Maine towns.
#
# None of the three clears MIN_GAIN and none of them ever could; the numbers are
# in docs/counterfactual.md. What is in the code is the machinery that measures
# the refusal rather than arguing it, which is the same job `reach_bound`,
# `mix_split`, `fit_scale` and `fit_constant` already do.
# --------------------------------------------------------------------------
def method_mix(row: dict[str, str] | None) -> float | None:
    """Mail's share of the ballots a state has returned so far, 0 to 1.

    Reads the split through `estimate.method_split`, which is the canonical
    reader and whose ORDER matters: the state's own mail figure is believed and
    everything else in its headline is treated as in-person. North Carolina
    reports 297,034 mail, `inperson = 0` and a headline of 4,520,768 -- its
    one-stop votes are simply not in that column -- so reading `inperson` first
    would make North Carolina a 100% mail state.
    """
    if not row:
        return None
    split = method_split(_num(row.get("ballots_total")),
                         _num(row.get("mail_returned")),
                         _num(row.get("inperson")))
    if split is None:
        return None
    mail, inperson = split
    total = mail + inperson
    return None if total <= 0 else mail / total


def method_mix_distance(
    now: dict[str, str] | None, reference: dict[str, str] | None
) -> float | None:
    """How far the MAIL/IN-PERSON mix moved between two days, in points.

    The same total-variation statistic `composition_distance` reports for
    age/race/sex and `mix_distance` for counties. Over two bands it collapses to
    the absolute change in mail's share, which is what makes it directly
    comparable with them.

    ⚠️ AND ON THE FINAL MATCHED DAY OF THE THIRTEEN SCOREABLE FOLDS IT IS ZERO IN
    FIVE OF THEM, INCLUDING BOTH PENNSYLVANIAS. PA 2022 0.00, PA 2024 0.00, IA
    0.00, MD 0.00, OR 0.00, NC 1.52, CO 1.60, KY 7.83, OK 2024 8.33, ME 12.61,
    LA 15.02, FL 17.44, OK 2022 28.08 -- against county-mix distances of 2.41 to
    9.48. Pennsylvania has no early in-person voting, so its observed early
    electorate is 100% mail in every cycle; Iowa and Oregon report one
    undifferentiated mail figure; Maryland publishes only its in-person centres,
    so its mail side is unreported rather than absent and a working mail file
    would give it a mix. The dimension the Pennsylvania finding names is the one
    dimension Pennsylvania does not have.

    ⚠️ AND WHERE IT DOES MOVE, MOST OF THE MOVEMENT IS PHASE -- BUT NOT ALL OF IT,
    AND THE EXCEPTION IS WHY `fit_method_gap` EXISTS. Kentucky's mix "moves" 58
    points at seven days out, because its 2022 reference series is a single day
    near the close while 2024 is still mail-only at that point. That is a calendar
    fact about when in-person voting opens, not a compositional one, and it is why
    the fitted specification scores -15.42 on Kentucky. The maturity gate exists
    for the same reason and does not catch this, because both days are mature.
    Oklahoma's 28.08 is NOT that: both of its days are day-0 snapshots at 100%
    completeness, and its mail share really did fall from 63.2% in 2020 to 35.1%
    in 2022 as the pandemic surge unwound. That one fold takes the method
    dimension from "refused by `required_span` everywhere but Florida" to
    "reachable in two of thirteen", which is why the refusal is now a measurement.
    """
    here, there = method_mix(now), method_mix(reference)
    if here is None or there is None:
        return None
    return abs(here - there) * 100.0


def required_span(distance: float | None, change: float) -> float | None:
    """The band-margin span a dimension would need to carry `change` on its own.

    `reach_bound` inverted, and the form that makes an unpriced dimension
    answerable. For ANY partition into bands, the between-band term is
    `Σ (w_now,g - w_ref,g)·m_ref,g` and the weight differences sum to zero, so

        |between|  <=  TV(band mix)  ×  (widest band margin - narrowest)

    exactly as for counties. Turn it round and a dimension whose mix moved
    `distance` points can only have carried a change of `change` points if its
    bands' registration margins span at least `change / distance`. A registration
    margin lives in [-100, +100], SO NO SPAN CAN EXCEED 200 AND ANY REQUIREMENT
    ABOVE THAT IS ARITHMETICALLY IMPOSSIBLE.

    That is the whole answer for the dimensions this repo cannot price. On the
    final matched day of each fold:

        method   PA 2022 inf, PA 2024 inf, IA inf, OR inf, MD inf (the mix did
                 not move at all), NC 752, OK 2024 227, KY 169, CO 124, ME 111,
                 LA 65, OK 2022 38, FL 28
        age      NC 91          race  NC 271        sex  NC 468, MD 872

    Seven of the thirteen folds put the method dimension past the 200-point
    ceiling or out of existence. Four more need 65 to 169, against a channel gap
    of 18 to 42 points measured directly in the five series whose window opens
    mail-only -- on a pure-mail day the reported margin IS the mail channel's, and
    the in-person channel's follows from the final day's identity -- and confirmed
    at 16 to 35 by the four folds that publish the crosstab outright.

    ⚠️ AND TWO ARE NOW INSIDE THAT ENVELOPE, WHERE ONE USED TO BE. Florida's 28 is
    the one this argument always reported. OK 2022's 38 arrived on 2026-09-08 and
    Oklahoma publishes no crosstab, so it cannot be checked directly. Eleven of
    thirteen is still a majority and it is not the clean sweep the eight-fold panel
    had, so the refusal moves from this bound to `fit_method_gap`'s measurement --
    exactly as it did for counties when PA 2022 came into reach.

    Returns None when the mix did not move, which is the "arithmetically
    impossible" case and is reported as such rather than as a large number.
    """
    if distance is None or distance <= 0:
        return None
    return abs(change) / (distance / 100.0)


def nested_split(
    now: dict[str, tuple[int, int]],
    reference: dict[str, tuple[int, int]],
    group_of: Callable[[str], str],
) -> tuple[float, float, float] | None:
    """`mix_split` with a level of geography inserted UNDERNEATH the county.

    THE ANSWER TO "IS IT THE COUNTY PARTITION THAT IS TOO COARSE?", and it is no.

    Same construction, same exactness, one more term. Write a unit's two-party
    registration base as `n_u` and its margin as `m_u`, let `W_c` be a group's
    share and `M_c` its margin; then the measured change decomposes with no
    residual into three parts rather than two:

        M_now - M_ref  =  Σ_c (W_now,c - W_ref,c)·M_ref,c            BETWEEN GROUPS
                       +  Σ_c W_now,c·Σ_{u∈c}(w_now,u|c - w_ref,u|c)·m_ref,u
                                                             BETWEEN UNITS IN A GROUP
                       +  Σ_u w_now,u·(m_now,u - m_ref,u)              WITHIN UNITS

    The first term is `mix_split`'s between-county term. The second is the whole
    of what a finer geography adds. `group_of` maps a unit key to its group; for
    Maine's towns that is the first five digits of the ten-digit county
    subdivision GEOID, which is the county by construction and never a name join.

    ⚠️ MEASURED ON MAINE -- THE ONLY STATE IN THIS REPO WITH A SUB-COUNTY UNIT
    CARRYING PARTY -- THE SECOND TERM IS 0.94 POINTS OF A 13.99-POINT MOVE.

        measured change              -13.99
        between counties              -0.73   ( 5.2%)
        between towns within counties -0.94   ( 6.7%)
        inside towns                 -12.21   (87.3%)

    463 towns inside 16 counties. Twenty-nine times finer than the county
    partition, in the most town-fragmented state in the country, and it takes
    geography from 5% of the answer to 12%. The between-town term is larger than
    the between-county term -- 0.70 against 0.45 averaged over Maine's 21 scored
    days -- and both are an order of magnitude short of the thing they are
    standing in for. Refining the geography does not refine the finding; it
    confirms it. And a town term is available in ONE of the eight folds, which is
    the disqualification this repo applies to every term that is only defined
    where the data is richest.

    Returns (between groups, between units within groups, within units) over the
    units BOTH days reported a party split for -- THE BLANK RULE, and what keeps
    all three terms over one common support so they add up.
    """
    shared = {
        u for u in set(now) & set(reference)
        if now[u][0] + now[u][1] > 0 and reference[u][0] + reference[u][1] > 0
    }
    if not shared:
        return None

    def parts(reg: dict[str, tuple[int, int]]):
        base = {u: reg[u][0] + reg[u][1] for u in shared}
        total = sum(base.values())
        if total <= 0:
            return None
        return ({u: base[u] / total for u in shared},
                {u: 100.0 * (reg[u][0] - reg[u][1]) / base[u] for u in shared})

    here, there = parts(now), parts(reference)
    if here is None or there is None:
        return None
    w_now, m_now = here
    w_ref, m_ref = there

    members: dict[str, list[str]] = defaultdict(list)
    for unit in shared:
        members[group_of(unit)].append(unit)

    between_groups = between_units = 0.0
    for group, units in members.items():
        share_now = sum(w_now[u] for u in units)
        share_ref = sum(w_ref[u] for u in units)
        if share_now <= 0 or share_ref <= 0:
            continue
        group_margin_ref = sum(w_ref[u] * m_ref[u] for u in units) / share_ref
        between_groups += (share_now - share_ref) * group_margin_ref
        between_units += share_now * sum(
            (w_now[u] / share_now - w_ref[u] / share_ref) * m_ref[u] for u in units
        )
    within_units = sum(w_now[u] * (m_now[u] - m_ref[u]) for u in shared)
    return between_groups, between_units, within_units


# --------------------------------------------------------------------------
# THE PARTITION THAT CUTS ACROSS COUNTIES: county x method
#
# `nested_split` put a level UNDER the county and found 6.7% of Maine. This is
# the other direction, and it is the one docs/counterfactual.md said did not
# exist: "no state publishes party crossed with method". That was true of
# `schema.py` and false of the sources -- FL renders two per-county voted tables
# each split by party, KY carries DEM/REP columns per channel, NC and ME publish
# the channel and the party on the same ballot row, CO ships a county x party
# matrix per channel. `schema.MethodDay` and `output/methods/<st>.csv` are where
# the crosstab now lives, and this is the machinery that scores it.
#
# The predictor is `mix_split`'s between term over the FINER partition -- cells
# are (county, method) rather than counties -- and it needs nothing fitted,
# because it is valued in the truth's own registration unit exactly as
# `mix_only` is. `nested_split` with `county_of_cell` as the grouper takes it
# apart into the county term, the method-within-county term, and the residue
# inside cells, and the first two sum to the fine between term exactly.
#
# ⚠️ AND ITS COVERAGE IS THE POINT. See `format_validation` and
# docs/counterfactual.md: it is defined on the folds whose sources are richest
# and undefined on the panel's two largest measured changes.
# --------------------------------------------------------------------------
CELL_SEPARATOR = "|"


def cell_key(county_fips: str, method: str) -> str:
    """The key of one county-x-method cell. Never parsed apart except here."""
    return f"{county_fips}{CELL_SEPARATOR}{method}"


def county_of_cell(key: str) -> str:
    """The county a cell belongs to -- the grouper `nested_split` wants.

    A slice of the key, exactly as `TownDay.county_fips` is a slice of its
    GEOID, so the cell -> county rollup is arithmetic rather than a join.
    """
    return key.split(CELL_SEPARATOR, 1)[0]


def reconciled_cells(
    county_party: dict[str, tuple[int, int]],
    method_party: dict[str, tuple[int, int]],
) -> dict[str, tuple[int, int]] | None:
    """The county x method cells, but ONLY where they add up to their county.

    ⚠️ THE GUARD THAT STOPS A ONE-BAND CROSSTAB FROM PRETENDING TO BE A
    PARTITION, and without it this dimension scores off the wrong electorate.

    Colorado is the case. Its 2024 workbook ships a per-county matrix for mail
    AND one for in-person, so its cells partition the county. Its 2022 workbook
    ships only the in-person matrix -- Colorado is an all-mail state, so that is
    **0.86%** of the ballots -- and `co.py` refuses to derive the mail band by
    subtraction, correctly, because it is a number Colorado did not print. Fed
    to `nested_split` unguarded, the two sides' shared support becomes Colorado's
    in-person voters alone and the split reports -2.45 / +0.00 / -11.87 against a
    measured change of -1.98: three terms of a decomposition of something else.

    So the test is a reconciliation and not a threshold: a county's cells count
    only if their two-party bases sum EXACTLY to the county's own, on the same
    day, in the same table -- and if any county that reports a party split fails
    that, the whole day has no cell term. Exact rather than approximate because
    all five sources are exact aggregations of the same ballots; a mismatch is a
    band we are not being shown, which is precisely what must not be averaged
    over. It is also a standing drift tripwire.

    Returns None when there is no complete crosstab for this day.
    """
    if not county_party or not method_party:
        return None
    totals: dict[str, list[int]] = {}
    for key, (dem, rep) in method_party.items():
        entry = totals.setdefault(county_of_cell(key), [0, 0])
        entry[0] += dem
        entry[1] += rep
    for fips, (dem, rep) in county_party.items():
        if totals.get(fips) != [dem, rep]:
            return None
    if set(totals) != set(county_party):
        return None
    return dict(method_party)


def completeness(ballots: dict[str, int], reference_final: int) -> float | None:
    """How far along a day is, measured against a FINISHED early-vote curve.

    The yardstick is the reference cycle's own final early vote, for both sides
    of the comparison. That choice is the whole point: the current cycle's final
    is not knowable while the current cycle is running, and a rule that divides
    by "the largest this series has reached so far" declares day one complete.

    It is deliberately not capped at 1.0. A cycle whose early vote overshoots the
    last one reads above 100%, which is true and worth seeing.
    """
    if reference_final <= 0:
        return None
    return ballots_in(ballots) / reference_final


def is_comparable(
    now_ballots: dict[str, int],
    reference_ballots: dict[str, int],
    reference_final: int,
    *,
    fraction: float = MATURE_FRACTION,
    floor: int = MIN_REFERENCE_BALLOTS,
) -> str | None:
    """None if the two days can be compared; otherwise why they cannot.

    THE MATURITY GATE, and the reason it exists: this model is validated only on
    mature days, and until 2026-09-06 it PUBLISHED on every day. 181 of the 260
    rows in `output/counterfactual.csv` sat outside the domain it had been
    scored on, and they were not marginal -- their mean |shift| was 6.4 points
    against 1.2 for the mature rows, running as far as 26.2, and 24 of them
    carried the top `confidence` label because completeness was not one of the
    things confidence looked at. Maine published an 11.7-point shift off TWO
    ballots.

    None of that is composition. Early in a window the returns are mail, and a
    mail electorate's geography is nothing like the eventual early electorate's;
    the difference between a 3%-complete snapshot and a finished one is phase,
    and this model reports phase as composition. So it refuses instead. Three
    conditions, all of them computable live:

      1. the reference curve is a real early electorate, not a stub;
      2. the reference day is itself mature within that curve;
      3. this cycle's day has reached the same share of it.
    """
    if reference_final < floor:
        return (f"reference curve tops out at {reference_final:,} ballots, "
                f"below the {floor:,} needed to measure against")
    there = completeness(reference_ballots, reference_final)
    if there is None or there < fraction:
        return (f"reference day is {0.0 if there is None else there:.0%} of that "
                f"curve, under the {fraction:.0%} maturity floor")
    here = completeness(now_ballots, reference_final)
    if here is None or here < fraction:
        return (f"this day is {0.0 if here is None else here:.0%} of the reference "
                f"curve, under the {fraction:.0%} maturity floor")
    return None


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
    #: days_to_election -> {5-digit FIPS: (party_dem, party_rep)} of the ballots
    #: that county has returned. Read only for `mix_split`, which needs the TRUTH
    #: dimension one level below the state total; it is never an input to
    #: `shift_pp`, which is county geography and nothing else.
    county_party: dict[int, dict[str, tuple[int, int]]] = field(default_factory=dict)
    #: days_to_election -> the state's own published row for that day
    state_rows: dict[int, dict[str, str]] = field(default_factory=dict)
    #: days_to_election -> {dimension: {bucket: ballots}}
    demo: dict[int, dict[str, dict[str, int]]] = field(default_factory=dict)
    #: days_to_election -> {10-digit cousub GEOID: (party_dem, party_rep)}. Read
    #: only for `nested_split`, which needs a unit BELOW the county carrying the
    #: truth's own dimension. Maine is the only state that has one. Never an
    #: input to `shift_pp`.
    town_party: dict[int, dict[str, tuple[int, int]]] = field(default_factory=dict)
    #: days_to_election -> {"<fips>|<method>": (party_dem, party_rep)}. The
    #: PARTY-BY-METHOD crosstab, from `output/methods/<st>.csv`. Read only for
    #: `cell_key`-ed splits, which need a partition that cuts ACROSS counties
    #: rather than under them; never an input to `shift_pp`.
    method_party: dict[int, dict[str, tuple[int, int]]] = field(default_factory=dict)

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
        if cycle is None or dte is None or dte < 0 or len(fips) != 5:
            continue
        dem, rep = _num(row.get("party_dem")), _num(row.get("party_rep"))
        if dem is not None and rep is not None and dem + rep > 0:
            slot(cycle).county_party.setdefault(dte, {})[fips] = (dem, rep)
        if total is None:
            continue
        slot(cycle).counties.setdefault(dte, {})[fips] = total

    for row in _read_csv(out_dir / "ev_state_daily.csv"):
        if (row.get("state") or "").strip().upper() != state:
            continue
        cycle, dte = _num(row.get("cycle")), _num(row.get("days_to_election"))
        if cycle is None or dte is None or dte < 0:
            continue
        slot(cycle).state_rows[dte] = row

    for row in _read_csv(out_dir / "towns" / f"{state.lower()}.csv"):
        cycle, dte = _num(row.get("cycle")), _num(row.get("days_to_election"))
        geoid = (row.get("town_geoid") or "").strip()
        dem, rep = _num(row.get("party_dem")), _num(row.get("party_rep"))
        if cycle is None or dte is None or dte < 0 or len(geoid) != 10:
            continue
        if dem is None or rep is None or dem + rep <= 0:
            continue
        slot(cycle).town_party.setdefault(dte, {})[geoid] = (dem, rep)

    for row in _read_csv(out_dir / "methods" / f"{state.lower()}.csv"):
        cycle, dte = _num(row.get("cycle")), _num(row.get("days_to_election"))
        fips = (row.get("county_fips") or "").strip()
        method = (row.get("method") or "").strip()
        dem, rep = _num(row.get("party_dem")), _num(row.get("party_rep"))
        if cycle is None or dte is None or dte < 0 or len(fips) != 5 or not method:
            continue
        if dem is None or rep is None or dem + rep <= 0:
            continue
        slot(cycle).method_party.setdefault(dte, {})[cell_key(fips, method)] = (dem, rep)

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
    #: Each side's ballots as a share of the REFERENCE cycle's final early vote
    #: -- the only finished yardstick available while this cycle is running.
    #: Published so a reader can see how far through each electorate is, and
    #: because THE MATURITY GATE that admitted the row is the same number.
    completeness: float | None = None
    reference_completeness: float | None = None
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
            "completeness": _pp(self.completeness),
            "reference_completeness": _pp(self.reference_completeness),
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

    # THE MATURITY GATE. Publish only on the domain the model is scored on.
    reference_final = reference.final_ballots()
    refusal = is_comparable(
        now.counties.get(dte, {}), reference.counties.get(ref_dte, {}), reference_final
    )
    if refusal:
        log.debug("%s %s d-%d: no row -- %s", state, now.cycle, dte, refusal)
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
        completeness=completeness(now.counties.get(dte, {}), reference_final),
        reference_completeness=completeness(
            reference.counties.get(ref_dte, {}), reference_final),
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


def write(out_dir: Path, rows: Sequence[Counterfactual], *, rebuild: bool = False) -> dict:
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
        # ...and for the same reason a FULL rebuild replaces rather than merges.
        # A merge cannot forget: when the maturity gate landed, the 182 rows this
        # model had published from immature days had nothing to replace them and
        # would have outlived the bug that made them. `rebuild` is only true when
        # the caller asked for every state and every cycle -- a `--state NC` run
        # must not delete the other nine states.
        replace=rebuild,
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
    #: The largest |shift| the county mix change on this pair of days could have
    #: produced under ANY reweighting of the counties -- see `reach_bound`. A day
    #: whose `truth` exceeds it is one the dimension could not have reached.
    reach: float | None = None
    #: `mix_split` on this pair of days: how much of the MEASURED change was the
    #: county mix moving (`mix_only`, the same construction as `shift` but valued
    #: in the truth's own registration unit) and how much happened INSIDE counties
    #: (`inside`), where no county-level model can see it. They sum to the
    #: county-derived truth exactly. Both are None where the state does not report
    #: party on county rows.
    mix_only: float | None = None
    inside: float | None = None
    #: The same decomposition over the COUNTY x METHOD partition, from
    #: `output/methods/<st>.csv`. `cell_only` is the fine between term -- what
    #: the county-and-channel mix carried, in the truth's own unit -- and
    #: `cell_county`/`cell_method` are `nested_split`'s two halves of it, so
    #: `cell_county + cell_method == cell_only` exactly. `cell_county` is the
    #: like-for-like county baseline: it is `mix_only` recomputed over the cell
    #: support, which is the only county number the fine one may be compared
    #: with. All None where the state publishes no crosstab, which is most of
    #: the panel.
    cell_only: float | None = None
    cell_county: float | None = None
    cell_method: float | None = None
    cell_inside: float | None = None
    #: The SIGNED change in mail's share of the returned ballots, in points --
    #: `method_mix_distance` before the absolute value. It is the regressor of
    #: the one within-county specification that survives everywhere the crosstab
    #: does not: `prediction = method_delta x g`, with the mail-minus-in-person
    #: registration gap `g` fitted leave-one-state-out. See `fit_method_gap`.
    #: 0.0 -- not None -- where the state has one channel, because "the mix did
    #: not move" is a measurement and the specification collapses to the null
    #: there. None only when the split cannot be read at all.
    method_delta: float | None = None


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
    #: `reach_bound` on the final matched day: the largest |shift| the county mix
    #: change could have produced there under any reweighting of the counties.
    reach: float | None = None
    #: False when the measured compositional change on that day is LARGER than
    #: the bound -- the dimension could not have reached the answer, so part of
    #: the error is structural at every county weighting. The exact analogue of
    #: `estimate.Validation.in_range`, and see `within_reach` for what it does
    #: and deliberately does not do to the headline.
    in_range: bool = True
    #: `mix_split` on the final matched day, plus the between-county term's own
    #: score over the whole series. `mix_only` is `shift`'s own construction with
    #: THE UNIT GAP REMOVED -- the same county mix change, the same counties,
    #: valued in the registration points the truth is measured in -- so
    #: `mix_gain` is what county geography buys when it is not being asked to
    #: cross a unit at all. REPORTED, NEVER A FILTER: it does not enter `gain`,
    #: which stays the published method's, and it changes no headline.
    final_mix_only: float | None = None
    final_inside: float | None = None
    mean_abs_mix_only: float | None = None
    mix_mean_abs_error: float | None = None
    #: LEAVE ONE CYCLE OUT. The constant fitted on the folds from every OTHER
    #: cycle transition, and what it scores on this one. None when the panel holds
    #: a single transition and there is no other cycle to fit on -- which is
    #: precisely the objection that disqualified the constant, encoded rather than
    #: argued. See `fit_constant`.
    cycle_constant: float | None = None
    cycle_constant_mean_abs_error: float | None = None
    #: Every scored day's MEASURED change, in order. Carried so the leave-one-
    #: cycle-out constant can be REFITTED with a state dropped -- a jackknife over
    #: a frozen parameter is not a jackknife, and the constant's +2.19 is one fold
    #: wide. See `constant_gain` and `constant_jackknife`.
    truths: tuple[float, ...] = ()
    #: THE MAIL/IN-PERSON MIX, scored as a specification rather than bounded:
    #: `prediction = method_delta x g`, with the channel gap `g` fitted on every
    #: OTHER state. The only within-county dimension defined in all thirteen
    #: folds. REPORTED, NEVER A FILTER: it does not enter `gain`.
    method_gap: float | None = None
    method_mean_abs_error: float | None = None
    #: The no-change null OVER THE DAYS THE MIX CAN BE READ ON, for the same
    #: reason `cell_null_mean_abs_error` exists: a dimension must be scored
    #: against the null on its own support or the number prices the day
    #: selection. Today the two sets coincide in every fold.
    method_null_mean_abs_error: float | None = None
    method_days: int = 0
    #: THE COUNTY x METHOD CELL MIX, scored the same way `mix_only` is: as a
    #: predictor of the measured registration change, in the truth's own unit,
    #: with nothing fitted. `cell_county_mean_abs_error` is the LIKE-FOR-LIKE
    #: county baseline -- the county term recomputed over the same cell support
    #: -- because the fine partition drops cells that only one side reported and
    #: comparing it with `mix_mean_abs_error`, which is over a different support,
    #: would credit the method dimension with a change of population.
    #: REPORTED, NEVER A FILTER: none of these enters `gain`.
    final_cell_only: float | None = None
    final_cell_county: float | None = None
    final_cell_method: float | None = None
    final_cell_inside: float | None = None
    mean_abs_cell_only: float | None = None
    cell_mean_abs_error: float | None = None
    cell_county_mean_abs_error: float | None = None
    #: The no-change null OVER THE CELL DAYS ONLY. `null_mean_abs_error` is over
    #: every scored day, and the cell term is defined on a subset of them, so
    #: scoring one against the other would price the day selection rather than
    #: the dimension. Today the two sets coincide in every fold that has a
    #: crosstab at all; this makes it impossible for a future one not to.
    cell_null_mean_abs_error: float | None = None
    cell_days: int = 0
    #: The multiplier fitted on every OTHER state's cell term, and what it
    #: scores here. Refitted in every fold -- a jackknife over a frozen
    #: parameter is not a jackknife.
    cell_scale: float | None = None
    cell_scaled_mean_abs_error: float | None = None

    @property
    def gain(self) -> float:
        """Points of accuracy the geography buys over "nothing changed"."""
        return self.null_mean_abs_error - self.mean_abs_error

    @property
    def inside_share(self) -> float | None:
        """Share of the final day's MEASURED change that happened inside counties.

        1.0 means the county mix contributed nothing at all and every point of the
        move happened between voters of the same county. It can exceed 1.0, which
        says the two terms point opposite ways and the county mix moved the wrong
        direction.
        """
        if self.final_mix_only is None or self.final_inside is None:
            return None
        total = self.final_mix_only + self.final_inside
        if abs(total) < 1e-9:
            return None
        return abs(self.final_inside) / abs(total)

    @property
    def mix_gain(self) -> float | None:
        """What the county mix buys in the truth's own unit. See `mix_split`."""
        if self.mix_mean_abs_error is None:
            return None
        return self.null_mean_abs_error - self.mix_mean_abs_error

    @property
    def cell_gain(self) -> float | None:
        """What the county x METHOD cell mix buys over the no-change null."""
        if self.cell_mean_abs_error is None or self.cell_null_mean_abs_error is None:
            return None
        return self.cell_null_mean_abs_error - self.cell_mean_abs_error

    @property
    def cell_county_gain(self) -> float | None:
        """The same for the county term ON THE SAME SUPPORT. The baseline the
        method dimension has to beat, and the only fair one."""
        if self.cell_county_mean_abs_error is None or self.cell_null_mean_abs_error is None:
            return None
        return self.cell_null_mean_abs_error - self.cell_county_mean_abs_error

    @property
    def cell_scaled_gain(self) -> float | None:
        if self.cell_scaled_mean_abs_error is None or self.cell_null_mean_abs_error is None:
            return None
        return self.cell_null_mean_abs_error - self.cell_scaled_mean_abs_error

    @property
    def cycle_constant_gain(self) -> float | None:
        """What a constant learned from ANOTHER cycle transition buys here."""
        if self.cycle_constant_mean_abs_error is None:
            return None
        return self.null_mean_abs_error - self.cycle_constant_mean_abs_error

    @property
    def method_gain(self) -> float | None:
        """What the mail/in-person mix buys, given a gap fitted elsewhere."""
        if (self.method_mean_abs_error is None
                or self.method_null_mean_abs_error is None):
            return None
        return self.method_null_mean_abs_error - self.method_mean_abs_error

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

    ⚠️ AND THERE IS NO MINIMUM DAY COUNT, WHICH WAS MEASURED RATHER THAN ASSUMED.
    Four of the thirteen folds rest on a SINGLE matched day -- OK 2022, OK 2024,
    LA 2024 and OR 2024 -- against Maine's 21 and Pennsylvania's 23, and each of
    them counts once in every mean `format_validation` prints. A floor was swept
    and it is not adopted, for three reasons and one measurement:

      1. THERE IS NO DEFECT IN THE DAY. The maturity gate refuses a day that is a
         sliver of an electorate. All four single-day folds are day-0 pairs --
         a finished early electorate against a finished early electorate, 100% of
         the reference curve on both sides -- so they are the MOST mature days in
         the panel, not the least. A day count is a fact about how much archive a
         state happens to publish, not about the day being compared.
      2. IT IS NOT THE SAME KIND OF RULE AS THE MATURITY GATE. `is_comparable` is
         a predicate on the two days in front of it and is computable live, so
         `build` and `score_panel` can apply the identical test. A day count is a
         predicate on how many OTHER days exist, so on a running cycle a row
         would be refused today and admitted next week -- a retroactive
         publication rule, which is worse than either answer it can give.
      3. IT DECIDES NOTHING. Swept at floors of 1 to 11 days the headline gain is
         -0.03, -0.55, -0.55, -0.55, -0.60, -0.60, -0.45 and -0.59: the county
         term never gets within four tenths of MIN_GAIN at any floor.

    The one number a floor DOES move is the leave-one-cycle-out constant, from
    +2.19 to -3.43, because OK 2022 is one of the panel's only two
    2022-transition folds -- and that is precisely the reason not to adopt it. A
    rule worth adopting only for the answer it produces is not a rule, and the
    constant is refused on its own jackknife instead; see `fit_constant`.
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
            reference_final = reference.final_ballots()
            for dte in sorted(now.counties, reverse=True):
                ref_dte = reference.at(dte, tolerance)
                if ref_dte is None:
                    continue
                # The SAME gate compare_day publishes under. A model scored on a
                # domain wider than the one it publishes on is scoring days its
                # own output never contains; narrower, and it is publishing days
                # it has never been measured on. Both were true here before.
                if is_comparable(now.counties[dte], reference.counties[ref_dte],
                                 reference_final):
                    continue
                here = composition_margin(now.counties[dte], baseline)
                there = composition_margin(reference.counties[ref_dte], baseline)
                if here is None or there is None:
                    continue
                truth_now = party_margin(now.state_rows.get(dte))
                truth_ref = party_margin(reference.state_rows.get(ref_dte))
                if truth_now is None or truth_ref is None:
                    continue
                split = mix_split(now.county_party.get(dte, {}),
                                  reference.county_party.get(ref_dte, {}))
                # The county x METHOD partition, and its own county baseline on
                # the same support. `nested_split`'s first two terms sum to the
                # fine between term, which is what makes the comparison exact
                # rather than two numbers over two populations.
                now_cells = reconciled_cells(now.county_party.get(dte, {}),
                                             now.method_party.get(dte, {}))
                ref_cells = reconciled_cells(
                    reference.county_party.get(ref_dte, {}),
                    reference.method_party.get(ref_dte, {}))
                cells = (
                    None if now_cells is None or ref_cells is None
                    else nested_split(now_cells, ref_cells, county_of_cell)
                )
                mail_now = method_mix(now.state_rows.get(dte))
                mail_ref = method_mix(reference.state_rows.get(ref_dte))
                panel[(cycle, state)].append(ScoredDay(
                    cycle=cycle, state=state, days_to_election=dte,
                    shift=here[0] - there[0],
                    truth=truth_now - truth_ref,
                    ballots=float(here[2]), reference_ballots=float(there[2]),
                    reach=reach_bound(now.counties[dte],
                                      reference.counties[ref_dte], baseline),
                    mix_only=None if split is None else split[0],
                    inside=None if split is None else split[1],
                    cell_only=None if cells is None else cells[0] + cells[1],
                    cell_county=None if cells is None else cells[0],
                    cell_method=None if cells is None else cells[1],
                    cell_inside=None if cells is None else cells[2],
                    method_delta=(
                        None if mail_now is None or mail_ref is None
                        else (mail_now - mail_ref) * 100.0
                    ),
                ))
    for series_days in panel.values():
        series_days.sort(key=lambda d: -d.days_to_election)
    return dict(panel)


def within_reach(days: Sequence[ScoredDay]) -> bool:
    """Could county geography have reached this series' answer at all?

    The counterfactual's version of `estimate.within_reach`, measured the same
    way -- on the final matched day, which is the fully-formed early electorate
    and the least noisy point in the series -- and asking the same question: does
    the answer sit further from the model's inputs than the model is
    structurally able to move? There it is a chosen cap, `MAX_ADJUSTMENT`. Here
    it is `reach_bound`, which is not a choice but the dimension's own arithmetic
    limit given the county mix change that actually happened.

    ⚠️ AND THE ANSWER, ON THE PUBLISHED TREE, IS "NO" ON NINE OF THE THIRTEEN.
    OK 2022 10.62 against a bound of 5.02, FL 4.94 against 3.76, IA 23.12 against
    4.15, KY 13.22 against 8.57, MD 6.28 against 6.17, ME 13.99 against 1.68,
    NC 11.42 against 5.48, OK 2024 18.89 against 7.97, PA 2024 27.64 against 4.57.
    That is the finding in a stronger form than the MAE ever managed: PA 2024 is
    not an outlier this method happened to miss, it is the extreme of a limit that
    binds wherever there was much to see.

    ⚠️ AND WHERE IT DOES NOT BIND, THE MODEL STILL FAILS -- WHICH IS WHY THIS
    PREDICATE IS NOT A COMPETENCE TEST. Colorado and Oregon are inside their bounds
    because their registration moved 1.98 and 0.82 points; nothing happened there.
    PA 2022 is inside its bound with 5.57 points against 6.28, so the county mix
    had room to report it, and the model reported -3.11: wrong by 8.68, pointing
    the opposite way, sign agreement 7%, gain -4.73, the worst fold in the panel.
    LA 2024 is the second such case, added 2026-09-08: 9.79 points against a bound
    of 10.26, on the widest county-margin span in the panel, and the model reported
    +0.50 -- the wrong sign again. A dimension being ABLE to say something is not
    the same as it saying it, and there are two demonstrations of that now.

    ⚠️ SO THIS PREDICATE IS REPORTED AND IS NEVER A FILTER, WHICH IS WHERE IT
    PARTS COMPANY WITH `estimate.within_reach`. There, an out-of-reach series is
    shown and not averaged, because MAX_ADJUSTMENT is a chosen cap and averaging
    such a series prices the cap instead of the method. Here the limit is not a
    parameter but the dimension's own arithmetic, and `|truth| > reach` is a
    condition on the TRUTH: dropping those folds selects the ones whose
    composition barely moved. It was tried, on 2026-09-07, and it reported this
    model's accuracy off Colorado alone -- 2.29 against a null of 2.14 -- while
    discarding Pennsylvania's 27.64-point move. See `format_validation`.
    """
    if not days:
        return False
    last = min(days, key=lambda d: d.days_to_election)
    return last.reach is not None and abs(last.truth) <= last.reach


def fit_scale(series: Iterable[Sequence[ScoredDay]]) -> float | None:
    """The multiplier k minimising Σ (truth - k·shift)², series-weighted.

    Weighted least squares through the origin: each state-cycle counts once, not
    once per day.

    This exists to give the method its best possible case. If the only thing
    wrong with county geography were that its unit is presidential points and the
    truth's is registration points, a fitted k would absorb the difference.
    Returns None rather than a number when nothing is fittable.

    ⚠️ THERE IS NO INTERCEPT, AND THE REASON WRITTEN HERE USED TO BE WRONG. It
    said an intercept was "a constant national shift, and the null already owns
    that". The null is zero. It owns no such thing, and the difference is not
    small: fitted leave-one-state-out on the seven-fold panel, an intercept ALONE
    -- "every state's early electorate moved by whatever the other states'
    registration moved" -- scores 6.75 against the null's 10.24, a gain of
    +3.49 pp, three and a half times the bar this feature is held to (+3.42 on
    the thirteen-fold panel). Adding `shift` on top of it buys a further +0.29.

    The honest reason to refuse the intercept was a different and better one: the
    panel contained exactly ONE cycle transition. All seven folds were
    2024-vs-2022, so leave-one-STATE-out never held out the transition, and the
    constant was fitted on the very thing it would be tested on. What it had
    learned was that 2022 -> 2024 was a one-off normalisation -- Republicans
    returning to a mail channel they had boycotted -- which is a fact about that
    transition, not a law about early electorates, and applying it to
    2024 -> 2026 would assert the same move happens twice.

    ⚠️ THAT WAS AN ARGUMENT UNTIL 2026-09-07 AND IT IS NOW A MEASUREMENT, BECAUSE
    PENNSYLVANIA'S 2020 CURVE GAVE THE PANEL A SECOND TRANSITION. Held out on a
    cycle rather than a state, the +3.49 became -3.35 on the eight-fold panel --
    and +2.19 on the thirteen-fold one, once Oklahoma's 2020 snapshot made the
    2022 transition two folds wide and flipped the fitted constant's sign. The
    refusal now rests on `constant_jackknife` (-2.96 without Oklahoma, +4.47
    without Pennsylvania) rather than on the holdout itself. See `fit_constant`,
    which exists to keep that measurement in the code.

    Colorado made the fragility visible before that, the moment it arrived: the
    constant scored +5.31 on the six folds that preceded it and costs -7.28 points
    on Colorado alone, because Colorado is a state whose composition did not move
    and the constant insists that it did. Oregon is the same case on the
    thirteen-fold panel and costs -10.98.

    ⚠️ AND ON THE THIRTEEN-FOLD PANEL THIS FUNCTION'S OWN NUMBER MOVED, IN A WAY
    WORTH RECORDING RATHER THAN BURYING. Every leave-one-state-out multiplier is
    POSITIVE for the first time since the maturity gate -- the sign half of the
    argument has now been wrong in both directions three times and is not evidence
    -- and THREE folds (IA +1.07, ME +1.31, NC +1.64) clear MIN_GAIN individually
    under a scale fitted elsewhere, where none did before. MIN_GAIN is a bar for a
    PANEL, so that is not a rescue, and the panel mean is -2.15. But it is -2.15
    only with Pennsylvania in: refit with one state dropped it stays between -2.31
    and -2.45 everywhere else and becomes +1.79 without PA. What has never flipped
    is the magnitude: 0.099 without Oklahoma against 4.595 without Pennsylvania, a
    forty-sixfold spread whose two ends are two states pulling opposite ways.

    Record it, do not ship it, and do not let it be mistaken for the county term
    working. Every predictor swept on this panel that beats the no-change null
    turns out to be a noisier estimate of that same constant, and none of them
    beats the constant alone; the county term's entire incremental value ON TOP
    of the constant is +0.29 pp, against a bar of +1.00. See
    docs/counterfactual.md.
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


def fit_cell_scale(series: Iterable[Sequence[ScoredDay]]) -> float | None:
    """`fit_scale` for the county x METHOD cell term, series-weighted, no intercept.

    The cell term needs no conversion -- it is already in the truth's own
    registration unit -- so this is not a unit fix; it is the same "give the
    dimension its best case" move `fit_scale` makes, and it is refitted in every
    fold because a jackknife over a frozen parameter is not a jackknife.
    """
    numerator = denominator = 0.0
    for one in series:
        days = [d for d in one if d.cell_only is not None]
        if not days:
            continue
        weight = 1.0 / len(days)
        for day in days:
            numerator += weight * day.cell_only * day.truth
            denominator += weight * day.cell_only * day.cell_only
    return numerator / denominator if denominator > 0 else None


def fit_method_gap(series: Iterable[Sequence[ScoredDay]]) -> float | None:
    """The channel gap `g` minimising Σ (truth - g·method_delta)², series-weighted.

    `fit_scale` for the mail/in-person dimension. The predictor is the signed
    change in mail's share and the fitted parameter is the mail-minus-in-person
    REGISTRATION margin gap the change would have to be worth -- so unlike
    `fit_scale` there is no unit to fix here, and unlike `fit_cell_scale` there
    is no crosstab needed: `method_delta` is defined on every fold in the panel.
    It is the only within-county specification with that property, which is why
    it is scored rather than merely bounded.

    ⚠️ AND ITS NUMBER MOVED ON 2026-09-08, WHICH IS WHY IT IS IN THE CODE NOW
    RATHER THAN IN A SWEEP IN THE DOCUMENT. On the eight-fold panel this scored
    -1.52 and `required_span` refused it before any arithmetic: only Florida's
    28-point requirement was inside the 18-to-42-point channel gap this repo can
    observe. Oklahoma's 2020 curve made OK 2022 a fold whose mail share fell from
    63.2% to 35.1% -- the pandemic mail surge unwinding, on two day-0 snapshots,
    so it is NOT the ±3-day calendar artefact that produces Kentucky's -15.42 --
    and its required span is 38, inside the observable range. Two of thirteen
    folds are now reachable by this dimension where one of nine was.

    Scored leave-one-state-out on the thirteen-fold panel it buys +0.42, up from
    -1.52 and still under MIN_GAIN; pinned at an exogenous 28-point gap -- the
    midpoint of what the crosstab actually measures -- it buys +1.29, which drops
    to +0.60 the moment Oklahoma is dropped. Five folds contribute exactly 0.00
    because their mail share is frozen (PA has no in-person channel, IA and OR
    report one mail figure, MD's mail file is a corrupt zip), and the fitted gap
    still cannot agree with itself: leave-one-state-out it runs 20.2 to 56.6, and
    each fold's own best gap runs 0.1 (KY) to 572.0 (NC).

    Record it, do not ship it. See docs/counterfactual.md.

    The number it returns is a SPAN IN REGISTRATION POINTS -- the same unit
    `required_span` names and the same unit the 18-to-42-point channel gaps this
    repo can observe are in -- so `prediction = g * method_delta / 100`.
    """
    numerator = denominator = 0.0
    for one in series:
        days = [d for d in one if d.method_delta is not None]
        if not days:
            continue
        weight = 1.0 / len(days)
        for day in days:
            share = day.method_delta / 100.0
            numerator += weight * share * day.truth
            denominator += weight * share * share
    return numerator / denominator if denominator > 0 else None


def fit_constant(series: Iterable[Sequence[ScoredDay]]) -> float | None:
    """The intercept `fit_scale` refuses: the series-weighted mean measured change.

    "This state's early electorate moved by whatever the OTHER states' moved."
    Never used in the published path and never will be. It exists so the refusal
    can be measured rather than argued, the same job `fit_scale` does.

    ⚠️ AND IT IS NOW MEASURED, WHICH IT COULD NOT BE UNTIL 2026-09-07. Fitted
    leave-one-STATE-out this constant scores +3.49 pp over the no-change null,
    three and a half times MIN_GAIN and better than every predictor swept on this
    panel -- and the objection recorded against it was that the panel held exactly
    one cycle transition, so leave-one-state-out never held the transition out and
    the constant was fitted on the very thing it would be tested on. That was an
    argument. Pennsylvania's 2020 backfill turned it into a test, and on the
    eight-fold panel the answer was -3.35: the two transitions pointed opposite
    ways, so a constant learned from one was worse than useless on the other.

    ⚠️ ON 2026-09-08 OKLAHOMA'S 2020 CURVE GAVE THE PANEL A SECOND
    2022-TRANSITION FOLD AND THE HELD-OUT NUMBER TURNED POSITIVE: -3.35 becomes
    **+2.19**, over MIN_GAIN. IT DOES NOT SURVIVE, AND `constant_jackknife` IS
    WHERE THAT IS MEASURED RATHER THAN ARGUED.

    Two facts decide it, and the second is the one that settles it.

    1. THE CONSTANT THE ELEVEN 2024 FOLDS ARE SCORED WITH IS THE MEAN OF TWO
       NUMBERS, AND ONE OF THEM IS A SINGLE MATCHED DAY. The panel holds eleven
       2024-vs-2022 folds and TWO 2022-vs-2020 ones, so "leave one cycle out"
       means predicting eleven folds from two and two from eleven. The two
       disagree violently -- OK 2022 gains +10.08 and PA 2022 loses -11.08, 21
       points apart -- and their mean, -4.11, is what every 2024 fold is scored
       against. Without Oklahoma that constant is PA 2022's +2.40 alone, it has
       the wrong sign for ten of the eleven, and the panel returns to -2.96.
       Drop Pennsylvania instead and it goes to +4.47. One fold either way, an
       eight-point swing: `constant_jackknife` reports both.

    2. NINE OF THE THIRTEEN GAINS ARE EXACTLY ±|c|, WHICH MEANS THE SCORE IS A
       SIGN COUNT AND NOT A FIT. Mean absolute error rewards ANY step taken in
       the right direction, so a fold whose truth lies beyond the constant in the
       same direction banks exactly |c| whatever its size -- FL, IA, LA, MD, ME,
       NC, OK 2024 and PA 2024 all score precisely +4.107, and OR, whose
       registration moved the other way, precisely -4.107. Only CO (+0.18) and KY
       (+0.51) straddle the constant and therefore say anything about its
       magnitude. "Both transitions happened to average negative" is one
       observation with n = 2 transitions, not evidence that a constant works.

    What the constant learns from 2022 -> 2024 is that Republicans came back to a
    mail channel they had boycotted; what 2020 -> 2022 says is that they left it.
    A constant is a claim that the same move happens every cycle. This panel can
    now check that claim twice, and the two checks are 21 points apart.

    Record it, do not ship it, and do not let it be mistaken for the county term
    working. See `fit_scale`, `constant_jackknife` and docs/counterfactual.md.
    """
    numerator = denominator = 0.0
    for one in series:
        if not one:
            continue
        weight = 1.0 / len(one)
        for day in one:
            numerator += weight * day.truth
            denominator += weight
    return numerator / denominator if denominator > 0 else None


def constant_gain(
    results: Sequence[Validation],
    *,
    drop_state: str | None = None,
    by_transition: bool = False,
) -> float | None:
    """Refit the leave-one-CYCLE-out constant over a panel and score it.

    `validate` already reports this per fold; this recomputes it from scratch so
    the panel can be JACKKNIFED, which is what `MIN_GAIN` being cleared obliges.
    Everything it needs is `Validation.truths`, so it works on a hand-built panel
    as well as on the published tree.

    `by_transition=True` weights each cycle transition equally instead of each
    fold. On the thirteen-fold panel eleven folds are the 2024 transition and two
    are the 2022 one, so a mean over folds is eleven parts one transition to two
    parts the other; the transition-weighted number is the one that asks "does a
    constant carry ACROSS transitions", which is the question. It is +2.19 by
    fold and +1.09 by transition, and both collapse under `constant_jackknife`.
    """
    kept = [r for r in results
            if r.truths and (drop_state is None or r.state != drop_state)]
    means = {(r.cycle, r.state): sum(r.truths) / len(r.truths) for r in kept}
    scored: list[tuple[int, float]] = []
    for r in kept:
        others = [m for key, m in means.items() if key[0] != r.cycle]
        if not others:
            continue
        constant = sum(others) / len(others)
        n = len(r.truths)
        null = sum(abs(t) for t in r.truths) / n
        mae = sum(abs(constant - t) for t in r.truths) / n
        scored.append((r.cycle, null - mae))
    if not scored:
        return None
    if not by_transition:
        return sum(g for _, g in scored) / len(scored)
    per: dict[int, list[float]] = defaultdict(list)
    for cycle, gain in scored:
        per[cycle].append(gain)
    return sum(sum(v) / len(v) for v in per.values()) / len(per)


def constant_jackknife(
    results: Sequence[Validation], *, by_transition: bool = False
) -> list[tuple[str, float]]:
    """`constant_gain` with one STATE dropped, for every state in the panel.

    ⚠️ THE MEASUREMENT THAT REFUSES THE CONSTANT ON THE THIRTEEN-FOLD PANEL, and
    the reason `fit_constant`'s docstring is as long as it is. Held out on a
    cycle the constant scores +2.19, over MIN_GAIN and the strongest thing this
    panel has ever produced. Jackknifed it runs from -2.96 without Oklahoma to
    +4.47 without Pennsylvania -- an eight-point range on a panel whose whole
    second transition is two folds, one of which is a single matched day.

    A state whose removal leaves one cycle transition is reported as "not
    computable" by omission: `constant_gain` returns None there, because the
    holdout no longer exists. That is not a missing number, it is the original
    objection reappearing.
    """
    states = sorted({r.state for r in results if r.truths})
    out: list[tuple[str, float]] = []
    for state in states:
        gain = constant_gain(results, drop_state=state, by_transition=by_transition)
        if gain is not None:
            out.append((state, gain))
    return sorted(out, key=lambda pair: pair[1])


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
        # LEAVE ONE CYCLE OUT: the constant learned from every OTHER transition.
        other_cycles = [v for k, v in panel.items() if k[0] != cycle]
        constant = fit_constant(other_cycles) if other_cycles else None
        shifts = [d.shift for d in days]
        truths = [d.truth for d in days]
        errors = [abs(s - t) for s, t in zip(shifts, truths)]
        nulls = [abs(t) for t in truths]
        n = len(days)
        last = min(days, key=lambda d: d.days_to_election)
        # `mix_split`, the county dimension with the unit gap removed. Only over
        # the days that have it; a state that reports no county party split
        # leaves every one of these None rather than a zero.
        splits = [d for d in days if d.mix_only is not None]
        # The county x METHOD panel, and the scale fitted WITHOUT this state.
        cells = [d for d in days if d.cell_only is not None]
        cell_scale = fit_cell_scale(others) if others and cells else None
        # The mail/in-person mix, with its channel gap fitted WITHOUT this state.
        # Unlike the crosstab this is defined in every fold, so it is the one
        # within-county dimension that can be scored over the whole panel.
        mixes = [d for d in days if d.method_delta is not None]
        method_gap = fit_method_gap(others) if others and mixes else None
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
            reach=last.reach,
            in_range=within_reach(days),
            final_mix_only=last.mix_only,
            final_inside=last.inside,
            mean_abs_mix_only=(
                None if not splits
                else sum(abs(d.mix_only) for d in splits) / len(splits)
            ),
            mix_mean_abs_error=(
                None if not splits
                else sum(abs(d.mix_only - d.truth) for d in splits) / len(splits)
            ),
            cycle_constant=constant,
            cycle_constant_mean_abs_error=(
                None if constant is None
                else sum(abs(constant - d.truth) for d in days) / n
            ),
            truths=tuple(truths),
            method_gap=method_gap,
            method_days=len(mixes),
            method_null_mean_abs_error=(
                None if not mixes
                else sum(abs(d.truth) for d in mixes) / len(mixes)
            ),
            method_mean_abs_error=(
                None if method_gap is None or not mixes
                else sum(abs(method_gap * d.method_delta / 100.0 - d.truth)
                         for d in mixes) / len(mixes)
            ),
            final_cell_only=last.cell_only,
            final_cell_county=last.cell_county,
            final_cell_method=last.cell_method,
            final_cell_inside=last.cell_inside,
            cell_days=len(cells),
            cell_null_mean_abs_error=(
                None if not cells
                else sum(abs(d.truth) for d in cells) / len(cells)
            ),
            mean_abs_cell_only=(
                None if not cells
                else sum(abs(d.cell_only) for d in cells) / len(cells)
            ),
            cell_mean_abs_error=(
                None if not cells
                else sum(abs(d.cell_only - d.truth) for d in cells) / len(cells)
            ),
            cell_county_mean_abs_error=(
                None if not cells
                else sum(abs(d.cell_county - d.truth) for d in cells) / len(cells)
            ),
            cell_scale=cell_scale,
            cell_scaled_mean_abs_error=(
                None if cell_scale is None or not cells
                else sum(abs(cell_scale * d.cell_only - d.truth) for d in cells)
                / len(cells)
            ),
        ))
    return results


def _fold(r: Validation) -> str:
    """A series' name. The cycle is part of it: since Pennsylvania's 2020 curve
    landed the panel holds TWO PA folds pointing opposite ways, and a line that
    said "PA" twice would read as a duplicate rather than as the point."""
    return f"{r.state}{r.cycle}"


def format_validation(results: Sequence[Validation]) -> Iterator[str]:
    yield (f"{'cycle':>5s} {'st':3s} {'days':>4s} {'shift':>7s} {'truth':>7s} "
           f"{'final':>7s} {'MAE':>6s} {'null':>6s} {'gain':>6s} "
           f"{'|shift|':>7s} {'|truth|':>7s} {'reach':>6s} {'r':>6s} {'sign':>5s} "
           f"{'k':>6s} {'k-MAE':>6s} {'k-gain':>7s}")
    for r in sorted(results, key=lambda r: (r.state, r.cycle)):
        yield (f"{r.cycle:5d} {r.state:3s} {r.days:4d} {r.final_shift:+7.2f} "
               f"{r.final_truth:+7.2f} {r.final_error:+7.2f} "
               f"{r.mean_abs_error:6.2f} {r.null_mean_abs_error:6.2f} "
               f"{r.gain:+6.2f} {r.mean_abs_shift:7.2f} {r.mean_abs_truth:7.2f} "
               + (f"{r.reach:6.2f}" if r.reach is not None else "   n/a")
               + " "
               + (f"{r.correlation:+6.2f}" if r.correlation is not None else "   n/a")
               + f" {r.sign_agreement:5.0%} "
               + (f"{r.fitted_scale:+6.2f}" if r.fitted_scale is not None else "   n/a")
               + (f" {r.scaled_mean_abs_error:6.2f}"
                  if r.scaled_mean_abs_error is not None else "    n/a")
               + (f" {r.scaled_gain:+7.2f}" if r.scaled_gain is not None else "     n/a")
               + ("" if r.held_out else "  (in sample: no other state to fit on)")
               + ("" if r.in_range else "  (beyond what county geography can reach)"))
    if not results:
        yield ("(no state-cycle in output/ has BOTH a county early series in two "
               "consecutive cycles AND a reported party split to score against)")
        return

    # ⚠️ REACH IS REPORTED AND IS NEVER A FILTER, AND THAT IS THE ONE PLACE THIS
    # MODEL MUST NOT COPY `estimate.py`.
    #
    # There, a series whose answer sits beyond MAX_ADJUSTMENT is shown and not
    # averaged, because MAX_ADJUSTMENT is a CHOSEN CAP and averaging such a
    # series prices the cap rather than the method. The same rule was written
    # here for consistency and then measured, and it is wrong here for two
    # reasons the measurement made obvious within the hour:
    #
    #   1. `in_range` is a condition ON THE TRUTH. Dropping the folds where
    #      |truth| exceeds the bound selects the folds where the compositional
    #      change was SMALL -- selection on the outcome, and it flatters nothing
    #      so much as a method that cannot move.
    #   2. It was tried. On the tree of 2026-09-07, Colorado arrived as a seventh
    #      fold and is the only one inside its bound, so the exclusion rule
    #      reported this model's accuracy off Colorado alone -- 2.29 against a
    #      null of 2.14 -- and threw away Pennsylvania's 27.64-point move. An
    #      "improvement" that discards the hardest six sevenths of the evidence.
    #
    # There is no cap to avoid pricing here. `reach_bound` is not a parameter of
    # the model, it is the dimension's own arithmetic, so a series beyond it is
    # not a series this method was measured unfairly on -- it is the finding.
    beyond = [r for r in results if not r.in_range]
    n = len(results)
    mae = sum(r.mean_abs_error for r in results) / n
    null = sum(r.null_mean_abs_error for r in results) / n
    gain = sum(r.gain for r in results) / n
    scaled = [r for r in results if r.scaled_mean_abs_error is not None]
    yield ""
    if beyond:
        yield (f"REACH: {len(beyond)} of {len(results)} series moved further than "
               "county geography could have reported under ANY reweighting of "
               "their counties ("
               + ", ".join(f"{_fold(r)} {abs(r.final_truth):.2f} vs {r.reach:.2f}"
                           for r in beyond)
               + "). reach = TV(county mix) x (widest county margin - narrowest), "
                 "a hard bound needing no ground truth. Every one is still "
                 "AVERAGED below -- unlike estimate.py, which drops its "
                 "out-of-reach series: there the limit is a chosen cap, here it "
                 "is the dimension itself, and dropping on it would select the "
                 "folds whose composition barely moved.")
    yield (f"mean MAE = {mae:.2f} pp   null (composition unchanged) = {null:.2f} pp   "
           f"gain = {gain:+.2f} pp")
    yield (f"county geography moves {sum(r.mean_abs_shift for r in results) / n:.2f} pp "
           f"while the composition it stands in for moves "
           f"{sum(r.mean_abs_truth for r in results) / n:.2f} pp")
    # ⚠️ THE SERIES IS THE UNIT AND SOME SERIES ARE ONE DAY LONG. Reported so a
    # reader can see it, and NOT a filter: see `score_panel` for the sweep that
    # says a minimum day count changes no verdict here and would change one
    # number, which is the reason not to have one.
    thin = [r for r in results if r.days == 1]
    if thin:
        pooled = sum(r.mean_abs_error * r.days for r in results) / sum(
            r.days for r in results)
        pooled_null = sum(r.null_mean_abs_error * r.days for r in results) / sum(
            r.days for r in results)
        yield (f"{len(thin)} of {len(results)} folds rest on a SINGLE matched day "
               + "(" + ", ".join(_fold(r) for r in thin) + ") against a longest of "
               + f"{max(r.days for r in results)}; every mean above weights them "
               + f"equally. Pooled by DAY instead: gain "
               + f"{pooled_null - pooled:+.2f} pp over "
               + f"{sum(r.days for r in results)} days")

    # ⚠️ INSIDE COUNTIES. `mix_split` splits the MEASURED change into the part the
    # county mix moved and the part that happened between voters of the same
    # county, exactly. The first term is `shift`'s own construction valued in the
    # truth's own registration unit, so it is the county dimension WITH THE UNIT
    # GAP REMOVED -- the one excuse the reach bound could not close. It is
    # reported here and it is NEVER A FILTER: `gain` above is the published
    # method's and is a mean over every scored series.
    split = [r for r in results if r.inside_share is not None]
    if split:
        yield ("inside counties: "
               + ", ".join(f"{_fold(r)} {r.inside_share:.0%}" for r in split)
               + " of the measured change happened between voters of the SAME "
                 "county, where no county-level model can see it")
        mixed = [r for r in results if r.mix_gain is not None]
        if mixed:
            mix_gain = sum(r.mix_gain for r in mixed) / len(mixed)
            yield (f"the same county mix valued in the truth's OWN unit "
                   f"(no unit gap) moves "
                   f"{sum(r.mean_abs_mix_only for r in mixed) / len(mixed):.2f} pp "
                   f"and buys {mix_gain:+.2f} pp over the null")
    # ⚠️ THE COUNTY x METHOD CROSSTAB. `mix_split` says the county dimension is
    # empty and `nested_split` says a finer geography does not fill it. This is
    # the partition that cuts ACROSS counties instead of under them, and it is
    # the one docs/counterfactual.md said did not exist. It does; `MethodDay` is
    # where it lives now. It is REPORTED AND NEVER A FILTER, exactly as
    # `mix_gain` is -- `gain` above is still the published method's, over every
    # scored series.
    #
    # The number to read is `vs county` and not `gain`: the fine partition drops
    # cells only one side reported, so `cell_county` is the county term
    # recomputed over that same support and is the only county figure the fine
    # one may be set against.
    celled = [r for r in results if r.cell_gain is not None]
    if celled:
        yield ""
        yield ("county x METHOD (the party-by-method crosstab, "
               + f"{len(celled)} of {len(results)} folds have one): "
               + ", ".join(
                   f"{_fold(r)} cell {r.cell_gain:+.2f} vs county "
                   f"{r.cell_county_gain:+.2f}" for r in celled)
               + f"   mean {sum(r.cell_gain for r in celled) / len(celled):+.2f} pp "
               + f"against the same-support county term's "
               + f"{sum(r.cell_county_gain for r in celled) / len(celled):+.2f} pp")
        yield ("   final day, where the change went: "
               + ", ".join(
                   f"{_fold(r)} counties {r.final_cell_county:+.2f} / methods "
                   f"{r.final_cell_method:+.2f} / inside cells "
                   f"{r.final_cell_inside:+.2f} of {r.final_truth:+.2f}"
                   for r in celled if r.final_cell_only is not None))
        # JACKKNIFE. Nothing in the cell term is fitted, so this is a mean over
        # folds with one more fold dropped -- and where something IS fitted (the
        # scale below) it is refitted inside every fold already.
        if len(celled) > 1:
            yield ("   jackknife (drop one more fold): "
                   + ", ".join(
                       f"without {_fold(dropped)} "
                       f"{sum(r.cell_gain for r in celled if r is not dropped) / (len(celled) - 1):+.2f}"
                       for dropped in sorted(celled, key=lambda r: -r.cell_gain)))
        cell_scaled = [r for r in celled if r.cell_scaled_gain is not None]
        if cell_scaled:
            yield ("   with a scale fitted leave-one-state-out: "
                   + ", ".join(f"{_fold(r)} k={r.cell_scale:+.2f} "
                               f"{r.cell_scaled_gain:+.2f}" for r in cell_scaled)
                   + f"   mean "
                   + f"{sum(r.cell_scaled_gain for r in cell_scaled) / len(cell_scaled):+.2f} pp")
        missing = [r for r in results if r.cell_gain is None]
        if missing:
            yield ("   NO CROSSTAB, so no cell term at all: "
                   + ", ".join(f"{_fold(r)} (|truth| {abs(r.final_truth):.2f})"
                               for r in sorted(missing,
                                               key=lambda r: -abs(r.final_truth)))
                   + " -- a term is only worth what it is worth WHERE IT EXISTS")

    if scaled:
        k_gain = sum(r.scaled_gain for r in scaled) / len(scaled)
        yield (f"leave-one-state-out fitted scale: "
               + ", ".join(f"{_fold(r)} k={r.fitted_scale:+.2f}" for r in scaled)
               + f"   mean gain {k_gain:+.2f} pp")

    # ⚠️ LEAVE ONE CYCLE OUT. The one test a fitted constant has never been able to
    # take on this panel, and the reason it was never allowed to ship. It is blank
    # while the panel holds a single cycle transition -- which is itself the
    # finding, so it says so rather than printing nothing.
    held = [r for r in results if r.cycle_constant_gain is not None]
    if held:
        yield ("leave-one-CYCLE-out constant: "
               + ", ".join(f"{_fold(r)} c={r.cycle_constant:+.2f} "
                           f"gain {r.cycle_constant_gain:+.2f}" for r in held)
               + f"   mean gain {sum(r.cycle_constant_gain for r in held) / len(held):+.2f}"
                 " pp -- a constant fitted leave-one-STATE-out scores +3.42 on this"
                 " panel, and this is what it is worth once a TRANSITION is held"
                 " out. OVER MIN_GAIN SINCE 2026-09-08, SO READ THE JACKKNIFE")
        # ⚠️ AND WHEN THAT NUMBER CLEARS MIN_GAIN IT GETS JACKKNIFED, which is the
        # rule this repo applies to every candidate: a +1.59 became +0.27 without
        # one state. The constant's holdout is two folds wide on one side and
        # eleven on the other, so the per-transition mean and the drop-one-state
        # refit are both printed rather than left to the reader.
        per_cycle: dict[int, list[float]] = defaultdict(list)
        for r in held:
            per_cycle[r.cycle].append(r.cycle_constant_gain)
        yield ("   by transition: "
               + ", ".join(
                   f"{cycle} n={len(v)} mean {sum(v) / len(v):+.2f}"
                   + (f" spread {max(v) - min(v):.2f}" if len(v) > 1 else "")
                   for cycle, v in sorted(per_cycle.items()))
               + (f"   transition-weighted mean "
                  f"{constant_gain(results, by_transition=True):+.2f} pp"
                  if constant_gain(results, by_transition=True) is not None else ""))
        jack = constant_jackknife(results)
        if jack:
            yield ("   jackknife (drop one state, constant refitted): "
                   + ", ".join(f"without {s} {g:+.2f}" for s, g in jack))
    else:
        yield ("leave-one-CYCLE-out constant: not computable -- every fold in this "
               "panel is the same cycle transition, so a fitted constant would be "
               "scored on the transition it was learned from")

    # ⚠️ THE MAIL/IN-PERSON MIX, scored rather than bounded. `required_span` used
    # to refuse this dimension outright -- only Florida's requirement was inside
    # a channel gap this repo has ever observed -- and OK 2022 is inside it too,
    # so the refusal has to be a measurement now. REPORTED, NEVER A FILTER.
    methoded = [r for r in results if r.method_gain is not None]
    if methoded:
        moving = [r for r in methoded if abs(r.method_gain) > 1e-9]
        yield ("method mix x a channel gap fitted leave-one-state-out "
               f"({len(methoded)} of {len(results)} folds, {len(moving)} whose mix "
               "moves at all): "
               + ", ".join(f"{_fold(r)} gap={r.method_gap:+.1f}pp "
                           f"{r.method_gain:+.2f}" for r in methoded)
               + f"   mean gain "
               + f"{sum(r.method_gain for r in methoded) / len(methoded):+.2f} pp"
               + (f", {sum(r.method_gain for r in moving) / len(moving):+.2f} pp "
                  "over the folds whose mix moves" if moving else ""))
    yield ""
    yield (f"VERDICT: {'SHIPS' if gain >= MIN_GAIN else 'NOTHING SHIPS'} "
           f"(bar is {MIN_GAIN:+.2f} pp of gain over the no-change null; "
           f"measured {gain:+.2f})")
    yield ("columns: shift/truth/final are the LAST matched day; MAE/null over "
           "days with >=25% of each series' early vote in; truth is the change in "
           "the state's OWN reported party REGISTRATION of the same ballots -- a "
           "different unit, which is why k is fitted and reported; sign = share of "
           "days the modelled shift agrees in direction with the measured one; "
           "reach = the largest |shift| county geography could have reported on "
           "the last matched day, whatever the counties had done.")


def cmd_counterfactual(args) -> int:
    """`python -m ev counterfactual`. Never part of the scheduled ingest."""
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

    # A filtered run knows only part of the table and may only merge into it.
    info = write(out_dir, rows, rebuild=not args.state and not args.cycle)
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
