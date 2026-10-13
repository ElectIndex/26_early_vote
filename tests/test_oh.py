"""Ohio: the SoS county absentee workbook, and the Power BI dashboard behind it.

Three fixtures, one per route:

* two workbooks -- the 2022 general has 11 columns and the 2024 general has 17,
  because Ohio added a dropbox/personal-delivery/by-mail breakdown between
  cycles. Parsing both with the same code is the whole point of matching columns
  by header name, so both are locked in here.
* `publicfiles_files-index.json` -- the SoS data portal's own file index,
  trimmed to the 2022/2024/2026 year entries but with every entry verbatim. It
  is how the adapter finds a workbook without guessing Ohio's inconsistent
  folder names.
* `powerbi_absentee_querydata.json` -- one real, whole response from the
  absentee dashboard's query API (808 rows, seven elections, 88 counties). Kept
  whole rather than trimmed because the wire format compresses each row against
  the one before it, so a truncated capture would decode differently from a
  real one.
* `powerbi_absentee_ledger_2024.json` -- one real, whole response to the
  LEDGER query (`build_ledger_query("2024 NOV GENERAL")`, with the report's
  own two page filters replicated server-side): 3,182 rows, 88 counties x
  `Data_Date` 2024-09-20..2024-11-06 plus one undated zero row per county.
  Whole for the same reason as the dashboard capture, and because the statewide
  checkpoints below need all 88 counties -- the parser refuses to sum fewer.
* `weekly_2022-*.xlsx` -- the four 2022 press-release county workbooks, whole
  (88 counties + TOTAL each, 13-15 KB). The 10-12 file is byte-identical to the
  live Constant Contact object (its MD5 is the S3 ETag); the other three are
  the Wayback captures pinned in `oh.WEEKLY_RELEASES`.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from ev.adapters import oh
from ev.adapters._net import Missing
from ev.adapters.base import NotYetPublished, SchemaDrift, SourceError

FIXTURES = Path(__file__).parent / "fixtures" / "oh"


def _parse(name: str, cycle: int):
    return oh.parse((FIXTURES / name).read_bytes(), cycle)


@pytest.fixture(scope="module")
def gen2024():
    return _parse("2024gen_absentee_report_web.xlsx", 2024)


@pytest.fixture(scope="module")
def gen2022():
    return _parse("2022gen_absentee_report_web.xlsx", 2022)


@pytest.fixture(scope="module")
def dashboard():
    return json.loads((FIXTURES / "powerbi_absentee_querydata.json").read_bytes())


@pytest.fixture(scope="module")
def dash2024(dashboard):
    return oh.parse_dashboard(dashboard, 2024)


@pytest.fixture(scope="module")
def files_index():
    return json.loads((FIXTURES / "publicfiles_files-index.json").read_bytes())


# --------------------------------------------------------------------------
# THE BLANK RULE -- the one that matters most for this batch
# --------------------------------------------------------------------------
def test_no_party_field_is_ever_populated(gen2024, gen2022, dash2024):
    """Ohio has no party registration. A 0 here would render as 'zero Democrats
    have voted' on a page whose whole subject is who is voting early.

    The dashboard is included deliberately: it DOES carry a
    `Voter_Party_Bucketed` column (derived from which primary ballot a voter last
    took), and reading it would publish a party breakdown Ohio does not have."""
    for result in (gen2024, gen2022, dash2024):
        for row in result.state_rows + result.county_rows:
            for field in ("party_dem", "party_rep", "party_oth", "party_npa"):
                assert getattr(row, field) is None, (field, row)


# --------------------------------------------------------------------------
# The statewide row is Ohio's own, not our sum
# --------------------------------------------------------------------------
def test_statewide_row_is_ohios_own_number(gen2024):
    (state,) = gen2024.state_rows
    assert state.state == "OH"
    assert state.day == date(2024, 11, 5)
    assert state.ballots_total == 2620750          # verbatim from the Statewide row
    assert state.mail_requested == 1131698 + 21870  # domestic + UOCAVA transmitted
    assert state.mail_returned == 1066815 + 17681   # domestic + UOCAVA by mail
    assert state.inperson == 1536219 + 35           # domestic + UOCAVA in person


def test_uocava_is_added_not_dropped(gen2024):
    """Military and overseas ballots live in their own column family; reading only
    the 'Domestic ...' columns silently understates every returns number."""
    (state,) = gen2024.state_rows
    assert state.mail_returned > 1066815


def test_statewide_is_not_emitted_as_a_county(gen2024):
    assert all(row.county_fips != "39000" for row in gen2024.county_rows)
    assert "Statewide" not in {row.county_name for row in gen2024.county_rows}


# --------------------------------------------------------------------------
# County rows are keyed by FIPS, never by name
# --------------------------------------------------------------------------
def test_county_rows_are_fips_keyed(gen2024):
    by_fips = {row.county_fips: row for row in gen2024.county_rows}
    assert by_fips["39035"].county_name == "Cuyahoga County"
    assert by_fips["39049"].county_name == "Franklin County"
    adams = by_fips["39001"]
    assert adams.ballots_total == 6966
    assert adams.mail_returned == 1681 + 20
    assert adams.inperson == 5265 + 0


def test_all_county_fips_are_five_digit_ohio(gen2024):
    for row in gen2024.county_rows:
        assert len(row.county_fips) == 5 and row.county_fips.startswith("39")


# --------------------------------------------------------------------------
# The 2022 layout has six fewer columns and must still parse
# --------------------------------------------------------------------------
def test_2022_layout_parses_by_name_not_position(gen2022):
    (state,) = gen2022.state_rows
    assert state.day == date(2022, 11, 8)
    assert state.ballots_total == 1473983
    assert state.mail_returned == 918333 + 5546
    assert state.inperson == 550092 + 12
    assert {r.county_fips for r in gen2022.county_rows} >= {"39001", "39035", "39049"}


def test_sheet_title_supplies_the_as_of_date():
    assert oh.sheet_date("11.5.2024 Absentee Report", 2024) == date(2024, 11, 5)
    assert oh.sheet_date("11.8.22 Absentee Report", 2022) == date(2022, 11, 8)


def test_sheet_title_from_the_wrong_cycle_is_drift():
    """A stale workbook left on the server would otherwise publish 2024 counts
    under the 2026 cycle."""
    with pytest.raises(SchemaDrift):
        oh.sheet_date("11.5.2024 Absentee Report", 2026)


# --------------------------------------------------------------------------
# Drift and absence in the workbook
# --------------------------------------------------------------------------
def test_renamed_column_raises_drift(tmp_path):
    """Ohio renaming a column must fail loudly, not silently map to something."""
    import openpyxl

    book = openpyxl.load_workbook(FIXTURES / "2024gen_absentee_report_web.xlsx")
    sheet = book.worksheets[0]
    sheet.cell(row=1, column=4).value = "Ballots we mailed out"
    path = tmp_path / "drifted.xlsx"
    book.save(path)
    with pytest.raises(SchemaDrift, match="missing columns"):
        oh.parse(path.read_bytes(), 2024)


def test_unknown_county_name_raises_drift(tmp_path):
    """A name we cannot place would otherwise vanish off the county map."""
    import openpyxl

    book = openpyxl.load_workbook(FIXTURES / "2024gen_absentee_report_web.xlsx")
    book.worksheets[0].cell(row=3, column=1).value = "Atlantis"
    path = tmp_path / "unknown.xlsx"
    book.save(path)
    with pytest.raises(SchemaDrift, match="unrecognised county"):
        oh.parse(path.read_bytes(), 2024)


# --------------------------------------------------------------------------
# Route 2: finding a workbook in the publicfiles index instead of guessing
# --------------------------------------------------------------------------
def test_index_lookup_finds_the_general_absentee_workbook(files_index):
    """Ohio's folder names are not guessable -- 2022 uses 'General Election:
    November 8, 2022' while the 2026 primary uses 'Primary+Special Election -
    May 5, 2026'. The path must come out of the index, never out of a template."""
    assert oh.find_workbook(files_index, 2024) == (
        "past-elections/2024/General Election: November 5, 2024/"
        "absentee_report_web.xlsx"
    )
    assert oh.find_workbook(files_index, 2022) == (
        "past-elections/2022/General Election: November 8, 2022/"
        "absentee_report_web.xlsx"
    )


