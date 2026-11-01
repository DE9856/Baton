"""The pipeline: extract, ground, support, guard, reconcile, raise gaps. Labels are computed by rules here.

`run_pipeline` rebuilds everything. `run_incremental` (Living handover) processes only new records and leaves
approved claims alone unless new evidence disagrees with them.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
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
               consent_note: str, code: str, title: str | None = None, origin_url: str = "") -> Source:
    """Store parsed records. Secrets are masked here, before anything is written or sent to a model."""
    source = Source(project_id=project.id, code=code, type=type_, title=title or parsed.title or type_,
                    audience=audience, authorised_by=authorised_by, consent_note=consent_note, origin_url=origin_url,
                    last_synced_at=datetime.now(), skipped=[vars(x) for x in parsed.skipped], notes=list(parsed.notes))
    s.add(source)
    s.flush()
    for seq, unit in enumerate(parsed.units):
        masked, _ = mask_secrets(unit.text)
        s.add(EvidenceUnit(project_id=project.id, source_id=source.id, uid=unit.uid, seq=seq, author=unit.author,
                           ts=unit.ts, text=masked, link=unit.link, kind=unit.kind, sensitivity=classify(masked)))
    merge_roster(s, project, parsed)
    return source


def merge_roster(s: Session, project: Project, parsed: ParseResult) -> None:
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
            member = Member(project_id=project.id, name=names[0], role="teammate", aliases=names,
                            clearances=["public"], title="Teammate")
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
    if claim.type != "deadline":
        return
    found = find_dates(evidence_text, anchor)
    if found:
        claim.due = max(found, key=lambda d: d.day).day


@dataclass
class Run:
    """What one pipeline run knows and has produced so far."""

    s: Session
    project: Project
    extractor: Extractor
    step: Callable[[str, int], None]
    nli: object
    embedder: object
    stats: dict
    roster: list[list[str]] = field(default_factory=list)
    leaver: Member | None = None
    reviewer: Member | None = None
    leaver_names: list[str] = field(default_factory=list)
    counter: int = 0
    premises: dict[int, str] = field(default_factory=dict)
    variants: dict[int, list[str]] = field(default_factory=dict)


def _start(s, project, extractor, job, progress, nli, embedder, extra: dict | None = None) -> Run:
    def step(text: str, pct: int) -> None:
        log.info("[project %s] %s", project.id, text)
        if job is not None:
            job.step, job.progress = text, pct
            # Commit as it goes: a single long transaction would block other work on SQLite. Claims start hidden
            # (Needs review), and a failed run is cleaned up by the caller.
            s.commit()
        if progress:
            progress(text, pct)

    members = s.scalars(select(Member).where(Member.project_id == project.id)).all()
    leaver = s.get(Member, project.leaver_id) if project.leaver_id else None
    return Run(s, project, extractor, step, nli or get_nli(), embedder if embedder is not None else get_embedder(),
               {"extractor": extractor.label, "chunks": 0, "failed_chunks": [], "extraction_errors": [], **(extra or {})},
               roster=[sorted({m.name, *(m.aliases or [])}) for m in members], leaver=leaver,
               reviewer=s.get(Member, project.reviewer_id) if project.reviewer_id else None,
               leaver_names=sorted({leaver.name, *(leaver.aliases or [])}) if leaver else [],
               counter=max_claim_number(s, project.id))


def _extract(run: Run, source: Source, only: set[int] | None = None) -> list[Claim]:
    """One chunk of one source at a time, grounding each quote. `only` limits extraction to those record ids."""
    s = run.s
    units = s.scalars(select(EvidenceUnit).where(EvidenceUnit.source_id == source.id, EvidenceUnit.retired.is_(False))
                      .order_by(EvidenceUnit.seq)).all()
    by_uid = {u.uid: u for u in units}
    prev_map = {u.id: (units[i - 1] if i else None) for i, u in enumerate(units)}
    todo = [u for u in units if only is None or u.id in only]
    claims: list[Claim] = []
    for chunk in _chunks(todo, run.extractor.chunk_size):
        if not chunk:
            continue
        run.stats["chunks"] += 1
        view = [ChunkUnit(u.uid, u.author, u.ts.strftime(_WHEN) if u.ts else "undated", u.text) for u in chunk]
        try:
            raw = run.extractor.extract(view, source.type, source.code)
        except ExtractionError as e:
            run.stats["failed_chunks"].append(f"{chunk[0].uid} to {chunk[-1].uid}")
            run.stats["extraction_errors"].append(str(e))
            continue
        chunk_text = {u.uid: u.text for u in chunk}
        for rc in raw:
            run.counter += 1
            # A model may repeat a secret it saw earlier, or be replayed from a run that saw the raw value.
            raw_evidence = rc.evidence or [{"id": "", "quote": ""}]
            masked = mask_fields(rc.statement, rc.subject, rc.value or "", *[ev.get("quote", "") for ev in raw_evidence])
            statement, subject, value, quotes = masked[0], masked[1], masked[2], masked[3:]
            claim = Claim(project_id=run.project.id, code=f"C{run.counter:03d}", type=rc.type, subject=subject[:300],
                          statement=statement, value=value[:300], source_id=source.id, audience=source.audience, origin="extracted")
            s.add(claim)
            best: EvidenceUnit | None = None
            for ev, quote in zip(raw_evidence, quotes):
                result = ground_quote(quote, ev.get("id", ""), chunk_text)
                unit = by_uid.get(result.unit_uid) if result.unit_uid else None
                claim.evidence.append(ClaimEvidence(unit=unit, quote=quote, match=result.match, match_reason=result.reason))
                best = best or unit
            if best is not None:
                claim.as_of = best.ts
                run.premises[id(claim)] = premise_for(best, prev_map.get(best.id))
                grounded_quote = next(e.quote for e in claim.evidence if e.unit is best)
                run.variants[id(claim)] = premise_variants(best, prev_map.get(best.id), grounded_quote)
            claims.append(claim)
    return claims


def _support(run: Run, claims: list[Claim]) -> None:
    """NLI + value guard on each grounded claim."""
    run.stats["nli_available"] = run.nli.available
    grounded = [c for c in claims if id(c) in run.premises]
    started = datetime.now()
    scores = support_score(run.nli, [(run.variants[id(c)], c.statement) for c in grounded])
    run.stats["nli_seconds"] = round((datetime.now() - started).total_seconds(), 1)
    for c, score in zip(grounded, scores):
        e = next(x for x in c.evidence if x.match != "rejected")
        e.entailment = round(score, 3)
        anchor = c.as_of.date() if c.as_of else run.project.as_of
        e.guard_missing = check_values(c.statement, run.premises[id(c)], anchor, run.roster).missing
        _claim_due(c, f"{e.quote} {c.statement}", anchor)


def _credential_tasks(run: Run, units: list[EvidenceUnit]) -> list[Claim]:
    """These come from the records themselves: a model may skip the secret or never mention it."""
    tasks: list[Claim] = []
    for u in units:
        if "secret" not in (u.sensitivity or []):
            continue
        run.counter += 1
        when = f" on {u.ts.day} {u.ts.strftime('%b')}" if u.ts else ""
        task = Claim(project_id=run.project.id, code=f"C{run.counter:03d}", type="access", subject=f"credential in {u.source.title}",
                     statement=f"A credential was shared in {u.source.title}{when}. Rotate it and move it to a password "
                               "manager (value withheld).",
                     value="rotate", source_id=u.source_id, audience=u.source.audience, origin="guard", as_of=u.ts)
        task.evidence.append(ClaimEvidence(unit=u, quote=u.text, match="exact", match_reason="found by the sensitive guard", entailment=1.0))
        run.s.add(task)
        tasks.append(task)
    return tasks


def _guard(claims: list[Claim]) -> None:
    """Runs before labels, because sensitive claims are withheld."""
    for c in claims:
        if c.origin == "guard":
            continue  # a rotation task carries only the masked record, so it can be shown
        ev_text = " ".join(e.quote for e in c.evidence)
        # A quote that avoids the sensitive part of its record may be shared: only the quote is ever shown.
        c.sens_tags = sorted(set(classify(f"{c.statement} {ev_text}")))
        c.sensitive = bool(c.sens_tags)


def _first_labels(run: Run, claims: list[Claim]) -> None:
    today = run.project.as_of
    for c in claims:
        _label(c, run.nli.available)
        if c.as_of is not None and c.label == "verified" and cf.is_stale(c.value, c.statement, c.as_of, today):
            c.stale = True
            c.label, c.label_reason = "outdated", "No supporting evidence in 30+ days."


def _reconcile(run: Run, candidates: list[Claim], only_involving: set[int] | None = None) -> list[cf.ConflictGroup]:
    """Conflicts among `candidates`. With `only_involving`, at least one claim in each card must be from that set."""
    usable = [c for c in candidates if c.evidence and any(e.unit is not None for e in c.evidence)
              and not c.sensitive and c.label != "unsupported" and c.origin != "guard"]
    views = [cf.ConflictClaim(c.id, c.type, c.subject, c.statement, c.value or "", c.as_of,
                              next((e.quote for e in c.evidence if e.match != "rejected"), ""), _context(c),
                              next((e.unit.uid for e in c.evidence if e.unit), "")) for c in usable]
    cards = cf.find_cards(views, run.nli, run.embedder, run.roster, only_involving, cf.project_name_words(run.project.name))
    run.stats["confirmed_cards"] = run.stats.get("confirmed_cards", 0) + len(cards)
    groups = cf.merge_cards(cards)
    cf.expand_groups(groups, views)
    by_id = {c.id: c for c in candidates}
    for g in groups:
        a_ids, b_ids = [x.id for x in g.side_a], [x.id for x in g.side_b]
        run.s.add(Conflict(project_id=run.project.id, claim_ids=a_ids + b_ids, side_a=a_ids, side_b=b_ids, facet=g.facet,
                           nli=round(g.nli, 3), newer_side="b", subject=g.subject))
        for cid in a_ids + b_ids:
            claim = by_id[cid]
            claim.label, claim.label_reason = "conflict", f"Sources disagree ({g.facet})."
            claim.review_state = "pending"  # an approved claim loses its approval when new evidence disagrees
    run.stats["conflicts"] = run.stats.get("conflicts", 0) + len(groups)
    return groups


def _gaps(run: Run, claims: list[Claim], n_conflicts: int, skip: set[str]) -> None:
    s, project = run.s, run.project

    def following(c: Claim):
        unit = next((e.unit for e in c.evidence if e.unit), None)
        if unit is None or unit.kind != "chat":
            return ("", "")
        after = s.scalars(select(EvidenceUnit).where(EvidenceUnit.source_id == unit.source_id, EvidenceUnit.seq == unit.seq + 1)).first()
        return (after.author, after.text) if after else ("", "")

    gap_claims = [gp.GapClaim(c.id, c.type, c.subject, c.statement, next((e.quote for e in c.evidence if e.match != "rejected"), ""),
                              next((e.unit.author for e in c.evidence if e.unit), ""), c.audience, c.label, c.due, c.sensitive,
                              c.origin == "guard", *following(c)) for c in claims]
    if run.leaver:
        owned = {gp._norm_subject(g.subject) for g in gap_claims if g.type == "owner" and gp.involves_leaver(g, run.leaver_names)}
        for g, c in zip(gap_claims, claims):
            c.cliff = c.label != "unsupported" and not c.sensitive and gp.is_cliff(g, run.leaver_names, project.last_day, owned)
    template = project.role_title if project.role_title in gp.ROLE_TEMPLATES else "default"
    raw = gp.raise_questions(gap_claims, run.leaver_names, run.leaver.clearances if run.leaver else [], project.last_day, template)
    unique, counts = gp.route_questions(raw)
    for q in unique:
        if _question_key(q.text) in skip:
            continue  # already answered, or already open
        asked = run.leaver.id if q.route == "leaver" and run.leaver else run.reviewer.id if run.reviewer else None
        s.add(GapQuestion(project_id=project.id, kind=q.kind, text=q.text, priority=q.priority, route=q.route,
                          asked_to_id=asked, claim_id=q.claim_id, audience=q.audience))
    counts["reviewer"] = counts.get("reviewer", 0) + n_conflicts  # one decision per conflict
    run.stats["questions"] = counts


def _finish(run: Run, claims: list[Claim]) -> None:
    for c in claims:
        c.bucket = bucket_of(c)
        c.review_state = c.review_state if c.review_state in ("approved", "rejected") else "pending"
    stats = run.stats
    stats["claims"] = sum(1 for c in claims if c.origin == "extracted")
    stats["grounded"] = sum(1 for c in claims if c.origin == "extracted" and any(e.match != "rejected" for e in c.evidence))
    stats["reanchored"] = sum(1 for c in claims if any("reanchored" in e.match for e in c.evidence))
    stats["sensitive"] = sum(1 for c in claims if c.sensitive)
    stats["bucket_one_pass"] = sum(1 for c in claims if c.bucket == "one_pass")
    stats["bucket_decide"] = sum(1 for c in claims if c.bucket == "decide")
    stats["bucket_needs_review"] = sum(1 for c in claims if c.bucket == "needs_review")
    stats["verified"] = sum(1 for c in claims if c.origin == "extracted" and (c.label == "verified" or (c.label in ("conflict", "outdated") and _supported(c))))
    stats["needs_review"] = sum(1 for c in claims if c.label == "needs_review")
    stats["unsupported"] = sum(1 for c in claims if c.label == "unsupported")
    stats["incomplete"] = bool(stats["failed_chunks"])


def run_pipeline(s: Session, project: Project, extractor: Extractor, job: Job | None = None,
                 progress: Callable[[str, int], None] | None = None, nli=None, embedder=None) -> dict:
    """Rebuild everything. People's words survive: answers and anything a reviewer wrote. Only machine output is replaced."""
    sources = s.scalars(select(Source).where(Source.project_id == project.id).order_by(Source.id)).all()
    if not sources:
        raise ValueError("Add at least one source before running the pipeline.")
    project.extractor = extractor.label
    clear_machine_output(s, project.id)
    s.flush()
    answered = {_question_key(q.text) for q in s.scalars(select(GapQuestion).where(GapQuestion.project_id == project.id)).all()}
    run = _start(s, project, extractor, job, progress, nli, embedder)

    run.step("Extracting claims", 5)
    claims: list[Claim] = []
    for i, source in enumerate(sources):
        claims += _extract(run, source)
        s.flush()
        run.step(f"Extracted claims from {source.title}", 5 + int(30 * (i + 1) / len(sources)))

    run.step("Checking that each quote supports its claim", 40)
    _support(run, claims)
    units = s.scalars(select(EvidenceUnit).where(EvidenceUnit.project_id == project.id, EvidenceUnit.retired.is_(False))).all()
    claims += _credential_tasks(run, units)
    s.flush()

    run.step("Looking for secrets and personal data", 55)
    _guard(claims)
    _first_labels(run, claims)

    run.step("Looking for conflicts between sources", 65)
    groups = _reconcile(run, claims)

    run.step("Finding gaps and routing questions", 80)
    _gaps(run, claims, len(groups), answered)
    _finish(run, claims)
    audit(s, project.id, "Baton", "pipeline_run", extractor.label,
          f"{run.stats['claims']} claims, {run.stats.get('conflicts', 0)} conflicts, {run.stats['questions'].get('leaver', 0)} questions for the leaver")
    run.step("Done", 100)
    return run.stats


