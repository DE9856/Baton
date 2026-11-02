"""Checks that a Supabase project is ready for Baton, from the database settings to what a stranger can reach.

Usage: python deploy/check_supabase.py [--account you@example.org]
Reads .env and prints no secrets. Creates Baton's tables and policies, as the app does on start. The permission
check loads the synthetic demo inside a transaction and rolls it back, so it leaves no data behind.
With --account it asks for that account's password and tries a real sign-in. Exits 1 if anything failed.
"""
from __future__ import annotations

import getpass
import os
import sys
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from baton import permissions  # noqa: E402
from baton.auth import AuthError, SupabaseAuth  # noqa: E402
from baton.db import Base, Member, Source, make_engine, make_sessionmaker  # noqa: E402
from baton.demo.loader import load_demo  # noqa: E402

failures: list[str] = []
skipped: list[str] = []


def report(status: str, what: str, detail: str = "") -> None:
    print(f"[{status:<4}] {what}" + (f": {detail}" if detail else ""))
    if status == "FAIL":
        failures.append(what)
    elif status == "SKIP":
        skipped.append(what)


def check(ok: bool, what: str, detail: str = "") -> bool:
    report("ok" if ok else "FAIL", what, detail)
    return ok


def check_url(url: str) -> bool:
    """Catches the common mistakes before trying to connect."""
    if "[YOUR-PASSWORD]" in url or "YOUR_PASSWORD" in url:
        return check(False, "Database URL", "still has the password placeholder in it")
    try:
        parsed = make_url(url)
    except ArgumentError:
        return check(False, "Database URL", "can't be read. If the password has symbols such as @ / # :, percent-encode them")
    host = parsed.host or ""
    if host.startswith("db.") and host.endswith(".supabase.co"):
        report("WARN", "Database URL", "this is the direct host, which may be IPv6-only and fail from Hugging Face. Use the Session pooler string")
    elif parsed.port == 6543:
        report("WARN", "Database URL", "this is the transaction pooler. Use the Session pooler (port 5432)")
    else:
        report("ok", "Database URL", f"host {host}, port {parsed.port}, user {parsed.username}")
    return True


def connect(url: str):
    started = time.perf_counter()
    try:
        engine = make_engine(url)
    except Exception as e:  # shown to the person running this, with the password taken out
        message = str(getattr(e, "orig", e)).split("\n")[0]
        password = make_url(url).password
        check(False, "Connect, create tables, apply policies", message.replace(password, "***") if password else message)
        print("       Check the password and use the Session pooler connection string. See docs/DEPLOY.md.")
        return None
    check(True, "Connect, create tables, apply policies", f"{time.perf_counter() - started:.1f} s")
    return engine


def check_database(engine) -> None:
    tables = sorted(t.name for t in Base.metadata.sorted_tables)
    with engine.connect() as conn:
        started = time.perf_counter()
        for _ in range(5):
            conn.execute(text("select 1"))
        ms = (time.perf_counter() - started) / 5 * 1000
        report("ok" if ms < 100 else "WARN", "Round trip to the database", f"{ms:.0f} ms" + ("" if ms < 100 else ". Pages will feel slow: the app and database are far apart"))
        is_supabase = conn.execute(text("select to_regclass('auth.users') is not null")).scalar()
        report("ok" if is_supabase else "WARN", "Supabase's auth schema", "present" if is_supabase else "absent, so this isn't a Supabase database")
        has_anon = conn.execute(text("select exists (select 1 from pg_roles where rolname = 'anon')")).scalar()
        unprotected, no_policy, can_write, anon_rights = [], [], [], []
        for t in tables:
            if not conn.execute(text("select relrowsecurity from pg_class where oid = to_regclass(:t)"), {"t": f"public.{t}"}).scalar():
                unprotected.append(t)
            if not conn.execute(text("select count(*) from pg_policies where tablename = :t and policyname like 'baton_%'"), {"t": t}).scalar():
                no_policy.append(t)
            if conn.execute(text("select has_table_privilege('authenticated', :t, 'INSERT,UPDATE,DELETE')"), {"t": f"public.{t}"}).scalar():
                can_write.append(t)
            if has_anon and conn.execute(text("select has_table_privilege('anon', :t, 'SELECT,INSERT,UPDATE,DELETE')"), {"t": f"public.{t}"}).scalar():
                anon_rights.append(t)
        check(not unprotected, f"Row-level security on all {len(tables)} tables", ", ".join(unprotected))
        check(not no_policy, "Every table has a read policy", ", ".join(no_policy))
        check(not can_write, "Signed-in users can't write directly", ", ".join(can_write))
        check(not anon_rights, "Anonymous users have no table rights", ", ".join(anon_rights))
        if has_anon:
            check(not conn.execute(text("select has_function_privilege('anon', 'baton_handover_for(integer,integer)', 'execute')")).scalar(),
                  "Anonymous users can't call the handover function")


