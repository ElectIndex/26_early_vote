"""Michigan: the SoS absentee-by-jurisdiction workbook.

The fixture keeps four whole counties out of the real 2024-10-15 file -- three
that Michigan partially suppressed and one it did not -- plus the real TOTALS
row and the real 2020 comparison sheet, so the suppression handling and the
sheet-per-cycle selection are both exercised against verbatim data.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from ev.adapters import mi
from ev.adapters._net import Missing
from ev.adapters.base import NotYetPublished, SchemaDrift

FIXTURE = (Path(__file__).parent / "fixtures" / "mi"
           / "2024-10-15_General-Election-Data-by-Jurisdiction.xlsx")


@pytest.fixture(scope="module")
def body():
    return FIXTURE.read_bytes()


@pytest.fixture(scope="module")
def gen2024(body):
    return mi.parse(body, 2024)


# --------------------------------------------------------------------------
# THE BLANK RULE
# --------------------------------------------------------------------------
def test_no_party_field_is_ever_populated(gen2024):
    """Michigan has no party registration; a 0 would claim zero Democrats voted."""
    for row in gen2024.state_rows + gen2024.county_rows:
        for field in ("party_dem", "party_rep", "party_oth", "party_npa"):
            assert getattr(row, field) is None, (field, row)


# --------------------------------------------------------------------------
# Statewide comes from Michigan's own TOTALS row
# --------------------------------------------------------------------------
def test_statewide_is_michigans_totals_row_not_our_sum(gen2024):
    """The TOTALS row is computed from unsuppressed data, so it is exact where a
    sum of the published jurisdictions could only ever be a lower bound."""
    (state,) = gen2024.state_rows
    assert state.state == "MI"
    assert state.day == date(2024, 10, 15)
    assert state.ballots_total == 672585
    assert state.mail_returned == 672585
    assert state.mail_requested == 2112367  # ISSUED: ballots actually sent out
    # Far larger than the four counties in this fixture could ever sum to.
    assert state.ballots_total > sum(
        r.ballots_total or 0 for r in gen2024.county_rows) * 100


def test_michigan_reports_no_in_person_early_vote_column(gen2024):
    """The workbook has five columns and none of them is in-person early voting,
    so the field stays blank rather than being filled from the mail count."""
    (state,) = gen2024.state_rows
    assert state.inperson is None
    assert all(row.inperson is None for row in gen2024.county_rows)


# --------------------------------------------------------------------------
# "Less than 10": suppressed is unknown, not zero
# --------------------------------------------------------------------------
def test_county_with_a_suppressed_jurisdiction_is_blank_not_a_lower_bound(gen2024):
    """Alcona, Alger and Keweenaw each contain a jurisdiction Michigan masked as
    "Less than 10". Summing that as zero would understate Alger's issued count by
    up to 10%; blank correctly says Michigan did not tell us."""
    by_fips = {row.county_fips: row for row in gen2024.county_rows}
    for fips in ("26001", "26003", "26083"):  # Alcona, Alger, Keweenaw
        assert by_fips[fips].mail_returned is None
        assert by_fips[fips].ballots_total is None


def test_county_with_no_suppression_is_exact(gen2024):
    """Baraga's five townships are all above the floor, so it must be a number."""
    baraga = next(r for r in gen2024.county_rows if r.county_fips == "26013")
    assert baraga.county_name == "Baraga County"
    assert baraga.mail_returned == 602
    assert baraga.ballots_total == 602


def test_suppression_flag_is_recognised_not_parsed_as_a_number():
    assert mi._count("Less than 10") is mi.MASKED
    assert mi._count("less than 10") is mi.MASKED
    assert mi._count(1234) == 1234
    assert mi._count(None) is None
    with pytest.raises(SchemaDrift):
        mi._count("about a hundred")


def test_a_suppressed_component_blanks_only_the_measure_it_touches():
    assert mi._sum([1, 2, 3]) == 6
    assert mi._sum([1, mi.MASKED, 3]) is None
    assert mi._sum([None, None]) is None
    assert mi._sum([0, 0]) == 0  # a real reported zero survives


