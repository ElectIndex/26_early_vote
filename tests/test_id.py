"""Idaho: the SoS absentee & early-voting tracker over Datawrapper charts.

Fixtures, all downloaded live 2026-09-08:

* `tracker_2026_primary.html` -- the stable `.gov` index, truncated to the only
  lines this module reads, all of them verbatim: the heading and the six chart
  references.
* `tracker_2026_general.html` -- the same file with `Primary` changed to
  `General`. **Synthetic, and labelled so in the fixture itself.** No general
  tracker has been published in this cycle, and a gate that nothing can ever
  pass is not a tested gate.
* `chart_jshNw_stub.html` -- the 241-byte stub `/{id}/` serves, naming v51.
* `chart_jshNw_51.csv` -- the county turnout dataset, verbatim, 44 counties.
* `chart_hGfEl_39.csv` -- a SIBLING chart from the same page (legislative
  district by party). It is here so the header-based pick is tested against a
  real neighbour rather than an invented one.

The first two tests are `coverage-research.md`'s objection and the version trap,
both enforced.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from ev.adapters import id as idaho
from ev.adapters.base import NotYetPublished, SchemaDrift
from ev.schema import TIER_SCRAPER

FIXTURES = Path(__file__).parent / "fixtures" / "id"


def fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


@pytest.fixture
def county_rows() -> list[dict[str, str]]:
    import csv, io
    return list(csv.DictReader(io.StringIO(
        fixture("chart_jshNw_51.csv").decode("utf-8-sig")
    )))


# --------------------------------------------------------------- the gate

def test_a_tracker_still_showing_the_primary_is_refused():
    """THE WHOLE REASON THIS ADAPTER IS SAFE.

    On 2026-09-08 -- four months after the May primary -- the live page still
    read "2026 Primary Election" and the datasets behind it still carried
    `last-modified: Wed, 20 May 2026`. A run that trusted the page would have
    stamped May's primary absentee counts with September's date under the 2026
    general, which is the exact bug `mt.py` was built to avoid and `az.py` was
    fixed for.
    """
    with pytest.raises(NotYetPublished) as exc:
        idaho.election(fixture("tracker_2026_primary.html"), 2026)
    assert "2026 Primary" in str(exc.value)
    assert "2026 General" in str(exc.value)


def test_the_general_passes_the_same_gate():
    # Synthetic fixture -- see the module docstring. Returns None, raises nothing.
    assert idaho.election(fixture("tracker_2026_general.html"), 2026) is None


def test_last_cycles_general_is_refused_too():
    # The gate is on the YEAR as well as the kind. A 2024 general tracker left
    # up in 2026 is the same trap wearing the right word.
    stale = fixture("tracker_2026_general.html").replace(b"2026 General", b"2024 General")
    with pytest.raises(NotYetPublished):
        idaho.election(stale, 2026)


def test_an_unreadable_heading_is_DRIFT_not_absence():
    # SchemaDrift falls THROUGH to the aggregator; NotYetPublished stops the
    # ladder. A page that answered and that we could not parse is a fact about
    # our parser, so it must fall through rather than silence the state.
    with pytest.raises(SchemaDrift):
        idaho.election(b"<html><h2>Absentee Ballot Stats</h2></html>", 2026)


# ------------------------------------------------------- the version trap

def test_the_version_comes_from_the_stub_and_is_never_pinned():
    """`/{id}/{version}/dataset.csv` with a stale version NEVER errors.

    coverage-research.md measured it: /HCDQQ/1/ served Ada 1,353, /HCDQQ/17/
    served Ada 10,644, /HCDQQ/23/ served Ada 18,868. All three returned 200.
    So the only safe read is to resolve the stub every run, and the stub is the
    single source of the number.
    """
    stub = fixture("chart_jshNw_stub.html")
    assert b"jshNw/51" in stub
    assert len(stub) < 512, "the stub is a redirect, not a page"
    # And nothing in the module hardcodes a version anywhere.
    source = (Path(idaho.__file__)).read_text()
    body = source.split('"""', 2)[-1]  # skip the docstring, which quotes the trap
    assert "/51/" not in body and "/23/" not in body


def test_charts_are_discovered_from_the_page_not_hardcoded():
    ids = idaho.chart_ids(fixture("tracker_2026_primary.html"))
    assert ids == ["aCECg", "PJZGl", "UDG7E", "hGfEl", "jshNw", "HCDQQ"]
    # Page order, deduped, and NOT sorted -- the survey's objection was that the
    # ids are cycle-specific, so the list must come out of the page every time.
    assert len(set(ids)) == len(ids)


def test_a_page_with_no_charts_is_drift():
    with pytest.raises(SchemaDrift):
        idaho.chart_ids(b"<html><body>nothing embedded</body></html>")


# ------------------------------------------------------------- the parse

