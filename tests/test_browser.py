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
def browser():
    with playwright_api.sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


def shot(page, name):
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS / f"{name}.png"), full_page=True)


def view_as(page, name):
    page.select_option("#viewer-select", label=name)
    page.wait_for_load_state("networkidle")


def test_first_time_judge_walkthrough(server, browser):
    errors = []
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    page = context.new_page()
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("response", lambda r: errors.append(f"{r.status} {r.url}") if r.status >= 500 else None)

    # 1. Landing: one obvious way to begin.
    page.goto(server)
    shot(page, "01_home")
    page.click("text=Load the demo project")

    # 2. Connect: the pipeline runs in the background and reports progress through HTMX.
    page.wait_for_selector("text=Run finished", timeout=240_000)
    assert page.locator("table >> text=WhatsApp: DevCircle Tech").count() == 1
    assert "core committee" not in page.locator("table").first.inner_text().lower()   # Arjun isn't cleared for it
    shot(page, "02_connect_as_leaver")

    # 3. The leaver answers one-tap and free-text questions.
    page.click("nav.steps >> text=Gap interview")
    shot(page, "03_interview")
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
    body = page.content()
    assert "Start here" in body and "Confirmed by Arjun Mehta" in body and "AIzaSy" not in body and "48,500" not in body
    page.locator("details.receipts summary").first.click()
    assert page.locator("blockquote:visible").count() >= 1
    page.locator("#start_here").get_by_role("button", name="Acknowledge").click()
    page.wait_for_load_state("networkidle")
    assert "1 of 10 sections acknowledged" in page.content()

    # 6. Another role sees less.
    view_as(page, "New member")
    page.wait_for_load_state("networkidle")
    shot(page, "07_handover_new_member")
    assert "campusconnect.in" not in page.content()

    # 7. Phone width: no sideways page scroll on the two pages used on a phone.
    phone = browser.new_context(viewport={"width": 390, "height": 800}, storage_state=context.storage_state()).new_page()
    phone.goto(page.url.split("/p/")[0] + "/p/" + page.url.split("/p/")[1].split("/")[0] + "/handover")
    overflow = phone.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
    shot(phone, "08_phone_handover")
    assert overflow <= 1, f"page scrolls sideways by {overflow}px"

    assert not errors, errors
