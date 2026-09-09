"""Arizona — the Secretary of State's "Sent/Accepted Early Ballots" table.

Arizona has no downloadable early-vote file. What the SoS publishes is a table
rendered into the cycle's election-information page,

    https://azsos.gov/elections/election-information/<cycle>-election-info

one row per county plus a `Total` row, with four columns:

    County | Last Updated | Sent | Accepted

`Sent` is early ballots put in voters' hands; `Accepted` is early ballots
returned and signature-verified. Arizona has only 15 counties, so this one small
table is the whole state.

Three things worth knowing before changing anything here:

* **Arizona does not split the Accepted figure.** Every returned early ballot is
  in it however it came back -- mailed, dropped at a vote center, or voted early
  in person at the recorder's office. `mail_returned` and `inperson` therefore
  stay blank: publishing `mail_returned = Accepted` would silently assert a
  mail/in-person split that Arizona has not reported. See THE BLANK RULE in
  schema.py.

* **This table carries no party breakdown**, even though Arizona registers by
  party (Democratic / Republican / Green / Libertarian / No Labels / PND "Party
  Not Designated"). A second route in this module -- see THE RECORDER ROUTE
  below -- exists to supply that split from the county recorders. As of the
  2026-09-06 survey recorded in `RECORDER_SURVEY`, **no Arizona county recorder
  publishes early-ballot returns by party to the public in any format**, so
  `RECORDERS` is empty and every `party_*` field here is still None rather than a
  guess or a zero. See docs/arizona-party.md for the survey and its statuses.

* **azsos.gov answers non-browser clients with a Cloudflare interstitial, and
  `_net.get` walks through it.** To a plain `requests` GET the page is a 403
  carrying a 5,845-byte "Just a moment..." challenge; that is not a missing
  report, so `looks_like_challenge` raises SourceError and lets the ladder fall
  through to the aggregator. Only a genuine 404, or a real page with no
  Sent/Accepted table on it, is NotYetPublished.

  VERIFIED 2026-09-08, two requests five seconds apart:

      requests + DEFAULT_HEADERS   -> 403,   5,867 b, "Just a moment..."
      curl_cffi impersonate=chrome -> 200, 284,999 b, the real page

  so `_net.get`'s 403 -> impersonate retry already reaches Arizona, and
  `AZScraper().fetch(2026, ...)` answers `NotYetPublished: 2026
  election-information page carries no Sent/Accepted table yet` -- an honest
  "not posted" rather than "we were refused". **Arizona's blank is a calendar,
  not a wall:** that page carries the cycle's four election tables, and the
  November 3 general's row says early voting begins and early ballots are mailed
  on **October 7, 2026**. There is nothing to scrape until then, and there was
  nothing to scrape today.

  `looks_like_challenge` stays where it is, belt-and-braces: the transport now
  refuses a wall centrally (`_net.looks_like_wall`), but a challenge that arrives
  in a shape the transport does not know must still never be parsed as a page.

The table is found by its HEADER NAMES, never by position: the same page carries
a second, decoy header row ("County | Ballot by Precinct (PDF)") and the card the
table lives in is titled "Primary Election - ..." or "General Election - ..."
depending on the season.

THE RECORDER ROUTE
------------------

Arizona is the one state that registers by party and publishes no party split of
its early ballots, so it was deliberately excluded from the modelled split in
docs/party-estimate.md: a model standing in for a fact that could be *fetched* is
the single number most likely to be contradicted in public. The fix that document
names is "a county adapter, not this model". This is that adapter's second route.

**The survey found no public source.** Every Arizona county recorder was fetched
on 2026-09-06 -- `RECORDER_SURVEY` records each endpoint with the exact status it
answered -- and none publishes early-ballot returns broken down by party in any
format, machine-readable or otherwise. Maricopa's own answer is explicit: its
election-data downloads sit behind a credentialed portal whose sign-in page says
"Cities, towns, and political parties can use the login feature to download
election data from an assigned secure folder". The data exists; the public is not
an audience for it. So `RECORDERS` is EMPTY, `fetch()` returns the SoS rows
unchanged, and Arizona's party columns stay blank.

What is built here is the plumbing that route needs the day a recorder does post
one, because the arithmetic around it is the part worth getting right in advance:

* `party_bucket()` maps Arizona's labels onto `normalize`'s four buckets. The
  load-bearing one is **PND -> npa, never oth**: `normalize.party()` does not know
  the string "PND", and a voter who declined a party is not a Libertarian. The
  same trap is set by the Secretary of State's own registration report, whose
  "Other" column is 34% of Arizona and is overwhelmingly PND -- it is `npa`, not
  `oth`, and reading that header literally would be the largest single
  misclassification available in this state.

* `attach_party()` writes the measured split onto the county rows it covers and
  **onto nothing else**. A county with no recorder keeps `None` in every party
  column -- blank, not zero (THE BLANK RULE). The StateDay's party columns are
  forced to None unconditionally, because a split covering 74% of Arizona is not
  Arizona's split, and `ev_state_daily.csv` has no column that can say so. This
  is exactly the gate tx.py puts on its TOTAL row, for the same reason: a
  plausible partial number is worse than a hole, because it looks right.

* `coverage()` measures what was actually covered, from the SoS's own accepted
  counts rather than a hardcoded share, and `PartyCoverage.label()` prints the
  sentence a reader needs -- which counties, and what share of the state's early
  ballots they are.

* `blend_party_share()` is the piece worth the trouble. The modelled split carries
  a flat +/-5 points of structural error, but that error applies only to the
  ballots the model is actually standing in for. Measure Maricopa and Pima and the
  modelled remainder is a quarter of the state, so the blended figure carries
  +/-1.3 points, not +/-5 -- the band shrinks in proportion to how much is
  counted. A blend is still partly a model, so it belongs in
  `output/party_estimate.csv` beside the models and never in the reported
  `party_*` columns; docs/arizona-party.md carries the columns `estimate.py` would
  need to publish it, since that module is not this one's to edit.

None of this is Arizona-specific except the label table and the county list. The
hierarchy it encodes -- real reported party beats a blend, a blend beats a pure
model, a pure model beats nothing -- is meant to generalise to any state whose
counties report unevenly.
"""

from __future__ import annotations

import csv
import html
import io
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Callable, Iterable, Mapping

