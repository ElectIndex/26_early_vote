"""Idaho: the SoS absentee & early-voting tracker over Datawrapper charts.

Fixtures, all downloaded live 2026-09-08:

* `tracker_2026_primary.html` -- the stable `.gov` index, truncated to the only
  lines this module reads, all of them verbatim: the heading and the six chart
  references.
* `tracker_2026_general.html` -- the same file with `Primary` changed to
  `General`. **Synthetic, and labelled so in the fixture itself.** No general
  tracker has been published in this cycle, and a gate that nothing can ever
  pass is not a tested gate.
* `chart_jshNw_stub.html` -- the 241-byte stub `/{id}/` serves, naming v51.
* `chart_jshNw_51.csv` -- the county turnout dataset, verbatim, 44 counties.
* `chart_hGfEl_39.csv` -- a SIBLING chart from the same page (legislative
  district by party). It is here so the header-based pick is tested against a
  real neighbour rather than an invented one.

The first two tests are `coverage-research.md`'s objection and the version trap,
both enforced.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from ev.adapters import id as idaho
from ev.adapters.base import NotYetPublished, SchemaDrift
from ev.schema import TIER_SCRAPER, TIER_SURVEY

FIXTURES = Path(__file__).parent / "fixtures" / "id"


def fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


@pytest.fixture
def county_rows() -> list[dict[str, str]]:
    import csv, io
    return list(csv.DictReader(io.StringIO(
        fixture("chart_jshNw_51.csv").decode("utf-8-sig")
    )))


# --------------------------------------------------------------- the gate

def test_a_tracker_still_showing_the_primary_is_refused():
    """THE WHOLE REASON THIS ADAPTER IS SAFE.

    On 2026-09-08 -- four months after the May primary -- the live page still
    read "2026 Primary Election" and the datasets behind it still carried
    `last-modified: Wed, 20 May 2026`. A run that trusted the page would have
    stamped May's primary absentee counts with September's date under the 2026
    general, which is the exact bug `mt.py` was built to avoid and `az.py` was
    fixed for.
    """
    with pytest.raises(NotYetPublished) as exc:
        idaho.election(fixture("tracker_2026_primary.html"), 2026)
    assert "2026 Primary" in str(exc.value)
    assert "2026 General" in str(exc.value)


def test_the_general_passes_the_same_gate():
    # Synthetic fixture -- see the module docstring. Returns None, raises nothing.
    assert idaho.election(fixture("tracker_2026_general.html"), 2026) is None


def test_last_cycles_general_is_refused_too():
    # The gate is on the YEAR as well as the kind. A 2024 general tracker left
    # up in 2026 is the same trap wearing the right word.
    stale = fixture("tracker_2026_general.html").replace(b"2026 General", b"2024 General")
    with pytest.raises(NotYetPublished):
        idaho.election(stale, 2026)


def test_an_unreadable_heading_is_DRIFT_not_absence():
    # SchemaDrift falls THROUGH to the aggregator; NotYetPublished stops the
    # ladder. A page that answered and that we could not parse is a fact about
    # our parser, so it must fall through rather than silence the state.
    with pytest.raises(SchemaDrift):
        idaho.election(b"<html><h2>Absentee Ballot Stats</h2></html>", 2026)


def test_the_2024_page_is_a_different_page_and_a_different_chart_vendor():
    """Why no 2024 curve can be had from this module's live path.

    `tracker_2024_general_wayback.html` is the SAME URL as `TRACKER`, as the
    Internet Archive captured it at 06:17 UTC on Election Day 2024, trimmed to
    verbatim lines. Two things in it end the question:

    * the heading is a different shape. It reads "Absentee Stats for the 2024
      Election Year" and puts the election in an Elementor TAB -- "General
      Election - Nov. 5" / "Primary Election - May 21" -- so the one gate this
      module has, which is the heading naming the election, has nothing to read.
    * the charts are TABLEAU, not Datawrapper: `AbsenteeNovember2024/Dashboard`
      for the general and `absentee_16978441327890/AbsenteeStats` for the
      primary. Measured 2026-09-09, Datawrapper chart `jshNw` version 1 carries
      `last-modified: Thu, 09 Apr 2026` and version 60 is a 404 -- so those
      charts were created in 2026 and NO version of them holds 2024 data.

    This fixture exists so nobody re-runs that experiment, and so that pointing
    `election()` or `chart_ids()` at an archived 2024 page fails loudly instead
    of quietly producing something.
    """
    page = fixture("tracker_2024_general_wayback.html")
    assert b"Absentee Stats for the 2024 Election Year" in page
    assert b"General Election - Nov. 5" in page
    with pytest.raises(SchemaDrift):
        idaho.election(page, 2024)

    assert b"AbsenteeNovember2024" in page
    assert b"dwcdn.net" not in page   # the comment says the word; the page has no chart
    with pytest.raises(SchemaDrift):
        idaho.chart_ids(page)


# ------------------------------------------------------- the version trap

def test_the_version_comes_from_the_stub_and_is_never_pinned():
    """`/{id}/{version}/dataset.csv` with a stale version NEVER errors.

    coverage-research.md measured it: /HCDQQ/1/ served Ada 1,353, /HCDQQ/17/
    served Ada 10,644, /HCDQQ/23/ served Ada 18,868. All three returned 200.
    So the only safe read is to resolve the stub every run, and the stub is the
    single source of the number.
    """
    stub = fixture("chart_jshNw_stub.html")
    assert b"jshNw/51" in stub
    assert len(stub) < 512, "the stub is a redirect, not a page"
    # And nothing in the module hardcodes a version anywhere.
    source = (Path(idaho.__file__)).read_text()
    body = source.split('"""', 2)[-1]  # skip the docstring, which quotes the trap
    assert "/51/" not in body and "/23/" not in body


