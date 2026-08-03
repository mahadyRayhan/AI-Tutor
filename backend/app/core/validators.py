# backend/app/core/validators.py
#
# Server-side input validation for account creation. The frontend also validates for
# UX, but this is the authoritative check — never trust the client. Returns a
# human-readable error string (safe to show the user) or None when the field is valid.

import re

# Standard, deliberately-simple email shape check. Full RFC 5322 is overkill and
# rejects nothing users actually type; this catches the common "random string" cases.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")
_USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,30}$")

# A short list of obviously-weak passwords to reject outright.
_COMMON_PASSWORDS = {
    "password", "password1", "password123", "12345678", "123456789",
    "qwerty123", "abc12345", "letmein1", "admin123", "welcome1", "iloveyou",
    "changeme", "passw0rd",
}

MAX_LENGTHS = {"name": 80, "email": 254, "username": 30, "password": 128}


def validate_name(name: str):
    name = (name or "").strip()
    if len(name) < 2:
        return "Please enter your full name (at least 2 characters)."
    if len(name) > MAX_LENGTHS["name"]:
        return "Name is too long."
    return None


def validate_email(email: str):
    email = (email or "").strip()
    if not email:
        return "Email is required."
    if len(email) > MAX_LENGTHS["email"] or not _EMAIL_RE.match(email):
        return "Please enter a valid email address."
    return None


def validate_username(username: str):
    username = (username or "").strip()
    if not username:
        return "Username is required."
    if not _USERNAME_RE.match(username):
        return ("Username must be 3–30 characters, using only letters, numbers, "
                "and underscores.")
    return None


def validate_password(password: str, username: str = "", email: str = ""):
    pw = password or ""
    if len(pw) < 8:
        return "Password must be at least 8 characters."
    if len(pw) > MAX_LENGTHS["password"]:
        return "Password is too long (max 128 characters)."
    if not re.search(r"[a-z]", pw):
        return "Password must include a lowercase letter."
    if not re.search(r"[A-Z]", pw):
        return "Password must include an uppercase letter."
    if not re.search(r"\d", pw):
        return "Password must include a number."
    if pw.lower() in _COMMON_PASSWORDS:
        return "That password is too common — please choose a stronger one."
    if username and username.lower() in pw.lower():
        return "Password must not contain your username."
    if email:
        local = email.split("@")[0].strip().lower()
        if local and len(local) >= 3 and local in pw.lower():
            return "Password must not contain your email."
    return None


def validate_signup(name: str, email: str, username: str, password: str):
    """Run all signup checks in a sensible order. Returns (field, message) on the
    first failure, or (None, None) if everything is valid."""
    for field, err in (
        ("name", validate_name(name)),
        ("email", validate_email(email)),
        ("username", validate_username(username)),
        ("password", validate_password(password, username, email)),
    ):
        if err:
            return field, err
    return None, None


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()