def check_permissions(engine) -> None:
    """The demo's people against the demo's sources, as the database itself answers. Rolled back at the end."""
    Session = make_sessionmaker(engine)
    with Session() as s:
        try:
            project = load_demo(s)
            s.flush()
            people = {m.name: m for m in s.scalars(select(Member).where(Member.project_id == project.id)).all()}

            def sources_for(viewer) -> set[str]:
                with permissions.rls_scope(s, viewer):
                    return set(s.scalars(select(Source.code).where(Source.project_id == project.id)).all())

            everything = set(s.scalars(select(Source.code).where(Source.project_id == project.id)).all())
            check("N3" in everything and permissions.rls_active(s), "The demo loads with row-level security on")
            check(sources_for(people["Sana"]) == everything, "Reviewer sees every source")
            check("N3" not in sources_for(people["Meera Iyer"]), "Successor can't see the core-committee notes")
            check(len(sources_for(people["New member"])) < len(sources_for(people["Meera Iyer"])), "A new member sees fewer sources than the successor")
            stranger = type("Stranger", (), {"user_id": "00000000-0000-0000-0000-00000000dead"})()
            check(not sources_for(stranger), "A stranger sees nothing")
        finally:
            s.rollback()


def check_rest(url: str, key: str) -> None:
    """What anyone holding the public key can reach. The key ships to browsers in most Supabase apps, so assume it's known."""
    tables = sorted(t.name for t in Base.metadata.sorted_tables)
    exposed = []
    with httpx.Client(timeout=15, headers={"apikey": key}) as client:
        try:
            for t in tables:
                r = client.get(f"{url}/rest/v1/{t}", params={"select": "*", "limit": 1})
                if r.status_code == 200 and r.json():
                    exposed.append(t)
            rpc = client.post(f"{url}/rest/v1/rpc/baton_handover_for", json={"project": 1, "version_number": 1})
            if rpc.status_code == 200 and rpc.json():
                exposed.append("baton_handover_for")
            check(not exposed, "Public key can't read data over the REST API", ", ".join(exposed))

            settings = client.get(f"{url}/auth/v1/settings")
        except httpx.HTTPError as e:
            check(False, "Reaching SUPABASE_URL", type(e).__name__)
            return
    if not check(settings.status_code == 200, "Sign-in service accepts SUPABASE_ANON_KEY", f"HTTP {settings.status_code}"):
        return
    data = settings.json()
    check(bool(data.get("external", {}).get("email")), "Email sign-in is turned on")
    if data.get("mailer_autoconfirm"):
        report("WARN", "Confirm email is off", "anyone can sign up with someone else's address and open that person's projects. Turn it on: docs/DEPLOY.md")
    else:
        report("ok", "Confirm email is on")
    report("ok", "Sign-ups", "closed" if data.get("disable_signup") else "open")


def check_account(url: str, key: str, email: str) -> None:
    try:
        user = SupabaseAuth(url, key).sign_in(email, getpass.getpass(f"Password for {email}: "))
        check(True, "Real sign-in", f"account {user.id}")
    except AuthError as e:
        check(False, "Real sign-in", str(e))


def main() -> None:
    load_dotenv(ROOT / ".env")
    db_url = os.environ.get("BATON_DATABASE_URL", "").strip()
    api_url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    key = os.environ.get("SUPABASE_ANON_KEY", "").strip()
    if not db_url:
        sys.exit("BATON_DATABASE_URL isn't set. Copy .env.example to .env and fill it in, following docs/DEPLOY.md.")

    if check_url(db_url):
        engine = connect(db_url)
        if engine is not None:
            check_database(engine)
            check_permissions(engine)
            engine.dispose()
    if api_url and key:
        check_rest(api_url, key)
        if "--account" in sys.argv:
            check_account(api_url, key, sys.argv[sys.argv.index("--account") + 1])
    else:
        report("SKIP", "REST API and sign-in", "SUPABASE_URL or SUPABASE_ANON_KEY isn't set")

    print()
    if failures:
        sys.exit(f"{len(failures)} check(s) failed: {'; '.join(failures)}")
    print("Not fully checked: " + "; ".join(skipped) if skipped else "All checks passed. Warnings above, if any, are still worth fixing.")


if __name__ == "__main__":
    main()
