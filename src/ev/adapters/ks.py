"""Kansas: the Secretary of State's advance-voting Power BI dashboard.

Kansas publishes no advance-voting file of any kind. What it publishes is one
embedded Power BI report, linked from
`https://sos.ks.gov/elections/advance-voting-data.html` as an
`app.powerbigov.us/view?r=<token>` iframe -- and, exactly as in Ohio, the query
API behind that embed answers a plain `requests` POST with no authentication
beyond the report's own public resource key. See `docs/ohio-source.md` Part 4 for
the rediscovery recipe; this module follows it.

WHAT THE DASHBOARD CARRIES, AND WHAT IT DOES NOT
------------------------------------------------
One table, four fields, and no geography at all:

    DATE                              one row per reporting day
    ADVANCE VOTING BALLOTS SENT       mail ballots mailed out    -> mail_requested
    ADVANCE VOTING BALLOTS RETURNED   mail ballots back          -> mail_returned
    IN PERSON ADVANCE                 in-person advance ballots  -> inperson

The counts are CUMULATIVE, so one request reconstructs the whole curve -- a
missed daily run cannot lose a day. `ballots_total` is `RETURNED + IN PERSON`,
which is Kansas's own definition, not ours: the report's headline card carries a
measure called `Total Number of Ballots Voted`, and querying it returns exactly
the final day's `RETURNED + IN PERSON` (202,231 = 40,446 + 161,785 for the August
2026 primary). That measure is a whole-table aggregate rather than a per-day one
-- it returns the same 202,231 against every date -- so it is NOT read per row;
it was used once, live, to prove the arithmetic and is recorded here instead.

**Statewide only.** There is no county, precinct or district column anywhere in
the model, so `county_rows` is always empty -- which `base.py` is explicit is not
an error, and which South Dakota and Alaska already do.

**No party breakdown.** Kansas DOES register voters by party (its own monthly
`*-Voter-Registration-Numbers-by-County.xlsx` files split D/R/L/U by county), but
this dashboard does not carry the dimension. So the four party fields are None --
"this source does not report it", per THE BLANK RULE in schema.py -- and that is
a different fact from Ohio's or Texas's None, where the state has no party
registration to report.

THE TRAP THIS MODULE IS SHAPED AROUND: THE PRIMARY
---------------------------------------------------
The table is named for the cycle, not the election. On 2026-09-06 it held
sixteen rows running 2026-07-15 .. 2026-08-04 -- the AUGUST PRIMARY, ending on
primary day with 202,231 advance ballots. A run today that trusted the table's
name would stamp primary advance turnout with today's date under the 2026
GENERAL, which is precisely the failure that got Montana rejected in
`docs/coverage-research.md`.

So every row is gated on the general's own advance window. Kansas's is statutory
(K.S.A. 25-1122): ballots go out no earlier than 20 days before the election, and
the primary series confirms it to the day -- 2026-08-04 minus 20 is 2026-07-15,
the first row. Anything outside `[Election Day - WINDOW_DAYS, Election Day]` is
not the general's data, and a table with nothing inside that window is
`NotYetPublished` (STOP the ladder), never a number.

`ballots_new` stays None. The series is cumulative and skips weekends -- there is
no 2026-07-18 or 2026-07-19 row -- so a difference between consecutive snapshots
would attribute three days of ballots to Monday. `tx.py` refuses the same
inference for the same reason.

DISCOVERY VS HARDCODING
-----------------------
Ohio hardcodes its five Power BI constants and documents how to re-find them.
Kansas can do better, because the SoS page itself carries the embed token and the
report tells you its own table name -- and that name has the CYCLE IN IT
(`ADVANCE VOTE COUNTS 2026`), so it is guaranteed to change. The chain is
therefore scraped end to end -- page -> resource key -> cluster -> model, report,
dataset and entity -- with each step falling back to the literal that was
verified live on 2026-09-06 if the scrape fails.
"""

