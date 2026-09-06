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

#: The dashboard title, whose first words name the election. VERIFIED for the
#: primary: "2026 Montana Primary Election Absentee Ballot Counts". The GENERAL
#: wording is **UNVERIFIED** -- no general-election dashboard has been published
#: in this cycle -- so the match is on the cycle year plus the word "General",
#: with the other election types refused by name rather than by guesswork.
TITLE = re.compile(
    r"(\d{4})\s+Montana\s+(General|Primary|Special)\s+Election", re.I
)
COMPILED = re.compile(
    r"Compiled On\s+(\d{1,2})/(\d{1,2})/(\d{4})"
)


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

    title = TITLE.search(text)
    if title is None:
        raise SchemaDrift(
            "MT: the dashboard PDF carries no '<year> Montana <type> Election' "
            "title, which is the only thing that says which election it holds"
        )
    year, kind = int(title.group(1)), title.group(2).lower()
    if year != int(cycle) or kind != "general":
        raise NotYetPublished(
            f"MT: the absentee dashboard is showing the {year} {kind} election, "
            f"not the {cycle} general"
        )
    compiled = COMPILED.search(text)
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
