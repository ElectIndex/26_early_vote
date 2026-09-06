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
"""

from __future__ import annotations

import html
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
    "bronx": "Bronx County",
    "the bronx": "Bronx County",
    "brooklyn": "Kings County",
    "queens": "Queens County",
    "staten island": "Richmond County",
}

#: New York City is five of New York State's sixty-two counties. Both numbers
#: are here because the gap is the whole reason no StateDay is emitted.
CITY_COUNTIES = len(BOROUGH_COUNTIES) - 1  # "the bronx" is an alias
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
    raise NotYetPublished(
        f"NY: the NYC Board's check-in page has no {cycle} general-election "
        f"section yet (its headings are {[h for _, h in headings(page)]})"
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
        """The Board overwrites this page each election, so there is no archive.

        The 2022 and 2024 generals survive only in the Wayback Machine
        (verified 200: `web.archive.org/web/20241102072441id_/...` and
        `...20221103025647id_/...`), which is a third-party host and not what a
        tier-1 scraper should read. Both captures are in tests/fixtures/ny, so
        the parser is exercised against both cycles even though the adapter
        cannot fetch them live.
        """
        raise NotYetPublished(
            f"NY: the NYC Board overwrites its check-in page each election, so "
            f"there is no {cycle} archive to backfill from"
        )
