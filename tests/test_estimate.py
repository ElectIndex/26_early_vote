"""The party estimate: its arithmetic, its honesty columns, and its error.

Three of these tests are worth more than the rest put together:

* `test_write_never_touches_the_reported_party_columns` — the estimate is a
  model, and the moment a model number lands in `party_dem` the published data
  stops meaning "the state reported this". That guarantee is the whole pipeline.

* `test_ground_truth_north_carolina_2024` — pins the MEASURED error against a
  state that does report party. That number is the argument for or against
  shipping this feature, so it belongs in a test that fails when it moves.

* `test_the_mail_term_is_scored_leave_one_state_out` — the mail correction has
  two fitted constants, and a fitted constant scored on its own training data is
  how `docs/regression.md` nearly shipped seven specifications that were worse
  than knowing nothing. This asserts the protocol itself, not just the answer.
"""

from __future__ import annotations

import csv
import hashlib
import shutil
from datetime import date
from pathlib import Path

import pytest

from ev import estimate as est
from ev.adapters import _fips
from ev.registry import TIER1
from ev.schema import STATE_DAILY_COLUMNS

FIXTURES = Path(__file__).parent / "fixtures" / "estimate"
NC_BASELINE = FIXTURES / "county_results_2024_nc.csv"

# Three real North Carolina counties, with their real 2024 presidential returns,
# chosen to span the state: Mecklenburg (Charlotte, heavily D), Alamance
# (suburban R) and Alleghany (small, heavily R).
MECKLENBURG, ALAMANCE, ALLEGHANY = "37119", "37001", "37005"


@pytest.fixture(scope="module")
def baseline() -> est.Baseline:
    return est.load_baseline(NC_BASELINE)


@pytest.fixture(scope="module")
def full_baseline() -> est.Baseline:
    return est.load_baseline()


def _out_dir(tmp_path: Path) -> Path:
    """An output/ tree carrying the real NC 2024 slice."""
    out = tmp_path / "output"
    (out / "counties").mkdir(parents=True)
    shutil.copy(FIXTURES / "nc_state_daily_2024.csv", out / "ev_state_daily.csv")
    shutil.copy(FIXTURES / "nc_counties_2024.csv", out / "counties" / "nc.csv")
    return out


# --------------------------------------------------------------------------
# The weighting arithmetic
# --------------------------------------------------------------------------
def test_weighted_share_is_the_ballot_weighted_mean_of_county_lean(baseline):
    """Σ b·d / Σ b, computed by hand from the fixture's own numbers."""
    ballots = {MECKLENBURG: 300, ALAMANCE: 100}
    share, used, total = est.weighted_share(ballots, baseline)

    meck, alam = baseline.get(MECKLENBURG), baseline.get(ALAMANCE)
    expected = (300 * meck.dem_share + 100 * alam.dem_share) / 400
    assert share == pytest.approx(expected)
    assert (used, total) == (2, 400)
    # Sanity on the direction: a Mecklenburg-heavy mix must land D of Alamance.
    assert alam.dem_share < share < meck.dem_share


def test_weights_are_ballots_not_counties(baseline):
    """One big D county outweighs three small R ones; that is the whole method."""
    lopsided = est.weighted_share({MECKLENBURG: 100_000, ALLEGHANY: 100}, baseline)[0]
    even = est.weighted_share({MECKLENBURG: 100, ALLEGHANY: 100}, baseline)[0]
    assert lopsided > even


def test_a_county_with_no_baseline_is_dropped_not_guessed(baseline):
    """An unmatched FIPS contributes nothing and is not counted as used."""
    share, used, total = est.weighted_share(
        {MECKLENBURG: 500, "99999": 500_000}, baseline
    )
    assert (used, total) == (1, 500)
    assert share == pytest.approx(baseline.get(MECKLENBURG).dem_share)


def test_a_reported_zero_county_is_used_and_contributes_no_weight(baseline):
    """0 ballots is data -- the county reported, it simply has nothing yet."""
    share, used, total = est.weighted_share({MECKLENBURG: 100, ALAMANCE: 0}, baseline)
    assert (used, total) == (2, 100)
    assert share == pytest.approx(baseline.get(MECKLENBURG).dem_share)


def test_blank_ballots_total_is_skipped_not_read_as_zero(tmp_path, baseline):
    """THE BLANK RULE, on the read side: a blank cell is absence, not a count."""
    path = tmp_path / "counties"
    path.mkdir()
    with (path / "nc.csv").open("w", newline="") as fh:
        fh.write("cycle,state,county_fips,date,ballots_total\n")
        fh.write(f"2026,NC,{MECKLENBURG},2026-10-20,500\n")
        fh.write(f"2026,NC,{ALAMANCE},2026-10-20,\n")
    days = est.read_county_ballots(tmp_path, "NC")
    assert days[(2026, "2026-10-20")] == {MECKLENBURG: 500}


def test_margin_is_the_two_party_dem_lead(baseline):
    row = est.estimate_day(2026, "NC", date(2026, 10, 20),
                           {MECKLENBURG: 300, ALAMANCE: 100}, baseline)
    out = row.to_dict()
    assert float(out["est_dem_share"]) + float(out["est_rep_share"]) == pytest.approx(1.0)
    assert float(out["est_margin"]) == pytest.approx(
        2 * float(out["est_dem_share"]) - 1, abs=1e-4
    )


