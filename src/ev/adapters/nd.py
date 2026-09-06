"""North Dakota: the SoS Voter Information Portal's absentee/early-voting page.

`https://vip.sos.nd.gov/abev.aspx?eid=<id>` is a WebForms page carrying, for one
election, a statewide Category/Value table and a county panel driven by a
dropdown. `docs/coverage-research.md` rejected it as "an HTML table needing 53
ASP.NET postbacks for county detail, [with] an opaque election id". Both halves
of that are true and neither is disqualifying:

* **The postbacks are STATELESS.** One `__VIEWSTATE` / `__EVENTVALIDATION` pair,
  scraped once from the GET, can be replayed for every county with no cookies
  and no session. Fifty-three POSTs cost about 1.6 MB and half a minute.
* **The county numbers reconcile to the state's own totals exactly.** A full
  harvest of the 2024 general sums to 95,908 sent / 91,556 returned / 99,007
  early -- byte-for-byte the statewide figures on the same page. So the county
  rows are not an approximation of the state row; they are the state row.

**North Dakota does not register voters -- at all, not just not by party.** Every
`party_*` field on every row here is None, and always will be.

THE ELECTION ID IS THE WHOLE RISK
---------------------------------
`eid` is an opaque integer with no dropdown, and the page will render *any*
integer you hand it, zeroed, rather than refusing. The 2026 PRIMARY is 346 and
the 2026 GENERAL is 348 -- two apart -- and on 2026-09-06 the only `abev` link
anywhere on `sos.nd.gov` still pointed at the primary. Publishing 346 as the
general would be the Montana failure from `docs/coverage-research.md` with an
off-by-two instead of a stale cache.

So the id is never trusted on its own. `identify()` reads
`candidatelist.aspx?eid=<id>`, whose `lblFormHeader` names the election in
words -- "2026 General Election Contest/Candidate List" -- and the id is used
only if that says this cycle's general. A wrong or retired id is refused, and
`election_id()` then walks a bounded range upward from the pinned one looking
for the right header -- an id that does not exist redirects to the portal's
error page, which carries no header at all, so it simply does not match.

EARLY VOTING IS OPTIONAL IN NORTH DAKOTA, AND THE PAGE SHOWS IT BY OMISSION
--------------------------------------------------------------------------
Counties choose whether to run early voting, and the county panel simply leaves
the `lblEarlyVotes` block out for a county that does not. In the 2024 general
that is 46 of the 53 counties. An omission is normally "not reported" -- but
here the page's own statewide `Early Voting Turnout` equals the sum of the seven
counties that DO report, to the ballot, which proves the other 46 are zero
rather than unknown.

That proof is re-run on every fetch rather than assumed: if the counties that
report early votes add up to the statewide figure, the silent counties are
published as a real `0`; if they do not, every silent county's `inperson` is
None and so is its `ballots_total`, because half a total is not a total. See
`_settle_early`.

`ballots_total` is `returned + early`, which is North Dakota's own definition:
its `Total Ballots Cast prior to Election Day` row is exactly that sum
(91,556 + 99,007 = 190,563 in 2024, 70,064 + 36,513 = 106,577 in 2022).

THE PAGE CARRIES NO DATE
------------------------
There is no as-of stamp anywhere in the HTML. The XLSX export has one, but it is
the server's request clock -- two exports a minute apart read 11:10:07 and
11:11:08 -- so it dates the download, not the data. Rows are therefore stamped
with the run's own `as_of`, and a backfill is stamped Election Day, because what
an archived `eid` serves today is that election's final position rather than a
curve.
"""

from __future__ import annotations

import html as _html
import logging
import re
from datetime import date

from ..calendar import election_date
from ..schema import TIER_SCRAPER, CountyDay, StateDay
from . import _fips
from ._net import DEFAULT_HEADERS, SESSION, Missing, cache_path, get, looks_like_html
from .base import Adapter, FetchResult, NotYetPublished, SchemaDrift, SourceError

log = logging.getLogger(__name__)

