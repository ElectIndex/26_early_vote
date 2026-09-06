"""Tests for the early-vote -> outcome regression.

Two of these carry the load, the same way two of test_estimate.py's do:

  * `test_write_touches_only_its_own_two_files` hashes an output/ tree before and
    after a publish and asserts nothing else moved. A model number in a column
    that means "the state reported this" is the one unrecoverable mistake here.

  * `test_measured_finding_is_pinned` re-derives the headline numbers in
    docs/regression.md from a saved slice of the real published data, so if the
    finding moves, the document fails a test rather than quietly going stale.
"""

from __future__ import annotations

import csv
import hashlib
import math
from pathlib import Path

import pytest

from ev import regress
from ev.regress import (
    Case, Collinear, NotEnoughData, Series, Spec,
    build_cases, leave_one_out, ols, prior_cycle, score, two_party_margin,
)

FIXTURES = Path(__file__).parent / "fixtures" / "regress"


# --------------------------------------------------------------------------
# The arithmetic
# --------------------------------------------------------------------------
def test_ols_reproduces_a_hand_computed_line():
    """y = 1 + 2x exactly, so the fit must return exactly that with zero error."""
    xs = [0.0, 1.0, 2.0, 3.0, 4.0]
    design = [[1.0, x] for x in xs]
    y = [1.0 + 2.0 * x for x in xs]

    fit = ols(design, y, ["intercept", "x"])

    assert fit.coef[0] == pytest.approx(1.0)
    assert fit.coef[1] == pytest.approx(2.0)
    assert fit.rss == pytest.approx(0.0, abs=1e-18)
    assert fit.r2 == pytest.approx(1.0)
    assert fit.mae == pytest.approx(0.0, abs=1e-9)


def test_ols_matches_the_closed_form_slope_on_noisy_data():
    """Slope = cov(x, y) / var(x). Written out because nothing here imports it."""
    xs = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
    y = [2.0, 4.1, 5.9, 8.3, 9.8, 12.2]
    mx, my = sum(xs) / len(xs), sum(y) / len(y)
    slope = (sum((a - mx) * (b - my) for a, b in zip(xs, y))
             / sum((a - mx) ** 2 for a in xs))

    fit = ols([[1.0, x] for x in xs], y, ["intercept", "x"])

    assert fit.coef[1] == pytest.approx(slope)
    assert fit.coef[0] == pytest.approx(my - slope * mx)
    # Standard error of the slope, the textbook form.
    sigma2 = fit.rss / (len(xs) - 2)
    expected_se = math.sqrt(sigma2 / sum((a - mx) ** 2 for a in xs))
    assert fit.std_error[1] == pytest.approx(expected_se)


def test_ols_intercept_only_has_zero_r2_and_the_mean_as_its_coefficient():
    y = [3.0, -1.0, 5.0, 1.0]
    fit = ols([[1.0]] * len(y), y, ["intercept"])
    assert fit.coef[0] == pytest.approx(sum(y) / len(y))
    assert fit.r2 == pytest.approx(0.0)


def test_ols_refuses_fewer_rows_than_parameters():
    with pytest.raises(NotEnoughData):
        ols([[1.0, 1.0], [1.0, 2.0]], [1.0, 2.0], ["intercept", "x"])


def test_ols_refuses_a_collinear_design_rather_than_inventing_an_answer():
    """Two identical predictors have no unique coefficients. Raise, never fudge."""
    design = [[1.0, x, x] for x in (1.0, 2.0, 3.0, 4.0, 5.0)]
    with pytest.raises(Collinear):
        ols(design, [1.0, 2.0, 3.0, 4.0, 5.0], ["intercept", "x", "x_again"])


def test_leave_one_out_really_holds_the_row_out():
    """One outlier cannot rescue its own prediction."""
    xs = [0.0, 1.0, 2.0, 3.0, 4.0, 5.0]
    y = [0.0, 1.0, 2.0, 3.0, 4.0, 40.0]     # the last point is the outlier
    design = [[1.0, x] for x in xs]

    in_sample = ols(design, y, ["intercept", "x"])
    loo = leave_one_out(design, y, ["intercept", "x"])

    assert len(loo) == len(y)
    # With the outlier held out the five remaining points are exactly y = x, so
    # its own prediction is 5 -- the fit has no way to bend toward it.
    assert loo[-1] == pytest.approx(5.0)
    # Its held-out error is therefore far bigger than its in-sample residual.
    # That gap is the whole reason LOO is the number reported.
    assert abs(y[-1] - loo[-1]) > abs(y[-1] - in_sample.fitted[-1])


