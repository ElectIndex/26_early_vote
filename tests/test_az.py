"""Arizona: the SoS Sent/Accepted table, and the county-recorder party route.

Two routes, two sets of tests.

**The SoS route** is the state's ballot counts. The fixture is the real 2024
election-information page. Arizona publishes no downloadable file — the numbers
live in an HTML table on the cycle's page — so the parser's whole job is reading
that table without being fooled by the two things the page does: it keeps LAST
cycle's table up until the new window opens, and it serves a bot challenge
instead of the page to some automated requests.

**The recorder route** is the party split that table lacks. Its finding is that
there isn't one: all fifteen county recorders were surveyed on 2026-09-06 and
none publishes early-ballot returns by party to the public (see
docs/arizona-party.md and `az.RECORDER_SURVEY`). So its tests do two jobs — pin
today's honest answer, that Arizona's party columns are blank rather than zero
and that nothing partial escapes into the statewide row; and pin the arithmetic
that will carry a real split the day one is published, above all that a partial
figure is labelled with its coverage and that a blended figure's error band
shrinks in proportion to how much of it was actually counted.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import pytest

from ev.adapters import az
from ev.adapters.base import NotYetPublished, SchemaDrift

FIXTURES = Path(__file__).parent / "fixtures" / "az"
PAGE_2024 = (FIXTURES / "2024-election-info.html").read_text(encoding="utf-8", errors="replace")
PAGE_EMPTY = (FIXTURES / "2026-election-info-no-table.html").read_text(encoding="utf-8", errors="replace")


@pytest.fixture
def parsed():
    return az.parse(PAGE_2024, 2024)


def test_all_fifteen_counties_parse(parsed):
    """Arizona has exactly 15 counties and the table carries every one."""
    assert len(parsed.county_rows) == 15


def test_county_rows_are_fips_keyed(parsed):
    for row in parsed.county_rows:
        assert len(row.county_fips) == 5 and row.county_fips.startswith("04")


def test_accepted_is_the_ballot_count_not_sent(parsed):
    """`Sent` is ballots put in voters' hands; only `Accepted` are votes.

    Counting Sent would overstate Arizona by its entire outstanding mail pile.
    """
    state = parsed.state_rows[0]
    assert state.ballots_total is not None
    assert state.mail_requested is not None
    assert state.mail_requested >= state.ballots_total


def test_counties_sum_to_the_state_total(parsed):
    """The page publishes its own Total row; ours has to reproduce it."""
    total = sum(r.ballots_total for r in parsed.county_rows if r.ballots_total is not None)
    assert total == parsed.state_rows[0].ballots_total


def test_total_row_is_not_emitted_as_a_county(parsed):
    names = {(r.county_name or "").strip().lower() for r in parsed.county_rows}
    assert "total" not in names


def test_party_is_never_reported(parsed):
    """This table carries no party breakdown, so every party field stays blank.

    Arizona DOES register by party — the state simply does not publish it here —
    so a 0 would be a claim the source never made.
    """
    for row in parsed.state_rows + parsed.county_rows:
        assert row.party_dem is None and row.party_rep is None
        assert row.party_npa is None and row.party_oth is None


def test_page_without_the_table_raises_drift():
    """The table missing entirely is drift, not an empty result."""
    with pytest.raises((SchemaDrift, NotYetPublished)):
        az.parse(PAGE_EMPTY, 2026)


def test_last_cycles_table_is_not_published_as_this_cycles(monkeypatch):
    """Arizona leaves the previous table up until the new window opens.

    Parsing it as 2026 data would publish a two-year-old number as today's.
    """
    from ev.adapters.base import AdapterError

    monkeypatch.setattr(az, "get", lambda *a, **k: PAGE_2024.encode("utf-8"))
    # The invariant is that a 2024-dated table never becomes 2026 data. WHICH
    # refusal is a ladder-policy call: the adapter raises SchemaDrift, so a stale
    # state page falls through to the aggregator, which may well be fresher --
    # NotYetPublished would instead stop the walk and publish nothing for AZ.
    with pytest.raises(AdapterError):
        az.AZScraper().fetch(2026, date(2026, 9, 5))


def test_the_backfill_refuses_the_primarys_table_exactly_as_the_live_path_does(
    monkeypatch,
):
    """⚠️ THE REGRESSION THIS EXISTS FOR, and it is a shape worth recognising.

    Arizona leaves the PRIMARY's Sent/Accepted table on the general's page for
    months -- the saved 2024 fixture is that table, dated Jul 29 2024, ninety-nine
    days before the general. `fetch()` has refused it since it was written.
    `fetch_history()` did not, so the BACKFILL published what the live path
    rejects: 921,671 Arizona ballots recorded as 2024 general early votes before
    a single general-election ballot had been printed, which then became the only
    2024 county series Arizona had and the basis of a published party estimate.

    A guard on the live path and not on the backfill path is not a guard. Both
    now go through `_refuse_a_stale_table`.
    """
    monkeypatch.setattr(az.AZScraper, "_load", lambda self, cycle, **kw: PAGE_2024)
    with pytest.raises(NotYetPublished, match="early-vote window"):
        az.AZScraper().fetch_history(2024)


def test_a_table_inside_the_window_still_backfills(monkeypatch):
    """The guard must refuse the primary WITHOUT refusing the general.

    Same fixture, redated into the general's early-vote window: it parses and
    publishes, so what the test above pins is the date and not the page.
    """
    inside = PAGE_2024.replace("Jul 29, 2024", "Oct 29, 2024")
    monkeypatch.setattr(az.AZScraper, "_load", lambda self, cycle, **kw: inside)
    result = az.AZScraper().fetch_history(2024)
    assert result.county_rows
    assert all(r.day == date(2024, 10, 29) for r in result.county_rows)


def test_missing_page_is_not_yet_published(monkeypatch):
    """A 404 STOPS the ladder -- patched at the network layer so the adapter's
    own Missing -> NotYetPublished conversion is what gets exercised."""
    def _missing(*a, **k):
        raise az.Missing("404")

    monkeypatch.setattr(az, "get", _missing)
    with pytest.raises(NotYetPublished):
        az.AZScraper().fetch(2026, date(2026, 9, 5))


def test_bot_challenge_falls_through_instead_of_stopping(monkeypatch):
    """A challenge means "unreachable", not "absent" -- the report may well be
    behind it, so the ladder must try the next tier rather than stop."""
    from ev.adapters.base import SourceError

    monkeypatch.setattr(az, "get", lambda *a, **k: b"<html><title>Just a moment...</title>" + b"x" * 4096)
    with pytest.raises(SourceError) as exc:
        az.AZScraper().fetch(2026, date(2026, 9, 5))
    assert not isinstance(exc.value, NotYetPublished)


def test_bot_challenge_is_detected():
    """A challenge page is not data. Parsing it would yield silent nonsense."""
    assert az.looks_like_challenge(b"<html><title>Just a moment...</title>")
    assert not az.looks_like_challenge(PAGE_2024.encode("utf-8", "replace")[:2000])


# ==========================================================================
# The county-recorder party route.
#
# The survey behind it found nothing publishable (see docs/arizona-party.md and
# az.RECORDER_SURVEY), so these tests do two jobs: they pin the arithmetic that
# will carry a real split the day one exists, and they pin TODAY'S honest answer
# -- that Arizona's party columns are blank, not zero, and that nothing partial
# escapes into the statewide row.
# ==========================================================================

MARICOPA = "04013"
PIMA = "04019"
COCONINO = "04005"

RECORDER_CSV = (FIXTURES / "recorder-party-shape.SYNTHETIC.csv").read_text(encoding="utf-8")


def _measured(dem=None, rep=None, npa=None, oth=None):
    """A county's published split, with only the buckets it actually reported."""
    out = {}
    for bucket, value in (("dem", dem), ("rep", rep), ("npa", npa), ("oth", oth)):
        if value is not None:
            out[bucket] = value
    return out