def test_charts_are_discovered_from_the_page_not_hardcoded():
    ids = idaho.chart_ids(fixture("tracker_2026_primary.html"))
    assert ids == ["aCECg", "PJZGl", "UDG7E", "hGfEl", "jshNw", "HCDQQ"]
    # Page order, deduped, and NOT sorted -- the survey's objection was that the
    # ids are cycle-specific, so the list must come out of the page every time.
    assert len(set(ids)) == len(ids)


def test_a_page_with_no_charts_is_drift():
    with pytest.raises(SchemaDrift):
        idaho.chart_ids(b"<html><body>nothing embedded</body></html>")


# ------------------------------------------------------------- the parse

def test_the_county_dataset_is_picked_by_its_HEADER(county_rows):
    import csv, io
    sibling = list(csv.DictReader(io.StringIO(
        fixture("chart_hGfEl_39.csv").decode("utf-8-sig")
    )))
    assert idaho._header(county_rows) == idaho.TURNOUT_HEADER
    # A real neighbour from the same page, which must not be mistaken for it.
    assert idaho._header(sibling) != idaho.TURNOUT_HEADER
    assert idaho._header(sibling)[0] == "legislativedistrict"


def test_every_idaho_county_is_published_and_keyed_by_fips(county_rows):
    result = idaho.parse(county_rows, date(2026, 5, 20), 2026)
    assert len(result.county_rows) == idaho.EXPECTED_COUNTIES == 44
    assert all(len(r.county_fips) == 5 and r.county_fips.startswith("16")
               for r in result.county_rows)
    ada = next(r for r in result.county_rows if r.county_fips == "16001")
    assert ada.county_name == "Ada County"
    assert (ada.ballots_total, ada.mail_returned, ada.inperson) == (29661, 16750, 12911)


def test_no_party_column_is_ever_written(county_rows):
    """Idaho registers by party and this source does not publish registration.

    The one party column it does publish is a PRIMARY BALLOT CHOICE
    (`AbsPartySelected` on the sibling chart says so in its own name). Writing
    it as party_dem/party_rep would be the ok.py and az.py mistake again, and
    the blank rule says the answer is None -- never 0.
    """
    result = idaho.parse(county_rows, date(2026, 5, 20), 2026)
    for row in result.county_rows + result.state_rows:
        assert row.party_dem is None
        assert row.party_rep is None
        assert row.party_oth is None
        assert row.party_npa is None


