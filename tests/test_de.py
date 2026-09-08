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

Two more, from the 2022 general -- a DIFFERENT directory, a different filename
and a party column (see the module docstring in `de.py` for how that cycle came
to be written off twice):

* `GE2022_VoterParticipationByVotingMethod_20221104.pdf` -- the flat layout, one
  row per (method, party), four days out.
* `GE2022_VoterParticipationByVotingMethod_20221108.pdf` -- Election Day, the
  SAME URL, re-laid-out as one block per method with the party order reversed.
  It is the fixture that pins reading the shape off the header rather than off
  the cycle.

Both from the Archive's copies of
`https://elections.delaware.gov/reports/pdfs/GE2022_Report_VoterCountsByVotingMethod.pdf`.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from ev.adapters import _methods, de
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


#: The 2022 header, which carries a second label column. Everything above it is
#: identical, which is why the layout is inferred from the header and not the
#: cycle.
PARTY_HEADER = HEADER[:-1] + [
    "Voting Method    Political Party    County    County    County    Total"]


def _lines(*rows: str, party: bool = False) -> list[str]:
    head = PARTY_HEADER if party else HEADER
    return [line.strip() for line in head + list(rows)]


def test_an_unrecognised_method_row_is_drift():
    lines = _lines("Absentee 1 1 1 3", "Early Voting 2 2 2 6", "Drop Box 1 1 1 3",
                   "Total 4 4 4 12")
    counties, at = de._county_columns(lines)
    assert [f for f, _ in counties] == [NEW_CASTLE, KENT, SUSSEX]
    with pytest.raises(SchemaDrift) as caught:
        de._table(lines[at:])
    assert "Drop Box" in str(caught.value)


def test_a_missing_method_row_is_drift():
    lines = _lines("Absentee 1 1 1 3", "Total 1 1 1 3")
    _, at = de._county_columns(lines)
    with pytest.raises(SchemaDrift) as caught:
        de._table(lines[at:])
    assert "inperson" in str(caught.value)


def test_a_row_that_does_not_add_up_across_counties_is_drift():
    lines = _lines("Absentee 1 1 1 4", "Early Voting 2 2 2 6", "Total 3 3 3 9")
    _, at = de._county_columns(lines)
    rows, stated, _parties = de._table(lines[at:])
    with pytest.raises(SchemaDrift) as caught:
        de._check_arithmetic(rows, stated)
    assert "Total column says 4" in str(caught.value)


def test_a_column_that_does_not_add_up_across_methods_is_drift():
    lines = _lines("Absentee 1 1 1 3", "Early Voting 2 2 2 6", "Total 3 3 4 10")
    _, at = de._county_columns(lines)
    rows, stated, _parties = de._table(lines[at:])
    with pytest.raises(SchemaDrift):
        de._check_arithmetic(rows, stated)


def test_an_unreadable_but_number_shaped_row_is_drift():
    lines = _lines("Absentee 1 1 1 3", "Early Voting 2 2 2 6",
                   "3rd Party?? 9 9 9 27", "Total 3 3 3 9")
    _, at = de._county_columns(lines)
    with pytest.raises(SchemaDrift):
        de._table(lines[at:])


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
    # 2020 is before Delaware had in-person early voting at all, so there is no
    # report to want. 2022 IS published -- see the 2022 block below.
    with pytest.raises(NotYetPublished) as caught:
        de.DEScraper().fetch_history(2020)
    assert "2022" in str(caught.value)


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
    de._archive_stamps(de.report_url(2022), 2022)
    de._archive_stamps(de.report_url(2024), 2024)
    de._archive_stamps(de.report_url(2026), 2026)
    assert asked == ["cdx-GE2022-report.json", "cdx-GE2024-report.json",
                     "cdx-GE2026-report.json"]


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


# --------------------------------------------------------------------------
# 2022: a different directory, a different filename, and a party column
#
# The two fixtures below are the same URL two report dates apart and are laid
# out DIFFERENTLY from each other -- flat during the season, one block per
# method on Election Day -- which is the whole reason `_table` infers the shape
# from the header rather than from the cycle. Every number asserted here is
# read off Delaware's own PDF.
# --------------------------------------------------------------------------
DURING_2022 = FIXTURES / "GE2022_VoterParticipationByVotingMethod_20221104.pdf"
FINAL_2022 = FIXTURES / "GE2022_VoterParticipationByVotingMethod_20221108.pdf"


@pytest.fixture(scope="module")
def during22():
    return de.parse(DURING_2022.read_bytes(), 2022)


