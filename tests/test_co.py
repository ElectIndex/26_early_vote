"""Colorado: the Secretary of State's daily Election Activity workbook.

Two real fixtures, one per cycle, because Colorado is an all-mail state whose
workbook carries the county x party matrix three times over — all returns, the
mail subset, and the in-person (vote-center) subset. `inperson` here is a real
separately-reported number, not one derived by subtraction, and these tests pin
that the three cuts stay consistent with each other.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from ev.adapters import _methods, co
from ev.adapters.base import NotYetPublished

FIXTURES = Path(__file__).parent / "fixtures" / "co"
BOOK_2024 = (FIXTURES / "20241031ElectionActivity.xlsx").read_bytes()
BOOK_2022 = (FIXTURES / "20221101ElectionActivity.xlsx").read_bytes()


@pytest.fixture
def parsed():
    return co.parse(BOOK_2024, 2024, date(2024, 10, 31))


def test_every_county_in_the_fixture_parses(parsed):
    """The fixtures are real workbooks TRUNCATED to their first counties (the
    full file is 64), so this pins the parse, not Colorado's county count."""
    assert len(parsed.county_rows) == 8
    assert {r.county_name for r in parsed.county_rows} >= {"Adams County", "Arapahoe County"}


def test_county_rows_are_fips_keyed(parsed):
    for row in parsed.county_rows:
        assert len(row.county_fips) == 5 and row.county_fips.startswith("08")


def test_state_total_is_colorados_own_not_a_sum_of_counties(parsed):
    """The state row comes from the workbook's own statewide figure.

    Deliberately not a sum: these fixtures are truncated, so the eight counties
    here add to about half the state — and on the real file, summing counties
    would silently diverge from Colorado's published total the moment one county
    is missing or late.
    """
    counties = sum(r.ballots_total for r in parsed.county_rows if r.ballots_total)
    assert parsed.state_rows[0].ballots_total > counties


def test_party_buckets_sum_to_the_total(parsed):
    """Colorado registers by party and reports every bucket."""
    row = parsed.state_rows[0]
    assert (row.party_dem + row.party_rep + row.party_npa + row.party_oth
            == row.ballots_total)


def test_unaffiliated_is_the_largest_bucket(parsed):
    """Colorado has more unaffiliated voters than either party — if UAF were
    being folded into `oth` instead of `npa`, this would not hold."""
    row = parsed.state_rows[0]
    assert row.party_npa > row.party_dem
    assert row.party_npa > row.party_rep


def test_mail_and_inperson_are_separately_reported(parsed):
    """Both are their own published sheets in 2024, not one derived from the
    other by subtraction, and together they account for every ballot."""
    row = parsed.state_rows[0]
    assert row.mail_returned is not None and row.inperson is not None
    assert row.mail_returned + row.inperson == row.ballots_total


def test_all_mail_state_reports_no_request_stage(parsed):
    """Colorado mails every active voter a ballot, so there is no request to
    count. Blank, not zero — zero would claim nobody was sent one."""
    assert parsed.state_rows[0].mail_requested is None


def test_the_2022_workbook_parses_with_the_same_code():
    """Both cycles go through one parser; that is the point of matching on sheet
    and header text rather than cell position."""
    older = co.parse(BOOK_2022, 2022, date(2022, 11, 1))
    assert older.county_rows
    assert older.state_rows[0].ballots_total > 0


def test_2022_reports_no_mail_split_and_says_so_with_a_blank():
    """A real cross-cycle difference: the 2022 workbook ships no
    Returned_Mail_Ballots sheet (it carries In_Person_by_Party_County instead),
    so the mail cut is genuinely unreported that cycle.

    Blank, never 0 -- a 0 would claim not one Coloradan voted by mail in an
    all-mail state, which is the most obviously wrong number this page could
    print.
    """
    older = co.parse(BOOK_2022, 2022, date(2022, 11, 1))
    assert older.state_rows[0].mail_returned is None
    assert older.state_rows[0].inperson is not None


def test_the_row_is_dated_by_the_workbook_not_the_run(parsed):
    assert parsed.state_rows[0].day == date(2024, 10, 31)


def test_no_workbook_for_today_is_not_yet_published(monkeypatch):
    """The daily path before Colorado starts posting. Must STOP the ladder."""
    monkeypatch.setattr(co.COScraper, "_load", lambda self, day, **kw: None)
    with pytest.raises(NotYetPublished):
        co.COScraper().fetch(2026, date(2026, 9, 5))


# --------------------------------------------------------------------------
# The party-by-method crosstab
# --------------------------------------------------------------------------
def test_the_two_channel_matrices_are_published_as_a_crosstab(parsed):
    """Colorado ships the county x party matrix once per channel in 2024, so the
    cells exist and `schema.MethodDay` is where they go. They must add back up
    to the county row: this is the same ballots partitioned, not a second
    estimate of them."""
    cells = _methods.rows_of(parsed)
    assert {r.method for r in cells} == {"mail", "inperson"}
    by_county = {}
    for row in cells:
        entry = by_county.setdefault(row.county_fips, [0, 0, 0])
        entry[0] += row.ballots_total
        entry[1] += row.party_dem
        entry[2] += row.party_rep
    for county in parsed.county_rows:
        assert by_county[county.county_fips] == [
            county.ballots_total, county.party_dem, county.party_rep]


def test_the_2022_workbook_yields_one_band_and_the_other_is_not_invented():
    """⚠️ THE REFUSAL THAT KEEPS THIS TABLE HONEST.

    Colorado's 2022 workbook ships `In_Person_by_Party_County` and NO per-county
    mail matrix. `all_returned - in_person` is arithmetic and it is still a
    number Colorado did not print, which is exactly why `mail_returned` is blank
    on the 2022 county row too. So 2022 gets the in-person band and nothing
    else — and because Colorado is an all-mail state that band is under 1% of
    its ballots, which is what `counterfactual.reconciled_cells` refuses.
    """
    result = co.parse(BOOK_2022, 2022, date(2022, 11, 1))
    cells = _methods.rows_of(result)
    assert {r.method for r in cells} == {"inperson"}
    counties = {r.county_fips: r.ballots_total for r in result.county_rows}
    covered = sum(r.ballots_total for r in cells)
    assert covered < 0.05 * sum(counties.values())
