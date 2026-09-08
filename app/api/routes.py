"""FastAPI REST API routes for dashboard, metrics, and actions."""

import asyncio
from datetime import datetime
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel

import logging
import os
import shutil
import subprocess
from pathlib import Path

from app.config.repositories import (
    load_repositories_config,
    save_repositories_config,
    add_or_update_repository,
    remove_repository,
    RepositoryConfig,
)
from app.config.settings import settings, update_env_settings
from app.database.database import get_async_session
from app.database.models import Event, PRStatus, PullRequest, RepairAttempt, Repository
from app.github.auth import GitHubAuthService, AntigravityAuthService
from app.github.client import GitHubClient, GitHubAPIError
from app.monitoring.scanner import PRRepairScanner
from app.monitoring.state import transition_pr_status
from app.scheduler.scheduler import scheduler_service
from app.workspace.git import SafeGitService

logger = logging.getLogger(__name__)
from app.workspace.manager import WorkspaceManager

router = APIRouter(prefix="/api")


class ApprovalRequest(BaseModel):
    notes: Optional[str] = None


class RepositoryAddRequest(BaseModel):
    github: str
    name: Optional[str] = None
    enabled: Optional[bool] = True
    only_my_prs: Optional[bool] = None


@router.get("/health")
async def health_check():
    return {
        "status": "ok",
        "timestamp": datetime.utcnow().isoformat(),
        "mode": settings.AUTOMATION_MODE,
        "repairs_enabled": settings.REPAIRS_ENABLED,
        "agent_provider": settings.AGENT_PROVIDER,
    }


@router.get("/status")
async def get_system_status(session: AsyncSession = Depends(get_async_session)):
    """Overview metrics for the dashboard."""
    total_prs = await session.scalar(select(func.count(PullRequest.id))) or 0
    needs_attention = await session.scalar(
        select(func.count(PullRequest.id)).where(
            PullRequest.status.in_([PRStatus.NEEDS_REPAIR, PRStatus.REQUIRES_HUMAN_REVIEW])
        )
    ) or 0
    currently_repairing = await session.scalar(
        select(func.count(PullRequest.id)).where(
            PullRequest.status.in_([PRStatus.PREPARING, PRStatus.AGENT_RUNNING, PRStatus.VERIFYING, PRStatus.PUSHING])
        )
    ) or 0
    successfully_repaired = await session.scalar(
        select(func.count(RepairAttempt.id)).where(RepairAttempt.status == "SUCCESS")
    ) or 0
    failed_repairs = await session.scalar(
        select(func.count(RepairAttempt.id)).where(RepairAttempt.status == "FAILED")
    ) or 0

    return {
        "total_prs": total_prs,
        "needs_attention": needs_attention,
        "currently_repairing": currently_repairing,
        "successfully_repaired": successfully_repaired,
        "failed_repairs": failed_repairs,
        "scheduler_running": scheduler_service._is_running,
        "automation_mode": settings.AUTOMATION_MODE,
        "repairs_enabled": settings.REPAIRS_ENABLED,
    }


@router.get("/repositories")
async def list_repositories(session: AsyncSession = Depends(get_async_session)):
    """Return monitored repositories from config and DB stats."""
    cfg_repos = load_repositories_config(settings.CONFIG_PATH)

    stmt = select(Repository).order_by(Repository.name)
    db_repos = (await session.execute(stmt)).scalars().all()
    db_map = {r.github_full_name.lower(): r for r in db_repos}

    # Count PRs per repo
    pr_rows = (await session.execute(
        select(PullRequest.repo_full_name, func.count(PullRequest.id)).group_by(PullRequest.repo_full_name)
    )).all()
    pr_counts = {r[0].lower(): r[1] for r in pr_rows}

    results = []
    seen = set()
    for r in cfg_repos:
        key = r.github.lower()
        seen.add(key)
        db_rec = db_map.get(key)
        results.append({
            "id": db_rec.id if db_rec else None,
            "name": r.name,
            "github": r.github,
            "enabled": r.enabled,
            "only_my_prs": r.pull_requests.only_my_prs,
            "pr_count": pr_counts.get(key, 0),
            "last_scanned_at": db_rec.last_scanned_at.isoformat() if db_rec and db_rec.last_scanned_at else None,
        })

    # Include any DB repos not in config
    for db_rec in db_repos:
        if db_rec.github_full_name.lower() not in seen:
            results.append({
                "id": db_rec.id,
                "name": db_rec.name,
                "github": db_rec.github_full_name,
                "enabled": db_rec.enabled,
                "only_my_prs": True,
                "pr_count": pr_counts.get(db_rec.github_full_name.lower(), 0),
                "last_scanned_at": db_rec.last_scanned_at.isoformat() if db_rec.last_scanned_at else None,
            })

    return results


