"""The evidence model (Round 1 plan, section 6.3), as SQLAlchemy tables.

The target is Supabase Postgres with Row-Level Security. The same models run on SQLite for local use
when none is configured; `rls.sql` holds the policies applied on Postgres. See docs/CHANGES_FROM_PLAN.md.
"""
from __future__ import annotations

import logging
import os
import uuid
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import (JSON, Boolean, Date, DateTime, Float, ForeignKey, Integer, LargeBinary, String, Text, create_engine, event,
                        inspect, text)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker


log = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    # A sandbox belongs to the browser that made it: no account, and nobody else can open it.
    is_sandbox: Mapped[bool] = mapped_column(Boolean, default=False)
    role_title: Mapped[str] = mapped_column(String(100), default="Tech lead")
    as_of: Mapped[date] = mapped_column(Date)
    last_day: Mapped[date] = mapped_column(Date)
    leaver_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    successor_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reviewer_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    extractor: Mapped[str] = mapped_column(String(60), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    members: Mapped[list["Member"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    sources: Mapped[list["Source"]] = relationship(back_populates="project", cascade="all, delete-orphan")


class Member(Base):
    __tablename__ = "members"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    title: Mapped[str] = mapped_column(String(120), default="")
    role: Mapped[str] = mapped_column(String(20), default="teammate")  # leaver | successor | reviewer | teammate
    email: Mapped[str] = mapped_column(String(200), default="", index=True)
    # The identity row-level security checks. A real sign-in replaces this placeholder with the account's id.
    user_id: Mapped[str] = mapped_column(String(36), default=lambda: str(uuid.uuid4()), index=True)
    aliases: Mapped[list] = mapped_column(JSON, default=list)
    clearances: Mapped[list] = mapped_column(JSON, default=list)
    project: Mapped[Project] = relationship(back_populates="members")


class Source(Base):
    __tablename__ = "sources"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    code: Mapped[str] = mapped_column(String(10))
    type: Mapped[str] = mapped_column(String(20))
    title: Mapped[str] = mapped_column(String(200))
    audience: Mapped[str] = mapped_column(String(60))
    authorised_by: Mapped[str] = mapped_column(String(120))
    consent_note: Mapped[str] = mapped_column(Text, default="")
    origin_url: Mapped[str] = mapped_column(String(300), default="")  # a GitHub repository can be re-synced
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    skipped: Mapped[list] = mapped_column(JSON, default=list)
    notes: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    project: Mapped[Project] = relationship(back_populates="sources")
    units: Mapped[list["EvidenceUnit"]] = relationship(back_populates="source", cascade="all, delete-orphan")


class EvidenceUnit(Base):
    __tablename__ = "evidence_units"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), index=True)
    uid: Mapped[str] = mapped_column(String(40), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    author: Mapped[str] = mapped_column(String(120))
    ts: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    text: Mapped[str] = mapped_column(Text)  # secrets are already masked: the raw value is never stored
    link: Mapped[str] = mapped_column(String(500), default="")
    kind: Mapped[str] = mapped_column(String(20), default="text")
    sensitivity: Mapped[list] = mapped_column(JSON, default=list)
    retired: Mapped[bool] = mapped_column(Boolean, default=False)  # no longer in the source after a re-sync
    source: Mapped[Source] = relationship(back_populates="units")


class Claim(Base):
    __tablename__ = "claims"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    code: Mapped[str] = mapped_column(String(10))
    type: Mapped[str] = mapped_column(String(24))
    subject: Mapped[str] = mapped_column(String(300), default="")
    statement: Mapped[str] = mapped_column(Text)
    value: Mapped[str] = mapped_column(String(300), default="")
    as_of: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    due: Mapped[date | None] = mapped_column(Date, nullable=True)
    source_id: Mapped[int | None] = mapped_column(ForeignKey("sources.id"), nullable=True)
    audience: Mapped[str] = mapped_column(String(60))
    origin: Mapped[str] = mapped_column(String(20), default="extracted")  # extracted | testimony | reviewer
    # Label, computed by rules. Reviewer sees all; successor sees verified and confirmed only.
    label: Mapped[str] = mapped_column(String(20), default="needs_review")
    label_reason: Mapped[str] = mapped_column(Text, default="")
    sensitive: Mapped[bool] = mapped_column(Boolean, default=False)
    sens_tags: Mapped[list] = mapped_column(JSON, default=list)
    cliff: Mapped[bool] = mapped_column(Boolean, default=False)
    stale: Mapped[bool] = mapped_column(Boolean, default=False)
    bucket: Mapped[str] = mapped_column(String(16), default="needs_review")  # one_pass | decide | needs_review
    review_state: Mapped[str] = mapped_column(String(12), default="pending")  # pending | approved | rejected
    reviewed_by: Mapped[str] = mapped_column(String(120), default="")
    edited: Mapped[bool] = mapped_column(Boolean, default=False)
    original_statement: Mapped[str] = mapped_column(Text, default="")
    answered_by: Mapped[str] = mapped_column(String(120), default="")
    answered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    check_flag: Mapped[str] = mapped_column(Text, default="")  # advisory "check this" note on testimony
    leaver_check: Mapped[str] = mapped_column(String(12), default="")  # "" | confirmed | disputed
    leaver_note: Mapped[str] = mapped_column(Text, default="")
    voice_note_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    evidence: Mapped[list["ClaimEvidence"]] = relationship(back_populates="claim", cascade="all, delete-orphan")


class ClaimEvidence(Base):
    __tablename__ = "claim_evidence"
    id: Mapped[int] = mapped_column(primary_key=True)
    claim_id: Mapped[int] = mapped_column(ForeignKey("claims.id"), index=True)
    unit_id: Mapped[int | None] = mapped_column(ForeignKey("evidence_units.id"), nullable=True)
    quote: Mapped[str] = mapped_column(Text, default="")
    match: Mapped[str] = mapped_column(String(20), default="rejected")
    match_reason: Mapped[str] = mapped_column(Text, default="")
    entailment: Mapped[float | None] = mapped_column(Float, nullable=True)
    guard_missing: Mapped[list] = mapped_column(JSON, default=list)
    claim: Mapped[Claim] = relationship(back_populates="evidence")
    unit: Mapped[EvidenceUnit | None] = relationship()


class Conflict(Base):
    __tablename__ = "conflicts"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    claim_ids: Mapped[list] = mapped_column(JSON)  # every claim on either side, after merging related cards
    side_a: Mapped[list] = mapped_column(JSON)     # claim ids, oldest side
    side_b: Mapped[list] = mapped_column(JSON)     # claim ids, newest side
    facet: Mapped[str] = mapped_column(String(40))
    nli: Mapped[float] = mapped_column(Float)
    newer_side: Mapped[str] = mapped_column(String(1), default="b")
    subject: Mapped[str] = mapped_column(String(300), default="")
    resolution: Mapped[str] = mapped_column(String(10), default="")  # a | b | neither
    note: Mapped[str] = mapped_column(Text, default="")
    resolved_by: Mapped[str] = mapped_column(String(120), default="")
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class GapQuestion(Base):
    __tablename__ = "gap_questions"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    kind: Mapped[str] = mapped_column(String(30))
    text: Mapped[str] = mapped_column(Text)
    priority: Mapped[int] = mapped_column(Integer, default=5)
    route: Mapped[str] = mapped_column(String(12))          # leaver | reviewer | triage | teammate
    asked_to_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    claim_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    audience: Mapped[str] = mapped_column(String(60))
    status: Mapped[str] = mapped_column(String(12), default="open")  # open | answered | dismissed
    answer: Mapped[str] = mapped_column(Text, default="")
    answered_by: Mapped[str] = mapped_column(String(120), default="")
    answered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    testimony_claim_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    origin_section: Mapped[str] = mapped_column(String(40), default="")


class AccessRequest(Base):
    """A person asks to see more instead of hitting a dead end. It reveals only that a restricted source exists."""

    __tablename__ = "access_requests"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    member_id: Mapped[int] = mapped_column(Integer)
    audience: Mapped[str] = mapped_column(String(60))
    reason: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(10), default="open")  # open | granted | denied
    decided_by: Mapped[str] = mapped_column(String(120), default="")
    at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class VoiceNote(Base):
    """A spoken answer, kept as evidence behind testimony, under the same audience as the question."""

    __tablename__ = "voice_notes"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    question_id: Mapped[int] = mapped_column(Integer, index=True)
    member_id: Mapped[int] = mapped_column(Integer)
    audience: Mapped[str] = mapped_column(String(60))
    sensitive: Mapped[bool] = mapped_column(Boolean, default=False)
    mime: Mapped[str] = mapped_column(String(60))
    data: Mapped[bytes] = mapped_column(LargeBinary)
    transcript: Mapped[str] = mapped_column(Text, default="")
    at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class Version(Base):
    """An immutable approved snapshot. Rows are never updated or deleted (enforced below)."""

    __tablename__ = "versions"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    approved_by: Mapped[str] = mapped_column(String(120))
    approved_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    snapshot: Mapped[dict] = mapped_column(JSON)
    content_hash: Mapped[str] = mapped_column(String(64))
    diff: Mapped[dict] = mapped_column(JSON, default=dict)


class Acknowledgement(Base):
    __tablename__ = "acknowledgements"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    section: Mapped[str] = mapped_column(String(40))
    member_id: Mapped[int] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(10))  # ack | unclear
    note: Mapped[str] = mapped_column(Text, default="")
    at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class AuditEvent(Base):
    """Append-only (enforced below)."""

    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(Integer, index=True)
    actor: Mapped[str] = mapped_column(String(120))
    action: Mapped[str] = mapped_column(String(60))
    target: Mapped[str] = mapped_column(String(200), default="")
    detail: Mapped[str] = mapped_column(Text, default="")
    at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    state: Mapped[str] = mapped_column(String(12), default="queued")  # queued | running | done | failed
    step: Mapped[str] = mapped_column(String(120), default="")
    progress: Mapped[int] = mapped_column(Integer, default=0)
    stats: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str] = mapped_column(Text, default="")
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