def test_index_lookup_ignores_primaries_and_specials(files_index):
    """2026 carries a May primary whose absentee report is right there in the
    index. Matching it would publish primary turnout as the general election."""
    with pytest.raises(NotYetPublished, match="no 2026 November general"):
        oh.find_workbook(files_index, 2026)


def test_index_lookup_does_not_return_the_provisional_report(files_index):
    """Every election folder holds `provisional_report_web.xlsx` beside the
    absentee one, and it has an entirely different meaning."""
    for cycle in (2022, 2024):
        assert "provisional" not in oh.find_workbook(files_index, cycle)


def test_index_without_the_expected_root_is_drift():
    with pytest.raises(SchemaDrift, match="pastElectionResults"):
        oh.find_workbook({"somethingElse": []}, 2024)


def test_blob_url_escapes_ohios_folder_names():
    """The colons, commas and spaces in 'General Election: November 5, 2024' all
    have to be percent-encoded or the blob store answers 404."""
    url = oh._blob_url("past-elections/2024/General Election: November 5, 2024/a.xlsx")
    assert url.startswith(oh.PUBLICFILES_BASE)
    assert " " not in url and ":" not in url.split("//", 1)[1]


# --------------------------------------------------------------------------
# Route 3: the Power BI absentee dashboard
# --------------------------------------------------------------------------
def test_dsr_decodes_to_named_columns(dashboard):
    rows = oh.decode_dsr(dashboard)
    assert len(rows) == 808
    assert set(rows[0]) == {
        "Election_Description", "County_Name", "REFRESH_DATE",
        "BALLOTS_SENT_INCL_EIP", "BALLOTS_RECEIVED_INCL_EIP",
        "BALLOTS_SENT_NO_EIP", "BALLOTS_RECEIVED_NO_EIP",
    }


def test_dsr_repeat_bitmask_is_expanded(dashboard):
    """Every row after the first omits any value identical to the row above and
    flags it in `R`. Forgetting to expand that leaves most rows with a blank
    election label, which would quietly drop them from the cycle filter."""
    rows = oh.decode_dsr(dashboard)
    assert all(r["Election_Description"] for r in rows)
    assert all(r["County_Name"] for r in rows)
    assert len({r["Election_Description"] for r in rows}) == 7


def test_dashboard_county_counts(dash2024):
    by_fips = {r.county_fips: r for r in dash2024.county_rows}
    assert len(dash2024.county_rows) == 88
    cuyahoga = by_fips["39035"]
    assert cuyahoga.county_name == "Cuyahoga County"
    assert cuyahoga.ballots_total == 178821
    assert cuyahoga.mail_returned == 122302
    # Early in-person is the INCL_EIP / NO_EIP difference, not a column.
    assert cuyahoga.inperson == 178821 - 122302


def test_dashboard_statewide_is_a_verified_full_sum(dash2024):
    """The dashboard has no statewide row of its own, so the StateDay is OUR sum
    -- and is only emitted because all 88 counties are present."""
    (state,) = dash2024.state_rows
    assert state.day == date(2024, 11, 12)
    assert state.ballots_total == 2262963
    assert state.mail_requested == 985715
    assert state.mail_returned == 915295
    assert state.inperson == 2262963 - 915295


def test_dashboard_is_a_different_measurement_from_the_workbook(dash2024, gen2024):
    """A guard against someone 'fixing' one of these to match the other. The
    workbook is the certified restatement; the dashboard is the daily tracker
    that stops before the last in-person days and the late mail are counted."""
    (tracker,) = dash2024.state_rows
    (certified,) = gen2024.state_rows
    assert tracker.ballots_total < certified.ballots_total


def test_dashboard_partial_coverage_publishes_no_statewide_row(dashboard):
    """A sum of 60 counties looks exactly like a number for Ohio without being
    one, so short coverage must publish counties and nothing else."""
    trimmed = json.loads(json.dumps(dashboard))
    holder = trimmed["results"][0]["result"]["data"]["dsr"]["DS"][0]["PH"][0]
    key = next(k for k in holder if k.startswith("DM"))
    # Cut inside the 2024 general's block, so the election is present but only
    # some of its counties are. Truncating from the end leaves every surviving
    # row's back-reference into the row above it intact.
    holder[key] = holder[key][:290]
    result = oh.parse_dashboard(trimmed, 2024)
    assert result.county_rows
    assert len(result.county_rows) < 88
    assert result.state_rows == []