@router.post("/repositories")
async def add_repository(req: RepositoryAddRequest):
    """Add a new repository to monitor and trigger an immediate scan."""
    clean_github = req.github.strip().strip("/")
    if "/" not in clean_github:
        raise HTTPException(status_code=400, detail="Repository must be in 'owner/repo' format.")

    if settings.is_repo_blocked(clean_github):
        raise HTTPException(status_code=400, detail=f"Repository '{clean_github}' is in the blocked organizations list and cannot be added.")

    # Validate with GitHub API if token available
    repo_name = req.name or clean_github.split("/")[1]
    if settings.GITHUB_TOKEN:
        try:
            async with GitHubClient() as client:
                repo_info = await client.get(f"/repos/{clean_github}")
                repo_name = req.name or repo_info.get("name", clean_github.split("/")[1])
        except Exception as e:
            logger.warning("Could not verify repo %s on GitHub: %s", clean_github, e)

    owner = clean_github.split("/")[0]
    is_my_repo = (owner.lower() == (settings.GITHUB_USERNAME or "").lower())
    only_my_prs = req.only_my_prs if req.only_my_prs is not None else (not is_my_repo)

    cfg = RepositoryConfig(
        name=repo_name,
        github=clean_github,
        enabled=req.enabled if req.enabled is not None else True,
    )
    cfg.pull_requests.only_my_prs = only_my_prs
    add_or_update_repository(settings.CONFIG_PATH, cfg)

    # Scan immediately in background
    scanner = PRRepairScanner()
    asyncio.create_task(scanner.scan_repository(cfg))

    return {"success": True, "message": f"Added repository {clean_github} to monitored repositories."}


@router.delete("/repositories/{owner}/{repo}")
async def delete_repository(owner: str, repo: str):
    """Remove a repository from monitoring configuration."""
    full_name = f"{owner}/{repo}"
    remove_repository(settings.CONFIG_PATH, full_name)
    return {"success": True, "message": f"Removed repository {full_name}"}


@router.post("/repositories/{owner}/{repo}/toggle")
async def toggle_repository(owner: str, repo: str):
    """Toggle repository enabled state."""
    full_name = f"{owner}/{repo}"
    repos = load_repositories_config(settings.CONFIG_PATH)
    found = False
    new_state = False
    for r in repos:
        if r.github.lower() == full_name.lower():
            r.enabled = not r.enabled
            new_state = r.enabled
            found = True
            break
    if not found:
        raise HTTPException(status_code=404, detail="Repository not found in configuration")

    save_repositories_config(settings.CONFIG_PATH, repos)
    return {"success": True, "enabled": new_state, "message": f"Repository {full_name} is now {'enabled' if new_state else 'disabled'}."}


@router.get("/github/user/repositories")
async def list_github_user_repositories():
    """List authenticated user's repositories from GitHub API."""
    if not settings.GITHUB_TOKEN:
        raise HTTPException(status_code=400, detail="GitHub token not configured.")

    cfg_repos = {r.github.lower() for r in load_repositories_config(settings.CONFIG_PATH)}

    try:
        async with GitHubClient() as client:
            repos_raw = await client.get("/user/repos", params={"per_page": 100, "sort": "updated", "affiliation": "owner,collaborator"})
            return [
                {
                    "id": r.get("id"),
                    "name": r.get("name"),
                    "full_name": r.get("full_name"),
                    "private": r.get("private"),
                    "open_issues_count": r.get("open_issues_count", 0),
                    "description": r.get("description"),
                    "html_url": r.get("html_url"),
                    "is_monitored": r.get("full_name", "").lower() in cfg_repos,
                }
                for r in repos_raw
                if not settings.is_repo_blocked(r.get("full_name"))
            ]
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to fetch user repositories from GitHub: {exc}")


