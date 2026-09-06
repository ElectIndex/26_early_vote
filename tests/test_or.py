"""Oregon: the SoS Daily Ballot Returns report, in both of its layouts.

Three fixtures, all real files, all sliced with pypdf and otherwise untouched:

* `G22-Daily-Ballot-Returns_2022-10-25.pdf` -- the CLASSIC layout on the third
  day of the 2022 general's return period. Pages 1-3 of 4 (the cross-cycle
  comparison page is dropped). This is the awkward one: only three of thirteen
  day columns carry values, Columbia and Wallowa print an en-dash instead of a
  zero, the statewide party row renders one cell as Excel's `#######`, and the
  day matrix sums to 742 fewer ballots than the summary page.
* `G24-Daily-Ballot-Returns_2024-11-05.pdf` -- the CLASSIC layout on Election
  Day 2024, with all thirteen days populated and the party split spread over
  TWO pages. Pages 1-4 of 5.
* `May-19-2026-Daily-Ballot-Returns_2026-05-13_pages2-4.pdf` -- the POWER BI
  layout Oregon switched to for the May 2026 primary. Pages 2-4 of 5; page 1 is
  a map and page 5 is notes. Blank cells everywhere, which is the whole reason
  that layout is read by character position.

The module is imported through importlib because `or` is a Python keyword.
"""

from __future__ import annotations

import importlib
from datetime import date
from pathlib import Path

import pytest

from ev.adapters.base import FetchResult, NotYetPublished, SchemaDrift
from ev.schema import Provenance, TIER_SCRAPER, county_row_to_dict, state_row_to_dict

orx = importlib.import_module("ev.adapters.or")

FIXTURES = Path(__file__).parent / "fixtures" / "or"
CLASSIC_2022 = FIXTURES / "G22-Daily-Ballot-Returns_2022-10-25.pdf"
CLASSIC_2024 = FIXTURES / "G24-Daily-Ballot-Returns_2024-11-05.pdf"
POWERBI_2026 = FIXTURES / "May-19-2026-Daily-Ballot-Returns_2026-05-13_pages2-4.pdf"


@pytest.fixture(scope="module")
def gen2022() -> FetchResult:
    return orx.parse(CLASSIC_2022.read_bytes(), 2022)


@pytest.fixture(scope="module")
def gen2024() -> FetchResult:
    return orx.parse(CLASSIC_2024.read_bytes(), 2024)


@pytest.fixture(scope="module")
def primary2026():
    """The May 2026 primary, parsed as if it were its cycle's general.

    `parse` refuses it by its own `Election Date: 5/19/2026` -- which is the
    point of that check and is asserted separately -- so the only way to
    exercise the Power BI assembly end to end is to move the expected election
    date onto the primary's.
    """
    original = orx.election_date
    orx.election_date = lambda cycle: date(2026, 5, 19)
    try:
        yield orx.parse(POWERBI_2026.read_bytes(), 2026)
    finally:
        orx.election_date = original


def _day(result: FetchResult) -> date:
    return max(row.day for row in result.county_rows)


def _on(result: FetchResult, day: date) -> dict[str, object]:
    return {row.county_fips: row for row in result.county_rows if row.day == day}


# --------------------------------------------------------------------------
# The numbers Oregon published
# --------------------------------------------------------------------------
def test_2024_statewide_matches_oregons_own_total(gen2024):
    last = gen2024.state_rows[-1]
    assert last.day == date(2024, 11, 5)
    assert last.ballots_total == 2_004_468
    assert last.ballots_new == 266_235


def test_2024_rebuilds_the_whole_daily_curve_from_one_download(gen2024):
    days = [row.day for row in gen2024.state_rows]
    assert days[0] == date(2024, 10, 18)
    assert days[-1] == date(2024, 11, 5)
    assert len(days) == 13
    assert days == sorted(days)
    # Oregon's own cumulative row, not a running sum we invented.
    assert [r.ballots_total for r in gen2024.state_rows][:3] == [15_747, 97_793, 164_050]


def test_all_36_counties_are_present_in_both_cycles(gen2022, gen2024):
    for result in (gen2022, gen2024):
        assert len({row.county_fips for row in result.county_rows}) == 36


def test_2022_party_split_sums_to_the_statewide_total(gen2022):
    last = gen2022.state_rows[-1]
    assert last.ballots_total == 65_944
    parts = (last.party_dem, last.party_rep, last.party_npa, last.party_oth)
    assert parts == (29_843, 21_022, 10_496, 4_583)
    assert sum(parts) == last.ballots_total


