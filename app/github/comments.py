"""Pull request comments and review comments handling."""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from app.github.client import GitHubClient


@dataclass
class ReviewComment:
    id: int
    user: str
    body: str
    path: str
    position: Optional[int]
    line: Optional[int]
    commit_id: str
    created_at: str
    updated_at: str
    html_url: str
    diff_hunk: Optional[str] = None


@dataclass
class IssueComment:
    id: int
    user: str
    body: str
    created_at: str
    updated_at: str
    html_url: str


class CommentService:
    """Service to fetch PR review comments and conversation comments."""

    def __init__(self, client: GitHubClient):
        self.client = client

    async def get_review_comments(self, repo: str, pr_number: int) -> List[ReviewComment]:
        """Fetch inline code review comments for a PR."""
        data = await self.client.get(f"/repos/{repo}/pulls/{pr_number}/comments")
        comments = []
        for c in data:
            comments.append(
                ReviewComment(
                    id=c["id"],
                    user=c.get("user", {}).get("login", ""),
                    body=c.get("body", ""),
                    path=c.get("path", ""),
                    position=c.get("position"),
                    line=c.get("line") or c.get("original_line"),
                    commit_id=c.get("commit_id", ""),
                    created_at=c.get("created_at", ""),
                    updated_at=c.get("updated_at", ""),
                    html_url=c.get("html_url", ""),
                    diff_hunk=c.get("diff_hunk"),
                )
            )
        return comments

    async def get_issue_comments(self, repo: str, pr_number: int) -> List[IssueComment]:
        """Fetch main issue/conversation comments on the PR."""
        data = await self.client.get(f"/repos/{repo}/issues/{pr_number}/comments")
        return [
            IssueComment(
                id=c["id"],
                user=c.get("user", {}).get("login", ""),
                body=c.get("body", ""),
                created_at=c.get("created_at", ""),
                updated_at=c.get("updated_at", ""),
                html_url=c.get("html_url", ""),
            )
            for c in data
        ]
