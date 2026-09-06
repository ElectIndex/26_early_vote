"""The counterfactual: its identity, its refusals, and its measured failure.

Five of these are worth more than the rest put together:

* `test_the_full_2024_electorate_reproduces_the_certified_margin` — the whole
  method rests on one identity: weight every county by its own 2024 votes and you
  get back the state's certified 2024 margin. If that ever stops holding, the
  "shift" stops being a difference in composition and becomes an artefact.

* `test_a_state_with_no_reference_series_produces_no_row` — the trap this feature
  exists to avoid. A state without a like-for-like 2024 early electorate must NOT
  fall back to comparing against the full 2024 electorate, which would mostly
  re-measure "early voters are not everyone". No data, no row, never a zero.

* `test_the_measured_gain_does_not_clear_the_bar` — the finding. County geography
  does not beat "the composition has not changed". If a future data pull makes it
  clear MIN_GAIN, this test fails and docs/counterfactual.md's recommendation has
  to be revisited rather than inherited.

* `test_the_scale_is_fitted_leave_one_state_out` — the protocol. A fitted
  constant scored on its own training data is how docs/regression.md nearly
  shipped seven specifications that were worse than knowing nothing.

* `test_write_touches_only_counterfactual_csv` — a model number must never land
  in a column that means "the state reported this".
"""

from __future__ import annotations

import csv
import hashlib
import shutil
from datetime import date
from pathlib import Path

import pytest

from ev import counterfactual as cf
from ev.adapters import _fips
from ev.registry import tracked_states

FIXTURES = Path(__file__).parent / "fixtures" / "counterfactual"
NC_BASELINE = FIXTURES / "county_results_2024_nc.csv"
REPO_OUTPUT = Path(__file__).resolve().parents[1] / "output"

# Three real North Carolina counties spanning the state: Mecklenburg (Charlotte,
# heavily D), Alamance (suburban R) and Alleghany (small, heavily R).
MECKLENBURG, ALAMANCE, ALLEGHANY = "37119", "37001", "37005"


@pytest.fixture(scope="module")
def nc_baseline() -> cf.Baseline:
    return cf.load_baseline(NC_BASELINE)


@pytest.fixture(scope="module")
def full_baseline() -> cf.Baseline:
    return cf.load_baseline()


@pytest.fixture()
def nc_out(tmp_path: Path) -> Path:
    """An output/ tree carrying the real North Carolina slice.

    Three matched days in each of 2022 and 2024 (30, 10 and 0 days out), all 100
    counties, plus the state's own daily rows and its age/race/sex tables, and
    the three real 2026 days as published today.
    """
    out = tmp_path / "output"
    (out / "counties").mkdir(parents=True)
    (out / "demo").mkdir()
    shutil.copy(FIXTURES / "nc_counties.csv", out / "counties" / "nc.csv")
    shutil.copy(FIXTURES / "nc_state_daily.csv", out / "ev_state_daily.csv")
    shutil.copy(FIXTURES / "nc_demo.csv", out / "demo" / "nc.csv")
    return out


def _hash_tree(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*")) if p.is_file()
    }


def _write(path: Path, columns: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, columns)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


COUNTY_COLUMNS = ["cycle", "state", "county_fips", "county_name", "date",
                  "days_to_election", "ballots_total"]
STATE_COLUMNS = ["cycle", "state", "date", "days_to_election", "ballots_total",
                 "party_dem", "party_rep", "party_oth", "party_npa"]
DEMO_COLUMNS = ["cycle", "state", "date", "days_to_election", "dimension",
                "bucket", "ballots_total"]


def _county(cycle, dte, ballots: dict[str, object], state="NC") -> list[dict]:
    return [
        {"cycle": cycle, "state": state, "county_fips": fips, "county_name": "",
         "date": (cf.election_date(cycle) - cf.timedelta(days=dte)).isoformat(),
         "days_to_election": dte,
         "ballots_total": "" if count is None else count}
        for fips, count in ballots.items()
    ]


def _state(cycle, dte, total, state="NC", **party) -> dict:
    row = {"cycle": cycle, "state": state,
           "date": (cf.election_date(cycle) - cf.timedelta(days=dte)).isoformat(),
           "days_to_election": dte, "ballots_total": total,
           "party_dem": "", "party_rep": "", "party_oth": "", "party_npa": ""}
    row.update({k: v for k, v in party.items()})
    return row


