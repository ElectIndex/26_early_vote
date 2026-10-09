"""Congressional districts: the sixth table, and the only one that is mostly
DERIVED rather than read.

Three states' files carry a district column (North Carolina, Virginia,
Maryland) and their adapters emit `DistrictDay` rows with `basis="reported"`.
Everywhere else `ev.districts.rebuild` splits each county's published daily
figures across the districts the county touches, weighted by that county's
2024 vote in each -- `basis="apportioned"`. The rules worth a test:

* a reported day beats an apportioned one, and an apportioned day never
  overwrites a reported one on disk;
* district counts sum back to the county counts exactly (largest-remainder
  rounding), so the state's district rows and county rows agree to the ballot;
* THE BLANK RULE survives the split: a county field that is blank yields a
  blank district field, never a 0 and never a partial sum;
* every district the crosswalk names is one the site can draw, and every
  county the pipeline can key is in the crosswalk.
"""

from __future__ import annotations

import csv
import json
from datetime import date
from pathlib import Path

import pytest

from ev import districts, publish
from ev.adapters import _districts, _fips
from ev.normalize import cd_code
from ev.schema import (
    BASIS_APPORTIONED, BASIS_REPORTED, DISTRICT_DAILY_COLUMNS, TIER_CIVIC,
    TIER_SCRAPER, CountyDay, DistrictDay, Provenance, district_row_to_dict,
)

FIXTURES = Path(__file__).parent / "fixtures" / "districts"
THEME_CODES = FIXTURES / "theme_district_codes.json"

PROV = Provenance(tier=TIER_SCRAPER, name="stub", retrieved_at="2026-10-20T12:00:00+00:00")


# --------------------------------------------------------------------------
# The code: "NC-01", from whatever a state calls it
# --------------------------------------------------------------------------

@pytest.mark.parametrize("state, raw, want", [
    ("NC", "CONGRESSIONAL DISTRICT 1", "NC-01"),
    ("NC", "CONGRESSIONAL DISTRICT 14", "NC-14"),
    ("VA", "2", "VA-02"),
    ("VA", " 11 ", "VA-11"),
    ("MD", "06", "MD-06"),
    ("md", "8", "MD-08"),
    ("PA", "District 12", "PA-12"),
    ("TX", "TX-38", "TX-38"),
    ("tx", "tx-01", "TX-01"),
    ("AK", "AT LARGE", "AK-01"),
    ("DE", "At-Large", "DE-01"),
    ("WY", "0", "WY-01"),
    ("VT", "00", "VT-01"),
])
def test_every_spelling_a_state_uses_becomes_one_code(state, raw, want):
    assert cd_code(state, raw) == want


@pytest.mark.parametrize("state, raw", [
    ("NC", ""), ("NC", None), ("NC", "   "),
    ("NC", "NC HOUSE DISTRICT 1"),      # the wrong chamber
    ("NC", "0"),                          # NC is not at-large
    ("NC", "AT LARGE"),
    ("VA", "VA-99"),                      # more seats than Virginia has
    ("VA", "NC-01"),                      # another state's district
    ("MD", "abc"),
])
def test_anything_else_is_none_never_a_guess(state, raw):
    assert cd_code(state, raw) is None


# --------------------------------------------------------------------------
# The row
# --------------------------------------------------------------------------

def _row(cd="NC-01", basis=BASIS_REPORTED, day=None, **kw):
    return DistrictDay(cycle=2026, state="NC", cd_code=cd, day=day or date(2026, 10, 20),
                       basis=basis, provenance=PROV, **kw)


def test_a_district_row_refuses_a_malformed_code_or_basis():
    with pytest.raises(ValueError):
        _row(cd="1")
    with pytest.raises(ValueError):
        _row(cd="NC-1")
    with pytest.raises(ValueError):
        _row(basis="guessed")


def test_the_published_row_carries_its_basis_and_honours_the_blank_rule():
    out = district_row_to_dict(_row(ballots_total=10, party_dem=None))
    assert list(out) == DISTRICT_DAILY_COLUMNS
    assert out["cd_code"] == "NC-01"
    assert out["basis"] == "reported"
    assert out["ballots_total"] == "10"
    assert out["party_dem"] == "", "blank, never 0"
    assert out["days_to_election"] == "14"
    assert out["source_name"] == "stub"


