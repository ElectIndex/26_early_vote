"""Maine: the Secretary of State's Statewide Absentee Voter Data File.

Maine publishes one pipe-delimited text file per election carrying ONE ROW PER
ABSENTEE BALLOT, with the voter's enrollment (party), the dates the ballot was
requested / issued / received, how it came back, and its accept/reject status.
Like North Carolina's file -- and unlike Michigan's or Ohio's daily snapshots --
every ballot carries its own return date, so a single download reconstructs the
whole daily curve and a missed run costs us nothing.

**Maine reports by MUNICIPALITY, and the municipality is the unit.** The file's
geography column is "RES MUNICIPALITY" and there is no county on the row, because
Maine does not administer elections by county -- town and city clerks do. So this
adapter publishes TOWN rows keyed by the 10-digit Census county-subdivision
GEOID (`adapters._towns`), and derives its COUNTY rows by summing them.

That rollup is exact, not inferred, and the reason is worth stating plainly: a
cousub GEOID is state(2) + county(3) + cousub(5), so Auburn's `2300102060`
already says "Androscoggin County" in digits 3-5. The earlier version of this
adapter was statewide-only because mapping municipality NAMES to counties needed
a crosswalk that does not exist; mapping name -> GEOID -> county needs no
crosswalk at all, because the county is inside the key.

**What does not map, and where it goes.** Of the 533 municipalities in the 2026
file, 460 resolve, 3 are ambiguous and 70 do not resolve at all:

* The 70 are unorganized-territory townships -- "T1 R9 WELS", "Prentiss Twp T7
  R3 NBPP", "Sandbar Tract Twp", "Sinclair". The Census names unorganized
  TERRITORIES, most of which aggregate many of these townships ("Central
  Aroostook UT"), so there is no township-level GEOID to key them to and
  equating a township with its territory would file a dozen places' ballots
  under one of them.
* The 3 are "Lincoln" (a town in Penobscot County AND a plantation in Oxford),
  "Unity" (a town in Waldo AND an unorganized territory in Kennebec) and
  "Rangeley" (a town AND a plantation, both in Franklin). The file separately
  names "Lincoln Plt", "Rangeley Plt" and "Unity Twp", so the bare name is
  almost certainly the town -- but "almost certainly" is a guess, and rule 3
  says do not guess a name mapping. They resolve to nothing.

Together that is 0.73% of the 2026 primary file's records and 0.67% of the 2024
general's. Those ballots ARE in the statewide row -- nothing is dropped from the
state total -- but they are in neither the town rows nor the county rows, so
**Maine's county rows do not sum to its statewide row, and are not meant to.**
Every excluded municipality is counted and named in the run log, and if the
unmapped share ever exceeds `1 - MIN_TOWN_COVERAGE` the parse raises SchemaDrift
rather than quietly publishing a thinner map: that is the tripwire for Maine
renaming the column or changing its municipality vocabulary. SchemaDrift falls
through to the aggregator, so Maine keeps a statewide line even on that day.

Three more things shape the parser:

* **The layout changed between 2024 and 2026.** The 2022 and 2024 general files
  have 21 columns headed `MUNICIPALITY|CH|DES|VOTER ID|P|...|ACC OR REJ|REJRSN|`;
  the 2026 file has 23 headed `RES MUNICIPALITY|DES|Voter Record #|P|IB|...`,
  splits date from time, and swapped the status vocabulary from ACC/REJ to
  ACT/ACU/ACH/PEN/REJ/RNC. Everything is therefore matched BY HEADER NAME through
  `_FIELD_ALIASES`, and an unmappable header is SchemaDrift.

* **The file has a TRAILER.** After the last record Maine appends its own totals
  ("Total Requested: 94279") and, in 2026, free-text footnotes. Those lines are
  not pipe-delimited, so records are identified by field count and the trailer is
  skipped. We do NOT publish Maine's trailer totals: the labels differ between
  vintages ("Total Returned" in 2024 vs "Total Returned & Accepted" in 2026) and
  every number in them is derivable from the rows we already parsed.

* **One file is posted at a time, for whatever election is next.** The link on
  the Voter Data page is the CURRENT file, which for most of 2026 is the June
  primary's, not the November general's -- and its URL is hand-stamped
  ("6-9-26 AB Voter File Final.txt"), so it cannot be constructed. We scrape the
  link, download it, and then check that the file's REQUEST DATES fall inside the
  general's own request window; if they do not, the general's file is not up yet
  and that is NotYetPublished, not an error. On the real files this separates
  cleanly: 99.7% of the 2024 general file's request dates land in its window, and
  0% of the June 2026 primary file's land in the November 2026 general's.

Maine registers voters by party, so every party_* field is a real count -- and
because `P` and `RECTYPE` sit on the same row, the file is a party-by-method
crosstab as well. `schema.MethodDay` carries it, per county per channel, on the
same VP/not-VP rule the `inperson` and `mail_returned` columns already use. The
county rows are unchanged.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from datetime import date, datetime, timedelta
from html import unescape

from ..calendar import election_date
from ..normalize import METHOD_INPERSON, METHOD_MAIL
from ..normalize import PARTY_DEM, PARTY_NPA, PARTY_OTH, PARTY_REP
from ..normalize import party as normalize_party
from ..schema import TIER_SCRAPER, CountyDay, MethodDay, StateDay, TownDay
from . import _methods, _towns
from ._net import Missing, get, looks_like_html
from .base import Adapter, FetchResult, NotYetPublished, SchemaDrift, SourceError

log = logging.getLogger(__name__)

#: VERIFIED. The Voter Data index carries a "Statewide Absentee Voter Data File
#: (Text)" link to the current election's file. The file's own URL is stamped
#: with a hand-typed date and cannot be constructed, so the index is the entry
#: point; scraping it is not a preference, it is the only stable handle.
INDEX_URL = "https://www.maine.gov/sos/elections-voting/voter-data"

#: VERIFIED. Every past election's file, newest first. Used only by
#: fetch_history.
ARCHIVE_URL = "https://www.maine.gov/sos/elections-voting/voter-data/previous-absentee-data"

HOST = "https://www.maine.gov"

#: The anchor text Maine uses for the data file on both pages.
LINK_TEXT = "statewide absentee voter"

_ANCHOR = re.compile(r'<a\b[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.IGNORECASE | re.DOTALL)
_TAGS = re.compile(r"<[^>]+>")

#: Header text (whitespace-collapsed, lowercased) -> the role we need it for.
#: Two vintages of the same report; see the module docstring.
_FIELD_ALIASES = {
    "municipality": "municipality",
    "res municipality": "municipality",
    "p": "party",
    "reqdate": "requested",
    "req date": "requested",
    "rectype": "return_method",
    "rec type": "return_method",
    "recdate": "returned",
    "rec date": "returned",
    "acc or rej": "status",
    "status": "status",
}

#: "municipality" is mapped but NOT required: at least one archived file (the
#: 2-24-2026 special) omits it entirely, and a file without it is still a
#: perfectly good statewide series. When it is absent this adapter publishes
#: statewide rows only and says so in the log -- it does not fail.
REQUIRED_ROLES = ("party", "requested", "return_method", "returned", "status")

#: Maine's own party legend, published beside the file on the absentee layout
#: page, translated into a spelling `normalize.party` recognises. Maine writes
#: "Unenrolled" where the rest of the country writes "unaffiliated", and it
#: added "NL" for the No Labels party that qualified in Maine for 2024. Neither
#: abbreviation is in the shared vocabulary, so they are expanded HERE rather
#: than by widening normalize.py for one state. An expansion that normalize
#: still does not recognise is SchemaDrift, so this table cannot rot silently.
PARTY_CODES = {
    "D": "Democratic",
    "R": "Republican",
    "U": "unaffiliated",     # Maine's legend: "Unenrolled"
    "G": "Green",            # Maine's legend: "Green Independent"
    "L": "Libertarian",
    "NL": "No Labels",
}

#: A ballot that came back and counts. ACC is the 2022/2024 spelling; ACT/ACU/ACH
#: are 2026's accepted, accepted-cured and accepted-cured-challenged.
ACCEPTED = frozenset({"ACC", "ACT", "ACU", "ACH"})

#: Returned but not counted, or returned and still being cured. Known and
#: deliberately excluded -- listing them means an unknown status is drift rather
#: than something we quietly treat as a rejection.
NOT_ACCEPTED = frozenset({"REJ", "RNC", "PEN"})

#: The only return method that means the voter voted early IN PERSON: Maine
#: absentee voting at the clerk's counter. Every other return method (mailed,
#: dropbox, delivered by the voter or a third party, electronic) is a mail
#: ballot coming back, whoever carried it.
IN_PERSON_RETURN = "VP"

#: How far before Election Day a file's request dates must reach for it to be
#: that election's file. Maine accepts absentee requests up to three months
#: out, so the general's earliest requests land ~92 days before; 120 days is
#: comfortably wider than that and still ends five months after a June primary.
REQUEST_WINDOW_DAYS = 120

#: Fraction of a file's request dates that must fall in the window above.
#: Ongoing and UOCAVA requests carry the date they were first filed, which can
#: be a year or more earlier, so this is a majority test and not an all-test.
WINDOW_SHARE = 0.5

#: How far back the published daily curve runs. Returns before this are real and
#: are folded into the first day's cumulative total rather than dropped -- the
#: cap exists because the files carry keying typos (a 2024 ballot stamped
#: "1520-10-10") that would otherwise stretch the axis by five centuries.
MAX_SPAN_DAYS = 120

#: How many archived files fetch_history will download before giving up. The
#: 2024 file is 37 MB, so probing the whole archive page is not free.
MAX_ARCHIVE_PROBES = 6

#: Share of ACCEPTED ballots whose municipality must resolve to a census GEOID
#: before the town and county tables are trusted. Maine currently runs at 99.3%
#: and the shortfall is unorganized territory, which is structural and will not
#: grow. A drop below this is not "a few more townships" -- it is the column
#: moving or the vocabulary changing wholesale, which is drift.
MIN_TOWN_COVERAGE = 0.90

#: How many unmapped municipality names to name in the log line. All of them are
#: counted; listing 70 townships every run would bury the message.
UNMAPPED_LOG_LIMIT = 12

_PARTY_FIELD = {
    PARTY_DEM: "party_dem", PARTY_REP: "party_rep",
    PARTY_NPA: "party_npa", PARTY_OTH: "party_oth",
}


def _text(html: str) -> str:
    return " ".join(unescape(_TAGS.sub(" ", html)).split())


def links(page: bytes) -> list[str]:
    """Absolute URLs of every "Statewide Absentee Voter Data File" link, in
    page order (Maine lists newest first)."""
    html = page.decode("utf-8", errors="replace")
    found: list[str] = []
    for href, label in _ANCHOR.findall(html):
        if LINK_TEXT not in _text(label).lower():
            continue
        if not href.lower().endswith(".txt"):
            # The same anchor text is also used for the layout page, which is
            # HTML documentation rather than the data file.
            continue
        url = href if href.startswith("http") else HOST + href
        if url not in found:
            found.append(url)
    return found


def _day(raw: str) -> date | None:
    """The date out of "10/23/2024" or "10/23/2024 02:52 pm", or None."""
    token = (raw or "").strip().split(" ")[0]
    if not token:
        return None
    try:
        return datetime.strptime(token, "%m/%d/%Y").date()
    except ValueError:
        return None


def _party(raw: str | None) -> str | None:
    """A canonical party bucket, or None when the row states no enrollment."""
    code = (raw or "").strip().upper()
    if not code:
        return None
    expanded = PARTY_CODES.get(code)
    if expanded is None:
        raise SchemaDrift(f"ME: unrecognised party code {code!r}")
    bucket = normalize_party(expanded)
    if bucket is None:
        raise SchemaDrift(f"ME: normalize.party does not know {expanded!r} (code {code!r})")
    return bucket


def _header(line: str) -> tuple[dict[str, int], int]:
    """(role -> column index, field count) for one of Maine's two layouts."""
    cells = line.split("|")
    index: dict[str, int] = {}
    for position, cell in enumerate(cells):
        role = _FIELD_ALIASES.get(" ".join(cell.strip().lower().split()))
        if role and role not in index:
            index[role] = position
    missing = [r for r in REQUIRED_ROLES if r not in index]
    if missing:
        raise SchemaDrift(f"ME: absentee file header is missing {missing}: {line[:200]!r}")
    return index, len(cells)