def _tree(tmp_path: Path, counties: list[dict], states: list[dict] | None = None,
          demo: list[dict] | None = None, state="NC") -> Path:
    out = tmp_path / "output"
    _write(out / "counties" / f"{state.lower()}.csv", COUNTY_COLUMNS, counties)
    _write(out / "ev_state_daily.csv", STATE_COLUMNS, states or [])
    if demo is not None:
        _write(out / "demo" / f"{state.lower()}.csv", DEMO_COLUMNS, demo)
    return out


# --------------------------------------------------------------------------
# The identity the whole method rests on
# --------------------------------------------------------------------------
def test_the_full_2024_electorate_reproduces_the_certified_margin(full_baseline):
    """Weight every county by its own 2024 votes and the state's margin comes back.

    This is what makes `shift_pp` a statement about COMPOSITION and nothing else:
    the arithmetic is exact at the full electorate, so any difference it reports
    is a difference in who is being weighted, not an error in the weighting.
    """
    for state in tracked_states():
        counties = full_baseline.counties(state)
        if not counties:
            continue
        full = {c.fips: c.two_party for c in counties}
        got = cf.composition_margin(full, full_baseline)
        assert got is not None, state
        assert got[0] == pytest.approx(cf.state_actual_margin(state, full_baseline),
                                       abs=1e-9), state
        assert got[1] == len(counties)


def test_the_baseline_still_carries_the_certified_2024_returns(full_baseline):
    """The vote totals docs/party-estimate.md verified, re-checked here.

    A short or restated baseline would move every number in this module, so it is
    pinned against the certified counts rather than trusted.
    """
    certified = {
        "GA": (2_548_017, 2_663_117),
        "NC": (2_715_375, 2_898_423),
        "PA": (3_423_042, 3_543_308),
        "TX": (4_835_250, 6_393_597),
    }
    for state, (dem, rep) in certified.items():
        counties = full_baseline.counties(state)
        assert sum(c.votes_dem for c in counties) == dem, state
        assert sum(c.votes_rep for c in counties) == rep, state
        assert cf.state_actual_margin(state, full_baseline) == pytest.approx(
            (dem - rep) / (dem + rep) * 100, abs=1e-9)


def test_county_margin_is_two_party_and_democratic_positive(nc_baseline):
    mecklenburg = nc_baseline.get(MECKLENBURG)
    alleghany = nc_baseline.get(ALLEGHANY)
    assert cf.county_margin(mecklenburg) > 0
    assert cf.county_margin(alleghany) < 0
    assert cf.county_margin(mecklenburg) == pytest.approx(
        (mecklenburg.votes_dem - mecklenburg.votes_rep) / mecklenburg.two_party * 100)


# --------------------------------------------------------------------------
# The refusals. Every one of these is "no row", never a zero.
# --------------------------------------------------------------------------
def test_a_state_with_no_reference_series_produces_no_row(tmp_path, nc_baseline):
    """The trap. 2026 alone must NOT be compared against the full 2024 electorate.

    This is Florida and Illinois today: real 2026 county rows, no 2024 county
    series. The answer is nothing, not a shift measured against everybody.
    """
    counties = _county(2026, 20, {MECKLENBURG: 1000, ALAMANCE: 500})
    out = _tree(tmp_path, counties, [_state(2026, 20, 1500)])
    assert cf.build(out, nc_baseline) == []


def test_a_missing_state_never_produces_a_zero_shift(tmp_path, nc_baseline):
    """Absence of data is not a value — the same instinct as THE BLANK RULE."""
    out = _tree(tmp_path, _county(2026, 20, {MECKLENBURG: 1000}))
    rows = cf.build(out, nc_baseline)
    assert not rows
    assert not any(r.shift_pp == 0 for r in rows)


