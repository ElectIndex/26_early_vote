"""Louisiana: the Secretary of State's daily absentee-and-early-voter rosters.

WHAT LOUISIANA PUBLISHES, AND WHAT IT DOES NOT
----------------------------------------------
Louisiana's headline early-vote product is the **Early Voting Statistical
Report** -- parish x precinct, with race, sex, party, UOCAVA and an in-person /
absentee split. It is the richest early-vote breakdown any state offers. It is
also posted **a week after the election**, one file per election, never during
the window:

    2024_1105_ParishStats.pdf   election 11/05/2024   created 11/12/2024
    2022_1108_ParishStats.pdf   election 11/08/2022   created 11/16/2022

So it cannot drive a tracker. It is used here for `fetch_history` only, where
being late costs nothing and the breakdown is worth having.

The during-season source is a different, humbler file: the **Absentee by Mail
and Early Voters** roster, published per parish per day at

    /Data/Absentee_Voter_List/{YYYYMMDD}_{PARISH}_Daily_{MMDD}.pdf

listing, by ward and precinct, every voter whose early or absentee ballot was
recorded on that date -- and ending with a `Total Voters: N` trailer, which is
the only line this adapter reads. A `..._PreEV.pdf` carries everything received
before early voting opened, and a `..._Cumulative.pdf` is written once, on
election day.

Four things follow from that shape, and they are the whole design:

* **The dailies are increments, so the cumulative curve is a running sum.**
  PreEV + every daily <= as_of. Verified against Louisiana's own published
  figures: East Baton Rouge's 2024 general dailies sum to 94,928 against the
  post-election report's 94,908 -- 20 ballots, 0.02%, and exactly the drift the
  SoS's own page warns about ("This list of voters does not account for
  subsequent changes, if any, made by a registrar"). Statewide the whole 2024
  window rebuilds to 970,313 by 11/04 against the report's 975,019, the balance
  being Election Day mail arrivals and registrar corrections -- which is why
  `fetch_history` lets the report REPLACE Election Day and stamps it
  `restated = 1`.
* **Every dated file is immutable**, so a missed run loses nothing: the next run
  re-reads the whole window and restates. Past days are served from `cache/`.
* **The roster carries no party, no method, no race and no sex.** Louisiana
  registers voters by party, and the post-election report breaks early votes down
  every way there is -- but this file has none of it, so every one of those
  fields is `None` here. Blank means "this source does not report it"; see THE
  BLANK RULE in schema.py.
* **It is expensive, and the host notices.** A full rebuild of the 2024
  general's window measured **1,026 files, 168 MB and 150 seconds** -- 64
  parishes x ~16 posting days -- and shortly afterwards the SoS's edge WAF began
  answering 403 to *every* URL on the host from this IP. See `_MIN_INTERVAL`:
  the module throttles itself, every day before `as_of` is served from `cache/`,
  and a wall it cannot get past raises `SourceError` so the ladder falls through
  instead of recording "Louisiana has nothing".

WHY NOT THE OTHER ROUTES
------------------------
* `Early_Voting_Statistics` (parish and statewide): post-election only, verified
  again 2026-09-06 by walking the year dropdown for 2022, 2024 and 2026.
* The 2026 files are `.xls` -- real OLE2/BIFF, not an HTML table with an .xls
  name (verified: the body starts `D0 CF 11 E0`). `openpyxl` cannot read those
  and `xlrd` is not a dependency, so only the PDF form is parsed.
* `voterportal.sos.la.gov` is live *results*, not turnout.

Louisiana uses PARISHES. They are keyed by 5-digit FIPS through `_fips.lookup`
like every other state's counties -- never by name, and never by the SoS's own
four-letter code, which is only ever used to build a URL.
"""

from __future__ import annotations

import html
import io
import logging
import re
import time
from collections import defaultdict
from datetime import date, datetime, timedelta

import pypdf

from ..calendar import election_date
from ..normalize import race as _race, sex as _sex
from ..schema import TIER_SCRAPER, CountyDay, DemoDay, StateDay
from . import _fips, _net
from .base import Adapter, FetchResult, NotYetPublished, SchemaDrift, SourceError

log = logging.getLogger(__name__)

BASE = "https://electionstatistics.sos.la.gov"

