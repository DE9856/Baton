import json
from datetime import datetime
from pathlib import Path

import pytest

from baton.parsers import ParseError, fetch_repo, parse_github, parse_notes, parse_trello, parse_whatsapp
from baton.parsers.github import parse_repo_url

DATA = Path(__file__).parent.parent / "baton" / "demo" / "data"


# --- the demo dataset's record counts (published in the Round 1 plan: 45 + 16 + 11 + 19 = 91)

def test_demo_whatsapp_counts():
    r = parse_whatsapp((DATA / "whatsapp_devcircle_tech.txt").read_text("utf-8"))
    assert len(r.units) == 45
    assert r.units[2].uid == "WA-0003" and r.units[2].text.startswith("For attendance")
    assert r.units[0].ts == datetime(2026, 8, 1, 19, 5, 43)
    assert any("system" in s.reason for s in r.skipped)  # the encryption notice is reported, not silently lost


def test_demo_multiline_message_kept_together():
    r = parse_whatsapp((DATA / "whatsapp_devcircle_tech.txt").read_text("utf-8"))
    qr = next(u for u in r.units if u.text.startswith("QR attendance failing"))
    assert "Workaround: ask them to clear Safari cache" in qr.text


def test_demo_github_counts():
    r = parse_github((DATA / "github_campusconnect.json").read_text("utf-8"))
    assert len(r.units) == 16
    ids = {u.uid for u in r.units}
    assert {"GH-28", "GH-28c1", "GH-28x", "GH-31m", "GH-45c1"} <= ids


def test_demo_trello_counts():
    r = parse_trello((DATA / "trello_campusconnect.json").read_text("utf-8"))
    assert len(r.units) == 11
    c5 = next(u for u in r.units if u.uid == "TR-c5")
    assert "due: 2026-11-20" in c5.text and "members: Arjun Mehta" in c5.text and "'To Do'" in c5.text


def test_demo_notes_counts():
    total = 0
    for i, f in enumerate(sorted((DATA / "notes").glob("*.md")), 1):
        total += len(parse_notes(f.read_text("utf-8"), f"N{i}", f.name).units)
    assert total == 19


# --- WhatsApp format variety

@pytest.mark.parametrize("line,expected", [
    ("[28/10/26, 16:20:00] Arjun: hello there", datetime(2026, 10, 28, 16, 20, 0)),          # iOS, day first
    ("28/10/26, 16:20 - Arjun: hello there", datetime(2026, 10, 28, 16, 20)),                # Android, 24h
    ("28/10/2026, 4:20 pm - Arjun: hello there", datetime(2026, 10, 28, 16, 20)),            # Android, 12h
    ("[28/10/26, 4:20:00 PM] Arjun: hello there", datetime(2026, 10, 28, 16, 20, 0)),   # iOS narrow nbsp
    ("10/28/26, 4:20 PM - Arjun: hello there", datetime(2026, 10, 28, 16, 20)),              # US
    ("2026-10-28, 16:20 - Arjun: hello there", datetime(2026, 10, 28, 16, 20)),              # ISO
    ("28.10.26, 16:20 - Arjun: hello there", datetime(2026, 10, 28, 16, 20)),                # dotted
    ("[28/10/26, 12:05:00 AM] Arjun: hello there", datetime(2026, 10, 28, 0, 5, 0)),         # midnight
])
def test_whatsapp_formats(line, expected):
    r = parse_whatsapp(line)
    assert len(r.units) == 1 and r.units[0].ts == expected and r.units[0].author == "Arjun"


def test_whatsapp_ambiguous_dates_use_chronology():
    # Every value is at most 12. Month-first runs forwards (2 Jan, 3 Jan, 1 Feb);
    # day-first would run backwards (1 Feb, 1 Mar, 2 Jan).
    text = "01/02/26, 10:00 - A: one\n01/03/26, 10:00 - A: two\n02/01/26, 10:00 - A: three"
    r = parse_whatsapp(text)
    assert [(u.ts.month, u.ts.day) for u in r.units] == [(1, 2), (1, 3), (2, 1)]


def test_whatsapp_truly_ambiguous_dates_default_to_day_first():
    r = parse_whatsapp("01/02/26, 10:00 - A: one\n01/03/26, 10:00 - A: two")
    assert [(u.ts.month, u.ts.day) for u in r.units] == [(2, 1), (3, 1)]


def test_whatsapp_media_and_system_lines_are_reported():
    text = ("01/08/26, 19:00 - Messages and calls are end-to-end encrypted.\n"
            "01/08/26, 19:01 - Arjun: <Media omitted>\n"
            "01/08/26, 19:02 - Arjun: real message")
    r = parse_whatsapp(text)
    assert [u.text for u in r.units] == ["real message"]
    reasons = " ".join(s.reason for s in r.skipped)
    assert "media" in reasons and "system" in reasons


