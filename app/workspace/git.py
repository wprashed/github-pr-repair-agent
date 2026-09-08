"""Safe Git operations with strict safety guardrails."""

import asyncio
import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Protected branch names that must NEVER be modified or pushed directly
PROTECTED_BRANCHES = {
    "main",
    "master",
    "develop",
    "development",
    "release",
    "staging",
    "production",
}


class GitSafetyViolationError(Exception):
    """Raised when a git operation violates safety rules."""
    pass


class SafeGitService:
    """Provides safe git operations with enforced push guardrails."""

    @classmethod
    async def run_git_cmd(
        cls,
        repo_dir: Path,
        args: List[str],
        check: bool = True,
        env: Optional[Dict[str, str]] = None,
    ) -> Tuple[int, str, str]:
        """Execute a git command safely as a subprocess."""
        cmd_env = os.environ.copy()
        if env:
            cmd_env.update(env)

        # Prevent interactive prompts
        cmd_env["GIT_TERMINAL_PROMPT"] = "0"
        cmd_env["GIT_ASKPASS"] = "echo"

        proc = await asyncio.create_subprocess_exec(
            "git",
            *args,
            cwd=str(repo_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=cmd_env,
        )

        stdout_bytes, stderr_bytes = await proc.communicate()
        stdout = stdout_bytes.decode("utf-8", errors="replace").strip()
        stderr = stderr_bytes.decode("utf-8", errors="replace").strip()

        if check and proc.returncode != 0:
            raise RuntimeError(f"Git command 'git {' '.join(args)}' failed ({proc.returncode}): {stderr}")

        return proc.returncode, stdout, stderr

    @classmethod
    async def get_current_branch(cls, repo_dir: Path) -> str:
        """Get the current active branch name."""
        code, stdout, _ = await cls.run_git_cmd(repo_dir, ["branch", "--show-current"], check=False)
        if stdout.strip():
            return stdout.strip()
        code, stdout, _ = await cls.run_git_cmd(repo_dir, ["rev-parse", "--abbrev-ref", "HEAD"], check=False)
        return stdout.strip()

    @classmethod
    async def get_head_sha(cls, repo_dir: Path) -> str:
        """Get the current HEAD commit SHA."""
        _, stdout, _ = await cls.run_git_cmd(repo_dir, ["rev-parse", "HEAD"])
        return stdout

    @classmethod
    async def get_remote_url(cls, repo_dir: Path, remote: str = "origin") -> str:
        """Get the remote repository URL."""
        _, stdout, _ = await cls.run_git_cmd(repo_dir, ["remote", "get-url", remote])
        return stdout

    @classmethod
    async def has_changes(cls, repo_dir: Path) -> bool:
        """Check if there are unstaged or staged changes."""
        code, stdout, _ = await cls.run_git_cmd(repo_dir, ["status", "--porcelain"])
        return bool(stdout.strip())

    @classmethod
    async def stage_and_commit(
        cls,
        repo_dir: Path,
        message: str,
        author_name: str = "PR Repair Agent",
        author_email: str = "pr-repair-agent@local",
    ) -> Optional[str]:
        """Stage safe changes and create a commit."""
        if not await cls.has_changes(repo_dir):
            logger.info("No modifications to commit in %s", repo_dir)
            return None

        await cls.run_git_cmd(repo_dir, ["add", "-A"])

        env = {
            "GIT_AUTHOR_NAME": author_name,
            "GIT_AUTHOR_EMAIL": author_email,
            "GIT_COMMITTER_NAME": author_name,
            "GIT_COMMITTER_EMAIL": author_email,
        }
        await cls.run_git_cmd(repo_dir, ["commit", "-m", message], env=env)
        return await cls.get_head_sha(repo_dir)

    @classmethod
    async def verify_push_safety(
        cls,
        repo_dir: Path,
        expected_repo: str,
        expected_branch: str,
        allow_force: bool = False,
    ) -> None:
        """Execute strict push verification rules before executing git push.

        Rules:
        1. Current branch == PR source branch
        2. Remote repository == expected repository
        3. No protected branch is being modified
        4. No force push allowed
        """
        # Rule 4: No force push
        if allow_force:
            raise GitSafetyViolationError("Force push is strictly prohibited by policy.")

        # Rule 1: Current branch must match expected PR branch
        current_branch = await cls.get_current_branch(repo_dir)
        if current_branch != expected_branch:
            raise GitSafetyViolationError(
                f"Current branch '{current_branch}' does not match expected PR branch '{expected_branch}'"
            )

        # Rule 3: Must NOT be a protected branch
        if current_branch.lower() in PROTECTED_BRANCHES:
            raise GitSafetyViolationError(
                f"Cannot push to protected branch '{current_branch}'!"
            )

        # Rule 2: Remote URL must match expected repo
        try:
            remote_url = await cls.get_remote_url(repo_dir, "origin")
            # remote_url could be https://github.com/owner/repo.git or git@github.com:owner/repo.git
            normalized_expected = expected_repo.lower()
            if normalized_expected not in remote_url.lower():
                raise GitSafetyViolationError(
                    f"Remote origin '{remote_url}' does not match expected repository '{expected_repo}'"
                )
        except RuntimeError as e:
            raise GitSafetyViolationError(f"Failed to verify remote repository: {e}")

    @classmethod
    async def push_to_pr_branch(
        cls,
        repo_dir: Path,
        expected_repo: str,
        expected_branch: str,
        auth_token: Optional[str] = None,
    ) -> None:
        """Push commits to the PR source branch after verifying all guardrails."""
        # 1. Enforce guardrails
        await cls.verify_push_safety(repo_dir, expected_repo, expected_branch, allow_force=False)

        # 2. Configure credentials safely in push environment if token provided
        env = {}
        push_cmd = ["push", "origin", expected_branch]

        if auth_token:
            # Inject token via git credential helper or custom URL
            remote_url = await cls.get_remote_url(repo_dir, "origin")
            if "github.com" in remote_url and not remote_url.startswith("git@"):
                # Use auth header
                push_cmd = [
                    "-c",
                    f"http.https://github.com/.extraheader=AUTHORIZATION: basic {auth_token}",
                    "push",
                    "origin",
                    expected_branch,
                ]

        logger.info("Executing safe push to %s on branch %s", expected_repo, expected_branch)
        await cls.run_git_cmd(repo_dir, push_cmd)
        logger.info("Push completed successfully to %s on branch %s", expected_repo, expected_branch)
