"""FastAPI routes for the deterministic research workflow."""

from __future__ import annotations

import os
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from workflow_engine import (
    InvalidTransitionError,
    NotFoundError,
    PermissionDeniedError,
    WorkflowError,
    WorkflowStore,
)


DEFAULT_DB_PATH = os.environ.get(
    "WORKFLOW_DB_PATH",
    os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "workflow.db"),
)


class TaskCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    query: str = Field(min_length=1)
    definition: dict[str, Any] = Field(default_factory=dict)


class DefinitionUpdate(BaseModel):
    definition: dict[str, Any]
    coverage: dict[str, int] | None = None


class PaperCandidate(BaseModel):
    id: str | None = None
    work_id: str | None = None
    title: str
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    abstract: str = ""
    doi: str = ""
    source: str = ""
    document_type: str = ""
    url: str = ""
    language: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    role_tags: list[str] = Field(default_factory=list)
    relevance_score: float | None = Field(default=None, ge=0, le=100)
    authority_score: float | None = Field(default=None, ge=0, le=100)
    confidence_score: float | None = Field(default=None, ge=0, le=100)
    evidence_level: str = "abstract_only"
    fulltext_status: str = "unknown"


class CandidateBatch(BaseModel):
    papers: list[PaperCandidate]


class PaperApproval(BaseModel):
    selected_ids: list[str]


class ExtractionCreate(BaseModel):
    paper_id: str
    payload: dict[str, Any]
    source_type: str = Field(pattern="^(explicit|derived|estimated|inferred|missing)$")
    confidence_score: float = Field(ge=0, le=100)


class DataReview(BaseModel):
    approved: bool
    notes: str = ""


class TransitionRequest(BaseModel):
    target: str


def _is_admin(user: dict) -> bool:
    return user.get("role") == "admin" or user.get("is_admin") is True


def _handle_error(exc: WorkflowError) -> HTTPException:
    if isinstance(exc, NotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, PermissionDeniedError):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, InvalidTransitionError):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


def create_workflow_router(
    auth_dependency: Callable,
    store: WorkflowStore | None = None,
) -> APIRouter:
    store = store or WorkflowStore(DEFAULT_DB_PATH)
    router = APIRouter(prefix="/api/research", tags=["research-workflow"])

    @router.post("/tasks", status_code=201)
    async def create_task(req: TaskCreate, user: dict = Depends(auth_dependency)):
        return store.create_task(user["sub"], req.title, req.query, req.definition)

    @router.get("/tasks")
    async def list_tasks(user: dict = Depends(auth_dependency)):
        return {"tasks": store.list_tasks(user["sub"], _is_admin(user))}

    @router.get("/tasks/{task_id}")
    async def get_task(task_id: str, user: dict = Depends(auth_dependency)):
        try:
            task = store.get_task(task_id, user["sub"], _is_admin(user))
            return {
                "task": task,
                "papers": store.list_papers(task_id, user["sub"], _is_admin(user)),
                "extractions": store.list_extractions(task_id, user["sub"], _is_admin(user)),
            }
        except WorkflowError as exc:
            raise _handle_error(exc) from exc

    @router.put("/tasks/{task_id}/definition")
    async def update_definition(
        task_id: str, req: DefinitionUpdate, user: dict = Depends(auth_dependency)
    ):
        try:
            return store.update_definition(task_id, user["sub"], req.definition, req.coverage)
        except WorkflowError as exc:
            raise _handle_error(exc) from exc

    @router.post("/tasks/{task_id}/start")
    async def start_task(task_id: str, user: dict = Depends(auth_dependency)):
        try:
            return store.start_search(task_id, user["sub"])
        except WorkflowError as exc:
            raise _handle_error(exc) from exc

    @router.post("/tasks/{task_id}/candidates")
    async def submit_candidates(
        task_id: str, req: CandidateBatch, user: dict = Depends(auth_dependency)
    ):
        try:
            return store.submit_candidates(
                task_id, user["sub"], [paper.model_dump() for paper in req.papers]
            )
        except WorkflowError as exc:
            raise _handle_error(exc) from exc

    @router.get("/tasks/{task_id}/papers")
    async def list_papers(task_id: str, user: dict = Depends(auth_dependency)):
        try:
            return {"papers": store.list_papers(task_id, user["sub"], _is_admin(user))}
        except WorkflowError as exc:
            raise _handle_error(exc) from exc

    @router.post("/tasks/{task_id}/papers/approve")
    async def approve_papers(
        task_id: str, req: PaperApproval, user: dict = Depends(auth_dependency)
    ):
        try:
            return store.approve_papers(task_id, user["sub"], req.selected_ids, is_admin=_is_admin(user))
        except WorkflowError as exc:
            raise _handle_error(exc) from exc

    @router.post("/tasks/{task_id}/extractions", status_code=201)
    async def record_extraction(
        task_id: str, req: ExtractionCreate, user: dict = Depends(auth_dependency)
    ):
        try:
            return store.record_extraction(
                task_id, user["sub"], req.paper_id, req.payload,
                req.source_type, req.confidence_score,
            )
        except WorkflowError as exc:
            raise _handle_error(exc) from exc

    @router.post("/tasks/{task_id}/review/request")
    async def request_review(task_id: str, user: dict = Depends(auth_dependency)):
        try:
            return store.request_data_review(task_id, user["sub"])
        except WorkflowError as exc:
            raise _handle_error(exc) from exc

    @router.post("/tasks/{task_id}/review")
    async def review_data(
        task_id: str, req: DataReview, user: dict = Depends(auth_dependency)
    ):
        try:
            return store.review_extractions(
                task_id, user["sub"], req.approved, _is_admin(user), req.notes
            )
        except WorkflowError as exc:
            raise _handle_error(exc) from exc

    @router.post("/tasks/{task_id}/advance")
    async def advance(
        task_id: str, req: TransitionRequest, user: dict = Depends(auth_dependency)
    ):
        try:
            return store.advance(task_id, user["sub"], req.target)
        except (WorkflowError, ValueError) as exc:
            raise _handle_error(InvalidTransitionError(str(exc))) from exc

    @router.post("/tasks/{task_id}/pause")
    async def pause(task_id: str, user: dict = Depends(auth_dependency)):
        try:
            return store.pause(task_id, user["sub"])
        except WorkflowError as exc:
            raise _handle_error(exc) from exc

    @router.post("/tasks/{task_id}/resume")
    async def resume(task_id: str, user: dict = Depends(auth_dependency)):
        try:
            return store.resume(task_id, user["sub"])
        except WorkflowError as exc:
            raise _handle_error(exc) from exc

    @router.post("/tasks/{task_id}/rollback")
    async def rollback(
        task_id: str, req: TransitionRequest, user: dict = Depends(auth_dependency)
    ):
        try:
            return store.rollback(task_id, user["sub"], req.target)
        except (WorkflowError, ValueError) as exc:
            raise _handle_error(InvalidTransitionError(str(exc))) from exc

    @router.get("/tasks/{task_id}/events")
    async def audit_events(task_id: str, user: dict = Depends(auth_dependency)):
        try:
            return {"events": store.events(task_id, user["sub"], _is_admin(user))}
        except WorkflowError as exc:
            raise _handle_error(exc) from exc

    return router