@pytest.fixture(scope="module")
def final22():
    return de.parse(FINAL_2022.read_bytes(), 2022)


def test_the_2022_report_lives_at_its_own_url():
    """It moved directory AND naming convention between the cycles, and one URL
    template for both is how this cycle came to be written off twice."""
    assert de.report_url(2022) == (
        "https://elections.delaware.gov/reports/pdfs/"
        "GE2022_Report_VoterCountsByVotingMethod.pdf")
    assert de.report_url(2024) == (
        "https://elections.delaware.gov/voter/registrationtotals/reports/pdfs/"
        "GE2024_GeneralElectionVoterCountsByVotingMethod.pdf")


def test_fetch_and_fetch_history_ask_for_the_same_2022_url(monkeypatch):
    """⚠️ GUARD PARITY on the URL itself. A backfill that hard-coded the 2024
    path would find nothing for 2022 and report it as absence."""
    fetch = _Fetcher({"index.html": b"<html></html>",
                      "GE2022_Report_": DURING_2022.read_bytes()})
    monkeypatch.setattr(de, "get", fetch)
    de.DEScraper().fetch(2022, date(2022, 11, 4))
    assert any("/reports/pdfs/GE2022_Report_" in url for url in fetch.asked)
    assert not any("registrationtotals" in url for url in fetch.asked
                   if "GE2022" in url)


def test_2022_report_dates_are_delawares_own(during22, final22):
    # NOT the capture dates (2022-11-05 and 2022-11-10). Delaware stamps the
    # report and that stamp is the day the row belongs to.
    assert during22.as_of == date(2022, 11, 4)
    assert final22.as_of == date(2022, 11, 8)


def test_2022_county_columns_resolve_in_the_reports_own_order(during22, final22):
    for report in (during22, final22):
        assert report.counties == [
            (NEW_CASTLE, "New Castle County"),
            (KENT, "Kent County"),
            (SUSSEX, "Sussex County"),
        ]


def test_the_flat_2022_layout_is_read_row_by_row(during22):
    """One row per (method, party), each naming its own method."""
    assert during22.by_party["mail_returned"] == {
        "dem": [6_788, 2_064, 4_180],
        "rep": [1_722, 738, 2_246],
        "oth": [1_236, 373, 984],
    }
    assert during22.by_party["inperson"] == {
        "dem": [7_973, 3_120, 8_772],
        "rep": [2_198, 1_755, 7_191],
        "oth": [2_063, 1_183, 3_812],
    }
    # ...and the method rows are SUMMED from them, never guessed.
    assert during22.rows["mail_returned"] == [9_746, 3_175, 7_410]
    assert during22.rows["inperson"] == [12_234, 6_058, 19_775]
    assert during22.rows["total"] == [21_980, 9_233, 27_185]


def test_the_blocked_election_day_2022_layout_is_read_the_same_way(final22):
    """Same numbers, re-laid-out: a header per method, party rows carrying no
    method label, a Total per block, and a Grand Total closing the table. Note
    Delaware also reverses the party order between the two layouts, which is
    why rows are keyed by their own label and never by position."""
    assert final22.by_party["mail_returned"] == {
        "oth": [1_451, 454, 1_115],
        "rep": [1_943, 850, 2_443],
        "dem": [7_575, 2_277, 4_489],
    }
    assert final22.rows["mail_returned"] == [10_969, 3_581, 8_047]
    assert final22.rows["inperson"] == [19_293, 8_725, 28_177]
    # The polling place appears only on Election Day, exactly as in 2024.
    assert final22.rows["electionday"] == [96_579, 29_111, 45_032]
    assert final22.rows["total"] == [126_841, 41_417, 81_256]


def test_2022_ballots_total_still_excludes_the_polling_place(during22, final22):
    """The one judgement call in this module, holding on the cycle that makes it
    hurt most: Delaware's own Grand Total on 2022-11-08 is 249,514, of which
    170,722 were cast at a polling place ON Election Day."""
    assert de.to_result(during22, 2022).state_rows[0].ballots_total == 58_398
    row = de.to_result(final22, 2022).state_rows[0]
    assert row.ballots_total == 78_792
    assert (row.mail_returned, row.inperson) == (22_597, 56_195)
    assert sum(final22.rows["total"]) == 249_514


def test_2022_publishes_real_party_numbers(during22, final22):
    row = de.to_result(during22, 2022).state_rows[0]
    assert (row.party_dem, row.party_rep, row.party_oth) == (32_897, 15_850, 9_651)
    assert row.party_dem + row.party_rep + row.party_oth == row.ballots_total
    row = de.to_result(final22, 2022).state_rows[0]
    assert (row.party_dem, row.party_rep, row.party_oth) == (43_248, 22_089, 13_455)
    assert row.party_dem + row.party_rep + row.party_oth == row.ballots_total


