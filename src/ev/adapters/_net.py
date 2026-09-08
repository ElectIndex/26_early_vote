"""Shared download plumbing for the scraper tier.

Every state adapter needs the same four things and none of them are interesting
enough to write six times: a browser-ish User-Agent (several state election sites
403 the python-requests default), a timeout, a translation of HTTP status into
the adapter exception vocabulary, and a copy of the bytes on disk under `cache/`
so a parser can be re-run without hammering a state election site in October.

The status translation is the part that matters:

    404 / 410  ->  Missing   -- the caller decides. For "today's file", missing
                               means NotYetPublished (STOP the ladder); for a
                               file we know should exist it means SourceError.
    a bot wall, WHATEVER STATUS IT WEARS -> SourceError. See WALL_MARKERS. A
                               block is a reachability problem; reading a
                               212-byte Imperva stub as "too short to be real"
                               would stop the ladder on a file that exists.
    any other non-2xx, timeout, connection reset, truncated body -> SourceError.

`Missing` subclasses `SourceError` so that an adapter which forgets to catch it
degrades to "fall through to the next tier" rather than crashing the run -- but
every adapter here catches it deliberately.
"""

from __future__ import annotations

import logging
import ssl
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.ssl_ import create_urllib3_context

from .base import SourceError

log = logging.getLogger(__name__)

#: Repo-root cache/. Gitignored: raw downloads are re-fetchable and some are huge.
CACHE_DIR = Path(__file__).resolve().parents[3] / "cache"

DEFAULT_TIMEOUT = 60

#: A handful of state election sites (OH and TX in particular) reject the default
#: python-requests User-Agent outright, so we send a real one.
DEFAULT_HEADERS = {
    # ONLY a real User-Agent -- nothing appended. michigan.gov's WAF 403s any UA
    # with a token after "Safari/537.36", including a bare
    # "(+https://electindex.com/early-vote/)" with no mention of a bot. The
    # courtesy self-identification moves to `From`, which it does not inspect.
    # Verified 2026-09-06: suffix -> 403, no suffix -> 200.
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "From": "contact@electindex.com",
}


class _TicketAdapter(HTTPAdapter):
    """Restore TLS session tickets in the ClientHello.

    urllib3 sets OP_NO_TICKET unconditionally, which drops TLS extension 35.
    Cloudflare's managed ruleset scores the resulting JA3 as automation and 403s
    coloradosos.gov -- its homepage included, and files we know exist included.
    curl and stdlib urllib both send the extension and were never blocked, which
    is why this looked like a site blocking us rather than us fingerprinting as
    a bot.

    Verified 2026-09-06: with OP_NO_TICKET set every coloradosos.gov URL 403s;
    cleared, the archived 2024-10-31 workbook returns 200 and the unposted 2026
    file returns an honest 404 -- which is the difference between "the state is
    blocking us" and "the file does not exist yet", and therefore between
    falling through to a weaker tier and correctly stopping the ladder.

    Certificate verification is unchanged: CERT_REQUIRED, check_hostname True.
    """

    def init_poolmanager(self, *args, **kwargs):
        ctx = create_urllib3_context()
        ctx.options &= ~ssl.OP_NO_TICKET
        kwargs["ssl_context"] = ctx
        return super().init_poolmanager(*args, **kwargs)


#: Module-level so connections are reused across a run. The ingest walk is
#: sequential; if that ever changes, give each worker its own session.
SESSION = requests.Session()
SESSION.mount("https://", _TicketAdapter())


# ==========================================================================
# A BOT WALL DRESSED AS SUCCESS
#
# The status line is not a reliable signal that we were refused. Imperva serves
# its interstitial with HTTP 200; so, on some configurations, does Akamai. A
# block that arrives as a 200 is worse than a 403 in a specific way: `get()`
# hands the bytes back and the adapter parses a block page, or -- because these
# pages are tiny -- the `min_bytes` floor fires and turns the wall into Missing,
# which the caller reads as "not published yet" and STOPS THE LADDER. A wall is
# a REACHABILITY problem and must fall THROUGH to a weaker tier; absence is the
# only thing allowed to stop the walk.
# ==========================================================================

