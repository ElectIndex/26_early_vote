"""New York: the New York CITY Board of Elections' early-voting check-ins.

**This is a PARTIAL source and is published as one.** It covers five of New
York's sixty-two counties, so it emits county rows and **never a `StateDay`** --
exactly the rule `tx.py` established for Texas: a number that looks like a New
York total and is not one is worse than a hole, because it looks right.

Why there is nothing better. `elections.ny.gov` was recorded in the coverage
survey as unreachable, and with a browser TLS fingerprint it now answers 200
(verified 2026-09-06: 403 to plain `requests`, 200 and 56,142 bytes to
`curl_cffi`). Re-reading it with that access changes nothing about the data:
its 4,218-URL sitemap carries enrolment statistics, absentee-deadline press
releases and election law, and no early-voting or mail-ballot turnout report of
any kind. The state genuinely publishes none. What exists during the season is
the city Board's page of daily check-ins by borough:

    https://www.vote.nyc/page/early-voting-check-ins

which is reachable with no fingerprint at all (200 to plain `requests`), and
which covers roughly two fifths of the state's electorate.

Four things drive the code.

* **The page holds ONE election and its `<h2>` is the only label.** Today it
  reads "Primary Election 2026" over the June primary's 172,743 check-ins; in
  2022 it read "November General Election 2022" and in 2024 "General Election
  2024". A heading that does not name a GENERAL in this cycle's year is never
  eligible, so the primary can never be published as the general.

* **Every day's boroughs must add up to the Board's own total.** Each block ends
  with a line like "*As of Close of Polls – Unofficial and Cumulative 257,860",
  and the five boroughs sum to exactly that (verified on every day of all three
  fixtures). A day that does not reconcile is drift rather than data. The 2022
  layout puts the total on its own line ("Total Number of Early Voting Check-Ins
  49,709") and the 2024/2026 layout puts it inside the bold line; both are read
  the same way, by looking for the Board's number anywhere between the borough
  list and the next rule.

* **The series is cumulative from Day 2 onward, and Day 1 is the same thing.**
  So one fetch reconstructs the whole curve and a missed run loses nothing.

* **These are IN-PERSON check-ins.** `inperson` and `ballots_total` are the same
  number and `mail_returned` is None: New York's absentee ballots are counted by
  the counties and are not on this page at all. New York DOES enrol voters by
  party, and this page does not break it out, so all four party fields are None
  -- "not reported", not zero. See THE BLANK RULE in schema.py.

## The past cycles: the WHOLE curve, one capture per cycle

The Board overwrites this one URL each election, so the live host serves only
whatever is up today and a past cycle exists only in the Wayback Machine.
`fetch_history` reads it there, which is the same route `fl.py`, `de.py`,
`or.py` and `wa.py` already take -- see `wa.py`'s docstring for why the "third
party host" objection this method used to raise is a policy this repo does not
hold.

What makes New York cheap is the cumulative series: **one capture taken after
the last early-voting day carries every day of the period**, so the archive's
sparseness costs nothing at all here. VERIFIED 2026-09-08 via the CDX API
(exact-URL query, `collapse=digest`, `filter=statuscode:200`):

* **2024 -- six distinct captures in the window,** of which `20241104114451`
  (taken the day after early voting closed) carries all NINE days, Day 1's
  140,145 through Day 9's 1,089,328.
* **2022 -- ten distinct captures,** of which `20221108194750` carries all nine,
  ending at 432,634.

Every capture is read anyway rather than just the last, and a later capture of
the same day wins: the Board edits days after posting them, and a capture that
predates the section is skipped by `find_general` rather than guessed at. The
five-of-sixty-two rule is unchanged on this path -- an archived read emits
county rows and **still never a `StateDay`**.
"""

from __future__ import annotations

import html
import json
import logging
import re
from datetime import date, datetime, timedelta

from ..calendar import election_date
from ..schema import TIER_SCRAPER, CountyDay
from . import _fips
from ._net import Missing, get
from .base import Adapter, FetchResult, NotYetPublished, SchemaDrift, SourceError

log = logging.getLogger(__name__)

