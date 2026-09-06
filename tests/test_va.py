"""Virginia: ELECT's absentee export, by locality.

Two fixtures, both real ELECT output. The 2024 one is downsampled to at most
three verbatim rows per locality -- one in-person, one returned mail ballot, one
still outstanding -- so that all 133 localities and all ten application types
survive in 45KB. The 2022 one keeps only twelve localities on purpose: it is the
partial-export case, and Virginia's four city/county name twins are all in it.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from ev.adapters import va
from ev.adapters._net import Missing
from ev.adapters.base import NotYetPublished, SchemaDrift, SourceError
from ev.schema import county_row_to_dict, state_row_to_dict
from ev.adapters.base import FetchResult
from ev.schema import Provenance, TIER_SCRAPER

FIXTURES = Path(__file__).parent / "fixtures" / "va"
FULL = FIXTURES / "2024NovemberGeneral_EnrAbsenteeRawCSV.csv"
PARTIAL = FIXTURES / "2022-November-General_Absentee_partial.csv"


@pytest.fixture(scope="module")
def gen2024():
    return va.parse(FULL.read_bytes(), 2024, date(2024, 11, 5))


@pytest.fixture(scope="module")
def gen2022_partial():
    return va.parse(PARTIAL.read_bytes(), 2022, date(2022, 11, 8))


# --------------------------------------------------------------------------
# THE BLANK RULE -- the one that matters most for this batch
# --------------------------------------------------------------------------
def test_no_party_field_is_ever_populated(gen2024, gen2022_partial):
    """Virginia has no party registration. A 0 here would render as 'zero
    Democrats have voted' on a page whose whole subject is who is voting."""
    for result in (gen2024, gen2022_partial):
        for row in result.state_rows + result.county_rows:
            for field in ("party_dem", "party_rep", "party_oth", "party_npa"):
                assert getattr(row, field) is None, (field, row)


def test_party_fields_write_as_blank_cells_not_zeros(gen2024):
    """The None has to survive all the way to the CSV the website reads."""
    prov = Provenance(tier=TIER_SCRAPER, name="va-elect")
    state = state_row_to_dict(FetchResult(state_rows=list(gen2024.state_rows))
                              .stamp(prov).state_rows[0])
    county = county_row_to_dict(FetchResult(county_rows=[gen2024.county_rows[0]])
                                .stamp(prov).county_rows[0])
    for cells in (state, county):
        for field in ("party_dem", "party_rep", "party_oth", "party_npa"):
            assert cells[field] == "", (field, cells)


# --------------------------------------------------------------------------
# Independent cities: the trap this adapter exists to avoid
# --------------------------------------------------------------------------
def test_independent_cities_are_not_their_namesake_counties(gen2024):
    """Fairfax city is 51600 and Fairfax County is 51059; the same trap is set
    for Richmond, Franklin and Roanoke. Both must appear, with their own
    numbers, and neither may absorb the other."""
    by_fips = {row.county_fips: row for row in gen2024.county_rows}
    twins = {
        "51600": "Fairfax city", "51059": "Fairfax County",
        "51760": "Richmond city", "51159": "Richmond County",
        "51620": "Franklin city", "51067": "Franklin County",
        "51770": "Roanoke city", "51161": "Roanoke County",
    }
    for fips, name in twins.items():
        assert fips in by_fips, f"{name} ({fips}) missing"
        assert by_fips[fips].county_name == name

    # And they really are distinct places, not the same row emitted twice.
    assert by_fips["51059"].ballots_total != by_fips["51600"].ballots_total
    assert by_fips["51760"].ballots_total != by_fips["51159"].ballots_total


def test_a_bare_ambiguous_locality_is_drift_not_a_guess():
    """If ELECT ever dropped the qualifying word we must refuse the row rather
    than credit a city's ballots to the county that surrounds it."""
    header = FULL.read_text(encoding="utf-8").splitlines()[0]
    body = "\n".join([
        header,
        '"FAIRFAX","11","41 to 60","F","NE - No Excuse","In Person","In Person","10"',
    ]).encode()
    with pytest.raises(SchemaDrift, match="unresolved localities"):
        va.parse(body, 2024, date(2024, 11, 5))


def test_ampersand_spelling_is_translated_not_guessed(gen2024):
    """ELECT writes "KING & QUEEN COUNTY"; the census writes the word out. That
    is a spelling, not an ambiguity, and it is the only one we translate."""
    assert "51097" in {row.county_fips for row in gen2024.county_rows}
    assert set(va.LOCALITY_SPELLINGS) == {"king & queen county"}


