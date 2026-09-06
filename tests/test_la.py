"""Louisiana: the SoS's daily early-voter rosters and its post-election report.

Four real fixtures, all saved from `electionstatistics.sos.la.gov` on
2026-09-06:

* `20241105_EBTR_Daily_1018.pdf` -- East Baton Rouge's roster for 2024-10-18,
  cut to its real first and last pages (the header and the `Total Voters: 8047`
  trailer are both verbatim).
* `20241105_ACAD_Daily_1102.pdf` -- Acadia on 2024-11-02, kept WHOLE because the
  whole point of it is that nobody voted: one banner page, no trailer. This is
  the fixture that keeps "zero voters" from being read as schema drift.
* `2024_1105_ParishStats.pdf` -- the first nine pages of the post-election Early
  Voting Statistical Report, which is four complete parish blocks.
* `EarlyVoterList_EBTR_2024.html` -- the real ASP.NET listing postback,
  viewstate and all.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from ev.adapters import la
from ev.adapters.base import NotYetPublished, SchemaDrift

FIXTURES = Path(__file__).parent / "fixtures" / "la"
DAILY = FIXTURES / "20241105_EBTR_Daily_1018.pdf"
EMPTY_DAY = FIXTURES / "20241105_ACAD_Daily_1102.pdf"
PARISH_STATS = FIXTURES / "2024_1105_ParishStats.pdf"
LISTING = FIXTURES / "EarlyVoterList_EBTR_2024.html"

ELECTION_2024 = date(2024, 11, 5)


@pytest.fixture(scope="module")
def stats_2024():
    return la.parse_parish_stats(PARISH_STATS.read_bytes(), 2024)


@pytest.fixture(scope="module")
def final_2024(stats_2024):
    return la.build_final(stats_2024, 2024, ELECTION_2024)


# --------------------------------------------------------------------------
# The daily roster
# --------------------------------------------------------------------------
def test_total_voters_reads_the_trailer():
    assert la.total_voters(DAILY.read_bytes(), DAILY.name) == 8047


def test_a_day_nobody_voted_is_zero_not_drift():
    """Louisiana still publishes the report on an empty day -- one banner page,
    no `Total Voters:` line. Raising here would break the whole state on the
    first quiet Tuesday; returning None would drop a real day out of a running
    sum that has no other way to know it existed."""
    assert la.total_voters(EMPTY_DAY.read_bytes(), EMPTY_DAY.name) == 0


def test_a_pdf_that_is_not_a_roster_raises():
    with pytest.raises(SchemaDrift):
        la.total_voters(PARISH_STATS.read_bytes(), "not-a-roster.pdf")


def test_daily_files_are_dated_by_their_own_filename():
    assert la.roster_day("20241105_EBTR_Daily_1018.pdf", None, ELECTION_2024) \
        == date(2024, 10, 18)


def test_preev_is_dated_from_the_listings_created_column():
    """The SoS prints each roster the morning AFTER the day it covers, so the
    PreEV file created on 10/18 covers everything through 10/17 -- which is
    exactly the day before the first daily."""
    assert la.roster_day("20241105_EBTR_PreEV.pdf", date(2024, 10, 18), ELECTION_2024) \
        == date(2024, 10, 17)


def test_the_cumulative_file_is_deliberately_skipped():
    """It restates everything the dailies already carry. Counting it would
    double Louisiana, and it is 8 MB per parish."""
    assert la.roster_day("20241105_EBTR_Cumulative.pdf", ELECTION_2024, ELECTION_2024) is None


def test_a_daily_dated_after_its_own_election_raises():
    with pytest.raises(SchemaDrift):
        la.roster_day("20241105_EBTR_Daily_1206.pdf", None, ELECTION_2024)


def test_the_listing_gives_filenames_and_created_dates():
    rows = [c for c in la._table_rows(LISTING.read_text())
            if len(c) >= 5 and c[1].startswith("20241105_EBTR_")]
    assert len(rows) == 17
    by_name = {c[1]: c[3] for c in rows}
    assert by_name["20241105_EBTR_PreEV.pdf"] == "10/18/2024"
    assert by_name["20241105_EBTR_Daily_1018.pdf"] == "10/19/2024"
    # Sundays have no file at all -- 10/20 and 10/27 in 2024.
    assert "20241105_EBTR_Daily_1020.pdf" not in by_name


# --------------------------------------------------------------------------
# The cumulative curve
# --------------------------------------------------------------------------
def _all_parishes(series):
    from ev.adapters import _fips
    return {name: dict(series) for name in _fips.names("LA")}


def test_series_accumulates_and_reports_the_days_own_count():
    daily = _all_parishes({date(2024, 10, 17): 100, date(2024, 10, 18): 40})
    result = la.build_series(daily, 2024, date(2024, 10, 18))
    ebtr = [r for r in result.county_rows if r.county_fips == "22033"]
    assert [(r.day, r.ballots_total, r.ballots_new) for r in ebtr] == [
        (date(2024, 10, 17), 100, 100),
        (date(2024, 10, 18), 140, 40),
    ]


def test_a_day_with_no_file_carries_the_total_and_blanks_the_daily():
    """Louisiana skips Sundays. "No file" is "not reported", not "zero ballots
    were recorded" -- the running total still has to hold."""
    daily = _all_parishes({date(2024, 10, 19): 10, date(2024, 10, 21): 5})
    result = la.build_series(daily, 2024, date(2024, 10, 21))
    sunday = [r for r in result.county_rows
              if r.county_fips == "22033" and r.day == date(2024, 10, 20)][0]
    assert sunday.ballots_total == 10
    assert sunday.ballots_new is None


