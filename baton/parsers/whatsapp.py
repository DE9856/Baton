"""WhatsApp "Export chat" parser.

Handles iOS (`[01/08/26, 19:02:11] Name: text`) and Android (`01/08/26, 19:02 - Name: text`) exports,
12-hour clocks, multi-line messages and system lines. Day/month order is detected from the data,
never assumed from a locale.
"""
from __future__ import annotations

import re
from datetime import datetime

from .base import ParseError, ParseResult, Skipped, Unit

_INVISIBLE = "‎‏‪‬ ﻿"
_TIME = r"(?P<h>\d{1,2})[:.](?P<mi>\d{2})(?:[:.](?P<s>\d{2}))?(?:\s*(?P<ap>[apAP]\.?\s?[mM]\.?))?"
_DATE = r"(?P<a>\d{1,4})[/.\-](?P<b>\d{1,2})[/.\-](?P<c>\d{1,4})"
_IOS = re.compile(rf"^\[{_DATE},?\s+{_TIME}\]\s(?P<rest>.*)$")
_ANDROID = re.compile(rf"^{_DATE},?\s+{_TIME}\s[-–]\s(?P<rest>.*)$")

_SYSTEM_PATTERNS = re.compile(
    r"(end-to-end encrypted|created group|added you|was added|changed the (subject|group)|"
    r"changed this group|left$|removed |joined using|security code|deleted this message|"
    r"this message was deleted|you were added|disappearing messages)",
    re.I,
)
_MEDIA = re.compile(r"^(<media omitted>|image omitted|video omitted|audio omitted|sticker omitted|"
                    r"document omitted|gif omitted|contact card omitted|null)$", re.I)


def _clean(s: str) -> str:
    return s.translate({ord(c): None for c in _INVISIBLE}).rstrip()


def _match(line: str):
    return _IOS.match(line) or _ANDROID.match(line)


def detect_date_order(triples: list[tuple[int, int, int]]) -> str:
    """Return 'ymd', 'dmy' or 'mdy' from the values actually present in the file."""
    if not triples:
        return "dmy"
    if any(a > 31 for a, _, _ in triples):
        return "ymd"
    first_over_12 = any(a > 12 for a, _, _ in triples)
    second_over_12 = any(b > 12 for _, b, _ in triples)
    if first_over_12 and not second_over_12:
        return "dmy"
    if second_over_12 and not first_over_12:
        return "mdy"
    # Ambiguous: chats are chronological, so pick the order in which the dates never run backwards.
    def backwards(order: str) -> int:
        seq = []
        for a, b, c in triples:
            d, m = (a, b) if order == "dmy" else (b, a)
            seq.append((c, m, d))
        return sum(1 for x, y in zip(seq, seq[1:]) if y < x)

    return "mdy" if backwards("mdy") < backwards("dmy") else "dmy"


def _to_datetime(m, order: str) -> datetime:
    a, b, c = int(m["a"]), int(m["b"]), int(m["c"])
    if order == "ymd":
        y, mo, d = a, b, c
    elif order == "mdy":
        mo, d, y = a, b, c
    else:
        d, mo, y = a, b, c
    if y < 100:
        y += 2000
    h = int(m["h"])
    ap = (m["ap"] or "").lower().replace(".", "").replace(" ", "")
    if ap == "pm" and h < 12:
        h += 12
    if ap == "am" and h == 12:
        h = 0
    return datetime(y, mo, d, h, int(m["mi"]), int(m["s"] or 0))


def parse_whatsapp(text: str, code: str = "WA") -> ParseResult:
    lines = text.splitlines()
    raw: list[tuple[object, list[str]]] = []  # (header match, body lines)
    result = ParseResult()
    for n, line in enumerate(lines, 1):
        line = _clean(line)
        m = _match(line)
        if m:
            raw.append((m, [m["rest"]]))
        elif raw:
            if line.strip():
                raw[-1][1].append(line)
        elif line.strip():
            result.skipped.append(Skipped(f"line {n}", "text before the first message", line[:80]))

    if not raw:
        raise ParseError(
            "No WhatsApp messages found. Expected lines such as "
            "'[01/08/26, 19:05:43] Name: text' or '01/08/26, 19:05 - Name: text'. "
            "Please upload the .txt file from WhatsApp's Export chat."
        )

    triples = [(int(m["a"]), int(m["b"]), int(m["c"])) for m, _ in raw]
    order = detect_date_order(triples)
    result.notes.append(f"Date format detected from the data: {order}")

    # Name of the chat itself: iOS system lines are written as "<group>: <system text>".
    n = 0
    for m, body in raw:
        rest = "\n".join(body)
        try:
            ts = _to_datetime(m, order)
        except ValueError as e:
            result.skipped.append(Skipped(m.group(0)[:30], f"bad date: {e}", rest[:80]))
            continue
        if ": " not in rest:
            result.skipped.append(Skipped(ts.isoformat(), "system line (no sender)", rest[:80]))
            continue
        author, msg = rest.split(": ", 1)
        author = author.strip()
        msg = msg.strip()
        if _SYSTEM_PATTERNS.search(msg.splitlines()[0]) and "encrypted" in msg.lower() or (
            _SYSTEM_PATTERNS.search(msg) and len(msg) < 90 and msg.lower().startswith(("you ", "this message"))
        ):
            result.skipped.append(Skipped(ts.isoformat(), "system line", msg[:80]))
            continue
        if _MEDIA.match(msg):
            result.skipped.append(Skipped(ts.isoformat(), "media without text", f"{author}: {msg}"[:80]))
            continue
        n += 1
        result.units.append(Unit(uid=f"{code}-{n:04d}", author=author, ts=ts, text=msg, kind="chat"))
    if not result.units:
        raise ParseError("The file looks like a WhatsApp export, but contains no readable text messages.")
    result.roster = [{"name": a, "aliases": [a]} for a in sorted({u.author for u in result.units})]
    return result
