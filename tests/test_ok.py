"""Oklahoma: the State Election Board's DevExpress dashboards.

Every fixture is a real `DashboardItemGetAction` response, saved verbatim from
`stats.okelections.gov` -- the compressed DSR shape and all. The 2024 set was
captured 2026-09-06; the 2020 and 2022 voter-history sets 2026-09-08, when the
archive route was built. They are what catches the two failure modes that matter
for a JSON dashboard: a renumbered `DataItemN` slot silently transposing
measures onto the wrong counties, and a new voting-method label quietly
vanishing out of the total.

Three cycles of the SAME dashboard is not redundancy. `VHCountsByCounty` is the
only CERTIFIED Oklahoma archive there is, `fetch_history` reads a different
election out of it for each cycle, and the three differ in the one way that
matters: 2020 is a mail-majority electorate (287,010 absentee against 167,132
early in person) and 2022 and 2024 are in-person-majority ones, so a bug that
swapped the two method filters would still look plausible against 2024 alone.

The DAILY curve comes from somewhere else: the Election Board's PDF reports,
linked from an index page on oklahoma.gov. Three real PDFs and an excerpt of
that index (saved 2026-10-13) sit beside the JSON fixtures:

  ballot-totals-by-affiliation-20241104-1.pdf   mail final, 11/4/2024 8:34 AM,
      78 pages -- and it was CROSS-FILED in the in-person folder.
  absentee-inperson-totals-20241104-1.pdf       in-person final, 8:32 AM, 78
      pages -- cross-filed in the mail folder.
  absentee-inperson-totals-20241030-2.pdf       the WIDE ap2680 layout, and a
      filename that lies: its printed run is 10/31/2024 8:00 AM.

The two 78-page files are ~120 KB each. They are kept whole rather than cut to
a few pages because the parser's own sum check -- 77 county pages must add up
to the `99 STATE` page -- is one of the things under test, and a cut file
cannot pass it.
"""

from __future__ import annotations

import copy
import json
import re
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path

import pytest

from ev.adapters import _methods, _net, ok
from ev.adapters.base import NotYetPublished, SchemaDrift

FIXTURES = Path(__file__).parent / "fixtures" / "ok"

#: The archived PDFs, by the basename the index links them under.
ARCHIVE_PDFS = (
    "ballot-totals-by-affiliation-20241104-1.pdf",
    "absentee-inperson-totals-20241104-1.pdf",
    "absentee-inperson-totals-20241030-2.pdf",
)
INDEX_EXCERPT = "absentee-voting-statistics-excerpt.html"

ELECTION_2020 = date(2020, 11, 3)
ELECTION_2022 = date(2022, 11, 8)
ELECTION_2024 = date(2024, 11, 5)
STAMP_2024 = ELECTION_2024.strftime(ok._FILTER_STAMP)

#: How the adapter spells an election in a FILTER value -> the cycle it is.
_CYCLE_OF_STAMP = {
    e.strftime(ok._FILTER_STAMP): c
    for c, e in ((2020, ELECTION_2020), (2022, ELECTION_2022), (2024, ELECTION_2024))
}

#: (dashboard, item, cycle, party-or-method filter value) -> fixture file. The
#: cycle is part of the key because `fetch_history` asks the SAME item for a
#: different election per cycle; keying without it is how a test ends up
#: asserting 2024's numbers against a 2022 query.
_ROUTES = {
    # The date combos are asked with no filter at all and answer with every
    # election the dashboard has ever carried, so one fixture serves all cycles.
    (ok.HISTORY_DASHBOARD, ok.HISTORY_DATE_COMBO, None, None): "VHCountsByCounty_dates.json",
    (ok.ABSENTEE_DASHBOARD, ok.ABSENTEE_DATE_COMBO, None, None): "AbsStatsByCounty_dates.json",

    (ok.HISTORY_DASHBOARD, ok.HISTORY_METHOD_PIE, 2020, None): "VHCountsByCounty_methods_2020.json",
    (ok.HISTORY_DASHBOARD, ok.HISTORY_PARTY_COMBO, 2020, None): "VHCountsByCounty_parties_2020.json",
    (ok.HISTORY_DASHBOARD, ok.HISTORY_PIVOT, 2020, "Absentee"): "VHCountsByCounty_pivot_2020_absentee.json",
    (ok.HISTORY_DASHBOARD, ok.HISTORY_PIVOT, 2020, "Early Voting"): "VHCountsByCounty_pivot_2020_early.json",

    (ok.HISTORY_DASHBOARD, ok.HISTORY_METHOD_PIE, 2022, None): "VHCountsByCounty_methods_2022.json",
    (ok.HISTORY_DASHBOARD, ok.HISTORY_PARTY_COMBO, 2022, None): "VHCountsByCounty_parties_2022.json",
    (ok.HISTORY_DASHBOARD, ok.HISTORY_PIVOT, 2022, "Absentee"): "VHCountsByCounty_pivot_2022_absentee.json",
    (ok.HISTORY_DASHBOARD, ok.HISTORY_PIVOT, 2022, "Early Voting"): "VHCountsByCounty_pivot_2022_early.json",

    (ok.HISTORY_DASHBOARD, ok.HISTORY_METHOD_PIE, 2024, None): "VHCountsByCounty_methods_2024.json",
    (ok.HISTORY_DASHBOARD, ok.HISTORY_PARTY_COMBO, 2024, None): "VHCountsByCounty_parties_2024.json",
    (ok.HISTORY_DASHBOARD, ok.HISTORY_PIVOT, 2024, "Absentee"): "VHCountsByCounty_pivot_2024_absentee.json",
    (ok.HISTORY_DASHBOARD, ok.HISTORY_PIVOT, 2024, "Early Voting"): "VHCountsByCounty_pivot_2024_early.json",

    (ok.ABSENTEE_DASHBOARD, ok.ABSENTEE_PARTY_COMBO, 2024, None): "AbsStatsByCounty_parties_2024.json",
    (ok.ABSENTEE_DASHBOARD, ok.ABSENTEE_PIVOT, 2024, None): "AbsStatsByCounty_pivot_2024.json",
    (ok.ABSENTEE_DASHBOARD, ok.ABSENTEE_PIVOT, 2024, "Democrat"): "AbsStatsByCounty_pivot_2024_democrat.json",
    (ok.ABSENTEE_DASHBOARD, ok.ABSENTEE_PIVOT, 2024, "Republican"): "AbsStatsByCounty_pivot_2024_republican.json",
    (ok.ABSENTEE_DASHBOARD, ok.ABSENTEE_PIVOT, 2024, "Independent"): "AbsStatsByCounty_pivot_2024_independent.json",
    (ok.ABSENTEE_DASHBOARD, ok.ABSENTEE_PIVOT, 2024, "Libertarian"): "AbsStatsByCounty_pivot_2024_libertarian.json",
}


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def zeroed(payload: dict) -> dict:
    """The same response with every measure set to 0 -- an election the
    dashboard lists but has credited nothing to."""
    payload = copy.deepcopy(payload)
    for slice_ in payload["ItemData"]["DataStorageDTO"]["Slices"]:
        for cells in slice_["Data"].values():
            for slot in cells:
                cells[slot] = 0
    return payload


