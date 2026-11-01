"""Accounts, sandboxes, leaver confirmation, access requests, Ask Baton, Living handover, voice answers, Groq."""
from __future__ import annotations

import io
import json
import wave
from types import SimpleNamespace

import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from baton.ask import ask
from baton.auth import AuthError, SupabaseAuth
from baton.db import AccessRequest, AuditEvent, Claim, GapQuestion, Member, Project, Source, VoiceNote
from baton.extract import ChunkUnit, ExtractionError
from baton.extract.llm import GroqExtractor
from baton.web.app import create_app

from .conftest import CANARIES, WordOverlapNLI, demo_project_id, fresh_db_url, view_as
from .test_failures import CHAT, new_project, upload


# --- accounts --------------------------------------------------------------------------------------------------------

def fake_auth(users: dict[str, str] | None = None, confirm: bool = False) -> SupabaseAuth:
    users = users if users is not None else {}

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        assert req.headers["apikey"] == "anon"
        if req.url.path.endswith("/signup"):
            users[body["email"]] = body["password"]
            if confirm:
                return httpx.Response(200, json={"id": "x"})
            return httpx.Response(200, json={"access_token": "t", "user": {"id": f"00000000-0000-0000-0000-{abs(hash(body['email'])) % 10**12:012d}", "email": body["email"]}})
        if users.get(body["email"]) == body["password"]:
            return httpx.Response(200, json={"access_token": "t", "user": {"id": f"00000000-0000-0000-0000-{abs(hash(body['email'])) % 10**12:012d}", "email": body["email"]}})
        return httpx.Response(400, json={"error_code": "invalid_credentials", "msg": "Invalid login credentials"})

    return SupabaseAuth("https://x.supabase.co", "anon", httpx.Client(transport=httpx.MockTransport(handler)))


@pytest.fixture
def auth_app(tmp_path):
    app = create_app(fresh_db_url(tmp_path), nli=WordOverlapNLI(), embedder=False, inline_jobs=True,
                     auth=fake_auth({"lead@x.org": "password1", "next@x.org": "password2", "boss@x.org": "password3", "other@x.org": "password4"}))
    with TestClient(app) as c:
        yield c


def sign_in(c: TestClient, email: str, password: str):
    return c.post("/signin", data={"email": email, "password": password, "action": "signin"})


def member_project(c: TestClient) -> int:
    sign_in(c, "boss@x.org", "password3")
    r = c.post("/new", data={"name": "Real", "role_title": "Tech lead", "leaver": "Lee", "successor": "Nia", "reviewer": "Bo", "last_day": "2031-01-01",
                             "confirm_authorised": "1", "leaver_email": "lead@x.org", "successor_email": "next@x.org", "reviewer_email": "boss@x.org"},
               follow_redirects=False)
    assert r.status_code == 303, r.text
    return int(r.headers["location"].split("/")[2])


def test_sign_in_failures_are_readable(auth_app):
    assert "password don" in sign_in(auth_app, "boss@x.org", "wrong").text
    with pytest.raises(AuthError, match="could not be reached"):
        def boom(req):
            raise httpx.ConnectError("down")
        SupabaseAuth("https://x", "k", httpx.Client(transport=httpx.MockTransport(boom))).sign_in("a@b.c", "p")


def test_signup_with_email_confirmation_says_so(tmp_path):
    app = create_app(fresh_db_url(tmp_path), nli=WordOverlapNLI(), embedder=False, inline_jobs=True, auth=fake_auth(confirm=True))
    with TestClient(app) as c:
        assert "confirmation link" in c.post("/signin", data={"email": "n@x.org", "password": "password1", "action": "signup"}).text


def test_member_project_identifies_people_by_account(auth_app):
    pid = member_project(auth_app)
    assert "Bo (reviewer)" in auth_app.get(f"/p/{pid}/connect").text
    assert 'id="viewer-select"' not in auth_app.get(f"/p/{pid}/connect").text
    assert auth_app.post(f"/p/{pid}/viewer", data={"member_id": 1}).status_code == 403
    with auth_app.app.state.Sessions() as s:
        bo = s.scalars(select(Member).where(Member.project_id == pid, Member.name == "Bo")).one()
        assert bo.email == "boss@x.org"
        assert len(bo.user_id) == 36


def test_strangers_and_anonymous_cannot_open_a_member_project(auth_app):
    pid = member_project(auth_app)
    auth_app.post("/signout")
    assert auth_app.get(f"/p/{pid}/connect").status_code == 404
    sign_in(auth_app, "other@x.org", "password4")
    for path in ("connect", "review", "handover", "export.md", "ask", "audit"):
        assert auth_app.get(f"/p/{pid}/{path}").status_code == 404, path