def test_the_statewide_row_is_the_counties_summed(county_rows):
    result = idaho.parse(county_rows, date(2026, 5, 20), 2026)
    assert len(result.state_rows) == 1
    state = result.state_rows[0]
    assert state.ballots_total == sum(r.ballots_total or 0 for r in result.county_rows)
    assert state.mail_returned == sum(r.mail_returned or 0 for r in result.county_rows)
    assert state.inperson == sum(r.inperson or 0 for r in result.county_rows)
    # Verified against the live fixture on 2026-09-08.
    assert (state.ballots_total, state.mail_returned, state.inperson) == (87204, 46381, 40823)
    assert state.mail_requested == 56084
    assert (state.mail_returned or 0) + (state.inperson or 0) == state.ballots_total


def test_a_partial_county_set_publishes_NO_statewide_row(county_rows):
    """A total over 43 of 44 counties looks exactly like an Idaho turnout figure.

    Same rule as mt.py and tx.py. The counties still publish; the state row does
    not, because there is no way to tell a short total from a real one on the page.
    """
    result = idaho.parse(county_rows[:-1], date(2026, 5, 20), 2026)
    assert len(result.county_rows) == 43
    assert result.state_rows == []


def test_the_sources_own_identity_is_checked(county_rows):
    """`total_voted` must equal `Returned + early_voting`, and it does today.

    If that stops holding, total_voted has started counting something else and
    every ballots_total here is wrong -- worth a loud failure, not a plausible
    number.
    """
    broken = [dict(r) for r in county_rows]
    broken[0]["total_voted"] = str(int(broken[0]["total_voted"]) + 1)
    with pytest.raises(SchemaDrift) as exc:
        idaho.parse(broken, date(2026, 5, 20), 2026)
    assert "total_voted" in str(exc.value)


def test_an_unknown_county_is_drift_not_a_dropped_row(county_rows):
    broken = [dict(r) for r in county_rows]
    broken[0]["ResCountyDesc"] = "Nonesuch"
    with pytest.raises(SchemaDrift):
        idaho.parse(broken, date(2026, 5, 20), 2026)


def test_a_non_numeric_count_is_drift(county_rows):
    broken = [dict(r) for r in county_rows]
    broken[0]["Returned"] = "n/a"
    with pytest.raises(SchemaDrift):
        idaho.parse(broken, date(2026, 5, 20), 2026)


# ------------------------------------------------------------- the adapter

def test_adapter_identity():
    scraper = idaho.IDScraper()
    assert scraper.state == "ID"
    assert scraper.name == "id-sos"
    assert scraper.tier == TIER_SCRAPER


def test_the_datawrapper_route_still_refuses_a_past_cycle(monkeypatch):
    """GUARD PARITY -- but not for the reason that used to be written here.

    The old refusal said "a version carries no date, only an ordinal". That is
    FALSE: `last-modified` dates a Datawrapper version, monotonically, and
    `jshNw` v1 reads Thu, 09 Apr 2026 while v51 reads Wed, 20 May 2026. What
    actually ends the 2024 question is that v1 is dated 2026 AT ALL -- the
    charts did not exist during the 2024 general, so there is no version of them
    to walk. `fetch_history` therefore reads EAVS, and nothing in it touches the
    tracker or the CDN.
    """
    def forbidden(url, **kwargs):
        assert "dwcdn.net" not in url and "voteidaho.gov" not in url, url
        raise AssertionError(f"the history path fetched {url}")

    monkeypatch.setattr(idaho, "get", forbidden)
    with pytest.raises(NotYetPublished):
        idaho.IDScraper().fetch_history(2026)

# ==========================================================================
# THE PAST CYCLES -- the EAC's Election Administration and Voting Survey
# ==========================================================================
#
# Two more fixtures, both downloaded live 2026-09-09:
#
# * `eac_datasets_index.html` -- the EAC's dataset index, truncated to every
#   `nolabel` EAVS anchor it carries, all verbatim. 2024 is linked TWICE (V1 and
#   V2), which is the whole reason the release is resolved from this page rather
#   than pinned.
# * `eavs_2024_ID.zip` -- the real 2024 V2 national CSV cut down to its verbatim
#   535-column header and Idaho's 44 jurisdiction rows, re-zipped. (The rows are
#   re-serialised rather than sliced by line: EAVS comment fields carry embedded
#   newlines, so the 6,461-record file is 17,013 physical lines and a line-based
#   trim would have split records. Every VALUE is verbatim.)

