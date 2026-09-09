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

**THE SHEET IS NOT ALWAYS CALLED THE SAME THING, AND THE FILE IS NOT ALWAYS A
WORKBOOK.** This module used to assert that "VBM Press Version" is the ONLY
sheet common to every version of the file. That is true of 2022 and false of
2024, which published one sheet called `Ballot Return Statistics` and nothing
else -- so every 2024 capture raised SchemaDrift and California's biggest cycle
was unreadable. Both names are accepted now; an unknown one is still drift.

2022's header, which the archived `.xlsm` captures still carry:

```
COUNTY | County Type | REGISTRATION (15-day ROR) 2020 |
Total voters Issued VBM ballots | Drop Box | Drop Off Location |
Vote Center Drop Off | Mail | FAX | Other | Sum |
Total Accepted VBM ballots | Total VBM Ballots in Review * |
Accepted % of Voter-returned Ballots
```

2024 dropped the registration column and **added a whole second block, ballots
cast IN PERSON at a vote centre**, which 2022 does not report at all:

```
... | Other | Sum | Total Accepted VBM ballots | Total VBM Ballots in Review * |
Accepted % of Voter-returned Ballots | Regular Ballots |
Provisional Ballots (Non-CVR) | Conditional (CVR) | Conditional ("Instant" CVR) |
Sum | Total Accepted | Accepted % of Registration | TOTAL BALLOTS CAST
```

⚠️ **`Sum` APPEARS TWICE IN THAT HEADER** -- once closing the six vote-by-mail
return channels, once closing the four in-person ones -- so a name-to-column map
built the obvious way binds `RETURNED` to the in-person total, and Los Angeles'
2024-11-05 `mail_returned` reads 424,340 instead of 1,641,552 -- the count of
ballots that did NOT come by mail. `_index` resolves every name to its FIRST
column and `_inperson_sum` finds the second `Sum` by position; see both. Three
arithmetic reconciliations -- each printed Sum against its own channels, and
`TOTAL BALLOTS CAST` against the two Sums -- are what actually prove the mapping,
and they fail loudly rather than publishing a plausible wrong number.

⚠️ **AND THE IN-PERSON COLUMN STOPS MEANING "EARLY" ON ELECTION DAY.** California
vote centres open ten days out, so before Election Day this block is early
in-person voting and belongs on the curve. On 2024-11-05 and after it also holds
people who voted on the day itself -- statewide in-person goes 3,316 (Oct 19) to
546,627 (Nov 4) to 857,153 (Nov 5) to 2,154,200 (Nov 6). Those days are still
published, with their honest dates, exactly as `fl.py`, `ia.py` and `co.py`
publish theirs; anything reading `ballots_total` at days_to_election <= 0 as
"early vote" has to know that. The 2022 rows carry no in-person figure at all,
because 2022 did not report one -- blank, not zero.

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
  calling `_net.get`, which does not return headers. **The archived route uses
  the SAME header**, replayed by the Wayback Machine as
  `x-archive-orig-last-modified`, so a 2022 row, a 2024 row and a 2026 row all
  mean the same thing on the same day; see `fetch_history`.

* **A missing file answers 403, not 404.** The CDN is S3 behind CloudFront with
  listing denied, so a key that does not exist returns `403` with
  `Server: AmazonS3` and a `<Code>AccessDenied</Code>` body -- verified today for
  every 2026 path. That is absence (`NotYetPublished`), and it is recognised
  from the BODY rather than the status, so a real WAF block still reads as
  SourceError. Tennessee's bucket behaves the same way; see `tn.py`.

