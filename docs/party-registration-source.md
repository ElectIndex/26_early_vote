# Statewide party registration — where every number came from

`data/meta/party_registration.csv` holds one row per **state × cycle**: the party
registration of **all registered voters** in that state, from that state's own
Secretary of State or Board of Elections, as close as could be got to October
2022, October 2024, and the most recent figure published as of **2026-09-07**.

**This is not early-vote data and must never be joined to one.** It is the
denominator, not the numerator: the thing a returned-ballot party split should be
*compared against*. Using the party mix of early voters here would be circular —
that is the quantity `docs/party-estimate.md` is trying to predict.

Nothing in `src/` reads this file yet. It is exogenous reference data, gathered so
that the decision about whether to anchor the party estimate on registration can
be made against real numbers rather than a recollection of them.

**94 rows, 32 jurisdictions** (31 states + DC), collected 2026-09-07. Every figure
in the file was read out of a document fetched that day; the URL is on the row.

---

## Why this file exists

`docs/party-estimate.md` names the problem precisely:

> **Registration is not vote.** [...] Kentucky is full of registered Democrats who
> vote Republican; its registration split (44.7% D of two-party) sits ten points
> Democratic of its presidential split (34.5%).

The model predicts a two-party share anchored on each county's **2024
presidential** result and is scored against each state's reported two-party
**registration** share of returned ballots. Those are different objects, and the
distance between them is what this file measures.

The distance is larger than the doc's ten points, it runs in **both directions**,
and it is **not a constant**:

| state | 2024 pres D | reg D 2022 | reg D 2024 | reg D 2026 | gap 2024 |
|---|---:|---:|---:|---:|---:|
| UT |  38.9 |  21.1 |  20.9 |  21.4 | **−18.0** |
| WY |  26.5 |  11.8 |  12.4 |  12.3 | **−14.1** |
| ID |  31.2 |  18.4 |  17.8 |  15.9 | **−13.5** |
| AK * |  43.1 |  34.8 |  33.9 |  33.0 | **−9.2** |
| KS |  41.8 |  37.2 |  36.3 |  35.8 | **−5.4** |
| NH |  51.4 |  50.9 |  46.6 |  45.2 | **−4.8** |
| NE |  39.6 |  36.3 |  35.2 |  34.6 | **−4.4** |
| SD * |  35.0 |  33.9 |  31.7 |  28.9 | **−3.4** |
| CO * |  55.6 |  52.9 |  52.8 |  52.7 | **−2.9** |
| AZ * |  47.2 |  46.9 |  44.8 |  44.2 | **−2.4** |
| IA * |  43.3 |  46.6 |  42.5 |  42.6 | **−0.7** |
| ME * |  53.5 |  55.2 |  53.5 |  52.7 | **−0.0** |
| DC |  93.3 |  93.5 |  93.8 |  93.8 | **+0.5** |
| OR |  57.4 |  58.1 |  57.9 |  57.2 | **+0.5** |
| FL * |  43.4 |  48.5 |  44.6 |  42.0 | **+1.2** |
| OK |  32.5 |  36.9 |  34.0 |  32.3 | **+1.5** |
| NV * |  48.4 |  52.3 |  50.4 |  50.0 | **+2.0** |
| PA * |  49.1 |  53.6 |  51.9 |  51.4 | **+2.7** |
| NC * |  48.4 |  52.9 |  51.2 |  49.9 | **+2.8** |
| MD * |  64.8 |  69.3 |  68.7 |  68.7 | **+4.0** |
| CA * |  60.4 |  66.3 |  64.7 |  64.3 | **+4.3** |
| NM |  53.1 |  58.7 |  57.6 |  56.4 | **+4.5** |
| CT |  57.4 |  63.7 |  62.8 |  61.8 | **+5.4** |
| DE |  57.5 |  63.3 |  63.1 |  62.4 | **+5.6** |
| NJ |  53.0 |  62.4 |  61.1 |  60.4 | **+8.1** |
| NY |  56.3 |  69.1 |  67.8 |  67.9 | **+11.5** |
| MA |  63.0 |  76.7 |  75.7 |  75.4 | **+12.7** |
| WV |  28.7 |  45.6 |  41.5 |  38.3 | **+12.9** |
| KY * |  34.5 |  49.7 |  47.6 |  45.8 | **+13.1** |
| LA |  38.8 |  54.2 |  52.0 |  49.8 | **+13.2** |
| RI |  57.1 |  75.2 |  72.5 |  70.1 | **+15.5** |
| AR |  34.3 |     — |     — |  35.4 | **—** |

