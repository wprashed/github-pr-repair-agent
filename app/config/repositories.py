"""Repository configuration models and YAML loader."""

from pathlib import Path
from typing import Dict, List, Optional
import yaml
from pydantic import BaseModel, Field


class PullRequestFilter(BaseModel):
    only_my_prs: bool = True
    only_open_prs: bool = True


class ChecksConfig(BaseModel):
    test: List[str] = Field(default_factory=list)
    lint: List[str] = Field(default_factory=list)
    static_analysis: List[str] = Field(default_factory=list)
    security: List[str] = Field(default_factory=list)
    build: List[str] = Field(default_factory=list)


class RepoSafetyConfig(BaseModel):
    max_files_changed: int = 20
    max_lines_added: int = 1000
    max_lines_deleted: int = 1000
    allow_new_files: bool = True
    allow_deleted_files: bool = False
    allow_workflow_changes: bool = False


class RepoRepairConfig(BaseModel):
    max_attempts: int = 3
    retry_failed_repairs: bool = True


class RepositoryConfig(BaseModel):
    name: str
    github: str  # owner/repo format
    enabled: bool = True
    pull_requests: PullRequestFilter = Field(default_factory=PullRequestFilter)
    checks: ChecksConfig = Field(default_factory=ChecksConfig)
    safety: RepoSafetyConfig = Field(default_factory=RepoSafetyConfig)
    repair: RepoRepairConfig = Field(default_factory=RepoRepairConfig)

    @property
    def owner(self) -> str:
        return self.github.split("/")[0]

    @property
    def repo_name(self) -> str:
        return self.github.split("/")[1] if "/" in self.github else self.github


class RepositoriesFile(BaseModel):
    repositories: List[RepositoryConfig] = Field(default_factory=list)


def load_repositories_config(config_path: Path) -> List[RepositoryConfig]:
    """Load and validate repository configuration from a YAML file."""
    if not config_path.exists():
        return []

    with open(config_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    parsed = RepositoriesFile(**data)
    return parsed.repositories


def save_repositories_config(config_path: Path, repositories: List[RepositoryConfig]) -> None:
    """Serialize and save repositories to YAML."""
    data = {
        "repositories": [repo.model_dump() for repo in repositories]
    }
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)


def add_or_update_repository(config_path: Path, repo_cfg: RepositoryConfig) -> List[RepositoryConfig]:
    """Add a new repository or update an existing one in config."""
    existing = load_repositories_config(config_path)
    updated = []
    found = False
    for r in existing:
        if r.github.lower() == repo_cfg.github.lower():
            updated.append(repo_cfg)
            found = True
        else:
            updated.append(r)
    if not found:
        updated.append(repo_cfg)
    save_repositories_config(config_path, updated)
    return updated


def remove_repository(config_path: Path, github_full_name: str) -> List[RepositoryConfig]:
    """Remove a repository from config by owner/repo name."""
    existing = load_repositories_config(config_path)
    updated = [r for r in existing if r.github.lower() != github_full_name.lower()]
    save_repositories_config(config_path, updated)
    return updated
