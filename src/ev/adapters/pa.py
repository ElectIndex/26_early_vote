"""Pennsylvania: the Department of State's mail-ballot file on data.pa.gov.

PA publishes one Socrata dataset per election holding ONE ROW PER MAIL BALLOT
APPLICATION -- county, party, application request date, ballot mailed date,
ballot returned date, and the application's disposition. Being Socrata means we
never download the 2.4-million-row table: SoQL aggregates server-side, so the
entire daily county x party curve comes back in ONE request -- 8,429 grouped rows
for 2024, 11,010 for 2022, 15,607 for 2020, none of them near `PAGE` -- and the
whole history is reconstructible from any single run exactly as it is for North
Carolina.

**The dataset id is discovered, never constructed.** Each election gets a fresh
four-character id (2020 general `mcba-yywm`, 2022 general `uhfm-zhus`, 2024
general `3q5t-ddp8`) that cannot be guessed, so we ask Socrata's catalog API for
the dataset whose name starts with "<cycle> General Election Mail Ballot
Requests". No such dataset means PA has not opened the cycle's file yet, which is
NotYetPublished. The catalog answer also carries the dataset's column names and
types, so one call does discovery and the schema check together.

**Three cycles reach this far back, not two.** The naming convention has held
since 2020, so the same discovery finds the 2020 general -- 3,079,710
applications, 2,648,056 of them returned, 67 counties, the same free-text party
vocabulary 2022 uses. It is the only pre-2022 early-vote series in this repo and
the only one that can give a model two CONSECUTIVE completed cycles below 2024.

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

* **`mail_requested` is APPROVED APPLICATIONS, and older vintages prove approval
  a different way.** PA's own dashboard headline is approved applications, and it
  is a live number from the first day of the cycle, months before a ballot exists
  to mail. (Michigan and Ohio fill the same field with ballots issued because
  that is all their reports give.) From 2024 the file says so outright in
  `ballot_application_disposition`.

  The 2020 and 2022 files have no disposition column and no status column of any
  name -- verified against the full column list, 11 columns in 2020 and 16 in
  2022, not just the catalog summary. What they do carry, and what this module
  did not look at for two cycles, is `ballotsentdate`: THE DATE PENNSYLVANIA
  MAILED THE BALLOT. A mailed ballot is not a guess about approval, it is
  approval carried out, so an application with one is counted and an application
  without one is not. See `MIN_BALLOT_MAILED_SHARE` for the one thing that can go
  wrong with that and the guard that stops it.

  The two readings are close enough to compare across cycles and the gap is
  measured, not assumed. 2024 is the only vintage that publishes both: 2,242,055
  approved applications, of which 2,225,938 got a ballot mailed, so the
  ballot-mailed reading recovers **99.28%** of approvals and is a floor rather
  than an estimate. It also admits almost nothing it should not -- of 162,036
  DECLINED 2024 applications only 282 (0.17%) have a mailed ballot.

* **`inperson` is always blank.** Pennsylvania has no in-person early voting.
  Voting "on demand" at a county office is legally a mail ballot applied for,
  issued and returned in one visit, and this file does not mark it, so there is
  no in-person number to report -- which is not the same as zero.

WHAT THIS FILE COULD SUPPORT AND THE SCHEMA CANNOT HOLD. `countyname` is on
every application row, so everything statewide here is available per county for
the asking -- one `$group` term, no new source and no join. Three of them have
nowhere to go today:

  * ballots MAILED per county per day. `CountyDay` has no request/sent column at
    all (civicapi.py says the same thing at its line 393), so `mail_requested` is
    a StateDay-only field. Adding one field to `CountyDay`, `COUNTY_DAILY_COLUMNS`
    and `county_row_to_dict` would give PA a 67-county sent-vs-returned pair for
    2020, 2022 and 2024 -- the denominator estimate.py says it does not have.
    The query is the one in `_load` with `{COUNTY},` prepended to `$select` and
    `$group`, and it is cheap: 9,360 grouped rows for 2020, 8,670 for 2022 and
    11,286 for 2024, one page each, one extra request per cycle.
  * `dateofbirth`, populated on 99.93% of 2020 rows and 99.99% of 2022 rows,
    which would give PA an `age` DemoDay series. It needs a documented rule for
    the confidentiality placeholder PA's own dataset description declares -- a
    protected voter's birth date is published as 1/1/1800, which
    `normalize.age_band` would silently bucket as 65+. Measured, that is 60 rows
    in 2020 and 3 in 2022, so it is a correctness detail rather than a volume
    problem; `AGE_BANDS` has no `unknown` band to put them in either way.
  * `congressional`, `senate` and `legislative` -- district, not county. There is
    no district table in schema.py.
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
#: applications and the 2020 general carries 208 for 3.1 million.
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

#: Present from the 2024 vintage on; absent in 2020/2022, where approval is read
#: off SENT instead. See the module docstring.
DISPOSITION = "ballot_application_disposition"
APPROVED = "Approved"

#: "Ballot Mailed Date", present in every vintage 2020-2024. Non-null means PA
#: put a ballot in the post for this application, which is approval carried out.
SENT = "ballotsentdate"

#: SoQL alias for "every application filed that day, mailed a ballot or not".
#: Only the ballot-mailed query selects it, and its presence in a row is how
#: `build` knows which of the two request queries it is reading.
APPLICATIONS = "apps"

REQUIRED_COLUMNS = (COUNTY, PARTY, RETURNED, REQUESTED)

#: How much of a file's applications must already have a MAILED BALLOT before
#: ballots-mailed is allowed to stand in for approved applications.
#:
#: ⚠️ THIS IS A GUARD AGAINST THE PROXY BEING RIGHT ONLY IN HINDSIGHT. Counting
#: applications PA has mailed a ballot for is a floor on approvals, and a floor is
#: only useful once it is close to the thing. On a FINISHED file it is: 99.42% of
#: 2020's applications and 99.60% of 2022's have a mailed ballot, and even a file
#: shaped like 2024's -- 6.7% of its applications declined outright -- sits at
#: 92.61%. Run the same query in August, before a single ballot has been printed,
#: and it is ~0%: a live cycle whose file lost its disposition column would
#: otherwise publish "3,000 requested" on a day 600,000 Pennsylvanians had
#: applied, which is not a cautious number, it is a wrong one.
#:
#: Below the threshold `mail_requested` goes BLANK -- THE BLANK RULE, we cannot
#: tell yet -- rather than being published low. The same pattern and the same
#: reasoning as MAX_UNKNOWN_PARTY_SHARE above: a material threshold, anchored on
#: measurements, refusing rather than guessing.
MIN_BALLOT_MAILED_SHARE = 0.90

#: Socrata reports these two types for the date columns and has used both for
#: the same column between cycles -- MEASURED, from the catalog answers this
#: module already asks for: `ballotreturneddate` is a calendar date in 2020 and
#: text in 2022 and 2024, while `appissuedate` and `ballotsentdate` are calendar
#: dates in 2020 and 2022 and `ballotsentdate` turns to text in 2024. Both render
#: ISO-8601, so both compare and sort correctly against a 'YYYY-MM-DD' literal;
#: anything else must not be silently string-compared.
DATE_TYPES = frozenset({"text", "calendar date", "calendar_date", "date"})

#: Socrata's hard ceiling per response. We page until a short page comes back;
#: the 2024 general's full county x party x day grouping is 8,429 rows, so one
#: page is the normal case and the loop is insurance.
PAGE = 50000

#: How far back the published daily curve runs. PA's permanent mail-ballot list
#: carries applications filed a year earlier, and the file holds keying typos at
#: both ends: the earliest application date is 1922-04-10 in 2020, 1940-07-07 in
#: 2022 and 1933-12-06 in 2024, and 2020's LATEST is 7020-08-21. So the axis is
#: capped, everything older is folded into the first day's cumulative total
#: rather than dropped, and anything after `as_of` -- the year 7020 included --
#: is filtered out by the same rule that stops us publishing tomorrow.
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
    # SENT is optional -- it is only consulted when the file has no disposition
    # column -- but if it is there it is date-compared like the others, so it is
    # type-checked on the same terms rather than trusted.
    for column in (RETURNED, REQUESTED, SENT):
        kind = columns.get(column)
        if kind is None:
            continue
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

    `returned` is one row per (county, party, return date). `requested` is one
    row per application date whose `n` is approved applications, or None when the
    dataset cannot tell us which applications were approved at all. A `requested`
    row that also carries `APPLICATIONS` came from the ballot-mailed query rather
    than the disposition query, and is coverage-checked before it is trusted --
    see MIN_BALLOT_MAILED_SHARE.
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
        approved = filed = 0
        for row in requested:
            day = _day(row.get("d"))
            if day is None or day > as_of:
                continue
            count = _count(row.get("n"))
            apps[day] += count
            approved += count
            # Only the ballot-mailed query carries `apps` (every application on
            # that day, mailed or not). The disposition query does not, and
            # needs no coverage guard: the file states the disposition outright.
            if APPLICATIONS in row:
                filed += _count(row.get(APPLICATIONS))

        if filed:
            share = approved / filed
            if share < MIN_BALLOT_MAILED_SHARE:
                # Not enough of this file's ballots are in the post yet for
                # "PA mailed one" to stand in for "PA approved it". Blank, not a
                # low number. See MIN_BALLOT_MAILED_SHARE.
                log.info(
                    "PA %s: only %d of %d applications on or before %s have a "
                    "mailed ballot (%.1f%%, floor %.0f%%); mail_requested stays "
                    "blank rather than understating approvals",
                    cycle, approved, filed, as_of.isoformat(),
                    share * 100, MIN_BALLOT_MAILED_SHARE * 100,
                )
                apps = None
            else:
                log.info(
                    "PA %s: mail_requested is the %d applications PA has mailed "
                    "a ballot for, %.2f%% of the %d filed -- this file has no "
                    "disposition column",
                    cycle, approved, share * 100, filed,
                )

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
        elif SENT in columns:
            # No disposition column, so approval is read off the ballot PA
            # actually mailed. `n` counts the applications with one and `apps`
            # counts them all, in ONE grouped answer, so `build` can check how
            # much of the file the proxy covers before publishing it.
            requested = self._soql(
                dataset,
                {
                    "$select": (f"{REQUESTED} AS d,count({SENT}) AS n,"
                                f"count(*) AS {APPLICATIONS}"),
                    "$where": f"{REQUESTED} IS NOT NULL",
                    "$group": "d",
                    "$order": "d",
                },
                tag="mailed",
                use_cache=use_cache,
            )
        else:
            log.info("PA: dataset %s has neither a %s nor a %s column; "
                     "mail_requested stays blank", dataset, DISPOSITION, SENT)
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
        from scratch. That reaches 2020, 2022 and 2024: all three survive this
        path, and 2022's 1.2 million returned ballots have been published from it
        since MAX_UNKNOWN_PARTY_SHARE stopped the old raise-on-first-unreadable-
        label rule from refusing the cycle. (This docstring claimed the opposite
        for a year after that stopped being true.)

        ⚠️ TWO GUARDS THAT `fetch` HAS AND THIS PATH DID NOT, both of which bite
        on `backfill --cycle <the current cycle>`, which argparse allows:

        1. `fetch` never publishes a day that has not happened; this ran to
           `election_date(cycle)` unconditionally. Asked for 2026 on 2026-09-07
           it emitted rows dated every day through 2026-11-03, each carrying
           today's cumulative total -- fifty-seven days of invented flat curve
           that `publish.py` would happily merge. The cap is now the earlier of
           Election Day and today, which is the same rule `fetch` obeys.
        2. `_net.get(use_cache=True)` is only for a file whose contents can never
           change, which a running cycle's dataset is not; it is now only passed
           for a cycle that is over.

        az.py's `_refuse_a_stale_table` is the same lesson learned the same way:
        a validation on one path and not its sibling meant the backfill published
        what the live path refuses.
        """
        # One reading of the clock, so a run crossing midnight cannot pick the
        # cache on one line and a later cap on the next.
        today = date.today()
        day_zero = election_date(cycle)
        returned, requested = self._load(cycle, use_cache=day_zero < today)
        result = build(returned, requested, cycle, min(day_zero, today))
        if not result:
            raise NotYetPublished(f"PA: the {cycle} mail-ballot dataset is empty")
        return result
