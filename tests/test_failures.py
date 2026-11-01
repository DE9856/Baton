"""Uploads, bad input, and failures of the AI service. The product must not mislead when things go wrong."""
from __future__ import annotations

import io
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from baton.db import Claim, GapQuestion, Job, Project, make_engine, make_sessionmaker
from baton.demo.loader import load_demo
from baton.extract import ExtractionError, Extractor, RawClaim, ReplayExtractor, RuleExtractor
from baton.extract.llm import CLAIM_SCHEMA, ClaudeExtractor, OllamaExtractor, parse_claims
from baton.pipeline import run_pipeline
from baton.web.app import create_app

from .conftest import DownNLI, WordOverlapNLI, fresh_db_url

CHAT = ("[01/08/26, 19:05:43] Asha: Decision: we're going with Postgres because it is cheap\n"
        "[02/08/26, 10:00:00] Ben: the deploy key is AIzaSyD4-AnotherKey_abcdefghijklmnopqr1\n"
        "[03/08/26, 10:00:00] Asha: The launch is due 20 Nov. I'll handle the registrar on my account\n")


def new_project(client: TestClient) -> int:
    r = client.post("/new", data={"name": "Club site", "role_title": "Tech lead", "leaver": "Asha Rao", "successor": "Ben Lee", "reviewer": "Cara",
                                  "last_day": "2030-11-15", "confirm_authorised": "1"}, follow_redirects=False)
    assert r.status_code == 303
    return int(r.headers["location"].split("/")[2])


def upload(client, pid, kind, name, data, audience="tech-team", authorised="1"):
    return client.post(f"/p/{pid}/sources/upload", data={"kind": kind, "audience": audience, "authorised": authorised, "consent": "ok"},
                       files={"file": (name, io.BytesIO(data), "application/octet-stream")})


def test_full_upload_path_without_any_api_key(fast_client):
    pid = new_project(fast_client)
    r = upload(fast_client, pid, "whatsapp", "chat.txt", CHAT.encode())
    assert "Added" in r.text and "3 records" in r.text
    r = fast_client.post(f"/p/{pid}/run")
    assert "Run finished" in r.text and "Basic rules" in r.text
    with fast_client.app.state.Sessions() as s:
        claims = s.scalars(select(Claim).where(Claim.project_id == pid)).all()
        assert claims
        assert any(c.type == "decision" for c in claims)
        qs = s.scalars(select(GapQuestion).where(GapQuestion.project_id == pid)).all()
        assert any(q.kind == "coverage" for q in qs)
        # The pasted key is masked and becomes a rotation task, whatever the extractor did.
        assert any(c.origin == "guard" for c in claims)
    assert "AIzaSy" not in fast_client.get(f"/p/{pid}/sources/1").text


@pytest.mark.parametrize("kind,name,data,message", [
    ("whatsapp", "x.txt", b"this is not a chat", "No WhatsApp messages"),
    ("trello", "x.json", b"{broken", "not valid JSON"),
    ("trello", "x.json", b'{"a":1}', "Trello board export"),
    ("github", "x.json", b"[]", "GitHub JSON"),
    ("notes", "x.md", b"   ", "empty"),
    ("notes", "x.docx", b"not really a docx", "could not be opened"),
    ("whatsapp", "x.txt", "café".encode("latin-1"), "not UTF-8"),
])
def test_bad_uploads_explain_themselves_and_add_nothing(fast_client, kind, name, data, message):
    pid = new_project(fast_client)
    r = upload(fast_client, pid, kind, name, data)
    assert r.status_code == 200 and message in r.text
    assert "No sources yet" in r.text


def test_oversize_upload_refused(fast_client):
    pid = new_project(fast_client)
    assert "10 MB limit" in upload(fast_client, pid, "notes", "big.md", b"x" * (10 * 1024 * 1024 + 1)).text


def test_upload_requires_authorisation_checkbox(fast_client):
    pid = new_project(fast_client)
    assert "confirm you are allowed" in upload(fast_client, pid, "whatsapp", "c.txt", CHAT.encode(), authorised="").text


def test_upload_needs_the_right_role(fast_client):
    pid = new_project(fast_client)
    with fast_client.app.state.Sessions() as s:
        p = s.get(Project, pid)
        successor = p.successor_id
    fast_client.post(f"/p/{pid}/viewer", data={"member_id": successor})
    assert upload(fast_client, pid, "whatsapp", "c.txt", CHAT.encode()).status_code == 403


def test_run_without_sources_says_so(fast_client):
    pid = new_project(fast_client)
    assert "Add at least one source" in fast_client.post(f"/p/{pid}/run").text


