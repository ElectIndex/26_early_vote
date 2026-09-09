"""The EAC's Election Administration and Voting Survey — every state at once.

One 41 MB national CSV, published more than a year after the election, in which
each state's own election administrators report their own counts. It is the only
source in this repo that answers for states we have never written a line of code
for.

WHAT IT IS AND IS NOT
---------------------
It is NOT a tracker. There is no curve here and there cannot be: EAVS is a
post-election administrative census of the whole election, so every row it
produces lands on `days_to_election` 0. It supplies ENDPOINTS — the denominator
behind "share of its 2024 early vote" — and nothing else.

It is NOT the state's own file either, which is the whole reason
`schema.TIER_SURVEY` exists. Three separate pieces of archaeology (Arizona,
Montana, Idaho) reached this survey independently and all three hit the same
wall: `ladder.py` stamps provenance FROM THE ADAPTER, so EAVS rows read through
`az.py` would publish as `az-sos` at tier 1. Two of the three refused to ship
rather than tell that lie. `schema.py` has the full reasoning for the rung.

⚠️ THIS MODULE MUST NEVER ENTER `registry.FALLBACKS`
----------------------------------------------------
`fetch()` raises `NotYetPublished`, and by Rule 1 that STOPS the ladder. A rung
that can never answer a live cycle, sitting above the aggregator, would end the
walk before the aggregator was ever asked — for every state, every day. It is
reachable only through `registry.history_ladder()`, which `backfill` uses and
`ingest` does not.

WHY THE COLUMNS ARE CHECKED PER STATE AND NOT TRUSTED
-----------------------------------------------------
Every state answers the same questionnaire and they do not all answer it. Across
the 2024 release the two columns this module reads are complete for some states,
sentinel-filled for others, and — the case that actually bites — complete for one
and absent for the other:

    MT   C1b complete in 56 counties, F1f a sentinel in ALL 56
    NJ   C1b complete in 21 counties, F1f a sentinel in all 21
    MS   both columns sentinel in all 82
    CA   C1b complete, F1f missing in 3 of 58

Montana is the instructive one. Its mail column is real and its in-person column
is not, so an unguarded read would publish 432,394 as Montana's 2024 early vote
when the true figure is 432,394 PLUS an unreported number. That total is a
DENOMINATOR, and a denominator that is too small inflates every percentage
computed against it for the whole 2026 season. `parse` therefore refuses a
statewide row unless EVERY jurisdiction answered BOTH columns.
"""

from __future__ import annotations

import csv
import io
import logging
import re
import zipfile
from datetime import date

from ..calendar import election_date
from ..schema import TIER_SURVEY, CountyDay, Provenance, StateDay
from . import _fips
from ._net import Missing, get
from .base import Adapter, FetchResult, NotYetPublished, SchemaDrift, SourceError

log = logging.getLogger(__name__)

#: The EAC's index of every EAVS release. Scraped rather than pinned because the
#: file it points at is REPLACED: 2024 has shipped twice already (V1 2025-06,
#: V2 2026-02 with an errata note) and a pinned V1 URL answers 200 forever.
INDEX = "https://www.eac.gov/research-and-data/datasets-codebooks-and-surveys"
HOST = "https://www.eac.gov"

#: `<cycle>_EAVS_for_Public_Release_nolabel_V<n>_csv.zip`, as the index links it.
#: The `nolabel` build has bare question codes as headers; the labelled build
#: repeats the whole question text and is 30x the size.
LINK = re.compile(
    r'href="([^"]*?(\d{4})_EAVS_for_Public_Release_nolabel[^"]*?\.zip)"', re.I
)

#: The version token in that filename, used to pick the NEWEST release.
VERSION = re.compile(r"_V(\d+(?:\.\d+)*)", re.I)