class _Bucket:
    """One day's tallies."""

    __slots__ = ("total", "mail", "inperson", "requested", "party")

    def __init__(self) -> None:
        self.total = 0
        self.mail = 0
        self.inperson = 0
        self.requested = 0
        self.party: dict[str, int] = defaultdict(int)

    def add(self, other: "_Bucket") -> None:
        self.total += other.total
        self.mail += other.mail
        self.inperson += other.inperson
        self.requested += other.requested
        for key, count in other.party.items():
            self.party[key] += count


def parse(body: bytes, cycle: int, as_of: date) -> FetchResult:
    """Parse one Statewide Absentee Voter Data File into statewide daily rows.

    Raises NotYetPublished when the file is a real Maine absentee file but for a
    different election than `cycle`'s general -- which is what the live link is
    for most of an election year.
    """
    if looks_like_html(body):
        raise SourceError("ME: absentee data file came back as HTML, not text")

    text = body.decode("utf-8", errors="replace")
    lines = text.split("\n")
    index, width = _header(lines[0])

    by_day: dict[date, _Bucket] = defaultdict(_Bucket)
    #: (10-digit cousub GEOID, return day) -> tallies.
    by_town: dict[tuple[str, date], _Bucket] = defaultdict(_Bucket)
    #: (10-digit cousub GEOID, method, return day) -> tallies. Rolled up to the
    #: county the same exact way the county rows are: the GEOID carries it.
    by_town_method: dict[tuple[str, str, date], _Bucket] = defaultdict(_Bucket)
    town_names: dict[str, str] = {}
    #: municipality name -> accepted ballots we could not place. Counted, never
    #: silently dropped; reported below and in the log.
    unmapped: dict[str, int] = defaultdict(int)
    #: Maine writes the same name on every one of a town's ~800 rows, so the
    #: normalise-and-join work is done once per name rather than once per ballot.
    resolved: dict[str, tuple[str, str] | None] = {}
    has_municipality = "municipality" in index
    accepted_total = 0

    requested_days: list[date] = []
    records = 0

    for line in lines[1:]:
        cells = line.rstrip("\r").split("|")
        if len(cells) != width:
            # The trailer: Maine's own totals and footnotes, not pipe-delimited.
            continue
        records += 1

        requested = _day(cells[index["requested"]])
        if requested is not None:
            requested_days.append(requested)

        # Party and status are validated on every record, accepted or not, so
        # that a vocabulary change shows up as drift wherever it lands rather
        # than only when it happens to hit a counted ballot.
        party = _party(cells[index["party"]])

        status = cells[index["status"]].strip().upper()
        if status and status not in ACCEPTED and status not in NOT_ACCEPTED:
            raise SchemaDrift(f"ME: unrecognised ballot status {status!r}")
        if status not in ACCEPTED:
            continue

        returned = _day(cells[index["returned"]])
        if returned is None:
            # Accepted with no return date is a clerk keying gap, not a ballot we
            # can place on the curve.
            continue

        accepted_total += 1
        in_person = cells[index["return_method"]].strip().upper() == IN_PERSON_RETURN
        band = METHOD_INPERSON if in_person else METHOD_MAIL

        def tally(bucket: _Bucket) -> None:
            bucket.total += 1
            if in_person:
                bucket.inperson += 1
            else:
                bucket.mail += 1
            if party:
                bucket.party[party] += 1

        tally(by_day[returned])

        if has_municipality:
            name = cells[index["municipality"]].strip()
            if name not in resolved:
                resolved[name] = _towns.lookup("ME", name)
            hit = resolved[name]
            if hit is None:
                # Unorganized territory, or one of the three ambiguous names.
                # Counted here so the exclusion is visible; see the docstring.
                unmapped[name] += 1
            else:
                geoid, canonical = hit
                town_names[geoid] = canonical
                tally(by_town[(geoid, returned)])
                tally(by_town_method[(geoid, band, returned)])

    if not records:
        raise SchemaDrift("ME: absentee file has a header but no records")

    day_zero = election_date(cycle)
    if not requested_days:
        raise SchemaDrift("ME: absentee file carries no parseable request dates")
    if not _is_this_election(requested_days, day_zero):
        raise NotYetPublished(
            f"ME: the posted absentee file is for another election "
            f"(request dates run {min(requested_days).isoformat()}.."
            f"{max(requested_days).isoformat()}, not the {cycle} general)"
        )

    for requested in requested_days:
        if requested <= as_of:
            by_day[requested].requested += 1

    if has_municipality:
        _report_coverage(unmapped, accepted_total, len(town_names))
    else:
        log.info("ME: this file has no municipality column; statewide rows only")

    return _emit(by_day, by_town, by_town_method, town_names, cycle, as_of, day_zero)


