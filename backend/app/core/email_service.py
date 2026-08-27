"""
Outbound email for account recovery.

Configured entirely from the environment so no credentials live in the repo:

    SMTP_HOST       e.g. smtp.gmail.com / email-smtp.us-east-2.amazonaws.com
    SMTP_PORT       587 (STARTTLS, default) or 465 (implicit TLS)
    SMTP_USER       username for the relay
    SMTP_PASSWORD   password / app password / SES SMTP secret
    SMTP_FROM       From: address, e.g. "SAGE <noreply@mizzousage.net>"
    SAGE_BASE_URL   public origin used to build links, e.g. https://mizzousage.net

If SMTP_HOST is unset the service runs in RELAY mode: nothing is sent, and the
message body is written to the log instead. That keeps account recovery usable
before a mail relay exists — the instructor reads the link out of `docker logs`
and passes it to the student — rather than silently dropping mail and leaving
students stuck with a "check your email" screen and no email.
"""
import logging
import os
import smtplib
from email.message import EmailMessage

logger = logging.getLogger(__name__)


def _cfg(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def base_url() -> str:
    return _cfg("SAGE_BASE_URL", "https://mizzousage.net").rstrip("/")


def is_configured() -> bool:
    return bool(_cfg("SMTP_HOST"))


def send(to_addr: str, subject: str, body: str) -> bool:
    """Send one plain-text message. Returns True if it was handed to the relay.

    Never raises: a recovery endpoint must not leak whether delivery succeeded,
    and must not 500 because the mail server is having a bad day.
    """
    if not is_configured():
        logger.warning(
            "[email] SMTP not configured — NOT SENT. Relay this manually:\n"
            "  To: %s\n  Subject: %s\n%s", to_addr, subject, body)
        return False

    msg = EmailMessage()
    msg["From"] = _cfg("SMTP_FROM", "SAGE <noreply@mizzousage.net>")
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg.set_content(body)

    host = _cfg("SMTP_HOST")
    port = int(_cfg("SMTP_PORT", "587"))
    user = _cfg("SMTP_USER")
    password = _cfg("SMTP_PASSWORD")

    try:
        if port == 465:
            server = smtplib.SMTP_SSL(host, port, timeout=20)
        else:
            server = smtplib.SMTP(host, port, timeout=20)
        with server:
            if port != 465:
                server.starttls()
            if user:
                server.login(user, password)
            server.send_message(msg)
        logger.info(f"[email] sent '{subject}' to {to_addr}")
        return True
    except Exception as e:
        # Log the body so a failed send is still recoverable by hand.
        logger.error("[email] send FAILED to %s: %s\nBody was:\n%s", to_addr, e, body)
        return False
