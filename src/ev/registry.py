"""Per-state adapter ladders, resolved lazily.

Ladders are declared as (module, class) strings rather than imported classes so
that a state whose adapter has not been written yet -- or whose module fails to
import -- degrades to its lower tiers instead of breaking the whole run. During
the build-out that means the pipeline is runnable from day one with zero state
scrapers; during the season it means one bad deploy cannot take the job down.

TIER 1 is our own scraper against the state's own file.
TIER 2 is civicAPI, a national early-vote API that answers with counties.
TIER 3 is the UF Election Lab aggregator, which is statewide-only.
TIER 4 is the hand-entered statewide total in data/manual/.

Every state gets tiers 2, 3 and 4 appended automatically, so a ladder entry here
only ever lists its tier-1 scraper.
"""

from __future__ import annotations

import importlib
import logging
from functools import lru_cache

from .adapters.base import Adapter

log = logging.getLogger(__name__)

#: state -> tier-1 adapter as "module_suffix:ClassName", resolved under
#: ev.adapters.<module_suffix>. Priority order for the build-out follows the
#: agreed dimension ranking (county > party > method > demographics) crossed with
#: which states open earliest.
TIER1: dict[str, str] = {
    # Voter-level daily files: county + party + method + demographics.
    "NC": "nc:NCScraper",
    # ⚠️ THE DATA HUB, NOT THE ABSENTEE FILE. `ga:GAScraper` parses the richer
    # mvp.sos.ga.gov download and still works with a hand-minted reCAPTCHA
    # token in $GA_SOS_RECAPTCHA_TOKEN -- it just cannot run unattended, which
    # is the whole finding in docs/georgia-source.md §1. The Data Hub reaches
    # the same state through an anonymous Qlik tenant (§7), so it is the one
    # the daily job can actually use. County + method only; see ga.py.
    "GA": "ga:GADataHubScraper",
    "FL": "fl:FLScraper",
    "NV": "nv:NVScraper",
    "AZ": "az:AZScraper",
    # County + party + method.
    "CO": "co:COScraper",
    "IA": "ia:IAScraper",
    "ME": "me:MEScraper",
    "PA": "pa:PAScraper",
    # County + party + method, from the SoS's Daily Ballot Returns PDF. One
    # download carries the whole daily curve; Oregon is all-mail, so every
    # returned ballot is a mail ballot and `inperson` is always blank.
    "OR": "or:ORScraper",
    # TOWN + county + party + method, from per-ballot files. New England runs
    # elections by municipality, so these publish town rows and let the county
    # rows fall out of the 10-digit cousub GEOID. See _towns.py.
    "CT": "ct:CTScraper",
    # County + method only. Delaware DOES register by party; its voting-method
    # report just does not break it out, so every party field is blank there.
    "DE": "de:DEScraper",
    # County + party + sex, in-person early voting only.
    "MD": "md:MDScraper",
    # County + party (DEM/REP only) + method.
    "KY": "ky:KYScraper",
    # County + party + method, from two DevExpress dashboards. The live one is
    # absentee-only, so `inperson` is blank until voter-history credit appears.
    "OK": "ok:OKScraper",
    # County + method (no party registration in these states).
    "IL": "il:ILScraper",
    "SC": "sc:SCScraper",
    "MI": "mi:MIScraper",
    "VA": "va:VAScraper",
    "OH": "oh:OHScraper",
    "WI": "wi:WIScraper",
    "TX": "tx:TXScraper",
    "TN": "tn:TNScraper",
    # County + method, from a stable .gov index page over cycle-specific
    # Datawrapper charts. Registers by party; the only party column the source
    # publishes is a PRIMARY BALLOT CHOICE, so none is published. See id.py.
    "ID": "id:IDScraper",
    "MN": "mn:MNScraper",
    "CA": "ca:CAScraper",
    # Voter-level daily file: county + method + sex, no party registration.
    "WA": "wa:WAScraper",
    # County + method, absentee only. Neither state registers voters by party.
    # Montana's dashboard is dated and named by its PDF export; Hawaii's report
    # names its own election, which is what keeps a primary out of the general.
    "MT": "mt:MTScraper",
    "HI": "hi:HIScraper",
    # Statewide only (no county breakdown published), but with party + method.
    "SD": "sd:SDScraper",
    # Statewide only, method only -- Alaska's report is keyed by House district,
    # which does not nest into boroughs, so no county rows are possible.
    "AK": "ak:AKScraper",
    #
    # ⚠️ NEW HAMPSHIRE IS DELIBERATELY ABSENT, and it is not a coverage gap.
    # NH HAS NO EARLY VOTING. There is no in-person early period and no
    # no-excuse absentee; a voter needs a statutory reason. So there is nothing
    # for this pipeline to count, which is a different statement from "the state
    # publishes nothing" -- the shape every other missing state has.
    #
    # Tracking it put a row on the public board badged "pending", which reads as
    # "this state has not opened yet" and is false: it will not open, because
    # there is no window to open. `src/ev/adapters/nh.py` and `tests/test_nh.py`
    # are KEPT as the record of the research (the 2020 weekly prose, the 403,
    # the absence of any machine-readable file) -- they simply are not wired
    # here, and `ev probe` no longer asks. See docs/coverage-research.md.
    # Statewide only, method only -- Kansas's advance-voting Power BI model has
    # no geography column at all. Kansas DOES register by party; the dashboard
    # just does not break it out. See ks.py.
    "KS": "ks:KSScraper",
    # County + method, from 53 stateless ASP.NET postbacks. North Dakota has no
    # voter registration at all, so every party field is blank. See nd.py.
    "ND": "nd:NDScraper",
    # PARTIAL: the NYC Board covers 5 of New York's 62 counties, so this one
    # publishes county rows and never a statewide row. See ny.py.
    "NY": "ny:NYScraper",
    # Parish + day, counted off Louisiana's daily early-voter roster PDFs. The
    # roster carries no party or method; the post-election report that does is
    # used only for backfill. See la.py.
    "LA": "la:LAScraper",
}

