"""`ev ingest` end to end, because nothing else ran it.

⚠️ THIS FILE EXISTS BECAUSE A BARE `NameError` REACHED PRODUCTION AND STAYED
THERE FOR AN HOUR. `cmd_ingest` grew a call to `_towns.rows_of(result)` without
the matching import, and every scheduled run died on

    NameError: name '_towns' is not defined

The suite was 656 tests green at the time. Not one of them called the command
the job calls: every adapter was tested against its own fixture, `publish.py`
was tested against hand-built rows, and the seam between them -- the forty lines
of `cmd_ingest` that walk the ladder and hand the result to the publishers --
was covered by nobody. Two scheduled runs failed on it (09:54 and 14:38 on
2026-09-06) before it was noticed, and the only symptom a reader would ever see
is a stale badge.

So these tests are deliberately shallow and deliberately WIDE. They do not check
that any state's numbers are right -- forty adapter test modules do that. They
check that the command runs, that every file it promises appears, and that the
paths which only execute when a state actually returns data are executed at
least once. An import error, a typo'd attribute or a renamed publisher function
fails here rather than in a cron log at two in the morning.

THE NETWORK IS NEVER TOUCHED. `registry.ladder` is replaced with a stub adapter,
which is also what lets a single test cover the town path -- no real state
returns town rows and county rows and demographic rows at once.
"""

from __future__ import annotations

import csv
import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from ev import cli
from ev.adapters import _methods, _towns
from ev.adapters.base import Adapter, FetchResult, NotYetPublished, SourceError
from ev.adapters.manual import ManualAdapter
from ev.schema import (
    CountyDay, DemoDay, MethodDay, Provenance, StateDay, TownDay, TIER_CIVIC,
    TIER_SCRAPER,
)

AS_OF = date(2026, 10, 20)
CYCLE = 2026

# Two real North Carolina counties, so the FIPS survive normalisation, and one
# real Maine town so the municipality path has something to roll up.
MECKLENBURG, WAKE = "37119", "37183"
PORTLAND_ME = "2300360545"


class StubScraper(Adapter):
    """A state that reports everything, so one run exercises every publisher."""

    name = "stub"
    tier = TIER_SCRAPER

    def fetch(self, cycle: int, as_of: date) -> FetchResult:
        state = self.state
        result = FetchResult(
            state_rows=[StateDay(
                cycle=cycle, state=state, day=as_of,
                ballots_total=1_200, mail_returned=700, inperson=500,
                party_dem=600, party_rep=400, party_npa=200,
            )],
            county_rows=[
                CountyDay(cycle=cycle, state=state, county_fips=MECKLENBURG,
                          day=as_of, county_name="Mecklenburg County",
                          ballots_total=800, mail_returned=500, inperson=300),
                CountyDay(cycle=cycle, state=state, county_fips=WAKE,
                          day=as_of, county_name="Wake County",
                          ballots_total=400, mail_returned=200, inperson=200),
            ],
            demo_rows=[
                DemoDay(cycle=cycle, state=state, day=as_of,
                        dimension="sex", bucket="female", ballots_total=650),
                DemoDay(cycle=cycle, state=state, day=as_of,
                        dimension="sex", bucket="male", ballots_total=550),
            ],
        )
        # The line that broke. Towns ride on the result as an attribute rather
        # than a field, so nothing type-checks this seam for us.
        # The county is NOT passed in: a cousub GEOID is state(2)+county(3)+
        # cousub(5), so the rollup is a slice of the key rather than a crosswalk.
        _towns.attach(result, [TownDay(
            cycle=cycle, state=state, day=as_of,
            town_geoid=PORTLAND_ME, town_name="Portland",
            ballots_total=1_200,
        )])
        # ...and the fifth table, which rides the same seam for the same reason.
        _methods.attach(result, [
            MethodDay(cycle=cycle, state=state, county_fips="37119", day=as_of,
                      method="mail", ballots_total=200, party_dem=120,
                      party_rep=80),
            MethodDay(cycle=cycle, state=state, county_fips="37119", day=as_of,
                      method="inperson", ballots_total=200, party_dem=90,
                      party_rep=110),
        ])
        return result