#: Strings that appear only in a vendor's block/challenge page.
#:
#: Verified 2026-09-08, each against a live capture kept in this repo:
#:   `_Incapsula_Resource`         Imperva. www.nvsos.gov answers a plain
#:                                 `requests` GET with HTTP 200 and a 212-byte
#:                                 stub, and an unimpersonated browser-header GET
#:                                 with HTTP 200 and a 1,030-byte interstitial.
#:                                 (tests/fixtures/nv/incapsula_block.html)
#:   `Incapsula incident ID`       the same wall's other rendering.
#:   `/cdn-cgi/challenge-platform` Cloudflare's managed challenge; azsos.gov
#:   `cf_chl_opt`                  answered `requests` with 403 + a 5,867-byte
#:                                 "Just a moment..." page.
#:   `edgesuite&#46;net`           Akamai's "Access Denied" template, which points
#:   `edgesuite.net`               the reader at an errors.edgesuite.net writeup.
#:                                 sos.nh.gov answers `requests` with 403 + 413
#:                                 bytes of it; www.nvsos.gov answers a
#:                                 User-Agent that contradicts its TLS
#:                                 fingerprint with 403 + 640 bytes of it.
#:
#: ⚠️ AKAMAI HTML-ENTITY-ENCODES THAT URL -- the body reads
#: `https&#58;&#47;&#47;errors&#46;edgesuite&#46;net&#47;...`, so the plain
#: spelling never matches and the saved fixture is the only reason we know. Both
#: encodings are listed rather than the bytes being entity-decoded first, because
#: decoding every body before testing it costs more than two constants.
WALL_MARKERS = (
    b"_Incapsula_Resource",
    b"Incapsula incident ID",
    b"/cdn-cgi/challenge-platform",
    b"cf_chl_opt",
    b"errors.edgesuite.net",
    b"errors&#46;edgesuite&#46;net",
)

#: A wall page is always small; a real state CMS page never is. The size gate is
#: not belt-and-braces, it is the thing that keeps a marker from firing on a real
#: page that merely MENTIONS the vendor -- Cloudflare and Imperva both inject a
#: sensor script into the pages they protect, so `_Incapsula_Resource` appears in
#: the genuine 135,761-byte www.nvsos.gov page too, at offset 135,648 --
#: injected just before its </body>.
#:
#: Measured 2026-09-08, every wall seen in this project against every real page
#: fetched today:
#:   walls  212 b (Imperva stub) .. 5,950 b (Cloudflare "Just a moment")
#:   pages  135,760 b (nvsos.gov) .. 2,960,857 b (sos.nh.gov)
#: 32 KiB sits 5x above the largest wall and 4x below the smallest real page.
#: A wall that ever grows past it is a MISSED block, which is exactly today's
#: behaviour and no worse; a real page under it would be a WRONG verdict, which
#: is why the gate is here at all.
WALL_MAX_BYTES = 32 * 1024


def looks_like_wall(body: bytes) -> bool:
    """True if these bytes are a bot wall rather than anything we asked for."""
    if not body or len(body) > WALL_MAX_BYTES:
        return False
    return any(marker in body for marker in WALL_MARKERS)


# curl_cffi impersonates a real browser's TLS/HTTP2 fingerprint. OPTIONAL on
# purpose: if it is not installed the module still works, and only the handful of
# hosts behind fingerprint rules stay unreachable. Declared in pyproject so CI
# has it; guarded so a stock checkout without it degrades rather than crashes.
try:  # pragma: no cover - import-time capability probe
    from curl_cffi import requests as _curl
except Exception:  # noqa: BLE001
    _curl = None

