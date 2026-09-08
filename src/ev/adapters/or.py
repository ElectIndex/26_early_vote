"""Oregon: the Secretary of State's "Daily Ballot Returns" report (PDF).

Oregon votes entirely by mail and publishes, most days of the return period, one
PDF carrying the whole picture: a statewide topline, ballots returned by county,
ballots returned by county **by day**, and ballots returned by county **by
party** across up to twelve parties. `docs/coverage-research.md` called it "the
highest-value PDF in the country" and rejected it only because "this pipeline has
no PDF dependency".

**That reason is gone.** `pypdf` is a declared dependency and `ia.py` already
parses Iowa's absentee report out of one, so the only thing that ever stood
between this repo and Oregon's file was a library that is now installed.

Because the county-by-day page carries every day of the season, ONE download
reconstructs the entire daily curve -- North Carolina style -- so a run we miss
is not a day of Oregon's series lost forever.

--------------------------------------------------------------------------
TWO LAYOUTS, AND THE 2026 SWITCH
--------------------------------------------------------------------------

Oregon rebuilt the report between the November 2025 election and the May 2026
primary. Both layouts are parsed here because the backfill needs the old one and
the live season needs the new one:

* **CLASSIC** (verified: 2022 general, 2024 general, November 2025). Page 1 is
  the summary, page 2 the county-by-day matrix, then one or two party pages, and
  a final cross-cycle comparison page we ignore. The party pages' column headers
  are **rotated 90 degrees**, and pypdf emits them AFTER the data rows and in an
  order that is not the column order -- so they are recovered from their text
  matrices' x coordinates and sorted, never from the order they extract in.

* **POWER BI** (verified: May 2026 primary, `Election Date: 5/19/2026`). A
  Power BI export. Every page carries `Election Date:` and `Report Generated:`,
  which is the provenance the classic layout only ever printed as a bare
  `10/25/22`. Its tables have **blank cells** -- Gilliam has no Progressive
  ballots and Power BI prints nothing rather than a zero -- so those pages cannot
  be parsed by splitting text on whitespace. They are read in pypdf's
  `extraction_mode="layout"`, which preserves the fixed-width columns, and each
  cell is assigned to the header whose character range it overlaps.

The 2026 GENERAL has not been published yet, so which layout it uses is
**unverified**; the dispatcher reads the layout off the file rather than off the
cycle, so either one works.

--------------------------------------------------------------------------
JUDGEMENT CALLS
--------------------------------------------------------------------------

* **"Independent" is a PARTY here, not an unaffiliated voter.** The Independent
  Party of Oregon is ballot-qualified and had 150,715 registrants in 2024;
  Oregon's unaffiliated bucket is spelled "Nonaffiliated". `normalize.party()`
  maps the bare word "independent" to `npa`, which is right for most states and
  wrong for this one by 150,000 voters, so Oregon's labels go through the
  documented `OR_PARTY` table below and anything not in it raises SchemaDrift.
  `normalize.party()` also does not recognise "nonaffiliated", "pacific green",
  "progressive" or "we the people" at all. See the note in
  `docs/coverage-research.md`.

* **Every returned ballot is a mail ballot, so `mail_returned == ballots_total`.**
  Oregon mails a ballot to every registered voter and has no in-person early
  voting to report; `inperson` is therefore `None` (not reported), never 0. This
  is not an inference about unreported data -- it is what an all-mail election
  is.

* **`mail_requested` stays None.** Nobody in Oregon requests a ballot. The
  report's "Eligible" column is registered voters, which is a denominator, not a
  request count.

* **The daily matrix and the summary page disagree, and the summary wins.** On
  the 2022-10-25 report the day columns sum to 65,202 statewide while the
  summary says 65,944, and Curry County's three days sum to 991 against a
  summary figure of 1,290: counties backfill late reports into the summary
  without restating the day they arrived on. So the as-of day's `ballots_total`
  is always the SUMMARY's number, and the earlier days' cumulative figures are
  the running sum of the matrix, which is the only thing the file says about
  them. `ballots_new` on the as-of day is the matrix's last column, which is why
  `ballots_new` can be smaller than the day-over-day change in `ballots_total`.
  (On the 2024 file they agree exactly: 2,004,468 both ways.)

* **The report is generated the morning after the day it covers.** The
  post-election 2022 file is stamped 2022-11-09 and its last day column is
  2022-11-08, so the two are NOT required to be equal -- only that no day column
  is dated after the stamp, which really would be impossible. When they differ,
  the day columns carry the curve and the summary's cumulative figure is filed
  under the stamp with `ballots_new` left None, because the file says nothing
  about how many of those ballots arrived that day.

* **Future days print as literal zeros in some reports and are absent in
  others.** The November 2025 file lists all thirteen planned dates and writes
  `0` for the eight that had not happened; the 2022-10-25 file lists thirteen
  dates and prints only three values per county. Both are handled by keeping
  only the dates on or before the report's own as-of date -- a `0` on a day that
  has happened is a real zero and is published as one.

* **Party totals are cross-checked against the summary.** Every county's party
  columns must sum to the total the summary page gives for that county, or the
  file raises SchemaDrift rather than publishing a party split we mis-assigned.

--------------------------------------------------------------------------
THE ARCHIVE SWEEP, AND WHY ONE CAPTURE IS NOT ENOUGH
--------------------------------------------------------------------------

One report rebuilds the whole daily curve, but it attaches its PARTY split to
one day only -- its own as-of day. Reading a single archived capture per cycle,
which is what this adapter used to do, therefore published a fourteen-day county
curve whose only party row sat at `days_to_election = -1`, and both
`estimate.read_state_daily` and `counterfactual.read_series` discard `dte < 0`.
Oregon -- county returns WITH a party split, the richest shape any state
publishes -- yielded zero folds for either model.

So `fetch_history` sweeps EVERY archived capture (`fl.py` and `de.py` do the
same) and merges them. The merge rule is one line and it is not the same one:

    each day is published as the EARLIEST report that covers it read that day.

That is the reading a daily ingest running that October would have written, and
it is the only rule that keeps the series monotone. The alternatives both fail
on real numbers:

* *Latest capture wins* (fl.py's rule, right for a source where one capture is
  one day) throws the party split away, because only a report's own as-of day
  has one.
* *The day's own capture wins, other days from the latest* mixes two readings of
  the same day and the curve goes BACKWARDS: the 2022-10-25 report's summary
  says 65,944 ballots statewide, while the 2022-11-04 report's day matrix says
  128,787 had arrived by 10-25 and 79,739 by 10-24. Publishing 79,739 on 10-24
  and 65,944 on 10-25 is a cumulative total that falls.

Taking each capture's whole prefix keeps every row internally consistent -- the
county distribution, the statewide total and the party split on a given day all
come from ONE file, which is what the party model needs, since it reads the
geography from the county rows and the truth from the party columns and would
otherwise be told about two different moments. VERIFIED on the merged output:
zero decreasing steps, statewide or in any of the 36 counties, in either cycle.

Cost of the rule: a report generated the morning after the day it is stamped
(2022-11-04 carries columns only through 11-03) files its summary under the
stamp, so 11-03 and 11-04 both read 869,375. That is what Oregon published on
the morning of 11-04, and the party split that comes with it is the split of
exactly those 869,375 ballots.
"""

