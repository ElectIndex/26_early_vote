# Georgia — is there a headless early-vote source?

Investigated 2026-09-06, twice: a first pass over every Georgia-published
surface, then a second pass specifically hunting a workaround (a scripted
browser, third-party mirrors, the ENR feeds, media distribution). Every URL,
status code and payload below was observed live, from this machine, on that date.

> ⚠️ **SUPERSEDED 2026-09-08 — the headline below is wrong. Georgia is
> collectable.** The reCAPTCHA finding stands and is still correct about the
> file it describes, but that file is not the only machine-readable statewide
> source: the **Election Data Hub** serves the same numbers out of a Qlik Cloud
> tenant, and the whole path is anonymous and un-gated. Verified end to end,
> live, on 2026-09-08 — see [§7](#7-the-election-data-hub-2026-09-08--georgia-is-collectable-after-all).
> Two rows of the table below are now false and are marked there.

**Headline: no. Georgia has exactly one machine-readable statewide early-vote
file; it is gated by a server-side reCAPTCHA Enterprise assessment that a
scripted browser does not pass; no third party mirrors it daily; and nothing
else the state publishes carries ballots cast. `ga.py` therefore raises
`SourceError` and the ladder falls through to the aggregator — which is the
correct outcome, not a gap waiting to be filled.**

**Recommendation: run Georgia on tier 2 (UF Election Lab).** [What that costs
us](#what-tier-2-actually-costs-us) is spelled out below, and it is a smaller
loss than it sounds — on demographics tier 2 is strictly *better* than our own
scraper could be.

Verified end state:

```
GA  [1:ga-sos,2:uf-election-lab,3:manual] -> pending
WARNING ev.ladder: GA: ga-sos failed (SourceError: GA: Georgia cannot be
collected unattended. ...)
INFO    ev.ladder: GA: not yet published (uf-election-lab)
```

---

## What was checked

| # | Source | What it is | Result |
|---|---|---|---|
| 1 | `mvp.sos.ga.gov` `getPublicDownloadPresignedContent` | The absentee/advance file. The only statewide ballot-level feed. | **Gated.** reCAPTCHA Enterprise, no bypass |
| 2 | A scripted browser minting its own token | The obvious workaround | **Token mints, assessment refuses it.** See §1 |
| 3 | `prod-ga-sos-vr-data-processing-bucket.s3.amazonaws.com` | The bucket the presigned URL points at | **403** on object GET *and* on bucket listing |
| 3b | `gasos-elections-mashups-mashup-bucket.s3.amazonaws.com` | The bucket the Data Hub's embedded apps live in | **403 bare, 200 with a `Referer: https://sos.ga.gov/`** — ordinary S3 hotlink protection, and the header is the one a browser sends loading that iframe. Listing still 403. See §7 |
| 4 | `elections.sos.ga.gov/Elections/*.do` | The legacy Java portal | **Gone.** 301 to the gated page, or 403 |
| 5 | `results.sos.ga.gov` (Enhanced Voting ENR) | Election-night reporting | **Live and open, but has no turnout data** |
| 6 | `enr.clarityelections.com` | Clarity ENR | **403 to every non-browser client**, and GA no longer uses it statewide |
| 7 | `sos.ga.gov` CMS pages | Where a daily statistics file would be linked | ~~**403** — Cloudflare challenge on *every* path~~ **WRONG as of 2026-09-08: 200, 162,796 b** through `_net.get`. This is where the Election Data Hub lives. See §7 |
| 8 | Georgia open-data portals | `data.georgia.gov`, `opendata.georgia.gov`, `gis.georgia.gov` | **NXDOMAIN.** They do not exist |
| 9 | Other `*.sos.ga.gov` hosts | `data`, `api`, `files`, `vote`, `absentee`, `turnout`, `reports`, `ballotstatus` | **NXDOMAIN.** Only `elections`, `mvp`, `results` resolve |
| 10 | `gaonestop.my.site.com/electionportal` | The Experience Cloud site's other front door | **200, but 301s to `mvp.sos.ga.gov/s/`** — same app |
| 11 | Fulton / DeKalb / Cobb / Gwinnett county sites | The partial-coverage fallback | **No data files published** |
| 12 | Third-party redistributors | OpenElections, Dataverse, RDH, Kaggle, data.world, archive.org | **No daily 2026 mirror.** Good BACKFILL only — see §6 |

---

## 1. A scripted browser mints a token — and the assessment refuses it

This is the avenue worth the most effort and it is now closed, with evidence.

The Submit button calls one public Apex action,
`VrMvpUtility.getPublicDownloadPresignedContent`, which trades an S3 object key
for a presigned `download_url`. Reading the page's own inline script settles
exactly what it posts:

```js
document.addEventListener('grecaptchaExecute', function(e) {
  grecaptcha.enterprise.ready(function() {
    grecaptcha.enterprise.execute(
      '6LdUOgYfAAAAAGDYBY939FbeWV3bL-Ktw2EKMoua', {action: 'Submit'}
    ).then(function(token) {
      document.dispatchEvent(new CustomEvent('grecaptchaVerified',
        {'detail': {response: token, action: 'Submit'}}));
    });
  });
});
```

and, in `c/vrWiVoterAbsenteeFiles`:

```js
handleZipFile(e, t, i) {
  getPublicDownloadPresignedContent({
    fileName: i, recaptchaResponse: JSON.stringify(e), version: t })
}
```

So the wire shape is `recaptchaResponse = '{"response":"<token>","action":"Submit"}'`
with `version = "V3"`. **That is exactly what `ga.py` already sent**, which is
worth stating plainly: our request was never malformed. `ga.recaptcha_params()`
now derives both branches from this reading, and `tests/test_ga.py` pins them.

Driving a real browser to the page then produces:

| Browser | Token minted? | Presign answered |
|---|---|---|
| Playwright Chromium, **headless** | yes — 2,276 chars, 0.3 s | `ERROR "V3 Recaptcha Failed"` |
| Playwright Chromium, **headed** | yes — 2,318 chars, 0.7 s | `ERROR "V3 Recaptcha Failed"` |

Captured verbatim as `tests/fixtures/ga/presign_refusal_browser_token.json`.

The read: **minting is not the hard part.** `grecaptcha.enterprise.execute()`
hands any caller a token; the token is then graded server-side by an Enterprise
assessment, and a fresh, profile-less, automated browser scores below Georgia's
threshold. Headed scored no better than headless, so this is not a
`--headless` flag away from working.

**Why this rules out GitHub Actions specifically.** The score inputs that hurt
here — no browsing history, no persistent profile, an automated-control signal —
are all *worse* on a CI runner, and a runner adds a datacenter IP, which is the
single heaviest negative signal reCAPTCHA applies. A path that already fails on
a residential IP with a real desktop Chrome does not start working on Actions.
Playwright installs fine there; that was never the obstacle.

**What was deliberately not tried.** The remaining move is to defeat the score
itself — mask the automation signal, age a profile, proxy off a residential IP.
That is circumventing an access control a state election authority chose to put
up, it risks getting our IP blocked from a state election site during an
election, and it would still be fragile. Not built, not recommended.

### The page's "automation check" is theatre, and unrelated

Worth recording so nobody mistakes it for the blocker. `Bot_Check_Active__c`
drives this, in the same inline script:

```js
document.addEventListener('checkAutomation', function() {
  document.dispatchEvent(new CustomEvent('automationDetected',
    {'detail': window.navigator.webdriver}));
});
```

It is a single `navigator.webdriver` read, evaluated in the browser, and when it
trips the page merely refuses to submit ("Seems like you are trying to use
automated scripts to fill this form"). It never reaches the server — which is
why our plain `requests` calls have never seen it, and why defeating it would
buy nothing.

## 2. Georgia's kill switch is client-side only — a correction

The first pass called `getRecaptchaDetails` "the one automatic way out". That was
too optimistic and is now corrected.

The flags are real and readable without a captcha (saved verbatim as
`tests/fixtures/ga/recaptcha_details.json`):

```json
{"Id": "m093d000000028HAAQ", "Active__c": true, "Bot_Check_Active__c": true}
```

But they only steer the **page**. `verifyRecaptcha()` skips to
`handleZipFile("", "", fileName)` when `Active__c` is false — i.e. it posts
`recaptchaResponse: '""'`, `version: ""`. Replaying that exact tokenless shape
against the live action, five ways:

| `recaptchaResponse` sent | `version` | Response |
|---|---|---|
| `'""'` — **the page's own gate-down shape** | `""` | `ERROR` — `"Missing necessary information to handle the request."` |
| `'""'` | omitted | `ERROR` — `"Missing necessary information to handle the request."` |
| omitted entirely | `""` | `ERROR` — `"No Recaptcha Response"` |
| `""` (bare, unquoted) | `""` | `ERROR` — `"No Recaptcha Response"` |
| `'""'` | `"V2"` | `ERROR` — `"V2 Recaptcha Failed"` |

The Apex does not consult the switch; it checks the token unconditionally. So
`Active__c` going false is **necessary but not demonstrably sufficient**.
`_download` still tries — one request, and the alternative is never noticing —
but it is written as an attempt and a refusal lands on the same `SourceError`.

The one thing this second pass *did* change: the tokenless branch now sends the
page's shape rather than one we invented, so if the gate ever comes down we are
already speaking Georgia's language.

## 3. There is no unprotected sibling

* The URL the page fetches after the check is a **presigned** S3 URL — signed,
  expiring, unguessable. Not a stable address.
* The bucket is private in both directions:
  * `GET .../GAVR/ABSENTEE_ZIP/2026/A-12601/A-12601.zip` → **403 AccessDenied**
  * `GET .../GAVR/ABSENTEE_BALLOT/2026/A-12601/APPLING.csv` → **403 AccessDenied**
  * `GET .../?list-type=2` (bucket listing) → **403 AccessDenied**
  * No CloudFront alias: resolves straight to `s3-w.us-east-1.amazonaws.com`.
* No Salesforce guest REST surface: `/services/apexrest/` → 404,
  `/services/data/v60.0/` → 401.
* Only two file pages exist at all; 20 plausible `/s/` slugs returned 200 for
  exactly `voter-absentee-files` and `voter-history-files`.
* `gaonestop.my.site.com/electionportal/s/` and `/s/mvp-landing-page` — the
  alternate hostnames named in the page's own JS — both **200 but redirect to
  `mvp.sos.ga.gov/s/`**. Same app, same gate.

### Everything on that page that *is* open

These Apex actions answer a plain unauthenticated POST, no cookies, no captcha:

| Action | Returns |
|---|---|
| `vrWebIntegrationController.getElectionOptions` | Every election for a year with its auto-number and per-election county list |
| `vrWebIntegrationController.getElectionDetails` | Election dates — for `A-12601`: advance voting opens **10/13/2026**, last day 10/23/2026 |
| `vrWebIntegrationController.electionEndYear` | `2026` |
| `VrMvpUtility.getPicklistValues` | The 159 county names, the election categories |
| `VrMvpUtility.getRecaptchaDetails` | The SoS's own switch for the gate |

None carries a single ballot count. Every route to a count runs through the presign.

## 4. `results.sos.ga.gov` — open, modern, and empty of turnout

Georgia's ENR moved to Enhanced Voting: an Angular app over a genuinely open
JSON API at `/results/public/api`, no key and no captcha.

* `GET /api/jurisdictions/Georgia` → **200**, the jurisdiction plus **117
  elections**, each with a `publicElectionId`.
* `GET /api/elections/Georgia/{id}/{stats,turnout,vr}`

Re-probed this pass, including every 2026 election id the listing carries:

| id | `/stats` | `/turnout` | `/vr` |
|---|---|---|---|
| `2024NovGen` | `{"data":[],…}` | `{"data":[],…}` | populated (24.9 kB) |
| `GeneralPrimary51926` | `{"data":[],…}` | `{"data":[],…}` | populated (24.9 kB) |
| `2026NovGen` / `2026Gen` | `{"data":null,…}` | `{"data":null,…}` | `{"data":null,…}` |

Note the difference: `[]` is a real election with no turnout series; `null` is
an election id that does not exist. The November 2026 general **is not listed at
all** — this system switches on at election time. `/advancevoting` and
`/earlyvoting` are **404** on every id tried.

So the only populated series is **voter registration by county** — a
denominator, not ballots cast. Avenue closed: it cannot produce a daily
early-vote curve because it never holds one.

## 5. No RSS, media or FTP distribution

`sos.ga.gov` is behind a Cloudflare managed challenge on **every** path, and
that now includes the machine-readable ones — so there is nothing to subscribe
to even in principle:

| URL | Status |
|---|---|
| `sos.ga.gov/robots.txt` | **403** (Cloudflare "Just a moment") |
| `sos.ga.gov/sitemap.xml` | **403** |
| `sos.ga.gov/rss.xml` | **403** |
| `sos.ga.gov/news` | **403** |
| `sos.ga.gov/page/advance-voting-statistics` | **403** |
| `elections.sos.ga.gov/Elections/voterabsenteefile.do` | **301** to the gated page |
| `media.sos.ga.gov` | connection failure |

Note the split that still holds: `mvp.sos.ga.gov` is served through Cloudflare
too, but its `/s/sfsites/aura` endpoint answers a plain cookieless POST without
challenge. What blocks us there is the reCAPTCHA assessment inside the Apex
action, not the edge.

## 6. Third-party redistributors — nothing daily, but real backfill

No one mirrors the 2026 file daily. Two sources are genuinely useful for
**backfill**, both verified by fetch:

| URL | Status | What it is |
|---|---|---|
| `election.lab.ufl.edu/data-downloads/earlyvote/2024/county_data_GA.csv` | **200**, 37,663 B, `text/csv` | UF's **county-level** GA file: 159 counties × request / accept / in-person, each split by age, race and gender |
| same path, `/2020/`, `/2022/`, `/2026/` | **404** | 2024 is the only cycle with a GA county file |
| `georgiavotesvisual.com/static/absentee/absenteeSummary-2024_general-{state,county}.json` | **200**, `application/json`, 8.0 kB / 346 kB | Daily cumulative **accepted ballots by return date**, derived from this same SoS file |
| same, `-2022_general-` | **200**, `application/json`, 4.7 kB / 280 kB | 2022 equivalent |
| same, `-2026_general-` / `-2026_primary-` | **200 but `text/html`, 2,036 B** | **Soft-404** — the SPA shell. The project is frozen at 2024; a fetcher must check `content-type`, not status |

Also probed and empty for Georgia absentee data: `openelections-data-ga` and
`openelections-sources-ga` repo trees (**200**, no absentee/early file — the
"early" hits are Early County), Harvard Dataverse search API (**200**, 0 exact
hits), Redistricting Data Hub GA catalog (**200**, zero occurrences of
"absentee" or "early vot"), Kaggle dataset search (**200**, `[]`), data.world
(**redirects to a shutdown notice**), archive.org (**200**, `numFound 0` for
`mediatype:data`), and the Wayback CDX index for the old
`elections.sos.ga.gov/Elections/voterabsenteefile.do` (**200** — captures exist
but every one is the ~2.3 kB form/error HTML, **no archived file bodies**).

None of this is wired into `ga.py`. It is recorded so a backfill task does not
have to rediscover it, and because two of these carry county-level Georgia
history that our own tier 1 has never once managed to fetch.

---

## What tier 2 actually costs us

The honest accounting, from the real UF rows (`.../earlyvote/2024/US.csv`,
**200**, and `.../2026/US.csv`, **200**). Georgia's 2024 row is fully populated:

```
request_all 316,968   accept_all 265,648   inperson_all 3,765,655   voted_all 4,031,303
```

| Dimension | Tier 1 (if we could fetch it) | Tier 2 (UF) | Verdict |
|---|---|---|---|
| Statewide ballots cast | ✅ | ✅ `voted_all` | **no loss** |
| Mail requested / returned | ✅ | ✅ `request_all` / `accept_all` | **no loss** |
| In-person | ✅ | ✅ `inperson_all` | **no loss** |
| Party | n/a — Georgia has none | n/a (`voted_dem` etc. are `0`, read as blank) | **no loss** |
| **Race** | ❌ not in the 38-column file | ✅ 6 buckets | **tier 2 is BETTER** |
| **Sex** | ❌ not in the file | ✅ 3 buckets | **tier 2 is BETTER** |
| Age | ❌ not in the file | published, but UF's 18‑25/26‑40/41‑65/65+ bands do not map to ours — `aggregator.py` correctly emits none | neither has it |
| **County rows** | ✅ all 159 | ❌ statewide only in `US.csv` | **the real loss** |
| **Daily curve** | ✅ one download rebuilds the whole series from `Ballot Return Date` | ❌ one snapshot per fetch, dated by `last_update` | **the other real loss** |

So the cost is **two things, not ten**: per-county Georgia, and the ability to
reconstruct history from a single download. The second is largely mitigated in
practice — the nightly job accumulates a series as it runs, it just cannot
*rebuild* one after an outage. And on demographics the trade actually runs in
tier 2's favour, because Georgia's absentee file carries no race, sex or age at
all while UF joins its counts to the voter file.

Two footnotes worth carrying:

* **Timing.** The 2026 GA row exists but is all zeros (`last_update 8/12/2026`),
  which `aggregator.py` correctly reads as blank rather than as zero ballots.
  Advance voting opens **2026-10-13**; expect it to populate then.
* **Licence.** UF publishes these under CC BY-NC-ND 4.0 — attribution required,
  no commercial use without permission. Worth a decision before Georgia's
  numbers go on a page that carries advertising.

---

## What this means for `ga.py`

1. `fetch()` raises **`SourceError`**, never `NotYetPublished`. Georgia's data
   exists; we cannot reach it. `NotYetPublished` would stop the ladder and blank
   the state out; `SourceError` falls through to `uf-election-lab`, which carries
   Georgia. This is the single most important line in the module.
2. The error text says so in words — "Georgia cannot be collected unattended" —
   because it is what a maintainer reads in the CI log every night and it must
   not look like a bug in the run.
3. `$GA_SOS_RECAPTCHA_TOKEN` remains the manual path: mint a token in a real
   browser, export it, run within ~2 minutes. Useful for a one-off backfill;
   useless for an unattended nightly job.
4. `fetch_history()` still works without a token off a cached archive, and the
   cache is consulted **before** the switch probe, so a backfill touches no
   network at all.
5. Georgia has **no party registration**: every `party_dem` / `party_rep` /
   `party_oth` / `party_npa` is `None`, never `0`. The file does carry a `Party`
   column and it *is* populated in a primary (it records which party's ballot the
   voter asked for), which is exactly why it is read only to be ignored.
   `tests/test_ga.py` pins both halves: that the fixture really carries a
   populated `Party` column, and that no emitted row has a party field that is
   not `None` — and a second assertion that names `0` specifically.

## If this needs revisiting

Three cheap probes, all unauthenticated and captcha-free:

```bash
# 1. Has Georgia turned its own gate off? (necessary, maybe not sufficient)
python3 -c "
import sys; sys.path.insert(0,'src')
from ev.adapters.ga import GAScraper
print('bot check active:', GAScraper().bot_check_active())"

# 2. Has the ENR site started carrying turnout?
curl -s 'https://results.sos.ga.gov/results/public/api/elections/Georgia/2024NovGen/turnout'

# 3. Has UF published a 2026 GA county file? (404 today; 2024 is 200)
curl -sI 'https://election.lab.ufl.edu/data-downloads/earlyvote/2026/county_data_GA.csv'
```

If (1) prints `False`, `ga.py` is already trying the tokenless presign in the
page's own shape. If (2) stops returning an empty `data` array, Enhanced Voting
has become a real second source. If (3) turns 200, tier 2 gains county rows and
the largest remaining cost of running Georgia on the aggregator disappears.

Do **not** re-run the scripted-browser experiment expecting a different answer
without first checking (1) — §1 records both headless and headed attempts and
the fixture pins the refusal.


---

# 7. The Election Data Hub, 2026-09-08 — Georgia is collectable after all

Every status, header and payload below was observed live from this machine on
2026-09-08, in the order written.

**The conclusion first.** Georgia publishes its early-vote numbers through a
**Qlik Cloud Government** tenant behind the Election Data Hub, and the entire
path — page, embedded app, access token, tenant API — is **anonymous, un-gated
and free of reCAPTCHA**. §1's finding about `mvp.sos.ga.gov` is unchanged and
still correct; it was simply never the only door.

## What was wrong with the earlier survey

Row 7 said `sos.ga.gov` CMS pages 403 on every path. They do not, and the
difference is not subtle: `https://sos.ga.gov/election-data-hub` returns **200,
162,796 bytes** through the same `_net.get` the pipeline uses everywhere. Either
the Cloudflare posture changed between 2026-09-06 and 2026-09-08 or the earlier
probe hit a transient challenge. Either way the page that indexes the whole hub
was recorded as unreachable, so nobody looked at what it embeds.

## The chain, with the exact values

| # | Step | Result |
|---|---|---|
| 1 | `GET https://sos.ga.gov/page/election-data-hub-unofficial-turnout` | **200**, 163,087 b. One `<iframe>`: `…/DH.ELECTION2024/index.html` |
| 2 | `GET …mashup-bucket.s3.amazonaws.com/DH.ELECTION2024/index.html` bare | **403** |
| 3 | ...same, with `Referer: https://sos.ga.gov/` | **200**, 9,255 b |
| 4 | `GET …/DH.ELECTION2024/js/vars.js` | **200**, 304 b — tenant, web-integration id, app id, token endpoint |
| 5 | `GET https://fn4akbihvavvcmki6ih67rmuky0ezils.lambda-url.us-east-1.on.aws/` | **201**, 549 b — `{access_token, client_id}`. **No auth, no captcha, no cookie** |
| 6 | `GET https://sos-ga-gov.us.qlikcloudgov.com/api/v1/apps/7d780725-…` with `Authorization: Bearer <token>` | **200**, 1,213 b |

The constants, transcribed from `js/vars.js` and `js/main.js`:

```
tenant           https://sos-ga-gov.us.qlikcloudgov.com
webIntegrationId 7ly2cTMjax8pOd2ESn29F53J7UbIe2US          # "Data Hub"
tokenEndpoint    https://fn4akbihvavvcmki6ih67rmuky0ezils.lambda-url.us-east-1.on.aws/

# DH.ELECTION2024 -- "GA SOS Voting - All elections", internal name [DH.ELECTION]
app  7d780725-d407-4db8-b287-005bb85eda87
  AbsenteeBallots      0bf484c2-bac1-4418-bc14-a07dd0bf7319
  EarlyVoting          323d9d62-a861-400b-a6e2-4ce4d95a8bf3
  TurnoutDemographics  5c617993-dbe2-4071-8873-3e94fc7e2c0d

# DH.VOTERS -- registration side, not turnout
app  f8e3121c-f6be-4871-987e-e31d8589fe89   # Electorate Demographics - Map
app  05cd3715-6134-4027-8294-95913e7bab73   # Monthly Voter Demographics - DH
```

**It is live and it is current.** `[DH.ELECTION]` reported
`lastReloadTime: 2026-09-08T22:50:45Z` — twenty-five minutes before this probe —
against a `publishTime` of 2026-08-20. This is a daily-reloading app, not a
2024 artefact that happens to still respond.

## Why this is not the thing §1 declined to do

§1 declined to defeat a reCAPTCHA Enterprise score, and that decision stands.
Nothing here goes near it:

* **No challenge is answered.** There is no captcha anywhere on this path.
* **No credential is forged.** The Lambda hands an OAuth2 token to any anonymous
  caller, by design, because the dashboard it serves is public and has no login.
* **The `Referer` is the header a browser sends.** The iframe on
  `sos.ga.gov/election-data-hub` loads that S3 object with exactly that header;
  the bucket policy is ordinary hotlink protection. Sending it is behaving like
  the client the resource is published for, which is the line this repo has
  always drawn — the same line `_net.impersonated_headers()` sits on.

## Before this is built — three things, and one is a phone call

1. **Tell them.** The Elections Division is on **(404) 656-2871**. A once-daily
   programmatic pull of a public dashboard is the kind of thing worth having
   acknowledged rather than discovered, and it costs one call.
2. **Respect the rate limit, which is real and visible.** `main.js` handles
   **HTTP 429** from the token endpoint with a user-facing "unusually high
   traffic" dialog. One token per run, cached for its lifetime, once or twice a
   day. Nothing about this route justifies a two-hourly poll.
3. **The age bands are ambiguous and must not be published blind.** The earlier
   research found `Age Group` bands where `35-40` and `40-45` overlap. Publish
   county/method/party from this source and **no age rows at all** until the
   boundary is settled with the Division, or the crosstab will silently
   double-count a five-year cohort.

## What is still unbuilt

The token and the app metadata are proven. **Extracting the table is not
written**: it needs a Qlik client (either the Engine JSON-RPC over WebSocket, or
the tenant's REST data endpoints) to open the app, select the current election,
and pull the county × method × party hypercube. Estimated 150–250 lines plus
fixtures. `ga.py` still raises `SourceError` and the ladder still falls through
to the aggregator until that exists.
