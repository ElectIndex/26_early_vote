"""Illinois: the State Board of Elections' Pre-Election Counts export.

`elections.il.gov/VotingAndRegistrationSystems/PreElectionCounts.aspx` says of
itself: *"These numbers will fluctuate daily."* Pick an election from its
dropdown and it offers the same table as CSV, PDF, XLS and tab-delimited text.
The CSV is one row per election authority:

```
JID,Name,ElectionDate,By-Mail,By-Mail Returned,Early,Grace
,STATEWIDE COUNTS,11/3/2026 0:00,947926,0,0,0
1,Adams County,11/3/2026 0:00,3952,0,0,0
```

Three things drive the code below.

* **The unit is 108 ELECTION AUTHORITIES, not 102 counties.** Six Illinois cities
  run their own election board separately from the county around them -- Chicago,
  Bloomington, Danville, East St. Louis, Galesburg and Rockford -- so each of
  those six county FIPS is TWO rows that must be summed. Getting Chicago wrong
  would mis-state Cook County, which is 40% of the state's mail ballots. The
  crosswalk is `CITY_BOARDS` below, it has exactly six entries, and a name in
  neither it nor `_fips` raises SchemaDrift rather than being dropped.

* **The general election, never the primary.** The dropdown carries both -- today
  it offers "2026 General Election" and "2026 General Primary" -- so the option is
  chosen by its exact label and then the file's own `ElectionDate` column is
  checked against `calendar.election_date(cycle)`. Both must agree. Illinois'
  March primary file is fully populated (856,815 mail requests, 430,000 early
  votes) and publishing it as the general's would be a confident, plausible,
  completely wrong headline.

* **Illinois does NOT register voters by party.** A voter picks a party's ballot
  at the primary; nothing is carried on the registration. So all four party
  fields are None on every row -- never 0. See THE BLANK RULE in schema.py.

`Grace` is grace-period registration-and-voting: registering and voting in the
same in-person transaction at the election authority, which Illinois counts
separately from `Early` rather than inside it (in the March 2026 primary, 4,550
against 430,000). Both are ballots cast in person before Election Day, so
`inperson` is their sum.

The page has no archive: its dropdown lists only the current cycle's elections.
The export is generated on demand and carries no as-of date of its own, so a
run's rows are dated `as_of`.

## Why there is no 2024 or 2022 county series, and how hard that was looked for

Florida's PublicStats page has the same "snapshot, overwritten in place" shape
and its 2022 and 2024 county curves came straight out of the Wayback Machine
(see `fl.py`). The same sweep against Illinois finds nothing, and the reason is
structural rather than bad luck: **the counts live behind an ASP.NET postback,
and a crawler only ever sees the page before the button is pressed.**

VERIFIED 2026-09-06 from this network, via the CDX API and by fetching captures:

* `elections.il.gov/VotingAndRegistrationSystems/PreElectionCounts.aspx` has
  **21 distinct captures of the bare URL in its whole history**, of which
  exactly **two are 200s in 2024** (2024-11-05 15:46 and 2024-11-06 04:42 UTC)
  and **one in 2022** (2022-03-20). Counting its `?MID=...&T=...` variants
  brings 2024 to thirteen 200s. Five of those were fetched, spanning 2024-11-01
  to 2024-11-18, plus the 2022 one: every single one carries the dropdown with
  `<option selected>Please Select an Election</option>`, **zero `<table>`
  elements and zero `NewDocDisplay.aspx` links**. The archive holds the form,
  never the answer.
* `elections.il.gov/NewDocDisplay.aspx?<token>` — the export links themselves —
  has **149 distinct 2024 captures**, none of them these counts: every one is
  `application/pdf` or `application/octet-stream` from the campaign-disclosure
  and voting-equipment trees. The three whose encrypted token shares the longest
  prefix with the 2026 counts export were downloaded and read; they are the
  Vote-By-Mail FAQ and two voting-equipment inventories. A crawler could not
  have reached the counts export, because no archived page ever linked it.
* **No archived `text/csv` or `text/plain` under any `Counts/` path in 2024 or
  2022** except `Counts/Registration/Active and Inactive totals.txt`, which is
  registration, not voting.
* There *was* a static path once:
  `DocDisplay.aspx?Doc=Downloads/VotingAndRegistrationSystems/Counts/PreElection/
  Pre-election Ballot Requests.pdf` is archived **once, 2021-11-02**, and it
  holds exactly this file's columns for the 2021 Consolidated Election
  (`JID / Name / ElectionDate / By-Mail / By-Mail Returned / Early / Grace`,
  STATEWIDE COUNTS 186,724 / 145,952 / 189,380 / 3,660). It has no 2022 or 2024
  capture, and today it serves an HTML not-found page rather than a PDF. Every
  extension of the bare download path — `.csv`, `.txt`, `.pdf`, `.xlsx` under
  `/Downloads/VotingAndRegistrationSystems/Counts/PreElection/` — returns **404
  live today**, so there is no static file to walk backwards either.

`fetch_history` therefore refuses, loudly and by name, rather than inheriting a
generic "no archive" from the base class: the refusal is a finding, and if
Illinois ever restores the static path the failing test is the reminder.

RE-VERIFIED 2026-09-08, three checks, all agreeing with the above:

* The dropdown live today offers exactly `Please Select an Election` (0),
  `2026 General Election` (78) and `2026 General Primary` (79). No 2022, no
  2024 -- the SBE really does purge past elections, so there is no postback that
  could reach them even with a browser driving it.
* `Downloads/.../Counts/PreElection/Pre-election Ballot Requests.pdf` still
  returns 404 live, and the CDX index holds FOUR captures under that whole
  directory -- `.csv`, `.pdf`, `.txt` and `.xlsx` -- **every one of them an
  archived 404**. Nothing was ever served there in 2022 or 2024 for a crawler
  to have kept.
* `PreElectionCounts.aspx` is still at 21 distinct captures, unchanged.

RE-VERIFIED AGAIN 2026-09-08, and this time the query itself was widened, which
is the check the two sweeps above did NOT run. Both of them were scoped: to one
page, to one download endpoint, to one `Counts/` directory. On the same day, a
scoped sweep of exactly that kind was overturned for Delaware -- a CDX listing of
one directory said "no 2022 report" and the file was sitting in a different tree
under a different name -- so the scoping is the thing that had to be tested, not
the conclusion.

**A prefix sweep can only prove something about the prefix. So the whole domain
was swept and the urlkeys grepped offline:**

* `url=elections.il.gov&matchType=domain&from=20240901&to=20250228&collapse=urlkey`
  returns **7,124 distinct urlkeys** across `elections.il.gov` and its four
  subdomains (`ova`, `berep`, `idis3`, `move`). Grepping them for `count`,
  `precinct`, `pre-election`, `ballot request`, `vbm`, `vote by mail`,
  `early vot`, `absentee` and `grace` leaves, under any `Counts/` path,
  **exactly four things**: `PreElectionCounts.aspx` and `RegistrationCounts.aspx`
  (the forms), `Counts/Registration/Active and Inactive totals.{csv,txt}`
  (registration, not voting), and
  `Counts/PreElection/Pre-election Ballot Requests.txt` -- captured as
  `text/html`, i.e. the archived 404 already described above.
* The same query for `from=20220901&to=20230228` returns **355 distinct
  urlkeys** and nine candidates, every one of them a static explainer or a
  locations search: `ElectionInformation/pdf/earlyvoting.pdf`,
  `votebymail.pdf`, `graceperiodreg.pdf`, `EarlyVotingLocationsSearch.aspx`.
  No counts file of any kind.
* The live dropdown, read again the same day, still offers only
  `Please Select an Election`, `2026 General Election` and `2026 General
  Primary`.

So the answer is the same and now it is the answer to a much bigger question:
across everything the Internet Archive holds of this whole domain in both
windows, the pre-election counts exist only as the form. The refusal is not
stale and it is not narrow. It is structural: an ASP.NET postback page archives
as the form and never as the answer, and that cannot change retroactively.

CHECKED AGAIN 2026-09-09, AND THIS TIME OFF elections.il.gov ENTIRELY
--------------------------------------------------------------------
Every sweep above is about ONE domain. That is still a scope, and the question
it cannot answer is whether somebody ELSE kept Illinois' 2024 numbers. So this
pass asked that instead, and the answer is a harder negative than the first one:

* **The party who would have mirrored it did not track Illinois at all.** The
  UF Election Lab's own page for this state and cycle --
  `election.lab.ufl.edu/early-vote/2024-early-voting/2024-general-election-
  early-vote-illinois/` (HTTP 200, 36,958 bytes, read from this network) --
  prints "No Data" against all six of Total Early Votes, In-Person Early Votes,
  Mail Ballots Returned, Mail Ballots Requested, Last Updated and Source, and
  then says in words: *"Detailed data are not available in this state."* There
  is no `Early-Vote-2024G` repository under `github.com/ElectProject` (it holds
  `Early-Vote-2020G`, `VEST_Test`, `Early-Vote-2026P` and nothing else), and
  even the 2020 tracker's `docs/IL.html` says "County reports have not been
  updated". Illinois has never been in that dataset at county level.
* **Illinois' own 2024 canvass carries no method dimension.** Unlike
  PreElectionCounts, `elections.il.gov/ElectionOperations/ElectionVoteTotals
  .aspx` DOES still offer "2024 GENERAL ELECTION" (option value 66), and its
  postback yields eighteen direct CSVs under
  `/Downloads/ElectionOperations/ElectionResults/ByOffice/66/`. The presidential
  one is 7,247,259 bytes / 59,624 lines and its header is
  `JurisdictionID, JurisContainerID, JurisName, EISCandidateID, CandidateName,
  EISContestID, ContestName, PrecinctName, Registration, EISPartyID, PartyName,
  VoteCount` -- precinct x candidate, with zero occurrences of "early",
  "absentee", "grace" or "provisional" anywhere in the results tree. So the
  official canvass cannot be turned into an early-vote series either, and
  neither can anything built on top of it (MEDSL's county file inherits this:
  Illinois' `mode` can only ever be TOTAL).
* **Every county-run site is a single jurisdiction and holds nothing anyway.**
  `matchType=domain` CDX sweeps for 2024-09-01..2025-03-01, grepped offline:
  chicagoelections.gov 1,991 urlkeys (an `/outstanding-ballot-count` page,
  Chicago only), chicagoelections.com 255, cookcountyclerkil.gov 3,306,
  cookcountyclerk.com 446, dupagecounty.gov 1,753, dupageresults.gov 161,
  lakecountyil.gov 24,196, willcountyclerk.gov 373 -- no per-county counts file
  in any of them. `elections.cookcountyil.gov` and `kanecountyclerk.gov` are not
  in the Archive at all in that window.
* **A near-miss worth writing down.** `illinoiselectiondata.com/analysis/
  earlyvote.php` (HTTP 200, 103,247 bytes) publishes exactly the table this
  adapter wants -- per election authority, `Mail Bal Req | Mail Bal Ret | Mail
  Bal Counted | EV BC | EV Bal Counted | Grace Per BC | Grace Per Counted | ...`
  -- but only for the **2016 and 2014** generals. Grepping its HTML for 2018,
  2020, 2022, 2024 and 2026 returns zero hits each. It is a live route to a 2016
  Illinois county backfill and no route at all to 2024.

WHAT DOES EXIST FOR 2024, AND WHY IT IS NOT WIRED HERE
------------------------------------------------------
There IS a clean per-authority 2024 FINAL: the EAC's Election Administration
and Voting Survey. VERIFIED 2026-09-09 by downloading and reading it here --
`https://www.eac.gov/sites/default/files/2026-02/2024_EAVS_for_Public_Release
_nolabel_V2_csv.zip` (HTTP 200, 2,119,187 bytes) unzips to a 41,239,612-byte
CSV of 6,461 rows x 535 columns, of which **exactly 108 are Illinois** -- the
102 counties plus `BLOOMINGTON CITY`, `CHICAGO CITY`, `DANVILLE CITY`,
`EAST ST. LOUIS CITY`, `GALESBURG CITY`, `ROCKFORD CITY`, a one-for-one match
with CITY_BOARDS above. Summed over those 108 rows: `C1a` mail transmitted
1,177,760, `C1b` mail returned 1,030,362, `F1d` mail votes counted 1,016,208,
`F1f` in-person early 2,001,203, `F1a` total voters 5,717,147. One sentinel in
the lot (Fulton County's `F1f` is -99).

It is deliberately NOT wired into this adapter, for three reasons:

1. **Provenance.** These rows would be stamped `il-sbe`, tier 1, and they are
   not the Illinois SBE's -- they are a federal survey of it. `ladder.py` stamps
   provenance from the adapter, so the only honest way to publish EAVS is as its
   own source, and EAVS is national: that is a repo-wide decision about a new
   tier, not an Illinois patch.
2. **It measures something else.** `By-Mail Returned` in this file is ballots
   back before Election Day; EAVS `C1b` includes everything that arrived through
   the grace period, and `F1d`/`F1f` are ballots COUNTED, not cast-by-a-date.
   Using an EAVS total as the denominator for a share whose numerator comes from
   PreElectionCounts silently divides two different quantities.
3. **There is no `Grace` column**, so Illinois' grace-period voting -- which
   this adapter reports separately and folds into `inperson` -- cannot be
   separated back out of `F1f`/`F1b`.

The URLs and column names are here so that decision can be made with the
evidence rather than re-gathered.
"""