from __future__ import annotations

import io
import json
import logging
import re
from datetime import date, timedelta

import pypdf

from ..calendar import election_date
from ..normalize import PARTY_DEM, PARTY_NPA, PARTY_OTH, PARTY_REP
from ..schema import TIER_SCRAPER, CountyDay, StateDay
from . import _fips
from ._net import DEFAULT_MIN_INTERVAL, Missing, get, looks_like_html
from .base import Adapter, FetchResult, NotYetPublished, SchemaDrift, SourceError

log = logging.getLogger(__name__)

#: The page the SoS links the live report from. VERIFIED 200 (2026-09-06). Its
#: October-2025 capture links `november-2025-Daily-Ballot-Returns.pdf` and its
#: October-2024 capture links `G24-Daily-Ballot-Returns.pdf`, so this is where a
#: filename we did not predict will be found.
CURRENT_ELECTION_URL = "https://sos.oregon.gov/voting/Pages/current-election.aspx"

SOS = "https://sos.oregon.gov"

#: Archived general-election reports: (live URL, capture stamps known to parse).
#: Both URLs are 404 on sos.oregon.gov today -- Oregon deletes the report after
#: each election -- so the archive is the only copy.
#:
#: These stamps are a FLOOR, not the sweep. `fetch_history` asks the Wayback CDX
#: index for every other capture of the same URL and merges them all, because a
#: report's party split reaches one day only and one capture is therefore one
#: party day. The list below is what was VERIFIED 200 and parsed end to end from
#: this network on 2026-09-08, kept so a backfill still works when the CDX index
#: is unreachable, and each stamp names its report's own as-of day:
#:
#:   2022  20221025193050 -> 10-25   20221104231413 -> 11-04 (columns end 11-03)
#:         20221108071723 -> 11-07   20221108183430 -> 11-08
#:         20221110100105 -> 11-09 (columns end 11-08)
#:   2024  20241106082418 -> 11-05   20241111105708 -> 11-06 (columns end 11-06)
#:
#: The post-canvass FINAL versions are no longer excluded by hand, because they
#: do not need to be. Oregon replaces the report weeks later with a seven-page
#: file whose party table gains a second, supplemental section: its own page-2
#: party columns come to 2,304,398 against a printed total of 2,307,070, and
#: adding the supplement overshoots to 2,317,716. Neither reading reconciles, so
#: `_check_party` refuses it -- VERIFIED, the 2024-11-23 capture raises
#: SchemaDrift on Baker County (9,928 against a reported 9,938) -- and the sweep
#: skips it and keeps the during-season captures, which is exactly what the
#: hand-curated list used to arrange.
ARCHIVED: dict[int, tuple[str, tuple[str, ...]]] = {
    2022: (f"{SOS}/elections/Documents/statistics/G22-Daily-Ballot-Returns.pdf",
           ("20221025193050", "20221104231413", "20221108071723",
            "20221108183430", "20221110100105")),
    2024: (f"{SOS}/voting/Documents/G24-Daily-Ballot-Returns.pdf",
           ("20241106082418", "20241111105708")),
}

WAYBACK = "https://web.archive.org/web/{stamp}id_/{url}"

CDX_URL = "http://web.archive.org/cdx/search/cdx"

#: web.archive.org is slower and less tolerant than a state host. See
#: DEFAULT_MIN_INTERVAL in _net for what a real ban cost us.
ARCHIVE_MIN_INTERVAL = 1.0

#: VERIFIED 2026-09-08: the CDX index lists 14 captures of the 2022 report and 10
#: of the 2024 one; `collapse=digest` reduces them to 5 and 3, because the
#: crawler visits far more often than Oregon regenerates the file. 60 leaves room
#: for a cycle the Archive crawled harder without ever running away.
MAX_ARCHIVE_PROBES = 60

#: How far either side of Election Day to look. The report only exists during the
#: return period, so this is a politeness budget rather than a filter: the file
#: itself names its election and `parse` refuses anything else. Wide enough after
#: the election to still reach the post-canvass final, which is then refused on
#: its own arithmetic rather than by being hidden from the sweep.
ARCHIVE_WINDOW_BEFORE = timedelta(days=120)
ARCHIVE_WINDOW_AFTER = timedelta(days=45)


def _archive_stamps(url: str, cycle: int) -> list[str]:
    """Wayback timestamps of every distinct version of `url` in `cycle`'s window.

    `collapse=digest` is what turns a crawler's repeat visits into the handful of
    genuinely distinct reports: the 2022 URL is captured fourteen times and
    Oregon regenerated the file five times, and an unchanged file is not another
    day of the curve.
    """
    anchor = election_date(cycle)
    query = {
        "url": url, "output": "json", "fl": "timestamp,statuscode,digest",
        "filter": "statuscode:200", "collapse": "digest",
        "from": (anchor - ARCHIVE_WINDOW_BEFORE).strftime("%Y%m%d"),
        "to": (anchor + ARCHIVE_WINDOW_AFTER).strftime("%Y%m%d"),
        "limit": str(MAX_ARCHIVE_PROBES),
    }
    try:
        body = get(CDX_URL, state="OR", filename=f"cdx-{cycle}.json", params=query,
                   min_bytes=2, min_interval=ARCHIVE_MIN_INTERVAL)
    except Missing:
        # The CDX API answers "nothing archived" with an empty body, which
        # _net.get reports as Missing. That is absence, not a fault.
        return []
    except SourceError as exc:
        # The index being down must not cost us the cycle: ARCHIVED's own stamps
        # are still there and every one of them is known to parse.
        log.warning("OR: the Wayback CDX index is unreachable (%s)", exc)
        return []
    try:
        rows = json.loads(body.decode("utf-8", errors="replace"))
    except ValueError as exc:
        raise SourceError(f"OR: the Wayback CDX index was not JSON: {exc}") from exc
    return [row[0] for row in rows[1:] if row and str(row[0]).isdigit()]


