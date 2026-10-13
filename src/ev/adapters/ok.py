"""Oklahoma: the State Election Board's DevExpress statistics dashboards.

THE FINDING THIS MODULE EXISTS TO CORRECT
-----------------------------------------
`docs/coverage-research.md` recorded Oklahoma as "an undocumented dashboard JSON
API ... `DashboardItemGetAction` answers HTTP 500 to every reconstructed query
shape ... it needs the DevExpress client's internal state blob", and rejected it
as too fragile to build.

**There is no state blob.** The dashboard's own browser makes a plain **GET**:

    GET /dashboardControl/data/DashboardItemGetAction
        ?dashboardId=AbsStatsByCounty
        &itemId=pivotDashboardItem1
        &query={"Filter":[{"dimensions":[...],"values":[[...]]}]}

Captured from the live page 2026-09-06 and replayed byte-for-byte from
`requests` -- HTTP 200, `application/json`, no cookie, no token, no session. The
500s came from POSTing. `stats.okelections.gov` is not behind any bot
protection either: plain `requests` and `curl_cffi` get identical answers.

WHAT IS BEHIND IT
-----------------
Two dashboards matter, and they measure different things:

* **`AbsStatsByCounty`** -- table `StatAbsentee`. County x party x ballot type x
  application source x delivery method, with `Sent` / `Received` / `Rejected`.
  This is ABSENTEE ONLY: Oklahoma's early in-person voting is not in it (2024
  general: 130,640 sent, 107,874 received, against 293,918 early in-person
  ballots the same year). **It is live for the 2026 general today** -- 428
  applications across six counties on 2026-09-06 -- which makes it the
  during-season feed.
* **`VHCountsByCounty`** -- table `StatVoterHistory`. County x precinct x party
  x `VotingMethod`, where the methods are `Absentee`, `Early Voting`,
  `Election Day` and `Protected`. This is voter-history CREDIT, i.e. ballots
  actually counted, and it is the only place the early in-person number exists.
  Verified totals: 2024 general 107,549 absentee + 293,918 early; 2022 general
  71,680 + 132,402; 2020 general 287,010 + 167,132.

  Its `PrecinctCode` dimension is declared in the pivot's row hierarchy and its
  EncodeMap comes back EMPTY, so COUNTY is the finest unit this source can
  actually return. There are no Oklahoma precinct rows to chase.

THE ARCHIVE ROUTE, PART ONE: THE CERTIFIED FINAL
------------------------------------------------
`VHCountsByCounty` is a permanent voter-history table, not a live feed: it
carries every election back to 2017, the 2020, 2022 and 2024 generals included
(verified live 2026-09-08). `fetch_history` reads it for a past cycle and gets
the certified early-vote position -- county, party, mail vs in-person -- for all
77 counties, as ONE row per county dated Election Day. Neither dashboard has a
within-election date dimension, so that is all the dashboards can say about a
past cycle. `AbsStatsByCounty` reaches back only to 2022 and is not used for
history at all.

THE ARCHIVE ROUTE, PART TWO: THE DAILY CURVE IS IN PDFs
-------------------------------------------------------
The daily curve exists after all -- not on `stats.okelections.gov` but on the
Election Board's CMS page at `INDEX_URL`, which links (2026-10-13: ~121) PDFs
grouped by election, each one a MESA report run at a stated moment during the
voting period. Two report types matter, one PDF per run, 78 pages each: 77
county pages and a `99 STATE` page.

* **`ap2520` "MESA - Ballot Totals by Affiliation"** -- MAIL absentee only:
  `Sent`, `Returned Undel.`, `Received`, by county x party. `Received` is the
  mail_returned reading; `Sent` is mail_requested.
* **`ap2541` "MESA - Absentee In-person Totals by Affiliation"** -- EARLY
  IN-PERSON ballots cast, by county x party.
* ⚠️ **`ap2680` "Absentee Inperson Statistics by Affiliation"** -- the first two
  2024 in-person runs (10/30 4:32 PM and 10/31 8:00 AM) were cut from a
  different template: three WIDE pages, one row per county with the four party
  columns and a Total, a `Grand Total` row for the state, and a BLANK cell
  where the standard layout omits the row. `_parse_wide` reads it by column
  position; `parse_pages` picks the reader by the PRINTED TITLE, never by the
  filename or the folder.

The traps, each of which this module was built around:

* **DATE BY THE PRINTED RUN STAMP, NEVER BY THE FILENAME.** Every page header
  carries the run moment (`11/4/2024 8:34 AM`). The filenames are the
  webmaster's and lie: `absentee-inperson-totals-20241030-2.pdf` is the run of
  10/31/2024 8:00 AM, and the index labels ("Posted November 1, 2024 - Run
  Time 11:37 p.m") are typed by hand. Several days carry more than one run
  (11/1/2024 has three of each type); the LAST run of a day is that day's
  reading, exactly as ca.py keeps the last capture of a day.
* **TWO FILES ARE CROSS-FILED IN THE WRONG FOLDER.** The 2024 mail final
  (`ballot-totals-by-affiliation-20241104-1.pdf`, 8:34 AM) sits in the
  in-person folder and the in-person final (`...-20241104-1.pdf`, 8:32 AM) in
  the mail folder. So the folder says nothing, the filename says nothing, and
  only the title printed on page 1 says what a PDF is. The walk enumerates the
  index page's anchors under the cycle's "General Election" heading rather
  than constructing paths, and then still checks each PDF's printed
  `Election Date` against `calendar.election_date(cycle)` and its run date
  against the cycle's window, because the index also carries 2026 primary and
  runoff files and three years of special elections.
* **ZERO-PARTY ROWS ARE OMITTED, NOT PRINTED AS 0.** Adair's 2024 mail page has
  no Libertarian line at all. Oklahoma registers all four affiliations and the
  report's own `99 STATE` page lists them, so an omitted party is a genuine 0
  (the convention `fetch` already uses off the absentee dashboard), never a
  blank. Every county block prints its own total line, and the party rows
  must add up to it -- `SchemaDrift` when they do not, so a row this reader
  failed to match cannot quietly shrink a county.
* **THE STATEWIDE ROW IS THE `99 STATE` PAGE,** not a county sum -- but the 77
  county pages are summed anyway and must agree with it, drift otherwise. A
  county page the regexes skipped would otherwise read as a smaller Oklahoma.
* **ONE REPORT TYPE ALONE IS A REAL READING.** Mail reports start before
  in-person voting opens (2024: mail from 10/29, in-person from 10/30; 2022:
  mail from 10/24, in-person from 11/2). On a mail-only day `inperson` is None
  -- not 0 -- and `ballots_total` is the mail count, which is precisely what
  `fetch` publishes off the absentee dashboard while voter history is empty.
  The two conventions are the same convention.

Each day's rows carry the party x method crosstab too (`schema.MethodDay`,
via `_methods`): the mail report is the `mail` band and the in-person report
the `inperson` band, each by party, each absent on a day that report does not
exist rather than present as zeros.

Election Day itself belongs to voter history. 2022's last runs were cut at
1:56 PM on Election Day, and that partial reading would collide with the
certified row on the same date -- and WIN the merge, because it carries
`mail_requested` and the certified row does not. So `fetch_history` drops any
PDF reading dated Election Day when voter history covers the cycle, and the
curve ends on the certified final.

Archived PDFs never change, so they are cached under `cache/ok/` and served
from disk on a re-run (`use_cache=True`, the rule ca.py's archive walk uses);
the index page is fetched fresh every time because it is what changes.

`VotingMethod` has no 2026-general rows yet (its newest election is the
2026-08-25 runoff), so which of the two answers on a given day decides what this
adapter can report:

* voter history present -> ballots cast, split mail vs in-person, by party.
* absentee only -> mail requested and mail returned, by party, and `inperson`
  stays **None**. Not 0 -- Oklahoma's early in-person voting is real and simply
  is not in that table. See THE BLANK RULE in schema.py.

Oklahoma registers voters by party and the dashboards carry all four
registrations -- Democrat, Republican, Independent and Libertarian -- so every
party field here is a real count, and a party with no ballots yet is a genuine 0.

WHAT IS NOT COUNTED, AND WHY
----------------------------
`Election Day` credits are excluded, obviously. `Protected` -- Oklahoma's
address-confidential voters -- is excluded too, because the table does not say
how or when those ballots were cast. It has never appeared in a November
general (verified for 2022, 2024 and the 2026 runoff: three methods each), and
`_check_methods` raises `SchemaDrift` if a method label ever turns up that this
module has not been taught, rather than letting it vanish.

Neither dashboard carries an as-of stamp, so rows are dated with the run's
`as_of`, exactly as `tx.py` does with its snapshot table.
"""

