"""Connecticut — the Secretary of the State's early-voting and absentee detail files.

Connecticut publishes **one row per ballot**, in two workbooks refreshed through
the voting period and linked from a per-cycle "Voter Data" page:

```
VOTER ID | TID | FIRST NAME | ... | RESIDENCE ADDRESS CITY | ... | YOB | DST | PR |
PARTY | SERIAL | DT MAILED | DT RETURNED | TM RETURN | RETURN_TYPE | ISSUE_TYPE
```

`early_voting_<date>.xlsx` is one row per in-person early vote;
`absentee_ballot_<date>.xlsx` is one row per absentee ballot **issued**, with a
`DT RETURNED` that is blank until it comes back. Because every ballot carries its
own dates, a single download reconstructs the whole daily curve the way North
Carolina's and Maine's files do, and a day we fail to run is not a day of
Connecticut's series lost forever.

**Connecticut runs elections by TOWN**, so this adapter publishes town rows keyed
by the 10-digit Census county-subdivision GEOID and derives its county rows by
summing them. That rollup is exact rather than inferred: a cousub GEOID is
state(2) + county(3) + cousub(5), so Enfield's `0900325990` already says
"Hartford County" in digits 3-5. See `adapters/_towns.py`, and `me.py` for the
same shape in Maine.

Seven things drive the parser.

* **The filename convention is not stable and must be scraped, not built.**
  Inside the 2026 cycle alone Connecticut has used two: the Wayback capture of
  the 2026 Voter Data page from 2026-08-01 links `abs_detail_073126.xlsx`
  (MMDDYY), while the same page today links `absentee_ballot_09032026.xlsx` and
  `early_voting_09032026.xlsx` (MMDDYYYY). Both are handled, but the index page
  is the entry point and the constructed names are only a fallback.

* **Only the current election's files stay up.** Sweeping every date from
  2026-07-20 to 2026-09-06 finds files on 08/14 through 09/03 -- the September 1
  special primary -- and nothing for the August 11 statewide primary, whose files
  have been deleted. Sweeping the 2024 and 2025 folder names finds nothing at
  all either (204 URLs; `fetch_history` has the list). So there is no daily
  archive and the daily run is the only way Connecticut's CURVE gets recorded --
  a past cycle gets its certified FINAL from the Statement of Vote instead, at
  the bottom of this docstring.

* **Which election a file belongs to is checked twice.** The folder is shared
  across every election in a cycle, so a September run must not read the special
  primary's ballots as general-election early voting. The filename's date must
  fall inside the general's own reporting window, AND the ballots' own dates must
  mostly fall inside it too. Outside either, this is `NotYetPublished` -- the
  general's file is not up yet -- not an error.

* **Connecticut skips days.** In the verified 2026 run there is no file for
  08/15, 08/16, 08/18, 08/20-08/23 or 09/02. A run that asked only for today's
  file would report "nothing yet" with a good snapshot sitting on the server, so
  the index is read for anything within `LOOKBACK_DAYS`.

* **`RETURN_TYPE` is read for one word only: "Void".** The two files already ARE
  the method split, so the return-type vocabulary (`Mail`, `Drop Box 3`,
  `In Person By Voter`, `In Person by Designee or Family`, `Supervised`, and
  blank for a ballot not yet back) never has to be interpreted, and an unfamiliar
  value is not drift -- nothing is bucketed by it. A voided ballot is dropped
  from every count, issued and returned alike.

* **Both files, or the absentee file alone -- never the early-voting file
  alone.** `_Bucket.total` is `returned + inperson`, so a method whose file was
  not read contributes 0 rather than nothing: on the September 2026 fixtures
  both files give 58, absentee alone gives 30 and early voting alone gives 28.
  A run that loses one workbook would therefore publish a cumulative total
  BELOW yesterday's, which is the one thing a cumulative series may never do.
  Absentee-only is legitimate (ballots go out a fortnight before early voting
  opens, so `inperson` is genuinely None then); early-voting-only never is, and
  `require_both` refuses it. `CTScraper.fetch` closes the other direction: a
  kind the index page LISTS for this election but that will not download is a
  failed fetch, not an absence.

* **Party is a real registration** -- Connecticut enrols by party -- but it is
  spelled out in full (`Democratic`), and Connecticut recognises minor parties
  whose names the shared vocabulary does not know and, worse, one it would get
  BACKWARDS: in Connecticut the "Independent Party" is a registered party, while
  `normalize.party("independent")` returns `npa`. So `PARTY_ALIASES` below is
  consulted FIRST, and only labels in neither table raise SchemaDrift.

--------------------------------------------------------------------------
THE PAST CYCLE: A CERTIFIED FINAL, NOT A CURVE
--------------------------------------------------------------------------

The daily workbooks above are DELETED when the next election starts, and that
is now measured rather than assumed -- see `fetch_history` for the sweep. So a
2024 daily series does not exist anywhere and this module does not pretend one
does.

What DOES exist is the certified **Statement of Vote**, and it carries the one
table this repo needs:

    https://portal.ct.gov/-/media/sots/electionservices/statementofvote_pdfs/
        2024_statement_of_vote.pdf                    VERIFIED 200, 3,139,465 B

Its last statistics section -- printed pages 157-162 of 170 -- is
"*Same-Day Registration (SDR), Turnout, Absentee & Early Voting Ballot
Statistics", one row per town over twelve columns:

```
Town  | Names on Official Check List (Active) | Number Checked as Having Voted |
      | Percentage Checked as Having Voted    |
      | Number of Absentee Ballots Received from Town Clerk / Rejected / Voted  |
      | Number of Early Ballots Received / Rejected / Voted                     |
      | Number of SDR Received / Rejected / Voted                               |
Andover  2,399  2,118  88.29%   62  0  62      870  3  867      68  1  67
...
TOTAL   2,348,545 1,788,954 76.17%  120,422 2,060 118,362  720,173 1,302 718,871
                                                             53,140 287 52,853
```

Five things this backfill does and why.

* **"Voted", not "Received".** Received minus Rejected is Voted exactly, on every
  row and on the totals line, so "Received" is ballots RETURNED to the town clerk
  and "Voted" is the ones that counted. `read()` above already drops a `Void`
  ballot from the daily files, so "Voted" is the same quantity the live path
  publishes and is what `mail_returned` and `inperson` carry here.

* **`mail_requested` is None.** The Statement of Vote never says how many
  absentee ballots were ISSUED. None, never 0. Same for all four `party_*`
  columns: Connecticut registers by party, but this document does not report it,
  and four zeroes would read as nobody of any party having voted early.

* **SDR is not early voting** and is not published at all. Same-day registrants
  vote on Election Day.

* **The town names are read with every space removed, and that is load-bearing.**
  This is Montana's lesson (see `mt.py`'s TITLE note) in a second file: pypdf
  extracts this table's headings as `To wn`, `Receiv ed`, `Hav ing`, and its
  town names as `M adison`, `M anchester`, with `North Stonington` split across
  two lines as `North Stonin` / `gton`. Every one of those is a text-extractor
  spacing heuristic rather than bytes in the file, and `pyproject.toml` pins only
  a RANGE of pypdf. So the header tags, the row names and the totals row are all
  matched against the extract with ALL whitespace stripped, and the match on a
  town name is EXACT after that -- never a prefix, never a nearest guess.

* **Two gates make a mis-attribution impossible to publish.** All 169 towns must
  resolve, each exactly once, and their columns must sum to the document's own
  TOTAL row on all eleven counts. Both were measured against the real file:
  169/169 and an exact match on every column. A header fragment swallowed into a
  name, a page dropped, a town renamed -- any of them breaks one of the two, and
  Rule 3 says raise rather than guess.
"""

