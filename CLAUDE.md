# 26_early_vote — ElectIndex early vote tracker

Daily ingest of 2026 early/absentee voting, published as CSVs that
`electindex.com/early-vote/` fetches directly from `raw.githubusercontent.com`.

Code and data live in the SAME repo because GitHub Actions runs the scrapers and
commits their output back here. (This differs from `26_us_forecast_data`, which is
data-only with the model kept local.)

## Run it

```bash
pip install -e ".[dev]"
python -m ev probe                    # which adapters exist and answer today
python -m ev ingest --state NC --dry-run
python -m ev ingest                   # all tracked states -> output/
python -m ev backfill --cycle 2022
pytest
```

## The ladder, top to bottom

| tier | source | gives |
|---|---|---|
| 1 | `adapters/<st>.py` — the state's own file | whatever that state publishes |
| 2 | `adapters/civicapi.py` — civicAPI's national API | statewide + counties + party |
| 3 | `adapters/aggregator.py` — UF Election Lab | statewide only |
| 4 | `adapters/manual.py` — `data/manual/` | a hand-typed statewide total |

Tiers 2-4 are appended to every state automatically (`registry.FALLBACKS`), so a
`TIER1` entry only ever names its own scraper. `publish.py`'s merge is relative
(lower wins) and names no tier number; `schema.TIER_LABELS` is the one place the
numbering is written down. See `docs/civicapi-source.md` for why civicAPI sits
above the aggregator and what it deliberately refuses to publish.

## Writing a state adapter — the contract

Subclass `ev.adapters.base.Adapter` in `src/ev/adapters/<st>.py`, set `state`,
`name` (e.g. `"nc-sbe"`), `tier = TIER_SCRAPER`, implement `fetch`. Register it in
`ev/registry.py`'s `TIER1` as `"<st>:<Class>Scraper"`.

**Four rules, in order of how badly getting them wrong hurts:**

1. **`None`, never `0`, for anything the state does not report.** Blank means "not
   reported"; `0` means "zero ballots". Georgia has no party registration, so
   writing `party_dem=0` there renders as "no Democrat has voted". See THE BLANK
   RULE in `schema.py`.

2. **Raise `NotYetPublished` when the data does not exist yet** — early voting has
   not opened, today's file is not posted. This STOPS the ladder; it does not fall
   through to a weaker source, because a weaker source would invent a zero. This is
   the normal outcome for most states most days before October, and it is not an
   error.

3. **Raise `SchemaDrift` when the file is there but its columns changed.** Do not
   guess a mapping. A silently mis-mapped column publishes confident wrong numbers,
   which is far worse than a gap. `normalize.party()` / `.method()` / `.race()`
   return `None` for labels they do not recognise — that is your drift signal, and
   you must raise rather than bucket the unknown into "other".

4. **Normalize every label through `ev.normalize`.** Never invent your own
   spellings — the frontend knows one vocabulary, not fifteen. County rows are
   keyed by 5-digit FIPS via `normalize.county_fips()`, never by county name
   (Louisiana parishes, Alaska boroughs, and "St." vs "Saint" all break name joins).

Do not set `provenance` yourself; `ladder.py` stamps it from the adapter.

## Tests

Every adapter needs a **saved real sample** of the source file in
`tests/fixtures/<st>/` plus a parse test asserting the canonical rows it produces.
That fixture is the only thing that catches schema drift without hammering live
state election sites, so it is not optional. Keep fixtures small — truncate a huge
file to a few hundred representative rows, but keep the real header verbatim.

## Layout

```
src/ev/
  calendar.py   election dates; days_to_election is the x-axis for ALL comparisons
  schema.py     StateDay / CountyDay / DemoDay + CSV column contract
  normalize.py  party / method / age / race / sex / FIPS vocabulary
  adapters/     base.py (the contract) + one module per state
  registry.py   state -> ordered ladder, resolved lazily
  ladder.py     the walk: NotYetPublished stops, SourceError falls through
  publish.py    merge (better tier and richer row win) + atomic write
  cli.py        ingest / backfill / probe
output/         published; the website reads these
  ev_state_daily.csv        all cycles, all states
  counties/<st>.csv         per state, lazily fetched by the page
  demo/<st>.csv             per state
  towns/<st>.csv            per state; ME and CT report by municipality
  methods/<st>.csv          per state; party CROSSED with mail/in-person, where
                            a state publishes the crosstab rather than the
                            margins separately
  ev_state_meta.csv         EV windows, party-reg flag, 2022/24 finals
  ev_status.json            per-state freshness; drives the page's stale badge
```
