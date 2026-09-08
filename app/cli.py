"""Click CLI tool for the Autonomous PR Repair Agent."""

import asyncio
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional
import click
from rich.console import Console
from rich.table import Table

import webbrowser

from app.config.repositories import load_repositories_config
from app.config.settings import settings, update_env_settings
from app.database.database import get_sync_db, init_db
from app.database.models import PullRequest, RepairAttempt, Repository
from app.github.auth import GitHubAuthService, AntigravityAuthService
from app.github.client import GitHubClient
from app.monitoring.scanner import PRRepairScanner

console = Console()


@click.group()
def cli():
    """Autonomous GitHub Pull Request Repair Agent CLI."""
    pass


@cli.command()
@click.option("--host", default=None, help="Host to bind server to")
@click.option("--port", default=None, type=int, help="Port to bind server to")
def start(host: Optional[str], port: Optional[int]):
    """Start the PR Repair service and web dashboard."""
    import uvicorn
    init_db()
    server_host = host or settings.SERVER_HOST
    server_port = port or settings.SERVER_PORT
    console.print(f"[bold green]Starting PR Repair Agent on http://{server_host}:{server_port}[/]")
    uvicorn.run("app.main:app", host=server_host, port=server_port, reload=False)


@cli.command()
@click.option("--repo", default=None, help="Target specific repository (owner/repo)")
@click.option("--pr", default=None, type=int, help="Target specific PR number")
def scan(repo: Optional[str], pr: Optional[int]):
    """Scan repositories for actionable pull requests."""
    init_db()
    console.print("[bold blue]Running PR scan...[/]")

    async def _do_scan():
        scanner = PRRepairScanner()
        if repo:
            repos = load_repositories_config(settings.CONFIG_PATH)
            target = next((r for r in repos if r.github.lower() == repo.lower()), None)
            if not target:
                console.print(f"[bold red]Repository '{repo}' not found in {settings.CONFIG_PATH}[/]")
                return
            await scanner.scan_repository(target)
        else:
            await scanner.scan_all_repositories()

    asyncio.run(_do_scan())
    console.print("[bold green]Scan complete.[/]")


@cli.command()
@click.option("--repo", required=True, help="Repository (owner/repo)")
@click.option("--pr", required=True, type=int, help="PR number")
def repair(repo: str, pr: int):
    """Trigger manual repair of a specific pull request."""
    init_db()
    console.print(f"[bold yellow]Triggering manual repair for {repo} #{pr}...[/]")

    async def _do_repair():
        scanner = PRRepairScanner()
        repos = load_repositories_config(settings.CONFIG_PATH)
        target = next((r for r in repos if r.github.lower() == repo.lower()), None)
        if not target:
            console.print(f"[bold red]Repository '{repo}' not found in {settings.CONFIG_PATH}[/]")
            return

        detail = await scanner.pr_service.get_pr(repo, pr)
        # Find DB repo id
        with get_sync_db() as session:
            db_repo = session.query(Repository).filter(Repository.github_full_name == repo).first()
            if not db_repo:
                db_repo = Repository(name=target.name, github_full_name=repo, enabled=True)
                session.add(db_repo)
                session.commit()
            repo_id = db_repo.id

        await scanner.evaluate_and_repair_pr(target, repo_id, detail)

    asyncio.run(_do_repair())
    console.print("[bold green]Repair run completed.[/]")


@cli.command()
def status():
    """Display current status of monitored PRs."""
    init_db()
    with get_sync_db() as session:
        prs = session.query(PullRequest).all()

        if not prs:
            console.print("[italic text-slate-400]No pull requests in database. Run 'pr-agent scan' first.[/]")
            return

        table = Table(title="Monitored Pull Requests")
        table.add_column("PR", style="bold cyan")
        table.add_column("Title", style="white")
        table.add_column("Repository", style="magenta")
        table.add_column("Branch", style="green")
        table.add_column("Status", style="bold yellow")
        table.add_column("Attempts", style="blue")
        table.add_column("Last Scan", style="slate-400")

        for p in prs:
            scan_time = p.last_scan_time.strftime("%Y-%m-%d %H:%M") if p.last_scan_time else "-"
            table.add_row(
                f"#{p.pr_number}",
                p.title[:30] + ("..." if len(p.title) > 30 else ""),
                p.repo_full_name,
                p.branch,
                p.status,
                f"{p.repair_attempts}/{p.max_attempts}",
                scan_time,
            )

        console.print(table)


