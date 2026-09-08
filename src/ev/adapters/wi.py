"""Wisconsin: the Elections Commission's absentee counts, by county.

WEC exports the same absentee report at two levels -- `AbsenteeCounts_County_*`
and `AbsenteeCounts_Muni_*`. Michigan's crosswalk problem (see mi.py) does not
arise here because we simply take the county file: WEC has already done the
municipal roll-up itself, on the unsuppressed data, and its last row is a TOTAL
that we publish as the statewide figure rather than summing 72 counties of our
own. The municipal file is never fetched.

Four columns carry the data, and the arithmetic between them is the one thing
worth reading carefully:

    AbsenteeApplications  requests received
    BallotsSent           ballots actually issued  -> mail_requested
    BallotsReturned       ALL absentee ballots back, in-person included
    InPersonAbsentee      of which cast in person  -> inperson

`BallotsReturned` is inclusive: on 2024-11-07 it read 1,558,257 against 957,467
in person, so `mail_returned` is the difference, 600,790. That subtraction is
exact arithmetic on two published columns -- but only when both are published.
WEC leaves `InPersonAbsentee` blank on some rows (the 2026 partisan primary's
TOTAL row is blank while its counties read 0), and a blank there makes the mail
split genuinely unknown, so `mail_returned` goes to None rather than silently
reporting the whole return count as mail. `ballots_total` is `BallotsReturned`
unchanged and is unaffected. See THE BLANK RULE in schema.py.

**Two filename schemes, because WEC uses both.** During a live election the
export sits at a stable, election-keyed name that is overwritten in place and
carries no date inside; Drupal renames it with a `_1`, `_2`, ... suffix whenever
a new copy is uploaded beside the old one, which is what happened through October
2024. Separately, WEC posts a dated copy for most days, and those dated files
survive the election -- they are the daily archive `fetch_history` walks for
2024. The dated file is authoritative when it exists because its name states the
as-of date; otherwise we take whichever election-keyed copy reports the most
ballots sent, since the count only ever rises.

**The Election column is the election, and it is checked on every row.** WEC
writes its own label into the file (`2024 General Election`, `2022 General
Election`, `2026 Partisan Primary`), and only the cycle's GENERAL is publishable.
Checking the year alone is not enough and never was: the 2022 files carry no
election in their NAME at all -- the live store serves
`AbsenteeCounts_County__1.csv`, which is 5,419 bytes of `2022 Partisan Primary`
that a year-only guard would have published as the 2022 general's curve. A file
that names a different election of the same year raises NotYetPublished (that
election's data is not this election's, and it does not exist yet); a file that
names a different YEAR is a stale export left in place, and raises SchemaDrift.

Wisconsin does not register voters by party, so every party_* field is None.

## 2022: the daily reports were Drupal NODES, and only the Archive kept them

VERIFIED 2026-09-08 from this network. The 2024 scheme does not reach backwards:
`County Absentee Counts as of October 25, 2022.csv` and its siblings 404, and the
Wayback CDX index has no `County Absentee Counts as of ..., 2022` under
`/sites/default/files/documents/` at all. That is where an earlier sweep stopped,
and it was the wrong conclusion -- WEC did publish a daily county file in 2022,
just not under a dated name.

In 2022 each day was a NODE on `elections.wi.gov/statistics-data/absentee-
statistics`, titled `Absentee Ballot Report - November 8, 2022 General Election`
and slugged `.../resources/statistics/absentee-ballot-report-november-8-2022-
general-election{,-0,-1,...,-20}`. **The node states the day in prose** ("Monday,
November 7, 2022") and carries two attachments, a county CSV and a municipal one,
both uploaded as `AbsenteeCounts_County_.csv` with the election label left blank.
Drupal stores them behind `/media/<id>/download`, and because every day got a
fresh media id, the id IS the day -- there is no overwriting to disentangle.

Neither half of that is reachable live: node pages and `/media/<id>/download`
both sit behind the Cloudflare rule that 403s automated requests (verified today:
`/media/17811/download` -> 403 "Just a moment...", while
`/sites/default/files/documents/…csv` -> 200). Both halves ARE in the Wayback
Machine, so `HISTORY_MEDIA` below is the harvest: all 22 node pages were read for
their stated date and media id, and every one of those media ids has an archived
200 `text/csv` capture. This is the same route `fl.py`, `de.py` and `or.py`
already take for their archived cycles.

The table is checked rather than trusted. Every file must name itself the 2022
General Election (`parse`), must carry all 72 counties, and the whole series must
be non-decreasing -- 125,903 ballots back on 10-12 rising to 741,795 on 11-08,
with in-person absentee opening on 10-25 exactly as Wisconsin's two-week window
says it should. A mis-keyed day would break monotonicity, and the test asserts it.
"""

