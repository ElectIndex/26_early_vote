"""Florida — the Division of Elections' public VBM/early-voting stats page.

`countyfilesvbm-ev.floridados.gov/VoteByMailEarlyVotingReports/PublicStats`
renders the same four tables server-side that it has rendered since 2016: a
statewide summary and three per-county tables, in a fixed order that mirrors the
summary's three rows —

    summary       Vote-by-Mail Provided (Not Yet Returned) / Voted VBM / Voted Early
    per county    Vote-by-Mail Provided (Not Yet Returned)
    per county    Voted Vote-by-Mail
    per county    Voted Early

each split Republican / Democrat / Other / No Party Affiliation / Total, and each
county row carrying its own `Election` label and `Compiled` timestamp.

Unlike North Carolina, this page is a SNAPSHOT — it carries today's cumulative
numbers and no history at all. Florida therefore genuinely depends on the cron
for the CURRENT cycle: a day we fail to run is a day of Florida's curve that
cannot be recovered later. That asymmetry is why the job runs every two hours
rather than daily — Florida is the state that pays for a missed slot, and
GitHub's scheduler misses them. A PAST cycle's curve is a different problem and it is solved
in `fetch_history` below, out of the Internet Archive.

Four things this parser refuses to do quietly:

* **`ballots_total` counts ballots CAST** — voted-by-mail plus voted-early. The
  "Provided (Not Yet Returned)" figure is outstanding ballots, not votes, and
  folding it in would overstate Florida by the size of its mail backlog. It is
  reported separately as `mail_requested`.
* **The party columns must sum to the row's own Total.** Florida renders some
  numbers with a leading zero ("01" for 1), so a naive parse can silently shift a
  digit. The state publishes its own total on every row, so we check against it
  and raise SchemaDrift on a mismatch rather than publishing a number that does
  not add up.
* **The page names the election it is reporting, and we demand the general.**
  Every county row carries an `Election` cell — "43888 - General" in 2024,
  "26906 - General" in 2022, "49894 - General" today — and the page shows
  whatever election is live, which for most of an even year is a PRIMARY. On
  2024-08-19 this page read "43887 - Primary", 1,240,384 mail ballots and 600,463
  early votes; publishing those as general-election early voting would be a
  confident, plausible, completely wrong headline. So the election label is
  matched, and its date is checked against `calendar.election_date`.
* **Tables are found by their header row, never by position.** The page renders
  one summary table PER LIVE ELECTION: during the 2024 primary-to-general
  crossover it carried two summaries and 134 county rows, and in March 2024 it
  carried four summaries and a county table keyed to the Presidential Preference
  Primary. Indexing `tables[0..3]` reads the wrong election on exactly the days
  when two elections overlap.

**The two voted tables are a PARTY-BY-METHOD CROSSTAB, and it is published as
one.** "Voted Vote-by-Mail" and "Voted Early" are separate per-county tables,
each split Republican / Democrat / Other / NPA, so Florida states the cells and
not merely the two margins. `CountyDay` has nowhere to put that -- its
`party_*` fields are the row totals -- so this adapter used to add the two
tables together and drop the split at the last step. It now also emits
`schema.MethodDay` rows, one per county per channel, and the county row is
unchanged: it is still the sum, which is what the site reads.

**The as-of date is the page's own `Compiled` stamp, never the run date.** Rows
compile at different times — on 2024-11-04 counties carried both 8:12AM and
11:04AM — and the freshest stamp on the target election's rows is what the page
is asserting. Wayback capture time is NOT usable here: the 2024-11-01 00:57 UTC
capture holds Florida's 10/31 8:15AM reading, so dating it by the crawl would
shift Florida a full day along the days-to-election axis.
"""

from __future__ import annotations

import html
import json
import logging
import re
from datetime import date, datetime, timedelta

from ..calendar import election_date
from ..normalize import (
    METHOD_INPERSON, METHOD_MAIL, PARTY_DEM, PARTY_NPA, PARTY_OTH, PARTY_REP,
)
from ..schema import TIER_SCRAPER, CountyDay, MethodDay, StateDay
from . import _fips, _methods, _net
from .base import Adapter, FetchResult, NotYetPublished, SchemaDrift, SourceError

log = logging.getLogger(__name__)

URL = ("https://countyfilesvbm-ev.floridados.gov"
       "/VoteByMailEarlyVotingReports/PublicStats")

