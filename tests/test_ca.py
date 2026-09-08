"""California: the SoS's VBM statistics workbook.

Two fixtures, both real:

* `vbm-statistics_2022-general_2022-10-27.xlsx` -- the DURING-SEASON snapshot,
  taken twelve days before the 2022 general. Its "VBM Press Version" sheet is
  copied cell for cell out of the 452 KB eight-sheet workbook the SoS served
  that day (Wayback capture `20221027003436`); only the seven sheets this
  adapter never reads are dropped, to keep the fixture small. This is the file
  that proves California publishes returns while the window is open.
* `vbm-statistics_2022-general_final.xlsm` -- the same URL as served live today,
  byte for byte, 77,507 bytes. It is the FINAL restatement and carries only the
  one sheet, which is why the parser finds its sheet by name.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from ev.adapters import ca
from ev.adapters.base import NotYetPublished, SchemaDrift, SourceError

FIX = Path(__file__).parent / "fixtures" / "ca"
SEASON = (FIX / "vbm-statistics_2022-general_2022-10-27.xlsx").read_bytes()
FINAL = (FIX / "vbm-statistics_2022-general_final.xlsm").read_bytes()

DAY = date(2022, 10, 27)


# --------------------------------------------------------------------------
# The during-season file, which the coverage survey said did not exist
# --------------------------------------------------------------------------
def test_the_during_season_snapshot_is_all_58_counties():
    result = ca.parse(SEASON, 2022, DAY)
    assert len(result.county_rows) == 58
    assert len({r.county_fips for r in result.county_rows}) == 58
    assert all(r.county_fips.startswith("06") for r in result.county_rows)


def test_the_statewide_row_is_californias_own_total():
    (row,) = ca.parse(SEASON, 2022, DAY).state_rows
    assert row.day == DAY
    assert row.ballots_total == 1_642_945
    assert row.mail_requested == 22_156_707


def test_the_counties_add_up_to_californias_own_total():
    result = ca.parse(SEASON, 2022, DAY)
    assert sum(r.ballots_total for r in result.county_rows) == 1_642_945


def test_los_angeles_is_keyed_by_fips_and_named_from_the_census():
    result = ca.parse(SEASON, 2022, DAY)
    la = next(r for r in result.county_rows if r.county_fips == "06037")
    assert la.county_name == "Los Angeles County"
    assert la.ballots_total == 310_707


def test_the_final_restatement_parses_from_its_single_sheet():
    """The live file has only the 'VBM Press Version' sheet; the during-season
    one has eight. The sheet is found by name for exactly that reason."""
    result = ca.parse(FINAL, 2022, date(2022, 11, 8))
    assert len(result.county_rows) == 58
    assert result.state_rows[0].ballots_total == 5_230_699


def test_returns_grow_between_the_two_snapshots():
    """This is what makes the source a tracker rather than a restatement."""
    early = ca.parse(SEASON, 2022, DAY).state_rows[0].ballots_total
    final = ca.parse(FINAL, 2022, date(2022, 11, 8)).state_rows[0].ballots_total
    assert early < final


# --------------------------------------------------------------------------
# What the counts mean
# --------------------------------------------------------------------------
def test_ballots_total_is_returns_not_the_accepted_subset():
    """Accepted lags returns by the county's signature-review queue, and a
    headline that moves when a county finishes checking is not turnout."""
    result = ca.parse(SEASON, 2022, DAY)
    la = next(r for r in result.county_rows if r.county_fips == "06037")
    # The sheet's own accepted figure for LA that day is lower than its Sum.
    assert la.ballots_total == 310_707
    assert ca.RETURNED == "sum" and ca.ACCEPTED == "total accepted vbm ballots"


def test_every_return_channel_is_mail_and_in_person_stays_blank():
    """Drop box, drop-off, vote-centre drop-off, post, fax and other are all a
    vote-by-mail ballot coming home. Ballots cast in person at a vote centre are
    not in this workbook at all, so that field is blank rather than 0."""
    for row in ca.parse(SEASON, 2022, DAY).county_rows:
        assert row.mail_returned == row.ballots_total
        assert row.inperson is None


def test_california_registers_by_party_but_this_workbook_does_not_split_it():
    result = ca.parse(SEASON, 2022, DAY)
    for row in result.state_rows + result.county_rows:
        assert (row.party_dem, row.party_rep, row.party_npa, row.party_oth) == (
            None, None, None, None
        )


# --------------------------------------------------------------------------
# The sheet has to prove it is the sheet we think it is
# --------------------------------------------------------------------------
def test_a_workbook_without_the_press_sheet_is_drift():
    import io
    import openpyxl

    book = openpyxl.Workbook()
    book.active.title = "Something Else"
    buf = io.BytesIO()
    book.save(buf)
    with pytest.raises(SchemaDrift, match="has no 'VBM Press Version' sheet"):
        ca.parse(buf.getvalue(), 2022, DAY)


def test_a_missing_column_is_drift():
    import io
    import openpyxl

    book = openpyxl.load_workbook(io.BytesIO(SEASON))
    sheet = book[ca.SHEET]
    for cell in sheet[4]:
        if str(cell.value or "").strip().lower() == ca.RETURNED:
            cell.value = "Total Returned"
    buf = io.BytesIO()
    book.save(buf)
    with pytest.raises(SchemaDrift, match="missing columns"):
        ca.parse(buf.getvalue(), 2022, DAY)


def test_a_county_whose_channels_do_not_add_to_its_sum_is_drift():
    import io
    import openpyxl

    book = openpyxl.load_workbook(io.BytesIO(SEASON))
    sheet = book[ca.SHEET]
    header = [str(c.value or "").strip().lower() for c in sheet[4]]
    col = header.index("mail") + 1
    sheet.cell(row=5, column=col).value = 999_999
    buf = io.BytesIO()
    book.save(buf)
    with pytest.raises(SchemaDrift, match="return channels add to"):
        ca.parse(buf.getvalue(), 2022, DAY)


def test_an_unrecognised_county_is_drift():
    import io
    import openpyxl

    book = openpyxl.load_workbook(io.BytesIO(SEASON))
    book[ca.SHEET].cell(row=5, column=2).value = "Alamedaa"
    buf = io.BytesIO()
    book.save(buf)
    with pytest.raises(SchemaDrift, match="unrecognised county names"):
        ca.parse(buf.getvalue(), 2022, DAY)


def test_a_short_workbook_publishes_counties_and_no_statewide_row():
    import io
    import openpyxl

    book = openpyxl.load_workbook(io.BytesIO(SEASON))
    book[ca.SHEET].delete_rows(5)          # drop Alameda
    buf = io.BytesIO()
    book.save(buf)
    result = ca.parse(buf.getvalue(), 2022, DAY)
    assert result.state_rows == []
    assert len(result.county_rows) == 57


# --------------------------------------------------------------------------
# Transport: a missing file answers 403, not 404
# --------------------------------------------------------------------------
def test_an_s3_access_denied_body_is_absence_not_a_refusal():
    assert ca.looks_absent(
        b'<?xml version="1.0" encoding="UTF-8"?>\n'
        b"<Error><Code>AccessDenied</Code><Message>Access Denied</Message></Error>"
    )
    assert not ca.looks_absent(b"<!DOCTYPE html><title>Just a moment...</title>")


def test_a_403_without_an_s3_body_falls_through_rather_than_stopping(monkeypatch):
    class Response:
        status_code = 403
        content = b"<!DOCTYPE html><title>Just a moment...</title>"
        headers: dict[str, str] = {}

    monkeypatch.setattr(ca.SESSION, "get", lambda *a, **k: Response())
    with pytest.raises(SourceError, match="without an S3 AccessDenied body"):
        ca.download("https://example.invalid/x.xlsx", filename="x.xlsx")


def test_a_legacy_xls_body_says_so_rather_than_pretending_to_parse(monkeypatch):
    """2024's file of this name is OLE2, which openpyxl cannot read and which
    would need xlrd -- not a dependency of this project."""
    class Response:
        status_code = 200
        content = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 8000
        headers = {"Last-Modified": "Fri, 01 Nov 2024 12:00:00 GMT"}

    monkeypatch.setattr(ca.SESSION, "get", lambda *a, **k: Response())
    with pytest.raises(SourceError, match="legacy .xls or a PDF"):
        ca.download("https://example.invalid/x.xlsx", filename="x.xlsx")


def test_the_as_of_date_is_the_objects_own_write_time():
    assert ca.last_modified("Mon, 16 Jun 2025 22:51:49 GMT") == date(2025, 6, 16)
    assert ca.last_modified("not a date") is None
    assert ca.last_modified(None) is None


# --------------------------------------------------------------------------
# Adapter wiring
# --------------------------------------------------------------------------
def test_adapter_identity():
    scraper = ca.CAScraper()
    assert (scraper.state, scraper.name, scraper.tier) == ("CA", "ca-sos", 1)


def test_the_url_is_built_from_the_cycle():
    assert ca.URL.format(cycle=2026, ext=".xlsx").endswith(
        "/statewide-elections/2026-general/vbm-statistics.xlsx"
    )


def test_a_stale_workbook_is_refused_rather_than_republished(monkeypatch):
    """The live 2022 workbook was rewritten on 2025-06-16. Publishing it as
    this season's snapshot would put three-year-old returns on today's curve."""
    monkeypatch.setattr(
        ca.CAScraper, "_load",
        lambda self, cycle, **k: (FINAL, date(2025, 6, 16)),
    )
    with pytest.raises(NotYetPublished, match="outside the window"):
        ca.CAScraper().fetch(2022, date(2022, 11, 8))


def test_a_workbook_with_no_last_modified_is_not_published(monkeypatch):
    monkeypatch.setattr(ca.CAScraper, "_load",
                        lambda self, cycle, **k: (SEASON, None))
    with pytest.raises(SourceError, match="without a Last-Modified header"):
        ca.CAScraper().fetch(2022, date(2022, 10, 27))


def test_fetch_stamps_the_row_with_the_objects_write_date(monkeypatch):
    monkeypatch.setattr(ca.CAScraper, "_load",
                        lambda self, cycle, **k: (SEASON, date(2022, 10, 27)))
    (row,) = ca.CAScraper().fetch(2022, date(2022, 10, 28)).state_rows
    assert row.day == date(2022, 10, 27)
    assert row.ballots_total == 1_642_945


def test_a_workbook_stamped_after_the_run_falls_through(monkeypatch):
    monkeypatch.setattr(ca.CAScraper, "_load",
                        lambda self, cycle, **k: (SEASON, date(2022, 10, 27)))
    with pytest.raises(SourceError, match="after the run date"):
        ca.CAScraper().fetch(2022, date(2022, 10, 26))


def test_there_is_no_dated_archive_to_backfill_from():
    with pytest.raises(NotYetPublished, match="overwrites one vbm-statistics URL"):
        ca.CAScraper().fetch_history(2022)


# --------------------------------------------------------------------------
# Nothing is blocking California, and that has to stay written down
# --------------------------------------------------------------------------
def test_california_is_empty_because_of_the_calendar_not_a_bot_wall():
    """⚠️ "CA has no county data in any cycle" reads like a block and is not one.

    Re-measured 2026-09-08 with plain `requests` and NO impersonation: every
    2026 path answers 403 with a 111-byte `<Code>AccessDenied</Code>` body from
    `Server: AmazonS3` -- S3's answer for a key that does not exist, because
    listing is denied -- and the 2022 workbook answers 200 with 77,507 real
    bytes. There is no WAF, no challenge and no fingerprint rule on this CDN,
    which is why this module has no impersonation path and should not grow one.
    """
    doc = ca.__doc__
    assert "NOTHING IS BLOCKING CALIFORNIA" in doc
    assert "AmazonS3" in doc
    # ...and the S3 absence body is still read as absence, not as a refusal.
    assert ca.looks_absent(b'<?xml version="1.0" encoding="UTF-8"?>'
                           b'<Error><Code>AccessDenied</Code>'
                           b'<Message>Access Denied</Message></Error>') is True


def test_a_403_that_is_not_s3_absence_is_a_refusal_not_absence():
    """The distinction the whole adapter turns on: S3 saying "no such key" STOPS
    the ladder, a WAF saying no falls THROUGH. A block page must never be read as
    California not having published."""
    from ev.adapters._net import WALL_MARKERS

    for marker in WALL_MARKERS:
        assert ca.looks_absent(marker + b" " * 400) is False


def test_the_archive_route_is_documented_with_the_captures_it_would_read():
    """It is unbuilt on purpose, but the survey behind it must not have to be
    redone: three during-season 2022 captures and one 2024, all of this exact
    key, all readable by `parse()` unchanged."""
    doc = ca.CAScraper.fetch_history.__doc__
    for stamp in ("2022-10-27", "2022-11-05", "2022-11-08", "2024-11-01"):
        assert stamp in doc