def first_counties(payload: dict, keep: int) -> dict:
    """The same pivot response cut down to its first `keep` counties.

    Both dashboards are populated independently and the absentee one really did
    hold six counties while the season was starting, so "fewer counties than
    Oklahoma has" is a shape this source produces, not an invented one.
    """
    payload = copy.deepcopy(payload)
    for slice_ in payload["ItemData"]["DataStorageDTO"]["Slices"]:
        slice_["Data"] = {
            key: cells for key, cells in slice_["Data"].items()
            if all(int(part) < keep
                   for part in key.strip("[]").split(",") if part != "")
        }
    return payload


#: What a date combo looks like when the dashboard has no elections to offer.
NO_ELECTIONS = {
    "ItemData": {
        "MetaData": {
            "DimensionDescriptors": {
                "Default": [{"ID": "DataItem0", "DataMember": ok.COL_ELECTION}]},
            "MeasureDescriptors": [],
        },
        "DataStorageDTO": {"EncodeMaps": {"DataItem0": []}, "Slices": []},
    },
}


class Offline(ok.OKScraper):
    """The real adapter with its three HTTP seams replaced.

    `archive=False` (the default) answers the index page with no elections on
    it, so the dashboard tests see exactly the certified Election-Day row they
    always did. `archive=True` serves the saved index excerpt and the three
    archived PDFs by basename; every other PDF the index links is Missing, which
    the walk must skip, not die on.
    """

    def __init__(self, *, history=True, absentee=True, archive=False, patch=None):
        super().__init__()
        self.history_on = history
        self.absentee_on = absentee
        self.archive_on = archive
        self.patch = patch or {}
        self.calls: list[tuple] = []
        self.pdf_urls: list[str] = []

    def _index(self):
        if not self.archive_on:
            return "<html><body><h2>2024</h2></body></html>"
        return (FIXTURES / INDEX_EXCERPT).read_text(encoding="utf-8")

    def _pdf(self, url):
        self.pdf_urls.append(url)
        name = url.rsplit("/", 1)[-1]
        if name in ARCHIVE_PDFS:
            return (FIXTURES / name).read_bytes()
        raise _net.Missing(f"OK: {url} returned 404 (not a saved fixture)")

    def _item(self, dashboard, item, filters=()):
        self.calls.append((dashboard, item))
        off = (dashboard == ok.HISTORY_DASHBOARD and not self.history_on) or (
            dashboard == ok.ABSENTEE_DASHBOARD and not self.absentee_on)
        if off:
            # A dashboard that does not carry this election answers its date
            # combo with a list the election is not in, and is asked nothing
            # else. Anything else here is the adapter reaching past `_has`.
            assert item in (ok.HISTORY_DATE_COMBO, ok.ABSENTEE_DATE_COMBO), (
                f"{dashboard}/{item} queried for an election it does not carry")
            return NO_ELECTIONS
        selector = cycle = None
        for dimension, value in filters:
            if dimension["@DataMember"] in (ok.COL_METHOD, ok.COL_PARTY):
                selector = value
            elif dimension["@DataMember"] == ok.COL_ELECTION:
                cycle = _CYCLE_OF_STAMP[value]
        key = (dashboard, item, cycle, selector)
        if key in self.patch:
            return self.patch[key]
        return load(_ROUTES[key])


