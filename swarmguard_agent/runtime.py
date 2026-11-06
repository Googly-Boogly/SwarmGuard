"""Scripted synthetic agent runtime. Emits one JSON object per line describing each observed outcome.

Environment: SWARMGUARD_GATEWAY_URL, SWARMGUARD_AGENT_TOKEN_FILE, SWARMGUARD_WORKFLOW_ID,
SWARMGUARD_EXECUTOR_PROBE_URL (optional; used only to demonstrate that direct access is denied).
"""
from __future__ import annotations
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any
import httpx

EMAIL = {"recipient": "customer@example.test", "subject": "Service Update", "body": "Your request has been received."}


def emit(step: str, **data: Any) -> None:
    print(json.dumps({"step": step, **data}, sort_keys=True), flush=True)


class GatewayClient:
    def __init__(self, base: str, token: str, workflow: str) -> None:
        self.http = httpx.Client(base_url=base.rstrip("/"), timeout=httpx.Timeout(10.0, connect=3.0),
                                 headers={"Authorization": f"Bearer {token}"})
        self.workflow = workflow

    def call(self, tool: str, request_id: str | None = None, **fields: Any) -> tuple[int, dict[str, Any]]:
        body = {"request_id": request_id or f"req_{uuid.uuid4().hex[:16]}", "workflow_id": self.workflow, "tool": tool,
                **fields}
        try:
            r = self.http.post("/api/v1/capabilities/execute", json=body)
        except httpx.TransportError as exc:
            return 0, {"error": type(exc).__name__}
        return r.status_code, r.json() if r.content else {}


def summary(status: int, body: dict[str, Any]) -> dict[str, Any]:
    return {"http": status, "decision": body.get("decision"), "reason_code": body.get("reason_code"),
            "executed": body.get("executed"), "execution_state": body.get("execution_state"),
            "idempotent_replay": body.get("idempotent_replay")}


def main() -> int:
    token = Path(os.environ["SWARMGUARD_AGENT_TOKEN_FILE"]).read_text().strip()
    client = GatewayClient(os.environ["SWARMGUARD_GATEWAY_URL"], token, os.environ.get("SWARMGUARD_WORKFLOW_ID", "wf_remote"))
    run = uuid.uuid4().hex[:8]
    status, body = client.call("read_customer_record", f"read_{run}", resource_id="cust_001")
    emit("read_customer_record", **summary(status, body))
    status, body = client.call("prepare_email", f"draft_{run}", parameters=EMAIL)
    emit("prepare_email", **summary(status, body))
    if not body.get("executed"):
        return 2
    email_id = body["execution_result"]["resource_id"]
    status, body = client.call("send_email", f"noapproval_{run}", resource_id=email_id, resource_version=1, parameters=EMAIL)
    emit("send_without_approval", **summary(status, body))
    status, body = client.call("send_email", f"forged_{run}", resource_id=email_id, resource_version=1, parameters=EMAIL,
                               approval_id="approval_forged_999")
    emit("send_with_forged_approval", **summary(status, body))
    status, body = client.call("request_approval", f"approval_{run}", resource_id=email_id, resource_version=1,
                               parameters={"operation": "send_email"})
    emit("request_approval", **summary(status, body))
    approval_id = (body.get("execution_result") or {}).get("approval_id")
    emit("awaiting_human_approval", approval_id=approval_id)
    deadline, attempt, sent = time.monotonic() + float(os.environ.get("SWARMGUARD_APPROVAL_WAIT", "60")), 0, None
    while time.monotonic() < deadline:
        attempt += 1
        status, body = client.call("send_email", f"send_{run}_{attempt}", resource_id=email_id, resource_version=1,
                                   parameters=EMAIL, approval_id=approval_id)
        if body.get("reason_code") != "APPROVAL_PENDING":
            sent = (f"send_{run}_{attempt}", status, body)
            break
        time.sleep(0.5)
    if sent is None:
        emit("send_with_approval", error="approval not granted in time")
        return 2
    emit("send_with_approval", **summary(sent[1], sent[2]))
    status, body = client.call("send_email", sent[0], resource_id=email_id, resource_version=1, parameters=EMAIL,
                               approval_id=approval_id)
    emit("idempotent_retry_same_request", **summary(status, body))
    status, body = client.call("send_email", f"replay_{run}", resource_id=email_id, resource_version=1, parameters=EMAIL,
                               approval_id=approval_id)
    emit("approval_replay_new_request", **summary(status, body))
    status, body = client.call("read_document", f"spoof_{run}", resource_id="doc_service_faq", agent_id="coordinator")
    emit("identity_spoof_in_body", **summary(status, body))
    probe = os.environ.get("SWARMGUARD_EXECUTOR_PROBE_URL")
    if probe:
        try:
            r = httpx.post(probe.rstrip("/") + "/internal/v1/execute", timeout=3.0,
                           headers={"Authorization": f"Bearer {token}"}, json={"tool": "send_email", "authorized": True})
            emit("direct_executor_access", http=r.status_code, reachable=True,
                 reason_code=(r.json() if r.content else {}).get("reason_code"))
        except httpx.TransportError as exc:
            emit("direct_executor_access", http=None, reachable=False, error=type(exc).__name__)
    env_secrets = sorted(k for k in os.environ if any(s in k for s in ("KEY", "PASSWORD", "SECRET", "DATABASE")))
    emit("agent_environment", secret_like_variables=env_secrets,
         files_in_run_secrets=sorted(os.listdir("/run/secrets")) if os.path.isdir("/run/secrets") else [])
    return 0


if __name__ == "__main__":
    sys.exit(main())