#: Filenames Oregon has actually used, newest first. UNVERIFIED for the 2026
#: general -- no such file exists yet -- and only tried after the SoS page has
#: been scraped, which is how every previous cycle's real name was found.
FALLBACK_PATHS: tuple[str, ...] = (
    "/elections/Documents/{cycle}-general-Daily-Ballot-Returns.pdf",
    "/elections/Documents/november-{cycle}-Daily-Ballot-Returns.pdf",
    "/voting/Documents/G{yy}-Daily-Ballot-Returns.pdf",
    "/elections/Documents/statistics/G{yy}-Daily-Ballot-Returns.pdf",
)

_REPORT_LINK = re.compile(
    r'href="([^"]*[Dd]aily-[Bb]allot-[Rr]eturns\.pdf)"', re.I
)

# --------------------------------------------------------------------------
# Oregon's party vocabulary.
#
# Deliberately NOT normalize.party(): that maps the bare word "independent" to
# the unaffiliated bucket, which is correct in most states and wrong in Oregon,
# where the Independent Party of Oregon is a ballot-qualified minor party with
# 150,715 registrants in the 2024 file. Oregon's unaffiliated voters are
# "Nonaffiliated", which normalize.party() does not recognise at all.
# Anything absent from this table raises SchemaDrift; it is never bucketed.
# --------------------------------------------------------------------------
OR_PARTY: dict[str, str] = {
    "democrat": PARTY_DEM,
    "republican": PARTY_REP,
    "nonaffiliated": PARTY_NPA,
    "constitution": PARTY_OTH,
    "independent": PARTY_OTH,
    "libertarian": PARTY_OTH,
    "no labels": PARTY_OTH,
    "other": PARTY_OTH,
    "pacific green": PARTY_OTH,
    "progressive": PARTY_OTH,
    "we the people": PARTY_OTH,
    "working families party of oregon": PARTY_OTH,
    "working families": PARTY_OTH,
}

TOTAL_LABEL = "total"
#: Oregon spells its total row differently on every page, and pypdf splits
#: "Statewide\nTotals" across two lines on the 2024 party page, so the label
#: that reaches us there is a bare "Totals".
STATEWIDE_LABELS = {
    "total", "totals", "statewide", "statewide total", "statewide totals",
}

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

_NUMBER = re.compile(r"^-?[\d,]+$")
_DASH = re.compile(r"^[\u2013\u2014-]$")
#: Excel writes ##### into a cell too narrow to render. It occupies a column
#: and carries no value, so it must not shift the columns to its right.
#: VERIFIED: the 2022-10-25 party page renders statewide Nonaffiliated
#: registration as "#######".
_HASHES = re.compile(r"^#+$")
#: Anything that occupies a numeric column, readable or not.
_VALUE = re.compile(r"^(?:-?[\d,]+|[\u2013\u2014-]|#+)$")
_ASOF = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{2})$")
_ELECTION_LINE = re.compile(r"^([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})\s+GENERAL", re.I)
_PBI_ELECTION = re.compile(r"Election Date:\s*(\d{1,2})/(\d{1,2})/(\d{4})")
_PBI_GENERATED = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})\s+\d{1,2}:\d{2}\s*[AP]M")
_DAY_HEADER = re.compile(r"^Statewide Ballot Returns by\s+(.*)$")
_MONTH_DAY = re.compile(r"([A-Za-z]{3})\s+(\d{1,2})")
_SLASH_DAY = re.compile(r"^(\d{1,2})/(\d{1,2})$")

#: Header cells in layout-mode text are separated by two or more spaces.
_CELL = re.compile(r"\S+(?: \S+)*")


def _int(raw: str | None) -> int | None:
    """A count, or None for a cell the report left blank.

    An en-dash is Oregon's rendering of zero in the summary table, not a blank;
    see the note in `classic_summary`.
    """
    if raw is None:
        return None
    text = raw.strip().replace(",", "")
    if not text:
        return None
    if _DASH.match(text):
        return 0
    if _HASHES.match(text):
        return None
    try:
        return int(text)
    except ValueError as exc:
        raise SchemaDrift(f"OR: {raw!r} is not a count") from exc


#: 5-digit FIPS -> census county name, so county rows carry the site's spelling
#: rather than Oregon's ("BAKER" in the Power BI layout, "Baker" in the classic).
_NAMES: dict[str, str] = {
    _fips.lookup("OR", name)[0]: name for name in _fips.names("OR")
}


def _county(name: str) -> tuple[str, str]:
    hit = _fips.lookup("OR", name)
    if hit is None:
        raise SchemaDrift(f"OR: unrecognised county name {name!r}")
    return hit


def _party(label: str) -> str:
    key = " ".join(label.strip().lower().split())
    bucket = OR_PARTY.get(key)
    if bucket is None:
        raise SchemaDrift(
            f"OR: unrecognised party column {label!r}. Add it to OR_PARTY only "
            f"after deciding whether it is a real party or an unaffiliated bucket"
        )
    return bucket


#: Power BI draws its icons from a private-use font range. They extract as
#: stray characters that sit in a table's own columns and would otherwise be
#: folded into a heading, so they are blanked before anything is parsed.
_PRIVATE_USE = re.compile("[\ue000-\uf8ff]")


def _pages(body: bytes, *, layout: bool = False) -> list[str]:
    try:
        reader = pypdf.PdfReader(io.BytesIO(body))
        mode = {"extraction_mode": "layout"} if layout else {}
        return [_PRIVATE_USE.sub(" ", page.extract_text(**mode))
                for page in reader.pages]
    except Exception as exc:  # noqa: BLE001 - pypdf raises a zoo of types
        raise SourceError(f"OR: report is not a readable PDF: {exc}") from exc


