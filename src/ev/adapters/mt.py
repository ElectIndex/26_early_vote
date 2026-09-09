"""Montana: the Secretary of State's absentee-ballot Tableau dashboard.

`docs/coverage-research.md` found this source, confirmed it works with one GET,
and then **rejected it**:

> Rejected because the CSV carries no election identifier and no as-of date, and
> the dashboard holds the LAST election's numbers between cycles -- today it is
> serving the June 2026 primary (514,152 sent / 268,125 received). A run in
> September would stamp primary absentee totals with today's date under the 2026
> general. Only the PDF export carries the provenance ("2026 Montana Primary
> Election...", "Compiled On 6/15/2026"), and this pipeline has no PDF
> dependency. **Buildable the moment either a PDF parser exists or the CSV grows
> a date column.**

A PDF parser exists. `pypdf` is a declared dependency and `ia.py` reads Iowa's
absentee report with it, so the condition the survey set is met, and this module
is the build it asked for.

--------------------------------------------------------------------------
TWO EXPORTS OF ONE VIEW: COUNTS FROM THE CSV, PROVENANCE FROM THE PDF
--------------------------------------------------------------------------

Tableau Server renders the same view in either format from the same URL stem.
Both were VERIFIED live 2026-09-06:

* `.../AbsenteeDash.csv` -> **200**, `text/csv`, 5,518 B, 171 rows:
  `County,Measure Names,Measure Values` over 56 counties plus an `All` row, with
  Ballots Sent / Ballots Received / % Received.
* `.../AbsenteeDash.pdf` -> **200**, `application/pdf`, 285,506 B, whose first
  line is the dashboard's title -- `2026 Montana Primary Election Absentee
  Ballot Counts` -- and which ends `Compiled On 6/15/2026 6:55:19 AM`.

The CSV is the numbers; the PDF is the label on them. The PDF is fetched FIRST
so its stamp can never be newer than the counts it is describing, and the whole
run is refused unless the title names **this cycle's GENERAL election**. Today
that means Montana reports `NotYetPublished`, which is the correct and honest
state of a dashboard still showing the June primary -- and precisely the failure
the survey was worried about.

**A bounded, documented risk:** the two exports are separate requests, so a
Tableau extract refresh landing between them would attach a stamp from one
refresh to counts from the next. Montana's compile stamps are daily (`Updates
Daily`, 06:55), so the window is seconds wide once a day and the worst case is a
one-day mislabel, not a wrong number. Reading both from one response is not
possible: Tableau's CSV export carries no title or timestamp, and the workbook
exposes no other sheet (`.../AbsenteeBallots/Sheet1.csv` -> **404**).

--------------------------------------------------------------------------
THE TITLE IS READ WITH EVERY SPACE REMOVED, AND THAT IS LOAD-BEARING
--------------------------------------------------------------------------

Tableau draws this PDF glyph by glyph with explicit positioning, so the spaces
in the extracted text are a text-extractor *heuristic* rather than characters in
the file. `pyproject.toml` pins only `pypdf>=5.0`, and three versions read the
same 285,506 bytes three different ways:

    pypdf 6.16.1   "2026 Montana Primary Election Absentee Ballot Counts"
    pypdf 6.18.0   "2026 M o n tan a P rim ary E lectio n  A b sen tee ..."
    pypdf 5.1.0    "2 0 2 6  M o n ta n a  P rim a ry  E le c tio n  ..."

The original patterns joined the words with `\\s+`, which matches the first line
and neither of the others. GitHub Actions resolved pypdf 6.18.0, so from
2026-09-06 `mt-sos` raised SchemaDrift on every published ingest run -- ten
consecutive `output/ev_status.json` commits -- and the tests workflow was red on
main with the same two failures, while the same code on a dev box with pypdf
6.16.1 answered correctly. Because SchemaDrift FALLS THROUGH, the visible effect
was not a loud break but Montana silently handing itself to the aggregator.

So both patterns are matched against the extract with ALL whitespace stripped
(`_compact`), which is the only reading of them that does not depend on a
version's spacing heuristic. The phrases are specific enough that nothing else
on a one-page dashboard can produce them.

--------------------------------------------------------------------------
JUDGEMENT CALLS
--------------------------------------------------------------------------

* **Montana does not register voters by party**, so every `party_*` field is
  None -- never 0. (`data/meta/states.csv` agrees: `has_party_reg` is `false`.)
  Montana's primaries are open and a voter chooses a ballot at the polls.

* **`ballots_total` and `mail_returned` are the same number, and that is
  correct.** Montana's report covers absentee ballots only. `normalize.py`
  defines `METHOD_MAIL` as "a returned/accepted **mail or absentee** ballot", so
  an absentee ballot returned over the counter at the county election office is
  a mail ballot in this vocabulary. There is no separate in-person early-voting
  figure to report, so `inperson` is None.

* **`ballots_new` is None.** The dashboard is a snapshot with no day dimension;
  the day-over-day change belongs to whatever consumes the series, not to a
  field that would claim Montana published it.

* **The county sums are checked against Montana's own `All` row** on every run
  (2026-06-15: 514,152 sent and 268,125 received both ways). A short download,
  or a county Tableau drops from the view, therefore fails loudly instead of
  publishing a statewide total that is quietly missing a county.

--------------------------------------------------------------------------
THERE IS NO 2024 ARCHIVE, AND THE ONE FINAL THAT EXISTS IS NOT PUBLISHED HERE
--------------------------------------------------------------------------

`fetch_history` refuses by name and the sweep behind it is written out there.
The short version is that a Tableau view archives as its *placeholder*: the
Secretary's own page, `sosmt.gov/elections/absentee-ballot-count/`, has 124
Wayback captures including a dense 2024-10/11 run, and the 278,890-byte
2024-11-04 capture contains the tokens `tableauPlaceholder`, `tableauViz` and
`AbsenteeDash` and **not one county number**. `AbsenteeDash.csv` and `.pdf` have
never been captured at all.

The one thing that survived the 2024 cycle is the format that PRECEDED the
dashboard, and it is in `tests/fixtures/mt/`: a real
`Absentee-Counts-By-County` workbook, `County Name | Number Sent | Number
Receive` over all 56 counties. It is the **primary**, and note what identifies
it as such --

```
Montana Absentee Ballot Counts by County
Compiled 5/16/2024 12:00:00 AM
Updated Daily
County Name   Number Sent   Number Receive
BEAVERHEAD           3437                2
```

-- a compile date and nothing else. **This family names no election.** That is
the same hole the survey rejected the CSV for, and it is why an archived file of
this shape could never be published as a general-election final on the strength
of its filename: only the Tableau PDF's title ever carried the election, and the
Tableau PDF was never archived. The fixture pins the county vocabulary against
the Secretary's own spellings (`LEWIS AND CLARK`, not Tableau's `Lewis & Clark`)
so a rename is caught, and it is the worked example of why the refusal stands.

**What DOES exist for 2024, and why it is not wired here.** The EAC's Election
Administration and Voting Survey carries all 56 Montana counties for the
November 5, 2024 general. VERIFIED 2026-09-09 by downloading and reading it:
`https://www.eac.gov/sites/default/files/2026-02/2024_EAVS_for_Public_Release
_nolabel_V2_csv.zip` (HTTP 200, 2,119,187 B) unzips to a 41,239,612-byte CSV,
of which exactly 56 rows are `State_Abbr == MT`, with no `-88`/`-99` sentinel in
any column read. Summed: `A1a` registered 800,573, `C1a` mail transmitted
503,295, `C1b` mail returned 432,394, `F1a` total voters 612,423. Those bracket
the Secretary's own 2024 press releases -- "more than 500,000 absentee ballots
provided", "nearly 425,000 received entering Election Day" -- in the right
direction.

It is deliberately NOT wired in, for the reasons `il.py` sets out at length and
which apply here unchanged: `ladder.py` stamps provenance from the adapter, so
these rows would be published as `mt-sos` tier 1 when they are a FEDERAL SURVEY
of Montana, and EAVS is national -- a new source is a repo-wide decision about a
new tier, not a Montana patch. `C1b` is also a different quantity from the
dashboard's "Ballots Received": it counts everything that arrived through the
end of the count, not what was back on a given day. The URLs and column names
are here so that decision can be made with the evidence rather than re-gathered.
"""

