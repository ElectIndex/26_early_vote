"""Maine: the SoS Statewide Absentee Voter Data File.

Two real fixtures, because Maine changed the file's layout between cycles and
both vintages have to keep parsing:

* `2024-11-05_ab_voter_file_sample.txt` -- the 21-column 2024 general layout,
  every record for the towns of Alna and Alton verbatim, plus Maine's own
  trailer totals for the whole state. Carries all six of Maine's party codes
  including "NL" (No Labels), which the shared vocabulary does not know.
* `2026-06-09_ab_voter_file_primary_sample.txt` -- the 23-column 2026 layout,
  from the June primary. This is the file that is actually posted today, and it
  is the reason `fetch` has to check which election a file is for.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from ev.adapters import me
from ev.adapters._net import Missing
from ev.adapters.base import NotYetPublished, SchemaDrift, SourceError

FIXTURES = Path(__file__).parent / "fixtures" / "me"
GENERAL_2024 = FIXTURES / "2024-11-05_ab_voter_file_sample.txt"
PRIMARY_2026 = FIXTURES / "2026-06-09_ab_voter_file_primary_sample.txt"
INDEX_EXCERPT = FIXTURES / "voter-data-index-excerpt.html"

ELECTION_DAY_2024 = date(2024, 11, 5)


@pytest.fixture(scope="module")
def body():
    return GENERAL_2024.read_bytes()


@pytest.fixture(scope="module")
def gen2024(body):
    return me.parse(body, 2024, ELECTION_DAY_2024)


@pytest.fixture(scope="module")
def final(gen2024):
    """The last day of the curve -- the cycle's finished position."""
    return gen2024.state_rows[-1]


def _mutate(raw: bytes, record: int, role: str, value: str) -> bytes:
    """Rewrite one field of one record, so drift tests run on the real file."""
    lines = raw.decode("utf-8").split("\n")
    index, width = me._header(lines[0])
    seen = 0
    for position, line in enumerate(lines):
        cells = line.split("|")
        if position == 0 or len(cells) != width:
            continue
        if seen == record:
            cells[index[role]] = value
            lines[position] = "|".join(cells)
            break
        seen += 1
    return "\n".join(lines).encode("utf-8")


# --------------------------------------------------------------------------
# Party: unenrolled is NOT "other"
# --------------------------------------------------------------------------
def test_unenrolled_voters_land_in_npa_not_oth(final):
    """Maine's "U" is Unenrolled -- a voter who declined a party, not a member of
    a third one. Alna and Alton return 65 of them, and they must not be swept in
    with the Greens and Libertarians."""
    assert final.party_npa == 65
    assert final.party_oth == 20  # Green 11 + No Labels 7 + Libertarian 2


def test_maines_party_codes_all_resolve_and_sum_to_the_ballot_total(final):
    """Every accepted ballot is attributed, so the four buckets are the total.
    A code that fell through to None would show up here as a shortfall."""
    assert (final.party_dem, final.party_rep) == (123, 109)
    assert final.party_dem + final.party_rep + final.party_npa + final.party_oth \
        == final.ballots_total == 317


def test_no_labels_is_expanded_before_normalising():
    """Maine invented the code "NL" for the No Labels party in 2024 and
    normalize.party has never heard of it -- but it does know "No Labels", so
    Maine's own legend is applied first. Without that expansion this file raises
    SchemaDrift and Maine goes dark for the whole cycle."""
    from ev.normalize import party as normalize_party

    assert normalize_party("NL") is None       # the shared vocabulary's gap
    assert me._party("NL") == "oth"            # closed by Maine's legend
    assert me._party("U") == "npa"
    assert me._party("D") == "dem"
    assert me._party("R") == "rep"
    assert me._party("") is None               # stated as blank, not guessed


def test_unknown_party_code_raises_drift(body):
    with pytest.raises(SchemaDrift, match="unrecognised party code"):
        me.parse(_mutate(body, 0, "party", "ZZ"), 2024, ELECTION_DAY_2024)


