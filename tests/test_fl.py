"""Florida: the Division of Elections PublicStats page.

The live fixture is a real trimmed capture from 2026-09-05 — the genuine
four-table structure and headers, with the county tables cut to five rows each.
It keeps Florida's leading-zero number formatting ("01" for 1), which is the
specific quirk that makes a naive parse shift a digit.

The other four fixtures are real Wayback captures of the SAME page, trimmed the
same way, and they are what the 2022 and 2024 county backfills are read out of:

    publicstats_archived_20241101005721.html   crawled 2024-11-01 00:57 UTC,
        compiled 10/31/2024 8:15AM — the gap between those two is the whole
        reason the crawl time is never the as-of date
    publicstats_archived_20240930164213.html   compiled 09/30, 3,243 cast
    publicstats_archived_20241001111047.html   compiled 09/30, 3,244 cast — a
        later capture of the SAME day, which must collapse to one row
    publicstats_archived_20221007061011.html   the 2022 primary/general
        crossover: two summary tables, both elections' counties in one table,
        and no "Voted Early" rows for the general at all

Florida is a SNAPSHOT source, so these tests pin the judgements that decide what
its numbers mean: outstanding mail ballots are not votes, a party split that does
not reproduce the state's own published Total is drift, the page's own Compiled
stamp is the as-of date, an election Florida has not labelled "General" is never
published as one, and a table Florida does not print is blank rather than zero.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import pytest

from ev.adapters import _methods, fl
from ev.adapters.base import NotYetPublished, SchemaDrift

FIXTURES = Path(__file__).parent / "fixtures" / "fl"
FIXTURE = FIXTURES / "publicstats_sample.html"

#: Crawled 2024-11-01 00:57 UTC; the page says it was compiled 10/31/2024 8:15AM.
ARCHIVED_2024 = FIXTURES / "publicstats_archived_20241101005721.html"
#: Two captures of the same compiled day, 3,243 ballots then 3,244.
ARCHIVED_0930_EARLY = FIXTURES / "publicstats_archived_20240930164213.html"
ARCHIVED_0930_LATE = FIXTURES / "publicstats_archived_20241001111047.html"
#: The 2022 crossover: the general and the primary on one page.
ARCHIVED_2022 = FIXTURES / "publicstats_archived_20221007061011.html"


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


# --------------------------------------------------------------------------
# The archived series: the 2022 and 2024 county curves out of the Wayback
# Machine. PublicStats is overwritten in place, so this is the only place a past
# cycle's daily arc exists.
# --------------------------------------------------------------------------
def _parse(path: Path, cycle: int) -> fl.Snapshot:
    return fl.parse(path.read_text(encoding="utf-8"), cycle)


def _relabel(markup: str, election: str, count: int = 0) -> str:
    """Rewrite the page's Election cells. Florida renders them '43888  - General'."""
    return re.sub(r"43888\s*-\s*General", election, markup, count=count)


def test_an_archived_capture_is_dated_by_the_pages_compiled_stamp(monkeypatch):
    """The crawl time is NOT the as-of date, and the gap is a whole day.

    The Wayback Machine visited this page at 2024-11-01 00:57 UTC. Florida had
    compiled it at 10/31/2024 8:15AM. Dating the row by the capture would push
    every Florida county one day along the days-to-election axis, which is the
    one axis the counterfactual is sensitive to.
    """
    snapshot = _parse(ARCHIVED_2024, 2024)
    assert snapshot.as_of == date(2024, 10, 31)


def test_an_archived_capture_names_the_general_it_is_reporting():
    assert _parse(ARCHIVED_2024, 2024).election == "43888 - General"


def test_the_archived_capture_carries_floridas_real_numbers():
    """2,482,331 mail ballots and 3,743,643 early votes, as published."""
    result = fl.to_result(_parse(ARCHIVED_2024, 2024), 2024, date(2024, 10, 31))
    row = result.state_rows[0]
    assert row.mail_returned == 2_482_331
    assert row.inperson == 3_743_643
    assert row.ballots_total == 6_225_974
    # Outstanding mail is provided-not-returned, still not a vote.
    assert row.mail_requested == 1_039_744 + 2_482_331


