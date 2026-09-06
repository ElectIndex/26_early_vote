"""Ohio: the Secretary of State's county absentee report (XLSX).

Ohio publishes one workbook for the election, refreshed through the early-vote
period and then restated as the official post-election report. It holds one sheet
whose TITLE is the as-of date ("11.5.2024 Absentee Report"), one row per county,
and a "Statewide" row that Ohio computes itself -- we use Ohio's own statewide
row rather than summing counties, so our headline can never disagree with the
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
record of which primary ballot you last requested, and it appears nowhere in this
report -- so every party_* field is None. See THE BLANK RULE in schema.py.
"""

from __future__ import annotations

import io
import re
from datetime import date, datetime

import openpyxl

from ..schema import TIER_SCRAPER, CountyDay, StateDay
from . import _fips
from ._net import Missing, get, looks_like_xlsx
from .base import Adapter, FetchResult, NotYetPublished, SchemaDrift, SourceError

#: The report lives under the SoS's per-election asset folder. `{cycle}` is the
#: four-digit year and `gen` is the general election; the primary uses `pri`.
#: VERIFIED for 2022 and 2024 (see tests/fixtures/oh/).
BASE = "https://www.ohiosos.gov/globalassets/elections/{cycle}/gen/absentee/"

#: Ohio renames the file as the election passes: the live pre-election report,
#: then the certified one. We try them newest-name-first and take the first that
#: exists, because during the EV period only the first of these is posted.
FILENAMES = (
    "{cycle}gen_absentee_report_web.xlsx",
    "{cycle}gen_official_absentee_report_web.xlsx",
)

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


class OHScraper(Adapter):
    """Tier 1 for Ohio: the SoS county absentee report."""

    state = "OH"
    name = "oh-sos"
    tier = TIER_SCRAPER

    def _load(self, cycle: int, *, use_cache: bool) -> bytes:
        base = BASE.format(cycle=cycle)
        problems: list[str] = []
        for template in FILENAMES:
            filename = template.format(cycle=cycle)
            try:
                body = get(
                    base + filename,
                    state="OH",
                    filename=f"{cycle}_{filename}",
                    use_cache=use_cache,
                    min_bytes=4096,
                )
            except Missing as exc:
                problems.append(str(exc))
                continue
            if not looks_like_xlsx(body):
                # The SoS CMS answers an unposted report with a styled HTML page.
                problems.append(f"{filename} came back as HTML, not xlsx")
                continue
            return body
        raise NotYetPublished(
            f"OH: no absentee report posted for {cycle} yet ({'; '.join(problems)})"
        )

    def fetch(self, cycle: int, as_of: date) -> FetchResult:
        result = parse(self._load(cycle, use_cache=False), cycle)
        published = next(
            (r.day for r in result.state_rows), next(iter(result.county_rows)).day
        )
        if published > as_of:
            # A workbook dated ahead of the run is the certified post-election
            # report showing up early in a backfill; refuse rather than publish a
            # future-dated row.
            raise NotYetPublished(
                f"OH: report is dated {published.isoformat()}, after {as_of.isoformat()}"
            )
        return result

    def fetch_history(self, cycle: int) -> FetchResult:
        """The archived workbook for a past cycle -- a single certified snapshot.

        Ohio overwrites the report in place rather than keeping a dated file per
        day, so the archive is one row per table, not a daily series. That is
        still worth backfilling: it is the cycle's final absentee total, which is
        what the 2022/2024 comparison lines anchor on.
        """
        if cycle >= datetime.now().year:
            raise NotYetPublished(f"OH: {cycle} is not an archived cycle")
        return parse(self._load(cycle, use_cache=True), cycle)