from __future__ import annotations

import base64
import json
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from ..calendar import election_date
from ..schema import TIER_SCRAPER, StateDay
from ._net import DEFAULT_HEADERS, SESSION, cache_path, get, looks_like_html
from .base import Adapter, FetchResult, NotYetPublished, SchemaDrift, SourceError

log = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Route: the SoS page -> the embed token
# --------------------------------------------------------------------------
#: VERIFIED live 2026-09-06: HTTP 200, 31,891 bytes, text/html. The page is a
#: static shell whose only content is the iframe below.
SOS_PAGE = "https://sos.ks.gov/elections/advance-voting-data.html"

#: The `?r=` token is base64 of `{"k": <resourceKey>, "t": <tenantId>}`. VERIFIED
#: live 2026-09-06 on SOS_PAGE, decoding to the key below.
_EMBED_TOKEN = re.compile(r"app\.powerbigov\.us/view\?r=([A-Za-z0-9_\-=]+)")

POWERBI_VIEW = "https://app.powerbigov.us/view?r={token}"

#: VERIFIED live 2026-09-06: the token on SOS_PAGE decodes to this key, and
#: `POWERBI_VIEW` with that token returns HTTP 200, 29,106 bytes.
POWERBI_RESOURCE_KEY = "de3b6e3c-b94e-419a-8d9c-9435eba72780"

#: The embed page names its own cluster in a `ClusterUri` assignment. The
#: `-redirect` host it names 403s every API call (VERIFIED: 403, 0 bytes); the
#: `-api` host is the one that answers (VERIFIED: 200, 82,782 bytes). Ohio's
#: dashboard behaves identically on a different cluster, so the substitution is
#: the documented shape of these endpoints, not a Kansas quirk.
_CLUSTER_URI = re.compile(r"ClusterUri['\"]?\s*[:=]\s*['\"]([^'\"]+)")
POWERBI_CLUSTER = "https://wabi-us-gov-virginia-api.analysis.usgovcloudapi.net"

MODELS_PATH = "/public/reports/{key}/modelsAndExploration?preferReadOnlySession=true"
QUERY_PATH = "/public/reports/querydata?synchronous=true"

# --------------------------------------------------------------------------
# The model. All four literals VERIFIED live 2026-09-06 from the response to
# MODELS_PATH; all four are also DISCOVERED from that same response at runtime,
# and these are only the fallback for when that GET fails.
# --------------------------------------------------------------------------
POWERBI_MODEL_ID = 2023851
POWERBI_REPORT_ID = "2797373"
POWERBI_DATASET_ID = "36f57e00-2a51-4c14-bc76-9a981c1efc63"

#: The table name carries the cycle, so it WILL change. Discovery is what makes
#: that survivable; this is the 2026 value.
ENTITY = "ADVANCE VOTE COUNTS 2026"

COL_DATE = "DATE"
MEASURE_SENT = "ADVANCE VOTING BALLOTS SENT"
MEASURE_RETURNED = "ADVANCE VOTING BALLOTS RETURNED"
MEASURE_INPERSON = "IN PERSON ADVANCE"

COLUMNS = (COL_DATE,)
MEASURES = (MEASURE_SENT, MEASURE_RETURNED, MEASURE_INPERSON)
#: A table must carry all four to be the advance-vote table. A renamed column is
#: SchemaDrift, not something to guess around.
REQUIRED = COLUMNS + MEASURES

#: Kansas's advance-voting window, in days before Election Day. K.S.A. 25-1122
#: puts the first mailing at 20 days; the August 2026 primary series starts
#: exactly there (2026-08-04 - 20 = 2026-07-15). Two days of slack absorb a
#: county that reports a day early, and nothing more: the whole point is that the
#: PRIMARY's rows -- 91 days before the general -- can never be mistaken for the
#: general's. A row after Election Day is refused outright.
WINDOW_DAYS = 22


