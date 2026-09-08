"""Wisconsin: the WEC county absentee export.

All four fixtures are whole real files, unedited -- they are only 5KB each.

* `County Absentee Counts as of October 30, 2024.csv` -- mid-window, in-person
  absentee running, WEC's dated 2024 scheme.
* `AbsenteeCounts_County_2026 Partisan Primary.csv` -- the live export in
  September 2026. Its TOTAL row leaves InPersonAbsentee blank while its counties
  report 0, which is exactly the distinction between "not reported" and "none".
  It is also the WRONG ELECTION, and `parse` refuses it by default.
* `2022-10-26_AbsenteeCounts_County.csv` / `2022-11-08_AbsenteeCounts_County.csv`
  -- two days of the 2022 general, out of the Wayback Machine via the Drupal
  media ids in `wi.HISTORY_MEDIA` (media 17521 and 17836). 2022 published no
  dated filenames at all, so these carry no as-of date of their own: the day
  comes from the node page that linked them.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from ev.adapters import wi
from ev.adapters._net import Missing
from ev.adapters.base import FetchResult, NotYetPublished, SchemaDrift, SourceError
from ev.schema import Provenance, TIER_SCRAPER, county_row_to_dict, state_row_to_dict

FIXTURES = Path(__file__).parent / "fixtures" / "wi"
DATED_2024 = FIXTURES / "County Absentee Counts as of October 30, 2024.csv"
CURRENT_2026 = FIXTURES / "AbsenteeCounts_County_2026 Partisan Primary.csv"
GEN2022_MID = FIXTURES / "2022-10-26_AbsenteeCounts_County.csv"
GEN2022_END = FIXTURES / "2022-11-08_AbsenteeCounts_County.csv"


@pytest.fixture(scope="module")
def gen2024():
    return wi.parse(DATED_2024.read_bytes(), 2024, date(2024, 10, 30))


@pytest.fixture(scope="module")
def primary2026():
    """Read on purpose, by naming the election -- see test_the_primary_is_refused
    for what happens when nobody names it."""
    return wi.parse(CURRENT_2026.read_bytes(), 2026, date(2026, 8, 13),
                    election="2026 Partisan Primary")


@pytest.fixture(scope="module")
def gen2022():
    return wi.parse(GEN2022_MID.read_bytes(), 2022, date(2022, 10, 26))


# --------------------------------------------------------------------------
# THE BLANK RULE
# --------------------------------------------------------------------------
def test_no_party_field_is_ever_populated(gen2024, primary2026):
    """Wisconsin has no party registration; a 0 would claim zero Democrats voted."""
    for result in (gen2024, primary2026):
        for row in result.state_rows + result.county_rows:
            for field in ("party_dem", "party_rep", "party_oth", "party_npa"):
                assert getattr(row, field) is None, (field, row)


def test_party_fields_write_as_blank_cells_not_zeros(gen2024):
    prov = Provenance(tier=TIER_SCRAPER, name="wi-wec")
    state = state_row_to_dict(FetchResult(state_rows=[gen2024.state_rows[0]])
                              .stamp(prov).state_rows[0])
    county = county_row_to_dict(FetchResult(county_rows=[gen2024.county_rows[0]])
                                .stamp(prov).county_rows[0])
    for cells in (state, county):
        for field in ("party_dem", "party_rep", "party_oth", "party_npa"):
            assert cells[field] == "", (field, cells)


def test_a_blank_in_person_cell_blanks_the_mail_split_rather_than_inflating_it(
        primary2026):
    """WEC leaves InPersonAbsentee empty on the 2026 primary's TOTAL row. The
    return count is still known, but how it splits is not -- and reporting the
    whole of it as mail would invent 90,828 mail ballots."""
    (state,) = primary2026.state_rows
    assert state.ballots_total == 90828      # BallotsReturned, reported
    assert state.mail_requested == 258856    # BallotsSent, reported
    assert state.inperson is None            # blank cell, NOT zero
    assert state.mail_returned is None       # therefore unknowable


def test_a_reported_zero_survives_as_zero(primary2026):
    """The counties in that same file report 0 in person, which is a real
    measurement and must not be flattened to blank."""
    adams = next(r for r in primary2026.county_rows if r.county_fips == "55001")
    assert adams.inperson == 0
    assert adams.ballots_total == 230
    assert adams.mail_returned == 230


def test_the_blank_and_the_zero_render_differently(primary2026):
    prov = Provenance(tier=TIER_SCRAPER, name="wi-wec")
    stamped = FetchResult(state_rows=list(primary2026.state_rows),
                          county_rows=list(primary2026.county_rows)).stamp(prov)
    assert state_row_to_dict(stamped.state_rows[0])["inperson"] == ""
    adams = next(r for r in stamped.county_rows if r.county_fips == "55001")
    assert county_row_to_dict(adams)["inperson"] == "0"


# --------------------------------------------------------------------------
# The arithmetic between the four columns
# --------------------------------------------------------------------------
def test_returned_is_inclusive_so_mail_is_the_difference(gen2024):
    """BallotsReturned counts in-person absentee too, so mail_returned is
    returned minus in person -- 1,109,037 back of which 609,461 in person."""
    (state,) = gen2024.state_rows
    assert state.state == "WI" and state.day == date(2024, 10, 30)
    assert state.ballots_total == 1109037
    assert state.inperson == 609461
    assert state.mail_returned == 1109037 - 609461
    assert state.mail_requested == 1247460   # BallotsSent, not applications


def test_statewide_is_wecs_own_total_row_not_our_sum(gen2024):
    (state,) = gen2024.state_rows
    assert state.ballots_total == sum(r.ballots_total for r in gen2024.county_rows)
    assert "TOTAL" not in {r.county_name for r in gen2024.county_rows}
    assert all(r.county_fips != "55000" for r in gen2024.county_rows)


# --------------------------------------------------------------------------
# Counties, keyed by FIPS
# --------------------------------------------------------------------------
def test_all_seventy_two_counties_are_five_digit_fips(gen2024):
    assert len(gen2024.county_rows) == 72
    for row in gen2024.county_rows:
        assert len(row.county_fips) == 5 and row.county_fips.startswith("55")
    by_fips = {r.county_fips: r for r in gen2024.county_rows}
    assert by_fips["55025"].county_name == "Dane County"
    assert by_fips["55079"].county_name == "Milwaukee County"
    dane = by_fips["55025"]
    assert (dane.ballots_total, dane.inperson) == (146265, 75592)


def test_no_municipality_crosswalk_is_needed(gen2024):
    """Michigan's problem does not arise: WEC rolls the 1,850 municipalities up
    itself and we take the county file, so no name join is ever attempted."""
    assert "AbsenteeCounts_Muni" not in wi.CURRENT_FILE


# --------------------------------------------------------------------------
# Drift and absence
# --------------------------------------------------------------------------
def test_renamed_column_raises_drift():
    body = (b'"Election","HINDI","Jurisdiction","AbsenteeApplications",'
            b'"BallotsIssued","BallotsReturned","InPersonAbsentee"\n')
    with pytest.raises(SchemaDrift, match="unexpected header"):
        wi.parse(body, 2024, date(2024, 10, 30))


def test_a_file_from_another_cycle_raises_drift():
    """A stale export left in place would otherwise publish 2024's counts under
    the 2026 key."""
    with pytest.raises(SchemaDrift, match="not the 2026 cycle"):
        wi.parse(DATED_2024.read_bytes(), 2026, date(2026, 10, 30))


def test_unknown_county_name_raises_drift():
    header = DATED_2024.read_text(encoding="utf-8-sig").splitlines()[0]
    body = "\n".join([header, '"2024 General Election","99","ATLANTIS COUNTY","1","1","1","0"']).encode()
    with pytest.raises(SchemaDrift, match="unrecognised county"):
        wi.parse(body, 2024, date(2024, 10, 30))


def test_an_html_page_instead_of_the_csv_is_a_source_error():
    with pytest.raises(SourceError):
        wi.parse(b"<!DOCTYPE html><html>nope</html>", 2026, date(2026, 11, 3))


def test_missing_export_is_not_yet_published(monkeypatch):
    """The path that runs every day until WEC posts the general's first export."""
    def missing(url, **kwargs):
        raise Missing(f"WI: {url} returned 404")

    monkeypatch.setattr(wi, "get", missing)
    with pytest.raises(NotYetPublished, match="no absentee export posted"):
        wi.WIScraper().fetch(2026, date(2026, 9, 5))


