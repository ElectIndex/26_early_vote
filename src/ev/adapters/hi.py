"""Hawaii: the Office of Elections' "Absentee Reconciliation" report (PDF).

`docs/coverage-research.md` filed Hawaii under "daily, dated, and PDF" and
rejected it for exactly one reason -- "this pipeline has no PDF dependency".
**That reason is gone**: `pypdf` is a declared dependency and `ia.py` already
parses Iowa's absentee report out of one.

The survey found the four **per-county** reports
(`AbsenteeReconDP-01-20260717.pdf` and friends). There is a fifth file it did
not find, and it is much better than all four together:

    https://elections.hawaii.gov/wp-content/uploads/AbsenteeReconState-{YYYYMMDD}.pdf

One page, one row per county, a totals row, and -- the part that matters -- a
footer carrying **both** the run timestamp and the election's own name:

```
County   ELECT Sent (a) | ELECT Voted (b) | ELECT Invalid (c) | EV Voted (d) |
         MAIL Sent (e)  | MAIL Voted (f)  | MAIL Invalid (g)  |
         VOTED (b + d + f) | TOTAL (a + d + e)
Hawai'i     287   51   0    804   112461   35712    325    36567   113552
Maui        329   45   0    459    97819   25056    410    25560    98607
Kaua'i       95    9   0    471    41720   14906    184    15386    42286
Honolulu   1844  221   0   2303   480780  154602   1010   157126   484927
           2555  326   0   4037   732780  230276   1929   234639   739372
Absentee Reconcillation      8/8/2026 3:11:26 AM      2026 Primary Election
```

That last line is why Hawaii is buildable and Montana's Tableau CSV was not: the
filename pattern is shared with the August primary, and the file says which
election it is. A primary's report is refused by name, not by a date window we
guessed.

VERIFIED live 2026-09-06, every status observed:

* `.../AbsenteeReconState-20260717.pdf` -> **200**, 72,858 B, real PDF
* `.../AbsenteeReconState-20260808.pdf` -> **200**, 72,960 B
* `.../AbsenteeReconDP-01-20260717.pdf` -> **200** (Honolulu), `-02-` Hawaii,
  `-03-` Kauai, `-04-` Maui
* `.../AbsenteeReconDP-01-20260905.pdf` -> **404** -- no general-election report
  exists yet, which is `NotYetPublished`, not an error
* `https://elections.hawaii.gov/resources/absentee-voting-report/` -> **200**,
  the index page that lists every dated report and is scraped first

Judgement calls:

* **Hawaii does not register voters by party, so every `party_*` field is None.**
  Never 0. See THE BLANK RULE in schema.py.

* **`ballots_total` is Hawaii's own `VOTED (b + d + f)` column**, which is
  bigger than `mail_returned + inperson` because it also counts ballots returned
  by email or fax under UOCAVA. Those are neither a mail ballot nor an in-person
  early vote in `normalize`'s vocabulary, so they are carried in the total and in
  neither part, and the arithmetic is checked on every row rather than assumed.

* **`mail_requested` is `MAIL Sent`.** Hawaii mails a ballot to every registered
  voter, so nobody "requests" one; the column is the number issued, which is what
  every other adapter here puts in that field.

* **Zero is real, and early in the season everything is zero.** On 2026-07-17,
  before ballots went out, `MAIL Voted` is a literal 0 for all four counties.
  That publishes as 0 -- Hawaii reported it -- while a column Hawaii does not
  publish at all stays None.

* **Four counties, not five.** Kalawao County (FIPS 15005, population under a
  hundred) has no elections division of its own and never appears in the report;
  Hawaii's four county clerks are the whole state. So four rows IS full coverage,
  and the statewide row is published from the file's own totals line only when
  all four are present -- the coverage gate `tx.py` uses.

* **The report's own timestamp must match its filename.** A file dated
  `20261020` whose footer says `10/19/2026` would mean the Office re-posted
  yesterday's numbers under today's name, which would flatten a day of the curve.
  That raises SchemaDrift.

There is **no archive**, and `fetch_history` below says so by name with the
sweep that establishes it. The short version: the Office keeps ONE election's
reports and the Wayback Machine never crawled this section at all.
"""

from __future__ import annotations

import io
import logging
import re
from datetime import date, timedelta

import pypdf

