"""Local console API. Read queries use mode=ro; authenticated commands use trusted services."""
from __future__ import annotations

import asyncio
import hmac
from collections import Counter
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Path as PathParam, Query, Request, Response, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from starlette.middleware.trustedhost import TrustedHostMiddleware

from swarmguard.api import create_app
from swarmguard.clock import Clock, SystemClock
from swarmguard.console.auth import COOKIE, ALLOWED_ORIGINS, OperatorAuth, load_password
from swarmguard.console.catalog import SCENARIOS
from swarmguard.console.jobs import ACTIVE, JobManager, measured_results
from swarmguard.console.models import AgentView, ApprovalDecision, JobRecord, Login, RunConfig, ScenarioView, SessionView
from swarmguard.models.actions import ID_PATTERN
from swarmguard.models.approvals import ApprovalStatus
from swarmguard.models.decisions import DecisionRecord
from swarmguard.models.events import WorkflowEvent
from swarmguard.models.workflows import WorkflowGraph, WorkflowRecord, WorkflowStatus
from swarmguard.security.approval_manager import ApprovalError
from swarmguard.security.policy_engine import DEFAULT_POLICY
from swarmguard.security.workflow_graph import WorkflowGraphService
from swarmguard.security.workflow_monitor import WorkflowMonitor
from swarmguard.storage.database import Database
from swarmguard.storage.repositories import ConsoleJobRepository, ConsoleReadRepository, Repositories, ResearchRepository



class WorkflowView(WorkflowRecord):
    job_id: str | None = None
    execution_status: str
    execution_mode: str
    scenario: str | None = None
    completed_at: str | None = None
    action_count: int
    blocked_count: int
    finding_count: int


class ApprovalView(BaseModel):
    approval_id: str
    workflow_id: str
    operation: str
    resource_id: str
    resource_version: int
    parameters: dict[str, Any]
    requested_by: str
    operation_hash: str
    status: str
    approver_id: str | None
    created_at: str
    expires_at: str | None
    actionable: bool


class Overview(BaseModel):
    registered_agents: int
    active_workflows: int
    completed_workflows: int
    proposed_actions: int
    allowed_actions: int
    blocked_actions: int
    escalated_actions: int
    pending_approvals: int
    safety_findings: int
    evaluation_runs: int
    recent_decisions: list[DecisionRecord]


