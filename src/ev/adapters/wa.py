"""Washington: the Secretary of State's daily statewide Ballot Status Report.

This is the best during-season file any state publishes. Washington votes
entirely by mail, and the SoS posts a dated ZIP each day of the return period
holding **one row per returned ballot** -- 515,318 of them in the 2024 general's
2024-10-22 snapshot -- each carrying its own county, return method, ballot
status, gender and, crucially, its own `Received Date`:

    https://www.sos.wa.gov/sites/default/files/current_election/Statewide2024-10-22.zip

Because every ballot is dated, **one download rebuilds the entire daily curve**,
North Carolina style: a day we fail to run is not a day of Washington's series
lost forever, and a past cycle is one file rather than an archive hunt.

Two transport facts, both verified 2026-09-06 and both the reason this state was
written off before:

* The `ballot-status-reports` landing PAGE sits behind a Cloudflare **managed
  challenge** and answers 403 to everything, including a full browser
  fingerprint through `curl_cffi` (chrome, chrome124/131/133a/136, safari,
  firefox and edge profiles were all tried; all 403). So this module never
  touches it and never needs to -- the URL above is constructible from the date.
* The `/sites/default/files/` path is **not** challenged. Today it answers an
  honest 404, from plain `requests` and from `curl_cffi` alike, because
  `current_election/` is purged between cycles. That 404 is `NotYetPublished`,
  which is the correct and honest state of Washington on any day before the SoS
  says returns begin -- **2026-10-20** for this cycle, per its own page.

Judgement calls, all locked by tests:

* **The election is named in every row, so it cannot be mistaken.** The
  `Election` column reads `General Nov  5 2024` (Washington's own double space).
  Rows are kept only when that parses to exactly this cycle's Election Day; a
  file holding only a primary yields NotYetPublished rather than primary turnout
  published as the general's.

* **`ballots_total` is every ballot RETURNED, whatever its status.** The file is
  a list of returned ballots; `Ballot Status` describes how far processing has
  got (2024-10-22: 393,692 Accepted, 117,364 Received, 4,262 Rejected). A
  rejected ballot was still returned and many are later cured, so counting only
  the accepted ones would understate returns by a number that moves for reasons
  that are not turnout.

* **Coverage is gated exactly as `tx.py` gates Texas.** County uploads trickle
  in: the 2024-10-22 file carries 38 of 39 counties (Grant is absent) and the
  2024-10-23 file carries all 39; the 2025-10-24 file misses Okanogan. A
  statewide row summed over 38 counties looks exactly like a Washington total
  and is not one, so when a snapshot is short the county rows are published and
  NO StateDay is emitted at all.

* **Washington does not register voters by party.** The `Party` column exists
  and is empty on all 515,318 rows of the 2024 file and all 342,572 of the 2025
  one -- Washington has no party enrolment. All four party fields are None,
  never 0. See THE BLANK RULE in schema.py.

## The past cycles: 2024 yes, 2022 no

`current_election/` is purged when the next election opens, so the live host
serves nothing for either cycle -- every date 404s, honestly. What survives is in
the Wayback Machine, and the two cycles get opposite answers there.

**2024 exists.** VERIFIED 2026-09-08 via the CDX API: `Statewide2024-10-20.zip`
through `Statewide2024-11-19.zip` are archived, one per day, the `www` host
answering 200 `application/zip` (the bare host is the 301 in front of it).
`ARCHIVED` below pins the last of them, 138,719,074 bytes, because **one file is
the whole cycle**: every ballot in it carries its own `Received Date`, so a single
snapshot rebuilds the entire daily curve and a later snapshot is a superset of an
earlier one. It is also the snapshot most likely to carry all 39 counties, which
is what the statewide coverage gate needs.

An earlier version of this module read those captures and refused them anyway, on
the ground that web.archive.org "is a third-party host and not what a tier-1 state
scraper should be reading". That was a policy this repo does not actually hold:
`fl.py` rebuilds Florida's 2022 and 2024 county curves out of the CDX index,
`de.py` walks it for Delaware, and `or.py` pins Wayback timestamps by hand exactly
as `ARCHIVED` does here. `hi.py` and `ks.py` go further and cite a NEGATIVE CDX
result as sufficient proof that a state has no archive -- so the Archive was
already being trusted when it said "no" and refused when it said "yes". The
`id_` suffix returns the SoS's ORIGINAL bytes, unrewritten, and every row of them
is still checked against this cycle's Election Day before anything is published.

**2022 exists too, and it is LIVE.** ⚠️ It was written off once, and the way it
was written off is worth keeping because the same shape of mistake is easy to
repeat. Three checks were run and all three were about the 2024 URL scheme: no
`Statewide2022-*.zip` in the CDX index (true -- the dated `current_election/`
scheme begins with `Statewide2024-07-31.zip`), no exact-URL match for
`Statewide2022-11-08.zip` (true), and a domain sweep filtered on
`.*[Bb]allot.?[Ss]tatus.*` which returned only one-off specials (true, and
misleading: **CDX filters run against the urlkey, where a space is `%20`**, so
`.?` -- one optional character -- cannot bridge `ballot%20status` and the whole
2022 general was invisible to that pattern).

Sweeping the domain and grepping the urlkeys OFFLINE instead finds it at once,
in 10,247 distinct urlkeys captured between September 2022 and March 2023:

```
www2.sos.wa.gov/_assets/elections/research/ballot status report 2022-11-09 all other counties.zip
www2.sos.wa.gov/_assets/elections/research/ballot status report 2022-11-09 cr pi.zip
www2.sos.wa.gov/_assets/elections/research/ballot status report 2022-11-09 ki.zip
www2.sos.wa.gov/_assets/elections/research/ballot status report 2022-11-09 sn sp.zip
```

Three things about that scheme, all verified live 2026-09-08:

* **It is four files, not one**, split by county because the per-ballot file for
  King alone is 29 MB. `parse_parts` accumulates them into one set of tallies;
  see it for the two guards that only a split report needs.
* **It is on `www2.sos.wa.gov/_assets/`, which is NOT challenged.** All four
  answer 200 `application/x-zip-compressed` to plain `requests` today. The
  Cloudflare wall in front of the `ballot-status-reports` landing page is real
  and is still never touched; it simply was never what stood between us and this
  cycle. (`www.sos.wa.gov` 301s to `www2` for these, which is why the CDX rows
  for the `www` host are all `text/html` redirects and read as absence.)
* **The file is identical in shape to 2024** -- same 21 columns, same
  `General Nov  8 2022` double space, same empty `Party`, same per-ballot
  `Received Date`, 2,711,613 rows across the four parts covering all 39
  counties, disjoint. So one day rebuilds the whole 2022 curve exactly as one
  day rebuilds 2024's.

2022-11-09 is the pinned date rather than 11-10: the 11-10 set's "all other
counties A-K" part appears in the index only as a 301 and is not served, so that
date is twenty-odd counties short.
"""

