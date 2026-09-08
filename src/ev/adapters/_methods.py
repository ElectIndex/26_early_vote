"""Carrying party-by-method rows on a FetchResult.

`adapters.base.FetchResult` has fields for the state, county and demographic
tables and predates this one, exactly as it predates `TownDay`. Adding a fifth
field there would touch the file every adapter in the tree imports, so method
rows ride as an ATTRIBUTE and these three functions are the only way to put them
on or take them off -- the same mechanism, and the same three names, that
`_towns` uses for municipalities.

The cost is the same too, and `_towns` already paid it once: `FetchResult.stamp()`
does not know about an attribute, so `ladder.run_state` and `cli.cmd_backfill`
call `stamp()` here right beside `_towns.stamp()`. An adapter that forgot would
walk its whole ladder, fetch real data, and then die at WRITE time on
`MethodDay written without provenance` -- loud, but loud in a cron log at two in
the morning rather than in a test. `schema._prov` remains the backstop.

See `schema.MethodDay` for what these rows are and why they are their own table.
"""

from __future__ import annotations

ROWS_ATTR = "method_rows"


def attach(result, rows):
    """Put MethodDay rows on a FetchResult and return it."""
    setattr(result, ROWS_ATTR, list(rows))
    return result


def rows_of(result) -> list:
    """The MethodDay rows on a FetchResult, or [] -- most adapters have none."""
    return list(getattr(result, ROWS_ATTR, None) or ())


def extend(result, rows):
    """Append MethodDay rows to whatever a FetchResult already carries.

    `FetchResult.extend` merges its three FIELDS and cannot see an attribute, so
    an adapter that builds one result per archived day -- fl.py's `fetch_history`
    is the case -- would otherwise keep only the last day's method rows.
    """
    return attach(result, rows_of(result) + list(rows))


def stamp(result, provenance):
    """Apply provenance to every method row that did not set its own."""
    for row in rows_of(result):
        if row.provenance is None:
            row.provenance = provenance
    return result