#: Browsers to impersonate, tried in order until one is not refused.
#:
#: A SINGLE profile is not enough, and the evidence is specific: from one machine
#: in one second, vote.sos.ri.gov answers 403 to "chrome", "chrome131" and "edge"
#: and 200 to "safari" and "firefox". A WAF rule can be keyed to a particular
#: fingerprint rather than to automation in general, so a state whose one profile
#: happens to be blocked reads as unreachable when it is not -- which is the same
#: mistake, one level down, that had Ohio and Arizona written off as blocked.
#:
#: Chrome first because it is the common case and usually settles it in one
#: request; the rest cost nothing unless the first is refused.
IMPERSONATE_PROFILES = ("chrome", "safari", "firefox")

#: Kept for callers that want the primary profile by name.
IMPERSONATE = IMPERSONATE_PROFILES[0]


def impersonated_headers(headers: dict[str, str]) -> dict[str, str]:
    """Our headers, minus the one that CONTRADICTS the fingerprint being sent.

    ⚠️ NEVER SEND `DEFAULT_HEADERS["User-Agent"]` ON AN IMPERSONATED REQUEST.
    `impersonate=<profile>` makes curl_cffi present that browser's TLS/HTTP2
    fingerprint AND its own matching User-Agent. Overriding the UA with ours
    leaves a request whose handshake says one Chrome and whose header says
    another, which is a cleaner automation signal than either half alone --
    an ordinary browser cannot produce it.

    Verified 2026-09-08 against www.nvsos.gov (Akamai Bot Manager), one URL,
    `/sos/elections/voters/election-turnout-statistics`, `impersonate="chrome"`
    throughout, four requests spaced ten seconds apart:

        headers={}                                   -> 200, 135,760 b, real page
        headers={"Accept": "*/*"}                    -> 200, 135,760 b, real page
        headers={"From": "contact@electindex.com"}   -> 200, 135,761 b, real page
        headers={"User-Agent": DEFAULT UA}           -> 403,     640 b, AkamaiGHost
                                                                "Access Denied"

    So it is the User-Agent alone, not `Accept: */*` and not the courtesy `From`.
    Dropping that one header is what turns Nevada from "behind an Imperva wall,
    unreachable" into a state that answers -- see nv.py's docstring, which was
    written when this contradiction was still being sent.

    Everything else is kept: an adapter that passes `Accept: text/csv` or an API
    key still gets it. Only the User-Agent goes.

    ⚠️ AND IT GOES EVEN WHEN AN ADAPTER SET IT DELIBERATELY. tx.py overrides the
    UA because the Texas SoS's WAF 403s any User-Agent carrying the word "bot",
    and it would be easy to read that as a choice to be honoured here. It is not:
    every UA override in this codebase exists to look more like a browser, and on
    an impersonated request curl_cffi's own UA does that strictly better, because
    it is the one the handshake already claims. Preserving tx.py's string on the
    retry would hand Texas the identical mismatch that Nevada 403s -- the bug
    fixed here, re-created one adapter over.
    """
    return {name: value for name, value in headers.items()
            if name.lower() != "user-agent"}


