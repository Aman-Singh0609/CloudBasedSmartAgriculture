"""Account endpoints: signup, login, logout, current session, password recovery."""

from __future__ import annotations

import re

from flask import Blueprint, current_app, request
from pymongo.errors import PyMongoError

from app.errors import DatabaseUnavailableError, InvalidCredentialsError, NotFoundError, ValidationError
from app.extensions import limiter
from app.utils.payload import request_payload
from app.utils.responses import success_response
from app.utils.security import keyed_hash, load_current_user, log_user_in, log_user_out
from app.utils.validators import normalize_email, validate_login, validate_new_password, validate_signup

bp = Blueprint("auth", __name__, url_prefix="/api/auth")

_RESET_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{20,128}$")
_RESET_REQUEST_MESSAGE = (
    "If an account exists for that email address, we have sent a link to reset the password. "
    "The link is valid for a limited time."
)
_INVALID_TOKEN_MESSAGE = "This password reset link is invalid or has expired. Please request a new one."


class InvalidResetTokenError(ValidationError):
    code = "INVALID_RESET_TOKEN"
    default_message = _INVALID_TOKEN_MESSAGE


def _services():
    return current_app.extensions["services"]


def _submitted_email_key() -> str:
    """Rate-limit key for the email address being attempted (never the raw value)."""
    payload = request.get_json(silent=True) if request.is_json else None
    raw = payload.get("email") if isinstance(payload, dict) else None
    return f"email:{keyed_hash(normalize_email(raw) or 'invalid')}"


def _public_user(user) -> dict:
    if isinstance(user, dict):
        return {"id": str(user["_id"]), "name": user["name"], "email": user["email"]}
    return {"id": str(user.id), "name": user.name, "email": user.email}


@bp.get("/me")
def me():
    """Who is logged in on this session, if anyone. Never returns 401."""
    user = load_current_user()
    return success_response(
        {"authenticated": user is not None, "user": _public_user(user) if user else None},
        "Session retrieved",
    )


@bp.post("/signup")
@limiter.limit("20 per hour")
def signup():
    data = validate_signup(request_payload())
    try:
        user = _services().auth.register(**data)
    except Exception:
        _services().audit.record("auth.signup", outcome="failure")
        raise
    _services().audit.record("auth.signup", user_id=user["_id"])
    return success_response({"user": _public_user(user)}, "Account created. You can now log in.", 201)


@bp.post("/login")
@limiter.limit("20 per minute;100 per hour")
@limiter.limit(
    "8 per 15 minutes",
    key_func=_submitted_email_key,
    # Only failed attempts count towards the per-account limit.
    deduct_when=lambda response: response.status_code == 401,
)
def login():
    email, password = validate_login(request_payload())
    user = _services().auth.authenticate(email, password)
    if user is None:
        _services().audit.record(
            "auth.login_failure",
            outcome="failure",
            # A keyed hash, never the typed value: people sometimes put their
            # password in the email box.
            details={"subject_hash": keyed_hash(email or "invalid")},
        )
        # One message for every cause, so accounts cannot be enumerated.
        raise InvalidCredentialsError()

    log_user_in(user)
    _services().audit.record("auth.login_success", user_id=user["_id"])
    return success_response({"user": _public_user(user)}, "Logged in successfully")


@bp.post("/logout")
def logout():
    """End the session. POST-only and CSRF-protected so another site cannot trigger it."""
    try:
        user = load_current_user()
    except (PyMongoError, DatabaseUnavailableError):
        # Ending a session must work even when the database is unreachable.
        current_app.extensions["database"].mark_failure()
        user = None
    log_user_out()
    if user is not None:
        _services().audit.record("auth.logout", user_id=user.id)
    return success_response({}, "Logged out")


# ------------------------------------------------------------ password reset
def _require_password_reset_enabled() -> None:
    # Without a mail backend there is no honest way to offer this feature.
    if not _services().auth.password_reset_enabled:
        raise NotFoundError("Password reset by email is not available on this server.")


@bp.post("/forgot-password")
@limiter.limit("5 per hour")
@limiter.limit("3 per hour", key_func=_submitted_email_key)
def forgot_password():
    _require_password_reset_enabled()
    email = normalize_email(request_payload().get("email"))
    if email is None:
        message = "Enter a valid email address, for example name@example.com."
        raise ValidationError(message, details={"email": message})

    sent = _services().auth.request_password_reset(email)
    _services().audit.record(
        "auth.password_reset_requested",
        outcome="success" if sent else "no_action",
        details={"subject_hash": keyed_hash(email)},
    )
    # Identical response whether or not the account exists.
    return success_response({}, _RESET_REQUEST_MESSAGE, 202)


def _user_for_token(token: str) -> dict:
    user = _services().auth.find_user_for_reset_token(token) if _RESET_TOKEN_RE.match(token) else None
    if user is None:
        raise InvalidResetTokenError()
    return user


@bp.get("/reset-password/<token>")
@limiter.limit("30 per hour")
def check_reset_token(token: str):
    """Tell the reset page whether its link is still usable (does not consume it)."""
    _require_password_reset_enabled()
    _user_for_token(token)
    return success_response({"valid": True}, "Reset link is valid")


@bp.post("/reset-password/<token>")
@limiter.limit("10 per hour")
def reset_password(token: str):
    _require_password_reset_enabled()
    user = _user_for_token(token)
    new_password = validate_new_password(request_payload(), email=user["email"], name=user["name"])
    if _services().auth.reset_password(token, new_password) is None:
        raise InvalidResetTokenError()

    # Any session for this account (including one in this browser) is now stale.
    log_user_out()
    _services().audit.record("auth.password_reset_completed", user_id=user["_id"])
    return success_response({}, "Your password has been changed. Please log in.")