def test_new_project_requires_consent(fast_client):
    r = fast_client.post("/new", data={"name": "x", "leaver": "a", "successor": "b", "reviewer": "c", "last_day": "2030-01-01"})
    assert "allowed to use" in r.text


def test_viewer_cannot_switch_into_another_project(fast_client):
    a = new_project(fast_client)
    b = new_project(fast_client)
    with fast_client.app.state.Sessions() as s:
        other = s.get(Project, b).reviewer_id
    assert fast_client.post(f"/p/{a}/viewer", data={"member_id": other}).status_code == 404


def test_viewer_switch_cannot_redirect_off_site(fast_client):
    pid = new_project(fast_client)
    with fast_client.app.state.Sessions() as s:
        m = s.get(Project, pid).reviewer_id
    r = fast_client.post(f"/p/{pid}/viewer", data={"member_id": m, "back": "https://evil.example/"}, follow_redirects=False)
    assert r.headers["location"] == f"/p/{pid}"


def test_html_in_sources_is_escaped(fast_client):
    pid = new_project(fast_client)
    chat = b"[01/08/26, 19:05:43] Asha: <script>alert(1)</script> We decided to use <b>Render</b> because it is free\n"
    upload(fast_client, pid, "whatsapp", "c.txt", chat)
    fast_client.post(f"/p/{pid}/run")
    for url in (f"/p/{pid}/sources/1", f"/p/{pid}/ledger", f"/p/{pid}/review"):
        body = fast_client.get(url).text
        assert "<script>alert" not in body


def test_unknown_project_is_a_clean_404(fast_client):
    r = fast_client.get("/p/9999/connect")
    assert r.status_code == 404 and "Not found" in r.text


# --- the extraction service failing ------------------------------------------------------------------------------

class FlakyExtractor(Extractor):
    name = "flaky"
    label = "Flaky"
    chunk_size = 2

    def __init__(self):
        self.calls = 0

    def extract(self, chunk, source_type, source_code):
        self.calls += 1
        if self.calls == 1:
            raise ExtractionError("The Anthropic API could not be reached.")
        return [RawClaim("decision", "x", chunk[0].text[:60], "", [{"id": chunk[0].uid, "quote": chunk[0].text[:60]}])]


def test_one_failed_chunk_is_labelled_incomplete_not_hidden(tmp_path):
    app = create_app(fresh_db_url(tmp_path), nli=WordOverlapNLI(), embedder=False, inline_jobs=True,
                     extractor_factory=lambda p: FlakyExtractor())
    with TestClient(app) as c:
        pid = new_project(c)
        upload(c, pid, "whatsapp", "c.txt", CHAT.encode())
        t = c.post(f"/p/{pid}/run").text
        assert "incomplete" in t and "could not be reached" in t
        with app.state.Sessions() as s:
            assert s.scalars(select(Job).where(Job.project_id == pid)).first().stats["incomplete"] is True


class Crashing(Extractor):
    def extract(self, chunk, source_type, source_code):
        raise RuntimeError("boom with internal detail")


def test_unexpected_crash_fails_cleanly_without_internal_detail(tmp_path):
    app = create_app(fresh_db_url(tmp_path), nli=WordOverlapNLI(), embedder=False, inline_jobs=True,
                     extractor_factory=lambda p: Crashing())
    with TestClient(app) as c:
        pid = new_project(c)
        upload(c, pid, "whatsapp", "c.txt", CHAT.encode())
        t = c.post(f"/p/{pid}/run").text
        assert "The run failed" in t and "RuntimeError" in t and "boom" not in t
        from baton import review

        with app.state.Sessions() as s:
            assert not s.scalars(select(Claim).where(Claim.project_id == pid)).all()  # no half-built ledger
            assert any("last run failed" in b for b in review.blockers(s, s.get(Project, pid)))


def test_when_the_checking_model_is_down_nothing_is_verified(tmp_path):
    Session = make_sessionmaker(make_engine(fresh_db_url(tmp_path)))
    with Session() as s:
        p = load_demo(s)
        s.commit()
        stats = run_pipeline(s, p, ReplayExtractor(), nli=DownNLI(), embedder=False)
        s.commit()
        claims = s.scalars(select(Claim)).all()
        assert stats["nli_available"] is False
        assert not [c for c in claims if c.label == "verified" and c.origin == "extracted"]


