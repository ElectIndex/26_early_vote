"""Nevada — the Secretary of State's daily turnout reporting.

Nevada publishes NOTHING machine-readable for early vote. Every turnout report on
nvsos.gov is a PDF printed out of Excel — "Acrobat PDFMaker 24 for Excel" sits in
the producer field of every one of them — and there is no CSV, XLSX or JSON of
any of them anywhere on the site. The one comma-delimited early-vote feed in the
state is Clark County's own voter-level daily file, and it covers Clark alone,
about seven Nevada voters in ten and none of the other sixteen localities, so it
is not a substitute for the statewide report. This adapter therefore parses a
PDF, and does it the way ia.py does — text layer only, anchored on arithmetic.

The daily suite lives on one page per cycle:

    https://www.nvsos.gov/sos/elections/election-information/
        <cycle>-election-information/<cycle>-turnout-reporting

and each report on it is an OPAQUE document id — `/sos/home/showpublisheddocument/
15581/638672847178800000` — that changes every time the file is replaced. Nothing
about tomorrow's URL is derivable from today's, so the index page is scraped for
the link rather than a filename being composed. The page keeps one row per report
and repoints it at the newest document id, which is why `fetch_history` is not
implemented: Nevada overwrites the cumulative report in place and the superseded
document ids are simply dropped from the page.

Of the six reports posted daily, one carries everything this project publishes:

    "Mail Ballot, EASE & Early Voting Cumulative Turnout Report"
        -> "Mail, EASE and Early Voting Turnout - Cumulative"

a county x party matrix repeated three times over — mail-and-EASE ballots
returned and accepted, early-voting ballots cast, and the two combined — plus
Nevada's own Statewide row. Because the mail and in-person cuts are each
separately published, `mail_returned` and `inperson` are real reported numbers
and neither is derived by subtracting the other. (The daily "Early Voting Turnout
Report N" files are deliberately NOT used: they are per-WEEK sheets, not
cumulative, so reading one would understate the state from week 2 onward, and
their closed-polling-day cells collapse under text extraction.)

Four things drive the parser's shape:

* **Party columns are read from the header, never assumed.** Nevada's 2022
  reports broke registration into Dem / Rep / Other / **NPP**; the 2024 reports
  collapsed that into Dem / Rep / **OTHER**. NPP is Nevada's non-partisan
  registration and belongs in `party_npa` — a Nevada nonpartisan has declined a
  party and a Libertarian has chosen one, and the unaffiliated share is the
  single most-watched number in early-vote coverage. The column set is therefore
  taken from the header cells' x-positions and each label is routed through
  `normalize.party()`, so if Nevada restores the NPP column it lands in `npa`
  automatically. When Nevada does not break nonpartisans out — the 2024 shape —
  `party_npa` is left BLANK rather than being invented out of `OTHER`. See THE
  BLANK RULE in schema.py.

* **Arithmetic is the anchor, because the text layer is not a grid.** Every row is
  checked against seven identities Nevada's own spreadsheet enforces: the party
  columns sum to each block's total, and the mail and early-voting blocks sum to
  the combined block column by column. Every county must appear, and the county
  rows must sum to Nevada's own Statewide row (which is an Excel SUM of them).
  A mis-read column cannot survive that, and anything that fails raises
  SchemaDrift rather than publishing.

* **The report dates itself.** Each PDF carries "<cycle> General Election Turnout"
  and "Updated M/D/YYYY at H:MM a.m." in its page header. Rows are dated by that
  line, not by the day the job ran, and a file for the wrong year or for a
  primary is rejected instead of parsed — the index page carries the same row for
  the presidential-preference primary and the June primary as it does for the
  November general.

* **nvsos.gov sits behind an Imperva/Incapsula bot wall.** From automation it
  answers every URL — index pages and document ids alike — with a ~1 KB
  Incapsula interstitial carrying HTTP 200, and it did so to the Internet
  Archive's crawler on every snapshot that crawler took during the 2024
  early-vote window. A block is NOT absence: it raises SourceError so the ladder
  falls through to the aggregator, because falling silent would leave Nevada
  blank for six weeks with nothing in the log to say why.
"""

