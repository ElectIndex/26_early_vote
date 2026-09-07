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
from datetime import date
from pathlib import Path

import pytest

from ev import cli
from ev.adapters import _towns
from ev.adapters.base import Adapter, FetchResult, NotYetPublished
from ev.schema import CountyDay, DemoDay, StateDay, TownDay, TIER_SCRAPER

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


def test_the_status_file_says_when_each_state_was_last_CHECKED(tmp_path, stub_ladder):
    """⚠️ This is the site's only freshness source, and nothing wrote it until
    2026-09-07.

    ev-core.js has always read `s.status.retrievedAt || s.latest.retrieved_at`
    -- written that way on purpose -- but the status file carried no such field,
    so every stale badge was computed from the DATA ROW's timestamp. That was
    survivable only while every run rewrote every row. Once an unchanged row
    started keeping its original stamp, a state reporting the same number for two
    days would have badged STALE at 36 hours while we were checking it every two.

    The two facts now live in different places: the row says when the NUMBER last
    changed, this says when we last LOOKED.
    """
    stub_ladder(StubScraper)
    cli.cmd_ingest(args(tmp_path))
    status = json.loads((tmp_path / "output" / "ev_status.json").read_text())
    assert status["states"]["NC"]["retrieved_at"] == status["generated_at"]


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