def test_dashboard_without_the_cycle_is_not_yet_published(dashboard):
    """The live path every day until Ohio's counties start submitting. It must
    STOP the ladder, not fall through to a source that invents a zero."""
    with pytest.raises(NotYetPublished, match="no 2026 November general"):
        oh.parse_dashboard(dashboard, 2026)


def test_dashboard_does_not_match_a_special_general(dashboard):
    """'2024 JUNE CD 6 SPECIAL GENERAL' is in the same table as
    '2024 NOV GENERAL' and covers 11 counties, not 88."""
    labels = {r["Election_Description"] for r in oh.decode_dsr(dashboard)}
    assert "2024 JUNE CD 6 SPECIAL GENERAL" in labels
    assert len(oh.parse_dashboard(dashboard, 2024).county_rows) == 88


def test_dashboard_missing_measure_is_drift(dashboard):
    """Power BI renaming or dropping a column must fail loudly: the measures are
    positional on the wire and are only safe because they are matched by name."""
    broken = json.loads(json.dumps(dashboard))
    select = broken["results"][0]["result"]["data"]["descriptor"]["Select"]
    for item in select:
        if item["Name"].endswith("BALLOTS_RECEIVED_NO_EIP)"):
            item["Name"] = "Sum(absentee v_detailed_grouped.SOMETHING_ELSE)"
    with pytest.raises(SchemaDrift, match="missing columns"):
        oh.decode_dsr(broken)


def test_dashboard_response_that_is_not_a_dsr_is_drift():
    with pytest.raises(SchemaDrift, match="not a DSR result"):
        oh.decode_dsr({"results": [{"result": {}}]})


def test_mail_larger_than_total_is_drift():
    """If NO_EIP ever stops nesting inside INCL_EIP, in-person would come out
    negative. Refuse rather than publish it."""
    with pytest.raises(SchemaDrift, match="no longer nest"):
        oh._sub(10, 11)


def test_unreported_measure_stays_none_not_zero():
    assert oh._sub(None, 5) is None
    assert oh._sub(5, None) is None
    assert oh._add(None, None) is None
    assert oh._add(None, 3) == 3


def test_query_body_asks_for_the_columns_the_parser_reads():
    """The request and the parser must not drift apart: every column the parser
    looks up has to be in the projection."""
    body = oh.build_query()
    command = body["queries"][0]["Query"]["Commands"][0]
    names = {s["Name"] for s in command["SemanticQueryDataShapeCommand"]["Query"]["Select"]}
    for column in oh.POWERBI_COLUMNS:
        assert f"{oh.POWERBI_ENTITY}.{column}" in names
    for measure in oh.POWERBI_MEASURES:
        assert f"Sum({oh.POWERBI_ENTITY}.{measure})" in names
    assert body["modelId"] == oh.POWERBI_MODEL_ID


# --------------------------------------------------------------------------
# The adapter's walk over the three routes
# --------------------------------------------------------------------------
@pytest.fixture
def no_workbooks(monkeypatch):
    """Both workbook routes 404 -- the ordinary state of the world during the
    early-vote period, before Ohio certifies and posts the file."""
    def missing(url, **kwargs):
        raise Missing(f"OH: {url} returned 404")

    monkeypatch.setattr(oh, "download", missing)


def test_missing_2026_report_is_not_yet_published(no_workbooks, monkeypatch, dashboard):
    """This is the path that runs every day for weeks before Ohio posts anything.
    It must STOP the ladder, not fall through to a source that invents a zero."""
    monkeypatch.setattr(oh, "fetch_dashboard", lambda: dashboard)
    with pytest.raises(NotYetPublished):
        oh.OHScraper().fetch(2026, date(2026, 9, 5))


def test_every_route_blocked_is_a_source_error_not_an_absence(monkeypatch):
    """The distinction the whole ladder turns on. A 403 wall means we could not
    LOOK; reporting it as NotYetPublished would stop the ladder and leave Ohio
    blank on a day the aggregator could have answered."""
    def blocked(url, **kwargs):
        raise SourceError(f"OH: {url} returned HTTP 403")

    monkeypatch.setattr(oh, "download", blocked)
    monkeypatch.setattr(
        oh, "fetch_dashboard",
        lambda: (_ for _ in ()).throw(SourceError("OH: dashboard unreachable")),
    )
    with pytest.raises(SourceError) as caught:
        oh.OHScraper().fetch(2026, date(2026, 9, 5))
    assert not isinstance(caught.value, NotYetPublished)


def test_blocked_history_is_a_source_error_not_an_absence(monkeypatch):
    def blocked(url, **kwargs):
        raise SourceError(f"OH: {url} returned HTTP 403")

    monkeypatch.setattr(oh, "download", blocked)
    monkeypatch.setattr(oh, "download_dated", blocked)
    with pytest.raises(SourceError) as caught:
        oh.OHScraper().fetch_history(2022)
    assert not isinstance(caught.value, NotYetPublished)


def test_history_for_an_election_not_in_the_index_is_not_yet_published(monkeypatch):
    """A 404, unlike a 403, really does mean the workbook is not there -- and
    the same holds for the four weekly files, every one of which is a 404
    here."""
    def missing(url, **kwargs):
        raise Missing(f"OH: {url} returned 404")

    monkeypatch.setattr(oh, "download", missing)
    monkeypatch.setattr(oh, "download_dated", missing)
    with pytest.raises(NotYetPublished):
        oh.OHScraper().fetch_history(2022)


def test_soft_404_html_page_is_not_yet_published(monkeypatch, dashboard):
    """The SoS CMS answers an unposted report with a styled 200 HTML page, and
    the retired globalassets path 301s to one."""
    monkeypatch.setattr(oh, "download", lambda url, **kw: b"<!DOCTYPE html><html>x</html>")
    monkeypatch.setattr(oh, "fetch_dashboard", lambda: dashboard)
    with pytest.raises(NotYetPublished):
        oh.OHScraper().fetch(2026, date(2026, 9, 5))


