"""Kansas: the SoS advance-voting Power BI dashboard.

Four fixtures, one per step of the discovery chain, all captured live
2026-09-06:

* `advance-voting-data.html` -- the SoS page, whole and verbatim. It is where
  the report's embed token comes from, and the only thing that makes a rotated
  resource key survivable.
* `powerbi_view.html` -- the Power BI embed page, whole and verbatim. It names
  the `-redirect` cluster that refuses every API call, so it is what proves the
  `-api` substitution is needed rather than assumed.
* `powerbi_modelsAndExploration.json` -- the report's model, PRUNED to the keys
  discovery reads (`models[0].id`/`dbName`, `exploration.reportId`, and every
  visual's `config` verbatim) because the live response is 82 KB of feature
  flags. Every retained value is byte-for-byte what the API returned.
* `powerbi_querydata.json` -- one real, whole query response. Kept whole because
  the DSR wire format compresses each row against the one before it, so a
  truncated capture would decode differently from a real one.

**The querydata fixture is the AUGUST PRIMARY**, because on 2026-09-06 that is
what the dashboard held: the table is named for the cycle, not the election.
That makes it the most valuable fixture here -- it pins the one mistake that
would publish 202,231 primary advance ballots as the general's.
"""

from __future__ import annotations

import copy
import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from ev.adapters import ks
from ev.adapters.base import NotYetPublished, SchemaDrift
from ev.calendar import election_date

FIXTURES = Path(__file__).parent / "fixtures" / "ks"

#: The primary the fixture actually covers, and the general it must never be
#: mistaken for.
PRIMARY_DAY = date(2026, 8, 4)
GENERAL_DAY = election_date(2026)
SHIFT = (GENERAL_DAY - PRIMARY_DAY).days

#: The real series, verbatim off the wire: (date, sent, returned, in person).
REAL_SERIES = [
    (date(2026, 7, 15), 43492, 94, 0),
    (date(2026, 7, 16), 46127, 115, 714),
    (date(2026, 7, 17), 48266, 170, 1535),
    (date(2026, 7, 20), 50435, 356, 6582),
    (date(2026, 7, 21), 52157, 1872, 12387),
    (date(2026, 7, 22), 54038, 4630, 19954),
    (date(2026, 7, 23), 55513, 7846, 27106),
    (date(2026, 7, 24), 56749, 11141, 33806),
    (date(2026, 7, 27), 59885, 14445, 46313),
    (date(2026, 7, 28), 61749, 19308, 58033),
    (date(2026, 7, 29), 63230, 22230, 68815),
    (date(2026, 7, 30), 63641, 25792, 85019),
    (date(2026, 7, 31), 63833, 29547, 103148),
    (date(2026, 8, 1), 63918, 32912, 124959),
    (date(2026, 8, 3), 63921, 33744, 142988),
    (date(2026, 8, 4), 64013, 40446, 161785),
]


def _load(name: str):
    return json.loads((FIXTURES / name).read_text())


def _text(name: str) -> str:
    return (FIXTURES / name).read_text()


@pytest.fixture
def querydata():
    return _load("powerbi_querydata.json")


@pytest.fixture
def shifted(querydata):
    """The real series moved into the 2026 GENERAL's advance window.

    Kansas has not published the general's advance vote yet -- it cannot have,
    the window opens 2026-10-14 -- so the only way to exercise the publishing
    path against real numbers is to move real rows. Every count is untouched;
    only the epoch-millisecond date is shifted by the 91 days between primary
    day and Election Day. The assertions below check the fixture really is in
    the uncompressed shape this rewrite assumes, so a future recapture that
    compresses differently fails here rather than silently shifting nothing.
    """
    payload = copy.deepcopy(querydata)
    rows = payload["results"][0]["result"]["data"]["dsr"]["DS"][0]["PH"][0]["DM0"]
    for row in rows:
        assert "R" not in row and "Ø" not in row, "fixture is row-compressed"
        assert len(row["C"]) == 4, "fixture row is not four slots wide"
        row["C"][0] += SHIFT * 86400 * 1000
    return payload


# --------------------------------------------------------------------------
# The primary trap -- the reason this module exists in the shape it does
# --------------------------------------------------------------------------
def test_todays_real_response_is_the_primary_and_is_refused(querydata):
    """The live dashboard holds the August primary. It must STOP the ladder."""
    with pytest.raises(NotYetPublished) as excinfo:
        ks.parse(querydata, 2026, date(2026, 9, 6))
    message = str(excinfo.value)
    assert "2026-07-15..2026-08-04" in message
    assert "no rows inside the 2026 general's window" in message


def test_the_primary_is_refused_even_on_election_day(querydata):
    """A run in November must not pick up August's 202,231 ballots."""
    with pytest.raises(NotYetPublished):
        ks.parse(querydata, 2026, GENERAL_DAY)


