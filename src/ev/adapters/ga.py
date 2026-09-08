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

**3. GEORGIA CANNOT BE COLLECTED UNATTENDED.** This is a finding, not a TODO.
The old `elections.sos.ga.gov/Elections/voterabsenteefile.do` endpoint now 301s
to a Salesforce Experience Cloud page,
`https://mvp.sos.ga.gov/s/voter-absentee-files`. Its Submit button calls a public
Apex action that returns a *presigned* S3 URL, and that action refuses every
request that does not carry a live reCAPTCHA Enterprise token. There is no
unprotected sibling: the URL it hands back is presigned, and the bucket behind it
answers 403 to an object GET and to a listing alike. (The Aura endpoints below
are NOT blocked -- Cloudflare challenges the sos.ga.gov CMS, not this API.)
`docs/georgia-source.md` records every Georgia source checked on 2026-09-06 and
what each returned.

Everything EXCEPT the token is plain HTTP and is implemented here:

  * `vrWebIntegrationController.getElectionOptions` (no captcha) resolves the
    cycle's election to its auto-number, e.g. the 2026 general is `A-12601`.
  * the statewide object key is `GAVR/ABSENTEE_ZIP/<year>/<A-number>/<A-number>.zip`
    and a per-county one is `GAVR/ABSENTEE_BALLOT/<year>/<A-number>/<COUNTY>.csv`.
  * `VrMvpUtility.getRecaptchaDetails` (no captcha) reports the SoS's OWN switch
    for the gate -- see `bot_check_active()`.
  * `VrMvpUtility.getPublicDownloadPresignedContent` (captcha) trades an object
    key for a presigned `download_url`.

So the only way to collect Georgia is to put a token in `$GA_SOS_RECAPTCHA_TOKEN`
immediately before the run: it is single-use and lives about two minutes, so a
human or a browser step has to mint it. That does not survive a nightly GitHub
Actions job, so in CI this adapter raises SourceError -- which is the correct
signal, not a failure to try. The data exists, we could not get it, and the
ladder falls through to the aggregator (which does carry Georgia) rather than
pretending Georgia has not started voting. Raising NotYetPublished here would be
the real bug: it would stop the ladder and blank the state out entirely.

A SCRIPTED BROWSER DOES NOT GET PAST THIS. Tested 2026-09-06, so that nobody
spends another day on it. Playwright Chromium loads the page, reCAPTCHA
Enterprise initialises, and `grecaptcha.enterprise.execute()` happily returns a
~2.3 kB token in under a second -- HEADLESS AND HEADED ALIKE. Both tokens were
then posted to the presign in the page's exact shape (see `recaptcha_params`,
which was read out of the page's own inline script) and both came back
`ERROR "V3 Recaptcha Failed"`. So the token mints fine and the SERVER-SIDE
Enterprise assessment is what refuses it: a fresh, profile-less, automated
browser scores below Georgia's threshold. That is a bot check working as
designed, and it would score WORSE on a GitHub Actions runner -- datacenter IP,
throwaway profile, no history -- not better. Note the page's own "automation"
check is unrelated and is pure theatre: it is literally
`new CustomEvent('automationDetected', {detail: window.navigator.webdriver})`,
evaluated in the browser, and it never reaches the server.

Georgia's kill switch is the remaining automatic way out, but it is WEAKER than
it looks. The page asks `getRecaptchaDetails` on every load and gets back
`Active__c` / `Bot_Check_Active__c`; both were true on 2026-09-06. Those flags
only steer the PAGE: `verifyRecaptcha()` skips straight to `handleZipFile("",
"", fileName)` when `Active__c` is false. The Apex action does not consult them
-- posting that same tokenless shape while the switch is on answers
`ERROR "Missing necessary information to handle the request."`, not a download.
So the switch coming down is NECESSARY but not demonstrably SUFFICIENT.
`_download` still tries, because trying costs one request and the alternative is
never noticing; it is written as an ATTEMPT, never an assumption, and a refusal
ends in the same SourceError.

VERIFIED 2026-09-05/06 by driving the real page and downloading real files; the
fixtures under tests/fixtures/ga/ are slices of those downloads and a verbatim
capture of the live `getRecaptchaDetails` response.
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

