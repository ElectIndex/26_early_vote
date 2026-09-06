# Results source — where `output/results_state.csv` comes from

The tracker's daily tables answer *how many ballots have come back*. This table
answers the question every reader asks next: **did the early vote tell us
anything?** It carries the actual outcome of each past cycle's statewide races,
on the same `(cycle, state)` key as the curve.

**Every URL below was fetched, and its HTTP status is recorded.** Nothing here is
a guessed URL — see the FEC note at the bottom for what happens when you guess.

---

## The choice: MIT Election Data + Science Lab, Harvard Dataverse

**Chosen because** it is the only source found that is simultaneously (a)
statewide and already aggregated, so nothing has to be summed and therefore
nothing can be summed short; (b) genuinely machine-readable — one flat file per
office, one row per candidate per state per race, with the state's own certified
race total repeated on every row so our arithmetic can be checked against the
source's; (c) **CC0 1.0**, i.e. no licence constraint on republishing; (d)
versioned behind a citable DOI, so a number we publish today can be traced to an
exact file id; and (e) compiled from state certified canvasses by a group whose
methodology is peer-reviewed (*Scientific Data*, 2022).

### The two files we use

| Office | Dataset | DOI | File id | Filename | Fetched |
|---|---|---|---|---|---|
| `president` | U.S. President 1976–2024, v10.1 | `doi:10.7910/DVN/42MVDX` | `13887042` | `1976-2024-president.csv` | **200**, `text/comma-separated-values`, 514,108 B |
| `senate` | U.S. Senate statewide 1976–2024, v8.0 | `doi:10.7910/DVN/PEJ5QU` | `13887039` | `1976-2024-senate-state.tab` | **200**, `text/tab-separated-values`, 632,787 B |

Access endpoint: `https://dataverse.harvard.edu/api/access/datafile/{file_id}`.
Metadata (title, version, licence, file ids):
`https://dataverse.harvard.edu/api/datasets/:persistentId/?persistentId={doi}` —
both **200**. Both datasets report `license: CC0 1.0`.

Headers, verbatim, as parsed:

```
year,state,state_po,state_fips,state_cen,state_ic,office,candidate,party_detailed,
writein,candidatevotes,totalvotes,version,notes,party_simplified

year	state	state_po	state_fips	state_cen	state_ic	office	district	stage	special
candidate	party_detailed	writein	mode	candidatevotes	totalvotes	unofficial	version
party_simplified
```

Both are UTF-8 (the Senate file carries `ANDRÉ`, so this is load-bearing).
Truncated real slices of both, header verbatim, are in
`tests/fixtures/results/`.

---

## Coverage achieved

117 rows, written by `python -m ev results`.

| Cycle | Office | States |
|---|---|---|
| 2024 | `president` | **51** (50 + DC) |
| 2024 | `senate` | **33** |
| 2022 | `senate` | **33** |

Only 2 of those 117 rows carry an `early_share_of_total` today (NC 2022 and NC
2024), because North Carolina is the only state with a completed early-vote
backfill in `output/`. Every other row's cell is **blank, not zero** — see below.

**Gap inside the chosen source:** MEDSL's Senate file has no **Connecticut 2022**
race (Blumenthal/Levy). 34 Class-3 seats were contested; 33 are in the file. That
is a hole in the source, not in the parser, and it is left blank.

---

## Six judgement calls, all locked by tests

### 1. `party_detailed` is read before `party_simplified`, because MEDSL's own simplification is wrong

MEDSL's `party_simplified` maps the label `DEMOCRAT` to `DEMOCRAT` but the label
`DEMOCRATIC` to `OTHER`. Seven rows in the 2022/2024 data are mis-coded by it:

```
2022 IL  TAMMY DUCKWORTH      DEMOCRATIC -> OTHER    2,329,136 votes
2022 MD  CHRIS VAN HOLLEN     DEMOCRATIC -> OTHER    1,316,897 votes
2024 MD  RALPH JAFFE          DEMOCRATIC -> OTHER           25
2024 MD  SHARON E HARRIS      DEMOCRATIC -> OTHER            2
2024 CO  BRIAN ANTHONY PERRY  DEMOCRATIC -> OTHER            2
2024 MD  PAIJ BORING          REPUBLICAN -> OTHER          104
2024 MD  SETHATINA NEWMAN     REPUBLICAN -> OTHER            1
```

