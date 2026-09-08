"""`_net.get`: one function, thirty-five adapters, and 0% executed until now.

⚠️ EVERY ADAPTER TEST MONKEYPATCHES THIS AWAY. `test_nc.py` and its thirty-four
siblings all do `monkeypatch.setattr(nc._net, "get", ...)` so that a parse test
reads a saved fixture instead of hammering a state election site -- which is the
right thing for a parse test and leaves the download itself completely untested.
Replacing the body of `_net.get` with `raise AssertionError` and running the
suite gave 1311 passed: the function never ran once.

That matters more than a normal coverage hole, because `_net.get` is where the
LADDER DECISION is made for every state at once. It translates HTTP into the
adapter vocabulary, and the two outcomes are opposites:

    Missing (404/410, or a 200 too short to be real)  ->  the caller usually
        turns this into NotYetPublished, which STOPS the ladder.
    SourceError (anything else)                       ->  fall THROUGH to a
        weaker tier.

Get that backwards and a state either publishes a weaker source's number when
its own file simply is not posted yet, or stops dead when it should have fallen
through. Three paths in particular had never executed: the `min_bytes` soft-404
guard, the `use_cache` hit, and the 403 -> impersonate -> 404 retry that the
module's own docstring calls a correctness fix rather than a reach fix.

Nothing here touches the network or the repo's `cache/`.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import requests

from ev.adapters import _net
from ev.adapters._net import Missing
from ev.adapters.base import SourceError

URL = "https://sos.example.gov/reports/daily.csv"
OTHER_HOST = "https://elections.example.org/daily.csv"
BODY = b"county,ballots\nWake,12345\n" * 8  # comfortably over the 64-byte floor


class FakeResponse:
    def __init__(self, status_code: int = 200, content: bytes = BODY) -> None:
        self.status_code = status_code
        self.content = content

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300


class FakeClock:
    """Replaces `_net.time` so the throttle is asserted rather than waited out."""

    def __init__(self) -> None:
        self.now = 1_000.0
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


@pytest.fixture()
def clock(monkeypatch) -> FakeClock:
    fake = FakeClock()
    monkeypatch.setattr(_net, "time", fake)
    monkeypatch.setattr(_net, "_last_request", {})
    return fake


@pytest.fixture(autouse=True)
def sandbox(tmp_path, monkeypatch, clock):
    """No request leaves the process and no byte lands in the repo's cache/."""
    monkeypatch.setattr(_net, "CACHE_DIR", tmp_path / "cache")
    # curl_cffi is an OPTIONAL dependency. Default it to absent so a machine that
    # happens to have it installed cannot change what these tests exercise.
    monkeypatch.setattr(_net, "_curl", None)

    def unstubbed(*args, **kwargs):  # pragma: no cover - a test bug, not a path
        raise AssertionError("this test reached the real SESSION.get")

    monkeypatch.setattr(_net.SESSION, "get", unstubbed)


def serve(monkeypatch, *responses, record=None):
    """Answer successive SESSION.get calls with `responses`."""
    queue = list(responses)

    def fake_get(url, **kwargs):
        if record is not None:
            record.append((url, kwargs))
        return queue.pop(0) if len(queue) > 1 else queue[0]

    monkeypatch.setattr(_net.SESSION, "get", fake_get)
    return queue


def fetch(**kw):
    kw.setdefault("state", "NC")
    kw.setdefault("filename", "daily.csv")
    kw.setdefault("min_interval", 0)
    return _net.get(kw.pop("url", URL), **kw)


# --------------------------------------------------------------------------
# The happy path, and the copy on disk that makes a parser re-runnable
# --------------------------------------------------------------------------
def test_a_200_is_returned_and_mirrored_into_the_cache(monkeypatch, tmp_path):
    serve(monkeypatch, FakeResponse())
    assert fetch() == BODY
    assert (tmp_path / "cache" / "nc" / "daily.csv").read_bytes() == BODY


def test_the_caller_headers_extend_the_browser_ones_rather_than_replacing_them(monkeypatch):
    calls: list[tuple] = []
    serve(monkeypatch, FakeResponse(), record=calls)
    fetch(headers={"Accept": "text/csv", "X-Trace": "1"})
    sent = calls[0][1]["headers"]
    assert sent["User-Agent"] == _net.DEFAULT_HEADERS["User-Agent"]
    assert sent["Accept"] == "text/csv"      # the caller wins on a collision
    assert sent["X-Trace"] == "1"


