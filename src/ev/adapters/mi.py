"""Michigan: the Bureau of Elections' absentee data by jurisdiction (XLSX).

Michigan reports absentee ballots by JURISDICTION -- 1,520-odd cities and
townships -- not by county, which would normally make a county roll-up a name
join against a municipality table and therefore unreliable. It does not, because
the file carries the county on every row: `COUNTY | JURISDICTION | REQUESTS |
ISSUED | RECEIVED`. We aggregate on that column and never touch a municipality
crosswalk. (Michigan cities may straddle a county line -- Novi appears as both a
city and a township -- so a crosswalk really would have been wrong.)

Two features of this file shape everything below.

**The TOTALS row.** The last row is Michigan's own statewide total, computed from
the unsuppressed data. We publish that as the StateDay rather than summing the
jurisdictions, so our headline is exactly the Bureau's number and can never drift
from it by a rounding or a suppression.

**"Less than 10".** Michigan masks any jurisdiction cell below ten with the
literal string "Less than 10" -- 97 cells on 2024-10-15, thinning to 30 a week
later. That value is genuinely unknown, so a county roll-up that includes one
cannot be stated. We publish such a county's affected measure as None (see THE
BLANK RULE in schema.py) rather than summing the suppressed cell as zero, which
would understate small counties by up to 10% -- Alger County's 2024-10-15 issued
count is six suppressed jurisdictions out of nine. The counties this blanks are
the small rural ones, and only until their counts pass ten; every other county,
and the statewide row, is exact.

The workbook holds one sheet per cycle -- "2024 (Oct 15)" alongside a "2020
(Oct 12)" comparison sheet -- and the sheet title carries the as-of date.

Michigan does not register voters by party, so every party_* field is None.
"""

from __future__ import annotations

import io
import re
from collections import defaultdict
from datetime import date, datetime

import openpyxl

from ..schema import TIER_SCRAPER, CountyDay, StateDay
from . import _fips
from ._net import Missing, get, looks_like_xlsx
from .base import Adapter, FetchResult, NotYetPublished, SchemaDrift, SourceError

#: UNVERIFIED FROM THIS NETWORK. The workbook is published from the Michigan SoS
#: election-data page; the file is overwritten in place as the count updates, so
#: there is no dated URL to walk. Confirmed to exist and to have this schema from
#: the 2024 general (see tests/fixtures/mi/), but the live host 403s automated
#: requests from some networks -- treat a non-404 failure as SourceError, not as
#: "not published", which is exactly what _net.get does.
URL = (
    "https://www.michigan.gov/sos/-/media/Project/Websites/sos/"
    "05mcdaniel/General_Election_Data_by_Jurisdiction.xlsx"
)

#: Alternates seen in the wild for the same report. Tried in order; the first one
#: that returns a real xlsx wins.
URL_CANDIDATES = (
    URL,
    "https://www.michigan.gov/sos/-/media/Project/Websites/sos/"
    "Elections/General-Election-Data-by-Jurisdiction.xlsx",
)

HEADER = ("county", "jurisdiction", "requests", "issued", "received")

#: Michigan's privacy floor. The cell is not blank and it is not a number: it is
#: a statement that the true value is somewhere in 0..9.
SUPPRESSED = "less than 10"

TOTALS_LABELS = {"totals", "total", "statewide", "state totals"}

_SHEET_DATE = re.compile(r"^\s*(\d{4})\s*\(\s*([A-Za-z]+)\.?\s*(\d{1,2})\s*\)")

_MONTHS = {m.lower(): i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], start=1)}


class _Suppressed:
    """Sentinel for a masked cell: reported, but not as a number."""

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "<suppressed>"


MASKED = _Suppressed()


