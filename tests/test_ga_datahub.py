"""Georgia's Election Data Hub — the Qlik route, replayed from a real transcript.

`tests/fixtures/ga/engine_2024_general.json` is a VERBATIM recording of every
frame in both directions of one real session against
`wss://sos-ga-gov.us.qlikcloudgov.com/app/7d780725-…` on 2026-09-08: open the
app, take the November 5 2024 general, read both county tables. 39 frames, 159
counties, 4,054,350 ballots.

Recording the wire rather than the parsed result is deliberate. Three of the
four bugs found building this adapter were PROTOCOL bugs that produced no error
at all — a select that silently selected nothing, a success flag read from the
wrong key, a measure label that differs from the column header — and none of
them is visible in a fixture of the finished rows.
"""

from __future__ import annotations

import gzip
import json
import re
from datetime import date
from pathlib import Path

import pytest

from ev.adapters import ga
from ev.adapters.base import NotYetPublished, SchemaDrift, SourceError
from ev.schema import TIER_SCRAPER

TRANSCRIPT = Path(__file__).parent / "fixtures" / "ga" / "engine_2024_general.json"


class Replay:
    """A socket that answers from the recording, by request id.

    Matching on the id rather than on the frame lets a test change a request and
    still get its recorded answer, which is what the drift tests below need. The
    protocol-pinning test asserts the requests themselves separately.
    """

    def __init__(self, log: list[dict], *, patch=None) -> None:
        self.sent: list[dict] = []
        self._answers = {
            entry["frame"]["id"]: entry["frame"]
            for entry in log
            if entry["dir"] == "recv" and "id" in entry["frame"]
        }
        self._queue: list[str] = []
        self._patch = patch or (lambda frame: frame)

    def send(self, text: str) -> None:
        frame = json.loads(text)
        self.sent.append(frame)
        answer = self._answers.get(frame["id"])
        if answer is None:
            raise AssertionError(f"no recorded answer for id {frame['id']} "
                                 f"({frame['method']})")
        self._queue.append(json.dumps(self._patch(json.loads(json.dumps(answer)))))

    def recv(self, timeout=None) -> str:
        if not self._queue:
            raise AssertionError("recv with nothing queued")
        return self._queue.pop(0)


@pytest.fixture
def log() -> list[dict]:
    return json.loads(TRANSCRIPT.read_text())


def run(log, *, patch=None):
    """Drive the real code against the recording; return (result, socket)."""
    ws = Replay(log, patch=patch)
    engine = ga.Engine(ws)
    doc = ga._handle(engine.call(-1, "OpenDoc", {"qDocName": ga.HUB_APP_ID}), "OpenDoc")
    names = ga.choose_election(engine, doc, date(2024, 11, 5))
    absentee = ga.county_measures(engine, doc, ga.HUB_ABSENTEE_TABLE,
                                  (ga.HUB_ACCEPTED, ga.HUB_REQUESTED))
    early = ga.county_measures(engine, doc, ga.HUB_EARLY_TABLE, (ga.HUB_ACCEPTED_EV,))
    return names, absentee, early, ws


# ------------------------------------------------------------- the whole path

def test_the_recorded_session_produces_georgias_2024_early_vote(log):
    names, absentee, early, _ = run(log)
    assert names == ["NOVEMBER 5, 2024 - GENERAL ELECTION"]
    assert len(absentee) == len(early) == ga.GA_COUNTIES == 159

    result = ga.hub_build(absentee, early, date(2024, 11, 5), 2024)
    assert len(result.county_rows) == 159
    assert len(result.state_rows) == 1

    state = result.state_rows[0]
    # Georgia's real 2024 early vote. If any of these move, something in the
    # chain is reading a different election or a different measure.
    assert state.ballots_total == 4_054_350
    assert state.mail_returned == 286_235
    assert state.inperson == 3_768_115
    assert state.mail_requested == 345_081
    assert (state.mail_returned or 0) + (state.inperson or 0) == state.ballots_total

    fulton = next(r for r in result.county_rows if r.county_fips == "13121")
    assert fulton.county_name == "Fulton County"
    assert (fulton.ballots_total, fulton.mail_returned, fulton.inperson) == (
        446_418, 29_304, 417_114)


