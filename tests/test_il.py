"""Illinois: the SBE's Pre-Election Counts export.

Four fixtures, all real and unedited:

* `PreElectionCounts.html` -- the live page, whose dropdown offers BOTH
  "2026 General Election" and "2026 General Primary". Picking the wrong one is
  the single mistake that would publish a confident wrong headline.
* `PreElectionCounts-2026-general-selected.html` -- the same page after the
  ASP.NET postback, carrying the four export links.
* `pre-election-ballot-requests-2026-general.csv` -- today's general-election
  export. Illinois writes a literal 0 for the three columns that have not
  started yet, which is a real zero and must survive as one.
* `pre-election-ballot-requests-2026-primary.csv` -- the March 2026 primary's
  export, fully populated. It is both the trap (dated 3/17/2026) and the only
  real numbers available for testing the six-city-board rollup.

Both CSVs are truncated to nineteen of the 109 authority rows, header verbatim.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from ev.adapters import il
from ev.adapters.base import (AdapterError, NotYetPublished, SchemaDrift,
                              SourceError)

FIXTURES = Path(__file__).parent / "fixtures" / "il"
PAGE = FIXTURES / "PreElectionCounts.html"
POSTED = FIXTURES / "PreElectionCounts-2026-general-selected.html"
GENERAL = FIXTURES / "pre-election-ballot-requests-2026-general.csv"
PRIMARY = FIXTURES / "pre-election-ballot-requests-2026-primary.csv"

TODAY = date(2026, 9, 6)


def _page(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


@pytest.fixture
def general():
    return il.parse(GENERAL.read_bytes(), 2026, TODAY)


@pytest.fixture
def rolled_up(monkeypatch):
    """The March primary's export, read as if it were the general.

    Illinois purges past elections from this page, so its primary file is the
    only export with populated Early / Grace / By-Mail Returned columns -- and
    the six-city-board rollup has to be tested against real numbers, not zeroes.
    Only the expected election date is stubbed; every value is Illinois'.
    """
    monkeypatch.setattr(il, "election_date", lambda cycle: date(2026, 3, 17))
    return il.parse(PRIMARY.read_bytes(), 2026, date(2026, 3, 18))


# --------------------------------------------------------------------------
# THE BLANK RULE: Illinois has no party registration
# --------------------------------------------------------------------------
def test_party_is_blank_everywhere_because_illinois_has_no_party_registration(
        general, rolled_up):
    """Illinois voters pick a party's ballot at the primary; nothing is carried
    on the registration. A 0 here would render as "no Democrat has voted"."""
    for result in (general, rolled_up):
        rows = result.state_rows + result.county_rows
        assert rows
        for row in rows:
            assert row.party_dem is None, row
            assert row.party_rep is None, row
            assert row.party_npa is None, row
            assert row.party_oth is None, row


def test_a_reported_zero_stays_zero(general):
    """On 2026-09-06 Illinois had 947,926 mail applications on file and had sent
    nothing out yet, so it wrote 0 in the other three columns. Zero ballots
    returned is a fact about the state, not a gap in the file."""
    (state,) = general.state_rows
    assert state.mail_requested == 947926
    assert state.mail_returned == 0
    assert state.inperson == 0
    assert state.ballots_total == 0


def test_an_empty_cell_would_be_blank_not_zero():
    assert il._count("") is None
    assert il._count("0") == 0
    assert il._add(None, None) is None
    assert il._add(None, 5) == 5


# --------------------------------------------------------------------------
# 108 election authorities rolled into 102 county FIPS
# --------------------------------------------------------------------------
def test_the_six_city_boards_are_added_to_their_county(rolled_up):
    """Chicago is not Cook County's row -- it is the OTHER half of Cook County.
    Reading only "Cook County" would report 74,298 mail ballots returned in the
    largest county in the state instead of 196,229."""
    by_fips = {r.county_fips: r for r in rolled_up.county_rows}
    cook = by_fips["17031"]
    assert cook.county_name == "Cook County"
    assert cook.mail_returned == 74298 + 121931
    assert cook.inperson == (120879 + 2152) + (118602 + 0)
    assert cook.ballots_total == cook.mail_returned + cook.inperson

    assert by_fips["17113"].mail_returned == 2233 + 2830    # McLean + Bloomington
    assert by_fips["17183"].mail_returned == 535 + 524      # Vermilion + Danville
    assert by_fips["17163"].mail_returned == 7085 + 1956    # St. Clair + E St Louis
    assert by_fips["17095"].mail_returned == 520 + 816      # Knox + Galesburg
    assert by_fips["17201"].mail_returned == 3945 + 4275    # Winnebago + Rockford


def test_a_county_without_a_city_board_is_left_alone(rolled_up):
    dupage = {r.county_fips: r for r in rolled_up.county_rows}["17043"]
    assert dupage.mail_returned == 119938
    assert dupage.inperson == 41081 + 0
    assert dupage.ballots_total == 119938 + 41081


def test_every_city_board_maps_to_a_real_county():
    from ev.adapters import _fips
    assert len(il.CITY_BOARDS) == 6
    for board, county in il.CITY_BOARDS.items():
        assert _fips.lookup("IL", county) is not None, board


def test_grace_period_voting_is_counted_as_in_person(rolled_up):
    """Grace-period voting is registering and voting in one in-person
    transaction, counted separately from Early rather than inside it."""
    (state,) = rolled_up.state_rows
    assert state.inperson == 430000 + 4550
    assert state.ballots_total == 502064 + 430000 + 4550


# --------------------------------------------------------------------------
# County rows are keyed by FIPS, never by authority name
# --------------------------------------------------------------------------
def test_county_rows_are_fips_keyed(general):
    names = {r.county_fips: r.county_name for r in general.county_rows}
    assert names["17039"] == "De Witt County", "the file writes 'DeWitt County'"
    assert names["17085"] == "Jo Daviess County", "the file writes 'JoDaviess County'"
    assert names["17099"] == "LaSalle County"
    assert names["17163"] == "St. Clair County"
    assert names["17031"] == "Cook County"
    for row in general.county_rows:
        assert len(row.county_fips) == 5 and row.county_fips.startswith("17")


def test_one_row_per_county_even_where_two_authorities_report(general):
    fips = [r.county_fips for r in general.county_rows]
    assert len(fips) == len(set(fips)), "the six city boards must not add rows"
    assert len(fips) == 12, "twelve counties across eighteen authority rows"


def test_statewide_row_is_illinois_own_not_our_sum(general):
    """The fixture keeps eighteen of 108 authorities, so a parser that summed
    them would report 727,724 rather than Illinois' 947,926."""
    (state,) = general.state_rows
    assert state.state == "IL" and state.day == TODAY and state.cycle == 2026
    assert state.mail_requested == 947926
    assert "STATEWIDE COUNTS" not in {r.county_name for r in general.county_rows}


