#!/usr/bin/env python3
"""
Verify the account-recovery mail settings before deploying them.

Sends one real message through the SAME code path the tutor uses
(app.core.email_service.send), so a pass here means password reset will work.

    python scripts/test_email.py you@example.com

Reads SMTP_* from the environment, or from a .env beside this repo. Run it with
the values inline to try a set without saving them anywhere:

    SMTP_HOST=smtp.gmail.com SMTP_PORT=587 \
    SMTP_USER=mizzouceri@gmail.com \
    SMTP_PASSWORD='xxxx xxxx xxxx xxxx' \
    SMTP_FROM='SAGE AI Tutor <mizzouceri@gmail.com>' \
    python scripts/test_email.py you@example.com

GMAIL NOTES — the three things that actually cause failures:
  1. The account password will NOT work. Gmail requires a 16-character App
     Password, which requires 2-Step Verification to be on first:
     myaccount.google.com -> Security -> 2-Step Verification -> App passwords
  2. SMTP_FROM must be the authenticated account. Gmail rewrites the From header
     to the account that logged in, so "SAGE <noreply@mizzousage.net>" would be
     silently replaced. Use the gmail address as the address part.
  3. Free Gmail allows roughly 500 recipients a day. Fine for one class; if the
     course grows past that, move to SES or the university relay.
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except Exception:
    pass

import logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

from app.core import email_service  # noqa: E402


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    to_addr = sys.argv[1]

    print("Configuration the app will use:")
    for k in ("SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_FROM", "SAGE_BASE_URL"):
        print(f"  {k:<14} {os.getenv(k) or '(unset)'}")
    pw = os.getenv("SMTP_PASSWORD") or ""
    print(f"  {'SMTP_PASSWORD':<14} {'set, ' + str(len(pw)) + ' chars' if pw else '(unset)'}")
    print()

    if not email_service.is_configured():
        print("SMTP_HOST is unset, so the app is in RELAY mode: recovery still works,")
        print("but the link is written to the log instead of emailed. Nothing was sent.")
        return 1

    # A Gmail app password is shown in groups of four. Pasting it with the spaces
    # is the single most common failure, and the server-side error ("Username and
    # Password not accepted") does not mention whitespace at all.
    if " " in pw:
        print("NOTE: SMTP_PASSWORD contains spaces. Gmail displays app passwords as")
        print("      'abcd efgh ijkl mnop' but expects them with no spaces.")
        print()

    ok = email_service.send(
        to_addr,
        "SAGE test message",
        "This is a test from SAGE.\n\n"
        "If you received this, password and username recovery will reach students.\n\n"
        f"Reset links will point at {email_service.base_url()}\n",
    )
    print()
    print("SENT — check the inbox (and the spam folder)." if ok else
          "FAILED — the error and the message body are in the log above.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
