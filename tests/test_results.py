"""Past-cycle election results: the outcome the early-vote curve is judged against.

Both fixtures are real slices of the MIT Election Data + Science Lab statewide
files pulled from Harvard Dataverse on 2026-09-06 — the genuine headers verbatim,
and the genuine rows for a handful of states chosen because each one is a trap:

  AK 2022 senate   every candidate row has an EMPTY party. Summing it naively
                   publishes dem=0/rep=0 for a race Murkowski and Tshibaka both
                   contested. Must come out BLANK.
  IL 2022 senate   MEDSL codes Tammy Duckworth `DEMOCRATIC` -> `OTHER`. Reading
                   `party_simplified` first would move 2.3M Democratic votes into
                   `oth` and flip the margin by 30 points.
  CA 2022 senate   a regular AND a concurrent special, same candidates.
  NE 2024 senate   the same, plus a genuine dem=0 (no Democrat on the ballot).
  GA 2022 senate   a November general AND a December runoff.
  ME 2024 senate   Angus King caucuses with the Democrats and must stay `oth`.
  VT 2024 senate   ditto Bernie Sanders.
  DC 2024 pres     the candidate rows overshoot the file's own race total.
  NY 2024 pres     fusion lines ("WORKING FAMILIES / DEMOCRAT").
  NC               the one state with a real early-vote final in output/, so the
                   early_share_of_total join has something to join to.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path

import pytest

from ev import results
from ev.adapters.base import SchemaDrift

FIXTURES = Path(__file__).parent / "fixtures" / "results"
PRESIDENT = FIXTURES / "1976-2024-president.sample.csv"
SENATE = FIXTURES / "1976-2024-senate-state.sample.tab"


def _parse(source: results.MedslSource, cycle: int) -> dict[str, results.StateResult]:
    path = PRESIDENT if source is results.PRESIDENT_SOURCE else SENATE
    return {r.state: r for r in results.parse(path.read_bytes(), source, cycle)}


def _rewrite(source: results.MedslSource, mutate) -> bytes:
    """Re-serialise a fixture after `mutate(fieldnames, rows)` edits it."""
    path = PRESIDENT if source is results.PRESIDENT_SOURCE else SENATE
    reader = csv.DictReader(
        io.StringIO(path.read_bytes().decode("utf-8")), delimiter=source.delimiter
    )
    fieldnames, rows = list(reader.fieldnames or []), list(reader)
    fieldnames, rows = mutate(fieldnames, rows)
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames, delimiter=source.delimiter,
                            lineterminator="\n", extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue().encode("utf-8")


# --------------------------------------------------------------------------
# Shares, margin, and the sign convention
# --------------------------------------------------------------------------
def test_shares_sum_to_the_whole():
    """dem + rep + oth is the whole race, so the three shares make 100%."""
    for source, cycle in ((results.SENATE_SOURCE, 2022),
                          (results.PRESIDENT_SOURCE, 2024),
                          (results.SENATE_SOURCE, 2024)):
        for state, row in _parse(source, cycle).items():
            if row.dem_votes is None:
                continue
            total = row.dem_votes + row.rep_votes + row.oth_votes
            # DC 2024 is the one state where MEDSL's own candidate rows overshoot
            # its published race total, by 0.78%. The guard tolerates that and
            # nothing looser -- see results.MAX_TOTAL_DRIFT.
            assert abs(total - row.total_votes) <= results.MAX_TOTAL_DRIFT * row.total_votes
            oth_share = 100.0 * row.oth_votes / row.total_votes
            assert abs(row.dem_share + row.rep_share + oth_share - 100.0) <= 1.0, state


def test_margin_is_dem_minus_rep_in_points():
    nc = _parse(results.SENATE_SOURCE, 2022)["NC"]
    assert nc.dem_votes == 1784049 and nc.rep_votes == 1905786
    assert nc.total_votes == 3773924
    assert nc.margin == pytest.approx(nc.dem_share - nc.rep_share)


def test_margin_is_positive_for_a_democratic_win():
    ga = _parse(results.SENATE_SOURCE, 2022)["GA"]
    assert ga.winner_party == "dem"
    assert ga.margin > 0


def test_margin_is_negative_for_a_republican_win():
    """D positive, everywhere in this project. NC 2022: Budd beat Beasley by 3.2."""
    nc = _parse(results.SENATE_SOURCE, 2022)["NC"]
    assert nc.winner_party == "rep"
    assert nc.margin < 0
    assert nc.margin == pytest.approx(-3.2257, abs=1e-4)


def test_president_margin_matches_the_certified_result():
    nc = _parse(results.PRESIDENT_SOURCE, 2024)["NC"]
    assert (nc.dem_votes, nc.rep_votes, nc.total_votes) == (2715375, 2898423, 5699141)
    assert nc.margin == pytest.approx(-3.2119, abs=1e-4)


# --------------------------------------------------------------------------
# THE BLANK RULE
# --------------------------------------------------------------------------
def test_a_race_with_no_reported_party_is_blank_not_zero():
    """Alaska 2022 ranked-choice: MEDSL gives nobody a party.

    dem_votes=0 there would say no Democrat received a vote. The total is still
    real and is still published, because that part IS reported.
    """
    ak = _parse(results.SENATE_SOURCE, 2022)["AK"]
    assert ak.total_votes == 263526
    assert ak.dem_votes is None and ak.rep_votes is None and ak.oth_votes is None
    assert ak.dem_share is None and ak.margin is None and ak.winner_party is None

    cells = ak.to_dict()
    assert cells["dem_votes"] == "" and cells["rep_votes"] == ""
    assert cells["oth_votes"] == "" and cells["margin"] == ""
    assert cells["winner_party"] == ""
    assert cells["total_votes"] == "263526"


def test_a_real_zero_is_written_as_zero():
    """Nebraska 2024 had no Democrat on the ballot. That is a 0, not a blank."""
    ne = _parse(results.SENATE_SOURCE, 2024)["NE"]
    assert ne.dem_votes == 0
    assert ne.to_dict()["dem_votes"] == "0"
    assert ne.to_dict()["dem_share"] == "0"


def test_none_and_zero_render_differently():
    blank = results.StateResult(cycle=2022, state="ZZ", office="senate",
                                source_name="x", total_votes=100, oth_votes=None)
    zero = results.StateResult(cycle=2022, state="ZZ", office="senate",
                               source_name="x", total_votes=100, oth_votes=0)
    assert blank.to_dict()["oth_votes"] == ""
    assert zero.to_dict()["oth_votes"] == "0"


# --------------------------------------------------------------------------
# Party bucketing
# --------------------------------------------------------------------------
def test_party_detailed_beats_medsls_own_simplified_column():
    """MEDSL codes Duckworth `DEMOCRATIC` -> `OTHER`; the ballot said Democrat."""
    il = _parse(results.SENATE_SOURCE, 2022)["IL"]
    assert il.dem_votes == 2329136
    assert il.winner_party == "dem"
    assert il.margin > 0


def test_an_independent_who_caucuses_with_a_party_stays_in_oth():
    senate24 = _parse(results.SENATE_SOURCE, 2024)
    king = senate24["ME"]
    assert king.dem_votes == 88875          # the actual Democratic nominee
    assert king.oth_votes >= 427570         # King's own votes are NOT dem
    assert king.winner_party == "oth"

    sanders = senate24["VT"]
    assert sanders.dem_votes == 0
    assert sanders.winner_party == "oth"


def test_fusion_lines_land_with_the_nominee():
    """NY lists Harris as WORKING FAMILIES / DEMOCRAT and Trump as CONSTITUTION /
    REPUBLICAN. `normalize.party()` knows neither, so MEDSL's own bucketing is
    the fallback -- and it is right here."""
    ny = _parse(results.PRESIDENT_SOURCE, 2024)["NY"]
    assert ny.dem_votes == 4619195
    assert ny.rep_votes == 3578899


def test_an_unrecognised_party_label_raises():
    def mutate(fieldnames, rows):
        for row in rows:
            if row["state_po"] == "GA" and row["year"] == "2022":
                row["party_detailed"] = "TOTALLY NEW PARTY"
                row["party_simplified"] = "TOTALLY NEW PARTY"
        return fieldnames, rows

    body = _rewrite(results.SENATE_SOURCE, mutate)
    with pytest.raises(SchemaDrift, match="party_simplified"):
        results.parse(body, results.SENATE_SOURCE, 2022)


# --------------------------------------------------------------------------
# Choosing the right race
# --------------------------------------------------------------------------
def test_a_regular_election_beats_a_concurrent_special():
    ca = _parse(results.SENATE_SOURCE, 2022)["CA"]
    assert ca.total_votes == 10843650          # the full-term seat
    assert ca.total_votes != 10771758          # not the special
    ne = _parse(results.SENATE_SOURCE, 2024)["NE"]
    assert ne.total_votes == 938336


def test_only_one_row_per_state_and_office():
    for source, cycle in ((results.SENATE_SOURCE, 2022),
                          (results.PRESIDENT_SOURCE, 2024)):
        rows = results.parse(
            (PRESIDENT if source is results.PRESIDENT_SOURCE else SENATE).read_bytes(),
            source, cycle,
        )
        keys = [r.key() for r in rows]
        assert len(keys) == len(set(keys))


def test_a_runoff_is_a_separate_election_and_is_not_published():
    """Georgia's December 2022 runoff has its own early vote and its own curve."""
    ga = _parse(results.SENATE_SOURCE, 2022)["GA"]
    assert ga.total_votes == 3935924           # November 8
    assert ga.total_votes != 3541877           # December 6


