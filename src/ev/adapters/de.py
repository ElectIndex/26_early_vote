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

* **No party breakdown IN 2024.** Delaware registers voters by party, and its
  separate registration totals carry it, but the 2024 report does not, so every
  `party_*` field is None for that cycle. See THE BLANK RULE in schema.py. The
  2022 report DOES carry it -- see below.

## 2022: a different directory, a different filename, and a party column

Delaware's 2022 general is the state's first with in-person early voting, and it
was reported under a different name from a different tree:

    2022  elections.delaware.gov/reports/pdfs/GE2022_Report_VoterCountsByVotingMethod.pdf
    2024  elections.delaware.gov/voter/registrationtotals/reports/pdfs/GE2024_GeneralElectionVoterCountsByVotingMethod.pdf

⚠️ That move is why this cycle was written off twice, and the mistake is worth
naming because it is easy to repeat. Both earlier sweeps were scoped to the 2024
file's own directory -- first live probes of `GE2022_*` under it (all 404), then
a CDX listing of every capture under that prefix, 141 files, of which the only
two named `…VoterCountsByVotingMethod` are `GE2024_` and `PR2024_`. Both results
are correct and neither is evidence about 2022. Widening the same query from one
directory to the whole DOMAIN (5,191 distinct urlkeys captured between September
2022 and March 2023) finds the file immediately. **A prefix sweep can only prove
something about the prefix.**

The live host 404s the 2022 path today, as it does the 2024 one, so both cycles
are Wayback jobs. Five captures, three distinct digests, three report dates:

```
Fri Nov  4 2022 13:32  absentee 20,331  early  38,067          (58,398 total)
Mon Nov  7 2022 10:52  absentee 21,445  early  55,532          (76,977 total)
Tue Nov  8 2022 16:04  absentee 22,597  early  56,195  polling place 170,722
```

Two things about the 2022 file are not true of the 2024 one:

* **It carries a `Political Party` column** -- DEMOCRATIC / REPUBLICAN / OTHER,
  crossed with the method -- so 2022 publishes real party numbers AND the
  party-by-method crosstab as `MethodDay` rows. `party_npa` stays blank all the
  same: Delaware's "OTHER" is every registrant who is neither a Democrat nor a
  Republican, its large No Party file included, and the unaffiliated share
  cannot be recovered from it. See `PUBLISHED_PARTIES`.

* **The Election Day capture is laid out differently from the two before it.**
  During the season the table is flat -- one row per (method, party), closed by
  a `Total`. On Election Day the same numbers are re-laid-out as one block per
  method, each with its own repeated header, its party rows carrying no method
  label at all, and its own `Total`, closed by a `Grand Total`. Both are read by
  the same walk in `_table`; the layout is inferred from the header, never from
  the cycle.
