"""Tests for git diff inspection, safety thresholds, and secret scanning."""

import subprocess
from pathlib import Path
import pytest

from app.config.repositories import RepoSafetyConfig
from app.verification.diff import DiffInspector


@pytest.mark.asyncio
async def test_diff_inspector_detects_forbidden_env_file(temp_dir):
    subprocess.run(["git", "init", str(temp_dir)], check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=temp_dir, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=temp_dir, check=True)

    # Initial commit
    (temp_dir / "app.py").write_text("print('hello')")
    subprocess.run(["git", "add", "."], cwd=temp_dir, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=temp_dir, check=True)

    # Create forbidden .env file
    (temp_dir / ".env").write_text("SECRET_KEY=123456")

    safety_cfg = RepoSafetyConfig(max_files_changed=10, max_lines_added=100)
    res = await DiffInspector.inspect(temp_dir, safety_cfg)

    assert res.passed is False
    assert res.requires_human_review is True
    assert ".env" in res.forbidden_files


@pytest.mark.asyncio
async def test_diff_inspector_detects_secret_in_diff(temp_dir):
    subprocess.run(["git", "init", str(temp_dir)], check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=temp_dir, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=temp_dir, check=True)

    # Initial commit
    (temp_dir / "config.py").write_text("# config")
    subprocess.run(["git", "add", "."], cwd=temp_dir, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=temp_dir, check=True)

    # Modify with an AWS key
    (temp_dir / "config.py").write_text("AWS_KEY = 'AKIA1234567890ABCDEF'")

    safety_cfg = RepoSafetyConfig(max_files_changed=10, max_lines_added=100)
    res = await DiffInspector.inspect(temp_dir, safety_cfg)

    assert res.passed is False
    assert res.requires_human_review is True
    assert any("AWS Access Key" in s for s in res.secrets_detected)


@pytest.mark.asyncio
async def test_diff_inspector_flags_excessive_files_changed(temp_dir):
    subprocess.run(["git", "init", str(temp_dir)], check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=temp_dir, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=temp_dir, check=True)

    (temp_dir / "init.txt").write_text("init")
    subprocess.run(["git", "add", "."], cwd=temp_dir, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=temp_dir, check=True)

    # Create 5 files when limit is 2
    for i in range(5):
        (temp_dir / f"file_{i}.txt").write_text(f"content {i}")

    safety_cfg = RepoSafetyConfig(max_files_changed=2)
    res = await DiffInspector.inspect(temp_dir, safety_cfg)

    assert res.passed is False
    assert res.requires_human_review is True
    assert any("Too many files changed" in v for v in res.violations)