#: `electionstatistics.sos.la.gov` sits behind an edge WAF that answers with a
#: ~400-byte "Access Denied / Reference #18.…" page and HTTP 403. Two distinct
#: behaviours were observed live on 2026-09-06:
#:
#: 1. **Flapping.** The same URL 403ing to one client and 200ing to the next in
#:    the same second, while a 1,026-file window rebuild ran clean.
#: 2. **A rate ban.** After that rebuild -- 1,026 requests in 150 s, about 7/s --
#:    the WHOLE HOST began answering 403 to every URL from this IP, including its
#:    root, to both `requests` and a full browser fingerprint. Not a fingerprint
#:    rule: it is per-IP and volume-triggered, and it cleared on its own after
#:    roughly half an hour.
#:
#: So this module throttles itself, and treats a wall it cannot get past as
#: `SourceError` -- "we could not look" -- which falls through to the aggregator
#: rather than being recorded as "Louisiana has nothing".
#:
#: **UNVERIFIED:** _MIN_INTERVAL was chosen after the ban was already in force,
#: so it has NOT been shown to avoid one. 0.35 s puts a full late-October run at
#: roughly six minutes and under 3 requests/second, against the ~7/s that
#: provoked it. Whoever runs the first live October ingest should watch for a
#: host-wide 403 and raise this if it appears.
_MIN_INTERVAL = 0.35
_RETRIES = 3
_BACKOFF = 4.0

_last_request = 0.0


def _throttle() -> None:
    """Space this module's requests out. See _MIN_INTERVAL."""
    global _last_request
    wait = _MIN_INTERVAL - (time.monotonic() - _last_request)
    if wait > 0:
        time.sleep(wait)
    _last_request = time.monotonic()

#: The ASP.NET page that lists which roster files exist for one parish and year.
#: VERIFIED live 2026-09-06: HTTP 200, and a POST carrying the initial page's
#: viewstate plus `cboParish`/`cboYear` returns that parish's file table.
VOTER_LIST_PAGE = f"{BASE}/EarlyVoterList.aspx"

#: Where the roster PDFs themselves live. VERIFIED: e.g.
#: `.../20241105_EBTR_Daily_1018.pdf` -> 200, 783,801 bytes, application/pdf;
#: `.../20241105_EBTR_Daily_1020.pdf` (a Sunday) -> 404, 1,245 bytes.
VOTER_LIST_DIR = f"{BASE}/Data/Absentee_Voter_List"

#: The post-election Early Voting Statistical Report. VERIFIED:
#: `2024_1105_ParishStats.pdf` -> 200, 2,036,173 b; `2022_1108_ParishStats.pdf`
#: -> 200, 2,039,710 b.
PARISH_STATS_DIR = f"{BASE}/Data/Early_Voting_Statistics/parish"

#: The SoS's four-letter parish code -> the parish name it labels, exactly as the
#: `cboParish` dropdown on VOTER_LIST_PAGE spells it (read live 2026-09-06). The
#: code exists only to build a URL; the NAME is what gets resolved to a FIPS
#: through `_fips.lookup`, so this table can never put a parish under the wrong
#: geography -- a rename just fails the lookup and raises SchemaDrift.
PARISH_CODES: tuple[tuple[str, str], ...] = (
    ("ACAD", "ACADIA"), ("ALLN", "ALLEN"), ("ASCN", "ASCENSION"),
    ("ASMP", "ASSUMPTION"), ("AVLS", "AVOYELLES"), ("BEAU", "BEAUREGARD"),
    ("BNVL", "BIENVILLE"), ("BSSR", "BOSSIER"), ("CADO", "CADDO"),
    ("CALC", "CALCASIEU"), ("CALD", "CALDWELL"), ("CAMN", "CAMERON"),
    ("CATL", "CATAHOULA"), ("CLBN", "CLAIBORNE"), ("CNCD", "CONCORDIA"),
    ("DSTO", "DE SOTO"), ("EBTR", "EAST BATON ROUGE"), ("ECRL", "EAST CARROLL"),
    ("EFLC", "EAST FELICIANA"), ("EVNG", "EVANGELINE"), ("FRNK", "FRANKLIN"),
    ("GRNT", "GRANT"), ("IBRA", "IBERIA"), ("IBVL", "IBERVILLE"),
    ("JAXN", "JACKSON"), ("JEFF", "JEFFERSON"), ("JFDV", "JEFFERSON DAVIS"),
    ("LAFT", "LAFAYETTE"), ("LAFX", "LAFOURCHE"), ("LASL", "LASALLE"),
    ("LNCN", "LINCOLN"), ("LVGN", "LIVINGSTON"), ("MDSN", "MADISON"),
    ("MRHS", "MOREHOUSE"), ("NTCH", "NATCHITOCHES"), ("ORLN", "ORLEANS"),
    ("OUCT", "OUACHITA"), ("PLQM", "PLAQUEMINES"), ("PTCP", "POINTE COUPEE"),
    ("RAPD", "RAPIDES"), ("RDRV", "RED RIVER"), ("RICH", "RICHLAND"),
    ("SABN", "SABINE"), ("STBR", "ST. BERNARD"), ("STCH", "ST. CHARLES"),
    ("STHL", "ST. HELENA"), ("STJM", "ST. JAMES"),
    ("STJN", "ST. JOHN THE BAPTIST"), ("STLN", "ST. LANDRY"),
    ("STMT", "ST. MARTIN"), ("STMY", "ST. MARY"), ("STTM", "ST. TAMMANY"),
    ("TNGP", "TANGIPAHOA"), ("TNSA", "TENSAS"), ("TRBN", "TERREBONNE"),
    ("UNON", "UNION"), ("VRML", "VERMILION"), ("VRNN", "VERNON"),
    ("WASH", "WASHINGTON"), ("WBST", "WEBSTER"), ("WBTR", "WEST BATON ROUGE"),
    ("WCRL", "WEST CARROLL"), ("WFLC", "WEST FELICIANA"), ("WINN", "WINN"),
)

