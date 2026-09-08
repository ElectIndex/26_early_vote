"""Alaska: the Division of Elections' "Election Statistics" table.

Alaska has an OPEN GOVERNORSHIP and a Senate race in 2026, and the earlier
coverage survey rejected it on geography: the Division's near-daily *Combined
Ballot Count Report* is keyed by the forty state HOUSE DISTRICTS, which do not
nest into boroughs or census areas, so the `_fips`-keyed county contract cannot
be met. That verdict still stands, and this module does not try to defeat it.

What the survey missed is that the Division publishes the same measurement a
second time, **statewide and in HTML**, on each election's own results page:

    https://www.elections.alaska.gov/election-results/e/?id=26genr

carries an "Election Statistics" block with Alaska's own two totals and an
eight-row `Ballot Type | Number Issued | Number Received` table. That is a
statewide-only source with a real method split, which `base.py` is explicit is
not an error, and it is exactly the shape `sd.py` publishes for South Dakota.

So: **`county_rows` is always empty.** Alaska's boroughs and census areas are in
`_fips`, but nothing in this source is keyed to them, and mapping forty House
districts onto thirty boroughs would be a guess dressed as geography.

Four things drive the code.

* **The election id is the gate, and it is unambiguous.** The block opens
  `<dt><strong>26PRIM Totals</strong></dt>`, so `24GENR` and `26PRIM` cannot be
  confused. Only `{yy}GENR` is eligible. Today `?id=26genr` returns 200 with no
  Election Statistics block at all, which is the NotYetPublished path this
  adapter runs every day until the Division posts one.

* **The table proves itself.** The eight ballot types sum to the two totals
  printed above them -- verified on all three fixtures, e.g. 2024's
  31+59,158+73,146+0+18,158+11,976+15,138+1,109 = 178,716 issued, and
  24+51,212+73,146+167+18,158+8,309+15,138+1,109 = 167,263 received. A table
  that does not reproduce Alaska's own totals is drift, not data.

* **"Questioned" is counted, but it is not a method.** A questioned ballot is
  cast in person when a voter's eligibility is in doubt; it is in Alaska's
  received total and belongs in neither `mail_returned` nor `inperson`. So
  `ballots_total` is always Alaska's own `Total Ballots Received`, never the sum
  of the two method buckets -- the two differ by exactly the questioned ballots.

* **Alaska registers voters by party, and this report does not carry it.** All
  four party fields are therefore None -- "not reported", not zero. See THE
  BLANK RULE in schema.py.

## The 2022 general: why this module still publishes nothing for it

`?id=22genr` serves a complete, well-formed Election Statistics block today --
110,549 issued / 100,877 received, eight ballot types that add up to both. What
it does NOT carry is any statement of what period those numbers cover, and the
difference between the two cycles is not cosmetic:

* 2024's block says `Statistics include Early Vote through 11/5/2024`. That is
  Alaska stating an as-of, and it is Election Day, so the row lands at
  days_to_election 0 beside every other state's Election-Day figure.
* 2022's block says no such thing. Its only date is `Table last updated
  September 13, 2024`, an edit stamp from twenty-two months later, which
  `report_day` reads and the window check in `parse` then rejects -- correctly.

VERIFIED 2026-09-08: the 2022 block DOES name a date, just not its own. Its
`Download Report` link is
`/doc/info/Combined%20Ballot%20Count%20Report_11.30.2022.pdf`, so the table is
the **11/30/2022 combined report** -- the certified final, taken twenty-two days
AFTER the election, after every late absentee had arrived. Dating it 2022-11-30
would be honest about the file and useless about the curve: it would put a
post-count final at days_to_election -22 on an axis whose whole purpose is
comparing cycles at the same distance from Election Day, next to a 2024 row that
is a genuine Election-Day snapshot. So the refusal STANDS, and the reason is the
semantics rather than a missing date.

**What is actually there for 2022, measured rather than assumed** (CDX,
2026-09-08). Alaska posted this report near-daily in 2022 and the Archive kept
about twenty of them under `/doc/info/Combined Ballot Count Report_M.D.2022.pdf`
-- 10.26, 10.27, 10.28, 10.31, 11.1 through 11.7, 11.9 through 11.12, 11.14,
11.16, 11.20, 11.22 and 11.30, all HTTP 200 `application/pdf`. The 11.30 one was
downloaded and read: its TOTALS block reproduces the 22GENR Election Statistics
table EXACTLY (mail 49,002 sent / 41,348 received, early 37,559, in-person
absentee 9,143, online 5,948/3,948, fax 49/37, special needs 815, FWAB 24/18,
questioned 8,009), which proves the PDF series and this HTML block are the same
measurement. **A real 2022 daily curve is therefore recoverable**, and it is the
only route to one.

It is not built here, and that is a deliberate call rather than an oversight.
The during-season files (52KB, PDF-1.6) and the post-election ones (76KB,
PDF-1.7) are two DIFFERENT layouts with different column sets, and pypdf's text
extraction of the during-season layout runs the columns together -- `48020` and
`49807` come back as the single token `4802049807`. Splitting that needs a
coordinate-based parse of a layout that changes mid-series, which is precisely
the situation rule 3 exists for: a mis-split column here would publish a
confident wrong Alaska curve. It wants measuring first, the way pa.py's
vocabulary was measured before a line of it was written.

**Unverified, and flagged here rather than assumed:** whether the Division
refreshes this block DAILY during the early-vote window. Every capture we can
see is post-election -- 24GENR's table says "Table last updated January 29,
2025" and 26PRIM's "August 31, 2026" -- and the Wayback Machine has no capture
of `?id=24genr` before 2024-12-07 to check against. The near-daily artefact in
2022 was the PDF; in 2024 the Division moved its HTML twin under
`/results/24GENR/`, which is behind a "Human Verification" wall that answers
**405 to every client, including a full browser fingerprint** (verified
2026-09-06; PDFs under the same directory return 200, so the wall is on HTML
only). If this block turns out to be posted only after the count, Alaska will
simply keep returning NotYetPublished through the season, which is the honest
outcome rather than a wrong one.
"""

