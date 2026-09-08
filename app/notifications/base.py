"""Notification provider interface and event types."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


class NotificationEvent:
    REPAIR_STARTED = "REPAIR_STARTED"
    REPAIR_SUCCEEDED = "REPAIR_SUCCEEDED"
    REPAIR_FAILED = "REPAIR_FAILED"
    HUMAN_APPROVAL_REQUIRED = "HUMAN_APPROVAL_REQUIRED"
    MAX_ATTEMPTS_REACHED = "MAX_ATTEMPTS_REACHED"
    UNEXPECTED_FILES_CHANGED = "UNEXPECTED_FILES_CHANGED"
    SECURITY_ISSUE_DETECTED = "SECURITY_ISSUE_DETECTED"


@dataclass
class NotificationMessage:
    event_type: str
    repository: str
    pr_number: int
    title: str
    body: str
    details_url: Optional[str] = None


class NotificationProvider(ABC):
    """Abstract interface for notification services."""

    @abstractmethod
    async def send(self, message: NotificationMessage) -> bool:
        """Send notification. Returns True if sent successfully."""
        raise NotImplementedError
