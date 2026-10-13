"""Virginia: ELECT's raw absentee export, published through the ENR system.

Virginia reports early voting by LOCALITY -- 95 counties plus 38 independent
cities, 133 in all -- as one long CSV with a row per
`locality x congressional district x age x gender x reason x application type x
receipt type` and a count. The Department of Elections refreshes it through the
45-day early-voting window and then freezes it as the certified file.

**The independent cities are the whole difficulty.** Fairfax city (51600) is not
Fairfax County (51059); the same trap is set for Richmond, Franklin and Roanoke,
and `_fips.lookup` deliberately refuses a bare "FAIRFAX" rather than guess. It
does not have to guess here, because ELECT always writes the disambiguating word:
the file says "FAIRFAX CITY" and "FAIRFAX COUNTY" on separate rows, which
`_fips.lookup` resolves exactly. A locality it cannot place is SchemaDrift -- an
unplaced Virginia locality is not a rounding error, it is either a whole city
missing from the map or, far worse, a city's ballots credited to its county.

The one spelling ELECT uses that the census table does not is "KING & QUEEN
COUNTY" for "King and Queen County". That is an ampersand, not an ambiguity, so
it is translated explicitly in LOCALITY_SPELLINGS below and nowhere else.

**Method comes from ApplicationType, not ReceiptType.** A Virginia early
in-person voter applies and votes at the registrar's office in one motion and is
recorded with ApplicationType "In Person"; that column reproduces ELECT's own
published in-person early-vote total (1,858,721 for the 2024 general). ReceiptType
says how a ballot came BACK, so a mailed ballot handed in at the office reads
"In Person" there while remaining a mail ballot -- bucketing on it would move
about 5,000 mail ballots into the in-person headline. A blank ReceiptType means
the ballot was issued and has not come back yet, which is `mail_requested` minus
`mail_returned`, never a zero.

Both column vocabularies are whitelisted. A new ELECT application form would
otherwise fall silently into the mail bucket, so an unrecognised label raises
SchemaDrift and the ladder drops to the aggregator for the day.

The file also carries AgeRange and Gender. Gender maps cleanly onto
`normalize.sex`, but ELECT's age bands ("18 to 25", "26 to 40", "41 to 60",
"61 to 70", "Over 70") do not line up with `normalize.AGE_BANDS` at any boundary
-- "41 to 60" would land in "35-44" -- so no demographic rows are emitted here
rather than published wrong.

Virginia does not register voters by party, so every party_* field is None. See
THE BLANK RULE in schema.py.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import time
import urllib.parse
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from ..calendar import election_date
from ..normalize import cd_code
from ..schema import TIER_SCRAPER, CountyDay, DistrictDay, StateDay
from . import _districts, _fips
from ._net import (DEFAULT_HEADERS, DEFAULT_TIMEOUT, SESSION, Missing, _throttle,
                   impersonated_headers,
                   cache_path, get, looks_like_html)
from .base import Adapter, FetchResult, NotYetPublished, SchemaDrift, SourceError

log = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Endpoints. All three VERIFIED live against the 2024 November General.
# --------------------------------------------------------------------------
ENR = "https://enr.elections.virginia.gov"

#: Lists Virginia's ENR jurisdiction id and every election it publishes, so the
#: cycle's election key is discovered rather than guessed. The 2026 general is
#: simply absent from this list until ELECT stands it up -- that absence is the
#: NotYetPublished signal, and it is the normal state of the world until autumn.
JURISDICTION_URL = f"{ENR}/results/public/api/jurisdictions/Virginia"

#: One election's metadata, including its downloadable public reports.
ELECTION_URL = ENR + "/results/public/api/elections/Virginia/{election}"

#: The report itself. `blob` changes on every republish, which is exactly why it
#: is read out of the election metadata instead of being pinned here.
BLOB_URL = ENR + "/cdn/results/{jurisdiction}/{blob}"

#: ELECT writes CongressionalDistrict "0" on a handful of rows -- the certified
#: 2024 file has five, all Harrisonburg City in-person voters -- for a ballot
#: the locality could not place in a district. It is not an unknown district
#: (there is no VA-00) and not drift: the ballot is real and belongs to the
#: locality's and the state's totals, it just carries no district. Refusing
#: the whole file over five ballots is what `backfill --cycle 2024` did from
#: 2026-10-09 until 2026-10-13, so the code now reads "0" as "unassigned".
UNASSIGNED_DISTRICT = "0"

#: The report we want, matched case-insensitively on `reportName`.
ABSENTEE_REPORT = "enrabsenteerawcsv"

#: Virginia's PREVIOUS election-night system, retired for elections after 2022
#: but still serving its archived files. Same eight columns, byte for byte.
#: VERIFIED live for the 2022 November General.
LEGACY_URL = ("https://results.elections.virginia.gov/vaelections/"
              "{election}/Site/Statistics/Absentee.csv")

#: Cycle -> the legacy system's folder name. Only cycles that predate the ENR
#: cutover appear here; everything later is served by the API above.
LEGACY_ELECTIONS = {2022: "2022 November General"}

# --------------------------------------------------------------------------
# The CSV
# --------------------------------------------------------------------------
LOCALITY = "localityname"
APPLICATION = "applicationtype"
RECEIPT = "receipttype"
COUNT = "thecount"

REQUIRED = (LOCALITY, APPLICATION, RECEIPT, COUNT)

#: Present in every ELECT export since 2022 but not REQUIRED: without it the
#: file still yields its localities, and only the district rows are lost.
DISTRICT = "congressionaldistrict"

#: ApplicationType for a voter who applied and voted at the registrar's office.
INPERSON_APPLICATION = "in person"

#: Every other ApplicationType ELECT has written in the 2022 and 2024 files.
#: These are all ballots SENT to a voter; a new one here is SchemaDrift, because
#: defaulting an unknown form to "mail" is exactly the silent mis-bucketing that
#: rule 3 in CLAUDE.md exists to prevent.
MAIL_APPLICATIONS = frozenset({
    "annual absentee application - sbe 703.1",
    "business/personal/medical emergency application - sbe 705.1-705.2",
    "emergency application - sbe 705",
    "federal post cards application - fpca",
    "national/state emergency",
    "permanent absentee application - sbe 703.1",
    "temporary change of info - sbe 703.1c",
    "virginia specific application - sbe 701",
    "write-in absentee ballot, federal - fwab",
})

#: A non-blank ReceiptType means the ballot came back, by whichever channel.
#: Blank means issued and still outstanding -- not zero, and not a return.
RETURNED_RECEIPTS = frozenset({
    "designated representative", "drop off", "in person",
    "mail", "mail (non-usps)",
})

#: Source spellings the census name table does not carry. ELECT writes an
#: ampersand where the census writes the word. This is a spelling difference,
#: not an ambiguity, so translating it is safe -- and note what is NOT here:
#: bare "FAIRFAX"/"RICHMOND"/"FRANKLIN"/"ROANOKE" stay unresolvable on purpose.
LOCALITY_SPELLINGS = {
    "king & queen county": "King and Queen County",
}

#: Virginia's full complement of localities. A file carrying fewer than this is
#: a partial export, and summing it would understate the state.
EXPECTED_LOCALITIES = len(_fips.CENSUS_COUNTIES["VA"])


def _key(raw) -> str:
    """Lowercase, collapse whitespace, and fold the en dash ELECT uses in FPCA."""
    text = " ".join(str(raw or "").strip().lower().split())
    return text.replace("–", "-").replace("—", "-")


def _count(value) -> int:
    """A count. Virginia writes one on every row; a blank here is a broken row."""
    text = str(value or "").strip().replace(",", "")
    if not text:
        raise SchemaDrift("VA: absentee row carries no count")
    try:
        return int(float(text))
    except ValueError as exc:
        raise SchemaDrift(f"VA: {value!r} is not a count") from exc


def _add(*values: int | None) -> int | None:
    """Sum the reported parts of a measure, or None if none was reported."""
    known = [v for v in values if v is not None]
    return sum(known) if known else None


class _Tally:
    """One locality's running totals, kept as None until something is reported."""

    __slots__ = ("inperson", "mail_sent", "mail_returned")

    def __init__(self) -> None:
        self.inperson: int | None = None
        self.mail_sent: int | None = None
        self.mail_returned: int | None = None

    def add(self, field: str, n: int) -> None:
        setattr(self, field, (getattr(self, field) or 0) + n)