def test_every_virginia_locality_is_present_and_fips_keyed(gen2024):
    """95 counties plus 38 independent cities."""
    assert len(gen2024.county_rows) == 133 == va.EXPECTED_LOCALITIES
    for row in gen2024.county_rows:
        assert len(row.county_fips) == 5 and row.county_fips.startswith("51")
    assert len({row.county_fips for row in gen2024.county_rows}) == 133


# --------------------------------------------------------------------------
# Method comes from ApplicationType
# --------------------------------------------------------------------------
def test_in_person_and_mail_are_split_on_the_application_column(gen2024):
    fairfax = next(r for r in gen2024.county_rows if r.county_fips == "51059")
    assert fairfax.inperson is not None and fairfax.mail_returned is not None
    assert fairfax.ballots_total == fairfax.inperson + fairfax.mail_returned


def test_outstanding_mail_ballots_are_requested_but_not_returned(gen2024):
    """A blank ReceiptType is a ballot issued and not yet back. It must raise
    mail_requested without raising mail_returned or the total."""
    (state,) = gen2024.state_rows
    assert state.mail_requested > state.mail_returned
    assert state.ballots_total == state.inperson + state.mail_returned


def test_statewide_row_totals_the_localities(gen2024):
    (state,) = gen2024.state_rows
    assert state.state == "VA" and state.day == date(2024, 11, 5)
    assert state.ballots_total == sum(r.ballots_total for r in gen2024.county_rows)
    assert state.inperson == sum(r.inperson or 0 for r in gen2024.county_rows)


def test_an_unknown_application_type_is_drift_not_bucketed_as_mail():
    header = FULL.read_text(encoding="utf-8").splitlines()[0]
    body = "\n".join([
        header,
        '"ARLINGTON COUNTY","8","41 to 60","F","NE - No Excuse","Kiosk Application","Mail","10"',
    ]).encode()
    with pytest.raises(SchemaDrift, match="unknown application types"):
        va.parse(body, 2024, date(2024, 11, 5))


def test_an_unknown_receipt_type_is_drift():
    header = FULL.read_text(encoding="utf-8").splitlines()[0]
    body = "\n".join([
        header,
        '"ARLINGTON COUNTY","8","41 to 60","F","NE - No Excuse","Virginia Specific '
        'Application - SBE 701","Carrier Pigeon","10"',
    ]).encode()
    with pytest.raises(SchemaDrift, match="unknown receipt types"):
        va.parse(body, 2024, date(2024, 11, 5))


def test_the_en_dash_in_the_fpca_label_is_folded(gen2024):
    """ELECT writes "Federal Post Cards Application – FPCA" with an en dash."""
    assert va._key("Federal Post Cards Application – FPCA") in va.MAIL_APPLICATIONS
    assert va._key("Federal Post Cards Application - FPCA") in va.MAIL_APPLICATIONS


# --------------------------------------------------------------------------
# A partial export is counties only -- never a Virginia total
# --------------------------------------------------------------------------
def test_a_partial_export_publishes_no_statewide_row(gen2022_partial):
    """ELECT publishes no TOTAL row of its own, so a statewide figure can only
    be our sum -- and a sum of twelve localities is not Virginia."""
    assert gen2022_partial.state_rows == []
    assert len(gen2022_partial.county_rows) == 12


def test_the_2022_layout_parses_with_the_same_code(gen2022_partial):
    by_fips = {row.county_fips: row for row in gen2022_partial.county_rows}
    assert by_fips["51600"].county_name == "Fairfax city"
    assert by_fips["51059"].county_name == "Fairfax County"
    assert all(r.inperson is not None or r.mail_returned is not None
               for r in gen2022_partial.county_rows)


# --------------------------------------------------------------------------
# Drift and absence
# --------------------------------------------------------------------------
def test_renamed_column_raises_drift():
    body = b'"LocalityName","CongressionalDistrict","AgeRange","Gender","Reason",' \
           b'"HowTheyApplied","ReceiptType","TheCount"\n'
    with pytest.raises(SchemaDrift, match="missing columns"):
        va.parse(body, 2026, date(2026, 11, 3))


def test_an_html_page_instead_of_the_csv_is_a_source_error():
    with pytest.raises(SourceError):
        va.parse(b"<!DOCTYPE html><html>nope</html>", 2026, date(2026, 11, 3))