BASE = "https://vip.sos.nd.gov/abev.aspx"
CANDIDATE_LIST = "https://vip.sos.nd.gov/candidatelist.aspx"

#: Election ids, VERIFIED live 2026-09-06 by reading `candidatelist.aspx`'s own
#: `lblFormHeader` for each -- and re-verified on every fetch, because the id is
#: the one thing here that can silently be about a different election.
#:
#:   326 -> "2022 General Election ..."   (abev: 76,034 sent / 106,577 cast)
#:   333 -> "2024 General Election ..."   (abev: 95,908 sent / 190,563 cast)
#:   348 -> "2026 General Election ..."   (abev: 63 sent, 0 returned, today)
#:
#: 346 is the 2026 PRIMARY and must never be used. 349 and above do not exist:
#: candidatelist 302s to the portal's error page, which names no election.
ELECTION_IDS: dict[int, int] = {2022: 326, 2024: 333, 2026: 348}

#: How far past a pinned id to look if it stops naming the right election.
ID_SEARCH_SPAN = 12

#: The header `candidatelist.aspx` prints for a cycle's general.
_FORM_HEADER = re.compile(
    r'id="[^"]*lblFormHeader"[^>]*>\s*([^<]*?)\s*<', re.I
)

#: Statewide Category/Value rows, by the span id that carries each value.
STATE_SENT = "lblTotalSent"
STATE_RETURNED = "lblTotalReturned"
STATE_EARLY = "lblEarlyVoting"
STATE_CAST = "lblBallotsCast"
STATE_LABELS = (STATE_SENT, STATE_RETURNED, STATE_EARLY, STATE_CAST)

#: The county panel's three spans. `lblEarlyVotes` is ABSENT, not zero, for a
#: county that runs no early voting -- see the module docstring.
COUNTY_SENT = "lblSent"
COUNTY_RETURNED = "lblReturned"
COUNTY_EARLY = "lblEarlyVotes"
COUNTY_HEADER = "lblHeader"

_COUNTY_OPTION = re.compile(r'<option value="(\d{2})">([^<]+)</option>')
_HIDDEN = r'id="{name}"[^>]*value="([^"]*)"'
#: The dropdown whose postback swaps the county panel.
COUNTY_CONTROL = "ctl00$ContentPlaceHolder1$ddlCounty"

EXPECTED_COUNTIES = len(_fips.CENSUS_COUNTIES["ND"])


def _span(markup: str, name: str) -> str | None:
    """The text of the span whose id ENDS with `name`, or None if absent."""
    match = re.search(
        r'<span id="[^"]*_' + re.escape(name) + r'"[^>]*>([^<]*)</span>', markup
    )
    return match.group(1) if match else None


def _int(value: str | None) -> int | None:
    """A count, or None when the page did not print one."""
    if value is None:
        return None
    text = value.strip().replace(",", "").replace("\xa0", "")
    if not text or text in {"-", "--"}:
        return None
    try:
        return int(text)
    except ValueError as exc:
        raise SchemaDrift(f"ND: {value!r} is not a ballot count") from exc


def _hidden(markup: str, name: str) -> str | None:
    match = re.search(_HIDDEN.format(name=re.escape(name)), markup)
    return _html.unescape(match.group(1)) if match else None


def identify(markup: str) -> str:
    """The election `candidatelist.aspx` says an id belongs to."""
    match = _FORM_HEADER.search(markup or "")
    if match is None:
        raise SchemaDrift("ND: candidate list carries no election header")
    return " ".join(match.group(1).split())


def is_general(header: str, cycle: int) -> bool:
    """True only for "<cycle> General Election ...".

    Anchored at the front so "2026 Primary Election" and the numbered special
    elections cannot match, and the cycle must be the one asked for.
    """
    return bool(re.match(rf"^{int(cycle)}\s+General\s+Election\b", header, re.I))


def counties(markup: str) -> list[tuple[str, str]]:
    """(dropdown value, county name) for every county on the page."""
    seen: dict[str, str] = {}
    for value, name in _COUNTY_OPTION.findall(markup):
        seen.setdefault(value, " ".join(_html.unescape(name).split()))
    return sorted(seen.items())