def test_the_user_agent_carries_no_suffix(monkeypatch):
    """⚠️ A VERIFIED FACT ABOUT michigan.gov, not a style preference.

    Its WAF 403s any User-Agent with a token after `Safari/537.36` -- including a
    bare `(+https://electindex.com/early-vote/)` with no mention of a bot. The
    courtesy self-identification lives in `From`, which it does not inspect. The
    obvious "let's be polite" edit silently loses Michigan.
    """
    assert _net.DEFAULT_HEADERS["User-Agent"].endswith("Safari/537.36")
    assert _net.DEFAULT_HEADERS["From"] == "contact@electindex.com"


# --------------------------------------------------------------------------
# Status translation: Missing STOPS the ladder, SourceError falls through
# --------------------------------------------------------------------------
@pytest.mark.parametrize("status", [404, 410])
def test_a_missing_file_is_Missing_so_the_caller_can_stop_the_ladder(monkeypatch, status):
    serve(monkeypatch, FakeResponse(status, b""))
    with pytest.raises(Missing) as exc:
        fetch()
    assert str(status) in str(exc.value)


@pytest.mark.parametrize("status", [400, 429, 500, 502, 503])
def test_any_other_refusal_is_a_plain_SourceError(monkeypatch, status):
    """Not Missing: a 500 is not evidence that the file does not exist, and
    reading it as one would stop the ladder on a transient outage."""
    serve(monkeypatch, FakeResponse(status, b""))
    with pytest.raises(SourceError) as exc:
        fetch()
    assert not isinstance(exc.value, Missing)
    assert str(status) in str(exc.value)


def test_Missing_is_a_SourceError_so_a_forgetful_adapter_degrades(monkeypatch):
    """Every adapter catches Missing deliberately; one that forgets must fall
    through to a weaker tier rather than crash the state."""
    assert issubclass(Missing, SourceError)


def test_a_transport_failure_is_a_SourceError_naming_the_url(monkeypatch):
    def explode(url, **kwargs):
        raise requests.ConnectionError("connection reset by peer")

    monkeypatch.setattr(_net.SESSION, "get", explode)
    with pytest.raises(SourceError) as exc:
        fetch()
    assert URL in str(exc.value) and "reset" in str(exc.value)


# --------------------------------------------------------------------------
# THE SOFT-404 GUARD: a state CMS answering "page not found" with status 200
# --------------------------------------------------------------------------
def test_a_200_that_is_too_short_to_be_real_is_Missing(monkeypatch):
    """State CMSes answer a missing report with a styled 200 far more often than
    with a 404, and status alone cannot tell them apart. Size can."""
    serve(monkeypatch, FakeResponse(200, b"not found"))
    with pytest.raises(Missing) as exc:
        fetch()
    assert "9 bytes" in str(exc.value)


def test_a_soft_404_is_never_written_to_the_cache(monkeypatch, tmp_path):
    """⚠️ Otherwise the shell poisons every later `use_cache=True` read of that
    filename -- an archived file that can never change, permanently wrong."""
    serve(monkeypatch, FakeResponse(200, b"not found"))
    with pytest.raises(Missing):
        fetch()
    assert not (tmp_path / "cache" / "nc" / "daily.csv").exists()


def test_the_floor_is_a_parameter_so_a_genuinely_tiny_file_still_passes(monkeypatch):
    serve(monkeypatch, FakeResponse(200, b"0"))
    assert fetch(min_bytes=1) == b"0"


def test_an_empty_200_is_Missing_not_an_empty_success(monkeypatch):
    serve(monkeypatch, FakeResponse(200, b""))
    with pytest.raises(Missing):
        fetch()


# --------------------------------------------------------------------------
# use_cache: for ARCHIVED dated files only, never for today's snapshot
# --------------------------------------------------------------------------
def test_a_cache_hit_makes_no_request_at_all(monkeypatch, tmp_path):
    """Re-running a parser against a Wayback backfill must not re-download it."""
    cached = tmp_path / "cache" / "nc" / "archive-2024-10-20.csv"
    cached.parent.mkdir(parents=True)
    cached.write_bytes(BODY)
    # SESSION.get is left as the sandbox's exploding stub: a request is a failure.
    assert fetch(filename="archive-2024-10-20.csv", use_cache=True) == BODY


