"""Georgia -- the Secretary of State's daily absentee/advance-voting file.

Georgia publishes one row PER ABSENTEE BALLOT APPLICATION for an election,
refreshed every morning, carrying the county, the ballot style (mail vs advance
in person), the application status, the ballot status, and -- the part that makes
this adapter shaped like North Carolina's rather than like Ohio's --
`Ballot Return Date`, the day the ballot actually came back. One download
therefore reconstructs the entire daily curve and a missed run costs nothing.

THREE THINGS ABOUT GEORGIA THAT DRIVE THIS MODULE
-------------------------------------------------

**1. Georgia has NO party registration, so every party_* field is None.**
The file *does* carry a `Party` column and it *is* populated -- but only in a
primary, where it records which party's ballot the voter asked for. In the
2026 May primary's Appling County file it reads REPUBLICAN / DEMOCRAT /
NON-PARTISAN; in the 2024 and 2026 general-election files the same column is
empty for every single row. Mapping it to `party_dem`/`party_rep` would publish
a primary's ballot-choice split as if it were general-election party
registration, and writing 0 in a general would render as "zero Democrats have
voted". Both are wrong. See THE BLANK RULE in schema.py: this column is read
only to be ignored.

**2. There are no demographics in this file.** The 38 columns are name, address,
districts and ballot lifecycle -- no age, no race, no gender (unlike the
pre-2020 layout and unlike North Carolina). So GA emits no DemoDay rows at all
rather than inventing an "unknown" bucket.

**3. The download is behind Google reCAPTCHA v3.** The old
`elections.sos.ga.gov/Elections/voterabsenteefile.do` endpoint now 301s to a
Salesforce Experience Cloud page, `https://mvp.sos.ga.gov/s/voter-absentee-files`.
Its Submit button calls a public Apex action which returns a *presigned* S3 URL,
and that Apex action rejects the call outright ("No Recaptcha Response") unless
it is handed a live reCAPTCHA v3 token. The S3 bucket itself is private -- an
unsigned GET is 403, and so is a bucket listing.

Everything except the token is plain HTTP and is implemented here:

  * `getElectionOptions` (no captcha) resolves the cycle's election to its
    auto-number, e.g. the 2026 general is `A-12601`.
  * the statewide object key is `GAVR/ABSENTEE_ZIP/<year>/<A-number>/<A-number>.zip`
    and a per-county one is `GAVR/ABSENTEE_BALLOT/<year>/<A-number>/<COUNTY>.csv`.
  * `getPublicDownloadPresignedContent` (captcha) trades an object key for a
    presigned `download_url`.

So `fetch()` needs a token in `$GA_SOS_RECAPTCHA_TOKEN`, minted immediately
before the run -- a token is single-use and lives about two minutes, so the CI
job has to run a headless browser step against the page and export the token
into the environment. Without one this adapter raises SourceError, which is the
correct signal: the data exists, we could not get it, and the ladder should fall
through to the aggregator rather than pretend Georgia has not started voting.

VERIFIED 2026-09-05 by driving the real page and downloading real files; the
fixtures under tests/fixtures/ga/ are slices of those downloads.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import os
import zipfile
from collections import defaultdict
from datetime import date, datetime

import requests

from ..calendar import election_date
from ..schema import TIER_SCRAPER, CountyDay, StateDay
from . import _fips, _net
from .base import Adapter, FetchResult, NotYetPublished, SchemaDrift, SourceError

log = logging.getLogger(__name__)

#: The public Aura endpoint behind mvp.sos.ga.gov. VERIFIED 2026-09-05: the
#: legacy elections.sos.ga.gov/Elections/voterabsenteefile.do 301s to the page
#: this endpoint serves.
PAGE = "https://mvp.sos.ga.gov/s/voter-absentee-files"
AURA = "https://mvp.sos.ga.gov/s/sfsites/aura"

#: Salesforce accepts a context this small -- verified against the live endpoint
#: with a bogus fwuid and with no fwuid at all, both of which still returned
#: SUCCESS. Pinning the real fwuid would break the day Salesforce upgrades the
#: org, so we deliberately do not send one.
AURA_CONTEXT = {"mode": "PROD", "app": "siteforce:communityApp"}

#: S3 object-key templates, read out of the page's own LWC bundle
#: (c/vrWiVoterAbsenteeFiles.handleSubmit) and confirmed by downloading both.
ZIP_KEY = "GAVR/ABSENTEE_ZIP/{year}/{number}/{number}.zip"
COUNTY_KEY = "GAVR/ABSENTEE_BALLOT/{year}/{number}/{county}.csv"

#: Where the presigned URL points. Private: an unsigned GET is 403.
BUCKET_HOST = "prod-ga-sos-vr-data-processing-bucket.s3.amazonaws.com"

#: A live reCAPTCHA v3 token for the page, minted by a browser step immediately
#: before the run. Single-use, ~2 minutes.
TOKEN_ENV = "GA_SOS_RECAPTCHA_TOKEN"

NO_TOKEN = (
    f"GA: no {TOKEN_ENV} in the environment. The Secretary of State's absentee "
    f"download is behind reCAPTCHA v3 ({PAGE}); a token must be minted by a "
    "browser step and exported before this adapter runs."
)

#: The statewide member inside the zip. It is the union of the 159 per-county
#: members (64,213 rows against 64,213 summed, in the 2026 general file), so we
#: read it and IGNORE the county members -- reading both would double every count.
STATEWIDE_MEMBER = "statewide.csv"

#: The file is valid UTF-8 today, but the only columns we read are ASCII
#: (county, dates, ballot style, ballot status) and latin-1 cannot raise on a
#: voter's name. Decoding must never be the thing that fails in October.
ENCODING = "latin-1"

#: Columns this adapter depends on. Their absence is drift, not a parse error.
REQUIRED = frozenset({
    "County", "Application Status", "Ballot Status",
    "Application Date", "Ballot Return Date", "Ballot Style",
})

#: Georgia's own `Ballot Style` vocabulary, verified across the 2024 general,
#: the 2026 primary and the 2026 general files. `normalize.method()` already
#: knows "absentee by mail"; it does NOT know Georgia's hyphenated
#: "EARLY IN-PERSON" or its UOCAVA "ELECTRONIC BALLOT DELIVERY", so those two
#: are aliased here -- in the adapter, next to the evidence -- rather than by
#: widening the shared vocabulary on one state's say-so.
STYLE_ALIASES = {
    "early in-person": "in person",
    # A UOCAVA ballot transmitted electronically and returned by mail/email. It
    # is not cast at an advance-voting site, so it belongs in the mail bucket.
    "electronic ballot delivery": "mail",
}

#: Only an ACCEPTED ballot counts as cast. The file also carries I (issued, not
#: back yet), C (cancelled -- surrendered to vote in person, undeliverable,
#: administrative) and R (rejected -- e.g. received after the deadline), all of
#: which have or may have a return date and none of which is a vote.
BALLOT_ACCEPTED = "A"

#: An application Georgia accepted. R is rejected, blank is cure-pending.
APPLICATION_ACCEPTED = "A"

MIN_BYTES = 2048


def _norm(raw: str | None) -> str:
    return " ".join(str(raw or "").strip().lower().split())


def _parse_day(raw: str | None) -> date | None:
    text = (raw or "").strip()
    if not text:
        return None
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise SchemaDrift(f"GA: {raw!r} is not a date")


def ballot_method(style: str | None) -> str:
    """Map a `Ballot Style` onto normalize's METHOD_MAIL/METHOD_INPERSON.

    Raises SchemaDrift for a style neither Georgia's alias table nor the shared
    vocabulary recognises -- a new style silently bucketed as mail would move
    thousands of in-person ballots into the wrong series.
    """
    from ..normalize import method as _method

    key = _norm(style)
    bucket = _method(STYLE_ALIASES.get(key, key))
    if bucket is None:
        raise SchemaDrift(f"GA: unrecognised Ballot Style {style!r}")
    return bucket


class _Bucket:
    """Per-day tallies for one geography."""

    __slots__ = ("total", "mail", "inperson", "requested")

    def __init__(self) -> None:
        self.total = 0
        self.mail = 0
        self.inperson = 0
        self.requested = 0


def _statewide_csv(body: bytes) -> str:
    """The statewide CSV text, whether `body` is the zip or a bare county file."""
    if body[:2] == b"PK":
        try:
            archive = zipfile.ZipFile(io.BytesIO(body))
        except zipfile.BadZipFile as exc:
            raise SchemaDrift("GA: absentee download is not a readable zip") from exc
        members = {name.lower(): name for name in archive.namelist()}
        member = members.get(STATEWIDE_MEMBER)
        if member is None:
            raise SchemaDrift(
                "GA: no STATEWIDE.csv inside the absentee zip "
                f"({sorted(members)[:5]})"
            )
        return archive.read(member).decode(ENCODING)
    if _net.looks_like_html(body):
        raise SourceError("GA: absentee download came back as HTML, not a data file")
    return body.decode(ENCODING)


def parse(body: bytes, cycle: int, as_of: date) -> FetchResult:
    """Parse one Georgia absentee download into canonical rows.

    Raises NotYetPublished when the file exists but Georgia has nothing to
    report yet -- no accepted mail application and no returned ballot on or
    before `as_of`. That is the normal state of the file for months: the 2026
    general zip already existed on 2026-09-05 with 62,530 accepted applications
    and not one returned ballot.
    """
    reader = csv.DictReader(io.StringIO(_statewide_csv(body)))
    missing = REQUIRED - set(reader.fieldnames or [])
    if missing:
        raise SchemaDrift(f"GA: absentee file missing columns {sorted(missing)}")

    by_state: dict[date, _Bucket] = defaultdict(_Bucket)
    by_county: dict[tuple[str, date], _Bucket] = defaultdict(_Bucket)
    county_names: dict[str, str] = {}
    unknown: set[str] = set()

    for row in reader:
        hit = _fips.lookup("GA", row.get("County"))
        if hit is None:
            unknown.add((row.get("County") or "").strip())
            continue
        fips, canonical = hit
        county_names[fips] = canonical

        method = ballot_method(row.get("Ballot Style"))

        # A mail ballot Georgia agreed to send. Counted on its APPLICATION date,
        # which is a different series from the return date below -- it is the
        # only Georgia number that exists at all before advance voting opens.
        applied = _parse_day(row.get("Application Date"))
        if (
            method == "mail"
            and (row.get("Application Status") or "").strip().upper()
            == APPLICATION_ACCEPTED
            and applied is not None
            and applied <= as_of
        ):
            by_state[applied].requested += 1
            by_county[(fips, applied)].requested += 1

        if (row.get("Ballot Status") or "").strip().upper() != BALLOT_ACCEPTED:
            continue
        returned = _parse_day(row.get("Ballot Return Date"))
        if returned is None or returned > as_of:
            continue

        for bucket in (by_state[returned], by_county[(fips, returned)]):
            bucket.total += 1
            if method == "mail":
                bucket.mail += 1
            else:
                bucket.inperson += 1

    if unknown:
        # Georgia has exactly 159 counties and they do not change. A name we
        # cannot place is a county that would silently vanish off the map.
        raise SchemaDrift(f"GA: unrecognised county names {sorted(unknown)[:5]}")

    if not by_state:
        raise NotYetPublished(
            f"GA: absentee file has no accepted application or returned ballot "
            f"on or before {as_of.isoformat()}"
        )

    return _emit(by_state, by_county, county_names, cycle, as_of)


def _span(start: date, end: date) -> list[date]:
    days = [start]
    while days[-1] < end:
        days.append(date.fromordinal(days[-1].toordinal() + 1))
    return days


def _emit(by_state, by_county, county_names, cycle: int, as_of: date) -> FetchResult:
    """Cumulative daily rows on a continuous axis, so the page never interpolates."""
    result = FetchResult()
    span = _span(min(by_state), as_of)

    running = _Bucket()
    for day in span:
        today = by_state.get(day)
        if today:
            running.total += today.total
            running.mail += today.mail
            running.inperson += today.inperson
            running.requested += today.requested
        result.state_rows.append(StateDay(
            cycle=cycle, state="GA", day=day,
            # Georgia's file carries every ballot for the election, so before the
            # first return these really are zero ballots cast -- not "unreported".
            ballots_total=running.total,
            ballots_new=today.total if today else 0,
            mail_requested=running.requested,
            mail_returned=running.mail,
            inperson=running.inperson,
            # GEORGIA HAS NO PARTY REGISTRATION. The file's `Party` column is the
            # primary ballot a voter asked for and is empty in a general; a 0
            # here would render as "zero Democrats have voted". Never a number.
            party_dem=None, party_rep=None, party_oth=None, party_npa=None,
        ))

    for fips in sorted({f for f, _ in by_county}):
        running = _Bucket()
        for day in span:
            today = by_county.get((fips, day))
            if today:
                running.total += today.total
                running.mail += today.mail
                running.inperson += today.inperson
            if running.total == 0:
                # CountyDay has no mail_requested field, so a county with only
                # outstanding applications has nothing to say yet.
                continue
            result.county_rows.append(CountyDay(
                cycle=cycle, state="GA", county_fips=fips, day=day,
                county_name=county_names.get(fips, ""),
                ballots_total=running.total,
                ballots_new=today.total if today else 0,
                mail_returned=running.mail,
                inperson=running.inperson,
                party_dem=None, party_rep=None, party_oth=None, party_npa=None,
            ))

    # No DemoDay rows: the 38-column file carries no age, race or gender.
    return result


class GAScraper(Adapter):
    """Tier 1 for Georgia: the SoS voter absentee file."""

    state = "GA"
    name = "ga-sos"
    tier = TIER_SCRAPER

    # ------------------------------------------------------------------
    # The public Aura plumbing
    # ------------------------------------------------------------------
    def _apex(self, cls: str, method: str, params: dict) -> dict:
        """Call one public Apex action on mvp.sos.ga.gov and return its action."""
        message = {"actions": [{
            "id": "1;a",
            "descriptor": "aura://ApexActionController/ACTION$execute",
            "callingDescriptor": "UNKNOWN",
            "params": {
                "namespace": "", "classname": cls, "method": method,
                "params": params, "cacheable": False, "isContinuation": False,
            },
        }]}
        try:
            response = requests.post(
                f"{AURA}?r=1&aura.ApexAction.execute=1",
                data={
                    "message": json.dumps(message),
                    "aura.context": json.dumps(AURA_CONTEXT),
                    "aura.pageURI": "/s/voter-absentee-files",
                    "aura.token": "null",
                },
                headers={
                    **_net.DEFAULT_HEADERS,
                    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                    "Referer": PAGE,
                    "Origin": "https://mvp.sos.ga.gov",
                },
                timeout=_net.DEFAULT_TIMEOUT,
            )
        except requests.RequestException as exc:
            raise SourceError(f"GA: {cls}.{method} request failed: {exc}") from exc

        if not response.ok:
            raise SourceError(f"GA: {cls}.{method} returned HTTP {response.status_code}")
        body = response.text
        if body.startswith("*/"):
            # Salesforce's shape for "no such action" -- the org changed under us.
            raise SchemaDrift(f"GA: {cls}.{method} is gone ({body[:120]})")
        try:
            actions = response.json()["actions"]
        except (ValueError, KeyError, TypeError) as exc:
            raise SchemaDrift(f"GA: {cls}.{method} returned {body[:120]!r}") from exc
        if not actions:
            raise SchemaDrift(f"GA: {cls}.{method} returned no actions")
        return actions[0]

    def election_number(self, cycle: int) -> str:
        """The SoS auto-number for the cycle's statewide general, e.g. "A-12601".

        Matched on the election DATE, never on its name -- Georgia has called it
        "11/08/2022 GENERAL/SPECIAL ELECTION", "NOVEMBER 5, 2024 - GENERAL
        ELECTION" and "NOVEMBER 3, 2026 - GENERAL & SPECIAL ELECTIONS" in three
        consecutive cycles, and no name match survives that.

        The date alone is not always unique, though: on 2022-11-08 Georgia also
        lists the CITY OF SASSER general and the WARWICK special, plus a
        duplicate of the statewide general itself. The tiebreak is county
        coverage, which the same payload already carries -- a municipal election
        offers no counties, and the statewide general offers all 159. Filtering
        on `electionCategory` instead would work for 2024 and 2026 and return
        nothing at all for 2022, whose records are not categorised the same way.
        """
        action = self._apex(
            "vrWebIntegrationController", "getElectionOptions",
            {"year": str(cycle), "electionCategory": "", "county": "",
             "isAdvVoterHistory": False},
        )
        if action.get("state") != "SUCCESS":
            raise SourceError(f"GA: getElectionOptions failed: {action.get('error')}")
        try:
            payload = action["returnValue"]["returnValue"]
            elections = payload["election"]
        except (KeyError, TypeError) as exc:
            raise SchemaDrift("GA: getElectionOptions has no election list") from exc

        wanted = election_date(cycle).isoformat()
        candidates = []
        for entry in elections:
            if not isinstance(entry, dict):
                raise SchemaDrift(f"GA: election entry is {type(entry).__name__}")
            if entry.get("electionDate") != wanted:
                continue
            number = (entry.get("name") or "").strip()
            if not number:
                raise SchemaDrift(f"GA: election on {wanted} has no auto-number")
            counties = payload.get(entry.get("electionId")) or []
            candidates.append((len(counties), number))

        if not candidates:
            raise NotYetPublished(f"GA: no election dated {wanted} is listed yet")

        # Widest coverage wins; the lowest auto-number breaks a genuine tie,
        # because Georgia issues the real statewide record first and its
        # duplicates later.
        candidates.sort(key=lambda c: c[1])
        coverage, number = max(candidates, key=lambda c: c[0])
        if coverage < len(_fips.names("GA")):
            raise SchemaDrift(
                f"GA: the widest election on {wanted} ({number}) covers only "
                f"{coverage} counties, not all {len(_fips.names('GA'))}"
            )
        if len(candidates) > 1:
            log.info("GA: %d elections dated %s; chose %s", len(candidates), wanted, number)
        return number

    def presigned_url(self, object_key: str, token: str) -> str:
        """Trade an S3 object key for a presigned download URL."""
        action = self._apex(
            "VrMvpUtility", "getPublicDownloadPresignedContent",
            {"fileName": object_key,
             "recaptchaResponse": json.dumps({"response": token, "action": "Submit"}),
             "version": "V3"},
        )
        if action.get("state") != "SUCCESS":
            messages = [e.get("message", "") for e in action.get("error") or []]
            raise SourceError(f"GA: presign of {object_key} failed: {messages}")
        try:
            payload = json.loads(json.loads(action["returnValue"]["returnValue"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise SchemaDrift("GA: presign response was not the expected JSON") from exc
        if payload.get("message") == "No Data Found":
            raise NotYetPublished(f"GA: {object_key} has not been published yet")
        url = payload.get("download_url")
        if not url:
            raise SchemaDrift(f"GA: presign response carried no download_url: {payload}")
        return url

    # ------------------------------------------------------------------
    def _cache_name(self, cycle: int, number: str) -> str:
        return f"{cycle}_{number}_absentee.zip"

    def _download(self, cycle: int, number: str, *, allow_cache: bool) -> bytes:
        filename = self._cache_name(cycle, number)
        token = os.environ.get(TOKEN_ENV, "").strip()
        if token:
            url = self.presigned_url(
                ZIP_KEY.format(year=cycle, number=number), token
            )
            try:
                return _net.get(url, state=self.state, filename=filename,
                                min_bytes=MIN_BYTES)
            except _net.Missing as exc:
                raise NotYetPublished(f"GA: {number} absentee zip is not posted") from exc

        if allow_cache:
            path = _net.cache_path(self.state, filename)
            if path.exists() and path.stat().st_size >= MIN_BYTES:
                log.debug("GA: serving archived %s from cache", filename)
                return path.read_bytes()

        raise SourceError(NO_TOKEN)

    # ------------------------------------------------------------------
    def fetch(self, cycle: int, as_of: date) -> FetchResult:
        number = self.election_number(cycle)
        return parse(self._download(cycle, number, allow_cache=False), cycle, as_of)

    def fetch_history(self, cycle: int) -> FetchResult:
        """A past cycle's file, which still carries every ballot's return date.

        Georgia keeps the archive keyed by election, so one download rebuilds the
        whole 2022 or 2024 daily curve -- there is no per-day archive to hunt.
        """
        if cycle >= datetime.now().year:
            raise NotYetPublished(f"GA: {cycle} is not an archived cycle")
        number = self.election_number(cycle)
        body = self._download(cycle, number, allow_cache=True)
        return parse(body, cycle, election_date(cycle))
