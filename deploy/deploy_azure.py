"""Creates or updates Baton on Azure Container Apps, using the image GitHub Actions publishes to ghcr.io.

Usage: python deploy/deploy_azure.py [--dry-run]
Needs: `az login`, the containerapp extension, and an existing environment (see docs/DEPLOY.md).
Reads credentials from .env and sends them to Azure as secrets. They are never printed.
"""
from __future__ import annotations

import os
import secrets
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).parent.parent
APP = os.environ.get("AZURE_APP", "baton")
GROUP = os.environ.get("AZURE_GROUP", "baton-rg")
ENV = os.environ.get("AZURE_ENV", "baton-env")
IMAGE = os.environ.get("AZURE_IMAGE", "ghcr.io/de9856/baton:latest")
REQUIRED = ("BATON_DATABASE_URL", "SUPABASE_URL", "SUPABASE_ANON_KEY")
# Environment variable -> Azure secret name (lower case, dashes).
SECRETS = {k: k.lower().replace("_", "-") for k in
           ("BATON_DATABASE_URL", "SUPABASE_URL", "SUPABASE_ANON_KEY", "GROQ_API_KEY", "BATON_SECRET")}


def az(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["az", *args, "--only-show-errors"], capture_output=True, text=True)


def main() -> None:
    load_dotenv(ROOT / ".env")
    missing = [k for k in REQUIRED if not os.environ.get(k, "").strip()]
    if missing:
        sys.exit(f"Missing from .env: {', '.join(missing)}")
    if not os.environ.get("BATON_SECRET", "").strip():
        os.environ["BATON_SECRET"] = secrets.token_urlsafe(32)
    values = {k: os.environ[k].strip() for k in SECRETS if os.environ.get(k, "").strip()}
    secret_args = [f"{SECRETS[k]}={v}" for k, v in values.items()]
    env_args = [f"{k}=secretref:{SECRETS[k]}" for k in values] + [
        f"BATON_EXTRACT_MODEL={os.environ.get('BATON_EXTRACT_MODEL', 'openai/gpt-oss-120b')}",
        "BATON_HTTPS_ONLY=1",
        "BATON_DATA_DIR=/data",
    ]
    if "--dry-run" in sys.argv:
        print(f"Would deploy {IMAGE} as {APP} in {GROUP}/{ENV} with secrets:", sorted(values))
        return

    exists = az("containerapp", "show", "-n", APP, "-g", GROUP, "--query", "name", "-o", "tsv").returncode == 0
    if exists:
        r = az("containerapp", "secret", "set", "-n", APP, "-g", GROUP, "--secrets", *secret_args)
        if r.returncode:
            sys.exit(r.stderr)
        r = az("containerapp", "update", "-n", APP, "-g", GROUP, "--image", IMAGE,
               "--revision-suffix", f"r{secrets.token_hex(3)}", "--set-env-vars", *env_args)
    else:
        r = az("containerapp", "create", "-n", APP, "-g", GROUP, "--environment", ENV, "--image", IMAGE,
               "--target-port", "7860", "--ingress", "external", "--cpu", "1.0", "--memory", "2.0Gi",
               # Jobs run in-process, so exactly one replica, always on (no cold start for judges).
               "--min-replicas", "1", "--max-replicas", "1",
               "--secrets", *secret_args, "--env-vars", *env_args)
    if r.returncode:
        sys.exit(r.stderr)
    fqdn = az("containerapp", "show", "-n", APP, "-g", GROUP,
              "--query", "properties.configuration.ingress.fqdn", "-o", "tsv").stdout.strip()
    print(f"Deployed. Link: https://{fqdn}")


if __name__ == "__main__":
    main()