from __future__ import annotations

import hashlib
import html as _html
import io
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from urllib.parse import urljoin

import pypdf

from ..calendar import election_date
from ..normalize import (
    METHOD_INPERSON, METHOD_MAIL, PARTY_DEM, PARTY_NPA, PARTY_OTH, PARTY_REP,
    method as _method, party as _party,
)
from ..schema import TIER_SCRAPER, CountyDay, MethodDay, StateDay
from . import _fips, _methods, _net
from .base import Adapter, FetchResult, NotYetPublished, SchemaDrift, SourceError

log = logging.getLogger(__name__)

BASE = "https://stats.okelections.gov"
ITEM_URL = f"{BASE}/dashboardControl/data/DashboardItemGetAction"
#: The dashboard catalogue -- 200, application/json, 18 dashboards. Fetched to
#: prove the two ids below still exist before anything is read from them.
DASHBOARD_LIST_URL = f"{BASE}/dashboardControl/dashboards"

ABSENTEE_DASHBOARD = "AbsStatsByCounty"
HISTORY_DASHBOARD = "VHCountsByCounty"

#: Item ids, read off the live pages 2026-09-06. Each one is validated against
#: the DataMember its response reports, so a renumbered dashboard raises
#: SchemaDrift instead of quietly reading the wrong control.
ABSENTEE_PIVOT = "pivotDashboardItem1"
ABSENTEE_DATE_COMBO = "comboBoxDashboardItem2"
ABSENTEE_PARTY_COMBO = "comboBoxDashboardItem1"
HISTORY_PIVOT = "pivotDashboardItem1"
HISTORY_DATE_COMBO = "comboBoxDashboardItem1"
HISTORY_PARTY_COMBO = "comboBoxDashboardItem2"
HISTORY_METHOD_PIE = "pieDashboardItem1"

COL_ELECTION = "ElectionDate"
COL_COUNTY = "CountyDesc"
COL_PARTY = "PartyDesc"
COL_METHOD = "VotingMethod"

MEASURE_SENT = "Sent"
MEASURE_RECEIVED = "Received"
MEASURE_REJECTED = "Rejected"
MEASURE_HISTORY = "HistoryCount"
MEASURE_TOTAL_VOTES = "SumTotVotes"

#: The two credit methods that are early voting. `Election Day` is not early
#: voting; `Protected` is early voting whose method the state withholds, so it
#: cannot be filed under either bucket -- see the module docstring.
EARLY_METHODS = ("Absentee", "Early Voting")
KNOWN_METHODS = frozenset({"Absentee", "Early Voting", "Election Day", "Protected"})

#: Every Oklahoma county. Fewer than this and no statewide row is published --
#: a sum over 74 of 77 looks exactly like an Oklahoma total and is not one.
#: Same rule as tx.py and oh.py.
EXPECTED_COUNTIES = len(_fips.CENSUS_COUNTIES["OK"])

_PARTY_FIELD = {
    PARTY_DEM: "party_dem", PARTY_REP: "party_rep",
    PARTY_NPA: "party_npa", PARTY_OTH: "party_oth",
}

#: How the dashboards spell a date in a filter value and in an EncodeMap.
_FILTER_STAMP = "%Y-%m-%dT00:00:00.000"
_ENCODED_STAMP = "%Y-%m-%dT00:00:00.0000000"


def _dimension(member: str, **extra) -> dict:
    """One entry of the `dimensions` array in a `query` filter."""
    return {"@ItemType": "Dimension", "@DataMember": member,
            "@DefaultId": "DataItem0", **extra}


def _election_dimension() -> dict:
    # The date dimension is grouped and sorted in the client's own filter; the
    # server rejects the value unless the grouping matches.
    return _dimension(COL_ELECTION, **{
        "@DateTimeGroupInterval": "DayMonthYear", "@SortOrder": "Descending",
    })


def build_query(filters: list[tuple[dict, str]]) -> str:
    """The `query` parameter: a list of one-dimension, one-value filters."""
    return json.dumps(
        {"Filter": [{"dimensions": [dim], "values": [[value]]}
                    for dim, value in filters]},
        separators=(",", ":"),
    )


# --------------------------------------------------------------------------
# Decoding one DashboardItemGetAction response
# --------------------------------------------------------------------------
def _slot_ids(payload: dict) -> tuple[dict[str, str], dict[str, str]]:
    """(dimension DataMember -> DataItem id, measure DataMember -> id).

    Everything downstream is addressed by DATA MEMBER, never by the `DataItemN`
    ids, which are the dashboard author's private numbering and differ between
    dashboards (county is DataItem6 in the absentee pivot and DataItem0 in the
    voter-history one). Re-deriving them from each response is what makes a
    reordered dashboard fail loudly instead of transposing measures onto the
    wrong counties.
    """
    try:
        meta = payload["ItemData"]["MetaData"]
    except (KeyError, TypeError) as exc:
        raise SchemaDrift(f"OK: dashboard response has no MetaData: {exc}") from exc
    dims: dict[str, str] = {}
    for axis in (meta.get("DimensionDescriptors") or {}).values():
        for descriptor in axis or []:
            dims[descriptor["DataMember"]] = descriptor["ID"]
    measures = {d["DataMember"]: d["ID"]
                for d in (meta.get("MeasureDescriptors") or [])}
    return dims, measures