def test_the_window_is_kansas_statutory_twenty_days():
    """K.S.A. 25-1122's 20 days, plus two of slack -- and never past Election Day."""
    opens, closes = ks.window(2026)
    assert closes == GENERAL_DAY
    assert (closes - opens).days == ks.WINDOW_DAYS
    assert ks.WINDOW_DAYS >= 20
    # The August primary's first day is 20 days before primary day, which is how
    # the statutory window was confirmed against real data.
    assert PRIMARY_DAY - timedelta(days=20) == REAL_SERIES[0][0]
    # ...and the primary series is nowhere near the general's window.
    assert REAL_SERIES[-1][0] < opens


# --------------------------------------------------------------------------
# Decoding
# --------------------------------------------------------------------------
def test_decode_recovers_the_whole_series(querydata):
    rows = ks.decode_dsr(querydata)
    assert len(rows) == len(REAL_SERIES)
    got = [
        (
            ks._day(r[ks.COL_DATE]),
            r[ks.MEASURE_SENT],
            r[ks.MEASURE_RETURNED],
            r[ks.MEASURE_INPERSON],
        )
        for r in rows
    ]
    assert got == REAL_SERIES


def test_slots_are_matched_by_name_not_position(querydata):
    """A reordered projection must not transpose sent onto in-person."""
    payload = copy.deepcopy(querydata)
    data = payload["results"][0]["result"]["data"]
    select = data["descriptor"]["Select"]
    # Swap the two measures' declared slots; the values on the wire do not move.
    sent = next(s for s in select if s["Name"].endswith(f"{ks.MEASURE_SENT})"))
    inperson = next(s for s in select if s["Name"].endswith(f"{ks.MEASURE_INPERSON})"))
    sent["Value"], inperson["Value"] = inperson["Value"], sent["Value"]

    rows = ks.decode_dsr(payload)
    # The last row's real values are sent=64,013 and in person=161,785. With the
    # descriptor swapped they must follow the NAMES, not the slot order.
    assert rows[-1][ks.MEASURE_SENT] == 161785
    assert rows[-1][ks.MEASURE_INPERSON] == 64013


def test_a_renamed_measure_raises_drift(querydata):
    payload = copy.deepcopy(querydata)
    for item in payload["results"][0]["result"]["data"]["descriptor"]["Select"]:
        if item["Name"].endswith(f"{ks.MEASURE_RETURNED})"):
            item["Name"] = "Sum(ADVANCE VOTE COUNTS 2026.BALLOTS BACK)"
    with pytest.raises(SchemaDrift) as excinfo:
        ks.decode_dsr(payload)
    assert ks.MEASURE_RETURNED in str(excinfo.value)


def test_a_response_that_is_not_a_dsr_raises_drift():
    with pytest.raises(SchemaDrift):
        ks.decode_dsr({"results": [{"result": {"data": {}}}]})


# --------------------------------------------------------------------------
# Publishing, against the real numbers moved into the general's window
# --------------------------------------------------------------------------
def test_the_general_window_publishes_the_whole_curve(shifted):
    result = ks.parse(shifted, 2026, GENERAL_DAY)
    assert len(result.state_rows) == len(REAL_SERIES)
    for row, (day, sent, returned, inperson) in zip(result.state_rows, REAL_SERIES):
        assert row.day == day + timedelta(days=SHIFT)
        assert row.mail_requested == sent
        assert row.mail_returned == returned
        assert row.inperson == inperson
        # Kansas's own headline: mail back plus in-person advance.
        assert row.ballots_total == returned + inperson


def test_the_final_total_is_kansas_own_headline(shifted):
    """202,231 is what the report's `Total Number of Ballots Voted` card shows."""
    result = ks.parse(shifted, 2026, GENERAL_DAY)
    assert result.state_rows[-1].ballots_total == 202231
    assert result.state_rows[-1].ballots_total == 40446 + 161785


def test_no_party_field_is_ever_populated(shifted):
    """Kansas registers by party; this dashboard does not report it. Never 0."""
    for row in ks.parse(shifted, 2026, GENERAL_DAY).state_rows:
        assert row.party_dem is None
        assert row.party_rep is None
        assert row.party_oth is None
        assert row.party_npa is None


def test_ballots_new_is_never_inferred(shifted):
    """The series skips weekends, so a snapshot difference is not a day's total."""
    for row in ks.parse(shifted, 2026, GENERAL_DAY).state_rows:
        assert row.ballots_new is None


def test_kansas_publishes_no_county_or_demographic_rows(shifted):
    """There is no geography and no demographic column in the model at all."""
    result = ks.parse(shifted, 2026, GENERAL_DAY)
    assert result.county_rows == []
    assert result.demo_rows == []


