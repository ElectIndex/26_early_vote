"""Connecticut — the Secretary of the State's early-voting and absentee detail files.

Connecticut publishes **one row per ballot**, in two workbooks refreshed through
the voting period and linked from a per-cycle "Voter Data" page:

```
VOTER ID | TID | FIRST NAME | ... | RESIDENCE ADDRESS CITY | ... | YOB | DST | PR |
PARTY | SERIAL | DT MAILED | DT RETURNED | TM RETURN | RETURN_TYPE | ISSUE_TYPE
```

`early_voting_<date>.xlsx` is one row per in-person early vote;
`absentee_ballot_<date>.xlsx` is one row per absentee ballot **issued**, with a
`DT RETURNED` that is blank until it comes back. Because every ballot carries its
own dates, a single download reconstructs the whole daily curve the way North
Carolina's and Maine's files do, and a day we fail to run is not a day of
Connecticut's series lost forever.

**Connecticut runs elections by TOWN**, so this adapter publishes town rows keyed
by the 10-digit Census county-subdivision GEOID and derives its county rows by
summing them. That rollup is exact rather than inferred: a cousub GEOID is
state(2) + county(3) + cousub(5), so Enfield's `0900325990` already says
"Hartford County" in digits 3-5. See `adapters/_towns.py`, and `me.py` for the
same shape in Maine.

Six things drive the parser.

* **The filename convention is not stable and must be scraped, not built.**
  Inside the 2026 cycle alone Connecticut has used two: the Wayback capture of
  the 2026 Voter Data page from 2026-08-01 links `abs_detail_073126.xlsx`
  (MMDDYY), while the same page today links `absentee_ballot_09032026.xlsx` and
  `early_voting_09032026.xlsx` (MMDDYYYY). Both are handled, but the index page
  is the entry point and the constructed names are only a fallback.

* **Only the current election's files stay up.** Sweeping every date from
  2026-07-20 to 2026-09-06 finds files on 08/14 through 09/03 -- the September 1
  special primary -- and nothing for the August 11 statewide primary, whose files
  have been deleted. Sweeping the 2024 and 2022 folder names finds nothing at
  all. So there is no archive, `fetch_history` says so, and the daily run is the
  only way Connecticut's curve gets recorded.

* **Which election a file belongs to is checked twice.** The folder is shared
  across every election in a cycle, so a September run must not read the special
  primary's ballots as general-election early voting. The filename's date must
  fall inside the general's own reporting window, AND the ballots' own dates must
  mostly fall inside it too. Outside either, this is `NotYetPublished` -- the
  general's file is not up yet -- not an error.

* **Connecticut skips days.** In the verified 2026 run there is no file for
  08/15, 08/16, 08/18, 08/20-08/23 or 09/02. A run that asked only for today's
  file would report "nothing yet" with a good snapshot sitting on the server, so
  the index is read for anything within `LOOKBACK_DAYS`.

* **`RETURN_TYPE` is read for one word only: "Void".** The two files already ARE
  the method split, so the return-type vocabulary (`Mail`, `Drop Box 3`,
  `In Person By Voter`, `In Person by Designee or Family`, `Supervised`, and
  blank for a ballot not yet back) never has to be interpreted, and an unfamiliar
  value is not drift -- nothing is bucketed by it. A voided ballot is dropped
  from every count, issued and returned alike.

* **Party is a real registration** -- Connecticut enrols by party -- but it is
  spelled out in full (`Democratic`), and Connecticut recognises minor parties
  whose names the shared vocabulary does not know and, worse, one it would get
  BACKWARDS: in Connecticut the "Independent Party" is a registered party, while
  `normalize.party("independent")` returns `npa`. So `PARTY_ALIASES` below is
  consulted FIRST, and only labels in neither table raise SchemaDrift.
"""

from __future__ import annotations

import io
import logging
import re
from collections import defaultdict
from datetime import date, datetime, timedelta
from html import unescape
from urllib.parse import urljoin

import openpyxl

