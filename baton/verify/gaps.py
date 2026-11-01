"""Gap questions and routing: the leaver gets only what they alone can answer.

Departure-cliff rules are deterministic checks over claims. Conflicts go to the reviewer, unanswered team questions
to triage, and anything from a source the leaver may not see goes to the reviewer.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

LEAVER_BUDGET = 12

# Role templates: what a successor in this role must know. Coverage is a keyword check over verified claims.
ROLE_TEMPLATES: dict[str, dict[str, dict]] = {
    "Tech lead": {
        "deployment": {
            "evidence": re.compile(r"\b(push to main|auto-?deploys?|deployment (?:steps|process)|deploy (?:by|with|using)|"
                                   r"to deploy|ci/cd|pipeline)\b", re.I),
            "mention": re.compile(r"\bdeploy", re.I),
            "question": "How is the project deployed, step by step, where do its settings live, and who else can do it?",
        },
    },
}
ROLE_TEMPLATES["default"] = ROLE_TEMPLATES["Tech lead"]

_HAS_REASON = re.compile(r"\b(because|why|since|so that|reason|due to|as the|as our|half the|too expensive|lets us|gives us|"
                         r"it's the \w+)\b", re.I)
# "must" and "need to" report an outside requirement rather than a choice the leaver can explain.
_NOT_A_CHOICE = re.compile(r"\b(must|need to|needs to|have to|mandatory|mandated)\b", re.I)
_DUE_SOON_TYPES = {"owner", "access", "deadline", "next_action"}


@dataclass
class GapClaim:
    id: int
    type: str
    subject: str
    statement: str
    quote: str
    author: str
    audience: str
    label: str
    due: date | None
    sensitive: bool = False
    secret: bool = False
    next_author: str = ""
    next_text: str = ""


@dataclass
class Question:
    kind: str
    text: str
    priority: int
    route: str
    claim_id: int | None
    audience: str


def mentions_person(text: str, aliases: list[str]) -> bool:
    low = text.lower()
    tokens = set(re.split(r"[^a-z0-9-]+", low))
    return any(a.lower() in tokens or (" " in a and a.lower() in low) for a in aliases)


def _first_person_holder(c: GapClaim, leaver: list[str]) -> bool:
    """The leaver said "on my account", "I have it", "I'll handle it" in their own message."""
    holder = re.compile(r"\b(i have|my \w+ account|on my|i'll|i will|before i)\b", re.I)
    if mentions_person(c.author, leaver) and holder.search(c.quote):
        return True
    # "Who has the key?" answered in the next message by the leaver: "I have it on my laptop".
    return mentions_person(c.next_author, leaver) and bool(holder.search(c.next_text))


def involves_leaver(c: GapClaim, leaver: list[str]) -> bool:
    return mentions_person(f"{c.statement} {c.subject}", leaver) or _first_person_holder(c, leaver)


def is_cliff(c: GapClaim, leaver: list[str], last_day: date, owned_subjects: set[str] | None = None) -> bool:
    """Owner, access, deadline or next-action claims that lapse once the leaver is gone."""
    if c.type not in _DUE_SOON_TYPES:
        return False
    if c.due and c.due < last_day and c.type == "deadline":
        return False
    if involves_leaver(c, leaver):
        return True
    return bool(owned_subjects and c.type == "deadline" and _norm_subject(c.subject) in owned_subjects)


def _norm_subject(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def _short(s: str, n: int = 140) -> str:
    s = re.sub(r"\s+", " ", s).strip()
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def raise_questions(claims: list[GapClaim], leaver: list[str], leaver_clearances: list[str], last_day: date,
                    template: str = "default", n_conflicts: int = 0) -> list[Question]:
    """Every question the evidence raises, each already routed. Dedup and budget are applied by route_questions."""
    last = f"{last_day.day} {last_day.strftime('%b')}"
    qs: list[Question] = []
    usable = [c for c in claims if c.label not in ("unsupported", "needs_review")]
    owned = {_norm_subject(c.subject) for c in usable
             if c.type == "owner" and involves_leaver(c, leaver)}

    def route_for(c: GapClaim) -> str:
        return "leaver" if c.audience in leaver_clearances else "reviewer"

    asked_subjects: set[str] = set()
    for c in sorted(usable, key=lambda x: x.type != "owner"):  # the ownership wording is the clearest
        if c.secret:
            qs.append(Question("credential", f"{_short(c.statement)} Who will own the new one?", 1, route_for(c), c.id, c.audience))
            continue
        if c.sensitive:
            continue  # sensitive items are decided by the reviewer, not turned into shared questions
        if is_cliff(c, leaver, last_day, owned):
            subject = _norm_subject(c.subject)
            if subject in asked_subjects:
                continue  # one question per item: "owner of X" and "deadline of X" have the same answer
            asked_subjects.add(subject)
            qs.append(Question("ownership", f"{_short(c.statement)} Who takes this over after {last}?", 2, route_for(c), c.id, c.audience))
        elif (c.type == "decision" and c.label == "verified" and not _HAS_REASON.search(f"{c.statement} {c.quote}")
              and not _NOT_A_CHOICE.search(c.statement)):
            qs.append(Question("rationale", f"Why was this decided? “{_short(c.statement)}”", 4, route_for(c), c.id, c.audience))
        elif c.type == "open_question" and c.label == "verified":
            qs.append(Question("open_question", _short(c.statement), 6, "triage", c.id, c.audience))

    # Coverage against the role template: what the successor needs and the evidence doesn't give.
    fields = ROLE_TEMPLATES.get(template, ROLE_TEMPLATES["default"])
    verified_text = " ".join(f"{c.statement} {c.quote}" for c in usable if c.label == "verified")
    all_text = " ".join(f"{c.statement} {c.quote}" for c in claims)
    for name, spec in fields.items():
        if spec["evidence"].search(verified_text):
            continue
        hint = ""
        if spec["mention"] is not None:
            m = re.search(r"it's a bit manual", all_text, re.I)
            if m:
                hint = f" We found only “{m.group(0)}”, with no steps."
        qs.append(Question("coverage", f"[{name}] {spec['question']}{hint}", 1, "leaver", None, "tech-team"))
    return qs


def route_questions(qs: list[Question], budget: int = LEAVER_BUDGET) -> tuple[list[Question], dict]:
    """Remove duplicates, then keep the leaver's list short by priority. Overflow goes to the reviewer, not the bin."""
    seen: set[str] = set()
    unique: list[Question] = []
    for q in sorted(qs, key=lambda x: x.priority):
        key = re.sub(r"[^a-z0-9 ]", "", q.text.lower())
        key = " ".join(key.split()[:9])
        if key in seen:
            continue
        seen.add(key)
        unique.append(q)
    leaver = [q for q in unique if q.route == "leaver"]
    for q in leaver[budget:]:
        q.route = "reviewer"
    counts = {"raised": len(qs), "unique": len(unique)}
    for route in ("leaver", "reviewer", "triage"):
        counts[route] = sum(1 for q in unique if q.route == route)
    return unique, counts
