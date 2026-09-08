"""Oregon: the SoS Daily Ballot Returns report, in both of its layouts.

Five fixtures, all real files, all sliced with pypdf and otherwise untouched.
The three 2022 ones are three archived captures of the SAME URL and are what the
archive sweep merges; each carries a party split for its own as-of day only.

* `G22-Daily-Ballot-Returns_2022-10-25.pdf` -- the CLASSIC layout on the third
  day of the 2022 general's return period (Wayback `20221025193050`). Pages 1-3
  of 4 (the cross-cycle comparison page is dropped). This is the awkward one:
  only three of thirteen day columns carry values, Columbia and Wallowa print an
  en-dash instead of a zero, the statewide party row renders one cell as Excel's
  `#######`, and the day matrix sums to 742 fewer ballots than the summary page.
* `G22-Daily-Ballot-Returns_2022-11-04.pdf` -- the MORNING-AFTER vintage
  (Wayback `20221104231413`), stamped 11/04/22 with day columns that stop at
  11-03. The summary is filed under the stamp and `ballots_new` is blank there,
  because the file says nothing about how many ballots arrived on the 4th.
* `G22-Daily-Ballot-Returns_2022-11-08.pdf` -- Election Day 2022 (Wayback
  `20221108183430`), thirteen day columns, stamped the same day it covers. This
  is the capture that puts Oregon's party split at `days_to_election = 0`.
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

from ev.adapters._net import Missing
from ev.adapters.base import FetchResult, NotYetPublished, SchemaDrift
from ev.schema import (
    Provenance, StateDay, TIER_SCRAPER, county_row_to_dict, state_row_to_dict,
)

orx = importlib.import_module("ev.adapters.or")

FIXTURES = Path(__file__).parent / "fixtures" / "or"
CLASSIC_2022 = FIXTURES / "G22-Daily-Ballot-Returns_2022-10-25.pdf"
CLASSIC_2022_1104 = FIXTURES / "G22-Daily-Ballot-Returns_2022-11-04.pdf"
CLASSIC_2022_1108 = FIXTURES / "G22-Daily-Ballot-Returns_2022-11-08.pdf"
CLASSIC_2024 = FIXTURES / "G24-Daily-Ballot-Returns_2024-11-05.pdf"
POWERBI_2026 = FIXTURES / "May-19-2026-Daily-Ballot-Returns_2026-05-13_pages2-4.pdf"

#: The three archived 2022 captures, under the Wayback stamps they really carry.
#: `ARCHIVED[2022]` names two more (11-07 and 11-09) that these tests do not ship
#: a fixture for, so the sweep is also exercised against captures it cannot
#: fetch -- which must cost those days and nothing else.
ARCHIVE_2022: dict[str, Path] = {
    "20221025193050": CLASSIC_2022,
    "20221104231413": CLASSIC_2022_1104,
    "20221108183430": CLASSIC_2022_1108,
}


def _sweep(monkeypatch, captures: dict[str, object], cycle: int = 2022) -> FetchResult:
    """Run `fetch_history` against `captures` ({stamp: fixture path or bytes}).

    Anything not in the map answers 404, including the ARCHIVED stamps this test
    file ships no fixture for.
    """
    scraper = orx.ORScraper()
    monkeypatch.setattr(orx, "_archive_stamps", lambda url, c: list(captures))

    def download(url, *, filename, use_cache=False, min_interval=orx.DEFAULT_MIN_INTERVAL):
        stamp = url.split("/web/", 1)[1].split("id_/", 1)[0]
        body = captures.get(stamp)
        if body is None:
            raise Missing(f"OR: {url} returned 404")
        return body if isinstance(body, bytes) else body.read_bytes()

    monkeypatch.setattr(scraper, "_download", download)
    return scraper.fetch_history(cycle)


def _party(row) -> tuple:
    return (row.party_dem, row.party_rep, row.party_npa, row.party_oth)


@pytest.fixture(scope="module")
def gen2022() -> FetchResult:
    return orx.parse(CLASSIC_2022.read_bytes(), 2022)


@pytest.fixture(scope="module")
def gen2022_1104() -> FetchResult:
    return orx.parse(CLASSIC_2022_1104.read_bytes(), 2022)


@pytest.fixture(scope="module")
def gen2022_1108() -> FetchResult:
    return orx.parse(CLASSIC_2022_1108.read_bytes(), 2022)


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
    """ARCHIVED is the verified floor the sweep starts from, not the sweep.

    Every stamp in it was fetched and parsed end to end; they are kept so a
    backfill still works when the Wayback CDX index is unreachable, and the
    post-canvass FINAL versions are left out of it only because they are not
    needed -- `_check_party` refuses them on their own arithmetic when the CDX
    sweep does turn them up.
    """
    assert set(orx.ARCHIVED) == {2022, 2024}
    for cycle, (url, stamps) in orx.ARCHIVED.items():
        assert url.startswith("https://sos.oregon.gov/")
        assert stamps and all(len(s) == 14 and s.isdigit() for s in stamps)
    # The 2022 stamps are what put a party split on four days a model can use.
    assert set(ARCHIVE_2022) <= set(orx.ARCHIVED[2022][1])


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


# --------------------------------------------------------------------------
# The two new archived vintages, read on their own
# --------------------------------------------------------------------------
def test_the_election_day_capture_is_the_one_that_carries_dte_zero(gen2022_1108):
    """2022-11-08 is Election Day, so this report's party split lands at
    days_to_election = 0 -- the whole reason every capture is swept. Reading only
    the newest one put Oregon's only party row at -1, which both models drop."""
    last = gen2022_1108.state_rows[-1]
    assert last.day == date(2022, 11, 8)
    assert last.ballots_total == 1_389_930
    assert last.ballots_new == 51_151
    assert _party(last) == (573_627, 444_920, 280_470, 90_913)
    assert sum(_party(last)) == last.ballots_total
    assert len(gen2022_1108.state_rows) == 13
    assert len({row.county_fips for row in gen2022_1108.county_rows}) == 36
    counties = _on(gen2022_1108, date(2022, 11, 8))
    assert counties["41001"].ballots_total == 7_033        # Baker
    assert _party(counties["41001"]) == (1_220, 3_992, 1_323, 498)
    assert counties["41051"].ballots_total == 255_442      # Multnomah
    assert _party(counties["41051"]) == (159_507, 33_031, 49_357, 13_547)
    assert counties["41009"].ballots_new == 0              # Columbia, a real zero


