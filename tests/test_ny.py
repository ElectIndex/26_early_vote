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
    with pytest.raises(NotYetPublished, match="has not opened its 2026 general-election section"):
        ny.parse(PRIM26, 2026, TODAY)


def test_the_refusal_does_not_dump_the_pages_headings_at_a_reader():
    """⚠️ `ladder` puts str(exc) into ev_status.json, and the state panel PRINTS it.

    This message used to append `(its headings are [...])` -- seventeen items of
    the Board's site navigation -- so New York's page read
    "...its headings are ['Register', 'Vote', 'NYC Elections', ...]" under the
    state's name. The list still exists and is still logged at DEBUG, because
    the day the Board renames its section is exactly what it is for. It just
    does not belong on a public page.
    """
    with pytest.raises(NotYetPublished) as exc:
        ny.parse(PRIM26, 2026, TODAY)
    message = str(exc.value)
    assert "headings" not in message
    # A bracket is the tell: this message carried a Python list repr.
    assert "[" not in message and "]" not in message
    assert len(message) < 140, f"too long for a page: {len(message)} chars"


def test_the_2024_general_is_refused_when_asked_for_as_2026():
    with pytest.raises(NotYetPublished, match="has not opened its 2026 general-election section"):
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


def test_fetch_history_refuses_the_cycle_that_is_still_running():
    with pytest.raises(NotYetPublished, match="not an archived cycle"):
        ny.NYScraper().fetch_history(date.today().year)


def test_fetch_history_refuses_a_cycle_before_the_first_tracked_one():
    with pytest.raises(NotYetPublished, match="before the first tracked cycle"):
        ny.NYScraper().fetch_history(2020)


def test_fetch_reads_the_page_and_parses_it(monkeypatch):
    monkeypatch.setattr(ny.NYScraper, "_load", lambda self, **k: GEN24)
    result = ny.NYScraper().fetch(2024, date(2024, 11, 2))
    assert len(result.county_rows) == 30
    assert result.state_rows == []


# --------------------------------------------------------------------------
# The archived borough curve, rebuilt from the Internet Archive
#
# The Board overwrites this URL each election, so the whole of New York's county
# history is here. Every byte is a real capture and every stamp is the real one
# the CDX index returns; nothing below touches the network.
# --------------------------------------------------------------------------
FULL24 = (FIX / "early-voting-check-ins_2024-11-04_general-complete.html").read_bytes()
ODD22 = (FIX / "early-voting-check-ins_2022-10-31_general-newyork-label.html").read_bytes()
CDX24 = (FIX / "cdx_2024_check-ins.json").read_bytes()

#: Three real 2024 captures. The September one predates the general's section
#: entirely (the Board still had the June primary up), the November 2nd one is
#: six days of nine, and the November 4th one -- taken the day after early
#: voting closed -- carries all nine.
CAPTURES_2024 = {
    "20240913023856": PRIM26,
    "20241102072441": GEN24,
    "20241104114451": FULL24,
}


@pytest.fixture
def archive(monkeypatch):
    def fake_get(url, *, state, filename, **kwargs):
        if url.startswith(ny.CDX_URL):
            return CDX24
        for stamp, body in CAPTURES_2024.items():
            if stamp in url:
                return body
        raise AssertionError(f"unexpected fetch of {url}")

    monkeypatch.setattr(ny, "get", fake_get)
    monkeypatch.setattr(ny, "archive_stamps", lambda cycle: sorted(CAPTURES_2024))


def test_one_late_capture_rebuilds_the_whole_2024_curve(archive):
    """Nine days of five boroughs out of an archive that holds six captures --
    the series is cumulative, so its sparseness costs nothing."""
    result = ny.NYScraper().fetch_history(2024)
    days = sorted({r.day for r in result.county_rows})
    assert days == ([date(2024, 10, d) for d in range(26, 32)]
                    + [date(2024, 11, d) for d in (1, 2, 3)])
    assert len(result.county_rows) == 9 * 5


def test_the_archived_final_day_is_the_boards_own_cumulative_figure(archive):
    result = ny.NYScraper().fetch_history(2024)
    last = [r for r in result.county_rows if r.day == date(2024, 11, 3)]
    assert sum(r.ballots_total for r in last) == 1_089_328
    manhattan = next(r for r in last if r.county_fips == "36061")
    assert (manhattan.county_name, manhattan.ballots_total) == (
        "New York County", 282_533
    )


def test_the_five_of_sixty_two_rule_holds_on_the_archive_path_too(archive):
    """The whole reason this adapter exists in its partial form. A backfill must
    not be the back door through which a five-county figure becomes New York."""
    result = ny.NYScraper().fetch_history(2024)
    assert result.state_rows == []
    assert result.demo_rows == []


