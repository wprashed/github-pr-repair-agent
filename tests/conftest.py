"""Pytest configuration and test fixtures."""

import os
import shutil
import tempfile
from pathlib import Path
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.models import Base
from app.database.database import sync_engine
from app.config.settings import settings


@pytest.fixture
def temp_dir():
    """Create a temporary directory for tests and cleanup afterwards."""
    tmp = tempfile.mkdtemp(prefix="pr_agent_test_")
    yield Path(tmp)
    shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture(autouse=True)
def clean_database():
    """Ensure a clean database state for every test."""
    Base.metadata.drop_all(bind=sync_engine)
    Base.metadata.create_all(bind=sync_engine)
    yield
    Base.metadata.drop_all(bind=sync_engine)
