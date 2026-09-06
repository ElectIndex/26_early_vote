"""Delaware — the Department of Elections' "Voter Counts by Voting Method" report.

Delaware publishes one PDF per election at a fixed, election-stamped URL and
**overwrites it in place while voting is happening**. The report is three
counties wide and three rows deep:

```
                              New Castle              Kent            Sussex
Voting Method                   County               County           County            Total
Absentee                       19,469                5,542            12,645          37,656
Early Voting                   82,957               36,363            90,196         209,516
Polling Place                 117,422               30,320            31,263         179,005
              Total           219,848               72,225          134,104          426,177
```

That the file is genuinely refreshed during the early-vote window is not an
assumption. The 2024 general's PDF is archived six times between 2024-10-28 and
2024-11-30, each with a different digest, and the numbers climb:

```
Mon Oct 28 2024 08:54  absentee 29,926   early  56,981   (no polling-place row)
Tue Oct 29 2024 08:45  absentee 30,891   early  81,315
Fri Nov  1 2024 08:44  absentee 34,207   early 153,131
Mon Nov  4 2024 08:34  absentee 36,403   early 209,517
Tue Nov  5 2024 09:11  absentee 37,416   early 209,517   polling place  54,722
Tue Nov  5 2024 15:41  absentee 37,656   early 209,516   polling place 179,005
```

Five things shape the adapter.

* **The report carries its own as-of stamp**, a weekday-dated line and a clock
  time, and that is the date the rows are published under -- never the date of
  the run. Delaware refreshes on business mornings, so a Saturday run legitimately
  re-reads Friday's report; publishing it under Saturday would invent a flat day.

* **The report names its own election** ("2024 General Election"), and the parser
  demands the cycle's own general in that line. This is a stronger guard than any
  date window: Delaware's PRIMARY report lives beside it under an almost identical
  filename (`PR2026_PrimaryElectionVoterCountsByVotingMethod.pdf`, verified 200
  today and showing the September primary's absentee and early votes), and
  publishing 16,559 primary ballots as general-election early voting would be a
  confident, wrong number.

* **`ballots_total` is absentee + early voting, NOT Delaware's own `Total`.**
  Their Total is every ballot cast including the polling place, which after
  Election Day is two-thirds of it. This is the one place where the repo's usual
  "publish the source's own total" rule is wrong: the source's total answers a
  different question. Both directions of Delaware's arithmetic are still checked
  (rows sum across counties, methods sum to Total) and a mismatch is SchemaDrift,
  so we are never quietly disagreeing with the Department.

* **The polling-place row is absent until Election Day**, and absent means
  absent. Delaware simply does not print the row, so nothing is published for it
  -- we do not synthesise a zero, and we do not fold it into anything.

* **No party breakdown.** Delaware registers voters by party, and its separate
  registration totals carry it, but THIS report does not, so every `party_*`
  field is None. See THE BLANK RULE in schema.py.
"""

from __future__ import annotations

import io
import json
import logging
import re
from datetime import date, datetime

import pypdf

from ..schema import TIER_SCRAPER, CountyDay, StateDay
from . import _fips
from ._net import Missing, get, looks_like_html
from .base import Adapter, FetchResult, NotYetPublished, SchemaDrift, SourceError

log = logging.getLogger(__name__)

HOST = "https://elections.delaware.gov"

#: VERIFIED 2026-09-06, live, from this network:
#:   .../pdfs/GE2024_GeneralElectionVoterCountsByVotingMethod.pdf   200, 97,330 B, application/pdf
#:   .../pdfs/PR2024_VoterCountsByVotingMethod.pdf                  200, 96,161 B  (primary -- never read)
#:   .../pdfs/PR2026_PrimaryElectionVoterCountsByVotingMethod.pdf   200, 198,768 B (primary -- never read)
#:   .../pdfs/GE2026_GeneralElectionVoterCountsByVotingMethod.pdf   404  <- the NotYetPublished path today
#:   .../pdfs/GE2022_GeneralElectionVoterCountsByVotingMethod.pdf   404
#:   .../pdfs/GE2022_VoterCountsByVotingMethod.pdf                  404
#:   .../pdfs/PR2022_VoterCountsByVotingMethod.pdf                  404
#: The practice starts with the 2024 cycle; there is no 2022 report under any
#: name we could find, which is why fetch_history refuses 2022 outright.
REPORT_URL = HOST + "/voter/registrationtotals/reports/pdfs/GE{cycle}_GeneralElectionVoterCountsByVotingMethod.pdf"

