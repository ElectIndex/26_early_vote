"""Georgia: the SoS voter absentee file.

Two real fixtures, both pulled from the Secretary of State's own S3 objects on
2026-09-05 with the genuine 38-column header verbatim:

* `absentee_2026_primary_APPLING.csv` -- a slice of
  `GAVR/ABSENTEE_BALLOT/2026/A-12599/APPLING.csv`, the May 2026 general primary.
  It has real `Ballot Return Date`s, both ballot styles, accepted/cancelled/
  rejected ballots, and -- the trap this whole module is built around -- a
  POPULATED `Party` column reading REPUBLICAN / DEMOCRAT / NON-PARTISAN.
* `absentee_2026_general_STATEWIDE.csv` -- a slice of the `STATEWIDE.csv` member
  of `GAVR/ABSENTEE_ZIP/2026/A-12601/A-12601.zip`, the November 2026 general.
  Ten counties, accepted / rejected / cure-pending applications, and not one
  returned ballot: this is what Georgia's file looks like for months.

The single most important property asserted here is that every party_* field is
None on every row. Georgia has no party registration; the `Party` column records
which party's ballot a primary voter asked for, and it is empty in a general.
Publishing it as party_dem/party_rep would be wrong in a primary and writing 0
would read as "zero Democrats have voted" in a general.
"""

from __future__ import annotations

import csv
import io
import json
import zipfile
from datetime import date
from pathlib import Path

import pytest

from ev.adapters import _fips, ga
from ev.adapters.base import NotYetPublished, SchemaDrift, SourceError
from ev.normalize import method as normalize_method

FIXTURES = Path(__file__).parent / "fixtures" / "ga"
PRIMARY = FIXTURES / "absentee_2026_primary_APPLING.csv"
GENERAL = FIXTURES / "absentee_2026_general_STATEWIDE.csv"

PRIMARY_DAY = date(2026, 5, 19)
PARTY_FIELDS = ("party_dem", "party_rep", "party_oth", "party_npa")


def _rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    reader = csv.DictReader(io.StringIO(path.read_bytes().decode(ga.ENCODING)))
    return list(reader.fieldnames or []), list(reader)


def _csv(fieldnames: list[str], rows: list[dict[str, str]]) -> bytes:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue().encode(ga.ENCODING)