#: Every Louisiana parish. A run covering fewer publishes parishes but NO
#: statewide row -- a sum over 61 of 64 parishes looks exactly like a Louisiana
#: turnout figure and is not one. Same rule as tx.py.
EXPECTED_PARISHES = len(_fips.CENSUS_COUNTIES["LA"])

#: The trailer this adapter reads out of a roster PDF. It appears exactly once
#: per file, on the last page (verified across the whole 2024 East Baton Rouge
#: series: 17 files, one hit each).
_TOTAL_VOTERS = re.compile(r"Total\s+Voters:\s*([\d,]+)")

#: The report's own banner, which is present on EVERY page of every roster --
#: including a day on which nobody voted, where it is the ONLY thing on the
#: page. That is how "zero voters" is told apart from "this is not the file we
#: think it is": verified on `20241105_ACAD_Daily_1102.pdf`, one page, 78,625
#: bytes, banner and column headings and no voter rows and no total.
_ROSTER_BANNER = re.compile(
    r"Louisiana Secretary of State.*?Absentee\s+\w+\s+Voters\s+Summary", re.S
)

_HIDDEN_INPUT = re.compile(r"<input[^>]*type=\"hidden\"[^>]*>", re.I)
_ATTR_NAME = re.compile(r"name=\"([^\"]+)\"", re.I)
_ATTR_VALUE = re.compile(r"value=\"([^\"]*)\"", re.I)
_ROW = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
_CELL = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S | re.I)
_TAG = re.compile(r"<[^>]+>")
_VIEWSTATE_DIV = re.compile(r"<div class=\"aspNetHidden\">.*?</div>", re.S)
_SCRIPT = re.compile(r"<script.*?</script>", re.S | re.I)

#: `20241105_EBTR_Daily_1018.pdf` / `..._PreEV.pdf` / `..._Cumulative.pdf`.
_ROSTER_NAME = re.compile(
    r"^(?P<stamp>\d{8})_(?P<parish>[A-Z]{4})_(?P<kind>Daily_(?P<mmdd>\d{4})|PreEV|Cumulative)\.pdf$",
    re.I,
)

#: The Early Voting Statistical Report's per-parish block header, e.g.
#: "ACADIA-01", "ST. JOHN THE BAPTIST-46", "WEST BATON ROUGE-61".
_PARISH_BLOCK = re.compile(r"^([A-Z][A-Z .'-]+)-(\d{2})$")

#: The columns of the report's per-parish SUM row, in file order. Read off the
#: printed header:
#:   --RACE-- ------SEX------ ----PARTY---- UOCAVA ---VOTES--- --ASST--
#:   TOTVTE WHITE BLACK OTH  MALE FEMALE  DEM REP OTH  IN OUT  INPER ABS  D I
SUM_COLUMNS = (
    "TOTVTE", "WHITE", "BLACK", "RACE_OTH", "MALE", "FEMALE",
    "DEM", "REP", "PARTY_OTH", "UOCAVA_IN", "UOCAVA_OUT",
    "INPER", "ABS", "ASST_D", "ASST_I",
)