class SilentScraper(Adapter):
    """A state whose early voting has not opened. The normal case before October."""

    name = "stub-pending"
    tier = TIER_SCRAPER

    def fetch(self, cycle: int, as_of: date) -> FetchResult:
        raise NotYetPublished("early voting has not opened")


def args(tmp_path: Path, **over):
    """The parsed namespace `main()` would hand `cmd_ingest`."""
    # `--output` is a GLOBAL option and goes before the subcommand. Getting
    # that wrong is exactly the kind of thing this file exists to catch.
    parsed = cli.build_parser().parse_args(
        ["--output", str(tmp_path / "output"),
         "ingest", "--cycle", str(CYCLE), "--as-of", AS_OF.isoformat(),
         "--state", "NC"]
    )
    for key, value in over.items():
        setattr(parsed, key, value)
    return parsed


@pytest.fixture()
def stub_ladder(monkeypatch):
    """Replace the ladder so no test in this file can reach the network."""
    def install(adapter_cls):
        monkeypatch.setattr(
            cli, "ladder", lambda state: [adapter_cls(state=state)]
        )
    return install


# --------------------------------------------------------------------------
# The regression this file is named for
# --------------------------------------------------------------------------
def test_ingest_runs_and_writes_every_file_it_promises(tmp_path, stub_ladder):
    """The whole seam, in one call. This is the test that was missing.

    A state that returns state rows, county rows, town rows and demographic
    rows exercises all four publishers plus the status write. The NameError
    that took down two scheduled runs lived between the second and the third.
    """
    stub_ladder(StubScraper)
    assert cli.cmd_ingest(args(tmp_path)) == 0

    out = tmp_path / "output"
    for relative in ("ev_state_daily.csv", "counties/nc.csv",
                     "towns/nc.csv", "demo/nc.csv", "ev_status.json"):
        assert (out / relative).exists(), f"{relative} was not written"


def test_the_published_rows_are_the_ones_the_adapter_returned(tmp_path, stub_ladder):
    """Shallow on purpose: that the numbers ARRIVE, not that they are right."""
    stub_ladder(StubScraper)
    cli.cmd_ingest(args(tmp_path))
    out = tmp_path / "output"

    state_rows = list(csv.DictReader((out / "ev_state_daily.csv").open()))
    assert [r["state"] for r in state_rows] == ["NC"]
    assert state_rows[0]["ballots_total"] == "1200"
    assert state_rows[0]["source_name"] == "stub"

    counties = list(csv.DictReader((out / "counties" / "nc.csv").open()))
    assert {r["county_fips"] for r in counties} == {MECKLENBURG, WAKE}

    towns = list(csv.DictReader((out / "towns" / "nc.csv").open()))
    assert [r["town_geoid"] for r in towns] == [PORTLAND_ME]
    # Derived from the GEOID at write time, never supplied by the adapter.
    assert towns[0]["county_fips"] == PORTLAND_ME[:5]

    demo = list(csv.DictReader((out / "demo" / "nc.csv").open()))
    assert {r["bucket"] for r in demo} == {"female", "male"}


def test_the_status_file_records_the_run(tmp_path, stub_ladder):
    """It is written unconditionally, because a status file that stops
    advancing is the signal the page uses to show a stale badge."""
    stub_ladder(StubScraper)
    cli.cmd_ingest(args(tmp_path))
    status = json.loads((tmp_path / "output" / "ev_status.json").read_text())
    assert status["as_of"] == AS_OF.isoformat()
    assert status["cycle"] == CYCLE
    assert status["states"]["NC"]["status"] == "ok"
    assert status["summary"]["ok"] == 1


