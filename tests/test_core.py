"""Invariants of the ingest core.

These lock in the rules that every state adapter depends on. If one of these
breaks, every adapter is quietly producing wrong data, so they are worth more
than any individual parser test.
"""

from __future__ import annotations

from datetime import date

import pytest

from ev import normalize as n
from ev.adapters.base import Adapter, FetchResult, NotYetPublished, SchemaDrift, SourceError
from ev.calendar import days_to_election, election_date
from ev.ladder import STATUS_FAILED, STATUS_OK, STATUS_PENDING, run_state
from ev.publish import merge_rows, mark_restatements
from ev.schema import (
    STATE_KEY, Provenance, StateDay, TIER_AGGREGATOR, TIER_MANUAL, TIER_SCRAPER,
    state_row_to_dict,
)


# --------------------------------------------------------------------------
# calendar: days_to_election is the x-axis for every cross-cycle comparison
# --------------------------------------------------------------------------
def test_days_to_election_aligns_cycles():
    """The same days-out in different cycles is the whole basis of comparison."""
    assert days_to_election(2026, date(2026, 10, 20)) == 14
    assert days_to_election(2022, date(2022, 10, 25)) == 14
    assert days_to_election(2024, date(2024, 10, 22)) == 14


def test_election_day_is_zero_and_after_is_negative():
    assert days_to_election(2026, election_date(2026)) == 0
    assert days_to_election(2026, date(2026, 11, 4)) == -1


def test_dst_transition_does_not_shift_the_axis():
    """DST lands inside every early-vote window; dates must stay whole days."""
    before = days_to_election(2026, date(2026, 10, 31))
    after = days_to_election(2026, date(2026, 11, 1))
    assert before - after == 1


# --------------------------------------------------------------------------
# schema: THE BLANK RULE
# --------------------------------------------------------------------------
def test_unreported_field_writes_blank_not_zero():
    """Georgia has no party registration; a 0 would read as 'no Democrat voted'."""
    row = StateDay(2026, "GA", date(2026, 10, 20), ballots_total=1000,
                   provenance=Provenance(TIER_SCRAPER, "ga-sos"))
    out = state_row_to_dict(row)
    assert out["party_dem"] == ""
    assert out["ballots_total"] == "1000"


def test_reported_zero_survives_as_zero():
    """A real zero is data and must not be flattened into a blank."""
    row = StateDay(2026, "NC", date(2026, 9, 20), ballots_total=0, inperson=0,
                   provenance=Provenance(TIER_SCRAPER, "nc-sbe"))
    out = state_row_to_dict(row)
    assert out["ballots_total"] == "0" and out["inperson"] == "0"


def test_row_without_provenance_is_refused():
    with pytest.raises(ValueError, match="provenance"):
        state_row_to_dict(StateDay(2026, "NC", date(2026, 10, 1)))


# --------------------------------------------------------------------------
# normalize: one vocabulary
# --------------------------------------------------------------------------
def test_unaffiliated_is_npa_not_other():
    """Unaffiliated is the most-watched early-vote number; merging it into
    'other' with the Libertarians would destroy it."""
    for label in ("UNA", "Unaffiliated", "UAF", "NF", "Non-Partisan", "NPA", "DTS"):
        assert n.party(label) == n.PARTY_NPA, label
    for label in ("LIB", "Green", "Working Families"):
        assert n.party(label) == n.PARTY_OTH, label


def test_unknown_label_returns_none_so_adapters_can_raise_drift():
    """None means 'I do not recognise this' -- never silently 'other'."""
    assert n.party("Whig") is None
    assert n.method("teleportation") is None
    assert n.race("klingon") is None


def test_method_vocabulary_covers_state_spellings():
    assert n.method("One Stop") == n.METHOD_INPERSON      # NC
    assert n.method("Advance Voting") == n.METHOD_INPERSON  # GA
    assert n.method("VBM") == n.METHOD_MAIL                # FL


def test_age_banding():
    assert n.age_band(18) == "18-24"
    assert n.age_band(64) == "55-64"
    assert n.age_band(91) == "65+"
    assert n.age_band("65 and over") == "65+"
    assert n.age_band(17) is None  # ineligible -> we misread a column


def test_county_fips_is_five_digits_zero_padded():
    assert n.county_fips("NC", 63) == "37063"
    assert n.county_fips("AL", "1") == "01001"


# --------------------------------------------------------------------------
# ladder: the two walk rules
# --------------------------------------------------------------------------
class _Raises(Adapter):
    def __init__(self, exc, tier=TIER_SCRAPER, name="t1"):
        super().__init__("NC")
        self._exc, self.tier, self.name = exc, tier, name

    def fetch(self, cycle, as_of):
        raise self._exc


