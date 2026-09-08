"""Coding Agent Abstraction and Repair Contract."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class AgentContext(BaseModel):
    """Context bundle passed into the coding agent."""

    repo_name: str
    pr_number: int
    branch: str
    workspace_path: str
    context_markdown: str
    instruction_files: Dict[str, str] = Field(default_factory=dict)
    run_checks_commands: List[str] = Field(default_factory=list)
    prior_failure_notes: Optional[str] = None


class RepairResult(BaseModel):
    """Structured contract returned by any CodingAgent implementation."""

    status: str = "success"  # success, blocked, failed, timed_out
    summary: str = ""
    files_changed: List[str] = Field(default_factory=list)
    tests_run: List[str] = Field(default_factory=list)
    tests_passed: bool = False
    notes: str = ""
    raw_output: str = ""


class CodingAgent(ABC):
    """Abstract interface for all AI coding agents."""

    @abstractmethod
    async def repair(self, context: AgentContext) -> RepairResult:
        """Diagnose and repair the pull request in the given workspace context."""
        raise NotImplementedError