def test_use_cache_is_off_by_default_so_todays_file_is_always_re_fetched(monkeypatch, tmp_path):
    """⚠️ The whole point of running daily. A stale hit would freeze a state's
    curve at whatever it said the first time we looked."""
    cached = tmp_path / "cache" / "nc" / "daily.csv"
    cached.parent.mkdir(parents=True)
    cached.write_bytes(b"yesterday's numbers, padded out past the min_bytes floor")
    fresh = BODY + b"today\n"
    serve(monkeypatch, FakeResponse(200, fresh))
    assert fetch() == fresh
    assert cached.read_bytes() == fresh


def test_a_cached_soft_404_is_not_served_and_the_file_is_re_fetched(monkeypatch, tmp_path):
    """A short cached file is a stored error shell, not an archive."""
    cached = tmp_path / "cache" / "nc" / "archive.csv"
    cached.parent.mkdir(parents=True)
    cached.write_bytes(b"404")
    serve(monkeypatch, FakeResponse(200, BODY))
    assert fetch(filename="archive.csv", use_cache=True) == BODY


def test_use_cache_with_no_cached_copy_just_downloads(monkeypatch):
    serve(monkeypatch, FakeResponse())
    assert fetch(use_cache=True) == BODY


# --------------------------------------------------------------------------
# 403 -> IMPERSONATE -> the honest answer. Correctness, not reach.
# --------------------------------------------------------------------------
class FakeCurl:
    """Stands in for the optional `curl_cffi.requests` module."""

    def __init__(self, by_profile: dict) -> None:
        self.by_profile = by_profile
        self.tried: list[str] = []

    def get(self, url, *, timeout, headers, params, impersonate):
        self.tried.append(impersonate)
        answer = self.by_profile[impersonate]
        if isinstance(answer, Exception):
            raise answer
        return answer


def test_a_403_hiding_a_404_stops_the_ladder_instead_of_falling_through(monkeypatch):
    """⚠️ THE REASON THIS RETRY EXISTS, and it is a correctness fix.

    ohiosos.gov and azsos.gov sit behind a rule that scores any Python client's
    TLS fingerprint as automation and answers 403 -- to their homepages and to
    archived files alike. A 403 is a SourceError, so the ladder falls THROUGH to
    a weaker tier; the honest answer behind the wall is very often a 404, which
    means "not posted yet" and must STOP the ladder. Ohio and Arizona were both
    written off as unreachable when the truth was "not posted yet".
    """
    curl = FakeCurl({"chrome": FakeResponse(404, b"")})
    monkeypatch.setattr(_net, "_curl", curl)
    serve(monkeypatch, FakeResponse(403, b""))

    with pytest.raises(Missing) as exc:
        fetch()
    assert "404" in str(exc.value)
    assert curl.tried == ["chrome"]  # a non-403 settles it in one request


def test_the_retry_walks_the_profiles_until_one_is_not_refused(monkeypatch):
    """⚠️ ONE PROFILE IS NOT ENOUGH, and the evidence is specific: from one
    machine in one second, vote.sos.ri.gov answered 403 to chrome and 200 to
    safari. A WAF rule can be keyed to a particular fingerprint, so a state whose
    single profile happens to be blocked reads as unreachable when it is not."""
    curl = FakeCurl({"chrome": FakeResponse(403, b""), "safari": FakeResponse(200, BODY)})
    monkeypatch.setattr(_net, "_curl", curl)
    serve(monkeypatch, FakeResponse(403, b""))

    assert fetch() == BODY
    assert curl.tried == ["chrome", "safari"]
    assert _net.IMPERSONATE_PROFILES[0] == "chrome"  # the common case goes first


def test_a_profile_that_raises_is_skipped_rather_than_ending_the_retry(monkeypatch):
    curl = FakeCurl({
        "chrome": OSError("TLS handshake failed"),
        "safari": FakeResponse(200, BODY),
    })
    monkeypatch.setattr(_net, "_curl", curl)
    serve(monkeypatch, FakeResponse(403, b""))
    assert fetch() == BODY
    assert curl.tried == ["chrome", "safari"]


def test_a_403_from_every_profile_stays_a_SourceError(monkeypatch):
    """A real refusal falls through to a weaker tier, which is the right answer."""
    curl = FakeCurl({p: FakeResponse(403, b"") for p in _net.IMPERSONATE_PROFILES})
    monkeypatch.setattr(_net, "_curl", curl)
    serve(monkeypatch, FakeResponse(403, b""))

    with pytest.raises(SourceError) as exc:
        fetch()
    assert not isinstance(exc.value, Missing)
    assert "403" in str(exc.value)
    assert curl.tried == list(_net.IMPERSONATE_PROFILES)


