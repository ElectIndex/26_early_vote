"""Alaska: the Division of Elections' Election Statistics block.

Four fixtures, all the real block copied verbatim from the live pages on
2026-09-06 -- `<summary>` through `</details>`, with the site's chrome left off:

* `election-results_24genr_statistics.html` -- the 2024 general, the one cycle
  whose block states its own as-of date ("Statistics include Early Vote through
  11/5/2024").
* `election-results_22genr_statistics.html` -- the 2022 general, whose block
  states no such thing and carries only an edit stamp from September 2024.
* `election-results_26prim_statistics.html` -- the August 2026 PRIMARY. Same
  shape, wrong election; it must never be published as the general.
* `election-results_26genr_no-statistics.html` -- the head of the live 2026
  general page, which has no Election Statistics block at all. That is the path
  this adapter takes every day until Alaska posts one.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from ev.adapters import ak
from ev.adapters.base import NotYetPublished, SchemaDrift, SourceError

FIX = Path(__file__).parent / "fixtures" / "ak"
GEN24 = (FIX / "election-results_24genr_statistics.html").read_bytes()
GEN22 = (FIX / "election-results_22genr_statistics.html").read_bytes()
PRIM26 = (FIX / "election-results_26prim_statistics.html").read_bytes()
GEN26 = (FIX / "election-results_26genr_no-statistics.html").read_bytes()

TODAY = date(2026, 9, 6)
AFTER_2024 = date(2024, 11, 20)


# --------------------------------------------------------------------------
# The 2024 general, parsed end to end
# --------------------------------------------------------------------------
def test_2024_general_is_alaskas_own_totals():
    (row,) = ak.parse(GEN24, 2024, AFTER_2024).state_rows
    assert row.day == date(2024, 11, 5)
    assert row.ballots_total == 167_263


def test_ballots_total_is_larger_than_the_two_method_buckets_by_the_questioned_pile():
    """A questioned ballot is counted but is not a mail-vs-in-person split.
    Folding it into `inperson` would overstate in-person early voting by
    15,138 ballots, so the total is Alaska's own and the parts do not add up
    to it -- on purpose."""
    (row,) = ak.parse(GEN24, 2024, AFTER_2024).state_rows
    assert row.mail_returned == 60_821
    assert row.inperson == 91_304
    assert row.ballots_total - row.mail_returned - row.inperson == 15_138


def test_mail_requested_is_the_absentee_family_issued():
    (row,) = ak.parse(GEN24, 2024, AFTER_2024).state_rows
    # 31 fax + 59,158 mail + 0 FWAB + 11,976 online + 1,109 special needs.
    assert row.mail_requested == 72_274


def test_alaska_registers_by_party_but_this_report_does_not_break_it_out():
    (row,) = ak.parse(GEN24, 2024, AFTER_2024).state_rows
    assert (row.party_dem, row.party_rep, row.party_npa, row.party_oth) == (
        None, None, None, None
    )


def test_alaska_is_statewide_only_and_that_is_not_an_error():
    """The near-daily report is keyed by the forty state HOUSE DISTRICTS, which
    do not nest into boroughs. Publishing no county rows is the honest outcome;
    inventing a borough mapping would not be."""
    result = ak.parse(GEN24, 2024, AFTER_2024)
    assert result.county_rows == []
    assert result.demo_rows == []
    assert len(result.state_rows) == 1


# --------------------------------------------------------------------------
# The election id is the gate
# --------------------------------------------------------------------------
def test_the_primary_can_never_be_published_as_the_general():
    with pytest.raises(NotYetPublished, match="26PRIM"):
        ak.parse(PRIM26, 2026, TODAY)


def test_the_2024_general_is_refused_when_asked_for_as_2026():
    with pytest.raises(NotYetPublished, match="24GENR, not 26GENR"):
        ak.parse(GEN24, 2026, TODAY)


def test_the_2026_general_has_no_statistics_block_yet():
    """The path that runs every day until Alaska posts one. It must STOP the
    ladder rather than fall through to a source that would invent a zero."""
    with pytest.raises(NotYetPublished, match="no Election Statistics block yet"):
        ak.parse(GEN26, 2026, TODAY)


def test_a_block_that_names_no_election_is_drift():
    page = GEN24.decode().replace("24GENR Totals", "Totals")
    with pytest.raises(SchemaDrift, match="names no election"):
        ak.parse(page.encode(), 2024, AFTER_2024)


# --------------------------------------------------------------------------
# Dating: Alaska's own note outranks the page's edit stamp
# --------------------------------------------------------------------------
def test_the_as_of_date_comes_from_alaskas_own_note_not_the_edit_stamp():
    """The 2024 block was last edited on 2025-01-29 and says the numbers run
    through 11/5/2024. Stamping the edit date would put the cycle's final row
    85 days the wrong side of Election Day."""
    assert ak.report_day(
        "24GENR Totals Statistics include Early Vote through 11/5/2024. "
        "Table last updated January 29, 2025 at 3:02 pm.", 2024
    ) == date(2024, 11, 5)


def test_a_2022_style_block_with_only_an_edit_stamp_is_not_published():
    """2022's block says nothing about what its numbers cover and was last
    edited in September 2024. There is no honest date to stamp, so nothing is
    published rather than something dated by guesswork."""
    with pytest.raises(NotYetPublished, match="outside the window"):
        ak.parse(GEN22, 2022, date(2022, 11, 20))


def test_the_2022_block_is_the_11_30_certified_final_not_an_election_day_snapshot():
    """The refusal above is about SEMANTICS, not a missing date -- and the block
    itself proves it. Its Download Report link names
    `Combined Ballot Count Report_11.30.2022.pdf`, so these are the numbers as
    they stood twenty-two days AFTER the election, once every late absentee had
    arrived. 2024's block, by contrast, states `Early Vote through 11/5/2024`.
    Publishing the two side by side on a days-to-election axis would compare a
    post-count final against an Election-Day snapshot."""
    assert b"Combined%20Ballot%20Count%20Report_11.30.2022.pdf" in GEN22
    assert b"Statistics include" not in GEN22
    assert b"Statistics include Early Vote through 11/5/2024" in GEN24


def test_the_two_cycles_report_the_same_measurement(monkeypatch):
    """The 2022 PDF series and this HTML block are one thing: the block's own
    totals are the PDF's TOTALS row. That is what makes the archived
    `Combined Ballot Count Report_M.D.2022.pdf` files a recoverable 2022 daily
    curve -- see the module docstring for why they are not read yet."""
    monkeypatch.setattr(ak, "report_day", lambda block, cycle: date(2022, 11, 30))
    (row,) = ak.parse(GEN22, 2022, date(2022, 12, 1)).state_rows
    assert row.day == date(2022, 11, 30)
    assert row.ballots_total == 100_877      # Alaska's own Total Ballots Received
    assert row.mail_requested == 49_002 + 5_948 + 49 + 815 + 24
    assert row.mail_returned == 41_348 + 3_948 + 37 + 815 + 18
    assert row.inperson == 37_559 + 9_143    # early vote + in-person absentee
    # Questioned ballots are counted and are in neither method bucket.
    assert row.ballots_total - row.mail_returned - row.inperson == 8_009


def test_a_block_with_no_date_at_all_is_drift():
    page = GEN24.decode()
    page = page.replace("Statistics include Early Vote through 11/5/2024.", "")
    page = page.replace("Table last updated January 29, 2025 at 3:02 pm.", "")
    with pytest.raises(SchemaDrift, match="carries no as-of date"):
        ak.parse(page.encode(), 2024, AFTER_2024)


def test_a_block_dated_after_the_run_falls_through_rather_than_publishing():
    with pytest.raises(SourceError, match="after the run date"):
        ak.parse(GEN24, 2024, date(2024, 11, 1))


# --------------------------------------------------------------------------
# The table proves itself against Alaska's own totals
# --------------------------------------------------------------------------
def test_ballot_types_that_do_not_sum_to_alaskas_totals_are_drift():
    page = GEN24.decode().replace(">51,212<", ">51,213<")
    with pytest.raises(SchemaDrift, match="but Alaska's own totals are"):
        ak.parse(page.encode(), 2024, AFTER_2024)


def test_a_ballot_type_we_cannot_name_is_drift_not_a_guess():
    page = GEN24.decode().replace(">Online Delivery<", ">Ranked Drop Box<")
    with pytest.raises(SchemaDrift, match="unrecognised ballot type"):
        ak.parse(page.encode(), 2024, AFTER_2024)


def test_a_renamed_column_is_drift():
    page = GEN24.decode().replace("<th>Number Received</th>", "<th>Received</th>")
    with pytest.raises(SchemaDrift, match="ballot-type table header"):
        ak.parse(page.encode(), 2024, AFTER_2024)


def test_every_2026_primary_ballot_type_is_already_recognised():
    """The primary's block is the newest layout Alaska has published, so every
    label in it must already map -- otherwise the general will drift on day one."""
    start, end = ak.section(PRIM26.decode())
    for label, _issued, _received in ak.ballot_types(PRIM26.decode()[start:end]):
        assert label.lower() in ak.UNSPLIT_TYPES or ak.bucket(label) is not None


# --------------------------------------------------------------------------
# Adapter wiring
# --------------------------------------------------------------------------
def test_adapter_identity():
    scraper = ak.AKScraper()
    assert (scraper.state, scraper.name, scraper.tier) == ("AK", "ak-doe", 1)


def test_the_url_is_built_from_the_cycle():
    assert ak.PAGE.format(cycle=26 % 100).endswith("?id=26genr")
    assert ak.PAGE.format(cycle=2024 % 100).endswith("?id=24genr")


def test_fetch_reads_the_page_and_parses_it(monkeypatch):
    monkeypatch.setattr(ak.AKScraper, "_load", lambda self, cycle, **k: GEN24)
    (row,) = ak.AKScraper().fetch(2024, AFTER_2024).state_rows
    assert row.ballots_total == 167_263


def test_history_refuses_the_current_cycle():
    with pytest.raises(NotYetPublished, match="not an archived cycle"):
        ak.AKScraper().fetch_history(date.today().year)


def test_a_non_page_response_falls_through():
    with pytest.raises(SourceError, match="did not come back as a page"):
        ak.parse(b'{"error": "nope"}', 2026, TODAY)