from ..calendar import election_date
from ..normalize import PARTY_DEM, PARTY_NPA, PARTY_OTH, PARTY_REP
from ..normalize import party as normalize_party
from ..schema import TIER_SCRAPER, CountyDay, StateDay, TownDay
from . import _towns
from ._net import Missing, get, looks_like_html, looks_like_xlsx
from .base import Adapter, FetchResult, NotYetPublished, SchemaDrift, SourceError

log = logging.getLogger(__name__)

HOST = "https://portal.ct.gov"

#: VERIFIED 2026-09-06, live, from this network:
#:   https://portal.ct.gov/sots/election-services/2026-voter-data              200, 27,056 B, real page
#:   https://portal.ct.gov/sots/election-services/2024-voter-data              200 but "<title>404 Error Page</title>"
#:   https://portal.ct.gov/sots/election-services/2022-voter-data              200 but "<title>404 Error Page</title>"
#: portal.ct.gov never sends a 404 status: a missing page and a missing file
#: both come back 200 with that title, which is why `_is_soft_404` exists.
INDEX_URL = HOST + "/sots/election-services/{cycle}-voter-data"

#: VERIFIED 2026-09-06, all real xlsx unless noted:
#:   .../2026_absentee_ballot_data/early_voting_09032026.xlsx      200, 81,797 B
#:   .../2026_absentee_ballot_data/absentee_ballot_09032026.xlsx   200, 47,911 B
#:   .../2026_absentee_ballot_data/early_voting_08252026.xlsx      200, 31,924 B  (first early file)
#:   .../2026_absentee_ballot_data/absentee_ballot_08142026.xlsx   200, 24,508 B  (first absentee file)
#:   .../2026_absentee_ballot_data/early_voting_09042026.xlsx      200 soft-404 HTML, 21,880 B
#:   .../2024_absentee_ballot_data/early_voting_11052024.xlsx      200 soft-404 HTML  (no 2024 archive)
#:   .../2022_absentee_ballot_data/absentee_ballot_11072022.xlsx   200 soft-404 HTML  (no 2022 archive)
#: UNVERIFIED for the general: no general-election file has ever been observed,
#: because Connecticut deletes each election's files when the next one starts.
#: The layout below is from the September 2026 special primary's files.
MEDIA_DIR = HOST + "/-/media/sots/electionservices/{cycle}_absentee_ballot_data/"

#: Filename patterns Connecticut has actually used, per kind. Fallback only --
#: `_index_files` scrapes the page first.
FILENAME_PATTERNS = {
    "inperson": ("early_voting_{mmddyyyy}.xlsx",),
    "mail": ("absentee_ballot_{mmddyyyy}.xlsx", "abs_detail_{mmddyy}.xlsx"),
}

#: How a SCRAPED filename says which of the two files it is. Checked in this
#: order, so "early_voting_..." cannot be mistaken for an absentee file. These
#: are wider than FILENAME_PATTERNS on purpose: classifying a link the page
#: already gave us costs nothing if the guess is unused, whereas constructing a
#: URL from a guess would be fabricating one. ("ev_detail" is the only entry
#: here that has never been seen in the wild; it is the symmetric partner of the
#: verified "abs_detail".)
KIND_MARKERS = (("inperson", ("early_vot", "early-vot", "ev_detail")),
                ("mail", ("absentee", "abs_detail", "abs_")))

#: Header text (whitespace-collapsed, uppercased) -> the role we need it for.
FIELD_ALIASES = {
    "RESIDENCE ADDRESS CITY": "town",
    "PARTY": "party",
    "DT MAILED": "mailed",
    "DT RETURNED": "returned",
    "RETURN_TYPE": "return_type",
}
REQUIRED_ROLES = ("town", "party", "mailed", "returned", "return_type")

#: The one `RETURN_TYPE` value that changes a count. See the module docstring.
VOID = "void"