def parse(body: bytes, cycle: int, day: date) -> FetchResult:
    """Parse one ELECT absentee export into canonical rows.

    `day` is supplied by the caller because the file itself carries no as-of
    date -- ELECT publishes the timestamp beside the file, not inside it.
    """
    if looks_like_html(body):
        raise SourceError("VA: absentee export came back as HTML, not CSV")

    text = body.decode("utf-8-sig", errors="replace")
    reader = csv.reader(io.StringIO(text))
    try:
        header = [_key(c) for c in next(reader)]
    except StopIteration as exc:
        raise SchemaDrift("VA: absentee export is empty") from exc

    index = {name: i for i, name in enumerate(header) if name}
    missing = [c for c in REQUIRED if c not in index]
    if missing:
        raise SchemaDrift(f"VA: absentee export is missing columns {missing}")

    tallies: dict[str, _Tally] = defaultdict(_Tally)
    #: Per congressional district, the same three running totals. ELECT labels
    #: every row with its district, so these are a COUNT, not a split.
    by_district: dict[str, _Tally] = defaultdict(_Tally)
    names: dict[str, str] = {}
    unknown_locality: set[str] = set()
    unknown_district: set[str] = set()
    unknown_application: set[str] = set()
    unknown_receipt: set[str] = set()

    for row in reader:
        if not row or len(row) <= max(index[c] for c in REQUIRED):
            continue
        raw_locality = " ".join(str(row[index[LOCALITY]] or "").split())
        if not raw_locality:
            continue

        hit = _fips.lookup("VA", LOCALITY_SPELLINGS.get(_key(raw_locality), raw_locality))
        if hit is None:
            # Either a name we do not know or -- the dangerous case -- a bare
            # "FAIRFAX" that could be the city or the county. Never guess.
            unknown_locality.add(raw_locality)
            continue

        application = _key(row[index[APPLICATION]])
        receipt = _key(row[index[RECEIPT]])
        if application != INPERSON_APPLICATION and application not in MAIL_APPLICATIONS:
            unknown_application.add(application)
            continue
        if receipt and receipt not in RETURNED_RECEIPTS:
            unknown_receipt.add(receipt)
            continue

        fips, canonical = hit
        names[fips] = canonical
        n = _count(row[index[COUNT]])
        targets = [tallies[fips]]
        if DISTRICT in index:
            raw_cd = str(row[index[DISTRICT]] or "").strip()
            if raw_cd == UNASSIGNED_DISTRICT:
                # Counted for the locality and the state, attributed to no
                # district. See UNASSIGNED_DISTRICT.
                cd = None
            else:
                cd = cd_code("VA", raw_cd) if raw_cd else None
                if raw_cd and cd is None:
                    unknown_district.add(raw_cd)
            if cd:
                targets.append(by_district[cd])
        for tally in targets:
            if application == INPERSON_APPLICATION:
                tally.add("inperson", n)
            else:
                tally.add("mail_sent", n)
                if receipt:
                    tally.add("mail_returned", n)

    if unknown_locality:
        raise SchemaDrift(f"VA: unresolved localities {sorted(unknown_locality)[:5]}")
    if unknown_application:
        raise SchemaDrift(f"VA: unknown application types {sorted(unknown_application)[:5]}")
    if unknown_receipt:
        raise SchemaDrift(f"VA: unknown receipt types {sorted(unknown_receipt)[:5]}")
    if unknown_district:
        raise SchemaDrift(f"VA: unknown congressional districts {sorted(unknown_district)[:5]}")
    if not tallies:
        raise SchemaDrift("VA: absentee export produced no locality rows")

    county_rows = [
        CountyDay(
            cycle=cycle, state="VA", county_fips=fips, day=day,
            county_name=names[fips],
            ballots_total=_add(tally.inperson, tally.mail_returned),
            mail_returned=tally.mail_returned,
            inperson=tally.inperson,
            # Virginia has no party registration. Never 0.
            party_dem=None, party_rep=None, party_oth=None, party_npa=None,
        )
        for fips, tally in sorted(tallies.items())
    ]

    state_rows: list[StateDay] = []
    district_rows: list[DistrictDay] = []
    if len(county_rows) == EXPECTED_LOCALITIES:
        # District rows are gated with the statewide row, for the same reason:
        # a district summed from a partial export is a number that looks like
        # the district and is not.
        district_rows = [
            DistrictDay(
                cycle=cycle, state="VA", cd_code=cd, day=day,
                ballots_total=_add(tally.inperson, tally.mail_returned),
                mail_returned=tally.mail_returned,
                inperson=tally.inperson,
                party_dem=None, party_rep=None, party_oth=None, party_npa=None,
            )
            for cd, tally in sorted(by_district.items())
        ]
        # ELECT publishes no statewide row, so the state total can only be our
        # sum -- which is a Virginia total ONLY when all 133 localities are in
        # the file. A partial export sums to a number that looks like Virginia
        # and is not, so it is published as counties and nothing else.
        state_rows.append(StateDay(
            cycle=cycle, state="VA", day=day,
            ballots_total=_add(*(r.ballots_total for r in county_rows)),
            mail_requested=_add(*(t.mail_sent for t in tallies.values())),
            mail_returned=_add(*(r.mail_returned for r in county_rows)),
            inperson=_add(*(r.inperson for r in county_rows)),
            party_dem=None, party_rep=None, party_oth=None, party_npa=None,
        ))
    else:
        log.warning(
            "VA: export covers %d of %d localities; publishing counties only",
            len(county_rows), EXPECTED_LOCALITIES,
        )

    result = FetchResult(state_rows=state_rows, county_rows=county_rows)
    _districts.attach(result, district_rows)
    return result


