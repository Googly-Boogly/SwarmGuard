"""Read-only observability API (local development only).

* Every route is GET and returns a Pydantic response model.
* SQLite is opened in read-only mode (``mode=ro``). Queries are fixed,
  parameterized repository methods; there is no way to submit SQL.
* There are deliberately no endpoints for creating identities, granting
  approvals, changing policy, or executing tools.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Path as PathParam
from pydantic import BaseModel

from swarmguard import STAGE, __version__
from swarmguard.clock import SystemClock
from swarmguard.config import Settings
from swarmguard.models.actions import ID_PATTERN
from swarmguard.models.decisions import DecisionRecord
from swarmguard.models.events import WorkflowEvent
from swarmguard.models.workflows import ActionDependencies, WorkflowGraph, WorkflowRecord, WorkflowSummary
from swarmguard.security.workflow_graph import WorkflowGraphService
from swarmguard.security.workflow_monitor import WorkflowMonitor
from swarmguard.storage.database import Database

WorkflowId = PathParam(pattern=ID_PATTERN)


class HealthResponse(BaseModel):
    status: str
    version: str
    stage: str
    database: str
    read_only: bool
    api_key_required: bool = False


def create_app(db_path: str | Path | None = None) -> FastAPI:
    path = str(db_path or Settings.from_env().db_path)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if not Path(path).exists():
            Database(path).close()  # create an empty schema so the read-only open succeeds
        db = Database(path, read_only=True)
        app.state.monitor = WorkflowMonitor(db, SystemClock())
        app.state.graph = WorkflowGraphService(app.state.monitor)
        try:
            yield
        finally:
            db.close()

    app = FastAPI(title="SwarmGuard (Stage 1)", version=__version__, lifespan=lifespan,
                  description="Read-only observability for the SwarmGuard multi-agent simulation.")

    def monitor() -> WorkflowMonitor:
        return app.state.monitor

    def require(workflow_id: str) -> WorkflowRecord:
        record = monitor().get_workflow(workflow_id)
        if record is None:
            raise HTTPException(status_code=404, detail="workflow not found")
        return record

    @app.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(status="ok", version=__version__, stage=STAGE, database=path, read_only=True)

    @app.get("/workflows", response_model=list[WorkflowRecord])
    def list_workflows() -> list[WorkflowRecord]:
        return monitor().list_workflows()

    @app.get("/workflows/{workflow_id}", response_model=WorkflowRecord)
    def get_workflow(workflow_id: str = WorkflowId) -> WorkflowRecord:
        return require(workflow_id)

    @app.get("/workflows/{workflow_id}/events", response_model=list[WorkflowEvent])
    def workflow_events(workflow_id: str = WorkflowId) -> list[WorkflowEvent]:
        require(workflow_id)
        return monitor().get_workflow_history(workflow_id)

    @app.get("/workflows/{workflow_id}/decisions", response_model=list[DecisionRecord])
    def workflow_decisions(workflow_id: str = WorkflowId) -> list[DecisionRecord]:
        require(workflow_id)
        return monitor().get_decisions(workflow_id)

    @app.get("/workflows/{workflow_id}/graph", response_model=WorkflowGraph)
    def workflow_graph(workflow_id: str = WorkflowId) -> WorkflowGraph:
        require(workflow_id)
        graph = app.state.graph.build_workflow_graph(workflow_id)
        assert graph is not None
        return graph

    @app.get("/workflows/{workflow_id}/summary", response_model=WorkflowSummary)
    def workflow_summary(workflow_id: str = WorkflowId) -> WorkflowSummary:
        require(workflow_id)
        summary = monitor().get_workflow_summary(workflow_id)
        assert summary is not None
        return summary

    @app.get("/actions/{action_id}/dependencies", response_model=ActionDependencies)
    def action_dependencies(action_id: str = PathParam(pattern=ID_PATTERN)) -> ActionDependencies:
        deps = app.state.graph.get_action_dependencies(action_id)
        if deps is None:
            raise HTTPException(status_code=404, detail="action not found")
        return deps

    return app
