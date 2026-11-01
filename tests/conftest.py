from __future__ import annotations

import json
import os
import re
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from baton.verify.models import NLIScore
from baton.web.app import create_app

ROOT = Path(__file__).parent.parent
GOLD = json.loads((ROOT / "eval" / "gold_campusconnect.json").read_text("utf-8"))
CANARIES = GOLD["canaries_for_incoming_tech_lead"]
NEVER_SHOWN = {k: v for k, v in CANARIES.items() if k in ("K3_restricted_password", "K5_secret_key")}


class WordOverlapNLI:
    """A fast stand-in for tests about flow rather than model quality: entailed when most statement words are present."""

    available = True

    def score(self, pairs):
        out = []
        for premise, hypothesis in pairs:
            p = set(re.findall(r"\w+", premise.lower()))
            h = set(re.findall(r"\w+", hypothesis.lower()))
            overlap = len(p & h) / max(len(h), 1)
            contra = 1.0 if {"never", "not", "no"} & (p ^ h) else 0.0
            out.append(NLIScore(overlap, contra, 1 - overlap))
        return out


class DownNLI:
    available = False

    def score(self, pairs):
        return [NLIScore(0.0, 0.0, 1.0) for _ in pairs]


@pytest.fixture(scope="session")
def real_nli():
    from baton.verify.models import get_nli

    nli = get_nli()
    assert nli.available
    return nli


@pytest.fixture(scope="session")
def real_embedder():
    from baton.verify.models import get_embedder

    return get_embedder()


def fresh_db_url(path) -> str:
    """A new empty database. Set BATON_TEST_POSTGRES_URL to run every test on Postgres, with RLS applied."""
    server = os.environ.get("BATON_TEST_POSTGRES_URL")
    if not server:
        return f"sqlite:///{path / 'test.sqlite3'}"
    import sqlalchemy

    name = f"t_{uuid.uuid4().hex[:12]}"
    engine = sqlalchemy.create_engine(server.replace("postgresql://", "postgresql+psycopg://", 1), isolation_level="AUTOCOMMIT")
    with engine.connect() as conn:
        conn.execute(sqlalchemy.text(f"create database {name}"))
    engine.dispose()
    return server.rsplit("/", 1)[0] + f"/{name}"


def demo_project_id(client: TestClient) -> int:
    r = client.post("/demo", follow_redirects=False)
    assert r.status_code == 303
    return int(r.headers["location"].split("/")[2])


def view_as(client: TestClient, pid: int, name: str) -> None:
    from sqlalchemy import select

    from baton.db import Member

    with client.app.state.Sessions() as s:
        m = s.scalars(select(Member).where(Member.project_id == pid, Member.name == name)).first()
        assert m, name
        mid = m.id
    r = client.post(f"/p/{pid}/viewer", data={"member_id": mid}, follow_redirects=False)
    assert r.status_code == 303


@pytest.fixture
def fast_client(tmp_path):
    """The app with a word-overlap NLI, no embedder and synchronous jobs."""
    app = create_app(fresh_db_url(tmp_path), nli=WordOverlapNLI(), embedder=False, inline_jobs=True)
    with TestClient(app) as c:
        yield c