def published_date(election: dict) -> date | None:
    """The instant ELECT last refreshed this election's reports, as a UTC date."""
    for field in ("asOf", "lastUpdated"):
        raw = election.get(field)
        if not raw:
            continue
        try:
            stamp = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            continue
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return stamp.astimezone(timezone.utc).date()
    return None


def absentee_blob(election: dict) -> str | None:
    """The absentee export's blob name, or None if this election has no report."""
    for category in election.get("publicReportCategories") or []:
        for report in category.get("reports") or []:
            if _key(report.get("reportName")) == ABSENTEE_REPORT:
                blob = report.get("blobName")
                if blob:
                    return str(blob)
    return None


# --------------------------------------------------------------------------
# THE ARCHIVED 2022 CURVE -- see `VAScraper.fetch_history` for what is in it.
#
# ELECT overwrites its absentee export in place, so the daily curve of a past
# cycle exists only where somebody else kept dated copies. For 2022 that is the
# Internet Archive's crawls of the retired results site; for 2024 nobody did
# (see `fetch_history`). This runs in `backfill`, never in the daily job.
# --------------------------------------------------------------------------
CDX_URL = "http://web.archive.org/cdx/search/cdx"

#: `id_` asks for the ORIGINAL bytes rather than a rewritten page. The payload
#: is a CSV, which the rewriter would leave alone, but the headers are what
#: matter here and only the raw replay carries the `x-archive-orig-*` set.
WAYBACK_SNAPSHOT = "https://web.archive.org/web/{stamp}id_/{url}"