def _report_coverage(unmapped: dict[str, int], accepted: int, towns: int) -> None:
    """Log what did not map, and refuse the file if too little did.

    An unmapped municipality is never silently dropped: its ballots stay in the
    statewide row, it is named here, and the share it represents is the thing
    that decides whether the town and county tables can be trusted at all.
    """
    lost = sum(unmapped.values())
    coverage = 1.0 - (lost / accepted) if accepted else 0.0
    if unmapped:
        worst = sorted(unmapped.items(), key=lambda kv: -kv[1])
        shown = ", ".join(f"{name} ({count})" for name, count in worst[:UNMAPPED_LOG_LIMIT])
        log.warning(
            "ME: %d municipalit%s (%d ballots, %.2f%% of %d accepted) have no "
            "census county-subdivision GEOID and are excluded from the town and "
            "county tables: %s%s",
            len(unmapped), "y" if len(unmapped) == 1 else "ies", lost,
            100 * lost / accepted if accepted else 0.0, accepted, shown,
            "" if len(worst) <= UNMAPPED_LOG_LIMIT else f", +{len(worst) - UNMAPPED_LOG_LIMIT} more",
        )
    log.info("ME: %d municipalities mapped, %.2f%% of accepted ballots placed",
             towns, 100 * coverage)
    if accepted and coverage < MIN_TOWN_COVERAGE:
        raise SchemaDrift(
            f"ME: only {100 * coverage:.1f}% of accepted ballots map to a census "
            f"county-subdivision GEOID (floor is {100 * MIN_TOWN_COVERAGE:.0f}%); "
            f"the municipality column or its vocabulary has changed"
        )