from .. import normalize
from ..calendar import days_to_election
from ..normalize import county_fips as _compose_fips
from ..schema import TIER_SCRAPER, CountyDay, StateDay
from . import _fips
from ._net import Missing, get
from .base import Adapter, FetchResult, NotYetPublished, SchemaDrift, SourceError

log = logging.getLogger(__name__)

#: VERIFIED: the `<cycle>-election-info` slug exists for both 2024 and 2026.
URL = "https://azsos.gov/elections/election-information/{cycle}-election-info"

#: ⚠️ AND IT IS NOT THE ONLY SLUG, WHICH HID THE WHOLE 2024 SEASON. Arizona also
#: publishes `<cycle>-election-INFORMATION`, and in 2024 that is the one that was
#: live while the general's early-vote window was open. VERIFIED 2026-09-09 in
#: the Internet Archive, captures of each exact URL:
#:
#:   .../2024-election-info          80 x HTTP 200, the EARLIEST 2024-11-11
#:   .../2024-election-information   12 x HTTP 200, 2024-09-18 .. 2024-11-06
#:
#: so a survey that only knew the short slug saw the 2024 page for the first
#: time six days after the election and concluded from a leftover. This is the
#: same class of miss as `de.py`'s -- a report that exists under a directory and
#: a filename nobody checked -- and the fix is the same: try every name we have
#: seen, in the order the live site uses them. It does NOT change the answer for
#: 2024 (see `fetch_history`), and that is exactly why it had to be checked
#: rather than assumed.
URLS = (URL, "https://azsos.gov/elections/election-information/"
             "{cycle}-election-information")

COUNTY_COLUMN = "county"
UPDATED_COLUMN = "last updated"
SENT_COLUMN = "sent"
ACCEPTED_COLUMN = "accepted"
REQUIRED = (COUNTY_COLUMN, UPDATED_COLUMN, SENT_COLUMN, ACCEPTED_COLUMN)

TOTAL_LABELS = {"total", "totals", "statewide", "state total"}

#: Arizona reuses one page for a cycle's primary and its general, and after the
#: primary it leaves the primary's table sitting there -- the only capture of the
#: 2024 page we have is from 2024-11-11 and still shows July's primary numbers.
#: A table whose newest row predates the general's ballot mailing therefore is
#: not the general's table, whatever the card above it is titled. UOCAVA ballots
#: go out 45 days ahead and A.R.S. 16-542 opens early voting at 27, so anything
#: older than this is last election's.
EARLY_WINDOW_DAYS = 45

#: "Jul 29, 2024 13:41" -- the only timestamp format this table has ever used.
_UPDATED = re.compile(r"([A-Z][a-z]{2})\s+(\d{1,2}),\s*(\d{4})(?:\s+(\d{1,2}):(\d{2}))?")

_TAG = re.compile(r"<[^>]+>")

#: Cloudflare's managed-challenge interstitial. Distinctive enough to tell apart
#: from a real page, and never present on one.
_CHALLENGE = ("just a moment", "cdn-cgi/challenge-platform", "cf_chl_opt")


def _text(fragment: str) -> str:
    return " ".join(html.unescape(_TAG.sub(" ", fragment)).split())


def looks_like_challenge(body: bytes) -> bool:
    """True if these bytes are a bot-check interstitial rather than the page."""
    head = body[:4096].decode("utf-8", errors="replace").lower()
    return any(marker in head for marker in _CHALLENGE)


def _rows(table: str) -> list[list[str]]:
    """Every row's cells, in order. Arizona renders the Total row's label and its
    counts as <th>, so both cell tags have to be read or the state total vanishes."""
    out = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", table, re.S):
        cells = [_text(c) for _, c in re.findall(r"<(td|th)[^>]*>(.*?)</\1>", tr, re.S)]
        if cells:
            out.append(cells)
    return out


def _int(raw: str) -> int | None:
    """A count, or None for a cell Arizona left as a placeholder ("---")."""
    text = raw.replace(",", "").strip()
    if not text or set(text) <= {"-", "–", "—"}:
        return None
    if not text.isdigit():
        raise SchemaDrift(f"AZ: non-numeric count {raw!r}")
    return int(text)


def _updated(raw: str) -> date | None:
    m = _UPDATED.search(raw or "")
    if not m:
        return None
    month, day, year = m.group(1), int(m.group(2)), int(m.group(3))
    try:
        return datetime.strptime(f"{month} {day} {year}", "%b %d %Y").date()
    except ValueError as exc:
        raise SchemaDrift(f"AZ: {raw!r} is not a Last Updated timestamp") from exc


def _ballot_table(markup: str) -> tuple[dict[str, int], list[list[str]]] | None:
    """(column index, data rows) for the Sent/Accepted table, or None if absent."""
    for table in re.findall(r"<table[^>]*>(.*?)</table>", markup, re.S):
        rows = _rows(table)
        for i, row in enumerate(rows):
            labels = [cell.lower() for cell in row]
            if all(name in labels for name in REQUIRED):
                return {name: labels.index(name) for name in REQUIRED}, rows[i + 1:]
    return None