#: The Wayback Machine replays the ORIGINAL response's `Last-Modified` under
#: this name, so an archived capture is dated by the same rule as a live file:
#: by when ELECT wrote it, never by when the crawler happened to pass.
ARCHIVE_STAMP_HEADER = "x-archive-orig-last-modified"

#: The capture sweep's window around Election Day. Virginia's early-voting
#: period is 45 days; 60 leaves room for a crawl of the empty pre-season file,
#: which parses to a partial export and is skipped on its own merits.
ARCHIVE_WINDOW_BEFORE = 60

#: Captures AFTER Election Day are swept too, because the file is still being
#: rewritten for weeks and a capture's crawl time says nothing about when it
#: was written. They are dated and then cut by `fetch_history`, not here.
ARCHIVE_WINDOW_AFTER = 45

#: Five captures of the 2022 file exist; 200 is room for a cycle the Archive
#: crawled harder without ever running away.
MAX_ARCHIVE_PROBES = 200

#: web.archive.org is slower and less tolerant than a state CDN. Same reasoning
#: as ca.py's constant of the same name.
ARCHIVE_MIN_INTERVAL = 1.0

#: How many times a 429 from the replay host is waited out before the capture
#: is given up on. Bounded so one unreachable capture cannot stall a backfill.
ARCHIVE_RETRIES = 3

