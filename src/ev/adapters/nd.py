"""North Dakota: the SoS Voter Information Portal's absentee/early-voting page.

`https://vip.sos.nd.gov/abev.aspx?eid=<id>` is a WebForms page carrying, for one
election, a statewide Category/Value table and a county panel driven by a
dropdown. `docs/coverage-research.md` rejected it as "an HTML table needing 53
ASP.NET postbacks for county detail, [with] an opaque election id". Both halves
of that are true and neither is disqualifying:

* **The postbacks are STATELESS.** One `__VIEWSTATE` / `__EVENTVALIDATION` pair,
  scraped once from the GET, can be replayed for every county with no cookies
  and no session. Fifty-three POSTs cost about 1.6 MB and, PACED at `_net`'s own
  courtesy interval, about 25 seconds. They are deliberately not fired flat out:
  this is the one request path in the tree that does not go through `_net.get`,
  so it never inherited the throttle, and the ban this repo actually took was
  earned by going fast at exactly this kind of host. See `_county`.
* **The county numbers reconcile to the state's own totals exactly.** A full
  harvest of the 2024 general sums to 95,908 sent / 91,556 returned / 99,007
  early -- byte-for-byte the statewide figures on the same page. So the county
  rows are not an approximation of the state row; they are the state row.

**North Dakota does not register voters -- at all, not just not by party.** Every
`party_*` field on every row here is None, and always will be.

THE ELECTION ID IS THE WHOLE RISK
---------------------------------
`eid` is an opaque integer with no dropdown, and the page will render *any*
integer you hand it, zeroed, rather than refusing. The 2026 PRIMARY is 346 and
the 2026 GENERAL is 348 -- two apart -- and on 2026-09-06 the only `abev` link
anywhere on `sos.nd.gov` still pointed at the primary. Publishing 346 as the
general would be the Montana failure from `docs/coverage-research.md` with an
off-by-two instead of a stale cache.

So the id is never trusted on its own. `identify()` reads
`candidatelist.aspx?eid=<id>`, whose `lblFormHeader` names the election in
words -- "2026 General Election Contest/Candidate List" -- and the id is used
only if that says this cycle's general. A wrong or retired id is refused, and
`election_id()` then walks a bounded range upward from the pinned one looking
for the right header -- an id that does not exist redirects to the portal's
error page, which carries no header at all, so it simply does not match.

EARLY VOTING IS OPTIONAL IN NORTH DAKOTA, AND THE PAGE SHOWS IT BY OMISSION
--------------------------------------------------------------------------
Counties choose whether to run early voting, and the county panel simply leaves
the `lblEarlyVotes` block out for a county that does not. In the 2024 general
that is 46 of the 53 counties. An omission is normally "not reported" -- but
here the page's own statewide `Early Voting Turnout` equals the sum of the seven
counties that DO report, to the ballot, which proves the other 46 are zero
rather than unknown.

That proof is re-run on every fetch rather than assumed: if the counties that
report early votes add up to the statewide figure, the silent counties are
published as a real `0`; if they do not, every silent county's `inperson` is
None and so is its `ballots_total`, because half a total is not a total. See
`_settle_early`.

`ballots_total` is `returned + early`, which is North Dakota's own definition:
its `Total Ballots Cast prior to Election Day` row is exactly that sum
(91,556 + 99,007 = 190,563 in 2024, 70,064 + 36,513 = 106,577 in 2022).

THE PAGE CARRIES NO DATE
------------------------
There is no as-of stamp anywhere in the HTML. The XLSX export has one, but it is
the server's request clock -- two exports a minute apart read 11:10:07 and
11:11:08 -- so it dates the download, not the data. Rows are therefore stamped
with the run's own `as_of`, and a backfill's FINAL is stamped Election Day,
because what an archived `eid` serves today is that election's settled position
rather than a curve.

THE CURVE FOR A PAST CYCLE IS IN THE WAYBACK MACHINE, STATEWIDE ONLY
--------------------------------------------------------------------
The live `eid` serves a final; the daily positions behind it survive only as
Internet Archive captures of the bare GET. `fetch_history` walks them (see
`archive_captures` / `NDScraper._archived_curve`) and adds one `StateDay` per
captured pre-election day to the final harvest. What the Archive holds, by
distinct capture inside the 60-day window (CDX, `statuscode:200`):

    2024 (eid 333)  20241007230303  Oct 7 CT    61,943 /  9,395 /      0 /   9,395
                    20241008175928  Oct 8 CT    63,554 / 10,541 /      0 /  10,541
                    20241103073731  Nov 3 CT    93,360 / 80,858 / 91,750 / 172,608
                    20241106004518  Nov 5 CT    95,907 / 90,809 / 98,684 / 189,493
                    20241106050439  Nov 5 CT    95,907 / 90,814 / 98,684 / 189,498
    2022 (eid 326)  20221004011209  Oct 3 CT    46,139 /    659 /      0 /     659
                    20221018145632  Oct 18 CT   61,808 / 19,100 /      0 /  19,100
                    20221108033026  Nov 7 CT    76,042 / 68,021 / 36,443 / 104,464

(sent / returned / early / cast prior). Four things about that walk are not
obvious and each one cost a wrong number before it was written down:

* **COUNTY ROWS ARE IMPOSSIBLE.** The county panel is a POST (`_county`), and
  the Archive crawls GETs. No capture of any county postback exists for either
  cycle, so the archived curve is `StateDay` rows ONLY. County rows for 2022
  and 2024 stay the Election-Day finals from the live `eid`.
* **THE CAPTURE TIME IS THE AS-OF, IN CENTRAL TIME.** The page is rendered from
  the database on every request and carries no date of its own (above), so,
  unlike `ca.py`, there is no `x-archive-orig-last-modified` to prefer: the
  moment the crawler fetched it IS the moment the numbers were true. Wayback
  stamps are UTC; the SoS keeps its books in Bismarck, so the row's day is the
  **Central-time calendar date** of the capture, and the last capture of a day
  wins. The one that looks wrong is `20241103073731`: 07:37 UTC is 01:37 CST on
  Nov 3 -- effectively the end of Nov 2 -- and it is filed under **Nov 3**,
  because 01:37 on Nov 3 is when that position was read and nothing says
  whether the portal had moved since midnight. A day is the calendar date the
  state would have printed, not a guess about when the counters last ticked.
  (ND's southwest corner is on Mountain time; the portal is not.)
* **ONLY PRE-ELECTION CAPTURES ARE A CURVE.** `days_to_election >= 0`. The
  post-election captures (Nov 7, 10, 13 2024; Nov 9, 10, 12 2022) are NOT the
  final -- the figures kept moving for weeks as late mail was logged -- and the
  real final already sits at Election Day from the live `eid`. The archive's
  own Election-Day captures are read (they are the last readings of that day)
  but the live final wins that day, because the settled 190,563 supersedes an
  election-evening 189,498 by the same rule that lets a later capture
  supersede an earlier one.
* **A 302 IN THE INDEX SILENTLY SERVES A LATER PAGE.** `20241104234455` is a
  capture of the `http://` URL that the Archive answers with a redirect to
  `20241106004518` -- a page from a day and a half later. `requests` follows
  it, and the body parses perfectly, dated Nov 4 with Nov 5's numbers. So the
  CDX query filters `statuscode:200` AND every download's `Memento-Datetime` is
  checked against the stamp that was asked for; a capture that answers for a
  different instant is skipped, never dated by the instant we wanted.

And one trap in the markup itself: the portal has rendered the statewide values
in TWO vintages, `<span id=…lblEarlyVoting style="font-weight:bold;">36,443
</span>` today and `<span id=…lblEarlyVoting><b>36,443</b></span>` in several
captures (2022-10-04, 2022-11-08/09/10, 2024-11-10). A reader that wants text
directly inside the span sees the second as EMPTY -- every value None -- and
`parse_state` would call that a missing table. `_span` tolerates the inner `<b>`
so one parser serves the live page and every archived vintage, and anything else
inside the span still fails `_int` as drift rather than publishing a blank.

The election ids on this path are pinned, not discovered: `ARCHIVE_ELECTION_IDS`
holds only the cycles whose captures were read by hand (the table above), and
the abev page names no election anywhere in its markup, so an id here cannot be
re-verified the way `election_id()` re-verifies the live one. A cycle not in
that table is refused.
"""