def _is_this_election(requested: list[date], day_zero: date) -> bool:
    """Do this file's request dates belong to `day_zero`'s general election?"""
    if not requested:
        return False
    opens = day_zero - timedelta(days=REQUEST_WINDOW_DAYS)
    inside = sum(1 for day in requested if opens <= day <= day_zero)
    return inside / len(requested) > WINDOW_SHARE


def _regroup(by_key: dict[tuple[str, date], _Bucket]) -> dict[str, dict[date, _Bucket]]:
    """(geography, day) -> tallies, regrouped as geography -> day -> tallies."""
    out: dict[str, dict[date, _Bucket]] = defaultdict(dict)
    for (geography, day), bucket in by_key.items():
        out[geography][day] = bucket
    return out


def _walk(series: dict[date, _Bucket], span: list[date], start: date):
    """Yield (day, today, running) along `span` for one geography.

    Ballots returned BEFORE the published axis are real and are folded into the
    first day's cumulative total rather than dropped -- the same rule the
    statewide series uses. Days before the geography's first ballot are skipped
    entirely, so a town contributes rows from the day it starts voting rather
    than a month of zeros.
    """
    running = _Bucket()
    for day, bucket in sorted(series.items()):
        if day < start:
            running.add(bucket)
    for day in span:
        today = series.get(day)
        if today:
            running.add(today)
        if running.total == 0:
            continue
        yield day, today, running


