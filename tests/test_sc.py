"""South Carolina: the SEC's absentee and early-voting workbooks.

Four fixtures, all truncated from the real files with every header row and every
retained value verbatim:

* `2024-09-24-Absentee-Statistics-by-Race-for-GE.xlsx` -- six weeks out, before
  in-person early voting opened, so absentee IS the early vote and the race
  breakdown describes all of it. Six of its nine worksheets are kept, including
  both "Multiple" and "Other" -- which share one canonical bucket.
* `2024-10-29-Absentee-Statistics-by-Race-for-GE.xlsx` -- deep in the in-person
  window, when the same race breakdown would describe 7% of early voters.
* `2024-GE-Early-Voting-Statistics-4.xlsx` -- the in-person file, one column per
  day, whose statewide row is South Carolina's published 1,106,201.
* `2026-08-24-Absentee-Statistics-by-Race-for-Primary.xlsx` -- the August 2026
  US Senate special primary runoff, in the NEW report layout. It is the trap:
  same report, same server, different election, and it must never be published.
* `2024-09-30-Absentee-Statistics-by-Race-for-GE.xlsx` -- kept whole, because the
  whole of it is four rows: the SEC posted an empty shell that day, banner and
  all, reading "Election Date: 1/1/0001".

Expected numbers below are read straight off the fixtures' own cells.
"""

from __future__ import annotations

import io
import json
from datetime import date
from pathlib import Path

import openpyxl
import pytest

from ev.adapters import sc
from ev.adapters._net import Missing
from ev.adapters.base import NotYetPublished, SchemaDrift, SourceError

FIXTURES = Path(__file__).parent / "fixtures" / "sc"
EARLY_ABS = FIXTURES / "2024-09-24-Absentee-Statistics-by-Race-for-GE.xlsx"
LATE_ABS = FIXTURES / "2024-10-29-Absentee-Statistics-by-Race-for-GE.xlsx"
EV = FIXTURES / "2024-GE-Early-Voting-Statistics-4.xlsx"
PRIMARY = FIXTURES / "2026-08-24-Absentee-Statistics-by-Race-for-Primary.xlsx"
EMPTY = FIXTURES / "2024-09-30-Absentee-Statistics-by-Race-for-GE.xlsx"

SEP24 = date(2024, 9, 24)
OCT29 = date(2024, 10, 29)


def _mutated(path: Path, sheet: str, row: int, column: int, value):
    """One cell of a real fixture changed, to prove drift fails loudly."""
    book = openpyxl.load_workbook(path)
    book[sheet].cell(row=row, column=column).value = value
    buf = io.BytesIO()
    book.save(buf)
    return buf.getvalue()


# `combine` folds the in-person file into the absentee snapshot IN PLACE, so
# these are function-scoped: a module-scoped one would be mutated by the first
# test that combines it and every later assertion would be about that.
@pytest.fixture
def before_ev():
    return sc.parse_absentee(EARLY_ABS.read_bytes(), 2024, SEP24, with_race=True)


@pytest.fixture
def during_ev():
    return sc.parse_absentee(LATE_ABS.read_bytes(), 2024, OCT29, with_race=False)


@pytest.fixture
def inperson():
    return sc.parse_early(EV.read_bytes(), 2024)


# --------------------------------------------------------------------------
# THE BLANK RULE: South Carolina has no party registration
# --------------------------------------------------------------------------
def test_party_is_blank_everywhere_because_sc_has_no_party_registration(
        before_ev, during_ev, inperson):
    """A 0 here would render as "no Democrat has voted in South Carolina"."""
    combined = sc.combine(during_ev, inperson, 2024, OCT29)
    for result in (before_ev, combined):
        rows = result.state_rows + result.county_rows
        assert rows
        for row in rows:
            assert row.party_dem is None, row
            assert row.party_rep is None, row
            assert row.party_npa is None, row
            assert row.party_oth is None, row


def test_a_reported_zero_stays_zero(before_ev):
    """On 2024-09-24 most counties had issued no ballots at all and South
    Carolina wrote 0. That is a claim, not a blank, and must survive as one."""
    by_fips = {r.county_fips: r for r in before_ev.county_rows}
    assert by_fips["45001"].mail_returned == 0          # Abbeville
    assert by_fips["45019"].mail_returned == 379        # Charleston
    (state,) = before_ev.state_rows
    assert state.mail_requested == 8731
    assert state.mail_returned == 534