#: VERIFIED live 2026-09-06: 200, 73,496 bytes, plain `requests`, no browser
#: fingerprint needed. It carries the June 2026 primary today.
PAGE = "https://www.vote.nyc/page/early-voting-check-ins"

#: The Board writes boroughs; the census writes counties. The FIPS itself is
#: still looked up through `_fips`, so this table only translates the name --
#: it never hard-codes a code. All five are verified to resolve.
BOROUGH_COUNTIES = {
    "manhattan": "New York County",
    # VERIFIED, not assumed. The Board wrote "New York" rather than "Manhattan"
    # on the 2022-10-31 capture (`web.archive.org/web/20221031142410id_/`), and
    # the identification is proved by VALUE rather than by the name looking
    # right: that capture's "New York - 16,314" on 2022-10-29 is exactly the
    # 16,314 the 2022-11-08 capture files under New York County, with the other
    # four boroughs identical on the same day. Without this the whole capture
    # raised "unrecognised borough" -- correctly, under rule 3, which is what
    # sent it to be measured instead of guessed at.
    "new york": "New York County",
    "bronx": "Bronx County",
    "the bronx": "Bronx County",
    "brooklyn": "Kings County",
    "queens": "Queens County",
    "staten island": "Richmond County",
}

#: New York City is five of New York State's sixty-two counties. Both numbers
#: are here because the gap is the whole reason no StateDay is emitted.
#:
#: Counted over the DISTINCT counties rather than the keys, because the table
#: holds aliases -- "the bronx" for Bronx, "new york" for Manhattan -- and a
#: subtraction would have to be re-tuned every time one is added. Getting this
#: number wrong breaks the five-borough completeness check, which is the guard
#: that makes a truncated capture impossible to publish.
CITY_COUNTIES = len(set(BOROUGH_COUNTIES.values()))
STATE_COUNTIES = len(_fips.CENSUS_COUNTIES["NY"])

#: Words a heading must and must not carry to be this cycle's general.
GENERAL_WORD = "general"
OTHER_ELECTIONS = ("primary", "special", "run-off", "runoff")

#: How far either side of Election Day a day block may be dated. New York's
#: early-voting period is nine days, so this is generous on purpose.
WINDOW_BEFORE = 60
WINDOW_AFTER = 10

_H2 = re.compile(r"<h2[^>]*>(.*?)</h2>", re.S | re.I)
_LI = re.compile(r"<li[^>]*>(.*?)</li>", re.S | re.I)
_TAG = re.compile(r"<[^>]+>")
_COMMENT = re.compile(r"<!--.*?-->", re.S)

#: "October 26, 2024 - Day 1"
_DAY_HEADING = re.compile(
    r"<p[^>]*>\s*([A-Z][a-z]+\s+\d{1,2},\s*\d{4})\s*[-–]\s*Day\s*\d+\s*</p>",
    re.I,
)
#: "Manhattan - 38,237"
_BOROUGH = re.compile(r"^(?P<name>[A-Za-z .']+?)\s*[-–]\s*(?P<count>[\d,]+)$")

# --------------------------------------------------------------------------
# The archived series. See "The past cycles" in the module docstring.
# --------------------------------------------------------------------------
CDX_URL = "http://web.archive.org/cdx/search/cdx"

#: `id_` asks the Wayback Machine for the Board's ORIGINAL bytes rather than a
#: rewritten page.
WAYBACK_SNAPSHOT = "https://web.archive.org/web/{stamp}id_/" + PAGE

#: web.archive.org is slower and less tolerant than a state host. Same reasoning
#: as fl.py's ARCHIVE_MIN_INTERVAL and DEFAULT_MIN_INTERVAL in _net.
ARCHIVE_MIN_INTERVAL = 1.0

#: Ten distinct 2022 captures is the densest cycle the Archive holds for this
#: URL; 60 leaves headroom without ever running away.
MAX_ARCHIVE_PROBES = 60

#: The first cycle this repo publishes.
FIRST_CYCLE = 2022

#: The Wayback timestamp, `YYYYMMDDhhmmss`.
_STAMP = re.compile(r"^(\d{4})(\d{2})(\d{2})\d{6}$")