@pytest.fixture(scope="module")
def history_2024():
    return Offline().fetch_history(2024)


@pytest.fixture(scope="module")
def absentee_2024():
    """The route Oklahoma is on today: absentee dashboard, no voter history."""
    return Offline(history=False).fetch(2024, date(2024, 11, 20))


# --------------------------------------------------------------------------
# The numbers
# --------------------------------------------------------------------------
def test_history_reproduces_oklahomas_own_totals(history_2024):
    """401,467 early ballots for the 2024 general: 107,549 absentee plus
    293,918 early in-person, which is what the dashboard's own statewide
    aggregate returns."""
    row = history_2024.state_rows[0]
    assert row.day == ELECTION_2024
    assert row.ballots_total == 401_467
    assert row.mail_returned == 107_549
    assert row.inperson == 293_918


def test_history_covers_every_county(history_2024):
    assert len(history_2024.county_rows) == ok.EXPECTED_COUNTIES == 77
    assert {r.county_fips[:2] for r in history_2024.county_rows} == {"40"}


def test_party_counts_partition_the_total(history_2024):
    """Oklahoma registers by party and the dashboard carries all four, so the
    four buckets must add back up to the total -- if they do not, a filter is
    dropping ballots."""
    row = history_2024.state_rows[0]
    assert row.party_dem == 113_521
    assert row.party_rep == 236_011
    assert row.party_npa == 49_886   # "Independent" is a no-party registration
    assert row.party_oth == 2_049    # Libertarian
    assert sum((row.party_dem, row.party_rep, row.party_npa, row.party_oth)) == 401_467


def test_counties_sum_to_the_statewide_row(history_2024):
    row = history_2024.state_rows[0]
    assert sum(r.ballots_total for r in history_2024.county_rows) == row.ballots_total
    assert sum(r.inperson for r in history_2024.county_rows) == row.inperson


def test_counties_are_keyed_by_fips_not_name(history_2024):
    adair = [r for r in history_2024.county_rows if r.county_fips == "40001"][0]
    assert adair.county_name == "Adair County"
    assert adair.ballots_total == 1_433
    assert (adair.mail_returned, adair.inperson) == (181, 1_252)


# --------------------------------------------------------------------------
# THE BLANK RULE
# --------------------------------------------------------------------------
def test_absentee_only_route_leaves_inperson_blank(absentee_2024):
    """The absentee table does not contain Oklahoma's early in-person voting at
    all. A 0 here would claim nobody voted early in person, in a state where
    293,918 people did so in 2024."""
    row = absentee_2024.state_rows[0]
    assert row.inperson is None
    assert all(r.inperson is None for r in absentee_2024.county_rows)


def test_absentee_only_route_reports_requested_and_returned(absentee_2024):
    row = absentee_2024.state_rows[0]
    assert row.mail_requested == 130_640
    assert row.mail_returned == 107_874
    assert row.ballots_total == 107_874


def test_absentee_and_history_disagree_and_must_not_be_reconciled(
    history_2024, absentee_2024,
):
    """Two honest measurements of the same election: 107,874 ballots RECEIVED by
    the county boards against 107,549 credited in voter history. Nobody should
    later 'fix' one to match the other."""
    assert absentee_2024.state_rows[0].mail_returned == 107_874
    assert history_2024.state_rows[0].mail_returned == 107_549


def test_a_party_with_no_ballots_in_a_county_is_zero_not_blank(history_2024):
    """Oklahoma reports every registration, so an empty bucket is a real zero --
    the other half of THE BLANK RULE."""
    zeroes = [r for r in history_2024.county_rows if r.party_oth == 0]
    assert zeroes, "expected at least one county with no Libertarian early votes"
    assert all(r.party_oth is not None for r in history_2024.county_rows)


def test_ballots_new_is_never_invented(history_2024, absentee_2024):
    """Neither dashboard has a within-election date dimension, so there is no
    day-over-day number to report."""
    for result in (history_2024, absentee_2024):
        assert all(r.ballots_new is None for r in result.state_rows + result.county_rows)


# --------------------------------------------------------------------------
# Absence vs failure
# --------------------------------------------------------------------------
def test_a_cycle_in_neither_dashboard_is_not_yet_published():
    with pytest.raises(NotYetPublished):
        Offline(history=False, absentee=False).fetch(2026, date(2026, 9, 6))


def test_history_backfill_refuses_a_cycle_it_does_not_have():
    with pytest.raises(NotYetPublished):
        Offline().fetch_history(2026)


def test_zero_returned_ballots_stops_the_ladder():
    """The 2026 general is already ON the absentee dashboard with applications
    but no returned ballots. Publishing that as `ballots_total = 0` would be a
    zero the aggregator could never correct; NotYetPublished is the honest read
    and it stops the ladder."""
    empty = load("AbsStatsByCounty_pivot_2024.json")
    storage = empty["ItemData"]["DataStorageDTO"]
    for slice_ in storage["Slices"]:
        for cells in slice_["Data"].values():
            cells["2"] = 0          # the `Received` slot
    patched = Offline(history=False, patch={
        (ok.ABSENTEE_DASHBOARD, ok.ABSENTEE_PIVOT, 2024, None): empty,
    })
    with pytest.raises(NotYetPublished):
        patched.fetch(2024, date(2024, 10, 1))