def test_district_rows_ride_the_result_as_an_attribute_like_towns_do():
    from ev.adapters.base import FetchResult
    result = FetchResult()
    assert _districts.rows_of(result) == []
    row = DistrictDay(cycle=2026, state="NC", cd_code="NC-01", day=date(2026, 10, 20))
    _districts.attach(result, [row])
    _districts.stamp(result, PROV)
    assert _districts.rows_of(result)[0].provenance is PROV


# --------------------------------------------------------------------------
# The crosswalk
# --------------------------------------------------------------------------

def test_every_district_in_the_crosswalk_is_one_the_site_can_draw():
    drawable = set(json.loads(THEME_CODES.read_text()))
    assert len(drawable) == 435
    ours = {cd for cds in districts.load_weights().values()
            for pieces in cds.values() for cd, _ in pieces}
    assert ours == drawable, (sorted(ours - drawable), sorted(drawable - ours))


@pytest.mark.parametrize("state", ["NC", "VA", "MD", "TX", "FL", "GA", "AK", "CT", "AZ", "CA"])
def test_every_county_the_pipeline_can_key_is_in_the_crosswalk(state):
    keyed = {_fips.lookup(state, name)[0] for name in _fips.names(state)}
    assert set(districts.load_weights()[state]) == keyed


def test_every_county_sums_to_one_and_dc_is_absent():
    weights = districts.load_weights()
    assert "DC" not in weights
    for state, counties in weights.items():
        for fips, pieces in counties.items():
            assert abs(sum(w for _, w in pieces) - 1.0) < 1e-9, (state, fips)


def test_a_split_county_looks_the_way_the_house_map_draws_it():
    # Wake County, NC: three districts on the 2026 map, NC-02 the lion's share.
    wake = dict(districts.load_weights()["NC"]["37183"])
    assert set(wake) == {"NC-02", "NC-04", "NC-13"}
    assert wake["NC-02"] > 0.6
    # And a county the map does not split has exactly one row of weight 1.
    assert districts.load_weights()["NC"]["37001"] == [("NC-09", 1.0)]


# --------------------------------------------------------------------------
# Apportionment arithmetic
# --------------------------------------------------------------------------

def test_split_counts_sum_back_to_the_county_exactly():
    pieces = [("X-01", 0.333333), ("X-02", 0.333333), ("X-03", 0.333334)]
    out = districts.split_count(100, pieces)
    assert sum(out.values()) == 100
    assert sorted(out.values()) == [33, 33, 34]
    out = districts.split_count(1, pieces)
    assert sum(out.values()) == 1 and max(out.values()) == 1
    assert districts.split_count(0, pieces) == {"X-01": 0, "X-02": 0, "X-03": 0}


def test_a_blank_county_field_splits_to_blank_never_zero():
    pieces = [("X-01", 0.5), ("X-02", 0.5)]
    assert districts.split_count(None, pieces) == {"X-01": None, "X-02": None}


def _county(fips, day=date(2026, 10, 20), cycle=2026, **kw):
    return CountyDay(cycle=cycle, state="NC", county_fips=fips, day=day,
                     provenance=PROV, **kw)


def test_apportioned_rows_sum_to_the_counties_and_carry_the_counties_provenance():
    weights = {"NC": {
        "37183": [("NC-02", 0.7), ("NC-04", 0.2), ("NC-13", 0.1)],
        "37001": [("NC-09", 1.0)],
    }}
    rows = districts.apportion([
        _county("37183", ballots_total=1000, mail_returned=400, inperson=600,
                party_dem=500, party_rep=300, party_npa=200, party_oth=0),
        _county("37001", ballots_total=10, mail_returned=4, inperson=6,
                party_dem=5, party_rep=3, party_npa=2, party_oth=0),
    ], weights)
    by = {r.cd_code: r for r in rows}
    assert set(by) == {"NC-02", "NC-04", "NC-13", "NC-09"}
    assert sum(r.ballots_total for r in rows) == 1010
    assert by["NC-02"].ballots_total == 700
    assert by["NC-09"].ballots_total == 10
    assert sum(r.party_dem for r in rows) == 505
    assert sum(r.mail_returned for r in rows) == 404
    assert all(r.basis == BASIS_APPORTIONED for r in rows)
    assert all(r.provenance is PROV for r in rows)
    assert all(r.day == date(2026, 10, 20) and r.cycle == 2026 for r in rows)


