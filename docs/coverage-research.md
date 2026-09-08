# Coverage research — which states publish usable early-vote data during the season

The question: for each state NOT already in `registry.py`'s `TIER1`, does the
official state election authority publish a **machine-readable file, while early
or absentee voting is happening**, that a daily scraper can run against?

Not "does the state publish early-vote numbers" — nearly all of them do, in a
press release, a Power BI embed or a PDF. The bar here is a file a program can
fetch and parse on a schedule, keyed to geography we can join, published *during*
the voting period rather than after the canvass.

**Every URL below was fetched.** Where a fetch failed, was blocked, or could not
exist yet, it says so. Nothing here is a guessed URL.

**Checked: 36 states + DC.** Genuinely usable during-season machine-readable
source: **11**. Built this pass: **3** (MD, KY, TN). Everything else either
publishes nothing a daily job can use, or is usable but was rejected for a
reason recorded below.

---

## Summary table

**Dims**: county / party / method (mail vs in-person) / age / race / sex.

| State | During-season machine-readable? | Format | Geographic unit | Dims | Confidence | Outcome |
|---|---|---|---|---|---|---|
| **MD** | **yes** — SBE `EarlyVoting RAW data.csv` | CSV | county (24) | county, party, sex (+age, unused) | high | **BUILT** `md-sbe` |
| **KY** | **yes** — SBE `Absentee_Public_MMDDYY.xlsx`, dated daily | XLSX | county (120) | county, party (DEM/REP only), method | high | **BUILT** `ky-sbe` |
| **TN** | **yes** — SoS `…EarlyAbsentee….xlsx`, long format | XLSX | county (95) | county, method | high | **BUILT** `tn-sos` |
| WA | yes — voter-level daily ZIP/CSV | CSV in ZIP | county (39) | county, method, sex, ballot status | high | rejected: effort vs 2026 stakes |
| OK | yes — undocumented dashboard JSON API | JSON | county (77) — **not precinct**, see below | county, party, method | medium | rejected: fragility |
| SC | yes — absentee + EV workbooks | XLSX/CSV | county (46) | county, method, **race** | medium-high | rejected: filename drift |
| IL | yes — pre-election counts CSV/XLSX | CSV/XLSX | 108 election authorities | authority, method | medium-high | rejected: needs 108→102 crosswalk |
| MT | yes — Tableau CSV export | CSV | county (56) | county, method | medium | rejected: no election provenance |
| ID | yes — Datawrapper CSVs + a .gov JSON | CSV/JSON | county (44) | county, party*, method, age | medium | rejected: cycle-specific chart IDs |
| MN | partial — county table, HTML only, bot-protected | HTML | county (87) | county, method | high | rejected: needs a headless browser |
| SD | partial — weekly HTML, statewide only | HTML | statewide | party, method | high | rejected: no county |
| KS | partial — Power BI model, statewide only | JSON | statewide | method | high | rejected: no county |
| ND | partial — one HTML page, ASP.NET postback per county | HTML | county (53) | county, method | low | rejected: cadence + stakes |
| NY | no at state level (NYC BOE only, HTML) | — | — | — | high | — |
| NJ | no — first per-county file each cycle is election night | — | — | — | high | — |
| OR | no — daily file is **PDF**, filename changes every cycle | PDF | county (36) | county, party, day | high | — |
| HI | no — daily reports are per-county **PDF** | PDF | county (4) | county | high | — |
| AK | no — keyed by **House District**, not borough | PDF/HTML | house district | method | high | — |
| MA | no during-season since 2018; post-election XLSX is good | — | municipality (351) | — | high | — |
| LA | no — early-vote stats are **post-election only** | XLS/PDF | parish | parish, party, race, sex, age | high | — |
| CA | no — SoS publishes nothing until E+2 | — | — | — | high | — |
| UT | no — the 2020 daily page was never revived | — | — | — | high | — |
| NM, CT, RI, DE, VT, WV, DC | no | — | — | — | medium-high | — |
| NE, WY, MO, IN, AL, AR | no | — | — | — | high | — |
| MS | **unverified** — `sos.ms.gov` 403s all automated requests | — | — | — | n/a | — |

\* Idaho's "party" during a primary is the ballot chosen, not registration.

---

## Built this pass

Chosen by 2026 consequence — a competitive or open Senate/governor race, or a
large electorate — crossed with how much the file actually carries and how
reliably it can be fetched unattended for six weeks.

### Maryland — `src/ev/adapters/md.py` (`md-sbe`)

**Why:** open governor's race, 4M registered voters, and the richest file of the
three. **Verified URLs**, all fetched, HTTP 200, real CSV:

- `https://elections.maryland.gov/press_room/2022_stats/GG22/EarlyVoting%20RAW%20data.csv` — 8.7 MB, 95,042 rows
- `https://elections.maryland.gov/press_room/2024_stats/PG24/EarlyVoting%20RAW%20data.csv` — 14.7 MB, 160,472 rows
- `https://elections.maryland.gov/press_room/2026_stats/GP26/EarlyVoting%20RAW%20data.csv` — 4.9 MB (June 2026 *primary*)

Header, verbatim and identical across all three cycles:

```
COUNTY_CODE,COUNTY_NAME,congressional_district_code,legislative_district_code,
councilmanic_district_code,Precinct,VOTER_STATUS,GENDER_CODE,AgeGroup,PARTY_CODE,
EARLY_VOTE_CENTER,Day1,Day2,Day3,Day4,Day5,Day6,Day7,Day8
```

The `Day1..Day8` columns mean **one download reconstructs the entire daily
curve**, like North Carolina's file and unlike any snapshot source: a missed run
cannot lose a day, and backfilling 2022 and 2024 is two downloads. A live run of
the finished adapter against the real 2022 file reproduces Maryland's published
total exactly:

```
2022-11-03  total 381,972   DEM 220,469  REP 112,083  UNA 46,041  other 3,379
```

Four judgement calls, all documented in the module and locked by tests:

1. **Never the primary.** The bare `press_room/<cycle>_stats/` folder *also*
   holds an `EarlyVoting RAW data.csv`, and for both 2022 and 2024 that copy is
   the **primary's** (4.1 MB vs the general's 8.7 MB in 2022). The adapter
   accepts only a folder whose election code says general — `GG22`, `PG24`,
   `GG26` — and additionally scrapes the press-room index, which correctly
   returns the general for 2022 and 2024 and nothing for 2026 (only the `GP26`
   primary is posted). Reading the cycle root would have published a third of
   the real number, confidently.
2. **`Day1` = Election Day − 12.** The file carries no dates. Maryland's window
   is statutory: eight days ending the Thursday before Election Day. Verified
   for all three cycles — 2022-10-27, 2024-10-24, 2026-10-22, each a Thursday,
   because Election Day is always a Tuesday. The adapter requires exactly eight
   `Day` columns and raises `SchemaDrift` otherwise, so a lengthened window
   fails loudly instead of redating every count.
3. **Age is carried but NOT published.** Maryland's bands are 18-24 / 25-44 /
   45-64 / 65 and older. `normalize.age_band("25-44")` returns `"25-34"` and
   `("45-64")` returns `"45-54"` — recognised, and wrong. Publishing two-thirds
   of Maryland's early voters under the wrong band is worse than publishing no
   age breakdown, so only sex is published. Race is not in the file.
4. **Mail stays blank.** This file is the early-voting centres.
   `Absentees_Sent_and_Returned_by_County.xlsx` exists and would supply
   `mail_requested`/`mail_returned`, but the SBE server serves it **malformed**:
   its end-of-central-directory record claims the central directory starts at
   offset 46138 when it actually starts at 45927, so Python's `zipfile` refuses
   it (Excel tolerates it). Verified byte-for-byte, not assumed. So both mail
   fields are `None`, never `0`.

**Unverified path, flagged in `md.GENERAL_CODES`:** `2026_stats/GG26/` cannot
exist yet. It returns the SBE CMS's 200-with-HTML shell today, which is exactly
the `NotYetPublished` path — and that is what the adapter returns right now.

### Kentucky — `src/ev/adapters/ky.py` (`ky-sbe`)

**Why:** an **open US Senate seat** in 2026, and the richest during-season file
found anywhere in this search — county × party × four methods, on a dated URL.

```
https://elect.ky.gov/Documents/Absentee_Public_MMDDYY.xlsx
```

**Verified 200 + real xlsx:** `100824`, `102924`, `103024`, `103124`, `110124`,
`110224`, `110524` (2024 general) and `051126`, `051326`, `051526`, `051826`
(2026 primary). Dates with no file 404 cleanly — `090526` returns 404 today,
which is the `NotYetPublished` path. There is no such file for 2022; the
practice starts with the 2024 cycle.

