"""Virginia: ELECT's absentee export, by locality.

Two fixtures, both real ELECT output. The 2024 one is downsampled to at most
three verbatim rows per locality -- one in-person, one returned mail ballot, one
still outstanding -- so that all 133 localities and all ten application types
survive in 45KB. The 2022 one keeps only twelve localities on purpose: it is the
partial-export case, and Virginia's four city/county name twins are all in it.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from ev.adapters import va
from ev.adapters._net import Missing
from ev.adapters.base import NotYetPublished, SchemaDrift, SourceError
from ev.schema import county_row_to_dict, state_row_to_dict
from ev.adapters.base import FetchResult
from ev.schema import Provenance, TIER_SCRAPER

FIXTURES = Path(__file__).parent / "fixtures" / "va"
FULL = FIXTURES / "2024NovemberGeneral_EnrAbsenteeRawCSV.csv"
PARTIAL = FIXTURES / "2022-November-General_Absentee_partial.csv"


@pytest.fixture(scope="module")
def gen2024():
    return va.parse(FULL.read_bytes(), 2024, date(2024, 11, 5))


@pytest.fixture(scope="module")
def gen2022_partial():
    return va.parse(PARTIAL.read_bytes(), 2022, date(2022, 11, 8))


# --------------------------------------------------------------------------
# THE BLANK RULE -- the one that matters most for this batch
# --------------------------------------------------------------------------
def test_no_party_field_is_ever_populated(gen2024, gen2022_partial):
    """Virginia has no party registration. A 0 here would render as 'zero
    Democrats have voted' on a page whose whole subject is who is voting."""
    for result in (gen2024, gen2022_partial):
        for row in result.state_rows + result.county_rows:
            for field in ("party_dem", "party_rep", "party_oth", "party_npa"):
                assert getattr(row, field) is None, (field, row)


def test_party_fields_write_as_blank_cells_not_zeros(gen2024):
    """The None has to survive all the way to the CSV the website reads."""
    prov = Provenance(tier=TIER_SCRAPER, name="va-elect")
    state = state_row_to_dict(FetchResult(state_rows=list(gen2024.state_rows))
                              .stamp(prov).state_rows[0])
    county = county_row_to_dict(FetchResult(county_rows=[gen2024.county_rows[0]])
                                .stamp(prov).county_rows[0])
    for cells in (state, county):
        for field in ("party_dem", "party_rep", "party_oth", "party_npa"):
            assert cells[field] == "", (field, cells)


# --------------------------------------------------------------------------
# Independent cities: the trap this adapter exists to avoid
# --------------------------------------------------------------------------
def test_independent_cities_are_not_their_namesake_counties(gen2024):
    """Fairfax city is 51600 and Fairfax County is 51059; the same trap is set
    for Richmond, Franklin and Roanoke. Both must appear, with their own
    numbers, and neither may absorb the other."""
    by_fips = {row.county_fips: row for row in gen2024.county_rows}
    twins = {
        "51600": "Fairfax city", "51059": "Fairfax County",
        "51760": "Richmond city", "51159": "Richmond County",
        "51620": "Franklin city", "51067": "Franklin County",
        "51770": "Roanoke city", "51161": "Roanoke County",
    }
    for fips, name in twins.items():
        assert fips in by_fips, f"{name} ({fips}) missing"
        assert by_fips[fips].county_name == name

    # And they really are distinct places, not the same row emitted twice.
    assert by_fips["51059"].ballots_total != by_fips["51600"].ballots_total
    assert by_fips["51760"].ballots_total != by_fips["51159"].ballots_total


def test_a_bare_ambiguous_locality_is_drift_not_a_guess():
    """If ELECT ever dropped the qualifying word we must refuse the row rather
    than credit a city's ballots to the county that surrounds it."""
    header = FULL.read_text(encoding="utf-8").splitlines()[0]
    body = "\n".join([
        header,
        '"FAIRFAX","11","41 to 60","F","NE - No Excuse","In Person","In Person","10"',
    ]).encode()
    with pytest.raises(SchemaDrift, match="unresolved localities"):
        va.parse(body, 2024, date(2024, 11, 5))


def test_ampersand_spelling_is_translated_not_guessed(gen2024):
    """ELECT writes "KING & QUEEN COUNTY"; the census writes the word out. That
    is a spelling, not an ambiguity, and it is the only one we translate."""
    assert "51097" in {row.county_fips for row in gen2024.county_rows}
    assert set(va.LOCALITY_SPELLINGS) == {"king & queen county"}