* **NOTHING IS BLOCKING CALIFORNIA.** Worth stating outright, because "CA has no
  county data in any cycle" reads like a bot wall and is not one. Re-measured
  2026-09-08 with plain `requests` and no impersonation at all, four requests
  three seconds apart:

      2026-general/vbm-statistics.xlsx -> 403, 111 b, `<Code>AccessDenied</Code>`
      2026-general/vbm-statistics.xlsm -> 403, 111 b, the same
      2026-general/vbm-statistics.xls  -> 403, 111 b, the same
      2022-general/vbm-statistics.xlsm -> 200, 77,507 b, a real workbook
                                          (Last-Modified: Mon, 16 Jun 2025)

  No WAF, no challenge, no fingerprint rule -- `Server: AmazonS3` answers us
  plainly. California is empty because the 2026 file is NOT POSTED YET (early
  voting opens 2026-10-05) and because the only live 2022 object is a June 2025
  re-upload that `fetch` refuses as a leftover. That is the whole story, and it
  is why this module has no impersonation path. Re-measured again 2026-09-09:
  every `2024-general/vbm-statistics.{xls,xlsx,pdf}` now answers 403 with the
  same 111-byte AccessDenied body -- California WITHDREW the 2024 files after
  certification, which is why the backfill goes to the Archive rather than to
  the CDN.

**The format churns, and that is this adapter's live risk.** 2022 published
`.xlsm` and `.pdf`, 2024 published `.xls` and `.pdf`. `.xls` is legacy OLE2
(verified: the archived 2024 general file begins `d0 cf 11 e0`), which `openpyxl`
cannot open. `xlrd` (2.x, which reads .xls and only .xls -- the mirror image of
openpyxl) is now a declared dependency, so both vintages are covered. It is
imported defensively all the same: without it a .xls cycle raises SourceError
naming exactly that and California falls through to the aggregator, rather than
the module failing to import at all.

⚠️ **AND `download()` USED TO THROW ALL OF THAT AWAY.** It refused any body not
beginning `PK`, which is to say every `.xls` and every `.pdf` -- so `EXTENSIONS`
listed `.xls`, `xlrd` was a declared dependency, `_rows` had a working OLE2
branch, and none of it could ever be reached, because the transport rejected the
bytes before the parser saw them. The magic-number gate now lives in `_rows`,
which is the function that dispatches on it.

**The PDF is a first-class shape of this report, not a fallback.** California
published the 2024 general as a PDF far more often than as a workbook -- the
Archive holds ONE `.xls` capture of that cycle and FOUR distinct PDFs -- so a
reader that only knows workbooks sees one day of a five-day curve. `_pdf_rows`
turns the PDF's text layer into the same rows a sheet gives, and refuses any
layout but 2024's; see the warning there for why the 2022 PDFs cannot be read
the same way and must not be made to.

