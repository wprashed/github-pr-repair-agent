"""End-to-end acceptance test demonstrating the full repair lifecycle and failure scenarios."""

import asyncio
import os
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, patch
import pytest

from app.agent.base import AgentContext, RepairResult
from app.agent.mock import MockCodingAgent
from app.config.repositories import ChecksConfig, RepositoryConfig, RepoSafetyConfig
from app.database.database import get_async_db, init_db
from app.database.models import PRStatus, PullRequest, RepairAttempt, Event
from sqlalchemy import select
from app.github.actions import FailedJobInfo
from app.github.checks import CheckRunItem
from app.github.comments import ReviewComment
from app.github.prs import ChangedFile, PullRequestDetail
from app.github.reviews import ReviewItem
from app.monitoring.scanner import PRRepairScanner
from app.workspace.git import SafeGitService


class SimulatingAgent(MockCodingAgent):
    """Agent that actually fixes the code in the workspace during the test."""

    async def repair(self, context: AgentContext) -> RepairResult:
        workspace = Path(context.workspace_path)
        calc_file = workspace / "calc.py"
        if calc_file.exists():
            # Fix the bug
            calc_file.write_text("def add(a, b):\n    return a + b\n")

        test_file = workspace / "test_calc.py"
        if test_file.exists():
            test_file.write_text("from calc import add\ndef test_add():\n    assert add(2, 3) == 5\n")

        return RepairResult(
            status="success",
            summary="Fixed addition operator in calc.py and verified tests",
            files_changed=["calc.py", "test_calc.py"],
            tests_run=context.run_checks_commands,
            tests_passed=True,
        )


@pytest.mark.asyncio
async def test_full_successful_repair_workflow(temp_dir):
    """Simulate:

    PR created with test failure -> Reviewer requests changes -> Scanner detects ->
    Agent fixes code -> Tests pass -> Diff safety passes -> Commit and push simulated.
    """
    # 1. Setup local 'remote' git repository
    remote_dir = temp_dir / "owner" / "math-lib.git"
    remote_dir.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "--bare", str(remote_dir)], check=True, capture_output=True)

    # 2. Setup local work branch simulating the PR branch
    local_dev = temp_dir / "local_dev"
    subprocess.run(["git", "clone", str(remote_dir), str(local_dev)], check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Developer"], cwd=local_dev, check=True)
    subprocess.run(["git", "config", "user.email", "dev@example.com"], cwd=local_dev, check=True)

    # Initial commit on main
    subprocess.run(["git", "checkout", "-b", "main"], cwd=local_dev, check=True)
    (local_dev / "README.md").write_text("# Project")
    subprocess.run(["git", "add", "."], cwd=local_dev, check=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=local_dev, check=True)
    subprocess.run(["git", "push", "origin", "main"], cwd=local_dev, check=True)

    # Create PR branch with intentional bug
    pr_branch = "fix/math-engine"
    subprocess.run(["git", "checkout", "-b", pr_branch], cwd=local_dev, check=True)
    (local_dev / "calc.py").write_text("def add(a, b):\n    return a - b  # BUG!\n")
    (local_dev / "test_calc.py").write_text("from calc import add\ndef test_add():\n    assert add(2, 3) == 5\n")
    subprocess.run(["git", "add", "."], cwd=local_dev, check=True)
    subprocess.run(["git", "commit", "-m", "Add calc feature with bug"], cwd=local_dev, check=True)
    subprocess.run(["git", "push", "origin", pr_branch], cwd=local_dev, check=True)

    head_sha = subprocess.getoutput(f"cd {local_dev} && git rev-parse HEAD").strip()

    # 3. Setup repo config with checks
    repo_cfg = RepositoryConfig(
        name="math-lib",
        github="owner/math-lib",
        enabled=True,
        checks=ChecksConfig(
            test=["pytest test_calc.py"]
        ),
        safety=RepoSafetyConfig(
            max_files_changed=5,
            max_lines_added=100,
            max_lines_deleted=100,
        ),
    )

    # 4. Mock GitHub client and PR details
    mock_gh = AsyncMock()
    pr_detail = PullRequestDetail(
        number=1,
        title="Add math calculations",
        body="Implements addition module.",
        author="contributor",
        state="open",
        head_repo="owner/math-lib",
        head_branch=pr_branch,
        head_sha=head_sha,
        base_branch="main",
        mergeable=True,
        draft=False,
        html_url="https://github.com/owner/math-lib/pull/1",
        created_at="2026-09-07T00:00:00Z",
        updated_at="2026-09-07T00:00:00Z",
    )

    # Mock services
    scanner = PRRepairScanner(github_client=mock_gh, agent=SimulatingAgent())
    scanner.pr_service.list_open_prs = AsyncMock(return_value=[pr_detail])
    scanner.pr_service.get_changed_files = AsyncMock(return_value=[
        ChangedFile(filename="calc.py", status="modified", additions=2, deletions=0, changes=2)
    ])
    scanner.review_service.get_reviews = AsyncMock(return_value=[
        ReviewItem(id=10, user="lead_dev", state="CHANGES_REQUESTED", body="calc.py addition test is failing!", submitted_at="2026-09-07T01:00:00Z", commit_id=head_sha, html_url="...")
    ])
    scanner.comment_service.get_review_comments = AsyncMock(return_value=[
        ReviewComment(id=20, user="lead_dev", body="Please fix addition logic in calc.py", path="calc.py", position=2, line=2, commit_id=head_sha, created_at="2026-09-07T01:00:00Z", updated_at="2026-09-07T01:00:00Z", html_url="...")
    ])
    scanner.comment_service.get_issue_comments = AsyncMock(return_value=[])
    scanner.check_service.get_failed_check_runs = AsyncMock(return_value=[])
    scanner.action_service.get_failed_jobs_for_branch = AsyncMock(return_value=[])

    # Point workspace manager to clone from local test bare remote
    custom_ws_base = temp_dir / "workspaces"
    scanner.workspace_mgr.base_dir = custom_ws_base

    # Mock clone to use local bare repo
    async def _mock_prepare_ws(repo_full_name, pr_number, head_branch, clone_url, auth_token=None):
        ws_path = custom_ws_base / "owner-math-lib" / f"pr-{pr_number}"
        ws_path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", "--branch", head_branch, str(remote_dir), str(ws_path)], check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Agent"], cwd=ws_path, check=True)
        subprocess.run(["git", "config", "user.email", "agent@local"], cwd=ws_path, check=True)
        return ws_path

    scanner.workspace_mgr.prepare_workspace = _mock_prepare_ws

    # 5. Execute scanner on repository
    await scanner.scan_repository(repo_cfg)

    # 6. Verify database records
    async with get_async_db() as session:
        from sqlalchemy import select
        stmt = select(PullRequest).where(PullRequest.pr_number == 1)
        pr_db = (await session.execute(stmt)).scalar_one()

        assert pr_db.status == PRStatus.WAITING_FOR_CI
        assert pr_db.repair_attempts == 1

        stmt_rep = select(RepairAttempt).where(RepairAttempt.pull_request_id == pr_db.id)
        attempt_db = (await session.execute(stmt_rep)).scalar_one()

        assert attempt_db.status == "SUCCESS"
        assert attempt_db.tests_passed is True
        assert attempt_db.diff_safety_passed is True
        assert attempt_db.commit_sha is not None