def test_the_county_dataset_is_picked_by_its_HEADER(county_rows):
    import csv, io
    sibling = list(csv.DictReader(io.StringIO(
        fixture("chart_hGfEl_39.csv").decode("utf-8-sig")
    )))
    assert idaho._header(county_rows) == idaho.TURNOUT_HEADER
    # A real neighbour from the same page, which must not be mistaken for it.
    assert idaho._header(sibling) != idaho.TURNOUT_HEADER
    assert idaho._header(sibling)[0] == "legislativedistrict"


def test_every_idaho_county_is_published_and_keyed_by_fips(county_rows):
    result = idaho.parse(county_rows, date(2026, 5, 20), 2026)
    assert len(result.county_rows) == idaho.EXPECTED_COUNTIES == 44
    assert all(len(r.county_fips) == 5 and r.county_fips.startswith("16")
               for r in result.county_rows)
    ada = next(r for r in result.county_rows if r.county_fips == "16001")
    assert ada.county_name == "Ada County"
    assert (ada.ballots_total, ada.mail_returned, ada.inperson) == (29661, 16750, 12911)


def test_no_party_column_is_ever_written(county_rows):
    """Idaho registers by party and this source does not publish registration.

    The one party column it does publish is a PRIMARY BALLOT CHOICE
    (`AbsPartySelected` on the sibling chart says so in its own name). Writing
    it as party_dem/party_rep would be the ok.py and az.py mistake again, and
    the blank rule says the answer is None -- never 0.
    """
    result = idaho.parse(county_rows, date(2026, 5, 20), 2026)
    for row in result.county_rows + result.state_rows:
        assert row.party_dem is None
        assert row.party_rep is None
        assert row.party_oth is None
        assert row.party_npa is None


def test_the_statewide_row_is_the_counties_summed(county_rows):
    result = idaho.parse(county_rows, date(2026, 5, 20), 2026)
    assert len(result.state_rows) == 1
    state = result.state_rows[0]
    assert state.ballots_total == sum(r.ballots_total or 0 for r in result.county_rows)
    assert state.mail_returned == sum(r.mail_returned or 0 for r in result.county_rows)
    assert state.inperson == sum(r.inperson or 0 for r in result.county_rows)
    # Verified against the live fixture on 2026-09-08.
    assert (state.ballots_total, state.mail_returned, state.inperson) == (87204, 46381, 40823)
    assert state.mail_requested == 56084
    assert (state.mail_returned or 0) + (state.inperson or 0) == state.ballots_total


def test_a_partial_county_set_publishes_NO_statewide_row(county_rows):
    """A total over 43 of 44 counties looks exactly like an Idaho turnout figure.

    Same rule as mt.py and tx.py. The counties still publish; the state row does
    not, because there is no way to tell a short total from a real one on the page.
    """
    result = idaho.parse(county_rows[:-1], date(2026, 5, 20), 2026)
    assert len(result.county_rows) == 43
    assert result.state_rows == []


def test_the_sources_own_identity_is_checked(county_rows):
    """`total_voted` must equal `Returned + early_voting`, and it does today.

    If that stops holding, total_voted has started counting something else and
    every ballots_total here is wrong -- worth a loud failure, not a plausible
    number.
    """
    broken = [dict(r) for r in county_rows]
    broken[0]["total_voted"] = str(int(broken[0]["total_voted"]) + 1)
    with pytest.raises(SchemaDrift) as exc:
        idaho.parse(broken, date(2026, 5, 20), 2026)
    assert "total_voted" in str(exc.value)


def test_an_unknown_county_is_drift_not_a_dropped_row(county_rows):
    broken = [dict(r) for r in county_rows]
    broken[0]["ResCountyDesc"] = "Nonesuch"
    with pytest.raises(SchemaDrift):
        idaho.parse(broken, date(2026, 5, 20), 2026)


def test_a_non_numeric_count_is_drift(county_rows):
    broken = [dict(r) for r in county_rows]
    broken[0]["Returned"] = "n/a"
    with pytest.raises(SchemaDrift):
        idaho.parse(broken, date(2026, 5, 20), 2026)


# ------------------------------------------------------------- the adapter

def test_adapter_identity():
    scraper = idaho.IDScraper()
    assert scraper.state == "ID"
    assert scraper.name == "id-sos"
    assert scraper.tier == TIER_SCRAPER


def test_history_is_refused_by_name_for_every_cycle():
    """GUARD PARITY, and the reason it is a refusal.

    Datawrapper keeps prior versions addressable, so a backfill is tempting --
    but a version is an ORDINAL, not a timestamp, and Idaho publishes no
    machine-readable date anywhere. Walking versions would produce undated
    snapshots that `backfill` then stamps with the election date, which is
    exactly what `nd.py` and `az.py` were both fixed for.
    """
    scraper = idaho.IDScraper()
    for cycle in (2020, 2022, 2024, 2026):
        with pytest.raises(NotYetPublished) as exc:
            scraper.fetch_history(cycle)
        assert str(cycle) in str(exc.value)
