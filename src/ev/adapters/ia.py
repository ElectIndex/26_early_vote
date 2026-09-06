"""Iowa — the Secretary of State's "Absentee Ballot Statistics" report (PDF).

Iowa publishes absentee requests and returns as a single PDF, refreshed daily
through the absentee period. The important and slightly surprising thing is that
it is not a snapshot: each refresh PRE-PENDS a new report to the same file, so
the 2024 general's `AbsenteeCounty2024.pdf` is 937 pages holding all sixteen
daily reports, newest first. Like North Carolina, one download therefore
reconstructs the entire daily curve, and a day we fail to run is not a day of
Iowa's series that is lost forever.

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
  against `mail_returned`. Unknown method labels still raise drift.

* **Iowa's own file carries a leaked junk row.** Four of the sixteen 2024 reports
  contain a receipt-method line labelled `37480`. An all-digit label is treated as
  one of these leaked internal codes and skipped; it can never be a county or a
  party, and the cross-checks above would catch it if it were.
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
})

_ELECTION_LINE = re.compile(r"^([A-Z][a-z]+ \d{1,2}, \d{4})\s+(\w+)\s+Election$")
#: The report's own as-of date, in the page footer. 2024 prints it bare; 2022
#: labelled it "report date 11/8/2022".
_FOOTER_DATE = re.compile(r"^(?:report date\s+)?(\d{1,2})/(\d{1,2})/(\d{4})$", re.I)
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


def _reports(body: bytes, cycle: int, *, limit: int | None = None):
    """Yield each daily _Report in the file, newest first.

    `limit` stops after that many reports, which is what the daily run wants: the
    newest report is the first ~45 pages of a file that is hundreds of pages long
    by November.
    """
    try:
        reader = pypdf.PdfReader(io.BytesIO(body))
    except Exception as exc:  # noqa: BLE001 -- pypdf raises a zoo of types
        raise SourceError(f"IA: could not open the absentee report: {exc}") from exc

    report: _Report | None = None
    current: _County | None = None
    done = 0

    for page in reader.pages:
        lines = page.extract_text().splitlines()
        page_day: date | None = None
        entries: list[tuple[str, object, tuple[int, int, int]]] = []

        for raw in lines:
            line = raw.strip()
            if not line or line == TITLE:
                continue
            lowered = line.lower()
            if lowered == COLUMN_HEADER or lowered.startswith(FOOTER_PREFIXES):
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
                month, day_of, year = (int(g) for g in m.groups())
                try:
                    page_day = date(year, month, day_of)
                except ValueError as exc:
                    raise SchemaDrift(f"IA: {line!r} is not a report date") from exc
                continue

            m = _COUNT_LINE.match(line)
            if not m:
                raise SchemaDrift(f"IA: unparseable line {line!r}")
            label = m.group("label").strip()
            if not label:
                raise SchemaDrift(f"IA: bare numbers with no label: {line!r}")
            kind, payload = _classify(label)
            entries.append((kind, payload, _counts(m.group("nums"))))

        if page_day is None:
            # Every real page carries the footer date; a page without one is a
            # page we misread, not a page to attribute to the previous day.
            raise SchemaDrift("IA: report page carries no 'Data Pulled' date")

        if report is None or page_day != report.day:
            if report is not None:
                yield report
                done += 1
                if limit is not None and done >= limit:
                    return
            report = _Report(page_day)
            current = None

        for kind, payload, (requested, issued, received) in entries:
            if kind == "total":
                report.total = (requested, issued, received)
                continue
            if kind == "county":
                fips, canonical = payload  # type: ignore[misc]
                if fips in report.counties:
                    raise SchemaDrift(
                        f"IA: {canonical} appears twice in the "
                        f"{report.day.isoformat()} report"
                    )
                current = _County(fips, canonical)
                report.counties[fips] = current
                current.requested = requested
                current.issued = issued
                current.received = received
                continue
            if current is None:
                # A party or method row before this report's first county row:
                # the tail of a county whose opening row is on a page we do not
                # have. Only possible on a truncated read.
                continue
            if kind == "party":
                current.party[str(payload)] += received
            # method rows are leaves; nothing to accumulate.

    if report is not None:
        yield report


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

    def fetch(self, cycle: int, as_of: date) -> FetchResult:
        """Only the newest report -- the rest of the file is yesterday's news."""
        result = parse(self._load(cycle, use_cache=False), cycle, limit=1)
        published = result.state_rows[0].day
        if published > as_of:
            raise NotYetPublished(
                f"IA: report is dated {published.isoformat()}, after {as_of.isoformat()}"
            )
        return result

    def fetch_history(self, cycle: int) -> FetchResult:
        """Every daily report in the archived file -- the whole cycle in one download."""
        return parse(self._load(cycle, use_cache=True), cycle)