def test_rows_after_the_run_date_are_withheld(shifted):
    """A snapshot dated after the run must not be published under that run."""
    as_of = REAL_SERIES[3][0] + timedelta(days=SHIFT)
    result = ks.parse(shifted, 2026, as_of)
    assert [r.day for r in result.state_rows] == [
        d + timedelta(days=SHIFT) for d, *_ in REAL_SERIES[:4]
    ]


def test_before_the_first_report_is_not_yet_published(shifted):
    opens, _ = ks.window(2026)
    with pytest.raises(NotYetPublished) as excinfo:
        ks.parse(shifted, 2026, opens)
    assert "opens" in str(excinfo.value)


def test_a_zero_is_kept_and_a_blank_is_not(shifted):
    """In-person advance is a real 0 on day one; a missing cell is None."""
    first = ks.parse(shifted, 2026, GENERAL_DAY).state_rows[0]
    assert first.inperson == 0
    assert first.ballots_total == 94

    payload = copy.deepcopy(shifted)
    rows = payload["results"][0]["result"]["data"]["dsr"]["DS"][0]["PH"][0]["DM0"]
    # Null the in-person slot (index 3) on the first row via the Ø bitmask.
    rows[0]["C"] = rows[0]["C"][:3]
    rows[0]["Ø"] = 1 << 3
    blank = ks.parse(payload, 2026, GENERAL_DAY).state_rows[0]
    assert blank.inperson is None
    # Half a total is not a total: with in-person unreported, the advance total
    # is unknown, and 94 would look exactly like a real one.
    assert blank.ballots_total is None
    assert blank.mail_returned == 94


def test_two_rows_for_one_date_is_drift(shifted):
    payload = copy.deepcopy(shifted)
    rows = payload["results"][0]["result"]["data"]["dsr"]["DS"][0]["PH"][0]["DM0"]
    twin = copy.deepcopy(rows[1])
    twin["C"] = [rows[1]["C"][0], 1, 2, 3]
    rows.append(twin)
    with pytest.raises(SchemaDrift):
        ks.parse(payload, 2026, GENERAL_DAY)


# --------------------------------------------------------------------------
# Discovery
# --------------------------------------------------------------------------
def test_the_embed_token_is_read_off_the_sos_page():
    found = ks.embed(_text("advance-voting-data.html"))
    assert found is not None
    token, key = found
    assert token == ks.POWERBI_TOKEN
    assert key == ks.POWERBI_RESOURCE_KEY


def test_a_page_without_an_embed_falls_back_rather_than_raising():
    assert ks.embed("<html><body>no dashboard here</body></html>") is None


def test_a_token_whose_key_is_not_a_uuid_is_refused():
    import base64

    token = base64.b64encode(json.dumps({"k": "not-a-key"}).encode()).decode()
    assert ks.embed(f'<iframe src="https://app.powerbigov.us/view?r={token}">') is None


def test_the_cluster_is_the_api_host_not_the_redirect_one():
    """The embed page advertises a host that 403s every call. Verified live."""
    page = _text("powerbi_view.html")
    assert "wabi-us-gov-virginia-redirect" in page
    assert ks.cluster(page) == ks.POWERBI_CLUSTER
    assert "-redirect" not in ks.cluster(page)


def test_api_host_follows_the_pages_own_rule():
    """`getAPIMUrl()`: strip -redirect, strip global-, append -api."""
    assert ks.api_host("https://wabi-us-gov-iowa-redirect.analysis.usgovcloudapi.net/") \
        == "https://wabi-us-gov-iowa-api.analysis.usgovcloudapi.net"
    assert ks.api_host("https://wabi-global-north-europe.analysis.windows.net") \
        == "https://wabi-north-europe-api.analysis.windows.net"


def test_discovery_finds_the_advance_vote_table():
    models = ks.discover(_load("powerbi_modelsAndExploration.json"))
    assert len(models) == 1
    model = models[0]
    assert model == ks.FALLBACK_MODEL
    # The table name carries the cycle, which is exactly why it is discovered.
    assert model.entity == "ADVANCE VOTE COUNTS 2026"


def test_discovery_refuses_a_report_without_the_columns():
    payload = _load("powerbi_modelsAndExploration.json")
    payload["exploration"]["sections"] = [{"visualContainers": [
        {"config": json.dumps({"singleVisual": {"prototypeQuery": {
            "From": [{"Name": "a", "Entity": "SOMETHING ELSE", "Type": 0}],
            "Select": [{"Column": {"Expression": {"SourceRef": {"Source": "a"}},
                                   "Property": "NOPE"}, "Name": "x"}],
        }}})}
    ]}]
    with pytest.raises(SchemaDrift):
        ks.discover(payload)


