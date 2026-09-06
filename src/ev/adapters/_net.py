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


def _impersonated_get(url, *, timeout, headers, params):
    """Retry a 403 with a real browser's TLS fingerprint.

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
    """
    if _curl is None:
        return None
    last = None
    for profile in IMPERSONATE_PROFILES:
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
        if response.status_code != 403:
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
#: 0.4s is deliberately gentle rather than tuned: this runs every six hours with
#: a whole season to spare, so throughput is worth nothing and a ban is
#: expensive. An adapter that knows its host tolerates more can pass a smaller
#: `min_interval`.
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
        log.debug("%s: cache hit %s", state, path)
        return path.read_bytes()

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
    if response.status_code == 403:
        retried = _impersonated_get(
            url, timeout=timeout,
            headers={**DEFAULT_HEADERS, **(headers or {})}, params=params,
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