def test_every_profile_raising_leaves_the_original_403(monkeypatch):
    curl = FakeCurl({p: OSError("no") for p in _net.IMPERSONATE_PROFILES})
    monkeypatch.setattr(_net, "_curl", curl)
    serve(monkeypatch, FakeResponse(403, b""))
    with pytest.raises(SourceError) as exc:
        fetch()
    assert "403" in str(exc.value)


def test_without_curl_cffi_a_403_degrades_rather_than_crashing(monkeypatch):
    """curl_cffi is optional on purpose: a stock checkout without it must still
    work, with only the handful of fingerprint-walled hosts unreachable."""
    assert _net._curl is None  # set by the sandbox fixture
    serve(monkeypatch, FakeResponse(403, b""))
    with pytest.raises(SourceError) as exc:
        fetch()
    assert "403" in str(exc.value)


def test_the_retry_is_only_for_a_403(monkeypatch):
    curl = FakeCurl({})
    monkeypatch.setattr(_net, "_curl", curl)
    serve(monkeypatch, FakeResponse(503, b""))
    with pytest.raises(SourceError):
        fetch()
    assert curl.tried == []


# --------------------------------------------------------------------------
# The throttle, which we learned about from a real ban
# --------------------------------------------------------------------------
def test_two_hits_on_one_host_are_spaced(monkeypatch, clock):
    """⚠️ Louisiana's 2024 backfill is 1,026 PDFs from one host. Run flat out it
    drew a HOST-WIDE 403 -- on every URL, to requests and to every browser
    fingerprint alike -- that took about half an hour to clear, which is
    indistinguishable from a bot wall while it lasts."""
    serve(monkeypatch, FakeResponse())
    fetch(min_interval=0.4)
    assert clock.slept == []
    fetch(min_interval=0.4)
    assert clock.slept == [pytest.approx(0.4)]


def test_a_different_host_is_never_delayed_by_the_first(monkeypatch, clock):
    """Keyed on host, so a run that walks fifty states is never slowed by its
    own breadth -- only by returning to the same server too soon."""
    serve(monkeypatch, FakeResponse())
    fetch(min_interval=0.4)
    fetch(url=OTHER_HOST, min_interval=0.4)
    assert clock.slept == []


def test_enough_elapsed_time_costs_no_sleep(monkeypatch, clock):
    serve(monkeypatch, FakeResponse())
    fetch(min_interval=0.4)
    clock.now += 5
    fetch(min_interval=0.4)
    assert clock.slept == []


def test_a_zero_interval_short_circuits_entirely(monkeypatch, clock):
    serve(monkeypatch, FakeResponse())
    fetch(min_interval=0)
    fetch(min_interval=0)
    assert clock.slept == [] and _net._last_request == {}


def test_the_default_interval_is_gentle_rather_than_tuned():
    """A two-hourly cron has a whole season to spare, so throughput is worth
    nothing and a ban is expensive."""
    assert _net.DEFAULT_MIN_INTERVAL == 0.4


# --------------------------------------------------------------------------
# cache_path
# --------------------------------------------------------------------------
def test_cache_path_is_per_state_and_lowercase(tmp_path):
    path = _net.cache_path("NC", "daily.csv")
    assert path == tmp_path / "cache" / "nc" / "daily.csv"
    assert path.parent.is_dir()  # created eagerly, so a writer never has to


# ==========================================================================
# A BOT WALL DRESSED AS SUCCESS
#
# Every file below is a REAL capture taken on 2026-09-08, one request each,
# spaced. They are here because the shape of the problem is not guessable: a
# block that arrives with HTTP 200 and 212 bytes is indistinguishable from a
# state that published a tiny file, by status and by size alone.
# ==========================================================================
WALLS = Path(__file__).parent / "fixtures" / "net"