Header on **row 10** (rows 1-9 are the SBE's prose caveats), four side-by-side
blocks each with its own `County` key column:

```
County | All Mail-in Applications | DEM/REP Mail-in Applications |
All Ballots SENT | DEM/REP Ballots Sent | All Ballots RETURNED | DEM/REP Ballots RETURNED |
% ALL Returned || County | Excused In-person | DEM/REP Excused In-person ||
County | No Excuse In-person | DEM/REP No Excuse In-person ||
County | FPCA Applications | FPCA RETURNED || UNOFFICIAL Total Absentee
```

A live backfill of 2024 recovers a genuine sixteen-day series (dated filenames
mean the archive really is a daily series, not one overwritten report):

```
2024-09-30      0        2024-10-25   82,906      2024-11-01  355,909
2024-10-08 15,097        2024-10-29  105,265      2024-11-02  590,442
2024-10-14 26,737        2024-10-31  125,052      2024-11-03  792,476
2024-10-21 48,211                                 2024-11-05  803,596
```

Judgement calls:

- **DEM and REP are real counts; NPA and OTH are blank and must stay blank.**
  Kentucky registers by party but splits out only Democrats and Republicans. The
  residual (355,909 − 150,268 − 179,101 = 26,540 on 2024-11-01) mixes
  unaffiliated voters with Libertarians and the rest, and `normalize.py` is
  explicit that collapsing those two together loses the most-watched number in
  an early-vote story. So neither is published.
- **FPCA is added, not dropped.** Kentucky's own `UNOFFICIAL Total Absentee` is
  `mail returned + FPCA returned + excused in-person + no-excuse in-person`,
  which is what proves `All Ballots RETURNED` is domestic-only. Reading only
  that column would understate returns, exactly as in Ohio.
- **A 90-day window, because the primary uses the same filename pattern.**
  `Absentee_Public_051826.xlsx` is the 2026 primary and is still on the server.
  A run outside 90 days of Election Day refuses rather than publishing primary
  turnout as general turnout.
- **A 7-day lookback**, because Kentucky skips days: 2024-10-15 and 2024-10-22
  have no file while 2024-10-08 does. Asking only for today's would report
  "nothing yet" with a good snapshot sitting on the server.
- **Blank vs zero, in one file.** On 2024-10-08 in-person voting had not opened:
  Kentucky left the county cells *empty* and wrote a literal `0` statewide. Both
  are published as what they are. That fixture is in the test suite for exactly
  this reason.

### Tennessee — `src/ev/adapters/tn.py` (`tn-sos`)

**Why:** an **open governor's race** in 2026, 4.5M registered voters, and a long-
format file where one download carries the whole curve.

**Verified 200 + real xlsx:**

- `https://sos-prod.tnsosgovfiles.com/s3fs-public/document/20241105EarlyAbsentee1031.xlsx` (48,729 B)
- `…/20241105EarlyAbsentee1030.xlsx`, `…/20241105EarlyAbsentee1025.xlsx`
- `…/20221108EarlyAbsenteethrough1103.xlsx` (45,919 B)

One row per county per day, plus Tennessee's own `Statewide` rows. The parsed
2024 file gives 2,214,870 early and absentee ballots over fourteen days, which is
Tennessee's published figure.

Judgement calls:

- **Two layouts, and only two.** 2024 is `County | Date | EarlyVoting | Absentee
  | Day Total`; 2022 is `CoID | County | Date | Day Total`, with **no method
  split at all**. Both parse; 2022's mail and in-person fields come out `None`,
  never `0`. The header is matched as a *set* against an allow-list of the two
  verified layouts, so a renamed column raises `SchemaDrift` rather than silently
  publishing "method not reported" for a cycle where Tennessee did report it.
- **`Day Total` ≠ `EarlyVoting + Absentee`.** On nine of the 1,344 rows in the
  2024 file it is one to three ballots higher — statewide on 10/28, 10/29 and
  10/31, and in Anderson, Chester, Hawkins, Perry and Rutherford. So
  `ballots_total` is always Tennessee's own `Day Total`, never a sum of parts.
- **No date window is needed**, unlike Kentucky and Maryland: the filename is
  prefixed with the ELECTION date, so the August state primary's files
  (`20260806…`) can never be mistaken for the November general's (`20261103…`).
- **An S3 403 means "no such file".** Tennessee's bucket answers a key that does
  not exist with **403 AccessDenied**, not 404, because listing is denied —
  verified: `…EarlyAbsentee1101.xlsx` returns 403 with a 243-byte XML body while
  `…1031.xlsx` returns the workbook. The adapter reads 403 as absence and any
  other error as a real fault that should fall through a tier. A test pins both
  halves of that.
- The landing page `https://sos.tn.gov/elections/services/early-voting-data` is
  **404 today** — Tennessee unpublishes it off-season — and 200 during early
  voting. It is scraped first so a filename we did not predict is still found.

---

## Usable, but not built — and why

These are real sources. They were passed over this round for the reasons given,
and each is a reasonable next target.

**Washington — the best data in the country that we are not using.** A
voter-level daily CSV inside a dated ZIP:
`https://www.sos.wa.gov/sites/default/files/current_election/Statewide{YYYY-MM-DD}.zip`
— 404 live right now because `current_election/` is purged between cycles;
archived `Statewide2025-10-24.zip` (14.4 MB), `2025-10-31.zip`, `2025-11-14.zip`
all verified 200 `application/zip`. Header:
`Ballot ID,Voter ID,County,First Name,Last Name,Gender,Election,Ballot Status,
Challenge Reason,Received Date,Address,City,State,Zip,Country,Split,Precinct,
Return Method,Return Location,Party`. Because every ballot carries its own
`Received Date` and `Ballot Status`, one download rebuilds the whole curve, NC
style. The SoS page states returns begin **2026-10-20**. Not built because
Washington has neither a Senate nor a governor's race in 2026, and the adapter
is a streaming-zip parser on the scale of `nc.py` — the largest single build in
this list. It should be first next round.

**Oklahoma — an undocumented but unauthenticated JSON API.**
`https://stats.okelections.gov/dashboardControl/data/DashboardItemGetAction` with
a URL-encoded JSON `query`; verified 200 for 2026-11-03, 2024-11-05 and
2022-11-08, with `PartyDesc` and `DeliveryMethod` filters and a companion
`VHCountsByCounty` dashboard for in-person early voting. County + party + method,
77 counties. Not built because it is an internal DevExpress dashboard endpoint
with no contract — the shape of that `query` parameter is the whole interface,
and it can change without notice mid-season. Oklahoma's open governor's race
makes it worth revisiting with a fixture-backed parser.

**South Carolina — the only source outside NC with a race breakdown.** Open
governor's race and a Senate race. Nine per-race worksheets on the daily
absentee workbook, plus a separate early-voting file rolling county → poll
place. Verified: `2024-10-29-Absentee-Statistics-by-Race-for-GE.xlsx` (74,282 B),
`2024-GE-Early-Voting-Statistics-4.xlsx` (28,152 B),
`Absentee-Stats-2022-10-21-noon-GE.xlsx` (77,673 B). Not built because filenames
drift hard between cycles (`Absentee-Stats-YYYY-MM-DD-noon-GE.xlsx` in 2022 vs
`YYYY-M-D-Absentee-Statistics-by-Race-for-GE.xlsx` in 2024, day not zero-padded,
WordPress `-N` dedupe suffixes), so it is an index-scraper plus a nine-sheet
parser with a header on row 10 — more than a session's work to do safely.

**Illinois — open Senate seat, and a real file.**
`https://elections.il.gov/VotingAndRegistrationSystems/PreElectionCounts.aspx`
says of itself: *"These numbers will fluctuate daily."* An ASP.NET postback
yields a CSV: `JID,Name,ElectionDate,By-Mail,By-Mail Returned,Early,Grace`. Not
built because the unit is **108 election authorities**, not counties: 102
counties plus six city boards (Chicago, Bloomington, Danville, East St. Louis,
Galesburg, Rockford), so six county FIPS each need two rows summed. `_fips`
covers counties only, and getting Chicago vs suburban Cook wrong would mis-state
the largest county in the state. That is a crosswalk project, not a parser, and
it deserves its own pass.

**Montana — one GET, but no provenance.**
`https://tableau-ext.mt.gov/t/SOS/views/AbsenteeBallots/AbsenteeDash.csv` returns
200 `text/csv`, 5,518 bytes, live right now: `County,Measure Names,Measure
Values` over 56 counties plus `All`, with Ballots Sent / Ballots Received.
Montana has a Senate race in 2026. **Rejected because the CSV carries no
election identifier and no as-of date, and the dashboard holds the LAST
election's numbers between cycles** — today it is serving the June 2026 primary
(514,152 sent / 268,125 received). A run in September would stamp primary
absentee totals with today's date under the 2026 general. Only the PDF export
carries the provenance ("2026 Montana Primary Election…", "Compiled On
6/15/2026"), and this pipeline has no PDF dependency. Buildable the moment
either a PDF parser exists or the CSV grows a date column.

**Idaho, Minnesota, South Dakota, Kansas, North Dakota** — each has a real
during-season signal and a disqualifying practicality: Idaho's data is served
from `datawrapper.dwcdn.net` under **cycle-specific chart IDs** that must be
rescraped every election; Minnesota's daily county table (87 counties,
applications and accepted ballots, verified live through August 2026) is
**HTML behind Radware bot protection** that 302s both curl and WebFetch to
`validate.perfdrive.com`, so it needs a headless browser rather than a file
fetch — a genuine loss, since Minnesota has the other open Senate seat; South
Dakota and Kansas are **statewide only**, no county dimension at all; North
Dakota's `https://vip.sos.nd.gov/abev.aspx?eid=329` is real but is an HTML table
needing 53 ASP.NET postbacks for county detail, has an opaque election id, has
only one election currently linked, and North Dakota has neither a Senate nor a
governor's race in 2026 and no voter registration at all.

---

## Rejected outright, with the reason

**Louisiana — worth spelling out, because it looks like a top-tier source and is
not.** `electionstatistics.sos.la.gov` publishes early-voting statistics by
parish with party, race, sex *and* age — the richest breakdown any state offers.
But the files are posted **after** each election, one per election, never daily:

```
2024_1105_ParishStats.pdf   election 11/05/2024   created 11/12/2024
2026_0627_ParishStats.xls   election 06/27/2026   created 07/04/2026
```

Every 2024 file is PDF; XLS only starts appearing in 2026. Verified by walking
the year dropdown for 2024 and 2022. A file created seven days *after* the
election is useless to a tracker whose product is the curve before it. Real URL
pattern:
`https://electionstatistics.sos.la.gov/Data/Early_Voting_Statistics/parish/{YYYY}_{MMDD}_ParishStats.{xls,pdf}`.

**New Mexico.** Consequential — open governorship, Senate race — and the SoS
*used* to publish daily turnout updates, but as press-release prose in 2018-2019,
and the practice stopped. `electionstats.sos.nm.gov` (found via nmvote.gov) is a
historical results database covering 2000-2025, not a during-season feed; voter
data is behind a paper request form. Checked `sos.nm.gov`, its WordPress search
API, `nmvote.gov` and `electionstats`.

**Oregon — a daily county × party × day file, in PDF.** Governor's race *and* a
Senate race, and the report is genuinely excellent: statewide topline, returns by
county, by county by day, and by county by party across twelve parties. It is a
Power BI PDF export, deleted after each election (every candidate URL 404s right
now; all verified 200 in the Wayback), and **the filename convention changes
every cycle** — `G22-Daily-Ballot-Returns.pdf`, `P22-…`,
`november-2025-…`, `May-19-2026-…`, across two different directories. The
Socrata dataset `data.oregon.gov/resource/rxzj-n3di.json` is not a substitute:
statewide-only and loaded after the election. Revisit if a PDF dependency is
ever added; Oregon would be the highest-value PDF in the country.

**Hawaii — daily, dated, and PDF.**
`https://elections.hawaii.gov/wp-content/uploads/AbsenteeReconDP-01-20260717.pdf`
(`01` = City and County of Honolulu) is a genuinely daily per-county report on a
clean dated URL. PDF only. Same verdict as Oregon.

**Alaska — house-district keyed, as suspected.** Open governorship and a Senate
race, and the Combined Ballot Count Report is published near-daily (verified 200:
`…/doc/info/Combined%20Ballot%20Count%20Report_11.1.2022.pdf`) with an excellent
method split. It is keyed by the 40 state **House Districts**, which do not nest
into boroughs or census areas. There is no mapping to county FIPS, so the
`_fips`-keyed county contract cannot be met. Rejected on geography, not quality.

**Massachusetts — municipality-keyed, and no during-season feed since 2018.**
Senate race and a governor's race. The post-election workbooks are excellent
(`2024-State-Election-Ballot-Statistics.xlsx`, 135,736 B) but are keyed by the
351 cities and towns; every town lies wholly within one county, so a crosswalk is
a clean many-to-one rollup, but it is a table this repo does not have. The only
during-season artifacts were the 2016 and 2018 early-voting map XML feeds
(`https://www.sec.state.ma.us/ele/ele18/early-voting_18/EV-stats-18.xml`, 200,
94,936 B), both dead live with no 2020/2022/2024 equivalent.
`sec.state.ma.us` also blocks automation with Incapsula (403 to curl).

**New York — nothing at state level.** `elections.ny.gov` sits behind a
Cloudflare interstitial (403 to curl and WebFetch alike). A browser-read of the
full 4,218-URL Drupal sitemap found no early-voting or mail-ballot turnout report
of any kind; a Wayback CDX sweep of 12,850 early/absentee/turnout URLs found zero
CSV/XLSX/JSON turnout files, ever. `data.ny.gov`'s 14 election datasets are all
campaign finance. The only during-season signal is NYC BOE's HTML page of daily
early-vote check-ins by borough (5 boroughs = 5 county FIPS), overwritten each
election.

**New Jersey — Senate race, nothing during the season.** Zero XLSX/CSV election
statistics anywhere under `nj.gov/state/elections` (Wayback CDX of 16,150 rows
returns only census and pollworker-reimbursement spreadsheets). The per-county
"Periodic Election Reporting" PDFs do carry early-voting and VBM issued/received
— but the **first file every cycle is election night** (2024: `1105`; 2026:
`1103`). Registration by county × party is monthly PDF.

**California — nothing until E+2.** Open governorship, largest electorate in the
country, and the SoS publishes nothing on ballots returned while the vote-by-mail
window is open. The familiar daily California trackers are built from the
commercial VoteCal extract and are not official. What exists: the Report of
Registration (a denominator, XLSX, ~5 drops per cycle,
`https://elections.cdn.sos.ca.gov/ror/15day-gen-2024/county.xlsx`, 200), and the
Unprocessed Ballots Report — county-level with a real method split, updated most
weekdays, **but starting two days after the election** and HTML-only.

**Utah.** In 2020 the Lt. Governor ran a daily county table at
`https://vote.utah.gov/ballots-processed/`; it now 404s and the 2022 archived
snapshot still shows stale 2020 content — it was never revived. The county ×
method workbook `Master-Aggregated-Numbers-2023-2026.xlsx` (200, 172 KB) is
excellent and post-canvass only.

**Alabama, Arkansas.** Both publish election data only after the election.
Alabama's "Elections Data Downloads" page is precinct results and
total-ballots-cast files back to 1992; nothing absentee, nothing during the
season. Alabama's open 2026 Senate seat makes this a real loss, but there is no
file to scrape.

**Nebraska, Wyoming.** Nebraska publishes voter registration as **PDF only** (244
PDFs, 0 CSV, 0 XLSX on its statistics page) and has never published early-vote
numbers as data. Wyoming has no public file, but does have a
**request-gated daily one**: `DailyAbsenteeFileRequestForm.pdf` (200, stamped
"Created 4/2026") promises individual-level daily uploads to a Google Drive
folder, party-tagged and address-keyed, **September 18 – November 2, 2026**, with
no fee stated. That is one email to `Elections@wyo.gov` away from being the best
file in the region.

**Missouri, Indiana, Connecticut, Rhode Island, Delaware, Vermont, West
Virginia, DC.** Nothing machine-readable during the season. Missouri publishes
nothing about absentee volume in any format at any time. Indiana's
`statewideTurnout_A.json` (200, verified) is post-election only. Connecticut's
`data.ct.gov` has zero early-voting datasets — and note the trap that an
unscoped Socrata catalog search surfaces "Absentee Voting in 2026" rows that are
**federated from Ramsey County MN, Fulton County GA and data.pa.gov**, not
Connecticut. Rhode Island's historical turnout CSVs now 404 and `vote.sos.ri.gov`
403s. Delaware's best artifact is a post-election PDF of counts by voting method.
Vermont has no early/absentee data file of any kind. West Virginia publishes
monthly registration totals; its `apps.wv.gov/SOS/BulkData` is business-entity
data behind a login, and its absentee-ballot endpoint is a per-voter lookup.
DC publishes monthly registration PDFs by ward and its early-voting app is a
vote-centre locator with no turnout endpoint.

**Mississippi — honestly unverified.** `sos.ms.gov` returns HTTP 403 to
automated requests regardless of headers. Mississippi has no in-person early
voting and no statewide absentee reporting, so the expected answer is "nothing",
but this row is unconfirmed from this network rather than checked.

---

## Two things worth carrying forward

1. **Almost none of these states offers a URL you can construct and forget.**
   Maryland changes its folder code every cycle; Kentucky, Tennessee, South
   Carolina and Oregon change the filename; Idaho changes the whole hosting
   platform. Every adapter here scrapes an index page *and* falls back to
   verified literal patterns, and every one of them treats "not found" as
   `NotYetPublished` rather than as an error. That is the shape of the work.

2. **A gap in `normalize.py`, reported not fixed** (per the ownership rules):
   `party()` does not recognise Maryland's `WCP` (Working Class Party, in the
   2022 and 2026 files) or `NLM` (No Labels Maryland, in the 2024 file). Both are
   state-recognised parties that appear in real data, so raising `SchemaDrift` on
   them would leave Maryland permanently broken. `md.py` therefore consults a
   documented two-entry table **only after** `normalize.party()` returns None, and
   still raises `SchemaDrift` for anything in neither. The right long-term fix is
   two lines in `_PARTY_MAP` alongside the North Carolina block; the owner of
   `normalize.py` should make that call. Separately, `AGE_BANDS` genuinely cannot
   express Maryland's 25-44 / 45-64 bands — that is not a bug, but it is why
   Maryland publishes no age dimension.

---

# Second pass, 2026-09-06 — re-testing the bot-blocked hosts, and five states built

Everything above stands except where this section says otherwise. The trigger
for the re-test is the change described in `docs/ohio-source.md`: `_net.get` now
retries any 403 through `curl_cffi` with a real Chrome TLS/HTTP2 fingerprint.
Several verdicts above were reached because a host answered 403 or served a bot
interstitial, and those verdicts were reached about our HTTP client rather than
about the state.

**Every status code below was observed live from this machine on 2026-09-06.**

## The blocked hosts, re-tested

| Host | plain `requests` | `curl_cffi` chrome | Verdict now |
|---|---|---|---|
| `www.sos.mn.gov` | **302 → validate.perfdrive.com**, 200 / 15,096 b of Radware challenge | **200 / 65,748 b, the real page** | **UNBLOCKED — built, `mn.py`** |
| `sos.ms.gov` | 403 / 370 b | **200 / 145,760 b** | **UNBLOCKED.** Nothing to scrape behind it (see below) |
| `www.sec.state.ma.us` | 200 / **212 b** (Incapsula stub) | **200 / 51,646 b, the real page** | **UNBLOCKED.** Verdict above unchanged: post-election only |
| `elections.ny.gov` | 403 / 5,709 b | **200 / 56,142 b** | **UNBLOCKED.** Verdict above unchanged: no turnout data exists |
| `www.voteinfo.net` (Riverside CA) | 403 | **200 / 139,590 b** (intermittently 403) | **UNBLOCKED**, probabilistically |
| `www.sos.wa.gov/elections/.../ballot-status-reports` | 403 | **403** | Still walled — but irrelevant, see Washington below |
| `www.nvsos.gov` | 200 / **925 b** Incapsula wall | 200 / **926 b**, same wall | **Still blocked.** `nv.py`'s SourceError is correct |
| `vote.sos.ri.gov` | 403 | **403** Cloudflare challenge | Still blocked (`elections.ri.gov` is 200 and has nothing) |
| `azsos.gov` | 403 | **403** on this run | Intermittent; `docs/ohio-source.md` saw 200. Score, not rule |

Impersonation profiles tried against the two hosts that still refuse:
`chrome`, `chrome120`, `chrome124`, `chrome131`, `chrome133a`, `chrome136`,
`safari184`, `safari18_0`, `firefox135`, `edge101`. All 403.

Two traps worth recording, because both cost real time:

* **Radware and Incapsula inject their own scripts into the GOOD page too.** The
  genuine 65,748-byte Minnesota page contains `validate.perfdrive.com` at byte
  3,691 and `SSJSConnectorObj` at byte 2,933; the genuine Massachusetts page
  contains `_Incapsula_Resource`. Detecting the challenge by domain name rejects
  every successful fetch. `mn.py` matches on `Radware Captcha Page` and
  `captcha.perfdrive.com`, which appear only on the challenge itself.
* **Radware challenges the FIRST request from a new session and cookies it.**
  Request A on a fresh `curl_cffi` session returned the 15,096-byte challenge and
  request B, identical in every other way, returned the real page. The retry has
  to REUSE the session; building a fresh one each time is challenged forever.

## Built this pass — five adapters

| State | Adapter | Geography | Dims | Why |
|---|---|---|---|---|
| **MN** | `mn.py` (`mn-sos`) | statewide + **87 counties** | method (2026 layout), applications | **Open US Senate seat** |
| **AK** | `ak.py` (`ak-doe`) | statewide only | method (mail / in-person) | **Open governorship + Senate** |
| **WA** | `wa.py` (`wa-sos`) | statewide + **39 counties** | method, **sex**, full daily curve | Best file in the country |
| **CA** | `ca.py` (`ca-sos`) | statewide + **58 counties** | method (return channel), issued | Largest electorate, open governorship |
| **NY** | `ny.py` (`ny-nycboe`) | **5 of 62 counties — PARTIAL** | in-person check-ins | ~40% of the state's electorate |

### Minnesota — `mn.py`, and the verdict above is now wrong

`https://www.sos.mn.gov/election-administration-campaigns/data-maps/absentee-data/`
is not "HTML behind Radware needing a headless browser". It needs a TLS
fingerprint, which `curl_cffi` supplies, and the page is a plain table:

```
State Primary Absentee Counts                (heading names the election)
Statewide Counts
  Applications (8/11/26 at 3 p.m.): 445,023
  Accepted ballots - by mail (8/11/26 at 3 p.m.): 116,414
  Accepted ballots - in person (8/11/26 at 3 p.m.): 133,364
Counts by County   <caption>Absentee Counts by County as of August 11, 2026 …</caption>
  Aitkin  5,889  2,164  292     … 87 rows, all 87 resolve through _fips
```

**The county table has no dependable header row** — it has none at all in the
live 2026 page or the 2024-10-03 capture, and does have one in the 2022 capture
and the 2024-11-09 one. So the columns are identified by PROOF: each column's
sum across the 87 counties must equal its statewide bullet exactly. It does, on
all three fixtures. A table that does not reproduce Minnesota's own statewide
figures raises `SchemaDrift`.

