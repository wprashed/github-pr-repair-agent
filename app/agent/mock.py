"""Mock Coding Agent for tests and simulations."""

from typing import List, Optional
from app.agent.base import AgentContext, CodingAgent, RepairResult


class MockCodingAgent(CodingAgent):
    """Configurable mock coding agent for testing workflows and edge cases."""

    def __init__(
        self,
        status: str = "success",
        summary: str = "Mock agent successfully repaired the code.",
        files_changed: Optional[List[str]] = None,
        tests_passed: bool = True,
        notes: str = "",
    ):
        self.status = status
        self.summary = summary
        self.files_changed = files_changed or []
        self.tests_passed = tests_passed
        self.notes = notes

    async def repair(self, context: AgentContext) -> RepairResult:
        return RepairResult(
            status=self.status,
            summary=self.summary,
            files_changed=self.files_changed,
            tests_run=context.run_checks_commands,
            tests_passed=self.tests_passed,
            notes=self.notes,
            raw_output="Mock execution completed.",
        )
