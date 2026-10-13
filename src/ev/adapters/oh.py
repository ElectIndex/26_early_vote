"""Ohio: the Secretary of State's county absentee data, by three routes.

THE PROBLEM THIS MODULE IS SHAPED AROUND
----------------------------------------
Every host in the `ohiosos.gov` zone sits behind a Cloudflare *managed challenge*
(`cf-mitigated: challenge`, a 1.25 MB interstitial branded "Ohio Secretary of
State's Office Website Maintenance"). `requests`, stdlib `urllib` and the `curl`
binary are all refused with HTTP 403 -- the site root, a bogus path and a file we
know exists all answer identically, so on that host we cannot even tell "missing"
from "blocked". Only a full browser TLS/HTTP2 fingerprint passes. See
docs/ohio-source.md for the measurements.

So this adapter tries three routes and takes the first that answers:

1. **The legacy `globalassets` workbook.** Kept first because it is the URL the
   parser was built against and it costs one request to find out. As of
   2026-09-06 that whole path is retired -- the SoS site was rebuilt on Next.js
   and `globalassets/...` now 301s to `/data` -- so in practice this route
   returns HTML and falls through. It stays because a redirect is cheap and a
   restored file would be the best source of all.

2. **The publicfiles blob store.** The rebuilt site's data portal is a React app
   that reads `https://publicfiles.ohiosos.gov/election-results/files-index.json`
   -- a machine-readable index of every election file the SoS publishes, back to
   2005, each with a `blobPath` into the same Azure container. The absentee
   workbook for a past election is found by *reading that index*, never by
   guessing a path: Ohio's own folder naming is inconsistent
   ("General Election: November 8, 2022" vs "Primary+Special Election - May 5,
   2026"), so a guessed URL would silently 404 forever. This host is behind the
   same Cloudflare challenge, so it needs the browser-fingerprint transport in
   `download()` -- see that function for what happens without it.

3. **The Power BI absentee dashboard.** Since the November 2024 general, each
   county board submits absentee and early-voting counts to the SoS daily under
   R.C. 3509.05(C)(4)(a), and the SoS publishes them through a "publish to web"
   Power BI report. Its anonymous query API is on `analysis.usgovcloudapi.net`,
   **not** behind Cloudflare, and answers a plain `requests` POST. That makes it
   the only route that works from a stock GitHub Actions runner today, and the
   only route with data *during* the early-vote period -- routes 1 and 2 carry
   one post-election snapshot per election, not a daily series.

WHAT THE TWO SHAPES OF SOURCE DO AND DO NOT AGREE ON
----------------------------------------------------
They are different measurements of the same election and will not match. For the
2024 general the certified workbook reports 2,620,750 absentee ballots cast; the
dashboard's cumulative feed reports 2,262,963, because county submissions stop
before the last in-person days and the late-arriving mail are counted. Both are
honest; the workbook is the restatement. Routes are ordered so that once the
workbook exists it wins.

THE WORKBOOK PARSER
-------------------
Ohio publishes one workbook per election, refreshed through the early-vote period
and then restated as the official post-election report. It holds one sheet whose
TITLE is the as-of date ("11.5.2024 Absentee Report"), one row per county, and a
"Statewide" row that Ohio computes itself -- we use Ohio's own statewide row
rather than summing counties, so our headline can never disagree with the
Secretary of State's.

Two things about this file drive the parser's shape:

* **The column set grows between cycles.** The 2022 general report has 11
  columns; the 2024 general report has 17, because Ohio added a dropbox / personal
  delivery / by-mail breakdown of returned ballots. The columns we need are named
  identically in both, so everything is matched BY HEADER NAME and a missing name
  is SchemaDrift. Matching by position would have silently shifted every count by
  six columns in 2024.
* **Domestic and UOCAVA are separate column families.** Military and overseas
  ballots are reported in their own set of columns; a parser that reads only the
  "Domestic ..." columns understates returns. Each measure is the sum of its
  domestic and its UOCAVA column.

Ohio does not register voters by party -- party affiliation exists only as a
record of which primary ballot you last requested, and it appears nowhere in
either source -- so every party_* field is None. The dashboard does carry a
`Voter_Party_Bucketed` column derived from primary participation; it is
deliberately not read, because publishing it as party registration would invent a
fact about Ohio that does not exist. See THE BLANK RULE in schema.py.

THE LEDGER BEHIND THE DASHBOARD (2024 daily series, backfill only)
------------------------------------------------------------------
The dashboard table `absentee v_detailed_grouped` is not a snapshot: it is a
per-event LEDGER. Every row is "this many ballots were sent / received in this
county on this `Data_Date`" (epoch-ms UTC midnight), a column the live route
never selects because it only wants the cumulative total. Grouping by county x
`Data_Date` and running a cumulative sum through each day D gives the county's
position "as of D" -- a whole daily curve from one 130 KB request, which is what
`fetch_history` now does for 2024. Measures: `BALLOTS_RECEIVED_NO_EIP` is mail
returned, `BALLOTS_RECEIVED_INCL_EIP - NO_EIP` is early in-person, and
`BALLOTS_SENT_NO_EIP` is mail requested (an in-person ballot is "sent" and
"received" in the same act, so the INCL_EIP sent column is not a request count).

Three things about that ledger decide the shape of `parse_ledger`:

* **It is RESTATED, not what the dashboard displayed on the day.** Rows are
  dated by when the ballot was sent or received, and the counties post their
  submissions days late and correct them afterwards. So "as of Oct 15" here is
  what Ohio NOW says had happened by Oct 15 -- a receipt-dated curve -- not the
  number the dashboard showed on Oct 15, which was smaller. That is the right
  series to compare a live cycle against (the live tracker's lag is the thing
  being measured), but it must not be described as "what was published".

* **The report filters it and so must we.** Unfiltered, the table carries rows
  dated 1984, 2027, 3162 and 9204 -- about 5,000 ballots on dates that cannot
  exist -- which the published report hides behind two page-level booleans,
  `Election Ballot Return Date Range` and `VALID_DATE_BOOL_AGG`. The ledger
  query replicates both as `Where` conditions, which for the 2024 general keeps
  exactly 2024-09-20 (UOCAVA mailing) through 2024-11-06 and reproduces the
  report's own totals to the ballot. A second, generous window check in
  `parse_ledger` is a backstop: anything the server-side filters let through
  that is nowhere near the cycle is dropped and logged with its ballot count,
  never summed.

* **Election Day stays the certified workbook's row.** The ledger's final
  (2,616,339 received through 11-09) and the certified workbook (2,620,750)
  differ by a few thousand ballots, and the workbook is the official
  restatement that the comparison lines anchor on. So `parse_ledger` emits only
  days BEFORE Election Day (`days_to_election >= 1`); the Election-Day row
  comes from the workbook route exactly as it did before, and the two never
  collide on a key.

The ledger's earliest election is the 2023 November general, so 2024 is the
first cycle it can serve (`LEDGER_FIRST_CYCLE`); 2022 is refused before any
query is sent, and the table's own labels are checked again on the way in.

THE 2022 WEEKLY PRESS-RELEASE FILES (backfill only)
--------------------------------------------------
Before the dashboard existed, the SoS press office attached a county workbook
to its weekly early-vote release. Four survive for the 2022 general -- one on
Constant Contact (live) and three on `ohiosos.gov` reachable only through the
Wayback Machine, because that host is behind Cloudflare (see the top of this
file) and web.archive.org is not. They are pinned by URL in `WEEKLY_RELEASES`
and dated by the RELEASE date, which each file's origin `Last-Modified` (replayed
by the Archive as `x-archive-orig-last-modified`) must agree with to within a
day, or the capture is not the file the release linked and is SchemaDrift.

They come in three column shapes, each matched BY HEADER NAME:

* **requests** (Oct 12): `UOCAVA Valid Applications` + `Non-UOCAVA Valid
  Applications` -> mail requested. The file's third column is UOCAVA returns
  only and is NOT read as mail returned. Nothing here fits a county row (the
  county table has no requested column), so this release is a statewide row.
* **requests+eip** (Oct 25): `Early Voting and Absentee Ballot Requests` and
  `Early Voting`. Requests INCLUDE in-person -- Ohio counts an early in-person
  ballot as an application and a cast in one act -- so mail requested is
  requests minus EIP, which is also what the certified workbook's "transmitted
  by mail" column and the 2024 ledger's `SENT_NO_EIP` both mean. Leaving EIP in
  would have the series fall by half a million on Election Day.
* **full** (Nov 1, Nov 7): `Total Ballots Requested`, `Ballots Cast Early In
  Person`, `Ballots Returned and Submitted for Counting`. The sheet carries a
  second block with the same three headers to the right -- the 2018
  comparison -- so the FIRST occurrence of each header is the 2022 block, and
  the county column is the unlabelled first one. Mail returned is returned
  minus EIP.

Every file has a `TOTAL` row, which is used as the statewide row (Ohio's own
number, as with the workbook) and checked against the county sum.
"""

from __future__ import annotations

import io
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import openpyxl

