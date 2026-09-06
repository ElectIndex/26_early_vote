"""Oklahoma: the State Election Board's DevExpress dashboards.

Every fixture is a real `DashboardItemGetAction` response, saved verbatim from
`stats.okelections.gov` on 2026-09-06 -- the compressed DSR shape and all. They
are what catches the two failure modes that matter for a JSON dashboard: a
renumbered `DataItemN` slot silently transposing measures onto the wrong
counties, and a new voting-method label quietly vanishing out of the total.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from ev.adapters import ok
from ev.adapters.base import NotYetPublished, SchemaDrift

FIXTURES = Path(__file__).parent / "fixtures" / "ok"

ELECTION_2024 = date(2024, 11, 5)
STAMP_2024 = ELECTION_2024.strftime(ok._FILTER_STAMP)

#: (dashboard, item, party-or-method filter value) -> fixture file.
_ROUTES = {
    (ok.HISTORY_DASHBOARD, ok.HISTORY_DATE_COMBO, None): "VHCountsByCounty_dates.json",
    (ok.HISTORY_DASHBOARD, ok.HISTORY_METHOD_PIE, None): "VHCountsByCounty_methods_2024.json",
    (ok.HISTORY_DASHBOARD, ok.HISTORY_PARTY_COMBO, None): "VHCountsByCounty_parties_2024.json",
    (ok.HISTORY_DASHBOARD, ok.HISTORY_PIVOT, "Absentee"): "VHCountsByCounty_pivot_2024_absentee.json",
    (ok.HISTORY_DASHBOARD, ok.HISTORY_PIVOT, "Early Voting"): "VHCountsByCounty_pivot_2024_early.json",
    (ok.ABSENTEE_DASHBOARD, ok.ABSENTEE_DATE_COMBO, None): "AbsStatsByCounty_dates.json",
    (ok.ABSENTEE_DASHBOARD, ok.ABSENTEE_PARTY_COMBO, None): "AbsStatsByCounty_parties_2024.json",
    (ok.ABSENTEE_DASHBOARD, ok.ABSENTEE_PIVOT, None): "AbsStatsByCounty_pivot_2024.json",
    (ok.ABSENTEE_DASHBOARD, ok.ABSENTEE_PIVOT, "Democrat"): "AbsStatsByCounty_pivot_2024_democrat.json",
    (ok.ABSENTEE_DASHBOARD, ok.ABSENTEE_PIVOT, "Republican"): "AbsStatsByCounty_pivot_2024_republican.json",
    (ok.ABSENTEE_DASHBOARD, ok.ABSENTEE_PIVOT, "Independent"): "AbsStatsByCounty_pivot_2024_independent.json",
    (ok.ABSENTEE_DASHBOARD, ok.ABSENTEE_PIVOT, "Libertarian"): "AbsStatsByCounty_pivot_2024_libertarian.json",
}


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


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
        selector = None
        for dimension, value in filters:
            if dimension["@DataMember"] in (ok.COL_METHOD, ok.COL_PARTY):
                selector = value
        key = (dashboard, item, selector)
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
        (ok.ABSENTEE_DASHBOARD, ok.ABSENTEE_PIVOT, None): empty,
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
            (ok.HISTORY_DASHBOARD, ok.HISTORY_PIVOT, "Early Voting"): payload,
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