def test_soft_404_html_page_is_not_yet_published(monkeypatch):
    """WEC answers a missing file with a 91KB styled page, sometimes at 200."""
    monkeypatch.setattr(wi, "get", lambda url, **kw: b"<!DOCTYPE html><html>nope</html>")
    with pytest.raises(NotYetPublished):
        wi.WIScraper().fetch(2026, date(2026, 9, 5))


def test_the_dated_file_wins_over_the_election_keyed_one(monkeypatch):
    """Its name states the as-of date, so it needs no inference at all."""
    served = []

    def fake_get(url, **kwargs):
        served.append(url)
        if "as%20of" in url:
            return DATED_2024.read_bytes()
        raise Missing(f"WI: {url} returned 404")

    monkeypatch.setattr(wi, "get", fake_get)
    result = wi.WIScraper().fetch(2024, date(2024, 10, 30))
    assert {r.day for r in result.state_rows} == {date(2024, 10, 30)}
    # One request: the election-keyed candidates were never reached.
    assert len(served) == 1


def test_the_newest_drupal_copy_wins_when_there_is_no_dated_file(monkeypatch):
    """Drupal renames a re-upload to `_1`, `_2`, ...; through October 2024 the
    live export was `_8` and `_9`. Cumulative counts only rise, so the copy
    reporting the most ballots sent is the newest one."""
    older = DATED_2024.read_bytes()
    newer = older.replace(b'"1247460"', b'"1300000"')

    def fake_get(url, **kwargs):
        if "as%20of" in url:
            raise Missing("no dated file")
        if url.endswith("_2.csv"):
            return newer
        if url.endswith("Election.csv"):
            return older
        raise Missing(f"WI: {url} returned 404")

    monkeypatch.setattr(wi, "get", fake_get)
    result = wi.WIScraper().fetch(2024, date(2024, 10, 30))
    (state,) = result.state_rows
    assert state.mail_requested == 1300000