from __future__ import annotations

import csv
import io
import logging
import urllib.parse
from datetime import date, timedelta

from ..calendar import election_date
from ..schema import TIER_SCRAPER, CountyDay, StateDay
from . import _fips
from ._net import Missing, get, looks_like_html
from .base import (
    Adapter, AdapterError, FetchResult, NotYetPublished, SchemaDrift, SourceError,
)

log = logging.getLogger(__name__)

#: WEC's Drupal file store. The site's HTML pages sit behind a Cloudflare rule
#: that 403s automated requests, but files under this prefix are served
#: directly -- robots.txt and every CSV below answered 200 to a plain GET.
#: VERIFIED live for the 2024 general and the 2026 partisan primary.
BASE = "https://elections.wi.gov/sites/default/files/documents/"

#: The live export. `{election}` is WEC's own election label.
#: VERIFIED live as "AbsenteeCounts_County_2026 Partisan Primary.csv".
CURRENT_FILE = "AbsenteeCounts_County_{election}.csv"

#: WEC's label for a cycle's November general, as it appears both in the file
#: name and in the CSV's own Election column ("2024 General Election").
GENERAL_LABEL = "{cycle} General Election"

#: Drupal's collision suffixes. A fresh upload beside an existing file becomes
#: `..._1.csv`, `..._2.csv` and so on, so the current export is not always the
#: bare name -- through October 2024 it was `_8` and `_9`.
SUFFIXES = ("",) + tuple(f"_{n}" for n in range(1, 10))

#: The dated copy. VERIFIED live for 2024-10-02 through 2024-11-07, with gaps on
#: the days WEC uploaded without renaming.
DATED_FILE = "County Absentee Counts as of {month} {day}, {year}.csv"

HEADER = ("election", "hindi", "jurisdiction", "absenteeapplications",
          "ballotssent", "ballotsreturned", "inpersonabsentee")

TOTAL_LABELS = {"total", "totals", "statewide", "state total", "state totals"}

#: How far back from Election Day `fetch_history` looks for dated files.
HISTORY_LEAD_DAYS = 45
#: WEC keeps posting for a couple of days after the election as counties report.
HISTORY_TRAIL_DAYS = 4

#: web.archive.org is slower and less tolerant than a state host. Same reasoning
#: as fl.py's ARCHIVE_MIN_INTERVAL; see DEFAULT_MIN_INTERVAL in _net.
ARCHIVE_MIN_INTERVAL = 1.0

#: `id_` asks for the ORIGINAL bytes rather than a rewritten page. These are
#: CSVs, so a rewrite would not touch them, but the original is what the parser
#: was written against.
WAYBACK_MEDIA = ("https://web.archive.org/web/{stamp}id_/"
                 "https://elections.wi.gov/media/{media}/download")

#: The 2022 general's daily county reports: (as-of day, Wayback stamp, Drupal
#: media id). HARVESTED 2026-09-08, one row per node under
#: `/resources/statistics/absentee-ballot-report-november-8-2022-general-election`
#: (the bare slug plus `-0` .. `-20`). The DAY is the node's own prose date
#: ("Monday, November 7, 2022"), not a capture timestamp and not an inference;
#: the media id is that node's county attachment; the stamp is the first
#: archived 200 `text/csv` capture of that media id in the CDX index.
#:
#: Twenty-two consecutive publishing days, 2022-10-12 to 2022-11-08, with no
#: weekend gap over the final weekend. WEC's own note on every one of those
#: pages: "These reports were generated by the system at 7:30 a.m. each day."
HISTORY_MEDIA: dict[int, tuple[tuple[date, str, int], ...]] = {
    2022: (
        (date(2022, 10, 12), "20221015103543", 17281),
        (date(2022, 10, 13), "20221015103534", 17316),
        (date(2022, 10, 14), "20221015103524", 17326),
        (date(2022, 10, 17), "20221115203723", 17341),
        (date(2022, 10, 18), "20221115203715", 17366),
        (date(2022, 10, 19), "20221115203705", 17411),
        (date(2022, 10, 20), "20221115203654", 17451),
        (date(2022, 10, 21), "20221115203643", 17476),
        (date(2022, 10, 24), "20221115203633", 17486),
        (date(2022, 10, 25), "20221115203622", 17496),
        (date(2022, 10, 26), "20221115203610", 17521),
        (date(2022, 10, 27), "20221115203557", 17536),
        (date(2022, 10, 28), "20221109145633", 17566),
        (date(2022, 10, 31), "20221109145647", 17616),
        (date(2022, 11, 1), "20221109145728", 17666),
        (date(2022, 11, 2), "20221109145649", 17721),
        (date(2022, 11, 3), "20221108005756", 17731),
        (date(2022, 11, 4), "20221108005857", 17766),
        (date(2022, 11, 5), "20221109145434", 17791),
        (date(2022, 11, 6), "20221108005729", 17801),
        (date(2022, 11, 7), "20221108010029", 17811),
        (date(2022, 11, 8), "20221109145519", 17836),
    ),
}


