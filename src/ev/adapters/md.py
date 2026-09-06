"""Maryland: the State Board of Elections' early-voting RAW data file.

The Maryland SBE publishes `EarlyVoting RAW data.csv` under its press room: one
row per (county, district, precinct, voter status, gender, age band, party,
early-vote center) cell, and then EIGHT day columns -- `Day1` .. `Day8` --
carrying that cell's ballot count on each day of the early-voting period.

Those day columns make Maryland behave like North Carolina rather than like a
snapshot source: ONE download reconstructs the entire daily curve, so a missed
run cannot lose a day and a past cycle is a single file rather than an archive
hunt. Four things drive everything below.

* **The file carries no dates.** `Day1..Day8` are positional. Maryland's window
  is statutory -- eight days, from the second Thursday before Election Day
  through the Thursday before it -- so Day1 is always Election Day minus twelve.
  Verified against all three cycles in `calendar.py`: 2022-11-08 -> 2022-10-27,
  2024-11-05 -> 2024-10-24, 2026-11-03 -> 2026-10-22, each a Thursday, because
  Election Day is always a Tuesday. We require EXACTLY eight day columns and
  raise SchemaDrift otherwise, so if Maryland ever lengthens the window this
  fails loudly instead of publishing every count shifted by a day.

* **The cycle's GENERAL election, never its primary.** SBE files live under
  `press_room/<cycle>_stats/<code>/`, where `<code>` is `GG22` / `PG24` style:
  the first letter is the cycle type (Gubernatorial / Presidential) and the
  SECOND is the election type (General / Primary). The bare `<cycle>_stats/`
  folder ALSO holds an `EarlyVoting RAW data.csv`, and for both 2022 and 2024
  that one is the PRIMARY's -- so we never read it. Publishing a primary's early
  vote as the general's would understate by a factor of three or more, on a page
  whose entire job is that number.

* **In-person only.** This file is the early-voting centers. Maryland's mail
  ballots live in a separate `Absentees_Sent_and_Returned_by_County.xlsx` which
  the SBE server currently serves as a corrupt zip, so `mail_requested` and
  `mail_returned` stay None -- "not reported", not zero. `ballots_total` is the
  in-person count, which is what this source reports, exactly as Michigan's
  mail-only file sets `ballots_total` to its returns.

* **Age is deliberately NOT published.** Maryland's four bands -- 18-24 / 25-44 /
  45-64 / 65 and older -- do not nest inside `normalize.AGE_BANDS`.
  `age_band("25-44")` returns "25-34" and `age_band("45-64")` returns "45-54":
  recognised, and wrong. Publishing two-thirds of Maryland's voters under the
  wrong band is worse than publishing no age breakdown, so we publish sex (which
  maps exactly) and nothing else. Race is not in the file.

Maryland registers voters by party, so the party fields are real counts and a
party sitting at 0 is a genuine zero rather than a blank.
"""

from __future__ import annotations

import csv
import io
import logging
import re
from collections import defaultdict
from datetime import date, timedelta

from ..calendar import election_date
from ..normalize import (
    PARTY_DEM, PARTY_NPA, PARTY_OTH, PARTY_REP, party as _party, sex as _sex,
)
from ..schema import TIER_SCRAPER, CountyDay, DemoDay, StateDay
from . import _fips
from ._net import Missing, get, looks_like_html
from .base import Adapter, FetchResult, NotYetPublished, SchemaDrift

log = logging.getLogger(__name__)

BASE = "https://elections.maryland.gov/press_room"

#: The press-room landing page lists every posted statistics file. We read it
#: first so that a folder Maryland names something we did not predict is still
#: found, and fall back to the literal codes below only if it does not parse.
INDEX = f"{BASE}/index.html"

FILENAME = "EarlyVoting%20RAW%20data.csv"

#: Election-code folders holding a cycle's GENERAL election, tried in order.
#:
#: VERIFIED on this network: `2022_stats/GG22/` and `2024_stats/PG24/` each
#: return the real multi-megabyte CSV (both are saved under tests/fixtures/md/).
#:
#: UNVERIFIED: `2026_stats/GG26/`. The 2026 general has not happened, and the SBE
#: CMS answers that path with a 200 HTML shell -- which is precisely the
#: NotYetPublished path this adapter is built around, and is what it returns
#: today. The code is derived from the two verified ones (gubernatorial cycle +
#: general election), and the index scrape above covers a different name.
GENERAL_CODES = ("GG{yy}", "PG{yy}")

#: Day1 is Election Day minus twelve; see the module docstring.
DAY1_OFFSET = 12
DAY_COUNT = 8
DAY_COLUMNS = tuple(f"Day{n}" for n in range(1, DAY_COUNT + 1))

REQUIRED = ("COUNTY_NAME", "GENDER_CODE", "PARTY_CODE") + DAY_COLUMNS

_PARTY_FIELD = {
    PARTY_DEM: "party_dem", PARTY_REP: "party_rep",
    PARTY_NPA: "party_npa", PARTY_OTH: "party_oth",
}