def test_the_newest_release_wins_and_a_pinned_one_would_not():
    """2024 shipped twice. A URL pinned to V1 would answer 200 forever.

    Same family as the Datawrapper version trap above, and the same answer:
    resolve it from the index on every run.
    """
    index = fixture("eac_datasets_index.html")
    assert idaho.eavs_release(index, 2024).endswith(
        "/2026-02/2024_EAVS_for_Public_Release_nolabel_V2_csv.zip")
    assert b"2025-06/2024_EAVS_for_Public_Release_nolabel_V1_csv.zip" in index
    # ...and it is absolutised against the EAC's host, because the page is
    # relative for its own uploads and absolute for some older ones.
    assert idaho.eavs_release(index, 2024).startswith("https://www.eac.gov/")


def test_2022_is_found_even_though_it_SHOUTS_its_extension():
    """`_CSV.zip` in 2022, `_csv.zip` in 2024.

    A case-sensitive pattern loses a whole cycle here and never says so, which
    is the "a path check is not evidence about another cycle" trap in the
    shape it actually appears in.
    """
    index = fixture("eac_datasets_index.html")
    assert b"2022_EAVS_for_Public_Release_nolabel_V1_CSV.zip" in index
    assert idaho.eavs_release(index, 2022).endswith(
        "2022_EAVS_for_Public_Release_nolabel_V1_CSV.zip")


def test_a_cycle_the_EAC_has_not_published_is_NOT_YET_PUBLISHED():
    with pytest.raises(NotYetPublished) as exc:
        idaho.eavs_release(fixture("eac_datasets_index.html"), 2018)
    assert "2018" in str(exc.value)


# --------------------------------------------------------------- the parse

@pytest.fixture
def eavs_rows() -> list[dict[str, str]]:
    return idaho.eavs_rows(fixture("eavs_2024_ID.zip"), "ID")


def test_the_zip_member_is_found_by_suffix_not_by_name(eavs_rows):
    """V1 wraps its CSV in a directory and V2 does not.

    Matching the member on its name would have broken on exactly the release
    that replaced the one it was written against.
    """
    assert len(eavs_rows) == 44
    assert {r["State_Abbr"] for r in eavs_rows} == {"ID"}


def test_idahos_2024_county_final_is_44_counties_on_election_day(eavs_rows):
    result = idaho.eavs_parse(eavs_rows, 2024, date(2024, 11, 5))
    assert len(result.county_rows) == idaho.EXPECTED_COUNTIES == 44
    assert {r.day for r in result.county_rows} == {date(2024, 11, 5)}
    assert all(len(r.county_fips) == 5 and r.county_fips.startswith("16")
               for r in result.county_rows)

    ada = next(r for r in result.county_rows if r.county_fips == "16001")
    assert ada.county_name == "Ada County"
    assert (ada.ballots_total, ada.mail_returned, ada.inperson) == (
        142_493, 61_398, 81_095)

    state = result.state_rows[0]
    # EAVS C1b + F1f. Idaho's own Secretary of State dashboard for the same
    # election reports 182,010 returned and 226,315 early, 408,325 together --
    # 0.23%, 0.15% and 0.02% away. Two independent chains.
    assert (state.ballots_total, state.mail_returned, state.inperson) == (
        408_407, 182_434, 225_973)
    assert state.mail_returned + state.inperson == state.ballots_total


def test_it_is_a_FINAL_and_says_so(eavs_rows):
    """One row per county at days_to_election 0, and no pretence of a curve.

    `ballots_new` is blank on every row: there is no previous day for a
    post-election survey to differ from, and a 0 would claim there was.
    """
    result = idaho.eavs_parse(eavs_rows, 2024, date(2024, 11, 5))
    assert len({r.day for r in result.county_rows}) == 1
    assert len(result.state_rows) == 1
    for row in result.county_rows + result.state_rows:
        assert row.ballots_new is None


