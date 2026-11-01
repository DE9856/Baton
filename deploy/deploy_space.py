"""Creates or updates the public Hugging Face Space and sets its secrets from .env.

Usage: python deploy/deploy_space.py [--dry-run]
Needs in .env: HF_TOKEN and HF_SPACE (owner/name), plus BATON_DATABASE_URL, SUPABASE_URL and SUPABASE_ANON_KEY.
GROQ_API_KEY is optional. Secret values are sent only to Hugging Face and never printed.
"""
from __future__ import annotations

import os
import secrets
import sys
import tempfile
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).parent.parent
FRONT_MATTER = """---
title: Baton
emoji: 🏃
colorFrom: green
colorTo: blue
sdk: docker
app_port: 7860
pinned: false
short_description: Verified, permission-safe project handovers
---

# Baton

Turns a departing member's authorised project trail into a verified, permission-safe handover.
Team sky-flyers, WCC Forge 48. Use the direct link to this Space so that sign-in and role switching work.
"""
REQUIRED = ("HF_TOKEN", "HF_SPACE", "BATON_DATABASE_URL", "SUPABASE_URL", "SUPABASE_ANON_KEY")
SECRETS = ("BATON_DATABASE_URL", "SUPABASE_URL", "SUPABASE_ANON_KEY", "GROQ_API_KEY", "BATON_EXTRACT_MODEL")


def main() -> None:
    load_dotenv(ROOT / ".env")
    missing = [k for k in REQUIRED if not os.environ.get(k)]
    if missing:
        sys.exit(f"Missing from .env: {', '.join(missing)}")
    space = os.environ["HF_SPACE"]
    if "--dry-run" in sys.argv:
        print(f"Would deploy to {space} with secrets:", [k for k in SECRETS if os.environ.get(k)])
        return

    from huggingface_hub import HfApi

    api = HfApi(token=os.environ["HF_TOKEN"])
    api.create_repo(space, repo_type="space", space_sdk="docker", exist_ok=True, private=False)
    for key in SECRETS:
        if os.environ.get(key):
            api.add_space_secret(space, key, os.environ[key])
    # Signing the cookie with a fixed secret keeps people signed in across restarts.
    api.add_space_secret(space, "BATON_SECRET", os.environ.get("BATON_SECRET") or secrets.token_hex(32))
    api.add_space_variable(space, "BATON_HTTPS_ONLY", "1")
    api.add_space_variable(space, "BATON_DATA_DIR", "/tmp/baton")

    with tempfile.TemporaryDirectory() as tmp:
        readme = Path(tmp) / "README.md"
        readme.write_text(FRONT_MATTER, "utf-8")
        api.upload_file(path_or_fileobj=str(readme), path_in_repo="README.md", repo_id=space, repo_type="space")
    for name in ("Dockerfile", "requirements.txt", ".dockerignore"):
        api.upload_file(path_or_fileobj=str(ROOT / name), path_in_repo=name, repo_id=space, repo_type="space")
    api.upload_folder(folder_path=str(ROOT / "baton"), path_in_repo="baton", repo_id=space, repo_type="space",
                      ignore_patterns=["__pycache__/*", "*.pyc"], commit_message="Deploy Baton")
    owner, name = space.split("/", 1)
    print(f"Deployed. The Space is building. Public link: https://{owner}-{name}.hf.space".lower())


if __name__ == "__main__":
    main()
