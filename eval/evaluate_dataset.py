"""Scores Baton on any dataset laid out as in eval/second_dataset/. Usage: python eval/evaluate_dataset.py <folder> [--show]

Uses Groq when GROQ_API_KEY is set and keyword rules otherwise. A simulated reviewer takes the newer side of each
conflict and approves non-withheld items; nobody answers the interview.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT.parent))

from dotenv import load_dotenv  # noqa: E402
from sqlalchemy import select  # noqa: E402

from baton import review  # noqa: E402
from baton.db import Conflict, Member, Project, make_engine, make_sessionmaker  # noqa: E402
from baton.extract import default_extractor  # noqa: E402
from baton.handover import compose, to_markdown  # noqa: E402
from baton.parsers import decode_text, parse_github, parse_notes, parse_trello, parse_whatsapp  # noqa: E402
from baton.pipeline import add_source, run_pipeline  # noqa: E402
from eval.evaluate import simulate_review  # noqa: E402


def hit(lines, patterns):
    return any(all(re.search(p, line, re.I) for p in patterns) for line in lines)


def main(folder: Path) -> None:
    load_dotenv()
    manifest = json.loads((folder / "manifest.json").read_text("utf-8"))
    gold = json.loads((folder / "gold.json").read_text("utf-8"))
    Session = make_sessionmaker(make_engine("sqlite:///:memory:"))
    with Session() as s:
        p = Project(name=manifest["project"], role_title=manifest.get("role_title", "Tech lead"), as_of=date.fromisoformat(manifest["as_of"]),
                    last_day=date.fromisoformat(manifest["last_day"]), is_sandbox=True)
        s.add(p)
        s.flush()
        people = {}
        for person in manifest["people"]:
            m = Member(project_id=p.id, name=person["name"], title=person.get("title", ""), role=person["role"], aliases=person.get("aliases", []),
                       clearances=person["clearances"])
            s.add(m)
            people[person["role"]] = m
        s.flush()
        p.leaver_id, p.successor_id, p.reviewer_id = people["leaver"].id, people["successor"].id, people["reviewer"].id
        readers = {"whatsapp": parse_whatsapp, "github": parse_github, "trello": parse_trello}
        for src in manifest["sources"]:
            text = decode_text((folder / src["path"]).read_bytes())
            parsed = parse_notes(text, src["code"], src["path"]) if src["type"] == "notes" else readers[src["type"]](text, src["code"])
            add_source(s, p, parsed, src["type"], src["audience"], "dataset", "Synthetic", src["code"], src.get("title"))
        s.commit()
        stats = run_pipeline(s, p, default_extractor())
        s.commit()
        n_conflicts = len(s.scalars(select(Conflict).where(Conflict.project_id == p.id)).all())
        simulate_review(s, p)
        s.commit()
        version = review.latest_version(s, p.id)
        print(f"Extractor: {stats['extractor']}. Records: {sum(1 for _ in s.execute(select(1).select_from(__import__('baton.db', fromlist=['EvidenceUnit']).EvidenceUnit)))}. "
              f"Claims: {stats['claims']}, grounded {stats['grounded']}, verified {stats['verified']}, needs review {stats['needs_review']}. "
              f"Conflict cards shown to the reviewer: {n_conflicts}. Questions for the leaver: {stats['questions'].get('leaver', 0)}.")
        if stats["incomplete"]:
            print("INCOMPLETE:", stats["failed_chunks"], stats["extraction_errors"][:2])
        for m in s.scalars(select(Member).where(Member.project_id == p.id)).all():
            md = to_markdown(compose(version, m), p.name, m)
            lines = [x for x in md.split("\n") if x.strip()]
            facts = [k for k, v in gold["facts"].items() if hit(lines, v)]
            conflicts = [k for k, v in gold["conflicts"].items() if hit(lines, v)]
            leaked = [c for c in gold["canaries"] if c.lower() in md.lower()]
            print(f"{m.name} ({m.role}): facts {len(facts)}/{len(gold['facts'])}, missed {sorted(set(gold['facts']) - set(facts))}, "
                  f"conflicts {len(conflicts)}/{len(gold['conflicts'])} {conflicts}, leaked {leaked}")
            if "--show" in sys.argv and m.role == "successor":
                print(md.encode("ascii", "replace").decode())
        for cf in s.scalars(select(Conflict).where(Conflict.project_id == p.id)).all():
            print(f"  conflict card [{cf.facet}] {cf.subject[:70]}")
        blob = json.dumps(version.snapshot, ensure_ascii=False)
        print("Secrets in the stored snapshot:", [c for c in gold.get("never_for_anyone", []) if c in blob])


if __name__ == "__main__":
    main(Path(sys.argv[1]))
