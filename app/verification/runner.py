"""Verification suite runner for tests, linting, and static analysis."""

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.config.repositories import ChecksConfig

logger = logging.getLogger(__name__)


@dataclass
class SingleCheckResult:
    category: str
    command: str
    passed: bool
    exit_code: int
    duration_seconds: float
    stdout: str
    stderr: str
    errors_detected: List[str] = field(default_factory=list)


@dataclass
class VerificationSuiteResult:
    passed: bool
    tests: Dict[str, Any] = field(default_factory=dict)
    lint: Dict[str, Any] = field(default_factory=dict)
    static_analysis: Dict[str, Any] = field(default_factory=dict)
    security: Dict[str, Any] = field(default_factory=dict)
    build: Dict[str, Any] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)
    results: List[SingleCheckResult] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "passed": self.passed,
            "tests": self.tests,
            "lint": self.lint,
            "static_analysis": self.static_analysis,
            "security": self.security,
            "build": self.build,
            "errors": self.errors,
        }


class VerificationRunner:
    """Runs repository verification commands in the isolated workspace."""

    def __init__(self, timeout_per_command: int = 300):
        self.timeout = timeout_per_command

    async def execute_command(self, workspace_path: Path, category: str, command: str) -> SingleCheckResult:
        """Run a single shell command in the workspace directory."""
        start_time = time.time()
        logger.info("[%s] Running '%s' in %s", category, command, workspace_path)

        import sys
        import os
        sub_env = os.environ.copy()
        venv_bin = str(Path(sys.executable).parent)
        sub_env["PATH"] = f"{venv_bin}:{sub_env.get('PATH', '')}"

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                cwd=str(workspace_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=sub_env,
            )

            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(),
                timeout=float(self.timeout),
            )

            stdout = stdout_bytes.decode("utf-8", errors="replace")
            stderr = stderr_bytes.decode("utf-8", errors="replace")
            duration = time.time() - start_time
            passed = (proc.returncode == 0)

            errors: List[str] = []
            if not passed:
                # Extract error lines from output
                for line in (stdout + "\n" + stderr).splitlines():
                    if re.search(r"(FAILED|Error|Exception|fatal|violation)", line, re.IGNORECASE):
                        clean_line = line.strip()
                        if clean_line and clean_line not in errors:
                            errors.append(clean_line)

            return SingleCheckResult(
                category=category,
                command=command,
                passed=passed,
                exit_code=proc.returncode or 0,
                duration_seconds=round(duration, 2),
                stdout=stdout[:20000],
                stderr=stderr[:20000],
                errors_detected=errors[:10],
            )

        except asyncio.TimeoutError:
            duration = time.time() - start_time
            return SingleCheckResult(
                category=category,
                command=command,
                passed=False,
                exit_code=-1,
                duration_seconds=round(duration, 2),
                stdout="",
                stderr=f"Command timed out after {self.timeout}s",
                errors_detected=[f"Command timed out after {self.timeout}s"],
            )
        except Exception as exc:
            duration = time.time() - start_time
            return SingleCheckResult(
                category=category,
                command=command,
                passed=False,
                exit_code=-1,
                duration_seconds=round(duration, 2),
                stdout="",
                stderr=str(exc),
                errors_detected=[str(exc)],
            )

    async def run_suite(self, workspace_path: Path, checks: ChecksConfig) -> VerificationSuiteResult:
        """Run all configured check categories for the repository."""
        suite_passed = True
        all_results: List[SingleCheckResult] = []
        category_summaries: Dict[str, Dict[str, Any]] = {
            "test": {"passed": True},
            "lint": {"passed": True},
            "static_analysis": {"passed": True},
            "security": {"passed": True},
            "build": {"passed": True},
        }
        all_errors: List[str] = []

        categories = [
            ("test", checks.test),
            ("lint", checks.lint),
            ("static_analysis", checks.static_analysis),
            ("security", checks.security),
            ("build", checks.build),
        ]

        for cat_name, commands in categories:
            if not commands:
                continue

            for cmd in commands:
                res = await self.execute_command(workspace_path, cat_name, cmd)
                all_results.append(res)

                if not res.passed:
                    suite_passed = False
                    category_summaries[cat_name]["passed"] = False
                    all_errors.extend(res.errors_detected)
                    if not res.errors_detected:
                        all_errors.append(f"Command '{cmd}' failed with exit code {res.exit_code}")

        return VerificationSuiteResult(
            passed=suite_passed,
            tests=category_summaries["test"],
            lint=category_summaries["lint"],
            static_analysis=category_summaries["static_analysis"],
            security=category_summaries["security"],
            build=category_summaries["build"],
            errors=all_errors[:20],
            results=all_results,
        )
