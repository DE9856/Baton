"""Claim schema and extractor interface. Whichever extractor runs, the output goes through the same verification."""
from __future__ import annotations

from dataclasses import dataclass, field

CLAIM_TYPES = ["decision", "owner", "deadline", "status", "risk", "dependency", "open_question", "next_action",
               "contact", "access", "recurring_problem", "event"]

TYPE_TITLES = {
    "decision": "Decision", "owner": "Owner", "deadline": "Deadline", "status": "Status", "risk": "Risk",
    "dependency": "Dependency", "open_question": "Open question", "next_action": "Next action", "contact": "Contact",
    "access": "Access", "recurring_problem": "Recurring problem", "event": "Event",
}


@dataclass
class ChunkUnit:
    uid: str
    author: str
    when: str
    text: str


@dataclass
class RawClaim:
    type: str
    subject: str
    statement: str
    value: str = ""
    evidence: list[dict] = field(default_factory=list)  # [{"id":..., "quote":...}]


class ExtractionError(RuntimeError):
    """One chunk failed. The pipeline records it and marks the run incomplete."""


class Extractor:
    name = "base"
    label = "Base extractor"
    description = ""
    chunk_size: int | None = 15  # records per chunk; None means the whole source

    def extract(self, chunk: list[ChunkUnit], source_type: str, source_code: str) -> list[RawClaim]:
        raise NotImplementedError
