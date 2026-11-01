"""Deterministic rules on their own: facets, staleness, the departure cliff, routing, the permission rule, re-runs."""
from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from baton.db import Claim, GapQuestion, Member, make_engine, make_sessionmaker
from baton.demo.loader import load_demo
from baton.extract import ReplayExtractor
from baton.handover import compose, item_visible
from baton.permissions import visible
from baton.pipeline import run_pipeline
from baton.verify.conflicts import ConflictClaim, expand_groups, facet_check, is_stale, merge_cards, Card
from baton.verify.gaps import GapClaim, Question, is_cliff, raise_questions, route_questions

from .conftest import WordOverlapNLI, fresh_db_url

ROSTER = [["Arjun", "Arjun Mehta"], ["Kabir", "Kabir Shah"], ["Meera", "Meera Iyer"]]


def cc(i, statement, subject="", type_="status", value="", when=(2026, 10, 1), context=""):
    return ConflictClaim(i, type_, subject, statement, value, datetime(*when), statement, context, f"U{i}")


# --- facet check ---------------------------------------------------------------------------------------------------

def test_facet_different_dates_same_subject():
    a = cc(1, "The SSO login is due by 2026-12-15.", "SSO login", "deadline")
    b = cc(2, "SSO is pushed to 10 Jan.", "SSO login", "deadline", when=(2026, 10, 28))
    assert facet_check(a, b, ROSTER) == "different dates"


def test_facet_same_date_is_no_conflict():
    a = cc(1, "The SSO login is due by 2026-12-15.", "SSO login", "deadline")
    b = cc(2, "SSO target is 15 Dec.", "SSO login", "deadline")
    assert facet_check(a, b, ROSTER) is None


def test_facet_different_subjects_no_conflict():
    a = cc(1, "The SSO login is due by 2026-12-15.", "SSO login", "deadline")
    b = cc(2, "The reports dashboard is due by 2026-11-25.", "reports dashboard", "deadline")
    assert facet_check(a, b, ROSTER) is None


def test_facet_a_person_in_common_is_not_a_shared_subject():
    a = cc(1, "Arjun leaves on 15 Nov.", "Arjun", "deadline")
    b = cc(2, "Arjun must renew the domain by 20 Nov.", "Arjun", "deadline")
    assert facet_check(a, b, ROSTER) is None


def test_facet_opposite_status():
    a = cc(1, "The weekly backup cron for the DB is configured.", "weekly backups")
    b = cc(2, "Weekly backups were never actually enabled.", "weekly backups")
    assert facet_check(a, b, ROSTER) == "opposite status"


def test_facet_status_needs_more_than_one_shared_word():
    a = cc(1, "The Supabase migration is completed.", "Supabase migration")
    b = cc(2, "The Supabase plan does not include weekly backups.", "Supabase plan")
    assert facet_check(a, b, ROSTER) is None


def test_facet_owners_via_aliases():
    a = cc(1, "Kabir owns the migration.", "Supabase migration", "owner", "Kabir Shah")
    same = cc(2, "Kabir owns the migration.", "Supabase migration", "owner", "Kabir")
    other = cc(3, "Meera owns the migration.", "Supabase migration", "owner", "Meera")
    assert facet_check(a, same, ROSTER) is None
    assert facet_check(a, other, ROSTER) == "different owners"


def test_cards_sharing_a_claim_merge_and_same_card_status_joins():
    old1 = cc(1, "SSO is due 15 Dec.", "SSO", "deadline", when=(2026, 10, 2))
    new1 = cc(2, "SSO is pushed to 10 Jan.", "SSO", "deadline", when=(2026, 10, 28))
    new2 = cc(3, "New target is 10 Jan.", "SSO", "deadline", when=(2026, 10, 29))
    groups = merge_cards([Card(old1, new1, 0.9, "different dates"), Card(old1, new2, 0.9, "different dates")])
    assert len(groups) == 1 and [c.id for c in groups[0].side_a] == [1] and {c.id for c in groups[0].side_b} == {2, 3}

    comment = cc(4, "Weekly backup cron configured.", "backups", context="Set up DB backups", when=(2026, 9, 25))
    newer = cc(5, "Backups were never enabled.", "backups", when=(2026, 10, 26))
    card = cc(6, "Set up DB backups is completed.", "Set up DB backups", context="Set up DB backups", when=(2026, 9, 25))
    groups = merge_cards([Card(comment, newer, 0.9, "opposite status")])
    expand_groups(groups, [comment, newer, card])
    assert {c.id for c in groups[0].side_a} == {4, 6}