Trusting `party_simplified` would have moved 2.3 million Illinois Democratic
votes into `oth_votes` and reported Duckworth's 15-point win as a 30-point
Republican margin. So the pipeline is: `normalize.party(party_detailed)` first;
if that recognises the label, it wins. Only when it returns `None` — fusion
tickets (`WORKING FAMILIES / DEMOCRAT`), state party names (`DEMOCRATIC-NPL`,
`DFL`), or an empty cell — do we fall back to `party_simplified`, whose four
values are mapped explicitly. **An unrecognised value in either column raises
`SchemaDrift`.** Nothing is bucketed into `oth` by default.

### 2. Alaska 2022 publishes BLANK party columns, not zeros

Alaska's ranked-choice Senate return carries an **empty `party_detailed` for
every candidate** and `party_simplified == OTHER` for all of them. Summing that
naively yields `dem_votes=0, rep_votes=0` — a confident claim that neither party
received a vote in a race Lisa Murkowski and Kelly Tshibaka both contested.

So: **a race in which no row resolves to either `dem` or `rep` is treated as "the
source did not report party"**, and `dem_votes`/`rep_votes`/`oth_votes`/
`dem_share`/`rep_share`/`margin`/`winner_party` are all written blank.
`total_votes` (263,526) is still published, because that part *is* reported. This
is THE BLANK RULE from `schema.py`, applied at its sharpest edge.

It fires on exactly one race in 2022–2024. It deliberately does **not** fire on
Nebraska 2024 or Vermont 2024, where `dem_votes = 0` is a *true* statement — no
Democrat was on the ballot — and a genuine `0` is what gets written.

### 3. An independent who caucuses with a party stays in `oth_votes`

Angus King (ME) and Bernie Sanders (VT) are `INDEPENDENT` on the ballot and are
published in `oth_votes`, with `winner_party = oth`. Folding them into `dem`
would make Maine 2024 look like a Democratic hold of a seat no Democrat won.
`normalize.party("independent")` returns `npa`, which this table maps to `oth`
alongside every real third party.

### 4. The November general only — never a runoff, never a concurrent special

* **Runoffs.** Georgia's 2022 Senate runoff (3,541,877 votes, 6 December) is a
  separate election with its own early-vote period. Publishing it against the
  November curve would compare our 8 November ballots against December's result.
  Only `stage == GEN` is published; `GEN RUNOFF` is dropped, and **any other
  stage value raises** rather than being silently skipped.
* **Specials.** California ran a regular and a special Senate election on the
  same day in both 2022 and 2024, as did Oklahoma in 2022 and Nebraska in 2024 —
  same ballot, nearly identical totals. The regular election wins; the special is
  used only where it is the *only* race that cycle.
* **Mode.** MEDSL splits some historical rows by voting mode. A mode-split file
  summed naively double-counts, so anything but `mode == TOTAL` **raises**.

### 5. `total_votes` is the source's number, never ours

`total_votes` is MEDSL's own `totalvotes` for the race — the state's certified
ballots-cast figure — not our sum of the candidate rows. In every 2022 and 2024
Senate race, and in 49 of the 51 2024 presidential returns, the two are
byte-identical. The two that differ:

* **DC 2024**, candidate rows overshoot by **2,535 (0.78%)** — MEDSL lists RFK Jr
  separately *and* inside the aggregate write-in line.
* **NY 2024**, candidate rows fall short by **874 (0.01%)**.

The parser refuses any race where the gap exceeds **2%** (`MAX_TOTAL_DRIFT`),
which is loose enough for those two and tight enough that a half-parsed file
raises instead of publishing.

