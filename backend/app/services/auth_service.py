"""Account registration, login verification and password recovery."""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import timedelta

from werkzeug.security import check_password_hash, generate_password_hash

from app.errors import ConflictError
from app.repositories.base import utcnow
from app.repositories.password_reset_repository import PasswordResetRepository
from app.repositories.user_repository import DuplicateEmailError, UserRepository
from app.services.mail_service import MailDeliveryError, Mailer, OutgoingEmail

logger = logging.getLogger(__name__)

# Werkzeug's default: scrypt with a per-password random salt.
_HASH_METHOD = "scrypt"

# Verified against when the email is unknown so that a login attempt takes
# about as long whether or not the account exists (user enumeration by timing).
_DUMMY_HASH = generate_password_hash(secrets.token_urlsafe(32), method=_HASH_METHOD)

_RESET_TOKEN_BYTES = 32


class EmailAlreadyRegisteredError(ConflictError):
    code = "EMAIL_ALREADY_REGISTERED"
    default_message = "An account with this email address already exists. Try logging in instead."


def hash_reset_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class AuthService:
    def __init__(
        self,
        users: UserRepository,
        reset_tokens: PasswordResetRepository,
        mailer: Mailer,
        *,
        app_base_url: str,
        reset_token_ttl_minutes: int,
    ) -> None:
        self._users = users
        self._reset_tokens = reset_tokens
        self._mailer = mailer
        self._app_base_url = app_base_url.rstrip("/")
        self._reset_ttl = timedelta(minutes=reset_token_ttl_minutes)

    # ------------------------------------------------------------ accounts
    def register(self, *, name: str, email: str, password: str) -> dict:
        """Create an account. ``email`` must already be normalized."""
        try:
            return self._users.create(
                name=name,
                email=email,
                password_hash=generate_password_hash(password, method=_HASH_METHOD),
            )
        except DuplicateEmailError as exc:
            raise EmailAlreadyRegisteredError() from exc

    def authenticate(self, email: str | None, password: str | None) -> dict | None:
        """Return the user document when the credentials are correct, else None."""
        user = self._users.find_by_email(email) if email else None
        stored_hash = user.get("password_hash") if user else None
        if not isinstance(stored_hash, str) or not stored_hash:
            # Unknown account (or one without a usable hash): burn the same
            # amount of work, then fail.
            check_password_hash(_DUMMY_HASH, password or "")
            return None
        if password is None or not check_password_hash(stored_hash, password):
            return None
        self._users.record_login(user["_id"])
        return user

    # ----------------------------------------------------- password reset
    @property
    def password_reset_enabled(self) -> bool:
        return self._mailer.can_send

    def request_password_reset(self, email: str | None) -> bool:
        """Send a reset link if the account exists.

        Returns True only when a message was handed to the mailer. Callers
        must show the same response either way so the endpoint cannot be used
        to discover which emails are registered.
        """
        user = self._users.find_by_email(email) if email else None
        if user is None:
            return False

        token = secrets.token_urlsafe(_RESET_TOKEN_BYTES)
        self._reset_tokens.create(
            user_id=user["_id"],
            token_hash=hash_reset_token(token),
            expires_at=utcnow() + self._reset_ttl,
        )
        minutes = int(self._reset_ttl.total_seconds() // 60)
        link = f"{self._app_base_url}/reset-password/{token}"
        body = (
            f"Hello {user['name']},\n\n"
            "We received a request to reset the password for your AgricultureCloud account.\n\n"
            f"Open this link to choose a new password (valid for {minutes} minutes, usable once):\n"
            f"{link}\n\n"
            "If you did not ask for this, you can ignore this email and your password will stay the same.\n"
        )
        try:
            self._mailer.send(
                OutgoingEmail(to=user["email"], subject="Reset your AgricultureCloud password", body=body)
            )
        except MailDeliveryError:
            self._reset_tokens.delete_for_user(user["_id"])
            return False
        return True

    def find_user_for_reset_token(self, token: str) -> dict | None:
        """Return the account a still-valid token belongs to, without using the token."""
        record = self._reset_tokens.find_valid(hash_reset_token(token))
        if record is None:
            return None
        return self._users.find_by_id(record["user_id"])

    def reset_password(self, token: str, new_password: str) -> dict | None:
        """Set a new password using a reset token. Returns the user, or None if the token is unusable."""
        record = self._reset_tokens.consume(hash_reset_token(token))
        if record is None:
            return None
        user = self._users.find_by_id(record["user_id"])
        if user is None:
            return None
        self._users.update_password(user["_id"], generate_password_hash(new_password, method=_HASH_METHOD))
        self._reset_tokens.delete_for_user(user["_id"])
        return user