#: A live reCAPTCHA token for the page, minted by a browser step immediately
#: before the run. Single-use, ~2 minutes.
TOKEN_ENV = "GA_SOS_RECAPTCHA_TOKEN"

#: The reCAPTCHA ENTERPRISE site key the page renders with. VERIFIED 2026-09-06
#: from the live page's own
#: <script src="https://www.google.com/recaptcha/enterprise.js?render=...">.
#: Recorded so a future reader can tell at a glance that this is Enterprise and
#: not classic v3 -- the adapter never calls Google itself.
RECAPTCHA_SITE_KEY = "6LdUOgYfAAAAAGDYBY939FbeWV3bL-Ktw2EKMoua"

#: The action name minted with the token. VERIFIED 2026-09-06 by reading the
#: inline script on the live page:
#:
#:     grecaptcha.enterprise.execute(
#:         '6LdU...', {action: 'Submit'}).then(function(token) {
#:         document.dispatchEvent(new CustomEvent('grecaptchaVerified',
#:             {'detail': {response: token, action: 'Submit'}})); });
#:
#: reCAPTCHA Enterprise binds the action into the token and an assessment can
#: reject a mismatch, so this string is not decorative.
RECAPTCHA_ACTION = "Submit"

#: `version` values the Apex action branches on, from the same component
#: (`c/vrWiVoterAbsenteeFiles.handleZipFile(token, version, fileName)`).
#: With a token the page passes "V3"; with the gate down it passes "".
VERSION_V3 = "V3"
VERSION_NONE = ""

#: The SoS's own switches for the gate, returned by
#: VrMvpUtility.getRecaptchaDetails. VERIFIED 2026-09-06: both are `true`.
#: A field we have never seen counts as "the gate is up" -- see bot_check_active.
BOT_CHECK_FIELDS = ("Active__c", "Bot_Check_Active__c")