def _encode_maps(payload: dict) -> dict[str, list]:
    try:
        return payload["ItemData"]["DataStorageDTO"]["EncodeMaps"] or {}
    except (KeyError, TypeError) as exc:
        raise SchemaDrift(f"OK: dashboard response has no EncodeMaps: {exc}") from exc


def _slices(payload: dict) -> list[dict]:
    try:
        return payload["ItemData"]["DataStorageDTO"]["Slices"] or []
    except (KeyError, TypeError) as exc:
        raise SchemaDrift(f"OK: dashboard response has no Slices: {exc}") from exc


def dimension_values(payload: dict, member: str) -> list[str]:
    """Every value of one dimension, in the order its EncodeMap lists them."""
    dims, _ = _slot_ids(payload)
    if member not in dims:
        raise SchemaDrift(f"OK: dashboard response does not carry {member!r}")
    return list(_encode_maps(payload).get(dims[member]) or [])


def decode(payload: dict, keys: tuple[str, ...], measures: tuple[str, ...]) -> dict:
    """{(key values...) -> {measure: number}} for one slice of a DSR response.

    A DevExpress pivot answers with every level of its hierarchy at once: a
    grand-total slice with no keys, one slice per dimension, and one per
    combination. `keys` picks the one wanted; `()` is the grand total. Each
    slice's `Data` is keyed by "[i]" / "[i,j]" -- indexes into the EncodeMaps of
    its own KeyIds, in KeyIds order.
    """
    dims, meas = _slot_ids(payload)
    missing = [m for m in keys if m not in dims] + [m for m in measures if m not in meas]
    if missing:
        raise SchemaDrift(f"OK: dashboard response is missing columns {missing}")

    wanted = [dims[k] for k in keys]
    maps = _encode_maps(payload)
    for candidate in _slices(payload):
        if list(candidate.get("KeyIds") or []) != wanted:
            continue
        value_ids = candidate.get("ValueIds") or {}
        slots = {}
        for member in measures:
            slot = value_ids.get(meas[member])
            if slot is None:
                raise SchemaDrift(
                    f"OK: dashboard slice {keys} does not carry {member!r}"
                )
            slots[member] = str(slot)
        out: dict[tuple, dict[str, float | None]] = {}
        for raw_key, cells in (candidate.get("Data") or {}).items():
            indexes = [int(part) for part in raw_key.strip("[]").split(",") if part != ""]
            if len(indexes) != len(wanted):
                raise SchemaDrift(f"OK: dashboard row key {raw_key!r} does not fit {keys}")
            try:
                label = tuple(maps[dim][index] for dim, index in zip(wanted, indexes))
            except (KeyError, IndexError) as exc:
                raise SchemaDrift(
                    f"OK: dashboard row key {raw_key!r} indexes outside its EncodeMap"
                ) from exc
            out[label] = {m: cells.get(slots[m]) for m in measures}
        return out
    raise SchemaDrift(f"OK: dashboard response has no slice keyed by {keys}")


def _int(value) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SchemaDrift(f"OK: {value!r} is not a count")
    return int(round(value))


def _add(*values: int | None) -> int | None:
    present = [v for v in values if v is not None]
    return sum(present) if present else None


def _party_bucket(label: str) -> str:
    bucket = _party(label)
    if bucket is None:
        raise SchemaDrift(f"OK: unrecognised party registration {label!r}")
    return bucket


def _county_fips(name: str) -> tuple[str, str]:
    hit = _fips.lookup("OK", name)
    if hit is None:
        raise SchemaDrift(f"OK: unrecognised county name {name!r}")
    return hit


def total_ballots(counties: dict[str, dict]) -> int | None:
    """Every early ballot in a county map, or None if not one is reported.

    ⚠️ THE ONE ZERO GUARD, AND IT HAS TO BE SHARED. Both dashboards list an
    election in their date combos before they have anything to say about it --
    `AbsStatsByCounty` carried the 2026 general months before a ballot came back
    -- so "the election is on the dashboard" is not the same fact as "there is
    an early vote to report". `fetch` has always refused an empty absentee
    table; `fetch_history` read the SAME kind of emptiness off the voter-history
    table and would have published 77 counties of zeroes dated Election Day,
    which is a confident zero and unfixable by any later tier. See THE BLANK
    RULE in schema.py.
    """
    return _add(*[row.get("ballots_total") for row in counties.values()])


def check_methods(labels: list[str]) -> None:
    """Refuse a voting-method vocabulary this module has not been taught.

    The early-vote total is `Absentee + Early Voting` and nothing else, so a new
    label -- an "In-Person Absentee" split, say -- would silently disappear out
    of every Oklahoma number rather than showing up as a gap.
    """
    unknown = sorted(set(labels) - KNOWN_METHODS)
    if unknown:
        raise SchemaDrift(f"OK: unrecognised voting methods {unknown}")


# --------------------------------------------------------------------------
# Row assembly
# --------------------------------------------------------------------------
def _emit(
    counties: dict[str, dict],
    names: dict[str, str],
    cycle: int,
    day: date,
    *,
    statewide_extra: dict | None = None,
) -> FetchResult:
    result = FetchResult()
    running: dict[str, int | None] = {
        "ballots_total": None, "mail_requested": None,
        "mail_returned": None, "inperson": None,
    }
    party_running: dict[str, int | None] = {b: None for b in _PARTY_FIELD}

    for fips in sorted(counties):
        row = counties[fips]
        party = row.get("party") or {}
        result.county_rows.append(CountyDay(
            cycle=cycle, state="OK", county_fips=fips, day=day,
            county_name=names[fips],
            ballots_total=row.get("ballots_total"),
            ballots_new=None,
            mail_returned=row.get("mail_returned"),
            inperson=row.get("inperson"),
            party_dem=party.get(PARTY_DEM), party_rep=party.get(PARTY_REP),
            party_npa=party.get(PARTY_NPA), party_oth=party.get(PARTY_OTH),
        ))
        for field in running:
            running[field] = _add(running[field], row.get(field))
        for bucket in party_running:
            party_running[bucket] = _add(party_running[bucket], party.get(bucket))

    if len(counties) != EXPECTED_COUNTIES:
        # PARTIAL COVERAGE -- counties only, no statewide row. See
        # EXPECTED_COUNTIES.
        log.warning(
            "OK: dashboard returned %d of %d counties; publishing counties only, "
            "no statewide row (a partial sum would read as an Oklahoma total)",
            len(counties), EXPECTED_COUNTIES,
        )
        return result

    extra = statewide_extra or {}
    result.state_rows.append(StateDay(
        cycle=cycle, state="OK", day=day,
        ballots_total=running["ballots_total"],
        ballots_new=None,
        mail_requested=extra.get("mail_requested", running["mail_requested"]),
        mail_returned=running["mail_returned"],
        inperson=running["inperson"],
        party_dem=party_running[PARTY_DEM], party_rep=party_running[PARTY_REP],
        party_npa=party_running[PARTY_NPA], party_oth=party_running[PARTY_OTH],
    ))
    return result


