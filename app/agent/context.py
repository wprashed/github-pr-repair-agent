"""PR Context Builder - compiles full context into workspace/pr-context.md."""

from pathlib import Path
from typing import Any, Dict, List, Optional
from app.github.prs import PullRequestDetail, ChangedFile
from app.github.reviews import ReviewItem
from app.github.comments import ReviewComment, IssueComment
from app.github.checks import CheckRunItem
from app.github.actions import FailedJobInfo


class PRContextBuilder:
    """Builds a comprehensive, structured markdown context for the coding agent."""

    INSTRUCTION_FILENAMES = [
        "AGENTS.md",
        "CLAUDE.md",
        ".claude.md",
        "CONTRIBUTING.md",
        "README.md",
        ".github/copilot-instructions.md",
        "CODING_STANDARDS.md",
    ]

    @classmethod
    def read_repo_instructions(cls, workspace_path: Path) -> Dict[str, str]:
        """Discover and read repository instructions from the checked-out workspace."""
        instructions: Dict[str, str] = {}
        for filename in cls.INSTRUCTION_FILENAMES:
            file_path = workspace_path / filename
            if file_path.is_file():
                try:
                    content = file_path.read_text(encoding="utf-8", errors="replace")
                    # Limit each instruction file to 4000 characters to keep prompt focused
                    if len(content) > 4000:
                        content = content[:4000] + "\n... [truncated for brevity]"
                    instructions[filename] = content
                except Exception:
                    pass
        return instructions

    @classmethod
    def build_context_markdown(
        cls,
        pr: PullRequestDetail,
        changed_files: List[ChangedFile],
        reviews: List[ReviewItem],
        review_comments: List[ReviewComment],
        issue_comments: List[IssueComment],
        failed_checks: List[CheckRunItem],
        failed_jobs: List[FailedJobInfo],
        repo_instructions: Dict[str, str],
        prior_error_feedback: Optional[str] = None,
    ) -> str:
        """Render complete structured markdown."""
        lines: List[str] = []

        lines.append(f"# Pull Request Repair Context: {pr.head_repo} #{pr.number}")
        lines.append("")
        lines.append(f"- **Repository**: `{pr.head_repo}`")
        lines.append(f"- **PR Number**: `#{pr.number}`")
        lines.append(f"- **Title**: {pr.title}")
        lines.append(f"- **Author**: @{pr.author}")
        lines.append(f"- **Source Branch**: `{pr.head_branch}`")
        lines.append(f"- **Target Branch**: `{pr.base_branch}`")
        lines.append(f"- **Head Commit**: `{pr.head_sha}`")
        lines.append("")

        # Description
        lines.append("## PR Description")
        lines.append(pr.body.strip() if pr.body else "_No description provided._")
        lines.append("")

        # Prior repair attempt failure (if retrying)
        if prior_error_feedback:
            lines.append("## Prior Repair Attempt Feedback (Action Required)")
            lines.append("> [!WARNING]")
            lines.append("> The previous attempt failed verification with the following error:")
            lines.append("```text")
            lines.append(prior_error_feedback)
            lines.append("```")
            lines.append("")

        # Requested Changes / Reviews
        changes_requested = [r for r in reviews if r.state == "CHANGES_REQUESTED"]
        if changes_requested:
            lines.append("## Requested Changes")
            for r in changes_requested:
                lines.append(f"### Review by @{r.user}")
                lines.append(r.body.strip() if r.body else "_No review summary text provided._")
                lines.append("")

        # Inline Review Comments
        if review_comments:
            lines.append("## Inline Code Review Comments")
            for c in review_comments:
                loc = f"`{c.path}`"
                if c.line:
                    loc += f" (Line {c.line})"
                lines.append(f"- **{loc}** by @{c.user}:")
                if c.diff_hunk:
                    lines.append("  ```diff")
                    for dh_line in c.diff_hunk.splitlines()[-6:]:
                        lines.append(f"  {dh_line}")
                    lines.append("  ```")
                lines.append(f"  > {c.body.strip()}")
                lines.append("")

        # General PR Issue Comments
        if issue_comments:
            lines.append("## Recent PR Discussion Comments")
            for ic in issue_comments[-5:]:  # show up to 5 most recent
                lines.append(f"- **@{ic.user}** ({ic.created_at}): {ic.body.strip()}")
            lines.append("")

        # CI / Check Run Failures
        if failed_checks or failed_jobs:
            lines.append("## CI & Check Failures")
            for chk in failed_checks:
                lines.append(f"### Check: `{chk.name}` (Conclusion: {chk.conclusion})")
                if chk.title or chk.summary:
                    lines.append(f"**{chk.title or ''}**")
                    lines.append(chk.summary or "")
                if chk.text:
                    lines.append("```text")
                    lines.append(chk.text[:2000])
                    lines.append("```")
                lines.append("")

            for job in failed_jobs:
                lines.append(f"### Workflow Job: `{job.workflow_name} / {job.name}`")
                for st in job.failed_steps:
                    lines.append(f"- Failed step: `{st.name}` (Step {st.number})")
                if job.extracted_errors:
                    lines.append("Extracted errors:")
                    lines.append("```text")
                    lines.append("\n".join(job.extracted_errors[:10]))
                    lines.append("```")
                if job.stack_traces:
                    lines.append("Stack traces:")
                    lines.append("```text")
                    lines.append("\n\n".join(job.stack_traces[:3]))
                    lines.append("```")
                lines.append("")

        # Changed Files in PR
        lines.append("## PR Changed Files")
        for f in changed_files:
            lines.append(f"- `{f.filename}` (+{f.additions}, -{f.deletions}) [{f.status}]")
        lines.append("")

        # Repository Instructions
        if repo_instructions:
            lines.append("## Repository Instructions")
            for fname, content in repo_instructions.items():
                lines.append(f"### `{fname}`")
                lines.append("```markdown")
                lines.append(content)
                lines.append("```")
                lines.append("")

        return "\n".join(lines)

    @classmethod
    def write_to_workspace(cls, workspace_path: Path, content: str) -> Path:
        """Write the generated context markdown to workspace/pr-context.md."""
        target = workspace_path / "pr-context.md"
        target.write_text(content, encoding="utf-8")
        return target
