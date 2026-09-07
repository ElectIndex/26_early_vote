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


#: Synthetic county counts are written in readable round numbers (900 against
#: 100) and then multiplied by this, because THE MATURITY GATE is real and a
#: fixture has to clear it: a reference curve under `MIN_REFERENCE_BALLOTS`
#: (50,000) is a stub the model refuses to measure against. Every quantity these
#: tests assert on -- the composition margin, the shift, the coverage -- is a
#: RATIO and is unchanged by the scaling, so the arithmetic stays as legible as
#: it was while the fixtures now travel the same path production does.
BALLOT_SCALE = 1_000


def _county(cycle, dte, ballots: dict[str, object], state="NC",
            scale: int = BALLOT_SCALE) -> list[dict]:
    return [
        {"cycle": cycle, "state": state, "county_fips": fips, "county_name": "",
         "date": (cf.election_date(cycle) - cf.timedelta(days=dte)).isoformat(),
         "days_to_election": dte,
         "ballots_total": "" if count is None else count * scale}
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


# --------------------------------------------------------------------------
# THE MATURITY GATE
#
# The model is validated on mature days and, until 2026-09-06, published on
# every day. 181 of the 260 rows in output/counterfactual.csv came from days
# outside the domain it had been scored on -- mean |shift| 6.4 points against
# 1.2 for the mature ones, up to 26.2, and 24 of them carrying the TOP
# confidence label because completeness was not one of the things confidence
# looked at. Maine published an 11.7-point shift computed off two ballots.
#
# Nothing in that is composition. It is phase: early in a window the returns
# are mail, and a mail electorate's geography is nothing like a finished one's.
# --------------------------------------------------------------------------
def test_completeness_is_measured_against_the_reference_cycles_final(nc_baseline):
    """⚠️ The denominator is the FINISHED curve, never the running one.

    This is the whole reason `completeness()` exists rather than reusing
    `mature_days()`. A rule that divides by "the largest this series has reached
    so far" declares day one complete, which is exactly what North Carolina's
    eight 2026 ballots do today: they are 100% of 2026 and 0.0002% of an
    electorate. The reference cycle's curve is over, so it can be the yardstick
    for both sides.
    """
    assert cf.completeness({"37119": 250_000}, 1_000_000) == pytest.approx(0.25)
    # Not capped: a cycle that outruns the last one reads above 100%, which is
    # true and worth seeing.
    assert cf.completeness({"37119": 2_000_000}, 1_000_000) == pytest.approx(2.0)
    # THE BLANK RULE on the read side: a county that reported nothing is absent
    # from the sum, not a zero in it.
    assert cf.completeness({"37119": 250_000, "37001": None}, 1_000_000) == pytest.approx(0.25)
    assert cf.completeness({"37119": 1}, 0) is None


def test_the_gate_refuses_a_reference_curve_that_is_a_stub(tmp_path, nc_baseline):
    """South Carolina's 2022 county series tops out at 16,975 ballots.

    Its 2024 one reaches 1,579,112. Thirteen rows were published off that
    comparison, and their "compositional shift" was the difference between a
    state and a rounding error.
    """
    counties = (_county(2024, 10, {MECKLENBURG: 900, ALAMANCE: 100})
                + _county(2022, 10, {MECKLENBURG: 10, ALAMANCE: 7}, scale=1))
    assert cf.build(_tree(tmp_path, counties), nc_baseline) == []


def test_the_gate_refuses_an_immature_reference_day(tmp_path, nc_baseline):
    """A reference day that is 1% of its own cycle is mail, not an electorate."""
    counties = (_county(2024, 10, {MECKLENBURG: 900, ALAMANCE: 100})
                + _county(2022, 10, {MECKLENBURG: 9, ALAMANCE: 1})
                + _county(2022, 0, {MECKLENBURG: 900, ALAMANCE: 100}))
    rows = cf.build(_tree(tmp_path, counties), nc_baseline)
    # d-10 in 2024 matches d-10 in 2022, which holds 1% of the 2022 curve.
    assert [r.days_to_election for r in rows] == []


def test_the_gate_refuses_a_current_day_that_has_barely_started(tmp_path, nc_baseline):
    """The live half. North Carolina's 2026 eight ballots must not get a row."""
    counties = (_county(2026, 10, {MECKLENBURG: 8}, scale=1)
                + _county(2024, 10, {MECKLENBURG: 900, ALAMANCE: 100}))
    assert cf.build(_tree(tmp_path, counties), nc_baseline) == []


def test_a_row_that_clears_the_gate_carries_the_two_shares_it_cleared_it_on(
    tmp_path, nc_baseline
):
    """Published, not just enforced -- a reader can check the gate themselves."""
    counties = (_county(2024, 10, {MECKLENBURG: 600, ALAMANCE: 400})
                + _county(2022, 10, {MECKLENBURG: 700, ALAMANCE: 300}))
    row = cf.build(_tree(tmp_path, counties), nc_baseline)[0]
    assert row.completeness == pytest.approx(1.0)
    assert row.reference_completeness == pytest.approx(1.0)
    assert row.to_dict()["completeness"] == "1.0000"


def test_the_published_table_never_carries_an_immature_row(full_baseline):
    """The regression that motivated all of this, checked against real output/."""
    rows = cf.build(REPO_OUTPUT, full_baseline)
    if not rows:
        pytest.skip("no published output/ tree in this checkout")
    assert all(r.completeness >= cf.MATURE_FRACTION for r in rows)
    assert all(r.reference_completeness >= cf.MATURE_FRACTION for r in rows)
    # ⚠️ THIS BOUND USED TO BE A FLAT 5.0, on the reasoning that every row above
    # five points was a phase artefact. Pennsylvania proved that was a fact about
    # the panel, not a law: PA publishes +10.48 on a day that clears both halves
    # of the gate with room to spare, and it is not immature -- it is just wrong,
    # by 22.6 points and in the wrong direction, and it is wrong by about that
    # much on every other day of PA's window too.
    #
    # So the bound that means something is the one tied to the model's own stated
    # error. The whole argument of docs/counterfactual.md is that the band IS the
    # number; a published shift larger than the band would be this model making a
    # claim, which is precisely what it has not earned.
    assert max(abs(r.shift_pp) for r in rows) < cf.MODEL_ERROR_PP


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


def test_2022_became_a_target_cycle_when_a_2020_reference_arrived(tmp_path,
                                                                 nc_baseline):
    """The rule never changed; the data did.

    This test used to read `assert 2022 not in cf.REFERENCE_CYCLE`, on the true
    statement that 2022 had no earlier early electorate in this repo to be
    compared against. On 2026-09-07 `pa.py` rebuilt Pennsylvania's 2020 mail
    curve from the application-level file and it does. So 2022 is a target, PA is
    the only state that can answer it, and every other state gets exactly the
    refusal a state with no reference series has always got -- which is what the
    second half of this test pins.

    It matters more than one extra fold. Every fold in the panel had been
    2024-vs-2022 -- ONE cycle transition -- which is the fact that disqualified
    the fitted constant. See `test_the_constant_does_not_survive_a_held_out_cycle`.
    """
    assert cf.REFERENCE_CYCLE[2022] == 2020
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
    # Different totals, identical 70/30 composition. The totals stay different
    # on purpose -- that is what shows the shift is scale-free -- but the smaller
    # side has to clear THE MATURITY GATE's 25% of the reference curve, so it is
    # 30% of it rather than the 10% this fixture used to carry.
    counties = (_county(2024, 10, {MECKLENBURG: 2100, ALAMANCE: 900})
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
    states = [_state(2024, 10, 2000 * BALLOT_SCALE),
              _state(2022, 10, 1000 * BALLOT_SCALE)]
    row = cf.build(_tree(tmp_path, counties, states), nc_baseline)[0]
    assert row.county_coverage == pytest.approx(0.5)
    assert (row.shift_hi - row.shift_lo) / 2 > cf.MODEL_ERROR_PP
    assert row.confidence == "low"


def test_confidence_is_never_high(nc_out, nc_baseline):
    """No amount of coverage repairs a method that does not beat the null."""
    for row in cf.build(nc_out, nc_baseline):
        assert row.confidence in ("low", "medium")


def test_a_thin_day_is_low_confidence(tmp_path, nc_baseline):
    """Thin and immature are different failures, and both are still checked.

    THE MATURITY GATE refuses a day that is a small SHARE of a finished curve;
    `confidence` still has to catch a day that is a fair share of a small one.
    20,000 ballots against a 60,000-ballot reference is a third of the way
    through -- comfortably mature -- and far too few ballots to characterise
    North Carolina.
    """
    counties = (_county(2024, 10, {MECKLENBURG: 20})
                + _county(2022, 10, {MECKLENBURG: 60}))
    row = cf.build(_tree(tmp_path, counties), nc_baseline)[0]
    assert row.ballots == 20 * BALLOT_SCALE
    assert row.ballots < cf.THIN_BALLOTS
    assert row.completeness == pytest.approx(1 / 3)
    assert row.confidence == "low"


def test_the_band_brackets_the_shift(nc_out, nc_baseline):
    for row in cf.build(nc_out, nc_baseline):
        assert row.shift_lo <= row.shift_pp <= row.shift_hi


# --------------------------------------------------------------------------
# The real North Carolina slice
# --------------------------------------------------------------------------
def test_north_carolina_2024_against_2022_is_pinned(nc_out, nc_baseline):
    """The real numbers, so a change in the arithmetic fails a test.

    Both days say the same thing: county geography moves about a point while the
    party registration of the very same ballots moves eleven to thirteen.

    The fixture carries a THIRD day, 30 days out, and it is deliberately not
    here. Its 2022 reference is 23,278 ballots -- 1.1% of that cycle's eventual
    2,187,856 -- and until THE MATURITY GATE landed this module published a
    -0.77 point "compositional shift" off it. A 1%-complete electorate is mail,
    and the difference between mail and a finished early electorate is phase,
    not composition. `test_the_gate_refuses_an_immature_reference_day` pins the
    refusal; this test pins that the row is gone.
    """
    rows = {r.days_to_election: r for r in cf.build(nc_out, nc_baseline)}
    assert sorted(rows) == [0, 10]

    expected = {  # dte: (shift_pp, party_margin_shift_pp, age_shift_tv)
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


# --------------------------------------------------------------------------
# THE REACH BOUND -- what county geography is arithmetically able to say
# --------------------------------------------------------------------------
def test_the_reach_bound_is_a_bound_and_it_is_tight(nc_baseline):
    """|shift| <= TV(county mix) x (widest county margin - narrowest), exactly.

    Two counties are the case where the bound is ATTAINED: every county gaining
    share is the most Democratic one and every county losing it the most
    Republican, which is what the bound is the supremum over.
    """
    dem, rep = MECKLENBURG, ALLEGHANY
    span = cf.county_margin(nc_baseline.get(dem)) - cf.county_margin(
        nc_baseline.get(rep))
    assert span > 0

    # A complete swap: TV = 1, so the bound is the whole span, and the shift is
    # the whole span too.
    now, ref = {dem: 100, rep: 0}, {dem: 0, rep: 100}
    assert cf.mix_distance(now, ref, nc_baseline) == pytest.approx(1.0)
    assert cf.margin_span(now, ref, nc_baseline) == pytest.approx(span)
    assert cf.reach_bound(now, ref, nc_baseline) == pytest.approx(span)
    shift = (cf.composition_margin(now, nc_baseline)[0]
             - cf.composition_margin(ref, nc_baseline)[0])
    assert shift == pytest.approx(span)

    # A ten-point move of share: TV = 0.10, and the shift is a tenth of the span.
    now, ref = {dem: 60, rep: 40}, {dem: 50, rep: 50}
    assert cf.mix_distance(now, ref, nc_baseline) == pytest.approx(0.10)
    bound = cf.reach_bound(now, ref, nc_baseline)
    assert bound == pytest.approx(0.10 * span)
    shift = (cf.composition_margin(now, nc_baseline)[0]
             - cf.composition_margin(ref, nc_baseline)[0])
    assert shift == pytest.approx(bound)

    # A third county in the middle takes share without moving the margin as far,
    # so the bound stops being attained -- but it is still a bound.
    now, ref = {dem: 50, ALAMANCE: 30, rep: 20}, {dem: 50, ALAMANCE: 20, rep: 30}
    bound = cf.reach_bound(now, ref, nc_baseline)
    shift = (cf.composition_margin(now, nc_baseline)[0]
             - cf.composition_margin(ref, nc_baseline)[0])
    assert 0 < abs(shift) < bound


def test_a_county_with_no_baseline_cannot_widen_the_reach(nc_baseline):
    """The bound's support is the counties that can carry weight, not the map."""
    now, ref = {MECKLENBURG: 60, ALLEGHANY: 40}, {MECKLENBURG: 50, ALLEGHANY: 50}
    plain = cf.reach_bound(now, ref, nc_baseline)
    with_stranger = cf.reach_bound({**now, "99999": 0}, {**ref, "99999": 0},
                                   nc_baseline)
    assert with_stranger == pytest.approx(plain)


def test_within_reach_is_measured_on_the_final_matched_day():
    """The same day `estimate.within_reach` uses: the formed early electorate."""
    reachable = [cf.ScoredDay(cycle=2024, state="AA", days_to_election=d,
                              shift=0.0, truth=truth, ballots=1e6,
                              reference_ballots=1e6, reach=10.0)
                 for d, truth in ((10, -30.0), (0, -4.0))]
    assert cf.within_reach(reachable) is True          # the last day is 4 < 10
    beyond = [cf.ScoredDay(cycle=2024, state="AA", days_to_election=d,
                           shift=0.0, truth=truth, ballots=1e6,
                           reference_ballots=1e6, reach=10.0)
              for d, truth in ((10, -4.0), (0, -30.0))]
    assert cf.within_reach(beyond) is False
    assert cf.within_reach([]) is False
    # No bound computable is not "in reach"; it is not a measurement at all.
    assert cf.within_reach([cf.ScoredDay(cycle=2024, state="AA",
                                         days_to_election=0, shift=0.0,
                                         truth=1.0, ballots=1e6,
                                         reference_ballots=1e6)]) is False


@pytest.mark.skipif(not (REPO_OUTPUT / "ev_state_daily.csv").exists(),
                    reason="no published output/ tree in this checkout")
def test_the_series_that_came_into_reach_is_the_one_that_points_the_wrong_way(
        full_baseline):
    """THE FINDING, and the case it said would have to be reported if it arrived.

    This test used to assert that EVERY series whose composition demonstrably
    moved was one county geography could not have reported under any reweighting
    of its counties, and its docstring said: "a future backfill that puts a MOVING
    state in reach" should fail it, and "if that ever happens, that series is the
    first honest measurement of how well this method estimates a real
    compositional change and docs/counterfactual.md has to say so."

    It happened, on 2026-09-07, when Pennsylvania's 2020 curve made 2022 a target
    cycle. PA 2022-vs-2020 moved 5.57 registration points against a reach bound of
    6.28: in range, and not quiet. And the honest measurement is worse than the
    out-of-reach ones. The model reports -3.11 where the truth is +5.57 -- wrong
    by 8.68 and pointing the OPPOSITE WAY -- for a gain of -4.73, the worst fold
    in the panel, agreeing in sign on 7% of its days.

    So the finding is not weakened by a series coming into reach; it is sharpened.
    Being arithmetically able to report an answer is not the same as reporting it,
    and this is the one series where the two can be told apart.
    """
    scores = cf.validate(REPO_OUTPUT, full_baseline)
    assert scores
    assert all(r.reach is not None for r in scores)
    moved = [r for r in scores if abs(r.final_truth) >= 4.0]
    assert moved, "the published tree should still hold a state that moved"

    reachable = [r for r in moved if r.in_range]
    assert reachable, (
        "no moving series is in reach any more; this test's whole subject is gone "
        "and docs/counterfactual.md has to be re-derived"
    )
    # Being in reach buys nothing: every one of them still fails, and they fail
    # at least as badly as the series that were out of reach.
    for r in reachable:
        assert r.gain < cf.MIN_GAIN, f"{r.state} {r.cycle} now clears the bar"
    assert min(r.gain for r in reachable) <= min(r.gain for r in moved), (
        "the in-reach series are no longer among the worst folds in the panel"
    )
    # And the bound is still a bound: no scored day's modelled shift exceeds it.
    for days in cf.score_panel(REPO_OUTPUT, full_baseline).values():
        for day in days:
            assert abs(day.shift) <= day.reach + 1e-9


@pytest.mark.skipif(not (REPO_OUTPUT / "ev_state_daily.csv").exists(),
                    reason="no published output/ tree in this checkout")
def test_the_constant_does_not_survive_a_held_out_cycle(full_baseline):
    """THE TEST THE CONSTANT HAS NEVER BEEN ABLE TO TAKE, and it fails it.

    A fitted constant -- "this state's early electorate moved by whatever the
    other states' did" -- scores +3.49 pp leave-one-STATE-out, three and a half
    times MIN_GAIN and better than every predictor ever swept on this panel. It
    was refused anyway, on the argument that all seven folds were the same cycle
    transition, so leave-one-state-out never held the transition out and the
    constant was being scored on the thing it was fitted to.

    Pennsylvania's 2020 curve turned that argument into a measurement. The two
    transitions point OPPOSITE ways -- PA's early electorate moved +5.57
    registration points from 2020 to 2022 and -27.64 from 2022 to 2024 -- so the
    constant learned from one is worse than useless on the other.
    """
    scores = cf.validate(REPO_OUTPUT, full_baseline)
    held = [r for r in scores if r.cycle_constant_gain is not None]
    assert held, (
        "the panel holds one cycle transition again, so the constant cannot be "
        "held out; docs/counterfactual.md's refusal is back to being an argument"
    )
    assert {r.cycle for r in scores} > {2024}, "expected more than one transition"
    gain = sum(r.cycle_constant_gain for r in held) / len(held)
    assert gain < 0, (
        f"a constant now survives a held-out cycle at {gain:+.2f} pp; that is the "
        "one objection docs/counterfactual.md rests its refusal on"
    )
    # Every fold, not just on average.
    assert all(r.cycle_constant_gain < cf.MIN_GAIN for r in held)
    printed = "\n".join(cf.format_validation(scores))
    assert "leave-one-CYCLE-out constant" in printed


def test_reach_is_reported_and_is_never_a_filter():
    """⚠️ THE TRAP THIS TEST EXISTS TO KEEP SHUT.

    `estimate.format_validation` drops its out-of-reach series from the mean and
    falls back to keeping everything when that would empty the list. The same
    rule was written here, and on 2026-09-07 Colorado arrived as the one in-reach
    fold and switched the fallback off: the headline silently became Colorado's
    own 2.29 over a single series while the all-fold mean was 10.39.

    `in_range` here is a condition ON THE TRUTH, so filtering on it selects the
    folds whose composition barely moved. The headline averages every scored
    series and says so; the bound is a diagnostic beside it.
    """
    def series(state, truth, reach):
        return cf.ScoredDay(cycle=2024, state=state, days_to_election=0,
                            shift=0.0, truth=truth, ballots=1e6,
                            reference_ballots=1e6, reach=reach)

    quiet = cf.Validation(
        cycle=2024, state="QQ", days=1, final_shift=0.0, final_truth=-2.0,
        final_error=2.0, mean_abs_error=2.0, null_mean_abs_error=2.0,
        mean_abs_shift=0.0, mean_abs_truth=2.0, correlation=None,
        sign_agreement=0.0, reach=9.0, in_range=True)
    loud = cf.Validation(
        cycle=2024, state="ZZ", days=1, final_shift=0.0, final_truth=-30.0,
        final_error=30.0, mean_abs_error=30.0, null_mean_abs_error=30.0,
        mean_abs_shift=0.0, mean_abs_truth=30.0, correlation=None,
        sign_agreement=0.0, reach=4.0, in_range=False)
    assert cf.within_reach([series("QQ", -2.0, 9.0)]) is True
    assert cf.within_reach([series("ZZ", -30.0, 4.0)]) is False

    printed = "\n".join(cf.format_validation([quiet, loud]))
    # The mean is over BOTH, not over the one series that was in reach.
    assert "mean MAE = 16.00 pp" in printed
    assert "REACH: 1 of 2 series" in printed
    assert "still\nAVERAGED" in printed or "still AVERAGED" in printed


# --------------------------------------------------------------------------
# Inside the counties: the reach bound's exact twin
# --------------------------------------------------------------------------
def test_the_mix_split_is_exact(nc_out, nc_baseline):
    """between + within reproduces the measured change to floating-point noise.

    `reach_bound` is an inequality that needs no ground truth. `mix_split` is the
    matching EQUALITY that does: it takes the truth column apart into the part
    the county mix moved and the part that moved between voters of the same
    county, and the two sum to the measured change exactly.
    """
    # Both counties sit at +20 and -20 registration margin on both days; only the
    # weights move, from 2:7 to 1:1, so the whole change is the mix.
    now = {MECKLENBURG: (60, 40), ALLEGHANY: (40, 60)}
    ref = {MECKLENBURG: (30, 20), ALLEGHANY: (70, 105)}
    between, within = cf.mix_split(now, ref)
    assert within == pytest.approx(0.0, abs=1e-9)
    assert between == pytest.approx(100.0 / 9, abs=1e-9)

    # And one where every point of it is INSIDE the counties: the shares are
    # identical and only the registration behind them moved.
    now = {MECKLENBURG: (60, 40), ALLEGHANY: (60, 40)}
    ref = {MECKLENBURG: (50, 50), ALLEGHANY: (50, 50)}
    between, within = cf.mix_split(now, ref)
    assert between == pytest.approx(0.0, abs=1e-9)
    assert within == pytest.approx(20.0, abs=1e-9)

    # On the real North Carolina slice the identity holds against the STATE's own
    # reported party split, which is a different file from the county one.
    for day in cf.score_panel(nc_out, nc_baseline)[(2024, "NC")]:
        assert day.mix_only + day.inside == pytest.approx(day.truth, abs=1e-6)


def test_a_county_reporting_party_on_one_side_only_is_dropped(nc_baseline):
    """THE BLANK RULE, and it is also what keeps the identity exact.

    Both terms have to be summed over one common support or they stop adding up
    to the measured change. A county the reference cycle never gave a party split
    for is not a county with no Democrats.
    """
    now = {MECKLENBURG: (60, 40), ALAMANCE: (10, 90)}
    ref = {MECKLENBURG: (50, 50)}
    between, within = cf.mix_split(now, ref)
    # Alamance is dropped, so this is Mecklenburg against itself: no mix change.
    assert between == pytest.approx(0.0, abs=1e-9)
    assert within == pytest.approx(20.0, abs=1e-9)
    assert cf.mix_split({}, ref) is None
    assert cf.mix_split(now, {}) is None


def test_the_north_carolina_split_is_pinned(nc_out, nc_baseline):
    """The real numbers, in the truth's own unit.

    County geography reports -1.25 points on the final day. Give the SAME county
    mix change the counties' own registration margins instead of their
    presidential ones -- removing the unit gap that the fitted scale exists to
    absorb -- and it reports -1.85 against a measured -11.42. The remaining
    -9.57 happened between voters of the same county.
    """
    got = cf.validate(nc_out, nc_baseline)[0]
    assert got.final_mix_only == pytest.approx(-1.85, abs=0.01)
    assert got.final_inside == pytest.approx(-9.57, abs=0.01)
    assert got.inside_share == pytest.approx(0.84, abs=0.01)
    # Removing the unit gap does not rescue it: 2.00 points on a bar of 1.00, in
    # the single most flattering state in the panel and in sample.
    assert got.mix_gain == pytest.approx(2.00, abs=0.05)
    printed = "\n".join(cf.format_validation([got]))
    assert "inside counties: NC2024 84%" in printed


@pytest.mark.skipif(not (REPO_OUTPUT / "ev_state_daily.csv").exists(),
                    reason="no published output/ tree in this checkout")
def test_the_measured_change_happened_inside_the_counties(full_baseline):
    """THE FINDING, tighter than the reach bound and in the truth's own unit.

    `reach_bound` says county geography COULD NOT have reported these moves. This
    says what actually carried them: on every scoreable series, the great majority
    of the measured compositional change happened between voters of the SAME
    county. Pennsylvania 2024 is 96% -- -26.54 of -27.64 -- which is the answer to
    "why is PA the worst fold": it is not geography, and nothing keyed on geography
    can reach it.

    Pinned as a floor on every series rather than as eight numbers, so a backfill
    that adds a state does not fail it and a state whose change really was
    geographic does.
    """
    scores = cf.validate(REPO_OUTPUT, full_baseline)
    split = [r for r in scores if r.inside_share is not None]
    assert split, "the published tree should still hold a county party split"
    assert all(r.inside_share > 0.5 for r in split), (
        "a series' compositional change is now mostly BETWEEN counties: "
        + ", ".join(f"{r.state} {r.cycle} {r.inside_share:.0%} inside"
                    for r in split if r.inside_share <= 0.5)
    )


@pytest.mark.skipif(not (REPO_OUTPUT / "ev_state_daily.csv").exists(),
                    reason="no published output/ tree in this checkout")
def test_closing_the_unit_gap_does_not_clear_the_bar_either(full_baseline):
    """The one excuse the reach bound could not close, closed.

    `fit_scale` exists because a registration point is not a presidential point,
    and a signal in the wrong unit would be fixed by one number. `mix_only` needs
    no number: it is `shift`'s own construction -- the same county mix change over
    the same counties -- valued in registration points directly. On the seven
    2024-vs-2022 folds it bought +0.30, and +0.03 with North Carolina dropped;
    with Pennsylvania's second transition in the panel it is -0.05.
    """
    scores = [r for r in cf.validate(REPO_OUTPUT, full_baseline)
              if r.mix_gain is not None]
    assert len(scores) >= 2
    gain = sum(r.mix_gain for r in scores) / len(scores)
    assert gain < cf.MIN_GAIN, (
        f"the county mix in the truth's own unit now buys {gain:+.2f} pp; "
        "docs/counterfactual.md says the dimension is blind and must be re-derived"
    )
    # And it does not survive dropping a state either, which is the check that
    # turned the flow specification's +1.59 into +0.27.
    worst = min(
        sum(r.mix_gain for r in scores if r.state != drop)
        / len([r for r in scores if r.state != drop])
        for drop in {r.state for r in scores}
    )
    assert worst < cf.MIN_GAIN


def test_the_split_is_reported_and_is_never_a_filter():
    """The same trap `test_reach_is_reported_and_is_never_a_filter` keeps shut.

    `inside_share` is a condition on the TRUTH, exactly like `in_range`, so a rule
    that dropped the series whose change was mostly within-county would select the
    series whose change was geographic -- which is to say the quiet ones. The
    headline is the published method's `gain` over every scored series and neither
    `mix_gain` nor `inside_share` may touch it.
    """
    geographic = cf.Validation(
        cycle=2024, state="QQ", days=1, final_shift=0.0, final_truth=-2.0,
        final_error=2.0, mean_abs_error=2.0, null_mean_abs_error=2.0,
        mean_abs_shift=0.0, mean_abs_truth=2.0, correlation=None,
        sign_agreement=0.0, reach=9.0, in_range=True,
        final_mix_only=-1.8, final_inside=-0.2,
        mean_abs_mix_only=1.8, mix_mean_abs_error=0.2)
    inside = cf.Validation(
        cycle=2024, state="ZZ", days=1, final_shift=0.0, final_truth=-30.0,
        final_error=30.0, mean_abs_error=30.0, null_mean_abs_error=30.0,
        mean_abs_shift=0.0, mean_abs_truth=30.0, correlation=None,
        sign_agreement=0.0, reach=4.0, in_range=False,
        final_mix_only=-0.5, final_inside=-29.5,
        mean_abs_mix_only=0.5, mix_mean_abs_error=29.5)
    assert geographic.inside_share == pytest.approx(0.10)
    assert inside.inside_share == pytest.approx(29.5 / 30.0)

    printed = "\n".join(cf.format_validation([geographic, inside]))
    # The mean is over BOTH, and it is the PUBLISHED method's, not mix_only's.
    assert "mean MAE = 16.00 pp" in printed
    assert "gain = +0.00 pp" in printed
    assert "inside counties: QQ2024 10%, ZZ2024 98%" in printed
    assert "buys +1.15 pp over the null" in printed
    assert "NOTHING SHIPS" in printed


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
    """A unit gap would be fixable by a scale. This is not a unit gap.

    ⚠️ THIS ARGUMENT HAS NOW BEEN WRONG IN BOTH DIRECTIONS, which is the most
    useful thing about it. Originally the multiplier fitting Kentucky and
    Maryland was the NEGATIVE of the one fitting Maine and North Carolina. The
    maturity gate made every fold's multiplier positive, and this docstring duly
    recorded that the sign half of the argument had been noise. Adding
    Pennsylvania put the signs back: FL -0.17, KY +0.40, MD +0.58, ME -0.92,
    NC -1.01, PA +2.62.

    Read that as a warning about the panel rather than a discovery about the
    model. Five folds were few enough that the sign pattern could flip on one
    state either way; what has never flipped is the magnitude. The scales still
    disagree by more than an order of magnitude (0.17 in Florida against 2.62 in
    Pennsylvania), and applying the one fitted on the other states still lifts no
    fold over MIN_GAIN. A signal in the wrong unit would be fixed by ONE number;
    nothing here is one number, in either sign.
    """
    scores = [r for r in cf.validate(REPO_OUTPUT, full_baseline)
              if r.fitted_scale is not None]
    assert len(scores) >= 2
    scales = [abs(r.fitted_scale) for r in scores]
    assert max(scales) / min(scales) > 10
    assert sum(r.scaled_gain for r in scores) / len(scores) < 0
    assert all(r.scaled_gain < cf.MIN_GAIN for r in scores)


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


# --------------------------------------------------------------------------
# THE DIMENSIONS THAT VARY *INSIDE* A COUNTY
#
# `mix_split` proved the county dimension empty: 84% to 135% of every measured
# compositional change happened between voters of the same county. These six pin
# what happened when the three within-county dimensions this tracker collects
# were measured against that gap, and the answer was that none of them can carry
# it. They fail loudly if a future backfill changes any of it.
# --------------------------------------------------------------------------
def test_the_nested_split_is_exact():
    """`mix_split` with a level of geography inserted UNDER the county.

    Three hand-built pairs where the whole move is known to be in one term, so
    each term is pinned on its own rather than only in aggregate.
    """
    group = lambda unit: unit[0]           # noqa: E731 -- "a1" and "a2" are group a

    # 1. The whole move is BETWEEN groups: group a doubles, nothing else changes.
    ref = {"a1": (80, 20), "b1": (20, 80)}
    now = {"a1": (160, 40), "b1": (20, 80)}
    between_groups, between_units, within_units = cf.nested_split(now, ref, group)
    assert between_groups == pytest.approx(20.0)
    assert between_units == pytest.approx(0.0)
    assert within_units == pytest.approx(0.0)

    # 2. The whole move is BETWEEN UNITS OF ONE GROUP: the group's own share is
    #    unchanged at 1.0, so no county-level method could see any of it.
    ref = {"a1": (80, 20), "a2": (20, 80)}
    now = {"a1": (160, 40), "a2": (20, 80)}
    between_groups, between_units, within_units = cf.nested_split(now, ref, group)
    assert between_groups == pytest.approx(0.0)
    assert between_units == pytest.approx(20.0)
    assert within_units == pytest.approx(0.0)

    # 3. The whole move is INSIDE a unit: same units, same weights, different
    #    registrants. Neither level of geography can see it.
    ref = {"a1": (80, 20), "a2": (20, 80)}
    now = {"a1": (50, 50), "a2": (20, 80)}
    between_groups, between_units, within_units = cf.nested_split(now, ref, group)
    assert between_groups == pytest.approx(0.0)
    assert between_units == pytest.approx(0.0)
    assert within_units == pytest.approx(-30.0)


def test_a_unit_reported_on_one_side_only_is_dropped_from_the_nested_split():
    """THE BLANK RULE, and what keeps all three terms over one common support."""
    group = lambda unit: unit[0]           # noqa: E731
    ref = {"a1": (80, 20), "a2": (20, 80)}
    now = {"a1": (80, 20), "a2": (20, 80), "a3": (900, 100)}
    assert cf.nested_split(now, ref, group) == pytest.approx((0.0, 0.0, 0.0))
    assert cf.nested_split({"a9": (1, 1)}, ref, group) is None


@pytest.mark.skipif(not (REPO_OUTPUT / "towns" / "me.csv").exists(),
                    reason="no published town table in this checkout")
def test_finer_geography_does_not_rescue_the_county_dimension(full_baseline):
    """THE ANSWER TO "IS THE COUNTY PARTITION SIMPLY TOO COARSE?", AND IT IS NO.

    Maine is the only state in this repo with a sub-county unit carrying party
    registration -- 463 towns inside 16 counties, twenty-nine times finer than
    the county partition, in the most town-fragmented state in the country. If
    the county dimension were failing because counties are large and mixed, a
    town-level split would recover the missing movement. It recovers 0.94 points
    of a 13.99-point move, taking geography from 5% of the answer to 12%, and
    leaves 87% inside individual Maine towns.

    Pinned as a ceiling on every scored day rather than as one number, so a
    backfill that lengthens the series does not fail it and a state whose change
    really was sub-county geography does.
    """
    panel = cf.score_panel(REPO_OUTPUT, full_baseline, states=["ME"])
    days = panel.get((2024, "ME"))
    assert days, "Maine's 2024 fold should still be scoreable"
    series = cf.read_series(REPO_OUTPUT, "ME")
    now, reference = series[2024], series[2022]
    seen = 0
    for day in days:
        ref_dte = reference.at(day.days_to_election, cf.DTE_MATCH_TOLERANCE)
        split = cf.nested_split(now.town_party.get(day.days_to_election, {}),
                                reference.town_party.get(ref_dte, {}),
                                lambda geoid: geoid[:5])
        if split is None:
            continue
        seen += 1
        between_groups, between_units, within_units = split
        measured = between_groups + between_units + within_units
        geography = abs(between_groups + between_units)
        assert geography < 0.25 * abs(measured), (
            f"ME {day.days_to_election} days out: geography now carries "
            f"{geography:.2f} of {measured:.2f} -- the town dimension has started "
            f"to see the change, and docs/counterfactual.md has to be revisited")
        # The finer level is bigger than the county level and still nowhere near.
        assert abs(within_units) > 3 * geography
    assert seen >= 15, "Maine's town series should still cover its scored days"


@pytest.mark.skipif(not (REPO_OUTPUT / "ev_state_daily.csv").exists(),
                    reason="no published output/ tree in this checkout")
def test_the_method_mix_is_frozen_where_the_finding_lives(full_baseline):
    """THE MAIL/IN-PERSON DIMENSION DOES NOT EXIST IN PENNSYLVANIA.

    docs/counterfactual.md attributes PA 2024's -27.64 to "the same voters, in
    the same places, choosing a different channel", which makes the method split
    the obvious next dimension to try. It cannot be tried there: Pennsylvania has
    no early in-person voting at all, so its observed early electorate is 100%
    mail in 2020, 2022 and 2024 and the mix moves EXACTLY zero. The channel those
    voters switched from was ELECTION DAY, which this tracker does not observe.

    Maryland reaches the same place for a lesser reason -- it publishes only its
    in-person centres and its mail file is served as a corrupt zip -- so three of
    the eight folds have no method dimension to look at, and the panel's largest
    measured change is two of them. Only Pennsylvania's zero is asserted here,
    because only Pennsylvania's is structural.
    """
    panel = cf.score_panel(REPO_OUTPUT, full_baseline)
    frozen = []
    for (cycle, state), days in panel.items():
        series = cf.read_series(REPO_OUTPUT, state)
        reference = series[cf.REFERENCE_CYCLE[cycle]]
        distances = [
            cf.method_mix_distance(
                series[cycle].state_rows.get(day.days_to_election),
                reference.state_rows.get(
                    reference.at(day.days_to_election, cf.DTE_MATCH_TOLERANCE)))
            for day in days
        ]
        if distances and all(d == 0.0 for d in distances if d is not None):
            frozen.append((cycle, state))
    assert (2024, "PA") in frozen and (2022, "PA") in frozen, (
        "Pennsylvania's method mix is no longer frozen -- it has gained an "
        "in-person early channel, and the method dimension is worth re-measuring")
    biggest = max(
        (max(days, key=lambda d: abs(d.truth)) for days in panel.values()),
        key=lambda d: abs(d.truth))
    assert (biggest.cycle, biggest.state) in frozen, (
        f"the panel's largest measured change is now {biggest.state} "
        f"{biggest.cycle}, which HAS a method dimension -- re-measure it")


@pytest.mark.skipif(not (REPO_OUTPUT / "ev_state_daily.csv").exists(),
                    reason="no published output/ tree in this checkout")
def test_no_within_county_dimension_covers_the_panel(full_baseline):
    """THE DISQUALIFICATION THIS REPO APPLIES TO EVERY THIN TERM.

    A term available only where the data is richest is not a term either model
    can use -- docs/party-estimate.md names that pattern five times. Counted over
    the folds the counterfactual can actually be scored on, every within-county
    dimension is thinner than the county one, which is present in all of them:

        method mix that MOVES at all   5 of 8   (frozen in PA 2022, PA 2024, MD)
        age, race                      1 of 8   (North Carolina)
        sex                            2 of 8   (North Carolina, Maryland)
        sub-county geography           1 of 8   (Maine)

    If a backfill ever makes one of them cover the panel, this fails and the
    dimension is worth building on rather than only measuring.
    """
    panel = cf.score_panel(REPO_OUTPUT, full_baseline)
    folds = len(panel)
    assert folds >= 8, "the panel should still hold every scoreable fold"
    moving_method = demo = towns = 0
    for (cycle, state), days in panel.items():
        series = cf.read_series(REPO_OUTPUT, state)
        now, reference = series[cycle], series[cf.REFERENCE_CYCLE[cycle]]
        matched = [(d.days_to_election,
                    reference.at(d.days_to_election, cf.DTE_MATCH_TOLERANCE))
                   for d in days]
        if any((cf.method_mix_distance(now.state_rows.get(a),
                                       reference.state_rows.get(b)) or 0.0) > 0
               for a, b in matched):
            moving_method += 1
        if any(cf.composition_distance(now.demo.get(a, {}).get(dim, {}),
                                       reference.demo.get(b, {}).get(dim, {}))
               is not None
               for a, b in matched for dim in cf.DEMO_DIMENSIONS):
            demo += 1
        if any(now.town_party.get(a) and reference.town_party.get(b)
               for a, b in matched):
            towns += 1
    assert moving_method <= folds - 3, (
        f"the method mix now moves in {moving_method} of {folds} folds")
    assert demo <= folds // 2, f"demographics now cover {demo} of {folds} folds"
    assert towns <= folds // 2, f"towns now cover {towns} of {folds} folds"


@pytest.mark.skipif(not (REPO_OUTPUT / "ev_state_daily.csv").exists(),
                    reason="no published output/ tree in this checkout")
def test_the_unpriced_dimensions_cannot_reach_the_largest_changes(full_baseline):
    """`required_span`, and the ceiling that makes an unpriced dimension answerable.

    Neither the method split nor `output/demo/*.csv` carries a party split, so
    the between-band term cannot be computed -- but it is bounded by
    TV(band mix) x (band-margin span), and a registration margin lives in
    [-100, +100], so no span can exceed 200. Turn that round and each fold names
    the span its dimension would need. On the final matched day the method
    dimension needs an infinite one in PA 2022, PA 2024 and MD (the mix does not
    move), 752 in NC, 169 in KY, 124 in CO, 111 in ME and 28 in FL -- against a
    mail-minus-in-person registration gap of 18 to 42 points measured directly in
    the five series whose window opens mail-only.

    Asserted on the folds whose measured change is large, because those are the
    ones the dimension would have to explain.
    """
    panel = cf.score_panel(REPO_OUTPUT, full_baseline)
    WIDEST_OBSERVED_CHANNEL_GAP = 42.0
    for (cycle, state), days in sorted(panel.items()):
        day = min(days, key=lambda d: d.days_to_election)
        if abs(day.truth) < 10.0:
            continue                       # CO, FL and MD: little happened to explain
        series = cf.read_series(REPO_OUTPUT, state)
        reference = series[cf.REFERENCE_CYCLE[cycle]]
        distance = cf.method_mix_distance(
            series[cycle].state_rows.get(day.days_to_election),
            reference.state_rows.get(
                reference.at(day.days_to_election, cf.DTE_MATCH_TOLERANCE)))
        needed = cf.required_span(distance, day.truth)
        assert needed is None or needed > WIDEST_OBSERVED_CHANNEL_GAP, (
            f"{state} {cycle}: the method mix moved {distance:.2f} points and "
            f"would need only a {needed:.0f}-point channel gap to carry "
            f"{day.truth:+.2f} -- inside what this repo can observe, so the "
            f"dimension is worth pricing")