def _norm(raw) -> str:
    return " ".join(str(raw or "").strip().lower().split())


def _count(value) -> int | None:
    """A count, or None for a blank cell -- which is unreported, not zero."""
    text = str(value or "").strip().replace(",", "")
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError as exc:
        raise SchemaDrift(f"WI: {value!r} is not a count") from exc


def _minus(total: int | None, part: int | None) -> int | None:
    """`total - part`, or None if either side was not reported.

    An unreported in-person count does not make the whole return count mail --
    it makes the split unknown, which is a blank.
    """
    if total is None or part is None:
        return None
    return total - part


def dated_filename(day: date) -> str:
    """WEC's dated file name. The day is written without a leading zero."""
    return DATED_FILE.format(month=day.strftime("%B"), day=day.day, year=day.year)


def parse(body: bytes, cycle: int, day: date, *, election: str | None = None
          ) -> FetchResult:
    """Parse one WEC county absentee export into canonical rows.

    `day` comes from the caller: the election-keyed export carries no as-of date
    inside it, and the dated export carries it in the file name.

    `election` is WEC's own label for the election the file must be about, and it
    defaults to the cycle's GENERAL. Nothing in the fetch path ever passes it --
    naming another election is a deliberate act, done only to read a primary's
    file on purpose (the 2026 partisan primary fixture). See the module docstring
    on why the year alone is not a sufficient check.
    """
    expected = election or GENERAL_LABEL.format(cycle=cycle)
    wanted = _norm(expected)
    if looks_like_html(body):
        raise SourceError("WI: absentee export came back as HTML, not CSV")

    reader = csv.reader(io.StringIO(body.decode("utf-8-sig", errors="replace")))
    try:
        header = tuple(_norm(c) for c in next(reader))
    except StopIteration as exc:
        raise SchemaDrift("WI: absentee export is empty") from exc
    if header[:len(HEADER)] != HEADER:
        raise SchemaDrift(f"WI: unexpected header {header!r}, wanted {HEADER!r}")

    state_rows: list[StateDay] = []
    county_rows: list[CountyDay] = []
    unknown: list[str] = []

    for row in reader:
        if not row or len(row) < len(HEADER):
            continue
        named, _hindi, jurisdiction, _apps, sent, returned, in_person = row[:len(HEADER)]
        name = " ".join(str(jurisdiction or "").split())
        if not name:
            continue

        # A file left over from another cycle would otherwise publish 2024's
        # counts under the 2026 key.
        label = _norm(named)
        if not label.startswith(str(cycle)):
            raise SchemaDrift(
                f"WI: export is for {named!r}, not the {cycle} cycle"
            )
        # Right year, WRONG ELECTION -- the trap the 2022 filenames set, and the
        # one the 2026 dated file could set live. The general's data does not
        # exist yet, and a primary's turnout on the general's curve is a
        # confident wrong answer, so this STOPS the ladder rather than falling
        # through to a tier that would answer for the general.
        if label != wanted:
            raise NotYetPublished(
                f"WI: this export is WEC's {named!r}, not {expected!r}"
            )

        sent, returned, in_person = _count(sent), _count(returned), _count(in_person)

        if _norm(name) in TOTAL_LABELS:
            state_rows.append(StateDay(
                cycle=cycle, state="WI", day=day,
                ballots_total=returned,
                mail_requested=sent,
                mail_returned=_minus(returned, in_person),
                inperson=in_person,
                # Wisconsin has no party registration. Never 0.
                party_dem=None, party_rep=None, party_oth=None, party_npa=None,
            ))
            continue

        hit = _fips.lookup("WI", name)
        if hit is None:
            unknown.append(name)
            continue
        fips, canonical = hit
        county_rows.append(CountyDay(
            cycle=cycle, state="WI", county_fips=fips, day=day,
            county_name=canonical,
            ballots_total=returned,
            mail_returned=_minus(returned, in_person),
            inperson=in_person,
            party_dem=None, party_rep=None, party_oth=None, party_npa=None,
        ))

    if unknown:
        raise SchemaDrift(f"WI: unrecognised county names {sorted(set(unknown))[:5]}")
    if not county_rows:
        raise SchemaDrift("WI: absentee export produced no county rows")
    return FetchResult(state_rows=state_rows, county_rows=county_rows)


def _sent(result: FetchResult) -> int:
    """Ballots issued, for choosing between two copies of the live export."""
    for row in result.state_rows:
        if row.mail_requested is not None:
            return row.mail_requested
    return sum(r.ballots_total or 0 for r in result.county_rows)