# --------------------------------------------------------------------------
# Transport helpers
# --------------------------------------------------------------------------
def _hidden_fields(markup: str) -> dict[str, str]:
    """Every hidden input on the page -- __VIEWSTATE and friends."""
    fields: dict[str, str] = {}
    for tag in _HIDDEN_INPUT.findall(markup):
        name = _ATTR_NAME.search(tag)
        if not name:
            continue
        value = _ATTR_VALUE.search(tag)
        fields[name.group(1)] = html.unescape(value.group(1)) if value else ""
    return fields


def _text(fragment: str) -> str:
    return " ".join(html.unescape(_TAG.sub(" ", fragment)).split())


def _table_rows(markup: str) -> list[list[str]]:
    body = _SCRIPT.sub("", _VIEWSTATE_DIV.sub("", markup))
    rows = []
    for row in _ROW.findall(body):
        cells = [_text(cell) for cell in _CELL.findall(row)]
        if any(cells):
            rows.append(cells)
    return rows


def _retrying(call):
    """Run `call`, retrying a SourceError a few times. `Missing` is never retried.

    See _RETRIES: this host's WAF refuses the odd request at random, and a
    SourceError anywhere in a thousand-file run would drop Louisiana to the
    aggregator for the day. A 404 is not a fault and is passed straight through.
    """
    for attempt in range(_RETRIES):
        _throttle()
        try:
            return call()
        except _net.Missing:
            raise
        except SourceError as exc:
            if attempt == _RETRIES - 1:
                if "403" in str(exc):
                    # Say what this is, because "HTTP 403" in ev_status.json
                    # reads like a transient fault somebody should retry, and
                    # this one is a per-IP rate ban that outlives the run.
                    raise SourceError(
                        f"{exc} -- electionstatistics.sos.la.gov is refusing "
                        f"this IP (its edge WAF rate-bans a whole host after a "
                        f"heavy run; see _MIN_INTERVAL in la.py). Louisiana is "
                        f"unreadable until it clears, which is not the same as "
                        f"Louisiana having no data."
                    ) from exc
                raise
            time.sleep(_BACKOFF * (attempt + 1))
    raise AssertionError("unreachable")


def _get(url: str, *, filename: str, use_cache: bool = False,
         min_bytes: int = 64, timeout: int = _net.DEFAULT_TIMEOUT) -> bytes:
    """A throttled, retried `_net.get`. A cache hit costs neither."""
    path = _net.cache_path("LA", filename)
    if use_cache and path.exists() and path.stat().st_size >= min_bytes:
        return path.read_bytes()
    return _retrying(lambda: _net.get(
        url, state="LA", filename=filename, use_cache=use_cache,
        min_bytes=min_bytes, timeout=timeout,
    ))


def _post(url: str, data: dict[str, str], *, timeout: int = 60) -> str:
    """POST an ASP.NET postback and return the page.

    `_net.get` is GET-only and this listing is a WebForms postback, so the one
    POST in this module goes through the shared session directly -- same
    headers, same TLS-ticket fix, same exception vocabulary, same retry.
    """
    def once() -> str:
        try:
            response = _net.SESSION.post(
                url, data=data, headers=_net.DEFAULT_HEADERS, timeout=timeout,
            )
        except Exception as exc:  # noqa: BLE001 - requests raises many shapes
            raise SourceError(f"LA: POST {url} failed: {exc}") from exc
        if response.status_code in (404, 410):
            raise _net.Missing(f"LA: {url} returned {response.status_code}")
        if not response.ok:
            raise SourceError(f"LA: {url} returned HTTP {response.status_code}")
        return response.text

    return _retrying(once)


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------
def _mdy(raw: str) -> date | None:
    """The listing's "Date Created" cell, e.g. "10/19/2024" or "5/23/2026".

    `%d`/`%m` accept an unpadded number, which the SoS's table uses for single
    digit months and days.
    """
    try:
        return datetime.strptime((raw or "").strip(), "%m/%d/%Y").date()
    except ValueError:
        return None


