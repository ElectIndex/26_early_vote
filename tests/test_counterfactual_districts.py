"""The district counterfactual: a companion to the state row, and nothing more.

The things worth pinning:

* the identity -- weight every segment by its own 2024 vote and a district's
  composition margin IS its 2024 margin, so the shift is a composition
  difference and not an artefact;
* a district row exists only where a state row does, carries the state's
  confidence, and its band is the state's own half-width;
* a district none of whose counties has reported gets no row, never a zero;
* `write` touches one file and that file carries no reported-party column.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from ev import counterfactual as cf
from ev import counterfactual_districts as cfd

FIXTURES = Path(__file__).parent / "fixtures" / "counterfactual"
NC_BASELINE = FIXTURES / "county_results_2024_nc.csv"


# --------------------------------------------------------------------------
# A two-district toy state: county A is wholly in D-01, county B is split
# 60/40 between D-01 and D-02, county C is wholly in D-02.
# --------------------------------------------------------------------------
A, B, C = "37001", "37003", "37005"
SEGS = {"NC": {
    "NC-01": [cfd.Segment(A, 1.0, 600, 400),       # D+20
              cfd.Segment(B, 0.6, 300, 300)],      # even
    "NC-02": [cfd.Segment(B, 0.4, 200, 200),       # even
              cfd.Segment(C, 1.0, 100, 900)],      # R+80
}}


def test_the_identity_the_shift_rests_on():
    # Ballots = each county's full 2024 two-party vote -> the composition
    # margin reproduces the district's own 2024 margin exactly.
    ballots = {A: 1000, B: 1000, C: 1000}
    for segs in SEGS["NC"].values():
        margin, _, _ = cfd.district_composition(ballots, segs)
        assert margin == pytest.approx(cfd.district_actual(segs))
    assert cfd.district_actual(SEGS["NC"]["NC-01"]) == pytest.approx((900 - 700) / 1600 * 100)


def test_the_real_segments_reproduce_each_states_certified_margin_closely():
    # The crosswalk's segment votes are block-level sums, not certified
    # returns; summed to a state they must still land within a point of the
    # certified two-party margin the state counterfactual anchors on.
    baseline = cf.load_baseline()
    segments = cfd.load_segments()
    for state in ("NC", "PA", "GA", "AZ", "MI", "WI"):
        all_segments = [s for segs in segments[state].values() for s in segs]
        got = cfd.district_actual(all_segments)
        want = cf.state_actual_margin(state, baseline)
        assert abs(got - want) < 1.0, (state, got, want)


def test_a_district_none_of_whose_counties_reported_has_no_composition():
    assert cfd.district_composition({A: 500}, SEGS["NC"]["NC-02"]) is None
    assert cfd.district_composition({}, SEGS["NC"]["NC-01"]) is None


def test_a_county_reporting_zero_is_used_and_adds_no_weight():
    margin, used, total = cfd.district_composition({A: 0, B: 100}, SEGS["NC"]["NC-01"])
    assert used == 2
    assert total == pytest.approx(60)
    assert margin == pytest.approx(0.0), "all the weight is B's even segment"


def _day(cycle: int, dte: int, ballots: dict[str, int]) -> cf.DayIndex:
    return cf.DayIndex(cycle=cycle, state="NC", counties={dte: ballots})


def _state_row(**over) -> dict[str, str]:
    row = {
        "cycle": "2026", "state": "NC", "date": "2026-10-20", "days_to_election": "14",
        "reference_cycle": "2024", "reference_date": "2024-10-22",
        "reference_days_to_election": "14",
        "shift_pp": "1.5000", "shift_lo": "-10.5000", "shift_hi": "13.5000",
        "completeness": "0.3000", "reference_completeness": "0.4000",
        "behaviour_cycle": "2024", "confidence": "medium",
        "retrieved_at": "2026-10-20T12:00:00+00:00",
    }
    row.update(over)
    return row


def test_a_district_row_is_the_difference_of_its_two_compositions():
    now = _day(2026, 14, {A: 900, B: 100, C: 100})     # NC-01 leans on A (D+20)
    ref = _day(2024, 14, {A: 100, B: 900, C: 100})     # NC-01 leaned on B (even)
    rows = cfd.compare_district(_state_row(), now, ref, SEGS)
    by = {r["cd_code"]: r for r in rows}
    assert set(by) == {"NC-01", "NC-02"}
    r = by["NC-01"]
    here = (900 * 20 + 60 * 0) / 960
    there = (100 * 20 + 540 * 0) / 640
    assert float(r["composition_margin"]) == pytest.approx(here, abs=1e-3)
    assert float(r["reference_composition_margin"]) == pytest.approx(there, abs=1e-3)
    assert float(r["shift_pp"]) == pytest.approx(here - there, abs=1e-3)
    assert float(r["implied_margin_2024"]) == pytest.approx(
        float(r["actual_margin_2024"]) + float(r["shift_pp"]), abs=1e-3)
    assert r["counties_used"] == "2" and r["counties_total"] == "2"
    assert r["ballots"] == "960" and r["reference_ballots"] == "640"
    assert r["confidence"] == "medium" and r["completeness"] == "0.3000"
    assert r["method"] == cfd.METHOD


def test_the_band_is_the_states_own_half_width():
    now = _day(2026, 14, {A: 900, B: 100, C: 100})
    ref = _day(2024, 14, {A: 100, B: 900, C: 100})
    (r,) = [r for r in cfd.compare_district(_state_row(), now, ref, SEGS) if r["cd_code"] == "NC-01"]
    shift = float(r["shift_pp"])
    assert float(r["shift_hi"]) - shift == pytest.approx(12.0, abs=1e-3)
    assert shift - float(r["shift_lo"]) == pytest.approx(12.0, abs=1e-3)


def test_an_early_band_always_contains_no_change():
    now = _day(2026, 14, {A: 900, B: 100, C: 100})
    ref = _day(2024, 14, {A: 100, B: 900, C: 100})
    early = _state_row(confidence="early", shift_pp="30.0", shift_lo="29.0", shift_hi="31.0")
    (r,) = [r for r in cfd.compare_district(early, now, ref, SEGS) if r["cd_code"] == "NC-01"]
    assert float(r["shift_lo"]) <= 0.0 <= float(r["shift_hi"])
    assert r["confidence"] == "early"


def test_an_unchanged_composition_gives_a_computed_zero():
    now = _day(2026, 14, {A: 500, B: 500, C: 500})
    ref = _day(2024, 14, {A: 50, B: 50, C: 50})
    for r in cfd.compare_district(_state_row(), now, ref, SEGS):
        assert float(r["shift_pp"]) == pytest.approx(0.0, abs=1e-6)


def test_a_district_with_nothing_reported_on_either_side_gets_no_row():
    now = _day(2026, 14, {A: 900})
    ref = _day(2024, 14, {A: 100, B: 900, C: 100})
    rows = cfd.compare_district(_state_row(), now, ref, SEGS)
    assert [r["cd_code"] for r in rows] == ["NC-01"], "NC-02 has no reporting county now"


def test_a_state_row_with_no_matching_series_produces_nothing():
    rows = cfd.compare_district(_state_row(days_to_election="99"), _day(2026, 14, {A: 1}),
                                _day(2024, 14, {A: 1}), SEGS)
    assert rows == []


def test_build_follows_the_published_state_rows_and_write_touches_one_file(tmp_path):
    out = tmp_path / "output"
    (out / "counties").mkdir(parents=True)
    cols = ["cycle", "state", "county_fips", "county_name", "date", "days_to_election", "ballots_total"]
    with (out / "counties" / "nc.csv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, cols)
        w.writeheader()
        for cycle, day, dte, ballots in [
            (2026, "2026-10-20", 14, {A: 900, B: 100, C: 100}),
            (2024, "2024-10-22", 14, {A: 100, B: 900, C: 100}),
        ]:
            for fips, n in ballots.items():
                w.writerow({"cycle": cycle, "state": "NC", "county_fips": fips, "county_name": "",
                            "date": day, "days_to_election": dte, "ballots_total": n})
    (out / "ev_state_daily.csv").write_text("cycle,state,date,days_to_election,ballots_total\n")
    with (out / cf.COUNTERFACTUAL_FILENAME).open("w", newline="") as fh:
        w = csv.DictWriter(fh, cf.COUNTERFACTUAL_COLUMNS, extrasaction="ignore")
        w.writeheader()
        w.writerow(_state_row())

    rows = cfd.build(out, segments=SEGS)
    assert {r["cd_code"] for r in rows} == {"NC-01", "NC-02"}
    before = sorted(p.name for p in out.rglob("*") if p.is_file())
    cfd.write(out, rows, rebuild=True)
    after = sorted(p.name for p in out.rglob("*") if p.is_file())
    assert set(after) - set(before) == {cfd.FILENAME}
    with (out / cfd.FILENAME).open(newline="") as fh:
        published = list(csv.DictReader(fh))
    assert [r["cd_code"] for r in published] == ["NC-01", "NC-02"]
    assert not (set(published[0]) & cf.FORBIDDEN_COLUMNS)


def test_the_real_crosswalk_covers_every_district_the_state_table_can_name():
    segments = cfd.load_segments()
    assert sum(len(cds) for cds in segments.values()) == 435
    for cds in segments.values():
        for cd, segs in cds.items():
            assert cfd.district_actual(segs) is not None, cd
            assert all(s.two_party > 0 for s in segs), cd
