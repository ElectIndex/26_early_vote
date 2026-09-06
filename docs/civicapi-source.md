# civicAPI — the tier-2 fallback

*Researched and wired 2026-09-06. Everything below was verified against the live
API on that date; the saved payloads are in `tests/fixtures/civicapi/`.*

## What it is

[civicAPI](https://civicapi.org) is a free, keyless, CORS-open JSON API of live and
historical election results. In 2026 it added an **early-vote tracker**, and that
is what this adapter reads. It is not a scrape of somebody's dashboard: it is a
documented API with a stable `/api/v2/` prefix, an `access-control-allow-origin: *`
header, and a status endpoint.

    GET /api/v2/status                                   -> {"status":"ok"}
    GET /api/v2/early-vote/<ST>/capabilities
    GET /api/v2/early-vote/<ST>/<category>
    GET /api/v2/early-vote/<ST>/<category>/demographics?by=<dimension>[&region=<name>]

`<category>` is `requested` | `returned` | `inperson`, which maps one-to-one onto
our `mail_requested` / `mail_returned` / `inperson`. `<dimension>` is
`party` | `gender` | `race` | `ethnicity` | `age_range`.

## Why it went in at tier 2, above the UF Election Lab

The ladder was scraper -> UF -> manual. UF's national CSV is **statewide only** —
its per-state county files are keyed by county name with no FIPS and no
jurisdiction crosswalk, so aggregator.py deliberately refuses them and a state
that drops to tier 2 loses its county map entirely.

civicAPI's category endpoint returns `regions`, a per-county (per-jurisdiction)
object, **already split by party**, alongside `statewide_total`. So a state whose
own scraper breaks keeps its county layer instead of collapsing to one number.
That is the whole argument for the position, and it is why the insert was worth
renumbering the tiers for.

## Coverage, 2026-09-06

Three states: **NC, IL, FL** — which today are exactly the three with any 2026
early-vote activity at all. Everything else 404s on `/capabilities`. Coverage is
expected to grow as state windows open; nothing in the adapter is pinned to a
state list, so a state appearing upstream needs no code change.

| | NC | IL | FL |
|---|---|---|---|
| party | yes | **no** (`Unspecified`) | yes |
| gender / race / ethnicity / age | yes | no | no |
| regions | 100, all counties | 108 **election authorities** | 67, all counties |

## What the adapter refuses to publish, and why

All five are stated at the top of `src/ev/adapters/civicapi.py`; this is the short
form.

1. **Zero is blank.** The API writes `0` both for "no ballots yet" and for a
   category it is not carrying, and nothing separates them. Same trade as UF.
2. **`Unspecified` is not a party.** A no-registration state still gets a "party"
   split — one bucket holding everything (Illinois: 947,926). Gated on
   `capabilities.provides.party`, which is authoritative.
3. **No age rows.** Its bands are 18-25 / 26-40 / 41-65 / over 65 (UF's bands,
   in fact). Ours are 18-24/25-34/35-44/45-54/55-64/65+. Nothing recovers one
   from the other, and `normalize.age_band` buckets on the low end, so a naive
   pass files two thirds of a state under the wrong band.
4. **No race rows.** civicAPI reports race and ethnicity separately, so its
   "White" includes Hispanic white voters. Every tier-1 adapter here folds
   Hispanic ethnicity *over* race (nc.py says so in its docstring), because
   early-vote coverage universally reports one combined bucket. The race ×
   ethnicity cross-tab needed to reconcile them is not published. **Sex is the
   one demographic we do take.**
5. **No history.** There is no cycle or date parameter; `?date=2026-09-01`
   returns `{"error": "early-vote data not found"}`. So `fetch` guards the
   snapshot date against the requested cycle's window and raises **SourceError**
   (fall through to UF, which does keep a 2024 file) rather than NotYetPublished
   (which would stop the walk and take UF's answer with it). `fetch_history` is
   left unimplemented; `ev backfill` `continue`s past NotYetPublished, so UF
   still gets its turn.

## The Illinois rule

`regions` is keyed by the state's own **jurisdiction** name, and a jurisdiction is
not always a county. Illinois runs elections through 108 election authorities, six
of them cities that report separately from the county around them. civicAPI lists
`City of Chicago` (382,508 mail requests) beside a `Cook` row that is **suburban
Cook only**.

Dropping the six unresolvable names would understate Cook County by 40%. Guessing
is worse. So:

> If every region name resolves through `_fips`, publish the county layer. If any
> does not, publish the statewide row alone and log which names failed.

Illinois is the known case and it costs nothing there — `il.py` publishes
Illinois' counties correctly at tier 1 (it carries the six-city crosswalk), and
civicAPI only ever runs when that has fallen over. Keeping fifty states' worth of
municipal-board crosswalks in a generic tier-2 adapter is the wrong shape; a
statewide-only Illinois is the right degraded answer.

## The tier renumber

civicAPI took tier 2, so:

| | before | after |
|---|---|---|
| our own scraper | 1 | 1 |
| civicAPI | — | **2** |
| UF Election Lab | 2 | **3** |
| hand-entered | 3 | **4** |

`publish.py`'s merge rule is purely relative (lower wins) and names no tier
number, so the insert is safe there by construction. Three places did name
numbers and were changed in the same commit:

* `schema.py` `TIER_LABELS` (which `Provenance.__post_init__` validates against).
* `assets/earlyvote/ev-core.js` `TIERS`, and `ev.css`'s `.ei-ev-badge--tier.is-t4`
  italic (the hand-entered marker, formerly `.is-t3`).
* `ev-table.js`'s Georgia footnote, which matched `status.tier === 2`.

**21 already-published rows** carried the old numbering (all `uf-election-lab`, in
`ev_state_daily.csv`, `demo/ga.csv` and `demo/mi.csv`; nothing was at tier 3).
They were renumbered in the same change, matched on `source_name` and never on
tier alone, so no CSV anywhere carries the old scheme.

## Terms

> "Our data may be freely used for personal, non-commercial, or commercial use...
> attribution is required. Please include either a link to civicapi.org
> (preferred) or mention civicAPI somewhere." — [civicapi.org/faq](https://civicapi.org/faq)

The Info tab's Data credits carries the link, under **National fallbacks**. Keep
it there. `robots.txt` is `Allow: /` for all agents; responses are
`cache-control: public, max-age=15`, and no rate limit is documented — the shared
`_net` throttle applies anyway, and every payload is memoised per process.