def test_every_county_is_keyed_by_a_georgia_fips(log):
    _, absentee, early, _ = run(log)
    result = ga.hub_build(absentee, early, date(2024, 11, 5), 2024)
    assert all(len(r.county_fips) == 5 and r.county_fips.startswith("13")
               for r in result.county_rows)
    assert len({r.county_fips for r in result.county_rows}) == 159


def test_no_party_field_is_ever_written(log):
    """Georgia registers no voters by party. Never 0 -- see THE BLANK RULE."""
    _, absentee, early, _ = run(log)
    result = ga.hub_build(absentee, early, date(2024, 11, 5), 2024)
    for row in result.county_rows + result.state_rows:
        assert row.party_dem is None and row.party_rep is None
        assert row.party_oth is None and row.party_npa is None


# ------------------------------------------- the three invisible protocol bugs

def test_the_election_is_SELECTED_and_then_READ_BACK(log):
    """The app opens on a saved selection of the 2024 PRIMARY.

    The recording proves both halves: the app hands us 03/12/2024, and the
    session ends up on 11/05/2024 because this module put it there.
    """
    _, _, _, ws = run(log)
    methods = [f["method"] for f in ws.sent]
    assert "ClearAll" in methods
    assert "SelectListObjectValues" in methods
    # The read-back is a SECOND look at the field AFTER selecting, not the one
    # used to find the element number.
    assert methods.count("CreateSessionObject") >= 2
    assert methods.index("SelectListObjectValues") < len(methods) - 1


def test_a_selection_answering_qReturn_instead_of_qSuccess_is_a_REFUSAL(log):
    """SelectListObjectValues answers `qSuccess`. Reading `qReturn` gives None.

    That bug invented a refusal on live data with no error to notice it by, so
    the wrong key must stay wrong: if someone 'fixes' this to qReturn again,
    this test fails.
    """
    def strip_success(frame):
        if isinstance(frame.get("result"), dict) and "qSuccess" in frame["result"]:
            frame["result"] = {"qReturn": True}
        return frame
    with pytest.raises(SourceError, match="refused to select"):
        run(log, patch=strip_success)


def test_the_two_tables_spell_their_measure_differently(log):
    """`Ballots Accepted` and `Ballots Accepted (EV)` are both real labels.

    The rendered column header reads "Ballots Accepted" on BOTH tables --
    `qFallbackTitle` agrees -- but the early-voting table's `qLabel`, which is
    what this module reads, is `Ballots Accepted (EV)`. One shared constant
    turned that into a SchemaDrift on live data. Asserted against the recording
    itself rather than through the replay harness, because the fact belongs to
    Georgia's app and not to our call sequence.
    """
    assert ga.HUB_ACCEPTED != ga.HUB_ACCEPTED_EV

    labels = []
    for entry in log:
        if entry["dir"] != "recv":
            continue
        cube = (entry["frame"].get("result", {}) or {}).get("qProp", {}).get("qHyperCubeDef")
        if not cube:
            continue
        labels.append([m.get("qDef", {}).get("qLabel") for m in cube.get("qMeasures", [])])

    assert len(labels) == 2, f"expected two table property reads, saw {len(labels)}"
    absentee, early = labels
    assert ga.HUB_ACCEPTED in absentee
    assert ga.HUB_REQUESTED in absentee
    assert ga.HUB_ACCEPTED_EV in early
    # The exact confusion: the early table does NOT carry the absentee spelling.
    assert ga.HUB_ACCEPTED not in early


def test_suppression_is_turned_OFF_on_our_own_cube(log):
    """158 vs 159 counties is what zero-suppression looks like from outside.

    A dropped row and an unreported row are indistinguishable, so the published
    objects' definitions are reused with both suppression flags cleared.
    """
    _, _, _, ws = run(log)
    cubes = [f["params"]["qProp"]["qHyperCubeDef"]
             for f in ws.sent
             if f["method"] == "CreateSessionObject"
             and "qHyperCubeDef" in f["params"].get("qProp", {})]
    assert cubes, "no session hypercube was created"
    for cube in cubes:
        assert cube["qSuppressZero"] is False
        assert cube["qSuppressMissing"] is False
    # And the measures came from the app, not from this repo.
    source = Path(ga.__file__).read_text()
    assert "Ballots_Accepted_Counter" not in source
    assert "Early In-Person" not in source