def _rotated_headers(body: bytes, index: int) -> list[tuple[str, str]]:
    """(kind, party) per column of a CLASSIC party page, left to right.

    The headers are drawn rotated, and pypdf emits them after the data rows in
    an order that is not the column order -- so they are read off their text
    matrices and sorted by x. Everything else in this module works off extracted
    text; this one page cannot.
    """
    reader = pypdf.PdfReader(io.BytesIO(body))
    chunks: list[tuple[float, str]] = []

    def visit(text, cm, tm, font, size):  # noqa: ANN001 - pypdf's callback shape
        if text.strip() and abs(tm[1]) > 0.01:  # a rotated glyph run
            chunks.append((tm[4], text.strip()))

    reader.pages[index].extract_text(visitor_text=visit)
    ordered = [t for _, t in sorted(chunks, key=lambda c: c[0])]
    if not ordered or len(ordered) % 2:
        raise SchemaDrift(
            f"OR: party page {index} has {len(ordered)} rotated header chunks, "
            f"which cannot pair into (Eligible|/Returned|, party)"
        )
    columns: list[tuple[str, str]] = []
    for kind, party in zip(ordered[0::2], ordered[1::2]):
        if kind not in ("Eligible|", "Returned|"):
            raise SchemaDrift(f"OR: party header {kind!r} is not Eligible|/Returned|")
        columns.append((kind.rstrip("|").lower(), party))
    return columns


# --------------------------------------------------------------------------
# CLASSIC layout
# --------------------------------------------------------------------------
def _lines(page: str) -> list[str]:
    return [line.strip() for line in page.splitlines() if line.strip()]


def classic_election_year(pages: list[str]) -> int:
    """The election year off the report's own second line."""
    for page in pages:
        for line in _lines(page)[:4]:
            match = _ELECTION_LINE.match(line)
            if match:
                return int(match.group(3))
    raise SchemaDrift("OR: report has no '<Month D, YYYY> GENERAL ELECTION' line")


def classic_summary(pages: list[str]) -> tuple[date, dict[str, tuple[int, int]]]:
    """(as-of date, {county or 'statewide': (eligible, returned)}).

    The summary page is the one whose header block is followed immediately by a
    bare M/D/YY as-of line; the cross-cycle comparison page shares its title and
    its three-numbers-per-county shape but has no such line.
    """
    for page in pages:
        lines = _lines(page)
        stamp = next(
            (i for i, line in enumerate(lines[:8]) if _ASOF.match(line)), None
        )
        if stamp is None:
            continue
        month, day, year = (int(g) for g in _ASOF.match(lines[stamp]).groups())
        as_of = date(2000 + year, month, day)
        rows: dict[str, tuple[int, int]] = {}
        head = lines[stamp + 1:stamp + 4]
        if len(head) != 3 or not all(_NUMBER.match(v) or v.endswith("%") for v in head):
            raise SchemaDrift("OR: summary page has no statewide eligible/returned block")
        rows["statewide"] = (_int(head[0]), _int(head[1]))
        for line in lines[stamp + 4:]:
            cells = line.split()
            # A county that has returned NOTHING prints an en-dash, not a 0
            # (2022-10-25: "Columbia 41,998  –  0.0%"). That dash is a real
            # zero -- the same file's day matrix gives Columbia 0 0 0 and the
            # percentage column says 0.0% -- so it is read as one, and the
            # percentage is checked to make sure of it.
            numbers = [c for c in cells if _VALUE.match(c)]
            if len(numbers) < 2 or not cells[0].isalpha():
                continue
            if _DASH.match(numbers[1]):
                percent = next((c for c in cells if c.endswith("%")), None)
                if percent is None or float(percent.rstrip("%")) != 0.0:
                    raise SchemaDrift(
                        f"OR: summary row {line!r} has a dash for ballots returned "
                        f"but a non-zero return rate"
                    )
            name = " ".join(
                c for c in cells if not _VALUE.match(c) and "%" not in c
            )
            if not name:
                continue
            if name.lower() in STATEWIDE_LABELS:
                # Some cycles repeat the statewide figures as a footer row. It
                # must agree with the block at the top of the page, or one of
                # the two is not what we think it is.
                footer = (_int(numbers[0]), _int(numbers[1]))
                if footer != rows["statewide"]:
                    raise SchemaDrift(
                        f"OR: summary page's statewide header {rows['statewide']} "
                        f"disagrees with its own footer row {footer}"
                    )
                continue
            fips, _canonical = _county(name)
            rows[fips] = (_int(numbers[0]), _int(numbers[1]))
        if len(rows) < 30:
            raise SchemaDrift(f"OR: summary page produced only {len(rows) - 1} counties")
        return as_of, rows
    raise SchemaDrift("OR: report has no summary page")


def classic_daily(pages: list[str], year: int) -> tuple[list[date], dict[str, list[int]]]:
    """(dates, {county fips or 'statewide new'/'statewide cum': [counts]})."""
    for page in pages:
        lines = _lines(page)
        header = next((line for line in lines if _DAY_HEADER.match(line)), None)
        if header is None:
            continue
        dates = [
            date(year, _MONTHS[m.group(1)[:3].lower()], int(m.group(2)))
            for m in _MONTH_DAY.finditer(_DAY_HEADER.match(header).group(1))
        ]
        if not dates:
            raise SchemaDrift(f"OR: day header {header!r} carries no dates")
        series: dict[str, list[int]] = {}
        for line in lines:
            cells = line.split()
            numbers = [c for c in cells if _VALUE.match(c)]
            label = " ".join(c for c in cells if not _VALUE.match(c)).strip()
            if not numbers or not label:
                continue
            key = label.lower()
            if key.startswith("number of ballots returned on this day"):
                series["statewide new"] = [_int(n) for n in numbers]
            elif key.startswith("cumulative number of ballots returned"):
                series["statewide cum"] = [_int(n) for n in numbers]
            elif key.startswith(("daily return", "cumulative return", "statewide ballot")):
                continue
            else:
                hit = _fips.lookup("OR", label)
                if hit is None:
                    continue
                series[hit[0]] = [_int(n) for n in numbers]
        counties = {k: v for k, v in series.items() if not k.startswith("statewide")}
        if not counties:
            raise SchemaDrift("OR: county-by-day page produced no county rows")
        widths = {len(v) for v in counties.values()}
        if len(widths) != 1:
            raise SchemaDrift(f"OR: county-by-day rows have ragged widths {sorted(widths)}")
        width = widths.pop()
        if width > len(dates):
            raise SchemaDrift(
                f"OR: county-by-day rows carry {width} values for {len(dates)} dates"
            )
        return dates[:width], series
    raise SchemaDrift("OR: report has no county-by-day page")


