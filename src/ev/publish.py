"""Merge new rows into the published CSVs and write them atomically.

The third ladder rule lives here: A WORSE TIER NEVER OVERWRITES A BETTER ONE for
the same key. If Tuesday's run scraped North Carolina's voter-level file (tier 1,
with counties and party) and Wednesday's scrape 502s so the aggregator answers
instead (tier 2, statewide only), Wednesday must not replace Tuesday's rich row
with a thin one. A same-or-better tier does replace, because that is a genuine
update -- states restate counts routinely.
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
    STATE_DAILY_COLUMNS, STATE_KEY,
    county_row_to_dict, demo_row_to_dict, state_row_to_dict,
)

log = logging.getLogger(__name__)

#: Never derive a "final" for the cycle still in progress.
CURRENT_CYCLE_HINT = 2026

#: Columns that are provenance/identity rather than data. Everything else counts
#: toward a row's "richness" for the content-loss guard below.
_NON_DATA_COLUMNS = frozenset({
    "cycle", "state", "county_fips", "county_name", "date", "days_to_election",
    "dimension", "bucket", "restated", "source_tier", "source_name", "retrieved_at",
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
) -> dict:
    """Merge `incoming` into the CSV at `path` and write it back atomically."""
    existing = _read(path)
    merged, replaced, kept_better, kept_richer = merge_rows(
        existing, incoming, key_cols, guard=guard
    )

    if flag_restatements:
        mark_restatements(merged)

    before, after = len(existing), len(merged)
    merged.sort(key=_sort_key(columns))
    _atomic_write(path, columns, merged)
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
    Election Day (`days_to_election == 0`). That condition is the whole safety of
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
        if (row.get("days_to_election") or "").strip() == "0":
            reaches_election_day.add(key)
        raw = (row.get("ballots_total") or "").strip()
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


def write_status(out_dir: Path, payload: dict) -> None:
    """ev_status.json -- the page's honesty layer.

    Written even when every state failed, because a status file that stops
    updating is itself the signal the page needs to show a stale badge.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(out_dir), prefix="ev_status", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=1, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, out_dir / "ev_status.json")