class _Answers(Adapter):
    def __init__(self, tier=TIER_AGGREGATOR, name="agg", total=5):
        super().__init__("NC")
        self.tier, self.name, self._total = tier, name, total

    def fetch(self, cycle, as_of):
        return FetchResult(state_rows=[StateDay(cycle, "NC", as_of, ballots_total=self._total)])


def test_not_yet_published_stops_the_walk():
    """The rule that stops a weaker tier inventing a zero for a state that
    simply has not opened early voting yet."""
    result, outcome = run_state("NC", [_Raises(NotYetPublished("not open")), _Answers()],
                                2026, date(2026, 9, 20))
    assert outcome.status == STATUS_PENDING
    assert outcome.tier is None
    assert result.state_rows == []


def test_source_error_falls_through():
    result, outcome = run_state("NC", [_Raises(SourceError("502")), _Answers()],
                                2026, date(2026, 10, 20))
    assert outcome.status == STATUS_OK and outcome.tier == TIER_AGGREGATOR
    assert result.state_rows[0].provenance.tier == TIER_AGGREGATOR


def test_schema_drift_falls_through():
    """Drift is a SourceError: better a lower tier than a guessed column mapping."""
    _, outcome = run_state("NC", [_Raises(SchemaDrift("columns moved")), _Answers()],
                           2026, date(2026, 10, 20))
    assert outcome.status == STATUS_OK and outcome.tier == TIER_AGGREGATOR


def test_a_crashing_adapter_does_not_abort_the_state():
    """One bad parser must not take down the other forty-nine states."""
    _, outcome = run_state("NC", [_Raises(ZeroDivisionError("boom")), _Answers()],
                           2026, date(2026, 10, 20))
    assert outcome.status == STATUS_OK
    assert outcome.attempts[0]["result"] == "crash"


def test_all_tiers_failing_is_reported_as_failed():
    _, outcome = run_state("NC", [_Raises(SourceError("a")), _Raises(SourceError("b"), TIER_MANUAL, "m")],
                           2026, date(2026, 10, 20))
    assert outcome.status == STATUS_FAILED


def test_empty_result_falls_through_to_next_tier():
    class _Empty(Adapter):
        state, name, tier = "NC", "empty", TIER_SCRAPER
        def fetch(self, cycle, as_of):
            return FetchResult()

    _, outcome = run_state("NC", [_Empty(), _Answers()], 2026, date(2026, 10, 20))
    assert outcome.status == STATUS_OK and outcome.tier == TIER_AGGREGATOR


# --------------------------------------------------------------------------
# publish: the merge rules
# --------------------------------------------------------------------------
K = {"cycle": "2026", "state": "NC", "date": "2026-10-20"}
RICH = K | {"source_tier": "1", "ballots_total": "100", "party_dem": "40", "party_rep": "35"}


def test_worse_tier_never_overwrites_better():
    """A transient scraper failure must not downgrade yesterday's county-level
    row to a bare statewide total."""
    rows, _, kept_better, _ = merge_rows([RICH], [K | {"source_tier": "2", "ballots_total": "100"}], STATE_KEY)
    assert kept_better == 1 and rows[0]["party_dem"] == "40"


def test_truncated_same_tier_row_is_rejected():
    """A genuine update never loses columns; a half-parsed download does."""
    rows, _, _, kept_richer = merge_rows([RICH], [K | {"source_tier": "1", "ballots_total": "100"}], STATE_KEY)
    assert kept_richer == 1 and rows[0]["party_dem"] == "40"


def test_real_update_at_same_tier_applies():
    rows, replaced, _, _ = merge_rows(
        [RICH], [K | {"source_tier": "1", "ballots_total": "120", "party_dem": "48", "party_rep": "41"}],
        STATE_KEY)
    assert replaced == 1 and rows[0]["ballots_total"] == "120"


def test_tier_upgrade_applies():
    rows, replaced, _, _ = merge_rows([K | {"source_tier": "2", "ballots_total": "100"}], [RICH], STATE_KEY)
    assert replaced == 1 and rows[0]["source_tier"] == "1"


def test_restatement_is_flagged_not_dropped():
    """States restate counts; a decrease is real data worth annotating."""
    series = [{"cycle": "2026", "state": "NC", "date": f"2026-10-2{i}",
               "ballots_total": t, "restated": "0"}
              for i, t in enumerate(["100", "250", "240", "300"])]
    assert mark_restatements(series) == 1
    assert series[2]["restated"] == "1"
    assert series[2]["ballots_total"] == "240"  # kept, not dropped


def test_blank_totals_do_not_trigger_restatement():
    series = [{"cycle": "2026", "state": "NC", "date": f"2026-10-2{i}",
               "ballots_total": t, "restated": "0"}
              for i, t in enumerate(["100", "", "150"])]
    assert mark_restatements(series) == 0


