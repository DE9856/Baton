"""One-click demo project: the synthetic CampusConnect dataset. No real people or data are involved."""
from __future__ import annotations

from datetime import date
from pathlib import Path

from sqlalchemy.orm import Session

from ..db import Member, Project, audit
from ..parsers import parse_github, parse_notes, parse_trello, parse_whatsapp
from ..pipeline import add_source

DATA = Path(__file__).parent / "data"
TECH = ["public", "tech-team"]
ALL = ["public", "tech-team", "core-committee"]


def load_demo(s: Session) -> Project:
    p = Project(name="CampusConnect (synthetic demo)", is_demo=True, role_title="Tech lead", as_of=date(2026, 10, 30),
                last_day=date(2026, 11, 15))
    s.add(p)
    s.flush()
    people = [
        ("Arjun Mehta", "Final-year tech lead, leaving", "leaver", ["Arjun", "arjun-m", "arjunmehta"], TECH),
        ("Meera Iyer", "Incoming tech lead", "successor", ["Meera", "meeraiyer"], TECH),
        ("Sana", "Club president, reviewer", "reviewer", ["Sana"], ALL),
        ("Kabir Shah", "Backend developer", "teammate", ["Kabir", "kabirs", "kabirshah"], TECH),
        ("Riya Thomas", "Designer", "teammate", ["Riya", "riya-t", "riyathomas"], TECH),
        ("New member", "Just joined the club", "teammate", ["New member"], ["public"]),
    ]
    members = {}
    for name, title, role, aliases, clearances in people:
        m = Member(project_id=p.id, name=name, title=title, role=role, aliases=aliases, clearances=clearances)
        s.add(m)
        members[role if role != "teammate" else name] = m
    s.flush()
    p.leaver_id, p.successor_id, p.reviewer_id = members["leaver"].id, members["successor"].id, members["reviewer"].id

    def read(rel: str) -> str:
        return (DATA / rel).read_text("utf-8")

    note = "Synthetic demo data"
    add_source(s, p, parse_whatsapp(read("whatsapp_devcircle_tech.txt"), "WA"), "whatsapp", "tech-team",
               "Arjun (group member export)", note, "WA", "WhatsApp: DevCircle Tech")
    add_source(s, p, parse_github(read("github_campusconnect.json"), "GH"), "github", "public",
               "Public repository", note, "GH", "GitHub: devcircle/campusconnect")
    add_source(s, p, parse_trello(read("trello_campusconnect.json"), "TR"), "trello", "tech-team",
               "Arjun (board member export)", note, "TR", "Trello: CampusConnect v2")
    for code, fname, audience, who, title in (
        ("N1", "2026-09-12-tech-sync.md", "tech-team", "Arjun (author)", "Notes: tech sync, 12 Sep"),
        ("N2", "2026-10-17-tech-sync.md", "tech-team", "Arjun (author)", "Notes: tech sync, 17 Oct"),
        ("N3", "2026-10-03-core-committee.md", "core-committee", "Sana (President)", "Notes: core committee, 3 Oct"),
    ):
        add_source(s, p, parse_notes(read(f"notes/{fname}"), code, fname, who.split(" ")[0]), "notes", audience, who, note, code, title)
    audit(s, p.id, "Baton", "demo_loaded", p.name, "Synthetic CampusConnect dataset")
    return p
