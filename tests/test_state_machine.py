"""Tests for state transitions, concurrency locks, and retry limit rules."""

import pytest
from app.database.models import PRStatus
from app.monitoring.state import (
    can_transition,
    concurrency_lock_manager,
    ConcurrencyLockManager,
)


def test_valid_and_invalid_state_transitions():
    assert can_transition(PRStatus.MONITORING, PRStatus.NEEDS_REPAIR) is True
    assert can_transition(PRStatus.NEEDS_REPAIR, PRStatus.PREPARING) is True
    assert can_transition(PRStatus.PREPARING, PRStatus.AGENT_RUNNING) is True
    assert can_transition(PRStatus.AGENT_RUNNING, PRStatus.VERIFYING) is True
    assert can_transition(PRStatus.VERIFYING, PRStatus.DIFF_REVIEW) is True
    assert can_transition(PRStatus.DIFF_REVIEW, PRStatus.READY_TO_PUSH) is True
    assert can_transition(PRStatus.READY_TO_PUSH, PRStatus.PUSHING) is True
    assert can_transition(PRStatus.PUSHING, PRStatus.WAITING_FOR_CI) is True

    # Invalid jump
    assert can_transition(PRStatus.MONITORING, PRStatus.PUSHING) is False
    assert can_transition(PRStatus.AGENT_RUNNING, PRStatus.READY_TO_PUSH) is False


@pytest.mark.asyncio
async def test_concurrency_lock_prevents_simultaneous_repair():
    lock_mgr = ConcurrencyLockManager(max_concurrent=2)

    # Acquire lock for PR #101
    acquired1 = await lock_mgr.acquire_lock("owner/repo", 101)
    assert acquired1 is True

    # Second attempt for same PR must be rejected
    acquired_dup = await lock_mgr.acquire_lock("owner/repo", 101)
    assert acquired_dup is False

    # PR #102 on same repo can be acquired (within max 2)
    acquired2 = await lock_mgr.acquire_lock("owner/repo", 102)
    assert acquired2 is True

    # PR #103 must be rejected because max_concurrent (2) is reached
    acquired3 = await lock_mgr.acquire_lock("owner/repo", 103)
    assert acquired3 is False

    # Release PR #101
    await lock_mgr.release_lock("owner/repo", 101)

    # Now PR #103 can be acquired
    acquired3_retry = await lock_mgr.acquire_lock("owner/repo", 103)
    assert acquired3_retry is True