ROW_OUTSTANDING = "vote-by-mail provided (not yet returned)"
ROW_VOTED_MAIL = "voted vote-by-mail"
ROW_VOTED_EARLY = "voted early"

#: Header labels we require, lowercased. Their absence is drift.
PARTY_HEADERS = {
    "republican": PARTY_REP,
    "democrat": PARTY_DEM,
    "other": PARTY_OTH,
    "no party affiliation": PARTY_NPA,
}

_PARTY_FIELD = {
    PARTY_DEM: "party_dem", PARTY_REP: "party_rep",
    PARTY_NPA: "party_npa", PARTY_OTH: "party_oth",
}

#: The first header cell of the statewide summary table, and the first two of a
#: per-county table. This is how a table is identified; see the module docstring
#: on why position will not do.
SUMMARY_HEADER = "stats type"
COUNTY_HEADERS = ("county", "election")

#: How Florida labels a general election in the `Election` column: an internal
#: election id, a dash, and the word General. Everything else it has ever
#: printed there is something we must not publish as the general —
#: "43887 - Primary", "43886 - Pres. Pref. Primary",
#: "49898 - 2025 Special Primary CD 1 & 6".
_GENERAL = re.compile(r"^\d+\s*-\s*general$")

#: How far either side of Election Day a `Compiled` stamp may fall and still be
#: read as that cycle's general. Florida opens the general's books when it mails
#: the first UOCAVA ballots (2024: a 09/08 compile, 58 days out) and keeps
#: canvassing for a fortnight after; the window is deliberately much wider than
#: both, because its job is only to catch a general from a DIFFERENT cycle.
WINDOW_BEFORE = timedelta(days=210)
WINDOW_AFTER = timedelta(days=90)

_TAG = re.compile(r"<[^>]+>")
_DATE = re.compile(r"\b\d{2}/\d{2}/\d{4}\b")


def _text(fragment: str) -> str:
    return " ".join(html.unescape(_TAG.sub(" ", fragment)).split())


def _tables(markup: str) -> list[str]:
    return re.findall(r"<table[^>]*>(.*?)</table>", markup, re.S)


def _rows(table: str) -> list[list[str]]:
    out = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", table, re.S):
        cells = re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)
        if cells:
            out.append([_text(c) for c in cells])
    return out


def _headers(table: str) -> list[str]:
    return [_text(t).lower() for t in re.findall(r"<th[^>]*>(.*?)</th>", table, re.S)]


def _int(raw: str) -> int:
    """Parse a count. Florida pads some values with a leading zero ('01' == 1)."""
    text = raw.replace(",", "").strip()
    if not text:
        raise SchemaDrift("FL: empty count cell")
    if not text.isdigit():
        raise SchemaDrift(f"FL: non-numeric count {raw!r}")
    return int(text)


def _add(*values: int | None) -> int | None:
    """Sum what is reported. All-None stays None -- see THE BLANK RULE."""
    known = [v for v in values if v is not None]
    return sum(known) if known else None


def _day(cell: str) -> date | None:
    hit = _DATE.search(cell)
    if hit is None:
        return None
    return datetime.strptime(hit.group(0), "%m/%d/%Y").date()


class Grid:
    """One HTML table off the page: its header labels and its data rows."""

    __slots__ = ("headers", "rows")

    def __init__(self, table: str) -> None:
        self.headers = _headers(table)
        self.rows = _rows(table)

    def column(self, label: str) -> int | None:
        return self.headers.index(label) if label in self.headers else None

    def cell(self, row: list[str], label: str) -> str:
        index = self.column(label)
        return row[index] if index is not None and index < len(row) else ""

    @property
    def is_summary(self) -> bool:
        return self.headers[:1] == [SUMMARY_HEADER]

    @property
    def is_county(self) -> bool:
        return tuple(self.headers[:2]) == COUNTY_HEADERS


def _grids(markup: str) -> tuple[list[Grid], list[Grid]]:
    """(statewide summary tables, per-county tables), identified by header."""
    grids = [Grid(t) for t in _tables(markup)]
    counties = [g for g in grids if g.is_county]
    if len(counties) != 3:
        raise SchemaDrift(
            f"FL: expected 3 per-county tables on PublicStats, found {len(counties)} "
            f"(of {len(grids)} tables)"
        )
    return [g for g in grids if g.is_summary], counties


