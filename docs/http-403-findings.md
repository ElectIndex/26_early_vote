# The four HTTP 403s — blocked, or just not published yet?

Investigated 2026-09-06. Every status code below was observed live, from this
machine, on that date. Where two clients disagree, both numbers are recorded.

**Headline: only two of the four hosts block us. The other two 403s are an
artefact of our own HTTP client, and behind them sit ordinary 404s — meaning CO
and MI should be raising `NotYetPublished` (STOP the ladder), not `SourceError`
(fall through). Today they fall through every run.**

---

## Results table

| Host | plain curl | curl + `_net.py` UA | curl + full browser headers | `requests` (what we actually ship) | archived file (2022/2024) | Verdict | Correct exception |
|---|---|---|---|---|---|---|---|
| **azsos.gov** (AZ) | **403** | **403** | **403** | **403** | **403** — 2024 page blocked identically | **Genuinely blocked.** Cloudflare *managed challenge*, host-wide | `SourceError` — fall through to aggregator ✅ already correct |
| **www.coloradosos.gov** (CO) | **404** (2026) / **200** (archived) | 404 / 200 | 404 / 200 | **403 on everything, incl. the root** | **200, 64 181 b real XLSX** | **Not blocked at all.** Our `requests` TLS fingerprint is what gets challenged | `NotYetPublished` ❌ currently `SourceError` |
| **www.michigan.gov** (MI) | **403** | **403** | **200** (page) / **404** (xlsx) | 403 with our UA, **404 with a clean UA** | n/a (no dated archive); `/sos` returns **200** | **Not blocked.** A pure User-Agent string rule | `NotYetPublished` ❌ currently `SourceError` |
| **www.ohiosos.gov** (OH) | **403** | **403** | **403** | **403** | **403** — 2022 *and* 2024 workbooks blocked identically | **Genuinely blocked.** Cloudflare *managed challenge*, host-wide | `SourceError` — fall through to aggregator ✅ already correct |

---

## Evidence, host by host

### AZ — azsos.gov: genuinely blocked (Cloudflare managed challenge)

Every combination returns 403, including the site root and the **archived 2024
page that the adapter's own fixture was built from**:

```
                              plain    _net UA   full hdrs   clean Chrome UA
https://azsos.gov/             403       403        403           403
.../2026-election-info         403       403        403           403
.../2024-election-info         403       403        403           403      <- archived, known-good
.../1999-election-info         403       403        403           403      <- bogus slug, same 403
```

Response headers name the mechanism outright:

```
HTTP/2 403
server: cloudflare
cf-mitigated: challenge
server-timing: chlray;desc="a36a980478aec102"
critical-ch: Sec-CH-UA-Bitness, Sec-CH-UA-Arch, ...
content-security-policy: ... script-src ... https://challenges.cloudflare.com ...
```

Body is `<title>Just a moment...</title>` with `/cdn-cgi/challenge-platform/`
JS. A *managed challenge* is not a header check — it requires executing
Cloudflare's JS and presenting a browser TLS/HTTP2 fingerprint. **No header
combination can pass it, and a datacentre IP makes the bot score worse, not
better.** `curl` with a genuine Chrome UA over HTTP/2 fails too, which rules out
"it's just the UA".

Note also that a bogus slug 403s the same as a real one — so on this host we
cannot even distinguish "page missing" from "blocked". `SourceError` is the only
honest reading.

> **Dead code worth knowing about:** `az.py:242` calls `looks_like_challenge(body)`
> to convert a Cloudflare interstitial into a `SourceError`. That branch can
> never run: the challenge arrives as **403**, and `_net.get` raises
> `SourceError` at line 94 before any body is returned to the adapter. The
> outcome is right by accident; the intended path is unreachable.

### OH — www.ohiosos.gov: genuinely blocked (Cloudflare managed challenge)

Identical picture, including both archived workbooks the parser was built and
fixtured against:

```
                                                   plain   _net UA   full hdrs   clean UA
https://www.ohiosos.gov/                            403      403       403        403
.../2026/gen/absentee/2026gen_absentee_report_web.xlsx   403  403      403        403
.../2024/gen/absentee/2024gen_absentee_report_web.xlsx   403  403      403        403   <- archived, known-good
.../2022/gen/absentee/2022gen_absentee_report_web.xlsx   403  403      403        403   <- archived, known-good
.../2024/gen/absentee/nope_does_not_exist.xlsx           403  403      403        403   <- bogus, same 403
```

