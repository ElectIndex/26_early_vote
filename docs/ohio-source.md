# Ohio: where the absentee data actually is

Investigated 2026-09-06, as a follow-up to `docs/http-403-findings.md`, which
concluded that `www.ohiosos.gov` was "genuinely blocked … unreachable by any
headless HTTP client". **That conclusion was wrong in an important way.** Ohio is
reachable headlessly, by two independent routes, and one of them needs no new
dependency at all.

Every status code below was observed live, from this machine, on that date.

---

## Headline

| | |
|---|---|
| **Is there a headless path?** | **Yes — two.** |
| Works on a stock runner today | The SoS's **Power BI absentee dashboard** query API. Not behind Cloudflare; answers a plain `requests` POST. Daily, county-level, mail-vs-early-in-person. |
| Works with one added dependency | The SoS's **`publicfiles.ohiosos.gov` blob store**, reached with a browser TLS fingerprint (`curl_cffi`). Carries the certified workbook for every election back to 2005 — including the 2022 and 2024 generals this repo backfills. |
| Coverage | **All 88 counties on both routes.** Nothing here is partial, so no partial-coverage labelling is needed. |
| Party fields | `None` everywhere, on every route. Ohio does not register voters by party. |
| Ohio's status today | `pending` (NotYetPublished), not `SourceError`. It was `SourceError` before this change, falling through to the aggregator every run. |

---

## Part 1 — the block is a TLS fingerprint, not a wall

`docs/http-403-findings.md` established that the Colorado 403 was urllib3's
`OP_NO_TICKET` dropping TLS extension 35, and fixed it. Ohio survived that fix.
It does not survive a **full** browser fingerprint.

Same machine, same IP, same minute, `https://www.ohiosos.gov/`:

| client | result |
|---|---|
| `requests` via `_net.SESSION` (ticket fix included) | **403**, 1 285 356 b, `cf-mitigated: challenge` |
| stdlib `urllib.request` | **403**, 1 285 548 b |
| `curl/8.7.1`, real Chrome UA, HTTP/2 | **403** |
| `curl --http1.1`, real Chrome UA | **403** |
| **`curl_cffi`, `impersonate="chrome"`** | **200**, 217 784 b, real HTML |

`curl_cffi` presents Chrome's actual JA3/JA4 and HTTP/2 SETTINGS. That is the
whole difference — it is the same class of fix as Colorado's, one step further.
The mitigation header confirms the mechanism:

```
HTTP/2 403
server: cloudflare
cf-mitigated: challenge
server-timing: chlray;desc="a36aa9d55b2badd7"
content-length: 1285356          <- the "Website Maintenance" interstitial
```

The whole zone is proxied through the same Cloudflare config (all on
`104.16.134.50` / `104.16.135.50`), and every host in it behaves identically:

| host | plain `requests` | `curl_cffi` chrome |
|---|---|---|
| `https://www.ohiosos.gov/` | 403 | **200** (217 784 b) |
| `https://ohiosos.gov/` | 403 | — (301 → www) |
| `https://data.ohiosos.gov/` | 403 | 301 → `www.ohiosos.gov/data` |
| `https://data.ohiosos.gov/portal/election-dashboards` | — | **200** (SPA shell) |
| `https://www6.ohiosos.gov/` | 403 | — |
| `https://voterlookup.ohiosos.gov/` | 403 | — |
| `https://publicfiles.ohiosos.gov/election-results/files-index.json` | 403 | **200** (363 585 b JSON) |
| `https://www.boe.ohio.gov/` (all 88 county BOE sites) | 403 | — |
| `https://liveresults.ohiosos.gov/` | **200** (186 979 b) | — |

Hosts that do **not** resolve at all (connection failure, exit code 000):
`sos.state.oh.us`, `www.sos.state.oh.us`, `results.ohiosos.gov`,
`electionresults.ohiosos.gov`.

`https://www.ohiosos.gov/robots.txt` (200, 173 b) is `Allow: /` for every agent
and advertises the sitemap. The Cloudflare rule contradicts the site's own stated
crawl policy; this is a WAF setting, not a publishing decision.

