# Party estimate — a model for the states that report no party, and its error

Ten of the states this tracker follows publish no party breakdown of their early
ballots. For nine — **GA, IL, MI, OH, SC, TN, TX, VA, WI** — that is because the
state does not register voters by party at all, so no such breakdown exists
anywhere. **Arizona is the tenth and a different case**: Arizona *does* register
by party, its Secretary of State's daily table simply omits it.

`python -m ev estimate` models the partisan composition of those returns from
the geography we do have — county-level early-vote counts — and writes it to
**`output/party_estimate.csv`**, its own table, never into the `party_dem` /
`party_rep` / `party_npa` / `party_oth` columns of `ev_state_daily.csv` or the
county files. Those columns mean *"the state reported this"*, and that is the one
guarantee the whole pipeline rests on.

**The headline finding, before anything else:** measured against the six states
that do report party registration, this estimate is off by a mean of **5.5
percentage points** (RMSE 6.8, worst 12.1) on the Democratic two-party share, it
is systematically **4.2 points too Republican**, and it beats the null model —
"quote that state's 2024 presidential result and stop" — by **0.79 points**.
Across an entire early-vote window it moves **1.3 points** while the thing it
claims to measure moves **3.3**. It is, to a first approximation, the 2024
election result with a live-data costume on.

