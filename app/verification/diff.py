"""Git diff safety inspector, limit enforcer, and secret scanner."""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Set

from app.config.repositories import RepoSafetyConfig
from app.workspace.git import SafeGitService


# Files that must NEVER be added or modified in automated PR repairs
FORBIDDEN_FILE_PATTERNS = [
    re.compile(r"^\.env(\..+)?$", re.IGNORECASE),
    re.compile(r".*\.pem$", re.IGNORECASE),
    re.compile(r".*\.key$", re.IGNORECASE),
    re.compile(r".*id_rsa.*", re.IGNORECASE),
    re.compile(r".*id_ed25519.*", re.IGNORECASE),
    re.compile(r".*credentials\.json$", re.IGNORECASE),
    re.compile(r".*service[-_]account.*\.json$", re.IGNORECASE),
    re.compile(r"^\.github/workflows/.*", re.IGNORECASE),  # Unless explicitly allowed
]

# Sensitive patterns that must NOT be present in added diff lines
SECRET_PATTERNS = [
    (re.compile(r"ghp_[0-9a-zA-Z]{36}"), "GitHub Personal Access Token"),
    (re.compile(r"github_pat_[0-9a-zA-Z_]{82}"), "GitHub Fine-Grained Token"),
    (re.compile(r"AKIA[0-9A-Z]{16}"), "AWS Access Key ID"),
    (re.compile(r"AIza[0-9A-Za-z\-_]{35}"), "Google API Key"),
    (re.compile(r"xox[baprs]-[0-9a-zA-Z]{10,48}"), "Slack Token"),
    (re.compile(r"-----BEGIN (RSA|EC|DSA|OPENSSH) PRIVATE KEY-----"), "Private Key Header"),
]


@dataclass
class DiffSafetyResult:
    passed: bool
    requires_human_review: bool = False
    files_changed_count: int = 0
    lines_added: int = 0
    lines_deleted: int = 0
    forbidden_files: List[str] = field(default_factory=list)
    secrets_detected: List[str] = field(default_factory=list)
    violations: List[str] = field(default_factory=list)
    diff_text: str = ""


class DiffInspector:
    """Inspects workspace diffs to prevent dangerous, oversized, or secret-leaking changes."""

    @classmethod
    async def inspect(
        cls,
        workspace_path: Path,
        safety_config: RepoSafetyConfig,
    ) -> DiffSafetyResult:
        """Run comprehensive diff safety analysis."""
        # 1. Get raw diff and diff stat safely
        code, raw_diff, _ = await SafeGitService.run_git_cmd(workspace_path, ["diff", "HEAD~1"], check=False)
        if code != 0 or not raw_diff:
            code, raw_diff, _ = await SafeGitService.run_git_cmd(workspace_path, ["diff", "HEAD"], check=False)
        if code != 0 or not raw_diff:
            _, raw_diff, _ = await SafeGitService.run_git_cmd(workspace_path, ["diff"], check=False)

        _, status_out, _ = await SafeGitService.run_git_cmd(workspace_path, ["status", "--porcelain"], check=False)

        violations: List[str] = []
        forbidden_files: List[str] = []
        secrets_detected: List[str] = []

        # Parse modified / added / deleted files from git status
        changed_files: List[str] = []
        deleted_files: List[str] = []
        new_files: List[str] = []

        for line in status_out.splitlines():
            if not line.strip():
                continue
            status_code = line[:2].strip()
            filepath = line[3:].strip()
            changed_files.append(filepath)

            if "D" in status_code:
                deleted_files.append(filepath)
            elif "?" in status_code or "A" in status_code:
                new_files.append(filepath)

        # 2. Check forbidden files & workflow changes
        for f in changed_files:
            for pattern in FORBIDDEN_FILE_PATTERNS:
                if pattern.match(f):
                    # Check if workflow changes are allowed
                    if f.startswith(".github/workflows/") and safety_config.allow_workflow_changes:
                        continue
                    forbidden_files.append(f)
                    violations.append(f"Forbidden file modified or created: {f}")

        # 3. Check deleted files policy
        if deleted_files and not safety_config.allow_deleted_files:
            violations.append(f"File deletion not allowed: {', '.join(deleted_files)}")

        # 4. Check new files policy
        if new_files and not safety_config.allow_new_files:
            violations.append(f"Creation of new files not allowed: {', '.join(new_files)}")

        # 5. Check diff size metrics
        code, numstat_out, _ = await SafeGitService.run_git_cmd(workspace_path, ["diff", "--numstat", "HEAD"], check=False)
        if code != 0:
            _, numstat_out, _ = await SafeGitService.run_git_cmd(workspace_path, ["diff", "--numstat"], check=False)
        lines_added = 0
        lines_deleted = 0
        file_count = len(changed_files)

        for line in numstat_out.splitlines():
            parts = line.split()
            if len(parts) >= 3:
                added = int(parts[0]) if parts[0].isdigit() else 0
                deleted = int(parts[1]) if parts[1].isdigit() else 0
                lines_added += added
                lines_deleted += deleted

        if file_count > safety_config.max_files_changed:
            violations.append(
                f"Too many files changed ({file_count} > {safety_config.max_files_changed})"
            )

        if lines_added > safety_config.max_lines_added:
            violations.append(
                f"Too many lines added ({lines_added} > {safety_config.max_lines_added})"
            )

        if lines_deleted > safety_config.max_lines_deleted:
            violations.append(
                f"Too many lines deleted ({lines_deleted} > {safety_config.max_lines_deleted})"
            )

        # 6. Secret scanning on added lines
        for line in raw_diff.splitlines():
            if line.startswith("+") and not line.startswith("+++"):
                added_content = line[1:]
                for pattern, desc in SECRET_PATTERNS:
                    if pattern.search(added_content):
                        secrets_detected.append(desc)
                        violations.append(f"Potential secret detected: {desc}")

        requires_human = len(violations) > 0 or len(secrets_detected) > 0 or len(forbidden_files) > 0

        return DiffSafetyResult(
            passed=not requires_human,
            requires_human_review=requires_human,
            files_changed_count=file_count,
            lines_added=lines_added,
            lines_deleted=lines_deleted,
            forbidden_files=forbidden_files,
            secrets_detected=secrets_detected,
            violations=violations,
            diff_text=raw_diff[:50000],  # cap stored diff size
        )