#: VERIFIED 2026-09-06: 200, 440,566 B. Delaware links the CURRENT election's
#: report from its home page ("View Report"), which today points at the
#: September primary's file. The index is scraped only for a link whose filename
#: starts with `GE<cycle>`, so a primary link can never be picked up by mistake.
INDEX_URL = HOST + "/index.html"

FIRST_CYCLE = 2024

#: The label Delaware prints for each method, mapped to the field it feeds.
#: An unrecognised label in the table body is SchemaDrift -- if Delaware adds a
#: "Provisional" or "Drop Box" row we must be told, not silently drop it and
#: then fail the arithmetic check for a reason nobody can read.
METHOD_ROWS = {
    "absentee": "mail_returned",
    "early voting": "inperson",
    "polling place": "electionday",
}
TOTAL_ROW = "total"

#: Delaware's three counties, in the column order the report prints them. Order
#: is read from the file, not assumed; this is only the set we accept.
COUNTY_COLUMNS = 3

_DATE_LINE = re.compile(
    r"^(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),\s+"
    r"([A-Z][a-z]+\s+\d{1,2},\s+\d{4})$"
)
_DATA_ROW = re.compile(r"^([A-Za-z][A-Za-z ]*?)\s+((?:[\d,]+\s+){%d}[\d,]+)$" % COUNTY_COLUMNS)
#: A line carrying as many bare numbers as the table has columns is a table row
#: whatever else it looks like. Used only to tell "a row we could not read" --
#: which is drift -- from the report's prose, which is not.
_LOOKS_LIKE_A_ROW = re.compile(r"(?:(?<!\S)[\d,]+(?!\S)\s+){%d}(?<!\S)[\d,]+(?!\S)" % COUNTY_COLUMNS)


def _collapse(line: str) -> str:
    return " ".join(line.split())


def _int(token: str) -> int:
    return int(token.replace(",", ""))


class Report:
    """One parsed Delaware report: an as-of date and a county x method table."""

    __slots__ = ("as_of", "counties", "rows")

    def __init__(self, as_of: date, counties: list[tuple[str, str]],
                 rows: dict[str, list[int]]) -> None:
        #: The date printed at the top of the report.
        self.as_of = as_of
        #: [(5-digit FIPS, census name)], in the report's own column order.
        self.counties = counties
        #: method key (and "total") -> one count per county. Delaware's own
        #: right-hand Total column is checked in `_check_arithmetic` and then
        #: dropped, because the statewide row is summed from the counties here
        #: exactly as it is everywhere else in this repo.
        self.rows = rows

    def value(self, method: str, column: int) -> int | None:
        row = self.rows.get(method)
        return None if row is None else row[column]

    def statewide(self, method: str) -> int | None:
        row = self.rows.get(method)
        return None if row is None else sum(row)


def _read_text(body: bytes) -> str:
    if looks_like_html(body):
        raise SourceError("DE: the voting-method report came back as HTML, not a PDF")
    if body[:4] != b"%PDF":
        raise SourceError(f"DE: the voting-method report is not a PDF (starts {body[:8]!r})")
    try:
        reader = pypdf.PdfReader(io.BytesIO(body))
        # Layout mode is what makes this file a table rather than a word soup:
        # plain extraction runs the Total row's numbers into the word "Total"
        # and, in the 2026 vintage, hoists every number below its own label.
        return "\n".join(page.extract_text(extraction_mode="layout") for page in reader.pages)
    except Exception as exc:  # noqa: BLE001 -- pypdf raises a zoo of types
        raise SourceError(f"DE: could not read the voting-method PDF: {exc}") from exc


