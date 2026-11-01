"""Human control: answering gap questions, reviewing claims, resolving conflicts, approving immutable versions."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .db import Acknowledgement, Claim, Conflict, GapQuestion, Member, Project, Version, audit
from .pipeline import bucket_of, max_claim_number
from .verify.sensitive import classify, mask_secrets

CONTRADICTION_FLAG = 0.5


class ReviewError(ValueError):
    """The action is not allowed now. The message is shown to the user."""


def _next_code(s: Session, project_id: int) -> str:
    return f"C{max_claim_number(s, project_id) + 1:03d}"


# --- gap interview ------------------------------------------------------------------------------------------------

def answer_question(s: Session, q: GapQuestion, by: Member, text: str, nli=None) -> Claim:
    """An answer becomes attributed, dated testimony: Confirmed by person, never Verified."""
    text, found = mask_secrets((text or "").strip())
    if not text:
        raise ReviewError("Please write an answer, or pass the question to someone else.")
    if q.status != "open":
        raise ReviewError("This question has already been answered.")
    project = s.get(Project, q.project_id)
    related = s.get(Claim, q.claim_id) if q.claim_id else None
    tags = classify(text)
    claim = Claim(
        project_id=q.project_id, code=_next_code(s, q.project_id), type=_testimony_type(q), subject=related.subject if related else q.kind,
        statement=text, value="", as_of=datetime.now(), audience=q.audience, origin="testimony", label="confirmed",
        label_reason=f"{by.name}'s word, given in the gap interview.", sensitive=bool(tags), sens_tags=tags,
        answered_by=by.name, answered_at=datetime.now(), bucket="decide", review_state="pending",
    )
    s.add(claim)
    s.flush()
    q.status, q.answer, q.answered_by, q.answered_at, q.testimony_claim_id = "answered", text, by.name, datetime.now(), claim.id
    if related is not None and nli is not None and nli.available and q.kind != "ownership":
        # Advisory only. In testing this flagged some consistent answers too, so it says "check", never "wrong".
        # Ownership answers are exempt: "Meera takes this over" is meant to differ from "Arjun is responsible".
        score = nli.score([(related.statement, text)])[0]
        if score.contradiction >= CONTRADICTION_FLAG:
            claim.check_flag = f"This answer may disagree with: “{related.statement}”. Check it."
    if found:
        claim.check_flag = (claim.check_flag + " A credential in this answer was masked.").strip()
    audit(s, q.project_id, by.name, "question_answered", f"Q{q.id}", text[:200])
    _ = project
    return claim


def _testimony_type(q: GapQuestion) -> str:
    return {"ownership": "owner", "credential": "access", "coverage": "next_action", "rationale": "decision",
            "open_question": "event", "unclear": "event"}.get(q.kind, "event")


def reassign_question(s: Session, q: GapQuestion, by: Member, to: Member) -> None:
    """"Not me -> Kabir". A question can only go to someone cleared for the source it came from."""
    if q.status != "open":
        raise ReviewError("This question has already been answered.")
    if to.project_id != q.project_id:
        raise ReviewError("That person is not part of this project.")
    if q.audience not in (to.clearances or []):
        raise ReviewError(f"{to.name} is not cleared to see where this question came from. Choose someone else.")
    q.asked_to_id = to.id
    q.route = "reviewer" if to.role == "reviewer" else ("leaver" if to.role == "leaver" else "teammate")
    audit(s, q.project_id, by.name, "question_reassigned", f"Q{q.id}", f"to {to.name}")


# --- review -------------------------------------------------------------------------------------------------------

def approve_one_pass(s: Session, project: Project, by: Member) -> int:
    claims = s.scalars(select(Claim).where(Claim.project_id == project.id, Claim.bucket == "one_pass",
                                           Claim.review_state == "pending")).all()
    for c in claims:
        c.review_state, c.reviewed_by = "approved", by.name
    audit(s, project.id, by.name, "one_pass_approved", "", f"{len(claims)} routine verified claims")
    return len(claims)


def decide_claim(s: Session, claim: Claim, by: Member, action: str, new_text: str = "") -> None:
    """Approve, reject or edit one claim. An edited or human-approved weak claim is the reviewer's word, not Verified."""
    if claim.label == "conflict":
        raise ReviewError("Resolve this conflict first; choosing a side updates its claims.")
    if action == "approve":
        if claim.label in ("needs_review", "unsupported"):
            if claim.label == "unsupported":
                raise ReviewError("This claim has no real source quote. Reject it, or rewrite it with Edit.")
            claim.label = "confirmed"
            claim.label_reason = f"{by.name} accepted this after review; the automatic check did not confirm it."
            claim.answered_by, claim.answered_at = by.name, datetime.now()
        claim.review_state, claim.reviewed_by = "approved", by.name
    elif action == "reject":
        claim.review_state, claim.reviewed_by = "rejected", by.name
    elif action == "edit":
        text, _ = mask_secrets((new_text or "").strip())
        if not text:
            raise ReviewError("The edited text is empty.")
        if not claim.original_statement:
            claim.original_statement = claim.statement
        claim.statement, claim.edited = text, True
        claim.origin = "reviewer"
        claim.sens_tags = classify(text)
        claim.sensitive = bool(claim.sens_tags)
        claim.label = "withheld" if claim.sensitive else "confirmed"
        claim.label_reason = f"Rewritten by {by.name}. This is the reviewer's word and no longer a source quote."
        claim.answered_by, claim.answered_at = by.name, datetime.now()
        claim.evidence.clear()  # a rewritten claim cannot borrow a receipt that says something else
        claim.review_state, claim.reviewed_by = "approved", by.name
    else:
        raise ReviewError("Unknown action.")
    audit(s, claim.project_id, by.name, f"claim_{action}", claim.code, (new_text or claim.statement)[:200])