California registers voters by party. This workbook does not break it out, so
all four party fields are None -- not reported, not zero.
"""

from __future__ import annotations

import io
import json
import logging
import re
from datetime import date, datetime, timedelta, timezone

from ..calendar import election_date
from ..schema import TIER_SCRAPER, CountyDay, StateDay
from . import _fips
from ._net import DEFAULT_HEADERS, DEFAULT_TIMEOUT, SESSION, _throttle, cache_path
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

#: Tried in order, modern format first. openpyxl reads the first two, xlrd the
#: third, pypdf the fourth. ⚠️ `download()` USED TO REJECT EVERYTHING BUT A ZIP,
#: so the .xls and .pdf entries here were unreachable and the whole 2024 vintage
#: -- which California published as legacy OLE2 and as a PDF and never as a zip
#: -- could not be read at all, whatever `_rows` was able to do with it. The
#: magic-number gate now lives in `_rows`, which is the thing that dispatches on
#: it, and `download()` only checks that a body is one of the three.
EXTENSIONS = (".xlsx", ".xlsm", ".xls", ".pdf")

try:  # pragma: no cover - import-time capability probe
    import xlrd as _xlrd
except Exception:  # noqa: BLE001
    _xlrd = None

#: The sheet, WHICH CALIFORNIA RENAMED BETWEEN CYCLES. 2022 published
#: "VBM Press Version"; 2024 published "Ballot Return Statistics" and nothing
#: else -- so "the one sheet present in every version", which this module used
#: to assert, was true of 2022 and only 2022, and made the whole 2024 vintage
#: unreadable. Both names are accepted; an unknown one is still SchemaDrift.
SHEET = "VBM Press Version"
SHEETS = ("Ballot Return Statistics", SHEET)

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

#: THE 2024 BLOCK -- ballots cast IN PERSON, which the 2022 sheet does not have.
#: The 2024 header repeats the label `Sum` twice, once to close the VBM return
#: channels and once to close these; `_index` resolves every name to its FIRST
#: column for exactly that reason, and `_inperson_sum` finds the second one by
#: position rather than by name. Getting that backwards maps `RETURNED` onto the
#: in-person total -- Alameda's 2024-11-05 return would read 20,069 rather than
#: 390,233 -- and it reconciles against nothing, which is how it is caught.
INPERSON_CHANNELS = ("regular ballots", "provisional ballots (non-cvr)",
                     "conditional (cvr)", 'conditional ("instant" cvr )')
INPERSON_FIRST = INPERSON_CHANNELS[0]
TOTAL_CAST = "total ballots cast"

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


#: The magic numbers of the three shapes California has published this report
#: in. Dispatching on the CONTENT rather than the URL's extension means a file
#: served under the wrong name still reads correctly -- which California has
#: done before now.
_OLE2_MAGIC = b"\xd0\xcf\x11\xe0"  # legacy .xls, a compound document
_ZIP_MAGIC = b"PK"                    # .xlsx / .xlsm, a zip
_PDF_MAGIC = b"%PDF-"
_KNOWN_MAGIC = (_ZIP_MAGIC, _OLE2_MAGIC, _PDF_MAGIC)

#: `Last-Modified` as the live CDN sends it, and as the Wayback Machine hands
#: back the ORIGINAL response's copy of it. See `fetch_history`: the archived
#: route dates its rows by the same header the live route does, so a 2024 row
#: and a 2026 row mean the same thing on the same day.
LIVE_STAMP_HEADER = "Last-Modified"
ARCHIVE_STAMP_HEADER = "x-archive-orig-last-modified"


def download(url: str, *, filename: str, use_cache: bool = False,
             stamp_header: str = LIVE_STAMP_HEADER,
             min_interval: float = 0.0) -> tuple[bytes, date | None]:
    """GET `url`, returning its bytes and the object's own write date.

    `_net.get` is not used because it discards response headers, and
    `Last-Modified` is the only as-of date this source has.

    `stamp_header` is which header carries that date: the CDN sends it plainly,
    and the Wayback Machine replays the original response's copy of it under
    `x-archive-orig-last-modified`. Same date, same meaning, two transports --
    which is what lets `fetch_history` date archived rows by the rule `fetch`
    dates live ones by, rather than by a crawl time.
    """
    path = cache_path("CA", filename)
    stamp = path.with_suffix(path.suffix + ".last-modified")
    if use_cache and path.exists() and path.stat().st_size >= 4096:
        cached = stamp.read_text().strip() if stamp.exists() else None
        return path.read_bytes(), last_modified(cached)

    if min_interval:
        _throttle(url, min_interval)
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
    if not body.startswith(_KNOWN_MAGIC):
        raise SourceError(
            f"CA: {url} is not a workbook or a PDF (it begins {body[:5]!r})"
        )
    path.write_bytes(body)
    written = last_modified(response.headers.get(stamp_header))
    if written:
        stamp.write_text(response.headers[stamp_header])
    return body, written


def _pick_sheet(names) -> str:
    """The report's sheet, under whichever of its names this vintage uses."""
    for name in SHEETS:
        if name in names:
            return name
    raise SchemaDrift(
        f"CA: the VBM workbook has none of the sheets {list(SHEETS)} "
        f"(it has {list(names)})"
    )


