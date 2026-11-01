"""How the pipeline copes with a large chat, using keyword rules and the real checking models on CPU.

Usage: python eval/scale_test.py [messages]. The messages are generated, so this measures speed and memory, not quality.
"""
from __future__ import annotations

import random
import sys
import time
import tracemalloc
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT.parent))

from baton.db import Member, Project, make_engine, make_sessionmaker  # noqa: E402
from baton.extract import RuleExtractor  # noqa: E402
from baton.parsers import parse_whatsapp  # noqa: E402
from baton.pipeline import add_source, run_pipeline  # noqa: E402

PEOPLE = ["Asha", "Ben", "Chitra", "Dev", "Esha"]
THINGS = ["the login page", "the payment flow", "the reports export", "the mobile build", "the search index", "the email digest", "the admin panel"]
LINES = ["lol ok", "will check", "thanks!", "sure", "{t} is due {d} Nov", "{t} is blocked until the vendor replies", "{t} failed again this week",
         "should we rewrite {t}?", "decision: we're going with a simpler version of {t}", "{p} owns {t}", "need to fix {t} before the release", "nice work everyone"]


def generate(n: int) -> str:
    rng = random.Random(7)
    when = datetime(2026, 1, 1, 9, 0)
    out = []
    for _ in range(n):
        when += timedelta(minutes=rng.randint(1, 240))
        line = rng.choice(LINES).format(t=rng.choice(THINGS), d=rng.randint(1, 28), p=rng.choice(PEOPLE))
        out.append(f"[{when:%d/%m/%y, %H:%M:%S}] {rng.choice(PEOPLE)}: {line}")
    return "\n".join(out)


def main(n: int) -> None:
    Session = make_sessionmaker(make_engine("sqlite:///:memory:"))
    text = generate(n)
    tracemalloc.start()
    with Session() as s:
        p = Project(name="Scale", as_of=date(2026, 12, 1), last_day=date(2026, 12, 20), is_sandbox=True)
        s.add(p)
        s.flush()
        people = [Member(project_id=p.id, name=x, role=r, aliases=[x], clearances=["public", "tech-team"]) for x, r in (("Asha", "leaver"), ("Ben", "successor"), ("Chitra", "reviewer"))]
        s.add_all(people)
        s.flush()
        p.leaver_id, p.successor_id, p.reviewer_id = people[0].id, people[1].id, people[2].id
        started = time.time()
        add_source(s, p, parse_whatsapp(text, "WA"), "whatsapp", "tech-team", "x", "", "WA", "Chat")
        s.commit()
        parsed_at = time.time()
        stats = run_pipeline(s, p, RuleExtractor())
        s.commit()
        done = time.time()
    _, peak = tracemalloc.get_traced_memory()
    print(f"{n} messages: parse and store {parsed_at - started:.1f} s; pipeline {done - parsed_at:.1f} s (NLI {stats['nli_seconds']} s); "
          f"{stats['claims']} claims, {stats['conflicts']} conflicts, {stats['questions'].get('leaver', 0)} questions for the leaver; "
          f"peak Python memory {peak / 1e6:.0f} MB")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 2000)
