"""Colorado — the Secretary of State's daily "Election Activity" workbook.

Colorado mails every active voter a ballot, so there is no request stage to
track: the only number that moves is how many ballots have come back. The SoS
publishes that as one XLSX per reporting day, alongside the ballot-return press
release, at

    https://www.coloradosos.gov/pubs/newsRoom/pressReleases/<yyyy>/<yyyymmdd>ElectionActivity.xlsx

The workbook carries a county x party matrix three times over — all returned
ballots, the mail subset, and the in-person (vote-center) subset — plus a
gender/age cut we do not consume. Because Colorado runs vote centers on top of
the all-mail system, `inperson` here is a real, separately reported number and
not a derived one.

Three things drive the parser's shape:

* **The sheet set changes between cycles.** The 2024 workbook has
  `Returned_Mail_Ballots_By_County` and `In_Person_Ballots_By_County`; the 2022
  workbook has neither — it names the in-person sheet `In_Person_by_Party_County`
  and ships no per-county mail sheet at all. Sheets are therefore looked up by an
  alias list, the all-returned sheet is required, and the other two are optional.
  When Colorado did not publish a per-county mail split, `mail_returned` is blank.
  It would be arithmetic to derive it (the all-returned sheet's own title says
  "MAIL AND IN PERSON COMBINED"), but blank means "Colorado did not report this"
  and every number we publish should be one Colorado published. See THE BLANK
  RULE in schema.py.

* **The header row moves.** In 2024 the sheet title sits in column A and the
  `COUNTY | ACN | APV | ... | Grand Total` header is the second row; in 2022 the
  title sits in column B. Everything is found by scanning for the row whose first
  cell is "COUNTY" and then matching columns BY NAME, so a new minor party
  appearing mid-cycle shifts nothing.

* **Colorado's party codes are Colorado's, not ours.** `UAF` is *Unaffiliated*
  and belongs in `party_npa`, never in `party_oth`: a Colorado unaffiliated voter
  has declined a party, a Libertarian has chosen one, and the unaffiliated share
  is the single most-watched number in early-vote coverage. The codes are
  translated to the vocabulary `normalize.party()` already knows rather than
  being mapped straight onto buckets here, so there is still exactly one party
  vocabulary in the project.

The statewide row is Colorado's own `Grand Total` row, not a sum of ours, so our
headline can never disagree with the Secretary of State's.
"""

from __future__ import annotations

import io
import logging
from datetime import date, timedelta

import openpyxl

from ..calendar import election_date
from ..normalize import PARTY_DEM, PARTY_NPA, PARTY_OTH, PARTY_REP, party as _party
from ..schema import TIER_SCRAPER, CountyDay, StateDay
from . import _fips
from ._net import Missing, get, looks_like_xlsx
from .base import Adapter, FetchResult, NotYetPublished, SchemaDrift, SourceError

log = logging.getLogger(__name__)

#: VERIFIED: 20241028/20241029/20241031 and 20221024/20221026/20221031/20221101/
#: 20221102/20221103/20221107 all return a real workbook here; neighbouring dates
#: 404. Colorado posts on the days it issues a return release, not every calendar
#: day, which is exactly the NotYetPublished path this adapter is built around.
BASE = "https://www.coloradosos.gov/pubs/newsRoom/pressReleases/{year}/"
FILENAME = "{stamp}ElectionActivity.xlsx"

#: Sheet names, lowercased. The all-returned sheet is required; the other two are
#: optional because 2022 did not ship them under any name.
SHEET_ALL = ("all_returned_ballots_by_county",)
SHEET_MAIL = ("returned_mail_ballots_by_county",)
SHEET_INPERSON = ("in_person_ballots_by_county", "in_person_by_party_county")

COUNTY_COLUMN = "county"
TOTAL_COLUMN = "grand total"
END_MARKER = "end of worksheet"