# --------------------------------------------------------------- the refusals

def test_an_election_not_in_the_model_is_NOT_YET_PUBLISHED(log):
    """November 3 2026 was simply not there on 2026-09-08."""
    ws = Replay(log)
    engine = ga.Engine(ws)
    doc = ga._handle(engine.call(-1, "OpenDoc", {"qDocName": ga.HUB_APP_ID}), "OpenDoc")
    with pytest.raises(NotYetPublished) as exc:
        ga.choose_election(engine, doc, date(2026, 11, 3))
    assert "11/03/2026" in str(exc.value)
    # And it names the newest election it DOES hold, so the log says something.
    assert "newest is" in str(exc.value)


def test_a_readback_that_disagrees_is_DRIFT(log):
    """If the app reports a different selection than we made, publish nothing."""
    seen = {"n": 0}

    def lie(frame):
        result = frame.get("result", {})
        layout = result.get("qLayout", {}).get("qListObject") if isinstance(result, dict) else None
        if layout and layout.get("qDataPages"):
            seen["n"] += 1
            if seen["n"] == 2:  # the read-back, not the first lookup
                for page in layout["qDataPages"]:
                    for row in page.get("qMatrix", []):
                        row[0]["qState"] = "S"
        return frame

    with pytest.raises(SchemaDrift, match="selected"):
        run(log, patch=lie)


def test_tables_that_disagree_on_counties_are_DRIFT(log):
    _, absentee, early, _ = run(log)
    short = dict(early)
    short.pop(next(iter(short)))
    with pytest.raises(SchemaDrift, match="disagree on which counties"):
        ga.hub_build(absentee, short, date(2024, 11, 5), 2024)


def test_partial_coverage_publishes_NO_statewide_row(log):
    """A total over 158 of 159 counties looks exactly like a Georgia turnout figure."""
    _, absentee, early, _ = run(log)
    drop = next(iter(absentee))
    absentee.pop(drop)
    early.pop(drop)
    result = ga.hub_build(absentee, early, date(2024, 11, 5), 2024)
    assert len(result.county_rows) == 158
    assert result.state_rows == []


def test_a_token_endpoint_that_stops_returning_json_is_drift(monkeypatch):
    monkeypatch.setattr(ga._net, "get", lambda *a, **k: b"<html>rate limited</html>")
    with pytest.raises(SchemaDrift, match="did not return JSON"):
        ga.hub_token()


def test_a_token_endpoint_with_no_token_is_drift(monkeypatch):
    monkeypatch.setattr(ga._net, "get", lambda *a, **k: b'{"client_id":"x"}')
    with pytest.raises(SchemaDrift, match="no access_token"):
        ga.hub_token()


# ---------------------------------------------------------------- the adapter

def test_adapter_identity():
    scraper = ga.GADataHubScraper()
    assert scraper.state == "GA"
    assert scraper.name == "ga-datahub"
    assert scraper.tier == TIER_SCRAPER


def test_the_mvp_adapter_is_still_here_and_still_separate():
    """The reCAPTCHA route is not replaced, only unregistered.

    `GAScraper` parses a richer per-ballot file and works the moment a token is
    supplied; deleting it would throw away the only Georgia source that carries
    a daily curve.
    """
    assert ga.GAScraper.name == "ga-sos"
    assert ga.GAScraper is not ga.GADataHubScraper


