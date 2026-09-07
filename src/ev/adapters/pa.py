"""Pennsylvania: the Department of State's mail-ballot file on data.pa.gov.

PA publishes one Socrata dataset per election holding ONE ROW PER MAIL BALLOT
APPLICATION -- county, party, application request date, ballot mailed date,
ballot returned date, and the application's disposition. Being Socrata means we
never download the 2.4-million-row table: SoQL aggregates server-side, so the
entire daily county x party curve comes back as ~8,400 grouped rows in one
request, and the whole history is reconstructible from any single run exactly as
it is for North Carolina.

**The dataset id is discovered, never constructed.** Each election gets a fresh
four-character id (2022 general `uhfm-zhus`, 2024 general `3q5t-ddp8`) that
cannot be guessed, so we ask Socrata's catalog API for the dataset whose name
starts with "<cycle> General Election Mail Ballot Requests". No such dataset
means PA has not opened the cycle's file yet, which is NotYetPublished. The
catalog answer also carries the dataset's column names and types, so one call
does discovery and the schema check together.

Three judgement calls worth naming:

* **Party is whatever PA's current vocabulary is, and npa may be unreportable.**
  The 2020 and 2022 files carry the raw registration string: PA's "NF" (no
  affiliation) which normalize maps to `npa`, PA's own abbreviations ("NOP",
  "INDE", "LN") which `PA_PARTY` below maps, and a ~0.1% tail of one-off typos
  which nothing maps. That tail is counted in the ballot total and EXCLUDED from
  every party bucket -- never folded into `other` -- and the cycle is refused
  only when there is more of it than `MAX_UNKNOWN_PARTY_SHARE`. From 2024 on PA
  publishes a cleaned five-value vocabulary, DEM/REP/OTH/LIB/GRN, in which the
  unaffiliated are folded into OTH and are no longer separable. When the file's
  vocabulary contains no unaffiliated label at all, `party_npa` is written BLANK,
  not 0: 0 would claim no unaffiliated Pennsylvanian has voted, when the truth is
  that PA stopped reporting the distinction. See THE BLANK RULE in schema.py.

* **`mail_requested` is APPROVED APPLICATIONS, not ballots mailed.** PA's own
  dashboard headline is approved applications, and it is a live number from the
  first day of the cycle, months before a ballot exists to mail. (Michigan and
  Ohio fill the same field with ballots issued because that is all their reports
  give.) A file with no disposition column -- the 2020 and 2022 vintages -- gets
  a blank rather than a count of applications we cannot tell were approved.

* **`inperson` is always blank.** Pennsylvania has no in-person early voting.
  Voting "on demand" at a county office is legally a mail ballot applied for,
  issued and returned in one visit, and this file does not mark it, so there is
  no in-person number to report -- which is not the same as zero.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import date, datetime, timedelta

from ..calendar import election_date
from ..normalize import PARTY_DEM, PARTY_NPA, PARTY_OTH, PARTY_REP
from ..normalize import party as normalize_party
from ..schema import TIER_SCRAPER, CountyDay, StateDay
from . import _fips
from ._net import Missing, get
from .base import Adapter, FetchResult, NotYetPublished, SchemaDrift, SourceError

log = logging.getLogger(__name__)

#: VERIFIED. Socrata's cross-domain catalog (discovery) API.
CATALOG_URL = "https://api.us.socrata.com/api/catalog/v1"

#: VERIFIED. The SoQL endpoint for one dataset on PA's portal.
RESOURCE_URL = "https://data.pa.gov/resource/{dataset}.json"

DOMAIN = "data.pa.gov"

#: How PA has named every one of these since 2020: "<year> <Primary|General>
#: Election Mail Ballot Requests Department of State", with a shouty
#: "NO FURTHER UPDATES" appended once the election is over. We match the prefix
#: so the suffix can change without breaking discovery.
DATASET_PREFIX = "{cycle} General Election Mail Ballot Requests"

#: PENNSYLVANIA'S OWN ABBREVIATIONS, consulted BEFORE normalize.party().
#:
#: normalize.py's own note says an adapter for a state whose vocabulary it does
#: not share must bring its own table -- or.py and ct.py already do. PA's 2020
#: and 2022 files are free text: the registration form offers Democratic,
#: Republican and "No Affiliation", and everything else is typed into an Other
#: box, so the 2022 general carries 146 distinct labels for 1.4 million
#: applications.
#:
#: These are the ones that are a READING rather than a guess -- PA's official
#: abbreviations and the unmistakable spellings of "none":
#:
#:   NOP NO NON None UND UNK NOPA   the no-affiliation family, 1.35% of 2022
#:   LN LI                          Libertarian (PA's own abbreviation is LN)
#:   GR                             Green
#:   INDE                           as IND, which normalize already reads as npa
#:
#: ⚠️ NOTHING AMBIGUOUS GOES IN HERE. The residue -- `C`, `S`, `CL`, `KEY`,
#: `RF`, `DS`, `AI` and a long tail of one-off typos, together about 0.1% -- is
#: left unrecognised on purpose. It is counted, reported and EXCLUDED from every
#: bucket; it is never folded into `other`, which is rule 3 in CLAUDE.md.
PA_PARTY: dict[str, str] = {
    "nop": PARTY_NPA, "no": PARTY_NPA, "non": PARTY_NPA, "none": PARTY_NPA,
    "und": PARTY_NPA, "unk": PARTY_NPA, "nopa": PARTY_NPA, "inde": PARTY_NPA,
    "ln": PARTY_OTH, "li": PARTY_OTH, "gr": PARTY_OTH,
}

#: How much of a file's applications may carry a label we cannot read before the
#: whole cycle is refused.
#:
#: ⚠️ THIS EXISTS BECAUSE THE OLD RULE COST US A CYCLE. A single unreadable label
#: raised SchemaDrift, so PA 2022 -- 1.4 million applications, 67 counties, the
#: longest county series any party-reporting state has -- was refused outright
#: over roughly 0.1% of free-text noise, and both models lost a fold they could
#: have been measured on. Refusing a state over a rounding error is not caution;
#: it is a different way of publishing the wrong thing.
#:
#: The threshold is deliberately tight. Above it the vocabulary really has moved
#: and a mapping would be invention; below it the recognised buckets are
#: published, the residue is excluded rather than bucketed, and the share is
#: logged so it is auditable rather than silent.
MAX_UNKNOWN_PARTY_SHARE = 0.005

COUNTY = "countyname"
PARTY = "party"
RETURNED = "ballotreturneddate"
REQUESTED = "appissuedate"

#: Present from the 2024 vintage on; absent in 2020/2022, where mail_requested
#: is therefore blank rather than a guess.
DISPOSITION = "ballot_application_disposition"
APPROVED = "Approved"

REQUIRED_COLUMNS = (COUNTY, PARTY, RETURNED, REQUESTED)

#: Socrata reports these two types for the date columns and has used both for
#: the same column between cycles (`ballotreturneddate` was text in 2022 and
#: 2024, a calendar date in 2026). Both render ISO-8601, so both compare and
#: sort correctly against a 'YYYY-MM-DD' literal; anything else must not be
#: silently string-compared.
DATE_TYPES = frozenset({"text", "calendar date", "calendar_date", "date"})

#: Socrata's hard ceiling per response. We page until a short page comes back;
#: the 2024 general's full county x party x day grouping is 8,429 rows, so one
#: page is the normal case and the loop is insurance.
PAGE = 50000

#: How far back the published daily curve runs. PA's permanent mail-ballot list
#: carries applications filed a year earlier (and the file holds keying typos
#: dated 1947), so the axis is capped and everything older is folded into the
#: first day's cumulative total rather than dropped.
MAX_SPAN_DAYS = 120

_PARTY_FIELD = {
    PARTY_DEM: "party_dem", PARTY_REP: "party_rep",
    PARTY_NPA: "party_npa", PARTY_OTH: "party_oth",
}


def _json(body: bytes, what: str):
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SourceError(f"PA: {what} was not JSON: {exc}") from exc


def discover(catalog: bytes, cycle: int) -> tuple[str, dict[str, str]]:
    """(dataset id, column -> lowercased type) for `cycle`'s general election.

    Raises NotYetPublished when the catalog has no dataset for this cycle --
    the normal answer until PA opens the file, and the reason this is not a
    constructed URL.
    """
    payload = _json(catalog, "the Socrata catalog")
    results = payload.get("results")
    if not isinstance(results, list):
        raise SchemaDrift("PA: Socrata catalog response has no results list")

    prefix = DATASET_PREFIX.format(cycle=cycle).lower()
    for result in results:
        resource = result.get("resource") or {}
        name = str(resource.get("name") or "")
        if not name.lower().startswith(prefix):
            continue
        dataset = str(resource.get("id") or "")
        if not dataset:
            raise SchemaDrift(f"PA: catalog entry {name!r} carries no dataset id")
        fields = resource.get("columns_field_name") or []
        types = resource.get("columns_datatype") or []
        columns = {str(f): str(t).strip().lower() for f, t in zip(fields, types)}
        return dataset, columns

    raise NotYetPublished(
        f"PA: data.pa.gov has no '{DATASET_PREFIX.format(cycle=cycle)}' dataset yet"
    )


def check_columns(columns: dict[str, str], dataset: str) -> None:
    """Fail loudly if the dataset is not shaped the way this parser reads it."""
    missing = [c for c in REQUIRED_COLUMNS if c not in columns]
    if missing:
        raise SchemaDrift(f"PA: dataset {dataset} is missing columns {missing}")
    for column in (RETURNED, REQUESTED):
        kind = columns[column]
        if kind not in DATE_TYPES:
            # A numeric or timestamp-with-zone column would still answer a
            # `<=` comparison, just not the one we mean.
            raise SchemaDrift(
                f"PA: dataset {dataset} column {column} is a {kind!r}, not a date"
            )


def _party_of(raw: str) -> str | None:
    """PA's label -> a canonical bucket, or None when we cannot read it.

    PA's own table first, then the shared vocabulary. None is a real answer and
    the caller counts it; it never becomes `other`.
    """
    key = " ".join(str(raw).strip().lower().split())
    if not key:
        return None
    return PA_PARTY.get(key) or normalize_party(key)


def _day(raw) -> date | None:
    """Socrata renders both column types ISO-first: "2024-10-11" or
    "2026-04-20T00:00:00.000"."""
    text = str(raw or "").strip()
    if len(text) < 10:
        return None
    try:
        return datetime.strptime(text[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _count(raw) -> int:
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError) as exc:
        raise SchemaDrift(f"PA: {raw!r} is not a count") from exc


class _Bucket:
    """One geography's tallies for one day."""

    __slots__ = ("total", "party")

    def __init__(self) -> None:
        self.total = 0
        self.party: dict[str, int] = defaultdict(int)

    def add(self, other: "_Bucket") -> None:
        self.total += other.total
        for key, count in other.party.items():
            self.party[key] += count