def _emit(by_day: dict[date, _Bucket], by_town: dict[tuple[str, date], _Bucket],
          by_town_method: dict[tuple[str, str, date], _Bucket],
          town_names: dict[str, str], cycle: int, as_of: date,
          day_zero: date) -> FetchResult:
    result = FetchResult()
    # Present even when empty, so a caller can always ask a Maine result for its
    # town rows without a getattr dance.
    _towns.attach(result, [])
    _methods.attach(result, [])
    days = [d for d in by_day if d <= as_of]
    if not days:
        return result

    start = max(min(days), day_zero - timedelta(days=MAX_SPAN_DAYS))

    running = _Bucket()
    for day, bucket in sorted(by_day.items()):
        if day < start:
            # Real ballots from before the published axis (and any keying typo
            # from 1520) -- counted, in the first day's cumulative total.
            running.add(bucket)

    span = [start]
    while span[-1] < as_of:
        span.append(span[-1] + timedelta(days=1))

    for day in span:
        today = by_day.get(day)
        if today:
            running.add(today)
        result.state_rows.append(StateDay(
            cycle=cycle, state="ME", day=day,
            ballots_total=running.total,
            ballots_new=today.total if today else 0,
            # Every absentee ballot Maine issues is requested; the file has no
            # separate "issued but not requested" population, so requested is
            # the application count as of this day.
            mail_requested=running.requested,
            mail_returned=running.mail,
            inperson=running.inperson,
            # Maine registers by party and reports every bucket, so a party with
            # no ballots yet is a real 0 -- blank would claim Maine does not
            # report party at all.
            **{field: running.party.get(key, 0) for key, field in _PARTY_FIELD.items()},
        ))

    if not by_town:
        return result

    # The county table is the town table summed, and it is EXACT: a cousub GEOID
    # carries its county in digits 3-5, so this is arithmetic on a key rather
    # than a name crosswalk. Municipalities that did not resolve are absent from
    # both, which is why these county rows do not sum to the statewide row above.
    by_county: dict[tuple[str, date], _Bucket] = defaultdict(_Bucket)
    for (geoid, day), bucket in by_town.items():
        by_county[(_towns.county_of(geoid), day)].add(bucket)

    town_rows: list[TownDay] = []
    for geoid, series in sorted(_regroup(by_town).items()):
        for day, today, running in _walk(series, span, start):
            town_rows.append(TownDay(
                cycle=cycle, state="ME", town_geoid=geoid, day=day,
                town_name=town_names.get(geoid, ""),
                ballots_total=running.total,
                ballots_new=today.total if today else 0,
                mail_returned=running.mail,
                inperson=running.inperson,
                **{field: running.party.get(key, 0) for key, field in _PARTY_FIELD.items()},
            ))
    _towns.attach(result, town_rows)

    # The party-by-method crosstab, rolled up from towns to counties by the same
    # arithmetic on the GEOID that the county rows use. A band a county has not
    # used yet is simply absent until its first ballot.
    by_county_method: dict[tuple[str, str, date], _Bucket] = defaultdict(_Bucket)
    for (geoid, band, day), bucket in by_town_method.items():
        by_county_method[(_towns.county_of(geoid), band, day)].add(bucket)

    method_rows: list[MethodDay] = []
    for fips, band in sorted({(f, b) for f, b, _ in by_county_method}):
        series = {d: v for (f, b, d), v in by_county_method.items()
                  if f == fips and b == band}
        for day, today, running in _walk(series, span, start):
            method_rows.append(MethodDay(
                cycle=cycle, state="ME", county_fips=fips, day=day,
                method=band, county_name=_towns.county_name_of("ME", fips),
                ballots_total=running.total,
                ballots_new=today.total if today else 0,
                **{field: running.party.get(key, 0)
                   for key, field in _PARTY_FIELD.items()},
            ))
    _methods.attach(result, method_rows)

    for fips, series in sorted(_regroup(by_county).items()):
        for day, today, running in _walk(series, span, start):
            result.county_rows.append(CountyDay(
                cycle=cycle, state="ME", county_fips=fips, day=day,
                county_name=_towns.county_name_of("ME", fips),
                ballots_total=running.total,
                ballots_new=today.total if today else 0,
                mail_returned=running.mail,
                inperson=running.inperson,
                **{field: running.party.get(key, 0) for key, field in _PARTY_FIELD.items()},
            ))
    return result