def _columns(grid: Grid) -> dict[str, int]:
    """Map party bucket -> column index, by HEADER NAME not position."""
    if not grid.headers:
        raise SchemaDrift("FL: table has no headers")
    columns: dict[str, int] = {}
    for index, label in enumerate(grid.headers):
        if label in PARTY_HEADERS:
            columns[PARTY_HEADERS[label]] = index
        elif label == "total":
            columns["total"] = index
    missing = (set(PARTY_HEADERS.values()) | {"total"}) - set(columns)
    if missing:
        raise SchemaDrift(f"FL: table missing columns {sorted(missing)}")
    return columns


def _counts(row: list[str], columns: dict[str, int]) -> dict[str, int]:
    counts = {}
    for key, index in columns.items():
        if index >= len(row):
            raise SchemaDrift(f"FL: row shorter than header ({len(row)} cells)")
        counts[key] = _int(row[index])

    # Florida publishes its own Total on every row; a party split that does not
    # reproduce it means we mis-read a column, so refuse rather than publish.
    parts = sum(counts[k] for k in _PARTY_FIELD)
    if parts != counts["total"]:
        raise SchemaDrift(
            f"FL: party columns sum to {parts} but row Total is {counts['total']}"
        )
    return counts


def _election(counties: list[Grid], cycle: int) -> str:
    """The `Election` label for `cycle`'s general, or raise.

    An `Election` cell that is not exactly "<id> - General" is never used: the
    label beside it is that cycle's PRIMARY, and the two are one digit apart
    (2024's were 43887 and 43888). Two DIFFERENT generals on one page is drift,
    not a choice we get to make.
    """
    generals, offered = set(), set()
    for grid in counties:
        for row in grid.rows:
            label = grid.cell(row, "election").strip()
            if not label:
                continue
            offered.add(label)
            if _GENERAL.match(label.lower()):
                generals.add(label)
    if len(generals) > 1:
        raise SchemaDrift(f"FL: two general elections on one page {sorted(generals)}")
    if not generals:
        raise NotYetPublished(
            f"FL: PublicStats is not showing a general election "
            f"(it shows {sorted(offered) or ['no election at all']}); "
            f"the {cycle} general is not open on this page"
        )
    return generals.pop()


class Snapshot:
    """One reading of PublicStats: an as-of date and one election's tables."""

    __slots__ = ("as_of", "election", "summary", "counties")

    def __init__(self, as_of: date, election: str,
                 summary: dict[str, dict[str, int]] | None,
                 counties: list[dict[str, dict[str, int] | None]]) -> None:
        #: The latest `Compiled` date the page prints for this election.
        self.as_of = as_of
        #: Florida's own label for the election these numbers belong to.
        self.election = election
        #: The statewide summary, when the page carried exactly one and it is
        #: therefore unambiguously this election's. None on a crossover day,
        #: where the statewide row is summed from the counties instead.
        self.summary = summary
        #: [{fips, name, outstanding, mail, early}], one entry per county that
        #: appears in ANY of the three tables. A county absent from a table gets
        #: None there, never 0 -- Florida prints no "Voted Early" county rows at
        #: all until early voting opens.
        self.counties = counties


def _county_map(grid: Grid, election: str) -> dict[str, dict]:
    """{fips: counts} for the rows of `grid` that belong to `election`."""
    columns = _columns(grid)
    out: dict[str, dict] = {}
    unknown: set[str] = set()
    for row in grid.rows:
        name = row[0].strip()
        if not name or grid.cell(row, "election").strip() != election:
            continue
        hit = _fips.lookup("FL", name)
        if hit is None:
            unknown.add(name)
            continue
        fips, canonical = hit
        entry = _counts(row, columns)
        entry["name"] = canonical
        out[fips] = entry
    if unknown:
        raise SchemaDrift(f"FL: unrecognised county names {sorted(unknown)[:5]}")
    return out


def _as_of(summaries: list[Grid], counties: list[Grid], election: str) -> date:
    """The latest `Compiled` date the page prints for this election.

    Verified against every archived 2022 and 2024 capture: the newest county
    stamp for an election and the newest summary stamp agree on the day, every
    time. They are pooled anyway so that a page which stops stamping one of the
    two still dates itself.
    """
    days: list[date] = []
    for grid in counties:
        for row in grid.rows:
            if grid.cell(row, "election").strip() == election:
                found = _day(grid.cell(row, "compiled"))
                if found:
                    days.append(found)
    if len(summaries) == 1:
        for row in summaries[0].rows:
            found = _day(summaries[0].cell(row, "compiled"))
            if found:
                days.append(found)
    if not days:
        raise NotYetPublished(
            f"FL: PublicStats prints no Compiled stamp for {election!r}, so the "
            f"reading cannot be dated"
        )
    return max(days)