class UnchangingScraper(Adapter):
    """A state that reports the SAME number every run, restamped each time.

    Which is what every state does most days: the file is re-fetched, the numbers
    have not moved, and the adapter builds a row with a fresh `retrieved_at`.
    `publish.merge_rows` then keeps the row it already had, stamp included.
    """

    name = "stub-unchanging"
    tier = TIER_SCRAPER
    runs = 0

    def fetch(self, cycle: int, as_of: date) -> FetchResult:
        type(self).runs += 1
        stamp = f"2026-10-20T{4 + 2 * type(self).runs:02d}:00:00+00:00"
        return FetchResult(state_rows=[StateDay(
            cycle=cycle, state=self.state, day=as_of, ballots_total=1_200,
            provenance=Provenance(TIER_SCRAPER, self.name, retrieved_at=stamp),
        )])


@pytest.fixture()
def ticking_clock(monkeypatch):
    """Two hours per run, so two runs cannot land on the same second."""
    ticks = iter([datetime(2026, 10, 20, hour, 0, tzinfo=timezone.utc)
                  for hour in (6, 8, 10, 12)])

    class _Datetime:
        @staticmethod
        def now(tz=None):
            return next(ticks)

        strptime = staticmethod(datetime.strptime)

    monkeypatch.setattr(cli, "datetime", _Datetime)


def test_the_status_file_says_when_each_state_was_last_CHECKED(
        tmp_path, stub_ladder, ticking_clock):
    """⚠️ This is the site's only freshness source, and nothing wrote it until
    2026-09-07.

    ev-core.js has always read `s.status.retrievedAt || s.latest.retrieved_at`
    -- written that way on purpose -- but the status file carried no such field,
    so every stale badge was computed from the DATA ROW's timestamp. That was
    survivable only while every run rewrote every row. Once an unchanged row
    started keeping its original stamp, a state reporting the same number for two
    days would have badged STALE at 36 hours while we were checking it every two.

    ⚠️ AND THE ASSERTION HAD TO BE REWRITTEN, BECAUSE IT COULD NOT FAIL. It read

        assert status["states"]["NC"]["retrieved_at"] == status["generated_at"]

    and `cmd_ingest` assigns both sides from the SAME local `checked_at`. It was
    a tautology about one variable, not a test of the regression it is named for
    -- which is a difference between two files that only appears on the SECOND
    run of an unchanged state.

    So: run twice, two hours apart, with the number unmoved. The CSV row's stamp
    must FREEZE (that fact is "when the number last changed") while the status
    file's stamp must ADVANCE (that fact is "when we last looked"). One assertion
    each, and they now genuinely disagree.
    """
    UnchangingScraper.runs = 0
    stub_ladder(UnchangingScraper)

    cli.cmd_ingest(args(tmp_path))
    out = tmp_path / "output"
    first_row = list(csv.DictReader((out / "ev_state_daily.csv").open()))[0]
    first_status = json.loads((out / "ev_status.json").read_text())

    cli.cmd_ingest(args(tmp_path))
    second_row = list(csv.DictReader((out / "ev_state_daily.csv").open()))[0]
    second_status = json.loads((out / "ev_status.json").read_text())

    # The adapter offered a NEW stamp on the second run and the merge refused it,
    # because the numbers did not move.
    assert second_row["ballots_total"] == first_row["ballots_total"] == "1200"
    assert second_row["retrieved_at"] == first_row["retrieved_at"], (
        "an unchanged row was restamped; the file now churns every run")

    # ...and the status file advanced anyway, which is the whole point.
    assert second_status["generated_at"] != first_status["generated_at"]
    assert (second_status["states"]["NC"]["retrieved_at"]
            == second_status["generated_at"])
    assert (second_status["states"]["NC"]["retrieved_at"]
            != first_status["states"]["NC"]["retrieved_at"]), (
        "the freshness stamp froze with the data row; every state would badge "
        "STALE while we were checking it every two hours")
    assert second_status["states"]["NC"]["retrieved_at"] != second_row["retrieved_at"]