def test_the_wrong_cycle_yields_nothing():
    assert results.parse(PRESIDENT.read_bytes(), results.PRESIDENT_SOURCE, 2022) == []


def test_an_unknown_stage_raises_rather_than_being_dropped():
    def mutate(fieldnames, rows):
        for row in rows:
            if row["state_po"] == "NV":
                row["stage"] = "GEN SECOND ROUND"
        return fieldnames, rows

    body = _rewrite(results.SENATE_SOURCE, mutate)
    with pytest.raises(SchemaDrift, match="stage"):
        results.parse(body, results.SENATE_SOURCE, 2022)


def test_a_mode_split_file_is_refused_rather_than_double_counted():
    def mutate(fieldnames, rows):
        for row in rows:
            if row["state_po"] == "NV":
                row["mode"] = "ELECTION DAY"
        return fieldnames, rows

    body = _rewrite(results.SENATE_SOURCE, mutate)
    with pytest.raises(SchemaDrift, match="mode"):
        results.parse(body, results.SENATE_SOURCE, 2022)


# --------------------------------------------------------------------------
# State codes
# --------------------------------------------------------------------------
def test_state_names_map_to_usps_codes():
    assert results.state_code("NORTH CAROLINA", "NC") == "NC"
    assert results.state_code("DISTRICT OF COLUMBIA", "DC") == "DC"
    assert results.state_code("  new   mexico ", None) == "NM"
    assert len(results.STATE_NAME_TO_CODE) == 51