A consequence worth stating: `oth_votes` is *everything in the state's own race
total that was not a Democratic or Republican candidate line*. In the handful of
states that report them inside the race total, that includes under-votes,
over-votes and Nevada's "None of these candidates". They are not dropped, because
dropping them would silently change the denominator every share is computed
against.

### 6. All `*_share` columns are PERCENTAGE POINTS, 0–100

`dem_share`, `rep_share`, `margin` and `early_share_of_total` are all percentages,
to four decimals, so `margin = dem_share - rep_share` is a margin in points —
positive for a Democratic lead, consistent with the rest of the project.

`early_share_of_total` is the state's **final** early-vote total divided by that
row's `total_votes`. The numerator comes from `output/ev_state_meta.csv`'s
`ev_2022_total`/`ev_2024_total` (hand-entered official figures win, exactly as in
`publish.publish_state_meta`), falling back to `publish.derive_prior_finals`,
which only answers for a series that actually reached `days_to_election == 0`.
**If neither has a number, the cell is blank. It is never guessed and never 0.**

Note the denominator is votes cast *in that race*, not statewide turnout, so a
state's presidential and Senate rows can carry slightly different early shares
from the same numerator. That is honest: they are different denominators.

---

## What we could NOT source

### `governor` — no open statewide dataset exists

MEDSL publishes none. Verified against the API, not assumed:

* `https://dataverse.harvard.edu/api/search?q=*&subtree=medsl_election_returns&type=dataset&per_page=50`
  → **200**, `total_count: 30`. The full list contains president, senate, house,
  county presidential, EPI, and per-cycle precinct datasets. **No gubernatorial
  dataset of any kind.** The only state-office roll-up is *State Office-Level
  Returns 2016* (`doi:10.7910/DVN/XSOFHD`) — 2016 only.
* `https://dataverse.harvard.edu/api/search?q=title:gubernatorial&type=dataset&per_page=25`
  → **200**, 66 hits, all replication archives for individual papers. The only
  returns dataset is Klarner's *Governors Dataset*
  (`doi:10.7910/DVN/PQ0Y1N`, **200**, CC0) — its own description says it "mostly
  covers 1961 to 2010". Useless here.
* `doi:10.7910/DVN/GGSDGB` (a DOI sometimes cited for MEDSL governors) → **404**.

**The path that was evaluated and rejected.** MEDSL's precinct files *do* carry
`GOVERNOR`, and aggregating them works: the 2022 Hawaii file
(`https://raw.githubusercontent.com/MEDSL/2022-elections-official/main/individual_states/2022-hi-local-precinct-general.zip`,
**200**, 115,447 B) sums to Green 261,025 / Aiona 152,237 — Hawaii's certified
numbers exactly. It was rejected because **the precinct data is knowingly
incomplete in states that had governor's races**. The repository's own README
(**200**, 37,933 B) says so:

> "Phillips County was not able to compile precinct-level election results for
> 2022… we do not anticipate that precinct-level results from the 2022 election
> in that county will be made available."

and, for Indiana, "numerous counties did not report precinct-level data".
Arkansas and Indiana both elected governors in 2022. Publishing a statewide
governor total that is quietly short by a county is exactly the "confident wrong
number" this project raises rather than emits. A cross-check *is* available —
aggregate the same file's Senate race and compare it against the authoritative
statewide file — but it can only validate the 26 of 36 states that had a 2022
Senate race, and it validates none of the five states this tracker actually
needs (ME, MI, TX, TN had governor's races and no Senate race; VA had no
statewide race at all). It closes no gap it is worth its risk to open.

The national precinct files are also not a casual dependency:
`STATE_precinct_general.csv` is **983,323,056 B** for 2022
(`doi:10.7910/DVN/OAARCY`) and **1,287,756,337 B** for 2024
(`doi:10.7910/DVN/DODOBJ`).

### `house_total` — MEDSL's file is behind a Dataverse guestbook

`doi:10.7910/DVN/IG0UN2`, *U.S. House 1976–2024* v15.0, would have supplied it.
The dataset metadata is public (**200**) but **every file in it refuses API
download**:

```
GET /api/access/datafile/13592823                    -> 400
GET /api/access/datafile/13592823?format=original    -> 400
GET /api/access/datafile/13592823?gbrecs=true        -> 400
GET /api/access/datafile/12066703  (the codebook)    -> 400
GET /api/access/datafile/8963860   (the v13 file)    -> 400

{"status":"ERROR","message":"You may not download this file without the
 required Guestbook response for guestbookID 458."}
```

The gate is on the dataset, not the file or the version — the codebook and every
archived version are equally blocked. (Harvard Dataverse 6.10.1, per
`/api/info/version`.) **No parser was written for a schema that could not be
read**: guessing the House file's columns from the 1976–2018 CSV on MEDSL's
GitHub would be exactly the mapping guess rule 3 of `CLAUDE.md` forbids.

**The FEC alternative, and why it was not taken.** The FEC's *Federal Elections
2022* report is authoritative and public domain, and its sheet `2. Table 1 GE
Vote for S & H` gives a statewide U.S. House vote for every state:

```
https://www.fec.gov/documents/5676/federalelections2022.xlsx
  -> 200, application/vnd.openxmlformats-...sheet, 3,179,722 B
```

But it does not carry a **party split for the House vote by state** — its Table 2
combines Senate and House — so `house_total` from it would be a total with a
blank margin, which answers nothing. The per-candidate sheet
`8. US House Results by State` (4,472 rows) could be aggregated, but only after
handling district subtotal rows, fusion lines, Louisiana's jungle primary and the
Alaska/Maine RCV columns. And there is no 2024 equivalent yet: the FEC's 2024
report page
(`/introduction-campaign-finance/election-results-and-voting-information/federal-elections-2024/`)
returns **404**, and only presidential results
(`/documents/5645/2024presgeresults.xlsx`) and *candidate* lists
(`/documents/5548/2024congressgecands.xlsx`) are posted. Since 2024 is fully
covered by the presidential row anyway, the whole exercise would buy 2022 only,
for a large parser and several judgement calls.

**Do not guess an FEC document id.** `/documents/3800/federalelections2022.xlsx`
— a plausible-looking guess — returns **200** and redirects to
`https://www.fec.gov/resources/cms-content/documents/2022042801.mp3`, an audio
file. The real ids were read off the FEC's own results page.

### The practical consequence

Of the 21 states with a tier-1 scraper, **five have no 2022 row**: `ME`, `MI`,
`TX`, `TN` (governor's races, no Senate race) and `VA` (no statewide race at all
in 2022 — only U.S. House). All 21 have a 2024 row, because every state elects a
president.

---

## Running it

```bash
python -m ev results                        # both past cycles, both offices
python -m ev results --cycle 2022           # just 2022
python -m ev results --office senate        # just the Senate file
python -m ev results --state NC GA --dry-run
python -m ev results --refresh              # re-download instead of using cache/
```

It is **deliberately not part of the `ingest` walk.** A certified return cannot
change, so re-fetching it on every scheduled run would be pure noise in the commit log;
downloads are cached under `cache/us/` via `_net.get(use_cache=True)` and only
re-fetched on `--refresh`. The write is a keyed merge on
`(cycle, state, office)`, so `--cycle 2022` cannot erase the 2024 rows, and it
goes through `publish._atomic_write`, so the published CSV is LF-terminated and
a reader never sees a half-written file.

`ev.results.publish_results` is a small local merge rather than
`publish.publish_table`: these rows carry no `source_tier` for that function's
tier comparison to reason about, and the table's natural order is
`(cycle, state, office)`, which `publish.py`'s shared sort key does not know
about. The atomic write itself is `publish.py`'s.

## Reported, not fixed (per the file-ownership rules)

`normalize.py`'s `_PARTY_MAP` does not recognise `non partisan` (with a space,
as Louisiana writes it) although it does recognise `non-partisan` and
`nonpartisan`. It lands in `oth` here either way via MEDSL's own
`party_simplified`, so nothing is mis-published — but the owner of
`normalize.py` may want the third spelling.