# --------------------------------------------------------------------------
# Today's answer: there is no public source, and we say so rather than guess
# --------------------------------------------------------------------------
def test_no_recorder_is_wired_up_because_none_publishes():
    """RECORDERS is empty, and that is the surveyed finding, not a stub.

    If somebody adds an entry, they must also have fetched it -- see the
    `verified` field -- and this test's message is where they will read why.
    """
    assert az.RECORDERS == ()
    assert len(az.RECORDER_SURVEY) >= 15


def test_every_surveyed_endpoint_carries_a_real_status():
    """No URL in the survey may be a guess. Each was fetched, on a date."""
    for row in az.RECORDER_SURVEY:
        assert row.url.startswith("https://")
        assert re.match(r"^(200|301|403|404)( \(browser TLS\))?$", row.status), row
        assert re.match(r"^\d{4}-\d{2}-\d{2}$", row.checked)
        assert row.verdict.strip()


def test_recorder_route_is_a_no_op_while_no_recorder_publishes(parsed):
    """With RECORDERS empty, fetch's enrichment must leave the rows untouched."""
    az.AZScraper()._add_recorder_party(parsed, 2024, parsed.county_rows[0].day)
    for row in parsed.state_rows + parsed.county_rows:
        assert row.party_dem is None and row.party_rep is None
        assert row.party_npa is None and row.party_oth is None