def _impersonated_get(url, *, timeout, headers, params,
                      min_interval=None):
    """Retry a refusal with a real browser's TLS fingerprint.

    Some state hosts (ohiosos.gov, azsos.gov) sit behind a Cloudflare rule that
    scores the JA3 of any Python HTTP client as automation and answers 403 --
    including their own homepages and archived files, and regardless of headers.
    Restoring TLS session tickets (see _TicketAdapter) was enough for Colorado;
    these two need the full fingerprint.

    This matters for correctness, not just reach: a 403 raises SourceError and
    falls through to a weaker tier, while what is actually behind the wall is
    very often an honest 404 that should raise Missing and STOP the ladder. Ohio
    and Arizona were both being read as "unreachable" when the truth was "not
    posted yet".

    A profile is "refused" if it answers 403 OR hands back a wall page under any
    status at all -- see `looks_like_wall`. Imperva serves its interstitial with
    HTTP 200, so a status-only test walks away from the first profile holding a
    block page and calls it the answer.

    ⚠️ AND THIS LOOP IS THROTTLED, which it was not when only a 403 could reach
    it. A 403 is rare; a wall served under 200 is what a walled host returns to
    EVERY request, so widening the trigger turned "one extra request now and
    then" into "three unspaced requests, every time, against precisely the hosts
    that ban" -- the failure mode the Louisiana ban taught (see
    DEFAULT_MIN_INTERVAL). `_throttle` is keyed on host and `get()` has just hit
    this one, so each profile now waits its turn like any other request.
    """
    if _curl is None:
        return None
    if min_interval is None:
        min_interval = DEFAULT_MIN_INTERVAL
    headers = impersonated_headers(headers)
    last = None
    for profile in IMPERSONATE_PROFILES:
        _throttle(url, min_interval)
        try:
            response = _curl.get(
                url, timeout=timeout, headers=headers, params=params,
                impersonate=profile,
            )
        except Exception as exc:  # noqa: BLE001 - a failed profile is just no retry
            log.debug("impersonated retry (%s) failed for %s: %s", profile, url, exc)
            continue
        # Any answer that is not another refusal is the answer -- including a
        # 404, which is the whole point: it means we finally got to ask.
        if response.status_code != 403 and not looks_like_wall(response.content):
            if profile != IMPERSONATE_PROFILES[0]:
                log.debug("%s answered %s to the %s fingerprint",
                          url, response.status_code, profile)
            return response
        last = response
    return last


#: Minimum seconds between requests to the SAME host.
#:
#: Learned from a real ban, not from caution. Rebuilding Louisiana's 2024 series
#: means 1,026 PDFs from one host; run flat out at about 7 requests a second, it
#: drew a HOST-WIDE 403 on every URL -- to requests and to every browser
#: fingerprint alike -- that took roughly half an hour to clear. A ban like that
#: is indistinguishable from a bot wall while it lasts, so it does not just cost
#: one state's data, it produces a wrong verdict about why the state is missing.
#:
#: 0.4s is deliberately gentle rather than tuned: this runs on a two-hourly
#: cron with a whole season to spare, so throughput is worth nothing and a ban is
#: expensive. An adapter that knows its host tolerates more can pass a smaller
#: `min_interval`. Note this governs pacing WITHIN a run -- the gap BETWEEN runs
#: is hours either way, so tightening the cron does not touch this number.
DEFAULT_MIN_INTERVAL = 0.4

_last_request: dict[str, float] = {}
_throttle_lock = threading.Lock()


def _throttle(url: str, min_interval: float) -> None:
    """Space requests to one host without slowing requests to others.

    Keyed on host, so a run that walks fifty states is never delayed by its own
    breadth -- only by returning to the same server too soon.
    """
    if min_interval <= 0:
        return
    host = urlsplit(url).netloc.lower()
    if not host:
        return
    with _throttle_lock:
        previous = _last_request.get(host)
        now = time.monotonic()
        if previous is not None:
            wait = min_interval - (now - previous)
            if wait > 0:
                time.sleep(wait)
                now = time.monotonic()
        _last_request[host] = now


class Missing(SourceError):
    """The URL returned 404/410. The caller decides what that means."""


