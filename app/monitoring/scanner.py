"""Continuous PR Scanner, Event Deduplicator, and Repair Orchestrator."""

from datetime import datetime
import logging
from pathlib import Path
from typing import Dict, List, Optional
import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.antigravity import AntigravityAgent
from app.agent.base import AgentContext, CodingAgent, RepairResult
from app.agent.context import PRContextBuilder
from app.agent.mock import MockCodingAgent
from app.config.repositories import RepositoryConfig, load_repositories_config
from app.config.settings import settings
from app.database.database import get_async_db
from app.database.models import (
    AgentRun,
    CommitRecord,
    Event,
    PRStatus,
    PullRequest,
    RepairAttempt,
    Repository,
    VerificationRun,
)
from app.github.actions import ActionService
from app.github.checks import CheckService
from app.github.client import GitHubClient
from app.github.comments import CommentService
from app.github.prs import PRService, PullRequestDetail
from app.github.reviews import ReviewService
from app.monitoring.analyzer import FeedbackAnalyzer
from app.monitoring.state import concurrency_lock_manager, transition_pr_status
from app.notifications import NotificationEvent, NotificationMessage, notification_manager
from app.verification.diff import DiffInspector
from app.verification.runner import VerificationRunner
from app.workspace.git import SafeGitService
from app.workspace.manager import WorkspaceManager

logger = logging.getLogger(__name__)


