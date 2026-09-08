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

import json
import logging
import re
from datetime import date, datetime

from ..normalize import PARTY_DEM, PARTY_NPA, PARTY_OTH, PARTY_REP, party as _party
from ..schema import TIER_SCRAPER, StateDay
from ._net import Missing, get
from .base import Adapter, FetchResult, NotYetPublished, SchemaDrift, SourceError

log = logging.getLogger(__name__)

#: VERIFIED on this network 2026-09-06: this URL returns the live 2026 page,
#: 117,370 bytes, carrying the June primary's and the July run-off's tables.
BASE = "https://sdsos.gov"
PAGE = (BASE + "/elections-voting/upcoming-elections/general-information/"
        "{cycle}%20Election%20Information/{cycle}-Election-Absentee-Data.aspx")

#: ⚠️ CORRECTION, MEASURED 2026-09-08. This module used to record that "the same
#: path for 2022 and 2024 redirects to sdsos.gov/404.aspx (with HTTP 200), so
#: there is no archive to backfill and `fetch_history` correctly finds none."
#: The first half is true and the conclusion does not follow: PAST cycles were
#: never published at the 2026 path. They lived at a DIFFERENT one --
#: `.../general-information/{cycle}/{cycle}-General-Election-Absentee-Numbers.aspx`
#: -- which the Wayback Machine holds, and which `parse` reads unchanged.
#:
#: The live host really does serve nothing: both cycles' URLs answer 200 with
#: the same 67,597-byte soft-404 page (verified 2026-09-08, plain `requests`,
#: no tables on it), which is why the archive is the only route. Reading it
#: there is the same route `fl.py`, `de.py`, `or.py` and `wa.py` take.
ARCHIVED_PAGE = (BASE + "/elections-voting/upcoming-elections/general-information/"
                 "{cycle}/{cycle}-General-Election-Absentee-Numbers.aspx")

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


#: The Wayback timestamp, `YYYYMMDDhhmmss`.
_WAYBACK_STAMP = re.compile(r"^(\d{4})(\d{2})(\d{2})\d{6}$")


def _stamp_day(stamp: str) -> date:
    """The date a capture was TAKEN, which is that capture's own as-of."""
    m = _WAYBACK_STAMP.match(str(stamp).strip())
    if m is None:
        raise SourceError(f"SD: {stamp!r} is not a Wayback timestamp")
    year, month, day = (int(g) for g in m.groups())
    try:
        return date(year, month, day)
    except ValueError as exc:
        raise SourceError(f"SD: {stamp!r} is not a Wayback timestamp") from exc


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


# --------------------------------------------------------------------------
# The archived series. See the note on ARCHIVED_PAGE.
# --------------------------------------------------------------------------
CDX_URL = "http://web.archive.org/cdx/search/cdx"

WAYBACK_SNAPSHOT = "https://web.archive.org/web/{stamp}id_/{url}"

#: web.archive.org is slower and less tolerant than a state host.
ARCHIVE_MIN_INTERVAL = 1.0

MAX_ARCHIVE_PROBES = 60

#: Cycles whose archived page this module will read, and it is deliberately NOT
#: "every cycle the Archive holds".
#:
#: **2024 is in, and it is clean.** Six distinct captures (measured 2026-09-08),
#: of which `20241106004829` -- taken the day after the election -- carries the
#: complete weekly series, 2024-09-20 through 2024-11-01, in EXACTLY the
#: vocabulary this parser already knows: `Date | Ballots Sent | Ballots Received
#: | Walk-in Voters | UOCAVA`, with a `Party Breakout ... as of 11/1/2024`.
#:
#: **2022 is OUT, and the reason is rule 3 rather than reachability.** Its page
#: is archived and fetches fine, and it is a DIFFERENT report in two ways that
#: this parser would have to be taught rather than allowed to guess at:
#:
#:   * its date table's fifth column is headed `Military`, not `UOCAVA`, and its
#:     party table is headed `Party | Ballots Mailed | Mail Ballots Received |
#:     In-Person Voting | UOCAVA` rather than the four labels above. Those party
#:     columns DO reconcile to the date table exactly (25,373+11,189+229+4,455+
#:     115+45,706 = 87,067 sent; the received column sums to 81,142 and the
#:     in-person one to 62,105, all three matching the 11/4/22 row), so the
#:     mapping is knowable -- it is simply not yet written or fixture-tested.
#:   * the page carries a row South Dakota itself disowns: a bare line reading
#:     "Incorrect numbers reported for 10/7/2022" sits between the 10/7 row
#:     (36,448 sent / 35,241 received) and a 10/12 row that is LOWER on every
#:     measure (28,883 / 18,615). Publishing the 10/7 row would put a spike in a
#:     cumulative series that the source has explicitly retracted, and nothing
#:     in this parser currently reads that note.
#:
#: So 2022 wants measuring and fixture-testing the way this cycle's vocabulary
#: was, not a mapping invented under time pressure. Recorded here rather than
#: silently omitted.
ARCHIVED_CYCLES = (2024,)