# --------------------------------------------------------------------------
# The statewide row is South Carolina's own
# --------------------------------------------------------------------------
def test_statewide_row_is_south_carolinas_own_not_our_sum(during_ev):
    """The fixture keeps seven counties out of 46, so a parser that summed them
    would report 34,863 rather than South Carolina's 79,144."""
    (state,) = during_ev.state_rows
    assert state.state == "SC" and state.day == OCT29
    assert state.mail_requested == 116941
    assert state.mail_returned == 79144
    assert sum(r.mail_returned for r in during_ev.county_rows) == 34863


def test_total_row_is_not_emitted_as_a_county(during_ev):
    assert "Total" not in {r.county_name for r in during_ev.county_rows}
    assert all(r.county_fips != "45000" for r in during_ev.county_rows)


# --------------------------------------------------------------------------
# County rows are keyed by FIPS, never by the file's own "01-ABBEVILLE" code
# --------------------------------------------------------------------------
def test_county_rows_are_fips_keyed(during_ev):
    by_fips = {r.county_fips: r for r in during_ev.county_rows}
    assert by_fips["45019"].county_name == "Charleston County"
    assert by_fips["45045"].county_name == "Greenville County"
    assert by_fips["45091"].county_name == "York County"
    assert by_fips["45001"].mail_returned == 216        # Abbeville, ballots returned
    assert by_fips["45001"].ballots_total == 216


def test_the_files_own_county_number_is_not_a_fips(during_ev):
    """"01-ABBEVILLE" is South Carolina's alphabetical county number, not a FIPS
    code -- Abbeville is 45001 and Aiken is 45003, not 45001 and 45002."""
    fips = sorted(r.county_fips for r in during_ev.county_rows)
    assert fips == ["45001", "45003", "45013", "45019", "45045", "45079", "45091"]
    for row in during_ev.county_rows:
        assert len(row.county_fips) == 5 and row.county_fips.startswith("45")


# --------------------------------------------------------------------------
# Race: published only while absentee IS the early vote
# --------------------------------------------------------------------------
def test_race_is_published_before_in_person_voting_opens(before_ev):
    buckets = {d.bucket: d.ballots_total for d in before_ev.demo_rows}
    assert buckets == {"black": 25, "white": 438, "other": 5 + 9, "unknown": 30}
    assert all(d.dimension == "race" and d.day == SEP24 for d in before_ev.demo_rows)


def test_multiple_and_other_are_added_rather_than_colliding(before_ev):
    """`normalize.RACE_BUCKETS` has no multiracial bucket, so South Carolina's
    "Multiple" (5) and "Other" (9) worksheets are both `other`. Dropping one
    would lose real ballots; raising on the collision would leave the state
    permanently broken."""
    (other,) = [d for d in before_ev.demo_rows if d.bucket == "other"]
    assert other.ballots_total == 14
    assert len({d.bucket for d in before_ev.demo_rows}) == len(before_ev.demo_rows)


def test_race_is_withheld_once_in_person_voting_opens(during_ev):
    """The race table covers absentee ballots only. Once in-person voting is
    running those 79,144 ballots are 7% of South Carolina's early vote, and the
    breakdown would render as if it described all of it."""
    assert during_ev.demo_rows == []


def test_race_labels_map_onto_canonical_buckets():
    """"Black/AA" and "Multiple" are real worksheet labels that normalize.race()
    does not carry; anything in neither still raises."""
    assert sc.race_bucket("Black/AA") == "black"
    assert sc.race_bucket("Multiple") == "other"
    assert sc.race_bucket("White") == "white"
    assert sc.race_bucket("Native American") == "native"
    with pytest.raises(SchemaDrift, match="unrecognised race"):
        sc.race_bucket("Klingon")


def test_the_statutory_early_voting_window():
    assert sc.early_window(2022) == (date(2022, 10, 24), date(2022, 11, 5))
    assert sc.early_window(2024) == (date(2024, 10, 21), date(2024, 11, 2))
    assert sc.early_window(2026) == (date(2026, 10, 19), date(2026, 10, 31))


# --------------------------------------------------------------------------
# The in-person file: one column per day, whole curve in one download
# --------------------------------------------------------------------------
def test_early_voting_file_carries_the_whole_curve(inperson):
    assert inperson.report_day == date(2024, 10, 31)
    assert inperson.days == [date(2024, 10, d) for d in
                             (21, 22, 23, 24, 25, 26, 28, 29, 30)]
    assert date(2024, 10, 27) not in inperson.days, "a Sunday, with no voting"
    assert sum(inperson.statewide.values()) == 1106201, "South Carolina's own figure"


