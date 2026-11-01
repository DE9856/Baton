"""The gold evaluation: runs the live product logic and scores what each viewer actually receives.

A simulated reviewer approves routine claims, picks the newer side of each conflict and approves non-withheld risky
items. Nobody answers the gap interview here, so deployment knowledge shows up as Missing.

Scoring: a fact counts as delivered if all its patterns co-occur on one line of the viewer's exported handover.
The answer key and patterns were written during the planning round, before this product existed, and apply equally
to any system. Usage: python eval/evaluate.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT.parent))

from sqlalchemy import select  # noqa: E402

from baton import review  # noqa: E402
from baton.db import Claim, Conflict, Member, make_engine, make_sessionmaker  # noqa: E402
from baton.demo.loader import load_demo  # noqa: E402
from baton.extract import ReplayExtractor  # noqa: E402
from baton.handover import compose, to_markdown  # noqa: E402
from baton.pipeline import run_pipeline  # noqa: E402

GOLD = json.loads((ROOT / "gold_campusconnect.json").read_text("utf-8"))

# The same 23 facts and patterns as the planning round: F9 is scored as conflict CF1, and F24 is included.
FACTS = {
    "F1": [r"supabase", r"firebase", r"mov|migrat|switch"], "F2": [r"\bqr\b", r"\bnfc\b"],
    "F3": [r"\bsso\b|\bcas\b", r"google"], "F4": [r"fcm|cloud messaging", r"push"],
    "F5": [r"freez", r"1 dec|exam"], "F6": [r"kabir", r"supabase|migration"], "F7": [r"meera", r"report|dashboard"],
    "F8": [r"riya", r"poster|ui kit"],
    "F10": [r"domain", r"20 nov|2026-11-20|november 20|20 november"],
    "F11": [r"logo|splash", r"30 nov|2026-11-30|november 30|30 november"],
    "F12": [r"#42|pr 42|push notification", r"rate limit|batch|blocked"], "F13": [r"ios|safari", r"\bqr\b|scan"],
    "F14": [r"backup", r"never|not enabled|no backups|not (been )?(properly )?(configured|enabled)"],
    "F15": [r"paus", r"inactiv"], "F16": [r"signing key", r"laptop|arjun"],
    "F17": [r"audit|credential", r"\bcas\b|\bsso\b|vikram"], "F18": [r"retention|attendance data after|keep attendance data"],
    "F19": [r"vikram"], "F20": [r"login redirect", r"fixed|#33|closed|outdated|stale|possibly"],
    "F21": [r"credential|api key|maps key", r"rotate|password manager"],
    "F22": [r"deploy", r"manual|undocumented|not documented|write it down|how .* deploy|steps"],
    "F23": [r"15 nov|november 15|2026-11-15|leaving|leaves", r"meera|tech lead"],
    "F24": [r"kabir", r"3 nov|17 nov|unavailable|offline|out from"],
}
CONFLICTS = {
    "CF1": [r"\bsso\b|\bcas\b", r"15 dec|december 15|2026-12-15", r"10 jan|january 10|-01-10"],
    "CF2": [r"backup", r"done|board|trello|configured", r"never|not enabled|no backups"],
    "CF3": [r"login redirect", r"in progress|board|trello", r"fixed|closed|#33"],
}


def hit(lines: list[str], patterns: list[str]) -> bool:
    return any(all(re.search(p, line, re.I) for p in patterns) for line in lines)


def score(text: str) -> dict:
    lines = [x for x in text.split("\n") if x.strip()]
    facts = [f for f, p in FACTS.items() if hit(lines, p)]
    conflicts = [c for c, p in CONFLICTS.items() if hit(lines, p)]
    leaks = [k for k, v in GOLD["canaries_for_incoming_tech_lead"].items() if v.lower() in text.lower()]
    citations = re.findall(r"\[((?:WA|GH|TR|N\d)-[\w]+)\]", text)
    return {"facts": len(facts), "facts_total": len(FACTS), "missed": sorted(set(FACTS) - set(facts), key=lambda x: int(x[1:])),
            "conflicts": conflicts, "leaks": leaks, "citations": len(citations)}


def simulate_review(s, project) -> None:
    reviewer = s.get(Member, project.reviewer_id)
    review.approve_one_pass(s, project, reviewer)
    for cf in s.scalars(select(Conflict).where(Conflict.project_id == project.id)).all():
        review.resolve_conflict(s, cf, reviewer, "b", "Newer source is correct.")
    for c in s.scalars(select(Claim).where(Claim.project_id == project.id, Claim.review_state == "pending", Claim.bucket == "decide")).all():
        if c.label == "withheld":
            review.decide_claim(s, c, reviewer, "reject")
        else:
            review.decide_claim(s, c, reviewer, "approve")
    review.approve_version(s, project, reviewer)
    s.flush()


def run(database_url: str = "sqlite:///:memory:", extractor=None) -> dict:
    Session = make_sessionmaker(make_engine(database_url))
    with Session() as s:
        p = load_demo(s)
        s.commit()
        stats = run_pipeline(s, p, extractor or ReplayExtractor())
        s.commit()
        simulate_review(s, p)
        s.commit()
        v = review.latest_version(s, p.id)
        out = {"pipeline": stats, "views": {}}
        for m in s.scalars(select(Member).where(Member.project_id == p.id)).all():
            md = to_markdown(compose(v, m), p.name, m)
            out["views"][m.name] = {**score(md), "role": m.role, "text": md}
        return out


def main() -> None:
    from baton.extract import RuleExtractor

    result = run(extractor=RuleExtractor() if "--rules" in sys.argv else None)
    print("Pipeline:", json.dumps({k: v for k, v in result["pipeline"].items() if k != "extraction_errors"}, default=str))
    for name, view in result["views"].items():
        print(f"\n{name} ({view['role']}): facts {view['facts']}/{view['facts_total']}, missed {view['missed']}, "
              f"conflicts {view['conflicts']}, canary leaks {view['leaks']}, citations {view['citations']}")
    if "--show" in sys.argv:
        print(result["views"]["Meera Iyer"]["text"])


if __name__ == "__main__":
    main()