from __future__ import annotations

import csv
import io
import logging
import re
from datetime import date

import pypdf

from ..schema import TIER_SCRAPER, CountyDay, StateDay
from . import _fips
from ._net import Missing, get
from .base import Adapter, FetchResult, NotYetPublished, SchemaDrift, SourceError

log = logging.getLogger(__name__)

VIEW = "https://tableau-ext.mt.gov/t/SOS/views/AbsenteeBallots/AbsenteeDash"
CSV_URL = f"{VIEW}.csv"
PDF_URL = f"{VIEW}.pdf"

COUNTY = "County"
MEASURE = "Measure Names"
VALUE = "Measure Values"
HEADER = (COUNTY, MEASURE, VALUE)

SENT = "Ballots Sent"
RECEIVED = "Ballots Received"
PERCENT = "% Received"
MEASURES = (SENT, RECEIVED, PERCENT)

#: Montana's own statewide row.
ALL = "All"

#: Every Montana county. A CSV listing fewer means the view dropped one, which
#: would silently shrink the statewide total.
EXPECTED_COUNTIES = len(_fips.CENSUS_COUNTIES["MT"])

#: The census spells it "Lewis and Clark County"; Tableau writes "Lewis &
#: Clark", and `_fips._norm` does not expand an ampersand. This is the only
#: name in the file that does not resolve on its own.
COUNTY_ALIASES = {"lewis & clark": "Lewis and Clark"}

