"""Tests for the turnout projection.

Four of these carry the load:

  * `test_write_touches_only_its_own_file` hashes an output/ tree before and
    after a publish and asserts nothing else moved. A model number in a column
    that means "the state counted this" is the one unrecoverable mistake here.

  * `test_a_cycle_in_progress_never_uses_a_cross_type_reference` pins the
    refusal that keeps the measured-wrong arithmetic out of a forward-looking
    row. A midterm projected off a presidential curve is off by 54% of turnout.

  * `test_the_measured_finding_is_pinned` re-derives every number in
    docs/turnout.md from a saved slice of the real published data, so if the
    finding moves the document fails a test rather than quietly going stale.

  * `test_nothing_beats_the_null_which_is_what_the_document_says` asserts the
    headline claim directly. If the early-vote pace ever buys more than
    MIN_GAIN against BOTH nulls, this fails and the recommendation has to be
    revisited rather than inherited.
"""

from __future__ import annotations

import csv
import hashlib
from datetime import date
from pathlib import Path

import pytest

from ev import turnout
from ev.turnout import (
    Curve, MIN_COVERAGE, MIN_GAIN, MODEL_ERROR, MODEL_ERROR_TOLERANCE,
    Projection, Reference, TurnoutError,
    build, build_references, comparable_cycle, election_kind, format_validation,
    measured_model_error, project_day, read_curves, read_turnout,
    reference_kind, rollup, shape_transfer, validate, write,
)

FIXTURES = Path(__file__).parent / "fixtures" / "turnout"

DAILY_HEADER = (
    "cycle,state,date,days_to_election,ballots_total,ballots_new,"
    "mail_requested,mail_returned,inperson,party_dem,party_rep,party_oth,"
    "party_npa,restated,source_tier,source_name,retrieved_at\n"
)
RESULTS_HEADER = (
    "cycle,state,office,dem_votes,rep_votes,oth_votes,total_votes,dem_share,"
    "rep_share,margin,winner_party,early_share_of_total,source_name,retrieved_at\n"
)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _daily(rows) -> str:
    out = [DAILY_HEADER]
    for cycle, state, day, dte, ballots in rows:
        cell = "" if ballots is None else str(ballots)
        out.append(f"{cycle},{state},{day},{dte},{cell},,,,,,,,,0,1,test,2026-01-01T00:00:00+00:00\n")
    return "".join(out)


def _results(rows) -> str:
    out = [RESULTS_HEADER]
    for cycle, state, office, total in rows:
        out.append(f"{cycle},{state},{office},,,,{total},,,,,,test,2026-01-01T00:00:00+00:00\n")
    return "".join(out)


def _tree(tmp_path: Path, daily: str, results: str) -> Path:
    out = tmp_path / "output"
    out.mkdir(parents=True, exist_ok=True)
    (out / "ev_state_daily.csv").write_text(daily, encoding="utf-8")
    (out / "results_state.csv").write_text(results, encoding="utf-8")
    return out


def _curve(cycle: int, state: str, days: dict[int, int]) -> Curve:
    return Curve(cycle, state,
                 {d: (f"20{cycle % 100:02d}-10-{max(1, 31 - d):02d}", b)
                  for d, b in days.items()})


def _reference(days: dict[int, int], early: int, total: int,
               cycle: int = 2022, state: str = "ZZ") -> Reference:
    return Reference(curve=_curve(cycle, state, days), early_final=early,
                     turnout=total, office="senate")


def _hash_tree(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*")) if p.is_file()
    }


# --------------------------------------------------------------------------
# The arithmetic
# --------------------------------------------------------------------------
def test_the_fitted_fraction_is_coverage_times_the_early_share():
    """The identity the whole module is built on, asserted rather than assumed."""
    ref = _reference({10: 400, 0: 1000}, early=1000, total=4000)

    fitted, coverage, ref_dte, _, ref_ballots = ref.fraction(10)

    assert coverage == pytest.approx(0.4)          # 400 of a 1000-ballot early vote
    assert ref.early_share == pytest.approx(0.25)  # 1000 of 4000 votes cast
    assert fitted == pytest.approx(0.4 * 0.25)
    assert (ref_dte, ref_ballots) == (10, 400)


def test_the_projection_is_ballots_over_the_fitted_fraction():
    ref = _reference({10: 400, 0: 1000}, early=1000, total=4000)
    got = project_day(2026, "ZZ", date(2026, 10, 24), 500, ref, 10)

    assert got.fitted_fraction == pytest.approx(0.1)
    assert got.projected_turnout == pytest.approx(5000)


