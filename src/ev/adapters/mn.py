"""Minnesota: the Secretary of State's "Absentee Data" page.

Minnesota has the other OPEN US SENATE SEAT of 2026, and it publishes a daily
county table of absentee applications and accepted ballots -- 87 counties, all
87 of them, every day of the absentee period. The earlier coverage survey
recorded it as a loss because `www.sos.mn.gov` 302s every python-requests call
to a Radware bot manager at `validate.perfdrive.com` and answers with an
interstitial rather than the page. That is a TLS fingerprint rule, not a
publishing decision: the same URL, same machine, same second, returns the real
61 KB page to a client presenting Chrome's JA3/HTTP2 fingerprint. So this module
carries its own `curl_cffi` transport (see `download` below) rather than going
through `_net.get`, whose 403 retry cannot help here -- Radware answers **302 to
a 200**, not 403, so there is no error status to retry on.

The page's own shape drives everything else.

* **It carries ONE election at a time and the heading is the only label.** Today
  it reads "State Primary Absentee Counts" and holds the August primary's
  445,023 applications; in 2022 and 2024 it read "State General Election
  Absentee Counts". A section that does not name a general is never eligible,
  and because the heading carries NO year, the cycle is proved from the report
  DATE instead: a general section dated outside the cycle's own window is
  refused rather than published as this cycle's.

* **The county table has no header row -- only a caption.** In the live 2026
  page and in the 2024-10-03 capture the 87 rows start straight at Aitkin; the
  2022 capture and the 2024-11-09 one do carry a `County | Applications
  Submitted | Accepted Ballots` header. So the header cannot be relied on to say
  what the columns MEAN, and guessing would be exactly the rule-3 mistake.
  Instead the columns are identified by PROOF: the "Statewide Counts" bullets
  above the table give one figure per column in the same order, and each
  column's sum across the 87 counties must equal its bullet exactly. Verified on
  all three fixtures -- e.g. the live 2026 page sums to 445,023 / 116,414 /
  133,364 against bullets of exactly those values. A table whose columns do not
  reproduce Minnesota's own statewide figures is drift, not data.

* **Two layouts, both real.** The 2022 and 2024 generals published two columns
  (applications submitted, accepted ballots) with NO method split; the 2026
  primary publishes three (applications, accepted by mail, accepted in person).
  Both parse. Under the two-column layout `mail_returned` and `inperson` come
  out None -- not reported, never 0.

* **`mail_requested` is Minnesota's "applications", which is broader than
  mail.** An in-person absentee voter in Minnesota also submits an absentee
  application, so this column counts every absentee ballot requested by any
  route, exactly as Iowa's "Requested" column does in `ia.py`. It is the
  closest thing Minnesota publishes to ballots issued, and it is the number its
  own page leads with.

Minnesota does NOT register voters by party -- it is an open-primary state with
no party enrolment -- so all four party fields are None everywhere. See THE
BLANK RULE in schema.py.

## The past cycles: county rows for 2022 and 2024, out of the Internet Archive

The SoS OVERWRITES this one URL every election -- there is no dated file, no
`/2024/` path, nothing on the live host but today's election. So a past cycle's
county table exists only in the Wayback Machine, and `fetch_history` reads it
there. That is not a new policy: `fl.py` rebuilds Florida's county curves out of
the CDX index, `de.py` walks it for Delaware, `or.py` and `wa.py` pin Wayback
timestamps by hand. The `id_` suffix returns the SoS's ORIGINAL bytes, and every
capture goes through the SAME `parse` the live path uses -- the column PROOF
against Minnesota's own statewide bullets included -- so an archived reading is
held to exactly the standard a live one is.

**What is actually there, measured 2026-09-08 via the CDX API** (exact-URL query,
`collapse=digest`, `filter=statuscode:200`):

* **2024 -- three distinct captures, two of them usable.** `20240919132653` is
  the August primary's page and is skipped by `find_general` (NotYetPublished,
  which here means "this capture is not the election we asked for").
  `20241003181752` carries `as of October 3, 2024` -- 522,784 applications and
  107,421 accepted -- and `20241109152601` carries the ELECTION-DAY final, `as of
  November 5, 2024`, 1,420,287 / 1,271,636. Both give all 87 counties, and the
  November one is a genuine days_to_election 0 reading.
* **2022 -- exactly one capture,** `20221014040640`, `as of October 13, 2022`,
  400,975 / 99,252, all 87 counties. It is the whole 2022 archive there is; the
  crawler never came back before Election Day.

So Minnesota's archived curve is two days in 2024 and one in 2022 rather than a
dense series. That is the Archive's density, not a parse limit, and it is the
difference between a county series and none at all.
"""

