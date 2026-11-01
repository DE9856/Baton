"""Drives the real server in a real browser through the whole workflow, as a first-time judge would.

Run: pytest -m browser tests/test_browser.py. Screenshots go to the folder in BATON_SHOTS (default: test-results/shots).
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

pytestmark = pytest.mark.browser
playwright_api = pytest.importorskip("playwright.sync_api")
ROOT = Path(__file__).parent.parent
SHOTS = Path(os.environ.get("BATON_SHOTS", ROOT / "test-results" / "shots"))


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = {**os.environ, "BATON_DATA_DIR": str(tmp_path_factory.mktemp("data")), "BATON_SECRET": "test-secret"}
    env.pop("ANTHROPIC_API_KEY", None)
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "baton.web.app:get_app", "--factory", "--port", str(port)], cwd=ROOT, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f"http://127.0.0.1:{port}"
    for _ in range(120):
        try:
            if httpx.get(f"{url}/healthz", timeout=1).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.5)
    else:
        proc.kill()
        raise RuntimeError("Server did not start")
    yield url
    proc.terminate()
    proc.wait(timeout=20)


@pytest.fixture(scope="module")
def pw():
    with playwright_api.sync_playwright() as p:
        yield p


@pytest.fixture(scope="module")
def browser(pw):
    b = pw.chromium.launch()
    yield b
    b.close()


def shot(page, name):
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS / f"{name}.png"), full_page=True)


def view_as(page, name):
    page.select_option("#viewer-select", label=name)
    page.wait_for_load_state("networkidle")


AXE = (ROOT / "tests" / "vendor" / "axe.min.js").read_text("utf-8")
A11Y_CHECKED: list[str] = []


def check_accessibility(page, name):
    """WCAG 2.1 A and AA rules from axe-core. Serious and critical problems fail the walkthrough."""
    page.evaluate(AXE)
    result = page.evaluate("axe.run(document, {runOnly: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']})")
    bad = [(v["id"], v["impact"], v["nodes"][0]["html"][:120], len(v["nodes"])) for v in result["violations"] if v["impact"] in ("serious", "critical")]
    A11Y_CHECKED.append(name)
    assert not bad, f"{name}: {bad}"


def test_first_time_judge_walkthrough(server, browser):
    errors = []
    video_dir = os.environ.get("BATON_VIDEO_DIR")
    context = browser.new_context(viewport={"width": 1280, "height": 900}, record_video_dir=video_dir, record_video_size={"width": 1280, "height": 900})
    page = context.new_page()
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("response", lambda r: errors.append(f"{r.status} {r.url}") if r.status >= 500 else None)

    # 1. Landing: one obvious way to begin.
    page.goto(server)
    shot(page, "01_home")
    check_accessibility(page, "home")
    page.click("text=Load the demo project")

    # 2. Connect: the pipeline runs in the background and reports progress through HTMX.
    page.wait_for_selector("text=Run finished", timeout=240_000)
    assert page.locator("table >> text=WhatsApp: DevCircle Tech").count() == 1
    assert "core committee" not in page.locator("table").first.inner_text().lower()   # Arjun isn't cleared for it
    shot(page, "02_connect_as_leaver")
    check_accessibility(page, "connect")

    # 3. The leaver answers one-tap and free-text questions.
    page.click("nav.steps >> text=Gap interview")
    shot(page, "03_interview")
    check_accessibility(page, "interview")
    assert page.locator("article.question").count() >= 6
    domain = page.locator("article.question", has_text="domain")
    domain.get_by_role("button", name="Meera takes it over").click()
    page.wait_for_load_state("networkidle")
    assert "Saved as your word" in page.content()
    deploy = page.locator("article.question", has_text="deployed")
    deploy.locator("textarea").fill("Push to main; Render auto-deploys. Env vars live in the Render dashboard.")
    deploy.get_by_role("button", name="Save answer").click()
    page.wait_for_load_state("networkidle")

    # 4. The reviewer: can't share yet, then reviews by exception.
    view_as(page, "Sana (reviewer)")
    page.click("nav.steps >> text=Review")
    shot(page, "04_review_console")
    check_accessibility(page, "review")
    assert "Not ready to share yet" in page.content()
    assert page.locator("article.conflict").count() == 3
    while page.get_by_role("button", name="Newer source is correct").count():
        page.get_by_role("button", name="Newer source is correct").first.click()
        page.wait_for_load_state("networkidle")
    page.get_by_role("button", name="Approve all").click()
    page.wait_for_load_state("networkidle")
    while page.locator("#decide").get_by_role("button", name="Keep withheld").count():
        page.locator("#decide").get_by_role("button", name="Keep withheld").first.click()
        page.wait_for_load_state("networkidle")
    while page.locator("#decide").get_by_role("button", name="Approve", exact=True).count():
        page.locator("#decide").get_by_role("button", name="Approve", exact=True).first.click()
        page.wait_for_load_state("networkidle")
    shot(page, "05_review_ready")
    page.get_by_role("button", name="Approve and share").click()
    page.wait_for_load_state("networkidle")
    assert "Approved version 1" in page.content()

    # 5. The successor sees the plan; receipts open; a section can be acknowledged.
    view_as(page, "Meera Iyer (successor)")
    page.click("nav.steps >> text=Handover")
    page.wait_for_load_state("networkidle")
    shot(page, "06_handover_meera")
    check_accessibility(page, "handover")
    body = page.content()
    assert "Start here" in body and "Confirmed by Arjun Mehta" in body and "AIzaSy" not in body and "48,500" not in body
    page.locator("details.receipts summary").first.click()
    assert page.locator("blockquote:visible").count() >= 1
    page.locator("#start_here").get_by_role("button", name="Acknowledge").click()
    page.wait_for_load_state("networkidle")
    assert "1 of 10 sections acknowledged" in page.content()

    # Ask Baton: a cited answer, then an honest "not known".
    page.get_by_label("Ask Baton a question").fill("When is the domain renewal due?")
    page.get_by_role("button", name="Ask Baton", exact=True).click()
    page.wait_for_load_state("networkidle")
    shot(page, "07_ask_answer")
    check_accessibility(page, "ask")
    assert "2026-11-20" in page.content()
    page.get_by_label("Your question").fill("Which payment gateway did we choose?")
    page.get_by_role("button", name="Ask", exact=True).click()
    page.wait_for_load_state("networkidle")
    assert "Not known" in page.content()
    page.click("nav.steps >> text=Handover")
    page.wait_for_load_state("networkidle")

    # 6. Another role sees less.
    view_as(page, "New member")
    page.wait_for_load_state("networkidle")
    shot(page, "07b_handover_new_member")
    assert "campusconnect.in" not in page.content()

    # 7. Phone width: no sideways page scroll on the two pages used on a phone.
    phone = browser.new_context(viewport={"width": 390, "height": 800}, storage_state=context.storage_state()).new_page()
    phone.goto(page.url.split("/p/")[0] + "/p/" + page.url.split("/p/")[1].split("/")[0] + "/handover")
    overflow = phone.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
    shot(phone, "08_phone_handover")
    assert overflow <= 1, f"page scrolls sideways by {overflow}px"

    # 8. Living handover: new activity reopens what it contradicts, and a second version shows the change.
    view_as(page, "Sana (reviewer)")
    page.click("nav.steps >> text=Connect")
    page.wait_for_load_state("networkidle")
    page.get_by_role("button", name="Simulate new activity").click()
    page.wait_for_selector("text=Run finished", timeout=240_000)
    assert "4 new record(s)" in page.content()
    page.click("nav.steps >> text=Review")
    page.wait_for_load_state("networkidle")
    shot(page, "09_review_after_update")
    while page.get_by_role("button", name="Newer source is correct").count():
        page.get_by_role("button", name="Newer source is correct").first.click()
        page.wait_for_load_state("networkidle")
    if page.get_by_role("button", name="Approve all").count():
        page.get_by_role("button", name="Approve all").click()
        page.wait_for_load_state("networkidle")
    while page.locator("#decide").get_by_role("button", name="Keep withheld").count():
        page.locator("#decide").get_by_role("button", name="Keep withheld").first.click()
        page.wait_for_load_state("networkidle")
    while page.locator("#decide").get_by_role("button", name="Approve", exact=True).count():
        page.locator("#decide").get_by_role("button", name="Approve", exact=True).first.click()
        page.wait_for_load_state("networkidle")
    page.get_by_role("button", name="Approve and share").click()
    page.wait_for_load_state("networkidle")
    assert "Approved version 2" in page.content()
    view_as(page, "Meera Iyer (successor)")
    page.click("nav.steps >> text=Handover")
    page.wait_for_load_state("networkidle")
    shot(page, "10_handover_v2")
    assert "What changed in version 2" in page.content()
    assert "Staging#2026x" not in page.content()

    video = page.video
    context.close()
    if video_dir and video:
        print("video:", video.path())
    assert not errors, errors


def test_voice_answer_with_a_real_microphone_stream(server, pw):
    """Chromium plays a speech file as its microphone, so the whole path runs: record, upload, Whisper, edit, save."""
    sample = ROOT / "tests" / "data" / "speech_sample.wav"
    errors = []
    b = pw.chromium.launch(args=["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream", f"--use-file-for-fake-audio-capture={sample}"])
    context = b.new_context(viewport={"width": 1280, "height": 900})
    context.grant_permissions(["microphone"])
    page = context.new_page()
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(server)
    page.click("text=Load the demo project")
    page.wait_for_selector("text=Run finished", timeout=240_000)
    page.click("nav.steps >> text=Gap interview")
    page.wait_for_load_state("networkidle")
    deploy = page.locator("article.question", has_text="deployed")
    deploy.get_by_role("button", name="Record a voice answer").click()
    page.wait_for_timeout(6000)
    deploy.get_by_role("button", name="Stop and send").click()
    page.wait_for_selector("text=a draft of what you said", timeout=180_000)
    deploy = page.locator("article.question", has_text="deployed")
    draft = deploy.locator("textarea").input_value()
    assert draft.strip(), "no transcript"
    assert deploy.locator("audio").count() == 1
    shot(page, "11_voice_draft")
    deploy.get_by_role("button", name="Save answer").click()
    page.wait_for_load_state("networkidle")
    assert "Saved as your word" in page.content()
    b.close()
    assert not errors, errors
    print("transcript:", draft)
