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
from pathlib import Path

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

    try:
        response = SESSION.get(
            url,
            timeout=timeout,
            headers={**DEFAULT_HEADERS, **(headers or {})},
            params=params,
        )
    except requests.RequestException as exc:
        raise SourceError(f"{state}: GET {url} failed: {exc}") from exc

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
