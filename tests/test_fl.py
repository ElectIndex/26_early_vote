"""Florida: the Division of Elections PublicStats page.

The fixture is a real trimmed capture from 2026-09-05 — the genuine four-table
structure and headers, with the county tables cut to five rows each. It keeps
Florida's leading-zero number formatting ("01" for 1), which is the specific
quirk that makes a naive parse shift a digit.

Florida is a SNAPSHOT source with no history, so these tests also pin the two
judgements that decide what its single daily number means: outstanding mail
ballots are not votes, and a party split that does not reproduce the state's own
published Total is drift rather than data.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from ev.adapters import fl
from ev.adapters.base import NotYetPublished, SchemaDrift

FIXTURE = Path(__file__).parent / "fixtures" / "fl" / "publicstats_sample.html"


def _serve(monkeypatch, markup: str | None = None):
    raw = FIXTURE.read_bytes() if markup is None else markup.encode("utf-8")
    monkeypatch.setattr(fl._net, "get", lambda *a, **k: raw)


@pytest.fixture
def parsed(monkeypatch):
    _serve(monkeypatch)
    return fl.FLScraper().fetch(2026, date(2026, 9, 5))


def test_leading_zero_counts_parse_as_their_value():
    """Florida renders 1 as '01'; int() must read 1, not 01-as-something-else."""
    assert fl._int("01") == 1
    assert fl._int("0") == 0
    assert fl._int("1,234") == 1234


def test_outstanding_mail_is_not_counted_as_a_vote(parsed):
    """The whole point: ballots_total is ballots CAST.

    Folding 'Provided (Not Yet Returned)' in would overstate Florida by the size
    of its outstanding mail backlog — millions of ballots at peak.
    """
    row = parsed.state_rows[0]
    assert row.ballots_total == row.mail_returned + row.inperson
    assert row.mail_requested >= row.mail_returned


def test_party_columns_sum_to_ballots_total(parsed):
    row = parsed.state_rows[0]
    assert (row.party_dem + row.party_rep + row.party_npa + row.party_oth
            == row.ballots_total)


def test_compiled_date_is_used_as_the_row_date(parsed):
    assert parsed.state_rows[0].day == date(2026, 9, 5)


def test_snapshot_never_dates_ahead_of_as_of(monkeypatch):
    """The page's compiled stamp must not push a row past the run's as_of."""
    _serve(monkeypatch)
    result = fl.FLScraper().fetch(2026, date(2026, 9, 4))
    assert result.state_rows[0].day <= date(2026, 9, 4)


def test_county_rows_are_fips_keyed(parsed):
    for row in parsed.county_rows:
        assert len(row.county_fips) == 5 and row.county_fips.startswith("12")


def test_party_sum_mismatch_raises_drift(monkeypatch):
    """A shifted column must fail loudly rather than publish a wrong split."""
    markup = FIXTURE.read_text(encoding="utf-8").replace(
        "<td>01</td>", "<td>07</td>", 1)
    if "<td>07</td>" not in markup:  # fixture spacing differs; force a mismatch
        markup = FIXTURE.read_text(encoding="utf-8").replace("01", "07", 1)
    _serve(monkeypatch, markup)
    with pytest.raises(SchemaDrift):
        fl.FLScraper().fetch(2026, date(2026, 9, 5))


def test_missing_table_raises_drift(monkeypatch):
    _serve(monkeypatch, "<html><body><table><tr><td>x</td></tr></table></body></html>")
    with pytest.raises(SchemaDrift):
        fl.FLScraper().fetch(2026, date(2026, 9, 5))


def test_renamed_header_raises_drift(monkeypatch):
    """Columns are matched by name; a rename must not silently shift positions."""
    markup = FIXTURE.read_text(encoding="utf-8").replace(
        "No Party Affiliation", "Unaffiliated")
    _serve(monkeypatch, markup)
    with pytest.raises(SchemaDrift):
        fl.FLScraper().fetch(2026, date(2026, 9, 5))


def test_page_unavailable_is_not_yet_published(monkeypatch):
    def _missing(*a, **k):
        raise fl._net.Missing("404")

    monkeypatch.setattr(fl._net, "get", _missing)
    with pytest.raises(NotYetPublished):
        fl.FLScraper().fetch(2026, date(2026, 9, 5))
