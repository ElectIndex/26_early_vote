"""Tennessee: the Secretary of State's early-voting and absentee workbook.

Tennessee posts one workbook per election to its file host, re-uploaded under a
new name each day of early voting. The file is in LONG format -- one row per
county per day, plus Tennessee's own `Statewide` rows -- so, like Maryland's and
North Carolina's, a single download carries the whole daily curve rather than a
snapshot, and a missed run cannot lose a day.

Three things drive the parser.

* **Two layouts, and only two.** The 2024 general's sheet is
  `County | Date | EarlyVoting | Absentee | Day Total`; the 2022 general's is
  `CoID | County | Date | Day Total`, with no method split at all. Both are real
  and both must parse, so the header is matched as a SET of names against an
  explicit allow-list of the two verified layouts. Anything else is SchemaDrift
  -- deliberately, because the failure this guards against is Tennessee renaming
  `EarlyVoting` and us silently publishing "method not reported" for a cycle
  where they did report it.

* **The counts are DAILY, not cumulative.** Every other snapshot source in this
  repo publishes a running total; Tennessee publishes the day's own ballots, and
  we accumulate.

* **`Day Total` does not always equal `EarlyVoting + Absentee`.** On nine of the
  1,344 rows in the 2024 general file it is one to three ballots higher --
  statewide on 10/28, 10/29 and 10/31, and in Anderson, Chester, Hawkins, Perry
  and Rutherford. So `ballots_total` is always Tennessee's own `Day Total`,
  never a sum of the parts, and the method fields are allowed not to add up to
  it. Deriving the headline from the split would put us a few ballots away from
  the Secretary of State's own published number for no reason.

* **The `Statewide` rows are Tennessee's own.** We publish those rather than
  summing the 95 counties, so our headline is exactly the Secretary of State's.

Tennessee does not register voters by party -- the party columns that appear in
its PRIMARY files record which ballot a voter pulled, not a registration -- so
every `party_*` field is None here. See THE BLANK RULE in schema.py.
"""

from __future__ import annotations

import io
import logging
import re
from collections import defaultdict
from datetime import date, datetime, timedelta

import openpyxl

from ..calendar import election_date
from ..schema import TIER_SCRAPER, CountyDay, StateDay
from . import _fips
from ._net import Missing, get, looks_like_xlsx
from .base import Adapter, FetchResult, NotYetPublished, SchemaDrift, SourceError

log = logging.getLogger(__name__)

#: VERIFIED on this network, all returning a real xlsx:
#:   .../20241105EarlyAbsentee1025.xlsx  (2024 general, through Oct 25)
#:   .../20241105EarlyAbsentee1030.xlsx
#:   .../20241105EarlyAbsentee1031.xlsx
#:   .../20221108EarlyAbsenteethrough1103.xlsx  (2022 general)
#: The 2026 general's names cannot exist yet; they are built from the same two
#: templates below and every one of them currently reports "not posted".
BASE = "https://sos-prod.tnsosgovfiles.com/s3fs-public/document"

#: The landing page, which is 200 during the early-voting period and 404 for the
#: rest of the cycle (Tennessee unpublishes it off-season -- it is 404 today).
#: Scraped first so a filename we did not predict is still found.
INDEX = "https://sos.tn.gov/elections/services/early-voting-data"

#: `{prefix}` is the ELECTION date, `{stamp}` the through-date. The prefix is
#: what keeps the August state primary's files from ever being mistaken for the
#: November general's -- they carry a different election date, so unlike Kentucky
#: and Maryland no date window is needed here.
FILENAMES = (
    "{prefix}EarlyAbsentee{stamp}.xlsx",
    "{prefix}EarlyAbsenteethrough{stamp}.xlsx",
    "{prefix}EarlyAbsentee_{stamp}.xlsx",
)

#: How far back a run looks for the newest posted file. Tennessee posts on
#: business days, so a Monday run must be able to see Friday's file.
LOOKBACK_DAYS = 10

STATEWIDE = "statewide"

COUNTY, DATE, TOTAL = "county", "date", "day total"
EARLY, ABSENTEE = "earlyvoting", "absentee"

#: The two verified sheet layouts, as sets of normalised header names. A layout
#: not listed here is drift; see the module docstring for why this is an
#: allow-list rather than "require the columns I need and ignore the rest".
LAYOUTS = (
    frozenset({COUNTY, DATE, EARLY, ABSENTEE, TOTAL}),   # 2024 general
    frozenset({"coid", COUNTY, DATE, TOTAL}),            # 2022 general
)