# --------------------------------------------------------------------------
# The mail-selection term
# --------------------------------------------------------------------------
def test_method_split_believes_mail_and_treats_the_rest_as_in_person():
    """North Carolina's real 2024 numbers: 297,034 mail, `inperson = 0`, and a
    headline of 4,520,768. Reading `inperson` first would make the state 100%
    mail and hand it the largest correction in the table instead of the
    smallest."""
    mail, inperson = est.method_split(4_520_768, 297_034, 0)
    assert (mail, inperson) == (297_034, 4_223_734)


def test_method_split_derives_mail_from_the_headline_when_only_in_person_is_given():
    """Maryland reports its in-person centres and nothing else, and its headline
    equals them exactly -- so its tracked early vote is zero mail."""
    assert est.method_split(994_663, None, 994_663) == (0.0, 994_663.0)
    assert est.method_split(1000, None, 400) == (600.0, 400.0)


def test_method_split_is_none_when_the_state_reports_neither():
    """THE BLANK RULE. No split reported is not a split of zero."""
    assert est.method_split(1000, None, None) is None
    assert est.method_split(None, 500, 500) is None
    assert est.method_split(0, 0, 0) is None


def test_method_split_clamps_a_mail_figure_above_the_headline():
    """A restatement can leave mail momentarily above the total; that is not
    120% mail."""
    assert est.method_split(1000, 1200, None) == (1000.0, 0.0)


def test_no_mail_means_no_correction():
    """Maryland's case: the term must be exactly zero, not merely small."""
    assert est.mail_selection(0.0, 1_000_000.0, 3_000_000.0) == 0.0


def test_the_correction_dies_as_mail_reaches_the_whole_electorate():
    """Colorado is the case that fixes the shape. Where every voter is mailed a
    ballot, asking for one selects nobody, and the term has to go to nothing --
    otherwise an all-mail state gets the largest correction in the table."""
    selective = est.mail_selection(100_000.0, 0.0, 3_000_000.0)   # reach 3%
    universal = est.mail_selection(2_900_000.0, 0.0, 3_000_000.0)  # reach 97%
    assert selective > 0.15
    assert universal < 0.001
    assert selective > 100 * universal


def test_the_correction_is_capped_where_it_would_extrapolate():
    """At the opening of a window mail reach is near zero and the raw formula
    asks for 36 points, which is past anything it was ever fitted against."""
    wide_open = est.mail_selection(10.0, 0.0, 3_000_000.0)
    assert wide_open == pytest.approx(est.MAX_ADJUSTMENT)
    assert est.MAIL_SELECTION > est.MAX_ADJUSTMENT, "the cap must actually bind"


def test_the_correction_scales_with_how_much_of_the_return_is_mail():
    """Half mail is half the correction of all mail, at the same mail reach.

    Deliberately at a reach where the cap does not bind, since the cap is not
    linear in anything.
    """
    electorate = 3_000_000.0
    all_mail = est.mail_selection(900_000.0, 0.0, electorate)
    half_mail = est.mail_selection(900_000.0, 900_000.0, electorate)
    assert all_mail < est.MAX_ADJUSTMENT
    assert half_mail == pytest.approx(all_mail / 2)


def test_a_midterm_electorate_is_smaller_than_the_presidential_one(full_baseline):
    """Same ballots, smaller electorate, so mail has reached more of it."""
    presidential = est.expected_electorate(2024, "PA", full_baseline)
    midterm = est.expected_electorate(2026, "PA", full_baseline)
    assert midterm == pytest.approx(presidential * est.MIDTERM_TURNOUT)
    assert est.expected_electorate(2026, "ZZ", full_baseline) is None


def test_a_state_that_reports_no_method_split_gets_the_geography_only_estimate(baseline):
    """The fallback has to be the model as it was, not a guess at the split."""
    plain = est.estimate_day(2026, "NC", date(2026, 10, 20),
                             {MECKLENBURG: 600}, baseline)
    assert plain.mail_adjustment is None
    assert plain.est_dem_share == pytest.approx(plain.geo_dem_share)


def test_the_mail_term_moves_the_estimate_and_is_published_separately(baseline):
    """A reader has to be able to see which half of the answer is last election."""
    mailed = est.estimate_day(2026, "NC", date(2026, 10, 20),
                              {MECKLENBURG: 600}, baseline,
                              statewide_ballots=600, mail_returned=600, inperson=0)
    plain = est.estimate_day(2026, "NC", date(2026, 10, 20),
                             {MECKLENBURG: 600}, baseline)
    assert mailed.geo_dem_share == pytest.approx(plain.est_dem_share)
    assert mailed.mail_adjustment > 0
    assert mailed.est_dem_share == pytest.approx(
        mailed.geo_dem_share + mailed.mail_adjustment
    )
    assert mailed.mail_share == pytest.approx(1.0)


def test_lean_vs_baseline_stays_a_statement_about_geography(baseline):
    """The one sentence docs/party-estimate.md was willing to defend on its own.
    Folding the mail term into it would turn a checkable fact about turnout
    geography into a model output wearing the same label."""
    row = est.estimate_day(2026, "NC", date(2026, 10, 20),
                           {MECKLENBURG: 600}, baseline,
                           statewide_ballots=600, mail_returned=600, inperson=0)
    assert row.lean_vs_baseline == pytest.approx(
        row.geo_dem_share - row.baseline_dem_share
    )
    assert row.mail_adjustment > 0.01, "the two would agree if the term were zero"