from __future__ import annotations

import csv
import html as _html
import io
import logging
import re
from datetime import date, datetime

import requests

from ..calendar import election_date
from ..schema import TIER_SCRAPER, CountyDay, StateDay
from . import _fips, _net
from .base import Adapter, FetchResult, NotYetPublished, SchemaDrift, SourceError

log = logging.getLogger(__name__)

#: VERIFIED on this network 2026-09-06: the page returns 200 with a dropdown
#: offering "2026 General Election" (value 78) and "2026 General Primary" (79),
#: and an ASP.NET postback selecting either one yields four working
#: `NewDocDisplay.aspx?<token>` links. The CSV behind the general's is 4,786
#: bytes / 109 rows; behind the primary's, 5,486 bytes / 109 rows.
BASE = "https://elections.il.gov/"
INDEX = f"{BASE}VotingAndRegistrationSystems/PreElectionCounts.aspx"

#: The dropdown label for a cycle's general election, matched exactly (after
#: whitespace collapsing). "2026 General Primary" differs by one word and must
#: never match. Illinois has used this wording for both 2026 elections.
GENERAL_LABEL = "{cycle} general election"

#: Which of the four offered formats to take.
DOWNLOAD_LABEL = "ascii comma delimited"

#: The dropdown's control id, and the hidden fields ASP.NET requires echoed back.
SELECT_NAME = "ctl00$ContentPlaceHolder1$ddlElection"
SELECT_ID = "ContentPlaceHolder1_ddlElection"
HIDDEN = ("__VIEWSTATE", "__VIEWSTATEGENERATOR", "__EVENTVALIDATION")