def test_the_party_split_excludes_the_polling_place_too(final22):
    """`party_dem` must be the same electorate `ballots_total` is. Delaware cast
    76,349 Democratic ballots at the polling place on 2022-11-08; folding those
    in would make the party fields describe Election Day and the total describe
    early voting."""
    assert final22.party("electionday", "dem", 0) == 51_750
    assert final22.party_early("dem") == 14_341 + 28_907


def test_delaware_never_publishes_an_unaffiliated_number(during22, final22):
    """⚠️ THE BLANK RULE. Delaware prints three buckets and the third is
    "OTHER" -- every registrant who is neither a Democrat nor a Republican, its
    large No Party file included. `party_oth` carries the word Delaware printed;
    `party_npa` stays blank because Delaware does not report it."""
    for report in (during22, final22):
        result = de.to_result(report, 2022)
        assert result.state_rows[0].party_npa is None
        assert all(row.party_npa is None for row in result.county_rows)
        assert all(row.party_npa is None for row in _methods.rows_of(result))


def test_2022_county_rows_carry_their_own_party_split(during22):
    result = de.to_result(during22, 2022)
    by_fips = {row.county_fips: row for row in result.county_rows}
    sussex = by_fips[SUSSEX]
    assert sussex.ballots_total == 27_185
    assert (sussex.mail_returned, sussex.inperson) == (7_410, 19_775)
    # 4,180 absentee + 8,772 early Democrats; 2,246 + 7,191 Republicans.
    assert (sussex.party_dem, sussex.party_rep, sussex.party_oth) == (
        12_952, 9_437, 4_796)
    assert sussex.party_dem + sussex.party_rep + sussex.party_oth == 27_185


def test_2022_publishes_the_party_by_method_crosstab(during22):
    """The cell of the table `CountyDay` cannot hold. See schema.MethodDay."""
    rows = _methods.rows_of(de.to_result(during22, 2022))
    assert len(rows) == 6                      # 3 counties x 2 counted methods
    assert {row.method for row in rows} == {"mail", "inperson"}
    cell = next(r for r in rows
                if r.county_fips == NEW_CASTLE and r.method == "inperson")
    assert cell.ballots_total == 12_234
    assert (cell.party_dem, cell.party_rep, cell.party_oth) == (7_973, 2_198, 2_063)
    assert cell.day == date(2022, 11, 4)
    # The polling place is not a `normalize` method and is never published here.
    assert all(row.method != "electionday" for row in rows)


def test_2024_carries_no_party_column_and_says_so_with_blanks(during, final):
    """The 2024 report simply does not have the column. Blank, never zero, and
    no crosstab rows at all rather than a table of zeros."""
    for report in (during, final):
        assert report.by_party == {}
        result = de.to_result(report, 2024)
        row = result.state_rows[0]
        assert (row.party_dem, row.party_rep, row.party_oth, row.party_npa) == (
            None, None, None, None)
        assert all(c.party_dem is None for c in result.county_rows)
        assert _methods.rows_of(result) == []


def test_a_2022_party_row_that_does_not_add_up_is_drift():
    """Every 2022 row prints its own three counties AND their total, so each one
    carries its own proof the columns were sliced where Delaware put them."""
    lines = _lines("Absentee DEMOCRATIC 1 1 1 4",
                   "Absentee REPUBLICAN 1 1 1 3",
                   "Early Voting DEMOCRATIC 2 2 2 6",
                   "Total 5 5 5 15", party=True)
    _, at = de._county_columns(lines)
    with pytest.raises(SchemaDrift) as caught:
        de._table(lines[at:])
    assert "Total column says 4" in str(caught.value)


def test_an_unrecognised_2022_party_is_drift():
    """Rule 3: if Delaware ever splits OTHER into No Party and the minor
    parties, we are told rather than quietly folding one into the other."""
    lines = _lines("Absentee INDEPENDENT PARTY OF DELAWARE 1 1 1 3", party=True)
    _, at = de._county_columns(lines)
    with pytest.raises(SchemaDrift) as caught:
        de._table(lines[at:])
    assert "independent party of delaware" in str(caught.value).lower()