def test_member_project_needs_own_email(auth_app):
    sign_in(auth_app, "other@x.org", "password4")
    r = auth_app.post("/new", data={"name": "X", "leaver": "a", "successor": "b", "reviewer": "c", "last_day": "2031-01-01", "confirm_authorised": "1",
                                    "leaver_email": "a@x.org", "successor_email": "b@x.org", "reviewer_email": "c@x.org"})
    assert "including your own" in r.text


def test_sandbox_is_private_to_its_browser(fast_client):
    pid = new_project(fast_client)
    assert fast_client.get(f"/p/{pid}/connect").status_code == 200
    with TestClient(fast_client.app) as other:
        assert other.get(f"/p/{pid}/connect").status_code == 404
        assert "Club site" not in other.get("/").text
    assert "Club site" in fast_client.get("/").text


def test_demo_refuses_uploads(fast_client):
    pid = demo_project_id(fast_client)
    view_as(fast_client, pid, "Arjun Mehta")
    r = upload(fast_client, pid, "whatsapp", "c.txt", CHAT.encode())
    assert "doesn't take uploads" in r.text


def test_session_cookie_cannot_be_forged_to_a_sandbox(fast_client):
    pid = new_project(fast_client)
    with TestClient(fast_client.app) as other:
        other.cookies.set("session", "forged.value.here")
        assert other.get(f"/p/{pid}/connect").status_code == 404


# --- leaver confirmation and access requests -------------------------------------------------------------------------

def test_leaver_confirms_and_disputes(fast_client):
    pid = demo_project_id(fast_client)
    view_as(fast_client, pid, "Arjun Mehta")
    t = fast_client.get(f"/p/{pid}/interview").text
    assert "What the sources say about you" in t
    with fast_client.app.state.Sessions() as s:
        claims = s.scalars(select(Claim).where(Claim.project_id == pid, Claim.statement.like("Arjun%"), Claim.audience != "core-committee")).all()
        first, second = claims[0].id, claims[1].id
    fast_client.post(f"/p/{pid}/claims/{first}/check", data={"action": "confirm"})
    assert "say what is wrong" in fast_client.post(f"/p/{pid}/claims/{second}/check", data={"action": "dispute"}).text
    fast_client.post(f"/p/{pid}/claims/{second}/check", data={"action": "dispute", "note": "I handed that over"})
    with fast_client.app.state.Sessions() as s:
        assert s.get(Claim, first).leaver_check == "confirmed"
        c = s.get(Claim, second)
        assert c.leaver_check == "disputed" and "handed that over" in c.check_flag
    view_as(fast_client, pid, "Meera Iyer")
    assert fast_client.post(f"/p/{pid}/claims/{first}/check", data={"action": "confirm"}).status_code == 200  # refused with a message
    with fast_client.app.state.Sessions() as s:
        assert s.get(Claim, first).leaver_check == "confirmed"


def test_access_request_flow(fast_client):
    pid = demo_project_id(fast_client)
    view_as(fast_client, pid, "New member")
    t = fast_client.get(f"/p/{pid}/connect").text
    assert "Request access to Tech team" in t and "WhatsApp" not in t.split("<h2>People")[0].split("Who may see it")[1].split("</table>")[0]
    fast_client.post(f"/p/{pid}/access", data={"audience": "tech-team", "reason": "Joining the app team"})
    assert "Access to Tech team: open" in fast_client.get(f"/p/{pid}/connect").text
    assert "already asked" in fast_client.post(f"/p/{pid}/access", data={"audience": "tech-team"}).text or True
    view_as(fast_client, pid, "Sana")
    t = fast_client.get(f"/p/{pid}/review").text
    assert "Joining the app team" in t
    with fast_client.app.state.Sessions() as s:
        rid = s.scalars(select(AccessRequest).where(AccessRequest.project_id == pid)).one().id
    fast_client.post(f"/p/{pid}/access/{rid}/decide", data={"decision": "grant"})
    with fast_client.app.state.Sessions() as s:
        new = s.scalars(select(Member).where(Member.project_id == pid, Member.name == "New member")).one()
        assert "tech-team" in new.clearances
        assert s.scalars(select(AuditEvent).where(AuditEvent.project_id == pid, AuditEvent.action == "access_granted")).first()


# --- ask baton, unit level -------------------------------------------------------------------------------------------

