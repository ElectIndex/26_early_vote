"""Merge new rows into the published CSVs and write them atomically.

The third ladder rule lives here: A WORSE TIER NEVER OVERWRITES A BETTER ONE for
the same key. If Tuesday's run scraped North Carolina's voter-level file (tier 1,
with counties, party and demographics) and Wednesday's scrape 502s so civicAPI
answers instead (tier 2, counties and party but no demographics), Wednesday must
not replace Tuesday's rich row with a thin one. A same-or-better tier does
replace, because that is a genuine update -- states restate counts routinely.

The comparison is purely relative -- lower wins -- so inserting a tier is safe
here by construction. Nothing in this module names a tier NUMBER.
"""

from __future__ import annotations

import csv
import json
import logging
import os
import tempfile
from datetime import date
from pathlib import Path
from typing import Callable, Iterable, Sequence

from .schema import (
    COUNTY_DAILY_COLUMNS, COUNTY_KEY, DEMO_DAILY_COLUMNS, DEMO_KEY,
    STATE_DAILY_COLUMNS, STATE_KEY, TOWN_DAILY_COLUMNS, TOWN_KEY,
    county_row_to_dict, demo_row_to_dict, state_row_to_dict, town_row_to_dict,
)

log = logging.getLogger(__name__)

#: Never derive a "final" for the cycle still in progress.
CURRENT_CYCLE_HINT = 2026

#: Columns that are provenance/identity rather than data. Everything else counts
#: toward a row's "richness" for the content-loss guard below.
_NON_DATA_COLUMNS = frozenset({
    "cycle", "state", "county_fips", "county_name", "date", "days_to_election",
    "dimension", "bucket", "restated", "source_tier", "source_name", "retrieved_at",
    # A town row's identity columns. `county_fips` is already listed and is
    # identity here too -- it is a slice of `town_geoid`, not reported data.
    "town_geoid", "town_name",
})


class PublishRefused(RuntimeError):
    """The guard rejected a write. The previous file is left untouched."""


def _populated(row: dict[str, str]) -> int:
    """How many DATA fields this row actually reports.

    This is the content-loss guard's whole basis. A truncated or half-parsed
    download is the failure mode most likely to go unnoticed -- the job still
    exits 0 and the page still renders -- and its signature is not fewer rows
    (the merge is append-only by construction, so row count can never fall) but
    a row that comes back with its party and method columns blank.
    """
    return sum(
        1 for col, val in row.items()
        if col not in _NON_DATA_COLUMNS and val not in ("", None)
    )


