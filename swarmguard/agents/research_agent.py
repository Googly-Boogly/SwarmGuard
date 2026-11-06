"""Agent B: Research Agent. Reads documents/records and shares structured information."""

from __future__ import annotations

from swarmguard.agents.base import BaseAgent
from swarmguard.models.decisions import GatewayResponse
from swarmguard.security.identity import AgentRole


class ResearchAgent(BaseAgent):
    role = AgentRole.RESEARCH

    async def update_document(self, workflow_id: str, document_id: str, content: str, *,
                              parent_action_ids: list[str] | None = None) -> list[GatewayResponse]:
        """Read the document (to learn its version), then write with that expected version."""
        current = await self.read(workflow_id, "read_document", document_id, parent_action_ids=parent_action_ids)
        if not current.executed or current.execution_result is None:
            return [current]
        update = await self.propose(
            workflow_id, "update_shared_document", {"content": content}, resource_id=document_id,
            resource_version=current.execution_result["version"],
            parent_action_ids=[*(parent_action_ids or []), current.action_id],
        )
        return [current, update]

    async def prepare_customer_brief(self, workflow_id: str, *, customer_id: str, brief_document_id: str,
                                     task_id: str, task_action_id: str) -> list[GatewayResponse]:
        """Scripted: read the customer record, write a structured brief, complete the task."""
        record = await self.read(workflow_id, "read_customer_record", customer_id, parent_action_ids=[task_action_id])
        steps = [record]
        if not record.executed or record.execution_result is None:
            return steps
        c = record.execution_result["content"]
        brief = f"customer_id: {customer_id}\nname: {c['name']}\ncontact: {c['email']}\nrequest: {c['open_request']}"
        steps += await self.update_document(workflow_id, brief_document_id, brief, parent_action_ids=[record.action_id])
        steps.append(await self.propose(
            workflow_id, "complete_task", {"summary": f"Brief written to {brief_document_id}"},
            resource_id=task_id, resource_version=1, parent_action_ids=[task_action_id, steps[-1].action_id],
        ))
        return steps

    async def publish_contact_update(self, workflow_id: str, *, brief_document_id: str, new_contact: str,
                                     parent_action_ids: list[str]) -> list[GatewayResponse]:
        """Scripted: revise the shared brief with a different contact address (an authorized edit)."""
        current = await self.read(workflow_id, "read_document", brief_document_id, parent_action_ids=parent_action_ids)
        if not current.executed or current.execution_result is None:
            return [current]
        lines = [f"contact: {new_contact}" if ln.startswith("contact:") else ln
                 for ln in current.execution_result["content"]["content"].splitlines()]
        update = await self.propose(
            workflow_id, "update_shared_document", {"content": "\n".join(lines)}, resource_id=brief_document_id,
            resource_version=current.execution_result["version"], parent_action_ids=[current.action_id],
        )
        return [current, update]
