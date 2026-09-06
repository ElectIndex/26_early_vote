"""Tier 2: the UF Election Lab / US Elections Project national early-vote tracker.

WHAT EXISTS FOR 2026 (verified 2026-09-05)
------------------------------------------
Michael McDonald's tracker has run every general election since 2008. For 2026 it
lives at https://election.lab.ufl.edu/early-vote/2026-early-voting/ and -- unlike
2020 (an R Markdown site reading a CSV off the author's Dropbox) and 2022 (an
RPubs render with no data file) -- the 2023 site rebuild feeds its dashboards from
a plain CSV that anyone can GET:

    https://election.lab.ufl.edu/data-downloads/earlyvote/2026/US.csv

That URL is not documented anywhere on the site; it is the `COMBINED_CSV_URL`
constant in the theme's `2026-early-voting.js`, joined to the `/data-downloads/`
prefix that `functions-csvtables.js` puts in front of every `csvfile` option. So
it is a real, live, load-bearing endpoint (the public dashboard breaks if it
moves) rather than a guess -- but it is also an implementation detail of someone
else's front end, which is exactly why every failure below degrades to
SourceError and lets tier 3 answer instead of pretending.

The 2024 file at .../earlyvote/2024/US.csv is still up with a byte-identical
column layout, which is what the parsing path here was written and tested
against. /2022/ and /2020/ are 404 -- those cycles predate the rebuild.

FOUR TRAPS IN THIS FILE, ALL OF WHICH WOULD PUBLISH CONFIDENT WRONG NUMBERS
--------------------------------------------------------------------------
1. ZERO MEANS BLANK. Every numeric cell is 0 when the state has not reported
   that field -- Georgia's `voted_dem` is 0 because Georgia has no party
   registration, not because no Democrat voted. UF's own dashboard agrees: its
   JS gates each chart on `statedata.voted_dem || statedata.voted_rep || ...`,
   i.e. it treats 0 as falsy/absent. We therefore map 0 -> None everywhere (see
   `_count`). A genuine zero is unrecoverable from this file, and blank is the
   safe direction: "unknown" renders honestly, "0" does not.

2. THE AGE BANDS DO NOT LINE UP AND MUST NOT BE MAPPED. UF bands are
   18-25 / 26-40 / 41-65 / over 65 (`age_1`..`age_4`, labelled in
   2026-early-voting-state.js). Ours are 18-24/25-34/35-44/45-54/55-64/65+.
   `normalize.age_band("41-65")` cheerfully returns "35-44" because it buckets on
   the low end -- so a naive pass would file two thirds of a state's voters under
   a band they are not in. There is no split that recovers our bands from theirs,
   so this adapter emits NO age rows at all. Race and sex do map cleanly and are
   emitted.

3. `*_none` IS "None/Minor", NOT "no party affiliation". The site labels that
   series "None/Minor", so it folds real third parties in with the unaffiliated.
   We put it in `party_npa` (matching normalize.py, which already routes the
   combined label "other/none" to NPA) and leave `party_oth` blank rather than
   splitting a number the source never split.

4. ALABAMA AND NEW HAMPSHIRE ARE NOT IN THE FILE. 49 rows, not 51. A state the
   aggregator does not carry is SourceError, not NotYetPublished, so the ladder
   falls through to the hand-entered tier 3 -- see `fetch` for why that
   distinction is the whole point of this module.

WHAT THIS ADAPTER DELIBERATELY DOES NOT DO
------------------------------------------
Per-state county files exist (.../earlyvote/2026/NC_county.csv) and are tempting,
but they are keyed by county NAME with no FIPS column, and CLAUDE.md's rule 4
forbids name joins for good reasons ("St." vs "Saint", parishes, boroughs).
Tier 2's job is the statewide total; counties stay a tier-1 concern.
"""

from __future__ import annotations

import csv
import io
import logging
from datetime import date, datetime
from pathlib import Path

from .. import normalize
from ..calendar import election_date
from ..schema import DemoDay, StateDay, TIER_AGGREGATOR
from .base import Adapter, FetchResult, NotYetPublished, SchemaDrift, SourceError

log = logging.getLogger(__name__)

URL_TEMPLATE = "https://election.lab.ufl.edu/data-downloads/earlyvote/{cycle}/US.csv"

HTTP_TIMEOUT = 30
USER_AGENT = "ElectIndex-earlyvote/0.1 (+https://electindex.com/early-vote/)"

#: Columns we actually read. Requiring only these means UF renaming the cycle's
#: turnout column (`turn_2020` in 2024, `turn_2022` in 2026) does not trip drift.
REQUIRED_COLUMNS = frozenset({
    "state_abbv", "last_update",
    "request_all", "accept_all", "inperson_all",
    "voted_all", "voted_dem", "voted_rep", "voted_none",
})