#: Maryland party codes that `normalize.party()` does not carry yet. Both are
#: real state-recognised parties that appear in the real files -- WCP is the
#: Working Class Party (2022 and 2026), NLM is No Labels Maryland (2024) -- so
#: raising drift on them would leave Maryland permanently broken, and silently
#: bucketing an UNKNOWN code would be exactly the bug rule 3 exists to prevent.
#: This table is therefore consulted ONLY after normalize returns None, and
#: anything in neither is still SchemaDrift. Reported upstream for normalize.py.
EXTRA_PARTY = {"wcp": PARTY_OTH, "nlm": PARTY_OTH}

#: A folder like "GG26" or "PG24": cycle type, then G for general, then the year.
_GENERAL_FOLDER = re.compile(r"^[GP]G(\d{2,4})$", re.IGNORECASE)
_INDEX_HREF = re.compile(
    r'href="([^"]*?(\d{4})_stats/([^"/]+)/EarlyVoting[%20\s]+RAW[%20\s]+data\.csv)"',
    re.IGNORECASE,
)


def day_dates(cycle: int) -> list[date]:
    """The eight calendar dates `Day1..Day8` stand for in `cycle`."""
    day1 = election_date(cycle) - timedelta(days=DAY1_OFFSET)
    return [day1 + timedelta(days=n) for n in range(DAY_COUNT)]


def general_paths(cycle: int) -> list[str]:
    """Candidate URLs for this cycle's general-election file, best first."""
    yy = f"{int(cycle) % 100:02d}"
    return [f"{BASE}/{cycle}_stats/{code.format(yy=yy)}/{FILENAME}"
            for code in GENERAL_CODES]


def discover(index_html: str, cycle: int) -> list[str]:
    """URLs the press-room index lists for this cycle's GENERAL election.

    Only folders whose code says "general" are accepted. The primary's file sits
    beside them under the same cycle folder and is the one mistake that would
    publish a confidently wrong headline, so an unmatched folder is skipped
    rather than tried.
    """
    yy = f"{int(cycle) % 100:02d}"
    found: list[str] = []
    for href, year, folder in _INDEX_HREF.findall(index_html or ""):
        if year != str(cycle):
            continue
        m = _GENERAL_FOLDER.match(folder)
        if not m or m.group(1)[-2:] != yy:
            continue
        url = href if href.startswith("http") else f"{BASE}/{href.lstrip('/')}"
        if url not in found:
            found.append(url.replace(" ", "%20"))
    return found


def _count(raw: str | None) -> int:
    """A day cell. Blank means no ballots in this cell on this day -- a real 0.

    Unlike an unreported measure, an empty day cell here is Maryland saying the
    cell had no ballots: the file is a dense cross-tabulation and only nonzero
    cells carry a number. Summing it as zero is correct; treating it as unknown
    would blank out almost every count in the file.
    """
    text = (raw or "").strip().replace(",", "")
    if not text:
        return 0
    try:
        return int(float(text))
    except ValueError as exc:
        raise SchemaDrift(f"MD: {raw!r} is not a ballot count") from exc


def party_bucket(raw: str | None) -> str | None:
    """Map a Maryland party code onto a canonical bucket.

    None only for a blank cell; an unrecognised code raises, per rule 3.
    """
    code = (raw or "").strip()
    if not code:
        return None
    bucket = _party(code) or EXTRA_PARTY.get(code.lower())
    if bucket is None:
        raise SchemaDrift(f"MD: unrecognised party code {code!r}")
    return bucket


class _Bucket:
    """Per-day tallies for one geography."""

    __slots__ = ("total", "party")

    def __init__(self) -> None:
        self.total = 0
        self.party: dict[str, int] = defaultdict(int)

    def add(self, other: "_Bucket") -> None:
        self.total += other.total
        for key, count in other.party.items():
            self.party[key] += count


def parse(body: bytes, cycle: int, as_of: date) -> FetchResult:
    """Parse one Maryland early-voting RAW file into canonical rows."""
    if looks_like_html(body):
        # The SBE CMS answers an unposted file with a styled 200 page.
        raise NotYetPublished(f"MD: {cycle} early-voting file came back as HTML")

    reader = csv.DictReader(io.StringIO(body.decode("utf-8-sig", errors="replace")))
    missing = [c for c in REQUIRED if c not in (reader.fieldnames or [])]
    if missing:
        raise SchemaDrift(f"MD: early-voting file is missing columns {missing}")
    extra_days = [c for c in (reader.fieldnames or []) if re.fullmatch(r"Day\d+", c)]
    if len(extra_days) != DAY_COUNT:
        # The eight columns ARE the calendar; a ninth would silently redate the
        # whole series. See the module docstring.
        raise SchemaDrift(
            f"MD: expected {DAY_COUNT} day columns, found {len(extra_days)}: {extra_days}"
        )

    dates = day_dates(cycle)
    span = [d for d in dates if d <= as_of]
    if not span:
        raise NotYetPublished(
            f"MD: early voting opens {dates[0].isoformat()}, after {as_of.isoformat()}"
        )

    by_state: dict[date, _Bucket] = defaultdict(_Bucket)
    by_county: dict[tuple[str, date], _Bucket] = defaultdict(_Bucket)
    by_demo: dict[tuple[date, str, str], int] = defaultdict(int)
    county_names: dict[str, str] = {}
    unknown: set[str] = set()

    for row in reader:
        name = " ".join((row.get("COUNTY_NAME") or "").split())
        if not name:
            continue
        hit = _fips.lookup("MD", name)
        if hit is None:
            unknown.add(name)
            continue
        fips, canonical = hit
        county_names[fips] = canonical

        bucket = party_bucket(row.get("PARTY_CODE"))
        gender = _sex(row.get("GENDER_CODE"))

        for column, day in zip(DAY_COLUMNS, dates):
            count = _count(row.get(column))
            if not count or day > as_of:
                continue
            for target in (by_state[day], by_county[(fips, day)]):
                target.total += count
                if bucket:
                    target.party[bucket] += count
            if gender:
                by_demo[(day, "sex", gender)] += count

    if unknown:
        # Maryland has exactly 24 localities and they do not change; a name we
        # cannot place is a county that would vanish off the map.
        raise SchemaDrift(f"MD: unrecognised county names {sorted(unknown)[:5]}")
    if not county_names:
        raise SchemaDrift("MD: early-voting file produced no county rows")

    return _emit(by_state, by_county, by_demo, county_names, cycle, span)


