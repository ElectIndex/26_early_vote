"""Invariants of the ingest core.

These lock in the rules that every state adapter depends on. If one of these
breaks, every adapter is quietly producing wrong data, so they are worth more
than any individual parser test.
"""

from __future__ import annotations

import csv

from datetime import date

import pytest

from ev import normalize as n
from ev.adapters.base import Adapter, FetchResult, NotYetPublished, SchemaDrift, SourceError
from ev.calendar import days_to_election, election_date
from ev.ladder import STATUS_FAILED, STATUS_OK, STATUS_PENDING, run_state
from ev.publish import merge_rows, mark_restatements
from ev.schema import (
    DEMO_DIMENSIONS, STATE_KEY, DemoDay, Provenance, StateDay, TIER_AGGREGATOR,
    TIER_CIVIC, TIER_MANUAL, TIER_SCRAPER, demo_row_to_dict, state_row_to_dict,
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
# schema: an unknown demographic dimension, refused four tiers earlier
# --------------------------------------------------------------------------
def test_an_unknown_demographic_dimension_is_refused_when_the_row_is_built():
    """⚠️ THIS USED TO BE A WRITE-TIME CRASH, AND WRITE TIME IS THE WORST MOMENT.

    `demo_row_to_dict` has always refused a dimension outside DEMO_DIMENSIONS,
    but it runs in the PUBLISH phase -- after `ev_state_daily.csv` has been
    written and BEFORE `ev_status.json` is. One adapter emitting
    `dimension="ethnicity"` therefore aborted the run for all thirty-five states
    and left the page holding half-new data behind a status file that never
    advanced, with no message anywhere saying why.

    Checked in `__post_init__` it happens inside `adapter.fetch`, which
    `run_state` wraps, so it becomes an ordinary fall-through. Same rule, same
    message, four tiers earlier -- exactly what TownDay already does with its
    GEOID.
    """
    with pytest.raises(ValueError, match="unknown demographic dimension"):
        DemoDay(cycle=2026, state="NC", day=date(2026, 10, 20),
                dimension="ethnicity", bucket="hispanic", ballots_total=5)


def test_every_declared_dimension_is_accepted():
    for dimension in DEMO_DIMENSIONS:
        assert DemoDay(cycle=2026, state="NC", day=date(2026, 10, 20),
                       dimension=dimension, bucket="x").dimension == dimension


def test_the_write_time_check_still_stands():
    """Belt and braces: the guard moved earlier, it did not move away."""
    row = DemoDay(cycle=2026, state="NC", day=date(2026, 10, 20),
                  dimension="sex", bucket="female", ballots_total=5,
                  provenance=Provenance(TIER_SCRAPER, "nc-sbe"))
    row.dimension = "ethnicity"  # smuggled past __post_init__
    with pytest.raises(ValueError, match="unknown demographic dimension"):
        demo_row_to_dict(row)


def test_a_bad_dimension_costs_one_tier_not_the_whole_run():
    """The point of moving the check: the ladder handles it like any other bug."""
    class BadDimension(Adapter):
        state, name, tier = "NC", "nc-sbe", TIER_SCRAPER

        def fetch(self, cycle, as_of):
            return FetchResult(demo_rows=[DemoDay(
                cycle=cycle, state="NC", day=as_of,
                dimension="ethnicity", bucket="hispanic", ballots_total=5)])

    _, outcome = run_state("NC", [BadDimension(), _Answers()],
                           2026, date(2026, 10, 20))
    assert outcome.status == STATUS_OK and outcome.tier == TIER_AGGREGATOR
    assert outcome.attempts[0]["result"] == "crash"


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


def _manual(tmp_path):
    """The REAL floor of every ladder, pointed at an empty data dir.

    Not a stub: `ManualAdapter` is what actually sits at tier 4 for all thirty-five
    states, and what it does with nothing to say is the whole subject below. The
    directory is empty rather than the repo's, so these assertions are about the
    adapter and not about whatever anyone types into data/manual/ tomorrow -- the
    three cases (no file, no row for this state, no row on or before today) all
    raise NotYetPublished alike.
    """
    from ev.adapters.manual import ManualAdapter

    return ManualAdapter(state="NC", data_dir=tmp_path)


def test_the_floor_of_the_ladder_says_not_yet_published_when_nobody_typed_a_number(tmp_path):
    """The fact that made STATUS_FAILED unreachable, asserted directly."""
    with pytest.raises(NotYetPublished):
        _manual(tmp_path).fetch(2026, date(2026, 10, 20))


def test_all_tiers_failing_is_reported_as_failed(tmp_path):
    """⚠️ REWRITTEN. THE SHAPE THIS USED TO ASSERT CANNOT OCCUR IN PRODUCTION.

    It walked two SourceError raisers and let the loop fall off the end. Every
    real ladder is four rungs ending in `ManualAdapter`, and the manual file has
    no 2026 rows -- so the last rung raises NotYetPublished, the loop never
    reaches its end, and the walk returned PENDING. STATUS_FAILED was therefore
    unreachable outside this test: `ev_status.json`'s failed count was
    structurally 0, ingest.yml's "Every tier failed for:" line could never print,
    and `ingest --strict` could never return 1. A state whose scraper, civicAPI
    and the aggregator all broke in the middle of early voting badged as "early
    voting has not opened yet".

    So this now walks the real floor.
    """
    rungs = [
        _Raises(SourceError("502 from the SoS"), TIER_SCRAPER, "nc-sbe"),
        _Raises(SourceError("no capabilities document"), TIER_CIVIC, "civicapi"),
        _Raises(SourceError("tracker unreachable"), TIER_AGGREGATOR, "uf-election-lab"),
        _manual(tmp_path),
    ]
    _, outcome = run_state("NC", rungs, 2026, date(2026, 10, 20))

    assert outcome.status == STATUS_FAILED
    assert [a["tier"] for a in outcome.attempts] == [
        TIER_SCRAPER, TIER_CIVIC, TIER_AGGREGATOR, TIER_MANUAL]
    # The message has to name the outage, not the manual file's opinion of it.
    assert "every tier failed" in outcome.message


def test_the_loop_falling_off_the_end_is_still_a_failure(tmp_path):
    """The original path, kept: a last rung that ERRORS rather than declining."""
    _, outcome = run_state(
        "NC", [_Raises(SourceError("a")), _Raises(SourceError("b"), TIER_MANUAL, "m")],
        2026, date(2026, 10, 20))
    assert outcome.status == STATUS_FAILED


def test_a_not_yet_published_from_a_rung_that_can_see_the_state_is_still_pending(tmp_path):
    """⚠️ AND THE NEW RULE MUST NOT FIRE HERE. This is the GA/MT/NV shape.

    Georgia's own scraper raises SourceError BY DESIGN -- the SoS's only
    machine-readable file is behind a single-use reCAPTCHA token, so it cannot be
    collected unattended -- and civicAPI does not carry Georgia at all. The walk
    therefore reaches the aggregator, which affirmatively reports that no ballots
    have been cast in Georgia yet. That is a true statement about the world made
    by a source that CAN see the state, so `pending` is the honest answer.

    Three states (GA, MT, NV) were in exactly this shape on the day this was
    written. A rule that alarmed on them would be permanently red, and a badge
    that is always red is a badge nobody reads in November.
    """
    rungs = [
        _Raises(SourceError("needs a live captcha token"), TIER_SCRAPER, "ga-sos"),
        _Raises(SourceError("no capabilities document"), TIER_CIVIC, "civicapi"),
        _Raises(NotYetPublished("UF tracker reports no ballots cast in GA"),
                TIER_AGGREGATOR, "uf-election-lab"),
        _manual(tmp_path),
    ]
    _, outcome = run_state("GA", rungs, 2026, date(2026, 9, 6))
    assert outcome.status == STATUS_PENDING
    assert "no ballots cast" in outcome.message


def test_a_one_rung_ladder_that_declines_is_pending_not_failed(tmp_path):
    """Nothing failed above it, because there is nothing above it."""
    _, outcome = run_state("NC", [_Raises(NotYetPublished("not open"))],
                           2026, date(2026, 9, 20))
    assert outcome.status == STATUS_PENDING


def test_an_adapter_that_forgot_its_tier_falls_through_instead_of_killing_the_run():
    """⚠️ ONE MISSING LINE IN A NEW ADAPTER USED TO ABORT ALL THIRTY-FIVE STATES.

    `Adapter.tier` defaults to 0 and `schema.Provenance` refuses a tier that is
    not one of TIER_LABELS, so an adapter that implements `fetch` perfectly and
    forgets `tier = TIER_SCRAPER` raises ValueError from `adapter.provenance()`.
    That call sat OUTSIDE `run_state`'s try block -- so the exception escaped the
    one function whose stated contract is that one bad parser must not take down
    the other states, aborting the ingest walk and taking the `ev_status.json`
    write with it. The page would then hold whatever it last had, with no
    explanation anywhere.
    """
    class ForgotTier(Adapter):
        name = "new-state-scraper"  # ...and no `tier`

        def fetch(self, cycle, as_of):
            return FetchResult(state_rows=[
                StateDay(cycle, "NC", as_of, ballots_total=10)])

    _, outcome = run_state("NC", [ForgotTier("NC"), _Answers()],
                           2026, date(2026, 10, 20))
    assert outcome.status == STATUS_OK and outcome.tier == TIER_AGGREGATOR
    assert outcome.attempts[0]["result"] == "crash"
    assert "unknown source tier" in outcome.attempts[0]["detail"]


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


def test_an_unchanged_row_keeps_its_original_timestamp():
    """⚠️ A state that has not moved is not an update, and must not look like one.

    Every run re-fetches the same file and rebuilds the same rows with a fresh
    `retrieved_at`. Writing them made the published CSVs differ on every run for
    no reason: at a two-hourly cadence, 418 lines across the county and
    demographic tables per run -- roughly 291,000 lines of nothing between
    2026-09-06 and Election Day, in a log whose only job is to show when the
    data moved.

    `retrieved_at` on an unchanged row now reads "this has been the state's
    answer since then" rather than "we looked again", which is the more useful
    of the two. Whether the job ran at all is `ev_status.json`'s `generated_at`,
    rewritten unconditionally every run so no data row has to carry it.
    """
    prior = {"cycle": "2026", "state": "NC", "date": "2026-10-20",
             "ballots_total": "800", "source_tier": "1", "source_name": "nc-sbe",
             "retrieved_at": "2026-10-20T06:00:00+00:00"}
    same = dict(prior, retrieved_at="2026-10-20T08:00:00+00:00")

    rows, replaced, better, richer = merge_rows([prior], [same], ("cycle", "state", "date"))
    assert (replaced, better, richer) == (0, 0, 0), "an unchanged row is not a replacement"
    assert rows[0]["retrieved_at"] == prior["retrieved_at"]

    # A row whose CONTENT moved is a real update and takes the new stamp.
    moved = dict(same, ballots_total="900")
    rows, replaced, _, _ = merge_rows([prior], [moved], ("cycle", "state", "date"))
    assert replaced == 1
    assert (rows[0]["ballots_total"], rows[0]["retrieved_at"]) == (
        "900", same["retrieved_at"])


def test_the_no_churn_rule_never_hides_a_tier_upgrade():
    """A better tier with identical numbers still wins -- provenance IS content.

    Georgia reaching the page through the aggregator and later through its own
    file are the same figure from different authorities, and the badge a reader
    sees is built from `source_tier`. `_same_but_for_stamp` compares every
    column but the timestamp, so the tier difference makes them unequal and the
    upgrade applies. This test exists because a sloppier rule -- "same
    ballots_total, skip" -- would have silently frozen the worse provenance.
    """
    aggregator = {"cycle": "2026", "state": "GA", "date": "2026-10-20",
                  "ballots_total": "500", "source_tier": "3",
                  "source_name": "uf-election-lab", "retrieved_at": "t0"}
    own_file = dict(aggregator, source_tier="1", source_name="ga-sos",
                    retrieved_at="t1")
    rows, replaced, _, _ = merge_rows([aggregator], [own_file],
                                      ("cycle", "state", "date"))
    assert replaced == 1
    assert (rows[0]["source_tier"], rows[0]["retrieved_at"]) == ("1", "t1")


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


def test_a_healthy_scoped_run_does_not_claim_the_merge_failed(tmp_path):
    import json
    from ev.publish import write_status

    write_status(tmp_path, {"states": {"NC": {"status": "ok"}, "TX": {"status": "pending"}},
                            "summary": {}})
    write_status(tmp_path, {"states": {"NC": {"status": "ok"}}, "summary": {}},
                 partial=True)
    out = json.loads((tmp_path / "ev_status.json").read_text())
    assert "merge_failed" not in out["summary"]


def test_a_scoped_run_over_an_unreadable_status_file_says_so(tmp_path, caplog):
    """⚠️ THE ONE PATH WHERE `partial` DOES THE VERY THING IT EXISTS TO PREVENT.

    `partial=True` merges so that a `--state NC` run cannot drop the other
    twenty. But if the file on disk will not parse -- a killed process between
    the temp write and the rename, a bad hand-edit -- the merge falls back to
    `existing = {}` and the scoped run's one state becomes the whole file. The
    other twenty vanish from the file the page reads, which is precisely the bug
    that once showed nineteen tracked states as "not tracked", arrived at from
    the other side.

    The loss itself is unavoidable: an unreadable file has no states to keep, and
    writing nothing at all would leave the page with no status. What was
    unacceptable was that it happened in silence -- the run exited 0 and printed
    a cheerful `ok=1`. Now it logs an ERROR and marks the file, so a reader can
    tell a state that is MISSING from a state that was never ASKED, and the fix
    (re-run unscoped) is in the log.
    """
    import json
    import logging
    from ev.publish import write_status

    write_status(tmp_path, {
        "generated_at": "2026-10-20T06:00:00+00:00",
        "states": {s: {"status": "ok"} for s in ("NC", "FL", "GA", "TX", "IL")},
        "summary": {"ok": 5, "pending": 0, "failed": 0},
    })
    (tmp_path / "ev_status.json").write_text('{"generated_at": "2026-10-2')  # truncated

    with caplog.at_level(logging.ERROR, logger="ev.publish"):
        write_status(tmp_path, {
            "generated_at": "2026-10-20T08:00:00+00:00",
            "states": {"NC": {"status": "ok"}},
            "summary": {"ok": 1, "pending": 0, "failed": 0},
        }, partial=True)

    out = json.loads((tmp_path / "ev_status.json").read_text())
    assert set(out["states"]) == {"NC"}, "nothing recoverable was on disk"
    assert out["summary"]["merge_failed"] is True, "...but it must not be silent"
    assert out["summary"]["partial_run"] is True
    assert "unreadable" in caplog.text and "ev_status.json" in caplog.text


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


def test_prior_final_needs_a_REPORTED_election_day_row(tmp_path):
    """A blank Election Day row is not proof the series finished.

    South Carolina 2022 has exactly that shape: a day-0 row with no total, the
    rest of the series stopping 18 days out. Accepting it published a partial
    count as the cycle's final, and the site divided by it — showing a 1.0%
    "share of its 2022 early vote" for a state that early-voted in the hundreds
    of thousands.
    """
    from ev.publish import derive_prior_finals

    rows = _series("2022", "SC", [(18, 16975)])
    rows.append({"cycle": "2022", "state": "SC", "date": "2022-11-08",
                 "days_to_election": "0", "ballots_total": "",
                 "source_tier": "1", "source_name": "x", "retrieved_at": "z"})
    _write_daily(tmp_path, rows)
    assert derive_prior_finals(tmp_path) == {}

    # The same series WITH a reported Election Day figure does complete.
    rows[-1]["ballots_total"] = "845000"
    _write_daily(tmp_path, rows)
    assert derive_prior_finals(tmp_path) == {("2022", "SC"): "845000"}


# --------------------------------------------------------------------------
# A DERIVED table replaces rather than merges -- and does not churn
# --------------------------------------------------------------------------
COLS = ["cycle", "state", "date", "value", "retrieved_at"]
KEY = ("cycle", "state", "date")


def _row(value, stamp, date="2026-10-20"):
    return {"cycle": "2026", "state": "NC", "date": date,
            "value": value, "retrieved_at": stamp}


def test_a_full_rebuild_drops_rows_the_model_no_longer_produces(tmp_path):
    """The merge cannot forget, and for a derived table that is wrong.

    `counterfactual.py` learned to refuse immature days and the 182 rows it had
    already published had nothing to replace them, so they outlived the bug that
    made them. A scraped table must never behave this way -- a state that fails
    today must not delete yesterday -- which is why only the caller may ask.
    """
    from ev.publish import publish_table

    path = tmp_path / "derived.csv"
    publish_table(path, COLS, KEY,
                  [_row("1", "t0", "2026-10-19"), _row("2", "t0", "2026-10-20")],
                  guard=False, replace=True)
    info = publish_table(path, COLS, KEY, [_row("2", "t1", "2026-10-20")],
                         guard=False, replace=True)
    assert info["rows"] == 1
    rows = list(csv.DictReader(path.open()))
    assert [r["date"] for r in rows] == ["2026-10-20"]

    # ...and a MERGE keeps both, which is the behaviour a scraped table needs.
    publish_table(path, COLS, KEY,
                  [_row("1", "t0", "2026-10-19"), _row("2", "t0", "2026-10-20")],
                  guard=False, replace=True)
    publish_table(path, COLS, KEY, [_row("2", "t1", "2026-10-20")], guard=False)
    assert len(list(csv.DictReader(path.open()))) == 2


def test_a_row_that_says_the_same_thing_keeps_its_original_timestamp(tmp_path):
    """⚠️ Otherwise the scheduled job commits 246 lines of nothing every run.

    A derived table is a pure function of data already on disk, so a run with no
    new input produces byte-identical numbers -- and used to rewrite every row
    anyway to bump `retrieved_at`. That made `git diff --cached --quiet`
    permanently false, so the two-hourly job committed on every run and the log
    stopped meaning "the data moved". Keeping the old stamp is also truer: it
    reads "this has been the answer since then" rather than "a model ran".
    """
    from ev.publish import publish_table

    path = tmp_path / "derived.csv"
    publish_table(path, COLS, KEY, [_row("42", "first")], guard=False, replace=True)
    before = path.read_bytes()

    # Same answer, later run: byte-identical file, so nothing to commit.
    publish_table(path, COLS, KEY, [_row("42", "second")], guard=False, replace=True)
    assert path.read_bytes() == before

    # A row whose CONTENT moved takes the new stamp, because it is new.
    publish_table(path, COLS, KEY, [_row("43", "third")], guard=False, replace=True)
    row = list(csv.DictReader(path.open()))[0]
    assert (row["value"], row["retrieved_at"]) == ("43", "third")


def test_the_timestamp_rule_is_per_row_not_per_file(tmp_path):
    """One row moving must not restamp the rows that did not."""
    from ev.publish import publish_table

    path = tmp_path / "derived.csv"
    publish_table(path, COLS, KEY,
                  [_row("1", "first", "2026-10-19"), _row("2", "first", "2026-10-20")],
                  guard=False, replace=True)
    publish_table(path, COLS, KEY,
                  [_row("1", "second", "2026-10-19"), _row("9", "second", "2026-10-20")],
                  guard=False, replace=True)
    stamps = {r["date"]: r["retrieved_at"] for r in csv.DictReader(path.open())}
    assert stamps["2026-10-19"] == "first", "unchanged row was restamped"
    assert stamps["2026-10-20"] == "second", "changed row kept a stale stamp"


# --------------------------------------------------------------------------
# A PARTIAL TIER-1 SOURCE MUST NOT BLOCK A STATEWIDE NUMBER THAT EXISTS
# --------------------------------------------------------------------------

class _CountiesOnly(Adapter):
    """New York's shape: real county rows, and deliberately no StateDay.

    `ny.py` covers five of sixty-two counties, so a summed "New York" total
    would be wrong in the one direction that looks right. It publishes counties
    and refuses the state row.
    """

    state = "NY"
    name = "ny-nyc"
    tier = TIER_SCRAPER

    def fetch(self, cycle: int, as_of: date) -> FetchResult:
        from ev.schema import CountyDay
        out = FetchResult()
        out.county_rows.append(CountyDay(
            cycle=cycle, state="NY", county_fips="36061", day=as_of,
            county_name="New York County", ballots_total=1000, inperson=1000,
        ))
        return out


class _StatewideOnly(Adapter):
    """The aggregator's shape: one statewide row, no counties."""

    state = "NY"
    name = "uf-election-lab"
    tier = TIER_AGGREGATOR

    def fetch(self, cycle: int, as_of: date) -> FetchResult:
        out = FetchResult()
        out.state_rows.append(StateDay(
            cycle=cycle, state="NY", day=as_of, ballots_total=2_985_181,
            inperson=2_985_181,
        ))
        return out


def test_a_partial_scraper_borrows_a_statewide_row_from_below():
    """The city is not the state, and the state's number was two rungs down.

    Before this, `ny-nyc` answered at tier 1 and the walk stopped, so New York
    had no statewide figure at all while the aggregator was carrying the state
    Board's own count. The page showed the five boroughs and called it New York.
    """
    result, outcome = run_state("NY", [_CountiesOnly(), _StatewideOnly()],
                                2026, date(2026, 10, 20))
    assert outcome.status == STATUS_OK
    # The WINNER is still tier 1: this is a top-up, not a demotion.
    assert outcome.tier == TIER_SCRAPER
    assert outcome.source_name == "ny-nyc"
    assert len(result.county_rows) == 1
    assert len(result.state_rows) == 1
    assert result.state_rows[0].ballots_total == 2_985_181
    # ...and the borrowed row is labelled at the tier it came from, not tier 1.
    assert result.state_rows[0].provenance.tier == TIER_AGGREGATOR
    assert result.county_rows[0].provenance.tier == TIER_SCRAPER
    assert any(a["result"] == "topup_statewide" for a in outcome.attempts)
    assert outcome.rows == 2


def test_only_the_statewide_row_is_borrowed_never_the_counties():
    """A top-up is not a merge. The tier that won is the richer one."""

    class _RicherBelow(_StatewideOnly):
        def fetch(self, cycle: int, as_of: date) -> FetchResult:
            from ev.schema import CountyDay
            out = super().fetch(cycle, as_of)
            out.county_rows.append(CountyDay(
                cycle=cycle, state="NY", county_fips="36001", day=as_of,
                county_name="Albany County", ballots_total=7,
            ))
            return out

    result, _ = run_state("NY", [_CountiesOnly(), _RicherBelow()],
                          2026, date(2026, 10, 20))
    assert [r.county_fips for r in result.county_rows] == ["36061"]
    assert len(result.state_rows) == 1


def test_a_source_that_already_has_a_state_row_is_left_alone():
    """No extra fetch, no borrowed row, nothing changed for the other states."""
    calls = []

    class _Counted(_StatewideOnly):
        def fetch(self, cycle: int, as_of: date) -> FetchResult:
            calls.append(1)
            return super().fetch(cycle, as_of)

    result, outcome = run_state("NC", [_Answers(), _Counted()],
                                2026, date(2026, 10, 20))
    assert outcome.status == STATUS_OK
    assert calls == [], "the lower rung must not be fetched at all"
    assert not any(a["result"].startswith("topup") for a in outcome.attempts)


def test_a_crash_below_cannot_spoil_a_good_run():
    """The top-up runs after real data is already in hand.

    The worst outcome it is allowed to produce is the one we already had, so
    every failure below is swallowed and recorded rather than raised.
    """

    class _Explodes(Adapter):
        state = "NY"
        name = "boom"
        tier = TIER_AGGREGATOR

        def fetch(self, cycle: int, as_of: date) -> FetchResult:
            raise RuntimeError("kaboom")

    result, outcome = run_state("NY", [_CountiesOnly(), _Explodes()],
                                2026, date(2026, 10, 20))
    assert outcome.status == STATUS_OK
    assert len(result.county_rows) == 1
    assert result.state_rows == []
    assert any(a["result"] == "topup_failed" for a in outcome.attempts)


def test_nothing_below_with_a_state_row_is_a_quiet_blank():
    """A state that genuinely has no statewide figure keeps its honest hole."""

    class _AlsoCountiesOnly(_CountiesOnly):
        name = "civicapi"
        tier = TIER_CIVIC

    result, outcome = run_state("NY", [_CountiesOnly(), _AlsoCountiesOnly()],
                                2026, date(2026, 10, 20))
    assert outcome.status == STATUS_OK
    assert result.state_rows == []
    assert any(a["result"] == "topup_no_state_row" for a in outcome.attempts)