def test_the_projection_equals_the_prior_turnout_scaled_by_the_pace_ratio():
    """The same number written the other way round, which is what it means.

    `ballots / (B_ref/E_ref * E_ref/T_ref)` is `T_ref * ballots / B_ref`. If
    those two ever disagree the model is not what the docs say it is.
    """
    ref = _reference({12: 250, 0: 1000}, early=1000, total=4000)
    got = project_day(2026, "ZZ", date(2026, 10, 22), 400, ref, 12)

    assert got.projected_turnout == pytest.approx(4000 * 400 / 250)


def test_the_early_vote_projection_uses_only_the_shape():
    """`projected_final_early` is the half that transfers, published on its own."""
    ref = _reference({10: 400, 0: 1000}, early=1000, total=4000)
    got = project_day(2026, "ZZ", date(2026, 10, 24), 500, ref, 10)

    assert got.projected_final_early == pytest.approx(500 / 0.4)
    # ...and dividing that by the reference LEVEL gives the turnout figure.
    assert got.projected_final_early / ref.early_share == pytest.approx(
        got.projected_turnout)


def test_the_band_is_multiplicative_and_contains_its_own_centre():
    ref = _reference({10: 400, 0: 1000}, early=1000, total=4000)
    got = project_day(2026, "ZZ", date(2026, 10, 24), 500, ref, 10)

    assert got.projected_lo < got.projected_turnout < got.projected_hi
    assert got.projected_hi == pytest.approx(
        got.projected_turnout * (1 + MODEL_ERROR))
    assert got.projected_lo == pytest.approx(
        got.projected_turnout / (1 + MODEL_ERROR))
    assert got.band_contains(got.projected_turnout) is True
    assert got.band_contains(got.projected_hi * 2) is False
    assert got.band_contains(None) is None


def test_a_day_gap_widens_the_band_and_is_published():
    ref = _reference({11: 400, 0: 1000}, early=1000, total=4000)
    got = project_day(2026, "ZZ", date(2026, 10, 24), 500, ref, 10)

    assert got.day_gap == 1
    assert got.half_width == pytest.approx(MODEL_ERROR + turnout.DAY_GAP_ERROR)
    assert got.to_dict()["day_gap"] == "1"


def test_the_null_on_the_row_is_the_reference_cycles_turnout():
    ref = _reference({0: 1000}, early=1000, total=4000)
    got = project_day(2026, "ZZ", date(2026, 11, 3), 1200, ref, 0)

    assert got.null_turnout == 4000
    assert got.projected_vs_null == pytest.approx(20.0)


def test_error_columns_are_blank_until_the_cycle_is_certified():
    ref = _reference({0: 1000}, early=1000, total=4000)
    live = project_day(2026, "ZZ", date(2026, 11, 3), 1200, ref, 0)
    audit = project_day(2026, "ZZ", date(2026, 11, 3), 1200, ref, 0,
                        actual_turnout=5000)

    assert live.error_pct is None and live.null_error_pct is None
    assert live.to_dict()["error_pct"] == ""
    assert live.to_dict()["actual_turnout"] == ""
    assert audit.error_pct == pytest.approx(100 * (4800 / 5000 - 1))
    assert audit.null_error_pct == pytest.approx(100 * (4000 / 5000 - 1))


# --------------------------------------------------------------------------
# THE BLANK RULE, on the read side and in the refusals
# --------------------------------------------------------------------------
def test_a_blank_ballot_total_is_absent_not_zero(tmp_path):
    out = _tree(tmp_path, _daily([
        (2024, "ZZ", "2024-10-26", 10, 100),
        (2024, "ZZ", "2024-10-27", 9, None),
        (2024, "ZZ", "2024-10-28", 8, 300),
    ]), _results([(2024, "ZZ", "president", 1000)]))

    curve = read_curves(out)[(2024, "ZZ")]

    assert sorted(curve.days) == [8, 10]
    assert 9 not in curve.days


def test_a_day_with_zero_ballots_projects_nothing_rather_than_zero_turnout():
    ref = _reference({10: 400, 0: 1000}, early=1000, total=4000)
    assert project_day(2026, "ZZ", date(2026, 10, 24), 0, ref, 10) is None


