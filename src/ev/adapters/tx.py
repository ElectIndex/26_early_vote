"""Texas: the Secretary of State's cumulative early-voting turnout report.

The SoS runs a small Struts app that renders, for one election and one early
voting day, a table of counties with their cumulative in-person and by-mail
counts and a TOTAL row. Getting to it is three GETs, because none of the ids are
guessable: the election list gives the cycle's election id, that id gives the
list of early voting dates, and an id plus a date gives the table.

**The TOTAL row is a sum of the counties printed above it, and nothing more.**
That is the fact this whole module is arranged around. In 2020, 2022 and 2024 the
report listed all 254 counties, and the TOTAL matched our own sum of them to the
ballot -- so it is a genuine Texas total and we publish it. But the SoS has not
always printed every county: the report historically covered only the largest
counties, and a TOTAL over a subset is a number that looks exactly like a Texas
turnout figure and is not one. There is no column in `StateDay` that can say
"this covers 30 counties of 254", and a partial number published there would be
read as Texas by every consumer downstream.

So coverage is checked against the census county list on every parse, and when
it is short the county rows are published and NO StateDay is emitted at all. A
gap in the statewide series is recoverable and visibly a gap; a plausible wrong
total is neither. (Note what the alternative would cost: a `StateDay` carrying
None everywhere is worse than nothing, because publish.py keeps the better tier
on a key collision, so an empty tier-1 row would suppress the aggregator's real
statewide number rather than merely be missing.)

Two smaller things:

* `ballots_new` stays None even though the report has a daily column. That column
  ("# In Person On 10/21/2024") counts in-person ballots only, so using it as the
  day's new ballots would silently drop every mail ballot that arrived.
* Columns are matched BY HEADER NAME. The 2024 layout has a ninth column the
  2022 layout does not (a per-county "Voter Details Report" link), and the daily
  column's header carries the report date, which is where the as-of date is read
  from.

Texas does not register voters by party, so every party_* field is None. See THE
BLANK RULE in schema.py.
"""

from __future__ import annotations

import html
import logging
import re
from datetime import date

from ..schema import TIER_SCRAPER, CountyDay, StateDay
from . import _fips
from ._net import Missing, get, looks_like_html
from .base import Adapter, FetchResult, NotYetPublished, SchemaDrift, SourceError

log = logging.getLogger(__name__)

#: The SoS's early-voting app. VERIFIED live: all three endpoints answer a plain
#: GET with the same body the site's own POST form produces.
BASE = "https://earlyvoting.texas-election.com/Elections"
ELECTIONS_URL = f"{BASE}/getElectionDetails.do"
EV_DATES_URL = f"{BASE}/getElectionEVDates.do"
EV_DETAIL_URL = f"{BASE}/getEVDetails.do"

#: The one header we override. The SoS sits behind a WAF that 403s any
#: User-Agent carrying the word "bot" -- which the shared one in _net.py
#: deliberately does, so that state sites can identify us. This is that same
#: string with the identifying suffix removed, and it is the difference between
#: HTTP 200 and HTTP 403 on every request below.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
    ),
}

#: Words that rule an election out of being the cycle's November general.
NOT_THE_GENERAL = (
    "PRIMARY", "RUNOFF", "SPECIAL", "LOCAL", "CONSTITUTIONAL", "AMEND",
)

COUNTY = "county"
INPERSON = "cumulative in-person voters"
BY_MAIL = "cumulative by mail voters"
BOTH = "cumulative in-person and mail voters"
#: Prefix of the daily column, whose header carries the report's as-of date.
DAILY_PREFIX = "# in person on"

REQUIRED = (COUNTY, INPERSON, BY_MAIL, BOTH)

TOTAL_LABELS = {"total", "totals", "statewide", "state total", "grand total"}

#: Every Texas county. A report listing fewer is a partial report -- see the
#: module docstring for what that costs and what we do about it.
EXPECTED_COUNTIES = len(_fips.CENSUS_COUNTIES["TX"])

_TABLE = re.compile(r"<table[^>]*custom-table.*?</table>", re.S)
_ROW = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
_CELL = re.compile(r"<t[hd][^>]*>(.*?)</t[hd]>", re.S)
_TAG = re.compile(r"<[^>]+>")
_COMMENT = re.compile(r"<!--.*?-->", re.S)
_OPTION = re.compile(r'<option[^>]*value="([^"]*)"[^>]*>(.*?)</option>', re.S)
_DATE_IN_HEADER = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})")


def _text(fragment: str) -> str:
    return " ".join(html.unescape(_TAG.sub(" ", _COMMENT.sub("", fragment))).split())