def test_no_party_and_no_mail_requested_are_written(eavs_rows):
    """Two blanks, two reasons, and neither may become a 0.

    EAVS carries no party at all. `C1a` ("Mail Transmitted Total", 196,032) is
    deliberately not published as `mail_requested`: Idaho's own dashboard says
    187,682 absentees issued for the same election, 4.4% apart, and an
    unreconcilable number is a blank.
    """
    result = idaho.eavs_parse(eavs_rows, 2024, date(2024, 11, 5))
    assert result.state_rows[0].mail_requested is None
    for row in result.county_rows + result.state_rows:
        assert row.party_dem is None and row.party_rep is None
        assert row.party_oth is None and row.party_npa is None


def test_a_real_zero_early_county_is_published_as_zero(eavs_rows):
    """Twelve Idaho counties ran no early-voting site and reported 0.

    This is the other half of the blank rule and it matters just as much: 0 is
    the state's own answer here, not an absence, and blanking it would hide a
    real fact. Idaho uses none of EAVS's -88/-99 codes in these columns.
    """
    result = idaho.eavs_parse(eavs_rows, 2024, date(2024, 11, 5))
    zeros = [r for r in result.county_rows if r.inperson == 0]
    assert len(zeros) == 12
    for row in zeros:
        assert row.inperson == 0 and row.inperson is not None
        assert row.mail_returned > 0
        assert row.ballots_total == row.mail_returned


def test_EAVS_own_missing_codes_become_None_and_never_zero(eavs_rows):
    """-88 "not applicable" and -99 "data not available" are not counts.

    Nationally 337 jurisdictions answer -99 for in-person early voting; writing
    0 for those would publish "nobody voted early" for a county that did not
    answer the question. And a county missing HALF its early vote has an unknown
    total, not a half-sized one.
    """
    assert idaho.eavs_count("-88", "x") is None
    assert idaho.eavs_count("-99", "x") is None
    assert idaho.eavs_count("", "x") is None
    assert idaho.eavs_count("0", "x") == 0

    blinded = [dict(r) for r in eavs_rows]
    blinded[0][idaho.EAVS_INPERSON] = "-99"
    result = idaho.eavs_parse(blinded, 2024, date(2024, 11, 5))
    hurt = next(r for r in result.county_rows
                if r.county_fips == blinded[0]["FIPSCode"][:5])
    assert hurt.inperson is None
    assert hurt.ballots_total is None          # NOT the mail half on its own
    assert hurt.mail_returned == 61_398
    # ...and a state total missing a county is not published at all.
    assert result.state_rows == []


def test_an_unrecognised_negative_code_is_DRIFT(eavs_rows):
    with pytest.raises(SchemaDrift, match="unrecognised code"):
        idaho.eavs_count("-77", "x")
    with pytest.raises(SchemaDrift, match="not a count"):
        idaho.eavs_count("some", "x")


def test_the_surveys_own_identity_is_checked(eavs_rows):
    """Early ballots are a subset of all ballots, and 2020 proves why this runs.

    The EAVS question CODES are not stable across cycles. In the 2020 release
    the column labelled `F1a` comes back equal to `C1b` for Bear Lake County --
    3,365 against 3,365, with 185 early votes on top -- so `F1a` did not mean
    "total voters" that year. This check is what refuses that instead of
    publishing it.
    """
    broken = [dict(r) for r in eavs_rows]
    broken[0][idaho.EAVS_TOTAL_VOTERS] = "10"
    with pytest.raises(SchemaDrift, match="total voters"):
        idaho.eavs_parse(broken, 2024, date(2024, 11, 5))


def test_a_jurisdiction_is_placed_by_its_FIPS_and_not_by_its_name(eavs_rows):
    """Rule 4, and the name is now advisory rather than a second lock.

    ⚠️ THIS TEST USED TO ASSERT THE OPPOSITE OF ITS FIRST HALF. It checked that
    a wrong NAME raises SchemaDrift, on the reasoning that name and code should
    corroborate each other. Generalising this reader to other states showed the
    name cannot carry that weight: New Mexico's own EAVS submission spells
    `DONA ANA COUNTY`, having lost the ñ somewhere upstream, and it matches no
    census name while its code 35013 is exactly right. Refusing it would have
    dropped a real county over a diacritic.

    So the code places the row and the census supplies the canonical name.
    """
    renamed = [dict(r) for r in eavs_rows]
    renamed[0]["Jurisdiction_Name"] = "NONESUCH COUNTY"
    result = idaho.eavs_parse(renamed, 2024, date(2024, 11, 5))
    assert len(result.county_rows) == 44
    assert "Nonesuch" not in {r.county_name for r in result.county_rows}


