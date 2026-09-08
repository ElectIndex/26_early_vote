"""Minnesota: the SoS's Absentee Data page.

Three fixtures, all real, all the content region of the page verbatim -- the
`<h2>` section heading through the closing `</table>` of the county table, with
the site's navigation chrome left off and not one digit changed:

* `absentee-data_2024-10-03_general.html` -- the 2024 general on day 14 of the
  absentee period. Two columns, NO method split, and NO header row.
* `absentee-data_2022-10-13_general.html` -- the 2022 general. Same two columns,
  but this one DOES carry a `County | Applications Submitted | Accepted Ballots`
  header row, which is why the parser detects the header rather than assuming it.
* `absentee-data_2026-08-11_primary.html` -- the live page on 2026-09-06. It is
  the August PRIMARY, which is the whole point: it exercises the
  NotYetPublished path that runs every day until Minnesota posts the general,
  and its three-column layout (applications, accepted by mail, accepted in
  person) is the other shape the parser has to handle.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from ev.adapters import mn
from ev.adapters.base import NotYetPublished, SchemaDrift, SourceError

FIX = Path(__file__).parent / "fixtures" / "mn"
GEN24 = (FIX / "absentee-data_2024-10-03_general.html").read_bytes()
GEN22 = (FIX / "absentee-data_2022-10-13_general.html").read_bytes()
PRIM26 = (FIX / "absentee-data_2026-08-11_primary.html").read_bytes()

TODAY = date(2026, 9, 6)


@pytest.fixture
def primary_as_general(monkeypatch):
    """The 2026 primary section read through the general's code path.

    Minnesota has not posted the general yet and the heading is the only thing
    that names an election, so the words matched on are stubbed to the
    primary's. Every count, every column and every date below is Minnesota's own.
    The date window is stubbed with it, because an August primary is outside the
    November general's window by construction.
    """
    monkeypatch.setattr(mn, "GENERAL_WORDS", ("primary", "absentee", "counts"))
    monkeypatch.setattr(mn, "OTHER_ELECTIONS", ("special", "run-off"))
    monkeypatch.setattr(mn, "WINDOW_BEFORE", 400)
    return mn.parse(PRIM26, 2026, TODAY)


# --------------------------------------------------------------------------
# The page carries one election and the heading is the only label
# --------------------------------------------------------------------------
def test_the_general_is_not_posted_yet_and_that_stops_the_ladder():
    """The path that runs every day until October. It must not fall through to
    a weaker source, and it must certainly not publish the primary's 445,023."""
    with pytest.raises(NotYetPublished, match="no general-election section"):
        mn.parse(PRIM26, 2026, TODAY)


def test_the_only_heading_today_is_the_primarys():
    assert [h for _, h in mn.headings(PRIM26.decode())] == [
        "State Primary Absentee Counts"
    ]


def test_a_primary_heading_can_never_be_read_as_the_general():
    page = GEN24.decode().replace(
        "State General Election Absentee Counts",
        "State Primary Election Absentee Counts",
    )
    with pytest.raises(NotYetPublished):
        mn.parse(page.encode(), 2024, date(2024, 10, 3))


# --------------------------------------------------------------------------
# The heading names no year, so the DATE proves which cycle this is
# --------------------------------------------------------------------------
def test_the_2024_general_is_refused_when_asked_for_as_2026():
    """The heading is identical every cycle. Without the window check, a stale
    page left up from 2024 would publish as 2026's early vote."""
    with pytest.raises(NotYetPublished, match="outside the 2026 general's window"):
        mn.parse(GEN24, 2026, TODAY)


def test_a_page_dated_after_the_run_falls_through_rather_than_publishing():
    with pytest.raises(SourceError, match="after the run date"):
        mn.parse(GEN24, 2024, date(2024, 10, 1))


