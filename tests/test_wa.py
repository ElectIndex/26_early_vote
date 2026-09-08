"""Washington: the SoS's daily statewide Ballot Status Report.

Two fixtures, both real, both built from the archived 2024 general:

* `Statewide2024-10-23.zip` -- 486 ballots sampled from the day Washington's
  file carried **all 39** counties, so it exercises the statewide path.
* `Statewide2024-10-22.zip` -- 474 ballots from the day before, which carried
  **38** (Grant County had not uploaded), so it exercises the partial-coverage
  path where county rows are published and no StateDay is.

Both are the real header verbatim, the real `Ballot Status Report YYYY-MM-DD.csv`
name inside the zip, and real rows -- including the awkward ones the parser has
to survive: a blank `Received Date`, a blank `Return Method`, `In Person`,
`Email`, `Non-Standard Mail`, `Rejected`, a gender of `O`, and two rows with one
more field than the header.

Four more from the 2022 general, `BallotStatus2022-11-09_*.zip`, which is that
cycle's whole report: Washington split it by county that year, so King is its
own file, Snohomish sits with Spokane, Clark with Pierce, and the other 34
counties share one. They are sampled the same way and carry the real 21-column
2022 header -- one column WIDER than 2024's, which gained a `Sent Date` the
parser does not read and must not care about.

The columns the parser never reads and that name an individual voter -- Ballot
ID, Voter ID, First/Last Name, Address, City, Zip, Precinct, Split and Return
Location -- are replaced with a placeholder. Every column the parser DOES read
(County, Gender, Election, Ballot Status, Received Date, Return Method, Party)
is untouched.
"""

from __future__ import annotations

import io
import zipfile
from datetime import date
from pathlib import Path

import pytest

from ev.adapters import wa
from ev.adapters._net import Missing
from ev.adapters.base import NotYetPublished, SchemaDrift, SourceError

FIX = Path(__file__).parent / "fixtures" / "wa"
FULL = (FIX / "Statewide2024-10-23.zip").read_bytes()      # 39 of 39 counties
SHORT = (FIX / "Statewide2024-10-22.zip").read_bytes()     # 38 of 39 counties

#: The 2022 general's report, in the four pieces Washington published it in.
PARTS_2022 = [
    (FIX / f"BallotStatus2022-11-09_{slug}.zip").read_bytes()
    for slug in ("all_other_counties", "cr_pi", "ki", "sn_sp")
]


def rezip(source: bytes, transform) -> bytes:
    """The same zip with its CSV text put through `transform`."""
    archive = zipfile.ZipFile(io.BytesIO(source))
    name = archive.namelist()[0]
    text = transform(archive.read(name).decode("utf-8"))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(name, text)
    return out.getvalue()


# --------------------------------------------------------------------------
# One download rebuilds the whole curve
# --------------------------------------------------------------------------
def test_one_snapshot_produces_a_daily_series_not_a_single_row():
    """Every ballot carries its own Received Date, so a missed run cannot lose
    a day of Washington's series."""
    result = wa.parse(FULL, 2024, date(2024, 10, 23))
    days = [r.day for r in result.state_rows]
    assert days == sorted(days)
    assert len(days) > 5
    assert days[-1] == date(2024, 10, 23)


def test_the_series_is_cumulative_and_the_last_day_is_the_snapshot():
    result = wa.parse(FULL, 2024, date(2024, 10, 23))
    totals = [r.ballots_total for r in result.state_rows]
    assert totals == sorted(totals)
    # 486 sampled ballots, two of which carry no Received Date at all.
    assert totals[-1] == 484
    assert result.state_rows[-1].ballots_new == 133


def test_as_of_truncates_the_series_rather_than_the_file():
    result = wa.parse(FULL, 2024, date(2024, 10, 18))
    assert result.state_rows[-1].day == date(2024, 10, 18)
    assert result.state_rows[-1].ballots_total == 60


# --------------------------------------------------------------------------
# Coverage is gated exactly as Texas's is
# --------------------------------------------------------------------------
def test_a_short_snapshot_publishes_counties_and_no_statewide_row():
    """A sum over 38 of 39 counties looks exactly like a Washington total and
    is not one, and nothing in StateDay can say which county is missing."""
    result = wa.parse(SHORT, 2024, date(2024, 10, 22))
    assert result.state_rows == []
    assert result.county_rows
    assert len({r.county_fips for r in result.county_rows}) == 38


def test_a_complete_snapshot_does_publish_the_statewide_row():
    result = wa.parse(FULL, 2024, date(2024, 10, 23))
    assert result.state_rows
    assert len({r.county_fips for r in result.county_rows}) == 39