Two layouts, both handled: 2022 and 2024 published two columns (applications,
accepted) with no method split; the 2026 primary publishes three. Today the page
carries the PRIMARY, so `mn-sos` correctly returns `NotYetPublished`.

Archived generals, both verified 200 and both in `tests/fixtures/mn/`:
`web.archive.org/web/20241003181752id_/…` and `…/20221014040640id_/…`.

### Alaska — `ak.py`. The geography verdict stands; there is a second file

The verdict above — house-district keyed, cannot meet the county contract — is
correct and unchanged. What it missed is that the Division publishes the same
measurement **statewide, in HTML**, on each election's own results page:

```
https://www.elections.alaska.gov/election-results/e/?id=24genr    200, 109,271 b
https://www.elections.alaska.gov/election-results/e/?id=22genr    200, 110,823 b
https://www.elections.alaska.gov/election-results/e/?id=26prim    200, 100,868 b
https://www.elections.alaska.gov/election-results/e/?id=26genr    200,  86,431 b — no statistics block yet
```

carrying `<dt>24GENR Totals</dt>`, Alaska's own two totals, and an eight-row
`Ballot Type | Number Issued | Number Received` table (By Fax / By Mail / Early
Vote / Federal Write-In / In-Person (Absentee) / Online Delivery / Questioned /
Special Needs). The rows sum to the totals exactly, which is the drift check.
Statewide only, so `county_rows` is always empty — as `sd.py` does for South
Dakota. Questioned ballots are in the total and in neither method bucket.

Transport notes worth keeping:

* `elections.alaska.gov` was never blocked; plain `requests` is enough.
* The near-daily 2022 artefact, `/doc/info/Combined%20Ballot%20Count%20Report_11.1.2022.pdf`
  (200, 58,995 b), is a 2022-only practice — there is no 2024 or 2026 file under
  `/doc/info/`, and its text layer runs the three numeric columns together
  (`FAX 110` could be 1/1/0 or 11/0), so it is not safely parseable anyway.
* In 2024 the equivalent moved to
  `/results/24GENR/20241204_Combined-Ballot-Count-Report.html`, which answers
  **405 "Human Verification" to every client including a full browser
  fingerprint**. PDFs under the same directory return 200, so the wall is on
  HTML only.
* **Unverified:** whether the Election Statistics block is refreshed DAILY during
  the early-vote window. Every capture we can see is post-election and the
  Wayback Machine has no `?id=24genr` capture before 2024-12-07. Flagged in
  `ak.py`'s docstring. If it turns out to be post-count only, Alaska stays
  `NotYetPublished` through the season, which is honest rather than wrong.
* A known consequence of tracking Alaska is recorded in `tests/test_estimate.py`
  as `KNOWN_BASELINE_GAPS`: `data/baseline/county_results_2024.csv` predates the
  2019 split of Valdez-Cordova (02261) into Chugach (02063) and Copper River
  (02066). It cannot bias anything, because `ak.py` publishes no county rows.

### Washington — `wa.py`, built as recommended

`https://www.sos.wa.gov/sites/default/files/current_election/Statewide{YYYY-MM-DD}.zip`
is confirmed real and daily. **The landing page is still 403 to everything, and
that does not matter**: the `/sites/default/files/` path is not challenged and
answers an honest 404 today from both clients, because `current_election/` is
purged between cycles.

Verified downloads and parses: `Statewide2024-10-22.zip` (18,139,811 b →
515,318 ballots, 38 of 39 counties), `Statewide2024-10-23.zip` (25,065,276 b →
39 of 39), `Statewide2025-10-24.zip` (14,370,190 b → 342,572 ballots, 38 of 39).
Every day from 2024-10-22 to 2024-11-19 is in the Wayback Machine at 200.

Decisions worth carrying:

* **`Party` is empty on every row of every file** — Washington has no party
  enrolment. All four party fields are None. If that column ever fills in, the
  adapter raises `SchemaDrift` rather than guessing.
* **Coverage varies day to day**: 2024-10-22 is missing Grant County, 2025-10-24
  is missing Okanogan, 2024-10-23 has all 39. So the statewide row is gated on
  all 39 counties being in the FILE, exactly as `tx.py` gates Texas. Coverage is
  a property of the file, not of the truncated span — every ballot carries its
  own `Received Date`, so a county in the file has all of its returns in it.
* `ballots_total` is every ballot RETURNED (2024-10-22: 393,692 Accepted /
  117,364 Received / 4,262 Rejected). Rejected ballots were returned and many
  are cured later.
* Drop Box, Email, Fax and both Non-Standard channels are `mail`; only
  `In Person` is `inperson`. Washington mails every voter a ballot, so
  `mail_requested` is meaningless here and stays None.
* Data rows carry **21 fields against a 20-column header** — a real trailing
  empty field, harmless to `DictReader`, and preserved in the fixtures.

### California — `ca.py`. **The verdict above is wrong.**

"The SoS publishes nothing on ballots returned while the vote-by-mail window is
open" is not correct. It publishes this, on a constructible URL:

```
https://elections.cdn.sos.ca.gov/statewide-elections/{cycle}-general/vbm-statistics.{xlsx,xlsm,xls,pdf}
```