@dataclass(frozen=True)
class Model:
    """Everything one querydata POST needs, discovered or fallen back to."""

    entity: str
    model_id: int
    report_id: str
    dataset_id: str


FALLBACK_MODEL = Model(
    entity=ENTITY,
    model_id=POWERBI_MODEL_ID,
    report_id=POWERBI_REPORT_ID,
    dataset_id=POWERBI_DATASET_ID,
)


# --------------------------------------------------------------------------
# Discovery
# --------------------------------------------------------------------------
def resource_key(markup: str) -> str | None:
    """The report's public resource key, out of the SoS page's iframe token.

    None -- not an exception -- when the page does not carry one: the caller
    falls back to the verified literal, because a redesigned SoS page is not a
    reason to stop reading a report that still works.
    """
    match = _EMBED_TOKEN.search(markup or "")
    if match is None:
        return None
    token = match.group(1)
    try:
        # The embed omits base64 padding; "==" is always enough to restore it.
        payload = json.loads(base64.b64decode(token + "==").decode("utf-8"))
    except Exception:  # noqa: BLE001 -- a malformed token is just no token
        log.debug("KS: embed token on %s did not decode", SOS_PAGE)
        return None
    key = str(payload.get("k") or "").strip()
    # A resource key is a UUID. Anything else is not one, and guessing would
    # send the SoS's tenant id to the query API as if it were a report.
    if not re.fullmatch(r"[0-9a-fA-F-]{36}", key):
        return None
    return key.lower()


def cluster(markup: str) -> str | None:
    """The API host, from the embed page's own `ClusterUri`.

    Power BI hands out a `-redirect` host that refuses every API call, so the
    `-api` sibling is what is returned. None when the page does not name one.
    """
    match = _CLUSTER_URI.search(markup or "")
    if match is None:
        return None
    uri = match.group(1).strip().rstrip("/")
    if not uri.startswith("https://") or "analysis.usgovcloudapi.net" not in uri:
        return None
    return uri.replace("-redirect.", "-api.")


def _visual_queries(exploration: dict):
    """Yield each visual's parsed `prototypeQuery`, skipping the unparseable."""
    for section in exploration.get("sections") or []:
        for container in section.get("visualContainers") or []:
            raw = container.get("config")
            if not raw:
                continue
            try:
                config = json.loads(raw)
            except (TypeError, ValueError):
                continue
            query = (config.get("singleVisual") or {}).get("prototypeQuery")
            if isinstance(query, dict):
                yield query


def _entity_columns(query: dict) -> dict[str, set[str]]:
    """entity -> the column/measure names this visual reads off it."""
    sources = {
        str(f.get("Name")): str(f.get("Entity"))
        for f in query.get("From") or []
        if f.get("Name") and f.get("Entity")
    }
    found: dict[str, set[str]] = {}
    for item in query.get("Select") or []:
        node = item.get("Column") or item.get("Measure") or {}
        if not node:
            aggregation = (item.get("Aggregation") or {}).get("Expression") or {}
            node = aggregation.get("Column") or {}
        prop = node.get("Property")
        ref = ((node.get("Expression") or {}).get("SourceRef") or {}).get("Source")
        entity = sources.get(str(ref))
        if prop and entity:
            found.setdefault(entity, set()).add(str(prop))
    return found


