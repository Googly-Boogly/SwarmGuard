"""Agent C: Operations Agent. Drafts emails and requests protected operations."""

from __future__ import annotations

from swarmguard.agents.base import BaseAgent
from swarmguard.models.decisions import GatewayResponse
from swarmguard.security.identity import AgentRole


def parse_brief(text: str) -> dict[str, str]:
    return {k.strip(): v.strip() for k, _, v in (ln.partition(":") for ln in text.splitlines()) if v}


class OperationsAgent(BaseAgent):
    role = AgentRole.OPERATIONS

    async def read_brief(self, workflow_id: str, brief_document_id: str, *,
                         parent_action_ids: list[str] | None = None) -> tuple[GatewayResponse, dict[str, str]]:
        r = await self.read(workflow_id, "read_document", brief_document_id, parent_action_ids=parent_action_ids)
        fields = parse_brief(r.execution_result["content"]["content"]) if r.executed and r.execution_result else {}
        return r, fields

    async def prepare_email(self, workflow_id: str, *, recipient: str, subject: str, body: str,
                            parent_action_ids: list[str] | None = None) -> GatewayResponse:
        return await self.propose(workflow_id, "prepare_email",
                                  {"recipient": recipient, "subject": subject, "body": body},
                                  parent_action_ids=parent_action_ids)

    async def request_send_approval(self, workflow_id: str, email_id: str, version: int, *,
                                    parent_action_ids: list[str] | None = None) -> GatewayResponse:
        return await self.propose(workflow_id, "request_approval", {"operation": "send_email"},
                                  resource_id=email_id, resource_version=version, parent_action_ids=parent_action_ids)

    async def modify_email(self, workflow_id: str, email_id: str, version: int, *,
                           parent_action_ids: list[str] | None = None, **changes: str) -> GatewayResponse:
        return await self.propose(workflow_id, "modify_email", changes, resource_id=email_id,
                                  resource_version=version, parent_action_ids=parent_action_ids)

    async def send_current_draft(self, workflow_id: str, email_id: str, *, approval_id: str | None,
                                 parent_action_ids: list[str] | None = None) -> list[GatewayResponse]:
        """Scripted: read the draft as it is *now*, then request sending exactly that version."""
        draft = await self.read(workflow_id, "read_email_draft", email_id)
        if not draft.executed or draft.execution_result is None:
            return [draft]
        c = draft.execution_result["content"]
        send = await self.propose(
            workflow_id, "send_email", {"recipient": c["recipient"], "subject": c["subject"], "body": c["body"]},
            resource_id=email_id, resource_version=draft.execution_result["version"], approval_id=approval_id,
            parent_action_ids=[*(parent_action_ids or []), draft.action_id],
        )
        return [draft, send]
