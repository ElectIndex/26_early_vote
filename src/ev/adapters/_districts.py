"""Carrying congressional-district rows on a FetchResult.

The same seam `_towns` and `_methods` use, for the same reason: `FetchResult`
has three fields and predates every later table, and adding a fourth field
would touch the file every adapter imports. District rows ride as an ATTRIBUTE
and these functions are the only way to put them on or take them off.

`ladder.run_state` and `cli.cmd_backfill` call `stamp()` here beside the town
and method stamps, so an adapter that emits district rows and forgets its
provenance still publishes rather than dying at write time.

Only three adapters attach anything: nc.py, va.py and md.py, whose files carry
a district column. Every other state's district rows are DERIVED from its
county rows by `ev.districts.rebuild` after the county file is written, and
never pass through here at all. See `schema.DistrictDay`.
"""

from __future__ import annotations

ROWS_ATTR = "district_rows"


def attach(result, rows):
    """Put DistrictDay rows on a FetchResult and return it."""
    setattr(result, ROWS_ATTR, list(rows))
    return result


def rows_of(result) -> list:
    """The DistrictDay rows on a FetchResult, or [] -- most adapters have none."""
    return list(getattr(result, ROWS_ATTR, None) or ())


def extend(result, rows):
    """Append DistrictDay rows to whatever a FetchResult already carries.

    `FetchResult.extend` merges its three FIELDS and cannot see an attribute,
    so an adapter that builds one result per archived day and folds them
    together needs this to keep its district rows.
    """
    setattr(result, ROWS_ATTR, rows_of(result) + list(rows))
    return result


def stamp(result, provenance):
    """Apply provenance to every district row that did not set its own."""
    for row in rows_of(result):
        if row.provenance is None:
            row.provenance = provenance
    return result