from ..schema import TIER_SCRAPER, CountyDay, StateDay
from . import _fips
from ._net import Missing, get
from .base import Adapter, FetchResult, NotYetPublished, SchemaDrift, SourceError

log = logging.getLogger(__name__)

#: The Office of Elections' index of every dated report. VERIFIED 200.
INDEX_URL = "https://elections.hawaii.gov/resources/absentee-voting-report/"

#: The statewide reconciliation, one file per publishing day. VERIFIED 200 for
#: 2026-07-17 and 2026-08-08; 404 for a day with no report.
STATE_REPORT = (
    "https://elections.hawaii.gov/wp-content/uploads/AbsenteeReconState-{stamp}.pdf"
)

_INDEX_LINK = re.compile(r"AbsenteeReconState-(\d{8})\.pdf", re.I)

#: How far back to look when today has no file. Hawaii publishes on business
#: days -- 2026-06-27, -06-28 and -08-02 are all absent from the index -- so a
#: run on a Sunday must still find Friday's snapshot rather than report nothing.
LOOKBACK_DAYS = 10

#: Reports to try in one run. The newest is almost always the answer; the rest
#: only cover a report withdrawn between the index being written and our GET.
#: Keeping this small is politeness, and the reason the probe stopped tripping
#: this host's rate limiter.
MAX_CANDIDATES = 3

#: The nine columns, in order, identified by the letter tags Hawaii prints in
#: their headings. Matching on the tags rather than the words survives the
#: line-wrapping pypdf introduces ("ELECT \nSent (a)") and still fails loudly if
#: a column is added, removed or reordered.
COLUMN_TAGS: tuple[str, ...] = (
    "ELECT Sent (a)",
    "ELECT Voted (b)",
    "ELECT Invalid (c)",
    "EV Voted (d)",
    "MAIL Sent (e)",
    "MAIL Voted (f)",
    "MAIL Invalid (g)",
    "VOTED (b + d + f)",
    "TOTAL (a + d + e)",
)
COLUMNS = len(COLUMN_TAGS)

#: Hawaii's four elections jurisdictions, as the report spells them. Kalawao
#: County (15005) has no elections division and never appears; these four are
#: full coverage.
COUNTY_NAMES: tuple[str, ...] = ("Hawaii", "Maui", "Kauai", "Honolulu")

#: The okina and the typographic apostrophes Hawaii uses in "Hawai'i" and
#: "Kaua'i". `_fips._norm` strips only the ASCII apostrophe and backtick.
_OKINA = re.compile("[ʻ‘’ʼ`']")

_ELECTION = re.compile(r"(\d{4})\s+(General|Primary|Special)\s+Election", re.I)
_STAMP = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})\s+\d{1,2}:\d{2}:\d{2}\s*[AP]M$")
_NUMBER = re.compile(r"^-?[\d,]+$")


def _int(raw: str) -> int:
    try:
        return int(raw.replace(",", ""))
    except ValueError as exc:
        raise SchemaDrift(f"HI: {raw!r} is not a count") from exc


def _county(name: str) -> tuple[str, str]:
    """(5-digit FIPS, census name) for a name as the report spells it."""
    plain = _OKINA.sub("", name)
    plain = re.sub(r"^(?:City and )?County of\s+", "", plain, flags=re.I).strip()
    hit = _fips.lookup("HI", plain)
    if hit is None:
        raise SchemaDrift(f"HI: unrecognised county name {name!r}")
    return hit


def _text(body: bytes) -> str:
    try:
        reader = pypdf.PdfReader(io.BytesIO(body))
        return "\n".join(page.extract_text() for page in reader.pages)
    except Exception as exc:  # noqa: BLE001 - pypdf raises a zoo of types
        raise SourceError(f"HI: report is not a readable PDF: {exc}") from exc


def election(text: str) -> tuple[int, str]:
    """(year, kind) off the report's own footer line."""
    match = _ELECTION.search(text)
    if match is None:
        raise SchemaDrift(
            "HI: report carries no '<year> <Primary|General> Election' line, so "
            "there is no way to tell which election it covers"
        )
    return int(match.group(1)), match.group(2).lower()


def stamp(text: str) -> date:
    """The report's own run date, off its footer timestamp."""
    for line in text.splitlines():
        match = _STAMP.match(line.strip())
        if match:
            month, day, year = (int(g) for g in match.groups())
            return date(year, month, day)
    raise SchemaDrift("HI: report carries no 'M/D/YYYY H:MM:SS AM' timestamp")


