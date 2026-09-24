"""Outbound email.

One function, `send_email`. In development it talks to Mailpit
(`docker-compose.yml`), which never leaves the machine; in production the same
SMTP settings point at a real relay. There is deliberately no templating
engine here yet — Phase 1's emails are short and few, and Jinja2 (already a
dependency, for future PDF/report templates) is the natural place to add
templates when there are enough of them to justify one.
"""

from __future__ import annotations

import smtplib
from email.message import EmailMessage

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger("mailer")


def send_email(*, to: str, subject: str, body: str) -> None:
    """Send a plain-text email synchronously.

    Called from a Celery task (never from a request handler), so the network
    round-trip to the SMTP relay never blocks an API response.
    """
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = f"{settings.mail_from_name} <{settings.mail_from}>"
    message["To"] = to
    message.set_content(body)

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as client:
        if settings.smtp_tls:
            client.starttls()
        if settings.smtp_user:
            client.login(settings.smtp_user, settings.smtp_password)
        client.send_message(message)

    log.info("mailer.sent", to=to, subject=subject)
