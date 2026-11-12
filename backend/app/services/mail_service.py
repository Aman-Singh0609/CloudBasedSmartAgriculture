"""Outgoing email, used only for password-reset links.

Backends (``MAIL_BACKEND``):

``smtp``      Sends through a real SMTP server.
``console``   Development only. Writes the message to the application log
              instead of sending it. Rejected by the production configuration
              because reset links are credentials.
``memory``    Tests only. Keeps messages in a list.
``disabled``  No delivery is possible. The password-reset pages return 404, so
              the application never pretends an email was sent.
"""

from __future__ import annotations

import logging
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage

logger = logging.getLogger(__name__)


class MailDeliveryError(RuntimeError):
    """The message could not be handed to the mail server."""


@dataclass(frozen=True)
class OutgoingEmail:
    to: str
    subject: str
    body: str


class Mailer:
    """Base mailer: delivery is not available."""

    can_send = False

    def send(self, message: OutgoingEmail) -> None:
        raise MailDeliveryError("mail delivery is disabled")


class DisabledMailer(Mailer):
    pass


class ConsoleMailer(Mailer):
    """Development helper: logs the message instead of sending it."""

    can_send = True

    def send(self, message: OutgoingEmail) -> None:
        logger.warning(
            "DEVELOPMENT MAIL (not sent) to=%s subject=%s\n%s",
            message.to,
            message.subject,
            message.body,
            extra={"event": "dev_mail"},
        )


class MemoryMailer(Mailer):
    """Test helper: records messages for assertions."""

    can_send = True

    def __init__(self) -> None:
        self.outbox: list[OutgoingEmail] = []

    def send(self, message: OutgoingEmail) -> None:
        self.outbox.append(message)


class SmtpMailer(Mailer):
    can_send = True

    def __init__(
        self,
        *,
        host: str,
        port: int,
        username: str,
        password: str,
        use_tls: bool,
        sender: str,
        timeout: float = 10.0,
    ) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._use_tls = use_tls
        self._sender = sender
        self._timeout = timeout

    def send(self, message: OutgoingEmail) -> None:
        email = EmailMessage()
        email["From"] = self._sender
        email["To"] = message.to
        email["Subject"] = message.subject
        email.set_content(message.body)
        try:
            with smtplib.SMTP(self._host, self._port, timeout=self._timeout) as server:
                if self._use_tls:
                    server.starttls(context=ssl.create_default_context())
                if self._username:
                    server.login(self._username, self._password)
                server.send_message(email)
        except (smtplib.SMTPException, OSError) as exc:
            # The exception text may echo server responses; log the type only.
            logger.error(
                "mail delivery failed",
                extra={"event": "mail_delivery_failed", "error_type": type(exc).__name__},
            )
            raise MailDeliveryError("could not deliver email") from exc


def build_mailer(config: dict) -> Mailer:
    backend = config.get("MAIL_BACKEND", "disabled")
    if backend == "smtp":
        return SmtpMailer(
            host=config["SMTP_HOST"],
            port=config["SMTP_PORT"],
            username=config.get("SMTP_USERNAME", ""),
            password=config.get("SMTP_PASSWORD", ""),
            use_tls=config.get("SMTP_USE_TLS", True),
            sender=config["MAIL_FROM"],
        )
    if backend == "console":
        return ConsoleMailer()
    if backend == "memory":
        return MemoryMailer()
    return DisabledMailer()
