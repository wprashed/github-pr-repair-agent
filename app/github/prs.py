"""Pull request discovery, details, and metadata operations."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.github.client import GitHubClient


@dataclass
class PullRequestDetail:
    number: int
    title: str
    body: Optional[str]
    author: str
    state: str
    head_repo: str
    head_branch: str
    head_sha: str
    base_branch: str
    mergeable: Optional[bool]
    draft: bool
    html_url: str
    created_at: str
    updated_at: str
    labels: List[str] = field(default_factory=list)


@dataclass
class ChangedFile:
    filename: str
    status: str  # added, modified, removed, renamed
    additions: int
    deletions: int
    changes: int
    patch: Optional[str] = None


class PRService:
    """Service to interact with GitHub Pull Requests."""

    def __init__(self, client: GitHubClient):
        self.client = client

    async def list_open_prs(
        self,
        repo: str,
        author: Optional[str] = None,
    ) -> List[PullRequestDetail]:
        """Fetch open pull requests for a repository, optionally filtered by author."""
        params = {"state": "open", "sort": "updated", "direction": "desc", "per_page": 50}
        prs_data = await self.client.get(f"/repos/{repo}/pulls", params=params)

        results = []
        for item in prs_data:
            pr_author = item.get("user", {}).get("login", "")
            if author and pr_author.lower() != author.lower():
                continue

            results.append(
                PullRequestDetail(
                    number=item["number"],
                    title=item.get("title", ""),
                    body=item.get("body"),
                    author=pr_author,
                    state=item.get("state", "open"),
                    head_repo=item.get("head", {}).get("repo", {}).get("full_name", repo),
                    head_branch=item.get("head", {}).get("ref", ""),
                    head_sha=item.get("head", {}).get("sha", ""),
                    base_branch=item.get("base", {}).get("ref", ""),
                    mergeable=item.get("mergeable"),
                    draft=item.get("draft", False),
                    html_url=item.get("html_url", ""),
                    created_at=item.get("created_at", ""),
                    updated_at=item.get("updated_at", ""),
                    labels=[lbl.get("name") for lbl in item.get("labels", []) if "name" in lbl],
                )
            )
        return results

    async def get_pr(self, repo: str, pr_number: int) -> PullRequestDetail:
        """Get full details for a single PR."""
        item = await self.client.get(f"/repos/{repo}/pulls/{pr_number}")
        return PullRequestDetail(
            number=item["number"],
            title=item.get("title", ""),
            body=item.get("body"),
            author=item.get("user", {}).get("login", ""),
            state=item.get("state", "open"),
            head_repo=item.get("head", {}).get("repo", {}).get("full_name", repo),
            head_branch=item.get("head", {}).get("ref", ""),
            head_sha=item.get("head", {}).get("sha", ""),
            base_branch=item.get("base", {}).get("ref", ""),
            mergeable=item.get("mergeable"),
            draft=item.get("draft", False),
            html_url=item.get("html_url", ""),
            created_at=item.get("created_at", ""),
            updated_at=item.get("updated_at", ""),
            labels=[lbl.get("name") for lbl in item.get("labels", []) if "name" in lbl],
        )

    async def get_changed_files(self, repo: str, pr_number: int) -> List[ChangedFile]:
        """Fetch list of changed files in the PR."""
        files_data = await self.client.get(f"/repos/{repo}/pulls/{pr_number}/files")
        return [
            ChangedFile(
                filename=f["filename"],
                status=f["status"],
                additions=f["additions"],
                deletions=f["deletions"],
                changes=f["changes"],
                patch=f.get("patch"),
            )
            for f in files_data
        ]

    async def get_diff(self, repo: str, pr_number: int) -> str:
        """Fetch unified diff for the PR."""
        return await self.client.get_raw_text(f"/repos/{repo}/pulls/{pr_number}")
