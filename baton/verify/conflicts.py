"""Conflict detection: MiniLM picks candidates, NLI proposes, and a deterministic facet check confirms.

NLI alone raises many false alarms, because any two statements about different things can score as contradictory.
The facet check can only remove cards, never add them: a card stays only if the two claims give different dates,
opposite done/not-done status, or different owners for the same subject. Conflicts are never auto-resolved.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime

from rapidfuzz import fuzz

from .dates import find_dates

CANDIDATE_MIN_COSINE = 0.35
CANDIDATE_CAP = 400
CONTRADICTION_THRESHOLD = 0.5

_STOP = {"the", "a", "an", "is", "are", "was", "were", "be", "been", "to", "of", "for", "on", "in", "at", "by", "and",
         "or", "with", "from", "it", "its", "this", "that", "will", "should", "must", "need", "needs", "has", "have",
         "had", "after", "before", "only", "so", "as", "we", "our", "they", "their", "not", "no", "never", "new", "due",
         "date", "deadline", "target", "status", "owner", "card", "list", "issue", "pr", "done", "completed", "progress",
         "todo", "still", "actually", "being", "then", "now", "up", "set", "get", "does", "do", "did", "than", "about",
         "responsible", "working", "college", "work", "there", "right", "fix", "fixed", "bug"}
_DONE = re.compile(r"\b(done|completed?|finished|configured|enabled|set up|are set|fixed|resolved|closed|merged)\b", re.I)
_NOT_DONE = re.compile(
    r"\b(never|not (?:yet |actually |been )?(?:enabled|done|finished|fixed|configured|completed|include[ds]?)|"
    r"(?:doesn't|does not|don't|do not) include|no \w+ (?:right now|exist)|in[ _]progress|still (?:open|working|being)|"
    r"to[ _]?do|started)\b",
    re.I,
)


@dataclass
class ConflictClaim:
    id: int
    type: str
    subject: str
    statement: str
    value: str
    as_of: datetime | None
    quote: str = ""
    context: str = ""  # record title or other text that names what the claim is about
    uid: str = ""


@dataclass
class Card:
    a: ConflictClaim
    b: ConflictClaim
    nli: float
    facet: str | None = None


@dataclass
class ConflictGroup:
    side_a: list[ConflictClaim] = field(default_factory=list)  # older
    side_b: list[ConflictClaim] = field(default_factory=list)  # newer
    facet: str = ""
    nli: float = 0.0
    subject: str = ""


def _tokens(text: str, ignore: set[str]) -> set[str]:
    words = re.findall(r"[a-z0-9#]+", text.lower())
    out = set()
    for w in words:
        if w in _STOP or w in ignore or len(w) < 2 or w.isdigit():
            continue
        out.add(w[:-1] if w.endswith("s") and len(w) > 3 else w)
    return out


def _about(c: ConflictClaim, ignore: set[str]) -> set[str]:
    return _tokens(f"{c.subject} {c.statement} {c.context}", ignore)


def _days(c: ConflictClaim) -> set[tuple[int, int]]:
    anchor = c.as_of.date() if c.as_of else None
    # Only the statement counts. A date elsewhere in a quote is often an aside ("see the 12 Sep notes").
    found = find_dates(c.statement, anchor)
    return {(d.day.month, d.day.day) for d in found}


def _polarity(c: ConflictClaim) -> str | None:
    text = f"{c.statement} {c.value} {c.quote}"
    if _NOT_DONE.search(text):
        return "not_done"
    if _DONE.search(text):
        return "done"
    return None


def _person(value: str, roster: list[list[str]]) -> int | None:
    v = value.strip().lower()
    for i, aliases in enumerate(roster):
        if any(v == a.lower() or v in re.split(r"[\s_-]+", a.lower()) for a in aliases):
            return i
    return None


def facet_check(a: ConflictClaim, b: ConflictClaim, roster: list[list[str]] | None = None) -> str | None:
    roster = roster or []
    ignore: set[str] = set()
    for aliases in roster:
        for alias in aliases:
            ignore |= {t.lower() for t in re.split(r"[\s_-]+", alias)}
    shared = _about(a, ignore) & _about(b, ignore)

    if a.type == "owner" and b.type == "owner" and fuzz.token_set_ratio(a.subject, b.subject) >= 85:
        pa, pb = _person(a.value, roster), _person(b.value, roster)
        if pa is not None and pb is not None and pa != pb:
            return "different owners"
    if len(shared) >= 1:
        da, db = _days(a), _days(b)
        if da and db and not da & db and (_is_schedule(a) and _is_schedule(b)):
            return "different dates"
    if len(shared) >= 2:
        pa, pb = _polarity(a), _polarity(b)
        if pa and pb and pa != pb:
            return "opposite status"
    return None


_SCHEDULE = re.compile(r"\b(due|deadline|target|pushed|moved|postponed|rescheduled|extended|by|until|on)\b", re.I)


def _is_schedule(c: ConflictClaim) -> bool:
    return c.type in ("deadline", "status") or bool(_SCHEDULE.search(c.statement))


def _text(c: ConflictClaim) -> str:
    return f"{c.subject}: {c.statement}" if c.subject else c.statement


def candidate_pairs(claims: list[ConflictClaim], embedder, min_cosine: float = CANDIDATE_MIN_COSINE,
                    cap: int = CANDIDATE_CAP) -> list[tuple[int, int]]:
    """Related claim pairs by cosine similarity; word overlap is the fallback without an embedder."""
    n = len(claims)
    pairs: list[tuple[float, int, int]] = []
    vectors = embedder.embed([_text(c) for c in claims]) if embedder and claims else None
    for i in range(n):
        for j in range(i + 1, n):
            if claims[i].uid and claims[i].uid == claims[j].uid:
                continue
            if vectors is not None:
                score = float(vectors[i] @ vectors[j])
            else:
                score = fuzz.token_set_ratio(_text(claims[i]), _text(claims[j])) / 100
            if score >= min_cosine:
                pairs.append((score, i, j))
    pairs.sort(reverse=True)
    return [(i, j) for _, i, j in pairs[:cap]]


def find_cards(claims: list[ConflictClaim], nli, embedder, roster=None) -> list[Card]:
    """A card must pass both the facet check and NLI contradiction (in either direction).

    The facet check is free and deterministic, so it filters first, and NLI runs only on what survives.
    """
    survivors = []
    for i, j in candidate_pairs(claims, embedder):
        facet = facet_check(claims[i], claims[j], roster)
        if facet:
            survivors.append((claims[i], claims[j], facet))
    if not survivors:
        return []
    forward = nli.score([(_text(a), _text(b)) for a, b, _ in survivors])
    backward = nli.score([(_text(b), _text(a)) for a, b, _ in survivors])
    cards = []
    for (a, b, facet), f, r in zip(survivors, forward, backward):
        score = max(f.contradiction, r.contradiction)
        if score >= CONTRADICTION_THRESHOLD:
            cards.append(Card(a, b, score, facet))
    return cards


def _ts(c: ConflictClaim) -> datetime:
    return c.as_of or datetime.min


def merge_cards(cards: list[Card]) -> list[ConflictGroup]:
    """Cards that share a claim describe one disagreement, so the reviewer resolves it once."""
    parent: dict[int, int] = {}

    def find(x: int) -> int:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for card in cards:
        parent[find(card.a.id)] = find(card.b.id)
    groups: dict[int, list[Card]] = {}
    for card in cards:
        groups.setdefault(find(card.a.id), []).append(card)

    out: list[ConflictGroup] = []
    for group_cards in groups.values():
        older: dict[int, ConflictClaim] = {}
        newer: dict[int, ConflictClaim] = {}
        for card in group_cards:
            first, second = sorted((card.a, card.b), key=_ts)
            older.setdefault(first.id, first)
            newer.setdefault(second.id, second)
        # A claim seen on both sides belongs with the side matching its date.
        for cid in set(older) & set(newer):
            del older[cid]
        best = max(group_cards, key=lambda c: c.nli)
        subject = min((c.subject for c in [*older.values(), *newer.values()] if c.subject), key=len, default="")
        out.append(ConflictGroup(sorted(older.values(), key=_ts), sorted(newer.values(), key=_ts), best.facet or "",
                                 best.nli, subject))
    return out


def expand_groups(groups: list[ConflictGroup], claims: list[ConflictClaim]) -> None:
    """A claim from the same card or issue, with the same status as a conflicted claim, is part of that conflict.

    Without this, "DB backups: completed" would stay Verified after "backup cron configured" is shown to be wrong.
    """
    placed = {c.id for g in groups for c in [*g.side_a, *g.side_b]}
    for g in groups:
        for side in (g.side_a, g.side_b):
            for member in list(side):
                polarity = _polarity(member)
                if not member.context or polarity is None:
                    continue
                for other in claims:
                    if (other.id not in placed and other.context == member.context and other.type == "status"
                            and _polarity(other) == polarity):
                        side.append(other)
                        placed.add(other.id)


def is_stale(value: str, statement: str, as_of: datetime | None, today: date, days: int = 30) -> bool:
    """A status of in-progress or to-do with nothing newer than 30 days."""
    if as_of is None:
        return False
    open_status = bool(re.search(r"\b(in[ _]progress|to[ _]?do)\b", f"{value} {statement}", re.I))
    return open_status and (today - as_of.date()).days > days
