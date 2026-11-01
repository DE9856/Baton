"""Ask Baton: cited answers from the approved claims this viewer may see. It abstains rather than guesses.

Answers are extractive: Baton returns the approved claims themselves, with their labels and receipts, and writes no
new sentences, so an answer can't say more than was checked. It can't leak what the viewer can't see, because the
search only ever receives that viewer's items.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .handover import _dedupe, item_visible

MIN_COSINE = 0.42
KEEP_WITHIN = 0.12
MAX_RESULTS = 4
_STOP = set("""a an the is are was were be been being do does did to of for on in at by and or with from it its this that these those
will would should must can could may need needs has have had what when where who whom whose why how which there here we our us you
your they their them he she his her i me my about after before into over any some all not no yes so if then than as up out get got
project team please tell know want thing things happen happens happening going current currently still next now""".split())


@dataclass
class Answer:
    question: str
    items: list[dict] = field(default_factory=list)
    abstained: bool = False
    reason: str = ""


def _tokens(text: str) -> set[str]:
    out = set()
    for w in re.findall(r"[a-z0-9#.]+", text.lower()):
        w = w.strip(".")
        if len(w) < 2 or w in _STOP:
            continue
        out.add(w[:-1] if w.endswith("s") and len(w) > 3 else w)
    return out


def searchable_items(snapshot: dict, viewer) -> list[dict]:
    items = [c for c in snapshot.get("claims", []) if item_visible(c, viewer) and c["label"] in ("verified", "confirmed")]
    return _dedupe(items)


def ask(question: str, snapshot: dict, viewer, embedder=None) -> Answer:
    question = (question or "").strip()
    answer = Answer(question)
    q_tokens = _tokens(question)
    if not q_tokens:
        answer.abstained, answer.reason = True, "That question doesn't name anything to look for."
        return answer
    items = searchable_items(snapshot, viewer)
    if not items:
        answer.abstained, answer.reason = True, "Nothing has been approved for you to see yet."
        return answer

    texts = [f"{i.get('subject') or ''}: {i['statement']}" for i in items]
    overlaps = [q_tokens & _tokens(t) for t in texts]
    if embedder:
        vectors = embedder.embed([question, *texts])
        cosines = [float(vectors[0] @ v) for v in vectors[1:]]
    else:
        cosines = [len(o) / max(len(q_tokens), 1) for o in overlaps]

    # A claim needs both: similar meaning, and at least one specific word in common with the question.
    ranked = sorted(((c, n) for n, c in enumerate(cosines) if overlaps[n]), reverse=True)
    if not ranked or ranked[0][0] < MIN_COSINE:
        answer.abstained = True
        answer.reason = "The approved handover you can see doesn't answer this. It may be unknown, or outside what your role can see."
        return answer
    top = ranked[0][0]
    answer.items = [items[n] for c, n in ranked if c >= top - KEEP_WITHIN][:MAX_RESULTS]
    return answer
