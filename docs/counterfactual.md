# The counterfactual — "if this were the 2024 electorate" — and why it does not ship

`python -m ev counterfactual` takes the people who have returned ballots so far
and asks one question about them:

> **If exactly that set of voters had turned out in 2024, and every group had
> voted the way it actually voted in 2024, what result would 2024 have
> produced?**

Not "who wins in 2026". Not "how accurate was 2024". The output is a
*compositional* statement in points of presidential margin — "the early
electorate so far is worth N points more Republican than the one it is being
compared with" — and it is written to **`output/counterfactual.csv`**, its own
table, never into the reported columns of `ev_state_daily.csv`.

**The headline finding, before anything else.** Measured against the only
compositional change anyone can actually observe — the party registration those
same states reported for those same ballots — the method is off by **9.09
percentage points of margin** and the null model *"the composition has not
changed at all"* is off by **9.16**. It buys **+0.07 points**, against a bar of
+1.00. County geography moves **0.95 points** across a window while the
composition it is standing in for moves **9.16**. Fitting a scale factor to close
the gap, leave-one-state-out, makes three of the five states worse and produces
multipliers of **+0.24, +0.40, +2.30, +4.20 and +7.28** — a thirty-fold spread.
A signal in the wrong unit would be fixed by one number. Nothing here is one
number.

For comparison, `docs/party-estimate.md` recommended **against** shipping a model
that bought 0.79 points, and `docs/regression.md` declined one at +0.17.

**And a second finding that matters as much.** On the day this was written —
2026-09-06, 58 days out — the command produces **zero 2026 rows**, because no
2026 state has yet returned enough ballots to be compared with anything. That is
the feature working, not failing. It is also the honest state of the evidence.

