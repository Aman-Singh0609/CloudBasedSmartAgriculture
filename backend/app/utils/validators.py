"""Server-side input validation.

The browser validates too, but only for convenience: everything here assumes
the request may have been hand-crafted. Validators either return clean values
or raise :class:`~app.errors.ValidationError` whose ``details`` map each field
name to a human-readable message.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Mapping
from typing import Any

from bson import ObjectId
from bson.errors import InvalidId

from app.errors import NotFoundError, ValidationError
from app.models.features import FEATURES, FeatureSpec, format_number

# Plain decimal notation only. This deliberately rejects forms that Python's
# float() would accept: exponents ("1e309" -> inf), "nan", "inf", digit-group
# underscores ("1_000"), hexadecimal and non-ASCII digits.
_DECIMAL_RE = re.compile(r"^[+-]?(?:\d{1,12}(?:\.\d{0,6})?|\.\d{1,6})$", re.ASCII)

_EMAIL_RE = re.compile(
    r"^[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$"
)
# U+2019 is the typographic apostrophe that phone keyboards insert (O’Brien).
_NAME_PUNCTUATION = " .'-’"
_CITY_PUNCTUATION = " .,'-’"

EMAIL_MAX_LENGTH = 254
NAME_MIN_LENGTH = 2
NAME_MAX_LENGTH = 80
CITY_MIN_LENGTH = 2
CITY_MAX_LENGTH = 80
PASSWORD_MIN_LENGTH = 8
PASSWORD_MAX_LENGTH = 128

DEFAULT_PAGE_SIZE = 10
MAX_PAGE_SIZE = 50
MAX_PAGE_NUMBER = 100_000

# Passwords that satisfy the length and character rules but are among the
# first guesses in any credential-stuffing list.
_COMMON_PASSWORDS = frozenset(
    {
        "password1",
        "password12",
        "password123",
        "passw0rd",
        "p@ssw0rd",
        "qwerty123",
        "qwertyuiop1",
        "abc12345",
        "abcd1234",
        "a1b2c3d4",
        "12345678a",
        "1234567a",
        "iloveyou1",
        "welcome1",
        "welcome123",
        "admin123",
        "admin1234",
        "letmein1",
        "letmein123",
        "farmer123",
        "agriculture1",
        "india123",
        "test1234",
        "changeme1",
    }
)


# --------------------------------------------------------------------------
# Prediction inputs
# --------------------------------------------------------------------------
def _range_text(spec: FeatureSpec) -> str:
    unit = f" {spec.unit}" if spec.unit else ""
    return f"{format_number(spec.hard_min)} and {format_number(spec.hard_max)}{unit}"


def parse_number(raw: Any) -> float | None:
    """Convert a form/JSON value to a finite float, or return None if it is not one."""
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        value = float(raw)
    elif isinstance(raw, str):
        text = raw.strip()
        if not _DECIMAL_RE.match(text):
            return None
        value = float(text)
    else:
        return None
    return value if math.isfinite(value) else None


def validate_prediction_inputs(payload: Mapping[str, Any]) -> tuple[dict[str, float], list[str]]:
    """Validate the seven model inputs.

    Returns ``(values, out_of_model_range)`` where ``values`` maps each feature
    key to a float and ``out_of_model_range`` lists the keys whose value is
    physically acceptable but outside the range the model was trained on.
    """
    if not isinstance(payload, Mapping):
        raise ValidationError("Send the seven readings as a JSON object or form fields.")

    values: dict[str, float] = {}
    errors: dict[str, str] = {}
    out_of_model_range: list[str] = []

    for spec in FEATURES:
        raw = payload.get(spec.key)
        if raw is None or (isinstance(raw, str) and raw.strip() == ""):
            errors[spec.key] = f"{spec.label} is required."
            continue
        value = parse_number(raw)
        if value is None:
            errors[spec.key] = f"{spec.label} must be a number, for example {format_number(spec.example)}."
            continue
        if value < spec.hard_min or value > spec.hard_max:
            errors[spec.key] = f"{spec.label} must be between {_range_text(spec)}."
            continue
        values[spec.key] = value
        if value < spec.model_min or value > spec.model_max:
            out_of_model_range.append(spec.key)

    if errors:
        raise ValidationError("Please correct the highlighted readings and try again.", details=errors)
    return values, out_of_model_range


# --------------------------------------------------------------------------
# Accounts
# --------------------------------------------------------------------------
def _require_string(raw: Any) -> str | None:
    """Reject non-string values so operator objects cannot reach a query."""
    return raw if isinstance(raw, str) else None


def _is_text_of(text: str, punctuation: str) -> bool:
    """True when ``text`` starts with a letter and contains only letters,
    combining marks and the given punctuation.

    Unicode categories are used instead of the regex word class because that
    class does not match the combining vowel signs of Indic scripts, which
    would reject names such as "रमेश" or places such as "पुणे".
    """
    if not text or not unicodedata.category(text[0]).startswith("L"):
        return False
    return all(ch in punctuation or unicodedata.category(ch)[0] in ("L", "M") for ch in text)


def normalize_email(raw: Any) -> str | None:
    """Trim and lower-case an email address; return None if it is not valid."""
    text = _require_string(raw)
    if text is None:
        return None
    email = text.strip().lower()
    if not email or len(email) > EMAIL_MAX_LENGTH or ".." in email:
        return None
    local, _, _domain = email.partition("@")
    if len(local) > 64 or local.startswith(".") or local.endswith("."):
        return None
    return email if _EMAIL_RE.match(email) else None


def _clean_name(raw: Any) -> tuple[str | None, str | None]:
    text = _require_string(raw)
    if text is None:
        return None, "Full name is required."
    name = " ".join(unicodedata.normalize("NFC", text).split())
    if not name:
        return None, "Full name is required."
    if len(name) < NAME_MIN_LENGTH or len(name) > NAME_MAX_LENGTH:
        return None, f"Full name must be between {NAME_MIN_LENGTH} and {NAME_MAX_LENGTH} characters."
    if not _is_text_of(name, _NAME_PUNCTUATION):
        return None, "Full name can contain letters, spaces, hyphens, apostrophes and full stops only."
    return name, None


def password_problem(password: Any, *, email: str | None = None, name: str | None = None) -> str | None:
    """Return a message describing why a new password is unacceptable, or None."""
    if not isinstance(password, str) or password == "":
        return "Password is required."
    if len(password) < PASSWORD_MIN_LENGTH:
        return f"Password must be at least {PASSWORD_MIN_LENGTH} characters long."
    if len(password) > PASSWORD_MAX_LENGTH:
        return f"Password must be at most {PASSWORD_MAX_LENGTH} characters long."
    if not any(ch.isalpha() for ch in password) or not any(ch.isdigit() for ch in password):
        return "Password must contain at least one letter and one number."
    lowered = password.lower()
    if lowered in _COMMON_PASSWORDS:
        return "That password is too common. Please choose a less predictable one."
    if email:
        local = email.split("@", 1)[0]
        if len(local) >= 4 and local in lowered:
            return "Password must not contain your email address."
    if name:
        for part in name.lower().split():
            if len(part) >= 4 and part in lowered:
                return "Password must not contain your name."
    return None


def validate_signup(payload: Mapping[str, Any]) -> dict[str, str]:
    """Validate a signup request and return ``{name, email, password}``."""
    errors: dict[str, str] = {}

    name, name_error = _clean_name(payload.get("name"))
    if name_error:
        errors["name"] = name_error

    email = normalize_email(payload.get("email"))
    if email is None:
        errors["email"] = "Enter a valid email address, for example name@example.com."

    password = payload.get("password")
    problem = password_problem(password, email=email, name=name)
    if problem:
        errors["password"] = problem
    elif payload.get("confirm_password") != password:
        errors["confirm_password"] = "The two passwords do not match."

    if errors:
        raise ValidationError("Please correct the highlighted fields.", details=errors)
    return {"name": name, "email": email, "password": password}


def validate_login(payload: Mapping[str, Any]) -> tuple[str | None, str | None]:
    """Return ``(email, password)``; either is None when it cannot be valid.

    Login failures are reported generically, so this never raises: an
    unusable email or password simply cannot match an account.
    """
    email = normalize_email(payload.get("email"))
    password = _require_string(payload.get("password"))
    if password is not None and (password == "" or len(password) > PASSWORD_MAX_LENGTH):
        password = None
    return email, password


def validate_new_password(payload: Mapping[str, Any], *, email: str | None, name: str | None) -> str:
    errors: dict[str, str] = {}
    password = payload.get("password")
    problem = password_problem(password, email=email, name=name)
    if problem:
        errors["password"] = problem
    elif payload.get("confirm_password") != password:
        errors["confirm_password"] = "The two passwords do not match."
    if errors:
        raise ValidationError("Please correct the highlighted fields.", details=errors)
    return password


# --------------------------------------------------------------------------
# Weather, identifiers, pagination
# --------------------------------------------------------------------------
def validate_city(raw: Any) -> str:
    text = _require_string(raw)
    city = " ".join(unicodedata.normalize("NFC", text).split()) if text else ""
    if not city:
        raise ValidationError("Enter a city or town name.", details={"city": "Enter a city or town name."})
    if len(city) < CITY_MIN_LENGTH or len(city) > CITY_MAX_LENGTH:
        message = f"Location must be between {CITY_MIN_LENGTH} and {CITY_MAX_LENGTH} characters."
        raise ValidationError(message, details={"city": message})
    if not _is_text_of(city, _CITY_PUNCTUATION):
        message = "Location can contain letters, spaces, commas, hyphens and apostrophes only."
        raise ValidationError(message, details={"city": message})
    return city


def parse_object_id(raw: Any) -> ObjectId:
    """Parse a record identifier; unknown shapes are reported as not found."""
    if not isinstance(raw, str) or len(raw) != 24:
        raise NotFoundError("That analysis could not be found.")
    try:
        return ObjectId(raw)
    except (InvalidId, TypeError) as exc:
        raise NotFoundError("That analysis could not be found.") from exc


def _parse_positive_int(raw: Any, default: int) -> int | None:
    if raw is None or raw == "":
        return default
    if not isinstance(raw, str) or not re.fullmatch(r"\d{1,7}", raw, re.ASCII):
        return None
    return int(raw)


def validate_pagination(args: Mapping[str, Any]) -> tuple[int, int]:
    """Validate ``page`` and ``per_page`` query parameters strictly (for the API)."""
    errors: dict[str, str] = {}
    page = _parse_positive_int(args.get("page"), 1)
    per_page = _parse_positive_int(args.get("per_page"), DEFAULT_PAGE_SIZE)
    if page is None or page < 1 or page > MAX_PAGE_NUMBER:
        errors["page"] = "page must be a whole number of 1 or more."
    if per_page is None or per_page < 1 or per_page > MAX_PAGE_SIZE:
        errors["per_page"] = f"per_page must be a whole number between 1 and {MAX_PAGE_SIZE}."
    if errors:
        raise ValidationError("The paging parameters are not valid.", details=errors)
    return page, per_page