def _zip(body: bytes, member: str = "STATEWIDE.csv") -> bytes:
    """The real shape of the download: a zip whose STATEWIDE.csv we read."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr(member, body)
    return buf.getvalue()


@pytest.fixture(scope="module")
def primary():
    return ga.parse(PRIMARY.read_bytes(), 2026, PRIMARY_DAY)


@pytest.fixture(scope="module")
def general():
    return ga.parse(GENERAL.read_bytes(), 2026, date(2026, 9, 5))


# --------------------------------------------------------------------------
# THE BLANK RULE -- the property Georgia exists to test
# --------------------------------------------------------------------------
def test_no_party_field_is_ever_populated(primary, general):
    """Georgia has no party registration. Not 0, not a number -- None."""
    for result in (primary, general):
        for row in result.state_rows + result.county_rows:
            for field in PARTY_FIELDS:
                assert getattr(row, field) is None, (field, row)


def test_no_party_field_is_ever_zero(primary, general):
    """The failure mode THE BLANK RULE exists for.

    `is None` above already excludes 0, but 0 is the specific wrong answer here
    -- it renders on the site as "zero Democrats have voted" in a state that has
    no party registration to count -- so it gets its own assertion by name.
    """
    for result in (primary, general):
        for row in result.state_rows + result.county_rows:
            for field in PARTY_FIELDS:
                assert getattr(row, field) != 0, (field, row)


def test_the_fixture_really_does_carry_a_party_column():
    """Guards the test above from passing vacuously.

    Georgia's file has a `Party` column and in a PRIMARY it is populated -- so
    an adapter that naively mapped it would produce numbers, and the assertion
    that we produce None would be meaningful only if that column is really there.
    """
    columns, rows = _rows(PRIMARY)
    assert "Party" in columns
    assert {r["Party"] for r in rows} >= {"REPUBLICAN", "DEMOCRAT", "NON-PARTISAN"}


def test_party_column_is_empty_in_a_general_election():
    """The same column carries nothing in a general, which is why a 0 would lie."""
    _, rows = _rows(GENERAL)
    assert {r["Party"] for r in rows} == {""}


def test_no_demographic_rows_at_all(primary, general):
    """The 38-column file has no age, race or gender. Absent, not 'unknown'."""
    assert primary.demo_rows == []
    assert general.demo_rows == []


def test_unreported_county_fields_stay_blank(primary):
    """CountyDay has no mail_requested; nothing may invent one."""
    for row in primary.county_rows:
        assert row.ballots_total is not None
        assert row.mail_returned is not None


# --------------------------------------------------------------------------
# One download reconstructs the daily curve
# --------------------------------------------------------------------------
def test_one_file_reconstructs_a_daily_series(primary):
    days = [r.day for r in primary.state_rows]
    assert days == sorted(days)
    assert days[-1] == PRIMARY_DAY
    assert all((days[i + 1] - days[i]).days == 1 for i in range(len(days) - 1))


def test_cumulative_totals_are_monotonic(primary):
    for field in ("ballots_total", "mail_returned", "inperson", "mail_requested"):
        series = [getattr(r, field) for r in primary.state_rows]
        assert series == sorted(series), field


def test_method_split_sums_to_the_total(primary):
    row = primary.state_rows[-1]
    assert row.mail_returned + row.inperson == row.ballots_total
    assert row.inperson > 0 and row.mail_returned > 0


def test_only_accepted_ballots_are_counted(primary):
    """The file also carries cancelled and rejected ballots WITH return dates."""
    _, rows = _rows(PRIMARY)
    assert {r["Ballot Status"] for r in rows} >= {"A", "C", "R"}
    expected = sum(
        1 for r in rows
        if r["Ballot Status"] == "A" and r["Ballot Return Date"]
    )
    assert primary.state_rows[-1].ballots_total == expected


def test_future_dated_returns_are_excluded():
    """as_of is authoritative; a ballot returned after it must not appear."""
    early = ga.parse(PRIMARY.read_bytes(), 2026, date(2026, 4, 28))
    late = ga.parse(PRIMARY.read_bytes(), 2026, PRIMARY_DAY)
    assert early.state_rows[-1].ballots_total < late.state_rows[-1].ballots_total


# --------------------------------------------------------------------------
# County rows are keyed by FIPS, never by name
# --------------------------------------------------------------------------
def test_county_rows_are_five_digit_georgia_fips(primary):
    assert primary.county_rows
    for row in primary.county_rows:
        assert len(row.county_fips) == 5 and row.county_fips.startswith("13")
    last = primary.county_rows[-1]
    assert last.county_fips == "13001" and last.county_name == "Appling County"


def test_every_county_name_in_the_file_resolves_to_a_fips():
    """Georgia writes BENHILL, JEFFDAVIS, MCDUFFIE -- no spaces, no punctuation.

    A name we cannot place is a county that silently vanishes off the map, so
    the whole 159 have to join, not just the easy ones.
    """
    _, rows = _rows(GENERAL)
    names = {r["County"] for r in rows}
    assert {"BENHILL", "JEFFDAVIS", "MCDUFFIE", "MCINTOSH", "DEKALB"} <= names
    for name in sorted(names):
        hit = _fips.lookup("GA", name)
        assert hit is not None, name
        assert len(hit[0]) == 5 and hit[0].startswith("13")


# --------------------------------------------------------------------------
# Requests exist for months before the first ballot comes back
# --------------------------------------------------------------------------
def test_general_file_reports_requests_and_a_genuine_zero_cast(general):
    """The November 2026 file already existed on 2026-09-05 with tens of
    thousands of accepted applications and no returned ballot at all."""
    row = general.state_rows[-1]
    assert row.mail_requested > 0
    # A real 0: Georgia's file carries every ballot for the election and none
    # has come back. Blank would claim Georgia does not report ballots at all.
    assert row.ballots_total == 0
    assert row.mail_returned == 0 and row.inperson == 0
    # CountyDay cannot carry a request count, so it has nothing to say yet.
    assert general.county_rows == []


def test_rejected_and_pending_applications_are_not_counted_as_requests():
    _, rows = _rows(GENERAL)
    assert {r["Application Status"] for r in rows} >= {"A", "R", ""}
    expected = sum(
        1 for r in rows
        if r["Application Status"] == "A"
        and r["Ballot Style"] in ("ABSENTEE BY MAIL", "ELECTRONIC BALLOT DELIVERY")
    )
    result = ga.parse(GENERAL.read_bytes(), 2026, date(2026, 9, 5))
    assert result.state_rows[-1].mail_requested == expected


# --------------------------------------------------------------------------
# The real download shape: a zip whose STATEWIDE.csv is the union of counties
# --------------------------------------------------------------------------
def test_statewide_member_of_the_zip_is_what_gets_parsed(general):
    zipped = ga.parse(_zip(GENERAL.read_bytes()), 2026, date(2026, 9, 5))
    assert [r.mail_requested for r in zipped.state_rows] == [
        r.mail_requested for r in general.state_rows
    ]


def test_per_county_members_are_ignored_so_nothing_double_counts():
    """The zip carries STATEWIDE.csv *and* 159 county files covering the same
    ballots. Summing both would double every number in Georgia."""
    body = GENERAL.read_bytes()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("STATEWIDE.csv", body)
        archive.writestr("APPLING.csv", body)
        archive.writestr("FULTON.csv", body)
    both = ga.parse(buf.getvalue(), 2026, date(2026, 9, 5))
    only = ga.parse(body, 2026, date(2026, 9, 5))
    assert both.state_rows[-1].mail_requested == only.state_rows[-1].mail_requested


def test_zip_without_a_statewide_member_is_drift():
    with pytest.raises(SchemaDrift, match="STATEWIDE"):
        ga.parse(_zip(GENERAL.read_bytes(), member="APPLING.csv"), 2026, date(2026, 9, 5))


# --------------------------------------------------------------------------
# Ballot Style -> method
# --------------------------------------------------------------------------
def test_georgias_own_style_labels_map_to_the_shared_vocabulary():
    assert ga.ballot_method("EARLY IN-PERSON") == "inperson"
    assert ga.ballot_method("ABSENTEE BY MAIL") == "mail"
    assert ga.ballot_method("ELECTRONIC BALLOT DELIVERY") == "mail"


def test_the_alias_table_earns_its_place():
    """Documents why ga.py keeps its own table instead of calling normalize alone:
    the shared vocabulary does not know Georgia's spellings."""
    assert normalize_method("absentee by mail") == "mail"
    assert normalize_method("early in-person") is None
    assert normalize_method("electronic ballot delivery") is None


