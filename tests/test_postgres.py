"""Row-Level Security on real Postgres. Needs BATON_TEST_POSTGRES_URL, for example a throwaway Docker container."""
from __future__ import annotations

import os

import pytest
from sqlalchemy import select, text

from baton import permissions, review
from baton.db import AuditEvent, Claim, ClaimEvidence, Conflict, EvidenceUnit, GapQuestion, Member, Source, Version, make_engine, make_sessionmaker
from baton.demo.loader import load_demo
from baton.extract import ReplayExtractor
from baton.pipeline import run_pipeline

from .conftest import WordOverlapNLI, fresh_db_url

pytestmark = [pytest.mark.postgres,
              pytest.mark.skipif(not os.environ.get("BATON_TEST_POSTGRES_URL"), reason="BATON_TEST_POSTGRES_URL is not set")]


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    engine = make_engine(fresh_db_url(tmp_path_factory.mktemp("pg")))
    Session = make_sessionmaker(engine)
    with Session() as s:
        p = load_demo(s)
        s.commit()
        run_pipeline(s, p, ReplayExtractor(), nli=WordOverlapNLI(), embedder=False)
        s.commit()
        reviewer = s.get(Member, p.reviewer_id)
        review.approve_one_pass(s, p, reviewer)
        for cf in s.scalars(select(Conflict).where(Conflict.project_id == p.id)).all():
            review.resolve_conflict(s, cf, reviewer, "b")
        for c in s.scalars(select(Claim).where(Claim.project_id == p.id, Claim.review_state == "pending", Claim.bucket == "decide")).all():
            review.decide_claim(s, c, reviewer, "reject" if c.label == "withheld" else "approve")
        review.approve_version(s, p, reviewer)
        s.commit()
        pid = p.id
    yield Session, pid
    engine.dispose()


def member(s, pid, name):
    return s.scalars(select(Member).where(Member.project_id == pid, Member.name == name)).one()


def raw_claims(s, viewer):
    """What the database alone returns for this viewer, with no filter from the app."""
    with permissions.rls_scope(s, viewer):
        return s.scalars(select(Claim)).all()


def test_rls_is_on(db):
    Session, _ = db
    with Session() as s:
        assert permissions.rls_active(s)
        n = s.execute(text("select count(*) from pg_policies where policyname like 'baton_%'")).scalar()
        assert n >= 14


def test_database_alone_hides_restricted_claims(db):
    Session, pid = db
    with Session() as s:
        meera = raw_claims(s, member(s, pid, "Meera Iyer"))
        s.rollback()
        sana = raw_claims(s, member(s, pid, "Sana"))
        s.rollback()
        new = raw_claims(s, member(s, pid, "New member"))
        s.rollback()
        assert meera and all(c.audience in ("public", "tech-team") and not c.sensitive for c in meera)
        assert any(c.audience == "core-committee" for c in sana) and any(c.sensitive for c in sana)
        assert new and all(c.audience == "public" for c in new)
        assert len(new) < len(meera) < len(sana)


def test_app_layer_bug_cannot_leak_on_postgres(db, monkeypatch):
    """If the Python and SQL filters both broke, the database must still refuse."""
    Session, pid = db
    monkeypatch.setattr(permissions, "visible", lambda claim, viewer: True)
    with Session() as s:
        meera = member(s, pid, "Meera Iyer")
        widened = type("V", (), {"user_id": meera.user_id, "clearances": ["public", "tech-team", "core-committee"], "role": "reviewer"})()
        rows = permissions.visible_claims(s, pid, widened)
        assert rows and all(c.audience != "core-committee" and not c.sensitive for c in rows)


def test_other_tables_are_scoped(db):
    Session, pid = db
    with Session() as s:
        meera = member(s, pid, "Meera Iyer")
        with permissions.rls_scope(s, meera):
            sources = s.scalars(select(Source.code)).all()
            versions = s.scalars(select(Version.id)).all()
            audit = s.scalars(select(AuditEvent.id)).all()
            conflicts = s.scalars(select(Conflict.id)).all()
            units = s.scalars(select(EvidenceUnit.sensitivity)).all()
            receipts = s.scalars(select(ClaimEvidence.id)).all()
            questions = s.scalars(select(GapQuestion.asked_to_id)).all()
        assert "N3" not in sources and "WA" in sources
        assert not versions and not audit and not conflicts   # snapshots and the log are reviewer-only
        assert units and all(not u for u in units)
        assert receipts
        assert all(q == meera.id for q in questions)


def test_handover_function_filters_inside_the_database(db):
    Session, pid = db
    with Session() as s:
        version = review.latest_version(s, pid)
        full = version.snapshot
        meera = permissions.snapshot_for(s, version, member(s, pid, "Meera Iyer")).snapshot
        new = permissions.snapshot_for(s, version, member(s, pid, "New member")).snapshot
        s.rollback()
        assert any(c["audience"] == "core-committee" for c in full["claims"])
        assert meera["claims"] and all(c["audience"] != "core-committee" and not c["sensitive"] for c in meera["claims"])
        assert new["claims"] and all(c["audience"] == "public" for c in new["claims"])
        assert meera["conflicts"] and not new["conflicts"]
        assert all(q["audience"] == "public" for q in new["missing"])


def test_nobody_signed_in_and_strangers_see_nothing(db):
    Session, pid = db
    with Session() as s:
        s.execute(text("select set_config('request.jwt.claims', '{}', true)"))
        s.execute(text("set local role authenticated"))
        assert not s.scalars(select(Claim)).all()
        s.rollback()
        stranger = type("V", (), {"user_id": "00000000-0000-0000-0000-00000000dead"})()
        with permissions.rls_scope(s, stranger):
            assert not s.scalars(select(Claim)).all()
            assert s.execute(text("select baton_handover_for(:p, 1)"), {"p": pid}).scalar() is None
        s.rollback()


def test_viewers_cannot_write_directly(db):
    Session, pid = db
    with Session() as s:
        with permissions.rls_scope(s, member(s, pid, "Sana")):
            with pytest.raises(Exception, match="permission denied"):
                s.execute(text("update claims set label = 'verified'"))
        s.rollback()


def test_versions_and_audit_are_immutable_in_the_database(db):
    Session, pid = db
    for sql in ("update versions set approved_by = 'x'", "delete from audit_events"):
        with Session() as s:
            with pytest.raises(Exception, match="append-only"):
                s.execute(text(sql))
            s.rollback()