# --------------------------------------------------------------------------
# Drift
# --------------------------------------------------------------------------
def test_an_unknown_voting_method_raises_rather_than_disappearing():
    """`Absentee + Early Voting` IS the early vote. A new label would otherwise
    be silently excluded from every Oklahoma number."""
    with pytest.raises(SchemaDrift):
        ok.check_methods(["Absentee", "Early Voting", "In-Person Absentee"])
    ok.check_methods(["Absentee", "Early Voting", "Election Day", "Protected"])


def test_a_renamed_measure_raises_rather_than_being_guessed():
    payload = load("VHCountsByCounty_pivot_2024_early.json")
    payload["ItemData"]["MetaData"]["MeasureDescriptors"][0]["DataMember"] = "Tally"
    with pytest.raises(SchemaDrift):
        ok.decode(payload, (ok.COL_COUNTY,), (ok.MEASURE_HISTORY,))


def test_slots_are_read_from_the_response_not_hardcoded():
    """County is DataItem6 in the absentee pivot and DataItem0 in the
    voter-history one. Anything that hardcoded either would put Oklahoma's
    numbers under the wrong counties."""
    absentee = load("AbsStatsByCounty_pivot_2024.json")
    history = load("VHCountsByCounty_pivot_2024_early.json")
    assert ok._slot_ids(absentee)[0][ok.COL_COUNTY] == "DataItem6"
    assert ok._slot_ids(history)[0][ok.COL_COUNTY] == "DataItem0"


def test_an_unrecognised_county_name_raises():
    payload = load("VHCountsByCounty_pivot_2024_early.json")
    maps = payload["ItemData"]["DataStorageDTO"]["EncodeMaps"]
    maps["DataItem0"][0] = "Sequoyah West"
    with pytest.raises(SchemaDrift):
        Offline(patch={
            (ok.HISTORY_DASHBOARD, ok.HISTORY_PIVOT, 2024, "Early Voting"): payload,
        }).fetch_history(2024)


def test_a_missing_slice_raises():
    payload = load("VHCountsByCounty_pivot_2024_early.json")
    with pytest.raises(SchemaDrift):
        ok.decode(payload, ("NoSuchDimension",), (ok.MEASURE_HISTORY,))


# --------------------------------------------------------------------------
# The query shape -- the whole interface to this source
# --------------------------------------------------------------------------
def test_query_is_the_shape_the_dashboard_itself_sends():
    """Captured from the live page: a GET, one filter per dimension, values
    nested one level deep. A POST of the same body is what returned HTTP 500 and
    got Oklahoma written off in docs/coverage-research.md."""
    query = json.loads(ok.build_query([(ok._election_dimension(), STAMP_2024)]))
    assert query == {"Filter": [{
        "dimensions": [{
            "@ItemType": "Dimension", "@DataMember": "ElectionDate",
            "@DefaultId": "DataItem0", "@DateTimeGroupInterval": "DayMonthYear",
            "@SortOrder": "Descending",
        }],
        "values": [["2024-11-05T00:00:00.000"]],
    }]}


def test_election_lists_use_the_dashboards_own_date_spelling():
    """The filter wants `...T00:00:00.000`; the EncodeMap answers with
    `...T00:00:00.0000000`. Comparing the wrong one finds no election, ever."""
    dates = ok.dimension_values(load("VHCountsByCounty_dates.json"), ok.COL_ELECTION)
    assert ELECTION_2024.strftime(ok._ENCODED_STAMP) in dates
    assert ELECTION_2024.strftime(ok._FILTER_STAMP) not in dates


def test_the_two_dashboards_are_combined_when_both_carry_the_cycle():
    """Voter history says how many ballots came back and how; only the absentee
    table says how many went out. `mail_requested` is the one field taken from
    the other dashboard, and its party breakdown is deliberately not fetched --
    voter history already has a better one."""
    scraper = Offline()
    result = scraper.fetch(2024, date(2024, 11, 20))
    row = result.state_rows[0]
    assert (row.ballots_total, row.mail_returned, row.inperson) == (
        401_467, 107_549, 293_918)
    assert row.mail_requested == 130_640      # from AbsStatsByCounty
    assert row.party_dem == 113_521           # from VHCountsByCounty
    assert row.day == date(2024, 11, 20)      # neither dashboard carries a stamp
    party_queries = [c for c in scraper.calls
                     if c == (ok.ABSENTEE_DASHBOARD, ok.ABSENTEE_PARTY_COMBO)]
    assert party_queries == []


# --------------------------------------------------------------------------
# THE CERTIFIED ROW -- `fetch_history` with no PDF archive to walk
# --------------------------------------------------------------------------
@pytest.fixture(scope="module")
def history_2022():
    return Offline().fetch_history(2022)


@pytest.fixture(scope="module")
def history_2020():
    return Offline().fetch_history(2020)


def test_the_archive_reaches_2020_2022_and_2024(history_2020, history_2022,
                                                history_2024):
    """`VHCountsByCounty` is a permanent voter-history table, so the certified
    row exists for every past general, not just the last one. Each cycle is
    ONE row per county dated its own Election Day -- neither dashboard has a
    within-election date dimension; the daily curve comes from the PDF archive
    (next section), and with no PDFs indexed this is all there is."""
    for result, day in ((history_2020, ELECTION_2020),
                        (history_2022, ELECTION_2022),
                        (history_2024, ELECTION_2024)):
        assert len(result.state_rows) == 1
        assert len(result.county_rows) == ok.EXPECTED_COUNTIES == 77
        assert result.state_rows[0].day == day
        assert {row.day for row in result.county_rows} == {day}


