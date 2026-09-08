"""South Dakota: the SoS's absentee statistics page.

One fixture: `2026-Election-Absentee-Data-content.html`, the content region of the
live page copied verbatim -- the `<h1>` through the last `</table>`, 7.4 KB of the
117 KB page, with the site's navigation chrome left off and not one character of
the tables changed.

It is the real state of the world on 2026-09-06: the page carries the June
primary's and the July run-off's sections and NOT the general's, so it exercises
both the NotYetPublished path that will run every day until October and, through
the identically-shaped primary section, the parse itself.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from ev.adapters import sd
from ev.adapters._net import Missing
from ev.adapters.base import NotYetPublished, SchemaDrift, SourceError

FIXTURE = (Path(__file__).parent / "fixtures" / "sd"
           / "2026-Election-Absentee-Data-content.html")
PAGE_BYTES = FIXTURE.read_bytes()
PAGE = PAGE_BYTES.decode("utf-8")

TODAY = date(2026, 9, 6)


@pytest.fixture
def primary_section(monkeypatch):
    """The primary's section, read through the general's code path.

    South Dakota has not posted the general's section yet, and its heading is the
    only thing that distinguishes one election from another on this page. So the
    words the heading is matched on are stubbed to the primary's; every table,
    every value and every date below is South Dakota's own.
    """
    monkeypatch.setattr(sd, "SECTION_WORDS", ("south dakota", "primary election", "absentee"))
    monkeypatch.setattr(sd, "OTHER_ELECTIONS", ("run-off", "runoff"))
    return sd.parse(PAGE_BYTES, 2026, TODAY)


# --------------------------------------------------------------------------
# The page holds several elections and only the heading tells them apart
# --------------------------------------------------------------------------
def test_the_general_has_no_section_yet_and_that_stops_the_ladder():
    """This is the path that runs every day until October. It must not fall
    through to a weaker source, and it must certainly not read the run-off."""
    with pytest.raises(NotYetPublished, match="no 2026 general-election section"):
        sd.parse(PAGE_BYTES, 2026, TODAY)


def test_the_headings_on_the_page_are_the_two_other_elections():
    assert [h for _, h in sd.sections(PAGE)] == [
        "2026 Election Absentee Ballot Statistics",
        "South Dakota 2026 Run-off Election Absentee Ballot Weekly Statistics",
        "Party Breakout on Absentee Ballot Statistics as of 7/28/26",
        "South Dakota 2026 Primary Election Absentee Ballot Weekly Statistics",
        "Party Breakout on Absentee Ballot Statistics as of 6/2/26",
    ]


def test_the_first_heading_survives_the_sites_unclosed_strong_tag():
    """The site's collapsed navigation leaves a <strong> open, so reading
    headings as <strong> elements swallows the FIRST heading in the content --
    which on the day the general is posted would be the general's own."""
    nav = "<p><strong>Menu Home About</p>" + PAGE
    assert [h for _, h in sd.sections(nav)] == [h for _, h in sd.sections(PAGE)]


def test_a_run_off_section_can_never_be_read_as_the_general():
    """The run-off's 26,957 ballots would be a plausible-looking November
    headline, and it is a Republican-only election."""
    for _, heading in sd.sections(PAGE):
        low = heading.lower()
        if "run-off" in low or "primary" in low:
            assert any(w in low for w in sd.OTHER_ELECTIONS)


def test_a_page_that_is_not_the_statistics_page_falls_through():
    with pytest.raises(SourceError, match="no tables on it"):
        sd.parse(b"<html><body><p>Page Not Found</p></body></html>", 2026, TODAY)


# --------------------------------------------------------------------------
# The parse itself, against South Dakota's own numbers
# --------------------------------------------------------------------------
def test_the_date_table_is_cumulative_and_gives_the_whole_curve(primary_section):
    rows = {r.day: r for r in primary_section.state_rows}
    assert sorted(rows) == [date(2026, 5, 1), date(2026, 5, 8), date(2026, 5, 15),
                            date(2026, 5, 26), date(2026, 6, 2)]
    first, last = rows[date(2026, 5, 1)], rows[date(2026, 6, 2)]
    assert (first.mail_requested, first.ballots_total, first.inperson) == (5058, 2893, 2664)
    assert (last.mail_requested, last.ballots_total, last.inperson) == (33425, 32453, 28612)
    assert all(r.state == "SD" and r.cycle == 2026 for r in primary_section.state_rows)


def test_ballots_received_is_the_total_and_mail_is_never_derived(primary_section):
    """South Dakota's own legend says Ballots Received already includes walk-ins,
    and a walk-in voter may take the ballot home, so Received minus Walk-in is
    not a mail number. Blank, never a subtraction. See THE BLANK RULE."""
    for row in primary_section.state_rows:
        assert row.mail_returned is None, row
        assert row.ballots_total is not None and row.inperson is not None