def test_unknown_ballot_style_raises_drift():
    """A new style bucketed silently as mail would move thousands of in-person
    ballots into the wrong series."""
    columns, rows = _rows(PRIMARY)
    rows[0] = dict(rows[0], **{"Ballot Style": "CURBSIDE HOVERCRAFT"})
    with pytest.raises(SchemaDrift, match="Ballot Style"):
        ga.parse(_csv(columns, rows), 2026, PRIMARY_DAY)


def test_dropped_column_raises_drift():
    columns, rows = _rows(PRIMARY)
    kept = [c for c in columns if c != "Ballot Return Date"]
    with pytest.raises(SchemaDrift, match="missing columns"):
        ga.parse(_csv(kept, rows), 2026, PRIMARY_DAY)


def test_unknown_county_name_raises_drift():
    columns, rows = _rows(PRIMARY)
    rows[0] = dict(rows[0], County="ATLANTIS")
    with pytest.raises(SchemaDrift, match="unrecognised county"):
        ga.parse(_csv(columns, rows), 2026, PRIMARY_DAY)


def test_unparseable_date_raises_drift():
    columns, rows = _rows(PRIMARY)
    rows[0] = dict(rows[0], **{"Application Date": "the ides of March"})
    with pytest.raises(SchemaDrift):
        ga.parse(_csv(columns, rows), 2026, PRIMARY_DAY)


# --------------------------------------------------------------------------
# NotYetPublished -- the path that runs every day for weeks
# --------------------------------------------------------------------------
def test_nothing_reported_yet_is_not_yet_published():
    """Before the first application is accepted there is nothing to publish, and
    a row of zeros would claim Georgia is reporting when it is not."""
    with pytest.raises(NotYetPublished):
        ga.parse(GENERAL.read_bytes(), 2026, date(2025, 12, 31))