def classic_party(body: bytes, pages: list[str]) -> dict[str, dict[str, int]]:
    """{county fips: {party bucket: ballots returned}} across every party page."""
    out: dict[str, dict[str, int]] = {}
    for index, page in enumerate(pages):
        lines = _lines(page)
        if not any(line.lower().startswith("statewide") for line in lines[:8]):
            continue
        if any(_DAY_HEADER.match(line) for line in lines) or any(
            _ASOF.match(line) for line in lines[:8]
        ):
            continue
        try:
            columns = _rotated_headers(body, index)
        except SchemaDrift:
            continue
        for line in lines:
            cells = line.split()
            numbers = [c for c in cells if _VALUE.match(c)]
            label = " ".join(
                c for c in cells if not _VALUE.match(c) and "%" not in c
            ).strip()
            if not numbers or not label:
                continue
            if label.lower() in STATEWIDE_LABELS:
                key = "statewide"
            else:
                hit = _fips.lookup("OR", label)
                if hit is None:
                    continue
                key = hit[0]
            if len(numbers) != len(columns):
                raise SchemaDrift(
                    f"OR: party row {label!r} has {len(numbers)} values for "
                    f"{len(columns)} columns"
                )
            bucket = out.setdefault(key, {})
            for (kind, party), raw in zip(columns, numbers):
                if kind != "returned" or party.strip().lower() == TOTAL_LABEL:
                    continue
                value = _int(raw)
                if value is None:
                    raise SchemaDrift(
                        f"OR: party row {label!r} has an unreadable returned cell "
                        f"{raw!r} under {party!r}"
                    )
                key = _party(party)
                bucket[key] = bucket.get(key, 0) + value
    return out


# --------------------------------------------------------------------------
# POWER BI layout
# --------------------------------------------------------------------------
def pbi_dates(pages: list[str]) -> tuple[date, date]:
    """(election date, report-generated date) -- both printed on every page.

    Read from the LAYOUT extraction: in plain mode Power BI's labels and their
    values arrive in separate runs ("...7:37 AMReport Generated:Election Date:"),
    so the label cannot be used to find the value it belongs to.
    """
    joined = "\n".join(pages)
    election = _PBI_ELECTION.search(joined)
    if election is None:
        raise SchemaDrift("OR: Power BI report carries no 'Election Date:' line")
    month, day, year = (int(g) for g in election.groups())
    generated = _PBI_GENERATED.search(joined)
    if generated is None:
        raise SchemaDrift("OR: Power BI report carries no 'Report Generated:' stamp")
    gm, gd, gy = (int(g) for g in generated.groups())
    return date(year, month, day), date(gy, gm, gd)


def _columns(header: str) -> list[tuple[str, int, int]]:
    return [(m.group(0).strip(), m.start(), m.end()) for m in _CELL.finditer(header)]


def _assign(row: str, columns: list[tuple[str, int, int]]) -> dict[str, str]:
    """Map a layout-mode data row's cells onto the header cells they sit under.

    Power BI leaves a cell EMPTY rather than writing 0, so a row cannot be
    aligned by counting tokens; it is aligned by character position instead.
    Numbers are right-aligned under their heading, so a cell is filed under the
    column whose span it overlaps, and failing that the nearest column to its
    right edge.
    """
    out: dict[str, str] = {}
    for match in _CELL.finditer(row):
        start, end = match.start(), match.end()
        best, score = None, None
        for label, cstart, cend in columns:
            overlap = min(end, cend) - max(start, cstart)
            distance = abs(end - cend)
            rank = (-overlap, distance)
            if score is None or rank < score:
                best, score = label, rank
        if best is not None:
            out[best] = (out.get(best, "") + " " + match.group(0)).strip()
    return out


def _pbi_table(layout_pages: list[str], title: str) -> tuple[list[tuple[str, int, int]], list[str]]:
    """(header columns, data rows) for the Power BI page whose title matches."""
    for page in layout_pages:
        lines = page.splitlines()
        head = next((i for i, line in enumerate(lines) if title.lower() in line.lower()), None)
        if head is None:
            continue
        header = next(
            (i for i in range(head + 1, min(head + 4, len(lines)))
             if lines[i].strip().lower().startswith("county")),
            None,
        )
        if header is None:
            raise SchemaDrift(f"OR: {title!r} page has no 'County' header row")
        columns = _columns(lines[header])
        # Power BI wraps a long heading onto the NEXT line ("Working Families" /
        # "Party of Oregon"), so exactly one continuation line is folded back
        # into the columns it sits under. Every later digit-free line is the
        # page's footer disclaimer, which must not be.
        index = header + 1
        while index < len(lines) and not lines[index].strip():
            index += 1
        if index < len(lines) and not any(ch.isdigit() for ch in lines[index]):
            folded = _assign(lines[index], columns)
            columns = [
                (f"{label} {folded[label]}".strip() if label in folded else label, cs, ce)
                for label, cs, ce in columns
            ]
            index += 1
        rows: list[str] = []
        for line in lines[index:]:
            if not line.strip():
                continue
            if not any(ch.isdigit() for ch in line):
                if rows:
                    break
                continue
            rows.append(line)
        if not rows:
            raise SchemaDrift(f"OR: {title!r} page carries no data rows")
        return columns, rows
    raise SchemaDrift(f"OR: report has no {title!r} page")