def test_archived_county_rows_are_fips_keyed_and_add_up():
    result = fl.to_result(_parse(ARCHIVED_2024, 2024), 2024, date(2024, 10, 31))
    assert result.county_rows
    for row in result.county_rows:
        assert len(row.county_fips) == 5 and row.county_fips.startswith("12")
        assert row.ballots_total == row.mail_returned + row.inperson
        assert (row.party_dem + row.party_rep + row.party_npa + row.party_oth
                == row.ballots_total)


def test_a_primary_capture_is_never_published_as_the_general(monkeypatch):
    """Florida's primary label differs from the general's by one digit.

    On 2024-08-19 this page really did read "43887 - Primary" with 1,240,384
    mail ballots and 600,463 early votes on it. Publishing those as the general's
    early vote would be a confident, plausible, completely wrong headline.
    """
    markup = _relabel(ARCHIVED_2024.read_text(encoding="utf-8"), "43887 - Primary")
    with pytest.raises(NotYetPublished):
        fl.parse(markup, 2024)


def test_a_capture_of_a_different_cycles_general_is_refused():
    """The label says General; the Compiled stamp says which General."""
    with pytest.raises(NotYetPublished):
        _parse(ARCHIVED_2024, 2026)


def test_two_generals_on_one_page_is_drift():
    markup = _relabel(ARCHIVED_2024.read_text(encoding="utf-8"),
                      "43889 - General", count=1)
    with pytest.raises(SchemaDrift):
        fl.parse(markup, 2024)


def test_a_table_florida_does_not_print_is_blank_not_zero():
    """The 2022 crossover: no "Voted Early" county rows exist for the general.

    Early voting had not opened, so Florida printed no general-election rows in
    that table at all. Absent is absent — `inperson` must be None, because a 0
    would render as "these counties held early voting and nobody came".
    """
    result = fl.to_result(_parse(ARCHIVED_2022, 2022), 2022, date(2022, 10, 6))
    assert result.county_rows
    for row in result.county_rows:
        assert row.inperson is None
        assert row.mail_returned is not None
        assert row.ballots_total == row.mail_returned
    assert result.state_rows[0].inperson is None


def test_a_crossover_capture_sums_its_own_election_rather_than_guessing():
    """Two live elections means two summary tables and no way to tell which.

    Rather than pick one, the statewide row is summed from the county rows that
    carry the general's own election label — the primary's 1.7 million returned
    ballots are on the same page and must not leak into it.
    """
    snapshot = _parse(ARCHIVED_2022, 2022)
    assert snapshot.summary is None
    assert snapshot.election == "26906 - General"
    result = fl.to_result(snapshot, 2022, snapshot.as_of)
    assert result.state_rows[0].mail_returned == sum(
        row.mail_returned for row in result.county_rows)


class _Fetcher:
    """Stands in for `_net.get`, keyed on a marker in the URL."""

    def __init__(self, bodies: dict[str, bytes]):
        self.bodies = bodies
        self.asked: list[str] = []

    def __call__(self, url, *, state, filename, **kwargs):
        self.asked.append(url)
        for marker, body in self.bodies.items():
            if marker in url:
                return body
        raise fl._net.Missing(f"{state}: {url} returned 404")


def _cdx(*stamps: str) -> bytes:
    rows = ",".join(f'["{s}","200","D{i}"]' for i, s in enumerate(stamps))
    return f'[["timestamp","statuscode","digest"],{rows}]'.encode()


def test_history_collapses_captures_of_one_compiled_day_and_keeps_the_later(monkeypatch):
    """Two distinct captures, one reading date, one row — and the later wins.

    Both of these are real, both have their own Wayback digest, and both say
    "Compiled 09/30/2024". The 16:42 crawl caught 3,243 ballots cast and the
    next morning's crawl caught 3,244 still under the same compiled date. Keying
    on the capture would publish 09/30 twice and invent a day of growth.
    """
    fetch = _Fetcher({
        "cdx/search": _cdx("20240930164213", "20241001111047"),
        "20240930164213": ARCHIVED_0930_EARLY.read_bytes(),
        "20241001111047": ARCHIVED_0930_LATE.read_bytes(),
    })
    monkeypatch.setattr(fl._net, "get", fetch)
    result = fl.FLScraper().fetch_history(2024)
    assert [row.day for row in result.state_rows] == [date(2024, 9, 30)]
    assert result.state_rows[0].ballots_total == 3_244


