"""Hawaii: the Office of Elections' statewide Absentee Reconciliation report.

Two real, unmodified fixtures downloaded 2026-09-06, both HTTP 200:

* `AbsenteeReconState-20260717.pdf` -- before ballots went out. Every "Voted"
  column is a literal 0, which is what makes it the test that a reported zero
  publishes as 0 while an unreported field stays None.
* `AbsenteeReconState-20260808.pdf` -- primary Election Day, all four counties
  fully populated.

Both are the 2026 PRIMARY, because no general-election report exists yet -- and
that is itself the most important thing this suite pins: the filename pattern is
shared between the primary and the general, and the only thing that tells them
apart is the line the report prints about itself.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import re

import pytest

from ev.adapters import hi
from ev.adapters.base import FetchResult, NotYetPublished, SchemaDrift, SourceError
from ev.schema import Provenance, TIER_SCRAPER, county_row_to_dict, state_row_to_dict

FIXTURES = Path(__file__).parent / "fixtures" / "hi"
BEFORE = FIXTURES / "AbsenteeReconState-20260717.pdf"
ELECTION_DAY = FIXTURES / "AbsenteeReconState-20260808.pdf"


@pytest.fixture(scope="module")
def before() -> FetchResult:
    return hi.build(hi._text(BEFORE.read_bytes()), 2026)


@pytest.fixture(scope="module")
def voted() -> FetchResult:
    return hi.build(hi._text(ELECTION_DAY.read_bytes()), 2026)


def _counties(result: FetchResult) -> dict[str, object]:
    return {row.county_fips: row for row in result.county_rows}


# --------------------------------------------------------------------------
# The report says which election it is, and that is the whole gate
# --------------------------------------------------------------------------
def test_a_primary_report_stops_the_ladder_instead_of_being_published():
    """A primary's file is not an error and not a general-election number.

    NotYetPublished, not SourceError: there is no better source for a general
    election that has not started, and falling through would let a weaker tier
    invent a zero.
    """
    for fixture in (BEFORE, ELECTION_DAY):
        with pytest.raises(NotYetPublished) as caught:
            hi.parse(fixture.read_bytes(), 2026)
        assert "primary" in str(caught.value)


def test_the_election_line_is_read_off_the_report_itself():
    assert hi.election(hi._text(ELECTION_DAY.read_bytes())) == (2026, "primary")


def test_a_report_with_no_election_line_raises_drift():
    with pytest.raises(SchemaDrift):
        hi.election("Absentee Reconcillation\n8/8/2026 3:11:26 AM\n")


def test_a_report_stamped_a_different_day_than_its_filename_raises_drift():
    """A re-post of yesterday's numbers under today's name would flatten a day
    of the curve, so it fails loudly rather than being published twice."""
    text = hi._text(ELECTION_DAY.read_bytes())
    with pytest.raises(SchemaDrift):
        hi.build(text, 2026, filename_date=date(2026, 8, 9))
    # The matching date is accepted.
    assert hi.build(text, 2026, filename_date=date(2026, 8, 8)).state_rows


# --------------------------------------------------------------------------
# The numbers Hawaii published
# --------------------------------------------------------------------------
def test_statewide_row_matches_the_reports_own_totals_line(voted):
    row = voted.state_rows[0]
    assert row.day == date(2026, 8, 8)
    assert row.ballots_total == 234_639     # VOTED (b + d + f)
    assert row.mail_requested == 732_780    # MAIL Sent (e)
    assert row.mail_returned == 230_276     # MAIL Voted (f)
    assert row.inperson == 4_037            # EV Voted (d)


def test_all_four_elections_jurisdictions_are_present(voted):
    counties = _counties(voted)
    assert set(counties) == {"15001", "15003", "15007", "15009"}
    assert counties["15003"].county_name == "Honolulu County"
    assert counties["15003"].ballots_total == 157_126
    assert counties["15003"].mail_returned == 154_602
    assert counties["15003"].inperson == 2_303


def test_kalawao_never_appears_and_four_counties_is_full_coverage(voted):
    """Kalawao County (15005) has no elections division; Hawaii's four county
    clerks are the whole state, so four rows earn a statewide row."""
    assert "15005" not in _counties(voted)
    assert len(voted.state_rows) == 1


def test_the_okina_in_hawaii_and_kauai_still_resolves_to_a_fips_code():
    assert hi._county("Hawai'i") == ("15001", "Hawaii County")
    assert hi._county("Kauaʻi") == ("15007", "Kauai County")
    assert hi._county("City and County of Honolulu") == ("15003", "Honolulu County")


def test_the_totals_row_is_checked_against_the_sum_of_the_counties(voted):
    for field in ("ballots_total", "mail_returned", "inperson"):
        assert getattr(voted.state_rows[0], field) == sum(
            getattr(row, field) for row in voted.county_rows
        )


def test_ballots_total_exceeds_mail_plus_inperson_because_of_email_returns(voted):
    """Hawaii's VOTED column also counts UOCAVA ballots returned by email or
    fax, which are neither a mail ballot nor an in-person early vote."""
    row = voted.state_rows[0]
    assert row.ballots_total > row.mail_returned + row.inperson
    assert row.ballots_total - row.mail_returned - row.inperson == 326


# --------------------------------------------------------------------------
# THE BLANK RULE
# --------------------------------------------------------------------------
def test_hawaii_has_no_party_registration_so_no_party_field_is_ever_set(before, voted):
    for result in (before, voted):
        for row in result.state_rows + result.county_rows:
            for field in ("party_dem", "party_rep", "party_oth", "party_npa"):
                assert getattr(row, field) is None, (field, row)


def test_party_fields_write_as_blank_cells_not_zeros(voted):
    prov = Provenance(tier=TIER_SCRAPER, name="hi-oe")
    state = state_row_to_dict(
        FetchResult(state_rows=[voted.state_rows[0]]).stamp(prov).state_rows[0]
    )
    county = county_row_to_dict(
        FetchResult(county_rows=[voted.county_rows[0]]).stamp(prov).county_rows[0]
    )
    for cells in (state, county):
        for field in ("party_dem", "party_rep", "party_oth", "party_npa"):
            assert cells[field] == ""


def test_a_reported_zero_publishes_as_zero(before):
    """On 2026-07-17 nothing had been returned. Hawaii printed 0, so we do."""
    row = before.state_rows[0]
    assert row.day == date(2026, 7, 17)
    assert row.mail_returned == 0
    assert row.inperson == 0
    assert row.mail_requested == 726_068   # ballots were already in the post
    assert row.ballots_total == 31          # 31 email/fax returns, and they are real


# --------------------------------------------------------------------------
# Drift and the ladder contract
# --------------------------------------------------------------------------
def test_a_missing_column_raises_drift():
    text = hi._text(ELECTION_DAY.read_bytes()).replace("MAIL Voted", "MAIL Cast")
    with pytest.raises(SchemaDrift) as caught:
        hi.build(text, 2026)
    assert "MAIL Voted (f)" in str(caught.value)


def test_the_arithmetic_catches_a_value_that_moved_across_the_sums():
    """VOTED must equal b+d+f and TOTAL must equal a+d+e on every row.

    That catches a value that crossed BETWEEN the two sums -- but not one that
    moved within either, which is the next test.
    """
    with pytest.raises(SchemaDrift) as caught:
        hi._check_arithmetic("15003", [1844, 221, 0, 2303, 480780, 154602, 1010, 1, 2])
    assert "VOTED" in str(caught.value)


# --------------------------------------------------------------------------
# ⚠️ THE ARITHMETIC IS COMMUTATIVE, SO IT IS NOT A COLUMN-ORDER CHECK
#
# `rows()` hands `build` nine numbers BY POSITION. The two things said to prove
# they landed in the right nine fields were `_check_header` (which tested
# presence only) and `_check_arithmetic` (which adds). Swap Hawaii's printed
# ELECT block (a, b, c) with its MAIL block (e, f, g) -- three adjacent columns
# -- and `VOTED = b+d+f` becomes `f+d+b` and `TOTAL = a+d+e` becomes `e+d+a`.
# Both still hold. Hawaii would have published `mail_requested` of 2,555 for a
# day it sent 732,780 ballots, with `ballots_total` correct and unremarkable.
# --------------------------------------------------------------------------
def _swap_elect_and_mail(nine: list[int]) -> list[int]:
    a, b, c, d, e, f, g, voted, total = nine
    return [e, f, g, d, a, b, c, voted, total]


ELECTION_DAY_TOTALS = [2555, 326, 0, 4037, 732780, 230276, 1929, 234639, 739372]


def test_the_arithmetic_alone_does_not_notice_the_elect_and_mail_blocks_swapping():
    hi._check_arithmetic("statewide", ELECTION_DAY_TOTALS)          # the truth
    hi._check_arithmetic("statewide", _swap_elect_and_mail(ELECTION_DAY_TOTALS))
    # ...and the second call is the bug: it passes, and those nine values would
    # have been published as Hawaii's mail figures.
    swapped = _swap_elect_and_mail(ELECTION_DAY_TOTALS)
    assert (swapped[4], swapped[5]) == (2555, 326)
    assert (ELECTION_DAY_TOTALS[4], ELECTION_DAY_TOTALS[5]) == (732780, 230276)


def _swap_headings(text: str, first: str, second: str) -> str:
    """Swap two printed column headings, tolerating pypdf's line wrapping.

    A real reordering moves the heading WITH its column, which is the only thing
    a parser can see -- the numbers underneath carry no labels at all.
    """
    def find(tag):
        return re.search(r"\s+".join(map(re.escape, tag.split())), text)

    left, right = find(first), find(second)
    assert left and right and left.start() < right.start(), (first, second)
    return (text[:left.start()] + right.group(0) + text[left.end():right.start()]
            + left.group(0) + text[right.end():])


def test_the_header_must_be_in_the_order_the_values_are_read_in():
    text = hi._text(ELECTION_DAY.read_bytes())
    hi._check_header(text)                       # as published: in order
    reordered = _swap_headings(text, "ELECT Sent (a)", "MAIL Sent (e)")
    assert reordered != text
    # Every tag is still present -- which is all the old check ever asked.
    flat = " ".join(reordered.split())
    assert all(tag in flat for tag in hi.COLUMN_TAGS)
    with pytest.raises(SchemaDrift) as caught:
        hi._check_header(reordered)
    assert "not in the order" in str(caught.value)


# --------------------------------------------------------------------------
# ⚠️ A COUNTY THAT DID NOT PARSE IS DRIFT, NOT PARTIAL COVERAGE
#
# `rows()` skips any line it cannot resolve to a county -- that is how it walks
# past the headings and the footer -- so a RENAMED county is skipped by the same
# `except SchemaDrift: continue` and the run used to succeed with three of the
# four. Hawaii's four clerks are the whole state; three is never "Maui has not
# reported yet".
# --------------------------------------------------------------------------
def test_a_county_hawaii_renames_is_drift_not_three_quarters_of_a_state():
    text = hi._text(ELECTION_DAY.read_bytes())
    renamed = text.replace("Maui", "Maui Nui")
    assert renamed != text
    with pytest.raises(SchemaDrift) as caught:
        hi.build(renamed, 2026)
    message = str(caught.value)
    assert "3 of 4" in message
    assert "15009" not in message      # Maui is the one that went missing


def test_the_four_counties_that_do_parse_are_still_the_whole_state(voted):
    assert len({row.county_fips for row in voted.county_rows}) == 4
    assert voted.state_rows, "four counties is full coverage, so a statewide row"


def test_something_that_is_not_a_pdf_is_a_source_error():
    with pytest.raises(SourceError):
        hi.parse(b"<html>not posted yet</html>", 2026)


# --------------------------------------------------------------------------
# The index page, and the measured negative about past cycles
# --------------------------------------------------------------------------
#: A real excerpt of https://elections.hawaii.gov/resources/absentee-voting-report/
#: fetched 2026-09-09 (HTTP 200, 189,456 B): the page head verbatim plus the
#: "County Voting Report by D/P" block for the six most recent report days, every
#: anchor as the Office wrote it. The other ~230 links are elided, and the
#: comment in the file says so.
INDEX = FIXTURES / "absentee-voting-report-index-excerpt.html"


def test_the_index_page_parses_into_report_dates(monkeypatch):
    monkeypatch.setattr(hi, "get", lambda *args, **kwargs: INDEX.read_bytes())
    assert hi.HIScraper()._index_dates() == [
        date(2026, 6, 22), date(2026, 8, 11), date(2026, 8, 12),
        date(2026, 8, 13), date(2026, 8, 14), date(2026, 8, 15),
    ]


def test_the_index_lists_nothing_from_a_past_cycle():
    """⚠️ THIS IS THE EVIDENCE UNDER `fetch_history`'s REFUSAL, NOT DECORATION.

    The Office keeps one election's reports. The day it starts keeping an
    archive, this fails and the refusal below can be replaced by a real
    backfill -- which is the only way a documented negative stays honest.
    """
    stamps = hi._INDEX_LINK.findall(INDEX.read_text(encoding="utf-8"))
    assert stamps, "the index excerpt lists no reports at all"
    assert {stamp[:4] for stamp in stamps} == {"2026"}


def test_no_archive_means_history_is_refused_by_name():
    """Not the base class's shrug: the sweep behind it is in the docstring."""
    for cycle in (2022, 2024):
        with pytest.raises(NotYetPublished) as caught:
            hi.HIScraper().fetch_history(cycle)
        assert str(cycle) in str(caught.value)


def test_the_adapter_declares_itself_correctly():
    scraper = hi.HIScraper()
    assert (scraper.state, scraper.name, scraper.tier) == ("HI", "hi-oe", TIER_SCRAPER)


def test_a_day_with_no_report_anywhere_stops_the_ladder(monkeypatch):
    from ev.adapters._net import Missing

    scraper = hi.HIScraper()
    monkeypatch.setattr(scraper, "_index_dates", list)
    monkeypatch.setattr(
        scraper, "_report",
        lambda day: (_ for _ in ()).throw(Missing(f"HI: {day} returned 404")),
    )
    with pytest.raises(NotYetPublished):
        scraper.fetch(2026, date(2026, 9, 6))