def parse(body: bytes, cycle: int) -> Report:
    """Parse one report. Raises NotYetPublished if it is a DIFFERENT election."""
    lines = [line.strip() for line in _read_text(body).splitlines() if line.strip()]
    if not lines:
        raise SchemaDrift("DE: the voting-method PDF has no extractable text")

    wanted = f"{cycle} General Election"
    if not any(_collapse(line) == wanted for line in lines):
        titles = [_collapse(l) for l in lines if re.search(r"\b(General|Primary|Special) Election\b", l)]
        raise NotYetPublished(
            f"DE: the posted report is not the {wanted} "
            f"(it says {titles[:2] or ['nothing recognisable']})"
        )

    as_of = _report_date(lines)
    counties, header_at = _county_columns(lines)
    rows, stated = _table(lines[header_at + 1:])
    _check_arithmetic(rows, stated)
    return Report(as_of, counties, rows)


def _report_date(lines: list[str]) -> date:
    for line in lines[:6]:
        hit = _DATE_LINE.match(_collapse(line))
        if hit:
            try:
                return datetime.strptime(" ".join(hit.group(1).split()), "%B %d, %Y").date()
            except ValueError as exc:
                raise SchemaDrift(f"DE: unparseable report date {hit.group(1)!r}") from exc
    raise SchemaDrift(f"DE: no report date in the first lines {lines[:4]!r}")


def _county_columns(lines: list[str]) -> tuple[list[tuple[str, str]], int]:
    """The county names above the table, resolved to FIPS, plus the header index.

    Delaware splits "New Castle County" across two lines -- the names on one and
    a row of "County" words on the next, beside the literal "Voting Method". The
    names are therefore taken from the line ABOVE the header and every one of
    them must resolve through `_fips`; a name we cannot place is SchemaDrift, not
    a guess, because the columns are positional and mis-ordering them would swap
    two counties' turnout.
    """
    for index, line in enumerate(lines):
        if not _collapse(line).lower().startswith("voting method"):
            continue
        if index == 0:
            break
        names = [part.strip() for part in re.split(r"\s{2,}", lines[index - 1].strip()) if part.strip()]
        if len(names) != COUNTY_COLUMNS:
            raise SchemaDrift(
                f"DE: expected {COUNTY_COLUMNS} county columns, found {names!r}"
            )
        resolved: list[tuple[str, str]] = []
        for name in names:
            hit = _fips.lookup("DE", name)
            if hit is None:
                raise SchemaDrift(f"DE: unrecognised county column {name!r}")
            resolved.append(hit)
        return resolved, index
    raise SchemaDrift("DE: no 'Voting Method' header row in the report")


def _table(lines: list[str]) -> tuple[dict[str, list[int]], dict[str, int]]:
    """(method key -> per-county counts, method key -> Delaware's own row total)."""
    rows: dict[str, list[int]] = {}
    stated: dict[str, int] = {}
    for line in lines:
        text = _collapse(line)
        hit = _DATA_ROW.match(text)
        if hit is None:
            if _LOOKS_LIKE_A_ROW.search(text):
                # As many numbers as the table is wide, but not a shape we can
                # read. Silently skipping it would drop a method row.
                raise SchemaDrift(f"DE: unreadable table row {text!r}")
            continue
        label = hit.group(1).strip().lower()
        key = METHOD_ROWS.get(label) or (TOTAL_ROW if label == TOTAL_ROW else None)
        if key is None:
            raise SchemaDrift(f"DE: unrecognised voting-method row {hit.group(1)!r}")
        numbers = [_int(token) for token in hit.group(2).split()]
        rows[key] = numbers[:COUNTY_COLUMNS]
        stated[key] = numbers[COUNTY_COLUMNS]
    missing = [k for k in ("mail_returned", "inperson", TOTAL_ROW) if k not in rows]
    if missing:
        raise SchemaDrift(f"DE: the report is missing rows {missing}")
    return rows, stated