def test_a_state_with_no_reference_produces_no_row_at_all(tmp_path):
    out = _tree(tmp_path, _daily([
        (2026, "ZZ", "2026-10-20", 14, 5000),
    ]), _results([]))

    rows, refused = build(out)

    assert rows == []
    assert any("ZZ" in r and "2022" in r for r in refused)


def test_a_truncated_reference_series_is_refused(tmp_path):
    """South Carolina 2022 is the case: it stops eighteen days out."""
    out = _tree(tmp_path, _daily([
        (2022, "SC", "2022-10-21", 18, 16975),
        (2024, "SC", "2024-10-18", 18, 29533),
    ]), _results([(2022, "SC", "senate", 1695702),
                  (2024, "SC", "president", 2548140)]))

    refs, refused = build_references(out)

    assert (2022, "SC") not in refs
    assert any("stops 18 days out" in r for r in refused)


def test_a_reference_without_a_certified_total_is_refused(tmp_path):
    """Maine, Tennessee, Texas and Virginia 2022: no Senate race that year."""
    out = _tree(tmp_path, _daily([
        (2022, "ME", "2022-11-08", 0, 236972),
    ]), _results([]))

    refs, refused = build_references(out)

    assert refs == {}
    assert any("no certified statewide total" in r for r in refused)


def test_a_day_below_the_coverage_floor_produces_no_row():
    """North Carolina 2022 is 17 ballots at 60 days out. Dividing by that is not
    a projection, it is a five-figure multiplication of a handful of ballots."""
    ref = _reference({60: 17, 0: 2187856}, early=2187856, total=3773924)
    assert ref.fraction(60)[1] < MIN_COVERAGE
    assert project_day(2026, "NC", date(2026, 9, 4), 8, ref, 60) is None


# --------------------------------------------------------------------------
# Cross-cycle matching
# --------------------------------------------------------------------------
def test_days_are_matched_by_days_to_election_never_by_date():
    """Election Day moves: Nov 8 2022, Nov 5 2024, Nov 3 2026."""
    curve = _curve(2022, "ZZ", {10: 100, 9: 200})
    got = curve.at(10)

    assert got[0] == 10 and got[2] == 100
    # The 2022 row's calendar date is three days later than the 2026 day it is
    # being matched to; only the days-out axis is consulted.
    assert got[1] != "2026-10-24"


def test_the_match_tolerance_is_one_day():
    curve = _curve(2022, "ZZ", {12: 100})

    assert curve.at(12)[0] == 12
    assert curve.at(11)[0] == 12
    assert curve.at(13)[0] == 12
    assert curve.at(10) is None
    assert curve.at(14) is None


def test_post_election_rows_are_never_the_final_or_a_match():
    curve = _curve(2024, "ZZ", {2: 100, 0: 900, -7: 1000})

    assert curve.final_dte == 0
    assert curve.final_early == 900
    assert curve.at(0)[0] == 0
    # The canvassed row is never returned, even when it is the nearest day.
    assert curve.at(-7) is None
    assert _curve(2024, "ZZ", {-7: 1000}).at(-7) is None
    assert _curve(2024, "ZZ", {-7: 1000}).final_dte is None


def test_a_restated_day_keeps_the_larger_figure_never_the_sum(tmp_path):
    out = _tree(tmp_path, _daily([
        (2024, "ZZ", "2024-10-26", 10, 100),
        (2024, "ZZ", "2024-10-26", 10, 250),
    ]), _results([]))

    curve = read_curves(out)[(2024, "ZZ")]

    assert curve.days[10][1] == 250


def test_completeness_uses_the_snapshot_rule():
    """Maryland, Kentucky, Texas and Tennessee close their windows days early."""
    assert _curve(2022, "MD", {5: 381972}).complete is True
    assert _curve(2022, "SC", {18: 16975}).complete is False
    assert _curve(2024, "AZ", {99: 921671}).complete is False


# --------------------------------------------------------------------------
# The rule that keeps a measured-wrong number out of a forecast
# --------------------------------------------------------------------------
def test_comparable_is_four_years_back_never_two():
    assert comparable_cycle(2026) == 2022
    assert comparable_cycle(2024) == 2020
    assert election_kind(2024) == "presidential"
    assert election_kind(2026) == "midterm"
    assert reference_kind(2026, 2022) == "same-type"
    assert reference_kind(2024, 2022) == "cross-type"


