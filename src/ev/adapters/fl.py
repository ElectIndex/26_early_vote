"""Florida — the Division of Elections' public VBM/early-voting stats page.

`countyfilesvbm-ev.floridados.gov/VoteByMailEarlyVotingReports/PublicStats`
renders four tables server-side: a statewide summary and three per-county tables,
in a fixed order that mirrors the summary's three rows —

    0  statewide     Vote-by-Mail Provided (Not Yet Returned) / Voted VBM / Voted Early
    1  per county    Vote-by-Mail Provided (Not Yet Returned)
    2  per county    Voted Vote-by-Mail
    3  per county    Voted Early

each split Republican / Democrat / Other / No Party Affiliation / Total.

Unlike North Carolina, this page is a SNAPSHOT — it carries today's cumulative
numbers and no history at all. Florida therefore genuinely depends on the daily
cron: a day we fail to run is a day of Florida's curve that cannot be recovered
later. That asymmetry is why the job runs every six hours rather than daily.

Two things this parser refuses to do quietly:

* **`ballots_total` counts ballots CAST** — voted-by-mail plus voted-early. The
  "Provided (Not Yet Returned)" figure is outstanding ballots, not votes, and
  folding it in would overstate Florida by the size of its mail backlog. It is
  reported separately as `mail_requested`.
* **The party columns must sum to the row's own Total.** Florida renders some
  numbers with a leading zero ("01" for 1), so a naive parse can silently shift a
  digit. The state publishes its own total on every row, so we check against it
  and raise SchemaDrift on a mismatch rather than publishing a number that does
  not add up.
"""

from __future__ import annotations

import html
import logging
import re
from datetime import date, datetime

from ..normalize import PARTY_DEM, PARTY_NPA, PARTY_OTH, PARTY_REP, party as _party
from ..schema import TIER_SCRAPER, CountyDay, StateDay
from . import _fips, _net
from .base import Adapter, FetchResult, NotYetPublished, SchemaDrift

log = logging.getLogger(__name__)

URL = ("https://countyfilesvbm-ev.floridados.gov"
       "/VoteByMailEarlyVotingReports/PublicStats")

ROW_OUTSTANDING = "vote-by-mail provided (not yet returned)"
ROW_VOTED_MAIL = "voted vote-by-mail"
ROW_VOTED_EARLY = "voted early"

#: Header labels we require, lowercased. Their absence is drift.
PARTY_HEADERS = {
    "republican": PARTY_REP,
    "democrat": PARTY_DEM,
    "other": PARTY_OTH,
    "no party affiliation": PARTY_NPA,
}

_PARTY_FIELD = {
    PARTY_DEM: "party_dem", PARTY_REP: "party_rep",
    PARTY_NPA: "party_npa", PARTY_OTH: "party_oth",
}

_TAG = re.compile(r"<[^>]+>")


def _text(fragment: str) -> str:
    return " ".join(html.unescape(_TAG.sub(" ", fragment)).split())


def _tables(markup: str) -> list[str]:
    return re.findall(r"<table[^>]*>(.*?)</table>", markup, re.S)


def _rows(table: str) -> list[list[str]]:
    out = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", table, re.S):
        cells = re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)
        if cells:
            out.append([_text(c) for c in cells])
    return out


def _headers(table: str) -> list[str]:
    return [_text(t).lower() for t in re.findall(r"<th[^>]*>(.*?)</th>", table, re.S)]


def _int(raw: str) -> int:
    """Parse a count. Florida pads some values with a leading zero ('01' == 1)."""
    text = raw.replace(",", "").strip()
    if not text:
        raise SchemaDrift("FL: empty count cell")
    if not text.isdigit():
        raise SchemaDrift(f"FL: non-numeric count {raw!r}")
    return int(text)


def _compiled(rows: list[list[str]]) -> date | None:
    for row in rows:
        for cell in row:
            m = re.search(r"(\d{2})/(\d{2})/(\d{4})", cell)
            if m:
                return datetime.strptime(m.group(0), "%m/%d/%Y").date()
    return None


