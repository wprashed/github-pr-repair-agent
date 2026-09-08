"""Console notification provider using Rich."""

import logging
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from app.notifications.base import NotificationMessage, NotificationProvider, NotificationEvent

logger = logging.getLogger(__name__)
console = Console()


class ConsoleNotificationProvider(NotificationProvider):
    """Prints styled notification cards to the terminal using Rich."""

    async def send(self, message: NotificationMessage) -> bool:
        color_map = {
            NotificationEvent.REPAIR_STARTED: "blue",
            NotificationEvent.REPAIR_SUCCEEDED: "green",
            NotificationEvent.REPAIR_FAILED: "red",
            NotificationEvent.HUMAN_APPROVAL_REQUIRED: "yellow",
            NotificationEvent.MAX_ATTEMPTS_REACHED: "bold red",
            NotificationEvent.UNEXPECTED_FILES_CHANGED: "magenta",
            NotificationEvent.SECURITY_ISSUE_DETECTED: "bold red on white",
        }
        color = color_map.get(message.event_type, "white")

        panel_content = Text()
        panel_content.append(f"PR: {message.repository} #{message.pr_number}\n", style="bold")
        panel_content.append(f"{message.body}\n")
        if message.details_url:
            panel_content.append(f"Details: {message.details_url}", style="underline")

        console.print(
            Panel(
                panel_content,
                title=f"[{color}]{message.event_type}: {message.title}[/]",
                border_style=color,
            )
        )
        return True