def test_counties_are_keyed_by_fips_not_by_name():
    result = wa.parse(FULL, 2024, date(2024, 10, 23))
    assert all(r.county_fips.startswith("53") for r in result.county_rows)
    king = [r for r in result.county_rows if r.county_fips == "53033"]
    assert king and king[0].county_name == "King County"


def test_an_unrecognised_county_is_drift():
    body = rezip(FULL, lambda t: t.replace(",Adams,", ",Adamsx,"))
    with pytest.raises(SchemaDrift, match="unrecognised county names"):
        wa.parse(body, 2024, date(2024, 10, 23))


# --------------------------------------------------------------------------
# The election is named in every row, so it cannot be mistaken
# --------------------------------------------------------------------------
def test_the_election_column_is_parsed_with_washingtons_double_space():
    assert wa.parse_election("General Nov  5 2024") == ("general", date(2024, 11, 5))
    assert wa.parse_election("Primary Aug  6 2024") == ("primary", date(2024, 8, 6))
    assert wa.parse_election("nonsense") is None


def test_a_primary_file_is_never_published_as_the_general():
    body = rezip(FULL, lambda t: t.replace("General Nov  5 2024", "Primary Aug  6 2024"))
    with pytest.raises(NotYetPublished, match="holds no ballots for the 2024 general"):
        wa.parse(body, 2024, date(2024, 10, 23))


def test_another_cycles_general_is_not_this_cycles():
    with pytest.raises(NotYetPublished, match="holds no ballots for the 2022 general"):
        wa.parse(FULL, 2022, date(2022, 10, 23))


# --------------------------------------------------------------------------
# What the counts mean
# --------------------------------------------------------------------------
def test_ballots_total_counts_every_returned_ballot_whatever_its_status():
    """Rejected ballots were still returned, and many are later cured. Counting
    only the accepted ones would move the headline for reasons that are not
    turnout."""
    result = wa.parse(FULL, 2024, date(2024, 10, 23))
    assert result.state_rows[-1].ballots_total == 484


def test_drop_box_and_email_returns_are_mail_because_washington_is_all_mail():
    for label in ("Mail", "Drop Box", "Email", "Fax",
                  "Non-Standard Mail", "Non-Standard Dropbox"):
        assert wa.method_bucket(label) == "mail"
    assert wa.method_bucket("In Person") == "inperson"


def test_a_blank_return_method_counts_in_the_total_and_in_neither_bucket():
    assert wa.method_bucket("") is None
    result = wa.parse(FULL, 2024, date(2024, 10, 23))
    row = result.state_rows[-1]
    assert row.ballots_total - row.mail_returned - row.inperson == 1


def test_an_unrecognised_return_method_is_drift_not_a_guess():
    body = rezip(FULL, lambda t: t.replace(",Drop Box,", ",Ballot Drone,"))
    with pytest.raises(SchemaDrift, match="unrecognised return method"):
        wa.parse(body, 2024, date(2024, 10, 23))


def test_mail_requested_stays_blank_because_washington_mails_every_voter_a_ballot():
    result = wa.parse(FULL, 2024, date(2024, 10, 23))
    assert all(r.mail_requested is None for r in result.state_rows)


def test_washington_has_no_party_registration_so_every_party_field_is_blank():
    result = wa.parse(FULL, 2024, date(2024, 10, 23))
    for row in result.state_rows + result.county_rows:
        assert (row.party_dem, row.party_rep, row.party_npa, row.party_oth) == (
            None, None, None, None
        )


def test_a_party_column_that_stops_being_empty_is_drift():
    """Washington has no party enrolment and the column is empty on all 515,318
    rows of the real 2024 file. If it fills in, that is a new dimension to add
    deliberately, not one to start guessing at mid-season."""
    body = rezip(FULL, lambda t: t.replace(",Mail,REDACTED,,\r\n", ",Mail,REDACTED,DEM,\r\n"))
    with pytest.raises(SchemaDrift, match="Party column is no longer empty"):
        wa.parse(body, 2024, date(2024, 10, 23))


# --------------------------------------------------------------------------
# Demographics
# --------------------------------------------------------------------------
def test_sex_is_published_and_sums_to_the_statewide_total():
    result = wa.parse(FULL, 2024, date(2024, 10, 23))
    last = date(2024, 10, 23)
    final = {r.bucket: r.ballots_total for r in result.demo_rows if r.day == last}
    assert set(final) == {"female", "male", "unknown"}
    assert sum(final.values()) == 484


def test_washingtons_single_letter_other_gender_maps_the_way_the_word_does():
    assert wa.sex_bucket("O") == "unknown"
    assert wa.sex_bucket("F") == "female"
    assert wa.sex_bucket("") == "unknown"