def roster_day(name: str, created: date | None, election: date) -> date | None:
    """The date a roster file's ballots were recorded, or None to skip it.

    `Daily_MMDD` says so in its own filename. `PreEV` -- everything received
    before early voting opened -- says so only through the listing's "Date
    Created" column, which the SoS stamps the morning AFTER the day it covers
    (verified: `Daily_1018` is created 10/19, `PreEV` is created 10/18 and the
    first daily is 10/18, so both are `created - 1`).

    `Cumulative` returns None: it is the end-of-window restatement of everything
    the dailies already carry, so counting it would double the state. It is also
    an 8 MB file per parish, which is reason enough on its own.
    """
    match = _ROSTER_NAME.match(name)
    if match is None:
        return None
    kind = match.group("kind")
    if kind.lower().startswith("cumulative"):
        return None
    if kind.lower() == "preev":
        return created - timedelta(days=1) if created else None
    mmdd = match.group("mmdd")
    month, day = int(mmdd[:2]), int(mmdd[2:])
    try:
        stamped = date(election.year, month, day)
    except ValueError as exc:
        raise SchemaDrift(f"LA: {name} carries an impossible date {mmdd}") from exc
    # A daily can never post-date its own election; a December election whose
    # window opens in November is still the same year, so the only way to land
    # after Election Day is a filename we have misread.
    if stamped > election:
        raise SchemaDrift(
            f"LA: {name} dates to {stamped.isoformat()}, after the "
            f"{election.isoformat()} election it is filed under"
        )
    return stamped


def total_voters(body: bytes, name: str) -> int:
    """The `Total Voters: N` trailer on a roster PDF's last page.

    Only the last page is rendered -- these files run to 240 pages of names and
    the count is the only thing wanted from any of them.
    """
    try:
        reader = pypdf.PdfReader(io.BytesIO(body))
        pages = reader.pages
        text = pages[len(pages) - 1].extract_text() or ""
    except Exception as exc:  # noqa: BLE001 - pypdf raises many shapes
        raise SchemaDrift(f"LA: {name} is not a readable PDF: {exc}") from exc
    match = _TOTAL_VOTERS.search(text)
    if match is not None:
        return int(match.group(1).replace(",", ""))
    # No trailer. Either nobody voted that day -- the SoS still publishes the
    # report, as a single banner page -- or this is not the file we think it is.
    # The banner is what tells those apart, and getting it wrong in the "0"
    # direction would silently drop a real day out of the running sum.
    if _ROSTER_BANNER.search(text):
        return 0
    raise SchemaDrift(f"LA: {name} has no 'Total Voters:' trailer")


def parse_parish_stats(body: bytes, cycle: int) -> dict[str, dict[str, int]]:
    """Per-parish SUM rows out of the post-election Early Voting Statistical Report.

    The report prints one block per parish -- a `PARISH NAME-NN` header, its
    precinct rows, then a `SUM` row of fifteen numbers in SUM_COLUMNS order.
    Every block is checked four ways before it is trusted: race, sex, party and
    method must each add up to the block's own TOTVTE. A misaligned SUM row
    cannot survive all four, which is what makes it safe to read a fixed column
    order out of a PDF text layer.
    """
    try:
        reader = pypdf.PdfReader(io.BytesIO(body))
        lines: list[str] = []
        for page in reader.pages:
            lines.extend((page.extract_text() or "").split("\n"))
    except Exception as exc:  # noqa: BLE001
        raise SchemaDrift(f"LA: {cycle} parish statistics is not a readable PDF: {exc}") from exc

    out: dict[str, dict[str, int]] = {}
    current: str | None = None
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        header = _PARISH_BLOCK.match(line)
        if header:
            current = header.group(1)
        elif line == "SUM":
            values: list[int] = []
            cursor = index + 1
            while cursor < len(lines) and len(values) < len(SUM_COLUMNS):
                token = lines[cursor].strip().replace(",", "")
                if not token:
                    cursor += 1
                    continue
                if not re.fullmatch(r"-?\d+", token):
                    break
                values.append(int(token))
                cursor += 1
            if current is None or len(values) != len(SUM_COLUMNS):
                raise SchemaDrift(
                    f"LA: {cycle} parish statistics has a SUM row with "
                    f"{len(values)} numbers under parish {current!r}"
                )
            row = dict(zip(SUM_COLUMNS, values))
            _check_sum_row(current, row, cycle)
            out[current] = row
        index += 1

    if not out:
        raise SchemaDrift(f"LA: {cycle} parish statistics yielded no parish blocks")
    return out


def require_full_coverage(stats: dict[str, dict[str, int]], cycle: int) -> None:
    """Refuse to publish a statewide total over fewer than all 64 parishes.

    `build_final` sums whatever it is handed, so this is the guard that keeps a
    short or half-downloaded report from being published as Louisiana. It is
    policy, not parsing, which is why it lives outside `parse_parish_stats`.
    """
    if len(stats) != EXPECTED_PARISHES:
        raise SchemaDrift(
            f"LA: {cycle} parish statistics yielded {len(stats)} parish blocks, "
            f"expected {EXPECTED_PARISHES}"
        )