def _int(value: str) -> int | None:
    """A count, or None when the cell is blank -- unreported, not zero."""
    text = value.strip().replace(",", "")
    if not text or text in {"-", "--", "N/A", "n/a"}:
        return None
    try:
        return int(float(text))
    except ValueError as exc:
        raise SchemaDrift(f"TX: {value!r} is not a count") from exc


def _select(markup: str, name: str) -> str:
    """The one <select> with this id, so sibling dropdowns cannot leak in."""
    match = re.search(
        rf'<select[^>]*id="{re.escape(name)}"[^>]*>(.*?)</select>', markup, re.S
    )
    if match is None:
        raise SchemaDrift(f"TX: page has no {name!r} dropdown")
    return match.group(1)


def elections(markup: str) -> list[tuple[str, str]]:
    """(id, name) for every election in the SoS's dropdown."""
    return [
        (value.strip(), _text(label).upper())
        for value, label in _OPTION.findall(_select(markup, "idElection"))
        if value.strip().isdigit()
    ]


def general_election_id(markup: str, cycle: int) -> str:
    """The id of the cycle's November general, or NotYetPublished."""
    matches = [
        (value, name) for value, name in elections(markup)
        if name.startswith(str(cycle))
        and "GENERAL" in name and "NOVEMBER" in name
        and not any(word in name for word in NOT_THE_GENERAL)
    ]
    if not matches:
        raise NotYetPublished(
            f"TX: the SoS has not listed a {cycle} November general yet"
        )
    if len(matches) > 1:
        raise SchemaDrift(f"TX: {cycle} matches {len(matches)} generals {matches}")
    return matches[0][0]


def ev_dates(markup: str) -> list[str]:
    """Every early-voting date the SoS offers, as its own opaque date strings."""
    return [
        value.strip()
        for value, _ in _OPTION.findall(_select(markup, "selectedDate"))
        if value.strip()
    ]


def ev_date(value: str) -> date:
    """"2024-10-21 00:00:00.0" -> date(2024, 10, 21)."""
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError as exc:
        raise SchemaDrift(f"TX: {value!r} is not an early-voting date") from exc


def report_date(header: list[str], cycle: int) -> date:
    """The as-of date the SoS writes into the daily column's header."""
    for name in header:
        if not name.startswith(DAILY_PREFIX):
            continue
        match = _DATE_IN_HEADER.search(name)
        if match is None:
            break
        month, day, year = (int(g) for g in match.groups())
        if year != int(cycle):
            raise SchemaDrift(
                f"TX: report header {name!r} is from {year}, not the {cycle} cycle"
            )
        try:
            return date(year, month, day)
        except ValueError as exc:
            raise SchemaDrift(f"TX: report header {name!r} is not a date") from exc
    raise SchemaDrift(f"TX: report has no {DAILY_PREFIX!r} column to date it")


def parse(markup: str, cycle: int) -> FetchResult:
    """Parse one early-voting turnout page into canonical rows."""
    table = _TABLE.search(markup)
    if table is None:
        raise SchemaDrift("TX: page carries no early-voting table")
    rows = _ROW.findall(table.group(0))
    if not rows:
        raise SchemaDrift("TX: early-voting table is empty")

    header = [_text(c).lower() for c in _CELL.findall(rows[0])]
    index = {name: i for i, name in enumerate(header) if name}
    missing = [c for c in REQUIRED if c not in index]
    if missing:
        raise SchemaDrift(f"TX: early-voting table is missing columns {missing}")
    day = report_date(header, cycle)

    def cell(cells: list[str], column: str) -> int | None:
        i = index[column]
        return _int(cells[i]) if i < len(cells) else None

    total_row: list[str] | None = None
    county_rows: list[CountyDay] = []
    unknown: list[str] = []

    for row in rows[1:]:
        cells = [_text(c) for c in _CELL.findall(row)]
        if len(cells) <= index[COUNTY]:
            continue
        name = cells[index[COUNTY]]
        if not name:
            continue
        if name.lower() in TOTAL_LABELS:
            total_row = cells
            continue

        hit = _fips.lookup("TX", name)
        if hit is None:
            unknown.append(name)
            continue
        fips, canonical = hit
        county_rows.append(CountyDay(
            cycle=cycle, state="TX", county_fips=fips, day=day,
            county_name=canonical,
            ballots_total=cell(cells, BOTH),
            # The daily column counts in-person ballots only, so it cannot
            # stand in for the day's new ballots. See the module docstring.
            ballots_new=None,
            mail_returned=cell(cells, BY_MAIL),
            inperson=cell(cells, INPERSON),
            # Texas has no party registration. Never 0.
            party_dem=None, party_rep=None, party_oth=None, party_npa=None,
        ))

    if unknown:
        raise SchemaDrift(f"TX: unrecognised county names {sorted(set(unknown))[:5]}")
    if not county_rows:
        raise SchemaDrift("TX: early-voting table produced no county rows")

    state_rows: list[StateDay] = []
    covered = len({r.county_fips for r in county_rows})
    if total_row is not None and covered == EXPECTED_COUNTIES:
        state_rows.append(StateDay(
            cycle=cycle, state="TX", day=day,
            ballots_total=cell(total_row, BOTH),
            ballots_new=None,
            # The report never says how many mail ballots were sent out.
            mail_requested=None,
            mail_returned=cell(total_row, BY_MAIL),
            inperson=cell(total_row, INPERSON),
            party_dem=None, party_rep=None, party_oth=None, party_npa=None,
        ))
    else:
        # PARTIAL COVERAGE. The TOTAL row sums only the counties printed above
        # it, so publishing it as Texas would overstate nothing and understate
        # everything -- which is worse than a hole, because it looks right.
        log.warning(
            "TX: report covers %d of %d counties; publishing counties only, no "
            "statewide row (its TOTAL would be a partial total)",
            covered, EXPECTED_COUNTIES,
        )

    return FetchResult(state_rows=state_rows, county_rows=county_rows)