def parse(markup: str, cycle: int) -> FetchResult:
    """Parse one election-information page into canonical rows."""
    found = _ballot_table(markup)
    if found is None:
        # Arizona renders this table only once early voting is underway. Before
        # then the page is simply a page -- not an error, and not a zero.
        raise NotYetPublished(
            f"AZ: {cycle} election-information page carries no Sent/Accepted table yet"
        )
    index, rows = found

    def cell(row: list[str], column: str) -> str:
        i = index[column]
        return row[i] if i < len(row) else ""

    # (is_statewide, fips, canonical name, sent, accepted, last updated)
    parsed: list[tuple[bool, str, str, int | None, int | None, date | None]] = []
    unknown: list[str] = []

    for row in rows:
        if len(row) < len(REQUIRED):
            # The page also carries a two-column "County | Ballot by Precinct"
            # header for a different table. Not our row.
            continue
        name = cell(row, COUNTY_COLUMN)
        if not name or name.lower() == COUNTY_COLUMN:
            continue

        sent = _int(cell(row, SENT_COLUMN))
        accepted = _int(cell(row, ACCEPTED_COLUMN))
        day = _updated(cell(row, UPDATED_COLUMN))

        if name.lower() in TOTAL_LABELS:
            parsed.append((True, "", name, sent, accepted, day))
            continue

        hit = _fips.lookup("AZ", name)
        if hit is None:
            unknown.append(name)
            continue
        fips, canonical = hit
        parsed.append((False, fips, canonical, sent, accepted, day))

    if unknown:
        # Arizona has exactly 15 counties and they do not change; a name we cannot
        # place is one we would silently drop off the map.
        raise SchemaDrift(f"AZ: unrecognised county names {sorted(set(unknown))[:5]}")
    if not any(not statewide for statewide, *_ in parsed):
        raise NotYetPublished(f"AZ: Sent/Accepted table for {cycle} has no county rows yet")

    days = [day for *_, day in parsed if day is not None]
    if not days:
        raise SchemaDrift("AZ: Sent/Accepted table carries no Last Updated timestamp")
    # Counties update at different times, so the report is only as current as its
    # newest row -- and a page left over from a past cycle must never be published
    # under this one.
    published = max(days)
    if published.year != int(cycle):
        raise SchemaDrift(
            f"AZ: table was last updated {published.isoformat()}, not in the {cycle} cycle"
        )

    result = FetchResult()
    for statewide, fips, name, sent, accepted, day in parsed:
        if statewide:
            result.state_rows.append(StateDay(
                cycle=cycle, state="AZ", day=day or published,
                ballots_total=accepted,
                mail_requested=sent,
                # Arizona publishes no mail/in-person split of Accepted, and no
                # party registration split of anything here. Never 0.
                mail_returned=None, inperson=None,
                party_dem=None, party_rep=None, party_oth=None, party_npa=None,
            ))
        else:
            result.county_rows.append(CountyDay(
                cycle=cycle, state="AZ", county_fips=fips, day=day or published,
                county_name=name,
                ballots_total=accepted,
                mail_returned=None, inperson=None,
                party_dem=None, party_rep=None, party_oth=None, party_npa=None,
            ))
    return result


def _refuse_a_stale_table(result: FetchResult, cycle: int) -> None:
    """Refuse a page still showing an earlier election's table.

    Arizona reuses one URL for a cycle's primary and its general and leaves the
    primary's numbers sitting there for months afterwards, so the table's own
    Last Updated date is the only thing that says which election it describes.
    See EARLY_WINDOW_DAYS. NotYetPublished rather than SchemaDrift: the general's
    table is not wrong, it is not there yet, and the ladder must stop rather than
    fall through to a source that would invent a number for the same day.
    """
    rows = result.state_rows or result.county_rows
    if not rows:
        return
    published = rows[0].day
    if days_to_election(cycle, published) > EARLY_WINDOW_DAYS:
        raise NotYetPublished(
            f"AZ: page is still showing the {published.isoformat()} table, from "
            f"before the {cycle} general's early-vote window opened"
        )