Read [What it is wrong about](#what-it-is-wrong-about) before putting any of it
on a page. The recommendation is in
[Should this ship](#should-this-ship-recommendation): **not as a party split.**

---

## Running it

```bash
python -m ev estimate                     # every cycle, every estimable state
python -m ev estimate --state TX OH --dry-run
python -m ev estimate --cycle 2024
python -m ev estimate --validate          # score it against states that DO report
```

It is a separate subcommand and deliberately **not** part of the daily `ingest`
walk. It is cheap and deterministic — a pure function of the CSVs already in
`output/`, no network — but keeping it out of the ingest path means the daily job
cannot accidentally publish a model number, and `ev.estimate` is imported inside
`cmd_estimate` so `ingest` never even loads it.

A state-day is estimated when it has county ballot counts **and** the state's own
row for that day reports no party. That test is made against the published data,
not a hardcoded list: the moment a state starts reporting party, its estimate
stops being produced.

`output/party_estimate.csv` is written only when there is at least one estimable
state-day. An empty run leaves the previous file alone rather than replacing real
rows with nothing.

---

## The method

For a state on a day:

```
est_dem_share  =  Σ_c  ballots_c · dem_share_c   /   Σ_c ballots_c
```

over the counties `c` that reported a ballot count and that we have a partisan
weight for, where

```
dem_share_c  =  votes_dem_c / (votes_dem_c + votes_rep_c)     — 2024 presidential
```

`est_rep_share` is `1 − est_dem_share`; `est_margin` is `2 · est_dem_share − 1`,
positive for a Democratic lead. **These are two-party shares.** They exclude
third-party and unaffiliated voters entirely, which is a second reason they must
not be read as "the split of ballots returned".

Rules the arithmetic obeys, all of them tested in `tests/test_estimate.py`:

* A county with a **blank** `ballots_total` is absent, not zero — the same rule
  as THE BLANK RULE in `schema.py`, applied on the read side.
* A county reporting a genuine **0** counts as reporting and contributes zero
  weight.
* A county FIPS with no partisan baseline is **dropped**, never guessed at.
* A state with nothing weightable produces **no row** — not a 50/50 default, not
  a zero. Absence of data is not a value.
* Counties are keyed by **5-digit FIPS**, never by name. The baseline file labels
  Georgia's `13121` "Campbell" rather than "Fulton"; a name join would have
  dropped Georgia's largest county or, worse, mis-mapped it.

### Where the weights come from

`data/baseline/county_results_2024.csv` — certified 2024 county presidential
returns, **vendored into this repo** so the estimate runs on a bare CI checkout.
It is a byte-for-byte copy, taken 2026-09-06, of the ElectIndex forecast model's
own county file (`forecasts/usa/data/county_results_2024.csv`, sha256
`03accfddddb0bd11741119e9b85262031bd89f8ec59cec165d8db094dc29f254`). Nothing
under `forecasts/` is modified by this feature; it is read once, to copy.

Verified before adopting it, and re-verified by test:

* Its state aggregates reproduce the certified 2024 presidential totals **to the
  vote** — GA 2,548,017 D / 2,663,117 R, NC 2,715,375 / 2,898,423, PA 3,423,042 /
  3,543,308, TX 4,835,250 / 6,393,597.
* Its FIPS set matches the census county list **exactly** for all 50 states plus
  DC, with one irrelevant exception: Alaska, which we do not track, carries the
  pre-2019 Valdez-Cordova `02261` rather than the split `02063`/`02066`.
* `test_baseline_covers_every_county_of_every_tracked_state` fails if a future
  copy is short. A partial baseline would silently drop counties from the
  weighting and bias every estimate toward whichever counties survived.

Pass `--baseline PATH` to score the method against different weights (2022
statewide results, a registration file) without touching the vendored copy.

### Coverage

Two coverage numbers, because they answer different questions and a state can be
complete on one and thin on the other:

| column | meaning |
| --- | --- |
| `coverage_share` | county ballots used ÷ the **state's own reported statewide total** for that day. Falls back to the county sum when the state publishes no statewide figure. Capped at 1.0 — counties summing above the headline is a restatement mismatch, not extra coverage, and it is logged. |
| `coverage_electorate` | the used counties' 2024 two-party votes ÷ the state's. "How much of the state is even in this number." |

The first day of the real North Carolina 2024 slice is the case that shows why
both exist: 74 of 100 counties had reported, but every ballot returned so far was
in one of those 74, so `coverage_share` is 1.00 while `coverage_electorate` is
well under it.

### The uncertainty band

`est_dem_lo` / `est_dem_hi` (and the same band doubled onto the margin, since
margin = 2·share − 1) are the sum of two parts:

1. **A hard bound on the ballots we cannot see.** The published share is a mean
   over the covered fraction *f*; the full-state share is
   `f·share + (1−f)·(whatever the uncovered ballots are)`, and those ballots have
   to come from real counties with real leans. The bound runs from the most
   Democratic to the most Republican county still unaccounted for. It collapses
   to zero at complete coverage. This is a bound, not a confidence interval.

2. **A flat ±10 points of model error**, `MODEL_ERROR`. That is the measured mean
   absolute distance between this estimate and the reported party split of the
   same state's early ballots, across every validation day on which at least a
   quarter of that series' final early vote was in (9.8 points; rounded up). It
   is empirical, not statistical, and it does **not** shrink as more ballots come
   in, because the error is structural rather than sampling noise.

The two are **added**, not combined in quadrature. This feature is dangerous
enough that the conservative arithmetic is the right one.

`confidence` is a label about whether the *inputs* are complete enough for the
band to mean anything — `low` when coverage is under 85%, or under 85% of the
state's counties have reported, or fewer than 50,000 ballots are in, or the
missing counties could move the answer by more than the model's own error.
Otherwise `medium`. **It is never `high`, and there is a test that says so.** No
amount of coverage repairs the assumption underneath.

### Columns

`cycle, state, date, days_to_election, est_dem_share, est_rep_share, est_margin,
est_dem_lo, est_dem_hi, est_margin_lo, est_margin_hi, method, confidence,
counties_used, counties_total, coverage_share, coverage_electorate,
ballots_used, baseline_dem_share, lean_vs_baseline, state_has_party_reg,
source_name, retrieved_at`

Shares and margins are fractions to four decimals, not percentages.
`counties_total` is the census county count, which for Virginia is 133 and
includes its independent cities — the number Virginia itself reports against.
`state_has_party_reg` is carried from `data/meta/states.csv` so the file itself
says whether a real party split exists somewhere for that state; it reads `true`
for Arizona.

---

## What it assumes

**One assumption, stated as a reader can check it:** that the partisan lean of a
county's *early voters* resembles that county's *overall* partisan lean.

That is exactly the assumption that failed in 2020 and 2022, when the two
parties' early-voting habits diverged sharply — Democrats voting early and by
mail at far higher rates than Republicans, then partially reverting. When early
voting is a partisan behaviour rather than a neutral convenience, the people
returning ballots in a county are not a random sample of that county, and no
amount of county-level weighting can see the difference.

Two further mismatches, both of which show up in the numbers below:

* **Registration is not vote.** The only ground truth available is the party
  *registration* of early voters. Kentucky is full of registered Democrats who
  vote Republican; its registration split (44.7% D of two-party) sits ten points
  Democratic of its presidential split (34.5%). That gap is definitional, not a
  modelling failure — but it is precisely the gap a reader will fall into if they
  compare our Ohio number to somebody else's North Carolina number.
* **Two-party only.** Independents and third parties are excluded from both the
  numerator and the denominator.

---

## What it is wrong about

### Measured error against states that DO report party

`python -m ev estimate --validate` runs the identical code path against every
state-cycle in `output/` that has county rows *and* a reported party split. Seven
completed series were available (North Carolina from the published data;
Colorado, Iowa, Kentucky, Maryland and Pennsylvania backfilled for 2024):

| cycle | state | days | est | truth | 2024 base | **final error** | MAE | null MAE | gain | est moved | truth moved |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 2024 | CO | 5 | 55.7 | 52.0 | 55.6 | **+3.7** | 3.3 | 3.8 | +0.5 | 0.8 | 0.4 |
| 2024 | IA | 16 | 43.7 | 48.9 | 43.3 | **−5.2** | 6.7 | 7.4 | +0.7 | 0.7 | 4.2 |
| 2024 | KY | 13 | 34.6 | 44.7 | 34.5 | **−10.2** | 10.6 | 10.5 | −0.0 | 0.4 | 0.9 |
| 2024 | MD | 8 | 64.1 | 63.2 | 64.8 | **+1.0** | 1.8 | 2.9 | +1.1 | 1.0 | 2.8 |
| 2022 | NC | 61 | 49.3 | 55.1 | 48.4 | **−5.8** | 6.3 | 7.0 | +0.8 | 0.5 | 1.7 |
| 2024 | NC | 47 | 48.7 | 49.4 | 48.4 | **−0.7** | 0.9 | 1.1 | +0.2 | 0.5 | 2.0 |
| 2024 | PA | 70 | 50.6 | 62.7 | 49.1 | **−12.1** | 15.5 | 17.9 | +2.4 | 4.9 | 11.2 |

All figures are percentage points of the **Democratic two-party share**. `est`
and `truth` are the last day of the series. `MAE` and `null MAE` are averaged
over the days on which at least 25% of that series' final early vote was in —
before that threshold every method looks terrible, because the returned ballots
are almost all mail and their party mix is nothing like the eventual electorate's
(North Carolina 2024 was 82% D-registered at day 45 and 49% at the close).

**Summary across the seven completed series:**

| | |
| --- | ---: |
| mean absolute final-day error | **5.5 pp** |
| signed mean final-day error | **−4.2 pp** (systematically too Republican) |
| RMSE of final-day error | 6.8 pp |
| worst | 12.1 pp (PA 2024) |
| mean MAE, mature days | 6.4 pp |
| mean MAE of the **null model** (quote the state's 2024 result) | 7.2 pp |
| **what the county weighting buys** | **+0.79 pp** |
| how far the estimate moved across a window | 1.3 pp |
| how far the reported party split moved | 3.3 pp |

An eighth series, North Carolina 2026, appears in `--validate` output today with
a −33.6 pp error. It is three days old, five counties, a few hundred ballots —
the degenerate early-window case, kept in the output because hiding it would
misrepresent what the tracker looks like in September. It is excluded from the
summary above.

### The finding that matters more than the error bar

Look at the "est moved" column. **Every state we track publishes every one of its
counties.** When all counties report, the ballot weights are close to proportional
to county size, so the weighted mean is arithmetically pinned near the state's
own prior result. Across the 2024 backfills of the four no-registration states we
have county history for, `lean_vs_baseline` — the estimate minus the state's 2024
presidential result — never left the corridor −2.8 to +2.1 points across 53
state-days, and averaged 1.3 points in absolute size:

| state | 2024 final estimate | state's 2024 result | `lean_vs_baseline` |
| --- | ---: | ---: | ---: |
| OH | 43.4 | 44.3 | −1.0 |
| TX | 43.2 | 43.1 | +0.2 |
| VA | 54.0 | 52.9 | +1.1 |
| WI | 51.2 | 49.6 | +1.7 |

So publishing "Texas early vote: 43% D / 57% R" is publishing the 2024
presidential result of Texas, restated as if it were a live measurement, with a
0.2-point correction. A reader who sees that next to North Carolina's *real*
"32.4% of returned ballots came from registered Democrats, 33.1% from registered
Republicans" has no way to know that one is a count and the other is a rerun of
the last election.

`lean_vs_baseline` is the only column here that geography actually measures, and
it is honest: *"the counties that have returned ballots so far are 1.7 points more
Democratic, in 2024 presidential terms, than Wisconsin as a whole."* That is a
real statement about turnout geography. It is also a much smaller and much duller
statement than "who is winning the early vote", which is the question the number
will be read as answering.

### When it is most wrong

* **Early in the window.** Before a quarter of the eventual early vote is in, the
  returns are mail-dominated and their party mix is nothing like the final
  electorate's. Errors of 20–35 points are routine there; every series above has
  them. `confidence` is `low` under 50,000 ballots, but low confidence has never
  once stopped a number from being quoted.
* **Where mail voting is partisan.** Pennsylvania is the worst series in the
  table (−12.1 final, 15.5 MAE) for exactly this reason: PA's mail electorate is
  overwhelmingly registered Democratic regardless of which counties it comes
  from. The model cannot see a behaviour that is invisible in geography. It is
  also, tellingly, the only series where the county weighting buys more than a
  point over the null model — because PA's mail usage genuinely does vary by
  county.
* **Where registration and vote have drifted apart.** Kentucky (−10.2) and the
  ancestral-Democratic South generally. This is the error a reader inherits when
  they compare our modelled number to another state's reported registration.
* **In a midterm.** The weights are 2024 *presidential*. The 2026 midterm
  electorate is a different one, and the one midterm series we can score (NC
  2022, −5.8) is eight times worse than the presidential year beside it (NC 2024,
  −0.7).
* **Under partial coverage.** No tracked state is short today, so the coverage
  band is dormant. If a state ever posts a subset of counties, the band opens up
  properly and `confidence` drops — but the point estimate would then be badly
  biased, not merely uncertain, because the counties a state publishes first are
  not a random sample of it.

### Arizona is a special case, and modelling it is the wrong fix

Arizona registers voters by party. The number exists; the Secretary of State's
daily statewide table just does not carry it, and county recorders publish party
breakdowns of their own. An estimate for Arizona is therefore a model standing in
for a fact that could be *fetched*, which is a worse trade than the same estimate
for Ohio — and it is the case most likely to be contradicted in public by the
real figure. Every row carries `state_has_party_reg`, which reads `true` for
Arizona, so the file itself flags it. **The right fix for Arizona is a county
adapter, not this model.**

---

## Should this ship? (recommendation)

**No — not as a party split, and not on the same axis as the states that report
one.** Specifically:

1. **Do not publish `est_dem_share` / `est_rep_share` / `est_margin` as "who is
   voting early" in any state.** They are 90% a restatement of the 2024
   presidential result, and any presentation that puts them beside North
   Carolina's or Nevada's reported party columns invites a false comparison that
   the numbers cannot survive. A ±10-point band on a share is a ±20-point band on
   a margin, which for Wisconsin's 2024 estimate spans D+22 to R+17 — an interval
   that contains every plausible answer and therefore says nothing.

2. **`lean_vs_baseline` is defensible and could ship**, framed as what it is: a
   turnout-geography statistic. *"Ballots returned so far come from counties that
   are 1.7 points more Democratic than Wisconsin as a whole (2024 presidential)."*
   It is small, it is checkable, it is a genuine fact about who is showing up, and
   it does not pretend to be a party split. If anything from this feature reaches
   a reader, it should be this and its companion — how the returning geography
   compares to the same point in 2022 and 2024, which the tracker can compute
   from its own backfills and which carries far more information than the level.

3. **Keep the table.** `output/party_estimate.csv` is worth generating and
   committing even if the page never renders it: it is the audit trail for this
   decision, `--validate` re-derives the error on demand, and if someone later
   proposes putting a party estimate on the page, the answer is a file and a
   number rather than an argument.

4. **The genuinely better fix is more data, not a better model.** Precinct-level
   early-vote counts (some Georgia and Texas counties publish them) would make the
   geographic weight enormously sharper. Method-split weights — mail versus
   in-person, which several no-registration states *do* report — would let the
   model see the behaviour that sinks it in Pennsylvania. Both are real work; both
   would beat any refinement of this arithmetic.

**A note on the honest failure.** The measured error is not the interesting
result. The interesting result is that the county weighting adds 0.79 points over
a constant, which means this method is not really estimating anything: it is
laundering a known election result through today's ballot counts. That is worth
knowing, worth keeping the code for, and worth not shipping.

---

## What shipped, and on what terms

The recommendation above was made, and **overruled: the estimate is on the site.**
That is recorded here rather than quietly edited out, because the argument
against it is the reason the presentation looks the way it does, and anyone
changing that presentation later needs to be able to read what it was defending
against.

It ships under six conditions, and every one of them is enforced in code with a
test behind it rather than left to a style guide.

1. **It is labelled an estimate everywhere it appears.** The badge reads
   `Estimate`, the heading says the state "publishes no party split — this is a
   model of one", and the word *reported* never appears next to one of these
   figures. There is no surface on which the modelled number sits in a column,
   chart or legend that also carries a reported split.

2. **The band ships with the number, always, and it is the headline.** The card
   leads with the interval (`33% – 53%`) set large; the central estimate is
   underneath it in small italic type. `ev-estimate.js`'s `range()` returns
   `null` unless the row carries *both* bounds, and `card()` refuses to draw
   without a range — there is no code path that renders a bare point estimate.

3. **The measured error is beside it, in plain words, at full size.**
   `ERROR_NOTE`, printed verbatim on every surface that shows an estimate, on
   the same red rule the party caption uses:

   > This is a model, not a count. Tested against the states that do report party
   > registration, it lands about 5 points off on average and leans Republican by
   > about 4. Most of what it knows is the state's own 2024 presidential result:
   > weighting by county buys under a point of accuracy over simply quoting that
   > result and stopping.

   It is not a tooltip, not a footnote and not a link. A reader who sees the
   number sees this.

4. **It cannot be mistaken for a reported split without reading.** A reported
   split on this page is a solid stacked navy/red bar in which every pixel of the
   track is filled and the boundary between the two colours *is* the number. The
   estimate is the opposite by construction: a hatched **interval on an axis**,
   in neutral graphite rather than a party colour, on a dashed card, where most
   of the track is empty and the empty part is the point. Flip between North
   Carolina's panel and Ohio's and the difference is visible from across the
   room.

5. **Arizona is excluded from the UI.** `estimate.py` still writes it — the CSV
   is the audit trail, and it flags the row with `state_has_party_reg=true` — but
   `ev-estimate.js`'s `showable()` refuses to draw any row carrying that flag.
   The test is made against the column, not against the string `"AZ"`, so a
   future state in the same position is excluded for the same reason with no code
   change. Arizona's own panel says the split is not reported, and says why we
   will not model it either: the real figure exists at its county recorders, so
   an estimate there stands in for a fact that can be collected and would be
   contradicted the moment somebody collects it.

6. **`lean_vs_baseline` is published alongside, framed as what it is.** Every
   card carries the sentence this document argued was the only defensible output
   — *"the counties that have returned ballots so far are 1.7 points more
   Democratic than Wisconsin as a whole in 2024 presidential terms"* — under the
   heading **What the geography does say**, kept visually apart from the estimate
   so it does not read as a caveat on it.

The Info tab carries the method, the full error table above, the summary
statistics, the "when it is most wrong" list and the Arizona reasoning. Its
numbers are read from `EIEV.estimate.ERROR_TABLE`, the same constant that backs
the note in condition 3, so the published error bar and the sentence describing
it cannot drift apart. `assets/earlyvote/tests/party-estimate.test.mjs`
recomputes the headline error claims from the per-series rows rather than
trusting them, and pins the gate: no band means no card, a reporting state never
gets the model beside its real split, and Arizona is never drawn.

**Nothing about the pipeline's guarantee changed.** The estimate is still written
to `output/party_estimate.csv` and nowhere else, still never to
`party_dem`/`party_rep`/`party_oth`/`party_npa`, and
`test_write_never_touches_the_reported_party_columns` still hashes the whole
output tree on every write to prove it.

---

## Where the code is

| | |
| --- | --- |
| `src/ev/estimate.py` | the model, the coverage arithmetic, the band, `validate()` |
| `src/ev/cli.py` | the `estimate` subcommand (that block only) |
| `data/baseline/county_results_2024.csv` | vendored county weights |
| `tests/test_estimate.py` | 35 tests, including the two that matter |
| `tests/fixtures/estimate/` | real NC 2024 slice — 100-county baseline, six days of county rows, the matching reported state rows |

Two tests carry the load. `test_write_never_touches_the_reported_party_columns`
hashes every file in an `output/` tree before and after a publish and asserts
that the only thing that changed is `party_estimate.csv`.
`test_ground_truth_north_carolina_2024` pins the measured final-day error and the
gain over the null model, so if either number moves, the claims on this page fail
a test rather than quietly going stale.
