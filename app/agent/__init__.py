"""Coding agent module."""

from app.agent.base import AgentContext, CodingAgent, RepairResult
from app.agent.antigravity import AntigravityAgent
from app.agent.mock import MockCodingAgent
from app.agent.context import PRContextBuilder
from app.agent.prompts import SURGICAL_REPAIR_SYSTEM_PROMPT, generate_repair_user_prompt

__all__ = [
    "AgentContext",
    "CodingAgent",
    "RepairResult",
    "AntigravityAgent",
    "MockCodingAgent",
    "PRContextBuilder",
    "SURGICAL_REPAIR_SYSTEM_PROMPT",
    "generate_repair_user_prompt",
]
