"""Tests for verification suite execution and multi-language checks."""

import pytest
from app.config.repositories import ChecksConfig
from app.verification.runner import VerificationRunner


@pytest.mark.asyncio
async def test_verification_runner_success(temp_dir):
    runner = VerificationRunner(timeout_per_command=10)
    checks = ChecksConfig(
        test=["python3 -c 'print(\"Tests passed\"); exit(0)'"],
        lint=["python3 -c 'print(\"Lint clean\"); exit(0)'"],
    )

    result = await runner.run_suite(temp_dir, checks)

    assert result.passed is True
    assert result.tests["passed"] is True
    assert result.lint["passed"] is True
    assert len(result.errors) == 0


@pytest.mark.asyncio
async def test_verification_runner_detects_failure(temp_dir):
    runner = VerificationRunner(timeout_per_command=10)
    checks = ChecksConfig(
        test=["python3 -c 'import sys; sys.stderr.write(\"FAILED: test_calc failed\\n\"); exit(1)'"]
    )

    result = await runner.run_suite(temp_dir, checks)

    assert result.passed is False
    assert result.tests["passed"] is False
    assert any("FAILED: test_calc failed" in err for err in result.errors)


@pytest.mark.asyncio
async def test_verification_runner_handles_timeout(temp_dir):
    runner = VerificationRunner(timeout_per_command=1)
    checks = ChecksConfig(
        test=["sleep 5"]
    )

    result = await runner.run_suite(temp_dir, checks)

    assert result.passed is False
    assert any("timed out" in err for err in result.errors)