def test_a_statewide_row_needs_every_parish():
    """A sum over 63 of 64 parishes looks exactly like a Louisiana turnout
    figure and is not one. Same rule as Texas."""
    full = _all_parishes({date(2024, 10, 17): 100})
    assert len(la.build_series(full, 2024, date(2024, 10, 17)).state_rows) == 1

    short = dict(full)
    short.pop("Orleans Parish")
    assert la.build_series(short, 2024, date(2024, 10, 17)).state_rows == []
    assert len(la.build_series(short, 2024, date(2024, 10, 17)).county_rows) == 63


def test_the_roster_reports_no_party_or_method(final_2024):
    """The roster is a list of names. Louisiana registers voters by party and
    splits in-person from absentee in its post-election report -- but not here,
    so these stay blank rather than 0."""
    result = la.build_series(_all_parishes({date(2024, 10, 17): 7}), 2024,
                             date(2024, 10, 17))
    for row in result.state_rows + result.county_rows:
        for field in ("mail_returned", "inperson", "party_dem", "party_rep",
                      "party_oth", "party_npa"):
            assert getattr(row, field) is None, (field, row)


def test_an_unrecognised_parish_name_raises():
    with pytest.raises(SchemaDrift):
        la.build_series({"Baton Rouge Parish": {date(2024, 10, 17): 1}}, 2024,
                        date(2024, 10, 17))


# --------------------------------------------------------------------------
# The post-election report
# --------------------------------------------------------------------------
def test_parish_blocks_parse_with_their_real_numbers(stats_2024):
    assert list(stats_2024) == ["ACADIA", "ALLEN", "ASCENSION", "ASSUMPTION"]
    assert stats_2024["ACADIA"] == {
        "TOTVTE": 9786, "WHITE": 8657, "BLACK": 969, "RACE_OTH": 160,
        "MALE": 4561, "FEMALE": 5223, "DEM": 2509, "REP": 5725,
        "PARTY_OTH": 1552, "UOCAVA_IN": 20, "UOCAVA_OUT": 17,
        "INPER": 8715, "ABS": 1071, "ASST_D": 111, "ASST_I": 23,
    }


