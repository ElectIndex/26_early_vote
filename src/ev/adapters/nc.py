"""North Carolina — the richest early-vote source in the country.

The NC State Board of Elections publishes one row PER BALLOT REQUEST in
`dl.ncsbe.gov/ENRS/<yyyy_mm_dd>/absentee_<yyyymmdd>.zip`, carrying the county,
party registration, race, ethnicity, gender, exact age, request type (MAIL vs
ONE-STOP in-person), and -- the important part -- `ballot_rtn_dt`, the date the
ballot came back.

That last column changes the shape of this adapter versus every other state.
Because each ballot carries its own return date, ONE download reconstructs the
entire daily curve from scratch. We are not stitching together daily snapshots
and cannot lose a day by missing a run, and backfilling 2022 and 2024 is just
two more downloads rather than an archive hunt.

Two judgement calls worth naming:

* **Only ACCEPTED ballots count.** The file also carries PENDING, SPOILED,
  RETURNED UNDELIVERABLE and witness-deficiency statuses. Counting those would
  inflate the total with ballots that may never be counted; "accepted" is the
  number every other tracker means by "ballots cast".
* **Hispanic ethnicity overrides race.** NC reports race and ethnicity in
  separate columns, so a Hispanic voter also has a race. Early-vote coverage
  universally reports one combined bucket, so ethnicity wins -- otherwise
  Hispanic voters would be double-counted across two dimensions.
"""

from __future__ import annotations

import csv
import io
import logging
import zipfile
from collections import defaultdict
from datetime import date, datetime

from ..calendar import election_date
from ..normalize import (
    PARTY_DEM, PARTY_NPA, PARTY_OTH, PARTY_REP, age_band, county_fips, race, sex,
)
from ..schema import TIER_SCRAPER, CountyDay, DemoDay, StateDay
from . import _fips, _net
from .base import Adapter, FetchResult, NotYetPublished, SchemaDrift

log = logging.getLogger(__name__)

BASE = "https://dl.ncsbe.gov/ENRS"

#: The file is Latin-1, not UTF-8 -- it carries bytes (0x90 among them) that are
#: undefined in cp1252, so latin-1 is the only decoding that round-trips. Getting
#: this wrong fails on a voter name halfway through the file, in October.
ENCODING = "latin-1"

#: Columns we depend on. Their absence is drift, not a parse error.
REQUIRED = frozenset({
    "county_desc", "voter_party_code", "ballot_req_type",
    "ballot_rtn_dt", "ballot_rtn_status", "race", "ethnicity", "gender", "age",
})

#: Only these count as a ballot cast. See the module docstring.
ACCEPTED = frozenset({"ACCEPTED", "ACCEPTED - CURED"})

_PARTY_FIELD = {
    PARTY_DEM: "party_dem", PARTY_REP: "party_rep",
    PARTY_NPA: "party_npa", PARTY_OTH: "party_oth",
}

_HISPANIC = "HISPANIC or LATINO"


def _folder(cycle: int) -> tuple[str, str]:
    day = election_date(cycle)
    return day.strftime("%Y_%m_%d"), day.strftime("%Y%m%d")


def _parse_day(raw: str) -> date | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


class _Bucket:
    """Per-day tallies for one geography."""

    __slots__ = ("total", "mail", "inperson", "party")

    def __init__(self) -> None:
        self.total = 0
        self.mail = 0
        self.inperson = 0
        self.party: dict[str, int] = defaultdict(int)