#: ⚠️ BOTH PATTERNS BELOW ARE MATCHED AGAINST `_compact(text)`, NOT THE RAW
#: EXTRACT, AND THAT IS NOT TIDINESS -- IT IS THE BUG THAT BROKE MONTANA IN
#: PRODUCTION FOR THREE DAYS.
#:
#: Tableau draws this PDF one glyph at a time with explicit positioning, so how
#: many spaces land between two letters is a *heuristic* in whatever pypdf the
#: runner resolved, and pyproject pins only `pypdf>=5.0`. The same 285,506-byte
#: file, from one machine:
#:
#:   pypdf 6.16.1  "2026 Montana Primary Election Absentee Ballot Counts"
#:   pypdf 6.18.0  "2026 M o n tan a P rim ary E lectio n  A b sen tee B allo t..."
#:   pypdf 5.1.0   "2 0 2 6  M o n ta n a  P rim a ry  E le c tio n  A b s e n..."
#:
#: A `\s+` between the words matches the first and not the other two. GitHub
#: Actions resolved 6.18.0 on 2026-09-08, so `mt-sos` raised SchemaDrift on
#: EVERY published ingest run from 2026-09-06 (`output/ev_status.json`, ten
#: consecutive commits) and the tests workflow was red on main with the same two
#: failures. SchemaDrift falls THROUGH, so Montana was also quietly handing
#: itself to the aggregator instead of stopping the ladder.
#:
#: Removing every space is the only reading of this title that does not depend
#: on a text-extractor's spacing heuristic, and the phrases are specific enough
#: that nothing else on the page can produce them.
#:
#: The dashboard title's first words name the election. VERIFIED for the
#: primary: "2026 Montana Primary Election Absentee Ballot Counts". The GENERAL
#: wording is **UNVERIFIED** -- no general-election dashboard has been published
#: in this cycle -- so the match is on the cycle year plus the word "General",
#: with the other election types refused by name rather than by guesswork.
TITLE = re.compile(r"(\d{4})Montana(General|Primary|Special)Election", re.I)
COMPILED = re.compile(r"CompiledOn(\d{1,2})/(\d{1,2})/(\d{4})")

#: How much of the extract to quote when neither pattern is there, so the log
#: says what the page actually said rather than only that it did not match.
_SNIPPET = 120


def _compact(text: str) -> str:
    """The extract with ALL whitespace removed. See the note on TITLE."""
    return re.sub(r"\s+", "", text)


def _int(raw: str) -> int:
    text = raw.strip().replace(",", "").replace('"', "")
    try:
        return int(text)
    except ValueError as exc:
        raise SchemaDrift(f"MT: {raw!r} is not a count") from exc


def _county(name: str) -> tuple[str, str]:
    hit = _fips.lookup("MT", COUNTY_ALIASES.get(name.strip().lower(), name))
    if hit is None:
        raise SchemaDrift(f"MT: unrecognised county name {name!r}")
    return hit


def provenance(body: bytes, cycle: int) -> date:
    """The dashboard's compile date, once its title says it is `cycle`'s general.

    Raises NotYetPublished for any other election -- which is what the dashboard
    is showing for most of the year, and is not an error.
    """
    try:
        reader = pypdf.PdfReader(io.BytesIO(body))
        text = "\n".join(page.extract_text() for page in reader.pages)
    except Exception as exc:  # noqa: BLE001 - pypdf raises a zoo of types
        raise SourceError(f"MT: the dashboard PDF is not readable: {exc}") from exc

    flat = _compact(text)
    title = TITLE.search(flat)
    if title is None:
        raise SchemaDrift(
            "MT: the dashboard PDF carries no '<year> Montana <type> Election' "
            "title, which is the only thing that says which election it holds "
            f"(the extract begins {text[:_SNIPPET]!r})"
        )
    year, kind = int(title.group(1)), title.group(2).lower()
    if year != int(cycle) or kind != "general":
        raise NotYetPublished(
            f"MT: the absentee dashboard is showing the {year} {kind} election, "
            f"not the {cycle} general"
        )
    compiled = COMPILED.search(flat)
    if compiled is None:
        raise SchemaDrift("MT: the dashboard PDF carries no 'Compiled On' stamp")
    month, day, stamp_year = (int(g) for g in compiled.groups())
    try:
        return date(stamp_year, month, day)
    except ValueError as exc:
        raise SchemaDrift(f"MT: 'Compiled On' is not a date: {compiled.group(0)}") from exc