def _check_arithmetic(rows: dict[str, list[int]], stated: dict[str, int]) -> None:
    """Both directions of Delaware's own table must add up, or we do not publish.

    Verified exact on every archived 2024 capture and on the 2026 primary file.
    A mismatch means either a column we mis-sliced or a method row Delaware has
    added and we are dropping -- both of which publish a confidently wrong
    number, so both are drift.
    """
    methods = [key for key in rows if key != TOTAL_ROW]
    for key in methods + [TOTAL_ROW]:
        summed = sum(rows[key])
        if stated[key] != summed:
            raise SchemaDrift(
                f"DE: the {key!r} row sums to {summed} across counties but the "
                f"report's Total column says {stated[key]}"
            )
    for column in range(COUNTY_COLUMNS):
        summed = sum(rows[key][column] for key in methods)
        if summed != rows[TOTAL_ROW][column]:
            raise SchemaDrift(
                f"DE: county column {column} sums to {summed} across methods but "
                f"the report's Total row says {rows[TOTAL_ROW][column]}"
            )


def to_result(report: Report, cycle: int) -> FetchResult:
    """One statewide row and three county rows for the report's own as-of date."""
    result = FetchResult()
    mail = report.statewide("mail_returned")
    inperson = report.statewide("inperson")
    result.state_rows.append(StateDay(
        cycle=cycle, state="DE", day=report.as_of,
        # NOT Delaware's own Total, which includes the polling place. See the
        # module docstring.
        ballots_total=(mail or 0) + (inperson or 0),
        mail_returned=mail,
        inperson=inperson,
        # Delaware registers by party; this report does not break it out.
    ))
    for column, (fips, name) in enumerate(report.counties):
        county_mail = report.value("mail_returned", column)
        county_inperson = report.value("inperson", column)
        result.county_rows.append(CountyDay(
            cycle=cycle, state="DE", county_fips=fips, county_name=name,
            day=report.as_of,
            ballots_total=(county_mail or 0) + (county_inperson or 0),
            mail_returned=county_mail,
            inperson=county_inperson,
        ))
    return result


# --------------------------------------------------------------------------
# The archived series. Delaware keeps no dated copies of its own -- the URL is
# overwritten -- so a past cycle's daily curve exists only in the Internet
# Archive. This runs in `backfill`, never in the daily job.
# --------------------------------------------------------------------------
CDX_URL = "http://web.archive.org/cdx/search/cdx"

#: `id_` asks the Wayback Machine for the ORIGINAL bytes rather than a rewritten
#: page, which for a PDF is the difference between a file and an HTML wrapper.
WAYBACK_SNAPSHOT = "https://web.archive.org/web/{stamp}id_/{url}"

#: VERIFIED 2026-09-06: the CDX API lists 14 captures of the 2024 general's
#: report, of which 6 have distinct digests -- 2024-10-28, 10-30, 11-01, 11-04,
#: 11-05 (twice) -- and every one of them fetches back as a real PDF.
MAX_ARCHIVE_PROBES = 40


def _archive_stamps(url: str) -> list[str]:
    """Wayback timestamps of every distinct version of `url`, oldest first."""
    query = {
        "url": url, "output": "json", "fl": "timestamp,statuscode,digest",
        "filter": "statuscode:200", "collapse": "digest", "limit": str(MAX_ARCHIVE_PROBES),
    }
    try:
        body = get(CDX_URL, state="DE", filename="cdx-ge-report.json",
                   params=query, min_bytes=2)
    except Missing:
        # The CDX API answers "nothing archived" with an empty body, which
        # _net.get reports as Missing. That is absence, not a fault.
        return []
    try:
        rows = json.loads(body.decode("utf-8", errors="replace"))
    except ValueError as exc:
        raise SourceError(f"DE: the Wayback CDX index was not JSON: {exc}") from exc
    return [row[0] for row in rows[1:]]


