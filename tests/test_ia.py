"""Iowa: the Secretary of State's Absentee Ballot Statistics PDF.

Iowa's file is not a snapshot. Each refresh PRE-PENDS a new report to the same
PDF, so one download carries every daily report of the cycle, newest first —
NC-shaped, not FL-shaped. `fetch` therefore takes only the newest report and
`fetch_history` takes them all, and these tests pin that split.

The fixtures are real slices of the 2024 general's file: `_slice` is the head
(the newest reports) and `_tail` a later section.
"""

from __future__ import annotations

import io
from datetime import date, timedelta
from pathlib import Path

import pypdf
import pytest

from ev.adapters import ia
from ev.adapters.base import NotYetPublished, SchemaDrift

FIXTURES = Path(__file__).parent / "fixtures" / "ia"
SLICE = (FIXTURES / "AbsenteeCounty2024_slice.pdf").read_bytes()
TAIL = (FIXTURES / "AbsenteeCounty2024_tail.pdf").read_bytes()


@pytest.fixture
def newest():
    """Just the newest report, which is what the daily run publishes."""
    return ia.parse(SLICE, 2024, limit=1)


def test_newest_report_yields_one_state_day(newest):
    assert len(newest.state_rows) == 1


def test_county_rows_are_fips_keyed(newest):
    assert newest.county_rows
    for row in newest.county_rows:
        assert len(row.county_fips) == 5 and row.county_fips.startswith("19")


def test_party_buckets_sum_to_the_total(newest):
    """Iowa registers by party and the report breaks every ballot out by it."""
    row = newest.state_rows[0]
    assert (row.party_dem + row.party_rep + row.party_npa + row.party_oth
            == row.ballots_total)


def test_no_party_maps_to_npa_not_other(newest):
    """Iowa's 'No Party' is the unaffiliated bucket and is large — if it were
    being folded into `oth` with the minor parties this would not hold."""
    row = newest.state_rows[0]
    assert row.party_npa > 0
    assert row.party_npa > row.party_oth


def test_requested_and_received_are_both_carried(newest):
    """The report's three columns are Requested / Issued / Received. Received is
    the ballot count; requested is the outstanding-mail story and must not be
    folded into it."""
    row = newest.state_rows[0]
    assert row.mail_requested is not None
    assert row.ballots_total <= row.mail_requested


def test_method_split_is_carried(newest):
    """Iowa breaks returns into Mail and Counter/In-Office.

    This early-season report carries no in-office rows at all, so `inperson` is
    genuinely unreported and stays blank rather than being written as 0 -- which
    would claim Iowans had been turned away from the counter.
    """
    row = newest.state_rows[0]
    assert row.mail_returned is not None
    assert row.mail_returned <= row.ballots_total
    assert row.inperson is None


def test_limit_takes_only_the_newest_report():
    """The daily run publishes one report; the archive parse takes them all.

    The fixture is a single-report slice of a file that really holds sixteen, so
    what is pinned here is that `limit` is honoured and that days never repeat.
    """
    everything = ia.parse(SLICE, 2024)
    days = [r.day for r in everything.state_rows]
    assert len(set(days)) == len(days)      # no day counted twice
    assert days == sorted(days)             # and in order
    assert len(ia.parse(SLICE, 2024, limit=1).state_rows) == 1


def test_counties_are_checked_against_iowas_own_grand_total():
    """The adapter cross-checks its county sum against the Grand Total the
    report prints, and refuses rather than publishing a figure that does not
    reconcile. The tail fixture is a mid-file slice whose counties cannot add up,
    so it must raise -- this is the guard firing, not a parse failure.
    """
    from ev.adapters.base import SchemaDrift

    with pytest.raises(SchemaDrift):
        ia.parse(TAIL, 2024)


def test_missing_pdf_is_not_yet_published(monkeypatch):
    """The daily path until Iowa starts posting. Must STOP the ladder."""
    def _missing(*a, **k):
        raise ia.Missing("404")

    monkeypatch.setattr(ia, "get", _missing)
    with pytest.raises(NotYetPublished):
        ia.IAScraper().fetch(2026, date(2026, 9, 5))