# --------------------------------------------------------------------------
# The PDF archive -- enumeration
# --------------------------------------------------------------------------
INDEX_URL = ("https://oklahoma.gov/elections/voters/absentee-voting-statistics/"
             "absentee-voting-statistics.html")

REPORT_MAIL = "mail"
REPORT_INPERSON = "inperson"
LAYOUT_STANDARD = "standard"   # 78 pages, one county block per page
LAYOUT_WIDE = "wide"           # ap2680: one row per county, blank = 0

#: Printed title -> (report kind, layout), keyed with everything but letters
#: stripped so "In-person" and "Inperson" are the same title. The title is the
#: ONLY identity a PDF has -- see the module docstring on the cross-filed files.
_TITLES = {
    "ballottotalsbyaffiliation": (REPORT_MAIL, LAYOUT_STANDARD),
    "absenteeinpersontotalsbyaffiliation": (REPORT_INPERSON, LAYOUT_STANDARD),
    "absenteeinpersonstatisticsbyaffiliation": (REPORT_INPERSON, LAYOUT_WIDE),
}

#: How far before Election Day a general-election report can be run. Mail
#: ballots go out about 45 days ahead; the first report of 2022 was 10/24. The
#: printed `Election Date` is the real gate -- this only bounds the run date.
ARCHIVE_WINDOW_DAYS = 60

#: The four registrations every report accounts for. A report that prints no
#: row for one of them in a county is saying 0 -- see the module docstring.
REGISTRATIONS = (PARTY_DEM, PARTY_REP, PARTY_NPA, PARTY_OTH)

#: The index page is an AEM accordion: an <h2> per year, an <h3> per election
#: holding the heading text in a nested span, and the PDF anchors in the panel
#: that follows. Walked in document order so each anchor is filed under the
#: year and election headings that precede it.
_HEADING_OR_ANCHOR = re.compile(
    r"<h([1-6])\b[^>]*>(.*?)</h\1>|<a\b[^>]*href=\"([^\"]+\.pdf)\"[^>]*>(.*?)</a>",
    re.I | re.S,
)
_TAG = re.compile(r"<[^>]+>")
_GENERAL = re.compile(r"\bgeneral\b", re.I)


def _plain(markup: str) -> str:
    return " ".join(_html.unescape(_TAG.sub(" ", markup)).split())


def index_entries(page: str) -> list[tuple[int | None, str, str, str]]:
    """(year, election heading, absolute PDF URL, anchor label), page order."""
    year: int | None = None
    heading = ""
    out = []
    for match in _HEADING_OR_ANCHOR.finditer(page):
        if match.group(2) is not None:
            text = _plain(match.group(2))
            if re.fullmatch(r"\d{4}", text):
                year, heading = int(text), ""
            else:
                heading = text
            continue
        href = _html.unescape(match.group(3)).strip()
        out.append((year, heading, urljoin(INDEX_URL, href), _plain(match.group(4))))
    return out


def select_general_pdfs(page: str, cycle: int) -> list[str]:
    """The PDFs filed under `cycle`'s "General Election" heading, in page order.

    Both report types come back together -- the folder a file sits in is not
    trusted to say which it is. Duplicated anchors (the index has them) are
    collapsed to the first occurrence.
    """
    seen: set[str] = set()
    urls = []
    for year, heading, url, _label in index_entries(page):
        if year != cycle or not _GENERAL.search(heading) or url in seen:
            continue
        seen.add(url)
        urls.append(url)
    return urls


def archive_filename(url: str) -> str:
    """A cache name that is unique per URL even when basenames repeat."""
    digest = hashlib.sha1(url.encode()).hexdigest()[:8]
    return f"archive-{digest}-{url.rsplit('/', 1)[-1]}"


# --------------------------------------------------------------------------
# The PDF archive -- parsing one report
# --------------------------------------------------------------------------
@dataclass
class Report:
    """One MESA report: every county's reading plus the printed STATE line.

    A reading is `{"total": int, "sent": int | None, "party": {bucket: int}}`;
    `total` is mail `Received` or the in-person count, `sent` is mail only.
    """

    kind: str
    layout: str
    run: datetime
    election: date
    counties: dict[str, dict] = field(default_factory=dict)
    names: dict[str, str] = field(default_factory=dict)
    state: dict | None = None


_TITLE_LINE = re.compile(
    r"^\s*MESA\s*-\s*(?P<title>.+?)\s+(?P<stamp>\d{1,2}/\d{1,2}/\d{4}\s+\d{1,2}:\d{2}\s*[AP]\.?M\.?)\s*$",
    re.I | re.M,
)
_STAMP = re.compile(r"(\d{1,2}/\d{1,2}/\d{4})\s+(\d{1,2}:\d{2})\s*([AP])\.?M\.?", re.I)
_ELECTION_LINE = re.compile(r"Election\s+Date:?\s*(\d{1,2}/\d{1,2}/\d{4})", re.I)
_NUMBER = re.compile(r"\d[\d,]*")

#: Standard mail layout (ap2520). The county's first party shares its line;
#: parties are Capitalised words, counties are UPPERCASE, which is what keeps
#: "ROGER MILLS Republican" from reading "MILLS" as the party.
_MAIL_COLUMNS = re.compile(r"\bSent\s+Undel\.?\s+Received\b", re.I)
_MAIL_FIRST = re.compile(
    r"^\s*(\d{2})\s+([A-Z][A-Z .'-]*?)\s+([A-Z][a-z]+)\s+(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)\s*$")
_MAIL_PARTY = re.compile(r"^\s*([A-Z][a-z]+)\s+(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)\s*$")
_MAIL_TOTAL = re.compile(r"^\s*(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)\s*$")

#: Standard in-person layout (ap2541): a "County:" line, one number per party,
#: a bare total.
_IP_COLUMN = re.compile(r"\bIn-?person\b", re.I)
_IP_COUNTY = re.compile(r"^\s*County:\s*(\d{2})\s+([A-Z][A-Z .'-]*?)\s*$")
_IP_PARTY = re.compile(r"^\s*([A-Z][a-z]+)\s+(\d[\d,]*)\s*$")
_IP_TOTAL = re.compile(r"^\s*(\d[\d,]*)\s*$")

#: Wide layout (ap2680): a header line naming the columns, county rows under it,
#: a Grand Total row on the last page.
_WIDE_HEADER = re.compile(r"^\s*County\b(?P<cols>.*\bTotal)\s*$")
_WIDE_LABEL = re.compile(r"\(\d\)\s*([A-Za-z]+)|\bTotal\b")
_WIDE_ROW = re.compile(r"^\s*(\d{2})\s+([A-Z][A-Z .'-]*?)(?=\s{2,}\d)")
_WIDE_STATE = re.compile(r"^\s*Grand\s+Total\b")

