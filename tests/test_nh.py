"""New Hampshire: the adapter that correctly publishes nothing.

New Hampshire has no early voting and no statewide absentee file. The fixture is
the only absentee count the Secretary of State has ever published -- a weekly
prose notice posted under the 2020 COVID emergency and discontinued afterwards --
kept verbatim (via the Internet Archive; the live URL is gone) so that the reason
this adapter is empty is evidence in the repo rather than a claim in a docstring.

These tests are the guard rail: they fail if someone later gives NH a source it
does not have, and they fail if someone quietly turns "no data" into a zero.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import pytest

from ev.adapters import nh
from ev.adapters.base import NotYetPublished

FIXTURE = (Path(__file__).parent / "fixtures" / "nh"
           / "2020-11-03_absentee-ballots-requested-notice.html")


@pytest.fixture(scope="module")
def notice():
    return FIXTURE.read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# There is no source, and saying so is the whole job
# --------------------------------------------------------------------------
@pytest.mark.parametrize("cycle", [2022, 2024, 2026])
def test_every_cycle_is_not_yet_published(cycle):
    with pytest.raises(NotYetPublished, match="no early voting"):
        nh.NHScraper().fetch(cycle, date(2026, 9, 5))


@pytest.mark.parametrize("cycle", [2022, 2024])
def test_history_is_not_yet_published_either(cycle):
    with pytest.raises(NotYetPublished):
        nh.NHScraper().fetch_history(cycle)


def test_no_rows_are_ever_returned():
    """Not "an empty FetchResult" -- an exception. An empty result would let the
    run record New Hampshire as answered with nothing, which reads on the page
    as zero ballots cast."""
    for call in (lambda: nh.NHScraper().fetch(2026, date(2026, 10, 20)),
                 lambda: nh.NHScraper().fetch_history(2022)):
        with pytest.raises(NotYetPublished):
            call()


def test_the_adapter_touches_no_network(monkeypatch):
    """There is no URL to invent, so there must be no request to make. If a
    future edit adds one, this fails before it reaches a state election site."""
    import ev.adapters._net as net

    def explode(*args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("NH adapter must not make a request")

    monkeypatch.setattr(net, "get", explode)
    with pytest.raises(NotYetPublished):
        nh.NHScraper().fetch(2026, date(2026, 9, 5))


def test_the_reason_names_what_is_missing():
    """The message ends up in ev_status.json as the state's pending reason, so
    it has to explain itself to someone reading the site's stale badge."""
    with pytest.raises(NotYetPublished) as caught:
        nh.NHScraper().fetch(2026, date(2026, 9, 5))
    message = str(caught.value)
    assert "no early voting" in message
    assert "2020" in message


# --------------------------------------------------------------------------
# What New Hampshire did once publish, and why it would not have been enough
# --------------------------------------------------------------------------
def test_the_only_counts_nh_ever_published_were_statewide_prose(notice):
    """Six lines of English, one a week, for one emergency cycle."""
    assert "148,630 absentee ballots have been requested statewide" in notice
    assert "updated every Tuesday until the election" in notice
    weeks = re.findall(r"([A-Z][a-z]+ \d{1,2}, 2020) - ([\d,]+)", notice)
    assert len(weeks) == 6
    assert weeks[0][0] == "September 29, 2020"
    assert weeks[-1] == ("November 3, 2020", "249,658")


def test_that_notice_carried_no_county_and_no_party(notice):
    """Even if it came back it could only ever fill a statewide ballots_total
    and mail_requested once a week -- no county rows, no party split, no method
    split. That is the ceiling on any NH tier-1, and it is below the aggregator
    and manual tiers, which at least carry a daily statewide number."""
    body = notice.lower()
    for county in ("belknap", "carroll", "cheshire", "coos", "grafton",
                   "hillsborough", "merrimack", "rockingham", "strafford",
                   "sullivan"):
        assert county not in body
    for party in ("democrat", "republican", "undeclared", "unaffiliated"):
        assert party not in body


def test_sources_are_documentation_not_endpoints():
    """Every URL here was checked and found to carry no data feed. None of them
    is fetched -- a constant that looked like a live endpoint would be the first
    step towards inventing one."""
    assert nh.SOURCES
    assert all(url.startswith("https://") for url in nh.SOURCES)
    assert not hasattr(nh, "get")  # _net.get is deliberately not imported


def test_adapter_identity():
    scraper = nh.NHScraper()
    assert (scraper.state, scraper.name, scraper.tier) == ("NH", "nh-sos", 1)