def test_an_unrecognised_gender_code_is_drift():
    with pytest.raises(SchemaDrift, match="unrecognised gender code"):
        wa.sex_bucket("Q")


# --------------------------------------------------------------------------
# Dates
# --------------------------------------------------------------------------
def test_the_snapshot_date_comes_from_the_csvs_own_name():
    assert wa.snapshot_day("Ballot Status Report 2024-10-22.csv") == date(2024, 10, 22)
    with pytest.raises(SchemaDrift, match="carries no snapshot date"):
        wa.snapshot_day("Ballot Status Report.csv")


def test_a_handful_of_undated_ballots_is_tolerated_but_a_file_of_them_is_drift():
    assert wa.parse(FULL, 2024, date(2024, 10, 23)).state_rows
    body = rezip(FULL, lambda t: t.replace("/2024 12:00:00 AM", ""))
    with pytest.raises(SchemaDrift, match="the date format has changed"):
        wa.parse(body, 2024, date(2024, 10, 23))


def test_ballots_dated_after_the_snapshot_are_not_counted():
    body = rezip(FULL, lambda t: t.replace("10/23/2024 12:00:00 AM",
                                           "11/23/2024 12:00:00 AM"))
    with pytest.raises(SchemaDrift, match="no usable Received Date"):
        wa.parse(body, 2024, date(2024, 10, 23))


# --------------------------------------------------------------------------
# The container
# --------------------------------------------------------------------------
def test_a_missing_column_is_drift():
    body = rezip(FULL, lambda t: t.replace("Return Method", "How Returned", 1))
    with pytest.raises(SchemaDrift, match="missing columns"):
        wa.parse(body, 2024, date(2024, 10, 23))


def test_a_body_that_is_not_a_zip_is_a_source_error():
    with pytest.raises(SourceError, match="not a readable zip"):
        wa.parse(b"not a zip at all, but long enough to get here", 2024,
                 date(2024, 10, 23))


def test_a_404_page_served_as_the_file_reads_as_absence():
    """The SoS's Drupal 404 is HTML with a 404 status, but a 200 shell would
    look the same to the parser -- and either way it means 'not posted'."""
    with pytest.raises(Missing, match="came back as a web page"):
        wa.parse(b"<!DOCTYPE html><html><head><title>Page not found</title></head>"
                 b"<body>nope</body></html>", 2024, date(2024, 10, 23))


def test_a_zip_with_more_than_one_csv_is_drift():
    archive = zipfile.ZipFile(io.BytesIO(FULL))
    name = archive.namelist()[0]
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr(name, archive.read(name))
        z.writestr("Extra Report 2024-10-23.csv", "a,b\n1,2\n")
    with pytest.raises(SchemaDrift, match="exactly one CSV"):
        wa.parse(out.getvalue(), 2024, date(2024, 10, 23))


# --------------------------------------------------------------------------
# Adapter wiring
# --------------------------------------------------------------------------
def test_adapter_identity():
    scraper = wa.WAScraper()
    assert (scraper.state, scraper.name, scraper.tier) == ("WA", "wa-sos", 1)


def test_the_url_is_built_from_the_date():
    assert wa.ZIP_URL.format(day="2024-10-22").endswith(
        "/current_election/Statewide2024-10-22.zip"
    )


def test_the_lookback_walks_back_over_days_the_sos_skipped(monkeypatch):
    asked: list[date] = []

    def fake(self, day, *, use_cache):
        asked.append(day)
        if day != date(2026, 10, 30):
            raise Missing(f"WA: {day} returned 404")
        return FULL

    monkeypatch.setattr(wa.WAScraper, "_snapshot", fake)
    day, body = wa.WAScraper()._latest(date(2026, 11, 1), use_cache=False)
    assert day == date(2026, 10, 30)
    assert body is FULL
    assert asked[:3] == [date(2026, 11, 1), date(2026, 10, 31), date(2026, 10, 30)]


def test_an_unposted_window_stops_the_ladder(monkeypatch):
    """Every date 404s today, because `current_election/` is purged between
    cycles and the SoS says 2026 returns begin 2026-10-20. That is absence, not
    an error, and it must not fall through to a source that would invent a
    zero."""
    monkeypatch.setattr(
        wa.WAScraper, "_snapshot",
        lambda self, day, *, use_cache: (_ for _ in ()).throw(Missing("404")),
    )
    with pytest.raises(NotYetPublished, match="no daily ballot status report"):
        wa.WAScraper().fetch(2026, date(2026, 9, 6))