#: Connecticut's party labels, consulted BEFORE `normalize.party`.
#:
#: "Democratic" is the only one verified in a real file (every row of the
#: September 2026 special DEMOCRATIC primary's two workbooks). The rest are the
#: registered parties named in the Secretary of the State's own Minor Party Key,
#: page 8 of
#: `portal.ct.gov/-/media/sots/electionservices/2025/registration-and-enrollment/nov25re.pdf`
#: (verified 200, 174,325 B): Green, Libertarian, Working Families, Independent,
#: Independence, Bottom Line, Concerned Citizens, We The People, Reform, Open.
#: Their spelling in the ballot file is UNVERIFIED -- no general-election file
#: has ever been observable -- so anything not here and not known to
#: `normalize.party` raises SchemaDrift rather than being bucketed.
#:
#: The entries that matter are the two the shared vocabulary would get wrong:
#: Connecticut's "Independent" is the Independent Party of Connecticut, a
#: registered party, NOT an unaffiliated voter (whom Connecticut calls
#: "Unaffiliated"); and "Independence" is a different registered party again.
PARTY_ALIASES = {
    "independent": PARTY_OTH,
    "independent party": PARTY_OTH,
    "independence": PARTY_OTH,
    "independence party": PARTY_OTH,
    "bottom line": PARTY_OTH,
    "concerned citizens": PARTY_OTH,
    "we the people": PARTY_OTH,
    "open": PARTY_OTH,
    "green party": PARTY_OTH,
    "working families party": PARTY_OTH,
    "libertarian party": PARTY_OTH,
    "reform party": PARTY_OTH,
}

_PARTY_FIELD = {
    PARTY_DEM: "party_dem", PARTY_REP: "party_rep",
    PARTY_NPA: "party_npa", PARTY_OTH: "party_oth",
}

#: How far before Election Day a file's own DATE may be and still be the
#: general's. Connecticut mails absentee ballots from 31 days out and runs 14
#: days of early voting, so the general's first file lands about five weeks
#: before; the September special primary's files, which live in the same folder,
#: are 60+ days out and are refused by this.
FILE_WINDOW_DAYS = 45

#: And how long after Election Day a file may still be updated.
FILE_TAIL_DAYS = 20

#: The second, independent check: the BALLOTS' own dates. A file whose ballots
#: were mostly mailed or returned outside this window belongs to another
#: election whatever its filename says.
BALLOT_WINDOW_DAYS = 60

#: Share of a file's dated ballots that must fall inside that window. Not an
#: all-test: UOCAVA ballots are issued months early.
BALLOT_WINDOW_SHARE = 0.75

#: Connecticut skips days -- verified above. Look back this far for the newest
#: file rather than reporting "nothing yet".
LOOKBACK_DAYS = 7

#: How far back the published curve runs. Ballots dated before this are real and
#: are folded into the first day's cumulative total rather than dropped.
MAX_SPAN_DAYS = 120

#: Share of counted ballots whose municipality must resolve to a census GEOID
#: before the town and county tables are trusted. Connecticut's 169 towns all
#: appear in `_towns`, and the file's `RESIDENCE ADDRESS CITY` is the town of
#: registration rather than a postal city -- every row of the Enfield file says
#: "Enfield", not "Thompsonville" or "Hazardville". A drop below this floor means
#: that stopped being true, which is drift, not a few odd rows.
MIN_TOWN_COVERAGE = 0.95

#: Share of counted ballots that must carry a party we could resolve before the
#: party columns are published at all. A blank `PARTY` cell is not a party
#: bucket, and four party numbers that quietly add up to well under the total
#: are worse than no party breakdown -- see THE BLANK RULE in schema.py.
MIN_PARTY_COVERAGE = 0.95

#: How many unmapped municipality names to name in the log line.
UNMAPPED_LOG_LIMIT = 12

_ANCHOR = re.compile(r'<a\b[^>]*href="([^"]+)"', re.IGNORECASE)
_SOFT_404 = re.compile(r"<title>\s*404\s*Error", re.IGNORECASE)
_DATE8 = re.compile(r"(\d{8})(?!\d)")
_DATE6 = re.compile(r"(\d{6})(?!\d)")