def test_blocked_workbook_routes_fall_through_to_the_dashboard(monkeypatch, dashboard):
    """The point of the whole exercise: every ohiosos.gov host answers HTTP 403
    to a stock HTTP client, and the dashboard -- which is not behind that
    protection -- has to be allowed to answer anyway."""
    def blocked(url, **kwargs):
        raise SourceError(f"OH: {url} returned HTTP 403")

    monkeypatch.setattr(oh, "download", blocked)
    monkeypatch.setattr(oh, "fetch_dashboard", lambda: dashboard)
    result = oh.OHScraper().fetch(2024, date(2024, 11, 20))
    assert len(result.county_rows) == 88
    (state,) = result.state_rows
    assert state.ballots_total == 2262963      # the dashboard's number, not 2,620,750


def test_workbook_beats_the_dashboard_when_both_answer(monkeypatch, dashboard):
    """Once the certified workbook exists it is the better measurement, so the
    dashboard must not be reached."""
    body = (FIXTURES / "2024gen_absentee_report_web.xlsx").read_bytes()
    monkeypatch.setattr(oh, "download", lambda url, **kw: body)
    monkeypatch.setattr(
        oh, "fetch_dashboard",
        lambda: pytest.fail("dashboard queried even though the workbook answered"),
    )
    (state,) = oh.OHScraper().fetch(2024, date(2024, 11, 20)).state_rows
    assert state.ballots_total == 2620750


def test_future_dated_report_is_not_published(monkeypatch, dashboard):
    body = (FIXTURES / "2024gen_absentee_report_web.xlsx").read_bytes()
    monkeypatch.setattr(oh, "download", lambda url, **kw: body)
    monkeypatch.setattr(oh, "fetch_dashboard", lambda: dashboard)
    with pytest.raises(NotYetPublished, match="after"):
        oh.OHScraper().fetch(2024, date(2024, 10, 1))


def test_future_dated_dashboard_snapshot_is_not_published(monkeypatch, dashboard):
    """The dashboard's snapshot is stamped with its own refresh date; a backfill
    run must not publish a row from after the day it is backfilling."""
    def missing(url, **kwargs):
        raise Missing(f"OH: {url} returned 404")

    monkeypatch.setattr(oh, "download", missing)
    monkeypatch.setattr(oh, "fetch_dashboard", lambda: dashboard)
    with pytest.raises(NotYetPublished, match="after"):
        oh.OHScraper().fetch(2024, date(2024, 10, 1))


def test_history_uses_the_workbook_not_the_dashboard(monkeypatch):
    """Backfill anchors the 2022/2024 comparison lines, so the Election-Day row
    must come from the certified workbook -- mixing in the tracker's smaller
    number would make the comparison read as a collapse in turnout. For 2022
    the dashboard is never even asked: the ledger starts with 2023.

    With every weekly file 404ing, the workbook's row still comes back alone."""
    body = (FIXTURES / "2022gen_absentee_report_web.xlsx").read_bytes()
    monkeypatch.setattr(oh, "download", lambda url, **kw: body)
    monkeypatch.setattr(
        oh, "download_dated",
        lambda url, **kw: (_ for _ in ()).throw(Missing(f"OH: {url} returned 404")),
    )
    monkeypatch.setattr(
        oh, "fetch_dashboard",
        lambda: pytest.fail("fetch_history(2022) must never query the dashboard"),
    )
    (state,) = oh.OHScraper().fetch_history(2022).state_rows
    assert state.ballots_total == 1473983
    assert state.day == date(2022, 11, 8)


def test_history_for_the_current_cycle_is_not_yet_published():
    with pytest.raises(NotYetPublished):
        oh.OHScraper().fetch_history(date.today().year)


def test_adapter_identity():
    scraper = oh.OHScraper()
    assert (scraper.state, scraper.name, scraper.tier) == ("OH", "oh-sos", 1)


# ==========================================================================
# THE 2024 LEDGER -- the dashboard's table read as a per-event ledger
# ==========================================================================
LEDGER_REQUIRED = oh.LEDGER_COLUMNS + oh.POWERBI_MEASURES


@pytest.fixture(scope="module")
def ledger():
    return json.loads((FIXTURES / "powerbi_absentee_ledger_2024.json").read_bytes())


@pytest.fixture(scope="module")
def ledger_rows(ledger):
    return oh.decode_dsr(ledger, required=LEDGER_REQUIRED)


@pytest.fixture(scope="module")
def ledger2024(ledger_rows):
    return oh.parse_ledger(ledger_rows, 2024)


def _epoch_ms(day: date) -> int:
    from datetime import datetime, timezone

    return int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp() * 1000)


def _ledger_row(county: str, day: date | None, sent_all=0, back_all=0, sent_mail=0, back_mail=0):
    return {
        "Election_Description": "2024 NOV GENERAL", "County_Name": county,
        "Data_Date": None if day is None else _epoch_ms(day),
        "BALLOTS_SENT_INCL_EIP": sent_all, "BALLOTS_RECEIVED_INCL_EIP": back_all,
        "BALLOTS_SENT_NO_EIP": sent_mail, "BALLOTS_RECEIVED_NO_EIP": back_mail,
    }


def test_ledger_decodes_with_its_own_column_set(ledger_rows):
    """The ledger selects `Data_Date` where the live query selects
    `REFRESH_DATE`; the decoder has to be told which set to insist on."""
    assert len(ledger_rows) == 3182
    assert set(ledger_rows[0]) == set(LEDGER_REQUIRED)
    assert {r["Election_Description"] for r in ledger_rows} == {"2024 NOV GENERAL"}
    assert len({r["County_Name"] for r in ledger_rows}) == 88


def test_ledger_response_fails_the_live_decoder(ledger):
    """...and the live decoder must refuse it, rather than read Data_Date as a
    refresh stamp."""
    with pytest.raises(SchemaDrift, match="REFRESH_DATE"):
        oh.decode_dsr(ledger)


def test_ledger_statewide_checkpoints(ledger2024):
    """Cumulative "as of D" totals under the report's own filters. These are the
    SoS's displayed-report numbers, which exclude 1,109 ballots the raw table
    dates to 2024-09-03..09-19 (before UOCAVA mailing) -- so 10-15 reads
    460,759 received, not the 460,768 a cycle-wide clamp would give."""
    by_day = {row.day: row for row in ledger2024.state_rows}
    oct15, oct29, nov4 = by_day[date(2024, 10, 15)], by_day[date(2024, 10, 29)], by_day[date(2024, 11, 4)]
    assert (oct15.ballots_total, oct15.mail_returned, oct15.inperson) == (460759, 131450, 329309)
    assert (oct29.ballots_total, oct29.mail_returned, oct29.inperson) == (1956377, 830842, 1125535)
    assert (nov4.ballots_total, nov4.mail_returned, nov4.inperson) == (2566824, 1028785, 1538039)
    assert oct15.mail_requested == 991022           # BALLOTS_SENT_NO_EIP, cumulated
    assert nov4.mail_requested == 1146476
    assert all(row.mail_requested >= row.mail_returned for row in ledger2024.state_rows)


