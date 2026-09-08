"""Iowa — the Secretary of State's "Absentee Ballot Statistics" report (PDF).

Iowa publishes absentee requests and returns as a single PDF, refreshed daily
through the absentee period. The important and slightly surprising thing is that
it is not a snapshot: each refresh PRE-PENDS a new report to the same file, so
the 2024 general's `AbsenteeCounty2024.pdf` is 937 pages holding all sixteen
daily reports and the 2022 general's is 1,289 pages holding thirty, newest
first. Like North Carolina, one download therefore reconstructs the entire daily
curve, and a day we fail to run is not a day of Iowa's series that is lost
forever.

Each report is one nested outline, three columns wide:

    County/ Party / Receipt Method     Requested  Issued  Received
    Adair                                   1586    1586     1568
      Democrat                               370     370      368
        Counter / In-Office                  244     244      244
        Mail                                 122     122      122
        Ballot not yet returned by voter       2       2
      No Party                               346     346      342
      ...
    Grand Total                           234410  234408    28102

with a per-page footer carrying the report's own as-of date. Indentation does not
survive text extraction, so the outline level is recovered from the LABEL: a
known Iowa county name opens a county, a party label opens a party within it, a
receipt-method label is a leaf we do not publish, and anything else is drift.

Four judgement calls worth naming:

* **"No Party" is Iowa's unaffiliated bucket and goes to `party_npa`.** It is not
  `party_oth`: Iowa's Libertarians and its "Other" registrants have chosen a
  party and Iowa's No Party registrants have declined one, and the unaffiliated
  share is the single most-watched number in early-vote coverage.

* **A trailing zero is rendered as a blank column.** "Libertarian 3 3" means 3
  requested, 3 issued and 0 received -- not "received unreported". That reading is
  not a guess: every county's party rows are checked against the county's own
  Received figure, and every report's counties against Iowa's own Grand Total, so
  a misread would raise SchemaDrift rather than publish.

* **The receipt-method split is validated but not published.** Iowa's methods
  (Counter / In-Office, Satellite, Mail, E-Mail, Drop Box, Fax, Health Care
  Facility) describe how an ABSENTEE ballot came back, not a mail-versus-in-person
  split of ballots cast, and folding them into `inperson` would double-count them
  against `mail_returned`. Unknown method labels still raise drift, and every
  method row is added up against the party row it hangs off -- see `_PartyBlock`,
  which is what makes an unfamiliar leaf label safe to recognise.

* **Iowa's own file carries a leaked junk row.** Four of the sixteen 2024 reports
  contain a receipt-method line labelled `37480`. An all-digit label is treated as
  one of these leaked internal codes and skipped; it can never be a county or a
  party, and the cross-checks above would catch it if it were.

Three things the 2022 file does and the 2024 file does not, all of them shape and
none of them a different reading of the data:

* **A date can be refreshed twice.** 2022's 1,289 pages are 33 complete passes
  over the 99 counties carrying only 30 distinct dates: 11/15 and 11/8 are each
  two 53-page passes and 10/6 is a 26-page pass followed by a 16-page one. A pass
  always ENDS with Iowa's own Grand Total row, so that -- not a change of date --
  is where one pass stops and the next begins. `_reports` keeps the FIRST pass
  over each date, because the file is newest-first and the file says so out loud
  on the one pair where it can: 11/8's first pass is footed "Data from 5:45 p.m."
  and its second "Data from night before", election evening ahead of election
  morning.

* **The date can sit in a different PLACE.** 2022's reports from 10/3 to 10/17 --
  253 pages, a fifth of the file -- append it to the credit line ("Prepared by
  the Office of Iowa Secretary of State 10/17/2022") instead of giving it a line
  of its own. Same date, read off a different line; a page with a date on NEITHER
  is still SchemaDrift, because a page whose date we cannot find is a page we
  misread rather than a page to attribute to the previous day.

* **"Mailing" is a receipt method, and is not another word for "Mail".** All 29
  of its appearances are the same leaf of the same block -- Linn County, No
  Party, once per pass from 10/6 on -- and 22 of those blocks carry their own
  "Mail" row as well, side by side with it (on 11/16, "Mail 2057 2057 2057" and
  "Mailing 2 2 1"). Folding it into Mail would therefore be inventing a mapping,
  not recognising a spelling. What settles what it IS is `_PartyBlock`: a party's
  receipt-method leaves sum to the party's own row exactly, in all three columns,
  in every one of the 11,590 party blocks of the 2022 file and the 7,327 of 2024.
  A row in the method slot that is really a PARTY is invisible to the county
  cross-check -- methods contribute nothing to it -- but it breaks that sum by
  its own counts. "Mailing" does not break it, so it is a method.
"""

