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


# --------------------------------------------------------------------------
# ⚠️ THE SPACING REGRESSION THAT BROKE MONTANA IN PRODUCTION
#
# Tableau positions this PDF one glyph at a time, so the spaces in the extracted
# text belong to pypdf's heuristic, not to the file. pyproject pins only
# `pypdf>=5.0`; the three strings below are the FIRST LINE of this very fixture
# as three real pypdf versions extracted it on 2026-09-08. The `\s+` patterns
# this module shipped with matched the first and neither of the others, and
# GitHub Actions had resolved 6.18.0 -- so `mt-sos` raised SchemaDrift on every
# published run from 2026-09-06 and fell through to the aggregator.
# --------------------------------------------------------------------------
EXTRACTIONS = {
    "6.16.1": (
        "2026 Montana Primary Election Absentee Ballot Counts",
        "Compiled On 6/15/2026 6:55:19 AM",
    ),
    "6.18.0": (
        "2026 M o n tan a P rim ary E lectio n  A b sen tee B allo t C o u n ts",
        "C om piled O n 6/15/2026 6:55:19 A M",
    ),
    "5.1.0": (
        "2 0 2 6  M o n ta n a  P rim a ry  E le c tio n  A b s e n te e  "
        "B a llo t C o u n ts",
        "C om piled O n 6/15/2026 6:55:19 A M",
    ),
}


@pytest.mark.parametrize("version", sorted(EXTRACTIONS))
def test_the_title_reads_the_same_however_pypdf_spaces_it(version):
    title, _stamp = EXTRACTIONS[version]
    match = mt.TITLE.search(mt._compact(title))
    assert match is not None, (version, title)
    assert (match.group(1), match.group(2).lower()) == ("2026", "primary")


@pytest.mark.parametrize("version", sorted(EXTRACTIONS))
def test_the_compiled_on_stamp_reads_the_same_however_pypdf_spaces_it(version):
    _title, stamp = EXTRACTIONS[version]
    match = mt.COMPILED.search(mt._compact(stamp))
    assert match is not None, (version, stamp)
    assert tuple(int(g) for g in match.groups()) == (6, 15, 2026)


def test_provenance_survives_a_glyph_spaced_extract(monkeypatch):
    """The whole of `provenance` against 6.18.0's text, not just the regex.

    Reproduces the production failure end to end: before the fix this raised
    SchemaDrift, which falls through to a weaker tier instead of stopping the
    ladder on a dashboard that is plainly showing the primary.
    """
    title, stamp = EXTRACTIONS["6.18.0"]
    monkeypatch.setattr(
        mt.pypdf, "PdfReader",
        lambda *_a, **_k: type("R", (), {"pages": [
            type("P", (), {"extract_text": lambda self: f"{title}\nsosmt.gov\n{stamp}"})()
        ]})(),
    )
    with pytest.raises(NotYetPublished) as caught:
        mt.provenance(b"%PDF-1.4 anything", 2026)
    assert "primary" in str(caught.value)


def test_a_general_dashboard_would_be_accepted_however_it_is_spaced():
    """The general's own wording is unverified -- no general dashboard has ever
    been published -- so the one thing that can be locked is that the SAME
    spacing damage does not stop `General` from being recognised."""
    spaced = "2 0 2 6  M o n ta n a  G e n e ra l  E le c tio n  A b s e n te e"
    match = mt.TITLE.search(mt._compact(spaced))
    assert match is not None
    assert match.group(2).lower() == "general"


def test_an_unreadable_title_still_says_what_the_page_did_say():
    """A drift message that only says "no title" cannot be triaged from a cron
    log. It now quotes the extract."""
    import io

    import pypdf

    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=612, height=792)
    blank = io.BytesIO()
    writer.write(blank)
    with pytest.raises(SchemaDrift) as caught:
        mt.provenance(blank.getvalue(), 2026)
    assert "the extract begins" in str(caught.value)


# --------------------------------------------------------------------------
# The 2024 cycle: what the archive actually holds, and the refusal it forces
# --------------------------------------------------------------------------
#: The ONE county-level Montana absentee workbook the Internet Archive holds
#: from the 2024 cycle, byte-for-byte as served: 11,524 B from
#: `https://web.archive.org/web/20240617062207id_/`
#: `https://sosmt.gov/docs/23/elections/63973/absentee-counts-by-county`
#: (HTTP 200, `application/octet-stream`). It has exactly two captures ever --
#: 2024-06-17 and 2025-05-16 -- and both carry the same digest, so this file is
#: the whole of the 2024 archive.
LEGACY_2024 = FIXTURES / "Absentee-Counts-By-County_2024-05-16.xlsx"


def _legacy_rows():
    import openpyxl

    sheet = openpyxl.load_workbook(LEGACY_2024).worksheets[0]
    rows = list(sheet.iter_rows(values_only=True))
    numeric = [row for row in rows if isinstance(row[1], int)]
    counties = [row for row in numeric if str(row[0] or "").strip()]
    (totals,) = [row for row in numeric if not str(row[0] or "").strip()]
    return rows, counties, totals


def test_the_only_archived_2024_workbook_names_no_election():
    """⚠️ THIS IS WHY `fetch_history` REFUSES RATHER THAN READING AN ARCHIVE.

    The pre-Tableau format carries a compile date and nothing else. Montana's
    dashboard holds the LAST election's numbers between cycles, so a file that
    does not say which election it is cannot be dated Election Day on the
    strength of when it happened to be captured -- which is the exact rejection
    `coverage-research.md` made of the CSV, in a second file.
    """
    rows, _counties, _totals = _legacy_rows()
    assert rows[0][0] == "Montana Absentee Ballot Counts by County"
    assert rows[1][0] == "Compiled 5/16/2024 12:00:00 AM"
    assert rows[2][0] == "Updated Daily"
    assert rows[3] == ("County Name", "Number Sent", "Number Receive")
    # 5/16/2024 is the run-up to Montana's June 4 PRIMARY, not the general, and
    # nothing in the file says so -- only the date does.
    flat = " ".join(str(cell) for row in rows for cell in row if cell)
    assert "General" not in flat and "Primary" not in flat


def test_every_county_the_secretary_spells_still_resolves():
    """The Secretary's own spellings, not Tableau's.

    This file writes `LEWIS AND CLARK`; the dashboard writes `Lewis & Clark`,
    which is the one name COUNTY_ALIASES exists for. Both must resolve, or a
    rename would land counts under the wrong FIPS.
    """
    _rows, counties, totals = _legacy_rows()
    assert len(counties) == mt.EXPECTED_COUNTIES == 56
    resolved = {mt._county(name)[0] for name, _sent, _received in counties}
    assert len(resolved) == 56
    assert mt._county("LEWIS AND CLARK") == mt._county("Lewis & Clark")
    # The workbook's own unnamed totals row, which is the same coverage gate
    # `parse` applies to the dashboard's `All` row.
    assert totals[1] == sum(row[1] for row in counties) == 450_167
    assert totals[2] == sum(row[2] for row in counties) == 10_015


def test_history_is_refused_by_name_for_every_past_cycle():
    for cycle in (2022, 2024):
        with pytest.raises(NotYetPublished) as caught:
            mt.MTScraper().fetch_history(cycle)
        detail = str(caught.value)
        assert str(cycle) in detail
        # The refusal has to say WHY, or the next reader repeats the sweep.
        assert "Tableau" in detail and "placeholder" in detail