def test_every_virginia_locality_is_present_and_fips_keyed(gen2024):
    """95 counties plus 38 independent cities."""
    assert len(gen2024.county_rows) == 133 == va.EXPECTED_LOCALITIES
    for row in gen2024.county_rows:
        assert len(row.county_fips) == 5 and row.county_fips.startswith("51")
    assert len({row.county_fips for row in gen2024.county_rows}) == 133


# --------------------------------------------------------------------------
# Method comes from ApplicationType
# --------------------------------------------------------------------------
def test_in_person_and_mail_are_split_on_the_application_column(gen2024):
    fairfax = next(r for r in gen2024.county_rows if r.county_fips == "51059")
    assert fairfax.inperson is not None and fairfax.mail_returned is not None
    assert fairfax.ballots_total == fairfax.inperson + fairfax.mail_returned


def test_outstanding_mail_ballots_are_requested_but_not_returned(gen2024):
    """A blank ReceiptType is a ballot issued and not yet back. It must raise
    mail_requested without raising mail_returned or the total."""
    (state,) = gen2024.state_rows
    assert state.mail_requested > state.mail_returned
    assert state.ballots_total == state.inperson + state.mail_returned


def test_statewide_row_totals_the_localities(gen2024):
    (state,) = gen2024.state_rows
    assert state.state == "VA" and state.day == date(2024, 11, 5)
    assert state.ballots_total == sum(r.ballots_total for r in gen2024.county_rows)
    assert state.inperson == sum(r.inperson or 0 for r in gen2024.county_rows)


def test_an_unknown_application_type_is_drift_not_bucketed_as_mail():
    header = FULL.read_text(encoding="utf-8").splitlines()[0]
    body = "\n".join([
        header,
        '"ARLINGTON COUNTY","8","41 to 60","F","NE - No Excuse","Kiosk Application","Mail","10"',
    ]).encode()
    with pytest.raises(SchemaDrift, match="unknown application types"):
        va.parse(body, 2024, date(2024, 11, 5))


def test_an_unknown_receipt_type_is_drift():
    header = FULL.read_text(encoding="utf-8").splitlines()[0]
    body = "\n".join([
        header,
        '"ARLINGTON COUNTY","8","41 to 60","F","NE - No Excuse","Virginia Specific '
        'Application - SBE 701","Carrier Pigeon","10"',
    ]).encode()
    with pytest.raises(SchemaDrift, match="unknown receipt types"):
        va.parse(body, 2024, date(2024, 11, 5))


def test_the_en_dash_in_the_fpca_label_is_folded(gen2024):
    """ELECT writes "Federal Post Cards Application – FPCA" with an en dash."""
    assert va._key("Federal Post Cards Application – FPCA") in va.MAIL_APPLICATIONS
    assert va._key("Federal Post Cards Application - FPCA") in va.MAIL_APPLICATIONS


# --------------------------------------------------------------------------
# A partial export is counties only -- never a Virginia total
# --------------------------------------------------------------------------
def test_a_partial_export_publishes_no_statewide_row(gen2022_partial):
    """ELECT publishes no TOTAL row of its own, so a statewide figure can only
    be our sum -- and a sum of twelve localities is not Virginia."""
    assert gen2022_partial.state_rows == []
    assert len(gen2022_partial.county_rows) == 12


def test_the_2022_layout_parses_with_the_same_code(gen2022_partial):
    by_fips = {row.county_fips: row for row in gen2022_partial.county_rows}
    assert by_fips["51600"].county_name == "Fairfax city"
    assert by_fips["51059"].county_name == "Fairfax County"
    assert all(r.inperson is not None or r.mail_returned is not None
               for r in gen2022_partial.county_rows)


# --------------------------------------------------------------------------
# Drift and absence
# --------------------------------------------------------------------------
def test_renamed_column_raises_drift():
    body = b'"LocalityName","CongressionalDistrict","AgeRange","Gender","Reason",' \
           b'"HowTheyApplied","ReceiptType","TheCount"\n'
    with pytest.raises(SchemaDrift, match="missing columns"):
        va.parse(body, 2026, date(2026, 11, 3))


