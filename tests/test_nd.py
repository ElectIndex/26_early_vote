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
* `2024_panels/<value>.html` -- ALL FIFTY-THREE county panels for the 2024
  general, each TRUNCATED to the verbatim `progressDiv` block that carries the
  four spans the parser reads. Every one was checked to parse identically to the
  29 KB page it came out of. They are here so the whole harvest -- and its one
  real judgement, what an absent early-vote block means -- can be tested end to
  end against North Dakota's own published totals without 53 live postbacks.
* `candidatelist_2026_general.html` / `candidatelist_2026_primary.html` -- the
  election-name headers for `eid=348` and `eid=346`, TRUNCATED (the live pages
  are 115 KB and 312 KB of candidate table) to the real `<head>` plus the
  verbatim header element. The primary's is here because it is two ids away from
  the general's and is what the guard exists to refuse.

Four more are Wayback Machine captures of the bare `abev.aspx?eid=<id>` GET,
each WHOLE and verbatim (`web/<stamp>id_/`, the SoS's original bytes), named
`archive_<eid>_<stamp>.html`. They are the archived curve's fixtures and each
one is here for a specific trap:

* `archive_326_20221004011209.html` -- 2022, 01:12 UTC on Oct 4 = 20:12 CDT on
  **Oct 3** (the Central-date rule), early voting at a REAL 0 (not open yet),
  and the OLD markup vintage that bolds each value with an inner `<b>` instead
  of a style on the span -- which the live parser used to read as empty.
* `archive_333_20241007230303.html` -- 2024, the live markup vintage, October,
  early voting 0.
* `archive_333_20241103073731.html` -- 07:37 UTC on Nov 3 = 01:37 CST, filed
  under Nov 3 by the calendar-date rule, documented in nd.py.
* `archive_333_20241106050439.html` -- 05:04 UTC on Nov 6 = 23:04 CST on
  Election Day, the LAST of that day's captures; the day's earlier reading
  (00:45 UTC, 189,493) is not kept as a file because the test only needs its
  numbers, and the live final supersedes both on Election Day anyway.

The county-name -> FIPS join and the statewide arithmetic are checked against
North Dakota's own published totals, which the harvested counties reproduce
exactly.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import json
import pytest

from ev.adapters import nd
from ev.adapters.base import FetchResult, NotYetPublished, SchemaDrift, SourceError

FIXTURES = Path(__file__).parent / "fixtures" / "nd"

#: North Dakota's own statewide figures for the 2024 general, as printed on the
#: page. The 53 harvested counties sum to the first three exactly.
STATE_2024 = {"sent": 95908, "returned": 91556, "early": 99007, "cast": 190563}


def _text(name: str) -> str:
    return (FIXTURES / name).read_text()


@pytest.fixture(autouse=True)
def no_throttle(monkeypatch):
    """Every Archive call here is stubbed; the courtesy interval between them
    is real and would make this file sleep several seconds for nothing."""
    monkeypatch.setattr(nd, "_throttle", lambda url, interval: None)


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


# --------------------------------------------------------------------------
# The whole 2024 harvest, end to end, against North Dakota's own totals
# --------------------------------------------------------------------------
PANELS = FIXTURES / "2024_panels"

#: The seven counties that ran early voting in the 2024 general, and what each
#: of them reported. The other 46 printed no early-vote block at all. Read off
#: the panels; they sum to the state's own 99,007 exactly, which is the proof
#: `_settle_early` re-runs on every fetch before it publishes a real 0.
EARLY_2024 = {
    "38015": 19_066,   # Burleigh
    "38017": 38_990,   # Cass
    "38035": 12_416,   # Grand Forks
    "38059": 6_490,    # Morton
    "38089": 6_420,    # Stark
    "38093": 3_478,    # Stutsman
    "38101": 12_147,   # Ward
}


#: The 2024 captures the Archive holds inside the window, as `archive_captures`
#: would list them, and the fixture (or the fixture whose numbers stand in)
#: each one serves. 20241106004518 and 20241106050439 are BOTH Election Day in
#: Bismarck; only the later one is on disk, and the earlier is served the same
#: bytes here so the "last capture of a day wins" rule has two to choose from.
CAPTURES_2024 = [
    ("20241007230303", "https://vip.sos.nd.gov/abev.aspx?eid=333"),
    ("20241008175928", "https://vip.sos.nd.gov/abev.aspx?eid=333"),
    ("20241103073731", "https://vip.sos.nd.gov/abev.aspx?eid=333"),
    ("20241106004518", "https://vip.sos.nd.gov/abev.aspx?eid=333"),
    ("20241106050439", "https://vip.sos.nd.gov/abev.aspx?eid=333"),
]
ARCHIVE_FILES = {
    "20221004011209": "archive_326_20221004011209.html",
    "20241007230303": "archive_333_20241007230303.html",
    "20241103073731": "archive_333_20241103073731.html",
    "20241106050439": "archive_333_20241106050439.html",
}


def _archived(stamp: str) -> str:
    """The fixture for a stamp; 2024-10-08 and the Nov 5 00:45 capture are
    stand-ins, rewritten to the numbers the real capture carries."""
    if stamp == "20241008175928":
        return (_text("archive_333_20241007230303.html")
                .replace(">61,943<", ">63,554<").replace(">9,395<", ">10,541<"))
    if stamp == "20241106004518":
        return (_text("archive_333_20241106050439.html")
                .replace(">90,814<", ">90,809<").replace(">189,498<", ">189,493<"))
    return _text(ARCHIVE_FILES[stamp])


def _stub_archive(monkeypatch, captures=None):
    """Drive the Wayback walk from the fixtures: every capture answers with
    its own Memento stamp, as the real Archive does for a 200."""
    monkeypatch.setattr(nd, "archive_captures",
                        lambda cycle: list(CAPTURES_2024 if captures is None else captures))
    monkeypatch.setattr(nd, "archived_page",
                        lambda stamp, original, *, eid: (_archived(stamp), stamp))


@pytest.fixture
def harvested(monkeypatch):
    """`fetch_history(2024)` driven entirely by the saved panels and captures."""
    def header(self, eid, *, use_cache):
        return "2024 General Election Contest/Candidate List"

    def page(self, eid, *, use_cache):
        assert eid == 333
        return _text("abev_2024_general.html")

    def county(self, eid, markup, value, *, use_cache=False):
        return (PANELS / f"{value}.html").read_text()

    monkeypatch.setattr(nd.NDScraper, "_header", header)
    monkeypatch.setattr(nd.NDScraper, "_page", page)
    monkeypatch.setattr(nd.NDScraper, "_county", county)
    _stub_archive(monkeypatch)
    return nd.NDScraper().fetch_history(2024)


def _final(result):
    return next(r for r in result.state_rows if r.day == date(2024, 11, 5))


def test_the_harvest_reproduces_north_dakotas_own_totals(harvested):
    """⚠️ CANONICAL VALUES, not invariants. The county rows are not an
    approximation of the state row; they ARE the state row, to the ballot."""
    assert len(harvested.county_rows) == nd.EXPECTED_COUNTIES == 53
    state = _final(harvested)
    assert (state.mail_requested, state.mail_returned, state.inperson,
            state.ballots_total) == (95_908, 91_556, 99_007, 190_563)
    assert sum(r.mail_returned for r in harvested.county_rows) == 91_556
    assert sum(r.inperson for r in harvested.county_rows) == 99_007
    assert sum(r.ballots_total for r in harvested.county_rows) == 190_563


def test_the_final_is_dated_election_day_not_the_run(harvested):
    """This path serves a settled election, so the final carries its own date
    -- and the county rows, which the Archive cannot supply, carry only it."""
    assert _final(harvested).day == date(2024, 11, 5)
    assert {row.day for row in harvested.county_rows} == {date(2024, 11, 5)}


def test_exactly_seven_counties_ran_early_voting_and_the_rest_are_real_zeros(
    harvested,
):
    """The whole point of `_settle_early`. Forty-six counties printed no block;
    the seven that did add up to the state's own figure, so the silence is
    proven to be zero and is published as one rather than as a blank."""
    early = {r.county_fips: r.inperson for r in harvested.county_rows}
    assert sum(1 for v in early.values() if v) == 7
    assert sum(1 for v in early.values() if v == 0) == 46
    assert None not in early.values()
    assert {f: v for f, v in early.items() if v} == EARLY_2024
    assert sum(EARLY_2024.values()) == 99_007 == _final(harvested).inperson


def test_named_counties_carry_their_own_numbers(harvested):
    rows = {r.county_fips: r for r in harvested.county_rows}
    cass = rows["38017"]
    assert (cass.county_name, cass.mail_returned, cass.inperson) == (
        "Cass County", 11_705, 38_990)
    assert cass.ballots_total == 50_695
    adams = rows["38001"]
    assert (adams.county_name, adams.mail_returned, adams.inperson) == (
        "Adams County", 583, 0)
    assert adams.ballots_total == 583


def test_the_harvest_registers_nobody_by_party(harvested):
    for row in harvested.state_rows + harvested.county_rows:
        assert (row.party_dem, row.party_rep, row.party_oth, row.party_npa) == (
            None,) * 4


def test_a_single_missing_postback_aborts_the_whole_harvest(monkeypatch):
    """Deliberate, and it must stay that way: 52 of 53 counties published as if
    complete is a statewide picture with a county silently missing from it."""
    def header(self, eid, *, use_cache):
        return "2024 General Election Contest/Candidate List"

    def page(self, eid, *, use_cache):
        return _text("abev_2024_general.html")

    def county(self, eid, markup, value, *, use_cache=False):
        if value == "09":
            raise SourceError("ND: county postback returned HTTP 500")
        return (PANELS / f"{value}.html").read_text()

    monkeypatch.setattr(nd.NDScraper, "_header", header)
    monkeypatch.setattr(nd.NDScraper, "_page", page)
    monkeypatch.setattr(nd.NDScraper, "_county", county)
    with pytest.raises(SourceError):
        nd.NDScraper().fetch_history(2024)


# --------------------------------------------------------------------------
# GUARD PARITY: this path dates every row Election Day, so the election must
# already have happened
# --------------------------------------------------------------------------
def test_history_refuses_a_cycle_that_has_not_happened_yet():
    """⚠️ `fetch` stamps the RUN's date, which cannot be in the future; this
    path stamps Election Day, which for the running cycle can be. `ELECTION_IDS`
    carries 2026 and the portal answers for it, so `backfill --cycle 2026` used
    to harvest today's position -- a state row of 0 ballots -- and date it
    2026-11-03, landing a fabricated number at days_to_election 0 where every
    comparison reads it as that cycle's final."""
    with pytest.raises(NotYetPublished) as excinfo:
        nd.NDScraper().fetch_history(2026)
    assert "2026-11-03" in str(excinfo.value)


def test_the_guard_is_about_the_election_date_not_the_id(monkeypatch):
    """A cycle whose Election Day HAS passed still backfills -- the guard must
    not become "refuse anything recent"."""
    seen: list[tuple[int, date]] = []

    def harvest(self, cycle, day, *, use_cache):
        seen.append((cycle, day))
        return FetchResult()

    monkeypatch.setattr(nd.NDScraper, "_harvest", harvest)
    monkeypatch.setattr(nd.NDScraper, "_archived_curve", lambda self, cycle: {})
    nd.NDScraper().fetch_history(2022)
    assert seen == [(2022, date(2022, 11, 8))]


# --------------------------------------------------------------------------
# THE ARCHIVED CURVE: Wayback captures of the bare GET, statewide only
# --------------------------------------------------------------------------
#: What each archived capture says, read by hand off the captures and pinned
#: here as canonical values: (sent, returned, early, cast prior).
ARCHIVE_2022_10_04 = (46_139, 659, 0, 659)
ARCHIVE_2024_10_07 = (61_943, 9_395, 0, 9_395)
ARCHIVE_2024_11_03 = (93_360, 80_858, 91_750, 172_608)
ARCHIVE_2024_11_06 = (95_907, 90_814, 98_684, 189_498)


def _values(markup):
    v = nd.parse_state(markup)
    return (v[nd.STATE_SENT], v[nd.STATE_RETURNED], v[nd.STATE_EARLY], v[nd.STATE_CAST])


def test_the_archive_ids_are_pinned_and_agree_with_the_live_table():
    """Two cycles, read by hand; 2026 is deliberately NOT here -- the running
    cycle has no finished curve to walk, and the live `fetch` tracks it."""
    assert nd.ARCHIVE_ELECTION_IDS == {2022: 326, 2024: 333}
    for cycle, eid in nd.ARCHIVE_ELECTION_IDS.items():
        assert nd.ELECTION_IDS[cycle] == eid
    assert 2026 not in nd.ARCHIVE_ELECTION_IDS


def test_an_unpinned_cycle_is_refused_by_the_archive(monkeypatch):
    """A cycle nobody has read the captures for gets no guess at an id."""
    with pytest.raises(NotYetPublished, match="no archived election id"):
        nd.archive_captures(2020)


def test_the_old_markup_vintage_with_an_inner_bold_is_read():
    """⚠️ THE MARKUP-VINTAGE TRAP. `<span id=…lblEarlyVoting><b>0</b></span>`
    where the live page has `style="font-weight:bold;"` on the span; the old
    `[^<]*` reader saw every value as empty and `parse_state` called the whole
    table missing."""
    markup = _text("archive_326_20221004011209.html")
    assert "<b>46,139</b>" in markup
    assert _values(markup) == ARCHIVE_2022_10_04


def test_the_live_markup_vintage_is_read_by_the_same_parser():
    markup = _text("archive_333_20241007230303.html")
    assert 'style="font-weight:bold;">61,943<' in markup
    assert _values(markup) == ARCHIVE_2024_10_07
    assert _values(_text("archive_333_20241103073731.html")) == ARCHIVE_2024_11_03
    assert _values(_text("archive_333_20241106050439.html")) == ARCHIVE_2024_11_06


def test_a_zero_early_vote_in_october_is_a_real_zero_and_is_published():
    """In-person early voting had not opened; 0 is what the state printed,
    exactly as the live page would be read. THE BLANK RULE cuts both ways."""
    row = nd.state_row(nd.parse_state(_text("archive_333_20241007230303.html")),
                       2024, date(2024, 10, 7))
    assert row.inperson == 0
    assert row.mail_returned == 9_395
    assert row.ballots_total == 9_395 == row.mail_returned + row.inperson
    assert row.mail_requested == 61_943
    assert (row.party_dem, row.party_rep, row.party_oth, row.party_npa) == (None,) * 4


def test_a_wrapper_the_parser_has_not_seen_is_drift_not_a_blank():
    """Only `<b>` is tolerated. Anything else inside the span fails as drift,
    because a reader that strips every tag would publish whatever it found."""
    markup = _text("archive_326_20221004011209.html").replace(
        "<b>46,139</b>", "<i>46,139</i>")
    with pytest.raises(SchemaDrift):
        nd.parse_state(markup)


def test_capture_days_are_the_central_calendar_date():
    """Stamps are UTC; the books are kept in Bismarck."""
    assert nd.capture_day("20221004011209") == date(2022, 10, 3)   # 20:12 CDT
    assert nd.capture_day("20241007230303") == date(2024, 10, 7)   # 18:03 CDT
    assert nd.capture_day("20241106004518") == date(2024, 11, 5)   # 18:45 CST
    assert nd.capture_day("20241106050439") == date(2024, 11, 5)   # 23:04 CST
    assert nd.capture_day("20241107101457") == date(2024, 11, 7)


def test_the_one_thirty_seven_am_capture_is_filed_under_its_calendar_date():
    """07:37 UTC on Nov 3 is 01:37 CST on Nov 3 -- effectively the end of
    Nov 2, and filed under Nov 3 anyway: the calendar date the state would
    print, not a guess about when the counters last moved. Documented in
    nd.py; if the rule changes, change it there first."""
    assert nd.capture_day("20241103073731") == date(2024, 11, 3)


def test_a_stamp_that_is_not_a_wayback_timestamp_is_refused():
    for bad in ("2024-11-03", "20241103", "2024110307373", "20241399000000"):
        with pytest.raises(SourceError):
            nd.capture_day(bad)


def test_memento_datetime_is_read_as_a_wayback_stamp():
    assert nd.memento_stamp("Wed, 06 Nov 2024 00:45:18 GMT") == "20241106004518"
    assert nd.memento_stamp("Tue, 04 Oct 2022 01:12:09 GMT") == "20221004011209"
    assert nd.memento_stamp(None) is None
    assert nd.memento_stamp("") is None
    assert nd.memento_stamp("not a date") is None


def test_the_archived_curve_is_one_row_per_central_day_last_capture_wins(monkeypatch):
    _stub_archive(monkeypatch)
    curve = nd.NDScraper()._archived_curve(2024)
    assert sorted(curve) == [date(2024, 10, 7), date(2024, 10, 8),
                             date(2024, 11, 3), date(2024, 11, 5)]
    assert curve[date(2024, 11, 3)][nd.STATE_CAST] == 172_608
    # Two captures on Election Day in Bismarck (00:45 and 05:04 UTC Nov 6);
    # the later reading, 189,498, is the day's.
    assert curve[date(2024, 11, 5)][nd.STATE_RETURNED] == 90_814
    assert curve[date(2024, 11, 5)][nd.STATE_CAST] == 189_498


def test_captures_after_election_day_are_dropped(monkeypatch):
    """The post-election figures kept moving for weeks and are not the final;
    the final sits at Election Day from the live eid."""
    _stub_archive(monkeypatch, CAPTURES_2024 + [
        ("20241107101457", "https://vip.sos.nd.gov/abev.aspx?eid=333"),
        ("20241113010851", "https://vip.sos.nd.gov/abev.aspx?eid=333"),
    ])
    monkeypatch.setattr(nd, "archived_page",
                        lambda stamp, original, *, eid: (
                            _text("archive_333_20241106050439.html"), stamp))
    curve = nd.NDScraper()._archived_curve(2024)
    assert max(curve) == date(2024, 11, 5)
    assert date(2024, 11, 7) not in curve


def test_a_capture_the_archive_served_from_a_different_instant_is_skipped(monkeypatch, caplog):
    """⚠️ THE 302 TRAP. `20241104234455` is on the index as an `http://`
    capture the Archive answers with a redirect to `20241106004518`. The body
    parses perfectly -- Nov 5's numbers -- and would land on Nov 4. The reply's
    `Memento-Datetime` is the only thing that says so."""
    captures = [("20241104234455", "http://vip.sos.nd.gov/abev.aspx?eid=333"),
                ("20241106050439", "https://vip.sos.nd.gov/abev.aspx?eid=333")]
    served = {"20241104234455": "20241106004518", "20241106050439": "20241106050439"}
    monkeypatch.setattr(nd, "archive_captures", lambda cycle: captures)
    monkeypatch.setattr(nd, "archived_page",
                        lambda stamp, original, *, eid: (
                            _text("archive_333_20241106050439.html"), served[stamp]))
    with caplog.at_level("WARNING", logger="ev.adapters.nd"):
        curve = nd.NDScraper()._archived_curve(2024)
    assert sorted(curve) == [date(2024, 11, 5)]
    assert date(2024, 11, 4) not in curve
    assert "20241104234455" in caplog.text and "20241106004518" in caplog.text


def test_a_capture_with_no_memento_header_is_skipped_too(monkeypatch):
    """No served instant means no proof of which capture this is."""
    monkeypatch.setattr(nd, "archive_captures", lambda cycle: CAPTURES_2024[:1])
    monkeypatch.setattr(nd, "archived_page",
                        lambda stamp, original, *, eid: (_archived(stamp), None))
    assert nd.NDScraper()._archived_curve(2024) == {}


def test_an_unreadable_capture_raises_rather_than_publishing_blanks(monkeypatch):
    monkeypatch.setattr(nd, "archive_captures", lambda cycle: CAPTURES_2024[:1])
    monkeypatch.setattr(nd, "archived_page",
                        lambda stamp, original, *, eid: (
                            _archived(stamp).replace("lblEarlyVoting", "lblGone"), stamp))
    with pytest.raises(SchemaDrift):
        nd.NDScraper()._archived_curve(2024)


def test_an_all_zero_capture_is_not_a_reading(monkeypatch):
    """The portal's "not loaded yet" rendering, read exactly as `_harvest`
    reads it live: nothing to plot, so no row."""
    zeroed = _text("archive_333_20241007230303.html")
    for literal in (">61,943<", ">9,395<"):
        zeroed = zeroed.replace(literal, ">0<")
    monkeypatch.setattr(nd, "archive_captures", lambda cycle: CAPTURES_2024[:1])
    monkeypatch.setattr(nd, "archived_page",
                        lambda stamp, original, *, eid: (zeroed, stamp))
    assert nd.NDScraper()._archived_curve(2024) == {}


def test_the_history_is_the_final_by_county_plus_the_statewide_curve(harvested):
    """Four state rows: three from the Archive and Election Day from the live
    eid. The Archive's own Election-Day reading (189,498) is superseded by the
    settled 190,563, and NO county row comes from the Archive."""
    days = [r.day for r in harvested.state_rows]
    assert days == sorted(days) == [date(2024, 10, 7), date(2024, 10, 8),
                                    date(2024, 11, 3), date(2024, 11, 5)]
    by_day = {r.day: r for r in harvested.state_rows}
    oct7 = by_day[date(2024, 10, 7)]
    assert (oct7.mail_requested, oct7.mail_returned, oct7.inperson,
            oct7.ballots_total) == ARCHIVE_2024_10_07
    nov3 = by_day[date(2024, 11, 3)]
    assert (nov3.mail_requested, nov3.mail_returned, nov3.inperson,
            nov3.ballots_total) == ARCHIVE_2024_11_03
    assert by_day[date(2024, 11, 5)].ballots_total == 190_563
    assert all(r.ballots_new is None for r in harvested.state_rows)
    assert {row.day for row in harvested.county_rows} == {date(2024, 11, 5)}


def test_the_archived_rows_register_nobody_by_party(harvested):
    for row in harvested.state_rows:
        assert (row.party_dem, row.party_rep, row.party_oth, row.party_npa) == (
            None,) * 4


def test_an_unreachable_archive_fails_the_backfill_loudly(monkeypatch):
    """The Archive has been "Temporarily Offline" for hours at a stretch. That
    is a SourceError for the whole backfill, not a final quietly published
    without its curve and not "nothing archived"."""
    import requests

    def header(self, eid, *, use_cache):
        return "2024 General Election Contest/Candidate List"

    monkeypatch.setattr(nd.NDScraper, "_header", header)
    monkeypatch.setattr(nd.NDScraper, "_page",
                        lambda self, eid, *, use_cache: _text("abev_2024_general.html"))
    monkeypatch.setattr(nd.NDScraper, "_county",
                        lambda self, eid, markup, value, *, use_cache=False: (
                            PANELS / f"{value}.html").read_text())

    def offline(*a, **k):
        raise requests.ConnectionError("web.archive.org: Temporarily Offline")

    monkeypatch.setattr(nd.SESSION, "get", offline)
    with pytest.raises(SourceError, match="CDX index was unreachable"):
        nd.NDScraper().fetch_history(2024)


def test_a_cdx_error_status_is_a_source_error_not_absence(monkeypatch):
    class Response:
        status_code = 503
        text = "Temporarily Offline"

    monkeypatch.setattr(nd.SESSION, "get", lambda *a, **k: Response())
    with pytest.raises(SourceError, match="HTTP 503"):
        nd.archive_captures(2024)


def test_an_empty_cdx_answer_is_absence_not_a_fault(monkeypatch):
    class Response:
        status_code = 200
        text = ""

    monkeypatch.setattr(nd.SESSION, "get", lambda *a, **k: Response())
    assert nd.archive_captures(2024) == []


def test_the_cdx_sweep_keeps_only_this_eids_200s_and_dedupes_within_a_day(monkeypatch):
    """Prefix on `eid=333` would also match `eid=3331`; a 302 row is the trap;
    three captures of the same bytes in one evening are one reading, but the
    same bytes on another day are a second, true, flat reading."""
    rows = [["timestamp", "original", "statuscode", "digest"],
            ["20241106015929", "https://vip.sos.nd.gov/abev.aspx?eid=333", "200", "AAA"],
            ["20241106004518", "https://vip.sos.nd.gov/abev.aspx?eid=333", "200", "AAA"],
            ["20241106011650", "https://vip.sos.nd.gov/abev.aspx?eid=333", "200", "AAA"],
            ["20241104234455", "http://vip.sos.nd.gov/abev.aspx?eid=333", "302", "ZZZ"],
            ["20241103120000", "https://vip.sos.nd.gov/abev.aspx?eid=3331", "200", "BBB"],
            ["20241007230303", "https://vip.sos.nd.gov/abev.aspx?eid=333", "200", "CCC"],
            ["20241008175928", "https://vip.sos.nd.gov/abev.aspx?eid=333&x=1", "200", "CCC"],
            # 00:13 CST on Nov 6: after Election Day, dropped off the index so
            # it is never downloaded (it drew the 429 that stopped a real run).
            ["20241106061333", "https://vip.sos.nd.gov/abev.aspx?eid=333", "200", "DDD"]]
    asked = {}

    class Response:
        status_code = 200
        text = __import__("json").dumps(rows)

    def get(url, params=None, **k):
        asked.update(params or {})
        return Response()

    monkeypatch.setattr(nd.SESSION, "get", get)
    got = nd.archive_captures(2024)
    assert got == [("20241007230303", "https://vip.sos.nd.gov/abev.aspx?eid=333"),
                   ("20241008175928", "https://vip.sos.nd.gov/abev.aspx?eid=333&x=1"),
                   ("20241106004518", "https://vip.sos.nd.gov/abev.aspx?eid=333")]
    assert asked["url"] == "vip.sos.nd.gov/abev.aspx?eid=333"
    assert asked["matchType"] == "prefix"
    assert asked["filter"] == "statuscode:200"
    # The window reaches one UTC day PAST Election Day so Bismarck's
    # election evening (stamped Nov 6 in UTC) is inside it.
    assert (asked["from"], asked["to"]) == ("20240906", "20241106")


def test_archived_page_refuses_a_non_page_and_checks_the_served_instant(monkeypatch, tmp_path):
    monkeypatch.setattr(nd, "cache_path", lambda state, name: tmp_path / name)

    class Response:
        status_code = 200
        content = b"<html>Temporarily Offline</html>"
        headers = {}

    monkeypatch.setattr(nd.SESSION, "get", lambda *a, **k: Response())
    with pytest.raises(SourceError, match="did not return the page"):
        nd.archived_page("20241007230303", "https://vip.sos.nd.gov/abev.aspx?eid=333", eid=333)

    class Good:
        status_code = 200
        content = _text("archive_333_20241007230303.html").encode()
        headers = {"memento-datetime": "Mon, 07 Oct 2024 23:03:03 GMT"}

    monkeypatch.setattr(nd.SESSION, "get", lambda *a, **k: Good())
    markup, served = nd.archived_page(
        "20241007230303", "https://vip.sos.nd.gov/abev.aspx?eid=333", eid=333)
    assert served == "20241007230303"
    assert _values(markup) == ARCHIVE_2024_10_07
    # ...and the second read comes from disk, sidecar and all, with no request.
    monkeypatch.setattr(nd.SESSION, "get",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("fetched")))
    again, served_again = nd.archived_page(
        "20241007230303", "https://vip.sos.nd.gov/abev.aspx?eid=333", eid=333)
    assert (again, served_again) == (markup, served)