#: The longest a `Retry-After` is honoured for, and the wait when it is absent
#: or unreadable. The Archive's own value has been 5-60s.
ARCHIVE_RETRY_CAP = 60.0

#: Seam for tests; nothing else should sleep in an adapter.
_sleep = time.sleep


def _retry_after(raw: str | None) -> float:
    """A `Retry-After` in seconds, capped; the cap itself if unreadable."""
    try:
        seconds = float(str(raw or "").strip())
    except ValueError:
        return ARCHIVE_RETRY_CAP
    return min(max(seconds, 1.0), ARCHIVE_RETRY_CAP)


def last_modified(raw: str | None) -> date | None:
    """An HTTP `Last-Modified` header as a UTC date, or None if unusable."""
    if not raw:
        return None
    try:
        return datetime.strptime(raw.strip(), "%a, %d %b %Y %H:%M:%S %Z").replace(
            tzinfo=timezone.utc
        ).date()
    except ValueError:
        return None


def _archive_captures(cycle: int) -> list[tuple[str, str]]:
    """(timestamp, original URL) for every DISTINCT capture of the cycle's
    legacy export, oldest first.

    Swept by PREFIX rather than exact URL so that a capture keyed under a
    query-string variant of the same file is not missed, and deduplicated on
    the payload digest HERE rather than with the CDX API's `collapse=digest`,
    which only folds ADJACENT rows -- a prefix sweep is ordered by urlkey, so
    the same bytes under two spellings are not adjacent.
    """
    folder = LEGACY_ELECTIONS[cycle]
    anchor = election_date(cycle)
    query = {
        "url": LEGACY_URL.format(election=urllib.parse.quote(folder)),
        "matchType": "prefix", "output": "json",
        "fl": "timestamp,original,digest", "filter": "statuscode:200",
        "from": (anchor - timedelta(days=ARCHIVE_WINDOW_BEFORE)).strftime("%Y%m%d"),
        "to": (anchor + timedelta(days=ARCHIVE_WINDOW_AFTER)).strftime("%Y%m%d"),
        "limit": str(MAX_ARCHIVE_PROBES),
    }
    _throttle(CDX_URL, ARCHIVE_MIN_INTERVAL)
    try:
        response = SESSION.get(CDX_URL, params=query, headers=DEFAULT_HEADERS,
                               timeout=DEFAULT_TIMEOUT)
    except Exception as exc:  # noqa: BLE001 - requests has its own hierarchy
        raise SourceError(f"VA: the Wayback CDX index was unreachable: {exc}") from exc
    if response.status_code != 200:
        raise SourceError(
            f"VA: the Wayback CDX index answered HTTP {response.status_code}"
        )
    text = response.text.strip()
    if not text:
        # The CDX API answers "nothing archived" with an empty body.
        return []
    try:
        rows = json.loads(text)
    except ValueError as exc:
        raise SourceError(f"VA: the Wayback CDX index was not JSON: {exc}") from exc

    seen: dict[str, tuple[str, str]] = {}
    for row in sorted(rows[1:], key=lambda r: r[0]):
        stamp, original, digest = row[0], row[1], row[2]
        seen.setdefault(digest, (stamp, original))
    return sorted(seen.values())


