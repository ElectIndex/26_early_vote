"""Kentucky: the State Board of Elections' daily absentee workbook.

The KY SBE posts `Absentee_Public_MMDDYY.xlsx` to `elect.ky.gov/Documents/`
during the absentee period -- a DATED filename, so each day's snapshot survives
as its own file and a backfill is a walk over dates rather than an archive hunt.
It carries every dimension this project ranks except demographics: 120 counties,
a party split, and a four-way method split.

The workbook's shape is unusual and drives everything below.

* **The header is on ROW 10.** Rows 1-9 are the SBE's prose caveats -- "these
  numbers are UNOFFICIAL", what an FPCA is, why the DEM and REP columns do not
  add up to the total. We find the header by looking for the row whose first cell
  is "County" rather than hard-coding 10, and raise SchemaDrift if there is none.

* **Four side-by-side blocks, each with its own "County" key column.** Mail sits
  in columns A-K, excused in-person in M-P, no-excuse in-person in R-U, and
  military/overseas (FPCA) in W-Y, separated by blank spacer columns. The word
  "County" therefore appears FOUR times in the header, so the twenty measure
  headers -- which are unique -- are matched by name and the key column is taken
  from the first occurrence only.

* **The TOTALS row is Kentucky's own.** The last data row is the SBE's statewide
  total. We publish that rather than summing counties, so our headline can never
  disagree with theirs. (It does agree: on 2024-11-01 both give 355,909.)

* **`UNOFFICIAL Total Absentee` = mail returned + FPCA returned + excused
  in-person + no-excuse in-person.** FPCA returns are ADDED by Kentucky's own
  formula, which is what tells us `All Ballots RETURNED` is domestic-only -- so a
  parser that reads only that column understates returns, exactly as in Ohio.

**Party: DEM and REP are real counts; NPA and OTH are blank, and must stay
blank.** Kentucky registers voters by party and this file splits out Democrats
and Republicans, but nothing else. The residual (total minus DEM minus REP) mixes
unaffiliated voters with Libertarians, Greens and the rest, and `normalize.py`
is explicit that collapsing those two together loses the single most-watched
number in an early-vote story. So we publish neither. See THE BLANK RULE.

**And the party split is BY METHOD, which is the crosstab `CountyDay` cannot
hold.** The DEM/REP columns are repeated for each of the three domestic blocks --
mail returned, excused in-person, no-excuse in-person -- so Kentucky states the
cells rather than only the two margins. `schema.MethodDay` is where they go, with
the two in-person blocks summed into one `inperson` band because
`normalize.method()` maps both there and the site knows two channels.

⚠️ **The FPCA block has no party columns**, and it is inside `mail_returned`.
Kentucky publishes `FPCA RETURNED` and no DEM/REP for it, so the mail band's
`ballots_total` includes military and overseas ballots that its `party_dem` and
`party_rep` do not. That is not a defect to paper over: the same gap is already
in the county row, where `party_dem + party_rep` has never summed to
`ballots_total` because NPA and OTH are unreported. `party_coverage` is the
column that says so.

**Only the general election.** Kentucky posts the same filename pattern for its
May primary -- `Absentee_Public_051826.xlsx` is the 2026 primary, and it is
still on the server. A run must never mistake it for the general, so we only
accept dates inside `WINDOW_DAYS` of that cycle's Election Day.
"""

from __future__ import annotations

import io
import logging
from datetime import date, datetime, timedelta

import openpyxl

from ..calendar import election_date
from ..normalize import METHOD_INPERSON, METHOD_MAIL
from ..schema import TIER_SCRAPER, CountyDay, MethodDay, StateDay
from . import _fips, _methods
from ._net import Missing, get, looks_like_xlsx
from .base import Adapter, FetchResult, NotYetPublished, SchemaDrift, SourceError

log = logging.getLogger(__name__)

#: VERIFIED on this network: Absentee_Public_100824 / 102924 / 103024 / 103124 /
#: 110124 / 110224 / 110524 (2024 general) and 051126 / 051326 / 051526 / 051826
#: (2026 primary) all return a real xlsx. Dates with no file 404 cleanly, which
#: is the NotYetPublished path. There was no such file in 2022 -- the practice
#: starts with the 2024 cycle -- so `fetch_history(2022)` correctly finds none.
BASE = "https://elect.ky.gov/Documents"
FILENAME = "Absentee_Public_{stamp}.xlsx"

#: How far back a run looks for the newest posted file. Kentucky skips days --
#: 2024-10-15 and 2024-10-22 have no file while 2024-10-08 does -- so asking only
#: for today's would report "nothing yet" on a day when a perfectly good snapshot
#: from earlier in the week is sitting there.
LOOKBACK_DAYS = 7

#: Dates this many days before Election Day, or fewer, count as the general
#: election's absentee period. Kentucky's primary is in May, so a 90-day window
#: cannot reach it; anything earlier is refused rather than published as if it
#: were the general. See the module docstring.
WINDOW_DAYS = 90