def _check_header(text: str) -> None:
    """The nine tags must be present AND IN ORDER.

    ⚠️ PRESENCE ALONE PROVES NOTHING, BECAUSE THE VALUES ARE CONSUMED
    POSITIONALLY AND THE ARITHMETIC CHECK IS COMMUTATIVE.

    `rows()` reads nine numeric lines after a county name and hands them to
    `build` as a, b, c, d, e, f, g, VOTED, TOTAL by position. The only two
    things that were said to prove those nine landed in the nine fields we think
    they did are this function and `_check_arithmetic` -- and neither could see
    a reordering:

    * this function tested `tag not in flat`, which is order-blind;
    * `_check_arithmetic` verifies `VOTED == b + d + f` and `TOTAL == a + d + e`,
      and addition does not care which addend is which.

    So if Hawaii ever printed the MAIL block (e, f, g) where the ELECT block
    (a, b, c) is -- three adjacent columns, one layout change -- both checks
    still passed and Hawaii published `mail_requested` of **2,555** instead of
    **732,780** and `mail_returned` of **326** instead of **230,276**, with
    `ballots_total` correct and unchanged. Run against the real 2026-08-08
    report, that is exactly what happened; it is a test below.

    Requiring the tags to appear in order is what actually pins the positions,
    and it costs one pass over the flattened text.
    """
    flat = " ".join(text.split())
    missing = [tag for tag in COLUMN_TAGS if tag not in flat]
    if missing:
        raise SchemaDrift(f"HI: report is missing columns {missing}")
    seen = [(flat.index(tag), tag) for tag in COLUMN_TAGS]
    if seen != sorted(seen):
        raise SchemaDrift(
            "HI: the report's columns are present but not in the order this "
            "parser reads them by position: it prints "
            f"{[tag for _, tag in sorted(seen)]}"
        )


def rows(text: str) -> tuple[dict[str, list[int]], list[int] | None]:
    """({county fips: nine counts}, statewide totals row or None).

    Every value in the report extracts onto its own line, so a county row is a
    name followed by exactly nine numeric lines, and the totals row is the nine
    numeric lines that follow the last county with no name in front of them.
    """
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    counties: dict[str, list[int]] = {}
    totals: list[int] | None = None
    index = 0
    while index < len(lines):
        line = lines[index]
        if _NUMBER.match(line):
            index += 1
            continue
        try:
            fips, _name = _county(line)
        except SchemaDrift:
            index += 1
            continue
        values = lines[index + 1:index + 1 + COLUMNS]
        if len(values) != COLUMNS or not all(_NUMBER.match(v) for v in values):
            raise SchemaDrift(
                f"HI: county {line!r} is not followed by {COLUMNS} counts, got {values}"
            )
        counties[fips] = [_int(v) for v in values]
        index += 1 + COLUMNS
        tail = lines[index:index + COLUMNS]
        if len(tail) == COLUMNS and all(_NUMBER.match(v) for v in tail):
            totals = [_int(v) for v in tail]
    if not counties:
        raise SchemaDrift("HI: report produced no county rows")
    return counties, totals


def _check_arithmetic(label: str, values: list[int]) -> None:
    a, b, c, d, e, f, g, voted, total = values
    if voted != b + d + f:
        raise SchemaDrift(
            f"HI: {label} VOTED is {voted} but b+d+f is {b + d + f}; the columns "
            f"are not in the order this parser expects"
        )
    if total != a + d + e:
        raise SchemaDrift(
            f"HI: {label} TOTAL is {total} but a+d+e is {a + d + e}; the columns "
            f"are not in the order this parser expects"
        )


def parse(body: bytes, cycle: int, *, filename_date: date | None = None) -> FetchResult:
    """Parse one statewide Absentee Reconciliation report.

    Refuses any report whose own footer names an election other than this
    cycle's general -- the primary's files share the filename pattern and sit on
    the same server all year.
    """
    text = _text(body)
    year, kind = election(text)
    if year != int(cycle) or kind != "general":
        raise NotYetPublished(
            f"HI: this report is the {year} {kind} election, not the {cycle} general"
        )
    return build(text, cycle, filename_date=filename_date)