STATE_PAGE_NAME = "STATE"


def _pages(body: bytes) -> list[str]:
    """Layout-mode text per page: columns keep their horizontal position,
    which the wide layout needs to place a blank cell and the standard layout
    is indifferent to."""
    try:
        reader = pypdf.PdfReader(io.BytesIO(body))
        return [page.extract_text(extraction_mode="layout") for page in reader.pages]
    except Exception as exc:  # noqa: BLE001 - pypdf raises a zoo of types
        raise SourceError(f"OK: report is not a readable PDF: {exc}") from exc


def _count(token: str) -> int:
    return int(token.replace(",", ""))


def _run_stamp(text: str) -> datetime:
    match = _STAMP.search(text)
    if match is None:
        raise SchemaDrift("OK: report page carries no run stamp")
    day, clock, half = match.groups()
    return datetime.strptime(f"{day} {clock} {half.upper()}M", "%m/%d/%Y %I:%M %p")


def _title(first_page: str) -> tuple[str, str, datetime]:
    match = _TITLE_LINE.search(first_page)
    if match is None:
        raise SchemaDrift("OK: report has no 'MESA - <title> <run stamp>' header line")
    title = " ".join(match.group("title").split())
    key = re.sub(r"[^a-z]", "", title.lower())
    if key not in _TITLES:
        raise SchemaDrift(f"OK: unrecognised report title {title!r}")
    kind, layout = _TITLES[key]
    return kind, layout, _run_stamp(match.group("stamp"))


def _election(first_page: str) -> date:
    match = _ELECTION_LINE.search(first_page)
    if match is None:
        raise SchemaDrift("OK: report has no 'Election Date:' line")
    return datetime.strptime(match.group(1), "%m/%d/%Y").date()


def _reading(party: dict[str, int], total: int, sent: int | None, where: str) -> dict:
    """A county's reading, with the party rows checked against its printed total
    and the registrations the report did not print filled in as 0."""
    if sum(party.values()) != total:
        raise SchemaDrift(
            f"OK: {where}: party rows sum to {sum(party.values())}, printed total is {total}"
        )
    return {"total": total, "sent": sent,
            "party": {b: party.get(b, 0) for b in REGISTRATIONS} | party}


def _file(report: Report, code: str, name: str, reading: dict, where: str) -> None:
    """Put one county block on the report; code 99 / STATE is the statewide line."""
    name = " ".join(name.split())
    if name.upper() == STATE_PAGE_NAME or code == "99":
        if report.state is not None:
            raise SchemaDrift(f"OK: {where}: a second STATE block")
        report.state = reading
        return
    fips, canonical = _county_fips(name)
    if fips in report.counties:
        raise SchemaDrift(f"OK: {where}: {canonical} appears twice")
    report.counties[fips] = reading
    report.names[fips] = canonical


def _add_party(party: dict[str, int], label: str, value: int, where: str) -> None:
    bucket = _party_bucket(label)
    if bucket in party:
        raise SchemaDrift(f"OK: {where}: {label!r} printed twice")
    party[bucket] = value


def _parse_standard_mail(report: Report, pages: list[str]) -> None:
    for index, text in enumerate(pages, start=1):
        where = f"mail report page {index}"
        if not _MAIL_COLUMNS.search(text):
            raise SchemaDrift(f"OK: {where}: columns are not Sent / Undel. / Received")
        block: tuple[str, str] | None = None
        party: dict[str, int] = {}
        sent: dict[str, int] = {}
        closed = 0
        for line in text.splitlines():
            if block is None:
                match = _MAIL_FIRST.match(line)
                if match:
                    code, name, label, sent_n, _undel, received = match.groups()
                    block, party, sent = (code, name), {}, {}
                    _add_party(party, label, _count(received), where)
                    sent[_party_bucket(label)] = _count(sent_n)
                continue
            match = _MAIL_PARTY.match(line)
            if match:
                label, sent_n, _undel, received = match.groups()
                _add_party(party, label, _count(received), where)
                sent[_party_bucket(label)] = _count(sent_n)
                continue
            match = _MAIL_TOTAL.match(line)
            if match:
                total_sent, _undel, total_received = (_count(t) for t in match.groups())
                if sum(sent.values()) != total_sent:
                    raise SchemaDrift(
                        f"OK: {where}: party Sent rows sum to {sum(sent.values())}, "
                        f"printed total is {total_sent}")
                _file(report, block[0], block[1],
                      _reading(party, total_received, total_sent, where), where)
                block, closed = None, closed + 1
        if block is not None or closed != 1:
            raise SchemaDrift(f"OK: {where}: expected one closed county block, read {closed}")


def _parse_standard_inperson(report: Report, pages: list[str]) -> None:
    for index, text in enumerate(pages, start=1):
        where = f"in-person report page {index}"
        if not _IP_COLUMN.search(text):
            raise SchemaDrift(f"OK: {where}: no In-person column")
        block: tuple[str, str] | None = None
        party: dict[str, int] = {}
        closed = 0
        for line in text.splitlines():
            match = _IP_COUNTY.match(line)
            if match:
                if block is not None:
                    raise SchemaDrift(f"OK: {where}: county line inside an open block")
                block, party = (match.group(1), match.group(2)), {}
                continue
            if block is None:
                continue
            match = _IP_PARTY.match(line)
            if match:
                _add_party(party, match.group(1), _count(match.group(2)), where)
                continue
            match = _IP_TOTAL.match(line)
            if match:
                _file(report, block[0], block[1],
                      _reading(party, _count(match.group(1)), None, where), where)
                block, closed = None, closed + 1
        if block is not None or closed != 1:
            raise SchemaDrift(f"OK: {where}: expected one closed county block, read {closed}")


def _wide_columns(header: str) -> list[tuple[int, str]]:
    """(right edge, bucket-or-'total') per column of the wide header line.

    The numbers are right-aligned under their labels, so a cell is placed by
    the header end its own end sits nearest. That is what makes a BLANK cell
    readable: "04 BEAVER 161 10 4 175" has no Libertarian figure, and nothing
    in the token sequence says which of the four is missing.
    """
    columns = []
    for match in _WIDE_LABEL.finditer(header):
        label = match.group(1)
        columns.append((match.end(), "total" if label is None else _party_bucket(label)))
    if "total" not in {b for _, b in columns} or len(columns) < 2:
        raise SchemaDrift(f"OK: wide header {header.strip()!r} has no party and Total columns")
    return columns


def _wide_cells(line: str, start: int, columns: list[tuple[int, str]], where: str) -> dict:
    cells: dict[str, int] = {}
    for match in _NUMBER.finditer(line, start):
        edge, bucket = min(columns, key=lambda c: abs(c[0] - match.end()))
        if bucket in cells:
            raise SchemaDrift(f"OK: {where}: two figures land in the {bucket} column")
        cells[bucket] = _count(match.group())
    if "total" not in cells:
        raise SchemaDrift(f"OK: {where}: row {line.strip()!r} has no Total figure")
    return cells