def test_ledger_cuyahoga_checkpoints(ledger2024):
    cuyahoga = {r.day: r for r in ledger2024.county_rows if r.county_fips == "39035"}
    assert cuyahoga[date(2024, 10, 15)].ballots_total == 32705
    assert cuyahoga[date(2024, 10, 25)].ballots_total == 135820
    assert cuyahoga[date(2024, 11, 4)].ballots_total == 221097
    row = cuyahoga[date(2024, 11, 4)]
    assert row.county_name == "Cuyahoga County"
    assert row.mail_returned == 158505
    assert row.inperson == 221097 - 158505


def test_ledger_series_stops_the_day_before_election_day(ledger_rows, ledger2024):
    """The raw ledger runs to 11-06 (and its 11-05 cumulative for Cuyahoga would
    be 227,645), but Election Day belongs to the certified workbook: the ledger
    final (2,616,339) and the workbook (2,620,750) disagree, and the workbook is
    the restatement the comparison lines anchor on. So the series ends 11-04."""
    raw_days = {oh._epoch_ms_to_date(r["Data_Date"]) for r in ledger_rows if r["Data_Date"]}
    assert date(2024, 11, 5) in raw_days and date(2024, 11, 6) in raw_days
    assert max(r.day for r in ledger2024.state_rows) == date(2024, 11, 4)
    assert max(r.day for r in ledger2024.county_rows) == date(2024, 11, 4)
    assert min(r.day for r in ledger2024.state_rows) == date(2024, 9, 20)
    assert len(ledger2024.state_rows) == 45


def test_ledger_cumulative_sum_matches_the_raw_events(ledger_rows, ledger2024):
    """Each county's row on D is the sum of its events dated <= D, computed here
    independently of the parser, for every county and every day."""
    events: dict[tuple[str, date], int] = {}
    for r in ledger_rows:
        if r["Data_Date"] is None:
            continue
        key = (r["County_Name"], oh._epoch_ms_to_date(r["Data_Date"]))
        events[key] = events.get(key, 0) + (r["BALLOTS_RECEIVED_INCL_EIP"] or 0)
    for row in ledger2024.county_rows:
        name = row.county_name.removesuffix(" County").upper()
        expected = sum(v for (county, day), v in events.items() if county == name and day <= row.day)
        assert row.ballots_total == expected, (row.county_name, row.day)


def test_ledger_carries_a_county_forward_on_days_it_had_no_event(ledger2024):
    """A county with nothing sent or received on a day is still "as of" that
    day at its previous total -- every county has a row on every day, and the
    totals never fall."""
    assert len(ledger2024.county_rows) == 88 * 45
    by_county: dict[str, list] = {}
    for row in ledger2024.county_rows:
        by_county.setdefault(row.county_fips, []).append(row)
    for rows in by_county.values():
        rows.sort(key=lambda r: r.day)
        totals = [r.ballots_total for r in rows]
        assert totals == sorted(totals)
    adams = {r.day: r for r in by_county["39001"]}
    assert adams[date(2024, 10, 15)].ballots_total == 1225
    assert adams[date(2024, 10, 15)].mail_returned == 236


def test_ledger_junk_dates_are_skipped_loudly(ledger_rows, ledger2024, caplog):
    """Unfiltered, the table carries rows dated 1984, 3162 and 9204. The query
    replicates the report's filters so they never arrive; if they ever do, they
    must not land on the curve, and the skip must be visible."""
    junk = [
        _ledger_row("ADAMS", date(1984, 10, 21), 3, 3, 3, 3),
        _ledger_row("ADAMS", date(3162, 10, 21), 2, 2, 2, 2),
        _ledger_row("CUYAHOGA", date(2024, 1, 1), 50, 50, 50, 50),
        _ledger_row("CUYAHOGA", None, 7, 7, 7, 7),           # undated, with ballots
        _ledger_row("CUYAHOGA", None, 0, 0, 0, 0),           # undated, empty: nothing
    ]
    with caplog.at_level("WARNING"):
        result = oh.parse_ledger(ledger_rows + junk, 2024)
    assert [r.ballots_total for r in result.state_rows] == \
        [r.ballots_total for r in ledger2024.state_rows]
    assert [r.ballots_total for r in result.county_rows] == \
        [r.ballots_total for r in ledger2024.county_rows]
    (record,) = [r for r in caplog.records if "SKIPPED" in r.getMessage()]
    assert "4 ledger rows" in record.getMessage()
    assert "62 sent, 62 received" in record.getMessage()
    assert "1984-10-21" in record.getMessage() and "undated" in record.getMessage()


def test_ledger_clean_input_logs_no_skip(ledger_rows, caplog):
    with caplog.at_level("WARNING"):
        oh.parse_ledger(ledger_rows, 2024)
    assert not [r for r in caplog.records if "SKIPPED" in r.getMessage()]


def test_ledger_rows_from_another_election_are_drift(ledger_rows):
    """The `Where` is supposed to keep the answer to one election; a row from
    another one means the filter is no longer honoured, and summing it into
    the cycle would be exactly the silent mis-mapping the rules forbid."""
    rows = [dict(r) for r in ledger_rows[:50]]
    rows[10]["Election_Description"] = "2023 NOV GENERAL"
    with pytest.raises(SchemaDrift, match="election filter"):
        oh.parse_ledger(rows, 2024)


def test_ledger_for_2022_is_refused_before_any_query(monkeypatch, dashboard):
    """The table starts with the 2023 November general. 2022 must be refused
    without a request, and the refusal is absence (NotYetPublished), not a
    failure. The dashboard fixture, which carries seven elections and no 2022,
    says the same thing by data."""
    monkeypatch.setattr(oh, "fetch_dashboard", lambda: pytest.fail("queried"))
    monkeypatch.setattr(oh, "fetch_ledger", lambda *a, **k: pytest.fail("queried"))
    with pytest.raises(NotYetPublished, match="begins with the 2023"):
        oh.OHScraper()._ledger_series(2022)
    with pytest.raises(NotYetPublished, match="no 2022 November general"):
        oh.general_label(oh.decode_dsr(dashboard), 2022)


