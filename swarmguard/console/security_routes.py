"""Stage 7 security operations API (console / control service).

Reads require an authenticated session with a permitted role; every mutation additionally requires the
exact local Origin, JSON, the session CSRF token, the named role, an explicit confirmation and a reason.
The browser never supplies an identity, role or approver: those come from the server-side session.
Without a configured control plane every endpoint returns 503 (no silent fallback).
"""
from __future__ import annotations
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from fastapi import FastAPI, HTTPException, Path as PathParam, Request
from pydantic import Field
from swarmguard.clock import Clock
from swarmguard.console.auth import OperatorAuth
from swarmguard.console.models import Command
from swarmguard.models.actions import ID_PATTERN
from swarmguard.models.events import ActorType, EventType
from swarmguard.security.approval_manager import ApprovalError

READ_ROLES = ("AUDITOR", "ADMINISTRATOR", "OPERATOR")
AUDIT_ROLES = ("AUDITOR", "ADMINISTRATOR")
STALE_AFTER_SECONDS = 30


class Confirmed(Command):
    reason: str = Field(min_length=3, max_length=500)
    confirm: Literal[True]


class Recovery(Command):
    reason: str = Field(min_length=3, max_length=500)
    confirmation_phrase: str = Field(max_length=64)


class CapabilityChange(Confirmed):
    enabled: bool


class SessionRevocation(Confirmed):
    username: str = Field(pattern=ID_PATTERN)