def test_2026_general_has_not_opened_so_arizona_is_not_yet_published(monkeypatch):
    """The honest answer today. Early voting opens 2026-10-07; before that the
    SoS page carries no table, which STOPS the ladder rather than falling through
    to a source that would invent a zero."""
    monkeypatch.setattr(az, "get", lambda *a, **k: PAGE_EMPTY.encode("utf-8"))
    with pytest.raises(NotYetPublished):
        az.AZScraper().fetch(2026, date(2026, 9, 6))


# --------------------------------------------------------------------------
# Party labels: PND is the one that matters
# --------------------------------------------------------------------------
def test_pnd_is_npa_not_oth():
    """"Party Not Designated" is a third of Arizona and it is UNAFFILIATED.

    Bucketing it as `oth` would move ~1.5 million voters into a third-party
    column and destroy the number every early-vote story leads on.
    """
    assert az.party_bucket("PND") == "npa"
    assert az.party_bucket("pnd") == "npa"
    assert az.party_bucket("Party Not Designated") == "npa"
    assert az.party_bucket("PND") != "oth"


def test_the_pnd_alias_is_load_bearing():
    """normalize does not know "PND", so az's alias table is what makes it npa.

    If normalize ever learns the label this test still passes; if the alias is
    deleted while normalize still does not know it, this fails instead of the
    label silently becoming a SchemaDrift or, worse, "other".
    """
    from ev import normalize

    assert normalize.party("pnd") is None
    assert az.party_bucket("pnd") == "npa"


def test_arizona_party_codes_map_to_the_shared_vocabulary():
    from ev import normalize

    assert az.party_bucket("Democratic") == normalize.PARTY_DEM
    assert az.party_bucket("REP") == normalize.PARTY_REP
    assert az.party_bucket("LBT") == normalize.PARTY_OTH   # Libertarian
    assert az.party_bucket("GRN") == normalize.PARTY_OTH   # Green
    assert az.party_bucket("No Labels") == normalize.PARTY_OTH
    assert az.party_bucket("Independent") == normalize.PARTY_NPA


def test_a_bare_other_is_refused_rather_than_guessed():
    """Arizona uses "Other" for unaffiliated in one official report and for minor
    parties in another. Guessing wrong moves a third of the state, so we don't."""
    assert az.party_bucket("Other") is None
    assert az.party_bucket("OTH") is None


