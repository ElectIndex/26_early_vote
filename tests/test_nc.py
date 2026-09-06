"""North Carolina: the voter-level absentee file.

The fixture is a real slice of `absentee_20261103.csv` pulled from NCSBE on
2026-09-05 — the genuine header, genuine Latin-1 bytes (it contains 0x90, which
is undefined in cp1252, so a cp1252 read raises), and the first ten accepted
ballots of the 2026 cycle.

What makes NC different from every other adapter is that each row carries its own
`ballot_rtn_dt`, so one file reconstructs the whole daily curve. These tests lock
that in, along with the zero-vs-blank distinction that NC is the reference case
for: NC reports party registration, so a party with no ballots is 0, while a
field NC does not report at all stays blank.
"""

from __future__ import annotations

import csv
import io
import zipfile
from datetime import date
from pathlib import Path

import pytest

from ev.adapters import nc
from ev.adapters.base import NotYetPublished, SchemaDrift

FIXTURE = Path(__file__).parent / "fixtures" / "nc" / "absentee_sample.csv"


def _rows() -> tuple[list[str], list[dict[str, str]]]:
    """The fixture as (fieldnames, rows). NCSBE quotes every field, so the file
    must be edited through the csv module, not by splitting on commas."""
    text = FIXTURE.read_bytes().decode(nc.ENCODING)
    reader = csv.DictReader(io.StringIO(text))
    return list(reader.fieldnames or []), list(reader)


def _csv(fieldnames: list[str], rows: list[dict[str, str]]) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames, quoting=csv.QUOTE_ALL,
                            extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue()


def _zip_bytes(text: str | None = None) -> bytes:
    raw = FIXTURE.read_bytes() if text is None else text.encode(nc.ENCODING)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("absentee_20261103.csv", raw)
    return buf.getvalue()


@pytest.fixture
def parsed(monkeypatch):
    monkeypatch.setattr(nc._net, "get", lambda *a, **k: _zip_bytes())
    return nc.NCScraper().fetch(2026, date(2026, 9, 5))


# --------------------------------------------------------------------------
def test_fixture_is_latin1_not_cp1252():
    """Guards the encoding choice: cp1252 fails on this real file."""
    raw = FIXTURE.read_bytes()
    with pytest.raises(UnicodeDecodeError):
        raw.decode("cp1252")
    assert raw.decode("latin-1")


def test_one_file_reconstructs_a_daily_series(parsed):
    """Each ballot carries its return date, so we never depend on daily snapshots."""
    days = [r.day for r in parsed.state_rows]
    assert days == sorted(days)
    assert days[-1] == date(2026, 9, 5)
    # Continuous axis: no gaps for the frontend to interpolate.
    assert all((days[i + 1] - days[i]).days == 1 for i in range(len(days) - 1))


def test_cumulative_total_is_monotonic(parsed):
    totals = [r.ballots_total for r in parsed.state_rows]
    assert totals == sorted(totals)


def test_party_buckets_sum_to_the_total(parsed):
    row = parsed.state_rows[-1]
    assert (row.party_dem + row.party_rep + row.party_npa + row.party_oth
            == row.ballots_total)


def test_reported_party_with_no_ballots_is_zero_not_blank(parsed):
    """NC reports every party, so an empty bucket is a real 0.

    Blank would claim NC does not report party at all — the opposite of true.
    """
    assert parsed.state_rows[-1].party_oth == 0


def test_unreported_field_stays_blank(parsed):
    """We filter to ACCEPTED ballots, so requested-but-unreturned is not
    derivable here and must not be invented."""
    assert parsed.state_rows[-1].mail_requested is None


def test_method_split_sums_to_total(parsed):
    row = parsed.state_rows[-1]
    assert row.mail_returned + row.inperson == row.ballots_total


def test_county_rows_are_fips_keyed(parsed):
    assert parsed.county_rows
    for row in parsed.county_rows:
        assert len(row.county_fips) == 5 and row.county_fips.startswith("37")


def test_demographics_cover_all_three_dimensions(parsed):
    assert {r.dimension for r in parsed.demo_rows} == {"age", "race", "sex"}


def test_hispanic_ethnicity_overrides_race(monkeypatch):
    """Otherwise a Hispanic voter is counted twice across the race dimension."""
    cols, rows = _rows()
    row = dict(rows[0], race="WHITE", ethnicity="HISPANIC or LATINO")
    monkeypatch.setattr(nc._net, "get", lambda *a, **k: _zip_bytes(_csv(cols, [row])))
    result = nc.NCScraper().fetch(2026, date(2026, 9, 5))
    races = {r.bucket for r in result.demo_rows if r.dimension == "race"}
    assert "hispanic" in races and "white" not in races


def test_missing_file_is_not_yet_published_not_an_error(monkeypatch):
    """The daily path for the next several weeks. Must STOP the ladder, so a
    weaker tier cannot invent a zero for a state that has not opened yet."""
    def _missing(*a, **k):
        raise nc._net.Missing("404")

    monkeypatch.setattr(nc._net, "get", _missing)
    with pytest.raises(NotYetPublished):
        nc.NCScraper().fetch(2026, date(2026, 9, 5))


def test_dropped_column_raises_drift(monkeypatch):
    cols, rows = _rows()
    kept = [c for c in cols if c != "ballot_rtn_status"]
    monkeypatch.setattr(nc._net, "get", lambda *a, **k: _zip_bytes(_csv(kept, rows)))
    with pytest.raises(SchemaDrift):
        nc.NCScraper().fetch(2026, date(2026, 9, 5))


def test_unknown_party_code_raises_drift(monkeypatch):
    """A new NC party must fail loudly, not land silently in 'other'."""
    cols, rows = _rows()
    row = dict(rows[0], voter_party_code="ZZZ", ballot_rtn_status="ACCEPTED")
    monkeypatch.setattr(nc._net, "get", lambda *a, **k: _zip_bytes(_csv(cols, [row])))
    with pytest.raises(SchemaDrift):
        nc.NCScraper().fetch(2026, date(2026, 9, 5))


def test_future_dated_returns_are_excluded(monkeypatch):
    """as_of is authoritative; a ballot returned after it must not appear."""
    monkeypatch.setattr(nc._net, "get", lambda *a, **k: _zip_bytes())
    early = nc.NCScraper().fetch(2026, date(2026, 9, 3))
    assert early.state_rows == [] or early.state_rows[-1].day <= date(2026, 9, 3)