def test_an_html_page_instead_of_the_csv_is_a_source_error():
    with pytest.raises(SourceError):
        va.parse(b"<!DOCTYPE html><html>nope</html>", 2026, date(2026, 11, 3))


def _feed(elections, *, reports=None, blob_body=b""):
    """A fake `get` that plays ELECT's three endpoints back to the adapter."""
    def fake_get(url, **kwargs):
        if url == va.JURISDICTION_URL:
            return json.dumps({
                "id": "d2c804ee-4ec2-46bb-91d7-5b41526eab03",
                "elections": elections,
            }).encode()
        if url.startswith(va.ENR + "/results/public/api/elections/"):
            return json.dumps({
                "asOf": "2026-10-20T09:00:00Z",
                "publicReportCategories": reports or [],
            }).encode()
        if not blob_body:
            raise Missing(f"VA: {url} returned 404")
        return blob_body
    return fake_get


ABSENTEE_CATEGORY = [{
    "categoryName": "Other Reports",
    "reports": [{"reportName": "EnrAbsenteeRawCSV",
                 "blobName": "EnrAbsenteeRawCSV_deadbeef.csv"}],
}]


def test_a_cycle_with_no_election_yet_is_not_yet_published(monkeypatch):
    """This is the path that runs every day until ELECT stands the general up --
    exactly where Virginia sits today. It must STOP the ladder, not fall through
    to a source that invents a zero."""
    monkeypatch.setattr(va, "get", _feed([
        {"publicElectionId": "2026-August-Democratic-Primary",
         "electionDate": "2026-08-04"},
    ]))
    with pytest.raises(NotYetPublished, match="has not stood up"):
        va.VAScraper().fetch(2026, date(2026, 9, 5))


def test_an_election_without_an_absentee_report_is_not_yet_published(monkeypatch):
    """The election exists, early voting has not produced a file yet."""
    monkeypatch.setattr(va, "get", _feed(
        [{"publicElectionId": "2026-November-General", "electionDate": "2026-11-03"}],
        reports=[{"categoryName": "Other Reports", "reports": []}],
    ))
    with pytest.raises(NotYetPublished, match="no absentee export"):
        va.VAScraper().fetch(2026, date(2026, 9, 5))


def test_a_missing_blob_is_not_yet_published(monkeypatch):
    monkeypatch.setattr(va, "get", _feed(
        [{"publicElectionId": "2026-November-General", "electionDate": "2026-11-03"}],
        reports=ABSENTEE_CATEGORY,
    ))
    with pytest.raises(NotYetPublished, match="not posted yet"):
        va.VAScraper().fetch(2026, date(2026, 9, 5))


def test_two_elections_on_the_same_general_date_is_drift(monkeypatch):
    monkeypatch.setattr(va, "get", _feed([
        {"publicElectionId": "2026-November-General", "electionDate": "2026-11-03"},
        {"publicElectionId": "2026-November-General-Copy", "electionDate": "2026-11-03"},
    ]))
    with pytest.raises(SchemaDrift, match="matches 2 elections"):
        va.VAScraper().fetch(2026, date(2026, 11, 3))


def test_the_published_date_dates_the_rows_and_never_runs_ahead(monkeypatch):
    monkeypatch.setattr(va, "get", _feed(
        [{"publicElectionId": "2026-November-General", "electionDate": "2026-11-03"}],
        reports=ABSENTEE_CATEGORY, blob_body=FULL.read_bytes(),
    ))
    result = va.VAScraper().fetch(2026, date(2026, 10, 25))
    # ELECT says it refreshed on the 20th; that is the honest as-of date.
    assert {r.day for r in result.state_rows} == {date(2026, 10, 20)}
    # A file stamped after the run is clamped rather than dated into the future.
    early = va.VAScraper().fetch(2026, date(2026, 10, 10))
    assert {r.day for r in early.state_rows} == {date(2026, 10, 10)}


def test_history_for_the_live_cycle_is_refused():
    with pytest.raises(NotYetPublished, match="not an archived cycle"):
        va.VAScraper().fetch_history(date.today().year)


def test_the_legacy_archive_is_only_claimed_for_pre_enr_cycles():
    assert set(va.LEGACY_ELECTIONS) == {2022}