class TXScraper(Adapter):
    """Tier 1 for Texas: the SoS cumulative early-voting turnout report."""

    state = "TX"
    name = "tx-sos"
    tier = TIER_SCRAPER

    def _page(self, url: str, *, filename: str, params: dict | None = None,
              use_cache: bool = False) -> str:
        try:
            body = get(
                url, state="TX", filename=filename, params=params,
                headers=HEADERS, use_cache=use_cache, min_bytes=2048,
            )
        except Missing as exc:
            raise NotYetPublished(f"TX: {exc}") from exc
        if not looks_like_html(body):
            raise SourceError(f"TX: {url} did not return a page")
        return body.decode("utf-8", errors="replace")

    def _election_id(self, cycle: int, *, use_cache: bool) -> str:
        return general_election_id(
            self._page(ELECTIONS_URL, filename=f"{cycle}_elections.html",
                       use_cache=use_cache),
            cycle,
        )

    def _dates(self, cycle: int, election_id: str, *, use_cache: bool) -> list[str]:
        markup = self._page(
            EV_DATES_URL, filename=f"{cycle}_ev_dates.html",
            params={"idElection": election_id, "webPageSyncDate": "true"},
            use_cache=use_cache,
        )
        dates = ev_dates(markup)
        if not dates:
            raise NotYetPublished(f"TX: no early-voting dates posted for {cycle} yet")
        return dates

    def _report(self, cycle: int, election_id: str, selected: str, *,
                use_cache: bool) -> FetchResult:
        markup = self._page(
            EV_DETAIL_URL,
            filename=f"{cycle}_ev_{ev_date(selected).isoformat()}.html",
            params={
                "idElection": election_id,
                "selectedDate": selected,
                "earlyVoteFlag": "true",
            },
            use_cache=use_cache,
        )
        return parse(markup, cycle)

    def fetch(self, cycle: int, as_of: date) -> FetchResult:
        election_id = self._election_id(cycle, use_cache=False)
        posted = [d for d in self._dates(cycle, election_id, use_cache=False)
                  if ev_date(d) <= as_of]
        if not posted:
            raise NotYetPublished(
                f"TX: early voting has not opened for {cycle} as of {as_of.isoformat()}"
            )
        latest = max(posted, key=ev_date)
        return self._report(cycle, election_id, latest, use_cache=False)

    def fetch_history(self, cycle: int) -> FetchResult:
        """Every early-voting day of a past cycle, as a real daily series.

        Texas is the one state in this batch whose own site still serves each
        day's report separately after the election, so a backfill recovers the
        whole curve rather than a single certified snapshot.
        """
        if cycle >= date.today().year:
            raise NotYetPublished(f"TX: {cycle} is not an archived cycle")
        election_id = self._election_id(cycle, use_cache=True)
        result = FetchResult()
        for selected in self._dates(cycle, election_id, use_cache=True):
            try:
                result.extend(self._report(cycle, election_id, selected, use_cache=True))
            except SourceError as exc:
                log.warning("TX: %s unusable (%s)", selected, exc)
        if not result:
            raise NotYetPublished(f"TX: no usable archived reports for {cycle}")
        return result
