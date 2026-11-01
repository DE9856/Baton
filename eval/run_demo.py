"""Runs the live pipeline on the demo project and prints what it found. Usage: python eval/run_demo.py [--dump]"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT.parent))

from sqlalchemy import select  # noqa: E402

from baton.db import Claim, Conflict, GapQuestion, make_engine, make_sessionmaker  # noqa: E402
from baton.demo.loader import load_demo  # noqa: E402
from baton.extract import ReplayExtractor  # noqa: E402
from baton.pipeline import run_pipeline  # noqa: E402


def main() -> None:
    Session = make_sessionmaker(make_engine("sqlite:///:memory:"))
    with Session() as s:
        p = load_demo(s)
        s.commit()
        stats = run_pipeline(s, p, ReplayExtractor())
        s.commit()
        print(json.dumps(stats, indent=1, default=str))
        claims = {c.id: c for c in s.scalars(select(Claim)).all()}
        for cf in s.scalars(select(Conflict)).all():
            print("\nCONFLICT", cf.facet, cf.nli, "|", cf.subject)
            for side, ids in (("older", cf.side_a), ("newer", cf.side_b)):
                for i in ids:
                    print("  ", side, claims[i].code, claims[i].statement[:110])
        for route in ("leaver", "reviewer", "triage"):
            print(f"\nROUTE {route}")
            for q in s.scalars(select(GapQuestion).where(GapQuestion.route == route)).all():
                print(f"   [{q.kind}] {q.text[:150]}")
        if "--dump" in sys.argv:
            for c in claims.values():
                e = c.evidence[0] if c.evidence else None
                print(c.code, c.label, c.bucket, "cliff" if c.cliff else "", c.due or "", c.sens_tags or "", e.entailment if e else "",
                      e.guard_missing if e else "", "|", c.statement[:90])


if __name__ == "__main__":
    main()