from ..calendar import election_date
from ..schema import TIER_SCRAPER, CountyDay, StateDay
from . import _fips
from ._net import (
    DEFAULT_HEADERS, DEFAULT_TIMEOUT, SESSION, Missing, _throttle, cache_path, get,
    impersonated_headers, looks_like_wall, looks_like_xlsx,
)
from .base import Adapter, FetchResult, NotYetPublished, SchemaDrift, SourceError

log = logging.getLogger(__name__)

#: Every Ohio county, per the census list. Used to decide whether a dashboard
#: pull covers the whole state and may therefore be summed into a StateDay.
EXPECTED_COUNTIES = len(_fips.CENSUS_COUNTIES["OH"])

# --------------------------------------------------------------------------
# Route 1: the legacy globalassets workbook
# --------------------------------------------------------------------------
#: The report used to live under the SoS's per-election asset folder. `{cycle}`
#: is the four-digit year and `gen` is the general election. Retired in the 2026
#: site rebuild -- verified 2026-09-06 that `globalassets/...` now 301s to
#: `/data` -- but kept as the first thing tried, because it is one request and a
#: restored file would be authoritative.
BASE = "https://www.ohiosos.gov/globalassets/elections/{cycle}/gen/absentee/"

#: Ohio renames the file as the election passes: the live pre-election report,
#: then the certified one. We try them newest-name-first and take the first that
#: exists, because during the EV period only the first of these is posted.
FILENAMES = (
    "{cycle}gen_absentee_report_web.xlsx",
    "{cycle}gen_official_absentee_report_web.xlsx",
)

# --------------------------------------------------------------------------
# Route 2: the publicfiles blob store
# --------------------------------------------------------------------------
#: The index the SoS data portal's own React bundle fetches (it is the
#: `prod` entry of the bundle's environment map, so it is load-bearing rather
#: than a guess). VERIFIED live 2026-09-06: 200, 363 KB of JSON.
PUBLICFILES_BASE = "https://publicfiles.ohiosos.gov/election-results/"
PUBLICFILES_INDEX = PUBLICFILES_BASE + "files-index.json"

#: The index labels the absentee workbook differently across cycles ("Absentee
#: Supplemental Report", "Absentee Ballot Report"), so entries are matched on the
#: word that is stable, and a file whose name is not a spreadsheet is refused.
_ABSENTEE_LABEL = re.compile(r"\babsentee\b", re.I)
_NOT_ABSENTEE_LABEL = re.compile(r"\bprovisional\b|\bamended by\b", re.I)

#: A November general, as the index spells it: "General Election: November 5,
#: 2024". Primaries, specials and congressional-district specials must not match.
_GENERAL_ELECTION = re.compile(r"^general election\b.*\bnovember\b", re.I)

# --------------------------------------------------------------------------
# Route 3: the Power BI absentee dashboard
# --------------------------------------------------------------------------
#: The "Ohio Absentee and Early Voting Data Dashboard" published from the SoS
#: data portal. The resource key is the `k` field of the `?r=` token in the
#: portal bundle's embed URL; it is the whole authentication for a publish-to-web
#: report. The cluster is the `ClusterUri` the embed page itself resolves to.
#: All five constants VERIFIED live 2026-09-06 (see docs/ohio-source.md for how
#: to rediscover them if the SoS republishes the report).
POWERBI_QUERY_URL = (
    "https://wabi-us-gov-iowa-api.analysis.usgovcloudapi.net"
    "/public/reports/querydata?synchronous=true"
)
POWERBI_RESOURCE_KEY = "77b7dd4e-7c38-4f79-9992-fdb5502cd2e9"
POWERBI_DATASET_ID = "0e7f070f-deff-4aad-8dba-bb0efd13fc0c"
POWERBI_MODEL_ID = 826990
POWERBI_REPORT_ID = "1307181"

#: The table behind the dashboard's county map. Its columns, and what they mean:
#:   BALLOTS_*_INCL_EIP  -- mail ballots PLUS early in-person ("EIP")
#:   BALLOTS_*_NO_EIP    -- mail ballots only
#: so early in-person is the difference, and (verified against the live data for
#: the 2024 general) EIP sent and EIP received are identical by construction --
#: an in-person ballot is issued and cast in the same act.
POWERBI_ENTITY = "absentee v_detailed_grouped"
COL_ELECTION = "Election_Description"
COL_COUNTY = "County_Name"
COL_REFRESHED = "REFRESH_DATE"
MEASURE_SENT_ALL = "BALLOTS_SENT_INCL_EIP"
MEASURE_BACK_ALL = "BALLOTS_RECEIVED_INCL_EIP"
MEASURE_SENT_MAIL = "BALLOTS_SENT_NO_EIP"
MEASURE_BACK_MAIL = "BALLOTS_RECEIVED_NO_EIP"

POWERBI_COLUMNS = (COL_ELECTION, COL_COUNTY, COL_REFRESHED)
POWERBI_MEASURES = (
    MEASURE_SENT_ALL, MEASURE_BACK_ALL, MEASURE_SENT_MAIL, MEASURE_BACK_MAIL,
)

#: How the dashboard labels a November general: "2024 NOV GENERAL". Anchored at
#: both ends so "2024 JUNE CD 6 SPECIAL GENERAL" cannot match.
_DASHBOARD_GENERAL = re.compile(r"^(\d{4})\s+NOV\w*\s+GENERAL$", re.I)

# --------------------------------------------------------------------------
# Route 3, the ledger behind it (backfill only -- see the module docstring)
# --------------------------------------------------------------------------
#: The day a ledger row's ballots were sent or received: epoch milliseconds at
#: UTC midnight, like REFRESH_DATE. Never selected by the live route.
COL_DATA_DATE = "Data_Date"

#: The published report's two page-level filters, booleans on the same table.
#: Replicated as `Where` conditions so the ledger query answers exactly what
#: the report displays -- verified for the 2024 general: 2024-09-20..2024-11-06
#: and the report's own four totals to the ballot. Without them the table
#: carries ~5,000 ballots on dates like 1984-10-21, 3162-10-21 and 9204-01-02.
FLAG_RETURN_RANGE = "Election Ballot Return Date Range"
FLAG_VALID_DATE = "VALID_DATE_BOOL_AGG"

LEDGER_COLUMNS = (COL_ELECTION, COL_COUNTY, COL_DATA_DATE)

#: The table's earliest election is the 2023 November general, so this is the
#: first November general it can give a daily series for. Earlier cycles are
#: refused before any query is sent.
LEDGER_FIRST_CYCLE = 2024

#: The backstop window around Election Day a ledger row must fall in. Generous
#: on purpose -- the report's own filters (above) are the real clamp, at ~46
#: days before through the day after; this only has to catch the impossible
#: dates if those filters ever stop being applied server-side.
LEDGER_WINDOW_BEFORE = 60
LEDGER_WINDOW_AFTER = 10

# --------------------------------------------------------------------------
# The 2022 weekly press-release files (backfill only -- see the module docstring)
# --------------------------------------------------------------------------
#: `Last-Modified` as an origin sends it, and as the Wayback Machine replays the
#: ORIGINAL response's copy of it. The same date under two names, which is what
#: lets an archived capture be dated by the file's own write time rather than by
#: when the Archive happened to crawl it. (ca.py's `fetch_history` is the
#: precedent.)
LIVE_STAMP_HEADER = "Last-Modified"
ARCHIVE_STAMP_HEADER = "x-archive-orig-last-modified"

#: `id_` asks for the ORIGINAL bytes rather than a rewritten page; essential for
#: a workbook, which any rewriting destroys.
WAYBACK_SNAPSHOT = "https://web.archive.org/web/{stamp}id_/{url}"

#: web.archive.org answers a burst of requests with HTTP 429, so archived
#: files are spaced out and a 429 is waited through rather than reported.
#: These files never change, so each is fetched once and cached.
#:
#: ⚠️ A 429 THAT NEVER CLEARS IS NOT A BURST. Measured 2026-10-13: the Archive
#: answered the Chrome `User-Agent` in DEFAULT_HEADERS with a 429 "flagged as
#: suspected abusive bot traffic" for minutes on end -- through waits totalling
#: 2.5 minutes -- while the same URL with no spoofed UA answered 200 at once.
#: It is the browser UA on a python-requests TLS handshake that trips it, the
#: contradiction `_net.impersonated_headers` exists to remove, so archived
#: requests go out without it. The waits below are for a genuine burst.
ARCHIVE_MIN_INTERVAL = 2.0
ARCHIVE_RETRY_WAITS = (15, 45)

#: A release's workbook may be dated this many days off its release date before
#: the capture is treated as some other file. Oct 25's archived copy was written
#: Oct 26; Nov 7's "through 2 pm" file was written after midnight UTC on Nov 8.
RELEASE_DATE_TOLERANCE = timedelta(days=1)

#: The three column shapes, by the header names that identify each. Keys are
#: whitespace-collapsed and lowercased like the workbook's.
WEEKLY_REQUESTS = "requests"
WEEKLY_REQUESTS_EIP = "requests+eip"
WEEKLY_FULL = "full"