def test_history_refuses_a_cycle_that_has_not_finished(monkeypatch):
    """The one guard `fetch` has that `fetch_history` cannot borrow.

    `fetch` refuses a day it has not reached; history has no as-of day, so the
    current-cycle guard stands in its place -- an in-progress election's
    "history" is what `fetch` is for. It fires BEFORE any socket is opened,
    which the monkeypatched `_session` proves: if the guard ever moves below the
    connect, this test starts hammering a state election service.
    """
    scraper = ga.GADataHubScraper()

    def forbidden(self):
        raise AssertionError("fetch_history opened a socket for a live cycle")

    monkeypatch.setattr(ga.GADataHubScraper, "_session", forbidden)
    for cycle in (2026, 2028):
        with pytest.raises(NotYetPublished) as exc:
            scraper.fetch_history(cycle)
        assert str(cycle) in str(exc.value)


# ==========================================================================
# THE DAILY HISTORY -- the refusal that was overturned
# ==========================================================================
#
# `tests/fixtures/ga/engine_2024_history.json.gz` is a VERBATIM recording of
# every frame in both directions of one real `fetch_history(2024)` against
# `wss://sos-ga-gov.us.qlikcloudgov.com/app/7d780725-…` on 2026-09-09: open the
# app, take the November 5 2024 general, read the election's own absentee
# window-open date, then read FOUR hypercubes -- absentee and early in-person
# by county AND day, and the two final county tables the days are checked
# against. 89 frames, 6,906 matrix rows, 159 counties, 50 days.
#
# It is gzipped because it is 2.2 MB raw and 111 KB compressed, and what makes
# this fixture worth keeping is that it is COMPLETE: all 159 counties and every
# day, so the statewide curve and the reconciliation are both exercised on real
# numbers. Trimming counties out would have cost both. `gzip.open` is the only
# difference from reading it with `open`.

HISTORY = Path(__file__).parent / "fixtures" / "ga" / "engine_2024_history.json.gz"


@pytest.fixture
def history_log() -> list[dict]:
    with gzip.open(HISTORY, "rt", encoding="utf-8") as handle:
        return json.load(handle)


class ReplaySession:
    """What `GADataHubScraper._session` returns, backed by the recording."""

    def __init__(self, log, *, patch=None) -> None:
        self.ws = Replay(log, patch=patch)

    def __enter__(self):
        return self.ws

    def __exit__(self, *exc):
        return False


def run_history(monkeypatch, log, *, cycle: int = 2024, patch=None):
    session = ReplaySession(log, patch=patch)
    monkeypatch.setattr(ga.GADataHubScraper, "_session", lambda self: session)
    return ga.GADataHubScraper().fetch_history(cycle), session.ws


def _cells(log, width: int):
    """Every matrix row of `width` cells in the recording, in order."""
    for entry in log:
        if entry["dir"] != "recv":
            continue
        for page in (entry["frame"].get("result", {}) or {}).get("qDataPages", []) or []:
            for row in page.get("qMatrix", []):
                if len(row) == width:
                    yield row


# ---------------------------------------------------- it is a SERIES, not a final

def test_the_recorded_history_is_a_daily_county_series(monkeypatch, history_log):
    """The whole point: many days per county, not one Election-Day figure.

    `fetch_history` used to refuse because "the app holds each election's
    current position only". It does not -- `Ballot Accepted Date` is a
    per-ballot field on the same table as `County` -- and this is the proof.
    """
    result, _ = run_history(monkeypatch, history_log)

    days = sorted({row.day for row in result.county_rows})
    # The axis starts at Georgia's first accepted ballot, not at the window
    # open: `Absentee_Min_Date` is 2024-09-17 and nothing came back until the
    # 19th, so the clip's floor and the curve's first point are not the same day.
    assert len(days) == 48
    assert days[0] == date(2024, 9, 19)
    assert days[-1] == date(2024, 11, 5)     # Election Day, days_to_election 0
    assert len({row.county_fips for row in result.county_rows}) == ga.GA_COUNTIES
    assert len(result.county_rows) == 5_515

    # A FINAL would be 159 rows on one day. Every large county has a curve.
    fulton = [r for r in result.county_rows if r.county_fips == "13121"]
    assert len(fulton) == 47
    assert fulton[-1].day == date(2024, 11, 5)
    assert (fulton[-1].ballots_total, fulton[-1].mail_returned,
            fulton[-1].inperson) == (446_279, 29_172, 417_107)
    # ...and it is CUMULATIVE and monotone, which is what the page draws.
    assert [r.ballots_total for r in fulton] == sorted(r.ballots_total for r in fulton)
    assert fulton[0].ballots_total < fulton[-1].ballots_total