class FLScraper(Adapter):
    state = "FL"
    name = "fl-doe"
    tier = TIER_SCRAPER

    def fetch(self, cycle: int, as_of: date) -> FetchResult:
        try:
            body = _net.get(URL, state=self.state, filename="publicstats.html",
                            min_bytes=2048)
        except _net.Missing as exc:
            raise NotYetPublished(f"FL: {URL} not available") from exc

        markup = body.decode("utf-8", errors="replace")
        tables = _tables(markup)
        if len(tables) < 4:
            raise SchemaDrift(f"FL: expected 4 tables on PublicStats, found {len(tables)}")

        summary = _rows(tables[0])
        day = _compiled(summary) or as_of
        if day > as_of:
            day = as_of

        columns = self._columns(tables[0])
        totals = {self._label(row): self._counts(row, columns) for row in summary}

        for required in (ROW_OUTSTANDING, ROW_VOTED_MAIL, ROW_VOTED_EARLY):
            if required not in totals:
                raise SchemaDrift(f"FL: summary table has no {required!r} row")

        voted_mail = totals[ROW_VOTED_MAIL]
        voted_early = totals[ROW_VOTED_EARLY]
        outstanding = totals[ROW_OUTSTANDING]

        cast = voted_mail["total"] + voted_early["total"]
        if cast == 0 and outstanding["total"] == 0:
            # Nothing has happened yet -- not a failure, and emitting a row of
            # zeros would claim Florida is reporting when it has nothing to report.
            raise NotYetPublished("FL: no ballots provided or cast yet")

        result = FetchResult(state_rows=[StateDay(
            cycle=cycle, state="FL", day=day,
            ballots_total=cast,
            mail_returned=voted_mail["total"],
            inperson=voted_early["total"],
            # Ballots the state has PROVIDED: still outstanding, plus those already
            # returned. Florida does not publish a request count separate from this.
            mail_requested=outstanding["total"] + voted_mail["total"],
            **{field: voted_mail[key] + voted_early[key]
               for key, field in _PARTY_FIELD.items()},
        )])

        result.county_rows = self._counties(tables, cycle, day)
        return result

    # ------------------------------------------------------------------
    def _columns(self, table: str) -> dict[str, int]:
        """Map party bucket -> column index, by HEADER NAME not position."""
        headers = _headers(table)
        if not headers:
            raise SchemaDrift("FL: table has no headers")
        columns: dict[str, int] = {}
        for index, label in enumerate(headers):
            if label in PARTY_HEADERS:
                columns[PARTY_HEADERS[label]] = index
            elif label == "total":
                columns["total"] = index
        missing = (set(PARTY_HEADERS.values()) | {"total"}) - set(columns)
        if missing:
            raise SchemaDrift(f"FL: table missing columns {sorted(missing)}")
        return columns

    def _label(self, row: list[str]) -> str:
        return row[0].strip().lower()

    def _counts(self, row: list[str], columns: dict[str, int]) -> dict[str, int]:
        counts = {}
        for key, index in columns.items():
            if index >= len(row):
                raise SchemaDrift(f"FL: row shorter than header ({len(row)} cells)")
            counts[key] = _int(row[index])

        # Florida publishes its own Total on every row; a party split that does not
        # reproduce it means we mis-read a column, so refuse rather than publish.
        parts = sum(counts[k] for k in _PARTY_FIELD)
        if parts != counts["total"]:
            raise SchemaDrift(
                f"FL: party columns sum to {parts} but row Total is {counts['total']}"
            )
        return counts

    def _counties(self, tables: list[str], cycle: int, day: date) -> list[CountyDay]:
        """Tables 2 and 3 are voted-by-mail and voted-early, by county."""
        mail = self._county_map(tables[2])
        early = self._county_map(tables[3])
        outstanding = self._county_map(tables[1])

        rows: list[CountyDay] = []
        for fips in sorted(set(mail) | set(early)):
            m = mail.get(fips, {})
            e = early.get(fips, {})
            o = outstanding.get(fips, {})
            cast = m.get("total", 0) + e.get("total", 0)
            if cast == 0 and o.get("total", 0) == 0:
                continue
            rows.append(CountyDay(
                cycle=cycle, state="FL", county_fips=fips, day=day,
                county_name=(m.get("name") or e.get("name") or ""),
                ballots_total=cast,
                mail_returned=m.get("total", 0),
                inperson=e.get("total", 0),
                **{field: m.get(key, 0) + e.get(key, 0)
                   for key, field in _PARTY_FIELD.items()},
            ))
        return rows

    def _county_map(self, table: str) -> dict[str, dict]:
        columns = self._columns(table)
        out: dict[str, dict] = {}
        unknown: set[str] = set()
        for row in _rows(table):
            name = row[0].strip()
            if not name:
                continue
            hit = _fips.lookup("FL", name)
            if hit is None:
                unknown.add(name)
                continue
            fips, canonical = hit
            entry = self._counts(row, columns)
            entry["name"] = canonical
            out[fips] = entry
        if unknown:
            raise SchemaDrift(f"FL: unrecognised county names {sorted(unknown)[:5]}")
        return out