HEADER = ("JID", "Name", "ElectionDate",
          "By-Mail", "By-Mail Returned", "Early", "Grace")

STATEWIDE = "statewide counts"

#: The six municipal election boards that run elections separately from the
#: county surrounding them, mapped to that county. Their rows must be ADDED to
#: the county's own row, never used in place of it: the "Cook County" row is
#: suburban Cook only, and Chicago is the other 40% of the state's mail ballots.
CITY_BOARDS = {
    "city of bloomington": "McLean County",
    "city of chicago": "Cook County",
    "city of danville": "Vermilion County",
    "city of east st louis": "St. Clair County",
    "city of galesburg": "Knox County",
    "city of rockford": "Winnebago County",
}

_OPTION = re.compile(r'<option[^>]*\bvalue="([^"]*)"[^>]*>(.*?)</option>', re.I | re.S)
_SELECT = re.compile(
    r'<select[^>]*\bid="%s".*?</select>' % SELECT_ID, re.I | re.S)
_HIDDEN_INPUT = r'<input[^>]*\bid="{name}"[^>]*\bvalue="([^"]*)"'
_LINK = re.compile(
    r'<a[^>]*\bhref="([^"]*NewDocDisplay\.aspx\?[^"]+)"[^>]*>(.*?)</a>', re.I | re.S)