def parse_state(markup: str) -> dict[str, int | None]:
    """The statewide Category/Value table."""
    values = {name: _int(_span(markup, name)) for name in STATE_LABELS}
    missing = [name for name, value in values.items() if value is None]
    if missing:
        raise SchemaDrift(
            f"ND: the statewide table is missing {missing} -- the page's layout "
            "changed, or this eid is not an absentee report"
        )
    total = values[STATE_CAST]
    parts = values[STATE_RETURNED] + values[STATE_EARLY]
    if total != parts:
        # North Dakota's own arithmetic. If it stops holding, the meaning of one
        # of these rows has changed and guessing which would publish a confident
        # wrong number.
        raise SchemaDrift(
            f"ND: 'Total Ballots Cast prior to Election Day' is {total}, but "
            f"returned + early is {parts}"
        )
    return values


def parse_county(markup: str, expected: str) -> tuple[int | None, int | None, int | None]:
    """(sent, returned, early) from one county postback's panel.

    `early` is None when the county's early-vote block is absent, which is what
    North Dakota does for a county that runs no early voting. `_settle_early`
    decides whether that None is a real zero.
    """
    header = _span(markup, COUNTY_HEADER)
    if header is None:
        raise SourceError(f"ND: the postback for {expected} returned no county panel")
    if not header.lower().startswith(expected.lower()):
        # The postback is stateless, so a mismatch means the server answered for
        # a different county than we asked for -- every subsequent number would
        # be filed under the wrong FIPS.
        raise SchemaDrift(
            f"ND: asked for {expected}, the panel says {header!r}"
        )
    return (
        _int(_span(markup, COUNTY_SENT)),
        _int(_span(markup, COUNTY_RETURNED)),
        _int(_span(markup, COUNTY_EARLY)),
    )


def _settle_early(
    reported: dict[str, int | None], statewide: int | None
) -> dict[str, int | None]:
    """Decide what a county's ABSENT early-vote block means.

    North Dakota omits the block for a county that runs no early voting. If the
    counties that DO print one add up to the state's own early-voting total,
    then the silent counties cast no early ballots and a real 0 is the truthful
    value -- verified for the 2024 general, where seven counties reporting sum
    to 99,007 and the state prints 99,007.

    If they do not add up, something is unreported rather than zero, and every
    silent county keeps None. A gap is recoverable; a fabricated zero is not.
    """
    present = [v for v in reported.values() if v is not None]
    if statewide is not None and sum(present) == statewide:
        return {k: (0 if v is None else v) for k, v in reported.items()}
    log.warning(
        "ND: counties reporting early votes sum to %d against a statewide %s; "
        "leaving the silent counties blank rather than assuming zero",
        sum(present), statewide,
    )
    return dict(reported)


def _total(returned: int | None, early: int | None) -> int | None:
    """Ballots cast before Election Day: North Dakota's own definition."""
    if returned is None or early is None:
        return None
    return returned + early


def build(
    statewide: dict[str, int | None],
    rows: dict[str, tuple[str, int | None, int | None, int | None]],
    cycle: int,
    day: date,
) -> FetchResult:
    """Assemble canonical rows from one statewide table and its county panels."""
    early = _settle_early({fips: r[3] for fips, r in rows.items()},
                          statewide[STATE_EARLY])

    result = FetchResult()
    result.state_rows.append(StateDay(
        cycle=cycle, state="ND", day=day,
        # The page's own total, never our sum -- though the two agree exactly.
        ballots_total=statewide[STATE_CAST],
        # The page is a cumulative position with no daily column of its own.
        ballots_new=None,
        mail_requested=statewide[STATE_SENT],
        mail_returned=statewide[STATE_RETURNED],
        inperson=statewide[STATE_EARLY],
        # North Dakota has no voter registration at all. Never 0.
        party_dem=None, party_rep=None, party_oth=None, party_npa=None,
    ))

    for fips, (name, sent, returned, _) in sorted(rows.items()):
        result.county_rows.append(CountyDay(
            cycle=cycle, state="ND", county_fips=fips, day=day,
            county_name=name,
            ballots_total=_total(returned, early[fips]),
            ballots_new=None,
            mail_returned=returned,
            inperson=early[fips],
            party_dem=None, party_rep=None, party_oth=None, party_npa=None,
        ))
        if sent is None:
            log.warning("ND: %s printed no ballots-sent figure", name)
    return result