def _archive_get(stamp: str, original: str) -> tuple[bytes, date | None]:
    """One archived capture's bytes and the date ELECT wrote them.

    `_net.get` is not used because it discards response headers, and
    `x-archive-orig-last-modified` is the only honest as-of date a capture
    has. The bytes are cached as any archived file is -- they can never change
    -- with the stamp beside them; a capture that arrived WITHOUT a stamp is
    not cached at all, so a transient header hiccup is retried next run
    rather than frozen as "undatable".
    """
    path = cache_path("VA", f"archive-{stamp}.csv")
    sidecar = path.with_suffix(path.suffix + ".last-modified")
    if path.exists() and sidecar.exists() and path.stat().st_size >= 1024:
        return path.read_bytes(), last_modified(sidecar.read_text())

    url = WAYBACK_SNAPSHOT.format(stamp=stamp, url=original)
    for attempt in range(ARCHIVE_RETRIES + 1):
        _throttle(url, ARCHIVE_MIN_INTERVAL)
        try:
            # No spoofed browser UA on the Archive: it flags a Chrome User-Agent
            # on a python-requests handshake as a bot and 429s it indefinitely
            # (see oh.py ARCHIVE_RETRY_WAITS, measured 2026-10-13).
            response = SESSION.get(url, headers=impersonated_headers(DEFAULT_HEADERS),
                                   timeout=DEFAULT_TIMEOUT)
        except Exception as exc:  # noqa: BLE001
            raise SourceError(f"VA: GET {url} failed: {exc}") from exc
        if response.status_code != 429 or attempt == ARCHIVE_RETRIES:
            break
        # The Archive rate-limits replays across everything this machine is
        # walking, and it says how long to wait. A capture given up on here is
        # a missing DAY, so one bounded wait is worth more than it costs.
        wait = _retry_after(response.headers.get("Retry-After"))
        log.info("VA: %s answered 429, waiting %ss (%d/%d)",
                 url, wait, attempt + 1, ARCHIVE_RETRIES)
        _sleep(wait)
    if response.status_code in (404, 410):
        raise Missing(f"VA: {url} returned {response.status_code}")
    if response.status_code != 200:
        raise SourceError(f"VA: {url} returned HTTP {response.status_code}")
    body = response.content
    if looks_like_html(body) or len(body) < 1024:
        raise SourceError(f"VA: {url} is not the absentee export ({len(body)} bytes)")
    raw_stamp = response.headers.get(ARCHIVE_STAMP_HEADER)
    written = last_modified(raw_stamp)
    if written is not None:
        path.write_bytes(body)
        sidecar.write_text(raw_stamp)
    return body, written


