"""SQLAlchemy database models for GitHub PR Repair Agent."""

from datetime import datetime
from typing import Optional
from sqlalchemy import (
    Column,
    Integer,
    String,
    Boolean,
    DateTime,
    ForeignKey,
    Text,
    Enum as SQLEnum,
    UniqueConstraint,
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


class PRStatus(str):
    MONITORING = "MONITORING"
    NEEDS_REPAIR = "NEEDS_REPAIR"
    PREPARING = "PREPARING"
    AGENT_RUNNING = "AGENT_RUNNING"
    VERIFYING = "VERIFYING"
    DIFF_REVIEW = "DIFF_REVIEW"
    READY_TO_PUSH = "READY_TO_PUSH"
    PUSHING = "PUSHING"
    WAITING_FOR_CI = "WAITING_FOR_CI"
    FIXED = "FIXED"
    FAILED = "FAILED"
    REQUIRES_HUMAN_REVIEW = "REQUIRES_HUMAN_REVIEW"


class Repository(Base):
    __tablename__ = "repositories"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False)
    github_full_name = Column(String(200), unique=True, nullable=False, index=True)
    enabled = Column(Boolean, default=True)
    last_scanned_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    pull_requests = relationship("PullRequest", back_populates="repository", cascade="all, delete-orphan")


class PullRequest(Base):
    __tablename__ = "pull_requests"

    id = Column(Integer, primary_key=True, autoincrement=True)
    repository_id = Column(Integer, ForeignKey("repositories.id"), nullable=False)
    repo_full_name = Column(String(200), nullable=False, index=True)
    pr_number = Column(Integer, nullable=False, index=True)
    title = Column(String(500), nullable=False)
    author = Column(String(100), nullable=False)
    branch = Column(String(255), nullable=False)
    base_branch = Column(String(255), nullable=False)
    last_commit_sha = Column(String(64), nullable=True)

    status = Column(String(50), default=PRStatus.MONITORING, nullable=False, index=True)
    repair_attempts = Column(Integer, default=0)
    max_attempts = Column(Integer, default=3)

    last_review_timestamp = Column(DateTime, nullable=True)
    last_comment_timestamp = Column(DateTime, nullable=True)
    last_check_status = Column(String(50), nullable=True)
    last_scan_time = Column(DateTime, nullable=True)
    last_repair_time = Column(DateTime, nullable=True)

    is_locked = Column(Boolean, default=False)
    lock_acquired_at = Column(DateTime, nullable=True)
    human_approval_required = Column(Boolean, default=False)
    latest_diff = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("repo_full_name", "pr_number", name="uq_repo_pr_number"),
    )

    repository = relationship("Repository", back_populates="pull_requests")
    events = relationship("Event", back_populates="pull_request", cascade="all, delete-orphan")
    repairs = relationship("RepairAttempt", back_populates="pull_request", cascade="all, delete-orphan")


class Event(Base):
    __tablename__ = "events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    pull_request_id = Column(Integer, ForeignKey("pull_requests.id"), nullable=False, index=True)
    event_type = Column(String(50), nullable=False)  # review, review_comment, issue_comment, check_run, workflow_run
    external_id = Column(String(255), nullable=False, index=True)  # unique ID from GitHub for deduplication
    status = Column(String(50), default="processed", nullable=False)  # processed, ignored, pending
    details = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("pull_request_id", "event_type", "external_id", name="uq_event_dedup"),
    )

    pull_request = relationship("PullRequest", back_populates="events")


class RepairAttempt(Base):
    __tablename__ = "repair_attempts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    pull_request_id = Column(Integer, ForeignKey("pull_requests.id"), nullable=False, index=True)
    attempt_number = Column(Integer, nullable=False)
    trigger_reason = Column(String(255), nullable=False)
    started_at = Column(DateTime, default=datetime.utcnow)
    finished_at = Column(DateTime, nullable=True)
    status = Column(String(50), default="RUNNING", nullable=False)  # SUCCESS, FAILED, BLOCKED, WAITING_APPROVAL
    files_changed_count = Column(Integer, default=0)
    tests_passed = Column(Boolean, default=False)
    diff_safety_passed = Column(Boolean, default=False)
    commit_sha = Column(String(64), nullable=True)
    error_message = Column(Text, nullable=True)
    summary = Column(Text, nullable=True)

    pull_request = relationship("PullRequest", back_populates="repairs")
    agent_runs = relationship("AgentRun", back_populates="repair_attempt", cascade="all, delete-orphan")
    verification_runs = relationship("VerificationRun", back_populates="repair_attempt", cascade="all, delete-orphan")


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    repair_attempt_id = Column(Integer, ForeignKey("repair_attempts.id"), nullable=False, index=True)
    agent_provider = Column(String(50), nullable=False)
    model = Column(String(50), nullable=True)
    prompt_summary = Column(Text, nullable=True)
    status = Column(String(50), nullable=False)
    duration_seconds = Column(Integer, default=0)
    raw_output = Column(Text, nullable=True)
    structured_output = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    repair_attempt = relationship("RepairAttempt", back_populates="agent_runs")


class VerificationRun(Base):
    __tablename__ = "verification_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    repair_attempt_id = Column(Integer, ForeignKey("repair_attempts.id"), nullable=False, index=True)
    check_type = Column(String(50), nullable=False)  # test, lint, static_analysis, security, build
    command = Column(String(255), nullable=False)
    passed = Column(Boolean, nullable=False)
    exit_code = Column(Integer, nullable=False)
    stdout = Column(Text, nullable=True)
    stderr = Column(Text, nullable=True)
    duration_seconds = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)

    repair_attempt = relationship("RepairAttempt", back_populates="verification_runs")


class CommitRecord(Base):
    __tablename__ = "commits"

    id = Column(Integer, primary_key=True, autoincrement=True)
    pull_request_id = Column(Integer, ForeignKey("pull_requests.id"), nullable=False, index=True)
    commit_sha = Column(String(64), nullable=False)
    message = Column(Text, nullable=False)
    branch = Column(String(255), nullable=False)
    pushed_at = Column(DateTime, default=datetime.utcnow)


class NotificationLog(Base):
    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True, autoincrement=True)
    channel = Column(String(50), nullable=False)  # console, slack, email
    event_type = Column(String(100), nullable=False)
    title = Column(String(255), nullable=False)
    content = Column(Text, nullable=False)
    sent_at = Column(DateTime, default=datetime.utcnow)
    status = Column(String(50), default="sent")