def test_a_morning_after_report_leaves_ballots_new_blank(gen2022_1104):
    """The 11/04/22 report's day columns stop at 11-03, so it says nothing about
    how many ballots arrived on the 4th. Its summary is still filed under the
    stamp -- with the party split that belongs to it -- and `ballots_new` is
    blank there, on the state row exactly as on the county rows. Reusing the last
    day column published 11-03's 94,895 twice, the second time under a day it did
    not describe."""
    last = gen2022_1104.state_rows[-1]
    assert last.day == date(2022, 11, 4)
    assert last.ballots_total == 869_375
    assert last.ballots_new is None
    assert _party(last) == (357_866, 292_391, 161_072, 58_046)
    assert sum(_party(last)) == last.ballots_total
    # ...and 11-03, the last day the matrix actually covers, keeps that number.
    penultimate = gen2022_1104.state_rows[-2]
    assert (penultimate.day, penultimate.ballots_total, penultimate.ballots_new) == (
        date(2022, 11, 3), 869_375, 94_895
    )
    for row in _on(gen2022_1104, date(2022, 11, 4)).values():
        assert row.ballots_new is None
    counties = _on(gen2022_1104, date(2022, 11, 4))
    assert counties["41001"].ballots_total == 4_869        # Baker
    assert _party(counties["41001"]) == (862, 2_831, 823, 353)
    assert sum(_party(counties["41001"])) == 4_869