from __future__ import annotations

import html
import logging
import re
from datetime import date, datetime, timedelta

from ..calendar import election_date
from ..normalize import METHOD_INPERSON, METHOD_MAIL, method as _method
from ..schema import TIER_SCRAPER, StateDay
from ._net import Missing, get
from .base import Adapter, FetchResult, NotYetPublished, SchemaDrift, SourceError

log = logging.getLogger(__name__)

#: VERIFIED live 2026-09-06, plain `requests`, no fingerprint needed: `22genr`
#: (200, 110,823 b), `24genr` (200, 109,271 b) and `26prim` (200, 100,868 b) all
#: carry an Election Statistics block; `26genr` (200, 86,431 b) exists and does
#: not. This host was never blocked -- it is `/results/*.html` under the same
#: domain that is walled, and this module never touches that path.
PAGE = "https://www.elections.alaska.gov/election-results/e/?id={cycle:02d}genr"

#: The accordion the statistics live in.
SECTION = "Election Statistics"

#: `<dt><strong>24GENR Totals</strong></dt>`. The two-digit year plus GENR is
#: the only thing that distinguishes a general from a primary, and it is exact.
_ELECTION_ID = re.compile(r"(\d{2})\s*(GENR|PRIM|REAA|SPEC\w*)\s+Totals", re.I)

#: `<dd>Total Ballots Issued: <em>178,716</em></dd>`
_TOTAL = re.compile(r"total ballots (issued|received)\s*:?\s*([\d,]+)", re.I)

#: `<dd>Table last updated January 29, 2025 at 3:02 pm.</dd>` -- the edit stamp,
#: and the as-of date during the season.
_UPDATED = re.compile(r"table last updated\s+([A-Z][a-z]+)\s+(\d{1,2}),\s*(\d{4})", re.I)