def register_security_routes(app: FastAPI, auth: OperatorAuth, control: Any | None, clock: Clock,
                             checkpoint_dir: Path | None) -> None:
    if control is not None:
        def audit_hook(kind: str, username: str, detail: str) -> None:
            event = EventType.AUTHENTICATION_FAILED if kind == "LOGIN_FAILED" else EventType.OPERATOR_ACTION
            control.monitor.record_event(event, "system_security", agent_id=f"console.{username}"[:64],
                                         actor_type=ActorType.HUMAN, parent_event_ids=[],
                                         summary=f"Console {kind}: {detail}"[:300],
                                         reason_code="AUTHENTICATION_FAILED" if kind == "LOGIN_FAILED" else None)
        auth.audit_hook = audit_hook

    def plane() -> Any:
        if control is None:
            raise HTTPException(503, "control plane not configured for this console")
        return control

    @app.get("/api/v1/security/status")
    async def status(request: Request) -> dict[str, Any]:
        auth.require_read(request, READ_ROLES)
        return plane().status()

    @app.get("/api/v1/security/events")
    async def events(request: Request, limit: int = 200) -> list[dict[str, Any]]:
        auth.require_read(request, READ_ROLES)
        return plane().security_events(max(1, min(limit, 500)))

    @app.get("/api/v1/security/identities")
    async def identities(request: Request) -> dict[str, Any]:
        auth.require_read(request, AUDIT_ROLES)
        return plane().identity_view()

    @app.get("/api/v1/security/operations")
    async def operations(request: Request, state: str | None = None) -> list[dict[str, Any]]:
        auth.require_read(request, READ_ROLES)
        return plane().ops.list(state=state, limit=500)

    @app.get("/api/v1/security/operations/{operation_id}")
    async def operation(request: Request, operation_id: str = PathParam(pattern=ID_PATTERN)) -> dict[str, Any]:
        auth.require_read(request, READ_ROLES)
        detail = plane().operation_detail(operation_id)
        if detail is None:
            raise HTTPException(404, "operation not found")
        return detail

    @app.get("/api/v1/audit/verification")
    async def verification(request: Request) -> dict[str, Any]:
        auth.require_read(request, AUDIT_ROLES)
        try:
            return plane().verify_audit(checkpoint_dir).as_dict()
        except ValueError as exc:   # malformed checkpoint files are reported, never ignored
            return {"status": "FAIL", "failures": [{"kind": "CHECKPOINT_UNREADABLE", "detail": str(exc)}]}

    @app.get("/api/v1/audit/checkpoints")
    async def checkpoints(request: Request) -> list[dict[str, Any]]:
        auth.require_read(request, AUDIT_ROLES)
        from swarmguard.audit.verification import load_checkpoints
        try:
            return load_checkpoints(checkpoint_dir)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None

    @app.get("/api/v1/services/health")
    async def services(request: Request) -> dict[str, Any]:
        auth.require_read(request, READ_ROLES)
        now = clock.now()
        rows = []
        for beat in plane().heartbeats.all():
            age = (now - datetime.fromisoformat(beat["heartbeat_at"])).total_seconds()
            rows.append({"service": beat["service"], "status": beat["status"] if age <= STALE_AFTER_SECONDS else "STALE",
                         "last_heartbeat": beat["heartbeat_at"], "age_seconds": round(age, 1),
                         "recent_failures": {k: v for k, v in (beat["detail"].get("counters") or {}).items()
                                             if "fail" in k or "error" in k}})
        db_ok = plane().db.ping()
        rows.append({"service": "control", "status": "UP" if db_ok else "DEGRADED", "last_heartbeat": now.isoformat(),
                     "age_seconds": 0.0, "recent_failures": {}, "readiness": {
                         "database": "ok" if db_ok else "failed",
                         "migrations": "ok" if db_ok and not plane().db.pending_migrations() else "failed"}})
        return {"services": rows, "stale_after_seconds": STALE_AFTER_SECONDS,
                "note": "Gateway and executor report via database heartbeats; the control service has no route to them."}

    @app.get("/api/v1/services/metrics")
    async def metrics(request: Request) -> dict[str, Any]:
        auth.require_read(request, READ_ROLES)
        return {"services": {b["service"]: b["detail"] for b in plane().heartbeats.all()}, "platform": plane().status()}

    # ------------------------------------------------------------------ administrator controls

    @app.post("/api/v1/security/identities/{identity_id}/revoke")
    async def revoke_identity(body: Confirmed, request: Request,
                              identity_id: str = PathParam(pattern=ID_PATTERN)) -> dict[str, Any]:
        session = auth.require(request, "ADMINISTRATOR")
        try:
            return plane().revoke_identity(identity_id, by=session.username, reason=body.reason)
        except KeyError:
            raise HTTPException(404, "identity not found") from None

    @app.post("/api/v1/security/emergency-stop")
    async def emergency_stop(body: Confirmed, request: Request) -> dict[str, Any]:
        session = auth.require(request, "ADMINISTRATOR")
        return plane().activate_emergency_stop(by=session.username, reason=body.reason)

    @app.post("/api/v1/security/emergency-stop/recover")
    async def emergency_recover(body: Recovery, request: Request) -> dict[str, Any]:
        session = auth.require(request, "ADMINISTRATOR")
        try:
            return plane().recover_emergency_stop(by=session.username, reason=body.reason,
                                                  confirm=body.confirmation_phrase)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None

    @app.post("/api/v1/security/capabilities/{tool}")
    async def capability(body: CapabilityChange, request: Request,
                         tool: str = PathParam(pattern=r"^[a-z][a-z0-9_]{0,63}$")) -> dict[str, Any]:
        session = auth.require(request, "ADMINISTRATOR")
        try:
            return plane().set_capability(tool, enabled=body.enabled, by=session.username, reason=body.reason)
        except KeyError:
            raise HTTPException(404, "unknown capability") from None

    @app.post("/api/v1/security/workflows/{workflow_id}/pause")
    async def pause_workflow(body: Confirmed, request: Request,
                             workflow_id: str = PathParam(pattern=ID_PATTERN)) -> dict[str, bool]:
        session = auth.require(request, "ADMINISTRATOR")
        try:
            plane().pause_workflow(workflow_id, by=session.username, reason=body.reason)
        except KeyError:
            raise HTTPException(404, "workflow not found") from None
        return {"paused": True}

    @app.post("/api/v1/security/sessions/revoke")
    async def revoke_sessions(body: SessionRevocation, request: Request) -> dict[str, int]:
        session = auth.require(request, "ADMINISTRATOR")
        count = auth.revoke_user_sessions(body.username)
        plane()._audit(EventType.OPERATOR_ACTION, session.username,
                       f"Revoked {count} console session(s) of {body.username}: {body.reason}")
        return {"revoked_sessions": count}

    @app.post("/api/v1/audit/checkpoints", status_code=201)
    async def checkpoint(body: Confirmed, request: Request) -> dict[str, Any]:
        session = auth.require(request, AUDIT_ROLES)
        if checkpoint_dir is None:
            raise HTTPException(503, "no external checkpoint directory configured")
        from swarmguard.audit.verification import write_checkpoint
        path = write_checkpoint(plane().db, checkpoint_dir)
        plane()._audit(EventType.OPERATOR_ACTION, session.username, f"Audit checkpoint {path.name} written: {body.reason}")
        return {"checkpoint": path.name}

    # ------------------------------------------------------------------ operator approval revocation

    @app.post("/api/v1/approvals/{approval_id}/revoke")
    async def revoke_approval(body: Confirmed, request: Request,
                              approval_id: str = PathParam(pattern=ID_PATTERN)) -> dict[str, Any]:
        session = auth.require(request, "OPERATOR")
        try:
            return plane().revoke_approval(approval_id, approver_id=session.username, reason=body.reason)
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from None
        except ApprovalError as exc:
            raise HTTPException(409, str(exc)) from None
        except LookupError:
            raise HTTPException(404, "approval not found") from None