def parse(markup: str, cycle: int) -> Snapshot:
    """One PublicStats page, read as `cycle`'s general.

    Raises NotYetPublished when the page is showing a different election (or one
    that is not dated), and SchemaDrift when it is showing this one in a shape we
    cannot read.
    """
    summaries, counties = _grids(markup)
    election = _election(counties, cycle)
    as_of = _as_of(summaries, counties, election)

    anchor = election_date(cycle)
    if not (anchor - WINDOW_BEFORE <= as_of <= anchor + WINDOW_AFTER):
        raise NotYetPublished(
            f"FL: {election!r} is compiled {as_of.isoformat()}, outside the "
            f"{cycle} general's window around {anchor.isoformat()}"
        )

    summary = None
    if len(summaries) == 1:
        # One live election, so the summary is unambiguously its own. Verified
        # exact against the county sum on every single-election capture.
        grid = summaries[0]
        columns = _columns(grid)
        summary = {row[0].strip().lower(): _counts(row, columns) for row in grid.rows}
        for required in (ROW_OUTSTANDING, ROW_VOTED_MAIL, ROW_VOTED_EARLY):
            if required not in summary:
                raise SchemaDrift(f"FL: summary table has no {required!r} row")

    outstanding = _county_map(counties[0], election)
    mail = _county_map(counties[1], election)
    early = _county_map(counties[2], election)

    rows = []
    for fips in sorted(set(outstanding) | set(mail) | set(early)):
        o, m, e = outstanding.get(fips), mail.get(fips), early.get(fips)
        rows.append({
            "fips": fips,
            "name": (m or e or o or {}).get("name", ""),
            "outstanding": o, "mail": m, "early": e,
        })
    return Snapshot(as_of, election, summary, rows)


def _bucket(entry: dict | None, key: str) -> int | None:
    return None if entry is None else entry[key]


def to_result(snapshot: Snapshot, cycle: int, day: date) -> FetchResult:
    """Canonical rows for one snapshot, dated `day`."""
    county_rows: list[CountyDay] = []
    totals = {key: None for key in ("outstanding", "mail", "early")}
    party_totals = {key: None for key in _PARTY_FIELD}

    method_rows: list[MethodDay] = []

    for row in snapshot.counties:
        mail_returned = _bucket(row["mail"], "total")
        inperson = _bucket(row["early"], "total")
        pending = _bucket(row["outstanding"], "total")
        cast = _add(mail_returned, inperson)
        if not cast and not pending:
            # Nothing provided and nothing cast: Florida is printing the county
            # because it prints all 67, not because anything has happened.
            continue
        # The crosstab, before the two channels are added together below. A
        # channel Florida prints no county row for is ABSENT here, never a row of
        # zeros -- it prints no "Voted Early" rows at all until early voting
        # opens. A channel it prints as 0 is a reported zero and is kept. The
        # skip above applies to both tables, so a county-day is either in both or
        # in neither.
        for entry, band in ((row["mail"], METHOD_MAIL), (row["early"], METHOD_INPERSON)):
            if entry is None:
                continue
            method_rows.append(MethodDay(
                cycle=cycle, state="FL", county_fips=row["fips"], day=day,
                method=band, county_name=row["name"],
                ballots_total=entry["total"],
                **{field: entry[key] for key, field in _PARTY_FIELD.items()},
            ))
        county_rows.append(CountyDay(
            cycle=cycle, state="FL", county_fips=row["fips"], day=day,
            county_name=row["name"],
            ballots_total=cast,
            mail_returned=mail_returned,
            inperson=inperson,
            **{field: _add(_bucket(row["mail"], key), _bucket(row["early"], key))
               for key, field in _PARTY_FIELD.items()},
        ))
        totals["outstanding"] = _add(totals["outstanding"], pending)
        totals["mail"] = _add(totals["mail"], mail_returned)
        totals["early"] = _add(totals["early"], inperson)
        for key in _PARTY_FIELD:
            party_totals[key] = _add(
                party_totals[key],
                _add(_bucket(row["mail"], key), _bucket(row["early"], key)),
            )

    if snapshot.summary is not None:
        # Florida's own headline, which is what `fetch` has always published.
        voted_mail = snapshot.summary[ROW_VOTED_MAIL]
        voted_early = snapshot.summary[ROW_VOTED_EARLY]
        pending = snapshot.summary[ROW_OUTSTANDING]
        mail_returned = voted_mail["total"]
        inperson = voted_early["total"]
        requested = pending["total"] + voted_mail["total"]
        party = {field: voted_mail[key] + voted_early[key]
                 for key, field in _PARTY_FIELD.items()}
        cast = mail_returned + inperson
        outstanding_total = pending["total"]
    else:
        # A crossover day: two elections, two summaries, and no way to tell which
        # summary is which. Sum this election's counties instead -- the same
        # derivation every other adapter in this repo uses.
        mail_returned, inperson = totals["mail"], totals["early"]
        requested = _add(totals["outstanding"], totals["mail"])
        party = {field: party_totals[key] for key, field in _PARTY_FIELD.items()}
        cast = _add(mail_returned, inperson)
        outstanding_total = totals["outstanding"]

    if not cast and not outstanding_total:
        # Nothing has happened yet -- not a failure, and emitting a row of zeros
        # would claim Florida is reporting when it has nothing to report.
        raise NotYetPublished("FL: no ballots provided or cast yet")

    result = FetchResult(state_rows=[StateDay(
        cycle=cycle, state="FL", day=day,
        ballots_total=cast,
        mail_returned=mail_returned,
        inperson=inperson,
        # Ballots the state has PROVIDED: still outstanding, plus those already
        # returned. Florida does not publish a request count separate from this.
        mail_requested=requested,
        **party,
    )])
    result.county_rows = county_rows
    _methods.attach(result, method_rows)
    return result