from __future__ import annotations

import html
import json
import logging
import re
from datetime import date, datetime, timedelta

from ..calendar import election_date
from ..schema import TIER_SCRAPER, CountyDay, StateDay
from . import _fips
from ._net import DEFAULT_HEADERS, Missing, cache_path, get
from .base import Adapter, FetchResult, NotYetPublished, SchemaDrift, SourceError

log = logging.getLogger(__name__)

#: VERIFIED live 2026-09-06 through a browser fingerprint: 200, 65,748 bytes,
#: carrying the August primary's section. Through plain `requests` the same URL
#: is a 302 to validate.perfdrive.com answering 200 with 15,038 bytes of
#: interstitial -- which is why this page was written off in the first survey.
PAGE = ("https://www.sos.mn.gov/election-administration-campaigns/data-maps/"
        "absentee-data/")

#: Radware's bot manager. Its interstitial answers **200**, from a redirected
#: URL, so neither the status code nor `_net.get`'s 403 retry can see it; these
#: markers can. Seeing one means "we could not look", which is SourceError --
#: never NotYetPublished, which would claim Minnesota has nothing to show.
#:
#: Note what is deliberately NOT in this list, because both were tried and both
#: matched the GENUINE page: "validate.perfdrive.com" (Radware writes
#: `ssConf("cu", "validate.perfdrive.com, ssc")` into the real page at byte
#: 3,691) and "SSJSConnectorObj" (its bootstrap script, injected into every page
#: the SoS serves, at byte 2,933). Matching on either rejects every good fetch.
#: These two strings appear on the challenge page and nowhere else.
BOT_MARKERS = (
    b"Radware Captcha Page",
    b"captcha.perfdrive.com",
)

#: The heading above the section we want. It names the election but NOT the
#: year, so `report_day` supplies the cycle proof.
GENERAL_WORDS = ("general", "absentee", "counts")
OTHER_ELECTIONS = ("primary", "special", "run-off", "runoff", "municipal")

#: How far either side of Election Day a general-election report may be dated.
#: Minnesota's absentee period opens 46 days before Election Day; the tail allows
#: for the post-election restatements the page keeps for a few weeks. A section
#: dated outside this cannot be this cycle's general, whatever its heading says.
WINDOW_BEFORE = 120
WINDOW_AFTER = 30

#: How many times to ask before concluding the bot manager will not let us
#: through. Radware challenges the first request from a new session and then
#: cookies it, so the second almost always succeeds; three leaves headroom
#: without hammering a state election site.
CHALLENGE_ATTEMPTS = 3

#: Statewide bullet label -> the StateDay field it fills. Minnesota's own words,
#: normalised to lower case and single-spaced. An unrecognised bullet raises
#: SchemaDrift: a new column we cannot name is a column we must not publish.
BULLET_FIELDS = {
    "applications": "mail_requested",
    "applications submitted": "mail_requested",
    "accepted ballots": "ballots_total",
    "accepted ballots - by mail": "mail_returned",
    "accepted ballots by mail": "mail_returned",
    "accepted ballots - in person": "inperson",
    "accepted ballots in person": "inperson",
}

