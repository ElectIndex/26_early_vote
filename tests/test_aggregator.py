"""Parse tests for the tier-2 UF Election Lab adapter.

Both fixtures are real downloads with their headers kept verbatim -- the 2026
file as it stood on 2026-09-05 (one state reporting, the rest all zeros) and the
2024 file as the tracker left it on election night. Between them they cover every
trap documented at the top of aggregator.py, which is the point: these fixtures
are the only thing that will notice if UF renames a column mid-October.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from ev.adapters.aggregator import AggregatorAdapter, clear_cache
from ev.adapters.base import NotYetPublished, SchemaDrift, SourceError
from ev.schema import TIER_AGGREGATOR

FIXTURES = Path(__file__).parent / "fixtures" / "aggregator"
US_2026 = FIXTURES / "US_2026.csv"
US_2024 = FIXTURES / "US_2024.csv"


@pytest.fixture(autouse=True)
def _no_cross_test_cache():
    clear_cache()
    yield
    clear_cache()


def adapter(state: str, path: Path = US_2026) -> AggregatorAdapter:
    return AggregatorAdapter(state=state, path=path)


def only_state_row(result):
    assert len(result.state_rows) == 1
    return result.state_rows[0]


# ---------------------------------------------------------------------------
# Registration in the ladder
# ---------------------------------------------------------------------------
def test_declares_the_aggregator_tier():
    a = adapter("NC")
    assert a.tier == TIER_AGGREGATOR
    assert a.name == "uf-election-lab"
    assert a.provenance().tier == TIER_AGGREGATOR


def test_registry_resolves_it_for_every_state():
    from ev.registry import ladder

    tiers = [(x.tier, x.name) for x in ladder("WY")]
    assert (TIER_AGGREGATOR, "uf-election-lab") in tiers
    # And it sorts between the scraper and the manual file.
    assert tiers == sorted(tiers)


# ---------------------------------------------------------------------------
# 2026: the state of the world on 2026-09-05
# ---------------------------------------------------------------------------
def test_2026_north_carolina_parses_its_ten_accepted_ballots():
    result = adapter("NC").fetch(2026, date(2026, 9, 5))
    row = only_state_row(result)
    assert row.day == date(2026, 9, 5)  # the row's own last_update, not "today"
    assert row.ballots_total == 10
    assert row.mail_returned == 10
    assert row.mail_requested == 49137
    assert row.party_dem == 6
    assert row.party_rep == 1
    assert row.party_npa == 3


def test_zero_is_written_as_blank_not_as_zero():
    """THE BLANK RULE against trap 1: NC has cast no in-person ballots yet."""
    row = only_state_row(adapter("NC").fetch(2026, date(2026, 9, 5)))
    assert row.inperson is None
    # `party_oth` has no source column at all -- never a fabricated 0.
    assert row.party_oth is None
    assert row.ballots_new is None


def test_all_zero_state_is_not_yet_published():
    """Georgia's row exists and is entirely zeros: voting has not opened."""
    with pytest.raises(NotYetPublished) as exc:
        adapter("GA").fetch(2026, date(2026, 9, 5))
    assert "no ballots cast" in str(exc.value)


def test_requests_without_any_ballots_cast_is_still_not_yet_published():
    """Colorado shows 3.98M ballots 'requested' (its whole active roll) and zero
    cast. Publishing that row would put a state on the board that has not voted."""
    with pytest.raises(NotYetPublished):
        adapter("CO").fetch(2026, date(2026, 9, 5))


def test_a_state_the_aggregator_does_not_carry_falls_through():
    """Alabama and New Hampshire are absent from the file (49 rows, not 51).

    This must be SourceError, not NotYetPublished, or the ladder would stop here
    and the hand-entered tier 3 would never get to answer for those two states.
    """
    with pytest.raises(SourceError) as exc:
        adapter("AL").fetch(2026, date(2026, 9, 5))
    assert not isinstance(exc.value, NotYetPublished)
    assert "no row for AL" in str(exc.value)


def test_a_row_dated_after_as_of_is_not_yet_published():
    with pytest.raises(NotYetPublished):
        adapter("NC").fetch(2026, date(2026, 9, 4))


# ---------------------------------------------------------------------------
# 2024: the full-season shape, which is what most of the parser only sees in Oct
# ---------------------------------------------------------------------------
def test_2024_north_carolina_full_row():
    result = adapter("NC", US_2024).fetch(2024, date(2024, 11, 6))
    row = only_state_row(result)
    assert row.day == date(2024, 11, 5)
    assert row.ballots_total == 4481606
    assert row.mail_requested == 463899
    assert row.mail_returned == 266728
    assert row.inperson == 4214878
    assert (row.party_dem, row.party_rep, row.party_npa) == (1451660, 1488826, 1541120)


def test_no_party_registration_state_gets_blank_party_columns():
    """Georgia does not register by party, so UF's party columns are 0 there.

    Writing 0 would render on the site as 'no Democrat has voted in Georgia'.
    """
    row = only_state_row(adapter("GA", US_2024).fetch(2024, date(2024, 11, 6)))
    assert row.ballots_total == 4031303
    assert row.party_dem is None
    assert row.party_rep is None
    assert row.party_npa is None


def test_all_mail_state_reports_no_inperson():
    row = only_state_row(adapter("OR", US_2024).fetch(2024, date(2024, 11, 6)))
    assert row.ballots_total == 1723085
    assert row.inperson is None


