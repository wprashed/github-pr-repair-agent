"""Workspace isolation and lifecycle manager."""

import asyncio
import logging
import shutil
from pathlib import Path
from typing import Optional

from app.config.settings import settings
from app.workspace.git import SafeGitService

logger = logging.getLogger(__name__)


class WorkspaceManager:
    """Manages isolated workspace directories for each PR repair session."""

    def __init__(self, base_dir: Optional[Path] = None):
        self.base_dir = base_dir or settings.WORKSPACE_DIR
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def get_workspace_path(self, repo_full_name: str, pr_number: int) -> Path:
        """Derive the deterministic workspace path for a PR."""
        sanitized_repo = repo_full_name.replace("/", "-")
        return self.base_dir / sanitized_repo / f"pr-{pr_number}"

    async def prepare_workspace(
        self,
        repo_full_name: str,
        pr_number: int,
        head_branch: str,
        clone_url: str,
        auth_token: Optional[str] = None,
    ) -> Path:
        """Set up an isolated workspace by cloning the PR branch."""
        workspace_path = self.get_workspace_path(repo_full_name, pr_number)

        # If existing workspace exists, clean or reset it
        if workspace_path.exists():
            try:
                # Check if it's a valid git repo on the right branch
                current_branch = await SafeGitService.get_current_branch(workspace_path)
                if current_branch == head_branch:
                    # Clean unstaged changes and pull latest
                    await SafeGitService.run_git_cmd(workspace_path, ["reset", "--hard", "HEAD"])
                    await SafeGitService.run_git_cmd(workspace_path, ["clean", "-fd"])
                    return workspace_path
            except Exception:
                # Corrupted or outdated: remove and re-clone
                shutil.rmtree(workspace_path, ignore_errors=True)

        workspace_path.parent.mkdir(parents=True, exist_ok=True)

        # Authenticated clone URL if token provided
        final_url = clone_url
        if auth_token and "github.com" in clone_url and "@" not in clone_url:
            final_url = clone_url.replace("https://", f"https://x-access-token:{auth_token}@")

        logger.info("Cloning %s (branch: %s) into %s", repo_full_name, head_branch, workspace_path)

        cmd = [
            "git",
            "clone",
            "--branch",
            head_branch,
            "--single-branch",
            final_url,
            str(workspace_path),
        ]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"Failed to clone repository: {stderr.decode('utf-8', errors='replace')}")

        return workspace_path

    def cleanup_workspace(self, repo_full_name: str, pr_number: int) -> None:
        """Remove a specific PR workspace."""
        path = self.get_workspace_path(repo_full_name, pr_number)
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)
            logger.info("Cleaned up workspace at %s", path)