from __future__ import annotations

import html as _html
import io
import logging
import re
from dataclasses import dataclass
from datetime import date
from urllib.parse import urljoin

import pypdf

from ..calendar import election_date
from ..normalize import PARTY_DEM, PARTY_NPA, PARTY_OTH, PARTY_REP, party as _party
from ..schema import TIER_SCRAPER, CountyDay, StateDay
from . import _fips
from ._net import Missing, get, looks_like_html
from .base import Adapter, FetchResult, NotYetPublished, SchemaDrift, SourceError

log = logging.getLogger(__name__)

BASE = "https://www.nvsos.gov"

#: Index pages carrying the cycle's daily report links, tried in order.
#:
#: The first is VERIFIED for 2024: it is the page the SoS linked as "Turnout
#: Reports are posted HERE" and it carries every report row this adapter looks
#: for. Its 2026 twin is UNVERIFIED — the {cycle}-election-information section
#: exists but the SoS does not create the turnout child page until the cycle's
#: reporting starts, so it 404s today, which is exactly the NotYetPublished path.
#: The second is the SoS's evergreen turnout page; it is VERIFIED to exist (it is
#: in the Elections > Voters nav) but its CONTENTS are UNVERIFIED as a source of
#: current-cycle report links, so it is only a fallback for the cycle page being
#: renamed, and anything it yields still has to survive the PDF's own
#: year-and-kind check below before a number comes out of it.
INDEX_URLS = (
    "https://www.nvsos.gov/sos/elections/election-information/"
    "{cycle}-election-information/{cycle}-turnout-reporting",
    "https://www.nvsos.gov/sos/elections/voters/election-turnout-statistics",
)

#: The row on the index page whose link this adapter follows, squashed. Nevada
#: writes it "Mail Ballot, EASE & Early Voting Cumulative Turnout Report".
REPORT_SIGNATURE = "mail ballot ease early voting cumulative turnout report"

#: The report's own title, and the three column blocks it repeats the county x
#: party matrix across, in left-to-right order. The order is READ from the page
#: rather than assumed: it is what says which block is mail and which is
#: in-person, and getting it backwards would swap the two headline numbers.
REPORT_TITLE = "mail, ease and early voting turnout"
BLOCK_MAIL = "mail and ease ballots returned and accepted"
BLOCK_INPERSON = "early voting ballots cast"
BLOCK_TOTAL = "total mail, ease and early voting turnout"
BLOCK_TITLES = (BLOCK_MAIL, BLOCK_INPERSON, BLOCK_TOTAL)

#: Nevada's own statewide row. Used as published, never re-summed from counties,
#: so our headline can never disagree with the Secretary of State's.
STATEWIDE_LABEL = "statewide"

ELECTION_KIND = "General"

#: Header cells sit on one text baseline but come out of the text layer with
#: sub-point differences in y, so the party header band is matched with slack.
HEADER_BAND = 1.5

#: Accounting-formatted zero. Nevada prints "-" for a bucket with no ballots in
#: it; that is a real zero in a bucket Nevada reports, not an unreported field.
DASHES = frozenset({"-", "–", "—"})

_UPDATED = re.compile(r"updated\s*:?\s*(\d{1,2})/(\d{1,2})/(\d{4})", re.I)
_ELECTION = re.compile(r"(\d{4})\s+(General|Primary|Presidential Preference Primary)"
                       r"\s+Election", re.I)
#: A data row: a locality name, then nothing but numbers, dashes and percents.
_ROW = re.compile(r"^(?P<label>[A-Za-z][A-Za-z .'\-]*?)\s+"
                  r"(?P<values>[-–—\d][-–—\d,.%\s]*)$")
_ANCHOR = re.compile(r"<a\b[^>]*?href\s*=\s*[\"']([^\"']+)[\"'][^>]*>(.*?)</a>",
                     re.I | re.S)

#: Imperva/Incapsula's interstitial. It arrives with HTTP 200 and a real body, so
#: status alone cannot tell it from a page; these markers can.
BLOCK_MARKERS = (b"_Incapsula_Resource", b"Incapsula incident ID",
                 b"Request unsuccessful")