EXPECTED_COUNTIES = len(_fips.CENSUS_COUNTIES["MN"])

# --------------------------------------------------------------------------
# The archived series. See "The past cycles" in the module docstring.
# --------------------------------------------------------------------------
CDX_URL = "http://web.archive.org/cdx/search/cdx"

#: `id_` asks the Wayback Machine for the SoS's ORIGINAL bytes rather than a
#: rewritten page.
WAYBACK_SNAPSHOT = "https://web.archive.org/web/{stamp}id_/" + PAGE

#: web.archive.org is slower and less tolerant than a state host. Same reasoning
#: as fl.py's ARCHIVE_MIN_INTERVAL and DEFAULT_MIN_INTERVAL in _net.
ARCHIVE_MIN_INTERVAL = 1.0

#: Three distinct 2024 captures is the densest cycle the Archive holds for this
#: URL; 60 leaves room for a cycle it crawled harder without ever running away.
MAX_ARCHIVE_PROBES = 60

#: The first cycle this repo publishes. Earlier captures exist but no cycle
#: before this one is tracked.
FIRST_CYCLE = 2022

#: The Wayback timestamp, `YYYYMMDDhhmmss`.
_STAMP = re.compile(r"^(\d{4})(\d{2})(\d{2})\d{6}$")


def stamp_day(stamp: str) -> date:
    """The date a capture was TAKEN, which is that capture's own as-of.

    A capture cannot report a day later than the day it was made, so this is the
    honest `as_of` for an archived read -- exactly what the run date is for a
    live one. It is derived from the CDX index rather than invented, and it
    keeps `parse`'s "dated after the run date" guard live on BOTH paths instead
    of being quietly disabled for history.
    """
    m = _STAMP.match(str(stamp).strip())
    if m is None:
        raise SourceError(f"MN: {stamp!r} is not a Wayback timestamp")
    year, month, day = (int(g) for g in m.groups())
    try:
        return date(year, month, day)
    except ValueError as exc:
        raise SourceError(f"MN: {stamp!r} is not a Wayback timestamp") from exc


def archive_stamps(cycle: int) -> list[str]:
    """Wayback timestamps of every distinct version of the page in the window.

    `collapse=digest` is what turns the crawler's visits into the handful of
    genuinely distinct readings: the Archive calls far more often than the SoS
    updates, and an unchanged page is one day, not five. Sorted here rather than
    trusted from the API, because `fetch_history` resolves two captures of one
    report date by letting the later one win, and that is only true in order.
    """
    anchor = election_date(cycle)
    query = {
        "url": PAGE, "output": "json", "fl": "timestamp,statuscode,digest",
        "filter": "statuscode:200", "collapse": "digest",
        "from": (anchor - timedelta(days=WINDOW_BEFORE)).strftime("%Y%m%d"),
        "to": (anchor + timedelta(days=WINDOW_AFTER)).strftime("%Y%m%d"),
        "limit": str(MAX_ARCHIVE_PROBES),
    }
    try:
        body = get(CDX_URL, state="MN", filename=f"cdx-absentee-{cycle}.json",
                   params=query, min_bytes=2, min_interval=ARCHIVE_MIN_INTERVAL)
    except Missing:
        # The CDX API answers "nothing archived" with an EMPTY body, which
        # _net.get reports as Missing. That is absence, not a fault.
        return []
    try:
        rows = json.loads(body.decode("utf-8", errors="replace"))
    except ValueError as exc:
        raise SourceError(f"MN: the Wayback CDX index was not JSON: {exc}") from exc
    return sorted(str(row[0]) for row in rows[1:])

