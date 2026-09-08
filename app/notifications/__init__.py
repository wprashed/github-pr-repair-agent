"""Notification system module."""

from app.notifications.base import NotificationEvent, NotificationMessage, NotificationProvider
from app.notifications.console import ConsoleNotificationProvider
from app.notifications.slack import SlackNotificationProvider
from app.notifications.email import EmailNotificationProvider
from app.notifications.manager import notification_manager, NotificationManager

__all__ = [
    "NotificationEvent",
    "NotificationMessage",
    "NotificationProvider",
    "ConsoleNotificationProvider",
    "SlackNotificationProvider",
    "EmailNotificationProvider",
    "notification_manager",
    "NotificationManager",
]
