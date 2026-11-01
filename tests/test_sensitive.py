import pytest

from baton.verify.sensitive import classify, mask_secrets


@pytest.mark.parametrize("text,leak", [
    ("here's the maps key: AIzaSyD4-DemoKey_x9QmT7vLpR2sKw8NcE3uYb", "AIzaSyD4"),
    ("The admin password for the club Google account was reset to DevCircle@2026.", "DevCircle@2026"),
    ("password: Hunter2!x", "Hunter2!x"),
    ("api_key = abc123XYZ789", "abc123XYZ789"),
    ("token ghp_abcdefghijklmnopqrstuvwxyz0123456789", "ghp_abcdefghijklmnop"),
    ("postgres://admin:s3cretpw@db.example.com/x", "s3cretpw"),
    ("AWS AKIAIOSFODNN7EXAMPLE leaked", "AKIAIOSFODNN7EXAMPLE"),
])
def test_secrets_are_masked(text, leak):
    masked, found = mask_secrets(text)
    assert leak not in masked and found and "[secret ****" in masked


@pytest.mark.parametrize("text", [
    "Dev will move it into the password manager.",
    "We need to reset the password for the account.",
    "The token is expired.",
    "Rotate this API key soon.",
    "Keep the secret: it is safe",
])
def test_ordinary_sentences_are_not_masked(text):
    assert mask_secrets(text) == (text, [])


def test_masking_is_idempotent():
    once, _ = mask_secrets("key AIzaSyD4-DemoKey_x9QmT7vLpR2sKw8NcE3uYb and password is Abc12345")
    twice, found = mask_secrets(once)
    assert once == twice and not found


@pytest.mark.parametrize("text,tag", [
    ("call me on +91 98450 12345", "phone"),
    ("Sponsor contact: Ananya (TechNova), +91 99000 55511.", "phone"),
    ("9845012345 is my number", "phone"),
    ("mail me at someone@gmail.com", "email"),
    ("I'll be out, knee surgery", "health"),
    ("a written warning was issued for misuse of club funds", "hr"),
    ("sponsorship confirmed: ₹48,500, paid in two parts", "finance"),
    ("[secret ****3uYb] was pasted", "secret"),
])
def test_classify_positive(text, tag):
    assert tag in classify(text)


@pytest.mark.parametrize("text", [
    "due: 2026-11-20",
    "PR #42 is blocked",
    "Contact: Vikram Rao (vikram.rao@college.edu)",  # a work address is a useful contact
    "Free tier is 50k reads/day and read at 38k/day",
    "last activity: 2026-10-17 and created 2026-09-14",
    "Issue #45 from 15 Dec to 10 Jan",
])
def test_classify_negative(text):
    assert classify(text) == []