# --------------------------------------------------------------------------
# Maine reports by municipality, so there are no county rows
# --------------------------------------------------------------------------
def test_no_county_rows_are_emitted(gen2024):
    """The file's geography is "MUNICIPALITY" and 66 of Maine's 533 of them are
    unorganized-territory townships no crosswalk resolves. Publishing a guessed
    county would put real ballots in the wrong place on the map, so Maine is a
    statewide line only."""
    assert gen2024.county_rows == []
    assert gen2024.state_rows  # ... but it is not an empty adapter


# --------------------------------------------------------------------------
# Method: voting at the clerk's counter is in-person early voting
# --------------------------------------------------------------------------
def test_clerks_presence_ballots_are_in_person_and_the_rest_are_mail(final):
    """Maine's "VP" return code is a ballot voted at the counter; a dropbox or
    hand delivery is still a mail ballot coming back, whoever carried it."""
    assert final.inperson == 142
    assert final.mail_returned == 175
    assert final.inperson + final.mail_returned == final.ballots_total


def test_requested_counts_every_application_including_the_rejected(final):
    """324 ballots were requested in these two towns and 317 came back accepted.
    The seven rejected ones were still requested, so they belong in that number
    and not in the returned one."""
    assert final.mail_requested == 324
    assert final.ballots_total == 317


def test_rejected_ballots_are_not_counted_as_cast(body):
    """Flipping one accepted ballot to REJ must lower the total by exactly one."""
    drifted = me.parse(_mutate(body, 0, "status", "REJ"), 2024, ELECTION_DAY_2024)
    assert drifted.state_rows[-1].ballots_total == 316


def test_unknown_status_raises_drift(body):
    """Maine swapped ACC/REJ for ACT/ACU/ACH/PEN/REJ/RNC between cycles, so a
    status we have never seen means the vocabulary moved again -- not that the
    ballot was rejected."""
    with pytest.raises(SchemaDrift, match="unrecognised ballot status"):
        me.parse(_mutate(body, 0, "status", "MAYBE"), 2024, ELECTION_DAY_2024)


# --------------------------------------------------------------------------
# The curve
# --------------------------------------------------------------------------
def test_the_whole_daily_curve_comes_out_of_one_download(gen2024):
    """Every ballot carries its own return date, so a single file rebuilds the
    series and a missed run costs nothing."""
    days = [row.day for row in gen2024.state_rows]
    assert days[-1] == ELECTION_DAY_2024
    assert days == sorted(days)
    assert len(set(days)) == len(days)                       # no duplicate days
    assert all(b.day.toordinal() - a.day.toordinal() == 1    # and no gaps
               for a, b in zip(gen2024.state_rows, gen2024.state_rows[1:]))


def test_the_curve_is_cumulative_and_never_goes_backwards(gen2024):
    totals = [row.ballots_total for row in gen2024.state_rows]
    assert totals == sorted(totals)
    assert sum(row.ballots_new for row in gen2024.state_rows) == totals[-1]


def test_a_keying_typo_cannot_stretch_the_axis(gen2024):
    """The real 2024 file contains a ballot stamped 1520-10-10. The published
    axis is capped at MAX_SPAN_DAYS before Election Day and anything older is
    folded into the first day, rather than drawing five centuries of blanks."""
    assert len(gen2024.state_rows) == me.MAX_SPAN_DAYS + 1
    assert gen2024.state_rows[0].day == date(2024, 7, 8)


def test_as_of_truncates_the_curve(body):
    partial = me.parse(body, 2024, date(2024, 10, 15))
    assert partial.state_rows[-1].day == date(2024, 10, 15)
    assert partial.state_rows[-1].ballots_total == 72
    assert partial.state_rows[-1].inperson == 29
    assert partial.state_rows[-1].mail_returned == 43


