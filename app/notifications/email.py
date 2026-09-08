"""Email notification provider via standard SMTP."""

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

from app.config.settings import settings
from app.notifications.base import NotificationMessage, NotificationProvider

logger = logging.getLogger(__name__)


class EmailNotificationProvider(NotificationProvider):
    """Sends notifications via SMTP email."""

    def __init__(
        self,
        host: Optional[str] = None,
        port: Optional[int] = None,
        user: Optional[str] = None,
        password: Optional[str] = None,
        recipient: Optional[str] = None,
    ):
        self.host = host or settings.EMAIL_SMTP_HOST
        self.port = port or settings.EMAIL_SMTP_PORT
        self.user = user or settings.EMAIL_SMTP_USER
        self.password = password or settings.EMAIL_SMTP_PASSWORD
        self.recipient = recipient or settings.EMAIL_TO

    async def send(self, message: NotificationMessage) -> bool:
        if not (self.host and self.user and self.recipient):
            return False

        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = f"[PR Repair Agent] {message.event_type}: {message.title} ({message.repository} #{message.pr_number})"
            msg["From"] = self.user
            msg["To"] = self.recipient

            text_body = f"{message.event_type}\nPR: {message.repository} #{message.pr_number}\n\n{message.body}"
            if message.details_url:
                text_body += f"\n\nLink: {message.details_url}"

            msg.attach(MIMEText(text_body, "plain"))

            # Send synchronously in executor to avoid blocking async loop
            import asyncio
            loop = asyncio.get_event_loop()

            def _send():
                with smtplib.SMTP(self.host, self.port) as server:
                    server.starttls()
                    if self.password:
                        server.login(self.user, self.password)
                    server.send_message(msg)

            await loop.run_in_executor(None, _send)
            return True
        except Exception as exc:
            logger.warning("Failed to send email notification: %s", exc)
            return False
