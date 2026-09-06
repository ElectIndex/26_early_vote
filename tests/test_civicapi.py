"""Parse tests for the tier-2 civicAPI adapter.

Every fixture is a real, unedited response captured on 2026-09-06, kept whole
rather than truncated because the interesting assertions are about the SHAPE of
the whole payload -- that all 100 North Carolina regions resolve to counties and
all 108 Illinois ones do not, that Illinois' party split is a single
"Unspecified" pile, that a state civicAPI does not carry answers with an `error`
object rather than a 404. Between them they cover every trap documented at the
top of civicapi.py, which is the point: these files are the only thing that will
notice if the API changes shape mid-October.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from ev.adapters.base import NotYetPublished, SchemaDrift, SourceError
from ev.adapters.civicapi import CivicApiAdapter, clear_cache
from ev.registry import ladder
from ev.schema import TIER_AGGREGATOR, TIER_CIVIC, TIER_MANUAL, TIER_SCRAPER

FIXTURES = Path(__file__).parent / "fixtures" / "civicapi"
AS_OF = date(2026, 9, 6)


@pytest.fixture(autouse=True)
def _no_cross_test_cache():
    clear_cache()
    yield
    clear_cache()


def adapter(state: str, fixture_dir: Path = FIXTURES) -> CivicApiAdapter:
    return CivicApiAdapter(state=state, fixture_dir=fixture_dir)


def fetch(state: str, cycle: int = 2026, as_of: date = AS_OF):
    return adapter(state).fetch(cycle, as_of)


def only_state_row(result):
    assert len(result.state_rows) == 1
    return result.state_rows[0]


def rewritten(tmp_path: Path, name: str, mutate) -> Path:
    """A copy of the whole fixture set with one file edited by `mutate`."""
    out = tmp_path / "civicapi"
    out.mkdir()
    for src in FIXTURES.iterdir():
        payload = json.loads(src.read_text())
        if src.name == name:
            mutate(payload)
        (out / src.name).write_text(json.dumps(payload))
    return out


# ---------------------------------------------------------------------------
# Where it sits in the ladder
# ---------------------------------------------------------------------------
def test_sits_between_the_state_scraper_and_the_aggregator():
    assert TIER_SCRAPER < TIER_CIVIC < TIER_AGGREGATOR < TIER_MANUAL


def test_every_state_walks_scraper_then_civicapi_then_aggregator_then_manual():
    names = [a.name for a in ladder("NC")]
    assert names.index("civicapi") < names.index("uf-election-lab") < names.index("manual")
    # And a state with no tier-1 scraper still gets the three fallbacks in order.
    assert [a.name for a in ladder("AL")] == ["civicapi", "uf-election-lab", "manual"]


def test_declares_its_tier_and_name():
    a = adapter("NC")
    assert (a.tier, a.name) == (TIER_CIVIC, "civicapi")
    assert a.provenance().tier == TIER_CIVIC


# ---------------------------------------------------------------------------
# The statewide row
# ---------------------------------------------------------------------------
def test_north_carolina_statewide_row():
    row = only_state_row(fetch("NC"))
    assert row.state == "NC"
    assert row.day == date(2026, 9, 6)
    assert row.mail_requested == 49152
    assert row.mail_returned == 10
    assert row.ballots_total == 10
    assert (row.party_dem, row.party_rep, row.party_npa) == (6, 1, 3)


def test_ballots_total_is_returned_plus_inperson_and_never_the_requests():
    row = only_state_row(fetch("NC"))
    # 49,152 mail ballots have been REQUESTED and 10 returned. A request is not
    # a vote, and conflating them would overstate the state by 4,915x.
    assert row.ballots_total == row.mail_returned
    assert row.mail_requested > row.ballots_total


def test_zero_is_written_as_blank_not_as_zero():
    """TRAP 1. Nobody has voted in person in NC yet; the API says 0, we say blank."""
    row = only_state_row(fetch("NC"))
    assert row.inperson is None
    # Likewise the party bucket that stands at 0 -- "Other" -- gets no number.
    assert row.party_oth is None


def test_a_state_with_only_requests_still_publishes_them():
    """Illinois has 947,926 mail requests and nothing cast. Both facts survive."""
    row = only_state_row(fetch("IL"))
    assert row.mail_requested == 947926
    assert row.ballots_total is None
    assert row.mail_returned is None and row.inperson is None


def test_no_party_registration_state_gets_four_blank_party_columns():
    """TRAP 2. Illinois' split is one 'Unspecified' pile; none of it is a party."""
    row = only_state_row(fetch("IL"))
    assert (row.party_dem, row.party_rep, row.party_npa, row.party_oth) == (
        None, None, None, None)


def test_unspecified_is_never_folded_into_a_party_bucket():
    """The 947,926 must not reappear under npa/oth by another route."""
    row = only_state_row(fetch("IL"))
    assert row.mail_requested == 947926
    assert not any(v == 947926 for v in (
        row.party_dem, row.party_rep, row.party_npa, row.party_oth))