def test_early_voting_counties_are_fips_keyed(inperson):
    assert inperson.names["45019"] == "Charleston County"
    assert sum(inperson.counties["45019"].values()) == 104859
    assert sum(inperson.counties["45001"].values()) == 4736
    assert all(len(f) == 5 and f.startswith("45") for f in inperson.counties)


def test_polling_place_rows_are_not_counted_as_jurisdictions(inperson):
    """Charleston's seven polling places each carry the same measure as the
    county row above them; counting them would double the county."""
    assert "45019" in inperson.counties
    assert len(inperson.counties) == 5, "five counties kept in the fixture"


# --------------------------------------------------------------------------
# Folding the two files together
# --------------------------------------------------------------------------
def test_the_anchor_day_carries_mail_plus_in_person():
    mail = sc.parse_absentee(LATE_ABS.read_bytes(), 2024, OCT29, with_race=False)
    result = sc.combine(mail, sc.parse_early(EV.read_bytes(), 2024), 2024, OCT29)
    anchor = [r for r in result.state_rows if r.day == OCT29]
    assert len(anchor) == 1
    (row,) = anchor
    assert row.inperson == 990013            # 10/21..10/29 of the statewide row
    assert row.mail_returned == 79144
    assert row.ballots_total == 990013 + 79144


def test_days_before_the_anchor_report_in_person_only_and_a_blank_total():
    """What mail had come back on 10/23 is not in either file, so the total for
    that day is blank -- never in-person alone, which would omit the mail."""
    mail = sc.parse_absentee(LATE_ABS.read_bytes(), 2024, OCT29, with_race=False)
    result = sc.combine(mail, sc.parse_early(EV.read_bytes(), 2024), 2024, OCT29)
    early = {r.day: r for r in result.state_rows if r.day != OCT29}
    assert sorted(early) == [date(2024, 10, d) for d in (21, 22, 23, 24, 25, 26, 28)]
    day3 = early[date(2024, 10, 23)]
    assert day3.inperson == 126732 + 127837 + 128995
    assert day3.ballots_total is None
    assert day3.mail_returned is None


def test_a_stale_in_person_file_is_not_folded_in():
    """Folding a report from a week ago into today's mail snapshot would publish
    a total that mixes two different days -- and once in-person voting is running,
    mail alone is not the total either, so the total is blank."""
    mail = sc.parse_absentee(LATE_ABS.read_bytes(), 2024, OCT29, with_race=False)
    result = sc.combine(mail, sc.parse_early(EV.read_bytes(), 2024),
                        2024, date(2024, 11, 5))
    (state,) = result.state_rows
    assert state.inperson is None
    assert state.ballots_total is None
    assert state.mail_returned == 79144, "the absentee detail survives"


def test_mail_alone_is_never_the_total_once_in_person_voting_is_open():
    mail = sc.parse_absentee(LATE_ABS.read_bytes(), 2024, OCT29, with_race=False)
    result = sc.combine(mail, None, 2024, OCT29)
    for row in result.state_rows + result.county_rows:
        assert row.ballots_total is None, row
    assert result.state_rows[0].mail_returned == 79144


def test_absentee_only_days_total_the_mail(before_ev):
    (state,) = before_ev.state_rows
    assert state.inperson is None
    assert state.ballots_total == state.mail_returned == 534


# --------------------------------------------------------------------------
# The election guard: same report, same server, a different election
# --------------------------------------------------------------------------
def test_a_primary_workbook_is_refused():
    body = PRIMARY.read_bytes()
    for cycle in (2024, 2026):
        with pytest.raises(NotYetPublished, match="is a primary"):
            sc.parse_absentee(body, cycle, date(2026, 8, 24), with_race=True)


def test_the_secs_empty_shell_is_an_absence_not_a_schema_change():
    """South Carolina really posted this on 2024-09-30: the report banner, an
    election date of 1/1/0001, and no table. A file with no data in it is data
    that does not exist yet, so the run moves on to the next candidate."""
    with pytest.raises(NotYetPublished, match="empty shell"):
        sc.parse_absentee(EMPTY.read_bytes(), 2024, date(2024, 9, 30), with_race=True)


def test_a_full_workbook_that_lost_its_county_header_is_drift():
    """The empty-shell path must not swallow a real renaming: a 50-row report
    whose key column moved is a schema change and has to fail loudly."""
    book = openpyxl.load_workbook(LATE_ABS)
    for sheet in book.worksheets:
        for row in sheet.iter_rows():
            for cell in row:
                if str(cell.value or "").strip().lower() == "county":
                    cell.value = "Jurisdiction"
    buf = io.BytesIO()
    book.save(buf)
    with pytest.raises(SchemaDrift, match="no worksheet with a 'County' header"):
        sc.parse_absentee(buf.getvalue(), 2024, OCT29, with_race=False)