class DEScraper(Adapter):
    """Tier 1 for Delaware: the Department of Elections' voting-method report.

    Statewide and three county rows, absentee vs early voting, published under
    the report's own as-of date. No party breakdown -- this report does not
    carry one -- so every party field is blank rather than zero.
    """

    state = "DE"
    name = "de-doe"
    tier = TIER_SCRAPER

    def _index_url(self, cycle: int) -> str | None:
        """A `GE<cycle>...VotingMethod.pdf` link on Delaware's home page, if any."""
        try:
            page = get(INDEX_URL, state="DE", filename="index.html")
        except Missing:
            return None
        except SourceError as exc:
            log.debug("DE: could not read %s (%s)", INDEX_URL, exc)
            return None
        pattern = re.compile(
            rf'href="([^"]*GE{cycle}[^"]*VotingMethod\.pdf)"', re.IGNORECASE
        )
        hit = pattern.search(page.decode("utf-8", errors="replace"))
        if hit is None:
            return None
        href = hit.group(1)
        return href if href.startswith("http") else HOST + "/" + href.lstrip("/")

    def _candidates(self, cycle: int) -> list[str]:
        literal = REPORT_URL.format(cycle=cycle)
        found = self._index_url(cycle)
        if found and found != literal:
            log.info("DE: home page links %s", found)
            return [found, literal]
        return [literal]

    def _download(self, url: str, *, filename: str | None = None,
                  use_cache: bool = False) -> bytes:
        return get(url, state="DE", filename=filename or url.rsplit("/", 1)[-1],
                   use_cache=use_cache, min_bytes=1024)

    def fetch(self, cycle: int, as_of: date) -> FetchResult:
        problems: list[str] = []
        for url in self._candidates(cycle):
            try:
                body = self._download(url)
            except Missing as exc:
                problems.append(str(exc))
                continue
            report = parse(body, cycle)
            if report.as_of > as_of:
                # Only reachable on a backfill asking for a day before the
                # report we can see. Absence, not an error.
                raise NotYetPublished(
                    f"DE: the posted report is dated {report.as_of.isoformat()}, "
                    f"after {as_of.isoformat()}"
                )
            log.info("DE: report as of %s (%s)", report.as_of.isoformat(), url)
            return to_result(report, cycle)
        raise NotYetPublished(
            f"DE: no {cycle} general voting-method report posted yet ({'; '.join(problems)})"
        )

    def fetch_history(self, cycle: int) -> FetchResult:
        """The whole archived daily curve for a past cycle, from the Wayback Machine.

        Delaware overwrites one URL, so its own site holds only the final
        position. Every distinct archived capture is fetched and parsed, and the
        LAST capture of any given report date wins -- 2024-11-05 was archived
        twice, at 09:11 with 54,722 polling-place ballots and at 15:41 with
        179,005, and the later reading is the one that is true of that day.
        """
        if cycle < FIRST_CYCLE:
            raise NotYetPublished(
                f"DE: no voting-method report exists for {cycle}; the practice "
                f"starts with {FIRST_CYCLE}"
            )
        url = REPORT_URL.format(cycle=cycle)
        by_day: dict[date, Report] = {}
        for stamp in _archive_stamps(url):
            snapshot = WAYBACK_SNAPSHOT.format(stamp=stamp, url=url)
            try:
                # The stamp has to be in the cache filename: every capture is
                # the SAME URL, so a bare basename would make them one file.
                body = self._download(snapshot, filename=f"GE{cycle}-{stamp}.pdf",
                                      use_cache=True)
            except SourceError as exc:
                log.debug("DE: archived capture %s unusable (%s)", stamp, exc)
                continue
            try:
                report = parse(body, cycle)
            except NotYetPublished:
                continue
            by_day[report.as_of] = report
        if not by_day:
            raise NotYetPublished(f"DE: nothing archived for the {cycle} general")
        result = FetchResult()
        for day in sorted(by_day):
            result.extend(to_result(by_day[day], cycle))
        return result