# --------------------------------------------------------------------------
# THE PDF SHAPE OF THE SAME TABLE
#
# California published the 2024 general's report as a PDF far more often than
# as a workbook -- the CDN kept ONE .xls capture and FOUR distinct PDFs -- so a
# reader that only knows workbooks sees one day of a five-day curve. The PDF's
# text layer is one line per county, which pypdf recovers exactly:
#
#   Alameda VCA County 966,173 355 9 0 1,007 464 0 1,835 1,822 0 99.29% 0 0 0 0
#   0 0 0.00% 1,835
#
# so the rows come back in the same list-of-lists shape a sheet does and
# `parse()` is shared, unchanged, between the two transports.
#
# ⚠️ AND THIS IS DELIBERATELY THE 2024 LAYOUT AND ONLY THE 2024 LAYOUT. The 2022
# PDFs of this same report CANNOT be read this way and must not be made to:
# their workbook leaves an unused return channel BLANK rather than writing 0
# (Alameda has no vote-centre drop-off on 2022-10-25), a blank cell prints as
# nothing at all, and the line then carries ten numbers where its neighbours
# carry eleven -- with no way to tell from the text WHICH channel went missing.
# Reading those by position would silently shift every column left. The 2024
# workbook writes an explicit 0 in every channel, which is why its rows are a
# fixed twenty-one wide and why `_pdf_rows` refuses anything else.
# --------------------------------------------------------------------------

#: The 2024 header, already normalised, in printed order. Synthesised rather
#: than read off the page because the PDF wraps its header across thirty lines
#: of single words; the arity check below is what actually proves a row matches.
PDF_HEADER: tuple[str, ...] = (
    COUNTY, "county type", ISSUED, *CHANNELS, RETURNED, ACCEPTED,
    "total vbm ballots in review *", "accepted % of voter-returned ballots",
    *INPERSON_CHANNELS, RETURNED, "total accepted", "accepted % of registration",
    TOTAL_CAST,
)

#: The three values California prints in `County Type`. A data line is found by
#: one of them rather than by a county name, because county names contain
#: spaces and these do not appear anywhere else on the page.
PDF_COUNTY_TYPES = ("VCA County", "All Mail", "Polling Place")

#: How many fields follow `County Type` on a 2024 data line. See the warning
#: above: this is the check that refuses the 2022 layout instead of misreading
#: it, so it is a hard equality and not a minimum.
PDF_FIELDS = len(PDF_HEADER) - 2

#: A printed count, a printed percentage, or a dash. Anything else on a data
#: line means the line is not what this reader thinks it is.
_PDF_FIELD = re.compile(r"^(?:-|--|[\d,]+|[\d.]+%)$")

#: The report names its own election on the first line: "2022 General Election",
#: "November, 5, 2024, General Election". Read as a guard, not as a date --
#: `fetch_history` dates rows from the object's write time. Arizona published a
#: July primary's table as November's general for want of this check.
_PDF_ELECTION = re.compile(r"(\d{4}),?\s+General Election", re.I)


def _pdf_rows(body: bytes, cycle: int) -> list[list]:
    """The 2024-layout PDF of this report, as the same rows a sheet gives."""
    import pypdf  # noqa: PLC0415 - heavy import, only needed on a real fetch

    try:
        reader = pypdf.PdfReader(io.BytesIO(body))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception as exc:  # noqa: BLE001 - pypdf raises a zoo of types
        raise SourceError(f"CA: could not read the VBM PDF: {exc}") from exc

    named = _PDF_ELECTION.search(" ".join(text.split()))
    if named is None:
        raise SchemaDrift("CA: the VBM PDF does not name a general election")
    if int(named.group(1)) != int(cycle):
        raise NotYetPublished(
            f"CA: this PDF is the {named.group(1)} general, not {cycle}'s"
        )

    rows: list[list] = [list(PDF_HEADER)]
    for line in text.splitlines():
        stripped = " ".join(line.split())
        label, fields = _pdf_split(stripped)
        if label is None:
            continue
        if len(fields) != PDF_FIELDS:
            raise SchemaDrift(
                f"CA: {label!r} prints {len(fields)} fields, not {PDF_FIELDS} "
                f"-- this is not the layout this reader knows"
            )
        rows.append([label, "", *fields])
    return rows


