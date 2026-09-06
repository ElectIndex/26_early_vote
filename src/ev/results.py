"""Actual 2022 and 2024 election results, so the early-vote curve has an answer.

The daily tables say how many ballots came back. They cannot say whether that
meant anything. This module publishes the OUTCOME next to the curve:
`output/results_state.csv`, one row per (cycle, state, office).

    cycle,state,office,dem_votes,rep_votes,oth_votes,total_votes,dem_share,
    rep_share,margin,winner_party,early_share_of_total,source_name,retrieved_at

Results are static history. They are fetched by `python -m ev results`, NOT by
the six-hourly `ingest` walk, and the downloads are cached because an archived
certified return cannot change.

THE SOURCE — MIT Election Data + Science Lab, Harvard Dataverse, CC0 1.0. See
`docs/results-source.md` for the full verification log. Two files, both fetched
and parsed against real fixtures in `tests/fixtures/results/`:

    president  doi:10.7910/DVN/42MVDX  1976-2024-president.csv
    senate     doi:10.7910/DVN/PEJ5QU  1976-2024-senate-state.tab

`governor` and `house_total` are declared in OFFICES but are NOT sourced. MEDSL
publishes no statewide gubernatorial dataset at all, and its statewide House
file (doi:10.7910/DVN/IG0UN2) is behind a Dataverse guestbook that refuses API
downloads with HTTP 400. Both are recorded in the docs rather than guessed at.

Four rules carried over from the adapter contract, because they matter more here
than anywhere else -- these numbers are what the reader will quote:

1. `None`, never `0`. Alaska's 2022 Senate return carries no party for ANY
   candidate (ranked-choice; MEDSL leaves `party_detailed` empty and buckets
   everyone as OTHER). Publishing `dem_votes=0` there would say no Democrat got
   a vote in a race that had one. So a race whose source reports no D and no R
   at all publishes BLANK party columns, not zeros. See `_party_reported`.

2. Never guess a party mapping. `party_detailed` is read through
   `normalize.party()` first and `party_simplified` only as a fallback, because
   MEDSL's own `party_simplified` is wrong on seven 2022 rows -- Tammy Duckworth
   (IL) and Chris Van Hollen (MD) are both coded `DEMOCRATIC` -> `OTHER`. An
   unrecognised value in EITHER column raises `SchemaDrift`.

3. An independent stays independent. Angus King and Bernie Sanders caucus with
   the Democrats and are published in `oth_votes`, because `party_detailed` says
   `INDEPENDENT` and that is what the ballot said.

4. Two-letter USPS or nothing. The state NAME is mapped through an explicit
   51-entry table and cross-checked against the source's own `state_po`; a
   disagreement or an unknown name raises rather than being coerced.
"""

from __future__ import annotations

import csv
import io
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence

from . import publish
from .adapters import _net
from .adapters.base import SchemaDrift, SourceError
from .normalize import PARTY_DEM, PARTY_OTH, PARTY_REP, party as normalize_party

log = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Published contract
# --------------------------------------------------------------------------
RESULTS_COLUMNS = [
    "cycle", "state", "office",
    "dem_votes", "rep_votes", "oth_votes", "total_votes",
    "dem_share", "rep_share", "margin", "winner_party",
    "early_share_of_total", "source_name", "retrieved_at",
]

#: Key columns. One outcome per office per state per cycle.
RESULTS_KEY = ("cycle", "state", "office")

OFFICE_PRESIDENT = "president"
OFFICE_SENATE = "senate"
OFFICE_GOVERNOR = "governor"
OFFICE_HOUSE = "house_total"

#: The full vocabulary the column may ever carry, so the frontend can be written
#: against it now. Only SOURCED_OFFICES are actually produced today.
OFFICES = (OFFICE_PRESIDENT, OFFICE_SENATE, OFFICE_GOVERNOR, OFFICE_HOUSE)

#: Cycles this command covers. 2026 is the cycle in progress and has no result.
RESULT_CYCLES = (2022, 2024)