def test_an_unrecognised_state_name_raises():
    with pytest.raises(results.UnknownState):
        results.state_code("PUERTO RICO", None)
    with pytest.raises(results.UnknownState):
        results.state_code("NORTH CAROLINA", "XX")


def test_a_name_and_code_that_disagree_raise():
    """Disagreement is a shifted column, not a spelling variant."""
    with pytest.raises(results.UnknownState, match="but the row says"):
        results.state_code("NORTH CAROLINA", "SC")


def test_an_unrecognised_state_in_the_file_raises():
    def mutate(fieldnames, rows):
        for row in rows:
            if row["state_po"] == "GA":
                row["state"] = "GEORGIA TERRITORY"
        return fieldnames, rows

    body = _rewrite(results.SENATE_SOURCE, mutate)
    with pytest.raises(results.UnknownState):
        results.parse(body, results.SENATE_SOURCE, 2022)


def test_every_published_state_is_a_two_letter_code():
    for source, cycle in ((results.SENATE_SOURCE, 2022),
                          (results.PRESIDENT_SOURCE, 2024)):
        for state in _parse(source, cycle):
            assert len(state) == 2 and state.isupper()
            assert state in results.STATE_CODES


# --------------------------------------------------------------------------
# Schema drift
# --------------------------------------------------------------------------
def test_a_missing_column_raises():
    def mutate(fieldnames, rows):
        fieldnames = [f for f in fieldnames if f != "candidatevotes"]
        return fieldnames, rows

    body = _rewrite(results.PRESIDENT_SOURCE, mutate)
    with pytest.raises(SchemaDrift, match="missing columns"):
        results.parse(body, results.PRESIDENT_SOURCE, 2024)


def test_a_renamed_office_raises():
    def mutate(fieldnames, rows):
        for row in rows:
            row["office"] = "PRESIDENT OF THE UNITED STATES"
        return fieldnames, rows

    body = _rewrite(results.PRESIDENT_SOURCE, mutate)
    with pytest.raises(SchemaDrift, match="office"):
        results.parse(body, results.PRESIDENT_SOURCE, 2024)


def test_candidate_rows_far_off_the_races_own_total_raise():
    def mutate(fieldnames, rows):
        for row in rows:
            if row["state_po"] == "GA" and row["year"] == "2022":
                row["totalvotes"] = "9999999"
        return fieldnames, rows

    body = _rewrite(results.SENATE_SOURCE, mutate)
    with pytest.raises(SchemaDrift, match="race total"):
        results.parse(body, results.SENATE_SOURCE, 2022)


def test_two_different_race_totals_in_one_state_raise():
    def mutate(fieldnames, rows):
        seen = False
        for row in rows:
            if row["state_po"] == "GA" and row["year"] == "2022" and row["stage"] == "GEN":
                if seen:
                    row["totalvotes"] = "3935925"
                seen = True
        return fieldnames, rows

    body = _rewrite(results.SENATE_SOURCE, mutate)
    with pytest.raises(SchemaDrift, match="different"):
        results.parse(body, results.SENATE_SOURCE, 2022)