def sanitise_claim(s: Session, claim: Claim, by: Member, text: str, audience: str) -> Claim:
    """Publish a safe version of a withheld item (the deadline without the amount) as the reviewer's own statement."""
    text, _ = mask_secrets((text or "").strip())
    if not text:
        raise ReviewError("Write the shareable version first.")
    tags = classify(text)
    if tags:
        raise ReviewError("That text still contains sensitive content (" + ", ".join(tags) + "). Remove it first.")
    new = Claim(project_id=claim.project_id, code=_next_code(s, claim.project_id), type=claim.type, subject=claim.subject,
                statement=text, audience=audience, origin="reviewer", label="confirmed",
                label_reason=f"Shareable version written by {by.name}.", answered_by=by.name, answered_at=datetime.now(),
                as_of=claim.as_of, due=claim.due, bucket="decide", review_state="approved", reviewed_by=by.name)
    s.add(new)
    claim.review_state, claim.reviewed_by = "rejected", by.name  # the original stays withheld
    audit(s, claim.project_id, by.name, "claim_sanitised", claim.code, f"Published as a shareable version for {audience}")
    return new


def resolve_conflict(s: Session, conflict: Conflict, by: Member, choice: str, note: str = "") -> None:
    if choice not in ("a", "b", "neither"):
        raise ReviewError("Choose which side is correct.")
    a = [s.get(Claim, i) for i in conflict.side_a]
    b = [s.get(Claim, i) for i in conflict.side_b]
    winners, losers = (a, b) if choice == "a" else (b, a) if choice == "b" else ([], a + b)
    from .pipeline import _supported

    for c in winners:
        c.review_state, c.reviewed_by = "approved", by.name
        if _supported(c):
            c.label, c.label_reason = "verified", f"Sources disagreed; {by.name} confirmed this side."
        else:
            c.label, c.label_reason = "confirmed", f"Sources disagreed; {by.name} confirmed this side."
            c.answered_by, c.answered_at = by.name, datetime.now()
        c.bucket = "decide"
    for c in losers:
        c.review_state, c.reviewed_by = "rejected", by.name
        c.label, c.label_reason = "superseded", f"{by.name} resolved a conflict against this claim."
    conflict.resolution, conflict.note, conflict.resolved_by, conflict.resolved_at = choice, note.strip(), by.name, datetime.now()
    audit(s, conflict.project_id, by.name, "conflict_resolved", conflict.subject, f"side {choice}. {note}".strip())