> **Also affects Arizona.** `https://azsos.gov/` returns **403** to `requests`
> and **200** (240 694 b) to `curl_cffi` chrome; the guessed 2026 slug
> `https://azsos.gov/elections/results-data/2026-election-info` returns an honest
> **404** (244 889 b) rather than a 403. So `docs/http-403-findings.md`'s "AZ is
> genuinely blocked" is also wrong, and AZ's `SourceError` is masking a 404 that
> should be read as `NotYetPublished`. **`src/ev/adapters/az.py` is not this
> module's to edit — flagging it for its owner.**

### The dependency question

`curl_cffi` is **not** in `pyproject.toml`, and `pyproject.toml` is not this
module's to edit. `oh.py` therefore imports it optionally: when it is present the
workbook routes work, and when it is absent they degrade to `SourceError` and the
Power BI route — which needs nothing — carries Ohio alone. Verified both ways
(see "Verified end to end" below).

**Recommendation for whoever owns `_net.py` / `pyproject.toml`:** add
`curl_cffi>=0.7` and give `_net.get` a browser-fingerprint transport. It would
unlock Ohio's workbook routes and, on the evidence above, Arizona's entire site.

---

## Part 2 — the SoS site was rebuilt, and the adapter's URL was already dead

Independently of the 403, the URL `oh.py` was pointed at no longer exists. The
SoS site is now a Next.js app backed by Contentful
(`images.ctfassets.net/q3i90m1u8zsl/…`), with assets at `/assets/<slug>` and
pages under `/[locale]/[...slug]`. The old EPiServer `globalassets/` tree is gone:

```
https://www.ohiosos.gov/globalassets/elections/2026/gen/absentee/2026gen_absentee_report_web.xlsx
    -> 404  (curl_cffi; 403 to everything else, which is why it read as "blocked")
https://www.ohiosos.gov/globalassets/elections/2024/gen/official/absentee_report_web.xlsx
    -> 200 but 187 340 b of text/html: a 301 to https://www.ohiosos.gov/data
```

The Wayback CDX index confirms the `{cycle}gen_absentee_report_web.xlsx` filename
in `FILENAMES` was never a real URL. What Ohio actually published, per
`http://web.archive.org/cdx/search/cdx?url=www.ohiosos.gov/globalassets/elections/*`:

```
20241007164550  .../2022/gen/absentee_report_web.xlsx           200  869 248 b
20250307064602  .../2024/gen/official/absentee_report_web.xlsx  200  871 185 b
```

Both fetch and parse today through Wayback's `id_` raw form (200, 1 232 782 b and
1 235 718 b), producing 1 statewide + 88 county rows with
`ballots_total = 1 473 983` and `2 620 750` — the same numbers as the repo's
fixtures. So Wayback is a working fallback for the 2022/2024 backfill, and is
recorded here as such. It is **not** wired in, because Part 3 gives the same
files from the SoS's own live host, which is better in every way.

Note the naming inconsistency this exposes: the folder is `2022/gen/` for one
cycle and `2024/gen/official/` for the next. Guessing Ohio's paths does not work.
**The adapter now reads an index instead.**

---

## Part 3 — route 2: `publicfiles.ohiosos.gov`, the SoS's own blob store