def test_fit_recovers_the_coefficients_it_was_given():
    """Synthetic series built from a known (alpha, decay); the fit must find it."""
    truth_alpha, truth_decay = 0.30, 4.0
    series = []
    for state, geo in (("AA", 0.45), ("BB", 0.55), ("CC", 0.50)):
        one = []
        for i in range(1, 9):
            mail = 40_000.0 * i
            obs = est.Observation(
                cycle=2024, state=state, day=f"2024-10-{i:02d}", geo=geo,
                truth=0.0, ballots=mail, mail=mail, inperson=10_000.0 * i,
                electorate=2_000_000.0, baseline_dem_share=geo,
            )
            x = obs.selection_input(truth_decay)
            one.append(est.Observation(**{**obs.__dict__, "truth": geo + truth_alpha * x}))
        series.append(one)
    alpha, decay = est.fit_mail_selection(series)
    assert alpha == pytest.approx(truth_alpha, abs=0.02)
    assert decay == pytest.approx(truth_decay, abs=0.25)


def test_fit_weights_a_series_once_not_a_day_once():
    """Pennsylvania is seventy days and Colorado is five. Pooling by day fits
    Pennsylvania and calls it a model.

    Both series below carry the same four mail positions, so they differ ONLY in
    how many days they contribute. Per-day pooling would land on the long one's
    coefficient; per-series weighting lands between them.
    """
    def series(state, alpha, n):
        out = []
        for i in range(n):
            mail = 200_000.0 * (i % 4 + 1)
            probe = est.Observation(cycle=2024, state=state, day=f"d{i:03d}", geo=0.5,
                                    truth=0.0, ballots=mail, mail=mail, inperson=0.0,
                                    electorate=2_000_000.0, baseline_dem_share=0.5)
            x = probe.selection_input(est.MAIL_DECAY)
            out.append(est.Observation(**{**probe.__dict__, "truth": 0.5 + alpha * x}))
        return out

    long_low, short_high = series("AA", 0.10, 60), series("BB", 0.50, 4)
    alpha, _ = est.fit_mail_selection([long_low, short_high])
    assert alpha == pytest.approx(0.30, abs=0.02)

    # ...and the same rows pooled as one series DO land on the long one, which
    # is the failure mode this weighting exists to prevent.
    pooled, _ = est.fit_mail_selection([long_low + short_high])
    assert pooled < 0.15


def test_fit_returns_none_rather_than_a_number_when_there_is_nothing_to_fit():
    assert est.fit_mail_selection([]) is None
    assert est.fit_mail_selection([[]]) is None


def test_fitted_constants_still_match_the_data():
    """The shipped constants are a measurement, and measurements go stale.

    Refit them on whatever output/ holds now. If a new backfill or a new state
    moves them materially, this fails and docs/party-estimate.md has to be
    re-measured rather than inherited.
    """
    root = Path(__file__).resolve().parents[1]
    out = root / "output"
    if not (out / "ev_state_daily.csv").exists():
        pytest.skip("no published output/ tree in this checkout")
    full = est.load_baseline()
    panel = est.observations(out, full)
    # The same two exclusions `validate` applies: a series the capped model
    # cannot reach, and a universal vote-by-mail state whose mail_share is ~1.00
    # by law rather than by choice. Refitting on a different panel than the one
    # the constants were fitted on would make this guard fail for a reason that
    # is not drift.
    trainable = [
        est.mature_days(v) for k, v in panel.items()
        if len(v) >= 2 and max(o.ballots for o in v) >= est.THIN_BALLOTS
        and est.within_reach(est.mature_days(v))
        and k[1] not in est.UNIVERSAL_VBM
    ]
    fitted = est.fit_mail_selection(trainable)
    if fitted is None:
        pytest.skip("no scoreable state-cycle in output/ yet")
    alpha, decay = fitted
    assert alpha == pytest.approx(est.MAIL_SELECTION, abs=0.05), (
        f"the data now says alpha={alpha:.3f}; re-measure docs/party-estimate.md"
    )
    assert decay == pytest.approx(est.MAIL_DECAY, abs=1.0), (
        f"the data now says decay={decay:.2f}; re-measure docs/party-estimate.md"
    )


def test_model_error_is_refitted_from_the_panel():
    """The published band is a measurement too, and it is the one that drifted.

    ⚠️ THIS TEST EXISTS BECAUSE ITS ABSENCE COST SOMETHING. `MODEL_ERROR` was set
    to 5 points as "deliberately above the measured 3.4", and then nothing ever
    checked it again. When Pennsylvania 2022 joined the panel the measured error
    went to 4.4 and the headroom silently became a rounding error -- the band was
    still called conservative on the page while it had stopped being conservative.
    `counterfactual.py`'s band has been refit-guarded since the day it was set,
    which is exactly why that one never drifted and this one did.

    Two assertions, and the second is the one that matters: the band may sit
    above the measured error (it is meant to -- the mail term assumes a direction
    2026 need not repeat), but it may never sit below it.
    """
    root = Path(__file__).resolve().parents[1]
    out = root / "output"
    if not (out / "ev_state_daily.csv").exists():
        pytest.skip("no published output/ tree in this checkout")
    scored = [r for r in est.validate(out, est.load_baseline())
              if r.scored and r.in_range]
    if not scored:
        pytest.skip("nothing scoreable yet")
    measured = sum(r.mean_abs_error for r in scored) / len(scored) / 100

    assert est.MODEL_ERROR == pytest.approx(measured, abs=0.02), (
        f"the panel now measures {measured * 100:.2f} points against a shipped "
        f"band of {est.MODEL_ERROR * 100:.0f}; re-measure docs/party-estimate.md"
    )
    assert est.MODEL_ERROR >= measured, (
        "the band must never be narrower than the error it stands for"
    )


