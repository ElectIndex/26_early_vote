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
survive the election -- they are the daily archive `fetch_history` walks. The
dated file is authoritative when it exists because its name states the as-of
date; otherwise we take whichever election-keyed copy reports the most ballots
sent, since the count only ever rises.

Wisconsin does not register voters by party, so every party_* field is None.
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
from .base import Adapter, FetchResult, NotYetPublished, SchemaDrift, SourceError

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


def parse(body: bytes, cycle: int, day: date) -> FetchResult:
    """Parse one WEC county absentee export into canonical rows.

    `day` comes from the caller: the election-keyed export carries no as-of date
    inside it, and the dated export carries it in the file name.
    """
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
        election, _hindi, jurisdiction, _apps, sent, returned, in_person = row[:len(HEADER)]
        name = " ".join(str(jurisdiction or "").split())
        if not name:
            continue

        # A file left over from another cycle would otherwise publish 2024's
        # counts under the 2026 key.
        if not _norm(election).startswith(str(cycle)):
            raise SchemaDrift(
                f"WI: export is for {election!r}, not the {cycle} cycle"
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
        """Every dated file WEC still serves for a past cycle, as a daily series.

        Unlike Ohio and Michigan, Wisconsin does keep a per-day archive: the
        dated copies outlive the election even though the election-keyed export
        is deleted with it. The window has holes on the days WEC uploaded
        without renaming, and a hole is skipped rather than interpolated.
        """
        if cycle >= date.today().year:
            raise NotYetPublished(f"WI: {cycle} is not an archived cycle")
        polls = election_date(cycle)
        result = FetchResult()
        day = polls - timedelta(days=HISTORY_LEAD_DAYS)
        while day <= polls + timedelta(days=HISTORY_TRAIL_DAYS):
            try:
                one = self._dated(cycle, day, use_cache=True)
            except SourceError as exc:
                log.warning("WI: %s unusable (%s)", day.isoformat(), exc)
                one = None
            if one is not None:
                result.extend(one)
            day += timedelta(days=1)
        if not result:
            raise NotYetPublished(f"WI: no archived daily files for {cycle}")
        return result