# --------------------------------------------------------------------------
# The 2024 general: two columns, no header row
# --------------------------------------------------------------------------
def test_2024_general_statewide_row_is_minnesotas_own_figures():
    result = mn.parse(GEN24, 2024, date(2024, 10, 3))
    (row,) = result.state_rows
    assert row.day == date(2024, 10, 3)
    assert row.ballots_total == 107_421
    assert row.mail_requested == 522_784
    # This layout publishes no method split at all. Blank, never 0.
    assert row.mail_returned is None
    assert row.inperson is None
    assert row.ballots_new is None


def test_minnesota_has_no_party_registration_so_every_party_field_is_blank():
    result = mn.parse(GEN24, 2024, date(2024, 10, 3))
    for row in result.state_rows + result.county_rows:
        assert row.party_dem is None
        assert row.party_rep is None
        assert row.party_npa is None
        assert row.party_oth is None


def test_all_87_counties_are_keyed_by_fips():
    result = mn.parse(GEN24, 2024, date(2024, 10, 3))
    assert len(result.county_rows) == 87
    assert len({r.county_fips for r in result.county_rows}) == 87
    assert all(r.county_fips.startswith("27") for r in result.county_rows)
    hennepin = next(r for r in result.county_rows if r.county_fips == "27053")
    assert hennepin.county_name == "Hennepin County"
    assert hennepin.ballots_total == 35_861


def test_the_counties_sum_to_minnesotas_own_statewide_total():
    result = mn.parse(GEN24, 2024, date(2024, 10, 3))
    assert sum(r.ballots_total for r in result.county_rows) == 107_421


# --------------------------------------------------------------------------
# The 2022 general: same two columns, but WITH a header row
# --------------------------------------------------------------------------
def test_2022_general_parses_with_its_header_row_present():
    result = mn.parse(GEN22, 2022, date(2022, 10, 13))
    (row,) = result.state_rows
    assert row.day == date(2022, 10, 13)
    assert row.ballots_total == 99_252
    assert row.mail_requested == 400_975
    assert len(result.county_rows) == 87


def test_a_header_row_that_disagrees_with_the_bullets_is_drift():
    page = GEN22.decode().replace(
        '<th scope="col">Applications Submitted</th>',
        '<th scope="col">Ballots Sent</th>',
    )
    with pytest.raises(SchemaDrift, match="does not match the statewide bullets"):
        mn.parse(page.encode(), 2022, date(2022, 10, 13))


# --------------------------------------------------------------------------
# The three-column layout, which is what 2026 is publishing
# --------------------------------------------------------------------------
def test_the_three_column_layout_splits_mail_from_in_person(primary_as_general):
    (row,) = primary_as_general.state_rows
    assert row.day == date(2026, 8, 11)
    assert row.mail_requested == 445_023
    assert row.mail_returned == 116_414
    assert row.inperson == 133_364
    # Minnesota publishes no combined figure in this layout, so the total is
    # its own two accepted-ballot columns added together.
    assert row.ballots_total == 116_414 + 133_364


def test_the_three_column_layout_carries_the_split_down_to_counties(primary_as_general):
    rows = primary_as_general.county_rows
    assert len(rows) == 87
    assert sum(r.mail_returned for r in rows) == 116_414
    assert sum(r.inperson for r in rows) == 133_364
    assert sum(r.ballots_total for r in rows) == 116_414 + 133_364


# --------------------------------------------------------------------------
# The columns have no dependable header, so they are proved by their sums
# --------------------------------------------------------------------------
def test_a_column_that_does_not_sum_to_its_statewide_figure_is_drift():
    """This is the whole safety argument for a headerless table: swap two
    columns and the sums stop matching, so nothing is published."""
    page = GEN24.decode().replace(
        "<td class=\"text-right\">5,600</td>\n<td class=\"text-right\">989</td>",
        "<td class=\"text-right\">989</td>\n<td class=\"text-right\">5,600</td>",
    )
    with pytest.raises(SchemaDrift, match="the columns cannot be identified"):
        mn.parse(page.encode(), 2024, date(2024, 10, 3))


