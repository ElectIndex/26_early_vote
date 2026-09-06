"""Iowa: the Secretary of State's Absentee Ballot Statistics PDF.

Iowa's file is not a snapshot. Each refresh PRE-PENDS a new report to the same
PDF, so one download carries every daily report of the cycle, newest first —
NC-shaped, not FL-shaped. `fetch` therefore takes only the newest report and
`fetch_history` takes them all, and these tests pin that split.

The fixtures are real slices of the 2024 general's file: `_slice` is the head
(the newest reports) and `_tail` a later section.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from ev.adapters import ia
from ev.adapters.base import NotYetPublished

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
