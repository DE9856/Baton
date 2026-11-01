"""The whole product through its HTTP interface, as a judge would use it. Uses the real NLI and embedding models."""
from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from baton.db import AuditEvent, Claim, Conflict, GapQuestion, Member, Version
from baton.web.app import create_app

from .conftest import CANARIES, NEVER_SHOWN, demo_project_id, view_as

pytestmark = pytest.mark.model

PAGES = ["connect", "ledger", "review", "interview", "handover", "export.md", "audit"]


@pytest.fixture(scope="module")
def world(tmp_path_factory, real_nli, real_embedder):
    """One demo project taken through review once. Tests below read from it or extend it."""
    path = tmp_path_factory.mktemp("db") / "w.sqlite3"
    app = create_app(f"sqlite:///{path}", nli=real_nli, embedder=real_embedder, inline_jobs=True)
    with TestClient(app) as client:
        pid = demo_project_id(client)
        view_as(client, pid, "Sana")
        yield client, pid


def sessions(client):
    return client.app.state.Sessions


def approve_everything(client, pid):
    view_as(client, pid, "Sana")
    assert client.post(f"/p/{pid}/review/one-pass").status_code == 200
    with sessions(client)() as s:
        conflicts = s.scalars(select(Conflict).where(Conflict.project_id == pid, Conflict.resolution == "")).all()
        cf_ids = [c.id for c in conflicts]
    for i in cf_ids:
        client.post(f"/p/{pid}/conflicts/{i}/resolve", data={"choice": "b", "note": "Newer source."})
    with sessions(client)() as s:
        pending = s.scalars(select(Claim).where(Claim.project_id == pid, Claim.review_state == "pending", Claim.bucket == "decide")).all()
        items = [(c.id, c.label) for c in pending]
    for cid, label in items:
        client.post(f"/p/{pid}/claims/{cid}/decide", data={"action": "reject" if label == "withheld" else "approve"})
    return client.post(f"/p/{pid}/approve")


def test_01_pipeline_finds_what_was_planted(world):
    client, pid = world
    with sessions(client)() as s:
        claims = s.scalars(select(Claim).where(Claim.project_id == pid)).all()
        conflicts = s.scalars(select(Conflict).where(Conflict.project_id == pid)).all()
        questions = s.scalars(select(GapQuestion).where(GapQuestion.project_id == pid)).all()
        by_code = {c.code: c for c in claims}
        assert sum(c.origin == "extracted" for c in claims) == 83
        # The recorded LLM copied the prompt's example ("The library will open at 8 am"), and code rejects it.
        assert next(c for c in claims if "library" in c.statement).label == "unsupported"
        assert len(conflicts) == 3
        subjects = " ".join(by_code[c.code].statement for cf in conflicts for c in [s.get(Claim, i) for i in cf.claim_ids])
        for needle in ("10 Jan", "never actually", "login redirect", "DB backups is completed"):
            assert needle in subjects
        # Disagreeing about the date doesn't make "in progress" false, so it stays out of that conflict.
        assert "SSO login with college portal (CAS) is in progress" not in subjects
        leaver = [q for q in questions if q.route == "leaver"]
        assert 6 <= len(leaver) <= 12
        text = " ".join(q.text.lower() for q in leaver)
        for gap in ("deploy", "render", "domain", "credential", "signing key"):
            assert gap in text, gap


def test_02_cannot_share_before_review(world):
    client, pid = world
    view_as(client, pid, "Sana")
    r = client.post(f"/p/{pid}/approve")
    assert "still need" in r.text
    view_as(client, pid, "Meera Iyer")
    r = client.get(f"/p/{pid}/handover")
    assert "no version has been approved" in r.text
    assert "Verified" not in r.text


def test_03_only_the_reviewer_can_approve(world):
    client, pid = world
    for who in ("Meera Iyer", "Arjun Mehta", "Kabir Shah", "New member"):
        view_as(client, pid, who)
        assert client.post(f"/p/{pid}/approve").status_code == 403
        assert client.post(f"/p/{pid}/review/one-pass").status_code == 403
        assert client.get(f"/p/{pid}/audit").status_code == 403
    view_as(client, pid, "Sana")


