"""Milestone scheduling.

One place, because the date arithmetic appears in four: the approval summary, the offline
planner, the live-model date stamping and the prompt's schedule block. Four copies of a
boundary rule is four chances for a plan to disagree with itself, and the first version
already did — every milestone ended on a Sunday, because "start plus N weeks minus a day"
lands on the weekend for a Monday start.

A delivery schedule that ends on a Sunday reads as arithmetic nobody checked. Work ends on
a Friday.
"""

from __future__ import annotations

from datetime import date, timedelta

#: A milestone's last day is the Friday of its final week. Five working days per week,
#: with the weekend excluded rather than counted and then apologised for.
FRIDAY = 4


def milestone_windows(
    start: date, durations: list[int]
) -> list[tuple[date, date]]:
    """Back-to-back milestone windows from ``start``, each ending on a Friday.

    ``durations`` are in weeks. Milestone *n* begins the working day after milestone
    *n-1* ends, so the schedule has no gaps and no overlaps.
    """
    windows: list[tuple[date, date]] = []
    cursor = start
    for weeks in durations:
        weeks = max(1, int(weeks or 1))
        begins = cursor
        ends = _friday_of(cursor + timedelta(weeks=weeks) - timedelta(days=1))
        windows.append((begins, ends))
        cursor = ends + timedelta(days=3)  # the Monday after
    return windows


def project_window(start: date, total_weeks: int) -> tuple[date, date]:
    """The whole engagement: ``start`` to the Friday of its final week."""
    weeks = max(1, int(total_weeks or 1))
    return start, _friday_of(start + timedelta(weeks=weeks) - timedelta(days=1))


def _friday_of(day: date) -> date:
    """The last working day of the week containing ``day``."""
    if day.weekday() == FRIDAY:
        return day
    if day.weekday() > FRIDAY:  # Saturday or Sunday: fall back to this week's Friday
        return day - timedelta(days=day.weekday() - FRIDAY)
    return day  # a weekday inside the final week is already a working day