class PRRepairScanner:
    """Scans configured repositories for PRs requiring repairs and orchestrates the repair lifecycle."""

    def __init__(
        self,
        github_client: Optional[GitHubClient] = None,
        agent: Optional[CodingAgent] = None,
    ):
        self.github_client = github_client or GitHubClient()
        self.agent = agent or (
            AntigravityAgent() if settings.AGENT_PROVIDER == "antigravity" else MockCodingAgent()
        )
        self.pr_service = PRService(self.github_client)
        self.review_service = ReviewService(self.github_client)
        self.comment_service = CommentService(self.github_client)
        self.check_service = CheckService(self.github_client)
        self.action_service = ActionService(self.github_client)
        self.workspace_mgr = WorkspaceManager()
        self.verification_runner = VerificationRunner()

    async def scan_all_repositories(self) -> None:
        """Scan all repositories configured in repositories.yaml."""
        repos = load_repositories_config(settings.CONFIG_PATH)
        if not repos:
            logger.info("No repositories configured in %s", settings.CONFIG_PATH)
            return

        for repo_cfg in repos:
            if not repo_cfg.enabled:
                logger.debug("Repository %s is disabled, skipping.", repo_cfg.github)
                continue
            try:
                await self.scan_repository(repo_cfg)
            except GitHubAPIError as exc:
                if exc.status_code == 404:
                    logger.warning("Repository %s not found on GitHub (404). Check spelling or repository access permissions.", repo_cfg.github)
                elif exc.status_code == 403:
                    logger.warning("Access denied or rate limit exceeded scanning %s: %s", repo_cfg.github, exc)
                else:
                    logger.warning("GitHub error scanning repository %s: %s", repo_cfg.github, exc)
            except Exception as exc:
                logger.exception("Error scanning repository %s: %s", repo_cfg.github, exc)

    async def scan_repository(self, repo_cfg: RepositoryConfig) -> None:
        """Scan a single repository for open PRs and process required repairs."""
        if settings.is_repo_blocked(repo_cfg.github):
            logger.info("Repository %s is in blocked organizations list, skipping scan.", repo_cfg.github)
            return

        logger.info("Scanning repository %s", repo_cfg.github)

        # Sync repository in DB
        async with get_async_db() as session:
            stmt = select(Repository).where(Repository.github_full_name == repo_cfg.github)
            res = await session.execute(stmt)
            repo_record = res.scalar_one_or_none()
            if not repo_record:
                repo_record = Repository(name=repo_cfg.name, github_full_name=repo_cfg.github, enabled=repo_cfg.enabled)
                session.add(repo_record)
                await session.flush()
            repo_record.last_scanned_at = datetime.utcnow()
            repo_id = repo_record.id

        # Discover PRs
        author_filter = settings.GITHUB_USERNAME if repo_cfg.pull_requests.only_my_prs else None
        prs = await self.pr_service.list_open_prs(repo_cfg.github, author=author_filter)
        logger.info("Discovered %d open PR(s) for %s", len(prs), repo_cfg.github)

        for pr in prs:
            try:
                await self.evaluate_and_repair_pr(repo_cfg, repo_id, pr)
            except Exception as exc:
                logger.exception("Failed processing PR #%d for %s: %s", pr.number, repo_cfg.github, exc)

    async def evaluate_and_repair_pr(
        self,
        repo_cfg: RepositoryConfig,
        repo_id: int,
        pr: PullRequestDetail,
    ) -> None:
        """Evaluate whether a PR requires action and execute the repair workflow."""
        repo_full_name = repo_cfg.github

        # 1. Sync or retrieve PullRequest DB record
        async with get_async_db() as session:
            stmt = select(PullRequest).where(
                PullRequest.repo_full_name == repo_full_name,
                PullRequest.pr_number == pr.number,
            )
            res = await session.execute(stmt)
            db_pr = res.scalar_one_or_none()
            if not db_pr:
                db_pr = PullRequest(
                    repository_id=repo_id,
                    repo_full_name=repo_full_name,
                    pr_number=pr.number,
                    title=pr.title,
                    author=pr.author,
                    branch=pr.head_branch,
                    base_branch=pr.base_branch,
                    last_commit_sha=pr.head_sha,
                    status=PRStatus.MONITORING,
                    max_attempts=repo_cfg.repair.max_attempts,
                )
                session.add(db_pr)
                await session.flush()

            db_pr_id = db_pr.id
            current_status = db_pr.status
            repair_attempts = db_pr.repair_attempts
            max_attempts = db_pr.max_attempts

        # 2. Fetch feedback and checks
        reviews = await self.review_service.get_reviews(repo_full_name, pr.number)
        review_comments = await self.comment_service.get_review_comments(repo_full_name, pr.number)
        issue_comments = await self.comment_service.get_issue_comments(repo_full_name, pr.number)
        failed_checks = await self.check_service.get_failed_check_runs(repo_full_name, pr.head_sha)
        failed_jobs = await self.action_service.get_failed_jobs_for_branch(repo_full_name, pr.head_branch, pr.head_sha)

        # 3. Filter out previously processed events (Event Deduplication)
        unprocessed_review_comments = []
        unprocessed_reviews = []
        unprocessed_issue_comments = []
        unprocessed_checks = []

        async with get_async_db() as session:
            for rc in review_comments:
                stmt = select(Event).where(
                    Event.pull_request_id == db_pr_id,
                    Event.event_type == "review_comment",
                    Event.external_id == str(rc.id),
                )
                if not (await session.execute(stmt)).scalar_one_or_none():
                    unprocessed_review_comments.append(rc)

            for r in reviews:
                stmt = select(Event).where(
                    Event.pull_request_id == db_pr_id,
                    Event.event_type == "review",
                    Event.external_id == str(r.id),
                )
                if not (await session.execute(stmt)).scalar_one_or_none():
                    unprocessed_reviews.append(r)

            for ic in issue_comments:
                stmt = select(Event).where(
                    Event.pull_request_id == db_pr_id,
                    Event.event_type == "issue_comment",
                    Event.external_id == str(ic.id),
                )
                if not (await session.execute(stmt)).scalar_one_or_none():
                    unprocessed_issue_comments.append(ic)

            for fc in failed_checks:
                stmt = select(Event).where(
                    Event.pull_request_id == db_pr_id,
                    Event.event_type == "check_run",
                    Event.external_id == f"{fc.id}:{pr.head_sha}",
                )
                if not (await session.execute(stmt)).scalar_one_or_none():
                    unprocessed_checks.append(fc)

        # 4. Diagnose if action is required
        diagnosis = FeedbackAnalyzer.diagnose(
            reviews=unprocessed_reviews,
            review_comments=unprocessed_review_comments,
            issue_comments=unprocessed_issue_comments,
            failed_checks=unprocessed_checks,
            failed_jobs=failed_jobs,
        )

        if not diagnosis.has_actionable_issues:
            logger.debug("No new actionable issues for PR #%d (%s)", pr.number, repo_full_name)
            async with get_async_db() as session:
                stmt = select(PullRequest).where(PullRequest.id == db_pr_id)
                p = (await session.execute(stmt)).scalar_one()
                p.last_scan_time = datetime.utcnow()
            return

        logger.info(
            "Action required for PR #%d (%s): %s",
            pr.number,
            repo_full_name,
            "; ".join(diagnosis.reasons),
        )

        # 5. Check if repairs are globally disabled
        if not settings.REPAIRS_ENABLED:
            logger.info("Repairs are paused/disabled globally (REPAIRS_ENABLED=False). Skipping repair for PR #%d (%s).", pr.number, repo_full_name)
            return

        # 6. Check repair attempt limits
        if repair_attempts >= max_attempts:
            logger.warning("Max repair attempts (%d) reached for PR #%d. Halting.", max_attempts, pr.number)
            async with get_async_db() as session:
                await transition_pr_status(session, db_pr_id, PRStatus.FAILED)
            await notification_manager.notify(
                NotificationMessage(
                    event_type=NotificationEvent.MAX_ATTEMPTS_REACHED,
                    repository=repo_full_name,
                    pr_number=pr.number,
                    title="Maximum repair attempts reached",
                    body=f"PR #{pr.number} reached the limit of {max_attempts} repair attempts. Stopping automatic repairs.",
                    details_url=pr.html_url,
                )
            )
            return

        # 7. Acquire Concurrency Lock
        acquired = await concurrency_lock_manager.acquire_lock(repo_full_name, pr.number)
        if not acquired:
            logger.info("PR #%d is currently locked by another operation. Skipping.", pr.number)
            return

        try:
            await self._execute_repair_pipeline(
                repo_cfg=repo_cfg,
                db_pr_id=db_pr_id,
                pr=pr,
                diagnosis_reasons="; ".join(diagnosis.reasons),
                reviews=unprocessed_reviews,
                review_comments=unprocessed_review_comments,
                issue_comments=unprocessed_issue_comments,
                failed_checks=unprocessed_checks,
                failed_jobs=failed_jobs,
            )
        finally:
            await concurrency_lock_manager.release_lock(repo_full_name, pr.number)

    async def _execute_repair_pipeline(
        self,
        repo_cfg: RepositoryConfig,
        db_pr_id: int,
        pr: PullRequestDetail,
        diagnosis_reasons: str,
        reviews: list,
        review_comments: list,
        issue_comments: list,
        failed_checks: list,
        failed_jobs: list,
    ) -> None:
        repo_full_name = repo_cfg.github
        # Check if repairs are globally disabled
        if not settings.REPAIRS_ENABLED:
            logger.info("Automatic repairs are disabled (REPAIRS_ENABLED=False). Halting pipeline for PR #%d (%s).", pr.number, repo_full_name)
            return

        # 1. Transition state to PREPARING
        async with get_async_db() as session:
            await transition_pr_status(session, db_pr_id, PRStatus.PREPARING)
            stmt = select(PullRequest).where(PullRequest.id == db_pr_id)
            db_pr = (await session.execute(stmt)).scalar_one()
            db_pr.repair_attempts += 1
            attempt_num = db_pr.repair_attempts

            # Record repair attempt in DB
            attempt_record = RepairAttempt(
                pull_request_id=db_pr_id,
                attempt_number=attempt_num,
                trigger_reason=diagnosis_reasons,
                status="RUNNING",
            )
            session.add(attempt_record)
            await session.flush()
            attempt_id = attempt_record.id

        await notification_manager.notify(
            NotificationMessage(
                event_type=NotificationEvent.REPAIR_STARTED,
                repository=repo_full_name,
                pr_number=pr.number,
                title=f"Repair Attempt #{attempt_num} Started",
                body=f"Triggered by: {diagnosis_reasons}",
                details_url=pr.html_url,
            )
        )

        try:
            # 2. Prepare isolated workspace (use head_repo if PR was submitted from a fork)
            clone_repo = pr.head_repo if (pr.head_repo and "/" in pr.head_repo) else repo_full_name
            workspace_path = await self.workspace_mgr.prepare_workspace(
                repo_full_name=repo_full_name,
                pr_number=pr.number,
                head_branch=pr.head_branch,
                clone_url=f"https://github.com/{clone_repo}.git",
                auth_token=settings.GITHUB_TOKEN,
            )

            # 3. Read repo instructions & build context
            changed_files = await self.pr_service.get_changed_files(repo_full_name, pr.number)
            repo_instructions = PRContextBuilder.read_repo_instructions(workspace_path)
            context_markdown = PRContextBuilder.build_context_markdown(
                pr=pr,
                changed_files=changed_files,
                reviews=reviews,
                review_comments=review_comments,
                issue_comments=issue_comments,
                failed_checks=failed_checks,
                failed_jobs=failed_jobs,
                repo_instructions=repo_instructions,
            )
            PRContextBuilder.write_to_workspace(workspace_path, context_markdown)

            # 4. Transition to AGENT_RUNNING and execute Coding Agent
            async with get_async_db() as session:
                await transition_pr_status(session, db_pr_id, PRStatus.AGENT_RUNNING)

            all_check_cmds = (
                repo_cfg.checks.test
                + repo_cfg.checks.lint
                + repo_cfg.checks.static_analysis
                + repo_cfg.checks.security
                + repo_cfg.checks.build
            )
            agent_ctx = AgentContext(
                repo_name=repo_full_name,
                pr_number=pr.number,
                branch=pr.head_branch,
                workspace_path=str(workspace_path),
                context_markdown=context_markdown,
                instruction_files=repo_instructions,
                run_checks_commands=all_check_cmds,
            )

            start_agent_time = datetime.utcnow()
            agent_result: RepairResult = await self.agent.repair(agent_ctx)
            agent_duration = int((datetime.utcnow() - start_agent_time).total_seconds())

            # Log agent run
            async with get_async_db() as session:
                agent_run_log = AgentRun(
                    repair_attempt_id=attempt_id,
                    agent_provider=settings.AGENT_PROVIDER,
                    model=settings.ANTIGRAVITY_MODEL,
                    prompt_summary=diagnosis_reasons,
                    status=agent_result.status,
                    duration_seconds=agent_duration,
                    raw_output=agent_result.raw_output,
                    structured_output=json.dumps(agent_result.model_dump()),
                )
                session.add(agent_run_log)

            if agent_result.status not in ("success", "completed"):
                raise RuntimeError(f"Coding Agent failed with status: {agent_result.status} ({agent_result.notes})")

            # 5. Transition to VERIFYING and run verification suite
            async with get_async_db() as session:
                await transition_pr_status(session, db_pr_id, PRStatus.VERIFYING)

            verification_res = await self.verification_runner.run_suite(workspace_path, repo_cfg.checks)

            # Record verification results
            async with get_async_db() as session:
                for single_res in verification_res.results:
                    v_run = VerificationRun(
                        repair_attempt_id=attempt_id,
                        check_type=single_res.category,
                        command=single_res.command,
                        passed=single_res.passed,
                        exit_code=single_res.exit_code,
                        stdout=single_res.stdout,
                        stderr=single_res.stderr,
                        duration_seconds=int(single_res.duration_seconds),
                    )
                    session.add(v_run)

            if not verification_res.passed:
                err_msg = f"Verification checks failed: {'; '.join(verification_res.errors)}"
                raise RuntimeError(err_msg)

            # 6. Transition to DIFF_REVIEW and inspect git diff safety
            async with get_async_db() as session:
                await transition_pr_status(session, db_pr_id, PRStatus.DIFF_REVIEW)

            diff_safety = await DiffInspector.inspect(workspace_path, repo_cfg.safety)

            if not diff_safety.passed or diff_safety.requires_human_review:
                violation_summary = "; ".join(diff_safety.violations)
                logger.warning("Diff safety check flagged violations: %s", violation_summary)
                async with get_async_db() as session:
                    await transition_pr_status(session, db_pr_id, PRStatus.REQUIRES_HUMAN_REVIEW, human_approval_required=True)
                    stmt = select(PullRequest).where(PullRequest.id == db_pr_id)
                    p = (await session.execute(stmt)).scalar_one()
                    p.latest_diff = diff_safety.diff_text

                await notification_manager.notify(
                    NotificationMessage(
                        event_type=NotificationEvent.UNEXPECTED_FILES_CHANGED,
                        repository=repo_full_name,
                        pr_number=pr.number,
                        title="Diff Safety Flagged - Human Approval Required",
                        body=f"Violations detected: {violation_summary}",
                        details_url=pr.html_url,
                    )
                )
                return

            # 7. Check automation mode (Auto vs Approval)
            if settings.AUTOMATION_MODE == "approval":
                logger.info("Approval mode active. Halting before push for human review.")
                async with get_async_db() as session:
                    await transition_pr_status(session, db_pr_id, PRStatus.REQUIRES_HUMAN_REVIEW, human_approval_required=True)
                    stmt = select(PullRequest).where(PullRequest.id == db_pr_id)
                    p = (await session.execute(stmt)).scalar_one()
                    p.latest_diff = diff_safety.diff_text

                await notification_manager.notify(
                    NotificationMessage(
                        event_type=NotificationEvent.HUMAN_APPROVAL_REQUIRED,
                        repository=repo_full_name,
                        pr_number=pr.number,
                        title="Human Approval Required to Push",
                        body=f"Repair passed tests and diff checks. Waiting for approval before pushing to {pr.head_branch}.",
                        details_url=pr.html_url,
                    )
                )
                return

            # 8. Commit and Push in Auto Mode
            await self._commit_and_push(
                workspace_path=workspace_path,
                repo_full_name=repo_full_name,
                pr=pr,
                db_pr_id=db_pr_id,
                attempt_id=attempt_id,
                diff_safety=diff_safety,
                reviews=reviews,
                review_comments=review_comments,
                issue_comments=issue_comments,
                failed_checks=failed_checks,
            )

        except Exception as exc:
            logger.exception("Repair pipeline failed for PR #%d: %s", pr.number, exc)
            async with get_async_db() as session:
                await transition_pr_status(session, db_pr_id, PRStatus.FAILED)
                stmt = select(RepairAttempt).where(RepairAttempt.id == attempt_id)
                att = (await session.execute(stmt)).scalar_one()
                att.status = "FAILED"
                att.error_message = str(exc)
                att.finished_at = datetime.utcnow()

            await notification_manager.notify(
                NotificationMessage(
                    event_type=NotificationEvent.REPAIR_FAILED,
                    repository=repo_full_name,
                    pr_number=pr.number,
                    title="Repair Attempt Failed",
                    body=f"Error: {exc}",
                    details_url=pr.html_url,
                )
            )

    async def _commit_and_push(
        self,
        workspace_path: Path,
        repo_full_name: str,
        pr: PullRequestDetail,
        db_pr_id: int,
        attempt_id: int,
        diff_safety: Any,
        reviews: list,
        review_comments: list,
        issue_comments: list,
        failed_checks: list,
    ) -> None:
        """Stage, commit, verify guardrails, push, and mark events as processed."""
        async with get_async_db() as session:
            await transition_pr_status(session, db_pr_id, PRStatus.READY_TO_PUSH)

        commit_msg = f"fix(pr-repair): autonomous repair for PR #{pr.number}\n\nCo-authored-by: PR-Repair-Agent <pr-repair-agent@local>"
        new_sha = await SafeGitService.stage_and_commit(workspace_path, commit_msg)

        async with get_async_db() as session:
            await transition_pr_status(session, db_pr_id, PRStatus.PUSHING)

        if new_sha:
            await SafeGitService.push_to_pr_branch(
                repo_dir=workspace_path,
                expected_repo=repo_full_name,
                expected_branch=pr.head_branch,
                auth_token=settings.GITHUB_TOKEN,
            )

        # Transition to WAITING_FOR_CI
        async with get_async_db() as session:
            await transition_pr_status(session, db_pr_id, PRStatus.WAITING_FOR_CI)
            stmt = select(PullRequest).where(PullRequest.id == db_pr_id)
            p = (await session.execute(stmt)).scalar_one()
            p.last_commit_sha = new_sha or pr.head_sha
            p.last_repair_time = datetime.utcnow()
            p.latest_diff = diff_safety.diff_text

            stmt_att = select(RepairAttempt).where(RepairAttempt.id == attempt_id)
            att = (await session.execute(stmt_att)).scalar_one()
            att.status = "SUCCESS"
            att.tests_passed = True
            att.diff_safety_passed = True
            att.files_changed_count = diff_safety.files_changed_count
            att.commit_sha = new_sha
            att.finished_at = datetime.utcnow()

            if new_sha:
                commit_record = CommitRecord(
                    pull_request_id=db_pr_id,
                    commit_sha=new_sha,
                    message=commit_msg,
                    branch=pr.head_branch,
                )
                session.add(commit_record)

            # Mark processed events for deduplication
            for rc in review_comments:
                session.add(Event(pull_request_id=db_pr_id, event_type="review_comment", external_id=str(rc.id), status="processed"))
            for r in reviews:
                session.add(Event(pull_request_id=db_pr_id, event_type="review", external_id=str(r.id), status="processed"))
            for ic in issue_comments:
                session.add(Event(pull_request_id=db_pr_id, event_type="issue_comment", external_id=str(ic.id), status="processed"))
            for fc in failed_checks:
                session.add(Event(pull_request_id=db_pr_id, event_type="check_run", external_id=f"{fc.id}:{pr.head_sha}", status="processed"))

        await notification_manager.notify(
            NotificationMessage(
                event_type=NotificationEvent.REPAIR_SUCCEEDED,
                repository=repo_full_name,
                pr_number=pr.number,
                title="Repair Succeeded and Pushed",
                body=f"Fix committed ({new_sha or 'clean'}) and pushed to `{pr.head_branch}`. Awaiting CI completion.",
                details_url=pr.html_url,
            )
        )
