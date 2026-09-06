"""Virginia: ELECT's raw absentee export, published through the ENR system.

Virginia reports early voting by LOCALITY -- 95 counties plus 38 independent
cities, 133 in all -- as one long CSV with a row per
`locality x congressional district x age x gender x reason x application type x
receipt type` and a count. The Department of Elections refreshes it through the
45-day early-voting window and then freezes it as the certified file.

**The independent cities are the whole difficulty.** Fairfax city (51600) is not
Fairfax County (51059); the same trap is set for Richmond, Franklin and Roanoke,
and `_fips.lookup` deliberately refuses a bare "FAIRFAX" rather than guess. It
does not have to guess here, because ELECT always writes the disambiguating word:
the file says "FAIRFAX CITY" and "FAIRFAX COUNTY" on separate rows, which
`_fips.lookup` resolves exactly. A locality it cannot place is SchemaDrift -- an
unplaced Virginia locality is not a rounding error, it is either a whole city
missing from the map or, far worse, a city's ballots credited to its county.

The one spelling ELECT uses that the census table does not is "KING & QUEEN
COUNTY" for "King and Queen County". That is an ampersand, not an ambiguity, so
it is translated explicitly in LOCALITY_SPELLINGS below and nowhere else.

**Method comes from ApplicationType, not ReceiptType.** A Virginia early
in-person voter applies and votes at the registrar's office in one motion and is
recorded with ApplicationType "In Person"; that column reproduces ELECT's own
published in-person early-vote total (1,858,721 for the 2024 general). ReceiptType
says how a ballot came BACK, so a mailed ballot handed in at the office reads
"In Person" there while remaining a mail ballot -- bucketing on it would move
about 5,000 mail ballots into the in-person headline. A blank ReceiptType means
the ballot was issued and has not come back yet, which is `mail_requested` minus
`mail_returned`, never a zero.

Both column vocabularies are whitelisted. A new ELECT application form would
otherwise fall silently into the mail bucket, so an unrecognised label raises
SchemaDrift and the ladder drops to the aggregator for the day.

The file also carries AgeRange and Gender. Gender maps cleanly onto
`normalize.sex`, but ELECT's age bands ("18 to 25", "26 to 40", "41 to 60",
"61 to 70", "Over 70") do not line up with `normalize.AGE_BANDS` at any boundary
-- "41 to 60" would land in "35-44" -- so no demographic rows are emitted here
rather than published wrong.

Virginia does not register voters by party, so every party_* field is None. See
THE BLANK RULE in schema.py.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import urllib.parse
from collections import defaultdict
from datetime import date, datetime, timezone

from ..calendar import election_date
from ..schema import TIER_SCRAPER, CountyDay, StateDay
from . import _fips
from ._net import Missing, get, looks_like_html
from .base import Adapter, FetchResult, NotYetPublished, SchemaDrift, SourceError

log = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Endpoints. All three VERIFIED live against the 2024 November General.
# --------------------------------------------------------------------------
ENR = "https://enr.elections.virginia.gov"

#: Lists Virginia's ENR jurisdiction id and every election it publishes, so the
#: cycle's election key is discovered rather than guessed. The 2026 general is
#: simply absent from this list until ELECT stands it up -- that absence is the
#: NotYetPublished signal, and it is the normal state of the world until autumn.
JURISDICTION_URL = f"{ENR}/results/public/api/jurisdictions/Virginia"

#: One election's metadata, including its downloadable public reports.
ELECTION_URL = ENR + "/results/public/api/elections/Virginia/{election}"

#: The report itself. `blob` changes on every republish, which is exactly why it
#: is read out of the election metadata instead of being pinned here.
BLOB_URL = ENR + "/cdn/results/{jurisdiction}/{blob}"

#: The report we want, matched case-insensitively on `reportName`.
ABSENTEE_REPORT = "enrabsenteerawcsv"

#: Virginia's PREVIOUS election-night system, retired for elections after 2022
#: but still serving its archived files. Same eight columns, byte for byte.
#: VERIFIED live for the 2022 November General.
LEGACY_URL = ("https://results.elections.virginia.gov/vaelections/"
              "{election}/Site/Statistics/Absentee.csv")

#: Cycle -> the legacy system's folder name. Only cycles that predate the ENR
#: cutover appear here; everything later is served by the API above.
LEGACY_ELECTIONS = {2022: "2022 November General"}

# --------------------------------------------------------------------------
# The CSV
# --------------------------------------------------------------------------
LOCALITY = "localityname"
APPLICATION = "applicationtype"
RECEIPT = "receipttype"
COUNT = "thecount"

REQUIRED = (LOCALITY, APPLICATION, RECEIPT, COUNT)

#: ApplicationType for a voter who applied and voted at the registrar's office.
INPERSON_APPLICATION = "in person"

#: Every other ApplicationType ELECT has written in the 2022 and 2024 files.
#: These are all ballots SENT to a voter; a new one here is SchemaDrift, because
#: defaulting an unknown form to "mail" is exactly the silent mis-bucketing that
#: rule 3 in CLAUDE.md exists to prevent.
MAIL_APPLICATIONS = frozenset({
    "annual absentee application - sbe 703.1",
    "business/personal/medical emergency application - sbe 705.1-705.2",
    "emergency application - sbe 705",
    "federal post cards application - fpca",
    "national/state emergency",
    "permanent absentee application - sbe 703.1",
    "temporary change of info - sbe 703.1c",
    "virginia specific application - sbe 701",
    "write-in absentee ballot, federal - fwab",
})

#: A non-blank ReceiptType means the ballot came back, by whichever channel.
#: Blank means issued and still outstanding -- not zero, and not a return.
RETURNED_RECEIPTS = frozenset({
    "designated representative", "drop off", "in person",
    "mail", "mail (non-usps)",
})

#: Source spellings the census name table does not carry. ELECT writes an
#: ampersand where the census writes the word. This is a spelling difference,
#: not an ambiguity, so translating it is safe -- and note what is NOT here:
#: bare "FAIRFAX"/"RICHMOND"/"FRANKLIN"/"ROANOKE" stay unresolvable on purpose.
LOCALITY_SPELLINGS = {
    "king & queen county": "King and Queen County",
}

#: Virginia's full complement of localities. A file carrying fewer than this is
#: a partial export, and summing it would understate the state.
EXPECTED_LOCALITIES = len(_fips.CENSUS_COUNTIES["VA"])


def _key(raw) -> str:
    """Lowercase, collapse whitespace, and fold the en dash ELECT uses in FPCA."""
    text = " ".join(str(raw or "").strip().lower().split())
    return text.replace("–", "-").replace("—", "-")


def _count(value) -> int:
    """A count. Virginia writes one on every row; a blank here is a broken row."""
    text = str(value or "").strip().replace(",", "")
    if not text:
        raise SchemaDrift("VA: absentee row carries no count")
    try:
        return int(float(text))
    except ValueError as exc:
        raise SchemaDrift(f"VA: {value!r} is not a count") from exc


def _add(*values: int | None) -> int | None:
    """Sum the reported parts of a measure, or None if none was reported."""
    known = [v for v in values if v is not None]
    return sum(known) if known else None


class _Tally:
    """One locality's running totals, kept as None until something is reported."""

    __slots__ = ("inperson", "mail_sent", "mail_returned")

    def __init__(self) -> None:
        self.inperson: int | None = None
        self.mail_sent: int | None = None
        self.mail_returned: int | None = None

    def add(self, field: str, n: int) -> None:
        setattr(self, field, (getattr(self, field) or 0) + n)