def test_out_of_cycle_last_update_is_a_source_error():
    """Montana's 2024 row is really dated 11/4/2023 in the published file.

    Taking that at face value would put the row 1,095 days from the election.
    """
    with pytest.raises(SourceError) as exc:
        adapter("MT", US_2024).fetch(2024, date(2024, 11, 6))
    assert not isinstance(exc.value, NotYetPublished)
    assert "outside cycle" in str(exc.value)


# ---------------------------------------------------------------------------
# Demographics: race and sex yes, age never
# ---------------------------------------------------------------------------
def test_race_and_sex_rows_use_the_shared_vocabulary():
    result = adapter("NC", US_2024).fetch(2024, date(2024, 11, 6))
    by_dim = {}
    for row in result.demo_rows:
        by_dim.setdefault(row.dimension, {})[row.bucket] = row.ballots_total

    assert set(by_dim) == {"race", "sex"}
    assert by_dim["race"] == {
        "white": 3068162, "black": 794157, "hispanic": 127609,
        "asian": 72596, "native": 24449, "other": 394201,
    }
    assert by_dim["sex"] == {"female": 2316713, "male": 1844036, "unknown": 320850}


def test_age_rows_are_never_emitted():
    """Trap 2. UF bands are 18-25/26-40/41-65/65+; ours are six different bands.

    normalize.age_band() would happily map "41-65" onto our "35-44" bucket, so
    the only safe number of age rows from this source is zero.
    """
    for path, cycle in ((US_2024, 2024), (US_2026, 2026)):
        result = AggregatorAdapter(state="NC", path=path).fetch(
            cycle, date(cycle, 11, 6)
        )
        assert [r for r in result.demo_rows if r.dimension == "age"] == []
        clear_cache()


def test_unreported_demographic_bucket_produces_no_row_at_all():
    """Georgia reports race but Colorado does not; a blank bucket is omitted,
    never emitted with a 0."""
    result = adapter("CO", US_2024).fetch(2024, date(2024, 11, 6))
    assert [r for r in result.demo_rows if r.dimension == "race"] == []
    # ...while the sex breakdown it does publish comes through.
    assert {r.bucket for r in result.demo_rows} == {"female", "male", "unknown"}


# ---------------------------------------------------------------------------
# Drift and failure modes
# ---------------------------------------------------------------------------
def test_missing_column_raises_schema_drift(tmp_path):
    text = US_2026.read_text(encoding="utf-8")
    header, *rest = text.splitlines()
    broken = tmp_path / "renamed.csv"
    broken.write_text(
        "\n".join([header.replace("voted_all", "total_voted"), *rest]) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(SchemaDrift) as exc:
        AggregatorAdapter(state="NC", path=broken).fetch(2026, date(2026, 9, 5))
    assert "voted_all" in str(exc.value)


def test_missing_demographic_column_raises_schema_drift(tmp_path):
    text = US_2024.read_text(encoding="utf-8")
    header, *rest = text.splitlines()
    broken = tmp_path / "no_race.csv"
    broken.write_text(
        "\n".join([header.replace("voted_nh_black", "voted_black"), *rest]) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(SchemaDrift):
        AggregatorAdapter(state="NC", path=broken).fetch(2024, date(2024, 11, 6))


def test_non_numeric_count_raises_schema_drift(tmp_path):
    lines = US_2026.read_text(encoding="utf-8").splitlines()
    patched = [
        line.replace(",10,6,1,3,", ",n/a,6,1,3,") if line.startswith("North Carolina")
        else line
        for line in lines
    ]
    assert patched != lines, "fixture changed; the NC counts no longer match"
    broken = tmp_path / "words.csv"
    broken.write_text("\n".join(patched) + "\n", encoding="utf-8")
    with pytest.raises(SchemaDrift):
        AggregatorAdapter(state="NC", path=broken).fetch(2026, date(2026, 9, 5))


def test_unreadable_source_is_a_source_error(tmp_path):
    with pytest.raises(SourceError):
        AggregatorAdapter(state="NC", path=tmp_path / "nope.csv").fetch(
            2026, date(2026, 9, 5)
        )


def test_the_file_is_downloaded_once_per_run_not_once_per_state(monkeypatch):
    """15 state ladders reaching tier 2 must not mean 15 GETs of the same file."""
    calls = []
    real = AggregatorAdapter._download

    def counting(self, cycle):
        calls.append(cycle)
        return real(self, cycle)

    monkeypatch.setattr(AggregatorAdapter, "_download", counting)
    for state in ("NC", "GA", "CO", "AK"):
        try:
            AggregatorAdapter(state=state, path=US_2026).fetch(2026, date(2026, 9, 5))
        except NotYetPublished:
            pass
    assert calls == [2026]


# ---------------------------------------------------------------------------
# Backfill
# ---------------------------------------------------------------------------
def test_fetch_history_returns_the_final_snapshot():
    result = AggregatorAdapter(state="NC", path=US_2024).fetch_history(2024)
    row = only_state_row(result)
    assert row.day == date(2024, 11, 5)
    assert row.ballots_total == 4481606


def test_fetch_history_dates_a_frozen_tracker_by_its_own_last_update():
    """South Dakota's 2024 tracker stopped on 10/25, so its 'final' is not one.

    Dating it 10/25 is what lets the site show it as an incomplete series rather
    than a suspiciously low election-day total.
    """
    row = only_state_row(
        AggregatorAdapter(state="SD", path=US_2024).fetch_history(2024)
    )
    assert row.day == date(2024, 10, 25)
    assert row.ballots_total == 141554


def test_fetch_history_for_an_empty_cycle_is_not_yet_published():
    with pytest.raises(NotYetPublished):
        AggregatorAdapter(state="GA", path=US_2026).fetch_history(2026)
