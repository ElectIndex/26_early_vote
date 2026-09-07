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