def test_published_date_reads_the_election_metadata():
    assert va.published_date({"asOf": "2024-10-21T13:05:00.123Z"}) == date(2024, 10, 21)
    assert va.published_date({"lastUpdated": "2024-10-20T01:00:00"}) == date(2024, 10, 20)
    assert va.published_date({}) is None


def test_adapter_identity():
    scraper = va.VAScraper()
    assert (scraper.state, scraper.name, scraper.tier) == ("VA", "va-elect", 1)


# --------------------------------------------------------------------------
# Congressional districts: ELECT labels every row with one
# --------------------------------------------------------------------------

def test_district_rows_are_reported_and_sum_to_the_state(gen2024):
    from ev.adapters import _districts
    rows = _districts.rows_of(gen2024)
    assert {r.cd_code for r in rows} == {f"VA-{n:02d}" for n in range(1, 12)}
    assert all(r.basis == "reported" for r in rows)
    (state,) = gen2024.state_rows
    assert sum(r.ballots_total for r in rows) == state.ballots_total
    assert sum(r.inperson for r in rows) == state.inperson
    assert sum(r.mail_returned for r in rows) == state.mail_returned
    assert all(r.party_dem is None for r in rows), "Virginia has no party registration"


def test_a_partial_export_publishes_no_district_rows_either(gen2022_partial):
    from ev.adapters import _districts
    assert gen2022_partial.state_rows == []
    assert _districts.rows_of(gen2022_partial) == []


def test_an_unreadable_district_is_drift():
    text = FULL.read_text(encoding="utf-8-sig").replace('"ACCOMACK COUNTY","2"', '"ACCOMACK COUNTY","99"', 1)
    with pytest.raises(SchemaDrift):
        va.parse(text.encode("utf-8"), 2024, date(2024, 11, 5))


def test_an_unassigned_district_counts_for_the_locality_and_no_district(gen2024):
    """ELECT writes district "0" on a ballot no district could claim.

    The certified 2024 file carries five such rows, all Harrisonburg City. They
    are real ballots in the locality and state totals; they are simply not in
    any VA-NN row. Refusing the file over them is what broke the 2024 backfill.
    """
    from ev.adapters import _districts
    text = FULL.read_text(encoding="utf-8-sig").replace('"ACCOMACK COUNTY","2"', '"ACCOMACK COUNTY","0"', 1)
    result = va.parse(text.encode("utf-8"), 2024, date(2024, 11, 5))
    (state,), (before,) = result.state_rows, gen2024.state_rows
    assert state.ballots_total == before.ballots_total, "the ballot still counts for the state"
    rows = _districts.rows_of(result)
    assert {r.cd_code for r in rows} == {f"VA-{n:02d}" for n in range(1, 12)}, "no VA-00 row"
    assert sum(r.ballots_total for r in rows) < state.ballots_total, "and it is in no district"


# --------------------------------------------------------------------------
# History: the certified final plus, for 2022, the archived curve
# --------------------------------------------------------------------------
# The third fixture is the Internet Archive's 2022-11-02 capture of the retired
# results site's Absentee.csv (x-archive-orig-last-modified 2022-11-02 16:11Z),
# cut to four localities so the Fairfax County / Fairfax city pair survives
# whole. Truncated it sums to in-person 54,284 + mail 38,817 = 93,101 ballots;
# the full capture reads 473,518 + 225,294 = 698,812 across all 133 localities.
ARCHIVED = FIXTURES / "2022-November-General_Absentee_wayback-20221102_partial.csv"

ELECTION_DAY_2022 = date(2022, 11, 8)


@pytest.fixture(scope="module")
def capture_2022():
    return va.parse(ARCHIVED.read_bytes(), 2022, date(2022, 11, 2))


def test_the_archived_2022_capture_parses_through_the_live_parser(capture_2022):
    """Same eight columns, same method rules, same city/county split. The
    numbers are the ones ELECT had published five days out."""
    by_fips = {r.county_fips: r for r in capture_2022.county_rows}
    assert (by_fips["51059"].inperson, by_fips["51059"].mail_returned) == (51375, 37731)
    assert (by_fips["51600"].inperson, by_fips["51600"].mail_returned) == (1919, 877)
    assert by_fips["51097"].county_name == "King and Queen County"
    assert sum(r.inperson for r in capture_2022.county_rows) == 54284
    assert sum(r.mail_returned for r in capture_2022.county_rows) == 38817
    assert sum(r.ballots_total for r in capture_2022.county_rows) == 93101
    assert {r.day for r in capture_2022.county_rows} == {date(2022, 11, 2)}
    # Four localities is a partial export: counties only, no Virginia total.
    assert capture_2022.state_rows == []
    assert all(r.party_dem is None for r in capture_2022.county_rows)


