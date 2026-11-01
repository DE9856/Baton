"""The pipeline: extract, ground, support, reconcile, guard, raise gaps. Labels are computed by rules here."""
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Callable

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .db import Claim, ClaimEvidence, Conflict, EvidenceUnit, GapQuestion, Job, Member, Project, Source, audit
from .extract import ChunkUnit, ExtractionError, Extractor
from .parsers import ParseResult
from .verify import conflicts as cf
from .verify import gaps as gp
from .verify.dates import find_dates
from .verify.grounding import ground_quote
from .verify.models import get_embedder, get_nli
from .verify.sensitive import classify, mask_fields, mask_secrets
from .verify.value_guard import check_values

log = logging.getLogger(__name__)

SUPPORT_THRESHOLD = 0.5
_WHEN = "%d %b %Y %H:%M"


def add_source(s: Session, project: Project, parsed: ParseResult, type_: str, audience: str, authorised_by: str,
               consent_note: str, code: str, title: str | None = None) -> Source:
    """Store parsed records. Secrets are masked here, before anything is written or sent to a model."""
    source = Source(project_id=project.id, code=code, type=type_, title=title or parsed.title or type_,
                    audience=audience, authorised_by=authorised_by, consent_note=consent_note,
                    skipped=[vars(x) for x in parsed.skipped], notes=list(parsed.notes))
    s.add(source)
    s.flush()
    for seq, unit in enumerate(parsed.units):
        masked, _ = mask_secrets(unit.text)
        s.add(EvidenceUnit(project_id=project.id, source_id=source.id, uid=unit.uid, seq=seq, author=unit.author,
                           ts=unit.ts, text=masked, link=unit.link, kind=unit.kind, sensitivity=classify(masked)))
    _merge_roster(s, project, parsed)
    return source


def _merge_roster(s: Session, project: Project, parsed: ParseResult) -> None:
    """People are matched across sources by alias: arjun-m = "Arjun" = "Arjun Mehta"."""
    members = s.scalars(select(Member).where(Member.project_id == project.id)).all()
    for hint in parsed.roster:
        names = [n for n in (hint.get("aliases") or [hint.get("name")]) if n]
        if not names:
            continue
        match = None
        for m in members:
            existing = {a.lower() for a in (m.aliases or [])} | {m.name.lower()}
            first_names = {re.split(r"[\s_-]", a)[0] for a in existing}
            if any(n.lower() in existing or re.split(r"[\s_-]", n.lower())[0] in first_names for n in names):
                match = m
                break
        if match:
            match.aliases = sorted({*(match.aliases or []), *names})
        elif not project.is_demo:
            base = {"public"}
            member = Member(project_id=project.id, name=names[0], role="teammate", aliases=names,
                            clearances=sorted(base), title="Teammate")
            s.add(member)
            members = [*members, member]


def max_claim_number(s: Session, project_id: int) -> int:
    codes = s.scalars(select(Claim.code).where(Claim.project_id == project_id)).all()
    return max((int(c[1:]) for c in codes if c[1:].isdigit()), default=0)


def _question_key(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9 ]", "", text.lower()).split())


def _chunks(units: list[EvidenceUnit], size: int | None):
    if not size:
        yield units
        return
    for i in range(0, len(units), size):
        yield units[i:i + size]


def premise_for(unit: EvidenceUnit, previous: EvidenceUnit | None) -> str:
    """Speaker + text + the previous chat message, so "I will" can be understood."""
    me = f"{unit.author}: {unit.text}"
    if previous is not None and unit.kind == "chat":
        return f"{previous.author}: {previous.text}\n{me}"
    return me


def support_score(nli, items: list[tuple[list[str], str]]) -> list[float]:
    """Entailment for each (premise variants, statement): the best score across its variants."""
    flat = [(p, st) for variants, st in items for p in variants]
    scores = iter(nli.score(flat)) if flat else iter(())
    return [max((next(scores).entailment for _ in variants), default=0.0) for variants, _ in items]


def premise_variants(unit: EvidenceUnit, previous: EvidenceUnit | None, quote: str) -> list[str]:
    """Long records confuse the classifier, so it also sees the exact sentence the quote came from."""
    variants = [premise_for(unit, previous), f"{unit.author}: {quote}"]
    return list(dict.fromkeys(variants))


def _claim_due(claim: Claim, evidence_text: str, anchor) -> None:
    """Dates are read from the verified quote and anchored to the record's time, never taken from the model."""
    if claim.type not in ("deadline", "next_action", "status", "decision"):
        return
    found = [d for d in find_dates(evidence_text, anchor)]
    if found:
        claim.due = max(found, key=lambda d: d.day).day if claim.type == "deadline" else None