_PARTY_FIELD = {
    PARTY_DEM: "party_dem", PARTY_REP: "party_rep",
    PARTY_NPA: "party_npa", PARTY_OTH: "party_oth",
}

#: How far either side of Election Day a report may be dated and still belong to
#: this cycle. Nevada's mail ballots go out ~20 days ahead and the reports keep
#: updating through the county canvass, which runs to ten days after.
WINDOW_BEFORE = 60
WINDOW_AFTER = 45


def _squash(raw: str) -> str:
    return " ".join(str(raw or "").split()).lower()


def bucket(label: str) -> str:
    """One of Nevada's party column labels -> one of normalize's four buckets.

    Public because it is the whole party contract in one function: "NPP",
    Nevada's non-partisan registration, must land in `npa` and not in `oth`.
    """
    mapped = _party(label)
    if mapped is None:
        raise SchemaDrift(f"NV: unrecognised party column {label!r}")
    return mapped


def _count(token: str) -> int:
    """A cell holding a count. Nevada's accounting zero "-" is a real zero."""
    if token in DASHES:
        return 0
    text = token.replace(",", "")
    if not text.isdigit():
        raise SchemaDrift(f"NV: {token!r} is not a count")
    return int(text)


def _is_percent(token: str) -> bool:
    return token.endswith("%") or token in DASHES


def _is_document(href: str) -> bool:
    """True for a link to a published file rather than to another page.

    Nevada's report rows carry a link on the word "EASE" pointing at an explainer
    PAGE as well as the "HERE" link pointing at the report, so the report link is
    picked by shape and not simply by being last.
    """
    lowered = href.lower()
    return "/showpublisheddocument" in lowered or "/showdocument" in lowered \
        or lowered.split("?")[0].endswith((".pdf", ".xlsx", ".xls", ".csv"))


def _blocked(body: bytes) -> bool:
    """True if these bytes are the bot wall rather than the page we asked for."""
    head = body[:4096]
    return any(marker in head for marker in BLOCK_MARKERS)


@dataclass(frozen=True)
class ReportLink:
    """One "<report name> ( Updated <date> ) - HERE" row on the index page."""

    label: str
    url: str
    updated: date | None


def report_links(body: bytes, *, base: str = BASE) -> list[ReportLink]:
    """Every index-page row pointing at the cumulative mail+EV turnout report.

    The anchor text on these rows is the word "HERE" -- sometimes split across two
    anchors -- so rows are identified by the SENTENCE they sit in, and the last
    DOCUMENT link on that line is the one taken. Anchor text is kept, not thrown
    away with the tag: Nevada links the word "EASE" in the middle of the report's
    own name out to its EASE explainer, so dropping it would take the report's
    name apart.
    """
    text = body.decode("utf-8", "replace")
    # Anchors become "<sentinel>href<sentinel>text" so the surrounding sentence
    # survives tag stripping while still carrying its href.
    sentinel = "\x00"
    text = _ANCHOR.sub(lambda m: f"{sentinel}{m.group(1)}{sentinel}{m.group(2)}", text)
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", text)
    text = re.sub(r"(?is)<br\s*/?>|</(p|div|li|tr|h[1-6])>", "\n", text)
    text = _html.unescape(re.sub(r"<[^>]+>", " ", text))

    out: list[ReportLink] = []
    for line in text.split("\n"):
        if sentinel not in line:
            continue
        parts = line.split(sentinel)
        # Odd indices are hrefs, even indices are the sentence around them.
        label = " ".join(parts[0::2])
        key = re.sub(r"[^a-z0-9]+", " ", label.lower()).strip()
        key = " ".join(key.split())
        if REPORT_SIGNATURE not in key:
            continue
        hrefs = [h.strip() for h in parts[1::2] if _is_document(h.strip())]
        if not hrefs:
            continue
        found = _UPDATED.search(label)
        updated = None
        if found:
            month, day, year = (int(g) for g in found.groups())
            try:
                updated = date(year, month, day)
            except ValueError:
                updated = None
        out.append(ReportLink(_squash(label), urljoin(base, hrefs[-1]), updated))
    return out