All figures are **D share of the two-party (D+R) total**, in points. `gap 2024` is
2024 registration minus the 2024 presidential result. `*` marks a state this
tracker follows with a party split. `2024 pres D` is computed from this repo's own
`data/baseline/county_results_2024.csv`, summed to state.

Three things fall out of that table, and each one bears on how — or whether — a
correction should be built:

1. **The gap spans 33.5 points, from −18.0 (Utah) to +15.5 (Rhode Island).** A
   single global conversion factor between vote and registration would be wrong
   by more than the model's entire current error in most states. This is the same
   objection `party-estimate.md` already levelled at "how partisan the state is"
   as a fitted term, now measured directly rather than inferred.

2. **The sign flips, and it flips for a structural reason.** Positive gaps are the
   ancestral-Democratic registration states (KY, LA, WV, and the closed-primary
   Northeast). Negative gaps are the closed-primary Republican West (UT, WY, ID,
   AK, KS), where the only primary that decides anything is the Republican one, so
   everyone registers into it. A correction fitted on one group inverts on the other.

3. **The gap is not stable in time.** Kentucky's registered-D share falls 49.7 →
   47.6 → 45.8 across three cycles — 3.9 points of drift in four years, all of it
   in the same direction. West Virginia moves 45.6 → 41.5 → 38.3, over 7 points.
   A factor calibrated on 2022 and applied in 2026 is stale by construction in
   exactly the states where it matters most. **This is the strongest argument for
   holding a dated registration figure per cycle rather than a constant.**

---

## The schema

    state,cycle,as_of_date,basis,reg_total,reg_dem,reg_rep,reg_oth,reg_npa,npa_label,source_url,retrieved_at,note

| column | meaning |
|---|---|
| `state` | two-letter postal code |
| `cycle` | 2022 / 2024 / 2026 — the cycle the row is *for*, **not** necessarily the year of `as_of_date` |
| `as_of_date` | the date the source says the count is as of, `YYYY-MM-DD` |
| `basis` | `active`, `active+inactive`, or `unstated` — see below, it moves shares by points |
| `reg_total` | the total the source itself prints; blank if it prints none |
| `reg_dem` / `reg_rep` | the two major parties as the source labels them |
| `reg_oth` | all minor/third parties, summed |
| `reg_npa` | the no-party / unaffiliated / undeclared / unenrolled / nonpartisan category |
| `npa_label` | the state's own name for that category, verbatim |
| `source_url` | the artifact actually fetched |
| `retrieved_at` | `2026-09-07` for every row in this pass |
| `note` | the caveat that would otherwise be lost |

### THE BLANK RULE applies here exactly as it does in `schema.py`

A category a state does not report is **blank**, never `0`. Three states have no
minor-party bucket or no no-party bucket at all, and inventing one would put
hundreds of thousands of voters in the wrong column:

* **`reg_npa` is blank for AZ (all cycles), LA 2022/2024, PA 2022/2024.** None of
  those reports publishes a no-party category separate from "Other". Arizona's
  own "Other" column — 1,496,589 of 4,340,860 registrants in April 2026 — mixes
  party-not-designated independents with unrecognised parties and cannot be
  decomposed, so `reg_oth` for AZ (1,577,033, that column plus Libertarian, No
  Labels and Green) is overwhelmingly *not* third parties. Pennsylvania's
  certified election reports publish only D / R / Libertarian / (Green) / Other
  Parties. **Do not read `reg_oth` as third parties in those rows.**
* **`reg_oth` is blank for NH, all three cycles.** New Hampshire's table has a
  Libertarian column but it is empty (the party lost recognised status after
  2018), and D + R + Undeclared equals the printed total exactly, so there is no
  residual to record.
* **`reg_oth` is `0` for RI 2022 and RI 2026, and those are real zeros** —
  Rhode Island's Moderate and No Labels columns exist and hold zero registrants
  on those dates. That is the distinction the blank rule is for.

Ballotpedia, by contrast, prints `0` for New Hampshire's and Rhode Island's
other-party counts alike. That is precisely the error this rule prevents.

### `basis` — the ambiguity most likely to move a number

"Active" and "active + inactive" are not close together. Pennsylvania 2026:

