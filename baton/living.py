"""Living handover: re-sync a source, find what is new, and leave everything unchanged alone."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .db import Claim, ClaimEvidence, EvidenceUnit, Project, Source, audit
from .handover import item_visible
from .parsers import ParseResult
from .pipeline import merge_roster
from .verify.grounding import normalise
from .verify.sensitive import classify, mask_secrets


@dataclass
class SyncResult:
    new_unit_ids: set[int]
    unchanged: int
    retired: int
    reopened_claims: int


def _key(author: str, ts, text: str) -> tuple:
    return (author, ts.isoformat() if ts else "", normalise(text))


def sync_source(s: Session, project: Project, source: Source, parsed: ParseResult, actor: str) -> SyncResult:
    """Match records by author, time and text, so nothing already read is read again."""
    existing = s.scalars(select(EvidenceUnit).where(EvidenceUnit.source_id == source.id)).all()
    active = {_key(u.author, u.ts, u.text): u for u in existing if not u.retired}
    used_uids = {u.uid for u in existing}
    seq = (s.scalar(select(func.max(EvidenceUnit.seq)).where(EvidenceUnit.source_id == source.id)) or 0) + 1
    sequential = source.type == "whatsapp"
    next_number = 1 + max((int(m.group(1)) for u in existing if (m := re.search(r"-(\d+)$", u.uid))), default=0)

    seen: set[tuple] = set()
    new_units: list[EvidenceUnit] = []
    for unit in parsed.units:
        masked, _ = mask_secrets(unit.text)
        key = _key(unit.author, unit.ts, masked)
        if key in seen:
            continue
        seen.add(key)
        if key in active:
            continue
        if sequential:
            uid = f"{source.code}-{next_number:04d}"
            next_number += 1
        else:
            uid = unit.uid
            revision = 2
            while uid in used_uids:  # the record was edited: the new text gets its own citable id
                uid = f"{unit.uid}r{revision}"
                revision += 1
        used_uids.add(uid)
        row = EvidenceUnit(project_id=project.id, source_id=source.id, uid=uid, seq=seq, author=unit.author, ts=unit.ts, text=masked,
                           link=unit.link, kind=unit.kind, sensitivity=classify(masked))
        seq += 1
        s.add(row)
        new_units.append(row)

    retired = [u for key, u in active.items() if key not in seen]
    reopened = 0
    for u in retired:
        u.retired = True
        claims = s.scalars(select(Claim).join(ClaimEvidence).where(ClaimEvidence.unit_id == u.id, Claim.origin.in_(("extracted", "guard")))).unique().all()
        for c in claims:
            c.label, c.label_reason = "needs_review", "The source record this cites has changed or been removed."
            c.bucket, c.review_state = "needs_review", "pending"
            reopened += 1
    merge_roster(s, project, parsed)
    source.last_synced_at = datetime.now()
    source.skipped = [vars(x) for x in parsed.skipped]
    s.flush()
    audit(s, project.id, actor, "source_synced", source.title,
          f"{len(new_units)} new, {len(retired)} removed or changed, {len(active) - len(retired)} unchanged")
    return SyncResult({u.id for u in new_units}, len(active) - len(retired), len(retired), reopened)


def changes_for_viewer(previous, current, viewer) -> dict:
    """What changed between two versions, as this viewer may see it. The stored diff is reviewer-only."""
    def lines(version) -> dict[str, dict]:
        return {c["statement"]: c for c in version.snapshot["claims"] if item_visible(c, viewer) and c["label"] in ("verified", "confirmed")}

    before, after = lines(previous), lines(current)
    return {"added": [after[k] for k in after if k not in before], "removed": [before[k] for k in before if k not in after]}