def test_ledger_partial_coverage_publishes_no_statewide_rows(ledger_rows, caplog):
    """Same rule as the live dashboard: fewer than 88 counties is counties
    only. Rows are ordered by county, so a prefix is whole counties."""
    counties = []
    for r in ledger_rows:
        if r["County_Name"] not in counties:
            counties.append(r["County_Name"])
    keep = set(counties[:18])                     # ADAMS .. CUYAHOGA
    with caplog.at_level("WARNING"):
        result = oh.parse_ledger([r for r in ledger_rows if r["County_Name"] in keep], 2024)
    assert result.state_rows == []
    assert {r.county_fips for r in result.county_rows} >= {"39001", "39035"}
    assert len({r.county_fips for r in result.county_rows}) == 18
    assert any("18 of 88" in r.getMessage() for r in caplog.records)


def test_ledger_with_only_election_day_rows_is_absence():
    rows = [_ledger_row("ADAMS", date(2024, 11, 5), 1, 1, 1, 1)]
    with pytest.raises(NotYetPublished, match="on or after Election Day"):
        oh.parse_ledger(rows, 2024)
    with pytest.raises(NotYetPublished, match="no rows"):
        oh.parse_ledger([], 2024)


def test_ledger_query_carries_the_reports_own_filters():
    """The election label, both page-filter booleans, and Data_Date in the
    projection -- the query and the parser must not drift apart."""
    body = oh.build_ledger_query("2024 NOV GENERAL")
    query = body["queries"][0]["Query"]["Commands"][0]["SemanticQueryDataShapeCommand"]["Query"]
    names = {s["Name"] for s in query["Select"]}
    for column in oh.LEDGER_COLUMNS:
        assert f"{oh.POWERBI_ENTITY}.{column}" in names
    for measure in oh.POWERBI_MEASURES:
        assert f"Sum({oh.POWERBI_ENTITY}.{measure})" in names
    where = json.dumps(query["Where"])
    assert "'2024 NOV GENERAL'" in where
    assert oh.FLAG_RETURN_RANGE in where and oh.FLAG_VALID_DATE in where
    assert where.count('"true"') == 2
    assert body["modelId"] == oh.POWERBI_MODEL_ID
    # ...and the live query is unchanged by the refactor: no Where at all.
    live = oh.build_query()["queries"][0]["Query"]["Commands"][0]["SemanticQueryDataShapeCommand"]["Query"]
    assert "Where" not in live


def test_model_rejection_under_http_200_is_surfaced():
    """Power BI answers a query on a renamed column with HTTP 200 and an
    `odata.error` inside the data shape. Unchecked, that is an empty ledger."""
    rejected = {"results": [{"result": {"data": {"dsr": {"DataShapes": [
        {"odata.error": {"code": "rsDataShapeQueryTranslationError", "message": {"value": "x"}}}
    ]}}}}]}
    error = oh._query_error(rejected)
    assert error is not None and "rsDataShapeQueryTranslationError" in error
    assert oh._query_error({"results": [{"result": {"data": {"dsr": {"DS": []}}}}]}) is None


# ==========================================================================
# THE 2022 WEEKLY PRESS-RELEASE FILES
# ==========================================================================
WEEKLY_FIXTURES = {
    date(2022, 10, 12): ("weekly_2022-10-12_absentee_survey_1.xlsx", oh.WEEKLY_REQUESTS),
    date(2022, 10, 25): ("weekly_2022-10-25_absentee_survey_3.xlsx", oh.WEEKLY_REQUESTS_EIP),
    date(2022, 11, 1): ("weekly_2022-11-01_oneweekoutev2022.xlsx", oh.WEEKLY_FULL),
    date(2022, 11, 7): ("weekly_2022-11-07_onedayoutev2022.xlsx", oh.WEEKLY_FULL),
}


def _weekly(as_of: date):
    name, kind = WEEKLY_FIXTURES[as_of]
    return oh.parse_weekly_xlsx((FIXTURES / name).read_bytes(), as_of, kind)


@pytest.fixture(scope="module")
def weekly2022():
    return {as_of: _weekly(as_of) for as_of in WEEKLY_FIXTURES}


def test_pinned_releases_match_the_fixtures():
    """The table in oh.py and the fixtures here describe the same four files."""
    releases = oh.WEEKLY_RELEASES[2022]
    assert [r.as_of for r in releases] == sorted(WEEKLY_FIXTURES)
    assert {r.as_of: r.kind for r in releases} == {d: k for d, (_, k) in WEEKLY_FIXTURES.items()}
    assert releases[0].stamp_header == oh.LIVE_STAMP_HEADER
    for release in releases[1:]:
        assert release.url.startswith("https://web.archive.org/web/")
        assert "id_/https://www.ohiosos.gov/" in release.url      # original bytes
        assert release.stamp_header == oh.ARCHIVE_STAMP_HEADER
    assert set(oh.WEEKLY_RELEASES) == {2022}


def test_weekly_requests_only_file_is_a_statewide_row(weekly2022):
    """Oct 12: UOCAVA + non-UOCAVA applications -> mail requested, and nothing
    else. Its third column is UOCAVA returns only and must not be read as mail
    returned; and the county table has no requested column, so no county rows."""
    result = weekly2022[date(2022, 10, 12)]
    (state,) = result.state_rows
    assert state.day == date(2022, 10, 12)
    assert state.mail_requested == 4938 + 807262 == 812200
    assert state.ballots_total is None and state.mail_returned is None and state.inperson is None
    assert result.county_rows == []


def test_weekly_requests_plus_eip_file(weekly2022):
    """Oct 25: requests INCLUDE early in-person, so mail requested is the
    difference -- the same quantity the workbook's "transmitted by mail" and
    the ledger's SENT_NO_EIP measure. Returns are not in this file."""
    result = weekly2022[date(2022, 10, 25)]
    (state,) = result.state_rows
    assert state.mail_requested == 1076049 - 135889
    assert state.inperson == 135889
    assert state.ballots_total is None and state.mail_returned is None
    cuyahoga = {r.county_fips: r for r in result.county_rows}["39035"]
    assert cuyahoga.inperson == 3708
    assert cuyahoga.ballots_total is None and cuyahoga.mail_returned is None
    assert len(result.county_rows) == 88