@pytest.mark.asyncio
async def test_duplicate_event_deduplication(temp_dir):
    """Verify scanner does not re-process the exact same review comment."""
    scanner = PRRepairScanner(github_client=AsyncMock(), agent=MockCodingAgent())
    repo_cfg = RepositoryConfig(name="test", github="owner/dedup-repo")

    mock_pr = PullRequestDetail(
        number=99,
        title="Test Dedup",
        body="",
        author="user",
        state="open",
        head_repo="owner/dedup-repo",
        head_branch="feature",
        head_sha="sha99",
        base_branch="main",
        mergeable=True,
        draft=False,
        html_url="",
        created_at="",
        updated_at="",
    )

    # Return same review comment
    scanner.pr_service.list_open_prs = AsyncMock(return_value=[mock_pr])
    scanner.review_service.get_reviews = AsyncMock(return_value=[])
    scanner.comment_service.get_review_comments = AsyncMock(return_value=[
        ReviewComment(id=555, user="bob", body="comment", path="a.py", position=1, line=1, commit_id="sha99", created_at="", updated_at="", html_url="")
    ])
    scanner.comment_service.get_issue_comments = AsyncMock(return_value=[])
    scanner.check_service.get_failed_check_runs = AsyncMock(return_value=[])
    scanner.action_service.get_failed_jobs_for_branch = AsyncMock(return_value=[])

    # First run will process and record event id 555
    with patch.object(scanner, "_execute_repair_pipeline", new_callable=AsyncMock) as mock_pipeline:
        await scanner.scan_repository(repo_cfg)
        assert mock_pipeline.call_count == 1

    # Simulate event 555 recorded as processed in DB
    async with get_async_db() as session:
        from app.database.models import Event
        stmt = select(PullRequest).where(PullRequest.pr_number == 99)
        db_pr = (await session.execute(stmt)).scalar_one()
        session.add(Event(pull_request_id=db_pr.id, event_type="review_comment", external_id="555", status="processed"))

    # Second run with same comment id 555 should NOT trigger pipeline!
    with patch.object(scanner, "_execute_repair_pipeline", new_callable=AsyncMock) as mock_pipeline_2:
        await scanner.scan_repository(repo_cfg)
        assert mock_pipeline_2.call_count == 0