def test_the_statewide_curve_is_the_counties_summed_day_by_day(monkeypatch,
                                                               history_log):
    result, _ = run_history(monkeypatch, history_log)
    assert len(result.state_rows) == 48

    by_day: dict = {}
    for row in result.county_rows:
        bucket = by_day.setdefault(row.day, [0, 0, 0])
        bucket[0] += row.ballots_total
        bucket[1] += row.mail_returned
        bucket[2] += row.inperson
    for row in result.state_rows:
        want = by_day.get(row.day, [0, 0, 0])
        assert [row.ballots_total, row.mail_returned, row.inperson] == want

    last = result.state_rows[-1]
    # Georgia's real 2024 early vote, on an ACCEPTED-DATE basis. Independently
    # corroborated by georgiavotesvisual.com's return-date series, which is
    # derived from the other Georgia route entirely: 4,051,640 there.
    assert last.day == date(2024, 11, 5)
    assert (last.ballots_total, last.mail_returned, last.inperson) == (
        4_052_653, 284_581, 3_768_072)
    assert (last.mail_returned or 0) + (last.inperson or 0) == last.ballots_total
    assert sum(r.ballots_new for r in result.state_rows) == last.ballots_total


def test_no_requested_series_is_invented_and_no_party_is_ever_written(
        monkeypatch, history_log):
    """Two blanks, two different reasons, and neither may become a 0.

    Nothing in the app dates a ballot REQUEST, so there is no daily requested
    curve and the final's 345,081 must not be stamped on fifty days. Georgia
    registers no voters by party at all.
    """
    result, _ = run_history(monkeypatch, history_log)
    for row in result.state_rows:
        assert row.mail_requested is None
    for row in result.county_rows + result.state_rows:
        assert row.party_dem is None and row.party_rep is None
        assert row.party_oth is None and row.party_npa is None
        assert 0 not in (row.party_dem, row.party_rep, row.party_oth, row.party_npa)


def test_every_row_is_keyed_by_a_five_digit_georgia_fips(monkeypatch, history_log):
    result, _ = run_history(monkeypatch, history_log)
    assert all(len(r.county_fips) == 5 and r.county_fips.startswith("13")
               for r in result.county_rows)


# ------------------------------------------------------------ the dirty dates

def test_the_recording_really_does_carry_georgias_broken_dates(history_log):
    """The clip is not defensive programming; the damage is in the data.

    `Ballot Accepted Date` for the 2024 general contains dates a Georgia ballot
    cannot have been accepted on. Asserted against the raw recording, because
    the fact belongs to Georgia's file and not to our parser.
    """
    seen = {(row[1].get("qText") or "") for row in _cells(history_log, 3)}
    assert "10/31/2224" in seen
    assert "10/07/1951" in seen
    assert "02/15/1977" in seen
    # ...and dates after the election, which the repo refuses everywhere.
    assert "12/31/2024" in seen


def test_dates_outside_the_elections_own_window_are_clipped(monkeypatch,
                                                           history_log):
    result, _ = run_history(monkeypatch, history_log)
    for row in result.county_rows + result.state_rows:
        assert date(2024, 9, 17) <= row.day <= date(2024, 11, 5)
    # 10/07/1951 and 10/31/2224 are in the recording (above) and in no row here.
    assert min(r.day for r in result.county_rows).year == 2024


def test_a_date_the_module_cannot_read_is_DRIFT(monkeypatch, history_log):
    """Rule 3. The whole series hangs off this cell; do not guess."""
    def scramble(frame):
        for page in (frame.get("result", {}) or {}).get("qDataPages", []) or []:
            for row in page.get("qMatrix", []):
                if len(row) == 3:
                    row[1]["qText"] = "sometime in October"
        return frame

    with pytest.raises(SchemaDrift, match="not a date"):
        run_history(monkeypatch, history_log, patch=scramble)