def _check_sum_row(parish: str, row: dict[str, int], cycle: int) -> None:
    """Race, party and method must total exactly; sex may fall short.

    Race, party and method each carry an explicit residual column ("OTH", "OTH",
    and the ABS/INPER pair), so those three add up to TOTVTE to the ballot in
    every one of the 128 parish blocks across the 2022 and 2024 reports. **Sex
    does not**: the report prints only MALE and FEMALE, and Louisiana's roll
    carries voters who are neither -- East Baton Rouge's 2024 general is 38,449
    + 56,293 = 94,742 against a TOTVTE of 94,908. So sex is checked as "no more
    than the total", and the 166 are published as the `unknown` bucket rather
    than dropped or folded into one of the other two.
    """
    total = row["TOTVTE"]
    for label, parts in (
        ("race", ("WHITE", "BLACK", "RACE_OTH")),
        ("party", ("DEM", "REP", "PARTY_OTH")),
        ("method", ("INPER", "ABS")),
    ):
        got = sum(row[p] for p in parts)
        if got != total:
            raise SchemaDrift(
                f"LA: {cycle} {parish} SUM row does not balance -- {label} "
                f"sums to {got} against TOTVTE {total}; the columns have moved"
            )
    sexed = row["MALE"] + row["FEMALE"]
    if sexed > total:
        raise SchemaDrift(
            f"LA: {cycle} {parish} SUM row does not balance -- sex sums to "
            f"{sexed}, more than TOTVTE {total}; the columns have moved"
        )


def _fips_for(name: str) -> tuple[str, str]:
    hit = _fips.lookup("LA", name)
    if hit is None:
        raise SchemaDrift(f"LA: unrecognised parish name {name!r}")
    return hit


# --------------------------------------------------------------------------
# Assembling the daily curve
# --------------------------------------------------------------------------
def build_series(
    daily: dict[str, dict[date, int]], cycle: int, as_of: date,
) -> FetchResult:
    """Turn {parish name -> {day -> that day's ballots}} into cumulative rows.

    A parish contributes from its own first file onward. Days with no file get a
    row carrying the running total and `ballots_new = None` -- Louisiana skips
    Sundays and the odd weekday, and "no file" is "not reported", not "zero
    ballots were recorded". See THE BLANK RULE.
    """
    result = FetchResult()
    days = sorted({d for series in daily.values() for d in series})
    if not days:
        return result

    span = []
    cursor = min(days)
    while cursor <= as_of:
        span.append(cursor)
        cursor += timedelta(days=1)

    statewide_total: dict[date, int] = defaultdict(int)
    statewide_new: dict[date, int] = defaultdict(int)
    reporting: dict[date, int] = defaultdict(int)
    parishes = [p for p, series in daily.items() if series]
    covered = len(parishes)
    # A parish contributes nothing before its own first file, so a statewide sum
    # taken earlier than the LAST parish's first file is short by however many
    # ballots the late parishes had already recorded. Statewide rows therefore
    # start where every parish has started, even though the parish rows do not.
    all_started = max(min(daily[p]) for p in parishes) if parishes else None

    for parish in sorted(parishes):
        fips, canonical = _fips_for(parish)
        series = daily[parish]
        running = 0
        started = False
        for day in span:
            today = series.get(day)
            if today is not None:
                running += today
                started = True
            if not started:
                continue
            result.county_rows.append(CountyDay(
                cycle=cycle, state="LA", county_fips=fips, day=day,
                county_name=canonical,
                ballots_total=running,
                ballots_new=today,
                # The roster splits nothing: no method, no party, no
                # demographics. None, never 0.
                mail_returned=None, inperson=None,
                party_dem=None, party_rep=None, party_oth=None, party_npa=None,
            ))
            statewide_total[day] += running
            if today is not None:
                statewide_new[day] += today
                reporting[day] += 1

    if covered == EXPECTED_PARISHES:
        for day in span:
            if all_started is None or day < all_started:
                continue
            result.state_rows.append(StateDay(
                cycle=cycle, state="LA", day=day,
                ballots_total=statewide_total[day],
                # Only a real day's-worth if EVERY parish filed for that day;
                # otherwise the missing parishes' new ballots are unknown, not
                # zero, and a partial sum would read as a national-style
                # "ballots cast today" figure.
                ballots_new=(statewide_new[day]
                             if reporting[day] == covered else None),
                mail_requested=None, mail_returned=None, inperson=None,
                party_dem=None, party_rep=None, party_oth=None, party_npa=None,
            ))
    else:
        # PARTIAL COVERAGE -- see EXPECTED_PARISHES.
        log.warning(
            "LA: rosters cover %d of %d parishes; publishing parishes only, no "
            "statewide row (a partial sum would read as a Louisiana total)",
            covered, EXPECTED_PARISHES,
        )
    return result