from __future__ import annotations

import csv
import io
import logging
import re
import zipfile
from collections import defaultdict
from datetime import date, datetime, timedelta

from ..calendar import election_date
from ..normalize import (
    METHOD_INPERSON, METHOD_MAIL, method as _method, sex as _sex,
)
from ..schema import TIER_SCRAPER, CountyDay, DemoDay, StateDay
from . import _fips
from ._net import Missing, get, looks_like_html
from .base import Adapter, FetchResult, NotYetPublished, SchemaDrift, SourceError

log = logging.getLogger(__name__)

#: VERIFIED: this pattern is real and daily. `Statewide2024-10-22.zip` (18.1 MB),
#: `…-10-23.zip` (25.1 MB) and `Statewide2025-10-24.zip` (14.4 MB) all download
#: and parse. Live today every date 404s -- the SoS purges `current_election/`
#: between cycles and its own page says 2026 returns begin 2026-10-20.
BASE = "https://www.sos.wa.gov/sites/default/files/current_election"
ZIP_URL = BASE + "/Statewide{day}.zip"

#: How many days back to look for a posted file. Washington posts daily during
#: the return period, but a skipped day must not read as "nothing returned yet".
LOOKBACK_DAYS = 7

#: `id_` asks the Wayback Machine for the SoS's ORIGINAL bytes rather than a
#: rewritten response. These are zips, so a rewrite would corrupt them outright.
WAYBACK_SNAPSHOT = "https://web.archive.org/web/{stamp}id_/" + ZIP_URL