def test_leave_one_out_reports_a_gap_not_a_zero_when_the_reduced_fit_fails():
    design = [[1.0, x] for x in (1.0, 2.0, 3.0, 4.0)]
    loo = leave_one_out(design, [1.0, 2.0, 3.0, 4.0], ["intercept", "x"])
    assert len(loo) == 4
    assert all(v is not None for v in loo)

    # Three rows and two parameters: every reduced fit has two rows and cannot
    # be made. None, never 0 -- a missing prediction is not a prediction of zero.
    short = leave_one_out([[1.0, 1.0], [1.0, 2.0], [1.0, 3.0]],
                          [1.0, 2.0, 3.0], ["intercept", "x"])
    assert short == [None, None, None]


# --------------------------------------------------------------------------
# THE BLANK RULE, on the read side
# --------------------------------------------------------------------------
def test_no_party_registration_is_blank_not_a_fifty_fifty_split():
    """Georgia registers nobody by party. That is None, never 0."""
    assert two_party_margin({"party_dem": "", "party_rep": ""}) is None
    assert two_party_margin({"party_dem": "1000", "party_rep": ""}) is None
    assert two_party_margin(None) is None


def test_a_genuine_tie_is_zero_because_zero_is_a_real_answer_here():
    assert two_party_margin({"party_dem": "500", "party_rep": "500"}) == 0.0
    assert two_party_margin({"party_dem": "600", "party_rep": "400"}) == pytest.approx(20.0)


def test_mail_share_is_blank_when_the_state_splits_no_methods():
    assert regress.mail_share({"mail_returned": "", "inperson": ""}) is None
    assert regress.mail_share({"mail_returned": "50", "inperson": "50"}) == 50.0


# --------------------------------------------------------------------------
# Cross-cycle alignment is by days-to-election, never by date
# --------------------------------------------------------------------------
def _series(cycle, state, rows):
    s = Series(cycle, state)
    for dte, payload in rows.items():
        s.days[dte] = {"days_to_election": str(dte), **payload}
    return s


def test_series_matches_on_days_out_and_ignores_the_calendar():
    """2022-11-01 is 7 days out; 2024-11-01 is 4. They must not be paired."""
    prior = _series(2022, "NC", {
        7: {"date": "2022-11-01", "ballots_total": "100"},
        0: {"date": "2022-11-08", "ballots_total": "900"},
    })
    matched = prior.at(7, tolerance=0)
    assert matched is not None and matched["date"] == "2022-11-01"

    # The same CALENDAR day in the other cycle is 4 days out and must miss.
    assert prior.at(4, tolerance=0) is None


def test_series_match_takes_the_closest_day_inside_the_tolerance():
    prior = _series(2022, "IA", {
        9: {"ballots_total": "10"},
        6: {"ballots_total": "20"},
        2: {"ballots_total": "30"},
    })
    assert prior.at(5, tolerance=3)["ballots_total"] == "20"
    # Six days out is one day from five, so a one-day tolerance still matches it
    # and a zero-day tolerance -- an exact days-out join -- matches nothing.
    assert prior.at(5, tolerance=1)["ballots_total"] == "20"
    assert prior.at(5, tolerance=0) is None


def test_the_cross_cycle_match_never_reaches_past_election_day():
    """Last cycle's canvass is not last cycle's Election-Day figure.

    A post-election row counts mail that arrived after the polls closed. Matching
    this cycle's Election Day against it would compare a live count to a
    certified one and report the difference as pace.
    """
    prior = _series(2022, "CO", {
        2: {"ballots_total": "1000"},
        -7: {"ballots_total": "1900"},
    })
    assert prior.at(0)["ballots_total"] == "1000"
    assert prior.at(-7) is None


def test_a_series_that_stops_weeks_out_has_no_usable_snapshot():
    """South Carolina 2022 stops eighteen days out; Arizona's archive is July.

    Both hold real ballots, at a point in the campaign no other state in the
    cross-section is at. The composition of returned ballots moves by tens of
    points across a window, so the honest answer is no snapshot at all.
    """
    partial = _series(2022, "SC", {18: {"ballots_total": "16975"}})
    assert partial.final_dte == 18
    assert partial.usable_snapshot is False
    assert partial.final_row() is None

    complete = _series(2022, "NC", {
        3: {"ballots_total": "2000000"},
        0: {"ballots_total": "2187856"},
    })
    assert complete.usable_snapshot is True
    assert complete.final_row()["ballots_total"] == "2187856"

    # Maryland's early voting genuinely CLOSES five days out. That is its finish
    # line, not a truncated scrape, and it stays in.
    maryland = _series(2022, "MD", {5: {"ballots_total": "381972"}})
    assert maryland.usable_snapshot is True

    # A post-election restatement is never the snapshot: it counts mail that
    # arrived after Election Day, which is not what the tracker shows a reader.
    with_late = _series(2024, "CO", {
        5: {"ballots_total": "1731171"},
        -7: {"ballots_total": "3276257"},
    })
    assert with_late.final_dte == 5
    assert with_late.final_row()["ballots_total"] == "1731171"


