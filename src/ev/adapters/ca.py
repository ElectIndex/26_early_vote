"""California: the Secretary of State's during-season VBM statistics workbook.

The earlier coverage survey concluded that California "publishes nothing on
ballots returned while the vote-by-mail window is open". **That is wrong**, and
the file that disproves it is a statewide, county-level, machine-readable
workbook on a constructible URL:

    https://elections.cdn.sos.ca.gov/statewide-elections/{cycle}-general/vbm-statistics.xlsx

Its during-season existence is not inferred. The Wayback Machine holds captures
of that path taken **before** each Election Day -- 2022-10-27 and 2022-11-05 for
a 2022-11-08 election, 2024-10-20 for 2024-11-05, and 2024-02-24 through
2024-03-02 for the March primary -- and the counts grow between them (Alameda's
returned ballots go 60,892 on 10/27 to 166,600 on 11/05), which is what makes it
a tracker rather than a restatement. The SoS overwrites one URL per election
rather than dating the filename, so the whole curve is not recoverable from one
download; each day's run captures that day's snapshot.

The sheet this module reads is **"VBM Press Version"**, because it is the ONLY
sheet common to every version of the file. The during-season workbooks carry
eight sheets (`VBM Ballot Counts`, `VPH Counts`, `Provisional`, ...) and the
final one carries just this sheet, but its header is identical in all of them:

```
COUNTY | County Type | REGISTRATION (15-day ROR) 2020 |
Total voters Issued VBM ballots | Drop Box | Drop Off Location |
Vote Center Drop Off | Mail | FAX | Other | Sum |
Total Accepted VBM ballots | Total VBM Ballots in Review * |
Accepted % of Voter-returned Ballots
```

Judgement calls:

* **`ballots_total` is `Sum`, the ballots voters RETURNED**, not `Total Accepted
  VBM ballots`. Accepted is a subset that lags returns by the county's review
  queue -- California prints the queue's size in the next column -- and a
  headline that moves when a county finishes signature checking is not turnout.
  Verified that `Sum` is exactly the six return-channel columns added up
  (Alameda 2022-10-27: 16,551 + 341 + 0 + 43,781 + 214 + 5 = 60,892).

* **Every return channel is `mail_returned`, and `inperson` stays None.** Drop
  box, drop-off location, vote-centre drop-off, post, fax and "other" are all a
  vote-by-mail ballot coming home; this workbook says nothing at all about
  ballots cast in person at a vote centre, so that field is blank rather than 0.
  See THE BLANK RULE in schema.py.

* **The statewide row is coverage-gated exactly as `tx.py` gates Texas.**
  California's own `Total` row is published only when all 58 counties are in the
  sheet; otherwise the county rows go out and no `StateDay` does.

* **The as-of date is the CDN's `Last-Modified`.** The workbook itself says only
  that its statistics "are as of a specific date and time" without giving one,
  and the URL carries no date either, so the object's own write time is the only
  honest stamp. That is why this module has its own transport rather than
  calling `_net.get`, which does not return headers.

* **A missing file answers 403, not 404.** The CDN is S3 behind CloudFront with
  listing denied, so a key that does not exist returns `403` with
  `Server: AmazonS3` and a `<Code>AccessDenied</Code>` body -- verified today for
  every 2026 path. That is absence (`NotYetPublished`), and it is recognised
  from the BODY rather than the status, so a real WAF block still reads as
  SourceError. Tennessee's bucket behaves the same way; see `tn.py`.

**The format churns, and that is this adapter's live risk.** 2022 published
`.xlsm` and `.pdf`, 2024 published `.xls` and `.pdf`. `.xls` is legacy OLE2
(verified: the archived 2024 general file begins `d0 cf 11 e0`), which `openpyxl`
cannot open. `xlrd` (2.x, which reads .xls and only .xls -- the mirror image of
openpyxl) is now a declared dependency, so both vintages are covered. It is
imported defensively all the same: without it a .xls cycle raises SourceError
naming exactly that and California falls through to the aggregator, rather than
the module failing to import at all.

California registers voters by party. This workbook does not break it out, so
all four party fields are None -- not reported, not zero.
"""

from __future__ import annotations

import io
import logging
from datetime import date, datetime, timedelta, timezone

from ..calendar import election_date
from ..schema import TIER_SCRAPER, CountyDay, StateDay
from . import _fips
from ._net import DEFAULT_HEADERS, DEFAULT_TIMEOUT, SESSION, cache_path
from .base import Adapter, FetchResult, NotYetPublished, SchemaDrift, SourceError