def test_a_field_blank_in_any_contributing_county_is_blank_in_the_district():
    # Two counties feed NC-02. One reports party, the other does not: a sum of
    # the one that does is not NC-02's party count, it is half of it.
    weights = {"NC": {
        "37183": [("NC-02", 1.0)],
        "37101": [("NC-02", 1.0)],
    }}
    rows = districts.apportion([
        _county("37183", ballots_total=100, party_dem=60, party_rep=40),
        _county("37101", ballots_total=50, party_dem=None, party_rep=None),
    ], weights)
    (row,) = rows
    assert row.ballots_total == 150
    assert row.party_dem is None and row.party_rep is None
    assert row.party_npa is None, "nobody reported it"


def test_a_county_missing_from_the_crosswalk_is_skipped_not_invented(caplog):
    weights = {"NC": {"37183": [("NC-02", 1.0)]}}
    rows = districts.apportion([
        _county("37183", ballots_total=100),
        _county("99999", ballots_total=100),
    ], weights)
    assert [r.ballots_total for r in rows] == [100]
    assert "99999" in caplog.text


def test_days_and_cycles_do_not_bleed_into_each_other():
    weights = {"NC": {"37183": [("NC-02", 1.0)]}}
    rows = districts.apportion([
        _county("37183", ballots_total=100, day=date(2026, 10, 20)),
        _county("37183", ballots_total=150, day=date(2026, 10, 21)),
        _county("37183", ballots_total=900, day=date(2024, 11, 5), cycle=2024),
    ], weights)
    got = {(r.cycle, r.day.isoformat()): r.ballots_total for r in rows}
    assert got == {(2026, "2026-10-20"): 100, (2026, "2026-10-21"): 150,
                   (2024, "2024-11-05"): 900}


# --------------------------------------------------------------------------
# The rebuild: reported beats apportioned, and nothing is forgotten
# --------------------------------------------------------------------------

def _write_counties(out_dir: Path, rows):
    publish.publish_county_daily(out_dir, "NC", rows)