def test_the_query_names_the_discovered_entity():
    model = ks.discover(_load("powerbi_modelsAndExploration.json"))[0]
    body = ks.build_query(model)
    command = body["queries"][0]["Query"]["Commands"][0]["SemanticQueryDataShapeCommand"]
    assert command["Query"]["From"] == [
        {"Name": "a", "Entity": model.entity, "Type": 0}
    ]
    asked = {
        item.get("Column", {}).get("Property")
        or item["Aggregation"]["Expression"]["Column"]["Property"]
        for item in command["Query"]["Select"]
    }
    assert asked == set(ks.REQUIRED)
    assert body["modelId"] == model.model_id
    assert body["queries"][0]["ApplicationContext"]["DatasetId"] == model.dataset_id


# --------------------------------------------------------------------------
# The adapter's contract
# --------------------------------------------------------------------------
def test_history_is_honest_about_having_no_archive():
    for cycle in (2022, 2024):
        with pytest.raises(NotYetPublished):
            ks.KSScraper().fetch_history(cycle)


def test_the_adapter_is_registered_as_tier_one():
    from ev.schema import TIER_SCRAPER

    scraper = ks.KSScraper()
    assert scraper.state == "KS"
    assert scraper.name == "ks-sos"
    assert scraper.tier == TIER_SCRAPER


# --------------------------------------------------------------------------
# WHY KANSAS HAS NO COUNTY ROWS: THE SOURCE, NOT THE ADAPTER
#
# `test_kansas_publishes_no_county_or_demographic_rows` asserts what this module
# emits, which proves nothing about what Kansas publishes. This is the evidence.
# `powerbi_conceptualschema.json` is the verbatim 7,256-byte body of
#
#     POST https://wabi-us-gov-virginia-api.analysis.usgovcloudapi.net
#          /public/reports/conceptualschema
#          {"modelIds": [2023851], "userPreferredLocale": "en-US"}
#          X-PowerBI-ResourceKey: de3b6e3c-b94e-419a-8d9c-9435eba72780
#
# fetched live and answering HTTP 200. It is the WHOLE dataset behind the
# dashboard -- not the subset the visuals happen to bind -- so if a county
# column existed anywhere in Kansas's advance-vote model, it would be in here.
# (Note the path carries NO resource key: the keyed form 405s.)
# --------------------------------------------------------------------------
def _schema_entities() -> dict[str, list[str]]:
    payload = _load("powerbi_conceptualschema.json")
    return {
        entity["Name"]: [p["Name"] for p in entity["Properties"]]
        for schema in payload["schemas"]
        for entity in schema["schema"]["Entities"]
    }


def test_the_whole_kansas_model_is_one_table_of_six_fields():
    entities = _schema_entities()
    assert entities["ADVANCE VOTE COUNTS 2026"] == [
        "DATE",
        "ADVANCE VOTING BALLOTS SENT",
        "ADVANCE VOTING BALLOTS RETURNED",
        "IN PERSON ADVANCE",
        "Percentage of Total Ballots Returned",   # a measure, not read per row
        "Total Number of Ballots Voted",          # a whole-table aggregate
    ]
    # Everything else in the model is Power BI's own date scaffolding.
    others = [name for name in entities if name != "ADVANCE VOTE COUNTS 2026"]
    assert all(name.startswith(("DateTableTemplate_", "LocalDateTable_"))
               for name in others), others


def test_no_geography_of_any_kind_exists_in_the_kansas_model():
    """105 counties, and not one of them is nameable from this source. This is
    what makes Kansas statewide-only -- the same fact South Dakota and Alaska
    are tracked under -- and it is a property of the dashboard, not a choice."""
    every_name = [
        name
        for entity, props in _schema_entities().items()
        for name in [entity, *props]
    ]
    for word in ("COUNTY", "PRECINCT", "DISTRICT", "CITY", "TOWNSHIP",
                 "REGION", "FIPS", "PARTY"):
        assert not any(word in name.upper() for name in every_name), word


def test_the_conceptualschema_and_the_visuals_agree():
    """The report's four visuals bind the same one table the dataset holds, so
    there is no hidden column a different report page could reach."""
    bound: dict[str, set[str]] = {}
    exploration = _load("powerbi_modelsAndExploration.json")["exploration"]
    for query in ks._visual_queries(exploration):
        for entity, props in ks._entity_columns(query).items():
            bound.setdefault(entity, set()).update(props)
    assert set(bound) == {"ADVANCE VOTE COUNTS 2026"}
    assert bound["ADVANCE VOTE COUNTS 2026"] <= set(
        _schema_entities()["ADVANCE VOTE COUNTS 2026"]
    )