# --------------------------------------------------------------------------
# No data means no row. Never a 50/50 default.
# --------------------------------------------------------------------------
def test_a_state_with_no_county_data_produces_no_row(baseline):
    assert est.estimate_day(2026, "NC", date(2026, 10, 20), {}, baseline) is None


def test_counties_with_no_usable_ballots_produce_no_row(baseline):
    """Every county present but none weightable is still nothing to publish."""
    assert est.estimate_day(2026, "NC", date(2026, 10, 20),
                            {MECKLENBURG: 0, ALAMANCE: 0}, baseline) is None
    assert est.estimate_day(2026, "NC", date(2026, 10, 20),
                            {"99999": 1000}, baseline) is None


def test_no_estimable_state_writes_nothing(tmp_path, baseline):
    """The 50/50 trap in file form: an empty run must not emit a placeholder."""
    out = tmp_path / "output"
    (out / "counties").mkdir(parents=True)
    assert est.build(out, baseline) == []
    assert not (out / est.ESTIMATE_FILENAME).exists()


def test_a_state_that_reports_party_gets_no_estimate(tmp_path, baseline):
    """North Carolina publishes the real thing; modelling over it is noise."""
    out = _out_dir(tmp_path)
    assert est.build(out, baseline) == []
    # ...and `force` is the only way to score the method against it.
    assert len(est.build(out, baseline, force=True)) == 6


# --------------------------------------------------------------------------
# Coverage
# --------------------------------------------------------------------------
def test_coverage_share_is_county_ballots_over_the_states_own_total(baseline):
    """A state can report a headline its county file does not add up to yet."""
    row = est.estimate_day(2026, "NC", date(2026, 10, 20),
                           {MECKLENBURG: 600, ALAMANCE: 200}, baseline,
                           statewide_ballots=1000)
    assert row.coverage_share == pytest.approx(0.8)
    assert row.ballots_used == 800


def test_coverage_share_is_one_when_the_state_reports_no_total(baseline):
    row = est.estimate_day(2026, "NC", date(2026, 10, 20),
                           {MECKLENBURG: 600}, baseline)
    assert row.coverage_share == pytest.approx(1.0)


def test_coverage_share_never_exceeds_one(baseline):
    """Counties summing above the headline is a restatement, not extra data."""
    row = est.estimate_day(2026, "NC", date(2026, 10, 20),
                           {MECKLENBURG: 900}, baseline, statewide_ballots=500)
    assert row.coverage_share == 1.0


def test_coverage_electorate_counts_the_counties_that_have_not_reported(baseline):
    """Two of a hundred counties is complete BALLOT coverage and 2% of the state."""
    row = est.estimate_day(2026, "NC", date(2026, 10, 20),
                           {MECKLENBURG: 600, ALAMANCE: 200}, baseline)
    assert row.coverage_share == pytest.approx(1.0)
    assert 0.0 < row.coverage_electorate < 0.25
    assert row.counties_used == 2
    assert row.counties_total == 100


def test_counties_total_is_the_census_county_count():
    """Virginia's 133 includes its independent cities, which is what VA reports."""
    assert est.county_count("NC") == 100
    assert est.county_count("VA") == 133
    assert est.county_count("ZZ") is None


def test_real_slice_reports_partial_electorate_coverage_on_the_first_day(tmp_path, baseline):
    """The real 2024-09-24 slice: 74 of 100 counties, every ballot accounted for."""
    out = _out_dir(tmp_path)
    rows = {r.day.isoformat(): r for r in est.build(out, baseline, force=True)}
    first = rows["2024-09-24"]
    assert first.counties_used == 74
    assert first.coverage_share == pytest.approx(1.0)
    assert first.coverage_electorate < 1.0


# --------------------------------------------------------------------------
# The uncertainty band
# --------------------------------------------------------------------------
def test_band_is_the_flat_model_error_when_coverage_is_complete(baseline):
    row = est.estimate_day(2026, "NC", date(2026, 10, 20),
                           {MECKLENBURG: 600_000, ALAMANCE: 200_000}, baseline)
    assert row.est_dem_hi - row.est_dem_lo == pytest.approx(2 * est.MODEL_ERROR)
    assert row.est_dem_lo < row.est_dem_share < row.est_dem_hi


def test_a_thin_day_carries_the_wider_band_it_has_earned(baseline):
    """"Low confidence" is a word; the band is the number, and under 50,000
    ballots the measured error is 15 points, not 5. A South Carolina series that
    stops eighteen days out at 17,000 all-mail ballots has to read as "we do not
    know", not as a five-point answer."""
    thin = est.estimate_day(2026, "NC", date(2026, 10, 20),
                            {MECKLENBURG: 800}, baseline)
    thick = est.estimate_day(2026, "NC", date(2026, 10, 20),
                             {MECKLENBURG: 800_000}, baseline)
    assert (thin.est_dem_hi - thin.est_dem_lo) / 2 == pytest.approx(est.THIN_MODEL_ERROR)
    assert (thick.est_dem_hi - thick.est_dem_lo) / 2 == pytest.approx(est.MODEL_ERROR)
    assert est.THIN_MODEL_ERROR > est.MODEL_ERROR
    assert thin.confidence == "low"
    assert est.model_error(est.THIN_BALLOTS) == est.MODEL_ERROR
    assert est.model_error(est.THIN_BALLOTS - 1) == est.THIN_MODEL_ERROR