log = logging.getLogger(__name__)

#: VERIFIED live 2026-09-06:
#:   `.../2022-general/vbm-statistics.xlsm` -> 200, 77,507 b, real xlsm
#:       (Last-Modified: Mon, 16 Jun 2025 22:51:49 GMT)
#:   `.../2024-general/vbm-statistics.xls`  -> 403 AccessDenied (withdrawn)
#:   every `.../2026-general/vbm-statistics.*` -> 403 AccessDenied (not posted)
#: and in the Wayback Machine, during the 2022 and 2024 seasons:
#:   `.../2022-general/vbm-statistics.xlsm` 2022-10-27 (452,327 b), 2022-11-05
#:   `.../2024-general/vbm-statistics.xls`  2024-11-01 (129,536 b)
#:   `.../2024-general/vbm-statistics.pdf`  2024-10-20
BASE = "https://elections.cdn.sos.ca.gov/statewide-elections"
URL = BASE + "/{cycle}-general/vbm-statistics{ext}"

#: Tried in order. `.xls` is deliberately absent: the 2024 file of that name is
#: legacy OLE2. openpyxl reads the first two; xlrd reads the third and nothing
#: else. Tried in this order because the modern format is the one in use.
EXTENSIONS = (".xlsx", ".xlsm", ".xls")

try:  # pragma: no cover - import-time capability probe
    import xlrd as _xlrd
except Exception:  # noqa: BLE001
    _xlrd = None

#: The one sheet present in every version of this workbook.
SHEET = "VBM Press Version"

COUNTY = "county"
ISSUED = "total voters issued vbm ballots"
RETURNED = "sum"
ACCEPTED = "total accepted vbm ballots"
#: The six return channels that `Sum` adds up. Kept as an explicit list so a new
#: channel appearing changes the reconciliation and raises rather than silently
#: dropping ballots out of the check.
CHANNELS = ("drop box", "drop off location", "vote center drop off",
            "mail", "fax", "other")
REQUIRED = (COUNTY, ISSUED, RETURNED, ACCEPTED) + CHANNELS

TOTAL_LABELS = {"total", "totals", "statewide", "state total", "grand total"}

EXPECTED_COUNTIES = len(_fips.CENSUS_COUNTIES["CA"])

#: How far either side of Election Day the object's write time may fall for the
#: snapshot to be this cycle's general. The live 2022 workbook is stamped
#: 2025-06-16 -- three years after its election -- which is exactly the stale
#: re-upload this window is here to refuse.
WINDOW_BEFORE = 120
WINDOW_AFTER = 30

#: S3's answer for a key that does not exist, since bucket listing is denied.
ABSENT_MARKERS = (b"<Code>AccessDenied</Code>", b"<Code>NoSuchKey</Code>")


def _norm(value) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _count(value) -> int | None:
    """A ballot count, or None when California left the cell blank.

    Blank is real in this sheet: a county with no fax returns leaves the cell
    empty rather than writing 0, and the two are not the same claim.
    """
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
        raise SchemaDrift(f"CA: {value!r} is not a ballot count") from exc


def _add(*values: int | None) -> int | None:
    known = [v for v in values if v is not None]
    return sum(known) if known else None


def last_modified(raw: str | None) -> date | None:
    """An HTTP `Last-Modified` header as a date, or None if unusable."""
    if not raw:
        return None
    try:
        return datetime.strptime(raw.strip(), "%a, %d %b %Y %H:%M:%S %Z").replace(
            tzinfo=timezone.utc
        ).date()
    except ValueError:
        return None


def looks_absent(body: bytes) -> bool:
    """True if this 403 is S3 saying "no such key" rather than a WAF saying no."""
    return any(marker in body[:512] for marker in ABSENT_MARKERS)