from __future__ import annotations

import functools
import html as _html
import json
import logging
import re
import time
from datetime import date, datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

from ..calendar import election_date
from ..schema import TIER_SCRAPER, CountyDay, StateDay
from . import _fips
from ._net import (
    DEFAULT_HEADERS, DEFAULT_MIN_INTERVAL, DEFAULT_TIMEOUT, SESSION, Missing,
    impersonated_headers,
    _throttle, cache_path, get, looks_like_html,
)
from .base import Adapter, FetchResult, NotYetPublished, SchemaDrift, SourceError

log = logging.getLogger(__name__)

BASE = "https://vip.sos.nd.gov/abev.aspx"
CANDIDATE_LIST = "https://vip.sos.nd.gov/candidatelist.aspx"

#: Election ids, VERIFIED live 2026-09-06 by reading `candidatelist.aspx`'s own
#: `lblFormHeader` for each -- and re-verified on every fetch, because the id is
#: the one thing here that can silently be about a different election.
#:
#:   326 -> "2022 General Election ..."   (abev: 76,034 sent / 106,577 cast)
#:   333 -> "2024 General Election ..."   (abev: 95,908 sent / 190,563 cast)
#:   348 -> "2026 General Election ..."   (abev: 63 sent, 0 returned, today)
#:
#: 346 is the 2026 PRIMARY and must never be used. 349 and above do not exist:
#: candidatelist 302s to the portal's error page, which names no election.
ELECTION_IDS: dict[int, int] = {2022: 326, 2024: 333, 2026: 348}

#: How far past a pinned id to look if it stops naming the right election.
ID_SEARCH_SPAN = 12

# --------------------------------------------------------------------------
# THE ARCHIVED CURVE -- see the module docstring. Runs in `backfill` only.
# --------------------------------------------------------------------------
CDX_URL = "http://web.archive.org/cdx/search/cdx"

#: `id_` asks for the SoS's ORIGINAL bytes rather than a rewritten page.
WAYBACK_SNAPSHOT = "https://web.archive.org/web/{stamp}id_/{url}"

