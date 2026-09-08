"""Party crossed with METHOD: the crosstab `CountyDay` could not hold.

`docs/counterfactual.md` said, for two revisions, that "no state publishes party
crossed with method". That was true of `schema.py` and false of the sources.
Five adapters were parsing the cells and adding them together at the last step:

* **FL** renders "Voted Vote-by-Mail" and "Voted Early" as two per-county tables,
  each split Republican / Democrat / Other / NPA;
* **KY**'s workbook carries DEM and REP columns for mail returned, for excused
  in-person and for no-excuse in-person;
* **NC** and **ME** publish one row per ballot with the party and the return
  channel on the same row;
* **CO**'s workbook is a county x party matrix repeated per channel.

`schema.MethodDay` and `output/methods/<st>.csv` are where the cells live now.
These tests pin the row type, the published table, and the one thing that makes
the table honest rather than merely present: a state that ships ONE band is a
state with one band, not a state whose other band is zero. Colorado 2022 is that
case and `counterfactual.reconciled_cells` is what stops it being averaged over.
"""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

import pytest

from ev.adapters import _methods
from ev.adapters.base import FetchResult
from ev.normalize import METHOD_INPERSON, METHOD_MAIL
from ev.publish import publish_method_daily
from ev.schema import (
    METHOD_DAILY_COLUMNS, METHODS, MethodDay, Provenance, TIER_SCRAPER,
    method_row_to_dict,
)


def _row(fips="12086", method=METHOD_MAIL, day=None, **kw):
    fields = dict(ballots_total=1200, party_dem=500, party_rep=400,
                  party_oth=40, party_npa=260)
    fields.update(kw)
    return MethodDay(cycle=2024, state="FL", county_fips=fips, day=day or date(2024, 11, 5),
                     method=method, county_name="Miami-Dade County",
                     provenance=Provenance(TIER_SCRAPER, "fl-doe"), **fields)


# --------------------------------------------------------------------------
# The row type
# --------------------------------------------------------------------------
def test_the_band_is_checked_at_construction_not_at_write_time():
    """The same rule, and the same reason, as `DemoDay.dimension`.

    A band this table does not know must fail inside `adapter.fetch`, which
    `ladder.run_state` wraps -- the attempt is recorded, the ladder falls
    through, and every other state still publishes. Raised at WRITE time it
    aborts the run for all of them, after the state table has been written and
    before the status file has.
    """
    for bad in ("EARLY VOTING", "ONE-STOP", "absentee", "", "in person"):
        with pytest.raises(ValueError, match="unknown method"):
            MethodDay(cycle=2024, state="FL", county_fips="12086",
                      day=date(2024, 11, 5), method=bad)


def test_the_vocabulary_is_normalize_s_and_there_is_only_one_of_it():
    """CLAUDE.md rule 4. A second spelling of "mail" in this file would be the
    fifteenth vocabulary the frontend has to know."""
    assert METHODS == (METHOD_MAIL, METHOD_INPERSON)


def test_the_key_carries_the_band_so_two_bands_are_two_rows():
    a, b = _row(method=METHOD_MAIL), _row(method=METHOD_INPERSON)
    assert a.key() != b.key()
    assert a.key() == (2024, "FL", "12086", "mail", "2024-11-05")


# --------------------------------------------------------------------------
# THE BLANK RULE, which applies twice over here
# --------------------------------------------------------------------------
def test_an_unreported_party_bucket_is_blank_and_a_reported_zero_is_zero():
    """Kentucky splits out DEM and REP and nothing else, so its NPA and OTH are
    genuinely unreported and its party buckets deliberately do not sum to the
    band's total. Florida reports all four, so an empty one is a real 0."""
    written = method_row_to_dict(_row(party_npa=None, party_oth=0))
    assert list(written) == METHOD_DAILY_COLUMNS
    assert written["party_npa"] == ""      # not reported
    assert written["party_oth"] == "0"     # reported, and zero
    assert written["method"] == "mail"
    assert written["days_to_election"] == "0"