def _read(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as fh:
        return list(csv.DictReader(fh))


@pytest.fixture
def weights(monkeypatch):
    table = {"NC": {
        "37183": [("NC-02", 0.5), ("NC-13", 0.5)],
        "37001": [("NC-09", 1.0)],
    }}
    monkeypatch.setattr(districts, "load_weights", lambda path=None: table)
    return table


def test_a_state_with_only_county_rows_gets_an_apportioned_file(tmp_path, weights):
    _write_counties(tmp_path, [
        _county("37183", ballots_total=100),
        _county("37001", ballots_total=10),
    ])
    districts.rebuild(tmp_path, "NC")
    rows = _read(tmp_path / "districts" / "nc.csv")
    assert {r["cd_code"]: r["ballots_total"] for r in rows} == {
        "NC-02": "50", "NC-13": "50", "NC-09": "10"}
    assert {r["basis"] for r in rows} == {"apportioned"}
    assert rows[0]["source_name"] == "stub", "the county rows' own provenance"


def test_a_reported_day_replaces_the_apportioned_one_for_that_day_only(tmp_path, weights):
    _write_counties(tmp_path, [
        _county("37183", ballots_total=100, day=date(2026, 10, 20)),
        _county("37183", ballots_total=200, day=date(2026, 10, 21)),
    ])
    districts.rebuild(tmp_path, "NC")
    districts.rebuild(tmp_path, "NC", reported=[
        _row(cd="NC-02", ballots_total=130, day=date(2026, 10, 21)),
        _row(cd="NC-13", ballots_total=70, day=date(2026, 10, 21)),
    ])
    rows = _read(tmp_path / "districts" / "nc.csv")
    by = {(r["date"], r["cd_code"]): r for r in rows}
    assert by[("2026-10-20", "NC-02")]["basis"] == "apportioned"
    assert by[("2026-10-21", "NC-02")]["basis"] == "reported"
    assert by[("2026-10-21", "NC-02")]["ballots_total"] == "130"
    assert by[("2026-10-21", "NC-13")]["ballots_total"] == "70"


def test_a_later_rebuild_without_reported_rows_keeps_the_reported_ones(tmp_path, weights):
    _write_counties(tmp_path, [_county("37183", ballots_total=100)])
    districts.rebuild(tmp_path, "NC", reported=[
        _row(cd="NC-02", ballots_total=60), _row(cd="NC-13", ballots_total=40)])
    # The next morning's ingest: counties again, no district rows this time
    # (the state's file failed and civicAPI answered). The reported day on
    # disk must survive, exactly as a county file survives a bad morning.
    districts.rebuild(tmp_path, "NC")
    rows = _read(tmp_path / "districts" / "nc.csv")
    assert {r["basis"] for r in rows} == {"reported"}
    assert {r["cd_code"]: r["ballots_total"] for r in rows} == {"NC-02": "60", "NC-13": "40"}


def test_a_past_cycle_is_apportioned_onto_todays_map(tmp_path, weights):
    # 2024's county rows carry no district label from the state, and even
    # where a state labelled them, those were 2024's districts. Every past
    # cycle is split onto the current map so the comparison is one geography.
    _write_counties(tmp_path, [
        _county("37183", ballots_total=1000, day=date(2024, 11, 5), cycle=2024),
        _county("37183", ballots_total=100, day=date(2026, 10, 20)),
    ])
    districts.rebuild(tmp_path, "NC", reported=[
        _row(cd="NC-02", ballots_total=60), _row(cd="NC-13", ballots_total=40)])
    rows = _read(tmp_path / "districts" / "nc.csv")
    by = {(r["cycle"], r["cd_code"]): r for r in rows}
    assert by[("2024", "NC-02")]["ballots_total"] == "500"
    assert by[("2024", "NC-02")]["basis"] == "apportioned"
    assert by[("2026", "NC-02")]["basis"] == "reported"


def test_a_state_with_nothing_to_split_writes_no_file(tmp_path, weights):
    assert districts.rebuild(tmp_path, "NC") is None
    assert not (tmp_path / "districts").exists()


def test_an_unchanged_rebuild_keeps_its_timestamps(tmp_path, weights):
    _write_counties(tmp_path, [_county("37183", ballots_total=100)])
    districts.rebuild(tmp_path, "NC")
    before = _read(tmp_path / "districts" / "nc.csv")
    districts.rebuild(tmp_path, "NC")
    after = _read(tmp_path / "districts" / "nc.csv")
    assert before == after


def test_a_worse_tier_county_file_still_apportions_with_its_own_tier(tmp_path, weights):
    civic = Provenance(tier=TIER_CIVIC, name="civicapi")
    _write_counties(tmp_path, [CountyDay(cycle=2026, state="NC", county_fips="37001",
                                         day=date(2026, 10, 20), ballots_total=10,
                                         provenance=civic)])
    districts.rebuild(tmp_path, "NC")
    (row,) = _read(tmp_path / "districts" / "nc.csv")
    assert row["source_tier"] == str(TIER_CIVIC)
    assert row["source_name"] == "civicapi"


def test_reported_rows_from_a_past_cycle_are_set_aside_not_kept(tmp_path, weights):
    _write_counties(tmp_path, [
        _county("37183", ballots_total=1000, day=date(2024, 11, 5), cycle=2024)])
    districts.rebuild(tmp_path, "NC", reported=[
        DistrictDay(cycle=2024, state="NC", cd_code="NC-02", day=date(2024, 11, 5),
                    ballots_total=999, provenance=PROV)])
    rows = _read(tmp_path / "districts" / "nc.csv")
    assert {(r["cd_code"], r["ballots_total"], r["basis"]) for r in rows} == {
        ("NC-02", "500", "apportioned"), ("NC-13", "500", "apportioned")}