#: UF column -> the raw label we hand to normalize, so a typo here fails loudly
#: rather than inventing a bucket the frontend has never heard of.
RACE_COLUMNS = {
    "voted_nh_white": "white",
    "voted_nh_black": "black",
    "voted_hispanic": "hispanic",
    "voted_nh_asian": "asian",
    "voted_nh_native_american": "native american",
    "voted_nh_other": "other",
}

SEX_COLUMNS = {
    "voted_female": "female",
    "voted_male": "male",
    "voted_gender_unknown": "unknown",
}

#: Parsed files, memoised per process and keyed by (cycle, source). One `ev
#: ingest` run walks 15+ state ladders and every one of them that reaches tier 2
#: wants the same national file; without this we would GET it 15 times.
_CACHE: dict[tuple[int, str], list[dict[str, str]]] = {}


def clear_cache() -> None:
    """Drop the memoised downloads. Tests call this; the CLI never needs to."""
    _CACHE.clear()


def _count(raw: str | None) -> int | None:
    """A cell's value, with 0 read as "not reported". See TRAP 1 above.

    Blank, "0", "0.0" and whitespace all become None. Anything non-numeric is
    drift: the file is a fixed-width grid of integers, so a word in a count
    column means we are reading a different file than we think we are.
    """
    if raw is None:
        return None
    text = str(raw).strip().replace(",", "")
    if not text:
        return None
    try:
        value = int(float(text))
    except ValueError as exc:
        raise SchemaDrift(f"UF tracker: {raw!r} is not a number") from exc
    return value or None


def _parse_last_update(raw: str | None, cycle: int, state: str) -> date:
    """The per-state "as of" date, e.g. "10/25/2024".

    Each row carries its OWN date -- the tracker updates states on different
    days -- so a row must be published under the day the state actually reported,
    not under the day we happened to fetch. Dating a stale row "today" would draw
    a flat line on the site's curve that the state never reported.
    """
    text = (raw or "").strip()
    if not text:
        raise SourceError(f"UF tracker: {state} row has no last_update")
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y"):
        try:
            day = datetime.strptime(text, fmt).date()
            break
        except ValueError:
            continue
    else:
        raise SchemaDrift(f"UF tracker: unparseable last_update {raw!r} for {state}")

    # The 2024 file really does ship a row dated 11/4/2023 (Montana). A one-digit
    # typo in the year would otherwise land a row 1,095 days from the election and
    # quietly wreck that state's x-axis, so an out-of-cycle date is a source
    # error for that state -- fall through and let a human enter it at tier 3.
    lo = date(cycle - 1, 12, 1)
    hi = election_date(cycle)
    if not (lo <= day <= hi):
        raise SourceError(
            f"UF tracker: {state} last_update {day.isoformat()} is outside "
            f"cycle {cycle} ({lo.isoformat()}..{hi.isoformat()}); likely a typo"
        )
    return day