def test_repeated_statements_do_not_crowd_out_a_real_conflict():
    import numpy as np

    from baton.verify import conflicts as cf

    class SameVector:
        def embed(self, texts):
            return np.ones((len(texts), 1))

    class AlwaysContradicts:
        def score(self, pairs):
            return [SimpleNamespace(contradiction=1.0, entailment=0.0) for _ in pairs]

    repeats = [cc(i, "Thanks everyone for the good work.", "thanks") for i in range(100)]  # 4,950 pairs with no facet
    real = [cc(200, "The launch is due by 2026-11-20.", "launch", "deadline"), cc(201, "The launch is pushed to 5 Dec.", "launch", "deadline")]
    cards = cf.find_cards([*repeats, *real], AlwaysContradicts(), SameVector())
    assert [(c.a.id, c.b.id) for c in cards] in ([(200, 201)], [(201, 200)])


# --- staleness -----------------------------------------------------------------------------------------------------

def test_stale_rule():
    today = date(2026, 10, 30)
    assert is_stale("in_progress", "x", datetime(2026, 8, 20), today)
    assert not is_stale("in_progress", "x", datetime(2026, 10, 20), today)      # recent
    assert not is_stale("done", "x is finished", datetime(2026, 8, 20), today)  # not an open status
    assert not is_stale("in_progress", "x", None, today)                        # undated: no claim either way


# --- departure cliff and routing -----------------------------------------------------------------------------------

def gc(i, type_, statement, audience="tech-team", label="verified", due=None, author="", quote="", subject="", **kw):
    return GapClaim(i, type_, subject or statement[:30], statement, quote or statement, author, audience, label, due, **kw)


LEAVER = ["Arjun", "Arjun Mehta", "arjun-m"]
LAST = date(2026, 11, 15)


@pytest.mark.parametrize("claim,expected", [
    (gc(1, "owner", "Arjun Mehta is responsible for the domain."), True),
    (gc(2, "owner", "Kabir is responsible for the domain."), False),
    (gc(3, "decision", "Arjun chose Render."), False),                                       # not an ownership type
    (gc(4, "access", "The key is on the laptop.", author="Arjun", quote="I have it on my laptop"), True),   # first person
    (gc(5, "access", "Riya needs the key.", author="Riya", next_author="Arjun", next_text="I have it on my laptop"), True),
    (gc(6, "deadline", "Arjun must submit by 2 Nov.", due=date(2026, 11, 2)), False),        # due before they leave
    (gc(7, "deadline", "Arjun must renew by 20 Nov.", due=date(2026, 11, 20)), True),
])
def test_cliff_rule(claim, expected):
    assert is_cliff(claim, LEAVER, LAST) is expected


def test_one_question_per_item_and_routing_by_clearance():
    claims = [
        gc(1, "owner", "Arjun Mehta is responsible for the domain renewal.", subject="domain renewal"),
        gc(2, "deadline", "The domain renewal is due by 20 Nov.", subject="domain renewal", due=date(2026, 11, 20)),
        gc(3, "owner", "Arjun holds the sponsor agreement.", audience="core-committee", subject="sponsor agreement"),
        gc(4, "decision", "Hosting stays on Render.", audience="tech-team"),
        gc(5, "decision", "We must switch to SSO.", audience="tech-team"),                  # outside requirement
        gc(6, "decision", "We use Postgres because it is cheap.", audience="tech-team"),    # has a reason
        gc(7, "open_question", "Should we keep the data?", label="verified"),
        gc(8, "owner", "Arjun owns the unsupported thing.", label="needs_review"),           # not verified: no question
    ]
    qs = raise_questions(claims, LEAVER, ["public", "tech-team"], LAST)
    by_kind = {}
    for q in qs:
        by_kind.setdefault(q.kind, []).append(q)
    assert [q.route for q in by_kind["ownership"]].count("leaver") == 1
    assert [q.route for q in by_kind["ownership"]].count("reviewer") == 1     # the leaver can't see that source
    assert len(by_kind["rationale"]) == 1 and "Render" in by_kind["rationale"][0].text
    assert by_kind["open_question"][0].route == "triage"
    assert not any("unsupported" in q.text for q in qs)
    assert by_kind["coverage"][0].route == "leaver"     # nobody explained deployment


def test_coverage_is_satisfied_by_verified_evidence_only():
    verified = gc(1, "event", "Push to main and Render auto-deploys.")
    unverified = gc(1, "event", "Push to main and Render auto-deploys.", label="needs_review")
    assert not [q for q in raise_questions([verified], LEAVER, ["tech-team"], LAST) if q.kind == "coverage"]
    assert [q for q in raise_questions([unverified], LEAVER, ["tech-team"], LAST) if q.kind == "coverage"]