def _norm_header(raw) -> str:
    return " ".join(str(raw or "").strip().lower().split())


def _int(value) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise SchemaDrift(f"TN: {value!r} is not a count")
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip().replace(",", "")
    if not text or text in {"-", "--", "N/A", "n/a"}:
        return None
    try:
        return int(float(text))
    except ValueError as exc:
        raise SchemaDrift(f"TN: {value!r} is not a count") from exc


def _day(value) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise SchemaDrift(f"TN: {value!r} is not a date")


def stamp(day: date) -> str:
    """Tennessee's through-date in a filename: two-digit month and day."""
    return day.strftime("%m%d")


def prefix(cycle: int) -> str:
    """The election-date prefix every file for a cycle carries."""
    return election_date(cycle).strftime("%Y%m%d")


def filenames(cycle: int, day: date) -> list[str]:
    return [t.format(prefix=prefix(cycle), stamp=stamp(day)) for t in FILENAMES]


class _Bucket:
    __slots__ = ("total", "early", "absentee")

    def __init__(self) -> None:
        self.total = 0
        self.early: int | None = None
        self.absentee: int | None = None

    def add(self, total, early, absentee) -> None:
        self.total += total or 0
        if early is not None:
            self.early = (self.early or 0) + early
        if absentee is not None:
            self.absentee = (self.absentee or 0) + absentee