def stamp_day(stamp: str) -> date:
    """The date a capture was TAKEN, which is that capture's own as-of.

    A capture cannot carry a day later than the day it was made, so this is the
    honest `as_of` for an archived read -- exactly what the run date is for a
    live one. Deriving it from the CDX index rather than passing Election Day
    keeps `parse`'s day filter doing real work on BOTH paths.
    """
    m = _STAMP.match(str(stamp).strip())
    if m is None:
        raise SourceError(f"NY: {stamp!r} is not a Wayback timestamp")
    year, month, day = (int(g) for g in m.groups())
    try:
        return date(year, month, day)
    except ValueError as exc:
        raise SourceError(f"NY: {stamp!r} is not a Wayback timestamp") from exc


def archive_stamps(cycle: int) -> list[str]:
    """Wayback timestamps of every distinct version of the page in the window.

    `collapse=digest` turns the crawler's visits into the handful of genuinely
    distinct readings. Sorted here rather than trusted from the API, because
    `fetch_history` resolves two captures of one day by letting the later one
    win, and that is only true in order.
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
        body = get(CDX_URL, state="NY", filename=f"cdx-check-ins-{cycle}.json",
                   params=query, min_bytes=2, min_interval=ARCHIVE_MIN_INTERVAL)
    except Missing:
        # The CDX API answers "nothing archived" with an EMPTY body, which
        # _net.get reports as Missing. That is absence, not a fault.
        return []
    try:
        rows = json.loads(body.decode("utf-8", errors="replace"))
    except ValueError as exc:
        raise SourceError(f"NY: the Wayback CDX index was not JSON: {exc}") from exc
    return sorted(str(row[0]) for row in rows[1:])


def _text(fragment: str) -> str:
    cleaned = _TAG.sub(" ", _COMMENT.sub(" ", fragment or ""))
    return " ".join(html.unescape(cleaned).replace("\xa0", " ").split())


def _count(raw: str) -> int:
    text = raw.replace(",", "").strip()
    if not re.fullmatch(r"\d+", text):
        raise SchemaDrift(f"NY: {raw!r} is not a check-in count")
    return int(text)


def headings(page: str) -> list[tuple[int, str]]:
    return [(m.start(), _text(m.group(1))) for m in _H2.finditer(page or "")]


def find_general(page: str, cycle: int) -> int:
    """Where this cycle's general-election section starts, or NotYetPublished.

    The heading names the year, so a stale page from a previous cycle cannot be
    read as this one's, and the primary -- which is what is up today -- is never
    eligible.
    """
    for position, heading in headings(page):
        low = heading.lower()
        if str(cycle) not in low or any(w in low for w in OTHER_ELECTIONS):
            continue
        if GENERAL_WORD in low:
            return position
    # ⚠️ THE HEADING LIST GOES TO THE LOG, NOT INTO THE EXCEPTION.
    #
    # `ladder.run_state` puts `str(exc)` into `ev_status.json`'s `message`, and
    # the state panel prints that message to the reader verbatim. So dumping
    # seventeen navigation headings here put
    # "...(its headings are ['Register', 'Vote', 'NYC Elections', ...])" on a
    # public page under New York's name. The list is genuinely useful for
    # spotting the day the Board renames its section -- which is the whole
    # reason this function reads headings at all -- so it is kept, at DEBUG,
    # where a diagnostic belongs.
    found = [h for _, h in headings(page)]
    log.debug("NY: no %s general heading; page offers %r", cycle, found)
    raise NotYetPublished(
        f"NY: the New York City Board's check-in page has not opened its "
        f"{cycle} general-election section yet"
    )


def day_blocks(page: str, start: int, end: int) -> list[tuple[date, str]]:
    """(date, markup) for every "<Month D, YYYY> - Day N" block in the section."""
    marks = list(_DAY_HEADING.finditer(page, start, end))
    out: list[tuple[date, str]] = []
    for index, m in enumerate(marks):
        stop = marks[index + 1].start() if index + 1 < len(marks) else end
        try:
            day = datetime.strptime(
                " ".join(m.group(1).replace(",", " ").split()), "%B %d %Y"
            ).date()
        except ValueError as exc:
            raise SchemaDrift(f"NY: {m.group(1)!r} is not a date") from exc
        out.append((day, page[m.end():stop]))
    if not out:
        raise NotYetPublished(
            "NY: the general-election section carries no early-voting days yet"
        )
    return out


def boroughs(block: str) -> dict[str, int]:
    """{census county name: check-ins} for one day."""
    found: dict[str, int] = {}
    for m in _LI.finditer(block):
        item = _text(m.group(1))
        hit = _BOROUGH.match(item)
        if hit is None:
            raise SchemaDrift(f"NY: {item!r} is not a borough check-in line")
        name = " ".join(hit.group("name").lower().split())
        county = BOROUGH_COUNTIES.get(name)
        if county is None:
            raise SchemaDrift(f"NY: unrecognised borough {hit.group('name')!r}")
        if county in found:
            raise SchemaDrift(f"NY: {county} listed twice on one day")
        found[county] = _count(hit.group("count"))
    if len(found) != CITY_COUNTIES:
        raise SchemaDrift(
            f"NY: a day lists {len(found)} boroughs, not the {CITY_COUNTIES} "
            f"New York City has ({sorted(found)})"
        )
    return found


def stated_total(block: str, counts: dict[str, int]) -> int:
    """The Board's own cumulative figure for the day, checked against the parts.

    2022 writes it on its own line ("Total Number of Early Voting Check-Ins
    49,709") and 2024/2026 inside the bold line ("*As of Close of Polls –
    Unofficial and Cumulative 257,860"), so it is looked for anywhere after the
    borough list rather than in a fixed place.
    """
    tail = block.split("</ul>", 1)[-1]
    numbers = [_count(n) for n in re.findall(r"[\d,]*\d", _text(tail))]
    expected = sum(counts.values())
    if expected not in numbers:
        raise SchemaDrift(
            f"NY: the boroughs sum to {expected:,} but the Board's own figure "
            f"for the day is {numbers or 'missing'}"
        )
    return expected


def parse(body: bytes, cycle: int, as_of: date) -> FetchResult:
    """Parse the NYC check-in page into cumulative borough rows."""
    page = body.decode("utf-8", errors="replace")
    if "<h2" not in page.lower():
        raise SourceError("NY: the check-in page did not come back as a page")

    start = find_general(page, cycle)
    later = [p for p, _h in headings(page) if p > start]
    end = min(later) if later else len(page)

    voting = election_date(cycle)
    result = FetchResult()
    published = 0
    for day, block in day_blocks(page, start, end):
        if not (voting - timedelta(days=WINDOW_BEFORE) <= day
                <= voting + timedelta(days=WINDOW_AFTER)):
            raise SchemaDrift(
                f"NY: the {cycle} general's section carries a day dated "
                f"{day.isoformat()}, nowhere near {voting.isoformat()}"
            )
        counts = boroughs(block)
        stated_total(block, counts)
        if day > as_of:
            continue
        published += 1
        for county, check_ins in sorted(counts.items()):
            hit = _fips.lookup("NY", county)
            if hit is None:  # pragma: no cover - BOROUGH_COUNTIES is verified
                raise SchemaDrift(f"NY: {county!r} is not a New York county")
            fips, canonical = hit
            result.county_rows.append(CountyDay(
                cycle=cycle, state="NY", county_fips=fips, day=day,
                county_name=canonical,
                # In-person early-voting check-ins. The city's absentee ballots
                # are not on this page, so `mail_returned` stays blank.
                ballots_total=check_ins,
                ballots_new=None,
                mail_returned=None,
                inperson=check_ins,
                # New York enrols voters by party; this page does not split it.
                party_dem=None, party_rep=None, party_oth=None, party_npa=None,
            ))

    if not published:
        raise NotYetPublished(
            f"NY: the NYC Board has posted no {cycle} general check-ins on or "
            f"before {as_of.isoformat()}"
        )

    # PARTIAL COVERAGE. Five counties of sixty-two. There is no column in
    # StateDay that can say so, and a five-county figure published there would
    # be read as New York, so no statewide row is emitted -- ever.
    log.warning(
        "NY: NYC Board covers %d of New York's %d counties; publishing county "
        "rows only, never a statewide row",
        CITY_COUNTIES, STATE_COUNTIES,
    )
    return result


class NYScraper(Adapter):
    """Tier 1 for New York: the NYC Board's early-voting check-ins (5 counties)."""

    state = "NY"
    name = "ny-nycboe"
    tier = TIER_SCRAPER

    def _load(self, *, use_cache: bool) -> bytes:
        try:
            return get(PAGE, state="NY", filename="early-voting-check-ins.html",
                       use_cache=use_cache, min_bytes=4096)
        except Missing as exc:
            raise NotYetPublished(f"NY: {exc}") from exc

    def fetch(self, cycle: int, as_of: date) -> FetchResult:
        return parse(self._load(use_cache=False), cycle, as_of)

    def fetch_history(self, cycle: int) -> FetchResult:
        """A past cycle's borough curve, rebuilt from the Internet Archive.

        The Board overwrites this page each election, so nothing on the live
        host answers for a past cycle. An earlier version of this method
        refused the archive on the ground that web.archive.org is "a
        third-party host and not what a tier-1 scraper should read"; that is a
        policy this repo does not actually hold (see `wa.py`'s docstring, and
        `fl.py`, `de.py`, `or.py`), and it was costing New York every county row
        it has.

        ⚠️ GUARD PARITY WITH `fetch`. Every capture goes through the SAME
        `parse`, so the year-in-the-heading gate, the five-borough count, the
        per-day reconciliation against the Board's own cumulative figure and the
        date window all run here exactly as they do live. The only difference is
        the `as_of`, and it is the CAPTURE'S OWN DATE, so the day filter keeps
        doing real work instead of being handed Election Day.

        And the five-of-sixty-two rule is unchanged: `parse` appends to
        `county_rows` and never to `state_rows`, on this path as on the other.

        A capture that predates the general's section raises NotYetPublished out
        of `find_general`, which HERE means "this capture is not the election we
        asked for" and is skipped. Drift is not swallowed: if every capture
        drifted, the drift is what is raised, so a changed layout can never be
        reported as "nothing archived".
        """
        if cycle < FIRST_CYCLE:
            raise NotYetPublished(f"NY: {cycle} is before the first tracked cycle")
        if cycle >= date.today().year:
            raise NotYetPublished(f"NY: {cycle} is not an archived cycle")

        rows: dict[tuple[date, str], CountyDay] = {}
        drift: SchemaDrift | None = None
        seen = 0
        for stamp in archive_stamps(cycle):
            snapshot = WAYBACK_SNAPSHOT.format(stamp=stamp)
            try:
                # The stamp has to be in the cache filename: every capture is the
                # SAME URL, so a bare basename would make them one file.
                body = get(snapshot, state="NY",
                           filename=f"check-ins-{stamp}.html",
                           use_cache=True, min_bytes=4096,
                           min_interval=ARCHIVE_MIN_INTERVAL)
            except SourceError as exc:
                log.debug("NY: archived capture %s unusable (%s)", stamp, exc)
                continue
            seen += 1
            try:
                captured = parse(body, cycle, stamp_day(stamp))
            except NotYetPublished as exc:
                log.debug("NY: capture %s skipped (%s)", stamp, exc)
                continue
            except SchemaDrift as exc:
                # ONE bad capture is a bad capture, not a changed source. Held
                # so "every capture drifted" is still reported as drift.
                log.warning("NY: capture %s did not parse (%s)", stamp, exc)
                drift = exc
                continue
            except SourceError as exc:
                log.debug("NY: capture %s unreadable (%s)", stamp, exc)
                continue
            for row in captured.county_rows:
                # A later capture of the same day is the truer reading.
                rows[(row.day, row.county_fips)] = row

        if not rows:
            if drift is not None:
                raise drift
            raise NotYetPublished(
                f"NY: nothing archived for the {cycle} general "
                f"({seen} captures read)"
            )

        log.info("NY: %d archived county rows over %d days from %d captures",
                 len(rows), len({d for d, _f in rows}), seen)
        return FetchResult(county_rows=[rows[key] for key in sorted(rows)])
