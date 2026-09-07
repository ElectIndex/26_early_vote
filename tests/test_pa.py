"""Pennsylvania: the DoS mail-ballot dataset on data.pa.gov.

Every fixture here is a verbatim response from the live Socrata API, truncated
by asking for fewer rows rather than by editing what came back:

* `catalog_2024_general.json` / `catalog_2022_general.json` -- real discovery
  answers, three and one result deep. They carry the dataset id AND its column
  names and types, which is how one call does discovery and the schema check.
* `catalog_2026_general.json` -- the real answer today: PA has not opened the
  2026 general's dataset, so the fuzzy search returns older elections and
  nothing that matches. This is the NotYetPublished path, live.
* `2024_returned_by_county_party_day.json` -- the whole returned-ballot curve
  for Adams, Cameron and Philadelphia, in PA's cleaned 2024 vocabulary
  (DEM/REP/OTH/LIB/GRN) which has no unaffiliated bucket at all.
* `2024_approved_apps_by_day.json` -- approved applications per day for the
  same three counties, including the file's real 1947 and 1954 keying typos.
* `2022_returned_cameron_*.json` -- the older vocabulary, where PA still
  published "NF" for no affiliation next to a long free-text tail.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from ev.adapters import pa
from ev.adapters._net import Missing
from ev.adapters.base import NotYetPublished, SchemaDrift, SourceError

FIXTURES = Path(__file__).parent / "fixtures" / "pa"

AS_OF = date(2024, 10, 15)


def load(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def raw(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


@pytest.fixture(scope="module")
def gen2024():
    return pa.build(load("2024_returned_by_county_party_day.json"),
                    load("2024_approved_apps_by_day.json"), 2024, AS_OF)


@pytest.fixture(scope="module")
def state(gen2024):
    return gen2024.state_rows[-1]


@pytest.fixture(scope="module")
def counties(gen2024):
    return {row.county_fips: row for row in gen2024.county_rows if row.day == AS_OF}


# --------------------------------------------------------------------------
# Discovery: the dataset id is never constructed
# --------------------------------------------------------------------------
def test_the_dataset_id_is_looked_up_by_name():
    """Every election gets a fresh unguessable four-character id, and the fuzzy
    search returns four other elections alongside the right one."""
    dataset, columns = pa.discover(raw("catalog_2024_general.json"), 2024)
    assert dataset == "3q5t-ddp8"
    assert columns[pa.RETURNED] == "text"
    assert columns[pa.REQUESTED] == "calendar date"
    pa.check_columns(columns, dataset)


def test_a_cycle_with_no_dataset_yet_is_not_yet_published():
    """The live answer today: data.pa.gov offers 2020, 2021 and 2022 for this
    query and nothing for 2026, because the file does not exist yet."""
    with pytest.raises(NotYetPublished, match="2026 General Election Mail Ballot Requests"):
        pa.discover(raw("catalog_2026_general.json"), 2026)


def test_missing_column_raises_drift():
    columns = {pa.COUNTY: "text", pa.PARTY: "text", pa.RETURNED: "text"}
    with pytest.raises(SchemaDrift, match="missing columns"):
        pa.check_columns(columns, "3q5t-ddp8")


def test_a_date_column_that_stops_being_a_date_raises_drift():
    """`ballotreturneddate` has been text in one cycle and a calendar date in
    the next -- both compare correctly against 'YYYY-MM-DD'. A number would
    still answer the comparison, just not the one we mean."""
    columns = {pa.COUNTY: "text", pa.PARTY: "text",
               pa.RETURNED: "number", pa.REQUESTED: "calendar date"}
    with pytest.raises(SchemaDrift, match="not a date"):
        pa.check_columns(columns, "3q5t-ddp8")


# --------------------------------------------------------------------------
# THE BLANK RULE: npa is unreportable in PA's current vocabulary
# --------------------------------------------------------------------------
def test_npa_is_blank_when_pa_stops_reporting_it(state, counties):
    """From 2024 PA publishes DEM/REP/OTH/LIB/GRN and folds the unaffiliated
    into OTH. A 0 here would say no unaffiliated Pennsylvanian has voted; blank
    says PA no longer tells us, which is the truth."""
    assert state.party_npa is None
    assert all(row.party_npa is None for row in counties.values())
    assert state.party_oth == 7138  # OTH + LIB + GRN, all of them real parties to us


def test_the_older_vocabulary_still_yields_a_real_npa_count():
    """2022 published "NF" -- no affiliation -- which normalize maps to npa. The
    same code path that blanks npa above must produce a number here, or the
    blank would just be a bug that happens to look principled."""
    result = pa.build(load("2022_returned_cameron_dem_rep_nf.json"), None,
                      2022, date(2022, 11, 8))
    final = result.state_rows[-1]
    assert final.party_npa == 21
    assert (final.party_dem, final.party_rep) == (177, 190)
    # ... and oth goes blank in turn, because this response has no oth label.
    assert final.party_oth is None


def test_a_bucket_that_is_blank_is_never_a_zero(gen2024):
    for row in gen2024.state_rows + gen2024.county_rows:
        assert row.party_npa is None
        assert row.inperson is None


def test_free_text_party_labels_are_excluded_never_bucketed():
    """The 2020 and 2022 files carry the raw registration string. PA's own
    abbreviations are in PA_PARTY; the residue ("CL" here) is not, and the one
    thing that must never happen to it is landing in party_oth -- that would
    publish a confident wrong third-party share, which is rule 3.

    Cameron 2022 is the whole argument in 407 ballots: 177 D, 190 R, 38 across
    the no-affiliation family, 1 OTH -- and 1 "CL" that is counted in the total
    and in NO bucket at all. The buckets fall one short of the total on purpose.
    """
    result = pa.build(load("2022_returned_cameron_all_parties.json"), None,
                      2022, date(2022, 11, 8))
    final = result.state_rows[-1]

    assert final.ballots_total == 407
    assert (final.party_dem, final.party_rep) == (177, 190)
    assert final.party_npa == 38          # NF + I + NO + NON + NOP
    assert final.party_oth == 1           # the OTH row, and ONLY the OTH row

    # The gap is the point: "CL" is a returned ballot, so it counts; it is not a
    # party we can name, so it is in no party bucket.
    assert (final.party_dem + final.party_rep + final.party_npa
            + final.party_oth) == final.ballots_total - 1


def test_too_much_unreadable_party_is_still_refused():
    """Excluding a rounding error is not the same as tolerating drift. Past
    MAX_UNKNOWN_PARTY_SHARE the vocabulary really has moved, and publishing a
    party split off the remainder would be inventing one."""
    rows = [{"countyname": "CAMERON", "party": "DEM", "d": "2022-10-01",
             "n": "1000"},
            {"countyname": "CAMERON", "party": "ZZQ", "d": "2022-10-01",
             "n": "50"}]
    with pytest.raises(SchemaDrift, match="cannot read"):
        pa.build(rows, None, 2022, date(2022, 11, 8))


def test_pennsylvania_has_no_in_person_early_voting(state):
    """Voting "on demand" at a county office is legally a mail ballot applied
    for, issued and returned in one visit, and the file does not mark it. That
    is not the same as zero people doing it."""
    assert state.inperson is None


# --------------------------------------------------------------------------
# Counties are FIPS-keyed
# --------------------------------------------------------------------------
def test_counties_are_five_digit_fips(counties):
    assert set(counties) == {"42001", "42023", "42101"}
    for fips, row in counties.items():
        assert len(fips) == 5 and fips.startswith("42")
        assert row.county_name.endswith(" County")


def test_county_numbers_are_pas_own(counties):
    assert counties["42001"].ballots_total == 4426          # Adams
    assert (counties["42001"].party_dem, counties["42001"].party_rep) == (2168, 1821)
    assert counties["42023"].ballots_total == 257           # Cameron, the smallest
    assert counties["42101"].county_name == "Philadelphia County"
    assert counties["42101"].ballots_total == 86506


def test_statewide_is_the_sum_of_the_counties(state, counties):
    """PA's file is one row per application with no suppression and no gaps, so
    summing is exact -- unlike Michigan, where a masked cell makes a roll-up
    unstateable."""
    assert state.ballots_total == sum(row.ballots_total for row in counties.values())


def test_unknown_county_name_raises_drift():
    with pytest.raises(SchemaDrift, match="unrecognised county names"):
        pa.build([{"countyname": "ATLANTIS", "party": "DEM",
                   "d": "2024-10-01", "n": "5"}], None, 2024, AS_OF)


# --------------------------------------------------------------------------
# The curve
# --------------------------------------------------------------------------
def test_one_query_rebuilds_the_whole_daily_curve(gen2024):
    days = [row.day for row in gen2024.state_rows]
    assert days[0] == date(2024, 7, 8)
    assert days[-1] == AS_OF
    assert all(b.day.toordinal() - a.day.toordinal() == 1
               for a, b in zip(gen2024.state_rows, gen2024.state_rows[1:]))
    totals = [row.ballots_total for row in gen2024.state_rows]
    assert totals == sorted(totals)


def test_applications_filed_before_the_axis_are_carried_not_dropped(gen2024):
    """PA's permanent mail-ballot list carries applications filed the previous
    winter, and the file holds typos dated 1947. Both are real requests as of
    day one of the published axis rather than a reason to widen it by 77 years."""
    opening = gen2024.state_rows[0]
    assert len(gen2024.state_rows) == 100    # capped, not stretched back to 1947
    assert opening.mail_requested == 94543   # everything filed before 2024-07-08
    assert opening.ballots_total == 0        # but no ballot had come back yet


def test_mail_requested_is_blank_when_approval_cannot_be_told():
    """The 2022 dataset has no disposition column, so counting its applications
    would silently include the declined ones."""
    _, columns = pa.discover(raw("catalog_2022_general.json"), 2022)
    assert pa.DISPOSITION not in columns
    result = pa.build(load("2022_returned_cameron_dem_rep_nf.json"), None,
                      2022, date(2022, 11, 8))
    assert all(row.mail_requested is None for row in result.state_rows)


def test_a_county_appears_only_once_its_first_ballot_is_back(gen2024):
    """Philadelphia's returns start six weeks before Cameron's; a row of zeroes
    in between would read as "nobody in Cameron has voted" rather than "Cameron
    has not started"."""
    by_fips: dict[str, list] = {}
    for row in gen2024.county_rows:
        by_fips.setdefault(row.county_fips, []).append(row)
    assert by_fips["42101"][0].day == date(2024, 9, 1)
    assert by_fips["42023"][0].day == date(2024, 10, 8)
    assert all(row.ballots_total > 0 for rows in by_fips.values() for row in rows)