def run_pipeline(s: Session, project: Project, extractor: Extractor, job: Job | None = None,
                 progress: Callable[[str, int], None] | None = None, nli=None, embedder=None) -> dict:
    """Re-runnable: claims, conflicts and open questions from an earlier run are replaced."""
    def step(text: str, pct: int) -> None:
        log.info("[project %s] %s", project.id, text)
        if job is not None:
            job.step, job.progress = text, pct
            s.commit()
        if progress:
            progress(text, pct)

    stats: dict = {"extractor": extractor.label, "chunks": 0, "failed_chunks": [], "extraction_errors": []}
    project.extractor = extractor.label
    # People's words survive a re-run: answers, and anything a reviewer wrote. Only machine output is replaced.
    s.execute(delete(Conflict).where(Conflict.project_id == project.id))
    s.execute(delete(GapQuestion).where(GapQuestion.project_id == project.id, GapQuestion.status != "answered"))
    for c in s.scalars(select(Claim).where(Claim.project_id == project.id, Claim.origin.in_(("extracted", "guard")))).all():
        s.delete(c)
    s.flush()
    answered = {_question_key(q.text) for q in s.scalars(select(GapQuestion).where(GapQuestion.project_id == project.id)).all()}

    sources = s.scalars(select(Source).where(Source.project_id == project.id).order_by(Source.id)).all()
    if not sources:
        raise ValueError("Add at least one source before running the pipeline.")
    members = s.scalars(select(Member).where(Member.project_id == project.id)).all()
    roster = [sorted({m.name, *(m.aliases or [])}) for m in members]
    leaver = s.get(Member, project.leaver_id) if project.leaver_id else None
    reviewer = s.get(Member, project.reviewer_id) if project.reviewer_id else None
    leaver_names = sorted({leaver.name, *(leaver.aliases or [])}) if leaver else []

    # --- 1. Extract, one chunk of one source at a time, and 2. ground each quote.
    step("Extracting claims", 5)
    counter = max_claim_number(s, project.id)
    claims: list[Claim] = []
    premises: dict[int, str] = {}
    variants: dict[int, list[str]] = {}
    total_sources = len(sources)
    for si, source in enumerate(sources):
        units = s.scalars(select(EvidenceUnit).where(EvidenceUnit.source_id == source.id).order_by(EvidenceUnit.seq)).all()
        by_uid = {u.uid: u for u in units}
        prev_map = {u.id: (units[i - 1] if i else None) for i, u in enumerate(units)}
        for chunk in _chunks(units, extractor.chunk_size):
            stats["chunks"] += 1
            view = [ChunkUnit(u.uid, u.author, u.ts.strftime(_WHEN) if u.ts else "undated", u.text) for u in chunk]
            try:
                raw = extractor.extract(view, source.type, source.code)
            except ExtractionError as e:
                stats["failed_chunks"].append(f"{chunk[0].uid} to {chunk[-1].uid}")
                stats["extraction_errors"].append(str(e))
                continue
            chunk_text = {u.uid: u.text for u in chunk}
            for rc in raw:
                counter += 1
                # A model may repeat a secret it saw earlier, or be replayed from a run that saw the raw value.
                raw_evidence = rc.evidence or [{"id": "", "quote": ""}]
                masked = mask_fields(rc.statement, rc.subject, rc.value or "", *[ev.get("quote", "") for ev in raw_evidence])
                statement, subject, value, quotes = masked[0], masked[1], masked[2], masked[3:]
                claim = Claim(project_id=project.id, code=f"C{counter:03d}", type=rc.type, subject=subject[:300],
                              statement=statement, value=value[:300], source_id=source.id,
                              audience=source.audience, origin="extracted")
                s.add(claim)
                best: EvidenceUnit | None = None
                for ev, quote in zip(raw_evidence, quotes):
                    result = ground_quote(quote, ev.get("id", ""), chunk_text)
                    unit = by_uid.get(result.unit_uid) if result.unit_uid else None
                    claim.evidence.append(ClaimEvidence(unit=unit, quote=quote, match=result.match, match_reason=result.reason))
                    best = best or unit
                if best is not None:
                    claim.as_of = best.ts
                    premises[id(claim)] = premise_for(best, prev_map.get(best.id))
                    grounded_quote = next(e.quote for e in claim.evidence if e.unit is best)
                    variants[id(claim)] = premise_variants(best, prev_map.get(best.id), grounded_quote)
                claims.append(claim)
        step(f"Extracted claims from {source.title}", 5 + int(30 * (si + 1) / total_sources))
    s.flush()
    stats["claims"] = len(claims)
    stats["grounded"] = sum(1 for c in claims if any(e.match != "rejected" for e in c.evidence))
    stats["reanchored"] = sum(1 for c in claims if any("reanchored" in e.match for e in c.evidence))

    # --- 3. Support: NLI + value guard.
    step("Checking that each quote supports its claim", 40)
    nli = nli or get_nli()
    stats["nli_available"] = nli.available
    grounded = [c for c in claims if id(c) in premises]
    started = datetime.now()
    scores = support_score(nli, [(variants[id(c)], c.statement) for c in grounded])
    stats["nli_seconds"] = round((datetime.now() - started).total_seconds(), 1)
    for c, score in zip(grounded, scores):
        e = next(x for x in c.evidence if x.match != "rejected")
        e.entailment = round(score, 3)
        anchor = c.as_of.date() if c.as_of else project.as_of
        guard = check_values(c.statement, premises[id(c)], anchor, roster)
        e.guard_missing = guard.missing
        _claim_due(c, f"{e.quote} {c.statement}", anchor)

    # --- 5. Credential tasks come from the records themselves: a model may skip the secret or never mention it.
    for source in sources:
        for u in s.scalars(select(EvidenceUnit).where(EvidenceUnit.source_id == source.id)).all():
            if "secret" in (u.sensitivity or []):
                counter += 1
                when = f" on {u.ts.day} {u.ts.strftime('%b')}" if u.ts else ""
                task = Claim(project_id=project.id, code=f"C{counter:03d}", type="access", subject=f"credential in {source.title}",
                             statement=f"A credential was shared in {source.title}{when}. Rotate it and move it to a password "
                                       "manager (value withheld).",
                             value="rotate", source_id=source.id, audience=source.audience, origin="guard", as_of=u.ts)
                task.evidence.append(ClaimEvidence(unit=u, quote=u.text, match="exact", match_reason="found by the sensitive guard",
                                                   entailment=1.0))
                s.add(task)
                claims.append(task)
    s.flush()

    # --- Guard runs before labels, because sensitive claims are withheld.
    step("Looking for secrets and personal data", 55)
    for c in claims:
        if c.origin == "guard":
            continue  # a rotation task carries only the masked record, so it can be shown
        ev_text = " ".join(e.quote for e in c.evidence)
        unit_tags = {t for e in c.evidence if e.unit is not None and e.match != "rejected" for t in (e.unit.sensitivity or [])}
        quote_tags = set(classify(f"{c.statement} {ev_text}"))
        # A quote that avoids the sensitive part of its record may be shared: only the quote is ever shown.
        c.sens_tags = sorted(quote_tags | (unit_tags & {"secret"} & quote_tags))
        c.sensitive = bool(c.sens_tags)
    stats["sensitive"] = sum(1 for c in claims if c.sensitive)

    # --- First-pass labels, needed before conflict detection and gap questions.
    today = project.as_of
    for c in claims:
        _label(c, nli.available)
        if c.as_of is not None and c.label == "verified" and cf.is_stale(c.value, c.statement, c.as_of, today):
            c.stale = True
            c.label, c.label_reason = "outdated", "No supporting evidence in 30+ days."

    # --- 4. Reconcile: candidates, NLI, facet check.
    step("Looking for conflicts between sources", 65)
    conflict_input = [c for c in claims if id(c) in premises and not c.sensitive and c.label != "unsupported"]
    views = [
        cf.ConflictClaim(c.id, c.type, c.subject, c.statement, c.value or "", c.as_of,
                         next((e.quote for e in c.evidence if e.match != "rejected"), ""),
                         _context(c), next((e.unit.uid for e in c.evidence if e.unit), ""))
        for c in conflict_input
    ]
    embedder = embedder if embedder is not None else get_embedder()
    confirmed = cf.find_cards(views, nli, embedder, roster)
    stats["confirmed_cards"] = len(confirmed)
    groups = cf.merge_cards(confirmed)
    cf.expand_groups(groups, views)
    by_id = {c.id: c for c in claims}
    for g in groups:
        a_ids = [x.id for x in g.side_a]
        b_ids = [x.id for x in g.side_b]
        s.add(Conflict(project_id=project.id, claim_ids=a_ids + b_ids, side_a=a_ids, side_b=b_ids, facet=g.facet,
                       nli=round(g.nli, 3), newer_side="b", subject=g.subject))
        for cid in a_ids + b_ids:
            by_id[cid].label = "conflict"
            by_id[cid].label_reason = f"Sources disagree ({g.facet})."
    stats["conflicts"] = len(groups)

    # --- 6. Gaps and routing.
    step("Finding gaps and routing questions", 80)
    def _following(c: Claim):
        unit = next((e.unit for e in c.evidence if e.unit), None)
        if unit is None or unit.kind != "chat":
            return ("", "")
        after = s.scalars(select(EvidenceUnit).where(EvidenceUnit.source_id == unit.source_id, EvidenceUnit.seq == unit.seq + 1)).first()
        return (after.author, after.text) if after else ("", "")

    gap_claims = [gp.GapClaim(c.id, c.type, c.subject, c.statement, next((e.quote for e in c.evidence if e.match != "rejected"), ""),
                              next((e.unit.author for e in c.evidence if e.unit), ""), c.audience, c.label, c.due, c.sensitive,
                              c.origin == "guard", *_following(c)) for c in claims]
    if leaver:
        owned = {gp._norm_subject(g.subject) for g in gap_claims if g.type == "owner" and gp.involves_leaver(g, leaver_names)}
        for g, c in zip(gap_claims, claims):
            c.cliff = c.label != "unsupported" and not c.sensitive and gp.is_cliff(g, leaver_names, project.last_day, owned)
    raw_questions = gp.raise_questions(gap_claims, leaver_names, leaver.clearances if leaver else [], project.last_day,
                                       project.role_title if project.role_title in gp.ROLE_TEMPLATES else "default")
    unique, counts = gp.route_questions(raw_questions)
    for q in unique:
        if _question_key(q.text) in answered:
            continue  # already answered before this re-run
        asked = None
        if q.route == "leaver" and leaver:
            asked = leaver.id
        elif q.route == "reviewer" and reviewer:
            asked = reviewer.id
        elif q.route == "triage" and reviewer:
            asked = reviewer.id
        s.add(GapQuestion(project_id=project.id, kind=q.kind, text=q.text, priority=q.priority, route=q.route,
                          asked_to_id=asked, claim_id=q.claim_id, audience=q.audience))
    stats["questions"] = counts
    for g in groups:
        stats["questions"]["reviewer"] = stats["questions"].get("reviewer", 0) + 1  # one decision per conflict

    # --- Review buckets: routine claims in one pass, risky ones decided individually.
    for c in claims:
        c.bucket = bucket_of(c)
        c.review_state = "pending"
    stats["bucket_one_pass"] = sum(1 for c in claims if c.bucket == "one_pass")
    stats["bucket_decide"] = sum(1 for c in claims if c.bucket == "decide")
    stats["bucket_needs_review"] = sum(1 for c in claims if c.bucket == "needs_review")
    stats["verified"] = sum(1 for c in claims if c.label == "verified" or (c.label in ("conflict", "outdated") and _supported(c)))
    stats["needs_review"] = sum(1 for c in claims if c.label == "needs_review")
    stats["unsupported"] = sum(1 for c in claims if c.label == "unsupported")
    stats["incomplete"] = bool(stats["failed_chunks"])
    audit(s, project.id, "Baton", "pipeline_run", extractor.label,
          f"{stats['claims']} claims, {stats['conflicts']} conflicts, {counts.get('leaver', 0)} questions for the leaver")
    step("Done", 100)
    return stats