from __future__ import annotations

import io
import logging
import re
from collections import defaultdict
from datetime import date, datetime

import pypdf

from ..normalize import PARTY_DEM, PARTY_NPA, PARTY_OTH, PARTY_REP, party as _party
from ..schema import TIER_SCRAPER, CountyDay, StateDay
from . import _fips
from ._net import Missing, get
from .base import Adapter, FetchResult, NotYetPublished, SchemaDrift, SourceError

log = logging.getLogger(__name__)

#: Candidate locations for one cycle's general-election report, tried in order.
#:
#: The first is VERIFIED: it serves the real report for both 2022 and 2024. The
#: rest are UNVERIFIED for a general election -- Iowa moved the June 2026 PRIMARY
#: report onto Drupal's dated media folder (2026-06/ABS Counties 2026.pdf, itself
#: verified), so the 2026 general will probably land in the same shape under
#: whichever month it is uploaded in, and none of those folders exists yet. A
#: wrong guess cannot publish anything: _load() reads each candidate's own
#: "<Month D, YYYY> General Election" title line and rejects a file that is not
#: this cycle's general.
FILE_URLS = (
    "https://sos.iowa.gov/elections/pdf/{cycle}/general/AbsenteeCounty{cycle}.pdf",
    "https://sos.iowa.gov/sites/default/files/{cycle}-11/ABS%20Counties%20{cycle}.pdf",
    "https://sos.iowa.gov/sites/default/files/{cycle}-10/ABS%20Counties%20{cycle}.pdf",
    "https://sos.iowa.gov/sites/default/files/{cycle}-09/ABS%20Counties%20{cycle}.pdf",
)

#: The report this adapter publishes. Iowa uses one format for both, so a primary
#: file is a perfectly valid report -- just not the one the general-election
#: series is made of.
ELECTION_KIND = "General"

TITLE = "Absentee Ballot Statistics"
COLUMN_HEADER = "county/ party / receipt method requested issued received"

#: Page furniture. The freshness note changes wording between cycles -- 2024 says
#: "Data Pulled: 7:00 AM (UTC)", 2022 said "Data from night before" and, on
#: Election Day itself, "Data from 5:45 p.m." -- so it is matched by prefix.
FOOTER_PREFIXES = (
    "prepared by the office of iowa secretary of state",
    "data pulled:", "data from",
)

TOTAL_LABEL = "grand total"

#: Receipt methods, lowercased. Leaves of the outline: validated so a renamed or
#: brand-new one surfaces as drift, but not published. See the module docstring.
METHODS = frozenset({
    "counter / in-office", "satellite", "mail", "e-mail", "drop box", "fax",
    "in-person", "health care facility", "ballot not yet returned by voter",
    # 2022 only, and NOT a second spelling of "mail": 22 of its 29 appearances
    # are in a block that prints its own "Mail" row alongside it. Mapped as a
    # method of its own on the evidence of `_PartyBlock` -- the receipt-method
    # leaves of the block it sits in add up to that block's party row exactly,
    # which they could not do if this row belonged at any other outline level.
    # See the module docstring.
    "mailing",
})

_ELECTION_LINE = re.compile(r"^([A-Z][a-z]+ \d{1,2}, \d{4})\s+(\w+)\s+Election$")
#: The report's own as-of date, in the page footer, on a line of its own: 2024
#: prints it bare ("10/17/2024"), 2022 labelled it ("report date 11/8/2022") on
#: every report from 10/18 onwards.
_FOOTER_DATE = re.compile(r"^(?:report date\s+)?(\d{1,2})/(\d{1,2})/(\d{4})$", re.I)
#: ...and the SAME date appended to a footer line instead, which is how 2022's
#: 10/3-10/17 reports carry it. Only ever searched on a line already recognised
#: as page furniture, which is why it can afford to be this loose.
_FOOTER_TAIL_DATE = re.compile(r"\s(\d{1,2})/(\d{1,2})/(\d{4})$")
_COUNT_LINE = re.compile(r"^(?P<label>.*?)(?P<nums>(?:\s+\d[\d,]*)+)$")

