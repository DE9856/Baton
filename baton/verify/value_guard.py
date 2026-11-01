"""Value guard: every date, number and capitalised name in a claim must appear in its evidence.

NLI models miss swapped names and numbers, so this deterministic check runs beside them.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from .dates import date_spans, find_dates
from .grounding import normalise

_NUMBER = re.compile(r"(?<![\w.])#?\d+(?:[.,]\d+)*k?\b", re.I)
_WORD = re.compile(r"[A-Z][\w'+.-]*[\w+]|[A-Z]\b")
# Capitalised words that are not names: sentence openers and ordinary terms.
_COMMON = {
    "the", "a", "an", "this", "that", "these", "those", "it", "its", "he", "she", "they", "we", "you", "i", "our",
    "their", "his", "her", "my", "your", "and", "or", "but", "if", "for", "to", "of", "in", "on", "at", "by", "with",
    "from", "after", "before", "because", "as", "so", "not", "no", "is", "are", "was", "were", "will", "should", "must",
    "can", "may", "need", "needs", "has", "have", "had", "who", "what", "when", "why", "how", "there", "here", "keep",
    "move", "moves", "new", "note", "decision", "contact", "target", "status", "owner", "deadline", "risk", "card",
    "issue", "pr", "comment", "let", "let's", "use", "using", "still", "also", "both", "all", "some", "each", "every",
    "next", "first", "second", "third", "last", "due", "done", "todo", "up", "out", "off", "said", "says", "mentioned",
    "stated", "reported", "confirmed", "asked", "agreed", "approved", "approving", "blocked", "closed", "open", "fixed",
    "fixes", "merged", "summary", "reminder", "update", "workaround", "attendees", "condition",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    "january", "february", "march", "april", "june", "july", "august", "september", "october", "november", "december",
    "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec",
}


@dataclass
class GuardResult:
    ok: bool
    missing: list[str] = field(default_factory=list)


def _strip_dates(text: str) -> str:
    chars = list(text)
    for a, b in date_spans(text):
        for i in range(a, b):
            chars[i] = " "
    return "".join(chars)


def _numbers(text: str) -> set[str]:
    return {m.group(0).lstrip("#").replace(",", "").lower() for m in _NUMBER.finditer(_strip_dates(text))}


def _names(statement: str, roster_tokens: set[str]) -> set[str]:
    names: set[str] = set()
    for m in _WORD.finditer(statement):
        word = m.group(0).rstrip(".").removesuffix("'s")
        low = word.lower()
        if low in _COMMON or len(word) < 2:
            continue
        # Sentence-initial words count too ("Sakshi asked..."). A faithful opener normally appears in the evidence
        # anyway, so an unusual opener costs a trip to Needs review, which is the safe direction.
        names.add(word)
    # A speaker prefix written in lower case ("sara: comment on...") is still a person.
    speaker = re.match(r"^([a-z][\w-]+):\s", statement)
    if speaker:
        names.add(speaker.group(1))
    return names


def check_values(statement: str, evidence: str, anchor: date | None = None,
                 roster: list[list[str]] | None = None) -> GuardResult:
    """`roster` is one alias list per person: "Kabir" passes against "kabirs" or "Kabir Shah" if they are aliases."""
    roster = roster or []
    roster_tokens: set[str] = set()
    for aliases in roster:
        for a in aliases:
            roster_tokens |= {t.lower() for t in re.split(r"[\s_-]+", a) if t}
    missing: list[str] = []
    ev_n = normalise(evidence)

    # Dates: compare resolved calendar days. A year the model wrote must match the year the anchor implies.
    ev_dates = find_dates(evidence, anchor)
    for d in find_dates(statement, anchor):
        same_day = [e for e in ev_dates if (e.day.month, e.day.day) == (d.day.month, d.day.day)]
        if not same_day or (d.explicit_year and not any(e.day == d.day for e in same_day)):
            missing.append(d.raw)

    ev_numbers = _numbers(evidence)
    for n in _numbers(statement):
        if n not in ev_numbers:
            missing.append(n)

    for name in _names(statement, roster_tokens):
        low = name.lower()
        if low in ev_n:
            continue
        people = [a for a in roster if any(low in re.split(r"[\s_-]+", x.lower()) or low == x.lower() for x in a)]
        if any(normalise(alias) in ev_n for person in people for alias in person):
            continue
        missing.append(name)
    return GuardResult(not missing, missing)