class AZScraper(Adapter):
    """Tier 1 for Arizona: the SoS Sent/Accepted Early Ballots table."""

    state = "AZ"
    name = "az-sos"
    tier = TIER_SCRAPER

    def _load(self, cycle: int, *, use_cache: bool) -> str:
        """The cycle's election-information page, under either of its slugs.

        See URLS: the short slug is what 2026 uses and what is tried first; the
        long one is where the 2024 general's season actually lived. A 404 on one
        is not absence until BOTH are 404 -- that mistake is what made a
        November leftover look like the whole of Arizona's 2024.
        """
        problems: list[str] = []
        for template in URLS:
            url = template.format(cycle=cycle)
            slug = url.rsplit("/", 1)[-1]
            try:
                body = get(url, state="AZ", filename=f"{slug}.html",
                           use_cache=use_cache, min_bytes=2048)
            except Missing:
                problems.append(f"{url} does not exist")
                continue
            if looks_like_challenge(body):
                # Reachability, not absence: the report may well be sitting
                # behind this. Fall through to the next tier rather than
                # stopping the ladder.
                raise SourceError(
                    f"AZ: {url} answered with a Cloudflare bot challenge"
                )
            return body.decode("utf-8", errors="replace")
        raise NotYetPublished(f"AZ: {'; '.join(problems)}")

    def fetch(self, cycle: int, as_of: date) -> FetchResult:
        result = parse(self._load(cycle, use_cache=False), cycle)
        published = result.state_rows[0].day if result.state_rows else result.county_rows[0].day
        if published > as_of:
            raise NotYetPublished(
                f"AZ: table is dated {published.isoformat()}, after {as_of.isoformat()}"
            )
        _refuse_a_stale_table(result, cycle)
        self._add_recorder_party(result, cycle, published)
        return result

    def _add_recorder_party(self, result: FetchResult, cycle: int, published: date) -> None:
        """Layer the county recorders' party split onto the SoS county rows.

        The SoS route stays authoritative for sent/accepted -- this only ever
        fills party columns, and only for counties that published one. It is a
        no-op today because RECORDERS is empty (see RECORDER_SURVEY).

        A recorder failing NEVER fails the run. Arizona's statewide ballot counts
        came from a different source and are already in hand; losing an optional
        enrichment must not turn a good SoS row into a fall-through to a weaker
        tier. NotYetPublished from a recorder means "that county has not posted
        today", which is a gap in one county, not a reason to stop the ladder.
        """
        if not RECORDERS:
            return
        measured: dict[str, dict[str, int | None]] = {}
        for recorder in RECORDERS:
            try:
                body = get(
                    recorder.url, state="AZ",
                    filename=f"{cycle}-recorder-{recorder.fips}.csv",
                    use_cache=False, min_bytes=32,
                )
                rows = [
                    (day, buckets)
                    for day, buckets in recorder.parse(
                        body.decode("utf-8", errors="replace"), cycle)
                    if day <= published
                ]
            except (SourceError, NotYetPublished) as exc:
                log.warning("AZ: %s recorder unusable (%s: %s)",
                            recorder.name, type(exc).__name__, exc)
                continue
            if not rows:
                log.info("AZ: %s recorder has posted nothing on or before %s",
                         recorder.name, published.isoformat())
                continue
            measured[recorder.fips] = dict(rows[-1][1])

        if not measured:
            log.info("AZ: no recorder party split available for %s", published.isoformat())
            return
        attach_party(result, measured)

    def fetch_history(self, cycle: int) -> FetchResult:
        """A past cycle's page -- one final snapshot, not a daily series.

        Arizona rewrites this table in place rather than keeping a dated file per
        day, so what survives for 2022/2024 is the last state it was left in.
        That is still the cycle's final early-ballot total, which is what the
        comparison lines anchor on.

        ⚠️ AND IT IS THE PRIMARY'S TABLE AS OFTEN AS THE GENERAL'S, which is why
        this shares `_refuse_a_stale_table` with `fetch()` rather than parsing and
        publishing whatever the page holds. `fetch()` has had that check since it
        was written; this path did not, so the backfill published what the live
        path refuses -- 921,671 Arizona ballots dated 2024-07-29, ninety-nine days
        before a general election whose ballots had not been printed. The live
        path and the history path must not disagree about what counts as this
        cycle's data.

        ⚠️ AND FOR 2024 THERE IS NO SUCH TABLE ANYWHERE. This is now settled, and
        settled on much better evidence than the note it replaces.

        The 2026-09-08 pass looked only at `<cycle>-election-info` and found two
        200s, 2024-11-11 and 2024-11-29, both showing the primary. That was the
        right conclusion from the wrong evidence: the earliest 200 of that slug
        is SIX DAYS AFTER the election, so it could only ever have shown a
        leftover, and "the general's table was never published" and "we only ever
        looked after it was taken down" are different claims. See URLS.

        Re-checked 2026-09-09 against the slug that WAS live during the season,
        `.../2024-election-information`. Twelve HTTP 200 captures span
        2024-09-18 to 2024-11-06; every one was fetched and parsed, including
        `20241011045207` -- two days after A.R.S. 16-542 opened early voting --
        and `20241106030640`, the morning after the general. Every one of the
        twelve, and the live page today, carries:

            card title   "Primary Election - Sent/Accepted Early Ballots"
            Last Updated  Jul 29, 2024 13:41  (Maricopa and Pima 14:35)
            Total         2,262,541 sent / 921,671 accepted

        identical across all thirteen documents, and the string "General Election
        - Sent" appears in NONE of them. Arizona did not merely leave the
        primary's table up after the general -- it never populated a general's
        table at all, not while ballots were going out, not on election night,
        not since. The general's Sent/Accepted numbers were never published here,
        so they cannot be recovered here.

        The refusal below is the whole answer and not a step towards one. Do not
        "fix" it by relaxing EARLY_WINDOW_DAYS -- that publishes July's primary
        as November's general, which is the exact bug it was added for.

        WHAT DOES EXIST FOR ARIZONA 2024, none of it this table, all verified
        2026-09-09 and none of it wired up:

        * `apps.azsos.gov/election/2024/ge/EarlyBallotsDroppedElectionDayGENERAL.pdf`
          -- 200, 86,176 b. Real, 15 counties, statewide 264,554. But it is the
          HB2785 report of early ballots dropped at polling places ON ELECTION
          DAY: one number, one day, a subset. Not sent/accepted, and not a final.
        * `recorder.pima.gov/VoterStats/EarlyVotingStatistics` -- 200, 103,457 b,
          now carrying a 2024 General row (563,702 requested / 442,409 returned).
          ONE county of fifteen, populated after the election; the Archive has no
          capture of it between 2024-10-05 and 2025-02-26, so no series.
        * The EAC's 2024 Election Administration and Voting Survey
          (`eac.gov/sites/default/files/2026-02/2024_EAVS_for_Public_Release_`
          `nolabel_V2_csv.zip`, 200, 2,119,187 b) has all fifteen counties with
          mail ballots transmitted/returned and votes counted by mode -- and its
          Pima figure is 442,409, matching Pima's own page exactly. It is the
          only complete 2024 county final that exists. It is ALSO a federal
          survey published fifteen months after the election, not Arizona's own
          file, so publishing it through `az-sos` at TIER_SCRAPER would be a
          provenance lie; and its mode split is drawn inconsistently by counties
          (Navajo reports zero in-person early votes). Wiring it in is a decision
          about what a national post-election source is worth and where it sits
          on the ladder, which is not this adapter's to make alone.
        """
        if cycle >= date.today().year:
            raise NotYetPublished(f"AZ: {cycle} is not an archived cycle")
        result = parse(self._load(cycle, use_cache=True), cycle)
        _refuse_a_stale_table(result, cycle)
        return result


# ==========================================================================
# THE RECORDER ROUTE -- the party split the SoS table lacks
#
# Read the module docstring's THE RECORDER ROUTE section first. Everything
# below is inert today: RECORDERS is empty because the 2026-09-06 survey found
# no public source. It is written and tested anyway because the coverage and
# blending arithmetic is the part that has to be right BEFORE a number exists,
# not after it is on a page.
# ==========================================================================

#: Arizona's 15 county FIPS, from the census list. A recorder whose fips is not
#: in here is a typo, and `_check_recorders()` refuses it at import time.
ALL_COUNTY_FIPS: frozenset[str] = frozenset(
    _compose_fips("AZ", code) for _, code in _fips.CENSUS_COUNTIES["AZ"]
)

#: Share of Arizona's ACTIVE registered voters in each county, from the
#: Secretary of State's own July 2026 registration report ("PE 2026" rows):
#: https://apps.azsos.gov/election/VoterReg/2026/State-Voter-Registration_July_2026.pdf
#: -- fetched 2026-09-06, HTTP 200, 472,326 bytes. Statewide active total
#: 4,306,554.
#:
#: This is registration, NOT ballots, and it is here for two jobs only: to say in
#: advance roughly how much of Arizona a given set of recorders would cover, and
#: to make that claim checkable against a citable state document. The coverage
#: actually published is computed from the day's own accepted-ballot counts by
#: `coverage()`, never from this table -- early-vote geography is not
#: registration geography and pretending otherwise is how a partial figure gets
#: quietly overstated.
REGISTRATION_SHARE: dict[str, float] = {
    "04013": 0.5899,  # Maricopa    2,540,618
    "04019": 0.1500,  # Pima          646,190
    "04021": 0.0672,  # Pinal         289,250
    "04025": 0.0402,  # Yavapai       172,971
    "04015": 0.0347,  # Mohave        149,407
    "04027": 0.0255,  # Yuma          109,840
    "04005": 0.0201,  # Coconino       86,421
    "04003": 0.0191,  # Cochise        82,413
    "04017": 0.0169,  # Navajo         72,918
    "04001": 0.0124,  # Apache         53,411
    "04007": 0.0080,  # Gila           34,258
    "04023": 0.0076,  # Santa Cruz     32,699
    "04009": 0.0047,  # Graham         20,375
    "04012": 0.0026,  # La Paz         11,219
    "04011": 0.0011,  # Greenlee        4,564
}