def test_2024_party_split_sums_to_the_statewide_total(gen2024):
    last = gen2024.state_rows[-1]
    parts = (last.party_dem, last.party_rep, last.party_npa, last.party_oth)
    assert parts == (776_441, 592_308, 497_111, 138_608)
    assert sum(parts) == last.ballots_total == 2_004_468


def test_the_independent_party_of_oregon_is_not_counted_as_unaffiliated(gen2024):
    """Oregon's unaffiliated bucket is "Nonaffiliated"; "Independent" is a
    ballot-qualified minor party with 150,715 registrants. `normalize.party()`
    would send it to `npa` and overstate the unaffiliated share by six figures.
    """
    assert orx.OR_PARTY["independent"] == "oth"
    assert orx.OR_PARTY["nonaffiliated"] == "npa"
    last = gen2024.state_rows[-1]
    # Nonaffiliated returns alone; the Independent Party's 104,174 are in oth.
    assert last.party_npa == 497_111
    assert last.party_oth >= 104_174


# --------------------------------------------------------------------------
# The two things the file does that a naive parser gets wrong
# --------------------------------------------------------------------------
def test_an_en_dash_in_the_summary_is_a_zero_not_a_missing_county(gen2022):
    """Columbia and Wallowa printed "-" for ballots returned on 2022-10-25.

    Reading that as "no such row" silently dropped two of Oregon's 36 counties;
    reading it as None would render as "not reported" for a county that had in
    fact returned nothing.
    """
    rows = _on(gen2022, date(2022, 10, 25))
    assert rows["41009"].ballots_total == 0     # Columbia
    assert rows["41063"].ballots_total == 0     # Wallowa


def test_the_summary_wins_where_the_day_matrix_disagrees(gen2022):
    """Curry County's three day columns sum to 991; the summary says 1,290.

    Counties backfill a late report into the summary without restating the day
    it belonged to, so the as-of day's cumulative figure is always the summary's.
    """
    rows = _on(gen2022, date(2022, 10, 25))
    assert rows["41015"].ballots_total == 1_290          # Curry, from the summary
    assert gen2022.state_rows[-1].ballots_total == 65_944  # not the matrix's 65,202


def test_a_partly_filled_day_matrix_publishes_only_the_days_that_happened(gen2022):
    days = sorted({row.day for row in gen2022.county_rows})
    assert days == [date(2022, 10, 21), date(2022, 10, 24), date(2022, 10, 25)]


# --------------------------------------------------------------------------
# THE BLANK RULE and the all-mail mapping
# --------------------------------------------------------------------------
def test_inperson_is_never_zero_because_oregon_has_no_in_person_early_vote(
    gen2022, gen2024
):
    for result in (gen2022, gen2024):
        for row in result.state_rows + result.county_rows:
            assert row.inperson is None


def test_mail_requested_is_never_zero(gen2024):
    for row in gen2024.state_rows:
        assert row.mail_requested is None


def test_every_returned_ballot_is_a_mail_ballot(gen2024):
    for row in gen2024.state_rows + gen2024.county_rows:
        assert row.mail_returned == row.ballots_total


def test_none_writes_as_a_blank_cell_not_a_zero(gen2024):
    prov = Provenance(tier=TIER_SCRAPER, name="or-sos")
    state = state_row_to_dict(
        FetchResult(state_rows=[gen2024.state_rows[-1]]).stamp(prov).state_rows[0]
    )
    county = county_row_to_dict(
        FetchResult(county_rows=[gen2024.county_rows[0]]).stamp(prov).county_rows[0]
    )
    assert state["inperson"] == ""
    assert state["mail_requested"] == ""
    assert county["inperson"] == ""


# --------------------------------------------------------------------------
# The Power BI layout
# --------------------------------------------------------------------------
def test_the_primary_is_refused_by_its_own_election_date():
    """The live report during the primary season parses perfectly and must
    still never be published as the general's.

    NotYetPublished, not SchemaDrift: nothing is wrong with the file, so the
    ladder must STOP rather than fall through to a source that would invent a
    number for an election that has not started.
    """
    with pytest.raises(NotYetPublished) as caught:
        orx.parse(POWERBI_2026.read_bytes(), 2026)
    assert "2026-05-19" in str(caught.value)