def test_missing_object_is_not_yet_published(monkeypatch):
    """The presign API's own words for an election whose file is not posted."""
    scraper = ga.GAScraper()
    monkeypatch.setattr(
        ga.GAScraper, "_apex",
        lambda self, cls, method, params: {
            "state": "SUCCESS",
            "returnValue": {"returnValue": '"{\\"message\\": \\"No Data Found\\"}"'},
        },
    )
    with pytest.raises(NotYetPublished, match="not been published"):
        scraper.presigned_url("GAVR/ABSENTEE_ZIP/2026/A-12601/A-12601.zip", "tok")


def test_s3_404_is_not_yet_published(monkeypatch):
    monkeypatch.setenv(ga.TOKEN_ENV, "a-live-token")
    monkeypatch.setattr(ga.GAScraper, "election_number", lambda self, cycle: "A-12601")
    monkeypatch.setattr(
        ga.GAScraper, "presigned_url",
        lambda self, key, token: f"https://{ga.BUCKET_HOST}/{key}?sig=x",
    )

    def missing(*args, **kwargs):
        raise ga._net.Missing("404")

    monkeypatch.setattr(ga._net, "get", missing)
    with pytest.raises(NotYetPublished):
        ga.GAScraper().fetch(2026, date(2026, 9, 5))


def test_unlisted_election_is_not_yet_published(monkeypatch):
    """Georgia lists an election before it posts its files; if it has not even
    listed one for Election Day, there is certainly no file."""
    payload = _options([("p", "2026-05-19", "A-12599", 159)])
    monkeypatch.setattr(
        ga.GAScraper, "_apex", lambda self, cls, method, params: payload
    )
    with pytest.raises(NotYetPublished, match="2026-11-03"):
        ga.GAScraper().election_number(2026)


ALL_COUNTIES = [{"label": n, "value": n} for n in _fips.names("GA")]


def _options(entries: list[tuple[str, str, str, int]]) -> dict:
    """A getElectionOptions payload: the election list plus per-id county lists."""
    payload: dict = {"election": []}
    for eid, day, number, coverage in entries:
        payload["election"].append({
            "electionDate": day, "electionId": eid, "name": number,
            "electionName": f"{number} on {day}",
        })
        payload[eid] = ALL_COUNTIES[:coverage]
    return {"state": "SUCCESS", "returnValue": {"returnValue": payload}}


def test_election_is_matched_by_date_not_by_name(monkeypatch):
    """Georgia renamed the general election in all three tracked cycles, so a
    name match survives nothing; the date is the only stable key."""
    listing = {
        2022: ("2022-11-08", "A-12054"),
        2024: ("2024-11-05", "A-12311"),
        2026: ("2026-11-03", "A-12601"),
    }
    for cycle, (day, number) in listing.items():
        payload = _options([
            ("r", "2026-12-01", "A-99999", 159),
            ("g", day, number, 159),
        ])
        monkeypatch.setattr(
            ga.GAScraper, "_apex",
            lambda self, cls, method, params, payload=payload: payload,
        )
        assert ga.GAScraper().election_number(cycle) == number


def test_same_day_municipal_elections_do_not_win(monkeypatch):
    """On 2022-11-08 Georgia also listed the CITY OF SASSER general and the
    WARWICK special, plus a duplicate of the statewide general itself. County
    coverage picks the real one; a bare date match picked a city of 300 people."""
    payload = _options([
        ("sasser", "2022-11-08", "A-12174", 0),
        ("statewide", "2022-11-08", "A-12054", 159),
        ("warwick", "2022-11-08", "A-12169", 0),
        ("dupe", "2022-11-08", "A-12175", 159),
    ])
    monkeypatch.setattr(
        ga.GAScraper, "_apex", lambda self, cls, method, params: payload
    )
    assert ga.GAScraper().election_number(2022) == "A-12054"


def test_an_election_that_covers_no_counties_is_drift(monkeypatch):
    """A statewide general that is not statewide means we picked the wrong row."""
    payload = _options([("m", "2026-11-03", "A-12601", 3)])
    monkeypatch.setattr(
        ga.GAScraper, "_apex", lambda self, cls, method, params: payload
    )
    with pytest.raises(SchemaDrift, match="covers only 3"):
        ga.GAScraper().election_number(2026)