"""

from __future__ import annotations

import io
import json
import logging
import re
from datetime import date, datetime, timedelta

import pypdf

from ..calendar import election_date
from ..normalize import (
    METHOD_INPERSON, METHOD_MAIL, PARTY_DEM, PARTY_OTH, PARTY_REP,
    party as _party_bucket,
)
from ..schema import TIER_SCRAPER, CountyDay, MethodDay, StateDay
from . import _fips, _methods
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
REPORT_URL = HOST + "/voter/registrationtotals/reports/pdfs/GE{cycle}_GeneralElectionVoterCountsByVotingMethod.pdf"

#: ⚠️ 2022 IS A DIFFERENT PATH AND A DIFFERENT FILENAME, and that is the whole
#: reason this cycle was written off twice.
#:
#: Two earlier sweeps concluded "there is no GE2022 report". Both were scoped to
#: the directory the 2024 file lives in -- a live probe of
#: `.../voter/registrationtotals/reports/pdfs/GE2022_*.pdf` (404) and then a CDX
#: listing of every capture under that same prefix, which returns 141 distinct
#: files of which exactly two are named `…VoterCountsByVotingMethod`, `GE2024_`
#: and `PR2024_`. Both statements are true. Neither is evidence about 2022,
#: because in 2022 the Department published from `elections.delaware.gov/reports/
#: pdfs/` and called the file `GE2022_Report_VoterCountsByVotingMethod.pdf`. It
#: moved directory AND naming convention between the two cycles.
#:
#: FOUND 2026-09-08 by widening the query to the whole DOMAIN rather than one
#: directory (`url=elections.delaware.gov&matchType=domain&from=20220901&
#: to=20230301&collapse=urlkey` -> 5,191 distinct urlkeys, one of them this
#: file). It is archived five times, three distinct digests, all
#: `application/pdf`, and all three parse. The lesson is in the query, not the
#: file: a prefix sweep can only ever prove something about the prefix.
#:
#: The live host serves 404 for GE2022, GE2024 and GE2026 under this path today
#: (VERIFIED 2026-09-08), exactly as it does for the 2024 path -- Delaware
#: overwrites and then retires these files, so every past cycle is a Wayback job.
REPORT_URLS: dict[int, str] = {
    2022: HOST + "/reports/pdfs/GE2022_Report_VoterCountsByVotingMethod.pdf",
}

#: VERIFIED 2026-09-06: 200, 440,566 B. Delaware links the CURRENT election's
#: report from its home page ("View Report"), which today points at the
#: September primary's file. The index is scraped only for a link whose filename
#: starts with `GE<cycle>`, so a primary link can never be picked up by mistake.
INDEX_URL = HOST + "/index.html"

#: 2022 is the first Delaware general with in-person early voting at all -- the
#: statute took effect for it -- so there is no earlier report to want.
FIRST_CYCLE = 2022


def report_url(cycle: int) -> str:
    """Where Delaware published `cycle`'s voting-method report.

    One function, used by BOTH `fetch` and `fetch_history`, because the two
    paths asking different URLs for the same cycle is precisely how a backfill
    ends up describing a different file from the daily job.
    """
    return REPORT_URLS.get(int(cycle)) or REPORT_URL.format(cycle=cycle)

#: How long after Election Day a report can still legitimately be describing
#: that election. Delaware certifies within about two weeks; 30 days is generous
#: for that and still an order of magnitude short of the mistake this bounds,
#: which is a report date belonging to a different YEAR. See `_check_window`.
POST_ELECTION_DAYS = 30

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

#: The 2022 Election Day layout closes the whole table with its own row, which
#: is the same quantity the flat layouts call "Total": every ballot, all methods.
GRAND_TOTAL_ROW = "grand total"

#: The two methods that make `ballots_total`. The polling place is a ballot cast
#: ON Election Day and is deliberately not one of them -- see the docstring.
COUNTED_METHODS = ("mail_returned", "inperson")

#: Which canonical method each counted key publishes as, for the crosstab table.
CROSSTAB_METHODS = {"mail_returned": METHOD_MAIL, "inperson": METHOD_INPERSON}

#: The party buckets Delaware's 2022 report prints, in the order it prints them
#: nowhere consistently -- the flat layout runs DEM/REP/OTHER and the Election
#: Day one runs OTHER/REP/DEM, which is why rows are keyed by their own label
#: rather than by position.
#:
#: ⚠️ `party_npa` IS AND STAYS BLANK for Delaware. The report has exactly three
#: buckets and the third is "OTHER", which is every registrant who is neither a
#: Democrat nor a Republican -- Delaware's large No Party file included. It is
#: therefore NOT the "a real third party" bucket `normalize` describes, and
#: there is no way to recover the unaffiliated share from it. `normalize.party`
#: maps the label "other" to `oth`, which is the vocabulary's own answer for
#: the word Delaware prints, and the unaffiliated field stays None because
#: Delaware does not report it. THE BLANK RULE: a number we do not have is
#: blank, and a bucket that would be wrong is not filled in to look complete.
PUBLISHED_PARTIES = (PARTY_DEM, PARTY_REP, PARTY_OTH)

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

#: The 2024 header: one label column, "Voting Method", and then the counties.
_PLAIN_HEADER = re.compile(r"^Voting Method\b", re.I)
#: The 2022 header, which carries a SECOND label column. Its lead is either the
#: literal "Voting Method" -- the during-season layout, where every row names
#: its own method -- or a method NAME, in the Election Day layout, which repeats
#: the header once per method and leaves the rows under it carrying only a party.
_PARTY_HEADER = re.compile(r"^(?P<lead>[A-Za-z][A-Za-z ]*?)\s+Political Party\b", re.I)
#: What `_header_lead` returns when the method is on the rows, not the header.
METHOD_ON_THE_ROW = "voting method"


def _collapse(line: str) -> str:
    return " ".join(line.split())


def _today() -> date:
    """Today. Indirected so the future-date guard in `fetch_history` can be
    tested against a fixed clock rather than against whatever day CI runs."""
    return date.today()


def _header_lead(line: str) -> str | None:
    """The label of a table header's first column, or None if not a header.

    `"voting method"` for the 2024 report and for the 2022 during-season one --
    in both, each row names its own method. A METHOD's own name for the 2022
    Election Day report, whose header repeats per method.
    """
    text = _collapse(line)
    hit = _PARTY_HEADER.match(text)
    if hit:
        return " ".join(hit.group("lead").split()).lower()
    return METHOD_ON_THE_ROW if _PLAIN_HEADER.match(text) else None


def _party_of(label: str) -> str:
    """One of Delaware's three party labels, through the shared vocabulary.

    Rule 3: an unrecognised label raises rather than being bucketed. If Delaware
    ever splits "OTHER" into No Party and the minor parties, this is where we
    find out -- and that would be a new bucket to publish deliberately, not one
    to fold into the one beside it.
    """
    bucket = _party_bucket(label)
    if bucket is None:
        raise SchemaDrift(f"DE: unrecognised political party {label!r}")
    return bucket


def _int(token: str) -> int:
    return int(token.replace(",", ""))


def _total(*values: int | None) -> int | None:
    """Absentee + early voting, or None if either of them is unknown.

    ⚠️ THE BLANK RULE, and this was written as `(mail or 0) + (inperson or 0)`.
    A report missing one method row would have published a total silently short
    by that row; a report missing both would have published a confident `0`
    alongside two blank method fields, which is the worst version of the
    mistake -- "nobody has voted early in Delaware" stated as fact, in a state
    where a quarter of a million people did in 2024.

    It was never wrong, because `_table` refuses a report that is missing
    either row before a `Report` can exist. That is exactly why it needed
    changing: the arithmetic must not depend on a guard three functions away
    that a later edit could relax. Same rule, same shape, as `mi._sum`.
    """
    if any(value is None for value in values):
        return None
    return sum(values)


class Report:
    """One parsed Delaware report: an as-of date and a county x method table."""

    __slots__ = ("as_of", "counties", "rows", "by_party")

    def __init__(self, as_of: date, counties: list[tuple[str, str]],
                 rows: dict[str, list[int]],
                 by_party: dict[str, dict[str, list[int]]] | None = None) -> None:
        #: The date printed at the top of the report.
        self.as_of = as_of
        #: [(5-digit FIPS, census name)], in the report's own column order.
        self.counties = counties
        #: method key (and "total") -> one count per county. Delaware's own
        #: right-hand Total column is checked in `_check_arithmetic` and then
        #: dropped, because the statewide row is summed from the counties here
        #: exactly as it is everywhere else in this repo.
        self.rows = rows
        #: method key -> party bucket -> one count per county, and EMPTY for the
        #: 2024-vintage report, which has no party column at all. Empty means
        #: "not reported", so `to_result` publishes blank party fields for that
        #: cycle rather than zeros. See THE BLANK RULE in schema.py.
        self.by_party = by_party or {}

    def value(self, method: str, column: int) -> int | None:
        row = self.rows.get(method)
        return None if row is None else row[column]

    def statewide(self, method: str) -> int | None:
        row = self.rows.get(method)
        return None if row is None else sum(row)

    def party(self, method: str, bucket: str, column: int) -> int | None:
        row = self.by_party.get(method, {}).get(bucket)
        return None if row is None else row[column]

    def party_early(self, bucket: str, column: int | None = None) -> int | None:
        """Ballots cast EARLY by one party -- absentee plus early voting.

        The polling place is excluded for exactly the reason `ballots_total`
        excludes it: it is Election Day, not early voting. A bucket missing from
        either counted method makes the answer None rather than a short sum --
        the same shape, and the same reasoning, as `_total`.
        """
        if not self.by_party:
            return None
        parts = []
        for method in COUNTED_METHODS:
            row = self.by_party.get(method, {}).get(bucket)
            if row is None:
                return None
            parts.append(sum(row) if column is None else row[column])
        return sum(parts)


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
    _check_window(as_of, cycle)
    counties, header_at = _county_columns(lines)
    # The table is handed its own FIRST header line, not the line after it: the
    # 2022 Election Day layout repeats the header once per method and the walk
    # needs to see every one of them to know which block a bare party row is in.
    rows, stated, by_party = _table(lines[header_at:])
    _check_arithmetic(rows, stated)
    return Report(as_of, counties, rows, by_party)


def _check_window(as_of: date, cycle: int) -> None:
    """The report's own date must belong to the election it names.

    ⚠️ GUARD PARITY, and this is the half that was missing. `fetch` refuses a
    report dated after the day being asked for -- it will not publish tomorrow's
    number today. `fetch_history` had no bound of any kind: it took whatever
    date the PDF printed and published a row under it, for as many archived
    captures as the Wayback Machine held. The date is the x-axis of every
    comparison this repo makes, so one capture of a REPRINT -- Delaware
    regenerating the 2024 report during a 2025 audit, say, which is a normal
    thing for an election office to do and which the archive would happily
    capture -- puts a full 2024 electorate at days_to_election -200 and makes it
    that cycle's final.

    Bounded the way `aggregator.py` and `civicapi.py` bound their own dates:
    from the December before the cycle to a month past Election Day. Drift
    rather than absence, because the title line has already confirmed this IS
    the cycle's general -- a report that says "2024 General Election" and is
    dated 2025 is a file we do not understand, not a file that is not there yet.
    """
    lo = date(cycle - 1, 12, 1)
    hi = election_date(cycle) + timedelta(days=POST_ELECTION_DAYS)
    if not lo <= as_of <= hi:
        raise SchemaDrift(
            f"DE: the {cycle} general's report is dated {as_of.isoformat()}, "
            f"outside {lo.isoformat()}..{hi.isoformat()}"
        )


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

    The FIRST header wins. All three of Delaware's layouts split the names the
    same way -- the 2022 Election Day report simply repeats the pair once per
    method, with the same three names above each -- so the first pair fixes the
    column order for the whole file and `_table` reads every later header only
    for the method it names.
    """
    for index, line in enumerate(lines):
        if _header_lead(line) is None:
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