#: Half-width of the modelled party split's error band, as a share.
#:
#: MUST equal `ev.estimate.MODEL_ERROR`. It is duplicated rather than imported
#: because docs/party-estimate.md is explicit that the ingest path never loads
#: `ev.estimate` -- keeping the model out of the daily job is what stops it
#: publishing a model number by accident. `test_model_error_tracks_estimate`
#: imports both and fails if they ever drift apart.
#:
#: It was 0.10 while the model was county geography alone. It is 0.05 since the
#: model gained a mail-selection term, whose measured leave-one-state-out error
#: is 3.85 points across thirteen completed series; see docs/party-estimate.md.
MODEL_ERROR = 0.06

#: How Arizona spells its parties, mapped onto a spelling `normalize.party()`
#: already knows. Only labels normalize does NOT recognise belong here; anything
#: it handles (dem/rep/libertarian/green/independent...) is left to it so this
#: table cannot quietly diverge from the shared vocabulary.
#:
#: PND is the one that matters. "Party Not Designated" is Arizona's term for a
#: voter who declined to register with a party -- a third of the state -- and it
#: is `npa`. Bucketing it as `oth` would move 1.5 million Arizonans into a
#: "third party" column and destroy the single most-watched number in an
#: early-vote story. `normalize.party("pnd")` returns None, so this alias is
#: load-bearing rather than decorative; there is a test that says so.
AZ_PARTY_ALIASES: dict[str, str] = {
    "pnd": "npa",
    "pd": "npa",
    "party not designated": "npa",
    "not designated": "npa",
    "no party designation": "npa",
    "unaffiliated/pnd": "npa",
    # The SoS's registration report calls this column "Other" and it is 34% of
    # Arizona -- overwhelmingly PND, not third parties. A recorder copying that
    # header is reporting unaffiliated voters, so it is NOT routed to `oth`.
    # A file that genuinely means minor parties must say so; see OTHER_IS_NPA.
    "oth/pnd": "npa",
    "other/pnd": "npa",
    # Arizona's recognised minor parties. `normalize` knows "lib"/"green" but
    # not the state's own three-letter codes.
    "lbt": "libertarian",
    "lbr": "libertarian",
    "grn": "green",
    "nol": "no labels",
    "no labels": "no labels",
    "nlb": "no labels",
}

#: Header spellings whose meaning we refuse to guess, with the reason. A file
#: using one of these raises SchemaDrift rather than being bucketed: in Arizona
#: a bare "Other" is the state's own name for unaffiliated voters in one report
#: and for minor parties in another, and no amount of context in a column header
#: settles which. Ask the recorder; do not guess.
AMBIGUOUS_PARTY_LABELS: frozenset[str] = frozenset({"other", "oth", "all others", "misc"})

#: Column names a recorder file might use for its as-of date.
DATE_COLUMNS: tuple[str, ...] = (
    "date", "report date", "as of", "as of date", "reporting date", "day",
)

#: Non-party columns a recorder file may carry that are not a drift signal.
IGNORED_COLUMNS: frozenset[str] = frozenset({
    "total", "totals", "grand total", "all", "county", "precinct", "notes",
})

_RECORDER_DATE = re.compile(r"(\d{4})-(\d{2})-(\d{2})|(\d{1,2})/(\d{1,2})/(\d{4})")


@dataclass(frozen=True)
class Surveyed:
    """One endpoint that was actually fetched, and what it turned out to be.

    This exists so the negative result is auditable. Every row was requested on
    `checked`; `status` is the literal HTTP status observed (with "(browser TLS)"
    where `_net.get`'s impersonated retry was what answered), and `verdict` says
    what the response contained. Nothing in here is a guess at a URL.
    """

    county: str
    url: str
    status: str
    checked: str
    verdict: str