def test_a_blank_ballots_total_is_absent_not_zero(tmp_path, nc_baseline):
    """A county that reported nothing must not be weighted as zero ballots."""
    blank = _county(2024, 10, {MECKLENBURG: 1000, ALAMANCE: None})
    present = _county(2022, 10, {MECKLENBURG: 1000, ALAMANCE: 0})
    out = _tree(tmp_path, blank + present)
    rows = cf.build(out, nc_baseline)
    assert len(rows) == 1
    assert rows[0].counties_used == 1
    assert rows[0].reference_counties_used == 2
    # Alamance is absent from this cycle and reports a genuine zero in the
    # reference, so both sides are Mecklenburg alone and the shift is zero.
    assert rows[0].shift_pp == pytest.approx(0.0)


def test_a_county_reporting_a_genuine_zero_counts_as_reporting(nc_baseline):
    got = cf.composition_margin({MECKLENBURG: 1000, ALLEGHANY: 0}, nc_baseline)
    assert got is not None
    margin, used, ballots = got
    assert used == 2 and ballots == 1000
    assert margin == pytest.approx(cf.county_margin(nc_baseline.get(MECKLENBURG)))


def test_a_county_with_no_baseline_is_dropped_never_guessed(nc_baseline):
    got = cf.composition_margin({MECKLENBURG: 100, "99999": 900}, nc_baseline)
    assert got is not None
    assert got[1] == 1 and got[2] == 100


def test_nothing_weightable_is_none_not_a_fifty_fifty(nc_baseline):
    assert cf.composition_margin({}, nc_baseline) is None
    assert cf.composition_margin({"99999": 500}, nc_baseline) is None
    assert cf.composition_margin({MECKLENBURG: 0}, nc_baseline) is None


def test_counties_are_keyed_by_fips_never_by_name(nc_baseline):
    """The baseline calls 13121 "Campbell", not "Fulton". Names do not join."""
    assert nc_baseline.get(MECKLENBURG) is not None
    assert nc_baseline.get("Mecklenburg") is None


# --------------------------------------------------------------------------
# Like-for-like: the days-to-election match
# --------------------------------------------------------------------------
def test_days_are_matched_on_days_to_election_not_calendar_date(tmp_path, nc_baseline):
    """Election Day moves, so the same date is a different point of the campaign."""
    counties = (_county(2024, 10, {MECKLENBURG: 1000})
                + _county(2022, 10, {MECKLENBURG: 1000}))
    rows = cf.build(_tree(tmp_path, counties), nc_baseline)
    assert len(rows) == 1
    row = rows[0]
    assert row.days_to_election == row.reference_days_to_election == 10
    # Same day out, three different calendar dates.
    assert row.day == date(2024, 10, 26)
    assert row.reference_day == date(2022, 10, 29)


def test_a_reference_day_outside_the_tolerance_is_no_match(tmp_path, nc_baseline):
    counties = (_county(2024, 10, {MECKLENBURG: 1000})
                + _county(2022, 10 + cf.DTE_MATCH_TOLERANCE + 1, {MECKLENBURG: 1000}))
    assert cf.build(_tree(tmp_path, counties), nc_baseline) == []


def test_the_closest_day_inside_the_tolerance_wins(tmp_path, nc_baseline):
    counties = (_county(2024, 10, {MECKLENBURG: 1000})
                + _county(2022, 8, {MECKLENBURG: 1000})
                + _county(2022, 12, {MECKLENBURG: 1000}))
    rows = cf.build(_tree(tmp_path, counties), nc_baseline)
    assert [r.reference_days_to_election for r in rows] == [8]


def test_post_election_days_are_dropped_from_both_sides(tmp_path, nc_baseline):
    """A post-election row counts mail that arrived after the polls closed."""
    counties = (_county(2024, -2, {MECKLENBURG: 1000})
                + _county(2022, -2, {MECKLENBURG: 1000}))
    assert cf.build(_tree(tmp_path, counties), nc_baseline) == []


def test_a_2026_row_appears_once_2024_reaches_the_same_days_out(tmp_path, nc_baseline):
    """The feature is not broken, it is waiting: today 2026 is 58 days out and
    the 2024 county series starts at 46."""
    too_early = (_county(2026, 58, {MECKLENBURG: 1000})
                 + _county(2024, 46, {MECKLENBURG: 1000}))
    assert cf.build(_tree(tmp_path, too_early), nc_baseline) == []

    in_range = (_county(2026, 46, {MECKLENBURG: 1000})
                + _county(2024, 46, {MECKLENBURG: 1000}))
    rows = cf.build(_tree(tmp_path, in_range), nc_baseline)
    assert [(r.cycle, r.reference_cycle) for r in rows] == [(2026, 2024)]