def run_incremental(s: Session, project: Project, extractor: Extractor, new_unit_ids: set[int], job: Job | None = None,
                    progress: Callable[[str, int], None] | None = None, nli=None, embedder=None) -> dict:
    """Living handover: only new records are read. Earlier claims keep their state unless new evidence disagrees."""
    run = _start(s, project, extractor, job, progress, nli, embedder, {"incremental": True, "new_records": len(new_unit_ids)})
    new_claims: list[Claim] = []
    run.step("Reading the new records", 10)
    new_units = s.scalars(select(EvidenceUnit).where(EvidenceUnit.id.in_(new_unit_ids))).all() if new_unit_ids else []
    for source_id in sorted({u.source_id for u in new_units}):
        new_claims += _extract(run, s.get(Source, source_id), new_unit_ids)
    s.flush()

    run.step("Checking the new claims", 40)
    _support(run, new_claims)
    new_claims += _credential_tasks(run, new_units)
    s.flush()
    _guard(new_claims)
    _first_labels(run, new_claims)

    run.step("Comparing new claims with what was approved", 60)
    active = s.scalars(select(Claim).where(Claim.project_id == project.id, Claim.origin.in_(("extracted", "guard")),
                                           Claim.label.notin_(("superseded", "unsupported")),
                                           Claim.review_state != "rejected")).all()
    groups = _reconcile(run, list(active), {c.id for c in new_claims}) if new_claims else []

    run.step("Finding new gaps", 80)
    existing = {_question_key(q.text) for q in s.scalars(select(GapQuestion).where(GapQuestion.project_id == project.id)).all()}
    if new_claims:
        # Coverage was already asked in the first run, so only claims raise new questions here.
        _gaps(run, new_claims, len(groups), existing)
    for c in active:
        if c.label == "conflict":
            c.bucket = "decide"
    _finish(run, new_claims)
    run.stats["reopened"] = sum(1 for c in active if c.label == "conflict" and c not in new_claims)
    audit(s, project.id, "Baton", "resync_processed", extractor.label,
          f"{len(new_units)} new records, {run.stats['claims']} new claims, {len(groups)} new conflicts")
    run.step("Done", 100)
    return run.stats


def clear_machine_output(s: Session, project_id: int) -> None:
    s.execute(delete(Conflict).where(Conflict.project_id == project_id))
    s.execute(delete(GapQuestion).where(GapQuestion.project_id == project_id, GapQuestion.status != "answered"))
    for c in s.scalars(select(Claim).where(Claim.project_id == project_id, Claim.origin.in_(("extracted", "guard")))).all():
        s.delete(c)


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
        c.label, c.label_reason = "unsupported", (c.evidence[0].match_reason if c.evidence else "no evidence")
        return
    if not nli_available and c.origin != "guard":
        c.label, c.label_reason = "needs_review", "The support check did not run because its model is unavailable."
        return
    if not _supported(c):
        e = ok[0]
        reasons = []
        if (e.entailment or 0) < SUPPORT_THRESHOLD:
            reasons.append(f"the quote does not clearly support the claim (entailment {e.entailment or 0:.2f})")
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
