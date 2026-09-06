"""South Dakota: the Secretary of State's absentee statistics page.

One ASP.NET page per cycle carries every 2026 election's absentee numbers, each
as a pair of hand-typed HTML tables under a bold heading that names the election:

```
South Dakota 2026 Primary Election Absentee Ballot Weekly Statistics
  Date | Ballots Sent | Ballots Received | Walk-in Voters | UOCAVA
  5/1/26 |  5,058 |  2,893 |  2,664 | 157
  6/2/26 | 33,425 | 32,453 | 28,612 | 221
Party Breakout on Absentee Ballot Statistics as of 6/2/26
  Party | Ballots Sent | Ballots Received | Walk-in Voters | UOCAVA
  DEM   |  4,454 |  4,201 |  3,596 |  70
  REP   | 26,654 | 26,125 | 23,175 | 104
```

The date table is CUMULATIVE and keeps every prior date, so one fetch
reconstructs the whole curve; the party table is a snapshot of the latest date
only, so the party series accumulates one row per daily run.

**Statewide only.** South Dakota publishes no county breakdown of any of this, so
`county_rows` is empty -- which `base.py` is explicit is not an error.

Four things drive the code below.

* **The page holds several elections at once, and the heading is the only thing
  that tells them apart.** Today it carries the June primary and a July run-off;
  the November general's section will appear beside them, not instead of them. A
  section is used only if its heading names this cycle's GENERAL election, and
  anything that says primary or run-off is refused. Reading the wrong one would
  publish a Republican run-off's 26,957 ballots as the general's.

* **`Ballots Received` is South Dakota's own total and INCLUDES walk-ins.** The
  page's own legend says so. There is therefore no mail-only return count on this
  page at all, and `mail_returned` stays None rather than being derived by
  subtraction -- a walk-in voter may "take home and return", so the difference is
  not a clean mail number. `ballots_total` is `Ballots Received`, `inperson` is
  `Walk-in Voters`, and `mail_requested` is `Ballots Sent`.

* **South Dakota registers by party, and the file splits six ways.** DEM and REP
  map straight across; IND and NPA are both "no party" and are added together;
  LIB and OTH are both third parties and are added together. A party label
  `normalize.party()` does not recognise raises SchemaDrift.

* **The tables are typed by hand, and it shows.** On 2026-07-27 the run-off's
  Walk-in cell reads `21.718` -- a period where a comma belongs. A fractional
  ballot count cannot exist, so a value of the shape `21.718` is read as the
  grouped integer it plainly is; anything else non-numeric still raises. The
  same hand-typing means the party table and the date table can disagree by a
  few ballots on the same day (26,275 against 26,257 on 2026-07-28), so neither
  is ever used to check the other.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime

from ..normalize import PARTY_DEM, PARTY_NPA, PARTY_OTH, PARTY_REP, party as _party
from ..schema import TIER_SCRAPER, StateDay
from ._net import Missing, get
from .base import Adapter, FetchResult, NotYetPublished, SchemaDrift, SourceError

log = logging.getLogger(__name__)

#: VERIFIED on this network 2026-09-06: this URL returns the live 2026 page,
#: 117,370 bytes, carrying the June primary's and the July run-off's tables. The
#: same path for 2022 and 2024 redirects to sdsos.gov/404.aspx (with HTTP 200),
#: so there is no archive to backfill and `fetch_history` correctly finds none.
BASE = "https://sdsos.gov"
PAGE = (BASE + "/elections-voting/upcoming-elections/general-information/"
        "{cycle}%20Election%20Information/{cycle}-Election-Absentee-Data.aspx")

#: The bold heading above a section's date table. UNVERIFIED for the general:
#: the two sections on the page today read "South Dakota 2026 Primary Election
#: Absentee Ballot Weekly Statistics" and "South Dakota 2026 Run-off Election
#: Absentee Ballot Weekly Statistics", so the general's is expected to read
#: "...2026 General Election Absentee Ballot Weekly Statistics". We therefore
#: match on the words rather than the whole string, and refuse anything that
#: names a primary or a run-off.
SECTION_WORDS = ("south dakota", "general election", "absentee")
OTHER_ELECTIONS = ("primary", "run-off", "runoff", "special", "municipal")

#: The party snapshot that follows a section's date table, e.g.
#: "Party Breakout on Absentee Ballot Statistics as of 7/28/26".
PARTY_HEADING = re.compile(r"party breakout.*?as of\s*(\d{1,2}/\d{1,2}/\d{2,4})", re.I)

DATE_HEADER = ("date", "ballots sent", "ballots received", "walk-in voters", "uocava")
PARTY_HEADER = ("party",) + DATE_HEADER[1:]

#: Column offsets inside a table row, after the Party/Date key column.
RECEIVED = 2

#: Section headings are read as TEXT NODES, not as <strong> elements. The site's
#: collapsed navigation menu leaves a <strong> unclosed, so a non-greedy
#: <strong>...</strong> swallows the first heading in the content -- verified on
#: the live page, where the run-off's heading disappears that way. A heading sits
#: in one text node, so ">...<" finds it whatever the surrounding tags do.
_LABEL = re.compile(r">([^<>]+)<")
_TABLE = re.compile(r"<table[^>]*>.*?</table>", re.S | re.I)
_ROW = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
_CELL = re.compile(r"<t[hd][^>]*>(.*?)</t[hd]>", re.S | re.I)
_TAG = re.compile(r"<[^>]+>")
_COMMENT = re.compile(r"<!--.*?-->", re.S)

#: A count typed with thousands separators. A ballot count cannot be fractional,
#: so "21.718" is 21,718 -- the SoS typed a period instead of a comma on
#: 2026-07-27 and `int(float("21.718"))` would publish 21.
_GROUPED = re.compile(r"^\d{1,3}(?:[.,]\d{3})+$")

_PARTY_FIELD = {
    PARTY_DEM: "party_dem", PARTY_REP: "party_rep",
    PARTY_NPA: "party_npa", PARTY_OTH: "party_oth",
}


def _text(raw: str) -> str:
    text = _TAG.sub(" ", _COMMENT.sub(" ", raw or ""))
    for entity, char in (("&nbsp;", " "), ("&amp;", "&"), ("&rsquo;", "'"),
                         ("&#160;", " ")):
        text = text.replace(entity, char)
    return " ".join(text.split())


def _norm(raw: str) -> str:
    return _text(raw).lower()


def _count(raw: str) -> int | None:
    """A cell of the statistics tables, or None if it is blank."""
    text = _text(raw)
    if not text or text in {"-", "--", "N/A", "n/a"}:
        return None
    if _GROUPED.match(text):
        return int(re.sub(r"[.,]", "", text))
    if re.fullmatch(r"\d+", text):
        return int(text)
    raise SchemaDrift(f"SD: {raw!r} is not a ballot count")


def _add(*values: int | None) -> int | None:
    known = [v for v in values if v is not None]
    return sum(known) if known else None


def _stamp(raw: str) -> date:
    """A "7/28/26" or "05/26/26" cell."""
    text = _text(raw)
    for fmt in ("%m/%d/%y", "%m/%d/%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise SchemaDrift(f"SD: {raw!r} is not a report date")


def _rows(table: str) -> list[list[str]]:
    return [[c for c in _CELL.findall(row)] for row in _ROW.findall(table)]


def _table_after(page: str, position: int) -> tuple[str, int] | None:
    m = _TABLE.search(page, position)
    return (m.group(0), m.end()) if m else None


def sections(page: str) -> list[tuple[int, str]]:
    """(position, text) for every text node on the page that looks like a heading."""
    out: list[tuple[int, str]] = []
    for m in _LABEL.finditer(page or ""):
        text = _text(m.group(1))
        low = text.lower()
        if 10 <= len(text) <= 200 and ("statistics" in low or "breakout" in low):
            out.append((m.start(), text))
    return out


def find_general(page: str, cycle: int) -> int:
    """Where this cycle's GENERAL-election section starts, or raise.

    The page carries several elections at once and the heading is the only thing
    that distinguishes them, so a heading naming a primary or a run-off is not
    merely skipped -- it is never eligible.
    """
    for position, heading in sections(page):
        low = heading.lower()
        if str(cycle) not in low or any(w in low for w in OTHER_ELECTIONS):
            continue
        if all(word in low for word in SECTION_WORDS):
            return position
    raise NotYetPublished(
        f"SD: the absentee statistics page has no {cycle} general-election section "
        f"yet (it has {[h for _, h in sections(page)]})"
    )


def parse_dates_table(table: str) -> list[tuple[date, list[int | None]]]:
    """The cumulative Date/Sent/Received/Walk-in/UOCAVA table."""
    rows = _rows(table)
    if not rows:
        raise SchemaDrift("SD: the general-election section's first table has no rows")
    header = tuple(_norm(c) for c in rows[0])
    if header[:len(DATE_HEADER)] != DATE_HEADER:
        raise SchemaDrift(f"SD: absentee date table header is {list(header)}, "
                          f"expected {list(DATE_HEADER)}")
    out: list[tuple[date, list[int | None]]] = []
    for cells in rows[1:]:
        if len(cells) < len(DATE_HEADER) or not _text(cells[0]):
            continue
        out.append((_stamp(cells[0]), [_count(c) for c in cells[1:len(DATE_HEADER)]]))
    if not out:
        raise NotYetPublished("SD: the general-election date table has no rows yet")
    return out


def parse_party_table(table: str) -> dict[str, int | None]:
    """The party snapshot, as canonical bucket -> ballots received."""
    rows = _rows(table)
    header = tuple(_norm(c) for c in rows[0]) if rows else ()
    if header[:len(PARTY_HEADER)] != PARTY_HEADER:
        raise SchemaDrift(f"SD: party breakout header is {list(header)}, "
                          f"expected {list(PARTY_HEADER)}")
    buckets: dict[str, int | None] = {}
    for cells in rows[1:]:
        label = _text(cells[0]) if cells else ""
        if not label or len(cells) < len(PARTY_HEADER):
            continue
        bucket = _party(label)
        if bucket is None:
            raise SchemaDrift(f"SD: unrecognised party registration {label!r}")
        # IND and NPA are both "no party"; LIB and OTH are both third parties.
        # Adding them is required, not optional -- dropping either loses ballots.
        buckets[bucket] = _add(buckets.get(bucket), _count(cells[RECEIVED]))
    if not buckets:
        raise SchemaDrift("SD: party breakout table has no party rows")
    return buckets


def parse(body: bytes, cycle: int, as_of: date) -> FetchResult:
    """Parse the absentee statistics page into statewide daily rows."""
    page = body.decode("utf-8", errors="replace")
    if "<table" not in page.lower():
        # `fetch` has already rejected anything that is not a web page; this
        # catches a page that IS one but carries none of the statistics tables.
        raise SourceError("SD: the absentee statistics page has no tables on it")

    start = find_general(page, cycle)
    first = _table_after(page, start)
    if first is None:
        raise SchemaDrift("SD: the general-election heading is followed by no table")
    dates_table, after = first

    party_day: date | None = None
    party: dict[str, int | None] = {}
    for position, heading in sections(page):
        if position < after:
            continue
        m = PARTY_HEADING.search(heading)
        if m is None:
            # The next election's own heading; the general's party table, if it
            # exists at all, must come before it.
            if any(w in heading.lower() for w in OTHER_ELECTIONS):
                break
            continue
        found = _table_after(page, position)
        if found is not None:
            party_day = _stamp(m.group(1))
            party = parse_party_table(found[0])
        break

    result = FetchResult()
    for day, (sent, received, walkin, _uocava) in parse_dates_table(dates_table):
        if day > as_of:
            continue
        row = StateDay(
            cycle=cycle, state="SD", day=day,
            ballots_total=received,
            # South Dakota's "Ballots Received" already includes walk-ins and it
            # publishes no mail-only return count, so this stays blank rather
            # than being derived by subtraction. See THE BLANK RULE.
            mail_requested=sent,
            mail_returned=None,
            inperson=walkin,
        )
        if party_day == day:
            for bucket, field in _PARTY_FIELD.items():
                setattr(row, field, party.get(bucket))
        result.state_rows.append(row)

    if not result.state_rows:
        raise NotYetPublished(
            f"SD: the {cycle} general-election table has no dates on or before "
            f"{as_of.isoformat()}")
    return result


class SDScraper(Adapter):
    """Tier 1 for South Dakota: the SoS's absentee statistics page (statewide)."""

    state = "SD"
    name = "sd-sos"
    tier = TIER_SCRAPER

    def fetch(self, cycle: int, as_of: date) -> FetchResult:
        url = PAGE.format(cycle=cycle)
        try:
            body = get(url, state="SD", filename=f"absentee_{cycle}.html",
                       min_bytes=2048)
        except Missing as exc:
            raise NotYetPublished(f"SD: {exc}") from exc
        # A cycle with no page 302s to sdsos.gov/404.aspx, which answers 200 with
        # a real page -- so the miss shows up as "no general-election section",
        # which is exactly what NotYetPublished means here. `parse` rejects a
        # body with no <table> in it, which is the check that matters.
        return parse(body, cycle, as_of)
