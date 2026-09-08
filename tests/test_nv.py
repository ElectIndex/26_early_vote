"""Nevada: the Secretary of State's daily turnout reporting.

Nevada publishes no CSV, no XLSX and no JSON for early vote — every turnout
report is a PDF printed out of Excel — so the fixtures here are a real report and
a real slice of the index page that links it.

Two Nevada-specific things these tests pin:

* the state's own party vocabulary. Nevada's 2022 reports broke registration into
  Dem / Rep / Other / NPP and its 2024 reports collapsed that to Dem / Rep /
  OTHER. NPP is *non-partisan* and belongs in `npa`; OTHER is a real third party
  and belongs in `oth`; and when Nevada stops breaking nonpartisans out, `npa`
  goes BLANK rather than being invented out of OTHER.

* that nvsos.gov's Imperva bot wall reads as a SOURCE ERROR and not as absence.
  It answers with HTTP 200 and a real body, so nothing but its content
  distinguishes it from a page, and calling it "not published yet" would stop the
  ladder and leave Nevada blank for six weeks.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from ev.adapters import nv
from ev.adapters._net import Missing
from ev.adapters.base import NotYetPublished, SchemaDrift, SourceError
from ev.normalize import PARTY_NPA, PARTY_OTH

FIXTURES = Path(__file__).parent / "fixtures" / "nv"
REPORT = (FIXTURES / "2024_mail_ease_ev_cumulative_20241115.pdf").read_bytes()
INDEX = (FIXTURES / "2024_turnout_reporting_excerpt.html").read_bytes()
WALL = (FIXTURES / "incapsula_block.html").read_bytes()

#: The 2024 general's published statewide figures, straight off the report.
NV_TOTAL = 1_243_163
NV_MAIL = 700_222
NV_INPERSON = 542_941


@pytest.fixture
def parsed():
    return nv.parse(REPORT, 2024)


# --------------------------------------------------------------------------
# The report
# --------------------------------------------------------------------------
def test_every_nevada_county_is_present(parsed):
    """Nevada has 17 localities -- 16 counties plus Carson City, which is an
    independent city and not inside any of them."""
    assert len(parsed.county_rows) == 17
    assert "Carson City" in {row.county_name for row in parsed.county_rows}


def test_county_rows_are_fips_keyed(parsed):
    for row in parsed.county_rows:
        assert len(row.county_fips) == 5 and row.county_fips.startswith("32")
    by_name = {row.county_name: row.county_fips for row in parsed.county_rows}
    # Carson City is 32510, not a 0xx county code -- a name join would not know.
    assert by_name["Carson City"] == "32510"
    assert by_name["Clark County"] == "32003"


def test_state_row_is_nevadas_own_statewide_row(parsed):
    """Taken from the report's Statewide row, so our headline can never disagree
    with the Secretary of State's."""
    assert parsed.state_rows[0].ballots_total == NV_TOTAL


def test_party_buckets_sum_to_the_total(parsed):
    """Nevada registers by party and reports every bucket it breaks out."""
    row = parsed.state_rows[0]
    assert row.party_dem + row.party_rep + row.party_oth == row.ballots_total
    for county in parsed.county_rows:
        assert (county.party_dem + county.party_rep + county.party_oth
                == county.ballots_total)


def test_mail_and_inperson_are_separately_reported(parsed):
    """Both are their own published column blocks, not one derived from the other
    by subtraction, and together they account for every ballot."""
    row = parsed.state_rows[0]
    assert row.mail_returned == NV_MAIL
    assert row.inperson == NV_INPERSON
    assert row.mail_returned + row.inperson == row.ballots_total


def test_universal_mail_state_reports_no_request_stage_here(parsed):
    """Nevada mails every active registered voter a ballot and publishes the count
    in a different report, so it is unreported on this one.

    Blank, never 0 -- a 0 would claim not one Nevadan was sent a mail ballot in a
    state that mails one to everybody.
    """
    assert parsed.state_rows[0].mail_requested is None


def test_unbroken_out_nonpartisans_are_blank_not_zero(parsed):
    """The 2024 report has DEM / REP / OTHER and no NPP column.

    Nevada's nonpartisans are inside OTHER that cycle and cannot be pulled back
    out, so `party_npa` is BLANK. It is emphatically not 0: about a third of
    Nevada's registrations are non-partisan, and a 0 would render as "no
    non-partisan Nevadan has voted".
    """
    assert parsed.state_rows[0].party_npa is None
    assert parsed.state_rows[0].party_oth > 0
    for row in parsed.county_rows:
        assert row.party_npa is None


def test_ballots_new_is_blank_because_nevada_publishes_cumulative_only(parsed):
    assert parsed.state_rows[0].ballots_new is None