def archived_days(cycle: int) -> dict[date, bytes]:
    """The archived export's bytes for each day it was captured BEFORE Election
    Day -- the dated captures that make up the cycle's curve.

    - A capture is dated by `x-archive-orig-last-modified`, ELECT's own write
      time. One without it is SKIPPED: a crawl time is only an upper bound and
      would put a row on the wrong day.
    - Only days strictly BEFORE Election Day are kept. ELECT keeps rewriting the
      file for weeks after the election as late mail and provisional ballots
      are logged; those fills are not the curve, and the Election-Day row is
      already published from the live final -- an archived capture must never
      replace it.
    - Captures are walked oldest first, so the LAST reading of a day wins.
    """
    anchor = election_date(cycle)
    by_day: dict[date, bytes] = {}
    for stamp, original in _archive_captures(cycle):
        try:
            body, written = _archive_get(stamp, original)
        except SourceError as exc:
            log.debug("VA: archived capture %s unusable (%s)", stamp, exc)
            continue
        if written is None:
            log.warning("VA: capture %s kept no %s; a crawl time is not an "
                        "as-of date, so it is skipped", stamp, ARCHIVE_STAMP_HEADER)
            continue
        if written >= anchor:
            log.debug("VA: capture %s is stamped %s, on or after Election Day %s; "
                      "the final already covers it", stamp, written, anchor)
            continue
        if written < anchor - timedelta(days=ARCHIVE_WINDOW_BEFORE):
            log.debug("VA: capture %s is stamped %s, before %s's window",
                      stamp, written, cycle)
            continue
        by_day[written] = body
    return by_day


