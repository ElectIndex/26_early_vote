"""Hawaii: the Office of Elections' statewide Absentee Reconciliation report.

Two real, unmodified fixtures downloaded 2026-09-06, both HTTP 200:

* `AbsenteeReconState-20260717.pdf` -- before ballots went out. Every "Voted"
  column is a literal 0, which is what makes it the test that a reported zero
  publishes as 0 while an unreported field stays None.
* `AbsenteeReconState-20260808.pdf` -- primary Election Day, all four counties
  fully populated.

Both are the 2026 PRIMARY, because no general-election report exists yet -- and
that is itself the most important thing this suite pins: the filename pattern is
shared between the primary and the general, and the only thing that tells them
apart is the line the report prints about itself.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from ev.adapters import hi
from ev.adapters.base import FetchResult, NotYetPublished, SchemaDrift, SourceError
from ev.schema import Provenance, TIER_SCRAPER, county_row_to_dict, state_row_to_dict

FIXTURES = Path(__file__).parent / "fixtures" / "hi"
BEFORE = FIXTURES / "AbsenteeReconState-20260717.pdf"
ELECTION_DAY = FIXTURES / "AbsenteeReconState-20260808.pdf"


@pytest.fixture(scope="module")
def before() -> FetchResult:
    return hi.build(hi._text(BEFORE.read_bytes()), 2026)


@pytest.fixture(scope="module")
def voted() -> FetchResult:
    return hi.build(hi._text(ELECTION_DAY.read_bytes()), 2026)


def _counties(result: FetchResult) -> dict[str, object]:
    return {row.county_fips: row for row in result.county_rows}


# --------------------------------------------------------------------------
# The report says which election it is, and that is the whole gate
# --------------------------------------------------------------------------
def test_a_primary_report_stops_the_ladder_instead_of_being_published():
    """A primary's file is not an error and not a general-election number.

    NotYetPublished, not SourceError: there is no better source for a general
    election that has not started, and falling through would let a weaker tier
    invent a zero.
    """
    for fixture in (BEFORE, ELECTION_DAY):
        with pytest.raises(NotYetPublished) as caught:
            hi.parse(fixture.read_bytes(), 2026)
        assert "primary" in str(caught.value)


def test_the_election_line_is_read_off_the_report_itself():
    assert hi.election(hi._text(ELECTION_DAY.read_bytes())) == (2026, "primary")


def test_a_report_with_no_election_line_raises_drift():
    with pytest.raises(SchemaDrift):
        hi.election("Absentee Reconcillation\n8/8/2026 3:11:26 AM\n")


def test_a_report_stamped_a_different_day_than_its_filename_raises_drift():
    """A re-post of yesterday's numbers under today's name would flatten a day
    of the curve, so it fails loudly rather than being published twice."""
    text = hi._text(ELECTION_DAY.read_bytes())
    with pytest.raises(SchemaDrift):
        hi.build(text, 2026, filename_date=date(2026, 8, 9))
    # The matching date is accepted.
    assert hi.build(text, 2026, filename_date=date(2026, 8, 8)).state_rows


# --------------------------------------------------------------------------
# The numbers Hawaii published
# --------------------------------------------------------------------------
def test_statewide_row_matches_the_reports_own_totals_line(voted):
    row = voted.state_rows[0]
    assert row.day == date(2026, 8, 8)
    assert row.ballots_total == 234_639     # VOTED (b + d + f)
    assert row.mail_requested == 732_780    # MAIL Sent (e)
    assert row.mail_returned == 230_276     # MAIL Voted (f)
    assert row.inperson == 4_037            # EV Voted (d)


def test_all_four_elections_jurisdictions_are_present(voted):
    counties = _counties(voted)
    assert set(counties) == {"15001", "15003", "15007", "15009"}
    assert counties["15003"].county_name == "Honolulu County"
    assert counties["15003"].ballots_total == 157_126
    assert counties["15003"].mail_returned == 154_602
    assert counties["15003"].inperson == 2_303


def test_kalawao_never_appears_and_four_counties_is_full_coverage(voted):
    """Kalawao County (15005) has no elections division; Hawaii's four county
    clerks are the whole state, so four rows earn a statewide row."""
    assert "15005" not in _counties(voted)
    assert len(voted.state_rows) == 1


def test_the_okina_in_hawaii_and_kauai_still_resolves_to_a_fips_code():
    assert hi._county("Hawai'i") == ("15001", "Hawaii County")
    assert hi._county("Kauaʻi") == ("15007", "Kauai County")
    assert hi._county("City and County of Honolulu") == ("15003", "Honolulu County")


def test_the_totals_row_is_checked_against_the_sum_of_the_counties(voted):
    for field in ("ballots_total", "mail_returned", "inperson"):
        assert getattr(voted.state_rows[0], field) == sum(
            getattr(row, field) for row in voted.county_rows
        )


def test_ballots_total_exceeds_mail_plus_inperson_because_of_email_returns(voted):
    """Hawaii's VOTED column also counts UOCAVA ballots returned by email or
    fax, which are neither a mail ballot nor an in-person early vote."""
    row = voted.state_rows[0]
    assert row.ballots_total > row.mail_returned + row.inperson
    assert row.ballots_total - row.mail_returned - row.inperson == 326


# --------------------------------------------------------------------------
# THE BLANK RULE
# --------------------------------------------------------------------------
def test_hawaii_has_no_party_registration_so_no_party_field_is_ever_set(before, voted):
    for result in (before, voted):
        for row in result.state_rows + result.county_rows:
            for field in ("party_dem", "party_rep", "party_oth", "party_npa"):
                assert getattr(row, field) is None, (field, row)


def test_party_fields_write_as_blank_cells_not_zeros(voted):
    prov = Provenance(tier=TIER_SCRAPER, name="hi-oe")
    state = state_row_to_dict(
        FetchResult(state_rows=[voted.state_rows[0]]).stamp(prov).state_rows[0]
    )
    county = county_row_to_dict(
        FetchResult(county_rows=[voted.county_rows[0]]).stamp(prov).county_rows[0]
    )
    for cells in (state, county):
        for field in ("party_dem", "party_rep", "party_oth", "party_npa"):
            assert cells[field] == ""


def test_a_reported_zero_publishes_as_zero(before):
    """On 2026-07-17 nothing had been returned. Hawaii printed 0, so we do."""
    row = before.state_rows[0]
    assert row.day == date(2026, 7, 17)
    assert row.mail_returned == 0
    assert row.inperson == 0
    assert row.mail_requested == 726_068   # ballots were already in the post
    assert row.ballots_total == 31          # 31 email/fax returns, and they are real


# --------------------------------------------------------------------------
# Drift and the ladder contract
# --------------------------------------------------------------------------
def test_a_missing_column_raises_drift():
    text = hi._text(ELECTION_DAY.read_bytes()).replace("MAIL Voted", "MAIL Cast")
    with pytest.raises(SchemaDrift) as caught:
        hi.build(text, 2026)
    assert "MAIL Voted (f)" in str(caught.value)


def test_a_column_order_change_is_caught_by_the_reports_own_arithmetic():
    """VOTED must equal b+d+f and TOTAL must equal a+d+e on every row, which is
    what proves the nine values landed in the nine fields we think they did."""
    with pytest.raises(SchemaDrift) as caught:
        hi._check_arithmetic("15003", [1844, 221, 0, 2303, 480780, 154602, 1010, 1, 2])
    assert "VOTED" in str(caught.value)


def test_something_that_is_not_a_pdf_is_a_source_error():
    with pytest.raises(SourceError):
        hi.parse(b"<html>not posted yet</html>", 2026)


def test_no_archive_means_history_is_not_yet_published():
    with pytest.raises(NotYetPublished):
        hi.HIScraper().fetch_history(2024)


def test_the_adapter_declares_itself_correctly():
    scraper = hi.HIScraper()
    assert (scraper.state, scraper.name, scraper.tier) == ("HI", "hi-oe", TIER_SCRAPER)


def test_a_day_with_no_report_anywhere_stops_the_ladder(monkeypatch):
    from ev.adapters._net import Missing

    scraper = hi.HIScraper()
    monkeypatch.setattr(scraper, "_index_dates", list)
    monkeypatch.setattr(
        scraper, "_report",
        lambda day: (_ for _ in ()).throw(Missing(f"HI: {day} returned 404")),
    )
    with pytest.raises(NotYetPublished):
        scraper.fetch(2026, date(2026, 9, 6))