`https://www.ohiosos.gov/data` (200, 187 340 b, "Ohio Secretary of State Data
Portal") links to `https://data.ohiosos.gov/portal/*`, a React SPA. Its bundle,
`https://data.ohiosos.gov/portal/static/js/main.97d9abb4.js` (200, 341 731 b),
contains its environment map:

```js
const qt = {
  dev:  "https://publicfilesdev.ohiosos.gov/election-results",
  uat:  "https://publicfilesuat.ohiosos.gov/election-results",
  prod: "https://publicfiles.ohiosos.gov/election-results"
}, Gt = "prod";
… fetch("https://publicfiles.ohiosos.gov/election-results/files-index.json")
```

That index is **200, 363 585 b of JSON**, and it is a complete, machine-readable
catalogue of every election file the SoS publishes — 82 elections back to 2005,
51 of them with an absentee report — each with a `blobPath` into the same
container. Response headers show an Azure Blob origin (`x-ms-blob-type:
BlockBlob`, `x-ms-version: 2009-09-19`) fronted by Cloudflare; container listing
is disabled (`?restype=container&comp=list` → 404 `ResourceNotFound`), so the
index is the only way in — which is fine, because the index is what the SoS's own
page uses.

Verified downloads (all `curl_cffi`, all real XLSX starting `PK`):

```
200 1 232 782 b  .../past-elections/2022/General Election: November 8, 2022/absentee_report_web.xlsx
200 1 235 718 b  .../past-elections/2024/General Election: November 5, 2024/absentee_report_web.xlsx
200 1 236 299 b  .../past-elections/2026/Primary+Special Election - May 5, 2026/absentee_report_web.xlsx
200 1 235 367 b  .../past-elections/2024/General Election: November 5, 2024/provisional_report_web.xlsx
404       215 b  .../past-elections/2026/General Election: November 3, 2026/absentee_report_web.xlsx
404       215 b  .../current-election/absentee_report_web.xlsx
```

Two things to notice. First, the 2026 general 404s **cleanly** — an Azure XML
error, not a challenge page — so this route can tell "not published yet" from
"blocked", which is exactly the distinction the ladder turns on. Second, note
`General Election: November 8, 2022` against `Primary+Special Election - May 5,
2026`: colon in one, hyphen in the other. `find_workbook()` reads the index and
matches on the election `type` and the file `displayName`; it never builds a path.

One 403 was observed mid-run on a file that had just succeeded, and the immediate
retry returned 200 — the challenge fires probabilistically even against a good
fingerprint, so `download()` retries once.

---

## Part 4 — route 3: the Power BI absentee dashboard (the live daily feed)

This is the important find. The same portal bundle carries eight
`app.powerbigov.us/view?r=<token>` embeds; two decode to the resource key behind
the **"Ohio Absentee and Early Voting Data Dashboard"**, which the portal
describes as the vehicle through which "Ohio can now provide detailed data
dashboards to help users track daily absentee and early voting trends beginning
with the November 5, 2024 General Election", noting that "each county board of
elections must populate and submit early voting and absentee data to the
Secretary of State's office pursuant to R.C. 3509.05(C)(4)(a)".

**The query API behind it is not behind Cloudflare and answers plain
`python-requests`.** That is what gives Ohio a headless path with no new
dependency.

How it was reached, and how to rediscover it if the SoS republishes the report:

1. `https://data.ohiosos.gov/portal/static/js/main.97d9abb4.js` → grep
   `app.powerbigov.us/view?r=`; base64-decode the token to
   `{"k":"<resourceKey>","t":"<tenantId>"}`. The one titled *Ohio Absentee and
   Early Voting Data Dashboard* is `77b7dd4e-7c38-4f79-9992-fdb5502cd2e9`
   (a second embed of the same report is `69e1004e-…`).
2. `GET https://app.powerbigov.us/view?r=<token>` (200, 29 101 b) → grep
   `ClusterUri` → `https://wabi-us-gov-iowa-redirect.analysis.usgovcloudapi.net/`.
   The `-redirect` host 403s every API call; the **`-api`** host is the one that
   works.
3. `GET https://wabi-us-gov-iowa-api.analysis.usgovcloudapi.net/public/reports/<key>/modelsAndExploration?preferReadOnlySession=true`
   with header `X-PowerBI-ResourceKey: <key>` → **200, 372 446 b**: dataset id
   `0e7f070f-deff-4aad-8dba-bb0efd13fc0c`, model id `826990`, report id
   `1307181`, and the visual definitions naming the table and columns.
4. `POST …/public/reports/querydata?synchronous=true`, same header → **200**.

Endpoints that do *not* work, recorded so nobody retries them:

```
wabi-us-gov-iowa-redirect.analysis.usgovcloudapi.net  /public/reports/<k>/modelsAndExploration  403
wabi-us-gov-iowa-redirect…                            /public/reports/querydata                 403
api.powerbigov.us                                     both of the above                         403
wabi-us-gov-iowa-api…                                 /public/reports/<k>/modelsAndExploration POST  405 (GET only)
```

### What the dataset holds

Table `absentee v_detailed_grouped`, with (among others) `Election_Description`,
`County_Name`, `REFRESH_DATE`, and four measures:

* `BALLOTS_SENT_INCL_EIP` / `BALLOTS_RECEIVED_INCL_EIP` — mail **plus** early
  in-person
* `BALLOTS_SENT_NO_EIP` / `BALLOTS_RECEIVED_NO_EIP` — mail only

so early in-person is the difference. Verified for the 2024 general that EIP sent
and EIP received are identical (2 333 383 − 985 715 = 2 262 963 − 915 295 =
1 347 668), which is what you would expect — an in-person ballot is issued and
cast in one act.

One request (40 KB, 808 rows) returns every election × every county:

```
2023 NOV GENERAL                  sent 818 211    received 774 576    88 counties
2024 JUNE CD 6 SPECIAL GENERAL      5 536           5 033             11 counties
2024 MAR PRIMARY                  329 344         312 120             88 counties
2024 NOV GENERAL                2 333 383       2 262 963             88 counties
2025 MAY PRIMARY/SPECIAL           75 679          67 753             88 counties
2025 NOV GENERAL                  218 792         205 200             88 counties
2026 MAY PRIMARY/SPECIAL          397 444         380 620             88 counties
```

**There is no 2026 NOV GENERAL row yet** — which is correct for 2026-09-06 and is
what makes today's `NotYetPublished` honest rather than a guess.

`Voter_Party_Bucketed` and `Age_Group` also exist in the table. The party column
is derived from which primary ballot a voter last took, which is not
registration; reading it would invent a fact about Ohio that does not exist, so
the adapter does not touch it. (`Age_Group` is left for later; UF's bands do not
map to ours and Ohio's have not been checked.)

### The one thing to be careful about

The dashboard and the workbook are **different measurements of the same
election** and will never match:

| 2024 general | workbook (certified) | dashboard (tracker) |
|---|---|---|
| ballots cast | 2 620 750 | 2 262 963 |
| mail returned | 1 084 496 | 915 295 |
| in person | 1 536 254 | 1 347 668 |

County submissions stop before the last in-person days and the late-arriving mail
are counted, so the tracker runs about 14 % light. Both are honest; the workbook
is the restatement. `oh.py` orders the routes so the workbook wins once it
exists, uses the workbook alone for backfill, and `test_oh.py` asserts the two
disagree so nobody later "fixes" one to match the other.

`REFRESH_DATE` is a per-record load stamp, not a daily cumulative series — the
2023 general's rows carry a 2026-05-08 stamp from a migration reload. The
adapter uses `max(REFRESH_DATE)` for the election it selected, which is the
snapshot's as-of date for a live election, and refuses anything dated after the
run's `as_of`.

---

## Part 5 — county boards of elections (checked, not needed)

The brief suggested a partial county-level source. It is not needed — both routes
above already cover all 88 counties — but here is what was found, because it
matters if the SoS routes ever close.

The SoS runs a unified platform for the county boards at **`boe.ohio.gov`**
(`https://www.summitcountyboe.gov/` 301s to `https://www.boe.ohio.gov/summit/`),
and it sits behind the **same** Cloudflare challenge: 403, 1 285 400 b,
`cf-mitigated: challenge`. So most of Ohio's 88 counties are behind the same wall
as the SoS, and a "big counties" partial would have been thin:

| county | host | status |
|---|---|---|
| Cuyahoga | `https://boe.cuyahogacounty.gov/` | **200** (62 108 b); `/maps-and-data` 200 — maps and polling places only, no absentee statistics |
| Franklin | `https://vote.franklincountyohio.gov/` | **200** (20 651 b); has `/maps-and-data/voter-and-absentee-data-files` (200, 173 716 b) — a request-driven ElectionVault portal, not a published file |
| Hamilton | `https://www.votehamiltoncountyohio.gov/` | **403**, `cf-mitigated: challenge` |
| Summit | `https://www.summitcountyboe.gov/` | **403** (via `boe.ohio.gov`) |
| Montgomery | `https://www.mcohio.org/departments/board_of_elections/index.php` | **404** |

Also checked: `https://data.ohio.gov/` (the state open-data portal) returns 200
but is a WebSphere portal, not CKAN — `https://data.ohio.gov/api/3/action/package_search?q=absentee`
is **404**. No election datasets surfaced.

---

## Part 6 — is it IP reputation?

Partly, and the evidence says a datacentre IP will not be the deciding factor.

The Wayback crawler's captures of `https://www.ohiosos.gov/` alternate between
200 and 403 through June and July 2026 (e.g. 200 on 2026-06-05, 403 on
2026-06-13, 200 on 2026-06-19, 403 on 2026-06-20 fourteen times in a row), and by
August are almost entirely 403. So the challenge is a score, not a blanket rule,
and archive.org's own IPs — well-known, non-residential — sometimes pass it. The
one intermittent 403 seen against `publicfiles` from this residential IP points
the same way.

That is weak evidence in both directions, so it is not something to rely on. What
matters is that the fingerprint half of the fix is IP-independent, and that the
Power BI host is not behind Cloudflare at all, so **the route Ohio actually
depends on in CI is not subject to this question**.

---

## What `oh.py` now does

Three routes, first answer wins, best measurement first:

1. **`globalassets`** — the legacy URL, kept first as instructed. Costs one
   request; currently 404s. If Ohio ever restores it, it is authoritative.
2. **`publicfiles`** — read `files-index.json`, find the cycle's November general,
   download its `blobPath`. Needs `curl_cffi`; without it, `SourceError`.
3. **Power BI dashboard** — one POST, decode the DSR, county rows plus a
   statewide sum. Needs nothing beyond `requests`.

Design points worth knowing:

* **The dashboard is the authority on absence.** It is the only route with data
  during the early-vote period, so `fetch` raises `NotYetPublished` (stop the
  ladder) only when the dashboard positively says the cycle is not in its table,
  and `SourceError` (fall through to the aggregator) when the dashboard could not
  be reached or parsed. A 403 wall means "we could not look", which must never be
  recorded as "Ohio has nothing".
* **The statewide row is a coverage-gated sum.** The dashboard publishes no
  statewide row of its own, so `parse_dashboard` sums the counties — and emits
  the `StateDay` only when all 88 census counties are present, exactly as
  `tx.py` does. Short coverage publishes counties and logs a warning. (The
  workbook routes still use Ohio's own `Statewide` row, unchanged.)
* **Everything is matched by name.** Workbook columns by header text; Power BI
  slots through the response's own `descriptor.Select`, so a reordered
  projection cannot transpose the measures onto the wrong counties. Anything
  unrecognised is `SchemaDrift`.
* **`_sub` refuses a negative in-person figure.** If `NO_EIP` ever stops nesting
  inside `INCL_EIP`, that is drift, not a number worth publishing.
* **Backfill never uses the dashboard**, so the 2022/2024 comparison anchors stay
  on the certified workbook.

### Verified end to end

```
# with curl_cffi present
OHScraper().fetch_history(2024)  -> 88 counties, statewide ballots_total 2 620 750  (publicfiles)
OHScraper().fetch_history(2022)  -> 88 counties, statewide ballots_total 1 473 983  (publicfiles)

# with curl_cffi import blocked, i.e. a stock GitHub Actions runner
OHScraper().fetch_history(2024)  -> SourceError: globalassets 403; publicfiles 403
OHScraper().fetch(2024, 2024-11-20)
                                 -> 88 counties, statewide ballots_total 2 262 963  (dashboard)

# today
python -m ev probe --state OH
OH [1:oh-sos,2:uf-election-lab,3:manual] -> pending
   globalassets: 404; publicfiles: index carries no 2026 November general;
   dashboard: no 2026 November general yet (it carries 2023 NOV GENERAL … 2026 MAY PRIMARY/SPECIAL)
```

### If it breaks

* **Dashboard 403 or 404** — the SoS republished the report and rotated its
  resource key. Redo steps 1–2 of Part 4 against the current
  `data.ohiosos.gov/portal/static/js/main.*.js` and update the five
  `POWERBI_*` constants. The failure is a `SourceError`, so Ohio falls through to
  the aggregator meanwhile.
* **`SchemaDrift: dashboard response is missing columns`** — Power BI renamed a
  measure. Re-run `modelsAndExploration` and read the visual `prototypeQuery`.
* **`publicfiles` 403 everywhere** — Cloudflare tightened again, or `curl_cffi`
  is missing. Check `_browser_session()` first.
* **Wayback** remains a working last resort for 2022/2024 only, at the two `id_`
  URLs in Part 2.