def test_power_bi_tables_reconcile_against_their_own_totals():
    layout = orx._pages(POWERBI_2026.read_bytes(), layout=True)
    assert orx.pbi_dates(layout) == (date(2026, 5, 19), date(2026, 5, 13))
    counties = orx.pbi_counties(layout)
    assert counties["statewide"] == (3_103_717, 382_662)
    assert counties["41001"] == (12_999, 2_292)      # Baker
    assert len(counties) == 37                        # 36 counties plus statewide
    party = orx.pbi_party(layout)
    assert sum(party["statewide"].values()) == 382_662
    assert party["statewide"]["dem"] == 152_964
    assert party["statewide"]["rep"] == 134_111


def test_a_blank_power_bi_cell_is_a_zero_only_because_the_row_total_proves_it(
    primary2026,
):
    """Power BI prints nothing rather than 0. Lane County received no ballots on
    5/1, 5/7 and 5/12; its row still sums to its own printed Total of 32,342,
    which is what licenses reading those gaps as zeros."""
    lane = sorted(
        (r for r in primary2026.county_rows if r.county_fips == "41039"),
        key=lambda r: r.day,
    )
    assert [r.ballots_new for r in lane] == [0, 2687, 134, 5215, 0, 11730, 12576, 0]
    assert lane[-1].ballots_total == 32_342


def test_power_bi_layout_produces_the_same_shape_as_the_classic_one(primary2026):
    assert len(primary2026.state_rows) == 8
    assert len({r.county_fips for r in primary2026.county_rows}) == 36
    last = primary2026.state_rows[-1]
    assert last.day == date(2026, 5, 12)
    assert last.ballots_total == 382_662
    assert sum((last.party_dem, last.party_rep, last.party_npa, last.party_oth)) == 382_662


# --------------------------------------------------------------------------
# Drift
# --------------------------------------------------------------------------
def test_a_day_column_after_the_reports_own_stamp_raises_drift():
    """Oregon generates the report the morning AFTER the day it covers, so a
    stamp later than the last day column is normal. The reverse is impossible
    and means the two tables were read out of step."""
    with pytest.raises(SchemaDrift) as caught:
        orx._rows(
            cycle=2026,
            as_of=date(2026, 10, 20),
            totals={"statewide": (100, 10), "41001": (50, 5)},
            dates=[date(2026, 10, 21)],
            series={"41001": [5], "statewide new": [10]},
            party={},
        )
    assert "after the report's own stamp" in str(caught.value)


def test_the_2022_backfill_covers_the_whole_season_not_just_one_snapshot():
    """The stamps in ARCHIVED are chosen so a backfill recovers the curve, and
    deliberately exclude the post-canvass FINAL versions, whose party tables do
    not reconcile against their own totals."""
    assert set(orx.ARCHIVED) == {2022, 2024}
    for cycle, (url, stamps) in orx.ARCHIVED.items():
        assert url.startswith("https://sos.oregon.gov/")
        assert stamps and all(len(s) == 14 and s.isdigit() for s in stamps)


def test_a_party_column_we_do_not_know_raises_rather_than_bucketing():
    with pytest.raises(SchemaDrift):
        orx._party("Cascadia Independence")


def test_a_report_for_another_cycle_is_refused():
    with pytest.raises(NotYetPublished) as caught:
        orx.parse(CLASSIC_2022.read_bytes(), 2024)
    assert "2022" in str(caught.value)


def test_something_that_is_not_a_pdf_is_a_source_error():
    from ev.adapters.base import SourceError

    with pytest.raises(SourceError):
        orx.parse(b"<html><body>not posted yet</body></html>", 2026)


# --------------------------------------------------------------------------
# The ladder contract
# --------------------------------------------------------------------------
def test_a_missing_report_stops_the_ladder_rather_than_falling_through(monkeypatch):
    scraper = orx.ORScraper()
    monkeypatch.setattr(scraper, "_discover", lambda: [])
    from ev.adapters._net import Missing

    def missing(url, **kwargs):
        raise Missing(f"OR: {url} returned 404")

    monkeypatch.setattr(scraper, "_download", missing)
    with pytest.raises(NotYetPublished):
        scraper.fetch(2026, date(2026, 9, 6))


def test_history_for_a_cycle_with_no_archive_is_not_yet_published():
    with pytest.raises(NotYetPublished):
        orx.ORScraper().fetch_history(2018)


def test_the_adapter_declares_itself_correctly():
    scraper = orx.ORScraper()
    assert (scraper.state, scraper.name, scraper.tier) == ("OR", "or-sos", TIER_SCRAPER)