def test_weekly_full_files(weekly2022):
    """Nov 1 and Nov 7: requests, EIP, returned (incl. EIP); mail returned is
    the difference. The Nov 7 file is "through 2 pm" and is dated the 7th."""
    nov1 = weekly2022[date(2022, 11, 1)]
    (state,) = nov1.state_rows
    assert (state.ballots_total, state.inperson) == (817644, 265062)
    assert state.mail_returned == 817644 - 265062
    assert state.mail_requested == 1243505 - 265062
    cuyahoga = {r.county_fips: r for r in nov1.county_rows}["39035"]
    assert (cuyahoga.ballots_total, cuyahoga.inperson, cuyahoga.mail_returned) == (90422, 8726, 90422 - 8726)

    nov7 = weekly2022[date(2022, 11, 7)]
    (state,) = nov7.state_rows
    assert state.day == date(2022, 11, 7)
    assert (state.ballots_total, state.inperson) == (1392767, 549771)
    assert state.mail_returned == 1392767 - 549771
    assert state.mail_requested == 1550440 - 549771
    cuyahoga = {r.county_fips: r for r in nov7.county_rows}["39035"]
    assert (cuyahoga.ballots_total, cuyahoga.inperson) == (145216, 19927)
    assert len(nov7.county_rows) == 88


def test_weekly_full_file_reads_the_2022_block_not_the_2018_comparison(weekly2022):
    """The sheet repeats the same three headers to the right for 2018. The
    first occurrence is 2022; the 2018 TOTAL requested is 1,211,557."""
    (state,) = weekly2022[date(2022, 11, 1)].state_rows
    assert state.mail_requested + state.inperson == 1243505 != 1211557


def test_weekly_series_is_consistent_with_the_certified_final(weekly2022, gen2022):
    """Mail requested must be mail-only, or the series would fall by half a
    million between Nov 7 and the certified Nov 8 row."""
    (final,) = gen2022.state_rows
    (nov7,) = weekly2022[date(2022, 11, 7)].state_rows
    assert nov7.mail_requested <= final.mail_requested == 1002020
    assert nov7.ballots_total <= final.ballots_total
    assert nov7.inperson <= final.inperson
    days = sorted(weekly2022)
    requested = [weekly2022[d].state_rows[0].mail_requested for d in days]
    assert requested == sorted(requested)


def test_weekly_shape_is_detected_by_header_names():
    """Each file carries exactly its own shape's headers; asking for another
    shape is drift, not a best-effort mapping."""
    for as_of, (name, kind) in WEEKLY_FIXTURES.items():
        body = (FIXTURES / name).read_bytes()
        for other in oh.WEEKLY_SHAPES:
            if other == kind:
                continue
            with pytest.raises(SchemaDrift, match="no header row"):
                oh.parse_weekly_xlsx(body, as_of, other)
    with pytest.raises(ValueError):
        oh.parse_weekly_xlsx(b"PK", date(2022, 10, 12), "something")


def test_weekly_county_sum_must_equal_total(tmp_path):
    """TOTAL is Ohio's own row and is published as the statewide number; a
    county edit that no longer sums to it is drift."""
    import openpyxl

    book = openpyxl.load_workbook(FIXTURES / "weekly_2022-10-25_absentee_survey_3.xlsx")
    sheet = book.worksheets[0]
    assert sheet.cell(row=3, column=1).value == "Adams"
    sheet.cell(row=3, column=3).value = 572             # was 571
    path = tmp_path / "off-by-one.xlsx"
    book.save(path)
    with pytest.raises(SchemaDrift, match="sum to 135890 .* against a TOTAL of 135889"):
        oh.parse_weekly_xlsx(path.read_bytes(), date(2022, 10, 25), oh.WEEKLY_REQUESTS_EIP)


def test_weekly_unknown_county_is_drift(tmp_path):
    import openpyxl

    book = openpyxl.load_workbook(FIXTURES / "weekly_2022-10-25_absentee_survey_3.xlsx")
    book.worksheets[0].cell(row=3, column=1).value = "Atlantis"
    path = tmp_path / "unknown.xlsx"
    book.save(path)
    with pytest.raises(SchemaDrift, match="unrecognised county"):
        oh.parse_weekly_xlsx(path.read_bytes(), date(2022, 10, 25), oh.WEEKLY_REQUESTS_EIP)


def test_weekly_rows_never_carry_a_party(weekly2022):
    for result in weekly2022.values():
        for row in result.state_rows + result.county_rows:
            for field in ("party_dem", "party_rep", "party_oth", "party_npa"):
                assert getattr(row, field) is None


def test_release_date_must_agree_with_the_files_write_date():
    """A capture written days from the release is some other attachment. The
    Nov 7 file was written after midnight UTC on the 8th, which is within the
    day's tolerance; two days is not; no date at all is not."""
    nov7 = oh.WEEKLY_RELEASES[2022][3]
    assert nov7.as_of == date(2022, 11, 7)
    oh.check_release_date(date(2022, 11, 8), nov7)
    oh.check_release_date(date(2022, 11, 7), nov7)
    with pytest.raises(SchemaDrift, match="not within a day"):
        oh.check_release_date(date(2022, 11, 9), nov7)
    with pytest.raises(SchemaDrift, match="not within a day"):
        oh.check_release_date(date(2022, 10, 25), nov7)
    with pytest.raises(SchemaDrift, match="undated"):
        oh.check_release_date(None, nov7)


def test_http_date_parses_both_header_spellings():
    assert oh._http_date("Wed, 12 Oct 2022 19:48:14 GMT") == date(2022, 10, 12)
    assert oh._http_date("Tue, 08 Nov 2022 00:26:00 GMT") == date(2022, 11, 8)
    assert oh._http_date(None) is None
    assert oh._http_date("not a date") is None