#: All *_share columns and `margin` are PERCENTAGE POINTS (0-100), not fractions,
#: so `margin = dem_share - rep_share` is a margin in points like the rest of the
#: project. Four decimals is enough to reproduce a certified count exactly.
_SHARE_DP = 4


class UnknownState(SchemaDrift):
    """A state label the explicit USPS table does not contain."""


# --------------------------------------------------------------------------
# States. Explicit, both directions, no fuzzy matching.
# --------------------------------------------------------------------------
STATE_NAME_TO_CODE: dict[str, str] = {
    "ALABAMA": "AL", "ALASKA": "AK", "ARIZONA": "AZ", "ARKANSAS": "AR",
    "CALIFORNIA": "CA", "COLORADO": "CO", "CONNECTICUT": "CT", "DELAWARE": "DE",
    "DISTRICT OF COLUMBIA": "DC", "FLORIDA": "FL", "GEORGIA": "GA",
    "HAWAII": "HI", "IDAHO": "ID", "ILLINOIS": "IL", "INDIANA": "IN",
    "IOWA": "IA", "KANSAS": "KS", "KENTUCKY": "KY", "LOUISIANA": "LA",
    "MAINE": "ME", "MARYLAND": "MD", "MASSACHUSETTS": "MA", "MICHIGAN": "MI",
    "MINNESOTA": "MN", "MISSISSIPPI": "MS", "MISSOURI": "MO", "MONTANA": "MT",
    "NEBRASKA": "NE", "NEVADA": "NV", "NEW HAMPSHIRE": "NH", "NEW JERSEY": "NJ",
    "NEW MEXICO": "NM", "NEW YORK": "NY", "NORTH CAROLINA": "NC",
    "NORTH DAKOTA": "ND", "OHIO": "OH", "OKLAHOMA": "OK", "OREGON": "OR",
    "PENNSYLVANIA": "PA", "RHODE ISLAND": "RI", "SOUTH CAROLINA": "SC",
    "SOUTH DAKOTA": "SD", "TENNESSEE": "TN", "TEXAS": "TX", "UTAH": "UT",
    "VERMONT": "VT", "VIRGINIA": "VA", "WASHINGTON": "WA",
    "WEST VIRGINIA": "WV", "WISCONSIN": "WI", "WYOMING": "WY",
}

STATE_CODES: frozenset[str] = frozenset(STATE_NAME_TO_CODE.values())


def state_code(name: str | None, state_po: str | None = None) -> str:
    """Resolve a source's state label to a two-letter USPS code.

    Both labels are checked when both are present. MEDSL carries `state` and
    `state_po` side by side, so a row where they disagree is a shifted column,
    not a naming variant, and must not be published under either code.
    """
    key = " ".join((name or "").strip().upper().split())
    code = STATE_NAME_TO_CODE.get(key)
    po = (state_po or "").strip().upper()

    if code is None and not po:
        raise UnknownState(f"unrecognised state {name!r}")
    if po and po not in STATE_CODES:
        raise UnknownState(f"unrecognised state code {state_po!r}")
    if code is None:
        raise UnknownState(f"unrecognised state name {name!r} (code {po})")
    if po and po != code:
        raise UnknownState(f"state {name!r} maps to {code}, but the row says {po}")
    return code


# --------------------------------------------------------------------------
# Source description
# --------------------------------------------------------------------------
#: Harvard Dataverse file-access endpoint. File ids are stable once published;
#: the DOI beside each one is the citable dataset.
DATAVERSE_ACCESS = "https://dataverse.harvard.edu/api/access/datafile/{file_id}"


@dataclass(frozen=True)
class MedslSource:
    """One MEDSL statewide returns file, pinned to a Dataverse file id."""

    office: str
    name: str            # written into source_name
    doi: str
    file_id: int
    filename: str
    delimiter: str
    office_label: str    # the value the file's `office` column must carry
    required: frozenset[str]
    #: Columns that only some of the files carry. Absent is fine; present and
    #: unexpected is not, which is what `required` is for.
    has_stage: bool = False

    @property
    def url(self) -> str:
        return DATAVERSE_ACCESS.format(file_id=self.file_id)


