"""Pushes this repository to GitHub and reports the first CI run.

Usage: python deploy/publish_github.py [--dry-run]
Needs in .env: GITHUB_TOKEN and GITHUB_REPO (owner/name). The repository must already exist on GitHub.
The token goes only in the command that pushes; it is never written to the git config.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).parent.parent


def run(*args: str) -> str:
    return subprocess.run(args, cwd=ROOT, check=True, capture_output=True, text=True).stdout.strip()


def main() -> None:
    load_dotenv(ROOT / ".env")
    token, repo = os.environ.get("GITHUB_TOKEN"), os.environ.get("GITHUB_REPO")
    if not token or not repo:
        sys.exit("Missing from .env: GITHUB_TOKEN, GITHUB_REPO")
    if run("git", "status", "--porcelain"):
        sys.exit("Commit your changes first: the working tree isn't clean.")
    branch = run("git", "branch", "--show-current")
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    info = httpx.get(f"https://api.github.com/repos/{repo}", headers=headers, timeout=20)
    if info.status_code != 200:
        sys.exit(f"Can't read {repo} on GitHub (HTTP {info.status_code}). Create it first, and check the token's access.")
    print(f"{repo}: {'private' if info.json()['private'] else 'public'}; pushing {branch} and main")
    if "--dry-run" in sys.argv:
        return
    url = f"https://x-access-token:{token}@github.com/{repo}.git"
    subprocess.run(["git", "push", url, f"{branch}:{branch}", f"{branch}:main"], cwd=ROOT, check=True, capture_output=True)
    sha = run("git", "rev-parse", "HEAD")
    print("Pushed. Waiting for CI on", sha[:8])
    for _ in range(90):
        time.sleep(20)
        runs = httpx.get(f"https://api.github.com/repos/{repo}/actions/runs", params={"head_sha": sha}, headers=headers, timeout=20).json().get("workflow_runs", [])
        if runs and all(r["status"] == "completed" for r in runs):
            for r in runs:
                print(f"{r['name']}: {r['conclusion']} {r['html_url']}")
            return
    print("CI is still running. Check the Actions tab.")


if __name__ == "__main__":
    main()
