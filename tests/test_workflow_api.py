"""Tests for the workflow API authentication and authorization."""

import os
import sys
import tempfile
import uuid
import pytest
from fastapi.testclient import TestClient

ROOT = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, os.path.join(ROOT, "python-tools"))

os.environ["APP_ENV"] = "test"
# Use a temp file so SQLite connections share the same database
os.environ["WORKFLOW_DB_PATH"] = os.path.join(
    tempfile.gettempdir(), f"test_workflow_api_{uuid.uuid4().hex}.db"
)

from web_api_server import app  # noqa: E402

client = TestClient(app)


def test_auth_token_requires_password():
    """Token request without password should return 422."""
    resp = client.post("/api/auth/token", json={"user_id": "alice"})
    assert resp.status_code == 422


def test_auth_token_invalid_password():
    """Token request with wrong password should return 401."""
    resp = client.post("/api/auth/token", json={"user_id": "alice", "password": "wrong"})
    assert resp.status_code == 401


def test_auth_token_valid():
    """Token request with valid password should return a bearer token."""
    resp = client.post("/api/auth/token", json={"user_id": "alice", "password": "test-pass"})
    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"


def test_create_task_with_token():
    """Authenticated user can create a research task."""
    resp = client.post("/api/auth/token", json={"user_id": "alice", "password": "test-pass"})
    token = resp.json()["access_token"]
    resp2 = client.post(
        "/api/research/tasks",
        json={"title": "test", "query": "test query"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp2.status_code == 201


def test_cannot_access_other_user_task():
    """A user cannot access a task belonging to another user."""
    # Alice creates task
    resp_a = client.post("/api/auth/token", json={"user_id": "alice", "password": "test-pass"})
    token_a = resp_a.json()["access_token"]
    create = client.post(
        "/api/research/tasks",
        json={"title": "alice task", "query": "x"},
        headers={"Authorization": f"Bearer {token_a}"},
    )
    task_id = create.json()["id"]

    # Bob tries to access
    resp_b = client.post("/api/auth/token", json={"user_id": "bob", "password": "test-pass"})
    token_b = resp_b.json()["access_token"]
    get_resp = client.get(
        f"/api/research/tasks/{task_id}",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert get_resp.status_code == 403


def test_admin_can_access_any_task():
    """Admin user can access any user's task."""
    # Alice creates task
    resp_a = client.post("/api/auth/token", json={"user_id": "alice", "password": "test-pass"})
    token_a = resp_a.json()["access_token"]
    create = client.post(
        "/api/research/tasks",
        json={"title": "alice task", "query": "x"},
        headers={"Authorization": f"Bearer {token_a}"},
    )
    task_id = create.json()["id"]

    # Admin accesses alice's task
    resp_admin = client.post("/api/auth/token", json={"user_id": "admin", "password": "admin-pass"})
    token_admin = resp_admin.json()["access_token"]
    get_resp = client.get(
        f"/api/research/tasks/{task_id}",
        headers={"Authorization": f"Bearer {token_admin}"},
    )
    assert get_resp.status_code == 200


def test_auth_token_without_header():
    """Requests without auth header should return 401."""
    resp = client.get("/api/research/tasks")
    assert resp.status_code == 401


def test_auth_token_invalid_token():
    """Requests with invalid token should return 401."""
    resp = client.get(
        "/api/research/tasks",
        headers={"Authorization": "Bearer invalidtoken"},
    )
    assert resp.status_code == 401


def test_admin_can_approve_others_papers():
    """Admin user can approve papers on another user's task."""
    # Create as alice
    resp_a = client.post("/api/auth/token", json={"user_id": "alice", "password": "test-pass"})
    token_a = resp_a.json()["access_token"]
    create = client.post(
        "/api/research/tasks",
        json={"title": "x", "query": "y"},
        headers={"Authorization": f"Bearer {token_a}"},
    )
    task_id = create.json()["id"]
    # Submit definition and start
    client.put(
        f"/api/research/tasks/{task_id}/definition",
        json={
            "definition": {
                "research_object": "x",
                "application": "y",
                "target_metrics": [],
                "hard_constraints": [],
                "optimization_objectives": [],
                "acceptable_tradeoffs": [],
            }
        },
        headers={"Authorization": f"Bearer {token_a}"},
    )
    client.post(f"/api/research/tasks/{task_id}/start", headers={"Authorization": f"Bearer {token_a}"})
    client.post(
        f"/api/research/tasks/{task_id}/candidates",
        json={"papers": [{"id": "p1", "title": "Test Paper", "role_tags": ["target_performance"]}]},
        headers={"Authorization": f"Bearer {token_a}"},
    )
    # Admin approves
    resp_adm = client.post("/api/auth/token", json={"user_id": "admin", "password": "admin-pass"})
    token_adm = resp_adm.json()["access_token"]
    approve = client.post(
        f"/api/research/tasks/{task_id}/papers/approve",
        json={"selected_ids": ["p1"]},
        headers={"Authorization": f"Bearer {token_adm}"},
    )
    assert approve.status_code == 200