def _forbid(*_args, **_kw):
    raise PermissionError("This table is append-only: rows cannot be changed or deleted.")


for _model in (AuditEvent, Version):
    event.listen(_model, "before_update", _forbid)
    event.listen(_model, "before_delete", _forbid)


def default_url() -> str:
    url = os.environ.get("BATON_DATABASE_URL")
    if url:
        return url
    path = Path(os.environ.get("BATON_DATA_DIR", "data"))
    path.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{path / 'baton.sqlite3'}"


def make_engine(url: str | None = None):
    url = url or default_url()
    # Supabase hands out postgresql:// URLs; SQLAlchemy needs to be told which driver to use.
    for prefix in ("postgresql://", "postgres://"):
        if url.startswith(prefix):
            url = "postgresql+psycopg://" + url[len(prefix):]
    if url.startswith("sqlite"):
        kwargs = {"connect_args": {"check_same_thread": False, "timeout": 30}}
    else:
        # Supabase's pooler reuses server connections, so prepared statements can't be relied on. A bad host fails in 15 s, not minutes.
        kwargs = {"pool_pre_ping": True, "pool_size": 5, "max_overflow": 5, "connect_args": {"prepare_threshold": None, "connect_timeout": 15}}
    engine = create_engine(url, **kwargs)
    if url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def _pragmas(conn, _rec):
            cur = conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()
    Base.metadata.create_all(engine)
    _add_missing_columns(engine)
    if engine.dialect.name == "postgresql":
        _apply_rls(engine)
    return engine