def pick_link(links: list[ReportLink], cycle: int) -> ReportLink | None:
    """The row belonging to `cycle`'s general election, or None.

    Nevada keeps every election of the cycle on one page, so the presidential
    preference primary and the June primary carry this same row. The parenthesised
    "Updated" date each row prints is what separates them; a row with no date at
    all is only taken when it is the only candidate, and the PDF still has to say
    which election it is before it is parsed.
    """
    election = election_date(cycle)
    dated = [
        link for link in links
        if link.updated is not None
        and -WINDOW_AFTER <= (election - link.updated).days <= WINDOW_BEFORE
    ]
    if dated:
        return max(dated, key=lambda link: link.updated)
    undated = [link for link in links if link.updated is None]
    return undated[0] if len(undated) == 1 else None


def _page(body: bytes):
    """(text lines, positioned header cells) for the report's single page."""
    if not body.startswith(b"%PDF-"):
        raise SourceError("NV: turnout report was not a PDF")
    try:
        reader = pypdf.PdfReader(io.BytesIO(body))
        page = reader.pages[0]
    except Exception as exc:  # noqa: BLE001 -- pypdf raises a zoo of types
        raise SourceError(f"NV: could not open the turnout report: {exc}") from exc

    cells: list[tuple[float, float, str]] = []

    def visit(text, cm, tm, font, size):  # noqa: ANN001 - pypdf's callback shape
        stripped = str(text).strip()
        if stripped:
            cells.append((round(float(tm[5]), 1), round(float(tm[4]), 1), stripped))

    try:
        flat = page.extract_text(visitor_text=visit)
    except Exception as exc:  # noqa: BLE001
        raise SourceError(f"NV: could not read the turnout report's text: {exc}") from exc
    return flat.splitlines(), cells


def report_header(lines: list[str]) -> tuple[date, int, str]:
    """(as-of date, election year, election kind) from the report's page header."""
    updated: date | None = None
    election: tuple[int, str] | None = None
    for line in lines:
        if updated is None:
            found = _UPDATED.search(line)
            if found:
                month, day, year = (int(g) for g in found.groups())
                try:
                    updated = date(year, month, day)
                except ValueError as exc:
                    raise SchemaDrift(f"NV: unreadable Updated date in {line!r}") from exc
        if election is None:
            found = _ELECTION.search(line)
            if found:
                election = (int(found.group(1)), found.group(2).title())
    if updated is None:
        raise SchemaDrift("NV: turnout report carries no 'Updated <date>' line")
    if election is None:
        raise SchemaDrift("NV: turnout report carries no '<year> <kind> Election' line")
    return updated, election[0], election[1]


def _block_order(lines: list[str]) -> None:
    """Assert the three column blocks run mail, then in-person, then combined.

    Nevada prints the block titles as one run of text across the top of the
    matrix, so their left-to-right order is exactly their order in that line.
    """
    for line in lines:
        flat = _squash(line)
        positions = [flat.find(title) for title in BLOCK_TITLES]
        if all(p >= 0 for p in positions) and positions == sorted(positions):
            return
    raise SchemaDrift(
        f"NV: report header does not carry {list(BLOCK_TITLES)} in that order"
    )


def party_columns(cells: list[tuple[float, float, str]]) -> list[str]:
    """The party buckets of one block, left to right, read off the header row.

    The header repeats the same party set once per block, so the cells that
    `normalize.party()` recognises are collected from the one baseline they share,
    sorted by x and split into three equal runs. The runs must agree -- if they do
    not, the columns are not what we think they are.
    """
    hits = [(y, x, _party(text)) for y, x, text in cells]
    hits = [(y, x, b) for y, x, b in hits if b is not None]
    if not hits:
        raise SchemaDrift("NV: report has no party column headers")

    baseline = max(y for y, _, _ in hits)
    band = sorted(((x, b) for y, x, b in hits if abs(y - baseline) <= HEADER_BAND))
    if len(band) != len(hits):
        raise SchemaDrift(
            f"NV: party column headers are not on one row (found {len(hits)}, "
            f"{len(band)} on the top one)"
        )
    if len(band) % len(BLOCK_TITLES):
        raise SchemaDrift(
            f"NV: {len(band)} party columns do not divide into "
            f"{len(BLOCK_TITLES)} blocks"
        )

    width = len(band) // len(BLOCK_TITLES)
    blocks = [[b for _, b in band[i * width:(i + 1) * width]]
              for i in range(len(BLOCK_TITLES))]
    if any(block != blocks[0] for block in blocks[1:]):
        raise SchemaDrift(f"NV: party columns differ between blocks: {blocks}")
    if len(set(blocks[0])) != width:
        raise SchemaDrift(f"NV: party columns repeat a bucket: {blocks[0]}")
    return blocks[0]


