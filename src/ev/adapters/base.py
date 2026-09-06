"""The adapter contract.

Every state source -- our own scraper, the aggregator fallback, the hand-entered
manual file -- implements `Adapter`. The runner in ladder.py walks a state's
ordered list of adapters and applies two rules that live entirely in the
exception types below, so getting these right in an adapter is the whole job:

  NotYetPublished  ->  STOP. Do not try the next tier.
      The state simply has not opened early voting, or has not posted today's
      file yet. There is no better source for data that does not exist, and
      falling through would let a weaker source invent a zero. This is a normal,
      expected, non-error outcome for most states on most days before October.

  SourceError      ->  fall through to the next tier.
      We expected data and could not get it: network failure, 500, truncated
      download, unparseable content.

  SchemaDrift      ->  fall through (it is a SourceError).
      The file is there and downloaded fine, but its columns are not what we
      parsed last time. Raise this rather than coercing: a silently mis-mapped
      column publishes confident wrong numbers, which is far worse than a gap.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from datetime import date

from ..schema import CountyDay, DemoDay, Provenance, StateDay


class AdapterError(Exception):
    """Base for every adapter-raised condition."""


class NotYetPublished(AdapterError):
    """No data exists yet. The runner stops here and does NOT try a worse tier."""


class SourceError(AdapterError):
    """Data was expected but could not be retrieved. The runner falls through."""


class SchemaDrift(SourceError):
    """The source's columns changed. Fail loudly rather than guess a mapping."""


@dataclass
class FetchResult:
    """Everything one adapter produced for one run.

    An adapter fills only the tables its source actually supports; a statewide-only
    source returns county_rows == [] and that is not an error.
    """

    state_rows: list[StateDay] = field(default_factory=list)
    county_rows: list[CountyDay] = field(default_factory=list)
    demo_rows: list[DemoDay] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.state_rows or self.county_rows or self.demo_rows)

    def extend(self, other: "FetchResult") -> "FetchResult":
        self.state_rows.extend(other.state_rows)
        self.county_rows.extend(other.county_rows)
        self.demo_rows.extend(other.demo_rows)
        return self

    def stamp(self, provenance: Provenance) -> "FetchResult":
        """Apply provenance to every row that did not set its own.

        Adapters get this for free -- they build bare rows and the runner stamps
        them -- so no adapter can forget, and schema.py's write path can insist
        that provenance is never None.
        """
        for bucket in (self.state_rows, self.county_rows, self.demo_rows):
            for row in bucket:
                if row.provenance is None:
                    row.provenance = provenance
        return self


class Adapter(abc.ABC):
    """One source for one state.

    Subclasses set `state`, `name` and `tier`, and implement `fetch`. Implement
    `fetch_history` only if the source exposes archived daily files for a past
    cycle; the default correctly reports that it does not.
    """

    #: Two-letter USPS code, uppercase.
    state: str = ""
    #: Stable identifier written into every row's `source_name` (e.g. "nc-sbe").
    name: str = ""
    #: One of schema.TIER_*.
    tier: int = 0

    def __init__(self, state: str | None = None) -> None:
        if state:
            self.state = state.upper()

    @abc.abstractmethod
    def fetch(self, cycle: int, as_of: date) -> FetchResult:
        """Return the state's cumulative position as published on `as_of`.

        Raise NotYetPublished if the state has no data yet; raise SourceError or
        SchemaDrift if data was expected but could not be parsed. Return counts as
        None -- never 0 -- for fields the source does not report.
        """

    def fetch_history(self, cycle: int) -> FetchResult:
        """Archived daily rows for a past cycle. Default: no archive available."""
        raise NotYetPublished(
            f"{self.name or type(self).__name__} has no archived daily file for {cycle}"
        )

    def provenance(self) -> Provenance:
        return Provenance(tier=self.tier, name=self.name)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<{type(self).__name__} {self.state} tier={self.tier} name={self.name!r}>"
