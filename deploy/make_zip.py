"""Zips the project to send to another system: python deploy/make_zip.py [output.zip]

Includes exactly what git tracks, the history in .git, and docs/reference/. By construction that leaves out .venv,
.env, local data and caches. Standard library only, and it checks the result before finishing.
Commit first: uncommitted changes to tracked files are included as they are on disk, but new files are not.
"""
from __future__ import annotations

import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MUST_HAVE = ["CLAUDE.md", "requirements.txt", ".env.example", "baton/demo/data/whatsapp_devcircle_tech.txt",
             "baton/demo/data/notes/2026-09-12-tech-sync.md", "baton/demo/recorded_extraction_qwen25.json",
             "tests/data/speech_sample.wav", ".git/HEAD"]
MUST_NOT_HAVE = (".env", ".venv/", "data/")


def project_files() -> list[str]:
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, check=True, capture_output=True, text=True, encoding="utf-8").stdout
    return [f for f in out.split("\0") if f]


def main() -> None:
    target = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else ROOT.parent / "baton-handoff.zip"
    files = [ROOT / f for f in project_files()]
    for folder in (".git", "docs/reference"):
        files += [p for p in (ROOT / folder).rglob("*") if p.is_file()]
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
        for path in files:
            if path.exists():  # a tracked file deleted on disk is skipped
                z.write(path, path.relative_to(ROOT).as_posix())
    with zipfile.ZipFile(target) as z:
        names = set(z.namelist())
    missing = [f for f in MUST_HAVE if f not in names]
    unwanted = sorted(n for n in names if n == ".env" or n.startswith((".venv/", "data/")))
    if missing or unwanted:
        target.unlink()
        sys.exit(f"Zip not made. Missing: {missing}. Must not include: {unwanted[:5]}.")
    print(f"Wrote {target} ({target.stat().st_size / 1e6:.1f} MB, {len(names)} files).")
    if any(n.startswith("docs/reference/") for n in names):
        print("It includes docs/reference/, which has team members' phone numbers. Share it only with the team.")


if __name__ == "__main__":
    main()