def test_2022_is_never_a_target_cycle(tmp_path, nc_baseline):
    """It has no earlier early electorate in this repo to be compared against."""
    assert 2022 not in cf.REFERENCE_CYCLE
    counties = _county(2022, 10, {MECKLENBURG: 1000})
    assert cf.build(_tree(tmp_path, counties), nc_baseline) == []


# --------------------------------------------------------------------------
# The arithmetic of the headline
# --------------------------------------------------------------------------
def test_shift_is_the_difference_of_the_two_compositions(tmp_path, nc_baseline):
    counties = (_county(2024, 10, {MECKLENBURG: 900, ALLEGHANY: 100})
                + _county(2022, 10, {MECKLENBURG: 100, ALLEGHANY: 900}))
    row = cf.build(_tree(tmp_path, counties), nc_baseline)[0]
    assert row.shift_pp == pytest.approx(
        row.composition_margin - row.reference_composition_margin)
    # Mecklenburg-heavy now, Alleghany-heavy then: the shift is Democratic.
    assert row.shift_pp > 0


def test_implied_margin_is_the_certified_result_moved_by_the_shift(tmp_path, nc_baseline):
    """NOT the raw composition margin, which would be the trap this refuses."""
    counties = (_county(2024, 10, {MECKLENBURG: 900, ALLEGHANY: 100})
                + _county(2022, 10, {MECKLENBURG: 100, ALLEGHANY: 900}))
    row = cf.build(_tree(tmp_path, counties), nc_baseline)[0]
    assert row.implied_margin_2024 == pytest.approx(
        row.actual_margin_2024 + row.shift_pp)
    assert row.actual_margin_2024 == pytest.approx(
        cf.state_actual_margin("NC", nc_baseline))
    assert row.implied_margin_2024 != pytest.approx(row.composition_margin)


def test_an_unchanged_composition_gives_a_computed_zero(tmp_path, nc_baseline):
    """A zero here is a measurement. A zero from missing data is forbidden."""
    counties = (_county(2024, 10, {MECKLENBURG: 700, ALAMANCE: 300})
                + _county(2022, 10, {MECKLENBURG: 7000, ALAMANCE: 3000}))
    row = cf.build(_tree(tmp_path, counties), nc_baseline)[0]
    assert row.shift_pp == pytest.approx(0.0, abs=1e-9)
    assert row.implied_margin_2024 == pytest.approx(row.actual_margin_2024)


def test_the_reference_is_an_early_electorate_not_the_whole_state(tmp_path, nc_baseline):
    """`estimate.lean_vs_baseline` compares against the state; this does not."""
    counties = (_county(2024, 10, {MECKLENBURG: 1000})
                + _county(2022, 10, {MECKLENBURG: 1000}))
    row = cf.build(_tree(tmp_path, counties), nc_baseline)[0]
    assert row.reference_composition_margin == pytest.approx(
        cf.county_margin(nc_baseline.get(MECKLENBURG)))
    assert row.reference_composition_margin != pytest.approx(row.actual_margin_2024)


# --------------------------------------------------------------------------
# The dimensions that are reported but never priced
# --------------------------------------------------------------------------
def test_dims_used_is_county_and_only_county(nc_out, nc_baseline):
    for row in cf.build(nc_out, nc_baseline):
        assert row.dims_used == ("county",)
        assert "party" in row.dims_reported
        assert set(row.dims_used) < set(row.dims_reported)


def test_the_party_shift_is_reported_but_never_folded_into_the_margin(
        tmp_path, nc_baseline):
    """A registration point is not a presidential point, so it is not added in."""
    counties = (_county(2024, 10, {MECKLENBURG: 1000})
                + _county(2022, 10, {MECKLENBURG: 1000}))
    states = [_state(2024, 10, 1000, party_dem=900, party_rep=100),
              _state(2022, 10, 1000, party_dem=100, party_rep=900)]
    row = cf.build(_tree(tmp_path, counties, states), nc_baseline)[0]
    assert row.party_margin_shift_pp == pytest.approx(80.0 - -80.0)
    # An eighty-point registration swing moves the published margin by nothing.
    assert row.shift_pp == pytest.approx(0.0, abs=1e-9)
    assert "party" in row.dims_reported and "party" not in row.dims_used


