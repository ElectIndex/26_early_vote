"""Montana: the SoS absentee-ballot Tableau dashboard, CSV plus PDF.

Two real fixtures, both downloaded live 2026-09-06 from the same Tableau view:

* `AbsenteeDash_2026-06-15.csv` -- byte-for-byte the 5,518-byte CSV export:
  `County,Measure Names,Measure Values` over 56 counties plus Montana's own
  `All` row.
* `AbsenteeDash_2026-06-15.pdf` -- the PDF export of the same view, with its
  images stripped and its content streams recompressed so it is a reasonable
  size; every character of text is the server's. It is here for two lines:
  the title `2026 Montana Primary Election Absentee Ballot Counts` and the
  footer `Compiled On 6/15/2026 6:55:19 AM`.

Those two lines are the entire reason this adapter exists. `coverage-research.md`
rejected Montana because "the CSV carries no election identifier and no as-of
date, and the dashboard holds the LAST election's numbers between cycles". The
first test below is that rejection, enforced.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from ev.adapters import mt
from ev.adapters.base import FetchResult, NotYetPublished, SchemaDrift, SourceError
from ev.schema import Provenance, TIER_SCRAPER, county_row_to_dict, state_row_to_dict

FIXTURES = Path(__file__).parent / "fixtures" / "mt"
CSV = FIXTURES / "AbsenteeDash_2026-06-15.csv"
PDF = FIXTURES / "AbsenteeDash_2026-06-15.pdf"

COMPILED_ON = date(2026, 6, 15)


@pytest.fixture(scope="module")
def parsed() -> FetchResult:
    return mt.parse(CSV.read_bytes(), COMPILED_ON, 2026)


# --------------------------------------------------------------------------
# The thing that made this source unusable, now enforced
# --------------------------------------------------------------------------
def test_a_primary_dashboard_is_never_published_as_the_general():
    """Between cycles the dashboard keeps serving the last election. Without
    this gate a September run would stamp June's primary absentee totals with
    today's date under the 2026 general."""
    with pytest.raises(NotYetPublished) as caught:
        mt.provenance(PDF.read_bytes(), 2026)
    assert "primary" in str(caught.value)


def test_a_dashboard_from_another_cycle_is_refused_too():
    with pytest.raises(NotYetPublished):
        mt.provenance(PDF.read_bytes(), 2024)


def test_a_readable_pdf_with_no_title_raises_drift():
    """A blank render -- Tableau serving an empty view -- must not be read as
    "no election named, carry on"."""
    import io

    import pypdf

    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=612, height=792)
    blank = io.BytesIO()
    writer.write(blank)
    with pytest.raises(SchemaDrift) as caught:
        mt.provenance(blank.getvalue(), 2026)
    assert "which election" in str(caught.value)


def test_bytes_that_are_not_a_pdf_are_a_source_error():
    with pytest.raises(SourceError):
        mt.provenance(b"<html>Tableau is down</html>", 2026)


def test_the_compiled_on_stamp_is_what_dates_every_row(parsed):
    assert {row.day for row in parsed.county_rows} == {COMPILED_ON}
    assert parsed.state_rows[0].day == COMPILED_ON


# --------------------------------------------------------------------------
# The numbers Montana published
# --------------------------------------------------------------------------
def test_all_56_counties_are_present(parsed):
    assert len({row.county_fips for row in parsed.county_rows}) == 56
    assert mt.EXPECTED_COUNTIES == 56


def test_statewide_row_matches_montanas_own_all_row(parsed):
    row = parsed.state_rows[0]
    assert row.mail_requested == 514_152
    assert row.mail_returned == 268_125
    assert row.ballots_total == 268_125


def test_the_all_row_is_checked_against_the_sum_of_the_counties(parsed):
    assert parsed.state_rows[0].ballots_total == sum(
        row.ballots_total for row in parsed.county_rows
    )


def test_lewis_and_clark_resolves_despite_the_ampersand():
    """Tableau writes "Lewis & Clark"; the census says "Lewis and Clark County",
    and `_fips` does not expand an ampersand on its own."""
    assert mt._county("Lewis & Clark") == ("30049", "Lewis and Clark County")


def test_a_county_name_we_do_not_know_raises_rather_than_being_dropped():
    with pytest.raises(SchemaDrift):
        mt._county("Deadwood")


# --------------------------------------------------------------------------
# THE BLANK RULE
# --------------------------------------------------------------------------
def test_montana_has_no_party_registration_so_no_party_field_is_ever_set(parsed):
    for row in parsed.state_rows + parsed.county_rows:
        for field in ("party_dem", "party_rep", "party_oth", "party_npa"):
            assert getattr(row, field) is None, (field, row)


def test_inperson_is_none_not_zero(parsed):
    """Montana reports absentee ballots only. A 0 here would say nobody has
    voted early in person, which the dashboard does not claim."""
    for row in parsed.state_rows + parsed.county_rows:
        assert row.inperson is None


def test_ballots_new_is_none_because_the_dashboard_has_no_day_dimension(parsed):
    for row in parsed.state_rows + parsed.county_rows:
        assert row.ballots_new is None


def test_none_writes_as_a_blank_cell_not_a_zero(parsed):
    prov = Provenance(tier=TIER_SCRAPER, name="mt-sos")
    state = state_row_to_dict(
        FetchResult(state_rows=[parsed.state_rows[0]]).stamp(prov).state_rows[0]
    )
    county = county_row_to_dict(
        FetchResult(county_rows=[parsed.county_rows[0]]).stamp(prov).county_rows[0]
    )
    for cells in (state, county):
        for field in ("party_dem", "party_rep", "party_oth", "party_npa", "inperson"):
            assert cells[field] == ""


# --------------------------------------------------------------------------
# Drift
# --------------------------------------------------------------------------
def test_a_changed_csv_header_raises_drift():
    body = CSV.read_bytes().replace(b"Measure Names", b"Measure", 1)
    with pytest.raises(SchemaDrift):
        mt.parse(body, COMPILED_ON, 2026)


def test_an_unknown_measure_raises_drift():
    body = CSV.read_bytes().replace(b"Ballots Sent", b"Ballots Mailed")
    with pytest.raises(SchemaDrift) as caught:
        mt.parse(body, COMPILED_ON, 2026)
    assert "Ballots Mailed" in str(caught.value)


def test_a_short_download_publishes_counties_and_no_statewide_row(caplog):
    """If Tableau drops a county from the view, the All row would still look
    like a Montana total and would not be one. Same rule as tx.py."""
    lines = CSV.read_bytes().decode().splitlines(keepends=True)
    trimmed = [ln for ln in lines if not ln.startswith("Yellowstone,")]
    result = mt.parse("".join(trimmed).encode(), COMPILED_ON, 2026)
    assert len(result.county_rows) == 55
    assert result.state_rows == []


def test_an_all_row_that_disagrees_with_its_counties_raises_drift():
    body = CSV.read_bytes().replace(b'All,Ballots Received,"268,125"',
                                    b'All,Ballots Received,"268,126"')
    with pytest.raises(SchemaDrift) as caught:
        mt.parse(body, COMPILED_ON, 2026)
    assert "268,126".replace(",", "") in str(caught.value)


def test_the_adapter_declares_itself_correctly():
    scraper = mt.MTScraper()
    assert (scraper.state, scraper.name, scraper.tier) == ("MT", "mt-sos", TIER_SCRAPER)