def _emit(by_state, by_county, by_demo, county_names, cycle, span) -> FetchResult:
    result = FetchResult()

    running = _Bucket()
    for day in span:
        today = by_state.get(day)
        if today:
            running.add(today)
        result.state_rows.append(StateDay(
            cycle=cycle, state="MD", day=day,
            ballots_total=running.total,
            ballots_new=today.total if today else 0,
            # This file is the early-voting centers only; Maryland's mail
            # ballots are a separate report. Blank, never 0 -- see THE BLANK
            # RULE in schema.py.
            mail_requested=None,
            mail_returned=None,
            inperson=running.total,
            # Maryland registers by party and reports every bucket, so a party
            # with no ballots yet is a genuine 0, not "not reported".
            **{field: running.party.get(key, 0) for key, field in _PARTY_FIELD.items()},
        ))

    for fips in sorted(county_names):
        running = _Bucket()
        for day in span:
            today = by_county.get((fips, day))
            if today:
                running.add(today)
            if running.total == 0:
                continue
            result.county_rows.append(CountyDay(
                cycle=cycle, state="MD", county_fips=fips, day=day,
                county_name=county_names[fips],
                ballots_total=running.total,
                ballots_new=today.total if today else 0,
                mail_returned=None,
                inperson=running.total,
                **{field: running.party.get(key, 0)
                   for key, field in _PARTY_FIELD.items()},
            ))

    cumulative: dict[tuple[str, str], int] = defaultdict(int)
    for day in span:
        for (dd, dimension, bucket), count in by_demo.items():
            if dd == day:
                cumulative[(dimension, bucket)] += count
        for (dimension, bucket), total in sorted(cumulative.items()):
            result.demo_rows.append(DemoDay(
                cycle=cycle, state="MD", day=day,
                dimension=dimension, bucket=bucket, ballots_total=total,
            ))
    return result


class MDScraper(Adapter):
    """Tier 1 for Maryland: the SBE early-voting RAW data file."""

    state = "MD"
    name = "md-sbe"
    tier = TIER_SCRAPER

    # ------------------------------------------------------------------
    def _index_urls(self, cycle: int) -> list[str]:
        try:
            body = get(INDEX, state="MD", filename="press_room_index.html",
                       min_bytes=1024)
        except Exception:  # noqa: BLE001 -- discovery is a nicety, not a source
            log.debug("MD: press-room index unavailable; using literal codes")
            return []
        return discover(body.decode("utf-8", errors="replace"), cycle)

    def _candidates(self, cycle: int) -> list[str]:
        urls = self._index_urls(cycle)
        for url in general_paths(cycle):
            if url not in urls:
                urls.append(url)
        return urls

    def _load(self, cycle: int, *, use_cache: bool) -> bytes:
        problems: list[str] = []
        for url in self._candidates(cycle):
            slug = url.rsplit("/", 2)[-2] if "/" in url else str(cycle)
            try:
                body = get(url, state="MD",
                           filename=f"{cycle}_{slug}_EarlyVoting_RAW_data.csv",
                           use_cache=use_cache, min_bytes=4096)
            except Missing as exc:
                problems.append(str(exc))
                continue
            if looks_like_html(body):
                problems.append(f"{url} came back as HTML, not CSV")
                continue
            return body
        raise NotYetPublished(
            f"MD: no general-election early-voting file posted for {cycle} yet "
            f"({'; '.join(problems) or 'no candidate URLs'})"
        )

    # ------------------------------------------------------------------
    def fetch(self, cycle: int, as_of: date) -> FetchResult:
        return parse(self._load(cycle, use_cache=False), cycle, as_of)

    def fetch_history(self, cycle: int) -> FetchResult:
        """A past cycle is the same file; all eight days are already in it."""
        return parse(self._load(cycle, use_cache=True), cycle, election_date(cycle))