def _parse_wide(report: Report, pages: list[str]) -> None:
    for index, text in enumerate(pages, start=1):
        where = f"wide in-person report page {index}"
        # Every page repeats the header, and its character offsets drift by a
        # column or two from page to page, so the columns are read per page.
        columns: list[tuple[int, str]] | None = None
        for line in text.splitlines():
            if _WIDE_HEADER.match(line):
                columns = _wide_columns(line)
                continue
            row = _WIDE_ROW.match(line)
            match = row or _WIDE_STATE.match(line)
            if match is None:
                continue
            if columns is None:
                raise SchemaDrift(f"OK: {where}: a county row before the column header")
            cells = _wide_cells(line, match.end(), columns, where)
            total = cells.pop("total")
            code, name = (row.group(1), row.group(2)) if row else ("99", STATE_PAGE_NAME)
            _file(report, code, name, _reading(cells, total, None, where), where)
        if columns is None:
            raise SchemaDrift(f"OK: {where}: no column header line")


def parse_pages(pages: list[str]) -> Report:
    """One MESA report from its pages' layout-mode text. Pure; see parse_report."""
    if not pages:
        raise SchemaDrift("OK: report has no pages")
    kind, layout, run = _title(pages[0])
    election = _election(pages[0])
    report = Report(kind=kind, layout=layout, run=run, election=election)
    for index, text in enumerate(pages[1:], start=2):
        # The run moment is printed on every page. A page from another run
        # bound into the same file would be a different reading.
        if _run_stamp(text) != run:
            raise SchemaDrift(f"OK: page {index} is stamped {_run_stamp(text)}, page 1 {run}")

    if layout == LAYOUT_WIDE:
        _parse_wide(report, pages)
    elif kind == REPORT_MAIL:
        _parse_standard_mail(report, pages)
    else:
        _parse_standard_inperson(report, pages)

    if len(report.counties) != EXPECTED_COUNTIES:
        raise SchemaDrift(
            f"OK: {kind} report run {run} has {len(report.counties)} of "
            f"{EXPECTED_COUNTIES} counties")
    if report.state is None:
        raise SchemaDrift(f"OK: {kind} report run {run} has no STATE line")
    _check_state(report)
    return report


def _state_of(report: Report) -> dict:
    """The report's STATE reading; `parse_pages` guarantees it is there."""
    if report.state is None:
        raise SchemaDrift(f"OK: {report.kind} report run {report.run} has no STATE line")
    return report.state


def _check_state(report: Report) -> None:
    """The 77 county blocks must add up to the printed STATE line -- in total,
    in Sent, and in every party. The STATE line is what gets published; the
    sum is how a county page the regexes failed to read is caught."""
    counties = list(report.counties.values())
    state = _state_of(report)
    summed = {
        "total": sum(c["total"] for c in counties),
        "sent": _add(*[c["sent"] for c in counties]),
        "party": {b: sum(c["party"].get(b, 0) for c in counties) for b in REGISTRATIONS},
    }
    printed = {"total": state["total"], "sent": state["sent"],
               "party": {b: state["party"].get(b, 0) for b in REGISTRATIONS}}
    if summed != printed:
        raise SchemaDrift(
            f"OK: {report.kind} report run {report.run}: county pages sum to "
            f"{summed}, STATE page prints {printed}")


def parse_report(body: bytes) -> Report:
    """One archived MESA PDF -> its Report. Raises SchemaDrift for any layout
    this module has not been taught, identified by the printed title."""
    return parse_pages(_pages(body))


# --------------------------------------------------------------------------
# The PDF archive -- assembling days
# --------------------------------------------------------------------------
def last_run_per_day(reports: list[Report]) -> dict[tuple[str, date], Report]:
    """{(kind, run date): the latest run that day} -- the day's reading."""
    latest: dict[tuple[str, date], Report] = {}
    for report in reports:
        key = (report.kind, report.run.date())
        if key not in latest or report.run > latest[key].run:
            latest[key] = report
    return latest


def _county_day(mail: dict | None, inperson: dict | None) -> dict:
    """One county's combined position, by `fetch`'s own conventions: a report
    that does not exist for the day leaves its field None, never 0; a party
    the report omitted is 0."""
    party = {}
    for bucket in REGISTRATIONS:
        party[bucket] = _add(mail["party"].get(bucket, 0) if mail else None,
                             inperson["party"].get(bucket, 0) if inperson else None)
    return {
        "ballots_total": _add(mail["total"] if mail else None,
                              inperson["total"] if inperson else None),
        "mail_requested": mail["sent"] if mail else None,
        "mail_returned": mail["total"] if mail else None,
        "inperson": inperson["total"] if inperson else None,
        "party": party,
    }


def archive_days(reports: list[Report], cycle: int) -> FetchResult:
    """Daily StateDay / CountyDay / MethodDay rows from a cycle's reports.

    The last run of each day per report type is the day's reading; a day with
    only one report type is still a day (see the module docstring). The
    statewide row is checked against the `99 STATE` page of each report, and
    the party x method crosstab rides along via `_methods`.
    """
    latest = last_run_per_day(reports)
    result = FetchResult()
    method_rows: list[MethodDay] = []
    for day in sorted({d for _, d in latest}):
        mail = latest.get((REPORT_MAIL, day))
        inperson = latest.get((REPORT_INPERSON, day))
        present = [r for r in (mail, inperson) if r is not None]
        names = {}
        for report in present:
            names.update(report.names)
        counties = {
            fips: _county_day(mail.counties.get(fips) if mail else None,
                              inperson.counties.get(fips) if inperson else None)
            for fips in sorted(names)
        }
        one = _emit(counties, names, cycle, day, statewide_extra={
            "mail_requested": _state_of(mail)["sent"] if mail else None})
        _check_state_row(one, mail, inperson, day)
        result.extend(one)
        for report, band in ((mail, METHOD_MAIL), (inperson, METHOD_INPERSON)):
            if report is None:
                continue
            for fips, reading in sorted(report.counties.items()):
                method_rows.append(MethodDay(
                    cycle=cycle, state="OK", county_fips=fips, day=day,
                    method=band, county_name=names[fips],
                    ballots_total=reading["total"],
                    **{f: reading["party"].get(b, 0) for b, f in _PARTY_FIELD.items()},
                ))
    _methods.attach(result, method_rows)
    return result


def _drop_day(result: FetchResult, day: date) -> None:
    """Remove every row dated `day` -- state, county and method -- in place."""
    dropped = sum(1 for r in result.state_rows if r.day == day)
    if not dropped:
        return
    result.state_rows[:] = [r for r in result.state_rows if r.day != day]
    result.county_rows[:] = [r for r in result.county_rows if r.day != day]
    _methods.attach(result, [r for r in _methods.rows_of(result) if r.day != day])
    log.info("OK: the PDF reading of %s is dropped in favour of the certified "
             "voter-history row on the same date", day.isoformat())


