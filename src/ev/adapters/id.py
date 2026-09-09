"""Idaho: the Secretary of State's absentee & early-voting tracker.

`docs/coverage-research.md` found this source twice. The first survey rejected it:

> Idaho's data is served from `datawrapper.dwcdn.net` under **cycle-specific
> chart IDs** that must be rescraped every election.

The West pass answered that and still did not build it:

> The chart ids are discoverable from a stable `.gov` page that also names the
> election. Rescraping per cycle is exactly the index-scrape pattern every
> adapter in this repo already does.

This is that build. Nothing here is hardcoded to a cycle: the page is the index,
the chart ids come out of it, and every dataset is identified by ITS OWN HEADER.

--------------------------------------------------------------------------
THE VERSION TRAP, WHICH IS THE WHOLE REASON THIS NEEDS TWO REQUESTS PER CHART
--------------------------------------------------------------------------

Datawrapper serves a chart's data at `/{id}/{version}/dataset.csv`, and the
version is not optional -- `/{id}/dataset.csv` is **404 (NoSuchKey)**. The trap
the survey recorded, re-verified live on 2026-09-08:

    /HCDQQ/1/dataset.csv    200, Ada 1,353 Republican   <- ancient
    /HCDQQ/17/dataset.csv   200, Ada 10,644             <- also stale
    /HCDQQ/23/dataset.csv   200, Ada 18,868             <- current today

**Any pinned version number silently serves an old snapshot forever.** It never
404s, never errors, and never stops being plausible. The only way to the current
data is `/{id}/`, which returns a 241-byte stub naming the live version -- so
this module resolves that stub on every run and pins nothing.

--------------------------------------------------------------------------
PROVENANCE IS THE TITLE, AND ONLY THE TITLE
--------------------------------------------------------------------------

The page's heading names the election:

    Absentee & Early Voting Stats - 2026 Primary Election

and TODAY (2026-09-08) it says exactly that, four months after the May primary,
with `last-modified: Wed, 20 May 2026` on the datasets themselves. So this
source has the same shape as Montana's dashboard: **it holds the last election's
numbers between cycles**, and a run that trusted it would stamp May's primary
absentee counts with September's date under the 2026 general.

The whole run is therefore refused unless the heading names THIS CYCLE'S
GENERAL, which today means Idaho reports `NotYetPublished`. That is the correct
and honest state of a tracker still showing the primary.

⚠️ **A LIMITATION WORTH STATING RATHER THAN HIDING.** Unlike Montana, Idaho
publishes no compile date anywhere a server can read: the "Last updated" line
and the statewide counters are both written by client-side JavaScript, and this
module makes no browser. So within the general's window rows are dated by the
RUN date, and a tracker that silently froze mid-October would keep producing
rows. The Datawrapper version numbers are the available change signal -- they
move when the data does -- and they are logged on every run for exactly that
reason. Detecting a frozen chart is left undone deliberately rather than
half-done.

--------------------------------------------------------------------------
WHAT IS PUBLISHED, AND THE ONE THING THAT IS NOT
--------------------------------------------------------------------------

Six charts are embedded. Two are county-level; this module reads one of them:

    jshNw  ResCountyDesc,Returned,total_issued,return_rate,early_voting,total_voted
    HCDQQ  ResCountyDesc,Republican,Democratic,Other

⚠️ **THE PARTY CHART IS DELIBERATELY NOT PUBLISHED.** During a primary Idaho's
"party" is the BALLOT A VOTER CHOSE, not their registration -- the first survey
flagged this ("Idaho's 'party' during a primary is the ballot chosen, not
registration") and the sibling chart `aCECg` says so in its own column name,
`AbsPartySelected`. What that column means in a GENERAL has never been observed,
because no general tracker has been published in this cycle. Publishing it as
`party_dem`/`party_rep` on a guess is precisely the mistake `ok.py` made with
Oklahoma and `az.py` made with the primary table, and this page's whole contract
is that a party column means party REGISTRATION.

So Idaho ships `county|method`. The moment a general tracker appears and its
party column is read, this is a one-function change -- `_party_rows` is not
written, and that is on purpose.
"""

from __future__ import annotations

import csv
import io
import logging
import re
from datetime import date

from ..schema import TIER_SCRAPER, CountyDay, StateDay
from . import _fips, eavs
from ._net import Missing, get
from .base import Adapter, FetchResult, NotYetPublished, SchemaDrift, SourceError

log = logging.getLogger(__name__)

#: The stable `.gov` index. It names the election and embeds every chart.
TRACKER = "https://voteidaho.gov/data-and-dashboards/absentee-tracker/"

DW = "https://datawrapper.dwcdn.net"