def test_south_dakota_registers_by_party_so_the_split_is_real(primary_section):
    (row,) = [r for r in primary_section.state_rows if r.day == date(2026, 6, 2)]
    assert row.party_dem == 4201
    assert row.party_rep == 26125
    assert row.party_npa == 1565 + 516, "IND and NPA are both 'no party'"
    assert row.party_oth == 35 + 11, "LIB and OTH are both third parties"


def test_the_party_split_is_only_claimed_for_the_day_it_is_as_of(primary_section):
    """The page publishes one party snapshot, for the latest date. Copying it
    onto the earlier dates would invent a party split for days it never had."""
    for row in primary_section.state_rows:
        if row.day == date(2026, 6, 2):
            continue
        assert row.party_dem is None and row.party_rep is None, row
        assert row.party_npa is None and row.party_oth is None, row


def test_rows_after_as_of_are_not_published(monkeypatch):
    monkeypatch.setattr(sd, "SECTION_WORDS", ("south dakota", "primary election", "absentee"))
    monkeypatch.setattr(sd, "OTHER_ELECTIONS", ("run-off", "runoff"))
    result = sd.parse(PAGE_BYTES, 2026, date(2026, 5, 15))
    assert max(r.day for r in result.state_rows) == date(2026, 5, 15)
    with pytest.raises(NotYetPublished, match="no dates on or before"):
        sd.parse(PAGE_BYTES, 2026, date(2026, 4, 1))


def test_there_are_no_county_rows_and_that_is_not_an_error(primary_section):
    """South Dakota publishes no county breakdown of any of this."""
    assert primary_section.county_rows == []
    assert primary_section.demo_rows == []
    assert bool(primary_section)


# --------------------------------------------------------------------------
# The tables are typed by hand
# --------------------------------------------------------------------------
def test_a_comma_typed_as_a_period_is_still_a_thousands_separator():
    """On 2026-07-27 the run-off's walk-in cell reads "21.718". A fractional
    ballot count cannot exist, and int(float("21.718")) would publish 21."""
    runoff = sd._TABLE.findall(PAGE)[0]
    rows = dict(sd.parse_dates_table(runoff))
    assert rows[date(2026, 7, 27)] == [25365, 24502, 21718, 98]
    assert rows[date(2026, 7, 13)] == [13961, 12617, 10438, 98]
    assert sd._count("21.718") == 21718
    assert sd._count("1,234,567") == 1234567
    assert sd._count("98") == 98
    assert sd._count("") is None


def test_the_party_table_and_the_date_table_are_never_used_to_check_each_other():
    """They disagree: the run-off's date table says 26,257 received on 7/28 and
    its party table says 26,275. Both are what South Dakota typed."""
    tables = sd._TABLE.findall(PAGE)
    dates = dict(sd.parse_dates_table(tables[0]))
    party = sd.parse_party_table(tables[1])
    assert dates[date(2026, 7, 28)][1] == 26257
    assert party["rep"] == 26275


def test_a_party_with_no_ballots_is_a_real_zero():
    """The July run-off was a Republican contest, so South Dakota reported an
    affirmative zero for every other party rather than leaving it blank."""
    party = sd.parse_party_table(sd._TABLE.findall(PAGE)[1])
    assert party["dem"] == 0 and party["npa"] == 0 and party["oth"] == 0
    assert party["rep"] == 26275


def test_a_bad_cell_raises_drift():
    with pytest.raises(SchemaDrift, match="not a ballot count"):
        sd._count("lots")
    with pytest.raises(SchemaDrift, match="not a report date"):
        sd._stamp("last Tuesday")


def test_a_renamed_column_raises_drift():
    table = sd._TABLE.findall(PAGE)[0].replace("Walk-in Voters", "In Person", 1)
    with pytest.raises(SchemaDrift, match="absentee date table header"):
        sd.parse_dates_table(table)


def test_a_renamed_party_column_raises_drift():
    table = sd._TABLE.findall(PAGE)[1].replace("Party", "Affiliation", 1)
    with pytest.raises(SchemaDrift, match="party breakout header"):
        sd.parse_party_table(table)


def test_an_unrecognised_party_raises_drift():
    table = sd._TABLE.findall(PAGE)[1].replace(">DEM<", ">FEDERALIST<", 1)
    with pytest.raises(SchemaDrift, match="unrecognised party registration"):
        sd.parse_party_table(table)


# --------------------------------------------------------------------------
# The adapter
# --------------------------------------------------------------------------
def test_the_live_page_is_pending_not_an_error(monkeypatch):
    monkeypatch.setattr(sd, "get", lambda *a, **kw: PAGE_BYTES)
    with pytest.raises(NotYetPublished, match="no 2026 general-election section"):
        sd.SDScraper().fetch(2026, TODAY)


def test_a_404_is_not_yet_published(monkeypatch):
    def missing(url, **kwargs):
        raise Missing(f"SD: {url} returned 404")

    monkeypatch.setattr(sd, "get", missing)
    with pytest.raises(NotYetPublished):
        sd.SDScraper().fetch(2024, date(2024, 10, 20))


