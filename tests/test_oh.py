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
    with pytest.raises(SourceError) as caught:
        oh.OHScraper().fetch_history(2022)
    assert not isinstance(caught.value, NotYetPublished)


def test_history_for_an_election_not_in_the_index_is_not_yet_published(monkeypatch):
    """A 404, unlike a 403, really does mean the workbook is not there."""
    def missing(url, **kwargs):
        raise Missing(f"OH: {url} returned 404")

    monkeypatch.setattr(oh, "download", missing)
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
    """Backfill anchors the 2022/2024 comparison lines, so it must use the
    certified workbook -- mixing in the tracker's smaller numbers would make the
    comparison read as a collapse in turnout."""
    body = (FIXTURES / "2022gen_absentee_report_web.xlsx").read_bytes()
    monkeypatch.setattr(oh, "download", lambda url, **kw: body)
    monkeypatch.setattr(
        oh, "fetch_dashboard",
        lambda: pytest.fail("fetch_history must never query the dashboard"),
    )
    (state,) = oh.OHScraper().fetch_history(2022).state_rows
    assert state.ballots_total == 1473983


def test_history_for_the_current_cycle_is_not_yet_published():
    with pytest.raises(NotYetPublished):
        oh.OHScraper().fetch_history(date.today().year)


def test_adapter_identity():
    scraper = oh.OHScraper()
    assert (scraper.state, scraper.name, scraper.tier) == ("OH", "oh-sos", 1)
