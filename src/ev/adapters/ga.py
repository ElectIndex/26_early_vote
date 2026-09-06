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