# --------------------------------------------------------------------------
# Both layouts, and telling the elections apart
# --------------------------------------------------------------------------
def test_both_published_layouts_are_matched_by_header_name():
    """21 columns in 2022/2024, 23 in 2026, with the party column at a different
    index in each. Matching by position would have silently read the ward out of
    one of them."""
    old = GENERAL_2024.read_text(encoding="utf-8").split("\n")[0]
    new = PRIMARY_2026.read_text(encoding="utf-8").split("\n")[0]
    assert me._header(old) == ({"municipality": 0, "party": 4, "requested": 11,
                                "return_method": 15, "returned": 16, "status": 18}, 21)
    assert me._header(new) == ({"municipality": 0, "party": 3, "requested": 13,
                                "return_method": 16, "returned": 17, "status": 20}, 23)


def test_renamed_column_raises_drift(body):
    lines = body.decode("utf-8").split("\n")
    lines[0] = lines[0].replace("|P|", "|PTY|")
    with pytest.raises(SchemaDrift, match="header is missing"):
        me.parse("\n".join(lines).encode("utf-8"), 2024, ELECTION_DAY_2024)


def test_the_posted_primary_file_is_not_the_generals_file():
    """Maine posts one file for whatever election is next, so for most of 2026
    the link is the June primary's. Its request dates fall five months before the
    general's window opens, which is 'not published yet' and not an error."""
    with pytest.raises(NotYetPublished, match="for another election"):
        me.parse(PRIMARY_2026.read_bytes(), 2026, date(2026, 9, 5))


def test_the_generals_own_file_passes_the_same_check(body):
    """The gate has to let the right file through: 99.7% of the real 2024
    general file's request dates land inside its window."""
    assert me.parse(body, 2024, ELECTION_DAY_2024).state_rows


# --------------------------------------------------------------------------
# Finding the file
# --------------------------------------------------------------------------
def test_the_data_file_link_is_picked_out_of_the_index_page():
    """The file's URL is stamped by hand ("6-9-26 AB Voter File Final.txt") and
    cannot be constructed, so it is read off the Voter Data page. The layout
    link one line above carries almost the same anchor text and must not win."""
    found = me.links(INDEX_EXCERPT.read_bytes())
    assert found == [
        "https://www.maine.gov/sos/sites/maine.gov.sos/files/inline-files/"
        "6-9-26%20AB%20Voter%20File%20Final.txt"
    ]


def test_index_page_without_the_link_is_drift(monkeypatch):
    monkeypatch.setattr(me, "get", lambda url, **kw: b"<html><body>redesigned</body></html>")
    with pytest.raises(SchemaDrift, match="no absentee data file link"):
        me.MEScraper().fetch(2026, date(2026, 9, 5))


def test_missing_index_page_is_a_source_error(monkeypatch):
    def missing(url, **kwargs):
        raise Missing(f"ME: {url} returned 404")

    monkeypatch.setattr(me, "get", missing)
    with pytest.raises(SourceError):
        me.MEScraper().fetch(2026, date(2026, 9, 5))


def test_html_where_the_data_file_should_be_is_a_source_error():
    with pytest.raises(SourceError, match="came back as HTML"):
        me.parse(b"<!DOCTYPE html><html><head></head><body>nope</body></html>" * 4,
                 2026, date(2026, 9, 5))


def test_fetch_walks_index_then_file(monkeypatch):
    """End to end with the network stubbed: the index gives the URL, the URL
    gives the primary's file, and 2026 is therefore still pending."""
    pages = {
        me.INDEX_URL: INDEX_EXCERPT.read_bytes(),
        "https://www.maine.gov/sos/sites/maine.gov.sos/files/inline-files/"
        "6-9-26%20AB%20Voter%20File%20Final.txt": PRIMARY_2026.read_bytes(),
    }
    monkeypatch.setattr(me, "get", lambda url, **kw: pages[url])
    with pytest.raises(NotYetPublished, match="for another election"):
        me.MEScraper().fetch(2026, date(2026, 9, 5))


def test_adapter_identity():
    scraper = me.MEScraper()
    assert (scraper.state, scraper.name, scraper.tier) == ("ME", "me-sos", 1)
