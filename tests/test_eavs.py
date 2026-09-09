"""The EAC survey rung.

⚠️ THE FIXTURE IS TRUNCATED AND THAT MATTERS FOR WHAT MAY BE ASSERTED.
`eavs_2024_sample.zip` carries the real 535-column header verbatim and every
jurisdiction for Idaho (44), New Mexico (33) and DC (1) -- so those three have
real statewide totals and are checked against them. Missouri, Montana, West
Virginia and Arizona are cut to a handful of rows each, kept for the SHAPE they
demonstrate. Their sums are fixture artefacts and nothing here asserts one.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from ev.adapters import eavs
from ev.adapters.base import NotYetPublished, SchemaDrift
from ev.registry import FALLBACKS, HISTORY_FALLBACKS, history_ladder, ladder
from ev.schema import TIER_SURVEY

FIXTURES = Path(__file__).parent / "fixtures" / "eavs"
DAY = date(2024, 11, 5)


@pytest.fixture(scope="module")
def archive() -> bytes:
    return (FIXTURES / "eavs_2024_sample.zip").read_bytes()


def parse(archive: bytes, state: str, cycle: int = 2024):
    return eavs.parse(eavs.rows(archive, state), state, cycle, DAY)


# ---------------------------------------------------------------- the states --

def test_a_state_whose_jurisdictions_are_its_counties_parses_whole(archive):
    """Idaho: 44 counties, and the total the SoS's own dashboard agrees with.

    408,407 against voteidaho.gov's 408,325 -- 0.02% on two independent chains.
    """
    result = parse(archive, "ID")
    assert len(result.county_rows) == 44
    assert len(result.state_rows) == 1
    row = result.state_rows[0]
    assert (row.ballots_total, row.mail_returned, row.inperson) == (
        408_407, 182_434, 225_973)
    # C1a is deliberately not published; see the constant in eavs.py.
    assert row.mail_requested is None


def test_a_county_is_placed_by_FIPS_even_when_its_name_lost_a_letter(archive):
    """⚠️ NEW MEXICO'S OWN SUBMISSION SPELLS IT `DONA ANA COUNTY`.

    The ñ is gone somewhere upstream, so the name matches no census entry while
    the code 35013 is exactly right. Matching on the name would have dropped a
    real county of 200,000 people over a diacritic; Rule 4 says the FIPS is the
    key and this is the row that proves why.
    """
    result = parse(archive, "NM")
    assert len(result.county_rows) == 33
    by_fips = {r.county_fips: r.county_name for r in result.county_rows}
    assert "35013" in by_fips
    # The CENSUS spelling is what gets published, not the survey's.
    assert by_fips["35013"].startswith("Do")
    assert by_fips["35013"] != "DONA ANA COUNTY"
    assert result.state_rows[0].ballots_total == 668_889


def test_a_jurisdiction_outside_any_county_withholds_every_county_row(archive):
    """⚠️ MISSOURI IS THE FILE BEING RIGHT, NOT THE FILE BEING BROKEN.

    `KANSAS CITY CITY` carries 2938000000 -- a PLACE code. Kansas City spans
    four counties and runs its own election board, so its ballots belong to no
    single county and the four it overlaps are each short an unknown number.

    Publishing the rest would understate Jackson County silently, which is worse
    than having no Jackson row at all. The statewide sum still INCLUDES Kansas
    City, so Missouri gets a total and no county table.
    """
    result = parse(archive, "MO")
    assert result.county_rows == []
    assert len(result.state_rows) == 1


def test_a_state_that_reported_neither_column_publishes_nothing(archive):
    """⚠️ MONTANA. All 56 counties report mail; none reports in-person early.

    An unguarded read would publish 432,394 as Montana's 2024 early vote when
    the truth is 432,394 PLUS an unreported number. That figure is a DENOMINATOR
    -- "share of its 2024 early vote" divides by it -- and one that is too small
    inflates every 2026 Montana reading for the whole season.

    Returning nothing is also what lets `backfill` fall through: it only asks
    whether a result is truthy, so a 56-row file of blank totals would have been
    recorded as a state WITH history.
    """
    result = parse(archive, "MT")
    assert not result
    assert result.county_rows == []
    assert result.state_rows == []


def test_columns_that_cannot_both_be_true_are_DRIFT(archive):
    """⚠️ ARIZONA, AND THIS IS WHY 'BOTH COLUMNS PRESENT' IS NOT THE GATE.

    Apache County reports 21,608 mail returned and 16,601 in-person early
    against 32,685 TOTAL VOTERS. Early ballots are a subset of all ballots, so
    the two columns are counting some of the same people twice -- in Arizona an
    early in-person voter returns an early ballot in person and lands in both.

    Every column is populated and every value is a plausible number. Only the
    survey's own identity catches it, which is Rule 3: do not guess a mapping.
    """
    with pytest.raises(SchemaDrift, match="against .* total voters"):
        parse(archive, "AZ")


def test_a_single_county_state_still_parses(archive):
    """DC has one jurisdiction and it is a real county FIPS."""
    result = parse(archive, "DC")
    assert [r.county_fips for r in result.county_rows] == ["11001"]
    assert result.state_rows[0].ballots_total == 242_194


# ------------------------------------------------------------- the blank rule --

def test_EAVS_own_codes_are_None_and_never_zero():
    for code in ("-88", "-99", ""):
        assert eavs.count(code, "x") is None
    assert eavs.count("0", "x") == 0
    assert eavs.count("12", "x") == 12


def test_an_unrecognised_negative_is_DRIFT_not_a_bucket():
    """Rule 3. -77 is not one of the two codes we know, so it is not any code."""
    with pytest.raises(SchemaDrift, match="unrecognised code"):
        eavs.count("-77", "x")


def test_a_half_answered_county_keeps_its_mail_figure_and_loses_its_total(archive):
    """⚠️ NOT `(mail or 0) + (early or 0)`.

    A county that answered one column has an UNKNOWN early vote, not a
    half-sized one. The mail number it did report is real and is kept.
    """
    rows = [dict(r) for r in eavs.rows(archive, "ID")]
    rows[0][eavs.INPERSON] = "-99"
    result = eavs.parse(rows, "ID", 2024, DAY)
    hurt = next(r for r in result.county_rows
                if r.county_fips == rows[0][eavs.FIPS_COL][:5])
    assert hurt.inperson is None
    assert hurt.ballots_total is None
    assert hurt.mail_returned is not None
    # And one blank column anywhere costs the STATE row, not just that county's.
    assert result.state_rows == []


def test_party_is_never_zero_because_EAVS_carries_none(archive):
    result = parse(archive, "ID")
    for row in result.county_rows:
        assert (row.party_dem, row.party_rep, row.party_oth, row.party_npa) == (
            None, None, None, None)


def test_there_is_no_such_thing_as_a_new_ballot_in_a_post_election_census(archive):
    result = parse(archive, "ID")
    assert all(r.ballots_new is None for r in result.county_rows)
    assert result.state_rows[0].ballots_new is None


def test_every_row_lands_on_election_day(archive):
    result = parse(archive, "ID")
    assert {r.day for r in result.county_rows} == {DAY}


# -------------------------------------------------------------- the ladder --

def test_EAVS_is_NOT_in_the_live_fallbacks():
    """⚠️ THE ONE THAT WOULD TAKE THE WHOLE SITE DOWN QUIETLY.

    `EAVSAdapter.fetch` raises NotYetPublished, and by Rule 1 that STOPS the
    ladder. In FALLBACKS it would sit above the aggregator and end the walk
    before the aggregator was ever asked -- for every state, every day, with no
    error anywhere to notice it by. Same shape as the bug that left fourteen
    states unwalked because "tracked" meant "we wrote a scraper".
    """
    assert not any("eavs" in spec for spec in FALLBACKS)
    assert any("eavs" in spec for spec in HISTORY_FALLBACKS)
    assert not any(a.name == eavs.NAME for a in ladder("WY"))


def test_the_history_ladder_puts_the_survey_above_the_aggregator():
    names = [a.name for a in history_ladder("WY")]
    assert names.index(eavs.NAME) < names.index("uf-election-lab")


def test_a_state_with_its_own_archive_never_reaches_the_survey():
    """Tier order alone is what protects a real state file from being replaced."""
    walk = history_ladder("NC")
    assert walk[0].tier == 1
    assert walk[0].name == "nc-sbe"
    assert walk.index(next(a for a in walk if a.name == eavs.NAME)) > 0


def test_the_survey_refuses_to_answer_a_live_cycle():
    with pytest.raises(NotYetPublished):
        eavs.EAVSAdapter(state="WY").fetch(2026, date(2026, 9, 9))


def test_the_survey_refuses_the_current_cycle_as_history():
    with pytest.raises(NotYetPublished, match="post-election survey"):
        eavs.history("WY", date.today().year)


def test_rows_say_they_came_from_the_EAC_and_carry_the_survey_tier(archive):
    """Both halves. The name alone was not enough -- Idaho first shipped these
    rows saying `eac-eavs` while claiming TIER_SCRAPER, which is the provenance
    lie the Arizona and Montana work refused to publish."""
    adapter = eavs.EAVSAdapter(state="ID")
    assert adapter.tier == TIER_SURVEY
    assert adapter.name == "eac-eavs"
    assert adapter.name != "id-sos"


# ---------------------------------------------------------------- the index --

INDEX = (
    b'<a href="/sites/default/files/2025-06/2024_EAVS_for_Public_Release_'
    b'nolabel_V1_csv.zip">2024 v1</a>'
    b'<a href="/sites/default/files/2026-02/2024_EAVS_for_Public_Release_'
    b'nolabel_V2_csv.zip">2024 v2</a>'
    b'<a href="/sites/default/files/2023-03/2022_EAVS_for_Public_Release_'
    b'nolabel_V1_CSV.zip">2022</a>'
)


def test_the_newest_release_wins_because_the_old_url_answers_forever():
    """2024 shipped twice. A pinned V1 URL keeps returning 200 with stale data."""
    assert eavs.release(INDEX, 2024).endswith("_V2_csv.zip")


def test_the_2022_extension_is_spelled_differently_and_is_still_found():
    """⚠️ `_CSV.zip` in 2022, `_csv.zip` in 2024. A case-sensitive pattern
    silently loses a whole cycle -- not an error, just an absent year."""
    assert eavs.release(INDEX, 2022).endswith("_V1_CSV.zip")


def test_a_cycle_the_EAC_has_not_posted_is_NotYetPublished():
    with pytest.raises(NotYetPublished, match="no EAVS release"):
        eavs.release(INDEX, 2018)


def test_a_relative_href_is_made_absolute():
    assert eavs.release(INDEX, 2024).startswith("https://www.eac.gov/")


def test_a_download_that_is_not_a_zip_is_DRIFT():
    with pytest.raises(SchemaDrift, match="not a readable zip"):
        eavs.rows(b"<html>rate limited</html>" * 10, "WY")


def test_a_zip_whose_columns_moved_is_DRIFT():
    import io
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("x.csv", "State_Abbr,FIPSCode\nWY,56001\n")
    with pytest.raises(SchemaDrift, match="missing columns"):
        eavs.rows(buf.getvalue(), "WY")


def test_the_member_is_found_by_suffix_not_by_name(archive):
    """The 2024 V1 zip wraps its CSV in a directory and V2 does not, so a name
    match would have broken on the release that replaced it."""
    import io
    import zipfile
    inner = zipfile.ZipFile(io.BytesIO(archive))
    body = inner.read(inner.namelist()[0])
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("some/nested/path/renamed.csv", body)
    assert len(eavs.rows(buf.getvalue(), "ID")) == 44