def test_party_coverage_counts_only_the_buckets_the_state_reported(tmp_path,
                                                                   nc_baseline):
    counties = (_county(2024, 10, {MECKLENBURG: 1000})
                + _county(2022, 10, {MECKLENBURG: 1000}))
    states = [_state(2024, 10, 1000, party_dem=400, party_rep=400),
              _state(2022, 10, 1000, party_dem=300, party_rep=300, party_npa=400)]
    row = cf.build(_tree(tmp_path, counties, states), nc_baseline)[0]
    assert row.party_coverage == pytest.approx(0.8)
    assert row.reference_party_coverage == pytest.approx(1.0)


def test_no_party_reported_leaves_the_party_columns_blank(tmp_path, nc_baseline):
    counties = (_county(2024, 10, {MECKLENBURG: 1000})
                + _county(2022, 10, {MECKLENBURG: 1000}))
    row = cf.build(_tree(tmp_path, counties, [_state(2024, 10, 1000)]), nc_baseline)[0]
    assert row.party_margin_shift_pp is None
    assert row.to_dict()["party_margin_shift_pp"] == ""
    assert "party" not in row.dims_reported


def test_composition_distance_is_total_variation_in_points():
    assert cf.composition_distance({"a": 1, "b": 1}, {"a": 1, "b": 1}) == pytest.approx(0)
    assert cf.composition_distance({"a": 1, "b": 0}, {"a": 0, "b": 1}) == pytest.approx(100)
    # 60/40 against 40/60 is twenty points of total variation.
    assert cf.composition_distance({"a": 60, "b": 40},
                                   {"a": 40, "b": 60}) == pytest.approx(20)


def test_a_bucket_present_in_one_cycle_only_is_not_read_as_zero():
    """THE BLANK RULE. A schema change must not manufacture a composition shift."""
    assert cf.composition_distance({"a": 1, "b": 1}, {"a": 1}) is None
    assert cf.composition_distance({}, {"a": 1}) is None


def test_demographic_shifts_are_distances_and_never_reach_the_margin(nc_out,
                                                                     nc_baseline):
    rows = {r.days_to_election: r for r in cf.build(nc_out, nc_baseline)}
    row = rows[0]
    assert set(row.demo_shift_tv) == {"age", "race", "sex"}
    assert all(0 <= v <= 100 for v in row.demo_shift_tv.values())
    assert row.shift_pp == pytest.approx(
        row.composition_margin - row.reference_composition_margin)


# --------------------------------------------------------------------------
# Coverage, the band and confidence
# --------------------------------------------------------------------------
def test_complete_coverage_leaves_only_the_flat_model_error(tmp_path, nc_baseline):
    counties = (_county(2024, 10, {MECKLENBURG: 1000})
                + _county(2022, 10, {MECKLENBURG: 1000}))
    states = [_state(2024, 10, 1000), _state(2022, 10, 1000)]
    row = cf.build(_tree(tmp_path, counties, states), nc_baseline)[0]
    assert row.county_coverage == pytest.approx(1.0)
    assert (row.shift_hi - row.shift_lo) / 2 == pytest.approx(cf.MODEL_ERROR_PP)


def test_partial_coverage_widens_the_band(tmp_path, nc_baseline):
    """Half the state's ballots unaccounted for is a bound, not a point."""
    counties = (_county(2024, 10, {MECKLENBURG: 1000})
                + _county(2022, 10, {MECKLENBURG: 1000}))
    states = [_state(2024, 10, 2000), _state(2022, 10, 1000)]
    row = cf.build(_tree(tmp_path, counties, states), nc_baseline)[0]
    assert row.county_coverage == pytest.approx(0.5)
    assert (row.shift_hi - row.shift_lo) / 2 > cf.MODEL_ERROR_PP
    assert row.confidence == "low"


def test_confidence_is_never_high(nc_out, nc_baseline):
    """No amount of coverage repairs a method that does not beat the null."""
    for row in cf.build(nc_out, nc_baseline):
        assert row.confidence in ("low", "medium")