_TAGS = re.compile(r"<[^>]+>")


def _text(raw: str) -> str:
    return " ".join(_html.unescape(_TAGS.sub(" ", raw or "")).split())


def _norm(raw) -> str:
    return " ".join(str(raw or "").split()).lower()


def hidden_fields(page: str) -> dict[str, str]:
    """The ASP.NET postback tokens the form will not work without."""
    fields: dict[str, str] = {}
    for name in HIDDEN:
        m = re.search(_HIDDEN_INPUT.format(name=name), page or "", re.I)
        if m is None:
            raise SourceError(f"IL: pre-election counts page has no {name}")
        fields[name] = _html.unescape(m.group(1))
    return fields


def election_option(page: str, cycle: int) -> str:
    """The dropdown value for `cycle`'s GENERAL ELECTION, or raise.

    An option whose label is not exactly "<cycle> General Election" is never
    used, because the one beside it is that cycle's General PRIMARY.
    """
    block = _SELECT.search(page or "")
    if block is None:
        raise SourceError("IL: pre-election counts page has no election dropdown")
    wanted = GENERAL_LABEL.format(cycle=cycle)
    offered = []
    for value, label in _OPTION.findall(block.group(0)):
        name = _norm(_text(label))
        offered.append(name)
        if name == wanted and value.strip():
            return value.strip()
    raise NotYetPublished(
        f"IL: the pre-election counts dropdown does not offer {wanted!r} "
        f"(it offers {offered})"
    )