# --------------------------------------------------------------------------
# publish.derive_prior_finals: the denominator behind "% of final"
# --------------------------------------------------------------------------
def _series(cycle, state, pairs):
    """pairs = [(days_to_election, ballots_total), ...]"""
    return [{"cycle": cycle, "state": state, "date": f"2022-11-0{i+1}",
             "days_to_election": str(d), "ballots_total": str(t),
             "source_tier": "1", "source_name": "x", "retrieved_at": "z"}
            for i, (d, t) in enumerate(pairs)]


def _write_daily(tmp_path, rows):
    from ev.publish import _atomic_write
    from ev.schema import STATE_DAILY_COLUMNS
    _atomic_write(tmp_path / "ev_state_daily.csv", STATE_DAILY_COLUMNS, rows)
    return tmp_path


def test_prior_final_requires_reaching_election_day(tmp_path):
    """A series that stops early would understate the final and inflate every
    percentage computed against it, so it yields nothing at all."""
    from ev.publish import derive_prior_finals
    _write_daily(tmp_path, _series("2022", "NC", [(5, 900), (3, 1000)]))
    assert derive_prior_finals(tmp_path) == {}


def test_prior_final_taken_when_series_completes(tmp_path):
    from ev.publish import derive_prior_finals
    _write_daily(tmp_path, _series("2022", "NC", [(2, 900), (0, 1200)]))
    assert derive_prior_finals(tmp_path) == {("2022", "NC"): "1200"}


def test_prior_final_ignores_the_cycle_in_progress(tmp_path):
    """2026 has no final yet; deriving one would divide by a moving target."""
    from ev.publish import derive_prior_finals
    _write_daily(tmp_path, _series("2026", "NC", [(0, 50)]))
    assert derive_prior_finals(tmp_path) == {}


def test_hand_entered_total_is_never_overwritten(tmp_path):
    """An official canvass figure beats our scrape; we only fill blanks."""
    import csv
    from ev.publish import STATE_META_COLUMNS, publish_state_meta

    meta = tmp_path / "states.csv"
    with meta.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=STATE_META_COLUMNS)
        w.writeheader()
        w.writerow({c: "" for c in STATE_META_COLUMNS}
                   | {"state": "NC", "name": "North Carolina", "ev_2022_total": "999"})
        w.writerow({c: "" for c in STATE_META_COLUMNS}
                   | {"state": "GA", "name": "Georgia"})

    out = tmp_path / "out"
    out.mkdir()
    publish_state_meta(out, meta, {("2022", "NC"): "111", ("2022", "GA"): "222"})

    with (out / "ev_state_meta.csv").open(newline="", encoding="utf-8") as fh:
        rows = {r["state"]: r for r in csv.DictReader(fh)}
    assert rows["NC"]["ev_2022_total"] == "999"   # hand-entered survives
    assert rows["GA"]["ev_2022_total"] == "222"   # blank gets filled


# --------------------------------------------------------------------------
# publish.write_status: a scoped run must not erase the other states
# --------------------------------------------------------------------------
def test_scoped_run_merges_status_instead_of_replacing(tmp_path):
    """`ingest --state NC` knows nothing about the other twenty.

    Writing its payload wholesale dropped them from the file the page reads, and
    the site showed nineteen tracked states as "not tracked". A scoped run must
    merge.
    """
    import json
    from ev.publish import write_status

    full = {
        "generated_at": "2026-09-06T00:00:00+00:00",
        "states": {s: {"status": "pending", "tier": None} for s in
                   ("NC", "FL", "IL", "GA", "TX")},
        "summary": {"ok": 0, "pending": 5, "failed": 0},
    }
    write_status(tmp_path, full)

    scoped = {
        "generated_at": "2026-09-06T06:00:00+00:00",
        "states": {"NC": {"status": "ok", "tier": 1}},
        "summary": {"ok": 1, "pending": 0, "failed": 0},
    }
    write_status(tmp_path, scoped, partial=True)

    out = json.loads((tmp_path / "ev_status.json").read_text())
    assert set(out["states"]) == {"NC", "FL", "IL", "GA", "TX"}
    assert out["states"]["NC"]["status"] == "ok"      # the scoped state updated
    assert out["states"]["TX"]["status"] == "pending"  # the others survived
    # The summary describes the whole file, not the slice that was run.
    assert out["summary"]["ok"] == 1 and out["summary"]["pending"] == 4
    assert out["summary"]["partial_run"] is True


def test_unscoped_run_replaces_status_wholesale(tmp_path):
    """The daily job IS the whole picture, so a state it no longer tracks must
    disappear rather than linger from an older file."""
    import json
    from ev.publish import write_status

    write_status(tmp_path, {"states": {"NC": {"status": "ok"}, "ZZ": {"status": "ok"}},
                            "summary": {}})
    write_status(tmp_path, {"states": {"NC": {"status": "ok"}}, "summary": {}})
    out = json.loads((tmp_path / "ev_status.json").read_text())
    assert set(out["states"]) == {"NC"}