#: `<dd><em>Please Note: Statistics include Early Vote through 11/5/2024.</em></dd>`
#: -- Alaska stating the data's own as-of date, which outranks the edit stamp.
_THROUGH = re.compile(r"statistics include[^.]*?through\s+(\d{1,2})/(\d{1,2})/(\d{4})", re.I)

HEADER = ("ballot type", "number issued", "number received")

#: Alaska's ballot types, mapped to a canonical method. This table is consulted
#: ONLY after `normalize.method()` returns None (it already knows "by mail" and
#: "early vote"), and anything in neither raises SchemaDrift -- a new ballot
#: type is a number we must not silently fold into a bucket. Every label here
#: appears verbatim in the 2022, 2024 and 2026 fixtures.
EXTRA_METHOD = {
    # An absentee ballot returned by fax or by the state's online-delivery
    # portal is still a ballot mailed out to the voter, not a vote cast in
    # person at a location.
    "by fax": METHOD_MAIL,
    "online delivery": METHOD_MAIL,
    "federal write-in (absentee)": METHOD_MAIL,
    # A "special needs" ballot is carried to a voter by a personal
    # representative and brought back -- absentee, not a polling place.
    "special needs": METHOD_MAIL,
    # Absentee-in-person: voted at an absentee voting location, in person.
    "in-person (absentee)": METHOD_INPERSON,
}

#: Types that are in Alaska's received total but are NOT a mail-versus-in-person
#: split. A questioned ballot is cast in person when eligibility is in doubt and
#: is set aside for review; counting it as `inperson` would overstate in-person
#: early voting by the size of the review pile (15,138 of 167,263 in 2024).
UNSPLIT_TYPES = {"questioned"}

#: How far either side of Election Day a general's statistics may be dated.
WINDOW_BEFORE = 120
WINDOW_AFTER = 30

_SUMMARY = re.compile(r"<summary[^>]*>(.*?)</summary>", re.S | re.I)
_TABLE = re.compile(r"<table[^>]*>.*?</table>", re.S | re.I)
_ROW = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
_CELL = re.compile(r"<t[hd][^>]*>(.*?)</t[hd]>", re.S | re.I)
_TAG = re.compile(r"<[^>]+>")
_COMMENT = re.compile(r"<!--.*?-->", re.S)


def _text(fragment: str) -> str:
    cleaned = _TAG.sub(" ", _COMMENT.sub(" ", fragment or ""))
    return " ".join(html.unescape(cleaned).replace("\xa0", " ").split())


def _count(raw: str) -> int:
    text = _text(raw).replace(",", "")
    if not re.fullmatch(r"\d+", text):
        raise SchemaDrift(f"AK: {raw!r} is not a ballot count")
    return int(text)


def _add(*values: int | None) -> int | None:
    known = [v for v in values if v is not None]
    return sum(known) if known else None


def section(page: str) -> tuple[int, int]:
    """(start, end) of the Election Statistics accordion, or NotYetPublished.

    The page is one accordion per topic, so the block simply is not there until
    the Division posts it -- which is the state of `?id=26genr` today.
    """
    for m in _SUMMARY.finditer(page or ""):
        if _text(m.group(1)).lower() != SECTION.lower():
            continue
        end = page.find("</details>", m.end())
        return m.end(), (end if end > 0 else len(page))
    raise NotYetPublished(
        "AK: the results page carries no Election Statistics block yet"
    )


def election_id(block: str) -> tuple[int, str]:
    """(two-digit year, election kind) from "24GENR Totals"."""
    m = _ELECTION_ID.search(block)
    if m is None:
        raise SchemaDrift(
            "AK: the Election Statistics block names no election "
            "(expected something like '26GENR Totals')"
        )
    return int(m.group(1)), m.group(2).upper()