def create_console_app(db_path: str | Path = "swarmguard_console.db", *,
                       operator_file: Path = Path(".console/operator.json"), encoded_password: str | None = None,
                       secure_cookie: bool = True, clock: Clock | None = None,
                       step_delay: float = .06, code_revision: str = "unavailable",
                       accounts: dict[str, Any] | None = None, control_plane: Any | None = None,
                       checkpoint_dir: Path | None = None, inprocess_execution: bool = True) -> FastAPI:
    path = str(db_path)
    clock = clock or SystemClock()
    if accounts is None and encoded_password is None:
        import os
        from swarmguard.console.auth import load_accounts
        accounts = load_accounts(Path(os.environ.get("SWARMGUARD_OPERATORS_FILE", ".console/operators.json"))) or None
    auth = OperatorAuth(accounts if accounts is not None else
                        (encoded_password if encoded_password is not None else load_password(operator_file)), clock)
    observation = create_app(path)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        writer = Database(path, migrate=inprocess_execution)   # hardened mode: migrations belong to the migrate job
        # Registry snapshots and synthetic fixtures are initialized by the trusted simulator.
        if inprocess_execution:
            from swarmguard.agents.simulator import SwarmSimulator
            initial = SwarmSimulator(path, clock=clock)
            initial.close()
        manager = JobManager(writer, clock, step_delay=step_delay, code_revision=code_revision)
        from swarmguard.console.research import ResearchJobs
        app.state.research = ResearchJobs(writer, clock, manager.worker, code_revision)
        from swarmguard.console.platform import PlatformJobs
        app.state.platform = PlatformJobs(writer, clock, manager.worker, code_revision)
        reader = Database(path, read_only=True)
        app.state.manager, app.state.auth = manager, auth
        app.state.reader, app.state.repos = reader, Repositories(reader)
        app.state.monitor = WorkflowMonitor(reader, clock)
        app.state.live_connections = 0
        async with observation.router.lifespan_context(observation):
            try:
                yield
            finally:
                await app.state.platform.close()
                await app.state.research.close()
                await manager.close()
                reader.close()
                writer.close()
                auth.sessions.clear()

    app = FastAPI(title="SwarmGuard Research Console", version="0.5.0", lifespan=lifespan,
                  description="Loopback research prototype. Mock execution only. Authorization stays in the Capability Gateway.")
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "testserver",
                                                         *[h for h in __import__("os").environ.get("SWARMGUARD_ALLOWED_HOSTS", "").split(",") if h]])
    from swarmguard.console.research import register_research_routes
    register_research_routes(app, auth)
    from swarmguard.console.platform import register_platform_routes
    register_platform_routes(app, auth)
    from swarmguard.console.security_routes import register_security_routes
    app.state.control = control_plane
    register_security_routes(app, auth, control_plane, clock, checkpoint_dir)

    INPROCESS_LAUNCH = ("/api/workflows", "/api/experiments", "/api/jobs/", "/api/research/", "/api/integrations/runs",
                        "/api/evaluation/", "/api/regression/", "/api/platform/")

    @app.middleware("http")
    async def hardened_mode(request: Request, call_next: Any) -> Response:
        # Stage 7 hardened deployment: the control service has no in-process executor. Synthetic research
        # simulators (which execute mock tools in-process) run only in local development mode.
        if not inprocess_execution and request.method == "POST" and request.url.path.startswith(INPROCESS_LAUNCH):
            return Response('{"detail":"in-process synthetic execution is disabled in the hardened deployment"}',
                            status_code=403, media_type="application/json")
        return await call_next(request)

    @app.middleware("http")
    async def browser_headers(request: Request, call_next: Any) -> Response:
        length = request.headers.get("content-length")
        if length and (not length.isdigit() or int(length) > 32_768):
            return Response("request too large", status_code=413)
        # Read and bound chunked bodies too, before validation. No arbitrary uploads supported.
        if request.method not in ("GET", "HEAD"):
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > 32_768:
                    return Response("request too large", status_code=413)
            request._body = bytes(body)
        response = await call_next(request)
        if request.method == "POST" and request.url.path.startswith(("/api/research/", "/api/integrations/", "/api/regression/", "/api/evaluation/", "/api/platform/")) and response.status_code >= 400:
            from swarmguard.storage.repositories import ResearchRepository
            ResearchRepository(app.state.manager.db).audit(
                "research_control", "swarmguard.console", clock.now().isoformat(),
                f"REJECTED_{response.status_code}_{request.url.path[:200]}", 1)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        return response

    def manager() -> JobManager:
        return app.state.manager

    def repos() -> Repositories:
        return app.state.repos

    def require_workflow(wid: str) -> WorkflowRecord:
        wf = repos().workflows.get(wid)
        if wf is None:
            raise HTTPException(404, "workflow not found")
        return wf

    def workflow_job(wid: str) -> JobRecord | None:
        return next((r for r in manager().records.values() if wid in r.workflow_ids), None) or app.state.platform.owners.get(wid)

    def approvals() -> list[ApprovalView]:
        out = []
        for a in repos().approvals.list():
            job = workflow_job(a.workflow_id)
            deadline = a.expires_at or (a.created_at + timedelta(minutes=15))
            wf = repos().workflows.get(a.workflow_id) if control_plane is not None else None
            # Stage 7 deployment workflows are owned by the control plane, not by a console job.
            control_owned = bool(wf and wf.metadata.get("control_plane"))
            out.append(ApprovalView(
                approval_id=a.approval_id, workflow_id=a.workflow_id, operation=a.operation,
                resource_id=a.resource_id, resource_version=a.resource_version, parameters=a.approved_parameters,
                requested_by=a.requested_by, operation_hash=a.operation_hash,
                status="EXPIRED" if a.status == ApprovalStatus.PENDING and deadline <= clock.now() else a.status.value,
                approver_id=a.approver_id, created_at=a.created_at.isoformat(), expires_at=deadline.isoformat(),
                actionable=bool(((job and job.status in {"RUNNING", "WAITING_APPROVAL", "PAUSED"}
                                  and job.config.execution_mode == "MOCK_AUTONOMOUS") or control_owned)
                                and a.status == ApprovalStatus.PENDING and deadline > clock.now())))
        return out

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        repos().workflows.list()
        research = ResearchRepository(app.state.reader).list()
        return {"status": "ok", "stage": "5", "database": "connected", "provider": "mock",
                "model": "deterministic synthetic responder", "live_available": False,
                "ethics_available": True, "transport": "websocket with cursor polling fallback",
                "secure_cookie": secure_cookie, "scope": "local research prototype",
                "active_jobs": sum(j.status in ACTIVE for j in manager().records.values()) +
                    sum(r["status"] in ("QUEUED", "RUNNING", "CANCEL_REQUESTED") for r in research),
                "recent_errors": ([j.error for j in manager().records.values() if j.error] +
                                  [r["error"] for r in research if r.get("error")])[-5:]}

    @app.get("/api/session", response_model=SessionView)
    async def session(request: Request) -> SessionView:
        s = auth.session(request)
        return SessionView(authenticated=bool(s), operator_id=s.username if s else None,
                           csrf_token=s.csrf if s else None, login_available=auth.password is not None,
                           roles=sorted(s.roles) if s else [])

    @app.post("/api/session", response_model=SessionView)
    async def login(body: Login, request: Request, response: Response) -> SessionView:
        token = auth.login(request, body.password, body.username)
        # Rotation prevents session fixation. JavaScript never receives this token.
        old = request.cookies.get(COOKIE)
        if old:
            import hashlib
            auth.sessions.pop(hashlib.sha256(old.encode()).hexdigest(), None)
        response.set_cookie(COOKIE, token, httponly=True, secure=secure_cookie, samesite="strict", max_age=3600, path="/")
        import hashlib
        s = auth.sessions[hashlib.sha256(token.encode()).hexdigest()]
        return SessionView(authenticated=True, operator_id=s.username, csrf_token=s.csrf, login_available=True,
                           roles=sorted(s.roles))

    @app.post("/api/session/logout")
    async def logout(request: Request, response: Response) -> dict[str, bool]:
        auth.logout(request)
        response.delete_cookie(COOKIE, path="/", secure=secure_cookie, httponly=True, samesite="strict")
        return {"logged_out": True}

    @app.get("/api/scenarios", response_model=list[ScenarioView])
    async def scenarios() -> list[ScenarioView]:
        return SCENARIOS

    @app.get("/api/workflows", response_model=list[WorkflowView])
    async def workflows() -> list[WorkflowView]:
        out = []
        for wf in repos().workflows.list():
            job = workflow_job(wf.workflow_id)
            decisions = repos().decisions.for_workflow(wf.workflow_id)
            findings = [f for a in repos().autonomous_assessments.for_workflow(wf.workflow_id) for f in a.get("findings", [])]
            out.append(WorkflowView(**wf.model_dump(), job_id=job.job_id if job else None,
                                    execution_status=job.status if job and wf.workflow_id == job.workflow_ids[-1] else wf.status.value,
                                    execution_mode=job.config.execution_mode if job else "RECORDED",
                                    scenario=job.current_scenario if job else None, completed_at=job.completed_at if job else None,
                                    action_count=len(decisions), blocked_count=sum(d.decision.value == "BLOCK" for d in decisions),
                                    finding_count=len(findings)))
        return out

    @app.get("/api/agents", response_model=list[AgentView])
    async def agents() -> list[AgentView]:
        out = []
        for a in repos().agent_registry.list():
            aid = a["agent_id"]
            history = repos().events.query(agent_id=aid)
            active = next((j for j in manager().records.values() if j.status in ACTIVE and j.workflow_ids), None)
            wf_ids = [w.workflow_id for w in repos().workflows.list() if aid in w.participants]
            status = "IDLE"
            if active and active.workflow_ids[-1] in wf_ids:
                status = active.status if active.status in ("PAUSED", "WAITING_APPROVAL") else (
                    "RUNNING" if active.active_agent == aid else "READY")
            tasks = [r for w in wf_ids for r in repos().resources.list(workflow_id=w)
                     if r.resource_type.value == "task" and r.content.get("assignee") == aid and r.content.get("status") != "COMPLETED"]
            from swarmguard.security.identity import AgentRole
            out.append(AgentView(agent_id=aid, role=a["role"], display_name=a["display_name"],
                                 permissions=sorted(DEFAULT_POLICY.permissions_for(AgentRole(a["role"]))), status=status,
                                 workflow_ids=wf_ids, actions=sum(d.executed for wid in wf_ids for d in repos().decisions.for_workflow(wid) if d.agent_id == aid),
                                 last_activity=history[-1].created_at.isoformat() if history else None,
                                 task=str(tasks[-1].content.get("description")) if tasks and status != "IDLE" else None))
        return out

    @app.get("/api/overview", response_model=Overview)
    async def overview() -> Overview:
        ws = await workflows()
        ds = [d for w in ws for d in repos().decisions.for_workflow(w.workflow_id)]
        counts = Counter(d.decision.value for d in ds)
        return Overview(registered_agents=len(repos().agent_registry.list()),
                        active_workflows=sum(w.execution_status in ACTIVE for w in ws),
                        completed_workflows=sum(w.status == WorkflowStatus.COMPLETED for w in ws),
                        proposed_actions=len(ds), allowed_actions=counts["ALLOW"], blocked_actions=counts["BLOCK"],
                        escalated_actions=counts["ESCALATE"], pending_approvals=sum(a.actionable for a in approvals()),
                        safety_findings=sum(w.finding_count for w in ws),
                        evaluation_runs=sum(j.status == "COMPLETED" for j in manager().records.values()),
                        recent_decisions=sorted(ds, key=lambda d: d.decided_at, reverse=True)[:12])

    @app.get("/api/events", response_model=list[WorkflowEvent])
    async def events(workflow_id: str | None = Query(None, pattern=ID_PATTERN),
                     agent_id: str | None = Query(None, pattern=ID_PATTERN), event_type: str | None = None,
                     search: str = Query("", max_length=200), after_seq: int = Query(0, ge=0),
                     limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0)) -> list[WorkflowEvent]:
        if workflow_id:
            require_workflow(workflow_id)
        return ConsoleReadRepository(app.state.reader).events(workflow_id=workflow_id, agent_id=agent_id,
                    event_type=event_type, search=search, after_seq=after_seq, limit=limit, offset=offset)

    @app.get("/api/decisions", response_model=list[DecisionRecord])
    async def decisions() -> list[DecisionRecord]:
        return [d for w in repos().workflows.list() for d in repos().decisions.for_workflow(w.workflow_id)]

    @app.get("/api/workflows/{workflow_id}/graph", response_model=WorkflowGraph)
    async def graph(workflow_id: str = PathParam(pattern=ID_PATTERN)) -> WorkflowGraph:
        require_workflow(workflow_id)
        result = WorkflowGraphService(app.state.monitor).build_workflow_graph(workflow_id)
        assert result is not None
        return result

    @app.get("/api/workflows/{workflow_id}/records")
    async def records(workflow_id: str = PathParam(pattern=ID_PATTERN)) -> dict[str, Any]:
        require_workflow(workflow_id)
        return {"actions": repos().actions.for_workflow(workflow_id),
                "decisions": repos().decisions.for_workflow(workflow_id),
                "messages": repos().messages.for_workflow(workflow_id),
                "tasks": repos().resources.list(workflow_id=workflow_id, resource_type=None),
                "resource_versions": {rid: repos().resources.versions(rid) for rid in sorted(
                    {r.resource_id for r in repos().resources.list(workflow_id=workflow_id)} |
                    {a.resource_id for a in repos().actions.for_workflow(workflow_id) if a.resource_id})},
                "agent_decisions": repos().agent_decisions.for_workflow(workflow_id),
                "assessments": repos().autonomous_assessments.for_workflow(workflow_id),
                "ethics": repos().ethics.for_workflow(workflow_id),
                "sent_emails": repos().sent_emails.list(workflow_id)}

    @app.get("/api/gateway")
    async def gateway() -> dict[str, Any]:
        return {"policy_version": DEFAULT_POLICY.version,
                "tools": [{"name": t.name, "access": t.access.value, "protected": t.protected,
                           "requires_version": t.requires_version, "requires_approval": t.requires_approval,
                           "description": t.description} for t in DEFAULT_POLICY.tools.values()],
                "role_permissions": {r.value: sorted(p) for r, p in DEFAULT_POLICY.role_permissions.items()},
                "trace_note": "checks_passed are persisted. Unrecorded or subsequent checks have unknown status."}

    @app.get("/api/approvals", response_model=list[ApprovalView])
    async def approval_queue() -> list[ApprovalView]:
        return approvals()

    @app.post("/api/approvals/{approval_id}", response_model=ApprovalView)
    async def decide_approval(body: ApprovalDecision, request: Request, approval_id: str = PathParam(pattern=ID_PATTERN)) -> ApprovalView:
        session = auth.require(request, "OPERATOR")
        a = repos().approvals.get(approval_id)
        if a is None:
            raise HTTPException(404, "approval not found")
        wf_record = repos().workflows.get(a.workflow_id)
        if control_plane is not None and wf_record is not None and wf_record.metadata.get("control_plane"):
            # Stage 7 deployment workflow: the authenticated account is the trusted approver itself.
            try:
                current = control_plane.approvals.get(approval_id)
                if current is None or current.status != ApprovalStatus.PENDING:
                    raise ApprovalError("approval is not pending")
                resource = control_plane.workspace.get(a.resource_id)
                if not resource or resource.version != a.resource_version or any(
                        resource.content.get(k) != v for k, v in current.approved_parameters.items()):
                    raise ApprovalError("resource or protected parameters changed since request")
                if body.decision == "APPROVE":
                    control_plane.grant_approval(approval_id, approver_id=session.username, operation_hash=body.operation_hash)
                else:
                    control_plane.reject_approval(approval_id, approver_id=session.username, reason=body.reason)
            except PermissionError as exc:
                raise HTTPException(403, str(exc)) from None
            except ApprovalError as exc:
                raise HTTPException(409, str(exc)) from None
            return next(r for r in approvals() if r.approval_id == approval_id)
        job = workflow_job(a.workflow_id)
        sim = (manager().simulators.get(job.job_id) or app.state.platform.simulators.get(job.job_id)) if job else None
        if not job or not sim or job.config.execution_mode != "MOCK_AUTONOMOUS" or job.status not in (
                "RUNNING", "WAITING_APPROVAL", "PAUSED"):
            raise HTTPException(409, "approval is read-only: no active interactive runtime owns this request")
        try:
            with sim.db.transaction():
                current = sim.approvals.get(approval_id)
                resource = sim.workspace.get(a.resource_id)
                wf = sim.monitor.get_workflow(a.workflow_id)
                if current is None or current.status != ApprovalStatus.PENDING or wf is None:
                    raise ApprovalError("approval is not pending")
                if current.created_at + timedelta(minutes=15) <= clock.now():
                    raise ApprovalError("approval request expired")
                if not resource or resource.workflow_id != a.workflow_id or resource.version != a.resource_version:
                    raise ApprovalError("resource version changed since request")
                if any(resource.content.get(k) != v for k, v in current.approved_parameters.items()):
                    raise ApprovalError("protected parameters changed since request")
                if not hmac.compare_digest(current.operation_hash, body.operation_hash):
                    raise ApprovalError("reviewed operation hash does not match")
                if body.decision == "APPROVE":
                    sim.operator.approve(sim.operator.review(approval_id))
                else:
                    sim.operator.reject(approval_id, body.reason)
        except ApprovalError as exc:
            raise HTTPException(409, str(exc)) from None
        return next(r for r in approvals() if r.approval_id == approval_id)

    @app.get("/api/jobs", response_model=list[JobRecord])
    async def jobs() -> list[JobRecord]:
        return [JobRecord.model_validate(r) for r in ConsoleJobRepository(app.state.reader).list()]

    @app.get("/api/jobs/{job_id}", response_model=JobRecord)
    async def job(job_id: str = PathParam(pattern=ID_PATTERN)) -> JobRecord:
        return manager().get(job_id)

    @app.post("/api/workflows", response_model=JobRecord, status_code=202)
    async def start_workflow(body: RunConfig, request: Request) -> JobRecord:
        auth.require(request, "RESEARCHER")
        return manager().start(body, "workflow")

    @app.post("/api/experiments", response_model=JobRecord, status_code=202)
    async def start_experiment(body: RunConfig, request: Request) -> JobRecord:
        auth.require(request, "RESEARCHER")
        return manager().start(body, "experiment")

    @app.post("/api/jobs/{job_id}/{command}", response_model=JobRecord)
    async def control_job(request: Request, job_id: str = PathParam(pattern=ID_PATTERN),
                          command: Literal["pause", "resume", "stop"] = "stop") -> JobRecord:
        auth.require(request, "RESEARCHER")
        return await manager().command(job_id, command)

    @app.get("/api/jobs/{job_id}/results")
    async def result(job_id: str = PathParam(pattern=ID_PATTERN)) -> dict[str, Any]:
        return measured_results(manager().get(job_id))

    @app.get("/api/jobs/{job_id}/export")
    async def export(job_id: str = PathParam(pattern=ID_PATTERN)) -> Response:
        import json
        record = manager().get(job_id)
        data = {"job": record.model_dump(mode="json"), "results": measured_results(record),
                "workflows": [{"workflow_id": wid, "events": [e.model_dump(mode="json") for e in
                               repos().events.query(workflow_id=wid)], "records": await records(wid),
                               "graph": (await graph(wid)).model_dump(mode="json")} for wid in record.workflow_ids
                              if repos().workflows.get(wid)]}
        from fastapi.encoders import jsonable_encoder
        return Response(json.dumps(jsonable_encoder(data), indent=2), media_type="application/json",
                        headers={"Content-Disposition": f'attachment; filename="{job_id}.json"'})

    @app.websocket("/ws/workflows/{workflow_id}")
    async def workflow_socket(ws: WebSocket, workflow_id: str, after_seq: int = 0) -> None:
        import re
        if (not auth.session(ws) or ws.headers.get("origin") not in ALLOWED_ORIGINS or
                not re.fullmatch(ID_PATTERN, workflow_id) or after_seq < 0 or
                not repos().workflows.get(workflow_id) or app.state.live_connections >= 8):
            await ws.close(code=1008)
            return
        app.state.live_connections += 1
        try:
            await ws.accept()
            cursor = after_seq
            while True:
                batch = ConsoleReadRepository(app.state.reader).events(workflow_id=workflow_id, after_seq=cursor, limit=100)
                for event in batch:
                    await ws.send_json({"type": "event", "event": event.model_dump(mode="json")})
                    cursor = event.seq
                await ws.send_json({"type": "heartbeat", "last_seq": cursor})
                if not auth.session(ws):
                    await ws.close(code=1008)
                    break
                await asyncio.sleep(.5)
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            app.state.live_connections -= 1

    app.mount("/observe", observation)
    return app
