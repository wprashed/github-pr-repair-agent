"""Antigravity Coding Agent Implementation."""

import asyncio
import json
import logging
import os
import re
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.agent.base import AgentContext, CodingAgent, RepairResult
from app.agent.prompts import SURGICAL_REPAIR_SYSTEM_PROMPT, generate_repair_user_prompt
from app.config.settings import settings

logger = logging.getLogger(__name__)


class AntigravityAgent(CodingAgent):
    """Coding agent that interfaces with Google Antigravity via agentapi CLI or Python SDK."""

    def __init__(
        self,
        bin_path: Optional[str] = None,
        model: Optional[str] = None,
        timeout_seconds: Optional[int] = None,
    ):
        self.bin_path = bin_path or settings.ANTIGRAVITY_BIN_PATH
        self.model = model or settings.ANTIGRAVITY_MODEL
        self.timeout_seconds = timeout_seconds or settings.AGENT_TIMEOUT_SECONDS

        # Auto-detect if bin_path is not executable or not found
        if not shutil.which(self.bin_path) and not Path(self.bin_path).is_file():
            discovered = shutil.which("agentapi")
            if discovered:
                self.bin_path = discovered
            else:
                logger.warning("Antigravity agentapi binary not found at %s. Will fallback if needed.", self.bin_path)

    async def repair(self, context: AgentContext) -> RepairResult:
        """Execute surgical repair via Antigravity in the workspace directory."""
        workspace_dir = Path(context.workspace_path)
        if not workspace_dir.is_dir():
            return RepairResult(
                status="failed",
                summary="Workspace directory does not exist",
                notes=f"Invalid workspace path: {context.workspace_path}",
            )

        # 1. Prepare prompt
        user_prompt = generate_repair_user_prompt(context.context_markdown)
        full_instructions = f"{SURGICAL_REPAIR_SYSTEM_PROMPT}\n\n{user_prompt}"

        # 2. Check if agentapi exists and is executable
        if shutil.which(self.bin_path) or Path(self.bin_path).is_file():
            return await self._run_via_agentapi(context, full_instructions)
        else:
            return await self._run_fallback(context, full_instructions)

    async def _run_via_agentapi(self, context: AgentContext, prompt: str) -> RepairResult:
        """Invoke Antigravity via agentapi new-conversation."""
        cmd = [
            self.bin_path,
            "new-conversation",
            f"--model={self.model}",
            f"--title=Repair {context.repo_name} PR #{context.pr_number}",
            prompt,
        ]

        logger.info(
            "Invoking Antigravity agentapi for %s #%d in %s (timeout: %ds)",
            context.repo_name,
            context.pr_number,
            context.workspace_path,
            self.timeout_seconds,
        )

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=context.workspace_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(),
                timeout=float(self.timeout_seconds),
            )

            stdout_text = stdout_bytes.decode("utf-8", errors="replace")
            stderr_text = stderr_bytes.decode("utf-8", errors="replace")

            if process.returncode != 0:
                logger.error("agentapi returned code %d: %s", process.returncode, stderr_text)
                return RepairResult(
                    status="failed",
                    summary=f"Antigravity process failed with code {process.returncode}",
                    notes=stderr_text or stdout_text,
                    raw_output=f"{stdout_text}\n{stderr_text}",
                )

            return self._parse_agent_output(stdout_text, stderr_text)

        except asyncio.TimeoutError:
            logger.error("Antigravity repair timed out after %d seconds", self.timeout_seconds)
            return RepairResult(
                status="timed_out",
                summary=f"Agent timed out after {self.timeout_seconds}s",
                notes="Agent operation exceeded timeout limit.",
            )
        except Exception as exc:
            logger.exception("Error running Antigravity agent: %s", exc)
            return RepairResult(
                status="failed",
                summary=f"Execution error: {exc}",
                notes=str(exc),
            )

    async def _run_fallback(self, context: AgentContext, prompt: str) -> RepairResult:
        """Fallback mode when binary is not installed in the environment."""
        logger.warning("Antigravity binary not found. Running in headless adapter mode.")
        return RepairResult(
            status="blocked",
            summary="Antigravity agentapi binary not found in environment",
            notes=f"Binary path '{self.bin_path}' is not accessible.",
        )

    def _parse_agent_output(self, stdout: str, stderr: str) -> RepairResult:
        """Parse structured result from the agent response."""
        full_output = f"{stdout}\n{stderr}".strip()

        # Check for JSON block in output
        json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", full_output, re.DOTALL)
        if json_match:
            try:
                data = json.loads(json_match.group(1))
                return RepairResult(
                    status=data.get("status", "success"),
                    summary=data.get("summary", "Agent completed repair."),
                    files_changed=data.get("files_changed", []),
                    tests_run=data.get("tests_run", []),
                    tests_passed=data.get("tests_passed", False),
                    notes=data.get("notes", ""),
                    raw_output=full_output,
                )
            except Exception:
                pass

        # Fallback text parsing
        status = "success"
        if "error" in stdout.lower() and "fixed" not in stdout.lower():
            status = "failed"

        summary = stdout.strip()[:500] if stdout.strip() else "Agent completed execution."
        return RepairResult(
            status=status,
            summary=summary,
            notes="",
            raw_output=full_output,
        )