def _table(lines: list[str]) -> tuple[dict[str, list[int]], dict[str, int],
                                      dict[str, dict[str, list[int]]]]:
    """Read the table body, in whichever of Delaware's three shapes it is.

    Returns (method key -> per-county counts, method key -> Delaware's own row
    total, method key -> party bucket -> per-county counts).

    THREE LAYOUTS, ONE WALK. Delaware has printed this table three ways and all
    three are live data we publish, so the walk carries one piece of state --
    which method block we are inside -- and everything else falls out of the row
    label:

    * **2024, no party column.** `Absentee 14,999 4,394 10,533 29,926`, closed by
      a `Total` row. The third return value is empty for it, which is what makes
      that cycle's party fields blank rather than zero.
    * **2022 during the season.** The header gains a `Political Party` column and
      each row names both: `Absentee DEMOCRATIC 6,788 2,064 4,180 13,032`. Same
      closing `Total`.
    * **2022 on Election Day.** The same numbers, re-laid-out as one block per
      method: a header naming the method, three rows carrying only a party, and
      the block's own `Total`; then a final `Grand Total`.

    The per-method totals in the party layouts are SUMMED from the party rows
    rather than read off the block's Total line, and where Delaware prints both
    they must agree -- that disagreement is the one thing a re-layout could
    silently break, so it is checked rather than assumed.
    """
    rows: dict[str, list[int]] = {}
    stated: dict[str, int] = {}
    by_party: dict[str, dict[str, list[int]]] = {}
    party_stated: dict[tuple[str, str], int] = {}
    printed_totals: dict[str, tuple[list[int], int]] = {}
    #: The method a bare-party row belongs to; None when rows name their own.
    block: str | None = None

    for line in lines:
        text = _collapse(line)
        lead = _header_lead(line)
        if lead is not None:
            if lead == METHOD_ON_THE_ROW:
                block = None
                continue
            block = METHOD_ROWS.get(lead)
            if block is None:
                raise SchemaDrift(f"DE: unrecognised table heading {lead!r}")
            continue

        hit = _DATA_ROW.match(text)
        if hit is None:
            if _LOOKS_LIKE_A_ROW.search(text):
                # As many numbers as the table is wide, but not a shape we can
                # read. Silently skipping it would drop a method row.
                raise SchemaDrift(f"DE: unreadable table row {text!r}")
            continue
        label = " ".join(hit.group(1).split()).lower()
        numbers = [_int(token) for token in hit.group(2).split()]
        counts, total = numbers[:COUNTY_COLUMNS], numbers[COUNTY_COLUMNS]

        if label == GRAND_TOTAL_ROW:
            # Closes the whole Election Day table, from inside the last block.
            block = None
            rows[TOTAL_ROW], stated[TOTAL_ROW] = counts, total
            continue
        if block is not None:
            if label == TOTAL_ROW:
                printed_totals[block] = (counts, total)
                block = None
                continue
            _keep_party(by_party, party_stated, block, _party_of(label), counts, total)
            continue
        if label == TOTAL_ROW:
            rows[TOTAL_ROW], stated[TOTAL_ROW] = counts, total
            continue
        key = METHOD_ROWS.get(label)
        if key is not None:
            rows[key], stated[key] = counts, total
            continue
        split = _method_and_party(label)
        if split is None:
            raise SchemaDrift(f"DE: unrecognised voting-method row {hit.group(1)!r}")
        _keep_party(by_party, party_stated, *split, counts, total)

    for method, buckets in by_party.items():
        summed = [sum(row[i] for row in buckets.values()) for i in range(COUNTY_COLUMNS)]
        summed_total = sum(party_stated[(method, bucket)] for bucket in buckets)
        printed = printed_totals.get(method)
        if printed is not None and printed != (summed, summed_total):
            raise SchemaDrift(
                f"DE: the {method!r} block's party rows sum to {summed} "
                f"({summed_total}) but its own Total row says {printed[0]} "
                f"({printed[1]})"
            )
        rows[method], stated[method] = summed, summed_total

    missing = [k for k in (*COUNTED_METHODS, TOTAL_ROW) if k not in rows]
    if missing:
        raise SchemaDrift(f"DE: the report is missing rows {missing}")
    return rows, stated, by_party


