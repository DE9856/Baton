"""Trello board JSON export parser (board menu -> Print and export -> Export as JSON)."""
from __future__ import annotations

import json
from datetime import datetime

from .base import ParseError, ParseResult, Skipped, Unit


def _ts(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)


def parse_trello(text: str | dict, code: str = "TR") -> ParseResult:
    if isinstance(text, str):
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise ParseError(f"This file is not valid JSON ({e.msg}, line {e.lineno}).") from e
    else:
        data = text
    if not isinstance(data, dict) or "cards" not in data or "lists" not in data:
        raise ParseError('Expected a Trello board export with "cards" and "lists". '
                         "In Trello: board menu, Print and export, Export as JSON.")

    members = {m.get("id"): m.get("fullName") or m.get("username") or "unknown" for m in data.get("members", [])}
    lists = {x.get("id"): x.get("name", "unknown list") for x in data.get("lists", [])}
    result = ParseResult(title=data.get("name", "Trello board"))
    card_ids: dict[str, str] = {}

    for pos, card in enumerate(data["cards"], 1):
        if card.get("closed"):
            result.skipped.append(Skipped(f"card {pos}", "archived card", card.get("name", "")[:80]))
            continue
        cid = str(card.get("id", pos))
        short = cid if cid.startswith("c") and len(cid) < 8 else f"c{pos}"
        uid = f"{code}-{short}"
        card_ids[cid] = uid
        parts = [f"Card '{card.get('name', '')}' is in list '{lists.get(card.get('idList'), 'unknown list')}'"]
        if card.get("due"):
            parts.append(f"due: {card['due'][:10]}")
        names = [members.get(i, "unknown member") for i in card.get("idMembers", [])]
        if names:
            parts.append("members: " + ", ".join(names))
        if card.get("dateLastActivity"):
            parts.append(f"last activity: {card['dateLastActivity'][:10]}")
        body = "; ".join(parts) + "."
        if (card.get("desc") or "").strip():
            body += f" Description: {card['desc'].strip()}"
        result.units.append(
            Unit(uid, names[0] if names else "trello", _ts(card.get("dateLastActivity")), body,
                 card.get("shortUrl") or card.get("url") or "", "card")
        )

    n = 0
    for action in data.get("actions", []):
        if action.get("type") != "commentCard":
            continue
        info = action.get("data") or {}
        comment = (info.get("text") or "").strip()
        if not comment:
            result.skipped.append(Skipped("comment", "empty comment"))
            continue
        n += 1
        card = info.get("card") or {}
        result.units.append(
            Unit(f"{code}-a{n}", (action.get("memberCreator") or {}).get("fullName") or "unknown",
                 _ts(action.get("date")), f"Comment on card '{card.get('name', '')}': {comment}", "", "comment")
        )

    if not result.units:
        raise ParseError("The Trello export has no open cards or comments.")
    result.roster = [
        {"name": m.get("fullName") or m.get("username"),
         "aliases": [x for x in (m.get("fullName"), m.get("username")) if x]}
        for m in data.get("members", [])
    ]
    return result