```
HTTP/2 403
server: cloudflare
cf-mitigated: challenge
server-timing: chlray;desc="a36a981d3fb88c00"
content-length: 1285703
```

The body is a 1.25 MB *custom* Cloudflare challenge page titled
`Ohio Secretary of State's Office Website Maintenance`, carrying the same
`/cdn-cgi/challenge-platform/` payload. Same conclusion as AZ: unreachable by any
headless HTTP client, from any header set, and worse from a datacentre IP.

### CO — www.coloradosos.gov: not blocked; **our own TLS fingerprint is the problem**

This is the most valuable finding in the investigation. From the *same machine,
same IP, same second*:

```
curl (any UA, incl. "python-requests/2.32.3", HTTP/1.1 or HTTP/2):
  .../2024/20241031ElectionActivity.xlsx   -> 200   64 181 b  application/vnd...spreadsheetml.sheet
  .../2022/20221101ElectionActivity.xlsx   -> 200   44 133 b  XLSX
  .../2026/20260905ElectionActivity.xlsx   -> 404   25 960 b  text/html
  .../2026/20260906ElectionActivity.xlsx   -> 404
  .../2024/20241001ElectionActivity.xlsx   -> 404              <- a day CO never posted; also 404
  https://www.coloradosos.gov/             -> 200

python `requests` 2.32.5 / urllib3 2.6.2 (every UA, incl. ours and a clean Chrome one):
  ALL of the above                          -> 403   4 837 b  server: cloudflare

python stdlib `urllib.request`, same interpreter, same OpenSSL 3.0.18:
  .../2024/20241031ElectionActivity.xlsx   -> 200   64 181 b
```

So it is not the IP, not the UA, not the headers, and not HTTP/1.1-vs-HTTP/2.
It is **urllib3's TLS context**. Bisected to a single bit:

```
urllib3 default context (baseline)   -> 403   4 837 b
urllib3 context minus OP_NO_TICKET   -> 200  64 181 b   <-- the whole difference
urllib3 context minus OP_NO_COMPRESSION -> 403
ssl.create_default_context()         -> 200  64 181 b

urllib3 opts: 2186428496 | stdlib opts: 2186412112 | diff bits: 16384 (= ssl.OP_NO_TICKET)
verify_mode/check_hostname identical in both (CERT_REQUIRED / True)
```

`urllib3` sets `OP_NO_TICKET`, which drops TLS extension 35 (`session_ticket`)
from the ClientHello. Browsers and curl send it; the resulting JA3 is unusual
enough that Cloudflare's managed ruleset scores it as automation and 403s.
Re-enabling session tickets makes the handshake look like the stdlib's and the
403 disappears. **Certificate verification is untouched** — verified above.

With the transport fixed, the 2026 file is a plain **404**, exactly like a 2024
date Colorado skipped. Colorado posts only on days it issues a return release,
which the adapter's `Missing -> None -> NotYetPublished` path already handles
correctly. **CO is not blocked and never was — it simply has no 2026 file yet.**

End-to-end confirmation that the parser is fine and only transport was broken:

```
CO archived 2024-10-31 fetched live and run through ev.adapters.co.parse:
  1 state row, 64 county rows, statewide ballots_total = 1,731,171
```

### MI — www.michigan.gov: not blocked; a **User-Agent string rule**

`curl` and `requests` agree here, so it is a header rule, not a fingerprint:

```
                                plain   _net UA   full browser hdrs   clean Chrome UA + Accept:*/*
https://www.michigan.gov/sos     403      403          200                  200      (391 453 b)
.../05mcdaniel/General_Election_Data_by_Jurisdiction.xlsx
                                 403      403          404                  404      (353 387 b HTML)
.../Elections/General-Election-Data-by-Jurisdiction.xlsx
                                 403      403          404                  404
.../05mcdaniel/Nope_Does_Not_Exist.xlsx
                                 403      403          404                  404
```

Bisecting the UA against `https://www.michigan.gov/sos` (everything else held
constant — same `Accept: */*`, no other headers):