def _keep_party(by_party: dict[str, dict[str, list[int]]],
                party_stated: dict[tuple[str, str], int],
                method: str, bucket: str, counts: list[int], total: int) -> None:
    """Record one (method, party) row, checking its own arithmetic first.

    Every row in the 2022 report prints its three counties AND their total, so
    each one carries its own proof that the columns were sliced where Delaware
    put them. Checking it here localises a mis-slice to the row that caused it;
    `_check_arithmetic` would only see the aggregate come out wrong.
    """
    if sum(counts) != total:
        raise SchemaDrift(
            f"DE: the {method!r}/{bucket!r} row is {counts}, summing to "
            f"{sum(counts)}, but its own Total column says {total}"
        )
    if bucket in by_party.get(method, {}):
        raise SchemaDrift(f"DE: two {bucket!r} rows for {method!r} in one report")
    by_party.setdefault(method, {})[bucket] = counts
    party_stated[(method, bucket)] = total


def _method_and_party(label: str) -> tuple[str, str] | None:
    """Split "absentee democratic" into its method key and its party bucket.

    None when the label names no method we know -- the caller raises, so that
    the message can quote Delaware's own capitalisation rather than the
    lowercased key we matched on. An unrecognised PARTY under a method we DO
    know still raises here, because that is drift in a row we understood.
    """
    for name, key in METHOD_ROWS.items():
        if label.startswith(f"{name} "):
            return key, _party_of(label[len(name):])
    return None


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
    """One statewide row and three county rows for the report's own as-of date.

    Plus, for a report that carries the party column, the party-by-method
    crosstab as `MethodDay` rows -- see `schema.MethodDay` for why that is its
    own table. The 2024 report has no party column, so it produces none of them
    and blank party fields, which is "not reported" and not "nobody voted".
    """
    result = FetchResult()
    mail = report.statewide("mail_returned")
    inperson = report.statewide("inperson")
    result.state_rows.append(StateDay(
        cycle=cycle, state="DE", day=report.as_of,
        # NOT Delaware's own Total, which includes the polling place. See the
        # module docstring.
        ballots_total=_total(mail, inperson),
        mail_returned=mail,
        inperson=inperson,
        # Delaware registers by party. The 2024 report does not break it out and
        # leaves these blank; the 2022 one does, and `party_npa` stays blank
        # even there -- see PUBLISHED_PARTIES.
        party_dem=report.party_early(PARTY_DEM),
        party_rep=report.party_early(PARTY_REP),
        party_oth=report.party_early(PARTY_OTH),
        party_npa=None,
    ))
    method_rows: list[MethodDay] = []
    for column, (fips, name) in enumerate(report.counties):
        county_mail = report.value("mail_returned", column)
        county_inperson = report.value("inperson", column)
        result.county_rows.append(CountyDay(
            cycle=cycle, state="DE", county_fips=fips, county_name=name,
            day=report.as_of,
            ballots_total=_total(county_mail, county_inperson),
            mail_returned=county_mail,
            inperson=county_inperson,
            party_dem=report.party_early(PARTY_DEM, column),
            party_rep=report.party_early(PARTY_REP, column),
            party_oth=report.party_early(PARTY_OTH, column),
            party_npa=None,
        ))
        for key, canonical in CROSSTAB_METHODS.items():
            if key not in report.by_party:
                continue
            method_rows.append(MethodDay(
                cycle=cycle, state="DE", county_fips=fips, county_name=name,
                day=report.as_of, method=canonical,
                ballots_total=report.value(key, column),
                party_dem=report.party(key, PARTY_DEM, column),
                party_rep=report.party(key, PARTY_REP, column),
                party_oth=report.party(key, PARTY_OTH, column),
                party_npa=None,
            ))
    return _methods.attach(result, method_rows)


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
#: RE-RUN 2026-09-08: 7 distinct digests now, collapsing to SIX report dates --
#: 10-28, 10-29, 11-01, 11-03, 11-04 and 11-05 -- which is the whole Delaware
#: 2024 curve there will ever be. Delaware overwrites the file on business
#: mornings, so the curve is as dense as the Internet Archive happened to be.
MAX_ARCHIVE_PROBES = 40