def test_whatsapp_rejects_non_chat():
    with pytest.raises(ParseError, match="No WhatsApp messages"):
        parse_whatsapp("this is not a chat export\nat all")


def test_whatsapp_message_containing_colon():
    r = parse_whatsapp("01/08/26, 19:02 - Arjun: note: deploy at 5")
    assert r.units[0].author == "Arjun" and r.units[0].text == "note: deploy at 5"


# --- other parsers: bad input fails with a readable message

@pytest.mark.parametrize("fn", [parse_github, parse_trello])
def test_json_parsers_reject_garbage(fn):
    with pytest.raises(ParseError):
        fn("not json {")
    with pytest.raises(ParseError):
        fn(json.dumps({"unexpected": True}))


def test_utf16_exports_are_readable():
    from baton.parsers import decode_text

    text = "01/08/26, 19:02 - Arjun: hello there"
    assert parse_whatsapp(decode_text(text.encode("utf-16"))).units[0].text == "hello there"
    assert decode_text(b"\xef\xbb\xbfplain") == "plain"
    with pytest.raises(ParseError, match="not UTF-8"):
        decode_text("caf\u00e9".encode("latin-1"))


def test_a_very_long_chat_parses_quickly():
    import time

    lines = "\n".join(f"[01/08/26, 19:{i % 60:02d}:00] Person{i % 7}: message number {i} about the project" for i in range(50_000))
    started = time.time()
    assert len(parse_whatsapp(lines).units) == 50_000
    assert time.time() - started < 10


def test_notes_rejects_empty():
    with pytest.raises(ParseError):
        parse_notes("   \n ", "N1")


def test_notes_date_from_title_and_units_from_bullets():
    r = parse_notes("# Sync - 3 Oct 2026\nAttendees: A, B\n\n- First thing\n- Second thing\n", "N1")
    assert [u.uid for u in r.units] == ["N1-01", "N1-02", "N1-03"]
    assert r.units[1].text == "First thing" and r.units[0].ts == datetime(2026, 10, 3)


def test_notes_undated_is_reported():
    r = parse_notes("- something happened", "N1")
    assert r.units[0].ts is None and any("undated" in n for n in r.notes)


def test_docx_notes_roundtrip():
    import io

    import docx

    from baton.parsers import docx_to_text
    doc = docx.Document()
    doc.add_heading("Sync 5 Nov 2026", 1)
    doc.add_paragraph("Decision: use Render.")
    buf = io.BytesIO()
    doc.save(buf)
    r = parse_notes(docx_to_text(buf.getvalue()), "N1", "sync.docx")
    assert r.units[0].text == "Decision: use Render." and r.units[0].ts == datetime(2026, 11, 5)


def test_docx_corrupt_file():
    from baton.parsers import docx_to_text
    with pytest.raises(ParseError, match="could not be opened"):
        docx_to_text(b"not a zip")


# --- GitHub connector, against a fake transport: no network in unit tests

def test_repo_url_forms():
    assert parse_repo_url("https://github.com/a/b.git") == ("a", "b")
    assert parse_repo_url("a/b") == ("a", "b")
    with pytest.raises(ParseError):
        parse_repo_url("https://example.com/not/a/repo/at/all")


def test_fetch_repo_builds_parseable_export():
    import httpx

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/issues") and req.url.params.get("page") == "1":
            return httpx.Response(200, json=[
                {"number": 7, "title": "Bug", "user": {"login": "u"}, "state": "open", "created_at": "2026-01-01T00:00:00Z",
                 "closed_at": None, "html_url": "http://x/7", "body": "Broken", "labels": [{"name": "bug"}], "comments": 1,
                 "comments_url": "https://api.github.com/c/7"},
                {"number": 8, "title": "Fix", "user": {"login": "v"}, "state": "closed", "created_at": "2026-01-02T00:00:00Z",
                 "closed_at": "2026-01-03T00:00:00Z", "html_url": "http://x/8", "body": None, "labels": [], "comments": 0,
                 "pull_request": {"merged_at": "2026-01-03T00:00:00Z"}},
            ])
        if req.url.path.endswith("/issues"):
            return httpx.Response(200, json=[])
        return httpx.Response(200, json=[{"user": {"login": "w"}, "created_at": "2026-01-01T01:00:00Z", "body": "same here"}])

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        data = fetch_repo("o/r", client=client)
    r = parse_github(data)
    assert {u.uid for u in r.units} == {"GH-7", "GH-7c1", "GH-8", "GH-8m"}


@pytest.mark.parametrize("status,msg", [(404, "not found"), (403, "refused"), (500, "HTTP 500")])
def test_fetch_repo_errors_are_readable(status, msg):
    import httpx

    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(status, json={}))) as client:
        with pytest.raises(ParseError, match=msg):
            fetch_repo("o/r", client=client)