def test_nonpartisan_is_npa_and_other_is_oth():
    """Nevada's own column labels, through the one party vocabulary.

    "NPP" is what Nevada calls a voter who declined a party and "Other" is what it
    calls one who chose a minor one; collapsing the two loses the single
    most-watched number in early-vote coverage.
    """
    for label in ("NPP", "Non-Partisan", "Nonpartisan", "No Party"):
        assert nv.bucket(label) == PARTY_NPA
    assert nv.bucket("OTHER") == PARTY_OTH
    assert nv.bucket("Dem") == "dem" and nv.bucket("Rep") == "rep"


def test_an_unknown_party_column_is_drift_not_other():
    with pytest.raises(SchemaDrift):
        nv.bucket("Silver Party")


def test_party_columns_are_read_off_the_header_not_assumed(parsed):
    """The 2024 report's three blocks each carry the same three columns."""
    _, cells = nv._page(REPORT)
    assert nv.party_columns(cells) == ["dem", "rep", "oth"]


def test_the_row_is_dated_by_the_report_not_the_run(parsed):
    assert parsed.state_rows[0].day == date(2024, 11, 15)
    assert all(row.day == date(2024, 11, 15) for row in parsed.county_rows)


def test_a_row_whose_arithmetic_does_not_hold_is_drift():
    """The parser's whole anchor: a mis-read column cannot survive Nevada's own
    identities, so it raises rather than publishing a confident wrong number."""
    with pytest.raises(SchemaDrift):
        # Carson City's real row with one party cell nudged by a thousand.
        nv._row(
            "39,712 5,430 5,678 5,214 16,322 41.1% 2,346 6,437 2,869 11,652 29.3% "
            "8,776 12,115 8,083 27,974 70.4%",
            ["dem", "rep", "oth"],
        )


def test_an_accounting_dash_is_a_real_zero_not_an_unreported_cell():
    """Nevada's spreadsheets print a zero as "-".

    This is Esmeralda County's row verbatim off the sibling "Mail and EASE Ballot
    Information - Cumulative" report of 11/4/2024: 17 Democratic, 70 Republican
    and 28 other mail ballots back, not one EASE ballot, and the same 115
    combined. Nevada reports the EASE bucket and it is empty, which is a 0 -- the
    blank rule is about buckets a state does not report at all.
    """
    blocks = nv._row(
        "619 17 70 28 115 20.6% - - - - - 17 70 28 115 18.6%",
        ["dem", "rep", "oth"],
    )
    assert blocks[1] == {"dem": 0, "rep": 0, "oth": 0, "total": 0}
    assert blocks[2]["total"] == 115


def test_a_row_with_the_wrong_number_of_cells_is_drift():
    with pytest.raises(SchemaDrift):
        nv._row("39,712 5,430 5,678 5,214 16,322 41.1%", ["dem", "rep", "oth"])


def test_a_report_for_the_wrong_election_is_refused():
    """The index page carries the same report row for the presidential preference
    primary and the June primary as it does for the November general."""
    with pytest.raises(SchemaDrift):
        nv.parse(REPORT, 2026)


# --------------------------------------------------------------------------
# The index page
# --------------------------------------------------------------------------
def test_the_index_link_is_found_through_its_sentence():
    """The anchor text is "HERE", split across two anchors, and the report's own
    name has a link buried in the middle of it -- so neither the anchor text nor
    "the last link on the line" would find it on its own."""
    links = nv.report_links(INDEX)
    assert len(links) == 2
    assert all("showpublisheddocument" in link.url for link in links)


def test_the_general_is_picked_over_the_same_row_for_the_primary():
    """One page holds every election of the cycle. The parenthesised Updated date
    is what separates the November general from the June primary."""
    picked = nv.pick_link(nv.report_links(INDEX), 2024)
    assert picked.updated == date(2024, 11, 15)
    assert picked.url.endswith("15581/638672847178800000")


def test_a_page_with_no_row_for_this_cycle_picks_nothing():
    """2026's page will not carry 2024's dates -- and must not fall back to them."""
    assert nv.pick_link(nv.report_links(INDEX), 2026) is None


def test_the_incapsula_wall_is_recognised():
    assert nv._blocked(WALL) is True
    assert nv._blocked(INDEX) is False


# --------------------------------------------------------------------------
# The daily path
# --------------------------------------------------------------------------
def _serve(monkeypatch, handler):
    monkeypatch.setattr(nv, "get", lambda url, **kw: handler(url))


def test_no_turnout_page_yet_is_not_yet_published(monkeypatch):
    """The normal outcome every day from now until Nevada opens early voting:
    the cycle's turnout-reporting page does not exist. Must STOP the ladder."""
    def handler(url):
        raise Missing(f"NV: {url} returned 404")

    _serve(monkeypatch, handler)
    with pytest.raises(NotYetPublished):
        nv.NVScraper().fetch(2026, date(2026, 9, 6))