def cache_path(state: str, filename: str) -> Path:
    path = CACHE_DIR / state.lower() / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def get(
    url: str,
    *,
    state: str,
    filename: str,
    timeout: int = DEFAULT_TIMEOUT,
    headers: dict[str, str] | None = None,
    params: dict | None = None,
    use_cache: bool = False,
    min_bytes: int = 64,
    min_interval: float = DEFAULT_MIN_INTERVAL,
) -> bytes:
    """Download `url`, mirror it into cache/, and return its bytes.

    `use_cache=True` serves an existing cached copy without a request. Only pass
    it for an ARCHIVED, dated file whose contents can never change -- never for
    today's snapshot, which is the whole point of running daily.
    """
    path = cache_path(state, filename)
    if use_cache and path.exists() and path.stat().st_size >= min_bytes:
        cached = path.read_bytes()
        # ⚠️ A WALL CACHED BEFORE THIS CHECK EXISTED IS PERMANENT OTHERWISE.
        # `use_cache=True` is only ever passed for an archived file that can
        # never change, so nothing would re-fetch it: az.py's `fetch_history`
        # reads `2024-election-info.html` from disk, and a 5,845-byte Cloudflare
        # challenge saved there clears the 2,048-byte floor and would be handed
        # to the parser as Arizona's page for as long as the file sat there. The
        # download path can no longer write one; this is for the ones already on
        # disk from before it could not.
        if looks_like_wall(cached):
            log.warning("%s: cached %s is a bot wall, re-fetching", state, path)
        else:
            log.debug("%s: cache hit %s", state, path)
            return cached

    _throttle(url, min_interval)
    try:
        response = SESSION.get(
            url,
            timeout=timeout,
            headers={**DEFAULT_HEADERS, **(headers or {})},
            params=params,
        )
    except requests.RequestException as exc:
        raise SourceError(f"{state}: GET {url} failed: {exc}") from exc

    # A 403 is very often a fingerprint rule rather than a real refusal, and the
    # honest answer behind it is frequently a 404. Retry once with a browser
    # fingerprint before concluding anything.
    #
    # ...AND A WALL SERVED UNDER 200 IS THE SAME REFUSAL WITH A DIFFERENT STATUS
    # LINE. Imperva answers www.nvsos.gov with HTTP 200 and a 212-byte stub, so a
    # status-only trigger never retried Nevada at all and the whole state read as
    # "the SoS publishes an unparseable page". Verified 2026-09-08: the same URL
    # under the impersonated fingerprint returns the real 135,760-byte page.
    if response.status_code == 403 or looks_like_wall(response.content):
        retried = _impersonated_get(
            url, timeout=timeout,
            headers={**DEFAULT_HEADERS, **(headers or {})}, params=params,
            min_interval=min_interval,
        )
        if retried is not None:
            log.debug("%s: %s answered %s to the impersonated retry",
                      state, url, retried.status_code)
            response = retried

    if response.status_code in (404, 410):
        raise Missing(f"{state}: {url} returned {response.status_code}")
    if not response.ok:
        raise SourceError(f"{state}: {url} returned HTTP {response.status_code}")

    body = response.content
    # ⚠️ BEFORE the size floor, and the order is the whole point. Every wall page
    # is small, so `min_bytes` would fire first and raise Missing -- which the
    # caller turns into NotYetPublished and the ladder STOPS on, leaving the
    # state blank with "not published yet" in the log for a file that is sitting
    # right there behind the block. A wall is SourceError: fall through, loudly.
    if looks_like_wall(body):
        raise SourceError(
            f"{state}: {url} answered with a bot wall ({len(body)} bytes), "
            f"not the file -- we were refused, not answered"
        )
    # A soft-404 -- a state CMS serving a "page not found" shell with status 200 --
    # is indistinguishable from a real file by status alone, so size is the check.
    if len(body) < min_bytes:
        raise Missing(f"{state}: {url} returned only {len(body)} bytes")

    path.write_bytes(body)
    return body


def looks_like_html(body: bytes) -> bool:
    """True if these bytes are a web page rather than the data file we asked for.

    State CMSes answer a missing report with a styled 200 "not found" page far
    more often than with a 404, so every adapter that expects CSV or XLSX checks
    this before parsing and treats a hit as "not posted yet".
    """
    head = body[:1024].lstrip().lower()
    return head.startswith(b"<!doctype html") or head.startswith(b"<html") or b"<head" in head[:200]


def looks_like_xlsx(body: bytes) -> bool:
    """XLSX is a zip; every real one starts with the PK local-file-header magic."""
    return body[:2] == b"PK"
