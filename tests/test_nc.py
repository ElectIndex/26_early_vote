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

from ev.adapters import _methods, nc
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


def test_every_accepted_form_counts_and_none_is_silently_dropped(parsed):
    """⚠️ THE REGRESSION THIS EXISTS FOR, and it was live in this fixture.

    `ACCEPTED` was a set of two literals, "ACCEPTED" and "ACCEPTED - CURED".
    North Carolina also writes "ACCEPTED - EXCEPTION", which is 2 of this
    fixture's 10 rows -- so the adapter published 8 ballots out of 10, every
    party and demographic bucket was 20% light, and Wayne County disappeared
    from the county file because its only ballot carried that status.

    Every other test in this file asserts an INVARIANT (monotonic, party sums to
    total, FIPS-shaped), and all of them held perfectly on the undercount. That
    is why this one asserts a CANONICAL VALUE: the fixture has ten accepted
    ballots and the adapter must publish ten.
    """
    assert parsed.state_rows[-1].ballots_total == 10
    fips = {row.county_fips for row in parsed.county_rows}
    assert "37191" in fips, "Wayne County's only ballot is ACCEPTED - EXCEPTION"


def test_a_status_we_cannot_classify_is_drift_not_a_silent_drop(monkeypatch):
    """Rule 3. Whether a ballot counts is the headline number, so a status that
    is neither an ACCEPTED form nor a known non-cast status must be refused
    rather than quietly discarded. SchemaDrift falls through to civicAPI, which
    carries North Carolina, so refusing costs detail and not the state."""
    raw = FIXTURE.read_bytes().decode(nc.ENCODING, errors="replace")
    mangled = raw.replace("ACCEPTED - EXCEPTION", "RETURNED SIDEWAYS")
    monkeypatch.setattr(nc._net, "get", lambda *a, **k: _zip_bytes(mangled))
    with pytest.raises(SchemaDrift, match="cannot classify"):
        nc.NCScraper().fetch(2026, date(2026, 9, 5))


def test_a_known_non_cast_status_is_excluded_without_complaint(monkeypatch):
    """The other half: PENDING and SPOILED are not drift, they are simply not
    ballots cast. The gate must not fire on them."""
    raw = FIXTURE.read_bytes().decode(nc.ENCODING, errors="replace")
    mangled = raw.replace("ACCEPTED - EXCEPTION", "SPOILED")
    monkeypatch.setattr(nc._net, "get", lambda *a, **k: _zip_bytes(mangled))
    result = nc.NCScraper().fetch(2026, date(2026, 9, 5))
    assert result.state_rows[-1].ballots_total == 8


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


# --------------------------------------------------------------------------
# THE CROSS-CYCLE LABEL CHANGE, and the crosstab it was hiding
# --------------------------------------------------------------------------
def _relabelled(label: str) -> nc.FetchResult:
    """The fixture with every ballot re-typed as `label`, parsed."""
    fieldnames, rows = _rows()
    for row in rows:
        row["ballot_req_type"] = label
    return nc.NCScraper()._aggregate(
        iter(rows), 2026, date(2026, 9, 4))


def test_both_cycles_spellings_of_in_person_count_as_in_person():
    """⚠️ THE REGRESSION THIS EXISTS FOR, AND IT WAS LIVE IN output/.

    North Carolina spells `ballot_req_type` `ONE-STOP` in its 2022 file and
    `EARLY VOTING` in its 2024 one. This adapter matched the raw cell against a
    hand-written tuple that knew only the 2022 spelling, so all **4,231,692** of
    2024's in-person ballots fell through both branches and `inperson` published
    as a flat **0** on every one of the cycle's 47 days -- against a headline of
    4,520,768. Zero, not blank, so it read as North Carolina reporting that
    nobody voted early in person.

    The fix is CLAUDE.md rule 4 and nothing else: the label goes through
    `normalize.method()`, which already knew both spellings.
    """
    for label in ("ONE-STOP", "ONE STOP", "EARLY VOTING", "Early Voting"):
        result = _relabelled(label)
        assert result.state_rows[-1].inperson == 10, label
        assert result.state_rows[-1].mail_returned == 0, label
    mail = _relabelled("MAIL")
    assert mail.state_rows[-1].mail_returned == 10
    assert mail.state_rows[-1].inperson == 0


def test_a_request_type_normalize_does_not_know_is_drift():
    """Rule 3. A spelling we have never seen must not vanish into neither band;
    that is exactly how the last one cost four million ballots."""
    with pytest.raises(SchemaDrift, match="ballot_req_type"):
        _relabelled("CURBSIDE PICKUP")


def test_the_ballot_row_is_a_party_by_method_crosstab(parsed):
    """Party and channel sit on the same record, so NC states the cells. The
    bands must reproduce the county row they were aggregated into."""
    cells = _methods.rows_of(parsed)
    assert cells and {r.method for r in cells} == {"mail"}
    by_key = {}
    for row in cells:
        entry = by_key.setdefault((row.county_fips, row.day), [0, 0, 0])
        entry[0] += row.ballots_total
        entry[1] += row.party_dem
        entry[2] += row.party_rep
    for county in parsed.county_rows:
        assert by_key[(county.county_fips, county.day)] == [
            county.ballots_total, county.party_dem, county.party_rep]
