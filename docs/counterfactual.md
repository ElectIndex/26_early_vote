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
same states reported for those same ballots — the method is off by **10.62
percentage points of margin** and the null model *"the composition has not
changed at all"* is off by **10.59**. It is **−0.03 points worse than saying
nothing**, against a bar of +1.00. County geography moves **1.42 points** across
a window while the composition it is standing in for moves **10.59**. Fitting a
scale factor to close the gap, leave-one-state-out, costs 2.15 points on average
and produces multipliers of **+0.10, +0.10, +1.25, +1.34, +1.34, +1.51, +1.55,
+1.55, +1.64, +1.68, +1.77, +4.60 and +4.60** — a **forty-sixfold** spread whose
two ends are two states pulling against each other. A signal in the wrong unit
would be fixed by one number. Nothing here is one number.

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

**A fourth, added 2026-09-07, and it is the first finding in a stronger form.**
`shift_pp` is a difference of two weighted means of county margins, so it obeys a
hard arithmetic bound: **|shift_pp| ≤ TV(county mix) × (widest county margin −
narrowest)**. Given how little each state's county mix actually moved between the
two cycles, that ceiling is **3.76 points in Florida, 8.57 in Kentucky, 6.17 in
Maryland, 1.68 in Maine, 5.48 in North Carolina and 4.57 in Pennsylvania** — and
every one of those states' measured compositional change is **larger**. On the
thirteen-fold panel, **nine of thirteen** scoreable series sit outside what county
geography was *able* to report at any weighting of its counties, Oklahoma's two
among them. Pennsylvania 2024 is not an outlier this method
happened to miss; it is the extreme of a limit that binds wherever there was
anything to see. And the bound needs **no ground truth at all** — it is
computable on a live 2026 day. The four series inside their own bound are Colorado
and Oregon, whose registration moved 1.98 and 0.82 points, and **PA 2022 and LA
2024, which moved 5.57 and 9.79 against bounds of 6.28 and 10.26** — and on both
of those the model *had* the room and still pointed the wrong way. See [The reach bound](#the-reach-bound).

**A fifth, added 2026-09-07, and it is what the whole panel had been missing.**
Pennsylvania's 2020 mail curve was recovered, which made 2022 a *target* cycle and
gave the panel its **second cycle transition** — an eighth fold, PA 2022-vs-2020.
Three things came with it, and none of them help the model. It is the first series
this method was arithmetically **able** to reach whose composition actually moved
(5.57 points against a bound of 6.28) and it gets it wrong by 8.68 **pointing the
opposite way**, for a gain of −4.73, the worst fold in the panel. The two
transitions point opposite ways — PA's early electorate moved **+5.57** points
from 2020 to 2022 and **−27.64** from 2022 to 2024 — so the headline moved from
−0.15 to **−0.73**. And it makes **leave-one-cycle-out** possible for the first
time, which is the test the fitted constant had never been able to take: the
constant that scores **+3.49** leave-one-*state*-out scores **−3.35** the moment
a transition is held out, and **−10.14** on the fold it was not fitted on. See
[Two transitions](#two-transitions-and-the-test-the-constant-finally-took).

**A sixth, added 2026-09-07, and it is the fourth with the inequality replaced
by an equality.** The reach bound says what county geography *could* have
reported. `mix_split` says what it *did*, by taking the truth column apart. The
measured registration change is itself a weighted mean over counties, so it
decomposes exactly into the part the county mix moved and the part that happened
between voters of the same county — and on the final matched day the second part
is **84% (NC) to 135% (PA 2022)** of it, **Pennsylvania 2024 96%: −26.54 of
−27.64**. The first part is `shift_pp`'s own construction with the unit gap
removed — the same county mix change, over the same counties, valued in the
registration points the truth is measured in rather than presidential ones — and
it buys **−0.05** over the null on the eight folds (**+0.30** on the seven that
existed before Pennsylvania's second transition, and +0.03 there with North
Carolina dropped). On the thirteen-fold panel it buys **+0.43**, and +0.15 without
Oklahoma; **no version of it has ever reached half the bar.** So it is not that
the county dimension is measuring the right thing in the wrong unit. There was
nothing in that dimension to measure. See
[Inside the counties](#inside-the-counties).

**A seventh, added 2026-09-07, and it is the search the sixth one demanded.** If
84% to 135% of every change happened *inside* counties, the next question is
whether any dimension that varies inside a county can see it. The tracker
collects three. **None of them ships, and two were settled before any
arithmetic.** (⚠️ On 2026-09-08 the third stopped being settled before the
arithmetic too — see the tenth finding below.)
The mail/in-person mix is the one the Pennsylvania finding names — and
Pennsylvania has no early in-person voting, so its observed early electorate is
100% mail in 2020, 2022 and 2024 and the mix moves **exactly 0.00**, as does
Maryland's; the channel those voters switched from was **Election Day**, which
this tracker does not observe. Where it does move it is a calendar variable
before a compositional one, it needs a mail-minus-in-person registration gap of
111 to 752 points in four of the five folds it moves in — against **18 to 42
measured** — and scored leave-one-state-out it buys **−1.52**. (⚠️ Both of those
numbers moved on 2026-09-08, and it is the tenth finding below: one fold now needs
only 38 and the score is +0.42.) Age, race and sex carry no
party split anywhere in this repo, so their between-band term cannot be computed
at all, and over the eight folds they cover **one and two**. The last one is the
sharpest: Maine publishes party by **town**, 463 of them inside 16 counties, and
that exact three-way split puts **−0.73 between counties, −0.94 between towns of
the same county, and −12.21 inside towns**. Twenty-nine times the resolution
takes geography from 5% of the answer to 12%. See
[Outside the counties](#outside-the-counties-2026-09-07--the-three-dimensions-that-vary-inside-one).

**An eighth, added 2026-09-08, and it is a correction to this page as much as a
finding.** The seventh said the between-method term "cannot be computed" because
*"no state publishes party crossed with method"*. **That was true of `schema.py`
and false of the sources.** Florida renders its mail and early county tables
separately, each split by party; Kentucky carries DEM and REP columns per
channel; North Carolina and Maine put the party and the channel on the same
ballot row; Colorado ships a county × party matrix per channel. Five adapters
were parsing the crosstab and adding the two channels together at the last step
before writing a `CountyDay` that had nowhere to put it. `schema.MethodDay` and
`output/methods/<st>.csv` hold it now, and the exact between-channel term buys
**+1.77** over the null — over the bar, and refused twice: the crosstab exists in
**four of the nine** folds and not in the panel's two largest changes, and
**dropping Florida alone takes +1.77 to +0.24**. Verifying the claim also turned
up a live bug: North Carolina renamed `ballot_req_type` from `ONE-STOP` to
`EARLY VOTING` between cycles and `nc.py` matched literals, so `inperson`
published as a flat **0** for 4,231,692 ballots on all 47 days of NC 2024. See
[Party by method](#party-by-method-2026-09-08--the-crosstab-that-did-exist).

**A ninth, added 2026-09-08 and not this document's own work.** Iowa's 2022
backfill made the panel **nine** folds. IA 2024's early electorate moved
**−23.12** registration points — the second largest in the panel — county
geography reported 1.32 of it against a reach bound of 4.15, and its own MAE of
22.52 took `MODEL_ERROR_PP` from 10.5 to **11.5**. Its method mix is 0.00 and it
has no crosstab, so it makes every coverage row in this document worse and
changes no conclusion in it.

**A tenth, added 2026-09-08, and it is the first entry in this list that makes
the refusal WEAKER rather than stronger. Read it as such.** Five states' backfills
landed at once — Louisiana 2022+2024, Oklahoma 2020+2022+2024, Oregon 2022+2024,
Delaware 2024, Wisconsin 2022 — and the panel went from nine folds to
**thirteen**. Wisconsin and Delaware contribute rows and no folds (neither has a
party split on returned ballots, so neither has a truth column). The other three
contribute four folds and, critically, **a second 2022-transition fold**:
Oklahoma archives one county snapshot per cycle going back to 2020, so OK
2022-vs-2020 joins PA 2022-vs-2020 and the panel holds two of them where it held
one. Four things follow and none of them is comfortable.

* **The county term's headline moved from −0.55 to −0.03**, and every point of
  that is dilution: all four new folds are a **single matched day**, their gains
  are +0.98, +4.23, −0.50 and −0.14, and nothing about the method changed.
  `MODEL_ERROR_PP` narrows 11.5 → **11.0**. See
  [the day-count question](#four-folds-are-one-day-long-and-there-is-still-no-minimum).
* **The leave-one-cycle-out constant stopped failing.** −3.35 becomes **+2.19**,
  over the bar and the strongest thing this panel has ever produced. It is
  refused on its **jackknife** instead — −2.96 without Oklahoma, +4.47 without
  Pennsylvania, an eight-point swing on one fold — and because nine of the
  thirteen gains are mechanically ±|c| and so measure a sign, not a fit. **That
  is a weaker refusal than the one this page carried this morning**, and
  [the section](#two-transitions-and-the-test-the-constant-finally-took) says so.
* **The method dimension came into reach.** Oklahoma's mail share fell from 63.2%
  in 2020 to 35.1% in 2022, so OK 2022's method mix moved 28.08 points and needs
  only a **38-point** channel gap to carry its whole −10.62 — inside the 18-to-42
  points this repo can observe, where before only Florida's 28 was. Scored, the
  specification goes from −1.52 to **+0.42** fitted leave-one-state-out and
  **+1.29** at an exogenously pinned 28-point gap, which falls to **+0.60**
  without Oklahoma. Still under the bar, and no longer refused before the
  arithmetic. See
  [§4, Scored anyway](#4-scored-anyway-at-its-very-best-leave-one-state-out).
* **And the fitted scale no longer disagrees with itself about SIGN.** All
  thirteen multipliers are positive, and three folds now clear `MIN_GAIN`
  individually where none did before. The magnitude disagreement is wider than it
  has ever been — a **forty-sixfold** spread, 0.10 to 4.60 — and that is the half
  of the argument that has never once flipped; but the panel mean's negativity
  now rests on Pennsylvania alone (**+1.79** without it). See
  [the three things](#the-three-things-in-that-table-that-decide-it).

**What did NOT move is the thing the recommendation rests on.** The county mix
carries 4% to 16% of every measured change, 84% to 135% happens between voters of
the same county, and giving the county term the truth's own unit has never in any
version of this panel bought half the bar (−0.05, +0.30, +0.10, +0.43). That is a
decomposition rather than a score: it does not depend on which folds are in the
panel. Everything that got weaker above is a *score*.

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

It is a separate subcommand and deliberately **not** part of the scheduled
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
* **A cycle is a *target* only where an earlier early electorate exists to compare
  it against.** 2022 was not one until 2026-09-07, when Pennsylvania's 2020 curve
  landed; it is now, for PA alone, and every other state gets the same no-row
  refusal as before. That single extra fold is the panel's only second cycle
  transition — see
  [Two transitions](#two-transitions-and-the-test-the-constant-finally-took).

---

## The dimensions, and the one that could be priced

A composition is a set of weights over groups. Valuing one in 2024 presidential
points requires each group's 2024 presidential margin. There is exactly one
dimension for which this repo holds that number.

| dimension | availability | in `shift_pp`? | why |
| --- | --- | --- | --- |
| **county** | every tracked state with a county file | **yes** | certified 2024 county returns are vendored, and the full-electorate identity above holds exactly |
| **party registration** | ~30 states register by party; KY, MD, ME, NC, CO, IA, PA, FL, NV, SD, LA, OK, OR report it on returned ballots | **no** — reported in its own unit | converting registration points to presidential points needs to know how registered Democrats actually *voted* in 2024. This repo does not have it. Kentucky's registration split sits ten points Democratic of its presidential split (`docs/party-estimate.md`), so the conversion is not a detail |
| **age / race / sex** | `output/demo/*.csv` — GA, LA, MD, MI, NC, SC, WA | **no** — reported as a distance | **group-level 2024 presidential behaviour by demographic was not sourced for this feature.** Saying so plainly is the honest option; inventing a citation is not one |
| **mail / in-person method** | `mail_returned` + `inperson`, every state row; the party crossed with it in `output/methods/*.csv` — FL, KY, ME, NC and CO 2024 | **no** — reported in the truth's own unit, four folds | FL, KY, NC, ME and CO **do** publish party crossed with method and their adapters had been adding the two channels together; `schema.MethodDay` holds the cells now. The between-channel term is exact where it exists and it exists in **4 of 13 folds** — not in PA, IA, OR or OK, which have one usable channel, nor LA, nor MD, whose mail file is a corrupt zip. [Measured](#party-by-method-2026-09-08--the-crosstab-that-did-exist) at +1.77, +0.24 without Florida. The mix itself is readable in **13 of 13** and scored [there](#4-scored-anyway-at-its-very-best-leave-one-state-out) at +0.42 |
| **sub-county geography** | `output/towns/*.csv` — Maine, and only Maine | **no** — one fold | the only within-county dimension carrying the truth's own unit, so its split is exact: −0.94 of Maine's −13.88, against the county level's −0.73. [Measured](#outside-the-counties-2026-09-07--the-three-dimensions-that-vary-inside-one) |

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
early electorate against the 2022 one, and — since Pennsylvania's 2020 curve
landed — on the 2022 one against 2020. **Thirteen** state-cycles qualify: a state
needs a county early series in **both** cycles *and* a reported party split in
both. CO, FL, IA, KY, LA, MD, ME, NC, OK, OR and PA on the 2024-vs-2022
transition, and **OK and PA** on 2022-vs-2020.

**Those two 2022-transition folds are the panel's most valuable rows**, and not
because of what they add to the mean. They are the only thing in this repo that
can hold a *cycle* out — and there are exactly two of them, they disagree by 21
points, and the answer they give changed sign when the second arrived. See
[Two transitions](#two-transitions-and-the-test-the-constant-finally-took).

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

`reach` is the ceiling on the last matched day — the largest \|shift\| county
geography could have reported there whatever its counties had done. See
[The reach bound](#the-reach-bound).

| cycle | state | days | shift (final) | truth (final) | **MAE** | **null** | **gain** | mean \|shift\| | mean \|truth\| | **reach** | r | sign | LOO k | k-gain |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| **2022** | **OK** | **1** | −0.98 | **−10.62** | 9.64 | 10.62 | **+0.98** | 0.98 | 10.62 | 5.02 | n/a | 100% | +0.10 | +0.10 |
| **2022** | **PA** | 15 | −3.11 | **+5.57** | 7.21 | 2.48 | **−4.73** | 4.80 | 2.48 | 6.28 | +0.97 | **7%** | +4.60 | −22.00 |
| 2024 | CO | 4 | +0.11 | −1.98 | 2.29 | 2.14 | **−0.15** | 0.15 | 2.14 | 3.30 | +0.20 | 0% | +1.55 | −0.23 |
| 2024 | FL | 8 | +0.04 | −4.94 | 6.89 | 7.15 | **+0.26** | 0.29 | 7.15 | 3.76 | +0.89 | 62% | +1.51 | +0.39 |
| 2024 | IA | 11 | −1.32 | −23.12 | 22.52 | 23.38 | **+0.86** | 0.86 | 23.38 | 4.15 | −0.59 | 100% | +1.25 | +1.07 |
| 2024 | KY | 6 | +1.43 | −13.22 | 7.10 | 6.54 | **−0.56** | 0.70 | 6.54 | 8.57 | −0.84 | 17% | +1.68 | −1.04 |
| **2024** | **LA** | **1** | +0.50 | −9.79 | 10.29 | 9.79 | **−0.50** | 0.50 | 9.79 | 10.26 | n/a | 0% | +1.64 | −0.82 |
| 2024 | MD | 6 | +0.70 | −6.28 | 7.16 | 5.60 | **−1.56** | 1.56 | 5.60 | 6.17 | +0.97 | 0% | +1.77 | −2.77 |
| 2024 | ME | 21 | −1.18 | −13.99 | 13.39 | 14.37 | **+0.98** | 0.98 | 14.37 | 1.68 | −0.62 | 100% | +1.34 | +1.31 |
| 2024 | NC | 15 | −1.25 | −11.42 | 10.91 | 12.14 | **+1.23** | 1.23 | 12.14 | 5.48 | +0.60 | 100% | +1.34 | +1.64 |
| **2024** | **OK** | **1** | −4.23 | −18.89 | 14.66 | 18.89 | **+4.23** | 4.23 | 18.89 | 7.97 | n/a | 100% | +0.10 | +0.42 |
| **2024** | **OR** | **1** | −0.14 | +0.82 | 0.95 | 0.82 | **−0.14** | 0.14 | 0.82 | 3.68 | n/a | 0% | +1.55 | −0.21 |
| 2024 | PA | 23 | −1.26 | −27.64 | 24.99 | 23.72 | **−1.27** | 2.09 | 23.72 | 4.57 | +0.98 | 52% | +4.60 | −5.84 |

**The four rows in bold are the 2026-09-08 backfills, and every one of them is a
single matched day.** Oklahoma and Louisiana publish one county file per cycle,
dated Election Day; Oregon publishes thirteen county days and the party
registration of returned ballots on **one** of them. So each contributes a single
day-0 comparison against Maine's 21 and Pennsylvania's 23. They are nonetheless the
*most* mature days in the panel: both sides are 100% of a finished curve. Their
four gains average **+1.14** against the nine-fold panel's −0.55, and that alone is
the whole of the headline's move from −0.55 to −0.03. See
[the day-count question](#four-folds-are-one-day-long-and-there-is-still-no-minimum).

Four `truth`s are smaller than their `reach`: Colorado's and Oregon's, where
nothing happened, and **PA 2022's and LA 2024's, where something did** — those two
are the rows to read first, and the model fails both.

| | |
| --- | ---: |
| mean MAE | **10.62 pp** |
| mean MAE of the null (composition unchanged) | **10.59 pp** |
| **what county geography buys** | **−0.03 pp** |
| pooled by day rather than by series (113 days) | −0.52 pp |
| the same over the nine folds that existed this morning | −0.55 pp |
| how far the modelled shift moves | **1.42 pp** |
| how far the composition it stands in for moves | **10.59 pp** |
| leave-one-state-out fitted scale, mean gain | **−2.15 pp** |
| **leave-one-CYCLE-out constant, mean gain** | **+2.19 pp — and see the jackknife** |
| the same, weighting each transition equally | **+1.09 pp** |
| the same, jackknifed (drop one state) | **−2.96 to +4.47** |
| series whose measured change was **beyond county geography's reach** | **9 of 13** |
| **the county × METHOD crosstab, where it exists** | **+1.77 pp on 4 of 13 folds, +0.24 without Florida** |
| the mail/in-person mix × a gap fitted leave-one-state-out | **+0.42 pp on 13 of 13** |

`--validate` prints its own verdict rather than leaving it to this page:

```
VERDICT: NOTHING SHIPS (bar is +1.00 pp of gain over the no-change null; measured -0.03)
```

The bar, `MIN_GAIN = 1.0`, is in code. It is the same bar `regress.py` sets.

### The three things in that table that decide it

**1. It does not beat the null.** −0.03 points on thirteen folds against a bar of
+1.00, and it is negative on seven of them. `docs/party-estimate.md` declined at
+0.79 and `docs/regression.md` declined at +0.17.

⚠️ **And −0.03 is not an improvement on −0.55; it is four single-day folds
joining.** The nine folds that existed this morning still score −0.55 among
themselves. Read a mean over series the way [the band](#the-uncertainty-band)
says to read it: it moves when the population moves, and that is not evidence
about the method.

**2. It barely moves.** The modelled shift travels 1.42 points while the measured
composition travels 10.59. This is the same mechanism `docs/party-estimate.md`
found for `lean_vs_baseline`: **every state we track publishes every one of its
counties**, so once coverage is complete the ballot weights are close to
proportional to county size and the weighted mean is arithmetically pinned near
the state's own last result. A difference of two numbers that are each pinned to
the same place is a small number by construction.

**3. A fitted scale makes it worse on the panel, and cannot agree with itself.**
The leave-one-state-out multipliers are **+0.10, +0.10, +1.25, +1.34, +1.34,
+1.51, +1.55, +1.55, +1.64, +1.68, +1.77, +4.60 and +4.60** — a **forty-sixfold**
spread, and the two ends are two states pulling against each other: fitted without
Oklahoma the multiplier is 0.10, fitted without Pennsylvania it is 4.60, fitted on
everything about 1.5. Applying the one fitted on the other states costs **2.15
points** on average. If county geography were measuring the right thing in the
wrong unit, ONE number would convert it.

> ⚠️ **The SIGN half of this argument has now been wrong in both directions three
> times, which is the most useful thing about it.** Before the maturity gate the
> multipliers were +3.89, +4.13, −1.28, −1.61 — both signs. The gate made every
> one positive and this page duly recorded that the sign half had been noise.
> Adding PA 2024 put the signs back; adding PA 2022 made six of eight negative;
> the thirteen-fold panel makes every one of them positive again. Read the sign
> pattern as a fact about a panel small enough to flip on one fold, in either
> direction. What has never flipped is the magnitude disagreement, and that is the
> part that was ever load-bearing.

> ⚠️ **And two clauses of this argument are weaker than they were on the nine-fold
> panel. Both are stated here rather than left in the code.**
>
> * **Three folds now clear MIN_GAIN individually** under the scale fitted
>   elsewhere — IA +1.07, ME +1.31, NC +1.64 — where none did before. The old
>   assertion "it lifts no fold over the bar" is gone from
>   `test_the_fitted_scale_does_not_agree_with_itself_across_states`, and it was
>   wrong to make: **`MIN_GAIN` is a bar for a panel, not for a single series**,
>   which is the same sentence this page already writes about Maine's towns and
>   about Florida's crosstab. What those three folds share is a small shift being
>   scaled further in a direction the truth was already going — the same
>   arithmetic that makes a constant look good, [below](#where-the-signal-in-this-panel-actually-is-and-why-it-still-cannot-ship).
> * **The panel mean's negativity now rests on one state.** Refit with one state
>   dropped it stays between −2.31 and −2.45 for every state except Pennsylvania,
>   where it becomes **+1.79** — over the bar. Pennsylvania is not trimmable: 2022
>   is the only midterm reference this panel has, and a band or a verdict fitted
>   without the hardest midterm case is wrong in exactly the cycle it is read in.
>   But "one state carries the refusal" is the same shape of finding this page
>   uses to refuse *candidates*, and it is only honest to report it pointing the
>   other way.

**And the third argument is now redundant, because there is a version of the
county term with no unit to fit.** See [Inside the counties](#inside-the-counties):
give the same county mix change the counties' own *registration* margins instead
of their presidential ones and there is no conversion left to get wrong. It buys
**+0.43** on thirteen folds, +0.15 without Oklahoma, and it has never in any
version of this panel reached half the bar.

### Four folds are one day long, and there is still no minimum

*Added 2026-09-08, when the Oklahoma, Louisiana and Oregon backfills made this a
question worth answering rather than a curiosity.*

`score_panel` keys the panel by state-cycle because **the series is the unit** —
pooling by day would score Maine, which contributes 21 days, and call it a
method. That choice has a cost the panel had never had to pay before: four of the
thirteen folds now rest on a **single matched day**.

| fold | matched days | why |
| --- | ---: | --- |
| OK 2022, OK 2024 | 1 each | Oklahoma publishes one county file per cycle, dated Election Day |
| LA 2024 | 1 | the same |
| OR 2024 | 1 | Oregon publishes 13 county days but a party split on only one of them |
| CO 2024 | 4 | |
| PA 2024 | 23 | the longest |

A floor was swept from 1 to 11 days and it is **not adopted**. Three reasons and
one measurement.

**1. There is no defect in the day.** [The maturity gate](#the-maturity-gate)
refuses a day that is a sliver of an electorate. All four single-day folds are
**day-0 pairs** — a finished early electorate against a finished early electorate,
100% of the reference curve on both sides — so they are the *most* mature days in
the panel, not the least. Every table in this document quotes the final matched
day as the cleanest point in a series; these folds are nothing but that point. A
day count is a fact about how much archive a state happens to post, not about the
comparison being made.

**2. It is not the same kind of rule as the maturity gate.** `is_comparable` is a
predicate on the two days in front of it and needs nothing else, which is exactly
why it can be applied identically in `build` and in `score_panel` — the domain
match this page treats as load-bearing. A day count is a predicate on *how many
other days exist*, so on a running 2026 cycle a row would be refused today and
admitted next week. A retroactive publication rule is worse than either answer it
can give.

**3. It decides nothing.** Swept, the headline is:

| minimum days | folds | mean MAE | null | **gain** |
| ---: | ---: | ---: | ---: | ---: |
| 1 (as shipped) | 13 | 10.62 | 10.59 | **−0.03** |
| 2, 3 or 4 | 9 | 11.39 | 10.84 | **−0.55** |
| 5 or 6 | 8 | 12.52 | 11.92 | **−0.60** |
| 8 | 6 | 14.32 | 13.87 | **−0.45** |
| 11 | 5 | 15.81 | 15.22 | **−0.59** |

County geography never comes within a full point of `MIN_GAIN` at any floor, and
is negative at every one of them. Pooling by **day** instead of by series — the
opposite extreme — gives −0.52 over 113 days. The verdict is the same object at
every weighting.

**And the one number a floor does move is the reason not to have one.** The
leave-one-cycle-out constant goes from **+2.19 to −3.43** at a floor of two days,
because OK 2022 is one of the panel's only two 2022-transition folds and dropping
it restores the single-fold holdout that produced this morning's −3.35. A rule
worth adopting only for the answer it produces is not a rule. The constant is
refused on [its own jackknife](#two-transitions-and-the-test-the-constant-finally-took)
instead, which needs no domain change at all.

`--validate` prints the count — *"4 of 13 folds rest on a SINGLE matched day"* —
so a reader can see it, and it is a **reported diagnostic and never a filter**,
for the same reason `in_range` and `inside_share` are.

### What Pennsylvania cost

Pennsylvania was the sixth fold to arrive, and it arrived late for a reason worth
writing down. `pa.py` refused the 2022 file outright — one unreadable free-text party
label was enough to raise `SchemaDrift` — and a 2024 fold cannot exist without a
2022 reference to compare it against. So the panel that fitted this model's error
band had been fitted with **the most volatile mail electorate in the country
missing from it**, and the reason it was missing was 0.04% of typos.

What PA turned out to be worth:

| | |
| --- | ---: |
| the shift PA's own registration measured, 2022 → 2024 | **−27.64 pp** |
| next largest in the panel (ME) | −13.99 pp |
| what this model reported for PA on the final day | −1.26 pp |
| what it reported 22 days out, while truth was already −12.08 | **+10.48 pp** |
| its MAE, against a null of 23.72 | **24.99 pp** |

The 2022 → 2024 move is real and it is not subtle: Republicans who had boycotted
mail voting in 2022 came back to it, and PA's mail electorate went from 76.5% to
62.7% Democratic by registration. County geography sees none of it, because the
counties barely moved — the same people's neighbours voted a different way.

Two things follow, and only one of them is about Pennsylvania.

**The band was understated, and then it was not.** `MODEL_ERROR_PP` went
9.5 → 12.0 on Pennsylvania's arrival — the largest single move it has made, and
it moved because the measurement got honest rather than because the model got
worse. It came back to **10.5** twice over, went to **11.5** when Iowa arrived,
and stands at **11.0** on the thirteen-fold panel — and not one of those moves was
the method changing. Colorado, PA 2022 and the four single-day folds are easy
series joining a mean over series; Iowa was a hard one. Pennsylvania 2024's own
24.99 is still in the panel the band is fitted on, and ±11.0 does not cover it:
this is a **mean** absolute error, never a maximum. See
[The uncertainty band](#the-uncertainty-band) for the full history and for the
second rule, which does not bind today.

**The +10.48 row is the one to look at.** It clears the maturity gate on both
sides with room to spare — 43.6% and 25.8% of a finished curve — so it is not a
phase artefact, which is what every previous row above five points turned out to
be. It is a mature day on which this model points the wrong way by 22.6 points.
That is the difference between a model that is imprecise and a model that is
blind, and it is why the recommendation below is unchanged rather than softened.

⚠️ **The temptation here is to call PA an outlier and trim it. Do not.** 2026 is a
midterm and 2022 is the only midterm reference the panel has. A band fitted
without the hardest midterm case is a band that will be wrong in exactly the
cycle it is read in.

⚠️ **And on 2026-09-08 that instruction started costing something, which is worth
recording rather than hiding.** Pennsylvania is now the single state whose removal
would let a leave-one-state-out fitted scale clear `MIN_GAIN` (+1.79 without it,
against −2.15 with it), and the single state whose removal takes the held-out
constant from +2.19 to +4.47. Two of this page's refusals lean on the fold it
also says must never be trimmed. That is not a contradiction — the reason to keep
Pennsylvania is that it is the hardest *real* midterm case, not that it is
convenient — but a refusal that rests on one state is a weaker refusal than one
that does not, and this page says so in both places.

#### And what Pennsylvania turned out to be, measured

Added 2026-09-07. "The counties barely moved" was an observation about the
predictor. [Inside the counties](#inside-the-counties) makes it a measurement of
the *answer*, by taking the truth apart rather than the model:

| where PA's −27.64 points went | |
| --- | ---: |
| between counties — the county mix moving | **−1.10** (4.0%) |
| inside counties — the same counties returning ballots from different registrants | **−26.54** (96.0%) |
| of the whole move, what the state's entire registered electorate did | −3.57 (12.9%) |

So the −27.64 is not geography, and it is not a different set of registered
Pennsylvanians either. It is the same voters, in the same places, choosing a
different channel — which is exactly what "Republicans came back to mail"
means, and it is a fact about *how people vote*, not about *who they are*. This
model freezes behaviour by construction. Pennsylvania is the case where all of
the movement was behaviour.

That also settles the shape of the failure. −1.10 is not "the model got the sign
of a real geographic move wrong"; it is the entire size of the geographic move.
The published `shift_pp` of −1.26 is, within a rounding error, **right** about
the only thing it is measuring. The number is not wrong. The dimension is empty.

### Seven specifications, none of which rescue it

Before concluding that the dimension is blind rather than miscalibrated, the
knobs were swept rather than argued about. Gain over the null, on the folds that
existed when each was measured — Pennsylvania and Colorado have since been
unlocked, and the two knobs worth re-checking were re-swept on all seven folds
[below](#nine-more-specifications-swept-2026-09-07-and-one-of-them-looked-like-a-finding):

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

### The reach bound

*What county geography is* able *to say, before any comparison with anything.*

Everything above measures how far `shift_pp` landed from the truth. This measures
something the MAE cannot: how far `shift_pp` was **allowed** to land.

`shift_pp` is `Σ_c (w_now,c − w_ref,c) · margin_c`, a difference of two weighted
means over the same counties, so the weight differences sum to zero. Subtracting
the midpoint of the county margins from every margin leaves the sum unchanged and
bounds each recentred margin by half the span, which gives

```
|shift_pp|  ≤  TV(county mix)  ×  (widest county margin − narrowest)
```

and the bound is **tight** — attained exactly when every county gaining share is
the most Democratic one and every county losing it the most Republican. It needs
**no ground truth**, so it is computable on a live 2026 day. `reach_bound()` is
the product, `mix_distance()` the first factor, `margin_span()` the second, and
`test_the_reach_bound_is_a_bound_and_it_is_tight` pins both halves.

| fold | county-mix TV, final day | margin span | **reach** | measured change | how far past |
| --- | ---: | ---: | ---: | ---: | ---: |
| **OK 2022** | 5.97% | 84.0 | **5.02** | **10.62** | 2.12× |
| **PA 2022** | 4.75% | 132.4 | **6.28** | **5.57** | **0.89× — in reach** |
| CO 2024 | 2.41% | 136.9 | **3.30** | 1.98 | **0.60× — in reach** |
| FL 2024 | 3.37% | 111.5 | **3.76** | 4.94 | 1.31× |
| IA 2024 | 3.75% | 110.5 | **4.15** | 23.12 | **5.58×** |
| KY 2024 | 8.34% | 102.7 | **8.57** | 13.22 | 1.54× |
| **LA 2024** | 6.59% | 155.7 | **10.26** | **9.79** | **0.95× — in reach** |
| MD 2024 | 4.66% | 132.3 | **6.17** | 6.28 | 1.02× |
| ME 2024 | 2.51% | 67.0 | **1.68** | 13.99 | **8.33×** |
| NC 2024 | 4.30% | 127.4 | **5.48** | 11.42 | 2.08× |
| **OK 2024** | 9.48% | 84.0 | **7.97** | **18.89** | 2.37× |
| **OR 2024** | 2.80% | 131.2 | **3.68** | 0.82 | **0.22× — in reach** |
| PA 2024 | 3.45% | 132.4 | **4.57** | 27.64 | **6.05×** |

**Nine of thirteen, and all four of the others are worth reading.** Two of them —
Colorado's and Oregon's — are states where nothing happened: their registration
moved 1.98 and 0.82 points. The nine beyond their bounds moved 4.94 to 27.64, and
in every one of those the county mix moved too little for geography to have
reported it at *any* weighting of the counties that actually reported. Maine is
the extreme: its county mix would have had to move **8.3 times as far as it did**
— 20.9 points of total variation instead of 2.51 — before county geography could
even in principle have said what Maine's registration said.

The other two are the interesting ones, and they are the subject of the next
section: **PA 2022 and LA 2024 both had the room and both failed.**

This is the same finding the MAE reports, in a form that does not depend on the
registration unit, on the choice of null, or on how many states are in the
panel. It also settles what Pennsylvania 2024 is: not an outlier this method
happened to miss, but the largest instance of a ceiling that binds wherever there
was anything to see.

#### And being in reach is not the same as reporting the answer

*Added 2026-09-07, and it is the case this section said it would have to report.*

Colorado was the only in-reach series and its composition had not moved, so the
bound had never once been tested on a series that had something to say. Pennsylvania
2022-vs-2020 is that test. Its registration moved **5.57** points against a bound of
**6.28**: the county mix had the room. What the model reported was **−3.11** —
wrong by 8.68 and **pointing the opposite way**, agreeing in sign on **7%** of its
fifteen days, for a gain of **−4.73**, the worst fold in the panel.

⚠️ **And on 2026-09-08 it got a second case, which is the first time this claim
has been able to rest on more than one fold.** Louisiana's early electorate moved
**9.79** registration points between 2022 and 2024 against a bound of **10.26** —
Louisiana's counties are wide (a 155.7-point margin span, the widest in the panel)
and its county mix moved 6.59%, so geography had more room here than anywhere
else. The model reported **+0.50** against a truth of −9.79: not merely too small
but the **wrong sign**, for a gain of **−0.50**. Two in-reach moving series now,
and both of them point the wrong way.

So the two halves of the argument are now separately measured, and they say the
same thing from opposite sides:

* **beyond reach** (nine folds) — the answer was arithmetically unreachable;
* **in reach, and moved** (PA 2022 and LA 2024) — the answer was reachable, and
  the model went the other way both times.

A bound is a statement about what a dimension *can* say. It was never a promise
that the dimension says it, and this is the fold that shows the difference.
`test_the_series_that_came_into_reach_is_the_one_that_points_the_wrong_way` pins
it, and it is the replacement for the test that used to assert every moving series
was out of reach — which was true until it was not, and whose docstring said this
page would have to say so.

#### The consistency question, and the answer that is *not* to copy `estimate.py`

`estimate.py` has an `in_range` concept — `estimate.within_reach` — for a series
whose answer sits further from its counties than `MAX_ADJUSTMENT` lets the model
move. Such a series is **shown and not averaged**, because part of its error is
structural at every parameter value and averaging it prices the cap rather than
the method. The obvious consistency fix here is to do the same thing with
`reach_bound`. **It was implemented, measured, and reverted the same day.**

Two reasons, and the second is the one that settles it:

1. **`estimate.py`'s limit is a chosen cap; this one is the dimension itself.**
   There, excluding an unreachable series protects two fitted constants from a
   target they cannot reach. Here there are no fitted constants in the published
   path, and the limit is not a modelling choice — so a series beyond it is not a
   series this method was measured *unfairly* on. It is the finding.

2. **`in_range` is a condition on the truth, so filtering on it selects the
   quiet states.** This is not a theoretical worry. On **2026-09-07**, Colorado's
   2022 backfill landed as a seventh fold and was at that moment the only in-reach
   series, which switched off the `or list(results)` fallback the rule had been
   inheriting from `estimate.format_validation`. The printed headline silently
   became **Colorado alone, 2.29 against a null of 2.14**, while the all-fold mean
   was **10.39** — and Pennsylvania's 27.64-point move had been dropped out of the
   average entirely. A headline that changes population when one fold crosses a
   threshold is worse than either number it can show.

   Later the same day PA 2022 arrived as a second in-reach series, so the filtered
   headline would now be a two-fold mean rather than a one-fold one. That does not
   rehabilitate the rule; it demonstrates the objection. **A headline whose
   population depends on which folds happen to sit under a truth-derived
   threshold is not a headline.**

So `reach` and `in_range` are **reported diagnostics and never filters** — they
are fields on `Validation` and a column and a line in `--validate`'s output, not
columns in `counterfactual.csv`, which is the same place `estimate.py` keeps its
own. The headline averages every scored series, explicitly rather than as the
fallback branch of a rule.
`test_reach_is_reported_and_is_never_a_filter` builds a two-series panel — one in
reach, one not — and asserts the mean is over both.

### Inside the counties

*The reach bound with the inequality replaced by an equality, and the answer to
Pennsylvania.*

The reach bound is an upper bound and it needs no ground truth, which is what
makes it usable on a live day. Its cost is that it is a worst case: it says the
county mix could not have carried more than 4.57 points in Pennsylvania, not how
much it actually carried. There is an exact version, and it is available
retrospectively because the truth column can be taken apart.

The truth is `(D−R)/(D+R)` of the returned ballots' party registration — and that
is itself a weighted mean over counties. Write a county's two-party registration
base as `n_c = d_c + r_c` and its registration margin as `m_c = 100(d_c−r_c)/n_c`;
then `M = Σ w_c m_c` with `w_c = n_c/Σn`, and

```
M_now − M_ref  =  Σ (w_now,c − w_ref,c)·m_ref,c      BETWEEN counties
               +  Σ w_now,c·(m_now,c − m_ref,c)      WITHIN counties
```

exactly, with no residual. `mix_split()` returns the pair, over the counties both
days reported a party split for — a county with a split on one side only is
dropped rather than guessed at, which is THE BLANK RULE and is also what keeps
both terms over one common support so they add up.

**The first term is `shift_pp`'s own construction with the unit gap removed.**
Same county mix change, same counties, same arithmetic; the only difference is
that each county is valued at its own observed registration margin instead of its
2024 presidential one. That matters because the unit gap is the last excuse the
reach bound could not close — a registration point is not a presidential point,
which is the whole reason `fit_scale` exists. Here there is no gap to fit: a
registration shift is being predicted with registration points. It is also
live-computable, needing only the reference cycle's county registration and this
cycle's county ballot mix.

| fold | days | measured (final) | between | inside | inside % | mean \|between\| | mix MAE | null | **mix gain** |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| OK 2022 | 1 | −10.62 | −0.88 | −9.74 | 92% | 0.88 | 9.74 | 10.62 | **+0.88** |
| PA 2022 | 15 | **+5.57** | **−1.98** | **+7.55** | **135%** | 2.59 | 4.99 | 2.48 | **−2.51** |
| CO 2024 | 4 | −1.98 | +0.05 | −2.03 | 103% | 0.09 | 2.23 | 2.14 | **−0.09** |
| FL 2024 | 8 | −4.94 | −0.13 | −4.81 | 97% | 0.54 | 6.61 | 7.15 | **+0.54** |
| IA 2024 | 11 | −23.12 | −1.92 | −21.20 | 92% | 1.30 | 22.08 | 23.38 | **+1.30** |
| KY 2024 | 6 | −13.22 | −0.03 | −13.19 | 100% | 0.47 | 6.29 | 6.54 | **+0.25** |
| LA 2024 | 1 | −9.79 | −0.68 | −9.12 | 93% | 0.68 | 9.12 | 9.79 | **+0.68** |
| MD 2024 | 6 | −6.28 | +0.59 | −6.87 | 109% | 1.59 | 7.19 | 5.60 | **−1.59** |
| ME 2024 | 21 | −13.89 | −0.74 | −13.16 | 95% | 0.46 | 13.91 | 14.37 | **+0.46** |
| NC 2024 | 15 | −11.42 | −1.85 | −9.57 | 84% | 1.94 | 10.20 | 12.14 | **+1.94** |
| OK 2024 | 1 | −18.89 | **−3.09** | −15.80 | 84% | 3.09 | 15.80 | 18.89 | **+3.09** |
| OR 2024 | 1 | +0.82 | +0.05 | +0.77 | 94% | 0.05 | 0.77 | 0.82 | **+0.05** |
| PA 2024 | 23 | −27.64 | −1.10 | −26.54 | 96% | 0.82 | 23.15 | 23.72 | **+0.57** |

| | |
| --- | ---: |
| mean gain over the no-change null, thirteen folds | **+0.43** |
| the same on the nine folds that existed this morning | +0.10 |
| the same on eight, and on the seven before PA 2022 | −0.05, +0.30 |
| jackknife of the +0.43 — drop one more state | **+0.15 without OK**, +0.30 without NC, +0.36, +0.41, +0.42, +0.43, +0.44, +0.46, +0.47, +0.60, +0.68 |
| how far the between-county term moves | 1.11 pp |
| how far the composition moves | 10.59 pp |

**Four things follow.**

**1. The between-county term never exceeds 3.09 points in any fold.** Against
measured changes of 0.82 to 27.64. It is smaller than the reach bound in every
fold, as it must be, and it is smaller by an order of magnitude in the folds
that moved. (The 3.09 is Oklahoma 2024's, and it is the largest this panel has
ever produced: Oklahoma's county mix moved 9.48%, the most in the panel, and it
still carried 16% of an 18.89-point change.)

**2. Closing the unit gap does not rescue the dimension.** **+0.43** on the
thirteen folds against a bar of +1.00. It has been −0.05 on eight, +0.30 on seven
and +0.10 on nine, and **no version of it has ever reached half the bar**. One
state has carried it every time the panel has been jackknifed, and which state
that is keeps changing: it was North Carolina, then Iowa, and today dropping
Oklahoma takes +0.43 to **+0.15**. That is the same jackknife that turned the flow
specification's +1.59 into +0.27, applied to the strongest possible version of the
county dimension — one that needs no fitted scale, no conversion and no
assumption. `fit_scale` can be retired as an explanation. The unit was never the
problem.

**3. It answers Pennsylvania.** 96% of PA 2024's −27.64 happened between voters of
the same county: the same counties, in the same proportions, returning ballots from
a different set of their own registrants. Republicans who had boycotted mail in
2022 came back to it, and they live where they already lived. There is no
reweighting of Pennsylvania's counties that reports that, there is no *choice of
county values* that reports it either, and the −1.10 the county mix did carry is
the whole of what any county-keyed method could ever have found.

**4. And PA 2022 is the case that shows the county term is not merely small but
mis-signed.** Its between-county term is **−1.98** while the measured change is
**+5.57** — so the inside-county term is +7.55, *more than the whole move*, and
the share reads 135%. The county mix moved the wrong way. That is why `shift_pp`
reports −3.11 there: it is faithfully reporting a real county-mix move that points
against the thing it is standing in for. Small and blind is one failure; small,
blind and anticorrelated is the one that produces a −4.73.

This is why the recommendation does not soften. The reach bound said the answer
was out of range; this says the range was empty.

⚠️ **And it is reported, never a filter — for exactly the reason `in_range` is.**
`inside_share` is a condition on the truth. A rule that dropped the series whose
change was mostly within-county would keep the series whose change was
geographic, which is selection on the outcome in its purest form. `mix_gain` does
not enter `gain`; the headline stays the published method's over every scored
series. `test_the_split_is_reported_and_is_never_a_filter` builds a two-series
panel — one geographic, one not — and asserts the mean is over both.

### Outside the counties, 2026-09-07 — the three dimensions that vary inside one

*[Inside the counties](#inside-the-counties) proves the county dimension is
empty. This is the search for one that is not, and it found nothing that ships.*

> ⚠️ **Written 2026-09-07 as "and it found nothing", re-measured twice on
> 2026-09-08, and the wording had to change.** The method dimension turned out to
> have an exact form (the crosstab, in four folds of thirteen) and then a scorable
> one (`fit_method_gap`, in all thirteen), and neither is nothing: +1.77 where the
> crosstab exists and +0.42 across the panel, against a bar of +1.00. Both are
> refused, on coverage and on a jackknife respectively rather than on the bounds
> §3 uses. §2, §3 and §5 are as written on 2026-09-07 with their tables extended
> to thirteen folds; §4 is rewritten.

The decomposition says 84% to 135% of every measured compositional change
happened between voters of the **same county**, so the only place a county-keyed
model could ever have looked is exhausted. The next question is not another
county term. It is whether any dimension this tracker collects that varies
**within** a county can see what counties cannot. There are three, and all three
were measured.

#### 1. Coverage first, because it decides two of them before any arithmetic

| dimension | where it exists | folds of the thirteen it covers |
| --- | --- | ---: |
| **county** (the incumbent) | every tracked state with a county file | **13 of 13** |
| **mail / in-person method** | `mail_returned` + `inperson`, on every state row | **13 readable, 8 that move** |
| **age / race / sex** | `output/demo/*.csv` — GA, LA, MD, MI, NC, SC, WA | **age 1, race 2, sex 3** |
| **sub-county geography** | `output/towns/*.csv` — Maine, and only Maine | **1 of 13** |

The demographic tables are thinner than they look. Seven states publish one;
**Georgia, Michigan and Washington hold no comparable pair**, **South Carolina
has no party registration** to be scored against and its 2022 curve is the
16,975-ballot stub [the maturity gate](#the-maturity-gate) already refuses,
Maryland publishes sex and nothing else, and Louisiana's fold is a single day. That
leaves **North Carolina** — the one state whose removal already took the county
term's +0.30 to +0.03 — carrying age on its own. `DemoDay` also carries **no party
split**, and no vendored table in this repo prices a demographic band, so the
between-band term cannot be *computed* on this dimension at all. Only bounded.

The town table is one state. That is the disqualification this repo has now
applied six times: *a term available only where the data is richest is not a term
either model can use.*

The method split is the one that is genuinely everywhere — and it is the one the
Pennsylvania finding names, so it gets measured properly below rather than
dismissed on coverage.

#### 2. The method dimension does not exist in Pennsylvania

`method_mix_distance` is `mix_distance` for the mail/in-person partition: the
same total-variation statistic, which over two bands collapses to the absolute
change in mail's share. On the final matched day of each fold:

| fold | TV(**method**) | TV(county) | measured change |
| --- | ---: | ---: | ---: |
| **PA 2022** | **0.00** | 4.75 | +5.57 |
| **PA 2024** | **0.00** | 3.45 | −27.64 |
| **IA 2024** | **0.00** | 3.75 | −23.12 |
| **MD 2024** | **0.00** | 4.66 | −6.28 |
| **OR 2024** | **0.00** | 2.80 | +0.82 |
| NC 2024 | 1.52 | 4.30 | −11.42 |
| CO 2024 | 1.60 | 2.41 | −1.98 |
| KY 2024 | 7.83 | 8.34 | −13.22 |
| OK 2024 | 8.33 | 9.48 | −18.89 |
| ME 2024 | 12.61 | 2.51 | −13.99 |
| LA 2024 | 15.02 | 6.59 | −9.79 |
| FL 2024 | 17.44 | 3.37 | −4.94 |
| **OK 2022** | **28.08** | 5.97 | −10.62 |

**Five of the thirteen folds have no method dimension to look at, and the panel's
two largest measured changes are two of them.** Pennsylvania has no early in-person
voting at all — `pa.py` writes `inperson = None` and the law is why — so its
observed early electorate is 100% mail in 2020, in 2022 and in 2024, and its
method mix moves *exactly* zero. Iowa and Oregon reach the same place by the same
route — one undifferentiated mail figure and no in-person column, mail share 1.000
on every published day. Maryland reaches it for a lesser reason and it is worth
keeping the four apart: Maryland publishes its in-person centres and its mail file
is served as a corrupt zip, so `mail_returned` is **not reported** rather than
absent, and a working mail file would give Maryland a mix. Pennsylvania's zero is
the law and is not fixable by any amount of data.

⚠️ **And the other end of that table moved on 2026-09-08.** OK 2022's method mix
moves **28.08 points**, the largest in the panel — Oklahoma's mail share fell from
63.2% in 2020 to 35.1% in 2022, which is the pandemic mail surge unwinding. Both
of its days are day-0 snapshots at 100% completeness, so this is *not* the
±3-day calendar artefact that inflates Kentucky's mix. That single fold is what
takes the method dimension from "refused by the bound everywhere but Florida" to
"reachable in two of thirteen and therefore something that has to be **scored**",
which is [§3](#3-what-the-other-five-would-need-and-it-is-more-than-exists)
and [§4](#4-scored-anyway-at-its-very-best-leave-one-state-out) below.

That is worth stating plainly, because it corrects a reading of this document.
"The same voters, in the same places, choosing a different channel" is the right
description of PA 2024 and **the channel they switched from is Election Day**,
which this tracker does not observe and never will. Within the early electorate
this repo can see, Pennsylvania's channel composition is a constant. On the
dimension the finding is named after, the county term's −1.10 is not merely
larger than what the method mix could contribute; the method mix contributes
**0.00 by construction**.

`test_the_method_mix_is_frozen_where_the_finding_lives` pins it, phrased against
the panel's largest change rather than against the string `"PA"`, so a future
state in the same position fails it for the same reason.

#### 3. What the other five would need, and it is more than exists

> ⚠️ **CORRECTED 2026-09-08, and the correction is the reason this page has a
> new section.** This paragraph used to open *"No state publishes party crossed
> with method — `schema.py` has no such column"*. **The second clause was true
> and the first was false**, and the two were run together into one sentence for
> two revisions. Five of the states this tracker scrapes publish the crosstab,
> their adapters were parsing it, and every one of them added the two channels
> together at the last step before writing a `CountyDay`. `schema.MethodDay` and
> `output/methods/<st>.csv` now hold it, and it is measured in
> [Party by method](#party-by-method-2026-09-08--the-crosstab-that-did-exist).
> The bound below is still the right tool where the crosstab does **not** exist,
> which is **nine of the thirteen** folds; it is superseded, on the four that have
> one, by the exact term — and on 2026-09-08 it stopped being a clean refusal even
> there, which is what the ⚠️ under the table says.

The between-method term could not be computed when this was written. It can be
**bounded**, by exactly the argument [the reach bound](#the-reach-bound) makes
for counties: for any partition into bands,

```
|between-band|  ≤  TV(band mix)  ×  (widest band margin − narrowest)
```

and a registration margin lives in [−100, +100], so **no band span can exceed
200**. Invert it and each fold names the channel gap its own answer would
require. `required_span` is that number:

| fold | TV(method) | **span it would need** | |
| --- | ---: | ---: | --- |
| PA 2022, PA 2024 | 0.00 | **infinite** | no in-person channel exists |
| IA 2024, OR 2024 | 0.00 | **infinite** | one undifferentiated mail figure |
| MD 2024 | 0.00 | **infinite** | mail unreported, so no mix is visible |
| NC 2024 | 1.52 | **752** | past the 200-point ceiling |
| OK 2024 | 8.33 | **227** | past the 200-point ceiling |
| KY 2024 | 7.83 | 169 | |
| CO 2024 | 1.60 | 124 | |
| ME 2024 | 12.61 | 111 | |
| LA 2024 | 15.02 | 65 | |
| **OK 2022** | **28.08** | **38** | **inside what this repo can observe** |
| FL 2024 | 17.44 | **28** | inside what this repo can observe |

And the gap **is** observable, in two ways now. Indirectly, in the one place this
tracker could always see it: a state whose window opens mail-only reports a day on
which mail's share is 1.000, and on that day the published party margin *is* the
mail channel's. The in-person channel's follows from the final day's identity
`M = w·m_mail + (1−w)·m_ip`. Five series carry it:

| | NC 2024 | FL 2024 | KY 2024 | FL 2022 | NC 2022 |
| --- | ---: | ---: | ---: | ---: | ---: |
| m_mail | +15.8 | +3.7 | +12.0 | +5.0 | +49.1 |
| implied m_in-person | −2.4 | −22.6 | −14.3 | −23.2 | +6.8 |
| **channel gap** | **18.1** | **26.3** | **26.3** | **28.2** | **42.2** |

And directly, since the [crosstab](#party-by-method-2026-09-08--the-crosstab-that-did-exist)
turned out to exist: on the final day of each series that publishes one, the gap
is **CO 2024 19.9, FL 2022 35.5, FL 2024 34.7, KY 2022 23.1, KY 2024 25.5, ME
2022 15.8, ME 2024 24.7, NC 2022 32.7, NC 2024 17.7**. Both routes give the same
envelope, and the wider of the two is the inferred one: **18 to 42 points.**

⚠️ **AND ON 2026-09-08 THAT ENVELOPE STOPPED REFUSING THE DIMENSION EVERYWHERE
BUT FLORIDA, WHICH IS WHY §4 BELOW IS NOW THE ARGUMENT AND THIS TABLE IS NOT.**
Five folds need an arithmetically impossible span, two need more than the
200-point ceiling, four need 65 to 169 — and **two are inside 18-to-42**: Florida,
whose 28 this page has always reported, and **OK 2022, which needs 38**. Oklahoma
does not publish a crosstab, so its channel gap cannot be checked directly; what
can be said is that 38 is a gap this repo has observed elsewhere. The dimension is
out of reach of its own answer in **eleven of thirteen folds**, against the county
dimension's nine of thirteen — still a majority, no longer a clean sweep, and a
bound that refuses eleven of thirteen is a weaker instrument than one that refuses
seven of eight.

That is the same thing PA 2022 was for counties, and it gets the same treatment:
being arithmetically able to report an answer is not the same as reporting it, so
the refusal moves from the bound to the **measurement** in §4.

The same arithmetic disposes of the demographic tables, and it is the only thing
that can, since nothing prices them. North Carolina's age mix moves **12.6**
points of total variation against its county mix's 4.30 — genuinely more, and
stably so across the whole window rather than as a phase artefact — and it would
need a **91-point** span between North Carolina's most Democratic and most
Republican age band to carry the −11.42 on its own. Race needs 271, sex 468, and
Maryland's sex mix needs 872. Age is the only within-county dimension in this repo
whose required span is not arithmetically absurd, and it is available in **one
fold**, in the state the county term's own jackknife already turned on.

#### 4. Scored anyway, at its very best, leave-one-state-out

Coverage and reach are both refusals before the fact, so the method dimension was
scored as a specification as well — `prediction = Δ(mail share) × g`, with the
channel gap `g` fitted **leave-one-state-out** against the same no-change null,
the same panel and the same bar of **+1.00**.

⚠️ **RE-MEASURED 2026-09-08 ON THE THIRTEEN-FOLD PANEL, AND EVERY NUMBER IN THIS
SUBSECTION MOVED TOWARD THE BAR.** The eight-fold figures are kept in the right
column so the movement is visible rather than quietly overwritten.

| | 13 folds | was, 8 folds |
| --- | ---: | ---: |
| **method mix × a channel gap fitted leave-one-state-out** | **+0.42** | −1.52 |
| the same, over the folds whose mix moves at all | **+0.68** (8) | −2.44 (5) |
| method mix × a pinned gap of 20 / 25 / **28** / 30 / 38 points | +0.92 / +1.15 / **+1.29** / +1.37 / +1.58 | — |
| **method mix × a gap fitted leave-one-CYCLE-out** | **+1.33**, +2.28 by transition | not computable |
| county mix in the truth's own unit, for comparison ([above](#inside-the-counties)) | +0.43 | −0.05 |
| county mix **plus** method mix | +0.86 | −1.55 |
| **method mix × its own best gap, in sample, per fold** (oracle) | +5.32 | +5.96 |

Per fold, leave-one-state-out: PA 2022, PA 2024, IA, MD and OR **+0.00** — where
the mix does not move the specification *is* the null — CO +0.40, NC +0.53, OK
2024 +1.68, ME +2.75, LA +3.92, OK 2022 **+5.67**, FL **+5.90**, KY **−15.42**.
Jackknifed, dropping one more state: **−0.75 without OK**, −0.36 without FL,
−0.08 without LA, +0.03 without ME, then +0.38 to +0.49, and **+2.14 without KY**.

`fit_method_gap` is now in `counterfactual.py` beside `fit_scale` and
`fit_constant`, and `--validate` prints the number on every run, for the same
reason those two are there: this refusal has to be re-derived rather than
inherited.

**Four things in that table decide it, and the fourth is the one that settles it.**

**It is under the bar on the protocol every other candidate here is held to.**
+0.42 leave-one-state-out against +1.00, and the jackknife takes it negative the
moment Oklahoma is dropped. The pinned-gap version at an exogenous 28 points — the
midpoint of what the crosstab actually measures — does clear the bar at **+1.29**,
and it falls to **+0.60 without Oklahoma**. That is the identical shape the
daily-flow specification failed in (+1.59 → +0.27 without Pennsylvania) and the
crosstab fails in (+1.77 → +0.24 without Florida). `MIN_GAIN` is a bar for a
panel, and a panel one state wide is not a panel.

**Kentucky's −15.42 is not an outlier, it is the mechanism.** KY's method mix
"moves" 58 points at seven days out, because its 2022 reference series is a single
day near the close while 2024 is still mail-only at that point. The method mix is
a **calendar** variable before it is a compositional one — it is dominated by when
in-person voting opens — and the ±3-day match tolerance that is harmless for
county geography is not harmless for it. The maturity gate does not catch it,
because both days are mature. Note that OK 2022's 28.08 is *not* this: both of its
days are day-0 snapshots at 100% completeness, so its mix really did move.

**The fitted gap is still not one number.** Leave-one-state-out it runs **20.2
(without OK) to 56.6 (without KY)** — and given perfect foresight, each fold's own
best gap is **0.1 (KY), 34.2 (FL), 37.8 (OK 2022), 65.2 (LA), 128.2 (ME), 151.1
(CO), 226.6 (OK 2024) and 572.0 (NC)**. A five-thousand-fold spread; two are past
the arithmetic ceiling altogether and only Florida's 34.2 and Oklahoma's 37.8 are
inside the 18-to-42-point channel gap this repo can observe. This is `fit_scale`'s
failure in a dimension that has no unit gap to blame it on: the channel gap is in
registration points and so is the truth.

**And it can now take the leave-one-cycle-out test — on one fold, and that is the
whole of it.** This subsection used to end *"it can never take the
leave-one-cycle-out test"*, because the panel's only second transition was PA
2022-vs-2020, whose method regressor is identically zero. **OK 2022's is 28.08**,
so the test exists, and the method dimension **passes** it: a gap fitted on the
2022 transition alone and applied to the eleven 2024 folds scores **+1.33** by
fold and **+2.28** weighting each transition equally, over the bar.

Read what that number is made of before reading anything else into it. The gap
fitted on the held-out transition is **37.8, which is OK 2022's own best gap**,
because the other 2022 fold contributes an identically zero regressor. So the
"held-out" parameter is one state's single day, and applying it to eleven folds in
which **mail's share fell in every single one** is one national move learned once
— exactly the object [`fit_constant`](#two-transitions-and-the-test-the-constant-finally-took)
exists to refuse, and refused there for exactly this reason. **Drop Oklahoma and
the test is not computable at all**, which is the same sentence this subsection
carried before Oklahoma arrived. A protocol whose holdout is one fold does not
hold anything out.

#### 5. And finer geography does not rescue geography

The last candidate is the strongest, because it is the only within-county
dimension that carries **the truth's own unit**, which makes the decomposition
exact rather than bounded. Maine publishes returned ballots by **town** with the
party registration on every row — 463 towns inside 16 counties, a partition
twenty-nine times finer than the county one, in the most town-fragmented state in
the country. If the county dimension were failing because counties are large and
internally mixed, this is where the missing movement would be.

`nested_split` inserts that level underneath the county, and the identity gains a
term without losing its exactness:

```
M_now − M_ref  =  Σ_c (W_now,c − W_ref,c)·M_ref,c                    BETWEEN COUNTIES
               +  Σ_c W_now,c · Σ_{t∈c} (w_now,t|c − w_ref,t|c)·m_ref,t
                                                            BETWEEN TOWNS IN A COUNTY
               +  Σ_t w_now,t·(m_now,t − m_ref,t)                      WITHIN TOWNS
```

Maine 2024 against 2022, on the final matched day:

| where Maine's −13.88 went | | |
| --- | ---: | ---: |
| between counties | **−0.73** | 5.3% |
| **between towns of the same county** | **−0.94** | **6.7%** |
| inside towns | **−12.21** | **88.0%** |
| | | residual **3.6e−15** |

Averaged over Maine's 21 scored days: between counties **0.45**, between towns
**0.70**, inside towns **13.10**.

**The between-town term is larger than the between-county term — 1.3 times on
the final day and 1.6 times averaged over the window — and that is the whole of
what it is worth.** Twenty-nine times the
resolution takes geography from 5% of the answer to **12%**, and leaves 87% of it
inside individual Maine towns whose median size is **110 two-party ballots**.
There is no third level to go to; the next one down is the voter, and a partition
into voters is not a dimension, it is the answer.

The reach bound says the same thing before any of that, and needs no truth to say
it. Maine's town mix moves **5.24** points of total variation against the county
mix's 2.51 — so the finer partition genuinely does move more, as a coarsening
argument says it must. At the **arithmetically maximal** 200-point span, where a
town of eleven voters is allowed to be 100% Democratic and the whole moving mass
is allowed to run from that town to its mirror image, the town dimension's
ceiling is **10.47** against a measured **13.99**. It is out of reach of its own
answer at the most generous weighting the arithmetic permits — and the generosity
is not small: across the 81 Maine towns with 500 or more ballots, the ones that
carry any real weight, the actual margin span is **81.3**.

Scored as a predictor on that one fold, county-plus-town buys **+1.15** over the
null against the county term's +0.45. ⚠️ **That is not the dimension working and
it must not be read as one.** The prediction is −1.67 against a truth of −13.99;
what buys the point is a small number pointing the right way, which is the same
arithmetic that gave the county term +0.46 on Maine inside a panel mean of +0.30
that [collapsed to +0.03 without North Carolina](#inside-the-counties). It is one
fold, in one state, on one transition, and it cannot be held out against
anything. `MIN_GAIN` is a bar for a panel, not for a single series.

#### What the whole search comes to

| dimension | between-part, final day | share of the change | folds it covers |
| --- | ---: | ---: | ---: |
| county | −3.09 (OK 2024) … −0.03 (KY) | 0.2% – 16% | 13 of 13 |
| **method**, exactly (the crosstab) | **−5.59** (FL) … **−0.30** (NC) | 3% – 113% | **4 of 13** |
| **method**, as a scored specification | +0.42 pp LOSO, +1.29 at a pinned 28 | — | **13 of 13, 8 that move** |
| **age / race / sex** | unpriceable — no band margins exist anywhere | needs a 91-to-872-point span | 1 – 3 |
| **town** | **−0.94** (Maine) | **6.7%** | 1 of 13 |

⚠️ **The method rows are the only ones in this table that have ever moved, and
they moved twice on 2026-09-08.** First when the crosstab turned out to exist —
it used to read "unpriceable elsewhere" — and again when Oklahoma's fold gave the
scored specification a real regressor and a leave-one-cycle-out test. It is
priceable exactly in four folds of thirteen, it is the largest within-county term
this repo has measured, it is the only within-county dimension defined in all
thirteen, and it still does not ship. See
[§4 above](#4-scored-anyway-at-its-very-best-leave-one-state-out) and
[Party by method](#party-by-method-2026-09-08--the-crosstab-that-did-exist).

**Nothing here changes the recommendation, and one leg of it is weaker than it
was.** The method dimension is no longer refused before the arithmetic in every
fold but Florida; it is refused on a jackknife and on a parameter that cannot
agree with itself, which is a weaker refusal than a bound. The reason the
recommendation nevertheless holds is sharper than "nothing scored well".
The county dimension was blind because every state
publishes all of its counties, so the weights are pinned near county size and the
weighted mean is pinned near the last election. The within-county dimensions are
blind for a *different* reason in each case and the same reason in aggregate:
**the movement is not compositional at all.** Pennsylvania's mail electorate went
from 76.5% to 62.7% Democratic with the same counties, the same channel, and a
move of only 3.57 points in the state's whole registered pool. Maine's moved
13.99 points with 88% of it inside individual towns whose median size is 110
ballots. What changed was **who,
among the same people, in the same places, through the same channel, chose to
return a ballot** — and that is behaviour, which this construction freezes by
definition and which no partition of an electorate can recover.


### Party by method, 2026-09-08 — the crosstab that did exist

*[Outside the counties](#outside-the-counties-2026-09-07--the-three-dimensions-that-vary-inside-one)
said the method dimension could only be bounded, because no state published party
crossed with method. Five of them do. This is what it is worth.*

#### What was actually in the files

The claim was checked against the real source files rather than against the
adapters' output, and it was wrong in five states at once:

| state | where the crosstab is | both cycles? |
| --- | --- | --- |
| **FL** | `PublicStats` renders "Voted Vote-by-Mail" and "Voted Early" as two separate per-county tables, each split Republican / Democrat / Other / NPA | yes — 24 archived 2022 days and 34 archived 2024 days |
| **KY** | the workbook carries `DEM/REP Ballots RETURNED`, `DEM/REP Excused In-person` and `DEM/REP No Excuse In-person` | yes — identical columns in the 2022 and 2024 workbooks, though 2022 is a single day |
| **NC** | one row per ballot, `voter_party_code` beside `ballot_req_type` | yes |
| **ME** | one row per ballot, `P` beside `RECTYPE` | yes |
| **CO** | `Returned_Mail_Ballots_By_County` and `In_Person_Ballots_By_County`, each a county × party matrix | **no** — 2022 ships `In_Person_by_Party_County` and no mail matrix at all |

`schema.MethodDay` is the row type and `output/methods/<st>.csv` the table, built
on the `TownDay` precedent: its own file beside `counties/`, rows riding on
`FetchResult` as an attribute, the band checked at construction so a bad label
fails inside `adapter.fetch` rather than at write time. The band vocabulary is
`normalize.method()`'s and there is no second copy of it. THE BLANK RULE applies
twice: a band the state does not report is an **absent row**, never a row of
zeros, and inside a row an unreported party bucket is blank — Kentucky's NPA and
OTH are blank, and its mail band's total includes FPCA ballots its DEM and REP
columns do not.

#### And one bug fell out of the verification

North Carolina spells `ballot_req_type` **`ONE-STOP`** in its 2022 file and
**`EARLY VOTING`** in its 2024 one. `nc.py` matched the raw cell against a
hand-written tuple of spellings that knew only the 2022 one, so all **4,231,692**
of 2024's in-person ballots fell through both branches and `inperson` published as
a flat **0** on every one of that cycle's 47 days — against a headline of
4,520,768 and a mail figure of 297,034. Zero rather than blank, so it read as
North Carolina reporting that nobody voted early in person: THE BLANK RULE
inverted, which is the worse of the two errors.

Nothing downstream had noticed because `estimate.method_split` believes a state's
own mail figure first and treats the rest of the headline as in-person — a
workaround written for this exact state, naming this exact number, in a docstring
that had been describing a bug rather than a source. The fix is CLAUDE.md rule 4
and nothing else: the label goes through `normalize.method()`, which already knew
both spellings, and one it does not know raises `SchemaDrift`. **The published
`shift_pp`, `truth`, `mix_split` and every scored number are unchanged to twelve
decimal places** — none of them reads `inperson` — so this is a correction to the
published table and not to the finding.

#### The term, and the guard that makes it honest

The predictor is `mix_split`'s between term over the finer partition: cells are
**(county, method)** rather than counties, valued in the truth's own registration
margins, with nothing fitted. `nested_split` with the county as the group takes
it apart, and the first two terms sum to the fine between term exactly:

```
M_now − M_ref  =  Σ_c (W_now,c − W_ref,c)·M_ref,c            BETWEEN COUNTIES
               +  Σ_c W_now,c · Σ_{b∈c} (w_now,b|c − w_ref,b|c)·m_ref,b
                                                    BETWEEN CHANNELS IN A COUNTY
               +  Σ_b w_now,b·(m_now,b − m_ref,b)              WITHIN CELLS
```

⚠️ **A one-band crosstab is not a partition, and Colorado is why
`reconciled_cells` exists.** CO 2022 publishes the in-person matrix and nothing
else, and `co.py` refuses to derive the mail band by subtraction — correctly, for
the same reason `mail_returned` is blank on its county row: it is a number
Colorado did not print. Colorado is an all-mail state, so that one band is
**0.86%** of its ballots. Handed to the split unguarded, the two sides' shared
support becomes Colorado's in-person voters alone, the decomposition reports
−2.45 / +0.00 / −11.87 against a measured change of −1.98, and the fold scores a
spurious **+1.28**. So a county's cells count only where they add up **exactly**
to that county's own party split, on the same day, in the same table — a
reconciliation rather than a threshold, because all five sources are exact
aggregations of the same ballots. Colorado 2022 fails it and Colorado leaves the
cell panel.

#### The measurement

Scored through `score_panel` on mature days, against the same no-change null, at
the same ±3-day match tolerance, on the same panel — thirteen folds since
2026-09-08, and **not one of the four new ones publishes a crosstab**, so every
number in the table below is unchanged and every coverage number is worse:

| fold | days | measured | between counties | **between channels** | inside cells | **cell gain** | same-support county gain |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| FL 2024 | 8 | −4.94 | −0.13 | **−5.59** | +0.77 | **+6.36** | +0.38 |
| KY 2024 | 6 | −13.22 | −0.03 | −1.34 | −11.85 | **−4.40** | +0.25 |
| ME 2024 | 21 | −13.99 | −0.74 | −2.21 | −10.95 | **+2.71** | +0.46 |
| NC 2024 | 15 | −11.42 | −1.85 | −0.30 | −9.27 | **+2.41** | +1.94 |

| | |
| --- | ---: |
| mean gain over the no-change null, four folds | **+1.77 pp** |
| the same-support county term on those four folds | +0.76 pp |
| **jackknife — drop one more fold** | **+0.24 without FL**, +1.46, +1.56, +3.83 |
| with a scale fitted leave-one-state-out (refitted in every fold) | **+0.44 pp** |
| pooled by day rather than by series (50 days) | +2.35 pp |
| folds the crosstab exists in | **4 of 13** (was 4 of 9) |

**It clears MIN_GAIN on the mean and it is refused twice over — and both refusals
are stronger on the thirteen-fold panel than they were on the nine.**

**1. Four of THIRTEEN, and the nine it misses are not a random nine.**
Pennsylvania has no early in-person channel at all; Iowa, Oregon and Oklahoma
report one undifferentiated figure with no usable second band; Louisiana's single
day carries no crosstab. So the panel's **three largest measured changes —
−27.64, −23.12 and −18.89 registration points — have no crosstab to have**, where
before it was two. Maryland's mail file is served as a corrupt zip. Colorado
shipped no 2022 mail matrix. This is the disqualification this repo has now
applied seven times: *a term available only where the data is richest is not a
term either model can use.* The county term is defined in 13 of 13, and the
[scored method specification](#4-scored-anyway-at-its-very-best-leave-one-state-out)
in 13 of 13 as well — which is exactly why the exact term's coverage is the thing
that disqualifies it and the scored one has to be refused on its jackknife.

**2. One state carries the mean, and dropping it leaves a quarter of the bar.**
+1.77 becomes **+0.24** without Florida — the identical shape the daily-flow
specification failed in, where +1.59 became +0.27 without Pennsylvania, and the
scored method specification fails in, where +1.29 becomes +0.60 without Oklahoma.
Florida is not an arbitrary outlier: it is the **one fold with a crosstab that
this page's own `required_span` said the method dimension could reach**, needing a
28-point channel gap against 26.3 observed. That the argument's own prediction
came true in the one place it said it would is the most interesting thing here,
and one fold is still one fold. `MIN_GAIN` is a bar for a panel.

⚠️ **And `required_span` has since named a second reachable fold — OK 2022, at
38 points — which Oklahoma does not publish a crosstab for.** So the one
prediction this argument can make about a fold it cannot measure is untested, and
the honest statement is now "the bound named two folds, one of them has a
crosstab, and in that one the term is worth +6.36 alone and +0.24 across
everything else.

**3. And Kentucky shows what the term is made of.** KY's cell term reads
**+10.20** at seven days out while the truth is **+0.42**, then falls to −1.37 as
the truth falls to −13.22. That is not composition; it is *when in-person voting
opens*. KY's 2022 reference is a single day near the close while 2024 is still
mail-only at the matched day, and the ±3-day tolerance that is harmless for
county geography is not harmless for a channel mix. Sweeping the tolerance moves
KY from −0.58 (±0) to −4.40 (±3) and moves nothing else at all — the method mix
is a **calendar** variable before it is a compositional one, exactly as the
fitted-gap specification found in 2026-09-07's sweep.

**And what the four folds do say, which is worth keeping.** On the final matched
day the cell term carries **116% of Florida's change, 21% of Maine's, 19% of
North Carolina's and 10% of Kentucky's**, against the county term's 3%, 5%, 16%
and 0.2%. So the method dimension is the largest within-county term this repo has
measured — larger than Maine's 463 towns, which took geography from 5% to 12% —
and 79% to 90% of the change is *still* inside a single county-and-channel cell in
three of the four. The conclusion the whole search reaches does not move: the
movement is not compositional. It is the same voters, in the same places, through
the same channel, deciding differently about whether to return a ballot.

`test_the_cell_split_is_exact_and_its_two_halves_add_up` pins the algebra,
`test_a_one_band_crosstab_is_refused_rather_than_averaged_over` pins Colorado's
guard, `test_the_crosstab_is_missing_where_the_panel_moved_most` pins the coverage
disqualification against the live tree, and
`test_the_county_by_method_term_is_carried_by_one_state` pins the jackknife — all
four fail if a backfill changes any of it, so this section gets revisited rather
than inherited. `test_the_cell_term_is_reported_and_is_never_a_filter` keeps the
usual trap shut: `cell_gain` does not enter `gain`, and the headline stays the
published method's over every scored series.

### Nine more specifications, swept 2026-09-07, and one of them looked like a finding

Every figure below is **leave-one-state-out** over the seven folds that existed
on the day it was swept — Pennsylvania's second transition has since made an
eighth — and scored against the same no-change null. The bar is **+1.00**. None of
them came close enough for the extra fold to matter; the two that would have
changed a verdict, the reach refusal and the flow, are re-measured on all eight
below.

| what was varied | measured gain | verdict |
| --- | ---: | --- |
| **counties restricted to those reporting on both days** | −0.16 | the two sides already report the same counties |
| **counties restricted to those the reference cycle reported on ≥50/75/90/100% of its days** | −0.32 to **−0.12** | no county's reporting is unstable enough to matter |
| **maturity denominator: this cycle's own final instead of the reference's** | −0.23 | costs 3 days and makes it worse |
| **maturity denominator: both finals at once** | −0.23 | as above |
| **asymmetric maturity gate — a 7×7 grid of floors on this side × the reference side** | best **+0.33** | the best cell keeps 26 of 83 days; a third of the bar |
| **coverage-weighting: uncovered ballots imputed at the state margin** | −0.15 | dormant, see below |
| **coverage-weighting: shift ÷ coverage, and × the coverage ratio** | −0.15 | dormant, see below |
| **refuse days where the reach bound is small** (≥2 … ≥12 pp) | −0.11 down to −2.17 | refusing the days it *can* speak on makes it worse |
| **the daily FLOW of ballots instead of the cumulative stock** | **+1.59** on the 4 folds that had it, **+0.91** on the 5 that do now | ⚠️ see below — it does not survive |

Day-match tolerance was re-swept on the seven folds as well and still buys
nothing: ±0 −0.17, ±1 −0.17, ±2 −0.18, ±3 −0.15, ±5 −0.17, ±7 −0.15.

**Coverage-weighting is arithmetically dormant, not merely unhelpful.** Every one
of the 83 scored days has county coverage ≥ **0.9931** on *both* sides. There are
no unseen ballots to weight, which is why all three variants land within 0.01 of
the published spec. This is the same fact `docs/party-estimate.md` found, and the
reason the published band's coverage term is always zero.

**The asymmetric gate is the one genuinely new knob, and it points at the
reference side.** Sweeping the two floors independently — which the earlier
single-threshold sweep could not see — the gain moves almost entirely with the
*reference* day's maturity and barely at all with this cycle's: at a reference
floor of 85% the gain is +0.33 whether this side's floor is 0%, 15% or 25%.
Worth knowing (an immature *reference* is the more damaging of the two) and still
three times short of the bar, at the price of two thirds of the domain.

#### Refusing on the reach bound, and the days it would keep

*Re-measured 2026-09-07, because "refuse rather than publish where the dimension
cannot reach" is the obvious use for a bound that needs no ground truth.*

The sweep row above says the accuracy verdict: keeping only the days where the
county mix moved enough for geography to have room takes the gain from −0.15 to
−2.17. The mechanism is worth writing down, because it is not noise and it
generalises.

The most natural threshold is not one of the swept integers but the model's own
error: **refuse to publish where `reach < MODEL_ERROR_PP`**, i.e. where the
largest number the method could possibly output is smaller than its own error
bar. It is live-computable, needs no truth, and it is exactly the observation
[the band](#the-uncertainty-band) already makes. Measured on the published tree it
refuses **99 of 120 rows**, leaving 21 in KY, PA 2022, PA 2024 and TN.

And the fourteen it keeps are the wrong fourteen:

| PA, days out | 22 | 20 | 18 | 16 | 14 | 12 | 8 | 4 | 0 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| reach | 43.4 | 29.5 | 23.9 | 18.9 | 12.3 | 9.4 | 8.0 | 5.0 | 4.6 |
| `shift_pp` | +10.48 | +5.15 | +3.78 | +2.59 | +0.67 | +0.01 | −0.20 | −0.90 | −1.26 |
| truth | −12.08 | −16.71 | −19.79 | −20.29 | −23.43 | −25.23 | −26.80 | −27.97 | −27.64 |
| error | 22.6 | 21.9 | 23.6 | 22.9 | 24.1 | 25.3 | 26.6 | 27.1 | 26.4 |
| the rule | keep | keep | keep | keep | keep | refuse | refuse | refuse | refuse |

The rule keeps PA 2024's ten earliest days — including the **+10.48** row, the
single most wrong number this model has ever published — and refuses the thirteen
on which it is least wrong. Scored, the surviving panel is 20 of 98 days over
three folds, MAE 10.07 against a null of 6.92: **−3.15**. The whole sweep on the
eight-fold panel runs −0.68 at reach ≥ 2 to **−3.29** at reach ≥ 12 — the same
shape as the seven-fold sweep, steeper.

The reason is phase, and it is the same reason the maturity gate exists. A large
county-mix total variation early in a window is not a state whose geography moved;
it is a state whose *counties have not all opened yet*. Reach is a bound on the
model's **magnitude**, not on its **accuracy**, and the days where it is largest
are the days the magnitude is least meaningful. So there is no publication rule to
be had here either: the bound tells a reader what the number could not have said,
and it does not tell the model when to speak.

#### The flow specification, and why a +1.59 is not a finding

`ballots_new` — the ballots a county returned *that day* rather than the running
total — is the only structurally different input in the tree, and the reach bound
is why it was worth trying: the daily flow's county mix moves far more between
cycles than the cumulative stock's does (Pennsylvania **42.8%** of total variation
against 10.5%), so the ceiling on what geography can say lifts with it. It duly
moves further — PA's flow shift travels **8.13** points where the stock's travels
2.09 — and it puts North Carolina inside its bound for the first time.

It measures **+1.59** against the null, over the bar. It does not survive:

| check | result |
| --- | --- |
| like-for-like — the published spec on those same four folds | −0.16, so the flow is worth **+1.75** over it |
| per fold | MD **−1.17**, ME +0.15, NC +1.84, PA **+5.56** |
| **jackknife — drop one more state** | without MD +2.51, without ME +2.08, without NC +1.51, **without PA +0.27** |
| against a leave-one-state-out constant | **−5.57**, and adding it to that constant buys **−0.04** |
| availability | `ballots_new` exists in **5 of 17** state county files (MD, ME, NC, PA, TN) and in 5 of the 8 scoreable folds |

**Drop Pennsylvania and the whole claim goes from +1.59 to +0.27.** One state
carries it, and it is the state whose 2022 → 2024 move is a documented one-off —
Republicans returning to a mail channel they had boycotted, which is exactly what
a *daily arrival mix* would pick up in that one transition and would have no
reason to repeat. It also answers a different question from the one this module
asks: the flow describes who is voting *today*, not the electorate that has voted
so far, and "if exactly that set of voters had turned out in 2024" is a statement
about the stock. And it is unavailable in two of the states this model can be
scored on at all, so adopting it would shrink the evidence as well as change the
question.

`docs/regression.md` is this repo's standing example of specifications that
looked like findings until they were measured against the right null. This is
another.

**Re-measured 2026-09-07 on the eight-fold panel, and it no longer clears the bar
even before the jackknife.** Pennsylvania's 2020 file carries `ballots_new` too,
so PA 2022 is a fifth flow fold — and the flow is **−1.83** on it. The whole
specification falls from **+1.59** on four folds to **+0.91** on five, under
MIN_GAIN, with the same one state still carrying it (**+0.27** without PA). The
like-for-like published spec on those same five folds is −1.07, so the flow is
still worth about two points over it and still measures the wrong quantity: the
stock is what "if exactly that set of voters had turned out" is a statement
about.

#### Where the signal in this panel actually is, and why it still cannot ship

The sharpest measurement of the whole sweep is not about county geography at all.
Fitted leave-one-state-out, a **constant** — "this state's early electorate moved
by whatever the *other* states' registration moved" — scores **6.75 against the
null's 10.24, a gain of +3.49**, three and a half times the bar. (On the
thirteen-fold panel the same protocol gives **+3.42**, so this has never moved
much; what moved is the leave-one-*cycle*-out figure below.) Every predictor
tried is a noisier version of it:

| predictor (all leave-one-state-out) | vs the no-change null | vs the fitted constant |
| --- | ---: | ---: |
| `shift` as published | −0.15 | −3.64 |
| `shift` × a fitted scale | −0.94 | −4.42 |
| `shift` ÷ its own reach bound, scaled | +0.94 | −2.54 |
| sign of `shift`, scaled | −1.32 | −4.80 |
| change in the state's mail share | −1.74 / +2.49 | −5.23 / −0.99 |
| ratio of this cycle's early volume to last's | +1.37 / +1.89 | −2.12 / −1.60 |
| county-mix total variation | +2.69 / +2.68 | −0.80 / −0.80 |
| the reach bound itself | +2.73 / +2.49 | −0.76 / −1.00 |
| the county mix in the truth's **own unit** (`mix_split` between) | +0.30 | −3.19 |
| **the fitted constant alone** | **+3.49** | 0.00 |
| the constant **plus** `shift` | +3.78 | **+0.29** |
| the state's own registered electorate (exogenous, nothing fitted) | **+2.56** | −0.93 |
| that **plus** `shift` | +2.44 | **−0.12** vs itself |
| that **plus** the unit-matched county mix | +2.75 | **+0.19** vs itself |

(Two figures where a predictor was fitted both through the origin and with an
intercept.) Read the last two rows together: **the county term's entire
incremental value, given the constant, is +0.29 points** — under a third of the
bar, and the single most direct statement of what this dimension is worth.

**And the constant must not ship, for a reason that has nothing to do with its
score.** The panel contained exactly **one cycle transition** — all seven folds
were 2024-vs-2022. Leave-one-*state*-out never holds out the transition, so the
constant was fitted on the very thing it would be tested on, and what it had
learned was that 2022 → 2024 was a one-off normalisation. Applying it to
2024 → 2026 asserts the same move happens twice. Colorado made the fragility
visible the moment it arrived: the constant, which scored +5.31 on the six folds
that preceded it, costs **−7.28 points on Colorado alone** and fell to +3.49,
because Colorado is a state where the composition did not move and the constant
insists that it did. It is also in **registration** points, which is the one
conversion this whole document exists to refuse.

⚠️ **That was an argument until 2026-09-07, and it is now a measurement.** Held
out on a cycle rather than a state, the +3.49 became **−3.35** — and on
2026-09-08, with a second 2022-transition fold in the panel, it became **+2.19**.
The refusal survives on the jackknife rather than on the holdout, and it is
weaker than it was. See
[Two transitions](#two-transitions-and-the-test-the-constant-finally-took),
which is rewritten for that.

⚠️ **A justification in the code was wrong, and is now corrected.** `fit_scale`
said it omitted an intercept "because an intercept would be a constant national
shift and the null already owns that". The null is zero and owns no such thing —
the intercept is worth +3.49 while the slope is worth −0.15. The right reason to
refuse the intercept is the one-transition argument above, and it is the stronger
reason.

**And there is now an exogenous version of that constant, which does not have the
one-transition problem.** Each state's own registered electorate moved a measured
amount between 2022 and 2024, and it moves again into 2026; scoring that as the
prediction buys **+2.56** with nothing fitted at all. It is the honest form of
what the constant was groping for, it belongs to the party *count* rather than to
this model, and it is [measured and disqualified below](#three-more-specifications-swept-2026-09-07-and-one-of-them-clears-the-bar).
And the county term's incremental value on top of *it* is the same nothing it was
on top of the fitted constant: the pool alone scores +2.56, the pool plus
`shift_pp` **+2.44** (−0.12), and the pool plus the unit-matched county mix
**+2.75** (**+0.19**).


### Two transitions, and the test the constant finally took

*Added 2026-09-07, when `pa.py` recovered Pennsylvania's 2020 mail curve from the
application-level file and 2022 became a target cycle. **Rewritten 2026-09-08,
when Oklahoma's 2020 snapshot made the 2022 transition two folds wide and the
answer changed sign.** The 2026-09-07 measurement is kept below, because the
difference between the two is the finding.*

> ⚠️ **READ THIS FIRST. THE REFUSAL IS WEAKER THAN IT WAS THIS MORNING.**
> Held out on a cycle, the constant scored **−3.35** on the eight-fold panel and
> scores **+2.19** on the thirteen-fold one — over `MIN_GAIN`, and the strongest
> number this panel has ever produced. It is still refused, but it is now refused
> on a **jackknife** and on **what the score is made of**, not on the holdout
> itself. A refusal that needs two supporting arguments is not the refusal a
> single decisive counterexample was.

#### What changed, and why it is one fold

Oklahoma publishes one county snapshot per cycle, on Election Day, going back to
2020 — so OK 2022-vs-2020 joins PA 2022-vs-2020 and the panel holds **two**
2022-transition folds against **eleven** 2024-transition ones. "Leave one cycle
out" therefore means predicting eleven folds from two, and two from eleven.

| | |
| --- | ---: |
| the constant fitted on the eleven 2024-vs-2022 folds | **−11.16** |
| the constant fitted on the two 2022-vs-2020 folds | **−4.11** |
| — OK 2022's own contribution to it | −10.62 |
| — PA 2022's own contribution to it | +2.40 |

**The two folds inside the thin side of the holdout disagree by 21 points**, and
their mean, −4.11, is what all eleven 2024 folds are scored against. On
2026-09-07 that constant was PA 2022's +2.40 alone, it had the wrong sign for ten
of the eleven, and the answer was −3.35. Oklahoma flips its sign, and with it the
verdict.

| held-out fold | constant fitted elsewhere | **gain** |
| --- | ---: | ---: |
| OK 2022 | −11.16 | **+10.08** |
| PA 2022 | −11.16 | **−11.08** |
| each of the eleven 2024 folds | −4.11 | +0.18 to +4.11, and −4.11 on Oregon |
| | **mean, by fold** | **+2.19** |
| | **mean, weighting each transition equally** | **+1.09** |

#### Why it is refused anyway, and the two reasons are separate

**1. It does not survive its own jackknife**, which is the rule this repo applies
to every candidate over the bar — a +1.59 became +0.27 without one state, a +1.77
becomes +0.24 without Florida, a +0.65 evaporated when a nuisance parameter was
refit jointly. Refitting the constant with one state dropped:

| dropped | **gain, by fold** | by transition |
| --- | ---: | ---: |
| **OK** | **−2.96** | −6.27 |
| MD | +1.93 | +0.74 |
| FL | +1.96 | +0.82 |
| LA | +2.00 | +0.95 |
| NC | +2.04 | +1.06 |
| ME | +2.08 | +1.18 |
| IA / OK 2024 / PA 2024 alone | +2.12 | +1.29 |
| CO | +2.20 | +0.76 |
| KY | +2.25 | +0.96 |
| OR | +2.51 | +0.83 |
| **PA** | **+4.47** | +6.92 |

**−2.96 to +4.47 — an eight-point range on which of two folds you keep**, and the
fold that flips the sign, OK 2022, is a **single matched day**. `constant_gain`
and `constant_jackknife` are in the code and `--validate` prints the whole row.

**2. Nine of the thirteen gains are exactly ±|c|, so the score is a sign count
rather than a fit.** Mean absolute error rewards *any* step in the right
direction, so a fold whose truth lies beyond the constant in the same direction
banks exactly |c| however far beyond it lies. FL, IA, LA, MD, ME, NC, OK 2024 and
PA 2024 all score precisely **+4.107**; Oregon, whose registration moved the other
way, precisely **−4.107**. Only Colorado (+0.18) and Kentucky (+0.51) straddle the
constant and therefore say anything at all about its magnitude.

So what "+2.19" reports is: *ten of eleven 2024 folds moved Republican, and a
constant fitted on two folds from another transition happened to be Republican
too, by 4.1 points.* That is **one observation with n = 2 transitions**, and the
two transitions inside it are 21 points apart. It is not evidence that a constant
carries across cycles; it is evidence that this panel's second transition is
still too thin to hold anything out with.

**And nothing about the original objection has been retired.** A constant is in
**registration** points, which is the one conversion this whole document exists to
refuse; it is undefined for the four tracked states that do not register by party;
and applying a 2022 → 2024 number to 2024 → 2026 is still the claim that a
documented one-off repeats.

#### The 2026-09-07 measurement, kept

*What follows is the eight-fold version, unaltered. It is kept because the
contrast is the point: one extra single-day fold turned a −3.35 into a +2.19, and
a conclusion that fragile is a conclusion about the panel.*

Everything above this line was measured on a panel with one property that no
amount of extra states could fix: **every fold was the same cycle transition.**
Seven states, all of them 2024-vs-2022. That is why the strongest thing in the
whole sweep — a fitted constant, +3.49 over the null, three and a half times the
bar — could not ship. Leave-one-*state*-out holds out a state; it never holds out
the transition, so a constant that had learned "2022 → 2024 moved everyone about
ten points Republican" was being scored on 2022 → 2024.

Pennsylvania is the only state in this repo that can reach back to 2020 — its
mail file is one row per application carrying its own return date, so a single
query rebuilds a whole cycle — and `REFERENCE_CYCLE` now maps 2022 → 2020. That
adds one fold, and the fold is the test.

**The two transitions point opposite ways.**

| | |
| --- | ---: |
| PA's early electorate, 2020 → 2022 | **+5.57 pp** (more Democratic) |
| PA's early electorate, 2022 → 2024 | **−27.64 pp** |
| the constant fitted on the seven 2024-vs-2022 folds | −10.22 |
| the constant fitted on the 2022-vs-2020 fold | +2.40 |

**And held out, it is worse than nothing by ten points.**

| held-out fold | constant fitted elsewhere | MAE | null | **gain** |
| --- | ---: | ---: | ---: | ---: |
| PA 2022 | −10.22 | 12.62 | 2.48 | **−10.14** |
| each of the seven 2024 folds | +2.40 | 12.62 | 10.24 | **−2.38** |
| | | | **mean** | **−3.35** |

Against **+3.49** leave-one-state-out. The whole 6.84-point difference is the
holdout, and it is the clearest single demonstration in this repo of what
`docs/regression.md` exists to warn about: a protocol that does not hold out the
thing that varies will price the thing that does not.

What the constant had learned is a fact about one transition — Republicans
returning to a mail channel they had boycotted in 2022 — and 2020 → 2022 is that
same fact running the other way, because 2020 is when they left it. A constant is
the claim that the move repeats. The first time this panel could check that claim,
it was wrong by ten points against a null of two and a half.

`fit_constant` is in the code for exactly this: it is never used in the published
path and exists so the refusal is measured rather than argued, the same job
`fit_scale` does. `--validate` prints the holdout on every run — with its
per-transition split and its jackknife since 2026-09-08 — and prints
*"not computable — every fold in this panel is the same cycle transition"* if a
future tree ever loses the second one.
`test_the_constant_does_not_survive_a_held_out_cycle` pins it, and the assertion
it pins is now the jackknife rather than `gain < 0`.

**Three warnings about this fold, so it is not over-read. The first one turned out
to be the important one.**

1. **It is one state and one transition.** Leave-one-cycle-out here is a
   single-fold holdout. It settles the constant — one decisive counterexample is
   enough to refuse a claim of universality — and it settles nothing about how
   large the *typical* cross-cycle disagreement is.

   ⚠️ **And that warning was right in a way this page did not expect.** A
   single-fold holdout can be reversed by a single fold, and on 2026-09-08 it was:
   OK 2022 arrived, the fitted constant changed sign, and −3.35 became +2.19. The
   warning was written about over-reading the *refusal*; it applies just as
   exactly to over-reading the reversal.
2. **2020 → 2022 is midterm-from-presidential and 2022 → 2024 is the reverse**,
   and 2020 was Pennsylvania's first general election under no-excuse mail voting
   (Act 77) as well as the pandemic one. Neither transition is a generic year, and
   Oklahoma's 2020 is the pandemic year too.
3. **The band narrowed while the model got worse**, which is the second time in a
   row that has happened and the reason `MODEL_ERROR_PP`'s history comment is as
   long as it is. Measured MAE fell 10.39 → 9.99 because PA 2022's own error
   (7.21) is below the panel mean; the gain fell −0.15 → −0.73 because PA 2022's
   *null* is 2.48. The value stayed at **10.5** anyway — see
   [The uncertainty band](#the-uncertainty-band).

### Three more specifications, swept 2026-09-07, and one of them clears the bar

| what was varied | measured gain | verdict |
| --- | ---: | --- |
| **the county mix valued in the truth's own unit** (`mix_split`'s between term) | **−0.05** on eight folds, +0.30 on the seven that preceded PA 2022 | one state carried even the +0.30; **+0.03** without NC |
| the same with ballot weights rather than registration weights | +0.18 on seven | **−0.05** without NC |
| **per-county-COHORT rather than per-county** | *reach falls* | see below |
| **the state's whole registered electorate, as an exogenous predictor** | **+2.56** on seven | clears the bar, survives the jackknife, and is disqualified — see below |

The first three were swept on the seven-fold panel and re-derived on eight where
the eighth fold has the inputs; the pool predictor is **undefined on PA 2022**,
because `data/meta/party_registration.csv` starts at 2022 and there is no 2020
row to difference against — which is a fourth reason it does not belong here.

**Per-county cohorts cannot say more than counties, and it is arithmetic rather
than a finding.** Grouping counties into cohorts is a coarsening of the county
partition, and total variation cannot increase under coarsening, so the reach
bound can only fall. Measured on the final matched day with 2, 3, 5 and 10
equal-width margin bins:

| state | TV(county) | TV(2) | TV(3) | TV(5) | TV(10) | reach(county) | best cohort reach |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| PA | 3.45% | 1.90% | 1.28% | 2.40% | 2.52% | 4.57 | 3.33 |
| NC | 4.30% | 1.49% | 1.22% | 2.01% | 2.06% | 5.48 | 2.63 |
| ME | 2.51% | 2.20% | 2.13% | 2.13% | 2.20% | 1.68 | 1.48 |
| KY | 8.34% | 1.37% | 1.56% | 1.81% | 2.38% | 8.57 | 2.44 |

Reporting the shift per cohort is strictly weaker than reporting it per county,
in every state and at every number of bins. It is a presentational choice with a
measurable cost and no measurable benefit.

**The state's own registered electorate is the strongest predictor found, and it
does not belong to this model.** `data/meta/party_registration.csv` (vendored
2026-09-07, statewide registration of *all* registered voters, so not circular
with the truth) gives each state's registration margin in each cycle. Using its
2022 → 2024 change as the prediction, with nothing fitted at all:

| | CO | FL | KY | MD | ME | NC | PA |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| the state's pool moved | −0.32 | −7.88 | −4.08 | −1.12 | −3.42 | −3.50 | −3.57 |
| its early electorate moved | −1.98 | −4.94 | −13.22 | −6.28 | −13.99 | −11.42 | −27.64 |
| gain over the null | +0.32 | +5.49 | +0.51 | +1.12 | +3.42 | +3.50 | +3.57 |

Mean **+2.56**, positive on all seven, and it survives every jackknife (+2.07 to
+2.94). It is not the fitted constant in disguise: nothing is fitted, each state
carries its own number, and 2026's numbers already exist, so it is not exposed to
the one-transition problem that
[the leave-one-cycle-out test](#two-transitions-and-the-test-the-constant-finally-took)
has now confirmed disqualifies the constant. It is also the only predictor here
that cannot be checked *against* that test: the registration file starts at 2022,
so PA 2022-vs-2020 — the one fold that would hold a transition out — has no pool
figure to difference.

**It is disqualified here for two reasons that have nothing to do with its
score, and a third that does.** First, the unit. It predicts the *truth column*, in registration points.
This module publishes `shift_pp`, in 2024 presidential margin points, and the
conversion between them is the one thing this whole document exists to refuse. A
registration-point predictor of a registration-point quantity is not a better
counterfactual; it is `party_margin_shift_pp`, which
[the recommendation](#should-this-ship-recommendation) already says to ship as a
count. Second, availability: it is defined in the seven states that register by
party and undefined in OH, TN, TX and VA — which are four of the eleven states
this table publishes for, and are also exactly the states where the model can
never be scored. A term present only where the evidence is is the disqualification
this repo already applies elsewhere. And third, its +2.56 is measured on the one
transition this panel has always been able to fit and has never been able to hold
out; the constant scored +3.49 under exactly that protocol and −3.35 the moment a
cycle was held out, and the pool predictor cannot yet take the same test.

What it is worth keeping is the *number*: Pennsylvania's whole registered
electorate moved **3.57** points between 2022 and 2024 while its early electorate
moved 27.64. So at most an eighth of PA's move is the state's registration
changing. Combined with the 96% that happened inside counties, PA's −27.64 is
neither geography nor a different set of registered voters. It is the same
voters, in the same places, choosing a different channel.

### What the counterfactual says about 2024 and 2022, for the record

`output/counterfactual.csv` today holds **155 rows** — 139 of them 2024-vs-2022
and 16 of them 2022-vs-2020. (It held 260 before the maturity gate; the 182 it
lost were the ones outside the domain the model had ever been scored on.
Pennsylvania, Colorado, Iowa and the 2026-09-08 backfills have since added rows
back.) Eighteen series, of which thirteen can be scored — Ohio, Tennessee, Texas,
Virginia and Wisconsin publish a `shift_pp` and no party registration to check it
against. This is the whole final-day picture:

| cycle | state | days | window (d-out) | `shift_pp` | party shift | age tv | race tv | sex tv |
| --- | --- | ---: | --- | ---: | ---: | ---: | ---: | ---: |
| 2024 | CO | 4 | 8 → 5 | +0.11 | −1.98 | — | — | — |
| 2024 | FL | 8 | 13 → 0 | +0.04 | −4.94 | — | — | — |
| 2024 | IA | 11 | 13 → 0 | −1.32 | −23.12 | — | — | — |
| 2024 | KY | 6 | 7 → 2 | +1.43 | −13.22 | — | — | — |
| **2024** | **LA** | **1** | 0 | **+0.50** | **−9.79** | — | ✓ | ✓ |
| 2024 | MD | 6 | 10 → 5 | +0.70 | −6.28 | — | — | 0.7 |
| 2024 | ME | 21 | 20 → 0 | −1.18 | −13.99 | — | — | — |
| 2024 | NC | 15 | 14 → 0 | −1.25 | −11.42 | 12.6 | 4.2 | 2.4 |
| 2024 | OH | 1 | 0 | −2.22 | — | — | — | — |
| **2024** | **OK** | **1** | 0 | **−4.23** | **−18.89** | — | — | — |
| **2024** | **OR** | **8** | 11 → 0 | **−0.14** | **+0.82** | — | — | — |
| 2024 | PA | 23 | 22 → 0 | −1.26 | −27.64 | — | — | — |
| 2024 | TN | 10 | 15 → 5 | −2.33 | — | — | — | — |
| 2024 | TX | 10 | 13 → 4 | −1.30 | — | — | — | — |
| 2024 | VA | 1 | 0 | −1.31 | — | — | — | — |
| **2024** | **WI** | **13** | 20 → 0 | **−2.49** | — | — | — | — |
| **2022** | **OK** | **1** | 0 | **−0.98** | **−10.62** | — | — | — |
| **2022** | **PA** | 15 | 14 → 0 | **−3.11** | **+5.57** | — | — | — |

Note the two Oregon and Louisiana row counts. Oregon publishes **8** mature
counterfactual rows and is scoreable on **1** of them, because Oregon reports the
party registration of returned ballots on a single day. A state can publish more
rows than it can be measured on, and that gap is its own kind of caveat.

Read the two Pennsylvania rows against each other. Between 2020 and 2022 PA's
early electorate moved **5.6 points more Democratic** by registration and the
model said 3.1 points more *Republican*. Between 2022 and 2024 it moved 27.6
points the other way and the model said 1.3. Same state, same counties, same
arithmetic, and the two answers have nothing to do with the two questions.

Read the North Carolina row across. County geography says the 2024 early
electorate was worth **1.25 points** more Republican than 2022's. The party
registration of those very same ballots moved **11.42 points** (registration
points, a count, not a vote). The age mix moved **12.6 points** of total
variation. **The one dimension we can price is the one that barely moved.**

South Carolina used to be a tenth row here. It is gone, and its absence is the
maturity gate's clearest single case: SC's 2022 county series tops out at
**16,975 ballots** against **1,579,112** in 2024, so thirteen published rows were
measuring a state against a rounding error. It is still a stub with more days in
it: the 2022 curve is 19 days long now and still tops out at the same 16,975.

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

| | before | after | today, thirteen folds |
| --- | ---: | ---: | ---: |
| published rows | 260 | 78 | **155** |
| mean \|shift_pp\| | 4.80 | 1.09 | **1.67** |
| max \|shift_pp\| | **26.16** | 3.65 | **10.48** |
| rows above 5 points | 94 | 0 | **10** |
| rows labelled `confidence: low` | 157 | **0** | **0** |
| validation MAE | 10.37 | 9.09 | **10.62** |
| gain over the null | −0.03 | +0.07 | **−0.03** |

The third column is not the maturity gate coming undone. Every one of those 155
rows still clears it on both sides; the ten above five points are all
Pennsylvania — seven from 2024 and three from 2022 — they are all mature, and
they are all simply wrong. See [Pennsylvania](#what-pennsylvania-cost) below.

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
[the maturity gate](#the-maturity-gate). **Fourteen** of the thirty-five tracked
states clear the first:

| state | 2024 county days | window (days out) | 2022 series? |
| --- | ---: | --- | --- |
| ME | 121 | 120 → 0 | 121 days |
| PA | 70 | 69 → 0 | 70 days — **and 121 days of 2020** |
| WA | 51 | 53 → 0 | — |
| NC | 47 | 46 → 0 | 61 days |
| TX | 34 | 37 → 4 | 34 days |
| SC | 31 | 42 → 0 | 8 days, 16,975 ballots — **a stub** |
| WI | 26 | 34 → 0 | **22 days** — new 2026-09-08; no party registration, so no fold |
| FL | 24 | 58 → 0 | 17 days |
| KY | 16 | 43 → 0 | 1 day |
| IA | 15 | 19 → 0 | 26 days |
| TN | 14 | 20 → 5 | 14 days |
| OR | 13 | 18 → 0 | **13 days** — new; party split on 1 day of 2024 |
| MD | 8 | 12 → 5 | 8 days |
| DE | 6 | 8 → 0 | — |
| CO | 4 | 8 → 5 | 11 days |
| LA | 1 | 0 | **1 day** — new |
| OH | 1 | 0 | 1 day |
| OK | 1 | 0 | **1 day — and 1 day of 2020**, the second state to reach back |
| VA | 1 | 0 | 1 day |

Two states reach back to **2020** and so carry a second cycle transition, for
completely different reasons. Pennsylvania's mail file is one row per
*application* carrying its own return date, so a single query rebuilds a whole
cycle's curve and the 2020 general is still posted. Oklahoma simply posts one
county file per election and has never taken the old ones down — which buys a
single day-0 snapshot per cycle and nothing else. Every other adapter guards its
own earliest cycle and raises `NotYetPublished` for 2020, which is the normal
answer, not an error. See
[Two transitions](#two-transitions-and-the-test-the-constant-finally-took) for
what those two folds are worth, and for how much the answer moved when the second
one arrived.

Florida is on that list because its 2022 and 2024 county curves were recovered
from the Internet Archive (136 captures in 2024, 78 in 2022 — see
`src/ev/adapters/fl.py`). **The same sweep found Illinois genuinely
unrecoverable**: its counts page archives as the empty form and never as the
answer, so IL is one of the **sixteen** — AK, AZ, CA, CT, GA, HI, IL, KS, MI, MN,
MT, ND, NH, NV, NY, SD — that **can never get a counterfactual** until a 2024
county backfill exists for them, no matter how much 2026 data arrives. (Arizona
used to appear in the table above on the strength of one July 2024 snapshot; it
holds no 2024 county rows today.)

⚠️ **That list was twenty-one until 2026-09-08.** DE, LA, OK, OR and WA came off
it in one afternoon. Note what the five arrivals were worth: Louisiana, Oklahoma
and Oregon added **four scoreable folds between them, every one a single matched
day**; Wisconsin added 13 published rows and no fold, because Wisconsin does not
register voters by party; Delaware and Washington added rows and no fold for the
same reason, and neither has a 2022 county series to be a target against. **More
states is not the same as more evidence**, and this page's fifth
[recommendation](#should-this-ship-recommendation) said as much before the fact.

### Why the table has zero 2026 rows today

2026-09-07 is **57 days out**, and the four states with any 2026 county data are
nowhere near an electorate:

* **NC** — 4 days, and **eight ballots in five counties**. Against North
  Carolina's finished 2024 early vote of 4,520,768 that is 0.0002%. The maturity
  gate refuses it; the naive "vs the whole state" comparison would happily report
  **D+2.79** off those eight ballots, which is exactly the number this module
  exists not to publish.
* **FL** — 3 days, no returned ballots yet.
* **IL** — 947,926 mail ballots *requested* and none returned. A request is not a
  vote.
* **ND** — 2 days, none returned.

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

2. **A flat ±11.0 points of model error**, `MODEL_ERROR_PP`. **Two rules set it,**
   and only once has the second ever bound: it is the measured mean
   absolute error, rounded up to the next half point, and it is **never narrower
   than the largest `shift_pp` this model publishes**. A band that did not contain
   the model's own output would be the model making a claim outside its stated
   error. It is empirical, not statistical, and it does **not** shrink as more
   ballots come in, because the error is structural rather than sampling noise.
   Read it as *the size of the compositional change county geography does not
   see*, because that is what it is: the geography moves one and a half points
   and the registration moves ten.
   `test_model_error_matches_the_measured_validation` refits it from `output/` and
   fails if the data moves away from it. Its history:

   | | measured | why | which rule bound |
   | ---: | ---: | --- | --- |
   | 11.5 | — | KY, MD, ME and NC were the only four scoreable state-cycles | — |
   | 10.5 | 10.37 | Florida's Internet Archive curves made a fifth | the mean |
   | 9.5 | 9.09 | the maturity gate corrected the domain | the mean |
   | 12.0 | 11.74 | Pennsylvania 2022 was unlocked | the mean |
   | 10.5 | 10.39 | Colorado's 2022 backfill added a seventh series | the mean |
   | 10.5 | 9.99 | Pennsylvania 2020 added an eighth — **and the value did not move** | **the floor** |
   | 11.5 | 11.39 | Iowa's 2022 backfill added a ninth, and IA 2024's own MAE is **22.52** | the mean |
   | **11.0** | **10.62** | **LA, OK, OR, DE and WI took the panel to thirteen folds** | **the mean** |

   ⚠️ **The last row is dilution in its purest form so far, and the band NARROWS
   while nothing improves.** All four of the new folds are a **single matched
   day** — OK 2022, OK 2024, LA 2024 and OR 2024 — their gains average +1.14
   against the nine-fold panel's −0.55, and that alone moves the mean error
   11.39 → 10.62 and the headline −0.55 → −0.03. No existing fold moved and
   nothing about the method changed. Rule 1 binds: 10.62 rounds up to 11.0. Rule
   2's floor is Pennsylvania's **+10.48**, which 11.0 clears with half a point to
   spare — it has bound exactly once, when a measured 9.99 would otherwise have
   rounded to 10.0.

   ⚠️ **Read the two rows above it beside this one.** Colorado's arrival and PA
   2020's each narrowed the band without improving anything, and PA 2020's
   narrowed it while the **gain got worse** (−0.15 → −0.73). Iowa's widened it
   while the gain got **better** (−0.73 → −0.55). This row narrows it while the
   gain gets better. All four directions have now occurred, which is the clearest
   possible demonstration that a mean absolute error and a gain over a null are
   two different questions and neither is evidence about the other.

   It is also a **mean** absolute error and never a maximum — Pennsylvania 2024's
   own 24.99 is inside the panel this is fitted on, and ±11.0 does not cover it.

**A ±11.0-point band on a shift that runs −7.06 to +10.48 points across every
published row is not a caveat on the number. It is the number.** Before the
maturity gate the file also carried shifts out to ±26, and every one of those was
a phase artefact off a sliver of an electorate. What is left is not: the +10.48
is Pennsylvania on a mature day, and the band has to hold it.

And [the reach bound](#the-reach-bound) says what the band cannot: on the final
matched day of every scoreable series, the *largest shift this method could have
reported at all* was 1.68 to 8.57 points. The band is wider than the whole range
the method is able to occupy.

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
* **County geography is nearly blind to composition.** Measured at −0.03 points
  against the no-change null on the thirteen-fold panel — and −0.55 on the nine
  folds that existed before four single-day folds joined — on a bar of +1.00,
  after more than twenty specifications were swept looking for a knob that would
  change it. This is the finding, not a caveat — and since 2026-09-07 it is a
  decomposition rather than an inference: 84% to 135% of every measured
  compositional change happened *inside* counties, Pennsylvania 2024 96%, and
  giving the county mix the truth's own unit rather than presidential points buys
  +0.43, which is +0.15 without Oklahoma and has never reached half the bar in any
  version.
* **The dimensions that carry the signal are the ones that cannot be priced
  here.** Party registration moved 10.59 points on average — 0.8 in the quietest
  fold and 23.7 in Pennsylvania 2024 — in the same electorates where geography
  moved 1.42. Age moved 12.6 points of total variation in North
  Carolina. Pricing either needs group-level 2024 presidential behaviour that
  this repo does not have and that was not sourced for this feature.

  ⚠️ **And no dimension that varies INSIDE a county rescues either of them —
  though the method dimension came closer on 2026-09-08 than it ever has.**
  All three the tracker collects were measured on 2026-09-07, and the sharpest
  of them was re-measured twice on 2026-09-08: once when the party-by-method
  crosstab turned out to exist, and again when Oklahoma's fold gave it a
  regressor the reach bound could not refuse. The mail/in-person mix is **exactly
  frozen** in Pennsylvania, Iowa, Oregon and Maryland; where the crosstab exists —
  four folds of **thirteen**, and not in the panel's three largest changes — its
  between-channel term is the largest within-county term in this repo and buys
  **+1.77**, which falls to **+0.24** without Florida. Scored as a specification
  across all thirteen folds it buys **+0.42** with a gap fitted
  leave-one-state-out, **+1.29** at a pinned 28-point gap and **+0.60** of that
  without Oklahoma. Age, race and sex have no band margins anywhere in this repo
  and cover one to three folds of thirteen; and
  Maine's 463 towns inside 16 counties move geography's share of its −13.88 from
  5.3% to 12.0%. See
  [Outside the counties](#outside-the-counties-2026-09-07--the-three-dimensions-that-vary-inside-one)
  and [Party by method](#party-by-method-2026-09-08--the-crosstab-that-did-exist).

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

### What got weaker on 2026-09-08, and it should be read before the rest

*This page's job is to record a refusal honestly, which includes recording when
the refusal costs more to make than it used to. Five states' backfills took the
panel from nine folds to thirteen and three of this document's arguments came out
of it weaker. None of them changes the verdict; all three change how much weight it
can carry.*

| argument | on nine folds | on thirteen | still refused because |
| --- | --- | --- | --- |
| **the leave-one-cycle-out constant** | **−3.43**: a decisive counterexample | **+2.19** by fold, **+1.09** by transition — *over the bar* | the jackknife runs −2.96 (without OK) to +4.47 (without PA), and nine of thirteen gains are mechanically ±\|c\| |
| **a fitted scale cannot help** | mean **−3.49**, and **no fold** cleared MIN_GAIN | mean −2.15, but **three folds** clear it, and the mean is **+1.79 without Pennsylvania** | the 46× disagreement between the multipliers, which has never once flipped |
| **the method dimension is out of reach of its own answer** | refused by `required_span` in **8 of 9**, only Florida inside an observable channel gap | refused in **11 of 13**, with **two** folds inside — FL 28 and **OK 2022 at 38** | it has to be *scored* now: +0.42 leave-one-state-out, +1.29 at a pinned gap, +0.60 of that without Oklahoma |

And two things got **stronger**, which is worth the same sentence:

* **The crosstab's coverage disqualification.** The exact between-channel term
  exists in 4 of 13 folds where it was 4 of 9, and the folds it misses now include
  the panel's **three** largest measured changes rather than two.
* **"In reach is not the same as reporting the answer."** It rested on PA 2022
  alone. LA 2024 is a second in-reach series whose composition moved 9.79 points,
  and the model reported +0.50 — the wrong sign again.

⚠️ **And one thing did not move at all, which is the one the recommendation
actually rests on.** The county mix carries 4% to 16% of every measured change,
84% to 135% happens between voters of the same county, and giving it the truth's
own unit has never in any version of this panel bought half the bar. That is a
decomposition, not a score: it does not depend on which folds are in the panel, on
the choice of null, or on the registration unit.


**Not the modelled margin. Yes to the count underneath it.** The verdict is
unchanged on the thirteen-fold panel and **three of the arguments behind it are
weaker than they were on the nine** — see
[what got weaker](#what-got-weaker-on-2026-09-08-and-it-should-be-read-before-the-rest).
Specifically:

1. **Do not publish `implied_margin_2024`, `shift_pp` or "the early electorate is
   N points more Republican" as a modelled figure in any state.** It is worth
   −0.03 points against "assume nothing changed" on a bar of +1.00 — worse
   than saying nothing, and −0.55 on the nine folds that existed before four
   single-day folds diluted it — it moves 1.42 points while the thing it describes
   moves 10.59, and its honest band is ±11.0 points on a quantity that spans
   **seventeen and a half** points across every published row. A band that
   contains every plausible answer says nothing.

   And the accuracy measurement is no longer the strongest argument against it.
   [The reach bound](#the-reach-bound) shows the number is *arithmetically*
   incapable of the job in nine of the thirteen series it can be scored on, needs
   no ground truth to say so, and would say it just as loudly on a 2026 day — and
   on the two series where it *did* have the room and something happened, PA 2022
   and LA 2024, it pointed the wrong way both times.
   [Inside the counties](#inside-the-counties) closes the last hole in that
   argument, which was the unit: 84% to 135% of every measured change happened
   between voters of the same county, and the county mix scored in the truth's
   own registration unit — no conversion, no fitted scale, nothing to calibrate —
   buys +0.43, and +0.15 without Oklahoma.
   [Outside the counties](#outside-the-counties-2026-09-07--the-three-dimensions-that-vary-inside-one)
   closes the one after that, which was "then use a dimension that varies inside
   a county". There are three, none of them ships, and the sharpest reasons need no
   model at all: the method mix — the dimension the Pennsylvania finding
   is *named* after — moves **exactly 0.00** in Pennsylvania, which has no early
   in-person voting, and in Iowa, Oregon and Maryland besides; and Maine's 463
   towns inside 16 counties, twenty-nine times the resolution, take geography from
   5% of its measured change to 12%.
   [Party by method](#party-by-method-2026-09-08--the-crosstab-that-did-exist)
   closes the one after *that*, which was the sentence "no state publishes party
   crossed with method" — five of them do, `schema.MethodDay` now holds the
   cells, and the exact between-channel term buys **+1.77** on the four folds of
   **thirteen** that have one, **+0.24** without Florida alone. It is the largest
   within-county term this repo has measured and it is still one state's.

   ⚠️ **And there is no version of this that refuses instead.** Refusing where the
   reach bound is smaller than the model's own error bar is live-computable and
   needs no truth, and it is the rule the band's own arithmetic suggests. Measured,
   it refuses 99 of 120 published rows and keeps the twenty-one where the model is
   *most* wrong, including Pennsylvania's +10.48; the surviving panel scores
   −3.15. See [Refusing on the reach bound](#refusing-on-the-reach-bound-and-the-days-it-would-keep).
   A **minimum day count** was swept on 2026-09-08 for the same reason and refused
   for a different one: it changes no verdict at any floor from 1 to 11 days, and
   the single number it does change is the one it would be adopted to change. See
   [Four folds are one day long](#four-folds-are-one-day-long-and-there-is-still-no-minimum).

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

   Thirteen tracked states report party on returned ballots (CO, FL, IA, KY, LA,
   MD, ME, NC, NV, OK, OR, PA, SD). The like-for-like version needs it in **two
   consecutive cycles at matching days out**, which today is **thirteen folds
   across eleven states** — CO, FL, IA, KY, LA, MD, ME, NC, OK, OR and PA on
   2024-vs-2022, plus OK and PA again on 2022-vs-2020 — and will be all thirteen
   from 2026 onward, because the tracker is now collecting the curve live for
   every state it follows.

   ⚠️ **Four of those thirteen are a single day**, which matters for this
   recommendation in a way it does not for the model: a count is a count on the
   day it is counted, so a one-day Oklahoma sentence is exactly as true as a
   twenty-three-day Pennsylvania one. What one day cannot do is describe a
   *window*, and most of the interesting things about early voting are things
   that happen across one.

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
   * ~~**A party split BY METHOD**, which no state we track publishes and which
     `schema.py` has no column for.~~ **Found, built and measured on
     2026-09-08.** Five states publish it, `schema.MethodDay` holds it, and the
     between-channel term buys **+1.77** on the four folds that have one —
     **+0.24 without Florida**, and it does not exist in **nine of thirteen** folds
     including the panel's three largest changes. The prediction attached to this
     bullet was right: it is **0.00 in Pennsylvania**, which has only one
     channel, so it does not touch the fold this document turns on. See
     [Party by method](#party-by-method-2026-09-08--the-crosstab-that-did-exist).
     What is still missing, and what would actually change the answer, is the
     next bullet.
   * **A sourced, citable table of 2024 presidential vote by party registration
     and by demographic group, per state**, vendored the way the county baseline
     is. That would let the two dimensions that actually move be priced in
     margin points, and it is the only thing that would. It has to be a real
     source with a real provenance note, not a remembered number.
   * **A 2024 county backfill for the sixteen tracked states that have
     none.** Today the like-for-like comparison is structurally impossible in
     nearly half the tracker, which is a data problem, not a modelling one.

     ⚠️ It would widen the *evidence*, not the *dimension*, and 2026-09-08 is the
     proof. Five states came off that list in one afternoon and the county term
     moved from −0.55 to −0.03 purely by dilution: the four folds they added are
     single days, three of the five states have no party registration at all, and
     nothing new went into `shift_pp`, because
     [the decomposition](#inside-the-counties) says the county mix carried 4% of
     Pennsylvania and 16% of North Carolina and a twentieth state's county mix has
     no reason to carry more. Oklahoma's 16% of an 18.89-point change is the
     largest between-county term this panel has ever produced, and it is still 16%.
   * ~~**A second cycle transition.**~~ **There are two now, and the second one is
     the reason this page had to be rewritten rather than appended to.** PA
     2022-vs-2020 arrived 2026-09-07 and OK 2022-vs-2020 on 2026-09-08, and the
     leave-one-cycle-out answer went from *not computable* to −3.35 to **+2.19**
     on two folds that disagree by 21 points. What that bought is not a licence for
     a constant; it is a demonstration that **a two-fold holdout is not a
     holdout**. The thing still worth wanting is a 2020 county backfill broad
     enough that the thin side of the holdout is not one state's single day. It
     would not rescue the county term either way: the decomposition is a
     within-transition measurement and does not depend on how many transitions
     there are.

**A note on the honest failure.** The interesting result is not the size of the
error bar. It is that the question was well posed and the arithmetic was exact —
the full-electorate identity holds to nine decimal places — and the method still
measured nothing, because the only dimension this repo can price is the one that
barely moves. Every state publishes all its counties, so the county weights are
close to proportional to county size, so the weighted mean is pinned near the
last election, so the *difference* of two such means is pinned near zero. That is
a fact about the data, and no amount of modelling gets around it — and since
2026-09-07 it is a *provable* fact rather than an observed one, because
[the reach bound](#the-reach-bound) is the same sentence written as an
inequality. It is the third
model in this repo to be declined, and it is declined for a more specific reason
than the other two: not "the signal is weak" but "we are looking in the one place
the signal is not."

---

## Where the code is

| | |
| --- | --- |
| `src/ev/counterfactual.py` | the comparison, the dimensions, the band, `validate()`, and the things that measure the refusal rather than argue it: `reach_bound`, `mix_split`, `fit_constant`, `constant_gain` / `constant_jackknife`, and — for the dimensions that vary *inside* a county — `method_mix_distance`, `required_span`, `fit_method_gap` and `nested_split` |
| `src/ev/cli.py` | the `counterfactual` subcommand (that block only) |
| `data/baseline/county_results_2024.csv` | vendored county weights, shared with `estimate.py` |
| `tests/test_counterfactual.py` | 78 tests |
| `output/towns/me.csv` | Maine by town, the one sub-county unit in the repo that carries a party split — read into `DayIndex.town_party` for `nested_split` and never an input to `shift_pp` |
| `output/methods/<st>.csv` | party crossed with METHOD, per county per day — FL, KY, ME, NC and CO. Written by `schema.MethodDay` / `publish.publish_method_daily`, read into `DayIndex.method_party` through `counterfactual.reconciled_cells`, and never an input to `shift_pp` |
| `tests/test_methods.py` | the row type, the published table, and the `FetchResult.extend` edge the attribute mechanism has |
| `tests/fixtures/counterfactual/` | the real North Carolina slice — 100-county baseline, three matched days in each of 2022 and 2024, the state's own rows and its age/race/sex tables (county rows carry the party split too, which is what `mix_split` is pinned on), plus the three real 2026 days |
| `output/counterfactual.csv` | per state per day: implied margin, actual margin, the shift, its band, which dimensions were used and what each covered |

Five tests carry the load, and they are named at the top of the test file.
`test_the_full_2024_electorate_reproduces_the_certified_margin` pins the identity
the whole method rests on. `test_a_state_with_no_reference_series_produces_no_row`
pins the refusal. `test_the_measured_gain_does_not_clear_the_bar` pins the
finding against the live `output/` tree, so this recommendation fails a test
rather than quietly going stale.

Three more carry [the reach bound](#the-reach-bound).
`test_the_reach_bound_is_a_bound_and_it_is_tight` pins the inequality and its
attainment. `test_every_series_whose_composition_moved_is_beyond_the_dimensions_reach`
pins the finding, phrased so that a quiet new state does not break it and a
*moving* state coming into reach does. `test_reach_is_reported_and_is_never_a_filter`
keeps the trap shut: it builds a two-series panel with one series in reach and
one beyond, and asserts the headline is the mean of both.

`test_the_series_that_came_into_reach_is_the_one_that_points_the_wrong_way`
replaces the test that used to assert every *moving* series was out of reach. That
was true until Pennsylvania 2022 arrived, in reach and moving, and the replacement
pins what it turned out to be: still failing, and pointing the wrong way.
`test_the_constant_does_not_survive_a_held_out_cycle` pins
[the other thing PA 2020 unlocked](#two-transitions-and-the-test-the-constant-finally-took),
and it fails loudly if a future tree ever drops back to one cycle transition,
because then the refusal is an argument again rather than a measurement.

And five carry [inside the counties](#inside-the-counties), which is the same
finding with the inequality replaced by an equality.
`test_the_mix_split_is_exact` pins the decomposition — one hand-built pair where
the whole move is the county mix, one where the whole move is inside the
counties, and the real North Carolina slice, whose two terms have to sum to the
truth column the *state* published. `test_a_county_reporting_party_on_one_side_only_is_dropped`
pins THE BLANK RULE, which is also what keeps both terms over one common support.
`test_the_north_carolina_split_is_pinned` pins the real numbers.
`test_the_measured_change_happened_inside_the_counties` pins the finding against
the live tree, as a floor on every series rather than as seven numbers, so a state
whose change really was geographic would fail it.
`test_closing_the_unit_gap_does_not_clear_the_bar_either` pins both the +0.30 and
the jackknife that halves it, and
`test_the_split_is_reported_and_is_never_a_filter` keeps the same trap shut a
second time, because `inside_share` is a condition on the truth exactly the way
`in_range` is.

And six carry
[outside the counties](#outside-the-counties-2026-09-07--the-three-dimensions-that-vary-inside-one).
`test_the_nested_split_is_exact` pins the three-way decomposition on three
hand-built pairs — one move entirely between counties, one entirely between towns
of the same county, one entirely inside a town — so each term is pinned on its
own rather than only in aggregate, and
`test_a_unit_reported_on_one_side_only_is_dropped_from_the_nested_split` pins THE
BLANK RULE that keeps all three over one common support.
`test_finer_geography_does_not_rescue_the_county_dimension` pins Maine's finding
as a ceiling on every scored day, so a state whose change really was sub-county
geography fails it. `test_the_method_mix_is_frozen_where_the_finding_lives` is
phrased against the panel's largest measured change rather than against the
string `"PA"`, so it fails the day Pennsylvania gains an in-person channel or the
day some other state takes over the top of the table.
`test_no_within_county_dimension_covers_the_panel` pins the coverage
disqualification and fails if a backfill ever makes one of these dimensions worth
building on.
`test_the_unpriced_dimensions_mostly_cannot_reach_the_largest_changes` pins
`required_span` against the widest channel gap this repo can observe — and it is
**renamed** as of 2026-09-08, because "cannot" stopped being true: OK 2022 needs
38 points against 18-to-42 observed, so the test now pins that the bound still
refuses a majority *and* that the scored specification refuses the rest.

Two tests were re-derived rather than relaxed on 2026-09-08 and each states its
own finding in its docstring.
`test_the_constant_does_not_survive_a_held_out_cycle` no longer asserts
`gain < 0` — the held-out constant is **+2.19** — and asserts instead that its
jackknife can still take it below the null, that one state moves it by more than
`MIN_GAIN`, and that a majority of the folds score it mechanically at ±\|c\|.
`test_the_fitted_scale_does_not_agree_with_itself_across_states` no longer asserts
that the scale lifts **no** fold over the bar — three folds now clear it, and
`MIN_GAIN` was never a per-series bar — and asserts instead that the folds it
helps are a minority and that **at most one state** carries the refusal.
