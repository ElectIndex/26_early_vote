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
"""

from __future__ import annotations

import io
import json
import logging
import re
import uuid
from datetime import date, datetime, timezone

import openpyxl

from ..schema import TIER_SCRAPER, CountyDay, StateDay
from . import _fips
from ._net import DEFAULT_HEADERS, SESSION, Missing, cache_path, get, looks_like_xlsx
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


def _sub(whole: int | None, part: int | None) -> int | None:
    """`whole - part`, or None if either side is unreported.

    A negative answer means the two columns do not mean what this module thinks
    they mean, which is drift, not a number worth publishing.
    """
    if whole is None or part is None:
        return None
    if whole < part:
        raise SchemaDrift(
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
def build_query() -> dict:
    """The querydata body: every election, every county, one request.

    Selecting all elections rather than filtering server-side keeps the request
    to columns and a Sum -- no filter DSL -- and the whole answer is 40 KB. The
    cycle is picked out locally, where a mismatch is visible and testable.
    """
    def source():
        return {"SourceRef": {"Source": "a"}}

    select = [
        {"Column": {"Expression": source(), "Property": name},
         "Name": f"{POWERBI_ENTITY}.{name}"}
        for name in POWERBI_COLUMNS
    ] + [
        {"Aggregation": {
            "Expression": {"Column": {"Expression": source(), "Property": name}},
            "Function": 0},
         "Name": f"Sum({POWERBI_ENTITY}.{name})"}
        for name in POWERBI_MEASURES
    ]
    return {
        "version": "1.0.0",
        "queries": [{
            "Query": {"Commands": [{"SemanticQueryDataShapeCommand": {
                "Query": {
                    "Version": 2,
                    "From": [{"Name": "a", "Entity": POWERBI_ENTITY, "Type": 0}],
                    "Select": select,
                },
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


def decode_dsr(payload: dict) -> list[dict]:
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
    """
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

    wanted = set(POWERBI_COLUMNS) | set(POWERBI_MEASURES)
    missing = sorted(wanted - set(slot_names.values()))
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


def parse_dashboard(payload: dict, cycle: int) -> FetchResult:
    """Parse the dashboard's cumulative county counts for `cycle`'s general."""
    rows = decode_dsr(payload)

    label = None
    for row in rows:
        m = _DASHBOARD_GENERAL.match(" ".join(str(row.get(COL_ELECTION) or "").split()))
        if m and int(m.group(1)) == int(cycle):
            label = row[COL_ELECTION]
            break
    if label is None:
        seen = sorted({str(r.get(COL_ELECTION)) for r in rows})
        raise NotYetPublished(
            f"OH: absentee dashboard has no {cycle} November general yet "
            f"(it carries {seen})"
        )

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
    running = [None, None, None, None]
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


def fetch_dashboard() -> dict:
    """POST the querydata request and return the parsed JSON.

    Uses the shared `_net` session because this host is NOT behind Cloudflare --
    it answers a stock python-requests POST, which is the entire reason Ohio has
    a headless path at all.
    """
    body = json.dumps(build_query())
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
            POWERBI_QUERY_URL, headers=headers, data=body, timeout=120
        )
    except Exception as exc:  # noqa: BLE001 -- requests raises several types
        raise SourceError(f"OH: absentee dashboard query failed: {exc}") from exc
    if response.status_code != 200:
        raise SourceError(
            f"OH: absentee dashboard returned HTTP {response.status_code} "
            "(the report's resource key may have been rotated -- see "
            "docs/ohio-source.md)"
        )
    cache_path("OH", "powerbi_absentee_querydata.json").write_bytes(response.content)
    try:
        return response.json()
    except ValueError as exc:
        raise SchemaDrift(f"OH: absentee dashboard did not return JSON: {exc}") from exc


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

    def fetch_history(self, cycle: int) -> FetchResult:
        """The archived workbook for a past cycle -- a single certified snapshot.

        Ohio overwrites the report in place rather than keeping a dated file per
        day, so the archive is one row per table, not a daily series. That is
        still worth backfilling: it is the cycle's final absentee total, which is
        what the 2022/2024 comparison lines anchor on.

        The dashboard is NOT used here. Its numbers are a pre-certification
        tracker (2,262,963 ballots for the 2024 general against the workbook's
        2,620,750), so backfilling from it would anchor the comparison lines on a
        different measurement than the one they are compared against.
        """
        if cycle >= datetime.now().year:
            raise NotYetPublished(f"OH: {cycle} is not an archived cycle")
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