# --------------------------------------------------------------------------
# The archive sweep and the merge
# --------------------------------------------------------------------------
def test_the_sweep_merges_every_capture_into_one_series(monkeypatch):
    """Three captures, one curve, each day taken from the earliest report that
    covers it. 10-21..10-25 come from the 10-25 report, 10-26..11-04 from the
    11-04 report and 11-07..11-08 from the 11-08 one."""
    result = _sweep(monkeypatch, ARCHIVE_2022)
    assert [(row.day, row.ballots_total) for row in result.state_rows] == [
        (date(2022, 10, 21), 6_979),
        (date(2022, 10, 24), 64_563),
        (date(2022, 10, 25), 65_944),
        (date(2022, 10, 26), 238_196),
        (date(2022, 10, 27), 318_343),
        (date(2022, 10, 28), 414_071),
        (date(2022, 10, 31), 590_337),
        (date(2022, 11, 1), 666_713),
        (date(2022, 11, 2), 774_480),
        (date(2022, 11, 3), 869_375),
        (date(2022, 11, 4), 869_375),
        (date(2022, 11, 7), 1_338_779),
        (date(2022, 11, 8), 1_389_930),
    ]
    assert len(result.county_rows) == 13 * 36
    multnomah = sorted(
        (row for row in result.county_rows if row.county_fips == "41051"),
        key=lambda row: row.day,
    )
    assert [row.ballots_total for row in multnomah] == [
        2_107, 16_045, 16_045, 34_018, 47_753, 55_812, 87_681,
        88_266, 113_429, 125_721, 125_721, 238_513, 255_442,
    ]


def test_each_days_party_split_is_its_own_reports_and_no_other(monkeypatch):
    """THE BLANK RULE across the merge: a day with no report of its own keeps
    None, and never inherits the neighbouring day's split. Three of thirteen days
    were stamped by a report, and exactly those three carry a party split."""
    result = _sweep(monkeypatch, ARCHIVE_2022)
    stamped = {date(2022, 10, 25), date(2022, 11, 4), date(2022, 11, 8)}
    assert {row.day for row in result.state_rows if row.party_dem is not None} == stamped
    assert {row.day for row in result.county_rows if row.party_dem is not None} == stamped
    for row in result.state_rows + result.county_rows:
        if row.day in stamped:
            assert sum(_party(row)) == row.ballots_total
        else:
            assert _party(row) == (None, None, None, None)
    by_day = {row.day: row for row in result.state_rows}
    assert _party(by_day[date(2022, 10, 25)]) == (29_843, 21_022, 10_496, 4_583)
    assert _party(by_day[date(2022, 11, 4)]) == (357_866, 292_391, 161_072, 58_046)
    assert _party(by_day[date(2022, 11, 8)]) == (573_627, 444_920, 280_470, 90_913)


def test_the_merged_curve_never_goes_backwards(monkeypatch):
    """A cumulative total that falls is the failure mode the merge rule exists to
    avoid: Oregon restates a past day UPWARD in every later report, so mixing a
    day's own summary with a later report's matrix reads 79,739 on 10-24 and
    65,944 on 10-25. Taking each report's whole prefix cannot do that."""
    result = _sweep(monkeypatch, ARCHIVE_2022)
    series: dict[str, list[tuple[date, int]]] = {"statewide": []}
    for row in result.state_rows:
        series["statewide"].append((row.day, row.ballots_total))
    for row in sorted(result.county_rows, key=lambda r: r.day):
        series.setdefault(row.county_fips, []).append((row.day, row.ballots_total))
    assert len(series) == 37
    for key, points in series.items():
        for (day, total), (_next_day, nxt) in zip(points, points[1:]):
            assert nxt >= total, f"{key} falls after {day}: {total} -> {nxt}"


def test_a_capture_the_archive_cannot_serve_costs_that_day_and_nothing_else(monkeypatch):
    """`ARCHIVED[2022]` names five stamps and this test ships three. The other
    two 404 and are skipped; the sweep still returns the thirteen days the three
    fixtures cover."""
    result = _sweep(monkeypatch, ARCHIVE_2022)
    assert len(result.state_rows) == 13
    assert max(row.day for row in result.state_rows) == date(2022, 11, 8)


def test_a_capture_of_another_election_is_skipped_not_fatal(monkeypatch):
    """A stale crawl -- the URL captured while it still served a different
    election's report -- raises NotYetPublished from `parse`. `fetch` has always
    caught that and tried the next candidate URL; `fetch_history` did not, so one
    such capture aborted the whole sweep and cost the cycle every other day."""
    captures = dict(ARCHIVE_2022)
    captures["20221101000000"] = CLASSIC_2024
    result = _sweep(monkeypatch, captures)
    assert len(result.state_rows) == 13
    assert result.state_rows[-1].ballots_total == 1_389_930