def test_a_cycle_in_progress_never_uses_a_cross_type_reference(tmp_path):
    """The load-bearing refusal. 2026 has a 2024 curve and no 2022 one -> no row."""
    out = _tree(tmp_path, _daily([
        (2024, "ZZ", "2024-10-26", 10, 400),
        (2024, "ZZ", "2024-11-05", 0, 1000),
        (2026, "ZZ", "2026-10-24", 10, 500),
    ]), _results([(2024, "ZZ", "president", 4000)]))

    rows, refused = build(out)

    assert [r for r in rows if r.cycle == 2026] == []
    assert any("2026 ZZ" in r and "in progress" in r for r in refused)


def test_the_cross_type_escape_hatch_is_opt_in(tmp_path):
    out = _tree(tmp_path, _daily([
        (2024, "ZZ", "2024-10-26", 10, 400),
        (2024, "ZZ", "2024-11-05", 0, 1000),
        (2026, "ZZ", "2026-10-24", 10, 500),
    ]), _results([(2024, "ZZ", "president", 4000)]))

    rows, _ = build(out, cross_type=True)
    live = [r for r in rows if r.cycle == 2026]

    assert len(live) == 1
    assert live[0].reference_kind == "cross-type"
    assert live[0].confidence == "low"


def test_a_certified_cycle_may_be_audited_off_any_reference(tmp_path):
    """An audit row carries its own actual and error, so it cannot read as a
    forecast. This is what fills output/turnout.csv today."""
    out = _tree(tmp_path, _daily([
        (2022, "ZZ", "2022-11-08", 0, 500),
        (2024, "ZZ", "2024-11-05", 0, 1000),
    ]), _results([(2022, "ZZ", "senate", 2000),
                  (2024, "ZZ", "president", 4000)]))

    rows, _ = build(out)

    assert len(rows) == 1
    assert rows[0].cycle == 2024
    assert rows[0].reference_kind == "cross-type"
    assert rows[0].actual_turnout == 4000
    assert rows[0].error_pct is not None


def test_a_same_type_reference_is_preferred_over_a_nearer_one(tmp_path):
    out = _tree(tmp_path, _daily([
        (2022, "ZZ", "2022-11-08", 0, 500),
        (2024, "ZZ", "2024-11-05", 0, 1000),
        (2026, "ZZ", "2026-11-03", 0, 600),
    ]), _results([(2022, "ZZ", "senate", 2000),
                  (2024, "ZZ", "president", 4000)]))

    rows, _ = build(out)
    live = [r for r in rows if r.cycle == 2026]

    assert len(live) == 1
    assert live[0].reference.cycle == 2022
    assert live[0].reference_kind == "same-type"


# --------------------------------------------------------------------------
# Confidence
# --------------------------------------------------------------------------
def test_confidence_is_never_high(tmp_path):
    out = _tree(tmp_path, _daily([
        (2022, "ZZ", "2022-10-29", 10, 400),
        (2022, "ZZ", "2022-10-30", 9, 500),
        (2022, "ZZ", "2022-10-31", 8, 600),
        (2022, "ZZ", "2022-11-01", 7, 1000),
        (2026, "ZZ", "2026-10-24", 10, 800),
    ]), _results([(2022, "ZZ", "senate", 4000)]))

    rows, _ = build(out)

    assert rows
    assert {r.confidence for r in rows} <= {"low", "medium"}
    assert all(r.to_dict()["confidence"] != "high" for r in rows)


def test_a_single_day_reference_curve_is_low_confidence():
    ref = _reference({0: 1000}, early=1000, total=4000)
    got = project_day(2026, "ZZ", date(2026, 11, 3), 900, ref, 0)

    assert ref.curve.n_days < turnout.MIN_REFERENCE_DAYS
    assert got.confidence == "low"
    assert got.to_dict()["reference_days"] == "1"


# --------------------------------------------------------------------------
# Publishing
# --------------------------------------------------------------------------
def test_write_touches_only_its_own_file(tmp_path):
    out = tmp_path / "output"
    (out / "counties").mkdir(parents=True)
    (out / "ev_state_daily.csv").write_text("cycle,state\n2024,NC\n", encoding="utf-8")
    (out / "results_state.csv").write_text("cycle,state\n2024,NC\n", encoding="utf-8")
    (out / "ev_state_meta.csv").write_text("state\nNC\n", encoding="utf-8")
    (out / "party_estimate.csv").write_text("cycle,state\n2024,NC\n", encoding="utf-8")
    (out / "regression.csv").write_text("spec\nx\n", encoding="utf-8")
    (out / "counterfactual.csv").write_text("cycle\n2024\n", encoding="utf-8")
    (out / "counties" / "nc.csv").write_text("cycle\n2024\n", encoding="utf-8")

    before = _hash_tree(out)

    ref = _reference({0: 1000}, early=1000, total=4000)
    row = project_day(2026, "ZZ", date(2026, 11, 3), 900, ref, 0)
    write(out, [row])

    after = _hash_tree(out)
    assert {k for k in after if before.get(k) != after[k]} == {"turnout.csv"}
    assert set(before) - set(after) == set()


