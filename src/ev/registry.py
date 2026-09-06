"""Per-state adapter ladders, resolved lazily.

Ladders are declared as (module, class) strings rather than imported classes so
that a state whose adapter has not been written yet -- or whose module fails to
import -- degrades to its lower tiers instead of breaking the whole run. During
the build-out that means the pipeline is runnable from day one with zero state
scrapers; during the season it means one bad deploy cannot take the job down.

TIER 1 is our own scraper against the state's own file.
TIER 2 is the aggregator fallback.
TIER 3 is the hand-entered statewide total in data/manual/.

Every state gets tiers 2 and 3 appended automatically, so a ladder entry here
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
    "GA": "ga:GAScraper",
    "FL": "fl:FLScraper",
    "NV": "nv:NVScraper",
    "AZ": "az:AZScraper",
    # County + party + method.
    "CO": "co:COScraper",
    "IA": "ia:IAScraper",
    "ME": "me:MEScraper",
    "PA": "pa:PAScraper",
    # County + party + sex, in-person early voting only.
    "MD": "md:MDScraper",
    # County + party (DEM/REP only) + method.
    "KY": "ky:KYScraper",
    # County + method (no party registration in these states).
    "IL": "il:ILScraper",
    "SC": "sc:SCScraper",
    "MI": "mi:MIScraper",
    "VA": "va:VAScraper",
    "OH": "oh:OHScraper",
    "WI": "wi:WIScraper",
    "TX": "tx:TXScraper",
    "NH": "nh:NHScraper",
    "TN": "tn:TNScraper",
    # Statewide only (no county breakdown published), but with party + method.
    "SD": "sd:SDScraper",
}

FALLBACKS: tuple[str, ...] = (
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


@lru_cache(maxsize=1)
def tracked_states() -> tuple[str, ...]:
    """States with a tier-1 scraper declared, in ladder-build order."""
    return tuple(TIER1)