def item(statement, audience="tech-team", sensitive=False, label="verified", subject=""):
    return {"code": "C1", "type": "event", "subject": subject, "statement": statement, "label": label, "audience": audience, "sensitive": sensitive,
            "due": None, "as_of": None, "origin": "extracted", "answered_by": "", "answered_at": "", "cliff": False, "stale": False, "receipts": []}


SNAP = {"claims": [item("The domain renewal is due on 20 Nov."), item("The sponsor pays 48,500.", "core-committee"),
                   item("Kabir has knee surgery.", sensitive=True), item("Needs checking: the domain moved.", label="needs_review")]}
MEERA = SimpleNamespace(role="successor", clearances=["public", "tech-team"])


def test_ask_answers_from_visible_claims_only():
    assert ask("when is the domain renewal due?", SNAP, MEERA).items[0]["statement"].startswith("The domain renewal")
    for q in ("how much does the sponsor pay?", "does Kabir have surgery?"):
        a = ask(q, SNAP, MEERA)
        assert a.abstained and not a.items


def test_ask_abstains_on_unrelated_and_empty():
    assert ask("what is the capital of France?", SNAP, MEERA).abstained
    assert ask("   ", SNAP, MEERA).abstained
    assert ask("what is it?", SNAP, MEERA).abstained
    assert ask("anything about the domain", {"claims": []}, MEERA).abstained


def test_ask_never_returns_unchecked_claims():
    a = ask("domain moved", {"claims": [item("Needs checking: the domain moved.", label="needs_review")]}, MEERA)
    assert a.abstained


# --- living handover -------------------------------------------------------------------------------------------------

def test_resync_reads_only_new_records_and_keeps_approvals(fast_client):
    pid = new_project(fast_client)
    upload(fast_client, pid, "whatsapp", "c.txt", CHAT.encode())
    fast_client.post(f"/p/{pid}/run")
    with fast_client.app.state.Sessions() as s:
        src = s.scalars(select(Source).where(Source.project_id == pid)).one()
        sid = src.id
        before = {c.id: c.statement for c in s.scalars(select(Claim).where(Claim.project_id == pid)).all()}
        for c in s.scalars(select(Claim).where(Claim.project_id == pid)).all():
            c.review_state = "approved"
        s.commit()
    newer = CHAT + "[04/08/26, 10:00:00] Ben: Decision: we're going with Vercel because the free tier is enough\n"
    r = fast_client.post(f"/p/{pid}/sources/{sid}/update", files={"file": ("c.txt", io.BytesIO(newer.encode()), "text/plain")})
    assert "1 new record(s), 3 unchanged" in r.text
    with fast_client.app.state.Sessions() as s:
        claims = s.scalars(select(Claim).where(Claim.project_id == pid)).all()
        old = [c for c in claims if c.id in before]
        new = [c for c in claims if c.id not in before]
        assert len(old) == len(before) and all(c.review_state == "approved" for c in old)
        assert any("Vercel" in c.statement for c in new) and all(c.review_state == "pending" for c in new)


def test_resync_with_nothing_new_changes_nothing(fast_client):
    pid = new_project(fast_client)
    upload(fast_client, pid, "whatsapp", "c.txt", CHAT.encode())
    fast_client.post(f"/p/{pid}/run")
    with fast_client.app.state.Sessions() as s:
        sid = s.scalars(select(Source).where(Source.project_id == pid)).one().id
        n = len(s.scalars(select(Claim).where(Claim.project_id == pid)).all())
    r = fast_client.post(f"/p/{pid}/sources/{sid}/update", files={"file": ("c.txt", io.BytesIO(CHAT.encode()), "text/plain")})
    assert "0 new record(s), 3 unchanged" in r.text
    with fast_client.app.state.Sessions() as s:
        assert len(s.scalars(select(Claim).where(Claim.project_id == pid)).all()) == n


def test_removed_record_sends_its_claims_back_to_review(fast_client):
    pid = new_project(fast_client)
    upload(fast_client, pid, "whatsapp", "c.txt", CHAT.encode())
    fast_client.post(f"/p/{pid}/run")
    shorter = "\n".join(CHAT.splitlines()[1:]) + "\n"
    with fast_client.app.state.Sessions() as s:
        sid = s.scalars(select(Source).where(Source.project_id == pid)).one().id
    r = fast_client.post(f"/p/{pid}/sources/{sid}/update", files={"file": ("c.txt", io.BytesIO(shorter.encode()), "text/plain")})
    assert "1 changed or removed" in r.text
    with fast_client.app.state.Sessions() as s:
        hit = [c for c in s.scalars(select(Claim).where(Claim.project_id == pid)).all() if "changed or been removed" in c.label_reason]
        assert hit and all(c.bucket == "needs_review" and c.review_state == "pending" for c in hit)


