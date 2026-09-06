# Party estimate — a model for the states that report no party, and its error

Ten of the states this tracker follows publish no party breakdown of their early
ballots. For nine — **GA, IL, MI, OH, SC, TN, TX, VA, WI** — that is because the
state does not register voters by party at all, so no such breakdown exists
anywhere. **Arizona is the tenth and a different case**: Arizona *does* register
by party, its Secretary of State's daily table simply omits it, and no Arizona
county recorder publishes one either.

`python -m ev estimate` models the partisan composition of those returns and
writes it to **`output/party_estimate.csv`**, its own table, never into the
`party_dem` / `party_rep` / `party_npa` / `party_oth` columns of
`ev_state_daily.csv` or the county files. Those columns mean *"the state reported
this"*, and that is the one guarantee the whole pipeline rests on.

**The headline finding, before anything else.** The model has two terms: where
the ballots came from, and how they arrived. The first term is the one this
document previously argued should not ship — county geography alone was off by a
mean of **6.9 points** and beat "quote the state's 2024 presidential result and
stop" by **0.72**, which is to say it was laundering a known election result
through today's ballot counts. Adding the second term — a correction for the fact
that the people who *ask for a mail ballot* are not a random draw from their
county — takes the measured error to **3.06 points** and the gain over that same
null to **+3.59**, out of sample, leave-one-state-out. Pennsylvania, the worst
state in the old table at 15.5, is **3.7**.

Those figures moved on 2026-09-06 and both directions are worth knowing. The
gain fell from +4.44 because a three-day, eight-ballot series had been averaged
into the headline as a full fold and is no longer — the error it was inflating
was the null's as much as the model's. The panel also grew from ten completed
series to twelve, which took the final-day **signed** bias from −2.53 to −2.15
(and it is **−0.46** averaged over every mature day rather than the last one —
two different numbers that this document previously reported as one).