def archive_stamps(cycle: int) -> list[str]:
    """Wayback timestamps of every distinct version of a past cycle's page."""
    url = ARCHIVED_PAGE.format(cycle=cycle)
    query = {
        "url": url, "output": "json", "fl": "timestamp,statuscode,digest",
        "filter": "statuscode:200", "collapse": "digest",
        "limit": str(MAX_ARCHIVE_PROBES),
    }
    try:
        body = get(CDX_URL, state="SD", filename=f"cdx-absentee-{cycle}.json",
                   params=query, min_bytes=2, min_interval=ARCHIVE_MIN_INTERVAL)
    except Missing:
        # The CDX API answers "nothing archived" with an EMPTY body, which
        # _net.get reports as Missing. That is absence, not a fault.
        return []
    try:
        rows = json.loads(body.decode("utf-8", errors="replace"))
    except ValueError as exc:
        raise SourceError(f"SD: the Wayback CDX index was not JSON: {exc}") from exc
    return sorted(str(row[0]) for row in rows[1:])


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

    def fetch_history(self, cycle: int) -> FetchResult:
        """A past cycle's weekly series, from the Internet Archive.

        STATEWIDE ONLY, exactly as the live path is -- South Dakota publishes no
        county breakdown of absentee voting in any cycle, so `county_rows` is
        empty here too. See the module docstring.

        ⚠️ GUARD PARITY WITH `fetch`. Every capture goes through the SAME
        `parse`, so the heading gate that refuses a primary or a run-off, the
        two header checks and the party-label check all run here as they do
        live. The only difference is the `as_of`, and it is the CAPTURE'S OWN
        DATE, so the "not after the run date" filter keeps doing real work.

        Which cycles are eligible is a deliberate list, not "whatever is
        archived" -- see ARCHIVED_CYCLES for why 2022's page is left alone.
        """
        if int(cycle) not in ARCHIVED_CYCLES:
            raise NotYetPublished(
                f"SD: {cycle} is not one of the archived cycles this module "
                f"reads ({', '.join(str(c) for c in ARCHIVED_CYCLES)})"
            )
        url = ARCHIVED_PAGE.format(cycle=cycle)
        by_day: dict[date, StateDay] = {}
        drift: SchemaDrift | None = None
        seen = 0
        for stamp in archive_stamps(cycle):
            try:
                body = get(WAYBACK_SNAPSHOT.format(stamp=stamp, url=url),
                           state="SD", filename=f"absentee-{cycle}-{stamp}.html",
                           use_cache=True, min_bytes=2048,
                           min_interval=ARCHIVE_MIN_INTERVAL)
            except SourceError as exc:
                log.debug("SD: archived capture %s unusable (%s)", stamp, exc)
                continue
            seen += 1
            try:
                captured = parse(body, cycle, _stamp_day(stamp))
            except NotYetPublished as exc:
                log.debug("SD: capture %s skipped (%s)", stamp, exc)
                continue
            except SchemaDrift as exc:
                log.warning("SD: capture %s did not parse (%s)", stamp, exc)
                drift = exc
                continue
            except SourceError as exc:
                log.debug("SD: capture %s unreadable (%s)", stamp, exc)
                continue
            for row in captured.state_rows:
                # A later capture of the same weekly date is the truer reading,
                # and it is also the one that carries the party breakout.
                existing = by_day.get(row.day)
                if existing is None or row.party_rep is not None:
                    by_day[row.day] = row

        if not by_day:
            if drift is not None:
                raise drift
            raise NotYetPublished(
                f"SD: nothing archived for the {cycle} general ({seen} captures read)"
            )
        log.info("SD: %d archived weekly rows from %d captures", len(by_day), seen)
        return FetchResult(state_rows=[by_day[d] for d in sorted(by_day)])