def test_history_publishes_one_row_per_compiled_day(monkeypatch):
    fetch = _Fetcher({
        "cdx/search": _cdx("20241001111047", "20241101005721"),
        "20241001111047": ARCHIVED_0930_LATE.read_bytes(),
        "20241101005721": ARCHIVED_2024.read_bytes(),
    })
    monkeypatch.setattr(fl._net, "get", fetch)
    result = fl.FLScraper().fetch_history(2024)
    assert [row.day for row in result.state_rows] == [
        date(2024, 9, 30), date(2024, 10, 31)]
    assert [row.ballots_total for row in result.state_rows] == [3_244, 6_225_974]
    assert {row.day for row in result.county_rows} == {
        date(2024, 9, 30), date(2024, 10, 31)}


def test_history_skips_a_capture_of_the_wrong_election(monkeypatch):
    """A primary capture is not an error and not a row; it is a different question."""
    primary = _relabel(
        ARCHIVED_2024.read_text(encoding="utf-8"), "43887 - Primary").encode("utf-8")
    fetch = _Fetcher({
        "cdx/search": _cdx("20240819120547", "20241101005721"),
        "20240819120547": primary,
        "20241101005721": ARCHIVED_2024.read_bytes(),
    })
    monkeypatch.setattr(fl._net, "get", fetch)
    result = fl.FLScraper().fetch_history(2024)
    assert [row.day for row in result.state_rows] == [date(2024, 10, 31)]


def test_history_with_nothing_archived_is_not_yet_published(monkeypatch):
    monkeypatch.setattr(fl._net, "get", _Fetcher({"cdx/search": b"[]"}))
    with pytest.raises(NotYetPublished):
        fl.FLScraper().fetch_history(2024)


def test_history_refuses_a_cycle_before_the_archived_host(monkeypatch):
    """2016-2020 captures live on a third host this adapter has never parsed."""
    with pytest.raises(NotYetPublished):
        fl.FLScraper().fetch_history(2020)


def test_history_sweeps_both_hosts_the_page_has_lived_at(monkeypatch):
    """Florida moved host without changing the page; the old one holds 2024."""
    fetch = _Fetcher({"cdx/search": _cdx("20241101005721"),
                      "20241101005721": ARCHIVED_2024.read_bytes()})
    monkeypatch.setattr(fl._net, "get", fetch)
    fl.FLScraper().fetch_history(2024)
    swept = [u for u in fetch.asked if "cdx/search" in u]
    assert len(swept) == len(fl.ARCHIVED_URLS) == 2


# --------------------------------------------------------------------------
# The party-by-method crosstab
# --------------------------------------------------------------------------
def test_the_two_voted_tables_are_published_as_a_crosstab():
    """"Voted Vote-by-Mail" and "Voted Early" are two per-county tables, each
    split four ways by party. The county row is still their sum -- that is what
    the site reads -- and the bands must reproduce it exactly.

    Read off a real archived capture from Election Day 2024, because the live
    2026 sample has nothing cast yet and therefore no county rows at all."""
    markup = (FIXTURES / "publicstats_archived_20241101005721.html").read_text()
    parsed = fl.to_result(fl.parse(markup, 2024), 2024, date(2024, 10, 31))
    cells = _methods.rows_of(parsed)
    assert cells, "the archived page carries both voted tables"
    by_county = {}
    for row in cells:
        entry = by_county.setdefault(row.county_fips, [0, 0, 0, 0, 0])
        for i, value in enumerate((row.ballots_total, row.party_dem, row.party_rep,
                                   row.party_oth, row.party_npa)):
            entry[i] += value
    for county in parsed.county_rows:
        assert by_county[county.county_fips] == [
            county.ballots_total, county.party_dem, county.party_rep,
            county.party_oth, county.party_npa]


def test_a_channel_florida_prints_no_county_row_for_is_absent_not_zero(monkeypatch):
    """Florida prints no "Voted Early" county rows at all until early voting
    opens. A zero row there would say nobody had voted in person; an absent row
    says Florida has not reported that channel, which is the truth."""
    markup = (FIXTURES / "publicstats_archived_20221007061011.html").read_text()
    snapshot = fl.parse(markup, 2022)
    assert all(row["early"] is None for row in snapshot.counties)
    result = fl.to_result(snapshot, 2022, date(2022, 10, 7))
    assert {r.method for r in _methods.rows_of(result)} == {"mail"}
