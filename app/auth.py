"""Access control for the Film Room API.

One operator, one access code. Logging in issues a random session token that
is sent back as an HttpOnly, SameSite=Strict cookie. Only a SHA-256 hash of the
token is stored, so a copy of the database cannot be used to log in.

Failed logins are counted per client and blocked for a while after repeated
failures. The counter runs before the access check, so guessing is limited
even when every guess is wrong.
"""
import hashlib
import hmac
import secrets
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

OWNER = "owner"
SESSION_HOURS = 12
COOKIE_NAME = "fr_session"
LOGIN_WINDOW_SECONDS = 15 * 60
LOGIN_MAX_FAILURES = 5

_failures = defaultdict(deque)
_failures_lock = threading.Lock()


def now_utc():
    return datetime.now(timezone.utc)


def iso(moment):
    return moment.isoformat(timespec="seconds")


def hash_token(token):
    return hashlib.sha256(token.encode()).hexdigest()


def code_matches(supplied, expected):
    if not expected or not isinstance(supplied, str):
        return False
    return hmac.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8"))


def login_blocked(client_key):
    cutoff = time.monotonic() - LOGIN_WINDOW_SECONDS
    with _failures_lock:
        attempts = _failures[client_key]
        while attempts and attempts[0] < cutoff:
            attempts.popleft()
        return len(attempts) >= LOGIN_MAX_FAILURES


def record_login_failure(client_key):
    with _failures_lock:
        _failures[client_key].append(time.monotonic())


def clear_login_failures(client_key):
    with _failures_lock:
        _failures.pop(client_key, None)


def create_session(conn):
    token = secrets.token_urlsafe(32)
    created = now_utc()
    conn.execute(
        "INSERT INTO auth_sessions (session_hash, owner, created_at, expires_at) VALUES (?, ?, ?, ?)",
        (hash_token(token), OWNER, iso(created), iso(created + timedelta(hours=SESSION_HOURS))),
    )
    conn.commit()
    return token, iso(created + timedelta(hours=SESSION_HOURS))


def session_owner(conn, token):
    """Return the owner for a live session token, or None."""
    if not token:
        return None
    row = conn.execute(
        "SELECT owner, expires_at FROM auth_sessions WHERE session_hash = ?",
        (hash_token(token),),
    ).fetchone()
    if row is None:
        return None
    if row["expires_at"] <= iso(now_utc()):
        end_session(conn, token)
        return None
    return row["owner"]


def end_session(conn, token):
    conn.execute("DELETE FROM auth_sessions WHERE session_hash = ?", (hash_token(token),))
    conn.commit()


def purge_expired_sessions(conn):
    conn.execute("DELETE FROM auth_sessions WHERE expires_at <= ?", (iso(now_utc()),))
    conn.commit()
