"""Re-measures the support check (NLI + value guard) on the planning round's held-out statements.

Run: python eval/heldout_support.py
The Round 1 plan published: NLI alone 92/102, NLI + guard 99/102. The statements were written by an LLM.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT.parent))

from baton.verify.models import get_nli  # noqa: E402
from baton.verify.value_guard import check_values  # noqa: E402


def load_pairs():
    pairs = []
    for name in ("heldout_support_pairs", "heldout_support_pairs_batch2"):
        pairs += json.loads((ROOT / "heldout" / f"{name}.json").read_text("utf-8"))
    return pairs


def main() -> None:
    nli = get_nli()
    rows = []
    for p in load_pairs():
        rows.append((p["record"], p["faithful"], True, p["kind"]))
        rows.append((p["record"], p["distorted"], False, p["kind"]))
    scores = nli.score([(r[0], r[1]) for r in rows])
    n_only = both = 0
    errors = []
    for (record, statement, expected, kind), s in zip(rows, scores):
        entailed = s.entailment >= 0.5
        guard = check_values(statement, record).ok
        n_only += entailed == expected
        ok = (entailed and guard) == expected
        both += ok
        if not ok:
            errors.append((expected, round(s.entailment, 2), guard, kind, statement[:110]))
    print(f"statements: {len(rows)}")
    print(f"NLI alone: {n_only}/{len(rows)}")
    print(f"NLI + value guard: {both}/{len(rows)}")
    for e in errors:
        print("  error:", e)


if __name__ == "__main__":
    main()