def download(url: str, *, filename: str,
             use_cache: bool = False) -> tuple[bytes, date | None]:
    """GET `url`, returning its bytes and the object's own write date.

    `_net.get` is not used because it discards response headers, and
    `Last-Modified` is the only as-of date this source has.
    """
    path = cache_path("CA", filename)
    stamp = path.with_suffix(path.suffix + ".last-modified")
    if use_cache and path.exists() and path.stat().st_size >= 4096:
        cached = stamp.read_text().strip() if stamp.exists() else None
        return path.read_bytes(), last_modified(cached)

    try:
        response = SESSION.get(url, headers=DEFAULT_HEADERS, timeout=DEFAULT_TIMEOUT)
    except Exception as exc:  # noqa: BLE001 - requests has its own hierarchy
        raise SourceError(f"CA: GET {url} failed: {exc}") from exc

    if response.status_code in (403, 404, 410):
        if response.status_code != 403 or looks_absent(response.content):
            raise NotYetPublished(f"CA: {url} is not posted "
                                  f"(HTTP {response.status_code})")
        raise SourceError(f"CA: {url} returned HTTP 403 without an S3 "
                          f"AccessDenied body -- we were refused, not answered")
    if response.status_code != 200:
        raise SourceError(f"CA: {url} returned HTTP {response.status_code}")

    body = response.content
    if len(body) < 4096:
        raise NotYetPublished(f"CA: {url} returned only {len(body)} bytes")
    if body[:2] != b"PK":
        raise SourceError(
            f"CA: {url} is not an xlsx/xlsm (it begins {body[:4]!r}); the SoS "
            f"published legacy .xls or a PDF this cycle, which this adapter "
            f"cannot read"
        )
    path.write_bytes(body)
    written = last_modified(response.headers.get("Last-Modified"))
    if written:
        stamp.write_text(response.headers["Last-Modified"])
    return body, written


#: The OLE2 compound-document magic. A .xls begins with it; a .xlsx is a zip and
#: begins "PK". Dispatching on the CONTENT rather than the URL's extension means
#: a file served under the wrong name still reads correctly -- which California
#: has done before now.
_OLE2_MAGIC = b"\xd0\xcf\x11\xe0"