def test_a_pending_state_is_stamped_too(tmp_path, stub_ladder):
    """"We checked Montana and it still has nothing" is exactly as much a
    freshness fact as a count is -- and pending is the whole board in
    September, so a badge that cannot age is a badge that says nothing."""
    stub_ladder(SilentScraper)
    cli.cmd_ingest(args(tmp_path))
    status = json.loads((tmp_path / "output" / "ev_status.json").read_text())
    assert status["states"]["NC"]["status"] == "pending"
    assert status["states"]["NC"]["retrieved_at"] == status["generated_at"]


# --------------------------------------------------------------------------
# The normal case, which must not look like a failure
# --------------------------------------------------------------------------
def test_a_state_that_has_not_opened_is_pending_and_exits_zero(tmp_path, stub_ladder):
    """Before October every state answers NotYetPublished. A red badge all
    September teaches everyone to ignore the badge in November."""
    stub_ladder(SilentScraper)
    assert cli.cmd_ingest(args(tmp_path)) == 0
    status = json.loads((tmp_path / "output" / "ev_status.json").read_text())
    assert status["states"]["NC"]["status"] == "pending"
    # No data means no row, not a row of zeros.
    assert not (tmp_path / "output" / "ev_state_daily.csv").exists()


def test_pending_is_not_a_failure_even_under_strict(tmp_path, stub_ladder):
    stub_ladder(SilentScraper)
    assert cli.cmd_ingest(args(tmp_path, strict=True)) == 0


# --------------------------------------------------------------------------
# ...AND A REAL FAILURE, WHICH `--strict` COULD NOT SEE UNTIL NOW
# --------------------------------------------------------------------------
class BrokenScraper(Adapter):
    """A state whose own file is unreachable. `SourceError` falls through."""

    name = "stub-broken"
    tier = TIER_SCRAPER

    def fetch(self, cycle: int, as_of: date) -> FetchResult:
        raise SourceError("502 from the Secretary of State")


@pytest.fixture()
def dead_ladder(tmp_path, monkeypatch):
    """The real production shape of a total outage: every tier broken, and the
    real `ManualAdapter` on the floor with nothing hand-typed in it."""
    monkeypatch.setattr(cli, "ladder", lambda state: [
        BrokenScraper(state=state),
        ManualAdapter(state=state, data_dir=tmp_path / "no-manual-entries"),
    ])


def test_a_state_that_used_no_tier_at_all_is_failed_and_strict_exits_one(
        tmp_path, dead_ladder):
    """⚠️ `test_pending_is_not_a_failure_even_under_strict` ABOVE STILL PASSES IF
    `--strict` IS REPLACED BY `return 0`. It asserts one half of a branch.

    This is the other half, and until the ladder learned that a NotYetPublished
    from the FLOOR is not a state that has not started voting, it could not be
    written at all: `ManualAdapter` is always the last rung and always declines,
    so `run_state` returned PENDING for a total outage. `ev_status.json`'s failed
    count was structurally 0, ingest.yml's "Every tier failed for:" line could
    never print, and this exit code could never be 1.
    """
    assert cli.cmd_ingest(args(tmp_path, strict=True)) == 1

    status = json.loads((tmp_path / "output" / "ev_status.json").read_text())
    assert status["states"]["NC"]["status"] == "failed"
    assert status["summary"]["failed"] == 1 and status["summary"]["pending"] == 0
    # The attempts carry the reason, which is what reachability.yml reads.
    assert [a["result"] for a in status["states"]["NC"]["attempts"]] == [
        "SourceError", "not_yet_published"]


def test_a_total_outage_without_strict_still_exits_zero(tmp_path, dead_ladder):
    """The scheduled job runs without `--strict` on purpose. It must still say so
    in the file and in the summary line rather than exiting non-zero."""
    assert cli.cmd_ingest(args(tmp_path)) == 0
    status = json.loads((tmp_path / "output" / "ev_status.json").read_text())
    assert status["states"]["NC"]["status"] == "failed"


def test_a_failed_state_publishes_no_row_of_zeros(tmp_path, dead_ladder):
    """A failure is a gap, exactly like pending. It is never a zero."""
    cli.cmd_ingest(args(tmp_path))
    assert not (tmp_path / "output" / "ev_state_daily.csv").exists()


