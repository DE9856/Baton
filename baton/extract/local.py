"""Two extractors that need no API key.

- ReplayExtractor plays back claims a real LLM produced earlier over the demo dataset, so the one-click demo works
  without a key. Only the extraction step is replayed; every check after it runs live.
- RuleExtractor reads uploads with keyword rules when no LLM is configured. Its statements stay close to the source,
  so they stay checkable, but it finds only sentences that use expected words, and the UI says so.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .base import ChunkUnit, Extractor, RawClaim

RECORDED = Path(__file__).parent.parent / "demo" / "recorded_extraction_qwen25.json"


class ReplayExtractor(Extractor):
    name = "replay"
    label = "Recorded Qwen2.5-7B run"
    description = ("Demo mode: claims were produced by a real LLM (Qwen2.5-7B) on 30 Oct 2026 and are replayed here. "
                   "All verification below runs live.")
    chunk_size = None

    def __init__(self, path: Path = RECORDED):
        data = json.loads(Path(path).read_text("utf-8"))
        self.by_source: dict[str, list[dict]] = {}
        for c in data["claims"]:
            self.by_source.setdefault(c["source"], []).append(c)

    def extract(self, chunk, source_type, source_code):
        return [RawClaim(c["type"], c["subject"], c["statement"], c["value"] or "", c["evidence"])
                for c in self.by_source.get(source_code, [])]


_SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")
_DATE_WORD = r"(?:\d{1,2}\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*|\d{4}-\d{2}-\d{2})"
_RULES: list[tuple[str, re.Pattern]] = [
    ("access", re.compile(r"\b(signing key|api key|credentials?|password|admin (?:rights|access)|my \w+ account|on my laptop|registrar)\b", re.I)),
    ("recurring_problem", re.compile(r"\b(again|third time|keeps? (?:failing|happening|breaking)|recurring|workaround)\b", re.I)),
    ("risk", re.compile(r"\b(risk|could take|might break|no backups|never actually|will fail|danger|outage|pauses?)\b", re.I)),
    ("deadline", re.compile(rf"\b(due|deadline|target|by|pushed to|until)\b.*{_DATE_WORD}|{_DATE_WORD}.*\b(due|deadline)\b", re.I)),
    ("decision", re.compile(r"\b(decision|decided|we're going with|we are going with|let's (?:use|go)|moving (?:the|to)|move the|we'll use|stays on|switch to)\b", re.I)),
    ("dependency", re.compile(r"\b(depends? on|still need|blocked|waiting on|requires?|only after)\b", re.I)),
    ("status", re.compile(r"\b(closed|fixed|merged|finished|deployed|enabled|configured|completed|shipped)\b", re.I)),
    ("owner", re.compile(r"\b(owns?|is handling|responsible for|assigned to|takes? over|will take|\w+ take the)\b", re.I)),
    ("contact", re.compile(r"\b(contact|reach out to|talk to|[\w.]+@[\w.]+)\b", re.I)),
    ("event", re.compile(rf"\b(leaving|unavailable|out from|on leave|away)\b.*{_DATE_WORD}", re.I)),
    ("next_action", re.compile(r"\b(need to|needs to|must|have to|should|i'll|will write|todo|to do)\b", re.I)),
]
_TRELLO = re.compile(r"Card '(?P<name>.*?)' is in list '(?P<list>.*?)'(?:; due: (?P<due>[\d-]+))?(?:; members: (?P<members>[^;.]*))?")
_STATUS_WORDS = {"done": "done", "complete": "done", "completed": "done", "closed": "done",
                 "doing": "in_progress", "in progress": "in_progress", "to do": "todo", "todo": "todo", "backlog": "todo"}


_FIRST_PERSON = re.compile(r"\b(i|i'm|i'll|i've|my|me|we|we're|we'll|our|us)\b", re.I)
_RECORD_PREFIX = re.compile(r"^(?:Comment on (?:card '(?P<card>[^']*)'|\w+ #\d+):|(?:Issue|PR) #\d+ '(?P<title>[^']*)' \(state: [^)]*\)(?: \[[^\]]*\])?:)\s*")
# "Good news from support: the renewal is due" keeps its point; the lead-in goes.
_LEAD_IN = re.compile(r"^[^:]{0,45}:\s+(?=\S.{14,})")
_SUBJECT_END = re.compile(r"\b(is|are|was|were|will|would|has|have|had|must|should|can|could|may|needs?|stays?|owns?|due|by|for|on|at|until|after|before|because|so|but|and|to)\b", re.I)
_LEADING_WORDS = re.compile(r"^(?:the|a|an|our|my|this|that|new|now)\s+", re.I)


def _subject_of(statement: str) -> str:
    """The words before the verb, which usually name the thing: "the domain renewal is now due" gives "domain renewal"."""
    text = _LEADING_WORDS.sub("", statement.strip())
    m = _SUBJECT_END.search(text)
    head = text[:m.start()].strip(" ,.:;") if m and m.start() >= 3 else ""
    return head[:60] if head else " ".join(text.split()[:5]).strip(" ,.:;")


def _status_of(list_name: str) -> str:
    return _STATUS_WORDS.get(list_name.strip().lower(), list_name.strip().lower())


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE.split(text) if len(s.strip()) >= 15]


class RuleExtractor(Extractor):
    name = "rules"
    label = "Basic rules (no AI)"
    description = ("No AI model is configured, so claims come from keyword rules. They only find sentences that use expected "
                   "words, and they can't rewrite a chat line into a self-contained claim. Add GROQ_API_KEY to use a hosted model.")
    chunk_size = 25

    def extract(self, chunk: list[ChunkUnit], source_type: str, source_code: str) -> list[RawClaim]:
        out: list[RawClaim] = []
        for u in chunk:
            card = _TRELLO.match(u.text)
            if card:
                out.extend(self._card(u, card))
                continue
            prefix = _RECORD_PREFIX.match(u.text)
            topic = (prefix.group("card") or prefix.group("title") or "") if prefix else ""
            body = u.text[prefix.end():] if prefix else u.text
            for sentence in _sentences(body):
                claim = self._sentence(u, sentence, topic)
                if claim:
                    out.append(claim)
        return out

    def _card(self, u: ChunkUnit, m: re.Match) -> list[RawClaim]:
        name = m["name"]
        ev = lambda q: [{"id": u.uid, "quote": q}]  # noqa: E731
        claims = [RawClaim("status", name, f"The card '{name}' is in the list '{m['list']}'.", _status_of(m["list"]),
                           ev(f"Card '{name}' is in list '{m['list']}'"))]
        if m["due"]:
            claims.append(RawClaim("deadline", name, f"The card '{name}' is due on {m['due']}.", m["due"],
                                   ev(f"due: {m['due']}")))
        if m["members"]:
            claims.append(RawClaim("owner", name, f"{m['members'].strip()} is responsible for the card '{name}'.",
                                   m["members"].strip(), ev(f"members: {m['members'].strip()}")))
        return claims

    def _sentence(self, u: ChunkUnit, sentence: str, topic: str = "") -> RawClaim | None:
        if sentence.endswith("?"):
            kind = "open_question"
        else:
            kind = next((k for k, rx in _RULES if rx.search(sentence)), None)
        if kind is None:
            return None
        core = _LEAD_IN.sub("", sentence)
        # First-person sentences keep the speaker, or "I'll handle it" would say nothing on its own.
        statement = f"{u.author}: {core}" if _FIRST_PERSON.search(core) else core[0].upper() + core[1:]
        return RawClaim(kind, topic or _subject_of(core), statement, "", [{"id": u.uid, "quote": sentence}])