class AggregatorAdapter(Adapter):
    """One state's statewide row out of the UF Election Lab national CSV."""

    name = "uf-election-lab"
    tier = TIER_AGGREGATOR

    def __init__(
        self,
        state: str | None = None,
        *,
        url_template: str = URL_TEMPLATE,
        path: Path | None = None,
    ) -> None:
        super().__init__(state)
        self.url_template = url_template
        #: A local file to read instead of the network. Tests use this; nothing
        #: in the pipeline sets it, so production always goes to the live URL.
        self.path = path

    # ------------------------------------------------------------------
    # Source access
    # ------------------------------------------------------------------
    def _source_key(self, cycle: int) -> str:
        return str(self.path) if self.path else self.url_template.format(cycle=cycle)

    def _download(self, cycle: int) -> str:
        if self.path is not None:
            try:
                return self.path.read_text(encoding="utf-8-sig")
            except OSError as exc:
                raise SourceError(f"UF tracker: cannot read {self.path}: {exc}") from exc

        url = self.url_template.format(cycle=cycle)
        try:
            import requests
        except ImportError as exc:  # pragma: no cover - requests is a hard dep
            raise SourceError(f"UF tracker: requests unavailable: {exc}") from exc

        try:
            resp = requests.get(
                url, timeout=HTTP_TIMEOUT, headers={"User-Agent": USER_AGENT}
            )
        except Exception as exc:  # noqa: BLE001 - requests raises a wide family
            raise SourceError(f"UF tracker: GET {url} failed: {exc}") from exc

        if resp.status_code == 404:
            # The only 404 we can interpret: that cycle predates the 2023 site
            # rebuild and has no CSV at all (true of 2022 and 2020).
            raise NotYetPublished(f"UF tracker has no file for cycle {cycle} ({url})")
        if resp.status_code != 200:
            raise SourceError(f"UF tracker: GET {url} returned {resp.status_code}")
        if not resp.text.strip():
            raise SourceError(f"UF tracker: GET {url} returned an empty body")
        return resp.text

    def _rows(self, cycle: int) -> list[dict[str, str]]:
        key = (int(cycle), self._source_key(cycle))
        cached = _CACHE.get(key)
        if cached is not None:
            return cached

        text = self._download(cycle)
        reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
        fieldnames = set(reader.fieldnames or [])
        missing = REQUIRED_COLUMNS - fieldnames
        if missing:
            raise SchemaDrift(
                f"UF tracker {cycle}: missing columns {sorted(missing)}; "
                f"got {sorted(fieldnames)[:8]}..."
            )
        rows = [r for r in reader if (r.get("state_abbv") or "").strip()]
        if not rows:
            raise SourceError(f"UF tracker {cycle}: file parsed to zero rows")

        _CACHE[key] = rows
        return rows

    def _row_for_state(self, cycle: int) -> dict[str, str]:
        want = self.state.upper()
        for row in self._rows(cycle):
            if (row.get("state_abbv") or "").strip().upper() == want:
                return row
        # NOT NotYetPublished. "This aggregator does not carry Alabama" is a gap
        # in OUR source, not evidence that Alabama has no early votes -- so the
        # ladder must fall through and let the hand-entered tier 3 answer.
        raise SourceError(f"UF tracker {cycle} has no row for {want}")

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------
    def _build(self, cycle: int, row: dict[str, str], day: date) -> FetchResult:
        state_row = StateDay(
            cycle=cycle,
            state=self.state,
            day=day,
            ballots_total=_count(row.get("voted_all")),
            # The file has no day-over-day delta; publish.py derives nothing and
            # a subtraction across a stale row would be fiction.
            ballots_new=None,
            mail_requested=_count(row.get("request_all")),
            mail_returned=_count(row.get("accept_all")),
            inperson=_count(row.get("inperson_all")),
            party_dem=_count(row.get("voted_dem")),
            party_rep=_count(row.get("voted_rep")),
            # TRAP 3: `voted_none` is "None/Minor" combined. It goes to NPA and
            # party_oth stays blank; we do not split what the source did not.
            party_npa=_count(row.get("voted_none")),
            party_oth=None,
        )

        demo_rows: list[DemoDay] = []
        for dimension, columns, mapper in (
            ("race", RACE_COLUMNS, normalize.race),
            ("sex", SEX_COLUMNS, normalize.sex),
        ):
            for column, raw_label in columns.items():
                if column not in row:
                    # A demographic column disappearing is drift: it means the
                    # layout moved under us, and the next column over would be
                    # silently attributed to the wrong bucket.
                    raise SchemaDrift(f"UF tracker {cycle}: no column {column!r}")
                bucket = mapper(raw_label)
                if bucket is None:  # pragma: no cover - guards our own typos
                    raise SchemaDrift(
                        f"UF tracker: {raw_label!r} is not a known {dimension} label"
                    )
                total = _count(row.get(column))
                if total is None:
                    continue  # unreported bucket: no row at all, never a zero
                demo_rows.append(DemoDay(
                    cycle=cycle, state=self.state, day=day,
                    dimension=dimension, bucket=bucket, ballots_total=total,
                ))

        # Age is absent on purpose. See TRAP 2 in the module docstring.
        return FetchResult(state_rows=[state_row], demo_rows=demo_rows)

    # ------------------------------------------------------------------
    # Adapter interface
    # ------------------------------------------------------------------
    def fetch(self, cycle: int, as_of: date) -> FetchResult:
        row = self._row_for_state(cycle)
        day = _parse_last_update(row.get("last_update"), cycle, self.state)

        if day > as_of:
            raise NotYetPublished(
                f"UF tracker dates {self.state} {day.isoformat()}, after {as_of}"
            )

        cast = [
            _count(row.get(col)) for col in ("voted_all", "accept_all", "inperson_all")
        ]
        if not any(c is not None for c in cast):
            # The row exists and is all zeros: the tracker is saying no ballots
            # have been cast in this state yet. That is data-does-not-exist, so
            # we STOP -- a lower tier could only manufacture the zero we are
            # refusing to write. This is the normal answer for ~48 states today.
            #
            # Note the deliberate asymmetry with a missing state above: zeros are
            # an assertion by the aggregator, a missing row is a hole in it.
            raise NotYetPublished(
                f"UF tracker reports no ballots cast in {self.state} "
                f"as of {day.isoformat()}"
            )

        return self._build(cycle, row, day)

    def fetch_history(self, cycle: int) -> FetchResult:
        """The tracker's FINAL row for a past cycle -- one snapshot, not a curve.

        The UF file is overwritten in place, so what survives for 2024 is the
        last state of the tracker rather than a daily archive. That single row
        is still the number the site's "% of 2024 final early vote" denominator
        needs, and it is dated by the state's own `last_update` so a state whose
        tracker froze on 10/25 lands on 10/25 and is visibly not a final.
        """
        row = self._row_for_state(cycle)
        day = _parse_last_update(row.get("last_update"), cycle, self.state)
        result = self._build(cycle, row, day)
        if not result.state_rows[0].ballots_total:
            raise NotYetPublished(
                f"UF tracker has no {cycle} early-vote total for {self.state}"
            )
        return result