def build_final(
    stats: dict[str, dict[str, int]], cycle: int, day: date,
) -> FetchResult:
    """The post-election report as one dated row per parish plus demographics.

    This is a RESTATEMENT of the roster sum, not an addition to it: the SoS
    reconciles registrar corrections here, so East Baton Rouge's 2024 general is
    94,908 in this report against 94,928 summed from the dailies. The statewide
    row is therefore stamped `restated = 1`.

    **DEM and REP are published; NPA and OTH are not.** Louisiana registers
    voters by party but this report collapses everything that is neither
    Democrat nor Republican into one `OTH` column, which mixes no-party voters
    with Libertarians and the rest. normalize.py is explicit that collapsing
    those two loses the most-watched number in an early-vote story, so the
    residual is published as neither -- exactly as ky.py does.
    """
    result = FetchResult()
    totals = {key: 0 for key in SUM_COLUMNS}

    for parish in sorted(stats):
        fips, canonical = _fips_for(parish)
        row = stats[parish]
        for key in SUM_COLUMNS:
            totals[key] += row[key]
        result.county_rows.append(CountyDay(
            cycle=cycle, state="LA", county_fips=fips, day=day,
            county_name=canonical,
            ballots_total=row["TOTVTE"],
            ballots_new=None,
            mail_returned=row["ABS"],
            inperson=row["INPER"],
            party_dem=row["DEM"], party_rep=row["REP"],
            # See the docstring: the residual is not splittable.
            party_oth=None, party_npa=None,
        ))

    result.state_rows.append(StateDay(
        cycle=cycle, state="LA", day=day,
        ballots_total=totals["TOTVTE"],
        ballots_new=None,
        # The report counts ballots CAST; it never says how many were requested.
        mail_requested=None,
        mail_returned=totals["ABS"],
        inperson=totals["INPER"],
        party_dem=totals["DEM"], party_rep=totals["REP"],
        party_oth=None, party_npa=None,
        restated=1,
    ))

    for column, raw in (("WHITE", "white"), ("BLACK", "black"), ("RACE_OTH", "oth")):
        result.demo_rows.append(DemoDay(
            cycle=cycle, state="LA", day=day, dimension="race",
            bucket=_bucket(_race(raw), "race", raw),
            ballots_total=totals[column],
        ))
    for column, raw in (("MALE", "male"), ("FEMALE", "female")):
        result.demo_rows.append(DemoDay(
            cycle=cycle, state="LA", day=day, dimension="sex",
            bucket=_bucket(_sex(raw), "sex", raw),
            ballots_total=totals[column],
        ))
    # The residual is exactly the voters the report does not sex, inside one
    # report and one universe -- see _check_sum_row. Unlike the party residual
    # it conflates nothing, because "neither male nor female" IS the `unknown`
    # bucket rather than two different things our vocabulary keeps apart.
    result.demo_rows.append(DemoDay(
        cycle=cycle, state="LA", day=day, dimension="sex",
        bucket=_bucket(_sex("unknown"), "sex", "unknown"),
        ballots_total=totals["TOTVTE"] - totals["MALE"] - totals["FEMALE"],
    ))
    return result


def _bucket(value: str | None, dimension: str, raw: str) -> str:
    if value is None:
        raise SchemaDrift(f"LA: cannot normalize {dimension} label {raw!r}")
    return value