@pytest.mark.asyncio
async def test_max_repair_attempts_reached_halts():
    """Verify scanner halts and marks PR as FAILED when max repair attempts is reached."""
    scanner = PRRepairScanner(github_client=AsyncMock(), agent=MockCodingAgent())
    repo_cfg = RepositoryConfig(
        name="test",
        github="owner/max-attempts-repo",
        repair={"max_attempts": 3},
    )

    mock_pr = PullRequestDetail(
        number=50,
        title="Max Attempt PR",
        body="",
        author="user",
        state="open",
        head_repo="owner/max-attempts-repo",
        head_branch="feature/buggy",
        head_sha="sha50",
        base_branch="main",
        mergeable=True,
        draft=False,
        html_url="",
        created_at="",
        updated_at="",
    )

    scanner.pr_service.list_open_prs = AsyncMock(return_value=[mock_pr])
    scanner.review_service.get_reviews = AsyncMock(return_value=[
        ReviewItem(id=1, user="rev", state="CHANGES_REQUESTED", body="Broken", submitted_at="", commit_id="sha50", html_url="")
    ])
    scanner.comment_service.get_review_comments = AsyncMock(return_value=[])
    scanner.comment_service.get_issue_comments = AsyncMock(return_value=[])
    scanner.check_service.get_failed_check_runs = AsyncMock(return_value=[])
    scanner.action_service.get_failed_jobs_for_branch = AsyncMock(return_value=[])

    # Pre-seed PR in DB with 3 repair attempts already made
    async with get_async_db() as session:
        db_pr = PullRequest(
            repository_id=1,
            repo_full_name="owner/max-attempts-repo",
            pr_number=50,
            title="Max Attempt PR",
            author="user",
            branch="feature/buggy",
            base_branch="main",
            last_commit_sha="sha50",
            status=PRStatus.MONITORING,
            repair_attempts=3,
            max_attempts=3,
        )
        session.add(db_pr)

    # Scanner must refuse to execute pipeline and transition to FAILED
    with patch.object(scanner, "_execute_repair_pipeline", new_callable=AsyncMock) as mock_pipe:
        await scanner.scan_repository(repo_cfg)
        assert mock_pipe.call_count == 0

    async with get_async_db() as session:
        stmt = select(PullRequest).where(PullRequest.pr_number == 50)
        res_pr = (await session.execute(stmt)).scalar_one()
        assert res_pr.status == PRStatus.FAILED


@pytest.mark.asyncio
async def test_agent_failure_transitions_to_failed():
    """Verify that when the coding agent fails or raises an error, the PR transitions to FAILED safely."""
    failing_agent = MockCodingAgent(status="failed", summary="Unable to diagnose failure")
    scanner = PRRepairScanner(github_client=AsyncMock(), agent=failing_agent)
    repo_cfg = RepositoryConfig(name="test", github="owner/failing-agent-repo")

    mock_pr = PullRequestDetail(
        number=77,
        title="Agent Fail PR",
        body="",
        author="user",
        state="open",
        head_repo="owner/failing-agent-repo",
        head_branch="feature/complex",
        head_sha="sha77",
        base_branch="main",
        mergeable=True,
        draft=False,
        html_url="",
        created_at="",
        updated_at="",
    )

    scanner.pr_service.list_open_prs = AsyncMock(return_value=[mock_pr])
    scanner.review_service.get_reviews = AsyncMock(return_value=[
        ReviewItem(id=9, user="lead", state="CHANGES_REQUESTED", body="Complex issue", submitted_at="", commit_id="sha77", html_url="")
    ])
    scanner.comment_service.get_review_comments = AsyncMock(return_value=[])
    scanner.comment_service.get_issue_comments = AsyncMock(return_value=[])
    scanner.check_service.get_failed_check_runs = AsyncMock(return_value=[])
    scanner.action_service.get_failed_jobs_for_branch = AsyncMock(return_value=[])

    # Mock workspace preparation to dummy path
    scanner.workspace_mgr.prepare_workspace = AsyncMock(return_value=Path("/tmp"))
    scanner.pr_service.get_changed_files = AsyncMock(return_value=[])

    await scanner.scan_repository(repo_cfg)

    async with get_async_db() as session:
        stmt = select(PullRequest).where(PullRequest.pr_number == 77)
        res_pr = (await session.execute(stmt)).scalar_one()
        assert res_pr.status == PRStatus.FAILED