WEEKLY_COUNTY = "county"
WEEKLY_SHAPES: dict[str, dict[str, str]] = {
    WEEKLY_REQUESTS: {
        "uocava": "uocava valid applications",
        "domestic": "non-uocava valid applications",
    },
    WEEKLY_REQUESTS_EIP: {
        "requests": "early voting and absentee ballot requests",
        "eip": "early voting",
    },
    WEEKLY_FULL: {
        "requests": "total ballots requested",
        "eip": "ballots cast early in person",
        "returned": "ballots returned and submitted for counting",
    },
}

#: How far down a sheet the header row may sit. The "full" files open with three
#: blank rows under a merged title.
WEEKLY_HEADER_SCAN = 10


@dataclass(frozen=True)
class WeeklyRelease:
    """One press-release workbook: where it is, what day it reports, its shape."""

    as_of: date
    url: str
    filename: str
    kind: str
    stamp_header: str = ARCHIVE_STAMP_HEADER


def _archived(stamp: str, url: str) -> str:
    return WAYBACK_SNAPSHOT.format(stamp=stamp, url=url)


_SOS_NEWS_2022 = "https://www.ohiosos.gov/globalassets/media-center/news/2022/"

#: The dated county files, per cycle. Each `as_of` is the press-release date;
#: the file's own write date must agree with it (see `check_release_date`).
#: VERIFIED 2026-10-13: the Constant Contact file answers live with
#: `Last-Modified: Wed, 12 Oct 2022 19:48:14 GMT`; the three Wayback captures
#: replay origin write times of 2022-10-26 11:01, 2022-11-01 21:41 and
#: 2022-11-08 00:26 UTC.
WEEKLY_RELEASES: dict[int, tuple[WeeklyRelease, ...]] = {
    2022: (
        WeeklyRelease(
            as_of=date(2022, 10, 12),
            url=("https://files.constantcontact.com/b01249ec501/"
                 "cdcfd945-3f2d-41e6-a238-b876909fa767.xlsx?rdr=true"),
            filename="2022_weekly_2022-10-12_absentee_survey_1.xlsx",
            kind=WEEKLY_REQUESTS,
            stamp_header=LIVE_STAMP_HEADER,
        ),
        WeeklyRelease(
            as_of=date(2022, 10, 25),
            url=_archived("20221026190624", _SOS_NEWS_2022
                          + "2022-10-25/nov-2022-general-absentee-survey-3.xlsx"),
            filename="2022_weekly_2022-10-25_nov-2022-general-absentee-survey-3.xlsx",
            kind=WEEKLY_REQUESTS_EIP,
        ),
        WeeklyRelease(
            as_of=date(2022, 11, 1),
            url=_archived("20250307224416", _SOS_NEWS_2022
                          + "2022-11-01/oneweekoutev2022.xlsx"),
            filename="2022_weekly_2022-11-01_oneweekoutev2022.xlsx",
            kind=WEEKLY_FULL,
        ),
        WeeklyRelease(
            # Reported "through 2 pm" on the 7th; dated the 7th, not by the
            # after-midnight-UTC write time.
            as_of=date(2022, 11, 7),
            url=_archived("20250306112939", _SOS_NEWS_2022
                          + "2022-11-07/onedayoutev2022.xlsx"),
            filename="2022_weekly_2022-11-07_onedayoutev2022.xlsx",
            kind=WEEKLY_FULL,
        ),
    ),
}

# --------------------------------------------------------------------------
# Workbook column vocabulary
# --------------------------------------------------------------------------
#: Header text -> the field it feeds. Keys are whitespace-collapsed and
#: lowercased; the raw file has a double space in the "cast" header and trailing
#: spaces on several UOCAVA ones.
COUNTY_COLUMN = "county"

TOTAL_CAST = (
    "total number of absentee ballots cast (including domestic, military & "
    "overseas; by mail & in person)"
)
DOMESTIC_SENT = "domestic absentee ballots transmitted to voters by mail"
DOMESTIC_MAIL = "domestic absentee ballots cast by mail (or dropped off at boes)"
DOMESTIC_INPERSON = "domestic absentee ballots requested & cast in person"
UOCAVA_SENT = "uocava ballots transmitted to voters by mail, fax or email"
UOCAVA_MAIL = "uocava ballots cast by mail (or dropped off at boes)"
UOCAVA_INPERSON = "uocava ballots requested & cast in person"

REQUIRED = (
    COUNTY_COLUMN, TOTAL_CAST,
    DOMESTIC_SENT, DOMESTIC_MAIL, DOMESTIC_INPERSON,
    UOCAVA_SENT, UOCAVA_MAIL, UOCAVA_INPERSON,
)

STATEWIDE_LABELS = {"statewide", "state wide", "total", "totals", "state total"}

_SHEET_DATE = re.compile(r"(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{2,4})")


# --------------------------------------------------------------------------
# Transport
# --------------------------------------------------------------------------
def _browser_session():
    """A curl_cffi session that presents a real Chrome TLS/HTTP2 fingerprint.

    `None` when curl_cffi is not installed. It is NOT a declared dependency of
    this project, so on a stock runner this returns None and routes 1 and 2 --
    both of which live behind Cloudflare -- degrade to SourceError while route 3
    (which is not behind Cloudflare) carries Ohio on its own.

    Adding `curl_cffi` to pyproject.toml would unlock the workbook routes; that
    file is not this module's to edit, so the import stays optional and the
    absence is logged once per call rather than being fatal.
    """
    try:
        from curl_cffi import requests as curl_requests  # noqa: PLC0415
    except ImportError:
        return None
    return curl_requests.Session(impersonate="chrome")


def download(
    url: str, *, filename: str, use_cache: bool = False, min_bytes: int = 64
) -> bytes:
    """GET `url`, preferring a browser TLS fingerprint, and mirror it to cache/.

    This is the seam every workbook route goes through, and the only reason it
    exists rather than calling `_net.get` directly: `ohiosos.gov` and
    `publicfiles.ohiosos.gov` refuse python-requests' TLS handshake with a
    Cloudflare managed challenge (HTTP 403) no matter what headers are sent.
    Raises the same `Missing` / `SourceError` vocabulary `_net.get` does, so
    callers cannot tell which transport answered.
    """
    session = _browser_session()
    if session is None:
        log.debug("OH: curl_cffi unavailable; %s will likely be challenged", url)
        return get(
            url, state="OH", filename=filename, use_cache=use_cache, min_bytes=min_bytes
        )

    path = cache_path("OH", filename)
    if use_cache and path.exists() and path.stat().st_size >= min_bytes:
        return path.read_bytes()

    # Cloudflare scores each request, so even a good fingerprint is challenged
    # occasionally -- observed once in a run of five identical requests for the
    # 2024 workbook, where the immediate retry returned 200. One retry, because
    # a persistent 403 means the rule changed and hammering will not fix it.
    response = None
    for attempt in (1, 2):
        try:
            response = session.get(url, headers=DEFAULT_HEADERS, timeout=90)
        except Exception as exc:  # noqa: BLE001 -- curl_cffi has its own hierarchy
            raise SourceError(f"OH: GET {url} failed: {exc}") from exc
        if response.status_code != 403 or attempt == 2:
            break
        log.debug("OH: %s was challenged; retrying once", url)
        session = _browser_session() or session

    if response.status_code in (404, 410):
        raise Missing(f"OH: {url} returned {response.status_code}")
    if response.status_code != 200:
        raise SourceError(f"OH: {url} returned HTTP {response.status_code}")

    body = response.content
    if len(body) < min_bytes:
        raise Missing(f"OH: {url} returned only {len(body)} bytes")
    path.write_bytes(body)
    return body


# --------------------------------------------------------------------------
# Shared helpers
# --------------------------------------------------------------------------
def _norm_header(raw) -> str:
    return " ".join(str(raw or "").strip().lower().split())