def test_an_archive_http_error_on_a_capture_raises_not_skips(monkeypatch, tmp_path):
    monkeypatch.setattr(nd, "cache_path", lambda state, name: tmp_path / name)

    class Response:
        status_code = 503
        content = b""
        headers = {}

    monkeypatch.setattr(nd.SESSION, "get", lambda *a, **k: Response())
    with pytest.raises(SourceError, match="HTTP 503"):
        nd.archived_page("20241007230303", "https://vip.sos.nd.gov/abev.aspx?eid=333", eid=333)


def test_a_cdx_row_with_a_malformed_stamp_is_skipped_not_written_under(monkeypatch):
    """The stamp is interpolated into cache/nd/archive_<eid>_<stamp>.html."""
    class Response:
        status_code = 200
        text = json.dumps([
            ["timestamp", "original", "statuscode", "digest"],
            ["../../evil", f"{nd.BASE}?eid=333", "200", "D1"],
            ["20241007230303", f"{nd.BASE}?eid=333", "200", "D2"],
        ])
    monkeypatch.setattr(nd, "_throttle", lambda *a, **k: None)
    monkeypatch.setattr(nd.SESSION, "get", lambda *a, **k: Response())
    captures = nd.archive_captures(2024)
    assert [c[0] for c in captures] == ["20241007230303"]