# --------------------------------------------------------------------------
# ONE BAD ADAPTER MUST NOT ABORT THE PUBLISH PHASE FOR EVERYONE
# --------------------------------------------------------------------------
class BadDimensionScraper(Adapter):
    """Emits a demographic dimension outside DEMO_DIMENSIONS.

    The realistic version of this is an adapter that learns to read an
    `ethnicity` column, or a normalize.py vocabulary that grows a bucket the
    schema has not been told about.
    """

    name = "stub-bad-dimension"
    tier = TIER_SCRAPER

    def fetch(self, cycle: int, as_of: date) -> FetchResult:
        return FetchResult(
            state_rows=[StateDay(cycle=cycle, state=self.state, day=as_of,
                                 ballots_total=1_200)],
            demo_rows=[DemoDay(cycle=cycle, state=self.state, day=as_of,
                               dimension="ethnicity", bucket="hispanic",
                               ballots_total=5)],
        )


def test_an_unknown_dimension_costs_one_tier_not_the_status_file(tmp_path, monkeypatch):
    """⚠️ THIS USED TO KILL THE RUN AT THE WORST POSSIBLE MOMENT.

    `demo_row_to_dict` refuses an unknown dimension, and it runs in the PUBLISH
    phase -- AFTER `ev_state_daily.csv` is written and BEFORE `ev_status.json`
    is. So one adapter's new column aborted the run for all thirty-five states
    and left the page holding half-new data behind a status file that never
    advanced, with no message anywhere. Proven by construction: the state CSV
    existed on disk and the status file did not.

    The check now happens when the row is built, inside `adapter.fetch`, which
    `run_state` wraps -- so it is an ordinary fall-through and every other state
    publishes normally.
    """
    class Fallback(StubScraper):
        name = "stub-fallback"
        tier = TIER_CIVIC

    monkeypatch.setattr(cli, "ladder", lambda state: [
        BadDimensionScraper(state=state), Fallback(state=state)])

    assert cli.cmd_ingest(args(tmp_path)) == 0
    out = tmp_path / "output"
    status = json.loads((out / "ev_status.json").read_text())
    assert status["states"]["NC"]["status"] == "ok"
    assert status["states"]["NC"]["attempts"][0]["result"] == "crash"
    assert (out / "ev_state_daily.csv").exists()


def test_an_unknown_dimension_with_no_fallback_still_writes_the_status_file(
        tmp_path, stub_ladder):
    """The status file is the page's only explanation, so it is the last thing
    that may be allowed to go missing."""
    stub_ladder(BadDimensionScraper)
    assert cli.cmd_ingest(args(tmp_path)) == 0
    status = json.loads((tmp_path / "output" / "ev_status.json").read_text())
    assert status["states"]["NC"]["status"] == "failed"


# --------------------------------------------------------------------------
# Dry run writes nothing at all
# --------------------------------------------------------------------------
def test_dry_run_touches_no_file(tmp_path, stub_ladder):
    stub_ladder(StubScraper)
    assert cli.cmd_ingest(args(tmp_path, dry_run=True)) == 0
    assert not (tmp_path / "output").exists()


# --------------------------------------------------------------------------
# The command is reachable the way the job reaches it
# --------------------------------------------------------------------------
def test_main_dispatches_ingest(tmp_path, monkeypatch):
    """`python -m ev ingest` is what the workflow runs, so that is what is
    tested -- not `cmd_ingest` alone, which would miss a broken parser."""
    monkeypatch.setattr(cli, "ladder", lambda state: [StubScraper(state=state)])
    code = cli.main([
        "--output", str(tmp_path / "output"),
        "ingest", "--cycle", str(CYCLE), "--as-of", AS_OF.isoformat(),
        "--state", "NC",
    ])
    assert code == 0
    assert (tmp_path / "output" / "ev_state_daily.csv").exists()


