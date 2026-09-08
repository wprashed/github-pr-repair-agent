"""GitHub Review detection and tracking."""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from app.github.client import GitHubClient


@dataclass
class ReviewItem:
    id: int
    user: str
    state: str  # APPROVED, CHANGES_REQUESTED, COMMENTED, DISMISSED, PENDING
    body: Optional[str]
    submitted_at: str
    commit_id: str
    html_url: str


class ReviewService:
    """Service to fetch and inspect PR reviews."""

    def __init__(self, client: GitHubClient):
        self.client = client

    async def get_reviews(self, repo: str, pr_number: int) -> List[ReviewItem]:
        """Fetch all reviews for a PR."""
        data = await self.client.get(f"/repos/{repo}/pulls/{pr_number}/reviews")
        reviews = []
        for r in data:
            reviews.append(
                ReviewItem(
                    id=r["id"],
                    user=r.get("user", {}).get("login", ""),
                    state=r.get("state", "COMMENTED"),
                    body=r.get("body"),
                    submitted_at=r.get("submitted_at", ""),
                    commit_id=r.get("commit_id", ""),
                    html_url=r.get("html_url", ""),
                )
            )
        return reviews

    async def get_requested_changes(self, repo: str, pr_number: int) -> List[ReviewItem]:
        """Filter reviews to only those requesting changes."""
        all_reviews = await self.get_reviews(repo, pr_number)
        return [r for r in all_reviews if r.state == "CHANGES_REQUESTED"]