It is still a model of a party split and not a count of one, it still gets
Colorado slightly worse than before, and it still carries an assumption that
could invert in 2026. Read [What it is wrong about](#what-it-is-wrong-about)
before putting any of it on a page.

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
est_dem_share  =  geography  +  mail
```

### Term one: geography

```
geography  =  Σ_c  ballots_c · dem_share_c   /   Σ_c ballots_c
```

over the counties `c` that reported a ballot count and that we have a partisan
weight for, where

```
dem_share_c  =  votes_dem_c / (votes_dem_c + votes_rep_c)     — 2024 presidential
```

This is published on its own as `geo_dem_share`, and it is a statement about
places rather than people: *"if everyone who has voted early so far voted exactly
the way their county did in 2024, the early vote would be X% Democratic."*

### Term two: mail

```
mail  =  min( MAX_ADJUSTMENT,
              MAIL_SELECTION · mail_share · (1 − mail_reach) ^ MAIL_DECAY )

mail_share  =  mail ballots returned   /   all ballots returned so far
mail_reach  =  mail ballots returned   /   the state's expected electorate
```

**Why this shape.** County geography cannot see who *chose* to vote early, and
that is what sank the geography-only model. But the tracker publishes one thing
that can see it: how many of the returned ballots came by mail. Across every
state-cycle we can score, the ballots already back are more Democratic than their
counties, and the size of that gap tracks the mail channel almost exactly:

| | |
| --- | --- |
| **North Carolina 2024** | 20+ points more Democratic than its counties through the mail-only phase; the day in-person early voting opens the gap collapses to 4, and it finishes at 0.7. |
| **Kentucky 2024** | +22 while mail-only, +10 once in-person is running. |
| **Pennsylvania 2024** | mail-only from start to finish, so the gap never collapses: +12 at the close. This is the state the old model was worst on, and this is exactly why. |
| **Colorado 2024** | all-mail, and the gap is **negative** (−3.7). |

Colorado is the case that fixes the functional form, and it is worth being
explicit about it because it is the difference between a model and a story. The
correction cannot be "mail ballots are Democratic". It has to be "*asking* for a
mail ballot is Democratic, and asking stops meaning anything once every voter is
mailed one". So the term decays in `mail_reach`, and it decays fast: Pennsylvania,
where mail has reached 28% of the electorate, gets +12; Colorado, where it has
reached 97%, gets less than a point.

`mail_share` and `mail_reach` are both published on the row, along with
`mail_adjustment`, so the two halves of every figure can be read apart.

### The constants, and where they come from

| | | |
| --- | ---: | --- |
| `MAIL_SELECTION` | **0.366** | the mail advantage in share points at the limit where mail has reached nobody |
| `MAIL_DECAY` | **5.0** | how fast it dies as mail reaches the electorate |
| `MAX_ADJUSTMENT` | **0.20** | the term never exceeds 20 points |
| `MIDTERM_TURNOUT` | **0.73** | a midterm electorate against the presidential one the baseline measures |

`MAIL_SELECTION` and `MAIL_DECAY` are **fitted**, by `fit_mail_selection`, on
every state-cycle in `output/` that reports party and has county rows.
`MAIL_DECAY` is searched on a 1.00–8.00 grid with `MAIL_SELECTION` solved in
closed form at each point. A series is weighted to count **once**, not once per
day: Pennsylvania is 70 days and Colorado is 5, and pooling them by day would fit
Pennsylvania and call it a model. A series that never got past 50,000 ballots
does not train them at all — North Carolina 2026 is three days and eight ballots,
six of them from registered Democrats, and without that floor it would count for
as much as a completed state.

`MAX_ADJUSTMENT` is not fitted; it is a rule about extrapolation. The largest gap
between a state's reported party split and its geography on any day the fit ever
saw is **18.7 points** (Pennsylvania, 13 October 2024, mail-only, mail having
reached 7% of the electorate). At the opening of a window `mail_reach` is near
zero and the raw formula asks for 36 points, which is past anything it has been
measured against. Capping at 20 is worth 2.3 points of error across all days and
cuts the estimate's travel across a full window from 28 points to 19.

`MIDTERM_TURNOUT` barely matters: the out-of-sample gain runs 3.4 to 3.7 points
across the whole range 0.50 to 1.00. It is 0.73 because that is roughly the ratio
of ballots cast nationally in 2022 to 2024, not because 0.73 scored well.

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
* A state that reports **no method split at all** gets no mail term and lands on
  the geography-only estimate. Not guessed at, not imputed.
* The method split is read from **the state's own row**, and the state's own mail
  figure is believed with everything else in its headline treated as in-person.
  That order is not interchangeable. North Carolina reports 297,034 mail,
  `inperson = 0` and a headline of 4,520,768 — its one-stop votes are simply not
  in that column — so reading `inperson` first would make North Carolina a 100%
  mail state and hand it the largest correction in the table instead of the
  smallest. Where only `inperson` is reported the mail side is the headline's
  remainder, which for Maryland is exactly zero: Maryland's tracked early vote is
  its in-person centres.
* Summing the county file for the method split would look equivalent and is not.
  Under partial county coverage the sum understates how far mail has reached and
  inflates the correction, so it is never used.

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

The same file supplies the *size* of each state's electorate, which is the
denominator of `mail_reach`: its two-party statewide vote, scaled by
`MIDTERM_TURNOUT` in a midterm. Two-party rather than total votes so that any
baseline satisfying `BASELINE_COLUMNS` can answer it; the third-party remainder
is 2–3% and the fitted coefficients absorb it either way.

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

2. **A flat model error, and it is no longer one number.** The measured error
   depends almost entirely on how much of the vote is in. Leave-one-state-out,
   across every validation day:

   | ballots in | mean absolute error |
   | --- | ---: |
   | under 10,000 | 17.3 pp |
   | 10,000 – 50,000 | 5.1 pp |
   | 50,000 – 250,000 | 4.6 pp |
   | over 250,000 | 2.6 pp |

   So `MODEL_ERROR` is **±5 points** on a day with at least `THIN_BALLOTS`
   (50,000) ballots in — above the 3.4-point measured mean deliberately, for a
   reason given below — and `THIN_MODEL_ERROR` is **±15** below that, which is
   the measured mean there. It was a flat ±10 when the model was geography alone.

   A South Carolina series that stops eighteen days out at 17,000 all-mail
   ballots therefore reads 63% ±15 — 48 to 78, which is the honest way to say
   *"this is a handful of the most eager mail voters in the state and we do not
   know"* — rather than 63% ±5. "Low confidence" is a word; the band is the
   number, and they now say the same thing.

The two are **added**, not combined in quadrature. This feature is dangerous
enough that the conservative arithmetic is the right one.

`confidence` is a label about whether the *inputs* are complete enough for the
band to mean anything — `low` when coverage is under 85%, or under 85% of the
state's counties have reported, or fewer than 50,000 ballots are in, or the
missing counties could move the answer by more than the model's own error.
Otherwise `medium`. **It is never `high`, and there is a test that says so.** No
amount of coverage repairs the assumptions underneath.

### Counted versus modelled

Three columns — `measured_fraction`, `modelled_fraction`, `estimate_basis` —
carry how much of a row was actually **counted** rather than modelled.
`estimate_basis` is `model`, `blend` or `reported`, and the band is the model
error applied only to the modelled part. **Every row today is `model` with
`measured_fraction = 0.0`**, and there is a test that says so: the nine
no-registration states have no such count anywhere, and Arizona's county
recorders publish none either (Maricopa's daily file exists but sits behind a
credentialed login for jurisdictions and parties).

The columns exist because the arithmetic for a partial count is settled and
tested in `ev.adapters.az.blend_party_share`, so turning it on the day some
county starts publishing should be data rather than a rewrite. That helper stays
in `az.py` rather than moving here for one specific reason: `az.py` is on the
ingest path, and the ingest path must never import `ev.estimate` — that is what
stops the daily job publishing a model number by accident. The two are kept from
drifting by `MODEL_ERROR`, which `az.py` copies and
`test_model_error_tracks_estimate` guards.

Note that `method` is the *algorithm's* name and has been since this table's
first row; `estimate_basis` is the provenance one. Rows written before the mail
term carry `method = pres2024-2party-county-weighted`; rows written after it
carry `pres2024-2party-county-weighted+mail-selection`.

### Columns

`cycle, state, date, days_to_election, est_dem_share, est_rep_share, est_margin,
est_dem_lo, est_dem_hi, est_margin_lo, est_margin_hi, method, confidence,
counties_used, counties_total, coverage_share, coverage_electorate,
ballots_used, baseline_dem_share, lean_vs_baseline, geo_dem_share,
mail_adjustment, mail_share, mail_reach, measured_fraction, modelled_fraction,
estimate_basis, state_has_party_reg, source_name, retrieved_at`

Shares and margins are fractions to four decimals, not percentages.
`counties_total` is the census county count, which for Virginia is 133 and
includes its independent cities — the number Virginia itself reports against.
`state_has_party_reg` is carried from `data/meta/states.csv` so the file itself
says whether a real party split exists somewhere for that state; it reads `true`
for Arizona.

`lean_vs_baseline` is measured on `geo_dem_share`, deliberately **not** on the
published estimate. It is the one sentence this document was ever willing to
defend on its own — *"the counties that have returned ballots so far are 1.7
points more Democratic than Wisconsin as a whole"* — and folding the mail term
into it would turn a checkable fact about turnout geography into a model output
wearing the same label.

---

## What it assumes

**Two assumptions now, and the second one is new.**

1. **That a county's early voters resemble that county's overall electorate,
   except for how they voted.** The geography term is a pure county-lean average
   and cannot see anything else.

2. **That asking for a mail ballot is a Democratic act, and less so the more
   universal mail becomes.** This is the assumption that rescued Pennsylvania and
   it is a fact about a particular decade of American politics, not about
   arithmetic. Democrats voted early and by mail at far higher rates than
   Republicans in 2020, 2022 and 2024; Republican mail voting has been rising
   since. **If the sign flips in 2026 this term will be confidently wrong in
   exactly the states it currently rescues, and wrong by more than the
   geography-only model ever was.** That is the main reason `MODEL_ERROR` sits
   above the measured mean rather than on it, and the reason `--validate`
   re-derives the constants from `output/` on demand: the day the sign flips is a
   command, not an argument.

Two further mismatches, both of which show up in the numbers below:

* **Registration is not vote.** The only ground truth available is the party
  *registration* of early voters. Kentucky is full of registered Democrats who
  vote Republican; its registration split (44.7% D of two-party) sits ten points
  Democratic of its presidential split (34.5%). That gap is definitional, not a
  modelling failure — and it is most of what is left in Kentucky's error. It is
  also a gap that **cannot exist in the states this model actually publishes
  for**, because they do not register by party at all, which is why no correction
  for it was adopted. See [What else was tried](#what-else-was-tried).
* **Two-party only.** Independents and third parties are excluded from both the
  numerator and the denominator.

---

## What it is wrong about

### Measured error against states that DO report party

`python -m ev estimate --validate` runs the identical code path against every
state-cycle in `output/` that has county rows *and* a reported party split. Ten
completed series are available (North Carolina and Maine from the published data;
Colorado, Iowa, Kentucky, Maryland and Pennsylvania backfilled).

**Every row is leave-one-state-out.** The mail term has two fitted constants, and
a fitted constant scored on its own training data is exactly how
`docs/regression.md` nearly shipped seven specifications that turned out to be
worse than knowing nothing. The fold that scores Pennsylvania has never seen
Pennsylvania, and North Carolina 2022 cannot train the fold that scores North
Carolina 2024.

| cycle | state | days | est | truth | 2024 base | **final error** | MAE | geo MAE | null MAE | gain vs geo | gain vs null | est moved | truth moved |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 2024 | CO | 5 | 55.7 | 52.0 | 55.6 | **+3.7** | 4.9 | 3.3 | 3.8 | −1.5 | −1.1 | 2.4 | 0.4 |
| 2022 | FL | 24 | 45.8 | 46.3 | 43.4 | **−0.5** | 1.4 | 3.6 | 3.6 | +2.2 | +2.2 | 11.4 | 6.0 |
| 2024 | FL | 32 | 46.1 | 43.5 | 43.4 | **+2.6** | 3.5 | 0.4 | 0.5 | −3.1 | −3.0 | 8.5 | 4.5 |
| 2024 | IA | 16 | 45.7 | 48.9 | 43.3 | **−3.2** | 3.0 | 6.7 | 7.4 | +3.7 | +4.4 | 16.4 | 4.2 |
| 2024 | KY | 13 | 38.1 | 44.7 | 34.5 | **−6.7** | 5.9 | 10.6 | 10.5 | +4.6 | +4.6 | 3.4 | 0.9 |
| 2022 | MD | 8 | 63.8 | 66.3 | 64.8 | **−2.5** | 1.9 | 1.9 | 0.5 | +0.0 | −1.4 | 1.4 | 2.2 |
| 2024 | MD | 8 | 64.1 | 63.2 | 64.8 | **+1.0** | 1.8 | 1.8 | 2.9 | +0.0 | +1.1 | 1.0 | 2.8 |
| 2022 | ME | 121 | 59.2 | 67.2 | 53.5 | **−8.0** | 4.2 | 14.1 | 15.9 | +9.9 | +11.7 | 14.5 | 5.4 |
| 2024 | ME | 121 | 58.3 | 60.2 | 53.5 | **−2.0** | 1.6 | 7.4 | 8.7 | +5.8 | +7.1 | 11.9 | 5.6 |
| 2022 | NC | 61 | 51.6 | 55.1 | 48.4 | **−3.5** | 3.9 | 6.3 | 7.0 | +2.4 | +3.1 | 1.1 | 1.7 |
| 2024 | NC | 47 | 50.4 | 49.4 | 48.4 | **+1.0** | 0.9 | 0.9 | 1.1 | +0.1 | +0.2 | 1.0 | 2.0 |
| 2024 | PA | 70 | 55.1 | 62.7 | 49.1 | **−7.6** | 3.7 | 15.5 | 17.9 | +11.7 | +14.1 | 20.1 | 11.2 |

All figures are percentage points of the **Democratic two-party share**. `est`
and `truth` are the last day of the series. `MAE`, `geo MAE` and `null MAE` are
averaged over the days on which at least 25% of that series' final early vote was
in. `geo` is the geography-only model this one replaced; `null` is quoting the
state's 2024 presidential result and stopping.

**Summary across the twelve completed series, and the same figures for the model
this one replaces:**

| | geography only | **+ mail term** |
| --- | ---: | ---: |
| mean absolute final-day error | 5.81 | **3.52** |
| signed mean final-day error | −4.88 | **−2.15** |
| RMSE of final-day error | 7.05 | **4.30** |
| worst final-day error | 12.11 (PA) | **8.04** (ME 2022) |
| mean MAE, mature days | 6.04 | **3.06** |
| mean MAE of the null model | 6.65 | 6.65 |
| **gain over the null** | **+0.61** | **+3.59** |
| **gain over the geography-only model** | — | **+2.98** |
| how far the estimate moved across a window | 1.07 | 7.75 |
| how far the reported party split moved | 3.90 | 3.90 |

**Two different signed biases, and they say different things.** The −2.15 above
is measured on each series' LAST day. Averaged over every mature day instead it
is **−0.46** — the model leans Republican at the close and is close to unbiased
through the middle of a window, which is consistent with the mail term doing
most of its work early and decaying out. Neither number rescues a flat
correction (see [What else was tried](#what-else-was-tried)), and the smaller
one is a reason there is less left to correct than this document used to think.

### A thirteenth series that is shown and not scored

North Carolina 2026 appears in `--validate` output and is marked
`SHOWN, NOT SCORED`. It is three days old, five counties and **eight ballots**,
six of them from registered Democrats.

It was already excluded from *fitting* the constants, with a comment saying why:
a series that thin would count for as much as Pennsylvania's seventy days. **It
was not excluded from the headline error, and it should have been by the same
sentence.** It scored an MAE of 13.6 and dragged the reported mean from 3.06 to
3.87 — nearly a point of this model's published accuracy, decided by eight
ballots.

⚠️ **`mature_days()` cannot catch this, and the reason generalises.** It
normalises by the SERIES' OWN maximum, so all three of those days are 100% of
"the eventual vote" when the eventual vote so far is eight. A running series is
not a yardstick for itself. `counterfactual.py` had the identical trap on its
publish path and it was found the same week; if a third model here ever measures
maturity, it needs a finished curve to measure against.

The row stays in the output. "What does this look like in September" and "how
accurate is this model" are different questions, and only the second one is a
mean.

### Which states got worse

Two, and they are the honest cost of the trade:

* **Colorado 2024: 3.3 → 5.3** on mature days, and 3.3 → 5.3 across all days. Its
  final-day error is barely touched (+3.7 → +3.7), so this is entirely
  mid-window: Colorado's mail had reached only 40% of its electorate eight days
  out, and the model reads that as selection where there is none, because in
  Colorado every voter is mailed a ballot whether they want one or not. Colorado
  is the state that dictated the shape of the decay term and it is still the
  state the shape fits worst. **None of the states this model publishes for is an
  all-mail state**, which is a reason to accept the trade and not a reason to
  think it does not exist.

* **North Carolina 2024, all-days only: 8.8 → 12.5 before the cap, 5.0 after.**
  The cap fixed this one; it is recorded because it is what the cap is for. NC's
  mail channel is tiny (7% of its early vote) and never grows, so `mail_reach`
  stays near zero through a six-week mail-only phase in which the truth falls
  from 81% to 58%. Uncapped, the model asked for a flat +36 the whole time.

Maryland is unchanged in both cycles, to the decimal, because Maryland's tracked
early vote is in-person only and its mail term is exactly zero.

### Is it robust, or is it Pennsylvania?

**Fit on one cycle, predict the other.** The test that matters most for October
2026, because 2026 is a cycle no fit has seen.

| | n | model | geography only | null | gain vs geo | gain vs null | fitted (α, decay) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| fit 2022 → test 2024 | 7 | **4.23** | 6.60 | 7.46 | +2.37 | +3.24 | (0.336, 2.75) |
| fit 2024 → test 2022 | 3 | **3.33** | 7.45 | 7.83 | +4.12 | +4.50 | (0.338, 5.00) |

Both directions positive and large, which is the opposite of what
`docs/regression.md` found for its specifications, where not one direction of one
spec beat even the crude null.

**Drop a state entirely and refit.** The headline is not one state's doing:

| removed | model MAE | geography only | null | gain vs geo | gain vs null |
| --- | ---: | ---: | ---: | ---: | ---: |
| CO | 2.88 | 7.25 | 7.99 | +4.37 | +5.11 |
| IA | 3.28 | 6.88 | 7.60 | +3.59 | +4.32 |
| KY | 2.82 | 6.44 | 7.25 | +3.63 | +4.43 |
| MD | 3.46 | 8.10 | 9.04 | +4.65 | +5.58 |
| ME | 3.30 | 5.87 | 6.39 | +2.57 | +3.09 |
| NC | 3.30 | 7.67 | 8.45 | +4.37 | +5.15 |
| PA | 3.29 | 5.90 | 6.43 | +2.61 | +3.14 |

The gain over the geography-only model never falls below **+2.57** whichever
state is removed, including Pennsylvania.

**Are the constants stable?** Every leave-one-state-out fold, so each of these
was fitted without the state named:

```
hold out CO   α = 0.319   decay = 4.00
hold out KY   α = 0.311   decay = 4.25
hold out NC   α = 0.348   decay = 4.75
hold out MD   α = 0.366   decay = 5.00
hold out IA   α = 0.382   decay = 5.00
hold out ME   α = 0.402   decay = 5.75
hold out PA   α = 0.466   decay = 6.50
full sample   α = 0.366   decay = 5.00
```

`test_fitted_constants_still_match_the_data` refits on whatever `output/` holds
and fails if the answer has moved by more than 0.05 in α or 1.0 in the decay, so
a future backfill that changes the finding fails a test rather than quietly
leaving this page stale.

### What else was tried

Every alternative below was scored on the same panel under the same
leave-one-state-out protocol, and every one of them beats the geography-only
model — which is the point of showing them. Ranking on the mean is not enough;
the last column is why.

⚠️ **This table is on the TEN-series panel** it was fitted against, so its
absolute numbers no longer match `--validate` (which is twelve series and no
longer averages in the eight-ballot one). The comparisons between rows are what
it is for, and those still hold — every candidate here was scored against every
other on the same footing. Four further candidates, measured on the current
panel, are in [A second search](#a-second-search-2026-09-06-and-four-more-dead-ends).

| specification | MAE | vs geo | vs null | worst single state |
| --- | ---: | ---: | ---: | --- |
| geography only (the old model) | 6.86 | — | −0.72 | — |
| a flat bias correction | 6.02 | +0.84 | +1.56 | MD 2024 **+7.3** |
| mail share alone (plain method weights) | 6.29 | +0.57 | +1.29 | CO 2024 **+12.5** |
| how much of the electorate has voted | 5.74 | +1.12 | +1.84 | MD 2024 **+9.8** |
| how partisan the state is | 5.33 | +1.52 | +2.24 | NC 2024 **+6.1** |
| mail × reach, linear decay | 4.55 | +2.31 | +3.03 | CO 2024 **+7.4** |
| mail × reach, squared decay | 3.69 | +3.16 | +3.88 | CO 2024 **+4.8** |
| **shipped: mail × reach⁵, capped** | **3.14** | **+3.72** | **+4.44** | CO 2024 **+2.0** |

Three of these are worth saying more about, because each one is a trap.

* **"How partisan the state is"** — a term in the state's own presidential lean —
  buys 1.5 points and is the most dangerous line on this page. What it is
  actually fitting is the gap between a state's *vote* and its *registration*:
  party registration is a lagging, less polarised indicator, so Kentucky's 34.5%
  presidential D share corresponds to a 48% registered-D electorate. Fitting that
  gap improves the score against registration-reporting states and would be
  **meaningless applied to Texas**, which has no registered Democrats to be
  ancestral about. It also wrecks the states the model was already good at (North
  Carolina 2024 from 0.9 to 7.0, Colorado from 3.3 to 8.8). Rejected on grounds
  that have nothing to do with its score.

* **A flat bias correction** for the systematic −4.9-point Republican lean is the
  obvious fix and it is the wrong one. It is the same registration-versus-vote
  gap in a cruder form, it moves Maryland — a state the model already gets to
  within a point — off by 7, and the mail term removes half of the same bias for
  a reason rather than by fiat. The remaining signed bias is −2.53.

* **A midterm baseline.** `output/results_state.csv` now carries 2022 Senate
  results, so the county leans can be shifted to match a state's 2022 statewide
  share instead of its 2024 presidential one. It is untestable and unusable.
  Untestable: of the three midterm series we can score, Maine 2022 had no Senate
  race so there is no anchor, North Carolina's 2022 Senate two-party share is
  48.4% — identical to its 2024 presidential share, so nothing moves — and only
  Maryland 2022 changes at all, by 1.1 points, on a series that was already
  within 2.5. That is n = 1. Unusable: four of the states this model publishes
  for (VA, TX, TN, MI) had no 2022 Senate race either, so the baseline would be
  missing exactly where it is needed. **No evidence, and structurally
  unavailable. Rejected.**

Also tried and beaten by the shipped form: a days-to-election term (6.10 — a
worse proxy for the same thing, because what matters is how much has come back,
not what day it is), separate additive weights for mail and in-person without the
reach interaction (6.29, and it is the one that destroys Colorado), and the reach
term applied to the in-person channel as well as mail (5.39, against 4.55 for the
mail-only version of the same linear form; the in-person coefficient comes out at
0.038 against mail's 0.142, so the channel is doing a quarter as much work and
costs 0.8 points to include).

### A second search, 2026-09-06, and four more dead ends

The panel had grown by five series since the table above was fitted (Florida's
two cycles out of the Internet Archive, plus Colorado, Iowa and Pennsylvania), so
the obvious question is whether anything rejected on the old panel should be
revisited, and whether anything untried now works. Four candidates, all on the
corrected twelve-fold panel, all leave-one-state-out:

| candidate | MAE | vs shipped |
| --- | ---: | ---: |
| **shipped: statewide mail × reach⁵, hard cap** | **3.06** | — |
| an in-person reach term, β = 0.05 … 0.366 | 3.68 … 7.66 | worse at every β |
| county-level mail × county-level reach | 5.46 | −2.40 |
| `tanh` saturation instead of the hard cap | 3.09 | −0.03 |
| `x/(1+x/cap)` saturation instead of the hard cap | 3.58 | −0.52 |
| no cap at all | 3.17 | −0.11 |

* **The in-person reach term.** This document already rejected one, but only
  inside the LINEAR family (5.39 against 4.55). It is worse in the reach⁵ family
  too, monotonically in β, and it destroys Maryland — a state the model is
  otherwise within two points of — at β ≥ 0.2. Now rejected in both families.

* **A county-level mail term** is the one genuinely new idea, and it is the
  better-looking one on paper: mail propensity varies enormously by county and
  the geography term is already per county, so the selection correction is the
  coarsest thing in an otherwise fine-grained model. It is much worse — 5.46
  against 3.06 — and it fails in the two ways that matter. Its per-county reach
  denominator (county 2024 votes × midterm turnout) is far noisier than the
  statewide one, so the finer instrument is dominated by denominator noise:
  Maine 2022 goes 5.0 → 10.0 and Iowa 2.4 → 6.7. And it is **structurally
  unavailable exactly where it is needed** — Pennsylvania's county file carries
  mail and no in-person at all, so PA falls back to geography-only and scores
  15.5. That is the same trap the midterm baseline died on, and it is worth
  naming as a pattern: a term that validates only where the data is richest is
  not a term this model can use.

* **The hard cap is not a patch, it is the best of the four shapes.** Worth
  checking, because a `min(…, 0.20)` bolted onto a model looks like one, and the
  uncapped term reaches **49.4 points** and is clipped on 231 of 526 days — 44%.
  But almost all of that clipping is on thin days the model is not scored on:
  on the 168 mature days the cap binds **six times**. It is clamping the model
  precisely where it cannot be trusted and barely touching it where it can,
  which is what a cap is for. Both smooth saturations and no cap at all score
  worse, on the mature days and on every day.

### When it is most wrong

* **Early in the window.** Under 10,000 ballots the mean error is 17 points and
  the 90th percentile is 49. The mail term helps enormously here — all-day error
  across the ten series falls from 13.2 to 6.6 — but "much better than terrible"
  is still not good. This is what `THIN_MODEL_ERROR` and `confidence = low` are
  for, and they now agree with each other.
* **Where mail is universal.** Colorado, and by extension Oregon, Washington,
  Utah, Nevada, Vermont, Hawaii and California. In an all-mail state mail is not
  a choice and selects nobody, and the decay term is a coarse instrument for
  saying so. None of the ten states this model publishes for is one, but Arizona
  — permanent-early-voting-list, mostly mail — is the closest, which is one more
  reason it is excluded from the UI.
* **Where registration and vote have drifted apart.** Kentucky (−6.7, down from
  −10.2) and the ancestral-Democratic South generally. This is the error a reader
  inherits when they compare our modelled number to another state's reported
  registration, and no early-vote data can fix it.
* **If Republican mail voting keeps rising.** See
  [What it assumes](#what-it-assumes). This is the failure that would matter
  most and the one the data cannot yet speak to.
* **Under partial coverage.** No tracked state is short today, so the coverage
  band is dormant. If a state ever posts a subset of counties, the band opens up
  properly and `confidence` drops — but the point estimate would then be badly
  biased, not merely uncertain, because the counties a state publishes first are
  not a random sample of it.

### What it now says about the states it is actually for

The geography-only model was pinned within about two points of each state's own
2024 result — the finding that made it "the 2024 election result with a live-data
costume on". The mail term is the first thing in this model that is a statement
about 2026 rather than about 2024. Last day of each backfilled cycle:

| state | cycle | 2024 pres | geography only | published | mail share | mail reach |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| OH | 2022 | 44.3 | 44.5 | **51.0** | 63% | 22% |
| TX | 2022 | 43.1 | 43.9 | **45.6** | 6% | 4% |
| VA | 2022 | 52.9 | 54.7 | **61.6** | 31% | 10% |
| OH | 2024 | 44.3 | 43.4 | **48.6** | 41% | 19% |
| SC | 2024 | 40.9 | 41.5 | **43.5** | 7% | 4% |
| TN | 2024 | 34.9 | 35.8 | **37.0** | 4% | 3% |
| TX | 2024 | 43.1 | 43.2 | **44.4** | 4% | 3% |
| VA | 2024 | 52.9 | 54.0 | **58.3** | 22% | 12% |
| WI | 2024 | 49.6 | 51.2 | **56.5** | 39% | 18% |

Texas and Tennessee barely move, because almost nobody in either state votes by
mail. Ohio, Virginia and Wisconsin move five to seven points, because a large
minority of their early vote is mail that a voter had to request.

Three backfilled rows are missing from the table and each is missing for a
reason. **South Carolina 2022** stops eighteen days out at 17,000 all-mail
ballots and reads 63% ±15 — the band doing its job. **Tennessee 2022** and
**Arizona 2024** report no method split at all, so they get no mail term and are
unchanged from the geography-only model: 37.0 and 47.1. That is the fallback
working as specified — a state that does not say how its ballots arrived does not
get guessed at.

There is no ground truth for any of these and there never will be, so the table
is a description of the model's behaviour and not evidence about it. The evidence
is the ten-series table above.

### Arizona is a special case, and it stays a model

Arizona registers voters by party. The number exists somewhere — the Secretary of
State's daily statewide table just does not carry it. It was worth checking
whether it could be fetched rather than modelled, and it was checked: **no
Arizona county recorder publishes a party-broken-down early-ballot file
publicly.** Maricopa produces one daily and it sits behind a credentialed login
for jurisdictions and political parties. So Arizona stays a pure model, its rows
carry `state_has_party_reg = true` so the file itself flags the difference, and
the blend machinery is in place for the day that changes.

---

## Should this ship? (recommendation)

The version of this document that argued **no** was arguing against a model that
bought 0.79 points over a constant. This one buys 4.44 out of sample and 3.72
over its own predecessor, clears both nulls in both cross-cycle directions, and
survives the removal of any single state. That argument no longer applies, and
the estimate remains on the site.

What has *not* changed, and what the presentation is still defending against:

1. **It is a model of a party split, not a count of one.** Nothing on this page
   makes `est_dem_share` comparable to North Carolina's *"32.4% of returned
   ballots came from registered Democrats"*. One is a count and the other is an
   inference, and the surfaces must keep saying which.

2. **The band ships with the number, always, and it is the headline.** It is now
   ±5 rather than ±10 on a mature day and ±15 rather than ±10 on a thin one,
   which means it got tighter where the model earned it and *wider* where it did
   not.

3. **The remaining error is not symmetric or well understood.** It is still
   −2.53 signed, still 6.0 points on Kentucky, and it rests on an assumption
   about mail voting that has held for three cycles and is not a law.

4. **`lean_vs_baseline` remains the honest small statement** and is still
   published beside the estimate, still measured on geography alone.

5. **The genuinely better fix is still more data.** Precinct-level early-vote
   counts (some Georgia and Texas counties publish them) would sharpen the
   geography term. A party split *by method* — which no state we track publishes,
   and which would let the mail coefficient be measured directly instead of
   inferred from how the gap moves when in-person opens — would replace the
   fitted constants with counted ones. Both are real work; both would beat any
   further refinement of this arithmetic.

**A note on what changed the answer.** It was not a better fit to the same data.
It was noticing that the tracker already publishes the variable the old model was
missing — `mail_returned`, in every state, every day — and that the old model's
worst state (Pennsylvania) and its best (North Carolina) differ in exactly that
variable and almost nothing else. The regression in `docs/regression.md` failed
because it went looking for a signal; this worked because a specific, named,
mechanical error had a specific, named, published cause.

---

## What shipped, and on what terms

The estimate is on the site under six conditions, and every one of them is
enforced in code with a test behind it rather than left to a style guide.

1. **It is labelled an estimate everywhere it appears.** The badge reads
   `Estimate`, the heading says the state "publishes no party split — this is a
   model of one", and the word *reported* never appears next to one of these
   figures. There is no surface on which the modelled number sits in a column,
   chart or legend that also carries a reported split.

2. **The band ships with the number, always, and it is the headline.** The card
   leads with the interval set large; the central estimate is underneath it in
   small italic type. `ev-estimate.js`'s `range()` returns `null` unless the row
   carries *both* bounds, and `card()` refuses to draw without a range — there is
   no code path that renders a bare point estimate.

3. **The measured error is beside it, in plain words, at full size.** It is not a
   tooltip, not a footnote and not a link. A reader who sees the number sees it.

   > This is a model, not a count. Tested against the states that do report party
   > registration — and tested on states the model had never seen — it lands
   > about 3 points off on average once a state is properly into its early-vote
   > window, and it still leans Republican by about 2. Most of what it knows is
   > two things: how each county voted in 2024, and how many of the ballots back
   > so far came by mail. Early in a window, before 50,000 ballots are in, it is
   > off by 15 points on average and the band says so.

4. **It cannot be mistaken for a reported split without reading.** A reported
   split on this page is a solid stacked navy/red bar in which every pixel of the
   track is filled and the boundary between the two colours *is* the number. The
   estimate is the opposite by construction: a hatched **interval on an axis**,
   in neutral graphite rather than a party colour, on a dashed card, where most
   of the track is empty and the empty part is the point.

5. **Arizona is excluded from the UI.** `estimate.py` still writes it — the CSV
   is the audit trail, and it flags the row with `state_has_party_reg=true` — but
   `ev-estimate.js`'s `showable()` refuses to draw any row carrying that flag.
   The test is made against the column, not against the string `"AZ"`, so a
   future state in the same position is excluded for the same reason with no code
   change.

6. **`lean_vs_baseline` is published alongside, framed as what it is.** Every
   card carries the sentence this document argued was the only defensible output,
   under the heading **What the geography does say**, kept visually apart from the
   estimate so it does not read as a caveat on it.

> ⚠️ **The frontend's copy of these numbers is in another repository.**
> `EIEV.estimate.ERROR_TABLE` and `ERROR_NOTE` in the theme back both condition 3
> and the Info tab, and `assets/earlyvote/tests/party-estimate.test.mjs`
> recomputes the headline claims from the per-series rows. They still carry the
> **geography-only** figures — 5.5 points, 4 points Republican, "weighting by
> county buys under a point" — and every one of those is now wrong. They must be
> replaced with the table and the note above, and the band drawn from the row's
> own `est_dem_lo` / `est_dem_hi` rather than from a hardcoded ±10.

**Nothing about the pipeline's guarantee changed.** The estimate is still written
to `output/party_estimate.csv` and nowhere else, still never to
`party_dem`/`party_rep`/`party_oth`/`party_npa`, and
`test_write_never_touches_the_reported_party_columns` still hashes the whole
output tree on every write to prove it.

---

## Where the code is

| | |
| --- | --- |
| `src/ev/estimate.py` | the model, the mail term, the fit, the coverage arithmetic, the band, `validate()` |
| `src/ev/cli.py` | the `estimate` subcommand (that block only) |
| `src/ev/adapters/az.py` | `blend_party_share`, and a guarded copy of `MODEL_ERROR` |
| `data/baseline/county_results_2024.csv` | vendored county weights, and the electorate sizes |
| `tests/test_estimate.py` | 60 tests, including the three that matter |
| `tests/fixtures/estimate/` | real NC 2024 slice — 100-county baseline, six days of county rows, the matching reported state rows |

Three tests carry the load.
`test_write_never_touches_the_reported_party_columns` hashes every file in an
`output/` tree before and after a publish and asserts that the only thing that
changed is `party_estimate.csv`. `test_the_mail_term_is_scored_leave_one_state_out`
asserts the protocol itself — it moves one state's truth and checks that the
*other* state's score moves, which it could not if scoring were in-sample.
`test_ground_truth_north_carolina_2024` pins the measured error against a state
that does report party, so if it moves, the claims on this page fail a test
rather than quietly going stale.