def _add_missing_columns(engine) -> None:
    """A small forward migration: new nullable or defaulted columns are added to an older database."""
    inspector = inspect(engine)
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            existing = {c["name"] for c in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing:
                    continue
                ddl = column.type.compile(engine.dialect)
                default = "false" if isinstance(column.type, Boolean) else "''" if isinstance(column.type, (String, Text)) else None
                if default is not None and column.default is not None:
                    conn.execute(text(f"alter table {table.name} add column {column.name} {ddl} not null default {default}"))
                else:
                    conn.execute(text(f"alter table {table.name} add column {column.name} {ddl}"))
                log.info("Added column %s.%s", table.name, column.name)


def _apply_rls(engine) -> None:
    sql = (Path(__file__).parent / "rls.sql").read_text("utf-8")
    with engine.begin() as conn:
        # Supabase provides these and doesn't let the app change its auth schema, so it is left alone when it's there.
        # A plain Postgres gets small equivalents so the same policies can be tested.
        if not conn.execute(text("select 1 from pg_roles where rolname = 'authenticated'")).first():
            conn.execute(text("create role authenticated nologin"))
        conn.execute(text("grant usage on schema public to authenticated"))
        has_uid = conn.execute(text("select 1 from pg_proc p join pg_namespace n on n.oid = p.pronamespace "
                                    "where n.nspname = 'auth' and p.proname = 'uid'")).first()
        if not has_uid:
            conn.execute(text("create schema if not exists auth"))
            conn.execute(text("grant usage on schema auth to authenticated"))
            conn.execute(text("create function auth.uid() returns uuid language sql stable as "
                              "$$ select nullif(nullif(current_setting('request.jwt.claims', true), '')::json ->> 'sub', '')::uuid $$"))
        conn.connection.driver_connection.execute(sql)  # no parameters, so the driver leaves % in the SQL alone
    engine.baton_rls = True


def make_sessionmaker(engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)


def audit(s: Session, project_id: int, actor: str, action: str, target: str = "", detail: str = "") -> None:
    s.add(AuditEvent(project_id=project_id, actor=actor, action=action, target=target, detail=detail))