def measures(body: bytes) -> dict[str, dict[str, int]]:
    """{county name: {measure: count}} straight off the long-format CSV."""
    text = body.decode("utf-8-sig", errors="replace")
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        raise SchemaDrift("MT: the dashboard CSV is empty")
    header = tuple(h.strip() for h in rows[0])
    if header != HEADER:
        raise SchemaDrift(f"MT: dashboard CSV header is {header}, expected {HEADER}")
    out: dict[str, dict[str, int]] = {}
    for row in rows[1:]:
        if len(row) != 3:
            raise SchemaDrift(f"MT: dashboard CSV row {row} is not three columns")
        name, measure, value = (cell.strip() for cell in row)
        if not name:
            continue
        if measure not in MEASURES:
            raise SchemaDrift(f"MT: unrecognised measure {measure!r}")
        if measure == PERCENT:
            continue
        out.setdefault(name, {})[measure] = _int(value)
    return out


def parse(csv_body: bytes, day: date, cycle: int) -> FetchResult:
    """Canonical rows from the dashboard CSV, dated by the PDF's compile stamp."""
    table = measures(csv_body)
    statewide = table.pop(ALL, None)
    if not table:
        raise SchemaDrift("MT: the dashboard CSV produced no county rows")

    result = FetchResult()
    totals = {SENT: 0, RECEIVED: 0}
    for name, values in table.items():
        missing = [m for m in (SENT, RECEIVED) if m not in values]
        if missing:
            raise SchemaDrift(f"MT: county {name!r} is missing {missing}")
        fips, canonical = _county(name)
        totals[SENT] += values[SENT]
        totals[RECEIVED] += values[RECEIVED]
        result.county_rows.append(CountyDay(
            cycle=cycle, state="MT", county_fips=fips, day=day,
            county_name=canonical,
            ballots_total=values[RECEIVED],
            ballots_new=None,
            mail_returned=values[RECEIVED],
            # Montana reports absentee only; there is no separate in-person
            # early-vote figure to publish. None, never 0.
            inperson=None,
            # Montana has no party registration. Never 0.
            party_dem=None, party_rep=None, party_oth=None, party_npa=None,
        ))

    covered = len({r.county_fips for r in result.county_rows})
    if covered != EXPECTED_COUNTIES or statewide is None:
        # Same rule as tx.py: a total over a subset of counties looks exactly
        # like a Montana turnout figure and is not one, so no StateDay at all.
        log.warning(
            "MT: dashboard covers %d of %d counties (statewide row %s); "
            "publishing counties only",
            covered, EXPECTED_COUNTIES, "present" if statewide else "absent",
        )
        return result

    for measure in (SENT, RECEIVED):
        if statewide.get(measure) != totals[measure]:
            raise SchemaDrift(
                f"MT: the dashboard's own All row says {statewide.get(measure)} "
                f"{measure} but its counties sum to {totals[measure]}"
            )
    result.state_rows.append(StateDay(
        cycle=cycle, state="MT", day=day,
        ballots_total=statewide[RECEIVED],
        ballots_new=None,
        mail_requested=statewide[SENT],
        mail_returned=statewide[RECEIVED],
        inperson=None,
        party_dem=None, party_rep=None, party_oth=None, party_npa=None,
    ))
    return result