@router.post("/github/auto-discover")
async def auto_discover_github_prs():
    """Automatically discover all open PRs created by or involving the user and monitor their repositories."""
    if not settings.GITHUB_TOKEN:
        raise HTTPException(status_code=400, detail="GitHub token not configured.")

    username = settings.GITHUB_USERNAME
    if not username:
        raise HTTPException(status_code=400, detail="GitHub username not configured.")

    discovered_repos = set()
    open_prs_count = 0

    try:
        async with GitHubClient() as client:
            # 1. Search PRs authored by user across all of GitHub
            res_authored = await client.get("/search/issues", params={"q": f"is:pr is:open author:{username}", "per_page": 100})
            items_authored = res_authored.get("items", [])
            for item in items_authored:
                repo_url = item.get("repository_url", "")
                if "/repos/" in repo_url:
                    rep_name = repo_url.split("/repos/")[1]
                    if not settings.is_repo_blocked(rep_name):
                        discovered_repos.add(rep_name)
                        open_prs_count += 1

            # 2. Search PRs in user's own repositories
            res_user = await client.get("/search/issues", params={"q": f"is:pr is:open user:{username}", "per_page": 100})
            items_user = res_user.get("items", [])
            for item in items_user:
                repo_url = item.get("repository_url", "")
                if "/repos/" in repo_url:
                    rep_name = repo_url.split("/repos/")[1]
                    if not settings.is_repo_blocked(rep_name):
                        discovered_repos.add(rep_name)

    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"GitHub Search API failed: {exc}")

    existing_repos = {r.github.lower(): r for r in load_repositories_config(settings.CONFIG_PATH)}
    added_names = []

    for full_name in sorted(discovered_repos):
        if settings.is_repo_blocked(full_name):
            continue
        if full_name.lower() not in existing_repos:
            owner, repo_name = full_name.split("/", 1)
            is_my_repo = (owner.lower() == username.lower())
            cfg = RepositoryConfig(
                name=repo_name,
                github=full_name,
                enabled=True,
            )
            cfg.pull_requests.only_my_prs = not is_my_repo
            add_or_update_repository(settings.CONFIG_PATH, cfg)
            added_names.append(full_name)

    # Trigger scan immediately in background
    scanner = PRRepairScanner()
    asyncio.create_task(scanner.scan_all_repositories())

    return {
        "success": True,
        "discovered_prs_count": open_prs_count,
        "total_repositories": len(discovered_repos),
        "newly_added": added_names,
        "all_discovered_repositories": sorted(list(discovered_repos)),
        "message": f"Discovered {open_prs_count} open PR(s) across {len(discovered_repos)} repositories. Added {len(added_names)} new repository/repositories.",
    }


@router.get("/pull-requests")
async def list_pull_requests(
    status: Optional[str] = Query(None),
    repo: Optional[str] = Query(None),
    session: AsyncSession = Depends(get_async_session),
):
    query = select(PullRequest).order_by(desc(PullRequest.updated_at))
    if status:
        query = query.where(PullRequest.status == status)
    if repo:
        query = query.where(PullRequest.repo_full_name == repo)

    res = await session.execute(query)
    prs = res.scalars().all()

    return [
        {
            "id": pr.id,
            "repository": pr.repo_full_name,
            "pr_number": pr.pr_number,
            "url": f"https://github.com/{pr.repo_full_name}/pull/{pr.pr_number}",
            "title": pr.title,
            "author": pr.author,
            "branch": pr.branch,
            "status": pr.status,
            "repair_attempts": pr.repair_attempts,
            "max_attempts": pr.max_attempts,
            "human_approval_required": pr.human_approval_required,
            "last_scan_time": pr.last_scan_time.isoformat() if pr.last_scan_time else None,
            "last_repair_time": pr.last_repair_time.isoformat() if pr.last_repair_time else None,
            "updated_at": pr.updated_at.isoformat() if pr.updated_at else None,
        }
        for pr in prs
    ]


