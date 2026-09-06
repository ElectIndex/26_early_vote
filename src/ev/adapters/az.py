"""Arizona — the Secretary of State's "Sent/Accepted Early Ballots" table.

Arizona has no downloadable early-vote file. What the SoS publishes is a table
rendered into the cycle's election-information page,

    https://azsos.gov/elections/election-information/<cycle>-election-info

one row per county plus a `Total` row, with four columns:

    County | Last Updated | Sent | Accepted

`Sent` is early ballots put in voters' hands; `Accepted` is early ballots
returned and signature-verified. Arizona has only 15 counties, so this one small
table is the whole state.

Three things worth knowing before changing anything here:

* **Arizona does not split the Accepted figure.** Every returned early ballot is
  in it however it came back -- mailed, dropped at a vote center, or voted early
  in person at the recorder's office. `mail_returned` and `inperson` therefore
  stay blank: publishing `mail_returned = Accepted` would silently assert a
  mail/in-person split that Arizona has not reported. See THE BLANK RULE in
  schema.py.

* **This table carries no party breakdown**, even though Arizona registers by
  party (DEM / REP / LBR / PND "Party Not Designated"). The party-level early
  ballot numbers exist only in the 15 county recorders' own daily reports, which
  are published separately, in different formats, and -- like every azsos.gov and
  arizona.vote host -- from behind a Cloudflare bot challenge. There is no
  verified statewide-by-party URL to point at, so every `party_*` field here is
  None rather than a guess or a zero.

* **azsos.gov answers non-browser clients with a Cloudflare interstitial.** That
  is a 403 carrying a "Just a moment..." page, not a missing report, so it raises
  SourceError and lets the ladder fall through to the aggregator. Only a genuine
  404, or a real page with no Sent/Accepted table on it, is NotYetPublished --
  which is the normal state of the world until Arizona's early voting opens.

The table is found by its HEADER NAMES, never by position: the same page carries
a second, decoy header row ("County | Ballot by Precinct (PDF)") and the card the
table lives in is titled "Primary Election - ..." or "General Election - ..."
depending on the season.
"""

from __future__ import annotations

import html
import logging
import re
from datetime import date, datetime

from ..calendar import days_to_election
from ..schema import TIER_SCRAPER, CountyDay, StateDay
from . import _fips
from ._net import Missing, get
from .base import Adapter, FetchResult, NotYetPublished, SchemaDrift, SourceError

log = logging.getLogger(__name__)

#: VERIFIED: the `<cycle>-election-info` slug exists for both 2024 and 2026.
URL = "https://azsos.gov/elections/election-information/{cycle}-election-info"

COUNTY_COLUMN = "county"
UPDATED_COLUMN = "last updated"
SENT_COLUMN = "sent"
ACCEPTED_COLUMN = "accepted"
REQUIRED = (COUNTY_COLUMN, UPDATED_COLUMN, SENT_COLUMN, ACCEPTED_COLUMN)

TOTAL_LABELS = {"total", "totals", "statewide", "state total"}

#: Arizona reuses one page for a cycle's primary and its general, and after the
#: primary it leaves the primary's table sitting there -- the only capture of the
#: 2024 page we have is from 2024-11-11 and still shows July's primary numbers.
#: A table whose newest row predates the general's ballot mailing therefore is
#: not the general's table, whatever the card above it is titled. UOCAVA ballots
#: go out 45 days ahead and A.R.S. 16-542 opens early voting at 27, so anything
#: older than this is last election's.
EARLY_WINDOW_DAYS = 45

#: "Jul 29, 2024 13:41" -- the only timestamp format this table has ever used.
_UPDATED = re.compile(r"([A-Z][a-z]{2})\s+(\d{1,2}),\s*(\d{4})(?:\s+(\d{1,2}):(\d{2}))?")

_TAG = re.compile(r"<[^>]+>")

#: Cloudflare's managed-challenge interstitial. Distinctive enough to tell apart
#: from a real page, and never present on one.
_CHALLENGE = ("just a moment", "cdn-cgi/challenge-platform", "cf_chl_opt")


def _text(fragment: str) -> str:
    return " ".join(html.unescape(_TAG.sub(" ", fragment)).split())


def looks_like_challenge(body: bytes) -> bool:
    """True if these bytes are a bot-check interstitial rather than the page."""
    head = body[:4096].decode("utf-8", errors="replace").lower()
    return any(marker in head for marker in _CHALLENGE)


def _rows(table: str) -> list[list[str]]:
    """Every row's cells, in order. Arizona renders the Total row's label and its
    counts as <th>, so both cell tags have to be read or the state total vanishes."""
    out = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", table, re.S):
        cells = [_text(c) for _, c in re.findall(r"<(td|th)[^>]*>(.*?)</\1>", tr, re.S)]
        if cells:
            out.append(cells)
    return out


def _int(raw: str) -> int | None:
    """A count, or None for a cell Arizona left as a placeholder ("---")."""
    text = raw.replace(",", "").strip()
    if not text or set(text) <= {"-", "–", "—"}:
        return None
    if not text.isdigit():
        raise SchemaDrift(f"AZ: non-numeric count {raw!r}")
    return int(text)


def _updated(raw: str) -> date | None:
    m = _UPDATED.search(raw or "")
    if not m:
        return None
    month, day, year = m.group(1), int(m.group(2)), int(m.group(3))
    try:
        return datetime.strptime(f"{month} {day} {year}", "%b %d %Y").date()
    except ValueError as exc:
        raise SchemaDrift(f"AZ: {raw!r} is not a Last Updated timestamp") from exc