from __future__ import annotations

import io
import logging
import re
from collections import defaultdict
from datetime import date, datetime, timedelta
from html import unescape
from urllib.parse import urljoin

import openpyxl
import pypdf

from ..calendar import election_date
from ..normalize import PARTY_DEM, PARTY_NPA, PARTY_OTH, PARTY_REP
from ..normalize import party as normalize_party
from ..schema import TIER_SCRAPER, CountyDay, StateDay, TownDay
from . import _towns
from ._net import Missing, get, looks_like_html, looks_like_xlsx
from .base import Adapter, FetchResult, NotYetPublished, SchemaDrift, SourceError

log = logging.getLogger(__name__)

HOST = "https://portal.ct.gov"

#: VERIFIED 2026-09-06, live, from this network:
#:   https://portal.ct.gov/sots/election-services/2026-voter-data              200, 27,056 B, real page
#:   https://portal.ct.gov/sots/election-services/2024-voter-data              200 but "<title>404 Error Page</title>"
#:   https://portal.ct.gov/sots/election-services/2022-voter-data              200 but "<title>404 Error Page</title>"
#: portal.ct.gov never sends a 404 status: a missing page and a missing file
#: both come back 200 with that title, which is why `_is_soft_404` exists.
INDEX_URL = HOST + "/sots/election-services/{cycle}-voter-data"

#: VERIFIED 2026-09-06, all real xlsx unless noted:
#:   .../2026_absentee_ballot_data/early_voting_09032026.xlsx      200, 81,797 B
#:   .../2026_absentee_ballot_data/absentee_ballot_09032026.xlsx   200, 47,911 B
#:   .../2026_absentee_ballot_data/early_voting_08252026.xlsx      200, 31,924 B  (first early file)
#:   .../2026_absentee_ballot_data/absentee_ballot_08142026.xlsx   200, 24,508 B  (first absentee file)
#:   .../2026_absentee_ballot_data/early_voting_09042026.xlsx      200 soft-404 HTML, 21,880 B
#:   .../2024_absentee_ballot_data/early_voting_11052024.xlsx      200 soft-404 HTML  (no 2024 archive)
#:   .../2022_absentee_ballot_data/absentee_ballot_11072022.xlsx   200 soft-404 HTML  (no 2022 archive)
#: UNVERIFIED for the general: no general-election file has ever been observed,
#: because Connecticut deletes each election's files when the next one starts.
#: The layout below is from the September 2026 special primary's files.
MEDIA_DIR = HOST + "/-/media/sots/electionservices/{cycle}_absentee_ballot_data/"

#: Filename patterns Connecticut has actually used, per kind. Fallback only --
#: `_index_files` scrapes the page first.
FILENAME_PATTERNS = {
    "inperson": ("early_voting_{mmddyyyy}.xlsx",),
    "mail": ("absentee_ballot_{mmddyyyy}.xlsx", "abs_detail_{mmddyy}.xlsx"),
}

#: How a SCRAPED filename says which of the two files it is. Checked in this
#: order, so "early_voting_..." cannot be mistaken for an absentee file. These
#: are wider than FILENAME_PATTERNS on purpose: classifying a link the page
#: already gave us costs nothing if the guess is unused, whereas constructing a
#: URL from a guess would be fabricating one. ("ev_detail" is the only entry
#: here that has never been seen in the wild; it is the symmetric partner of the
#: verified "abs_detail".)
KIND_MARKERS = (("inperson", ("early_vot", "early-vot", "ev_detail")),
                ("mail", ("absentee", "abs_detail", "abs_")))

#: Header text (whitespace-collapsed, uppercased) -> the role we need it for.
FIELD_ALIASES = {
    "RESIDENCE ADDRESS CITY": "town",
    "PARTY": "party",
    "DT MAILED": "mailed",
    "DT RETURNED": "returned",
    "RETURN_TYPE": "return_type",
}
REQUIRED_ROLES = ("town", "party", "mailed", "returned", "return_type")

#: The one `RETURN_TYPE` value that changes a count. See the module docstring.
VOID = "void"

#: Connecticut's party labels, consulted BEFORE `normalize.party`.
#:
#: "Democratic" is the only one verified in a real file (every row of the
#: September 2026 special DEMOCRATIC primary's two workbooks). The rest are the
#: registered parties named in the Secretary of the State's own Minor Party Key,
#: page 8 of
#: `portal.ct.gov/-/media/sots/electionservices/2025/registration-and-enrollment/nov25re.pdf`
#: (verified 200, 174,325 B): Green, Libertarian, Working Families, Independent,
#: Independence, Bottom Line, Concerned Citizens, We The People, Reform, Open.
#: Their spelling in the ballot file is UNVERIFIED -- no general-election file
#: has ever been observable -- so anything not here and not known to
#: `normalize.party` raises SchemaDrift rather than being bucketed.
#:
#: The entries that matter are the two the shared vocabulary would get wrong:
#: Connecticut's "Independent" is the Independent Party of Connecticut, a
#: registered party, NOT an unaffiliated voter (whom Connecticut calls
#: "Unaffiliated"); and "Independence" is a different registered party again.
PARTY_ALIASES = {
    "independent": PARTY_OTH,
    "independent party": PARTY_OTH,
    "independence": PARTY_OTH,
    "independence party": PARTY_OTH,
    "bottom line": PARTY_OTH,
    "concerned citizens": PARTY_OTH,
    "we the people": PARTY_OTH,
    "open": PARTY_OTH,
    "green party": PARTY_OTH,
    "working families party": PARTY_OTH,
    "libertarian party": PARTY_OTH,
    "reform party": PARTY_OTH,
}

_PARTY_FIELD = {
    PARTY_DEM: "party_dem", PARTY_REP: "party_rep",
    PARTY_NPA: "party_npa", PARTY_OTH: "party_oth",
}

#: How far before Election Day a file's own DATE may be and still be the
#: general's. Connecticut mails absentee ballots from 31 days out and runs 14
#: days of early voting, so the general's first file lands about five weeks
#: before; the September special primary's files, which live in the same folder,
#: are 60+ days out and are refused by this.
FILE_WINDOW_DAYS = 45

#: And how long after Election Day a file may still be updated.
FILE_TAIL_DAYS = 20

#: The second, independent check: the BALLOTS' own dates. A file whose ballots
#: were mostly mailed or returned outside this window belongs to another
#: election whatever its filename says.
BALLOT_WINDOW_DAYS = 60