_PARTY_FIELD = {
    PARTY_DEM: "party_dem", PARTY_REP: "party_rep",
    PARTY_NPA: "party_npa", PARTY_OTH: "party_oth",
}


def _counts(raw: str) -> tuple[int, int, int]:
    """(requested, issued, received) from a row's trailing numbers.

    Iowa renders trailing zeros as empty columns, so "Other 1" is one requested,
    none issued, none back and "Libertarian 3 3" is three requested, three issued,
    none back. See the module docstring for why that reading is safe to make.
    """
    values = [int(n.replace(",", "")) for n in raw.split()]
    if not 1 <= len(values) <= 3:
        raise SchemaDrift(f"IA: expected 1-3 counts on a row, got {values!r}")
    values += [0] * (3 - len(values))
    return values[0], values[1], values[2]


class _County:
    __slots__ = ("fips", "name", "requested", "issued", "received", "party")

    def __init__(self, fips: str, name: str) -> None:
        self.fips = fips
        self.name = name
        self.requested = 0
        self.issued = 0
        self.received = 0
        self.party: dict[str, int] = defaultdict(int)


class _PartyBlock:
    """One party row, and the receipt-method leaves hanging off it.

    Iowa prints both and they reconcile EXACTLY -- the leaves sum to the party
    row in all three columns, in every one of the 11,590 party blocks of the 2022
    file and the 7,327 of 2024 -- so a mismatch means we read the outline wrong.

    This is the only check that can tell a receipt-method label we have never
    seen from a PARTY we have never seen. A party misread as a method is
    invisible to `_emit`'s county cross-check, because methods contribute
    nothing to it; here it shows up immediately as its own counts going missing
    from the sum. It is what makes mapping 2022's "Mailing" a fact rather than a
    guess.
    """

    __slots__ = ("bucket", "row", "leaves")

    def __init__(self, bucket: str, row: tuple[int, int, int]) -> None:
        self.bucket = bucket
        self.row = row
        self.leaves = [0, 0, 0]

    def add(self, counts: tuple[int, int, int]) -> None:
        for index, value in enumerate(counts):
            self.leaves[index] += value

    def close(self) -> None:
        if tuple(self.leaves) != self.row:
            raise SchemaDrift(
                f"IA: the {self.bucket} row reads {self.row} requested/issued/"
                f"received but its receipt-method rows total "
                f"{tuple(self.leaves)}"
            )


def _close(block: _PartyBlock | None) -> None:
    """Check a party block, now that a sibling row has ended it.

    Only a block CLOSED by the next county, party or Grand Total row is checked.
    A block still open when the pages run out is not wrong, it is unfinished --
    that is every truncated read, the saved fixtures included -- and there is
    nothing to compare it against.
    """
    if block is not None:
        block.close()


class _Report:
    """One daily report: its counties, and Iowa's own Grand Total row."""

    __slots__ = ("day", "counties", "total")

    def __init__(self, day: date) -> None:
        self.day = day
        self.counties: dict[str, _County] = {}
        self.total: tuple[int, int, int] | None = None


def election_line(text: str) -> tuple[date, str] | None:
    """(election date, kind) from a report's second line, or None if it is not one."""
    m = _ELECTION_LINE.match(text.strip())
    if not m:
        return None
    try:
        day = datetime.strptime(m.group(1), "%B %d, %Y").date()
    except ValueError:
        return None
    return day, m.group(2)


def _report_date(line: str, match: "re.Match[str]") -> date:
    """The date in a footer match, whichever line it was found on."""
    month, day_of, year = (int(g) for g in match.groups())
    try:
        return date(year, month, day_of)
    except ValueError as exc:
        raise SchemaDrift(f"IA: {line!r} is not a report date") from exc


def _one_date(existing: date | None, found: date) -> date:
    """One page carries one report date, wherever it is printed.

    Two that disagree would mean one of them was read off a line that is not the
    report date -- which is exactly the mistake available here, since 2022 prints
    its date somewhere 2024 does not.
    """
    if existing is not None and existing != found:
        raise SchemaDrift(
            f"IA: page carries two different report dates, "
            f"{existing.isoformat()} and {found.isoformat()}"
        )
    return found