# --------------------------------------------------------------------------
# The archived series. PublicStats is overwritten in place -- it holds today's
# position and nothing else -- so a past cycle's daily curve exists only in the
# Internet Archive. This runs in `backfill`, never in the daily job.
#
# VERIFIED 2026-09-06 from this network, via the CDX API:
#   countyballotfiles.floridados.gov/VoteByMailEarlyVotingReports/PublicStats
#       136 distinct captures in 2024 and 78 in 2022, every one HTTP 200
#   countyfilesvbm-ev.floridados.gov/VoteByMailEarlyVotingReports/PublicStats
#       archived only from 2025 -- 0 captures in 2024
#   countyballotfiles.elections.myflorida.com/FVRSCountyBallotReports/
#       AbsenteeEarlyVotingReports/PublicStats
#       103 distinct captures (97 of them 200) spanning 2016-2020, then HTTP 301
#       redirects; the same four-table layout, but no cycle this repo tracks
#       lives there, so it is not swept
# Florida moved host twice without changing the page, so both live paths are
# swept and the answers are merged on the page's own Compiled date.
# --------------------------------------------------------------------------
CDX_URL = "http://web.archive.org/cdx/search/cdx"

#: `id_` asks the Wayback Machine for the ORIGINAL bytes rather than a rewritten
#: page. PublicStats is server-rendered HTML, so the rewrite is survivable, but
#: the original is what the parser was written against.
WAYBACK_SNAPSHOT = "https://web.archive.org/web/{stamp}id_/{url}"

#: Both hosts the page has lived at while a cycle this repo tracks was running.
ARCHIVED_URLS = (
    "https://countyballotfiles.floridados.gov/VoteByMailEarlyVotingReports/PublicStats",
    URL,
)

FIRST_CYCLE = 2022

#: 136 distinct 2024 captures is the largest cycle we have seen; 400 leaves room
#: for a cycle the Archive crawled harder without ever running away.
MAX_ARCHIVE_PROBES = 400

#: web.archive.org is slower and less tolerant than a state host, and a backfill
#: walks two hundred captures of one URL. See DEFAULT_MIN_INTERVAL in _net.
ARCHIVE_MIN_INTERVAL = 1.0