def test_last_modified_reads_the_archive_stamp_and_refuses_junk():
    assert va.last_modified("Wed, 02 Nov 2022 16:11:01 GMT") == date(2022, 11, 2)
    assert va.last_modified("Mon, 07 Nov 2022 23:59:59 GMT") == date(2022, 11, 7)
    assert va.last_modified("2022-11-02") is None
    assert va.last_modified("") is None and va.last_modified(None) is None


def _captures(monkeypatch, feed):
    """Play a set of archived captures back to `archived_days`.

    `feed` maps a Wayback timestamp to (bytes, written date | None); the CDX
    listing is derived from its keys so the walk sees them oldest first.
    """
    monkeypatch.setattr(va, "_archive_captures", lambda cycle: [
        (stamp, va.LEGACY_URL.format(election="2022%20November%20General"))
        for stamp in sorted(feed)
    ])
    monkeypatch.setattr(va, "_archive_get", lambda stamp, original: feed[stamp])


def test_archived_days_are_dated_by_elect_not_by_the_crawl(monkeypatch):
    """The crawl ran on 2022-11-02 at 20:44Z; ELECT wrote the file at 16:11Z
    the same day -- and on another capture, the crawl is a day later than the
    write. The write date is the row's date."""
    _captures(monkeypatch, {
        "20221102204427": (b"nov2", date(2022, 11, 2)),
        "20221108010000": (b"nov7", date(2022, 11, 7)),   # crawled on the 8th
    })
    assert va.archived_days(2022) == {date(2022, 11, 2): b"nov2",
                                      date(2022, 11, 7): b"nov7"}


def test_captures_on_or_after_election_day_are_cut(monkeypatch):
    """The final is already published on Election Day from the live file; the
    post-election rewrites (Nov 15, Nov 30, the following May) are late fills,
    not the curve. None of them may reach the output, and an archived capture
    written ON Election Day must not replace the live final either."""
    _captures(monkeypatch, {
        "20221102204427": (b"nov2", date(2022, 11, 2)),
        "20221108120000": (b"eday", ELECTION_DAY_2022),
        "20221115125251": (b"nov15", date(2022, 11, 15)),
        "20221130002252": (b"nov30", date(2022, 11, 30)),
        "20230517135216": (b"may", date(2023, 5, 17)),
    })
    assert va.archived_days(2022) == {date(2022, 11, 2): b"nov2"}


def test_the_last_capture_of_a_day_wins(monkeypatch):
    _captures(monkeypatch, {
        "20221102090000": (b"morning", date(2022, 11, 2)),
        "20221102204427": (b"evening", date(2022, 11, 2)),
    })
    assert va.archived_days(2022) == {date(2022, 11, 2): b"evening"}


def test_an_undatable_capture_is_skipped_not_dated_by_its_crawl(monkeypatch, caplog):
    _captures(monkeypatch, {
        "20221102204427": (b"nov2", date(2022, 11, 2)),
        "20221105000000": (b"mystery", None),
    })
    with caplog.at_level("WARNING", logger="ev.adapters.va"):
        assert va.archived_days(2022) == {date(2022, 11, 2): b"nov2"}
    assert "crawl time is not an as-of date" in caplog.text


def test_a_capture_long_before_the_window_is_skipped(monkeypatch):
    """A crawl of last cycle's leftover file, stamped a year early."""
    _captures(monkeypatch, {
        "20220101000000": (b"stale", date(2021, 11, 10)),
        "20221102204427": (b"nov2", date(2022, 11, 2)),
    })
    assert va.archived_days(2022) == {date(2022, 11, 2): b"nov2"}


def test_an_unusable_capture_falls_out_of_the_walk(monkeypatch):
    """A capture the Archive cannot serve is one less day, not a dead walk."""
    monkeypatch.setattr(va, "_archive_captures", lambda cycle: [
        ("20221101000000", "x"), ("20221102204427", "x")])

    def flaky(stamp, original):
        if stamp == "20221101000000":
            raise SourceError("VA: replay returned HTTP 503")
        return b"nov2", date(2022, 11, 2)
    monkeypatch.setattr(va, "_archive_get", flaky)
    assert va.archived_days(2022) == {date(2022, 11, 2): b"nov2"}


