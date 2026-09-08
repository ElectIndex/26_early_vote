"""Delaware: the Department of Elections' "Voter Counts by Voting Method" PDF.

Two real fixtures, both of them captures of the SAME URL -- which is the point,
because Delaware overwrites that URL as voting happens and keeps no dated copies:

* `GE2024_VoterCountsByVotingMethod_20241028.pdf` -- the report as it stood on
  the fourth day of the 2024 general's early-vote window. It has **no
  Polling Place row at all**, which is the during-season shape this adapter
  exists to read.
* `GE2024_VoterCountsByVotingMethod_final.pdf` -- the same URL after the
  election, with the polling-place row present. It is the fixture that pins the
  one judgement call in the module: `ballots_total` must be absentee + early
  voting (247,172), never Delaware's own Total (426,177), which counts
  Election Day.

Both were fetched from the Internet Archive's copies of
`https://elections.delaware.gov/voter/registrationtotals/reports/pdfs/GE2024_GeneralElectionVoterCountsByVotingMethod.pdf`
and are byte-for-byte what Delaware served.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from ev.adapters import de
from ev.adapters._net import Missing
from ev.adapters.base import NotYetPublished, SchemaDrift, SourceError

FIXTURES = Path(__file__).parent / "fixtures" / "de"
DURING = FIXTURES / "GE2024_VoterCountsByVotingMethod_20241028.pdf"
FINAL = FIXTURES / "GE2024_VoterCountsByVotingMethod_final.pdf"

NEW_CASTLE, KENT, SUSSEX = "10003", "10001", "10005"


@pytest.fixture(scope="module")
def during():
    return de.parse(DURING.read_bytes(), 2024)


@pytest.fixture(scope="module")
def final():
    return de.parse(FINAL.read_bytes(), 2024)


# --------------------------------------------------------------------------
# The table
# --------------------------------------------------------------------------
def test_report_carries_its_own_as_of_date(during, final):
    assert during.as_of == date(2024, 10, 28)
    assert final.as_of == date(2024, 11, 5)


def test_county_columns_are_resolved_to_fips_in_the_reports_own_order(during):
    assert during.counties == [
        (NEW_CASTLE, "New Castle County"),
        (KENT, "Kent County"),
        (SUSSEX, "Sussex County"),
    ]


def test_during_season_numbers_are_delawares_own(during):
    assert during.rows["mail_returned"] == [14_999, 4_394, 10_533]
    assert during.rows["inperson"] == [16_957, 9_700, 30_324]
    assert during.statewide("mail_returned") == 29_926
    assert during.statewide("inperson") == 56_981


def test_polling_place_row_is_absent_before_election_day(during, final):
    # Delaware does not print the row until Election Day. Absent is absent: we
    # do not synthesise a zero for it.
    assert "electionday" not in during.rows
    assert final.rows["electionday"] == [117_422, 30_320, 31_263]


# --------------------------------------------------------------------------
# The rows we publish
# --------------------------------------------------------------------------
def test_state_row_totals_early_plus_absentee_not_delawares_total(final):
    row = de.to_result(final, 2024).state_rows[0]
    assert row.mail_returned == 37_656
    assert row.inperson == 209_516
    # Delaware's own Total for that report is 426,177 -- every ballot cast,
    # including 179,005 at the polling place. Publishing it as early voting
    # would treble the headline.
    assert row.ballots_total == 37_656 + 209_516 == 247_172


def test_state_row_during_the_window(during):
    row = de.to_result(during, 2024).state_rows[0]
    assert (row.day, row.ballots_total) == (date(2024, 10, 28), 86_907)


def test_party_fields_are_blank_not_zero(final):
    result = de.to_result(final, 2024)
    row = result.state_rows[0]
    assert (row.party_dem, row.party_rep, row.party_npa, row.party_oth) == (None,) * 4
    # Delaware DOES register by party; this particular report just does not
    # break it out, so blank is the honest answer and 0 would be a lie.
    assert all(c.party_dem is None for c in result.county_rows)


def test_mail_requested_is_blank(final):
    # The report counts absentee ballots CAST, not issued.
    assert de.to_result(final, 2024).state_rows[0].mail_requested is None


def test_county_rows_are_keyed_by_fips(final):
    rows = {row.county_fips: row for row in de.to_result(final, 2024).county_rows}
    assert set(rows) == {NEW_CASTLE, KENT, SUSSEX}
    assert rows[SUSSEX].county_name == "Sussex County"
    assert rows[SUSSEX].mail_returned == 12_645
    assert rows[SUSSEX].inperson == 90_196
    assert rows[SUSSEX].ballots_total == 102_841


def test_counties_sum_to_the_state_row(final):
    result = de.to_result(final, 2024)
    state = result.state_rows[0]
    assert sum(c.ballots_total for c in result.county_rows) == state.ballots_total
    assert sum(c.mail_returned for c in result.county_rows) == state.mail_returned
    assert sum(c.inperson for c in result.county_rows) == state.inperson


# --------------------------------------------------------------------------
# Which election is this?
# --------------------------------------------------------------------------
def test_a_report_for_another_election_is_not_yet_published():
    # The 2026 general's file does not exist yet (verified 404). If Delaware
    # ever serves the wrong year's report at that URL, this is what must happen
    # -- NotYetPublished stops the ladder, it does not publish 2024's numbers
    # under 2026.
    with pytest.raises(NotYetPublished) as caught:
        de.parse(FINAL.read_bytes(), 2026)
    assert "2026 General Election" in str(caught.value)
    assert "2024 General Election" in str(caught.value)


def test_html_is_not_a_report():
    with pytest.raises(SourceError):
        de.parse(b"<!doctype html><html><head><title>404</title></head>", 2024)


def test_non_pdf_bytes_are_a_source_error():
    with pytest.raises(SourceError):
        de.parse(b"PK\x03\x04 this is a spreadsheet, not a pdf" * 20, 2024)


# --------------------------------------------------------------------------
# Drift, tested on the table shape directly -- a PDF cannot be edited in a test
# --------------------------------------------------------------------------
HEADER = [
    "Tuesday, November 5, 2024",
    "3:41:10 PM",
    "2024 General Election",
    "New Castle              Kent            Sussex",
    "Voting Method    County    County    County    Total",
]


def _lines(*rows: str) -> list[str]:
    return [line.strip() for line in HEADER + list(rows)]


def test_an_unrecognised_method_row_is_drift():
    lines = _lines("Absentee 1 1 1 3", "Early Voting 2 2 2 6", "Drop Box 1 1 1 3",
                   "Total 4 4 4 12")
    counties, at = de._county_columns(lines)
    assert [f for f, _ in counties] == [NEW_CASTLE, KENT, SUSSEX]
    with pytest.raises(SchemaDrift) as caught:
        de._table(lines[at + 1:])
    assert "Drop Box" in str(caught.value)


def test_a_missing_method_row_is_drift():
    lines = _lines("Absentee 1 1 1 3", "Total 1 1 1 3")
    _, at = de._county_columns(lines)
    with pytest.raises(SchemaDrift) as caught:
        de._table(lines[at + 1:])
    assert "inperson" in str(caught.value)


def test_a_row_that_does_not_add_up_across_counties_is_drift():
    lines = _lines("Absentee 1 1 1 4", "Early Voting 2 2 2 6", "Total 3 3 3 9")
    _, at = de._county_columns(lines)
    rows, stated = de._table(lines[at + 1:])
    with pytest.raises(SchemaDrift) as caught:
        de._check_arithmetic(rows, stated)
    assert "Total column says 4" in str(caught.value)


def test_a_column_that_does_not_add_up_across_methods_is_drift():
    lines = _lines("Absentee 1 1 1 3", "Early Voting 2 2 2 6", "Total 3 3 4 10")
    _, at = de._county_columns(lines)
    rows, stated = de._table(lines[at + 1:])
    with pytest.raises(SchemaDrift):
        de._check_arithmetic(rows, stated)


def test_an_unreadable_but_number_shaped_row_is_drift():
    lines = _lines("Absentee 1 1 1 3", "Early Voting 2 2 2 6",
                   "3rd Party?? 9 9 9 27", "Total 3 3 3 9")
    _, at = de._county_columns(lines)
    with pytest.raises(SchemaDrift):
        de._table(lines[at + 1:])


def test_an_unknown_county_column_is_drift():
    lines = [line.strip() for line in HEADER[:3]] + [
        "New Castle              Kent            Wicomico",
        "Voting Method    County    County    County    Total",
    ]
    with pytest.raises(SchemaDrift) as caught:
        de._county_columns(lines)
    assert "Wicomico" in str(caught.value)


def test_a_fourth_county_column_is_drift():
    lines = [line.strip() for line in HEADER[:3]] + [
        "New Castle   Kent   Sussex   Somewhere",
        "Voting Method    County    County    County    County   Total",
    ]
    with pytest.raises(SchemaDrift):
        de._county_columns(lines)


def test_no_date_line_is_drift():
    lines = ["2024 General Election",
             "New Castle   Kent   Sussex",
             "Voting Method    County    County    County    Total",
             "Absentee 1 1 1 3", "Early Voting 2 2 2 6", "Total 3 3 3 9"]
    with pytest.raises(SchemaDrift):
        de._report_date(lines)


# --------------------------------------------------------------------------
# The adapter
# --------------------------------------------------------------------------
class _Fetcher:
    """Stands in for `_net.get`, recording what was asked for."""

    def __init__(self, bodies: dict[str, bytes]):
        self.bodies = bodies
        self.asked: list[str] = []

    def __call__(self, url, *, state, filename, **kwargs):
        self.asked.append(url)
        for marker, body in self.bodies.items():
            if marker in url:
                return body
        raise Missing(f"{state}: {url} returned 404")


def test_fetch_reads_the_literal_url_and_dates_rows_by_the_report(monkeypatch):
    fetch = _Fetcher({"index.html": b"<html><body>no report yet</body></html>",
                      "GE2024_": DURING.read_bytes()})
    monkeypatch.setattr(de, "get", fetch)
    result = de.DEScraper().fetch(2024, date(2024, 10, 31))
    assert [row.day for row in result.state_rows] == [date(2024, 10, 28)]
    assert result.state_rows[0].ballots_total == 86_907
    assert len(result.county_rows) == 3


def test_fetch_prefers_a_link_found_on_the_index_page(monkeypatch):
    # A real drift: the same report moved to another directory. Only the
    # literal path is a guess, so the link on the page wins.
    page = (b'<html><a href="/voter/registrationtotals/2026reports/'
            b'GE2024_GeneralElectionVoterCountsByVotingMethod.pdf">View Report</a></html>')
    fetch = _Fetcher({"index.html": page, "/2026reports/": FINAL.read_bytes()})
    monkeypatch.setattr(de, "get", fetch)
    result = de.DEScraper().fetch(2024, date(2024, 11, 30))
    assert result.state_rows[0].ballots_total == 247_172
    assert any("/2026reports/" in url for url in fetch.asked)


def test_a_primary_link_on_the_index_page_is_never_picked_up(monkeypatch):
    # Delaware links the CURRENT election's report from its home page, which for
    # most of an election year is the primary's.
    page = (b'<html><a href="/voter/registrationtotals/reports/pdfs/'
            b'PR2026_PrimaryElectionVoterCountsByVotingMethod.pdf">View Report</a></html>')
    fetch = _Fetcher({"index.html": page})
    monkeypatch.setattr(de, "get", fetch)
    with pytest.raises(NotYetPublished):
        de.DEScraper().fetch(2026, date(2026, 9, 6))
    assert not any("PR2026" in url for url in fetch.asked)


def test_a_missing_report_is_not_yet_published(monkeypatch):
    fetch = _Fetcher({"index.html": b"<html></html>"})
    monkeypatch.setattr(de, "get", fetch)
    with pytest.raises(NotYetPublished) as caught:
        de.DEScraper().fetch(2026, date(2026, 9, 6))
    assert "GE2026" in str(caught.value)


def test_a_report_dated_after_the_run_is_not_yet_published(monkeypatch):
    fetch = _Fetcher({"index.html": b"<html></html>", "GE2024_": FINAL.read_bytes()})
    monkeypatch.setattr(de, "get", fetch)
    with pytest.raises(NotYetPublished) as caught:
        de.DEScraper().fetch(2024, date(2024, 11, 1))
    assert "2024-11-05" in str(caught.value)


def test_fetch_history_refuses_a_cycle_delaware_never_published():
    with pytest.raises(NotYetPublished) as caught:
        de.DEScraper().fetch_history(2022)
    assert "starts with 2024" in str(caught.value)


def test_fetch_history_keeps_the_last_capture_of_each_report_date(monkeypatch):
    cdx = (b'[["timestamp","statuscode","digest"],'
           b'["20241028211523","200","A"],["20241130150318","200","B"]]')
    bodies = {
        "cdx/search": cdx,
        "20241028211523": DURING.read_bytes(),
        "20241130150318": FINAL.read_bytes(),
    }
    monkeypatch.setattr(de, "get", _Fetcher(bodies))
    result = de.DEScraper().fetch_history(2024)
    assert [row.day for row in result.state_rows] == [date(2024, 10, 28), date(2024, 11, 5)]
    assert [row.ballots_total for row in result.state_rows] == [86_907, 247_172]
    assert len(result.county_rows) == 6


def test_fetch_history_with_nothing_archived_is_not_yet_published(monkeypatch):
    monkeypatch.setattr(de, "get", _Fetcher({"cdx/search": b"[]"}))
    with pytest.raises(NotYetPublished):
        de.DEScraper().fetch_history(2024)


# --------------------------------------------------------------------------
# THE BLANK RULE on the one number this module computes itself
# --------------------------------------------------------------------------
def _report(rows: dict) -> de.Report:
    return de.Report(
        date(2024, 10, 28),
        [(NEW_CASTLE, "New Castle County"), (KENT, "Kent County"),
         (SUSSEX, "Sussex County")],
        rows,
    )


def test_a_missing_method_row_leaves_the_total_blank_not_short():
    """`ballots_total` is absentee + early voting, computed here rather than
    read off the report, so an absent component makes it UNKNOWN. Adding what
    is there and calling it the total would under-report by a whole method."""
    result = de.to_result(_report({"mail_returned": [1, 2, 3]}), 2024)
    row = result.state_rows[0]
    assert row.mail_returned == 6
    assert row.inperson is None
    assert row.ballots_total is None
    assert [c.ballots_total for c in result.county_rows] == [None, None, None]


def test_a_report_with_neither_method_row_publishes_no_zero():
    """⚠️ THE WORST SHAPE, and the one `(mail or 0) + (inperson or 0)` produced:
    a `ballots_total` of 0 sitting beside two blank method fields. Blank says
    "Delaware did not report this"; 0 says "nobody voted early", which of a
    state that cast 247,172 early ballots in 2024 is a confident lie."""
    result = de.to_result(_report({}), 2024)
    row = result.state_rows[0]
    assert (row.mail_returned, row.inperson) == (None, None)
    assert row.ballots_total is None
    assert all(c.ballots_total is None for c in result.county_rows)


def test_the_real_reports_are_unaffected(during, final):
    """The guard above changes nothing about a report Delaware actually served:
    `_table` refuses one that is missing either row, so both are always there."""
    assert de.to_result(during, 2024).state_rows[0].ballots_total == 86_907
    assert de.to_result(final, 2024).state_rows[0].ballots_total == 247_172


# --------------------------------------------------------------------------
# GUARD PARITY: the report's date must belong to the election it names
# --------------------------------------------------------------------------
def test_every_archived_2024_report_date_is_inside_the_window(during, final):
    for report in (during, final):
        de._check_window(report.as_of, 2024)          # does not raise


@pytest.mark.parametrize("day", [
    date(2023, 11, 30),      # before the cycle's window opens
    date(2025, 3, 1),        # a REPRINT, months later -- the real failure mode
    date(2026, 11, 3),       # next cycle's Election Day
])
def test_a_report_date_outside_the_cycle_is_drift(day):
    with pytest.raises(SchemaDrift) as caught:
        de._check_window(day, 2024)
    assert day.isoformat() in str(caught.value)


def _text(date_line: str, election: str = "2024 General Election") -> str:
    return "\n".join([
        f"                                        {date_line}",
        "                                        8:54:15 AM",
        "     Department of ElectionsState of Delaware",
        "     General Election Voter Counts by Voting Method",
        f"                {election}",
        "     New Castle              Kent            Sussex",
        "Voting Method     County               County           County      Total",
        "Absentee          14,999                4,394           10,533      29,926",
        "Early Voting      16,957                9,700           30,324      56,981",
        "        Total     31,956               14,094           40,857      86,907",
    ])


def test_parse_itself_refuses_an_out_of_cycle_report_date(monkeypatch):
    """The check lives in `parse`, so `fetch` and `fetch_history` cannot come
    apart on it -- which is what happened: `fetch` refuses a report dated after
    the day it was asked for, and `fetch_history` bounded the date not at all.
    """
    monkeypatch.setattr(de, "_read_text",
                        lambda body: _text("Saturday, March 1, 2025"))
    with pytest.raises(SchemaDrift) as caught:
        de.parse(b"%PDF-1.4 pretend", 2024)
    assert "2025-03-01" in str(caught.value)
    # ...and the same bytes with a real report date parse fine.
    monkeypatch.setattr(de, "_read_text",
                        lambda body: _text("Monday, October 28, 2024"))
    assert de.parse(b"%PDF-1.4 pretend", 2024).as_of == date(2024, 10, 28)


def test_an_out_of_cycle_capture_stops_the_backfill_rather_than_dating_a_row(
    monkeypatch,
):
    """End to end on the archive path. Before the guard this published a full
    2024 early electorate at days_to_election -116, which then became that
    cycle's final for everything that compares against it."""
    cdx = (b'[["timestamp","statuscode","digest"],'
           b'["20241028211523","200","A"],["20250301150318","200","B"]]')
    monkeypatch.setattr(de, "get", _Fetcher({
        "cdx/search": cdx,
        "20241028211523": DURING.read_bytes(),
        "20250301150318": b"%PDF-1.4 pretend a reprint",
    }))
    monkeypatch.setattr(de, "_read_text", lambda body: _text(
        "Monday, October 28, 2024" if b"reprint" not in body
        else "Saturday, March 1, 2025"))
    with pytest.raises(SchemaDrift):
        de.DEScraper().fetch_history(2024)