def test_as_of_truncates_the_curve():
    early = pa.build(load("2024_returned_by_county_party_day.json"),
                     load("2024_approved_apps_by_day.json"), 2024, date(2024, 9, 30))
    assert early.state_rows[-1].day == date(2024, 9, 30)
    assert early.state_rows[-1].ballots_total < 91189


def test_which_buckets_are_reportable_does_not_depend_on_the_run_date():
    """Whether PA reports the unaffiliated is a property of the FILE, not of how
    far into the cycle we are. Reading the vocabulary only from rows dated on or
    before `as_of` would blank a real party bucket in July and fill it in
    October, so a bucket would flip between "zero" and "not reported" mid-season
    for no reason in the data."""
    rows = load("2024_returned_by_county_party_day.json")
    apps = load("2024_approved_apps_by_day.json")
    for as_of in (date(2024, 7, 10), date(2024, 9, 2), AS_OF):
        final = pa.build(rows, apps, 2024, as_of).state_rows[-1]
        assert final.party_npa is None      # never reported, at any run date
        assert final.party_oth is not None  # always reported, even at zero


# --------------------------------------------------------------------------
# The live path
# --------------------------------------------------------------------------
def test_fetch_stops_at_discovery_when_the_cycle_has_no_dataset(monkeypatch):
    """No 2026 dataset means no SoQL query is even attempted."""
    calls: list[str] = []

    def fake_get(url, **kwargs):
        calls.append(url)
        return raw("catalog_2026_general.json")

    monkeypatch.setattr(pa, "get", fake_get)
    with pytest.raises(NotYetPublished):
        pa.PAScraper().fetch(2026, date(2026, 9, 5))
    assert calls == [pa.CATALOG_URL]


def test_an_unreachable_catalog_falls_through_rather_than_stopping(monkeypatch):
    """A 404 on Socrata itself is "we could not get the data", not "there is no
    data" -- SourceError so the ladder tries the aggregator."""
    def missing(url, **kwargs):
        raise Missing(f"PA: {url} returned 404")

    monkeypatch.setattr(pa, "get", missing)
    with pytest.raises(SourceError):
        pa.PAScraper().fetch(2026, date(2026, 9, 5))


def test_an_opened_but_empty_dataset_is_not_yet_published(monkeypatch):
    """PA creates the table before the first application lands."""
    def fake_get(url, **kwargs):
        return raw("catalog_2024_general.json") if url == pa.CATALOG_URL else b"[]"

    monkeypatch.setattr(pa, "get", fake_get)
    with pytest.raises(NotYetPublished, match="has no rows"):
        pa.PAScraper().fetch(2024, date(2024, 1, 1))


def test_non_json_from_socrata_is_a_source_error():
    with pytest.raises(SourceError, match="was not JSON"):
        pa.discover(b"<html>maintenance</html>", 2024)


def test_adapter_identity():
    scraper = pa.PAScraper()
    assert (scraper.state, scraper.name, scraper.tier) == ("PA", "pa-dos", 1)