def discover(payload: dict) -> list[Model]:
    """Every table in the report that carries the advance-vote columns.

    Usually exactly one. More than one is possible if Kansas ever ships the
    primary and the general as separate tables in the same report, so the caller
    tries each in turn rather than guessing -- the election window in `parse` is
    what actually decides which is the general's.
    """
    try:
        model_id = int(payload["models"][0]["id"])
        dataset_id = str(payload["models"][0]["dbName"])
        exploration = payload["exploration"]
        report_id = str(exploration["reportId"])
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise SchemaDrift(f"KS: dashboard model response is not a report: {exc}") from exc

    columns: dict[str, set[str]] = {}
    for query in _visual_queries(exploration):
        for entity, props in _entity_columns(query).items():
            columns.setdefault(entity, set()).update(props)

    matches = sorted(e for e, props in columns.items() if set(REQUIRED) <= props)
    if not matches:
        raise SchemaDrift(
            f"KS: no table in the advance-voting report carries {list(REQUIRED)} "
            f"(it has {sorted(columns)})"
        )
    return [
        Model(entity=e, model_id=model_id, report_id=report_id, dataset_id=dataset_id)
        for e in matches
    ]


# --------------------------------------------------------------------------
# The query
# --------------------------------------------------------------------------
def build_query(model: Model) -> dict:
    """The querydata body: every reporting date, one request.

    No server-side filter: the whole table is 16 rows and 2.6 KB, and picking
    the election out locally is visible and testable, where a filter DSL is
    neither.
    """
    def source():
        return {"SourceRef": {"Source": "a"}}

    select = [
        {"Column": {"Expression": source(), "Property": name},
         "Name": f"{model.entity}.{name}"}
        for name in COLUMNS
    ] + [
        {"Aggregation": {
            "Expression": {"Column": {"Expression": source(), "Property": name}},
            "Function": 0},
         "Name": f"Sum({model.entity}.{name})"}
        for name in MEASURES
    ]
    return {
        "version": "1.0.0",
        "queries": [{
            "Query": {"Commands": [{"SemanticQueryDataShapeCommand": {
                "Query": {
                    "Version": 2,
                    "From": [{"Name": "a", "Entity": model.entity, "Type": 0}],
                    "Select": select,
                },
                "Binding": {
                    "Primary": {"Groupings": [{"Projections": list(range(len(select)))}]},
                    "DataReduction": {
                        "DataVolume": 4, "Primary": {"Window": {"Count": 30000}},
                    },
                    "Version": 1,
                },
                "ExecutionMetricsKind": 1,
            }}]},
            "QueryId": "",
            "ApplicationContext": {
                "DatasetId": model.dataset_id,
                "Sources": [{"ReportId": model.report_id}],
            },
        }],
        "cancelQueries": [],
        "modelId": model.model_id,
    }