def build(text: str, cycle: int, *, filename_date: date | None = None) -> FetchResult:
    """Canonical rows from an already-identified report's extracted text."""
    _check_header(text)
    day = stamp(text)
    if filename_date is not None and day != filename_date:
        raise SchemaDrift(
            f"HI: {filename_date.isoformat()} report is stamped {day.isoformat()}; "
            f"a re-posted snapshot would flatten a day of the curve"
        )
    counties, totals = rows(text)

    result = FetchResult()
    for fips, values in counties.items():
        _check_arithmetic(fips, values)
        _a, b, _c, d, e, f, _g, voted, _total = values
        result.county_rows.append(CountyDay(
            cycle=cycle, state="HI", county_fips=fips, day=day,
            ballots_total=voted,
            ballots_new=None,
            mail_returned=f,
            inperson=d,
            # Hawaii has no party registration. Never 0.
            party_dem=None, party_rep=None, party_oth=None, party_npa=None,
        ))

    # ⚠️ A COUNTY THAT DID NOT PARSE IS DRIFT, NOT PARTIAL COVERAGE.
    #
    # `rows()` has to skip any line it cannot resolve to a county -- that is how
    # it walks past "Absentee Reconcillation", the column headings and the
    # footer -- so a county Hawaii RENAMES is skipped by exactly the same
    # `except SchemaDrift: continue`, and the run then succeeds with three
    # counties. That is not the Texas case this branch was modelled on: Texas
    # has 254 counties that report progressively, while Hawaii's report is one
    # page generated by one office and its FOUR clerks are the entire state.
    # Three of four is therefore never "Maui has not reported yet"; it is "we
    # stopped recognising Maui", and publishing the other three would blank Maui
    # on the map, which reads as nobody in Maui having voted. Rule 3: raise
    # rather than guess. SchemaDrift falls through, so Hawaii still gets a
    # statewide number from a weaker tier.
    if len(counties) != len(COUNTY_NAMES):
        got = sorted(counties)
        raise SchemaDrift(
            f"HI: the report produced {len(counties)} of {len(COUNTY_NAMES)} "
            f"county rows ({got}); Hawaii's four clerks are the whole state, so "
            f"a missing one means a name this parser no longer recognises, not a "
            f"county yet to report"
        )

    state_rows: list[StateDay] = []
    if totals is not None:
        _check_arithmetic("statewide", totals)
        summed = [sum(v[i] for v in counties.values()) for i in range(COLUMNS)]
        if summed != totals:
            raise SchemaDrift(
                f"HI: the totals row {totals} is not the sum of the county rows "
                f"{summed}"
            )
        _a, b, _c, d, e, f, _g, voted, _total = totals
        state_rows.append(StateDay(
            cycle=cycle, state="HI", day=day,
            ballots_total=voted, ballots_new=None,
            mail_requested=e, mail_returned=f, inperson=d,
            party_dem=None, party_rep=None, party_oth=None, party_npa=None,
        ))
    else:
        # All four counties are there but the report printed no totals line. The
        # counties are still real and are published; the statewide row is not,
        # because the totals line is the only thing that proves nothing was
        # dropped between the last county and the end of the page.
        log.warning(
            "HI: report carries no statewide totals line; publishing the "
            "%d county rows only", len(counties),
        )
    result.state_rows.extend(state_rows)
    for row in result.county_rows:
        row.county_name = _NAMES.get(row.county_fips, "")
    return result


#: 5-digit FIPS -> census county name, so county rows carry the site's spelling
#: rather than Hawaii's ("Hawai'i", "Kaua'i").
_NAMES: dict[str, str] = {
    _fips.lookup("HI", name)[0]: name for name in _fips.names("HI")
}