# --------------------------------------------------------------------------
# Index scraping
# --------------------------------------------------------------------------
def _is_soft_404(body: bytes) -> bool:
    """portal.ct.gov answers a missing page or file with 200 and this title."""
    return bool(_SOFT_404.search(body[:4096].decode("utf-8", errors="replace")))


def _kind_of(filename: str) -> str | None:
    lower = filename.lower()
    for kind, markers in KIND_MARKERS:
        if any(marker in lower for marker in markers):
            return kind
    return None


def _date_of(filename: str) -> date | None:
    """The date stamped into a Connecticut filename, MMDDYYYY or MMDDYY."""
    stem = filename.rsplit(".", 1)[0]
    hit = _DATE8.search(stem)
    if hit:
        try:
            return datetime.strptime(hit.group(1), "%m%d%Y").date()
        except ValueError:
            return None
    hit = _DATE6.search(stem)
    if hit:
        try:
            return datetime.strptime(hit.group(1), "%m%d%y").date()
        except ValueError:
            return None
    return None


def index_files(page: bytes, cycle: int) -> dict[str, list[tuple[date, str]]]:
    """kind -> [(file date, absolute URL)], newest first, from the Voter Data page."""
    html = page.decode("utf-8", errors="replace")
    found: dict[str, dict[date, str]] = {"inperson": {}, "mail": {}}
    for href in _ANCHOR.findall(html):
        url = urljoin(HOST, unescape(href))
        path = url.split("?", 1)[0]
        if not path.lower().endswith(".xlsx"):
            continue
        filename = path.rsplit("/", 1)[-1]
        kind = _kind_of(filename)
        day = _date_of(filename)
        if kind is None or day is None:
            continue
        # The query string carries Sitecore's rev/hash; the bare path serves the
        # same bytes (verified), so it is dropped to keep the cache key stable.
        found[kind].setdefault(day, path)
    listed = {kind: sorted(days.items(), reverse=True) for kind, days in found.items()}
    log.debug("CT: the %s voter-data page lists %s", cycle,
              {kind: [day.isoformat() for day, _ in entries] for kind, entries in listed.items()})
    return listed


def constructed(cycle: int, kind: str, day: date) -> list[str]:
    """The verified literal filename patterns for one kind on one day."""
    stamps = {"mmddyyyy": day.strftime("%m%d%Y"), "mmddyy": day.strftime("%m%d%y")}
    directory = MEDIA_DIR.format(cycle=cycle)
    return [directory + pattern.format(**stamps) for pattern in FILENAME_PATTERNS[kind]]


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------
def _party(raw) -> str | None:
    """A canonical party bucket, or None when the row states no enrolment."""
    label = " ".join(str(raw or "").split()).lower()
    if not label:
        return None
    bucket = PARTY_ALIASES.get(label) or normalize_party(label)
    if bucket is None:
        raise SchemaDrift(f"CT: unrecognised party {raw!r}")
    return bucket


def _day(raw) -> date | None:
    """A date out of "2026-08-30", a real datetime, or "08/30/2026"."""
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw
    token = str(raw).strip().split(" ")[0]
    if not token:
        return None
    for pattern in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(token, pattern).date()
        except ValueError:
            continue
    return None


class _Bucket:
    """One geography's tallies for one day."""

    __slots__ = ("issued", "returned", "inperson", "party")

    def __init__(self) -> None:
        self.issued = 0      # absentee ballots MAILED that day
        self.returned = 0    # absentee ballots RETURNED that day
        self.inperson = 0    # in-person early votes cast that day
        self.party: dict[str, int] = defaultdict(int)

    @property
    def total(self) -> int:
        return self.returned + self.inperson

    def add(self, other: "_Bucket") -> None:
        self.issued += other.issued
        self.returned += other.returned
        self.inperson += other.inperson
        for key, count in other.party.items():
            self.party[key] += count