def download_url(page: str, label: str = DOWNLOAD_LABEL) -> str:
    """The absolute URL of one of the four export links on the posted-back page."""
    for href, text in _LINK.findall(page or ""):
        if _norm(_text(text)) == label:
            return BASE + _html.unescape(href).lstrip("./")
    raise NotYetPublished(
        f"IL: no {label!r} download offered for this election yet")


def _count(raw: str | None) -> int | None:
    """A count. Illinois writes a literal 0 for "none yet", which is a real 0."""
    text = (raw or "").strip().replace(",", "")
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError as exc:
        raise SchemaDrift(f"IL: {raw!r} is not a count") from exc


def _add(*values: int | None) -> int | None:
    known = [v for v in values if v is not None]
    return sum(known) if known else None


class _Authority:
    """Running totals for one county FIPS, summed across its election boards."""

    __slots__ = ("mail_requested", "mail_returned", "early", "grace", "name")

    def __init__(self, name: str) -> None:
        self.name = name
        self.mail_requested = self.mail_returned = None
        self.early = self.grace = None

    def add(self, row: dict[str, str]) -> None:
        self.mail_requested = _add(self.mail_requested, _count(row["By-Mail"]))
        self.mail_returned = _add(self.mail_returned, _count(row["By-Mail Returned"]))
        self.early = _add(self.early, _count(row["Early"]))
        self.grace = _add(self.grace, _count(row["Grace"]))

    @property
    def inperson(self) -> int | None:
        return _add(self.early, self.grace)

    @property
    def total(self) -> int | None:
        return _add(self.mail_returned, self.inperson)


def parse(body: bytes, cycle: int, day: date) -> FetchResult:
    """Parse one Pre-Election Counts CSV into canonical rows."""
    text = body.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    if tuple(reader.fieldnames or ()) != HEADER:
        raise SchemaDrift(
            f"IL: pre-election counts header is {reader.fieldnames}, expected {list(HEADER)}")

    wanted = election_date(cycle)
    counties: dict[str, _Authority] = {}
    state: StateDay | None = None
    unknown: list[str] = []

    for row in reader:
        name = " ".join((row.get("Name") or "").split())
        if not name:
            continue

        # Belt and braces on the primary/general trap: the dropdown said this was
        # the general, and the file must say the same date.
        stamp = (row.get("ElectionDate") or "").split()[0]
        try:
            named = datetime.strptime(stamp, "%m/%d/%Y").date()
        except ValueError as exc:
            raise SchemaDrift(f"IL: {row.get('ElectionDate')!r} is not an election date") from exc
        if named != wanted:
            raise SchemaDrift(
                f"IL: {name} is dated {named.isoformat()}, not the {cycle} general "
                f"({wanted.isoformat()})")

        if _norm(name) == STATEWIDE:
            if state is not None:
                raise SchemaDrift("IL: two statewide rows in one export")
            bucket = _Authority(name)
            bucket.add(row)
            state = StateDay(
                cycle=cycle, state="IL", day=day,
                ballots_total=bucket.total,
                mail_requested=bucket.mail_requested,
                mail_returned=bucket.mail_returned,
                inperson=bucket.inperson,
                # Illinois does not register voters by party.
                party_dem=None, party_rep=None, party_npa=None, party_oth=None,
            )
            continue

        hit = _fips.lookup("IL", CITY_BOARDS.get(_norm(name), name))
        if hit is None:
            unknown.append(name)
            continue
        fips, canonical = hit
        counties.setdefault(fips, _Authority(canonical)).add(row)

    if state is None and not counties and not unknown:
        # A well-formed header with no rows under it is Illinois not having
        # generated the counts yet, which STOPS the ladder rather than letting a
        # weaker source invent a number for an election with no data.
        raise NotYetPublished(
            f"IL: the {cycle} pre-election counts export has no rows yet")
    if unknown:
        # Illinois has 102 counties plus six city boards and that has not changed
        # in decades; an unplaced authority is a whole jurisdiction going missing.
        raise SchemaDrift(f"IL: unrecognised election authorities {sorted(set(unknown))[:5]}")
    if state is None:
        raise SchemaDrift("IL: pre-election counts export has no STATEWIDE COUNTS row")
    if not counties:
        raise NotYetPublished(f"IL: the {cycle} pre-election counts export is empty")

    result = FetchResult(state_rows=[state])
    for fips in sorted(counties):
        bucket = counties[fips]
        result.county_rows.append(CountyDay(
            cycle=cycle, state="IL", county_fips=fips, day=day,
            county_name=bucket.name,
            ballots_total=bucket.total,
            mail_returned=bucket.mail_returned,
            inperson=bucket.inperson,
            party_dem=None, party_rep=None, party_npa=None, party_oth=None,
        ))
    return result