# ------------------------------------------------- reconciled against the final

def test_the_daily_series_is_checked_against_the_final_table(monkeypatch,
                                                             history_log):
    """The last day is what the DAYS say -- and it is proved close to the final.

    4,052,653 across the days against 4,054,350 in the final tables: 1,697
    ballots, 0.042%, whose accepted date is missing or outside the window. The
    series is never topped up to close that gap, because those ballots were not
    accepted on any day this could put them on.
    """
    result, _ = run_history(monkeypatch, history_log)
    last = result.state_rows[-1]
    final = 4_054_350
    assert last.ballots_total < final
    assert final - last.ballots_total == 1_697
    assert (final - last.ballots_total) / final < ga.HISTORY_STATE_SHORTFALL_FRACTION


def test_a_county_the_day_cube_stops_reporting_is_DRIFT(monkeypatch, history_log):
    """A missing county is why the daily cubes may run with suppression ON.

    Zeroing Cobb's per-day cells leaves it in the final tables with 319,373
    ballots and in the daily ones with none -- which is exactly what a county
    silently dropping out of a suppressed cube looks like from outside.
    """
    def silence_cobb(frame):
        for page in (frame.get("result", {}) or {}).get("qDataPages", []) or []:
            for row in page.get("qMatrix", []):
                if len(row) == 3 and (row[0].get("qText") or "").strip() == "COBB":
                    row[2]["qNum"] = 0
        return frame

    with pytest.raises(SchemaDrift, match="COBB"):
        run_history(monkeypatch, history_log, patch=silence_cobb)


def test_days_that_total_MORE_than_the_final_are_DRIFT():
    """The shortfall can only ever run one way, so the other way is a bug.

    The day-dimensioned cube reads a SUBSET of the same ballots through the same
    measure. More across the days than in the final means the two objects have
    stopped measuring the same thing.
    """
    with pytest.raises(SchemaDrift, match="disagree"):
        ga._reconcile("absentee", {"FULTON": 101}, {"FULTON": 100})
    # ...and a county that is merely a little short is fine: that is the window.
    ga._reconcile("absentee", {"FULTON": 99}, {"FULTON": 100})


def test_a_county_short_by_more_than_the_window_can_explain_is_DRIFT():
    with pytest.raises(SchemaDrift, match="ballots short"):
        ga._reconcile("absentee", {"COBB": 10_000}, {"COBB": 25_760})


def test_a_county_only_the_day_cube_knows_about_is_DRIFT():
    """It would never be emitted -- the row loop walks the FINAL table's counties.

    So an extra county in the day cube is a row that vanishes in silence, which
    is precisely what `_reconcile` exists to make impossible.
    """
    with pytest.raises(SchemaDrift, match="missing from the final"):
        ga._reconcile("absentee", {"FULTON": 10, "ATLANTIS": 5}, {"FULTON": 10})


def test_a_statewide_shortfall_spread_across_every_county_is_DRIFT():
    """Each county inside its own 5% ceiling, the state outside its 1% one."""
    daily = {f"C{i}": 960 for i in range(100)}
    final = {f"C{i}": 1_000 for i in range(100)}
    with pytest.raises(SchemaDrift, match="every county at once"):
        ga._reconcile("absentee", daily, final)


# ---------------------------------------------- the app's own shapes, not ours

def test_the_day_dimension_is_lifted_off_the_apps_own_chart(monkeypatch,
                                                            history_log):
    """`=[Ballot Accepted Date]` is an EXPRESSION, so it is never composed here.

    The early-voting sheet already publishes a chart dimensioned by it. Both
    dimensions come off published objects and are swapped between them; this
    module writes neither.
    """
    _, ws = run_history(monkeypatch, history_log)
    cubes = [f["params"]["qProp"]["qHyperCubeDef"]
             for f in ws.sent
             if f["method"] == "CreateSessionObject"
             and "qHyperCubeDef" in f["params"].get("qProp", {})]
    two_dim = [c for c in cubes if len(c["qDimensions"]) == 2]
    assert len(two_dim) == 2, "expected one day cube per method"
    for cube in two_dim:
        fields = [f for d in cube["qDimensions"]
                  for f in d["qDef"]["qFieldDefs"]]
        assert fields == ["County", ga.HUB_DAY_DIM]
    # The constant is only ever an assertion about the app, never a source of
    # truth: an object dimensioned by something else is refused outright.
    with pytest.raises(SchemaDrift, match="dimensioned by"):
        ga._dimension({"qDimensions": [{"qDef": {"qFieldDefs": ["Date"]}}]},
                      [ga.HUB_DAY_DIM], ga.HUB_EV_BY_DAY)


