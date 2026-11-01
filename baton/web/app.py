"""The web app: one FastAPI service with server-rendered Jinja pages, HTMX for progress, and jobs in the same process.

Sign-in here is a demo stand-in (a "viewing as" picker) until Supabase Auth is connected. See docs/CHANGES_FROM_PLAN.md.
"""
from __future__ import annotations

import logging
import os
import secrets
import threading
from datetime import date, datetime
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from .. import __version__, permissions, review
from ..db import (Acknowledgement, AuditEvent, Claim, Conflict, EvidenceUnit, GapQuestion, Job, Member, Project, Source, Version,
                  audit, make_engine, make_sessionmaker)
from ..demo.loader import load_demo
from ..extract import TYPE_TITLES, ExtractionError, Extractor, ReplayExtractor, default_extractor
from ..handover import SECTION_TITLES, SECTIONS, compose, conflict_line, to_markdown
from ..parsers import ParseError, docx_to_text, fetch_repo, parse_github, parse_notes, parse_trello, parse_whatsapp
from ..pipeline import add_source, run_pipeline
from ..review import ReviewError
from ..verify.models import get_embedder, get_nli
from ..verify.sensitive import TAG_LABELS

log = logging.getLogger(__name__)
HERE = Path(__file__).parent
MAX_UPLOAD = 10 * 1024 * 1024
AUDIENCES = [("public", "Public"), ("tech-team", "Tech team"), ("core-committee", "Core committee")]
LABELS = {"verified": "Verified", "confirmed": "Confirmed by person", "missing": "Missing", "conflict": "Conflict",
          "needs_review": "Needs review", "outdated": "Possibly outdated", "withheld": "Withheld",
          "unsupported": "No real quote", "superseded": "Superseded"}
STEPS = [("connect", "Connect"), ("ledger", "Reconstruct"), ("review", "Review"), ("interview", "Gap interview"), ("handover", "Handover")]


