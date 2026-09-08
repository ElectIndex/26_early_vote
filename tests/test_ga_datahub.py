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

import json
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


def test_history_is_refused_by_name(log):
    """Every past election is one selection away, and that is the trap.

    The app holds each election's CURRENT position only -- no daily history --
    so a backfill would stamp one Election-Day figure across a whole window.
    """
    scraper = ga.GADataHubScraper()
    for cycle in (2020, 2022, 2024):
        with pytest.raises(NotYetPublished) as exc:
            scraper.fetch_history(cycle)
        assert str(cycle) in str(exc.value)
