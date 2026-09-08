"""Tests for Coding Agent abstraction, prompts, and context builder."""

import pytest
from app.agent.base import AgentContext, CodingAgent, RepairResult
from app.agent.mock import MockCodingAgent
from app.agent.prompts import SURGICAL_REPAIR_SYSTEM_PROMPT, generate_repair_user_prompt
from app.agent.context import PRContextBuilder
from app.github.prs import PullRequestDetail, ChangedFile
from app.github.reviews import ReviewItem
from app.github.comments import ReviewComment


def test_surgical_repair_system_prompt_rules():
    assert "Work ONLY on the current PR branch" in SURGICAL_REPAIR_SYSTEM_PROMPT
    assert "Do NOT force push" in SURGICAL_REPAIR_SYSTEM_PROMPT
    assert "Do NOT merge anything" in SURGICAL_REPAIR_SYSTEM_PROMPT
    assert "Do NOT touch, create, or modify secrets" in SURGICAL_REPAIR_SYSTEM_PROMPT
    assert "Make the SMALLEST reasonable surgical change" in SURGICAL_REPAIR_SYSTEM_PROMPT


def test_pr_context_builder_formatting(temp_dir):
    pr = PullRequestDetail(
        number=42,
        title="Enhance token validation",
        body="Improves expired token handling.",
        author="alice",
        state="open",
        head_repo="acme/api",
        head_branch="feature/token-val",
        head_sha="9876543210abcdef",
        base_branch="main",
        mergeable=True,
        draft=False,
        html_url="https://github.com/acme/api/pull/42",
        created_at="2026-09-07T00:00:00Z",
        updated_at="2026-09-07T01:00:00Z",
    )

    changed_files = [
        ChangedFile(filename="src/auth.py", status="modified", additions=10, deletions=2, changes=12)
    ]
    reviews = [
        ReviewItem(
            id=1,
            user="bob",
            state="CHANGES_REQUESTED",
            body="Return 401 instead of 403 on expiry.",
            submitted_at="2026-09-07T01:30:00Z",
            commit_id="9876543210abcdef",
            html_url="https://...",
        )
    ]
    review_comments = [
        ReviewComment(
            id=10,
            user="bob",
            body="Use HTTPException(status_code=401)",
            path="src/auth.py",
            position=1,
            line=24,
            commit_id="9876543210abcdef",
            created_at="2026-09-07T01:35:00Z",
            updated_at="2026-09-07T01:35:00Z",
            html_url="https://...",
            diff_hunk="@@ -23,3 +23,4 @@",
        )
    ]
    repo_instructions = {
        "CONTRIBUTING.md": "# Guidelines\nAll endpoints must return standard JSON error schemas."
    }

    markdown = PRContextBuilder.build_context_markdown(
        pr=pr,
        changed_files=changed_files,
        reviews=reviews,
        review_comments=review_comments,
        issue_comments=[],
        failed_checks=[],
        failed_jobs=[],
        repo_instructions=repo_instructions,
    )

    assert "acme/api #42" in markdown
    assert "Enhance token validation" in markdown
    assert "feature/token-val" in markdown
    assert "`src/auth.py` (Line 24)" in markdown
    assert "Return 401 instead of 403" in markdown
    assert "All endpoints must return standard JSON error schemas" in markdown

    # Write to temp workspace
    context_file = PRContextBuilder.write_to_workspace(temp_dir, markdown)
    assert context_file.exists()
    assert "Enhance token validation" in context_file.read_text()


@pytest.mark.asyncio
async def test_mock_coding_agent_success():
    agent = MockCodingAgent(status="success", summary="Resolved token validation bug", files_changed=["src/auth.py"])
    context = AgentContext(
        repo_name="acme/api",
        pr_number=42,
        branch="feature/token-val",
        workspace_path="/tmp",
        context_markdown="...",
        run_checks_commands=["pytest"],
    )
    res = await agent.repair(context)

    assert res.status == "success"
    assert res.files_changed == ["src/auth.py"]
    assert res.tests_passed is True