def _rows(body: bytes) -> list[list]:
    """Every row of the VBM sheet, whichever workbook format it arrived in.

    openpyxl reads .xlsx/.xlsm and cannot open .xls at all; xlrd 2.x reads .xls
    and nothing else. They are exact complements, so between them every vintage
    California has published is covered.
    """
    if body[:4] == _OLE2_MAGIC:
        if _xlrd is None:
            raise SourceError(
                "CA: the VBM workbook is legacy .xls and xlrd is not installed"
            )
        try:
            book = _xlrd.open_workbook(file_contents=body)
        except Exception as exc:  # noqa: BLE001 - xlrd raises a zoo of types
            raise SourceError(f"CA: could not open the .xls VBM workbook: {exc}") from exc
        names = book.sheet_names()
        if SHEET not in names:
            raise SchemaDrift(f"CA: the VBM workbook has no {SHEET!r} sheet (it has {names})")
        sheet = book.sheet_by_name(SHEET)
        return [list(sheet.row_values(i)) for i in range(sheet.nrows)]

    import openpyxl  # noqa: PLC0415 - heavy import, only needed on a real fetch

    try:
        book = openpyxl.load_workbook(io.BytesIO(body), read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001 - openpyxl raises a zoo of types
        raise SourceError(f"CA: could not open the VBM workbook: {exc}") from exc
    if SHEET not in book.sheetnames:
        raise SchemaDrift(
            f"CA: the VBM workbook has no {SHEET!r} sheet (it has "
            f"{book.sheetnames})"
        )
    return [list(r) for r in book[SHEET].iter_rows(values_only=True)]


def parse(body: bytes, cycle: int, day: date) -> FetchResult:
    """Parse one VBM statistics workbook into county rows and a statewide row."""
    rows = _rows(body)
    header_at = next(
        (i for i, r in enumerate(rows)
         if any(_norm(c) == COUNTY for c in r)),
        None,
    )
    if header_at is None:
        raise SchemaDrift(f"CA: the {SHEET!r} sheet has no COUNTY header row")
    header = [_norm(c) for c in rows[header_at]]
    index = {name: i for i, name in enumerate(header) if name}
    missing = [c for c in REQUIRED if c not in index]
    if missing:
        raise SchemaDrift(f"CA: the {SHEET!r} sheet is missing columns {missing}")

    def cell(row: list, column: str) -> int | None:
        i = index[column]
        return _count(row[i]) if i < len(row) else None

    county_rows: list[CountyDay] = []
    total_row: list | None = None
    unknown: list[str] = []
    for row in rows[header_at + 1:]:
        label = str(row[index[COUNTY]] or "").strip() if index[COUNTY] < len(row) else ""
        if not label:
            continue
        if label.lower() in TOTAL_LABELS:
            total_row = row
            continue
        # The sheet ends with two footnote paragraphs in the COUNTY column.
        if len(label) > 40:
            continue
        hit = _fips.lookup("CA", label)
        if hit is None:
            unknown.append(label)
            continue
        fips, canonical = hit

        returned = cell(row, RETURNED)
        channels = _add(*(cell(row, c) for c in CHANNELS))
        if returned is not None and channels is not None and returned != channels:
            raise SchemaDrift(
                f"CA: {canonical}'s Sum is {returned:,} but its return channels "
                f"add to {channels:,} -- the columns are not what they say"
            )
        county_rows.append(CountyDay(
            cycle=cycle, state="CA", county_fips=fips, day=day,
            county_name=canonical,
            # Ballots voters RETURNED, not the subset already accepted.
            ballots_total=returned,
            ballots_new=None,
            # Every channel in this workbook is a vote-by-mail ballot coming
            # back; in-person vote-centre ballots are not in it at all.
            mail_returned=returned,
            inperson=None,
            # California registers by party; this workbook does not split it.
            party_dem=None, party_rep=None, party_oth=None, party_npa=None,
        ))

    if unknown:
        raise SchemaDrift(f"CA: unrecognised county names {sorted(set(unknown))[:5]}")
    if not county_rows:
        raise SchemaDrift(f"CA: the {SHEET!r} sheet produced no county rows")

    state_rows: list[StateDay] = []
    covered = len({r.county_fips for r in county_rows})
    if total_row is not None and covered == EXPECTED_COUNTIES:
        state_rows.append(StateDay(
            cycle=cycle, state="CA", day=day,
            ballots_total=cell(total_row, RETURNED),
            ballots_new=None,
            mail_requested=cell(total_row, ISSUED),
            mail_returned=cell(total_row, RETURNED),
            inperson=None,
            party_dem=None, party_rep=None, party_oth=None, party_npa=None,
        ))
    else:
        # PARTIAL COVERAGE, as in tx.py: California's own Total row sums only
        # the counties printed above it, and a 55-county figure published as
        # California would be wrong in a way that looks right.
        log.warning(
            "CA: the VBM workbook covers %d of %d counties; publishing counties "
            "only, no statewide row", covered, EXPECTED_COUNTIES,
        )
    return FetchResult(state_rows=state_rows, county_rows=county_rows)


class CAScraper(Adapter):
    """Tier 1 for California: the SoS's VBM statistics workbook."""

    state = "CA"
    name = "ca-sos"
    tier = TIER_SCRAPER

    def _load(self, cycle: int, *, use_cache: bool) -> tuple[bytes, date | None]:
        problems: list[str] = []
        absent = 0
        for ext in EXTENSIONS:
            url = URL.format(cycle=cycle, ext=ext)
            try:
                return download(url, filename=f"{cycle}_vbm-statistics{ext}",
                                use_cache=use_cache)
            except NotYetPublished as exc:
                absent += 1
                problems.append(str(exc))
            except SourceError as exc:
                problems.append(str(exc))
        detail = f"CA: no readable VBM workbook for {cycle} ({'; '.join(problems)})"
        if absent == len(EXTENSIONS):
            raise NotYetPublished(detail)
        raise SourceError(detail)

    def fetch(self, cycle: int, as_of: date) -> FetchResult:
        body, written = self._load(cycle, use_cache=False)
        if written is None:
            raise SourceError(
                "CA: the VBM workbook came back without a Last-Modified header, "
                "and nothing inside it carries an as-of date"
            )
        voting = election_date(cycle)
        if not (voting - timedelta(days=WINDOW_BEFORE) <= written
                <= voting + timedelta(days=WINDOW_AFTER)):
            raise NotYetPublished(
                f"CA: the {cycle} VBM workbook was last written {written.isoformat()}, "
                f"outside the window around {voting.isoformat()} -- it is a "
                f"leftover, not this season's snapshot"
            )
        if written > as_of:
            raise SourceError(
                f"CA: the VBM workbook is stamped {written.isoformat()}, after "
                f"the run date {as_of.isoformat()}"
            )
        return parse(body, cycle, written)

    def fetch_history(self, cycle: int) -> FetchResult:
        """There is no archive: the SoS overwrites one URL per election.

        The 2022 general's workbook is still served (200, 77,507 bytes) but it
        is the FINAL restatement and its object was rewritten on 2025-06-16, so
        it carries no honest during-season date; the during-season copies exist
        only in the Wayback Machine. `fetch` refuses a stale stamp for exactly
        this reason, and so does this.
        """
        raise NotYetPublished(
            f"CA: the SoS overwrites one vbm-statistics URL per election, so "
            f"there is no dated {cycle} archive to backfill from"
        )