# ---------------------------------------------------------------------------
# 2022 -- the vintage that unlocks the largest validation fold in the project.
#
# `AbsenteeCounty2022_slice.pdf` is 87 pages cut verbatim out of the real
# 1,289-page `AbsenteeCounty2022.pdf`: three COMPLETE reports, each opened by
# Adair and closed by Iowa's own Grand Total, chosen because between them they
# carry everything the 2022 file does that the 2024 file does not.
#
#   pages 1008-1036  the 2022-10-18 report -- prints its date on a line of its
#                    own and LABELS it, "report date 10/18/2022"
#   pages 1200-1241  2022-10-06, which Iowa refreshed TWICE: a 26-page pass and
#                    then a 16-page one, both under the same date, and the pass
#                    that carries the "Mailing" receipt-method row
#   pages 1274-1289  2022-10-03, the oldest report in the file, in which Iowa
#                    prints 98 counties rather than 99
#
# and all 87 of them print the report date appended to the credit line
# ("Prepared by the Office of Iowa Secretary of State 10/6/2022") rather than on
# a line of its own, which is how 253 pages -- a fifth -- of the real file do it.
#
# These tests assert Iowa's OWN PUBLISHED NUMBERS, not just invariants. Every
# invariant below (party buckets sum to the total, requested >= received, FIPS
# well-formed) holds perfectly on a parse that has silently lost a fifth of the
# file, which is exactly how this file looked before it was fixed.

SLICE_2022 = (FIXTURES / "AbsenteeCounty2022_slice.pdf").read_bytes()

#: Iowa's own Grand Total rows for the three reports in the fixture, read off
#: the PDF: (requested, issued, received).
GRAND_TOTALS_2022 = {
    date(2022, 10, 18): (153489, 147923, 616),
    date(2022, 10, 6): (106321, 56244, 151),
    date(2022, 10, 3): (79974, 40538, 41),
}

#: ...and the second, superseded pass over 2022-10-06, which must NOT be what
#: gets published for that day.
SUPERSEDED_10_06 = (93806, 49368, 137)


@pytest.fixture
def y2022():
    return ia.parse(SLICE_2022, 2022)


def test_2022_reports_are_the_days_iowa_dated_them(y2022):
    assert [row.day for row in y2022.state_rows] == [
        date(2022, 10, 18), date(2022, 10, 6), date(2022, 10, 3),
    ]


def test_2022_statewide_totals_are_iowas_own_grand_totals(y2022):
    """The canonical numbers, not a shape.

    `requested` and `received` are summed from the 99 (or 98) county rows and
    then checked against the Grand Total Iowa prints at the foot of the report,
    so pinning them here pins the whole outline read: a county dropped, a party
    row read as a method or a receipt-method row folded into the county total
    all move one of these.
    """
    got = {row.day: (row.mail_requested, row.ballots_total)
           for row in y2022.state_rows}
    assert got == {
        day: (requested, received)
        for day, (requested, _issued, received) in GRAND_TOTALS_2022.items()
    }


def test_2022_party_split_is_iowas_own(y2022):
    """Iowa registers by party, so these are reported figures and not modelled
    ones. 10/03 is the interesting row: no No Party and no Other ballot is back
    yet, and Iowa says so by printing the buckets, so they are a genuine 0."""
    got = {row.day: (row.party_dem, row.party_rep, row.party_npa, row.party_oth)
           for row in y2022.state_rows}
    assert got == {
        date(2022, 10, 18): (384, 155, 73, 4),
        date(2022, 10, 6): (96, 41, 10, 4),
        date(2022, 10, 3): (26, 15, 0, 0),
    }


def test_2022_report_date_is_read_off_the_credit_line():
    """Every page of the fixture prints its date appended to "Prepared by the
    Office of Iowa Secretary of State", not on a line of its own -- 253 pages of
    the real file do. Before this was handled the parser saw no date on any of
    them and refused the whole vintage.
    """
    page = pypdf.PdfReader(io.BytesIO(SLICE_2022)).pages[-1].extract_text()
    assert "Prepared by the Office of Iowa Secretary of State 10/3/2022" in page
    assert "\n10/3/2022" not in page          # nowhere else to read it from
    assert ia.parse(SLICE_2022, 2022).state_rows[-1].day == date(2022, 10, 3)


