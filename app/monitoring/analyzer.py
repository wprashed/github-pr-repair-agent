"""Analyzer for review comments grouping and failure diagnostics."""

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from app.github.comments import ReviewComment, IssueComment
from app.github.reviews import ReviewItem
from app.github.checks import CheckRunItem
from app.github.actions import FailedJobInfo


@dataclass
class GroupedReviewFeedback:
    file_path: str
    comments: List[ReviewComment] = field(default_factory=list)
    combined_notes: str = ""


@dataclass
class ActionableDiagnosis:
    has_actionable_issues: bool
    reasons: List[str] = field(default_factory=list)
    reviews_requesting_changes: List[ReviewItem] = field(default_factory=list)
    grouped_comments_by_file: Dict[str, GroupedReviewFeedback] = field(default_factory=dict)
    general_comments: List[IssueComment] = field(default_factory=list)
    failed_checks: List[CheckRunItem] = field(default_factory=list)
    failed_ci_jobs: List[FailedJobInfo] = field(default_factory=list)


class FeedbackAnalyzer:
    """Intelligently groups review comments and diagnoses failures."""

    @staticmethod
    def group_review_comments(comments: List[ReviewComment]) -> Dict[str, GroupedReviewFeedback]:
        """Group related review comments by file to avoid isolated, fragmented edits."""
        grouped: Dict[str, GroupedReviewFeedback] = defaultdict(
            lambda: GroupedReviewFeedback(file_path="")
        )

        for c in comments:
            path = c.path or "general"
            if not grouped[path].file_path:
                grouped[path].file_path = path
            grouped[path].comments.append(c)

        # Build combined notes per file
        for path, group in grouped.items():
            notes = []
            for c in group.comments:
                line_str = f"Line {c.line}: " if c.line else ""
                notes.append(f"- [{c.user}] {line_str}{c.body}")
            group.combined_notes = "\n".join(notes)

        return dict(grouped)

    @classmethod
    def diagnose(
        cls,
        reviews: List[ReviewItem],
        review_comments: List[ReviewComment],
        issue_comments: List[IssueComment],
        failed_checks: List[CheckRunItem],
        failed_jobs: List[FailedJobInfo],
    ) -> ActionableDiagnosis:
        """Synthesize all feedback and checks to decide if action is needed."""
        reasons = []

        changes_requested = [r for r in reviews if r.state == "CHANGES_REQUESTED"]
        if changes_requested:
            reasons.append(f"{len(changes_requested)} review(s) requested changes")

        if review_comments:
            reasons.append(f"{len(review_comments)} new review comment(s) received")

        if failed_checks:
            reasons.append(f"{len(failed_checks)} check run(s) failed")

        if failed_jobs:
            reasons.append(f"{len(failed_jobs)} CI workflow job(s) failed")

        grouped_comments = cls.group_review_comments(review_comments)

        return ActionableDiagnosis(
            has_actionable_issues=len(reasons) > 0,
            reasons=reasons,
            reviews_requesting_changes=changes_requested,
            grouped_comments_by_file=grouped_comments,
            general_comments=issue_comments,
            failed_checks=failed_checks,
            failed_ci_jobs=failed_jobs,
        )
