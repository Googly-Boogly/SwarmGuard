"""Stage 5 controls share the existing operator boundary and single worker."""
from __future__ import annotations
import asyncio
from typing import Any
from fastapi import FastAPI, HTTPException, Request, Query
from swarmguard.clock import Clock
from swarmguard.console.auth import OperatorAuth
from swarmguard.storage.database import Database
from swarmguard.adversarial.adaptive_controller import AdaptiveController
from swarmguard.models.adversarial import ResearchConfig
from swarmguard.console.models import Command
from swarmguard.storage.repositories import ResearchRepository

ACTIVE = {"QUEUED", "RUNNING", "CANCEL_REQUESTED"}


class ResearchJobs:
    def __init__(self, db: Database, clock: Clock, worker: asyncio.Semaphore, revision: str) -> None:
        self.controller = AdaptiveController(db, clock=clock, revision=revision)
        self.repo = self.controller.repo
        self.worker = worker
        self.tasks: dict[str, asyncio.Task[None]] = {}
        # Never recreate credentials or resume any research after a restart.
        for record in self.repo.list():
            if record["status"] in ACTIVE:
                _, version = self.repo.get(record["run_id"])
                record["status"] = "INTERRUPTED"
                record["manifest"]["completed_at"] = clock.now().isoformat()
                self.repo.save(record, expected_version=version, actor="swarmguard.stage5.recovery",
                               occurred_at=clock.now().isoformat())

    def start(self, config: ResearchConfig) -> dict[str, Any]:
        if config.execution_mode != "mock":
            raise HTTPException(422, "Browser live execution is disabled; use the explicitly authorized, priced CLI")
        if len([t for t in self.tasks.values() if not t.done()]) >= 3:
            raise HTTPException(429, "research queue capacity exhausted")
        try:
            record = self.controller.prepare(config)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        rid = record["run_id"]
        self.tasks = {k: t for k, t in self.tasks.items() if not t.done()}
        self.tasks[rid] = asyncio.create_task(self._run(config, rid))
        return record

    async def _run(self, config: ResearchConfig, run_id: str) -> None:
        async with self.worker:
            await self.controller.run(config, run_id=run_id)

    async def close(self) -> None:
        tasks = [t for t in self.tasks.values() if not t.done()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def register_research_routes(app: FastAPI, auth: OperatorAuth) -> None:
    def reader() -> ResearchRepository:
        return ResearchRepository(app.state.reader)

    def authenticated(request: Request) -> None:
        if auth.session(request) is None:
            raise HTTPException(401, "researcher login required")

    def get(run_id: str, kind: str | None = None) -> dict[str, Any]:
        result = reader().get(run_id)
        if result is None or (kind and result[0]["kind"] != kind):
            raise HTTPException(404, "research run not found")
        return result[0]

    @app.get("/api/research/{section}")
    async def runs(section: str, request: Request, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)) -> list[dict[str, Any]]:
        authenticated(request)
        kinds = {"adversarial": "adaptive", "benchmarks": "benchmark", "scaling": "scaling"}
        if section == "failures":
            return [{**f, "run_id": r["run_id"]} for r in reader().list(limit, offset)
                    for c in reader().candidates(r["run_id"]) for f in c.get("failures", [])][:500]
        if section not in kinds:
            raise HTTPException(404, "unknown research section")
        return [r for r in reader().list(limit, offset) if r["kind"] == kinds[section]]

    @app.get("/api/research/adversarial/runs")
    async def adaptive_runs(request: Request) -> list[dict[str, Any]]:
        authenticated(request)
        return [r for r in reader().list() if r["kind"] == "adaptive"]

    @app.get("/api/research/runs/{run_id}")
    @app.get("/api/research/adversarial/runs/{run_id}")
    @app.get("/api/research/benchmarks/{run_id}")
    @app.get("/api/research/scaling/{run_id}")
    async def detail(run_id: str, request: Request) -> dict[str, Any]:
        authenticated(request)
        kind = "benchmark" if "/benchmarks/" in request.url.path else (
            "scaling" if "/scaling/" in request.url.path else None)
        return get(run_id, kind)

    @app.get("/api/research/runs/{run_id}/candidates")
    @app.get("/api/research/adversarial/runs/{run_id}/candidates")
    async def candidates(run_id: str, request: Request) -> list[dict[str, Any]]:
        authenticated(request)
        get(run_id)
        return reader().candidates(run_id)

    @app.get("/api/research/failures/{failure_id}")
    async def failure(failure_id: str, request: Request) -> dict[str, Any]:
        authenticated(request)
        for run in reader().list():
            for candidate in reader().candidates(run["run_id"]):
                found = next((f for f in candidate.get("failures", []) if f["failure_id"] == failure_id), None)
                if found:
                    return {**found, "run_id": run["run_id"], "workflow": candidate}
        raise HTTPException(404, "failure not found")

    @app.post("/api/research/adversarial/runs", status_code=202)
    @app.post("/api/research/benchmarks/runs", status_code=202)
    @app.post("/api/research/scaling/runs", status_code=202)
    async def start(body: ResearchConfig, request: Request) -> dict[str, Any]:
        auth.require(request, "RESEARCHER")
        expected = "adaptive" if "/adversarial/" in request.url.path else (
            "benchmark" if "/benchmarks/" in request.url.path else "scaling")
        if body.kind != expected:
            raise HTTPException(422, "run kind does not match endpoint")
        return app.state.research.start(body)

    @app.post("/api/research/runs/{run_id}/cancel")
    @app.post("/api/research/adversarial/runs/{run_id}/cancel")
    async def cancel(run_id: str, body: Command, request: Request) -> dict[str, bool]:
        auth.require(request, "RESEARCHER")
        get(run_id)
        app.state.research.controller.cancel(run_id)
        return {"cancel_requested": True}

    @app.get("/api/research/runs/{run_id}/export")
    async def export(run_id: str, request: Request) -> dict[str, Any]:
        authenticated(request)
        return {"manifest": get(run_id), "candidates": reader().candidates(run_id)}
