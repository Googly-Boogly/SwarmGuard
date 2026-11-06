"""Trusted message store and router views.

Agents send messages ONLY through the capability gateway's ``send_message`` tool. That path is the
one that verifies the sender (from the credential), the recipient (a workflow participant), the
workflow, the parent message and the size limit, and that writes a DecisionRecord. This class:

* reads inboxes and threads for the runtime (an agent sees only messages addressed to it),
* lets trusted runtime components (interventions) send ``is_system`` messages.

Message text is untrusted data. Nothing here, and nothing an agent receives, changes identities,
permissions, approvals or policy.
"""

from __future__ import annotations

from swarmguard.clock import Clock
from swarmguard.models.events import ActorType, EventType
from swarmguard.models.messages import MAX_MESSAGE_CHARS, AgentMessage, MessageType
from swarmguard.security.workflow_monitor import WorkflowMonitor
from swarmguard.storage.database import Database

SYSTEM_PREFIX = "swarmguard."


class MessageBus:
    def __init__(self, monitor: WorkflowMonitor, db: Database, clock: Clock) -> None:
        self._monitor = monitor
        self._repos = monitor.repos
        self._db = db
        self._clock = clock

    def inbox(self, workflow_id: str, agent_id: str) -> list[AgentMessage]:
        return self._repos.messages.inbox(workflow_id, agent_id)

    def history(self, workflow_id: str) -> list[AgentMessage]:
        return self._repos.messages.for_workflow(workflow_id)

    def get(self, message_id: str) -> AgentMessage | None:
        return self._repos.messages.get(message_id)

    def thread(self, message_id: str) -> list[AgentMessage]:
        chain: list[AgentMessage] = []
        current = self.get(message_id)
        while current is not None and len(chain) < 100:
            chain.append(current)
            current = self.get(current.parent_message_id) if current.parent_message_id else None
        return list(reversed(chain))

    def system_send(self, workflow_id: str, recipient_id: str, message_type: MessageType, text: str, *,
                    source: str, related_action_id: str | None = None) -> AgentMessage:
        """Trusted components only (``source`` must be a ``swarmguard.*`` principal)."""
        if not source.startswith(SYSTEM_PREFIX):
            raise PermissionError("system messages can only come from trusted swarmguard.* components")
        workflow = self._monitor.get_workflow(workflow_id)
        if workflow is None or recipient_id not in workflow.participants:
            raise ValueError(f"{recipient_id} is not a participant of {workflow_id}")
        text = text[:MAX_MESSAGE_CHARS]
        with self._db.transaction():
            message_id = self._db.next_id("message")
            event = self._monitor.record_event(
                EventType.MESSAGE_SENT, workflow_id, agent_id=source, actor_type=ActorType.SYSTEM,
                action_id=related_action_id,
                summary=f"{source} sent system {message_type.value} {message_id} to {recipient_id}.",
                details={"message_id": message_id, "recipient": recipient_id, "message_type": message_type.value,
                         "text": text, "is_system": True})
            message = AgentMessage(message_id=message_id, workflow_id=workflow_id, sender_id=source,
                                   recipient_id=recipient_id, message_type=message_type, text=text,
                                   event_id=event.event_id, is_system=True, created_at=self._clock.now())
            self._repos.messages.insert(message)
        return message