#: web.archive.org is slower and less tolerant than a state host, and this is a
#: 132MB file. Same reasoning as fl.py's ARCHIVE_MIN_INTERVAL.
ARCHIVE_MIN_INTERVAL = 1.0

#: Where the 2022 general's report still lives, on the SoS's own `www2` host.
#: One `{part}` per zip; see `ARCHIVED` and the module docstring.
PARTED_2022 = (
    "https://www2.sos.wa.gov/_assets/elections/research/"
    "ballot%20status%20report%202022-11-09%20{part}.zip"
)

#: The snapshot(s) that rebuild a past cycle, as (day, ((url, cache name), ...)).
#:
#: ONE DAY per cycle on purpose: every ballot in the file carries its own
#: Received Date, so the last usable snapshot of a cycle contains every earlier
#: day of it and `parse` trims the series at Election Day. What varies is how
#: many FILES that day is, which is a property of the cycle's publishing scheme
#: and not of this adapter.
#:
#: 2024 -- one statewide zip, from the Wayback Machine because `current_election/`
#: is purged between cycles. VERIFIED 2026-09-08: 2024-11-19 is 138,719,074 bytes
#: of `application/zip` holding `Ballot Status Report 2024-11-19.csv`.
#:
#: 2022 -- FOUR zips, LIVE on `www2.sos.wa.gov`, no archive needed. VERIFIED
#: 2026-09-08: all four answer 200 `application/x-zip-compressed`, they hold
#: 2,711,613 rows between them, their county sets are disjoint and together are
#: all 39. 2022-11-09 is the pin rather than 11-08 or 11-10 because it is the
#: LAST date whose parts are all available -- 11-10's "all other counties A-K"
#: exists only as a 301 in the index and 404s live, so that date is 20-odd
#: counties short and its statewide row would be suppressed anyway.
ARCHIVED: dict[int, tuple[date, tuple[tuple[str, str], ...]]] = {
    2022: (date(2022, 11, 9), tuple(
        (PARTED_2022.format(part=part.replace(" ", "%20")),
         f"BallotStatus2022-11-09_{part.replace(' ', '_')}.zip")
        for part in ("all other counties", "cr pi", "ki", "sn sp")
    )),
    2024: (date(2024, 11, 19), (
        (WAYBACK_SNAPSHOT.format(stamp="20241120010214", day="2024-11-19"),
         "Statewide2024-11-19.zip"),
    )),
}

REQUIRED = (
    "County", "Gender", "Election", "Ballot Status", "Received Date",
    "Return Method", "Party",
)

#: `General Nov  5 2024` -- note Washington's double space, which is why the
#: separator is \s+ rather than a literal.
_ELECTION = re.compile(r"^(?P<kind>[A-Za-z][A-Za-z ]*?)\s+"
                       r"(?P<month>[A-Za-z]{3,9})\s+(?P<day>\d{1,2})\s+(?P<year>\d{4})$")

#: The snapshot date, from the CSV's own name inside the zip:
#: "Ballot Status Report 2024-10-22.csv".
_INNER_DATE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")