#: The cycles whose Wayback captures have been read by hand, and the id each
#: was captured under. ⚠️ NOT `ELECTION_IDS`, deliberately: that table carries
#: the RUNNING cycle for the live fetch, and the abev page names no election in
#: its markup, so an archived capture cannot be checked against
#: `candidatelist.aspx` the way the live id is. Both ids below are the ones
#: sos.nd.gov's Past Elections page links for those generals, and the captured
#: finals reconcile to the live `eid`'s (2024: 95,907 sent on Nov 5 against
#: 95,908 settled). A cycle absent here is refused, not guessed.
ARCHIVE_ELECTION_IDS: dict[int, int] = {2022: 326, 2024: 333}

#: Days before Election Day the sweep starts. The earliest ballots go out ~40
#: days ahead (UOCAVA at 46); the Archive's first in-season capture is Oct 3-7.
ARCHIVE_WINDOW_BEFORE = 60

#: 26 captures of eid 333 and 90 of eid 326 are on the index ACROSS ALL YEARS;
#: inside a 60-day window it is a dozen. 400 leaves room without running away.
MAX_ARCHIVE_PROBES = 400

#: web.archive.org is slower and less tolerant than a state host. Same
#: reasoning as ca.py's and fl.py's constants of the same name.
ARCHIVE_MIN_INTERVAL = 1.0

#: The Wayback reply header that says which instant was actually served.
MEMENTO_HEADER = "memento-datetime"

#: Where the Secretary of State keeps the books. Bismarck is Central.
STATE_TIMEZONE = "America/Chicago"

#: A Wayback capture timestamp: `YYYYMMDDhhmmss`, UTC.
_STAMP = re.compile(r"^\d{14}$")

#: The header `candidatelist.aspx` prints for a cycle's general.
_FORM_HEADER = re.compile(
    r'id="[^"]*lblFormHeader"[^>]*>\s*([^<]*?)\s*<', re.I
)

#: Statewide Category/Value rows, by the span id that carries each value.
STATE_SENT = "lblTotalSent"
STATE_RETURNED = "lblTotalReturned"
STATE_EARLY = "lblEarlyVoting"
STATE_CAST = "lblBallotsCast"
STATE_LABELS = (STATE_SENT, STATE_RETURNED, STATE_EARLY, STATE_CAST)

#: The county panel's three spans. `lblEarlyVotes` is ABSENT, not zero, for a
#: county that runs no early voting -- see the module docstring.
COUNTY_SENT = "lblSent"
COUNTY_RETURNED = "lblReturned"
COUNTY_EARLY = "lblEarlyVotes"
COUNTY_HEADER = "lblHeader"

_COUNTY_OPTION = re.compile(r'<option value="(\d{2})">([^<]+)</option>')
_HIDDEN = r'id="{name}"[^>]*value="([^"]*)"'
#: The dropdown whose postback swaps the county panel.
COUNTY_CONTROL = "ctl00$ContentPlaceHolder1$ddlCounty"

EXPECTED_COUNTIES = len(_fips.CENSUS_COUNTIES["ND"])


#: The one wrapper the portal has ever put INSIDE a value span. Two vintages of
#: the same page: `style="font-weight:bold;"` on the span today, a bare `<b>`
#: inside it in several 2022 and 2024 captures. Only `<b>` is stripped -- any
#: other tag left in the text fails `_int` as drift, which is the right answer
#: for a wrapper nobody has seen.
_INNER_BOLD = re.compile(r"</?b\s*>", re.I)


def _span(markup: str, name: str) -> str | None:
    """The text of the span whose id ENDS with `name`, or None if absent.

    Tolerates the archived vintage that bolds the value with an inner `<b>`
    rather than a style on the span -- see the module docstring. Without this
    the 2022-10-04 capture reads as a page with no values at all.
    """
    match = re.search(
        r'<span id="[^"]*_' + re.escape(name) + r'"[^>]*>(.*?)</span>',
        markup, re.S,
    )
    return _INNER_BOLD.sub("", match.group(1)) if match else None


def _int(value: str | None) -> int | None:
    """A count, or None when the page did not print one."""
    if value is None:
        return None
    text = value.strip().replace(",", "").replace("\xa0", "")
    if not text or text in {"-", "--"}:
        return None
    try:
        return int(text)
    except ValueError as exc:
        raise SchemaDrift(f"ND: {value!r} is not a ballot count") from exc


def _hidden(markup: str, name: str) -> str | None:
    match = re.search(_HIDDEN.format(name=re.escape(name)), markup)
    return _html.unescape(match.group(1)) if match else None


def identify(markup: str) -> str:
    """The election `candidatelist.aspx` says an id belongs to."""
    match = _FORM_HEADER.search(markup or "")
    if match is None:
        raise SchemaDrift("ND: candidate list carries no election header")
    return " ".join(match.group(1).split())


def is_general(header: str, cycle: int) -> bool:
    """True only for "<cycle> General Election ...".

    Anchored at the front so "2026 Primary Election" and the numbered special
    elections cannot match, and the cycle must be the one asked for.
    """
    return bool(re.match(rf"^{int(cycle)}\s+General\s+Election\b", header, re.I))