def test_2022_10_06_was_refreshed_twice_and_the_newest_pass_wins():
    """Iowa published 2022-10-06 twice: a 26-page pass and then a 16-page one,
    both under that date. The file is newest-first, so the FIRST pass is the
    day's latest and the second has been superseded -- and the two disagree by
    12,515 requested ballots, so which one is published is not academic.
    """
    assert sum(1 for _ in ia._refreshes(SLICE_2022, 2022)) == 4
    rows = ia.parse(SLICE_2022, 2022).state_rows
    assert len(rows) == 3
    (row,) = [r for r in rows if r.day == date(2022, 10, 6)]
    assert (row.mail_requested, row.ballots_total) == (106321, 151)
    assert (row.mail_requested, row.ballots_total) != SUPERSEDED_10_06[::2]


def test_a_county_repeated_inside_one_pass_is_still_drift():
    """The same day refreshed twice is fine; the same county twice inside ONE
    refresh is drift and must still raise. The two are told apart by Iowa's own
    Grand Total, which ends every pass -- so this doubles a page from the middle
    of the 10/03 report, well before its Grand Total, and the guard must fire.
    """
    reader = pypdf.PdfReader(io.BytesIO(SLICE_2022))
    writer = pypdf.PdfWriter()
    for index in range(71, 87):                 # the whole 10/03 report...
        writer.add_page(reader.pages[index])
        if index == 71:
            writer.add_page(reader.pages[index])   # ...with its first page twice
    buf = io.BytesIO()
    writer.write(buf)

    with pytest.raises(SchemaDrift, match="appears twice"):
        ia.parse(buf.getvalue(), 2022)


def test_mailing_is_a_receipt_method_not_a_party():
    """2022's one unrecognised label, and the only one in all 1,289 pages.

    It is NOT another spelling of Mail: Linn County's No Party block prints both
    on 10/18 ("Mail 2 2 2" and "Mailing 1 1"). It is mapped as a method on the
    evidence of the party-block sum below, which it does not break.
    """
    assert ia._classify("Mailing") == ("method", None)
    assert ia._classify("Mail") == ("method", None)
    assert "mailing" in ia.METHODS

    page = pypdf.PdfReader(io.BytesIO(SLICE_2022)).pages[16].extract_text()
    assert "Mail 2 2 2" in page and "Mailing 1 1" in page


def test_mailing_is_not_folded_into_a_party_bucket():
    """Linn County on 10/18: the No Party row says 9 ballots back and its four
    receipt-method leaves (E-Mail 7, Mail 2, not-yet-returned 0, Mailing 0) say
    the same 9. A "Mailing" bucketed as a party would show up here.
    """
    rows = {(row.day, row.county_fips): row
            for row in ia.parse(SLICE_2022, 2022).county_rows}
    linn = rows[(date(2022, 10, 18), "19113")]
    assert linn.county_name == "Linn County"
    assert (linn.party_dem, linn.party_rep, linn.party_npa, linn.party_oth) == (18, 9, 9, 0)
    assert linn.ballots_total == 36


def test_party_block_leaves_must_reconcile_with_the_party_row():
    """The guard that makes an unfamiliar leaf label safe to map.

    A party misread as a receipt method is invisible to the county cross-check,
    because methods contribute nothing to it. It is visible here.
    """
    block = ia._PartyBlock("dem", (10, 10, 8))
    block.add((6, 6, 5))
    block.add((4, 4, 3))
    block.close()

    short = ia._PartyBlock("dem", (10, 10, 8))
    short.add((6, 6, 5))
    with pytest.raises(SchemaDrift, match="receipt-method rows total"):
        short.close()