# --------------------------------------------------------------------------
# Jurisdictions roll up on the file's own COUNTY column
# --------------------------------------------------------------------------
def test_counties_are_fips_keyed_and_jurisdictions_do_not_leak(gen2024):
    """Michigan reports 1,520 cities and townships; none of them may reach the
    county table, and Novi exists as both a city and a township so a municipality
    crosswalk would have been wrong anyway."""
    assert {r.county_fips for r in gen2024.county_rows} == {
        "26001", "26003", "26013", "26083"}
    for row in gen2024.county_rows:
        assert len(row.county_fips) == 5 and row.county_fips.startswith("26")


def test_prior_cycle_sheet_is_selected_by_year(body):
    """The workbook carries a 2020 comparison sheet beside the live one; asking
    for a cycle must never silently return the other sheet's numbers."""
    old = mi.parse(body, 2020)
    assert {r.day for r in old.county_rows} == {date(2020, 10, 12)}
    alcona = next(r for r in old.county_rows if r.county_fips == "26001")
    assert alcona.mail_returned == 1340  # no suppression at all in the 2020 sheet


def test_sheet_title_supplies_year_and_as_of_date():
    assert mi.sheet_date("2024 (Oct 15)") == (2024, date(2024, 10, 15))
    assert mi.sheet_date("2020 (Oct 12)") == (2020, date(2020, 10, 12))
    with pytest.raises(SchemaDrift):
        mi.sheet_date("Absentee Data")


# --------------------------------------------------------------------------
# Drift and absence
# --------------------------------------------------------------------------
def test_renamed_column_raises_drift(tmp_path):
    import openpyxl

    book = openpyxl.load_workbook(FIXTURE)
    book["2024 (Oct 15)"].cell(row=1, column=5).value = "RETURNED"
    path = tmp_path / "drifted.xlsx"
    book.save(path)
    with pytest.raises(SchemaDrift, match="unexpected header"):
        mi.parse(path.read_bytes(), 2024)


def test_unknown_county_name_raises_drift(tmp_path):
    import openpyxl

    book = openpyxl.load_workbook(FIXTURE)
    book["2024 (Oct 15)"].cell(row=2, column=1).value = "ATLANTIS"
    path = tmp_path / "unknown.xlsx"
    book.save(path)
    with pytest.raises(SchemaDrift, match="unrecognised county"):
        mi.parse(path.read_bytes(), 2024)


def test_workbook_without_the_current_cycle_is_not_yet_published(body):
    """Before Michigan adds the 2026 sheet the file is still up, still valid, and
    still has nothing for us -- that is 'pending', not a failure."""
    with pytest.raises(NotYetPublished, match="no 2026 sheet"):
        mi.parse(body, 2026)


def test_missing_workbook_is_not_yet_published(monkeypatch):
    def missing(url, **kwargs):
        raise Missing(f"MI: {url} returned 404")

    monkeypatch.setattr(mi, "get", missing)
    with pytest.raises(NotYetPublished):
        mi.MIScraper().fetch(2026, date(2026, 9, 5))


def test_soft_404_html_page_is_not_yet_published(monkeypatch):
    monkeypatch.setattr(mi, "get", lambda url, **kw: b"<!DOCTYPE html><html>nope</html>")
    with pytest.raises(NotYetPublished):
        mi.MIScraper().fetch(2026, date(2026, 9, 5))


def test_future_dated_workbook_is_not_published(monkeypatch, body):
    monkeypatch.setattr(mi, "get", lambda url, **kw: body)
    with pytest.raises(NotYetPublished, match="after"):
        mi.MIScraper().fetch(2024, date(2024, 10, 1))


def test_adapter_identity():
    scraper = mi.MIScraper()
    assert (scraper.state, scraper.name, scraper.tier) == ("MI", "mi-sos", 1)