def blockers(s: Session, project: Project) -> list[str]:
    """Why a version can't be approved yet. Needs-review claims don't block: they stay hidden unless fixed."""
    out: list[str] = []
    open_conflicts = s.scalar(select(func.count(Conflict.id)).where(Conflict.project_id == project.id, Conflict.resolution == "")) or 0
    if open_conflicts:
        out.append(f"{open_conflicts} conflict(s) still need a decision.")
    pending = s.scalars(select(Claim).where(Claim.project_id == project.id, Claim.review_state == "pending",
                                            Claim.bucket.in_(("one_pass", "decide")))).all()
    one_pass = sum(1 for c in pending if c.bucket == "one_pass")
    decide = sum(1 for c in pending if c.bucket == "decide" and c.label != "conflict")
    if one_pass:
        out.append(f"{one_pass} routine claim(s) still need the one-pass approval.")
    if decide:
        out.append(f"{decide} risky item(s) still need a decision.")
    has_any = s.scalar(select(func.count(Claim.id)).where(Claim.project_id == project.id)) or 0
    if not has_any:
        out.append("There are no claims yet. Run the pipeline first.")
    return out


# --- versions -----------------------------------------------------------------------------------------------------

def _receipt(e) -> dict:
    u = e.unit
    return {"uid": u.uid if u else "", "source": u.source.title if u else "", "author": u.author if u else "",
            "when": u.ts.strftime("%d %b %Y") if u and u.ts else "", "quote": e.quote, "link": u.link if u else "",
            "match": e.match, "entailment": e.entailment, "audience": u.source.audience if u else ""}


def _side_quotes(by_id: dict, ids: list[int]) -> list[dict]:
    """Both receipts stay visible after a conflict is resolved, so a successor can see what was overruled."""
    out = []
    for i in ids:
        for e in by_id[i].evidence if i in by_id else []:
            if e.match != "rejected":
                out.append(_receipt(e))
    return out


def build_snapshot(s: Session, project: Project) -> dict:
    claims = s.scalars(select(Claim).where(Claim.project_id == project.id, Claim.review_state == "approved").order_by(Claim.id)).all()
    conflicts = s.scalars(select(Conflict).where(Conflict.project_id == project.id)).all()
    conflict_view: list[dict] = []
    all_by_id = {c.id: c for c in s.scalars(select(Claim).where(Claim.project_id == project.id)).all()}
    for cf in conflicts:
        winners = cf.side_a if cf.resolution == "a" else cf.side_b if cf.resolution == "b" else []
        losers = [i for i in cf.claim_ids if i not in winners]
        conflict_view.append({
            "subject": cf.subject, "facet": cf.facet, "resolution": cf.resolution, "note": cf.note, "resolved_by": cf.resolved_by,
            "winner_codes": [all_by_id[i].code for i in winners if i in all_by_id],
            "winner_text": [all_by_id[i].statement for i in winners if i in all_by_id],
            "loser_text": [all_by_id[i].statement for i in losers if i in all_by_id],
            "winner_quotes": _side_quotes(all_by_id, winners),
            "loser_quotes": _side_quotes(all_by_id, losers),
            "audiences": sorted({all_by_id[i].audience for i in cf.claim_ids if i in all_by_id}),
            "sensitive": any(all_by_id[i].sensitive for i in cf.claim_ids if i in all_by_id),
        })
    items = [{
        "code": c.code, "type": c.type, "subject": c.subject, "statement": c.statement, "label": c.label, "audience": c.audience,
        "sensitive": c.sensitive, "due": c.due.isoformat() if c.due else None, "as_of": c.as_of.strftime("%Y-%m-%d") if c.as_of else None,
        "origin": c.origin, "answered_by": c.answered_by, "answered_at": c.answered_at.strftime("%d %b %Y") if c.answered_at else "",
        "cliff": c.cliff, "stale": c.stale, "receipts": [_receipt(e) for e in c.evidence if e.match != "rejected"],
    } for c in claims]
    questions = s.scalars(select(GapQuestion).where(GapQuestion.project_id == project.id, GapQuestion.status == "open")).all()
    members = {m.id: m.name for m in s.scalars(select(Member).where(Member.project_id == project.id)).all()}
    missing = [{"id": q.id, "kind": q.kind, "text": q.text, "audience": q.audience, "ask": members.get(q.asked_to_id or 0, "the reviewer")}
               for q in questions]
    return {"claims": items, "conflicts": conflict_view, "missing": missing, "last_day": project.last_day.isoformat(),
            "as_of": project.as_of.isoformat(), "role_title": project.role_title}