# --------------------------------------------------------------------------
# The reCAPTCHA gate is a SourceError, deliberately -- the data exists
# --------------------------------------------------------------------------
def _gate(monkeypatch, up: bool) -> None:
    """Pin the SoS's bot-check switch so no test ever calls the live endpoint."""
    monkeypatch.setattr(ga.GAScraper, "bot_check_active", lambda self: up)


def test_missing_recaptcha_token_is_a_source_error_not_not_yet_published(monkeypatch):
    """Georgia's file is there; we simply cannot reach it without a token. That
    must fall THROUGH to the aggregator, not stop the ladder as if Georgia had
    not started voting."""
    monkeypatch.delenv(ga.TOKEN_ENV, raising=False)
    _gate(monkeypatch, up=True)
    monkeypatch.setattr(ga.GAScraper, "election_number", lambda self, cycle: "A-12601")
    with pytest.raises(SourceError) as caught:
        ga.GAScraper().fetch(2026, date(2026, 9, 5))
    assert not isinstance(caught.value, NotYetPublished)
    assert ga.TOKEN_ENV in str(caught.value)


def test_the_error_says_georgia_cannot_be_collected_unattended(monkeypatch):
    """The finding, not a TODO: this text is what a maintainer reads in the CI
    log every night, so it has to name the cause and the consequence."""
    monkeypatch.delenv(ga.TOKEN_ENV, raising=False)
    _gate(monkeypatch, up=True)
    monkeypatch.setattr(ga.GAScraper, "election_number", lambda self, cycle: "A-12601")
    with pytest.raises(SourceError) as caught:
        ga.GAScraper().fetch(2026, date(2026, 9, 5))
    message = str(caught.value)
    assert "cannot be collected unattended" in message
    assert "aggregator" in message
    assert "docs/georgia-source.md" in message


def test_a_token_drives_the_real_presign_and_download(monkeypatch):
    monkeypatch.setenv(ga.TOKEN_ENV, "a-live-token")
    monkeypatch.setattr(ga.GAScraper, "election_number", lambda self, cycle: "A-12601")
    seen: dict = {}

    def presign(self, key, token):
        seen["key"] = key
        seen["token"] = token
        return f"https://{ga.BUCKET_HOST}/{key}?AWSAccessKeyId=x&Signature=y"

    monkeypatch.setattr(ga.GAScraper, "presigned_url", presign)
    monkeypatch.setattr(
        ga._net, "get",
        lambda url, **kw: _zip(GENERAL.read_bytes()),
    )
    result = ga.GAScraper().fetch(2026, date(2026, 9, 5))
    assert seen["key"] == "GAVR/ABSENTEE_ZIP/2026/A-12601/A-12601.zip"
    assert seen["token"] == "a-live-token"
    assert result.state_rows[-1].mail_requested > 0


def test_history_falls_back_to_the_cached_archive(monkeypatch, tmp_path):
    """A past cycle's zip can never change, so a cached copy is as good as a
    fresh download -- and it is the only way to backfill without a token."""
    monkeypatch.delenv(ga.TOKEN_ENV, raising=False)
    body = _zip(PRIMARY.read_bytes())
    cached = tmp_path / "2022_A-12054_absentee.zip"
    cached.write_bytes(body)
    monkeypatch.setattr(ga._net, "cache_path", lambda state, filename: cached)

    assert ga.GAScraper()._download(2022, "A-12054", allow_cache=True) == body


def test_live_fetch_never_serves_a_stale_cache(monkeypatch, tmp_path):
    """Today's file changes every morning, so a cached copy is not an answer to
    'what does Georgia say now' -- only to 'what did 2022 finish at'."""
    monkeypatch.delenv(ga.TOKEN_ENV, raising=False)
    _gate(monkeypatch, up=True)
    cached = tmp_path / "2026_A-12601_absentee.zip"
    cached.write_bytes(_zip(GENERAL.read_bytes()))
    monkeypatch.setattr(ga._net, "cache_path", lambda state, filename: cached)

    with pytest.raises(SourceError):
        ga.GAScraper()._download(2026, "A-12601", allow_cache=False)