@cli.command()
@click.option("--limit", default=10, help="Number of log entries to display")
def logs(limit: int):
    """View recent repair attempt logs."""
    init_db()
    with get_sync_db() as session:
        attempts = session.query(RepairAttempt).order_by(RepairAttempt.started_at.desc()).limit(limit).all()

        if not attempts:
            console.print("[italic text-slate-400]No repair logs recorded yet.[/]")
            return

        table = Table(title=f"Last {len(attempts)} Repair Attempts")
        table.add_column("Attempt", style="bold")
        table.add_column("PR ID", style="cyan")
        table.add_column("Status", style="bold")
        table.add_column("Started", style="slate-400")
        table.add_column("Reason", style="white")
        table.add_column("Commit", style="green")

        for a in attempts:
            table.add_row(
                f"#{a.attempt_number}",
                str(a.pull_request_id),
                a.status,
                a.started_at.strftime("%Y-%m-%d %H:%M:%S") if a.started_at else "-",
                (a.trigger_reason or "")[:35],
                a.commit_sha or "-",
            )

        console.print(table)


@cli.command()
def doctor():
    """Verify system health, credentials, git, and agent availability."""
    console.print("[bold cyan]Running PR Repair Agent Doctor Diagnostics...[/]\n")

    # 1. Check Git
    git_path = shutil.which("git")
    if git_path:
        git_ver = subprocess.getoutput("git --version")
        console.print(f"  [green]✔[/] Git installed: {git_ver}")
    else:
        console.print("  [red]✘[/] Git is not installed or not in PATH")

    # 2. Check GitHub Token
    token = settings.GITHUB_TOKEN
    if token:
        masked = token[:4] + "..." + token[-4:] if len(token) > 8 else "***"
        console.print(f"  [green]✔[/] GITHUB_TOKEN configured: {masked}")
        async def _check_gh():
            try:
                async with GitHubClient() as client:
                    user_data = await client.verify_auth()
                    login = user_data.get("login", "unknown")
                    console.print(f"  [green]✔[/] GitHub Authentication successful (User: @{login})")
            except Exception as e:
                console.print(f"  [red]✘[/] GitHub Authentication failed: {e}")
        asyncio.run(_check_gh())
    else:
        console.print("  [yellow]![/] GITHUB_TOKEN not configured (set in .env)")

    # 3. Check Antigravity
    antigravity_bin = settings.ANTIGRAVITY_BIN_PATH
    if shutil.which(antigravity_bin) or Path(antigravity_bin).is_file():
        console.print(f"  [green]✔[/] Antigravity binary available at: {antigravity_bin}")
    elif shutil.which("agentapi"):
        console.print(f"  [green]✔[/] Antigravity agentapi discovered in PATH: {shutil.which('agentapi')}")
    else:
        console.print(f"  [yellow]![/] Antigravity binary not found at '{antigravity_bin}' (MockCodingAgent can be used)")

    # 4. Check Docker
    docker_path = shutil.which("docker")
    if docker_path:
        docker_ver = subprocess.getoutput("docker --version")
        console.print(f"  [green]✔[/] Docker installed: {docker_ver}")
    else:
        console.print("  [slate-400]○[/] Docker not found (optional, used for containerized execution)")

    # 5. Check Configuration
    cfg_path = settings.CONFIG_PATH
    if cfg_path.exists():
        repos = load_repositories_config(cfg_path)
        console.print(f"  [green]✔[/] Configuration loaded: {len(repos)} repository/ies defined in {cfg_path.name}")
    else:
        console.print(f"  [red]✘[/] Configuration file not found at: {cfg_path}")

    # 6. Check Database
    try:
        init_db()
        console.print(f"  [green]✔[/] SQLite Database initialized at: {settings.SQLITE_SYNC_URL}")
    except Exception as e:
        console.print(f"  [red]✘[/] Database initialization failed: {e}")

    console.print("\n[bold green]Diagnostics complete.[/]")


@cli.group()
def auth():
    """Manage authentication for GitHub and Antigravity."""
    pass