def test_dated_filename_has_no_leading_zero_on_the_day():
    assert wi.dated_filename(date(2024, 10, 2)) == \
        "County Absentee Counts as of October 2, 2024.csv"
    assert wi.dated_filename(date(2024, 11, 7)) == \
        "County Absentee Counts as of November 7, 2024.csv"


def test_history_for_the_live_cycle_is_refused():
    with pytest.raises(NotYetPublished, match="not an archived cycle"):
        wi.WIScraper().fetch_history(date.today().year)


def test_history_walks_the_dated_archive_and_skips_its_holes(monkeypatch):
    """WEC posted no dated file on several days; a hole is skipped, never
    interpolated and never filled with a zero."""
    posted = {
        wi.dated_filename(date(2024, 10, 30)): DATED_2024.read_bytes(),
    }

    def fake_get(url, **kwargs):
        for name, body in posted.items():
            if name.replace(" ", "%20").replace(",", "%2C") in url:
                return body
        raise Missing(f"WI: {url} returned 404")

    monkeypatch.setattr(wi, "get", fake_get)
    result = wi.WIScraper().fetch_history(2024)
    assert {r.day for r in result.state_rows} == {date(2024, 10, 30)}
    assert len(result.county_rows) == 72


# --------------------------------------------------------------------------
# The primary/general trap
# --------------------------------------------------------------------------
def test_the_primary_is_refused_by_default():
    """The 2026 partisan primary's file is a real, well-formed, fully populated
    WEC export for the right YEAR. Publishing it as the 2026 general would put a
    primary's 90,828 returned ballots on the general's curve. A year-only guard
    let it through; naming the election is what stops it."""
    with pytest.raises(NotYetPublished, match="2026 Partisan Primary"):
        wi.parse(CURRENT_2026.read_bytes(), 2026, date(2026, 8, 13))


def test_the_primary_stops_the_ladder_rather_than_falling_through(monkeypatch):
    """NotYetPublished, not SchemaDrift: the general's file is not posted yet, so
    there is nothing for a weaker tier to be better than."""
    monkeypatch.setattr(wi, "get", lambda url, **kw: CURRENT_2026.read_bytes())
    with pytest.raises(NotYetPublished):
        wi.WIScraper().fetch(2026, date(2026, 8, 13))


def test_the_2022_files_carry_no_election_in_their_name_only_inside():
    """The live store still serves `AbsenteeCounts_County__1.csv`, which is the
    2022 PARTISAN PRIMARY under a name that says nothing at all. Only the
    Election column can tell the two 2022 files apart."""
    header = GEN2022_MID.read_text(encoding="utf-8-sig").splitlines()[0]
    body = "\n".join([header,
                      '"2022 Partisan Primary","01","ADAMS COUNTY","796","758","459","74"',
                      '"2022 Partisan Primary","99999","TOTAL","1","1","1","0"']).encode()
    with pytest.raises(NotYetPublished, match="2022 Partisan Primary"):
        wi.parse(body, 2022, date(2022, 10, 26))