def test_a_page_without_the_report_yet_is_not_yet_published(monkeypatch):
    """The page is up but the cumulative report has not been posted on it."""
    _serve(monkeypatch, lambda url: INDEX)
    with pytest.raises(NotYetPublished):
        nv.NVScraper().fetch(2026, date(2026, 11, 1))


def test_a_report_dated_after_today_is_not_yet_published(monkeypatch):
    """Backfilling a day Nevada had not reached yet must not publish the future."""
    _serve(monkeypatch, lambda url: INDEX if url.endswith("reporting") else REPORT)
    with pytest.raises(NotYetPublished):
        nv.NVScraper().fetch(2024, date(2024, 11, 1))


def test_the_bot_wall_is_a_source_error_not_absence(monkeypatch):
    """nvsos.gov answers automation with an Incapsula interstitial carrying HTTP
    200. That is a block, not a state that has published nothing: it must fall
    through to the aggregator rather than stopping the ladder."""
    _serve(monkeypatch, lambda url: WALL)
    with pytest.raises(SourceError) as caught:
        nv.NVScraper().fetch(2026, date(2026, 10, 20))
    assert not isinstance(caught.value, NotYetPublished)


def test_the_walled_report_download_is_also_a_source_error(monkeypatch):
    """The wall sits in front of the document ids too, not just the pages."""
    _serve(monkeypatch, lambda url: INDEX if url.endswith("reporting") else WALL)
    with pytest.raises(SourceError):
        nv.NVScraper().fetch(2024, date(2024, 11, 20))


def test_the_happy_path_publishes_the_report(monkeypatch):
    _serve(monkeypatch, lambda url: INDEX if url.endswith("reporting") else REPORT)
    result = nv.NVScraper().fetch(2024, date(2024, 11, 20))
    assert result.state_rows[0].ballots_total == NV_TOTAL
    assert len(result.county_rows) == 17


# --------------------------------------------------------------------------
# The history path -- one final snapshot, and the SAME guards as the live one
# --------------------------------------------------------------------------
def test_history_publishes_the_cycles_final_report(monkeypatch):
    """Nevada replaces the cumulative report in place and drops the superseded
    document id, so a past cycle's page yields ONE day, not a curve. That day is
    the cycle's final position -- every county, both methods, the party split --
    and it is what the comparison lines anchor on."""
    _serve(monkeypatch, lambda url: INDEX if url.endswith("reporting") else REPORT)
    result = nv.NVScraper().fetch_history(2024)
    assert result.state_rows[0].day == date(2024, 11, 15)
    assert result.state_rows[0].ballots_total == NV_TOTAL
    assert result.state_rows[0].mail_returned == NV_MAIL
    assert result.state_rows[0].inperson == NV_INPERSON
    assert len(result.county_rows) == 17
    # One snapshot, so one day across every row -- never a series.
    assert {row.day for row in result.county_rows} == {date(2024, 11, 15)}


def test_history_refuses_the_current_cycle(monkeypatch):
    """An in-progress cycle's "final" is not final. `fetch` is the path that
    should be reading the report while Nevada is still replacing it."""
    def explode(url, **kw):  # pragma: no cover - must never run
        raise AssertionError("history must not fetch the running cycle")

    monkeypatch.setattr(nv, "get", explode)
    with pytest.raises(NotYetPublished, match="not an archived cycle"):
        nv.NVScraper().fetch_history(date.today().year)


def test_history_with_no_page_for_that_cycle_is_not_yet_published(monkeypatch):
    """VERIFIED 2026-09-08: the 2022 turnout-reporting page was retired and now
    404s, so 2022 must yield nothing rather than reaching for another cycle's
    numbers."""
    def handler(url):
        raise Missing(f"NV: {url} returned 404")

    _serve(monkeypatch, handler)
    with pytest.raises(NotYetPublished):
        nv.NVScraper().fetch_history(2022)


def test_history_refuses_a_report_from_another_election(monkeypatch):
    """The 2024 report carries "2024 General Election" in its own page header.
    Asked for 2022, the parser must refuse it rather than relabel it."""
    _serve(monkeypatch, lambda url: INDEX if url.endswith("reporting") else REPORT)
    with pytest.raises(NotYetPublished):
        # pick_link finds no 2022-dated row on the 2024 page.
        nv.NVScraper().fetch_history(2022)


@pytest.mark.parametrize("walled", ["index", "report"])
def test_history_reads_the_wall_as_a_source_error_exactly_like_fetch(monkeypatch, walled):
    """⚠️ GUARD PARITY. The live path has always refused the Imperva wall; a
    history path that read a 212-byte block page as "Nevada published nothing"
    would stop the backfill on a report that is sitting right there."""
    def handler(url):
        if url.endswith("reporting"):
            return WALL if walled == "index" else INDEX
        return WALL

    _serve(monkeypatch, handler)
    with pytest.raises(SourceError) as caught:
        nv.NVScraper().fetch_history(2024)
    assert not isinstance(caught.value, NotYetPublished)