class NCScraper(Adapter):
    state = "NC"
    name = "nc-sbe"
    tier = TIER_SCRAPER

    # ------------------------------------------------------------------
    def fetch(self, cycle: int, as_of: date) -> FetchResult:
        folder, stamp = _folder(cycle)
        url = f"{BASE}/{folder}/absentee_{stamp}.zip"
        try:
            body = _net.get(url, state=self.state, filename=f"absentee_{stamp}.zip")
        except _net.Missing as exc:
            # NC posts the file well before early voting opens, so a 404 here
            # means the cycle's directory does not exist yet.
            raise NotYetPublished(f"NC: {url} not published yet") from exc

        rows = self._read_zip(body, stamp)
        return self._aggregate(rows, cycle, as_of)

    def fetch_history(self, cycle: int) -> FetchResult:
        """Past cycles are the same file; every day is in it already."""
        folder, stamp = _folder(cycle)
        url = f"{BASE}/{folder}/absentee_{stamp}.zip"
        try:
            body = _net.get(url, state=self.state, filename=f"absentee_{stamp}.zip",
                            use_cache=True)
        except _net.Missing as exc:
            raise NotYetPublished(f"NC: no archived file for {cycle}") from exc
        return self._aggregate(self._read_zip(body, stamp), cycle, election_date(cycle))

    # ------------------------------------------------------------------
    def _read_zip(self, body: bytes, stamp: str):
        """Yield rows, streaming.

        Deliberately a generator, not a list. NC's 2024 file is 207 MB zipped and
        carries millions of ballot records; materialising it as a list of dicts
        needs tens of gigabytes and simply cannot complete. The 2026 file reaches
        the same size by late October, so this is a live-season constraint and not
        just a backfill one.
        """
        try:
            archive = zipfile.ZipFile(io.BytesIO(body))
        except zipfile.BadZipFile as exc:
            raise SchemaDrift(f"NC: absentee_{stamp}.zip is not a zip") from exc

        members = [n for n in archive.namelist() if n.lower().endswith(".csv")]
        if not members:
            raise SchemaDrift(f"NC: no CSV inside absentee_{stamp}.zip")

        with archive.open(members[0]) as raw:
            stream = io.TextIOWrapper(raw, encoding=ENCODING, newline="")
            reader = csv.DictReader(stream)
            missing = REQUIRED - set(reader.fieldnames or [])
            if missing:
                raise SchemaDrift(f"NC: absentee file missing columns {sorted(missing)}")
            for row in reader:
                yield row

    # ------------------------------------------------------------------
    def _aggregate(self, rows, cycle: int, as_of: date) -> FetchResult:
        by_state: dict[date, _Bucket] = defaultdict(_Bucket)
        by_county: dict[tuple[str, date], _Bucket] = defaultdict(_Bucket)
        by_demo: dict[tuple[date, str, str], int] = defaultdict(int)
        county_names: dict[str, str] = {}
        unknown_counties: set[str] = set()

        for row in rows:
            if (row.get("ballot_rtn_status") or "").strip().upper() not in ACCEPTED:
                continue
            day = _parse_day(row.get("ballot_rtn_dt", ""))
            if day is None or day > as_of:
                continue

            hit = _fips.lookup("NC", row.get("county_desc"))
            if hit is None:
                unknown_counties.add((row.get("county_desc") or "").strip())
                continue
            fips, canonical = hit
            county_names[fips] = canonical

            method = (row.get("ballot_req_type") or "").strip().upper()
            party = self._party(row.get("voter_party_code"))

            for bucket in (by_state[day], by_county[(fips, day)]):
                bucket.total += 1
                if method == "MAIL":
                    bucket.mail += 1
                elif method in ("ONE-STOP", "ONE STOP"):
                    bucket.inperson += 1
                if party:
                    bucket.party[party] += 1

            for dimension, value in self._demographics(row):
                by_demo[(day, dimension, value)] += 1

        if unknown_counties:
            # A name we cannot map is drift, not a row to silently drop -- NC has
            # exactly 100 counties and they do not change.
            raise SchemaDrift(f"NC: unrecognised county names {sorted(unknown_counties)[:5]}")

        return self._emit(by_state, by_county, by_demo, county_names, cycle, as_of)

    def _party(self, raw: str | None) -> str | None:
        from ..normalize import party as _party
        code = (raw or "").strip()
        if not code:
            return None
        bucket = _party(code)
        if bucket is None:
            raise SchemaDrift(f"NC: unrecognised party code {code!r}")
        return bucket

    def _demographics(self, row: dict[str, str]):
        """Yield (dimension, bucket) pairs, skipping values we cannot classify."""
        band = age_band((row.get("age") or "").strip())
        if band:
            yield "age", band

        ethnicity = (row.get("ethnicity") or "").strip().upper()
        if ethnicity == _HISPANIC.upper():
            yield "race", "hispanic"
        else:
            bucket = race(row.get("race"))
            if bucket:
                yield "race", bucket

        gender = sex(row.get("gender"))
        if gender:
            yield "sex", gender

    # ------------------------------------------------------------------
    def _emit(self, by_state, by_county, by_demo, county_names, cycle, as_of) -> FetchResult:
        result = FetchResult()
        if not by_state:
            return result

        # A continuous daily axis from the first accepted ballot to as_of, so the
        # frontend never has to interpolate a gap in the cumulative curve.
        start = min(by_state)
        span = [start]
        while span[-1] < as_of:
            span.append(date.fromordinal(span[-1].toordinal() + 1))

        running = _Bucket()
        for day in span:
            today = by_state.get(day)
            if today:
                running.total += today.total
                running.mail += today.mail
                running.inperson += today.inperson
                for key, count in today.party.items():
                    running.party[key] += count
            result.state_rows.append(StateDay(
                cycle=cycle, state="NC", day=day,
                ballots_total=running.total,
                ballots_new=today.total if today else 0,
                mail_returned=running.mail,
                inperson=running.inperson,
                # NC's file is one row per REQUEST, so requests are countable, but
                # only for ballots that came back -- we filter to ACCEPTED above,
                # so a requested-but-unreturned count is not derivable here and
                # stays None rather than being guessed at.
                # NC registers by party and reports every bucket, so a party with
                # no ballots yet is a genuine 0 -- not a blank. Blank here would
                # claim the state does not report party at all. This is the exact
                # distinction THE BLANK RULE exists for, in both directions.
                **{field: running.party.get(key, 0) for key, field in _PARTY_FIELD.items()},
            ))

        for fips in sorted({f for f, _ in by_county}):
            running = _Bucket()
            for day in span:
                today = by_county.get((fips, day))
                if today:
                    running.total += today.total
                    running.mail += today.mail
                    running.inperson += today.inperson
                    for key, count in today.party.items():
                        running.party[key] += count
                if running.total == 0:
                    continue
                result.county_rows.append(CountyDay(
                    cycle=cycle, state="NC", county_fips=fips, day=day,
                    county_name=county_names.get(fips, ""),
                    ballots_total=running.total,
                    ballots_new=today.total if today else 0,
                    mail_returned=running.mail,
                    inperson=running.inperson,
                    **{field: running.party.get(key, 0) for key, field in _PARTY_FIELD.items()},
                ))

        cumulative: dict[tuple[str, str], int] = defaultdict(int)
        for day in span:
            touched = {(d, b) for (dd, d, b) in by_demo if dd == day}
            for dimension, bucket in sorted(touched):
                cumulative[(dimension, bucket)] += by_demo[(day, dimension, bucket)]
            for (dimension, bucket), total in sorted(cumulative.items()):
                result.demo_rows.append(DemoDay(
                    cycle=cycle, state="NC", day=day,
                    dimension=dimension, bucket=bucket, ballots_total=total,
                ))
        return result