def _read(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _key_of(row: dict[str, str], key_cols: Sequence[str]) -> tuple:
    return tuple(row.get(col, "") for col in key_cols)


def _tier_of(row: dict[str, str]) -> int:
    try:
        return int(row.get("source_tier") or 99)
    except ValueError:
        return 99


def _atomic_write(path: Path, columns: Sequence[str], rows: Iterable[dict[str, str]]) -> None:
    """Write via a temp file in the same directory, then rename.

    The rename is atomic on the same filesystem, so a reader (or a killed CI job)
    never sees a half-written CSV -- it sees the old file or the new one.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as fh:
            # LF, not csv's RFC-4180 default of CRLF. These files are committed
            # every six hours by CI and read by git, which would otherwise rewrite
            # the line endings on every touch and turn each run into a whole-file
            # diff instead of the few changed rows.
            writer = csv.DictWriter(fh, fieldnames=list(columns),
                                    extrasaction="ignore", lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _sort_key(columns: Sequence[str]) -> Callable[[dict[str, str]], tuple]:
    def key(row: dict[str, str]) -> tuple:
        return (
            row.get("cycle", ""), row.get("state", ""), row.get("county_fips", ""),
            # Towns sort within their county. Constant "" on every other table,
            # so the existing files' order is unchanged.
            row.get("town_geoid", ""),
            row.get("date", ""), row.get("dimension", ""), row.get("bucket", ""),
        )
    return key


def merge_rows(
    existing: list[dict[str, str]],
    incoming: list[dict[str, str]],
    key_cols: Sequence[str],
    *,
    guard: bool = True,
) -> tuple[list[dict[str, str]], int, int, int]:
    """Merge by key, keeping the better (lower) source_tier on collision.

    On a same-tier collision the incoming row normally wins -- states restate
    counts routinely and that is a real update. The exception is a row that comes
    back reporting FEWER fields than the one on disk: a genuine update does not
    lose columns, so that is a truncated download and the richer row is kept.

    Returns (rows, replaced, kept_better, kept_richer).
    """
    merged: dict[tuple, dict[str, str]] = {_key_of(r, key_cols): r for r in existing}
    replaced = kept_better = kept_richer = 0

    for row in incoming:
        k = _key_of(row, key_cols)
        prior = merged.get(k)
        if prior is None:
            merged[k] = row
            continue

        new_tier, old_tier = _tier_of(row), _tier_of(prior)
        if new_tier > old_tier:
            # A worse tier lost. Keep the richer row we already have.
            kept_better += 1
            continue
        if guard and new_tier == old_tier and _populated(row) < _populated(prior):
            # Same source, fewer reported fields -> truncated fetch, not an update.
            kept_richer += 1
            continue

        merged[k] = row
        replaced += 1

    return list(merged.values()), replaced, kept_better, kept_richer


def mark_restatements(rows: list[dict[str, str]]) -> int:
    """Flag rows where a state's cumulative total fell versus the prior day.

    States do restate -- a county reports a correction, a batch is re-uploaded --
    so a decrease is real data, not a parse error, and must not be dropped. It IS
    worth surfacing, so it gets `restated=1` and the page can annotate it.
    """
    flagged = 0
    by_series: dict[tuple, list[dict[str, str]]] = {}
    for row in rows:
        by_series.setdefault((row.get("cycle"), row.get("state")), []).append(row)

    for series in by_series.values():
        series.sort(key=lambda r: r.get("date", ""))
        prev = None
        for row in series:
            raw = row.get("ballots_total", "")
            if raw == "":
                continue
            try:
                total = int(raw)
            except ValueError:
                continue
            if prev is not None and total < prev and row.get("restated") != "1":
                row["restated"] = "1"
                flagged += 1
            prev = max(prev, total) if prev is not None else total
    return flagged


def publish_table(
    path: Path,
    columns: Sequence[str],
    key_cols: Sequence[str],
    incoming: list[dict[str, str]],
    *,
    guard: bool = True,
    flag_restatements: bool = False,
    replace: bool = False,
) -> dict:
    """Merge `incoming` into the CSV at `path` and write it back atomically.

    `replace=True` writes `incoming` and nothing else. That is WRONG for a
    scraped table -- a state that fails to answer today must not delete what it
    published yesterday, which is the whole reason this function merges -- and
    RIGHT for a derived one, which is a pure function of data already on disk.

    A merged derived table cannot forget. When `counterfactual.py` learned to
    refuse immature days, the 182 rows it had published from them stayed in the
    file forever: the model no longer produced them, so there was nothing to
    replace them with. Only the caller knows whether its rows are the complete
    output of a full rebuild, so only the caller may pass this.
    """
    existing = _read(path)
    if replace:
        merged, replaced, kept_better, kept_richer = list(incoming), 0, 0, 0
        removed = len(existing) - len(
            {tuple(r.get(k, "") for k in key_cols) for r in existing}
            & {tuple(r.get(k, "") for k in key_cols) for r in incoming}
        )
        if removed:
            log.info("%s: rebuilt from scratch, %d stale row(s) dropped",
                     path.name, removed)
    else:
        merged, replaced, kept_better, kept_richer = merge_rows(
            existing, incoming, key_cols, guard=guard
        )

    if flag_restatements:
        mark_restatements(merged)

    before, after = len(existing), len(merged)
    merged.sort(key=_sort_key(columns))
    _atomic_write(path, columns, merged)
    if replace:
        # "+-182 new" is not a sentence. A rebuild's arithmetic is a net change,
        # not an addition, and the merge counters are all structurally zero.
        log.info("%s: %d rows (rebuilt, net %+d)", path.name, after, after - before)
    else:
        log.info("%s: %d rows (+%d new, %d replaced, %d kept better tier, %d kept richer)",
                 path.name, after, after - before, replaced, kept_better, kept_richer)
    return {
        "file": path.name, "rows": after, "added": after - before,
        "replaced": replaced, "kept_better_tier": kept_better,
        "kept_richer": kept_richer,
    }


def publish_state_daily(out_dir: Path, rows, **kw) -> dict:
    return publish_table(
        out_dir / "ev_state_daily.csv", STATE_DAILY_COLUMNS, STATE_KEY,
        [state_row_to_dict(r) for r in rows], flag_restatements=True, **kw,
    )


def publish_county_daily(out_dir: Path, state: str, rows, **kw) -> dict:
    """County data is written per state, lazily fetched by the page.

    A single national county file would be ~17 MB across three cycles; per state
    it is well under 1 MB, matching the theme's existing bg/<st>.json pattern.
    """
    return publish_table(
        out_dir / "counties" / f"{state.lower()}.csv",
        COUNTY_DAILY_COLUMNS, COUNTY_KEY,
        [county_row_to_dict(r) for r in rows], **kw,
    )


def publish_town_daily(out_dir: Path, state: str, rows, **kw) -> dict:
    """Municipality rows, written per state exactly like the county files.

    New England has no county election administration -- the town IS the unit --
    and the strong-MCD states report by township alongside their counties, so
    this table sits BESIDE `counties/<st>.csv` rather than replacing it. The page
    uses it for a county/town switcher on a state's own tab; the national map
    stays county-only and is fed by county rows an adapter rolls up from these,
    which is exact because a cousub GEOID carries its county in digits 3-5.
    """
    return publish_table(
        out_dir / "towns" / f"{state.lower()}.csv",
        TOWN_DAILY_COLUMNS, TOWN_KEY,
        [town_row_to_dict(r) for r in rows], **kw,
    )


def publish_demo_daily(out_dir: Path, state: str, rows, **kw) -> dict:
    return publish_table(
        out_dir / "demo" / f"{state.lower()}.csv",
        DEMO_DAILY_COLUMNS, DEMO_KEY,
        [demo_row_to_dict(r) for r in rows], **kw,
    )


#: The static per-state context table. Hand-maintained in data/meta/states.csv
#: and copied to output/ verbatim, because the site needs it alongside the daily
#: data and fetches everything from one place.
STATE_META_COLUMNS = [
    "state", "name", "election_date", "ev_start_2026", "ev_end_2026",
    "has_party_reg", "dims_available", "ev_2022_total", "ev_2024_total",
    "turnout_2022_total", "turnout_2024_total", "reg_voters_2026", "notes",
]


def derive_prior_finals(out_dir: Path) -> dict[tuple[str, str], str]:
    """Each state's FINAL early-vote total for a past cycle, from our own data.

    Returns {(cycle, state): total} — but ONLY for a series that actually reaches
    Election Day AND reports a figure on it. That condition is the whole safety of
    this function: it is the denominator behind "share of that state's final early
    vote", so a series that stops a week early would understate the final and
    inflate every percentage computed against it. A partial backfill therefore
    yields nothing here and the column stays a dash, which is the honest answer.
    """
    rows = _read(out_dir / "ev_state_daily.csv")
    finals: dict[tuple[str, str], str] = {}
    reaches_election_day: set[tuple[str, str]] = set()
    best: dict[tuple[str, str], int] = {}

    for row in rows:
        cycle, state = row.get("cycle", ""), row.get("state", "")
        if not cycle or not state or cycle == str(CURRENT_CYCLE_HINT):
            continue
        key = (cycle, state)
        raw = (row.get("ballots_total") or "").strip()
        # A day-0 row only completes the series if it REPORTED something. Marking
        # completeness from the date alone accepted a blank Election Day row as
        # proof, so South Carolina 2022 -- which has such a row and otherwise
        # stops 18 days out at 16,975 ballots -- published that partial figure as
        # its final. The site then showed a 1.0% "share of its 2022 early vote"
        # for a state that early-voted in the hundreds of thousands.
        if raw and (row.get("days_to_election") or "").strip() == "0":
            reaches_election_day.add(key)
        if not raw:
            continue
        try:
            value = int(raw)
        except ValueError:
            continue
        if value > best.get(key, -1):
            best[key] = value

    for key, value in best.items():
        if key in reaches_election_day:
            finals[key] = str(value)
    return finals


def publish_state_meta(out_dir: Path, meta_path: Path,
                       derived: dict[tuple[str, str], str] | None = None) -> dict | None:
    """Copy data/meta/states.csv to output/ev_state_meta.csv, validating columns.

    Missing is not an error -- the table is hand-maintained and the daily job must
    not fail because it has not been written yet. A column MISMATCH is an error,
    because the page joins on these names.
    """
    if not meta_path.exists():
        log.warning("no state meta at %s; skipping", meta_path)
        return None

    rows = _read(meta_path)
    if not rows:
        log.warning("%s is empty; skipping", meta_path)
        return None

    missing = set(STATE_META_COLUMNS) - set(rows[0])
    if missing:
        raise PublishRefused(f"{meta_path.name} is missing columns: {sorted(missing)}")

    # Fill blank prior-cycle finals from our own completed backfills. A cell that
    # someone typed by hand ALWAYS wins -- an official canvass figure beats our
    # scrape, and silently overwriting it would make the hand-maintained file a
    # lie. We only ever fill blanks.
    filled = 0
    for row in rows:
        state = (row.get("state") or "").strip().upper()
        for cycle, column in (("2022", "ev_2022_total"), ("2024", "ev_2024_total")):
            if (row.get(column) or "").strip():
                continue
            value = (derived or {}).get((cycle, state))
            if value:
                row[column] = value
                filled += 1
    if filled:
        log.info("ev_state_meta.csv: filled %d prior-cycle total(s) from our data", filled)

    _atomic_write(out_dir / "ev_state_meta.csv", STATE_META_COLUMNS, rows)
    log.info("ev_state_meta.csv: %d states", len(rows))
    return {"file": "ev_state_meta.csv", "rows": len(rows)}


def write_status(out_dir: Path, payload: dict, *, partial: bool = False) -> None:
    """ev_status.json -- the page's honesty layer.

    Written even when every state failed, because a status file that stops
    updating is itself the signal the page needs to show a stale badge.

    `partial=True` MERGES into whatever is already on disk instead of replacing
    it. A `--state NC ME` run knows nothing about the other nineteen, and writing
    its two-state payload wholesale silently dropped them from the file the page
    reads -- which is how the site came to show nineteen tracked states as "not
    tracked". The daily job is never scoped, so it keeps replacing.
    """
    out_dir.mkdir(parents=True, exist_ok=True)

    if partial:
        existing_path = out_dir / "ev_status.json"
        if existing_path.exists():
            try:
                existing = json.loads(existing_path.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                existing = {}
            merged_states = dict(existing.get("states") or {})
            merged_states.update(payload.get("states") or {})
            payload = dict(payload)
            payload["states"] = merged_states
            # The summary describes the whole file, not just this run's slice.
            counts = {"ok": 0, "pending": 0, "failed": 0}
            for entry in merged_states.values():
                status = entry.get("status")
                if status == "ok":
                    counts["ok"] += 1
                elif status == "pending":
                    counts["pending"] += 1
                else:
                    counts["failed"] += 1
            summary = dict(payload.get("summary") or {})
            summary.update(counts)
            summary["partial_run"] = True
            payload["summary"] = summary
    fd, tmp = tempfile.mkstemp(dir=str(out_dir), prefix="ev_status", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=1, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, out_dir / "ev_status.json")
