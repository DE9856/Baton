"""Measures Ask Baton on the demo: answerable questions must find the right claim, others must abstain.

The questions were written before tuning MIN_COSINE, but the threshold was then chosen on them, so this is a
development check rather than a held-out result. Run: python eval/ask_eval.py
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT.parent))

from sqlalchemy import select  # noqa: E402

from baton import ask as ask_module  # noqa: E402
from baton import review  # noqa: E402
from baton.db import Member, make_engine, make_sessionmaker  # noqa: E402
from baton.demo.loader import load_demo  # noqa: E402
from baton.extract import ReplayExtractor  # noqa: E402
from baton.pipeline import run_pipeline  # noqa: E402
from baton.verify.models import get_embedder  # noqa: E402
from eval.evaluate import GOLD, simulate_review  # noqa: E402

ANSWERABLE = [
    ("Who renews the domain?", r"domain"),
    ("When is the domain renewal due?", r"domain"),
    ("Why did we move away from Firebase?", r"firebase"),
    ("Do we still need Firebase for anything?", r"firebase|fcm"),
    ("Why are we using QR codes for attendance?", r"qr"),
    ("Are the database backups working?", r"backup"),
    ("Who owns the reports dashboard?", r"report|dashboard"),
    ("When is the SSO login due?", r"sso|cas|10 jan"),
    ("What is blocking the push notifications PR?", r"#42|push|batch"),
    ("What happens to Supabase over the December break?", r"paus|inactiv"),
    ("Who is the contact at college IT?", r"vikram"),
    ("Where is the Android signing key?", r"signing key"),
    ("Why does QR scanning fail on iPhones?", r"safari|ios|iphone"),
    ("When is the feature freeze?", r"freeze"),
    ("When is the sponsor logo due?", r"logo"),
    ("Who is working on the Hackfest poster?", r"hackfest|poster"),
    ("Is the login redirect bug fixed?", r"login redirect"),
    ("When does Arjun leave?", r"15 nov|leav"),
]
UNANSWERABLE = [
    "What is the capital of France?",
    "How much does the sponsor pay?",
    "What is the admin password?",
    "Which payment gateway did we choose?",
    "Who is the club treasurer's mother?",
    "What was the budget for the fest food stalls?",
    "How many users signed up last week?",
    "Does the app support dark mode?",
    "What is the team's favourite restaurant?",
    "Is Kabir having surgery?",
]


def main() -> None:
    import re

    Session = make_sessionmaker(make_engine("sqlite:///:memory:"))
    embedder = get_embedder()
    with Session() as s:
        p = load_demo(s)
        s.commit()
        run_pipeline(s, p, ReplayExtractor())
        s.commit()
        simulate_review(s, p)
        s.commit()
        snapshot = review.latest_version(s, p.id).snapshot
        meera = s.scalars(select(Member).where(Member.project_id == p.id, Member.name == "Meera Iyer")).one()
        viewer = SimpleNamespace(role=meera.role, clearances=meera.clearances)

    print(f"Threshold {ask_module.MIN_COSINE}")
    right = 0
    for q, pattern in ANSWERABLE:
        a = ask_module.ask(q, snapshot, viewer, embedder)
        ok = not a.abstained and any(re.search(pattern, f"{i['subject']} {i['statement']}", re.I) for i in a.items)
        right += ok
        if not ok:
            print("  missed:", q, "->", "abstained" if a.abstained else a.items[0]["statement"][:80])
    print(f"Answerable, found the right claim: {right}/{len(ANSWERABLE)}")
    held = 0
    secrets = list(GOLD["canaries_for_incoming_tech_lead"].values())
    leaks = 0
    for q in UNANSWERABLE:
        a = ask_module.ask(q, snapshot, viewer, embedder)
        held += a.abstained
        if not a.abstained:
            print("  answered:", q, "->", a.items[0]["statement"][:80])
        text = " ".join(f"{i['statement']} {[r['quote'] for r in i['receipts']]}" for i in a.items)
        leaks += sum(1 for x in secrets if x.lower() in text.lower())
    print(f"Unanswerable, abstained: {held}/{len(UNANSWERABLE)}; canary strings in any answer: {leaks}")


if __name__ == "__main__":
    main()