def parse(body: bytes, cycle: int, day: date) -> FetchResult:
    """Parse one ELECT absentee export into canonical rows.

    `day` is supplied by the caller because the file itself carries no as-of
    date -- ELECT publishes the timestamp beside the file, not inside it.
    """
    if looks_like_html(body):
        raise SourceError("VA: absentee export came back as HTML, not CSV")

    text = body.decode("utf-8-sig", errors="replace")
    reader = csv.reader(io.StringIO(text))
    try:
        header = [_key(c) for c in next(reader)]
    except StopIteration as exc:
        raise SchemaDrift("VA: absentee export is empty") from exc

    index = {name: i for i, name in enumerate(header) if name}
    missing = [c for c in REQUIRED if c not in index]
    if missing:
        raise SchemaDrift(f"VA: absentee export is missing columns {missing}")

    tallies: dict[str, _Tally] = defaultdict(_Tally)
    names: dict[str, str] = {}
    unknown_locality: set[str] = set()
    unknown_application: set[str] = set()
    unknown_receipt: set[str] = set()

    for row in reader:
        if not row or len(row) <= max(index[c] for c in REQUIRED):
            continue
        raw_locality = " ".join(str(row[index[LOCALITY]] or "").split())
        if not raw_locality:
            continue

        hit = _fips.lookup("VA", LOCALITY_SPELLINGS.get(_key(raw_locality), raw_locality))
        if hit is None:
            # Either a name we do not know or -- the dangerous case -- a bare
            # "FAIRFAX" that could be the city or the county. Never guess.
            unknown_locality.add(raw_locality)
            continue

        application = _key(row[index[APPLICATION]])
        receipt = _key(row[index[RECEIPT]])
        if application != INPERSON_APPLICATION and application not in MAIL_APPLICATIONS:
            unknown_application.add(application)
            continue
        if receipt and receipt not in RETURNED_RECEIPTS:
            unknown_receipt.add(receipt)
            continue

        fips, canonical = hit
        names[fips] = canonical
        tally = tallies[fips]
        n = _count(row[index[COUNT]])
        if application == INPERSON_APPLICATION:
            tally.add("inperson", n)
        else:
            tally.add("mail_sent", n)
            if receipt:
                tally.add("mail_returned", n)

    if unknown_locality:
        raise SchemaDrift(f"VA: unresolved localities {sorted(unknown_locality)[:5]}")
    if unknown_application:
        raise SchemaDrift(f"VA: unknown application types {sorted(unknown_application)[:5]}")
    if unknown_receipt:
        raise SchemaDrift(f"VA: unknown receipt types {sorted(unknown_receipt)[:5]}")
    if not tallies:
        raise SchemaDrift("VA: absentee export produced no locality rows")

    county_rows = [
        CountyDay(
            cycle=cycle, state="VA", county_fips=fips, day=day,
            county_name=names[fips],
            ballots_total=_add(tally.inperson, tally.mail_returned),
            mail_returned=tally.mail_returned,
            inperson=tally.inperson,
            # Virginia has no party registration. Never 0.
            party_dem=None, party_rep=None, party_oth=None, party_npa=None,
        )
        for fips, tally in sorted(tallies.items())
    ]

    state_rows: list[StateDay] = []
    if len(county_rows) == EXPECTED_LOCALITIES:
        # ELECT publishes no statewide row, so the state total can only be our
        # sum -- which is a Virginia total ONLY when all 133 localities are in
        # the file. A partial export sums to a number that looks like Virginia
        # and is not, so it is published as counties and nothing else.
        state_rows.append(StateDay(
            cycle=cycle, state="VA", day=day,
            ballots_total=_add(*(r.ballots_total for r in county_rows)),
            mail_requested=_add(*(t.mail_sent for t in tallies.values())),
            mail_returned=_add(*(r.mail_returned for r in county_rows)),
            inperson=_add(*(r.inperson for r in county_rows)),
            party_dem=None, party_rep=None, party_oth=None, party_npa=None,
        ))
    else:
        log.warning(
            "VA: export covers %d of %d localities; publishing counties only",
            len(county_rows), EXPECTED_LOCALITIES,
        )

    return FetchResult(state_rows=state_rows, county_rows=county_rows)