def test_budget_overflow_goes_to_reviewer_not_the_bin():
    qs = [Question("rationale", f"Why was decision number {i} of the many decisions taken?", 4, "leaver", i, "tech-team") for i in range(15)]
    unique, counts = route_questions(qs, budget=10)
    assert counts["leaver"] == 10 and counts["reviewer"] == 5 and len(unique) == 15


# --- the permission rule -------------------------------------------------------------------------------------------

@pytest.mark.parametrize("audience,sensitive,role,clearances,expected", [
    ("tech-team", False, "successor", ["public", "tech-team"], True),
    ("core-committee", False, "successor", ["public", "tech-team"], False),
    ("tech-team", True, "successor", ["public", "tech-team"], False),
    ("tech-team", True, "reviewer", ["public", "tech-team"], True),
    ("core-committee", True, "reviewer", ["public", "tech-team"], False),   # being the reviewer doesn't grant clearance
    ("public", False, "teammate", ["public"], True),
    ("tech-team", False, "teammate", [], False),                            # default deny
])
def test_visible_rule(audience, sensitive, role, clearances, expected):
    viewer = SimpleNamespace(role=role, clearances=clearances)
    assert visible(SimpleNamespace(audience=audience, sensitive=sensitive), viewer) is expected
    assert item_visible({"audience": audience, "sensitive": sensitive}, viewer) is expected


def test_nobody_signed_in_sees_nothing():
    assert visible(SimpleNamespace(audience="public", sensitive=False), None) is False


# --- re-runs ---------------------------------------------------------------------------------------------------------

def test_rerun_keeps_peoples_answers_and_does_not_ask_again(tmp_path):
    from baton import review

    Session = make_sessionmaker(make_engine(fresh_db_url(tmp_path)))
    with Session() as s:
        p = load_demo(s)
        s.commit()
        run_pipeline(s, p, ReplayExtractor(), nli=WordOverlapNLI(), embedder=False)
        s.commit()
        arjun = s.get(Member, p.leaver_id)
        q = s.scalars(select(GapQuestion).where(GapQuestion.project_id == p.id, GapQuestion.kind == "coverage")).first()
        text = q.text
        review.answer_question(s, q, arjun, "Push to main.")
        s.commit()
        run_pipeline(s, p, ReplayExtractor(), nli=WordOverlapNLI(), embedder=False)
        s.commit()
        same = s.scalars(select(GapQuestion).where(GapQuestion.project_id == p.id, GapQuestion.text == text)).all()
        assert len(same) == 1 and same[0].status == "answered"
        assert s.scalars(select(Claim).where(Claim.project_id == p.id, Claim.origin == "testimony")).one().statement == "Push to main."
        codes = [c.code for c in s.scalars(select(Claim).where(Claim.project_id == p.id)).all()]
        assert len(codes) == len(set(codes))


@pytest.mark.parametrize("subject,statement,shown", [
    ("Hackfest poster + UI kit", "The Hackfest poster + UI kit is due by 2026-11-05.", False),
    ("Target for CAS integration", "New target is 10 Jan.", True),
    ("How it works", "Push to main; Render auto-deploys.", True),
    ("", "Anything at all.", False),
])
def test_a_line_names_its_subject_only_when_the_statement_does_not(subject, statement, shown):
    from baton.handover import needs_subject

    assert needs_subject({"subject": subject, "statement": statement}) is shown


def test_handover_lists_an_open_question_once():
    claim = {"code": "C1", "type": "open_question", "subject": "", "statement": "Should we keep the data?", "label": "verified",
             "audience": "public", "sensitive": False, "due": None, "as_of": "2026-10-01", "origin": "extracted", "answered_by": "",
             "answered_at": "", "cliff": False, "stale": False, "receipts": []}
    snapshot = {"claims": [claim], "conflicts": [], "last_day": "2026-11-15", "as_of": "2026-10-30", "role_title": "x",
                "missing": [{"id": 1, "kind": "open_question", "text": "Should we keep the data?", "audience": "public", "ask": "Sana"}]}
    version = SimpleNamespace(number=1, approved_by="Sana", approved_at=datetime(2026, 11, 1), snapshot=snapshot)
    h = compose(version, SimpleNamespace(role="successor", clearances=["public"]))
    assert h.sections["questions"] == [] and len(h.missing) == 1
