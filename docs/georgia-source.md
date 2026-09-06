# Georgia — is there a headless early-vote source?

Investigated 2026-09-06. Every URL, status code and payload below was observed
live, from this machine, on that date.

**Headline: no. Georgia has exactly one machine-readable statewide early-vote
file, it is gated by reCAPTCHA Enterprise, the gate has no unprotected sibling,
and nothing else the state publishes carries ballots cast. `ga.py` therefore
raises `SourceError` and the ladder falls through to the aggregator — which is
the correct outcome, not a gap waiting to be filled.**

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
| 2 | `prod-ga-sos-vr-data-processing-bucket.s3.amazonaws.com` | The bucket the presigned URL points at | **403** on object GET *and* on bucket listing |
| 3 | `elections.sos.ga.gov/Elections/*.do` | The legacy Java portal | **Gone.** 301 to the gated page, or 403 |
| 4 | `results.sos.ga.gov` (Enhanced Voting ENR) | Election-night reporting | **Live and open, but has no turnout data** |
| 5 | `enr.clarityelections.com` / `results.enr.clarityelections.com` | Clarity ENR | **403 to every non-browser client**, and GA no longer uses it statewide |
| 6 | `sos.ga.gov` CMS pages | Where a daily statistics file would be linked | **403** — Cloudflare bot challenge, unsolvable headlessly |
| 7 | Georgia open-data portals | `data.georgia.gov`, `opendata.georgia.gov`, `gis.georgia.gov` | **NXDOMAIN.** They do not exist |
| 8 | Other `*.sos.ga.gov` hosts | `data`, `api`, `files`, `vote`, `absentee`, `turnout`, `reports`, `ballotstatus` | **NXDOMAIN.** Only `elections`, `mvp`, `results` resolve |
| 9 | Fulton / DeKalb / Cobb / Gwinnett county sites | The partial-coverage fallback | **No data files published**, and see the note below |

---

## 1. The gate is real, and it is Enterprise

The Submit button on `https://mvp.sos.ga.gov/s/voter-absentee-files` calls one
public Apex action, `VrMvpUtility.getPublicDownloadPresignedContent`, which
trades an S3 object key for a presigned `download_url`. Four probes of that
action, with the object key for the 2026 general
(`GAVR/ABSENTEE_ZIP/2026/A-12601/A-12601.zip`):

| `recaptchaResponse` sent | `version` | Response |
|---|---|---|
| field omitted entirely | `V3` | `ERROR` — `"No Recaptcha Response"` |
| `{"response": "", ...}` | `V3` | `ERROR` — `"V3 Recaptcha Failed"` |
| `{"response": "junk", ...}` | `V3` | `ERROR` — `"V3 Recaptcha Failed"` |
| `{"response": "junk", ...}` | `V2` | `ERROR` — `"V2 Recaptcha Failed"` |
| `{"response": "junk", ...}` | `""` | `ERROR` — `"Missing necessary information to handle the request."` |

The check is server-side and it is not a formality: there is no parameter
combination that skips it, and the V2 branch exists too, so switching version
buys nothing.

Loading the page in a real browser shows what mints the token:

```
GET https://www.google.com/recaptcha/enterprise.js?render=6LdUOgYfAAAAAGDYBY939FbeWV3bL-Ktw2EKMoua
```

That is reCAPTCHA **Enterprise**, not classic v3 — worth recording, because the
module docstring previously said v3 and the two behave differently under
assessment. The site key is kept in `ga.RECAPTCHA_SITE_KEY` as evidence; the
adapter never calls Google.

## 2. There is no unprotected sibling

The brief asked specifically whether the protection sits on the HTML form rather
than on the file. It does not.

* The file URL the page fetches *after* the check is a **presigned** S3 URL —
  signed, expiring, and unguessable. It is not a stable address we could fetch
  on our own.
* The bucket behind it is private in both directions:
  * `GET .../GAVR/ABSENTEE_ZIP/2026/A-12601/A-12601.zip` → **403 AccessDenied**
  * `GET .../GAVR/ABSENTEE_BALLOT/2026/A-12601/APPLING.csv` → **403 AccessDenied**
  * `GET .../?list-type=2` (bucket listing) → **403 AccessDenied**
  * No CloudFront alias: the host resolves straight to `s3-w.us-east-1.amazonaws.com`.
* No Salesforce guest REST surface is exposed: `/services/apexrest/` → 404,
  `/services/data/v60.0/` → 401.
* The Experience Cloud site has only two file pages at all. Probing 20 plausible
  slugs under `/s/` returned 200 for exactly `voter-absentee-files` and
  `voter-history-files`; everything else 404s.

### Everything on that page that *is* open

Worth stating plainly, because it is what `ga.py` already uses and it is the
reason the adapter can still resolve an election headlessly. These Apex actions
answer a plain unauthenticated POST with no cookies and no captcha:

| Action | Returns |
|---|---|
| `vrWebIntegrationController.getElectionOptions` | Every election for a year with its auto-number and per-election county list |
| `vrWebIntegrationController.getElectionDetails` | Election dates — for `A-12601`: advance voting opens **10/13/2026**, last day 10/23/2026, registration deadline 10/05/2026 |
| `vrWebIntegrationController.electionEndYear` | `2026` |
| `VrMvpUtility.getPicklistValues` | The 159 county names, the election categories |
| `VrMvpUtility.getRecaptchaDetails` | **The SoS's own switch for the gate** |

None of them carries a single ballot count. Every route to a count runs through
the presign.

## 3. Georgia's own kill switch — the one automatic way out

