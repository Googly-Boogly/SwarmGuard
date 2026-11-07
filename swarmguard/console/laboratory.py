"""Authenticated final laboratory, sharing the console execution semaphore."""
from __future__ import annotations
import asyncio
from typing import Any, Literal
from fastapi import FastAPI, HTTPException, Request, Response
from swarmguard.console.auth import OperatorAuth
from swarmguard.datasets.registry import DatasetDefinition
from swarmguard.models.adversarial import StrictModel
from swarmguard.research.experiment_designer import ExperimentConfig
from swarmguard.research.hypothesis_registry import questions
from swarmguard.research.laboratory import Laboratory
from swarmguard.research.report_generator import generate_report
from swarmguard.research.export import bundle_zip
from swarmguard.research.comparison import ablations
from swarmguard.storage.repositories import PlatformRepository

class ReportRequest(StrictModel):
    sections: list[str] | None = None
    format: Literal['summary', 'full'] = 'summary'

class LaboratoryJobs:
    def __init__(self, lab: Laboratory, worker: asyncio.Semaphore) -> None:
        self.lab, self.worker = lab, worker
        self.tasks: list[asyncio.Task[None]] = []
        lab.datasets.seed()
        lab.recover()
    def start(self, config: ExperimentConfig, actor: str) -> dict[str, Any]:
        self.tasks = [t for t in self.tasks if not t.done()]
        if len(self.tasks) >= 3:
            raise HTTPException(429, 'laboratory queue full')
        record = self.lab.prepare(config, actor)
        async def execute() -> None:
            async with self.worker:
                await self.lab.run(record['experiment_id'])
        self.tasks.append(asyncio.create_task(execute()))
        return record
    async def close(self) -> None:
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)

def register_laboratory_routes(app: FastAPI, auth: OperatorAuth) -> None:
    def read(eid: str) -> dict[str, Any]:
        found = PlatformRepository(app.state.reader).get('experiment', eid)
        if not found:
            raise HTTPException(404, 'experiment not found')
        return found[0]
    @app.get('/api/research/lab/catalog')
    async def catalog(request: Request) -> dict[str, Any]:
        auth.require_read(request, 'RESEARCHER')
        repo = PlatformRepository(app.state.reader)
        records = repo.list('experiment')
        return {'questions': questions(records), 'datasets': repo.list('dataset'),
                'experiments': [{k:v for k,v in r.items() if k not in ('rows','dataset')} for r in records],
                'provider': 'mock', 'live_available': False}
    @app.post('/api/research/lab/experiments', status_code=202)
    async def start(body: ExperimentConfig, request: Request) -> dict[str, Any]:
        session = auth.require(request, 'RESEARCHER')
        try:
            return app.state.laboratory.start(body, session.username)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
    @app.get('/api/research/lab/experiments/{eid}')
    async def detail(eid: str, request: Request) -> dict[str, Any]:
        auth.require_read(request, 'RESEARCHER')
        return read(eid)
    @app.post('/api/research/lab/experiments/{eid}/cancel')
    async def cancel(eid: str, body: ReportRequest, request: Request) -> dict[str, bool]:
        session = auth.require(request, 'RESEARCHER')
        read(eid)
        try:
            app.state.laboratory.lab.cancel(eid, session.username)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None
        return {'cancel_requested': True}
    @app.post('/api/research/lab/datasets')
    async def dataset(body: DatasetDefinition, request: Request) -> dict[str, Any]:
        session = auth.require(request, 'RESEARCHER')
        try:
            return app.state.laboratory.lab.datasets.register(body, session.username)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
    @app.post('/api/research/lab/experiments/{eid}/report')
    async def report(eid: str, body: ReportRequest, request: Request) -> dict[str, Any]:
        auth.require(request, 'RESEARCHER')
        try:
            return generate_report(read(eid), body.sections, concise=body.format == 'summary' and body.sections is None)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
    @app.get('/api/research/lab/experiments/{eid}/bundle')
    async def bundle(eid: str, request: Request) -> Response:
        auth.require_read(request, 'RESEARCHER')
        try:
            return Response(bundle_zip(read(eid)), media_type='application/zip',
                            headers={'Content-Disposition': f'attachment; filename="{eid}.zip"'})
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
    @app.get('/api/research/lab/experiments/{eid}/ablations')
    async def ablation(eid: str, request: Request) -> dict[str, Any]:
        auth.require_read(request, 'RESEARCHER')
        return ablations(read(eid))
