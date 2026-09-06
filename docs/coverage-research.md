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
| OK | yes — undocumented dashboard JSON API | JSON | county (77) + precinct | county, party, method | medium | rejected: fragility |
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