def test_2022_reproduces_oklahomas_own_totals(history_2022):
    """204,082 early ballots for the 2022 general: 71,680 absentee plus 132,402
    early in person."""
    row = history_2022.state_rows[0]
    assert row.ballots_total == 204_082
    assert row.mail_returned == 71_680
    assert row.inperson == 132_402
    assert (row.party_dem, row.party_rep, row.party_npa, row.party_oth) == (
        76_096, 105_422, 21_816, 748)
    assert sum((row.party_dem, row.party_rep, row.party_npa, row.party_oth)) == 204_082


def test_2020_is_a_mail_MAJORITY_electorate(history_2020):
    """454,142 early ballots for the 2020 general: 287,010 absentee against
    167,132 early in person.

    ⚠️ THIS IS THE FIXTURE THAT CATCHES A SWAPPED METHOD FILTER. 2022 and 2024
    are both roughly two-to-one in-person, so reading `Early Voting` counts into
    `mail_returned` would still look like Oklahoma in either of them. 2020 runs
    the other way and nothing else in this file does.
    """
    row = history_2020.state_rows[0]
    assert row.ballots_total == 454_142
    assert row.mail_returned == 287_010
    assert row.inperson == 167_132
    assert row.mail_returned > row.inperson
    assert (row.party_dem, row.party_rep, row.party_npa, row.party_oth) == (
        187_464, 209_439, 55_153, 2_086)
    assert sum((row.party_dem, row.party_rep, row.party_npa, row.party_oth)) == 454_142


def test_archived_counties_are_keyed_by_fips_not_name(history_2020, history_2022):
    """Oklahoma County is 40109 and Tulsa County 40143 in both cycles; a name
    join would have to get "Oklahoma County, Oklahoma" right twice."""
    okc22 = [r for r in history_2022.county_rows if r.county_fips == "40109"][0]
    assert okc22.county_name == "Oklahoma County"
    assert (okc22.ballots_total, okc22.mail_returned, okc22.inperson) == (
        44_584, 23_594, 20_990)
    assert (okc22.party_dem, okc22.party_rep) == (19_827, 18_772)

    tulsa20 = [r for r in history_2020.county_rows if r.county_fips == "40143"][0]
    assert tulsa20.county_name == "Tulsa County"
    assert (tulsa20.ballots_total, tulsa20.mail_returned, tulsa20.inperson) == (
        74_988, 61_628, 13_360)


def test_archived_counties_sum_to_their_statewide_row(history_2020, history_2022):
    for result in (history_2020, history_2022):
        row = result.state_rows[0]
        assert sum(r.ballots_total for r in result.county_rows) == row.ballots_total
        assert sum(r.mail_returned for r in result.county_rows) == row.mail_returned
        assert sum(r.inperson for r in result.county_rows) == row.inperson
        assert sum(r.party_dem for r in result.county_rows) == row.party_dem


def test_the_archive_never_reports_mail_requested(history_2020, history_2022,
                                                  history_2024):
    """`AbsStatsByCounty` starts at 2022, so folding it into the certified row
    would give two cycles a column the third could never have -- and the daily
    PDF rows carry `Sent` for every day the mail report ran anyway. Blank, not
    zero: the voter-history table does not say how many ballots went out."""
    for result in (history_2020, history_2022, history_2024):
        assert result.state_rows[0].mail_requested is None


# --------------------------------------------------------------------------
# GUARD PARITY: `fetch` and `fetch_history` refuse the same emptiness
# --------------------------------------------------------------------------
def _uncredited() -> dict:
    """Patches making the voter-history dashboard LIST 2024 and credit nothing."""
    return {
        (ok.HISTORY_DASHBOARD, ok.HISTORY_PIVOT, 2024, method): zeroed(
            load(f"VHCountsByCounty_pivot_2024_{name}.json"))
        for method, name in (("Absentee", "absentee"), ("Early Voting", "early"))
    }


def test_an_election_the_history_dashboard_has_credited_nothing_to_is_absent():
    """⚠️ `fetch` has always refused an absentee table with nothing in it. The
    voter-history table can be empty in exactly the same way -- a general is
    listed in the date combo before credits are posted -- and `fetch_history`
    would have published 77 counties of zeroes dated Election Day, which is a
    confident zero no later tier can correct."""
    with pytest.raises(NotYetPublished) as caught:
        Offline(patch=_uncredited()).fetch_history(2024)
    assert "no early-vote credit" in str(caught.value)


def test_an_uncredited_history_falls_through_to_the_absentee_table():
    """The same emptiness, on the daily path. This is the week after an
    election: voter history lists the general and has credited nothing, while
    the absentee table has real returned ballots. Stopping here would be wrong,
    and so would publishing history's zeroes."""
    result = Offline(patch=_uncredited()).fetch(2024, date(2024, 11, 20))
    row = result.state_rows[0]
    assert row.mail_returned == 107_874        # AbsStatsByCounty's own count
    assert row.inperson is None                # not in that table at all
    assert row.ballots_total == 107_874