def parse(body: bytes, cycle: int, as_of: date) -> FetchResult:
    """Parse one Tennessee early-voting workbook into canonical rows."""
    if not looks_like_xlsx(body):
        raise SourceError("TN: early-voting report was not an xlsx")
    try:
        book = openpyxl.load_workbook(io.BytesIO(body), read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001 -- openpyxl raises a zoo of types
        raise SourceError(f"TN: could not open early-voting workbook: {exc}") from exc

    rows = book.worksheets[0].iter_rows(values_only=True)
    try:
        header = [_norm_header(c) for c in next(rows)]
    except StopIteration as exc:
        raise SchemaDrift("TN: early-voting report is empty") from exc

    names = frozenset(n for n in header if n)
    if names not in LAYOUTS:
        raise SchemaDrift(f"TN: unrecognised sheet layout {sorted(names)}")
    index = {name: i for i, name in enumerate(header) if name}

    by_state: dict[date, tuple] = {}
    by_county: dict[tuple[str, date], tuple] = {}
    county_names: dict[str, str] = {}
    unknown: set[str] = set()
    seen_days: set[date] = set()

    def cell(row, column):
        i = index.get(column)
        if i is None or i >= len(row):
            return None
        return _int(row[i])

    for row in rows:
        if not row:
            continue
        name = " ".join(str(row[index[COUNTY]] or "").split())
        if not name:
            continue
        day = _day(row[index[DATE]])
        seen_days.add(day)
        if day > as_of:
            continue

        measures = (cell(row, TOTAL), cell(row, EARLY), cell(row, ABSENTEE))
        if name.lower() == STATEWIDE:
            by_state[day] = measures
            continue

        hit = _fips.lookup("TN", name)
        if hit is None:
            unknown.add(name)
            continue
        fips, canonical = hit
        county_names[fips] = canonical
        by_county[(fips, day)] = measures

    if unknown:
        # Tennessee has exactly 95 counties and they do not change.
        raise SchemaDrift(f"TN: unrecognised county names {sorted(unknown)[:5]}")
    if not seen_days:
        raise SchemaDrift("TN: early-voting report has no data rows")
    if not by_state and not by_county:
        raise NotYetPublished(
            f"TN: early voting opens {min(seen_days).isoformat()}, "
            f"after {as_of.isoformat()}"
        )

    span = sorted(d for d in seen_days if d <= as_of)
    return _emit(by_state, by_county, county_names, cycle, span)


def _emit(by_state, by_county, county_names, cycle, span) -> FetchResult:
    result = FetchResult()

    running = _Bucket()
    for day in span:
        today = by_state.get(day)
        if today:
            running.add(*today)
        result.state_rows.append(StateDay(
            cycle=cycle, state="TN", day=day,
            ballots_total=running.total,
            ballots_new=(today[0] if today else 0),
            # Tennessee reports ballots RETURNED, never ballots requested.
            mail_requested=None,
            mail_returned=running.absentee,
            inperson=running.early,
            # No party registration in Tennessee. Never 0.
            party_dem=None, party_rep=None, party_oth=None, party_npa=None,
        ))

    for fips in sorted(county_names):
        running = _Bucket()
        for day in span:
            today = by_county.get((fips, day))
            if today:
                running.add(*today)
            if running.total == 0:
                continue
            result.county_rows.append(CountyDay(
                cycle=cycle, state="TN", county_fips=fips, day=day,
                county_name=county_names[fips],
                ballots_total=running.total,
                ballots_new=(today[0] if today else 0),
                mail_returned=running.absentee,
                inperson=running.early,
                party_dem=None, party_rep=None, party_oth=None, party_npa=None,
            ))
    return result


def discover(index_html: str, cycle: int) -> list[str]:
    """Workbook URLs the early-voting landing page lists for this cycle."""
    want = prefix(cycle)
    found: list[str] = []
    for href in re.findall(r'href="([^"]+\.xlsx)"', index_html or "", re.IGNORECASE):
        tail = href.rsplit("/", 1)[-1]
        if not tail.startswith(want) or "earlyabsentee" not in tail.lower():
            continue
        url = href if href.startswith("http") else f"{BASE}/{tail}"
        if url not in found:
            found.append(url)
    return found


def _absent(exc: SourceError) -> bool:
    """True if this failure means "no such file" rather than "we broke".

    Tennessee's S3 bucket answers a key that does NOT exist with **403
    AccessDenied**, not 404, because listing the bucket is denied. Verified:
    `20241105EarlyAbsentee1101.xlsx` returns HTTP 403 with a 243-byte
    application/xml body while `...1031.xlsx` returns the workbook. So for this
    host a 403 is absence, and only some OTHER error is a real fault worth
    falling through a tier for.
    """
    return "HTTP 403" in str(exc)


class TNScraper(Adapter):
    """Tier 1 for Tennessee: the SoS early-voting and absentee workbook."""

    state = "TN"
    name = "tn-sos"
    tier = TIER_SCRAPER

    # ------------------------------------------------------------------
    def _index_urls(self, cycle: int) -> list[str]:
        try:
            body = get(INDEX, state="TN", filename="early_voting_data.html",
                       min_bytes=512)
        except Exception:  # noqa: BLE001 -- 404 off-season is the normal case
            log.debug("TN: early-voting landing page unavailable")
            return []
        return discover(body.decode("utf-8", errors="replace"), cycle)

    def _candidates(self, cycle: int, as_of: date) -> list[str]:
        urls = self._index_urls(cycle)
        newest = min(as_of, election_date(cycle))
        for offset in range(LOOKBACK_DAYS + 1):
            for name in filenames(cycle, newest - timedelta(days=offset)):
                url = f"{BASE}/{name}"
                if url not in urls:
                    urls.append(url)
        return urls

    def _load(self, urls: list[str], *, use_cache: bool) -> bytes | None:
        for url in urls:
            name = url.rsplit("/", 1)[-1]
            try:
                body = get(url, state="TN", filename=name,
                           use_cache=use_cache, min_bytes=4096)
            except Missing:
                continue
            except SourceError as exc:
                if _absent(exc):
                    continue
                raise
            if looks_like_xlsx(body):
                return body
        return None

    # ------------------------------------------------------------------
    def fetch(self, cycle: int, as_of: date) -> FetchResult:
        body = self._load(self._candidates(cycle, as_of), use_cache=False)
        if body is None:
            raise NotYetPublished(
                f"TN: no early-voting workbook posted for {cycle} in the "
                f"{LOOKBACK_DAYS} days to {as_of.isoformat()}"
            )
        return parse(body, cycle, as_of)

    def fetch_history(self, cycle: int) -> FetchResult:
        """A past cycle's final workbook, which already holds every day."""
        if cycle >= datetime.now().year:
            raise NotYetPublished(f"TN: {cycle} is not an archived cycle")
        end = election_date(cycle)
        urls = []
        for offset in range(LOOKBACK_DAYS + 1):
            urls.extend(f"{BASE}/{n}" for n in filenames(cycle, end - timedelta(days=offset)))
        body = self._load(urls, use_cache=True)
        if body is None:
            raise NotYetPublished(f"TN: no archived early-voting workbook for {cycle}")
        return parse(body, cycle, end)
