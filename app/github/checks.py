"""Check runs and commit statuses detection."""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from app.github.client import GitHubClient


@dataclass
class CheckRunItem:
    id: int
    name: str
    head_sha: str
    status: str  # queued, in_progress, completed
    conclusion: Optional[str]  # success, failure, neutral, cancelled, timed_out, action_required
    html_url: str
    title: Optional[str] = None
    summary: Optional[str] = None
    text: Optional[str] = None


class CheckService:
    """Service to inspect GitHub Checks and Statuses."""

    def __init__(self, client: GitHubClient):
        self.client = client

    async def get_check_runs_for_ref(self, repo: str, ref: str) -> List[CheckRunItem]:
        """Fetch check runs for a specific commit SHA or branch name."""
        data = await self.client.get(f"/repos/{repo}/commits/{ref}/check-runs")
        check_runs = data.get("check_runs", [])
        results = []
        for cr in check_runs:
            output = cr.get("output") or {}
            results.append(
                CheckRunItem(
                    id=cr["id"],
                    name=cr.get("name", ""),
                    head_sha=cr.get("head_sha", ref),
                    status=cr.get("status", "completed"),
                    conclusion=cr.get("conclusion"),
                    html_url=cr.get("html_url", ""),
                    title=output.get("title"),
                    summary=output.get("summary"),
                    text=output.get("text"),
                )
            )
        return results

    async def get_failed_check_runs(self, repo: str, ref: str) -> List[CheckRunItem]:
        """Return only failed check runs."""
        runs = await self.get_check_runs_for_ref(repo, ref)
        return [
            r for r in runs
            if r.conclusion in ("failure", "timed_out", "action_required")
        ]
