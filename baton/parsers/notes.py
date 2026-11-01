"""Meeting-note parser for .md, .txt and .docx. One bullet or paragraph is one evidence unit.

The note's date comes from its title or file name; headings give context but are not citable claims.
"""
from __future__ import annotations

import io
import re
from datetime import datetime
from pathlib import PurePath

from .base import ParseError, ParseResult, Skipped, Unit

_ISO = re.compile(r"(20\d{2})-(\d{2})-(\d{2})")
_DMY = re.compile(r"\b(\d{1,2})\s+([A-Za-z]{3,9})\.?,?\s+(20\d{2})\b")
_MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}
_BULLET = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+")
_HEADING = re.compile(r"^\s*#{1,6}\s+(.*)$")


def _find_date(*texts: str) -> datetime | None:
    for t in texts:
        if not t:
            continue
        m = _DMY.search(t)
        if m and m.group(2)[:3].lower() in _MONTHS:
            try:
                return datetime(int(m.group(3)), _MONTHS[m.group(2)[:3].lower()], int(m.group(1)))
            except ValueError:
                pass
        m = _ISO.search(t)
        if m:
            try:
                return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            except ValueError:
                pass
    return None


def docx_to_text(data: bytes) -> str:
    import docx  # imported here so plain-text parsing has no dependency on it

    try:
        document = docx.Document(io.BytesIO(data))
    except Exception as e:  # python-docx raises several unrelated types on malformed files
        raise ParseError("This .docx file could not be opened. It may be corrupted or not a real Word file.") from e
    lines = []
    for p in document.paragraphs:
        text = p.text.strip()
        if not text:
            continue
        style = (p.style.name or "").lower() if p.style is not None else ""
        if style.startswith("title") or style.startswith("heading"):
            lines.append(f"# {text}")
        elif "list" in style:
            lines.append(f"- {text}")
        else:
            lines.append(text)
    for table in document.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells:
                lines.append("- " + " | ".join(cells))
    return "\n".join(lines)


def parse_notes(text: str, code: str = "N1", filename: str = "", author: str = "notes", link: str = "") -> ParseResult:
    if not text.strip():
        raise ParseError("This notes file is empty.")
    lines = text.splitlines()
    headings = [m.group(1) for m in (_HEADING.match(x) for x in lines) if m]
    title = headings[0] if headings else PurePath(filename).stem or "Notes"
    when = _find_date(title, PurePath(filename).name if filename else "", text[:400])
    result = ParseResult(title=title)
    if when is None:
        result.notes.append("No date found in the title or file name; these records are undated.")
    n = 0
    para: list[str] = []

    def flush() -> None:
        nonlocal n, para
        if para:
            body = " ".join(para).strip()
            para = []
            if len(body) < 2:
                result.skipped.append(Skipped("paragraph", "too short to cite", body))
                return
            n += 1
            result.units.append(Unit(f"{code}-{n:02d}", author, when, body, link, "note"))

    for line in lines:
        if _HEADING.match(line):
            flush()
            continue
        if not line.strip():
            flush()
            continue
        if _BULLET.match(line):
            flush()
            para = [_BULLET.sub("", line).strip()]
            flush()
        else:
            # Lines such as "Attendees: ..." stand alone instead of merging with the next paragraph.
            flush()
            para = [line.strip()]
            flush()
    flush()
    if not result.units:
        raise ParseError("No readable text was found in this notes file.")
    return result
