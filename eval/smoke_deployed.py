"""Smoke test for a running deployment: loads the demo, waits for the pipeline, checks permissions over real HTTP.

Usage: python eval/smoke_deployed.py http://localhost:7860
"""
from __future__ import annotations

import re
import sys
import time

import httpx

CANARIES = ["AIzaSyD4-DemoKey", "DevCircle@2026", "48,500", "misuse of club funds", "99000 55511", "98450 12345", "surgery"]


def main(base: str) -> None:
    with httpx.Client(base_url=base, follow_redirects=True, timeout=60) as c:
        assert c.get("/healthz").json()["status"] == "ok"
        r = c.post("/demo")
        pid = re.search(r"/p/(\d+)/", str(r.url)).group(1)
        started = time.time()

        def sign_in(name: str) -> None:
            c.post(f"/p/{pid}/logout")
            r = c.post(f"/p/{pid}/login", data={"user_id": name, "password": "pass"})
            assert f"{name} (" in r.text, f"could not sign in as {name}"

        sign_in("Sana")
        while True:
            t = c.get(f"/p/{pid}/job").text
            if "Run finished" in t:
                break
            assert "failed" not in t, t
            assert time.time() - started < 900, "pipeline timed out"
            time.sleep(3)
        print(f"Pipeline finished in {time.time() - started:.0f} s:", re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", t)).strip()[:150])
        c.post(f"/p/{pid}/review/one-pass")
        page = c.get(f"/p/{pid}/review").text
        for cf in re.findall(r"/conflicts/(\d+)/resolve", page):
            c.post(f"/p/{pid}/conflicts/{cf}/resolve", data={"choice": "b"})
        page = c.get(f"/p/{pid}/review").text
        decide = page.split('id="decide"')[1].split('id="questions"')[0]
        for block in re.split(r'<article class="claim', decide)[1:]:
            m = re.search(r"/claims/(\d+)/decide", block)
            if m:
                action = "reject" if "Keep withheld" in block else "approve"
                c.post(f"/p/{pid}/claims/{m.group(1)}/decide", data={"action": action})
        assert "Approved version 1" in c.post(f"/p/{pid}/approve").text, "approval failed"
        for name in ("Meera Iyer", "New member"):
            sign_in(name)
            body = c.get(f"/p/{pid}/handover").text + c.get(f"/p/{pid}/export.md").text
            leaks = [x for x in CANARIES if x.lower() in body.lower()]
            assert not leaks, f"{name} saw {leaks}"
            print(f"{name}: handover loaded, 0 of {len(CANARIES)} canaries leaked")
        print("SMOKE TEST PASSED")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "http://localhost:7860")