class Tally:
    """Everything one pass over Connecticut's ballot rows produced."""

    def __init__(self) -> None:
        self.by_day: dict[date, _Bucket] = defaultdict(_Bucket)
        self.by_town: dict[tuple[str, date], _Bucket] = defaultdict(_Bucket)
        self.town_names: dict[str, str] = {}
        self.unmapped: dict[str, int] = defaultdict(int)
        self.counted = 0        # ballots placed on the RETURNS/early-vote curve
        self.placed = 0         # rows that landed in any bucket at all
        self.unenrolled = 0     # counted ballots with a blank PARTY cell
        self.ballot_days: list[date] = []
        self.kinds: set[str] = set()


def _header(row) -> dict[str, int]:
    index: dict[str, int] = {}
    for position, cell in enumerate(row):
        role = FIELD_ALIASES.get(" ".join(str(cell or "").split()).upper())
        if role and role not in index:
            index[role] = position
    missing = [role for role in REQUIRED_ROLES if role not in index]
    if missing:
        got = [" ".join(str(c or "").split()) for c in row][:40]
        raise SchemaDrift(f"CT: ballot file header is missing {missing}: {got!r}")
    return index


def read(body: bytes, kind: str, tally: Tally) -> None:
    """Fold one Connecticut ballot file into `tally`.

    `kind` is "inperson" for the early-voting file and "mail" for the absentee
    file. The two are counted differently and that difference is the whole
    method split -- see the module docstring.
    """
    if looks_like_html(body) or not looks_like_xlsx(body):
        raise SourceError(f"CT: the {kind} ballot file is not an xlsx")
    try:
        workbook = openpyxl.load_workbook(io.BytesIO(body), read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001 -- openpyxl raises a zoo of types
        raise SourceError(f"CT: could not open the {kind} ballot workbook: {exc}") from exc

    sheet = workbook.worksheets[0]
    rows = sheet.iter_rows(values_only=True)
    try:
        header = next(rows)
    except StopIteration as exc:
        raise SchemaDrift(f"CT: the {kind} ballot file is empty") from exc
    index = _header(header)
    tally.kinds.add(kind)

    seen = 0
    for row in rows:
        if row is None or all(cell is None for cell in row):
            continue
        seen += 1
        return_type = " ".join(str(row[index["return_type"]] or "").split()).lower()
        if return_type == VOID:
            # Issued and cancelled. Not a ballot in any count.
            continue

        # Validated on every row, counted or not, so a vocabulary change shows up
        # wherever it lands rather than only when it hits a counted ballot.
        party = _party(row[index["party"]])
        name, town = _resolve(row[index["town"]], tally)

        mailed = _day(row[index["mailed"]])
        returned = _day(row[index["returned"]])

        # A row with no usable date lands in no bucket, so it is neither placed
        # nor held against the town-coverage floor -- that floor is about names
        # we could not resolve, not about dates the clerk did not key.
        if returned is not None or (kind == "mail" and mailed is not None):
            tally.placed += 1
            if town is None:
                tally.unmapped[name] += 1

        if kind == "mail":
            if mailed is not None:
                tally.ballot_days.append(mailed)
                tally.by_day[mailed].issued += 1
                if town is not None:
                    tally.by_town[(town, mailed)].issued += 1
            if returned is None:
                # Issued but not back yet. Real, and not on the returns curve.
                continue
            field = "returned"
        else:
            if returned is None:
                # An early vote with no date cannot be placed on the curve.
                continue
            field = "inperson"

        tally.ballot_days.append(returned)
        tally.counted += 1
        if party is None:
            tally.unenrolled += 1

        bucket = tally.by_day[returned]
        setattr(bucket, field, getattr(bucket, field) + 1)
        if party:
            bucket.party[party] += 1
        if town is not None:
            town_bucket = tally.by_town[(town, returned)]
            setattr(town_bucket, field, getattr(town_bucket, field) + 1)
            if party:
                town_bucket.party[party] += 1

    if not seen:
        raise SchemaDrift(f"CT: the {kind} ballot file has a header but no rows")


def _resolve(raw, tally: Tally) -> tuple[str, str | None]:
    """(the name as written, its 10-digit GEOID or None).

    None is never guessed into a county: the caller counts the miss and the row
    is excluded from both the town and the county table. `read` does the
    counting rather than this function, so a row that lands in no bucket at all
    is not held against the coverage floor.
    """
    name = " ".join(str(raw or "").split())
    if not name:
        return "", None
    hit = _towns.lookup("CT", name)
    if hit is None:
        return name, None
    geoid, canonical = hit
    tally.town_names[geoid] = canonical
    return name, geoid


def belongs_to(tally: Tally, cycle: int) -> bool:
    """Do these ballots belong to `cycle`'s general election?

    The folder is shared by every election in a cycle and only the current one's
    files are kept, so a run in September would otherwise read a special
    primary's ballots as general-election early voting.
    """
    if not tally.ballot_days:
        return False
    day_zero = election_date(cycle)
    opens = day_zero - timedelta(days=BALLOT_WINDOW_DAYS)
    closes = day_zero + timedelta(days=FILE_TAIL_DAYS)
    inside = sum(1 for day in tally.ballot_days if opens <= day <= closes)
    return inside / len(tally.ballot_days) >= BALLOT_WINDOW_SHARE


def in_file_window(day: date, cycle: int) -> bool:
    day_zero = election_date(cycle)
    return (day_zero - timedelta(days=FILE_WINDOW_DAYS)
            <= day <= day_zero + timedelta(days=FILE_TAIL_DAYS))


# --------------------------------------------------------------------------
# Emission
# --------------------------------------------------------------------------
def _report_coverage(tally: Tally) -> bool:
    """Log what did not map; refuse the file if too little did. Returns whether
    the party columns may be published."""
    lost = sum(tally.unmapped.values())
    placed = tally.placed
    coverage = 1.0 - (lost / placed) if placed else 0.0
    if tally.unmapped:
        worst = sorted(tally.unmapped.items(), key=lambda kv: -kv[1])
        shown = ", ".join(f"{name or '<blank>'} ({count})" for name, count in worst[:UNMAPPED_LOG_LIMIT])
        log.warning(
            "CT: %d municipalit%s (%d rows) have no census county-subdivision "
            "GEOID and are excluded from the town and county tables: %s%s",
            len(tally.unmapped), "y" if len(tally.unmapped) == 1 else "ies", lost, shown,
            "" if len(worst) <= UNMAPPED_LOG_LIMIT else f", +{len(worst) - UNMAPPED_LOG_LIMIT} more",
        )
    log.info("CT: %d towns mapped, %.2f%% of %d placed ballots keyed to a GEOID",
             len(tally.town_names), 100 * coverage, placed)
    if placed and coverage < MIN_TOWN_COVERAGE:
        raise SchemaDrift(
            f"CT: only {100 * coverage:.1f}% of ballots map to a census "
            f"county-subdivision GEOID (floor is {100 * MIN_TOWN_COVERAGE:.0f}%); "
            f"the residence-city column or its vocabulary has changed"
        )

    counted = tally.counted
    enrolled = 1.0 - (tally.unenrolled / counted) if counted else 0.0
    if counted and enrolled < MIN_PARTY_COVERAGE:
        log.warning(
            "CT: %d of %d counted ballots (%.1f%%) have a blank PARTY cell; "
            "publishing no party breakdown rather than four numbers that do not "
            "add up to the total",
            tally.unenrolled, counted, 100 * (1 - enrolled),
        )
        return False
    return True


def _walk(series: dict[date, _Bucket], span: list[date], start: date):
    """Yield (day, today, running) along `span` for one geography."""
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


def _regroup(by_key: dict[tuple[str, date], _Bucket]) -> dict[str, dict[date, _Bucket]]:
    out: dict[str, dict[date, _Bucket]] = defaultdict(dict)
    for (geography, day), bucket in by_key.items():
        out[geography][day] = bucket
    return out


def emit(tally: Tally, cycle: int, through: date, *, with_party: bool) -> FetchResult:
    result = FetchResult()
    _towns.attach(result, [])
    days = [day for day in tally.by_day if day <= through]
    if not days:
        return result

    day_zero = election_date(cycle)
    start = max(min(days), day_zero - timedelta(days=MAX_SPAN_DAYS))
    span = [start]
    while span[-1] < through:
        span.append(span[-1] + timedelta(days=1))

    def party_fields(running: _Bucket) -> dict[str, int | None]:
        if not with_party:
            return {field: None for field in _PARTY_FIELD.values()}
        # Connecticut enrols by party and this file names it on every ballot, so
        # a bucket with no ballots yet is a real 0 -- blank would claim
        # Connecticut does not report party at all.
        return {field: running.party.get(key, 0) for key, field in _PARTY_FIELD.items()}

    # The absentee file is the only one carrying issued-but-not-returned
    # ballots. If it was not read, `mail_requested` and `mail_returned` are not
    # reported rather than zero -- and the same the other way for `inperson`.
    saw_mail = "mail" in tally.kinds
    saw_inperson = "inperson" in tally.kinds

    running = _Bucket()
    for day, bucket in sorted(tally.by_day.items()):
        if day < start:
            running.add(bucket)
    for day in span:
        today = tally.by_day.get(day)
        if today:
            running.add(today)
        result.state_rows.append(StateDay(
            cycle=cycle, state="CT", day=day,
            ballots_total=running.total,
            ballots_new=today.total if today else 0,
            mail_requested=running.issued if saw_mail else None,
            mail_returned=running.returned if saw_mail else None,
            inperson=running.inperson if saw_inperson else None,
            **party_fields(running),
        ))

    if not tally.by_town:
        return result

    by_county: dict[tuple[str, date], _Bucket] = defaultdict(_Bucket)
    for (geoid, day), bucket in tally.by_town.items():
        by_county[(_towns.county_of(geoid), day)].add(bucket)

    town_rows: list[TownDay] = []
    for geoid, series in sorted(_regroup(tally.by_town).items()):
        for day, today, walked in _walk(series, span, start):
            town_rows.append(TownDay(
                cycle=cycle, state="CT", town_geoid=geoid, day=day,
                town_name=tally.town_names.get(geoid, ""),
                ballots_total=walked.total,
                ballots_new=today.total if today else 0,
                mail_returned=walked.returned if saw_mail else None,
                inperson=walked.inperson if saw_inperson else None,
                **party_fields(walked),
            ))
    _towns.attach(result, town_rows)

    # Exact, not inferred: the county is digits 3-5 of the town's own GEOID.
    for fips, series in sorted(_regroup(by_county).items()):
        for day, today, walked in _walk(series, span, start):
            result.county_rows.append(CountyDay(
                cycle=cycle, state="CT", county_fips=fips, day=day,
                county_name=_towns.county_name_of("CT", fips),
                ballots_total=walked.total,
                ballots_new=today.total if today else 0,
                mail_returned=walked.returned if saw_mail else None,
                inperson=walked.inperson if saw_inperson else None,
                **party_fields(walked),
            ))
    return result


def build(files: list[tuple[str, bytes]], cycle: int, through: date) -> FetchResult:
    """Parse one day's pair of Connecticut files into canonical rows."""
    tally = Tally()
    for kind, body in files:
        read(body, kind, tally)
    if not tally.ballot_days:
        raise SchemaDrift("CT: the ballot files carry no parseable dates")
    if not belongs_to(tally, cycle):
        raise NotYetPublished(
            f"CT: the posted ballot files are for another election "
            f"(ballot dates run {min(tally.ballot_days).isoformat()}.."
            f"{max(tally.ballot_days).isoformat()}, not the {cycle} general)"
        )
    with_party = _report_coverage(tally)
    return emit(tally, cycle, through, with_party=with_party)


class CTScraper(Adapter):
    """Tier 1 for Connecticut: the SOTS per-ballot early-voting and absentee files.

    Statewide, town and county rows, with party and a mail/in-person split.
    Towns are the real unit in Connecticut and the counties are summed from
    them; see the module docstring for what does not map and where it goes.
    """

    state = "CT"
    name = "ct-sots"
    tier = TIER_SCRAPER

    def _stamped(self, result: FetchResult) -> FetchResult:
        """Stamp the town rows -- `FetchResult.stamp()` does not know about them."""
        return _towns.stamp(result, self.provenance())

    def _index(self, cycle: int) -> dict[str, list[tuple[date, str]]]:
        url = INDEX_URL.format(cycle=cycle)
        try:
            page = get(url, state="CT", filename=f"{cycle}-voter-data.html")
        except Missing as exc:
            raise SourceError(f"CT: {url} is gone") from exc
        if _is_soft_404(page):
            # We could not LOOK. That is a fall-through, never an absence.
            raise SourceError(f"CT: {url} answered portal.ct.gov's 404 page")
        return index_files(page, cycle)

    def _download(self, url: str) -> bytes | None:
        try:
            body = get(url, state="CT", filename=url.rsplit("/", 1)[-1], min_bytes=1024)
        except Missing:
            return None
        return None if looks_like_html(body) else body

    def _pick(self, index: dict[str, list[tuple[date, str]]], kind: str,
              cycle: int, as_of: date) -> tuple[date, bytes] | None:
        """The newest usable file of one kind at or before `as_of`."""
        earliest = as_of - timedelta(days=LOOKBACK_DAYS)
        for day, url in index.get(kind, ()):
            if not (earliest <= day <= as_of) or not in_file_window(day, cycle):
                continue
            body = self._download(url)
            if body is not None:
                return day, body
        # Only if the index named nothing usable: Connecticut has changed the
        # filename convention mid-cycle before, so a page that has not been
        # updated is not proof the file is absent.
        day = as_of
        while day >= earliest:
            if in_file_window(day, cycle):
                for url in constructed(cycle, kind, day):
                    body = self._download(url)
                    if body is not None:
                        log.info("CT: %s file found by pattern at %s", kind, url)
                        return day, body
            day -= timedelta(days=1)
        return None

    def fetch(self, cycle: int, as_of: date) -> FetchResult:
        if not in_file_window(as_of, cycle):
            raise NotYetPublished(
                f"CT: {as_of.isoformat()} is outside the {cycle} general's "
                f"reporting window (opens {FILE_WINDOW_DAYS} days out)"
            )
        index = self._index(cycle)
        found: list[tuple[str, bytes]] = []
        dates: list[date] = []
        for kind in ("mail", "inperson"):
            hit = self._pick(index, kind, cycle, as_of)
            if hit is None:
                continue
            day, body = hit
            found.append((kind, body))
            dates.append(day)
        if not found:
            raise NotYetPublished(
                f"CT: no {cycle} general ballot file posted on or before "
                f"{as_of.isoformat()}"
            )
        # The series can only speak for the OLDER of the two files: the newer
        # one's extra days would otherwise be published with the other method
        # frozen, which reads as a day on which nobody voted by mail.
        through = min(dates)
        log.info("CT: read %s through %s", [k for k, _ in found], through.isoformat())
        return self._stamped(build(found, cycle, through))

    def fetch_history(self, cycle: int) -> FetchResult:
        """There is no archive. Verified, not assumed.

        Every date from 2026-07-20 to 2026-09-06 was probed in the 2026 folder:
        files exist only for 2026-08-14 through 2026-09-03, the run-up to the
        September 1 special primary. The August 11 statewide primary's files are
        gone, and both `2024_absentee_ballot_data/` and
        `2022_absentee_ballot_data/` answer portal.ct.gov's soft 404 for every
        filename in either convention. Connecticut keeps the current election's
        files and nothing else.
        """
        raise NotYetPublished(
            f"CT: the Secretary of the State keeps only the current election's "
            f"ballot files; there is no {cycle} archive to backfill from"
        )