@auth.command("status")
def auth_status():
    """Display authentication status for GitHub and Antigravity."""
    console.print("[bold cyan]═══ Integration Authentication Status ═══[/]\n")

    # 1. GitHub Status
    token = settings.GITHUB_TOKEN
    username = settings.GITHUB_USERNAME
    if token:
        masked = token[:4] + "..." + token[-4:] if len(token) > 8 else "***"
        console.print(f"  [bold]GitHub:[/] [green]Configured[/] (Token: {masked})")

        async def _check():
            try:
                async with GitHubClient(token=token) as client:
                    u = await client.verify_auth()
                    login = u.get("login")
                    name = u.get("name")
                    console.print(f"  [bold]GitHub User:[/] [green]✔ @{login}[/] ({name or 'No display name'})")
            except Exception as e:
                console.print(f"  [bold]GitHub Check:[/] [red]✘ Failed ({e})[/]")

        asyncio.run(_check())
    else:
        console.print("  [bold]GitHub:[/] [yellow]Not configured[/] (Run 'pr-agent auth login')")

    # 2. Antigravity Status
    ag_stat = AntigravityAuthService.get_auth_status()
    console.print(f"\n  [bold]Antigravity Session:[/] {'[green]Active[/]' if ag_stat['session_active'] else '[yellow]None detected[/]'}")
    console.print(f"  [bold]Antigravity Binary:[/] {'[green]Available (' + str(ag_stat['binary_path']) + ')[/]' if ag_stat['binary_exists'] else '[red]Not found[/]'}")
    console.print(f"  [bold]Antigravity Status:[/] {ag_stat['summary']}")
    console.print(f"  [bold]Default Model:[/] [cyan]{settings.ANTIGRAVITY_MODEL}[/]")


@auth.command("login")
@click.option("--browser/--no-browser", default=True, help="Automatically open browser for authorization")
def auth_login(browser: bool):
    """Authenticate with GitHub using Device Authorization flow (RFC 8628)."""
    console.print("[bold cyan]═══ GitHub Device Authorization Login ═══[/]\n")
    console.print("Initiating secure one-time device authorization flow...")

    async def _do_login():
        data = await GitHubAuthService.start_device_flow()
        if "error" in data or "user_code" not in data:
            console.print(f"[bold red]✘ Failed to initiate device flow: {data.get('error')}[/]")
            return

        user_code = data["user_code"]
        verification_uri = data["verification_uri"]
        device_code = data["device_code"]
        client_id = data["client_id"]
        interval = max(data.get("interval", 5), 5)

        console.print(f"\n[bold yellow]1. Your One-Time Authorization Code:[/] [bold green on black]  {user_code}  [/]")
        console.print(f"[bold yellow]2. Verification URL:[/] [bold underline cyan]{verification_uri}[/]\n")

        if browser:
            try:
                webbrowser.open(verification_uri)
                console.print("[dim]Opened verification URL in your default browser...[/]")
            except Exception:
                pass

        console.print(f"Waiting for your authorization in browser (polling every {interval}s)...")

        while True:
            await asyncio.sleep(interval)
            poll_res = await GitHubAuthService.check_device_token(
                client_id=client_id,
                device_code=device_code,
                client_secret=settings.GITHUB_CLIENT_SECRET,
            )

            if "access_token" in poll_res:
                token = poll_res["access_token"]
                username = None
                try:
                    async with GitHubClient(token=token) as client:
                        u = await client.verify_auth()
                        username = u.get("login")
                except Exception:
                    pass

                updates = {"GITHUB_TOKEN": token}
                if username:
                    updates["GITHUB_USERNAME"] = username

                update_env_settings(updates)
                console.print(f"\n[bold green]✔ Successfully authenticated with GitHub as @{username or 'user'}![/]")
                console.print("[bold green]✔ Token saved securely to .env and active in runtime.[/]")
                break

            err = poll_res.get("error")
            if err == "authorization_pending":
                console.print("[dim].[/]", end="")
                continue
            elif err == "slow_down":
                interval += 5
                console.print("[dim]slow_down...[/]", end="")
                continue
            elif err == "expired_token":
                console.print("\n[bold red]✘ The device authorization code has expired. Please run 'pr-agent auth login' again.[/]")
                break
            elif err == "access_denied":
                console.print("\n[bold red]✘ Authorization was denied by the user on GitHub.[/]")
                break
            else:
                console.print(f"\n[bold red]✘ Authorization failed: {poll_res.get('error_description') or err}[/]")
                break

    asyncio.run(_do_login())