def test_the_published_table_never_carries_a_reported_count_column():
    assert not (turnout.FORBIDDEN_COLUMNS & set(turnout.TURNOUT_COLUMNS))
    for column in ("ballots_total", "party_dem", "mail_returned", "inperson"):
        assert column in turnout.FORBIDDEN_COLUMNS
        assert column not in turnout.TURNOUT_COLUMNS


def test_write_refuses_a_forbidden_column(monkeypatch, tmp_path):
    monkeypatch.setattr(turnout, "TURNOUT_COLUMNS",
                        turnout.TURNOUT_COLUMNS + ["party_dem"])
    with pytest.raises(TurnoutError):
        write(tmp_path, [])


def test_every_published_column_is_written(tmp_path):
    out = tmp_path / "output"
    out.mkdir()
    ref = _reference({0: 1000}, early=1000, total=4000)
    write(out, [project_day(2026, "ZZ", date(2026, 11, 3), 900, ref, 0)])

    with (out / "turnout.csv").open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))

    assert len(rows) == 1
    assert list(rows[0]) == turnout.TURNOUT_COLUMNS
    assert rows[0]["dims_used"] == turnout.DIMS_USED
    assert rows[0]["method"] == turnout.METHOD


# --------------------------------------------------------------------------
# The roll-up
# --------------------------------------------------------------------------
def test_the_rollup_says_how_many_states_it_covers(tmp_path):
    out = _tree(tmp_path, _daily([
        (2022, "AA", "2022-11-08", 0, 500), (2024, "AA", "2024-11-05", 0, 900),
        (2022, "BB", "2022-11-08", 0, 400), (2024, "BB", "2024-11-05", 0, 800),
    ]), _results([(2022, "AA", "senate", 2000), (2024, "AA", "president", 3000),
                  (2022, "BB", "senate", 1000), (2024, "BB", "president", 2000)]))

    rows, _ = build(out)
    total = rollup(rows, 2024, tracked=["AA", "BB", "CC", "DD"])

    assert total["states_included"] == 2
    assert total["states_tracked"] == 4
    assert total["states"] == ["AA", "BB"]
    assert total["reference_turnout"] == 3000
    assert total["projected_lo"] < total["projected_turnout"] < total["projected_hi"]


def test_the_rollup_is_none_when_nothing_qualifies():
    assert rollup([], 2026) is None


def test_the_rollup_takes_each_states_latest_day_only(tmp_path):
    out = _tree(tmp_path, _daily([
        (2022, "AA", "2022-11-04", 4, 400), (2022, "AA", "2022-11-08", 0, 500),
        (2024, "AA", "2024-11-01", 4, 700), (2024, "AA", "2024-11-05", 0, 900),
    ]), _results([(2022, "AA", "senate", 2000), (2024, "AA", "president", 3000)]))

    rows, _ = build(out)
    total = rollup(rows, 2024)

    assert len(rows) == 2
    assert total["states_included"] == 1
    assert total["ballots_so_far"] == 900


# --------------------------------------------------------------------------
# Validation protocol
# --------------------------------------------------------------------------
def test_the_uniform_null_is_computed_leave_one_state_out(tmp_path):
    """Move one state's certified turnout and ANOTHER state's uniform null must
    move. It could not if the national ratio were fitted in sample."""
    daily = _daily([
        (2022, "AA", "2022-11-08", 0, 500), (2024, "AA", "2024-11-05", 0, 900),
        (2022, "BB", "2022-11-08", 0, 400), (2024, "BB", "2024-11-05", 0, 800),
        (2022, "CC", "2022-11-08", 0, 300), (2024, "CC", "2024-11-05", 0, 700),
    ])
    base = [(2022, "AA", "senate", 2000), (2024, "AA", "president", 3000),
            (2022, "BB", "senate", 1000), (2024, "BB", "president", 2000),
            (2022, "CC", "senate", 1500), (2024, "CC", "president", 2500)]
    out_a = _tree(tmp_path / "a", daily, _results(base))
    moved = [r if r[1] != "CC" or r[0] != 2024 else (2024, "CC", "president", 4000)
             for r in base]
    out_b = _tree(tmp_path / "b", daily, _results(moved))

    a = {s.state: s for s in validate(out_a)}
    b = {s.state: s for s in validate(out_b)}

    assert a["AA"].mae_model == pytest.approx(b["AA"].mae_model)
    assert a["AA"].mae_null_uniform != pytest.approx(b["AA"].mae_null_uniform)


