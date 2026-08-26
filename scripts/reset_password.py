#!/usr/bin/env python3
"""
Reset a user's password.

Passwords are bcrypt-hashed, so a forgotten one cannot be recovered — only replaced.
The app has no self-service reset flow, so this is the admin path.

The new hash is produced by the app's OWN helpers (`_normalize_password` + the shared
CryptContext), never reimplemented here: passwords are SHA-256 normalized before
bcrypt to dodge bcrypt's 72-byte input limit, and a hand-rolled hash that skipped that
step would be written successfully and then fail every login.

List accounts:
    python scripts/reset_password.py --list
Dry run (default) — shows what would change, writes nothing:
    python scripts/reset_password.py --user PathfindersChallenge
Apply, with a generated password:
    python scripts/reset_password.py --user PathfindersChallenge --apply
Apply, choosing the password:
    python scripts/reset_password.py --user PathfindersChallenge --password 'my-new-pw' --apply
"""
import argparse
import os
import secrets
import sqlite3
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _resolve_db():
    """Locate the database the way the running app does.

    Mirrors scripts/seed_video_catalog.py so this works unchanged under Docker,
    where ./backend/database is mounted at /backend/database.
    """
    for candidate in (REPO, os.path.join(REPO, "backend")):
        if candidate not in sys.path:
            sys.path.insert(0, candidate)
    try:
        from app.core import config
        return str(config.DB_DIR / "ai_tutor.db")
    except Exception:
        return os.path.join(REPO, "backend", "database", "ai_tutor.db")


def _hasher():
    """Borrow the app's hashing, so the stored hash is byte-for-byte what login expects."""
    from app.core.user_manager import pwd_context, _normalize_password
    return lambda pw: pwd_context.hash(_normalize_password(pw))


def main():
    ap = argparse.ArgumentParser(description="Reset a SAGE user's password.")
    ap.add_argument("--user", help="exact username (case-sensitive)")
    ap.add_argument("--password", help="new password; generated if omitted")
    ap.add_argument("--list", action="store_true", help="list real (non-test) accounts")
    ap.add_argument("--apply", action="store_true", help="write the change")
    args = ap.parse_args()

    db_path = _resolve_db()
    print(f"database: {db_path}")
    if not os.path.exists(db_path):
        sys.exit(f"ERROR: no database at {db_path}")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    if args.list:
        rows = conn.execute(
            "SELECT username, role, name, email FROM users "
            "WHERE username NOT LIKE 'rt|_%' ESCAPE '|' "
            "  AND username NOT LIKE 'mt|_%' ESCAPE '|' "
            "  AND username NOT LIKE 'aud|_%' ESCAPE '|' "
            "  AND username NOT LIKE 'irl%' "
            "  AND username NOT LIKE 'TEST%' "
            "ORDER BY role DESC, username"
        ).fetchall()
        for r in rows:
            print(f"  {r['username']:<28} {r['role']:<8} {r['name'] or '':<24} {r['email'] or ''}")
        return

    if not args.user:
        ap.error("--user is required (or use --list)")

    row = conn.execute(
        "SELECT username, role, name, email, is_blocked FROM users WHERE username = ?",
        (args.user,),
    ).fetchone()
    if not row:
        near = conn.execute(
            "SELECT username FROM users WHERE lower(username) LIKE ?",
            (f"%{args.user.lower().replace('_', '')[:10]}%",),
        ).fetchall()
        msg = f"ERROR: no user named {args.user!r}."
        if near:
            msg += " Did you mean: " + ", ".join(n["username"] for n in near)
        sys.exit(msg)

    new_pw = args.password or secrets.token_urlsafe(12)

    print(f"\n  user:    {row['username']}")
    print(f"  role:    {row['role']}")
    print(f"  email:   {row['email'] or '(none)'}")
    if row["is_blocked"]:
        print("  status:  BLOCKED — login will still fail after the reset")
    print(f"  new password: {new_pw}")

    if not args.apply:
        print("\nDRY RUN — nothing written. Re-run with --apply to set it.")
        return

    with conn:
        conn.execute(
            "UPDATE users SET password_hash = ? WHERE username = ?",
            (_hasher()(new_pw), row["username"]),
        )

    # Verify through the real authenticate() path rather than trusting the write.
    from app.core.user_manager import UserManager
    if UserManager().authenticate(row["username"], new_pw):
        print("\nDone — verified by logging in through the app's own authenticate().")
    else:
        sys.exit("\nERROR: password written but authenticate() rejected it. Not usable.")


if __name__ == "__main__":
    main()
