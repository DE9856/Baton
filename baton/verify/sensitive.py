"""Sensitive-content guard: deterministic regex rules.

Secrets are masked at ingestion, before anything is stored or sent to an AI model, so they cannot leak from the
database or a prompt. Other sensitive content (personal phones, personal emails, health, HR, finance) is kept for
the reviewer and withheld from everyone else.
"""
from __future__ import annotations

import re

# Gitleaks-style credential shapes.
_SECRET_RULES: list[tuple[str, re.Pattern]] = [
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_\-]{20,}")),
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("github_token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[0-9A-Za-z]{30,}\b|\bgithub_pat_[0-9A-Za-z_]{30,}\b")),
    ("anthropic_key", re.compile(r"\bsk-ant-[0-9A-Za-z_\-]{20,}")),
    ("openai_style_key", re.compile(r"\bsk-[0-9A-Za-z]{20,}\b")),
    ("slack_token", re.compile(r"\bxox[abprs]-[0-9A-Za-z\-]{10,}")),
    ("jwt", re.compile(r"\beyJ[0-9A-Za-z_\-]{10,}\.[0-9A-Za-z_\-]{10,}\.[0-9A-Za-z_\-]{10,}")),
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("db_url_password", re.compile(r"\b(?:postgres|postgresql|mysql|mongodb(?:\+srv)?)://[^\s:/]+:([^\s@/]+)@")),
]
# "password ... reset to X", "api_key = X", "pwd: X". Group 1 is the value that gets masked.
_ASSIGNED = re.compile(
    r"\b(?:password|passwd|pwd|passcode|api[_ -]?key|secret|token)\b"
    r"(?:\s+(?:for|of)\s+[\w ]{1,40}?)?"
    r"(?:\s+(?:was|has been|is|got)\s+(?:reset|changed|set|updated)?)?"
    r"\s*(?:to\b|is\b|=|:)\s*[\"'`]?([^\s\"'`,;]+)",
    re.I,
)
_MASK = re.compile(r"\[secret \*{4}[^\]]{0,4}\]")
# Words that may follow "token:" or "password is" without being a credential.
_NOT_A_VALUE = {"the", "a", "an", "in", "on", "our", "my", "your", "this", "that", "it", "shared", "stored",
                "needed", "required", "missing", "expired", "reset", "changed", "manager", "not", "only", "still",
                "safe", "there", "here", "somewhere", "gone", "set", "updated", "rotated", "secure", "with"}
_VALUE_SHAPE = re.compile(r"(?=.*\d)|(?=.*[@#$%^&*!])|(?=.*[A-Z].*[a-z].*[A-Z])")

PHONE = re.compile(r"(?<![\w.#-])(?:\+?\d{1,3}[\s-]?)?(?:\(?\d{2,5}\)?[\s-]?)?\d{3,5}[\s-]?\d{4,5}(?![\w-])")
EMAIL = re.compile(r"\b[\w.+-]+@([\w-]+(?:\.[\w-]+)+)\b")
_PERSONAL_DOMAINS = {"gmail.com", "yahoo.com", "yahoo.co.in", "hotmail.com", "outlook.com", "icloud.com",
                     "proton.me", "protonmail.com", "rediffmail.com", "live.com"}
HEALTH = re.compile(r"\b(surgery|hospital(?:ised|ized)?|diagnos\w+|therapy|depression|anxiety|medical leave|"
                    r"illness|pregnan\w+|chemo\w*|medication)\b", re.I)
HR = re.compile(r"\b(disciplinary|written warning|misuse of|harass\w*|terminated|fired|suspended|salary|"
                r"performance review|grievance)\b", re.I)
FINANCE = re.compile(r"(?:[\u20b9$\u20ac\u00a3]\s?\d[\d,.]*|\b(?:Rs\.?|INR|USD)\s?\d[\d,.]*|"
                     r"\b(?:bank account|IFSC|UPI id|invoice no)\b)", re.I)


def _mask_value(value: str) -> str:
    tail = value[-3:] if len(value) > 6 else ""
    return f"[secret ****{tail}]"


def mask_secrets(text: str) -> tuple[str, list[str]]:
    """Return (masked text, rule names that fired). Idempotent: masking twice changes nothing."""
    found: list[str] = []
    out = text
    for name, rule in _SECRET_RULES:
        def repl(m: re.Match, name: str = name) -> str:
            found.append(name)
            if m.groups() and m.group(1):
                return m.group(0).replace(m.group(1), _mask_value(m.group(1)))
            return _mask_value(m.group(0))

        out = rule.sub(repl, out)

    def assigned(m: re.Match) -> str:
        value = m.group(1).rstrip(".")
        if _MASK.search(m.group(0)) or value.lower() in _NOT_A_VALUE or value.startswith("[secret"):
            return m.group(0)
        if not _VALUE_SHAPE.search(value) or len(value) < 6:
            return m.group(0)
        found.append("assigned_credential")
        return m.group(0).replace(value, "[secret ****]")  # a password keeps no visible tail at all

    return _ASSIGNED.sub(assigned, out), found


def secret_values(text: str) -> set[str]:
    """The raw values mask_secrets would hide in this text."""
    values: set[str] = set()
    for _, rule in _SECRET_RULES:
        for m in rule.finditer(text):
            values.add(m.group(1) if m.groups() and m.group(1) else m.group(0))
    for m in _ASSIGNED.finditer(text):
        value = m.group(1).rstrip(".")
        if value.lower() not in _NOT_A_VALUE and not value.startswith("[secret") and _VALUE_SHAPE.search(value) and len(value) >= 6:
            values.add(value)
    return values


def mask_fields(*fields: str) -> list[str]:
    """Mask several related fields together: a bare value ("DevCircle@2026") has no context of its own to match on,
    so a secret found in any one field is removed from all of them."""
    known: set[str] = set()
    for f in fields:
        known |= secret_values(f or "")
    out = []
    for f in fields:
        masked = mask_secrets(f or "")[0]
        for value in sorted(known, key=len, reverse=True):
            if value in masked:
                masked = masked.replace(value, "[secret ****]")
        out.append(masked)
    return out


def classify(text: str) -> list[str]:
    """Sensitivity tags for text that has already been through mask_secrets."""
    tags: list[str] = []
    if _MASK.search(text):
        tags.append("secret")
    for m in PHONE.finditer(text):
        digits = re.sub(r"\D", "", m.group(0))
        # 10 digits, optionally with a country code: excludes dates, years, PR numbers and short counts.
        if 10 <= len(digits) <= 13 and not re.fullmatch(r"20\d{2}[\s-]?\d{2}[\s-]?\d{2}", m.group(0)):
            tags.append("phone")
            break
    for m in EMAIL.finditer(text):
        if m.group(1).lower() in _PERSONAL_DOMAINS:
            tags.append("email")
            break
    if HEALTH.search(text):
        tags.append("health")
    if HR.search(text):
        tags.append("hr")
    if FINANCE.search(text):
        tags.append("finance")
    return tags


TAG_LABELS = {"secret": "a credential", "phone": "a personal phone number", "email": "a personal email address",
              "health": "health information", "hr": "HR or disciplinary content", "finance": "financial details"}