def test_an_unknown_label_is_none_not_other():
    assert az.party_bucket("Whig") is None
    assert az.party_bucket("") is None
    assert az.party_bucket(None) is None


# --------------------------------------------------------------------------
# The recorder file parser
# --------------------------------------------------------------------------
def test_recorder_table_parses_into_normalized_buckets():
    rows = az.parse_party_table(RECORDER_CSV, 2024)
    assert [day.isoformat() for day, _ in rows] == [
        "2024-10-09", "2024-10-10", "2024-10-11",
    ]
    day, buckets = rows[0]
    assert buckets["dem"] == 101234
    assert buckets["rep"] == 118902
    assert buckets["npa"] == 84551          # the PND column
    assert buckets["oth"] == 912 + 140 + 0  # Libertarian + Green + No Labels


def test_a_reported_zero_is_kept_as_zero():
    """0 means "the recorder counted none". Only a BLANK cell means unreported."""
    _, buckets = az.parse_party_table("Date,Democratic,Republican,PND\n2024-10-09,5,0,7\n", 2024)[0]
    assert buckets["rep"] == 0


def test_a_blank_cell_is_none_not_zero():
    _, buckets = az.parse_party_table("Date,Democratic,Republican,PND\n2024-10-09,5,,7\n", 2024)[0]
    assert buckets["rep"] is None


def test_an_unmappable_column_raises_drift():
    """A column we cannot place is drift, never a quiet dump into party_oth."""
    with pytest.raises(SchemaDrift):
        az.parse_party_table("Date,Democratic,Whig\n2024-10-09,5,6\n", 2024)


def test_a_file_with_no_date_column_raises_drift():
    with pytest.raises(SchemaDrift):
        az.parse_party_table("Democratic,Republican\n5,6\n", 2024)


def test_a_row_from_another_cycle_raises_drift():
    """Last cycle's file left on a server is the trap this whole module guards."""
    with pytest.raises(SchemaDrift):
        az.parse_party_table("Date,Democratic,Republican\n2022-10-09,5,6\n", 2024)


# --------------------------------------------------------------------------
# Coverage: a split covering part of Arizona is not Arizona's split
# --------------------------------------------------------------------------
def test_coverage_is_reported_and_marked_partial(parsed):
    """Maricopa + Pima is about three quarters of Arizona. It is NOT Arizona."""
    found = az.attach_party(parsed, {
        MARICOPA: _measured(dem=400_000, rep=430_000, npa=300_000, oth=9_000),
        PIMA: _measured(dem=120_000, rep=95_000, npa=70_000, oth=2_500),
    })
    assert found.partial is True
    assert found.complete is False
    assert set(found.counties) == {MARICOPA, PIMA}
    assert 0.5 < found.measured_fraction < 0.95
    label = found.label()
    assert "PARTIAL" in label
    assert "2 of 15" in label
    assert "Maricopa" in label and "Pima" in label
    assert "%" in label


def test_a_county_outside_the_covered_set_does_not_become_zero(parsed):
    """The rule the whole file rests on. Coconino published nothing, so Coconino
    reports nothing -- blank, not "no Democrat in Coconino has voted"."""
    az.attach_party(parsed, {MARICOPA: _measured(dem=400_000, rep=430_000)})
    by_fips = {r.county_fips: r for r in parsed.county_rows}
    outside = by_fips[COCONINO]
    assert outside.party_dem is None
    assert outside.party_rep is None
    assert outside.party_npa is None
    assert outside.party_oth is None
    # ...and its ballot count is untouched, because the SoS route owns that.
    assert outside.ballots_total is not None


def test_a_bucket_the_recorder_did_not_report_stays_blank(parsed):
    az.attach_party(parsed, {MARICOPA: _measured(dem=400_000, rep=430_000)})
    by_fips = {r.county_fips: r for r in parsed.county_rows}
    assert by_fips[MARICOPA].party_dem == 400_000
    assert by_fips[MARICOPA].party_npa is None
    assert by_fips[MARICOPA].party_oth is None


