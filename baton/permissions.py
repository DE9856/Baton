"""The permission model (Round 1 plan, section 6.4). Every read of claims or evidence for a viewer goes through here.

visible(claim, viewer) = the claim's audience is among the viewer's clearances
                         AND (the viewer is the reviewer OR the claim is not sensitive).

Enforced in layers. On Postgres, the database decides which rows the viewer may read, under Row-Level Security with
the viewer's identity (rls.sql). A SQL filter applies either way, and every row is then re-checked in Python, so a
mistake in one layer alone can't expose a claim.
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from types import SimpleNamespace

from sqlalchemy import or_, select, text
from sqlalchemy.orm import Session, selectinload

from .db import Claim, ClaimEvidence, EvidenceUnit, Member

AUDIENCE_LABELS = {"public": "Public", "tech-team": "Tech team", "core-committee": "Core committee"}


def is_reviewer(viewer: Member | None) -> bool:
    return bool(viewer and viewer.role == "reviewer")


def visible(claim: Claim, viewer: Member | None) -> bool:
    if viewer is None or claim.audience not in (viewer.clearances or []):
        return False
    return is_reviewer(viewer) or not claim.sensitive


def rls_active(s: Session) -> bool:
    bind = s.get_bind()
    return bind.dialect.name == "postgresql" and getattr(bind, "baton_rls", False)


@contextmanager
def rls_scope(s: Session, viewer: Member):
    """Runs reads as the viewer: Postgres applies the policies in rls.sql. Does nothing on SQLite."""
    if not rls_active(s):
        yield
        return
    # auth.uid() reads one or the other, depending on the Supabase version. Unset, it finds nobody and every read comes back empty.
    s.execute(text("select set_config('request.jwt.claims', :claims, true), set_config('request.jwt.claim.sub', :sub, true)"),
              {"claims": json.dumps({"sub": viewer.user_id, "role": "authenticated"}), "sub": viewer.user_id})
    s.execute(text("set local role authenticated"))
    yield  # after an error the transaction is rolled back, which ends the role change too
    s.execute(text("reset role"))


def visible_claims(s: Session, project_id: int, viewer: Member | None, *criteria) -> list[Claim]:
    if viewer is None:
        return []
    # The database picks the rows as this viewer. Receipts load afterwards: a claim the viewer may see can quote
    # a record whose other, sensitive text they may not, and only the quote is ever shown.
    with rls_scope(s, viewer):
        stmt = select(Claim.id).where(Claim.project_id == project_id, Claim.audience.in_(viewer.clearances or []), *criteria)
        if not is_reviewer(viewer):
            stmt = stmt.where(or_(Claim.sensitive.is_(False)))
        ids = list(s.scalars(stmt).all())
    if not ids:
        return []
    rows = s.scalars(select(Claim).where(Claim.id.in_(ids)).options(selectinload(Claim.evidence).selectinload(ClaimEvidence.unit))
                     .order_by(Claim.id)).all()
    return [c for c in rows if visible(c, viewer)]


def unit_visible(unit: EvidenceUnit, viewer: Member | None) -> bool:
    if viewer is None or unit.source.audience not in (viewer.clearances or []):
        return False
    return is_reviewer(viewer) or not unit.sensitivity


def readable_unit_ids(s: Session, source_id: int, viewer: Member) -> set[int]:
    """The records the database lets this viewer read in full."""
    with rls_scope(s, viewer):
        return set(s.scalars(select(EvidenceUnit.id).where(EvidenceUnit.source_id == source_id)).all())


def snapshot_for(s: Session, version, viewer: Member | None):
    """An approved version as this viewer may read it. On Postgres the database filters it first."""
    if viewer is None or not rls_active(s):
        return version
    with rls_scope(s, viewer):
        snapshot = s.execute(text("select baton_handover_for(:p, :n)"), {"p": version.project_id, "n": version.number}).scalar()
    if isinstance(snapshot, str):
        snapshot = json.loads(snapshot)
    return SimpleNamespace(number=version.number, approved_by=version.approved_by, approved_at=version.approved_at,
                           project_id=version.project_id, snapshot=snapshot or {**version.snapshot, "claims": [], "missing": [], "conflicts": []})