def test_a_thin_day_is_low_confidence(tmp_path, nc_baseline):
    counties = (_county(2024, 10, {MECKLENBURG: 8})
                + _county(2022, 10, {MECKLENBURG: 8}))
    row = cf.build(_tree(tmp_path, counties), nc_baseline)[0]
    assert row.ballots == 8
    assert row.confidence == "low"


def test_the_band_brackets_the_shift(nc_out, nc_baseline):
    for row in cf.build(nc_out, nc_baseline):
        assert row.shift_lo <= row.shift_pp <= row.shift_hi


# --------------------------------------------------------------------------
# The real North Carolina slice
# --------------------------------------------------------------------------
def test_north_carolina_2024_against_2022_is_pinned(nc_out, nc_baseline):
    """The real numbers, so a change in the arithmetic fails a test.

    All three days say the same thing: county geography moves about a point while
    the party registration of the very same ballots moves eleven to thirty.
    """
    rows = {r.days_to_election: r for r in cf.build(nc_out, nc_baseline)}
    assert sorted(rows) == [0, 10, 30]

    expected = {  # dte: (shift_pp, party_margin_shift_pp, age_shift_tv)
        30: (-0.77, -29.51, 8.63),
        10: (-1.41, -13.09, 12.60),
        0: (-1.25, -11.42, 12.55),
    }
    for dte, (shift, party, age) in expected.items():
        row = rows[dte]
        assert row.shift_pp == pytest.approx(shift, abs=0.01), dte
        assert row.party_margin_shift_pp == pytest.approx(party, abs=0.01), dte
        assert row.demo_shift_tv["age"] == pytest.approx(age, abs=0.01), dte
        # The geography sees a fraction of what the registration says moved.
        assert abs(row.shift_pp) < abs(row.party_margin_shift_pp) / 5

    final = rows[0]
    assert final.actual_margin_2024 == pytest.approx(-3.26, abs=0.01)
    assert final.implied_margin_2024 == pytest.approx(-4.51, abs=0.01)
    assert final.counties_used == final.reference_counties_used == 100


def test_the_fixture_validation_is_pinned(nc_out, nc_baseline):
    """One state, in sample, and it is the most flattering case in the data.

    North Carolina is one of only two state-cycles where county geography beats
    the no-change null at all. Scored alone it looks like the method works, which
    is exactly why the shipped verdict is taken from every state at once and why
    `held_out` is published on every row.
    """
    scores = cf.validate(nc_out, nc_baseline)
    assert len(scores) == 1
    got = scores[0]
    assert (got.cycle, got.state) == (2024, "NC")
    assert got.held_out is False          # no other state to fit a scale on
    assert got.fitted_scale is None
    assert got.mean_abs_error == pytest.approx(10.93, abs=0.05)
    assert got.null_mean_abs_error == pytest.approx(12.26, abs=0.05)
    assert got.mean_abs_shift == pytest.approx(1.33, abs=0.05)
    assert got.gain == pytest.approx(1.33, abs=0.05)
    printed = "\n".join(cf.format_validation(scores))
    assert "in sample: no other state to fit on" in printed


# --------------------------------------------------------------------------
# The protocol, and the finding
# --------------------------------------------------------------------------
def _scored(state: str, pairs: list[tuple[float, float]]) -> list[cf.ScoredDay]:
    return [
        cf.ScoredDay(cycle=2024, state=state, days_to_election=len(pairs) - i,
                     shift=shift, truth=truth,
                     ballots=1e6, reference_ballots=1e6)
        for i, (shift, truth) in enumerate(pairs)
    ]


def test_fit_scale_is_weighted_by_series_not_by_day():
    """Maine is 121 days and Kentucky is 3. Pooling by day would fit Maine."""
    long_series = _scored("ME", [(1.0, -1.0)] * 100)
    short_series = _scored("KY", [(1.0, 3.0)] * 2)
    got = cf.fit_scale([long_series, short_series])
    # Each series counts once, so the answer is the mean of -1 and +3.
    assert got == pytest.approx(1.0)


def test_fit_scale_returns_none_rather_than_a_number_when_nothing_is_fittable():
    assert cf.fit_scale([]) is None
    assert cf.fit_scale([_scored("XX", [(0.0, 5.0)])]) is None