def test_a_state_civicapi_does_not_carry_falls_through_to_the_aggregator():
    """Not NotYetPublished: a hole in OUR source is not evidence of no votes."""
    with pytest.raises(SourceError) as exc:
        fetch("GA")
    assert "does not carry GA" in str(exc.value)


def test_a_snapshot_dated_after_as_of_is_not_yet_published():
    with pytest.raises(NotYetPublished):
        fetch("NC", as_of=date(2026, 9, 1))


def test_a_past_cycle_is_a_source_error_not_not_yet_published():
    """TRAP 5. The API answers a 2024 question with 2026 numbers.

    SourceError so the ladder falls THROUGH to UF, which does keep a 2024 file.
    NotYetPublished here would stop the walk and take that answer with it.
    """
    with pytest.raises(SourceError) as exc:
        fetch("NC", cycle=2024, as_of=date(2024, 11, 5))
    assert "no archive" in str(exc.value)


def test_fetch_history_declines_without_blocking_the_tier_below():
    with pytest.raises(NotYetPublished):
        adapter("NC").fetch_history(2024)


def test_a_state_reporting_nothing_at_all_stops_the_walk(tmp_path):
    def zero_everything(payload):
        for bucket in payload["statewide_total"].values():
            bucket["votes"] = 0

    out = tmp_path / "allzero"
    out.mkdir()
    for src in FIXTURES.iterdir():
        payload = json.loads(src.read_text())
        if src.name.startswith("civicapi_NC_") and "statewide_total" in payload:
            zero_everything(payload)
        (out / src.name).write_text(json.dumps(payload))

    clear_cache()
    with pytest.raises(NotYetPublished):
        CivicApiAdapter("NC", fixture_dir=out).fetch(2026, AS_OF)


# ---------------------------------------------------------------------------
# County rows
# ---------------------------------------------------------------------------
def test_counties_are_keyed_by_fips_and_carry_the_census_name():
    result = fetch("NC")
    by_fips = {c.county_fips: c for c in result.county_rows}
    assert by_fips["37119"].county_name == "Mecklenburg County"
    assert by_fips["37119"].ballots_total == 3
    assert (by_fips["37119"].party_dem, by_fips["37119"].party_rep) == (1, 1)


def test_county_rows_sum_to_the_statewide_row():
    result = fetch("NC")
    row = only_state_row(result)
    assert sum(c.ballots_total for c in result.county_rows) == row.ballots_total


def test_a_county_that_reported_nothing_gets_no_row_at_all():
    """94 of North Carolina's 100 counties are still at zero; none is published."""
    result = fetch("NC")
    assert len(result.county_rows) == 6
    assert all(c.ballots_total for c in result.county_rows)


def test_illinois_county_layer_is_withheld_entirely(caplog):
    """THE ILLINOIS RULE.

    Six of the 108 region names are city election boards, not counties -- and
    "City of Chicago" holds 382,508 of the state's mail requests against a "Cook"
    row that is suburban Cook only. Dropping the six would understate Cook by
    40%, so the whole county layer goes rather than a wrong one.
    """
    result = fetch("IL")
    assert result.county_rows == []
    assert result.state_rows, "the statewide row must survive"
    assert "City of Chicago" in caplog.text


def test_illinois_regions_really_do_contain_the_six_city_boards():
    """Pins the reason the rule above fires, so a fixture refresh cannot hide it."""
    payload = json.loads((FIXTURES / "civicapi_IL_requested.json").read_text())
    cities = sorted(r for r in payload["regions"] if r.lower().startswith("city of"))
    assert cities == [
        "City of Bloomington", "City of Chicago", "City of Danville",
        "City of East St Louis", "City of Galesburg", "City of Rockford",
    ]


def test_a_county_holding_only_requests_produces_no_row():
    """Florida's one live number is a mail REQUEST, and CountyDay has no such column."""
    result = fetch("FL")
    assert only_state_row(result).mail_requested == 1
    assert result.county_rows == []


def test_an_unknown_county_name_withholds_the_layer_rather_than_dropping_it(tmp_path):
    out = rewritten(
        tmp_path, "civicapi_NC_returned.json",
        lambda p: p["regions"].update({"Nonesuch": p["regions"].pop("Wake")}),
    )
    clear_cache()
    result = CivicApiAdapter("NC", fixture_dir=out).fetch(2026, AS_OF)
    assert result.county_rows == []
    assert result.state_rows


# ---------------------------------------------------------------------------
# Demographics
# ---------------------------------------------------------------------------
def test_sex_rows_use_the_shared_vocabulary_and_sum_to_the_ballots_cast():
    result = fetch("NC")
    rows = {r.bucket: r.ballots_total for r in result.demo_rows if r.dimension == "sex"}
    assert set(rows) <= {"female", "male", "unknown"}
    assert sum(rows.values()) == only_state_row(result).ballots_total