#: Colorado's party codes -> the label `normalize.party()` knows them by. Taken
#: verbatim from the workbook's own Voter_Counts legend ("Party Code"/"Party
#: Name"). A code that is not here is drift: we refuse to bucket an unrecognised
#: party rather than quietly folding a new one into "other".
PARTY_LABELS = {
    "DEM": "democratic",           # Colorado Democratic Party
    "REP": "republican",           # Colorado Republican Party
    "UAF": "unaffiliated",         # -> npa. NOT "other". See the module docstring.
    "ACN": "constitution",         # American Constitution Party
    "APV": "minor",                # Approval Voting Party
    "CTR": "minor",                # Colorado Center Party
    "FWD": "forward",              # Colorado Forward Party
    "GRN": "green",                # Green Party of Colorado
    "LBR": "libertarian",          # Libertarian Party of Colorado
    "LIB": "libertarian",          # the gender/age sheets spell LBR this way
    "NOL": "no labels",            # No Labels Colorado Party
    "UNI": "unity",                # Unity Party of Colorado
}

_PARTY_FIELD = {
    PARTY_DEM: "party_dem", PARTY_REP: "party_rep",
    PARTY_NPA: "party_npa", PARTY_OTH: "party_oth",
}

#: How far either side of Election Day to sweep when backfilling a past cycle.
#: Colorado's mail ballots go out ~22 days ahead and returns are reported for a
#: week after, so this brackets the whole published series with room to spare.
HISTORY_BEFORE = 30
HISTORY_AFTER = 7


def _norm(raw) -> str:
    return " ".join(str(raw or "").strip().lower().split())


def _int(value) -> int | None:
    """A count, or None when Colorado left the cell empty."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip().replace(",", "")
    if not text or text in {"-", "--", "N/A", "n/a"}:
        return None
    try:
        return int(float(text))
    except ValueError as exc:
        raise SchemaDrift(f"CO: {value!r} is not a count") from exc


def _bucket(code: str) -> str:
    """Colorado party code -> one of normalize's four buckets."""
    label = PARTY_LABELS.get(code.strip().upper())
    if label is None:
        raise SchemaDrift(f"CO: unrecognised party code {code!r}")
    bucket = _party(label)
    if bucket is None:  # pragma: no cover - PARTY_LABELS is checked against normalize
        raise SchemaDrift(f"CO: normalize does not know party label {label!r}")
    return bucket


class _Sheet:
    """One county x party matrix: per-county party counts plus Colorado's total."""

    __slots__ = ("counties", "totals", "statewide", "statewide_total")

    def __init__(self) -> None:
        self.counties: dict[str, dict[str, int]] = {}
        self.totals: dict[str, int | None] = {}
        self.statewide: dict[str, int] = {}
        self.statewide_total: int | None = None


def _read_matrix(sheet) -> _Sheet:
    rows = sheet.iter_rows(values_only=True)
    header = None
    for row in rows:
        if row and _norm(row[0]) == COUNTY_COLUMN:
            header = row
            break
    if header is None:
        raise SchemaDrift(f"CO: sheet {sheet.title!r} has no COUNTY header row")

    party_columns: list[tuple[int, str]] = []
    total_column: int | None = None
    for i, cell in enumerate(header[1:], start=1):
        name = _norm(cell)
        if not name:
            continue
        if name == TOTAL_COLUMN:
            total_column = i
            continue
        party_columns.append((i, _bucket(str(cell))))
    if not party_columns:
        raise SchemaDrift(f"CO: sheet {sheet.title!r} has no party columns")

    out = _Sheet()
    for row in rows:
        if not row:
            continue
        label = " ".join(str(row[0] or "").split())
        if not label or _norm(label) == END_MARKER:
            continue

        counts: dict[str, int] = {}
        for i, bucket in party_columns:
            value = _int(row[i]) if i < len(row) else None
            if value is not None:
                counts[bucket] = counts.get(bucket, 0) + value
        total = _int(row[total_column]) if total_column is not None and total_column < len(row) else None

        if _norm(label) == TOTAL_COLUMN:
            # Colorado's own statewide row -- see the module docstring.
            out.statewide = counts
            out.statewide_total = total
            continue
        out.counties[label] = counts
        out.totals[label] = total
    return out


def _find_sheet(book, names: tuple[str, ...]):
    for title in book.sheetnames:
        if _norm(title) in names:
            return book[title]
    return None