def _context(c: Claim) -> str:
    """The title of the record a claim came from. It names the work even when the sentence doesn't."""
    for e in c.evidence:
        if e.unit is not None:
            m = re.match(r"(?:Card|Comment on card|Issue #\d+|PR #\d+|Comment on \w+ #\d+:?)\s*'([^']+)'", e.unit.text)
            if m:
                return m.group(1)
    return ""


def _supported(c: Claim) -> bool:
    for e in c.evidence:
        if e.match != "rejected" and (e.entailment or 0) >= SUPPORT_THRESHOLD and not e.guard_missing:
            return True
    return False


def _label(c: Claim, nli_available: bool = True) -> None:
    """Rules only. The model's confidence plays no part, and each stage can only downgrade a claim."""
    ok = [e for e in c.evidence if e.match != "rejected"]
    if not ok:
        c.label, c.label_reason = "unsupported", ok and "" or (c.evidence[0].match_reason if c.evidence else "no evidence")
        return
    if not nli_available:
        c.label, c.label_reason = "needs_review", "The support check did not run because its model is unavailable."
        return
    if not _supported(c):
        e = ok[0]
        reasons = []
        if (e.entailment or 0) < SUPPORT_THRESHOLD:
            reasons.append(f"the quote does not clearly support the claim (entailment {e.entailment:.2f})")
        if e.guard_missing:
            reasons.append("values not found in the evidence: " + ", ".join(map(str, e.guard_missing)))
        c.label, c.label_reason = "needs_review", "; ".join(reasons).capitalize() + "."
        return
    if c.sensitive:
        c.label, c.label_reason = "withheld", "Contains " + ", ".join(c.sens_tags) + "."
        return
    c.label, c.label_reason = "verified", "Quote matched by code, supported by the NLI check, values present."


def bucket_of(c: Claim) -> str:
    if c.label in ("needs_review", "unsupported"):
        return "needs_review"
    if c.label in ("conflict", "withheld", "outdated", "confirmed") or c.cliff or c.sensitive:
        return "decide"
    return "one_pass"
