"""The party estimate: its arithmetic, its honesty columns, and its error.

Two of these tests are worth more than the rest put together:

* `test_write_never_touches_the_reported_party_columns` — the estimate is a
  model, and the moment a model number lands in `party_dem` the published data
  stops meaning "the state reported this". That guarantee is the whole pipeline.

* `test_ground_truth_north_carolina_2024` — pins the MEASURED error against a
  state that does report party, and pins how little the county weighting adds
  over simply quoting the state's 2024 presidential result. Those two numbers
  are the argument for or against shipping this feature, so they belong in a
  test that fails when they move.
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
                           {MECKLENBURG: 600, ALAMANCE: 200}, baseline)
    assert row.est_dem_hi - row.est_dem_lo == pytest.approx(2 * est.MODEL_ERROR)
    assert row.est_dem_lo < row.est_dem_share < row.est_dem_hi


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


def test_band_never_leaves_zero_to_one(baseline):
    row = est.estimate_day(2026, "NC", date(2026, 10, 20),
                           {ALLEGHANY: 100}, baseline)
    assert 0.0 <= row.est_dem_lo <= row.est_dem_hi <= 1.0


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

    North Carolina is the method's best case: every county reports, the file is
    voter-level, and the state's registration split happens to sit close to its
    presidential split. Even here the county weighting buys under a point over
    the null model of quoting the state's 2024 result and calling it an estimate
    -- and the estimate barely moves across the window while the real party mix
    moves points. Both numbers are pinned deliberately; if they change, the
    claims in docs/party-estimate.md are stale.
    """
    out = _out_dir(tmp_path)
    results = est.validate(out, baseline)
    assert len(results) == 1
    nc = results[0]

    assert (nc.cycle, nc.state) == (2024, "NC")
    # Final-day error on the Democratic two-party share, in percentage points.
    assert nc.final_error == pytest.approx(-0.7, abs=0.3)
    assert abs(nc.final_error) < 3.0

    # The null model: quote North Carolina's 2024 presidential result and stop.
    assert nc.gain < 1.0, "county weighting is claiming more than it earns"
    assert nc.null_mean_abs_error < 2.0

    # The estimate is near-static; the thing it claims to measure is not.
    assert nc.est_range < nc.truth_range


def test_validate_ignores_state_days_with_no_reported_party(tmp_path, baseline):
    """Nothing to score against means no score, not a zero error."""
    out = tmp_path / "output"
    (out / "counties").mkdir(parents=True)
    shutil.copy(FIXTURES / "nc_counties_2024.csv", out / "counties" / "nc.csv")
    assert est.validate(out, baseline) == []


def test_format_validation_says_so_when_there_is_nothing_to_score():
    assert any("no state-cycle" in line for line in est.format_validation([]))