```
python-requests/2.32.3                                            -> 403
curl/8.7.1                                                        -> 403
Mozilla/5.0 (...) Chrome/125.0 Safari/537.36 (+https://electindex.com/early-vote/ bot)  -> 403
Mozilla/5.0 (...) Chrome/125.0 Safari/537.36 (+https://electindex.com/early-vote/)      -> 403
Mozilla/5.0 (...) Chrome/125.0 Safari/537.36 bot                  -> 403
Mozilla/5.0 (...) Chrome/125.0 Safari/537.36                      -> 200
Mozilla/5.0 (...) Chrome/125.0.0.0 Safari/537.36                  -> 200
```

The trigger is **any token appended after `Safari/537.36`** — not the word
"bot". Dropping "bot" but keeping `(+https://electindex.com/early-vote/)` still
403s. The WAF appears to match a browser UA anchored at end-of-string.

Good news for politeness: the attribution can move out of the UA and survive.
Both of these return **200** alongside a clean UA:

```
clean UA + `X-Contact: https://electindex.com/early-vote/`  -> 200
clean UA + `From: <mailbox>`                                -> 200
```

Once reachable, both candidate workbook URLs return **404** — Michigan's
absentee-by-jurisdiction workbook is not posted off-season. That is
`NotYetPublished`, and `mi.py:_load` already produces exactly that from a
`Missing`. **MI is not blocked either.**

> **Separate follow-up (not a 403 issue):** with a working UA, the live
> `/sos/elections/election-results-and-data` page (200, 419 KB, 100 file links)
> publishes its media under
> `/sos/-/media/Project/Websites/sos/Election-Results-and-Statistics/...`.
> Neither `05mcdaniel/` nor `Elections/` — the two paths in `URL_CANDIDATES` —
> appears anywhere on it, and no `.xlsx` link is listed at all today. The 404 is
> consistent with "off-season", but the URL may also have rotted since 2024. It
> should be rediscovered from the SoS page when the AV period opens, rather than
> trusted to still be there.

---

## Recommended change to `src/ev/adapters/_net.py`

Two independent fixes, one per non-blocked host. Neither affects AZ or OH.

```diff
--- a/src/ev/adapters/_net.py
+++ b/src/ev/adapters/_net.py
@@
 from __future__ import annotations
 
 import logging
+import ssl
 from pathlib import Path
 
 import requests
+from requests.adapters import HTTPAdapter
+from urllib3.util.ssl_ import create_urllib3_context
 
 from .base import SourceError
 
@@
-#: A handful of state election sites (OH and TX in particular) reject the default
-#: python-requests User-Agent outright, so we send a real one.
+#: A handful of state election sites (TX in particular) reject the default
+#: python-requests User-Agent outright, so we send a real one -- and ONLY a real
+#: one. michigan.gov's WAF 403s any User-Agent with a token appended after
+#: "Safari/537.36": verified 2026-09-06 that "... Safari/537.36" returns 200
+#: while both "... Safari/537.36 (+https://electindex.com/early-vote/ bot)" and
+#: "... Safari/537.36 (+https://electindex.com/early-vote/)" return 403. The
+#: courtesy self-identification therefore lives in `From`, which michigan.gov
+#: does not inspect (also verified: 200 with it present).
 DEFAULT_HEADERS = {
     "User-Agent": (
         "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
-        "(KHTML, like Gecko) Chrome/125.0 Safari/537.36 "
-        "(+https://electindex.com/early-vote/ bot)"
+        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
     ),
     "Accept": "*/*",
+    "From": "earlyvote@electindex.com",   # <- use a mailbox you actually read
 }
 
 
+class _TicketAdapter(HTTPAdapter):
+    """Restore TLS session tickets in the ClientHello.
+
+    urllib3 sets OP_NO_TICKET unconditionally, which drops TLS extension 35.
+    Cloudflare's managed ruleset scores the resulting JA3 as automation and 403s
+    coloradosos.gov -- including its own homepage, and including files we know
+    exist. Verified 2026-09-06: with OP_NO_TICKET set, every coloradosos.gov URL
+    returns 403; with it cleared, the archived 2024-10-31 workbook returns 200
+    (64,181 bytes) and the unposted 2026 file returns an honest 404. curl and
+    stdlib urllib -- which both send the extension -- were never blocked.
+
+    Certificate verification is unchanged: verify_mode CERT_REQUIRED,
+    check_hostname True, same as urllib3's default.
+    """
+
+    def init_poolmanager(self, *args, **kwargs):
+        ctx = create_urllib3_context()
+        ctx.options &= ~ssl.OP_NO_TICKET
+        kwargs["ssl_context"] = ctx
+        return super().init_poolmanager(*args, **kwargs)
+
+
+#: Module-level so connections are reused across a run. The ingest walk is
+#: sequential; if that ever changes, give each worker its own session.
+SESSION = requests.Session()
+SESSION.mount("https://", _TicketAdapter())
+
+
 class Missing(SourceError):
     """The URL returned 404/410. The caller decides what that means."""