def test_the_archive_is_walked_oldest_first_whatever_order_cdx_answers_in(
    monkeypatch,
):
    """"The last capture of a report date wins" is only true if the captures
    arrive in order, and nothing was making them."""
    cdx = (b'[["timestamp","statuscode","digest"],'
           b'["20241130150318","200","B"],["20241028211523","200","A"]]')
    monkeypatch.setattr(de, "get", _Fetcher({
        "cdx/search": cdx,
        "20241028211523": DURING.read_bytes(),
        "20241130150318": FINAL.read_bytes(),
    }))
    result = de.DEScraper().fetch_history(2024)
    assert [row.day for row in result.state_rows] == [
        date(2024, 10, 28), date(2024, 11, 5)]
    assert [row.ballots_total for row in result.state_rows] == [86_907, 247_172]


def test_the_cdx_cache_filename_names_the_cycle(monkeypatch):
    """One filename for every cycle made 2024's index and 2026's the same file
    on disk."""
    asked: list[str] = []

    def fake(url, *, state, filename, **kwargs):
        asked.append(filename)
        return b"[]"

    monkeypatch.setattr(de, "get", fake)
    de._archive_stamps(de.REPORT_URL.format(cycle=2024), 2024)
    de._archive_stamps(de.REPORT_URL.format(cycle=2026), 2026)
    assert asked == ["cdx-GE2024-report.json", "cdx-GE2026-report.json"]


