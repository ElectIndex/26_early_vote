"""Tier 2: civicAPI's national early-vote API.

WHAT EXISTS (verified against the live API 2026-09-06)
-----------------------------------------------------
civicAPI (https://civicapi.org) publishes a free, keyless, CORS-open JSON API of
election results, and since 2026 an early-vote tracker alongside it. Unlike the
UF Election Lab file at tier 3, this is a real documented API rather than an
undocumented constant lifted out of somebody's front-end bundle, and it carries
one thing UF does not: **county rows**.

    GET /api/v2/early-vote/<ST>/capabilities
    GET /api/v2/early-vote/<ST>/<category>              # statewide + per-county
    GET /api/v2/early-vote/<ST>/<category>/demographics?by=party|gender|race|age_range|ethnicity

`<category>` is one of `requested`, `returned`, `inperson` -- which is exactly
our `mail_requested` / `mail_returned` / `inperson`. (`grace` appears in the
capabilities payload but the category endpoint rejects it; Illinois' grace-period
votes are the only thing it could mean, and our own il.py already folds them into
`inperson`.) A category response is:

    {"state": "NC", "category": "returned", "snapshot_date": "2026-09-06",
     "regions": {"Alamance": {"Democratic": {"votes": 0, "color": "..."}, ...}, ...},
     "statewide_total": {"Democratic": {"votes": 6, ...}, ...}}

so every number arrives already split by party, statewide and per county.

WHY IT SITS AT TIER 2, ABOVE UF
-------------------------------
It answers with counties and party for a state where our own scraper broke, which
is the whole job of a fallback. UF's national CSV is statewide-only and its county
files are unusable (county NAME with no FIPS and no jurisdiction crosswalk, so
Chicago silently vanishes from Cook). This adapter has the same name problem and
refuses to publish through it -- see THE ILLINOIS RULE below -- but where names DO
resolve it delivers a county layer the tier below cannot.

FIVE TRAPS, ALL OF WHICH WOULD OTHERWISE PUBLISH CONFIDENT WRONG NUMBERS
------------------------------------------------------------------------
1. ZERO IS AMBIGUOUS, SO ZERO IS BLANK. The API writes 0 both for "no ballots yet"
   and for a category it is not carrying for that state, and nothing in the
   payload separates the two. Every count therefore goes through `_count()`, which
   maps 0 -> None. A genuine zero is unrecoverable here, and blank is the safe
   direction: "not reported" renders honestly and "0" does not. This is the same
   trade the UF adapter makes for the same reason, and it is why a state whose
   early voting has not opened publishes `mail_requested` alone rather than a row
   of zeros. See THE BLANK RULE in schema.py.

2. `capabilities.provides.party` IS AUTHORITATIVE, AND "Unspecified" IS NOT A
   PARTY. In a state with no party registration the API still returns a party
   split -- one bucket named "Unspecified" holding every ballot (Illinois:
   `{"Unspecified": {"votes": 947926}}`). `normalize.party("Unspecified")` returns
   None, which is our SchemaDrift signal, so it MUST be intercepted before it gets
   there. The gate is `capabilities.provides.party`: false means all four party
   fields are None on every row, exactly as if the state had published no party
   column at all.

3. THE AGE BANDS DO NOT LINE UP AND MUST NOT BE MAPPED. civicAPI's bands are
   "Age 18 - 25" / "26 - 40" / "41 - 65" / "Over 65"; ours are
   18-24/25-34/35-44/45-54/55-64/65+. `normalize.age_band("Age 41 - 65")` buckets
   on the low end and would cheerfully file two thirds of a state's voters under
   a band they are not in. No split recovers our bands from theirs, so this
   adapter emits NO age rows. (These are UF's bands too, and the UF adapter
   refuses them for the same reason.)

4. ITS `race` IS RACE ALONE, WHICH IS NOT WHAT THIS SITE'S `race` MEANS.
   civicAPI reports race and ethnicity as two separate breakdowns, so its "White"
   INCLUDES Hispanic white voters and its Hispanic voters live only under
   `?by=ethnicity`. Every tier-1 adapter here does the opposite -- nc.py's
   docstring states it outright: "Hispanic ethnicity overrides race", because
   early-vote coverage universally reports one combined bucket. Reconciling the
   two needs a race x ethnicity cross-tab that the API does not publish, so this
   adapter emits NO race rows either. **Sex is the one demographic it does emit**
   (Female/Male/Undesignated -> female/male/unknown, no definitional gap).

5. IT SERVES THE CURRENT ELECTION AND HAS NO ARCHIVE. There is no cycle or date
   parameter -- `?date=2026-09-01` returns `{"error": "early-vote data not
   found"}` -- so the only thing on offer is today's snapshot. A `fetch` for a
   past cycle would be answered with THIS cycle's numbers, which is why
   `_snapshot_date` refuses a date outside the requested cycle's window and raises
   SourceError (fall through to UF, which does keep a 2024 file) rather than
   NotYetPublished (which would stop the walk and take UF's answer with it).
   `fetch_history` is left unimplemented for the same reason; `ev backfill`
   `continue`s past NotYetPublished, so UF still gets its turn.

THE ILLINOIS RULE: COUNTY ROWS ARE ALL OR NOTHING
-------------------------------------------------
`regions` is keyed by the state's own jurisdiction NAME, and a jurisdiction is not
always a county. Illinois runs elections through 108 election authorities, six of
which are cities that report separately from the county around them -- civicAPI
lists "City of Chicago" (382,508 mail requests) beside a "Cook" row that is
suburban Cook only. Dropping the unresolvable names would understate Cook County
by 40%; guessing at them is worse.

So: if EVERY region name resolves through `_fips`, we publish the county layer.
If ANY does not, we publish the statewide row alone and log which names failed.
Illinois is the known case, and it costs nothing there -- il.py already publishes
Illinois' counties correctly at tier 1, and this adapter only ever runs when that
one has fallen over. A generic tier-2 source is the wrong place to keep fifty
states' worth of municipal-board crosswalks.

TERMS
-----
"Our data may be freely used for personal, non-commercial, or commercial use...
attribution is required. Please include either a link to civicapi.org (preferred)
or mention civicAPI somewhere." (civicapi.org/faq). The Info tab's data credits
carry that link; keep it there.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime

from .. import normalize
from ..calendar import election_date
from ..schema import CountyDay, DemoDay, StateDay, TIER_CIVIC
from . import _fips, _net
from .base import Adapter, FetchResult, NotYetPublished, SchemaDrift, SourceError

log = logging.getLogger(__name__)

BASE = "https://civicapi.org/api/v2/early-vote"

#: The three category endpoints, mapped to the StateDay/CountyDay field each one
#: fills. Order matters only for the log line.
CATEGORY_FIELD = {
    "requested": "mail_requested",
    "returned": "mail_returned",
    "inperson": "inperson",
}

#: The categories that are ballots CAST. `requested` is a request, not a vote, so
#: it feeds neither `ballots_total` nor the party split.
CAST_CATEGORIES = ("returned", "inperson")

#: The single bucket a no-party-registration state's split collapses to. See
#: TRAP 2 -- this string never reaches normalize.party().
NO_PARTY_BUCKET = "unspecified"

#: normalize.party() bucket -> the StateDay/CountyDay field it lands in.
PARTY_FIELD = {
    normalize.PARTY_DEM: "party_dem",
    normalize.PARTY_REP: "party_rep",
    normalize.PARTY_NPA: "party_npa",
    normalize.PARTY_OTH: "party_oth",
}

#: Parsed payloads, memoised per process and keyed by (state, path). One `ev
#: ingest` run can walk a state's ladder more than once, and the capabilities
#: document is re-read by every category.
_CACHE: dict[tuple[str, str], dict | None] = {}


def clear_cache() -> None:
    """Drop the memoised downloads. Tests call this; the CLI never needs to."""
    _CACHE.clear()


def _count(raw) -> int | None:
    """A vote count, with 0 read as "not reported". See TRAP 1.

    Anything non-numeric is drift: these are integer vote counts, so a string in
    one means we are reading a different document than we think we are.
    """
    if raw is None:
        return None
    if isinstance(raw, bool):  # bool is an int subclass; never a vote count
        raise SchemaDrift(f"civicAPI: {raw!r} is not a vote count")
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise SchemaDrift(f"civicAPI: {raw!r} is not a vote count") from exc
    return value or None


def _add(left: int | None, right: int | None) -> int | None:
    """Sum two counts, keeping None when NEITHER side reported.

    None is "not reported", so `None + 5` is 5 (we know about five ballots) while
    `None + None` stays None (we know about none of them). Writing 0 for the
    second case would assert a zero the source never asserted.
    """
    if left is None:
        return right
    if right is None:
        return left
    return left + right


def _votes(bucket) -> int | None:
    """Pull `votes` out of one `{"votes": n, "color": "#..."}` cell."""
    if not isinstance(bucket, dict):
        raise SchemaDrift(f"civicAPI: expected a bucket object, got {bucket!r}")
    if "votes" not in bucket:
        raise SchemaDrift(f"civicAPI: bucket {bucket!r} has no 'votes'")
    return _count(bucket["votes"])


class CivicApiAdapter(Adapter):
    """One state's early-vote position from civicAPI, statewide and by county."""

    name = "civicapi"
    tier = TIER_CIVIC

    def __init__(self, state: str | None = None, *, fixture_dir=None) -> None:
        super().__init__(state)
        #: A directory of saved payloads to read instead of the network, named
        #: `<ST>_<category>.json`. Tests use this; nothing in the pipeline sets
        #: it, so production always goes to the live API.
        self.fixture_dir = fixture_dir

    # ------------------------------------------------------------------
    # Source access
    # ------------------------------------------------------------------
    def _json(self, path: str, filename: str) -> dict | None:
        """GET one endpoint and parse it, memoised for the life of the process.

        Returns None when the endpoint is not there. civicAPI signals "I do not
        have this" TWO ways -- a 404 (what `<ST>/capabilities` does for a state it
        does not carry) and a 200 whose body is `{"error": ...}` -- so callers get
        one uniform answer and decide for themselves what an absence means.
        """
        key = (self.state.upper(), path)
        if key in _CACHE:
            return _CACHE[key]

        if self.fixture_dir is not None:
            fixture = self.fixture_dir / filename
            try:
                text = fixture.read_text(encoding="utf-8")
            except OSError as exc:
                raise SourceError(f"civicAPI: cannot read {fixture}: {exc}") from exc
            raw = text.encode("utf-8")
        else:
            try:
                raw = _net.get(f"{BASE}/{path}", state=self.state, filename=filename)
            except _net.Missing:
                _CACHE[key] = None
                return None

        try:
            payload = json.loads(raw)
        except ValueError as exc:
            raise SchemaDrift(f"civicAPI: {path} is not JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise SchemaDrift(f"civicAPI: {path} returned {type(payload).__name__}, not an object")

        _CACHE[key] = payload
        return payload

    def _capabilities(self) -> dict:
        """What this state reports -- and whether civicAPI carries it at all.

        A state the API does not know answers 404 -- and, on some paths, 200 with
        `{"error": "capabilities not found for this state"}`. Either way that is a
        hole in OUR source, not evidence the state has no early votes, so it is a
        SourceError and the ladder falls through to UF. (Same reasoning, and the
        same deliberate asymmetry against a zero, as aggregator.py's missing-row
        branch.)
        """
        st = self.state.upper()
        payload = self._json(f"{st}/capabilities", f"civicapi_{st}_capabilities.json")
        if payload is None:
            raise SourceError(f"civicAPI does not carry {st}: no capabilities document")
        if payload.get("error"):
            raise SourceError(f"civicAPI does not carry {st}: {payload['error']}")
        provides = payload.get("provides")
        if not isinstance(provides, dict):
            raise SchemaDrift(f"civicAPI {st}: capabilities has no 'provides' object")
        return payload

    def _category(self, category: str) -> dict | None:
        """One category payload, or None if the API does not carry it here."""
        st = self.state.upper()
        payload = self._json(f"{st}/{category}", f"civicapi_{st}_{category}.json")
        if payload is None:
            return None
        if payload.get("error"):
            return None
        if "statewide_total" not in payload:
            raise SchemaDrift(
                f"civicAPI {st}/{category}: no 'statewide_total'; "
                f"got {sorted(payload)[:6]}"
            )
        return payload

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------
    @staticmethod
    def _snapshot_date(payload: dict, cycle: int, state: str, category: str) -> date:
        """The `snapshot_date` on one category, checked against the cycle.

        The API has no cycle parameter (TRAP 5), so this is the only thing
        standing between a request for 2024 and a confident answer full of 2026
        numbers. Out of window is a SourceError so the ladder falls THROUGH to a
        source that does keep an archive -- never NotYetPublished, which would
        stop the walk and take that source's answer with it.
        """
        text = str(payload.get("snapshot_date") or "").strip()
        if not text:
            raise SchemaDrift(f"civicAPI {state}/{category}: no snapshot_date")
        try:
            day = datetime.strptime(text, "%Y-%m-%d").date()
        except ValueError as exc:
            raise SchemaDrift(
                f"civicAPI {state}/{category}: unparseable snapshot_date {text!r}"
            ) from exc

        lo = date(cycle - 1, 12, 1)
        hi = election_date(cycle)
        if not (lo <= day <= hi):
            raise SourceError(
                f"civicAPI {state}/{category} is dated {day.isoformat()}, outside "
                f"cycle {cycle} ({lo.isoformat()}..{hi.isoformat()}); the API serves "
                f"only the current election and has no archive"
            )
        return day

    def _split(self, buckets, has_party: bool, where: str) -> tuple[int | None, dict[str, int | None]]:
        """One `{party: {votes: n}}` object -> (total, {field: count}).

        The total is the sum of every bucket, party-aware or not; the per-field
        map is empty whenever the state does not register by party (TRAP 2), so
        a no-party state's four party columns stay blank rather than collecting
        an "Unspecified" pile.
        """
        if not isinstance(buckets, dict):
            raise SchemaDrift(f"civicAPI {where}: expected a party object, got {buckets!r}")

        total: int | None = None
        fields: dict[str, int | None] = {}
        for label, cell in buckets.items():
            votes = _votes(cell)
            total = _add(total, votes)
            if not has_party:
                continue
            if str(label).strip().lower() == NO_PARTY_BUCKET:
                # capabilities said this state DOES register by party, and the
                # API still handed us the no-party bucket. Two incompatible
                # claims about the same state: refuse rather than pick one.
                raise SchemaDrift(
                    f"civicAPI {where}: capabilities says party is reported but the "
                    f"split contains {label!r}"
                )
            bucket = normalize.party(label)
            if bucket is None:
                raise SchemaDrift(f"civicAPI {where}: unknown party label {label!r}")
            field = PARTY_FIELD[bucket]
            fields[field] = _add(fields.get(field), votes)
        return total, fields

    def _counties(self, payloads: dict[str, dict], has_party: bool, cycle: int, day: date):
        """County rows, or [] when a region name will not resolve. See THE ILLINOIS RULE."""
        st = self.state.upper()
        names: set[str] = set()
        for payload in payloads.values():
            regions = payload.get("regions") or {}
            if not isinstance(regions, dict):
                raise SchemaDrift(f"civicAPI {st}: 'regions' is not an object")
            names.update(regions)
        if not names:
            return []

        resolved: dict[str, tuple[str, str]] = {}
        unresolved: list[str] = []
        for name in sorted(names):
            hit = _fips.lookup(st, name)
            if hit is None:
                unresolved.append(name)
            else:
                resolved[name] = hit
        if unresolved:
            log.warning(
                "%s: civicAPI county layer withheld -- %d of %d region names are not "
                "counties (%s). Publishing the statewide row only; summing the rest "
                "would understate whichever county they belong to.",
                st, len(unresolved), len(names), ", ".join(unresolved[:6]),
            )
            return []

        rows: dict[str, CountyDay] = {}
        for name, (fips, canonical) in resolved.items():
            row = CountyDay(
                cycle=cycle, state=st, county_fips=fips, day=day, county_name=canonical,
            )
            for category, payload in payloads.items():
                buckets = (payload.get("regions") or {}).get(name)
                if buckets is None:
                    continue
                total, fields = self._split(buckets, has_party, f"{st}/{category}/{name}")
                field = CATEGORY_FIELD[category]
                if field != "mail_requested":  # CountyDay has no request column
                    setattr(row, field, total)
                if category in CAST_CATEGORIES:
                    row.ballots_total = _add(row.ballots_total, total)
                    for party_field, votes in fields.items():
                        setattr(row, party_field,
                                _add(getattr(row, party_field), votes))
            # A county that reported nothing gets no row at all -- the same rule
            # the demographic buckets follow. Every state here publishes all of
            # its counties every day, so keeping the empty ones would put a
            # hundred informationless rows a day into the county file and make a
            # county that has genuinely not started look identical to one that
            # has. Blank is carried by the county's ABSENCE, never by a row of
            # blanks.
            if any(getattr(row, f) is not None for f in (
                    "ballots_total", "mail_returned", "inperson",
                    "party_dem", "party_rep", "party_npa", "party_oth")):
                rows[fips] = row
        return list(rows.values())

    def _demographics(self, cycle: int, day: date, provides: dict) -> list[DemoDay]:
        """Sex rows for ballots cast. Age and race are refused -- TRAPS 3 and 4."""
        if not provides.get("gender"):
            return []
        st = self.state.upper()

        totals: dict[str, int | None] = {}
        for category in CAST_CATEGORIES:
            path = f"{st}/{category}/demographics"
            payload = self._json(
                f"{path}?by=gender", f"civicapi_{st}_{category}_gender.json"
            )
            if payload is None:
                continue
            if payload.get("error"):
                continue
            values = payload.get("values")
            if not isinstance(values, dict):
                raise SchemaDrift(f"civicAPI {path}: no 'values' object")
            for label, raw in values.items():
                bucket = normalize.sex(label)
                if bucket is None:
                    raise SchemaDrift(f"civicAPI {path}: unknown sex label {label!r}")
                totals[bucket] = _add(totals.get(bucket), _count(raw))

        return [
            DemoDay(cycle=cycle, state=st, day=day, dimension="sex",
                    bucket=bucket, ballots_total=total)
            for bucket, total in sorted(totals.items())
            if total is not None  # an unreported bucket gets no row, never a zero
        ]

    # ------------------------------------------------------------------
    # Adapter interface
    # ------------------------------------------------------------------
    def fetch(self, cycle: int, as_of: date) -> FetchResult:
        st = self.state.upper()
        capabilities = self._capabilities()
        has_party = bool(capabilities["provides"].get("party"))

        payloads: dict[str, dict] = {}
        days: list[date] = []
        for category in CATEGORY_FIELD:
            payload = self._category(category)
            if payload is None:
                continue
            payloads[category] = payload
            days.append(self._snapshot_date(payload, cycle, st, category))

        if not payloads:
            raise SourceError(f"civicAPI carries no early-vote category for {st}")

        # The EARLIEST contributing snapshot. The categories can update on
        # different days, and a combined row must never claim to be fresher than
        # its stalest component -- dating it forward would draw a curve the state
        # did not report.
        day = min(days)
        if day > as_of:
            raise NotYetPublished(
                f"civicAPI dates {st} {day.isoformat()}, after {as_of.isoformat()}"
            )

        state_row = StateDay(cycle=cycle, state=st, day=day)
        for category, payload in payloads.items():
            total, fields = self._split(
                payload["statewide_total"], has_party, f"{st}/{category}"
            )
            setattr(state_row, CATEGORY_FIELD[category], total)
            if category in CAST_CATEGORIES:
                state_row.ballots_total = _add(state_row.ballots_total, total)
                for field, votes in fields.items():
                    setattr(state_row, field, _add(getattr(state_row, field), votes))

        reported = (state_row.ballots_total, state_row.mail_requested)
        if not any(v is not None for v in reported):
            # Every count came back 0, which under TRAP 1 we read as "nothing
            # reported". Neither a request nor a ballot exists here yet, so we
            # STOP -- a lower tier could only manufacture the zero we are
            # refusing to write.
            raise NotYetPublished(
                f"civicAPI reports nothing for {st} as of {day.isoformat()}"
            )

        return FetchResult(
            state_rows=[state_row],
            county_rows=self._counties(payloads, has_party, cycle, day),
            demo_rows=self._demographics(cycle, day, capabilities["provides"]),
        )