#: The columns this reads, with the codebook's own names
#: (2024_EAVS_Codebook.xlsx, fetched 2026-09-09):
#:
#:     C1b   "Mail Returned By Voters Total"
#:     F1f   "In Person Early Voting"
#:     F1a   "Total Voters"
MAIL_RETURNED = "C1b"
INPERSON = "F1f"
TOTAL_VOTERS = "F1a"
STATE_COL = "State_Abbr"
FIPS_COL = "FIPSCode"
JURISDICTION = "Jurisdiction_Name"

#: EAVS's own codes for "did not answer" and "does not apply". Blank is the
#: third. All three are `None` and NEVER 0 -- nationally 337 jurisdictions
#: answer -99 for in-person early voting, and writing 0 for those would publish
#: "no one voted early" for a county that did not answer the question.
NOT_A_COUNT = frozenset({"-88", "-99", ""})

#: What these rows are stamped with. Never a state's own `<st>-sos`.
NAME = "eac-eavs"

#: The download is 2.1 MB compressed. A short body is a wall or an error page.
MIN_BYTES = 500_000


def release(index: bytes, cycle: int) -> str:
    """The NEWEST EAVS release URL for `cycle`, from the EAC's own index page.

    Verified live 2026-09-09: the page links 2020, 2022 and 2024, and the 2022
    one spells its extension `_CSV.zip` where 2024's is `_csv.zip` -- which is
    why the pattern is case-insensitive and why a case-sensitive one silently
    loses a whole cycle.
    """
    found: list[tuple[list[int], str]] = []
    for href, year in LINK.findall(index.decode("utf-8", "replace")):
        if int(year) != cycle:
            continue
        hit = VERSION.search(href)
        version = [int(p) for p in hit.group(1).split(".")] if hit else [0]
        found.append((version, href))
    if not found:
        raise NotYetPublished(f"the EAC has published no EAVS release for {cycle}")
    _, href = max(found)
    return href if href.startswith("http") else HOST + href


def rows(body: bytes, state: str) -> list[dict[str, str]]:
    """One state's jurisdiction rows out of the national EAVS zip.

    The member is found by SUFFIX, never by name: the 2024 V1 zip wraps its CSV
    in a directory and the V2 zip does not, so a name match would have broken on
    the release that replaced it.
    """
    try:
        archive = zipfile.ZipFile(io.BytesIO(body))
    except zipfile.BadZipFile as exc:
        raise SchemaDrift(f"{state}: the EAVS download is not a readable zip") from exc
    members = [n for n in archive.namelist() if n.lower().endswith(".csv")]
    if len(members) != 1:
        raise SchemaDrift(f"{state}: the EAVS zip holds {members[:5]}, not one CSV")

    with archive.open(members[0]) as handle:
        reader = csv.DictReader(
            io.TextIOWrapper(handle, encoding="utf-8-sig", errors="replace")
        )
        required = {STATE_COL, FIPS_COL, JURISDICTION,
                    MAIL_RETURNED, INPERSON, TOTAL_VOTERS}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise SchemaDrift(f"{state}: EAVS is missing columns {sorted(missing)}")
        return [row for row in reader
                if (row.get(STATE_COL) or "").strip().upper() == state.upper()]


def count(raw: str | None, what: str) -> int | None:
    """One EAVS cell as a count, or `None` when it is one of EAVS's own codes."""
    text = (raw or "").strip()
    if text in NOT_A_COUNT:
        return None
    try:
        value = int(text)
    except ValueError as exc:
        raise SchemaDrift(f"EAVS {what} is {raw!r}, which is not a count") from exc
    if value < 0:
        # A negative that is not one of the two codes we know is a code we do
        # not know. Rule 3: do not bucket an unrecognised value.
        raise SchemaDrift(f"EAVS {what} is {value}, an unrecognised code")
    return value


def _counties(state: str) -> dict[str, str]:
    """{5-digit FIPS: canonical county name} for one state."""
    table: dict[str, str] = {}
    for name in _fips.names(state):
        hit = _fips.lookup(state, name)
        if hit is not None:
            table[hit[0]] = hit[1]
    return table