def test_a_capture_predating_the_general_is_skipped_not_published(archive):
    """The September capture still had the June primary up. Its 172,743
    check-ins must never appear under the November general."""
    result = ny.NYScraper().fetch_history(2024)
    assert all(r.day.month in (10, 11) for r in result.county_rows)
    assert all(r.day.year == 2024 for r in result.county_rows)


def test_a_later_capture_of_the_same_day_wins(archive):
    """Days 1-6 are in both the 11-02 and the 11-04 capture. The later reading
    is the one that is true of that day."""
    result = ny.NYScraper().fetch_history(2024)
    day_one = [r for r in result.county_rows if r.day == date(2024, 10, 26)]
    assert len(day_one) == 5
    assert sum(r.ballots_total for r in day_one) == 140_145


def test_archived_rows_obey_the_blank_rule(archive):
    for row in ny.NYScraper().fetch_history(2024).county_rows:
        assert row.mail_returned is None
        assert row.inperson == row.ballots_total
        for field in ("party_dem", "party_rep", "party_oth", "party_npa"):
            assert getattr(row, field) is None


def test_an_archive_with_nothing_in_it_is_not_yet_published(monkeypatch):
    monkeypatch.setattr(ny, "archive_stamps", lambda cycle: [])
    with pytest.raises(NotYetPublished, match="nothing archived"):
        ny.NYScraper().fetch_history(2024)


def test_every_capture_drifting_is_reported_as_drift_not_as_absence(monkeypatch):
    drifted = GEN24.decode().replace("<li>Queens -\u00a031,671</li>",
                                     "<li>Nassau -\u00a031,671</li>").encode()
    monkeypatch.setattr(ny, "archive_stamps", lambda cycle: ["20241102072441"])
    monkeypatch.setattr(ny, "get", lambda url, **k: drifted)
    with pytest.raises(SchemaDrift, match="unrecognised borough"):
        ny.NYScraper().fetch_history(2024)


# --------------------------------------------------------------------------
# "New York" is Manhattan, and it is proved by value rather than by the name
# --------------------------------------------------------------------------
def test_the_board_sometimes_writes_new_york_for_manhattan():
    """The 2022-10-31 capture labels Manhattan "New York". Before this was
    measured the whole capture raised "unrecognised borough" -- correctly, under
    rule 3, which is what sent it to be checked instead of guessed at."""
    result = ny.parse(ODD22, 2022, date(2022, 10, 31))
    assert {r.county_fips for r in result.county_rows} == {
        "36005", "36047", "36061", "36081", "36085"
    }
    manhattan = [r for r in result.county_rows if r.county_fips == "36061"]
    assert [r.ballots_total for r in manhattan] == [16_314, 32_020]


def test_the_new_york_label_agrees_with_the_capture_that_says_manhattan():
    """The identification is not a guess about a name: the same two days in the
    capture that writes "Manhattan" carry exactly these numbers."""
    odd = {r.day: r.ballots_total
           for r in ny.parse(ODD22, 2022, date(2022, 10, 31)).county_rows
           if r.county_fips == "36061"}
    named = {r.day: r.ballots_total
             for r in ny.parse(GEN22, 2022, date(2022, 11, 3)).county_rows
             if r.county_fips == "36061"}
    assert odd[date(2022, 10, 29)] == named[date(2022, 10, 29)] == 16_314
    assert odd[date(2022, 10, 30)] == named[date(2022, 10, 30)] == 32_020


def test_the_borough_aliases_do_not_inflate_the_completeness_check():
    """CITY_COUNTIES counts DISTINCT counties, so adding an alias cannot
    quietly raise the bar a day's borough list has to clear."""
    assert ny.CITY_COUNTIES == 5
    assert len(ny.BOROUGH_COUNTIES) == 7  # two aliases


# --------------------------------------------------------------------------
# GUARD PARITY: the archive path runs the SAME parse the live path does
# --------------------------------------------------------------------------
def test_a_captures_as_of_is_its_own_stamp():
    assert ny.stamp_day("20241104114451") == date(2024, 11, 4)
    with pytest.raises(SourceError, match="not a Wayback timestamp"):
        ny.stamp_day("20241104")


def test_the_run_date_filter_stays_live_on_the_archive_path(monkeypatch):
    """A capture stamped October 28 cannot publish October 31's check-ins, on
    this path exactly as on the live one."""
    monkeypatch.setattr(ny, "archive_stamps", lambda cycle: ["20241028120000"])
    monkeypatch.setattr(ny, "get", lambda url, **k: FULL24)
    result = ny.NYScraper().fetch_history(2024)
    assert max(r.day for r in result.county_rows) == date(2024, 10, 28)