# --------------------------------------------------------------------------
# 2022: the Drupal-node archive
# --------------------------------------------------------------------------
def test_2022_parses_to_wecs_own_canonical_values(gen2022):
    (state,) = gen2022.state_rows
    assert (state.cycle, state.state, state.day) == (2022, "WI", date(2022, 10, 26))
    assert state.mail_requested == 504321          # BallotsSent
    assert state.ballots_total == 305453           # BallotsReturned, inclusive
    assert state.inperson == 33644
    assert state.mail_returned == 305453 - 33644   # 271,809
    assert len(gen2022.county_rows) == 72
    by_fips = {r.county_fips: r for r in gen2022.county_rows}
    assert by_fips["55025"].county_name == "Dane County"
    assert (by_fips["55025"].ballots_total, by_fips["55025"].inperson) == (47400, 4657)
    assert (by_fips["55025"].mail_returned, by_fips["55025"].ballots_new) == (42743, None)
    assert by_fips["55079"].county_name == "Milwaukee County"
    assert (by_fips["55079"].ballots_total, by_fips["55079"].inperson) == (49078, 5125)


def test_2022_final_day_is_wisconsins_own_total():
    result = wi.parse(GEN2022_END.read_bytes(), 2022, date(2022, 11, 8))
    (state,) = result.state_rows
    assert state.ballots_total == 741795
    assert state.inperson == 318130
    assert state.mail_returned == 741795 - 318130
    assert state.ballots_total == sum(r.ballots_total for r in result.county_rows)


def test_2022_has_no_party_anywhere(gen2022):
    for row in gen2022.state_rows + gen2022.county_rows:
        for field in ("party_dem", "party_rep", "party_oth", "party_npa"):
            assert getattr(row, field) is None


def test_the_2022_index_is_one_row_per_publishing_day_and_only_2022():
    days = [d for d, _stamp, _media in wi.HISTORY_MEDIA[2022]]
    assert len(days) == len(set(days)) == 22
    assert days == sorted(days)
    assert days[0] == date(2022, 10, 12) and days[-1] == date(2022, 11, 8)
    assert all(d.year == 2022 for d in days)
    medias = [m for _d, _s, m in wi.HISTORY_MEDIA[2022]]
    # A fresh media id per day is what makes the id a date rather than a guess.
    assert len(set(medias)) == 22 and medias == sorted(medias)
    assert set(wi.HISTORY_MEDIA) == {2022}


def test_history_walks_the_media_index_for_2022(monkeypatch):
    """2022 must NOT probe the dated filenames -- they were never posted, and
    fifty 404s at WEC is fifty requests for nothing."""
    bodies = {17521: GEN2022_MID.read_bytes(), 17836: GEN2022_END.read_bytes()}
    asked = []

    def fake_get(url, **kwargs):
        asked.append(url)
        assert "elections.wi.gov/sites/default/files" not in url
        for media, body in bodies.items():
            if f"/media/{media}/download" in url:
                return body
        raise Missing(f"WI: {url} returned 404")

    monkeypatch.setattr(wi, "get", fake_get)
    result = wi.WIScraper().fetch_history(2022)
    assert {r.day for r in result.state_rows} == {date(2022, 10, 26), date(2022, 11, 8)}
    assert len(result.county_rows) == 144
    assert len(asked) == 22
    assert all("web.archive.org" in u for u in asked)


def test_a_2022_day_that_names_another_election_is_skipped_not_published(monkeypatch):
    """A mis-keyed media id would be caught here rather than silently dated."""
    def fake_get(url, **kwargs):
        if "/media/17836/download" in url:
            return GEN2022_END.read_bytes()
        return CURRENT_2026.read_bytes()   # right shape, wrong election

    monkeypatch.setattr(wi, "get", fake_get)
    result = wi.WIScraper().fetch_history(2022)
    assert {r.day for r in result.state_rows} == {date(2022, 11, 8)}


def test_history_for_the_live_cycle_is_refused_even_though_2022_is_indexed():
    """Guard parity: the cycle gate runs before the media index is consulted."""
    with pytest.raises(NotYetPublished, match="not an archived cycle"):
        wi.WIScraper().fetch_history(date.today().year)


def test_adapter_identity():
    scraper = wi.WIScraper()
    assert (scraper.state, scraper.name, scraper.tier) == ("WI", "wi-wec", 1)