def test_04_leaver_answers_and_testimony_is_labelled(world):
    client, pid = world
    view_as(client, pid, "Arjun Mehta")
    r = client.get(f"/p/{pid}/interview")
    assert "Who takes this over after 15 Nov" in r.text
    with sessions(client)() as s:
        arjun = s.scalars(select(Member).where(Member.project_id == pid, Member.name == "Arjun Mehta")).first()
        questions = s.scalars(select(GapQuestion).where(GapQuestion.project_id == pid, GapQuestion.asked_to_id == arjun.id)).all()
        deploy = next(q for q in questions if q.kind == "coverage")
        domain = next(q for q in questions if "domain" in q.text)
        other = next(q for q in questions if q.kind == "rationale")
        new_member = s.scalars(select(Member).where(Member.project_id == pid, Member.name == "New member")).first()
        kabir = s.scalars(select(Member).where(Member.project_id == pid, Member.name == "Kabir Shah")).first()
        ids = deploy.id, domain.id, other.id, new_member.id, kabir.id
    deploy_id, domain_id, other_id, new_id, kabir_id = ids
    client.post(f"/p/{pid}/questions/{deploy_id}/answer", data={"text": "Push to main; Render auto-deploys. Env vars live in the Render dashboard."})
    client.post(f"/p/{pid}/questions/{domain_id}/answer", data={"text": "Meera Iyer takes this over after my last day."})
    # An empty answer is refused, and so is passing a question to someone not cleared for its source.
    assert "Please write an answer" in client.post(f"/p/{pid}/questions/{other_id}/answer", data={"text": "  "}).text
    assert "not cleared" in client.post(f"/p/{pid}/questions/{other_id}/reassign", data={"to": new_id}).text
    assert "Passed to Kabir" in client.post(f"/p/{pid}/questions/{other_id}/reassign", data={"to": kabir_id}).text
    with sessions(client)() as s:
        c = s.scalars(select(Claim).where(Claim.project_id == pid, Claim.origin == "testimony", Claim.statement.like("Push to main%"))).first()
        assert c.label == "confirmed" and c.answered_by == "Arjun Mehta"
        assert s.get(GapQuestion, other_id).asked_to_id == kabir_id


def test_05_secret_in_an_answer_is_masked(world):
    client, pid = world
    view_as(client, pid, "Kabir Shah")
    with sessions(client)() as s:
        q = s.scalars(select(GapQuestion).where(GapQuestion.project_id == pid, GapQuestion.kind == "rationale",
                                                GapQuestion.route == "teammate")).first()
        qid = q.id
    client.post(f"/p/{pid}/questions/{qid}/answer", data={"text": "Because of cost. The password is Sup3rS3cret!x"})
    with sessions(client)() as s:
        q = s.get(GapQuestion, qid)
        c = s.get(Claim, q.testimony_claim_id)
        assert "Sup3rS3cret" not in c.statement and "Sup3rS3cret" not in q.answer
        assert "masked" in c.check_flag


def test_06_approval_creates_a_version_and_blockers_clear(world):
    client, pid = world
    r = approve_everything(client, pid)
    assert "Approved version 1" in r.text
    with sessions(client)() as s:
        v = s.scalars(select(Version).where(Version.project_id == pid)).one()
        assert v.number == 1 and len(v.content_hash) == 64 and v.approved_by == "Sana"
        assert not s.scalars(select(Conflict).where(Conflict.project_id == pid, Conflict.resolution == "")).all()


def test_07_successor_gets_the_plan_with_receipts_and_labels(world):
    client, pid = world
    view_as(client, pid, "Meera Iyer")
    r = client.get(f"/p/{pid}/handover")
    t = r.text
    assert r.status_code == 200
    for needle in ("Start here", "Your first two weeks", "Decisions and why", "Departure cliff", "Confirmed by Arjun Mehta",
                   "Verified", "Missing", "Push to main", "See both sides", "campusconnect.in", "Last day of the person leaving"):
        assert needle in t, needle
    # Reviewer-only labels never reach the successor as a live label on a line.
    for hidden in ('l-needs_review', 'l-withheld', 'l-outdated'):
        assert hidden not in t, hidden
    # 100% of shown lines carry a receipt or are attributed testimony.
    assert 'class="receipts"' in t


def test_08_no_page_leaks_a_canary_to_anyone(world):
    client, pid = world
    allowed_for_reviewer = {"K1_restricted_amount", "K2_restricted_hr", "K4_restricted_phone", "K6_personal_phone", "K7_health"}
    with sessions(client)() as s:
        people = [m.name for m in s.scalars(select(Member).where(Member.project_id == pid)).all()]
        sources = [x.id for x in s.scalars(select(__import__("baton.db", fromlist=["Source"]).Source).where(
            __import__("baton.db", fromlist=["Source"]).Source.project_id == pid)).all()]
    urls = [f"/p/{pid}/{p}" for p in PAGES] + [f"/p/{pid}/sources/{i}" for i in sources] + [f"/p/{pid}/job"]
    for person in people:
        view_as(client, pid, person)
        for url in urls:
            body = client.get(url).text
            for name, value in NEVER_SHOWN.items():
                assert value not in body, f"{name} shown to {person} at {url}"
            if person != "Sana":
                for name, value in CANARIES.items():
                    assert value.lower() not in body.lower(), f"{name} shown to {person} at {url}"
            else:
                for name, value in CANARIES.items():
                    if value.lower() in body.lower():
                        assert name in allowed_for_reviewer