def test_a_partial_split_never_reaches_the_statewide_row(parsed):
    """`ev_state_daily.csv` has no column that can say "this is 74% of Arizona",
    so a partial split written there would be summed into a national party total
    as though it were complete. Same gate tx.py puts on its TOTAL row."""
    az.attach_party(parsed, {
        MARICOPA: _measured(dem=400_000, rep=430_000, npa=300_000, oth=9_000),
        PIMA: _measured(dem=120_000, rep=95_000, npa=70_000, oth=2_500),
    })
    state = parsed.state_rows[0]
    assert state.party_dem is None and state.party_rep is None
    assert state.party_npa is None and state.party_oth is None
    # The SoS's own figures are untouched -- it stays authoritative for these.
    assert state.ballots_total is not None and state.mail_requested is not None


def test_even_complete_coverage_leaves_the_statewide_row_blank(parsed):
    """At 15/15 the counties are still a DIFFERENT source than that row's, so the
    split belongs in the county file and the estimate table, not in a column that
    means "the Secretary of State reported this"."""
    every = {r.county_fips: _measured(dem=10, rep=11) for r in parsed.county_rows}
    found = az.attach_party(parsed, every)
    assert found.complete is True and found.partial is False
    assert parsed.state_rows[0].party_dem is None


def test_coverage_of_nothing_is_empty_not_partial(parsed):
    found = az.coverage(parsed)
    assert found.empty is True
    assert found.partial is False
    assert found.measured_ballots == 0
    assert "no county recorder reported" in found.label()


def test_coverage_share_is_computed_from_the_days_own_ballots(parsed):
    """Not from a hardcoded electorate share. Early-vote geography is not
    registration geography, and the denominator has to be the real one."""
    az.attach_party(parsed, {MARICOPA: _measured(dem=1, rep=1)})
    found = az.coverage(parsed)
    by_fips = {r.county_fips: r for r in parsed.county_rows}
    expected = by_fips[MARICOPA].ballots_total / parsed.state_rows[0].ballots_total
    assert found.measured_fraction == pytest.approx(expected)


def test_party_counts_for_a_county_outside_arizona_raise_drift(parsed):
    with pytest.raises(SchemaDrift):
        az.attach_party(parsed, {"48201": _measured(dem=1, rep=1)})


def test_an_unknown_bucket_name_raises_drift(parsed):
    with pytest.raises(SchemaDrift):
        az.attach_party(parsed, {MARICOPA: {"whig": 5}})


def test_registration_share_matches_the_published_state_total():
    """REGISTRATION_SHARE is transcribed from the SoS's July 2026 report. It has
    to cover all 15 counties and sum to one, or a coverage claim built on it is
    quietly short."""
    assert set(az.REGISTRATION_SHARE) == az.ALL_COUNTY_FIPS
    assert sum(az.REGISTRATION_SHARE.values()) == pytest.approx(1.0, abs=0.001)
    assert az.REGISTRATION_SHARE[MARICOPA] > 0.55  # Maricopa is most of Arizona
    assert az.REGISTRATION_SHARE[MARICOPA] + az.REGISTRATION_SHARE[PIMA] > 0.70


# --------------------------------------------------------------------------
# The blend: the band shrinks in proportion to what is actually counted
# --------------------------------------------------------------------------
def test_model_error_tracks_estimate():
    """az.MODEL_ERROR is a copy, because the ingest path must not import the
    model. This is the guard that stops the copy drifting."""
    from ev import estimate

    assert az.MODEL_ERROR == estimate.MODEL_ERROR


def test_a_pure_model_carries_the_full_band():
    """Nothing counted -- Arizona on exactly the terms every other modelled state
    gets, +/-10 points, per docs/party-estimate.md."""
    blended = az.blend_party_share(
        measured_dem=None, measured_rep=None,
        measured_fraction=0.0, modelled_dem_share=0.47,
    )
    assert blended.method == "model"
    assert blended.dem_share == pytest.approx(0.47)
    assert blended.band_half_width == pytest.approx(az.MODEL_ERROR)