_H2 = re.compile(r"<h2[^>]*>(.*?)</h2>", re.S | re.I)
_TABLE = re.compile(r"<table[^>]*>.*?</table>", re.S | re.I)
_CAPTION = re.compile(r"<caption[^>]*>(.*?)</caption>", re.S | re.I)
_ROW = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
_CELL = re.compile(r"<t[hd][^>]*>(.*?)</t[hd]>", re.S | re.I)
_LI = re.compile(r"<li[^>]*>(.*?)</li>", re.S | re.I)
_TAG = re.compile(r"<[^>]+>")
_COMMENT = re.compile(r"<!--.*?-->", re.S)

#: "Applications submitted (10/3/24): 522,784" and
#: "Accepted ballots - by mail (8/11/26 at 3 p.m.): 116,414".
_BULLET = re.compile(r"^(?P<label>[^(]+?)\s*\((?P<when>[^)]*)\)\s*:\s*(?P<value>[\d,]+)$")
_BULLET_DATE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{2,4})")

#: "Absentee Counts by County as of October 3, 2024" /
#: "... as of August 11, 2026 at 3 p.m."
_CAPTION_DATE = re.compile(r"as of\s+([A-Z][a-z]+)\s+(\d{1,2}),\s*(\d{4})", re.I)


# --------------------------------------------------------------------------
# Transport
# --------------------------------------------------------------------------
def _browser_session():
    """A curl_cffi session presenting a real Chrome TLS/HTTP2 fingerprint.

    `None` when curl_cffi is missing. It IS declared in pyproject.toml, but the
    import stays guarded so a stock checkout without it degrades to a
    SourceError -- "we could not look" -- rather than crashing the run.
    """
    try:
        from curl_cffi import requests as curl_requests  # noqa: PLC0415
    except ImportError:
        return None
    return curl_requests.Session(impersonate="chrome")


def download(url: str, *, filename: str, use_cache: bool = False,
             min_bytes: int = 4096) -> bytes:
    """GET `url` with a browser fingerprint and mirror it to cache/.

    Raises the same `Missing` / `SourceError` vocabulary `_net.get` does, so the
    caller cannot tell which transport answered. `_net.get` is used only as the
    fallback when curl_cffi is absent -- it will very likely come back with the
    bot interstitial, which `parse` rejects as SourceError.
    """
    session = _browser_session()
    if session is None:
        log.debug("MN: curl_cffi unavailable; %s will likely be intercepted", url)
        return get(url, state="MN", filename=filename, use_cache=use_cache,
                   min_bytes=min_bytes)

    path = cache_path("MN", filename)
    if use_cache and path.exists() and path.stat().st_size >= min_bytes:
        return path.read_bytes()

    # Radware challenges the FIRST request from a new session and hands back a
    # cookie with the interstitial; the next request on the SAME session gets
    # the real page. Verified 2026-09-06: request A on a fresh session returned
    # the 15,096-byte challenge and request B, identical in every other way,
    # returned the genuine 65,752-byte page. So the retry has to reuse the
    # session -- building a fresh one each time would be challenged forever.
    response = None
    for attempt in range(1, CHALLENGE_ATTEMPTS + 1):
        try:
            response = session.get(url, headers=DEFAULT_HEADERS, timeout=60)
        except Exception as exc:  # noqa: BLE001 - curl_cffi has its own hierarchy
            raise SourceError(f"MN: GET {url} failed: {exc}") from exc
        if response.status_code != 200 or not looks_intercepted(response.content):
            break
        log.debug("MN: %s was challenged (attempt %d); retrying on the same "
                  "session with the cookie it just set", url, attempt)

    if response.status_code in (404, 410):
        raise Missing(f"MN: {url} returned {response.status_code}")
    if response.status_code != 200:
        raise SourceError(f"MN: {url} returned HTTP {response.status_code}")

    body = response.content
    if len(body) < min_bytes:
        raise Missing(f"MN: {url} returned only {len(body)} bytes")
    path.write_bytes(body)
    return body


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------
def _text(fragment: str) -> str:
    cleaned = _TAG.sub(" ", _COMMENT.sub(" ", fragment or ""))
    return " ".join(html.unescape(cleaned).replace("\xa0", " ").split())


