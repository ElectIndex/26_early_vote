#!/usr/bin/env python3
"""Regenerate data/crosswalk/county_cd_weights.csv from the House model's
county x congressional-district segments.

    python3 scripts/build_cd_weights.py ~/ElectIndex/forecasts/usa/data/county_segments.csv

The source is the forecast model's `county_segments.csv` -- one row per piece
of a county that lies inside one congressional district, carrying that piece's
2024 presidential vote (`votes_total`) and population. It is built from the
block-level assignments behind electindex.com/forecasts, on the maps in force
for the 2026 election (the 2025 redraws in Texas, California, Missouri, North
Carolina, Ohio and Utah included), so the weights here describe the districts
the page draws and not the ones that existed in 2024.

WHAT A WEIGHT MEANS. `weight` is the share of a county's 2024 presidential
vote cast inside each district, and it sums to exactly 1.000000 across the
county's rows. `ev.districts.apportion` multiplies a county's early-vote count
by it to split that count across the districts the county touches. A county
wholly inside one district has one row with weight 1 -- which is most of them.

Three rules, all decided here so the pipeline never has to:

* The weight is VOTES, not population. The thing being split is ballots, and
  the best available guess at where a county's ballots come from is where its
  votes came from last time. A county whose segments report no 2024 vote at
  all (none do today) falls back to population, then to an equal split, and
  the fallback is named in the `basis_note` column so it can be found.
* The District of Columbia is dropped. It has no House seat; "DC-00" is the
  model's delegate placeholder and the page draws no district for it.
* Rows are written with six decimals and the LAST row of each county absorbs
  the rounding so the weights sum to one exactly. `apportion` relies on that
  to make district counts sum back to the county count.
* Each row also carries the segment's own 2024 presidential vote
  (`votes_dem`, `votes_rep`, `votes_total`) -- the piece of the county inside
  that district. `ev.counterfactual_districts` values a district's early
  electorate in 2024 presidential points with these, exactly as the state
  counterfactual values a state's with the certified county returns. They are
  the block-level sums behind the House forecast, not certified returns, and
  they sum to within a fraction of a point of the certified county totals.

The file is vendored rather than read live from the forecasts checkout for the
same reason `_towns.py` vendors the Census table: the pipeline owns its own
reference data, CI has no forecasts checkout, and a crosswalk that changes
should change in a commit that says so.
"""

from __future__ import annotations

import csv
import sys
from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "data" / "crosswalk" / "county_cd_weights.csv"
COLUMNS = ["state", "county_fips", "cd_code", "weight", "votes_dem", "votes_rep",
           "votes_total", "basis_note"]
PLACES = Decimal("0.000001")


def _num(raw: str) -> int:
    raw = (raw or "").strip()
    return int(float(raw)) if raw else 0


def build(source: Path) -> list[dict[str, str]]:
    segments: dict[tuple[str, str], list[tuple[str, int, int]]] = defaultdict(list)
    with source.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            state = row["state"].strip().upper()
            if state == "DC":
                continue
            fips = row["county_fips"].strip().zfill(5)
            cd = row["cd_code"].strip().upper()
            segments[(state, fips)].append((
                cd, _num(row["votes_total"]), _num(row["pop_tot"]),
                _num(row["votes_dem"]), _num(row["votes_rep"]),
            ))

    out: list[dict[str, str]] = []
    for (state, fips), pieces in sorted(segments.items()):
        # One row per district even if the model split a county into several
        # pieces of the same district.
        by_cd: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0, 0])
        for cd, votes, pop, dem, rep in pieces:
            by_cd[cd][0] += votes
            by_cd[cd][1] += pop
            by_cd[cd][2] += dem
            by_cd[cd][3] += rep
        votes_total = sum(v[0] for v in by_cd.values())
        pop_total = sum(v[1] for v in by_cd.values())
        if votes_total > 0:
            shares = {cd: Decimal(v[0]) / Decimal(votes_total) for cd, v in by_cd.items()}
            note = ""
        elif pop_total > 0:
            shares = {cd: Decimal(v[1]) / Decimal(pop_total) for cd, v in by_cd.items()}
            note = "population"
        else:
            shares = {cd: Decimal(1) / Decimal(len(by_cd)) for cd in by_cd}
            note = "equal"

        cds = sorted(shares)
        rounded = {cd: shares[cd].quantize(PLACES, rounding=ROUND_HALF_UP) for cd in cds}
        # The last district absorbs the rounding so the county sums to one.
        rounded[cds[-1]] = Decimal(1) - sum(rounded[cd] for cd in cds[:-1])
        for cd in cds:
            out.append({
                "state": state, "county_fips": fips, "cd_code": cd,
                "weight": f"{rounded[cd]:.6f}",
                "votes_dem": str(by_cd[cd][2]), "votes_rep": str(by_cd[cd][3]),
                "votes_total": str(by_cd[cd][0]), "basis_note": note,
            })
    return out


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print((__doc__ or "").strip().splitlines()[0], file=sys.stderr)
        print(f"usage: {argv[0]} <county_segments.csv>", file=sys.stderr)
        return 2
    rows = build(Path(argv[1]))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    states = {r["state"] for r in rows}
    counties = {(r["state"], r["county_fips"]) for r in rows}
    cds = {r["cd_code"] for r in rows}
    print(f"wrote {OUT}: {len(rows)} rows, {len(counties)} counties, "
          f"{len(cds)} districts, {len(states)} states")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