@@
     try:
-        response = requests.get(
+        response = SESSION.get(
             url,
             timeout=timeout,
             headers={**DEFAULT_HEADERS, **(headers or {})},
             params=params,
         )
```

**What it fixes:** `coloradosos.gov` and `www.michigan.gov` — both completely.
**What it does not fix:** `azsos.gov` and `www.ohiosos.gov`. Nothing in an HTTP
client can fix those.

### Verified end-to-end

Simulating exactly the diff above at runtime (patched `_net.DEFAULT_HEADERS` and
routed `_net`'s requests through the ticket-enabled session), then calling each
tier-1 adapter's real `fetch(2026, 2026-09-06)`:

```
AZ az-sos: SourceError    -> AZ: .../2026-election-info returned HTTP 403
CO co-sos: NotYetPublished-> CO: no election activity workbook posted for 2026-09-06
MI mi-sos: NotYetPublished-> MI: no absentee workbook posted for 2026 yet (... returned 404 ...)
OH oh-sos: SourceError    -> OH: .../2026gen_absentee_report_web.xlsx returned HTTP 403
```

Against today's actual `python -m ev probe`, where all four are `SourceError`.

**Residual risk to check on the first CI run:** the CO fix was proven from a
residential IP. Cloudflare combines fingerprint score with IP reputation, so a
GitHub Actions datacentre IP could still be challenged. `urllib3` sets
`OP_NO_TICKET` on every platform, so the fingerprint half of the fix carries over
to a Linux runner unchanged; only the IP half is unverified. If CO still 403s in
Actions, the failure degrades safely to `SourceError` → aggregator, and the
verdict for CO moves into the AZ/OH row. Worth confirming explicitly rather than
assuming.

---

## Which exception each host should raise, and why it matters

| State | Today | Should be | Consequence of the current behaviour |
|---|---|---|---|
| **AZ** | `SourceError` | **`SourceError`** ✅ | Correct. Blocked host → fall through to the aggregator is the only route to Arizona. |
| **OH** | `SourceError` | **`SourceError`** ✅ | Correct, same reason. |
| **CO** | `SourceError` | **`NotYetPublished`** ❌ | Ladder falls through instead of stopping. Colorado posts only on release days, so once the season opens **every non-release day will fall through to the aggregator rather than stopping** — precisely the "a weaker source invents a number" case rule 1 exists to prevent. Plus a daily `WARNING` for the ~two months until Colorado's first file. |
| **MI** | `SourceError` | **`NotYetPublished`** ❌ | Same: daily warning spam now, and a wrong-tier fall-through later. |

Neither `co.py` nor `mi.py` needs an adapter change — both already map
`Missing` → `NotYetPublished` correctly. They have simply never been able to
*see* the 404, because the 403 fired first. Fixing `_net.py` fixes both.

### Making the AZ/OH block legible in `ev_status.json` (optional, second-order)

`ev_status.json` currently records `"detail": "AZ: ... returned HTTP 403"`, which
reads like a transient fault someone should retry. It is not: it is permanent,
and it means **Arizona and Ohio can only ever be reached via the aggregator tier
or a manual entry.** Whoever reads the status file should be able to see that
without re-running this investigation.

A minimal way to say so, if you want it — note it **must** subclass
`SourceError` so the ladder's fall-through is unchanged:

```python
class Blocked(SourceError):
    """The host's bot protection refused us. Not retryable, not absence.

    A Cloudflare managed challenge (403 + `cf-mitigated: challenge`) cannot be
    passed by any header set or from any datacentre IP -- see
    docs/http-403-findings.md. The state is reachable only via a lower tier.
    """

# in get(), before the generic `not response.ok` branch:
if response.status_code in (403, 503) and response.headers.get("cf-mitigated"):
    raise Blocked(
        f"{state}: {url} is behind Cloudflare bot protection "
        f"(HTTP {response.status_code}); reachable only via a lower tier"
    )
```

Both AZ and OH send `cf-mitigated: challenge`, so this catches exactly them and
nothing else. It changes no ladder behaviour — only what the status file says.