#: www.nvsos.gov to a plain `requests` GET: HTTP **200**, 212 bytes.
INCAPSULA_STUB = (WALLS / "incapsula_stub.html").read_bytes()
#: azsos.gov to a plain `requests` GET: HTTP 403, 5,845 bytes, "Just a moment...".
CLOUDFLARE_CHALLENGE = (WALLS / "cloudflare_challenge.html").read_bytes()
#: www.sos.nh.gov to a plain `requests` GET: HTTP 403, 413 bytes.
AKAMAI_DENIED = (WALLS / "akamai_access_denied.html").read_bytes()
#: www.nvsos.gov to a Chrome TLS fingerprint carrying OUR User-Agent: HTTP 403,
#: 640 bytes. The same URL with the header left off answers 200 with the page.
AKAMAI_UA_MISMATCH = (WALLS / "akamai_access_denied_ua_mismatch.html").read_bytes()
#: The last 4,096 bytes of the GENUINE 135,761-byte www.nvsos.gov turnout page.
#: Imperva injects its sensor script into the pages it protects, so the real page
#: carries `_Incapsula_Resource` too -- at offset 135,648, right before </body>.
REAL_PAGE_TAIL = (WALLS / "nvsos_real_page_tail.html").read_bytes()


@pytest.mark.parametrize("wall, expected_len", [
    (INCAPSULA_STUB, 212),
    (CLOUDFLARE_CHALLENGE, 5845),
    (AKAMAI_DENIED, 413),
    (AKAMAI_UA_MISMATCH, 640),
])
def test_every_wall_shape_we_have_actually_met_is_recognised(wall, expected_len):
    assert len(wall) == expected_len
    assert _net.looks_like_wall(wall) is True


def test_a_real_page_that_merely_mentions_the_vendor_is_not_a_wall():
    """⚠️ THE REASON `looks_like_wall` HAS A SIZE GATE AT ALL.

    Cloudflare and Imperva both inject a sensor script into the pages they
    protect, so the marker is present in genuine content. Marker alone would call
    the real 135,761-byte nvsos.gov page a block and fall Nevada through to a
    weaker tier -- a WRONG verdict, which is worse than the missed block that the
    gate risks in the other direction.
    """
    assert b"_Incapsula_Resource" in REAL_PAGE_TAIL
    # The tail on its own is small enough to trip the marker...
    assert _net.looks_like_wall(REAL_PAGE_TAIL) is True
    # ...but at the real page's real size it does not, which is the whole point.
    page = b"<html>" + b" " * (135_761 - len(REAL_PAGE_TAIL)) + REAL_PAGE_TAIL
    assert len(page) > _net.WALL_MAX_BYTES
    assert _net.looks_like_wall(page) is False


def test_ordinary_data_is_never_a_wall():
    assert _net.looks_like_wall(BODY) is False
    assert _net.looks_like_wall(b"") is False
    assert _net.looks_like_wall(b"county,ballots\nClark,1\n") is False


def test_the_size_gate_clears_every_wall_and_every_real_page_by_a_margin():
    """Measured 2026-09-08: walls run 212 b .. 5,950 b, real state CMS pages run
    135,760 b .. 2,960,857 b. The gate sits between them with room on both
    sides, so it is not tuned to a single observation."""
    assert max(len(w) for w in (INCAPSULA_STUB, CLOUDFLARE_CHALLENGE,
                                AKAMAI_DENIED, AKAMAI_UA_MISMATCH)) * 5 < _net.WALL_MAX_BYTES
    assert _net.WALL_MAX_BYTES * 4 < 135_760


# --------------------------------------------------------------------------
# ...which means the RETRY cannot be keyed on the status line
# --------------------------------------------------------------------------
def test_a_wall_served_under_200_is_retried_with_a_fingerprint(monkeypatch):
    """⚠️ THE NEVADA BUG. Imperva answers www.nvsos.gov with HTTP 200 and a
    212-byte stub, so a status-only trigger never retried Nevada at all: the
    whole state read as "the SoS publishes an unparseable page" for two cycles.
    Verified 2026-09-08 that the same URL under the impersonated fingerprint
    returns the real 135,760-byte page."""
    curl = FakeCurl({"chrome": FakeResponse(200, BODY)})
    monkeypatch.setattr(_net, "_curl", curl)
    serve(monkeypatch, FakeResponse(200, INCAPSULA_STUB))

    assert fetch() == BODY
    assert curl.tried == ["chrome"]