def _page_date(page_day: date | None, cycle: int) -> date:
    """The report date a page carries, checked.

    Iowa's reports run from early October into mid-November of the election year
    -- 2022's span 10/3 to 11/16 and 2024's 10/17 to 11/6 -- and the footer date
    is the one number on the page with no cross-check of its own, so it is pinned
    to the cycle it claims to belong to.
    """
    if page_day is None:
        # Every real page carries the date, on its own line or appended to the
        # credit line; a page with one in NEITHER place is a page we misread,
        # not a page to attribute to the previous day.
        raise SchemaDrift("IA: report page carries no 'Data Pulled' date")
    if page_day.year != int(cycle):
        raise SchemaDrift(
            f"IA: page is dated {page_day.isoformat()}, which is not in the "
            f"{cycle} cycle"
        )
    return page_day


def _classify(label: str):
    """(kind, payload) for one outline label.

    kind is one of "total", "method", "county" or "party"; payload is the
    (FIPS, canonical name) pair for a county and the bucket for a party. An
    unrecognised label raises rather than being dropped -- a silently skipped row
    would understate a county forever.
    """
    key = " ".join(label.lower().split())
    if key == TOTAL_LABEL:
        return "total", None
    if key in METHODS:
        return "method", None
    if label.isdigit():
        # One of Iowa's leaked internal codes (see the module docstring). It sits
        # where a receipt method sits and contributes nothing we publish.
        return "method", None
    hit = _fips.lookup("IA", label)
    if hit is not None:
        return "county", hit
    bucket = _party(label)
    if bucket is not None:
        return "party", bucket
    raise SchemaDrift(f"IA: unrecognised outline label {label!r}")


def _refreshes(body: bytes, cycle: int):
    """Yield every complete pass over the counties in the file, newest first.

    A "pass" (Iowa's own refresh) opens at the first county row and closes at
    Iowa's own Grand Total. USUALLY one pass per date -- but 2022's 1,289 pages
    are 33 passes carrying 30 dates, so a change of date is not a safe boundary
    on its own. `_reports` is what turns these into one report per date.
    """
    try:
        reader = pypdf.PdfReader(io.BytesIO(body))
    except Exception as exc:  # noqa: BLE001 -- pypdf raises a zoo of types
        raise SourceError(f"IA: could not open the absentee report: {exc}") from exc

    report: _Report | None = None
    current: _County | None = None
    block: _PartyBlock | None = None

    for page in reader.pages:
        lines = page.extract_text().splitlines()
        page_day: date | None = None
        entries: list[tuple[str, object, tuple[int, int, int]]] = []

        for raw in lines:
            line = raw.strip()
            if not line or line == TITLE:
                continue
            lowered = line.lower()
            if lowered == COLUMN_HEADER:
                continue
            if lowered.startswith(FOOTER_PREFIXES):
                # 2022's October reports append the report date to the credit
                # line rather than giving it one of its own. A different PLACE,
                # not a different date -- so read it before the line is dropped
                # as furniture.
                tail = _FOOTER_TAIL_DATE.search(line)
                if tail is not None:
                    page_day = _one_date(page_day, _report_date(line, tail))
                continue

            found = election_line(line)
            if found is not None:
                day, kind = found
                if day.year != int(cycle) or kind != ELECTION_KIND:
                    raise SchemaDrift(
                        f"IA: report is for the {day.year} {kind}, not the {cycle} "
                        f"{ELECTION_KIND}"
                    )
                continue

            m = _FOOTER_DATE.match(line)
            if m:
                page_day = _one_date(page_day, _report_date(line, m))
                continue

            m = _COUNT_LINE.match(line)
            if not m:
                raise SchemaDrift(f"IA: unparseable line {line!r}")
            label = m.group("label").strip()
            if not label:
                raise SchemaDrift(f"IA: bare numbers with no label: {line!r}")
            kind, payload = _classify(label)
            entries.append((kind, payload, _counts(m.group("nums"))))

        page_day = _page_date(page_day, cycle)

        if report is not None and page_day != report.day:
            yield report
            report, current, block = None, None, None

        for kind, payload, (requested, issued, received) in entries:
            if kind == "county" and report is not None and report.total is not None:
                # THE SAME DAY REFRESHED TWICE, which 2022 does three times.
                # Every pass ends with Iowa's own Grand Total, so a county row
                # arriving AFTER that total is the top of a new pass. That is
                # what separates it from the other way a county can repeat --
                # the same county twice INSIDE one pass, before its Grand Total,
                # which is real drift and still raises below, because the
                # duplicate guard only ever looks within one terminated pass.
                # If Iowa ever drops the Grand Total, a genuine double refresh
                # raises rather than being guessed at, which is the right way
                # round.
                yield report
                report, current, block = None, None, None

            if report is None:
                report = _Report(page_day)
                current, block = None, None

            if kind == "total":
                _close(block)
                block = None
                report.total = (requested, issued, received)
                continue
            if kind == "county":
                fips, canonical = payload  # type: ignore[misc]
                if fips in report.counties:
                    # Checked BEFORE the party block above it is reconciled. A
                    # county seen twice inside one pass means the outline itself
                    # repeated, which is the root cause; the half-finished party
                    # block that repetition interrupts is only its symptom, and
                    # reporting the symptom would send the next reader looking
                    # at the wrong row.
                    raise SchemaDrift(
                        f"IA: {canonical} appears twice in the "
                        f"{report.day.isoformat()} report"
                    )
                _close(block)
                block = None
                current = _County(fips, canonical)
                report.counties[fips] = current
                current.requested = requested
                current.issued = issued
                current.received = received
                continue
            if kind == "party":
                _close(block)
                block = _PartyBlock(str(payload), (requested, issued, received))
                if current is not None:
                    current.party[str(payload)] += received
                # A party row before this report's first county row is the tail
                # of a county whose opening row is on a page we do not have --
                # only possible on a truncated read. Its own method leaves still
                # have to reconcile against it.
                continue
            # A receipt-method row: a leaf we do not publish, but one that has to
            # add up against the party row above it.
            if block is not None:
                block.add((requested, issued, received))

    if report is not None:
        yield report