#: Return methods `normalize.method()` does not carry. Consulted ONLY after it
#: returns None, and anything in neither raises SchemaDrift. Every label here
#: appears verbatim in the 2024 and 2025 files. Washington is an all-mail state:
#: a ballot posted back, dropped in a drop box, emailed or faxed is the same
#: mailed ballot coming home, and only "In Person" is a vote cast at a counter.
EXTRA_METHOD = {
    "drop box": METHOD_MAIL,
    "dropbox": METHOD_MAIL,
    "non-standard mail": METHOD_MAIL,
    "non-standard dropbox": METHOD_MAIL,
    "email": METHOD_MAIL,
    "fax": METHOD_MAIL,
}

#: `normalize.sex()` maps "other" to "unknown" but does not carry Washington's
#: single-letter "O" (144 rows in the 2024 file). Mapping it the same way the
#: shared table already maps the spelled-out word is consistent rather than
#: inventive; reported upstream for normalize.py alongside the same note in
#: `md.py` about party codes.
EXTRA_SEX = {"o": "unknown"}

#: A blank or unparseable `Received Date` is one junk row in half a million
#: (there is exactly one in the 2024-10-22 file). Above this share it is not
#: junk, it is the date format having changed, and that is drift.
UNDATED_BUDGET = 0.01

EXPECTED_COUNTIES = len(_fips.CENSUS_COUNTIES["WA"])


def _clean(value: str | None) -> str:
    return " ".join((value or "").split())


def parse_election(raw: str) -> tuple[str, date] | None:
    """"General Nov  5 2024" -> ("general", date(2024, 11, 5)), else None."""
    m = _ELECTION.match(_clean(raw))
    if m is None:
        return None
    for fmt in ("%b", "%B"):
        try:
            month = datetime.strptime(m.group("month")[:3] if fmt == "%b"
                                      else m.group("month"), fmt).month
            break
        except ValueError:
            continue
    else:
        return None
    try:
        return m.group("kind").strip().lower(), date(
            int(m.group("year")), month, int(m.group("day"))
        )
    except ValueError:
        return None


def snapshot_day(name: str) -> date:
    """The as-of date, from the CSV's own filename inside the zip."""
    m = _INNER_DATE.search(name or "")
    if m is None:
        raise SchemaDrift(f"WA: {name!r} carries no snapshot date")
    try:
        return date(*(int(g) for g in m.groups()))
    except ValueError as exc:
        raise SchemaDrift(f"WA: {name!r} carries no valid snapshot date") from exc


def received_day(raw: str) -> date | None:
    """"10/22/2024 12:00:00 AM" -> date(2024, 10, 22); None if unusable."""
    text = _clean(raw).split(" ")[0]
    if not text:
        return None
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def method_bucket(raw: str) -> str | None:
    """A canonical method for one Washington return method, or None if blank.

    None only for an EMPTY cell -- three rows in the 2024 file. An unrecognised
    label raises, per rule 3: a new return method is a bucket decision that must
    be made deliberately, not by falling into `mail`.
    """
    key = _clean(raw).lower()
    if not key:
        return None
    hit = _method(key) or EXTRA_METHOD.get(key)
    if hit is None:
        raise SchemaDrift(f"WA: unrecognised return method {raw!r}")
    return hit


def sex_bucket(raw: str) -> str:
    key = _clean(raw).lower()
    hit = _sex(key) or EXTRA_SEX.get(key)
    if hit is None:
        raise SchemaDrift(f"WA: unrecognised gender code {raw!r}")
    return hit


class _Tally:
    """One geography's ballots on one day, split by method."""

    __slots__ = ("total", "mail", "inperson")

    def __init__(self) -> None:
        self.total = 0
        self.mail = 0
        self.inperson = 0

    def add(self, other: "_Tally") -> None:
        self.total += other.total
        self.mail += other.mail
        self.inperson += other.inperson

    def count(self, where: str | None) -> None:
        self.total += 1
        if where == METHOD_MAIL:
            self.mail += 1
        elif where == METHOD_INPERSON:
            self.inperson += 1