def test_the_url_carries_the_cycle():
    assert sd.PAGE.format(cycle=2026).endswith("2026-Election-Absentee-Data.aspx")
    assert "2026%20Election%20Information" in sd.PAGE.format(cycle=2026)


def test_2022_is_deliberately_not_an_archived_cycle():
    """Its page IS archived and fetches fine. It is refused because it is a
    DIFFERENT report -- `Military` for `UOCAVA`, a party table headed `Ballots
    Mailed | Mail Ballots Received | In-Person Voting`, and a 10/7 row South
    Dakota itself retracts -- and rule 3 says measure it, do not guess."""
    with pytest.raises(NotYetPublished, match="not one of the archived cycles"):
        sd.SDScraper().fetch_history(2022)
    assert sd.ARCHIVED_CYCLES == (2024,)


def test_adapter_identity():
    scraper = sd.SDScraper()
    assert (scraper.state, scraper.name, scraper.tier) == ("SD", "sd-sos", 1)


# --------------------------------------------------------------------------
# The archived 2024 weekly series, out of the Internet Archive
#
# The live host serves 2022 and 2024 as the same 67,597-byte soft-404 (verified
# 2026-09-08), so the archive is the only route. Every byte below is the real
# capture taken the day after the 2024 election.
# --------------------------------------------------------------------------
FIX = Path(__file__).parent / "fixtures" / "sd"
GEN24 = (FIX / "2024-General-Election-Absentee-Numbers-content.html").read_bytes()
CDX24 = (FIX / "cdx_2024_absentee.json").read_bytes()


@pytest.fixture
def archive(monkeypatch):
    def fake_get(url, *, state, filename, **kwargs):
        if url.startswith(sd.CDX_URL):
            return CDX24
        return GEN24

    monkeypatch.setattr(sd, "get", fake_get)


def test_the_2024_backfill_is_the_whole_weekly_curve(archive):
    result = sd.SDScraper().fetch_history(2024)
    assert [r.day for r in result.state_rows] == [
        date(2024, 9, 20), date(2024, 9, 27), date(2024, 10, 4),
        date(2024, 10, 11), date(2024, 10, 18), date(2024, 10, 25),
        date(2024, 11, 1),
    ]
    assert [r.ballots_total for r in result.state_rows] == [
        1_305, 11_193, 22_868, 40_135, 62_572, 97_333, 141_554
    ]


def test_south_dakota_publishes_no_counties_in_any_cycle(archive):
    """The defining fact about this source, and the archive path does not
    change it."""
    assert sd.SDScraper().fetch_history(2024).county_rows == []


def test_the_archived_party_breakout_is_south_dakotas_own(archive):
    """A snapshot of the latest date only, so the capture taken after the
    election carries 11/1's."""
    result = sd.SDScraper().fetch_history(2024)
    final = result.state_rows[-1]
    assert (final.party_dem, final.party_rep) == (35_862, 77_517)
    assert (final.party_npa, final.party_oth) == (27_607, 567)
    # IND + NPA are added together, LIB + OTH + No Labels likewise.
    assert final.party_dem + final.party_rep + final.party_npa + final.party_oth \
        == 141_553  # one ballot off SD's own total: the tables are hand-typed


def test_the_party_columns_are_not_transposed(archive):
    """South Dakota registers about two Republicans for every Democrat, and the
    absentee split follows. A D column larger than the R column here would be
    the transposition this report has shown elsewhere."""
    final = sd.SDScraper().fetch_history(2024).state_rows[-1]
    assert final.party_rep > final.party_dem
    assert 1.8 < final.party_rep / final.party_dem < 2.5


def test_mail_returned_stays_blank_on_the_archive_path_too(archive):
    """"Ballots Received" already includes walk-ins and SD publishes no
    mail-only count, so this is blank rather than a subtraction."""
    for row in sd.SDScraper().fetch_history(2024).state_rows:
        assert row.mail_returned is None
        assert row.inperson is not None


def test_an_archive_with_nothing_in_it_is_not_yet_published(monkeypatch):
    monkeypatch.setattr(sd, "archive_stamps", lambda cycle: [])
    with pytest.raises(NotYetPublished, match="nothing archived"):
        sd.SDScraper().fetch_history(2024)


def test_a_captures_as_of_is_its_own_stamp():
    assert sd._stamp_day("20241106004829") == date(2024, 11, 6)
    with pytest.raises(SourceError, match="not a Wayback timestamp"):
        sd._stamp_day("nope")


def test_the_run_date_filter_stays_live_on_the_archive_path(monkeypatch):
    """A capture stamped October 8 cannot publish November 1's weekly row."""
    monkeypatch.setattr(sd, "archive_stamps", lambda cycle: ["20241008000000"])
    monkeypatch.setattr(sd, "get", lambda url, **k: GEN24)
    result = sd.SDScraper().fetch_history(2024)
    assert max(r.day for r in result.state_rows) == date(2024, 10, 4)