def test_a_wall_that_survives_every_profile_is_a_SourceError_not_Missing(monkeypatch):
    """⚠️ AND IT MUST NOT BE Missing. Every wall page is small, so the `min_bytes`
    floor would fire first and raise Missing -- which the caller turns into
    NotYetPublished and the LADDER STOPS ON, leaving the state blank with "not
    published yet" in the log for a file sitting right there behind the block."""
    curl = FakeCurl({p: FakeResponse(200, INCAPSULA_STUB)
                     for p in _net.IMPERSONATE_PROFILES})
    monkeypatch.setattr(_net, "_curl", curl)
    serve(monkeypatch, FakeResponse(200, INCAPSULA_STUB))

    with pytest.raises(SourceError) as exc:
        fetch()
    assert not isinstance(exc.value, Missing)
    assert "bot wall" in str(exc.value)
    assert curl.tried == list(_net.IMPERSONATE_PROFILES)


def test_a_wall_is_never_written_to_the_cache(monkeypatch, tmp_path):
    """Otherwise it poisons every later `use_cache=True` read of that filename."""
    serve(monkeypatch, FakeResponse(200, INCAPSULA_STUB))
    with pytest.raises(SourceError):
        fetch()
    assert not (tmp_path / "cache" / "nc" / "daily.csv").exists()


def test_a_profile_answering_with_a_wall_is_not_taken_as_the_answer(monkeypatch):
    """A 200 carrying a block page is still a refusal, so the walk has to keep
    going. Reading it as success stops on the first profile holding a wall."""
    curl = FakeCurl({
        "chrome": FakeResponse(200, INCAPSULA_STUB),
        "safari": FakeResponse(404, b""),
    })
    monkeypatch.setattr(_net, "_curl", curl)
    serve(monkeypatch, FakeResponse(403, CLOUDFLARE_CHALLENGE))

    with pytest.raises(Missing):
        fetch()
    assert curl.tried == ["chrome", "safari"]


def test_without_curl_cffi_a_walled_200_still_refuses_rather_than_returning_it(monkeypatch):
    """A stock checkout without curl_cffi cannot get past the wall, but it must
    not hand the block page to a parser either."""
    assert _net._curl is None  # set by the sandbox fixture
    serve(monkeypatch, FakeResponse(200, INCAPSULA_STUB))
    with pytest.raises(SourceError) as exc:
        fetch()
    assert "bot wall" in str(exc.value)


# --------------------------------------------------------------------------
# NEVER SEND A USER-AGENT THAT CONTRADICTS THE FINGERPRINT
# --------------------------------------------------------------------------
def test_the_impersonated_retry_drops_our_user_agent(monkeypatch):
    """⚠️ A VERIFIED FACT ABOUT www.nvsos.gov, not tidiness.

    `impersonate=<profile>` presents that browser's TLS/HTTP2 fingerprint AND its
    matching User-Agent. Overriding the UA with ours leaves a request whose
    handshake says one Chrome and whose header says another -- something no real
    browser can produce, and a cleaner automation signal than either half alone.

    Verified 2026-09-08, `/sos/elections/voters/election-turnout-statistics`,
    `impersonate="chrome"`, four requests ten seconds apart:
        headers={}                      -> 200, 135,760 b, the real page
        headers={"Accept": "*/*"}       -> 200, the real page
        headers={"From": "contact@..."} -> 200, the real page
        headers={"User-Agent": OURS}    -> 403,     640 b, AkamaiGHost
    """
    calls: list[dict] = []

    class Recorder:
        def get(self, url, *, timeout, headers, params, impersonate):
            calls.append(headers)
            return FakeResponse(200, BODY)

    monkeypatch.setattr(_net, "_curl", Recorder())
    serve(monkeypatch, FakeResponse(403, AKAMAI_UA_MISMATCH))
    assert fetch() == BODY

    sent = calls[0]
    assert "User-Agent" not in sent
    # ...and nothing else is lost: the courtesy address and Accept still go.
    assert sent["From"] == "contact@electindex.com"
    assert sent["Accept"] == "*/*"


def test_the_plain_request_still_sends_our_user_agent(monkeypatch):
    """⚠️ michigan.gov's WAF needs it (and 403s any suffix on it). Dropping the UA
    is ONLY for the impersonated retry, where curl_cffi supplies a better one."""
    calls: list[tuple] = []
    serve(monkeypatch, FakeResponse(), record=calls)
    fetch()
    assert calls[0][1]["headers"]["User-Agent"] == _net.DEFAULT_HEADERS["User-Agent"]


