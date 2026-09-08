"""Workspace cleanup and retention policies."""

import logging
import shutil
import time
from pathlib import Path
from typing import Optional

from app.config.settings import settings

logger = logging.getLogger(__name__)


def cleanup_expired_workspaces(
    base_dir: Optional[Path] = None,
    max_age_hours: Optional[int] = None,
) -> int:
    """Delete workspaces older than the configured retention threshold."""
    base = base_dir or settings.WORKSPACE_DIR
    age_limit = (max_age_hours or settings.WORKSPACE_RETENTION_HOURS) * 3600
    now = time.time()
    cleaned_count = 0

    if not base.exists():
        return 0

    for repo_dir in base.iterdir():
        if repo_dir.is_dir():
            for pr_dir in repo_dir.iterdir():
                if pr_dir.is_dir():
                    try:
                        mtime = pr_dir.stat().st_mtime
                        if (now - mtime) > age_limit:
                            shutil.rmtree(pr_dir, ignore_errors=True)
                            cleaned_count += 1
                            logger.info("Removed expired workspace: %s", pr_dir)
                    except Exception as exc:
                        logger.warning("Error checking workspace %s: %s", pr_dir, exc)

    return cleaned_count