def test_history_parses_what_download_returns(monkeypatch):
    """Wiring check: fetch_history resolves the election, allows the cache, and
    dates the archived series at that cycle's Election Day."""
    monkeypatch.setattr(ga.GAScraper, "election_number", lambda self, cycle: "A-12054")
    seen: dict = {}

    def download(self, cycle, number, *, allow_cache):
        seen.update(cycle=cycle, number=number, allow_cache=allow_cache)
        return PRIMARY.read_bytes()

    monkeypatch.setattr(ga.GAScraper, "_download", download)
    monkeypatch.setattr(
        ga, "parse",
        lambda body, cycle, as_of: seen.update(as_of=as_of) or ga.FetchResult(),
    )
    ga.GAScraper().fetch_history(2022)
    assert seen == {"cycle": 2022, "number": "A-12054", "allow_cache": True,
                    "as_of": date(2022, 11, 8)}


def test_current_cycle_has_no_archive():
    with pytest.raises(NotYetPublished):
        ga.GAScraper().fetch_history(2026)


def test_html_error_page_is_a_source_error():
    with pytest.raises(SourceError):
        ga.parse(b"<!DOCTYPE html><html>nope</html>", 2026, date(2026, 9, 5))


# --------------------------------------------------------------------------
# Georgia's own kill switch: the only route to an unattended Georgia
# --------------------------------------------------------------------------
RECAPTCHA_DETAILS = FIXTURES / "recaptcha_details.json"


def test_the_live_switch_really_is_on():
    """A verbatim capture of VrMvpUtility.getRecaptchaDetails on 2026-09-06.

    This fixture is the evidence for the whole "Georgia cannot run unattended"
    finding: both of the Secretary of State's switches are on. If a future
    capture disagrees, the tokenless path below is what should start working.
    """
    action = json.loads(RECAPTCHA_DETAILS.read_text())
    details = action["returnValue"]["returnValue"]
    assert action["state"] == "SUCCESS"
    assert [details[f] for f in ga.BOT_CHECK_FIELDS] == [True, True]


def test_bot_check_reads_the_real_payload(monkeypatch):
    action = json.loads(RECAPTCHA_DETAILS.read_text())
    monkeypatch.setattr(ga.GAScraper, "_apex", lambda self, c, m, p: action)
    assert ga.GAScraper().bot_check_active() is True


@pytest.mark.parametrize("flags", [
    {"Active__c": False, "Bot_Check_Active__c": True},
    {"Active__c": True, "Bot_Check_Active__c": False},
    {"Active__c": False, "Bot_Check_Active__c": False},
])
def test_either_switch_off_takes_the_gate_down(monkeypatch, flags):
    monkeypatch.setattr(
        ga.GAScraper, "_apex",
        lambda self, c, m, p: {"state": "SUCCESS", "returnValue": {"returnValue": flags}},
    )
    assert ga.GAScraper().bot_check_active() is False


@pytest.mark.parametrize("action", [
    {"state": "ERROR", "error": [{"message": "boom"}]},
    {"state": "SUCCESS", "returnValue": {"returnValue": None}},
    {"state": "SUCCESS", "returnValue": {"returnValue": "not a map"}},
    # A field disappearing is a shape we have never seen, not permission to
    # start hammering the presign endpoint.
    {"state": "SUCCESS", "returnValue": {"returnValue": {"Active__c": False}}},
])
def test_an_uncertain_answer_means_the_gate_is_up(monkeypatch, action):
    monkeypatch.setattr(ga.GAScraper, "_apex", lambda self, c, m, p: action)
    assert ga.GAScraper().bot_check_active() is True


def test_a_failed_probe_means_the_gate_is_up(monkeypatch):
    def boom(self, cls, method, params):
        raise SchemaDrift("GA: getRecaptchaDetails is gone")

    monkeypatch.setattr(ga.GAScraper, "_apex", boom)
    assert ga.GAScraper().bot_check_active() is True


