"""Oklahoma: the State Election Board's DevExpress dashboards.

Every fixture is a real `DashboardItemGetAction` response, saved verbatim from
`stats.okelections.gov` -- the compressed DSR shape and all. The 2024 set was
captured 2026-09-06; the 2020 and 2022 voter-history sets 2026-09-08, when the
archive route was built. They are what catches the two failure modes that matter
for a JSON dashboard: a renumbered `DataItemN` slot silently transposing
measures onto the wrong counties, and a new voting-method label quietly
vanishing out of the total.

Three cycles of the SAME dashboard is not redundancy. `VHCountsByCounty` is the
only Oklahoma archive there is, `fetch_history` reads a different election out of
it for each cycle, and the three differ in the one way that matters: 2020 is a
mail-majority electorate (287,010 absentee against 167,132 early in person) and
2022 and 2024 are in-person-majority ones, so a bug that swapped the two method
filters would still look plausible against 2024 alone.
"""

from __future__ import annotations

import copy
import json
from datetime import date
from pathlib import Path

import pytest

from ev.adapters import ok
from ev.adapters.base import NotYetPublished, SchemaDrift

FIXTURES = Path(__file__).parent / "fixtures" / "ok"

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
    """The real adapter with only its one HTTP seam replaced."""

    def __init__(self, *, history=True, absentee=True, patch=None):
        super().__init__()
        self.history_on = history
        self.absentee_on = absentee
        self.patch = patch or {}
        self.calls: list[tuple] = []

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
# THE ARCHIVE ROUTE -- `fetch_history`, one Election-Day row per cycle
# --------------------------------------------------------------------------
@pytest.fixture(scope="module")
def history_2022():
    return Offline().fetch_history(2022)


@pytest.fixture(scope="module")
def history_2020():
    return Offline().fetch_history(2020)


def test_the_archive_reaches_2020_2022_and_2024(history_2020, history_2022,
                                                history_2024):
    """`VHCountsByCounty` is a permanent voter-history table, so the archive is
    every past general, not just the last one. Each cycle is ONE row per county
    dated its own Election Day -- neither dashboard has a within-election date
    dimension, so there is no daily curve to be had."""
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
    """`AbsStatsByCounty` starts at 2022, so folding it into the archive would
    give two cycles a column the third could never have. Blank, not zero: the
    voter-history table does not say how many ballots went out."""
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