# ==========================================================================
# fetch_history: the certified final plus the dated series, combined
# ==========================================================================
@pytest.fixture
def weekly_downloads(monkeypatch):
    """`download_dated` answering each pinned URL with its fixture, dated as the
    live/archived headers really are."""
    written = {
        date(2022, 10, 12): date(2022, 10, 12),
        date(2022, 10, 25): date(2022, 10, 26),
        date(2022, 11, 1): date(2022, 11, 1),
        date(2022, 11, 7): date(2022, 11, 8),
    }
    by_url = {r.url: r for r in oh.WEEKLY_RELEASES[2022]}

    def fake(url, **kwargs):
        release = by_url[url]
        name, _ = WEEKLY_FIXTURES[release.as_of]
        return (FIXTURES / name).read_bytes(), written[release.as_of]

    monkeypatch.setattr(oh, "download_dated", fake)


def test_history_2022_is_the_final_plus_four_weekly_snapshots(monkeypatch, weekly_downloads):
    body = (FIXTURES / "2022gen_absentee_report_web.xlsx").read_bytes()
    monkeypatch.setattr(oh, "download", lambda url, **kw: body)
    monkeypatch.setattr(oh, "fetch_dashboard", lambda: pytest.fail("queried"))
    result = oh.OHScraper().fetch_history(2022)
    days = sorted(r.day for r in result.state_rows)
    assert days == [date(2022, 10, 12), date(2022, 10, 25), date(2022, 11, 1),
                    date(2022, 11, 7), date(2022, 11, 8)]
    by_day = {r.day: r for r in result.state_rows}
    assert by_day[date(2022, 11, 8)].ballots_total == 1473983        # the workbook, untouched
    assert by_day[date(2022, 11, 7)].ballots_total == 1392767
    # 10-25, 11-01 and 11-07 carry all 88 counties; the workbook fixture is
    # truncated to a handful, so its own count is what the final contributes.
    final_counties = len(oh.parse(body, 2022).county_rows)
    assert len(result.county_rows) == 88 * 3 + final_counties
    keys = [r.key() for r in result.county_rows]
    assert len(keys) == len(set(keys))


def test_history_2022_skips_a_capture_dated_away_from_its_release(monkeypatch, weekly_downloads, caplog):
    """One wrong capture costs that week, not the series."""
    body = (FIXTURES / "2022gen_absentee_report_web.xlsx").read_bytes()
    monkeypatch.setattr(oh, "download", lambda url, **kw: body)
    real = oh.download_dated
    nov1 = oh.WEEKLY_RELEASES[2022][2]

    def drifted(url, **kwargs):
        content, written = real(url, **kwargs)
        return content, (date(2022, 11, 4) if url == nov1.url else written)

    monkeypatch.setattr(oh, "download_dated", drifted)
    with caplog.at_level("WARNING"):
        result = oh.OHScraper().fetch_history(2022)
    assert date(2022, 11, 1) not in {r.day for r in result.state_rows}
    assert len(result.state_rows) == 4
    assert any("not within a day" in r.getMessage() for r in caplog.records)


@pytest.fixture
def ledger_queries(monkeypatch, dashboard, ledger):
    monkeypatch.setattr(oh, "fetch_dashboard", lambda: dashboard)
    seen = []

    def fake_ledger(label, cycle, **kwargs):
        seen.append((label, cycle))
        return ledger

    monkeypatch.setattr(oh, "fetch_ledger", fake_ledger)
    return seen


def test_history_2024_is_the_final_plus_the_daily_ledger(monkeypatch, ledger_queries):
    body = (FIXTURES / "2024gen_absentee_report_web.xlsx").read_bytes()
    monkeypatch.setattr(oh, "download", lambda url, **kw: body)
    result = oh.OHScraper().fetch_history(2024)
    assert ledger_queries == [("2024 NOV GENERAL", 2024)]      # the table's label, from the live query
    assert len(result.state_rows) == 45 + 1
    by_day = {r.day: r for r in result.state_rows}
    assert by_day[date(2024, 11, 5)].ballots_total == 2620750        # certified workbook
    assert by_day[date(2024, 11, 4)].ballots_total == 2566824        # ledger
    assert by_day[date(2024, 11, 5)].mail_requested == 1153568
    keys = [r.key() for r in result.state_rows] + [r.key() for r in result.county_rows]
    assert len(keys) == len(set(keys))
    final_counties = len(oh.parse(body, 2024).county_rows)      # the fixture is truncated
    assert len(result.county_rows) == 88 * 45 + final_counties


def test_history_2024_series_survives_a_blocked_workbook_route(monkeypatch, ledger_queries, caplog):
    """A 403 on ohiosos.gov -- the usual state of a stock runner -- must not
    cost the 45 ledger days. The problem is logged, the series is returned."""
    def blocked(url, **kwargs):
        raise SourceError(f"OH: {url} returned HTTP 403")

    monkeypatch.setattr(oh, "download", blocked)
    with caplog.at_level("WARNING"):
        result = oh.OHScraper().fetch_history(2024)
    assert len(result.state_rows) == 45
    assert date(2024, 11, 5) not in {r.day for r in result.state_rows}
    assert any("history is partial" in r.getMessage() for r in caplog.records)


def test_history_2024_workbook_survives_a_dead_ledger(monkeypatch, dashboard):
    body = (FIXTURES / "2024gen_absentee_report_web.xlsx").read_bytes()
    monkeypatch.setattr(oh, "download", lambda url, **kw: body)
    monkeypatch.setattr(oh, "fetch_dashboard", lambda: dashboard)
    monkeypatch.setattr(
        oh, "fetch_ledger",
        lambda *a, **k: (_ for _ in ()).throw(SourceError("OH: ledger HTTP 500")),
    )
    (state,) = oh.OHScraper().fetch_history(2024).state_rows
    assert state.day == date(2024, 11, 5) and state.ballots_total == 2620750


def test_history_2024_with_nothing_at_all_is_a_source_error(monkeypatch, dashboard):
    """Blocked workbook plus a dead ledger: we could not look, so fall through."""
    def blocked(url, **kwargs):
        raise SourceError(f"OH: {url} returned HTTP 403")

    monkeypatch.setattr(oh, "download", blocked)
    monkeypatch.setattr(oh, "fetch_dashboard", lambda: dashboard)
    monkeypatch.setattr(
        oh, "fetch_ledger",
        lambda *a, **k: (_ for _ in ()).throw(SourceError("OH: ledger HTTP 500")),
    )
    with pytest.raises(SourceError) as caught:
        oh.OHScraper().fetch_history(2024)
    assert not isinstance(caught.value, NotYetPublished)
