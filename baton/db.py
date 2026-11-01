"""The evidence model (Round 1 plan, section 6.3), as SQLAlchemy tables.

The plan's target is Supabase Postgres with Row-Level Security. This build runs on SQLite through the same models
until credentials exist (see docs/CHANGES_FROM_PLAN.md); `db/supabase_schema.sql` holds the Postgres schema and policies.
"""
from __future__ import annotations

import os
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import JSON, Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker


class Base(DeclarativeBase):
    pass


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
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
    kwargs = {"connect_args": {"check_same_thread": False, "timeout": 30}} if url.startswith("sqlite") else {}
    engine = create_engine(url, **kwargs)
    if url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def _pragmas(conn, _rec):
            cur = conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()
    Base.metadata.create_all(engine)
    return engine


def make_sessionmaker(engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)


def audit(s: Session, project_id: int, actor: str, action: str, target: str = "", detail: str = "") -> None:
    s.add(AuditEvent(project_id=project_id, actor=actor, action=action, target=target, detail=detail))