def _reports(body: bytes, cycle: int, *, limit: int | None = None):
    """Yield one _Report per report DATE, newest first.

    Iowa prepends each refresh, so the first pass over a date is that date's
    latest one and a later pass over the same date has been superseded. The file
    says so out loud on the one pair where it can: 2022-11-08 appears twice,
    first footed "Data from 5:45 p.m." and then "Data from night before" --
    election evening ahead of election morning -- and 10/6's two passes likewise
    run larger then smaller.

    `limit` stops after that many reports, which is what the daily run wants: the
    newest report is the first ~53 pages of a file that is well over a thousand
    pages long by November.
    """
    seen: set[date] = set()
    done = 0

    for report in _refreshes(body, cycle):
        if report.day in seen:
            log.debug("IA: %s was refreshed more than once; keeping the first "
                      "(newest) pass", report.day.isoformat())
            continue
        seen.add(report.day)
        yield report
        done += 1
        if limit is not None and done >= limit:
            return


def _emit(report: _Report, cycle: int) -> FetchResult:
    """One report -> one StateDay plus its CountyDay rows, cross-checked."""
    if not report.counties:
        raise SchemaDrift(f"IA: report for {report.day.isoformat()} has no counties")

    result = FetchResult()
    totals = {"requested": 0, "received": 0}
    party_totals: dict[str, int] = defaultdict(int)

    for fips in sorted(report.counties):
        county = report.counties[fips]
        by_party = sum(county.party.values())
        if by_party != county.received:
            # Iowa publishes both, so they must agree; a mismatch means we read
            # the outline wrong and would publish a party split that is not real.
            raise SchemaDrift(
                f"IA: {county.name} party rows total {by_party} received, but the "
                f"county row says {county.received}"
            )
        totals["requested"] += county.requested
        totals["received"] += county.received
        for bucket, count in county.party.items():
            party_totals[bucket] += count

        result.county_rows.append(CountyDay(
            cycle=cycle, state="IA", county_fips=fips, day=report.day,
            county_name=county.name,
            ballots_total=county.received,
            mail_returned=county.received,
            # Iowa's receipt-method split is not a mail/in-person split of ballots
            # cast -- see the module docstring. Never 0.
            inperson=None,
            # Iowa registers by party and reports every bucket, so a party with no
            # ballots back yet is a genuine 0, not a blank.
            **{field: county.party.get(key, 0) for key, field in _PARTY_FIELD.items()},
        ))

    if report.total is not None:
        requested, _issued, received = report.total
        if (requested, received) != (totals["requested"], totals["received"]):
            raise SchemaDrift(
                f"IA: counties total {totals['requested']}/{totals['received']} "
                f"requested/received, but Iowa's Grand Total says {requested}/{received}"
            )

    result.state_rows.append(StateDay(
        cycle=cycle, state="IA", day=report.day,
        ballots_total=totals["received"],
        mail_requested=totals["requested"],
        mail_returned=totals["received"],
        inperson=None,
        **{field: party_totals.get(key, 0) for key, field in _PARTY_FIELD.items()},
    ))
    return result