def decode_dsr(payload: dict) -> list[dict]:
    """Turn one Power BI querydata response into a list of {column: value}.

    The wire format is column-compressed and has to be un-compressed exactly, or
    the counts come out attached to the wrong dates:

    * `S` on the first row names the slots ("G0" for a grouping, "M0" for a
      measure) and, for dictionary-encoded slots, a `DN` naming an entry in
      `ValueDicts`; the value on the wire is then an index into that list.
    * `C` holds only the slots that changed. `R` is a bitmask of slots repeating
      the previous row's value and `Ø` a bitmask of null slots; both are omitted
      from `C`.

    Slots are matched back to names through the response's OWN
    `descriptor.Select`, so a reordered projection cannot transpose sent onto
    returned. Anything unrecognised is SchemaDrift.
    """
    try:
        data = payload["results"][0]["result"]["data"]
        descriptor = data["descriptor"]["Select"]
        dataset = data["dsr"]["DS"][0]
        holder = dataset["PH"][0]
    except (KeyError, IndexError, TypeError) as exc:
        raise SchemaDrift(f"KS: dashboard response is not a DSR result: {exc}") from exc

    slot_names: dict[str, str] = {}
    for item in descriptor:
        name = str(item.get("Name", ""))
        slot = item.get("Value")
        if slot is None:
            raise SchemaDrift(f"KS: dashboard column {name!r} has no slot")
        # "ADVANCE VOTE COUNTS 2026.DATE" and
        # "Sum(ADVANCE VOTE COUNTS 2026.IN PERSON ADVANCE)" both reduce to the
        # bare property name.
        slot_names[slot] = name.rsplit(".", 1)[-1].rstrip(")")

    missing = sorted(set(REQUIRED) - set(slot_names.values()))
    if missing:
        raise SchemaDrift(f"KS: dashboard response is missing columns {missing}")

    rows_key = next((k for k in holder if k.startswith("DM")), None)
    if rows_key is None:
        raise SchemaDrift("KS: dashboard response has no data rows")
    raw_rows = holder[rows_key]
    if not raw_rows or "S" not in raw_rows[0]:
        raise SchemaDrift("KS: dashboard response carries no row schema")

    schema = raw_rows[0]["S"]
    dicts = dataset.get("ValueDicts", {})
    width = len(schema)
    out: list[dict] = []
    previous: list = [None] * width

    for raw in raw_rows:
        values = list(raw.get("C", []))
        repeat = raw.get("R", 0)
        nulls = raw.get("Ø", 0)
        row: list = []
        cursor = 0
        for i in range(width):
            if repeat >> i & 1:
                row.append(previous[i])
                continue
            if nulls >> i & 1:
                row.append(None)
                continue
            if cursor >= len(values):
                raise SchemaDrift("KS: dashboard row is shorter than its schema")
            value = values[cursor]
            cursor += 1
            book = schema[i].get("DN")
            if book is not None and isinstance(value, int):
                try:
                    value = dicts[book][value]
                except (KeyError, IndexError) as exc:
                    raise SchemaDrift(
                        f"KS: dashboard value dictionary {book!r} has no entry {value}"
                    ) from exc
            row.append(value)
        previous = row
        out.append({
            slot_names[schema[i]["N"]]: row[i]
            for i in range(width)
            if schema[i].get("N") in slot_names
        })
    return out


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------
def _day(value) -> date:
    """A Power BI date: epoch milliseconds, or an ISO string."""
    if isinstance(value, bool):
        raise SchemaDrift(f"KS: dashboard date {value!r} is not a date")
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc).date()
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip()[:10])
        except ValueError as exc:
            raise SchemaDrift(f"KS: dashboard date {value!r} is not a date") from exc
    raise SchemaDrift(f"KS: dashboard date {value!r} is not a date")


def _int(value) -> int | None:
    """A count, or None when the dashboard left the cell empty.

    Blank is "Kansas did not report this", which is not zero -- and Kansas does
    write a real 0 when it means one (in-person advance on the first day of the
    August 2026 primary is 0, not blank).
    """
    if value is None:
        return None
    if isinstance(value, bool):
        raise SchemaDrift(f"KS: {value!r} is not a ballot count")
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError as exc:
        raise SchemaDrift(f"KS: {value!r} is not a ballot count") from exc


def _add(*values: int | None) -> int | None:
    """Sum, keeping None when NOTHING was reported. 0 + None is 0, not None."""
    present = [v for v in values if v is not None]
    return sum(present) if present else None


def window(cycle: int) -> tuple[date, date]:
    """The date range a row must fall in to be this general's advance vote."""
    end = election_date(cycle)
    return end - timedelta(days=WINDOW_DAYS), end


