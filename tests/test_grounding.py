from datetime import date

import pytest

from baton.verify.dates import find_dates
from baton.verify.grounding import ground_quote
from baton.verify.value_guard import check_values

RECORD = ("Kabir: ok bad news, I checked: weekly backups were never actually enabled. The cron job was configured but "
          "our Supabase plan doesn't include it. We have NO backups right now")
READS = "Kabir: Firebase reads are already at 38k/day in testing"
SSO = "Arjun: Update from Vikram Rao (IT): CAS credentials will only be issued after their security audit, so SSO is pushed to 10 Jan"
CHUNK = {"WA-1": RECORD, "WA-2": READS, "WA-3": SSO}


def test_exact_quote():
    assert ground_quote("weekly backups were never actually enabled", "WA-1", CHUNK).match == "exact"


def test_exact_after_normalisation():
    q = "Weekly   backups were never actually “enabled”".replace("“", "").replace("”", "")
    assert ground_quote(q, "WA-1", CHUNK).match == "exact"


def test_wrong_record_id_is_reanchored_within_the_chunk():
    r = ground_quote("Firebase reads are already at 38k/day", "WA-1", CHUNK)
    assert r.match == "exact_reanchored" and r.unit_uid == "WA-2"


def test_near_verbatim_small_typo_allowed():
    r = ground_quote("weekly backups were never actualy enabled", "WA-1", CHUNK)
    assert r.match == "near"


# The adversarial set: fabricated or distorted quotes that must never be accepted.
@pytest.mark.parametrize("quote", [
    "weekly backups were actually enabled",                          # dropped "never"
    "weekly backups were always actually enabled",                   # flipped
    "Firebase reads are already at 83k/day in testing",              # 38k -> 83k, same digits
    "Firebase reads are already at 380k/day in testing",             # changed magnitude
    "so SSO is pushed to 10 Feb",                                    # changed date
    "so SSO is pushed to 11 Jan",                                    # changed day
    "CAS credentials will be issued before their security audit",    # changed meaning
    "The library will open at 8 am because exam season starts.",     # copied from a prompt example
    "our Supabase plan does include it",                             # negation removed
    "We have backups right now",                                     # NO dropped
    "Arjun approved the budget of 50000 rupees",                     # invented
    "",                                                              # empty
    "ok",                                                            # too short to prove anything
])
def test_adversarial_quotes_rejected(quote):
    assert not ground_quote(quote, "WA-1", CHUNK).grounded


def test_quote_outside_the_chunk_is_rejected_even_if_true_elsewhere():
    assert not ground_quote("Firebase reads are already at 38k/day", "WA-2", {"WA-1": RECORD}).grounded


def test_short_quote_carrying_a_value_is_allowed():
    assert ground_quote("due: 2026-11-30", "TR-1", {"TR-1": "Card 'Logo'; due: 2026-11-30; members: Arjun"}).grounded


# --- deterministic dates

def test_year_rollover_from_anchor():
    assert find_dates("pushed to 10 Jan", date(2026, 10, 28))[0].day == date(2027, 1, 10)


def test_past_reference_stays_in_past():
    assert find_dates("the tech sync on 12 Sep", date(2026, 10, 2))[0].day == date(2026, 9, 12)


@pytest.mark.parametrize("text,expected", [
    ("due 2026-11-20", date(2026, 11, 20)), ("20 November 2026", date(2026, 11, 20)),
    ("Nov 20", date(2026, 11, 20)), ("November 20th, 2026", date(2026, 11, 20)), ("20th of Nov", date(2026, 11, 20)),
])
def test_date_forms(text, expected):
    assert find_dates(text, date(2026, 10, 30))[0].day == expected


def test_invalid_date_ignored():
    assert find_dates("31 Feb 2026 and 2026-13-40", date(2026, 1, 1)) == []


def test_may_as_a_verb_is_not_a_date():
    assert find_dates("We may 5 times try it") == []


# --- value guard

ROSTER = [["Arjun", "Arjun Mehta", "arjun-m"], ["Kabir", "Kabir Shah", "kabirs"]]
ANCHOR = date(2026, 10, 28)


@pytest.mark.parametrize("statement", [
    "SSO is pushed to 10 Jan.",
    "SSO is pushed to January 10.",
    "SSO is pushed to 2027-01-10.",
    "Vikram Rao said CAS credentials will only be issued after the audit.",
])
def test_guard_accepts_faithful_values(statement):
    assert check_values(statement, SSO, ANCHOR, ROSTER).ok


@pytest.mark.parametrize("statement,bad", [
    ("SSO is pushed to 15 Jan.", "15 Jan"),
    ("SSO is pushed to 2026-01-10.", "2026-01-10"),     # the model's classic wrong year
    ("Vikram Sharma said credentials are delayed.", "Sharma"),
    ("Sofia said CAS credentials will only be issued after the audit.", "Sofia"),
    ("The audit takes 14 days.", "14"),
])
def test_guard_rejects_changed_values(statement, bad):
    r = check_values(statement, SSO, ANCHOR, ROSTER)
    assert not r.ok and bad in r.missing


def test_guard_allows_roster_alias():
    assert check_values("Kabir Shah owns the migration.", "kabirs: PR #31 Migrate backend", None, ROSTER).ok


def test_guard_number_forms():
    assert check_values("PR #42 is blocked.", "PR #42 for push notifications is blocked", None, []).ok
    assert not check_values("PR #43 is blocked.", "PR #42 for push notifications is blocked", None, []).ok
    assert check_values("Reads reached 38k per day.", READS, None, []).ok
    assert not check_values("Reads reached 83k per day.", READS, None, []).ok
