"""The permission model (Round 1 plan, section 6.4). Every read of claims or evidence goes through this module.

visible(claim, viewer) = the claim's audience is among the viewer's clearances
                         AND (the viewer is the reviewer OR the claim is not sensitive).

Layer 1 is the SQL filter in `visible_claims`. Layer 2 re-checks every row in Python, so a mistake in either one
alone can't expose a claim. On Postgres, Row-Level Security adds a further layer (`db/supabase_schema.sql`).
"""
from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from .db import Claim, ClaimEvidence, EvidenceUnit, Member

AUDIENCE_LABELS = {"public": "Public", "tech-team": "Tech team", "core-committee": "Core committee"}


def is_reviewer(viewer: Member | None) -> bool:
    return bool(viewer and viewer.role == "reviewer")


def visible(claim: Claim, viewer: Member | None) -> bool:
    if viewer is None or claim.audience not in (viewer.clearances or []):
        return False
    return is_reviewer(viewer) or not claim.sensitive


def visible_claims(s: Session, project_id: int, viewer: Member | None, *criteria) -> list[Claim]:
    if viewer is None:
        return []
    stmt = (
        select(Claim)
        .where(Claim.project_id == project_id, Claim.audience.in_(viewer.clearances or []), *criteria)
        .options(selectinload(Claim.evidence).selectinload(ClaimEvidence.unit))
        .order_by(Claim.id)
    )
    if not is_reviewer(viewer):
        stmt = stmt.where(or_(Claim.sensitive.is_(False)))
    return [c for c in s.scalars(stmt).all() if visible(c, viewer)]


def unit_visible(unit: EvidenceUnit, viewer: Member | None) -> bool:
    if viewer is None or unit.source.audience not in (viewer.clearances or []):
        return False
    return is_reviewer(viewer) or not unit.sensitivity
