"""Central configuration for the workflow system. All values come from
environment variables with sensible defaults for local development."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class WorkflowConfig:
    # Database
    db_path: str = field(
        default_factory=lambda: os.environ.get(
            "WORKFLOW_DB_PATH",
            str(Path(__file__).resolve().parent.parent / "data" / "workflow.db"),
        )
    )

    # Worker
    worker_id: str = field(
        default_factory=lambda: os.environ.get("WORKER_ID", "worker-1")
    )
    poll_interval: float = float(os.environ.get("WORKER_POLL_INTERVAL", "2.0"))
    lease_duration: int = int(os.environ.get("WORKER_LEASE_DURATION", "300"))
    renew_interval: int = int(os.environ.get("WORKER_RENEW_INTERVAL", "30"))

    # Data directory
    data_dir: str = field(
        default_factory=lambda: os.environ.get(
            "WORKFLOW_DATA_DIR",
            str(Path(__file__).resolve().parent.parent / "data"),
        )
    )

    # Auth (test mode)
    app_env: str = os.environ.get("APP_ENV", "test")
    jwt_secret: str = field(
        default_factory=lambda: os.environ.get("JWT_SECRET", "dev-secret-change-in-production")
    )

    # Test users (only valid when APP_ENV=test)
    test_users: dict = field(default_factory=lambda: {
        "alice": {"password": "test-pass", "role": "user"},
        "bob": {"password": "test-pass", "role": "user"},
        "admin": {"password": "admin-pass", "role": "admin"},
    })

    # Agent backend
    agent_backend: str = field(
        default_factory=lambda: os.environ.get("AGENT_BACKEND", "mock")
    )
    pi_command: str = field(
        default_factory=lambda: os.environ.get("PI_COMMAND", "pi")
    )
    pi_timeout: int = int(os.environ.get("PI_TIMEOUT_SECONDS", "300"))


def load_config(**overrides) -> WorkflowConfig:
    """Factory that applies optional overrides (useful for tests)."""
    return WorkflowConfig(**overrides)