def build(returned: list[dict], requested: list[dict] | None,
          cycle: int, as_of: date) -> FetchResult:
    """Turn PA's two grouped SoQL answers into the canonical daily rows.

    `returned` is one row per (county, party, return date); `requested` is one
    row per application date, or None when the dataset cannot tell us which
    applications were approved.
    """
    by_state: dict[date, _Bucket] = defaultdict(_Bucket)
    by_county: dict[tuple[str, date], _Bucket] = defaultdict(_Bucket)
    county_names: dict[str, str] = {}
    unknown_counties: set[str] = set()
    labels: set[str] = set()
    unreadable_labels: set[str] = set()
    counted = unreadable = 0

    for row in returned:
        # The county name and the party label are checked for every row, before
        # the date filter, so that what the file's vocabulary can express -- and
        # whether we recognise all of it -- does not depend on how far into the
        # cycle `as_of` happens to be.
        raw_county = row.get(COUNTY)
        hit = _fips.lookup("PA", raw_county)
        if hit is None:
            unknown_counties.add(" ".join(str(raw_county or "").split()))
            continue
        fips, canonical = hit
        county_names[fips] = canonical

        raw_party = str(row.get(PARTY) or "").strip()
        labels.add(raw_party)
        bucket_key = _party_of(raw_party) if raw_party else None

        day = _day(row.get("d"))
        if day is None or day > as_of:
            continue

        count = _count(row.get("n"))
        # Counted whether or not we can read the label: `total` is ballots
        # RETURNED, which is a fact about the ballot and not about the party
        # written on the registration behind it.
        for bucket in (by_state[day], by_county[(fips, day)]):
            bucket.total += count
            if bucket_key:
                bucket.party[bucket_key] += count
        counted += count
        if raw_party and bucket_key is None:
            unreadable += count
            unreadable_labels.add(raw_party)

    # ⚠️ A LABEL WE CANNOT READ IS COUNTED AND EXCLUDED, NEVER BUCKETED, and the
    # cycle is refused only when there is enough of it to matter. See
    # MAX_UNKNOWN_PARTY_SHARE for what the old raise-on-first-sight rule cost.
    if counted and unreadable:
        share = unreadable / counted
        if share > MAX_UNKNOWN_PARTY_SHARE:
            raise SchemaDrift(
                f"PA: {share:.2%} of applications carry a party label this "
                f"adapter cannot read ({sorted(unreadable_labels)[:8]}); the "
                f"vocabulary has moved and a mapping would be invention"
            )
        log.info(
            "PA %s: %d of %d applications (%.3f%%) carry an unreadable party "
            "label and are excluded from the party split, not bucketed: %s",
            cycle, unreadable, counted, share * 100,
            ", ".join(sorted(unreadable_labels)[:12]),
        )

    if unknown_counties:
        # PA has 67 counties and they do not change; a name we cannot place is a
        # county we would silently drop off the map.
        raise SchemaDrift(f"PA: unrecognised county names {sorted(unknown_counties)[:5]}")

    apps: dict[date, int] | None = None
    if requested is not None:
        apps = defaultdict(int)
        for row in requested:
            day = _day(row.get("d"))
            if day is None or day > as_of:
                continue
            apps[day] += _count(row.get("n"))

    # Which canonical buckets this file's vocabulary can even express. A bucket
    # the vocabulary cannot express is blank, not 0 -- see the module docstring
    # on PA folding the unaffiliated into OTH from 2024 on.
    expressible = {_party_of(label) for label in labels if label}
    expressible.discard(None)

    return _emit(by_state, by_county, county_names, apps, expressible, cycle, as_of)