def report_day(block: str, cycle: int) -> date:
    """The as-of date, preferring Alaska's own statement of it.

    "Statistics include Early Vote through 11/5/2024" is the Division saying
    what the numbers cover; "Table last updated January 29, 2025" is only when
    somebody edited the page. During the season the edit stamp IS the as-of, and
    after the count the two diverge -- which is why the explicit note wins.
    """
    m = _THROUGH.search(block)
    if m is not None:
        month, day, year = (int(g) for g in m.groups())
        try:
            return date(year, month, day)
        except ValueError as exc:
            raise SchemaDrift(f"AK: {m.group(0)!r} is not a date") from exc
    m = _UPDATED.search(block)
    if m is None:
        raise SchemaDrift(
            "AK: the Election Statistics block carries no as-of date "
            "(neither a 'Statistics include ... through' note nor a "
            "'Table last updated' stamp)"
        )
    try:
        return datetime.strptime(
            f"{m.group(1)} {m.group(2)} {m.group(3)}", "%B %d %Y"
        ).date()
    except ValueError as exc:
        raise SchemaDrift(f"AK: {m.group(0)!r} is not a date") from exc


def totals(block: str) -> dict[str, int]:
    """Alaska's own `Total Ballots Issued` / `Total Ballots Received`."""
    found = {m.group(1).lower(): _count(m.group(2)) for m in _TOTAL.finditer(block)}
    missing = [k for k in ("issued", "received") if k not in found]
    if missing:
        raise SchemaDrift(f"AK: the statistics block has no Total Ballots {missing}")
    return found


def ballot_types(block: str) -> list[tuple[str, int, int]]:
    """(label, issued, received) for every row of the ballot-type table."""
    m = _TABLE.search(block)
    if m is None:
        raise SchemaDrift("AK: the statistics block carries no ballot-type table")
    rows = [[_text(c) for c in _CELL.findall(r)] for r in _ROW.findall(m.group(0))]
    if not rows:
        raise SchemaDrift("AK: the ballot-type table has no rows")
    header = tuple(c.lower() for c in rows[0])
    if header != HEADER:
        raise SchemaDrift(
            f"AK: ballot-type table header is {list(header)}, expected {list(HEADER)}"
        )
    out: list[tuple[str, int, int]] = []
    for cells in rows[1:]:
        if len(cells) < 3 or not cells[0]:
            continue
        out.append((cells[0], _count(cells[1]), _count(cells[2])))
    if not out:
        raise SchemaDrift("AK: the ballot-type table has no ballot types in it")
    return out


def bucket(label: str) -> str | None:
    """A canonical method for one Alaska ballot type, or None for 'neither'.

    None means the type is real and counted but is not a mail-versus-in-person
    split (see UNSPLIT_TYPES). An UNRECOGNISED label raises instead -- per rule
    3, a type we cannot name must not be quietly added to a bucket.
    """
    key = " ".join(label.strip().lower().split())
    if key in UNSPLIT_TYPES:
        return None
    hit = _method(key) or EXTRA_METHOD.get(key)
    if hit is None:
        raise SchemaDrift(f"AK: unrecognised ballot type {label!r}")
    return hit


def parse(body: bytes, cycle: int, as_of: date) -> FetchResult:
    """Parse an Alaska results page into one statewide row."""
    if not body.lstrip()[:1] == b"<" or b"Election" not in body:
        raise SourceError("AK: the results page did not come back as a page")
    page = body.decode("utf-8", errors="replace")
    start, end = section(page)
    block = page[start:end]

    # Everything above the ballot-type table is a <dl> of prose, and the tags
    # sit INSIDE the values ("Total Ballots Issued: <em>178,716</em>"), so the
    # summary regexes read stripped text while `ballot_types` reads the markup.
    blurb = _text(block.split("<table", 1)[0])

    year, kind = election_id(blurb)
    if kind != "GENR":
        raise NotYetPublished(
            f"AK: the statistics block on this page is the {year:02d}{kind} "
            f"election, not the {cycle} general"
        )
    if year != int(cycle) % 100:
        raise NotYetPublished(
            f"AK: the statistics block is {year:02d}GENR, not {cycle % 100:02d}GENR"
        )

    day = report_day(blurb, cycle)
    voting = election_date(cycle)
    if not (voting - timedelta(days=WINDOW_BEFORE) <= day
            <= voting + timedelta(days=WINDOW_AFTER)):
        raise NotYetPublished(
            f"AK: the {cycle} general's statistics are dated {day.isoformat()}, "
            f"outside the window around {voting.isoformat()} -- Alaska has "
            f"posted a restatement, not a during-season snapshot"
        )
    if day > as_of:
        raise SourceError(
            f"AK: the statistics block is dated {day.isoformat()}, after the "
            f"run date {as_of.isoformat()}"
        )

    return _emit(block, blurb, cycle, day)