class HIScraper(Adapter):
    """Tier 1 for Hawaii: the Office of Elections' statewide absentee report."""

    state = "HI"
    name = "hi-oe"
    tier = TIER_SCRAPER

    def _report(self, day: date) -> bytes:
        stamped = day.strftime("%Y%m%d")
        body = get(
            STATE_REPORT.format(stamp=stamped),
            state="HI", filename=f"AbsenteeReconState-{stamped}.pdf",
            min_bytes=4096,
        )
        if not body.startswith(b"%PDF"):
            raise Missing(f"HI: the {stamped} report is not a PDF")
        return body

    def _index_dates(self) -> list[date]:
        """Every report date the Office's own index page lists."""
        try:
            body = get(INDEX_URL, state="HI", filename="index.html", min_bytes=2048)
        except Missing:
            return []
        out: list[date] = []
        for stamped in _INDEX_LINK.findall(body.decode("utf-8", errors="replace")):
            try:
                out.append(date(int(stamped[:4]), int(stamped[4:6]), int(stamped[6:8])))
            except ValueError:
                continue
        return sorted(set(out))

    def _candidates(self, as_of: date) -> list[date]:
        """Report dates to try, newest first and deliberately few.

        The index page is authoritative and one request, so it is used alone
        whenever it answers: walking a blind date range instead costs eleven
        requests a run, and `elections.hawaii.gov` answers that with HTTP 429.
        The constructed range stays as the fallback for a day the index is down.
        """
        listed = [d for d in self._index_dates() if d <= as_of]
        if listed:
            return sorted(listed, reverse=True)[:MAX_CANDIDATES]
        constructed = [as_of - timedelta(days=n) for n in range(LOOKBACK_DAYS + 1)]
        return constructed[:MAX_CANDIDATES]

    def fetch(self, cycle: int, as_of: date) -> FetchResult:
        seen = 0
        for day in self._candidates(as_of):
            try:
                body = self._report(day)
            except Missing:
                continue
            seen += 1
            try:
                return parse(body, cycle, filename_date=day)
            except NotYetPublished as exc:
                # A live PRIMARY report, refused by its own name. Older files are
                # older primaries, so there is nothing to gain by walking back.
                raise NotYetPublished(
                    f"HI: no {cycle} general absentee report yet ({exc})"
                ) from exc
        if seen:  # pragma: no cover - every readable report is either used or refused
            raise NotYetPublished(f"HI: no usable {cycle} general report")
        raise NotYetPublished(
            f"HI: the Office of Elections has posted no absentee reconciliation "
            f"report on or before {as_of.isoformat()}"
        )

    def fetch_history(self, cycle: int) -> FetchResult:
        """No archive, refused BY NAME. This is a measured negative, not a guess.

        ⚠️ AND THE WAYBACK MACHINE IS THE HALF OF IT THAT PROVES NOTHING.

        Three things were checked on 2026-09-09, and only the third is evidence:

        1. **A CDX sweep** of `web.archive.org` with `matchType=domain` over
           `elections.hawaii.gov`, 2022 to 2025, collapsed to 186,534 distinct
           urlkeys, greped OFFLINE. It holds ZERO captures of any `AbsenteeRecon`
           file. That reads like proof and is not: the same sweep holds zero
           captures of `INDEX_URL` itself, which is live right now and lists 240
           reports. The archive never crawled this section, so it is SILENT about
           Hawaii rather than negative, and a path check in an archive that never
           looked is exactly the false negative that hid Delaware's 2022 file.

        2. **The Office's own index**, fetched live: HTTP 200, 189,456 B, listing
           80 `AbsenteeReconState-` and 160 `AbsenteeReconDP-0N-` files. Every one
           of them is dated 2026-06-25..2026-08-15 -- this cycle's PRIMARY. Not
           one 2024 or 2022 report is listed. (`tests/fixtures/hi/` keeps an
           excerpt, and the test asserts the dates are all in-cycle, so the day
           Hawaii publishes an archive this stops being true loudly.)

        3. **The live server, probed directly**, which is the part that counts.
           34 URLs: `AbsenteeReconState-{stamp}.pdf` and
           `AbsenteeReconDP-01-{stamp}.pdf` for 2024-10-08, -10-15, -10-21,
           -10-25, -10-28, -10-30, -11-01, -11-04, -11-05, -11-06, -11-12, for
           the 2024 primary's 08-01/08-08/08-10, and for 2022-10-25, -11-01 and
           -11-08. **All 34 returned a real HTTP 404** (116,560 B error page) --
           not a 403, not a challenge, not a soft 404: this host answers 200 with
           a PDF for a file that exists and 404 for one that does not, and both
           were observed in the same minute from the same client.

        So the reports are deleted when the next election starts, exactly as
        Connecticut deletes its workbooks, and there is nothing to backfill. The
        refusal is by name so a future cycle cannot quietly inherit it.
        """
        raise NotYetPublished(
            f"HI: the Office of Elections keeps only the current election's "
            f"absentee reconciliation reports -- its index lists none before "
            f"2026-06-25, and every {cycle} filename in the published convention "
            f"404s on the live server -- so there is no {cycle} archive to "
            f"backfill from"
        )