def _ballot_table(markup: str) -> tuple[dict[str, int], list[list[str]]] | None:
    """(column index, data rows) for the Sent/Accepted table, or None if absent."""
    for table in re.findall(r"<table[^>]*>(.*?)</table>", markup, re.S):
        rows = _rows(table)
        for i, row in enumerate(rows):
            labels = [cell.lower() for cell in row]
            if all(name in labels for name in REQUIRED):
                return {name: labels.index(name) for name in REQUIRED}, rows[i + 1:]
    return None


def parse(markup: str, cycle: int) -> FetchResult:
    """Parse one election-information page into canonical rows."""
    found = _ballot_table(markup)
    if found is None:
        # Arizona renders this table only once early voting is underway. Before
        # then the page is simply a page -- not an error, and not a zero.
        raise NotYetPublished(
            f"AZ: {cycle} election-information page carries no Sent/Accepted table yet"
        )
    index, rows = found

    def cell(row: list[str], column: str) -> str:
        i = index[column]
        return row[i] if i < len(row) else ""

    # (is_statewide, fips, canonical name, sent, accepted, last updated)
    parsed: list[tuple[bool, str, str, int | None, int | None, date | None]] = []
    unknown: list[str] = []

    for row in rows:
        if len(row) < len(REQUIRED):
            # The page also carries a two-column "County | Ballot by Precinct"
            # header for a different table. Not our row.
            continue
        name = cell(row, COUNTY_COLUMN)
        if not name or name.lower() == COUNTY_COLUMN:
            continue

        sent = _int(cell(row, SENT_COLUMN))
        accepted = _int(cell(row, ACCEPTED_COLUMN))
        day = _updated(cell(row, UPDATED_COLUMN))

        if name.lower() in TOTAL_LABELS:
            parsed.append((True, "", name, sent, accepted, day))
            continue

        hit = _fips.lookup("AZ", name)
        if hit is None:
            unknown.append(name)
            continue
        fips, canonical = hit
        parsed.append((False, fips, canonical, sent, accepted, day))

    if unknown:
        # Arizona has exactly 15 counties and they do not change; a name we cannot
        # place is one we would silently drop off the map.
        raise SchemaDrift(f"AZ: unrecognised county names {sorted(set(unknown))[:5]}")
    if not any(not statewide for statewide, *_ in parsed):
        raise NotYetPublished(f"AZ: Sent/Accepted table for {cycle} has no county rows yet")

    days = [day for *_, day in parsed if day is not None]
    if not days:
        raise SchemaDrift("AZ: Sent/Accepted table carries no Last Updated timestamp")
    # Counties update at different times, so the report is only as current as its
    # newest row -- and a page left over from a past cycle must never be published
    # under this one.
    published = max(days)
    if published.year != int(cycle):
        raise SchemaDrift(
            f"AZ: table was last updated {published.isoformat()}, not in the {cycle} cycle"
        )

    result = FetchResult()
    for statewide, fips, name, sent, accepted, day in parsed:
        if statewide:
            result.state_rows.append(StateDay(
                cycle=cycle, state="AZ", day=day or published,
                ballots_total=accepted,
                mail_requested=sent,
                # Arizona publishes no mail/in-person split of Accepted, and no
                # party registration split of anything here. Never 0.
                mail_returned=None, inperson=None,
                party_dem=None, party_rep=None, party_oth=None, party_npa=None,
            ))
        else:
            result.county_rows.append(CountyDay(
                cycle=cycle, state="AZ", county_fips=fips, day=day or published,
                county_name=name,
                ballots_total=accepted,
                mail_returned=None, inperson=None,
                party_dem=None, party_rep=None, party_oth=None, party_npa=None,
            ))
    return result


class AZScraper(Adapter):
    """Tier 1 for Arizona: the SoS Sent/Accepted Early Ballots table."""

    state = "AZ"
    name = "az-sos"
    tier = TIER_SCRAPER

    def _load(self, cycle: int, *, use_cache: bool) -> str:
        url = URL.format(cycle=cycle)
        try:
            body = get(url, state="AZ", filename=f"{cycle}-election-info.html",
                       use_cache=use_cache, min_bytes=2048)
        except Missing as exc:
            raise NotYetPublished(f"AZ: {url} does not exist yet") from exc
        if looks_like_challenge(body):
            # Reachability, not absence: the report may well be sitting behind
            # this. Fall through to the next tier rather than stopping the ladder.
            raise SourceError(f"AZ: {url} answered with a Cloudflare bot challenge")
        return body.decode("utf-8", errors="replace")

    def fetch(self, cycle: int, as_of: date) -> FetchResult:
        result = parse(self._load(cycle, use_cache=False), cycle)
        published = result.state_rows[0].day if result.state_rows else result.county_rows[0].day
        if published > as_of:
            raise NotYetPublished(
                f"AZ: table is dated {published.isoformat()}, after {as_of.isoformat()}"
            )
        if days_to_election(cycle, published) > EARLY_WINDOW_DAYS:
            # Last election's table, left on the page. See EARLY_WINDOW_DAYS.
            raise NotYetPublished(
                f"AZ: page is still showing the {published.isoformat()} table, from "
                f"before the {cycle} general's early-vote window opened"
            )
        return result

    def fetch_history(self, cycle: int) -> FetchResult:
        """A past cycle's page -- one final snapshot, not a daily series.

        Arizona rewrites this table in place rather than keeping a dated file per
        day, so what survives for 2022/2024 is the last state it was left in.
        That is still the cycle's final early-ballot total, which is what the
        comparison lines anchor on.
        """
        if cycle >= date.today().year:
            raise NotYetPublished(f"AZ: {cycle} is not an archived cycle")
        return parse(self._load(cycle, use_cache=True), cycle)