def test_band_widens_with_the_ballots_we_cannot_see(baseline):
    """Half the ballots unaccounted for has to widen the answer, a lot."""
    covered = est.estimate_day(2026, "NC", date(2026, 10, 20),
                               {MECKLENBURG: 800}, baseline, statewide_ballots=800)
    half = est.estimate_day(2026, "NC", date(2026, 10, 20),
                            {MECKLENBURG: 800}, baseline, statewide_ballots=1600)
    assert half.est_dem_share == pytest.approx(covered.est_dem_share)
    assert (half.est_dem_hi - half.est_dem_lo) > (covered.est_dem_hi - covered.est_dem_lo)
    assert half.confidence == "low"


def test_coverage_band_collapses_at_full_coverage(baseline):
    lo, hi = est.coverage_band(0.5, 1.0, [], baseline.counties("NC"))
    assert (lo, hi) == (0.5, 0.5)


def test_coverage_band_is_bounded_by_the_missing_counties(baseline):
    """A hard bound: the unseen ballots come from real places with real leans."""
    missing = [baseline.get(ALLEGHANY)]
    lo, hi = est.coverage_band(0.60, 0.5, missing, baseline.counties("NC"))
    expected = 0.60 * 0.5 + 0.5 * baseline.get(ALLEGHANY).dem_share
    assert lo == pytest.approx(expected) and hi == pytest.approx(expected)


def test_band_never_leaves_zero_to_one_and_always_contains_its_centre(baseline):
    """A share cannot be negative or above one, and a band that does not contain
    its own centre is worse than no band. The mail term can push a lopsided
    county's estimate hard against either end."""
    for ballots, mail in (({ALLEGHANY: 100}, None), ({ALLEGHANY: 100}, 100),
                          ({MECKLENBURG: 100}, 100), ({MECKLENBURG: 900_000}, 900_000)):
        row = est.estimate_day(2026, "NC", date(2026, 10, 20), ballots, baseline,
                               statewide_ballots=sum(ballots.values()),
                               mail_returned=mail, inperson=0 if mail else None)
        assert 0.0 <= row.est_dem_lo <= row.est_dem_hi <= 1.0
        assert row.est_dem_lo <= row.est_dem_share <= row.est_dem_hi


def test_confidence_is_never_high(tmp_path, full_baseline):
    """No amount of coverage repairs the assumption underneath. See the docs."""
    out = _out_dir(tmp_path)
    rows = est.build(out, full_baseline, force=True)
    assert rows
    assert {r.confidence for r in rows} <= {"low", "medium"}


def test_thin_days_are_low_confidence(tmp_path, baseline):
    """2,252 ballots statewide tells you nothing about the eventual electorate."""
    out = _out_dir(tmp_path)
    rows = {r.day.isoformat(): r for r in est.build(out, baseline, force=True)}
    assert rows["2024-09-24"].ballots_used < est.THIN_BALLOTS
    assert rows["2024-09-24"].confidence == "low"
    assert rows["2024-11-05"].confidence == "medium"


# --------------------------------------------------------------------------
# The measured / modelled split, for the day part of a state can be counted
# --------------------------------------------------------------------------
def test_every_row_today_is_wholly_modelled(tmp_path, baseline):
    """Nothing counts any of this yet -- the nine no-registration states have no
    such count anywhere, and Arizona's county recorders publish none either. The
    default has to be 'model', never a quiet 'blend' of nothing."""
    out = _out_dir(tmp_path)
    for row in est.build(out, baseline, force=True):
        assert row.measured_fraction == 0.0
        assert row.modelled_fraction == 1.0
        assert row.estimate_basis == "model"
        assert row.to_dict()["estimate_basis"] == "model"


def test_the_band_shrinks_in_proportion_to_what_is_counted(baseline):
    """Count part of it and the model is only standing in for the rest, so it
    only gets to be wrong about the rest. This is arithmetic, not a claim that
    the model got better."""
    ballots = {MECKLENBURG: 600_000, ALAMANCE: 200_000}
    widths = {}
    for fraction in (0.0, 0.5, 1.0):
        row = est.estimate_day(2026, "NC", date(2026, 10, 20), ballots, baseline,
                               measured_dem=300, measured_rep=300,
                               measured_fraction=fraction)
        widths[fraction] = (row.est_dem_hi - row.est_dem_lo) / 2
    assert widths[0.0] == pytest.approx(est.MODEL_ERROR)
    assert widths[0.5] == pytest.approx(est.MODEL_ERROR / 2)
    assert widths[1.0] == pytest.approx(0.0)


def test_a_fully_counted_row_is_reported_not_modelled(baseline):
    """At f=1 the answer is not a model at all and must not claim to be one."""
    row = est.estimate_day(2026, "NC", date(2026, 10, 20), {MECKLENBURG: 600},
                           baseline, measured_dem=700, measured_rep=300,
                           measured_fraction=1.0)
    assert row.estimate_basis == "reported"
    assert row.est_dem_share == pytest.approx(0.7)


def test_a_measured_fraction_with_no_split_behind_it_is_ignored(baseline):
    """Claiming to have counted some of it with nothing to show is not a blend.
    Never assume the counted part split 50/50."""
    row = est.estimate_day(2026, "NC", date(2026, 10, 20), {MECKLENBURG: 600},
                           baseline, measured_fraction=0.5)
    assert row.estimate_basis == "model"
    assert row.measured_fraction == 0.0
    assert row.measured_dem_share is None