_COMMON_REQUIRED = {
    "year", "state", "state_po", "office", "candidate",
    "party_detailed", "party_simplified", "candidatevotes", "totalvotes",
}

PRESIDENT_SOURCE = MedslSource(
    office=OFFICE_PRESIDENT,
    name="medsl-president",
    doi="doi:10.7910/DVN/42MVDX",
    file_id=13887042,
    filename="1976-2024-president.csv",
    delimiter=",",
    office_label="US PRESIDENT",
    required=frozenset(_COMMON_REQUIRED | {"writein"}),
)

SENATE_SOURCE = MedslSource(
    office=OFFICE_SENATE,
    name="medsl-senate",
    doi="doi:10.7910/DVN/PEJ5QU",
    file_id=13887039,
    filename="1976-2024-senate-state.tab",
    delimiter="\t",
    office_label="US SENATE",
    required=frozenset(_COMMON_REQUIRED | {"stage", "special", "mode", "writein"}),
    has_stage=True,
)

SOURCES: dict[str, MedslSource] = {
    PRESIDENT_SOURCE.office: PRESIDENT_SOURCE,
    SENATE_SOURCE.office: SENATE_SOURCE,
}

#: Offices we can actually produce. `governor` and `house_total` are in OFFICES
#: but have no verified open source -- see the module docstring and the docs.
SOURCED_OFFICES: tuple[str, ...] = tuple(SOURCES)

#: The one stage we publish. The GA 2022 Senate runoff is a SEPARATE election
#: five weeks after Election Day with its own early vote, so folding it into the
#: November row would compare our November curve against December's result.
STAGE_GENERAL = "GEN"

#: Stages that exist in the file and are deliberately skipped. Anything outside
#: this set plus STAGE_GENERAL is drift, not a row to drop quietly.
STAGES_IGNORED = frozenset({"PRI", "PRE", "PRIMARY", "RUNOFF", "GEN RUNOFF"})

#: MEDSL splits some historical rows by voting mode. A mode-split file summed
#: naively double-counts, so anything but the aggregate row is refused outright.
MODE_TOTAL = "TOTAL"

#: MEDSL's own four-value party field, used ONLY when `party_detailed` is a label
#: `normalize.party()` does not know. Every value here was observed in the 2022
#: and 2024 rows of both files.
_SIMPLIFIED_TO_BUCKET = {
    "DEMOCRAT": PARTY_DEM,
    "REPUBLICAN": PARTY_REP,
    "LIBERTARIAN": PARTY_OTH,
    "OTHER": PARTY_OTH,
    # A ballot line with no party at all -- Hawaii's "OVER VOTES", Nevada's
    # "NONE OF THESE CANDIDATES". These are inside the state's own race total,
    # so they belong in `oth`; dropping them would break the share arithmetic.
    "": PARTY_OTH,
}

#: How far the candidate rows may fall short of (or overshoot) the file's own
#: `totalvotes` before we refuse the race. MEDSL's largest 2024 discrepancy is
#: DC at 0.78% (it lists RFK Jr both separately and inside the write-in line);
#: NY is 0.01%. Anything past 2% is a parse failure, not a source quirk.
MAX_TOTAL_DRIFT = 0.02


