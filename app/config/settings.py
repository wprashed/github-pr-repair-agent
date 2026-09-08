"""Application configuration and settings via Pydantic Settings."""

from pathlib import Path
from typing import Optional, Literal, List
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Base paths
    BASE_DIR: Path = Field(default_factory=lambda: Path(__file__).resolve().parent.parent.parent)
    CONFIG_PATH: Path = Field(default_factory=lambda: Path(__file__).resolve().parent.parent.parent / "config" / "repositories.yaml")
    WORKSPACE_DIR: Path = Field(default_factory=lambda: Path(__file__).resolve().parent.parent.parent / "workspaces")
    LOG_DIR: Path = Field(default_factory=lambda: Path(__file__).resolve().parent.parent.parent / "logs")
    DATA_DIR: Path = Field(default_factory=lambda: Path(__file__).resolve().parent.parent.parent / "data")

    # Blocked / Ignored Organizations & Repositories
    BLOCKED_ORGS: List[str] = Field(default_factory=lambda: ["themeum"])

    def is_repo_blocked(self, repo_full_name: Optional[str]) -> bool:
        """Check whether a repository belongs to a blocked organization or prefix."""
        if not repo_full_name:
            return False
        clean = repo_full_name.strip().lower()
        owner = clean.split("/")[0] if "/" in clean else clean
        return any(owner == b.lower() or f"{b.lower()}/" in clean for b in self.BLOCKED_ORGS)

    # GitHub
    GITHUB_TOKEN: Optional[str] = Field(default=None)
    GITHUB_USERNAME: Optional[str] = Field(default=None)
    GITHUB_API_URL: str = Field(default="https://api.github.com")
    GITHUB_CLIENT_ID: Optional[str] = Field(default=None)
    GITHUB_CLIENT_SECRET: Optional[str] = Field(default=None)

    # Scheduler & Automation
    SCHEDULER_INTERVAL_HOURS: float = Field(default=3.0)
    AUTOMATION_MODE: Literal["auto", "approval"] = Field(default="auto")
    REPAIRS_ENABLED: bool = Field(default=True)
    MAX_CONCURRENT_REPAIRS: int = Field(default=2)

    # Agent
    AGENT_PROVIDER: str = Field(default="antigravity")
    ANTIGRAVITY_BIN_PATH: str = Field(default="/Users/rashed/.gemini/antigravity/bin/agentapi")
    ANTIGRAVITY_MODEL: Literal["flash_lite", "flash", "pro"] = Field(default="flash")
    AGENT_TIMEOUT_SECONDS: int = Field(default=900)

    # Global Safety Defaults
    SAFETY_MAX_FILES_CHANGED: int = Field(default=20)
    SAFETY_MAX_LINES_ADDED: int = Field(default=1000)
    SAFETY_MAX_LINES_DELETED: int = Field(default=1000)
    SAFETY_ALLOW_NEW_FILES: bool = Field(default=True)
    SAFETY_ALLOW_DELETED_FILES: bool = Field(default=False)
    SAFETY_SECRET_SCANNING: bool = Field(default=True)

    # Repair Attempts
    REPAIR_MAX_ATTEMPTS: int = Field(default=3)
    REPAIR_RETRY_FAILED: bool = Field(default=True)

    # Database
    WORKSPACE_RETENTION_HOURS: int = Field(default=24)
    LOG_LEVEL: str = Field(default="INFO")

    @property
    def DATABASE_URL(self) -> str:
        return f"sqlite+aiosqlite:///{self.DATA_DIR / 'pr_agent.db'}"

    @property
    def SQLITE_SYNC_URL(self) -> str:
        return f"sqlite:///{self.DATA_DIR / 'pr_agent.db'}"

    # Notifications
    NOTIFICATIONS_ENABLED: bool = Field(default=True)
    NOTIFICATIONS_CONSOLE: bool = Field(default=True)
    SLACK_WEBHOOK_URL: Optional[str] = Field(default=None)
    EMAIL_SMTP_HOST: Optional[str] = Field(default=None)
    EMAIL_SMTP_PORT: int = Field(default=587)
    EMAIL_SMTP_USER: Optional[str] = Field(default=None)
    EMAIL_SMTP_PASSWORD: Optional[str] = Field(default=None)
    EMAIL_TO: Optional[str] = Field(default=None)

    # Web Dashboard & API
    SERVER_HOST: str = Field(default="0.0.0.0")
    SERVER_PORT: int = Field(default=8000)
    DASHBOARD_AUTH_TOKEN: Optional[str] = Field(default=None)

    def ensure_directories(self) -> None:
        """Ensure all runtime directories exist."""
        self.WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
        self.LOG_DIR.mkdir(parents=True, exist_ok=True)
        self.DATA_DIR.mkdir(parents=True, exist_ok=True)


settings = Settings()
settings.ensure_directories()


def update_env_settings(updates: dict) -> None:
    """Persist key-value updates to .env file and update active in-memory settings."""
    env_path = settings.BASE_DIR / ".env"
    existing_lines = []
    if env_path.exists():
        existing_lines = env_path.read_text(encoding="utf-8").splitlines()

    existing_dict = {}
    for line in existing_lines:
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            existing_dict[k.strip()] = v.strip()

    # Update in memory
    for k, v in updates.items():
        if hasattr(settings, k):
            setattr(settings, k, v)
        existing_dict[k] = str(v)

    # Re-write .env safely
    new_lines = []
    keys_written = set()
    for line in existing_lines:
        if "=" in line and not line.strip().startswith("#"):
            k = line.split("=", 1)[0].strip()
            if k in existing_dict:
                new_lines.append(f"{k}={existing_dict[k]}")
                keys_written.add(k)
                continue
        new_lines.append(line)

    for k, v in existing_dict.items():
        if k not in keys_written:
            new_lines.append(f"{k}={v}")

    env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