def test_two_dates_on_one_page_is_drift():
    """A page carries one report date wherever it is printed. Two that disagree
    means one was read off a line that is not the report date -- the specific
    mistake available in a file that moved the date between vintages."""
    assert ia._one_date(None, date(2022, 10, 3)) == date(2022, 10, 3)
    assert ia._one_date(date(2022, 10, 3), date(2022, 10, 3)) == date(2022, 10, 3)
    with pytest.raises(SchemaDrift, match="two different report dates"):
        ia._one_date(date(2022, 10, 3), date(2022, 10, 6))


def test_2022_counties_are_reported_not_invented(y2022):
    """Iowa printed 99 counties on 10/18 and 10/06 and only 98 on 10/03: Taylor
    County had no absentee activity yet and Iowa simply left it out. A missing
    county is a MISSING ROW, never a row of zeros -- a zero there would read as
    "nobody in Taylor County has voted" rather than "Iowa has not said".
    """
    by_day = {}
    for row in y2022.county_rows:
        by_day.setdefault(row.day, set()).add(row.county_fips)
    assert {day: len(fips) for day, fips in by_day.items()} == {
        date(2022, 10, 18): 99, date(2022, 10, 6): 99, date(2022, 10, 3): 98,
    }
    assert "19173" in by_day[date(2022, 10, 6)]          # Taylor County
    assert "19173" not in by_day[date(2022, 10, 3)]


def test_2022_carries_no_in_person_split(y2022):
    """Iowa's receipt-method split is how an ABSENTEE ballot came back, not a
    mail-versus-in-person split of ballots cast, so `inperson` stays blank on
    every row of every report. Never 0."""
    for row in y2022.state_rows:
        assert row.inperson is None
        assert row.mail_returned == row.ballots_total
    assert all(row.inperson is None for row in y2022.county_rows)


def test_both_fetch_paths_apply_the_same_horizon_guard(monkeypatch):
    """⚠️ PARITY. A check present on `fetch` and absent from `fetch_history` has
    produced three separate published-wrong-numbers bugs in this repo, and Iowa
    is a state where it would bite: the daily path reads the 2026 file and the
    history path reads the 2022 one, and 2022 is the file that prints its date
    somewhere else.
    """
    seen = []
    original = ia.IAScraper._no_later_than

    monkeypatch.setattr(ia.IAScraper, "_load",
                        lambda self, cycle, *, use_cache: SLICE_2022)
    monkeypatch.setattr(
        ia.IAScraper, "_no_later_than",
        staticmethod(lambda result, horizon: (seen.append(horizon), original(result, horizon))[1]),
    )

    assert ia.IAScraper().fetch(2022, date(2022, 10, 18)).state_rows
    assert ia.IAScraper().fetch_history(2022).state_rows
    assert len(seen) == 2, "one fetch path skipped the horizon guard"


def test_a_report_dated_after_the_horizon_is_refused():
    result = ia.parse(SLICE_2022, 2022, limit=1)
    newest = result.state_rows[0].day
    assert ia.IAScraper._no_later_than(result, newest) is result
    with pytest.raises(NotYetPublished, match="after"):
        ia.IAScraper._no_later_than(result, newest - timedelta(days=1))


def test_a_page_date_is_required_and_pinned_to_the_cycle():
    """The footer date is the one number on the page with no cross-check of its
    own. A date found in neither of the two places Iowa prints it is drift -- it
    must never be optional, and never inherited from the previous page -- and a
    date outside the cycle means it was read off a line that is not the date.
    """
    assert ia._page_date(date(2022, 10, 3), 2022) == date(2022, 10, 3)
    with pytest.raises(SchemaDrift, match="carries no 'Data Pulled' date"):
        ia._page_date(None, 2022)
    with pytest.raises(SchemaDrift, match="not in the 2024 cycle"):
        ia._page_date(date(2022, 10, 3), 2024)


def test_the_wrong_cycles_file_is_refused_outright():
    """...and the title line on every page pins the ELECTION, so the 2022 file
    can never be read as 2024 even before the footer date is looked at."""
    with pytest.raises(SchemaDrift, match="report is for the 2022 General"):
        ia.parse(SLICE_2022, 2024)
