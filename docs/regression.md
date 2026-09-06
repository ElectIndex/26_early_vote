# Does the early vote predict the result? — the regression, and why it does not ship

`python -m ev regress` fits a least-squares regression from what the early vote
shows to what actually happened, on 2022 and 2024, and then measures it out of
sample against the null model a reader already has for free. It writes
**`output/regression.csv`** (one row per term per specification) and
**`output/regression_fit.csv`** (one row per state: predicted, actual, residual,
and every candidate feature's value) — its own tables, never into
`ev_state_daily.csv`'s reported columns.

**The headline finding, before anything else.** Across every specification, every
predictor and every cut of the data, the early vote buys **between −0.9 and +0.2
percentage points** of margin accuracy out of sample over the statement *"the
country swung X this year, apply X to every state."* On the pooled 2022+2024
sample **not one of the seven early-vote specifications is even positive** — the
best of them, mail share, is worth **−0.05 points**. The largest gain anywhere in
the data is **+0.17 points**, on twelve 2024 presidential states, from one of
eight specifications tried. For comparison, `docs/party-estimate.md` recommended
**against** shipping a model that bought **0.79** points over its own constant.

The specifications *do* beat the cruder null — "quote this state's last
comparable result and stop" — by one to two and a half points. **All of that is
the national swing, and none of it is the early vote**: an intercept-only fit,
which contains no early-vote data whatsoever, beats that same null by 1.94
points on the same rows.

**Recommendation: do not ship it.** Nothing has been added to the site. Read
[What it actually measured](#what-it-actually-measured) and
[Should this ship](#should-this-ship-recommendation).

---

## Running it

```bash
python -m ev regress                    # fit, score, and publish both tables
python -m ev regress --dry-run          # print the score table, write nothing
python -m ev regress --cross-cycle      # ...and fit on one cycle, test the other
python -m ev -v regress --dry-run       # ...and name every case that was dropped
python -m ev regress --cycle 2024
python -m ev regress --state NC OH IA
```

It is a separate subcommand and deliberately **not** part of the daily `ingest`
walk, for the same reason `estimate` is not: keeping it out means the scheduled
job cannot accidentally publish a model number, and `ev.regress` is imported
inside the CLI's dispatcher so `ingest` never even loads it.

It reads `output/ev_state_daily.csv` and `output/results_state.csv`, plus the two
MEDSL files `ev results` already downloads (cached; `--refresh` re-fetches) for
the prior-cycle anchors. It reproduces the numbers below only after the history
is in:

```bash
python -m ev backfill --cycle 2022
python -m ev backfill --cycle 2024
python -m ev regress --cross-cycle
```

The exact slice those two commands produced on 2026-09-06 is saved verbatim in
`tests/fixtures/regress/`, and `test_measured_finding_is_pinned` re-derives every
number on this page from it. If a future data pull moves the finding, this
document fails a test rather than quietly going stale.

---

## What is being fitted

### The target is the swing, not the margin

```
target  =  margin(this race)  −  margin(the same state's last comparable race)
```

Both in percentage points of `dem_share − rep_share`, Democratic positive.
"Comparable" is the same office's previous election: president four years back,
Senate six — the same seat class, so 2018 for a 2024 Senate race and 2016 for a
2022 one.

Regressing the *level* of a state's margin on anything at all produces a huge
R², because states differ from each other far more than early votes differ from
each other; the fit reads "this is Wyoming" and reports it as insight.
Differencing against the state's own last comparable result removes that, and —
the reason it is the honest choice — it makes the null model exactly the constant
zero, which is a thing a reader can check.

The prior margin comes from the same two MEDSL files `results.py` publishes 2022
and 2024 from (`1976-2024-president.csv`, `1976-2024-senate-state.tab`), parsed
for the earlier cycle through the same `results.parse`. Nothing is written back:
`results_state.csv` carries 2022 and 2024 and keeps doing so.

**One caveat, stated because it is real.** For a state that ran a Senate special
election, "six years back" is the same seat *class*, not necessarily the same
seat; `results.parse` prefers the regular race over a concurrent special, which
keeps the anchor on the seat the whole state was voting on but does not guarantee
it is the same office-holder's seat. Georgia 2022 (Warnock, class 3) is anchored
to 2016 (Isakson), not to the 2020 special.

### The two null models

| | |
| --- | --- |
| **null: prior result** | `swing = 0`. "Quote this state's last comparable result and stop." Costs nothing, needs no data. Its MAE is the mean absolute swing. |
| **null: uniform swing** | "The country swung X this year; apply X to every state." Computed leave-one-out, so it never sees the state it is predicting. |

The second null exists because the first is easy to beat for the wrong reason.
**Any** regression fitted on a cycle learns that cycle's national swing in its
intercept, and would beat `swing = 0` without a single early-vote number
contributing anything. A specification that does not beat *both* has measured
nothing about the early vote. This is the whole test.

### The predictors

All six are derivable from what the tracker already publishes. Each is here
because it is a sentence a reader can check, and the specifications use one or
two at a time — a kitchen sink fits better and means less.

| feature | definition |
| --- | --- |
| `early_share_of_total` | the state's completed early vote ÷ the votes eventually cast in that race, ×100 |
| `ev_party_margin` | (D − R) ÷ (D + R) of the party-registered ballots returned. Blank where the state does not register by party |
| `mail_share` | mail ÷ (mail + in-person) of the ballots returned |
| `ev_party_margin_swing` | the same, minus the same state's figure at the **same days-to-election** last cycle |
| `mail_share_swing` | likewise |
| `pace_vs_prior` | ballots returned as a % of the same state's count at the same days-to-election last cycle, minus 100 |

Cross-cycle comparison aligns on **days to election, never calendar date** —
Election Day moves (Nov 8 2022, Nov 5 2024, Nov 3 2026), so a date join silently
compares two different points of two different campaigns. The match allows three
days of slack and takes the closest day inside it.

### Three rules the sample construction obeys

1. **A state that does not register by party has no `ev_party_margin` — blank,
   not zero** — and is dropped from any specification that uses it rather than
   imputed to a mean. THE BLANK RULE, applied on the read side.

2. **A series that stops weeks before Election Day has no usable snapshot at
   all.** The composition of returned ballots moves enormously across a window
   (North Carolina's 2024 returns were 82% registered-Democratic at 45 days out
   and 49% at the close — see `docs/party-estimate.md`), so a party mix read
   eleven days out in South Dakota is not the same object as one read on Election
   Day in Ohio. `SNAPSHOT_MAX_DTE = 7` days is the cut: wide enough to keep
   Maryland, Kentucky, Texas and Tennessee, whose early-vote windows genuinely
   *close* four or five days out, and narrow enough to drop South Carolina 2022
   (stops at 18 days) and Arizona's archived page (a July snapshot).

   The same gate applies to `early_share_of_total`, and it has to.
   `publish.derive_prior_finals` asks whether a series has a row *dated* Election
   Day, not whether that row reported anything — South Carolina 2022 has such a
   row with a blank total and stops counting eighteen days out at 16,975 ballots,
   which would publish a 1.0% "early share" for a state that early-voted in the
   hundreds of thousands. Blank is the answer; a number that wrong is worse than
   none.

3. **A method split that does not add up is refused.** North Carolina's own 2024
   file reports 297,034 mail ballots, `inperson = 0`, and a headline of 4,520,768
   — its one-stop early votes are simply not in that column. "100% mail" would be
   a confident wrong number; blank is the honest one.

Every dropped case is counted, and `python -m ev -v regress` names each one.

---

## The sample

Assembled from the full 2022 + 2024 backfill: **37 state-cycle-office cases**,
from 25 distinct state-cycles.

| | 2022 | 2024 |
| --- | ---: | ---: |
| cases | 5 | 32 |
| mean swing | **+2.99** | **−6.22** |
| mean absolute swing | 6.62 | 6.32 |

The 2022 side is five rows — Kentucky, Maryland, North Carolina, Ohio, South
Carolina — because only nine states have any archived 2022 daily file at all and
four of those (Maine, Tennessee, Texas, Virginia) had no Senate race that year.
Alaska 2022 is dropped for a different reason: MEDSL records no party for any
candidate in that ranked-choice race, so it has no margin, and a blank margin is
missing, not zero. The other 79 dropped rows are states with a published result
and no early-vote series.

Feature coverage across the 37 cases:

| feature | cases with a value |
| --- | ---: |
| `mail_share` | 22 |
| `early_share_of_total` | 21 |
| `ev_party_margin` | 17 |
| `pace_vs_prior` | 13 |
| `mail_share_swing` | 8 |
| `ev_party_margin_swing` | 5 |

The swing features are thin for a structural reason that will not improve before
2026: computing a 2024 swing needs a 2022 curve for the same state, and only nine
states have one. Computing a 2022 swing would need 2020 curves, which do not
exist anywhere in this repo — the UF Election Lab's `/2020/` and `/2022/` files
predate its 2023 site rebuild and 404.

---

## The result

All figures are **mean absolute error in percentage points of margin**. `LOO MAE`
is leave-one-out: each state predicted by a fit that has never seen it. The two
right-hand columns are the whole point.

| specification | predictor | n | in-sample R² | in-sample MAE | **LOO MAE** | null: prior result | null: uniform swing | gain vs prior | **gain vs uniform** |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `uniform` | — (intercept only) | 37 | 0.000 | 4.30 | **4.42** | 6.36 | 4.42 | 1.94 | **0.00** |
| `mail` | `mail_share` | 22 | 0.181 | 3.80 | **4.27** | 6.21 | 4.23 | 1.93 | **−0.05** |
| `share` | `early_share_of_total` | 21 | 0.053 | 4.05 | **4.56** | 6.01 | 4.29 | 1.46 | **−0.27** |
| `party` | `ev_party_margin` | 17 | 0.016 | 4.79 | **5.50** | 6.49 | 5.16 | 0.99 | **−0.34** |
| `party_swing` | `ev_party_margin_swing` | 5 | 0.525 | 4.81 | **8.77** | 6.60 | 8.39 | −2.17 | **−0.38** |
| `mail_swing` | `mail_share_swing` | 8 | 0.007 | 2.82 | **3.84** | 5.35 | 3.35 | 1.51 | **−0.49** |
| `pace` | `pace_vs_prior` | 13 | 0.266 | 4.91 | **5.85** | 7.49 | 5.19 | 1.65 | **−0.65** |
| `party_and_share` | both | 10 | 0.116 | 3.88 | **5.64** | 5.60 | 4.75 | −0.04 | **−0.89** |

Read the `gain vs prior` column and the `gain vs uniform` column together. Every
specification appears to buy one to two points over "quote the last result" — and
so does the intercept-only fit, which contains no early-vote data at all. Once
that is netted out, **all seven early-vote specifications are worse than knowing
nothing but the national swing**, by between 0.05 and 0.89 points.

### Coefficients

```
uniform          intercept −4.9724 (se 1.0545, t −4.72)
share            intercept +0.1612 (se 4.4563, t +0.04);  early_share_of_total  −0.0827 (se 0.0803, t −1.03)
mail             intercept −7.7790 (se 1.9832, t −3.92);  mail_share            +0.1050 (se 0.0499, t +2.11)
party            intercept −5.7044 (se 1.9221, t −2.97);  ev_party_margin       +0.0542 (se 0.1096, t +0.49)
party_swing      intercept −24.5710 (se 10.7859, t −2.28); ev_party_margin_swing −1.7901 (se 0.9840, t −1.82)
mail_swing       intercept −5.4304 (se 2.7611, t −1.97);  mail_share_swing      −0.0419 (se 0.2077, t −0.20)
pace             intercept +1.7582 (se 4.8209, t +0.36);  pace_vs_prior         −0.0829 (se 0.0415, t −1.99)
party_and_share  intercept −6.8058 (se 7.5256, t −0.90);  ev_party_margin       +0.1348 (se 0.1694, t +0.80);
                                                          early_share_of_total  +0.0222 (se 0.1228, t +0.18)
```

Exactly one predictor clears |t| = 2 in sample: `mail_share`, at +0.105 points of
Democratic swing per point of mail share. It is the most tempting number on this
page — the story writes itself, mail voting is Democratic — and it is worth
−0.05 points out of sample. `pace_vs_prior` is next at t = −1.99 and is worth
−0.65. Two marginal t-statistics out of eight tries on samples of 13 to 22 is
what noise looks like.

The residual standard error is 4.1 to 7.7 points of margin depending on the
specification (5.75 for `mail`, 6.41 for the intercept alone). A one-sigma band
on a predicted margin is therefore about ±6 points — wider than the outcome of
every competitive race in the sample.

Every intercept is doing the real work, and every intercept is the same number:
the country swung about five points toward the Republicans across this sample.

### Fit on one cycle, predict the other

This is the test that matters most for October 2026, because 2026 is a cycle no
fit has seen. It is catastrophic.

```
specification      direction              n    model    null     gain
uniform            fit 2022 -> test 2024  32    9.21    6.32    −2.89
uniform            fit 2024 -> test 2022   5   10.35    6.62    −3.73
share              fit 2024 -> test 2022   2   13.99    8.61    −5.38
mail               fit 2022 -> test 2024  19    8.81    5.80    −3.01
mail               fit 2024 -> test 2022   3   10.17    8.77    −1.41
party              fit 2022 -> test 2024  14    6.95    6.59    −0.35
party              fit 2024 -> test 2022   3    8.54    6.01    −2.53
party_and_share    fit 2024 -> test 2022   1    7.70    2.47    −5.23
```

Not one direction of one specification beats even the crude null. A fit trained
on 2022 predicts 2024 **worse by 0.4 to 3 points** than saying nothing, and one
trained on 2024 predicts 2022 worse by 1.4 to 5.4. That is not a subtle
overfitting problem: it is the intercept, the only part carrying any signal,
being trained on a cycle that swung +3 and applied to one that swung −6. A model
whose one useful parameter is last year's national swing is a model that cannot
be pointed at 2026.

### Robustness

The conclusion does not move under any cut. `gain vs uniform`, best
specification, in points:

| sample | n cases | best gain vs uniform |
| --- | ---: | ---: |
| pooled 2022 + 2024 | 37 | **−0.05** (`mail`) |
| 2024 only | 32 | **+0.02** (`mail`) |
| 2024 president only | 20 | **+0.17** (`mail`, n=12) |
| excluding Texas (partial county coverage) | 35 | **+0.09** (`pace`) |
| tier-1 state scrapes only, no aggregator snapshots | 25 | **+0.05** (`mail`) |

The single best number the early vote produces anywhere in this data is **+0.17
points of margin**, on twelve states, on one office, in one cycle, from one of
eight specifications tried.

`output/regression.csv` is written from whatever `output/` holds when the command
runs, which is not necessarily the fixture above. As published today the two
agree row for row — the 2022 and 2024 backfills are complete for every state that
has an archive. Every partial state of that backfill checked along the way landed
in the same place: best gain against the uniform null between −0.05 and +0.41
points, and `NOTHING SHIPS`. The command prints that verdict itself, so it does
not have to be taken from this page.

---

## What it actually measured

**The regression is a national-swing detector wearing an early-vote costume.**
That is the finding, and it is the same shape as the one in
`docs/party-estimate.md`: a model that looks like it is reading live data is
actually restating something already known.

The mechanism is visible in the table. `uniform` — a single number, the mean
swing, with no early-vote input at all — has a leave-one-out error of 4.42
points. Every early-vote specification lands between 3.84 and 8.77 on its own
subsample, and the ones that land low do so on subsamples where the *uniform
null also lands low*. Once each specification is compared to the uniform null **on
its own rows**, which is the only comparison that is not rigged, the early vote
contributes nothing distinguishable from zero.

There are three reasons to expect exactly this, and all three are structural
rather than fixable by a better fit:

1. **The early vote measures who voted, not who they voted for.** Only 17 of 37
   cases have a party breakdown at all, and a party *registration* breakdown is
   not a vote — Kentucky's early ballots were 2.6 points more Democratic than
   Republican by registration in 2022, in a race the Democrat lost by 23.6. This
   is the same gap `docs/party-estimate.md` measured at ten points for Kentucky.

2. **Convenience-voting behaviour is mostly law, not politics.** Pennsylvania's
   2024 early share is 28% and Georgia's is 77%; Georgia's early ballots were 7%
   mail and Ohio's 41%. Those are facts about how the two states run elections,
   not about how they were going to vote. Cross-sectionally,
   `early_share_of_total` and `mail_share` are largely reading each state's
   election code.

3. **The one predictor with a real story — the swing in the registration mix —
   has five observations.** `party_swing` is the honest form of the party signal
   and the only one that differences away a state's fixed characteristics. It has
   the highest in-sample R² on this page (0.525) and the *worst* out-of-sample
   error (8.77 against a 6.60 null). Five points, two parameters, one cycle: that
   R² is a straight line through noise.

### The thing that would change the answer

Not a better model. More cycles, and specifically **more state-cycles that have a
curve in two consecutive elections**, which is what `party_swing`, `mail_swing`
and `pace_vs_prior` need and what would let a cycle fixed effect absorb the
national swing so the early vote is measured against something other than the
year it happened in. Today that is 5, 8 and 13 cases. After 2026 it will be
roughly 21 for each, because the tracker is now collecting the curve live for
every state it follows. That is the point at which this is worth re-running —
and `python -m ev regress` will re-run it, on whatever `output/` then holds,
without anyone editing this file.

Two smaller things would also help and are worth recording:

* **The 2022 backfill is nine states**, and four of them had no Senate race. If
  archived 2022 daily files were recovered for more states — Colorado, Iowa,
  Pennsylvania and Wisconsin all publish 2024 archives but not 2022 — the 2022
  side would stop being five rows.
* **`early_share_of_total` needs a completed early vote**, which is the
  `publish.derive_prior_finals` contract: a series that reaches Election Day, or
  a hand-entered figure in `ev_state_meta.csv`. Filling in `ev_2022_total` /
  `ev_2024_total` by hand from official canvasses would add cases that our
  scrape cannot supply.

---

## Should this ship? (recommendation)

**No. Nothing from this feature goes on a page, and nothing has been added to
one.** Specifically:

1. **Do not publish a predicted margin, a predicted swing, or an "early vote
   implies" number in any state.** It would be the national swing with a state's
   name on it. The uncertainty band around any of these fits is about ±6 points
   of margin at one standard error — wider than the outcome of every competitive
   race in the sample — and a band that contains every plausible answer says
   nothing. The measured out-of-sample gain over knowing nothing but the national
   swing is negative on the pooled sample and +0.17 points at its most flattering
   cut; `docs/party-estimate.md` declined to ship at 0.79.

2. **Do not ship the tempting one.** `mail_share` at t = 2.11 with a clean story
   is precisely the number that would get published, and it is worth −0.05 points
   out of sample. It is on this page so that when someone proposes it, the answer
   is a measurement rather than an argument.

3. **Keep the command and both tables.** `output/regression.csv` and
   `output/regression_fit.csv` are the audit trail for this decision. Every state
   in the fit is in the second file with its predicted value, its residual, its
   held-out residual and every feature that went in, so the claim above is
   checkable line by line rather than taken on trust. `python -m ev regress`
   re-derives the whole thing from `output/` on demand, and it prints
   `NOTHING SHIPS` until something does — the bar is
   `MIN_GAIN = 1.0` point against **both** nulls, in code, not in prose.

4. **What the page should say instead is what it already says.** The tracker's
   honest product is the count and the comparison: how many ballots are in, and
   how that compares to the same state at the same number of days out in 2022 and
   2024. That is a fact. "What it implies about the result" is not one, and this
   document is the measurement that says so.

**A note on the honest failure.** The interesting result is not the size of the
error bar. It is that eight specifications, six predictors and 37 cases produce
*no* out-of-sample gain at all over a single national number — while appearing,
if you only read the `gain vs prior` column, to buy one to two points. The gap
between those two columns is the entire finding, and it is the reason the second
null exists.

---

## Where the code is

| | |
| --- | --- |
| `src/ev/regress.py` | the features, the least squares (written out, no numpy), leave-one-out, both nulls, `write()` |
| `src/ev/cli.py` | the `regress` subcommand (that block only) |
| `tests/test_regress.py` | 33 tests |
| `tests/fixtures/regress/` | the real 2022 + 2024 backfill slice, the results table, and the prior-cycle anchors |
| `output/regression.csv` | coefficients, standard errors, n, R², and both out-of-sample comparisons, per specification |
| `output/regression_fit.csv` | per state: predicted, actual, residual, held-out residual, and every feature |

Three tests carry the load. `test_write_touches_only_its_own_two_files` hashes
every file in an `output/` tree before and after a publish and asserts that the
only things that changed are `regression.csv` and `regression_fit.csv`.
`test_measured_finding_is_pinned` re-derives every number in the result table
above from the saved slice. `test_nothing_beats_the_null_which_is_what_the_document_says`
asserts the headline claim directly — if an early-vote predictor ever buys more
than 0.2 points out of sample, that test fails and this recommendation has to be
revisited rather than inherited.
