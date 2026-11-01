"""Composes the successor's view from an approved version, filtered for one viewer at read time.

Writing for a viewer uses only that viewer's visible claims, and critical sections are rendered from claims rather
than generated prose, so nothing here can state more than the approved claims do.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta

from rapidfuzz import fuzz

from .db import Member

SECTIONS = [
    ("start_here", "Start here: next 14 days"),
    ("plan", "Your first two weeks"),
    ("decisions", "Decisions and why"),
    ("owners", "Who owns what"),
    ("risks", "Risks and dependencies"),
    ("problems", "Recurring problems"),
    ("contacts", "Contacts"),
    ("access", "Access to transfer"),
    ("questions", "Open questions"),
    ("timeline", "Timeline"),
]
SECTION_TITLES = dict(SECTIONS)
_TYPE_SECTION = {"decision": "decisions", "owner": "owners", "risk": "risks", "dependency": "risks", "recurring_problem": "problems",
                 "contact": "contacts", "access": "access", "open_question": "questions"}


def item_visible(item: dict, viewer: Member | None) -> bool:
    """The same rule as permissions.visible, applied to a snapshot row."""
    if viewer is None or item.get("audience") not in (viewer.clearances or []):
        return False
    if viewer.role == "reviewer":
        return True
    return not item.get("sensitive")


@dataclass
class Handover:
    version: int
    approved_by: str
    approved_at: str
    last_day: date
    role_title: str
    sections: dict[str, list[dict]] = field(default_factory=dict)
    plan: dict[str, list[dict]] = field(default_factory=dict)
    timeline: list[dict] = field(default_factory=list)
    missing: list[dict] = field(default_factory=list)
    conflicts: list[dict] = field(default_factory=list)
    total_items: int = 0


def _dedupe(items: list[dict]) -> list[dict]:
    """Near-identical statements collapse into one line that carries every receipt."""
    kept: list[dict] = []
    for item in items:
        twin = next((k for k in kept if fuzz.token_set_ratio(k["statement"].lower(), item["statement"].lower()) >= 92), None)
        if twin is None:
            kept.append({**item, "receipts": list(item["receipts"]), "also": []})
        else:
            twin["receipts"].extend(r for r in item["receipts"] if r["uid"] not in {x["uid"] for x in twin["receipts"]})
            twin["also"].append(item["code"])
    return kept


def _due(item: dict) -> date | None:
    return date.fromisoformat(item["due"]) if item.get("due") else None


def compose(version, viewer: Member | None) -> Handover:
    snap = version.snapshot
    last_day = date.fromisoformat(snap["last_day"])
    items = [c for c in snap["claims"] if item_visible(c, viewer) and c["label"] in ("verified", "confirmed")]
    items = _dedupe(items)
    h = Handover(version.number, version.approved_by, version.approved_at.strftime("%d %b %Y"), last_day, snap.get("role_title", ""))
    h.total_items = len(items)

    by_section: dict[str, list[dict]] = {key: [] for key, _ in SECTIONS}
    for item in items:
        key = _TYPE_SECTION.get(item["type"])
        if key:
            by_section[key].append(item)

    # Start here: what falls due within 14 days of the successor starting, plus anything that lapses with the leaver.
    horizon = last_day + timedelta(days=14)
    start_soon = date.fromisoformat(snap["as_of"])
    soon = [i for i in items if (d := _due(i)) and start_soon <= d <= horizon]
    risks = [i for i in items if i["cliff"] and i not in soon]
    by_section["start_here"] = sorted(soon, key=lambda i: _due(i)) + risks

    # The plan: access first, then work in date order.
    plan = {"Day 1": [], "Week 1": [], "Week 2": [], "Later": []}
    for i in items:
        d = _due(i)
        if i["type"] == "access" or (i["cliff"] and d is None):
            plan["Day 1"].append(i)
        elif d is not None and d >= last_day:
            if d <= last_day + timedelta(days=7):
                plan["Week 1"].append(i)
            elif d <= horizon:
                plan["Week 2"].append(i)
            else:
                plan["Later"].append(i)
        elif i["type"] == "next_action":
            plan["Week 1"].append(i)
        elif i["type"] == "risk":
            plan["Week 2"].append(i)
    h.plan = plan
    h.sections = by_section

    # Everything dated, for the timeline; the last day is marked by the template.
    dated = []
    for i in items:
        when = i["due"] or i["as_of"]
        if when:
            dated.append({"date": date.fromisoformat(when), "item": i, "is_deadline": bool(i["due"])})
    h.timeline = sorted(dated, key=lambda x: x["date"])

    # Conflicts stay visible after resolution, but only if every claim involved is visible to this viewer.
    for cf in snap["conflicts"]:
        allowed = viewer is not None and all(a in (viewer.clearances or []) for a in cf["audiences"])
        if allowed and (viewer.role == "reviewer" or not cf["sensitive"]):
            h.conflicts.append(cf)

    h.missing = [q for q in snap["missing"] if viewer is not None and q["audience"] in (viewer.clearances or [])]
    # A question that is still open appears once, as Missing, with the person to ask.
    missing_text = " ".join(q["text"].rstrip("\u2026") for q in h.missing).lower()  # shortened questions end with an ellipsis
    by_section["questions"] = [i for i in by_section["questions"] if i["statement"].lower()[:100] not in missing_text]
    return h


def needs_subject(item: dict) -> bool:
    """A line names its subject only if the statement doesn't already say it."""
    subject, statement = (item.get("subject") or "").lower(), (item.get("statement") or "").lower()
    words = [w for w in re.findall(r"[a-z0-9#]+", subject) if len(w) > 2 and w not in ("the", "for", "and", "with")] or subject.split()
    if not words:
        return False
    return sum(w in statement for w in words) / len(words) < 0.75


