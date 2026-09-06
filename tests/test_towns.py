"""The municipality lookup: name -> 10-digit Census county-subdivision GEOID.

`_towns` is to New England and the township belt what `_fips` is to the other
forty states, and it carries the same two guarantees: an unrecognised name
returns None, and an AMBIGUOUS one returns None too rather than being resolved
to whichever place is bigger.

Two fixtures, and they are the point of this file:

* `national_cousub2020_me_excerpt.txt` -- Maine's 530 rows of the real Census
  county-subdivision code file, header verbatim. The vendored table in `_towns`
  is machine-generated from that file, so this is the sample that catches a
  regeneration that went wrong.
* `theme_town_geoids.json` -- every `cousub_fp` in the site's shipped town
  geometry, for the six New England states plus Michigan and Wisconsin. A town
  we can key but not draw is a hole in the map, so the two sets must agree
  exactly. Snapshotted rather than read live: the pipeline must not depend on
  theme assets, but it does have to keep faith with them.
"""

from __future__ import annotations

import csv
import json
from datetime import date
from pathlib import Path

import pytest

from ev.adapters import _towns
from ev.publish import publish_town_daily
from ev.schema import TIER_SCRAPER, TOWN_DAILY_COLUMNS, Provenance, TownDay, town_row_to_dict

FIXTURES = Path(__file__).parent / "fixtures" / "towns"
ME_EXCERPT = FIXTURES / "national_cousub2020_me_excerpt.txt"
THEME_GEOIDS = FIXTURES / "theme_town_geoids.json"


# --------------------------------------------------------------------------
# The key: a GEOID, and the county is inside it
# --------------------------------------------------------------------------
def test_the_county_is_a_slice_of_the_town_geoid():
    """This is the whole reason a town->county rollup is exact. Auburn, Maine is
    state 23 + county 001 (Androscoggin) + subdivision 02060, so getting from a
    town to its county is a string slice and not a crosswalk that can be wrong."""
    assert _towns.lookup("ME", "Auburn") == ("2300102060", "Auburn city")
    assert _towns.county_of("2300102060") == "23001"


def test_a_geoid_that_is_not_a_geoid_is_refused():
    """A 5-digit county FIPS silently sliced to 5 digits would look like it
    worked, so the length is checked rather than assumed."""
    for bad in ("23001", "2300102060x", "", "230010206"):
        with pytest.raises(ValueError, match="10-digit"):
            _towns.county_of(bad)


# --------------------------------------------------------------------------
# Ambiguity: None, never a coin flip
# --------------------------------------------------------------------------
def test_a_bare_name_two_places_share_returns_none():
    """Maine's three. "Lincoln" is a town in Penobscot County and a plantation in
    Oxford; "Unity" is a town in Waldo and an unorganized territory in Kennebec;
    "Rangeley" is a town and a plantation, both in Franklin. Handing back the
    bigger one would silently put a thousand ballots in the wrong county."""
    assert _towns.lookup("ME", "Lincoln") is None
    assert _towns.lookup("ME", "Unity") is None
    assert _towns.lookup("ME", "Rangeley") is None


def test_the_qualified_form_of_the_same_name_resolves():
    """The ambiguity is in the bare name, not in the place. A source that says
    which one it means gets an answer -- including through the abbreviation
    Maine actually writes."""
    assert _towns.lookup("ME", "Lincoln Plt") == ("2301739422", "Lincoln plantation")
    assert _towns.lookup("ME", "Lincoln plantation") == ("2301739422", "Lincoln plantation")
    assert _towns.lookup("ME", "Lincoln town") == ("2301939475", "Lincoln town")
    assert _towns.lookup("ME", "Rangeley Plt") == ("2300761875", "Rangeley plantation")


def test_ambiguity_is_the_normal_case_in_the_township_belt():
    """Michigan and Wisconsin both have hundreds of names that are simultaneously
    a township and a city, so an adapter for either MUST pass the class word
    through. That is a feature of those files, not a defect of this table."""
    assert _towns.lookup("MI", "Adrian") is None
    assert _towns.lookup("MI", "Adrian city") == ("2609100440", "Adrian city")
    assert _towns.lookup("MI", "Adrian township") == ("2609100460", "Adrian township")
    assert _towns.lookup("WI", "Madison") is None
    assert _towns.lookup("WI", "Madison city") == ("5502548000", "Madison city")
    assert _towns.lookup("WI", "Madison town") == ("5502548025", "Madison town")


def test_unorganized_territory_is_not_matched_to_a_township():
    """Maine's file names individual unorganized townships; the Census names
    unorganized TERRITORIES, most of which aggregate many of them ("Central
    Aroostook UT"). "Argyle UT" happens to be exactly Argyle township, but
    accepting Twp as a synonym for UT to catch that one would file a dozen
    townships' ballots under "North Penobscot UT". So Twp does not match UT."""
    assert _towns.lookup("ME", "Argyle UT") == ("2301901500", "Argyle UT")
    assert _towns.lookup("ME", "Argyle Twp") is None
    assert _towns.lookup("ME", "T1 R9 WELS") is None
    assert _towns.lookup("ME", "Prentiss Twp T7 R3 NBPP") is None


# --------------------------------------------------------------------------
# Spelling
# --------------------------------------------------------------------------
def test_saint_and_st_are_the_same_place():
    assert _towns.lookup("VT", "St. Albans Town") == ("5001161750", "St. Albans town")
    assert _towns.lookup("VT", "Saint Albans Town") == ("5001161750", "St. Albans town")


def test_st_albans_without_the_class_word_is_ambiguous_in_vermont():
    """Vermont has both a St. Albans town and a St. Albans city, so the saint
    handling must not paper over a real collision."""
    assert _towns.lookup("VT", "St Albans") is None