def test_a_short_report_is_never_published_as_louisiana(stats_2024):
    """`build_final` sums whatever it is handed, so the coverage guard is what
    keeps four parishes from being published as the state."""
    with pytest.raises(SchemaDrift):
        la.require_full_coverage(stats_2024, 2024)


def test_a_misaligned_sum_row_raises(stats_2024):
    """Race, party and method each carry an explicit residual column and must
    total exactly. This is the check that makes reading a fixed column order out
    of a PDF text layer safe."""
    broken = {"ACADIA": dict(stats_2024["ACADIA"], REP=5724)}
    with pytest.raises(SchemaDrift):
        la._check_sum_row("ACADIA", broken["ACADIA"], 2024)


def test_sex_is_allowed_to_fall_short_of_the_total(stats_2024):
    """The report prints only MALE and FEMALE; Louisiana's roll carries voters
    who are neither, so sex is the one breakdown that does not close."""
    acadia = stats_2024["ACADIA"]
    assert acadia["MALE"] + acadia["FEMALE"] == 9784 < acadia["TOTVTE"]
    la._check_sum_row("ACADIA", acadia, 2024)   # must not raise
    with pytest.raises(SchemaDrift):
        la._check_sum_row("ACADIA", dict(acadia, MALE=9999), 2024)


def test_final_publishes_dem_and_rep_but_never_the_residual(final_2024):
    """Louisiana collapses everything that is neither Democrat nor Republican
    into one OTH column, which mixes no-party voters with third parties.
    normalize.py is explicit that collapsing those loses the most-watched number
    in an early-vote story, so the residual is published as neither. Same call
    as ky.py."""
    row = final_2024.state_rows[0]
    assert row.party_dem == 2509 + 1009 + 10655 + 1834
    assert row.party_rep == 5725 + 1638 + 17125 + 1431
    assert row.party_oth is None
    assert row.party_npa is None
    for county in final_2024.county_rows:
        assert county.party_oth is None and county.party_npa is None


def test_final_splits_in_person_from_absentee(final_2024):
    row = final_2024.state_rows[0]
    assert row.inperson == 8715 + 2753 + 32632 + 3297
    assert row.mail_returned == 1071 + 364 + 2560 + 467
    assert row.ballots_total == row.inperson + row.mail_returned
    # The report counts ballots cast and never says how many were requested.
    assert row.mail_requested is None


def test_final_is_marked_as_a_restatement(final_2024):
    """It reconciles registrar corrections, so it disagrees with the roster sum
    by ~0.02% and REPLACES Election Day rather than adding to it."""
    assert final_2024.state_rows[0].restated == 1


def test_final_carries_race_and_sex(final_2024):
    buckets = {(d.dimension, d.bucket): d.ballots_total for d in final_2024.demo_rows}
    assert buckets[("race", "white")] == 8657 + 2556 + 26023 + 2598
    assert buckets[("race", "black")] == 969 + 466 + 7699 + 1141
    assert buckets[("race", "other")] == 160 + 95 + 1470 + 25
    total = final_2024.state_rows[0].ballots_total
    assert buckets[("sex", "male")] + buckets[("sex", "female")] \
        + buckets[("sex", "unknown")] == total


def test_counties_are_keyed_by_fips(final_2024):
    ascension = [r for r in final_2024.county_rows if r.county_fips == "22005"][0]
    assert ascension.county_name == "Ascension Parish"
    assert ascension.ballots_total == 35192


# --------------------------------------------------------------------------
# Absence
# --------------------------------------------------------------------------
def test_no_roster_for_the_cycle_is_not_yet_published(monkeypatch):
    scraper = la.LAScraper()
    monkeypatch.setattr(scraper, "_listings", lambda election: {})
    with pytest.raises(NotYetPublished):
        scraper.fetch(2026, date(2026, 9, 6))