def _feed(elections, *, reports=None, blob_body=b""):
    """A fake `get` that plays ELECT's three endpoints back to the adapter."""
    def fake_get(url, **kwargs):
        if url == va.JURISDICTION_URL:
            return json.dumps({
                "id": "d2c804ee-4ec2-46bb-91d7-5b41526eab03",
                "elections": elections,
            }).encode()
        if url.startswith(va.ENR + "/results/public/api/elections/"):
            return json.dumps({
                "asOf": "2026-10-20T09:00:00Z",
                "publicReportCategories": reports or [],
            }).encode()
        if not blob_body:
            raise Missing(f"VA: {url} returned 404")
        return blob_body
    return fake_get


ABSENTEE_CATEGORY = [{
    "categoryName": "Other Reports",
    "reports": [{"reportName": "EnrAbsenteeRawCSV",
                 "blobName": "EnrAbsenteeRawCSV_deadbeef.csv"}],
}]


def test_a_cycle_with_no_election_yet_is_not_yet_published(monkeypatch):
    """This is the path that runs every day until ELECT stands the general up --
    exactly where Virginia sits today. It must STOP the ladder, not fall through
    to a source that invents a zero."""
    monkeypatch.setattr(va, "get", _feed([
        {"publicElectionId": "2026-August-Democratic-Primary",
         "electionDate": "2026-08-04"},
    ]))
    with pytest.raises(NotYetPublished, match="has not stood up"):
        va.VAScraper().fetch(2026, date(2026, 9, 5))


def test_an_election_without_an_absentee_report_is_not_yet_published(monkeypatch):
    """The election exists, early voting has not produced a file yet."""
    monkeypatch.setattr(va, "get", _feed(
        [{"publicElectionId": "2026-November-General", "electionDate": "2026-11-03"}],
        reports=[{"categoryName": "Other Reports", "reports": []}],
    ))
    with pytest.raises(NotYetPublished, match="no absentee export"):
        va.VAScraper().fetch(2026, date(2026, 9, 5))


def test_a_missing_blob_is_not_yet_published(monkeypatch):
    monkeypatch.setattr(va, "get", _feed(
        [{"publicElectionId": "2026-November-General", "electionDate": "2026-11-03"}],
        reports=ABSENTEE_CATEGORY,
    ))
    with pytest.raises(NotYetPublished, match="not posted yet"):
        va.VAScraper().fetch(2026, date(2026, 9, 5))


def test_two_elections_on_the_same_general_date_is_drift(monkeypatch):
    monkeypatch.setattr(va, "get", _feed([
        {"publicElectionId": "2026-November-General", "electionDate": "2026-11-03"},
        {"publicElectionId": "2026-November-General-Copy", "electionDate": "2026-11-03"},
    ]))
    with pytest.raises(SchemaDrift, match="matches 2 elections"):
        va.VAScraper().fetch(2026, date(2026, 11, 3))


def test_the_published_date_dates_the_rows_and_never_runs_ahead(monkeypatch):
    monkeypatch.setattr(va, "get", _feed(
        [{"publicElectionId": "2026-November-General", "electionDate": "2026-11-03"}],
        reports=ABSENTEE_CATEGORY, blob_body=FULL.read_bytes(),
    ))
    result = va.VAScraper().fetch(2026, date(2026, 10, 25))
    # ELECT says it refreshed on the 20th; that is the honest as-of date.
    assert {r.day for r in result.state_rows} == {date(2026, 10, 20)}
    # A file stamped after the run is clamped rather than dated into the future.
    early = va.VAScraper().fetch(2026, date(2026, 10, 10))
    assert {r.day for r in early.state_rows} == {date(2026, 10, 10)}


def test_history_for_the_live_cycle_is_refused():
    with pytest.raises(NotYetPublished, match="not an archived cycle"):
        va.VAScraper().fetch_history(date.today().year)


def test_the_legacy_archive_is_only_claimed_for_pre_enr_cycles():
    assert set(va.LEGACY_ELECTIONS) == {2022}


def test_published_date_reads_the_election_metadata():
    assert va.published_date({"asOf": "2024-10-21T13:05:00.123Z"}) == date(2024, 10, 21)
    assert va.published_date({"lastUpdated": "2024-10-20T01:00:00"}) == date(2024, 10, 20)
    assert va.published_date({}) is None


def test_adapter_identity():
    scraper = va.VAScraper()
    assert (scraper.state, scraper.name, scraper.tier) == ("VA", "va-elect", 1)