#: Share of a file's dated ballots that must fall inside that window. Not an
#: all-test: UOCAVA ballots are issued months early.
BALLOT_WINDOW_SHARE = 0.75

#: Connecticut skips days -- verified above. Look back this far for the newest
#: file rather than reporting "nothing yet".
LOOKBACK_DAYS = 7

#: How far back the published curve runs. Ballots dated before this are real and
#: are folded into the first day's cumulative total rather than dropped.
MAX_SPAN_DAYS = 120

#: Share of counted ballots whose municipality must resolve to a census GEOID
#: before the town and county tables are trusted. Connecticut's 169 towns all
#: appear in `_towns`, and the file's `RESIDENCE ADDRESS CITY` is the town of
#: registration rather than a postal city -- every row of the Enfield file says
#: "Enfield", not "Thompsonville" or "Hazardville". A drop below this floor means
#: that stopped being true, which is drift, not a few odd rows.
MIN_TOWN_COVERAGE = 0.95

#: Share of counted ballots that must carry a party we could resolve before the
#: party columns are published at all. A blank `PARTY` cell is not a party
#: bucket, and four party numbers that quietly add up to well under the total
#: are worse than no party breakdown -- see THE BLANK RULE in schema.py.
MIN_PARTY_COVERAGE = 0.95

#: How many unmapped municipality names to name in the log line.
UNMAPPED_LOG_LIMIT = 12

_ANCHOR = re.compile(r'<a\b[^>]*href="([^"]+)"', re.IGNORECASE)
_SOFT_404 = re.compile(r"<title>\s*404\s*Error", re.IGNORECASE)
_DATE8 = re.compile(r"(\d{8})(?!\d)")
_DATE6 = re.compile(r"(\d{6})(?!\d)")


# --------------------------------------------------------------------------
# The certified Statement of Vote -- the only past-cycle source. See the
# module docstring's last section.
# --------------------------------------------------------------------------
#: Cycle -> the Statement of Vote for that cycle's general, VERIFIED live.
#:
#: Only 2024 is listed and that is deliberate: every other year's file was
#: probed under this exact naming and answers portal.ct.gov's soft 404, so a
#: constructed URL for it would be a guess. `fetch_history` refuses an
#: unlisted cycle BY NAME rather than inventing one.
#:
#: VERIFIED live 2026-09-09 from this network:
#:   .../statementofvote_pdfs/2024_statement_of_vote.pdf  200, 3,139,465 B, real PDF
#:   .../statementofvote_pdfs/2022_statement_of_vote.pdf  200, soft-404 HTML
#:   .../statementofvote_pdfs/2020_statement_of_vote.pdf  200, soft-404 HTML
#:   .../statementofvote_pdfs/2026_statement_of_vote.pdf  200, soft-404 HTML
STATEMENT_OF_VOTE = {
    2024: HOST + "/-/media/sots/electionservices/statementofvote_pdfs/"
                 "2024_statement_of_vote.pdf",
}

#: The twelve column headings of the statistics table, IN THE ORDER the values
#: are read positionally. Matched against the extract with all whitespace
#: removed, because pypdf renders them "To wn", "Receiv ed", "Hav ing".
#:
#: ⚠️ ORDER IS CHECKED, NOT JUST PRESENCE -- hi.py's `_check_header` is the
#: precedent and the reason. The nine counts are consumed by position, and the
#: only other check on them is that they sum to the document's own totals row,
#: which is commutative and cannot see the absentee block printed where the
#: early block was. Requiring the tags in order is what pins the positions.
SOV_COLUMN_TAGS: tuple[str, ...] = (
    "Town",
    "NamesonOfficialCheckList(Active)",
    "NumberCheckedasHavingVoted",
    "PercentageCheckedasHavingVoted",
    "NumberofAbsenteeBallotsReceivedfromTownClerk",
    "NumberofAbsenteeBallotsRejected",
    "NumberofAbsenteeBallotsVoted",
    "NumberofEarlyBallotsReceived",
    "NumberofEarlyBallotsRejected",
    "NumberofEarlyBallotsVoted",
    "NumberofSDRReceived",
    "NumberofSDRRejected",
    "NumberofSDRVoted",
)

#: The last heading, lowercased and compacted. A page's header block runs
#: together with the first data row when pypdf wraps it, so a candidate name is
#: cut at the LAST occurrence of this.
_SOV_HEADER_END = "numberofsdrvoted"

#: The caption that sits between the header and the first row on the continuation
#: pages, likewise compacted and stripped off a candidate name.
_SOV_CAPTION = "*same-dayregistration(sdr),turnout,absentee&earlyvotingballotstatistics"

#: One statistics row: an optional name, the checklist count, the number checked
#: as having voted, a percentage, then the NINE counts. Anchored at both ends so
#: a line with a stray trailing figure cannot match.
_SOV_ROW = re.compile(
    r"^(?P<name>.*?)\s*(?P<listed>[\d,]+)\s+(?P<voted>[\d,]+)\s+[\d.]+%"
    r"(?P<counts>(?:\s+[\d,]+){9})\s*$"
)

#: The cover page names the election. Both patterns are matched against the
#: compacted extract, for the same reason the town names are.
_SOV_KIND = re.compile(r"StatementofVote(General|Primary|Special)Election", re.I)
_SOV_DAY = re.compile(
    r"(January|February|March|April|May|June|July|August|September|October|"
    r"November|December)(\d{1,2}),(\d{4})", re.I)

_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12,
}

#: Connecticut's 169 towns are the whole state; anything less is drift.
SOV_TOWNS = 169

#: A census county-subdivision name carries a type suffix the Statement of Vote
#: does not ("Andover town" vs "Andover").
_COUSUB_SUFFIX = re.compile(r"\s+(town|city|borough)$", re.IGNORECASE)


def _compact(text: str) -> str:
    """The extract with ALL whitespace removed. See the module docstring."""
    return re.sub(r"\s+", "", text)


# --------------------------------------------------------------------------
# Index scraping
# --------------------------------------------------------------------------
def _is_soft_404(body: bytes) -> bool:
    """portal.ct.gov answers a missing page or file with 200 and this title."""
    return bool(_SOFT_404.search(body[:4096].decode("utf-8", errors="replace")))


def _kind_of(filename: str) -> str | None:
    lower = filename.lower()
    for kind, markers in KIND_MARKERS:
        if any(marker in lower for marker in markers):
            return kind
    return None


def _date_of(filename: str) -> date | None:
    """The date stamped into a Connecticut filename, MMDDYYYY or MMDDYY."""
    stem = filename.rsplit(".", 1)[0]
    hit = _DATE8.search(stem)
    if hit:
        try:
            return datetime.strptime(hit.group(1), "%m%d%Y").date()
        except ValueError:
            return None
    hit = _DATE6.search(stem)
    if hit:
        try:
            return datetime.strptime(hit.group(1), "%m%d%y").date()
        except ValueError:
            return None
    return None