class VAScraper(Adapter):
    """Tier 1 for Virginia: ELECT's absentee export, by locality."""

    state = "VA"
    name = "va-elect"
    tier = TIER_SCRAPER

    # -- discovery ---------------------------------------------------------
    def _json(self, url: str, *, filename: str, use_cache: bool) -> dict:
        try:
            body = get(url, state="VA", filename=filename, use_cache=use_cache)
        except Missing as exc:
            raise NotYetPublished(f"VA: {exc}") from exc
        try:
            return json.loads(body.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SourceError(f"VA: {url} did not return JSON: {exc}") from exc

    def _election_key(self, cycle: int, *, use_cache: bool) -> tuple[str, str]:
        """(jurisdiction id, public election id) for the cycle's November general."""
        jurisdiction = self._json(
            JURISDICTION_URL, filename=f"{cycle}_jurisdiction.json", use_cache=use_cache
        )
        jurisdiction_id = jurisdiction.get("id")
        if not jurisdiction_id:
            raise SchemaDrift("VA: jurisdiction feed carries no id")

        wanted = election_date(cycle).isoformat()
        matches = [
            e for e in jurisdiction.get("elections") or []
            if str(e.get("electionDate") or "") == wanted and e.get("publicElectionId")
        ]
        if not matches:
            raise NotYetPublished(
                f"VA: ELECT has not stood up the {wanted} general yet"
            )
        if len(matches) > 1:
            raise SchemaDrift(
                f"VA: {wanted} matches {len(matches)} elections "
                f"{[m.get('publicElectionId') for m in matches]}"
            )
        return str(jurisdiction_id), str(matches[0]["publicElectionId"])

    # -- loading -----------------------------------------------------------
    def _load_current(self, cycle: int, *, use_cache: bool) -> tuple[bytes, date | None]:
        jurisdiction_id, key = self._election_key(cycle, use_cache=use_cache)
        election = self._json(
            ELECTION_URL.format(election=urllib.parse.quote(key)),
            filename=f"{cycle}_election.json", use_cache=use_cache,
        )
        blob = absentee_blob(election)
        if blob is None:
            # The election exists but early voting has not produced a report.
            raise NotYetPublished(f"VA: {key} has no absentee export yet")
        try:
            body = get(
                BLOB_URL.format(jurisdiction=jurisdiction_id,
                                blob=urllib.parse.quote(blob)),
                state="VA", filename=f"{cycle}_{blob}",
                use_cache=use_cache, min_bytes=1024,
            )
        except Missing as exc:
            raise NotYetPublished(f"VA: absentee export not posted yet ({exc})") from exc
        if looks_like_html(body):
            raise NotYetPublished("VA: absentee export came back as HTML, not CSV")
        return body, published_date(election)

    def _load_legacy(self, cycle: int) -> bytes:
        folder = LEGACY_ELECTIONS[cycle]
        try:
            body = get(
                LEGACY_URL.format(election=urllib.parse.quote(folder)),
                state="VA", filename=f"{cycle}_legacy_absentee.csv",
                use_cache=True, min_bytes=1024,
            )
        except Missing as exc:
            raise NotYetPublished(f"VA: no archived {folder} absentee file ({exc})") from exc
        if looks_like_html(body):
            # The retired system answers a missing file with a styled 200 page.
            raise NotYetPublished(f"VA: archived {folder} absentee file is gone")
        return body

    # -- the contract ------------------------------------------------------
    def fetch(self, cycle: int, as_of: date) -> FetchResult:
        body, published = self._load_current(cycle, use_cache=False)
        day = as_of if published is None or published > as_of else published
        return parse(body, cycle, day)

    def fetch_history(self, cycle: int) -> FetchResult:
        """A past cycle: the certified final on Election Day, plus -- for 2022
        -- the daily curve rebuilt out of the Internet Archive.

        ELECT overwrites the export in place rather than keeping a file per
        day, so the state itself holds no daily series. What it holds is the
        cycle's final absentee position, which the 2022/2024 comparison lines
        anchor on; it is dated to Election Day rather than to whenever ELECT
        last touched the file (months later, after certification).

        **2022** also has a curve, because the Internet Archive crawled the
        retired results site's `Absentee.csv` during the season. Each capture
        replays ELECT's own `Last-Modified` as `x-archive-orig-last-modified`,
        so an archived day is dated by the rule the live file is dated by.
        VERIFIED 2026-10-13, captures of LEGACY_URL (CDX, 200s only):

            capture          x-archive-orig-last-modified   voted
            20221102204427   2022-11-02 16:11Z              698,812
            20221107224812   2022-11-07                     943,162
            20221115125251   2022-11-15                     988,506   (cut)
            20221130002252   2022-11-30                               (cut)
            live final       2022-12-06                     992,231   (Election Day)

        Only captures written strictly BEFORE Election Day are emitted; the
        post-election rewrites are late fills, not the curve, and the
        Election-Day row is the live final, which no capture may replace.
        See `archived_days` for the rules. The archived days parse through
        the same `parse` as the live route -- locality, statewide and district
        rows alike.

        **2024 has no curve and this does not pretend otherwise.** ELECT's
        ENR system came up for that cycle and the Archive's first capture of
        its absentee blob is from election night; no pre-election copy of any
        state file exists. The 2024 daily series lives in `data/manual/
        2024_statewide.csv` (VPAP's by-ballot-date tracker, vendored once),
        at the manual tier, and this returns the final alone.
        """
        if cycle >= date.today().year:
            raise NotYetPublished(f"VA: {cycle} is not an archived cycle")
        day = election_date(cycle)
        if cycle not in LEGACY_ELECTIONS:
            body, _ = self._load_current(cycle, use_cache=True)
            return parse(body, cycle, day)

        result = parse(self._load_legacy(cycle), cycle, day)
        try:
            by_day = archived_days(cycle)
        except SourceError as exc:
            # The final is the load-bearing row; a curve the Archive cannot
            # serve today is a gap, not a reason to publish nothing.
            log.warning("VA: the %s archive walk failed, final only: %s", cycle, exc)
            return result
        days = 0
        for archived in sorted(by_day):
            try:
                one = parse(by_day[archived], cycle, archived)
            except SourceError as exc:
                # A capture of something other than the export -- a styled
                # error page, a partial crawl, a drifted header. Skipped,
                # never guessed at.
                log.info("VA: archived %s skipped (%s: %s)",
                         archived.isoformat(), type(exc).__name__, exc)
                continue
            # District rows ride as an attribute `FetchResult.extend` cannot
            # see, so they are folded in through the seam made for it.
            result.extend(one)
            _districts.extend(result, _districts.rows_of(one))
            days += 1
        log.info("VA: %d archived days for %s before Election Day", days, cycle)
        return result