@router.get("/pull-requests/{pr_id}")
async def get_pull_request_detail(pr_id: int, session: AsyncSession = Depends(get_async_session)):
    stmt = select(PullRequest).where(PullRequest.id == pr_id)
    pr = (await session.execute(stmt)).scalar_one_or_none()
    if not pr:
        raise HTTPException(status_code=404, detail="Pull request not found")

    return {
        "id": pr.id,
        "repository": pr.repo_full_name,
        "pr_number": pr.pr_number,
        "url": f"https://github.com/{pr.repo_full_name}/pull/{pr.pr_number}",
        "title": pr.title,
        "author": pr.author,
        "branch": pr.branch,
        "base_branch": pr.base_branch,
        "last_commit_sha": pr.last_commit_sha,
        "status": pr.status,
        "repair_attempts": pr.repair_attempts,
        "max_attempts": pr.max_attempts,
        "human_approval_required": pr.human_approval_required,
        "latest_diff": pr.latest_diff,
        "last_scan_time": pr.last_scan_time.isoformat() if pr.last_scan_time else None,
        "last_repair_time": pr.last_repair_time.isoformat() if pr.last_repair_time else None,
        "updated_at": pr.updated_at.isoformat() if pr.updated_at else None,
    }


@router.get("/pull-requests/{pr_id}/events")
async def get_pr_events(pr_id: int, session: AsyncSession = Depends(get_async_session)):
    stmt = select(Event).where(Event.pull_request_id == pr_id).order_by(desc(Event.created_at))
    events = (await session.execute(stmt)).scalars().all()
    return [
        {
            "id": e.id,
            "event_type": e.event_type,
            "external_id": e.external_id,
            "status": e.status,
            "created_at": e.created_at.isoformat() if e.created_at else None,
        }
        for e in events
    ]


@router.get("/pull-requests/{pr_id}/repairs")
async def get_pr_repairs(pr_id: int, session: AsyncSession = Depends(get_async_session)):
    stmt = select(RepairAttempt).where(RepairAttempt.pull_request_id == pr_id).order_by(desc(RepairAttempt.started_at))
    repairs = (await session.execute(stmt)).scalars().all()
    return [
        {
            "id": r.id,
            "attempt_number": r.attempt_number,
            "trigger_reason": r.trigger_reason,
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "finished_at": r.finished_at.isoformat() if r.finished_at else None,
            "status": r.status,
            "files_changed_count": r.files_changed_count,
            "tests_passed": r.tests_passed,
            "diff_safety_passed": r.diff_safety_passed,
            "commit_sha": r.commit_sha,
            "error_message": r.error_message,
        }
        for r in repairs
    ]


@router.post("/scan")
async def trigger_scan():
    """Trigger an immediate scan across all configured repositories."""
    await scheduler_service.trigger_manual_scan()
    return {"message": "Scan triggered successfully"}


@router.post("/pull-requests/{pr_id}/repair")
async def trigger_pr_repair(pr_id: int, session: AsyncSession = Depends(get_async_session)):
    """Force-trigger repair attempt for a PR."""
    if not settings.REPAIRS_ENABLED:
        raise HTTPException(status_code=403, detail="PR repairs are disabled.")

    stmt = select(PullRequest).where(PullRequest.id == pr_id)
    pr = (await session.execute(stmt)).scalar_one_or_none()
    if not pr:
        raise HTTPException(status_code=404, detail="Pull request not found")

    if settings.is_repo_blocked(pr.repo_full_name):
        raise HTTPException(status_code=400, detail=f"Repairs for repository '{pr.repo_full_name}' are disabled in blocked organizations.")

    pr.status = PRStatus.NEEDS_REPAIR
    await session.commit()

    # Trigger scan asynchronously
    scanner = PRRepairScanner()
    asyncio.create_task(scanner.scan_all_repositories())
    return {"message": f"Repair queued for PR #{pr.pr_number}"}


