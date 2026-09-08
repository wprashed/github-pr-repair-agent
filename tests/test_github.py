"""Tests for GitHub integration, API responses, and event deduplication."""

import pytest
from unittest.mock import AsyncMock, patch

from app.github.actions import ActionService
from app.github.checks import CheckService
from app.github.client import GitHubClient, GitHubAPIError
from app.github.comments import CommentService
from app.github.prs import PRService
from app.github.reviews import ReviewService


@pytest.mark.asyncio
async def test_list_open_prs():
    mock_client = AsyncMock(spec=GitHubClient)
    mock_client.get.return_value = [
        {
            "number": 101,
            "title": "Fix memory leak in parser",
            "body": "Resolves issue #42",
            "user": {"login": "octocat"},
            "state": "open",
            "head": {"repo": {"full_name": "owner/repo"}, "ref": "fix-leak", "sha": "abc1234"},
            "base": {"ref": "main"},
            "mergeable": True,
            "draft": False,
            "html_url": "https://github.com/owner/repo/pull/101",
            "created_at": "2026-09-07T10:00:00Z",
            "updated_at": "2026-09-07T10:30:00Z",
            "labels": [{"name": "bug"}],
        }
    ]

    pr_service = PRService(mock_client)
    prs = await pr_service.list_open_prs("owner/repo")

    assert len(prs) == 1
    assert prs[0].number == 101
    assert prs[0].author == "octocat"
    assert prs[0].head_branch == "fix-leak"
    assert prs[0].head_sha == "abc1234"


@pytest.mark.asyncio
async def test_review_detection_request_changes():
    mock_client = AsyncMock(spec=GitHubClient)
    mock_client.get.return_value = [
        {
            "id": 1,
            "user": {"login": "reviewer_alice"},
            "state": "CHANGES_REQUESTED",
            "body": "Please validate user input and add missing test.",
            "submitted_at": "2026-09-07T10:15:00Z",
            "commit_id": "abc1234",
            "html_url": "https://github.com/owner/repo/pull/101#pullrequestreview-1",
        },
        {
            "id": 2,
            "user": {"login": "reviewer_bob"},
            "state": "COMMENTED",
            "body": "Looks mostly good.",
            "submitted_at": "2026-09-07T10:20:00Z",
            "commit_id": "abc1234",
            "html_url": "https://github.com/owner/repo/pull/101#pullrequestreview-2",
        },
    ]

    review_service = ReviewService(mock_client)
    requested = await review_service.get_requested_changes("owner/repo", 101)

    assert len(requested) == 1
    assert requested[0].user == "reviewer_alice"
    assert requested[0].state == "CHANGES_REQUESTED"


@pytest.mark.asyncio
async def test_ci_log_intelligent_parsing():
    raw_log = """
    Running test suite...
    tests/test_auth.py::test_login PASSED
    tests/test_api.py::test_create_record FAILED
    =================================== FAILURES ===================================
    ______________________________ test_create_record ______________________________
    Traceback (most recent call last):
      File "/app/tests/test_api.py", line 45, in test_create_record
        assert response.status_code == 201
    AssertionError: assert 400 == 201
    =========================== short test summary info ============================
    FAILED tests/test_api.py::test_create_record - AssertionError: assert 400 == 201
    """

    parsed = ActionService.parse_ci_log(raw_log)

    assert len(parsed["errors"]) > 0
    assert any("AssertionError" in err or "FAILED" in err for err in parsed["errors"])
    assert len(parsed["stack_traces"]) > 0
    assert any("assert response.status_code == 201" in st for st in parsed["stack_traces"])
    assert any("tests/test_api.py:45" in fr for fr in parsed["file_references"])