def open_report(body: bytes) -> tuple[str, io.TextIOWrapper, zipfile.ZipFile]:
    """The single CSV inside the daily zip, as (name, text stream, zip)."""
    if looks_like_html(body):
        raise Missing("WA: the daily report came back as a web page, not a zip")
    try:
        archive = zipfile.ZipFile(io.BytesIO(body))
    except zipfile.BadZipFile as exc:
        raise SourceError(f"WA: the daily report is not a readable zip: {exc}") from exc
    members = [n for n in archive.namelist() if n.lower().endswith(".csv")]
    if len(members) != 1:
        raise SchemaDrift(
            f"WA: expected exactly one CSV in the daily zip, found {archive.namelist()}"
        )
    name = members[0]
    stream = io.TextIOWrapper(archive.open(name), encoding="utf-8-sig",
                              errors="replace", newline="")
    return name, stream, archive


def parse(body: bytes, cycle: int, as_of: date) -> FetchResult:
    """Parse one daily Ballot Status Report into the whole curve to date."""
    return parse_parts([body], cycle, as_of)


def parse_parts(bodies, cycle: int, as_of: date) -> FetchResult:
    """The same, over one day's report split across several zips.

    ONE report, several files. The daily 2024 scheme is a single statewide zip,
    but the 2022 general was published as four -- King on its own, Snohomish
    with Spokane, Clark with Pierce, and "all other counties" -- because the
    per-ballot file for the big counties is tens of megabytes each. They are one
    snapshot of one election in every way that matters here, so they accumulate
    into ONE set of tallies and are emitted once; parsing them separately and
    concatenating would produce four partial statewide curves, each of which
    looks exactly like a Washington total and is not one.

    Two things are checked BECAUSE they are split, and neither can go wrong in
    the single-file case:

    * every part must carry the same snapshot date, because a mixed set is not
      one day's position; and
    * no county may appear in two parts, because these tallies ADD and a part
      listed twice would silently double a county's turnout rather than fail.
    """
    voting = election_date(cycle)
    by_state: dict[date, _Tally] = defaultdict(_Tally)
    by_county: dict[tuple[str, date], _Tally] = defaultdict(_Tally)
    by_sex: dict[tuple[date, str], int] = defaultdict(int)
    names: dict[str, str] = {}
    unknown_counties: set[str] = set()
    elections: set[str] = set()
    rows = matched = undated = 0
    party_seen: set[str] = set()
    snapshots: set[date] = set()
    claimed: dict[str, str] = {}

    for body in bodies:
        name, stream, archive = open_report(body)
        with archive:
            snapshot = snapshot_day(name)
            snapshots.add(snapshot)
            mine: set[str] = set()

            reader = csv.DictReader(stream)
            missing = [c for c in REQUIRED if c not in (reader.fieldnames or [])]
            if missing:
                raise SchemaDrift(
                    f"WA: ballot status report is missing columns {missing}")

            for row in reader:
                rows += 1
                label = _clean(row.get("Election"))
                elections.add(label)
                parsed = parse_election(label)
                if parsed is None or parsed[0] != "general" or parsed[1] != voting:
                    continue
                matched += 1

                county = _clean(row.get("County"))
                hit = _fips.lookup("WA", county)
                if hit is None:
                    unknown_counties.add(county)
                    continue
                fips, canonical = hit
                # Coverage is a property of the FILE, not of the truncated span: a
                # county that is in the snapshot has all of its returns in it, dated,
                # so an early day with none of its ballots yet is a real zero rather
                # than a gap. Recording the county here -- before `as_of` trims the
                # series -- is what keeps the statewide gate from suppressing the
                # first week of every cycle.
                names[fips] = canonical
                mine.add(fips)

                day = received_day(row.get("Received Date"))
                if day is None or day > snapshot:
                    undated += 1
                    continue
                if day > as_of:
                    continue

                where = method_bucket(row.get("Return Method"))
                by_state[day].count(where)
                by_county[(fips, day)].count(where)
                by_sex[(day, sex_bucket(row.get("Gender")))] += 1
                if _clean(row.get("Party")):
                    party_seen.add(_clean(row.get("Party")))

        overlap = sorted(mine & set(claimed))
        if overlap:
            raise SchemaDrift(
                f"WA: {name!r} repeats counties already read from "
                f"{claimed[overlap[0]]!r} ({overlap[:3]}) -- these tallies add, "
                f"so one part read twice would double a county's turnout"
            )
        claimed.update({fips: name for fips in mine})

    if len(snapshots) > 1:
        raise SchemaDrift(
            f"WA: the parts of this report are dated "
            f"{sorted(d.isoformat() for d in snapshots)} -- a mixed set is not "
            f"one day's position"
        )
    snapshot = snapshots.pop() if snapshots else as_of

    if unknown_counties:
        raise SchemaDrift(f"WA: unrecognised county names {sorted(unknown_counties)[:5]}")
    if not matched:
        raise NotYetPublished(
            f"WA: the {snapshot.isoformat()} report holds no ballots for the "
            f"{cycle} general (its elections are {sorted(elections)[:3]})"
        )
    if matched and undated / matched > UNDATED_BUDGET:
        raise SchemaDrift(
            f"WA: {undated:,} of {matched:,} ballots have no usable Received "
            f"Date -- the date format has changed"
        )
    if party_seen:
        # Washington has no party registration and the column is empty in every
        # file we have. If it ever fills in, that is a new dimension to publish
        # deliberately, not one to start guessing at mid-season.
        raise SchemaDrift(
            f"WA: the Party column is no longer empty ({sorted(party_seen)[:5]}) "
            f"-- Washington has no party registration, so this needs a look"
        )
    if not by_state:
        raise NotYetPublished(
            f"WA: no ballots returned for the {cycle} general on or before "
            f"{as_of.isoformat()}"
        )
    return _emit(by_state, by_county, by_sex, names, cycle, as_of, snapshot, rows)