@router.post("/pull-requests/{pr_id}/approve")
async def approve_pr_repair(
    pr_id: int,
    req: Optional[ApprovalRequest] = None,
    session: AsyncSession = Depends(get_async_session),
):
    """Human approval to push a verified repair that was held in REQUIRES_HUMAN_REVIEW."""
    stmt = select(PullRequest).where(PullRequest.id == pr_id)
    pr = (await session.execute(stmt)).scalar_one_or_none()
    if not pr:
        raise HTTPException(status_code=404, detail="Pull request not found")

    if pr.status != PRStatus.REQUIRES_HUMAN_REVIEW:
        raise HTTPException(
            status_code=400,
            detail=f"PR #{pr.pr_number} is in status '{pr.status}', cannot approve push.",
        )

    # Execute push from workspace
    ws_mgr = WorkspaceManager()
    ws_path = ws_mgr.get_workspace_path(pr.repo_full_name, pr.pr_number)

    if not ws_path.exists():
        raise HTTPException(status_code=400, detail="Workspace directory not found for this PR")

    await SafeGitService.push_to_pr_branch(
        repo_dir=ws_path,
        expected_repo=pr.repo_full_name,
        expected_branch=pr.branch,
        auth_token=settings.GITHUB_TOKEN,
    )

    await transition_pr_status(session, pr.id, PRStatus.WAITING_FOR_CI, human_approval_required=False)
    return {"message": f"Approved and pushed fixes to PR #{pr.pr_number} ({pr.branch})"}


@router.post("/pull-requests/{pr_id}/stop")
async def stop_pr_repairs(pr_id: int, session: AsyncSession = Depends(get_async_session)):
    """Manually halt repairs on a PR."""
    stmt = select(PullRequest).where(PullRequest.id == pr_id)
    pr = (await session.execute(stmt)).scalar_one_or_none()
    if not pr:
        raise HTTPException(status_code=404, detail="Pull request not found")

    await transition_pr_status(session, pr.id, PRStatus.FAILED)
    return {"message": f"Repairs stopped for PR #{pr.pr_number}"}


class GitHubConfigRequest(BaseModel):
    token: Optional[str] = None
    username: Optional[str] = None
    api_url: Optional[str] = None


class AntigravityConfigRequest(BaseModel):
    provider: Optional[str] = None
    bin_path: Optional[str] = None
    model: Optional[str] = None


class IntegrationsUpdateRequest(BaseModel):
    github: Optional[GitHubConfigRequest] = None
    antigravity: Optional[AntigravityConfigRequest] = None


@router.get("/settings/integrations")
async def get_integrations_status():
    """Get current status of GitHub and Antigravity connections."""
    token = settings.GITHUB_TOKEN
    masked_token = None
    if token:
        masked_token = token[:4] + "..." + token[-4:] if len(token) > 8 else "***"

    antigravity_bin = settings.ANTIGRAVITY_BIN_PATH
    bin_exists = bool(shutil.which(antigravity_bin) or Path(antigravity_bin).is_file())
    if not bin_exists:
        discovered = shutil.which("agentapi")
        if discovered:
            antigravity_bin = discovered
            bin_exists = True

    return {
        "github": {
            "token_configured": bool(token),
            "masked_token": masked_token,
            "username": settings.GITHUB_USERNAME,
            "api_url": settings.GITHUB_API_URL,
        },
        "antigravity": {
            "provider": settings.AGENT_PROVIDER,
            "bin_path": antigravity_bin,
            "model": settings.ANTIGRAVITY_MODEL,
            "binary_exists": bin_exists,
        },
    }


@router.post("/settings/integrations")
async def update_integrations(req: IntegrationsUpdateRequest):
    """Save GitHub and Antigravity settings and persist to .env."""
    updates: Dict[str, Any] = {}

    if req.github:
        if req.github.token is not None and req.github.token.strip() != "":
            updates["GITHUB_TOKEN"] = req.github.token.strip()
        if req.github.username is not None:
            updates["GITHUB_USERNAME"] = req.github.username.strip()
        if req.github.api_url is not None:
            updates["GITHUB_API_URL"] = req.github.api_url.strip()

    if req.antigravity:
        if req.antigravity.provider is not None:
            updates["AGENT_PROVIDER"] = req.antigravity.provider.strip()
        if req.antigravity.bin_path is not None:
            updates["ANTIGRAVITY_BIN_PATH"] = req.antigravity.bin_path.strip()
        if req.antigravity.model is not None:
            updates["ANTIGRAVITY_MODEL"] = req.antigravity.model.strip()

    if updates:
        update_env_settings(updates)

    return {"message": "Integrations updated successfully", "updated_keys": list(updates.keys())}