#: The survey behind `RECORDERS = ()`. Fetched 2026-09-06 in county-size order,
#: Maricopa and Pima first, then the next four by size, then the SoS-side
#: statewide candidates. NOT ONE of these carries early-ballot returns broken
#: down by party. docs/arizona-party.md narrates what each one is instead.
RECORDER_SURVEY: tuple[Surveyed, ...] = (
    Surveyed(
        "Maricopa", "https://elections.maricopa.gov/sitemap.xml", "200", "2026-09-06",
        "176 URLs, complete site map. No early-ballot-returns page of any kind.",
    ),
    Surveyed(
        "Maricopa", "https://elections.maricopa.gov/results-and-data/election-data.html",
        "200", "2026-09-06",
        'Sign-in form. "Cities, towns, and political parties can use the login '
        'feature to download election data from an assigned secure folder." The '
        "machine-readable data exists and the public is not an audience for it.",
    ),
    Surveyed(
        "Maricopa", "https://elections.maricopa.gov/results-and-data/election-data/portal.html",
        "200", "2026-09-06",
        "The portal behind that login. Renders 'Login Required' to an anonymous "
        "client; the file list is served only to an authenticated session.",
    ),
    Surveyed(
        "Maricopa", "https://elections.maricopa.gov/results-and-data/election-results.html",
        "200", "2026-09-06", "Canvass and summary PDFs only. No early-vote series.",
    ),
    Surveyed(
        "Maricopa", "https://elections.maricopa.gov/results-and-data/early-ballot-returns.html",
        "404", "2026-09-06", "Does not exist (checked because the name is the obvious one).",
    ),
    Surveyed(
        "Maricopa", "https://elections.maricopa.gov/results-and-data/early-voting-statistics.html",
        "404", "2026-09-06", "Does not exist.",
    ),
    Surveyed(
        "Maricopa", "https://recorder.maricopa.gov/", "200", "2026-09-06",
        "Recorder homepage. Every election link is a per-voter lookup (request a "
        "ballot, check my ballot's status). No aggregate reporting.",
    ),
    Surveyed(
        "Maricopa", "https://recorder.maricopa.gov/sitemap.xml", "404", "2026-09-06",
        "No site map; the 2022, 2024 and 2026 Wayback captures of this host carry "
        "canvass PDFs and per-voter lookup pages, and no returns report.",
    ),
    Surveyed(
        "Pima", "https://recorder.pima.gov/VoterStats/EarlyVotingStatistics", "200", "2026-09-06",
        "The closest thing in the state to what we want, and it is not it: one "
        "table of FINAL early ballots requested/returned per election back to "
        "1996. No party columns, and no during-the-window daily series.",
    ),
    Surveyed(
        "Pima", "https://recorder.pima.gov/BallotProcessingReports", "200", "2026-09-06",
        "Landing page with no table and no report links, in or out of an election "
        "window (the 2024-10-04 capture is the same).",
    ),
    Surveyed(
        "Pima", "https://recorder.pima.gov/Voter_dashboard_login.aspx", "200", "2026-09-06",
        "Pima's 'NEW Voter Dashboard' is a sign-in, like Maricopa's portal.",
    ),
    Surveyed(
        "Pinal", "https://www.pinalcountyaz.gov/elections/", "200", "2026-09-06",
        "Results, polling places, sample ballots. No returns reporting.",
    ),
    Surveyed(
        "Pinal", "https://www.pinalcountyaz.gov/recorder/", "200", "2026-09-06",
        "Recording services; elections handed off to the Elections Department page.",
    ),
    Surveyed(
        "Yavapai", "https://www.yavapaivotes.gov/Home", "200 (browser TLS)", "2026-09-06",
        "Yavapai's dedicated elections site. Early-voting locations and a per-voter "
        "ballot-status lookup. No aggregate returns.",
    ),
    Surveyed(
        "Mohave", "https://www.mohave.gov/departments/recorder/voter-registration/"
        "early-voting-information/", "200", "2026-09-06",
        "Dates and locations for the 2026 general (in-person early voting opens "
        "2026-10-07). No returns.",
    ),
    Surveyed(
        "Mohave", "https://www.mohave.gov/departments/elections/maps-and-statistics/",
        "200", "2026-09-06",
        "Registration figures and precinct maps. Registration, not ballots.",
    ),
    Surveyed(
        "Yuma", "https://www.yumacountyaz.gov/government/voter-registration/widget-pages/"
        "voter-registration-stats/voter-registration-stats-2025",
        "200 (browser TLS)", "2026-09-06",
        "Registration counts by party, monthly. Registration, not ballots.",
    ),
    Surveyed(
        "statewide", "https://apps.arizona.vote/electioninfo/BPS/68/0", "403", "2026-09-06",
        "The SoS's Ballot Progress page. Cloudflare managed challenge even through "
        "the browser TLS fingerprint -- a wall, not an absence. The 2024 archive of "
        "it shows what is behind: ballots tabulated and left to process per county, "
        "post-election-day, with no party split.",
    ),
    Surveyed(
        "statewide", "https://azsos.gov/elections/results-data/voter-registration-statistics",
        "200", "2026-09-06",
        "County x party ACTIVE REGISTRATION, quarterly, as a PDF. Real, citable, "
        "and the source of REGISTRATION_SHARE above -- but registration, not "
        "ballots. Its 'Other' column is PND; see AZ_PARTY_ALIASES.",
    ),
)


@dataclass(frozen=True)
class Recorder:
    """One county recorder's early-ballot-returns-by-party feed.

    `verified` is not documentation: it is the exact status the URL answered and
    the date it did so. An entry may only be added here after somebody fetched
    it, because a plausible-looking URL that 404s in October is indistinguishable
    from a county that has not posted yet, and the ladder treats those very
    differently.
    """

    fips: str
    name: str
    url: str
    verified: str
    parse: Callable[[str, int], "list[tuple[date, dict[str, int | None]]]"]


#: THE LIVE ROUTES. Empty, and that is the finding rather than a stub: see
#: RECORDER_SURVEY above and docs/arizona-party.md. Adding an entry is the whole
#: change -- `fetch()` picks it up, `attach_party()` writes only that county,
#: `coverage()` reports the share, and the state row stays blank either way.
RECORDERS: tuple[Recorder, ...] = ()


def _check_recorders(recorders: Iterable[Recorder] = RECORDERS) -> None:
    """Refuse a recorder pointing at a FIPS Arizona does not have."""
    for r in recorders:
        if r.fips not in ALL_COUNTY_FIPS:
            raise ValueError(f"AZ: recorder {r.name!r} has non-Arizona FIPS {r.fips!r}")


_check_recorders()


# --------------------------------------------------------------------------
# Party labels
# --------------------------------------------------------------------------
def party_bucket(raw: str | None) -> str | None:
    """Arizona's party label -> one of normalize.PARTY_BUCKETS, or None.

    None means "I do not recognise this", exactly as `normalize.party()` does,
    and the caller must raise SchemaDrift rather than bucket it as "other".
    A label in AMBIGUOUS_PARTY_LABELS also returns None, deliberately: in
    Arizona "Other" means unaffiliated in one official report and minor parties
    in another, and guessing wrong moves a third of the state.
    """
    if raw is None:
        return None
    key = " ".join(str(raw).strip().lower().split())
    if not key:
        return None
    if key in AMBIGUOUS_PARTY_LABELS:
        return None
    return normalize.party(AZ_PARTY_ALIASES.get(key, key))


def _count(raw: str) -> int | None:
    """A count, or None for a cell the recorder left blank. Never 0 by default."""
    text = str(raw).replace(",", "").strip()
    if not text or set(text) <= {"-", "–", "—"} or text.lower() in {"n/a", "na"}:
        return None
    if not text.lstrip("+").isdigit():
        raise SchemaDrift(f"AZ: recorder returned non-numeric count {raw!r}")
    return int(text)


def _recorder_date(raw: str, cycle: int) -> date:
    m = _RECORDER_DATE.search(str(raw or ""))
    if m is None:
        raise SchemaDrift(f"AZ: recorder row {raw!r} carries no report date")
    if m.group(1):
        day = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    else:
        day = date(int(m.group(6)), int(m.group(4)), int(m.group(5)))
    if day.year != int(cycle):
        raise SchemaDrift(
            f"AZ: recorder row is dated {day.isoformat()}, not in the {cycle} cycle"
        )
    return day