def test_age_rows_are_never_emitted():
    """TRAP 3. civicAPI's 18-25/26-40/41-65/65+ do not map onto our six bands."""
    assert not [r for r in fetch("NC").demo_rows if r.dimension == "age"]


def test_race_rows_are_never_emitted():
    """TRAP 4. Its 'White' includes Hispanic voters; every tier-1 adapter's does not."""
    assert not [r for r in fetch("NC").demo_rows if r.dimension == "race"]


def test_no_demographics_for_a_state_that_does_not_report_them():
    assert fetch("IL").demo_rows == []
    assert fetch("FL").demo_rows == []


# ---------------------------------------------------------------------------
# Drift
# ---------------------------------------------------------------------------
def test_a_party_label_we_do_not_know_raises_schema_drift(tmp_path):
    out = rewritten(
        tmp_path, "civicapi_NC_returned.json",
        lambda p: p["statewide_total"].update(
            {"Whig": {"votes": 4, "color": "#000"}}),
    )
    clear_cache()
    with pytest.raises(SchemaDrift, match="Whig"):
        CivicApiAdapter("NC", fixture_dir=out).fetch(2026, AS_OF)


def test_unspecified_alongside_real_parties_raises_rather_than_guessing(tmp_path):
    """Capabilities says NC reports party; an 'Unspecified' pile contradicts that."""
    out = rewritten(
        tmp_path, "civicapi_NC_returned.json",
        lambda p: p["statewide_total"].update(
            {"Unspecified": {"votes": 900, "color": "#999"}}),
    )
    clear_cache()
    with pytest.raises(SchemaDrift, match="Unspecified"):
        CivicApiAdapter("NC", fixture_dir=out).fetch(2026, AS_OF)


def test_a_non_numeric_count_raises_schema_drift(tmp_path):
    out = rewritten(
        tmp_path, "civicapi_NC_returned.json",
        lambda p: p["statewide_total"]["Democratic"].update({"votes": "six"}),
    )
    clear_cache()
    with pytest.raises(SchemaDrift):
        CivicApiAdapter("NC", fixture_dir=out).fetch(2026, AS_OF)


def test_a_missing_statewide_total_raises_schema_drift(tmp_path):
    out = rewritten(
        tmp_path, "civicapi_NC_returned.json",
        lambda p: p.pop("statewide_total"),
    )
    clear_cache()
    with pytest.raises(SchemaDrift, match="statewide_total"):
        CivicApiAdapter("NC", fixture_dir=out).fetch(2026, AS_OF)


def test_a_missing_snapshot_date_raises_schema_drift(tmp_path):
    out = rewritten(
        tmp_path, "civicapi_NC_returned.json",
        lambda p: p.pop("snapshot_date"),
    )
    clear_cache()
    with pytest.raises(SchemaDrift, match="snapshot_date"):
        CivicApiAdapter("NC", fixture_dir=out).fetch(2026, AS_OF)


def test_capabilities_without_a_provides_object_raises_schema_drift(tmp_path):
    out = rewritten(
        tmp_path, "civicapi_NC_capabilities.json", lambda p: p.pop("provides"),
    )
    clear_cache()
    with pytest.raises(SchemaDrift, match="provides"):
        CivicApiAdapter("NC", fixture_dir=out).fetch(2026, AS_OF)


def test_an_unreadable_source_is_a_source_error(tmp_path):
    clear_cache()
    with pytest.raises(SourceError):
        CivicApiAdapter("NC", fixture_dir=tmp_path / "nowhere").fetch(2026, AS_OF)


def test_each_endpoint_is_fetched_once_per_run_not_once_per_call(monkeypatch):
    """One ingest can touch a state's ladder more than once; the API should not
    see the same GET twice for it."""
    reads = []
    real = Path.read_text

    def counting(self, *args, **kwargs):
        reads.append(self.name)
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", counting)
    a = adapter("NC")
    a.fetch(2026, AS_OF)
    first = len(reads)
    a.fetch(2026, AS_OF)
    assert len(reads) == first, "second fetch re-read the payloads"


# ---------------------------------------------------------------------------
# The blank rule, stated once more where it matters most
# ---------------------------------------------------------------------------
def test_no_row_anywhere_carries_a_zero():
    """A published 0 asserts "zero ballots"; this source cannot assert that."""
    for state in ("NC", "IL", "FL"):
        clear_cache()
        result = fetch(state)
        for bucket in (result.state_rows, result.county_rows, result.demo_rows):
            for row in bucket:
                zeros = [
                    f for f, v in vars(row).items()
                    if isinstance(v, int) and not isinstance(v, bool)
                    and v == 0 and f not in ("restated",)
                ]
                assert not zeros, f"{state} {row} carries zeros in {zeros}"