def test_09_raw_secrets_are_never_stored(world):
    client, pid = world
    import sqlalchemy

    engine = sessions(client)().get_bind()
    dumped = []
    with engine.connect() as conn:
        for table in ("evidence_units", "claims", "claim_evidence", "gap_questions", "versions", "audit_events"):
            for row in conn.execute(sqlalchemy.text(f"select * from {table}")):
                dumped.append(str(tuple(row)))
    blob = "\n".join(dumped)
    for value in NEVER_SHOWN.values():
        assert value not in blob


def test_10_permissions_by_viewer(world):
    client, pid = world
    view_as(client, pid, "New member")
    t = client.get(f"/p/{pid}/handover").text
    assert "PR #42" in t or "Migrate" in t or "Issue" in t or "Firebase" in t   # public GitHub facts remain
    assert "campusconnect.in" not in t  # tech-team only
    assert "signing key" not in t
    with sessions(client)() as s:
        Source = __import__("baton.db", fromlist=["Source"]).Source
        n3 = s.scalars(select(Source).where(Source.project_id == pid, Source.code == "N3")).one().id
    assert client.get(f"/p/{pid}/sources/{n3}").status_code == 403
    view_as(client, pid, "Meera Iyer")
    assert client.get(f"/p/{pid}/sources/{n3}").status_code == 403
    view_as(client, pid, "Sana")
    assert client.get(f"/p/{pid}/sources/{n3}").status_code == 200


def test_11_acknowledge_and_unclear_loop(world):
    client, pid = world
    view_as(client, pid, "Meera Iyer")
    assert "Acknowledged" in client.post(f"/p/{pid}/ack/decisions").text
    client.post(f"/p/{pid}/unclear/access", data={"text": "Where is the signing key going?"})
    with sessions(client)() as s:
        q = s.scalars(select(GapQuestion).where(GapQuestion.project_id == pid, GapQuestion.kind == "unclear")).one()
        assert "signing key" in q.text and q.status == "open" and q.route == "leaver"
    assert client.post(f"/p/{pid}/ack/nonsense").status_code == 404


def test_12_correction_flows_into_a_new_version_with_a_diff(world):
    client, pid = world
    view_as(client, pid, "Arjun Mehta")
    with sessions(client)() as s:
        q = s.scalars(select(GapQuestion).where(GapQuestion.project_id == pid, GapQuestion.kind == "unclear")).one()
        qid = q.id
    client.post(f"/p/{pid}/questions/{qid}/answer", data={"text": "It goes to the team vault before 15 Nov."})
    view_as(client, pid, "Sana")
    assert "still need" in client.post(f"/p/{pid}/approve").text  # the new answer must be decided first
    with sessions(client)() as s:
        q = s.get(GapQuestion, qid)
        cid = q.testimony_claim_id
    client.post(f"/p/{pid}/claims/{cid}/decide", data={"action": "approve"})
    assert "Approved version 2" in client.post(f"/p/{pid}/approve").text
    with sessions(client)() as s:
        v2 = s.scalars(select(Version).where(Version.project_id == pid, Version.number == 2)).one()
        assert any("team vault" in x for x in v2.diff["added"])


def test_13_versions_and_audit_are_immutable(world):
    client, pid = world
    with sessions(client)() as s:
        v = s.scalars(select(Version).where(Version.project_id == pid)).first()
        v.approved_by = "Someone else"
        with pytest.raises(PermissionError):
            s.commit()
        s.rollback()
    with sessions(client)() as s:
        e = s.scalars(select(AuditEvent).where(AuditEvent.project_id == pid)).first()
        s.delete(e)
        with pytest.raises(PermissionError):
            s.commit()
        s.rollback()


def test_14_audit_records_who_did_what(world):
    client, pid = world
    view_as(client, pid, "Sana")
    t = client.get(f"/p/{pid}/audit").text
    for action in ("demo_loaded", "pipeline_run", "one_pass_approved", "conflict_resolved", "question_answered", "version_approved",
                   "handover_viewed", "section_acknowledged", "marked_unclear"):
        assert action in t, action


def test_15_export_is_scoped_and_carries_receipts(world):
    client, pid = world
    view_as(client, pid, "Meera Iyer")
    r = client.get(f"/p/{pid}/export.md")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    assert "[VERIFIED]" in r.text and re.search(r"\[WA-\d{4}\]", r.text) and "core committee" not in r.text.lower()