def pbi_counties(layout_pages: list[str]) -> dict[str, tuple[int, int]]:
    """{county fips or 'statewide': (eligible, ballots received)}.

    This is the one Power BI page NOT read by character position, because its
    two rightmost headings are printed with no gap between them
    ("Ballots Received% Ballots Received") and so cannot be told apart as
    columns. Its rows have a fixed shape instead -- a name, then eligible,
    ballots received and a return rate -- and anything else raises SchemaDrift.
    """
    columns, rows = _pbi_table(layout_pages, "Unofficial Ballots Returns by County")
    header = " ".join(c[0] for c in columns).lower()
    for needed in ("county", "eligible", "received"):
        if needed not in header:
            raise SchemaDrift(
                f"OR: county page is missing a {needed!r} heading, got {header!r}"
            )
    out: dict[str, tuple[int, int]] = {}
    for row in rows:
        cells = row.split()
        values = [c for c in cells if _VALUE.match(c)]
        percents = [c for c in cells if c.endswith("%")]
        name = " ".join(
            c for c in cells if not _VALUE.match(c) and not c.endswith("%")
        ).strip()
        if not name:
            continue
        if len(values) == 1 and len(percents) == 1:
            # Nothing returned yet: Power BI leaves the cell empty rather than
            # writing 0, exactly as it does in the by-day and by-party tables.
            if float(percents[0].rstrip("%")) != 0.0:
                raise SchemaDrift(
                    f"OR: county row {row.strip()!r} has one count and a non-zero "
                    f"return rate"
                )
            values = [values[0], "0"]
        if len(values) != 2 or len(percents) != 1:
            raise SchemaDrift(
                f"OR: county row {row.strip()!r} is not "
                f"<name> <eligible> <received> <rate>"
            )
        key = "statewide" if name.lower() in STATEWIDE_LABELS else _county(name)[0]
        out[key] = (_int(values[0]), _int(values[1]))
    if "statewide" not in out or len(out) < 30:
        raise SchemaDrift(f"OR: county page produced {len(out)} rows")
    return out


def pbi_daily(layout_pages: list[str], year: int) -> tuple[list[date], dict[str, list[int | None]]]:
    columns, rows = _pbi_table(layout_pages, "by County by Day")
    county_col = columns[0][0]
    dates: list[date] = []
    day_columns: list[str] = []
    for label, _s, _e in columns[1:]:
        match = _SLASH_DAY.match(label.strip())
        if match:
            dates.append(date(year, int(match.group(1)), int(match.group(2))))
            day_columns.append(label)
    if not dates:
        raise SchemaDrift(f"OR: county-by-day page has no M/D columns, got {columns}")
    total_col = next((c[0] for c in columns if c[0].strip().lower() == TOTAL_LABEL), None)
    if total_col is None:
        raise SchemaDrift("OR: county-by-day page has no Total column to reconcile against")
    series: dict[str, list[int | None]] = {}
    for row in rows:
        cells = _assign(row, columns)
        name = cells.get(county_col, "").strip()
        if not name:
            continue
        values = [_int(cells.get(c)) for c in day_columns]
        stated = _int(cells.get(total_col))
        summed = sum(v for v in values if v is not None)
        if stated is not None and stated != summed:
            raise SchemaDrift(
                f"OR: county-by-day row {name!r} sums to {summed} against its own "
                f"Total of {stated}; the columns were mis-assigned"
            )
        if stated is not None:
            # The row reconciles against its own Total, which PROVES the empty
            # cells are zeros rather than unreported days -- Power BI simply
            # does not print a 0. Only then are they published as zeros; a row
            # whose Total is itself blank keeps its Nones.
            values = [0 if v is None else v for v in values]
        key = "statewide new" if name.lower() in STATEWIDE_LABELS else _county(name)[0]
        series[key] = values
    return dates, series


def pbi_party(layout_pages: list[str]) -> dict[str, dict[str, int]]:
    columns, rows = _pbi_table(layout_pages, "by County by Party")
    county_col = columns[0][0]
    party_columns = [
        c[0] for c in columns[1:] if c[0].strip().lower() != TOTAL_LABEL
    ]
    total_col = next((c[0] for c in columns if c[0].strip().lower() == TOTAL_LABEL), None)
    if total_col is None:
        raise SchemaDrift("OR: party page has no Total column to reconcile against")
    out: dict[str, dict[str, int]] = {}
    for row in rows:
        cells = _assign(row, columns)
        name = cells.get(county_col, "").strip()
        if not name:
            continue
        stated = _int(cells.get(total_col))
        summed = 0
        bucket: dict[str, int] = {}
        for label in party_columns:
            # An empty party cell is Power BI's rendering of zero; the row's own
            # Total is what proves it, and the check below is what enforces it.
            value = _int(cells.get(label)) or 0
            summed += value
            key = _party(label)
            bucket[key] = bucket.get(key, 0) + value
        if stated is not None and stated != summed:
            raise SchemaDrift(
                f"OR: party row {name!r} sums to {summed} against its own Total of "
                f"{stated}; the columns were mis-assigned"
            )
        if name.lower() in STATEWIDE_LABELS:
            out["statewide"] = bucket
        else:
            out[_county(name)[0]] = bucket
    return out