def test_unrecognised_names_and_blanks_return_none():
    for name in (None, "", "   ", "Nowhere", "Auburn County"):
        assert _towns.lookup("ME", name) is None


# --------------------------------------------------------------------------
# What is carried, and what is not
# --------------------------------------------------------------------------
def test_only_states_whose_subdivisions_run_elections_are_carried():
    """Outside the twenty-one MCD states a "county subdivision" is a statistical
    box the Census Bureau drew, with no clerk and no ballots. Carrying those
    names would only invite a false match."""
    assert _towns.covered("ME") and _towns.covered("MI") and _towns.covered("PA")
    assert not _towns.covered("CA") and not _towns.covered("GA")
    assert _towns.lookup("CA", "Anaheim") is None
    assert len(_towns.states()) == 21
    assert set("CT MA ME NH RI VT".split()) <= set(_towns.states())


def test_every_geoid_is_ten_digits_and_unique_within_its_state():
    for state in _towns.states():
        seen = set()
        for name, geoid in _towns._entries(state):
            assert len(geoid) == 10 and geoid.isdigit(), (state, name, geoid)
            assert geoid not in seen, (state, geoid)
            seen.add(geoid)


def test_the_county_name_helper_agrees_with_the_county_table():
    assert _towns.county_name_of("ME", "23001") == "Androscoggin County"
    assert _towns.county_name_of("ME", "23019") == "Penobscot County"
    assert _towns.county_name_of("ME", "23999") == ""


# --------------------------------------------------------------------------
# The vendored table against its two sources
# --------------------------------------------------------------------------
def test_the_vendored_table_reproduces_the_census_file_exactly():
    """Maine's 530 rows out of the real code file, rebuilt and compared. This is
    the test that catches a bad regeneration -- a dropped county, a shifted
    field, a name silently retitled."""
    expected = set()
    for line in ME_EXCERPT.read_text(encoding="utf-8").splitlines()[1:]:
        f = line.split("|")
        if len(f) != 9 or f[7] == "Z9":
            continue
        expected.add((f[6], f[1] + f[2] + f[4]))
    assert set(_towns._entries("ME")) == expected
    assert len(expected) == 530


def test_every_town_we_can_key_is_a_town_the_site_can_draw():
    """The site ships town geometry keyed by the same `cousub_fp`. A GEOID in one
    set and not the other is either a town we publish with no shape or a shape
    with no data, so the two must agree exactly -- and they do, for all six New
    England states and for Michigan and Wisconsin."""
    snapshot = json.loads(THEME_GEOIDS.read_text())
    for state, geoids in snapshot.items():
        ours = {geoid for _, geoid in _towns._entries(state)}
        assert ours == set(geoids), state


# --------------------------------------------------------------------------
# The published table
# --------------------------------------------------------------------------
def _row(geoid="2301501010", day=None, **kw):
    fields = dict(ballots_total=163, ballots_new=4, mail_returned=101,
                  inperson=62, party_dem=85, party_rep=36, party_oth=10,
                  party_npa=32)
    fields.update(kw)
    return TownDay(cycle=2024, state="ME", town_geoid=geoid,
                   day=day or date(2024, 11, 5), town_name="Alna town",
                   provenance=Provenance(TIER_SCRAPER, "me-sos"), **fields)


def test_a_town_row_cannot_be_built_from_a_name_or_a_county():
    """The GEOID is checked at construction, not at write time, so a county FIPS
    or a town name can never get as far as the CSV."""
    for bad in ("23015", "Alna", "", "230150101"):
        with pytest.raises(ValueError, match="10-digit"):
            TownDay(cycle=2024, state="ME", town_geoid=bad, day=date(2024, 11, 5))


def test_the_published_row_derives_its_county_and_honours_the_blank_rule():
    """`county_fips` is written from the GEOID, never from a caller. And a count
    the source did not report writes an empty cell -- Maine reports every party
    bucket so those are real zeros, but `ballots_new` here is genuinely absent."""
    written = town_row_to_dict(_row(ballots_new=None, party_oth=0))
    assert list(written) == TOWN_DAILY_COLUMNS
    assert written["town_geoid"] == "2301501010"
    assert written["county_fips"] == "23015"
    assert written["ballots_new"] == ""     # not reported
    assert written["party_oth"] == "0"      # reported, and zero
    assert written["days_to_election"] == "0"


def test_town_rows_are_written_per_state_beside_the_county_file(tmp_path):
    """Same shape as `counties/<st>.csv`: one file per state, lazily fetched, so
    the page pays for the town view only when a reader switches to it."""
    publish_town_daily(tmp_path, "ME", [_row(), _row(geoid="2301901115")])
    path = tmp_path / "towns" / "me.csv"
    assert path.exists()
    rows = list(csv.DictReader(path.open()))
    assert [r["town_geoid"] for r in rows] == ["2301501010", "2301901115"]
    assert [r["county_fips"] for r in rows] == ["23015", "23019"]


def test_a_restated_town_is_replaced_and_a_thinner_one_is_not(tmp_path):
    """publish.py's merge rules apply here unchanged: a same-tier update wins,
    but a row that comes back with fewer populated columns is a truncated
    download rather than a correction, and the richer row on disk is kept."""
    publish_town_daily(tmp_path, "ME", [_row()])
    publish_town_daily(tmp_path, "ME", [_row(ballots_total=170)])
    publish_town_daily(tmp_path, "ME", [_row(ballots_total=1, party_dem=None,
                                             party_rep=None, party_npa=None)])
    rows = list(csv.DictReader((tmp_path / "towns" / "me.csv").open()))
    assert len(rows) == 1
    assert rows[0]["ballots_total"] == "170"