def _norm(fragment: str) -> str:
    return _text(fragment).lower()


def _count(raw: str) -> int:
    text = _text(raw).replace(",", "")
    if not re.fullmatch(r"\d+", text):
        raise SchemaDrift(f"MN: {raw!r} is not a ballot count")
    return int(text)


def looks_intercepted(body: bytes) -> bool:
    """True if this is the bot manager's page rather than the SoS's.

    Radware answers 200, so this is the only way to tell. A hit must become
    SourceError: reading it as "Minnesota has posted nothing" would stop the
    ladder on a state we simply failed to reach.
    """
    return any(marker in body for marker in BOT_MARKERS)


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------
def headings(page: str) -> list[tuple[int, str]]:
    """(position, text) for every <h2> on the page."""
    return [(m.start(), _text(m.group(1))) for m in _H2.finditer(page or "")]


def find_general(page: str) -> int:
    """Where the GENERAL-election section starts, or NotYetPublished.

    The page carries one election at a time and the heading is the only thing
    that names it. A heading that says primary -- which is what it says today,
    over the August primary's 445,023 applications -- is never eligible.
    """
    for position, heading in headings(page):
        low = heading.lower()
        if any(word in low for word in OTHER_ELECTIONS):
            continue
        if all(word in low for word in GENERAL_WORDS):
            return position
    raise NotYetPublished(
        f"MN: the absentee data page has no general-election section yet "
        f"(its headings are {[h for _, h in headings(page)]})"
    )


def bullets(page: str, start: int, end: int) -> list[tuple[str, date, int]]:
    """The "Statewide Counts" list: (normalised label, as-of date, value).

    These are the only statement of what the county table's columns mean, and
    the order they appear in is the order of those columns.
    """
    out: list[tuple[str, date, int]] = []
    for m in _LI.finditer(page, start, end):
        item = _text(m.group(1))
        hit = _BULLET.match(item)
        if hit is None:
            continue
        label = " ".join(hit.group("label").lower().split())
        stamp = _BULLET_DATE.match(hit.group("when").strip())
        if stamp is None:
            raise SchemaDrift(f"MN: statewide bullet {item!r} carries no date")
        month, day, year = (int(g) for g in stamp.groups())
        if year < 100:
            year += 2000
        try:
            when = date(year, month, day)
        except ValueError as exc:
            raise SchemaDrift(f"MN: statewide bullet {item!r} has no valid date") from exc
        out.append((label, when, _count(hit.group("value"))))
    if not out:
        raise SchemaDrift("MN: the general-election section has no Statewide Counts list")
    return out


def caption_day(caption: str) -> date:
    """"Absentee Counts by County as of October 3, 2024" -> 2024-10-03."""
    hit = _CAPTION_DATE.search(caption or "")
    if hit is None:
        raise SchemaDrift(f"MN: county table caption {caption!r} carries no date")
    try:
        return datetime.strptime(
            f"{hit.group(1)} {hit.group(2)} {hit.group(3)}", "%B %d %Y"
        ).date()
    except ValueError as exc:
        raise SchemaDrift(f"MN: county table caption {caption!r} has no valid date") from exc


def county_table(page: str, start: int) -> tuple[str, list[list[str]]]:
    """The first table after `start`, as (caption text, rows of cell text)."""
    m = _TABLE.search(page, start)
    if m is None:
        raise SchemaDrift("MN: the general-election heading is followed by no table")
    table = m.group(0)
    caption = _CAPTION.search(table)
    rows = [[_text(c) for c in _CELL.findall(r)] for r in _ROW.findall(table)]
    return (_text(caption.group(1)) if caption else ""), rows