def create_app(database_url: str | None = None, extractor_factory=None, nli=None, embedder=None, inline_jobs: bool = False,
               preload_models: bool = False) -> FastAPI:
    app = FastAPI(title="Baton", version=__version__, docs_url=None, redoc_url=None)
    app.add_middleware(SessionMiddleware, secret_key=os.environ.get("BATON_SECRET") or secrets.token_hex(32), same_site="lax",
                       https_only=os.environ.get("BATON_HTTPS_ONLY", "") == "1", max_age=60 * 60 * 24 * 7)
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    templates = Jinja2Templates(directory=HERE / "templates")
    templates.env.globals.update(LABELS=LABELS, TYPE_TITLES=TYPE_TITLES, SECTIONS=SECTIONS, SECTION_TITLES=SECTION_TITLES,
                                 AUDIENCES=AUDIENCES, AUDIENCE_LABELS=permissions.AUDIENCE_LABELS, TAG_LABELS=TAG_LABELS,
                                 STEPS=STEPS, conflict_line=conflict_line, version=__version__)
    engine = make_engine(database_url)
    Sessions = make_sessionmaker(engine)
    app.state.Sessions = Sessions
    run_lock = threading.Lock()

    with Sessions() as s:  # a restart loses in-process jobs: say so rather than showing "running" forever
        for job in s.scalars(select(Job).where(Job.state.in_(("queued", "running")))).all():
            job.state, job.error, job.finished_at = "failed", "The server restarted during this run. Please run it again.", datetime.now()
        s.commit()

    if preload_models:
        threading.Thread(target=lambda: (get_nli(), get_embedder()), daemon=True).start()

    # --- helpers ------------------------------------------------------------------------------------------------

    def viewer_of(request: Request, s: Session, project: Project) -> Member | None:
        vid = request.session.get(f"viewer_{project.id}")
        m = s.get(Member, vid) if vid else None
        if m is None or m.project_id != project.id:
            m = s.get(Member, project.reviewer_id) if project.reviewer_id else None
            if m:
                request.session[f"viewer_{project.id}"] = m.id
        return m

    def flash(request: Request, text: str, kind: str = "info") -> None:
        request.session.setdefault("flash", []).append({"text": text, "kind": kind})

    def get_project(s: Session, pid: int) -> Project:
        p = s.get(Project, pid)
        if p is None:
            raise HTTPException(404, "Project not found")
        return p

    def require(viewer: Member | None, *roles: str) -> None:
        if viewer is None or viewer.role not in roles:
            who = " or ".join(roles)
            raise HTTPException(403, f"Only the {who} can do this.")

    def page(request: Request, name: str, s: Session, project: Project | None = None, **ctx) -> HTMLResponse:
        messages = request.session.pop("flash", [])
        base = {"request": request, "messages": messages, "project": project, "viewer": None, "members": [], "step": ctx.pop("step", "")}
        if project is not None:
            base["viewer"] = viewer_of(request, s, project)
            base["members"] = s.scalars(select(Member).where(Member.project_id == project.id).order_by(Member.id)).all()
            base["job"] = latest_job(s, project.id)
            base["open_q"] = _open_for(s, project.id, base["viewer"])
            base["has_version"] = review.latest_version(s, project.id) is not None
        return templates.TemplateResponse(request, name, {**base, **ctx})

    def latest_job(s: Session, pid: int) -> Job | None:
        return s.scalars(select(Job).where(Job.project_id == pid).order_by(Job.id.desc())).first()

    def _open_for(s: Session, pid: int, viewer: Member | None) -> int:
        if viewer is None:
            return 0
        return s.scalar(select(func.count(GapQuestion.id)).where(GapQuestion.project_id == pid, GapQuestion.asked_to_id == viewer.id,
                                                                 GapQuestion.status == "open", GapQuestion.route != "triage")) or 0

    def start_job(pid: int) -> None:
        with Sessions() as s:
            running = s.scalars(select(Job).where(Job.project_id == pid, Job.state.in_(("queued", "running")))).first()
            if running:
                return
            job = Job(project_id=pid, state="queued", step="Waiting to start")
            s.add(job)
            s.commit()
            job_id = job.id
        if inline_jobs:
            _run_job(job_id)
        else:
            threading.Thread(target=_run_job, args=(job_id,), daemon=True).start()

    def _run_job(job_id: int) -> None:
        with run_lock, Sessions() as s:  # one pipeline at a time: the models are shared, and the host has two CPUs
            job = s.get(Job, job_id)
            project = s.get(Project, job.project_id)
            job.state = "running"
            s.commit()
            try:
                extractor: Extractor
                if extractor_factory is not None:
                    extractor = extractor_factory(project)
                elif project.is_demo:
                    extractor = ReplayExtractor()
                else:
                    extractor = default_extractor()
                job.stats = run_pipeline(s, project, extractor, job=job, nli=nli, embedder=embedder)
                job.state = "done"
                job.finished_at = datetime.now()
                s.commit()
            except Exception as e:  # any failure must reach the user, not vanish inside a thread
                log.exception("Pipeline failed for project %s", project.id)
                s.rollback()
                job = s.get(Job, job_id)
                job.state, job.finished_at = "failed", datetime.now()
                job.error = str(e) if isinstance(e, (ValueError, ExtractionError)) else f"The run stopped unexpectedly ({type(e).__name__})."
                s.commit()

    def next_code(s: Session, pid: int, prefix: str) -> str:
        n = s.scalar(select(func.count(Source.id)).where(Source.project_id == pid, Source.code.like(f"{prefix}%"))) or 0
        if prefix == "N":
            return f"N{n + 1}"
        return prefix if n == 0 else f"{prefix}{n + 1}"

    # --- landing ------------------------------------------------------------------------------------------------

    @app.get("/healthz")
    def healthz():
        return JSONResponse({"status": "ok", "version": __version__})

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request):
        with Sessions() as s:
            projects = s.scalars(select(Project).order_by(Project.id.desc()).limit(30)).all()
            return page(request, "home.html", s, projects=projects, extractor=default_extractor().label if not extractor_factory else "test")

    @app.post("/demo")
    def demo(request: Request):
        with Sessions() as s:
            p = load_demo(s)
            s.commit()
            pid = p.id
            request.session[f"viewer_{pid}"] = p.leaver_id
        start_job(pid)
        flash(request, "Demo loaded. You are viewing as Arjun, the person leaving. The pipeline is running on 91 synthetic records.")
        return RedirectResponse(f"/p/{pid}/connect", 303)

    @app.get("/new", response_class=HTMLResponse)
    def new_form(request: Request):
        with Sessions() as s:
            return page(request, "new_project.html", s, today=date.today())

    @app.post("/new")
    def new_project(request: Request, name: str = Form(...), role_title: str = Form("Tech lead"), leaver: str = Form(...),
                    successor: str = Form(...), reviewer: str = Form(...), last_day: date = Form(...),
                    confirm_authorised: str = Form("")):
        if not confirm_authorised:
            flash(request, "Please confirm that you are allowed to use the project's data.", "error")
            return RedirectResponse("/new", 303)
        names = [leaver.strip(), successor.strip(), reviewer.strip()]
        if not all(names) or not name.strip():
            flash(request, "Please fill in the project name and all three people.", "error")
            return RedirectResponse("/new", 303)
        with Sessions() as s:
            p = Project(name=name.strip()[:200], role_title=role_title.strip()[:100] or "Tech lead", as_of=date.today(), last_day=last_day)
            s.add(p)
            s.flush()
            team = ["public", "tech-team"]
            people = [(names[0], "Leaving", "leaver", team), (names[1], "Taking over", "successor", team),
                      (names[2], "Reviewer", "reviewer", [a for a, _ in AUDIENCES])]
            created = []
            for who, title, role, clearances in people:
                m = Member(project_id=p.id, name=who[:120], title=title, role=role, aliases=[who, who.split(" ")[0]], clearances=clearances)
                s.add(m)
                created.append(m)
            s.flush()
            p.leaver_id, p.successor_id, p.reviewer_id = created[0].id, created[1].id, created[2].id
            audit(s, p.id, created[2].name, "project_created", p.name)
            s.commit()
            request.session[f"viewer_{p.id}"] = created[0].id
            return RedirectResponse(f"/p/{p.id}/connect", 303)

    @app.post("/p/{pid}/viewer")
    def switch_viewer(pid: int, request: Request, member_id: int = Form(...), back: str = Form("")):
        with Sessions() as s:
            m = s.get(Member, member_id)
            if m is None or m.project_id != pid:
                raise HTTPException(404, "No such person in this project")
            request.session[f"viewer_{pid}"] = m.id
            audit(s, pid, m.name, "viewer_switched", "", "Demo sign-in stand-in")
            s.commit()
        # Only ever redirect within this project's own pages.
        target = back if back.startswith(f"/p/{pid}/") else f"/p/{pid}"
        return RedirectResponse(target, 303)

    @app.get("/p/{pid}")
    def project_home(pid: int, request: Request):
        with Sessions() as s:
            p = get_project(s, pid)
            viewer = viewer_of(request, s, p)
            role = viewer.role if viewer else ""
            if role == "reviewer":
                return RedirectResponse(f"/p/{pid}/review", 303)
            if role == "leaver":
                return RedirectResponse(f"/p/{pid}/interview", 303)
            return RedirectResponse(f"/p/{pid}/handover", 303)

    # --- connect ------------------------------------------------------------------------------------------------

    @app.get("/p/{pid}/connect", response_class=HTMLResponse)
    def connect(pid: int, request: Request):
        with Sessions() as s:
            p = get_project(s, pid)
            viewer = viewer_of(request, s, p)
            sources = s.scalars(select(Source).where(Source.project_id == pid).order_by(Source.id)).all()
            allowed = [x for x in sources if viewer and x.audience in (viewer.clearances or [])]
            hidden = len(sources) - len(allowed)
            counts = {x.id: s.scalar(select(func.count(EvidenceUnit.id)).where(EvidenceUnit.source_id == x.id)) for x in allowed}
            stats = latest_job(s, pid)
            return page(request, "connect.html", s, p, step="connect", sources=allowed, hidden=hidden, counts=counts,
                        run_stats=stats.stats if stats else {}, total=sum(counts.values()))

    @app.post("/p/{pid}/sources/upload")
    async def upload(pid: int, request: Request, kind: str = Form(...), audience: str = Form(...), consent: str = Form(""),
                     authorised: str = Form(""), file: UploadFile = File(...)):
        with Sessions() as s:
            p = get_project(s, pid)
            viewer = viewer_of(request, s, p)
            require(viewer, "reviewer", "leaver")
            back = RedirectResponse(f"/p/{pid}/connect", 303)
            if audience not in dict(AUDIENCES):
                raise HTTPException(400, "Unknown audience")
            if not authorised:
                flash(request, "Please confirm you are allowed to add this source.", "error")
                return back
            data = await file.read(MAX_UPLOAD + 1)
            if len(data) > MAX_UPLOAD:
                flash(request, "That file is larger than the 10 MB limit.", "error")
                return back
            filename = file.filename or "upload"
            try:
                prefix = {"whatsapp": "WA", "trello": "TR", "github": "GH", "notes": "N"}[kind]
            except KeyError:
                raise HTTPException(400, "Unknown source type") from None
            code = next_code(s, pid, prefix)
            try:
                if kind == "notes" and filename.lower().endswith(".docx"):
                    text = docx_to_text(data)
                else:
                    try:
                        text = data.decode("utf-8-sig")
                    except UnicodeDecodeError:
                        raise ParseError("This file is not UTF-8 text. Please upload the original export.") from None
                parsed = {"whatsapp": lambda: parse_whatsapp(text, code), "trello": lambda: parse_trello(text, code),
                          "github": lambda: parse_github(text, code),
                          "notes": lambda: parse_notes(text, code, filename, viewer.name)}[kind]()
            except ParseError as e:
                flash(request, f"Could not read {filename}: {e}", "error")
                audit(s, pid, viewer.name, "source_rejected", filename, str(e))
                s.commit()
                return back
            title = f"{kind.capitalize()}: {parsed.title or filename}"
            add_source(s, p, parsed, kind, audience, viewer.name, consent.strip(), code, title[:200])
            audit(s, pid, viewer.name, "source_added", title, f"{len(parsed.units)} records; audience {audience}")
            s.commit()
            note = f" Skipped {len(parsed.skipped)} line(s); see the skip report below." if parsed.skipped else ""
            flash(request, f"Added {title}: {len(parsed.units)} records.{note} Raw file discarded; only parsed records are kept.")
            return back

    @app.post("/p/{pid}/sources/github")
    def add_github(pid: int, request: Request, url: str = Form(...), token: str = Form(""), audience: str = Form("public"),
                   consent: str = Form(""), authorised: str = Form("")):
        with Sessions() as s:
            p = get_project(s, pid)
            viewer = viewer_of(request, s, p)
            require(viewer, "reviewer", "leaver")
            back = RedirectResponse(f"/p/{pid}/connect", 303)
            if audience not in dict(AUDIENCES):
                raise HTTPException(400, "Unknown audience")
            if not authorised:
                flash(request, "Please confirm you are allowed to add this source.", "error")
                return back
            try:
                code = next_code(s, pid, "GH")
                parsed = parse_github(fetch_repo(url, token.strip() or None), code)
            except ParseError as e:
                flash(request, str(e), "error")
                return back
            title = f"GitHub: {parsed.title}"
            add_source(s, p, parsed, "github", audience, viewer.name, consent.strip(), code, title[:200])
            audit(s, pid, viewer.name, "source_added", title, f"{len(parsed.units)} records; audience {audience}")
            s.commit()
            flash(request, f"Added {title}: {len(parsed.units)} records. The token, if any, was used once and not stored.")
            return back

    @app.get("/p/{pid}/sources/{sid}", response_class=HTMLResponse)
    def source_records(pid: int, sid: int, request: Request):
        with Sessions() as s:
            p = get_project(s, pid)
            viewer = viewer_of(request, s, p)
            src = s.get(Source, sid)
            if src is None or src.project_id != pid:
                raise HTTPException(404, "Source not found")
            if viewer is None or src.audience not in (viewer.clearances or []):
                raise HTTPException(403, "You are not cleared to see this source.")
            units = s.scalars(select(EvidenceUnit).where(EvidenceUnit.source_id == sid).order_by(EvidenceUnit.seq)).all()
            shown = [(u, permissions.unit_visible(u, viewer)) for u in units]
            return page(request, "records.html", s, p, step="connect", source=src, shown=shown)

    @app.post("/p/{pid}/members/{mid}")
    def update_member(pid: int, mid: int, request: Request, clearances: list[str] = Form(default=[])):
        with Sessions() as s:
            p = get_project(s, pid)
            viewer = viewer_of(request, s, p)
            require(viewer, "reviewer")
            m = s.get(Member, mid)
            if m is None or m.project_id != pid:
                raise HTTPException(404, "No such person")
            valid = [c for c in clearances if c in dict(AUDIENCES)]
            m.clearances = sorted({"public", *valid})
            audit(s, pid, viewer.name, "clearances_changed", m.name, ", ".join(m.clearances))
            s.commit()
            flash(request, f"Updated {m.name}'s clearances.")
            return RedirectResponse(f"/p/{pid}/connect", 303)

    @app.post("/p/{pid}/run")
    def run(pid: int, request: Request):
        with Sessions() as s:
            p = get_project(s, pid)
            viewer = viewer_of(request, s, p)
            require(viewer, "reviewer", "leaver")
            has = s.scalar(select(func.count(Source.id)).where(Source.project_id == pid)) or 0
            if not has:
                flash(request, "Add at least one source first.", "error")
                return RedirectResponse(f"/p/{pid}/connect", 303)
        start_job(pid)
        return RedirectResponse(f"/p/{pid}/connect", 303)

    @app.get("/p/{pid}/job", response_class=HTMLResponse)
    def job_status(pid: int, request: Request):
        with Sessions() as s:
            p = get_project(s, pid)
            return templates.TemplateResponse(request, "_job.html", {"job": latest_job(s, pid), "project": p})

    # --- ledger -------------------------------------------------------------------------------------------------

    @app.get("/p/{pid}/ledger", response_class=HTMLResponse)
    def ledger(pid: int, request: Request, label: str = "", type: str = ""):
        with Sessions() as s:
            p = get_project(s, pid)
            viewer = viewer_of(request, s, p)
            if viewer is None or viewer.role not in ("reviewer", "leaver"):
                flash(request, "The working ledger is for the reviewer and the leaver. You see the approved handover here.")
                return RedirectResponse(f"/p/{pid}/handover", 303)
            claims = permissions.visible_claims(s, pid, viewer)
            counts: dict[str, int] = {}
            for c in claims:
                counts[c.label] = counts.get(c.label, 0) + 1
            shown = [c for c in claims if (not label or c.label == label) and (not type or c.type == type)]
            audit(s, pid, viewer.name, "ledger_viewed", "", f"{len(shown)} claims")
            s.commit()
            return page(request, "ledger.html", s, p, step="ledger", claims=shown, counts=counts, active=label, active_type=type, total=len(claims))

    # --- review -------------------------------------------------------------------------------------------------

    @app.get("/p/{pid}/review", response_class=HTMLResponse)
    def review_console(pid: int, request: Request):
        with Sessions() as s:
            p = get_project(s, pid)
            viewer = viewer_of(request, s, p)
            if viewer is None or viewer.role != "reviewer":
                flash(request, "Only the reviewer can use the review console. Switch to the reviewer in the “viewing as” menu to see it.")
                return RedirectResponse(f"/p/{pid}/handover" if viewer and viewer.role != "leaver" else f"/p/{pid}/interview", 303)
            claims = permissions.visible_claims(s, pid, viewer)
            by_id = {c.id: c for c in claims}
            conflicts = s.scalars(select(Conflict).where(Conflict.project_id == pid).order_by(Conflict.id)).all()
            one_pass = [c for c in claims if c.bucket == "one_pass"]
            decide = [c for c in claims if c.bucket == "decide" and c.label not in ("conflict", "superseded")]
            needs = [c for c in claims if c.bucket == "needs_review"]
            questions = s.scalars(select(GapQuestion).where(GapQuestion.project_id == pid, GapQuestion.status == "open",
                                                            GapQuestion.route.in_(("reviewer", "triage"))).order_by(GapQuestion.priority)).all()
            members = {m.id: m for m in s.scalars(select(Member).where(Member.project_id == pid)).all()}
            versions = s.scalars(select(Version).where(Version.project_id == pid).order_by(Version.number.desc())).all()
            q_leaver = s.scalars(select(GapQuestion).where(GapQuestion.project_id == pid, GapQuestion.route.notin_(("reviewer", "triage")))).all()
            return page(request, "review.html", s, p, step="review", by_id=by_id, conflicts=conflicts,
                        one_pass=one_pass, one_pass_pending=sum(1 for c in one_pass if c.review_state == "pending"),
                        decide=decide, needs=needs, questions=questions, member_map=members,
                        blockers=review.blockers(s, p), versions=versions,
                        leaver_open=sum(1 for q in q_leaver if q.status == "open"), leaver_total=len(q_leaver))

    def _review_action(pid: int, request: Request, fn, anchor: str = ""):
        with Sessions() as s:
            p = get_project(s, pid)
            viewer = viewer_of(request, s, p)
            require(viewer, "reviewer")
            try:
                message = fn(s, p, viewer)
                s.commit()
                if message:
                    flash(request, message)
            except ReviewError as e:
                s.rollback()
                flash(request, str(e), "error")
        return RedirectResponse(f"/p/{pid}/review{anchor}", 303)

    @app.post("/p/{pid}/review/one-pass")
    def one_pass(pid: int, request: Request):
        return _review_action(pid, request, lambda s, p, v: f"Approved {review.approve_one_pass(s, p, v)} routine verified claims.")

    def _claim(s: Session, pid: int, code_id: int, viewer: Member) -> Claim:
        c = s.get(Claim, code_id)
        if c is None or c.project_id != pid or not permissions.visible(c, viewer):
            raise HTTPException(404, "Claim not found")
        return c

    @app.post("/p/{pid}/claims/{cid}/decide")
    def decide(pid: int, cid: int, request: Request, action: str = Form(...), text: str = Form("")):
        def go(s, p, v):
            c = _claim(s, pid, cid, v)
            review.decide_claim(s, c, v, action, text)
            return f"{c.code}: {action} recorded."
        return _review_action(pid, request, go, "#decide")

    @app.post("/p/{pid}/claims/{cid}/sanitise")
    def sanitise(pid: int, cid: int, request: Request, text: str = Form(""), audience: str = Form("tech-team")):
        def go(s, p, v):
            if audience not in dict(AUDIENCES):
                raise ReviewError("Unknown audience.")
            c = _claim(s, pid, cid, v)
            review.sanitise_claim(s, c, v, text, audience)
            return "Published a shareable version. The original stays withheld."
        return _review_action(pid, request, go, "#decide")

    @app.post("/p/{pid}/conflicts/{cfid}/resolve")
    def resolve(pid: int, cfid: int, request: Request, choice: str = Form(...), note: str = Form("")):
        def go(s, p, v):
            cf = s.get(Conflict, cfid)
            if cf is None or cf.project_id != pid:
                raise HTTPException(404, "Conflict not found")
            review.resolve_conflict(s, cf, v, choice, note)
            return "Conflict resolved. Both receipts stay on record."
        return _review_action(pid, request, go, "#conflicts")

    @app.post("/p/{pid}/questions/{qid}/dismiss")
    def dismiss(pid: int, qid: int, request: Request):
        def go(s, p, v):
            q = s.get(GapQuestion, qid)
            if q is None or q.project_id != pid:
                raise HTTPException(404, "Question not found")
            q.status = "dismissed"
            audit(s, pid, v.name, "question_dismissed", f"Q{q.id}", q.text[:200])
            return "Question removed from the handover."
        return _review_action(pid, request, go, "#questions")

    @app.post("/p/{pid}/approve")
    def approve(pid: int, request: Request):
        def go(s, p, v):
            ver = review.approve_version(s, p, v)
            return f"Approved version {ver.number}. The handover is now shared."
        return _review_action(pid, request, go)

    # --- gap interview ------------------------------------------------------------------------------------------

    @app.get("/p/{pid}/interview", response_class=HTMLResponse)
    def interview(pid: int, request: Request):
        with Sessions() as s:
            p = get_project(s, pid)
            viewer = viewer_of(request, s, p)
            mine = s.scalars(select(GapQuestion).where(GapQuestion.project_id == pid, GapQuestion.asked_to_id == (viewer.id if viewer else -1),
                                                       GapQuestion.route != "triage").order_by(GapQuestion.priority, GapQuestion.id)).all()
            everyone = s.scalars(select(Member).where(Member.project_id == pid)).all()
            people = [m for m in everyone if viewer is None or m.id != viewer.id]
            related = {q.id: s.get(Claim, q.claim_id) for q in mine if q.claim_id}
            return page(request, "interview.html", s, p, step="interview", questions=mine, people=people, related=related,
                        open_count=sum(1 for q in mine if q.status == "open"))

    def _my_question(s: Session, pid: int, qid: int, viewer: Member | None) -> GapQuestion:
        q = s.get(GapQuestion, qid)
        if q is None or q.project_id != pid:
            raise HTTPException(404, "Question not found")
        if viewer is None or (q.asked_to_id != viewer.id and viewer.role != "reviewer"):
            raise HTTPException(403, "This question was not sent to you.")
        return q

    @app.post("/p/{pid}/questions/{qid}/answer")
    def answer(pid: int, qid: int, request: Request, text: str = Form(""), next: str = Form("")):
        with Sessions() as s:
            p = get_project(s, pid)
            viewer = viewer_of(request, s, p)
            q = _my_question(s, pid, qid, viewer)
            try:
                review.answer_question(s, q, viewer, text, nli if nli is not None else get_nli())
                s.commit()
                flash(request, "Saved as your word: dated, attributed, and shown as Confirmed by person.")
            except ReviewError as e:
                s.rollback()
                flash(request, str(e), "error")
        target = "review" if next == "review" else "interview"
        return RedirectResponse(f"/p/{pid}/{target}", 303)

    @app.post("/p/{pid}/questions/{qid}/reassign")
    def reassign(pid: int, qid: int, request: Request, to: int = Form(...)):
        with Sessions() as s:
            p = get_project(s, pid)
            viewer = viewer_of(request, s, p)
            q = _my_question(s, pid, qid, viewer)
            target = s.get(Member, to)
            if target is None:
                raise HTTPException(404, "No such person")
            try:
                review.reassign_question(s, q, viewer, target)
                s.commit()
                flash(request, f"Passed to {target.name}.")
            except ReviewError as e:
                s.rollback()
                flash(request, str(e), "error")
        return RedirectResponse(f"/p/{pid}/interview", 303)

    # --- handover -----------------------------------------------------------------------------------------------

    @app.get("/p/{pid}/handover", response_class=HTMLResponse)
    def handover(pid: int, request: Request):
        with Sessions() as s:
            p = get_project(s, pid)
            viewer = viewer_of(request, s, p)
            version = review.latest_version(s, pid)
            if version is None:
                return page(request, "handover.html", s, p, step="handover", h=None, acks={}, unclear={})
            h = compose(version, viewer)
            acks_rows = s.scalars(select(Acknowledgement).where(Acknowledgement.project_id == pid, Acknowledgement.version == version.number,
                                                                Acknowledgement.member_id == (viewer.id if viewer else -1))).all()
            acks = {a.section: a for a in acks_rows if a.state == "ack"}
            unclear = {a.section: a for a in acks_rows if a.state == "unclear"}
            audit(s, pid, viewer.name if viewer else "anonymous", "handover_viewed", f"v{version.number}", f"{h.total_items} items visible")
            s.commit()
            return page(request, "handover.html", s, p, step="handover", h=h, acks=acks, unclear=unclear)

    @app.post("/p/{pid}/ack/{section}")
    def ack(pid: int, section: str, request: Request):
        if section not in SECTION_TITLES:
            raise HTTPException(404, "Unknown section")
        with Sessions() as s:
            p = get_project(s, pid)
            viewer = viewer_of(request, s, p)
            version = review.latest_version(s, pid)
            if version is None or viewer is None:
                raise HTTPException(400, "There is no approved handover yet.")
            review.acknowledge(s, p, version, viewer, section)
            s.commit()
        return RedirectResponse(f"/p/{pid}/handover#{section}", 303)

    @app.post("/p/{pid}/unclear/{section}")
    def unclear(pid: int, section: str, request: Request, text: str = Form("")):
        if section not in SECTION_TITLES:
            raise HTTPException(404, "Unknown section")
        with Sessions() as s:
            p = get_project(s, pid)
            viewer = viewer_of(request, s, p)
            version = review.latest_version(s, pid)
            if version is None or viewer is None:
                raise HTTPException(400, "There is no approved handover yet.")
            try:
                review.mark_unclear(s, p, version, viewer, section, text)
                s.commit()
                flash(request, "Sent as a new question. It will come back in the next version of the handover.")
            except ReviewError as e:
                s.rollback()
                flash(request, str(e), "error")
        return RedirectResponse(f"/p/{pid}/handover#{section}", 303)

    @app.get("/p/{pid}/export.md")
    def export(pid: int, request: Request):
        with Sessions() as s:
            p = get_project(s, pid)
            viewer = viewer_of(request, s, p)
            version = review.latest_version(s, pid)
            if version is None:
                raise HTTPException(404, "There is no approved handover yet.")
            text = to_markdown(compose(version, viewer), p.name, viewer)
            audit(s, pid, viewer.name if viewer else "anonymous", "handover_exported", f"v{version.number}")
            s.commit()
            return PlainTextResponse(text, media_type="text/markdown; charset=utf-8",
                                     headers={"Content-Disposition": f'attachment; filename="handover-v{version.number}.md"'})

    @app.get("/p/{pid}/audit", response_class=HTMLResponse)
    def audit_log(pid: int, request: Request):
        with Sessions() as s:
            p = get_project(s, pid)
            viewer = viewer_of(request, s, p)
            require(viewer, "reviewer")
            events = s.scalars(select(AuditEvent).where(AuditEvent.project_id == pid).order_by(AuditEvent.id.desc()).limit(500)).all()
            return page(request, "audit.html", s, p, step="", events=events)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        return templates.TemplateResponse(request, "error.html", {"status": exc.status_code, "detail": exc.detail, "project": None,
                                                                  "viewer": None, "members": [], "messages": [], "step": ""},
                                          status_code=exc.status_code)

    return app


def get_app() -> FastAPI:
    logging.basicConfig(level=logging.INFO)
    return create_app(preload_models=os.environ.get("BATON_PRELOAD", "1") == "1")