def parse(body: bytes, cycle: int, *, limit: int | None = None) -> FetchResult:
    """Parse an absentee report PDF into canonical rows, newest report first."""
    result = FetchResult()
    for report in _reports(body, cycle, limit=limit):
        result.extend(_emit(report, cycle))
    if not result:
        raise SchemaDrift(f"IA: absentee report for {cycle} contained no reports")
    return result


class IAScraper(Adapter):
    """Tier 1 for Iowa: the SoS daily Absentee Ballot Statistics report."""

    state = "IA"
    name = "ia-sos"
    tier = TIER_SCRAPER

    def _load(self, cycle: int, *, use_cache: bool) -> bytes:
        problems: list[str] = []
        for template in FILE_URLS:
            url = template.format(cycle=cycle)
            filename = f"{cycle}_" + url.rsplit("/", 1)[-1].replace("%20", "_")
            try:
                body = get(url, state="IA", filename=filename,
                           use_cache=use_cache, min_bytes=4096)
            except Missing as exc:
                problems.append(str(exc))
                continue
            if not body.startswith(b"%PDF-"):
                # The SoS CMS answers an unposted report with a styled HTML page.
                problems.append(f"{url} came back as HTML, not a PDF")
                continue
            problem = self._wrong_election(body, cycle)
            if problem:
                problems.append(f"{url}: {problem}")
                continue
            return body
        raise NotYetPublished(
            f"IA: no {cycle} general-election absentee report posted yet "
            f"({'; '.join(problems)})"
        )

    @staticmethod
    def _wrong_election(body: bytes, cycle: int) -> str | None:
        """None if this file is the cycle's general, else why it is not.

        Iowa uses one format for primaries and generals, and the guessed 2026
        locations could land on either, so the file has to say which it is before
        a single number comes out of it.
        """
        try:
            first = pypdf.PdfReader(io.BytesIO(body)).pages[0].extract_text()
        except Exception as exc:  # noqa: BLE001
            raise SourceError(f"IA: could not open the absentee report: {exc}") from exc
        for line in first.splitlines():
            found = election_line(line)
            if found is None:
                continue
            day, kind = found
            if day.year != int(cycle) or kind != ELECTION_KIND:
                return f"is the {day.year} {kind} report"
            return None
        raise SchemaDrift("IA: absentee report has no '<date> <kind> Election' title line")

    @staticmethod
    def _no_later_than(result: FetchResult, horizon: date) -> FetchResult:
        """Refuse a report dated after `horizon`. Used by BOTH fetch paths.

        This is the check that catches a report date read off the wrong line, and
        Iowa is a state where that can happen: its 2022 file prints the date
        somewhere its 2024 file does not, and a fifth of the 2022 pages parsed as
        dateless until that was fixed. A guard that only ran on the daily path
        would have left the backfill -- the path that actually reads the 2022
        file -- with nothing checking it at all.
        """
        newest = max(row.day for row in result.state_rows)
        if newest > horizon:
            raise NotYetPublished(
                f"IA: report is dated {newest.isoformat()}, after {horizon.isoformat()}"
            )
        return result

    def fetch(self, cycle: int, as_of: date) -> FetchResult:
        """Only the newest report -- the rest of the file is yesterday's news."""
        return self._no_later_than(
            parse(self._load(cycle, use_cache=False), cycle, limit=1), as_of)

    def fetch_history(self, cycle: int) -> FetchResult:
        """Every daily report in the archived file -- the whole cycle in one download.

        The horizon is TODAY rather than the cycle's election date, which is what
        the NC-shaped adapters pass: Iowa keeps publishing for more than a week
        after Election Day, and four of 2022's thirty reports are dated after it
        and are real.
        """
        return self._no_later_than(
            parse(self._load(cycle, use_cache=True), cycle), date.today())