def test_a_rate_limited_replay_is_waited_out_then_given_up_on(monkeypatch, tmp_path):
    """web.archive.org answers 429 across everything one machine is walking.
    One capture is one DAY of the curve, so a bounded wait is worth it -- and
    bounded means a capture that never clears does not stall the backfill."""
    monkeypatch.setattr(va, "cache_path", lambda state, name: tmp_path / name)
    waits = []
    monkeypatch.setattr(va, "_sleep", waits.append)
    answers = []

    class Response:
        def __init__(self, status, body=b"", headers=None):
            self.status_code, self.content = status, body
            self.headers = headers or {}
    monkeypatch.setattr(va.SESSION, "get", lambda url, **kw: answers.pop(0))

    answers[:] = [Response(429, headers={"Retry-After": "7"}),
                  Response(200, FULL.read_bytes(),
                           {va.ARCHIVE_STAMP_HEADER: "Wed, 02 Nov 2022 16:11:01 GMT"})]
    body, written = va._archive_get("20221102204427", "https://x/Absentee.csv")
    assert written == date(2022, 11, 2) and body == FULL.read_bytes()
    assert waits == [7.0]
    # ...and it is now cached beside its stamp, so the next walk never asks.
    assert (tmp_path / "archive-20221102204427.csv.last-modified").read_text() \
        == "Wed, 02 Nov 2022 16:11:01 GMT"

    waits.clear()
    answers[:] = [Response(429, headers={"Retry-After": "nonsense"})] * 10
    with pytest.raises(SourceError, match="HTTP 429"):
        va._archive_get("20221107224812", "https://x/Absentee.csv")
    assert waits == [va.ARCHIVE_RETRY_CAP] * va.ARCHIVE_RETRIES
    assert len(answers) == 10 - va.ARCHIVE_RETRIES - 1


def test_an_undatable_replay_is_not_cached(monkeypatch, tmp_path):
    """A capture that arrives without its stamp is skipped today and asked
    for again next run, rather than frozen on disk as "undatable"."""
    monkeypatch.setattr(va, "cache_path", lambda state, name: tmp_path / name)

    class Response:
        status_code, content, headers = 200, FULL.read_bytes(), {}
    monkeypatch.setattr(va.SESSION, "get", lambda url, **kw: Response())
    body, written = va._archive_get("20221102204427", "https://x/Absentee.csv")
    assert written is None and body == FULL.read_bytes()
    assert list(tmp_path.iterdir()) == []

def test_2022_history_is_the_final_plus_the_archived_days(monkeypatch):
    """The live final dated Election Day, exactly as before, and the archived
    captures before it -- each through the same parser, locality + statewide +
    district rows -- in one result."""
    monkeypatch.setattr(va.VAScraper, "_load_legacy", lambda self, cycle: FULL.read_bytes())
    _captures(monkeypatch, {
        "20221102204427": (FULL.read_bytes(), date(2022, 11, 2)),
        "20221107224812": (FULL.read_bytes(), date(2022, 11, 7)),
        "20221115125251": (FULL.read_bytes(), date(2022, 11, 15)),
    })
    result = va.VAScraper().fetch_history(2022)
    from ev.adapters import _districts
    days = sorted({r.day for r in result.state_rows})
    assert days == [date(2022, 11, 2), date(2022, 11, 7), ELECTION_DAY_2022]
    assert len(result.county_rows) == 3 * 133
    assert sorted({r.day for r in _districts.rows_of(result)}) == days
    assert all(r.cycle == 2022 for r in result.state_rows + result.county_rows)


def test_2022_history_keeps_the_final_when_the_archive_is_down(monkeypatch, caplog):
    """The final is the load-bearing row. A CDX outage is a missing curve,
    logged, not a missing Virginia."""
    monkeypatch.setattr(va.VAScraper, "_load_legacy", lambda self, cycle: FULL.read_bytes())

    def down(cycle):
        raise SourceError("VA: the Wayback CDX index answered HTTP 503")
    monkeypatch.setattr(va, "_archive_captures", down)
    with caplog.at_level("WARNING", logger="ev.adapters.va"):
        result = va.VAScraper().fetch_history(2022)
    assert {r.day for r in result.state_rows} == {ELECTION_DAY_2022}
    assert "archive walk failed, final only" in caplog.text