def _utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# --------------------------------------------------------------------------
# The published record
# --------------------------------------------------------------------------
@dataclass
class StateResult:
    """One office's statewide outcome in one state in one cycle."""

    cycle: int
    state: str
    office: str
    source_name: str
    total_votes: int | None = None
    dem_votes: int | None = None
    rep_votes: int | None = None
    oth_votes: int | None = None
    winner_party: str | None = None
    early_votes: int | None = None       # not published; the numerator only
    retrieved_at: str = ""

    def key(self) -> tuple:
        return (int(self.cycle), self.state.upper(), self.office)

    # -- derived, never stored -------------------------------------------
    def _share(self, votes: int | None) -> float | None:
        if votes is None or not self.total_votes:
            return None
        return round(100.0 * votes / self.total_votes, _SHARE_DP)

    @property
    def dem_share(self) -> float | None:
        return self._share(self.dem_votes)

    @property
    def rep_share(self) -> float | None:
        return self._share(self.rep_votes)

    @property
    def margin(self) -> float | None:
        """Democratic share minus Republican share, in points. D positive."""
        d, r = self.dem_share, self.rep_share
        if d is None or r is None:
            return None
        return round(d - r, _SHARE_DP)

    @property
    def early_share_of_total(self) -> float | None:
        return self._share(self.early_votes)

    def to_dict(self) -> dict[str, str]:
        return {
            "cycle": str(int(self.cycle)),
            "state": self.state.upper(),
            "office": self.office,
            "dem_votes": _cell(self.dem_votes),
            "rep_votes": _cell(self.rep_votes),
            "oth_votes": _cell(self.oth_votes),
            "total_votes": _cell(self.total_votes),
            "dem_share": _num(self.dem_share),
            "rep_share": _num(self.rep_share),
            "margin": _num(self.margin),
            "winner_party": self.winner_party or "",
            "early_share_of_total": _num(self.early_share_of_total),
            "source_name": self.source_name,
            "retrieved_at": self.retrieved_at,
        }


def _cell(value: int | None) -> str:
    """None -> empty cell. THE BLANK RULE, same as schema.py's."""
    return "" if value is None else str(int(value))


def _num(value: float | None) -> str:
    """None -> empty cell; a real value keeps its sign and drops trailing zeros."""
    if value is None:
        return ""
    text = f"{value:.{_SHARE_DP}f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------
def _bucket(row: dict[str, str], source: MedslSource) -> str:
    """Which of dem/rep/oth this candidate line's votes belong to.

    `party_detailed` first, because it is what the ballot said and because
    MEDSL's `party_simplified` demonstrably mis-codes some of it. Only when
    `normalize.party()` does not recognise the detailed label -- fusion tickets
    ("WORKING FAMILIES / DEMOCRAT"), state party names ("DEMOCRATIC-NPL"), or a
    blank -- do we fall back to MEDSL's own bucketing.
    """
    detailed = normalize_party(row.get("party_detailed"))
    if detailed == PARTY_DEM:
        return PARTY_DEM
    if detailed == PARTY_REP:
        return PARTY_REP
    if detailed is not None:
        # npa or a real third party. Both are `oth` here: this table has three
        # buckets, and an independent is never folded into a party.
        return PARTY_OTH

    simplified = (row.get("party_simplified") or "").strip().upper()
    bucket = _SIMPLIFIED_TO_BUCKET.get(simplified)
    if bucket is None:
        raise SchemaDrift(
            f"{source.name}: unrecognised party_simplified {simplified!r} "
            f"(party_detailed {row.get('party_detailed')!r})"
        )
    return bucket


def _votes(raw: str | None) -> int | None:
    """MEDSL writes counts as floats ('2572294.0'). Blank/NA is not zero."""
    text = (raw or "").strip()
    if not text or text.upper() in ("NA", "N/A", "NULL"):
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def _flag(raw: str | None, column: str, source: MedslSource) -> bool:
    text = (raw or "").strip().upper()
    if text in ("TRUE", "T", "1", "YES"):
        return True
    if text in ("FALSE", "F", "0", "NO", ""):
        return False
    raise SchemaDrift(f"{source.name}: {column} is {raw!r}, not a boolean")


