"""Deterministic date reading. Relative dates are anchored to the message's own timestamp, never left to a model."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

_MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}
_MON = r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
_ISO = re.compile(r"\b(20\d{2})-(\d{1,2})-(\d{1,2})\b")
_DM = re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?{_MON}\b\.?(?:,?\s+(20\d{{2}}))?", re.I)
_MD = re.compile(rf"\b{_MON}\b\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?\b(?:,?\s+(20\d{{2}}))?", re.I)


@dataclass(frozen=True)
class FoundDate:
    day: date           # resolved using the anchor when the year is not written
    explicit_year: bool
    raw: str


def _closest_year(month: int, dom: int, anchor: date | None) -> date | None:
    """A date without a year means the occurrence closest to when it was written ("10 Jan" in Oct 2026 = 2027)."""
    base = anchor or date.today()
    best: date | None = None
    for y in (base.year - 1, base.year, base.year + 1):
        try:
            d = date(y, month, dom)
        except ValueError:
            continue
        if best is None or abs((d - base).days) < abs((best - base).days):
            best = d
    return best


def find_dates(text: str, anchor: date | None = None) -> list[FoundDate]:
    found: list[FoundDate] = []
    taken: list[tuple[int, int]] = []

    def free(span: tuple[int, int]) -> bool:
        return all(span[1] <= a or span[0] >= b for a, b in taken)

    for m in _ISO.finditer(text):
        try:
            found.append(FoundDate(date(int(m[1]), int(m[2]), int(m[3])), True, m[0]))
            taken.append(m.span())
        except ValueError:
            continue
    for rx, d_group, m_group in ((_DM, 1, 2), (_MD, 2, 1)):
        for m in rx.finditer(text):
            if not free(m.span()):
                continue
            if rx is _MD and m[m_group] == "may":
                continue  # lowercase "may 5" is a verb followed by a number, not a date
            month = _MONTHS[m[m_group][:3].lower()]
            dom = int(m[d_group])
            year = m[3]
            if year:
                try:
                    found.append(FoundDate(date(int(year), month, dom), True, m[0].rstrip(".")))
                except ValueError:
                    continue
            else:
                d = _closest_year(month, dom, anchor)
                if d is None:
                    continue
                found.append(FoundDate(d, False, m[0].rstrip(".")))
            taken.append(m.span())
    return found


def date_spans(text: str) -> list[tuple[int, int]]:
    spans = [m.span() for m in _ISO.finditer(text)]
    for rx in (_DM, _MD):
        spans += [m.span() for m in rx.finditer(text)]
    return spans