@cli.command()
@click.option("--token", default=None, help="GitHub Personal Access Token")
@click.option("--username", default=None, help="GitHub Username")
@click.option("--antigravity-bin", default=None, help="Path to Antigravity binary (agentapi)")
@click.option("--model", default=None, type=click.Choice(["flash_lite", "flash", "pro"]), help="Antigravity Model")
@click.option("--device/--no-device", default=False, help="Use GitHub Device Authorization flow")
def connect(token: Optional[str], username: Optional[str], antigravity_bin: Optional[str], model: Optional[str], device: bool):
    """Connect and configure GitHub and Antigravity credentials."""
    console.print("[bold cyan]═══ Connect GitHub & Antigravity Integrations ═══[/]\n")

    if device:
        # Delegate to auth login
        ctx = click.get_current_context()
        ctx.invoke(auth_login, browser=True)
        final_token = settings.GITHUB_TOKEN
        final_user = settings.GITHUB_USERNAME
    else:
        # 1. GitHub Token & Username
        current_token = settings.GITHUB_TOKEN or ""
        masked_curr = current_token[:4] + "..." + current_token[-4:] if len(current_token) > 8 else (current_token or "None")

        if not token:
            token_input = click.prompt(
                f"GitHub Token [{masked_curr}] (Press Enter to keep, or type 'auth' to use Device Flow)",
                default="",
                show_default=False,
                hide_input=True,
            )
            if token_input.strip().lower() == "auth":
                ctx = click.get_current_context()
                ctx.invoke(auth_login, browser=True)
                final_token = settings.GITHUB_TOKEN
                final_user = settings.GITHUB_USERNAME
            else:
                final_token = token_input.strip() if token_input.strip() else current_token
                final_user = username.strip() if username else click.prompt("GitHub Username", default=settings.GITHUB_USERNAME or "")
        else:
            final_token = token.strip()
            final_user = username.strip() if username else settings.GITHUB_USERNAME or ""

        # Test GitHub Connection
        if final_token:
            console.print("[blue]Testing GitHub authentication...[/]")
            async def _test_gh():
                try:
                    async with GitHubClient(token=final_token) as client:
                        data = await client.verify_auth()
                        console.print(f"  [green]✔ GitHub Connected successfully! User: @{data.get('login')}[/]")
                        return True
                except Exception as e:
                    console.print(f"  [red]✘ GitHub connection error: {e}[/]")
                    return False
            asyncio.run(_test_gh())

    # 2. Antigravity Configuration
    console.print("\n[bold cyan]─── Antigravity Configuration ───[/]")
    default_bin = settings.ANTIGRAVITY_BIN_PATH
    if not (shutil.which(default_bin) or Path(default_bin).is_file()):
        detected = shutil.which("agentapi")
        if detected:
            default_bin = detected

    if not antigravity_bin:
        final_bin = click.prompt("Antigravity Binary Path", default=default_bin)
    else:
        final_bin = antigravity_bin.strip()

    if not model:
        final_model = click.prompt("Antigravity Model", default=settings.ANTIGRAVITY_MODEL, type=click.Choice(["flash_lite", "flash", "pro"]))
    else:
        final_model = model.strip()

    # Test Antigravity
    resolved_bin = final_bin if (shutil.which(final_bin) or Path(final_bin).is_file()) else shutil.which("agentapi")
    if resolved_bin:
        console.print(f"  [green]✔ Antigravity binary located at: {resolved_bin}[/]")
        try:
            out = subprocess.check_output([resolved_bin, "--help"], stderr=subprocess.STDOUT, text=True)
            console.print(f"  [green]✔ Antigravity is responsive and ready![/]")
        except Exception as e:
            console.print(f"  [yellow]! Antigravity returned: {e}[/]")
    else:
        console.print(f"  [yellow]! Antigravity binary not found at '{final_bin}'[/]")

    # Check session
    ag_stat = AntigravityAuthService.get_auth_status()
    if ag_stat.get("session_active"):
        console.print("  [green]✔ Active Antigravity developer session detected[/]")

    # Save to .env
    updates = {
        "AGENT_PROVIDER": "antigravity",
        "ANTIGRAVITY_BIN_PATH": final_bin,
        "ANTIGRAVITY_MODEL": final_model,
    }
    if final_token:
        updates["GITHUB_TOKEN"] = final_token
    if final_user:
        updates["GITHUB_USERNAME"] = final_user

    update_env_settings(updates)
    console.print("\n[bold green]✔ Integration settings saved to .env and applied successfully![/]")


if __name__ == "__main__":
    cli()