def test_every_subcommand_parses(tmp_path):
    """A renamed flag breaks the job as surely as a NameError does, and the
    workflow runs three of these."""
    parser = cli.build_parser()
    for argv in (
        ["ingest"],
        ["ingest", "--strict", "--dry-run"],
        ["estimate"],
        ["counterfactual"],
        ["counterfactual", "--validate"],
        ["backfill", "--cycle", "2022"],
        ["probe"],
        ["turnout"],
        ["regress"],
    ):
        parsed = parser.parse_args(argv)
        assert callable(parsed.func), argv


# --------------------------------------------------------------------------
# PROBE, which is the whole payload of reachability.yml
# --------------------------------------------------------------------------
# `probe` lives here rather than in a file of its own for the reason this module
# exists: it is a CLI seam nothing else executes, and the workflow that runs it
# parses its stdout line by line.
class ForbiddenScraper(Adapter):
    """A state behind a bot wall, which is what reachability.yml goes looking for."""

    name = "stub-403"
    tier = TIER_SCRAPER

    def fetch(self, cycle: int, as_of: date) -> FetchResult:
        raise SourceError(
            "NC: https://sos.example.gov/reports/daily.csv returned HTTP 403")


def _probe(tmp_path, monkeypatch, rungs):
    monkeypatch.setattr(cli, "ladder", lambda state: rungs(state))
    parsed = cli.build_parser().parse_args(
        ["probe", "--cycle", str(CYCLE), "--as-of", AS_OF.isoformat(),
         "--state", "NC"])
    return cli.cmd_probe(parsed)


def test_probe_prints_the_refusal_reachability_exists_to_find(
        tmp_path, monkeypatch, capsys):
    """⚠️ A 403 AT EVERY TIER USED TO PRINT AS `pending` WITH NO "403" ANYWHERE.

    reachability.yml exists for exactly one question -- can an Actions runner
    reach each state's source, given that Colorado, Ohio, Arizona and Nevada were
    all fixed against IP-SENSITIVE blocks proven from a residential address? It
    answers it by reading `probe`'s stdout. But `probe` printed only `status` and
    `message`, and the refusals sat unread on `outcome.attempts`, so the one
    thing the workflow was built to surface was the one thing it could not see.
    """
    assert _probe(tmp_path, monkeypatch, lambda state: [
        ForbiddenScraper(state=state),
        ManualAdapter(state=state, data_dir=tmp_path / "none"),
    ]) == 0

    out = capsys.readouterr().out.strip()
    assert "403" in out
    assert "stub-403" in out
    assert out.startswith("NC ")
    # ⚠️ ONE LINE PER STATE. reachability.yml greps `^[A-Z]{2} ` to build its job
    # summary, so a refusal on a continuation line is a refusal nobody sees.
    assert "\n" not in out


def test_probe_keeps_one_state_on_one_line_however_the_detail_is_shaped(
        tmp_path, monkeypatch, capsys):
    """An adapter's message is free text -- a PDF parser's complaint, a pasted
    response body -- and a newline in it would split one state's verdict in two."""
    class Chatty(Adapter):
        name, tier = "stub-chatty", TIER_SCRAPER

        def fetch(self, cycle, as_of):
            raise SourceError("line one\nline two\n" + "x" * 900)

    _probe(tmp_path, monkeypatch, lambda state: [
        Chatty(state=state), ManualAdapter(state=state, data_dir=tmp_path / "none")])
    out = capsys.readouterr().out.strip()
    assert "\n" not in out
    assert "line one line two" in out
    assert len(out) < 600, "the detail is truncated, not dumped"


def test_probe_says_nothing_extra_about_a_state_that_simply_has_not_opened(
        tmp_path, monkeypatch, capsys):
    """The September answer for most states. It must stay quiet, or the signal
    the workflow is looking for drowns."""
    _probe(tmp_path, monkeypatch, lambda state: [SilentScraper(state=state)])
    out = capsys.readouterr().out.strip()
    assert "-> pending" in out and "|" not in out


def test_probe_reports_the_tier_that_answered(tmp_path, monkeypatch, capsys):
    _probe(tmp_path, monkeypatch, lambda state: [StubScraper(state=state)])
    out = capsys.readouterr().out.strip()
    assert "-> ok via stub" in out and "|" not in out


