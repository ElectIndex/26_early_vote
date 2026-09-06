# Turnout projection — the early-vote pace, and why it does not ship

`python -m ev turnout` projects each state's final 2026 turnout from its
early-vote pace: fit the share of a state's final turnout that had been cast at
each number of days before Election Day, then divide this cycle's ballots by that
fraction. It writes **`output/turnout.csv`**, its own table, never into
`ev_state_daily.csv`'s reported columns.

**The headline finding, before anything else.** Out of sample, across the four
states where both ends of the curve are known in two cycles, the projection is
off by a mean of **54.2% of actual turnout**. "Quote this state's last comparable
election and stop" is off by **31.2%**. A single national scaling factor —
*"turnout moved X this cycle, apply X to every state"*, computed
leave-one-state-out — is off by **5.2%**. **The early-vote pace is ten times
worse than one national number and twice as bad as knowing nothing.**

The mechanism is specific and it is not a modelling failure. The fitted fraction
is a product of two things:

```
fitted_fraction(d)  =  coverage(d)   x   early_share
                       the SHAPE          the LEVEL
```

**The shape transfers. The level does not.** Measured on the five states with a
usable curve in both cycles, the normalised accumulation curve is the same to
within **5.4 points** of that cycle's early vote. Measured on the four states
where the level can be computed at all, the early-vote share of turnout moved
**+27% to +72% relative** between 2022 and 2024. The projection is
`ballots ÷ (shape × level)`, so its relative error is *exactly* the drift in the
level — the half that does not transfer is the whole of the error.

For comparison, `docs/party-estimate.md` recommended **against** shipping a model
that bought 0.79 points, `docs/regression.md` declined one at +0.17, and
`docs/counterfactual.md` declined one at −0.10.