def test_an_empty_shell_does_not_stop_a_run_that_has_a_real_file(monkeypatch):
    listing = _media([
        ("2024-09-30", "2024-09-30-Absentee-Statistics-by-Race-for-GE.xlsx"),
        ("2024-09-24", "2024-09-24-Absentee-Statistics-by-Race-for-GE.xlsx"),
    ])
    bodies = {"2024-09-30-Absentee-Statistics-by-Race-for-GE.xlsx": EMPTY.read_bytes(),
              "2024-09-24-Absentee-Statistics-by-Race-for-GE.xlsx": EARLY_ABS.read_bytes()}

    def fake_get(url, **kwargs):
        return listing if "wp-json" in url else bodies[url.rsplit("/", 1)[-1]]

    monkeypatch.setattr(sc, "get", fake_get)
    result = sc.SCScraper().fetch(2024, date(2024, 9, 30))
    (state,) = result.state_rows
    assert state.day == SEP24 and state.mail_returned == 534


def test_a_general_from_another_cycle_is_refused():
    with pytest.raises(NotYetPublished, match="names no 2026-11-03 election"):
        sc.parse_absentee(LATE_ABS.read_bytes(), 2026, OCT29, with_race=False)


def test_an_early_voting_file_for_another_election_is_refused():
    with pytest.raises(NotYetPublished, match="is for 2024-11-05"):
        sc.parse_early(EV.read_bytes(), 2026)


def test_a_party_filtered_worksheet_is_never_read_as_the_state():
    """The 2026 report adds a `Political Party:` filter, and its Republican-only
    sheets are not a statewide universe."""
    book = openpyxl.load_workbook(PRIMARY)
    rows = list(book["Sheet1"].iter_rows(values_only=True))
    header_at, _, _ = sc._sheet_layout(rows)
    assert sc._labelled(rows, header_at, sc._PARTY_LINE) == "ALL"
    rows2 = list(book["Sheet2"].iter_rows(values_only=True))
    header2, _, _ = sc._sheet_layout(rows2)
    assert sc._labelled(rows2, header2, sc._PARTY_LINE) == "Republican"


# --------------------------------------------------------------------------
# SchemaDrift rather than a guessed mapping
# --------------------------------------------------------------------------
def test_a_renamed_ballot_column_raises_drift():
    body = _mutated(LATE_ABS, "Sheet1", 11, 12, "Sent")   # "Issued" of the Ballots block
    with pytest.raises(SchemaDrift, match="absentee ballot columns"):
        sc.parse_absentee(body, 2024, OCT29, with_race=False)


def test_a_missing_ballots_banner_raises_drift():
    body = _mutated(LATE_ABS, "Sheet1", 10, 9, "Envelopes")
    with pytest.raises(SchemaDrift, match="no 'ballots' block"):
        sc.parse_absentee(body, 2024, OCT29, with_race=False)


def test_an_unknown_county_name_raises_drift():
    body = _mutated(LATE_ABS, "Sheet1", 12, 2, "01-ATLANTIS")
    with pytest.raises(SchemaDrift, match="unrecognised county"):
        sc.parse_absentee(body, 2024, OCT29, with_race=False)


def test_a_renamed_early_voting_header_raises_drift():
    body = _mutated(EV, "2024-GE-Early-Voting-Statistics", 4, 1, "Locality")
    with pytest.raises(SchemaDrift, match="no 'Jurisdiction' header row"):
        sc.parse_early(body, 2024)


def test_a_missed_day_column_raises_drift():
    """If a date column stops parsing as a date, every county in the state is
    understated -- so the file's own Total Voters column has to disagree loudly."""
    body = _mutated(EV, "2024-GE-Early-Voting-Statistics", 4, 6, "Day 3")
    with pytest.raises(SchemaDrift, match="neither a date"):
        sc.parse_early(body, 2024)


def test_a_day_total_that_does_not_add_up_raises_drift():
    body = _mutated(EV, "2024-GE-Early-Voting-Statistics", 6, 13, 999)
    with pytest.raises(SchemaDrift, match="early-voting days sum to"):
        sc.parse_early(body, 2024)


# --------------------------------------------------------------------------
# Discovery and the NotYetPublished path, which runs every day for weeks
# --------------------------------------------------------------------------
def _media(items):
    return json.dumps([{"date": f"{d}T09:00:00", "source_url":
                        f"https://scvotes.gov/wp-content/uploads/x/{f}"}
                       for d, f in items]).encode()


