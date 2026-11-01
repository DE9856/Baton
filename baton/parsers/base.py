"""Shared parser types. Every parser returns evidence units plus an explicit skip report."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Unit:
    """The smallest citable thing: one message, comment, card or bullet."""

    uid: str
    author: str
    ts: datetime | None
    text: str
    link: str = ""
    kind: str = "text"  # chat | issue | comment | event | card | note


@dataclass
class Skipped:
    where: str
    reason: str
    excerpt: str = ""


@dataclass
class ParseResult:
    units: list[Unit] = field(default_factory=list)
    skipped: list[Skipped] = field(default_factory=list)
    title: str = ""
    notes: list[str] = field(default_factory=list)
    roster: list[dict] = field(default_factory=list)  # {"name":..., "aliases":[...]} hints


class ParseError(ValueError):
    """The file cannot be read as this source type. The message is shown to the user."""


def decode_text(data: bytes) -> str:
    """Exports are UTF-8, sometimes with a BOM. Some Windows tools write UTF-16, which always starts with a BOM."""
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        try:
            return data.decode("utf-16")
        except UnicodeDecodeError:
            pass
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ParseError("This file is not UTF-8 text. Please upload the original export.") from None
