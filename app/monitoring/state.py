"""PR repair state machine and concurrency lock management."""

from datetime import datetime
import logging
from typing import Optional, Set
import asyncio

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.database.models import PRStatus, PullRequest, RepairAttempt

logger = logging.getLogger(__name__)

# Valid state transitions
VALID_TRANSITIONS = {
    PRStatus.MONITORING: {PRStatus.NEEDS_REPAIR, PRStatus.PREPARING, PRStatus.FIXED},
    PRStatus.NEEDS_REPAIR: {PRStatus.PREPARING, PRStatus.MONITORING, PRStatus.FAILED},
    PRStatus.PREPARING: {PRStatus.AGENT_RUNNING, PRStatus.FAILED},
    PRStatus.AGENT_RUNNING: {PRStatus.VERIFYING, PRStatus.FAILED, PRStatus.REQUIRES_HUMAN_REVIEW},
    PRStatus.VERIFYING: {PRStatus.DIFF_REVIEW, PRStatus.FAILED, PRStatus.NEEDS_REPAIR},
    PRStatus.DIFF_REVIEW: {PRStatus.READY_TO_PUSH, PRStatus.REQUIRES_HUMAN_REVIEW, PRStatus.FAILED},
    PRStatus.READY_TO_PUSH: {PRStatus.PUSHING, PRStatus.REQUIRES_HUMAN_REVIEW},
    PRStatus.PUSHING: {PRStatus.WAITING_FOR_CI, PRStatus.FAILED},
    PRStatus.WAITING_FOR_CI: {PRStatus.MONITORING, PRStatus.FIXED, PRStatus.NEEDS_REPAIR},
    PRStatus.FIXED: {PRStatus.MONITORING, PRStatus.NEEDS_REPAIR},
    PRStatus.FAILED: {PRStatus.NEEDS_REPAIR, PRStatus.PREPARING, PRStatus.MONITORING},
    PRStatus.REQUIRES_HUMAN_REVIEW: {PRStatus.READY_TO_PUSH, PRStatus.NEEDS_REPAIR, PRStatus.MONITORING, PRStatus.FAILED},
}


class ConcurrencyLockManager:
    """In-memory and DB-backed concurrency lock to prevent simultaneous repairs on the same PR."""

    def __init__(self, max_concurrent: int = 2):
        self.max_concurrent = max_concurrent
        self._active_locks: Set[str] = set()
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._lock = asyncio.Lock()

    def get_lock_key(self, repo: str, pr_number: int) -> str:
        return f"{repo.lower()}#{pr_number}"

    async def acquire_lock(self, repo: str, pr_number: int) -> bool:
        """Attempt to acquire repair lock for a PR."""
        key = self.get_lock_key(repo, pr_number)
        async with self._lock:
            if key in self._active_locks:
                logger.info("PR %s is already locked by another active process.", key)
                return False
            if len(self._active_locks) >= self.max_concurrent:
                logger.info("Max concurrent repairs limit (%d) reached. Skipping %s.", self.max_concurrent, key)
                return False
            self._active_locks.add(key)
            return True

    async def release_lock(self, repo: str, pr_number: int) -> None:
        """Release repair lock for a PR."""
        key = self.get_lock_key(repo, pr_number)
        async with self._lock:
            self._active_locks.discard(key)


concurrency_lock_manager = ConcurrencyLockManager()


def can_transition(current_status: str, new_status: str) -> bool:
    """Validate whether state transition is allowed."""
    allowed = VALID_TRANSITIONS.get(current_status, set())
    return new_status in allowed or current_status == new_status


async def transition_pr_status(
    session: AsyncSession,
    pr_id: int,
    new_status: str,
    human_approval_required: Optional[bool] = None,
) -> PullRequest:
    """Safely transition PR to a new status in the database."""
    stmt = select(PullRequest).where(PullRequest.id == pr_id)
    result = await session.execute(stmt)
    pr = result.scalar_one_or_none()
    if not pr:
        raise ValueError(f"PullRequest with id={pr_id} not found")

    if not can_transition(pr.status, new_status):
        logger.warning(
            "Invalid transition requested: %s -> %s for PR #%d (%s)",
            pr.status,
            new_status,
            pr.pr_number,
            pr.repo_full_name,
        )

    pr.status = new_status
    pr.updated_at = datetime.utcnow()
    if human_approval_required is not None:
        pr.human_approval_required = human_approval_required

    await session.commit()
    await session.refresh(pr)
    return pr