def _party_reported(dem: int, rep: int) -> bool:
    """False when the source gave no party for anyone in this race.

    Alaska 2022 is the whole reason this exists: every candidate row has an empty
    `party_detailed` and `party_simplified == OTHER`, so a naive sum yields
    dem=0, rep=0 -- a confident claim that nobody voted for either party in a
    race Lisa Murkowski and Kelly Tshibaka both contested. Blank is the truth.
    """
    return bool(dem or rep)


def parse(body: bytes, source: MedslSource, cycle: int) -> list[StateResult]:
    """Turn one MEDSL file into one StateResult per state for `cycle`."""
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SchemaDrift(f"{source.name}: {source.filename} is not UTF-8") from exc

    reader = csv.DictReader(io.StringIO(text), delimiter=source.delimiter)
    missing = source.required - set(reader.fieldnames or [])
    if missing:
        raise SchemaDrift(
            f"{source.name}: {source.filename} missing columns {sorted(missing)}"
        )

    # (state, is_special) -> rows. The pair matters because a state can run a
    # regular and a special Senate election on the same day (OK 2022, NE 2024).
    races: dict[tuple[str, bool], list[dict[str, str]]] = {}
    want = str(int(cycle))

    for row in reader:
        if (row.get("year") or "").strip() != want:
            continue

        office = (row.get("office") or "").strip().upper()
        if office != source.office_label:
            raise SchemaDrift(
                f"{source.name}: office column says {office!r}, "
                f"expected {source.office_label!r}"
            )

        if source.has_stage:
            stage = " ".join((row.get("stage") or "").strip().upper().split())
            if stage != STAGE_GENERAL:
                if stage in STAGES_IGNORED:
                    continue
                raise SchemaDrift(f"{source.name}: unknown stage {stage!r}")
            mode = (row.get("mode") or "").strip().upper()
            if mode != MODE_TOTAL:
                raise SchemaDrift(
                    f"{source.name}: mode {mode!r} is not {MODE_TOTAL!r}; a "
                    "mode-split file cannot be summed without double counting"
                )

        state = state_code(row.get("state"), row.get("state_po"))
        special = _flag(row.get("special"), "special", source) if source.has_stage else False
        races.setdefault((state, special), []).append(row)

    return _emit(races, source, cycle)


def _emit(races: dict[tuple[str, bool], list[dict[str, str]]],
          source: MedslSource, cycle: int) -> list[StateResult]:
    stamp = _utcnow()
    chosen: dict[str, list[dict[str, str]]] = {}

    for (state, special), rows in races.items():
        # A regular election always beats a concurrent special, so the published
        # row is the seat the whole state was voting on. Where the special is the
        # ONLY race that cycle it is used, because it is then the real election.
        if special and (state, False) in races:
            continue
        chosen[state] = rows

    out: list[StateResult] = []
    for state, rows in sorted(chosen.items()):
        totals = {_votes(r.get("totalvotes")) for r in rows}
        totals.discard(None)
        if len(totals) != 1:
            raise SchemaDrift(
                f"{source.name}: {state} {cycle} reports {len(totals)} different "
                f"race totals {sorted(totals)}"
            )
        total = totals.pop()

        counts = {PARTY_DEM: 0, PARTY_REP: 0, PARTY_OTH: 0}
        top_votes, top_bucket = -1, None
        unreadable = False
        for row in rows:
            votes = _votes(row.get("candidatevotes"))
            bucket = _bucket(row, source)
            if votes is None:
                # One unreadable candidate line makes every sum in this race
                # wrong, so the race is skipped rather than published short.
                unreadable = True
                continue
            counts[bucket] += votes
            if votes > top_votes:
                top_votes, top_bucket = votes, bucket

        if unreadable:
            log.warning("%s: %s %s has an unreadable vote count; skipping the race",
                        source.name, state, cycle)
            continue

        summed = sum(counts.values())
        if total and abs(summed - total) > MAX_TOTAL_DRIFT * total:
            raise SchemaDrift(
                f"{source.name}: {state} {cycle} candidate rows sum to {summed} "
                f"but the file's race total is {total}"
            )

        reported = _party_reported(counts[PARTY_DEM], counts[PARTY_REP])
        out.append(StateResult(
            cycle=int(cycle), state=state, office=source.office,
            source_name=source.name, retrieved_at=stamp,
            total_votes=total,
            # Blank, not zero, when the source gave nobody a party. See
            # _party_reported -- this is THE BLANK RULE's sharpest edge here.
            dem_votes=counts[PARTY_DEM] if reported else None,
            rep_votes=counts[PARTY_REP] if reported else None,
            oth_votes=counts[PARTY_OTH] if reported else None,
            winner_party=top_bucket if reported else None,
        ))
    return out