def to_markdown(h: Handover, project_name: str, viewer: Member | None) -> str:
    lines = [f"# Handover: {project_name}", f"Role: {h.role_title}. Prepared for {viewer.name if viewer else 'nobody'}.",
             f"Approved by {h.approved_by} on {h.approved_at}, version {h.version}. The leaver's last day is {_fmt(h.last_day)}.",
             "", "Baton may miss things. Check the Missing items with your team.", ""]

    def line(i: dict) -> str:
        label = "VERIFIED" if i["label"] == "verified" else f"CONFIRMED BY {(i.get('answered_by') or 'PERSON').upper()}"
        due = f" (due {i['due']})" if i.get("due") else ""
        subject = f"{i['subject']}: " if i.get("subject") else ""
        text = f"- [{label}] {subject}{i['statement']}{due}"
        quotes = "; ".join(f"{r['source']}, {r['when']}: “{r['quote']}” [{r['uid']}]" for r in i["receipts"][:2])
        return f"{text} Source: {quotes}" if quotes else text

    if h.conflicts:
        lines += ["## Conflicts the reviewer resolved"]
        for cf in h.conflicts:
            lines.append("- " + conflict_line(cf))
        lines.append("")
    for key, title in SECTIONS:
        if key in ("plan", "timeline"):
            continue
        rows = h.sections.get(key) or []
        if rows:
            lines += [f"## {title}"] + [line(i) for i in rows] + [""]
    lines += ["## Your first two weeks"]
    for name, rows in h.plan.items():
        if rows:
            lines += [f"### {name}"] + [line(i) for i in rows]
    lines.append("")
    # Items of other types (dated events, status) are kept in the timeline.
    lines += ["## Timeline"]
    for row in h.timeline:
        lines.append(f"- {_fmt(row['date'])}: {row['item']['statement']}")
    lines.append("")
    if h.missing:
        lines += ["## Missing: nobody has answered these yet"]
        for q in h.missing:
            lines.append(f"- [MISSING, ask {q['ask']}] {q['text']}")
    return "\n".join(lines) + "\n"


def conflict_line(cf: dict) -> str:
    who = cf["resolved_by"] or "the reviewer"
    winners = " / ".join(cf["winner_text"]) or "Neither side was correct."
    losers = "; ".join(f"{q['source']}, {q['when']}: \u201c{q['quote']}\u201d" for q in cf.get("loser_quotes", []))
    return f"{winners} Overruled: {losers}. Resolved by {who}."


def _fmt(d: date) -> str:
    return f"{d.day} {d.strftime('%b %Y')}"
