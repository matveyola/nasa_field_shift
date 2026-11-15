"""Recognize date spans without granting their digits numeric-claim authority.

Only endpoints of current retrieved temporal evidence can support a data date.
Names, farmer prose, old chat text and retrieval timestamps are not data periods.
Slash dates are deliberately rejected: their day/month order is ambiguous.
"""

import datetime as dt
import re
from dataclasses import dataclass

_MONTHS = (
    ("Jan", "January", "січня"),
    ("Feb", "February", "лютого"),
    ("Mar", "March", "березня"),
    ("Apr", "April", "квітня"),
    ("May", "May", "травня"),
    ("Jun", "June", "червня"),
    ("Jul", "July", "липня"),
    ("Aug", "August", "серпня"),
    ("Sep", "September", "вересня"),
    ("Oct", "October", "жовтня"),
    ("Nov", "November", "листопада"),
    ("Dec", "December", "грудня"),
)
_MONTH_NUMBER = {
    name.casefold(): i for i, names in enumerate(_MONTHS, 1) for name in names
}
_MONTH = "(?:" + "|".join(sorted(_MONTH_NUMBER, key=len, reverse=True)) + ")"
DATE = re.compile(
    r"(?<![\w./])(?:"
    r"\d{4}-\d{2}-\d{2}"
    r"|\d{1,2}[./]\d{1,2}[./]\d{4}"
    r"|\d{1,2}\.\d{2}(?=\s*[–—-]\s*\d{1,2}\.\d{2}\.\d{4})"
    rf"|\d{{1,2}}\s+{_MONTH}(?:\s+\d{{4}}(?:\s+року)?)?"
    rf"|{_MONTH}\s+\d{{1,2}}(?:,?\s+\d{{4}})?"
    r")(?![\w/])",
    re.IGNORECASE,
)
_RANGE = re.compile(r"\s*(?:[–—-]|to|до)\s*", re.IGNORECASE)


@dataclass(frozen=True)
class DateSpan:
    start: int
    end: int
    year: int | None
    month: int
    day: int

    def resolve(
        self, endpoints: set[dt.date], year: int | None = None
    ) -> dt.date | None:
        effective_year = self.year if self.year is not None else year
        if effective_year is None:
            return None  # A bare month/day does not communicate an exact year.
        matches = {
            d
            for d in endpoints
            if d.month == self.month and d.day == self.day and d.year == effective_year
        }
        return next(iter(matches)) if len(matches) == 1 else None


def date_spans(text: str) -> list[DateSpan]:
    spans = []
    for m in DATE.finditer(text):
        value = m.group(0).casefold().removesuffix(" року")
        year = None
        if "/" in value:
            # An invalid month makes this span fail even if its digits occur in
            # trusted evidence. Never infer which locale a slash date intended.
            month, day = 0, 0
        elif "-" in value:
            year, month, day = map(int, value.split("-"))
        elif "." in value:
            parts = [int(p) for p in value.split(".")]
            day, month = parts[:2]
            year = parts[2] if len(parts) == 3 else None
        else:
            parts = value.replace(",", "").split()
            if parts[0].isdigit():
                day, month = int(parts[0]), _MONTH_NUMBER[parts[1]]
            else:
                month, day = _MONTH_NUMBER[parts[0]], int(parts[1])
            if len(parts) == 3:
                year = int(parts[2])
        spans.append(DateSpan(m.start(), m.end(), year, month, day))
    return spans


def range_year(text: str, spans: list[DateSpan], index: int) -> int | None:
    """In '1 Nov – 14 Nov 2026', the final year applies to both endpoints.

    A cross-year interval must spell both years rather than borrowing a matching
    day/month from a different year in the source.
    """
    span = spans[index]
    if span.year is None and index + 1 < len(spans):
        following = spans[index + 1]
        if _RANGE.fullmatch(text[span.end : following.start]):
            return following.year
    return None


def joins_range(text: str, left: DateSpan, right: DateSpan) -> bool:
    return bool(_RANGE.fullmatch(text[left.end : right.start]))