def test_the_blend_is_the_arithmetic_arizona_already_uses():
    """`az.blend_party_share` owns this arithmetic for the adapter side and stays
    there, because the ingest path must never import `ev.estimate`. The two are
    kept honest by MODEL_ERROR and by this."""
    from ev.adapters import az

    blended = az.blend_party_share(
        measured_dem=600, measured_rep=400, measured_fraction=0.25,
        modelled_dem_share=0.40,
    )
    assert blended.dem_share == pytest.approx(0.25 * 0.60 + 0.75 * 0.40)
    assert blended.band_half_width == pytest.approx(est.MODEL_ERROR * 0.75)


# --------------------------------------------------------------------------
# THE guarantee: a model number never lands in a reported column
# --------------------------------------------------------------------------
def test_estimate_columns_carry_no_reported_party_column():
    assert not (est.FORBIDDEN_COLUMNS & set(est.ESTIMATE_COLUMNS))
    assert est.FORBIDDEN_COLUMNS <= set(STATE_DAILY_COLUMNS)


def test_write_never_touches_the_reported_party_columns(tmp_path, baseline):
    """Publish into a real output/ tree and prove nothing else moved."""
    out = _out_dir(tmp_path)
    before = {
        p.relative_to(out): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(out.rglob("*")) if p.is_file()
    }

    rows = est.build(out, baseline, force=True)
    est.write(out, rows)

    after = {
        p.relative_to(out): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(out.rglob("*")) if p.is_file()
    }
    assert set(after) - set(before) == {Path(est.ESTIMATE_FILENAME)}
    for name, digest in before.items():
        assert after[name] == digest, f"{name} was modified by the estimate write"