def test_a_statewide_figure_we_cannot_name_is_drift_not_a_guess():
    page = GEN24.decode().replace(
        "Applications submitted (10/3/24)", "Ballots transmitted (10/3/24)"
    )
    with pytest.raises(SchemaDrift, match="unrecognised statewide figure"):
        mn.parse(page.encode(), 2024, date(2024, 10, 3))


def test_a_missing_county_is_drift():
    page = GEN24.decode().replace(
        '<th scope="row">Aitkin</th>\n<td class="text-right">5,600</td>\n'
        '<td class="text-right">989</td>\n</tr>\n<tr>\n', "", 1
    )
    with pytest.raises(SchemaDrift, match="lists 86 counties"):
        mn.parse(page.encode(), 2024, date(2024, 10, 3))


def test_a_caption_and_a_bullet_that_disagree_on_the_date_are_drift():
    page = GEN24.decode().replace("as of October 3, 2024", "as of October 4, 2024")
    with pytest.raises(SchemaDrift, match="is captioned 2024-10-04"):
        mn.parse(page.encode(), 2024, date(2024, 10, 5))


# --------------------------------------------------------------------------
# The bot manager answers 200, so "we could not look" has to be detected
# --------------------------------------------------------------------------
def test_the_radware_challenge_is_a_source_error_not_an_absence():
    """A 200-with-an-interstitial must fall THROUGH to the aggregator. Reading
    it as NotYetPublished would stop the ladder and record that Minnesota has
    nothing, when the truth is that we never saw its page."""
    challenge = (b"<!DOCTYPE html><html><head><title>Radware Captcha Page</title>"
                 b'<link href="https://captcha.perfdrive.com/x.css"></head>'
                 b"<body><table></table></body></html>")
    with pytest.raises(SourceError, match="we could not look"):
        mn.parse(challenge, 2026, TODAY)
    assert not isinstance(SourceError, NotYetPublished)


def test_the_real_page_is_not_mistaken_for_the_challenge():
    """Radware injects its own script into the genuine page too, so the markers
    have to be narrow enough not to reject every good fetch."""
    for fixture in (GEN22, GEN24, PRIM26):
        assert not mn.looks_intercepted(fixture)
    live_markers = b'ssConf("cu", "validate.perfdrive.com, ssc");w["SSJSConnectorObj"]'
    assert not mn.looks_intercepted(live_markers)


def test_a_page_with_no_tables_is_a_source_error():
    with pytest.raises(SourceError, match="carries no tables"):
        mn.parse(b"<html><body><h2>State General Absentee Counts</h2></body></html>",
                 2026, TODAY)


# --------------------------------------------------------------------------
# Adapter wiring
# --------------------------------------------------------------------------
def test_adapter_identity():
    scraper = mn.MNScraper()
    assert (scraper.state, scraper.name, scraper.tier) == ("MN", "mn-sos", 1)


def test_fetch_history_refuses_the_cycle_that_is_still_running():
    with pytest.raises(NotYetPublished, match="not an archived cycle"):
        mn.MNScraper().fetch_history(date.today().year)


def test_fetch_history_refuses_a_cycle_before_the_first_tracked_one():
    with pytest.raises(NotYetPublished, match="before the first tracked cycle"):
        mn.MNScraper().fetch_history(2020)


def test_fetch_reads_the_page_and_parses_it(monkeypatch):
    monkeypatch.setattr(mn, "download", lambda *a, **k: GEN24)
    result = mn.MNScraper().fetch(2024, date(2024, 10, 3))
    assert result.state_rows[0].ballots_total == 107_421
    assert len(result.county_rows) == 87


# --------------------------------------------------------------------------
# The archived county series, rebuilt from the Internet Archive
#
# Every byte below is a real capture of the SoS's own page, and the stamps are
# the real ones the CDX index returns. Nothing here touches the network: the
# CDX fixture stands in for the index and the captures stand in for the fetch.
# --------------------------------------------------------------------------
FINAL24 = (FIX / "absentee-data_2024-11-05_general-final.html").read_bytes()
CDX24 = (FIX / "cdx_2024_absentee-data.json").read_bytes()
CDX22 = (FIX / "cdx_2022_absentee-data.json").read_bytes()

