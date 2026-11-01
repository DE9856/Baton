"""Quote grounding: code, not the AI, decides whether a quote really exists in its source record.

Accepted: an exact match after normalisation, or a near-verbatim match whose numbers and negations are identical.
A true quote cited with the wrong record ID is re-anchored, but only within the chunk the model was shown.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from rapidfuzz import fuzz

NEAR_THRESHOLD = 93.0
MIN_PLAIN_QUOTE = 15

_REMOVE = {ord(c): None for c in "​‌‍‎‏‪‬﻿"}
_REPLACE = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-",
                          "…": "...", " ": " ", " ": " "})
_NUMBER = re.compile(r"\d+(?:[.,:/-]\d+)*")
_NEGATION = re.compile(
    r"\b(?:never|not|no|none|nobody|nothing|neither|nor|without|cannot|unable|isn't|wasn't|aren't|weren't|"
    r"don't|doesn't|didn't|won't|wouldn't|can't|couldn't|shouldn't|hasn't|haven't|hadn't)\b",
    re.I,
)


def normalise(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).translate(_REMOVE).translate(_REPLACE)
    return re.sub(r"\s+", " ", text).strip().lower()


def carries_value(quote: str) -> bool:
    return bool(_NUMBER.search(quote))


def _facts_identical(quote: str, window: str) -> bool:
    return sorted(_NUMBER.findall(quote)) == sorted(_NUMBER.findall(window)) and sorted(
        x.lower() for x in _NEGATION.findall(quote)
    ) == sorted(x.lower() for x in _NEGATION.findall(window))


@dataclass
class GroundResult:
    match: str  # exact | near | exact_reanchored | near_reanchored | rejected
    unit_uid: str | None = None
    reason: str = ""

    @property
    def grounded(self) -> bool:
        return self.match != "rejected"


def _match_in(quote_n: str, text: str) -> str | None:
    text_n = normalise(text)
    if quote_n in text_n:
        return "exact"
    if len(quote_n) < MIN_PLAIN_QUOTE:
        return None  # a short quote has too little text for a fuzzy score to mean anything
    alignment = fuzz.partial_ratio_alignment(quote_n, text_n)
    if alignment is None or alignment.score < NEAR_THRESHOLD:
        return None
    window = text_n[alignment.dest_start:alignment.dest_end]
    return "near" if _facts_identical(quote_n, window) else None


def ground_quote(quote: str, cited_uid: str, chunk: dict[str, str]) -> GroundResult:
    """`chunk` maps record ID to record text, for the records the model was shown."""
    if not (quote or "").strip():
        return GroundResult("rejected", reason="no quote was given")
    quote_n = normalise(quote)
    if len(quote_n) < MIN_PLAIN_QUOTE and not carries_value(quote_n):
        # "members: Arjun Mehta" passes on length; a bare "ok" carries nothing checkable.
        return GroundResult("rejected", reason="the quote is too short to prove anything")
    if cited_uid in chunk:
        m = _match_in(quote_n, chunk[cited_uid])
        if m:
            return GroundResult(m, cited_uid)
    exact_elsewhere = [u for u, t in chunk.items() if u != cited_uid and quote_n in normalise(t)]
    if exact_elsewhere:
        return GroundResult("exact_reanchored", exact_elsewhere[0], f"model cited {cited_uid or 'no record'}")
    for u, t in chunk.items():
        if u != cited_uid and _match_in(quote_n, t) == "near":
            return GroundResult("near_reanchored", u, f"model cited {cited_uid or 'no record'}")
    return GroundResult("rejected", reason="the quote was not found in the records the model was shown")