def test_history_refuses_the_current_cycle():
    with pytest.raises(NotYetPublished, match="not an archived cycle"):
        wa.WAScraper().fetch_history(date.today().year)


# --------------------------------------------------------------------------
# The archived cycles
# --------------------------------------------------------------------------
def test_the_archive_pins_one_day_per_cycle():
    """One DAY per cycle, because every ballot is dated and the last usable
    snapshot of a cycle therefore contains every earlier day of it. What varies
    is how many FILES that day is."""
    assert set(wa.ARCHIVED) == {2022, 2024}
    day, parts = wa.ARCHIVED[2024]
    assert day == date(2024, 11, 19)
    assert len(parts) == 1
    url, filename = parts[0]
    # `id_` for the SoS's ORIGINAL bytes -- these are zips, and a rewrite would
    # corrupt one outright -- under the LIVE cache name.
    assert "web.archive.org/web/20241120010214id_/" in url
    assert filename == "Statewide2024-11-19.zip"


def test_2022_is_four_live_files_on_the_unchallenged_host():
    """⚠️ This cycle was written off as "does not exist, anywhere". It does: the
    2024 URL scheme is not the 2022 one, and the three checks that produced that
    verdict were all about the 2024 scheme. See the module docstring."""
    day, parts = wa.ARCHIVED[2022]
    assert day == date(2022, 11, 9)
    assert len(parts) == 4
    urls = [u for u, _ in parts]
    # Live on `www2`, under `_assets/` -- NOT `current_election/`, which is what
    # was swept, and NOT behind the Cloudflare wall on the landing page.
    assert all(u.startswith("https://www2.sos.wa.gov/_assets/elections/research/")
               for u in urls)
    assert all("web.archive.org" not in u for u in urls)
    assert [u.rsplit("%20", 1)[-1] for u in urls] == [
        "counties.zip", "pi.zip", "ki.zip", "sp.zip"]
    assert [f for _, f in parts] == [
        "BallotStatus2022-11-09_all_other_counties.zip",
        "BallotStatus2022-11-09_cr_pi.zip",
        "BallotStatus2022-11-09_ki.zip",
        "BallotStatus2022-11-09_sn_sp.zip",
    ]


def test_a_cycle_with_no_pinned_report_is_refused_by_name():
    with pytest.raises(NotYetPublished, match="no pinned 2020 ballot status"):
        wa.WAScraper().fetch_history(2020)


def test_history_reads_the_pinned_capture_under_the_live_cache_name(monkeypatch):
    """`id_` for the SoS's original bytes, and the cache filename is the LIVE
    one so a snapshot taken during the season is reused instead of the Archive."""
    seen = {}

    def fake_get(url, **kwargs):
        seen.update(url=url, **kwargs)
        return FULL

    monkeypatch.setattr(wa, "get", fake_get)
    result = wa.WAScraper().fetch_history(2024)
    assert seen["url"] == (
        "https://web.archive.org/web/20241120010214id_/"
        "https://www.sos.wa.gov/sites/default/files/current_election/"
        "Statewide2024-11-19.zip"
    )
    assert seen["filename"] == "Statewide2024-11-19.zip"
    assert seen["use_cache"] is True
    assert result.state_rows and result.county_rows


def test_history_stops_at_election_day_while_fetch_stops_at_the_run_date(monkeypatch):
    """Guard parity, on the axis that bit az.py and nc.py: neither path may
    publish a ballot dated after the day it was asked for."""
    monkeypatch.setattr(wa, "get", lambda url, **kw: FULL)
    history = wa.WAScraper().fetch_history(2024)
    assert max(r.day for r in history.state_rows) <= date(2024, 11, 5)

    monkeypatch.setattr(wa.WAScraper, "_snapshot",
                        lambda self, day, *, use_cache: FULL)
    live = wa.WAScraper().fetch(2024, date(2024, 10, 22))
    assert max(r.day for r in live.state_rows) <= date(2024, 10, 22)


def test_a_vanished_capture_is_absence_not_a_fallthrough(monkeypatch):
    def gone(url, **kwargs):
        raise Missing(f"WA: {url} returned 404")

    monkeypatch.setattr(wa, "get", gone)
    with pytest.raises(NotYetPublished, match="Statewide2024-11-19.zip is gone"):
        wa.WAScraper().fetch_history(2024)