def parse_party_table(text: str, cycle: int) -> list[tuple[date, dict[str, int | None]]]:
    """Parse a delimited county early-ballot-returns file into (day, buckets).

    Columns are matched BY HEADER NAME, never by position: one column names the
    report date, every other column is a party label, and two columns that both
    map to the same bucket (Libertarian and Green -> `oth`) are summed. A header
    this cannot place raises SchemaDrift -- see rule 3 in CLAUDE.md. A blank cell
    stays None; only a cell the recorder actually wrote as 0 becomes 0.

    NOTE ON PROVENANCE: no Arizona recorder publishes such a file today, so this
    parser's contract was written rather than reverse-engineered, and the fixture
    behind its test is marked SYNTHETIC for that reason. The rules it enforces --
    header-by-name, unknown-label-is-drift, blank-is-not-zero -- are the ones the
    project requires of every parser regardless of the file, which is why it is
    worth pinning now; the exact dialect gets adjusted against the real file the
    first day one exists.
    """
    rows = list(csv.reader(io.StringIO(text.lstrip("﻿"))))
    rows = [r for r in rows if any(cell.strip() for cell in r)]
    if not rows:
        raise SchemaDrift("AZ: recorder file is empty")

    header = [" ".join(cell.strip().lower().split()) for cell in rows[0]]
    date_index = next((i for i, name in enumerate(header) if name in DATE_COLUMNS), None)
    if date_index is None:
        raise SchemaDrift(f"AZ: recorder file has no date column in {header}")

    columns: list[tuple[int, str]] = []
    unknown: list[str] = []
    for i, name in enumerate(header):
        if i == date_index or not name or name in IGNORED_COLUMNS:
            continue
        bucket = party_bucket(name)
        if bucket is None:
            unknown.append(name)
            continue
        columns.append((i, bucket))
    if unknown:
        raise SchemaDrift(f"AZ: recorder file has unmappable party columns {unknown}")
    if not columns:
        raise SchemaDrift(f"AZ: recorder file carries no party columns: {header}")

    out: list[tuple[date, dict[str, int | None]]] = []
    for row in rows[1:]:
        if date_index >= len(row):
            continue
        day = _recorder_date(row[date_index], cycle)
        buckets: dict[str, int | None] = {}
        for i, bucket in columns:
            value = _count(row[i]) if i < len(row) else None
            if value is None:
                # Absent, not zero. A bucket stays None unless SOME column
                # reporting it carried a real number.
                buckets.setdefault(bucket, None)
                continue
            buckets[bucket] = (buckets.get(bucket) or 0) + value
        out.append((day, buckets))
    if not out:
        raise SchemaDrift("AZ: recorder file has a header and no rows")
    return sorted(out, key=lambda pair: pair[0])


# --------------------------------------------------------------------------
# Coverage -- what a partial split actually covers
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class PartyCoverage:
    """How much of Arizona a party split covers, and therefore what it is.

    `measured_fraction` is the covered counties' share of the day's ACCEPTED
    early ballots as the Secretary of State reported them -- the real
    denominator, computed per day, not a hardcoded electorate share. That is the
    number that has to appear next to any published figure, because
    "77% of Arizona" and "Arizona" are different claims and only one of them is
    true.
    """

    counties: tuple[str, ...]
    county_names: tuple[str, ...]
    measured_ballots: int
    state_ballots: int | None
    total_counties: int = 15

    @property
    def measured_fraction(self) -> float | None:
        """Covered share of the day's early ballots, or None if unknowable."""
        if not self.state_ballots:
            return None
        return min(1.0, self.measured_ballots / self.state_ballots)

    @property
    def modelled_fraction(self) -> float | None:
        f = self.measured_fraction
        return None if f is None else 1.0 - f

    @property
    def empty(self) -> bool:
        """No county reported party at all. Not a partial figure -- no figure."""
        return not self.counties

    @property
    def complete(self) -> bool:
        """Every Arizona county reported party. Only then is this a state split."""
        return len(self.counties) == self.total_counties

    @property
    def partial(self) -> bool:
        """Some but not all of Arizona. NEVER publishable as a statewide split."""
        return bool(self.counties) and not self.complete

    def label(self) -> str:
        """The sentence that has to travel with the number."""
        if self.empty:
            return "AZ: no county recorder reported a party split"
        share = self.measured_fraction
        pct = "an unknown share" if share is None else f"{100 * share:.1f}%"
        names = ", ".join(self.county_names)
        if self.complete:
            return (
                f"AZ: all {self.total_counties} counties reported a party split "
                f"({pct} of accepted early ballots)"
            )
        return (
            f"AZ: PARTIAL party split -- {len(self.counties)} of "
            f"{self.total_counties} counties ({names}), covering {pct} of the "
            f"state's accepted early ballots. Not Arizona's split."
        )


def coverage(result: FetchResult, measured: Iterable[str] | None = None) -> PartyCoverage:
    """Which counties carry a party split in `result`, and what share they are.

    Pass `measured` to ask the question of a set of FIPS directly; by default it
    reads the rows, so it reports what was actually written rather than what was
    intended.
    """
    county_rows = [r for r in result.county_rows if r.state.upper() == "AZ"]
    if measured is None:
        covered = {
            r.county_fips for r in county_rows
            if any(v is not None for v in
                   (r.party_dem, r.party_rep, r.party_npa, r.party_oth))
        }
    else:
        covered = set(measured)

    by_fips = {r.county_fips: r for r in county_rows}
    measured_ballots = sum(
        by_fips[f].ballots_total or 0 for f in covered if f in by_fips
    )
    state_rows = [r for r in result.state_rows if r.state.upper() == "AZ"]
    state_ballots = state_rows[0].ballots_total if state_rows else None
    if state_ballots is None:
        # No headline figure -- fall back to the sum of the counties we have, so
        # the fraction is still against a real denominator rather than nothing.
        summed = sum(r.ballots_total or 0 for r in county_rows)
        state_ballots = summed or None

    ordered = sorted(covered, key=lambda f: (-REGISTRATION_SHARE.get(f, 0.0), f))
    names = tuple(
        (by_fips[f].county_name or f) if f in by_fips else f for f in ordered
    )
    return PartyCoverage(
        counties=tuple(ordered),
        county_names=names,
        measured_ballots=measured_ballots,
        state_ballots=state_ballots,
    )


