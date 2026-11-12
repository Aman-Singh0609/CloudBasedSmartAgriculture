"""Security helpers: authentication guard, session handling, response headers,
keyed hashing of identifiers and rate-limit key functions."""

from __future__ import annotations

import hashlib
import hmac
from functools import wraps

from bson import ObjectId
from bson.errors import InvalidId
from flask import Flask, current_app, g, session
from flask_limiter.util import get_remote_address

from app.errors import AuthenticationRequiredError
from app.models.schemas import CurrentUser

SESSION_USER_ID = "user_id"
SESSION_VERSION = "sv"
SESSION_WEATHER = "weather"

_SENSITIVE_KEY_PARTS = (
    "password",
    "passwd",
    "secret",
    "token",
    "cookie",
    "authorization",
    "api_key",
    "apikey",
    "appid",
    "govt",
    "government",
    "aadhaar",
    "pan_number",
    "session",
    "csrf",
)

# The React build ships only external scripts and stylesheets, so nothing
# inline is ever needed and the policy can forbid it outright.
CONTENT_SECURITY_POLICY = "; ".join(
    (
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self'",
        "img-src 'self' data:",
        "font-src 'self'",
        "connect-src 'self'",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    )
)


def is_sensitive_key(key: str) -> bool:
    """True for field names that must never be written to logs."""
    lowered = str(key).lower()
    return any(part in lowered for part in _SENSITIVE_KEY_PARTS)


def keyed_hash(value: str) -> str:
    """Stable, non-reversible fingerprint of an identifier (HMAC-SHA256, truncated).

    Lets repeated events be correlated in logs without storing the raw value.
    """
    key = current_app.config["SECRET_KEY"].encode("utf-8")
    return hmac.new(key, value.encode("utf-8"), hashlib.sha256).hexdigest()[:24]


# --------------------------------------------------------------------------
# Session handling
# --------------------------------------------------------------------------
def log_user_in(user: dict) -> None:
    """Start an authenticated session for ``user``.

    The session is cleared first so a pre-login session cannot be carried
    into the authenticated state (session fixation).
    """
    session.clear()
    session[SESSION_USER_ID] = str(user["_id"])
    session[SESSION_VERSION] = int(user.get("session_version", 1))
    session.permanent = True


def log_user_out() -> None:
    session.clear()


def load_current_user() -> CurrentUser | None:
    """Resolve the session to a user, verifying it against the database.

    Checking ``session_version`` on every authenticated request means a
    password reset invalidates all existing sessions, and a deleted account
    stops working immediately even though sessions live in signed cookies.
    """
    if "current_user" not in g:
        # Cached only on success: a database failure propagates (-> 503)
        # rather than being mistaken for "not logged in".
        g.current_user = _resolve_session_user()
    return g.current_user


def _resolve_session_user() -> CurrentUser | None:
    raw_id = session.get(SESSION_USER_ID)
    if not isinstance(raw_id, str):
        return None
    try:
        user_id = ObjectId(raw_id)
    except (InvalidId, TypeError):
        session.clear()
        return None

    user = current_app.extensions["repositories"].users.find_by_id(user_id)
    if user is None or int(user.get("session_version", 1)) != session.get(SESSION_VERSION):
        session.clear()
        return None
    return CurrentUser(id=user["_id"], name=user["name"], email=user["email"])


def login_required(view):
    """Require an authenticated session; anonymous callers receive 401."""

    @wraps(view)
    def wrapper(*args, **kwargs):
        if load_current_user() is None:
            raise AuthenticationRequiredError()
        return view(*args, **kwargs)

    return wrapper


# --------------------------------------------------------------------------
# Rate-limit keys
# --------------------------------------------------------------------------
def rate_limit_user_key() -> str:
    """Limit authenticated endpoints per account rather than per IP address.

    Farmers frequently share one public address (mobile carrier NAT, a village
    kiosk), so per-IP limits on everyday actions would punish the wrong people.
    """
    user_id = session.get(SESSION_USER_ID)
    if isinstance(user_id, str) and user_id:
        return f"user:{user_id}"
    return f"ip:{get_remote_address()}"


# --------------------------------------------------------------------------
# Response headers
# --------------------------------------------------------------------------
def register_security_headers(app: Flask) -> None:
    @app.after_request
    def set_security_headers(response):
        headers = response.headers
        headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
        headers.setdefault("X-Content-Type-Options", "nosniff")
        headers.setdefault("X-Frame-Options", "DENY")
        headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        headers.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=(), payment=()")
        headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        if app.config.get("SESSION_COOKIE_SECURE"):
            headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        # API responses are per-user; keep them out of shared caches. Hashed
        # front-end assets set their own long-lived Cache-Control first.
        headers.setdefault("Cache-Control", "no-store")
        return response
