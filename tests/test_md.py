"""Maryland: the SBE early-voting RAW data file.

Two fixtures, both truncated from the real files with the header verbatim: the
2022 general (`GG22`, which carries the WCP party code) and the 2024 general
(`PG24`, which carries NLM instead). Between them they lock the two folder
naming conventions and the two minor-party vocabularies that would otherwise
break the adapter on a cycle boundary.

Every expected number below was recomputed straight from the fixture with a
plain `csv.DictReader` loop, independently of the adapter.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from ev.adapters import md
from ev.adapters._net import Missing
from ev.adapters.base import NotYetPublished, SchemaDrift

FIXTURES = Path(__file__).parent / "fixtures" / "md"

GG22 = FIXTURES / "GG22_EarlyVoting_RAW_data.csv"
PG24 = FIXTURES / "PG24_EarlyVoting_RAW_data.csv"

#: 2022 early voting ran 2022-10-27 .. 2022-11-03.
GG22_LAST = date(2022, 11, 3)
PG24_LAST = date(2024, 10, 31)


@pytest.fixture(scope="module")
def gen2022():
    return md.parse(GG22.read_bytes(), 2022, GG22_LAST)


@pytest.fixture(scope="module")
def gen2024():
    return md.parse(PG24.read_bytes(), 2024, PG24_LAST)


# --------------------------------------------------------------------------
# The positional day columns ARE the calendar
# --------------------------------------------------------------------------
def test_day_columns_map_onto_the_statutory_window():
    """Eight days ending the Thursday before a Tuesday Election Day."""
    assert md.day_dates(2022)[0] == date(2022, 10, 27)
    assert md.day_dates(2022)[-1] == date(2022, 11, 3)
    assert md.day_dates(2024)[0] == date(2024, 10, 24)
    assert md.day_dates(2024)[-1] == date(2024, 10, 31)
    assert md.day_dates(2026)[0] == date(2026, 10, 22)
    assert md.day_dates(2026)[-1] == date(2026, 10, 29)
    for cycle in (2022, 2024, 2026):
        days = md.day_dates(cycle)
        assert len(days) == 8
        assert days[0].weekday() == 3, "Day1 must be a Thursday"


def test_state_series_is_one_cumulative_row_per_day(gen2022):
    days = [r.day for r in gen2022.state_rows]
    assert days == md.day_dates(2022)
    totals = [r.ballots_total for r in gen2022.state_rows]
    assert totals == [31, 60, 91, 115, 152, 191, 236, 350]
    assert [r.ballots_new for r in gen2022.state_rows] == [
        31, 29, 31, 24, 37, 39, 45, 114
    ]
    assert totals == sorted(totals), "a cumulative series can never fall"


def test_as_of_truncates_the_series(gen2022):
    """The adapter runs daily; a mid-window run must not publish future days."""
    partial = md.parse(GG22.read_bytes(), 2022, date(2022, 10, 29))
    assert [r.day for r in partial.state_rows] == [
        date(2022, 10, 27), date(2022, 10, 28), date(2022, 10, 29)
    ]
    assert partial.state_rows[-1].ballots_total == 91


def test_a_run_before_early_voting_opens_is_not_yet_published():
    """This is the path that runs every day for weeks. It must STOP the ladder,
    not fall through to a source that would invent a zero."""
    with pytest.raises(NotYetPublished, match="early voting opens"):
        md.parse(GG22.read_bytes(), 2022, date(2022, 9, 5))


# --------------------------------------------------------------------------
# THE BLANK RULE, in both directions
# --------------------------------------------------------------------------
def test_mail_fields_are_blank_not_zero(gen2022, gen2024):
    """This file is the early-voting centres only. Maryland's mail ballots are a
    separate report, so `0` here would claim no Marylander voted by mail."""
    for result in (gen2022, gen2024):
        for row in result.state_rows:
            assert row.mail_requested is None
            assert row.mail_returned is None
        for row in result.county_rows:
            assert row.mail_returned is None


def test_party_fields_are_real_counts_not_blanks(gen2022):
    """Maryland registers by party, so a party with no ballots is a genuine 0 --
    blanking it would claim the state does not report party at all."""
    final = gen2022.state_rows[-1]
    assert final.party_dem == 214
    assert final.party_rep == 90
    assert final.party_npa == 32        # UNA
    assert final.party_oth == 14        # LIB + GRN + WCP + OTH
    assert final.party_dem + final.party_rep + final.party_npa + final.party_oth == 350
    for row in gen2022.state_rows:
        for field in ("party_dem", "party_rep", "party_npa", "party_oth"):
            assert isinstance(getattr(row, field), int)


def test_inperson_carries_the_total_and_is_never_blank(gen2022):
    for row in gen2022.state_rows:
        assert row.inperson == row.ballots_total


# --------------------------------------------------------------------------
# County rows are keyed by FIPS, never by name
# --------------------------------------------------------------------------
def test_county_rows_are_fips_keyed(gen2022):
    final = {r.county_fips: r for r in gen2022.county_rows if r.day == GG22_LAST}
    assert final["24001"].ballots_total == 83      # Allegany
    assert final["24003"].ballots_total == 6       # Anne Arundel
    assert final["24005"].ballots_total == 86      # Baltimore County
    assert final["24510"].ballots_total == 35      # Baltimore City
    assert final["24031"].ballots_total == 36      # Montgomery
    assert final["24033"].ballots_total == 52      # Prince George's
    assert final["24035"].ballots_total == 9       # Queen Anne's
    assert final["24037"].ballots_total == 43      # St. Mary's
    assert sum(r.ballots_total for r in final.values()) == 350


def test_baltimore_city_is_not_baltimore_county(gen2022):
    """The one name join in Maryland that a fuzzy match gets wrong."""
    final = {r.county_fips: r for r in gen2022.county_rows if r.day == GG22_LAST}
    assert final["24510"].county_name == "Baltimore city"
    assert final["24005"].county_name == "Baltimore County"
    assert final["24510"].ballots_total != final["24005"].ballots_total


def test_saints_and_apostrophes_resolve(gen2022):
    names = {r.county_fips: r.county_name for r in gen2022.county_rows}
    assert names["24037"] == "St. Mary's County"     # file says "Saint Mary's"
    assert names["24033"] == "Prince George's County"


def test_all_county_fips_are_five_digit_maryland(gen2022, gen2024):
    for result in (gen2022, gen2024):
        for row in result.county_rows:
            assert len(row.county_fips) == 5 and row.county_fips.startswith("24")


# --------------------------------------------------------------------------
# Demographics: sex only, and deliberately so
# --------------------------------------------------------------------------
def test_sex_is_published_cumulatively(gen2022):
    final = {r.bucket: r.ballots_total for r in gen2022.demo_rows
             if r.dimension == "sex" and r.day == GG22_LAST}
    assert final == {"female": 197, "male": 153}
    assert sum(final.values()) == 350


def test_age_is_never_published(gen2022, gen2024):
    """Maryland's 25-44 / 45-64 bands do not nest inside normalize.AGE_BANDS;
    age_band() would map them to 25-34 and 45-54 -- recognised, and wrong."""
    for result in (gen2022, gen2024):
        assert not [r for r in result.demo_rows if r.dimension == "age"]


def test_race_is_never_published(gen2022):
    assert not [r for r in gen2022.demo_rows if r.dimension == "race"]


# --------------------------------------------------------------------------
# The 2024 file: a different folder code and a different minor party
# --------------------------------------------------------------------------
def test_2024_file_parses_with_its_own_party_vocabulary(gen2024):
    assert [r.day for r in gen2024.state_rows] == md.day_dates(2024)
    final = gen2024.state_rows[-1]
    assert final.ballots_total == 323
    assert final.party_dem == 184
    assert final.party_rep == 98
    assert final.party_npa == 30
    assert final.party_oth == 11        # includes the four NLM ballots
    final_counties = {r.county_fips: r.ballots_total
                      for r in gen2024.county_rows if r.day == PG24_LAST}
    assert final_counties["24027"] == 179   # Howard
    assert final_counties["24047"] == 21    # Worcester


def test_maryland_minor_parties_bucket_to_other():
    assert md.party_bucket("WCP") == "oth"
    assert md.party_bucket("NLM") == "oth"
    assert md.party_bucket("UNA") == "npa"
    assert md.party_bucket("DEM") == "dem"
    assert md.party_bucket("  ") is None


def test_unknown_party_code_raises_drift():
    """A code in neither normalize nor Maryland's own table must fail loudly
    rather than land in party_oth."""
    with pytest.raises(SchemaDrift, match="party code"):
        md.party_bucket("ZZZ")


# --------------------------------------------------------------------------
# Drift
# --------------------------------------------------------------------------
def test_renamed_column_raises_drift():
    body = GG22.read_bytes().replace(b"PARTY_CODE", b"PARTY", 1)
    with pytest.raises(SchemaDrift, match="missing columns"):
        md.parse(body, 2022, GG22_LAST)


def test_a_ninth_day_column_raises_drift():
    """The day columns ARE the calendar. A longer window would silently redate
    every count, so it must fail rather than publish."""
    # Line-ending agnostic: git normalises CRLF to LF in the checked-in fixture,
    # and a b",Day8\r\n" match silently does nothing once that happens -- the test
    # then passes a valid file and reports "did not raise" for the wrong reason.
    raw = GG22.read_bytes()
    for eol in (b"\r\n", b"\n"):
        if b",Day8" + eol in raw:
            body = raw.replace(b",Day8" + eol, b",Day8,Day9" + eol, 1)
            break
    else:
        raise AssertionError("fixture has no Day8 header column to extend")
    with pytest.raises(SchemaDrift, match="day columns"):
        md.parse(body, 2022, GG22_LAST)


def test_unknown_county_name_raises_drift():
    body = GG22.read_bytes().replace(b",Allegany,", b",Atlantis,")
    with pytest.raises(SchemaDrift, match="unrecognised county"):
        md.parse(body, 2022, GG22_LAST)


def test_non_numeric_day_cell_raises_drift():
    body = GG22.read_bytes().replace(b"Allegany County Office Complex,,,,,,,,1",
                                     b"Allegany County Office Complex,,,,,,,,lots", 1)
    with pytest.raises(SchemaDrift, match="ballot count"):
        md.parse(body, 2022, GG22_LAST)


# --------------------------------------------------------------------------
# Absence: the normal outcome on most days
# --------------------------------------------------------------------------
def test_soft_404_html_page_is_not_yet_published():
    """The SBE CMS answers an unposted file with a styled 200 HTML shell -- that
    is literally what https://elections.maryland.gov/press_room/2026_stats/GG26/
    returns today, months before the 2026 general."""
    with pytest.raises(NotYetPublished):
        md.parse(b"<!doctype html><html><head><title>SBE</title></head></html>",
                 2026, date(2026, 11, 3))


def test_missing_file_is_not_yet_published(monkeypatch):
    def missing(url, **kwargs):
        raise Missing(f"MD: {url} returned 404")

    monkeypatch.setattr(md, "get", missing)
    with pytest.raises(NotYetPublished):
        md.MDScraper().fetch(2026, date(2026, 9, 5))


def test_html_shell_from_every_candidate_is_not_yet_published(monkeypatch):
    monkeypatch.setattr(md, "get", lambda url, **kw: b"<!DOCTYPE html><html>nope</html>")
    with pytest.raises(NotYetPublished):
        md.MDScraper().fetch(2026, date(2026, 9, 5))


# --------------------------------------------------------------------------
# URL selection: the general election, never the primary
# --------------------------------------------------------------------------
def test_general_paths_are_the_verified_folder_codes():
    assert md.general_paths(2022)[0].endswith(
        "2022_stats/GG22/EarlyVoting%20RAW%20data.csv")
    assert md.general_paths(2024)[1].endswith(
        "2024_stats/PG24/EarlyVoting%20RAW%20data.csv")
    assert md.general_paths(2026)[0].endswith(
        "2026_stats/GG26/EarlyVoting%20RAW%20data.csv")


def test_index_discovery_ignores_the_primary_and_the_cycle_root():
    """The bare `<cycle>_stats/` copy is the PRIMARY's file for both 2022 and
    2024. Reading it during the general would publish a third of the real
    number, confidently."""
    index = (
        '<a href="2026_stats/GP26/EarlyVoting RAW data.csv">primary</a>'
        '<a href="2026_stats/EarlyVoting RAW data.csv">cycle root</a>'
        '<a href="2026_stats/SPG26/EarlyVoting RAW data.csv">special</a>'
        '<a href="2024_stats/PG24/EarlyVoting RAW data.csv">wrong cycle</a>'
        '<a href="2026_stats/GG26/EarlyVoting RAW data.csv">general</a>'
    )
    assert md.discover(index, 2026) == [
        "https://elections.maryland.gov/press_room/"
        "2026_stats/GG26/EarlyVoting%20RAW%20data.csv"
    ]
    assert md.discover(index, 2024) == [
        "https://elections.maryland.gov/press_room/"
        "2024_stats/PG24/EarlyVoting%20RAW%20data.csv"
    ]


def test_discovery_survives_an_index_it_cannot_parse():
    assert md.discover("", 2026) == []
    assert md.discover("<html>no links here</html>", 2026) == []


def test_adapter_identity():
    scraper = md.MDScraper()
    assert (scraper.state, scraper.name, scraper.tier) == ("MD", "md-sbe", 1)