**A third, added 2026-09-06, and it was a bug rather than a finding.** This model
is *validated* on mature days and, until that date, *published* on every day.
**181 of the 260 rows in `output/counterfactual.csv` sat outside the domain it
had ever been scored on** — mean |shift| **6.39** points against **1.20** for the
mature rows, as far as **26.2**, and 24 of them carrying the top `confidence`
label, because completeness was not one of the things `confidence` looked at.
Maine published an **11.7-point** compositional shift computed off **two
ballots**. None of that was composition; it was phase. See
[The maturity gate](#the-maturity-gate).

**Recommendation: do not ship the modelled margin.** Ship the thing underneath
it, which is a count rather than a model: the like-for-like *party-registration*
shift. See [Should this ship](#should-this-ship-recommendation).

---

## Running it

```bash
python -m ev counterfactual                 # every state-day with a reference
python -m ev counterfactual --dry-run       # print, write nothing
python -m ev counterfactual --validate      # score it. Read this section first.
python -m ev counterfactual --state NC --cycle 2026
python -m ev counterfactual --baseline PATH # score it against different weights
```

It is a separate subcommand and deliberately **not** part of the six-hourly
`ingest` walk, for the same reason `estimate` and `regress` are not: keeping it
out means the daily job cannot accidentally publish a model number, and
`ev.counterfactual` is imported inside the CLI's dispatcher so `ingest` never
even loads it. It is a pure function of the CSVs already in `output/` — no
network, deterministic.

`output/counterfactual.csv` is written only when there is at least one comparable
state-day. An empty run leaves the previous file alone rather than replacing real
rows with nothing.

---

## The trap this feature exists to avoid

`estimate.py` already publishes a sentence of this shape:

> the counties that have returned ballots so far are 16.7 points more Democratic
> than Georgia as a whole was in 2024

That comparison is **nearly meaningless**, and it is the obvious way to build
this feature. Early voters are a **self-selected subset**. Of course they differ
from the whole state. Setting 2026's early electorate against 2024's *full*
electorate mostly re-measures "early voters are not everyone", which nobody
needed a model to learn.

So the comparison here is like-for-like and only like-for-like:

```
the 2026 early electorate   vs   the 2024 early electorate
                at the same DAYS TO ELECTION
```

Days-to-election, never calendar date: Election Day moves (Nov 8 2022, Nov 5
2024, Nov 3 2026), so a date join silently compares two different points of two
different campaigns. Two series rarely land on the same day out, so the match
allows **three days** of slack and takes the closest day inside it — the same
tolerance `regress.py` uses, deliberately narrow because the composition of
returned ballots moves enormously across a window.

**A state with no early series in the reference cycle gets no counterfactual.**
Not a fallback to the full-state comparison. Not a zero. That refusal is the
single most load-bearing line of code in the module and it has three tests.

---

## The method

For a state on a day *d*:

```
composition_margin(d)  =  Σ_c  ballots_c(d) · margin_c   /   Σ_c ballots_c(d)

shift_pp               =  composition_margin(2026, d) − composition_margin(2024, d')
implied_margin_2024    =  actual_margin_2024 + shift_pp
```

where `d'` is the matched day-out in the reference cycle and

```
margin_c  =  (votes_dem_c − votes_rep_c) / (votes_dem_c + votes_rep_c) × 100
```

from the certified 2024 county presidential returns. Democratic positive,
two-party, in points — the unit the rest of this project uses.

### Why the anchor is the certified result and not the raw composition

`implied_margin_2024` is the certified 2024 margin *moved by the like-for-like
shift*, not `composition_margin` itself. Publishing the raw composition margin as
"the implied result" would be publishing the trap: it differs from the state's
actual result mostly because early voters are not everyone. Both raw figures are
in the table (`composition_margin`, `reference_composition_margin`) so the
subtraction is checkable rather than taken on trust.

### The identity underneath it

Weight every county by its own 2024 two-party votes and this arithmetic returns
the state's certified 2024 margin **exactly** — to 1e-9, in every tracked state,
and `test_the_full_2024_electorate_reproduces_the_certified_margin` asserts it.
The vendored baseline also reproduces `output/results_state.csv`'s two-party
margin to 0.014 points in every state this tracker follows (0.104 in Alaska,
which it does not).

That identity is what makes `shift_pp` a statement about *composition* and
nothing else. It is also the reason this method's failure is interesting: the
arithmetic is not wrong, the *dimension* is blind.

### Where the weights come from

`data/baseline/county_results_2024.csv` — the same vendored, verified county file
`estimate.py` uses, described in `docs/party-estimate.md`. Counties are keyed by
**5-digit FIPS**, never by name.

### Rules the arithmetic obeys, all of them tested

* A county with a **blank** `ballots_total` is absent, not zero — THE BLANK RULE
  from `schema.py`, applied on the read side.
* A county reporting a genuine **0** counts as reporting and contributes zero
  weight.
* A county FIPS with no partisan baseline is **dropped**, never guessed at.
* A state with nothing weightable on either side produces **no row**.
* **Post-election days are dropped from both sides.** A row dated after the polls
  closed counts mail that arrived late, so matching this cycle's Election Day
  against last cycle's canvass would compare a live figure to a certified one and
  call the difference composition.
* 2022 is never a *target* cycle. It has no earlier early electorate in this repo
  to be compared against.

---

## The dimensions, and the one that could be priced

A composition is a set of weights over groups. Valuing one in 2024 presidential
points requires each group's 2024 presidential margin. There is exactly one
dimension for which this repo holds that number.

| dimension | availability | in `shift_pp`? | why |
| --- | --- | --- | --- |
| **county** | every tracked state with a county file | **yes** | certified 2024 county returns are vendored, and the full-electorate identity above holds exactly |
| **party registration** | ~30 states register by party; KY, MD, ME, NC, CO, IA, PA, FL, NV, SD report it on returned ballots | **no** — reported in its own unit | converting registration points to presidential points needs to know how registered Democrats actually *voted* in 2024. This repo does not have it. Kentucky's registration split sits ten points Democratic of its presidential split (`docs/party-estimate.md`), so the conversion is not a detail |
| **age / race / sex** | `output/demo/*.csv` — GA, MD, MI, NC, SC | **no** — reported as a distance | **group-level 2024 presidential behaviour by demographic was not sourced for this feature.** Saying so plainly is the honest option; inventing a citation is not one |

`dims_used` names the dimensions inside the headline number. In every row this
module can write today it reads `county`. `dims_reported` names the dimensions
that exist. **The distance between those two columns is the honest summary of
this feature.**

The unpriced dimensions are still published, because they are measured facts
about who is turning out:

* `party_margin_shift_pp` — the change in (D−R)/(D+R) of the *party-registered*
  ballots, 2026 vs 2024 at the same day out. Registration points. A count.
* `age_shift_tv` / `race_shift_tv` / `sex_shift_tv` — the **total-variation
  distance** between the two cycles' mixes, in points: half the sum of absolute
  differences in bucket share, running 0 (identical) to 100 (disjoint). It says
  *how far* the mix moved and deliberately says nothing about which way that
  cuts. It is blank unless both cycles report the **same bucket vocabulary** — a
  bucket present in one cycle and absent in the other is "not reported", not
  zero, and treating it as zero would manufacture a shift out of a schema change.

### What this deliberately excludes

**Behaviour.** The whole construction freezes 2024 vote choice. If every group
votes five points more Republican in 2026 than in 2024, this number cannot see it
and will not move. That is the definition of the question, not a defect — but it
means the output says **nothing whatsoever about persuasion**.

`estimate.py`'s mail-selection term is the behavioural correction that this
module does not use and must not: it models *how mail voters vote differently
from their county*, which is precisely the thing being held fixed here.

---

## Standard of proof: what can be validated, and what cannot

There is no observed "compositional margin shift" anywhere in the world to check
this against. Nobody publishes what the 2024 early electorate would have done if
it had been the whole electorate; the counterfactual is counterfactual. So the
answerable question is narrower:

> **When the composition of an early electorate demonstrably changed between two
> cycles, does this method see it?**

The states that report the party registration of returned ballots give a directly
measured compositional change for exactly the electorates being compared:
(D−R registration of 2024's early ballots at day *d*) minus (the same for 2022's
at day *d'*). **That is the truth column. County geography is the predictor.**
The two are never both — a party-registration shift is never fed into `shift_pp`
— which is what keeps this test non-circular.

`python -m ev counterfactual --validate` runs the identical machinery on the 2024
early electorate against the 2022 one. Four state-cycles qualify: a state needs a
county early series in **both** cycles *and* a reported party split in both. KY,
MD, ME, NC.

### One honest caveat about the unit, and the two things that stop it being an excuse

A registration point is not a presidential margin point. `estimate.validate`
makes the same compromise for the same reason — it is the only ground truth that
exists, and it is the comparison a reader will make. Two things stop the unit gap
from rescuing the method:

1. **The null is the same object in the same unit.** "The composition has not
   changed" predicts zero, and zero is unit-free, so the *comparison* between
   method and null is fair even if neither error is in a perfect unit.
2. **A fitted scale is searched for, leave-one-state-out.** If a unit gap were
   all that was wrong, a multiplier would fix it. It does not — see below.

---

## The result

All figures are **mean absolute error in percentage points of margin**, over days
that clear [the maturity gate](#the-maturity-gate) on both sides.

| cycle | state | days | shift (final) | truth (final) | **MAE** | **null** | **gain** | mean \|shift\| | mean \|truth\| | r | sign | LOO k | k-MAE | k-gain |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 2024 | FL | 8 | +0.04 | −4.94 | 6.89 | 7.15 | **+0.26** | 0.29 | 7.15 | +0.89 | 62% | +2.30 | 6.55 | +0.60 |
| 2024 | KY | 6 | +1.43 | −13.22 | 7.10 | 6.54 | **−0.56** | 0.70 | 6.54 | −0.84 | 17% | +4.20 | 9.34 | −2.80 |
| 2024 | MD | 6 | +0.70 | −6.28 | 7.16 | 5.60 | **−1.56** | 1.56 | 5.60 | +0.97 | 0% | +7.28 | 16.99 | −11.39 |
| 2024 | ME | 21 | −1.18 | −13.99 | 13.39 | 14.37 | **+0.98** | 0.98 | 14.37 | −0.62 | 100% | +0.40 | 13.98 | +0.39 |
| 2024 | NC | 15 | −1.25 | −11.42 | 10.91 | 12.14 | **+1.23** | 1.23 | 12.14 | +0.60 | 100% | +0.24 | 11.84 | +0.30 |

| | |
| --- | ---: |
| mean MAE | **9.09 pp** |
| mean MAE of the null (composition unchanged) | **9.16 pp** |
| **what county geography buys** | **+0.07 pp** |
| pooled by day rather than by series (56 days) | +0.51 pp |
| how far the modelled shift moves | **0.95 pp** |
| how far the composition it stands in for moves | **9.16 pp** |
| leave-one-state-out fitted scale, mean gain | **−2.58 pp** |

`--validate` prints its own verdict rather than leaving it to this page:

```
VERDICT: NOTHING SHIPS (bar is +1.00 pp of gain over the no-change null; measured +0.07)
```

The bar, `MIN_GAIN = 1.0`, is in code. It is the same bar `regress.py` sets.

### The three things in that table that decide it

**1. It does not beat the null.** +0.07 points on five states against a bar of
+1.00, and it is negative on two of them. `docs/party-estimate.md` declined at
+0.79 and `docs/regression.md` declined at +0.17.

**2. It barely moves.** The modelled shift travels 0.95 points while the measured
composition travels 9.16. This is the same mechanism `docs/party-estimate.md`
found for `lean_vs_baseline`: **every state we track publishes every one of its
counties**, so once coverage is complete the ballot weights are close to
proportional to county size and the weighted mean is arithmetically pinned near
the state's own last result. A difference of two numbers that are each pinned to
the same place is a small number by construction.

**3. A fitted scale makes it worse, and cannot agree with itself.** The
leave-one-state-out multipliers are **+0.24, +0.40, +2.30, +4.20, +7.28** — a
thirty-fold spread — and applying the one fitted on the other states makes three
of the five folds worse, by up to 11.4 points, and lifts none of them over the
bar. If county geography were measuring the right thing in the wrong unit, ONE
number would convert it.

> ⚠️ **Half of this argument used to be stronger, and it is worth recording which
> half was real.** Before the maturity gate the multipliers were **+3.89, +4.13,
> −1.28, −1.61** — *both signs*, which is about as clean a "there is no signal
> here" as a fitted constant can give. Every one of them is positive now. What
> the immature days were contributing was noise with a sign, and removing them
> removed the sign disagreement along with it. The magnitude disagreement is what
> survives, and it is the part that was ever load-bearing.

### Seven specifications, none of which rescue it

Before concluding that the dimension is blind rather than miscalibrated, the
knobs were swept rather than argued about. Gain over the null, on the same folds:

| what was varied | range tried | best gain |
| --- | --- | ---: |
| day-match tolerance | ±0 to ±7 | +0.05 |
| maturity threshold | 0% to 80% | +0.13 |
| county-coverage floor | 0% to 99% | +0.00 (no effect at all) |
| state-coverage floor | 0% to 95% | +0.00 (no effect at all) |
| matching axis (days-out / mail mix / progress) | three | +0.01 |
| stage gate on progress | ±5% to ±50% | +0.06 |
| truth-column denominator (two-party / all-party / all ballots) | three | ±0.00 |

That last row is the one that settles it. Changing the denominator moves how far
the truth travels (10.34 → 8.57 points) and leaves **every per-state gain
identical to two decimals**, because the modelled shift is too small to interact
with it either way. There is no unit to fix and no threshold to tune. The
dimension is blind; `docs/regression.md` reports the same shape of result from
seven specifications of its own.

### What the counterfactual says about 2024, for the record

`output/counterfactual.csv` today holds **78 rows**, all of them 2024-vs-2022.
(It held 260 before the maturity gate; the 182 it lost were the ones outside the
domain the model had ever been scored on.) This is the whole final-day picture:

| state | days | window (d-out) | `shift_pp` | party shift | age tv | race tv | sex tv |
| --- | ---: | --- | ---: | ---: | ---: | ---: | ---: |
| FL | 8 | 13 → 0 | +0.04 | −4.94 | — | — | — |
| KY | 6 | 7 → 2 | +1.43 | −13.22 | — | — | — |
| MD | 6 | 10 → 5 | +0.70 | −6.28 | — | — | 0.7 |
| ME | 21 | 20 → 0 | −1.18 | −13.99 | — | — | — |
| NC | 15 | 14 → 0 | −1.25 | −11.42 | 12.6 | 4.2 | 2.4 |
| OH | 1 | 0 | −2.22 | — | — | — | — |
| TN | 10 | 15 → 5 | −2.33 | — | — | — | — |
| TX | 10 | 13 → 4 | −1.30 | — | — | — | — |
| VA | 1 | 0 | −1.31 | — | — | — | — |

Read the North Carolina row across. County geography says the 2024 early
electorate was worth **1.25 points** more Republican than 2022's. The party
registration of those very same ballots moved **11.42 points** (registration
points, a count, not a vote). The age mix moved **12.6 points** of total
variation. **The one dimension we can price is the one that barely moved.**

South Carolina used to be a tenth row here. It is gone, and its absence is the
maturity gate's clearest single case: SC's 2022 county series tops out at
**16,975 ballots** against **1,579,112** in 2024, so thirteen published rows were
measuring a state against a rounding error.

---

## The maturity gate

**Three refusals, all computable while a cycle is still running**, in
`is_comparable()`:

1. the reference curve is a real early electorate, not a stub
   (≥ `MIN_REFERENCE_BALLOTS`, 50,000);
2. the reference day is itself mature within that curve (≥ `MATURE_FRACTION`);
3. this cycle's day has reached the same share of it.

Both shares are published, as `completeness` and `reference_completeness`, so a
reader can check the gate rather than take it on trust.

### The denominator is the reference cycle's final, and that is the whole point

`mature_days()` divides by `final_ballots()` — the largest a series has *ever*
reached. That is correct for a completed cycle and catastrophically wrong for a
live one: **North Carolina's eight 2026 ballots are 100% of what 2026 has reached
so far**, so a self-referential rule calls them a mature electorate. The
reference cycle's curve is finished, which is exactly why it can be the yardstick
for both sides. It is deliberately not capped at 1.0 — a cycle whose early vote
outruns the last one reads above 100%, which is true and worth seeing.

### What it changed

| | before | after |
| --- | ---: | ---: |
| published rows | 260 | **78** |
| mean \|shift_pp\| | 4.80 | **1.09** |
| max \|shift_pp\| | **26.16** | **3.65** |
| rows above 5 points | 94 | **0** |
| rows labelled `confidence: low` | 157 | **0** |
| validation MAE | 10.37 | **9.09** |
| gain over the null | −0.03 | **+0.07** |

Not one of the 94 rows above five points survived, and the model's honest range
turns out to be **±3.65 points**. The `confidence: low` rows went to zero for the
same reason: almost every one of them was low *because* it was immature, and
`confidence` had no way to say so.

### And a merge that could not forget

Fixing the model was only half of it. `publish_table()` merges by key, which is
correct for a scraped table — a state that fails to answer today must not delete
what it published yesterday — and **wrong for a derived one**. When the gate
landed, the 182 rows it retired had nothing to replace them and would have
outlived the bug that made them. Derived tables (`counterfactual.csv`,
`party_estimate.csv`, `turnout.csv`) are pure functions of data already on disk,
so a full rebuild now passes `replace=True` and rewrites the file. A **filtered**
run (`--state NC`) still merges, because it knows only part of the table.

---

## Which states can have a counterfactual at all

**Two bars, not one.** A 2026 state-day needs (1) the same state's **2024 county
early series at the same days-to-election, ±3**, and (2) both sides through
[the maturity gate](#the-maturity-gate). Sixteen of the thirty-five tracked
states clear the first:

| state | 2024 county days | window (days out) | 2022 series? |
| --- | ---: | --- | --- |
| ME | 121 | 120 → 0 | 121 days |
| PA | 70 | 69 → 0 | — |
| NC | 47 | 46 → 0 | 61 days |
| TX | 34 | 37 → 4 | 34 days |
| SC | 31 | 42 → 0 | 8 days, 16,975 ballots — **a stub** |
| WI | 26 | 34 → 0 | — |
| FL | 24 | 58 → 0 | 17 days |
| KY | 16 | 43 → 0 | 1 day |
| IA | 15 | 19 → 0 | — |
| TN | 14 | 20 → 5 | 14 days |
| MD | 8 | 12 → 5 | 8 days |
| CO | 4 | 8 → 5 | — |
| OH | 1 | 0 | 1 day |
| VA | 1 | 0 | 1 day |
| AZ | 1 | 99 (a July snapshot; nothing will ever match it but a July day) | — |

Florida is on that list because its 2022 and 2024 county curves were recovered
from the Internet Archive (136 captures in 2024, 78 in 2022 — see
`src/ev/adapters/fl.py`). **The same sweep found Illinois genuinely
unrecoverable**: its counts page archives as the empty form and never as the
answer, so IL is one of the nineteen — AK, CA, CT, DE, GA, HI, IL, KS, LA, MI,
MN, MT, ND, NH, NV, NY, OK, OR, SD, WA — that **can never get a counterfactual**
until a 2024 county backfill exists for them, no matter how much 2026 data
arrives.

### Why the table has zero 2026 rows today

2026-09-06 is **58 days out**, and the three states with any 2026 county data are
nowhere near an electorate:

* **NC** — 3 days, and **eight ballots in five counties**. Against North
  Carolina's finished 2024 early vote of 4,520,768 that is 0.0002%. The maturity
  gate refuses it; the naive "vs the whole state" comparison would happily report
  **D+2.79** off those eight ballots, which is exactly the number this module
  exists not to publish.
* **FL** — 2 days, one ballot.
* **IL** — 947,926 mail ballots *requested* and none returned. A request is not a
  vote.

As 2026 windows open, a state gets its first row when it has returned about a
quarter of its own 2024 early vote **and** its 2024 series reaches back that far.
For North Carolina that is roughly 1.13 million ballots; on 2024's curve it
crossed that around 12 days out.

---

## The uncertainty band

`shift_lo` / `shift_hi` are the sum of two parts, **added** rather than combined
in quadrature, because a feature this easy to misread deserves the conservative
arithmetic:

1. **A hard bound on the ballots we cannot see, on both sides.** The published
   figure is a mean over the covered fraction *f*; the rest of the ballots have
   to come from real counties with real margins, so the bound runs from the most
   Democratic to the most Republican county still unaccounted for. A *difference*
   is widest when this cycle sits at one end of its bound and the reference at
   the other, so the two bounds are subtracted crosswise. It collapses to zero at
   complete coverage on both sides. This is a bound, not a confidence interval.

2. **A flat ±9.5 points of model error**, `MODEL_ERROR_PP`. That is the measured
   mean absolute error above (9.09), rounded up. It is empirical, not
   statistical, and it does **not** shrink as more ballots come in, because the
   error is structural rather than sampling noise. Read it as *the size of the
   compositional change county geography does not see*, because that is what it
   is: the geography moves a point and the registration moves nine.
   `test_model_error_matches_the_measured_validation` refits it from `output/`
   and fails if the data moves away from it. It has moved twice already —
   11.5 → 10.5 when Florida became a fifth scoreable state, 10.5 → 9.5 when the
   maturity gate corrected the domain — and it is a measurement, not a constant.

**A ±9.5-point band on a shift that runs −3.65 to +2.34 points across every
published row is not a caveat on the number. It is the number.** That range is
now the honest one: before the maturity gate the file also carried shifts out to
±26, and every one of them was a phase artefact off a sliver of an electorate.

`confidence` is a label about whether the *inputs* are complete enough for the
band to mean anything — `low` when either side is under 85% coverage, or under
85% of counties have reported, or either side has fewer than 50,000 ballots, or
the ballots we cannot see could move the answer by more than the model's own
error. Otherwise `medium`. **It is never `high`, and there is a test that says
so.** No amount of coverage repairs a method that does not beat the null.

⚠️ **`confidence` does not, and did not, look at completeness** — which is how 24
rows off immature days carried its top label. That is now the maturity gate's job
and it is a refusal rather than a label, because a day that is 1% of an
electorate does not need grading, it needs leaving out.

---

## Columns

```
cycle, state, date, days_to_election,
reference_cycle, reference_date, reference_days_to_election,
implied_margin_2024, actual_margin_2024, shift_pp, shift_lo, shift_hi,
composition_margin, reference_composition_margin,
dims_used, dims_reported,
counties_used, counties_total, county_coverage,
reference_counties_used, reference_county_coverage,
ballots, reference_ballots,
party_margin_shift_pp, party_coverage, reference_party_coverage,
age_shift_tv, race_shift_tv, sex_shift_tv, demo_coverage,
behaviour_cycle, confidence, method, source_name, retrieved_at
```

Margins and shifts are **percentage points**, Democratic positive, four decimals.
Coverage figures are fractions. `counties_total` is the census county count,
which for Virginia is 133 and includes its independent cities.
`county_coverage` is county ballots used ÷ the **state's own reported statewide
total** for that day, capped at 1.0, falling back to the county sum where the
state publishes no headline. `behaviour_cycle` is `2024` on every row and names
the thing being held fixed.

---

## When it is wrong

* **It is wrong about persuasion, always, by construction.** It holds 2024 vote
  choice fixed. A state where every group has moved five points to the right
  produces the same number as one where nothing has moved. Any sentence built on
  this table that contains the words "swing", "gaining" or "on track" is a
  misreading. **This is the caveat that must appear wherever the number does.**
* **Early voters are not the final electorate.** A composition shift measured
  three weeks out can vanish entirely by Election Day, because the voters who
  have not voted yet are the ones who decide whether the early electorate was a
  head start or a substitution. North Carolina's own 2024 returns were 82%
  registered-Democratic at 45 days out and 49% at the close — a 33-point
  compositional swing *within one window*, none of which was a change in the
  electorate, all of which was mail being counted before in-person.
* **County geography is nearly blind to composition.** Measured at +0.07 points
  against the no-change null, on a bar of +1.00, after seven specifications were
  swept looking for a knob that would change it. This is the finding, not a
  caveat.
* **The dimensions that carry the signal are the ones that cannot be priced
  here.** Party registration moved 5.6 to 14.4 points in the same electorates
  where geography moved 1. Age moved 12.6 points of total variation in North
  Carolina. Pricing either needs group-level 2024 presidential behaviour that
  this repo does not have and that was not sourced for this feature.

  ⚠️ **And pricing party registration would destroy the only validation there
  is.** The truth column IS the party-registration shift; a model that predicted
  it from the same registration numbers would score beautifully and mean nothing.
  Any future attempt at that dimension needs a different measured compositional
  change to be scored against first.
* **In a midterm.** The weights are 2024 *presidential*. A 2026 midterm
  electorate is a different one, and the county-level relationship between "who
  showed up" and "how the county voted for president" is not the same object in
  an off-year.
* **Under partial coverage.** No tracked state is short today, so the coverage
  bound is dormant and the band is the flat model error. If a state ever posts a
  subset of counties, the band opens up properly and `confidence` drops — but the
  point estimate would then be badly *biased*, not merely uncertain, because the
  counties a state publishes first are not a random sample of it.
* **On a handful of ballots.** North Carolina 2026 is eight ballots. This used to
  read "`confidence` is `low` under 50,000, but low confidence has never once
  stopped a number from being quoted" — and it was right, which is why the
  maturity gate refuses the row outright rather than grading it.

---

## Should this ship? (recommendation)

**Not the modelled margin. Yes to the count underneath it.** Specifically:

1. **Do not publish `implied_margin_2024`, `shift_pp` or "the early electorate is
   N points more Republican" as a modelled figure in any state.** It is worth
   +0.07 points against "assume nothing changed" on a bar of +1.00, it moves 0.95
   points while the thing it describes moves 9.16, and its honest band is ±9.5
   points on a quantity that spans **six** points across every published row. A
   band that contains every plausible answer says nothing.

2. **`party_margin_shift_pp` is defensible and should ship**, framed as what it
   is — a **count**, not a model:

   > North Carolina's 2024 early ballots were **11.4 points less Democratic by
   > registration** than its 2022 early ballots were at the same number of days
   > before Election Day — 100 counties, 4.5 million ballots against 2.2 million,
   > both figures counted by the state.

   In 2026 the same sentence reads "…than its 2024 early ballots were at the same
   number of days out", and it will be available for the ten tracked states that
   report party on returned ballots.

   It is the *same* like-for-like construction — same state, same days out,
   previous early electorate — applied to a dimension where the state hands us
   the numbers instead of us modelling them. It needs no baseline, no weights and
   no frozen-behaviour assumption. It is also, on the evidence above, where all
   the movement actually is. If anything from this feature reaches a reader, it
   should be this.

   Ten tracked states report party on returned ballots (CO, FL, IA, KY, MD, ME,
   NC, NV, PA, SD). The like-for-like version needs it in **two consecutive
   cycles at matching days out**, which today is four — KY, MD, ME, NC — and will
   be all ten from 2026 onward, because the tracker is now collecting the curve
   live for every state it follows.

   It must be labelled **registration**, never vote. Kentucky is full of
   registered Democrats who vote Republican, and a reader comparing our
   registration shift to somebody else's vote projection will be ten points wrong
   in a predictable direction.

3. **The demographic distances (`age_shift_tv`, `race_shift_tv`,
   `sex_shift_tv`) are publishable as distances and nothing more.** "The age mix
   of returned ballots has moved 12.6 points from the same point in 2024" is a
   fact. "Which is worth X points of margin" is not one we can source, and the
   difference between those two sentences is the whole of this document.

4. **Keep the table.** `output/counterfactual.csv` is worth generating and
   committing even if no page renders it: it is the audit trail for this
   decision, `--validate` re-derives the error on demand, and
   `test_the_measured_gain_does_not_clear_the_bar` fails if a future backfill
   ever makes the method clear `MIN_GAIN` — so the recommendation gets revisited
   rather than inherited.

5. **The genuinely better fix is more data and a sourced weight, not a better
   model.** Two things would change the answer, and neither is a refinement of
   this arithmetic:
   * **A sourced, citable table of 2024 presidential vote by party registration
     and by demographic group, per state**, vendored the way the county baseline
     is. That would let the two dimensions that actually move be priced in
     margin points, and it is the only thing that would. It has to be a real
     source with a real provenance note, not a remembered number.
   * **A 2024 county backfill for the twenty-one tracked states that have
     none.** Today the like-for-like comparison is structurally impossible in
     three-fifths of the tracker, which is a data problem, not a modelling one.

**A note on the honest failure.** The interesting result is not the size of the
error bar. It is that the question was well posed and the arithmetic was exact —
the full-electorate identity holds to nine decimal places — and the method still
measured nothing, because the only dimension this repo can price is the one that
barely moves. Every state publishes all its counties, so the county weights are
close to proportional to county size, so the weighted mean is pinned near the
last election, so the *difference* of two such means is pinned near zero. That is
a fact about the data, and no amount of modelling gets around it. It is the third
model in this repo to be declined, and it is declined for a more specific reason
than the other two: not "the signal is weak" but "we are looking in the one place
the signal is not."

---

## Where the code is

| | |
| --- | --- |
| `src/ev/counterfactual.py` | the comparison, the dimensions, the band, `validate()` |
| `src/ev/cli.py` | the `counterfactual` subcommand (that block only) |
| `data/baseline/county_results_2024.csv` | vendored county weights, shared with `estimate.py` |
| `tests/test_counterfactual.py` | 49 tests |
| `tests/fixtures/counterfactual/` | the real North Carolina slice — 100-county baseline, three matched days in each of 2022 and 2024, the state's own rows and its age/race/sex tables, plus the three real 2026 days |
| `output/counterfactual.csv` | per state per day: implied margin, actual margin, the shift, its band, which dimensions were used and what each covered |

Five tests carry the load, and they are named at the top of the test file.
`test_the_full_2024_electorate_reproduces_the_certified_margin` pins the identity
the whole method rests on. `test_a_state_with_no_reference_series_produces_no_row`
pins the refusal. `test_the_measured_gain_does_not_clear_the_bar` pins the
finding against the live `output/` tree, so this recommendation fails a test
rather than quietly going stale.
