"""Command line entry point.

    python -m ev ingest                 # every tracked state, cycle 2026, today
    python -m ev ingest --state NC GA   # just these
    python -m ev ingest --dry-run       # fetch and report, write nothing
    python -m ev backfill --cycle 2022  # archived daily history
    python -m ev probe                  # which adapters exist and answer today
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from . import publish
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
    per_state_demo: dict[str, list] = {}

    for state in states:
        result, outcome = run_state(state, ladder(state), args.cycle, as_of)
        outcomes.append(outcome)
        if not outcome.ok:
            continue
        combined.state_rows.extend(result.state_rows)
        if result.county_rows:
            per_state_county.setdefault(state, []).extend(result.county_rows)
        if result.demo_rows:
            per_state_demo.setdefault(state, []).extend(result.demo_rows)

    ok = [o for o in outcomes if o.ok]
    pending = [o for o in outcomes if o.status == STATUS_PENDING]
    failed = [o for o in outcomes if o.status not in (STATUS_OK, STATUS_PENDING)]

    if args.dry_run:
        print(f"DRY RUN  as_of={as_of}  cycle={args.cycle}")
    else:
        if combined.state_rows:
            files.append(publish.publish_state_daily(out_dir, combined.state_rows))
        for state, rows in sorted(per_state_county.items()):
            files.append(publish.publish_county_daily(out_dir, state, rows))
        for state, rows in sorted(per_state_demo.items()):
            files.append(publish.publish_demo_daily(out_dir, state, rows))

        meta = publish.publish_state_meta(out_dir, STATE_META)
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
        })

    print(f"ok={len(ok)} pending={len(pending)} failed={len(failed)} "
          f"state_rows={len(combined.state_rows)}")
    for o in failed:
        print(f"  FAILED {o.state}: {o.message}", file=sys.stderr)

    # Pending states are the normal case before October and must not fail CI.
    # Only a state that had a source and could not use ANY tier is an error.
    return 1 if failed and args.strict else 0


def cmd_backfill(args) -> int:
    out_dir = Path(args.output or OUTPUT_DIR)
    combined = FetchResult()
    per_state_county: dict[str, list] = {}
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

    pr = common(sub.add_parser("probe", help="report which adapters answer"))
    pr.add_argument("--as-of", help="YYYY-MM-DD (default today)")
    pr.set_defaults(func=cmd_probe)
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