def counties(markup: str) -> list[tuple[str, str]]:
    """(dropdown value, county name) for every county on the page."""
    seen: dict[str, str] = {}
    for value, name in _COUNTY_OPTION.findall(markup):
        seen.setdefault(value, " ".join(_html.unescape(name).split()))
    return sorted(seen.items())


def parse_state(markup: str) -> dict[str, int | None]:
    """The statewide Category/Value table."""
    values = {name: _int(_span(markup, name)) for name in STATE_LABELS}
    missing = [name for name, value in values.items() if value is None]
    if missing:
        raise SchemaDrift(
            f"ND: the statewide table is missing {missing} -- the page's layout "
            "changed, or this eid is not an absentee report"
        )
    counts = {name: int(value) for name, value in values.items() if value is not None}
    total = counts[STATE_CAST]
    parts = counts[STATE_RETURNED] + counts[STATE_EARLY]
    if total != parts:
        # North Dakota's own arithmetic. If it stops holding, the meaning of one
        # of these rows has changed and guessing which would publish a confident
        # wrong number.
        raise SchemaDrift(
            f"ND: 'Total Ballots Cast prior to Election Day' is {total}, but "
            f"returned + early is {parts}"
        )
    return values


def parse_county(markup: str, expected: str) -> tuple[int | None, int | None, int | None]:
    """(sent, returned, early) from one county postback's panel.

    `early` is None when the county's early-vote block is absent, which is what
    North Dakota does for a county that runs no early voting. `_settle_early`
    decides whether that None is a real zero.
    """
    header = _span(markup, COUNTY_HEADER)
    if header is None:
        raise SourceError(f"ND: the postback for {expected} returned no county panel")
    if not header.lower().startswith(expected.lower()):
        # The postback is stateless, so a mismatch means the server answered for
        # a different county than we asked for -- every subsequent number would
        # be filed under the wrong FIPS.
        raise SchemaDrift(
            f"ND: asked for {expected}, the panel says {header!r}"
        )
    return (
        _int(_span(markup, COUNTY_SENT)),
        _int(_span(markup, COUNTY_RETURNED)),
        _int(_span(markup, COUNTY_EARLY)),
    )


def _settle_early(
    reported: dict[str, int | None], statewide: int | None
) -> dict[str, int | None]:
    """Decide what a county's ABSENT early-vote block means.

    North Dakota omits the block for a county that runs no early voting. If the
    counties that DO print one add up to the state's own early-voting total,
    then the silent counties cast no early ballots and a real 0 is the truthful
    value -- verified for the 2024 general, where seven counties reporting sum
    to 99,007 and the state prints 99,007.

    If they do not add up, something is unreported rather than zero, and every
    silent county keeps None. A gap is recoverable; a fabricated zero is not.
    """
    present = [v for v in reported.values() if v is not None]
    if statewide is not None and sum(present) == statewide:
        return {k: (0 if v is None else v) for k, v in reported.items()}
    log.warning(
        "ND: counties reporting early votes sum to %d against a statewide %s; "
        "leaving the silent counties blank rather than assuming zero",
        sum(present), statewide,
    )
    return dict(reported)


def _total(returned: int | None, early: int | None) -> int | None:
    """Ballots cast before Election Day: North Dakota's own definition."""
    if returned is None or early is None:
        return None
    return returned + early


def state_row(statewide: dict[str, int | None], cycle: int, day: date) -> StateDay:
    """The statewide row for one reading of the Category/Value table.

    One function for the live page and every archived capture, so the two
    paths cannot drift on what `ballots_total` or `inperson` means. An
    `Early Voting Turnout` of 0 in an October capture is a REAL zero -- in-person
    early voting had not opened -- and is published as one, exactly as the live
    page's would be.
    """
    return StateDay(
        cycle=cycle, state="ND", day=day,
        # The page's own total, never our sum -- though the two agree exactly.
        ballots_total=statewide[STATE_CAST],
        # The page is a cumulative position with no daily column of its own.
        ballots_new=None,
        mail_requested=statewide[STATE_SENT],
        mail_returned=statewide[STATE_RETURNED],
        inperson=statewide[STATE_EARLY],
        # North Dakota has no voter registration at all. Never 0.
        party_dem=None, party_rep=None, party_oth=None, party_npa=None,
    )


def build(
    statewide: dict[str, int | None],
    rows: dict[str, tuple[str, int | None, int | None, int | None]],
    cycle: int,
    day: date,
) -> FetchResult:
    """Assemble canonical rows from one statewide table and its county panels."""
    early = _settle_early({fips: r[3] for fips, r in rows.items()},
                          statewide[STATE_EARLY])

    result = FetchResult()
    result.state_rows.append(state_row(statewide, cycle, day))

    for fips, (name, sent, returned, _) in sorted(rows.items()):
        result.county_rows.append(CountyDay(
            cycle=cycle, state="ND", county_fips=fips, day=day,
            county_name=name,
            ballots_total=_total(returned, early[fips]),
            ballots_new=None,
            mail_returned=returned,
            inperson=early[fips],
            party_dem=None, party_rep=None, party_oth=None, party_npa=None,
        ))
        if sent is None:
            log.warning("ND: %s printed no ballots-sent figure", name)
    return result


