"""The grid: a row for every year of the owner's life, and 52 squares in each.

A row runs from one birthday to the next, so a column is the same weeks of the year in every
row and the months can be written above it. The 52nd square takes the one or two days a year
has over 52 weeks.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from ...foundation.charts import MONTHS, day_month

LIFE_WEEKS = 52

# A square: the year of age, and the week since that year's birthday.
type Cell = tuple[int, int]


def birthday(born: date, year: int) -> date:
    """The birthday in `year`; 29 February falls on the 28th in a year without one."""
    try:
        return born.replace(year=year)
    except ValueError:
        return born.replace(year=year, day=28)


@dataclass(frozen=True, slots=True)
class LifeGrid:
    born: date
    years: int

    def cell(self, day: date) -> Cell:
        age = day.year - self.born.year - (day < birthday(self.born, day.year))
        week = (day - birthday(self.born, self.born.year + age)).days // 7
        return age, min(week, LIFE_WEEKS - 1)

    def start(self, cell: Cell) -> date:
        age, week = cell
        return birthday(self.born, self.born.year + age) + timedelta(weeks=week)

    def rows(self, today: date) -> int:
        """As many as the settings say, or one more than the owner's age when that is more."""
        return max(self.years, self.cell(today)[0] + 1)


def month_starts(born: date) -> list[tuple[float, str]]:
    """Where each month begins along a row, in weeks from the birthday, by its name. The birth
    month begins again just before the next birthday, unless the birthday is its first day."""
    start = birthday(born, born.year)
    starts = []
    for month in range(1, 13):
        first = date(born.year, month, 1)
        if first < start:
            first = date(born.year + 1, month, 1)
        starts.append(((first - start).days / 7, MONTHS[month - 1]))
    return starts


def ordinal(cell: Cell) -> int:
    return cell[0] * LIFE_WEEKS + cell[1]


def cells(first: Cell, last: Cell) -> list[Cell]:
    """Every square from `first` to `last`, both in, in the order they were lived."""
    return [divmod(index, LIFE_WEEKS) for index in range(ordinal(first), ordinal(last) + 1)]


def day_label(day: date, today: date) -> str:
    """1 Jun, with the year only when it is not this one."""
    return day_month(day) if day.year == today.year else f"{day_month(day)} {day.year}"


def heading(grid: LifeGrid, today: date, since: date) -> str:
    """Where the owner is in the grid, and since when it holds anything."""
    age, week = grid.cell(today)
    lived = (today - grid.born).days // 7
    return (
        f"Age {age}, week {week + 1} of the year · {lived:,} weeks lived · records since "
        f"{day_month(since)} {since.year}"
    )