class NDScraper(Adapter):
    """Tier 1 for North Dakota: the SoS voter portal's absentee/early page."""

    state = "ND"
    name = "nd-sos"
    tier = TIER_SCRAPER

    # ---- the election id ---------------------------------------------------
    def _header(self, eid: int, *, use_cache: bool) -> str | None:
        """`candidatelist.aspx`'s name for an id, or None if it does not exist.

        A missing id answers 302 to the portal's error page rather than 404, so
        "no such election" arrives as a 200 with no header element and is read
        as absence.

        A transport failure is NOT absence and is deliberately allowed to
        propagate: if the portal is unreachable we do not know which election is
        which, and swallowing that here would turn "we could not look" into
        `NotYetPublished`, which STOPS the ladder. See rule 2 in CLAUDE.md.
        """
        try:
            body = get(
                f"{CANDIDATE_LIST}?eid={eid}", state="ND",
                filename=f"candidatelist_{eid}.html",
                use_cache=use_cache, min_bytes=2048,
            )
        except Missing:
            return None
        try:
            return identify(body.decode("utf-8", errors="replace"))
        except SchemaDrift:
            return None

    def election_id(self, cycle: int, *, use_cache: bool = False) -> int:
        """The id of `cycle`'s November general, checked against its own name.

        The pinned id is tried first and ACCEPTED ONLY if the portal agrees it
        is that cycle's general. Otherwise the search walks upward -- ids are
        issued in order, and the 2026 general (348) is two past the 2026 primary
        (346) -- and gives up rather than guessing.
        """
        pinned = ELECTION_IDS.get(int(cycle))
        problems: list[str] = []
        if pinned is not None:
            header = self._header(pinned, use_cache=use_cache)
            if header and is_general(header, cycle):
                return pinned
            problems.append(f"eid {pinned} is {header!r}")

        start = (pinned or max(ELECTION_IDS.values())) + 1
        for eid in range(start, start + ID_SEARCH_SPAN):
            header = self._header(eid, use_cache=use_cache)
            if header is None:
                continue
            if is_general(header, cycle):
                log.warning(
                    "ND: pinned election id for %s was wrong; using %d (%s)",
                    cycle, eid, header,
                )
                return eid
        raise NotYetPublished(
            f"ND: the voter portal lists no {cycle} general election "
            f"({'; '.join(problems) or 'nothing in the id range'})"
        )

    # ---- the page ----------------------------------------------------------
    def _page(self, eid: int, *, use_cache: bool) -> str:
        body = get(f"{BASE}?eid={eid}", state="ND",
                   filename=f"abev_{eid}.html", use_cache=use_cache, min_bytes=4096)
        markup = body.decode("utf-8", errors="replace")
        if not looks_like_html(body):
            raise SourceError(f"ND: {BASE}?eid={eid} did not return a page")
        return markup

    def _county(
        self, eid: int, markup: str, value: str, *, use_cache: bool = False
    ) -> str:
        """One county's panel, by replaying the page's own postback tokens.

        No session and no cookies: the tokens from the initial GET validate on
        their own, which is what makes 53 counties cheap. `__VIEWSTATE`,
        `__EVENTVALIDATION` and `__EVENTTARGET` are all required -- dropping any
        one of them answers the portal's error page with HTTP 200.

        Every response is mirrored under `cache/nd/` like a `_net.get` would be,
        so a parse can be re-run offline instead of putting 53 more POSTs
        through a county election site. `use_cache` serves that copy back, which
        is only ever passed for a past cycle's settled numbers.
        """
        path = cache_path("ND", f"abev_{eid}_county_{value}.html")
        if use_cache and path.exists() and path.stat().st_size >= 4096:
            return path.read_text(encoding="utf-8", errors="replace")

        form = {
            "__EVENTTARGET": COUNTY_CONTROL,
            "__EVENTARGUMENT": "",
            "__VIEWSTATE": _hidden(markup, "__VIEWSTATE"),
            "__VIEWSTATEGENERATOR": _hidden(markup, "__VIEWSTATEGENERATOR") or "",
            "__EVENTVALIDATION": _hidden(markup, "__EVENTVALIDATION"),
            COUNTY_CONTROL: value,
        }
        if not form["__VIEWSTATE"] or not form["__EVENTVALIDATION"]:
            raise SchemaDrift("ND: the absentee page carries no WebForms tokens")
        url = f"{BASE}?eid={eid}"
        try:
            response = SESSION.post(
                url, data=form, timeout=60,
                headers={**DEFAULT_HEADERS, "Referer": url,
                         "Content-Type": "application/x-www-form-urlencoded"},
            )
        except Exception as exc:  # noqa: BLE001 -- requests raises several types
            raise SourceError(f"ND: county postback failed: {exc}") from exc
        if response.status_code != 200:
            raise SourceError(
                f"ND: county postback returned HTTP {response.status_code}"
            )
        path.write_bytes(response.content)
        return response.text

    def _harvest(self, cycle: int, day: date, *, use_cache: bool) -> FetchResult:
        eid = self.election_id(cycle, use_cache=use_cache)
        markup = self._page(eid, use_cache=use_cache)
        statewide = parse_state(markup)

        if not any(statewide[name] for name in
                   (STATE_SENT, STATE_RETURNED, STATE_EARLY, STATE_CAST)):
            # The portal renders a zeroed page for an election it has not begun
            # loading, which is indistinguishable from one where nothing has
            # happened yet. Either way there is nothing to plot, and calling it
            # data would start the series with a fabricated flat line.
            raise NotYetPublished(
                f"ND: the {cycle} general (eid {eid}) is listed but carries no "
                "ballots yet"
            )

        options = counties(markup)
        if len(options) != EXPECTED_COUNTIES:
            raise SchemaDrift(
                f"ND: the county dropdown lists {len(options)} of "
                f"{EXPECTED_COUNTIES} counties"
            )

        rows: dict[str, tuple[str, int | None, int | None, int | None]] = {}
        unknown: list[str] = []
        for value, name in options:
            hit = _fips.lookup("ND", name)
            if hit is None:
                unknown.append(name)
                continue
            fips, canonical = hit
            sent, returned, early = parse_county(
                self._county(eid, markup, value, use_cache=use_cache), name
            )
            rows[fips] = (canonical, sent, returned, early)

        if unknown:
            raise SchemaDrift(f"ND: unrecognised county names {sorted(set(unknown))}")
        if len(rows) != EXPECTED_COUNTIES:
            raise SchemaDrift(
                f"ND: harvested {len(rows)} of {EXPECTED_COUNTIES} counties"
            )
        return build(statewide, rows, cycle, day)

    # ---- the contract ------------------------------------------------------
    def fetch(self, cycle: int, as_of: date) -> FetchResult:
        """Today's cumulative position.

        The page carries no as-of date of its own -- see the module docstring --
        so the run's own date is what stamps the rows.
        """
        return self._harvest(cycle, as_of, use_cache=False)

    def fetch_history(self, cycle: int) -> FetchResult:
        """A past cycle's FINAL position, not a curve.

        An archived `eid` still answers, and still answers with county detail --
        `eid=326` and `eid=333` both do today -- but it serves the election's
        settled numbers, not a series. So a backfill is one row per county dated
        Election Day. (The Wayback Machine does hold dated captures of this page
        through October 2022 and October 2024, which would rebuild the curve;
        that is a bigger job and is recorded in docs/coverage-research.md rather
        than wired in here.)
        """
        if int(cycle) not in ELECTION_IDS:
            raise NotYetPublished(f"ND: no known election id for {cycle}")
        return self._harvest(cycle, election_date(cycle), use_cache=True)
