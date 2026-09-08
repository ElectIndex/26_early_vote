"""The source ladder: try each state's adapters in order, best tier first.

Two rules, and they are the whole design:

  1. NotYetPublished STOPS the walk. A state that has not opened early voting has
     no data at ANY tier, so falling through would only let a weaker source
     manufacture a zero. "Nothing yet" is recorded as a status, not as a row.

     One refinement, and it is about the STATUS only, never the walk: raised by
     the LAST rung it is recorded as STATUS_FAILED rather than STATUS_PENDING.
     Getting that far means every rung above it failed, and the last rung is the
     hand-entered file, which has no opinion about whether a state has started
     voting -- only about whether anybody typed a number. See the note at the
     handler.

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

from .adapters import _methods, _towns
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

    last_rung = len(adapters) - 1
    for index, adapter in enumerate(adapters):
        label = adapter.name or type(adapter).__name__
        try:
            result = adapter.fetch(cycle, as_of)
            # ⚠️ `provenance()` BELONGS INSIDE THE TRY, and did not used to be.
            # It builds a `schema.Provenance`, which REFUSES a tier that is not
            # one of TIER_LABELS -- and `Adapter.tier` defaults to 0. So a new
            # adapter that implements `fetch` correctly and forgets one line
            # (`tier = TIER_SCRAPER`) raised ValueError out of the one function
            # whose entire contract is "one bad adapter must not kill the run",
            # aborting the walk for every state after it and taking the status
            # write with it. Computed here, that is just another crash: the
            # attempt is recorded and the ladder falls through to civicAPI.
            provenance = adapter.provenance()
        except NotYetPublished as exc:
            # RULE 1: stop. There is no better source for data that does not exist.
            outcome.attempts.append(
                {"tier": adapter.tier, "name": label, "result": "not_yet_published",
                 "detail": str(exc)}
            )
            if index and index == last_rung:
                # ⚠️ ...EXCEPT FROM THE FLOOR OF THE LADDER, WHICH KNOWS NOTHING
                # ABOUT THE WORLD.
                #
                # Reaching the last rung at all means every rung above it failed
                # to produce data -- NotYetPublished anywhere higher would have
                # returned already. And the last rung is always `ManualAdapter`,
                # whose "not yet published" means "nobody has typed a number into
                # data/manual/", not "the state has not started voting". Reading
                # that as PENDING made STATUS_FAILED unreachable in production:
                # `ev_status.json`'s failed count was structurally 0, ingest.yml's
                # "Every tier failed for:" line could never print, and
                # `ingest --strict` could never return 1. A state whose scraper,
                # civicAPI and the aggregator all broke in the middle of early
                # voting badged as "early voting has not opened".
                #
                # The WALK is unchanged -- nothing below this rung is consulted,
                # because there is nothing below it -- and no row is published
                # either way. Only the label on the outcome changes, and it
                # changes to the one that is true.
                outcome.status = STATUS_FAILED
                outcome.message = (
                    f"every tier failed for {state}; the floor of the ladder "
                    f"({label}) has nothing either: {exc}"
                )
                log.error("%s: every tier failed; %s has nothing either (%s)",
                          state, label, exc)
            else:
                outcome.status = STATUS_PENDING
                outcome.message = str(exc)
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

        result.stamp(provenance)
        # ...AND THE FOURTH TABLE, which `FetchResult.stamp` cannot reach.
        # Town rows ride on the result as an ATTRIBUTE rather than a field (see
        # the note in _towns.py: adding a field to base.py would touch the file
        # every adapter imports), so until now each town-emitting adapter had to
        # remember `_towns.stamp()` for itself. me.py and ct.py do. The next one
        # to forget would have walked its whole ladder, fetched real data, and
        # then died at WRITE time on `TownDay written without provenance` -- loud,
        # but loud in a cron log at two in the morning rather than in a test.
        # Stamping here costs one line and makes forgetting impossible; the
        # adapters' own calls are now belt-and-braces rather than load-bearing.
        _towns.stamp(result, provenance)
        # ...AND THE FIFTH, for the identical reason. See _methods.py.
        _methods.stamp(result, provenance)
        outcome.status = STATUS_OK
        outcome.tier = adapter.tier
        outcome.source_name = label
        outcome.rows = (
            len(result.state_rows) + len(result.county_rows)
            + len(result.demo_rows) + len(_towns.rows_of(result))
            + len(_methods.rows_of(result))
        )
        outcome.attempts.append({"tier": adapter.tier, "name": label, "result": "ok"})
        log.info("%s: %s answered with %d rows", state, label, outcome.rows)
        return result, outcome

    outcome.status = STATUS_FAILED
    outcome.message = f"all {len(adapters)} tier(s) failed for {state}"
    return FetchResult(), outcome