class MTScraper(Adapter):
    """Tier 1 for Montana: the SoS absentee-ballot Tableau dashboard."""

    state = "MT"
    name = "mt-sos"
    tier = TIER_SCRAPER

    def fetch(self, cycle: int, as_of: date) -> FetchResult:
        # The PDF first: it is the only thing that says which election the
        # dashboard is holding, and fetching it before the counts keeps its
        # stamp from ever describing data newer than itself.
        try:
            pdf = get(PDF_URL, state="MT", filename=f"{cycle}_dashboard.pdf",
                      min_bytes=4096)
        except Missing as exc:
            raise NotYetPublished(f"MT: {exc}") from exc
        if not pdf.startswith(b"%PDF"):
            raise SourceError("MT: the dashboard PDF export did not return a PDF")
        day = provenance(pdf, cycle)
        if day > as_of:
            raise SchemaDrift(
                f"MT: the dashboard is stamped {day.isoformat()}, which is after "
                f"the run date {as_of.isoformat()}"
            )
        try:
            body = get(CSV_URL, state="MT", filename=f"{cycle}_dashboard.csv",
                       min_bytes=512)
        except Missing as exc:
            raise NotYetPublished(f"MT: {exc}") from exc
        return parse(body, day, cycle)

    def fetch_history(self, cycle: int) -> FetchResult:
        """No archive, refused BY NAME. Measured on 2026-09-09, not assumed.

        ⚠️ THE DASHBOARD HOLDS THE LAST ELECTION'S NUMBERS BETWEEN CYCLES, SO
        "FETCH IT AND DATE IT ELECTION DAY" IS THE WORST AVAILABLE ANSWER.

        `fetch` is safe only because the PDF names the election. A history path
        has no such luxury -- the live view serves the 2026 primary today, and
        stamping those counts 2024-11-05 would put the primary's 268,125
        received ballots at days_to_election 0 as Montana's 2024 final. So the
        question is only whether a DATED 2024 artefact exists, and it does not:

        * **The Tableau view was never archived.** A Wayback CDX sweep with
          `matchType=domain` over `tableau-ext.mt.gov`, 2015-2026, collapses to
          9,074 urlkeys; a prefix sweep of `tableau-ext.mt.gov/t/SOS/` returns
          exactly ONE row in all of history (the view itself, a 302, from
          2026-06-11), and `/vizql/t/SOS/` returns none. `AbsenteeDash.csv` and
          `AbsenteeDash.pdf` have no capture on any date. Common Crawl's
          `CC-MAIN-2024-46` and `-51` indexes for that host hold only DEQ, HHS,
          DOAMTAB and LEGFinance views.

        * **The Secretary's page archives as the placeholder.**
          `sosmt.gov/elections/absentee-ballot-count/` has 124 captures,
          including 2024-10-13, -10-29, -11-01 through -11-05. The 2024-11-04
          one is 278,890 bytes of `tableauPlaceholder` and contains no county
          number. That is what a Tableau embed always archives as.

        * **The domain sweep found no file either.** `sosmt.gov`,
          `matchType=domain`, 2024-2025, 45,693 collapsed urlkeys greped
          OFFLINE. The only county-level absentee workbook in it is
          `docs/23/elections/63973/absentee-counts-by-county`, and it has TWO
          captures ever -- 2024-06-17 and 2025-05-16 -- with the SAME digest
          (`HT4EIPRTTHWHPKXSKT3M2FX6HGXK7JCK`), both the 2024 PRIMARY compiled
          5/16/2024. It is in `tests/fixtures/mt/`, and the module docstring
          says why a file of that family could not be published as a general's
          final even if a November capture turned up.

        * **Two live decoys, named so nobody re-finds them.**
          `sosmt.gov/Portals/142/Elections/Documents/Absentee_Counts_By_County.xlsx`
          is HTTP 200 today and byte-identical to its 2018 capture: it is the
          2017 special election, "Compiled 5/31/2017". `.../Absentee-Turnout-
          2000-Present.xlsx` is HTTP 200 and stops at "2018 General".

        * **Election-night reporting has no absentee dimension.**
          `electionresults.mt.gov` publishes turnout percent and precincts
          reported, for the CURRENT election only.

        A per-county 2024 FINAL does exist, in the EAC's EAVS, and the module
        docstring carries its URL, its column names and the verified Montana
        sums. It is a federal survey rather than the Secretary's own file, so
        publishing it from here would stamp it `mt-sos` tier 1; `il.py` made the
        same call for the same reason.
        """
        raise NotYetPublished(
            f"MT: no dated {cycle} artefact exists to backfill -- the absentee "
            f"dashboard is a Tableau view that archives as its placeholder "
            f"(124 captures of the Secretary's page, none with a county number), "
            f"AbsenteeDash.csv/.pdf have no Wayback capture on any date, and the "
            f"one archived county workbook from the {cycle} cycle is the primary "
            f"and names no election. The live view holds whichever election ran "
            f"last, so there is nothing here that could honestly be dated "
            f"{cycle}. See the module docstring for the EAVS final that exists "
            f"instead, and for why it is not published from this adapter."
        )
