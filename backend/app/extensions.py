"""Flask extension instances shared across the application."""

from __future__ import annotations

from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_wtf.csrf import CSRFProtect

csrf = CSRFProtect()

# A generous default guards every endpoint; sensitive routes add tighter,
# purpose-specific limits. Static assets and health checks are exempt.
DEFAULT_LIMIT = "300 per minute"

limiter = Limiter(key_func=get_remote_address, default_limits=[DEFAULT_LIMIT])