def _emit(by_state, by_county, county_names, apps, expressible, cycle, as_of) -> FetchResult:
    result = FetchResult()
    if not by_state and not apps:
        return result

    day_zero = election_date(cycle)
    seen = list(by_state) + list(apps or ())
    start = max(min(seen), day_zero - timedelta(days=MAX_SPAN_DAYS))
    span = [start]
    while span[-1] < as_of:
        span.append(span[-1] + timedelta(days=1))

    def party_fields(bucket: _Bucket) -> dict[str, int | None]:
        return {
            field: (bucket.party.get(key, 0) if key in expressible else None)
            for key, field in _PARTY_FIELD.items()
        }

    running = _Bucket()
    for day, bucket in sorted(by_state.items()):
        if day < start:
            running.add(bucket)
    requested_running = sum(n for day, n in (apps or {}).items() if day < start)

    for day in span:
        today = by_state.get(day)
        if today:
            running.add(today)
        if apps is not None:
            requested_running += apps.get(day, 0)
        result.state_rows.append(StateDay(
            cycle=cycle, state="PA", day=day,
            ballots_total=running.total,
            ballots_new=today.total if today else 0,
            mail_requested=requested_running if apps is not None else None,
            mail_returned=running.total,
            # No in-person early voting in Pennsylvania. Never 0.
            inperson=None,
            **party_fields(running),
        ))

    baseline: dict[str, _Bucket] = defaultdict(_Bucket)
    for (fips, day), bucket in by_county.items():
        if day < start:
            baseline[fips].add(bucket)

    for fips in sorted({f for f, _ in by_county}):
        running = baseline.get(fips) or _Bucket()
        for day in span:
            today = by_county.get((fips, day))
            if today:
                running.add(today)
            if running.total == 0:
                continue
            result.county_rows.append(CountyDay(
                cycle=cycle, state="PA", county_fips=fips, day=day,
                county_name=county_names.get(fips, ""),
                ballots_total=running.total,
                ballots_new=today.total if today else 0,
                mail_returned=running.total,
                inperson=None,
                **party_fields(running),
            ))
    return result