def test_even_an_adapters_own_user_agent_is_dropped_on_the_retry():
    """⚠️ INCLUDING tx.py's, and that is the point rather than an oversight.

    Texas overrides the UA because its SoS's WAF 403s any User-Agent carrying the
    word "bot", which reads like a deliberate choice to preserve. It is not: every
    UA override here exists to look more like a browser, and on an impersonated
    request curl_cffi's own UA does that better because it is the one the
    handshake already claims. Keeping tx.py's string would hand Texas the exact
    mismatch Nevada 403s -- this bug, re-created one adapter over.
    """
    for supplied in (dict(_net.DEFAULT_HEADERS),
                     {"User-Agent": "SpecificClient/1.0", "Accept": "text/csv"},
                     {"user-agent": "lowercase/1.0"}):
        sent = _net.impersonated_headers(supplied)
        assert not any(k.lower() == "user-agent" for k in sent)
    # ...and nothing else is touched.
    assert _net.impersonated_headers(
        {"User-Agent": "x", "Accept": "text/csv", "X-Key": "k"}
    ) == {"Accept": "text/csv", "X-Key": "k"}


def test_the_akamai_marker_survives_its_own_entity_encoding():
    """⚠️ CAUGHT BY THE SAVED FIXTURE, not by reading the page in a browser.

    Akamai writes its reference URL entity-encoded --
    `https&#58;&#47;&#47;errors&#46;edgesuite&#46;net&#47;...` -- so the plain
    spelling `errors.edgesuite.net` matches nothing at all. Both forms are in
    WALL_MARKERS; drop the encoded one and New Hampshire's 413-byte block page
    reads as an ordinary short body again.
    """
    assert b"errors.edgesuite.net" not in AKAMAI_DENIED
    assert b"errors&#46;edgesuite&#46;net" in AKAMAI_DENIED
    assert _net.looks_like_wall(AKAMAI_DENIED) is True


def test_a_cached_wall_is_not_served_and_the_file_is_re_fetched(monkeypatch, tmp_path):
    """⚠️ `use_cache=True` is only ever passed for an ARCHIVED file that can never
    change, so nothing else would ever re-fetch it. A block page saved there
    before the download path could recognise one -- az.py's `fetch_history` reads
    `2024-election-info.html` off disk, and a 5,845-byte Cloudflare challenge
    clears its 2,048-byte floor -- would be parsed as Arizona's page for ever."""
    cached = tmp_path / "cache" / "nc" / "daily.csv"
    cached.parent.mkdir(parents=True)
    cached.write_bytes(CLOUDFLARE_CHALLENGE)
    real = BODY * 200  # a page-sized file, comfortably over the 2,048-byte floor
    serve(monkeypatch, FakeResponse(200, real))

    assert fetch(use_cache=True, min_bytes=2048) == real
    assert cached.read_bytes() == real  # and the poisoned copy is replaced


def test_the_profile_walk_is_throttled_like_any_other_request(monkeypatch, clock):
    """⚠️ WIDENING THE TRIGGER WIDENED THE BAN RISK, and this is the offset.

    A 403 is rare, so three unspaced profile attempts behind one were a
    curiosity. A wall served under HTTP 200 is what a walled host returns to
    EVERY request, so the same loop became three unspaced requests every time,
    against precisely the hosts that ban -- which is how Louisiana's backfill
    drew a host-wide 403 that took half an hour to clear.
    """
    curl = FakeCurl({p: FakeResponse(200, INCAPSULA_STUB)
                     for p in _net.IMPERSONATE_PROFILES})
    monkeypatch.setattr(_net, "_curl", curl)
    serve(monkeypatch, FakeResponse(200, INCAPSULA_STUB))

    with pytest.raises(SourceError):
        fetch(min_interval=0.4)
    assert curl.tried == list(_net.IMPERSONATE_PROFILES)
    # One wait before each profile: the first because get() has just hit this
    # host, the rest because the previous profile did.
    assert clock.slept == [pytest.approx(0.4)] * len(_net.IMPERSONATE_PROFILES)


def test_a_zero_interval_still_short_circuits_the_profile_walk(monkeypatch, clock):
    """A caller that has asked for no spacing (a test, or a host known to
    tolerate it) must not have it reintroduced behind its back."""
    curl = FakeCurl({p: FakeResponse(403, b"") for p in _net.IMPERSONATE_PROFILES})
    monkeypatch.setattr(_net, "_curl", curl)
    serve(monkeypatch, FakeResponse(403, b""))
    with pytest.raises(SourceError):
        fetch(min_interval=0)
    assert clock.slept == []