# --------------------------------------------------------------------------
# The host's WAF
# --------------------------------------------------------------------------
def test_a_random_403_is_retried_not_surrendered_to(monkeypatch):
    """`electionstatistics.sos.la.gov` answers the odd request with a 408-byte
    "Access Denied / Reference #18...." page. One of those anywhere in a
    thousand-file window rebuild would drop Louisiana to the aggregator for the
    day, so it is retried rather than raised."""
    from ev.adapters.base import SourceError

    monkeypatch.setattr(la, "_BACKOFF", 0)
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise SourceError("LA: returned HTTP 403")
        return "recovered"

    assert la._retrying(flaky) == "recovered"
    assert len(calls) == 3


def test_a_404_is_never_retried(monkeypatch):
    """A missing file is an answer, not a fault -- Louisiana skips Sundays."""
    from ev.adapters import _net

    monkeypatch.setattr(la, "_BACKOFF", 0)
    calls = []

    def gone():
        calls.append(1)
        raise _net.Missing("LA: returned 404")

    with pytest.raises(_net.Missing):
        la._retrying(gone)
    assert len(calls) == 1


def test_a_persistent_403_still_falls_through(monkeypatch):
    """A wall that does not clear is "we could not look", which must fall
    through to a weaker tier -- never be recorded as "Louisiana has nothing"."""
    from ev.adapters.base import NotYetPublished, SourceError

    monkeypatch.setattr(la, "_BACKOFF", 0)

    def blocked():
        raise SourceError("LA: returned HTTP 403")

    with pytest.raises(SourceError) as caught:
        la._retrying(blocked)
    assert not isinstance(caught.value, NotYetPublished)


def test_the_statewide_row_waits_until_every_parish_has_started():
    """A parish contributes nothing before its own first file, so a statewide
    sum taken earlier than the LAST parish's first file is short by whatever the
    late parishes had already recorded."""
    from ev.adapters import _fips

    daily = {name: {date(2024, 10, 17): 100} for name in _fips.names("LA")}
    daily["Orleans Parish"] = {date(2024, 10, 19): 500}
    result = la.build_series(daily, 2024, date(2024, 10, 19))
    assert [r.day for r in result.state_rows] == [date(2024, 10, 19)]
    assert result.state_rows[0].ballots_total == 63 * 100 + 500
    # The parishes that did start on 10/17 still get their own rows.
    assert any(r.day == date(2024, 10, 17) for r in result.county_rows)


def test_statewide_ballots_new_is_blank_unless_every_parish_filed():
    """Otherwise the day's figure silently omits whichever parishes did not
    post -- their new ballots are unknown, not zero."""
    from ev.adapters import _fips

    daily = {name: {date(2024, 10, 17): 100, date(2024, 10, 18): 10}
             for name in _fips.names("LA")}
    daily["Orleans Parish"] = {date(2024, 10, 17): 100}
    rows = {r.day: r for r in la.build_series(daily, 2024, date(2024, 10, 18)).state_rows}
    assert rows[date(2024, 10, 17)].ballots_new == 64 * 100
    assert rows[date(2024, 10, 18)].ballots_new is None
    assert rows[date(2024, 10, 18)].ballots_total == 64 * 100 + 63 * 10


def test_a_persistent_403_names_the_rate_ban(monkeypatch):
    """"HTTP 403" in ev_status.json reads like something to retry. This one is a
    per-IP ban that outlives the run, and the status file should say so."""
    from ev.adapters.base import SourceError

    monkeypatch.setattr(la, "_BACKOFF", 0)
    monkeypatch.setattr(la, "_MIN_INTERVAL", 0)

    def blocked():
        raise SourceError("LA: https://electionstatistics.sos.la.gov/ returned HTTP 403")

    with pytest.raises(SourceError) as caught:
        la._retrying(blocked)
    assert "refusing this IP" in str(caught.value)
