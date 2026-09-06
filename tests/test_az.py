"""Arizona: the Secretary of State's Sent/Accepted early-ballot table.

The fixture is the real 2024 election-information page. Arizona publishes no
downloadable file — the numbers live in an HTML table on the cycle's page — so
the parser's whole job is reading that table without being fooled by the two
things the page does: it keeps LAST cycle's table up until the new window opens,
and it serves a bot challenge instead of the page to some automated requests.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from ev.adapters import az
from ev.adapters.base import NotYetPublished, SchemaDrift

FIXTURES = Path(__file__).parent / "fixtures" / "az"
PAGE_2024 = (FIXTURES / "2024-election-info.html").read_text(encoding="utf-8", errors="replace")
PAGE_EMPTY = (FIXTURES / "2026-election-info-no-table.html").read_text(encoding="utf-8", errors="replace")


@pytest.fixture
def parsed():
    return az.parse(PAGE_2024, 2024)


def test_all_fifteen_counties_parse(parsed):
    """Arizona has exactly 15 counties and the table carries every one."""
    assert len(parsed.county_rows) == 15


def test_county_rows_are_fips_keyed(parsed):
    for row in parsed.county_rows:
        assert len(row.county_fips) == 5 and row.county_fips.startswith("04")


def test_accepted_is_the_ballot_count_not_sent(parsed):
    """`Sent` is ballots put in voters' hands; only `Accepted` are votes.

    Counting Sent would overstate Arizona by its entire outstanding mail pile.
    """
    state = parsed.state_rows[0]
    assert state.ballots_total is not None
    assert state.mail_requested is not None
    assert state.mail_requested >= state.ballots_total


def test_counties_sum_to_the_state_total(parsed):
    """The page publishes its own Total row; ours has to reproduce it."""
    total = sum(r.ballots_total for r in parsed.county_rows if r.ballots_total is not None)
    assert total == parsed.state_rows[0].ballots_total


def test_total_row_is_not_emitted_as_a_county(parsed):
    names = {(r.county_name or "").strip().lower() for r in parsed.county_rows}
    assert "total" not in names


def test_party_is_never_reported(parsed):
    """This table carries no party breakdown, so every party field stays blank.

    Arizona DOES register by party — the state simply does not publish it here —
    so a 0 would be a claim the source never made.
    """
    for row in parsed.state_rows + parsed.county_rows:
        assert row.party_dem is None and row.party_rep is None
        assert row.party_npa is None and row.party_oth is None


def test_page_without_the_table_raises_drift():
    """The table missing entirely is drift, not an empty result."""
    with pytest.raises((SchemaDrift, NotYetPublished)):
        az.parse(PAGE_EMPTY, 2026)


def test_last_cycles_table_is_not_published_as_this_cycles(monkeypatch):
    """Arizona leaves the previous table up until the new window opens.

    Parsing it as 2026 data would publish a two-year-old number as today's.
    """
    from ev.adapters.base import AdapterError

    monkeypatch.setattr(az, "get", lambda *a, **k: PAGE_2024.encode("utf-8"))
    # The invariant is that a 2024-dated table never becomes 2026 data. WHICH
    # refusal is a ladder-policy call: the adapter raises SchemaDrift, so a stale
    # state page falls through to the aggregator, which may well be fresher --
    # NotYetPublished would instead stop the walk and publish nothing for AZ.
    with pytest.raises(AdapterError):
        az.AZScraper().fetch(2026, date(2026, 9, 5))


def test_missing_page_is_not_yet_published(monkeypatch):
    """A 404 STOPS the ladder -- patched at the network layer so the adapter's
    own Missing -> NotYetPublished conversion is what gets exercised."""
    def _missing(*a, **k):
        raise az.Missing("404")

    monkeypatch.setattr(az, "get", _missing)
    with pytest.raises(NotYetPublished):
        az.AZScraper().fetch(2026, date(2026, 9, 5))


def test_bot_challenge_falls_through_instead_of_stopping(monkeypatch):
    """A challenge means "unreachable", not "absent" -- the report may well be
    behind it, so the ladder must try the next tier rather than stop."""
    from ev.adapters.base import SourceError

    monkeypatch.setattr(az, "get", lambda *a, **k: b"<html><title>Just a moment...</title>" + b"x" * 4096)
    with pytest.raises(SourceError) as exc:
        az.AZScraper().fetch(2026, date(2026, 9, 5))
    assert not isinstance(exc.value, NotYetPublished)


def test_bot_challenge_is_detected():
    """A challenge page is not data. Parsing it would yield silent nonsense."""
    assert az.looks_like_challenge(b"<html><title>Just a moment...</title>")
    assert not az.looks_like_challenge(PAGE_2024.encode("utf-8", "replace")[:2000])