def _emit(by_state, by_county, by_sex, names, cycle, as_of, snapshot, rows) -> FetchResult:
    span = sorted(d for d in by_state if d <= as_of)
    covered = len(names)
    result = FetchResult()

    running = _Tally()
    for day in span:
        running.add(by_state[day])
        if covered == EXPECTED_COUNTIES:
            result.state_rows.append(StateDay(
                cycle=cycle, state="WA", day=day,
                ballots_total=running.total,
                ballots_new=by_state[day].total,
                # The report lists ballots RETURNED. Washington mails a ballot
                # to every registered voter, so "requested" has no meaning here
                # and this file says nothing about how many went out.
                mail_requested=None,
                mail_returned=running.mail,
                inperson=running.inperson,
                # No party registration in Washington. Never 0.
                party_dem=None, party_rep=None, party_oth=None, party_npa=None,
            ))

    if covered != EXPECTED_COUNTIES:
        # PARTIAL COVERAGE, exactly as tx.py describes it: a statewide sum over
        # 38 of 39 counties looks like a Washington total and is not one, and
        # nothing in StateDay can say "this is missing Grant County".
        log.warning(
            "WA: the %s snapshot carries %d of %d counties; publishing counties "
            "only, no statewide row",
            snapshot.isoformat(), covered, EXPECTED_COUNTIES,
        )

    for fips in sorted(names):
        running = _Tally()
        for day in span:
            today = by_county.get((fips, day))
            if today:
                running.add(today)
            if running.total == 0:
                continue
            result.county_rows.append(CountyDay(
                cycle=cycle, state="WA", county_fips=fips, day=day,
                county_name=names[fips],
                ballots_total=running.total,
                ballots_new=today.total if today else 0,
                mail_returned=running.mail,
                inperson=running.inperson,
                party_dem=None, party_rep=None, party_oth=None, party_npa=None,
            ))

    cumulative: dict[str, int] = defaultdict(int)
    for day in span:
        for (when, bucket), count in by_sex.items():
            if when == day:
                cumulative[bucket] += count
        for bucket, total in sorted(cumulative.items()):
            result.demo_rows.append(DemoDay(
                cycle=cycle, state="WA", day=day,
                dimension="sex", bucket=bucket, ballots_total=total,
            ))

    log.debug("WA: %s snapshot, %d rows -> %d state / %d county / %d demo rows",
              snapshot.isoformat(), rows, len(result.state_rows),
              len(result.county_rows), len(result.demo_rows))
    return result