#: How far back `fetch_history` walks looking for archived daily snapshots.
HISTORY_DAYS = 45

COUNTY = "county"

MAIL_APPS = "all mail-in applications"
MAIL_SENT = "all ballots sent"
MAIL_BACK = "all ballots returned"
MAIL_DEM = "dem ballots returned"
MAIL_REP = "rep ballots returned"
EXCUSED = "excused in-person"
EXCUSED_DEM = "dem excused in-person"
EXCUSED_REP = "rep excused in-person"
NOEXCUSE = "no excuse in-person"
NOEXCUSE_DEM = "dem no excuse in-person"
NOEXCUSE_REP = "rep no excuse in-person"
FPCA_APPS = "fpca applications"
FPCA_BACK = "fpca returned"
TOTAL = "unofficial total absentee"

REQUIRED = (
    MAIL_APPS, MAIL_SENT, MAIL_BACK, MAIL_DEM, MAIL_REP,
    EXCUSED, EXCUSED_DEM, EXCUSED_REP,
    NOEXCUSE, NOEXCUSE_DEM, NOEXCUSE_REP,
    FPCA_APPS, FPCA_BACK, TOTAL,
)

TOTALS_LABELS = {"totals", "total", "statewide", "state total"}

#: The header row is row 10 today, but we search for it rather than assume it.
HEADER_SEARCH_ROWS = 25


def _norm_header(raw) -> str:
    return " ".join(str(raw or "").strip().lower().split())


def stamp(day: date) -> str:
    """Kentucky's filename date: two-digit month, day and year."""
    return day.strftime("%m%d%y")


def _int(value) -> int | None:
    """A count, or None when the cell is blank ("Kentucky did not report this")."""
    if value is None:
        return None
    if isinstance(value, bool):
        raise SchemaDrift(f"KY: {value!r} is not a count")
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip().replace(",", "")
    if not text or text in {"-", "--", "N/A", "n/a"}:
        return None
    try:
        return int(float(text))
    except ValueError as exc:
        raise SchemaDrift(f"KY: {value!r} is not a count") from exc


def _add(*values: int | None) -> int | None:
    """Sum the reported parts of a measure, or None if no part was reported.

    None + 5 is 5: an unreported FPCA column must not wipe out a reported
    domestic one. Only an entirely unreported measure stays blank.
    """
    known = [v for v in values if v is not None]
    return sum(known) if known else None


def _find_header(rows: list[tuple]) -> tuple[int, dict[str, int]]:
    """Locate the header row and map each unique measure name to its column.

    "County" appears four times -- once per side-by-side block -- so it is bound
    to its FIRST column and every other name must be unique. A duplicate measure
    name means the blocks have been rearranged and we cannot tell which one we
    are reading.
    """
    for i, row in enumerate(rows[:HEADER_SEARCH_ROWS]):
        if not row or _norm_header(row[0]) != COUNTY:
            continue
        index: dict[str, int] = {COUNTY: 0}
        for column, cell in enumerate(row):
            name = _norm_header(cell)
            if not name or name == COUNTY:
                continue
            if name in index:
                raise SchemaDrift(f"KY: duplicate header {name!r} in the absentee report")
            index[name] = column
        missing = [c for c in REQUIRED if c not in index]
        if missing:
            raise SchemaDrift(f"KY: absentee report is missing columns {missing}")
        return i, index
    raise SchemaDrift("KY: absentee report has no 'County' header row")


