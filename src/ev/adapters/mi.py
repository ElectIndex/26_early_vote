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

PAST CYCLES: THE FILE IS OVERWRITTEN, THE PRESS RELEASES ARE NOT
----------------------------------------------------------------
This module used to say a past cycle was unrecoverable because the workbook has
no dated URL. That was true of the one URL it knew and false of Michigan. The
Bureau of Elections attaches a DATED copy of the same workbook to each weekly
early-vote press release, under a media-library path that never changes -- and
the Wayback Machine has them all. `fetch_history` walks that list; ARCHIVE_PATHS
carries the URLs and how each was found.

What comes back is not a daily curve. Michigan published this workbook WEEKLY:
three readings in the 2024 general (Oct 15 / 22 / 29) and four in 2022
(Oct 10 / 17 / 24 / 31). Those land at days-to-election 21/14/7 and 29/22/15/8,
which line up pairwise inside `counterfactual.DTE_MATCH_TOLERANCE`, so the two
cycles are comparable at three matched days even though neither is daily.

⚠️ AND THE HEADER IS DIFFERENT IN EVERY CYCLE. See FIELDS: the same five
measures have been published under three different column layouts, one of which
changed MID-CYCLE in 2022. The columns are therefore found by name, and an
unknown name raises SchemaDrift instead of being positionally assumed.
"""

from __future__ import annotations

import io
import json
import logging
import re
from collections import defaultdict
from datetime import date
from urllib.parse import urlsplit

import openpyxl

from ..calendar import election_date
from ..schema import TIER_SCRAPER, CountyDay, StateDay
from . import _fips, _net
from ._net import Missing, get, looks_like_xlsx
from .base import Adapter, FetchResult, NotYetPublished, SchemaDrift, SourceError

log = logging.getLogger(__name__)

#: ⚠️ DEAD AS OF 2026-09-09, AND THAT IS A LIVE RISK FOR THE 2026 SEASON, not
#: just a stale comment. Both URL_CANDIDATES redirect to
#: `https://www.michigan.gov/sos/404`, which answers with status 404 and a
#: 353,387-byte not-found page -- so `_net.get` raises Missing and `fetch`
#: correctly reports NotYetPublished. (`probe` prints exactly that today.)
#: Michigan purges its media library between cycles: the 2024 workbooks now live
#: only in the Internet Archive (see ARCHIVE_PATHS), and the August-2024 primary
#: file is gone from this path too.
#:
#: So these two URLs are a GUESS about where the 2026 file will appear, and the
#: evidence from 2024 is that it will not appear here -- every 2024 copy was
#: published under a dated `Press-Release-Media-N/` name instead. Michigan says
#: it posts data from 45 days before Election Day (2026-09-19). If `probe` still
#: shows MI pending after that date, the fix is to find the new press-release
#: path, not to assume Michigan is late.
#:
#: Michigan also runs a Power BI dashboard at
#: `/sos/elections/election-results-and-data/voter-participation-dashboard`
#: (michigan.gov/VotingDashboard, launched 2024-10-16, county AND jurisdiction
#: detail, "updated daily" from the QVF). It is an `app.powerbigov.us` iframe, so
#: it archives as an empty frame and is not a route to past cycles -- but it is
#: where a human should look first to confirm what Michigan is publishing now.
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

HOST = "https://www.michigan.gov"

# ==========================================================================
# THE PAST CYCLES: PRESS-RELEASE ATTACHMENTS, NOT A REPORT PAGE
#
# `URL` above is a file Michigan overwrites in place, which is why this module
# used to say a past cycle only survives if this repo happened to be running.
# That was true of that URL and false of Michigan. The Bureau of Elections
# publishes the SAME workbook, DATED, as an attachment on its weekly early-vote
# press release, and those media-library URLs never change:
#
#   2024-10-15  .../Press-Release-Media-3/10-2024-General-Election-Data-by-
#               Jurisdiction.xlsx           linked from news/2024/10/15/
#               "more-than-670k-michigan-voters-have-cast-absentee-ballots..."
#   2024-10-22  .../Press-Release-Media-4/10_22_2024-General-Election-Data-by-
#               Jursidictionv3.xlsx         linked from news/2024/10/22/
#               "michigan-sees-record-breaking-early-voting-turnout"
#   2024-10-29  .../Press-Release-Media-4/102924-General-Election-Data-by-
#               Jursidiction.xlsx           linked from news/2024/10/29/
#               "nearly-2-million-michigan-voters-have-already-cast-their-ballot"
#
# ⚠️ "Jursidiction" IS NOT A TYPO IN THIS FILE. Michigan misspelled it in both
# 2024-10-22 and 2024-10-29 filenames and spelled it correctly on 2024-10-15.
# A sweep that greps only for the correct spelling finds one third of the cycle.
#
# The workbooks reconcile to the press releases they were attached to, to the
# ballot: 2024-10-15 says 2,133,272 requested / 672,585 returned and so does the
# release; 2024-10-29 says 2,360,407 / 1,602,831 and so does the release.
#
# VERIFIED 2026-09-09 from this network: every one of these paths now answers
# HTTP 302 -> https://www.michigan.gov/sos/404 (353,387 bytes of not-found HTML)
# on the LIVE host -- Michigan purges its media library between cycles, and so
# does `URL` itself, which 404s today. All of them are in the Wayback Machine,
# which is therefore the only source and the reason the query below exists.
# --------------------------------------------------------------------------
ARCHIVE_PATHS: dict[int, tuple[str, ...]] = {
    2022: (
        "/sos/-/media/Project/Websites/sos/35lawens/"
        "pr-ballot-stats-by-jurisdiction---10-10-2022_v2.xlsx",
        "/sos/-/media/Project/Websites/sos/press-release-images/"
        "pr-nov-2022-ballot-stats-by-jurisdiction---10-17-2022.xlsx",
        "/sos/-/media/Project/Websites/sos/press-release-images/"
        "pr-nov-2022-av--ballot-stats-2022-10-24.xlsx",
        "/sos/-/media/Project/Websites/sos/press-release-images/"
        "10-31-2022-pr-nov-2022-av--ballot-stats.xlsx",
    ),
    2024: (
        "/sos/-/media/Project/Websites/sos/Press-Release-Media-3/"
        "10-2024-General-Election-Data-by-Jurisdiction.xlsx",
        "/sos/-/media/Project/Websites/sos/Press-Release-Media-4/"
        "10_22_2024-General-Election-Data-by-Jursidictionv3.xlsx",
        "/sos/-/media/Project/Websites/sos/Press-Release-Media-4/"
        "102924-General-Election-Data-by-Jursidiction.xlsx",
    ),
}

#: ⚠️ THE QUERY STRING IS NOT PART OF THE IDENTITY, which is why ARCHIVE_PATHS
#: holds bare paths and the lookup below is a PREFIX match. Sitecore appends
#: `?hash=...&rev=...` and regenerates the hash whenever the item is touched --
#: the 2024-10-15 workbook is linked from its press release under
#: `hash=F899A7A6...` and archived under `hash=a5f24527...`, the same file both
#: times. An exact-URL CDX match would find nothing.
CDX_URL = "http://web.archive.org/cdx/search/cdx"

#: `id_` asks for the ORIGINAL bytes. For an xlsx that is not optional: the
#: rewriting proxy is for HTML and a rewritten zip is not a zip.
WAYBACK_SNAPSHOT = "https://web.archive.org/web/{stamp}id_/{url}"

#: web.archive.org is slower and less tolerant than a state host, and a backfill
#: walks a dozen of its URLs. See DEFAULT_MIN_INTERVAL in _net.
ARCHIVE_MIN_INTERVAL = 1.0
ARCHIVE_TIMEOUT = 180

#: THE HEADER IS A VOCABULARY, NOT A FIXED TUPLE -- and it has to be, because
#: Michigan has published this same five-measure report under three different
#: headers in three cycles and TWICE INSIDE ONE CYCLE:
#:
#:   2024 Oct 15/22/29  COUNTY | JURISDICTION | REQUESTS | ISSUED | RECEIVED
#:   2022 Oct 10/17     <blank> | DLCOUNTYCODE | JURISDCODE | COUNTY |
#:                      JURISDICTION | APPS RETURNED | BALLOTS SENT |
#:                      BALLOTS RECEIVED
#:   2022 Oct 24/31     <blank> | COUNTY | JURISDICTION | APPS RECEIVED |
#:                      BALLOTS SENT | BALLOTS RECEIVED | ...blanks... |
#:                      BALLOTS SPOILED | BALLOTS REJECTED
#:
#: So the five columns are found BY NAME rather than by position. `normalize.py`
#: is the model: a name in `FIELDS` is that measure, a name in `IGNORED` is a
#: column this report carries and we do not publish, and anything else raises
#: SchemaDrift rather than being guessed at (RULE 3). Adding a spelling here is
#: a deliberate, reviewable act; silently accepting one is what publishes
#: confident wrong numbers.
FIELDS: dict[str, str] = {
    "county": "county",
    "jurisdiction": "jurisdiction",
    # Applications the clerk has RECEIVED from voters. Michigan has called this
    # column three things and meant the same thing every time.
    "requests": "requests",
    "apps returned": "requests",
    "apps received": "requests",
    # Ballots the clerk has SENT OUT.
    "issued": "issued",
    "ballots sent": "issued",
    # Voted ballots back in the clerk's hands. This is the early vote.
    "received": "received",
    "ballots received": "received",
}

#: Columns these files carry that we deliberately do not publish. An unnamed
#: column (the row-number index the 2022 exports lead with, and the blank
#: padding the 2022-10-24 export trails) normalises to "" and lands here too.
IGNORED: frozenset[str] = frozenset({
    "", "dlcountycode", "jurisdcode", "ballots spoiled", "ballots rejected",
})

#: The five names every layout must supply exactly once.
REQUIRED: tuple[str, ...] = ("county", "jurisdiction", "requests", "issued", "received")

#: Michigan's privacy floor. The cell is not blank and it is not a number: it is
#: a statement that the true value is somewhere in 0..9.
SUPPRESSED = "less than 10"

#: The statewide line. It is NOT always in the COUNTY column: the 2024-10-22
#: workbook leaves COUNTY blank and writes "TOTALS" in JURISDICTION, and the
#: 2024-10-29 one writes "GRAND TOTAL" there. Both spellings and both positions
#: are recognised; see `parse`.
TOTALS_LABELS = {"totals", "total", "grand total", "statewide", "state totals"}

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


def _cell(value) -> str:
    """A header cell, whitespace-collapsed and lowercased. None becomes ""."""
    return " ".join(str(value or "").strip().lower().split())


def layout(header: tuple) -> dict[str, int]:
    """Which column holds each of the five measures, by NAME.

    Raises SchemaDrift for a header carrying a name this module does not know,
    or missing one of the five, or carrying one of them twice. That is the drift
    signal: a column Michigan renamed is a column whose meaning we cannot assume.
    """
    found: dict[str, int] = {}
    for index, raw in enumerate(header):
        name = _cell(raw)
        if name in IGNORED:
            continue
        field = FIELDS.get(name)
        if field is None:
            raise SchemaDrift(
                f"MI: unrecognised column {name!r} in header {[_cell(c) for c in header]}"
            )
        if field in found:
            raise SchemaDrift(f"MI: column {field!r} appears twice in {header!r}")
        found[field] = index
    missing = [f for f in REQUIRED if f not in found]
    if missing:
        raise SchemaDrift(
            f"MI: header {[_cell(c) for c in header]} has no {missing} column"
        )
    return found


def _at(row: tuple, index: int):
    return row[index] if index < len(row) else None


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
        columns = layout(next(rows))
    except StopIteration as exc:
        raise SchemaDrift("MI: jurisdiction sheet is empty") from exc

    state_rows: list[StateDay] = []
    by_county: dict[str, dict[str, list]] = defaultdict(
        lambda: {"requests": [], "issued": [], "received": []}
    )
    unknown: list[str] = []
    #: Set once the sheet's trailing statewide line has been passed. A county row
    #: after it means the "trailer" was not a trailer at all -- see below.
    past_the_trailer = False

    def statewide(issued, received) -> None:
        if state_rows:
            raise SchemaDrift(f"MI: two statewide rows on the {wanted.title!r} sheet")
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

    for row in rows:
        if not row:
            continue
        county = " ".join(str(_at(row, columns["county"]) or "").split())
        jurisdiction = " ".join(str(_at(row, columns["jurisdiction"]) or "").split())
        requests, issued, received = (
            _count(_at(row, columns[field]))
            for field in ("requests", "issued", "received")
        )

        # ------------------------------------------------------------------
        # THE STATEWIDE LINE MOVES. In the 2024-10-15 workbook it is a row whose
        # COUNTY reads "TOTALS"; a week later COUNTY is blank and JURISDICTION
        # reads "TOTALS"; a week after that, "GRAND TOTAL". The 2022 exports
        # leave BOTH blank and just put the numbers on a final row.
        #
        # The old loop skipped every row with a blank COUNTY, which silently
        # threw away two of the three 2024 statewide readings -- and the numbers
        # it threw away are Michigan's own, computed from the unsuppressed data.
        # ------------------------------------------------------------------
        if not county:
            if not jurisdiction:
                if requests is None and issued is None and received is None:
                    continue                     # trailing filler row
                # An UNLABELLED trailing line of numbers. It is almost certainly
                # the statewide total -- it is the last row and it is far larger
                # than any county -- but "almost certainly" is exactly what
                # RULE 3 forbids publishing, and there is no label to normalise.
                # It is dropped rather than guessed: the state row for such a
                # cycle then comes from a weaker tier, which is a gap, not a
                # wrong number.
                log.debug("MI: dropping unlabelled trailing row %r on %s", row[:8], day)
                past_the_trailer = True
                continue
            if jurisdiction.lower() in TOTALS_LABELS:
                statewide(issued, received)
                past_the_trailer = True
                continue
            raise SchemaDrift(
                f"MI: row with no county and unrecognised label {jurisdiction!r}"
            )

        if past_the_trailer:
            # A county row AFTER the trailing total means the row we treated as
            # the sheet's trailer was something else -- a merged county cell, a
            # sub-total, a section break -- and every row we attributed since is
            # suspect. Fail rather than publish it.
            raise SchemaDrift(
                f"MI: county row {county!r} appears after the sheet's trailing total"
            )

        if county.lower() in TOTALS_LABELS:
            statewide(issued, received)
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

    # ------------------------------------------------------------------
    def _captures(self, path: str, cycle: int) -> list[str]:
        """Every archived 200 for `path` as a snapshot URL, newest first.

        A PREFIX match, because the query string carries a Sitecore hash that is
        regenerated whenever the item is touched -- see CDX_URL's note. The
        archived `original` is used verbatim so the snapshot URL asks the Archive
        for exactly the bytes it holds.

        ⚠️ ALL of them, and NOT `collapse=digest`. A capture the CDX index calls
        200 can still 404 on playback: the 2022-10-24 workbook has two captures
        of identical bytes and the older one (2022-11-12) is unreadable while the
        newer (2025-09-04) serves fine. Collapsing on digest hid the good one
        behind the broken one and silently cost the cycle a whole day -- which is
        the day that pairs with 2024-10-29. The caller walks this list until one
        downloads.
        """
        query = {
            "url": HOST.split("//", 1)[1] + path,
            "matchType": "prefix",
            "output": "json",
            "fl": "timestamp,original,statuscode",
            "filter": "statuscode:200",
            "limit": "60",
        }
        stem = path.split("?", 1)[0].lower()
        try:
            body = _net.get(
                CDX_URL, state=self.state,
                filename=f"cdx-{cycle}-{path.rsplit('/', 1)[-1]}.json",
                params=query, min_bytes=2,
                min_interval=ARCHIVE_MIN_INTERVAL,
            )
        except Missing:
            # The CDX API answers "nothing archived" with an empty body, which
            # _net.get reports as Missing. That is absence, not a fault.
            return []
        try:
            rows = json.loads(body.decode("utf-8", errors="replace"))
        except ValueError as exc:
            raise SourceError(f"MI: the Wayback CDX index was not JSON: {exc}") from exc
        found: list[tuple[str, str]] = []
        for stamp, original, _status in rows[1:]:
            # A prefix match can also catch `<name>.xlsx.bak` and friends; only
            # the file itself, under whatever query string, is wanted.
            if urlsplit(original).path.lower() != stem:
                continue
            found.append((stamp, original))
        return [WAYBACK_SNAPSHOT.format(stamp=stamp, url=url)
                for stamp, url in sorted(found, reverse=True)]

    def fetch_history(self, cycle: int) -> FetchResult:
        """The archived dated workbooks for a past cycle, from the Wayback Machine.

        Michigan overwrites `URL` in place, which is why this used to refuse --
        but it also attaches a DATED copy of the same workbook to each weekly
        early-vote press release, and those survive. See ARCHIVE_PATHS.

        Each workbook dates itself in its sheet title, so nothing here is stamped
        with a date it did not carry: `parse` reads "2024 (Oct 29)" off the sheet
        and that is the row's day. A cycle therefore comes back as the three or
        four readings Michigan actually published, not as a daily curve.

        ⚠️ GUARD PARITY, same shape as nd.py's and wa.py's. ARCHIVE_PATHS is a
        HAND-PINNED list. For a finished cycle that is a complete list and it can
        never grow; for a RUNNING cycle it is whatever a human happened to have
        found on the day they wrote it down, and serving that as "the archive"
        would publish a three-day stub beside -- and in `publish.py`'s merge,
        competing with -- the live daily `fetch` that is the actual tracker for
        that cycle. So a cycle whose election has not happened is refused by
        name, even if someone adds paths for it.
        """
        paths = ARCHIVE_PATHS.get(int(cycle))
        if not paths:
            raise NotYetPublished(
                f"MI: no archived {cycle} press-release workbooks are known -- "
                f"the media-library copies this adapter walks exist for "
                f"{sorted(ARCHIVE_PATHS)} only"
            )
        settled = election_date(cycle)
        if settled > date.today():
            raise NotYetPublished(
                f"MI: the {cycle} general has not happened yet -- the pinned "
                f"press-release list can only be complete for a finished cycle, "
                f"and a partial one published as history would compete with the "
                f"daily `fetch` that actually tracks {cycle}"
            )

        by_day: dict[date, FetchResult] = {}
        seen = 0
        for path in paths:
            captures = self._captures(path, cycle)
            if not captures:
                log.warning("MI: nothing archived under %s", path)
                continue
            one = None
            for snapshot in captures:
                stamp = snapshot.split("/web/", 1)[1].split("id_/", 1)[0]
                try:
                    body = _net.get(
                        snapshot, state=self.state,
                        # The stamp has to be in the cache filename: two captures
                        # of one path would otherwise be one file on disk.
                        filename=f"archive-{path.rsplit('/', 1)[-1]}-{stamp}.xlsx",
                        use_cache=True, min_bytes=4096,
                        timeout=ARCHIVE_TIMEOUT, min_interval=ARCHIVE_MIN_INTERVAL,
                    )
                except SourceError as exc:
                    log.debug("MI: capture %s of %s unusable (%s)", stamp, path, exc)
                    continue
                seen += 1
                try:
                    one = parse(body, cycle)
                except NotYetPublished as exc:
                    # A workbook that carries no sheet for this cycle is not
                    # about the election we asked for. Try the next capture.
                    log.debug("MI: capture %s of %s skipped (%s)", stamp, path, exc)
                    one = None
                    continue
                break
            if one is None:
                log.warning("MI: no readable capture of %s (%s tried)",
                            path, len(captures))
                continue
            day = (one.state_rows or one.county_rows)[0].day
            by_day[day] = one

        if not by_day:
            raise NotYetPublished(
                f"MI: nothing usable archived for the {cycle} general "
                f"({seen} workbooks read)"
            )
        result = FetchResult()
        for day in sorted(by_day):
            result.extend(by_day[day])
        log.info("MI: %s archived days from %s workbooks", len(by_day), seen)
        return result