def test_restart_marks_interrupted_jobs_failed(tmp_path):
    url = fresh_db_url(tmp_path)
    app = create_app(url, nli=WordOverlapNLI(), embedder=False, inline_jobs=True)
    with TestClient(app) as c:
        pid = new_project(c)
    with app.state.Sessions() as s:
        s.add(Job(project_id=pid, state="running", step="x"))
        s.commit()
    app2 = create_app(url, nli=WordOverlapNLI(), embedder=False, inline_jobs=True)
    with app2.state.Sessions() as s:
        job = s.scalars(select(Job).where(Job.project_id == pid)).first()
        assert job.state == "failed" and "restarted" in job.error


# --- adapters, with fake clients ----------------------------------------------------------------------------------

class FakeMessages:
    def __init__(self, reply=None, error=None):
        self.reply, self.error, self.calls = reply, error, []

    def create(self, **kw):
        self.calls.append(kw)
        if self.error:
            raise self.error
        return self.reply


def fake_response(text, stop="end_turn"):
    return SimpleNamespace(stop_reason=stop, content=[SimpleNamespace(type="text", text=text)])


GOOD = json.dumps({"claims": [{"type": "risk", "subject": "backups", "statement": "No backups exist.", "value": "", "record_id": "WA-1", "quote": "no backups"}]})


def test_claude_adapter_request_shape_and_parsing():
    fake = FakeMessages(fake_response(GOOD))
    ex = ClaudeExtractor(model="claude-haiku-5-5", client=SimpleNamespace(messages=fake))
    from baton.extract import ChunkUnit
    out = ex.extract([ChunkUnit("WA-1", "Kabir", "26 Oct", "no backups right now")], "whatsapp", "WA")
    assert out[0].type == "risk" and out[0].evidence == [{"id": "WA-1", "quote": "no backups"}]
    call = fake.calls[0]
    assert call["output_config"]["format"]["schema"] is CLAIM_SCHEMA
    assert call["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "<records>" in call["messages"][0]["content"] and "[WA-1]" in call["messages"][0]["content"]


@pytest.mark.parametrize("stop,message", [("refusal", "declined"), ("max_tokens", "cut off")])
def test_claude_adapter_stop_reasons(stop, message):
    ex = ClaudeExtractor(model="m", client=SimpleNamespace(messages=FakeMessages(fake_response(GOOD, stop))))
    with pytest.raises(ExtractionError, match=message):
        ex.extract([], "whatsapp", "WA")


def test_claude_adapter_api_errors_become_readable():
    import anthropic
    import httpx

    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    errors = [
        (anthropic.RateLimitError("x", response=httpx.Response(429, request=request), body=None), "rate limit"),
        (anthropic.AuthenticationError("x", response=httpx.Response(401, request=request), body=None), "rejected"),
        (anthropic.InternalServerError("x", response=httpx.Response(500, request=request), body=None), "HTTP 500"),
        (anthropic.APIConnectionError(request=request), "could not be reached"),
    ]
    for err, message in errors:
        ex = ClaudeExtractor(model="m", client=SimpleNamespace(messages=FakeMessages(error=err)))
        with pytest.raises(ExtractionError, match=message):
            ex.extract([], "whatsapp", "WA")


@pytest.mark.parametrize("text", ["not json", '{"nope": []}', '{"claims": 5}'])
def test_malformed_model_output_is_an_error_not_silence(text):
    with pytest.raises(ExtractionError):
        parse_claims(text)


def test_model_output_with_unknown_type_is_dropped():
    bad = json.dumps({"claims": [{"type": "gossip", "statement": "x", "record_id": "a", "quote": "b"}]})
    assert parse_claims(bad) == []


def test_ollama_adapter_failure_is_readable():
    import httpx

    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    with pytest.raises(ExtractionError, match="could not be reached"):
        OllamaExtractor(client=client).extract([], "whatsapp", "WA")


def test_rule_extractor_keeps_quotes_verbatim():
    from baton.extract import ChunkUnit
    from baton.verify.grounding import ground_quote

    units = [ChunkUnit("WA-1", "Asha", "x", "Decision: we're going with Postgres because it is cheap. Who owns backups?"),
             ChunkUnit("TR-c1", "A", "x", "Card 'Logo' is in list 'To Do'; due: 2026-11-30; members: Asha Rao; last activity: 2026-10-01.")]
    claims = RuleExtractor().extract(units, "x", "x")
    assert {c.type for c in claims} >= {"decision", "open_question", "status", "deadline", "owner"}
    chunk = {u.uid: u.text for u in units}
    for c in claims:
        assert ground_quote(c.evidence[0]["quote"], c.evidence[0]["id"], chunk).grounded, c.evidence