# --------------------------------------------------------------------------
# The Wayback walk
# --------------------------------------------------------------------------
@functools.cache
def _state_tz():
    """The SoS's clock, resolved once and late.

    Late so that a checkout without a tz database (a bare Windows Python with
    no `tzdata` wheel) breaks the ARCHIVE path with a SourceError rather than
    the module import -- the live fetch never needs a timezone.
    """
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError  # noqa: PLC0415

    try:
        return ZoneInfo(STATE_TIMEZONE)
    except ZoneInfoNotFoundError as exc:
        raise SourceError(
            f"ND: no tz database for {STATE_TIMEZONE}; archived captures "
            "cannot be dated"
        ) from exc


def capture_instant(stamp: str) -> datetime:
    """A Wayback timestamp as the UTC instant it names."""
    text = str(stamp).strip()
    if not _STAMP.match(text):
        raise SourceError(f"ND: {stamp!r} is not a Wayback timestamp")
    try:
        return datetime.strptime(text, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise SourceError(f"ND: {stamp!r} is not a Wayback timestamp") from exc


def capture_day(stamp: str) -> date:
    """The as-of day of a capture: its CENTRAL-TIME calendar date.

    The page is rendered from the database on every request and carries no
    date, so the capture instant is when the numbers were true, and the day the
    Secretary of State would have printed for that instant is the one in
    Bismarck. `20241106004518` -- 00:45 UTC on Nov 6 -- is 18:45 CST on
    Election Day, Nov 5, and is filed there. `20241103073731` is 01:37 CST on
    Nov 3 and is filed under Nov 3: the calendar date, not a judgement about
    whether the counters had moved since midnight. See the module docstring.
    """
    return capture_instant(stamp).astimezone(_state_tz()).date()


def memento_stamp(header: str | None) -> str | None:
    """A `Memento-Datetime` header as a Wayback timestamp, or None if absent.

    The header is RFC 1123 (`Wed, 06 Nov 2024 00:45:18 GMT`); the stamp the
    capture was asked for is `20241106004518`. Same instant, two spellings --
    this puts them in one so they can be compared.
    """
    if not header:
        return None
    try:
        instant = datetime.strptime(header.strip(), "%a, %d %b %Y %H:%M:%S %Z")
    except ValueError:
        return None
    return instant.strftime("%Y%m%d%H%M%S")


def _eid_of(url: str) -> int | None:
    """The `eid` in a captured URL's query string, or None if it has none."""
    values = parse_qs(urlsplit(url).query).get("eid") or []
    try:
        return int(values[0]) if values else None
    except ValueError:
        return None


def archive_captures(cycle: int) -> list[tuple[str, str]]:
    """(timestamp, original URL) for every capture worth reading, oldest first.

    Swept by PREFIX on `abev.aspx?eid=<id>` so a capture keyed under a junk
    query string (`&foo=`) or the `http://` spelling is found too -- then each
    row's own `eid` is checked, because that prefix would also match
    `eid=3330`. `statuscode:200` keeps the `http://` 302 of 2024-11-04 out of
    the list (see the module docstring); `Memento-Datetime` on the download is
    the second check on the same trap.

    The window runs from `ARCHIVE_WINDOW_BEFORE` days ahead of Election Day to
    **the day after** it in UTC: the election-evening captures that belong to
    Election Day in Bismarck are stamped the next morning in UTC
    (`20241106004518` is 18:45 CST on Nov 5). `_archived_curve` then drops
    anything whose CENTRAL date is after Election Day.

    Deduplicated on (Central day, digest) rather than on digest alone: the same
    bytes captured three times in one evening are one reading, but the same
    bytes on two different days are two true readings of a page that did not
    move, and a curve with the second day missing looks like a gap.

    Raises SourceError when the index is unreachable or not JSON -- the
    Archive has been "Temporarily Offline" for hours at a stretch, and a walk
    that read that as "nothing archived" would quietly publish no curve.
    """
    eid = ARCHIVE_ELECTION_IDS.get(int(cycle))
    if eid is None:
        raise NotYetPublished(
            f"ND: no archived election id is pinned for {cycle} "
            f"(known: {sorted(ARCHIVE_ELECTION_IDS)})"
        )
    anchor = election_date(cycle)
    query = {
        "url": f"vip.sos.nd.gov/abev.aspx?eid={eid}",
        "matchType": "prefix", "output": "json",
        "fl": "timestamp,original,statuscode,digest",
        "filter": "statuscode:200",
        "from": (anchor - timedelta(days=ARCHIVE_WINDOW_BEFORE)).strftime("%Y%m%d"),
        "to": (anchor + timedelta(days=1)).strftime("%Y%m%d"),
        "limit": str(MAX_ARCHIVE_PROBES),
    }
    _throttle(CDX_URL, ARCHIVE_MIN_INTERVAL)
    try:
        response = SESSION.get(CDX_URL, params=query, headers=DEFAULT_HEADERS,
                               timeout=DEFAULT_TIMEOUT)
    except Exception as exc:  # noqa: BLE001 - requests has its own hierarchy
        raise SourceError(f"ND: the Wayback CDX index was unreachable: {exc}") from exc
    if response.status_code != 200:
        raise SourceError(
            f"ND: the Wayback CDX index answered HTTP {response.status_code}"
        )
    text = response.text.strip()
    if not text:
        # The CDX API answers "nothing archived" with an empty body. Absence,
        # not a fault.
        return []
    try:
        rows = json.loads(text)
    except ValueError as exc:
        raise SourceError(f"ND: the Wayback CDX index was not JSON: {exc}") from exc

    seen: dict[tuple[date, str], tuple[str, str]] = {}
    for row in sorted(rows[1:], key=lambda r: str(r[0])):
        stamp, original, status, digest = (str(v) for v in row[:4])
        if status != "200":
            # Belt to the query's braces: a 302 here is the trap itself.
            continue
        if _eid_of(original) != eid:
            log.debug("ND: capture %s is of %s, not eid %d", stamp, original, eid)
            continue
        day = capture_day(stamp)
        if day > anchor:
            # Decided HERE, off the index, so a post-election capture costs no
            # request: the window reaches a UTC day past Election Day only to
            # catch Bismarck's election evening, and 00:13 CST on Nov 6 is not
            # that. Every trip to web.archive.org is one the 429 can land on.
            log.debug("ND: capture %s is %s, after Election Day", stamp, day)
            continue
        seen.setdefault((day, digest), (stamp, original))
    return sorted(seen.values())


def archived_page(stamp: str, original: str, *, eid: int) -> tuple[str, str | None]:
    """One capture's original bytes and the stamp the Archive says it served.

    Mirrored under `cache/nd/` with a `.memento` sidecar, so a re-run reads the
    page AND its served instant from disk without another trip to
    web.archive.org -- `use_cache` semantics as `_net.get`'s, and only ever for
    an archived capture, which cannot change. A cached page with no sidecar is
    re-fetched: without the served instant it cannot be trusted to be the
    capture its filename claims.

    `_net.get` is not used because it discards response headers, and
    `Memento-Datetime` is the only thing that distinguishes the capture asked
    for from the later one a 302 silently substitutes.

    Raises SourceError, never skips, on a transport failure: the Archive flaps,
    and a curve with the failed captures quietly left out is a wrong curve
    that looks like a thin one.
    """
    url = WAYBACK_SNAPSHOT.format(stamp=stamp, url=original)
    path = cache_path("ND", f"archive_{eid}_{stamp}.html")
    sidecar = path.with_suffix(path.suffix + ".memento")
    if path.exists() and sidecar.exists() and path.stat().st_size >= 4096:
        return (path.read_text(encoding="utf-8", errors="replace"),
                memento_stamp(sidecar.read_text().strip()))

    _throttle(url, ARCHIVE_MIN_INTERVAL)
    try:
        # No spoofed browser UA on the Archive: it flags a Chrome User-Agent on
        # a python-requests handshake as a bot and 429s it indefinitely (see
        # oh.py ARCHIVE_RETRY_WAITS, measured 2026-10-13).
        response = SESSION.get(url, headers=impersonated_headers(DEFAULT_HEADERS),
                               timeout=DEFAULT_TIMEOUT)
    except Exception as exc:  # noqa: BLE001 - requests has its own hierarchy
        raise SourceError(f"ND: GET {url} failed: {exc}") from exc
    if response.status_code != 200:
        raise SourceError(f"ND: {url} answered HTTP {response.status_code}")
    body = response.content
    if len(body) < 4096 or not looks_like_html(body):
        raise SourceError(f"ND: {url} did not return the page ({len(body)} bytes)")
    served = response.headers.get(MEMENTO_HEADER)
    path.write_bytes(body)
    if served:
        sidecar.write_text(served)
    return body.decode("utf-8", errors="replace"), memento_stamp(served)


class NDScraper(Adapter):
    """Tier 1 for North Dakota: the SoS voter portal's absentee/early page."""

    state = "ND"
    name = "nd-sos"
    tier = TIER_SCRAPER

    # ---- the election id ---------------------------------------------------
    def _header(self, eid: int, *, use_cache: bool) -> str | None:
        """`candidatelist.aspx`'s name for an id, or None if it does not exist.

        A missing id answers 302 to the portal's error page rather than 404, so
        "no such election" arrives as a 200 with no header element and is read
        as absence.

        A transport failure is NOT absence and is deliberately allowed to
        propagate: if the portal is unreachable we do not know which election is
        which, and swallowing that here would turn "we could not look" into
        `NotYetPublished`, which STOPS the ladder. See rule 2 in CLAUDE.md.
        """
        try:
            body = get(
                f"{CANDIDATE_LIST}?eid={eid}", state="ND",
                filename=f"candidatelist_{eid}.html",
                use_cache=use_cache, min_bytes=2048,
            )
        except Missing:
            return None
        try:
            return identify(body.decode("utf-8", errors="replace"))
        except SchemaDrift:
            return None

    def election_id(self, cycle: int, *, use_cache: bool = False) -> int:
        """The id of `cycle`'s November general, checked against its own name.

        The pinned id is tried first and ACCEPTED ONLY if the portal agrees it
        is that cycle's general. Otherwise the search walks upward -- ids are
        issued in order, and the 2026 general (348) is two past the 2026 primary
        (346) -- and gives up rather than guessing.
        """
        pinned = ELECTION_IDS.get(int(cycle))
        problems: list[str] = []
        if pinned is not None:
            header = self._header(pinned, use_cache=use_cache)
            if header and is_general(header, cycle):
                return pinned
            problems.append(f"eid {pinned} is {header!r}")

        start = (pinned or max(ELECTION_IDS.values())) + 1
        for eid in range(start, start + ID_SEARCH_SPAN):
            header = self._header(eid, use_cache=use_cache)
            if header is None:
                continue
            if is_general(header, cycle):
                log.warning(
                    "ND: pinned election id for %s was wrong; using %d (%s)",
                    cycle, eid, header,
                )
                return eid
        raise NotYetPublished(
            f"ND: the voter portal lists no {cycle} general election "
            f"({'; '.join(problems) or 'nothing in the id range'})"
        )

    # ---- the page ----------------------------------------------------------
    def _page(self, eid: int, *, use_cache: bool) -> str:
        body = get(f"{BASE}?eid={eid}", state="ND",
                   filename=f"abev_{eid}.html", use_cache=use_cache, min_bytes=4096)
        markup = body.decode("utf-8", errors="replace")
        if not looks_like_html(body):
            raise SourceError(f"ND: {BASE}?eid={eid} did not return a page")
        return markup

    def _county(
        self, eid: int, markup: str, value: str, *, use_cache: bool = False
    ) -> str:
        """One county's panel, by replaying the page's own postback tokens.

        No session and no cookies: the tokens from the initial GET validate on
        their own, which is what makes 53 counties cheap. `__VIEWSTATE`,
        `__EVENTVALIDATION` and `__EVENTTARGET` are all required -- dropping any
        one of them answers the portal's error page with HTTP 200.

        Every response is mirrored under `cache/nd/` like a `_net.get` would be,
        so a parse can be re-run offline instead of putting 53 more POSTs
        through a county election site. `use_cache` serves that copy back, which
        is only ever passed for a past cycle's settled numbers.
        """
        path = cache_path("ND", f"abev_{eid}_county_{value}.html")
        if use_cache and path.exists() and path.stat().st_size >= 4096:
            return path.read_text(encoding="utf-8", errors="replace")

        # ⚠️ THIS LOOP IS 53 REQUESTS TO ONE STATE HOST, and it is the only
        # request path in the tree that does not go through `_net.get` -- a
        # POST, so it never met `_net`'s throttle and ran flat out. Fifty-three
        # postbacks in "about half a minute", as the module docstring cheerfully
        # says, is roughly two a second at a county election site. The host-wide
        # 403 this repo actually took (see `_net.DEFAULT_MIN_INTERVAL`) was
        # earned at about seven a second, and it cost half an hour and a wrong
        # verdict about why a state was missing. The same courtesy interval is
        # applied here by hand rather than by reaching into `_net`'s private
        # throttle, and it costs one run 21 seconds twice a day.
        time.sleep(DEFAULT_MIN_INTERVAL)

        form = {
            "__EVENTTARGET": COUNTY_CONTROL,
            "__EVENTARGUMENT": "",
            "__VIEWSTATE": _hidden(markup, "__VIEWSTATE"),
            "__VIEWSTATEGENERATOR": _hidden(markup, "__VIEWSTATEGENERATOR") or "",
            "__EVENTVALIDATION": _hidden(markup, "__EVENTVALIDATION"),
            COUNTY_CONTROL: value,
        }
        if not form["__VIEWSTATE"] or not form["__EVENTVALIDATION"]:
            raise SchemaDrift("ND: the absentee page carries no WebForms tokens")
        url = f"{BASE}?eid={eid}"
        try:
            response = SESSION.post(
                url, data=form, timeout=60,
                headers={**DEFAULT_HEADERS, "Referer": url,
                         "Content-Type": "application/x-www-form-urlencoded"},
            )
        except Exception as exc:  # noqa: BLE001 -- requests raises several types
            raise SourceError(f"ND: county postback failed: {exc}") from exc
        if response.status_code != 200:
            raise SourceError(
                f"ND: county postback returned HTTP {response.status_code}"
            )
        path.write_bytes(response.content)
        return response.text

    def _harvest(self, cycle: int, day: date, *, use_cache: bool) -> FetchResult:
        eid = self.election_id(cycle, use_cache=use_cache)
        markup = self._page(eid, use_cache=use_cache)
        statewide = parse_state(markup)

        if not any(statewide[name] for name in
                   (STATE_SENT, STATE_RETURNED, STATE_EARLY, STATE_CAST)):
            # The portal renders a zeroed page for an election it has not begun
            # loading, which is indistinguishable from one where nothing has
            # happened yet. Either way there is nothing to plot, and calling it
            # data would start the series with a fabricated flat line.
            raise NotYetPublished(
                f"ND: the {cycle} general (eid {eid}) is listed but carries no "
                "ballots yet"
            )

        options = counties(markup)
        if len(options) != EXPECTED_COUNTIES:
            raise SchemaDrift(
                f"ND: the county dropdown lists {len(options)} of "
                f"{EXPECTED_COUNTIES} counties"
            )

        rows: dict[str, tuple[str, int | None, int | None, int | None]] = {}
        unknown: list[str] = []
        for value, name in options:
            hit = _fips.lookup("ND", name)
            if hit is None:
                unknown.append(name)
                continue
            fips, canonical = hit
            sent, returned, early = parse_county(
                self._county(eid, markup, value, use_cache=use_cache), name
            )
            rows[fips] = (canonical, sent, returned, early)

        if unknown:
            raise SchemaDrift(f"ND: unrecognised county names {sorted(set(unknown))}")
        if len(rows) != EXPECTED_COUNTIES:
            raise SchemaDrift(
                f"ND: harvested {len(rows)} of {EXPECTED_COUNTIES} counties"
            )
        return build(statewide, rows, cycle, day)

    # ---- the contract ------------------------------------------------------
    def fetch(self, cycle: int, as_of: date) -> FetchResult:
        """Today's cumulative position.

        The page carries no as-of date of its own -- see the module docstring --
        so the run's own date is what stamps the rows.
        """
        return self._harvest(cycle, as_of, use_cache=False)

    # ---- the archived curve ------------------------------------------------
    def _archived_curve(self, cycle: int) -> dict[date, dict[str, int | None]]:
        """The statewide table as of each archived PRE-election day.

        Keyed by Central calendar date; the last capture of a day wins, which
        is why `archive_captures` is walked oldest first. A capture is skipped
        -- with a warning, never silently -- only when the Archive served a
        different instant than the one asked for (`Memento-Datetime` disagrees
        with the stamp: the 302 trap), when its Central date is after Election
        Day (the moving post-election figures are not the final), or when the
        page is all zeros (the portal's "not loaded yet" rendering, exactly as
        `_harvest` reads it live). Anything unreadable raises: a value this
        parser cannot read is drift, not a blank.
        """
        anchor = election_date(cycle)
        by_day: dict[date, dict[str, int | None]] = {}
        read = 0
        for stamp, original in archive_captures(cycle):
            markup, served = archived_page(
                stamp, original, eid=ARCHIVE_ELECTION_IDS[int(cycle)]
            )
            if served != stamp:
                log.warning(
                    "ND: asked the Archive for %s and it served %s -- a redirect "
                    "to a different capture; skipped rather than misdated",
                    stamp, served,
                )
                continue
            read += 1
            day = capture_day(stamp)
            if day > anchor:
                log.debug("ND: capture %s is %s, after Election Day", stamp, day)
                continue
            statewide = parse_state(markup)
            if not any(statewide.values()):
                log.info("ND: capture %s carries no ballots yet; skipped", stamp)
                continue
            by_day[day] = statewide
        log.info("ND: %d archived days for %s from %d captures",
                 len(by_day), cycle, read)
        return by_day

    def fetch_history(self, cycle: int) -> FetchResult:
        """A past cycle's FINAL by county, plus its STATEWIDE curve from the Archive.

        An archived `eid` still answers, and still answers with county detail --
        `eid=326` and `eid=333` both do today -- but it serves the election's
        settled numbers, not a series. So the county rows are one per county
        dated Election Day, and so is the statewide final. The days before it
        come from the Wayback Machine's captures of the bare GET
        (`_archived_curve`), statewide only, because no county postback was
        ever crawled. On Election Day itself the live final wins over the
        archive's election-evening reading -- see the module docstring.

        The archive walk runs AFTER the final harvest and its failure
        propagates: an unreachable Archive is a SourceError for the whole
        backfill, not a final quietly published without its curve.

        ⚠️ GUARD PARITY, and this is the half that was missing. `fetch` stamps
        its rows with the RUN's date, which cannot be in the future. This path
        stamps them with Election Day, which for the running cycle very much
        can: `ELECTION_IDS` carries 2026, the portal answers for it, and
        `backfill --cycle 2026` therefore harvested today's position -- a state
        row of 0 ballots -- and dated it 2026-11-03. That row lands at
        days_to_election 0 and becomes the cycle's FINAL for everything that
        compares against it, which is the worst possible place to put a
        fabricated number. Same failure as `de.py`'s future-dated capture; same
        shape of guard as `wa.py`'s, which refuses a cycle that is not over.
        """
        if int(cycle) not in ELECTION_IDS:
            raise NotYetPublished(f"ND: no known election id for {cycle}")
        day = election_date(cycle)
        if day > date.today():
            raise NotYetPublished(
                f"ND: the {cycle} general has not happened yet -- this path "
                f"dates every row {day.isoformat()}, so backfilling a running "
                f"cycle would publish today's position as that cycle's final. "
                f"The daily `fetch` is what tracks {cycle}."
            )
        result = self._harvest(cycle, day, use_cache=True)
        if int(cycle) not in ARCHIVE_ELECTION_IDS:
            # A cycle whose captures nobody has read by hand gets its final and
            # no curve, loudly: the pinned table is the only guard on this path.
            log.warning("ND: no archived curve is pinned for %s; final only", cycle)
            return result
        for archived_day, statewide in sorted(self._archived_curve(cycle).items()):
            if archived_day >= day:
                # Election Day is the live final's. The archive's reading of
                # that evening is real but superseded, by the same rule that
                # lets the later capture of any day supersede the earlier.
                continue
            result.state_rows.append(state_row(statewide, cycle, archived_day))
        result.state_rows.sort(key=lambda row: row.day)
        return result