def parse(records: list[dict[str, str]], state: str, cycle: int,
          day: date) -> FetchResult:
    """One state's county FINAL for one cycle. ONE ROW PER COUNTY, dated Election Day.

    ⚠️ JURISDICTIONS ARE MATCHED BY FIPS, NOT BY NAME, and that is not a style
    preference -- it is Rule 4, and two states prove why in the same file. New
    Mexico's `DONA ANA COUNTY` has lost its ñ somewhere upstream and matches no
    census name, while its code 35013 is exactly right. Missouri's
    `KANSAS CITY CITY` carries 2938000000, which is a PLACE code and not a county
    at all: Kansas City spans four counties and runs its own election board, so
    its ballots belong to no single county and the name would have been the only
    thing to notice by.

    Two independent completeness questions, answered separately:

    * COUNTY rows need every jurisdiction to sit inside exactly one county. One
      that does not (Kansas City) means the surrounding counties are each short
      an unknown number of ballots, so NO county rows ship for that state --
      silently understating Jackson County is worse than having no Jackson row.
    * The STATE row needs every jurisdiction to have answered BOTH columns.
      Kansas City still counts toward that sum, so Missouri gets a statewide
      total and no counties. Montana, where all 56 counties leave in-person
      blank, gets neither.
    """
    result = FetchResult()
    known = _counties(state)
    totals = {"mail": 0, "inperson": 0, "voted": 0}
    every_column_answered = True
    any_usable_total = False
    unplaceable: list[str] = []
    seen: set[str] = set()

    for row in records:
        code = (row.get(FIPS_COL) or "").strip()
        name = (row.get(JURISDICTION) or "").strip()
        fips = code[:5]

        mail = count(row.get(MAIL_RETURNED), f"{state} {name} {MAIL_RETURNED}")
        early = count(row.get(INPERSON), f"{state} {name} {INPERSON}")
        voted = count(row.get(TOTAL_VOTERS), f"{state} {name} {TOTAL_VOTERS}")

        # ⚠️ NOT `(mail or 0) + (early or 0)`. A jurisdiction that did not report
        # one half has an UNKNOWN early vote, not a half-sized one.
        total = None if mail is None or early is None else mail + early
        if total is None:
            every_column_answered = False
        else:
            any_usable_total = True
            if voted is not None and total > voted:
                # EAVS's own identity: early ballots are a subset of all ballots.
                # A violation means a column has started counting something else.
                raise SchemaDrift(
                    f"{state}: {name} reports {mail} mail + {early} early = "
                    f"{total} against {voted} total voters"
                )
            totals["mail"] += mail          # type: ignore[operator]
            totals["inperson"] += early     # type: ignore[operator]
            totals["voted"] += total

        canonical = known.get(fips)
        if canonical is None or fips in seen:
            unplaceable.append(f"{name} ({code})")
            continue
        seen.add(fips)


        result.county_rows.append(CountyDay(
            cycle=cycle, state=state, county_fips=fips, day=day,
            county_name=canonical,
            ballots_total=total,
            # EAVS is one post-election figure. There is no previous day, so
            # there is no "new today" -- blank, never 0.
            ballots_new=None,
            mail_returned=mail,
            inperson=early,
            # EAVS carries no party registration for anyone. Never 0.
            party_dem=None, party_rep=None, party_oth=None, party_npa=None,
        ))

    if not records:
        raise SchemaDrift(f"{state}: EAVS carried no {state} jurisdictions")

    missing = set(known) - seen
    if unplaceable or missing:
        log.warning(
            "%s: EAVS county rows withheld -- %d jurisdiction(s) outside any "
            "county (%s), %d county/counties absent",
            state, len(unplaceable), ", ".join(unplaceable[:3]) or "none",
            len(missing),
        )
        result.county_rows.clear()

    # ⚠️ A COUNTY THAT ANSWERED HALF STILL GETS ITS ROW. Its `ballots_total` is
    # blank -- never the mail half on its own -- but `mail_returned` is a real
    # number the state really reported and throwing it away buys nothing.
    #
    # The state where that goes wrong is the one where EVERY county answered
    # half. Montana reports mail in all 56 counties and in-person in none, so
    # the rule above would hand back a complete-looking 56-row Montana file in
    # which every early-vote total is empty -- and `backfill`, which only asks
    # whether a result is truthy, would record Montana as a state WITH history
    # and never fall through to a source that can actually answer it.
    #
    # So: no usable total anywhere means this survey cannot answer for this
    # state, and it says so by returning nothing at all.
    # ⚠️ Read off the SURVEY, not off `result.county_rows`. Those have already
    # been cleared above when a jurisdiction could not be placed in a county,
    # and testing the emptied list refused Idaho outright the moment a single
    # jurisdiction carried an unusable code -- state row and all.
    if not any_usable_total:
        log.warning("%s: EAVS has no jurisdiction reporting both columns; "
                    "nothing published, the ladder continues", state)
        return FetchResult()

    if not every_column_answered:
        log.warning("%s: EAVS leaves a column blank somewhere; no statewide "
                    "total (a short denominator inflates every 2026 reading)",
                    state)
        return result

    if totals["inperson"] == 0 and totals["mail"] > 0 and len(records) > 1:
        # Not an error and not refused: a state can legitimately be all-mail
        # (Washington reports 171 in-person early votes statewide). But a column
        # that is exactly 0 in EVERY jurisdiction of a state that runs in-person
        # absentee is more likely "we did not separate it" reported as a number.
        log.warning("%s: EAVS in-person early is 0 in all %d jurisdictions; "
                    "published as reported", state, len(records))

    result.state_rows.append(StateDay(
        cycle=cycle, state=state, day=day,
        ballots_total=totals["voted"],
        ballots_new=None,
        # ⚠️ C1a ("Mail Transmitted") is NOT published as mail_requested. In
        # Idaho it disagrees with the state's own dashboard by 4.4% where the
        # other two columns agree to 0.2%, and nothing in the codebook explains
        # the gap. A number that cannot be reconciled against the state that
        # reported it is a blank, not a row.
        mail_requested=None,
        mail_returned=totals["mail"],
        inperson=totals["inperson"],
        party_dem=None, party_rep=None, party_oth=None, party_npa=None,
    ))
    return result