def test_the_scale_is_fitted_leave_one_state_out(tmp_path, monkeypatch):
    """The fold that scores a state has never seen it.

    Built as a stub panel rather than from CSVs, because the thing under test is
    the PROTOCOL: docs/regression.md is this repo's standing example of a fitted
    constant that looked like a discovery until it was scored on data it had not
    trained on.
    """
    panel = {
        (2024, "AA"): _scored("AA", [(1.0, 10.0), (2.0, 20.0)]),
        (2024, "BB"): _scored("BB", [(1.0, -10.0), (2.0, -20.0)]),
    }
    monkeypatch.setattr(cf, "score_panel", lambda *a, **k: panel)
    scores = {r.state: r for r in cf.validate(tmp_path, None)}
    assert set(scores) == {"AA", "BB"}
    # AA's scale is fitted on BB alone (slope -10) and BB's on AA alone (+10).
    assert scores["AA"].fitted_scale == pytest.approx(-10.0)
    assert scores["BB"].fitted_scale == pytest.approx(+10.0)
    assert all(r.held_out for r in scores.values())
    # A scale fitted on the other state is catastrophic on this one, which is the
    # whole point of holding it out.
    assert scores["AA"].scaled_gain < 0
    assert scores["BB"].scaled_gain < 0


@pytest.mark.skipif(not (REPO_OUTPUT / "ev_state_daily.csv").exists(),
                    reason="no published output/ tree in this checkout")
def test_the_measured_gain_does_not_clear_the_bar(full_baseline):
    """THE FINDING. County geography does not beat "nothing changed".

    If a future backfill makes it clear MIN_GAIN, this fails and the
    recommendation in docs/counterfactual.md has to be revisited rather than
    inherited. That is the same guard `regress.py` keeps over its own verdict.
    """
    scores = cf.validate(REPO_OUTPUT, full_baseline)
    assert scores, "the published tree should still score at least one state"
    gain = sum(r.gain for r in scores) / len(scores)
    assert gain < cf.MIN_GAIN, (
        f"county geography now buys {gain:+.2f} pp over the no-change null; "
        "docs/counterfactual.md says it buys nothing and must be re-derived"
    )
    # And the shape of the failure: the method moves far less than the thing it
    # is standing in for.
    moved = sum(r.mean_abs_shift for r in scores) / len(scores)
    truth = sum(r.mean_abs_truth for r in scores) / len(scores)
    assert moved < truth / 3
    assert "NOTHING SHIPS" in "\n".join(cf.format_validation(scores))


@pytest.mark.skipif(not (REPO_OUTPUT / "ev_state_daily.csv").exists(),
                    reason="no published output/ tree in this checkout")
def test_the_fitted_scale_does_not_agree_with_itself_across_states(full_baseline):
    """A unit gap would be fixable by a scale. This is not a unit gap."""
    scores = [r for r in cf.validate(REPO_OUTPUT, full_baseline)
              if r.fitted_scale is not None]
    assert len(scores) >= 2
    assert min(r.fitted_scale for r in scores) < 0 < max(r.fitted_scale for r in scores)
    # Every fold is worse than doing nothing.
    assert all(r.scaled_gain < 0 for r in scores)


@pytest.mark.skipif(not (REPO_OUTPUT / "ev_state_daily.csv").exists(),
                    reason="no published output/ tree in this checkout")
def test_model_error_matches_the_measured_validation(full_baseline):
    """MODEL_ERROR_PP is a measurement, and it is refitted here rather than trusted."""
    scores = cf.validate(REPO_OUTPUT, full_baseline)
    measured = sum(r.mean_abs_error for r in scores) / len(scores)
    assert cf.MODEL_ERROR_PP == pytest.approx(measured, abs=1.0)
    assert cf.MODEL_ERROR_PP >= measured, "the band must not be narrower than the error"