def _pdf_split(line: str) -> tuple[str | None, list[str]]:
    """A data line as (county or total label, printed fields), or (None, [])."""
    for kind in PDF_COUNTY_TYPES:
        marker = f" {kind} "
        if marker in line:
            label, rest = line.split(marker, 1)
            return label.strip(), rest.split()
    # The statewide row carries no County Type, so it is found by its label and
    # confirmed by every following token being a number.
    head, _, rest = line.partition(" ")
    if _norm(head) in TOTAL_LABELS and rest:
        fields = rest.split()
        if all(_PDF_FIELD.match(f) for f in fields):
            return head.strip(), fields
    # ...and in one of the two renderings California used in 2024 the word
    # "Total" is laid out with the header rather than with its own numbers, so
    # the statewide row prints as a bare line of exactly the right arity. A line
    # of nineteen numbers and nothing else is that row; a county line can never
    # reach here because it carries a County Type, which is matched first.
    fields = line.split()
    if len(fields) == PDF_FIELDS and all(_PDF_FIELD.match(f) for f in fields):
        return "Total", fields
    return None, []


def _rows(body: bytes, cycle: int) -> list[list]:
    """Every row of the VBM report, whichever shape it arrived in.

    openpyxl reads .xlsx/.xlsm and cannot open .xls at all; xlrd 2.x reads .xls
    and nothing else; pypdf reads the PDF. Between them every vintage California
    has published is covered.
    """
    if body.startswith(_PDF_MAGIC):
        return _pdf_rows(body, cycle)

    if body.startswith(_OLE2_MAGIC):
        if _xlrd is None:
            raise SourceError(
                "CA: the VBM workbook is legacy .xls and xlrd is not installed"
            )
        try:
            book = _xlrd.open_workbook(file_contents=body)
        except Exception as exc:  # noqa: BLE001 - xlrd raises a zoo of types
            raise SourceError(f"CA: could not open the .xls VBM workbook: {exc}") from exc
        sheet = book.sheet_by_name(_pick_sheet(book.sheet_names()))
        return [list(sheet.row_values(i)) for i in range(sheet.nrows)]

    import openpyxl  # noqa: PLC0415 - heavy import, only needed on a real fetch

    try:
        book = openpyxl.load_workbook(io.BytesIO(body), read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001 - openpyxl raises a zoo of types
        raise SourceError(f"CA: could not open the VBM workbook: {exc}") from exc
    name = _pick_sheet(book.sheetnames)
    return [list(r) for r in book[name].iter_rows(values_only=True)]


def _index(header: list[str]) -> dict[str, int]:
    """Column name -> its FIRST column.

    ⚠️ FIRST, not last, and the whole 2024 vintage turns on it. That header
    prints `Sum` twice -- once closing the six vote-by-mail return channels and
    once closing the four in-person ones -- so a plain dict comprehension binds
    `RETURNED` to the in-person total. Alameda's 2024-11-05 reading would have
    been 20,069 instead of 390,233, and `mail_returned` would have been the
    count of ballots that did NOT come by mail.
    """
    index: dict[str, int] = {}
    for i, name in enumerate(header):
        if name:
            index.setdefault(name, i)
    return index


def _inperson_sum(header: list[str], index: dict[str, int]) -> int | None:
    """The column of the SECOND `Sum`, which closes the in-person block.

    None when this vintage has no in-person block at all -- 2022 does not, and
    that is a blank field rather than a zero. Found by position because its name
    is not unique; see `_index`.
    """
    if INPERSON_FIRST not in index:
        return None
    return next((i for i, name in enumerate(header)
                 if name == RETURNED and i > index[INPERSON_FIRST]), None)


def parse(body: bytes, cycle: int, day: date) -> FetchResult:
    """Parse one VBM statistics report into county rows and a statewide row."""
    rows = _rows(body, cycle)
    header_at = next(
        (i for i, r in enumerate(rows)
         if any(_norm(c) == COUNTY for c in r)),
        None,
    )
    if header_at is None:
        raise SchemaDrift("CA: the VBM report has no COUNTY header row")
    header = [_norm(c) for c in rows[header_at]]
    index = _index(header)
    missing = [c for c in REQUIRED if c not in index]
    if missing:
        raise SchemaDrift(f"CA: the VBM report is missing columns {missing}")

    # The 2024 report adds ballots cast IN PERSON at a vote centre. 2022 does
    # not, and there `inperson` stays None -- not reported, not zero.
    inperson_at = _inperson_sum(header, index)
    inperson_missing = [c for c in INPERSON_CHANNELS if c not in index]
    if inperson_at is not None and (inperson_missing or TOTAL_CAST not in index):
        raise SchemaDrift(
            f"CA: the report has an in-person Sum but is missing "
            f"{inperson_missing + ([TOTAL_CAST] if TOTAL_CAST not in index else [])}"
        )

    def cell(row: list, column: str) -> int | None:
        i = index[column]
        return _count(row[i]) if i < len(row) else None

    def at(row: list, i: int | None) -> int | None:
        if i is None or i >= len(row):
            return None
        return _count(row[i])

    def reconcile(label: str, row: list) -> tuple[int | None, int | None, int | None]:
        """(returned, in person, total cast) for one row, checked against itself.

        Both sums the report prints are re-added from their own channels, and
        the report's own grand total is re-added from the two sums. That is what
        proves the column mapping is the one the header claims -- a shifted
        header fails here rather than publishing confident wrong numbers.
        """
        returned = cell(row, RETURNED)
        channels = _add(*(cell(row, c) for c in CHANNELS))
        if returned is not None and channels is not None and returned != channels:
            raise SchemaDrift(
                f"CA: {label}'s Sum is {returned:,} but its return channels "
                f"add to {channels:,} -- the columns are not what they say"
            )
        if inperson_at is None:
            return returned, None, returned
        inperson = at(row, inperson_at)
        in_channels = _add(*(cell(row, c) for c in INPERSON_CHANNELS))
        if inperson is not None and in_channels is not None and inperson != in_channels:
            raise SchemaDrift(
                f"CA: {label}'s in-person Sum is {inperson:,} but its in-person "
                f"channels add to {in_channels:,}"
            )
        cast = cell(row, TOTAL_CAST)
        both = _add(returned, inperson)
        if cast is not None and both is not None and cast != both:
            raise SchemaDrift(
                f"CA: {label}'s TOTAL BALLOTS CAST is {cast:,} but its two sums "
                f"add to {both:,}"
            )
        return returned, inperson, cast if cast is not None else both

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

        returned, inperson, cast = reconcile(canonical, row)
        county_rows.append(CountyDay(
            cycle=cycle, state="CA", county_fips=fips, day=day,
            # Every early ballot the report accounts for. Where the report has
            # no in-person block this IS the vote-by-mail figure -- the ballots
            # voters RETURNED, never the subset already accepted.
            county_name=canonical,
            ballots_total=cast,
            ballots_new=None,
            # The six return channels are all a vote-by-mail ballot coming back.
            mail_returned=returned,
            inperson=inperson,
            # California registers by party; this report does not split it.
            party_dem=None, party_rep=None, party_oth=None, party_npa=None,
        ))

    if unknown:
        raise SchemaDrift(f"CA: unrecognised county names {sorted(set(unknown))[:5]}")
    if not county_rows:
        raise SchemaDrift("CA: the VBM report produced no county rows")

    state_rows: list[StateDay] = []
    covered = len({r.county_fips for r in county_rows})
    if total_row is not None and covered == EXPECTED_COUNTIES:
        returned, inperson, cast = reconcile("the statewide total", total_row)
        state_rows.append(StateDay(
            cycle=cycle, state="CA", day=day,
            ballots_total=cast,
            ballots_new=None,
            mail_requested=cell(total_row, ISSUED),
            mail_returned=returned,
            inperson=inperson,
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


# --------------------------------------------------------------------------
# THE ARCHIVED SERIES -- see `CAScraper.fetch_history` for what is in it.
#
# California keeps no dated copies of its own: one URL per election, overwritten
# in place, and withdrawn from the CDN once the election is certified. The daily
# curve therefore exists only in the Internet Archive. This runs in `backfill`,
# never in the daily job.
# --------------------------------------------------------------------------
CDX_URL = "http://web.archive.org/cdx/search/cdx"

#: `id_` asks for the ORIGINAL bytes rather than a rewritten page -- essential
#: here, since the payload is a workbook or a PDF and any rewriting destroys it.
WAYBACK_SNAPSHOT = "https://web.archive.org/web/{stamp}id_/{url}"

#: Swept by PREFIX, not by exact URL. See the warning in `fetch_history`: three
#: of the five distinct 2024 captures are only addressable through a junk query
#: string that CloudFront ignored and the Archive did not.
ARCHIVE_PREFIX = BASE + "/{cycle}-general/vbm-statistics"

#: 160 captures of the 2024 key is the largest we have seen; 400 leaves room for
#: a cycle the Archive crawled harder without ever running away.
MAX_ARCHIVE_PROBES = 400

#: web.archive.org is slower and less tolerant than a state CDN. Same reasoning
#: as fl.py's and wa.py's constants of the same name.
ARCHIVE_MIN_INTERVAL = 1.0


def _extension(url: str) -> str:
    """`.pdf` / `.xls` / `.xlsm` from a capture's URL, for the cache filename.

    Only ever cosmetic -- `_rows` dispatches on the body's magic number, not on
    this -- but a cache full of `archive-2024….bin` is unreadable by a human
    trying to work out what a bad backfill saw.
    """
    tail = url.split("?", 1)[0].rsplit("/", 1)[-1]
    _, dot, ext = tail.rpartition(".")
    return f".{ext}" if dot and len(ext) <= 5 and ext.isalnum() else ""


def _archive_captures(cycle: int) -> list[tuple[str, str]]:
    """(timestamp, original URL) for every DISTINCT version archived, oldest first.

    Deduplicated on the payload digest HERE rather than with the CDX API's own
    `collapse=digest`, which only folds ADJACENT rows -- and a prefix sweep is
    ordered by urlkey, so the same object captured under five different query
    strings is not adjacent and would come back five times.
    """
    anchor = election_date(cycle)
    query = {
        "url": ARCHIVE_PREFIX.format(cycle=cycle),
        "matchType": "prefix", "output": "json",
        "fl": "timestamp,original,digest", "filter": "statuscode:200",
        "from": (anchor - timedelta(days=WINDOW_BEFORE)).strftime("%Y%m%d"),
        "to": (anchor + timedelta(days=WINDOW_AFTER)).strftime("%Y%m%d"),
        "limit": str(MAX_ARCHIVE_PROBES),
    }
    _throttle(CDX_URL, ARCHIVE_MIN_INTERVAL)
    try:
        response = SESSION.get(CDX_URL, params=query, headers=DEFAULT_HEADERS,
                               timeout=DEFAULT_TIMEOUT)
    except Exception as exc:  # noqa: BLE001 - requests has its own hierarchy
        raise SourceError(f"CA: the Wayback CDX index was unreachable: {exc}") from exc
    if response.status_code != 200:
        raise SourceError(
            f"CA: the Wayback CDX index answered HTTP {response.status_code}"
        )
    text = response.text.strip()
    if not text:
        # The CDX API answers "nothing archived" with an empty body. Absence,
        # not a fault.
        return []
    try:
        rows = json.loads(text)
    except ValueError as exc:
        raise SourceError(f"CA: the Wayback CDX index was not JSON: {exc}") from exc

    seen: dict[str, tuple[str, str]] = {}
    for row in sorted(rows[1:], key=lambda r: r[0]):
        stamp, original, digest = row[0], row[1], row[2]
        seen.setdefault(digest, (stamp, original))
    return sorted(seen.values())


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
        """A past cycle's daily curve, rebuilt out of the Internet Archive.

        The SoS overwrites ONE URL per election rather than dating the filename,
        so the curve is not on the CDN -- the live 2024 keys answer 403
        AccessDenied and the live 2022 workbook is a 2025-06-16 re-upload that
        `fetch` refuses as a leftover. What survives is in the Wayback Machine,
        and this walks it.

        ⚠️ THE DATING RULE IS THE LIVE ONE, NOT A NEW ONE, and that is the whole
        reason this could finally be built. The note that used to sit here said
        an archive fetcher "needs its own dating rule (the capture is when we SAW
        the file)". It does not: the Wayback Machine replays the ORIGINAL
        response's headers under `x-archive-orig-*`, so a capture still carries
        the CDN's own `Last-Modified` -- verified on every capture below, e.g.
        the 2024-11-01 .xls answers `x-archive-orig-last-modified: Thu, 31 Oct
        2024 16:15:00 GMT`, which the workbook's own hidden A1 timestamp
        (2024-10-31 01:46) independently agrees with. A capture WITHOUT that
        header is skipped rather than dated by its crawl time, because a crawl
        time is an upper bound and would put a row on the wrong day.

        VERIFIED 2026-09-09, distinct digests under
        `.../{cycle}-general/vbm-statistics*` (CDX, matchType=prefix, 200s only):

          2024   capture           served       x-archive-orig-last-modified
                 20241020170838    .pdf         2024-10-19   (as of Oct 19)
                 20241101232312    .xls         2024-10-31
                 20241105142944    .pdf         2024-11-04   (as of Nov 4)
                 20241105224543    .pdf         2024-11-05   (as of Nov 5)
                 20241106192325    .pdf         2024-11-06   (as of Nov 6)
          2022   20221027003436    .xlsm        2022-10-26
                 20221105004307    .xlsm        2022-11-04
                 20221108015555    .xlsm        2022-11-07
                 20221111024125    .xlsm        2022-11-08   (the final; the
                     archive keeps its ORIGINAL stamp, which is what makes it
                     usable where the live copy of the same bytes is not)

        ⚠️ THREE OF THE FIVE 2024 CAPTURES ARE ONLY REACHABLE THROUGH THEIR QUERY
        STRING. CloudFront ignores `?os=...&ref=app` and served the same object,
        but the Archive keys on the full URL, so the bare path has just two
        captures while the junk-query variants hold three more distinct digests.
        This sweeps by PREFIX and carries each capture's own `original` URL
        through to the fetch; asking for the bare path at those timestamps gets
        you a redirect to the nearest bare-path capture and silently collapses a
        five-day curve to two. `Memento-Datetime` on the reply is how that was
        caught, and it is worth checking again if the curve ever thins.
        """
        anchor = election_date(cycle)
        if anchor >= date.today():
            raise NotYetPublished(
                f"CA: {cycle} is not over, so there is nothing to backfill"
            )
        by_day: dict[date, bytes] = {}
        seen = 0
        for stamp, original in _archive_captures(cycle):
            try:
                body, written = download(
                    WAYBACK_SNAPSHOT.format(stamp=stamp, url=original),
                    filename=f"archive-{stamp}{_extension(original)}",
                    use_cache=True, stamp_header=ARCHIVE_STAMP_HEADER,
                    min_interval=ARCHIVE_MIN_INTERVAL,
                )
            except (SourceError, NotYetPublished) as exc:
                log.debug("CA: archived capture %s unusable (%s)", stamp, exc)
                continue
            seen += 1
            if written is None:
                log.warning("CA: capture %s kept no %s; a crawl time is not an "
                            "as-of date, so it is skipped",
                            stamp, ARCHIVE_STAMP_HEADER)
                continue
            if not (anchor - timedelta(days=WINDOW_BEFORE) <= written
                    <= anchor + timedelta(days=WINDOW_AFTER)):
                log.debug("CA: capture %s is stamped %s, outside %s's window",
                          stamp, written.isoformat(), cycle)
                continue
            # Captures are walked oldest first, so the LAST reading of a day
            # wins -- California rewrites this file more than once some days.
            by_day[written] = body

        result = FetchResult()
        days = 0
        for day in sorted(by_day):
            try:
                result.extend(parse(by_day[day], cycle, day))
            except (SchemaDrift, NotYetPublished) as exc:
                # A vintage this reader cannot honestly map -- 2022's PDFs drop
                # blank cells, see `_pdf_rows` -- or a capture of a different
                # election. Skipped, never guessed at.
                log.info("CA: archived %s skipped (%s: %s)",
                         day.isoformat(), type(exc).__name__, exc)
                continue
            days += 1
        if not days:
            raise NotYetPublished(
                f"CA: nothing readable is archived for the {cycle} general "
                f"({seen} captures read)"
            )
        log.info("CA: %d archived days for %s from %d captures",
                 days, cycle, seen)
        return result