def history(state: str, cycle: int) -> FetchResult:
    """Fetch, parse and STAMP one state's EAVS county final for a past cycle."""
    if cycle >= date.today().year:
        raise NotYetPublished(
            f"{state}: {cycle} is not an archived cycle -- EAVS is a "
            "post-election survey published over a year afterwards"
        )
    try:
        index = get(INDEX, state=state, filename="eac_datasets.html", min_bytes=4096)
    except Missing as exc:
        raise SourceError(f"{state}: the EAC dataset index is gone: {exc}") from exc

    url = release(index, cycle)
    try:
        # An archived, dated, immutable release: safe to serve from cache, and
        # the alternative is a 2 MB download per state per backfill run.
        body = get(url, state=state, filename=f"eavs_{cycle}.zip",
                   min_bytes=MIN_BYTES, use_cache=True)
    except Missing as exc:
        raise SourceError(f"{state}: the EAC links {url} and it is not "
                          f"there: {exc}") from exc

    result = parse(rows(body, state), state, cycle, election_date(cycle))
    result.stamp(Provenance(tier=TIER_SURVEY, name=NAME))
    return result


class EAVSAdapter(Adapter):
    """The survey rung. HISTORY ONLY -- see the module docstring.

    ⚠️ `fetch()` raising NotYetPublished is what keeps this out of the live
    walk, and is also exactly why this class must never be added to
    `registry.FALLBACKS`: there, that same exception would stop the ladder
    above the aggregator for every state on every run.
    """

    name = NAME
    tier = TIER_SURVEY

    def fetch(self, cycle: int, as_of: date) -> FetchResult:
        raise NotYetPublished(
            f"{self.state}: EAVS is a post-election survey and has nothing to "
            f"say about {cycle} while it is being voted"
        )

    def fetch_history(self, cycle: int) -> FetchResult:
        return history(self.state, cycle)
