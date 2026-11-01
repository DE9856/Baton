"""Round 3 features with the real NLI, embedding and speech models."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from baton.db import Claim, Conflict, GapQuestion
from baton.web.app import create_app

from .conftest import CANARIES, demo_project_id, fresh_db_url, view_as
from .test_workflow import approve_everything

pytestmark = pytest.mark.model


@pytest.fixture(scope="module")
def client(tmp_path_factory, real_nli, real_embedder):
    app = create_app(fresh_db_url(tmp_path_factory.mktemp("r3")), nli=real_nli, embedder=real_embedder, inline_jobs=True)
    with TestClient(app) as c:
        pid = demo_project_id(c)
        assert "Approved version 1" in approve_everything(c, pid).text
        yield c, pid


def ask(c, pid, q):
    return c.get(f"/p/{pid}/ask", params={"q": q}).text


def test_ask_cites_and_abstains_per_viewer(client):
    c, pid = client
    view_as(c, pid, "Meera Iyer")
    t = ask(c, pid, "When is the domain renewal due?")
    assert "2026-11-20" in t and "What the approved handover says" in t and "TR-c5" in t and "object at 0x" not in t
    t = ask(c, pid, "What is the capital of France?")
    assert "Not known" in t and "Send this question" in t
    view_as(c, pid, "New member")
    assert "Not known" in ask(c, pid, "When is the domain renewal due?")   # exists, but this person isn't cleared for it


def test_ask_never_shows_a_canary(client):
    c, pid = client
    questions = ["What is the admin password?", "What is the Google Maps API key?", "How much does TechNova pay?", "What did the member misuse?",
                 "What is Arjun's phone number?", "Why is Kabir away?", "What is the sponsor's phone number?", "maps key", "password", "surgery"]
    for person in ("Meera Iyer", "New member", "Riya Thomas"):
        view_as(c, pid, person)
        for q in questions:
            body = ask(c, pid, q).replace(q, "")  # the page repeats what was typed; that isn't a leak
            for name, value in CANARIES.items():
                assert value.lower() not in body.lower(), (person, q, name)


def test_unanswered_question_is_routed(client):
    c, pid = client
    view_as(c, pid, "Meera Iyer")
    c.post(f"/p/{pid}/ask/route", data={"q": "Which payment gateway did we choose?"})
    with c.app.state.Sessions() as s:
        q = s.scalars(select(GapQuestion).where(GapQuestion.project_id == pid, GapQuestion.text.like("%payment gateway%"))).one()
        assert q.status == "open" and q.route in ("leaver", "reviewer")


def test_new_activity_is_read_incrementally_and_conflicts_with_approved(client):
    c, pid = client
    view_as(c, pid, "Sana")
    with c.app.state.Sessions() as s:
        before = {x.id: x.review_state for x in s.scalars(select(Claim).where(Claim.project_id == pid)).all()}
    t = c.post(f"/p/{pid}/simulate-update").text
    assert "4 new record(s), 45 unchanged" in t
    with c.app.state.Sessions() as s:
        claims = s.scalars(select(Claim).where(Claim.project_id == pid)).all()
        new = [x for x in claims if x.id not in before]
        assert new and all(x.review_state == "pending" for x in new)
        # Unrelated earlier decisions keep their state.
        changed = [x for x in claims if x.id in before and x.review_state != before[x.id]]
        conflicts = s.scalars(select(Conflict).where(Conflict.project_id == pid, Conflict.resolution == "")).all()
        assert len(conflicts) >= 1
        statements = " ".join(s.get(Claim, i).statement for cf in conflicts for i in cf.claim_ids)
        assert "5 Dec" in statements and "2026-11-20" in statements
        assert all(x.label == "conflict" for x in changed)
        assert len(changed) <= 6
        # The new credential became a task, masked.
        assert any(x.origin == "guard" and x.id not in before for x in claims)
    # The approved version is untouched until a person approves a new one.
    view_as(c, pid, "Meera Iyer")
    assert "now due 5 Dec" not in c.get(f"/p/{pid}/handover").text


def test_staging_password_from_the_update_is_never_stored(client):
    c, _ = client
    engine = c.app.state.Sessions().get_bind()
    with engine.connect() as conn:
        for table in ("evidence_units", "claims", "claim_evidence", "gap_questions", "audit_events"):
            for row in conn.execute(text(f"select * from {table}")):
                assert "Staging#2026x" not in str(tuple(row)), table


def test_second_version_shows_what_changed_to_the_successor(client):
    c, pid = client
    assert "Approved version 2" in approve_everything(c, pid).text
    view_as(c, pid, "Meera Iyer")
    t = c.get(f"/p/{pid}/handover").text
    assert "What changed in version 2" in t and "now due 5 Dec" in t
    view_as(c, pid, "New member")
    t = c.get(f"/p/{pid}/handover").text
    assert "now due 5 Dec" not in t  # the change came from a source this person can't see


def test_real_speech_is_transcribed():
    from pathlib import Path

    from baton.voice import get_transcriber, transcribe_bytes

    sample = Path(__file__).parent / "data" / "speech_sample.wav"
    tool = get_transcriber()
    assert tool is not None
    text_out = transcribe_bytes(sample.read_bytes(), tool).lower()
    assert "render" in text_out and "main" in text_out, text_out
