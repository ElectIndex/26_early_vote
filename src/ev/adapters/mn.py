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
"""

from __future__ import annotations

import html
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
        try:
            return download(PAGE, filename="absentee-data.html", use_cache=use_cache)
        except Missing as exc:
            raise NotYetPublished(f"MN: {exc}") from exc

    def fetch(self, cycle: int, as_of: date) -> FetchResult:
        return parse(self._load(use_cache=False), cycle, as_of)

    def fetch_history(self, cycle: int) -> FetchResult:
        """Minnesota overwrites this page every cycle, so there is no archive.

        The 2022 and 2024 generals are only in the Wayback Machine (verified:
        `web.archive.org/web/20241003181752id_/...` and `...20221014040640id_/...`
        both 200), which is a third-party host and not what this tier is for.
        The fixtures in tests/fixtures/mn are taken from those two captures, so
        the parser is exercised against both generals even though the adapter
        cannot fetch them live.
        """
        raise NotYetPublished(
            f"MN: the SoS overwrites its absentee data page every cycle, so "
            f"there is no {cycle} file to backfill from"
        )