# --------------------------------------------------------------------------
# Assembly
# --------------------------------------------------------------------------
def _rows(
    cycle: int,
    as_of: date,
    totals: dict[str, tuple[int, int] | int],
    dates: list[date],
    series: dict[str, list[int | None]],
    party: dict[str, dict[str, int]],
) -> FetchResult:
    """Build the canonical rows from the three tables every layout produces."""
    if dates and dates[-1] > as_of:
        raise SchemaDrift(
            f"OR: the last day column is {dates[-1].isoformat()}, which is after "
            f"the report's own stamp of {as_of.isoformat()}"
        )

    def bucket(key: str) -> dict[str, int | None]:
        split = party.get(key, {})
        return {
            f"party_{b}": split.get(b) for b in ("dem", "rep", "oth", "npa")
        } if split else {"party_dem": None, "party_rep": None,
                         "party_oth": None, "party_npa": None}

    result = FetchResult()
    counties = [k for k in totals if k != "statewide"]

    for fips in counties:
        stated = totals[fips]
        returned = stated[1] if isinstance(stated, tuple) else stated
        daily = series.get(fips)
        running = 0
        if daily:
            for day, new in zip(dates, daily):
                if new is None:
                    continue
                running += new
                if day == as_of:
                    continue
                result.county_rows.append(CountyDay(
                    cycle=cycle, state="OR", county_fips=fips, day=day,
                    county_name=_NAMES[fips],
                    ballots_total=running, ballots_new=new,
                    mail_returned=running, inperson=None,
                    party_dem=None, party_rep=None, party_oth=None, party_npa=None,
                ))
        last_new = None
        if daily and dates and dates[-1] == as_of:
            last_new = daily[-1]
        result.county_rows.append(CountyDay(
            cycle=cycle, state="OR", county_fips=fips, day=as_of,
            county_name=_NAMES[fips],
            ballots_total=returned, ballots_new=last_new,
            mail_returned=returned, inperson=None,
            **bucket(fips),
        ))

    state_new = series.get("statewide new")
    state_cum = series.get("statewide cum")
    running = 0
    for index, day in enumerate(dates):
        new = state_new[index] if state_new and index < len(state_new) else None
        if new is not None:
            running += new
        cumulative = (
            state_cum[index] if state_cum and index < len(state_cum) else running
        )
        if day == as_of:
            continue
        result.state_rows.append(StateDay(
            cycle=cycle, state="OR", day=day,
            ballots_total=cumulative, ballots_new=new,
            mail_requested=None, mail_returned=cumulative, inperson=None,
            party_dem=None, party_rep=None, party_oth=None, party_npa=None,
        ))
    stated = totals.get("statewide")
    statewide_total = stated[1] if isinstance(stated, tuple) else stated
    # ballots_new ONLY when the last day column is the stamp's own day, exactly
    # as the county rows above already do it. A report generated the morning
    # after the day it covers (2022-11-04 carries columns through 11-03 only)
    # says nothing about how many ballots arrived on the stamp's day, and
    # reusing the last column there filed 11-03's 94,895 under 11-04 as well --
    # the same number published twice, once under a day it did not describe.
    last_state_new = (
        state_new[-1] if state_new and dates and dates[-1] == as_of else None
    )
    result.state_rows.append(StateDay(
        cycle=cycle, state="OR", day=as_of,
        ballots_total=statewide_total,
        ballots_new=last_state_new,
        mail_requested=None, mail_returned=statewide_total, inperson=None,
        **bucket("statewide"),
    ))

    for row in result.county_rows:
        if not row.county_name:
            row.county_name = _NAMES.get(row.county_fips, "")
    return result


def parse(body: bytes, cycle: int) -> FetchResult:
    """Parse one Daily Ballot Returns report, in either layout."""
    plain = _pages(body)
    if not plain:
        raise SchemaDrift("OR: report has no pages")
    if any("Power BI" in page[:80] for page in plain) or any(
        _PBI_ELECTION.search(page) for page in plain
    ):
        return _parse_powerbi(body, plain, cycle)
    return _parse_classic(body, plain, cycle)


def _parse_classic(body: bytes, plain: list[str], cycle: int) -> FetchResult:
    year = classic_election_year(plain)
    if year != int(cycle):
        raise NotYetPublished(
            f"OR: this report is the {year} general, not the {cycle} general"
        )
    as_of, totals = classic_summary(plain)
    dates, series = classic_daily(plain, year)
    party = classic_party(body, plain)
    _check_party(party, totals)
    return _rows(cycle, as_of, totals, dates, series, party)


def _parse_powerbi(body: bytes, plain: list[str], cycle: int) -> FetchResult:
    layout = _pages(body, layout=True)
    election, generated = pbi_dates(layout)
    if election != election_date(cycle):
        # The live report during the primary season parses perfectly and is a
        # completely different election. NotYetPublished, not SchemaDrift: there
        # is nothing wrong with the file, the general simply has not started.
        raise NotYetPublished(
            f"OR: this report is for the {election.isoformat()} election, not "
            f"the {cycle} general"
        )
    totals = pbi_counties(layout)
    dates, series = pbi_daily(layout, election.year)
    party = pbi_party(layout)
    _check_party(party, totals)
    if dates and dates[-1] != generated:
        # Power BI stamps the run time, which is the morning after the last day
        # it has data for; trust the last data column and keep the stamp for the
        # log rather than redating every count.
        log.info("OR: report generated %s, last day column %s",
                 generated.isoformat(), dates[-1].isoformat())
    as_of = dates[-1] if dates else generated
    return _rows(cycle, as_of, totals, dates, series, party)


def _check_party(party: dict[str, dict[str, int]],
                 totals: dict[str, tuple[int, int] | int]) -> None:
    """Every county's party columns must sum to its own reported total."""
    for key, split in party.items():
        stated = totals.get(key)
        if stated is None:
            continue
        returned = stated[1] if isinstance(stated, tuple) else stated
        summed = sum(split.values())
        if returned is not None and summed != returned:
            raise SchemaDrift(
                f"OR: {key} party columns sum to {summed} against a reported "
                f"return of {returned}"
            )


def _as_of_of(one: FetchResult) -> date | None:
    """The day a parsed report is stamped: the last day it publishes."""
    days = [row.day for row in one.state_rows] + [row.day for row in one.county_rows]
    return max(days) if days else None


def _merge(captures: list[tuple[date, str, FetchResult]]) -> FetchResult:
    """Merge archived captures into one series, EARLIEST report per day wins.

    Every capture carries the whole curve up to its own as-of day, so the same
    day is described by every later capture too. The first report to cover a day
    is the one published, which means:

    * the day a report is STAMPED gets that report's summary and its party
      split -- the only place a party split exists at all;
    * the days before it get that same report's day matrix, so a day's county
      distribution, statewide total and party columns are all one file's
      reading of one moment;
    * a later capture never restates an earlier day, which is what keeps the
      cumulative curve monotone. Oregon's own numbers for a past day drift
      UPWARD from report to report as counties backfill late ballots, and a
      summary day sandwiched between two restated matrix days reads as a drop.

    THE BLANK RULE holds by construction: a day is one capture's rows or another
    capture's rows, never a splice, so a day with no report of its own keeps
    `party_* = None` rather than inheriting the neighbouring day's split.

    Two captures of the SAME as-of day -- Oregon regenerates the file during
    Election Day -- resolve to the LATER stamp, which is fl.py's and de.py's
    rule and for the same reason: it is the fresher reading of that day.
    """
    ordered = sorted(captures, key=lambda cap: (cap[0], -int(cap[1])))
    state: dict[date, StateDay] = {}
    county: dict[tuple[date, str], CountyDay] = {}
    for _as_of, _stamp, one in ordered:
        for row in one.state_rows:
            state.setdefault(row.day, row)
        for row in one.county_rows:
            county.setdefault((row.day, row.county_fips), row)
    result = FetchResult()
    result.state_rows = [state[day] for day in sorted(state)]
    result.county_rows = [county[key] for key in sorted(county)]
    return result