#: The heading, VERIFIED live 2026-09-08:
#:     "Absentee &amp; Early Voting Stats - 2026 Primary Election"
#: The `&amp;` is why the ampersand is optional, and the dash is matched as any
#: of hyphen/en/em because a CMS will change it without telling anyone.
TITLE = re.compile(
    r"Absentee\s*(?:&amp;|&)\s*Early\s+Voting\s+Stats\s*[-‐-―]\s*"
    r"(\d{4})\s+(General|Primary|Special)\s+Election",
    re.I,
)

#: Every Datawrapper chart the page embeds. Five characters, case-sensitive.
CHART_ID = re.compile(r"datawrapper\.dwcdn\.net/([A-Za-z0-9]{5})/")

#: Idaho has 44 counties. A statewide row is published only at full coverage.
EXPECTED_COUNTIES = 44

#: The county turnout dataset, identified BY ITS HEADER rather than by chart id.
#: The ids are cycle-specific -- that is the survey's original objection and it
#: is correct -- so matching on them would reintroduce exactly the fragility
#: this module exists to avoid.
TURNOUT_HEADER = (
    "rescountydesc", "returned", "total_issued",
    "return_rate", "early_voting", "total_voted",
)


def _int(raw: str, what: str) -> int:
    text = (raw or "").strip().replace(",", "")
    try:
        return int(text)
    except ValueError as exc:
        raise SchemaDrift(f"ID: {what} is {raw!r}, which is not a count") from exc


def _county(name: str) -> tuple[str, str]:
    hit = _fips.lookup("ID", name)
    if hit is None:
        raise SchemaDrift(f"ID: unrecognised county name {name!r}")
    return hit


def election(page: bytes, cycle: int) -> None:
    """Refuse the run unless the tracker's own heading names `cycle`'s general.

    Raises `NotYetPublished` for a tracker still holding another election --
    which STOPS the ladder, and is right: the aggregator has nothing better to
    say about a state whose window has not opened, and falling through would
    only trade one silence for another.
    """
    text = page.decode("utf-8", "replace")
    hit = TITLE.search(text)
    if hit is None:
        # SchemaDrift, not NotYetPublished: the page answered and we could not
        # read it, which is a fact about our parser and must fall through.
        raise SchemaDrift(
            "ID: the tracker page carries no 'Absentee & Early Voting Stats - "
            "<year> <election>' heading"
        )
    year, kind = int(hit.group(1)), hit.group(2).title()
    if year != cycle or kind != "General":
        raise NotYetPublished(
            f"ID: the tracker is showing the {year} {kind} election, not the "
            f"{cycle} General"
        )


def chart_ids(page: bytes) -> list[str]:
    """Every embedded chart id, in page order, deduped."""
    seen: dict[str, None] = {}
    for cid in CHART_ID.findall(page.decode("utf-8", "replace")):
        seen.setdefault(cid, None)
    if not seen:
        raise SchemaDrift("ID: the tracker page embeds no Datawrapper charts")
    return list(seen)


def current_version(cid: str) -> str:
    """The live version of one chart, from the stub `/{id}/` redirects to.

    See the module docstring: pinning a version serves stale data forever and
    never errors, so this is resolved on every run.
    """
    try:
        stub = get(DW + f"/{cid}/", state="ID", filename=f"chart_{cid}.html",
                   min_bytes=32)
    except Missing as exc:
        raise SourceError(f"ID: chart {cid} has no version stub: {exc}") from exc
    hit = re.search(rf"{re.escape(cid)}/(\d+)", stub.decode("utf-8", "replace"))
    if hit is None:
        raise SchemaDrift(f"ID: chart {cid}'s stub names no version")
    return hit.group(1)


def dataset(cid: str, version: str) -> list[dict[str, str]]:
    try:
        body = get(DW + f"/{cid}/{version}/dataset.csv", state="ID",
                   filename=f"chart_{cid}_{version}.csv", min_bytes=64)
    except Missing as exc:
        raise SourceError(f"ID: chart {cid} v{version} has no dataset: {exc}") from exc
    text = body.decode("utf-8-sig", "replace")
    return list(csv.DictReader(io.StringIO(text)))


def _header(rows: list[dict[str, str]]) -> tuple[str, ...]:
    return tuple((k or "").strip().lower() for k in (rows[0].keys() if rows else ()))


