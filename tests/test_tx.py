"""Texas: the SoS cumulative early-voting turnout report.

Two fixtures, and the difference between them is the whole point of this
adapter. The 2022 one is the real table for 2022-10-24 with all 254 counties and
the SoS's TOTAL row (only the per-line indentation is stripped; every cell is
verbatim). The 2024 one is the same report truncated to its first 30 counties
plus the TOTAL -- the shape the SoS used when it published only the largest
counties -- so the TOTAL row it carries is a partial total that must never reach
the statewide table.

The 2024 layout also has a ninth column the 2022 one does not, which is why
columns are matched by header name.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from ev.adapters import tx
from ev.adapters._net import Missing
from ev.adapters.base import FetchResult, NotYetPublished, SchemaDrift
from ev.schema import Provenance, TIER_SCRAPER, county_row_to_dict, state_row_to_dict

FIXTURES = Path(__file__).parent / "fixtures" / "tx"
FULL = FIXTURES / "2022gen_ev_2022-10-24_table.html"
PARTIAL = FIXTURES / "2024gen_ev_2024-10-21_partial_table.html"


@pytest.fixture(scope="module")
def gen2022():
    return tx.parse(FULL.read_text(encoding="utf-8"), 2022)


@pytest.fixture(scope="module")
def partial2024():
    return tx.parse(PARTIAL.read_text(encoding="utf-8"), 2024)


# --------------------------------------------------------------------------
# THE BLANK RULE
# --------------------------------------------------------------------------
def test_no_party_field_is_ever_populated(gen2022, partial2024):
    """Texas has no party registration -- its primaries are open. A 0 here
    would render as 'zero Democrats have voted'."""
    for result in (gen2022, partial2024):
        for row in result.state_rows + result.county_rows:
            for field in ("party_dem", "party_rep", "party_oth", "party_npa"):
                assert getattr(row, field) is None, (field, row)


def test_party_fields_write_as_blank_cells_not_zeros(gen2022):
    prov = Provenance(tier=TIER_SCRAPER, name="tx-sos")
    state = state_row_to_dict(FetchResult(state_rows=[gen2022.state_rows[0]])
                              .stamp(prov).state_rows[0])
    county = county_row_to_dict(FetchResult(county_rows=[gen2022.county_rows[0]])
                                .stamp(prov).county_rows[0])
    for cells in (state, county):
        for field in ("party_dem", "party_rep", "party_oth", "party_npa"):
            assert cells[field] == "", (field, cells)


def test_unreported_measures_stay_blank(gen2022):
    """The report never says how many mail ballots were sent out, and its daily
    column counts in-person ballots only -- so neither field is invented."""
    (state,) = gen2022.state_rows
    assert state.mail_requested is None
    assert state.ballots_new is None
    assert all(r.ballots_new is None for r in gen2022.county_rows)


# --------------------------------------------------------------------------
# PARTIAL COVERAGE -- the main correctness property for Texas
# --------------------------------------------------------------------------
def test_a_partial_report_publishes_no_statewide_row(partial2024):
    """The TOTAL row sums only the counties printed above it. Over 30 of 254
    counties that is not a Texas total, and there is no way to say so in
    StateDay, so nothing is published there at all."""
    assert partial2024.state_rows == []
    assert len(partial2024.county_rows) == 30


def test_a_partial_report_still_publishes_its_counties(partial2024):
    """The counties that did report are real data and are kept."""
    by_fips = {r.county_fips: r for r in partial2024.county_rows}
    harris = by_fips["48201"]
    assert harris.county_name == "Harris County"
    assert (harris.ballots_total, harris.inperson, harris.mail_returned) == \
        (152075, 126473, 25602)
    assert harris.day == date(2024, 10, 21)


def test_the_partial_total_never_leaks_into_a_county_row(partial2024):
    assert "TOTAL" not in {r.county_name for r in partial2024.county_rows}
    assert all(r.county_fips != "48000" for r in partial2024.county_rows)


def test_a_complete_report_does_publish_the_sos_own_total(gen2022):
    """When all 254 counties are listed the TOTAL is a genuine Texas figure --
    and it matches our own sum of the counties to the ballot."""
    assert len(gen2022.county_rows) == 254 == tx.EXPECTED_COUNTIES
    (state,) = gen2022.state_rows
    assert state.state == "TX" and state.day == date(2022, 10, 24)
    assert (state.ballots_total, state.inperson, state.mail_returned) == \
        (647957, 476460, 171497)
    assert state.ballots_total == sum(r.ballots_total for r in gen2022.county_rows)
    assert state.inperson == sum(r.inperson for r in gen2022.county_rows)
    assert state.mail_returned == sum(r.mail_returned for r in gen2022.county_rows)


def test_dropping_one_county_is_enough_to_withhold_the_total():
    """The gate is coverage, not a guess about which counties matter."""
    markup = FULL.read_text(encoding="utf-8")
    trimmed = markup.replace("<th>LOVING</th>", "<th>TOTAL</th>", 1)
    result = tx.parse(trimmed, 2022)
    assert len(result.county_rows) == 253
    assert result.state_rows == []


# --------------------------------------------------------------------------
# Counties, keyed by FIPS
# --------------------------------------------------------------------------
def test_all_county_fips_are_five_digit_texas(gen2022):
    for row in gen2022.county_rows:
        assert len(row.county_fips) == 5 and row.county_fips.startswith("48")
    assert len({r.county_fips for r in gen2022.county_rows}) == 254
    by_fips = {r.county_fips: r for r in gen2022.county_rows}
    assert by_fips["48113"].county_name == "Dallas County"
    assert by_fips["48029"].county_name == "Bexar County"


def test_the_2024_layout_has_an_extra_column_and_still_parses(partial2024):
    """Matching by position would have shifted every count by one in 2024."""
    dallas = next(r for r in partial2024.county_rows if r.county_fips == "48113")
    assert (dallas.ballots_total, dallas.inperson, dallas.mail_returned) == \
        (70542, 56341, 14201)


# --------------------------------------------------------------------------
# The as-of date comes out of the daily column's header
# --------------------------------------------------------------------------
def test_report_date_is_read_from_the_daily_column_header():
    assert tx.report_date(["county", "# in person on 10/21/2024"], 2024) == \
        date(2024, 10, 21)
    with pytest.raises(SchemaDrift, match="not the 2026 cycle"):
        tx.report_date(["county", "# in person on 10/21/2024"], 2026)
    with pytest.raises(SchemaDrift, match="no .* column to date it"):
        tx.report_date(["county", "cumulative by mail voters"], 2024)


# --------------------------------------------------------------------------
# Drift and absence
# --------------------------------------------------------------------------
def test_renamed_column_raises_drift():
    markup = FULL.read_text(encoding="utf-8").replace(
        "<th>Cumulative By Mail Voters</th>", "<th>Mail Ballots</th>", 1)
    with pytest.raises(SchemaDrift, match="missing columns"):
        tx.parse(markup, 2022)


def test_unknown_county_name_raises_drift():
    markup = FULL.read_text(encoding="utf-8").replace("<th>HARRIS</th>", "<th>ATLANTIS</th>", 1)
    with pytest.raises(SchemaDrift, match="unrecognised county"):
        tx.parse(markup, 2022)


def test_a_page_without_the_table_raises_drift():
    with pytest.raises(SchemaDrift, match="no early-voting table"):
        tx.parse("<html><body>Service unavailable</body></html>", 2026)


#: The stubs below wrap their dropdown in a minimal document because the real
#: pages are full HTML and the adapter checks that it got a page, not a blob.
PAGE = "<!DOCTYPE html><html><head><title>Elections</title></head><body>{}</body></html>"

ELECTION_LIST = PAGE.format("""
<select id="idElection" name="idElection">
<option value="">-- Select Election --</option>
<option value="49664">2024 NOVEMBER 5TH GENERAL ELECTION</option>
<option value="47009">2022 NOVEMBER 8TH GENERAL ELECTION</option>
<option value="49665">2024 MARCH 5TH DEMOCRATIC PRIMARY</option>
<option value="50643">2024 SPECIAL ELECTION CONGRESSIONAL DISTRICT 18</option>
</select>
""")

DATE_LIST = PAGE.format("""
<select id="selectedDate" name="selectedDate">
<option value="">-- Select Early Voting Date --</option>
<option value="2024-10-21 00:00:00.0">October 21,2024</option>
<option value="2024-10-22 00:00:00.0">October 22,2024</option>
</select>
<select id="pollPlaceIdtown" name="pollPlaceIdtown">
<option value="101">HARRIS</option>
</select>
""")


def test_the_general_is_picked_out_of_the_dropdown_by_name():
    assert tx.general_election_id(ELECTION_LIST, 2024) == "49664"
    assert tx.general_election_id(ELECTION_LIST, 2022) == "47009"


def test_a_cycle_with_no_general_listed_is_not_yet_published():
    """This is the path that runs every day until the SoS adds the election --
    exactly where Texas sits today."""
    with pytest.raises(NotYetPublished, match="has not listed a 2026"):
        tx.general_election_id(ELECTION_LIST, 2026)


def test_only_the_selected_date_dropdown_is_read():
    """The page carries a 254-option county dropdown beside the date one."""
    assert tx.ev_dates(DATE_LIST) == \
        ["2024-10-21 00:00:00.0", "2024-10-22 00:00:00.0"]


def _serve(elections=ELECTION_LIST, dates=DATE_LIST, report=None):
    def fake_get(url, **kwargs):
        if url == tx.ELECTIONS_URL:
            return elections.encode()
        if url == tx.EV_DATES_URL:
            return dates.encode()
        if report is None:
            raise Missing(f"TX: {url} returned 404")
        return report
    return fake_get


def test_missing_election_list_is_not_yet_published(monkeypatch):
    def missing(url, **kwargs):
        raise Missing(f"TX: {url} returned 404")

    monkeypatch.setattr(tx, "get", missing)
    with pytest.raises(NotYetPublished):
        tx.TXScraper().fetch(2026, date(2026, 9, 5))


def test_no_general_in_the_live_list_is_not_yet_published(monkeypatch):
    monkeypatch.setattr(tx, "get", _serve())
    with pytest.raises(NotYetPublished, match="has not listed a 2026"):
        tx.TXScraper().fetch(2026, date(2026, 9, 5))


def test_early_voting_not_open_yet_is_not_yet_published(monkeypatch):
    """The election is listed and early voting has not started; every posted
    date is in the future, so there is nothing to report."""
    monkeypatch.setattr(tx, "get", _serve())
    with pytest.raises(NotYetPublished, match="has not opened"):
        tx.TXScraper().fetch(2024, date(2024, 10, 1))


def test_no_dates_posted_at_all_is_not_yet_published(monkeypatch):
    empty = PAGE.format(
        '<select id="selectedDate" name="selectedDate"><option value="">x</option></select>')
    monkeypatch.setattr(tx, "get", _serve(dates=empty))
    with pytest.raises(NotYetPublished, match="no early-voting dates"):
        tx.TXScraper().fetch(2024, date(2024, 10, 25))


def test_the_latest_posted_day_on_or_before_the_run_is_fetched(monkeypatch):
    asked = {}

    def fake_get(url, **kwargs):
        if url == tx.ELECTIONS_URL:
            return ELECTION_LIST.encode()
        if url == tx.EV_DATES_URL:
            return DATE_LIST.encode()
        asked.update(kwargs.get("params") or {})
        return PAGE.format(PARTIAL.read_text(encoding="utf-8")).encode()

    monkeypatch.setattr(tx, "get", fake_get)
    result = tx.TXScraper().fetch(2024, date(2024, 10, 21))
    assert asked["selectedDate"] == "2024-10-21 00:00:00.0"
    assert asked["idElection"] == "49664"
    assert result.state_rows == []          # still only 30 counties
    assert len(result.county_rows) == 30


def test_history_for_the_live_cycle_is_refused():
    with pytest.raises(NotYetPublished, match="not an archived cycle"):
        tx.TXScraper().fetch_history(date.today().year)


def test_history_walks_every_early_voting_day(monkeypatch):
    """Texas is the one state here whose site still serves each day separately
    after the election, so a backfill recovers the whole curve."""
    monkeypatch.setattr(tx, "get", _serve(report=PAGE.format(FULL.read_text(encoding="utf-8")).encode()))
    result = tx.TXScraper().fetch_history(2022)
    # Two dates in the stub list, one report each.
    assert len(result.state_rows) == 2
    assert len(result.county_rows) == 254 * 2


def test_adapter_identity():
    scraper = tx.TXScraper()
    assert (scraper.state, scraper.name, scraper.tier) == ("TX", "tx-sos", 1)
