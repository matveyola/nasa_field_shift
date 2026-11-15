"""Pure calendar arithmetic; monthly reference periods are not admissible windows."""

from bisect import bisect_right
from calendar import monthrange
from datetime import date
from math import isfinite


def month_period(year: int, month: int) -> tuple[date, date]:
    return date(year, month, 1), date(year, month, monthrange(year, month)[1])


def rolling_end(planning_date: date, year_count: int) -> date:
    if not 1 <= year_count <= 6 or planning_date.year + year_count > 2100:
        raise ValueError("Choose one to six years ending no later than 2100")
    year = planning_date.year + year_count
    return date(
        year, planning_date.month, min(planning_date.day, monthrange(year, planning_date.month)[1])
    )


def maturity_year(planting_year: int, planting_month: int, maturity_month: int) -> int | None:
    """Following annual occurrence; equal months cannot identify duration/year."""
    if not 1 <= planting_month <= 12 or not 1 <= maturity_month <= 12:
        return None
    if planting_month == maturity_month:
        return None
    return planting_year + (maturity_month < planting_month)


def containing_cell(coordinates: list[float], value: float) -> int | None:
    """Native midpoint cell edges, half-open intervals; never jump to valid land."""
    if not isfinite(value) or len(coordinates) < 2:
        return None
    descending = coordinates[0] > coordinates[-1]
    centers = [-x for x in coordinates] if descending else coordinates
    point = -value if descending else value
    if not all(isfinite(x) for x in centers) or any(a >= b for a, b in zip(centers, centers[1:])):
        raise ValueError("Strictly monotone finite native coordinates required")
    lower = centers[0] - (centers[1] - centers[0]) / 2
    upper = centers[-1] + (centers[-1] - centers[-2]) / 2
    if point < lower or point >= upper:
        return None
    edges = [(a + b) / 2 for a, b in zip(centers, centers[1:])]
    return bisect_right(edges, point)