def _row(values: str, parties: list[str]) -> list[dict[str, int]]:
    """One locality's three blocks, each as {bucket: count} plus a "total".

    Every identity Nevada's own spreadsheet enforces is checked here: the party
    columns sum to their block total, and the mail and early-voting blocks sum to
    the combined block column by column. That is what makes it safe to read a
    grid off a text layer.
    """
    tokens = values.split()
    width = len(parties) + 2  # the party columns, the block total, its percent
    expected = 1 + len(BLOCK_TITLES) * width  # registered voters, then the blocks
    if len(tokens) != expected:
        raise SchemaDrift(
            f"NV: expected {expected} cells on a county row, got {len(tokens)}: "
            f"{tokens!r}"
        )

    blocks: list[dict[str, int]] = []
    at = 1  # tokens[0] is the county's active-registered-voter count
    for index in range(len(BLOCK_TITLES)):
        counts = {p: _count(tokens[at + i]) for i, p in enumerate(parties)}
        total = _count(tokens[at + len(parties)])
        percent = tokens[at + len(parties) + 1]
        if not _is_percent(percent):
            raise SchemaDrift(f"NV: {percent!r} is not a percent cell")
        if sum(counts.values()) != total:
            raise SchemaDrift(
                f"NV: block {index} parties {counts} do not sum to its total {total}"
            )
        counts["total"] = total
        blocks.append(counts)
        at += width

    mail, inperson, combined = blocks
    for key in combined:
        if mail[key] + inperson[key] != combined[key]:
            raise SchemaDrift(
                f"NV: {key} mail {mail[key]} + early voting {inperson[key]} "
                f"!= combined {combined[key]}"
            )
    return blocks


def parse(body: bytes, cycle: int) -> FetchResult:
    """Parse one cumulative mail+EASE+early-voting report into canonical rows."""
    lines, cells = _page(body)
    day, year, kind = report_header(lines)
    if year != int(cycle) or kind != ELECTION_KIND:
        raise SchemaDrift(f"NV: report is the {year} {kind} election, not {cycle}")
    if not any(REPORT_TITLE in _squash(line) for line in lines):
        raise SchemaDrift(f"NV: report is not titled {REPORT_TITLE!r}")
    _block_order(lines)
    parties = party_columns(cells)

    statewide: list[dict[str, int]] | None = None
    counties: dict[str, tuple[str, list[dict[str, int]]]] = {}
    for line in lines:
        match = _ROW.match(line.strip())
        if match is None:
            continue
        label = match.group("label").strip()
        if _squash(label) == STATEWIDE_LABEL:
            if statewide is not None:
                raise SchemaDrift("NV: report carries two Statewide rows")
            statewide = _row(match.group("values"), parties)
            continue
        hit = _fips.lookup("NV", label)
        if hit is None:
            continue  # page furniture: notes, the registration-as-of line, percents
        fips, canonical = hit
        if fips in counties:
            raise SchemaDrift(f"NV: report carries two rows for {canonical}")
        counties[fips] = (canonical, _row(match.group("values"), parties))

    if statewide is None:
        raise SchemaDrift("NV: report has no Statewide row")
    missing = sorted(set(_fips.names("NV")) - {name for name, _ in counties.values()})
    if missing:
        # A county we failed to read is a county that would silently vanish off
        # the map while the statewide headline stayed right.
        raise SchemaDrift(f"NV: report is missing counties {missing}")
    for index, block in enumerate(statewide):
        for key, value in block.items():
            summed = sum(blocks[index][key] for _, blocks in counties.values())
            if summed != value:
                raise SchemaDrift(
                    f"NV: block {index} {key} counties sum to {summed}, "
                    f"Nevada's Statewide row says {value}"
                )

    def fields(blocks: list[dict[str, int]]) -> dict[str, int | None]:
        mail, inperson, combined = blocks
        row: dict[str, int | None] = {
            "ballots_total": combined["total"],
            "mail_returned": mail["total"],
            "inperson": inperson["total"],
        }
        # Nevada reports every bucket it breaks out, so 0 in one of them is a
        # genuine zero; a bucket Nevada does not break out at all stays blank.
        for party_bucket, field in _PARTY_FIELD.items():
            row[field] = combined.get(party_bucket)
        return row

    state_row = StateDay(
        cycle=cycle, state="NV", day=day,
        # Nevada mails a ballot to every active registered voter and publishes the
        # count in a DIFFERENT report, so it is unreported here. Blank, not zero.
        mail_requested=None,
        **fields(statewide),
    )
    county_rows = [
        CountyDay(cycle=cycle, state="NV", county_fips=fips, day=day,
                  county_name=name, **fields(blocks))
        for fips, (name, blocks) in sorted(counties.items())
    ]
    return FetchResult(state_rows=[state_row], county_rows=county_rows)