def parse(rows: list[dict[str, str]], day: date, cycle: int) -> FetchResult:
    """Canonical rows from the county turnout dataset."""
    result = FetchResult()
    totals = {"returned": 0, "issued": 0, "early": 0, "voted": 0}

    for row in rows:
        name = (row.get("ResCountyDesc") or "").strip()
        if not name:
            continue
        fips, canonical = _county(name)
        returned = _int(row.get("Returned", ""), f"{name} Returned")
        issued = _int(row.get("total_issued", ""), f"{name} total_issued")
        early = _int(row.get("early_voting", ""), f"{name} early_voting")
        voted = _int(row.get("total_voted", ""), f"{name} total_voted")

        # ⚠️ THE SOURCE'S OWN IDENTITY, CHECKED. Ada on 2026-09-08:
        # 16,750 returned + 12,911 early = 29,661 total_voted, exactly. If that
        # stops holding, `total_voted` has started counting something else and
        # every ballots_total this module publishes is wrong -- which is worth a
        # loud failure rather than a plausible number.
        if returned + early != voted:
            raise SchemaDrift(
                f"ID: {name} reports {returned} returned + {early} early = "
                f"{returned + early}, but total_voted is {voted}"
            )

        totals["returned"] += returned
        totals["issued"] += issued
        totals["early"] += early
        totals["voted"] += voted

        result.county_rows.append(CountyDay(
            cycle=cycle, state="ID", county_fips=fips, day=day,
            county_name=canonical,
            ballots_total=voted,
            ballots_new=None,
            mail_returned=returned,
            inperson=early,
            # Idaho registers by party, but see the module docstring: the only
            # party column this source publishes is a PRIMARY BALLOT CHOICE and
            # its meaning in a general has never been observed. Never 0.
            party_dem=None, party_rep=None, party_oth=None, party_npa=None,
        ))

    if not result.county_rows:
        raise SchemaDrift("ID: the county dataset produced no rows")

    covered = len({r.county_fips for r in result.county_rows})
    if covered != EXPECTED_COUNTIES:
        # Same rule as mt.py and tx.py: a statewide total over a subset of
        # counties looks exactly like an Idaho turnout figure and is not one.
        log.warning(
            "ID: tracker covers %d of %d counties; publishing counties only",
            covered, EXPECTED_COUNTIES,
        )
        return result

    result.state_rows.append(StateDay(
        cycle=cycle, state="ID", day=day,
        ballots_total=totals["voted"],
        ballots_new=None,
        mail_requested=totals["issued"],
        mail_returned=totals["returned"],
        inperson=totals["early"],
        party_dem=None, party_rep=None, party_oth=None, party_npa=None,
    ))
    return result


class IDScraper(Adapter):
    """Tier 1 for Idaho: the SoS absentee & early-voting tracker."""

    state = "ID"
    name = "id-sos"
    tier = TIER_SCRAPER

    def fetch(self, cycle: int, as_of: date) -> FetchResult:
        try:
            page = get(TRACKER, state="ID", filename=f"{cycle}_tracker.html",
                       min_bytes=4096)
        except Missing as exc:
            raise NotYetPublished(f"ID: {exc}") from exc

        # The title gate FIRST, before a single chart is fetched. A tracker
        # holding the primary must cost one request, not thirteen.
        election(page, cycle)

        wanted = None
        for cid in chart_ids(page):
            version = current_version(cid)
            rows = dataset(cid, version)
            if _header(rows) == TURNOUT_HEADER:
                log.info("ID: county turnout from chart %s v%s", cid, version)
                wanted = rows
                break
            log.debug("ID: chart %s v%s is %s", cid, version, _header(rows))

        if wanted is None:
            raise SchemaDrift(
                "ID: no embedded chart carries the county turnout header "
                f"{TURNOUT_HEADER}"
            )
        return parse(wanted, as_of, cycle)

    def fetch_history(self, cycle: int) -> FetchResult:
        """A past cycle's county FINAL from EAVS. ONE DAY, not a series.

        ⚠️ THIS IS NOT THE TRACKER, and the reason is below in EAVS's own
        section. The tracker cannot answer for a past cycle at all, and the
        refusal that used to stand here gave the wrong reason for it -- see
        `no_curve_from_datawrapper` for what was actually measured.
        """
        return eavs_history(cycle)


