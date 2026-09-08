"""Central notification dispatcher."""

import logging
from typing import List

from app.config.settings import settings
from app.database.database import get_async_db
from app.database.models import NotificationLog
from app.notifications.base import NotificationEvent, NotificationMessage, NotificationProvider
from app.notifications.console import ConsoleNotificationProvider
from app.notifications.email import EmailNotificationProvider
from app.notifications.slack import SlackNotificationProvider

logger = logging.getLogger(__name__)


class NotificationManager:
    """Dispatches notifications across all configured channels and persists logs."""

    def __init__(self):
        self.providers: List[NotificationProvider] = []
        if settings.NOTIFICATIONS_CONSOLE:
            self.providers.append(ConsoleNotificationProvider())
        if settings.SLACK_WEBHOOK_URL:
            self.providers.append(SlackNotificationProvider())
        if settings.EMAIL_SMTP_HOST and settings.EMAIL_TO:
            self.providers.append(EmailNotificationProvider())

    async def notify(self, message: NotificationMessage) -> None:
        """Broadcast message to all providers and record in database."""
        if not settings.NOTIFICATIONS_ENABLED:
            return

        for provider in self.providers:
            try:
                channel_name = provider.__class__.__name__.replace("NotificationProvider", "").lower()
                success = await provider.send(message)
                # Record to database
                async with get_async_db() as session:
                    log_entry = NotificationLog(
                        channel=channel_name,
                        event_type=message.event_type,
                        title=message.title,
                        content=message.body,
                        status="sent" if success else "failed",
                    )
                    session.add(log_entry)
            except Exception as exc:
                logger.warning("Error dispatching notification via %s: %s", provider, exc)


notification_manager = NotificationManager()