def published_date(election: dict) -> date | None:
    """The instant ELECT last refreshed this election's reports, as a UTC date."""
    for field in ("asOf", "lastUpdated"):
        raw = election.get(field)
        if not raw:
            continue
        try:
            stamp = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            continue
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return stamp.astimezone(timezone.utc).date()
    return None


def absentee_blob(election: dict) -> str | None:
    """The absentee export's blob name, or None if this election has no report."""
    for category in election.get("publicReportCategories") or []:
        for report in category.get("reports") or []:
            if _key(report.get("reportName")) == ABSENTEE_REPORT:
                blob = report.get("blobName")
                if blob:
                    return str(blob)
    return None


class VAScraper(Adapter):
    """Tier 1 for Virginia: ELECT's absentee export, by locality."""

    state = "VA"
    name = "va-elect"
    tier = TIER_SCRAPER

    # -- discovery ---------------------------------------------------------
    def _json(self, url: str, *, filename: str, use_cache: bool) -> dict:
        try:
            body = get(url, state="VA", filename=filename, use_cache=use_cache)
        except Missing as exc:
            raise NotYetPublished(f"VA: {exc}") from exc
        try:
            return json.loads(body.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SourceError(f"VA: {url} did not return JSON: {exc}") from exc

    def _election_key(self, cycle: int, *, use_cache: bool) -> tuple[str, str]:
        """(jurisdiction id, public election id) for the cycle's November general."""
        jurisdiction = self._json(
            JURISDICTION_URL, filename=f"{cycle}_jurisdiction.json", use_cache=use_cache
        )
        jurisdiction_id = jurisdiction.get("id")
        if not jurisdiction_id:
            raise SchemaDrift("VA: jurisdiction feed carries no id")

        wanted = election_date(cycle).isoformat()
        matches = [
            e for e in jurisdiction.get("elections") or []
            if str(e.get("electionDate") or "") == wanted and e.get("publicElectionId")
        ]
        if not matches:
            raise NotYetPublished(
                f"VA: ELECT has not stood up the {wanted} general yet"
            )
        if len(matches) > 1:
            raise SchemaDrift(
                f"VA: {wanted} matches {len(matches)} elections "
                f"{[m.get('publicElectionId') for m in matches]}"
            )
        return str(jurisdiction_id), str(matches[0]["publicElectionId"])

    # -- loading -----------------------------------------------------------
    def _load_current(self, cycle: int, *, use_cache: bool) -> tuple[bytes, date | None]:
        jurisdiction_id, key = self._election_key(cycle, use_cache=use_cache)
        election = self._json(
            ELECTION_URL.format(election=urllib.parse.quote(key)),
            filename=f"{cycle}_election.json", use_cache=use_cache,
        )
        blob = absentee_blob(election)
        if blob is None:
            # The election exists but early voting has not produced a report.
            raise NotYetPublished(f"VA: {key} has no absentee export yet")
        try:
            body = get(
                BLOB_URL.format(jurisdiction=jurisdiction_id,
                                blob=urllib.parse.quote(blob)),
                state="VA", filename=f"{cycle}_{blob}",
                use_cache=use_cache, min_bytes=1024,
            )
        except Missing as exc:
            raise NotYetPublished(f"VA: absentee export not posted yet ({exc})") from exc
        if looks_like_html(body):
            raise NotYetPublished("VA: absentee export came back as HTML, not CSV")
        return body, published_date(election)

    def _load_legacy(self, cycle: int) -> bytes:
        folder = LEGACY_ELECTIONS[cycle]
        try:
            body = get(
                LEGACY_URL.format(election=urllib.parse.quote(folder)),
                state="VA", filename=f"{cycle}_legacy_absentee.csv",
                use_cache=True, min_bytes=1024,
            )
        except Missing as exc:
            raise NotYetPublished(f"VA: no archived {folder} absentee file ({exc})") from exc
        if looks_like_html(body):
            # The retired system answers a missing file with a styled 200 page.
            raise NotYetPublished(f"VA: archived {folder} absentee file is gone")
        return body

    # -- the contract ------------------------------------------------------
    def fetch(self, cycle: int, as_of: date) -> FetchResult:
        body, published = self._load_current(cycle, use_cache=False)
        day = as_of if published is None or published > as_of else published
        return parse(body, cycle, day)

    def fetch_history(self, cycle: int) -> FetchResult:
        """The archived export for a past cycle -- one certified snapshot.

        ELECT overwrites the export in place rather than keeping a file per day,
        so there is no daily series to recover. What survives is the cycle's
        final absentee position, which is what the 2022/2024 comparison lines
        anchor on, and it is dated to Election Day rather than to whenever ELECT
        last touched the file (months later, after certification).
        """
        if cycle >= date.today().year:
            raise NotYetPublished(f"VA: {cycle} is not an archived cycle")
        day = election_date(cycle)
        if cycle in LEGACY_ELECTIONS:
            return parse(self._load_legacy(cycle), cycle, day)
        body, _ = self._load_current(cycle, use_cache=True)
        return parse(body, cycle, day)