def test_an_empty_media_index_is_not_yet_published(monkeypatch):
    monkeypatch.setattr(sc, "get", lambda *a, **kw: b"[]")
    with pytest.raises(NotYetPublished, match="lists no 2026 workbooks"):
        sc.SCScraper().fetch(2026, date(2026, 9, 6))


def test_a_media_index_holding_only_primaries_is_not_yet_published(monkeypatch):
    """This is today's real state of the world: the newest absentee workbook on
    scvotes.gov is the August US Senate special primary runoff."""
    listing = _media([("2026-08-24", "2026-08-24-Absentee-Statistics-by-Race-for-Primary.xlsx")])
    body = PRIMARY.read_bytes()

    def fake_get(url, **kwargs):
        return listing if "wp-json" in url else body

    monkeypatch.setattr(sc, "get", fake_get)
    with pytest.raises(NotYetPublished, match="no 2026 general-election absentee"):
        sc.SCScraper().fetch(2026, date(2026, 9, 6))


def test_a_missing_file_is_not_yet_published(monkeypatch):
    def fake_get(url, **kwargs):
        if "wp-json" in url:
            return _media([("2026-10-20", "2026-10-20-Absentee-Statistics-by-Race-for-GE.xlsx")])
        raise Missing(f"SC: {url} returned 404")

    monkeypatch.setattr(sc, "get", fake_get)
    with pytest.raises(NotYetPublished, match="no 2026 general-election absentee"):
        sc.SCScraper().fetch(2026, date(2026, 10, 20))


def test_the_general_is_tried_before_a_primary_posted_the_same_day():
    candidates = [
        {"day": date(2024, 10, 29), "file": "2024-10-29-Absentee-Statistics-by-Race-for-Primary.xlsx"},
        {"day": date(2024, 10, 29), "file": "2024-10-29-Absentee-Statistics-by-Race-for-GE.xlsx"},
        {"day": date(2024, 10, 29), "file": "2024-GE-Early-Voting-Statistics-2.xlsx"},
    ]
    ranked = sc.SCScraper._rank(candidates, sc._ABSENTEE_FILE)
    assert [c["file"] for c in ranked] == [
        "2024-10-29-Absentee-Statistics-by-Race-for-GE.xlsx",
        "2024-10-29-Absentee-Statistics-by-Race-for-Primary.xlsx",
    ], "early-voting files are a different report and are not candidates here"


def test_a_missing_in_person_file_inside_the_window_falls_through(monkeypatch):
    """In-person is the great majority of South Carolina's early vote, so an
    absentee-only answer during the window would understate the headline by an
    order of magnitude. That is a SourceError -- fall through a tier -- not a
    NotYetPublished, which would stop the ladder on a wrong number."""
    listing = _media([("2024-10-29", "2024-10-29-Absentee-Statistics-by-Race-for-GE.xlsx")])

    def fake_get(url, **kwargs):
        if "wp-json" in url:
            return listing
        return LATE_ABS.read_bytes()

    monkeypatch.setattr(sc, "get", fake_get)
    with pytest.raises(SourceError, match="no early-voting workbook"):
        sc.SCScraper().fetch(2024, OCT29)


def test_a_live_run_reads_both_files(monkeypatch):
    listing = _media([
        ("2024-10-29", "2024-10-29-Absentee-Statistics-by-Race-for-GE.xlsx"),
        ("2024-10-31", "2024-GE-Early-Voting-Statistics-4.xlsx"),
    ])
    bodies = {
        "2024-10-29-Absentee-Statistics-by-Race-for-GE.xlsx": LATE_ABS.read_bytes(),
        "2024-GE-Early-Voting-Statistics-4.xlsx": EV.read_bytes(),
    }

    def fake_get(url, **kwargs):
        if "wp-json" in url:
            return listing
        return bodies[url.rsplit("/", 1)[-1]]

    monkeypatch.setattr(sc, "get", fake_get)
    result = sc.SCScraper().fetch(2024, date(2024, 10, 31))
    anchor = [r for r in result.state_rows if r.day == OCT29]
    assert anchor and anchor[0].ballots_total == 990013 + 79144
    assert {r.state for r in result.county_rows} == {"SC"}


def test_history_of_the_current_cycle_is_not_an_archive():
    with pytest.raises(NotYetPublished, match="not an archived cycle"):
        sc.SCScraper().fetch_history(2026)


def test_adapter_identity():
    scraper = sc.SCScraper()
    assert (scraper.state, scraper.name, scraper.tier) == ("SC", "sc-sec", 1)