# --------------------------------------------------------------------------
# Publishing
# --------------------------------------------------------------------------
def test_write_touches_only_counterfactual_csv(tmp_path, nc_baseline, nc_out):
    """A model number may never land in a column that means "the state said so"."""
    (nc_out / "party_estimate.csv").write_text("cycle,state\n2024,NC\n", encoding="utf-8")
    (nc_out / "results_state.csv").write_text("cycle,state\n2024,NC\n", encoding="utf-8")
    before = _hash_tree(nc_out)

    cf.write(nc_out, cf.build(nc_out, nc_baseline))

    after = _hash_tree(nc_out)
    assert {k for k in after if before.get(k) != after[k]} == {"counterfactual.csv"}
    assert set(before) - set(after) == set()


def test_the_published_table_never_carries_a_reported_column():
    forbidden = {"party_dem", "party_rep", "party_oth", "party_npa",
                 "ballots_total", "mail_returned", "inperson"}
    assert not forbidden & set(cf.COUNTERFACTUAL_COLUMNS)
    assert not cf.FORBIDDEN_COLUMNS & set(cf.COUNTERFACTUAL_COLUMNS)


def test_every_column_is_written_for_every_row(nc_out, nc_baseline):
    for row in cf.build(nc_out, nc_baseline):
        assert set(row.to_dict()) == set(cf.COUNTERFACTUAL_COLUMNS)


def test_rows_are_merged_on_cycle_state_date(tmp_path, nc_baseline, nc_out):
    cf.write(nc_out, cf.build(nc_out, nc_baseline))
    first = len(list(csv.DictReader((nc_out / "counterfactual.csv").open())))
    cf.write(nc_out, cf.build(nc_out, nc_baseline))
    second = list(csv.DictReader((nc_out / "counterfactual.csv").open()))
    assert len(second) == first
    keys = [(r["cycle"], r["state"], r["date"]) for r in second]
    assert len(set(keys)) == len(keys)


def test_the_written_file_says_which_dimensions_are_in_the_number(nc_out, nc_baseline):
    cf.write(nc_out, cf.build(nc_out, nc_baseline))
    rows = list(csv.DictReader((nc_out / "counterfactual.csv").open()))
    assert rows
    for row in rows:
        assert row["dims_used"] == "county"
        assert row["behaviour_cycle"] == "2024"
        assert row["method"] == cf.METHOD
        # The reference is always a previous EARLY electorate, named in the row.
        assert int(row["reference_cycle"]) < int(row["cycle"])
        assert row["reference_date"] and row["reference_days_to_election"]


# --------------------------------------------------------------------------
# The subcommand
# --------------------------------------------------------------------------
def test_the_cli_exposes_counterfactual_and_ingest_never_loads_it():
    """Same guarantee `estimate` and `regress` keep: the six-hourly job must not
    be able to publish a model number, so the module is imported inside the
    dispatcher and not at `cli` module scope."""
    import ev.cli as cli

    assert "counterfactual" not in dir(cli)
    parser = cli.build_parser()
    args = parser.parse_args(["counterfactual", "--dry-run"])
    assert args.dry_run and args.validate is False
    assert args.func.__module__ == "ev.cli"


def test_dry_run_writes_nothing(tmp_path, nc_out, capsys):
    import ev.cli as cli

    args = cli.build_parser().parse_args(
        ["--output", str(nc_out), "counterfactual", "--dry-run",
         "--baseline", str(NC_BASELINE)])
    assert args.func(args) == 0
    assert not (nc_out / "counterfactual.csv").exists()
    assert "DRY RUN" in capsys.readouterr().out


def test_an_empty_run_leaves_the_previous_file_alone(tmp_path, capsys):
    """Writing an empty table would replace real rows with nothing."""
    import ev.cli as cli

    out = _tree(tmp_path, _county(2026, 20, {MECKLENBURG: 10}))
    (out / "counterfactual.csv").write_text("cycle,state\n2024,NC\n", encoding="utf-8")
    args = cli.build_parser().parse_args(
        ["--output", str(out), "counterfactual", "--baseline", str(NC_BASELINE)])
    assert args.func(args) == 0
    assert (out / "counterfactual.csv").read_text(encoding="utf-8") == "cycle,state\n2024,NC\n"
    assert "left untouched" in capsys.readouterr().out


def test_counties_total_is_the_census_count(nc_out, nc_baseline):
    rows = cf.build(nc_out, nc_baseline)
    assert rows
    assert all(r.counties_total == len(_fips.CENSUS_COUNTIES["NC"]) for r in rows)
    assert rows[0].counties_total == 100
