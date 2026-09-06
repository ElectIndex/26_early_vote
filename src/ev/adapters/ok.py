"""Oklahoma: the State Election Board's DevExpress statistics dashboards.

THE FINDING THIS MODULE EXISTS TO CORRECT
-----------------------------------------
`docs/coverage-research.md` recorded Oklahoma as "an undocumented dashboard JSON
API ... `DashboardItemGetAction` answers HTTP 500 to every reconstructed query
shape ... it needs the DevExpress client's internal state blob", and rejected it
as too fragile to build.

**There is no state blob.** The dashboard's own browser makes a plain **GET**:

    GET /dashboardControl/data/DashboardItemGetAction
        ?dashboardId=AbsStatsByCounty
        &itemId=pivotDashboardItem1
        &query={"Filter":[{"dimensions":[...],"values":[[...]]}]}

Captured from the live page 2026-09-06 and replayed byte-for-byte from
`requests` -- HTTP 200, `application/json`, no cookie, no token, no session. The
500s came from POSTing. `stats.okelections.gov` is not behind any bot
protection either: plain `requests` and `curl_cffi` get identical answers.

WHAT IS BEHIND IT
-----------------
Two dashboards matter, and they measure different things:

* **`AbsStatsByCounty`** -- table `StatAbsentee`. County x party x ballot type x
  application source x delivery method, with `Sent` / `Received` / `Rejected`.
  This is ABSENTEE ONLY: Oklahoma's early in-person voting is not in it (2024
  general: 130,640 sent, 107,874 received, against 293,918 early in-person
  ballots the same year). **It is live for the 2026 general today** -- 428
  applications across six counties on 2026-09-06 -- which makes it the
  during-season feed.
* **`VHCountsByCounty`** -- table `StatVoterHistory`. County x precinct x party
  x `VotingMethod`, where the methods are `Absentee`, `Early Voting`,
  `Election Day` and `Protected`. This is voter-history CREDIT, i.e. ballots
  actually counted, and it is the only place the early in-person number exists.
  Verified totals: 2024 general 107,549 absentee + 293,918 early; 2022 general
  71,680 + 132,402.

`VotingMethod` has no 2026-general rows yet (its newest election is the
2026-08-25 runoff), so which of the two answers on a given day decides what this
adapter can report:

* voter history present -> ballots cast, split mail vs in-person, by party.
* absentee only -> mail requested and mail returned, by party, and `inperson`
  stays **None**. Not 0 -- Oklahoma's early in-person voting is real and simply
  is not in that table. See THE BLANK RULE in schema.py.

Oklahoma registers voters by party and the dashboards carry all four
registrations -- Democrat, Republican, Independent and Libertarian -- so every
party field here is a real count, and a party with no ballots yet is a genuine 0.

WHAT IS NOT COUNTED, AND WHY
----------------------------
`Election Day` credits are excluded, obviously. `Protected` -- Oklahoma's
address-confidential voters -- is excluded too, because the table does not say
how or when those ballots were cast. It has never appeared in a November
general (verified for 2022, 2024 and the 2026 runoff: three methods each), and
`_check_methods` raises `SchemaDrift` if a method label ever turns up that this
module has not been taught, rather than letting it vanish.

Neither dashboard carries an as-of stamp, so rows are dated with the run's
`as_of`, exactly as `tx.py` does with its snapshot table.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import date

from ..calendar import election_date
from ..normalize import (
    METHOD_INPERSON, METHOD_MAIL, PARTY_DEM, PARTY_NPA, PARTY_OTH, PARTY_REP,
    method as _method, party as _party,
)
from ..schema import TIER_SCRAPER, CountyDay, StateDay
from . import _fips, _net
from .base import Adapter, FetchResult, NotYetPublished, SchemaDrift, SourceError

log = logging.getLogger(__name__)

BASE = "https://stats.okelections.gov"
ITEM_URL = f"{BASE}/dashboardControl/data/DashboardItemGetAction"
#: The dashboard catalogue -- 200, application/json, 18 dashboards. Fetched to
#: prove the two ids below still exist before anything is read from them.
DASHBOARD_LIST_URL = f"{BASE}/dashboardControl/dashboards"

ABSENTEE_DASHBOARD = "AbsStatsByCounty"
HISTORY_DASHBOARD = "VHCountsByCounty"

#: Item ids, read off the live pages 2026-09-06. Each one is validated against
#: the DataMember its response reports, so a renumbered dashboard raises
#: SchemaDrift instead of quietly reading the wrong control.
ABSENTEE_PIVOT = "pivotDashboardItem1"
ABSENTEE_DATE_COMBO = "comboBoxDashboardItem2"
ABSENTEE_PARTY_COMBO = "comboBoxDashboardItem1"
HISTORY_PIVOT = "pivotDashboardItem1"
HISTORY_DATE_COMBO = "comboBoxDashboardItem1"
HISTORY_PARTY_COMBO = "comboBoxDashboardItem2"
HISTORY_METHOD_PIE = "pieDashboardItem1"

COL_ELECTION = "ElectionDate"
COL_COUNTY = "CountyDesc"
COL_PARTY = "PartyDesc"
COL_METHOD = "VotingMethod"

MEASURE_SENT = "Sent"
MEASURE_RECEIVED = "Received"
MEASURE_REJECTED = "Rejected"
MEASURE_HISTORY = "HistoryCount"
MEASURE_TOTAL_VOTES = "SumTotVotes"

#: The two credit methods that are early voting. `Election Day` is not early
#: voting; `Protected` is early voting whose method the state withholds, so it
#: cannot be filed under either bucket -- see the module docstring.
EARLY_METHODS = ("Absentee", "Early Voting")
KNOWN_METHODS = frozenset({"Absentee", "Early Voting", "Election Day", "Protected"})

#: Every Oklahoma county. Fewer than this and no statewide row is published --
#: a sum over 74 of 77 looks exactly like an Oklahoma total and is not one.
#: Same rule as tx.py and oh.py.
EXPECTED_COUNTIES = len(_fips.CENSUS_COUNTIES["OK"])

_PARTY_FIELD = {
    PARTY_DEM: "party_dem", PARTY_REP: "party_rep",
    PARTY_NPA: "party_npa", PARTY_OTH: "party_oth",
}

#: How the dashboards spell a date in a filter value and in an EncodeMap.
_FILTER_STAMP = "%Y-%m-%dT00:00:00.000"
_ENCODED_STAMP = "%Y-%m-%dT00:00:00.0000000"


def _dimension(member: str, **extra) -> dict:
    """One entry of the `dimensions` array in a `query` filter."""
    return {"@ItemType": "Dimension", "@DataMember": member,
            "@DefaultId": "DataItem0", **extra}


def _election_dimension() -> dict:
    # The date dimension is grouped and sorted in the client's own filter; the
    # server rejects the value unless the grouping matches.
    return _dimension(COL_ELECTION, **{
        "@DateTimeGroupInterval": "DayMonthYear", "@SortOrder": "Descending",
    })


def build_query(filters: list[tuple[dict, str]]) -> str:
    """The `query` parameter: a list of one-dimension, one-value filters."""
    return json.dumps(
        {"Filter": [{"dimensions": [dim], "values": [[value]]}
                    for dim, value in filters]},
        separators=(",", ":"),
    )


# --------------------------------------------------------------------------
# Decoding one DashboardItemGetAction response
# --------------------------------------------------------------------------
def _slot_ids(payload: dict) -> tuple[dict[str, str], dict[str, str]]:
    """(dimension DataMember -> DataItem id, measure DataMember -> id).

    Everything downstream is addressed by DATA MEMBER, never by the `DataItemN`
    ids, which are the dashboard author's private numbering and differ between
    dashboards (county is DataItem6 in the absentee pivot and DataItem0 in the
    voter-history one). Re-deriving them from each response is what makes a
    reordered dashboard fail loudly instead of transposing measures onto the
    wrong counties.
    """
    try:
        meta = payload["ItemData"]["MetaData"]
    except (KeyError, TypeError) as exc:
        raise SchemaDrift(f"OK: dashboard response has no MetaData: {exc}") from exc
    dims: dict[str, str] = {}
    for axis in (meta.get("DimensionDescriptors") or {}).values():
        for descriptor in axis or []:
            dims[descriptor["DataMember"]] = descriptor["ID"]
    measures = {d["DataMember"]: d["ID"]
                for d in (meta.get("MeasureDescriptors") or [])}
    return dims, measures


def _encode_maps(payload: dict) -> dict[str, list]:
    try:
        return payload["ItemData"]["DataStorageDTO"]["EncodeMaps"] or {}
    except (KeyError, TypeError) as exc:
        raise SchemaDrift(f"OK: dashboard response has no EncodeMaps: {exc}") from exc


def _slices(payload: dict) -> list[dict]:
    try:
        return payload["ItemData"]["DataStorageDTO"]["Slices"] or []
    except (KeyError, TypeError) as exc:
        raise SchemaDrift(f"OK: dashboard response has no Slices: {exc}") from exc


def dimension_values(payload: dict, member: str) -> list[str]:
    """Every value of one dimension, in the order its EncodeMap lists them."""
    dims, _ = _slot_ids(payload)
    if member not in dims:
        raise SchemaDrift(f"OK: dashboard response does not carry {member!r}")
    return list(_encode_maps(payload).get(dims[member]) or [])


def decode(payload: dict, keys: tuple[str, ...], measures: tuple[str, ...]) -> dict:
    """{(key values...) -> {measure: number}} for one slice of a DSR response.

    A DevExpress pivot answers with every level of its hierarchy at once: a
    grand-total slice with no keys, one slice per dimension, and one per
    combination. `keys` picks the one wanted; `()` is the grand total. Each
    slice's `Data` is keyed by "[i]" / "[i,j]" -- indexes into the EncodeMaps of
    its own KeyIds, in KeyIds order.
    """
    dims, meas = _slot_ids(payload)
    missing = [m for m in keys if m not in dims] + [m for m in measures if m not in meas]
    if missing:
        raise SchemaDrift(f"OK: dashboard response is missing columns {missing}")

    wanted = [dims[k] for k in keys]
    maps = _encode_maps(payload)
    for candidate in _slices(payload):
        if list(candidate.get("KeyIds") or []) != wanted:
            continue
        value_ids = candidate.get("ValueIds") or {}
        slots = {}
        for member in measures:
            slot = value_ids.get(meas[member])
            if slot is None:
                raise SchemaDrift(
                    f"OK: dashboard slice {keys} does not carry {member!r}"
                )
            slots[member] = str(slot)
        out: dict[tuple, dict[str, float | None]] = {}
        for raw_key, cells in (candidate.get("Data") or {}).items():
            indexes = [int(part) for part in raw_key.strip("[]").split(",") if part != ""]
            if len(indexes) != len(wanted):
                raise SchemaDrift(f"OK: dashboard row key {raw_key!r} does not fit {keys}")
            try:
                label = tuple(maps[dim][index] for dim, index in zip(wanted, indexes))
            except (KeyError, IndexError) as exc:
                raise SchemaDrift(
                    f"OK: dashboard row key {raw_key!r} indexes outside its EncodeMap"
                ) from exc
            out[label] = {m: cells.get(slots[m]) for m in measures}
        return out
    raise SchemaDrift(f"OK: dashboard response has no slice keyed by {keys}")


def _int(value) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SchemaDrift(f"OK: {value!r} is not a count")
    return int(round(value))


def _add(*values: int | None) -> int | None:
    present = [v for v in values if v is not None]
    return sum(present) if present else None


def _party_bucket(label: str) -> str:
    bucket = _party(label)
    if bucket is None:
        raise SchemaDrift(f"OK: unrecognised party registration {label!r}")
    return bucket


def _county_fips(name: str) -> tuple[str, str]:
    hit = _fips.lookup("OK", name)
    if hit is None:
        raise SchemaDrift(f"OK: unrecognised county name {name!r}")
    return hit


def check_methods(labels: list[str]) -> None:
    """Refuse a voting-method vocabulary this module has not been taught.

    The early-vote total is `Absentee + Early Voting` and nothing else, so a new
    label -- an "In-Person Absentee" split, say -- would silently disappear out
    of every Oklahoma number rather than showing up as a gap.
    """
    unknown = sorted(set(labels) - KNOWN_METHODS)
    if unknown:
        raise SchemaDrift(f"OK: unrecognised voting methods {unknown}")


# --------------------------------------------------------------------------
# Row assembly
# --------------------------------------------------------------------------
def _emit(
    counties: dict[str, dict],
    names: dict[str, str],
    cycle: int,
    day: date,
    *,
    statewide_extra: dict | None = None,
) -> FetchResult:
    result = FetchResult()
    running = {"ballots_total": None, "mail_requested": None,
               "mail_returned": None, "inperson": None}
    party_running: dict[str, int | None] = {b: None for b in _PARTY_FIELD}

    for fips in sorted(counties):
        row = counties[fips]
        party = row.get("party") or {}
        result.county_rows.append(CountyDay(
            cycle=cycle, state="OK", county_fips=fips, day=day,
            county_name=names[fips],
            ballots_total=row.get("ballots_total"),
            ballots_new=None,
            mail_returned=row.get("mail_returned"),
            inperson=row.get("inperson"),
            **{field: party.get(bucket) for bucket, field in _PARTY_FIELD.items()},
        ))
        for field in running:
            running[field] = _add(running[field], row.get(field))
        for bucket in party_running:
            party_running[bucket] = _add(party_running[bucket], party.get(bucket))

    if len(counties) != EXPECTED_COUNTIES:
        # PARTIAL COVERAGE -- counties only, no statewide row. See
        # EXPECTED_COUNTIES.
        log.warning(
            "OK: dashboard returned %d of %d counties; publishing counties only, "
            "no statewide row (a partial sum would read as an Oklahoma total)",
            len(counties), EXPECTED_COUNTIES,
        )
        return result

    extra = statewide_extra or {}
    result.state_rows.append(StateDay(
        cycle=cycle, state="OK", day=day,
        ballots_total=running["ballots_total"],
        ballots_new=None,
        mail_requested=extra.get("mail_requested", running["mail_requested"]),
        mail_returned=running["mail_returned"],
        inperson=running["inperson"],
        **{field: party_running[bucket] for bucket, field in _PARTY_FIELD.items()},
    ))
    return result


# --------------------------------------------------------------------------
# The adapter
# --------------------------------------------------------------------------
class OKScraper(Adapter):
    """Tier 1 for Oklahoma: the State Election Board's statistics dashboards."""

    state = "OK"
    name = "ok-seb"
    tier = TIER_SCRAPER

    # ------------------------------------------------------------------
    def fetch(self, cycle: int, as_of: date) -> FetchResult:
        election = election_date(cycle)
        history = self._history(election)
        if history is not None:
            counties, names = history
            absentee = self._absentee(election)
            extra = None
            if absentee is not None:
                requested = _add(*[c.get("mail_requested") for c in absentee[0].values()])
                extra = {"mail_requested": requested}
            return _emit(counties, names, cycle, as_of, statewide_extra=extra)

        absentee = self._absentee(election)
        if absentee is None:
            raise NotYetPublished(
                f"OK: neither the absentee nor the voter-history dashboard "
                f"carries the {cycle} general ({election.isoformat()}) yet"
            )
        counties, names = absentee
        returned = _add(*[c.get("mail_returned") for c in counties.values()]) or 0
        if returned <= 0:
            raise NotYetPublished(
                f"OK: the {cycle} general is on the absentee dashboard but no "
                f"ballot has been returned yet"
            )
        return _emit(counties, names, cycle, as_of)

    # ------------------------------------------------------------------
    def fetch_history(self, cycle: int) -> FetchResult:
        """The certified early-vote position, from voter-history credit.

        One dated row per county on Election Day. The dashboards carry no
        within-election date dimension -- there is no `Received Date` anywhere in
        either table -- so an archived DAILY curve simply does not exist for
        Oklahoma, and inventing one from a snapshot would be a fabrication.
        """
        election = election_date(cycle)
        history = self._history(election)
        if history is None:
            raise NotYetPublished(
                f"OK: the voter-history dashboard has no {cycle} general "
                f"({election.isoformat()})"
            )
        counties, names = history
        return _emit(counties, names, cycle, election)

    # ------------------------------------------------------------------
    def _item(self, dashboard: str, item: str, filters=()) -> dict:
        params = {"dashboardId": dashboard, "itemId": item}
        if filters:
            params["query"] = build_query(list(filters))
        # A stable cache filename: `hash()` is salted per interpreter, which
        # would scatter a new copy of every response across cache/ on each run.
        digest = hashlib.sha1(params.get("query", "").encode()).hexdigest()[:12]
        body = _net.get(
            ITEM_URL, state="OK",
            filename=f"{dashboard}_{item}_{digest}.json",
            params=params, min_bytes=32,
        )
        try:
            return json.loads(body)
        except ValueError as exc:
            raise SchemaDrift(f"OK: {dashboard}/{item} did not return JSON") from exc

    def _elections(self, dashboard: str, combo: str) -> set[str]:
        payload = self._item(dashboard, combo)
        return set(dimension_values(payload, COL_ELECTION))

    def _has(self, dashboard: str, combo: str, election: date) -> bool:
        return election.strftime(_ENCODED_STAMP) in self._elections(dashboard, combo)

    # ------------------------------------------------------------------
    def _absentee(self, election: date) -> tuple[dict[str, dict], dict[str, str]] | None:
        """County mail applications sent and ballots returned, by party."""
        if not self._has(ABSENTEE_DASHBOARD, ABSENTEE_DATE_COMBO, election):
            return None
        stamp = election.strftime(_FILTER_STAMP)
        date_filter = (_election_dimension(), stamp)

        payload = self._item(ABSENTEE_DASHBOARD, ABSENTEE_PIVOT, [date_filter])
        totals = decode(payload, (COL_COUNTY,), (MEASURE_SENT, MEASURE_RECEIVED))

        counties: dict[str, dict] = {}
        names: dict[str, str] = {}
        for (raw_name,), cells in totals.items():
            fips, canonical = _county_fips(raw_name)
            names[fips] = canonical
            counties[fips] = {
                "ballots_total": _int(cells[MEASURE_RECEIVED]),
                "mail_requested": _int(cells[MEASURE_SENT]),
                "mail_returned": _int(cells[MEASURE_RECEIVED]),
                # Oklahoma's early in-person voting is NOT in this table. None,
                # never 0 -- see the module docstring.
                "inperson": None,
                "party": {},
            }

        parties = self._item(ABSENTEE_DASHBOARD, ABSENTEE_PARTY_COMBO, [date_filter])
        for label in dimension_values(parties, COL_PARTY):
            bucket = _party_bucket(label)
            per_party = decode(
                self._item(ABSENTEE_DASHBOARD, ABSENTEE_PIVOT,
                           [date_filter, (_dimension(COL_PARTY), label)]),
                (COL_COUNTY,), (MEASURE_RECEIVED,),
            )
            counted = {
                _county_fips(name)[0]: _int(cells[MEASURE_RECEIVED])
                for (name,), cells in per_party.items()
            }
            for fips, row in counties.items():
                # Oklahoma registers by party and reports all four, so a party
                # with no ballots in a county is a real 0, not a blank.
                row["party"][bucket] = counted.get(fips, 0)
        return counties, names

    # ------------------------------------------------------------------
    def _history(self, election: date) -> tuple[dict[str, dict], dict[str, str]] | None:
        """County early ballots CAST, split mail vs in-person, by party."""
        if not self._has(HISTORY_DASHBOARD, HISTORY_DATE_COMBO, election):
            return None
        stamp = election.strftime(_FILTER_STAMP)
        date_filter = (_election_dimension(), stamp)

        methods = self._item(HISTORY_DASHBOARD, HISTORY_METHOD_PIE, [date_filter])
        check_methods(dimension_values(methods, COL_METHOD))

        counties: dict[str, dict] = {}
        names: dict[str, str] = {}
        for raw_method in EARLY_METHODS:
            bucket = _method(raw_method)
            if bucket not in (METHOD_MAIL, METHOD_INPERSON):
                raise SchemaDrift(f"OK: cannot normalize voting method {raw_method!r}")
            field = "mail_returned" if bucket == METHOD_MAIL else "inperson"
            filters = [date_filter, (_dimension(COL_METHOD), raw_method)]
            payload = self._item(HISTORY_DASHBOARD, HISTORY_PIVOT, filters)

            for (raw_name,), cells in decode(
                payload, (COL_COUNTY,), (MEASURE_HISTORY,)
            ).items():
                fips, canonical = _county_fips(raw_name)
                names[fips] = canonical
                row = counties.setdefault(
                    fips, {"ballots_total": None, "mail_returned": None,
                           "inperson": None, "party": {}},
                )
                count = _int(cells[MEASURE_HISTORY])
                row[field] = _add(row[field], count)
                row["ballots_total"] = _add(row["ballots_total"], count)

            for (raw_name, label), cells in decode(
                payload, (COL_COUNTY, COL_PARTY), (MEASURE_HISTORY,)
            ).items():
                fips, _ = _county_fips(raw_name)
                party = counties[fips]["party"]
                party_bucket = _party_bucket(label)
                party[party_bucket] = _add(
                    party.get(party_bucket), _int(cells[MEASURE_HISTORY])
                )

        # Every registration Oklahoma recognises appears in the dashboard's own
        # Affiliation list, so a party with no early ballots in a county is a
        # genuine 0 rather than a blank.
        registrations = {
            _party_bucket(label)
            for label in dimension_values(
                self._item(HISTORY_DASHBOARD, HISTORY_PARTY_COMBO, [date_filter]),
                COL_PARTY,
            )
        }
        for row in counties.values():
            for bucket in registrations:
                row["party"].setdefault(bucket, 0)
        return counties, names