def index_files(page: bytes, cycle: int) -> dict[str, list[tuple[date, str]]]:
    """kind -> [(file date, absolute URL)], newest first, from the Voter Data page."""
    html = page.decode("utf-8", errors="replace")
    found: dict[str, dict[date, str]] = {"inperson": {}, "mail": {}}
    for href in _ANCHOR.findall(html):
        url = urljoin(HOST, unescape(href))
        path = url.split("?", 1)[0]
        if not path.lower().endswith(".xlsx"):
            continue
        filename = path.rsplit("/", 1)[-1]
        kind = _kind_of(filename)
        day = _date_of(filename)
        if kind is None or day is None:
            continue
        # The query string carries Sitecore's rev/hash; the bare path serves the
        # same bytes (verified), so it is dropped to keep the cache key stable.
        found[kind].setdefault(day, path)
    listed = {kind: sorted(days.items(), reverse=True) for kind, days in found.items()}
    log.debug("CT: the %s voter-data page lists %s", cycle,
              {kind: [day.isoformat() for day, _ in entries] for kind, entries in listed.items()})
    return listed


def constructed(cycle: int, kind: str, day: date) -> list[str]:
    """The verified literal filename patterns for one kind on one day."""
    stamps = {"mmddyyyy": day.strftime("%m%d%Y"), "mmddyy": day.strftime("%m%d%y")}
    directory = MEDIA_DIR.format(cycle=cycle)
    return [directory + pattern.format(**stamps) for pattern in FILENAME_PATTERNS[kind]]


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------
def _party(raw) -> str | None:
    """A canonical party bucket, or None when the row states no enrolment."""
    label = " ".join(str(raw or "").split()).lower()
    if not label:
        return None
    bucket = PARTY_ALIASES.get(label) or normalize_party(label)
    if bucket is None:
        raise SchemaDrift(f"CT: unrecognised party {raw!r}")
    return bucket


def _day(raw) -> date | None:
    """A date out of "2026-08-30", a real datetime, or "08/30/2026"."""
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw
    token = str(raw).strip().split(" ")[0]
    if not token:
        return None
    for pattern in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(token, pattern).date()
        except ValueError:
            continue
    return None


class _Bucket:
    """One geography's tallies for one day."""

    __slots__ = ("issued", "returned", "inperson", "party")

    def __init__(self) -> None:
        self.issued = 0      # absentee ballots MAILED that day
        self.returned = 0    # absentee ballots RETURNED that day
        self.inperson = 0    # in-person early votes cast that day
        self.party: dict[str, int] = defaultdict(int)

    @property
    def total(self) -> int:
        return self.returned + self.inperson

    def add(self, other: "_Bucket") -> None:
        self.issued += other.issued
        self.returned += other.returned
        self.inperson += other.inperson
        for key, count in other.party.items():
            self.party[key] += count


class Tally:
    """Everything one pass over Connecticut's ballot rows produced."""

    def __init__(self) -> None:
        self.by_day: dict[date, _Bucket] = defaultdict(_Bucket)
        self.by_town: dict[tuple[str, date], _Bucket] = defaultdict(_Bucket)
        self.town_names: dict[str, str] = {}
        self.unmapped: dict[str, int] = defaultdict(int)
        self.counted = 0        # ballots placed on the RETURNS/early-vote curve
        self.placed = 0         # rows that landed in any bucket at all
        self.unenrolled = 0     # counted ballots with a blank PARTY cell
        self.ballot_days: list[date] = []
        self.kinds: set[str] = set()


def _header(row) -> dict[str, int]:
    index: dict[str, int] = {}
    for position, cell in enumerate(row):
        role = FIELD_ALIASES.get(" ".join(str(cell or "").split()).upper())
        if role and role not in index:
            index[role] = position
    missing = [role for role in REQUIRED_ROLES if role not in index]
    if missing:
        got = [" ".join(str(c or "").split()) for c in row][:40]
        raise SchemaDrift(f"CT: ballot file header is missing {missing}: {got!r}")
    return index