def parse(payload: dict, cycle: int, as_of: date) -> FetchResult:
    """Parse one dashboard response into Kansas's cumulative daily series."""
    rows = decode_dsr(payload)
    if not rows:
        raise NotYetPublished(f"KS: the advance-voting dashboard is empty for {cycle}")

    opens, closes = window(cycle)
    seen: list[date] = []
    inside: dict[date, tuple[int | None, int | None, int | None]] = {}

    for row in rows:
        day = _day(row.get(COL_DATE))
        seen.append(day)
        if not (opens <= day <= closes):
            continue
        counts = (
            _int(row.get(MEASURE_SENT)),
            _int(row.get(MEASURE_RETURNED)),
            _int(row.get(MEASURE_INPERSON)),
        )
        if day in inside and inside[day] != counts:
            # One row per date is the shape of this table. Two rows for the same
            # date carrying different numbers would mean the model gained a
            # dimension we are silently summing away.
            raise SchemaDrift(f"KS: dashboard has two different rows for {day}")
        inside[day] = counts

    if not inside:
        # THE PRIMARY TRAP. The table is named for the cycle, not the election,
        # and between elections it holds the LAST one -- the August primary,
        # right now. Nothing inside the general's window means Kansas has not
        # published the general's advance vote, which STOPS the ladder: a weaker
        # tier would otherwise invent a number for a state that has none.
        span = f"{min(seen).isoformat()}..{max(seen).isoformat()}" if seen else "nothing"
        raise NotYetPublished(
            f"KS: the advance-voting dashboard carries no rows inside the {cycle} "
            f"general's window ({opens.isoformat()}..{closes.isoformat()}); "
            f"it carries {span}"
        )

    days = sorted(d for d in inside if d <= as_of)
    if not days:
        raise NotYetPublished(
            f"KS: advance voting for {cycle} opens "
            f"{min(inside).isoformat()}, after {as_of.isoformat()}"
        )

    result = FetchResult()
    for day in days:
        sent, returned, inperson = inside[day]
        result.state_rows.append(StateDay(
            cycle=cycle, state="KS", day=day,
            # Kansas's own headline: mail ballots back plus in-person advance.
            ballots_total=_add(returned, inperson),
            # The series is cumulative and skips weekends, so a snapshot
            # difference is not a day's new ballots. See the module docstring.
            ballots_new=None,
            mail_requested=sent,
            mail_returned=returned,
            inperson=inperson,
            # The dashboard has no party dimension. Kansas DOES register by
            # party -- this is "not reported here", never a zero.
            party_dem=None, party_rep=None, party_oth=None, party_npa=None,
        ))
    return result