# --------------------------------------------------------------------------
# The general election, never the primary -- checked twice
# --------------------------------------------------------------------------
def test_the_dropdown_option_is_the_general_not_the_primary():
    assert il.election_option(_page(PAGE), 2026) == "78"


def test_a_cycle_the_dropdown_does_not_offer_is_not_yet_published():
    """Illinois purges past elections from this page, so a backfill run and a
    run before the cycle is posted look the same, and both must STOP the ladder
    rather than let a weaker source invent a zero."""
    for cycle in (2022, 2024):
        with pytest.raises(NotYetPublished, match="does not offer"):
            il.election_option(_page(PAGE), cycle)


def test_a_page_without_the_dropdown_is_a_source_error():
    with pytest.raises(SourceError, match="no election dropdown"):
        il.election_option("<html><body>maintenance</body></html>", 2026)


def test_the_primary_export_is_refused_for_the_general():
    """Same page, same button, fully populated file -- and 856,815 mail requests
    from March would be a completely plausible November headline."""
    with pytest.raises(SchemaDrift, match="dated 2026-03-17"):
        il.parse(PRIMARY.read_bytes(), 2026, TODAY)


# --------------------------------------------------------------------------
# The ASP.NET plumbing
# --------------------------------------------------------------------------
def test_hidden_postback_fields_are_read_from_the_live_page():
    fields = il.hidden_fields(_page(PAGE))
    assert sorted(fields) == ["__EVENTVALIDATION", "__VIEWSTATE", "__VIEWSTATEGENERATOR"]
    assert all(v for v in fields.values())


def test_a_page_missing_a_postback_token_is_a_source_error():
    with pytest.raises(SourceError, match="__VIEWSTATE"):
        il.hidden_fields("<html><form></form></html>")


def test_the_csv_link_is_picked_out_of_the_four_offered():
    url = il.download_url(_page(POSTED))
    assert url.startswith("https://elections.il.gov/NewDocDisplay.aspx?")
    assert "&amp;" not in url, "HTML entities must be unescaped before the request"


def test_no_download_link_yet_is_not_yet_published():
    with pytest.raises(NotYetPublished, match="no 'ascii comma delimited'"):
        il.download_url(_page(PAGE))


# --------------------------------------------------------------------------
# SchemaDrift rather than a guessed mapping
# --------------------------------------------------------------------------
def test_a_renamed_column_raises_drift():
    body = GENERAL.read_bytes().replace(b"By-Mail Returned", b"Mail Received", 1)
    with pytest.raises(SchemaDrift, match="header is"):
        il.parse(body, 2026, TODAY)


def test_an_unknown_election_authority_raises_drift():
    body = GENERAL.read_bytes().replace(b"City of Galesburg", b"City of Atlantis", 1)
    with pytest.raises(SchemaDrift, match="unrecognised election authorities"):
        il.parse(body, 2026, TODAY)


def test_a_non_numeric_count_raises_drift():
    body = GENERAL.read_bytes().replace(b"11/3/2026 0:00,3952", b"11/3/2026 0:00,lots", 1)
    with pytest.raises(SchemaDrift, match="not a count"):
        il.parse(body, 2026, TODAY)