def test_prior_cycle_offsets_are_by_office_not_a_flat_two_years():
    assert prior_cycle(2024, "president") == 2020
    assert prior_cycle(2022, "senate") == 2016      # the same Senate class
    assert prior_cycle(2024, "senate") == 2018
    assert prior_cycle(2024, "nonsense") is None
    # The early-vote comparison is a different axis: the previous GENERAL, which
    # is the only other curve this repo holds.
    assert regress.prior_cycle_for_ev(2024) == 2022


# --------------------------------------------------------------------------
# Case assembly drops rather than imputes
# --------------------------------------------------------------------------
def _write(path: Path, columns, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore",
                           lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def _minimal_tree(tmp_path: Path, daily_rows, result_rows) -> Path:
    from ev.results import RESULTS_COLUMNS
    from ev.schema import STATE_DAILY_COLUMNS

    out = tmp_path / "output"
    _write(out / "ev_state_daily.csv", STATE_DAILY_COLUMNS, daily_rows)
    _write(out / "results_state.csv", RESULTS_COLUMNS, result_rows)
    return out


def _daily(cycle, state, dte, **kw):
    row = {"cycle": cycle, "state": state, "days_to_election": dte,
           "date": "", "ballots_total": "", "source_tier": "1",
           "source_name": "test"}
    row.update(kw)
    return row


def _result(cycle, state, office, margin, total="1000000"):
    return {"cycle": cycle, "state": state, "office": office,
            "margin": margin, "total_votes": total}


def test_a_blank_margin_is_dropped_and_named(tmp_path):
    """Alaska 2022 has no party for anyone, so it has no margin. Not a zero."""
    out = _minimal_tree(
        tmp_path,
        [_daily(2022, "AK", 0, ballots_total="100000")],
        [_result(2022, "AK", "senate", "")],
    )
    cases, skipped = build_cases(out, anchors={("senate", 2016): {"AK": -15.0}})
    assert cases == []
    assert any("AK" in s and "no published margin" in s for s in skipped)


def test_a_state_with_no_prior_result_is_dropped_and_named(tmp_path):
    out = _minimal_tree(
        tmp_path,
        [_daily(2024, "XX", 0, ballots_total="10")],
        [_result(2024, "XX", "president", "5")],
    )
    cases, skipped = build_cases(out, anchors={("president", 2020): {}})
    assert cases == []
    assert any("no 2020 result to swing from" in s for s in skipped)


def test_a_state_with_no_early_vote_series_is_dropped_and_named(tmp_path):
    out = _minimal_tree(tmp_path, [], [_result(2024, "WY", "president", "-45")])
    cases, skipped = build_cases(out, anchors={("president", 2020): {"WY": -43.0}})
    assert cases == []
    assert any("no early-vote series" in s for s in skipped)


def test_the_target_is_the_swing_against_the_prior_comparable_result(tmp_path):
    out = _minimal_tree(
        tmp_path,
        [_daily(2024, "MI", 0, ballots_total="400000", mail_returned="300000",
                inperson="100000")],
        [_result(2024, "MI", "president", "-1.4", total="5600000")],
    )
    cases, _ = build_cases(out, anchors={("president", 2020): {"MI": 2.8}})
    assert len(cases) == 1
    case = cases[0]
    assert case.target == pytest.approx(-4.2)
    assert case.prior_cycle == 2020
    # Michigan does not register by party: blank, not a fifty-fifty split.
    assert case.features["ev_party_margin"] is None
    assert case.features["mail_share"] == pytest.approx(75.0)
    assert case.features["early_share_of_total"] == pytest.approx(
        100.0 * 400000 / 5600000)


def test_a_series_stopping_weeks_out_contributes_no_features_at_all(tmp_path):
    """The case is kept -- the result is real -- but every feature is blank."""
    out = _minimal_tree(
        tmp_path,
        [_daily(2022, "SC", 18, ballots_total="16975", party_dem="9000",
                party_rep="6000")],
        [_result(2022, "SC", "senate", "-25.9", total="1700000")],
    )
    cases, _ = build_cases(out, anchors={("senate", 2016): {"SC": -13.6}})
    assert len(cases) == 1
    assert all(v is None for v in cases[0].features.values())


def test_a_state_whose_window_closes_early_keeps_its_mix_but_not_its_share(tmp_path):
    """Maryland stops five days out because Maryland stops five days out.

    Its party mix is a real reading of its completed early electorate. Its share
    of the eventual total is not, because nothing in this repo knows Maryland's
    final early-vote count -- and a partial numerator over a final denominator is
    not a share of anything. Blank, not a smaller number.
    """
    out = _minimal_tree(
        tmp_path,
        [_daily(2022, "MD", 5, ballots_total="381972", party_dem="220469",
                party_rep="112083")],
        [_result(2022, "MD", "senate", "31.7", total="1700000")],
    )
    cases, _ = build_cases(out, anchors={("senate", 2016): {"MD": 25.2}})
    assert len(cases) == 1
    assert cases[0].features["early_share_of_total"] is None
    assert cases[0].features["ev_party_margin"] == pytest.approx(
        100.0 * (220469 - 112083) / (220469 + 112083))


def test_a_method_split_that_does_not_add_up_is_refused(tmp_path):
    """North Carolina 2024: 297,034 mail, `inperson = 0`, headline 4,520,768.

    Its one-stop early votes are simply not in the `inperson` column, so 100%
    mail would be a confident wrong number. Blank is the honest answer.
    """
    out = _minimal_tree(
        tmp_path,
        [_daily(2024, "NC", 0, ballots_total="4520768", mail_returned="297034",
                inperson="0")],
        [_result(2024, "NC", "president", "-3.2", total="5699141")],
    )
    cases, _ = build_cases(out, anchors={("president", 2020): {"NC": -1.3}})
    assert len(cases) == 1
    assert cases[0].features["mail_share"] is None


def test_swing_features_align_across_cycles_by_days_to_election(tmp_path):
    out = _minimal_tree(
        tmp_path,
        [
            # 2022 at 7 days out, and a decoy on the same CALENDAR date as the
            # 2024 row, which is a different number of days out.
            _daily(2022, "NV", 7, date="2022-11-01", ballots_total="200000",
                   party_dem="90000", party_rep="80000"),
            _daily(2022, "NV", 4, date="2022-11-04", ballots_total="400000",
                   party_dem="150000", party_rep="200000"),
            _daily(2024, "NV", 4, date="2024-11-01", ballots_total="500000",
                   party_dem="200000", party_rep="220000"),
        ],
        [_result(2024, "NV", "president", "-3.1", total="1500000")],
    )
    cases, _ = build_cases(out, cycles=[2024],
                           anchors={("president", 2020): {"NV": 2.4}})
    assert len(cases) == 1
    now = 100.0 * (200000 - 220000) / 420000
    then = 100.0 * (150000 - 200000) / 350000       # the 2022 row at 4 days out
    assert cases[0].features["ev_party_margin_swing"] == pytest.approx(now - then)
    assert cases[0].features["pace_vs_prior"] == pytest.approx(
        100.0 * 500000 / 400000 - 100.0)


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------
def _case(cycle, state, target, **feats):
    return Case(cycle=cycle, state=state, office="senate",
                actual_margin=target, prior_margin=0.0, prior_cycle=cycle - 6,
                days_to_election=0, features={n: feats.get(n) for n in regress.FEATURES})


def test_a_spec_is_fitted_only_on_the_rows_that_report_its_predictor():
    cases = [_case(2024, "AA", 1.0, ev_party_margin=10.0),
             _case(2024, "BB", 2.0, ev_party_margin=20.0),
             _case(2024, "CC", 3.0, ev_party_margin=30.0),
             _case(2024, "DD", 9.0)]        # no party registration
    got = score(Spec("party", ("ev_party_margin",), "x"), cases)
    assert got is not None
    assert got.fit.n == 3
    assert [c.state for c in got.cases] == ["AA", "BB", "CC"]


def test_the_prior_null_is_predicting_no_swing_at_all():
    cases = [_case(2024, "AA", 4.0, ev_party_margin=1.0),
             _case(2024, "BB", -2.0, ev_party_margin=2.0),
             _case(2024, "CC", 6.0, ev_party_margin=3.0)]
    got = score(Spec("party", ("ev_party_margin",), "x"), cases)
    assert got.mae_null_prior == pytest.approx((4.0 + 2.0 + 6.0) / 3)


def test_an_intercept_only_spec_never_counts_as_beating_the_null():
    """It IS the uniform null. It cannot be evidence about the early vote."""
    cases = [_case(2024, s, 5.0) for s in ("AA", "BB", "CC", "DD")]
    got = score(Spec("uniform", (), "x"), cases)
    assert got is not None
    assert got.gain_vs_null_prior == pytest.approx(5.0)   # a big apparent gain
    assert got.beats_null is False


def test_beats_null_needs_both_nulls_beaten_by_the_stated_margin():
    # A predictor that IS the target: perfect in sample and out of it.
    cases = [_case(2024, f"S{i}", float(i) * 3.0, ev_party_margin=float(i) * 3.0)
             for i in range(-4, 5)]
    got = score(Spec("party", ("ev_party_margin",), "x"), cases)
    assert got.mae_loo == pytest.approx(0.0, abs=1e-9)
    assert got.gain_vs_null_prior > regress.MIN_GAIN
    assert got.gain_vs_null_uniform > regress.MIN_GAIN
    assert got.beats_null is True

    # Pure noise in the predictor: out-of-sample error is WORSE than the null.
    noisy = [_case(2024, f"S{i}", float(i), ev_party_margin=float((i * 7) % 5))
             for i in range(-6, 7)]
    weak = score(Spec("party", ("ev_party_margin",), "x"), noisy)
    assert weak.beats_null is False


# --------------------------------------------------------------------------
# The write guarantee
# --------------------------------------------------------------------------
def _hash_tree(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*")) if p.is_file()
    }


def test_write_touches_only_its_own_two_files(tmp_path):
    out = tmp_path / "output"
    (out / "counties").mkdir(parents=True)
    (out / "ev_state_daily.csv").write_text("cycle,state\n2024,NC\n", encoding="utf-8")
    (out / "results_state.csv").write_text("cycle,state\n2024,NC\n", encoding="utf-8")
    (out / "ev_state_meta.csv").write_text("state\nNC\n", encoding="utf-8")
    (out / "party_estimate.csv").write_text("cycle,state\n2024,NC\n", encoding="utf-8")
    (out / "counties" / "nc.csv").write_text("cycle\n2024\n", encoding="utf-8")

    before = _hash_tree(out)

    cases = [_case(2024, f"S{i}", float(i), ev_party_margin=float(i) * 2)
             for i in range(6)]
    got = score(Spec("party", ("ev_party_margin",), "x"), cases)
    regress.write(out, [got])

    after = _hash_tree(out)
    changed = {k for k in after if before.get(k) != after[k]}
    assert changed == {"regression.csv", "regression_fit.csv"}
    assert set(before) - set(after) == set()


def test_the_published_tables_never_carry_a_reported_party_column():
    """The columns that mean "the state reported this" may not appear here."""
    forbidden = {"party_dem", "party_rep", "party_oth", "party_npa",
                 "ballots_total", "mail_returned", "inperson"}
    assert not forbidden & set(regress.REGRESSION_COLUMNS)
    assert not forbidden & set(regress.FIT_COLUMNS)


def test_the_fit_table_publishes_every_feature_even_the_unused_ones(tmp_path):
    cases = [_case(2024, f"S{i}", float(i), ev_party_margin=float(i) * 2)
             for i in range(6)]
    got = score(Spec("party", ("ev_party_margin",), "x"), cases)
    rows = regress.fit_rows([got])
    assert len(rows) == 6
    for name in regress.FEATURES:
        assert name in rows[0]
    # An unreported feature is an empty cell, never a zero.
    assert rows[0]["mail_share"] == ""
    assert rows[0]["null_predicted"] == "0"


def test_an_empty_run_produces_no_rows_so_the_cli_can_refuse_to_write():
    """Nothing fitted must not become an empty file where real rows were.

    `cmd_regress` guards on `if not scores` before calling `write` -- the same
    guard `cmd_estimate` keeps for `party_estimate.csv`. These two assertions
    are what makes that guard sufficient.
    """
    assert regress.regression_rows([]) == []
    assert regress.fit_rows([]) == []


# --------------------------------------------------------------------------
# The finding, pinned against a saved slice of the real published data
# --------------------------------------------------------------------------
def _fixture_anchors() -> dict[tuple[str, int], dict[str, float]]:
    """The prior-cycle margins, saved so the test needs no network or cache."""
    table: dict[tuple[str, int], dict[str, float]] = {}
    with (FIXTURES / "prior_margins.csv").open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            key = (row["office"], int(row["cycle"]))
            table.setdefault(key, {})[row["state"]] = float(row["margin"])
    return table


@pytest.mark.skipif(not (FIXTURES / "prior_margins.csv").exists(),
                    reason="regression fixtures not present")
def test_measured_finding_is_pinned():
    """The numbers docs/regression.md quotes, re-derived from the real slice.

    The fixture is the whole 2022 and 2024 early-vote backfill (`python -m ev
    backfill --cycle 2022` / `--cycle 2024`) beside the published results table
    and the prior-cycle anchors. If a future data pull moves the finding, this
    fails and the document has to be rewritten -- which is the point.
    """
    cases, _ = build_cases(FIXTURES, anchors=_fixture_anchors())
    assert len(cases) == PINNED_CASES

    scored = {s.spec.name: s for s in
              (score(spec, cases) for spec in regress.SPECS) if s is not None}
    assert set(scored) == set(PINNED)

    for name, expected in PINNED.items():
        got = scored[name]
        assert got.fit.n == expected["n"], name
        assert got.mae_null_prior == pytest.approx(expected["null_prior"], abs=0.05), name
        assert got.mae_null_uniform == pytest.approx(expected["null_uniform"], abs=0.05), name
        assert got.mae_loo == pytest.approx(expected["loo"], abs=0.05), name
        assert got.gain_vs_null_uniform == pytest.approx(
            expected["gain_uniform"], abs=0.05), name


def test_nothing_beats_the_null_which_is_what_the_document_says():
    """The headline claim of docs/regression.md, as an assertion.

    Not one specification beats BOTH nulls by MIN_GAIN out of sample. The best
    any of them manages against "the country swung X, apply it everywhere" is
    +0.07 points of margin -- a tenth of the 0.79 that docs/party-estimate.md
    already judged insufficient to ship. If this ever fails, the early vote has
    started to carry signal and the recommendation has to be revisited.
    """
    cases, _ = build_cases(FIXTURES, anchors=_fixture_anchors())
    scored = [s for s in (score(spec, cases) for spec in regress.SPECS)
              if s is not None]

    assert not any(s.beats_null for s in scored)

    # On the pooled sample not one of them is even positive: the best is mail
    # share, at -0.05 points. The 0.2 bar is deliberately generous -- it is a
    # quarter of the 0.79 that docs/party-estimate.md already judged too little.
    best = max((s.gain_vs_null_uniform for s in scored
                if s.spec.predictors and s.gain_vs_null_uniform is not None))
    assert best < 0.2, f"an early-vote predictor now buys {best:.2f} points"
    assert best < 0.0, "an early-vote predictor now beats the uniform null at all"


#: Pinned in one place so docs/regression.md and this test cannot drift apart.
#: All figures are mean absolute error in PERCENTAGE POINTS of margin.
#:   null_prior   -- "quote this state's last comparable result and stop"
#:   null_uniform -- "the country swung X this year, apply X everywhere"
#:   loo          -- the fit's own leave-one-out error, the honest number
PINNED_CASES = 37

PINNED = {
    "uniform":         {"n": 37, "null_prior": 6.36, "null_uniform": 4.42,
                        "loo": 4.42, "gain_uniform": 0.00},
    "share":           {"n": 21, "null_prior": 6.01, "null_uniform": 4.29,
                        "loo": 4.56, "gain_uniform": -0.27},
    "party":           {"n": 17, "null_prior": 6.49, "null_uniform": 5.16,
                        "loo": 5.50, "gain_uniform": -0.34},
    "party_swing":     {"n": 5, "null_prior": 6.60, "null_uniform": 8.39,
                        "loo": 8.77, "gain_uniform": -0.38},
    "mail":            {"n": 22, "null_prior": 6.21, "null_uniform": 4.23,
                        "loo": 4.27, "gain_uniform": -0.05},
    "mail_swing":      {"n": 8, "null_prior": 5.35, "null_uniform": 3.35,
                        "loo": 3.84, "gain_uniform": -0.49},
    "pace":            {"n": 13, "null_prior": 7.49, "null_uniform": 5.19,
                        "loo": 5.85, "gain_uniform": -0.65},
    "party_and_share": {"n": 10, "null_prior": 5.60, "null_uniform": 4.75,
                        "loo": 5.64, "gain_uniform": -0.89},
}