class ILScraper(Adapter):
    """Tier 1 for Illinois: the SBE's Pre-Election Counts CSV export."""

    state = "IL"
    name = "il-sbe"
    tier = TIER_SCRAPER

    # ------------------------------------------------------------------
    def _session(self) -> requests.Session:
        session = requests.Session()
        session.headers.update(_net.DEFAULT_HEADERS)
        return session

    def _get(self, session: requests.Session, url: str, filename: str) -> str:
        try:
            response = session.get(url, timeout=_net.DEFAULT_TIMEOUT)
        except requests.RequestException as exc:
            raise SourceError(f"IL: GET {url} failed: {exc}") from exc
        if not response.ok:
            raise SourceError(f"IL: {url} returned HTTP {response.status_code}")
        _net.cache_path("IL", filename).write_bytes(response.content)
        return response.text

    def _postback(self, session: requests.Session, page: str, value: str) -> str:
        form = {
            "__EVENTTARGET": SELECT_NAME,
            "__EVENTARGUMENT": "",
            "__LASTFOCUS": "",
            SELECT_NAME: value,
            "MenuTabContainer_ClientState": "",
            **hidden_fields(page),
        }
        try:
            response = session.post(INDEX, data=form, timeout=_net.DEFAULT_TIMEOUT)
        except requests.RequestException as exc:
            raise SourceError(f"IL: postback for election {value} failed: {exc}") from exc
        if not response.ok:
            raise SourceError(
                f"IL: postback for election {value} returned HTTP {response.status_code}")
        _net.cache_path("IL", f"counts_{value}.html").write_bytes(response.content)
        return response.text

    def _download(self, session: requests.Session, url: str, cycle: int) -> bytes:
        try:
            response = session.get(url, timeout=_net.DEFAULT_TIMEOUT)
        except requests.RequestException as exc:
            raise SourceError(f"IL: export download failed: {exc}") from exc
        if not response.ok:
            raise SourceError(f"IL: export download returned HTTP {response.status_code}")
        body = response.content
        if _net.looks_like_html(body):
            # The site answers an expired postback token with the page again.
            raise SourceError("IL: export download came back as HTML, not CSV")
        _net.cache_path("IL", f"pre_election_counts_{cycle}.csv").write_bytes(body)
        return body

    # ------------------------------------------------------------------
    def fetch(self, cycle: int, as_of: date) -> FetchResult:
        session = self._session()
        page = self._get(session, INDEX, "pre_election_counts.html")
        value = election_option(page, cycle)
        posted = self._postback(session, page, value)
        return parse(self._download(session, download_url(posted), cycle), cycle, as_of)

    def fetch_history(self, cycle: int) -> FetchResult:
        """There is no archived Illinois series. See the module docstring.

        This is not the base class's "no archive available" shrug: the Internet
        Archive was swept for this page, for its export links, and for the static
        download path that carried the same file in 2021, and the answer is that
        a postback page archives as the form and never as the answer. Saying so
        here keeps the next reader from repeating the search.

        The 2026-09-09 pass widened it off this domain altogether -- the UF
        Election Lab never tracked Illinois, the official 2024 canvass carries no
        mode dimension, and no county site mirrored it. The one thing that does
        exist is a per-authority FINAL in the EAC's EAVS, which is a different
        source measuring a different quantity; the docstring says why it is not
        published from here.
        """
        raise NotYetPublished(
            f"IL: no {cycle} pre-election counts exist to fetch — the SBE purges "
            f"past elections from the dropdown, and every Wayback capture of "
            f"{INDEX} carries only the unselected form (no tables, no export "
            f"links). The static path that held this file in 2021 has no {cycle} "
            f"capture and 404s today. No third party mirrored it either; see the "
            f"module docstring for the off-domain sweep and for the EAVS final "
            f"that exists instead."
        )