def attach_party(
    result: FetchResult,
    measured: Mapping[str, Mapping[str, int | None]],
) -> PartyCoverage:
    """Write a per-county party split onto `result`, and return its coverage.

    `measured` is FIPS -> {normalize bucket -> count}. Three rules, and they are
    the whole point of this function:

    1. A county NOT in `measured` is left alone. Its party columns stay None --
       blank, meaning "not reported" -- and never become 0. A reader summing the
       county file must come up short by exactly the counties nobody measured,
       which is the honest answer.
    2. A bucket missing from a county's mapping is None for that county too. Only
       a count the recorder actually published is written, including a real 0.
    3. The STATE row's party columns are set to None unconditionally, whatever
       the coverage. `ev_state_daily.csv` has no column that can say "this covers
       11 counties of 15", so a partial split written there would be read as
       Arizona's split by every consumer downstream and summed into a national
       party total as though it were complete. This is tx.py's gate on its TOTAL
       row, applied to a column instead of a table.
    """
    unknown = [f for f in measured if f not in ALL_COUNTY_FIPS]
    if unknown:
        raise SchemaDrift(f"AZ: party counts for non-Arizona counties {sorted(unknown)[:5]}")

    for row in result.county_rows:
        if row.state.upper() != "AZ":
            continue
        counts = measured.get(row.county_fips)
        if counts is None:
            continue  # Rule 1. Not zero.
        bad = [b for b in counts if b not in normalize.PARTY_BUCKETS]
        if bad:
            raise SchemaDrift(f"AZ: {row.county_fips} reported unknown buckets {sorted(bad)}")
        row.party_dem = counts.get(normalize.PARTY_DEM)
        row.party_rep = counts.get(normalize.PARTY_REP)
        row.party_npa = counts.get(normalize.PARTY_NPA)
        row.party_oth = counts.get(normalize.PARTY_OTH)

    for row in result.state_rows:
        if row.state.upper() != "AZ":
            continue
        # Rule 3. Not a policy choice made per-run: there is no coverage under
        # which a county-recorder split becomes Arizona's reported split, because
        # even at 15/15 it would be a different source than the row's own.
        row.party_dem = row.party_rep = row.party_npa = row.party_oth = None

    found = coverage(result)
    if found.partial:
        log.warning("%s", found.label())
    elif found.complete:
        log.info("%s", found.label())
    return found


# --------------------------------------------------------------------------
# The blend -- measured counties plus a modelled remainder
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class BlendedShare:
    """A two-party Democratic share that is part count and part model.

    `method` says which, and it is the field a renderer must branch on:

      "reported"  every ballot in this figure was counted. Not a model at all --
                  it belongs in the reported party columns, not here.
      "blend"     part counted, part modelled. `measured_fraction` says how much,
                  and the band is the model's error applied ONLY to the modelled
                  part.
      "model"     nothing was counted. This is the pure estimate, on exactly the
                  terms docs/party-estimate.md sets for every other state.

    The band is the reason to do this at all. A pure model carries +/-5 points
    because that is the measured structural error of standing in for a party
    split with a model. Count 74% of the ballots and the model is only standing
    in for the other 26%, so the band is +/-1.3. It shrinks in proportion to what
    is measured, which is a fact about the arithmetic and not a claim about the
    model getting better.
    """

    dem_share: float
    dem_lo: float
    dem_hi: float
    measured_fraction: float
    method: str
    measured_dem_share: float | None
    modelled_dem_share: float | None

    @property
    def modelled_fraction(self) -> float:
        return 1.0 - self.measured_fraction

    @property
    def band_half_width(self) -> float:
        return (self.dem_hi - self.dem_lo) / 2

    @property
    def margin(self) -> float:
        """Democratic minus Republican, two-party. Positive is a D lead."""
        return 2 * self.dem_share - 1

    def label(self) -> str:
        if self.method == "reported":
            return "100% of these ballots are counted"
        if self.method == "model":
            return "0% of these ballots are counted; all of it is modelled"
        return (
            f"{100 * self.measured_fraction:.0f}% of these ballots are counted, "
            f"{100 * self.modelled_fraction:.0f}% is modelled"
        )


def blend_party_share(
    *,
    measured_dem: int | None,
    measured_rep: int | None,
    measured_fraction: float,
    modelled_dem_share: float | None,
    model_error: float = MODEL_ERROR,
) -> BlendedShare | None:
    """Combine a counted party split with a modelled one, honestly banded.

        share = f * measured_share + (1 - f) * modelled_share
        band  = model_error * (1 - f)

    `f` is the share of the ballots that were actually counted. The band is the
    model's error applied only to the fraction the model is responsible for --
    at f=0 it is the full +/-5 points every other modelled state carries, at
    f=1 it is zero and the answer is not a model at all.

    Returns None rather than a number when the inputs cannot support one: no
    modelled share for an uncovered remainder, no two-party ballots in the
    counted part, or a fraction outside [0, 1]. Absence of data is not a value.
    """
    if not 0.0 <= measured_fraction <= 1.0:
        raise ValueError(f"measured_fraction {measured_fraction!r} is not a share")

    two_party = (measured_dem or 0) + (measured_rep or 0)
    measured_share = (measured_dem / two_party) if (two_party and measured_dem is not None) else None

    if measured_fraction > 0 and measured_share is None:
        # We claim to have counted some of it and have no split to show for it.
        return None
    if measured_fraction < 1.0 and modelled_dem_share is None:
        # Nothing to stand in for the uncounted remainder. Never assume 50/50.
        return None

    if measured_fraction >= 1.0:
        share, method = float(measured_share), "reported"
    elif measured_fraction <= 0.0:
        share, method = float(modelled_dem_share), "model"
    else:
        share = (
            measured_fraction * measured_share
            + (1.0 - measured_fraction) * modelled_dem_share
        )
        method = "blend"

    half = model_error * (1.0 - measured_fraction)
    return BlendedShare(
        dem_share=share,
        dem_lo=max(0.0, share - half),
        dem_hi=min(1.0, share + half),
        measured_fraction=float(measured_fraction),
        method=method,
        measured_dem_share=measured_share,
        modelled_dem_share=modelled_dem_share,
    )