**Recommendation: do not ship the projected turnout.** Ship the thing underneath
it, which is a shape and not a level: *"N% of this state's early vote is in"*.
See [Should this ship](#should-this-ship-recommendation).

**And a second finding that matters as much.** On the day this was written —
2026-09-06, 58 days out — the command produces **zero 2026 rows**. Only four
states can ever get one, and none of them before about fifteen days out. That is
the feature working, not failing, and it is the honest state of the evidence.

---

## Running it

```bash
python -m ev turnout                        # project, roll up, publish
python -m ev turnout --dry-run              # print, write nothing
python -m ev -v turnout --dry-run           # ...and name every refusal
python -m ev turnout --validate                     # score it. Read this first.
python -m ev turnout --validate --both-directions   # ...both ways round
python -m ev turnout --state NC --cycle 2026
```

It is a separate subcommand and deliberately **not** part of the six-hourly
`ingest` walk, for the same reason `estimate`, `regress` and `counterfactual` are
not: keeping it out means the daily job cannot accidentally publish a model
number, and `ev.turnout` is imported inside the CLI's dispatcher so `ingest` never
even loads it. It is a pure function of the CSVs already in `output/` — no
network, deterministic.

`output/turnout.csv` is written only when there is at least one projectable
state-day. An empty run leaves the previous file alone rather than replacing real
rows with nothing.

The slice those commands ran against on 2026-09-06 is saved verbatim in
`tests/fixtures/turnout/`, and `test_the_measured_finding_is_pinned` re-derives
every number on this page from it. If a future backfill moves the finding, this
document fails a test rather than quietly going stale.

---

## The method

For a state on a day *d* days before Election Day:

```
coverage(d)         =  ballots_ref(d)  /  early_final_ref
early_share         =  early_final_ref /  turnout_ref
fitted_fraction(d)  =  coverage(d) x early_share

projected_turnout   =  ballots(d)  /  fitted_fraction(d)
```

`ref` is the same state in the **last comparable election** — four years back,
never two. Everything is matched on **days to election, never calendar date**:
Election Day moves (Nov 8 2022, Nov 5 2024, Nov 3 2026), so a date join silently
compares two different points of two different campaigns.

### The same number written the other way round

```
projected_turnout  =  turnout_ref  x  ballots(d) / ballots_ref(d)
```

The two are algebraically identical and there is a test that says so
(`test_the_projection_equals_the_prior_turnout_scaled_by_the_pace_ratio`). It is
worth writing both ways because the second form is what the model *means*: **this
state's turnout will change from its last comparable election in the same
proportion as its early vote at the same number of days out.** Everything on this
page follows from that sentence, including why it fails.

### Per state, never a national curve

Colorado has 90%+ of its vote in before Election Day and New York has almost
none; Pennsylvania's 2024 early share is 28% and Georgia's is 77%. A pooled curve
is not a model of anything, so there isn't one here. The fitted fraction is the
state's own reported position on that day out, divided by its own certified
turnout — **no pooling, no smoothing, and no fitted parameter at all.** That has
a consequence for the protocol, spelled out under
[Standard of proof](#standard-of-proof).

### The denominator, and its one honest caveat

`total_votes` from `output/results_state.csv` is the votes cast in **one race**,
not the ballots cast in the election: a voter who skips the Senate line is in the
second number and not the first. The gap is roughly 1–2% at the top of the ticket
and larger down-ballot. It is the best denominator this repo holds —
`ev_state_meta.csv` has `turnout_2022_total` and `turnout_2024_total` columns and
they are **empty in all 51 rows**. President is preferred over Senate where both
exist, because it has the smallest undervote.

Filling those two columns by hand from official canvasses is the single cheapest
improvement available to this feature, and it would not change the finding — see
[The thing that would change the answer](#the-thing-that-would-change-the-answer).

### Rules the arithmetic obeys, all of them tested

* A **blank** `ballots_total` is absent, not zero — THE BLANK RULE from
  `schema.py`, applied on the read side. A blank day is simply not in the curve.
* A day on which **nobody has voted yet** projects nothing. Zero ballots divided
  by a fraction is arithmetically a turnout of zero, and that is not a projection
  of no turnout; it is no answer. Florida's first two 2026 rows are exactly this.
* **Post-election rows are dropped from both sides.** A row dated after the polls
  closed counts mail that arrived late, so matching this cycle's Election Day
  against last cycle's canvass would compare a live figure to a certified one and
  call the difference pace.
* A state with no comparable reference produces **no row**. Not a projection
  equal to last time, not a zero.
* Two rows on the same days-out (a restatement) keep the larger figure, **never
  the sum**.

### Four constants, and the reasoning for each

| | | |
| --- | ---: | --- |
| `COMPARABLE_OFFSET` | **4** | a midterm's comparable election is the previous midterm. 2022 and 2024 differ in turnout level by about 45%, so a two-year reference is not a weaker reference, it is a different kind of election |
| `SNAPSHOT_MAX_DTE` | **7** | how close to Election Day a reference series must get before its last figure counts as a *completed* early vote. Same value and reasoning as `regress.SNAPSHOT_MAX_DTE` |
| `DTE_MATCH_TOLERANCE` | **1** | **not** the 3 that `regress.py` allows, and the difference is deliberate — see below |
| `MIN_COVERAGE` | **0.25** | a row is produced only once a quarter of the reference cycle's early vote was in at the matched day |

**Why the tolerance is one day and not three.** `regress.py` matches a
*composition*, which drifts a point or two a day. This matches a *level*, and a
state's cumulative early vote routinely grows 20–40% in a day once in-person
voting opens — so a three-day mismatch is a 60% error in the denominator before
the model has done anything. Kentucky is the worked case: against a single-day
2022 reference, a three-day tolerance makes its projection travel **653%** across
six days. At one day it travels 372%, which is still disqualifying and is at
least the state's fault rather than the tolerance's.

**Why there is a coverage floor.** North Carolina's 2022 curve is **17 ballots at
60 days out**. Dividing a 2026 count by that fraction multiplies a handful of
ballots by a five-figure factor and prints a turnout. A day under the floor is
data we have; it is not an answer.

---

## Which states qualify, and why so few

A reference needs **both ends**: a complete early-vote curve *and* a certified
statewide total, in the same state in the same cycle. For a 2026 projection that
reference has to be **2022**, the only midterm this repo holds.

Nine states have a 2022 daily curve. **Four** have both ends.

| state | 2022 curve | 2022 statewide result | reference? |
| --- | --- | --- | --- |
| **KY** | 1 day, at 4 days out | Senate, 1,477,830 | **yes** |
| **MD** | 8 days, 12 → 5 days out | Senate, 2,002,336 | **yes** |
| **NC** | 61 days, 60 → 0 | Senate, 3,773,924 | **yes** |
| **OH** | 1 day, at 0 | Senate, 4,133,342 | **yes** |
| SC | 8 days, 32 → 18 days out | Senate, 1,695,702 | no — the series **stops 18 days out** at 16,975 ballots, so its last figure is not a completed early vote. Using it as a denominator would understate South Carolina's early vote by two orders of magnitude |
| ME | 121 days, 120 → 0 | — | no — **no 2022 Senate race**, and MEDSL publishes no statewide gubernatorial file (`docs/results-source.md`) |
| TN | 14 days | — | no — same |
| TX | 34 days | — | no — same |
| VA | 1 day | — | no — same, and Virginia had no statewide race at all in 2022 |

So the 2026 product is **four states**, and the coverage floor narrows it
further:

| state | a 2026 row is possible from | why |
| --- | --- | --- |
| **NC** | about **15 days out** | its 2022 curve clears 25% coverage at 14 days out |
| **MD** | about **11 days out** | its 2022 curve is 8 days, 12 → 5 days out |
| **KY** | **5 to 3 days out** | its 2022 curve is one day |
| **OH** | **Election Day** | its 2022 curve is one day, at 0 |

The other thirty-one tracked states **can never get a 2026 projection**, no
matter how much 2026 data arrives, until a 2022 daily backfill and a 2022
statewide turnout figure both exist for them. That is a data problem, not a
modelling one.

---

## Standard of proof

### The two nulls

| | |
| --- | --- |
| **null: prior turnout** | `ratio = 1`. "This state's turnout will equal its last comparable election's turnout." Costs nothing, needs no data. |
| **null: uniform ratio** | "The country's turnout moved X this cycle; apply X to every state." Computed **leave-one-state-out**, so it never sees the state it is predicting. |

The second null exists because the first is easy to beat for the wrong reason —
the same argument `docs/regression.md` makes. Any method that knows roughly how
this cycle differs from the last one beats "assume nothing changed" without the
early vote contributing anything at all. **A method that does not beat *both* has
measured nothing about the early vote.**

### On leave-one-state-out

The model has **no pooled parameter**. The fitted fraction is the state's own
reference curve and nothing else, so holding a state out changes the model for
every other state by exactly zero, and there is a test asserting it
(`test_the_model_has_no_pooled_parameter_so_leaving_a_state_out_changes_it`).
Leave-one-state-out is therefore not the protocol that protects this model —
**the nulls are**, and it is the *nulls* that are computed leave-one-state-out,
which is the generous direction.
`test_the_uniform_null_is_computed_leave_one_state_out` asserts the protocol
itself: it moves one state's certified turnout and checks that a *different*
state's uniform-null score moves, which it could not if the national ratio were
fitted in sample.

### On cross-cycle testing, and the one that cannot be run

The only pair of cycles in this repo is 2022 and 2024 — **a midterm and a
presidential year**. So:

* `fit 2022 → test 2024` and `fit 2024 → test 2022` are both available, and both
  are reported below.
* `fit 2022 → test 2026`, the comparison the feature is actually for, is
  **same-type and cannot be validated at all**, because there is no second
  midterm anywhere in this repo. `ev.calendar.CYCLES` is `(2022, 2024, 2026)`,
  no adapter has ever been pointed at 2018, and `regress.py` records the same
  wall for 2020: the UF Election Lab's pre-2023 archives 404 after its site
  rebuild, and no other 2018 daily source has been found (see
  `docs/coverage-research.md`, which documents several states whose only
  during-season artefact is a dead 2018 feed).

That gap is stated rather than papered over. It is also not an escape: the level
drift measured across cycle types is **27% to 72%**, and for the model to beat
the uniform null the same-type drift would have to be **under about 5%** — the
model's relative error *is* the drift, so the bar is that literal. Nothing in
this data suggests a state's early-vote share of turnout is stable to 5% across
four years, and early voting has grown secularly in every cycle on record.

---

## The result

All figures are **mean absolute error as a percent of actual turnout**, one score
per state, averaged over the days past the coverage floor.

### fit on 2022 (midterm) → project 2024 (presidential)

| state | days | ref days | 2022 early share | 2024 early share | **level drift** | **model** | null: prior | null: uniform | vs prior | **vs uniform** | projection travel | in band |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| KY | 3 | 1 | 22.87% | 38.74% | **×1.694** | 41.0 | 28.8 | 5.0 | −12.3 | **−36.0** | 372% | 67% |
| MD | 6 | 8 | 19.08% | 32.74% | **×1.716** | 95.2 | 34.1 | 5.3 | −61.1 | **−89.9** | 22% | 0% |
| NC | 15 | 61 | 57.97% | 79.32% | **×1.368** | 53.0 | 33.8 | 4.7 | −19.2 | **−48.3** | 25% | 47% |
| OH | 1 | 1 | 35.66% | 45.44% | **×1.274** | 27.4 | 28.3 | 5.9 | +0.9 | **−21.6** | 0% | 100% |
| **mean** | | | | | | **54.2** | **31.2** | **5.2** | **−22.9** | **−48.9** | | **53%** |

```
VERDICT: NOTHING SHIPS (bar is +1.00% of turnout against BOTH nulls; measured -22.9 and -48.9)
```

### fit on 2024 (presidential) → project 2022 (midterm)

| state | days | **model** | null: prior | null: uniform | vs prior | **vs uniform** |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| KY | 1 | 33.3 | 40.4 | 4.6 | +7.1 | **−28.7** |
| MD | 7 | 49.1 | 51.7 | 5.8 | +2.6 | **−43.4** |
| NC | 16 | 34.7 | 51.0 | 5.1 | +16.3 | **−29.6** |
| OH | 1 | 21.5 | 39.5 | 5.4 | +18.0 | **−16.1** |
| **mean** | | **34.7** | **45.7** | **5.2** | **+11.0** | **−29.4** |

Read the last two columns together. In this direction the model **appears** to
buy 11 points over "quote the last election" — and every point of that is the
level of the cycle, not the early vote: a *single national number* buys 40.5 on
the same rows. This is the exact shape of the trap `docs/regression.md`'s second
null exists to catch, and it is why one null is not enough.

### In raw votes, so the size is legible

Each state at its last projectable day:

| state | day out | ballots in | **projected** | interval | actual | miss | null |
| --- | ---: | ---: | ---: | --- | ---: | ---: | ---: |
| **NC** | 0 | 4,520,768 | **7,798,061** | 5,031,007 – 12,086,995 | 5,699,141 | **+2,098,920** (+36.8%) | 3,773,924 (−33.8%) |
| **OH** | 0 | 2,620,750 | **7,349,105** | 4,741,358 – 11,391,113 | 5,767,788 | **+1,581,317** (+27.4%) | 4,133,342 (−28.3%) |
| **MD** | 5 | 994,663 | **5,214,124** | 3,363,951 – 8,081,893 | 3,038,334 | **+2,175,790** (+71.6%) | 2,002,336 (−34.1%) |
| **KY** | 3 | 590,442 | **2,581,768** | 1,395,550 – 4,776,270 | 2,074,530 | **+507,238** (+24.5%) | 1,477,830 (−28.8%) |

North Carolina on Election Day 2024 — the most favourable case in the whole
table, 100% of the early vote in, a 61-day reference curve, complete county
coverage — is out by **2.1 million votes**.

### The three things in that table that decide it

**1. The predictor varies six times as much as the target.** At each state's last
matched day:

| | KY | MD | NC | OH | spread |
| --- | ---: | ---: | ---: | ---: | ---: |
| early-vote pace ratio (the predictor) | 1.747 | 2.604 | 2.066 | 1.778 | **49%** |
| turnout ratio (the target) | 1.404 | 1.517 | 1.510 | 1.395 | **8.7%** |

The thing being predicted barely moves across states. The thing predicting it
moves enormously. Multiplying a tightly-clustered quantity by a wildly-varying
one does not sharpen it; it destroys it. That is the whole of the 5.2% versus
54.2%.

**2. The projection is not stable within one window.** Kentucky's travels 372%
across three days; North Carolina's 25% across fifteen. A number that moves by a
quarter of itself while the underlying election does not move at all is not a
projection, it is a reading of where the state is in its own window.

**3. The interval does not cover.** The published band is **±55%** — the measured
mean error, rounded — and it still fails to contain the certified turnout on
**47% of scored days**, including *every* day in Maryland. A band that is more
than half a million votes wide on Kentucky and still wrong half the time is not a
caveat on the number. It is the number.

### What the shape does, measured separately

The shape needs no certified turnout — only the two curves — so it can be scored
on five states instead of four. `python -m ev turnout --validate` prints this
table beside the one above.

| state | days | mean gap | max gap |
| --- | ---: | ---: | ---: |
| ME | 21 | **2.3p** | 4.6p |
| TX | 10 | **4.3p** | 7.1p |
| NC | 16 | **6.1p** | 9.8p |
| MD | 7 | **6.2p** | 8.7p |
| TN | 12 | **8.2p** | 11.3p |
| **mean** | | **5.4p** | |

Points of that cycle's early vote. **Maine's 2022 and 2024 accumulation curves
are the same curve to within 2.3 points, across a 121-day window, spanning a
midterm and a presidential year.** This is a real and slightly surprising result:
*when* a state's early voters vote is a stable property of the state, and it
survives a change of election type that moves the level by half.

It is also the half of the model that is not the answer.

---

## The uncertainty band

`projected_lo` / `projected_hi` are **multiplicative**, not symmetric:

```
lo  =  projected / (1 + h)          hi  =  projected x (1 + h)

h   =  MODEL_ERROR (0.55)  +  DAY_GAP_ERROR (0.30) if the matched day is not exact
```

Multiplicative because the error is a ratio: the projection is
`ballots ÷ fitted_fraction`, so getting the fraction wrong by a factor scales the
answer by that factor, and a symmetric ±x% band would be too tight on the high
side and too loose on the low side of the same misjudgement.

`MODEL_ERROR = 0.55` is the measured 54.2%, rounded up. It is **empirical, not
statistical**, and it does **not** shrink as ballots come in, because the error is
not sampling noise — it is the early-vote share of turnout moving between two
elections, which is a fixed property of the pair of cycles rather than of how much
has been counted. `test_model_error_matches_the_measured_validation` refits it
from `output/` and fails if the data moves away from it.

It is also a **cross-type** measurement standing in for a same-type one, because
this repo holds no same-type pair to measure. Saying so is the honest option;
quoting a tighter band nobody has measured is not one.

`confidence` is a label about whether the *inputs* are complete enough for the
band to mean anything — `low` when the reference is a cross-type one, or has
fewer than four days, or the matched day is not exact, or coverage is under the
floor. Otherwise `medium`. **It is never `high`, and there is a test that says
so.** No amount of coverage repairs a method that does not beat its null.

---

## Columns

```
cycle, state, date, days_to_election, ballots_so_far,
reference_cycle, reference_date, reference_days_to_election, reference_kind,
reference_ballots, reference_early_final, reference_turnout, reference_office,
coverage, reference_early_share, fitted_fraction,
projected_turnout, projected_lo, projected_hi, projected_final_early,
null_turnout, projected_vs_null,
actual_turnout, error_pct, null_error_pct,
dims_used, dims_reported,
reference_days, reference_complete, day_gap,
confidence, method, source_name, retrieved_at
```

Counts are whole votes. `coverage`, `reference_early_share` and
`fitted_fraction` are fractions to six decimals; `projected_vs_null`, `error_pct`
and `null_error_pct` are percentages.

Three of these are worth pointing at:

* **`coverage` × `reference_early_share` = `fitted_fraction`.** The two halves are
  published apart so a reader can see which one is the shape and which one is the
  level, and there is a test asserting the identity.
* **`null_turnout`, `actual_turnout`, `error_pct`, `null_error_pct`.** Every row
  carries the null's prediction, and every row for a cycle that is already
  certified carries the truth and both errors. The table says how wrong it was on
  its own face. `actual_turnout` and the two error columns are **blank** — never
  zero — for a cycle still in progress.
* **`dims_used` vs `dims_reported`.** `dims_used` is `ballots_total|days_to_election`
  on every row: one dimension, the statewide cumulative count, on the days-out
  axis. `dims_reported` is what the state actually publishes beside it —
  `county|party|method|age|race|sex` for North Carolina. **The distance between
  those two columns is an honest summary of how little of the data this model
  uses**, and the same observation `docs/counterfactual.md` makes about itself.

### What fills the table today

`output/turnout.csv` holds **25 rows, all of them 2024 projected off 2022**, all
labelled `reference_kind = cross-type` and `confidence = low`, and every one of
them carrying its own `actual_turnout` and `error_pct`. It is an **audit trail,
not a forecast**, and the rule in code makes that structural:

* A cycle still **in progress** may only be projected from the previous election
  of the **same kind**. Where no same-type reference exists there is **no row**.
  `test_a_cycle_in_progress_never_uses_a_cross_type_reference` pins it.
* A cycle already **certified** may be audited off the nearest available
  reference, because such a row carries the truth beside the projection and
  cannot be read as a forecast.

`--cross-type` lifts the first rule so the measurement can be reproduced. It is
off by default and this paragraph is why.

### The national roll-up

`python -m ev turnout` prints a roll-up and it is **never** presented as a
national total. The line names how many states qualified out of how many are
tracked, in the same breath as the number:

```
2024 ROLL-UP over the 4 qualifying state(s) of 35 tracked -- NOT a national
total: 22,943,058 [14,531,867 - 36,336,271], against 11,387,432 in the
reference cycle
```

(The four states' actual 2024 turnout was 16,579,793. The roll-up is out by 6.4
million votes, which is the same finding summed.)

---

## When it is wrong

* **Whenever the early-vote share of turnout has moved**, which is always, and
  which is the entire error. It moved +27% to +72% between 2022 and 2024 in the
  four states where it can be measured at all.
* **In a midterm projected off a presidential year, or the reverse.** 54% and
  35% of turnout respectively. This is refused in code for a cycle in progress.
* **Against a one-day reference curve.** Kentucky and Ohio have a single 2022
  row each, so "coverage" is 1.0 by construction on the matched day and the
  projection is a bare pace ratio with no shape information in it at all.
  `reference_days` is on the row and `confidence` is `low`, but low confidence
  has never once stopped a number from being quoted.
* **Early in a window.** Below the 25% coverage floor no row is produced,
  because the division is a four-figure multiplication of a handful of ballots.
  North Carolina 2026 today is eight ballots against a 2022 curve that held
  seventeen at the same point.
* **Where a state changed its voting law between cycles.** This is a signal, not
  noise, and the table surfaces it: a large `reference_early_share` against a
  very different current pace is exactly what a law change looks like. Maryland
  is the live example in this data — its tracked early vote is in-person centres
  only, its mail is a separate figure it does not publish here, and its early
  share went from 19.1% to 32.7% in one cycle. It is also the state the model is
  worst on, at 95.2%.
* **Against a `total_votes` denominator that is a race total.** A state where
  many voters skip the top race has a real turnout above this number, so every
  `early_share` here is very slightly high and every projection very slightly
  low. It is a 1–2% effect against a 54% error.

---

## Should this ship? (recommendation)

**No — not the projected turnout. Yes to the shape underneath it.**
Specifically:

1. **Do not publish `projected_turnout`, `projected_lo`/`projected_hi`, or "on
   track for N votes" in any state.** It is worth −22.9 points against "assume
   nothing changed" and −48.9 against one national number, out of sample, in
   both directions. Its honest band is ±55% and even that fails to contain the
   truth on 47% of the days it can be checked against. On the single most
   favourable row in the data — North Carolina, Election Day, complete coverage,
   a 61-day reference — it is out by 2.1 million votes. A turnout projection is
   quoted and remembered, and this one would be remembered as wrong.

2. **`coverage` is defensible and should ship**, framed as what it is — a
   statement about the *shape*, not the level:

   > **About 31% of North Carolina's early vote is in.** At this point in 2022 —
   > fourteen days before Election Day — 668,913 of the state's eventual
   > 2,187,856 early ballots had been returned.

   That is the half of the arithmetic that was measured to transfer, to within
   5.4 points of the early vote across five states and two different kinds of
   election. It needs no certified turnout, so it is available for **nine**
   states rather than four. It must be labelled *early vote*, never *turnout* —
   the whole finding on this page is that the step from one to the other is where
   everything goes wrong.

   `projected_final_early` is the same statement as a count and is published on
   the row for the same reason.

3. **Say the level explicitly, as history rather than projection.** *"North
   Carolina cast 58% of its 2022 votes early"* is a fact about 2022 and a useful
   one. *"So 2026 will be 58% too"* is the assumption this document declines, and
   the two sentences must not be run together.

4. **Keep the command and the table.** `output/turnout.csv` is worth generating
   and committing even if no page renders it: it is the audit trail for this
   decision, `--validate` re-derives the error on demand, and
   `test_nothing_beats_the_null_which_is_what_the_document_says` fails if a
   future backfill ever makes the method clear `MIN_GAIN` — so the
   recommendation gets revisited rather than inherited. The command prints
   `NOTHING SHIPS` itself, so the verdict does not have to be taken from this
   page.

### The thing that would change the answer

Not a better model. Three data things, in order of how much they would help:

* **A second midterm.** Everything above is measured across a change of election
  type, which is the one comparison the feature is not for. After November 2026
  the 2022→2026 pair is a genuine same-type test, and it is the first one this
  repo will ever have. `python -m ev turnout --validate --fit 2022 --test 2026`
  will run it without anyone editing this file. If the same-type level drift
  turns out to be under about 5%, this recommendation is wrong and should be
  reversed; on the evidence available today there is no reason to expect that,
  and one cycle of evidence would still be one cycle.
* **`turnout_2022_total` and `turnout_2024_total`, filled in.** Both columns
  exist in `data/meta/states.csv` and are empty in all 51 rows. Filling them from
  official canvasses would replace a race total with real ballots cast, and —
  more importantly — would give **Maine, Tennessee, Texas and Virginia** a 2022
  denominator, taking the reference set from four states to eight and the
  validation sample from four to eight. It would not change the finding; the
  level drift is 27–72% and eight states of it is still 27–72%.
* **A 2022 daily backfill for more states.** Nine states have one. Colorado,
  Iowa, Pennsylvania and Wisconsin all publish a 2024 archive and not a 2022 one.

**A note on the honest failure.** This model was expected to work where the other
three did not, and the reasoning was sound: turnout is a count, not a preference,
and "ballots returned" and "ballots cast" really are the same quantity at two
times. The arithmetic is exact and the mechanical half of it *does* work — Maine's
accumulation curve is the same curve two years apart to within 2.3 points. What
sank it is one step further on: converting a completed early vote into a total
turnout requires the early-vote **share**, that share is not a mechanical quantity
at all, and it moved by up to 72% in a single cycle. The failure is not "the
signal is weak" (`docs/regression.md`) and not "we are looking where the signal is
not" (`docs/counterfactual.md`). It is: **the model is two multiplications, the
first one is right, and the second one is a guess about the future of a state's
election law and voting habits wearing the costume of a measurement.**

---

## Where the code is

| | |
| --- | --- |
| `src/ev/turnout.py` | the curve, the reference rule, the projection, the band, `validate()`, `shape_transfer()`, `rollup()` |
| `src/ev/cli.py` | the `turnout` subcommand (that block only) |
| `tests/test_turnout.py` | 49 tests |
| `tests/fixtures/turnout/` | the real 2022 + 2024 + 2026 slice: `ev_state_daily.csv`, `results_state.csv`, `ev_state_meta.csv` |
| `output/turnout.csv` | per state per day: ballots in, the fitted fraction and both its halves, the projection, its interval, the prior-cycle actual, and — for a certified cycle — the truth and both errors |

Four tests carry the load, and they are named at the top of the test file.
`test_write_touches_only_its_own_file` hashes every file in an `output/` tree
before and after a publish and asserts that the only thing that changed is
`turnout.csv`. `test_a_cycle_in_progress_never_uses_a_cross_type_reference` pins
the refusal that keeps the measured-wrong arithmetic out of a forward-looking
row. `test_the_measured_finding_is_pinned` re-derives every number in the result
tables above from the saved slice.
`test_nothing_beats_the_null_which_is_what_the_document_says` asserts the
headline claim directly, in both directions.