def parse(body: bytes, cycle: int, as_of: date) -> FetchResult:
    """Parse the absentee data page into one statewide row and 87 county rows."""
    if looks_intercepted(body):
        raise SourceError(
            "MN: the absentee data page came back as the Radware bot manager's "
            "interstitial, not the SoS's page -- we could not look"
        )
    page = body.decode("utf-8", errors="replace")
    if "<table" not in page.lower():
        raise SourceError("MN: the absentee data page carries no tables")

    start = find_general(page)
    caption, rows = county_table(page, start)
    table_at = page.find("<table", start)
    stats = bullets(page, start, table_at if table_at > 0 else len(page))

    day = caption_day(caption)
    for label, when, _value in stats:
        if when != day:
            raise SchemaDrift(
                f"MN: the county table is captioned {day.isoformat()} but the "
                f"statewide bullet {label!r} is dated {when.isoformat()}"
            )

    # The heading names no year, so the DATE is the only proof of which election
    # this section is. A general-election section dated outside this cycle's own
    # window is a leftover from a previous cycle, not this cycle's data.
    voting = election_date(cycle)
    if not (voting - timedelta(days=WINDOW_BEFORE) <= day
            <= voting + timedelta(days=WINDOW_AFTER)):
        raise NotYetPublished(
            f"MN: the general-election section is dated {day.isoformat()}, "
            f"outside the {cycle} general's window around {voting.isoformat()}"
        )
    if day > as_of:
        raise SourceError(
            f"MN: the absentee page is dated {day.isoformat()}, after the run "
            f"date {as_of.isoformat()}"
        )

    counties, header = _county_rows(rows)
    _check_header(header, stats)
    fields = _identify_columns(counties, stats)
    return _emit(counties, stats, fields, cycle, day)


def _county_rows(rows: list[list[str]]) -> tuple[list[tuple[str, str, list[int]]], list[str]]:
    """(fips, name, counts) per county, plus the header row if there was one.

    The header row is present in some captures and absent in others, so it is
    detected rather than assumed -- and it is never used to name the columns.
    """
    header: list[str] = []
    out: list[tuple[str, str, list[int]]] = []
    unknown: list[str] = []
    for cells in rows:
        if not cells or not cells[0]:
            continue
        if cells[0].strip().lower() == "county":
            header = [c for c in cells[1:]]
            continue
        hit = _fips.lookup("MN", cells[0])
        if hit is None:
            unknown.append(cells[0])
            continue
        fips, canonical = hit
        out.append((fips, canonical, [_count(c) for c in cells[1:]]))
    if unknown:
        raise SchemaDrift(f"MN: unrecognised county names {sorted(set(unknown))[:5]}")
    if len(out) != EXPECTED_COUNTIES:
        raise SchemaDrift(
            f"MN: the county table lists {len(out)} counties, not the "
            f"{EXPECTED_COUNTIES} Minnesota has"
        )
    widths = {len(counts) for _f, _n, counts in out}
    if len(widths) != 1:
        raise SchemaDrift(f"MN: county rows have differing widths {sorted(widths)}")
    return out, header


def _check_header(header: list[str], stats: list[tuple[str, date, int]]) -> None:
    """When the table DOES carry a header, it must agree with the bullets."""
    if not header:
        return
    labels = [" ".join(h.lower().split()) for h in header]
    expected = [label for label, _when, _value in stats]
    if [l.replace(" - ", " ") for l in labels] != [e.replace(" - ", " ") for e in expected]:
        raise SchemaDrift(
            f"MN: the county table header {labels} does not match the statewide "
            f"bullets {expected}"
        )