def parse(body: bytes, cycle: int, day: date) -> FetchResult:
    """Parse one Election Activity workbook into canonical rows for `day`."""
    if not looks_like_xlsx(body):
        raise SourceError("CO: election activity report was not an xlsx")
    try:
        book = openpyxl.load_workbook(io.BytesIO(body), read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001 -- openpyxl raises a zoo of types
        raise SourceError(f"CO: could not open election activity workbook: {exc}") from exc

    all_sheet = _find_sheet(book, SHEET_ALL)
    if all_sheet is None:
        raise SchemaDrift(
            f"CO: workbook has no all-returned county sheet; found {book.sheetnames}"
        )
    returned = _read_matrix(all_sheet)

    mail_sheet = _find_sheet(book, SHEET_MAIL)
    mail = _read_matrix(mail_sheet) if mail_sheet is not None else None
    inperson_sheet = _find_sheet(book, SHEET_INPERSON)
    inperson = _read_matrix(inperson_sheet) if inperson_sheet is not None else None

    if not returned.counties:
        raise SchemaDrift("CO: all-returned sheet produced no county rows")

    state = StateDay(
        cycle=cycle, state="CO", day=day,
        ballots_total=returned.statewide_total,
        # Colorado is an all-mail state: every active voter is sent a ballot
        # automatically and the SoS publishes no "requested" count, because there
        # is nothing to request. Blank, not zero.
        mail_requested=None,
        mail_returned=mail.statewide_total if mail is not None else None,
        inperson=inperson.statewide_total if inperson is not None else None,
        # Colorado registers by party and reports every bucket, so a party with no
        # ballots yet is a genuine 0 -- blank here would claim Colorado does not
        # report party at all.
        **{field: returned.statewide.get(key, 0) for key, field in _PARTY_FIELD.items()},
    )

    county_rows: list[CountyDay] = []
    unknown: list[str] = []
    for label, counts in returned.counties.items():
        hit = _fips.lookup("CO", label)
        if hit is None:
            unknown.append(label)
            continue
        fips, canonical = hit
        county_rows.append(CountyDay(
            cycle=cycle, state="CO", county_fips=fips, day=day,
            county_name=canonical,
            ballots_total=returned.totals.get(label),
            mail_returned=mail.totals.get(label) if mail is not None else None,
            inperson=inperson.totals.get(label) if inperson is not None else None,
            **{field: counts.get(key, 0) for key, field in _PARTY_FIELD.items()},
        ))

    if unknown:
        # A name we cannot place is a county we would silently drop off the map.
        raise SchemaDrift(f"CO: unrecognised county names {sorted(set(unknown))[:5]}")

    return FetchResult(state_rows=[state], county_rows=county_rows)


class COScraper(Adapter):
    """Tier 1 for Colorado: the SoS daily Election Activity workbook."""

    state = "CO"
    name = "co-sos"
    tier = TIER_SCRAPER

    def _url(self, day: date) -> tuple[str, str]:
        stamp = day.strftime("%Y%m%d")
        filename = FILENAME.format(stamp=stamp)
        return BASE.format(year=day.year) + filename, filename

    def _load(self, day: date, *, use_cache: bool) -> bytes | None:
        """The workbook for one day, or None when Colorado posted nothing."""
        url, filename = self._url(day)
        try:
            body = get(url, state="CO", filename=filename,
                       use_cache=use_cache, min_bytes=4096)
        except Missing:
            return None
        if not looks_like_xlsx(body):
            # The SoS CMS answers an unposted file with a styled HTML page.
            return None
        return body

    def fetch(self, cycle: int, as_of: date) -> FetchResult:
        body = self._load(as_of, use_cache=False)
        if body is None:
            raise NotYetPublished(
                f"CO: no election activity workbook posted for {as_of.isoformat()}"
            )
        return parse(body, cycle, as_of)

    def fetch_history(self, cycle: int) -> FetchResult:
        """Every archived daily workbook for a past cycle.

        Colorado keeps each day's file under its own dated name rather than
        overwriting one workbook, so a past cycle really does backfill as a daily
        series. The days it skipped simply 404 and are skipped here too.
        """
        election = election_date(cycle)
        result = FetchResult()
        for offset in range(-HISTORY_BEFORE, HISTORY_AFTER + 1):
            day = election + timedelta(days=offset)
            body = self._load(day, use_cache=True)
            if body is None:
                continue
            result.extend(parse(body, cycle, day))
        if not result:
            raise NotYetPublished(f"CO: no archived election activity workbooks for {cycle}")
        return result