#: The three real 2024 captures, keyed by their real Wayback stamps. The first
#: is the AUGUST PRIMARY's page -- the Archive caught the URL before Minnesota
#: swapped the election over -- which is exactly the capture that must be
#: skipped rather than published as the general.
CAPTURES_2024 = {
    "20240919132653": PRIM26,
    "20241003181752": GEN24,
    "20241109152601": FINAL24,
}


@pytest.fixture
def archive(monkeypatch):
    """Serve the CDX index and the captures from fixtures, never the network."""
    def fake_get(url, *, state, filename, **kwargs):
        if url.startswith(mn.CDX_URL):
            return CDX24 if "2024" in filename else CDX22
        for stamp, body in CAPTURES_2024.items():
            if stamp in url:
                return body
        if "20221014040640" in url:
            return GEN22
        raise AssertionError(f"unexpected fetch of {url}")

    monkeypatch.setattr(mn, "get", fake_get)


def test_2024_backfill_gives_two_days_of_all_87_counties(archive):
    result = mn.MNScraper().fetch_history(2024)
    assert sorted({r.day for r in result.county_rows}) == [
        date(2024, 10, 3), date(2024, 11, 5)
    ]
    assert len(result.county_rows) == 2 * 87
    assert len(result.state_rows) == 2


def test_the_archived_election_day_final_is_minnesotas_own_figure(archive):
    """1,271,636 accepted ballots as of November 5, 2024 -- a genuine
    days_to_election 0 reading, which is the one the cycle comparison needs."""
    result = mn.MNScraper().fetch_history(2024)
    final = next(r for r in result.state_rows if r.day == date(2024, 11, 5))
    assert (final.ballots_total, final.mail_requested) == (1_271_636, 1_420_287)
    election_day = [r for r in result.county_rows if r.day == date(2024, 11, 5)]
    assert sum(r.ballots_total for r in election_day) == 1_271_636
    hennepin = next(r for r in election_day if r.county_fips == "27053")
    assert (hennepin.county_name, hennepin.ballots_total) == (
        "Hennepin County", 342_971
    )


def test_the_primary_capture_is_skipped_not_published_as_the_general(archive):
    """The Archive caught this URL while the August primary was still up. Its
    445,023 applications must never appear under the November general."""
    result = mn.MNScraper().fetch_history(2024)
    assert all(r.mail_requested != 445_023 for r in result.state_rows)
    assert all(r.day.month in (10, 11) for r in result.state_rows)


def test_2022_backfill_is_the_one_capture_the_archive_holds(archive):
    result = mn.MNScraper().fetch_history(2022)
    assert {r.day for r in result.county_rows} == {date(2022, 10, 13)}
    assert len(result.county_rows) == 87
    (state,) = result.state_rows
    assert (state.ballots_total, state.mail_requested) == (99_252, 400_975)


def test_the_archived_rows_obey_the_blank_rule_like_the_live_ones(archive):
    """Minnesota has no party registration in any cycle, and neither of these
    two-column layouts splits mail from in-person."""
    result = mn.MNScraper().fetch_history(2024)
    for row in result.state_rows + result.county_rows:
        for field in ("party_dem", "party_rep", "party_oth", "party_npa"):
            assert getattr(row, field) is None
        assert row.mail_returned is None and row.inperson is None


def test_an_archive_with_nothing_in_it_is_not_yet_published(monkeypatch):
    monkeypatch.setattr(mn, "archive_stamps", lambda cycle: [])
    with pytest.raises(NotYetPublished, match="nothing archived"):
        mn.MNScraper().fetch_history(2024)