def _identify_columns(counties, stats) -> list[str]:
    """Prove which StateDay field each county column is, by summing it.

    This is the whole safety argument for a table with no dependable header: the
    bullets above it publish one statewide figure per column, in order, and a
    column that does not sum to its bullet is not the column we think it is.
    """
    width = len(counties[0][2])
    if width != len(stats):
        raise SchemaDrift(
            f"MN: the county table has {width} count columns but the statewide "
            f"list has {len(stats)} figures {[s[0] for s in stats]}"
        )
    fields: list[str] = []
    for index, (label, _when, value) in enumerate(stats):
        field = BULLET_FIELDS.get(label)
        if field is None:
            raise SchemaDrift(f"MN: unrecognised statewide figure {label!r}")
        total = sum(counts[index] for _f, _n, counts in counties)
        if total != value:
            raise SchemaDrift(
                f"MN: county column {index} sums to {total:,} but Minnesota's "
                f"own {label!r} figure is {value:,} -- the columns cannot be "
                f"identified, so nothing is published"
            )
        fields.append(field)
    if len(set(fields)) != len(fields):
        raise SchemaDrift(f"MN: statewide figures map to duplicate fields {fields}")
    return fields


def _emit(counties, stats, fields, cycle: int, day: date) -> FetchResult:
    values = {field: value for field, (_l, _w, value) in zip(fields, stats)}

    # With a by-mail and an in-person figure but no combined one -- the 2026
    # layout -- Minnesota's own total is the sum of the two.
    total = values.get("ballots_total")
    if total is None and "mail_returned" in values and "inperson" in values:
        total = values["mail_returned"] + values["inperson"]

    result = FetchResult()
    result.state_rows.append(StateDay(
        cycle=cycle, state="MN", day=day,
        ballots_total=total,
        # The page is a snapshot with no daily column, so the day's new ballots
        # are not reported. Blank, never 0.
        ballots_new=None,
        mail_requested=values.get("mail_requested"),
        mail_returned=values.get("mail_returned"),
        inperson=values.get("inperson"),
        # Minnesota has no party registration. Never 0 -- see THE BLANK RULE.
        party_dem=None, party_rep=None, party_oth=None, party_npa=None,
    ))

    for fips, name, counts in counties:
        row = {field: count for field, count in zip(fields, counts)}
        county_total = row.get("ballots_total")
        if county_total is None and "mail_returned" in row and "inperson" in row:
            county_total = row["mail_returned"] + row["inperson"]
        result.county_rows.append(CountyDay(
            cycle=cycle, state="MN", county_fips=fips, day=day, county_name=name,
            ballots_total=county_total,
            ballots_new=None,
            # `CountyDay` has no applications column, so Minnesota's per-county
            # applications figure has nowhere to go; it is not lost data, it is
            # a column this schema does not carry.
            mail_returned=row.get("mail_returned"),
            inperson=row.get("inperson"),
            party_dem=None, party_rep=None, party_oth=None, party_npa=None,
        ))
    return result