def _count(value):
    """A count, MASKED for Michigan's privacy floor, or None for an empty cell."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    text = " ".join(str(value).strip().lower().split())
    if not text:
        return None
    if text.startswith(SUPPRESSED) or text in {"<10", "less than ten"}:
        return MASKED
    try:
        return int(float(text.replace(",", "")))
    except ValueError as exc:
        raise SchemaDrift(f"MI: {value!r} is neither a count nor a suppression flag") from exc


def sheet_date(title: str) -> tuple[int, date]:
    """(year, as-of date) from a sheet title like "2024 (Oct 15)"."""
    m = _SHEET_DATE.match(title or "")
    if not m:
        raise SchemaDrift(f"MI: sheet title {title!r} is not '<year> (<Mon> <day>)'")
    year, month_name, day = int(m.group(1)), m.group(2).lower()[:3], int(m.group(3))
    month = _MONTHS.get(month_name)
    if month is None:
        raise SchemaDrift(f"MI: sheet title {title!r} has no month")
    try:
        return year, date(year, month, day)
    except ValueError as exc:
        raise SchemaDrift(f"MI: sheet title {title!r} is not a date") from exc


def _sum(values: list) -> int | None:
    """Total a county's jurisdictions, or None if any of them was suppressed.

    An unknown component makes the sum unknown. Michigan tells us the value is
    under ten, not that it is zero, and a blank cell says exactly that -- see the
    module docstring for why this is the choice over a lower bound.
    """
    total = 0
    seen = False
    for value in values:
        if value is MASKED:
            return None
        if value is None:
            continue
        total += value
        seen = True
    return total if seen else None


def parse(body: bytes, cycle: int) -> FetchResult:
    """Parse the Michigan jurisdiction workbook into canonical rows."""
    if not looks_like_xlsx(body):
        raise SourceError("MI: jurisdiction report was not an xlsx")
    try:
        book = openpyxl.load_workbook(io.BytesIO(body), read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001 -- openpyxl raises a zoo of types
        raise SourceError(f"MI: could not open jurisdiction workbook: {exc}") from exc

    wanted = None
    for sheet in book.worksheets:
        year, _ = sheet_date(sheet.title)
        if year == int(cycle):
            wanted = sheet
            break
    if wanted is None:
        # The workbook is up but still carries only the previous cycle's sheets.
        raise NotYetPublished(
            f"MI: workbook has no {cycle} sheet (only {[s.title for s in book.worksheets]})"
        )

    _, day = sheet_date(wanted.title)
    rows = wanted.iter_rows(values_only=True)
    try:
        header = tuple(" ".join(str(c or "").strip().lower().split()) for c in next(rows))
    except StopIteration as exc:
        raise SchemaDrift("MI: jurisdiction sheet is empty") from exc
    if header[:5] != HEADER:
        raise SchemaDrift(f"MI: unexpected header {header!r}, wanted {HEADER!r}")

    state_rows: list[StateDay] = []
    by_county: dict[str, dict[str, list]] = defaultdict(
        lambda: {"requests": [], "issued": [], "received": []}
    )
    unknown: list[str] = []

    for row in rows:
        if not row or row[0] is None:
            continue
        county = " ".join(str(row[0]).split())
        requests, issued, received = (_count(row[i]) if i < len(row) else None
                                      for i in (2, 3, 4))

        if county.lower() in TOTALS_LABELS:
            state_rows.append(StateDay(
                cycle=cycle, state="MI", day=day,
                # Returned absentee ballots are every early vote Michigan reports
                # here; the file carries no in-person early-voting column.
                ballots_total=received if received is not MASKED else None,
                mail_requested=issued if issued is not MASKED else None,
                mail_returned=received if received is not MASKED else None,
                inperson=None,
                # Michigan has no party registration. Never 0.
                party_dem=None, party_rep=None, party_oth=None, party_npa=None,
            ))
            continue

        hit = _fips.lookup("MI", county)
        if hit is None:
            unknown.append(county)
            continue
        bucket = by_county[hit[0]]
        bucket["requests"].append(requests)
        bucket["issued"].append(issued)
        bucket["received"].append(received)
        bucket["name"] = hit[1]

    if unknown:
        raise SchemaDrift(f"MI: unrecognised county names {sorted(set(unknown))[:5]}")
    if not by_county:
        raise SchemaDrift("MI: jurisdiction sheet produced no county rows")

    county_rows = [
        CountyDay(
            cycle=cycle, state="MI", county_fips=fips, day=day,
            county_name=bucket["name"],
            ballots_total=_sum(bucket["received"]),
            mail_returned=_sum(bucket["received"]),
            inperson=None,
            party_dem=None, party_rep=None, party_oth=None, party_npa=None,
        )
        for fips, bucket in sorted(by_county.items())
    ]
    return FetchResult(state_rows=state_rows, county_rows=county_rows)


class MIScraper(Adapter):
    """Tier 1 for Michigan: the SoS absentee-by-jurisdiction workbook."""

    state = "MI"
    name = "mi-sos"
    tier = TIER_SCRAPER

    def _load(self, cycle: int, *, use_cache: bool) -> bytes:
        problems: list[str] = []
        for url in URL_CANDIDATES:
            try:
                body = get(
                    url,
                    state="MI",
                    filename=f"{cycle}_jurisdiction.xlsx",
                    use_cache=use_cache,
                    min_bytes=4096,
                )
            except Missing as exc:
                problems.append(str(exc))
                continue
            if not looks_like_xlsx(body):
                problems.append(f"{url} came back as HTML, not xlsx")
                continue
            return body
        raise NotYetPublished(
            f"MI: no absentee workbook posted for {cycle} yet ({'; '.join(problems)})"
        )

    def fetch(self, cycle: int, as_of: date) -> FetchResult:
        result = parse(self._load(cycle, use_cache=False), cycle)
        published = (result.state_rows or result.county_rows)[0].day
        if published > as_of:
            raise NotYetPublished(
                f"MI: workbook is dated {published.isoformat()}, after {as_of.isoformat()}"
            )
        return result

    def fetch_history(self, cycle: int) -> FetchResult:
        """Michigan overwrites this workbook in place -- there is no dated archive.

        What survives is whatever we cached while the cycle was live, so history
        for a past cycle is only available if this repo was running then. Later
        cycles' workbooks do carry a comparison sheet for the prior presidential
        year, which `parse` will pick up when asked for that cycle.
        """
        if cycle >= datetime.now().year:
            raise NotYetPublished(f"MI: {cycle} is not an archived cycle")
        return parse(self._load(cycle, use_cache=True), cycle)