def test_a_no_party_row_would_be_published_as_unaffiliated():
    """The other half of the rule above: `normalize` DOES know "NO PARTY", so if
    Delaware ever splits OTHER we start publishing `party_npa` rather than
    raising. Rule 3 is about labels we do not recognise, not about new ones."""
    lines = _lines("Absentee NO PARTY 1 1 1 3",
                   "Absentee DEMOCRATIC 1 1 1 3",
                   "Early Voting DEMOCRATIC 2 2 2 6",
                   "Total 4 4 4 12", party=True)
    _, at = de._county_columns(lines)
    _rows, _stated, parties = de._table(lines[at:])
    assert parties["mail_returned"]["npa"] == [1, 1, 1]


def test_a_block_total_that_disagrees_with_its_party_rows_is_drift():
    """The Election Day layout prints the per-method total twice over -- once as
    its own row and once as the sum of the party rows above it. They must agree;
    a re-layout that silently dropped a party row is the thing this catches."""
    lines = [line.strip() for line in HEADER[:3]] + [
        "New Castle   Kent   Sussex",
        "Absentee     Political Party    County   County   County   Total",
        "DEMOCRATIC   1 1 1 3",
        "REPUBLICAN   2 2 2 6",
        "Total        9 9 9 27",
        "New Castle   Kent   Sussex",
        "Early Voting Political Party    County   County   County   Total",
        "DEMOCRATIC   1 1 1 3",
        "Total        1 1 1 3",
        "Grand Total  4 4 4 12",
    ]
    _, at = de._county_columns(lines)
    with pytest.raises(SchemaDrift) as caught:
        de._table(lines[at:])
    assert "own Total row says" in str(caught.value)


def test_an_unrecognised_block_heading_is_drift():
    lines = [line.strip() for line in HEADER[:3]] + [
        "New Castle   Kent   Sussex",
        "Drop Box     Political Party    County   County   County   Total",
        "DEMOCRATIC   1 1 1 3",
    ]
    with pytest.raises(SchemaDrift) as caught:
        de._table(lines[3:])
    assert "drop box" in str(caught.value).lower()


def test_fetch_history_2022_rebuilds_the_whole_curve(monkeypatch):
    """End to end on the two real captures: three report dates, monotonic, with
    the party split on every one."""
    cdx = (b'[["timestamp","statuscode","digest"],'
           b'["20221105094659","200","A"],["20221110174456","200","B"]]')
    monkeypatch.setattr(de, "get", _Fetcher({
        "cdx/search": cdx,
        "20221105094659": DURING_2022.read_bytes(),
        "20221110174456": FINAL_2022.read_bytes(),
    }))
    result = de.DEScraper().fetch_history(2022)
    assert [row.day for row in result.state_rows] == [
        date(2022, 11, 4), date(2022, 11, 8)]
    assert [row.ballots_total for row in result.state_rows] == [58_398, 78_792]
    assert [row.party_dem for row in result.state_rows] == [32_897, 43_248]
    assert len(result.county_rows) == 6
    # ⚠️ Method rows ride as an ATTRIBUTE, so `FetchResult.extend` cannot see
    # them and a curve would otherwise keep only the last day's crosstab.
    rows = _methods.rows_of(result)
    assert len(rows) == 12
    assert {row.day for row in rows} == {date(2022, 11, 4), date(2022, 11, 8)}


def test_a_capture_dated_in_the_future_is_skipped_not_published(monkeypatch):
    """⚠️ GUARD PARITY. `fetch` refuses a report dated after the day it was asked
    for; `fetch_history` had no such bound. `_check_window` only bounds the date
    to the CYCLE, so a backfill of the RUNNING cycle would happily take a report
    stamped weeks ahead and publish a row at a days-to-election this election
    has not reached -- a fabricated point that then reads as the curve's end."""
    today = date(2026, 10, 20)
    past, ahead = today - timedelta(days=7), today + timedelta(days=10)
    monkeypatch.setattr(de, "_today", lambda: today)
    monkeypatch.setattr(de, "get", _Fetcher({
        "cdx/search": (b'[["timestamp","statuscode","digest"],'
                       b'["20261013000000","200","A"],["20261030000000","200","B"]]'),
        "20261013000000": b"%PDF-1.4 already happened",
        "20261030000000": b"%PDF-1.4 not yet",
    }))
    monkeypatch.setattr(de, "_read_text", lambda body: _text(
        f"{(past if b'already' in body else ahead):%A}, "
        f"{(past if b'already' in body else ahead):%B} "
        f"{(past if b'already' in body else ahead).day}, 2026",
        election="2026 General Election"))
    result = de.DEScraper().fetch_history(2026)
    # Both captures are inside the cycle's window; only the one that has
    # actually happened is published.
    assert [row.day for row in result.state_rows] == [past]