#: The message that goes out when Georgia cannot be collected. It has to say
#: plainly that this is a property of the source and not a bug in the run,
#: because it is what a maintainer sees in the CI log every single night.
UNATTENDED = (
    "GA: Georgia cannot be collected unattended. The Secretary of State's only "
    "machine-readable absentee/advance-voting file is gated by reCAPTCHA "
    f"Enterprise on {PAGE}: the presign action refuses every request without a "
    "live browser-minted token, the S3 bucket behind it is private, and no "
    "unprotected sibling or alternative statewide feed exists (see "
    f"docs/georgia-source.md for what was checked). Export {TOKEN_ENV} from a "
    "browser step to collect Georgia by hand; otherwise this must fall through "
    "to the aggregator, which does carry Georgia."
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


def recaptcha_params(token: str) -> dict[str, str]:
    """The `recaptchaResponse` / `version` pair for a presign call.

    Mirrors `c/vrWiVoterAbsenteeFiles.handleZipFile(token, version, fileName)`,
    which posts `{recaptchaResponse: JSON.stringify(token), version: version}`
    where `token` is the whole `grecaptchaVerified` detail object, not the bare
    string. So the wire shape with a token is::

        {"recaptchaResponse": '{"response": "03AF...", "action": "Submit"}',
         "version": "V3"}

    and the shape the page falls back to when the Secretary of State's own
    `Active__c` switch is OFF -- `handleZipFile("", "", fileName)` -- is::

        {"recaptchaResponse": '""', "version": ""}

    VERIFIED 2026-09-06, both replayed against the live action:

    ==========================================  ==========================================
    sent                                        answered
    ==========================================  ==========================================
    the token shape above, real browser token    ERROR "V3 Recaptcha Failed"
    the tokenless shape above                    ERROR "Missing necessary information ..."
    ``recaptchaResponse`` omitted                ERROR "No Recaptcha Response"
    ==========================================  ==========================================

    The middle row is the important one and it is why this function exists: the
    tokenless branch now sends what the SoS's own front end sends rather than a
    shape we made up, so if Georgia ever lowers the gate we are already speaking
    its language. It still fails TODAY, because the switch is only a client-side
    toggle -- the Apex checks the token unconditionally. See docs/georgia-source.md.
    """
    if not token:
        return {"recaptchaResponse": json.dumps(""), "version": VERSION_NONE}
    return {
        "recaptchaResponse": json.dumps(
            {"response": token, "action": RECAPTCHA_ACTION}
        ),
        "version": VERSION_V3,
    }


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

    def bot_check_active(self) -> bool:
        """Is the SoS's own reCAPTCHA gate switched on right now?

        The page asks this on every load and we ask it for one reason: it is the
        only *change of Georgia's own* that could plausibly make the state
        collectable unattended. If Georgia turns the gate off, `_download` stops
        demanding a token and tries the presign with no code change.

        It is a CLIENT-SIDE toggle, so a `False` here is a green light to try,
        not a promise of success -- the Apex refused the tokenless shape on
        2026-09-06 with the switch on. See `recaptcha_params`.

        Every uncertain answer -- the call failed, the action errored, the shape
        is not what we saw on 2026-09-06, a flag we do not recognise -- returns
        True. "I could not tell" must mean "assume the gate is up", because the
        alternative is a pointless presign call against a state election site.
        """
        try:
            action = self._apex("VrMvpUtility", "getRecaptchaDetails", {})
        except SourceError:
            return True
        if action.get("state") != "SUCCESS":
            return True
        details = (action.get("returnValue") or {}).get("returnValue")
        if not isinstance(details, dict):
            return True
        flags = [details.get(field) for field in BOT_CHECK_FIELDS]
        if any(flag is None for flag in flags):
            return True
        # Either switch being off is the gate coming down, so it takes BOTH
        # being on to keep it up.
        return all(bool(flag) for flag in flags)

    def presigned_url(self, object_key: str, token: str) -> str:
        """Trade an S3 object key for a presigned download URL.

        The payload is built to match `c/vrWiVoterAbsenteeFiles` BYTE FOR BYTE,
        because the Apex action validates the shape before it validates the
        token and an invented shape gets refused for the wrong reason -- which
        would read in a log as "Georgia is captcha'd" when it really meant "we
        sent nonsense". Both branches were replayed against the live endpoint on
        2026-09-06; see `recaptcha_params` for what each one returns.
        """
        action = self._apex(
            "VrMvpUtility", "getPublicDownloadPresignedContent",
            {"fileName": object_key, **recaptcha_params(token)},
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

    def _presigned_download(self, object_key: str, filename: str, token: str) -> bytes:
        url = self.presigned_url(object_key, token)
        try:
            return _net.get(url, state=self.state, filename=filename,
                            min_bytes=MIN_BYTES)
        except _net.Missing as exc:
            raise NotYetPublished(f"GA: {object_key} is not posted") from exc

    def _download(self, cycle: int, number: str, *, allow_cache: bool) -> bytes:
        filename = self._cache_name(cycle, number)
        object_key = ZIP_KEY.format(year=cycle, number=number)
        token = os.environ.get(TOKEN_ENV, "").strip()
        if token:
            return self._presigned_download(object_key, filename, token)

        if allow_cache:
            path = _net.cache_path(self.state, filename)
            if path.exists() and path.stat().st_size >= MIN_BYTES:
                log.debug("GA: serving archived %s from cache", filename)
                return path.read_bytes()

        if self.bot_check_active():
            raise SourceError(UNATTENDED)

        # Georgia reports its own gate as OFF. Still an ATTEMPT, not an
        # assumption: the switch is a CLIENT-SIDE toggle, and the Apex action
        # refused this exact tokenless shape on 2026-09-06 while the switch was
        # on ("Missing necessary information to handle the request"). Whether it
        # relents once the switch is genuinely off cannot be tested from here --
        # so we spend one request to find out, and a refusal ends in exactly the
        # same SourceError as above.
        log.info("GA: SoS reports its bot check off; trying a tokenless presign")
        try:
            return self._presigned_download(object_key, filename, "")
        except SchemaDrift:
            # The org changed shape under us. That is its own signal and must
            # not be flattened into "Georgia is captcha'd". (NotYetPublished is
            # not a SourceError, so it already passes through untouched.)
            raise
        except SourceError as exc:
            raise SourceError(f"{UNATTENDED} Tokenless attempt: {exc}") from exc

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


# ==========================================================================
# THE ELECTION DATA HUB -- the route that actually runs unattended
# ==========================================================================
#
# Everything above is the mvp.sos.ga.gov absentee file: richer per ballot, fully
# parsed, fully tested, and reachable only with a hand-minted reCAPTCHA token.
# It is kept, and `GAScraper` still works the moment `$GA_SOS_RECAPTCHA_TOKEN`
# is set.
#
# What follows is a SECOND source for the same state, found 2026-09-08 and
# written up as docs/georgia-source.md §7. The survey recorded `sos.ga.gov` CMS
# pages as "403 -- Cloudflare challenge on every path"; they are not, and the
# page it wrote off indexes an Election Data Hub whose numbers come out of a
# Qlik Cloud Government tenant over an entirely anonymous chain. No captcha
# anywhere on it, so this is the class the registry points at.
#
# ⚠️ IT IS THINNER THAN THE FILE ABOVE, ON PURPOSE. County and method only: the
# hub carries `Age Group`, `RACE_DESC` and `Gender_Clean`, and the age bands
# OVERLAP (`35-40` and `40-45` share a year), so an age crosstab built from them
# double-counts a cohort in silence. Until that boundary is settled with the
# Elections Division none of the three is published, and states.csv is corrected
# to `county|method` to match -- a dims list promising rows nothing produces is
# its own kind of wrong.

#: The Qlik Cloud Government tenant, from `DH.ELECTION2024/js/vars.js`.
HUB_TENANT = "sos-ga-gov.us.qlikcloudgov.com"

#: The public Lambda the mashup calls for an anonymous OAuth2 token. It takes no
#: parameters and no credentials; it answers 201 with `{access_token, client_id}`.
HUB_TOKEN_URL = "https://fn4akbihvavvcmki6ih67rmuky0ezils.lambda-url.us-east-1.on.aws/"

#: The Data Hub page the mashup is embedded on. Sent as `Referer` on the token
#: request for the same reason a browser would: this is where the call comes from.
HUB_REFERER = "https://sos.ga.gov/"

#: "GA SOS Voting - All elections", internal name `[DH.ELECTION]`.
HUB_APP_ID = "7d780725-d407-4db8-b287-005bb85eda87"

#: The two county tables, by object id. Their MEASURES are read at run time; only
#: their identity is pinned here, and both are asserted to still carry a `County`
#: dimension before anything is read off them.
HUB_ABSENTEE_TABLE = "CTgPMg"                                    # by-county absentee
HUB_EARLY_TABLE = "7cc869cd-2124-43be-82f4-1c8d394dc6d8"         # by-county in-person

#: The measures this module needs, by the label the app gives them. A label that
#: stops appearing is SchemaDrift -- never a silently dropped column.
HUB_ACCEPTED = "Ballots Accepted"
HUB_REQUESTED = "Ballots Requested"
#: ⚠️ AND THE EARLY-VOTING TABLE SPELLS IT DIFFERENTLY. Its measure is labelled
#: `Ballots Accepted (EV)` in the app's properties even though the rendered
#: column header reads "Ballots Accepted" -- `qFallbackTitle` and `qLabel` are
#: not the same string here. Reading the label is what this module does, so the
#: label is what gets pinned, and the two tables need two constants.
HUB_ACCEPTED_EV = "Ballots Accepted (EV)"

#: The field the saved selection sits on, and the one we take control of.
HUB_ELECTION_FIELD = "Election Date"
HUB_ELECTION_NAME = "Election Name"

#: Georgia has 159 counties. A statewide row is published only at full coverage.
GA_COUNTIES = 159

#: How long any single engine call may take.
HUB_TIMEOUT = 60


def hub_token() -> str:
    """An anonymous access token from the mashup's own endpoint.

    `_net.get` gives this the repo's throttle and its cache mirror for free. It
    is deliberately NOT served from cache -- a token has a lifetime and a stale
    one produces a 401 three calls later, which is a much worse error than a
    fresh request.
    """
    try:
        raw = _net.get(HUB_TOKEN_URL, state="GA", filename="token.json",
                  headers={"Origin": HUB_REFERER.rstrip("/"), "Referer": HUB_REFERER},
                  min_bytes=64)
    except _net.Missing as exc:
        # The mashup's own code shows a "receiving unusually high traffic"
        # dialog on 429. If we ever see it, backing off is the answer, not a
        # retry loop against a state election service.
        raise SourceError(f"GA: the Data Hub token endpoint refused: {exc}") from exc
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise SchemaDrift("GA: the token endpoint did not return JSON") from exc
    access = payload.get("access_token")
    if not access:
        raise SchemaDrift(f"GA: the token endpoint returned {sorted(payload)}, "
                          "with no access_token")
    return access


class Engine:
    """The thinnest Qlik Engine JSON-RPC client this adapter can be built on.

    Not a general Qlik library and not trying to be. It speaks the six calls
    this module makes and treats everything else as an error.
    """

    def __init__(self, ws: Any) -> None:
        self._ws = ws
        self._id = 0

    def call(self, handle: int, method: str, params: Any) -> dict:
        self._id += 1
        self._ws.send(json.dumps({
            "jsonrpc": "2.0", "id": self._id,
            "handle": handle, "method": method, "params": params,
        }))
        while True:
            try:
                message = json.loads(self._ws.recv(timeout=HUB_TIMEOUT))
            except Exception as exc:  # transport, timeout, or malformed frame
                raise SourceError(f"GA: engine {method} failed: {exc}") from exc
            # The engine interleaves notifications (OnConnected, change
            # events) with responses. Anything without our id is not ours.
            if message.get("id") != self._id:
                continue
            if "error" in message:
                raise SourceError(f"GA: engine {method}: {message['error']}")
            return message.get("result", {})


def _handle(result: dict, what: str) -> int:
    handle = (result.get("qReturn") or {}).get("qHandle")
    if handle is None:
        raise SchemaDrift(f"GA: {what} returned no handle")
    return int(handle)


def selected_elections(engine: Engine, doc: int) -> dict[str, list[str]]:
    """What the app currently has selected, field by field."""
    sel = _handle(engine.call(doc, "CreateSessionObject", {"qProp": {
        "qInfo": {"qType": "ev-selection"},
        "qSelectionObjectDef": {},
    }}), "CreateSessionObject(selection)")
    layout = engine.call(sel, "GetLayout", {}).get("qLayout", {})
    out: dict[str, list[str]] = {}
    for entry in layout.get("qSelectionObject", {}).get("qSelections", []):
        out[entry.get("qField", "")] = [
            v.get("qName", "") for v in entry.get("qSelectedFieldSelectionInfo", [])
        ] or ([entry.get("qSelected")] if entry.get("qSelected") else [])
    return out


def field_values(engine: Engine, doc: int,
                 field: str) -> tuple[int, list[dict]]:
    """A list object over one field, and every value in it.

    Returns `(handle, values)` because the handle is not incidental: selecting
    is done THROUGH it.

    ⚠️ `Field.SelectValues` IS THE WRONG CALL, and it fails in the worst
    possible way. A FieldValue is `{qText, qIsNumeric, qNumber}` -- there is no
    element number in it -- so passing one is accepted and selects nothing, and
    the method simply returns false. For a date the field demonstrably contains
    that is indistinguishable from "this election does not exist yet", which
    would have left Georgia permanently and silently not-yet-published.
    `ListObject.SelectListObjectValues` is the call that takes element numbers,
    and element numbers are what actually identify a value.
    """
    handle = _handle(engine.call(doc, "CreateSessionObject", {"qProp": {
        "qInfo": {"qType": "ev-values"},
        "qListObjectDef": {
            "qDef": {"qFieldDefs": [field]},
            "qInitialDataFetch": [{"qTop": 0, "qLeft": 0, "qHeight": 500, "qWidth": 1}],
        },
    }}), f"CreateSessionObject({field})")
    layout = engine.call(handle, "GetLayout", {}).get("qLayout", {})
    out: list[dict] = []
    for page in layout.get("qListObject", {}).get("qDataPages", []):
        for row in page.get("qMatrix", []):
            cell = row[0]
            out.append({
                "text": (cell.get("qText") or "").strip(),
                "elem": cell.get("qElemNumber"),
                "state": cell.get("qState"),
            })
    return handle, out


def choose_election(engine: Engine, doc: int, day: date) -> list[str]:
    """Clear everything, select `day`, and prove that is what got selected.

    Returns the election NAMES now in scope. Raises `NotYetPublished` when the
    app has no election on that date -- the normal answer until Georgia's window
    opens, because the general does not enter the model until it does.
    """
    # ⚠️ ClearAll DOES NOT CLEAR THE SAVED SELECTION HERE. Measured: after
    # `ClearAll(qLockedAlso=True)` the app still reports 03/12/2024 as selected.
    # It is called anyway -- it does drop anything else -- but it is not the
    # guard. The explicit select below and the read-back after it are.
    engine.call(doc, "ClearAll", {"qLockedAlso": True})

    wanted = f"{day.month:02d}/{day.day:02d}/{day.year}"
    handle, values = field_values(engine, doc, HUB_ELECTION_FIELD)
    if not values:
        raise SchemaDrift(f"GA: the app has no {HUB_ELECTION_FIELD} field values")
    match = next((v for v in values if v["text"] == wanted), None)
    if match is None:
        latest = max((v["text"] for v in values), key=_as_sortable, default="none")
        raise NotYetPublished(
            f"GA: the Data Hub holds no election dated {wanted}; its newest is "
            f"{latest}"
        )

    # ⚠️ `qSuccess`, NOT `qReturn`. Most engine calls answer under `qReturn`;
    # this one does not, and reading the wrong key gives a falsy value on a
    # selection that actually worked -- which is a refusal invented by the
    # client, on live data, with no error anywhere to notice it by.
    took = engine.call(handle, "SelectListObjectValues", {
        "qPath": "/qListObjectDef",
        "qValues": [match["elem"]],
        "qToggleMode": False,
        "qSoftLock": False,
    }).get("qSuccess")
    if not took:
        raise SourceError(
            f"GA: the engine refused to select {HUB_ELECTION_FIELD} {wanted} "
            f"(element {match['elem']})"
        )

    # ⚠️ READ IT BACK. A successful select means the call was accepted, not that
    # the intended value is what ended up selected -- and this app ships with a
    # saved selection on this very field.
    got = [v["text"] for v in field_values(engine, doc, HUB_ELECTION_FIELD)[1]
           if v["state"] == "S"]
    if got != [wanted]:
        raise SchemaDrift(
            f"GA: asked for {HUB_ELECTION_FIELD} {wanted} and the app reports "
            f"{got!r} selected"
        )

    names = _names_in_scope(engine, doc)
    log.info("GA: selected %s -> %s", wanted, names)
    if names and not any("GENERAL" in n.upper() for n in names):
        raise SchemaDrift(
            f"GA: {wanted} is in the model but names no general election: {names!r}"
        )
    return names


def _as_sortable(text: str) -> tuple[int, int, int]:
    """MM/DD/YYYY -> a sortable key, so "its newest is" says something true."""
    try:
        month, day, year = (int(p) for p in text.split("/"))
        return (year, month, day)
    except (ValueError, TypeError):
        return (0, 0, 0)


def _names_in_scope(engine: Engine, doc: int) -> list[str]:
    lb = _handle(engine.call(doc, "CreateSessionObject", {"qProp": {
        "qInfo": {"qType": "ev-names"},
        "qListObjectDef": {
            "qDef": {"qFieldDefs": [HUB_ELECTION_NAME]},
            "qInitialDataFetch": [{"qTop": 0, "qLeft": 0, "qHeight": 50, "qWidth": 1}],
        },
    }}), "CreateSessionObject(names)")
    layout = engine.call(lb, "GetLayout", {}).get("qLayout", {})
    out = []
    for page in layout.get("qListObject", {}).get("qDataPages", []):
        for row in page.get("qMatrix", []):
            cell = row[0]
            # 'S' selected, 'O' optional (in scope). 'X' is excluded by the
            # date we just chose and is exactly what we do not want.
            if cell.get("qState") in ("S", "O"):
                out.append(cell.get("qText", ""))
    return out


def county_measures(engine: Engine, doc: int, object_id: str,
                    wanted: tuple[str, ...]) -> dict[str, dict[str, int]]:
    """`{COUNTY: {label: value}}` for one published table, WITHOUT suppression.

    The table's own hypercube definition is fetched and reused, so the state's
    set-analysis is never transcribed into this repo; only the two suppression
    flags are overridden. See the module docstring.
    """
    handle = _handle(engine.call(doc, "GetObject", {"qId": object_id}),
                     f"GetObject({object_id})")
    props = engine.call(handle, "GetProperties", {}).get("qProp", {})
    cube = props.get("qHyperCubeDef")
    if not cube:
        raise SchemaDrift(f"GA: object {object_id} carries no hypercube")

    dims = cube.get("qDimensions", [])
    fields = [f for d in dims for f in d.get("qDef", {}).get("qFieldDefs", [])]
    if fields != ["County"]:
        raise SchemaDrift(
            f"GA: object {object_id} is dimensioned by {fields!r}, not ['County']"
        )

    labels = [m.get("qDef", {}).get("qLabel") or "" for m in cube.get("qMeasures", [])]
    missing = [w for w in wanted if w not in labels]
    if missing:
        raise SchemaDrift(f"GA: object {object_id} no longer publishes {missing}; "
                          f"it has {labels!r}")

    cube = dict(cube)
    cube["qSuppressZero"] = False
    cube["qSuppressMissing"] = False
    cube["qInitialDataFetch"] = []
    session = _handle(engine.call(doc, "CreateSessionObject", {"qProp": {
        "qInfo": {"qType": "ev-table"}, "qHyperCubeDef": cube,
    }}), "CreateSessionObject(table)")

    layout = engine.call(session, "GetLayout", {}).get("qLayout", {}).get("qHyperCube", {})
    height = int(layout.get("qSize", {}).get("qcy", 0))
    width = 1 + len(labels)
    if height <= 0:
        raise SchemaDrift(f"GA: object {object_id} produced no rows for this election")

    want_at = {label: 1 + i for i, label in enumerate(labels) if label in wanted}
    out: dict[str, dict[str, int]] = {}
    top = 0
    while top < height:
        pages = engine.call(session, "GetHyperCubeData", {
            "qPath": "/qHyperCubeDef",
            "qPages": [{"qTop": top, "qLeft": 0,
                        "qHeight": min(500, height - top), "qWidth": width}],
        }).get("qDataPages", [])
        if not pages or not pages[0].get("qMatrix"):
            break
        for row in pages[0]["qMatrix"]:
            name = (row[0].get("qText") or "").strip()
            if not name:
                continue
            values: dict[str, int] = {}
            for label, index in want_at.items():
                cell = row[index]
                number = cell.get("qNum")
                if number is None or cell.get("qIsNull"):
                    raise SchemaDrift(
                        f"GA: {name} has no numeric {label!r} in {object_id}"
                    )
                values[label] = int(round(float(number)))
            out[name] = values
        top += len(pages[0]["qMatrix"])
    return out


def hub_build(absentee: dict[str, dict[str, int]], early: dict[str, dict[str, int]],
          day: date, cycle: int) -> FetchResult:
    """Canonical rows from the two county tables.

    ⚠️ THE UNION, not either table alone. With suppression off both should carry
    all 159 counties, and a county in one and not the other is drift worth
    raising rather than a row to quietly complete with a zero -- the whole point
    of turning suppression off was to stop guessing what an absence meant.
    """
    only_absentee = sorted(set(absentee) - set(early))
    only_early = sorted(set(early) - set(absentee))
    if only_absentee or only_early:
        raise SchemaDrift(
            f"GA: the two county tables disagree on which counties exist "
            f"(absentee only: {only_absentee[:5]}, early only: {only_early[:5]})"
        )

    result = FetchResult()
    totals = {"mail": 0, "inperson": 0, "requested": 0}
    for name in sorted(absentee):
        hit = _fips.lookup("GA", name.title())
        if hit is None:
            raise SchemaDrift(f"GA: unrecognised county name {name!r}")
        fips, canonical = hit
        mail = absentee[name][HUB_ACCEPTED]
        requested = absentee[name][HUB_REQUESTED]
        inperson = early[name][HUB_ACCEPTED_EV]
        totals["mail"] += mail
        totals["inperson"] += inperson
        totals["requested"] += requested
        result.county_rows.append(CountyDay(
            cycle=cycle, state="GA", county_fips=fips, day=day,
            county_name=canonical,
            ballots_total=mail + inperson,
            ballots_new=None,
            mail_returned=mail,
            inperson=inperson,
            # Georgia registers no voters by party. Never 0. The app's `Party`
            # field is a primary ballot choice, which is a different thing.
            party_dem=None, party_rep=None, party_oth=None, party_npa=None,
        ))

    covered = len({r.county_fips for r in result.county_rows})
    if covered != GA_COUNTIES:
        # A statewide total over a subset of counties looks exactly like a
        # Georgia turnout figure and is not one. Same rule as mt.py and id.py.
        log.warning("GA: %d of %d counties reported; publishing counties only",
                    covered, GA_COUNTIES)
        return result

    result.state_rows.append(StateDay(
        cycle=cycle, state="GA", day=day,
        ballots_total=totals["mail"] + totals["inperson"],
        ballots_new=None,
        mail_requested=totals["requested"],
        mail_returned=totals["mail"],
        inperson=totals["inperson"],
        party_dem=None, party_rep=None, party_oth=None, party_npa=None,
    ))
    return result


class GADataHubScraper(Adapter):
    """Tier 1 for Georgia: the SoS Election Data Hub's Qlik app.

    ⚠️ THE APP OPENS ON A SAVED SELECTION, AND IT IS THE 2024 PRIMARY.

    This is the trap, and it is completely invisible. Connect, open the app,
    read the county tables, and you get 159 correctly-named Georgia counties
    with entirely plausible ballot counts -- from **MARCH 12, 2024 -
    PRESIDENTIAL PREFERENCE PRIMARY**, because that is the selection saved into
    the published app:

        GetCurrentSelections -> Election Date: 1 of 24 -> '03/12/2024'

    Same family as Montana's dashboard holding the June primary and Idaho's
    tracker holding the May one, but worse: those two at least SAY on the page
    which election they are showing. Here the election is a selection inside a
    data model and the only way to know is to ask.

    So this never trusts the state it is handed. It clears every selection,
    selects the target election by date itself, READS THE SELECTION BACK, and
    refuses unless exactly the intended election is selected. A run that cannot
    prove what it selected does not publish.

    `NotYetPublished` until the general enters the model, which is the honest
    answer: on 2026-09-08 the app's newest election was AUGUST 25, 2026, and
    November 3 was simply not there yet.
    """

    state = "GA"
    name = "ga-datahub"
    tier = TIER_SCRAPER

    def fetch(self, cycle: int, as_of: date) -> FetchResult:
        try:
            from websockets.sync.client import connect
        except ImportError as exc:  # pragma: no cover - a packaging failure
            raise SourceError(f"GA: websockets is not installed: {exc}") from exc

        access = hub_token()
        target = election_date(cycle)
        try:
            session = connect(
                f"wss://{HUB_TENANT}/app/{HUB_APP_ID}",
                additional_headers={"Authorization": f"Bearer {access}"},
                max_size=64 * 1024 * 1024,
                open_timeout=HUB_TIMEOUT,
            )
        except Exception as exc:
            raise SourceError(f"GA: could not open the engine socket: {exc}") from exc

        with session as ws:
            engine = Engine(ws)
            doc = _handle(engine.call(-1, "OpenDoc", {"qDocName": HUB_APP_ID}),
                          "OpenDoc")
            choose_election(engine, doc, target)
            absentee = county_measures(engine, doc, HUB_ABSENTEE_TABLE,
                                       (HUB_ACCEPTED, HUB_REQUESTED))
            early = county_measures(engine, doc, HUB_EARLY_TABLE, (HUB_ACCEPTED_EV,))

        return hub_build(absentee, early, as_of, cycle)

    def fetch_history(self, cycle: int) -> FetchResult:
        # ⚠️ GUARD PARITY, and a refusal rather than a walk. Every past election
        # IS in this app -- `Election Date` lists 24 of them -- so a backfill
        # looks one selection away. It is not: the app holds each election's
        # CURRENT position only, with no daily history, so every past cycle
        # would come back as a single Election-Day figure stamped across the
        # window. `backfill` would then date it at the election date, which is
        # the fabricated-final-row bug `nd.py` was fixed for.
        raise NotYetPublished(
            f"GA: the Data Hub holds no daily history for {cycle}, only each "
            "election's final position"
        )
