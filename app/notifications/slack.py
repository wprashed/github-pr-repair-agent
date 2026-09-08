"""Slack notification provider via Incoming Webhooks."""

import logging
from typing import Optional
import httpx

from app.config.settings import settings
from app.notifications.base import NotificationMessage, NotificationProvider

logger = logging.getLogger(__name__)


class SlackNotificationProvider(NotificationProvider):
    """Sends notifications to Slack via Incoming Webhook URL."""

    def __init__(self, webhook_url: Optional[str] = None):
        self.webhook_url = webhook_url or settings.SLACK_WEBHOOK_URL

    async def send(self, message: NotificationMessage) -> bool:
        if not self.webhook_url:
            return False

        payload = {
            "text": f"*{message.event_type}*: {message.title} ({message.repository} #{message.pr_number})\n{message.body}",
            "blocks": [
                {
                    "type": "header",
                    "text": {"type": "plain_text", "text": f"PR Agent: {message.event_type}"},
                },
                {
                    "type": "section",
                    "fields": [
                        {"type": "mrkdwn", "text": f"*Repository:*\n`{message.repository}`"},
                        {"type": "mrkdwn", "text": f"*PR Number:*\n`#{message.pr_number}`"},
                    ],
                },
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": message.body},
                },
            ],
        }

        if message.details_url:
            payload["blocks"].append(
                {
                    "type": "context",
                    "elements": [{"type": "mrkdwn", "text": f"<{message.details_url}|View Pull Request>"}],
                }
            )

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(self.webhook_url, json=payload)
                return resp.status_code == 200
        except Exception as exc:
            logger.warning("Failed to send Slack notification: %s", exc)
            return False
