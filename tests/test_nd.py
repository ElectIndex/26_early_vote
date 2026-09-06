"""North Dakota: the SoS voter portal's absentee / early-voting page.

Five fixtures, captured live 2026-09-06:

* `abev_2024_general.html` -- the statewide page for `eid=333`, whole and
  verbatim, including its WebForms hidden fields. It is what the county
  postbacks are replayed from.
* `abev_2024_general_cass.html` -- the county panel for Cass, a county that DOES
  run early voting.
* `abev_2024_general_adams.html` -- the county panel for Adams, a county that
  does NOT. Its early-vote block is absent from the markup entirely rather than
  printed as zero, which is the single most consequential quirk in this source.
* `candidatelist_2026_general.html` / `candidatelist_2026_primary.html` -- the
  election-name headers for `eid=348` and `eid=346`, TRUNCATED (the live pages
  are 115 KB and 312 KB of candidate table) to the real `<head>` plus the
  verbatim header element. The primary's is here because it is two ids away from
  the general's and is what the guard exists to refuse.

The county-name -> FIPS join and the statewide arithmetic are checked against
North Dakota's own published totals, which the harvested counties reproduce
exactly.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import pytest

from ev.adapters import nd
from ev.adapters.base import NotYetPublished, SchemaDrift, SourceError

FIXTURES = Path(__file__).parent / "fixtures" / "nd"

#: North Dakota's own statewide figures for the 2024 general, as printed on the
#: page. The 53 harvested counties sum to the first three exactly.
STATE_2024 = {"sent": 95908, "returned": 91556, "early": 99007, "cast": 190563}


def _text(name: str) -> str:
    return (FIXTURES / name).read_text()


@pytest.fixture
def statewide():
    return _text("abev_2024_general.html")


# --------------------------------------------------------------------------
# The election id -- the one thing that can silently be a different election
# --------------------------------------------------------------------------
def test_the_general_is_recognised_by_name():
    header = nd.identify(_text("candidatelist_2026_general.html"))
    assert header == "2026 General Election Contest/Candidate List"
    assert nd.is_general(header, 2026)


def test_the_primary_two_ids_away_is_refused():
    """eid 346 is the 2026 PRIMARY and 348 the general. This is the guard."""
    header = nd.identify(_text("candidatelist_2026_primary.html"))
    assert header == "2026 Primary Election Contest/Candidate List"
    assert not nd.is_general(header, 2026)


def test_the_right_election_in_the_wrong_cycle_is_refused():
    assert not nd.is_general("2024 General Election Contest/Candidate List", 2026)
    assert not nd.is_general("2026 General Election Runoff", 2024)


def test_specials_and_runoffs_do_not_match():
    for header in (
        "2026 Special Election Contest/Candidate List",
        "Special 2026 General Election",
        "2026 Statewide Primary Election",
    ):
        assert not nd.is_general(header, 2026)


def test_a_page_with_no_header_is_drift():
    with pytest.raises(SchemaDrift):
        nd.identify("<html><body>nothing here</body></html>")


def test_the_pinned_ids_are_the_generals():
    """2022, 2024 and 2026 -- and 346, the 2026 primary, is not among them."""
    assert nd.ELECTION_IDS == {2022: 326, 2024: 333, 2026: 348}
    assert 346 not in nd.ELECTION_IDS.values()


# --------------------------------------------------------------------------
# The statewide table
# --------------------------------------------------------------------------
def test_statewide_numbers_are_read_off_the_page(statewide):
    values = nd.parse_state(statewide)
    assert values[nd.STATE_SENT] == STATE_2024["sent"]
    assert values[nd.STATE_RETURNED] == STATE_2024["returned"]
    assert values[nd.STATE_EARLY] == STATE_2024["early"]
    assert values[nd.STATE_CAST] == STATE_2024["cast"]


def test_north_dakotas_own_arithmetic_is_enforced(statewide):
    """Cast == returned + early. If that stops holding, a row changed meaning."""
    assert STATE_2024["returned"] + STATE_2024["early"] == STATE_2024["cast"]
    broken = statewide.replace(">190,563<", ">190,564<")
    assert broken != statewide
    with pytest.raises(SchemaDrift) as excinfo:
        nd.parse_state(broken)
    assert "190564" in str(excinfo.value) or "190,564" in str(excinfo.value)


def test_a_missing_statewide_row_is_drift(statewide):
    broken = statewide.replace("lblEarlyVoting", "lblSomethingElse")
    with pytest.raises(SchemaDrift) as excinfo:
        nd.parse_state(broken)
    assert nd.STATE_EARLY in str(excinfo.value)


def test_every_north_dakota_county_is_on_the_page(statewide):
    from ev.adapters import _fips

    options = nd.counties(statewide)
    assert len(options) == nd.EXPECTED_COUNTIES == 53
    resolved = {_fips.lookup("ND", name) for _, name in options}
    assert None not in resolved
    assert len(resolved) == 53


def test_the_page_carries_the_webforms_tokens(statewide):
    assert nd._hidden(statewide, "__VIEWSTATE")
    assert nd._hidden(statewide, "__EVENTVALIDATION")


# --------------------------------------------------------------------------
# The county panel
# --------------------------------------------------------------------------
def test_a_county_that_runs_early_voting(statewide):
    sent, returned, early = nd.parse_county(_text("abev_2024_general_cass.html"), "Cass")
    assert (sent, returned, early) == (12358, 11705, 38990)


def test_a_county_that_does_not_run_early_voting_reports_nothing():
    """Adams prints no early-vote block AT ALL -- not a zero, an omission."""
    markup = _text("abev_2024_general_adams.html")
    assert "lblEarlyVotes" not in markup
    sent, returned, early = nd.parse_county(markup, "Adams")
    assert (sent, returned) == (614, 583)
    assert early is None


def test_a_panel_for_the_wrong_county_is_drift():
    """The postback is stateless; a mismatch would file numbers under bad FIPS."""
    with pytest.raises(SchemaDrift) as excinfo:
        nd.parse_county(_text("abev_2024_general_cass.html"), "Adams")
    assert "Cass" in str(excinfo.value)


def test_a_response_with_no_panel_is_a_source_error():
    with pytest.raises(SourceError):
        nd.parse_county("<html><body>error page</body></html>", "Cass")


# --------------------------------------------------------------------------
# What an absent early-vote block means
# --------------------------------------------------------------------------
def test_silence_becomes_zero_only_when_the_state_total_proves_it():
    """Seven counties reporting 99,007 against a statewide 99,007: the other
    forty-six really did cast no early ballots."""
    reported = {"38017": 38990, "38015": 19066, "38035": 12416, "38001": None}
    settled = nd._settle_early(reported, 38990 + 19066 + 12416)
    assert settled["38001"] == 0


def test_silence_stays_blank_when_the_arithmetic_does_not_close():
    """If the reporting counties fall short, something is unknown, not zero."""
    reported = {"38017": 38990, "38001": None}
    settled = nd._settle_early(reported, 99007)
    assert settled["38001"] is None


def test_a_county_with_no_early_total_publishes_no_total():
    """Half a total is not a total."""
    statewide = {
        nd.STATE_SENT: 100, nd.STATE_RETURNED: 50,
        nd.STATE_EARLY: 99007, nd.STATE_CAST: 50 + 99007,
    }
    rows = {"38001": ("Adams County", 614, 583, None)}
    result = nd.build(statewide, rows, 2024, date(2024, 11, 5))
    county = result.county_rows[0]
    assert county.inperson is None
    assert county.ballots_total is None
    assert county.mail_returned == 583


# --------------------------------------------------------------------------
# The rows that come out
# --------------------------------------------------------------------------
@pytest.fixture
def built():
    statewide = {
        nd.STATE_SENT: STATE_2024["sent"],
        nd.STATE_RETURNED: STATE_2024["returned"],
        nd.STATE_EARLY: STATE_2024["early"],
        nd.STATE_CAST: STATE_2024["cast"],
    }
    rows = {
        "38017": ("Cass County", 12358, 11705, 38990),
        "38001": ("Adams County", 614, 583, None),
    }
    # Only these two counties, so the early-vote arithmetic deliberately does
    # NOT close and Adams stays blank -- see the settle tests above.
    return nd.build(statewide, rows, 2024, date(2024, 11, 5))


def test_the_state_row_is_north_dakotas_own_total(built):
    row = built.state_rows[0]
    assert row.ballots_total == STATE_2024["cast"]
    assert row.mail_requested == STATE_2024["sent"]
    assert row.mail_returned == STATE_2024["returned"]
    assert row.inperson == STATE_2024["early"]
    assert row.ballots_new is None


def test_no_party_field_is_ever_populated(built):
    """North Dakota has no voter registration at all. Never 0."""
    for row in built.state_rows + built.county_rows:
        assert row.party_dem is None
        assert row.party_rep is None
        assert row.party_oth is None
        assert row.party_npa is None


def test_counties_are_keyed_by_fips_not_name(built):
    assert {r.county_fips for r in built.county_rows} == {"38001", "38017"}
    for row in built.county_rows:
        assert len(row.county_fips) == 5 and row.county_fips.startswith("38")


def test_a_reporting_county_totals_returned_plus_early(built):
    cass = next(r for r in built.county_rows if r.county_fips == "38017")
    assert cass.ballots_total == 11705 + 38990 == 50695
    assert cass.mail_returned == 11705
    assert cass.inperson == 38990


# --------------------------------------------------------------------------
# Counts
# --------------------------------------------------------------------------
def test_thousands_separators_and_blanks():
    assert nd._int("12,358") == 12358
    assert nd._int("0") == 0
    assert nd._int(None) is None
    assert nd._int("  ") is None
    with pytest.raises(SchemaDrift):
        nd._int("twelve")


def test_history_refuses_a_cycle_with_no_known_id():
    with pytest.raises(NotYetPublished):
        nd.NDScraper().fetch_history(2018)


def test_the_adapter_is_registered_as_tier_one():
    from ev.schema import TIER_SCRAPER

    scraper = nd.NDScraper()
    assert scraper.state == "ND"
    assert scraper.name == "nd-sos"
    assert scraper.tier == TIER_SCRAPER


def test_the_page_carries_no_as_of_date(statewide):
    """There is no date anywhere in the HTML, which is why rows use the run's.

    If North Dakota ever adds one this fails, and the adapter should start
    reading it instead of stamping its own.
    """
    assert not re.search(r"as of|last updat|refreshed", statewide, re.I)