def test_an_archived_capture_the_parser_refuses_is_skipped(monkeypatch):
    monkeypatch.setattr(va.VAScraper, "_load_legacy", lambda self, cycle: FULL.read_bytes())
    _captures(monkeypatch, {
        "20221102204427": (b"<!DOCTYPE html><html>Site under maintenance</html>",
                           date(2022, 11, 2)),
        "20221107224812": (FULL.read_bytes(), date(2022, 11, 7)),
    })
    result = va.VAScraper().fetch_history(2022)
    assert sorted({r.day for r in result.state_rows}) == [date(2022, 11, 7),
                                                          ELECTION_DAY_2022]


def test_2024_history_is_the_final_alone_and_never_walks_the_archive(monkeypatch):
    """ENR came up for 2024 and the Archive's first capture of its absentee
    blob is from election night: no pre-election state file exists. The 2024
    curve is the manual tier's (data/manual/2024_statewide.csv), so this must
    not touch the CDX index at all -- it is never a 2024 route."""
    monkeypatch.setattr(va.VAScraper, "_load_current",
                        lambda self, cycle, use_cache: (FULL.read_bytes(), None))

    def never(cycle):
        raise AssertionError("2024 must not walk the Wayback archive")
    monkeypatch.setattr(va, "_archive_captures", never)
    result = va.VAScraper().fetch_history(2024)
    assert {r.day for r in result.state_rows} == {date(2024, 11, 5)}
    assert 2024 not in va.LEGACY_ELECTIONS


def test_the_cdx_query_sweeps_the_legacy_url_by_prefix(monkeypatch):
    """A capture keyed under a query-string variant of the same file would be
    missed by an exact match, and the digest de-dup is what keeps the same
    bytes under two spellings from being walked twice."""
    seen = {}

    class Response:
        status_code = 200
        text = json.dumps([
            ["timestamp", "original", "digest"],
            ["20221102204427", "https://x/Absentee.csv", "AAA"],
            ["20221102204428", "https://x/Absentee.csv?nocache=1", "AAA"],
            ["20221107224812", "https://x/Absentee.csv", "BBB"],
        ])

    def fake_get(url, params=None, **kwargs):
        seen.update(params)
        return Response()
    monkeypatch.setattr(va.SESSION, "get", fake_get)
    captures = va._archive_captures(2022)
    assert captures == [("20221102204427", "https://x/Absentee.csv"),
                        ("20221107224812", "https://x/Absentee.csv")]
    assert seen["matchType"] == "prefix" and seen["filter"] == "statuscode:200"
    assert seen["url"].endswith("2022%20November%20General/Site/Statistics/Absentee.csv")
    assert seen["from"] <= "20221108" <= seen["to"]


# --------------------------------------------------------------------------
# The manual tier: VPAP's statewide daily series, vendored
# --------------------------------------------------------------------------
from ev.adapters.manual import ManualAdapter  # noqa: E402
from ev.schema import TIER_MANUAL  # noqa: E402


def _manual(cycle):
    return ManualAdapter(state="VA").fetch_history(cycle).state_rows


@pytest.mark.parametrize("cycle, first, election_day, final", [
    (2024, date(2024, 9, 20), date(2024, 11, 5), (2353062, 1862990, 490072)),
    (2022, date(2022, 9, 23), date(2022, 11, 8), (968163, 681657, 286506)),
])
def test_the_vendored_series_runs_from_the_first_day_to_election_day(
        cycle, first, election_day, final):
    """One row per calendar day of the window and not one past Election Day:
    the late-mail tail in VPAP's source (2022 to 11-14, 2024 to 11-10) is cut."""
    rows = _manual(cycle)
    days = [r.day for r in rows]
    assert days == sorted(days) and len(set(days)) == len(days)
    assert days[0] == first and days[-1] == election_day
    assert len(days) == (election_day - first).days + 1 == 47
    last = rows[-1]
    assert (last.ballots_total, last.inperson, last.mail_returned) == final