def _diff(old: dict | None, new: dict) -> dict:
    # Compared by wording, because claim codes can be reassigned when the pipeline is run again.
    before = [c["statement"] for c in (old or {}).get("claims", [])]
    after = [c["statement"] for c in new["claims"]]
    return {"added": [x for x in after if x not in before], "removed": [x for x in before if x not in after], "changed": []}


def approve_version(s: Session, project: Project, by: Member) -> Version:
    if by.role != "reviewer":
        raise ReviewError("Only the reviewer can approve a handover.")
    problems = blockers(s, project)
    if problems:
        raise ReviewError(" ".join(problems))
    previous = latest_version(s, project.id)
    snapshot = build_snapshot(s, project)
    digest = hashlib.sha256(json.dumps(snapshot, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    if previous is not None and previous.content_hash == digest:
        raise ReviewError("Nothing has changed since the last approved version.")
    v = Version(project_id=project.id, number=(previous.number + 1) if previous else 1, approved_by=by.name, snapshot=snapshot,
                content_hash=digest, diff=_diff(previous.snapshot if previous else None, snapshot))
    s.add(v)
    audit(s, project.id, by.name, "version_approved", f"v{v.number}", f"{len(snapshot['claims'])} claims; sha256 {digest[:12]}")
    return v


def latest_version(s: Session, project_id: int) -> Version | None:
    return s.scalars(select(Version).where(Version.project_id == project_id).order_by(Version.number.desc())).first()


# --- acknowledgement ----------------------------------------------------------------------------------------------

def acknowledge(s: Session, project: Project, version: Version, member: Member, section: str) -> None:
    s.add(Acknowledgement(project_id=project.id, version=version.number, section=section, member_id=member.id, state="ack"))
    audit(s, project.id, member.name, "section_acknowledged", section, f"v{version.number}")


def mark_unclear(s: Session, project: Project, version: Version, member: Member, section: str, text: str) -> GapQuestion:
    """"This is unclear" loops back as a new routed question."""
    text = mask_secrets((text or "").strip())[0]
    if not text:
        raise ReviewError("Please say what is unclear.")
    audience = next((c for c in (member.clearances or []) if c != "public"), "public")
    s.add(Acknowledgement(project_id=project.id, version=version.number, section=section, member_id=member.id, state="unclear", note=text))
    leaver = s.get(Member, project.leaver_id) if project.leaver_id else None
    reviewer = s.get(Member, project.reviewer_id) if project.reviewer_id else None
    target = leaver if leaver and audience in (leaver.clearances or []) else reviewer
    q = GapQuestion(project_id=project.id, kind="unclear", text=f"{member.name} finds this unclear ({section}): {text}", priority=3,
                    route="leaver" if target is leaver else "reviewer", asked_to_id=target.id if target else None, audience=audience,
                    origin_section=section)
    s.add(q)
    audit(s, project.id, member.name, "marked_unclear", section, text[:200])
    return q


_ = bucket_of