Live today: `2022-general/vbm-statistics.xlsm` → **200, 77,507 b, real xlsm**
(`Last-Modified: Mon, 16 Jun 2025 22:51:49 GMT`). Every `2026-general/*` and
`2026-primary/*` path → **403 AccessDenied** (S3 with listing denied, exactly
like Tennessee's bucket), which is absence, not a block.

That it is a DURING-season file is not inferred. The Wayback Machine holds
captures taken before Election Day, and the counts grow between them:

```
2022-general/vbm-statistics.xlsm   2022-10-27 (452,327 b)   2022-11-05 (456,693 b)
2022-general/vbm-statistics.pdf    2022-10-27, 11-02, 11-04 …
2024-general/vbm-statistics.pdf    2024-10-20
2024-general/vbm-statistics.xls    2024-11-01 (129,536 b)
2024-primary/vbm-statistics.pdf    2024-02-24 … 2024-03-02   (primary was 03-05)
2025-special/vbm-statistics.pdf    2025-10-25, 10-27, 10-28  (special was 11-04)

Alameda, ballots returned:  60,892 on 2022-10-27  ->  166,600 on 2022-11-05
Statewide, ballots returned: 1,642,945 on 2022-10-27 -> 5,230,699 final
```

The sheet to read is **"VBM Press Version"**, the only one present in every
version (the during-season workbooks have eight sheets, the final has one).
Header, identical across all of them:

```
COUNTY | County Type | REGISTRATION (15-day ROR) 2020 |
Total voters Issued VBM ballots | Drop Box | Drop Off Location |
Vote Center Drop Off | Mail | FAX | Other | Sum |
Total Accepted VBM ballots | Total VBM Ballots in Review * |
Accepted % of Voter-returned Ballots
```

58 counties plus California's own `Total` row. `Sum` is exactly the six return
channels added up, which is the per-county drift check. `ballots_total` is `Sum`
(returned), not `Total Accepted` — accepted lags returns by each county's
signature-review queue, whose size is printed in the next column.

Two live risks, both handled rather than hidden:

* **The as-of date is the CDN's `Last-Modified`.** The workbook says its numbers
  are "as of a specific date and time" without giving one, and the URL carries
  no date. `ca.py` therefore has its own transport (`_net.get` discards headers)
  and refuses a stamp outside the cycle's window — which is what keeps the live
  2022 file, rewritten 2025-06-16, from being republished as this season's.
* **The extension churns.** 2022 shipped `.xlsm`, 2024 shipped `.xls`. The 2024
  file is legacy OLE2 (`d0 cf 11 e0`), which `openpyxl` cannot open and which
  needs `xlrd` — not a dependency, and `pyproject.toml` is not this module's to
  change. `.xlsx` and `.xlsm` are tried in order; a cycle that ships only `.xls`
  or only `.pdf` raises SourceError naming exactly that and California falls
  through to the aggregator, which is where it is today anyway. **Adding `xlrd`,
  or a PDF route, would make California safe against both.**

The county-level plan in the brief was checked and is not needed: no California
county publishes a during-season return file at a constructible URL today (there
is no election in progress), and a Wayback sweep of `lavote.gov`, `ocvote.gov`,
`sdvote.com`, `sccvote.sccgov.org`, `acvote.org`, `elections.saccounty.gov`,
`voteinfo.net`, `sfelections.sfgov.org` and `contracostavote.gov` for dated
CSV/XLSX return files found **one** hit, a 2021 recall SOV workbook from Santa
Clara. Orange County's `ocvote.gov/datacentral` is a live registration dashboard
(200, 38,606 b) whose ballots tab 400s off-season. The statewide file above is
better than any of them and needs no partial-coverage labelling.

### New York — `ny.py`, PARTIAL and marked as such

`elections.ny.gov` is now reachable (403 → **200, 56,142 b**), and reading it
changes nothing: its 4,218-URL sitemap carries enrolment statistics, absentee
DEADLINE press releases and election law. There is no turnout report. The
verdict above is re-confirmed with access rather than assumed without it.

The NYC Board's page is reachable with no fingerprint at all:
`https://www.vote.nyc/page/early-voting-check-ins` → **200, 73,496 b**, plain
`requests`. It carries one election at a time under an `<h2>` that names it
("November General Election 2022", "General Election 2024", "Primary Election
2026" today), then one block per day:

```
October 26, 2024 - Day 1
  Manhattan - 38,237   Bronx - 16,462   Brooklyn - 40,289
  Queens - 31,671      Staten Island - 13,486
  *Unofficial as of Close of Polls 140,145
```

The five boroughs sum to the Board's own figure on every day of every fixture,
which is the drift check; the series is cumulative from Day 2, so one fetch
rebuilds the curve. **Five of New York's sixty-two counties, so `ny.py` emits
county rows and never a `StateDay`** — the rule `tx.py` established. In-person
check-ins only: `mail_returned` is None because the city's absentee ballots are
counted by the counties and are not on this page.

Archived generals, both verified 200 and both in `tests/fixtures/ny/`:
`web.archive.org/web/20241102072441id_/…` and `…/20221103025647id_/…`.

## Re-tested and still rejected

**Mississippi — no longer "unverified".** `sos.ms.gov` answers 200 to a browser
fingerprint (145,760 b root, 168,555 b `/elections-voting`). The elections
section links one absentee page, `/yall-vote/absentee-voting-information`, which
is instructional. Mississippi has no in-person early voting and no statewide
absentee reporting, so the expected answer was "nothing" and that is now the
CONFIRMED answer rather than an assumption.

**Massachusetts — reachable, and still post-election only.** With access, the
Election Data and Statistics Hub (200, 55,347 b) lists exactly what the verdict
above predicted: `2018-State-Election-Early-and-Absentee-Statistics.xlsx`,
`2020-State-Election-Early-and-Vote-by-Mail-Statistics.xlsx`,
`2022-State-Election-Early-and-Vote-by-Mail-Statistics.xlsx`,
`2024-State-Election-Ballot-Statistics.xlsx` and their primary equivalents. All
post-canvass. No during-season artefact, and none since the 2018 XML feed.

**Nevada — still genuinely blocked.** `www.nvsos.gov` answers a 925-byte
Incapsula wall to plain `requests` and a 926-byte one to `curl_cffi` chrome.
`nv.py`'s SourceError is the correct reading and the fingerprint retry does not
help. Nevada reaches the site through the aggregator tier.

**Rhode Island.** `vote.sos.ri.gov` is still a Cloudflare challenge to both
clients. `elections.ri.gov` is 200 and carries nothing during the season.

## Two things worth carrying forward from this pass

1. **"403" and "blocked" are different claims, and so are "302 to a bot manager"
   and "blocked".** Of the nine hosts re-tested here, five were reachable with a
   browser fingerprint and two of those (Minnesota, California) were carrying a
   usable during-season file the whole time. Any future "we could not look"
   verdict should name the client it was reached with.

2. **The four states whose `notes` in `data/meta/states.csv` still read "not
   tracked in 2026" — AK, CA, MN, NY, WA — are now tracked.** That column is
   published to `output/ev_state_meta.csv` and the site reads it. The
   `has_party_reg` flags there are already right (AK/CA/NY true, MN/WA false).
   Left for that file's owner rather than edited here.

---

# Midwest & Plains pass — IN, MO, KS, NE, ND

Investigated 2026-09-06. Every status code below was observed live, from this
machine, on that date. Nothing here is a guessed URL.

**Built: 2 (KS, ND). Rejected with evidence: IN, NE — and both rejections are
firmer than the earlier rows they replace.**

Two of this batch's rows in the summary table above were wrong, and both were
wrong in the direction of underestimating the source:

| State | Old verdict | Corrected |
|---|---|---|
| **KS** | "partial — Power BI model, statewide only … rejected: no county" | Statewide-only is right; rejecting it was not. South Dakota and Alaska are already tracked statewide-only, and Kansas's model is a **cumulative daily series with a mail/in-person split** that one request reconstructs end to end. **BUILT** `ks-sos`. |
| **ND** | "partial — one HTML page, ASP.NET postback per county … rejected: cadence + stakes" | The postbacks are **stateless** — 53 of them replay one token pair with no session — and the county numbers **reconcile to the state's own totals to the ballot**. Also: the survey's `eid=329` is the 2024 *primary*. The 2026 **general is 348**, and it is already live. **BUILT** `nd-sos`. |

---

## Kansas — `src/ev/adapters/ks.py` (`ks-sos`)

**The Ohio Power BI recipe transfers whole.** `docs/ohio-source.md` Part 4 was
followed step for step and Kansas answered at every one, on a plain
`requests` call with no browser fingerprint needed:

| step | endpoint | result |
|---|---|---|
| 1 | `https://sos.ks.gov/elections/advance-voting-data.html` | **200**, 31,891 b, `text/html` — carries one `app.powerbigov.us/view?r=…` iframe |
| 2 | that token, base64-decoded | `{"k":"de3b6e3c-b94e-419a-8d9c-9435eba72780","t":"dcae8101-…"}` |
| 3 | `https://app.powerbigov.us/view?r=<token>` | **200**, 29,106 b — `FixedClusterUri: https://wabi-us-gov-virginia-redirect.analysis.usgovcloudapi.net/` |
| 4 | `…-redirect…/public/reports/<key>/modelsAndExploration` | **403**, 0 b |
| 4′ | `…-api…/public/reports/<key>/modelsAndExploration?preferReadOnlySession=true` + `X-PowerBI-ResourceKey` | **200**, 82,782 b |
| 5 | `…-api…/public/reports/querydata?synchronous=true` (POST) | **200**, 2,598 b, 16 rows |
| — | `…-api…/public/reports/<key>/conceptualschema` (POST) | **200**, 7,256 b — the whole model, three tables |

The `-redirect` → `-api` substitution is not folklore: the embed page ships its
own `getAPIMUrl()` (strip `-redirect`, strip `global-`, append `-api`), and
`ks.api_host()` implements exactly that rather than a hand-rolled replace.

**What the model holds, from `conceptualschema` — this is the whole of it:**

```
ENTITY: ADVANCE VOTE COUNTS 2026
    DATE
    ADVANCE VOTING BALLOTS SENT
    ADVANCE VOTING BALLOTS RETURNED
    IN PERSON ADVANCE
    Percentage of Total Ballots Returned   (measure)
    Total Number of Ballots Voted          (measure)
```

No county. No precinct. No party. No age. Two date tables Power BI generates for
itself. So `county_rows` is empty and all four `party_*` fields are None —
noting that Kansas **does** register by party (its own
`vr-statistics/<yyyy>/<mm>-<yyyy>-Voter-Registration-Numbers-by-County.xlsx`
series is county × party, verified 200), so this blank means "this source does
not report it", not "the state has none".

Judgement calls, all locked by tests:

1. **The table is named for the CYCLE, not the election, and today it holds the
   AUGUST PRIMARY.** Sixteen rows, 2026-07-15 → 2026-08-04, ending at 40,446
   mail returned + 161,785 in person. Publishing that as the general's advance
   vote is the Montana failure with a different mechanism. Every row is
   therefore gated on `[Election Day − 22, Election Day]`, and a table with
   nothing in that window raises `NotYetPublished` — which is what
   `python -m ev probe --state KS` returns today, naming the span it did find.
   Kansas's window is statutory (K.S.A. 25-1122, 20 days), and the primary
   series confirms it to the day: 2026-08-04 − 20 = 2026-07-15.
2. **`ballots_total` is Kansas's own definition, proved rather than assumed.**
   The report's headline card is a measure called `Total Number of Ballots
   Voted`; queried, it returns **202,231**, which is exactly the final row's
   `RETURNED + IN PERSON` (40,446 + 161,785). It is a whole-table aggregate — it
   returns 202,231 against *every* date — so it is not read per row; the
   arithmetic it proves is what the adapter computes. `Percentage of Total
   Ballots Returned` = 0.63184 = 40,446 / 64,013 confirms `SENT` is the mail
   denominator.
3. **`ballots_new` stays None.** The series is cumulative and skips weekends
   (no 2026-07-18 or -19 row), so a difference between snapshots would book
   three days of ballots on Monday. Same call as `tx.py`.
4. **A blank component blanks the total.** Kansas writes a real `0` when it
   means one — in-person advance on day one of the primary is `0`, not empty —
   so a missing cell would be a model change, and half a total is not a total.
5. **The chain is discovered, not hardcoded.** The entity name carries the cycle
   (`ADVANCE VOTE COUNTS 2026`), so it is guaranteed to change; the adapter
   scrapes page → token → cluster → model/report/dataset/entity, with each step
   falling back to the literal verified above. If two tables ever carry the four
   columns, each is queried and the election window decides.

**No archive.** The Wayback CDX index has no capture of
`sos.ks.gov/elections/advance-voting-data.html` before **2026-08-05**, and all
17 captures since carry one digest — the page was created for this cycle, and
the model holds exactly one table. `fetch_history` says so rather than
inventing a route. Kansas's only archived artefact is post-election:
`https://sos.ks.gov/elections/22elec/2022-General-Election-Turnout-Information.xlsx`
(**200**, 13,851 b), county-level but carrying only `ADVANCE BALLOTS RETURNED BY
MAIL` and `TOTAL BALLOTS CAST` — no in-person advance, no series, and no 2024
equivalent is published (`…/24elec/2024-General-Election-Turnout-Information.xlsx`
returns the CMS's 200-with-HTML shell, 30,815 b).

Two other Power BI reports were found and are not early-vote sources:
`b2e8009d-04b7-4a41-8771-895d6fba9014` on the voter-registration-statistics page
(a registration denominator) and `fdbdbdb1-07ce-4840-afaf-72b930f2f8d6` on
election-results.

## North Dakota — `src/ev/adapters/nd.py` (`nd-sos`)

`https://vip.sos.nd.gov/abev.aspx?eid=<id>` — a WebForms page with a statewide
Category/Value table and a county panel behind a dropdown. The earlier
rejection ("53 ASP.NET postbacks … opaque election id … only one election
currently linked") described it accurately and drew the wrong conclusion.

**The postbacks are stateless.** One `__VIEWSTATE` / `__EVENTVALIDATION` pair
scraped from the GET validates for every county, with no cookies and no session.
A 53-county harvest is one GET plus 53 POSTs, about 34 s sequentially. Verified
with plain `requests` — no `curl_cffi` needed anywhere on this host.

**The county rows reconcile to the state's own totals exactly.** Full harvests:

```
2024 general (eid 333):  Σcounty = 95,908 sent / 91,556 returned / 99,007 early
                         page    = 95,908       / 91,556         / 99,007
2022 general (eid 326):  Σcounty = 76,034       / 70,064         / 36,513   (page identical)
```

and North Dakota's own `Total Ballots Cast prior to Election Day` is
`returned + early` in both cycles (91,556 + 99,007 = 190,563; 70,064 + 36,513 =
106,577), which is what `ballots_total` uses.

**The election id was the survey's real error.** `eid` is a plain GET parameter
with no dropdown, and the page renders *any* integer, zeroed, rather than
refusing — so a wrong id fails silently. Verified by reading
`candidatelist.aspx?eid=<id>`'s own `lblFormHeader`:

```
326 -> "2022 General Election Contest/Candidate List"        200, 119,251 b
333 -> "2024 General Election Contest/Candidate List"        200,  81,979 b
346 -> "2026 Primary Election Contest/Candidate List"        200, 311,689 b   <- NOT this one
348 -> "2026 General Election Contest/Candidate List"        200, 115,542 b   <- the target
349 -> 302 (does not exist)                                  166 b
```

The 2026 general is **already live**: `abev.aspx?eid=348` returns 200 (22,857 b)
with 63 absentee/VBM ballots sent, 0 returned, and all 53 counties in its
dropdown. The only `abev` link on `sos.nd.gov` still points at the 2026
*primary* (346) — two ids away — so the adapter never trusts a pinned id: it
verifies the id names that cycle's general and otherwise walks upward, and
refuses rather than guessing.

Judgement calls:

- **An absent early-vote block is not automatically a zero.** North Dakota
  counties choose whether to run early voting, and the county panel simply omits
  `lblEarlyVotes` for one that does not — 46 of 53 counties in the 2024 general.
  The seven that do report sum to 99,007 against a statewide 99,007, which
  proves the silent 46 are zero. That proof is **re-run on every fetch**: if the
  reporting counties add up, the silent ones publish a real `0`; if they do not,
  they publish None and so does their `ballots_total`.
- **All-zero means not yet.** The portal renders a zeroed page for an election it
  has not begun loading, which is indistinguishable from one where nothing has
  happened; either way there is nothing to plot, so a statewide row of zeros
  raises `NotYetPublished` before any county POST is made.
- **The page carries no date.** Grepping the abev captures for `as of` /
  `last updat` / `refreshed` finds nothing. The `Export to Excel` button does
  produce a real workbook (**200**, 2,513 b,
  `Content-Disposition: attachment; filename=ABEV.xlsx`, 17 rows) whose cell A1
  is stamped — and that stamp is useless twice over, verified both ways against
  `eid=333`, the 2024 general:

  ```
  no county selected  ->  "...(as of 9/6/2026 11:42:28 AM)"   Total Absentee Ballots Sent 49,210
  Cass selected       ->  "...(as of 9/6/2026 11:42:29 AM)"   Total Absentee Ballots Sent 49,210
  ```

  One second apart, tracking the request clock rather than the data (which
  settled in November 2024) — and the Cass-selected export carries the STATEWIDE
  49,210 rather than Cass's 12,358, so it ignores the county selection
  entirely. Rows are therefore stamped with the run's own date, and a backfill
  with Election Day.
- **`fetch_history` returns a final, not a curve.** An archived `eid` still
  serves county detail, but it serves the settled numbers. (Wayback does hold
  dated captures of `abev.aspx` through October 2022 and October 2024 — a real
  curve for whoever wants to build it.)
- **No party fields, ever.** North Dakota has no voter registration at all.
- `CountyDay` has no `mail_requested` column, so the county panel's `Ballots
  Sent` is read, range-checked and dropped; only the statewide figure is
  published.

**No JSON anywhere.** `abev.aspx/GetData` and `abev.asmx` were tried and answer
the portal's error page or a 401; the page's `PageRequestManager` initialises
with empty UpdatePanel arrays, so there are no async postbacks to intercept.

## Indiana — rejected: everything is post-election, confirmed

The earlier row ("`statewideTurnout_A.json` (200, verified) is post-election
only") is right, and this pass makes it firmer rather than changing it.

Indiana **does** collect what we want and publishes it — afterwards, once per
election. `https://www.in.gov/sos/elections/voter-information/files/2026-Primary-Registration-and-Turnout-Data.xlsx`
(**200**, 15,615 b, real xlsx) is county × `Registered Voters | Total Voters |
Turnout | Election Day | Absentee | Absentee %` for all 92 counties — a genuine
absentee split, dated after the canvass. The same page
(`.../register-to-vote/voter-registration-and-turnout-statistics`, **200**,
43,262 b) carries one such file per election back to 1990, xlsx since 2023 and
PDF before.

Nothing during the season, from any direction:

- `https://www.in.gov/sos/elections/` (**200**, 39,317 b) and
  `.../statistics-and-maps` (**200**, 37,612 b) link no absentee-count file of
  any kind; every data link is a district map PDF or a voter-count PDF.
- `.../voter-information/ways-to-vote/absentee-voting` (**200**, 64,806 b) is
  instructions and application forms only.
- **No dashboard exists.** `in.gov/sos/elections/*`, `.../voter-information/`,
  `.../election-results/` and `https://indianavoters.in.gov/` (**200**,
  149,490 b) were each grepped for `app.powerbi*`, `public.tableau.com`,
  `datawrapper` and `arcgis.com`: **zero hits on all four pages.**
- `https://enr.indianavoters.in.gov/` (**200**, 16,688 b) is an Angular
  election-night app on Azure Blob storage; its asset paths answer Azure's own
  `OutOfRangeInput` (**400**, 226 b) or `ResourceNotFound` (**404**, 223 b)
  rather than serving, and it carries results, not absentee counts.
- `https://hub.mph.in.gov/api/3/action/package_search?q=voter&rows=50` — **200**,
  214 b, `"count": 0`. Indiana's open-data portal has no voter datasets at all.
- A Wayback CDX sweep of **13,677 unique `www.in.gov/sos/elections/*` URLs**
  found exactly **four** machine-readable files whose name mentions absentee,
  and all four are administrative rosters, not counts:
  `2014_Absentee_Central_Count_Counties-1.xls` (200, 7,289 b),
  `Absentee_Central_Count_Counties.xls` (200, 6,937 b) and two copies of a
  county absentee **email address list** (200, 12,939 b).

**Verdict: nothing machine-readable during the season, at any point in the
archive.** Indiana is a documented "no", not an unexplored one. The 2022/2024
turnout workbooks are worth having as `ev_state_meta.csv` finals, which is that
file's owner's call.

## Nebraska — rejected this pass, but the target is now precise

The state half of the earlier row is confirmed and the "never as data" half is
wrong — though not in a way that is buildable today.

- **The SoS publishes nothing during the season, and it is not a 403.**
  `https://sos.nebraska.gov/elections/voter-registration-statistics` (**200**,
  116,267 b) is PDF-only registration — county × party, monthly. Party
  registration confirmed from `…/vrstats/2026VR/Statewide-September-2026.pdf`
  (**200**, 595,214 b, processed 09/01/2026): R 619,984 / D 327,935 /
  Nonpartisan 282,473 of 1,258,669. So `has_party_reg` for NE is true — but no
  source breaks the early vote down by it.
- `https://electionresults.nebraska.gov/resultsCSV.aspx?text=All&type=SW&map=CTY`
  (**200**, 3,931 b, `Media.csv`) is a real live CSV API — of **contest
  results**, with no vote-mode column, and dark between elections.
- **Douglas County (Omaha) is the prize, and it is empty today.**
  `https://www.votedouglascounty-ne.gov/earlyvotinglist/index.aspx` (**200**,
  27,073 b) says in its own words: *"The Douglas County Election Commission
  provides a list each night of the next working day's early voting ballots that
  will be sent and early voting ballots that have been voted and returned."*
  Douglas is ~25 % of Nebraska's vote and is NE-02.

  Why it is not built: **the files are purged after each election, so there is no
  sample to fixture and no format to verify.** The page's own commented-out
  markup carries one real 2026 filename —
  `/earlyvotinglist/2026_Primary_Election_Requests_Enetered_Party_List_3-27-2026.txt`
  (sic, "Enetered") — and it returns **404, 1,245 b** today, as does a 2024
  general return-list name. No `.txt` href is present on the live page at all;
  the visible list is empty and the template in the comments is the only
  evidence of the shape. Writing a voter-level aggregator against a format
  nobody in this repo has seen, keyed to filenames nobody can confirm, is
  exactly the speculative build the quality bar rules out.

  **Revisit from 2026-09-28**, which is when Douglas begins mailing. The right
  shape is a `ny.py`-style PARTIAL — county rows for Douglas (31055) and never a
  statewide row — that scrapes the index page for `.txt` hrefs rather than
  constructing them. Lancaster and Sarpy publish nothing during the season
  (`lancaster.ne.gov/331/EarlyAbsentee-Ballot` **200**, 128,802 b;
  `sarpy.gov/861/Early-Voting` **200**, 108,904 b — both instructional), so a
  Nebraska partial is one county of 93.

## Missouri — rejected: nothing, anywhere, ever, and there is a statute saying so

The earlier row ("Missouri publishes nothing about absentee volume in any format
at any time") is confirmed, and the reason is now on the record.

- `https://www.sos.mo.gov/elections/results` — **200**, 40,489 b. Counted its
  own links: **83 `.pdf`, 3 `.aspx`, zero CSV / XLSX / JSON.** The official
  post-election turnout report
  (`…/ElectionResultsStatistics/Nov2024OfficialVoterTurnout.pdf`, **200**,
  134,488 b) is `County | Registered | Active | Inactive | Actual Voters |
  Turnout %` — **no absentee or early column even after the fact.**
- **No dashboard to attach the Kansas/Ohio recipe to.** Every fetched
  `sos.mo.gov` page was grepped for `app.powerbi*`, `public.tableau`,
  `datawrapper`, `arcgis.com` and `<iframe`: **zero matches.**
- `https://www.sos.mo.gov/cmsimages/countyinfo.json` — **200**, 48,821 b,
  `application/json` — is the only JSON the elections section serves: 117 rows
  of local-election-authority contacts (`COUNTY_NAME, CLERK, STREET_ADDRESS,
  EMAIL, WORK_PHONE, WEBSITE…`). A roster, not counts — but the right starting
  point if anyone ever builds the county-by-county version.
- `data.mo.gov` has no elections data: the Socrata catalog scoped to the domain
  (`api.us.socrata.com/api/catalog/v1?domains=data.mo.gov&only=dataset&limit=400`,
  **200**) returns **263 datasets, 0** matching `elect|vote|ballot|absentee`.
  Note the same federation trap Connecticut had — an *unscoped* `q=absentee`
  search returns King County WA, NYC, Oregon and Edmonton rows.
- **Wayback: never.** CDX over `sos.mo.gov/elections*` (3,600 rows) and
  `sos.mo.gov/CMSImages/Election*` (2,363 rows) finds no absentee CSV/XLSX/JSON
  in any year, and a domain-wide `filter=mimetype:text/csv` returns **0 rows** —
  the Internet Archive has never captured a single CSV from `sos.mo.gov`. The
  one promising filename, `Absentee-MailinBallotSummaries.pdf` (~247 KB,
  captured ~20× across Sept–Nov 2020, now **404**), is a voter-facing
  eligibility explainer, not counts.
- **The statutory reason.** RSMo **115.157.4** (revisor.mo.gov, **200**,
  39,699 b) makes the statewide absentee-applicant file available only to "a
  candidate, a duly authorized representative of a campaign committee, or a
  political party committee", for a fee, and says verbatim: *"Nothing in this
  section shall require such voter information to be released to the public over
  the internet."* 115.157.2 has election authorities forward voter history "not
  more than three months after the election". The pipe is post-hoc by design.
- **Independent corroboration.** UF's own 2024 file
  (`election.lab.ufl.edu/data-downloads/earlyvote/2024/US.csv`, **200**,
  15,539 b) carries Missouri as
  `279,918 requested / 303,843 returned`, `data_source = "St. Louis, St.
  Charles, Greene, Jefferson, and Jasper Counties"` — the best-resourced
  aggregator in the country hand-assembles Missouri from five county clerks
  because there is no state file. The 2026 file (**200**, 11,353 b) has MO at
  `data_source = TBD`, all zeros, `last_update 8/12/2026`.

**One correction that outlives this row: Missouri now records party
affiliation.** RSMo **115.155** (**200**, 43,045 b), effective 2022-08-28 via
H.B. 1878, puts on the registration form: *"Political Party Affiliation
(OPTIONAL: You shall be unaffiliated unless you designate an affiliation.)"*,
and 115.157.1(20) makes it a required field of the statewide system. So the
common shorthand "Missouri does not register by party" is no longer legally
accurate. In practice it is close to useless — the field is optional with
unaffiliated as the default, primaries stay open under RSMo 115.397 so there is
no incentive to fill it in, and the SoS publishes no party-breakdown statistics
at all. Flagging it for the owner of `data/meta/states.csv` rather than editing
that file: `has_party_reg` for MO is now a judgement call, not a fact.

---

## Carried forward from this pass

1. **"Statewide only" is not a rejection reason, and it should stop being used
   as one.** Kansas was passed over for having no county dimension while South
   Dakota and Alaska — equally statewide-only — were built. What actually
   matters is whether the source carries a *series*: Kansas's does, and one
   request rebuilds the whole curve.

2. **A dashboard embed is a lead, not a wall.** Kansas took about twenty minutes
   from "an `app.powerbigov.us` iframe the survey called a black box" to a
   working query API, entirely by following `docs/ohio-source.md` Part 4. The
   generalisable steps: the token on the page decodes to `{"k": resourceKey}`;
   the embed page names its cluster AND ships the `getAPIMUrl()` function that
   converts it to the API host; `modelsAndExploration` hands over model, report,
   dataset and every table and column; `conceptualschema` proves what is *not*
   in the model, which is how "there is no county column" became a fact instead
   of an assumption. **Oklahoma's DevExpress dashboard is the remaining one of
   this shape and should get the same treatment.**

3. **Re-check every "opaque id" rejection.** North Dakota's `eid` was called
   opaque and it is — but `candidatelist.aspx?eid=<id>` names the election in
   English, which turns an opaque id into a *verifiable* one. The survey's
   `eid=329` was the 2024 primary, and the 2026 general (348) has been live and
   unlinked the whole time. Where a state has an id nobody can read, look for a
   sibling page that reads it.

4. **Two live traps of the same shape, in two different states.** Kansas's
   dashboard table is named for the CYCLE and currently holds the AUGUST
   PRIMARY; North Dakota's 2026 primary and general are `eid` 346 and 348. Both
   would publish a primary's turnout as the general's, confidently, and neither
   is detectable from the data alone. Every adapter in this batch therefore
   pins the election by something the source itself says — a date window against
   the statutory advance period, or an election name in words — and refuses
   rather than guessing. This is now the third state (after Montana and
   Maryland) where the primary/general confusion was the single largest risk in
   the build.

5. **The blank-vs-zero call got a new wrinkle worth naming: a zero that the
   source proves by arithmetic.** North Dakota omits a county's early-vote block
   entirely when the county runs no early voting, which is normally "not
   reported". But the page's own statewide early-vote total equals the sum of
   the counties that *do* print one, which proves the silent ones are zero. The
   adapter re-runs that proof on every fetch and only writes `0` when it holds —
   blank otherwise. An inference that the source itself can be made to confirm,
   every time, is a different thing from an assumption.

---

# Pass 4 — New England and the Mid-Atlantic: MA, CT, RI, VT, NJ, DE, DC

Seven jurisdictions, all of which register voters by party and four of which run
elections by TOWN rather than by county. **Built: 2** (CT, DE). **Rejected: 5**
(MA, NJ, DC, RI, VT), each with the exact status of every endpoint below.

Every URL in this section was fetched from this machine on 2026-09-06 and the
status recorded is the status observed. Nothing here is constructed and unchecked;
where a URL is a pattern that could not be exercised, it says so.

**The headline is uncomfortable and worth saying first:** the two states this
pass was told to prioritise, **Massachusetts and New Jersey**, publish nothing
machine-readable during the season, and that verdict survived a much harder look
than the original survey gave them. What turned up instead was **Connecticut**,
which the survey had listed as "no" — and which in fact publishes a
**per-ballot** file, by town, with party, refreshed on most business days --
the richest New England source in this repo after Maine's.

---

## Summary

| State | During-season machine-readable? | Format | Unit | Dims | Outcome |
|---|---|---|---|---|---|
| **CT** | **yes** — SOTS per-ballot early-voting + absentee workbooks | XLSX | **town** (169) | town, party, method, day | **BUILT** `ct-sots` |
| **DE** | **yes** — DOE "Voter Counts by Voting Method" | PDF | county (3) | county, method | **BUILT** `de-doe` |
| MA | no — post-election only, and no during-season feed since 2018 | — | municipality (351) | — | rejected |
| NJ | no — first per-county file every cycle is election night | PDF | county (21) | — | rejected |
| DC | no — monthly registration PDFs; the only API is authenticated | — | — | — | rejected |
| RI | no — post-election Count Books; the SoS site is Cloudflare-gated | PDF | — | — | rejected |
| VT | no — post-election canvass; the portal's report API is a form, not a feed | — | — | — | rejected |

---

## Built

### Connecticut — `src/ev/adapters/ct.py` (`ct-sots`)

**The find of this pass, and it was invisible from the navigation.** The
Secretary of the State publishes two workbooks with **one row per ballot** —
`early_voting_<date>.xlsx` and `absentee_ballot_<date>.xlsx` — on a page that is
not linked from the Elections landing page, the Statistics and Data page or the
Early Voting page. It is reachable only at a per-cycle slug:

```
https://portal.ct.gov/sots/election-services/2026-voter-data     200   27,056 B  real page
https://portal.ct.gov/sots/election-services/2024-voter-data     200   22,076 B  <title>404 Error Page</title>
https://portal.ct.gov/sots/election-services/2022-voter-data     200   22,076 B  <title>404 Error Page</title>
```

**portal.ct.gov never sends a 404.** A missing page and a missing file both come
back HTTP 200 with a Sitecore error shell titled `404 Error Page`, which the
adapter has to detect by title — and which is why a missing *index* is read as
`SourceError` ("we could not look") while a missing *file* is read as absence.

**Verified files** (all 200; XLSX unless noted):

```
.../2026_absentee_ballot_data/early_voting_09032026.xlsx        81,797 B  real xlsx
.../2026_absentee_ballot_data/absentee_ballot_09032026.xlsx     47,911 B  real xlsx
.../2026_absentee_ballot_data/early_voting_08252026.xlsx        31,924 B  first early file
.../2026_absentee_ballot_data/absentee_ballot_08142026.xlsx     24,508 B  first absentee file
.../2026_absentee_ballot_data/early_voting_09042026.xlsx        21,880 B  soft-404 HTML
.../2024_absentee_ballot_data/early_voting_11052024.xlsx        21,880 B  soft-404 HTML
.../2022_absentee_ballot_data/absentee_ballot_11072022.xlsx     21,880 B  soft-404 HTML
```

A day-by-day sweep of **2026-07-20 through 2026-09-06** (49 days x both
filename kinds, 98 requests) returns **22 real workbooks, dated 2026-08-14 to 2026-09-03**, with
no file on 08/15, 08/16, 08/18, 08/20–08/23 or 09/02. Nothing before 08/14 —
the August 11 statewide primary's files have been **deleted**. The same sweep
over `2024_absentee_ballot_data/` (2024-10-18..2024-11-08) and
`2025_absentee_ballot_data/` (2025-10-15..2025-11-10) returns **zero** files.
Connecticut keeps the current election's data and nothing else, so
`fetch_history` refuses by design rather than guessing at a URL.

The header, verbatim, 33 columns, identical in both workbooks:

```
VOTER ID | TID | FIRST NAME | MIDDLE NAME | LAST  NAME | SUFFX | AD NUM | AD UNIT |
RESIDENCE ADDRESS | RESIDENCE ADDRESS CITY | ZIP5 | ZIP4 | ST | ROUT | MAL NM |
MAIL UNT | MAIL ADDRESS | MAIL ADDRESS2 | MAIL CITY | MS | MAIL ZIP | MRTE |
MAIL COUNTRY | YOB | DST | PR | PARTY | SERIAL | DT MAILED | DT RETURNED |
TM RETURN | RETURN_TYPE | ISSUE_TYPE
```

Because every ballot carries `DT MAILED` and `DT RETURNED`, **one download
rebuilds the whole daily curve**, exactly like North Carolina's and Maine's
files: a missed run costs nothing. `RESIDENCE ADDRESS CITY` is the town of
registration, so Connecticut is published as **town rows keyed by the 10-digit
census cousub GEOID**, with the county rows summed out of digits 3–5 of that key
— the Maine pattern, and exact rather than a crosswalk.

Judgement calls, all locked by tests:

1. **The filename convention is not stable, so the index page is scraped.**
   Inside the 2026 cycle alone Connecticut has used two: the Wayback capture of
   the Voter Data page from **2026-08-01** links `abs_detail_073126.xlsx`
   (MMDDYY) while the same page today links `absentee_ballot_09032026.xlsx`
   (MMDDYYYY). Both are parsed and both are in the constructed fallback, but the
   page is the entry point. (The archived `abs_detail_073126.xlsx` itself is
   **404 in the Wayback Machine** — only the link survives.)
2. **Which election a file belongs to is checked twice.** The folder is shared
   by every election in a cycle. The filename's date must fall inside the
   general's own window (45 days before Election Day through 20 after) *and*
   ≥75% of the ballots' own dates must fall within 60 days of it. The live
   fixture — the September 1 special primary, ballots dated 08/14–09/01 — fails
   both, which is why today's probe says `pending` for Connecticut rather than
   publishing a special primary in Enfield as statewide general-election early
   voting. That case is a test.
3. **The two files ARE the method split**, so no method label is ever
   interpreted. `RETURN_TYPE` is read for one word, `Void`; everything else it
   can say (`Mail`, `Drop Box 3`, `In Person By Voter`,
   `In Person by Designee or Family`, `Supervised`, and blank for a ballot still
   out) is ignored, and an unfamiliar value is therefore **not** drift. A voided
   ballot is dropped from every count, issued and returned alike.
4. **The absentee file counts ballots ISSUED**, with `DT RETURNED` blank until
   they come back, which gives Connecticut a real `mail_requested` as well as
   `mail_returned`. On 2026-09-03 in Enfield: 36 issued, 30 back, 28 early votes.
5. **The series ends at the OLDER of the two files' dates.** Publishing the
   newer file's extra days would freeze the other method and render as a day on
   which nobody voted by mail. Likewise, before early voting opens there is no
   early-voting file at all, and `inperson` is then `None` — not `0`.
6. **A blank `PARTY` cell suppresses the party columns entirely.** Four party
   numbers that quietly add up to well under the total are worse than no party
   breakdown; below 95% enrolment coverage the adapter publishes `None` for all
   four and says why in the log.

### Delaware — `src/ev/adapters/de.py` (`de-doe`)

Delaware publishes one PDF per election at an election-stamped URL and
**overwrites it in place while voting is happening**:

```
.../reports/pdfs/GE2024_GeneralElectionVoterCountsByVotingMethod.pdf   200   97,330 B  application/pdf
.../reports/pdfs/PR2024_VoterCountsByVotingMethod.pdf                  200   96,161 B  (primary)
.../reports/pdfs/PR2026_PrimaryElectionVoterCountsByVotingMethod.pdf   200  198,768 B  (primary, LIVE today)
.../reports/pdfs/GE2026_GeneralElectionVoterCountsByVotingMethod.pdf   404              <- NotYetPublished today
.../reports/pdfs/GE2022_GeneralElectionVoterCountsByVotingMethod.pdf   404
.../reports/pdfs/GE2022_VoterCountsByVotingMethod.pdf                  404
.../reports/pdfs/PR2022_VoterCountsByVotingMethod.pdf                  404
https://elections.delaware.gov/index.html                              200  440,566 B
```

(host: `https://elections.delaware.gov/voter/registrationtotals/`)

That the file is genuinely refreshed *during* the window is not an assumption:
the Wayback CDX index lists **14 captures of the 2024 general's report, 6 with
distinct digests**, and running the finished adapter against them recovers a
real six-day curve:

```
2024-10-28   86,907    absentee 29,926   early  56,981
2024-10-29  112,206    absentee 30,891   early  81,315
2024-11-01  187,338    absentee 34,207   early 153,131
2024-11-03  229,492    absentee 35,990   early 193,502
2024-11-04  245,920    absentee 36,403   early 209,517
2024-11-05  247,172    absentee 37,656   early 209,516   (+ 179,005 polling place)
```

Judgement calls:

- **`ballots_total` is absentee + early voting, NOT Delaware's own `Total`.**
  Their Total is every ballot cast; on the final report it is 426,177 against
  247,172 of early and absentee voting. This is the one place in the repo where
  "publish the source's own total" is the wrong rule, because the source's total
  answers a different question. Both directions of Delaware's arithmetic are
  still checked (each row sums across counties, each column sums across methods)
  and any mismatch is `SchemaDrift`.
- **The election is pinned by the report's own words**, not by a date window:
  the PDF says `2024 General Election`, and a report that does not name the
  cycle's general is `NotYetPublished`. The primary's file sits beside it under
  an almost identical name and is live right now showing 16,559 primary ballots
  — publishing those as general-election early voting is exactly the Montana /
  Kansas / North Dakota trap this repo keeps meeting.
- **The polling-place row is simply absent until Election Day** and absence is
  published as absence.
- **The report carries its own as-of stamp** ("Monday, October 28, 2024,
  8:54:15 AM") and rows are dated by it, never by the date of the run: Delaware
  refreshes on business mornings, so a weekend run legitimately re-reads
  Friday's report and dating it "today" would invent a flat day.
- **No party breakdown.** Delaware registers by party; this report does not
  split it, so every `party_*` field is blank rather than zero.
- **`fetch_history` reads the Internet Archive**, because Delaware keeps no
  dated copies of its own and there is nothing else to read. It runs only in
  `backfill`. 2022 is refused outright: no report exists under any name.

---

## Rejected, with the exact status of every endpoint

### Massachusetts — reachable now, and still publishes nothing during the season

The previous survey's reason ("`sec.state.ma.us` blocks automation with
Incapsula, 403 to curl") is **obsolete**: with `_net`'s browser-fingerprint retry
the whole site answers.

```
https://www.sec.state.ma.us/                                                     200   36,292 B
https://www.sec.state.ma.us/divisions/elections/elections-and-voting.htm         200   51,646 B
https://www.sec.state.ma.us/divisions/elections/research-and-statistics/statistics-hub.htm      200   55,213 B
https://www.sec.state.ma.us/divisions/elections/research-and-statistics/early-voting-statistics.htm  200   42,833 B
https://www.sec.state.ma.us/divisions/elections/research-and-statistics/hub-content/2024-State-Election-Ballot-Statistics.xlsx                 200  135,736 B
https://www.sec.state.ma.us/divisions/elections/research-and-statistics/hub-content/2022-State-Election-Early-and-Vote-by-Mail-Statistics.xlsx 200  112,809 B
https://www.sec.state.ma.us/divisions/elections/research-and-statistics/hub-content/2026-state-primary.xlsx                                    200  151,594 B  (registration, not turnout)
https://www.sec.state.ma.us/divisions/elections/research-and-statistics/hub-content/2026-State-Election-Ballot-Statistics.xlsx                 404
https://www.sec.state.ma.us/ele/ele18/early-voting_18/EV-stats-18.xml    200 but 302s to elections-and-voting.htm — dead
```

So the block is gone and the answer did not change. The Election Data &
Statistics Hub says of itself that registration statistics are published "after
the deadline for registration" and turnout statistics are "detailed statistical
information on voter turnout by municipality and method of voting" — both
post-election. The separate **Early & Mail Voting Statistics** page, which is in
the sitemap but linked from nowhere, carries three percentages per election
(2024 State Election: mail 34.2%, early 17.0%, Election Day 48.8%, 3,512,866
ballots) and is written after the canvass.

Two exhaustive checks, so this is not "we did not find it":

* **Wayback CDX, prefix sweeps.** `sec.state.ma.us/ele/` → 2,807 URLs, of which
  **12** are `.xml/.csv/.xlsx/.xls/.json/.txt`; the newest during-season one is
  `ele18/early-voting_18/EV-stats-18.xml` (2018). `sec.state.ma.us/divisions/elections/`
  → 1,404 URLs, **14** data files, every one of them post-election.
* **`sitemap.xml`** (200, 346,868 B): 1,782 URLs, 392 of them election-related,
  and not one is a during-season data page.

Massachusetts remains the biggest gap in the country by electorate: 351
municipalities, a Senate race, a governor's race, an excellent post-election
municipality × method workbook, and no file to scrape while it matters.

### New Jersey — the prior survey was right, and here is the proof

```
https://www.nj.gov/state/elections/election-information.shtml            200   42,296 B
https://www.nj.gov/state/elections/vote-by-mail.shtml                    200   81,970 B
https://www.nj.gov/state/elections/vote-early-voting.shtml               200  114,886 B
https://www.nj.gov/state/elections/election-results-information.shtml    200  174,540 B  (2026 Periodic Election Reporting)
https://www.nj.gov/state/elections/election-results-information-2024.shtml  200
https://www.nj.gov/state/elections/election-information-svrs.shtml       200  176,204 B
https://www.njelections.org/                                             200 -> nj.gov/state/elections (Incapsula)
```

The Periodic Election Reporting pages are the only per-county series, and the
dates settle it. **2026: the first file for every one of the 21 counties is
`2026-1103-…-periodic-report-election-night.pdf`**, then `1104` through `1117`.
2024: `2024-1105-…-election-night.pdf` then `1106`–`1121`. The 2026 page already
has all fifteen dates linked, pre-built, none of them before Election Day. There
is no such thing as a New Jersey VBM file published while vote-by-mail is
happening.

`election-information-svrs.shtml` carries only registration: three PDFs a month
(by county, by congressional district, by legislative district), plus an
Election-Day snapshot. The Socrata catalog for `data.nj.gov` returns **0**
results for "election".

The task brief's premise that "its Division of Elections publishes VBM data" is
true only of the post-election periodic reports, which do carry VBM issued and
received — a week too late to be a tracker.

### Washington DC — a single jurisdiction, and no turnout endpoint

```
https://www.dcboe.org/                                                       200   51,659 B
https://www.dcboe.org/data,-maps,-forms/voter-registration-statistics        200   75,820 B
https://www.dcboe.org/elections/2026-elections                                200   63,072 B
https://www.dcboe.org/open-government,-reports,-foia                          200   42,740 B
https://www.dcboe.org/Voters/Absentee-Voting/Early-Voting                     404
https://earlyvoting.dcboe.org/                                                200    3,154 B  (React SPA)
https://earlyvoting.dcboe.org/api/ev_center                                   401  "Authorization has been denied for this request."
https://earlyvoting.dcboe.org/api/ev_client                                   401  same
https://earlyvoting.dcboe.org/api/states                                      500
https://earlyvoting.dcboe.org/api/VoteCenters                                 404
https://electionresults.dcboe.org/                                            200    4,811 B  (React SPA, election-night only)
```

The Data, Maps & Forms page is a monthly `Data-Statistics-Report-<M>_<YYYY>.pdf`
series — registration by ward and party, back to 2024, current through
2026-07-31. Nothing about ballots cast. A Wayback CDX sweep of `dcboe.org`
(12,943 URLs) finds **14** `.xls/.xlsx` files, all of them 2008 certified
results, and three "turnout" hits, all 2004 PDFs. `opendata.dc.gov`'s search API
returns three DCBOE datasets — Early Vote Center, Election Day Vote Center, Mail
Ballot Drop Boxes — which are **locations, not counts**.

The early-voting app is a poll-worker admin tool: its `/api/` is real but every
data route is `401`. The brief's premise that DCBOE "publishes daily early-vote
counts during the window" could not be confirmed anywhere machine-readable.

### Rhode Island — and a live `_net.py` finding, reported not fixed

```
https://elections.ri.gov/                                    200   82,673 B   (Board of Elections)
https://elections.ri.gov/elections/publications              200  213,677 B
https://vote.sos.ri.gov/                                     403 / 200        <- see below
https://www.sos.ri.gov/divisions/elections                   200 -> vote.sos.ri.gov
https://datahub.sos.ri.gov/RegisteredVoter.aspx              200    8,285 B
https://ri-voter-turnout-tracker-ridos.hub.arcgis.com/       200   38,014 B
  .../api/feed/dcat-us/1.1.json                              200      266 B   "dataset": []
  .../data.json                                              200      266 B   "dataset": []
http://api.us.socrata.com/api/catalog/v1?domains=data.ri.gov 404             (no such Socrata domain)
```

The Board of Elections' Publications page is **Count Books**, biennial
post-election PDFs from 1950 to 2018. The ArcGIS "Voter Turnout Tracker" hub
site publishes an **empty** DCAT catalogue, and an ArcGIS Online search finds
only historical feature services (`2020 Rhode Island Voter Turnout_WFL1`,
`Voter Turnout 50/75_WFL1`, owner `kdunham_agol`) plus two StoryMaps. No
during-season feed.

**Worth passing to whoever owns `_net.py`:** `vote.sos.ri.gov` is Cloudflare-
gated and the outcome depends on *which browser* curl_cffi impersonates, from
the same machine in the same second:

```
impersonate="chrome"     403   5,939 B   "Just a moment..."
impersonate="chrome131"  403   5,939 B   "Just a moment..."
impersonate="edge"       403   5,960 B   "Just a moment..."
impersonate="safari"     200  41,415 B   the real page
impersonate="firefox"    200  41,415 B   the real page
```

`_net.IMPERSONATE` is hard-coded to `"chrome"`. Nothing in this pass depends on
it — Rhode Island has no file worth reaching — but a *second* impersonation
attempt, or a per-call override, would be a cheap way to convert this class of
403 into an honest answer, exactly as the curl_cffi retry already did for Ohio
and Arizona. That is a `_net.py` change and `_net.py` is not this agent's file.

### Vermont — an all-mail general with no public counts

```
https://sos.vermont.gov/elections                                          200   32,005 B
https://sos.vermont.gov/elections/election-info-resources/elections-results-data/   200   61,786 B
https://sos.vermont.gov/elections/voters/early-absentee-voting              200   42,871 B
https://electionresults.vermont.gov/                                        200    1,223 B  (Angular, election night)
https://vote.vermont.gov/public/dashboard                                   200   70,477 B  (Angular)
https://api.vote.vermont.gov/api                                            404
POST https://api.vote.vermont.gov/api/Report/GetPublicReport                400
     {"errors":{"Code":["The Code field is required."],"Type":[...],
                "Criteria":[...],"Description":[...],"RequestedBy":[...]}}
```

Vermont mails a ballot to every active voter by October 1 of each even year, so
it *has* the data; it publishes only after the canvass. The Elections Results &
Data page carries `2024_general_election_voter_turnout.pdf`,
`2026-august-primary-voter-turnout-report-by-party.pdf` and winner-listing
`.xlsx` files — all post-election. The Voter Portal's `Report/GetPublicReport`
is a **form**, not a feed: it is a POST that requires `Code`, `Type`, `Criteria`,
`Description` and `RequestedBy`, is invoked in the bundle through a
recaptcha-header path (`postSecurity`), and returns a blob. `data.vermont.gov`'s
Socrata catalogue returns 5 results for "election", none of them electoral.

---

## Three things this pass adds to the two carried forward above

1. **A "no" in an earlier survey is a claim about a search, not about a state.**
   Connecticut was listed as publishing nothing. It publishes per-ballot files
   with party and town — on a page linked from no navigation anywhere on
   `portal.ct.gov`, findable only by searching the open web for the phrase a
   newspaper used. Every state whose rejection reads "we looked at the elections
   pages and found nothing" deserves one search engine query against the
   filename patterns before it is believed.

2. **A 200 is not a success and a 403 is not a refusal.** `portal.ct.gov`
   answers every missing page and every missing file with HTTP 200 and a
   Sitecore shell titled `404 Error Page`, so Connecticut's adapter has to read a
   `<title>` to tell "not posted yet" (stop the ladder) from "the index moved"
   (fall through). In the other direction, `vote.sos.ri.gov` returns 403 to a
   Chrome fingerprint and 200 to a Safari one. Both of these are the same bug
   class the CO/MI investigation found, one level up: the status code is not the
   answer.

3. **The primary/general trap has now bitten in five states, and New England
   makes it worse.** Maryland, Montana, Kansas and North Dakota each hide a
   primary behind the general's URL. Connecticut goes further: every election in
   a cycle — including a single-town special primary — shares one folder and one
   filename convention, and only the current one's files exist. There is no
   election identifier anywhere in the file. The only defence is the ballots'
   own dates, which is why `ct.py` checks the filename's date *and* the ballot
   dates and refuses on either. Delaware, by contrast, prints
   `2024 General Election` inside the PDF, which is the cheapest and strongest
   provenance any source in this repo offers. **A source that names its own
   election in words should be preferred over one that does not, all else equal.**

### A `normalize.py` gap, reported not fixed (per the ownership rules)

`party()` maps `"independent"` to `PARTY_NPA`. That is right in most of the
country and **wrong in Connecticut**, where the *Independent Party* is a
state-recognised party with its own ballot line and unaffiliated voters are
called "Unaffiliated" — the Secretary of the State's own Minor Party Key (page 8
of `.../2025/registration-and-enrollment/nov25re.pdf`, 200, 174,325 B) lists
Independent, Independence, Green, Libertarian, Working Families, Bottom Line,
Concerned Citizens, We The People, Reform and Open as registered minor parties.
`ct.py` therefore consults a documented `PARTY_ALIASES` table **before**
`normalize.party()` rather than after, which is the opposite of what `md.py`
does, and it is the only adapter that needs to. Anything in neither table still
raises `SchemaDrift`. The owner of `normalize.py` should decide whether
`"independent"` deserves a per-state override rather than a global bucket.

---

# The South: LA, OK, AL, MS, AR, WV

Investigated 2026-09-06. **Every status code below was observed live from this
machine on that date.** Two states were built (`la-sos`, `ok-seb`); four
publish nothing a daily job can use, and the reasons differ enough to be worth
writing down. Two rows in the summary table at the top of this file are
superseded: **Louisiana** ("no — early-vote stats are post-election only") and
**Oklahoma** ("rejected: fragility"), and Mississippi's `unverified` row is now
verified.

## Built

### Louisiana — `src/ev/adapters/la.py` (`la-sos`)

**Why:** a US Senate race in 2026, and the only parish-level number Louisiana
publishes while people are voting.

The earlier verdict was right about the headline product and wrong about the
state. Re-walked the year dropdown on
`https://electionstatistics.sos.la.gov/default.aspx?Stats=Early_Voting_Statistics&Type=Parish`
(200) for 2022, 2024 and 2026: the **Early Voting Statistical Report** really is
posted only after the election, every time.

```
2024_1105_ParishStats.pdf   election 11/05/2024   created 11/12/2024
2022_1108_ParishStats.pdf   election 11/08/2022   created 11/16/2022
2026_0627_ParishStats.xls   election 06/27/2026   created 07/04/2026
```

But the same host publishes a **second, daily** artefact that the earlier survey
did not reach, because it is behind an ASP.NET postback rather than a link:
`https://electionstatistics.sos.la.gov/EarlyVoterList.aspx` (200), the
**Absentee by Mail and Early Voters** roster, one PDF per parish per day at

```
https://electionstatistics.sos.la.gov/Data/Absentee_Voter_List/{YYYYMMDD}_{PARISH}_{Daily_MMDD|PreEV|Cumulative}.pdf
```

**Verified 200 + real PDF:** `20241105_EBTR_Daily_1018.pdf` (783,801 b, 179 pp),
`…_1019`, `…_1021` … `…_1104`, `20241105_EBTR_PreEV.pdf` (701,736 b),
`20241105_ACAD_PreEV.pdf` (154,084 b), `20241105_ACAD_Daily_1102.pdf` (78,625 b).
**Verified 404** (1,245 b): `…_Daily_1020`, `…_1027`, `…_1103`, `…_1105` — the
Sundays Louisiana does not early-vote, and the day after the election.

Each file is a ward-and-precinct roster of names ending in a single
`Total Voters: N` trailer on its last page, which is the only line the adapter
reads. It carries **no party, no method, no race, no sex** — every one of those
fields comes out `None`, in a state that registers by party.

Judgement calls, all locked by tests:

- **The dailies are increments; the curve is a running sum.** Validated against
  Louisiana's own published figures: East Baton Rouge's 2024 general sums to
  **94,928** against the post-election report's **94,908** (0.02%), and the whole
  state rebuilds to **970,313** by 11/04 against **975,019**. The residue is
  Election Day mail and registrar corrections — which the SoS's own page warns
  about in as many words.
- **`fetch_history` lets the report REPLACE Election Day** rather than add to it,
  and marks that row `restated = 1`. That is where Louisiana's party, race, sex
  and in-person/absentee split come from: 2024 statewide 849,796 in-person +
  125,223 absentee; DEM 351,863 / REP 434,871; white 682,487 / black 248,757 /
  other 43,775.
- **DEM and REP are published; the residual is not.** The report collapses
  everything that is neither into one `OTH` column, mixing no-party voters with
  minor parties. Same call as `ky.py`, for the same reason.
- **A missing trailer means zero, not drift.** `20241105_ACAD_Daily_1102.pdf` is
  one banner page with no voters and no total. The report banner is what
  separates "nobody voted" from "this is not the file we think it is".
- **`Cumulative` is never counted** — it restates the dailies and is 8 MB per
  parish.
- **`.xls` is unreadable.** The 2026 files are real OLE2/BIFF (body starts
  `D0 CF 11 E0`), not an HTML table with an `.xls` name; `openpyxl` cannot read
  them and `xlrd` is not a dependency. Only the PDF form is parsed, which is what
  2022 and 2024 are anyway.
- **It is the most expensive adapter in the repo.** A full 2024 window rebuild
  measured **1,026 files, 168 MB, 150 seconds**. Every day before `as_of` is
  served from `cache/`, because a dated roster never changes.
- **The host's WAF bites, twice over — and this is the one thing to watch.**
  It answers with a ~400-byte "Access Denied / Reference #18.…" page and HTTP
  403, in two distinct modes:
  1. *Flapping* — the same URL 403ing to one client and 200ing to the next in
     the same second, while the 1,026-file rebuild itself ran clean.
  2. *A rate ban* — shortly after that rebuild (1,026 requests in 150 s, ≈7/s)
     **every URL on the host, including its root, began answering 403 from this
     IP**, to plain `requests` and to a full Chrome TLS fingerprint alike. It is
     per-IP and volume-triggered, not a fingerprint rule. It cleared on its
     own after roughly half an hour.

  `la.py` therefore throttles itself to `_MIN_INTERVAL = 0.35 s` (≈6 minutes for
  a full late-October run, under 3 req/s) and retries a 403 three times with
  backoff. **That interval is UNVERIFIED against the ban** — it was chosen after
  the ban was already in force — and it is flagged as such in the module. A wall
  it cannot get past raises `SourceError`, so the ladder falls through to the
  aggregator rather than recording "Louisiana has nothing"; whoever runs the
  first live October ingest should watch for a host-wide 403 and raise the
  interval if one appears.

Not used, and why: `voterportal.sos.la.gov/Graphical` (200) is live *results*, not
turnout; `www.sos.la.gov/robots.txt` is `Disallow: /` for `*`, which is why
nothing on that host is fetched beyond the one page that names the iframe.

### Oklahoma — `src/ev/adapters/ok.py` (`ok-seb`)

**The earlier finding was wrong, and the correction is one word: GET.** This file
recorded that `DashboardItemGetAction` "answers HTTP 500 to every reconstructed
query shape … it needs the DevExpress client's internal state blob". There is no
state blob. Captured from the live page and replayed from plain `requests`:

```
GET https://stats.okelections.gov/dashboardControl/data/DashboardItemGetAction
    ?dashboardId=AbsStatsByCounty
    &itemId=pivotDashboardItem1
    &query={"Filter":[{"dimensions":[{"@ItemType":"Dimension",
             "@DataMember":"ElectionDate","@DefaultId":"DataItem0",
             "@DateTimeGroupInterval":"DayMonthYear","@SortOrder":"Descending"}],
             "values":[["2024-11-05T00:00:00.000"]]}]}
-> 200, application/json, 13,247 b
```

No cookie, no token, no session, and `stats.okelections.gov` is behind no bot
protection at all (plain `requests` and `curl_cffi` return byte-identical
answers, 200/265,653 b at the root). The 500s came from POSTing. Also verified
200: `/dashboardControl/dashboards` (1,226 b, 18 dashboards) and
`/dashboardControl/dashboards/AbsStatsByCounty` (11,131 b). Verified **500**:
`/dashboardControl/dataSources` (70 b, `"Callback request failed due to an
internal server error."`) — not needed.

Two dashboards, measuring different things:

| | `AbsStatsByCounty` (`StatAbsentee`) | `VHCountsByCounty` (`StatVoterHistory`) |
|---|---|---|
| unit | county × party × ballot type × source × delivery | county × party × **VotingMethod** — `PrecinctCode` is declared and does not answer, see below |
| measures | Sent / Received / Rejected | HistoryCount |
| 2024 general | 130,640 sent, 107,874 received | 107,549 absentee + 293,918 early in-person |
| 2022 general | — | 71,680 absentee + 132,402 early in-person |
| 2026 general | **live today** — 428 applications, 19 sent, 0 returned | absent (newest election is 2026-08-25) |

The absentee table is **mail only**; Oklahoma's early in-person voting exists
solely in voter history. So the adapter prefers voter history when the election
is in it and falls back to absentee-only, where `inperson` stays `None` — never
`0`, in a state where 293,918 people voted early in person in 2024.

Oklahoma registers by party and both dashboards carry all four registrations, so
every party field is a real count and an empty bucket is a genuine `0`. The four
buckets partition the total exactly: 2024 DEM 113,521 + REP 236,011 + NPA 49,886
+ OTH 2,049 = 401,467.

`Election Day` credits are excluded, and so is `Protected` — Oklahoma's
address-confidential voters, whose method the table does not state. It has never
appeared in a November general (2022, 2024 and the 2026-08-25 runoff each return
exactly three methods), and an unrecognised method label raises `SchemaDrift`
rather than silently vanishing out of the total.

Neither dashboard has a within-election date dimension — there is no received
date anywhere in either table — so rows are stamped with the run's `as_of`, and
`fetch_history` returns one dated row per county on Election Day rather than
inventing an archived daily curve.

⚠️ **CORRECTED 2026-09-08: there is no retrievable precinct dimension, and this
file said twice that there was.** `VHCountsByCounty` declares `PrecinctCode` as
level 1 of the pivot's row hierarchy and lists it in `DataSourceColumns`, which
is where the claim came from. It is a declaration and not data. Across every
captured response the dimension's `EncodeMaps` entry is an **empty array** and
every slice keyed on it is empty:

```
EncodeMaps      DataItem0 (CountyDesc) 77   DataItem4 (PartyDesc) 4
                DataItem1 (PrecinctCode) 0
Slices          [DataItem0]                          77 rows
                [DataItem0, DataItem4]              304 rows
                [DataItem0, DataItem1]                0 rows
                [DataItem0, DataItem1, DataItem4]     0 rows
                [DataItem0, DataItem1, DataItem4, DataItem2]  0 rows
```

So a precinct code cannot be decoded even where a slice keyed on it exists, and
none does. The retrievable unit is the **county**, and Oklahoma's row above says
so. (This does not change the rejection, which was always about fragility, and
it does not change `ok.py`, which never read a precinct.)

## Nothing machine-readable during the season

### Mississippi — now VERIFIED, and the answer is no

The `unverified` row is closed. `www.sos.ms.gov` **403s plain `requests` (370 b)
and returns 200 to `curl_cffi` (145,760 b)** — the same fingerprint rule as Ohio
and Arizona, which `_net.get` already retries through. So the site is readable,
and what it publishes is:

- `/elections-voting/active-voter-count-reports` (200): a monthly **PDF** of the
  ACTIVE voter count per county, back to 2021. A denominator, not turnout.
- `/yall-vote/absentee-voting-information` (200): Mississippi has **no
  no-excuse early voting**. Absentee is excuse-required, in person at the circuit
  clerk's office or by mail, under an enumerated list of qualifications.
- `/elections-voting` (200) links nothing absentee-statistical at all; the only
  spreadsheets on the elections side are handbooks and calendars.

Mississippi does not register voters by party. There is no file, and there is
also no early-voting electorate to count.

### Alabama — no early voting, and an inventory that proves the negative

`www.sos.alabama.gov/alabama-votes/voter/election-data` (200, 218,654 b) lists
**every** election data file the SoS publishes — 150-odd links, precinct results
back to 1992, `ALVR-<year>.xls` registration totals through `ALVR-2026.xlsx`,
`…TotalBallotsCast.pdf` per election, and participation by age / gender / race as
post-election PDFs. There is not one absentee or early-voting file anywhere on
it, in any year. `/alabama-votes/voter/absentee-voting` (200) is application
forms and a county absentee-manager lookup.

Alabama has no in-person early voting and does not register voters by party, so
even a hypothetical file would carry neither dimension. Its open 2026 Senate seat
makes this a real loss and there is nothing to scrape.

### Arkansas — results only, and a correction to the party assumption

`www.sos.arkansas.gov/elections/` (200) and its `research/election-results` page
(200, 113,327 b) publish certified results back to 1976 as PDF/XLS and nothing
else. `elections/research/` offers results, the historical report, and NVRA
statistics. `www.voterview.ar-nova.org/voterview` (200) is the ESSVR VoterView
per-voter lookup — registration status, party association, polling place for one
named person — with no statistics endpoint. Arkansas runs 15 days of early voting
through its county clerks and the state publishes no count of it during or after.

**Correction worth recording:** Arkansas is commonly assumed to register voters
by party. Its own registration form,
`https://www.sos.arkansas.gov/uploads/elections/ArkansasVoterRegistrationApplication.pdf`
(200, 187,050 b), carries `Party Affiliation (Optional)` and a
`This is a party change.` checkbox — so a party field exists on the record — but
the state publishes no party-registration statistics and Arkansas is not counted
as a party-registration state. It is moot here, because there is no early-vote
file to attach a party to; it is recorded so nobody populates a party field on
the strength of the form alone.

### West Virginia — registers by party, publishes no early-vote data

**West Virginia does register by party**, and its own page says which ones:
`sos.wv.gov/elections/election-data/west-virginia-voter-registration-totals`
(200) — *"Voter registration numbers are broken down by political party:
Democrat, Republican, Mountain, Libertarian, Constitution, No Party, and Other."*

That is the whole of what it publishes. The election-data section is exactly
three pages: that one (monthly registration reports, a denominator),
`…/historical-voter-turnout` (200 — post-election, one link per election back to
2008) and `…/historical-election-results-and-turnout`. Nothing absentee, nothing
during the window, in a state that runs 13 days of early voting.

The two live apps are both per-voter, not statistical:
`https://apps.sos.wv.gov/Elections/Voter/AbsenteeBallotTracking` (200, 9,305 b —
a lookup form) and `https://apps.wv.gov/SOS/BulkData` (200, but a 302 to
`/SOS/BulkData/Login.aspx`, and it is business-entity data). Results go to
Clarity: `https://results.enr.clarityelections.com/WV/126209/` (200), election
night only.

## What `normalize.py` needed, and did not

The known `"non partisan"` gap did **not** bite Louisiana, because Louisiana's
report is column-headed `DEM | REP | OTH` rather than labelled per row —
`normalize.party()` is never called for it, and the `OTH` column is deliberately
not published at all. Its race and sex labels (`WHITE`/`BLACK`/`OTH`,
`MALE`/`FEMALE`) all resolve through `normalize.race()` / `.sex()` unchanged.

Oklahoma's four registrations — `Democrat`, `Republican`, `Independent`,
`Libertarian` — all resolve through `normalize.party()` (to `dem`, `rep`, `npa`,
`oth`), and its `Absentee` / `Early Voting` method labels through
`normalize.method()`. **No new vocabulary was needed for either state**, and no
per-state override table exists in either module.

One thing Louisiana cannot express through `DemoDay`: its report prints only
`MALE` and `FEMALE`, and the two do not add up to the total (East Baton Rouge
2024: 38,449 + 56,293 = 94,742 against 94,908). The residual is published as the
`unknown` sex bucket, which is exact — unlike the party residual, "neither male
nor female" *is* the bucket our vocabulary has, so nothing is conflated.

---

# The West: CA · OR · UT · ID · MT · NM · HI · WY

Investigated 2026-09-06. Every status below was observed live from this machine
on that date, through the same transport `_net.get` uses (requests with TLS
session tickets restored, retrying any 403 through `curl_cffi`'s Chrome
fingerprint). Nothing here is a guessed URL.

**Built this pass: OREGON, MONTANA, HAWAII.** All three were rejected by the
earlier survey, and **all three were rejected for the same reason: "this
pipeline has no PDF dependency".** That premise was already false when it was
written — `pypdf` is declared in `pyproject.toml` and `ia.py` parses Iowa's
absentee report with it — so the three highest-value sources in the West were
sitting behind a library that was already installed.

| State | Verdict | Source | Unit | Dims |
|---|---|---|---|---|
| **OR** | **BUILT** `or-sos` | SoS Daily Ballot Returns PDF | county (36) + statewide | county, party (12), **day** |
| **MT** | **BUILT** `mt-sos` | SoS Tableau CSV + PDF | county (56) + statewide | county, mail sent/received |
| **HI** | **BUILT** `hi-oe` | OoE Absentee Reconciliation PDF | county (4) + statewide | county, method |
| ID | not built — see below | voteidaho.gov + Datawrapper | county (44) | county, party, age |
| UT | **nothing during the general season** | — | — | — |
| NM | **nothing**, confirming the earlier survey | — | — | — |
| WY | **nothing public**; request-gated only | — | — | — |
| CA | built elsewhere this pass (SoS VBM workbook); the county survey is below | — | — | — |

---

## Oregon — `src/ev/adapters/or.py` (`or-sos`)

**Why:** an open governor's race *and* a Senate race, 3.1M registered voters, and
the richest during-season file found anywhere in this project — the county-by-day
page means **one download rebuilds the entire daily curve**, North Carolina style.

Verified, with exact statuses:

| URL | live | Wayback |
|---|---|---|
| `sos.oregon.gov/voting/Pages/current-election.aspx` | **200** | — |
| `…/elections/Documents/statistics/G22-Daily-Ballot-Returns.pdf` | **404** | **200**, 198,966 B @ `20221110100105` |
| `…/voting/Documents/G24-Daily-Ballot-Returns.pdf` | **404** | **200**, 206,077 B @ `20241113125733` |
| `…/elections/Documents/november-2025-Daily-Ballot-Returns.pdf` | **404** | **200**, 189,782 B @ `20251026202619` |
| `…/elections/Documents/May-19-2026-Daily-Ballot-Returns.pdf` | **404** | **200**, 688,555 B @ `20260514032204` |
| `…/elections/Documents/November-3-2026-Daily-Ballot-Returns.pdf` | **404** | — (does not exist yet) |

The survey's worry that "the filename convention changes every cycle" is real and
is solved the way every adapter here solves it: `voting/Pages/current-election.aspx`
is scraped first, and its archived captures prove it works — the October-2025
capture links `november-2025-Daily-Ballot-Returns.pdf` and the October-2024
capture links `G24-Daily-Ballot-Returns.pdf`, each under a *different* directory.
Literal fallback paths are tried only afterwards and are flagged UNVERIFIED in
`or.FALLBACK_PATHS`.

`data.oregon.gov` is not a substitute and was re-checked: filtering the Socrata
catalog to `domains=data.oregon.gov` returns exactly **two** ballot datasets, both
called "Ballot Count History" (`rxzj-n3di`, updated 2025-05-29; `9xrd-w6my`,
2022), both statewide and post-election. The 408 hits an unscoped search returns
are federated from other portals — the same trap Connecticut set earlier in this
document.

### Six things the file does that a naive parser gets wrong

1. **Oregon rebuilt the report between November 2025 and May 2026.** The classic
   layout (2014-2025) is an Excel print; the new one is a Power BI export.
   `or.py` parses **both**, dispatching on the file rather than the cycle,
   because which one the 2026 general uses cannot be known yet.
2. **The classic party table's column headings are rotated 90°**, and pypdf
   emits them *after* the data rows in an order that is not the column order.
   They are recovered from their text matrices' x coordinates and sorted. Reading
   them in extraction order would have swapped Democrat with Constitution.
3. **The Power BI tables have blank cells** — Gilliam has no Progressive
   ballots and Power BI prints nothing rather than `0`, so four of Oregon's 36
   counties have short rows. Splitting on whitespace silently shifts every column
   after the gap. Those pages are read in pypdf's `extraction_mode="layout"` and
   assigned by character position, then each row is reconciled against its own
   printed `Total` — which is also what licenses reading the gaps as zeros.
4. **An en-dash is a zero.** On 2022-10-25 Columbia and Wallowa print `–` for
   ballots returned. Read as "no such row" that silently drops two of 36
   counties; read as `None` it renders as "not reported" for a county that had
   in fact returned nothing. The row's own `0.0%` return rate is checked.
5. **Excel's `#######` occupies a column and carries no value.** The 2022
   statewide party row renders Nonaffiliated registration that way. It must not
   shift the columns to its right.
6. **The daily matrix and the summary page disagree, and the summary wins.** On
   2022-10-25 the day columns sum to 65,202 statewide against a summary of
   65,944, and Curry County's three days sum to 991 against 1,290 — counties
   backfill a late report into the summary without restating the day it belonged
   to. The as-of day's cumulative figure is always the summary's. (On 2024-11-05
   they agree exactly: 2,004,468 both ways.)

### The backfill, and the trap in it

`fetch_history` recovers **fourteen days for each of 2022 and 2024**:

```
2022  2022-10-21 .. 2022-11-09   1,813,994 returned   DEM 736,049
2024  2024-10-18 .. 2024-11-06   2,137,613 returned   DEM 823,326
```

The archive stamps in `or.ARCHIVED` are deliberately **not** the newest captures.
Oregon replaces the report with a post-canvass FINAL version weeks later, and the
2024 final is a seven-page file whose party table gains a second, supplemental
section: its page-2 party columns sum to 2,304,398 against a printed total of
2,307,070, and adding the supplement overshoots to 2,317,716. Neither reading
reconciles, so `parse` refuses all five 2024 captures from `20241123001836`
onward — which is the right answer, and is why the stamps recorded here are the
last captures of the *original* during-season report.

### `normalize.py` gaps, reported not fixed (per the ownership rules)

Oregon's twelve party labels expose one outright bug and three omissions:

* **`normalize.party("independent")` returns `npa`, and in Oregon that is
  wrong by 150,715 voters.** The Independent Party of Oregon is a
  ballot-qualified minor party; Oregon's unaffiliated bucket is spelled
  **`Nonaffiliated`**, which `normalize.party()` does not recognise at all.
* `pacific green`, `progressive` and `we the people` are also unrecognised.

`or.py` therefore routes every label through a documented `OR_PARTY` table and
raises `SchemaDrift` for anything absent from it. The long-term fix is a few
lines in `_PARTY_MAP` — but note that `"independent" -> npa` cannot simply be
changed, because it is correct for the states that use the word to mean
"unaffiliated". The owner of `normalize.py` should decide whether that entry
needs a per-state escape hatch.

---

## Montana — `src/ev/adapters/mt.py` (`mt-sos`)

The earlier survey found this source, confirmed it works with one GET, and then
rejected it, ending: *"**Buildable the moment either a PDF parser exists or the
CSV grows a date column.**"* A PDF parser exists. This is that build.

Verified:

| URL | status |
|---|---|
| `tableau-ext.mt.gov/t/SOS/views/AbsenteeBallots/AbsenteeDash.csv` | **200**, `text/csv`, 5,518 B, 171 rows |
| `…/AbsenteeDash.pdf` | **200**, `application/pdf`, 285,506 B |
| `…/views/AbsenteeBallots/Sheet1.csv` | **404** |
| `…/views/AbsenteeBallots` (workbook index) | **404** |
| `tableau-ext.mt.gov/api/v1/sites/SOS/workbooks` | **401** |

Tableau Server renders the same view in either format from one URL stem. The CSV
is the numbers; **the PDF is the label on them** — its first line is
`2026 Montana Primary Election Absentee Ballot Counts` and it ends
`Compiled On 6/15/2026 6:55:19 AM`. The adapter fetches the PDF first, refuses
the whole run unless the title names *this cycle's general*, and dates every row
from the compile stamp. Today Montana therefore reports `NotYetPublished`, which
is the correct state of a dashboard still showing the June primary — precisely
the failure mode the survey identified.

The counts themselves are exact and self-checking: 56 counties plus Montana's own
`All` row, and the county sums are verified against it on every run (2026-06-15:
514,152 sent and 268,125 received, both ways). A short download therefore fails
loudly instead of publishing a statewide total quietly missing a county.

**A bounded, documented risk:** the two exports are separate requests, so a
Tableau extract refresh landing between them attaches one refresh's stamp to the
next refresh's counts. The stamps are daily, so the window is seconds wide once a
day and the worst case is a one-day mislabel, not a wrong number. Reading both
from one response is not possible — the CSV carries no title or timestamp and the
workbook exposes no other sheet (404 above).

Two corrections to the record: the CSV is *not* keyed by a Tableau parameter that
can be changed to select an election (there is none), and **Montana does not
register voters by party** — `data/meta/states.csv` has `has_party_reg=false` for
MT and is right. Every `party_*` field is `None`.

---

## Hawaii — `src/ev/adapters/hi.py` (`hi-oe`)

The survey found Hawaii's four **per-county** reports
(`AbsenteeReconDP-01-20260717.pdf` and friends) and rejected them as "daily,
dated, and PDF". There is a **fifth file it did not find**, and it is better than
all four together:

```
https://elections.hawaii.gov/wp-content/uploads/AbsenteeReconState-{YYYYMMDD}.pdf
```

One page, one row per county, a totals row, and a footer carrying **both** the run
timestamp **and the election's own name**:

```
County   ELECT Sent (a) | ELECT Voted (b) | ELECT Invalid (c) | EV Voted (d) |
         MAIL Sent (e)  | MAIL Voted (f)  | MAIL Invalid (g)  |
         VOTED (b+d+f)  | TOTAL (a+d+e)
Hawai'i     287   51   0    804   112461   35712    325    36567   113552
Maui        329   45   0    459    97819   25056    410    25560    98607
Kaua'i       95    9   0    471    41720   14906    184    15386    42286
Honolulu   1844  221   0   2303   480780  154602   1010   157126   484927
           2555  326   0   4037   732780  230276   1929   234639   739372
Absentee Reconcillation     8/8/2026 3:11:26 AM     2026 Primary Election
```

That last line is why Hawaii is buildable and Montana's bare CSV was not: the
filename pattern is shared with the August primary, and the file says which
election it is. A primary's report is refused **by name**, not by a date window
we guessed.

| URL | status |
|---|---|
| `elections.hawaii.gov/resources/absentee-voting-report/` | **200**, 189,727 B — an index listing every dated report |
| `…/uploads/AbsenteeReconState-20260717.pdf` | **200**, 72,858 B |
| `…/uploads/AbsenteeReconState-20260808.pdf` | **200**, 72,960 B |
| `…/uploads/AbsenteeReconDP-01-20260717.pdf` | **200**, 143,942 B (Honolulu) |
| `…/uploads/AbsenteeReconDP-{02,03,04}-20260717.pdf` | **200** (Hawaii, Kauai, Maui) |
| `…/uploads/AbsenteeReconDP-01-20260905.pdf` | **404** — no general-election report yet |
| `…/uploads/AbsenteeReconDP-01-20241030.pdf`, `-20241105`, `-20221101` | **404** — purged |

**No archive exists.** A Wayback CDX sweep of `elections.hawaii.gov` from 2022
(100,001 rows) returns **zero** captures of any `AbsenteeRecon*` file, so
`fetch_history` keeps the base class's `NotYetPublished`.

**Politeness matters on this host.** Walking a blind eleven-day range of dated
URLs earns **HTTP 429** — observed during a `python -m ev probe` run. The index
page is authoritative and is one request, so it is used alone whenever it
answers, capped at three candidates; the constructed range is the fallback for a
day the index is down.

Judgement calls: Hawaii has no party registration, so every `party_*` is `None`;
`ballots_total` is Hawaii's own `VOTED` column, which exceeds
`mail_returned + inperson` because it also counts UOCAVA ballots returned by
email or fax; the file's own arithmetic (`VOTED = b+d+f`, `TOTAL = a+d+e`, and
the totals row against the sum of the counties) is verified on every run, which
is what proves nine values landed in the nine fields we think they did. **Kalawao
County (15005) has no elections division and never appears** — Hawaii's four
county clerks are the whole state, so four rows *is* full coverage.

---

## Idaho — a real advance on the survey, and still not built

The survey rejected Idaho because "its data is served from `datawrapper.dwcdn.net`
under **cycle-specific chart IDs** that must be rescraped every election". Two
things it did not have:

1. **The chart ids are discoverable from a stable `.gov` page that also names the
   election.** `https://voteidaho.gov/data-and-dashboards/absentee-tracker/`
   (**200**, 111,366 B) carries `Absentee & Early Voting Stats – 2026 Primary
   Election`, a `Last updated:` line, statewide `Absentee Ballots Returned` /
   `Issued` / `Early Voting … Voted` counters — all server-rendered into the
   HTML — and six Datawrapper embeds: `HCDQQ`, `PJZGl`, `UDG7E`, `aCECg`,
   `hGfEl`, `jshNw`. Rescraping per cycle is exactly the index-scrape pattern
   every adapter in this repo already does. (`/absentee-tracker-2024/` is
   **200** too and shows the 2024 cycle used **Tableau Public**, not Datawrapper
   — so the hosting platform really does move.)
2. **A version trap worth recording.** `https://datawrapper.dwcdn.net/HCDQQ/dataset.csv`
   is **404** (`NoSuchKey`); the dataset lives at `/{id}/{version}/dataset.csv`.
   `/HCDQQ/1/dataset.csv` returns **200 with STALE data** (Ada 1,353 Republican),
   and `/HCDQQ/17/dataset.csv` returns **200** with different, later data (Ada
   10,644). Only `/{id}/` redirects to the current version — and it moved from 17
   to 23 during this session. **Any pinned version number silently serves an old
   snapshot forever.** The chart itself is genuinely county × party:
   `ResCountyDesc,Republican,Democratic,Other`.

Also found and worth knowing: `results.voteidaho.gov` is an Angular SPA over a
plain, unauthenticated `.gov` REST API. `…/results/public/api/elections/id/may2026`
→ **200** JSON with `electionDate`, `asOf`, `lastUpdated` and a list of published
`.xlsx` reports; `…/vr` → **200** (registration by county); `…/localities` →
**200**; `…/turnout` and `…/stats` → **200 but empty** for a completed election;
`…/api/elections` → **404**. Nothing there is early-vote data today, but
`/turnout` is the endpoint to re-check once the general opens.

**Not built**, for two reasons that are about correctness, not effort:

* The tracker page's counters and its `Last updated:` value are hand-maintained
  WordPress content, and today they are all `0` with the date **blank**. There is
  no way to verify from off-season what an as-of date looks like when it exists.
* The page and the charts are published **independently**. A general-season page
  reset (election name updated, counters zeroed) while the charts still hold
  primary data would publish primary county numbers under the general — the exact
  failure Montana was rejected for. The guard is available (require the charts'
  county totals to reconcile against the page's own statewide counter, else
  `SchemaDrift`), but it cannot be tested against real general-season data, and
  an untested guard on a mis-publish path is not worth shipping.

Idaho is the clear next target in this region the day its tracker goes live.

Correction to `data/meta/states.csv`, for its owner: **Idaho is marked
`has_party_reg=false` and does register by party** — its own absentee tracker
breaks out `Republican / Democratic / Other`. (The survey's footnote that
"Idaho's 'party' during a primary is the ballot chosen, not registration" is
right about primaries and does not apply to a general election.) MT, HI and CA
are all correct in that file.

---

## Utah — re-checked, and the 2020 page really was never revived

| URL | status |
|---|---|
| `vote.utah.gov/ballots-processed/` | **404** |
| `elections.utah.gov/` | **200** → 301 to `vote.utah.gov` |
| `vote.utah.gov/wp-json/wp/v2/pages?per_page=100` | **200** — all 89 pages enumerated, none is a during-season returns page |
| `public.tableau.com/views/August2026Dashboard/GeographicDashboard.csv` | **404** |

The archived `/ballots-processed/` page (capture `20221121102806`) is a genuine
county × ballots-processed HTML table — and it still says *"Last updated:
November 3, 2020 … This page will be updated every day until November 3, 2020."*
Two years after the fact. It was never revived.

The only during-season artifact since is
`vote.utah.gov/june-2024-primary-estimated-of-ballots-returned/` (capture
`20240611222133`), and it publishes **percentages with no counts** — "Beaver –
0.4% … Salt Lake – 5.0%" — alongside PNG images (`June-11-Estimated-Ballots.png`
and eight more in the media library). A percentage of an unstated denominator is
not a ballot count.

`Master-Aggregated-Numbers-2023-2026.xlsx` is still there and was modified
**2026-08-28**, which is after the June primary canvass — confirming it is a
post-canvass aggregate, not a tracker.

**Verdict unchanged: Utah publishes nothing machine-readable during the general
election season.** For an all-mail state with a Senate race that is a real loss,
and the thing to watch is whether `/ballots-processed/` or a
`november-2026-…-ballots-returned/` page appears in the WordPress REST index once
ballots go out — that index makes discovery a single request.

---

## New Mexico — confirmed, with the search that confirms it

The earlier survey's verdict holds. Re-checked from a different angle — the SoS
runs WordPress, so its own search API can be asked exhaustively:

* `sos.nm.gov/wp-json/wp/v2/search?search=absentee` → **200**: rule-making
  archives, "how to return your ballot" FAQs, and press releases. The only ones
  carrying numbers are from **2018** ("Sec. Toulouse Oliver Announces Final Early
  Voting and Current Absentee Turnout Numbers for the 2018 General Election"), as
  prose. The practice stopped.
* `…?search=early+voting` → **200**, same shape.
* `…/wp-json/wp/v2/media?search=absentee` → **200**: an application form, two
  videos and a transcript. No data file of any kind.
* `www.sos.nm.gov/voting-and-elections/` → **200**, no data links.
* `electionresults.sos.nm.gov/` → **200** — an election-night results SPA.

**Nothing machine-readable during the season**, in a state with an open
governorship and a Senate race. The gap is a publishing decision, not a
transport problem.

---

## Wyoming — confirmed; the only daily file is request-gated

`sos.wyo.gov/Elections/` → **200**. `sos.wyo.gov/Elections/Statistics.aspx` →
**200**, and its entire contents are two links: `/Elections/VRStats.aspx` (voter
registration) and `/Elections/Docs/VoterProfile.pdf` (post-election turnout).
There is no absentee or early-vote file.

The survey's finding stands and is the whole story: Wyoming's daily
individual-level absentee file exists, is party-tagged, runs **September 18 –
November 2, 2026**, and is delivered to a Google Drive folder on request via
`DailyAbsenteeFileRequestForm.pdf`. **One email to `Elections@wyo.gov` away** —
which is a decision for a human, not a scraper.

---

## California — the county survey the brief asked for

California's statewide source was built this pass from a different find (the SoS
`vbm-statistics.xlsx` workbook — see that section). The ten large counties were
still checked properly, because a partial county source was the fallback plan.
Recording the result so nobody repeats it:

| County | site | during-season machine-readable returns? |
|---|---|---|
| Los Angeles | `www.lavote.gov` **200**, `results.lavote.gov` **200** (SPA) | **No.** `…/current-elections/vote-by-mail-turnout` → **404**. A Wayback CDX sweep of `lavote.gov` from 2020 finds no daily VBM-return report of any kind, in any format. |
| **Orange** | `ocvote.gov/datacentral/` **200** | **Yes — the best county source in the state.** See below. |
| San Diego | `www.sdvote.com` **200** | No. "Past Voter Turnout" (historical) only. |
| Santa Clara | `vote.santaclaracounty.gov` **200** *(403 to plain requests; 200 through `curl_cffi`)* | No. "Reports and Statistics" carries no during-season returns. |
| Alameda | `acvote.alamedacountyca.gov` **200**; `alamedacountyca.gov/rov_app/edata?page=vbm` **200** | No. The eData "Vote By Mail" tab is **registration** — the word "return" does not appear on the page. |
| Sacramento | `elections.saccounty.gov` **200** | No. Registration totals only. |
| Riverside | `voteinfo.net` **200** *(403 plain; 200 through `curl_cffi`)* | No data links at all. |
| San Bernardino | `elections.sbcounty.gov` **200** | No. Historical turnout plus an ArcGIS precinct map. |
| Contra Costa | `www.contracostavote.gov` **200** | No matching links. |
| Fresno | `fresnocountyca.gov/…/County-ClerkRegistrar-of-Voters` **200** *(403 plain; 200 through `curl_cffi`)* | No. |

**Orange County has an undocumented but unauthenticated JSON API**, and it is
worth writing down even though it was not needed:

```
https://ocvote.gov/datacentral/bin/get.php?q=vbm-trend-returned      -> 200 JSON
https://ocvote.gov/datacentral/bin/get.php?q=vbm-counts-returned&p=*552-1 -> 200 JSON
https://ocvote.gov/datacentral/?tab=ballots                          -> 200 HTML
```

`vbm-trend-returned` is ballots returned **by date** — a real daily curve.
`?tab=ballots` is server-rendered HTML naming the election ("2026 Primary
Election") with countywide VBM issued and returned **broken out by party**
(`DEM 311,666 · REP 324,320 · N-P 130,965 · AI · GRN · LIB · P-F`, total
717,548 as of this check). `q=vbm-counts-issued` with no `p` → **200** with
`{"info":{"error":"something went wrong"}}`, so the parameter shape is the whole
contract — the same fragility that got Oklahoma's DevExpress endpoint deferred.

Also checked at state level, for completeness:

* `api.sos.ca.gov/returns/status` → **200** JSON, county turnout and precincts
  reporting — **election night**, not the return period.
* `api.sos.ca.gov/returns/unprocessed-ballots` → **500**;
  `api.sos.ca.gov/` → **403**.
* `elections.cdn.sos.ca.gov/` (bucket root) → **403** XML, while
  `…/ror/15day-gen-2024/county.xlsx` → **200**, 29,979 B. The root 403 is a
  bucket-listing denial, not a block — the same shape as Tennessee's S3.
* `data.ca.gov/api/3/action/package_search?q=vote+by+mail` → **200**;
  `data.lacounty.gov/api/3/action/package_search` → **404**.

---

## Transport: three more hosts the `curl_cffi` retry unblocked

The brief asked which previously-blocked hosts the new transport opened. In this
region the answer is three, all California county sites, all **403 to plain
`requests` and 200 through the Chrome fingerprint**:

```
vote.santaclaracounty.gov      403 -> 200   86,876 B
voteinfo.net (Riverside)       403 -> 200  139,590 B
fresnocountyca.gov (Fresno)    403 -> 200  118,976 B
```

None of them turned out to publish return data, so the unblock changed no verdict
here — but it changed those three from "unverifiable" to "checked and empty",
which is the difference between a gap and a guess.

Nothing in OR, UT, ID, MT, NM, HI or WY was ever blocked. Every 404 recorded in
this section is an honest 404: the file does not exist yet.