# --------------------------------------------------------------------------
# The adapter
# --------------------------------------------------------------------------
class LAScraper(Adapter):
    """Tier 1 for Louisiana: the SoS's daily absentee-and-early-voter rosters."""

    state = "LA"
    name = "la-sos"
    tier = TIER_SCRAPER

    # ------------------------------------------------------------------
    def fetch(self, cycle: int, as_of: date) -> FetchResult:
        election = election_date(cycle)
        listings = self._listings(election)
        daily = self._read_rosters(listings, election, as_of, archived=False)
        if not daily:
            raise NotYetPublished(
                f"LA: no absentee/early voter roster posted for the {cycle} "
                f"general as of {as_of.isoformat()}"
            )
        return build_series(daily, cycle, as_of)

    # ------------------------------------------------------------------
    def fetch_history(self, cycle: int) -> FetchResult:
        """The archived daily curve, restated on Election Day by the SoS report.

        Both halves are real and they disagree by ~0.02%; the report is the
        reconciliation, so it replaces Election Day rather than adding to it.
        """
        election = election_date(cycle)
        listings = self._listings(election)
        daily = self._read_rosters(listings, election, election, archived=True)
        result = FetchResult()
        if daily:
            series = build_series(daily, cycle, election)
            # Election Day belongs to the report below, where it exists.
            result.state_rows = [r for r in series.state_rows if r.day != election]
            result.county_rows = [r for r in series.county_rows if r.day != election]

        try:
            final = self._parish_stats(cycle, election)
        except (_net.Missing, NotYetPublished):
            if not result:
                raise NotYetPublished(
                    f"LA: no archived early-vote data for {cycle}"
                ) from None
            log.warning("LA: no post-election parish statistics for %s", cycle)
            return result
        return result.extend(final)

    # ------------------------------------------------------------------
    def _listings(self, election: date) -> dict[str, list[tuple[str, date | None]]]:
        """Which roster files exist, per parish, for this election.

        One GET for the page's viewstate, then one postback per parish. The
        table gives the filename AND the SoS's own "Date Created", which is the
        only thing that dates the PreEV file.
        """
        try:
            page = _get(
                VOTER_LIST_PAGE, filename="EarlyVoterList.aspx", min_bytes=2048,
            ).decode("utf-8", errors="replace")
        except _net.Missing as exc:
            raise SourceError(f"LA: {VOTER_LIST_PAGE} is gone: {exc}") from exc
        if not _net.looks_like_html(page.encode()):
            raise SourceError(f"LA: {VOTER_LIST_PAGE} did not return a page")

        base_fields = _hidden_fields(page)
        if "__VIEWSTATE" not in base_fields:
            raise SchemaDrift(f"LA: {VOTER_LIST_PAGE} carries no __VIEWSTATE")

        stamp = election.strftime("%Y%m%d")
        listings: dict[str, list[tuple[str, date | None]]] = {}
        for code, parish in PARISH_CODES:
            fields = dict(base_fields)
            fields.update({
                "__EVENTTARGET": "cboParish",
                "__EVENTARGUMENT": "",
                "cboParish": code,
                "cboYear": str(election.year),
            })
            markup = _post(VOTER_LIST_PAGE, fields)
            files: list[tuple[str, date | None]] = []
            for cells in _table_rows(markup):
                if len(cells) < 5:
                    continue
                filename = cells[1]
                if not filename.startswith(f"{stamp}_{code}_"):
                    continue
                files.append((filename, _mdy(cells[3])))
            if files:
                listings[parish] = files
        return listings

    # ------------------------------------------------------------------
    def _read_rosters(
        self,
        listings: dict[str, list[tuple[str, date | None]]],
        election: date,
        as_of: date,
        *,
        archived: bool,
    ) -> dict[str, dict[date, int]]:
        daily: dict[str, dict[date, int]] = {}
        for parish, files in listings.items():
            series: dict[date, int] = {}
            for filename, created in files:
                day = roster_day(filename, created, election)
                if day is None or day > as_of:
                    continue
                # Every dated roster is immutable once written, so anything
                # strictly before today can be served from cache/ -- which is
                # what makes a ~1,000-file window rebuild affordable to re-run.
                body = self._pdf(filename, use_cache=archived or day < as_of)
                if body is None:
                    continue
                series[day] = total_voters(body, filename)
            if series:
                daily[parish] = series
        return daily

    def _pdf(self, filename: str, *, use_cache: bool) -> bytes | None:
        url = f"{VOTER_LIST_DIR}/{filename}"
        try:
            body = _get(url, filename=filename, use_cache=use_cache, min_bytes=1024)
        except _net.Missing:
            # The listing said it was there and it is not. Treat it as absent
            # rather than as a fault: the next run re-reads the whole window.
            log.warning("LA: %s is listed but returned 404", filename)
            return None
        if _net.looks_like_html(body):
            raise SourceError(f"LA: {url} returned a page, not a PDF")
        return body

    # ------------------------------------------------------------------
    def _parish_stats(self, cycle: int, election: date) -> FetchResult:
        filename = f"{cycle}_{election.strftime('%m%d')}_ParishStats.pdf"
        url = f"{PARISH_STATS_DIR}/{filename}"
        body = _get(url, filename=filename, use_cache=True,
                    min_bytes=4096, timeout=300)
        if _net.looks_like_html(body):
            raise NotYetPublished(f"LA: {url} is not posted yet")
        stats = parse_parish_stats(body, cycle)
        require_full_coverage(stats, cycle)
        return build_final(stats, cycle, election)