The page asks `VrMvpUtility.getRecaptchaDetails` on every load. Captured
verbatim on 2026-09-06 and saved as `tests/fixtures/ga/recaptcha_details.json`:

```json
{"Id": "m093d000000028HAAQ", "Active__c": true, "Bot_Check_Active__c": true}
```

Those are the Secretary of State's own toggles for the gate, readable without a
captcha. `ga.GAScraper.bot_check_active()` reads them, and `_download()` uses the
answer: with no `$GA_SOS_RECAPTCHA_TOKEN` and the switch **on**, it raises
`SourceError` immediately without touching the presign; with the switch **off**
it tries the presign with no token, and Georgia becomes headless with no code
change.

That branch is marked UNVERIFIED in the source and is written as an *attempt*,
never an assumption — it cannot be exercised against the live site while the
switch is on. If the tokenless presign still refuses, the adapter lands on the
same `SourceError` with the refusal appended. Every uncertain answer from the
probe (call failed, action errored, unfamiliar shape, a flag we have not seen)
is read as "the gate is up", so an unknown never turns into traffic against a
state election site.

## 4. `results.sos.ga.gov` — open, modern, and empty of turnout

Georgia's election-night reporting moved to Enhanced Voting. It is an Angular app
over a genuinely open JSON API at `/results/public/api`, no key and no captcha:

* `GET /api/jurisdictions/Georgia` → the jurisdiction plus **117 elections**,
  each with a `publicElectionId` such as `2024NovGen`, `GeneralPrimary51926`.
* `GET /api/elections/{jurisdiction}/{publicElectionId}/{stats,turnout,vr,localities,closeraces,data}`

The two that would have mattered are empty for every election tried:

| Endpoint | 2024 general (`2024NovGen`) | 2026 primary (`GeneralPrimary51926`) |
|---|---|---|
| `/stats` | `{"data": [], "totalRecordCount": 0}` | `{"data": [], "totalRecordCount": 0}` |
| `/turnout` | `{"data": [], "totalRecordCount": 0}` | `{"data": [], "totalRecordCount": 0}` |
| `/vr` | populated | populated — registered voters by county |

So the only populated series is **voter registration by county**, a denominator,
not ballots cast. And the November 3, 2026 general is not listed on the ENR site
at all yet: this system switches on at election time. It cannot produce a daily
early-vote curve because it never holds one.

Clarity (`results.enr.clarityelections.com/GA/…`) answers **403** to every
non-browser client, and Georgia's statewide reporting no longer runs on it.

## 5. Counties — nothing to scrape, and it would not be worth it

`sos.ga.gov` — the CMS, not the Aura API — is behind a Cloudflare **managed
challenge**: plain `curl`, `WebFetch` and a real automated Chrome all sat on
"Performing security verification" and never cleared. So the CMS pages that would
link a statistics file cannot be read headlessly either. (Note the split:
`mvp.sos.ga.gov` is served through Cloudflare too, but its `/s/sfsites/aura`
endpoint answers a plain cookieless POST without challenge. What blocks us there
is the reCAPTCHA assessment inside the Apex action, not the edge.)

The four big counties were checked directly:

* `fultoncountyga.gov/…/registration-and-elections` — 200, no data links
* `cobbcounty.gov/elections` — 200, no data links (only a `mailto:`)
* `gwinnettcounty.com/government/departments/elections` — 200, no data links
* `dekalbvotes.com` — connection failure; `dekalbcountyga.gov/departments/voter-registration-and-elections`
  is 200 with no data links

All four are JavaScript-rendered CMSes that publish no file. Two further reasons
not to build this even if files appeared in October:

1. **Timing.** Advance voting for the 2026 general does not open until
   **2026-10-13** (from `getElectionDetails`), so nothing any county might post
   exists yet to verify a parser against. Writing four scrapers now would mean
   inventing four URLs, which rule 1 of this repo forbids.
2. **Shape.** Fulton, DeKalb, Cobb and Gwinnett are ~25% of Georgia's electorate
   and among its most Democratic counties. County rows from those four alone,
   with no statewide row, would be a partial series that is easy to misread as
   a statewide one and is skewed in a specific direction. The aggregator's
   statewide number is a better answer than four blue metro counties.

---

## What this means for `ga.py`

1. `fetch()` raises **`SourceError`**, never `NotYetPublished`. Georgia's data
   exists; we cannot reach it. `NotYetPublished` would stop the ladder and blank
   the state out; `SourceError` falls through to `uf-election-lab`, which carries
   Georgia. This is the single most important line in the module.
2. The error text says so in words — "Georgia cannot be collected unattended" —
   because it is what a maintainer reads in the CI log every night and it must
   not look like a bug in the run.
3. `$GA_SOS_RECAPTCHA_TOKEN` remains the manual path: mint a token in a browser,
   export it, run within ~2 minutes. Useful for a one-off backfill; useless for
   an unattended nightly job.
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

Re-run the two cheap probes; both are unauthenticated and captcha-free:

```bash
# Has Georgia turned its own gate off?
python3 -c "
import sys; sys.path.insert(0,'src')
from ev.adapters.ga import GAScraper
print('bot check active:', GAScraper().bot_check_active())"

# Has the ENR site started carrying turnout?
curl -s 'https://results.sos.ga.gov/results/public/api/elections/Georgia/2024NovGen/turnout'
```

If the first prints `False`, `ga.py` will already be trying the tokenless
download on its own. If the second stops returning an empty `data` array,
Enhanced Voting has become a real second source and is worth an adapter.