def test_a_jurisdiction_outside_any_county_withholds_the_county_rows(eavs_rows):
    """Not drift, and not a partial file either -- no county rows at all.

    ⚠️ ALSO A REVERSAL, and Missouri is the reason. This raised SchemaDrift on a
    code that is not one of the state's counties, which is right for Idaho,
    whose jurisdictions ARE its counties, and wrong in general: Missouri's
    `KANSAS CITY CITY` carries 2938000000, a PLACE code, because Kansas City
    spans four counties and runs its own election board. That is the file being
    correct, not the file being broken.

    Its ballots therefore belong to no single county, and the four counties it
    overlaps are each short an unknown number. Publishing the other 114 would
    understate Jackson County silently, so the whole county table is withheld
    and only the statewide total -- which still includes Kansas City -- ships.
    """
    shifted = [dict(r) for r in eavs_rows]
    shifted[0]["FIPSCode"] = "9900100000"
    result = idaho.eavs_parse(shifted, 2024, date(2024, 11, 5))
    assert result.county_rows == []
    # The statewide row survives: every jurisdiction still answered both
    # columns, so the SUM is still the whole state even though one of its
    # addresses is unusable.
    assert len(result.state_rows) == 1
    assert result.state_rows[0].ballots_total == 408_407


def test_a_download_that_is_not_a_zip_is_DRIFT():
    with pytest.raises(SchemaDrift, match="not a readable zip"):
        idaho.eavs_rows(b"<html>rate limited</html>" * 10, "ID")


def test_missing_columns_are_DRIFT():
    import io, zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("x.csv", "FIPSCode,State_Abbr\n1600100000,ID\n")
    with pytest.raises(SchemaDrift, match="missing columns"):
        idaho.eavs_rows(buf.getvalue(), "ID")


# --------------------------------------------------------------- the adapter

def test_history_refuses_a_cycle_that_has_not_finished(monkeypatch):
    """EAVS is a POST-election survey; the live tracker is what reads a live one.

    The guard fires before a single request, which the poisoned `get` proves.
    """
    def forbidden(*args, **kwargs):
        raise AssertionError("fetch_history fetched for a live cycle")

    monkeypatch.setattr(idaho, "get", forbidden)
    scraper = idaho.IDScraper()
    for cycle in (2026, 2028):
        with pytest.raises(NotYetPublished) as exc:
            scraper.fetch_history(cycle)
        assert str(cycle) in str(exc.value)


def test_history_rows_say_they_came_from_the_EAC_not_from_the_SoS(monkeypatch):
    """`id-sos` would be a lie: these numbers arrive from the EAC.

    The adapter stamps them itself rather than letting `backfill` stamp them,
    which is the same thing me.py and ct.py do inside their own history paths.

    Both halves of the stamp are pinned, because the NAME alone was never
    enough. This test originally asserted `TIER_SCRAPER` alongside
    `eac-eavs` -- a row that named the EAC in one field while claiming the
    state's own tier in the other, which is the provenance lie the Arizona and
    Montana work refused to publish. `TIER_SURVEY` exists now; asserting it
    here is what stops these rows drifting back onto the state's rung.
    """
    pages = {idaho.EAVS_INDEX: fixture("eac_datasets_index.html")}

    def fake_get(url, **kwargs):
        return pages.get(url, fixture("eavs_2024_ID.zip"))

    monkeypatch.setattr(idaho, "get", fake_get)
    result = idaho.IDScraper().fetch_history(2024)
    assert len(result.county_rows) == 44
    for row in result.county_rows + result.state_rows:
        assert row.provenance.name == "eac-eavs" == idaho.EAVS_NAME
        assert row.provenance.tier == TIER_SURVEY
        assert row.provenance.tier != idaho.IDScraper.tier
        assert row.provenance.name != idaho.IDScraper.name