def test_the_model_has_no_pooled_parameter_so_leaving_a_state_out_changes_it(tmp_path):
    """...nothing. Stated as a test because it is the reason leave-one-state-out
    is not the protocol that protects this model -- the nulls are."""
    daily = _daily([
        (2022, "AA", "2022-11-08", 0, 500), (2024, "AA", "2024-11-05", 0, 900),
        (2022, "BB", "2022-11-08", 0, 400), (2024, "BB", "2024-11-05", 0, 800),
    ])
    results_csv = _results([
        (2022, "AA", "senate", 2000), (2024, "AA", "president", 3000),
        (2022, "BB", "senate", 1000), (2024, "BB", "president", 2000)])
    out = _tree(tmp_path, daily, results_csv)

    both = {s.state: s.mae_model for s in validate(out)}
    alone = {s.state: s.mae_model for s in validate(out, states=["AA"])}

    assert both["AA"] == pytest.approx(alone["AA"])


def test_the_share_drift_is_exactly_the_models_relative_error(tmp_path):
    """The projection is ballots / (coverage x early_share), so its error is the
    ratio of the two cycles' early shares and nothing else."""
    out = _tree(tmp_path, _daily([
        (2022, "AA", "2022-11-08", 0, 500),
        (2024, "AA", "2024-11-05", 0, 1200),
    ]), _results([(2022, "AA", "senate", 2000), (2024, "AA", "president", 3000)]))

    score = validate(out)[0]
    rows, _ = build(out)
    final = [r for r in rows if r.cycle == 2024][-1]

    assert score.share_drift == pytest.approx(1 + (final.error_pct or 0) / 100)


def test_shape_transfer_skips_a_single_day_curve(tmp_path):
    out = _tree(tmp_path, _daily([
        (2022, "AA", "2022-11-08", 0, 500),
        (2024, "AA", "2024-11-05", 0, 900),
    ]), _results([]))

    assert shape_transfer(out) == []


def test_format_validation_says_so_when_nothing_can_be_scored():
    lines = list(format_validation([], ["2022 ME: no certified statewide total"]))
    assert any("no state can be scored" in line for line in lines)


# --------------------------------------------------------------------------
# The finding, pinned against the real published slice
# --------------------------------------------------------------------------
@pytest.fixture(scope="module")
def slice_dir() -> Path:
    return FIXTURES


def test_the_qualifying_states_are_the_four_the_document_names(slice_dir):
    """A midterm reference needs a complete 2022 curve AND a 2022 statewide
    result. Nine states have the curve; four have both."""
    refs, _ = build_references(slice_dir)
    assert sorted(s for c, s in refs if c == 2022) == ["KY", "MD", "NC", "OH"]


def test_the_2022_refusals_are_the_ones_the_document_explains(slice_dir):
    _, refused = build_references(slice_dir)
    text = "\n".join(refused)
    for state in ("ME", "TN", "TX", "VA"):
        assert f"2022 {state}: no certified statewide total" in text
    assert "2022 SC: series stops 18 days out" in text