class PAScraper(Adapter):
    """Tier 1 for Pennsylvania: the DoS mail-ballot dataset on data.pa.gov."""

    state = "PA"
    name = "pa-dos"
    tier = TIER_SCRAPER

    # ------------------------------------------------------------------
    def _catalog(self, cycle: int, *, use_cache: bool) -> bytes:
        try:
            return get(
                CATALOG_URL,
                state="PA",
                filename=f"{cycle}_catalog.json",
                params={
                    "domains": DOMAIN,
                    "search_context": DOMAIN,
                    "only": "dataset",
                    "q": DATASET_PREFIX.format(cycle=cycle),
                    "limit": "10",
                },
                use_cache=use_cache,
            )
        except Missing as exc:
            raise SourceError(f"PA: Socrata catalog is unreachable: {exc}") from exc

    def _soql(self, dataset: str, params: dict[str, str], *, tag: str,
              use_cache: bool) -> list[dict]:
        """Run one grouped SoQL query, paging until a short page comes back."""
        rows: list[dict] = []
        offset = 0
        while True:
            page = dict(params, **{"$limit": str(PAGE), "$offset": str(offset)})
            try:
                body = get(
                    RESOURCE_URL.format(dataset=dataset),
                    state="PA",
                    filename=f"{dataset}_{tag}_{offset}.json",
                    params=page,
                    use_cache=use_cache,
                    min_bytes=2,
                )
            except Missing as exc:
                raise SourceError(f"PA: dataset {dataset} is gone: {exc}") from exc
            batch = _json(body, f"the {tag} query")
            if not isinstance(batch, list):
                raise SchemaDrift(f"PA: {tag} query did not return a row list")
            rows.extend(batch)
            if len(batch) < PAGE:
                return rows
            offset += PAGE

    def _load(self, cycle: int, *, use_cache: bool) -> tuple[list[dict], list[dict] | None]:
        dataset, columns = discover(self._catalog(cycle, use_cache=use_cache), cycle)
        check_columns(columns, dataset)

        returned = self._soql(
            dataset,
            {
                "$select": f"{COUNTY},{PARTY},{RETURNED} AS d,count(*) AS n",
                "$where": f"{RETURNED} IS NOT NULL",
                "$group": f"{COUNTY},{PARTY},d",
                "$order": f"{COUNTY},{PARTY},d",
            },
            tag="returned",
            use_cache=use_cache,
        )

        requested: list[dict] | None = None
        if DISPOSITION in columns:
            requested = self._soql(
                dataset,
                {
                    "$select": f"{REQUESTED} AS d,count(*) AS n",
                    "$where": f"{REQUESTED} IS NOT NULL AND {DISPOSITION}='{APPROVED}'",
                    "$group": "d",
                    "$order": "d",
                },
                tag="approved",
                use_cache=use_cache,
            )
        else:
            log.info("PA: dataset %s has no %s column; mail_requested stays blank",
                     dataset, DISPOSITION)
        return returned, requested

    # ------------------------------------------------------------------
    def fetch(self, cycle: int, as_of: date) -> FetchResult:
        returned, requested = self._load(cycle, use_cache=False)
        result = build(returned, requested, cycle, as_of)
        if not result:
            # The dataset exists but is still empty, which is how PA opens a
            # cycle: the table is created before the first application lands.
            raise NotYetPublished(
                f"PA: the {cycle} mail-ballot dataset has no rows on or before "
                f"{as_of.isoformat()} yet"
            )
        return result

    def fetch_history(self, cycle: int) -> FetchResult:
        """A past cycle's whole daily curve.

        Every ballot in PA's file carries its own return date, so an archived
        cycle needs no daily snapshots -- the same two queries rebuild the curve
        from scratch. 2020 and 2022 do not survive this path: those vintages
        publish the raw free-text registration string, and normalize.party
        refuses to bucket labels like "NOP" or "INDE", which is SchemaDrift by
        design rather than a mis-mapped party split.
        """
        returned, requested = self._load(cycle, use_cache=True)
        result = build(returned, requested, cycle, election_date(cycle))
        if not result:
            raise NotYetPublished(f"PA: the {cycle} mail-ballot dataset is empty")
        return result