class WIScraper(Adapter):
    """Tier 1 for Wisconsin: the WEC county absentee export."""

    state = "WI"
    name = "wi-wec"
    tier = TIER_SCRAPER

    def _get(self, filename: str, *, cycle: int, use_cache: bool) -> bytes | None:
        """Fetch one candidate file, or None if WEC has not posted it."""
        try:
            body = get(
                BASE + urllib.parse.quote(filename),
                state="WI", filename=f"{cycle}_{filename}",
                use_cache=use_cache, min_bytes=256,
            )
        except Missing:
            return None
        if looks_like_html(body):
            # WEC answers a missing file with a full styled page, sometimes at
            # HTTP 200 rather than 404.
            return None
        return body

    def _dated(self, cycle: int, day: date, *, use_cache: bool) -> FetchResult | None:
        body = self._get(dated_filename(day), cycle=cycle, use_cache=use_cache)
        return None if body is None else parse(body, cycle, day)

    def _archived(self, cycle: int, day: date, stamp: str, media: int
                  ) -> FetchResult | None:
        """One 2022 daily report, by its Drupal media id, out of the Archive.

        `use_cache=True` is correct here and only here: a capture of a fixed
        stamp of a media id that was never re-uploaded cannot change.
        """
        try:
            body = get(
                WAYBACK_MEDIA.format(stamp=stamp, media=media),
                state="WI", filename=f"{cycle}_media{media}.csv",
                use_cache=True, min_bytes=1024,
                min_interval=ARCHIVE_MIN_INTERVAL,
            )
        except Missing:
            return None
        if looks_like_html(body):
            return None
        return parse(body, cycle, day)

    def _current(self, cycle: int, day: date, *, use_cache: bool) -> FetchResult | None:
        """The best of the election-keyed copies, or None if none is posted."""
        label = GENERAL_LABEL.format(cycle=cycle)
        best: FetchResult | None = None
        for suffix in SUFFIXES:
            body = self._get(
                CURRENT_FILE.format(election=label + suffix), cycle=cycle,
                use_cache=use_cache,
            )
            if body is None:
                continue
            candidate = parse(body, cycle, day)
            if best is None or _sent(candidate) > _sent(best):
                best = candidate
        return best

    def fetch(self, cycle: int, as_of: date) -> FetchResult:
        dated = self._dated(cycle, as_of, use_cache=False)
        if dated is not None:
            return dated
        current = self._current(cycle, as_of, use_cache=False)
        if current is not None:
            return current
        raise NotYetPublished(
            f"WI: no absentee export posted for the {cycle} general as of "
            f"{as_of.isoformat()}"
        )

    def fetch_history(self, cycle: int) -> FetchResult:
        """A past cycle's daily series, by whichever scheme that cycle used.

        Unlike Ohio and Michigan, Wisconsin does keep a per-day archive -- but it
        has kept it two different ways, so this walks one route or the other and
        never both:

        * **2024 and later**: the DATED copies on WEC's own file store outlive the
          election even though the election-keyed export is deleted with it. The
          window has holes on the days WEC uploaded without renaming, and a hole
          is skipped rather than interpolated.
        * **2022**: there were no dated copies -- probing for them would be 50
          requests to WEC for files that were never posted. That cycle's daily
          reports were Drupal nodes whose attachments are keyed by media id, and
          `HISTORY_MEDIA` is the harvested date-to-id index. See the module
          docstring.

        A day that fails or names the wrong election is skipped and logged, never
        interpolated -- and, exactly as in `fetch`, every file is put through the
        same `parse`, so the general-election guard is identical on both paths.
        """
        if cycle >= date.today().year:
            raise NotYetPublished(f"WI: {cycle} is not an archived cycle")
        result = FetchResult()

        archived = HISTORY_MEDIA.get(int(cycle))
        if archived:
            for day, stamp, media in archived:
                try:
                    one = self._archived(cycle, day, stamp, media)
                except AdapterError as exc:
                    log.warning("WI: archived %s (media %s) unusable (%s)",
                                day.isoformat(), media, exc)
                    one = None
                if one is not None:
                    result.extend(one)
        else:
            polls = election_date(cycle)
            day = polls - timedelta(days=HISTORY_LEAD_DAYS)
            while day <= polls + timedelta(days=HISTORY_TRAIL_DAYS):
                try:
                    one = self._dated(cycle, day, use_cache=True)
                except AdapterError as exc:
                    log.warning("WI: %s unusable (%s)", day.isoformat(), exc)
                    one = None
                if one is not None:
                    result.extend(one)
                day += timedelta(days=1)

        if not result:
            raise NotYetPublished(f"WI: no archived daily files for {cycle}")
        return result