def parse(body: bytes, cycle: int, day: date) -> FetchResult:
    """Parse one dated Kentucky absentee workbook into canonical rows."""
    if not looks_like_xlsx(body):
        raise SourceError("KY: absentee report was not an xlsx")
    try:
        book = openpyxl.load_workbook(io.BytesIO(body), read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001 -- openpyxl raises a zoo of types
        raise SourceError(f"KY: could not open absentee workbook: {exc}") from exc

    rows = list(book.worksheets[0].iter_rows(values_only=True))
    header_at, index = _find_header(rows)

    def cell(row, column):
        i = index[column]
        return _int(row[i]) if i < len(row) else None

    state_rows: list[StateDay] = []
    county_rows: list[CountyDay] = []
    method_rows: list[MethodDay] = []
    unknown: list[str] = []

    for row in rows[header_at + 1:]:
        if not row:
            continue
        name = " ".join(str(row[0] or "").split())
        if not name:
            continue

        mail_sent = _add(cell(row, MAIL_SENT), cell(row, FPCA_APPS))
        mail_back = _add(cell(row, MAIL_BACK), cell(row, FPCA_BACK))
        in_person = _add(cell(row, EXCUSED), cell(row, NOEXCUSE))
        # Kentucky's own column, so our total is theirs. It is a cached formula
        # value; if a workbook ever ships without one, fall back to the parts it
        # is defined as rather than publishing a blank headline.
        total = cell(row, TOTAL)
        if total is None:
            total = _add(mail_back, in_person)

        mail_dem, mail_rep = cell(row, MAIL_DEM), cell(row, MAIL_REP)
        in_person_dem = _add(cell(row, EXCUSED_DEM), cell(row, NOEXCUSE_DEM))
        in_person_rep = _add(cell(row, EXCUSED_REP), cell(row, NOEXCUSE_REP))
        party_dem = _add(mail_dem, in_person_dem)
        party_rep = _add(mail_rep, in_person_rep)
        # Kentucky splits out only Democrats and Republicans. The residual mixes
        # unaffiliated voters with third parties, which are different things, so
        # neither bucket is derivable. Blank, never 0 -- see THE BLANK RULE.
        party = dict(party_dem=party_dem, party_rep=party_rep,
                     party_npa=None, party_oth=None)

        if name.lower() in TOTALS_LABELS:
            state_rows.append(StateDay(
                cycle=cycle, state="KY", day=day,
                ballots_total=total,
                mail_requested=mail_sent,
                mail_returned=mail_back,
                inperson=in_person,
                **party,
            ))
            continue

        hit = _fips.lookup("KY", name)
        if hit is None:
            unknown.append(name)
            continue
        fips, canonical = hit
        county_rows.append(CountyDay(
            cycle=cycle, state="KY", county_fips=fips, day=day,
            county_name=canonical,
            ballots_total=total,
            mail_returned=mail_back,
            inperson=in_person,
            **party,
        ))
        # The crosstab. A band whose ballot count Kentucky did not report at all
        # is an absent row, never a zero one -- THE BLANK RULE.
        for band, count, dem, rep in (
            (METHOD_MAIL, mail_back, mail_dem, mail_rep),
            (METHOD_INPERSON, in_person, in_person_dem, in_person_rep),
        ):
            if count is None:
                continue
            method_rows.append(MethodDay(
                cycle=cycle, state="KY", county_fips=fips, day=day,
                method=band, county_name=canonical,
                ballots_total=count,
                party_dem=dem, party_rep=rep,
                # Kentucky splits out DEM and REP and nothing else; the residual
                # mixes unaffiliated with third parties and is not derivable.
                party_npa=None, party_oth=None,
            ))

    if unknown:
        # Kentucky has exactly 120 counties and they do not change.
        raise SchemaDrift(f"KY: unrecognised county names {sorted(set(unknown))[:5]}")
    if not county_rows:
        raise SchemaDrift("KY: absentee report produced no county rows")

    return _methods.attach(
        FetchResult(state_rows=state_rows, county_rows=county_rows), method_rows)


class KYScraper(Adapter):
    """Tier 1 for Kentucky: the SBE's dated daily absentee workbook."""

    state = "KY"
    name = "ky-sbe"
    tier = TIER_SCRAPER

    # ------------------------------------------------------------------
    def _window(self, cycle: int) -> tuple[date, date]:
        end = election_date(cycle)
        return end - timedelta(days=WINDOW_DAYS), end

    def _load(self, day: date, *, use_cache: bool) -> bytes | None:
        """The workbook for one date, or None if Kentucky posted none that day."""
        name = FILENAME.format(stamp=stamp(day))
        try:
            body = get(f"{BASE}/{name}", state="KY", filename=name,
                       use_cache=use_cache, min_bytes=4096)
        except Missing:
            return None
        if not looks_like_xlsx(body):
            return None
        return body

    # ------------------------------------------------------------------
    def fetch(self, cycle: int, as_of: date) -> FetchResult:
        start, end = self._window(cycle)
        newest = min(as_of, end)
        if newest < start:
            raise NotYetPublished(
                f"KY: general-election absentee reporting for {cycle} does not open "
                f"until {start.isoformat()}"
            )

        for offset in range(LOOKBACK_DAYS + 1):
            day = newest - timedelta(days=offset)
            if day < start:
                break
            body = self._load(day, use_cache=False)
            if body is not None:
                return parse(body, cycle, day)

        raise NotYetPublished(
            f"KY: no absentee workbook posted in the {LOOKBACK_DAYS} days to "
            f"{newest.isoformat()}"
        )

    def fetch_history(self, cycle: int) -> FetchResult:
        """Every archived daily snapshot for a past cycle.

        Kentucky's dated filenames mean the archive really is a daily series --
        unlike a state that overwrites one report in place -- so a backfill
        recovers the curve, not just the final. Days with no file are simply
        skipped; Kentucky does not post every day.
        """
        if cycle >= datetime.now().year:
            raise NotYetPublished(f"KY: {cycle} is not an archived cycle")

        end = election_date(cycle)
        combined = FetchResult()
        _methods.attach(combined, [])
        for offset in range(HISTORY_DAYS + 1):
            day = end - timedelta(days=HISTORY_DAYS - offset)
            body = self._load(day, use_cache=True)
            if body is None:
                continue
            one = parse(body, cycle, day)
            combined.extend(one)
            # An attribute is invisible to `FetchResult.extend`; see _methods.py.
            _methods.extend(combined, _methods.rows_of(one))
        if not combined:
            raise NotYetPublished(f"KY: no archived absentee workbooks for {cycle}")
        return combined