@router.post("/settings/test-github")
async def test_github_connection(req: Optional[GitHubConfigRequest] = None):
    """Test connection to GitHub API using provided or current token."""
    if req and req.token is not None:
        test_token = req.token.strip()
    else:
        test_token = settings.GITHUB_TOKEN
    test_url = req.api_url.strip() if req and req.api_url and req.api_url.strip() else settings.GITHUB_API_URL

    if not test_token:
        return {"success": False, "error": "No GitHub token provided or configured."}

    try:
        async with GitHubClient(token=test_token, base_url=test_url) as client:
            user_data = await client.verify_auth()
            login = user_data.get("login", "")
            return {
                "success": True,
                "login": login,
                "name": user_data.get("name"),
                "avatar_url": user_data.get("avatar_url"),
                "html_url": user_data.get("html_url"),
            }
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@router.post("/settings/test-antigravity")
async def test_antigravity_connection(req: Optional[AntigravityConfigRequest] = None):
    """Verify Antigravity binary availability and readiness."""
    bin_path = (req.bin_path.strip() if req and req.bin_path and req.bin_path.strip() else settings.ANTIGRAVITY_BIN_PATH)

    # Check executable
    resolved_bin = None
    if shutil.which(bin_path) or Path(bin_path).is_file():
        resolved_bin = bin_path
    elif shutil.which("agentapi"):
        resolved_bin = shutil.which("agentapi")

    if not resolved_bin:
        return {
            "success": False,
            "error": f"Antigravity binary not found at '{bin_path}' and 'agentapi' is not in PATH.",
        }

    try:
        proc = await asyncio.create_subprocess_exec(
            resolved_bin,
            "--help",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        out_text = (stdout or stderr).decode("utf-8", errors="replace")
        return {
            "success": True,
            "binary_path": resolved_bin,
            "help_snippet": out_text[:300].strip(),
            "model": req.model if req and req.model else settings.ANTIGRAVITY_MODEL,
        }
    except Exception as exc:
        return {"success": False, "error": f"Failed to execute Antigravity binary: {exc}"}


class DeviceFlowStartRequest(BaseModel):
    client_id: Optional[str] = None
    scope: Optional[str] = "repo,read:user"


class DeviceFlowPollRequest(BaseModel):
    client_id: str
    device_code: str


@router.post("/auth/github/device/start")
async def start_github_device_auth(req: Optional[DeviceFlowStartRequest] = None):
    """Start GitHub Device Code authorization flow."""
    cid = (req.client_id if req and req.client_id else settings.GITHUB_CLIENT_ID)
    scope = (req.scope if req and req.scope else "repo,read:user")
    data = await GitHubAuthService.start_device_flow(client_id=cid, scope=scope)
    return data


@router.post("/auth/github/device/poll")
async def poll_github_device_auth(req: DeviceFlowPollRequest):
    """Poll GitHub to check if user authorized the device code, save token on success."""
    data = await GitHubAuthService.check_device_token(
        client_id=req.client_id,
        device_code=req.device_code,
        client_secret=settings.GITHUB_CLIENT_SECRET,
    )

    if "access_token" in data:
        token = data["access_token"]
        # Verify token and get username
        username = None
        user_info = {}
        try:
            async with GitHubClient(token=token) as client:
                user_info = await client.verify_auth()
                username = user_info.get("login")
        except Exception as e:
            logger.warning("Could not fetch user profile after device auth: %s", e)

        updates = {"GITHUB_TOKEN": token}
        if username:
            updates["GITHUB_USERNAME"] = username
        update_env_settings(updates)

        return {
            "success": True,
            "access_token": token,
            "username": username,
            "user": user_info,
        }

    err = data.get("error", "unknown")
    is_pending = err in ("authorization_pending", "slow_down")
    interval = data.get("interval", 10 if err == "slow_down" else 5)
    return {
        "success": False,
        "pending": is_pending,
        "error": err,
        "error_description": data.get("error_description", ""),
        "interval": interval,
    }


@router.post("/auth/github/disconnect")
async def disconnect_github():
    """Disconnect GitHub account and clear stored token."""
    update_env_settings({"GITHUB_TOKEN": "", "GITHUB_USERNAME": ""})
    settings.GITHUB_TOKEN = None
    settings.GITHUB_USERNAME = None
    return {"success": True, "message": "GitHub account disconnected."}


@router.get("/auth/antigravity/status")
async def get_antigravity_auth_status():
    """Check Antigravity session authentication and readiness."""
    return AntigravityAuthService.get_auth_status()