FALLBACKS: tuple[str, ...] = (
    # Ordered best-first for readability; ladder() sorts on Adapter.tier anyway,
    # so a mistake here cannot silently reorder the walk.
    "civicapi:CivicApiAdapter",
    "aggregator:AggregatorAdapter",
    "manual:ManualAdapter",
)


def _resolve(spec: str, state: str) -> Adapter | None:
    module_suffix, _, class_name = spec.partition(":")
    dotted = f"{__package__}.adapters.{module_suffix}"
    try:
        module = importlib.import_module(dotted)
    except ModuleNotFoundError:
        # Expected while adapters are still being written. Not an error.
        log.debug("%s: no adapter module %s yet", state, dotted)
        return None
    except Exception:  # noqa: BLE001 -- a broken module must not kill the run
        log.exception("%s: adapter module %s failed to import", state, dotted)
        return None

    cls = getattr(module, class_name, None)
    if cls is None:
        log.error("%s: %s has no %s", state, dotted, class_name)
        return None
    try:
        return cls(state=state)
    except Exception:  # noqa: BLE001
        log.exception("%s: %s could not be constructed", state, class_name)
        return None


def ladder(state: str) -> list[Adapter]:
    """The ordered adapter list for one state, best tier first."""
    state = state.upper()
    specs = ([TIER1[state]] if state in TIER1 else []) + list(FALLBACKS)
    adapters = [a for a in (_resolve(s, state) for s in specs) if a is not None]
    adapters.sort(key=lambda a: a.tier)
    return adapters


#: Rungs that exist ONLY for `backfill`. Not in FALLBACKS, and the distinction is
#: load-bearing rather than tidy.
#:
#: ⚠️ `EAVSAdapter.fetch()` raises NotYetPublished, because a post-election
#: survey has nothing to say about a live cycle. By Rule 1 that STOPS the ladder.
#: In FALLBACKS it would sit above the aggregator and end the walk before the
#: aggregator was ever asked -- silently, for every state, every day, which is
#: the same class of bug as the one that left fourteen states unwalked.
HISTORY_FALLBACKS: tuple[str, ...] = (
    "eavs:EAVSAdapter",
)


def history_ladder(state: str) -> list[Adapter]:
    """The ladder `backfill` walks: the live one plus the history-only rungs.

    EAVS is offered to EVERY state rather than an allowlist, because the survey
    itself is the better judge of which states it can serve. `eavs.parse` refuses
    a statewide total unless every jurisdiction answered both of its columns and
    refuses county rows it cannot place inside a county, so a state it cannot
    honestly answer for produces nothing and the walk simply continues to the
    aggregator. Montana, New Jersey and Mississippi all fall through on their
    own evidence; no list has to be kept in step with a file the EAC reissues.

    Tier order does the rest. A state with a real archived file of its own
    answers at tier 1 and EAVS is never fetched.
    """
    state = state.upper()
    adapters = ladder(state)
    adapters += [a for a in (_resolve(s, state) for s in HISTORY_FALLBACKS)
                 if a is not None]
    adapters.sort(key=lambda a: a.tier)
    return adapters


#: States with NO tier-1 scraper that are walked ANYWAY, because the fallback
#: rungs carry them and a statewide count is worth more than a blank.
#:
#: Every one of these was investigated and rejected in `docs/coverage-research.md`
#: for the same reason: the state publishes nothing machine-readable DURING the
#: voting period. That verdict stands and none of them has an adapter. What it
#: missed is that the verdict is about the STATE, not about the page -- the UF
#: Election Lab tracker collected all fourteen through 2024, with real numbers
#: (New Jersey 3.1M early ballots, Massachusetts 1.7M, Indiana 1.6M), and the
#: ladder has always been able to reach them. Nothing walked them, so nothing
#: asked.
#:
#: They cost one aggregator row each and they are honest about what they are:
#: no `county` in their dims, so the front end badges them "Statewide only"
#: without being told, and their rows carry the aggregator tier badge.
#:
#: ⚠️ ALABAMA IS DELIBERATELY ABSENT, and it is the one state here that is a
#: finding rather than an omission: UF does not carry it at all, and Alabama has
#: no early voting to carry -- absentee is excuse-required. Same category as New
#: Hampshire, which is why neither is tracked.
AGGREGATOR_ONLY: tuple[str, ...] = (
    "AR", "DC", "IN", "MA", "MO", "MS",
    "NE", "NJ", "NM", "RI", "UT", "VT", "WV", "WY",
)


@lru_cache(maxsize=1)
def tracked_states() -> tuple[str, ...]:
    """Every state the daily walk visits.

    ⚠️ THIS IS NO LONGER `tuple(TIER1)`. "Tracked" is a decision about whether
    the page should carry a state; "tier 1" is a decision about whether we wrote
    a scraper for it. Conflating them meant a state with no scraper was never
    walked at all -- so the three fallback rungs, which exist precisely for that
    case and are tested for it, could never fire on the states that needed them
    most.
    """
    return tuple(TIER1) + AGGREGATOR_ONLY