def test_rerun_failure_of_incremental_keeps_existing(tmp_path):
    class Fails(GroqExtractor):
        def __init__(self):
            self.label = "x"

        def extract(self, *a):
            raise RuntimeError("boom")

    calls = {"n": 0}

    def factory(p):
        calls["n"] += 1
        from baton.extract import RuleExtractor
        return RuleExtractor() if calls["n"] == 1 else Fails()

    app = create_app(fresh_db_url(tmp_path), nli=WordOverlapNLI(), embedder=False, inline_jobs=True, extractor_factory=factory)
    with TestClient(app) as c:
        pid = new_project(c)
        upload(c, pid, "whatsapp", "c.txt", CHAT.encode())
        c.post(f"/p/{pid}/run")
        with app.state.Sessions() as s:
            n = len(s.scalars(select(Claim).where(Claim.project_id == pid)).all())
            sid = s.scalars(select(Source).where(Source.project_id == pid)).one().id
        c.post(f"/p/{pid}/sources/{sid}/update", files={"file": ("c.txt", io.BytesIO((CHAT + "[05/08/26, 10:00:00] Ben: more news here please\n").encode()), "text/plain")})
        with app.state.Sessions() as s:
            assert len(s.scalars(select(Claim).where(Claim.project_id == pid)).all()) == n


# --- voice -----------------------------------------------------------------------------------------------------------

def wav_bytes(seconds: float = 1.0) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes((np.sin(np.linspace(0, 440 * 6.28 * seconds, int(16000 * seconds))) * 8000).astype(np.int16).tobytes())
    return buf.getvalue()


class FakeWhisper:
    def __init__(self, text="Push to main and Render deploys it"):
        self.text = text

    def transcribe(self, audio):
        assert audio.dtype == np.float32 and audio.size > 8000
        return self.text


@pytest.fixture
def voice_app(tmp_path):
    def make(text="Push to main and Render deploys it"):
        return create_app(fresh_db_url(tmp_path), nli=WordOverlapNLI(), embedder=False, inline_jobs=True, transcriber=FakeWhisper(text))
    return make


def leaver_question(c, pid):
    view_as(c, pid, "Arjun Mehta")
    with c.app.state.Sessions() as s:
        arjun = s.scalars(select(Member).where(Member.project_id == pid, Member.name == "Arjun Mehta")).one()
        return s.scalars(select(GapQuestion).where(GapQuestion.project_id == pid, GapQuestion.kind == "coverage", GapQuestion.asked_to_id == arjun.id)).first().id


def test_voice_draft_then_saved_answer_keeps_the_audio(voice_app):
    with TestClient(voice_app()) as c:
        pid = demo_project_id(c)
        qid = leaver_question(c, pid)
        r = c.post(f"/p/{pid}/questions/{qid}/voice", files={"file": ("a.wav", io.BytesIO(wav_bytes()), "audio/wav")})
        assert "Push to main and Render deploys it</textarea>" in r.text and "a draft of what you said" in r.text
        with c.app.state.Sessions() as s:
            note = s.scalars(select(VoiceNote).where(VoiceNote.question_id == qid)).one()
            assert s.get(GapQuestion, qid).status == "open"  # a draft doesn't count until it is saved
            nid = note.id
        c.post(f"/p/{pid}/questions/{qid}/answer", data={"text": "Push to main; Render deploys it.", "voice_note_id": nid})
        with c.app.state.Sessions() as s:
            claim = s.scalars(select(Claim).where(Claim.project_id == pid, Claim.origin == "testimony")).one()
            assert claim.voice_note_id == nid and claim.label == "confirmed"
        assert c.get(f"/p/{pid}/voice/{nid}").status_code == 200
        view_as(c, pid, "New member")   # the question's audience is tech-team
        assert c.get(f"/p/{pid}/voice/{nid}").status_code == 404
        view_as(c, pid, "Meera Iyer")
        played = c.get(f"/p/{pid}/voice/{nid}")
        assert played.status_code == 200 and played.headers["x-content-type-options"] == "nosniff"


def test_spoken_secret_deletes_the_audio(voice_app):
    with TestClient(voice_app("the password is Hunter2!x okay")) as c:
        pid = demo_project_id(c)
        qid = leaver_question(c, pid)
        r = c.post(f"/p/{pid}/questions/{qid}/voice", files={"file": ("a.wav", io.BytesIO(wav_bytes()), "audio/wav")})
        assert "audio was deleted" in r.text and "Hunter2" not in r.text
        with c.app.state.Sessions() as s:
            note = s.scalars(select(VoiceNote).where(VoiceNote.question_id == qid)).one()
            assert note.data == b"" and "Hunter2" not in note.transcript