@pytest.mark.parametrize("cycle", [2024, 2022])
def test_the_vendored_series_is_cumulative_and_blank_where_it_should_be(cycle):
    rows = _manual(cycle)
    for prev, row in zip(rows, rows[1:]):
        assert row.ballots_total >= prev.ballots_total, row.day
        assert row.inperson >= prev.inperson and row.mail_returned >= prev.mail_returned
    for row in rows:
        assert row.ballots_total == row.inperson + row.mail_returned, row.day
        # VPAP reports no requests; Virginia has no party registration. Blank,
        # never 0 -- THE BLANK RULE.
        assert row.mail_requested is None
        assert (row.party_dem, row.party_rep, row.party_oth, row.party_npa) == (
            None, None, None, None)


def test_the_vendored_2024_series_hits_its_checkpoints():
    by_day = {r.day: r for r in _manual(2024)}
    oct11 = by_day[date(2024, 10, 11)]
    assert (oct11.ballots_total, oct11.inperson, oct11.mail_returned) == (
        716818, 501604, 215214)
    assert by_day[date(2024, 10, 30)].ballots_total == 1903252
    assert by_day[date(2024, 11, 4)].ballots_total == 2323133


def test_the_manual_files_say_where_the_numbers_came_from():
    import csv as _csv
    for cycle, asof in ((2024, "11/10/24"), (2022, "11/13/22")):
        with ManualAdapter(state="VA")._path(cycle).open(newline="") as fh:
            rows = [r for r in _csv.DictReader(fh) if r["state"] == "VA"]
        assert rows and all("VPAP" in r["note"] and asof in r["note"] for r in rows)
        assert all("Virginia Dept of Elections" in r["note"] for r in rows)


def test_the_manual_rung_is_on_virginias_history_ladder():
    """The series is only useful if backfill can reach it."""
    from ev.registry import history_ladder
    rungs = history_ladder("VA")
    assert isinstance(rungs[0], va.VAScraper)
    assert any(isinstance(a, ManualAdapter) for a in rungs)


def test_manual_history_reaches_backfill_and_never_displaces_the_states_final(
        tmp_path, monkeypatch):
    """End to end through `cmd_backfill` and `merge_rows`: the manual curve
    lands on disk at tier 5, and on Election Day -- the one date both sources
    answer for -- the tier-1 ELECT final already on disk is kept."""
    import csv as _csv
    from ev import cli, publish
    from ev.schema import Provenance, StateDay
    out = tmp_path / "output"
    final = StateDay(cycle=2024, state="VA", day=date(2024, 11, 5),
                     ballots_total=2376938, mail_requested=575846,
                     mail_returned=518217, inperson=1858721,
                     provenance=Provenance(tier=TIER_SCRAPER, name="va-elect"))
    publish.publish_state_daily(out, [final])

    monkeypatch.setattr(cli, "history_ladder",
                        lambda state: [ManualAdapter(state=state)])
    parsed = cli.build_parser().parse_args(
        ["--output", str(out), "backfill", "--cycle", "2024", "--state", "VA"])
    assert cli.cmd_backfill(parsed) == 0

    rows = {r["date"]: r for r in _csv.DictReader(
        (out / "ev_state_daily.csv").open()) if r["state"] == "VA"}
    assert len(rows) == 47
    eday = rows["2024-11-05"]
    assert (eday["source_tier"], eday["source_name"], eday["ballots_total"]) == (
        "1", "va-elect", "2376938")
    eve = rows["2024-11-04"]
    assert (eve["source_tier"], eve["source_name"]) == (str(TIER_MANUAL), "manual")
    assert (eve["ballots_total"], eve["inperson"], eve["mail_returned"]) == (
        "2323133", "1857722", "465411")
    assert eve["party_dem"] == "" and eve["mail_requested"] == ""


def test_a_cdx_row_with_a_malformed_stamp_is_skipped_not_written_under(monkeypatch):
    """The stamp is interpolated into cache/va/archive-<stamp>.csv."""
    class Response:
        status_code = 200
        text = json.dumps([
            ["timestamp", "original", "digest"],
            ["../../evil", va.LEGACY_URL.format(election="2022 November General"), "D1"],
            ["20221102204427", va.LEGACY_URL.format(election="2022 November General"), "D2"],
        ])
    monkeypatch.setattr(va, "_throttle", lambda *a, **k: None)
    monkeypatch.setattr(va.SESSION, "get", lambda *a, **k: Response())
    captures = va._archive_captures(2022)
    assert [c[0] for c in captures] == ["20221102204427"]