def test_estimate_write_leaves_unreported_party_cells_blank(tmp_path, baseline):
    """The Georgia case: blank must survive the estimate run as blank."""
    out = tmp_path / "output"
    (out / "counties").mkdir(parents=True)
    with (out / "ev_state_daily.csv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=STATE_DAILY_COLUMNS, lineterminator="\n")
        w.writeheader()
        w.writerow({"cycle": "2026", "state": "NC", "date": "2026-10-20",
                    "days_to_election": "14", "ballots_total": "800",
                    "source_tier": "1", "source_name": "test"})
    with (out / "counties" / "nc.csv").open("w", newline="") as fh:
        fh.write("cycle,state,county_fips,date,ballots_total\n")
        fh.write(f"2026,NC,{MECKLENBURG},2026-10-20,600\n")
        fh.write(f"2026,NC,{ALAMANCE},2026-10-20,200\n")

    est.write(out, est.build(out, baseline))

    reported = list(csv.DictReader((out / "ev_state_daily.csv").open()))
    assert len(reported) == 1
    for column in sorted(est.FORBIDDEN_COLUMNS):
        assert reported[0][column] == "", column
    estimated = list(csv.DictReader((out / est.ESTIMATE_FILENAME).open()))
    assert len(estimated) == 1
    assert float(estimated[0]["est_dem_share"]) > 0.5


def test_published_row_matches_the_column_contract(baseline):
    row = est.estimate_day(2026, "NC", date(2026, 10, 20),
                           {MECKLENBURG: 600}, baseline, has_party_reg=True)
    out = row.to_dict()
    assert set(out) == set(est.ESTIMATE_COLUMNS)
    assert out["days_to_election"] == "14"
    assert out["method"] == est.METHOD
    assert out["state_has_party_reg"] == "true"


def test_party_reg_flag_is_carried_from_the_meta_table():
    """Arizona registers by party; its SoS table just omits it. The CSV says so."""
    flags = est.read_party_reg_flags(
        Path(__file__).resolve().parents[1] / "data" / "meta" / "states.csv"
    )
    assert flags["AZ"] is True
    assert flags["GA"] is False and flags["OH"] is False


# --------------------------------------------------------------------------
# The vendored baseline
# --------------------------------------------------------------------------
#: The one place the vendored baseline is knowingly short of the 2020 census
#: county list, recorded rather than papered over.
#:
#: Alaska split Valdez-Cordova Census Area (02261) into Chugach (02063) and
#: Copper River (02066) in 2019, and `data/baseline/county_results_2024.csv` --
#: the forecast model's own county file, copied byte-for-byte and checksummed in
#: estimate.py, so not ours to rewrite -- still carries the pre-split 02261.
#:
#: It cannot bias an estimate: Alaska's tier-1 source is the Division's
#: statewide Election Statistics block, its near-daily report is keyed by state
#: HOUSE DISTRICT rather than by borough, and `ak.py` therefore publishes no
#: county rows at all. `estimate` never reads an Alaska county because there is
#: never one to read. If Alaska ever gains a borough-keyed source, this entry
#: must go and the baseline must be extended first.
KNOWN_BASELINE_GAPS: dict[str, set[str]] = {"AK": {"02063", "02066"}}


def test_baseline_covers_every_county_of_every_tracked_state(full_baseline):
    """A short baseline would silently drop counties and bias every estimate."""
    for state in TIER1:
        names = [n for n, _ in _fips.CENSUS_COUNTIES[state]]
        expected = {_fips.lookup(state, n)[0] for n in names}
        have = {c.fips for c in full_baseline.counties(state)}
        short = expected - have - KNOWN_BASELINE_GAPS.get(state, set())
        assert not short, f"{state}: baseline is missing {sorted(short)}"


def test_baseline_state_totals_reproduce_the_certified_2024_result(full_baseline):
    """Spot check against the certified presidential totals, to the vote."""
    for state, dem, rep in [
        ("GA", 2_548_017, 2_663_117),
        ("NC", 2_715_375, 2_898_423),
        ("PA", 3_423_042, 3_543_308),
        ("TX", 4_835_250, 6_393_597),
    ]:
        counties = full_baseline.counties(state)
        assert sum(c.votes_dem for c in counties) == dem
        assert sum(c.votes_rep for c in counties) == rep


def test_baseline_is_keyed_by_fips_not_name(full_baseline):
    """The vendored file labels 13121 'Campbell'; a name join would mis-map it."""
    assert full_baseline.get("13121").state == "GA"
    assert full_baseline.get("13121").dem_share > 0.7


def test_load_baseline_raises_rather_than_returning_a_partial_table(tmp_path):
    with pytest.raises(est.EstimateError):
        est.load_baseline(tmp_path / "nope.csv")

    wrong = tmp_path / "wrong.csv"
    wrong.write_text("county,dem,rep\n1,2,3\n")
    with pytest.raises(est.EstimateError):
        est.load_baseline(wrong)

    empty = tmp_path / "empty.csv"
    empty.write_text("county_fips,state,votes_dem,votes_rep\n")
    with pytest.raises(est.EstimateError):
        est.load_baseline(empty)


# --------------------------------------------------------------------------
# Ground truth. These numbers are the argument for shipping or not shipping.
# --------------------------------------------------------------------------
def test_ground_truth_north_carolina_2024(tmp_path, baseline):
    """Scored against a state that DOES report party registration.

    North Carolina 2024 is the method's easiest case and always was: every county
    reports, the file is voter-level, its registration split sits close to its
    presidential split, and only 7% of its early ballots came by mail, so the
    mail term has almost nothing to do. It is here to pin the number, and to pin
    the fact that the mail term does not WRECK the state it was not needed for --
    a correction that fixes Pennsylvania by moving North Carolina off a
    0.9-point error would be a trade, not an improvement.

    This fixture is one state, so `validate` has nothing to hold it out against
    and falls back to the shipped constants; `held_out` says so, and the
    leave-one-state-out numbers in docs/party-estimate.md come from the full
    output/ tree instead.
    """
    out = _out_dir(tmp_path)
    results = est.validate(out, baseline)
    assert len(results) == 1
    nc = results[0]

    assert (nc.cycle, nc.state) == (2024, "NC")
    assert nc.held_out is False
    assert nc.fitted == (est.MAIL_SELECTION, est.MAIL_DECAY)

    # Final-day error on the Democratic two-party share, in percentage points.
    assert nc.final_error == pytest.approx(+1.1, abs=0.3)
    assert abs(nc.final_error) < 3.0

    # It is still better than quoting the 2024 result, and it did not make the
    # geography-only model worse here.
    assert nc.mean_abs_error < nc.null_mean_abs_error
    assert nc.mean_abs_error <= nc.geo_mean_abs_error + 0.5
    assert nc.null_mean_abs_error < 2.0


def _two_state_tree(tmp_path: Path, other_truth: float) -> Path:
    """An output/ tree with two all-mail states, one of which is a control.

    Both report party. `NC` always returns a split 20 points more Democratic
    than its counties; `PA` returns whatever `other_truth` says. The point is
    that the constants scoring one are paid for by the other.
    """
    out = tmp_path / "output"
    (out / "counties").mkdir(parents=True)
    days = [("2024-10-11", 25), ("2024-10-21", 15), ("2024-11-05", 0)]
    state_rows, county_rows = [], {"nc": [], "pa": []}
    for state, fips, truth in (("NC", MECKLENBURG, 0.70), ("PA", "42101", other_truth)):
        for i, (day, dte) in enumerate(days, start=1):
            total = 200_000 * i
            dem = int(total * truth)
            state_rows.append(
                f"2024,{state},{day},{dte},{total},,,{total},0,{dem},{total - dem},0,0,0,1,test,x"
            )
            county_rows[state.lower()].append(f"2024,{state},{fips},{day},{total}")
    with (out / "ev_state_daily.csv").open("w", newline="") as fh:
        fh.write(",".join(STATE_DAILY_COLUMNS) + "\n")
        fh.write("\n".join(state_rows) + "\n")
    for name, rows in county_rows.items():
        with (out / "counties" / f"{name}.csv").open("w", newline="") as fh:
            fh.write("cycle,state,county_fips,date,ballots_total\n")
            fh.write("\n".join(rows) + "\n")
    return out


def test_the_mail_term_is_scored_leave_one_state_out(tmp_path, full_baseline):
    """The protocol, asserted as a protocol.

    `validate` fits the two mail constants on the OTHER states and scores this
    one with them. The failure this guards against is the one docs/regression.md
    documents: a specification that looks like a finding because it was scored on
    the rows it was fitted on.

    The proof is that moving the OTHER state's truth moves this state's score. If
    scoring were in-sample it could not.

    ⚠️ THE CONTROL VALUE USED TO BE 0.50 AND CANNOT BE ANY MORE. Philadelphia's
    counties are 79.8% Democratic, so a 50% truth asks the model for 29.8 points
    of correction against a `MAX_ADJUSTMENT` of 20 — outside its reach, which
    `within_reach` now (correctly) keeps out of the fit, leaving the other state
    with nothing to be held out against. 0.62 is 17.8 points out: the same
    argument, inside the range the model actually serves.
    """
    scores = {}
    for other_truth in (0.70, 0.62):
        out = _two_state_tree(tmp_path / str(other_truth), other_truth)
        results = {v.state: v for v in est.validate(out, full_baseline)}
        assert set(results) == {"NC", "PA"}
        assert all(v.held_out for v in results.values())
        scores[other_truth] = results["NC"].mean_abs_error

    # Pennsylvania moved further from its counties, so the constants North
    # Carolina is scored with moved with it and North Carolina's error grew.
    assert scores[0.62] > scores[0.70] + 1.0


def test_a_state_with_no_other_state_falls_back_to_the_shipped_constants(
    tmp_path, baseline
):
    """One state cannot hold itself out, and must say so rather than pretend."""
    out = _out_dir(tmp_path)
    only = est.validate(out, baseline)[0]
    assert only.held_out is False
    assert only.fitted == (est.MAIL_SELECTION, est.MAIL_DECAY)
    assert any("in sample" in line for line in est.format_validation([only]))


def test_a_degenerate_series_never_trains_the_constants(tmp_path, full_baseline):
    """Eight ballots must not carry the same weight as Pennsylvania's seventy days.

    North Carolina 2026 is three days and eight ballots, six of them from
    registered Democrats. `fit_mail_selection` sees series, not days, so without
    a floor that would count for as much as a completed state.
    """
    out = _two_state_tree(tmp_path, 0.70)
    with (out / "ev_state_daily.csv").open("a", newline="") as fh:
        fh.write("2026,ME,2026-09-10,54,8,,,8,0,6,1,0,1,0,1,test,x\n")
        fh.write("2026,ME,2026-09-11,53,8,,,8,0,6,1,0,1,0,1,test,x\n")
    with (out / "counties" / "me.csv").open("w", newline="") as fh:
        fh.write("cycle,state,county_fips,date,ballots_total\n")
        fh.write("2026,ME,23005,2026-09-10,8\n2026,ME,23005,2026-09-11,8\n")

    scored = {v.state: v for v in est.validate(out, full_baseline)}
    # Maine is still SHOWN -- hiding what September looks like would be its own
    # dishonesty -- but it neither trains the constants nor counts as a fold.
    assert "ME" in scored
    assert scored["ME"].scored is False
    without = est.validate(_two_state_tree(tmp_path / "clean", 0.70), full_baseline)
    assert scored["NC"].fitted == {v.state: v for v in without}["NC"].fitted


def test_a_series_too_thin_to_fit_is_too_thin_to_average(tmp_path, full_baseline):
    """The same sentence, applied twice, and it took a second look to see it.

    North Carolina 2026 -- three days, EIGHT ballots -- was excluded from
    fitting the constants with a comment saying why, and then averaged into the
    headline error as a full fold. It scored an MAE of 13.6 against a mean of
    3.9 over twelve real series, moving this model's published accuracy by
    nearly a point on the strength of eight ballots.

    `mature_days()` cannot catch it: it normalises by the SERIES' OWN maximum,
    so all three of those days are 100% of "the eventual vote" when the eventual
    vote so far is eight. A running series is not a yardstick for itself.
    """
    out = _two_state_tree(tmp_path, 0.70)
    with (out / "ev_state_daily.csv").open("a", newline="") as fh:
        fh.write("2026,ME,2026-09-10,54,8,,,8,0,6,1,0,1,0,1,test,x\n")
        fh.write("2026,ME,2026-09-11,53,8,,,8,0,6,1,0,1,0,1,test,x\n")
    with (out / "counties" / "me.csv").open("w", newline="") as fh:
        fh.write("cycle,state,county_fips,date,ballots_total\n")
        fh.write("2026,ME,23005,2026-09-10,8\n2026,ME,23005,2026-09-11,8\n")

    results = est.validate(out, full_baseline)
    thin = [r for r in results if not r.scored]
    assert [r.state for r in thin] == ["ME"]

    # The row is printed, and printed with the reason.
    lines = list(est.format_validation(results))
    assert any("SHOWN, NOT SCORED" in line for line in lines)
    # ...and the averages are over the folds that survived, and say so.
    kept = sum(1 for r in results if r.scored)
    assert any(f"Averages are over the {kept} series" in line for line in lines)

    # The headline is the same one the thin series was never part of.
    alone = est.validate(_two_state_tree(tmp_path / "clean", 0.70), full_baseline)
    def mae(rs):
        keep = [r for r in rs if r.scored]
        return sum(r.mean_abs_error for r in keep) / len(keep)
    assert mae(results) == pytest.approx(mae(alone))


def test_every_published_fold_is_thick_enough_to_be_one(full_baseline):
    """Against the real output/ tree, so a new thin state cannot slip in."""
    out = Path(__file__).resolve().parents[1] / "output"
    if not (out / "ev_state_daily.csv").exists():
        pytest.skip("no published output/ tree in this checkout")
    results = est.validate(out, full_baseline)
    if not results:
        pytest.skip("nothing scoreable yet")
    panel = est.observations(out, full_baseline)
    for r in results:
        peak = max(o.ballots for o in panel[(r.cycle, r.state)])
        assert r.scored == (peak >= est.THIN_BALLOTS), (r.state, r.cycle, peak)


def test_validate_ignores_state_days_with_no_reported_party(tmp_path, baseline):
    """Nothing to score against means no score, not a zero error."""
    out = tmp_path / "output"
    (out / "counties").mkdir(parents=True)
    shutil.copy(FIXTURES / "nc_counties_2024.csv", out / "counties" / "nc.csv")
    assert est.validate(out, baseline) == []


def test_format_validation_says_so_when_there_is_nothing_to_score():
    assert any("no state-cycle" in line for line in est.format_validation([]))