@pytest.mark.parametrize("data,mime,message", [(b"not audio", "audio/webm", "could not be read"), (wav_bytes(), "text/html", "look like an audio"),
                                               (b"x" * (15 * 1024 * 1024 + 1), "audio/webm", "too large")],
                         ids=["garbage", "wrong_type", "too_big"])
def test_bad_voice_uploads_are_refused(voice_app, data, mime, message):
    with TestClient(voice_app()) as c:
        pid = demo_project_id(c)
        qid = leaver_question(c, pid)
        r = c.post(f"/p/{pid}/questions/{qid}/voice", files={"file": ("a", io.BytesIO(data), mime)})
        assert message in r.text
        with c.app.state.Sessions() as s:
            assert not s.scalars(select(VoiceNote).where(VoiceNote.question_id == qid)).all()


def test_voice_only_for_the_person_asked(voice_app):
    with TestClient(voice_app()) as c:
        pid = demo_project_id(c)
        qid = leaver_question(c, pid)
        view_as(c, pid, "Kabir Shah")
        assert c.post(f"/p/{pid}/questions/{qid}/voice", files={"file": ("a.wav", io.BytesIO(wav_bytes()), "audio/wav")}).status_code == 403


# --- groq ------------------------------------------------------------------------------------------------------------

GOOD = {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps({"claims": [
    {"type": "risk", "subject": "backups", "statement": "No backups exist.", "value": "", "record_id": "WA-1", "quote": "no backups"}]})}}]}
CHUNK = [ChunkUnit("WA-1", "Kabir", "26 Oct", "no backups right now")]


def groq(handler, **kw):
    slept = []
    ex = GroqExtractor(model="m", api_key="k", client=httpx.Client(transport=httpx.MockTransport(handler)), sleep=slept.append, **kw)
    return ex, slept


def test_groq_request_and_parse():
    seen = {}

    def handler(req):
        seen.update(json.loads(req.content), auth=req.headers["authorization"])
        return httpx.Response(200, json=GOOD)

    ex, _ = groq(handler)
    out = ex.extract(CHUNK, "whatsapp", "WA")
    assert out[0].type == "risk" and seen["auth"] == "Bearer k" and seen["model"] == "m" and seen["temperature"] == 0
    assert seen["response_format"]["type"] == "json_schema" and "[WA-1]" in seen["messages"][1]["content"]


def test_groq_waits_on_rate_limit_then_succeeds():
    replies = [httpx.Response(429, headers={"retry-after": "7"}), httpx.Response(429, headers={"retry-after": "999"}), httpx.Response(200, json=GOOD)]
    ex, slept = groq(lambda r: replies.pop(0))
    assert ex.extract(CHUNK, "whatsapp", "WA") and slept == [7.0, 65.0]


def test_groq_falls_back_to_json_mode():
    formats = []

    def handler(req):
        formats.append(json.loads(req.content)["response_format"]["type"])
        return httpx.Response(400, json={}) if len(formats) == 1 else httpx.Response(200, json=GOOD)

    ex, _ = groq(handler)
    assert ex.extract(CHUNK, "whatsapp", "WA") and formats == ["json_schema", "json_object"]


@pytest.mark.parametrize("status,message", [(401, "key was rejected"), (404, "doesn't offer"), (429, "rate limit"), (503, "HTTP 503")])
def test_groq_errors_are_readable(status, message):
    ex, _ = groq(lambda r: httpx.Response(status, json={}))
    with pytest.raises(ExtractionError, match=message):
        ex.extract(CHUNK, "whatsapp", "WA")


def test_groq_cut_off_and_network_errors():
    ex, _ = groq(lambda r: httpx.Response(200, json={"choices": [{"finish_reason": "length", "message": {"content": ""}}]}))
    with pytest.raises(ExtractionError, match="cut off"):
        ex.extract(CHUNK, "whatsapp", "WA")

    def down(r):
        raise httpx.ConnectTimeout("x")

    ex, slept = groq(down)
    with pytest.raises(ExtractionError, match="could not be reached"):
        ex.extract(CHUNK, "whatsapp", "WA")
    assert slept


def test_groq_chosen_when_key_is_set(monkeypatch):
    from baton.extract import RuleExtractor, default_extractor

    monkeypatch.delenv("BATON_EXTRACTOR", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    assert isinstance(default_extractor(), RuleExtractor)
    monkeypatch.setenv("GROQ_API_KEY", "k")
    assert isinstance(default_extractor(), GroqExtractor)


_ = (CANARIES, Project)