def test_suppression_is_ON_for_the_day_cubes_and_OFF_for_the_finals(
        monkeypatch, history_log):
    """Opposite settings, opposite reasons. See `county_day_measures`.

    On the finals a missing row is indistinguishable from a real zero, so
    suppression is off. On a county x day cube the zeros ARE the cross product
    and `_reconcile` catches what suppression could hide.
    """
    _, ws = run_history(monkeypatch, history_log)
    cubes = [f["params"]["qProp"]["qHyperCubeDef"]
             for f in ws.sent
             if f["method"] == "CreateSessionObject"
             and "qHyperCubeDef" in f["params"].get("qProp", {})]
    for cube in cubes:
        want = len(cube["qDimensions"]) == 2
        assert cube["qSuppressZero"] is want
        assert cube["qSuppressMissing"] is want


def test_the_history_path_overrides_the_saved_2024_PRIMARY_selection_too(
        monkeypatch, history_log):
    """GUARD PARITY. The app opens on 03/12/2024 and `ClearAll` does not fix it.

    A history run that skipped `choose_election` would publish March's
    presidential preference primary as November's general.
    """
    _, ws = run_history(monkeypatch, history_log)
    methods = [f["method"] for f in ws.sent]
    assert methods.count("ClearAll") == 1
    assert "SelectListObjectValues" in methods
    assert methods.index("SelectListObjectValues") < methods.index("GetObject")


def test_2024_is_the_oldest_cycle_the_app_can_backfill(history_log):
    """2022 is not in the model, so `fetch_history(2022)` refuses on its own.

    `Election Date` holds 45 elections in the recording and the oldest is
    01/23/2024. `choose_election` raises NotYetPublished for a date the app does
    not have -- one socket, no parsing -- and the ladder falls through. Asserted
    against the recording because the fact belongs to Georgia's app.
    """
    dates = set()
    for entry in history_log:
        if entry["dir"] != "recv":
            continue
        listbox = ((entry["frame"].get("result", {}) or {})
                   .get("qLayout", {}).get("qListObject") or {})
        for page in listbox.get("qDataPages", []):
            for row in page.get("qMatrix", []):
                dates.add(row[0].get("qText") or "")
    dates = {d for d in dates if re.fullmatch(r"\d{2}/\d{2}/\d{4}", d)}
    assert len(dates) == 45
    assert min(dates, key=lambda d: (d[6:], d[:2], d[3:5])) == "01/23/2024"
    assert not any(d.endswith("/2022") for d in dates)


def test_the_window_start_is_the_apps_own_date_and_not_a_span_we_chose():
    """A hardcoded "45 days before" would move the day Georgia changed a statute."""
    class Stub:
        def __init__(self, values):
            self.values = values

        def call(self, handle, method, params):
            if method == "CreateSessionObject":
                return {"qReturn": {"qHandle": 7}}
            return {"qLayout": {"qListObject": {"qDataPages": [{"qMatrix": [
                [{"qText": text, "qElemNumber": i, "qState": state}]
                for i, (text, state) in enumerate(self.values)]}]}}}

    assert ga.window_start(Stub([("09/17/2024", "O"), ("05/21/2024", "X")]),
                           1) == date(2024, 9, 17)
    with pytest.raises(SchemaDrift, match="Absentee_Min_Date"):
        ga.window_start(Stub([("09/17/2024", "O"), ("09/18/2024", "O")]), 1)
    with pytest.raises(SchemaDrift, match="Absentee_Min_Date"):
        ga.window_start(Stub([]), 1)