class MNScraper(Adapter):
    """Tier 1 for Minnesota: the SoS's Absentee Data page."""

    state = "MN"
    name = "mn-sos"
    tier = TIER_SCRAPER

    def _load(self, *, use_cache: bool) -> bytes:
        """The live page.

        ⚠️ A 404 HERE IS NOT "NOT YET PUBLISHED", AND READING IT THAT WAY WOULD
        BLANK MINNESOTA SILENTLY. This URL is a PERMANENT landing page, not a
        dated file: it answers 200 with ~65 KB every day of the year, carrying
        whichever election is current -- the August primary today, the November
        general from October. There is no state of the world in which Minnesota
        "has not posted it yet" and the page 404s.

        So a `Missing` from here means the CMS moved the page, not that voting
        has not started. The difference is the whole ladder: NotYetPublished
        STOPS it, publishes nothing, and badges the state `pending` -- the same
        badge a state that simply has not opened early voting gets -- so a
        re-path in late October would look exactly like "Minnesota has not
        started", with no fall-through to civicAPI and nothing in the failure
        count for ingest.yml to shout about. SourceError falls through to a
        weaker tier and shows up as a refusal, which is what "we could not look"
        means everywhere else in this repo (see `looks_intercepted`, which makes
        the same distinction about Radware answering 200).

        `download` already raises SourceError for every other failure; this only
        stops the 404/short-body case from being downgraded.
        """
        return download(PAGE, filename="absentee-data.html", use_cache=use_cache)

    def fetch(self, cycle: int, as_of: date) -> FetchResult:
        return parse(self._load(use_cache=False), cycle, as_of)

    def fetch_history(self, cycle: int) -> FetchResult:
        """A past cycle's county table, rebuilt from the Internet Archive.

        Minnesota overwrites this one URL every election, so nothing on the live
        host answers for a past cycle -- the archive is the only route, and the
        module docstring records exactly which captures exist and what each one
        says. An earlier version of this method refused them on the ground that
        web.archive.org "is a third-party host and not what this tier is for";
        that is a policy this repo does not actually hold (see `wa.py`, whose
        docstring makes the same correction, and `fl.py`, `de.py`, `or.py`).

        ⚠️ GUARD PARITY WITH `fetch`. Every capture goes through the SAME
        `parse`, so the header check, the column PROOF against Minnesota's own
        statewide bullets, the 87-county count, the caption/bullet date
        agreement and the cycle window all run here exactly as they do live. The
        only difference is the `as_of`, and it is the CAPTURE'S OWN DATE -- so
        the "dated after the run date" guard stays live on this path too rather
        than being disabled by passing Election Day.

        A capture showing a different election raises NotYetPublished out of
        `find_general`, which HERE means "this capture is not the election we
        asked for" and is skipped -- never "stop the ladder". Drift is not
        swallowed: if every capture drifted, the drift is what is raised, so a
        real vocabulary change can never be reported as "nothing archived".
        """
        if cycle < FIRST_CYCLE:
            raise NotYetPublished(f"MN: {cycle} is before the first tracked cycle")
        if cycle >= date.today().year:
            raise NotYetPublished(f"MN: {cycle} is not an archived cycle")

        by_day: dict[date, tuple[StateDay, list[CountyDay]]] = {}
        drift: SchemaDrift | None = None
        seen = 0
        for stamp in archive_stamps(cycle):
            snapshot = WAYBACK_SNAPSHOT.format(stamp=stamp)
            try:
                # The stamp has to be in the cache filename: every capture is the
                # SAME URL, so a bare basename would make them one file.
                body = get(snapshot, state="MN",
                           filename=f"absentee-data-{stamp}.html",
                           use_cache=True, min_bytes=4096,
                           min_interval=ARCHIVE_MIN_INTERVAL)
            except SourceError as exc:
                log.debug("MN: archived capture %s unusable (%s)", stamp, exc)
                continue
            seen += 1
            try:
                captured = parse(body, cycle, stamp_day(stamp))
            except NotYetPublished as exc:
                log.debug("MN: capture %s skipped (%s)", stamp, exc)
                continue
            except SchemaDrift as exc:
                # ONE bad capture is a bad capture, not a changed source. Held
                # so that "every capture drifted" can still be reported as
                # drift rather than as absence.
                log.warning("MN: capture %s did not parse (%s)", stamp, exc)
                drift = exc
                continue
            except SourceError as exc:
                log.debug("MN: capture %s unreadable (%s)", stamp, exc)
                continue
            row = captured.state_rows[0]
            # A later capture of the same report date is the truer reading.
            by_day[row.day] = (row, captured.county_rows)

        if not by_day:
            if drift is not None:
                raise drift
            raise NotYetPublished(
                f"MN: nothing archived for the {cycle} general "
                f"({seen} captures read)"
            )

        result = FetchResult()
        for day in sorted(by_day):
            state_row, county_rows = by_day[day]
            result.state_rows.append(state_row)
            result.county_rows.extend(county_rows)
        log.info("MN: %d archived days (%d county rows) from %d captures",
                 len(result.state_rows), len(result.county_rows), seen)
        return result
