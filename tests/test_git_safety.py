"""Tests for Git safety controls and push guardrails."""

import asyncio
import subprocess
from pathlib import Path
import pytest

from app.workspace.git import SafeGitService, GitSafetyViolationError


@pytest.mark.asyncio
async def test_git_push_safety_rejects_protected_branches(temp_dir):
    # Initialize a dummy git repo on main
    subprocess.run(["git", "init", "-b", "main", str(temp_dir)], check=True, capture_output=True)
    subprocess.run(["git", "remote", "add", "origin", "https://github.com/owner/repo.git"], cwd=temp_dir, check=True)

    # Attempting to verify push to 'main' must raise GitSafetyViolationError
    with pytest.raises(GitSafetyViolationError, match="Cannot push to protected branch 'main'"):
        await SafeGitService.verify_push_safety(
            repo_dir=temp_dir,
            expected_repo="owner/repo",
            expected_branch="main",
            allow_force=False,
        )


@pytest.mark.asyncio
async def test_git_push_safety_rejects_force_push(temp_dir):
    subprocess.run(["git", "init", "-b", "feature/my-fix", str(temp_dir)], check=True, capture_output=True)
    subprocess.run(["git", "remote", "add", "origin", "https://github.com/owner/repo.git"], cwd=temp_dir, check=True)

    with pytest.raises(GitSafetyViolationError, match="Force push is strictly prohibited"):
        await SafeGitService.verify_push_safety(
            repo_dir=temp_dir,
            expected_repo="owner/repo",
            expected_branch="feature/my-fix",
            allow_force=True,
        )


@pytest.mark.asyncio
async def test_git_push_safety_rejects_mismatched_branch(temp_dir):
    subprocess.run(["git", "init", "-b", "feature/branch-a", str(temp_dir)], check=True, capture_output=True)
    subprocess.run(["git", "remote", "add", "origin", "https://github.com/owner/repo.git"], cwd=temp_dir, check=True)

    with pytest.raises(GitSafetyViolationError, match="does not match expected PR branch"):
        await SafeGitService.verify_push_safety(
            repo_dir=temp_dir,
            expected_repo="owner/repo",
            expected_branch="feature/branch-b",
            allow_force=False,
        )


@pytest.mark.asyncio
async def test_git_push_safety_rejects_mismatched_repo(temp_dir):
    subprocess.run(["git", "init", "-b", "feature/my-fix", str(temp_dir)], check=True, capture_output=True)
    subprocess.run(["git", "remote", "add", "origin", "https://github.com/wrong-owner/wrong-repo.git"], cwd=temp_dir, check=True)

    with pytest.raises(GitSafetyViolationError, match="does not match expected repository"):
        await SafeGitService.verify_push_safety(
            repo_dir=temp_dir,
            expected_repo="owner/expected-repo",
            expected_branch="feature/my-fix",
            allow_force=False,
        )


@pytest.mark.asyncio
async def test_git_push_safety_passes_valid_branch_and_repo(temp_dir):
    subprocess.run(["git", "init", "-b", "feature/my-fix", str(temp_dir)], check=True, capture_output=True)
    subprocess.run(["git", "remote", "add", "origin", "https://github.com/owner/repo.git"], cwd=temp_dir, check=True)

    # Should not raise exception
    await SafeGitService.verify_push_safety(
        repo_dir=temp_dir,
        expected_repo="owner/repo",
        expected_branch="feature/my-fix",
        allow_force=False,
    )