def test_one_unreadable_capture_does_not_cost_the_rest_of_the_curve(monkeypatch):
    """The Wayback Machine sometimes answers a capture with an error page under
    HTTP 200. That is the same "this capture is unusable" as a download failure,
    which was already skipped one line earlier -- but it arrived as a
    SourceError out of `parse` and took the whole archive with it."""
    cdx = (b'[["timestamp","statuscode","digest"],'
           b'["20241028211523","200","A"],["20241101000000","200","B"],'
           b'["20241130150318","200","C"]]')
    monkeypatch.setattr(de, "get", _Fetcher({
        "cdx/search": cdx,
        "20241028211523": DURING.read_bytes(),
        "20241101000000": b"<!doctype html><html><head>oops</head>" + b"x" * 2000,
        "20241130150318": FINAL.read_bytes(),
    }))
    result = de.DEScraper().fetch_history(2024)
    assert [row.day for row in result.state_rows] == [
        date(2024, 10, 28), date(2024, 11, 5)]


def test_drift_in_one_capture_still_stops_everything(monkeypatch):
    """The other half of the same change: SchemaDrift is a SourceError, so the
    skip above must not be able to swallow it."""
    cdx = (b'[["timestamp","statuscode","digest"],'
           b'["20241028211523","200","A"],["20241130150318","200","B"]]')
    monkeypatch.setattr(de, "get", _Fetcher({
        "cdx/search": cdx,
        "20241028211523": DURING.read_bytes(),
        "20241130150318": b"%PDF-1.4 pretend a reprint",
    }))
    monkeypatch.setattr(de, "_read_text", lambda body: _text(
        "Monday, October 28, 2024" if b"reprint" not in body
        else "Saturday, March 1, 2025"))
    with pytest.raises(SchemaDrift):
        de.DEScraper().fetch_history(2024)