def test_a_capture_that_will_not_parse_is_skipped_not_fatal(monkeypatch):
    """The post-canvass FINAL is the real case: its party table does not
    reconcile against its own totals and `_check_party` refuses it. Losing that
    capture must not cost the cycle its other days."""
    captures = dict(ARCHIVE_2022)
    captures["20221201000000"] = b"%PDF-1.4 truncated" + b"\0" * 5000
    result = _sweep(monkeypatch, captures)
    assert len(result.state_rows) == 13


def test_a_cycle_whose_captures_all_fail_is_not_yet_published(monkeypatch):
    with pytest.raises(NotYetPublished):
        _sweep(monkeypatch, {"20221201000000": b"%PDF-1.4 truncated" + b"\0" * 5000})


def test_two_captures_of_the_same_day_resolve_to_the_later_stamp():
    """Oregon regenerates the file during Election Day, so one as-of date can
    have two captures. fl.py and de.py both keep the later reading; so does this."""
    def capture(day: date, total: int) -> FetchResult:
        return FetchResult(state_rows=[StateDay(
            cycle=2022, state="OR", day=day, ballots_total=total, ballots_new=None,
            mail_requested=None, mail_returned=total, inperson=None,
            party_dem=None, party_rep=None, party_oth=None, party_npa=None,
        )])

    day = date(2022, 11, 8)
    merged = orx._merge([
        (day, "20221108071723", capture(day, 900_000)),
        (day, "20221108183430", capture(day, 1_389_930)),
    ])
    assert [row.ballots_total for row in merged.state_rows] == [1_389_930]


# --------------------------------------------------------------------------
# fetch / fetch_history guard parity
# --------------------------------------------------------------------------
def test_fetch_never_publishes_a_day_after_the_one_it_was_asked_for(monkeypatch):
    """ONE download gives Oregon every day of the season, so a run asked about an
    earlier day must not answer with days that had not happened when it was
    asked. nc.py clamps its own whole-curve-in-one-file source the same way; OR
    did not, which is the same guard-on-one-path-only shape that has bitten
    az.py, co.py, pa.py and ia.py."""
    scraper = orx.ORScraper()
    monkeypatch.setattr(scraper, "_discover", lambda: [f"{orx.SOS}/report.pdf"])
    monkeypatch.setattr(
        scraper, "_download", lambda url, **kw: CLASSIC_2022_1108.read_bytes()
    )
    result = scraper.fetch(2022, date(2022, 11, 2))
    assert max(row.day for row in result.state_rows) == date(2022, 11, 2)
    assert max(row.day for row in result.county_rows) == date(2022, 11, 2)
    # The 11-08 report's own reading of 11-02 -- 230 higher than the 11-04
    # report's 774,480, which is Oregon restating a past day upward.
    assert result.state_rows[-1].ballots_total == 774_710
    # ...and nothing at all is published for a day before the report starts.
    with pytest.raises(NotYetPublished):
        scraper.fetch(2022, date(2022, 10, 1))


def test_both_paths_run_every_row_through_the_same_parse(monkeypatch):
    """The validations that matter -- the party cross-check, the county count,
    the day-after-the-stamp check, the election the file names -- all live in
    `parse`, which both paths call. This is the assertion that they still do."""
    scraper = orx.ORScraper()
    monkeypatch.setattr(scraper, "_discover", lambda: [f"{orx.SOS}/report.pdf"])
    monkeypatch.setattr(
        scraper, "_download", lambda url, **kw: CLASSIC_2022_1108.read_bytes()
    )
    live = scraper.fetch(2022, date(2022, 11, 8))
    archived = _sweep(monkeypatch, {"20221108183430": CLASSIC_2022_1108})
    assert [(r.day, r.ballots_total, _party(r)) for r in live.state_rows] == [
        (r.day, r.ballots_total, _party(r)) for r in archived.state_rows
    ]