def test_a_lowered_gate_is_tried_without_a_token(monkeypatch):
    """If Georgia ever switches its own bot check off, the nightly job starts
    working with no code change. This is the whole reason we ask."""
    monkeypatch.delenv(ga.TOKEN_ENV, raising=False)
    _gate(monkeypatch, up=False)
    monkeypatch.setattr(ga.GAScraper, "election_number", lambda self, cycle: "A-12601")
    seen: dict = {}

    def presign(self, key, token):
        seen.update(key=key, token=token)
        return f"https://{ga.BUCKET_HOST}/{key}?sig=x"

    monkeypatch.setattr(ga.GAScraper, "presigned_url", presign)
    monkeypatch.setattr(ga._net, "get", lambda url, **kw: _zip(GENERAL.read_bytes()))

    result = ga.GAScraper().fetch(2026, date(2026, 9, 5))
    assert seen == {"key": "GAVR/ABSENTEE_ZIP/2026/A-12601/A-12601.zip", "token": ""}
    assert result.state_rows[-1].mail_requested > 0


def test_a_lowered_gate_that_still_refuses_is_the_same_source_error(monkeypatch):
    """The tokenless branch is an ATTEMPT, not an assumption -- it cannot be
    exercised against the live site while the switch is on, so a refusal has to
    land on the ordinary fall-through and say the ordinary thing."""
    monkeypatch.delenv(ga.TOKEN_ENV, raising=False)
    _gate(monkeypatch, up=False)

    def refuse(self, key, token):
        raise SourceError("GA: presign of ... failed: ['V3 Recaptcha Failed']")

    monkeypatch.setattr(ga.GAScraper, "presigned_url", refuse)
    with pytest.raises(SourceError) as caught:
        ga.GAScraper()._download(2026, "A-12601", allow_cache=False)
    assert not isinstance(caught.value, NotYetPublished)
    assert "cannot be collected unattended" in str(caught.value)
    assert "V3 Recaptcha Failed" in str(caught.value)


def test_a_lowered_gate_does_not_flatten_drift_into_the_captcha_message(monkeypatch):
    """Salesforce changing shape under us is its own signal, not a captcha."""
    monkeypatch.delenv(ga.TOKEN_ENV, raising=False)
    _gate(monkeypatch, up=False)

    def drifted(self, key, token):
        raise SchemaDrift("GA: presign response carried no download_url")

    monkeypatch.setattr(ga.GAScraper, "presigned_url", drifted)
    with pytest.raises(SchemaDrift, match="download_url"):
        ga.GAScraper()._download(2026, "A-12601", allow_cache=False)


def test_a_lowered_gate_still_reports_an_unposted_file_as_not_yet_published(monkeypatch):
    """NotYetPublished must survive the tokenless branch: an election whose zip
    is not up yet is not the same condition as a gate we cannot pass."""
    monkeypatch.delenv(ga.TOKEN_ENV, raising=False)
    _gate(monkeypatch, up=False)
    monkeypatch.setattr(
        ga.GAScraper, "presigned_url",
        lambda self, key, token: f"https://{ga.BUCKET_HOST}/{key}?sig=x",
    )

    def missing(*args, **kwargs):
        raise ga._net.Missing("404")

    monkeypatch.setattr(ga._net, "get", missing)
    with pytest.raises(NotYetPublished):
        ga.GAScraper()._download(2026, "A-12601", allow_cache=False)


def test_the_cache_still_wins_before_the_switch_is_consulted(monkeypatch, tmp_path):
    """A backfill off a cached archive must not touch the network at all."""
    monkeypatch.delenv(ga.TOKEN_ENV, raising=False)
    body = _zip(PRIMARY.read_bytes())
    cached = tmp_path / "2022_A-12054_absentee.zip"
    cached.write_bytes(body)
    monkeypatch.setattr(ga._net, "cache_path", lambda state, filename: cached)

    def never(self, cls, method, params):  # pragma: no cover - must not run
        raise AssertionError("the cached archive should not need the network")

    monkeypatch.setattr(ga.GAScraper, "_apex", never)
    assert ga.GAScraper()._download(2022, "A-12054", allow_cache=True) == body


def test_the_recorded_site_key_is_the_enterprise_one():
    """Recorded so a reader can tell this is reCAPTCHA Enterprise, not classic
    v3. The adapter never calls Google; the key is evidence, not plumbing."""
    assert ga.RECAPTCHA_SITE_KEY.startswith("6Ld")
    assert "recaptcha" not in ga.PAGE


