"""Application error types and the centralized error handlers.

Every failure leaves the application through one of the handlers registered
here. Clients receive a short, understandable message in the standard JSON
error envelope; developers receive a structured log entry that carries the
request ID. Tracebacks, driver exceptions and upstream responses are never
sent to the client.
"""

from __future__ import annotations

import logging

from flask import Flask, make_response, request
from flask_limiter.errors import RateLimitExceeded
from flask_wtf.csrf import CSRFError
from pymongo.errors import PyMongoError
from werkzeug.exceptions import HTTPException

from app.utils.responses import error_response

logger = logging.getLogger(__name__)


class AppError(Exception):
    """Base class for expected, user-facing failures."""

    status_code = 500
    code = "INTERNAL_ERROR"
    default_message = "Something went wrong on our side. Please try again."

    def __init__(self, message: str | None = None, *, details: dict | None = None) -> None:
        super().__init__(message or self.default_message)
        self.message = message or self.default_message
        self.details = details


class BadRequestError(AppError):
    status_code = 400
    code = "BAD_REQUEST"
    default_message = "The request could not be understood."


class UnsupportedMediaTypeError(AppError):
    status_code = 415
    code = "UNSUPPORTED_MEDIA_TYPE"
    default_message = "Send the request body as JSON with the header Content-Type: application/json."


class ValidationError(AppError):
    status_code = 422
    code = "INVALID_INPUT"
    default_message = "Some of the values provided are not valid."


class AuthenticationRequiredError(AppError):
    status_code = 401
    code = "AUTHENTICATION_REQUIRED"
    default_message = "Please log in to continue."


class InvalidCredentialsError(AppError):
    status_code = 401
    code = "INVALID_CREDENTIALS"
    default_message = "Invalid email or password."


class ForbiddenError(AppError):
    status_code = 403
    code = "FORBIDDEN"
    default_message = "You do not have permission to do that."


class NotFoundError(AppError):
    status_code = 404
    code = "NOT_FOUND"
    default_message = "We could not find what you were looking for."


class ConflictError(AppError):
    status_code = 409
    code = "CONFLICT"
    default_message = "That conflicts with existing data."


class ServiceUnavailableError(AppError):
    status_code = 503
    code = "SERVICE_UNAVAILABLE"
    default_message = "The service is temporarily unavailable. Please try again shortly."


class DatabaseUnavailableError(ServiceUnavailableError):
    code = "DATABASE_UNAVAILABLE"
    default_message = (
        "We cannot reach our database right now, so your data cannot be loaded or saved. "
        "Please try again in a few minutes."
    )


class ModelUnavailableError(ServiceUnavailableError):
    code = "MODEL_UNAVAILABLE"
    default_message = "The crop recommendation model is not available right now. Please try again later."


class ExternalServiceError(AppError):
    """An upstream provider (e.g. the weather API) failed."""

    status_code = 502
    code = "EXTERNAL_SERVICE_ERROR"
    default_message = "An external service did not respond correctly."


_HTTP_ERROR_CODES = {
    400: "BAD_REQUEST",
    401: "AUTHENTICATION_REQUIRED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    413: "PAYLOAD_TOO_LARGE",
    415: "UNSUPPORTED_MEDIA_TYPE",
    429: "RATE_LIMITED",
    500: "INTERNAL_ERROR",
    503: "SERVICE_UNAVAILABLE",
}

_HTTP_MESSAGES = {
    400: "The request could not be understood.",
    401: "Please log in to continue.",
    403: "You do not have permission to do that.",
    404: "The requested resource does not exist.",
    405: "That method is not supported for this address.",
    413: "The request was too large to process.",
    415: "Send the request body as JSON with the header Content-Type: application/json.",
    429: "You have made too many requests in a short time. Please wait a moment and try again.",
    500: "Something went wrong on our side. Please try again.",
    503: "The service is temporarily unavailable. Please try again shortly.",
}


def _respond(status: int, code: str, message: str, details: dict | None = None, headers: dict | None = None):
    response = make_response(error_response(code, message, status, details=details))
    for name, value in (headers or {}).items():
        response.headers[name] = value
    return response


def register_error_handlers(app: Flask) -> None:
    @app.errorhandler(AppError)
    def handle_app_error(exc: AppError):
        if exc.status_code >= 500:
            logger.warning(
                "request failed: %s",
                exc.code,
                extra={"event": "app_error", "error_code": exc.code, "status": exc.status_code},
            )
        return _respond(exc.status_code, exc.code, exc.message, exc.details)

    @app.errorhandler(CSRFError)
    def handle_csrf_error(exc: CSRFError):
        logger.warning("csrf validation failed", extra={"event": "csrf_failed", "path": request.path})
        return _respond(
            400,
            "CSRF_VALIDATION_FAILED",
            "Your session security token is missing or has expired. Please reload the page and try again.",
        )

    @app.errorhandler(RateLimitExceeded)
    def handle_rate_limit(exc: RateLimitExceeded):
        logger.warning("rate limit exceeded", extra={"event": "rate_limited", "path": request.path})
        # Flask-Limiter adds Retry-After and the X-RateLimit-* headers itself.
        return _respond(429, "RATE_LIMITED", _HTTP_MESSAGES[429])

    @app.errorhandler(PyMongoError)
    def handle_database_error(exc: PyMongoError):
        # The driver message can contain host names; log only the class name.
        database = app.extensions.get("database")
        if database is not None:
            database.mark_failure()
        logger.error(
            "database operation failed",
            extra={"event": "database_error", "error_type": type(exc).__name__, "path": request.path},
        )
        return _respond(503, DatabaseUnavailableError.code, DatabaseUnavailableError.default_message)

    @app.errorhandler(HTTPException)
    def handle_http_exception(exc: HTTPException):
        status = exc.code or 500
        code = _HTTP_ERROR_CODES.get(status, "HTTP_ERROR")
        message = _HTTP_MESSAGES.get(status, "The request could not be completed.")
        headers = {}
        if status == 405 and getattr(exc, "valid_methods", None):
            headers["Allow"] = ", ".join(sorted(exc.valid_methods))
        return _respond(status, code, message, headers=headers)

    @app.errorhandler(Exception)
    def handle_unexpected_error(exc: Exception):
        logger.exception(
            "unhandled exception",
            extra={"event": "unhandled_exception", "error_type": type(exc).__name__, "path": request.path},
        )
        audit = app.extensions.get("audit")
        if audit is not None:
            audit.record(
                "server.unhandled_exception",
                outcome="failure",
                details={"error_type": type(exc).__name__, "endpoint": request.endpoint},
            )
        return _respond(500, "INTERNAL_ERROR", _HTTP_MESSAGES[500])