def test_the_band_shrinks_in_proportion_to_what_is_measured():
    """The whole reason to do this. Count 77% of the ballots and the model is
    only standing in for 23%, so it can only be wrong about 23%."""
    blended = az.blend_party_share(
        measured_dem=440_000, measured_rep=560_000,
        measured_fraction=0.77, modelled_dem_share=0.47,
    )
    assert blended.method == "blend"
    assert blended.band_half_width == pytest.approx(az.MODEL_ERROR * 0.23)
    assert blended.band_half_width < az.MODEL_ERROR / 4


def test_a_blend_is_the_weighted_average_of_its_two_halves():
    blended = az.blend_party_share(
        measured_dem=440_000, measured_rep=560_000,   # 44.0% D two-party
        measured_fraction=0.75, modelled_dem_share=0.60,
    )
    assert blended.measured_dem_share == pytest.approx(0.44)
    assert blended.dem_share == pytest.approx(0.75 * 0.44 + 0.25 * 0.60)


def test_fully_measured_is_not_a_model_at_all():
    """At f=1 the band is zero and the method says "reported" -- the caller's cue
    that this belongs in the reported party columns, not the estimate table."""
    blended = az.blend_party_share(
        measured_dem=44, measured_rep=56,
        measured_fraction=1.0, modelled_dem_share=None,
    )
    assert blended.method == "reported"
    assert blended.band_half_width == pytest.approx(0.0)
    assert blended.dem_share == pytest.approx(0.44)


def test_no_model_for_the_remainder_means_no_number():
    """Never assume 50/50 for the counties nobody measured."""
    assert az.blend_party_share(
        measured_dem=44, measured_rep=56,
        measured_fraction=0.5, modelled_dem_share=None,
    ) is None


def test_claiming_measured_ballots_with_no_split_means_no_number():
    assert az.blend_party_share(
        measured_dem=None, measured_rep=None,
        measured_fraction=0.5, modelled_dem_share=0.47,
    ) is None


def test_the_blend_says_out_loud_how_much_is_counted():
    """A reader must be able to see "77% counted, 23% modelled" without reading
    a caption."""
    blended = az.blend_party_share(
        measured_dem=440_000, measured_rep=560_000,
        measured_fraction=0.77, modelled_dem_share=0.47,
    )
    assert blended.measured_fraction == pytest.approx(0.77)
    assert blended.modelled_fraction == pytest.approx(0.23)
    assert "77% of these ballots are counted" in blended.label()
    assert "23% is modelled" in blended.label()


def test_a_share_outside_zero_to_one_is_a_bug_not_a_number():
    with pytest.raises(ValueError):
        az.blend_party_share(
            measured_dem=1, measured_rep=1,
            measured_fraction=1.4, modelled_dem_share=0.5,
        )


def test_blend_end_to_end_from_a_real_coverage_figure(parsed):
    """The two halves joined: real Arizona ballot counts decide the fraction, and
    the fraction decides the band."""
    az.attach_party(parsed, {
        MARICOPA: _measured(dem=400_000, rep=430_000, npa=300_000, oth=9_000),
        PIMA: _measured(dem=120_000, rep=95_000, npa=70_000, oth=2_500),
    })
    found = az.coverage(parsed)
    rows = {r.county_fips: r for r in parsed.county_rows}
    dem = sum(rows[f].party_dem for f in found.counties)
    rep = sum(rows[f].party_rep for f in found.counties)
    blended = az.blend_party_share(
        measured_dem=dem, measured_rep=rep,
        measured_fraction=found.measured_fraction, modelled_dem_share=0.47,
    )
    assert blended.method == "blend"
    assert blended.band_half_width == pytest.approx(
        az.MODEL_ERROR * found.modelled_fraction
    )
    assert blended.band_half_width < az.MODEL_ERROR