def test_a_partial_absentee_dashboard_contributes_no_statewide_total():
    """⚠️ `mail_requested` was walking straight past EXPECTED_COUNTIES. The two
    dashboards fill up independently -- the absentee one held six counties on
    2026-09-06 -- so a six-county sum could be hung on a 77-county statewide row
    and read as an Oklahoma total. Blank is the only honest answer."""
    scraper = Offline(patch={
        (ok.ABSENTEE_DASHBOARD, ok.ABSENTEE_PIVOT, 2024, None):
            first_counties(load("AbsStatsByCounty_pivot_2024.json"), 6),
    })
    result = scraper.fetch(2024, date(2024, 11, 20))
    row = result.state_rows[0]
    assert row.mail_requested is None
    # ...and nothing else is disturbed: the voter-history numbers still publish.
    assert (row.ballots_total, row.mail_returned, row.inperson) == (
        401_467, 107_549, 293_918)
    assert len(result.county_rows) == 77


# --------------------------------------------------------------------------
# THE PDF ARCHIVE -- the daily curve
# --------------------------------------------------------------------------
def pdf(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


@pytest.fixture(scope="module")
def mail_final() -> ok.Report:
    return ok.parse_report(pdf("ballot-totals-by-affiliation-20241104-1.pdf"))


@pytest.fixture(scope="module")
def inperson_final() -> ok.Report:
    return ok.parse_report(pdf("absentee-inperson-totals-20241104-1.pdf"))


@pytest.fixture(scope="module")
def inperson_wide() -> ok.Report:
    return ok.parse_report(pdf("absentee-inperson-totals-20241030-2.pdf"))


@pytest.fixture(scope="module")
def mail_pages() -> list[str]:
    return ok._pages(pdf("ballot-totals-by-affiliation-20241104-1.pdf"))


@pytest.fixture(scope="module")
def archive_2024():
    return Offline(archive=True).fetch_history(2024)


def _party(reading: dict) -> tuple[int, int, int, int]:
    """(rep, dem, npa, oth) -- the order the checkpoints are written in."""
    p = reading["party"]
    return p["rep"], p["dem"], p["npa"], p["oth"]


def test_the_mail_final_reproduces_the_state_page(mail_final):
    """11/4/2024 8:34 AM: 100,298 received (R 49,867 / D 36,112 / I 13,876 /
    L 443) of 130,644 sent -- the figures printed on page 78 of 78."""
    assert (mail_final.kind, mail_final.layout) == (ok.REPORT_MAIL, ok.LAYOUT_STANDARD)
    assert mail_final.run == datetime(2024, 11, 4, 8, 34)
    assert mail_final.election == ELECTION_2024
    assert mail_final.state["total"] == 100_298
    assert mail_final.state["sent"] == 130_644
    assert _party(mail_final.state) == (49_867, 36_112, 13_876, 443)
    assert len(mail_final.counties) == 77


def test_the_inperson_final_reproduces_the_state_page(inperson_final):
    """11/4/2024 8:32 AM: 292,684 (R 182,562 / D 74,183 / I 34,396 / L 1,543)."""
    assert (inperson_final.kind, inperson_final.layout) == (
        ok.REPORT_INPERSON, ok.LAYOUT_STANDARD)
    assert inperson_final.run == datetime(2024, 11, 4, 8, 32)
    assert inperson_final.state["total"] == 292_684
    assert inperson_final.state["sent"] is None       # not a mail report
    assert _party(inperson_final.state) == (182_562, 74_183, 34_396, 1_543)
    assert len(inperson_final.counties) == 77


def test_the_wide_layout_is_read_by_column_position(inperson_wide):
    """ap2680's three wide pages: a Grand Total row for the state, and a BLANK
    cell where a county has no Libertarian ballots. Beaver's row reads
    "161 10 4 175" -- four figures, and only the column positions say the
    missing one is Libertarian rather than Independent."""
    assert (inperson_wide.kind, inperson_wide.layout) == (ok.REPORT_INPERSON, ok.LAYOUT_WIDE)
    assert inperson_wide.state["total"] == 80_237
    assert _party(inperson_wide.state) == (51_873, 19_839, 8_150, 375)
    beaver = inperson_wide.counties["40007"]
    assert beaver["total"] == 175
    assert _party(beaver) == (161, 10, 4, 0)
    assert len(inperson_wide.counties) == 77


def test_a_report_is_dated_by_its_printed_run_stamp_not_its_filename(inperson_wide):
    """`absentee-inperson-totals-20241030-2.pdf` is the run of 10/31/2024
    8:00 AM. Dating it by the filename would put it a day early and, on 10/31,
    make it lose to the 4:28 PM run anyway -- quietly thinning the curve."""
    assert inperson_wide.run == datetime(2024, 10, 31, 8, 0)
    assert inperson_wide.run.date() == date(2024, 10, 31)


def test_a_zero_party_row_is_omitted_and_reads_as_zero(mail_final):
    """Adair's mail page prints Republican, Democrat and Independent and no
    Libertarian line at all. Oklahoma registers all four, the STATE page lists
    443 Libertarians, so the omission is a 0 -- the convention `fetch` uses off
    the absentee dashboard -- never a blank."""
    adair = mail_final.counties["40001"]
    assert set(adair["party"]) == set(ok.REGISTRATIONS)
    assert adair["party"]["oth"] == 0
    assert adair["total"] == 172 and adair["sent"] == 251
    assert mail_final.state["party"]["oth"] == 443


def test_counties_are_keyed_by_fips_through_the_census_table(mail_final):
    """LEFLORE, MCCLAIN and ROGER MILLS are printed UPPERCASE and unspaced;
    the join goes through `_fips.lookup`, never a name key."""
    assert mail_final.names["40079"] == "Le Flore County"
    assert mail_final.names["40087"] == "McClain County"
    assert mail_final.names["40129"] == "Roger Mills County"
    assert all(f.startswith("40") and len(f) == 5 for f in mail_final.counties)


def test_party_rows_must_add_up_to_the_countys_printed_total(mail_pages):
    """Each county block prints its own total line. A row the regexes failed
    to match would otherwise shrink the county without a trace."""
    pages = list(mail_pages)
    tampered, n = re.subn(r"(\n\s+251\s+0\s+)172\b", r"\g<1>173", pages[0])
    assert n == 1
    pages[0] = tampered
    with pytest.raises(SchemaDrift) as caught:
        ok.parse_pages(pages)
    assert "printed total is 173" in str(caught.value)


def test_county_pages_must_add_up_to_the_state_page(mail_pages):
    """The STATE page is what gets published; the 77 county pages are summed
    anyway and must agree with it."""
    pages = list(mail_pages)
    # One more Libertarian ballot on the STATE page, and a total to match, so
    # the page is consistent with itself and only the county sum disagrees.
    tampered, n = re.subn(r"(Libertarian\s+709\s+5\s+)443\b", r"\g<1>444", pages[-1])
    assert n == 1 and "100,298" in tampered
    pages[-1] = tampered.replace("100,298", "100,299")
    with pytest.raises(SchemaDrift) as caught:
        ok.parse_pages(pages)
    assert "STATE page" in str(caught.value)


def test_an_unknown_report_title_is_drift(mail_pages):
    """The printed title is the only identity a PDF has. A template this module
    has not been taught must raise, not be read as whichever shape fits."""
    pages = list(mail_pages)
    pages[0] = pages[0].replace("Ballot Totals by Affiliation", "Ballot Totals by Precinct")
    with pytest.raises(SchemaDrift) as caught:
        ok.parse_pages(pages)
    assert "unrecognised report title" in str(caught.value)


def test_a_page_from_a_different_run_is_drift(mail_pages):
    pages = list(mail_pages)
    pages[5] = pages[5].replace("11/4/2024 8:34 AM", "11/4/2024 9:00 AM")
    with pytest.raises(SchemaDrift) as caught:
        ok.parse_pages(pages)
    assert "page 6 is stamped" in str(caught.value)


def test_the_last_run_of_a_day_is_the_days_reading(mail_final):
    """11/1/2024 was run three times (7:54 AM, 4:33 PM, 11:37 PM). The latest
    run wins, per report type, whatever order the index lists them in."""
    morning = replace(mail_final, run=datetime(2024, 11, 1, 7, 54))
    evening = replace(mail_final, run=datetime(2024, 11, 1, 23, 37))
    afternoon = replace(mail_final, run=datetime(2024, 11, 1, 16, 33))
    latest = ok.last_run_per_day([evening, morning, afternoon, mail_final])
    assert latest[(ok.REPORT_MAIL, date(2024, 11, 1))] is evening
    assert latest[(ok.REPORT_MAIL, date(2024, 11, 4))] is mail_final
    assert len(latest) == 2


def test_a_mail_only_day_leaves_inperson_blank(mail_final):
    """Mail reports start before in-person voting opens. `inperson` is None --
    not 0 -- and `ballots_total` is the mail count, exactly what `fetch`
    publishes off the absentee dashboard while voter history is empty."""
    result = ok.archive_days([mail_final], 2024)
    row = result.state_rows[0]
    assert row.day == date(2024, 11, 4)
    assert (row.ballots_total, row.mail_returned, row.inperson) == (100_298, 100_298, None)
    assert row.mail_requested == 130_644
    assert (row.party_rep, row.party_dem, row.party_npa, row.party_oth) == (
        49_867, 36_112, 13_876, 443)
    assert all(r.inperson is None for r in result.county_rows)
    bands = {r.method for r in _methods.rows_of(result)}
    assert bands == {"mail"}
    assert len(_methods.rows_of(result)) == 77


def test_both_reports_combine_and_the_crosstab_rides_along(mail_final, inperson_final):
    """A day with both reports: the CountyDay is the sum, by party, and the
    MethodDay rows are the two bands it was summed from."""
    result = ok.archive_days([inperson_final, mail_final], 2024)
    row = result.state_rows[0]
    assert (row.ballots_total, row.mail_returned, row.inperson) == (392_982, 100_298, 292_684)
    assert row.mail_requested == 130_644
    assert row.party_rep == 49_867 + 182_562
    assert row.party_oth == 443 + 1_543
    assert len(result.county_rows) == 77
    methods = _methods.rows_of(result)
    assert len(methods) == 154
    by_county: dict[str, dict[str, int]] = {}
    for m in methods:
        by_county.setdefault(m.county_fips, {})[m.method] = m.ballots_total
    for county in result.county_rows:
        bands = by_county[county.county_fips]
        assert bands["mail"] + bands["inperson"] == county.ballots_total
        assert bands["mail"] == county.mail_returned
    assert all(m.party_oth is not None for m in methods)


def test_the_index_walk_takes_the_cycles_general_election_only():
    """The index carries 2026 primary and runoff files, three years of special
    elections and both 2022 and 2024 generals. Only the anchors under the
    cycle's own "General Election" heading are taken -- so a 2026 PDF is never
    picked up for 2024, and the still-empty 2026 general yields nothing."""
    page = (FIXTURES / INDEX_EXCERPT).read_text(encoding="utf-8")
    urls_2024 = ok.select_general_pdfs(page, 2024)
    assert len(urls_2024) == 17
    assert all("2024" in u and "general" in u for u in urls_2024)
    assert ok.select_general_pdfs(page, 2026) == []
    assert not any("2026" in u for u in urls_2024)
    assert len(ok.select_general_pdfs(page, 2022)) == 20
    assert all(u.startswith("https://oklahoma.gov/content/dam/") for u in urls_2024)


def test_the_cross_filed_files_are_read_by_title_not_folder():
    """The 2024 mail final sits in the in-person folder and the in-person
    final in the mail folder. Both are walked, both are identified by their
    printed title, and the day gets both readings."""
    page = (FIXTURES / INDEX_EXCERPT).read_text(encoding="utf-8")
    urls = ok.select_general_pdfs(page, 2024)
    crossed = [u for u in urls if u.endswith("ballot-totals-by-affiliation-20241104-1.pdf")]
    assert crossed and "absentee-in-person-totals" in crossed[0]
    crossed = [u for u in urls if u.endswith("absentee-inperson-totals-20241104-1.pdf")]
    assert crossed and "ballot-totals-by-affiliation/" in crossed[0]
    reports = Offline(archive=True)._archive_reports(2024)
    assert sorted((r.kind, r.run) for r in reports) == [
        (ok.REPORT_INPERSON, datetime(2024, 10, 31, 8, 0)),
        (ok.REPORT_INPERSON, datetime(2024, 11, 4, 8, 32)),
        (ok.REPORT_MAIL, datetime(2024, 11, 4, 8, 34)),
    ]


def test_fetch_history_walks_the_archive_and_ends_on_the_certified_row(archive_2024):
    """Three fixture PDFs give two archived days; voter history adds Election
    Day. Fourteen other indexed PDFs are Missing offline and are skipped."""
    days = [r.day for r in archive_2024.state_rows]
    assert days == [date(2024, 10, 31), date(2024, 11, 4), ELECTION_2024]
    oct31, nov4, nov5 = archive_2024.state_rows
    assert (oct31.ballots_total, oct31.mail_returned, oct31.inperson) == (80_237, None, 80_237)
    assert (nov4.ballots_total, nov4.mail_returned, nov4.inperson) == (392_982, 100_298, 292_684)
    assert nov4.mail_requested == 130_644
    assert (nov5.ballots_total, nov5.mail_returned, nov5.inperson) == (401_467, 107_549, 293_918)
    assert nov5.mail_requested is None
    assert len(archive_2024.county_rows) == 77 * 3
    methods = _methods.rows_of(archive_2024)
    assert len(methods) == 77 + 154
    assert not any(m.day == ELECTION_2024 for m in methods)


def test_a_pdf_reading_dated_election_day_yields_to_the_certified_row(mail_final):
    """2022's last runs were cut at 1:56 PM on Election Day. Published beside
    the certified row on the same key, the partial reading would WIN the merge
    -- it carries `mail_requested` and the certified row does not."""
    class ElectionDayRun(Offline):
        def _archive_reports(self, cycle):
            return [replace(mail_final, run=datetime(2024, 11, 5, 13, 56))]

    result = ElectionDayRun().fetch_history(2024)
    assert [r.day for r in result.state_rows] == [ELECTION_2024]
    row = result.state_rows[0]
    assert row.ballots_total == 401_467 and row.mail_requested is None
    assert len(result.county_rows) == 77
    assert _methods.rows_of(result) == []


def test_a_pdf_for_another_election_is_skipped_by_name():
    """A 2024 report filed under the 2022 heading is not drift -- it is
    mis-filed, and its printed `Election Date` says so."""
    class Misfiled(Offline):
        def _pdf(self, url):
            return pdf("ballot-totals-by-affiliation-20241104-1.pdf")

    scraper = Misfiled(archive=True)
    assert scraper._archive_reports(2022) == []
    result = scraper.fetch_history(2022)
    assert [r.day for r in result.state_rows] == [ELECTION_2022]


def test_a_cycle_still_in_progress_is_not_backfilled():
    with pytest.raises(NotYetPublished):
        Offline(archive=True).fetch_history(2026)


def test_archive_cache_names_are_unique_per_url():
    """Two folders can hold the same basename, so the cache name carries a
    digest of the whole URL."""
    a = ok.archive_filename("https://oklahoma.gov/a/ballot-totals-by-affiliation-20241104-1.pdf")
    b = ok.archive_filename("https://oklahoma.gov/b/ballot-totals-by-affiliation-20241104-1.pdf")
    assert a != b and a.endswith("ballot-totals-by-affiliation-20241104-1.pdf")