def _archive_stamps(url: str, cycle: int) -> list[str]:
    """Wayback timestamps of every distinct version of `url` in `cycle`'s window.

    `collapse=digest` is what turns hundreds of captures into the handful of
    genuinely distinct readings: the crawler visits far more often than Florida
    recompiles, and an unchanged page is one day, not five.
    """
    anchor = election_date(cycle)
    query = {
        "url": url, "output": "json", "fl": "timestamp,statuscode,digest",
        "filter": "statuscode:200", "collapse": "digest",
        "from": (anchor - WINDOW_BEFORE).strftime("%Y%m%d"),
        "to": (anchor + WINDOW_AFTER).strftime("%Y%m%d"),
        "limit": str(MAX_ARCHIVE_PROBES),
    }
    host = url.split("//", 1)[-1].split("/", 1)[0]
    try:
        body = _net.get(CDX_URL, state="FL", filename=f"cdx-{host}-{cycle}.json",
                        params=query, min_bytes=2, min_interval=ARCHIVE_MIN_INTERVAL)
    except _net.Missing:
        # The CDX API answers "nothing archived" with an empty body, which
        # _net.get reports as Missing. That is absence, not a fault.
        return []
    try:
        rows = json.loads(body.decode("utf-8", errors="replace"))
    except ValueError as exc:
        raise SourceError(f"FL: the Wayback CDX index was not JSON: {exc}") from exc
    return [row[0] for row in rows[1:]]


class FLScraper(Adapter):
    """Tier 1 for Florida: the Division of Elections' PublicStats page.

    Statewide and 67 county rows, outstanding vs voted-by-mail vs voted-early,
    split by party registration, published under the page's own Compiled date.
    """

    state = "FL"
    name = "fl-doe"
    tier = TIER_SCRAPER

    def fetch(self, cycle: int, as_of: date) -> FetchResult:
        try:
            body = _net.get(URL, state=self.state, filename="publicstats.html",
                            min_bytes=2048)
        except _net.Missing as exc:
            raise NotYetPublished(f"FL: {URL} not available") from exc

        snapshot = parse(body.decode("utf-8", errors="replace"), cycle)
        # A snapshot never dates ahead of the run: a backfill asking for an
        # earlier day must not be handed today's numbers under today's date.
        day = min(snapshot.as_of, as_of)
        return to_result(snapshot, cycle, day)

    def fetch_history(self, cycle: int) -> FetchResult:
        """The whole archived daily curve for a past cycle, from the Wayback Machine.

        Every distinct archived capture is fetched and parsed, and the LAST
        capture of any given Compiled date wins: Florida recompiles twice a day
        in the window (8AM and 11AM stamps both appear on 2024-11-04) and the
        later reading is the one that is true of that day.

        Captures showing the primary, the presidential preference primary or a
        special are skipped, not guessed at — `parse` raises NotYetPublished for
        them, which here means "this capture is not about the election we asked
        for" rather than "stop the ladder".
        """
        if cycle < FIRST_CYCLE:
            raise NotYetPublished(
                f"FL: PublicStats captures before {FIRST_CYCLE} live on a host "
                f"this adapter does not sweep"
            )
        by_day: dict[date, Snapshot] = {}
        seen = 0
        for url in ARCHIVED_URLS:
            for stamp in _archive_stamps(url, cycle):
                snapshot_url = WAYBACK_SNAPSHOT.format(stamp=stamp, url=url)
                try:
                    # The stamp has to be in the cache filename: every capture is
                    # the SAME URL, so a bare basename would make them one file.
                    body = _net.get(
                        snapshot_url, state=self.state,
                        filename=f"publicstats-{stamp}.html",
                        use_cache=True, min_bytes=2048,
                        min_interval=ARCHIVE_MIN_INTERVAL,
                    )
                except SourceError as exc:
                    log.debug("FL: archived capture %s unusable (%s)", stamp, exc)
                    continue
                seen += 1
                try:
                    snapshot = parse(body.decode("utf-8", errors="replace"), cycle)
                except NotYetPublished as exc:
                    log.debug("FL: capture %s skipped (%s)", stamp, exc)
                    continue
                by_day[snapshot.as_of] = snapshot
        if not by_day:
            raise NotYetPublished(
                f"FL: nothing archived for the {cycle} general ({seen} captures read)"
            )
        result = FetchResult()
        _methods.attach(result, [])
        for day in sorted(by_day):
            try:
                one = to_result(by_day[day], cycle, day)
            except NotYetPublished:
                # A dated reading with nothing in it yet. Not a row.
                continue
            result.extend(one)
            # `FetchResult.extend` merges its three FIELDS and cannot see an
            # attribute, so the method rows have to be carried across by hand or
            # every archived day but the last would be dropped. See _methods.py.
            _methods.extend(result, _methods.rows_of(one))
        log.info("FL: %s archived days from %s captures", len(result.state_rows), seen)
        return result
