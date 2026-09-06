# Arizona's early-vote party split — the survey, and what it costs

Arizona registers voters by party. Its Secretary of State's daily Sent/Accepted
Early Ballots table does not carry the split, which is why
[docs/party-estimate.md](party-estimate.md) singles Arizona out as the one state
that must not be modelled: *"an estimate for Arizona is a model standing in for a
fact that could be fetched… **the right fix for Arizona is a county adapter, not
this model.**"*

This document is the attempt at that county adapter, and its result.

**The finding, before anything else: no Arizona county recorder publishes
early-ballot returns broken down by party to the public, in any format.** Not as
a file, not as a table, not even as a PDF. The data exists — Maricopa's own
sign-in page says who gets it — but the public is not the audience. Arizona's
party columns therefore stay blank, and Arizona's estimate stays a pure model on
exactly the terms every other modelled state gets.

What did get built is the route that will carry the real number the day one is
published, and the arithmetic that decides what a partial number is allowed to
say. That arithmetic is the interesting part and it is in
[The blend](#the-blend-measured-plus-modelled) below.

---

## The survey

Fetched **2026-09-06**, largest county first. Every URL below was actually
requested; the status is what it answered, and `(browser TLS)` marks the ones
that needed `_net.get`'s impersonated retry after a 403. The machine-readable
copy of this table is `az.RECORDER_SURVEY`, and a test asserts every row of it
carries a real status and a date, so no guessed URL can be added to it later.

Arizona is unusually concentrated, so the top six counties are ~90% of it. The
share column is **active registration** from the Secretary of State's own
[July 2026 registration report](https://apps.azsos.gov/election/VoterReg/2026/State-Voter-Registration_July_2026.pdf)
(HTTP 200, 472,326 bytes, 2026-09-06; statewide active total 4,306,554).

| county | share | endpoint | status | what it actually is |
| --- | ---: | --- | --- | --- |
| Maricopa | 59.0% | `elections.maricopa.gov/sitemap.xml` | 200 | The complete site map, 176 URLs. **No early-ballot-returns page exists.** |
| Maricopa | | `elections.maricopa.gov/results-and-data/election-data.html` | 200 | A login. *"Cities, towns, and political parties can use the login feature to download election data from an assigned secure folder."* |
| Maricopa | | `elections.maricopa.gov/results-and-data/election-data/portal.html` | 200 | The portal behind it. Renders "Login Required" to an anonymous client. |
| Maricopa | | `elections.maricopa.gov/results-and-data/election-results.html` | 200 | Canvass and summary PDFs. No early-vote series. |
| Maricopa | | `elections.maricopa.gov/results-and-data/early-ballot-returns.html` | 404 | Checked because it is the obvious name. It is not a page. |
| Maricopa | | `elections.maricopa.gov/results-and-data/early-voting-statistics.html` | 404 | Likewise. |
| Maricopa | | `recorder.maricopa.gov/` | 200 | Every election link is a per-**voter** lookup (request a ballot, track my ballot). No aggregate reporting. |
| Maricopa | | `recorder.maricopa.gov/sitemap.xml` | 404 | No map. The 2022, 2024 and 2026 Wayback captures of this host carry canvass PDFs and lookup pages, and no returns report. |
| Pima | 15.0% | `recorder.pima.gov/VoterStats/EarlyVotingStatistics` | 200 | The closest thing in the state, and not it: one table of **final** early ballots requested/returned per election back to 1996. No party columns, no daily series. |
| Pima | | `recorder.pima.gov/BallotProcessingReports` | 200 | A landing page with no table and no report links — the same during the 2024 general (2024-10-04 capture). |
| Pima | | `recorder.pima.gov/Voter_dashboard_login.aspx` | 200 | Pima's "NEW Voter Dashboard" is a sign-in, like Maricopa's portal. |
| Pinal | 6.7% | `www.pinalcountyaz.gov/elections/` | 200 | Results, polling places, sample ballots. No returns reporting. |
| Pinal | | `www.pinalcountyaz.gov/recorder/` | 200 | Recording services; elections handed off to the Elections Department. |
| Yavapai | 4.0% | `www.yavapaivotes.gov/Home` | 200 (browser TLS) | Yavapai's dedicated elections site. Locations and a per-voter ballot-status lookup. |
| Mohave | 3.5% | `www.mohave.gov/departments/recorder/voter-registration/early-voting-information/` | 200 | Dates and locations for the 2026 general. In-person early voting opens **2026-10-07**. |
| Mohave | | `www.mohave.gov/departments/elections/maps-and-statistics/` | 200 | Registration figures and precinct maps. Registration, not ballots. |
| Yuma | 2.6% | `www.yumacountyaz.gov/…/voter-registration-stats-2025` | 200 (browser TLS) | Monthly registration counts by party. Registration, not ballots. |
| statewide | | `apps.arizona.vote/electioninfo/BPS/68/0` | **403** | The SoS's Ballot Progress page — a Cloudflare managed challenge even through the browser fingerprint, so a **wall, not an absence**. The 2024 archive shows what is behind it: ballots tabulated and left to process per county, *post*-election-day, no party. |
| statewide | | `azsos.gov/elections/results-data/voter-registration-statistics` | 200 | County × party **active registration**, quarterly, as a PDF. Real and citable — it is the source of `az.REGISTRATION_SHARE` — but registration, not ballots. |

Also checked and empty of returns reporting: the Coconino (`coconino.az.gov/107/Elections`),
Cochise (`cochise.az.gov/elections`) and Navajo (`navajocountyaz.gov/506/Election-Results`)
election pages, all 200; the ArcGIS Hub dataset search and the `arcgis.com`
content search for Arizona early-ballot datasets (200, **zero** matching items);
`data-maricopa.opendata.arcgis.com` (200); and the Wayback CDX index, which is
the strongest of these because it sees what a site served during an election
rather than what it serves today — `recorder.maricopa.gov` over the 2022 general
(819 captured URLs), `elections.maricopa.gov` over the 2024 general (277),
`recorder.pima.gov` over 2022–2024 (670), and all of them again over the
2026 primary window. Not one returns report among them.

### What this means

Maricopa's portal is the whole answer in one sentence. The daily early-ballot
return file **exists**, it is machine-readable, and it is distributed to
jurisdictions and political parties through a credentialed folder. That is why
Arizona early-vote-by-party numbers circulate during an election while no public
URL serves them. Getting them is a credentialing question, not a scraping one,
and building a scraper against a login page would be building a fragile thing
that fails closed at the worst possible moment.

**A documented "not machine-readable" is the result.** Arizona joins the modelled
states on the same terms as the rest of them: the full ±10-point band, the same
caveats, and `state_has_party_reg=true` on the row so the file itself says a real
number exists somewhere.

---

## What was built anyway, and why

`RECORDERS` in `src/ev/adapters/az.py` is an empty tuple. Adding one entry —
FIPS, name, URL, the status it was verified at, and a parser — is the entire
change needed to start publishing a real split. Everything downstream of that
entry is written and tested:

| piece | what it guarantees |
| --- | --- |
| `party_bucket()` | Arizona's labels → `normalize`'s four buckets. **PND → `npa`.** An unknown or ambiguous label returns `None` so the caller raises `SchemaDrift`. |
| `parse_party_table()` | Header-matched-by-name parsing of a delimited county file. Blank stays blank; a real `0` stays `0`; an unmappable column is drift. |
| `attach_party()` | Writes the split onto the counties that published one and **onto nothing else**. |
| `coverage()` / `PartyCoverage` | What fraction of the day's accepted early ballots the split actually covers, computed from the SoS's own counts. |
| `blend_party_share()` | Measured counties plus a modelled remainder, banded in proportion. |

### PND is `npa`, and it is the largest mistake available in this state

`normalize.party()` does not recognise the string `PND`. Arizona's "Party Not
Designated" voters are about a third of its electorate — 1,490,185 of 4,306,554
in July 2026 — and they are **unaffiliated**, not a third party. Bucketing them
as `oth` would empty the number every early-vote story leads on and fill a
"minor party" column with a third of Arizona.

The same trap is set by the state's own paperwork. The Secretary of State's
registration report has columns `Democratic | Green | Libertarian | No Labels |
Republican | Other | Total`, and that **`Other` column is the PND column** — 34%
of the state. A parser reading `Other` literally through `normalize.party()` gets
`oth` and is wrong by 1.5 million voters. `az.AMBIGUOUS_PARTY_LABELS` therefore
makes a bare `Other`/`OTH` raise `SchemaDrift` rather than resolve: in Arizona
that word means unaffiliated in one official document and minor parties in
another, and no column header settles which. Ask the recorder; do not guess.

### The coverage rule

**A party split covering 77% of Arizona is not Arizona's party split.** Three
things enforce that, and none of them is a caption:

1. **Counties outside the covered set stay blank.** Not zero. A reader summing
   the county file comes up short by exactly the counties nobody measured, which
   is the honest answer. (`test_a_county_outside_the_covered_set_does_not_become_zero`)
2. **The statewide row's party columns are forced to `None`, unconditionally** —
   including at 15/15 coverage, because even then the counties are a different
   source than the row's own. `ev_state_daily.csv` has no column that can say
   "this covers 11 counties of 15", so a partial split written there would be
   read as Arizona's and summed into a national party total as though complete.
   This is precisely the gate `tx.py` puts on its `TOTAL` row, applied to a
   column instead of a table, and for the same stated reason: *a plausible wrong
   total is worse than a hole, because it looks right.*
3. **The coverage travels with the number.** `PartyCoverage.label()` prints,
   verbatim,

   > `AZ: PARTIAL party split -- 2 of 15 counties (Maricopa County, Pima County), covering 75.8% of the state's accepted early ballots. Not Arizona's split.`

   (75.8% is what Maricopa + Pima actually were on the day the 2024 fixture was
   captured — the figure is read off that day's accepted ballots, so it moves.)
   `attach_party()` logs this at WARNING on every partial run.

The covered share is computed from **that day's accepted ballots**, not from
`REGISTRATION_SHARE`. Early-vote geography is not registration geography, and
using the static share as the denominator is how a partial figure gets quietly
overstated. `REGISTRATION_SHARE` is there only to say in advance roughly how much
of Arizona a given set of recorders would be, and to make that claim checkable
against a citable state document.

---

## The blend: measured plus modelled

This is the piece worth the trouble, and it generalises.

The modelled party split carries a flat **±10 points** because that is the
measured structural error of standing in for a party split with county geography
(`estimate.MODEL_ERROR`, derived in `docs/party-estimate.md`). But that error
applies **only to the ballots the model is actually standing in for.** If
recorders count 77% of Arizona's early ballots and the model covers the other
23%, the blended figure carries ±2.3 points, not ±10:

```
share = f · measured_share + (1 − f) · modelled_share
band  = MODEL_ERROR · (1 − f)
```

where `f` is the fraction of ballots actually counted. At `f = 0` the band is the
full ±10 every other modelled state carries. At `f = 1` it is zero — and then it
is not a model at all. **The uncertainty shrinks in proportion to how much is
measured.** That is a fact about the arithmetic, not a claim that the model got
better, and it is the reason a partial recorder route beats both a pure model and
nothing.

For the six largest Arizona counties, the sizes are:

| covered | share of active registration | modelled remainder | blended band |
| --- | ---: | ---: | ---: |
| Maricopa | 59.0% | 41.0% | ±4.1 pp |
| Maricopa + Pima | 74.0% | 26.0% | ±2.6 pp |
| …+ Pinal | 80.7% | 19.3% | ±1.9 pp |
| …+ Yavapai, Mohave, Yuma | 90.7% | 9.3% | ±0.9 pp |
| nothing (today) | 0% | 100% | **±10 pp** |

`blend_party_share()` returns a `BlendedShare` whose `method` says which régime
it is in, and a renderer must branch on it:

* `"reported"` — every ballot counted. This belongs in the **reported** party
  columns, not in the estimate table.
* `"blend"` — part counted, part modelled. Belongs in the estimate table, with
  `measured_fraction` visible.
* `"model"` — nothing counted. The pure estimate, unchanged terms.

It returns **`None`** rather than a number when the inputs cannot support one: no
modelled share for an uncovered remainder (never assume 50/50), or a claim of
measured ballots with no split to show for it.

### Never blend silently

A blend is still partly a model, so it belongs in `output/party_estimate.csv`
beside the models and **never** in the `party_dem` / `party_rep` / `party_oth` /
`party_npa` columns of `ev_state_daily.csv` or the county files. Those columns
mean *"the state reported this"*, which is the one guarantee the pipeline rests
on.

And a reader must be able to see "77% of these ballots are counted, 23% is
modelled" without reading a caption. `BlendedShare.label()` produces exactly that
sentence. The existing hatched-interval treatment for pure estimates is the right
visual family for a blend; a mostly-measured figure should read as measurably
more solid than a pure model, proportionally — the natural encoding is to fill
`measured_fraction` of the bar solid and hatch the rest, so the picture *is* the
coverage rather than a note beside it.

### What `estimate.py` needs (it is not this module's to edit)

`ev/estimate.py` belongs to another agent, so the blend lives in `az.py` as a
pure, state-agnostic function. To publish a blended row, `estimate.py` would
need:

* **Three columns** in `ESTIMATE_COLUMNS`: `measured_fraction`,
  `modelled_fraction`, and a `method` value of `"blend"` (it already has a
  `method` column, currently a constant).
* **Three fields** on `PartyEstimate`: `measured_fraction: float = 0.0`,
  `measured_dem_share: float | None = None`, and a band that is
  `MODEL_ERROR * (1 - measured_fraction)` instead of flat `MODEL_ERROR`. With
  `measured_fraction` defaulting to 0.0 every existing row is unchanged, which is
  what makes this additive.
* **One read**: the reported `party_dem` / `party_rep` already in the county rows
  for the covered counties — the measured half is data the pipeline already has,
  not a new input.
* `confidence` should keep its ceiling of `"medium"`. A shrinking band does not
  repair the assumption underneath the modelled remainder; it only says the
  remainder is smaller.

The function to call is
`ev.adapters.az.blend_party_share(measured_dem=…, measured_rep=…,
measured_fraction=…, modelled_dem_share=…)`. It should move to `estimate.py`
under a state-neutral name once that module's owner wants it — nothing in it is
Arizona-specific.

---

## This is meant to generalise

Nothing above is about Arizona except the label table and the county list. The
hierarchy it encodes is general:

> **Real reported party beats a blend. A blend beats a pure model. A pure model
> beats nothing.**

Any state whose counties report unevenly gets the same treatment with no new
code: attach the split to the counties that published one, leave the rest blank,
compute the covered fraction from that day's own ballots, keep the statewide
reported columns empty, and band the blend by the modelled fraction. The state's
own label vocabulary is the only thing that has to be written per state — and the
`PND`-shaped question ("what does this state's residual column actually mean?")
is worth asking every time, because the answer is `npa` about as often as it is
`oth`, and getting it backwards is a third of an electorate.

---

## Where the code is

| | |
| --- | --- |
| `src/ev/adapters/az.py` | the SoS route (unchanged, still authoritative for sent/accepted) and THE RECORDER ROUTE below it |
| `az.RECORDER_SURVEY` | the survey table above, machine-readable, with statuses |
| `az.RECORDERS` | **empty**; one entry is the whole change |
| `az.REGISTRATION_SHARE` | county shares from the SoS's July 2026 registration report |
| `tests/test_az.py` | 46 tests; the load-bearing ones are `test_pnd_is_npa_not_oth`, `test_a_county_outside_the_covered_set_does_not_become_zero`, `test_a_partial_split_never_reaches_the_statewide_row` and `test_the_band_shrinks_in_proportion_to_what_is_measured` |
| `tests/fixtures/az/README.md` | why one fixture here is synthetic, and what replaces it |