def _emit(block: str, blurb: str, cycle: int, day: date) -> FetchResult:
    stated = totals(blurb)
    rows = ballot_types(block)

    issued = sum(i for _l, i, _r in rows)
    received = sum(r for _l, _i, r in rows)
    if (issued, received) != (stated["issued"], stated["received"]):
        raise SchemaDrift(
            f"AK: the ballot types sum to {issued:,} issued / {received:,} "
            f"received but Alaska's own totals are {stated['issued']:,} / "
            f"{stated['received']:,}"
        )

    mail_issued = mail_received = inperson_received = None
    for label, sent, back in rows:
        where = bucket(label)
        if where == METHOD_MAIL:
            mail_issued = _add(mail_issued, sent)
            mail_received = _add(mail_received, back)
        elif where == METHOD_INPERSON:
            inperson_received = _add(inperson_received, back)

    return FetchResult(state_rows=[StateDay(
        cycle=cycle, state="AK", day=day,
        # Alaska's own received total, which INCLUDES questioned ballots and so
        # is deliberately larger than mail_returned + inperson.
        ballots_total=stated["received"],
        # A snapshot with no daily column; the day's new ballots are unreported.
        ballots_new=None,
        mail_requested=mail_issued,
        mail_returned=mail_received,
        inperson=inperson_received,
        # Alaska DOES register voters by party -- this report just does not
        # break it out. Blank, never 0. See THE BLANK RULE in schema.py.
        party_dem=None, party_rep=None, party_oth=None, party_npa=None,
    )])


class AKScraper(Adapter):
    """Tier 1 for Alaska: the Division's Election Statistics block (statewide)."""

    state = "AK"
    name = "ak-doe"
    tier = TIER_SCRAPER

    def _load(self, cycle: int, *, use_cache: bool) -> bytes:
        url = PAGE.format(cycle=int(cycle) % 100)
        try:
            return get(url, state="AK", filename=f"{cycle}genr_results.html",
                       use_cache=use_cache, min_bytes=4096)
        except Missing as exc:
            raise NotYetPublished(f"AK: {exc}") from exc

    def fetch(self, cycle: int, as_of: date) -> FetchResult:
        return parse(self._load(cycle, use_cache=False), cycle, as_of)

    def fetch_history(self, cycle: int) -> FetchResult:
        """A past cycle's certified statistics, dated by Alaska's own note.

        The Division keeps every election's page up, so `?id=24genr` still
        serves its Election Statistics block -- but the block's "Table last
        updated" stamp is the day somebody edited the page (2025-01-29 for the
        2024 general, 2024-09-13 for the 2022 one), not the day the numbers
        describe. Only when Alaska states the as-of itself, as 2024's "Statistics
        include Early Vote through 11/5/2024" does, is there an honest date to
        stamp.

        2022 is not published, and the window check in `parse` is what stops it:
        that block's numbers are the 11/30/2022 certified final, which would sit
        at days_to_election -22 against a 2024 row taken on Election Day. Note
        that the SAME guard runs on both paths -- `fetch` and `fetch_history`
        differ only in the as-of they hand `parse`, never in what `parse` will
        accept. The recoverable 2022 daily series is the archived PDF one, and
        the module docstring records exactly what it is and why it is not read
        yet.
        """
        if cycle >= date.today().year:
            raise NotYetPublished(f"AK: {cycle} is not an archived cycle")
        return parse(self._load(cycle, use_cache=True), cycle, election_date(cycle))