# --------------------------------------------------------------------------
# Fetch
# --------------------------------------------------------------------------
def fetch(source: MedslSource, *, use_cache: bool = True) -> bytes:
    """Download one MEDSL file, mirroring it into cache/us/.

    `use_cache` defaults True because these are archived, versioned, immutable
    files -- the opposite of the daily snapshots the ingest walk pulls.
    """
    body = _net.get(source.url, state="US", filename=source.filename,
                    use_cache=use_cache, min_bytes=1024)
    if _net.looks_like_html(body):
        # Dataverse answers a gated or missing file with JSON or an HTML shell.
        raise SourceError(
            f"{source.name}: {source.url} returned a web page, not {source.filename}"
        )
    if body.lstrip()[:1] == b"{":
        raise SourceError(
            f"{source.name}: {source.url} returned an API error: "
            f"{body[:200].decode('utf-8', 'replace')}"
        )
    return body


# --------------------------------------------------------------------------
# early_share_of_total
# --------------------------------------------------------------------------
def early_totals(out_dir: Path) -> dict[tuple[str, str], int]:
    """Each state's FINAL early-vote total per past cycle, {(cycle, state): n}.

    Two sources, in order: the hand-maintained `ev_state_meta.csv` columns, then
    `publish.derive_prior_finals`, which only answers for a series that actually
    reached Election Day. Neither is guessed at, and a state with no final at all
    simply does not appear -- which leaves `early_share_of_total` BLANK rather
    than zero, because a zero would claim nobody voted early.
    """
    finals: dict[tuple[str, str], int] = {}

    for (cycle, state), value in publish.derive_prior_finals(out_dir).items():
        try:
            finals[(str(cycle), state.upper())] = int(value)
        except (TypeError, ValueError):
            continue

    meta = out_dir / "ev_state_meta.csv"
    if meta.exists():
        with meta.open(newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                state = (row.get("state") or "").strip().upper()
                if not state:
                    continue
                for cycle, column in (("2022", "ev_2022_total"), ("2024", "ev_2024_total")):
                    raw = (row.get(column) or "").strip()
                    if not raw:
                        continue
                    try:
                        # A hand-entered official figure wins over our scrape,
                        # exactly as it does in publish.publish_state_meta.
                        finals[(cycle, state)] = int(float(raw))
                    except ValueError:
                        log.warning("ev_state_meta.csv: %s %s is not a number: %r",
                                    state, column, raw)
    return finals


def attach_early_share(rows: Iterable[StateResult], out_dir: Path) -> int:
    """Fill each row's early-vote numerator. Returns how many were filled."""
    finals = early_totals(out_dir)
    filled = 0
    for row in rows:
        early = finals.get((str(int(row.cycle)), row.state.upper()))
        if early is None:
            continue
        row.early_votes = early
        filled += 1
        share = row.early_share_of_total
        if share is not None and share > 100.0:
            # Possible and not an error: `total_votes` is votes in ONE race, and
            # a state's ballots cast can exceed that when voters skip it. Worth
            # saying out loud, though, because it usually means a mismatch.
            log.warning("%s %s: early total %d exceeds the %s race total %s",
                        row.state, row.cycle, early, row.office, row.total_votes)
    return filled


# --------------------------------------------------------------------------
# Publish
# --------------------------------------------------------------------------
def _read(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def publish_results(out_dir: Path, rows: Sequence[StateResult]) -> dict:
    """Merge into output/results_state.csv and write it atomically.

    Implemented here rather than in publish.py (which this module does not own).
    It is a keyed replace rather than publish.publish_table because these rows
    have no `source_tier` for that merge to reason about and because the table's
    natural order is (cycle, state, office), which publish.py's shared sort key
    does not know about. The atomic write itself is publish.py's.
    """
    path = out_dir / "results_state.csv"
    merged = {tuple(r.get(c, "") for c in RESULTS_KEY): r for r in _read(path)}
    before = len(merged)

    for row in rows:
        payload = row.to_dict()
        merged[tuple(payload[c] for c in RESULTS_KEY)] = payload

    ordered = sorted(merged.values(), key=lambda r: tuple(r.get(c, "") for c in RESULTS_KEY))
    publish._atomic_write(path, RESULTS_COLUMNS, ordered)
    log.info("results_state.csv: %d rows (+%d new, %d written)",
             len(ordered), len(ordered) - before, len(rows))
    return {"file": path.name, "rows": len(ordered),
            "added": len(ordered) - before, "written": len(rows)}


# --------------------------------------------------------------------------
# Build
# --------------------------------------------------------------------------
def build(cycles: Sequence[int], *, offices: Sequence[str] | None = None,
          states: Sequence[str] | None = None,
          use_cache: bool = True) -> tuple[list[StateResult], list[str]]:
    """Fetch and parse every (office, cycle) pair. Returns (rows, problems)."""
    wanted_offices = list(offices or SOURCED_OFFICES)
    unknown = [o for o in wanted_offices if o not in SOURCES]
    if unknown:
        raise ValueError(f"no source for office(s) {unknown}; have {list(SOURCES)}")
    keep = {s.upper() for s in states} if states else None

    rows: list[StateResult] = []
    problems: list[str] = []

    for office in wanted_offices:
        source = SOURCES[office]
        try:
            body = fetch(source, use_cache=use_cache)
        except SourceError as exc:
            problems.append(f"{office}: {exc}")
            continue

        for cycle in cycles:
            try:
                produced = parse(body, source, cycle)
            except SchemaDrift as exc:
                problems.append(f"{office} {cycle}: {exc}")
                continue
            if not produced:
                # Normal: no state elects a president in a midterm.
                log.info("%s: no %s races in %s", source.name, office, cycle)
                continue
            if keep is not None:
                produced = [r for r in produced if r.state in keep]
            rows.extend(produced)

    return rows, problems


def cmd_results(args) -> int:
    """`python -m ev results` -- static history, deliberately not in `ingest`."""
    from .cli import OUTPUT_DIR  # local: cli imports this module at load time

    out_dir = Path(args.output or OUTPUT_DIR)
    cycles = [int(c) for c in (args.cycle or RESULT_CYCLES)]

    rows, problems = build(
        cycles,
        offices=args.office,
        states=args.state,
        use_cache=not args.refresh,
    )

    if rows:
        # Joined even on a dry run, so the count printed below is the real one.
        attach_early_share(rows, out_dir)
        if not args.dry_run:
            publish_results(out_dir, rows)

    by_office: dict[tuple[int, str], int] = {}
    for row in rows:
        by_office[(row.cycle, row.office)] = by_office.get((row.cycle, row.office), 0) + 1
    with_early = sum(1 for r in rows if r.early_votes is not None)

    prefix = "DRY RUN " if args.dry_run else ""
    print(f"{prefix}results: {len(rows)} rows, {with_early} with an early-vote total")
    for (cycle, office), count in sorted(by_office.items()):
        print(f"  {cycle} {office}: {count} states")
    for problem in problems:
        print(f"  PROBLEM {problem}")

    missing = [o for o in OFFICES if o not in SOURCED_OFFICES]
    if missing:
        print("  not sourced: " + ", ".join(missing) + " (see docs/results-source.md)")
    return 1 if problems else 0