def test_a_band_the_state_does_not_report_is_an_absent_row(tmp_path):
    """Not a row of zeros. Florida prints no "Voted Early" county rows at all
    until early voting opens, and Colorado shipped no per-county mail matrix in
    2022; a zero row there would say nobody voted that way."""
    publish_method_daily(tmp_path, "FL", [_row(method=METHOD_MAIL)])
    rows = list(csv.DictReader((tmp_path / "methods" / "fl.csv").open()))
    assert [r["method"] for r in rows] == ["mail"]


# --------------------------------------------------------------------------
# The published table
# --------------------------------------------------------------------------
def test_method_rows_are_written_per_state_beside_the_county_file(tmp_path):
    """Same shape as `counties/<st>.csv` and `towns/<st>.csv`: one file per
    state, lazily fetched, because this is a different partition of the same
    ballots rather than more columns on the same row."""
    publish_method_daily(tmp_path, "FL", [
        _row(method=METHOD_INPERSON), _row(method=METHOD_MAIL),
        _row(fips="12011", method=METHOD_MAIL),
    ])
    path = tmp_path / "methods" / "fl.csv"
    assert path.exists()
    rows = list(csv.DictReader(path.open()))
    # Sorted by county then band, so a county's two bands are adjacent.
    assert [(r["county_fips"], r["method"]) for r in rows] == [
        ("12011", "mail"), ("12086", "inperson"), ("12086", "mail")]


def test_a_restated_band_is_replaced_and_a_thinner_one_is_not(tmp_path):
    """publish.py's merge rules apply here unchanged. `method` is part of the
    KEY and must not count toward a row's richness, or every method row would
    look one field richer than it is."""
    publish_method_daily(tmp_path, "FL", [_row()])
    publish_method_daily(tmp_path, "FL", [_row(ballots_total=1300)])
    publish_method_daily(tmp_path, "FL", [_row(ballots_total=1, party_dem=None,
                                               party_rep=None, party_npa=None,
                                               party_oth=None)])
    rows = list(csv.DictReader((tmp_path / "methods" / "fl.csv").open()))
    assert len(rows) == 1
    assert rows[0]["ballots_total"] == "1300"


# --------------------------------------------------------------------------
# Carrying the rows, which is where TownDay's one sharp edge lives
# --------------------------------------------------------------------------
def test_rows_ride_as_an_attribute_and_extend_does_not_lose_them():
    """⚠️ `FetchResult.extend` merges its three FIELDS and cannot see an
    attribute, so an adapter that builds one result per archived day -- FL, KY
    and CO all do -- would keep only the LAST day's method rows. That is a silent
    data loss with no error anywhere, so `_methods.extend` exists and the archive
    loops call it."""
    combined, one = FetchResult(), FetchResult()
    _methods.attach(combined, [_row(fips="12011")])
    _methods.attach(one, [_row(fips="12086")])
    combined.extend(one)
    assert [r.county_fips for r in _methods.rows_of(combined)] == ["12011"]
    _methods.extend(combined, _methods.rows_of(one))
    assert [r.county_fips for r in _methods.rows_of(combined)] == ["12011", "12086"]


def test_a_result_with_no_method_rows_answers_with_an_empty_list():
    assert _methods.rows_of(FetchResult()) == []


def test_stamping_fills_only_what_the_adapter_left_blank():
    result = FetchResult()
    own = Provenance(TIER_SCRAPER, "already-mine")
    _methods.attach(result, [_row(), MethodDay(
        cycle=2024, state="FL", county_fips="12011", day=date(2024, 11, 5),
        method=METHOD_MAIL, provenance=own)])
    _methods.stamp(result, Provenance(TIER_SCRAPER, "fl-doe"))
    assert [r.provenance.name for r in _methods.rows_of(result)] == [
        "fl-doe", "already-mine"]