def _not_after(one: FetchResult, as_of: date) -> FetchResult:
    """Drop rows dated after `as_of`.

    ONE download gives Oregon every day of the season at once, so a run asked
    for an earlier day would otherwise publish days that had not happened when
    it was asked about -- the same guard nc.py applies to its own
    whole-curve-in-one-file source (`if day > as_of: continue`). Nothing is
    invented either way; this only stops a `--as-of` rerun from answering a
    question it was not asked.
    """
    kept = FetchResult()
    kept.state_rows = [row for row in one.state_rows if row.day <= as_of]
    kept.county_rows = [row for row in one.county_rows if row.day <= as_of]
    kept.demo_rows = [row for row in one.demo_rows if row.day <= as_of]
    return kept


class ORScraper(Adapter):
    """Tier 1 for Oregon: the SoS Daily Ballot Returns PDF."""

    state = "OR"
    name = "or-sos"
    tier = TIER_SCRAPER

    def _download(self, url: str, *, filename: str, use_cache: bool = False,
                  min_interval: float = DEFAULT_MIN_INTERVAL) -> bytes:
        body = get(url, state="OR", filename=filename, use_cache=use_cache,
                   min_bytes=4096, min_interval=min_interval)
        if looks_like_html(body) or not body.startswith(b"%PDF"):
            raise Missing(f"OR: {url} did not return a PDF")
        return body

    def _discover(self) -> list[str]:
        """Report URLs linked from the SoS's own current-election page."""
        try:
            page = get(CURRENT_ELECTION_URL, state="OR",
                       filename="current_election.html", min_bytes=2048)
        except Missing as exc:
            raise NotYetPublished(f"OR: {exc}") from exc
        markup = page.decode("utf-8", errors="replace")
        out: list[str] = []
        for href in _REPORT_LINK.findall(markup):
            href = href.replace("&amp;", "&")
            out.append(href if href.startswith("http") else SOS + href)
        return out

    def fetch(self, cycle: int, as_of: date) -> FetchResult:
        candidates = list(self._discover())
        candidates += [
            SOS + path.format(cycle=cycle, yy=str(cycle)[-2:])
            for path in FALLBACK_PATHS
        ]
        wrong_election: list[str] = []
        for index, url in enumerate(dict.fromkeys(candidates)):
            try:
                body = self._download(url, filename=f"{cycle}_daily_{index}.pdf")
            except Missing:
                continue
            try:
                report = parse(body, cycle)
            except NotYetPublished as exc:
                # A live report for the PRIMARY parses fine and is refused by its
                # own election date. Another candidate URL may still be the
                # general's, so keep looking. SchemaDrift and SourceError are
                # deliberately NOT caught: a file whose columns changed must
                # fall through a tier, not be reported as "no data yet".
                wrong_election.append(str(exc))
                log.info("OR: %s is not this cycle's general (%s)", url, exc)
                continue
            report = _not_after(report, as_of)
            if not report:
                raise NotYetPublished(
                    f"OR: the posted report covers nothing on or before "
                    f"{as_of.isoformat()}"
                )
            return report
        if wrong_election:
            raise NotYetPublished(
                f"OR: no {cycle} general Daily Ballot Returns report yet "
                f"({wrong_election[0]})"
            )
        raise NotYetPublished(
            f"OR: the SoS has not posted a {cycle} Daily Ballot Returns report yet"
        )

    def fetch_history(self, cycle: int) -> FetchResult:
        """The whole archived daily curve for a past cycle, from the Wayback Machine.

        Oregon deletes the report after each election -- both 2022 and 2024 URLs
        are 404 on sos.oregon.gov today -- so the archive is the only copy. One
        download rebuilds the whole curve, but it carries a PARTY split for its
        own as-of day alone, so EVERY capture is swept and the answers are
        merged by the day each report is stamped. See `_merge` for the rule and
        the module docstring for why it is not fl.py's.

        A capture that will not parse is skipped, never guessed at, and the two
        ways that happens are deliberately both caught:

          NotYetPublished  the file is a different election's report. In `fetch`
                           this means "try the next candidate URL"; here it means
                           "try the next capture". Not catching it aborted the
                           whole sweep on the first stale crawl.
          SchemaDrift      the post-canvass FINAL, whose party table does not
                           reconcile against its own totals. Losing that capture
                           must not cost the cycle its other thirteen days.
        """
        entry = ARCHIVED.get(int(cycle))
        if entry is None:
            raise NotYetPublished(f"OR: no archived report recorded for {cycle}")
        url, known = entry
        stamps = sorted(dict.fromkeys(tuple(known) + tuple(_archive_stamps(url, cycle))))
        captures: list[tuple[date, str, FetchResult]] = []
        problems: list[str] = []
        for stamp in stamps:
            try:
                body = self._download(
                    WAYBACK.format(stamp=stamp, url=url),
                    filename=f"{cycle}_archive_{stamp}.pdf", use_cache=True,
                    min_interval=ARCHIVE_MIN_INTERVAL,
                )
            except SourceError as exc:
                # Missing included: one capture the Archive cannot serve is not
                # a reason to stop asking for the others.
                problems.append(f"{stamp}: {exc}")
                log.debug("OR: archived capture %s could not be fetched (%s)", stamp, exc)
                continue
            try:
                one = parse(body, cycle)
            except NotYetPublished as exc:
                problems.append(f"{stamp}: {exc}")
                log.info("OR: archived capture %s is not this cycle's general (%s)",
                         stamp, exc)
                continue
            except SourceError as exc:
                problems.append(f"{stamp}: {exc}")
                log.warning("OR: archived capture %s is unusable (%s)", stamp, exc)
                continue
            as_of = _as_of_of(one)
            if as_of is None:
                continue
            captures.append((as_of, stamp, one))
        if not captures:
            raise NotYetPublished(
                f"OR: no usable archived {cycle} report ({problems[0] if problems else ''})"
            )
        result = _merge(captures)
        log.info("OR: %s archived days from %s of %s captures (party on %s)",
                 len(result.state_rows), len(captures), len(stamps),
                 ", ".join(cap[0].isoformat() for cap in sorted(captures)))
        return result