def test_a_file_with_no_statewide_row_raises_drift():
    body = GENERAL.read_bytes().replace(b"STATEWIDE COUNTS", b"Adams County", 1)
    with pytest.raises(SchemaDrift, match="no STATEWIDE COUNTS row"):
        il.parse(body, 2026, TODAY)


def test_a_header_only_export_is_not_yet_published():
    """A well-formed header with nothing under it is Illinois not having run the
    counts yet. That STOPS the ladder; it is not a parse failure."""
    body = GENERAL.read_bytes().split(b"\r\n")[0] + b"\r\n"
    with pytest.raises(NotYetPublished, match="has no rows yet"):
        il.parse(body, 2026, TODAY)


# --------------------------------------------------------------------------
# End to end, over a stubbed session
# --------------------------------------------------------------------------
class _Response:
    def __init__(self, content: bytes, status: int = 200) -> None:
        self.content = content
        self.status_code = status
        self.ok = 200 <= status < 400

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")


class _Session:
    def __init__(self, *, export: bytes, status: int = 200) -> None:
        self.export = export
        self.status = status
        self.posted: list[dict] = []

    def get(self, url, timeout=None):
        if url == il.INDEX:
            return _Response(PAGE.read_bytes())
        return _Response(self.export, self.status)

    def post(self, url, data=None, timeout=None):
        self.posted.append(data)
        return _Response(POSTED.read_bytes())


def test_a_live_run_selects_the_general_and_parses_its_export(monkeypatch):
    session = _Session(export=GENERAL.read_bytes())
    monkeypatch.setattr(il.ILScraper, "_session", lambda self: session)
    result = il.ILScraper().fetch(2026, TODAY)
    assert session.posted[0][il.SELECT_NAME] == "78", "the general, not the primary"
    assert result.state_rows[0].mail_requested == 947926
    assert len(result.county_rows) == 12


def test_an_html_error_page_instead_of_the_export_falls_through(monkeypatch):
    """An expired postback token gets the page back with status 200. That is a
    fetch we expected to work and did not, so it falls through a tier."""
    session = _Session(export=b"<!DOCTYPE html><html><body>expired</body></html>")
    monkeypatch.setattr(il.ILScraper, "_session", lambda self: session)
    with pytest.raises(SourceError, match="came back as HTML"):
        il.ILScraper().fetch(2026, TODAY)


def test_a_500_on_the_export_falls_through(monkeypatch):
    session = _Session(export=b"", status=500)
    monkeypatch.setattr(il.ILScraper, "_session", lambda self: session)
    with pytest.raises(SourceError, match="HTTP 500"):
        il.ILScraper().fetch(2026, TODAY)


def test_there_is_no_archive_to_backfill():
    """Illinois has no recoverable 2024 or 2022 county series, and this says why.

    Florida's page has the same "snapshot, overwritten in place" shape and its
    two past cycles came straight out of the Wayback Machine. Illinois' does not,
    because the counts live behind an ASP.NET postback: every archived capture of
    the page that was fetched — five spread across November 2024, plus the single
    2022 one — carries the dropdown unselected, with no tables and no export
    links. The
    refusal is a finding, so the message has to carry it rather than inherit the
    base class's generic shrug.
    """
    with pytest.raises(NotYetPublished) as caught:
        il.ILScraper().fetch_history(2024)
    message = str(caught.value)
    assert "pre-election counts" in message
    assert "Wayback" in message
    assert il.INDEX in message


def test_the_history_refusal_covers_2022_too():
    with pytest.raises(NotYetPublished, match="2022"):
        il.ILScraper().fetch_history(2022)


def test_the_refusal_records_the_widened_sweep_not_just_the_scoped_one():
    """⚠️ WHY THIS TEST EXISTS. On the same day this was last checked, Delaware's
    identical-looking "nothing archived" verdict was OVERTURNED -- it had been
    reached by listing one directory, and the 2022 report was in a different tree
    under a different name. A scoped sweep can only prove something about its
    scope, so Illinois' refusal has to rest on a DOMAIN-wide one, and the
    docstring has to say so or the next reader has no way to tell which kind was
    run.

    7,124 distinct urlkeys for the 2024 window and 355 for 2022, across
    elections.il.gov and its four subdomains; the only thing under any `Counts/`
    path is the form, the registration totals, and an archived 404.
    """
    assert "matchType=domain" in il.__doc__
    assert "7,124 distinct urlkeys" in il.__doc__
    assert "355 distinct" in il.__doc__
    assert "prefix sweep can only prove something about the prefix" in il.__doc__


def test_the_history_refusal_is_a_stop_not_a_fallthrough():
    """NotYetPublished STOPS the ladder. A SourceError here would let a weaker
    tier invent a statewide-only Illinois row and call it county data."""
    assert issubclass(NotYetPublished, AdapterError)
    assert not issubclass(NotYetPublished, SourceError)


def test_adapter_identity():
    scraper = il.ILScraper()
    assert (scraper.state, scraper.name, scraper.tier) == ("IL", "il-sbe", 1)