def _check_state_row(one: FetchResult, mail: Report | None, inperson: Report | None,
                     day: date) -> None:
    """The county-summed statewide row must equal the reports' own STATE pages."""
    if not one.state_rows:
        raise SchemaDrift(f"OK: {day.isoformat()}: no statewide row from "
                          f"{len(one.county_rows)} counties")
    row = one.state_rows[0]
    expected = _county_day(_state_of(mail) if mail else None,
                           _state_of(inperson) if inperson else None)
    got = {"ballots_total": row.ballots_total, "mail_requested": row.mail_requested,
           "mail_returned": row.mail_returned, "inperson": row.inperson,
           "party": {b: getattr(row, f) for b, f in _PARTY_FIELD.items()}}
    if got != expected:
        raise SchemaDrift(
            f"OK: {day.isoformat()}: county pages sum to {got}, STATE pages print {expected}")


# --------------------------------------------------------------------------
# The adapter
# --------------------------------------------------------------------------
class OKScraper(Adapter):
    """Tier 1 for Oklahoma: the State Election Board's statistics dashboards."""

    state = "OK"
    name = "ok-seb"
    tier = TIER_SCRAPER

    # ------------------------------------------------------------------
    def fetch(self, cycle: int, as_of: date) -> FetchResult:
        election = election_date(cycle)
        history = self._history(election)
        if history is not None:
            counties, names = history
            # The only thing voter history cannot say is how many mail ballots
            # went OUT; that lives in the absentee table. Its party breakdown is
            # not wanted here -- voter history already has a better one -- so the
            # four per-party queries are skipped.
            return _emit(counties, names, cycle, as_of,
                         statewide_extra=self._requested(election))

        absentee = self._absentee(election)
        if absentee is None:
            raise NotYetPublished(
                f"OK: neither the absentee nor the voter-history dashboard "
                f"carries the {cycle} general ({election.isoformat()}) yet"
            )
        counties, names = absentee
        if not total_ballots(counties):
            raise NotYetPublished(
                f"OK: the {cycle} general is on the absentee dashboard but no "
                f"ballot has been returned yet"
            )
        return _emit(counties, names, cycle, as_of)

    # ------------------------------------------------------------------
    def fetch_history(self, cycle: int) -> FetchResult:
        """A past cycle's daily curve from the PDF archive, ending on the
        certified Election-Day row from voter-history credit.

        The daily part walks the Election Board's index page for the cycle's
        general-election PDFs -- both report types, however they are filed --
        and dates each by its printed run stamp; see the module docstring for
        the traps. The index reaches back to 2022, so 2020 gets the certified
        row alone.

        The certified row is deliberately voter-history ONLY, where `fetch`
        also reaches into the absentee table for `mail_requested`: it is the
        state's own final credit, and the daily rows already carry `Sent` for
        every day the mail report was run. A PDF reading dated Election Day
        (2022's last runs were cut at 1:56 PM that day) is dropped in favour of
        it -- the partial reading carries `mail_requested`, the certified row
        does not, and `publish.merge_rows` would otherwise keep the partial
        one as the richer of two same-tier rows on the same key.

        ⚠️ A dashboard failure propagates rather than leaving the curve
        without its final: a curve that stops the day before the election
        looks complete and is not.
        """
        election = election_date(cycle)
        if election >= date.today():
            raise NotYetPublished(
                f"OK: the {cycle} general ({election.isoformat()}) is not over, "
                f"so there is nothing to backfill"
            )
        result = archive_days(self._archive_reports(cycle), cycle)
        history = self._history(election)
        if history is None:
            if not result:
                raise NotYetPublished(
                    f"OK: no archived PDF and no early-vote credit in voter "
                    f"history for the {cycle} general ({election.isoformat()})"
                )
            log.warning("OK: %s has a daily curve but no certified Election-Day "
                        "row -- the voter-history dashboard carries no credit for it",
                        cycle)
            return result
        counties, names = history
        _drop_day(result, election)
        result.extend(_emit(counties, names, cycle, election))
        log.info("OK: %s archived days for %s, plus the certified Election-Day row",
                 len(result.state_rows) - 1, cycle)
        return result

    # ------------------------------------------------------------------
    def _index(self) -> str:
        """The index page, fetched fresh: it is the one thing that changes."""
        body = _net.get(INDEX_URL, state="OK",
                        filename="absentee-voting-statistics.html", min_bytes=4096)
        return body.decode("utf-8", "replace")

    def _pdf(self, url: str) -> bytes:
        """One archived report. Never changes once posted, so cached on disk."""
        return _net.get(url, state="OK", filename=archive_filename(url),
                        use_cache=True, min_bytes=4096)

    def _archive_reports(self, cycle: int) -> list[Report]:
        """Every readable general-election report for the cycle, unordered.

        A file that cannot be fetched is skipped with a warning and the walk
        goes on, as ca.py's does; a file that fetches but does not PARSE raises
        -- a template this module cannot read must be taught, not skipped, or
        the curve thins without anyone noticing (the Montana lesson in
        pyproject.toml). A file whose printed election is another one, or
        whose run date is outside the window, is not drift: it is mis-filed on
        the index, and it is skipped by name.
        """
        election = election_date(cycle)
        earliest = election - timedelta(days=ARCHIVE_WINDOW_DAYS)
        reports: list[Report] = []
        urls = select_general_pdfs(self._index(), cycle)
        for url in urls:
            name = url.rsplit("/", 1)[-1]
            try:
                body = self._pdf(url)
            except SourceError as exc:
                log.warning("OK: archived %s unusable (%s)", name, exc)
                continue
            if not body.startswith(b"%PDF"):
                log.warning("OK: archived %s is not a PDF (begins %r)", name, body[:8])
                continue
            report = parse_report(body)
            if report.election != election:
                log.warning("OK: %s is a %s report for the %s election, not the "
                            "%s general; skipped", name, report.kind,
                            report.election.isoformat(), cycle)
                continue
            if not earliest <= report.run.date() <= election:
                log.warning("OK: %s was run %s, outside the %s general's window; "
                            "skipped", name, report.run, cycle)
                continue
            log.debug("OK: %s -> %s report run %s", name, report.kind, report.run)
            reports.append(report)
        log.info("OK: %d of %d indexed PDFs are %s general-election reports",
                 len(reports), len(urls), cycle)
        return reports

    # ------------------------------------------------------------------
    def _requested(self, election: date) -> dict | None:
        """Statewide mail ballots SENT, for a day whose counts came from history.

        ⚠️ GATED ON FULL COVERAGE, exactly as `_emit` gates the statewide row it
        rides on. The two dashboards are populated independently -- on
        2026-09-06 the absentee table held six counties -- so summing whatever
        it happens to return and hanging that on a 77-county statewide row would
        publish a six-county figure as an Oklahoma total. That is the one thing
        EXPECTED_COUNTIES exists to stop, and this field was walking straight
        past it.
        """
        absentee = self._absentee(election, with_party=False)
        if absentee is None:
            return None
        counties = absentee[0]
        if len(counties) != EXPECTED_COUNTIES:
            log.warning(
                "OK: the absentee dashboard returned %d of %d counties; leaving "
                "mail_requested blank rather than publishing a partial sum as a "
                "statewide total",
                len(counties), EXPECTED_COUNTIES,
            )
            return None
        return {"mail_requested":
                _add(*[c.get("mail_requested") for c in counties.values()])}

    # ------------------------------------------------------------------
    def _item(self, dashboard: str, item: str, filters=()) -> dict:
        params = {"dashboardId": dashboard, "itemId": item}
        if filters:
            params["query"] = build_query(list(filters))
        # A stable cache filename: `hash()` is salted per interpreter, which
        # would scatter a new copy of every response across cache/ on each run.
        digest = hashlib.sha1(params.get("query", "").encode()).hexdigest()[:12]
        body = _net.get(
            ITEM_URL, state="OK",
            filename=f"{dashboard}_{item}_{digest}.json",
            params=params, min_bytes=32,
        )
        try:
            return json.loads(body)
        except ValueError as exc:
            raise SchemaDrift(f"OK: {dashboard}/{item} did not return JSON") from exc

    def _elections(self, dashboard: str, combo: str) -> set[str]:
        payload = self._item(dashboard, combo)
        return set(dimension_values(payload, COL_ELECTION))

    def _has(self, dashboard: str, combo: str, election: date) -> bool:
        return election.strftime(_ENCODED_STAMP) in self._elections(dashboard, combo)

    # ------------------------------------------------------------------
    def _absentee(
        self, election: date, *, with_party: bool = True,
    ) -> tuple[dict[str, dict], dict[str, str]] | None:
        """County mail applications sent and ballots returned, by party."""
        if not self._has(ABSENTEE_DASHBOARD, ABSENTEE_DATE_COMBO, election):
            return None
        stamp = election.strftime(_FILTER_STAMP)
        date_filter = (_election_dimension(), stamp)

        payload = self._item(ABSENTEE_DASHBOARD, ABSENTEE_PIVOT, [date_filter])
        totals = decode(payload, (COL_COUNTY,), (MEASURE_SENT, MEASURE_RECEIVED))

        counties: dict[str, dict] = {}
        names: dict[str, str] = {}
        for (raw_name,), cells in totals.items():
            fips, canonical = _county_fips(raw_name)
            names[fips] = canonical
            counties[fips] = {
                "ballots_total": _int(cells[MEASURE_RECEIVED]),
                "mail_requested": _int(cells[MEASURE_SENT]),
                "mail_returned": _int(cells[MEASURE_RECEIVED]),
                # Oklahoma's early in-person voting is NOT in this table. None,
                # never 0 -- see the module docstring.
                "inperson": None,
                "party": {},
            }

        if not with_party:
            return counties, names

        parties = self._item(ABSENTEE_DASHBOARD, ABSENTEE_PARTY_COMBO, [date_filter])
        for label in dimension_values(parties, COL_PARTY):
            bucket = _party_bucket(label)
            per_party = decode(
                self._item(ABSENTEE_DASHBOARD, ABSENTEE_PIVOT,
                           [date_filter, (_dimension(COL_PARTY), label)]),
                (COL_COUNTY,), (MEASURE_RECEIVED,),
            )
            counted = {
                _county_fips(name)[0]: _int(cells[MEASURE_RECEIVED])
                for (name,), cells in per_party.items()
            }
            for fips, row in counties.items():
                # Oklahoma registers by party and reports all four, so a party
                # with no ballots in a county is a real 0, not a blank.
                row["party"][bucket] = counted.get(fips, 0)
        return counties, names

    # ------------------------------------------------------------------
    def _history(self, election: date) -> tuple[dict[str, dict], dict[str, str]] | None:
        """County early ballots CAST, split mail vs in-person, by party.

        None means "no early-vote credit to read", which covers BOTH an election
        the dashboard does not carry and one it carries with nothing credited to
        it yet. Answering None for the second case is what gives `fetch` and
        `fetch_history` the same zero guard without either of them writing it:
        `fetch` falls through to the absentee table, which is exactly what
        should happen the week after an election, and `fetch_history` reports
        the cycle as unavailable instead of publishing 77 zeroes. See
        `total_ballots`.
        """
        if not self._has(HISTORY_DASHBOARD, HISTORY_DATE_COMBO, election):
            return None
        stamp = election.strftime(_FILTER_STAMP)
        date_filter = (_election_dimension(), stamp)

        methods = self._item(HISTORY_DASHBOARD, HISTORY_METHOD_PIE, [date_filter])
        check_methods(dimension_values(methods, COL_METHOD))

        counties: dict[str, dict] = {}
        names: dict[str, str] = {}
        for raw_method in EARLY_METHODS:
            bucket = _method(raw_method)
            if bucket not in (METHOD_MAIL, METHOD_INPERSON):
                raise SchemaDrift(f"OK: cannot normalize voting method {raw_method!r}")
            field = "mail_returned" if bucket == METHOD_MAIL else "inperson"
            filters = [date_filter, (_dimension(COL_METHOD), raw_method)]
            payload = self._item(HISTORY_DASHBOARD, HISTORY_PIVOT, filters)

            for (raw_name,), cells in decode(
                payload, (COL_COUNTY,), (MEASURE_HISTORY,)
            ).items():
                fips, canonical = _county_fips(raw_name)
                names[fips] = canonical
                row = counties.setdefault(
                    fips, {"ballots_total": None, "mail_returned": None,
                           "inperson": None, "party": {}},
                )
                count = _int(cells[MEASURE_HISTORY])
                row[field] = _add(row[field], count)
                row["ballots_total"] = _add(row["ballots_total"], count)

            for (raw_name, label), cells in decode(
                payload, (COL_COUNTY, COL_PARTY), (MEASURE_HISTORY,)
            ).items():
                fips, _ = _county_fips(raw_name)
                party = counties[fips]["party"]
                party_bucket = _party_bucket(label)
                party[party_bucket] = _add(
                    party.get(party_bucket), _int(cells[MEASURE_HISTORY])
                )

        if not total_ballots(counties):
            log.info(
                "OK: the voter-history dashboard lists %s but has credited no "
                "early ballot to it", election.isoformat(),
            )
            return None

        # Every registration Oklahoma recognises appears in the dashboard's own
        # Affiliation list, so a party with no early ballots in a county is a
        # genuine 0 rather than a blank.
        registrations = {
            _party_bucket(label)
            for label in dimension_values(
                self._item(HISTORY_DASHBOARD, HISTORY_PARTY_COMBO, [date_filter]),
                COL_PARTY,
            )
        }
        for row in counties.values():
            for bucket in registrations:
                row["party"].setdefault(bucket, 0)
        return counties, names
