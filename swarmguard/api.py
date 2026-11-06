"""Read-only observability API (local development only).

* Every route is GET and returns a Pydantic response model.
* SQLite is opened in read-only mode (``mode=ro``). Queries are fixed,
  parameterized repository methods; there is no way to submit SQL.
* There are deliberately no endpoints for creating identities, granting
  approvals, changing policy, or executing tools.
* Stage 2 risk endpoints *reconstruct* assessments from the read-only log with the
  same deterministic engine the evaluation uses; nothing is written.
* Evaluation endpoints read JSON files from the results directory. Experiment IDs are
  validated against ``ID_PATTERN``, which blocks path traversal.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from typing import Any

from fastapi import FastAPI, HTTPException, Path as PathParam
from pydantic import BaseModel, Field

from swarmguard import STAGE, __version__
from swarmguard.clock import SystemClock
from swarmguard.config import Settings
from swarmguard.models.actions import ID_PATTERN
from swarmguard.models.decisions import DecisionRecord
from swarmguard.models.events import WorkflowEvent
from swarmguard.ethics.disagreement import DisagreementReport
from swarmguard.evaluation.ethics_comparison import list_ethics_experiments, load_ethics_results
from swarmguard.evaluation.results import list_experiments, load_results
from swarmguard.models.assessments import EthicalAssessment, MonitorFailure
from swarmguard.models.risk import (
    ApprovalRelationship,
    CrossAgentDependency,
    DataFlowTrace,
    PatternFinding,
    SharedResourceModification,
    WorkflowRiskAssessment,
)
from swarmguard.models.workflows import ActionDependencies, WorkflowGraph, WorkflowRecord, WorkflowSummary
from swarmguard.security.workflow_intelligence import WorkflowIntelligenceEngine
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


class WorkflowDependencyReport(BaseModel):
    workflow_id: str
    participating_agents: list[str]
    cross_agent_dependencies: list[CrossAgentDependency]
    shared_resource_modifications: list[SharedResourceModification]
    approval_relationships: list[ApprovalRelationship]
    data_flows: list[DataFlowTrace]


class CouncilEvaluationSummary(BaseModel):
    evaluation_id: str
    action_id: str
    council_type: str
    experiment_id: str | None
    council_recommendation: str
    aggregation_method: str
    disagreement_detected: bool
    n_assessments: int
    failed_monitors: list[str]
    contains_mock_assessments: bool
    advisory: bool = True
    created_at: str


class CouncilAssessments(BaseModel):
    evaluation_id: str
    action_id: str
    council_type: str
    assessments: list[EthicalAssessment]
    failures: list[MonitorFailure]


class CouncilDisagreement(BaseModel):
    evaluation_id: str
    council_type: str
    disagreement: DisagreementReport | None


class EthicsEvaluationSummary(BaseModel):
    experiment_id: str
    executed_at: str | None
    provider: str | None
    model: str | None
    provider_is_synthetic: bool | None
    scenario_count: int | None
    configurations: list[str]
    is_research_result: bool
    note: str


class EvaluationSummary(BaseModel):
    experiment_id: str
    executed_at: str | None
    scenario_count: int | None
    monitor_configurations: list[str]
    is_research_result: bool
    note: str


class EvaluationDetail(BaseModel):
    experiment_id: str
    config: dict[str, Any]
    metadata: dict[str, Any]
    is_research_result: bool
    note: str
    predictions: list[dict[str, Any]]
    enforcement: list[dict[str, Any]]
    ground_truth: list[dict[str, Any]] = Field(description="Hand-written labels, joined after prediction.")


def create_app(db_path: str | Path | None = None, results_dir: str | Path | None = None) -> FastAPI:
    path = str(db_path or Settings.from_env().db_path)
    results_root = Path(results_dir or Settings.from_env().results_dir)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        from swarmguard.storage.database import is_postgres_url
        if not is_postgres_url(path) and not Path(path).exists():
            Database(path).close()  # create an empty schema so the read-only open succeeds
        db = Database(path, read_only=True)
        app.state.monitor = WorkflowMonitor(db, SystemClock())
        app.state.graph = WorkflowGraphService(app.state.monitor)
        app.state.intel = WorkflowIntelligenceEngine(app.state.monitor)
        try:
            yield
        finally:
            db.close()

    app = FastAPI(title="SwarmGuard Observability", version=__version__, lifespan=lifespan,
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

    # ------------------------------------------------------------------ Stage 2: workflow risk (read-only)

    async def analyze(workflow_id: str) -> WorkflowRiskAssessment:
        require(workflow_id)
        return await app.state.intel.analyze_workflow(workflow_id)

    @app.get("/workflows/{workflow_id}/risk", response_model=WorkflowRiskAssessment)
    async def workflow_risk(workflow_id: str = WorkflowId) -> WorkflowRiskAssessment:
        return await analyze(workflow_id)

    @app.get("/workflows/{workflow_id}/patterns", response_model=list[PatternFinding])
    async def workflow_patterns(workflow_id: str = WorkflowId) -> list[PatternFinding]:
        return (await analyze(workflow_id)).findings

    @app.get("/workflows/{workflow_id}/dependencies", response_model=WorkflowDependencyReport)
    async def workflow_dependencies(workflow_id: str = WorkflowId) -> WorkflowDependencyReport:
        a = await analyze(workflow_id)
        return WorkflowDependencyReport(
            workflow_id=a.workflow_id, participating_agents=a.participating_agents,
            cross_agent_dependencies=a.cross_agent_dependencies,
            shared_resource_modifications=a.shared_resource_modifications,
            approval_relationships=a.approval_relationships, data_flows=a.data_flows)

    # ------------------------------------------------------------------ Stage 3: recorded ethics councils (read-only)
    # These endpoints only READ previously recorded results. Nothing here can trigger an LLM call.

    def councils(workflow_id: str):  # noqa: ANN202
        require(workflow_id)
        return monitor().get_council_evaluations(workflow_id)

    @app.get("/workflows/{workflow_id}/ethics", response_model=list[CouncilEvaluationSummary])
    def workflow_ethics(workflow_id: str = WorkflowId) -> list[CouncilEvaluationSummary]:
        return [CouncilEvaluationSummary(
            evaluation_id=r.evaluation_id, action_id=r.action_id, council_type=r.council_type,
            experiment_id=r.experiment_id, council_recommendation=r.report.council_recommendation.value,
            aggregation_method=r.report.aggregation_method, disagreement_detected=r.report.disagreement_detected,
            n_assessments=len(r.report.assessments), failed_monitors=r.report.failed_monitors,
            contains_mock_assessments=r.report.contains_mock_assessments, created_at=r.created_at.isoformat(),
        ) for r in councils(workflow_id)]

    @app.get("/workflows/{workflow_id}/ethics/assessments", response_model=list[CouncilAssessments])
    def workflow_ethics_assessments(workflow_id: str = WorkflowId) -> list[CouncilAssessments]:
        return [CouncilAssessments(evaluation_id=r.evaluation_id, action_id=r.action_id, council_type=r.council_type,
                                   assessments=r.report.assessments, failures=r.report.failures)
                for r in councils(workflow_id)]

    @app.get("/workflows/{workflow_id}/ethics/disagreement", response_model=list[CouncilDisagreement])
    def workflow_ethics_disagreement(workflow_id: str = WorkflowId) -> list[CouncilDisagreement]:
        return [CouncilDisagreement(
            evaluation_id=r.evaluation_id, council_type=r.council_type,
            disagreement=DisagreementReport.model_validate(r.report.disagreement) if r.report.disagreement else None)
            for r in councils(workflow_id)]

    ethics_root = results_root / "stage3"

    @app.get("/evaluations/ethics", response_model=list[EthicsEvaluationSummary])
    def ethics_evaluations() -> list[EthicsEvaluationSummary]:
        out = []
        for eid in list_ethics_experiments(ethics_root):
            cfg = load_ethics_results(ethics_root, eid)["experiment_config"]
            meta = cfg["metadata"]
            out.append(EthicsEvaluationSummary(
                experiment_id=eid, executed_at=meta.get("executed_at"), provider=meta["provider"].get("provider"),
                model=meta.get("model"), provider_is_synthetic=meta.get("provider_is_synthetic"),
                scenario_count=meta.get("scenario_count"), configurations=cfg["config"].get("configurations", []),
                is_research_result=cfg.get("is_research_result", False), note=cfg.get("note", "")))
        return out

    @app.get("/evaluations/ethics/{experiment_id}", response_model=dict[str, Any])
    def ethics_evaluation(experiment_id: str = PathParam(pattern=ID_PATTERN)) -> dict[str, Any]:
        try:
            return load_ethics_results(ethics_root, experiment_id)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="experiment not found") from None

    # ------------------------------------------------------------------ Stage 2: evaluations (read-only files)

    def load(experiment_id: str):  # noqa: ANN202
        if experiment_id not in list_experiments(results_root):
            raise HTTPException(status_code=404, detail="experiment not found")
        return load_results(results_root, experiment_id)

    @app.get("/evaluations", response_model=list[EvaluationSummary])
    def evaluations() -> list[EvaluationSummary]:
        out = []
        for eid in list_experiments(results_root):
            r = load_results(results_root, eid)
            out.append(EvaluationSummary(
                experiment_id=eid, executed_at=r.metadata.get("executed_at"),
                scenario_count=r.metadata.get("scenario_count"),
                monitor_configurations=list(r.config.get("monitor_configurations", [])),
                is_research_result=r.is_research_result, note=r.note))
        return out

    @app.get("/evaluations/{experiment_id}", response_model=EvaluationDetail)
    def evaluation(experiment_id: str = PathParam(pattern=ID_PATTERN)) -> EvaluationDetail:
        r = load(experiment_id)
        return EvaluationDetail(
            experiment_id=experiment_id, config=r.config, metadata=r.metadata,
            is_research_result=r.is_research_result, note=r.note,
            predictions=[p.model_dump(mode="json") for p in r.predictions],
            enforcement=[e.model_dump(mode="json") for e in r.enforcement], ground_truth=r.ground_truth)

    @app.get("/evaluations/{experiment_id}/metrics", response_model=dict[str, Any])
    def evaluation_metrics(experiment_id: str = PathParam(pattern=ID_PATTERN)) -> dict[str, Any]:
        return load(experiment_id).metrics

    return app