def read(body: bytes, kind: str, tally: Tally) -> None:
    """Fold one Connecticut ballot file into `tally`.

    `kind` is "inperson" for the early-voting file and "mail" for the absentee
    file. The two are counted differently and that difference is the whole
    method split -- see the module docstring.
    """
    if looks_like_html(body) or not looks_like_xlsx(body):
        raise SourceError(f"CT: the {kind} ballot file is not an xlsx")
    try:
        workbook = openpyxl.load_workbook(io.BytesIO(body), read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001 -- openpyxl raises a zoo of types
        raise SourceError(f"CT: could not open the {kind} ballot workbook: {exc}") from exc

    sheet = workbook.worksheets[0]
    rows = sheet.iter_rows(values_only=True)
    try:
        header = next(rows)
    except StopIteration as exc:
        raise SchemaDrift(f"CT: the {kind} ballot file is empty") from exc
    index = _header(header)
    tally.kinds.add(kind)

    seen = 0
    for row in rows:
        if row is None or all(cell is None for cell in row):
            continue
        seen += 1
        return_type = " ".join(str(row[index["return_type"]] or "").split()).lower()
        if return_type == VOID:
            # Issued and cancelled. Not a ballot in any count.
            continue

        # Validated on every row, counted or not, so a vocabulary change shows up
        # wherever it lands rather than only when it hits a counted ballot.
        party = _party(row[index["party"]])
        name, town = _resolve(row[index["town"]], tally)

        mailed = _day(row[index["mailed"]])
        returned = _day(row[index["returned"]])

        # A row with no usable date lands in no bucket, so it is neither placed
        # nor held against the town-coverage floor -- that floor is about names
        # we could not resolve, not about dates the clerk did not key.
        if returned is not None or (kind == "mail" and mailed is not None):
            tally.placed += 1
            if town is None:
                tally.unmapped[name] += 1

        if kind == "mail":
            if mailed is not None:
                tally.ballot_days.append(mailed)
                tally.by_day[mailed].issued += 1
                if town is not None:
                    tally.by_town[(town, mailed)].issued += 1
            if returned is None:
                # Issued but not back yet. Real, and not on the returns curve.
                continue
            field = "returned"
        else:
            if returned is None:
                # An early vote with no date cannot be placed on the curve.
                continue
            field = "inperson"

        tally.ballot_days.append(returned)
        tally.counted += 1
        if party is None:
            tally.unenrolled += 1

        bucket = tally.by_day[returned]
        setattr(bucket, field, getattr(bucket, field) + 1)
        if party:
            bucket.party[party] += 1
        if town is not None:
            town_bucket = tally.by_town[(town, returned)]
            setattr(town_bucket, field, getattr(town_bucket, field) + 1)
            if party:
                town_bucket.party[party] += 1

    if not seen:
        raise SchemaDrift(f"CT: the {kind} ballot file has a header but no rows")


def _resolve(raw, tally: Tally) -> tuple[str, str | None]:
    """(the name as written, its 10-digit GEOID or None).

    None is never guessed into a county: the caller counts the miss and the row
    is excluded from both the town and the county table. `read` does the
    counting rather than this function, so a row that lands in no bucket at all
    is not held against the coverage floor.
    """
    name = " ".join(str(raw or "").split())
    if not name:
        return "", None
    hit = _towns.lookup("CT", name)
    if hit is None:
        return name, None
    geoid, canonical = hit
    tally.town_names[geoid] = canonical
    return name, geoid


def belongs_to(tally: Tally, cycle: int) -> bool:
    """Do these ballots belong to `cycle`'s general election?

    The folder is shared by every election in a cycle and only the current one's
    files are kept, so a run in September would otherwise read a special
    primary's ballots as general-election early voting.
    """
    if not tally.ballot_days:
        return False
    day_zero = election_date(cycle)
    opens = day_zero - timedelta(days=BALLOT_WINDOW_DAYS)
    closes = day_zero + timedelta(days=FILE_TAIL_DAYS)
    inside = sum(1 for day in tally.ballot_days if opens <= day <= closes)
    return inside / len(tally.ballot_days) >= BALLOT_WINDOW_SHARE


def in_file_window(day: date, cycle: int) -> bool:
    day_zero = election_date(cycle)
    return (day_zero - timedelta(days=FILE_WINDOW_DAYS)
            <= day <= day_zero + timedelta(days=FILE_TAIL_DAYS))


# --------------------------------------------------------------------------
# Emission
# --------------------------------------------------------------------------
def _report_coverage(tally: Tally) -> bool:
    """Log what did not map; refuse the file if too little did. Returns whether
    the party columns may be published."""
    lost = sum(tally.unmapped.values())
    placed = tally.placed
    coverage = 1.0 - (lost / placed) if placed else 0.0
    if tally.unmapped:
        worst = sorted(tally.unmapped.items(), key=lambda kv: -kv[1])
        shown = ", ".join(f"{name or '<blank>'} ({count})" for name, count in worst[:UNMAPPED_LOG_LIMIT])
        log.warning(
            "CT: %d municipalit%s (%d rows) have no census county-subdivision "
            "GEOID and are excluded from the town and county tables: %s%s",
            len(tally.unmapped), "y" if len(tally.unmapped) == 1 else "ies", lost, shown,
            "" if len(worst) <= UNMAPPED_LOG_LIMIT else f", +{len(worst) - UNMAPPED_LOG_LIMIT} more",
        )
    log.info("CT: %d towns mapped, %.2f%% of %d placed ballots keyed to a GEOID",
             len(tally.town_names), 100 * coverage, placed)
    if placed and coverage < MIN_TOWN_COVERAGE:
        raise SchemaDrift(
            f"CT: only {100 * coverage:.1f}% of ballots map to a census "
            f"county-subdivision GEOID (floor is {100 * MIN_TOWN_COVERAGE:.0f}%); "
            f"the residence-city column or its vocabulary has changed"
        )

    counted = tally.counted
    enrolled = 1.0 - (tally.unenrolled / counted) if counted else 0.0
    if counted and enrolled < MIN_PARTY_COVERAGE:
        log.warning(
            "CT: %d of %d counted ballots (%.1f%%) have a blank PARTY cell; "
            "publishing no party breakdown rather than four numbers that do not "
            "add up to the total",
            tally.unenrolled, counted, 100 * (1 - enrolled),
        )
        return False
    return True


def _walk(series: dict[date, _Bucket], span: list[date], start: date):
    """Yield (day, today, running) along `span` for one geography."""
    running = _Bucket()
    for day, bucket in sorted(series.items()):
        if day < start:
            running.add(bucket)
    for day in span:
        today = series.get(day)
        if today:
            running.add(today)
        if running.total == 0:
            continue
        yield day, today, running


def _regroup(by_key: dict[tuple[str, date], _Bucket]) -> dict[str, dict[date, _Bucket]]:
    out: dict[str, dict[date, _Bucket]] = defaultdict(dict)
    for (geography, day), bucket in by_key.items():
        out[geography][day] = bucket
    return out


def emit(tally: Tally, cycle: int, through: date, *, with_party: bool) -> FetchResult:
    result = FetchResult()
    _towns.attach(result, [])
    days = [day for day in tally.by_day if day <= through]
    if not days:
        return result

    day_zero = election_date(cycle)
    start = max(min(days), day_zero - timedelta(days=MAX_SPAN_DAYS))
    span = [start]
    while span[-1] < through:
        span.append(span[-1] + timedelta(days=1))

    def party_fields(running: _Bucket) -> dict[str, int | None]:
        if not with_party:
            return {field: None for field in _PARTY_FIELD.values()}
        # Connecticut enrols by party and this file names it on every ballot, so
        # a bucket with no ballots yet is a real 0 -- blank would claim
        # Connecticut does not report party at all.
        return {field: running.party.get(key, 0) for key, field in _PARTY_FIELD.items()}

    # The absentee file is the only one carrying issued-but-not-returned
    # ballots. If it was not read, `mail_requested` and `mail_returned` are not
    # reported rather than zero -- and the same the other way for `inperson`.
    saw_mail = "mail" in tally.kinds
    saw_inperson = "inperson" in tally.kinds

    running = _Bucket()
    for day, bucket in sorted(tally.by_day.items()):
        if day < start:
            running.add(bucket)
    for day in span:
        today = tally.by_day.get(day)
        if today:
            running.add(today)
        result.state_rows.append(StateDay(
            cycle=cycle, state="CT", day=day,
            ballots_total=running.total,
            ballots_new=today.total if today else 0,
            mail_requested=running.issued if saw_mail else None,
            mail_returned=running.returned if saw_mail else None,
            inperson=running.inperson if saw_inperson else None,
            **party_fields(running),
        ))

    if not tally.by_town:
        return result

    by_county: dict[tuple[str, date], _Bucket] = defaultdict(_Bucket)
    for (geoid, day), bucket in tally.by_town.items():
        by_county[(_towns.county_of(geoid), day)].add(bucket)

    town_rows: list[TownDay] = []
    for geoid, series in sorted(_regroup(tally.by_town).items()):
        for day, today, walked in _walk(series, span, start):
            town_rows.append(TownDay(
                cycle=cycle, state="CT", town_geoid=geoid, day=day,
                town_name=tally.town_names.get(geoid, ""),
                ballots_total=walked.total,
                ballots_new=today.total if today else 0,
                mail_returned=walked.returned if saw_mail else None,
                inperson=walked.inperson if saw_inperson else None,
                **party_fields(walked),
            ))
    _towns.attach(result, town_rows)

    # Exact, not inferred: the county is digits 3-5 of the town's own GEOID.
    for fips, series in sorted(_regroup(by_county).items()):
        for day, today, walked in _walk(series, span, start):
            result.county_rows.append(CountyDay(
                cycle=cycle, state="CT", county_fips=fips, day=day,
                county_name=_towns.county_name_of("CT", fips),
                ballots_total=walked.total,
                ballots_new=today.total if today else 0,
                mail_returned=walked.returned if saw_mail else None,
                inperson=walked.inperson if saw_inperson else None,
                **party_fields(walked),
            ))
    return result


def require_both(kinds: set[str]) -> None:
    """⚠️ AN EARLY-VOTING FILE WITHOUT ITS ABSENTEE PARTNER IS A FAILED FETCH,
    NOT A CONNECTICUT WITH NO MAIL BALLOTS -- AND A CUMULATIVE SERIES MAY NEVER
    FALL.

    `_Bucket.total` is `returned + inperson`, and a method whose file was not
    read contributes 0 to it rather than nothing. Read only the absentee file
    and the total is 30; read only the early-voting file and it is 28; read both
    and it is 58. So a run that loses one workbook publishes a cumulative total
    BELOW the one it published yesterday -- measured on the September 2026
    fixtures, 58 -> 28 -- which no consumer of a cumulative curve can read as
    anything but ballots being taken back.

    One of the two directions is legitimate and the other never is:

    * **Absentee alone is real.** Connecticut issues absentee ballots from 31
      days out and opens early voting 14 days before Election Day (the window
      behind FILE_WINDOW_DAYS above), so for the first fortnight of the season
      there IS no early-voting file. `inperson` is then None -- not 0 -- and the
      total is complete, because no in-person early vote can have been cast.

    * **Early voting alone is not.** The absentee file necessarily exists by the
      time the early-voting one does, so its absence means we could not fetch
      it. That is `SourceError` -- fall through to a weaker tier, which will at
      least publish a statewide number that is not missing a method -- and never
      a tier-1 row asserting a Connecticut early-vote total with every mail
      ballot silently dropped out of it. Same call as `tx.py` and `mt.py` make
      about a total over a subset of counties: it looks exactly like the real
      figure and is not one.

    Published in order, the total therefore goes absentee-only -> both, which is
    monotone. `CTScraper.fetch` closes the other half of the hole: a kind the
    index page LISTS but that would not download is a failure too, so the
    both -> absentee-only direction cannot happen either.
    """
    if "inperson" in kinds and "mail" not in kinds:
        raise SourceError(
            "CT: the early-voting file was read but the absentee file was not. "
            "Connecticut issues absentee ballots two weeks before early voting "
            "opens, so this is a failed fetch, not a day with no mail ballots; "
            "publishing it would drop every returned absentee ballot out of a "
            "cumulative total"
        )


def build(files: list[tuple[str, bytes]], cycle: int, through: date) -> FetchResult:
    """Parse one day's pair of Connecticut files into canonical rows."""
    tally = Tally()
    for kind, body in files:
        read(body, kind, tally)
    if not tally.ballot_days:
        raise SchemaDrift("CT: the ballot files carry no parseable dates")
    if not belongs_to(tally, cycle):
        raise NotYetPublished(
            f"CT: the posted ballot files are for another election "
            f"(ballot dates run {min(tally.ballot_days).isoformat()}.."
            f"{max(tally.ballot_days).isoformat()}, not the {cycle} general)"
        )
    require_both(tally.kinds)
    with_party = _report_coverage(tally)
    return emit(tally, cycle, through, with_party=with_party)


# --------------------------------------------------------------------------
# Statement of Vote parsing
# --------------------------------------------------------------------------
def sov_index() -> dict[str, tuple[str, str]]:
    """{compacted town name: (10-digit GEOID, census name)} for all 169 towns.

    The Statement of Vote writes "Andover"; the census table writes "Andover
    town". Stripping the type suffix is the whole crosswalk, and the keys are
    compacted so pypdf's "M adison" lands on Madison.
    """
    index: dict[str, tuple[str, str]] = {}
    for name in _towns.names("CT"):
        bare = _COUSUB_SUFFIX.sub("", name)
        key = _compact(bare).lower()
        if key in index:  # pragma: no cover - the census table has no CT collisions
            raise SchemaDrift(f"CT: two towns compact to {key!r}")
        hit = _towns.lookup("CT", name)
        if hit is None:  # pragma: no cover - names() and lookup() share a table
            raise SchemaDrift(f"CT: census name {name!r} does not resolve")
        index[key] = hit
    return index


def sov_pages(body: bytes) -> list[str]:
    """The extracted text of every page, for one Statement of Vote PDF."""
    try:
        reader = pypdf.PdfReader(io.BytesIO(body))
        return [page.extract_text() or "" for page in reader.pages]
    except Exception as exc:  # noqa: BLE001 - pypdf raises a zoo of types
        raise SourceError(f"CT: the Statement of Vote is not a readable PDF: {exc}") from exc


def sov_election(pages: list[str]) -> tuple[str, date]:
    """(kind, election day) off the Statement of Vote's own cover.

    The kind and the date are read from ONE match rather than two, so a document
    cannot be accepted as a general because the word "General" appears somewhere
    else on a page that is dated something else.
    """
    for text in pages:
        flat = _compact(text)
        match = _SOV_KIND.search(flat)
        if match is None:
            continue
        day = _SOV_DAY.match(flat, match.end())
        if day is None:
            continue
        month = _MONTHS[day.group(1).lower()]
        try:
            return match.group(1).lower(), date(int(day.group(3)), month, int(day.group(2)))
        except ValueError as exc:
            raise SchemaDrift(f"CT: {day.group(0)!r} is not a date") from exc
    raise SchemaDrift(
        "CT: the Statement of Vote carries no 'Statement of Vote <type> Election "
        "<Month> <D>, <YYYY>' cover line, which is the only thing that says which "
        "election it is"
    )


def sov_statistics(pages: list[str]) -> list[str]:
    """The pages of the SDR/turnout/absentee/early statistics table.

    A page qualifies only if all thirteen headings are present AND IN ORDER --
    the counts are consumed positionally, so see the note on SOV_COLUMN_TAGS.
    """
    out: list[str] = []
    for text in pages:
        flat = _compact(text)
        seen = [(flat.find(tag), tag) for tag in SOV_COLUMN_TAGS]
        if any(where < 0 for where, _ in seen):
            continue
        if seen != sorted(seen):
            raise SchemaDrift(
                "CT: a Statement of Vote statistics page prints its columns in "
                f"the order {[tag for _, tag in sorted(seen)]}, not the order "
                "this parser reads them by position"
            )
        out.append(text)
    if not out:
        raise SchemaDrift(
            "CT: the Statement of Vote has no page carrying the SDR/turnout/"
            "absentee/early statistics table"
        )
    return out


def _sov_name(pending: str, prefix: str) -> str:
    """A row's town name, compacted, with the page furniture cut off the front.

    pypdf runs a page's whole header block -- and, on the continuation pages,
    the table's caption -- together with the first data row, and it splits
    "North Stonington" across two lines. Both are handled here rather than by
    guessing at the name end: cut everything up to the LAST header heading, then
    drop a leading caption. What is left must match a town EXACTLY.
    """
    candidate = _compact(f"{pending} {prefix}").lower()
    cut = candidate.rfind(_SOV_HEADER_END)
    if cut >= 0:
        candidate = candidate[cut + len(_SOV_HEADER_END):]
    if candidate.startswith(_SOV_CAPTION):
        candidate = candidate[len(_SOV_CAPTION):]
    return candidate


def sov_rows(
    pages: list[str],
) -> tuple[dict[str, list[int]], dict[str, str], list[int] | None]:
    """({town GEOID: eleven counts}, {GEOID: census name}, the totals row or None)."""
    index = sov_index()
    towns: dict[str, list[int]] = {}
    names: dict[str, str] = {}
    totals: list[int] | None = None
    for text in pages:
        pending = ""
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            match = _SOV_ROW.match(line)
            if match is None:
                pending = f"{pending} {line}".strip()
                continue
            candidate = _sov_name(pending, match.group("name"))
            pending = ""
            counts = [
                _sov_int(match.group("listed")), _sov_int(match.group("voted")),
                *(_sov_int(value) for value in match.group("counts").split()),
            ]
            if not candidate:
                totals = counts
                continue
            hit = index.get(candidate)
            if hit is None:
                raise SchemaDrift(
                    f"CT: the Statement of Vote row {candidate!r} is not one of "
                    f"Connecticut's {SOV_TOWNS} towns"
                )
            geoid, name = hit
            if geoid in towns:
                raise SchemaDrift(f"CT: {name} appears twice in the statistics table")
            towns[geoid] = counts
            names[geoid] = name
    if len(towns) != SOV_TOWNS:
        raise SchemaDrift(
            f"CT: the statistics table produced {len(towns)} of {SOV_TOWNS} towns; "
            f"Connecticut's towns are the whole state, so a missing one is a page "
            f"or a name this parser no longer reads, not a town yet to report"
        )
    return towns, names, totals


def _sov_int(raw: str) -> int:
    try:
        return int(raw.replace(",", ""))
    except ValueError as exc:  # pragma: no cover - _SOV_ROW only matches digits
        raise SchemaDrift(f"CT: {raw!r} is not a count") from exc


#: The eleven counts a statistics row carries, in order: SOV_COLUMN_TAGS minus
#: the town name and minus the percentage, which `_SOV_ROW` consumes separately.
#: DERIVED from the tag list rather than written out again, so the positions the
#: values are read at cannot drift away from the header order that is checked.
_SOV_COUNT_TAGS: tuple[str, ...] = tuple(
    tag for tag in SOV_COLUMN_TAGS
    if tag not in ("Town", "PercentageCheckedasHavingVoted")
)
_SOV_COUNTS = len(_SOV_COUNT_TAGS)
_ABS_VOTED = _SOV_COUNT_TAGS.index("NumberofAbsenteeBallotsVoted")
_EARLY_VOTED = _SOV_COUNT_TAGS.index("NumberofEarlyBallotsVoted")

#: No party breakdown and no issued count in this document. None, never 0.
_SOV_BLANKS = {
    "party_dem": None, "party_rep": None, "party_oth": None, "party_npa": None,
}


def sov_build(body: bytes, cycle: int) -> FetchResult:
    """Canonical rows for one certified Statement of Vote.

    Every row is dated the election's own day, which is why `fetch_history`
    refuses a cycle whose election has not happened -- see the guard there.
    """
    pages = sov_pages(body)
    kind, day = sov_election(pages)
    if kind != "general" or day != election_date(cycle):
        raise NotYetPublished(
            f"CT: this Statement of Vote is the {day.isoformat()} {kind} election, "
            f"not the {cycle} general ({election_date(cycle).isoformat()})"
        )

    towns, names, totals = sov_rows(sov_statistics(pages))
    summed = [sum(counts[i] for counts in towns.values()) for i in range(_SOV_COUNTS)]
    if totals is None:
        raise SchemaDrift(
            "CT: the statistics table printed no TOTAL row, which is the only "
            "thing that proves no town was dropped between the last row read and "
            "the end of the table"
        )
    if summed != totals:
        raise SchemaDrift(
            f"CT: the Statement of Vote's own totals row {totals} is not the sum "
            f"of its {len(towns)} town rows {summed}"
        )

    result = FetchResult()
    town_rows: list[TownDay] = []
    by_county: dict[str, list[int]] = defaultdict(lambda: [0] * _SOV_COUNTS)
    for geoid, counts in sorted(towns.items()):
        town_rows.append(TownDay(
            cycle=cycle, state="CT", town_geoid=geoid, day=day,
            town_name=names.get(geoid, ""),
            ballots_total=counts[_ABS_VOTED] + counts[_EARLY_VOTED],
            # One certified snapshot, so there is no day-over-day change to
            # report. None, never 0.
            ballots_new=None,
            mail_returned=counts[_ABS_VOTED],
            inperson=counts[_EARLY_VOTED],
            **_SOV_BLANKS,
        ))
        running = by_county[_towns.county_of(geoid)]
        for position, value in enumerate(counts):
            running[position] += value
    _towns.attach(result, town_rows)

    for fips, counts in sorted(by_county.items()):
        result.county_rows.append(CountyDay(
            cycle=cycle, state="CT", county_fips=fips, day=day,
            county_name=_towns.county_name_of("CT", fips),
            ballots_total=counts[_ABS_VOTED] + counts[_EARLY_VOTED],
            ballots_new=None,
            mail_returned=counts[_ABS_VOTED],
            inperson=counts[_EARLY_VOTED],
            **_SOV_BLANKS,
        ))

    result.state_rows.append(StateDay(
        cycle=cycle, state="CT", day=day,
        ballots_total=totals[_ABS_VOTED] + totals[_EARLY_VOTED],
        ballots_new=None,
        # The Statement of Vote never says how many absentee ballots were
        # ISSUED; "Received from Town Clerk" is ballots returned. None, never 0.
        mail_requested=None,
        mail_returned=totals[_ABS_VOTED],
        inperson=totals[_EARLY_VOTED],
        **_SOV_BLANKS,
    ))
    return result


class CTScraper(Adapter):
    """Tier 1 for Connecticut: the SOTS per-ballot early-voting and absentee files.

    Statewide, town and county rows, with party and a mail/in-person split.
    Towns are the real unit in Connecticut and the counties are summed from
    them; see the module docstring for what does not map and where it goes.
    """

    state = "CT"
    name = "ct-sots"
    tier = TIER_SCRAPER

    def _stamped(self, result: FetchResult) -> FetchResult:
        """Stamp the town rows -- `FetchResult.stamp()` does not know about them."""
        return _towns.stamp(result, self.provenance())

    def _index(self, cycle: int) -> dict[str, list[tuple[date, str]]]:
        url = INDEX_URL.format(cycle=cycle)
        try:
            page = get(url, state="CT", filename=f"{cycle}-voter-data.html")
        except Missing as exc:
            raise SourceError(f"CT: {url} is gone") from exc
        if _is_soft_404(page):
            # We could not LOOK. That is a fall-through, never an absence.
            raise SourceError(f"CT: {url} answered portal.ct.gov's 404 page")
        return index_files(page, cycle)

    def _download(self, url: str) -> bytes | None:
        try:
            body = get(url, state="CT", filename=url.rsplit("/", 1)[-1], min_bytes=1024)
        except Missing:
            return None
        return None if looks_like_html(body) else body

    def _pick(self, index: dict[str, list[tuple[date, str]]], kind: str,
              cycle: int, as_of: date) -> tuple[date, bytes] | None:
        """The newest usable file of one kind at or before `as_of`."""
        earliest = as_of - timedelta(days=LOOKBACK_DAYS)
        for day, url in index.get(kind, ()):
            if not (earliest <= day <= as_of) or not in_file_window(day, cycle):
                continue
            body = self._download(url)
            if body is not None:
                return day, body
        # Only if the index named nothing usable: Connecticut has changed the
        # filename convention mid-cycle before, so a page that has not been
        # updated is not proof the file is absent.
        day = as_of
        while day >= earliest:
            if in_file_window(day, cycle):
                for url in constructed(cycle, kind, day):
                    body = self._download(url)
                    if body is not None:
                        log.info("CT: %s file found by pattern at %s", kind, url)
                        return day, body
            day -= timedelta(days=1)
        return None

    def fetch(self, cycle: int, as_of: date) -> FetchResult:
        if not in_file_window(as_of, cycle):
            raise NotYetPublished(
                f"CT: {as_of.isoformat()} is outside the {cycle} general's "
                f"reporting window (opens {FILE_WINDOW_DAYS} days out)"
            )
        index = self._index(cycle)
        found: list[tuple[str, bytes]] = []
        dates: list[date] = []
        for kind in ("mail", "inperson"):
            hit = self._pick(index, kind, cycle, as_of)
            if hit is None:
                continue
            day, body = hit
            found.append((kind, body))
            dates.append(day)

        # ⚠️ GUARD PARITY WITH `require_both`, FROM THE OTHER SIDE.
        #
        # `require_both` refuses an early-voting file with no absentee partner.
        # This refuses the mirror image: a kind the index page POSITIVELY LISTS
        # for this general, that we could not download. Without it, a day on
        # which the early-voting workbook 404s while the absentee one serves
        # would publish an absentee-only total -- 58 back down to 30 on the
        # September 2026 fixtures -- and a cumulative curve may never fall.
        #
        # "Listed" is the whole test and it is deliberately wider than
        # `_pick`'s lookback: if Connecticut's own page says a file exists for
        # this election and we have not got it, we could not LOOK, and "we could
        # not look" is a fall-through, never an absence. A kind the page does
        # not list at all is the legitimate case -- early voting has not opened
        # yet -- and is left to `require_both`.
        listed = {
            kind for kind in ("mail", "inperson")
            if any(day <= as_of and in_file_window(day, cycle)
                   for day, _ in index.get(kind, ()))
        }
        missed = sorted(listed - {kind for kind, _ in found})
        if missed:
            raise SourceError(
                f"CT: the {cycle} voter-data page lists a {' and a '.join(missed)} "
                f"file for this election and none of them could be downloaded; "
                f"publishing the other method alone would drop a whole method "
                f"out of a cumulative total"
            )

        if not found:
            raise NotYetPublished(
                f"CT: no {cycle} general ballot file posted on or before "
                f"{as_of.isoformat()}"
            )
        # The series can only speak for the OLDER of the two files: the newer
        # one's extra days would otherwise be published with the other method
        # frozen, which reads as a day on which nobody voted by mail.
        through = min(dates)
        log.info("CT: read %s through %s", [k for k, _ in found], through.isoformat())
        return self._stamped(build(found, cycle, through))

    def fetch_history(self, cycle: int) -> FetchResult:
        """A past cycle's certified FINAL, out of the Statement of Vote.

        ⚠️ A FINAL, NOT A CURVE, AND THE DAILY FILES REALLY ARE GONE.

        The per-ballot workbooks the live path reads are deleted when the next
        election starts, and that is measured rather than assumed. Probed live
        on 2026-09-09, every one answering portal.ct.gov's soft 404 (HTTP 200
        with `<title>404 Error Page</title>`, 21,880 B) against a control -- the
        current `2026_absentee_ballot_data/absentee_ballot_09032026.xlsx` -- that
        served 47,911 B of real xlsx from the same directory shape:

          * `2024_absentee_ballot_data/`, every date 2024-10-01..2024-11-12 in
            all four filename conventions (`absentee_ballot_MMDDYYYY`,
            `early_voting_MMDDYYYY`, `abs_detail_MMDDYY`, `ev_detail_MMDDYY`) --
            172 URLs, 172 soft 404s;
          * the same for `2025_absentee_ballot_data/` over the 2025 municipal
            election's own window, 2025-10-28..2025-11-04 -- 32 more. That is the
            control that turns "we guessed the 2024 name wrong" into "Connecticut
            keeps one election's files": the 2025 files are ten months old and
            equally gone;
          * `2024/absentee_ballot_data/`, `2024-absentee-ballot-data/`,
            `2024_voter_data/`, `2024_early_voting_data/`, `2024_ballot_data/`,
            `absentee_ballot_data/` and `early-voting/`.

        The index pages are gone too: `/sots/election-services/2024-voter-data`
        and `-2022-` and `-2025-` all answer the soft 404, with and without
        Sitecore's `?archived=true`, and the Wayback Machine holds ZERO captures
        of any file under `portal.ct.gov/-/media/sots/` matching
        `absentee_ballot|early_voting|abs_detail` -- including the 2026 ones that
        are live right now, which is what makes the archive silent rather than
        negative.

        So there is no 2024 daily series, and this returns the certified
        Statement of Vote's town table instead: 169 towns and 8 counties dated
        Election Day, which is the denominator the site's "% of its 2024 early
        vote" column wants and is NOT a series the counterfactual can pair
        against a 2026 day.

        ⚠️ GUARD PARITY, the same one `nd.py` carries. `fetch` stamps its rows
        with the file's own date, which cannot be in the future. This path stamps
        every row with ELECTION DAY, so a backfill of a running cycle would land
        today's position -- or, worse, a Statement of Vote that does not exist --
        at days_to_election 0 and make it that cycle's final. Refused by name.
        """
        url = STATEMENT_OF_VOTE.get(int(cycle))
        if url is None:
            raise NotYetPublished(
                f"CT: no Statement of Vote is published for {cycle} under the "
                f"naming this module has verified, and the daily ballot files "
                f"for a past election are deleted; there is nothing to backfill"
            )
        day_zero = election_date(cycle)
        if day_zero > date.today():
            raise NotYetPublished(
                f"CT: the {cycle} general has not happened yet -- this path dates "
                f"every row {day_zero.isoformat()}, so backfilling a running cycle "
                f"would publish a certified final that does not exist. The daily "
                f"`fetch` is what tracks {cycle}."
            )
        try:
            body = get(url, state="CT", filename=f"{cycle}_statement_of_vote.pdf",
                       min_bytes=4096)
        except Missing as exc:
            raise SourceError(f"CT: {url} is gone") from exc
        if looks_like_html(body) or not body.startswith(b"%PDF"):
            # portal.ct.gov answers a missing media item with HTTP 200 and its
            # 404 page, so "did we get a PDF" is the only real status check.
            raise SourceError(
                f"CT: {url} did not return a PDF ({len(body)} bytes); "
                f"portal.ct.gov serves its 404 page with HTTP 200"
            )
        return self._stamped(sov_build(body, cycle))
