"""New York: the NYC Board of Elections' early-voting check-ins.

Three fixtures, all the real content block verbatim -- the `<h2>` that names the
election through the last `<hr />` of the section:

* `early-voting-check-ins_2024-11-02_general.html` -- the 2024 general, days 1-6
  of nine, captured mid-early-voting.
* `early-voting-check-ins_2022-11-03_general.html` -- the 2022 general, whose
  layout puts the day's total on its own line rather than inside the bold one.
* `early-voting-check-ins_2026-09-06_primary.html` -- the live page on
  2026-09-06, which is the June PRIMARY. That is the path this adapter takes
  every day until the Board posts the general.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from ev.adapters import ny
from ev.adapters.base import NotYetPublished, SchemaDrift, SourceError

FIX = Path(__file__).parent / "fixtures" / "ny"
GEN24 = (FIX / "early-voting-check-ins_2024-11-02_general.html").read_bytes()
GEN22 = (FIX / "early-voting-check-ins_2022-11-03_general.html").read_bytes()
PRIM26 = (FIX / "early-voting-check-ins_2026-09-06_primary.html").read_bytes()

TODAY = date(2026, 9, 6)


# --------------------------------------------------------------------------
# PARTIAL COVERAGE is the defining fact about this source
# --------------------------------------------------------------------------
def test_new_york_never_gets_a_statewide_row_from_five_counties():
    """NYC is five of New York's sixty-two counties. Nothing in StateDay can
    say so, and a five-county figure published there would be read as New York."""
    result = ny.parse(GEN24, 2024, date(2024, 11, 2))
    assert result.state_rows == []
    assert result.county_rows
    assert (ny.CITY_COUNTIES, ny.STATE_COUNTIES) == (5, 62)


def test_the_five_boroughs_resolve_to_five_new_york_county_fips():
    result = ny.parse(GEN24, 2024, date(2024, 11, 2))
    assert {r.county_fips for r in result.county_rows} == {
        "36005", "36047", "36061", "36081", "36085"
    }
    names = {r.county_fips: r.county_name for r in result.county_rows}
    assert names["36061"] == "New York County"      # Manhattan
    assert names["36047"] == "Kings County"         # Brooklyn
    assert names["36085"] == "Richmond County"      # Staten Island


# --------------------------------------------------------------------------
# One fetch rebuilds the whole curve
# --------------------------------------------------------------------------
def test_one_fetch_gives_every_day_of_the_period_so_far():
    result = ny.parse(GEN24, 2024, date(2024, 11, 2))
    days = sorted({r.day for r in result.county_rows})
    assert days == [date(2024, 10, d) for d in range(26, 32)]
    assert len(result.county_rows) == 6 * 5


def test_the_series_is_cumulative():
    result = ny.parse(GEN24, 2024, date(2024, 11, 2))
    manhattan = [r.ballots_total for r in result.county_rows
                 if r.county_fips == "36061"]
    assert manhattan == [38_237, 71_321, 106_870, 136_206, 164_107, 190_542]
    assert manhattan == sorted(manhattan)


def test_as_of_truncates_the_series():
    result = ny.parse(GEN24, 2024, date(2024, 10, 28))
    assert max(r.day for r in result.county_rows) == date(2024, 10, 28)
    assert len(result.county_rows) == 3 * 5


def test_check_ins_are_in_person_and_mail_stays_blank():
    """The city's absentee ballots are counted by the counties and are not on
    this page at all -- blank, never 0."""
    for row in ny.parse(GEN24, 2024, date(2024, 11, 2)).county_rows:
        assert row.inperson == row.ballots_total
        assert row.mail_returned is None


def test_new_york_enrols_by_party_but_this_page_does_not_break_it_out():
    for row in ny.parse(GEN24, 2024, date(2024, 11, 2)).county_rows:
        assert (row.party_dem, row.party_rep, row.party_npa, row.party_oth) == (
            None, None, None, None
        )


# --------------------------------------------------------------------------
# The 2022 layout, whose day total sits on its own line
# --------------------------------------------------------------------------
def test_the_2022_layout_parses_too():
    result = ny.parse(GEN22, 2022, date(2022, 11, 3))
    days = sorted({r.day for r in result.county_rows})
    assert days[0] == date(2022, 10, 29)
    assert days[-1] == date(2022, 11, 2)
    last = [r for r in result.county_rows if r.day == days[-1]]
    assert sum(r.ballots_total for r in last) == 212_746


def test_the_2024_days_reconcile_to_the_boards_own_cumulative_figure():
    result = ny.parse(GEN24, 2024, date(2024, 11, 2))
    last = [r for r in result.county_rows if r.day == date(2024, 10, 31)]
    assert sum(r.ballots_total for r in last) == 701_402


# --------------------------------------------------------------------------
# The heading is the only label, and it names the year
# --------------------------------------------------------------------------
def test_the_primary_is_up_today_and_that_stops_the_ladder():
    with pytest.raises(NotYetPublished, match="no 2026 general-election section"):
        ny.parse(PRIM26, 2026, TODAY)


def test_the_2024_general_is_refused_when_asked_for_as_2026():
    with pytest.raises(NotYetPublished, match="no 2026 general-election section"):
        ny.parse(GEN24, 2026, TODAY)


def test_a_run_off_or_special_heading_is_never_the_general():
    page = GEN24.decode().replace("General Election 2024",
                                  "Special General Election 2024")
    with pytest.raises(NotYetPublished):
        ny.parse(page.encode(), 2024, date(2024, 11, 2))


# --------------------------------------------------------------------------
# Every day must reconcile, and every borough must be there
# --------------------------------------------------------------------------
def test_a_day_whose_boroughs_do_not_sum_to_the_boards_total_is_drift():
    page = GEN24.decode().replace("<li>Bronx -\u00a016,462</li>",
                                  "<li>Bronx -\u00a016,463</li>")
    with pytest.raises(SchemaDrift, match="the Board's own figure"):
        ny.parse(page.encode(), 2024, date(2024, 11, 2))


def test_a_missing_borough_is_drift():
    page = GEN24.decode().replace("<li>Staten Island -\u00a013,486</li>", "", 1)
    with pytest.raises(SchemaDrift, match="lists 4 boroughs"):
        ny.parse(page.encode(), 2024, date(2024, 11, 2))


def test_an_unrecognised_borough_is_drift_not_a_guess():
    page = GEN24.decode().replace("<li>Queens -\u00a031,671</li>",
                                  "<li>Nassau -\u00a031,671</li>")
    with pytest.raises(SchemaDrift, match="unrecognised borough"):
        ny.parse(page.encode(), 2024, date(2024, 11, 2))


def test_a_day_dated_nowhere_near_the_election_is_drift():
    page = GEN24.decode().replace("October 26, 2024 - Day 1",
                                  "January 26, 2024 - Day 1")
    with pytest.raises(SchemaDrift, match="nowhere near 2024-11-05"):
        ny.parse(page.encode(), 2024, date(2024, 11, 2))


def test_a_section_with_no_days_yet_stops_the_ladder():
    page = '<h2 class="center">General Election 2026</h2><p> </p>'
    with pytest.raises(NotYetPublished, match="no early-voting days yet"):
        ny.parse(page.encode(), 2026, TODAY)


def test_days_after_the_run_are_not_published():
    with pytest.raises(NotYetPublished, match="posted no 2024 general check-ins"):
        ny.parse(GEN24, 2024, date(2024, 10, 25))


def test_a_body_that_is_not_the_page_falls_through():
    with pytest.raises(SourceError, match="did not come back as a page"):
        ny.parse(b'{"error":"nope"}', 2026, TODAY)


# --------------------------------------------------------------------------
# Adapter wiring
# --------------------------------------------------------------------------
def test_adapter_identity():
    scraper = ny.NYScraper()
    assert (scraper.state, scraper.name, scraper.tier) == ("NY", "ny-nycboe", 1)


def test_there_is_no_archive_to_backfill_from():
    with pytest.raises(NotYetPublished, match="overwrites its check-in page"):
        ny.NYScraper().fetch_history(2024)


def test_fetch_reads_the_page_and_parses_it(monkeypatch):
    monkeypatch.setattr(ny.NYScraper, "_load", lambda self, **k: GEN24)
    result = ny.NYScraper().fetch(2024, date(2024, 11, 2))
    assert len(result.county_rows) == 30
    assert result.state_rows == []