# ==========================================================================
# THE PAST CYCLES -- why the tracker cannot answer for one, and what can
# ==========================================================================
#
# `fetch_history` used to refuse by name, and its stated reason was:
#
#     "a version carries no date, only an ordinal, and this module has already
#      established that Idaho publishes no machine-readable timestamp"
#
# ⚠️ THE FIRST HALF OF THAT IS FALSE. Measured 2026-09-09 against the live CDN,
# one HEAD per version:
#
#     jshNw v1    200,    21 b, last-modified Thu, 09 Apr 2026
#     jshNw v5    200, 1,687 b, last-modified Mon, 13 Apr 2026
#     jshNw v30   200,   908 b, last-modified Fri, 01 May 2026
#     jshNw v51   200, 1,338 b, last-modified Wed, 20 May 2026
#     jshNw v60   404
#
# `last-modified` DOES date a Datawrapper version, monotonically, at Idaho's own
# 12:10 UTC publish cadence. So the ordinal is datable after all and a version
# walk would produce a dated series.
#
# ⚠️ AND IT STILL CANNOT REACH 2024, for a stronger reason that ends the
# question: **VERSION 1 OF THAT CHART IS DATED 2026-04-09.** These charts did
# not exist during the 2024 general. There is no version of them holding 2024
# data, so there is nothing to walk.
#
# What Idaho published in 2024 was a TABLEAU workbook, and the Internet Archive
# has the page that named it -- `tests/fixtures/id/tracker_2024_general_wayback.html`,
# captured at the same URL at 06:17 UTC on Election Day:
#
#     <h2>Absentee Stats for the 2024 Election Year</h2>
#     tab "General Election - Nov. 5"  -> AbsenteeNovember2024/Dashboard
#     tab "Primary Election - May 21"  -> absentee_16978441327890/AbsenteeStats
#
# That workbook is still live (`public.tableau.com/views/AbsenteeNovember2024/
# Dashboard` 302s to `/app/profile/voteidaho/viz/...`) and it is NOT usable from
# here, for two independent reasons. Its author disabled data download --
# `startSession` answers `allow_view_underlying: false`, and every crosstab
# endpoint is a 404 or an AWS WAF shell served as HTTP 200 -- and its
# `bootstrapSession` requires a `stickySessionKey` and a session-feature-flag
# blob that only a real browser produces. A BROWSER IS REQUIRED, which this
# repo will not do for a nightly job. Its `Timestamp` sheet also renders
# "Updated 25 February 2025" and it carries no date dimension on any of its
# eight sheets, so even with a browser it is ONE FINAL, not a curve.
#
# ⚠️ SO THERE IS NO 2024 COUNTY-BY-DAY SERIES FOR IDAHO, ANYWHERE. Ruled out
# four ways on 2026-09-09: the SoS's only 2024-general county publication is
# that single-snapshot Tableau; the Wayback CDX has zero captures of the viz
# page itself; the SoS's dated absentee XLSX series was discontinued after 2020;
# and TargetSmart's TargetEarly, which does carry all 44 counties, publishes its
# daily file STATEWIDE only (its county-by-day path is a soft-404 -- HTTP 200,
# `text/html`, 4,294 bytes of React shell) and its county totals are a voter-file
# credit measure running 15% below the clerks' own (345,485 against 408,325).
#
# What DOES exist is the county FINAL, and this is it -- and it does not live
# here. `adapters/eavs.py` reads the EAC's survey for ANY state; Idaho was
# simply the first to need it, and the whole reader was written in this module
# before three other states turned out to want the same file.
#
# Everything below is an alias. The parse, the release picker, the zip reader
# and the sentinel handling are the shared ones, byte-for-byte -- verified by
# running both against the real 2024 file and comparing all 44 counties and the
# statewide row before this module stopped carrying its own copy.
#
# ⚠️ The aliases are NOT nostalgia. `eavs.parse` takes a state and Idaho's did
# not, so `eavs_parse` here is a partial application that keeps Idaho's call
# shape. Tests and any caller reading `idaho.EAVS_NAME` still work, and there is
# exactly one implementation to fix when the EAC reissues the file.

EAVS_INDEX = eavs.INDEX
EAVS_HOST = eavs.HOST
EAVS_LINK = eavs.LINK
EAVS_VERSION = eavs.VERSION
EAVS_MAIL_RETURNED = eavs.MAIL_RETURNED
EAVS_INPERSON = eavs.INPERSON
EAVS_TOTAL_VOTERS = eavs.TOTAL_VOTERS
EAVS_STATE = eavs.STATE_COL
EAVS_FIPS = eavs.FIPS_COL
EAVS_JURISDICTION = eavs.JURISDICTION
EAVS_NOT_A_COUNT = eavs.NOT_A_COUNT
EAVS_MIN_BYTES = eavs.MIN_BYTES

#: NOT `id-sos`: these numbers reach us from the Election Assistance Commission,
#: even though every one of them was reported to the EAC by an Idaho county
#: clerk. They ride `schema.TIER_SURVEY`, not TIER_SCRAPER -- see schema.py for
#: why a rung had to exist before this could ship honestly.
EAVS_NAME = eavs.NAME

eavs_release = eavs.release
eavs_rows = eavs.rows
eavs_count = eavs.count


def eavs_parse(rows: list[dict[str, str]], cycle: int, day) -> FetchResult:
    """Idaho's county final. `eavs.parse` with the state already supplied."""
    return eavs.parse(rows, "ID", cycle, day)


def eavs_history(cycle: int) -> FetchResult:
    """Idaho's EAVS county final for a past cycle, stamped `eac-eavs`."""
    return eavs.history("ID", cycle)