class WAScraper(Adapter):
    """Tier 1 for Washington: the SoS's daily statewide Ballot Status Report."""

    state = "WA"
    name = "wa-sos"
    tier = TIER_SCRAPER

    def _snapshot(self, day: date, *, use_cache: bool) -> bytes:
        stamp = day.isoformat()
        return get(ZIP_URL.format(day=stamp), state="WA",
                   filename=f"Statewide{stamp}.zip", use_cache=use_cache,
                   min_bytes=4096, timeout=300)

    def _latest(self, as_of: date, *, use_cache: bool) -> tuple[date, bytes]:
        problems: list[str] = []
        for back in range(LOOKBACK_DAYS + 1):
            day = as_of - timedelta(days=back)
            try:
                return day, self._snapshot(day, use_cache=use_cache)
            except Missing as exc:
                problems.append(str(exc))
        raise NotYetPublished(
            f"WA: no daily ballot status report posted in the {LOOKBACK_DAYS} "
            f"days to {as_of.isoformat()} ({problems[0] if problems else ''})"
        )

    def fetch(self, cycle: int, as_of: date) -> FetchResult:
        _day, body = self._latest(as_of, use_cache=False)
        return parse(body, cycle, as_of)

    def _archived(self, parts: tuple[tuple[str, str], ...]) -> list[bytes]:
        """One past cycle's pinned snapshot, however many files it is.

        `use_cache=True` is correct here and nowhere else in this module: a fixed
        Wayback stamp, and a dated report of a settled election on the SoS's own
        host, are both files that cannot change. For 2024 the cache filename is
        the LIVE one, so a snapshot downloaded while that cycle was running is
        reused and the Archive is never asked.

        `min_interval` spaces the parts even though they are only four requests:
        they are 18-39 MB each off a state host, and the ban this repo actually
        took was for going fast at one.
        """
        bodies: list[bytes] = []
        for url, filename in parts:
            try:
                bodies.append(get(url, state="WA", filename=filename,
                                  use_cache=True, min_bytes=4096, timeout=900,
                                  min_interval=ARCHIVE_MIN_INTERVAL))
            except Missing as exc:
                raise NotYetPublished(
                    f"WA: the pinned snapshot part {filename} is gone ({exc})"
                ) from exc
        return bodies

    def fetch_history(self, cycle: int) -> FetchResult:
        """One archived snapshot rebuilds a past cycle's entire curve.

        Every ballot in the file is dated, so the LAST snapshot of a cycle
        contains every earlier day of it and there is nothing to walk: this is
        one download, not an archive sweep. `parse` then trims the series at
        Election Day, exactly as `fetch` trims it at the run date -- both paths
        go through the same election-identity and coverage guards, and neither
        can publish a ballot dated after the day it was asked for.

        `current_election/` is purged when the next election opens, so the live
        host serves nothing for either past cycle at THAT path. 2024 survives in
        the Wayback Machine; 2022 was never there because it was never at that
        path -- it is still live under `_assets/elections/research/`, in four
        pieces. Both are pinned in `ARCHIVED` and both come back through the
        same `parse_parts`, so neither cycle has a code path of its own.
        """
        if cycle >= date.today().year:
            raise NotYetPublished(f"WA: {cycle} is not an archived cycle")
        pin = ARCHIVED.get(int(cycle))
        if pin is None:
            raise NotYetPublished(
                f"WA: there is no pinned {cycle} ballot status report to "
                f"backfill from. The per-ballot report exists for 2022 and 2024; "
                f"anything earlier is a one-off post-election zip for a special, "
                f"not a daily general-election series."
            )
        _day, parts = pin
        return parse_parts(self._archived(parts), cycle, election_date(cycle))