# --------------------------------------------------------------------------
# BACKFILL runs the same seam and does NOT go through the ladder
# --------------------------------------------------------------------------
class HistoryScraper(Adapter):
    """An archive-backed state that returns town rows and does NOT self-stamp.

    That last clause is the whole test. `me.py` and `ct.py` both call
    `_towns.stamp()` inside their own `fetch_history`, so the backfill path has
    never been exercised by an adapter that relies on the caller to do it -- and
    `cmd_backfill` does not walk `ladder.run_state`, so it does not inherit the
    stamping that was added there.
    """

    name = "stub-history"
    tier = TIER_SCRAPER

    def fetch(self, cycle: int, as_of: date) -> FetchResult:
        raise NotYetPublished("this stub only has history")

    def fetch_history(self, cycle: int) -> FetchResult:
        day = date(cycle, 10, 20)
        result = FetchResult(state_rows=[StateDay(
            cycle=cycle, state=self.state, day=day, ballots_total=900,
        )])
        _towns.attach(result, [TownDay(
            cycle=cycle, state=self.state, day=day,
            town_geoid=PORTLAND_ME, town_name="Portland", ballots_total=900,
        )])
        _methods.attach(result, [MethodDay(
            cycle=cycle, state=self.state, county_fips="37119", day=day,
            method="mail", ballots_total=900,
        )])
        return result


def test_backfill_stamps_town_rows_it_did_not_fetch_itself(tmp_path, monkeypatch):
    """`ev backfill` publishes towns too, on a path the ladder never touches.

    Without the stamp this dies at WRITE time on `TownDay written without
    provenance` -- after the archive has been fetched, which for a Wayback
    backfill is minutes of downloads thrown away.
    """
    monkeypatch.setattr(cli, "ladder", lambda state: [HistoryScraper(state=state)])
    parsed = cli.build_parser().parse_args(
        ["--output", str(tmp_path / "output"),
         "backfill", "--cycle", "2024", "--state", "NC"]
    )
    assert cli.cmd_backfill(parsed) == 0

    towns = list(csv.DictReader(
        (tmp_path / "output" / "towns" / "nc.csv").open()))
    assert [r["town_geoid"] for r in towns] == [PORTLAND_ME]
    assert towns[0]["source_name"] == "stub-history"


def test_ingest_publishes_the_party_by_method_crosstab(tmp_path, monkeypatch):
    """The fifth table reaches disk, stamped, through the same seam the town
    rows use -- an ATTRIBUTE on the FetchResult, which `FetchResult.stamp` cannot
    reach, so `ladder.run_state` stamps it and `cmd_ingest` publishes it."""
    monkeypatch.setattr(cli, "ladder", lambda state: [StubScraper(state=state)])
    assert cli.cmd_ingest(args(tmp_path)) == 0
    rows = list(csv.DictReader(
        (tmp_path / "output" / "methods" / "nc.csv").open()))
    assert [(r["method"], r["ballots_total"]) for r in rows] == [
        ("inperson", "200"), ("mail", "200")]
    assert all(r["source_name"] == "stub" for r in rows)
    assert all(r["source_tier"] == "1" for r in rows)


def test_backfill_stamps_method_rows_it_did_not_fetch_itself(tmp_path, monkeypatch):
    """`ev backfill` does not walk the ladder, so it does its own stamping.
    Without it this dies at WRITE time on `MethodDay written without
    provenance`, after the archive has been fetched."""
    monkeypatch.setattr(cli, "ladder", lambda state: [HistoryScraper(state=state)])
    parsed = cli.build_parser().parse_args(
        ["--output", str(tmp_path / "output"),
         "backfill", "--cycle", "2024", "--state", "NC"]
    )
    assert cli.cmd_backfill(parsed) == 0
    rows = list(csv.DictReader(
        (tmp_path / "output" / "methods" / "nc.csv").open()))
    assert [r["method"] for r in rows] == ["mail"]
    assert rows[0]["source_name"] == "stub-history"