def test_every_capture_drifting_is_reported_as_drift_not_as_absence():
    """A changed vocabulary must be loud. Reporting it as "nothing archived"
    would read as "the Archive has no Minnesota", which is a different fact."""
    drifted = GEN24.replace(b"Accepted ballots (10/3/24)",
                            b"Ballots we like (10/3/24)")

    def only_drift(url, *, state, filename, **kwargs):
        return drifted

    import pytest as _pytest
    with _pytest.MonkeyPatch.context() as m:
        m.setattr(mn, "archive_stamps", lambda cycle: ["20241003181752"])
        m.setattr(mn, "get", only_drift)
        with pytest.raises(SchemaDrift, match="unrecognised statewide figure"):
            mn.MNScraper().fetch_history(2024)


# --------------------------------------------------------------------------
# GUARD PARITY: the archive path runs the SAME parse the live path does
# --------------------------------------------------------------------------
def test_a_captures_as_of_is_its_own_stamp_so_the_run_date_guard_stays_live():
    """`fetch` will not publish a page dated after the run; `fetch_history`
    must not either, or a capture rewritten by the Archive could smuggle a
    later reading in under an earlier stamp."""
    assert mn.stamp_day("20241109152601") == date(2024, 11, 9)
    with pytest.raises(SourceError, match="not a Wayback timestamp"):
        mn.stamp_day("2024-11-09")


def test_a_capture_dated_after_its_own_stamp_is_refused(monkeypatch):
    monkeypatch.setattr(mn, "archive_stamps", lambda cycle: ["20241001000000"])
    monkeypatch.setattr(mn, "get", lambda url, **k: GEN24)
    # The capture says October 3; the stamp says October 1. `parse` raises
    # SourceError, which this path skips, so the run reports absence.
    with pytest.raises(NotYetPublished, match="nothing archived"):
        mn.MNScraper().fetch_history(2024)


def test_the_87_county_count_is_enforced_on_the_archive_path_too(monkeypatch):
    short = GEN24.replace(
        b'<th scope="row">Aitkin</th>\n<td class="text-right">5,600</td>\n'
        b'<td class="text-right">989</td>\n', b"", 1)
    monkeypatch.setattr(mn, "archive_stamps", lambda cycle: ["20241003181752"])
    monkeypatch.setattr(mn, "get", lambda url, **k: short)
    with pytest.raises(SchemaDrift):
        mn.MNScraper().fetch_history(2024)


# --------------------------------------------------------------------------
# The landing page is PERMANENT, so a 404 on it is a re-path, not an absence
# --------------------------------------------------------------------------
def test_a_404_on_the_landing_page_falls_through_instead_of_blanking_minnesota():
    """This URL answers 200 with ~65 KB every day of the year, carrying
    whichever election is current. There is no state of the world in which
    Minnesota "has not posted it yet" and the page 404s -- so reading a 404 as
    NotYetPublished would STOP the ladder, publish nothing, and badge the state
    with the same "early voting has not opened" it gets in July."""
    def gone(url, *, filename, use_cache=False, min_bytes=4096):
        raise mn.Missing(f"MN: {url} returned 404")

    with pytest.MonkeyPatch.context() as m:
        m.setattr(mn, "download", gone)
        with pytest.raises(SourceError) as caught:
            mn.MNScraper().fetch(2026, TODAY)
    assert not isinstance(caught.value, NotYetPublished)


def test_a_truncated_landing_page_is_also_a_refusal_not_an_absence():
    def stub(url, *, filename, use_cache=False, min_bytes=4096):
        raise mn.Missing(f"MN: {url} returned only 12 bytes")

    with pytest.MonkeyPatch.context() as m:
        m.setattr(mn, "download", stub)
        with pytest.raises(SourceError):
            mn.MNScraper().fetch(2026, TODAY)


def test_the_primary_being_up_is_still_not_yet_published(monkeypatch):
    """The fix must not blunt the REAL pending path: the page is there, it is
    fine, and it is showing an election that is not ours."""
    monkeypatch.setattr(mn, "download", lambda *a, **k: PRIM26)
    with pytest.raises(NotYetPublished, match="no general-election section"):
        mn.MNScraper().fetch(2026, TODAY)