class MEScraper(Adapter):
    """Tier 1 for Maine: the SoS Statewide Absentee Voter Data File.

    Statewide, town and county rows. Towns are the real unit in Maine and the
    counties are summed from them; see the module docstring for what does not
    map and where those ballots go.
    """

    state = "ME"
    name = "me-sos"
    tier = TIER_SCRAPER

    def _stamped(self, result: FetchResult) -> FetchResult:
        """Stamp the town rows.

        `FetchResult.stamp()` covers the three tables `adapters.base` knows
        about, and ladder.py calls it for us. Town rows ride on the result as an
        attribute (see `_towns.attach`), so they need this one extra line --
        forget it and schema.py refuses the write by name rather than publishing
        rows with no provenance.
        """
        return _towns.stamp(result, self.provenance())

    def _index(self, url: str) -> list[str]:
        try:
            page = get(url, state="ME", filename=url.rstrip("/").rsplit("/", 1)[-1] + ".html")
        except Missing as exc:
            raise SourceError(f"ME: {url} is gone") from exc
        found = links(page)
        if not found:
            raise SchemaDrift(f"ME: no absentee data file link on {url}")
        return found

    def _download(self, url: str, *, use_cache: bool) -> bytes:
        filename = url.rsplit("/", 1)[-1] or "absentee.txt"
        try:
            return get(url, state="ME", filename=filename, use_cache=use_cache, min_bytes=4096)
        except Missing as exc:
            raise SourceError(f"ME: linked absentee file {url} is missing") from exc

    def fetch(self, cycle: int, as_of: date) -> FetchResult:
        url = self._index(INDEX_URL)[0]
        # The filename is hand-stamped, so log which one we actually took --
        # "which file was that?" is the first question on any Maine surprise.
        log.info("ME: current absentee file is %s", url)
        return self._stamped(parse(self._download(url, use_cache=False), cycle, as_of))

    def fetch_history(self, cycle: int) -> FetchResult:
        """The archived file for a past cycle's general.

        Maine keeps every election's file, but the archive page labels them only
        in prose and stamps the URLs by hand, so we probe the candidates whose
        filename carries the cycle's digits and take the first whose request
        dates say it is that general. Four-digit matches go first because they
        are nearly always exact ("2024-11-05 Final AB Voter File.txt"); the
        two-digit fallback exists because older files are stamped like
        "Absentee Voter File11822_0.txt" and match nothing else. Each probe is a
        20-40 MB download, hence the cap and the cache.
        """
        archive = self._index(ARCHIVE_URL)
        candidates: list[str] = []
        for digits in (str(cycle), f"{cycle % 100:02d}"):
            for url in archive:
                if digits in url.rsplit("/", 1)[-1] and url not in candidates:
                    candidates.append(url)
        problems: list[str] = []
        for url in candidates[:MAX_ARCHIVE_PROBES]:
            try:
                return self._stamped(parse(self._download(url, use_cache=True), cycle,
                                           election_date(cycle)))
            except NotYetPublished as exc:
                problems.append(str(exc))
        raise NotYetPublished(
            f"ME: no archived file for the {cycle} general ({'; '.join(problems) or 'no candidates'})"
        )
