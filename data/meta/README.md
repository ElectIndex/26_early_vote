# data/meta/states.csv — per-state context

The static table the site joins against to give any running count meaning.
Copied verbatim to `output/ev_state_meta.csv` by every ingest run.

## What is filled in, and what is deliberately not

**Filled:** `state`, `name`, `election_date`, `has_party_reg`, `dims_available`.
`dims_available` reflects what each adapter actually found in that state's real
file, not what the state is reputed to publish.

**Blank on purpose — do not guess these:**

- `ev_start_2026` / `ev_end_2026`. Statutory early-voting windows vary, and
  several are defined relative to Election Day rather than as fixed dates. A
  blank renders as "unknown"; a wrong date makes the tracker announce that a
  state's voting has opened when it has not, which is worse than saying nothing.
- `ev_2022_total`, `ev_2024_total`, `turnout_*_total`, `reg_voters_2026`. These
  drive the "% of that state's final early vote" comparison — the number that
  tells a reader whether a count is a lot. A guessed denominator produces a
  confident, wrong percentage, which is the single most damaging error this page
  could make. Each needs sourcing from the state's own canvass report or the EAC
  Election Administration and Voting Survey before it goes in.

The page already degrades correctly on every blank cell: the comparison column
renders "—" rather than inventing a ratio.

## Blanks the pipeline fills in for itself

`ev_2022_total` / `ev_2024_total` are ALSO derived automatically, by
`publish.derive_prior_finals()`, from our own backfilled daily series — but only
where that series actually reaches Election Day (`days_to_election == 0`). A
backfill that stopped a week early would understate the final and inflate every
percentage measured against it, so a partial one yields nothing and the cell
stays blank.

A value typed here by hand ALWAYS wins over the derived one: an official canvass
figure beats our scrape, and silently overwriting it would make this file a lie.
Filling a cell here is therefore how you correct the pipeline.

## has_party_reg

Whether the state records a party on the voter registration record. It is a
DISPLAY HINT only — the party columns in the daily data are authoritative, and
the page decides whether to draw a party split from whether those cells are
populated, not from this flag. Several states sit near the line (Arkansas and
Louisiana's registration forms, and Utah's, are all worth re-checking against the
state's own form before this column is relied on for anything but presentation).