# --------------------------------------------------------------------------
# The adapter
# --------------------------------------------------------------------------
class KSScraper(Adapter):
    """Tier 1 for Kansas: the SoS advance-voting dashboard's query API.

    Statewide only -- the model has no geography -- and party-blank, because the
    model has no party column.
    """

    state = "KS"
    name = "ks-sos"
    tier = TIER_SCRAPER

    # ---- discovery ---------------------------------------------------------
    def _resource_key(self) -> str:
        try:
            body = get(SOS_PAGE, state="KS", filename="advance-voting-data.html",
                       min_bytes=1024)
        except Exception:  # noqa: BLE001 -- discovery is a nicety, not a source
            log.debug("KS: SoS page unavailable; using the verified resource key")
            return POWERBI_RESOURCE_KEY
        found = resource_key(body.decode("utf-8", errors="replace"))
        if found is None:
            log.debug("KS: %s carries no embed token; using the verified key", SOS_PAGE)
            return POWERBI_RESOURCE_KEY
        return found

    def _cluster(self, key: str) -> str:
        token = base64.b64encode(
            json.dumps({"k": key}, separators=(",", ":")).encode()
        ).decode().rstrip("=")
        try:
            body = get(POWERBI_VIEW.format(token=token), state="KS",
                       filename="powerbi_view.html", min_bytes=1024)
        except Exception:  # noqa: BLE001
            log.debug("KS: embed page unavailable; using the verified cluster")
            return POWERBI_CLUSTER
        found = cluster(body.decode("utf-8", errors="replace"))
        if found is None:
            log.debug("KS: embed page names no ClusterUri; using the verified cluster")
            return POWERBI_CLUSTER
        return found

    def _models(self, host: str, key: str) -> list[Model]:
        url = host + MODELS_PATH.format(key=key)
        try:
            body = get(url, state="KS", filename="powerbi_modelsAndExploration.json",
                       headers={"X-PowerBI-ResourceKey": key}, min_bytes=1024)
        except SourceError as exc:
            log.debug("KS: %s (using the verified model constants)", exc)
            return [FALLBACK_MODEL]
        if looks_like_html(body):
            log.debug("KS: %s answered with a web page; using the verified model", url)
            return [FALLBACK_MODEL]
        try:
            payload = json.loads(body)
        except ValueError as exc:
            raise SchemaDrift(f"KS: {url} did not return JSON: {exc}") from exc
        return discover(payload)

    # ---- the query ---------------------------------------------------------
    def query(self, host: str, key: str, model: Model) -> dict:
        """POST the querydata request and return the parsed JSON.

        Uses the shared `_net` session: this host is not behind Cloudflare and
        answers a stock python-requests POST, which is the entire reason Kansas
        has a headless path at all.
        """
        url = host + QUERY_PATH
        headers = {
            **DEFAULT_HEADERS,
            "X-PowerBI-ResourceKey": key,
            "ActivityId": str(uuid.uuid4()),
            "RequestId": str(uuid.uuid4()),
            "Content-Type": "application/json;charset=UTF-8",
            "Accept": "application/json, text/plain, */*",
            "Origin": "https://app.powerbigov.us",
            "Referer": "https://app.powerbigov.us/",
        }
        try:
            response = SESSION.post(
                url, headers=headers, data=json.dumps(build_query(model)), timeout=120
            )
        except Exception as exc:  # noqa: BLE001 -- requests raises several types
            raise SourceError(f"KS: advance-voting dashboard query failed: {exc}") from exc
        if response.status_code != 200:
            raise SourceError(
                f"KS: advance-voting dashboard returned HTTP {response.status_code} "
                "(the report's resource key may have been rotated -- rediscover it "
                "from the iframe on " + SOS_PAGE + ")"
            )
        cache_path("KS", "powerbi_querydata.json").write_bytes(response.content)
        try:
            return response.json()
        except ValueError as exc:
            raise SchemaDrift(
                f"KS: advance-voting dashboard did not return JSON: {exc}"
            ) from exc

    # ---- the walk ----------------------------------------------------------
    def fetch(self, cycle: int, as_of: date) -> FetchResult:
        """The dashboard is the only source, so it is the authority on absence.

        `NotYetPublished` (STOP) only when the dashboard was read and positively
        has nothing inside this general's advance window. `SourceError` (fall
        through) when it could not be read or parsed -- "we could not look" must
        never be recorded as "Kansas has nothing".
        """
        key = self._resource_key()
        host = self._cluster(key)
        models = self._models(host, key)

        problems: list[str] = []
        absent: list[str] = []
        for model in models:
            try:
                result = parse(self.query(host, key, model), cycle, as_of)
            except NotYetPublished as exc:
                absent.append(str(exc))
                continue
            except SourceError as exc:   # SchemaDrift included
                problems.append(str(exc))
                continue
            return result

        if problems:
            raise SourceError(
                f"KS: the advance-voting dashboard could not be read "
                f"({'; '.join(problems + absent)})"
            )
        raise NotYetPublished("; ".join(absent))

    def fetch_history(self, cycle: int) -> FetchResult:
        """There is no archive.

        The SoS page was created for this cycle -- the Wayback CDX index has no
        capture of `advance-voting-data.html` before 2026-08-05, and every
        capture since carries the same digest -- and the report's model holds
        exactly one table, this cycle's. Kansas's only archived artefact is the
        post-election `22elec/2022-General-Election-Turnout-Information.xlsx`
        (VERIFIED 200, 13,851 bytes), which is county-level but carries only
        `ADVANCE BALLOTS RETURNED BY MAIL` and `TOTAL BALLOTS CAST`: no
        in-person advance figure, no daily series, and no 2024 equivalent
        published. That is a final, not a curve, so it is not read here.
        """
        raise NotYetPublished(
            f"KS: the advance-voting dashboard has no archive; {cycle} cannot be "
            "backfilled from it"
        )