def _archive_stamps(url: str, cycle: int) -> list[str]:
    """Wayback timestamps of every distinct version of `url`, oldest first.

    Sorted here rather than trusted from the CDX API, because `fetch_history`
    resolves two captures of one report date by letting the later one win and
    that is only true if these arrive in order. The cache filename carries the
    cycle for the same kind of reason: every cycle asks the same endpoint, and
    one filename for all of them makes the saved copies overwrite each other.
    """
    query = {
        "url": url, "output": "json", "fl": "timestamp,statuscode,digest",
        "filter": "statuscode:200", "collapse": "digest", "limit": str(MAX_ARCHIVE_PROBES),
    }
    try:
        body = get(CDX_URL, state="DE", filename=f"cdx-GE{cycle}-report.json",
                   params=query, min_bytes=2)
    except Missing:
        # The CDX API answers "nothing archived" with an empty body, which
        # _net.get reports as Missing. That is absence, not a fault.
        return []
    try:
        rows = json.loads(body.decode("utf-8", errors="replace"))
    except ValueError as exc:
        raise SourceError(f"DE: the Wayback CDX index was not JSON: {exc}") from exc
    return sorted(str(row[0]) for row in rows[1:])


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
        literal = report_url(cycle)
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

        The URL is `report_url(cycle)`, the same call `fetch` makes, because
        2022 lives in a different directory under a different filename and a
        backfill that hard-coded the 2024 shape would silently find nothing for
        it -- which is exactly how this cycle came to be written off twice.
        """
        if cycle < FIRST_CYCLE:
            raise NotYetPublished(
                f"DE: no voting-method report exists for {cycle}; Delaware's "
                f"first general with early voting is {FIRST_CYCLE}"
            )
        url = report_url(cycle)
        today = _today()
        by_day: dict[date, Report] = {}
        for stamp in _archive_stamps(url, cycle):
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
                # A capture of some OTHER election served at this URL. Absence.
                continue
            except SchemaDrift:
                # ⚠️ NEVER SWALLOWED, and it is listed before the SourceError
                # arm on purpose -- SchemaDrift IS a SourceError, so catching
                # the parent first would quietly drop the one condition this
                # module exists to shout about. A report shape we do not
                # understand, or a report date outside the cycle, makes every
                # capture suspect and stops the backfill.
                raise
            except SourceError as exc:
                # Bytes we cannot read at all: the Wayback Machine occasionally
                # answers a capture with an error page under HTTP 200. Same
                # class as the download failure above and skipped for the same
                # reason -- one unusable capture must not cost the other six.
                log.debug("DE: archived capture %s unreadable (%s)", stamp, exc)
                continue
            if report.as_of > today:
                # ⚠️ GUARD PARITY with `fetch`, which refuses a report dated
                # after the day it was asked for. `_check_window` only bounds
                # the date to the CYCLE, so for a backfill of the running cycle
                # it would happily accept a report stamped weeks ahead and
                # publish a row at a days-to-election this election has not
                # reached -- a future point that then reads as the curve's end.
                # Skipped rather than raised, for the same reason a capture of
                # another election is: one bad capture must not cost the rest.
                log.warning("DE: archived capture %s is dated %s, in the future",
                            stamp, report.as_of.isoformat())
                continue
            by_day[report.as_of] = report
        if not by_day:
            raise NotYetPublished(f"DE: nothing archived for the {cycle} general")
        result = FetchResult()
        for day in sorted(by_day):
            one = to_result(by_day[day], cycle)
            result.extend(one)
            # `FetchResult.extend` merges the three FIELDS; method rows ride as
            # an attribute, so without this the whole curve would keep only the
            # last day's crosstab. Same trap, same fix, as fl.py's.
            _methods.extend(result, _methods.rows_of(one))
        return result
