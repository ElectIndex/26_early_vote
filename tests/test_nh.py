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


# --------------------------------------------------------------------------
# The wall, and why it is no longer the reason this adapter is empty
# --------------------------------------------------------------------------
#: The REAL body sos.nh.gov returned to `requests` + DEFAULT_HEADERS on
#: 2026-09-08: HTTP 403, 413 bytes of Akamai's "Access Denied" template. Shared
#: with test_net.py because it is the generic Akamai shape as well as New
#: Hampshire's.
NH_WALL = (Path(__file__).parent / "fixtures" / "net"
           / "akamai_access_denied.html").read_bytes()


def test_new_hampshires_wall_is_now_something_the_transport_can_see():
    """⚠️ The old docstring said sos.nh.gov "answers automated requests with HTTP
    403 regardless of User-Agent", which is the sentence that stops the next
    person looking. The User-Agent was never the variable -- Akamai is scoring
    the TLS fingerprint, and `_net.get`'s 403 -> impersonate retry walks through
    it. Measured 2026-09-08: `requests` -> 403 / 413 b of this; curl_cffi
    `impersonate="chrome"` -> 200 and 2,960,857 bytes of the real page.
    """
    from ev.adapters import _net

    assert len(NH_WALL) == 413
    assert b"Access Denied" in NH_WALL
    assert b"sos" in NH_WALL and b"nh" in NH_WALL
    # It is a wall, not a short file: SourceError and fall through, never Missing.
    assert _net.looks_like_wall(NH_WALL) is True


def test_the_emptiness_is_a_finding_about_nh_not_about_our_reach():
    """The docstring has to say, in words, that the page was re-read from BEHIND
    the wall and still carried no counts. If someone deletes that, the adapter
    goes back to being indistinguishable from a state we simply could not fetch.
    """
    doc = nh.__doc__
    assert "2026-09-08" in doc
    assert "reachable" in doc.lower()
    assert "2,960,857" in doc  # the size of the page that was actually read
    sources = " ".join(str(s) for s in [nh.SOURCES])
    assert "absentee-ballots" in sources


def test_the_ladder_policy_call_is_recorded_with_its_cost():
    """NotYetPublished still STOPS the ladder here, and the reason is no longer
    "a daily warning in the log": ladder.py records NotYetPublished from the
    FLOOR as STATUS_FAILED, and data/manual/ is empty, so flipping this to
    SourceError would badge New Hampshire `failed` on every run for ever."""
    doc = nh.__doc__
    assert "STATUS_FAILED" in doc
    assert "manual" in doc