# --------------------------------------------------------------------------
# early_share_of_total
# --------------------------------------------------------------------------
def _meta(tmp_path: Path, rows: list[dict[str, str]]) -> Path:
    from ev.publish import STATE_META_COLUMNS
    out = tmp_path / "output"
    out.mkdir(parents=True, exist_ok=True)
    with (out / "ev_state_meta.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=STATE_META_COLUMNS,
                                extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    return out


def test_a_missing_early_total_leaves_the_column_blank_not_zero(tmp_path):
    out = _meta(tmp_path, [{"state": "NC", "ev_2022_total": "", "ev_2024_total": ""}])
    rows = results.parse(SENATE.read_bytes(), results.SENATE_SOURCE, 2022)
    assert results.attach_early_share(rows, out) == 0
    for row in rows:
        assert row.early_votes is None
        assert row.early_share_of_total is None
        assert row.to_dict()["early_share_of_total"] == ""


def test_an_early_total_becomes_a_percentage_of_the_race_total(tmp_path):
    out = _meta(tmp_path, [{"state": "NC", "ev_2022_total": "2187856", "ev_2024_total": ""}])
    rows = results.parse(SENATE.read_bytes(), results.SENATE_SOURCE, 2022)
    assert results.attach_early_share(rows, out) == 1
    nc = next(r for r in rows if r.state == "NC")
    assert nc.early_votes == 2187856
    assert nc.early_share_of_total == pytest.approx(100 * 2187856 / 3773924, abs=1e-4)
    # Every other state in the file has no early total and must stay blank.
    assert all(r.early_share_of_total is None for r in rows if r.state != "NC")


def test_a_state_absent_from_the_meta_file_stays_blank(tmp_path):
    out = _meta(tmp_path, [{"state": "NC", "ev_2022_total": "2187856"}])
    rows = results.parse(SENATE.read_bytes(), results.SENATE_SOURCE, 2022)
    results.attach_early_share(rows, out)
    ga = next(r for r in rows if r.state == "GA")
    assert ga.to_dict()["early_share_of_total"] == ""


def test_a_non_numeric_early_total_is_ignored_rather_than_coerced(tmp_path):
    out = _meta(tmp_path, [{"state": "NC", "ev_2022_total": "n/a"}])
    rows = results.parse(SENATE.read_bytes(), results.SENATE_SOURCE, 2022)
    assert results.attach_early_share(rows, out) == 0


# --------------------------------------------------------------------------
# Writing
# --------------------------------------------------------------------------
def test_publish_writes_the_declared_columns_with_lf_endings(tmp_path):
    rows = results.parse(SENATE.read_bytes(), results.SENATE_SOURCE, 2022)
    summary = results.publish_results(tmp_path, rows)
    raw = (tmp_path / "results_state.csv").read_bytes()

    assert summary["rows"] == len(rows)
    assert b"\r\n" not in raw
    assert raw.split(b"\n")[0].decode() == ",".join(results.RESULTS_COLUMNS)


def test_publish_merges_rather_than_replacing_other_cycles(tmp_path):
    senate22 = results.parse(SENATE.read_bytes(), results.SENATE_SOURCE, 2022)
    results.publish_results(tmp_path, senate22)
    president24 = results.parse(PRESIDENT.read_bytes(), results.PRESIDENT_SOURCE, 2024)
    results.publish_results(tmp_path, president24)

    with (tmp_path / "results_state.csv").open(newline="", encoding="utf-8") as fh:
        written = list(csv.DictReader(fh))

    assert len(written) == len(senate22) + len(president24)
    assert {r["cycle"] for r in written} == {"2022", "2024"}
    assert {r["office"] for r in written} == {"senate", "president"}
    keys = [(r["cycle"], r["state"], r["office"]) for r in written]
    assert keys == sorted(keys)


def test_republishing_the_same_key_replaces_rather_than_duplicates(tmp_path):
    rows = results.parse(SENATE.read_bytes(), results.SENATE_SOURCE, 2022)
    results.publish_results(tmp_path, rows)
    summary = results.publish_results(tmp_path, rows)
    assert summary["added"] == 0
    assert summary["rows"] == len(rows)


def test_offices_without_a_source_are_declared_but_not_produced():
    assert set(results.SOURCED_OFFICES) == {"president", "senate"}
    assert set(results.OFFICES) - set(results.SOURCED_OFFICES) == {"governor", "house_total"}
    with pytest.raises(ValueError, match="no source"):
        results.build([2022], offices=["governor"])