| basis | D | R | two-party D |
|---|---:|---:|---:|
| active + inactive (**the row in the file**) | 3,846,695 | 3,634,536 | 51.42% |
| active only | 3,417,637 | 3,384,915 | 50.24% |

**1.2 points**, on the same day, from the same agency. New York is worse: its
active-only total is 12.5M against 13.4M active+inactive.

The rule followed was **prefer active-only where the source publishes both**, and
record `unstated` where the source publishes neither label rather than guess.
The result is *not* uniform across the file, and any consumer that pools states
must know this:

| basis | states |
|---|---|
| `active` | AZ, CA, CO, CT, FL, IA, MD, ME, NV, NY, RI, SD, UT |
| `active+inactive` | KS, LA, NC, PA (2026) |
| `unstated` | AK, AR, DC, DE, ID, KY, MA, NE, NH, NJ, NM, OK, OR, PA (2022/2024), WV, WY |

Two of these were forced rather than chosen:

* **Pennsylvania 2026 is `active+inactive`** because the only sheet in PA's weekly
  workbook that carries a separate `No Aff` column is the combined one; its
  active-only sheet collapses no-affiliation into "Other". Both figures are in the
  row's note.
* **North Carolina is `active+inactive`**, which contradicts the usual assumption
  that NCSBE's RegStat is active-only. This was verified empirically rather than
  assumed: the live Alamance County voter file was downloaded and cross-tabbed
  status × party against RegStat's Alamance row for the same date. All five party
  counts and the total match active+inactive exactly (DEM 31,380 active + 5,158
  inactive = 36,538 = RegStat's figure); active-only does not. **NC's 7.84M
  denominator is therefore about 10% larger than an active-only figure and is not
  directly comparable to the NY rows.**

### Party-label mapping — what was folded into `reg_oth` and `reg_npa`

No unrecognised label was bucketed silently. The traps that would have changed an
answer:

* **"Independent" means opposite things in different states.** Oklahoma's
  "Independent" **is** its no-party category → `reg_npa`. Oregon's **Independent
  Party of Oregon** is a real registered party with 153,045 members → `reg_oth`.
  Same for Florida's **Independent Party of Florida** (276,467 in 2024) and
  Utah's **Independent American Party**. Merging Florida's into NPA would inflate
  its unaffiliated share by roughly two points.
* **Kentucky prints both an `Other` column and an `Ind` column and has no
  `Unaffiliated` column.** `Ind` (142k → 167k → 180k, growing) went to `reg_npa`;
  `Other` (192k → 192k → 185k, flat/declining — the shape of a legacy residual)
  went to `reg_oth`. The alternative mapping moves 185k–192k voters. It does not
  affect the two-party share, which is the quantity of interest.
* **Alaska has two no-party categories** — `N Nonpartisan` and `U Undeclared` —
  summed into `reg_npa`, with the split in the note.
* **California prints a separate "Unknown" column**, which CA's own note defines
  as voters who did not select a preference. It went to `reg_oth`, so `reg_npa` is
  strictly "No Party Preference". The alternative figures are in the row notes.
* **South Dakota prints `Independent` and `No Party Affiliation` as two columns**
  while its own footnote says they are the same thing; both went to `reg_npa`.
* **Connecticut's minor-party code `UNAFF`** is "Unaffiliated (Conservative)" — a
  *party*, inside Minor Parties, not the unaffiliated bucket.

---

## Which states register by party — `has_party_reg` is wrong in two places

`data/meta/states.csv` was checked state by state against the states' own
publications. It has **two errors**, both in the direction that matters:

| state | `states.csv` | should be | why |
|---|---|---|---|
| **MO** | `true` | **`false`** | Missouri added an *optional* party field to the registration form only on 2023-01-01 (RSMo 115.628: *"Beginning January 1, 2023, the voter registration application form shall be amended to include a choice of political party affiliation."*). The SOS publishes registered-voter counts by county with **no party breakdown at all**, so there is no figure to record — and a 2022 figure is impossible in principle. |
| **AR** | `false` | **`true`, heavily caveated** | Arkansas *does* publish a party breakdown, but see below. |

Two further notes on that column:

* **Washington's note is wrong.** `states.csv` says WA "registers by party, but the
  state file's party column is empty". Washington does not register by party at
  all — it runs a Top Two primary and voters may not declare an affiliation. The
  empty column is empty because there is nothing to put in it.
* **Ohio's `false` is correct.** Ohio's "party affiliation" is derived from the
  last partisan primary ballot a voter requested, not from the registration
  record. It is a different object again and should not be added to this file.

Everything else in `has_party_reg` matches. The resulting set is 31 states + DC,
consistent with Ballotpedia's August 2026 count of "31 states, the U.S. Virgin
Islands, and the District of Columbia".

### Arkansas is in the file and should almost certainly not be used

Arkansas publishes a "VR Statistics Count Report" whose statewide row as of
2026-07-17 reads:

    Grand Total   84043   153227   744   3   1592912   124   1   1831054

under the columns Democratic, Republican, Libertarian, Nonpartisan Judicial,
Optional, Green, Other, Grand Total. (The header wraps; the columns were resolved
from `pdftotext -bbox-layout` word positions and the row cross-foots to the
printed total.) **87% of Arkansas registrants sit in "Optional"** — the party field
is optional and almost nobody fills it in. The D/R split therefore describes 13%
of the file and is not a usable registration anchor. It is recorded because it
exists, and flagged because it is a trap.

No Arkansas figure could be found for 2022 or 2024: the SOS research page links
only the single current report, and Wayback captures of that page at 2022-11-02
and 2024-09-22 carry no registration PDF at all.

---

## Every row is corroborated

As a check independent of the collection itself, all 32 of the 2026 rows were
compared against Ballotpedia's separately-transcribed August 2026 table. **Every
one agrees** — exactly where the snapshot dates match, and with small drift
explained by a different date otherwise. Exact matches on CA, DC, FL, MA, ME, NH,
SD, OR, AR and (for the active+inactive variant recorded in the note) NY.

**One disagreement, and our number is the right one.** Ballotpedia's Connecticut
row cites "Registration and Party Enrollment Statistics as of October 17, 2025"
but prints figures identical to Connecticut's *October 2024* report. Fetching
`nov25re.pdf` directly gives, on its own Totals line:

    Totals   489,905 38,895 528,800 | 792,887 85,566 878,453 | 34,030 4,348 38,378 | 935,892 121,669 1,057,561 | 2,252,714 250,478 2,503,192

i.e. R active 489,905, D active 792,887, Minor active 34,030, Unaffiliated active
935,892, total active 2,252,714 — which is what the file records. Ballotpedia
updated its footnote and not its numbers.

The three differences from Ballotpedia that are *basis*, not error: Colorado (ours
is active-only; theirs includes inactive and 16/17-year-old pre-registrants),
New York (ours active, theirs active+inactive — both are in the row) and Nevada
(ours active-only end-of-August; theirs a different report a month earlier).

---

## Per-state caveats worth reading before trusting a row

### Rows whose `as_of_date` is not in their cycle year

Two states publish too rarely to have a 2026 figure at all. Both rows are kept
because a dated stale number is more useful than a gap, and `as_of_date` makes the
staleness self-documenting — but **do not treat either as a 2026 observation**:

* **MA 2026 is really 2025-02-01.** Massachusetts's own Registration Statistics
  index (captured 2026-01-03) and its historical enrollment table (captured
  2026-09-03) both still end at February 2025. Ballotpedia, working independently,
  also lands on "Registered Voters and Party Enrollment as of February 1, 2025".
  The site sits behind Imperva and blocks curl, WebFetch, headless Chrome and
  Wayback's own crawler, so a newer report may exist and be unreachable.
* **CT 2026 is 2025-10-17.** Connecticut publishes this report once a year in late
  October; as of 2026-09-07 the 2026 edition is not out.

### Reports that are not the newest their state has published

* **AZ 2026 is the April 2026 report, not July.** A `State-Voter-Registration_July_2026.pdf`
  exists but `apps.azsos.gov` sits behind a Cloudflare managed challenge that
  could not be cleared by curl with browser headers, WebFetch, or a CDP-driven
  Chrome; it is not in Wayback, Save Page Now returned 520. **Every Arizona
  artifact used came from Wayback's raw replay of the official azsos.gov file**,
  and the 2024 and April-2026 numbers match AZ SOS's own press release and
  statistics page digit for digit.
* **SD 2026 is the 2026-07-31 report, deliberately, because the newest one is
  corrupt.** South Dakota's 2026-09-01 PDF has its **Democratic and Republican
  data columns transposed**. Oglala Lakota County (Pine Ridge, ~90% Democratic)
  reads 5,043 D / 593 R on July 31 and **597 D / 5,047 R** on September 1 — the
  same numbers, swapped; statewide D/R flips 134,109 / 329,420 → 330,401 /
  134,454 in 31 days. This was confirmed four ways (pdftotext `-layout`, word
  bounding boxes, a visual read of a rendered PNG, and an independent re-fetch),
  so it is a publisher error, not a parsing artifact. **The file does not silently
  un-swap it**; it uses the last internally-consistent report. Ballotpedia
  independently also uses the July file.
* **KS 2026 is 2026-06-01** — a three-month lag. Kansas's July/August/September
  2026 URLs return HTTP 200 with an HTML 404 body, which is worth knowing: check
  the payload, not the status code.
* **KS 2024 is 2026-11-01** because Kansas published no October 2024 file at all.
  The SOS's own cumulative workbook lists identical figures for 2024-10-01 and
  2024-11-01, so the true snapshot is one of those two.
* **NY publishes only February and November.** 2022 and 2024 use November 1;
  2026 uses February 20, which is genuinely the newest.
* **NH 2022 is 2022-08-30**, about two months early — New Hampshire's Party
  Registration History jumps from 2022-08-30 straight to 2023-05-22.
* **ME 2026 is 2026-03-07**, a post-NVRA-inactivation snapshot whose total falls
  ~140k from the 2025-11-04 figure.

### Format changes that break a series

* **Louisiana's `reg_oth` is not comparable across cycles.** In 2022 and 2024 LA
  printed only DEM / REP / OTHER PARTIES, so `reg_npa` is blank and the 816,012 /
  840,874 conflates no-party voters with minor-party registrants. From the
  2026-05-01 file LA breaks out a real `NO PARTY` column, so the 2026 row has
  `reg_npa` 823,028 and `reg_oth` of only 24,516.
* **Iowa's active total is not a continuous series.** Active fell 1,867,161
  (Oct 2022) → 1,606,466 (Oct 2024) while the Grand Total barely moved
  (2,227,640 → 2,234,201). That is list maintenance in the source, not a parse
  error. The Grand Totals, if continuity is what you need, are 2,227,640 /
  2,234,201 / 2,157,732.
* **NM 2026 dropped its Libertarian column** and renamed the no-party category.
  `reg_oth` for that row is the single printed OTHER column, which now appears to
  absorb Libertarians. Do not build a Libertarian series across 2024 → 2026.
* **Oklahoma's 2026 PDF reordered its columns** (Republican first). Anything
  reading these files positionally rather than by label will silently swap D and R.
* Minor-party columns appear and disappear constantly: No Labels exists in ME 2024
  and WY 2024 and is gone by 2026; Kentucky gains a "KY Prty" column in 2026;
  Maryland's LIB and NLM columns vanish; Iowa printed a Libertarian column only
  in 2024; DC stopped printing LIB after 2022.

### Access notes, for whoever refreshes this next

Several of these sources are not fetchable the obvious way. `elections.ny.gov`
(Cloudflare JS challenge, no Wayback), Massachusetts (Imperva), Nevada, Arizona,
`vote.sos.ri.gov` and `www.sos.mo.gov` all block plain clients. Delaware needs
only a browser `User-Agent`. Rhode Island publishes **no static file at all** — its
numbers come from querying the Department of State's own Power BI dashboard
dataset through the Power BI public REST API, which is why RI's rows carry lower
confidence than the rest: they cross-foot exactly, but there is no
human-readable artifact to eyeball. New Mexico's files are served from a RealFile
document widget rather than plain links.

The full evidence trail for every row — verbatim source snippets, per-party
breakdowns, county-row reconciliations and the failed-fetch log — was produced
during collection but is not vendored here; the `source_url` on each row is the
artifact itself.

---

## What is NOT in this file, and why

* **Missouri, Ohio, and the 17 other states that do not register by party.** No
  figure exists. See above.
* **Arkansas 2022 and 2024.** Not published anywhere reachable.
* **Any interpolated, averaged or remembered number.** Every row was read off a
  fetched document. Where a figure could not be fetched, the row is absent rather
  than estimated — which is why Arkansas has one row instead of three.
* **A vote-to-registration conversion factor.** That is a modelling decision, not
  a datum, and the table at the top of this document is the argument for why a
  single one cannot exist.
