"""Command line entry point.

    python -m ev ingest                 # every tracked state, cycle 2026, today
    python -m ev ingest --state NC GA   # just these
    python -m ev ingest --dry-run       # fetch and report, write nothing
    python -m ev backfill --cycle 2022  # archived daily history
    python -m ev probe                  # which adapters exist and answer today
    python -m ev estimate               # MODELLED party split -> party_estimate.csv
    python -m ev estimate --validate    # ...and how wrong it is. Read the docs.
    python -m ev regress                # early vote -> result, scored against the
                                        # null model. Read docs/regression.md.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from . import publish, results
from .adapters import _towns
from .adapters.base import FetchResult, NotYetPublished
from .calendar import CURRENT_CYCLE, CYCLES, days_to_election
from .ladder import STATUS_OK, STATUS_PENDING, run_state
from .registry import ladder, tracked_states

ROOT = Path(__file__).resolve().parents[2]
OUTPUT_DIR = ROOT / "output"
STATE_META = ROOT / "data" / "meta" / "states.csv"

log = logging.getLogger("ev")


def _states(argv_states: list[str] | None) -> list[str]:
    return [s.upper() for s in argv_states] if argv_states else list(tracked_states())


def cmd_ingest(args) -> int:
    as_of = (
        datetime.strptime(args.as_of, "%Y-%m-%d").date() if args.as_of else date.today()
    )
    out_dir = Path(args.output or OUTPUT_DIR)
    states = _states(args.state)

    outcomes, files = [], []
    combined = FetchResult()
    per_state_county: dict[str, list] = {}
    per_state_town: dict[str, list] = {}
    per_state_demo: dict[str, list] = {}

    for state in states:
        result, outcome = run_state(state, ladder(state), args.cycle, as_of)
        outcomes.append(outcome)
        if not outcome.ok:
            continue
        combined.state_rows.extend(result.state_rows)
        if result.county_rows:
            per_state_county.setdefault(state, []).extend(result.county_rows)
        # Municipality rows, for the states whose towns are the unit that runs
        # elections. They ride on the FetchResult as an attribute rather than a
        # field; see _towns.attach.
        town_rows = _towns.rows_of(result)
        if town_rows:
            per_state_town.setdefault(state, []).extend(town_rows)
        if result.demo_rows:
            per_state_demo.setdefault(state, []).extend(result.demo_rows)

    ok = [o for o in outcomes if o.ok]
    pending = [o for o in outcomes if o.status == STATUS_PENDING]
    failed = [o for o in outcomes if o.status not in (STATUS_OK, STATUS_PENDING)]

    if args.dry_run:
        print(f"DRY RUN  as_of={as_of}  cycle={args.cycle}")
        for state, rows in sorted(per_state_county.items()):
            print(f"  would write counties/{state.lower()}.csv  {len(rows)} rows")
        for state, rows in sorted(per_state_town.items()):
            print(f"  would write towns/{state.lower()}.csv     {len(rows)} rows")
        for state, rows in sorted(per_state_demo.items()):
            print(f"  would write demo/{state.lower()}.csv      {len(rows)} rows")
    else:
        if combined.state_rows:
            files.append(publish.publish_state_daily(out_dir, combined.state_rows))
        for state, rows in sorted(per_state_county.items()):
            files.append(publish.publish_county_daily(out_dir, state, rows))
        for state, rows in sorted(per_state_town.items()):
            files.append(publish.publish_town_daily(out_dir, state, rows))
        for state, rows in sorted(per_state_demo.items()):
            files.append(publish.publish_demo_daily(out_dir, state, rows))

        meta = publish.publish_state_meta(
            out_dir, STATE_META, publish.derive_prior_finals(out_dir)
        )
        if meta:
            files.append(meta)

        # Written unconditionally: a status file that stops advancing is itself
        # the signal the page uses to show a stale badge.
        publish.write_status(out_dir, {
            "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "as_of": as_of.isoformat(),
            "cycle": args.cycle,
            "days_to_election": days_to_election(args.cycle, as_of),
            "states": {
                o.state: {
                    "status": o.status, "tier": o.tier, "source": o.source_name,
                    "rows": o.rows, "message": o.message, "attempts": o.attempts,
                }
                for o in sorted(outcomes, key=lambda o: o.state)
            },
            "summary": {
                "ok": len(ok), "pending": len(pending), "failed": len(failed),
                "files": files,
            },
        }, partial=bool(args.state))

    print(f"ok={len(ok)} pending={len(pending)} failed={len(failed)} "
          f"state_rows={len(combined.state_rows)} "
          f"county_rows={sum(len(r) for r in per_state_county.values())} "
          f"town_rows={sum(len(r) for r in per_state_town.values())}")
    for o in failed:
        print(f"  FAILED {o.state}: {o.message}", file=sys.stderr)

    # Pending states are the normal case before October and must not fail CI.
    # Only a state that had a source and could not use ANY tier is an error.
    return 1 if failed and args.strict else 0


def cmd_backfill(args) -> int:
    out_dir = Path(args.output or OUTPUT_DIR)
    combined = FetchResult()
    per_state_county: dict[str, list] = {}
    per_state_town: dict[str, list] = {}
    per_state_demo: dict[str, list] = {}
    found, absent = [], []

    for state in _states(args.state):
        for adapter in ladder(state):
            try:
                result = adapter.fetch_history(args.cycle)
            except NotYetPublished:
                continue
            except Exception as exc:  # noqa: BLE001
                log.warning("%s: %s history failed: %s", state, adapter.name, exc)
                continue
            if not result:
                continue
            result.stamp(adapter.provenance())
            combined.state_rows.extend(result.state_rows)
            if result.county_rows:
                per_state_county.setdefault(state, []).extend(result.county_rows)
            town_rows = _towns.rows_of(result)
            if town_rows:
                per_state_town.setdefault(state, []).extend(town_rows)
            if result.demo_rows:
                per_state_demo.setdefault(state, []).extend(result.demo_rows)
            found.append(f"{state}:{adapter.name}")
            break
        else:
            absent.append(state)

    if not args.dry_run:
        if combined.state_rows:
            publish.publish_state_daily(out_dir, combined.state_rows)
        for state, rows in sorted(per_state_county.items()):
            publish.publish_county_daily(out_dir, state, rows)
        for state, rows in sorted(per_state_town.items()):
            publish.publish_town_daily(out_dir, state, rows)
        for state, rows in sorted(per_state_demo.items()):
            publish.publish_demo_daily(out_dir, state, rows)

    print(f"backfill {args.cycle}: {len(found)} with history, {len(absent)} without")
    if absent:
        # Not a failure. Some states simply have no archived daily file, and the
        # page renders those with finals-only and no curve.
        print("  no daily archive: " + " ".join(absent))
    return 0


def cmd_probe(args) -> int:
    as_of = (
        datetime.strptime(args.as_of, "%Y-%m-%d").date() if args.as_of else date.today()
    )
    for state in _states(args.state):
        rungs = ladder(state)
        tiers = ",".join(f"{a.tier}:{a.name}" for a in rungs) or "(none)"
        _, outcome = run_state(state, rungs, args.cycle, as_of)
        print(f"{state:3s} [{tiers}] -> {outcome.status}"
              + (f" via {outcome.source_name}" if outcome.ok else "")
              + (f" ({outcome.message})" if outcome.message else ""))
    return 0


def cmd_estimate(args) -> int:
    """Modelled party split for the states that publish none.

    Deliberately its own subcommand and not part of the ingest walk. It is cheap
    and deterministic -- a pure function of the CSVs already in output/ -- but
    keeping it separate means the daily job cannot accidentally publish a model
    number, and `ev.estimate` is imported here rather than at module scope so
    the ingest path never even loads it. Read docs/party-estimate.md before
    putting any of its output on a page; the measured error is large.
    """
    from . import estimate as estimator

    out_dir = Path(args.output or OUTPUT_DIR)
    baseline = estimator.load_baseline(args.baseline)

    if args.validate:
        # `scores`, not `results` -- this module imports a `results` module at
        # top level and shadowing it inside a function is a trap for the next edit.
        scores = estimator.validate(out_dir, baseline, states=args.state)
        for line in estimator.format_validation(scores):
            print(line)
        return 0

    rows = estimator.build(
        out_dir, baseline,
        states=args.state,
        cycles=[args.cycle] if args.cycle else None,
        meta_path=STATE_META,
    )
    if args.dry_run:
        print(f"DRY RUN  {len(rows)} estimate row(s), nothing written")
        for row in rows[-10:]:
            d = row.to_dict()
            print(f"  {d['cycle']} {d['state']} {d['date']}  D {d['est_dem_share']} "
                  f"[{d['est_dem_lo']}-{d['est_dem_hi']}]  coverage {d['coverage_share']} "
                  f"  {d['confidence']}")
        return 0

    if not rows:
        # No county data for any unreported-party state is the normal answer
        # before early voting opens. Writing an empty file would replace real
        # rows from a previous run with nothing.
        print("no estimable state-days; party_estimate.csv left untouched")
        return 0

    info = estimator.write(out_dir, rows)
    print(f"party_estimate.csv: {info['rows']} rows ({len(rows)} estimated this run)")
    return 0


def regress_cycles() -> list[int]:
    """Cycles `regress` can fit: the ones with a published result.

    Spelled out here rather than imported from `ev.regress` so that building the
    parser does not load the model module -- the same lazy-import guarantee the
    `estimate` subcommand keeps.
    """
    return list(results.RESULT_CYCLES)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ev", description="ElectIndex early vote ingest")
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("--output", help="output directory (default ./output)")
    sub = p.add_subparsers(dest="command", required=True)

    def common(sp):
        sp.add_argument("--state", nargs="+", help="limit to these states")
        sp.add_argument("--cycle", type=int, default=CURRENT_CYCLE, choices=CYCLES)
        sp.add_argument("--dry-run", action="store_true")
        return sp

    ing = common(sub.add_parser("ingest", help="fetch today's data"))
    ing.add_argument("--as-of", help="YYYY-MM-DD (default today)")
    ing.add_argument("--strict", action="store_true",
                     help="exit non-zero if any state failed every tier")
    ing.set_defaults(func=cmd_ingest)

    back = common(sub.add_parser("backfill", help="fetch a past cycle's daily archive"))
    back.set_defaults(func=cmd_backfill)

    # Static history, NOT part of the six-hourly ingest walk: a certified return
    # cannot change, so re-fetching it every run would be pure noise. Structured
    # like cmd_backfill -- its own subcommand, its own cycles, its own file.
    res = sub.add_parser("results", help="ingest past cycles' actual election results")
    res.add_argument("--cycle", type=int, nargs="+", default=list(results.RESULT_CYCLES),
                     choices=results.RESULT_CYCLES,
                     help="cycles to publish (default: every past cycle)")
    res.add_argument("--office", nargs="+", choices=results.SOURCED_OFFICES,
                     help="limit to these offices")
    res.add_argument("--state", nargs="+", help="limit to these states")
    res.add_argument("--dry-run", action="store_true")
    res.add_argument("--refresh", action="store_true",
                     help="re-download instead of reading the cached copy")
    res.set_defaults(func=results.cmd_results)

    pr = common(sub.add_parser("probe", help="report which adapters answer"))
    pr.add_argument("--as-of", help="YYYY-MM-DD (default today)")
    pr.set_defaults(func=cmd_probe)

    est = sub.add_parser(
        "estimate",
        help="MODELLED party split for states that report none -> "
             "output/party_estimate.csv (never the reported party columns)",
    )
    est.add_argument("--state", nargs="+", help="limit to these states")
    # Unlike ingest, `estimate` defaults to EVERY cycle: it re-derives the whole
    # table from output/ each run, so restricting it to 2026 by default would
    # leave 2022/2024 rows frozen under whatever method wrote them first.
    est.add_argument("--cycle", type=int, default=None, choices=CYCLES)
    est.add_argument("--dry-run", action="store_true")
    est.add_argument("--baseline", help="county partisan baseline CSV "
                                        "(default data/baseline/county_results_2024.csv)")
    est.add_argument("--validate", action="store_true",
                     help="score the method against states that DO report party "
                          "and print the per-state error in percentage points")
    est.set_defaults(func=cmd_estimate)

    # Static analysis over 2022 and 2024, deliberately NOT in the six-hourly
    # ingest walk -- same reasoning as `estimate`: the daily job must not be able
    # to publish a model number, and `ev.regress` is imported inside the
    # dispatcher below so `ingest` never loads it. It writes ONLY
    # output/regression.csv and output/regression_fit.csv.
    reg = sub.add_parser(
        "regress",
        help="fit early-vote features to actual 2022/2024 outcomes and score "
             "the fit OUT OF SAMPLE against the null model -> "
             "output/regression.csv (never a reported column)",
    )
    reg.add_argument("--state", nargs="+", help="limit to these states")
    # Every fittable cycle by default: the table is re-derived from output/ on
    # each run, so restricting it would freeze one cycle under an older fit.
    reg.add_argument("--cycle", type=int, nargs="+", default=None,
                     choices=regress_cycles(),
                     help="cycles to fit (default: every cycle with a result)")
    reg.add_argument("--dry-run", action="store_true")
    reg.add_argument("--refresh", action="store_true",
                     help="re-download the prior-cycle result files instead of "
                          "reading the cached copies")
    reg.add_argument("--cross-cycle", action="store_true",
                     help="also fit on one cycle and test on the other")

    def _regress(args):
        from . import regress as regression

        return regression.cmd_regress(args)

    reg.set_defaults(func=_regress)

    # The COUNTERFACTUAL: what 2024 would have produced if the 2026 early
    # electorate had been the one that turned out, with every group's behaviour
    # frozen at 2024. Like `estimate` and `regress`, it is its own subcommand and
    # NOT part of the six-hourly ingest walk -- the daily job must not be able to
    # publish a model number -- and `ev.counterfactual` is imported inside the
    # dispatcher below so `ingest` never loads it. It writes ONLY
    # output/counterfactual.csv, never a reported column of ev_state_daily.csv.
    cfa = sub.add_parser(
        "counterfactual",
        help="project the 2024 presidential result under the CURRENT early "
             "electorate's composition, compared like-for-like against the same "
             "state's previous early electorate at the same days-to-election -> "
             "output/counterfactual.csv. Read docs/counterfactual.md: it does "
             "not beat the no-change null.",
    )
    cfa.add_argument("--state", nargs="+", help="limit to these states")
    # Every comparable cycle by default, for the same reason `estimate` does it:
    # the table is re-derived from output/ on each run, so restricting it to 2026
    # would freeze the 2024-vs-2022 audit rows under whatever method wrote them.
    cfa.add_argument("--cycle", type=int, nargs="+", default=None, choices=CYCLES,
                     help="target cycles (default: every cycle with a reference)")
    cfa.add_argument("--dry-run", action="store_true")
    cfa.add_argument("--baseline", help="county partisan baseline CSV "
                                        "(default data/baseline/county_results_2024.csv)")
    cfa.add_argument("--validate", action="store_true",
                     help="score the method against the only measured "
                          "compositional change there is -- the reported party "
                          "registration of the same ballots -- and print the "
                          "per-state error in percentage points of margin")

    def _counterfactual(args):
        from . import counterfactual as cf

        return cf.cmd_counterfactual(args)

    cfa.set_defaults(func=_counterfactual)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
