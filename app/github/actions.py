"""GitHub Actions workflow runs, jobs, and intelligent log parser."""

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from app.github.client import GitHubClient


@dataclass
class FailedStepInfo:
    name: str
    number: int
    conclusion: str
    log_snippet: Optional[str] = None


@dataclass
class FailedJobInfo:
    id: int
    name: str
    workflow_name: str
    html_url: str
    failed_steps: List[FailedStepInfo] = field(default_factory=list)
    extracted_errors: List[str] = field(default_factory=list)
    stack_traces: List[str] = field(default_factory=list)
    file_references: List[str] = field(default_factory=list)


class ActionService:
    """Service to inspect GitHub Actions and parse CI failures."""

    def __init__(self, client: GitHubClient):
        self.client = client

    async def get_failed_jobs_for_branch(self, repo: str, branch: str, head_sha: Optional[str] = None) -> List[FailedJobInfo]:
        """Fetch failed workflow runs and jobs for a specific branch."""
        params = {"branch": branch, "per_page": 10}
        runs_data = await self.client.get(f"/repos/{repo}/actions/runs", params=params)
        workflow_runs = runs_data.get("workflow_runs", [])

        failed_jobs_list: List[FailedJobInfo] = []

        for run in workflow_runs:
            # Match commit SHA if provided
            if head_sha and run.get("head_sha") != head_sha:
                continue

            if run.get("conclusion") not in ("failure", "timed_out", "action_required"):
                continue

            run_id = run["id"]
            workflow_name = run.get("name", "CI")
            jobs_data = await self.client.get(f"/repos/{repo}/actions/runs/{run_id}/jobs")

            for job in jobs_data.get("jobs", []):
                if job.get("conclusion") in ("failure", "timed_out"):
                    failed_steps = []
                    for step in job.get("steps", []):
                        if step.get("conclusion") in ("failure", "timed_out"):
                            failed_steps.append(
                                FailedStepInfo(
                                    name=step.get("name", "Unknown step"),
                                    number=step.get("number", 0),
                                    conclusion=step.get("conclusion", "failure"),
                                )
                            )

                    failed_job = FailedJobInfo(
                        id=job["id"],
                        name=job.get("name", ""),
                        workflow_name=workflow_name,
                        html_url=job.get("html_url", ""),
                        failed_steps=failed_steps,
                    )
                    failed_jobs_list.append(failed_job)

        return failed_jobs_list

    @staticmethod
    def parse_ci_log(raw_log: str, max_chars: int = 5000) -> Dict[str, Any]:
        """Intelligently parse raw CI logs to extract errors, stack traces, and file paths.

        Avoids passing massive unformatted logs to the AI.
        """
        lines = raw_log.splitlines()
        extracted_errors: List[str] = []
        stack_traces: List[str] = []
        file_references: List[str] = []

        error_patterns = [
            re.compile(r"(error|fatal|fail|failed|failure):.*", re.IGNORECASE),
            re.compile(r"(AssertionError|TypeError|ValueError|KeyError|AttributeError|Exception):.*"),
            re.compile(r"FAILED\s+([a-zA-Z0-9_./\-]+::[a-zA-Z0-9_]+)"),
            re.compile(r"([a-zA-Z0-9_./\-]+\.php:[0-9]+)"),
            re.compile(r"([a-zA-Z0-9_./\-]+\.py:[0-9]+)"),
            re.compile(r"([a-zA-Z0-9_./\-]+\.ts:[0-9]+)"),
            re.compile(r"([a-zA-Z0-9_./\-]+\.js:[0-9]+)"),
        ]

        in_traceback = False
        current_trace: List[str] = []

        py_file_pattern = re.compile(r'File "([^"]+)", line ([0-9]+)')

        for line in lines:
            line_str = line.strip()

            py_file_match = py_file_pattern.search(line_str)
            if py_file_match:
                ref = f"{py_file_match.group(1)}:{py_file_match.group(2)}"
                if ref not in file_references:
                    file_references.append(ref)

            # Track python / standard stack traces
            if "Traceback (most recent call last):" in line:
                in_traceback = True
                current_trace = [line.strip()]
                continue

            if in_traceback:
                current_trace.append(line.strip())
                if any(err_kw in line for err_kw in ("Error", "Exception", "Fault")):
                    stack_traces.append("\n".join(current_trace))
                    in_traceback = False
                    current_trace = []
                elif len(current_trace) > 40:
                    stack_traces.append("\n".join(current_trace))
                    in_traceback = False
                    current_trace = []

            for pattern in error_patterns:
                match = pattern.search(line_str)
                if match:
                    matched_text = match.group(0)
                    if any(ext in matched_text for ext in (".py:", ".php:", ".ts:", ".js:")):
                        if matched_text not in file_references:
                            file_references.append(matched_text)
                    else:
                        if matched_text not in extracted_errors:
                            extracted_errors.append(matched_text)

        # Truncate summary if needed
        combined_summary = "\n".join(extracted_errors[:20])
        if len(combined_summary) > max_chars:
            combined_summary = combined_summary[:max_chars] + "... [truncated]"

        return {
            "errors": extracted_errors[:20],
            "stack_traces": stack_traces[:5],
            "file_references": file_references[:15],
            "summary": combined_summary,
        }