class NVScraper(Adapter):
    """Tier 1 for Nevada: the SoS daily cumulative mail + early-voting report."""

    state = "NV"
    name = "nv-sos"
    tier = TIER_SCRAPER

    def _indexes(self, cycle: int):
        """Each reachable index page as (bytes, url), best candidate first."""
        for template in INDEX_URLS:
            url = template.format(cycle=cycle)
            try:
                body = get(url, state="NV",
                           filename=f"{cycle}_{url.rstrip('/').rsplit('/', 1)[-1]}.html")
            except Missing:
                continue
            if _blocked(body):
                # A wall is not an absence. Fall through to the aggregator loudly
                # rather than reporting Nevada as having published nothing.
                raise SourceError(
                    f"NV: {url} answered with the Incapsula bot wall, not the page"
                )
            yield body, url

    def _report(self, cycle: int) -> ReportLink:
        tried: list[str] = []
        for body, url in self._indexes(cycle):
            tried.append(url)
            link = pick_link(report_links(body, base=url), cycle)
            if link is not None:
                return link
        if not tried:
            raise NotYetPublished(
                f"NV: no {cycle} turnout-reporting page yet "
                f"({', '.join(u.format(cycle=cycle) for u in INDEX_URLS)})"
            )
        raise NotYetPublished(
            f"NV: {', '.join(tried)} does not link a {cycle} cumulative mail and "
            f"early-voting turnout report yet"
        )

    def _load(self, link: ReportLink, cycle: int) -> bytes:
        try:
            body = get(link.url, state="NV",
                       filename=f"{cycle}_mail_ease_ev_cumulative.pdf",
                       min_bytes=4096)
        except Missing as exc:
            # The page links it, so a 404 is the CMS misbehaving rather than
            # Nevada not having published: fall through, do not stop the ladder.
            raise SourceError(f"NV: linked report is not downloadable: {exc}") from exc
        if _blocked(body):
            raise SourceError(
                f"NV: {link.url} answered with the Incapsula bot wall, not the report"
            )
        if looks_like_html(body):
            raise SourceError(f"NV: {link.url} served HTML, not the turnout report")
        return body

    def fetch(self, cycle: int, as_of: date) -> FetchResult:
        link = self._report(cycle)
        result = parse(self._load(link, cycle), cycle)
        published = result.state_rows[0].day
        if published > as_of:
            raise NotYetPublished(
                f"NV: report is dated {published.isoformat()}, after {as_of.isoformat()}"
            )
        return result

    # fetch_history is deliberately the base default. Nevada replaces the
    # cumulative report in place -- the index page keeps ONE row for it and
    # repoints that row at the newest document id -- so a past cycle's page
    # yields exactly one day, its last, and there is no archived daily series to
    # walk. (The per-day "Early Voting Turnout Report N" files that page does keep
    # are per-WEEK sheets of in-person votes only, not a cumulative curve.)
