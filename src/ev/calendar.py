"""Election dates and the days-to-election axis.

Every comparison in this project is "2026 day N before the election vs 2022/2024
day N before *their* election" -- calendar dates are useless across cycles because
Election Day moves. `days_to_election` is therefore the real x-axis everywhere,
and it is computed here and nowhere else.
"""

from __future__ import annotations

from datetime import date

# General election dates. Cycle keys are ints so they can come straight off a
# CSV row without conversion at the call site.
#
# 2020 is here because ONE state can actually reach back that far: Pennsylvania's
# mail-ballot file is one row per application carrying its own return date, so a
# single query rebuilds a whole cycle's curve and the 2020 general
# (data.pa.gov `mcba-yywm`, 3,079,710 applications) is still posted. Every other
# adapter guards its own earliest cycle -- fl.py's FIRST_CYCLE is the pattern --
# and simply raises NotYetPublished for 2020, which is the normal, non-error
# answer. Nothing here asserts a cycle is COMPLETE across states; that is what
# each model's own FIT_CYCLES / RESULT_CYCLES / REFERENCE_CYCLE tuple is for, and
# they are deliberately left at (2022, 2024).
ELECTION_DATES: dict[int, date] = {
    2020: date(2020, 11, 3),
    2022: date(2022, 11, 8),
    2024: date(2024, 11, 5),
    2026: date(2026, 11, 3),
}

CYCLES: tuple[int, ...] = (2020, 2022, 2024, 2026)

#: The cycle this repo is actively tracking. Backfill targets the other two.
CURRENT_CYCLE = 2026


class UnknownCycle(ValueError):
    """Raised for a cycle we have no election date for."""


def election_date(cycle: int) -> date:
    try:
        return ELECTION_DATES[int(cycle)]
    except KeyError as exc:
        raise UnknownCycle(f"no election date for cycle {cycle!r}") from exc


def days_to_election(cycle: int, day: date) -> int:
    """Whole days from `day` to that cycle's Election Day.

    Positive before the election, 0 on Election Day, negative after. Both operands
    are `date`, never `datetime`, so daylight-saving transitions -- which land in
    the middle of every early-vote window -- cannot shift the axis by a day.
    """
    return (election_date(cycle) - day).days


def cycle_for_date(day: date) -> int:
    """The cycle a given day belongs to: the next election on or after it."""
    for cycle in CYCLES:
        if day <= ELECTION_DATES[cycle]:
            return cycle
    raise UnknownCycle(f"{day.isoformat()} is after every known election")