# --------------------------------------------------------------------------
# The presign payload must match the page BYTE FOR BYTE
# --------------------------------------------------------------------------
def test_token_payload_matches_the_pages_own_shape():
    """`handleZipFile(token, "V3", fileName)` posts
    `{recaptchaResponse: JSON.stringify({response, action}), version: "V3"}`.

    The Apex validates the SHAPE before it validates the token, so an invented
    shape is refused for the wrong reason -- it answers "Missing necessary
    information" rather than "V3 Recaptcha Failed", and a maintainer reading the
    log would conclude Georgia is captcha'd when we had actually sent nonsense.
    """
    params = ga.recaptcha_params("03AF-a-real-token")
    assert params["version"] == "V3"
    assert json.loads(params["recaptchaResponse"]) == {
        "response": "03AF-a-real-token",
        "action": "Submit",
    }


def test_tokenless_payload_matches_the_pages_gate_down_shape():
    """With `Active__c` false the page calls `handleZipFile("", "", fileName)`,
    i.e. `JSON.stringify("")` -- the two-character string `""` -- and an EMPTY
    version, not "V3".

    This is the branch that would start working if Georgia lowered its gate, so
    it has to speak the SoS's language rather than ours.
    """
    params = ga.recaptcha_params("")
    assert params == {"recaptchaResponse": '""', "version": ""}


def test_the_tokenless_shape_is_not_the_token_shape():
    """Guards the pair above from drifting back together. The old code sent
    `{"response": "", "action": "Submit"}` with version "V3" for BOTH, which is
    a shape the live action has never been observed to accept."""
    assert ga.recaptcha_params("") != ga.recaptcha_params("tok")
    assert "response" not in json.loads(ga.recaptcha_params("")["recaptchaResponse"] or '""')


REFUSAL = FIXTURES / "presign_refusal_browser_token.json"


def test_a_real_browser_minted_token_is_still_refused():
    """The evidence for "a scripted browser does not get past this".

    Captured 2026-09-06: headless Playwright Chromium loaded the page, reCAPTCHA
    Enterprise minted a genuine 2,318-character token in under a second, and the
    presign -- sent in the page's own shape -- still came back "V3 Recaptcha
    Failed". Minting is not the hard part; passing the server-side assessment is,
    and a fresh automated browser scores below Georgia's threshold.

    If a future capture of this shows SUCCESS, a browser step became viable and
    the module docstring's headline needs rewriting.
    """
    captured = json.loads(REFUSAL.read_text())
    assert captured["_token_length"] > 1000, "a real token, not a stub"
    assert captured["_request_params"]["version"] == "V3"
    action = captured["action"]
    assert action["state"] == "ERROR"
    assert [e["message"] for e in action["error"]] == ["V3 Recaptcha Failed"]


def test_that_refusal_becomes_a_source_error_not_not_yet_published(monkeypatch):
    """However it fails, a refused token must fall THROUGH to the aggregator."""
    action = json.loads(REFUSAL.read_text())["action"]
    monkeypatch.setattr(ga.GAScraper, "_apex", lambda self, c, m, p: action)
    with pytest.raises(SourceError) as caught:
        ga.GAScraper().presigned_url("GAVR/ABSENTEE_ZIP/2026/A-12601/A-12601.zip", "tok")
    assert not isinstance(caught.value, NotYetPublished)
    assert "V3 Recaptcha Failed" in str(caught.value)


def test_presign_sends_exactly_those_params(monkeypatch):
    """Wiring: presigned_url must hand the Apex call the page's pair plus the
    object key, and nothing else."""
    seen = {}

    def apex(self, cls, method, params):
        seen.update(cls=cls, method=method, params=params)
        return {"state": "SUCCESS",
                "returnValue": {"returnValue": '"{\\"download_url\\": \\"https://x/y\\"}"'}}

    monkeypatch.setattr(ga.GAScraper, "_apex", apex)
    url = ga.GAScraper().presigned_url("GAVR/ABSENTEE_ZIP/2026/A-12601/A-12601.zip", "tok")
    assert url == "https://x/y"
    assert (seen["cls"], seen["method"]) == (
        "VrMvpUtility", "getPublicDownloadPresignedContent")
    assert seen["params"] == {
        "fileName": "GAVR/ABSENTEE_ZIP/2026/A-12601/A-12601.zip",
        **ga.recaptcha_params("tok"),
    }


def test_adapter_identity():
    scraper = ga.GAScraper()
    assert (scraper.state, scraper.name, scraper.tier) == ("GA", "ga-sos", 1)
