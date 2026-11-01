"""Re-measures the facet check on held-out claim pairs from another domain (a food-bank project).

The Round 1 plan published: on 60 new pairs, the facet check dropped none of the 26 conflicts NLI found.
Each pair was written by an LLM with an intended kind. Pairs NLI doesn't flag never reach the facet check.
Run: python eval/heldout_facet.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT.parent))

from baton.verify.conflicts import CONTRADICTION_THRESHOLD, ConflictClaim, _text, facet_check  # noqa: E402
from baton.verify.models import get_nli  # noqa: E402


def claim(i: int, d: dict) -> ConflictClaim:
    return ConflictClaim(i, d.get("type", "status"), d.get("subject", ""), d.get("statement", ""), str(d.get("value", "")), None)


def main() -> None:
    data = json.loads((ROOT / "heldout" / "heldout_facet_pairs.json").read_text("utf-8"))
    nli = get_nli()
    print(f"{'kind':<32}{'pairs':>6}{'NLI flags':>11}{'facet keeps':>13}")
    totals = {}
    for kind, pairs in data.items():
        a = [claim(1, p["a"]) for p in pairs]
        b = [claim(2, p["b"]) for p in pairs]
        forward = nli.score([(_text(x), _text(y)) for x, y in zip(a, b)])
        backward = nli.score([(_text(y), _text(x)) for x, y in zip(a, b)])
        flagged = kept = 0
        lost = []
        for x, y, f, r in zip(a, b, forward, backward):
            if max(f.contradiction, r.contradiction) >= CONTRADICTION_THRESHOLD:
                flagged += 1
                if facet_check(x, y):
                    kept += 1
                else:
                    lost.append((x.statement[:60], y.statement[:60]))
        totals[kind] = (len(pairs), flagged, kept, lost)
        print(f"{kind:<32}{len(pairs):>6}{flagged:>11}{kept:>13}")
    for kind, (_, _, _, lost) in totals.items():
        for pair in lost[:6]:
            print(f"  dropped [{kind}]: {pair}")


if __name__ == "__main__":
    main()