# --------------------------------------------------------------------------
# 2022: one report, four files
# --------------------------------------------------------------------------
def test_the_four_parts_are_read_as_one_report():
    """Parsed separately they would be four partial statewide curves, each of
    which looks exactly like a Washington total and is not one."""
    result = wa.parse_parts(PARTS_2022, 2022, date(2022, 11, 8))
    assert len({row.county_fips for row in result.county_rows}) == 39
    assert len(result.county_rows) == 509
    # A statewide row per day only because all 39 counties are present.
    assert len(result.state_rows) == 19
    assert result.state_rows[0].day == date(2022, 10, 3)
    assert result.state_rows[-1].day == date(2022, 11, 8)


def test_one_part_alone_publishes_counties_and_no_statewide_row():
    """The coverage gate, on the shape that makes it matter most: King County
    alone is a fifth of Washington and would look like a plausible total."""
    result = wa.parse_parts([PARTS_2022[2]], 2022, date(2022, 11, 8))
    assert {row.county_fips for row in result.county_rows} == {"53033"}
    assert result.state_rows == []


def test_the_2022_curve_is_cumulative_and_ends_on_election_day():
    result = wa.parse_parts(PARTS_2022, 2022, date(2022, 11, 8))
    totals = [row.ballots_total for row in result.state_rows]
    assert totals == sorted(totals)
    assert totals[-1] == 505
    assert sum(row.ballots_new for row in result.state_rows) == 505
    # `parse` trims at Election Day even though the file was pulled on the 9th:
    # Washington counts ballots postmarked by Election Day for days afterwards.
    assert max(row.day for row in result.county_rows) == date(2022, 11, 8)


def test_the_2022_file_is_one_column_wider_and_that_is_fine():
    """2022 carries a `Sent Date` the parser does not read. An EXTRA column is
    not drift; a missing REQUIRED one is."""
    import csv as _csv
    archive = zipfile.ZipFile(io.BytesIO(PARTS_2022[1]))
    header = next(_csv.reader(io.StringIO(
        archive.read(archive.namelist()[0]).decode("utf-8-sig"))))
    assert "Sent Date" in header
    assert set(wa.REQUIRED) <= set(header)


def test_the_2022_election_label_is_matched_the_same_way():
    """Washington's double space, in a different year."""
    assert wa.parse_election("General Nov  8 2022") == ("general", date(2022, 11, 8))
    # ...and a 2022 file is not a 2024 one.
    with pytest.raises(NotYetPublished, match="holds no ballots for the 2024 general"):
        wa.parse_parts(PARTS_2022, 2024, date(2024, 11, 5))


def test_washington_still_registers_nobody_by_party_in_2022():
    result = wa.parse_parts(PARTS_2022, 2022, date(2022, 11, 8))
    for row in result.state_rows + result.county_rows:
        assert (row.party_dem, row.party_rep, row.party_oth, row.party_npa) == (
            None,) * 4


def test_a_part_read_twice_is_drift_not_a_doubled_county():
    """⚠️ These tallies ADD. A part listed twice in `ARCHIVED` would silently
    double a county's turnout, which is the one failure a split report can have
    that a single file cannot."""
    with pytest.raises(SchemaDrift, match="repeats counties already read"):
        wa.parse_parts(PARTS_2022 + [PARTS_2022[2]], 2022, date(2022, 11, 8))


def test_parts_from_different_days_are_drift():
    """A mixed set is not one day's position."""
    archive = zipfile.ZipFile(io.BytesIO(PARTS_2022[2]))
    text = archive.read(archive.namelist()[0]).decode("utf-8")
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("Ballot Status Report 2022-11-10 KI.csv", text)
    with pytest.raises(SchemaDrift, match="parts of this report are dated"):
        wa.parse_parts([PARTS_2022[0], out.getvalue()], 2022, date(2022, 11, 8))


def test_fetch_history_2022_reads_every_part_and_asks_the_live_host(monkeypatch):
    """⚠️ GUARD PARITY on the fetch side: both cycles go through the SAME
    `parse_parts`, so a 2022 backfill cannot skip a check the daily job makes."""
    asked: list[tuple[str, str]] = []
    bodies = dict(zip(
        ("all_other_counties", "cr_pi", "ki", "sn_sp"), PARTS_2022))

    def fake_get(url, *, state, filename, **kwargs):
        asked.append((url, filename))
        assert kwargs["use_cache"] is True
        return bodies[filename.removeprefix("BallotStatus2022-11-09_")
                      .removesuffix(".zip")]

    monkeypatch.setattr(wa, "get", fake_get)
    result = wa.WAScraper().fetch_history(2022)
    assert len(asked) == 4
    assert all("www2.sos.wa.gov" in url for url, _ in asked)
    assert len({row.county_fips for row in result.county_rows}) == 39
    assert result.state_rows[-1].day == date(2022, 11, 8)
    assert result.state_rows[-1].ballots_total == 505