def _int(value) -> int | None:
    """A count, or None when the cell is blank.

    Blank is "Ohio did not report this", which is not zero -- an empty UOCAVA
    in-person cell in a small county means the county left it off, and a 0 would
    claim they affirmatively counted none.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip().replace(",", "")
    if not text or text in {"-", "--", "N/A", "n/a"}:
        return None
    try:
        return int(float(text))
    except ValueError as exc:
        raise SchemaDrift(f"OH: {value!r} is not a count") from exc


def _add(*values: int | None) -> int | None:
    """Sum the parts of a measure, or None if no part was reported at all.

    None + 5 is 5 here, not None: an unreported UOCAVA column must not wipe out a
    reported domestic one. Only an entirely unreported measure stays blank.
    """
    known = [v for v in values if v is not None]
    return sum(known) if known else None


def _sub(whole: int | None, part: int | None, what: str | None = None) -> int | None:
    """`whole - part`, or None if either side is unreported.

    A negative answer means the two columns do not mean what this module thinks
    they mean, which is drift, not a number worth publishing. `what` names the
    pair for the message; the default is the dashboard's.
    """
    if whole is None or part is None:
        return None
    if whole < part:
        raise SchemaDrift(
            what.format(whole=whole, part=part) if what else
            f"OH: dashboard reports {part} mail ballots inside {whole} total; "
            "the INCL_EIP / NO_EIP columns no longer nest"
        )
    return whole - part


def sheet_date(title: str, cycle: int) -> date:
    """The as-of date Ohio writes into the sheet title ("11.5.2024 Absentee Report")."""
    m = _SHEET_DATE.search(title or "")
    if not m:
        raise SchemaDrift(f"OH: sheet title {title!r} carries no report date")
    month, day, year = (int(g) for g in m.groups())
    if year < 100:
        year += 2000
    if year != int(cycle):
        raise SchemaDrift(
            f"OH: sheet title {title!r} is from {year}, not the {cycle} cycle"
        )
    try:
        return date(year, month, day)
    except ValueError as exc:
        raise SchemaDrift(f"OH: sheet title {title!r} is not a date") from exc


# --------------------------------------------------------------------------
# Route 1 & 2: the workbook parser
# --------------------------------------------------------------------------
def parse(body: bytes, cycle: int) -> FetchResult:
    """Parse one Ohio absentee workbook into canonical rows."""
    if not looks_like_xlsx(body):
        raise SourceError("OH: absentee report was not an xlsx")
    try:
        book = openpyxl.load_workbook(io.BytesIO(body), read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001 -- openpyxl raises a zoo of types
        raise SourceError(f"OH: could not open absentee workbook: {exc}") from exc

    sheet = book.worksheets[0]
    day = sheet_date(sheet.title, cycle)

    rows = sheet.iter_rows(values_only=True)
    try:
        header = [_norm_header(c) for c in next(rows)]
    except StopIteration as exc:
        raise SchemaDrift("OH: absentee report is empty") from exc

    index = {name: i for i, name in enumerate(header) if name}
    missing = [c for c in REQUIRED if c not in index]
    if missing:
        raise SchemaDrift(f"OH: absentee report is missing columns {missing}")

    def cell(row, column):
        i = index[column]
        return _int(row[i]) if i < len(row) else None

    state_rows: list[StateDay] = []
    county_rows: list[CountyDay] = []
    unknown: list[str] = []

    for row in rows:
        if not row:
            continue
        raw_name = row[index[COUNTY_COLUMN]] if index[COUNTY_COLUMN] < len(row) else None
        name = " ".join(str(raw_name or "").split())
        if not name:
            continue

        total = cell(row, TOTAL_CAST)
        mail_sent = _add(cell(row, DOMESTIC_SENT), cell(row, UOCAVA_SENT))
        mail_back = _add(cell(row, DOMESTIC_MAIL), cell(row, UOCAVA_MAIL))
        in_person = _add(cell(row, DOMESTIC_INPERSON), cell(row, UOCAVA_INPERSON))

        if name.lower() in STATEWIDE_LABELS:
            # Ohio's own statewide row, not a sum of ours -- see module docstring.
            state_rows.append(StateDay(
                cycle=cycle, state="OH", day=day,
                ballots_total=total,
                mail_requested=mail_sent,
                mail_returned=mail_back,
                inperson=in_person,
                # Ohio has no party registration. Never 0.
                party_dem=None, party_rep=None, party_oth=None, party_npa=None,
            ))
            continue

        hit = _fips.lookup("OH", name)
        if hit is None:
            unknown.append(name)
            continue
        fips, canonical = hit
        county_rows.append(CountyDay(
            cycle=cycle, state="OH", county_fips=fips, day=day,
            county_name=canonical,
            ballots_total=total,
            mail_returned=mail_back,
            inperson=in_person,
            party_dem=None, party_rep=None, party_oth=None, party_npa=None,
        ))

    if unknown:
        # A name we cannot place is a county we would silently drop off the map.
        raise SchemaDrift(f"OH: unrecognised county names {sorted(set(unknown))[:5]}")
    if not county_rows:
        raise SchemaDrift("OH: absentee report produced no county rows")

    return FetchResult(state_rows=state_rows, county_rows=county_rows)


# --------------------------------------------------------------------------
# Route 2: reading the publicfiles index
# --------------------------------------------------------------------------
def find_workbook(index: dict, cycle: int) -> str:
    """The blob path of the `cycle` November general's absentee workbook.

    Raises NotYetPublished when the index carries no such election -- which is
    the ordinary answer for the current cycle, because this index is the *past*
    election archive and Ohio adds the year's general to it after certification.
    """
    if not isinstance(index, dict) or "pastElectionResults" not in index:
        raise SchemaDrift("OH: files-index.json has no pastElectionResults")

    for year in index["pastElectionResults"]:
        if not isinstance(year, dict) or int(year.get("year", -1)) != int(cycle):
            continue
        for election in year.get("elections", []):
            if not _GENERAL_ELECTION.match(str(election.get("type", "")).strip()):
                continue
            for group in election.get("fileGroups", []):
                for entry in group.get("files", []):
                    label = str(entry.get("displayName", ""))
                    blob = str(entry.get("blobPath", ""))
                    if not blob.lower().endswith((".xlsx", ".xls")):
                        continue
                    if _NOT_ABSENTEE_LABEL.search(label):
                        continue
                    if _ABSENTEE_LABEL.search(label) or _ABSENTEE_LABEL.search(blob):
                        return blob
    raise NotYetPublished(
        f"OH: publicfiles index carries no {cycle} November general absentee report"
    )


def _blob_url(blob_path: str) -> str:
    """Percent-encode a blobPath. Ohio's folder names contain spaces, colons and
    commas ("General Election: November 5, 2024"), none of which survive raw."""
    from urllib.parse import quote  # noqa: PLC0415 -- one caller, one line

    return PUBLICFILES_BASE + quote(blob_path)


# --------------------------------------------------------------------------
# Route 3: the Power BI dashboard
# --------------------------------------------------------------------------
def _column(name: str) -> dict:
    return {"Column": {"Expression": {"SourceRef": {"Source": "a"}}, "Property": name}}


def _select(columns, measures) -> list[dict]:
    return [
        {**_column(name), "Name": f"{POWERBI_ENTITY}.{name}"} for name in columns
    ] + [
        {"Aggregation": {"Expression": _column(name), "Function": 0},
         "Name": f"Sum({POWERBI_ENTITY}.{name})"}
        for name in measures
    ]


def _query_body(select: list[dict], where: list[dict] | None = None) -> dict:
    query = {
        "Version": 2,
        "From": [{"Name": "a", "Entity": POWERBI_ENTITY, "Type": 0}],
        "Select": select,
    }
    if where:
        query["Where"] = where
    return {
        "version": "1.0.0",
        "queries": [{
            "Query": {"Commands": [{"SemanticQueryDataShapeCommand": {
                "Query": query,
                "Binding": {
                    "Primary": {"Groupings": [{"Projections": list(range(len(select)))}]},
                    "DataReduction": {
                        "DataVolume": 4, "Primary": {"Window": {"Count": 30000}},
                    },
                    "Version": 1,
                },
                "ExecutionMetricsKind": 1,
            }}]},
            "QueryId": "",
            "ApplicationContext": {
                "DatasetId": POWERBI_DATASET_ID,
                "Sources": [{"ReportId": POWERBI_REPORT_ID}],
            },
        }],
        "cancelQueries": [],
        "modelId": POWERBI_MODEL_ID,
    }


def build_query() -> dict:
    """The querydata body: every election, every county, one request.

    Selecting all elections rather than filtering server-side keeps the request
    to columns and a Sum -- no filter DSL -- and the whole answer is 40 KB. The
    cycle is picked out locally, where a mismatch is visible and testable.
    """
    return _query_body(_select(POWERBI_COLUMNS, POWERBI_MEASURES))


def build_ledger_query(label: str) -> dict:
    """The ledger body: one election, every county, every `Data_Date`.

    Unlike `build_query` this DOES filter server-side, for two reasons. The
    election filter keeps the answer to one cycle's ~3,200 rows instead of every
    election's; `label` is the table's own spelling, discovered from the live
    query, never guessed. The two boolean filters are the published report's own
    page filters -- the thing that turns the raw table into what the report
    shows -- and replicating them is what makes "as of D" mean what Ohio means.
    """
    def equals(name: str, literal: str) -> dict:
        return {"Condition": {"Comparison": {
            "ComparisonKind": 0,
            "Left": _column(name),
            "Right": {"Literal": {"Value": literal}},
        }}}

    quoted = "'" + label.replace("'", "''") + "'"
    where = [
        {"Condition": {"In": {
            "Expressions": [_column(COL_ELECTION)],
            "Values": [[{"Literal": {"Value": quoted}}]],
        }}},
        equals(FLAG_RETURN_RANGE, "true"),
        equals(FLAG_VALID_DATE, "true"),
    ]
    return _query_body(_select(LEDGER_COLUMNS, POWERBI_MEASURES), where)


def decode_dsr(payload: dict, required: tuple[str, ...] | None = None) -> list[dict]:
    """Turn one Power BI querydata response into a list of {column: value}.

    The wire format is column-compressed and has to be un-compressed exactly, or
    the numbers come out attached to the wrong counties:

    * `S` on the first row names the slots ("G0" for a grouping, "M0" for a
      measure) and, for dictionary-encoded slots, a `DN` naming an entry in
      `ValueDicts`; the value on the wire is then an index into that list.
    * `C` holds only the slots that changed. `R` is a bitmask of slots that
      repeat the previous row's value, `Ø` a bitmask of slots that are null;
      both are omitted from `C`.

    Slots are matched back to column names through the response's own
    `descriptor.Select`, so a reordered projection cannot silently transpose the
    measures. Anything unrecognised is SchemaDrift.

    `required` is the column set the caller is about to read; the default is the
    live dashboard query's. The ledger query selects `Data_Date` in place of
    `REFRESH_DATE` and passes its own.
    """
    if required is None:
        required = POWERBI_COLUMNS + POWERBI_MEASURES
    try:
        data = payload["results"][0]["result"]["data"]
        descriptor = data["descriptor"]["Select"]
        dataset = data["dsr"]["DS"][0]
        holder = dataset["PH"][0]
    except (KeyError, IndexError, TypeError) as exc:
        raise SchemaDrift(f"OH: dashboard response is not a DSR result: {exc}") from exc

    slot_names: dict[str, str] = {}
    for item in descriptor:
        name = str(item.get("Name", ""))
        slot = item.get("Value")
        if slot is None:
            raise SchemaDrift(f"OH: dashboard column {name!r} has no slot")
        # "absentee v_detailed_grouped.County_Name" / "Sum(... .BALLOTS_SENT_NO_EIP)"
        bare = name.rsplit(".", 1)[-1].rstrip(")")
        slot_names[slot] = bare

    missing = sorted(set(required) - set(slot_names.values()))
    if missing:
        raise SchemaDrift(f"OH: dashboard response is missing columns {missing}")

    rows_key = next((k for k in holder if k.startswith("DM")), None)
    if rows_key is None:
        raise SchemaDrift("OH: dashboard response has no data rows")
    raw_rows = holder[rows_key]
    if not raw_rows or "S" not in raw_rows[0]:
        raise SchemaDrift("OH: dashboard response carries no row schema")

    schema = raw_rows[0]["S"]
    dicts = dataset.get("ValueDicts", {})
    width = len(schema)
    out: list[dict] = []
    previous: list = [None] * width

    for raw in raw_rows:
        values = list(raw.get("C", []))
        repeat = raw.get("R", 0)
        nulls = raw.get("Ø", 0)
        row: list = []
        cursor = 0
        for i in range(width):
            if repeat >> i & 1:
                row.append(previous[i])
                continue
            if nulls >> i & 1:
                row.append(None)
                continue
            if cursor >= len(values):
                raise SchemaDrift("OH: dashboard row is shorter than its schema")
            value = values[cursor]
            cursor += 1
            book = schema[i].get("DN")
            if book is not None and isinstance(value, int):
                try:
                    value = dicts[book][value]
                except (KeyError, IndexError) as exc:
                    raise SchemaDrift(
                        f"OH: dashboard value dictionary {book!r} has no entry {value}"
                    ) from exc
            row.append(value)
        previous = row
        out.append({
            slot_names[schema[i]["N"]]: row[i]
            for i in range(width)
            if schema[i].get("N") in slot_names
        })
    return out


def _epoch_ms_to_date(value) -> date:
    if not isinstance(value, (int, float)):
        raise SchemaDrift(f"OH: dashboard refresh stamp {value!r} is not a timestamp")
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc).date()


def general_label(rows: list[dict], cycle: int) -> str:
    """The table's own spelling of `cycle`'s November general ("2024 NOV GENERAL").

    Read off the rows rather than formatted from the cycle, so that a ledger
    `Where` always carries a label the table actually uses. NotYetPublished when
    the table has no such election -- the ordinary answer for a live cycle before
    the counties start submitting, and the permanent answer for 2022.
    """
    for row in rows:
        m = _DASHBOARD_GENERAL.match(" ".join(str(row.get(COL_ELECTION) or "").split()))
        if m and int(m.group(1)) == int(cycle):
            return row[COL_ELECTION]
    seen = sorted({str(r.get(COL_ELECTION)) for r in rows})
    raise NotYetPublished(
        f"OH: absentee dashboard has no {cycle} November general yet "
        f"(it carries {seen})"
    )


def parse_dashboard(payload: dict, cycle: int) -> FetchResult:
    """Parse the dashboard's cumulative county counts for `cycle`'s general."""
    rows = decode_dsr(payload)
    label = general_label(rows, cycle)

    # One county can appear under several REFRESH_DATE partitions, so the counts
    # are summed and the newest stamp is the snapshot's as-of date.
    totals: dict[str, list[int | None]] = {}
    names: dict[str, str] = {}
    unknown: list[str] = []
    refreshed: date | None = None

    for row in rows:
        if row.get(COL_ELECTION) != label:
            continue
        raw_name = " ".join(str(row.get(COL_COUNTY) or "").split())
        if not raw_name:
            continue
        hit = _fips.lookup("OH", raw_name)
        if hit is None:
            unknown.append(raw_name)
            continue
        fips, canonical = hit
        names[fips] = canonical
        stamp = _epoch_ms_to_date(row.get(COL_REFRESHED))
        refreshed = stamp if refreshed is None or stamp > refreshed else refreshed
        bucket = totals.setdefault(fips, [None, None, None, None])
        for i, measure in enumerate(POWERBI_MEASURES):
            bucket[i] = _add(bucket[i], _int(row.get(measure)))

    if unknown:
        raise SchemaDrift(f"OH: unrecognised county names {sorted(set(unknown))[:5]}")
    if not totals or refreshed is None:
        raise NotYetPublished(f"OH: absentee dashboard has no {cycle} county rows yet")

    county_rows: list[CountyDay] = []
    running: list[int | None] = [None, None, None, None]
    for fips, (sent_all, back_all, sent_mail, back_mail) in sorted(totals.items()):
        county_rows.append(CountyDay(
            cycle=cycle, state="OH", county_fips=fips, day=refreshed,
            county_name=names[fips],
            # Ballots cast = mail returned + early in-person cast.
            ballots_total=back_all,
            mail_returned=back_mail,
            inperson=_sub(back_all, back_mail),
            # Ohio has no party registration. Never 0.
            party_dem=None, party_rep=None, party_oth=None, party_npa=None,
        ))
        for i, value in enumerate((sent_all, back_all, sent_mail, back_mail)):
            running[i] = _add(running[i], value)

    state_rows: list[StateDay] = []
    if len(county_rows) == EXPECTED_COUNTIES:
        sent_all, back_all, sent_mail, back_mail = running
        state_rows.append(StateDay(
            cycle=cycle, state="OH", day=refreshed,
            ballots_total=back_all,
            mail_requested=sent_mail,
            mail_returned=back_mail,
            inperson=_sub(back_all, back_mail),
            party_dem=None, party_rep=None, party_oth=None, party_npa=None,
        ))
    else:
        # PARTIAL COVERAGE. The dashboard has no statewide row of its own, so a
        # StateDay here would be OUR sum -- and a sum of 60 counties looks
        # exactly like a number for Ohio without being one. A hole in the
        # statewide series is recoverable; a plausible wrong total is not.
        log.warning(
            "OH: dashboard covers %d of %d counties; publishing counties only, "
            "no statewide row", len(county_rows), EXPECTED_COUNTIES,
        )

    return FetchResult(state_rows=state_rows, county_rows=county_rows)


def _query_error(payload: dict) -> str | None:
    """The model's own complaint, if the query was rejected under HTTP 200.

    Power BI answers a query it cannot run -- a `Where` on a column that has
    been renamed, say -- with a 200 and an `odata.error` inside the first data
    shape, not with a status code. Left unchecked that reads as an empty table.
    """
    try:
        shapes = payload["results"][0]["result"]["data"]["dsr"]["DataShapes"]
        error = shapes[0].get("odata.error")
    except (KeyError, IndexError, TypeError, AttributeError):
        return None
    return json.dumps(error)[:300] if error else None


def _post_query(body: dict, *, filename: str, what: str, use_cache: bool = False) -> dict:
    """POST one querydata body and return the parsed JSON, mirrored to cache/.

    Uses the shared `_net` session because this host is NOT behind Cloudflare --
    it answers a stock python-requests POST, which is the entire reason Ohio has
    a headless path at all. `use_cache` is for the archived ledger only, never
    for the live dashboard.
    """
    path = cache_path("OH", filename)
    if use_cache and path.exists() and path.stat().st_size >= 1024:
        try:
            cached = json.loads(path.read_bytes())
        except ValueError:
            cached = None
        if cached is not None and _query_error(cached) is None:
            log.debug("OH: cache hit %s", path)
            return cached

    headers = {
        **DEFAULT_HEADERS,
        "X-PowerBI-ResourceKey": POWERBI_RESOURCE_KEY,
        "ActivityId": str(uuid.uuid4()),
        "RequestId": str(uuid.uuid4()),
        "Content-Type": "application/json;charset=UTF-8",
        "Accept": "application/json, text/plain, */*",
        "Origin": "https://app.powerbigov.us",
        "Referer": "https://app.powerbigov.us/",
    }
    try:
        response = SESSION.post(
            POWERBI_QUERY_URL, headers=headers, data=json.dumps(body), timeout=120
        )
    except Exception as exc:  # noqa: BLE001 -- requests raises several types
        raise SourceError(f"OH: {what} query failed: {exc}") from exc
    if response.status_code != 200:
        raise SourceError(
            f"OH: {what} returned HTTP {response.status_code} "
            "(the report's resource key may have been rotated -- see "
            "docs/ohio-source.md)"
        )
    try:
        payload = response.json()
    except ValueError as exc:
        raise SchemaDrift(f"OH: {what} did not return JSON: {exc}") from exc
    error = _query_error(payload)
    if error:
        raise SchemaDrift(f"OH: {what} query was rejected by the model: {error}")
    path.write_bytes(response.content)
    return payload


def fetch_dashboard() -> dict:
    """POST the live querydata request and return the parsed JSON."""
    return _post_query(
        build_query(), filename="powerbi_absentee_querydata.json",
        what="absentee dashboard",
    )


def fetch_ledger(label: str, cycle: int, *, use_cache: bool = False) -> dict:
    """POST the ledger query for one election and return the parsed JSON.

    Cached per cycle: a past cycle's ledger is an archived file in all but
    name, and `backfill` is the only caller.
    """
    return _post_query(
        build_ledger_query(label), filename=f"{cycle}_powerbi_absentee_ledger.json",
        what=f"absentee ledger ({label})", use_cache=use_cache,
    )


def parse_ledger(rows: list[dict], cycle: int) -> FetchResult:
    """The per-event ledger, cumulated into "as of D" rows for every day before
    Election Day. See THE LEDGER BEHIND THE DASHBOARD in the module docstring.

    `rows` are decoded ledger rows (`decode_dsr` with `LEDGER_COLUMNS`), one per
    county x `Data_Date`. Rows for any other election are SchemaDrift -- the
    query's `Where` is supposed to have excluded them, and a filter that stops
    being honoured must not be summed into the wrong cycle. Rows dated outside
    the backstop window, or undated rows that carry ballots, are skipped and
    logged with their ballot count; they are never on the curve.

    A county with no event on or before a day is at zero that day, not blank:
    this is a complete per-event ledger, so the absence of an event IS the
    report. The BLANK RULE is about measures a state does not publish, and Ohio
    publishes all four here for every county.
    """
    voting = election_date(cycle)
    earliest = voting - timedelta(days=LEDGER_WINDOW_BEFORE)
    latest = voting + timedelta(days=LEDGER_WINDOW_AFTER)
    if not rows:
        raise NotYetPublished(
            f"OH: the absentee ledger has no rows for the {cycle} November general"
        )

    events: dict[str, dict[date, list[int]]] = {}
    names: dict[str, str] = {}
    unknown: list[str] = []
    junk_rows = 0
    junk_dates: set[str] = set()
    junk = [0, 0, 0, 0]

    for row in rows:
        label = " ".join(str(row.get(COL_ELECTION) or "").split())
        m = _DASHBOARD_GENERAL.match(label)
        if not m or int(m.group(1)) != int(cycle):
            raise SchemaDrift(
                f"OH: ledger row is for {label!r}, not the {cycle} November "
                "general -- the query's election filter is not being honoured"
            )
        raw_name = " ".join(str(row.get(COL_COUNTY) or "").split())
        if not raw_name:
            continue
        hit = _fips.lookup("OH", raw_name)
        if hit is None:
            unknown.append(raw_name)
            continue
        fips, canonical = hit
        names[fips] = canonical
        measures = [_int(row.get(name)) or 0 for name in POWERBI_MEASURES]

        stamp = row.get(COL_DATA_DATE)
        if stamp is None:
            # The table carries one undated bookkeeping row per county. Empty
            # ones are nothing; one with ballots on it cannot be placed on a day.
            if any(measures):
                junk_rows += 1
                junk_dates.add("undated")
                junk = [a + b for a, b in zip(junk, measures)]
            continue
        day = _epoch_ms_to_date(stamp)
        if not (earliest <= day <= latest):
            junk_rows += 1
            junk_dates.add(day.isoformat())
            junk = [a + b for a, b in zip(junk, measures)]
            continue
        bucket = events.setdefault(fips, {}).setdefault(day, [0, 0, 0, 0])
        for i, value in enumerate(measures):
            bucket[i] += value

    if unknown:
        raise SchemaDrift(f"OH: unrecognised county names {sorted(set(unknown))[:5]}")
    if junk_rows:
        log.warning(
            "OH: %d ledger rows for %s are dated outside %s..%s and were SKIPPED "
            "(%d sent, %d received; dates %s)", junk_rows, cycle,
            earliest.isoformat(), latest.isoformat(), junk[0], junk[1],
            sorted(junk_dates)[:8],
        )
    if not events:
        raise NotYetPublished(
            f"OH: the absentee ledger has no dated county rows for {cycle}"
        )

    days = sorted({day for per_day in events.values() for day in per_day})
    emit = [day for day in days if day < voting]
    if not emit:
        raise NotYetPublished(
            f"OH: every {cycle} ledger day is on or after Election Day "
            f"{voting.isoformat()}, which the certified workbook supplies"
        )

    county_rows: list[CountyDay] = []
    state_totals: dict[date, list[int]] = {day: [0, 0, 0, 0] for day in emit}
    for fips in sorted(events):
        per_day = events[fips]
        running = [0, 0, 0, 0]
        for day in days:
            if day in per_day:
                running = [a + b for a, b in zip(running, per_day[day])]
            if day >= voting:
                # Election Day and after belong to the certified workbook.
                break
            sent_all, back_all, sent_mail, back_mail = running
            county_rows.append(CountyDay(
                cycle=cycle, state="OH", county_fips=fips, day=day,
                county_name=names[fips],
                ballots_total=back_all,
                mail_returned=back_mail,
                inperson=_sub(back_all, back_mail),
                # Ohio has no party registration. Never 0.
                party_dem=None, party_rep=None, party_oth=None, party_npa=None,
            ))
            total = state_totals[day]
            for i, value in enumerate(running):
                total[i] += value

    state_rows: list[StateDay] = []
    if len(events) == EXPECTED_COUNTIES:
        for day in emit:
            sent_all, back_all, sent_mail, back_mail = state_totals[day]
            state_rows.append(StateDay(
                cycle=cycle, state="OH", day=day,
                ballots_total=back_all,
                mail_requested=sent_mail,
                mail_returned=back_mail,
                inperson=_sub(back_all, back_mail),
                party_dem=None, party_rep=None, party_oth=None, party_npa=None,
            ))
    else:
        # Same rule as parse_dashboard: a sum of fewer than 88 counties looks
        # exactly like a number for Ohio without being one.
        log.warning(
            "OH: ledger covers %d of %d counties for %s; publishing counties "
            "only, no statewide rows", len(events), EXPECTED_COUNTIES, cycle,
        )
    return FetchResult(state_rows=state_rows, county_rows=county_rows)


# --------------------------------------------------------------------------
# The 2022 weekly press-release files
# --------------------------------------------------------------------------
def _http_date(raw: str | None) -> date | None:
    """An HTTP-date header as a UTC calendar date, or None if unusable."""
    if not raw:
        return None
    try:
        parsed = parsedate_to_datetime(raw.strip())
    except (TypeError, ValueError, IndexError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).date()


def download_dated(
    url: str, *, filename: str, stamp_header: str, use_cache: bool = False,
    min_interval: float = 0.0, min_bytes: int = 4096,
) -> tuple[bytes, date | None]:
    """GET `url`, returning its bytes and the date in `stamp_header`.

    `_net.get` is not used because it discards response headers, and the write
    date is the only thing that ties an archived file to its release. Neither
    host this serves -- files.constantcontact.com and web.archive.org -- is
    behind Cloudflare, so the plain session is enough; the browser-fingerprint
    `download()` above is for `ohiosos.gov` itself, which these files
    deliberately avoid.

    The header is mirrored beside the cached body so a cache hit is dated the
    same way a fresh fetch is. web.archive.org's 429s are waited through.
    """
    path = cache_path("OH", filename)
    stamp = path.with_name(path.name + ".last-modified")
    if use_cache and path.exists() and path.stat().st_size >= min_bytes:
        cached = path.read_bytes()
        if not looks_like_wall(cached):
            log.debug("OH: cache hit %s", path)
            return cached, _http_date(stamp.read_text() if stamp.exists() else None)

    # The Archive flags a browser User-Agent on a non-browser handshake as a
    # bot and 429s it indefinitely (see ARCHIVE_RETRY_WAITS); Constant Contact
    # does not care either way. So no spoofed UA on the archived host.
    headers = (impersonated_headers(DEFAULT_HEADERS)
               if url.startswith("https://web.archive.org/") else DEFAULT_HEADERS)

    def attempt():
        _throttle(url, min_interval)
        try:
            return SESSION.get(url, headers=headers, timeout=DEFAULT_TIMEOUT)
        except Exception as exc:  # noqa: BLE001 -- requests raises several types
            raise SourceError(f"OH: GET {url} failed: {exc}") from exc

    response = attempt()
    for wait in ARCHIVE_RETRY_WAITS:
        if response.status_code != 429:
            break
        log.debug("OH: %s answered 429; waiting %ss", url, wait)
        time.sleep(wait)
        response = attempt()

    if response.status_code in (404, 410):
        raise Missing(f"OH: {url} returned {response.status_code}")
    if response.status_code != 200:
        raise SourceError(f"OH: {url} returned HTTP {response.status_code}")
    body = response.content
    if looks_like_wall(body):
        raise SourceError(f"OH: {url} answered with a bot wall, not the file")
    if len(body) < min_bytes:
        raise Missing(f"OH: {url} returned only {len(body)} bytes")

    path.write_bytes(body)
    raw = response.headers.get(stamp_header)
    if raw:
        stamp.write_text(raw)
    elif stamp.exists():
        stamp.unlink()
    return body, _http_date(raw)


def check_release_date(written: date | None, release: WeeklyRelease) -> None:
    """Refuse a file whose own write date does not agree with its release date.

    The row is dated by the press release, so the file must be the one the
    release linked. A capture written days away from the release is some other
    version of the page's attachment -- or the Archive's own redirect target --
    and would put real numbers on the wrong day.
    """
    if written is None:
        raise SchemaDrift(
            f"OH: {release.url} carries no {release.stamp_header}; an undated "
            f"file cannot be pinned to the {release.as_of.isoformat()} release"
        )
    if abs(written - release.as_of) > RELEASE_DATE_TOLERANCE:
        raise SchemaDrift(
            f"OH: {release.url} was written {written.isoformat()}, not within a "
            f"day of its {release.as_of.isoformat()} release -- this is not the "
            "file the release linked"
        )


def parse_weekly_xlsx(body: bytes, as_of: date, kind: str) -> FetchResult:
    """Parse one 2022 press-release county workbook into rows dated `as_of`.

    `kind` is one of WEEKLY_SHAPES and says which headers must be present; the
    header row is found by those names within the first WEEKLY_HEADER_SCAN rows,
    and the FIRST occurrence of each name is the one read (the "full" files
    repeat the block to the right for the 2018 comparison). The `TOTAL` row is
    the statewide row and must equal the county sum.
    """
    if kind not in WEEKLY_SHAPES:
        raise ValueError(f"unknown weekly shape {kind!r}")
    wanted = WEEKLY_SHAPES[kind]
    if not looks_like_xlsx(body):
        raise SourceError(f"OH: {as_of.isoformat()} weekly file was not an xlsx")
    try:
        book = openpyxl.load_workbook(io.BytesIO(body), read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001 -- openpyxl raises a zoo of types
        raise SourceError(f"OH: could not open {as_of.isoformat()} weekly file: {exc}") from exc
    rows = list(book.worksheets[0].iter_rows(values_only=True))

    header_at = None
    for i, row in enumerate(rows[:WEEKLY_HEADER_SCAN]):
        names = [_norm_header(c) for c in row]
        if all(name in names for name in wanted.values()):
            header_at = i
            break
    if header_at is None:
        raise SchemaDrift(
            f"OH: {as_of.isoformat()} weekly file has no header row carrying "
            f"{sorted(wanted.values())} in its first {WEEKLY_HEADER_SCAN} rows "
            f"(expected the {kind!r} shape)"
        )
    names = [_norm_header(c) for c in rows[header_at]]
    index = {key: names.index(name) for key, name in wanted.items()}
    county_at = names.index(WEEKLY_COUNTY) if WEEKLY_COUNTY in names else 0
    if names[county_at] not in ("", WEEKLY_COUNTY):
        raise SchemaDrift(
            f"OH: {as_of.isoformat()} weekly file's first column is "
            f"{names[county_at]!r}, not the county"
        )

    def cell(row, key):
        i = index[key]
        return _int(row[i]) if i < len(row) else None

    total_raw: dict[str, int | None] | None = None
    county_raw: list[tuple[str, str, dict[str, int | None]]] = []
    unknown: list[str] = []
    for row in rows[header_at + 1:]:
        if not row:
            continue
        raw_name = row[county_at] if county_at < len(row) else None
        name = " ".join(str(raw_name or "").split())
        if not name:
            continue
        values = {key: cell(row, key) for key in wanted}
        if name.lower() in STATEWIDE_LABELS:
            if total_raw is not None:
                raise SchemaDrift(f"OH: {as_of.isoformat()} weekly file has two TOTAL rows")
            total_raw = values
            continue
        hit = _fips.lookup("OH", name)
        if hit is None:
            unknown.append(name)
            continue
        fips, canonical = hit
        county_raw.append((fips, canonical, values))

    if unknown:
        raise SchemaDrift(f"OH: unrecognised county names {sorted(set(unknown))[:5]}")
    if total_raw is None:
        raise SchemaDrift(f"OH: {as_of.isoformat()} weekly file has no TOTAL row")
    if not county_raw:
        raise SchemaDrift(f"OH: {as_of.isoformat()} weekly file has no county rows")
    for key in wanted:
        summed = _add(*(values[key] for _, _, values in county_raw))
        if summed != total_raw[key]:
            raise SchemaDrift(
                f"OH: {as_of.isoformat()} weekly file's counties sum to {summed} "
                f"{wanted[key]!r} against a TOTAL of {total_raw[key]}"
            )

    def measures(values: dict[str, int | None]) -> tuple:
        """(mail_requested, mail_returned, inperson, ballots_total)."""
        if kind == WEEKLY_REQUESTS:
            return _add(values["uocava"], values["domestic"]), None, None, None
        eip = values["eip"]
        requested = _sub(
            values["requests"], eip,
            "OH: weekly file reports {part} early in-person inside {whole} "
            "requests; requests no longer include in-person",
        )
        if kind == WEEKLY_REQUESTS_EIP:
            return requested, None, eip, None
        returned = values["returned"]
        mail_back = _sub(
            returned, eip,
            "OH: weekly file reports {part} early in-person inside {whole} "
            "returned; returns no longer include in-person",
        )
        return requested, mail_back, eip, returned

    requested, mail_back, eip, total = measures(total_raw)
    state_rows = [StateDay(
        cycle=as_of.year, state="OH", day=as_of,
        ballots_total=total,
        mail_requested=requested,
        mail_returned=mail_back,
        inperson=eip,
        # Ohio has no party registration. Never 0.
        party_dem=None, party_rep=None, party_oth=None, party_npa=None,
    )]
    county_rows: list[CountyDay] = []
    if kind != WEEKLY_REQUESTS:
        # The requests-only file has nothing a county row can carry.
        for fips, canonical, values in county_raw:
            _, mail_back, eip, total = measures(values)
            county_rows.append(CountyDay(
                cycle=as_of.year, state="OH", county_fips=fips, day=as_of,
                county_name=canonical,
                ballots_total=total,
                mail_returned=mail_back,
                inperson=eip,
                party_dem=None, party_rep=None, party_oth=None, party_npa=None,
            ))
    return FetchResult(state_rows=state_rows, county_rows=county_rows)


# --------------------------------------------------------------------------
# The adapter
# --------------------------------------------------------------------------
class OHScraper(Adapter):
    """Tier 1 for Ohio: the SoS county absentee report, by whichever route answers.

    Ohio does NOT register voters by party, so every party_* field on every row
    this adapter emits is None -- never 0.
    """

    state = "OH"
    name = "oh-sos"
    tier = TIER_SCRAPER

    # ---- route 1 -----------------------------------------------------------
    def _legacy_workbook(self, cycle: int, *, use_cache: bool) -> bytes:
        base = BASE.format(cycle=cycle)
        problems: list[str] = []
        for template in FILENAMES:
            filename = template.format(cycle=cycle)
            try:
                body = download(
                    base + filename,
                    filename=f"{cycle}_{filename}",
                    use_cache=use_cache,
                    min_bytes=4096,
                )
            except Missing as exc:
                problems.append(str(exc))
                continue
            if not looks_like_xlsx(body):
                # The retired globalassets path 301s to the new site, so this is
                # now the usual answer rather than an exceptional one.
                problems.append(f"{filename} came back as HTML, not xlsx")
                continue
            return body
        raise Missing(f"OH: no legacy workbook for {cycle} ({'; '.join(problems)})")

    # ---- route 2 -----------------------------------------------------------
    def _publicfiles_workbook(self, cycle: int, *, use_cache: bool) -> bytes:
        raw = download(
            PUBLICFILES_INDEX,
            filename="publicfiles_files-index.json",
            use_cache=use_cache,
            min_bytes=1024,
        )
        try:
            index = json.loads(raw)
        except ValueError as exc:
            raise SchemaDrift(f"OH: files-index.json is not JSON: {exc}") from exc
        blob = find_workbook(index, cycle)
        body = download(
            _blob_url(blob),
            filename=f"{cycle}_publicfiles_{blob.rsplit('/', 1)[-1]}",
            use_cache=use_cache,
            min_bytes=4096,
        )
        if not looks_like_xlsx(body):
            raise SourceError(f"OH: {blob} was not an xlsx")
        return body

    # ---- the walk ----------------------------------------------------------
    def _workbook_routes(self, cycle: int, *, use_cache: bool):
        """Yield ('label', bytes) for each workbook route that answered.

        A route that 403s, 404s or hands back a web page is a reason to try the
        next one, not a reason to stop: only when every route has nothing does
        Ohio genuinely have nothing.
        """
        for label, loader in (
            ("globalassets", self._legacy_workbook),
            ("publicfiles", self._publicfiles_workbook),
        ):
            try:
                yield label, loader(cycle, use_cache=use_cache)
            except NotYetPublished as exc:
                yield label, exc
            except SourceError as exc:
                yield label, exc

    def fetch(self, cycle: int, as_of: date) -> FetchResult:
        """Whichever route answers, best measurement first.

        The exception at the end is the load-bearing part. The dashboard is the
        only route with data during the early-vote period, so it -- and only it
        -- is the authority on whether Ohio HAS data yet:

          * dashboard says the cycle is not in its table  -> NotYetPublished,
            STOP the ladder. Ohio really has nothing, and a weaker tier would
            invent a zero.
          * dashboard could not be reached or parsed      -> SourceError, fall
            through to the aggregator. We do not know what Ohio has, and
            "unknown" must never masquerade as "nothing". This is the branch
            that runs if Cloudflare spreads to the Power BI host or the SoS
            rotates the report's resource key.
        """
        problems: list[str] = []
        absent = False

        for label, answer in self._workbook_routes(cycle, use_cache=False):
            if isinstance(answer, Exception):
                problems.append(f"{label}: {answer}")
                continue
            try:
                result = parse(answer, cycle)
            except SourceError as exc:   # SchemaDrift included
                problems.append(f"{label}: {exc}")
                continue
            published = next(
                (r.day for r in result.state_rows), next(iter(result.county_rows)).day
            )
            if published > as_of:
                # A workbook dated ahead of the run is the certified
                # post-election report showing up early in a backfill; refuse
                # rather than publish a future-dated row. For the day being
                # fetched, that IS an absence -- we looked and Ohio had nothing
                # for it yet.
                absent = True
                problems.append(
                    f"{label}: report is dated {published.isoformat()}, "
                    f"after {as_of.isoformat()}"
                )
                continue
            return result

        try:
            result = parse_dashboard(fetch_dashboard(), cycle)
        except NotYetPublished as exc:
            absent = True
            problems.append(f"dashboard: {exc}")
        except SourceError as exc:
            problems.append(f"dashboard: {exc}")
        else:
            published = next(
                (r.day for r in result.state_rows), next(iter(result.county_rows)).day
            )
            if published > as_of:
                absent = True
                problems.append(
                    f"dashboard: snapshot is dated {published.isoformat()}, "
                    f"after {as_of.isoformat()}"
                )
            else:
                return result

        detail = f"OH: no absentee data for {cycle} on any route ({'; '.join(problems)})"
        if absent:
            raise NotYetPublished(detail)
        raise SourceError(detail)

    # ---- history: the certified final -------------------------------------
    def _archived_workbook(self, cycle: int) -> FetchResult:
        """The archived workbook for a past cycle -- the certified Election-Day row.

        Ohio overwrites the report in place rather than keeping a dated file per
        day, so this is one row per table, not a series: the cycle's final
        absentee total, which the 2022/2024 comparison lines anchor on.
        """
        problems: list[str] = []
        absent = False
        for label, answer in self._workbook_routes(cycle, use_cache=True):
            if isinstance(answer, Exception):
                # A 404, or an index that does not list the election, is real
                # absence. A 403 from Cloudflare is not -- it is us being unable
                # to look, which must not be recorded as Ohio having nothing.
                absent = absent or isinstance(answer, (Missing, NotYetPublished))
                problems.append(f"{label}: {answer}")
                continue
            try:
                return parse(answer, cycle)
            except SourceError as exc:
                problems.append(f"{label}: {exc}")
        detail = (
            f"OH: no archived absentee workbook for {cycle} ({'; '.join(problems)})"
        )
        if absent:
            raise NotYetPublished(detail)
        raise SourceError(detail)

    # ---- history: the dated series ----------------------------------------
    def _ledger_series(self, cycle: int) -> FetchResult:
        """Every pre-Election-Day day of `cycle`, cumulated out of the ledger."""
        if cycle < LEDGER_FIRST_CYCLE:
            raise NotYetPublished(
                f"OH: the absentee ledger begins with the 2023 November general; "
                f"it has no {cycle} rows"
            )
        # The table's own label for the cycle, from the live query -- a guessed
        # "2024 NOV GENERAL" that the table spells differently would come back
        # as an empty ledger and read as absence.
        label = general_label(decode_dsr(fetch_dashboard()), cycle)
        payload = fetch_ledger(label, cycle, use_cache=True)
        rows = decode_dsr(payload, required=LEDGER_COLUMNS + POWERBI_MEASURES)
        return parse_ledger(rows, cycle)

    def _weekly_series(self, cycle: int) -> FetchResult:
        """The pinned press-release files for `cycle`, each dated by its release.

        One unreadable file is logged and skipped rather than failing the
        others: each is its own dated snapshot and the rest are still right.
        """
        releases = WEEKLY_RELEASES.get(cycle)
        if not releases:
            raise NotYetPublished(f"OH: no weekly press-release files are pinned for {cycle}")
        result = FetchResult()
        problems: list[str] = []
        absent = True
        for release in releases:
            archived = release.url.startswith("https://web.archive.org/")
            try:
                body, written = download_dated(
                    release.url, filename=release.filename,
                    stamp_header=release.stamp_header, use_cache=True,
                    min_interval=ARCHIVE_MIN_INTERVAL if archived else 0.0,
                )
                check_release_date(written, release)
                result.extend(parse_weekly_xlsx(body, release.as_of, release.kind))
            except SourceError as exc:   # Missing and SchemaDrift included
                # A 404 is a file that is gone, which is absence; anything else
                # (a 429 that never cleared, a wall, drift) is a failure to look.
                absent = absent and isinstance(exc, Missing)
                log.warning("OH: %s weekly file skipped: %s", release.as_of.isoformat(), exc)
                problems.append(f"{release.as_of.isoformat()}: {exc}")
        if not result:
            detail = (
                f"OH: none of the {cycle} weekly files could be read ({'; '.join(problems)})"
            )
            if absent:
                raise NotYetPublished(detail)
            raise SourceError(detail)
        return result

    def _dated_series(self, cycle: int) -> FetchResult:
        if cycle in WEEKLY_RELEASES:
            return self._weekly_series(cycle)
        return self._ledger_series(cycle)

    def fetch_history(self, cycle: int) -> FetchResult:
        """A past cycle: the certified Election-Day row plus a dated series.

        Two parts, fetched independently and combined:

        * the archived workbook (routes 1 and 2) -- the certified final, which
          is the cycle's Election-Day row and the number the comparison lines
          anchor on. Unchanged from before the series existed.
        * the dated series -- for 2024 the dashboard's ledger cumulated per day
          (receipt-dated and restated; see THE LEDGER BEHIND THE DASHBOARD),
          emitted only for days BEFORE Election Day so the two parts never meet
          on a key; for 2022 the four weekly press-release workbooks.

        Either part may fail on its own: a blocked workbook route with a working
        ledger still returns the series (and logs the problem), and vice versa.
        Only when NEITHER produced a row does this raise -- NotYetPublished if
        both reported absence, SourceError if either was a failure to look.

        The live dashboard snapshot (`parse_dashboard`) is still NOT used here:
        its single cumulative number is a pre-certification tracker (2,262,963
        for 2024 against the workbook's 2,620,750). The ledger is a different
        reading of the same table, and it stops where the workbook starts.
        """
        if cycle >= datetime.now().year:
            raise NotYetPublished(f"OH: {cycle} is not an archived cycle")
        result = FetchResult()
        problems: list[str] = []
        failed = False
        for part, loader in (
            ("workbook", self._archived_workbook),
            ("series", self._dated_series),
        ):
            try:
                result.extend(loader(cycle))
            except NotYetPublished as exc:
                problems.append(f"{part}: {exc}")
            except SourceError as exc:
                failed = True
                problems.append(f"{part}: {exc}")
        if result:
            for problem in problems:
                log.warning("OH: %s history is partial -- %s", cycle, problem)
            return result
        detail = f"OH: no archived absentee data for {cycle} ({'; '.join(problems)})"
        if failed:
            raise SourceError(detail)
        raise NotYetPublished(detail)