def test_the_measured_finding_is_pinned(slice_dir):
    """Every headline number in docs/turnout.md, re-derived from the slice."""
    scores = validate(slice_dir, fit_cycle=2022, test_cycle=2024)
    by = {s.state: s for s in scores}

    assert sorted(by) == ["KY", "MD", "NC", "OH"]
    n = len(scores)
    model = sum(s.mae_model for s in scores) / n
    prior = sum(s.mae_null_prior for s in scores) / n
    uniform = sum(s.mae_null_uniform for s in scores) / n

    assert model == pytest.approx(54.2, abs=0.5)
    assert prior == pytest.approx(31.2, abs=0.5)
    assert uniform == pytest.approx(5.2, abs=0.5)
    assert prior - model == pytest.approx(-22.9, abs=0.5)
    assert uniform - model == pytest.approx(-48.9, abs=0.5)

    # The level drift is the whole of the error, and it is large in every state.
    drifts = [s.share_drift for s in scores]
    assert min(drifts) == pytest.approx(1.274, abs=0.01)
    assert max(drifts) == pytest.approx(1.716, abs=0.01)

    # Per state, as a percent of that state's actual turnout.
    assert by["KY"].mae_model == pytest.approx(41.0, abs=0.5)
    assert by["MD"].mae_model == pytest.approx(95.2, abs=0.5)
    assert by["NC"].mae_model == pytest.approx(53.0, abs=0.5)
    assert by["OH"].mae_model == pytest.approx(27.4, abs=0.5)


def test_the_reverse_direction_also_fails_the_uniform_null(slice_dir):
    scores = validate(slice_dir, fit_cycle=2024, test_cycle=2022)
    n = len(scores)
    model = sum(s.mae_model for s in scores) / n
    prior = sum(s.mae_null_prior for s in scores) / n
    uniform = sum(s.mae_null_uniform for s in scores) / n

    assert model == pytest.approx(34.7, abs=0.5)
    # It beats the CRUDE null here -- and all of that is the level of the cycle,
    # exactly the trap docs/regression.md's second null exists to catch.
    assert prior - model == pytest.approx(11.0, abs=0.5)
    assert uniform - model == pytest.approx(-29.4, abs=0.5)


def test_the_shape_transfers_even_though_the_level_does_not(slice_dir):
    """The positive finding, and the reason this failure is specific rather than
    general: the curve is right and the level it is scaled by is not."""
    rows = shape_transfer(slice_dir, fit_cycle=2022, test_cycle=2024)
    by = {r.state: r for r in rows}

    assert sorted(by) == ["MD", "ME", "NC", "TN", "TX"]
    mean_gap = sum(r.mean_gap for r in rows) / len(rows)
    assert mean_gap == pytest.approx(5.4, abs=0.5)
    assert max(r.mean_gap for r in rows) < 10.0
    assert by["ME"].mean_gap == pytest.approx(2.3, abs=0.3)


def test_the_band_does_not_cover_and_the_validation_says_so(slice_dir):
    scores = validate(slice_dir, fit_cycle=2022, test_cycle=2024)
    coverage = sum(s.band_coverage for s in scores) / len(scores)
    assert coverage < 0.7


def test_model_error_matches_the_measured_validation(slice_dir):
    measured = measured_model_error(validate(slice_dir))
    assert measured is not None
    assert abs(measured - MODEL_ERROR) <= MODEL_ERROR_TOLERANCE


def test_nothing_beats_the_null_which_is_what_the_document_says(slice_dir):
    """The headline claim. If the pace ever buys MIN_GAIN against BOTH nulls in
    either direction, this fails and docs/turnout.md has to be rewritten rather
    than inherited."""
    for fit, test in ((2022, 2024), (2024, 2022)):
        scores = validate(slice_dir, fit_cycle=fit, test_cycle=test)
        n = len(scores)
        model = sum(s.mae_model for s in scores) / n
        prior = sum(s.mae_null_prior for s in scores) / n
        uniform = sum(s.mae_null_uniform for s in scores) / n
        ships = (prior - model) >= MIN_GAIN and (uniform - model) >= MIN_GAIN
        assert not ships, f"fit {fit} -> test {test} now clears the bar"


def test_the_verdict_line_says_nothing_ships(slice_dir):
    lines = list(format_validation(validate(slice_dir)))
    assert any("NOTHING SHIPS" in line for line in lines)


def test_no_2026_row_is_projectable_from_the_published_slice(slice_dir):
    """As of the saved slice, 2026 is three states and eight ballots, and every
    one of them sits below the coverage floor of its own 2022 curve."""
    rows, _ = build(slice_dir)
    assert [r for r in rows if r.cycle == 2026] == []
    assert sorted({r.state for r in rows}) == ["KY", "MD", "NC", "OH"]
    assert all(r.reference_kind == "cross-type" for r in rows)


def test_the_denominator_is_the_race_total_and_prefers_president(slice_dir):
    table = read_turnout(slice_dir)
    assert table[(2024, "NC")] == (5699141, "president")
    assert table[(2022, "NC")] == (3773924, "senate")
    assert (2022, "ME") not in table
