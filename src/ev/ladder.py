"""The source ladder: try each state's adapters in order, best tier first.

Two rules, and they are the whole design:

  1. NotYetPublished STOPS the walk. A state that has not opened early voting has
     no data at ANY tier, so falling through would only let a weaker source
     manufacture a zero. "Nothing yet" is recorded as a status, not as a row.

  2. SourceError / SchemaDrift FALL THROUGH to the next tier. We wanted data and
     could not get it here, so a worse source beats no source -- and every row
     carries the tier it came from, so the page can say which.

The complementary rule (a worse tier never overwrites a better one already on
disk) lives in publish.py, because it is about the merge, not the walk.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date

from .adapters.base import Adapter, AdapterError, FetchResult, NotYetPublished, SourceError

log = logging.getLogger(__name__)

STATUS_OK = "ok"
STATUS_PENDING = "pending"      # NotYetPublished -- expected, not a failure
STATUS_FAILED = "failed"        # every tier errored
STATUS_NO_SOURCE = "no_source"  # no adapters registered for this state


@dataclass
class StateOutcome:
    """What happened for one state on one run. Serialised into ev_status.json."""

    state: str
    status: str
    tier: int | None = None
    source_name: str | None = None
    rows: int = 0
    #: Every tier we tried and why it did not answer, best-first.
    attempts: list[dict] = field(default_factory=list)
    message: str = ""

    @property
    def ok(self) -> bool:
        return self.status == STATUS_OK


def run_state(
    state: str,
    adapters: list[Adapter],
    cycle: int,
    as_of: date,
) -> tuple[FetchResult, StateOutcome]:
    """Walk one state's ladder and return the first tier that answered."""
    outcome = StateOutcome(state=state, status=STATUS_NO_SOURCE)
    if not adapters:
        outcome.message = f"no adapters registered for {state}"
        return FetchResult(), outcome

    for adapter in adapters:
        label = adapter.name or type(adapter).__name__
        try:
            result = adapter.fetch(cycle, as_of)
        except NotYetPublished as exc:
            # RULE 1: stop. There is no better source for data that does not exist.
            outcome.status = STATUS_PENDING
            outcome.message = str(exc)
            outcome.attempts.append(
                {"tier": adapter.tier, "name": label, "result": "not_yet_published",
                 "detail": str(exc)}
            )
            log.info("%s: not yet published (%s)", state, label)
            return FetchResult(), outcome
        except SourceError as exc:
            # RULE 2: fall through to the next tier.
            outcome.attempts.append(
                {"tier": adapter.tier, "name": label,
                 "result": type(exc).__name__, "detail": str(exc)}
            )
            log.warning("%s: %s failed (%s: %s)", state, label, type(exc).__name__, exc)
            continue
        except AdapterError as exc:  # defensive: an adapter raised the base class
            outcome.attempts.append(
                {"tier": adapter.tier, "name": label,
                 "result": type(exc).__name__, "detail": str(exc)}
            )
            log.warning("%s: %s raised %s", state, label, type(exc).__name__)
            continue
        except Exception as exc:  # noqa: BLE001 -- one bad adapter must not kill the run
            # A crash in one state's parser cannot be allowed to abort the other
            # forty-nine on a day when ballots are being counted.
            outcome.attempts.append(
                {"tier": adapter.tier, "name": label,
                 "result": "crash", "detail": f"{type(exc).__name__}: {exc}"}
            )
            log.exception("%s: %s crashed", state, label)
            continue

        if not result:
            outcome.attempts.append(
                {"tier": adapter.tier, "name": label, "result": "empty",
                 "detail": "adapter returned no rows"}
            )
            log.warning("%s: %s returned no rows", state, label)
            continue

        result.stamp(adapter.provenance())
        outcome.status = STATUS_OK
        outcome.tier = adapter.tier
        outcome.source_name = label
        outcome.rows = (
            len(result.state_rows) + len(result.county_rows) + len(result.demo_rows)
        )
        outcome.attempts.append({"tier": adapter.tier, "name": label, "result": "ok"})
        log.info("%s: %s answered with %d rows", state, label, outcome.rows)
        return result, outcome

    outcome.status = STATUS_FAILED
    outcome.message = f"all {len(adapters)} tier(s) failed for {state}"
    return FetchResult(), outcome
