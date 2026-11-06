"""Shared workspace (Boundary A): agent-editable shared state with versioning.

Holds documents, email drafts, synthetic customer records, task assignments,
and workflow metadata. Agents never receive a ``SharedWorkspace`` reference;
they reach it only through tools that the capability gateway executes.

Every mutation:
1. checks the resource ACL for the responsible agent (defense in depth: the
   policy engine has already checked it),
2. checks the expected version (optimistic concurrency, compare-and-set),
3. applies the change and increments the version,
4. stores an immutable snapshot in ``resource_versions``,
5. records an audit event naming the responsible agent, with a field diff.
"""

from __future__ import annotations

from typing import Any

from swarmguard.clock import Clock
from swarmguard.models.events import EventType, WorkflowEvent
from swarmguard.models.workflows import ResourceACL, ResourceType, ResourceVersion, SharedResource
from swarmguard.security.workflow_monitor import WorkflowMonitor
from swarmguard.storage.database import Database
from swarmguard.storage.repositories import Repositories

_PREFIX = {
    ResourceType.DOCUMENT: "doc",
    ResourceType.EMAIL_DRAFT: "email",
    ResourceType.CUSTOMER_RECORD: "cust",
    ResourceType.TASK: "task",
    ResourceType.WORKFLOW_METADATA: "meta",
}


class WorkspaceError(RuntimeError):
    pass


class ResourceNotFoundError(WorkspaceError, KeyError):
    pass


class ResourceAccessError(WorkspaceError, PermissionError):
    pass


class VersionConflictError(WorkspaceError):
    """The resource changed since the caller read it (stale ``expected_version``)."""


class SharedWorkspace:
    def __init__(self, db: Database, monitor: WorkflowMonitor, clock: Clock, repos: Repositories | None = None) -> None:
        self._db = db
        self._monitor = monitor
        self._clock = clock
        self._repos = repos or monitor.repos

    # ------------------------------------------------------------------ reads

    def get(self, resource_id: str) -> SharedResource | None:
        """Trusted lookup (no ACL, no audit event). Not reachable by agents."""
        return self._repos.resources.get(resource_id)

    def list(self, resource_type: ResourceType | None = None, workflow_id: str | None = None) -> list[SharedResource]:
        return self._repos.resources.list(resource_type, workflow_id)

    def versions(self, resource_id: str) -> list[ResourceVersion]:
        return self._repos.resources.versions(resource_id)

    def get_version(self, resource_id: str, version: int) -> ResourceVersion | None:
        return next((v for v in self.versions(resource_id) if v.version == version), None)

    def read(self, resource_id: str, *, agent_id: str, event_workflow_id: str, tool: str,
             action_id: str | None = None) -> tuple[SharedResource, WorkflowEvent]:
        resource = self._require(resource_id)
        if not resource.acl.can_read(agent_id):
            raise ResourceAccessError(f"{agent_id} may not read {resource_id}")
        event = self._monitor.record_event(
            EventType.RESOURCE_READ, event_workflow_id, agent_id=agent_id, action_id=action_id, tool=tool,
            resource_id=resource_id, resource_version_before=resource.version,
            resource_version_after=resource.version,
            summary=f"{agent_id} read {resource.resource_type.value} {resource_id} v{resource.version}.",
        )
        return resource, event

    # ------------------------------------------------------------------ writes

    def create(
        self,
        resource_type: ResourceType,
        content: dict[str, Any],
        *,
        agent_id: str,
        event_workflow_id: str,
        resource_workflow_id: str | None,
        acl: ResourceACL,
        action_id: str | None = None,
        resource_id: str | None = None,
        tool: str | None = None,
    ) -> tuple[SharedResource, WorkflowEvent]:
        now = self._clock.now()
        with self._db.transaction():
            rid = resource_id or self._db.next_id(_PREFIX[resource_type])
            resource = SharedResource(
                resource_id=rid, resource_type=resource_type, workflow_id=resource_workflow_id, version=1,
                content=dict(content), acl=acl, created_by=agent_id, updated_by=agent_id, created_at=now,
                updated_at=now,
            )
            self._repos.resources.insert(resource)
            event = self._monitor.record_event(
                EventType.RESOURCE_CREATED, event_workflow_id, agent_id=agent_id, action_id=action_id, tool=tool,
                resource_id=rid, resource_version_after=1,
                summary=f"{agent_id} created {resource_type.value} {rid} (v1).",
                details={"content": content},
            )
            self._repos.resources.insert_version(ResourceVersion(
                resource_id=rid, version=1, content=dict(content), agent_id=agent_id, action_id=action_id,
                event_id=event.event_id, created_at=now,
            ))
        return resource, event

    def update(
        self,
        resource_id: str,
        changes: dict[str, Any],
        *,
        agent_id: str,
        expected_version: int,
        event_workflow_id: str,
        action_id: str | None = None,
        tool: str | None = None,
        enforce_acl: bool = True,
    ) -> tuple[SharedResource, dict[str, dict[str, Any]], WorkflowEvent | None]:
        """Versioned compare-and-set update. Returns (resource, diff, event).

        Raises ``VersionConflictError`` on a stale version, so there are no silent overwrites.
        A change that alters nothing returns the unchanged resource without bumping the version.
        """
        with self._db.transaction():
            current = self._require(resource_id)
            if enforce_acl and not current.acl.can_write(agent_id):
                raise ResourceAccessError(f"{agent_id} may not modify {resource_id}")
            if current.version != expected_version:
                raise VersionConflictError(
                    f"{resource_id} is at v{current.version}; caller expected v{expected_version}"
                )
            diff = {k: {"old": current.content.get(k), "new": v} for k, v in changes.items()
                    if current.content.get(k) != v}
            if not diff:
                return current, {}, None
            content = {**current.content, **changes}
            now = self._clock.now()
            if not self._repos.resources.compare_and_set(
                resource_id, expected_version=expected_version, content=content, agent_id=agent_id, at=now
            ):
                raise VersionConflictError(f"{resource_id} changed concurrently")
            new_version = expected_version + 1
            event = self._monitor.record_event(
                EventType.RESOURCE_MODIFIED, event_workflow_id, agent_id=agent_id, action_id=action_id, tool=tool,
                resource_id=resource_id, resource_version_before=expected_version,
                resource_version_after=new_version,
                summary=f"{agent_id} modified {', '.join(sorted(diff))} of {resource_id} "
                f"(v{expected_version} -> v{new_version}).",
                details={"changes": diff},
            )
            self._repos.resources.insert_version(ResourceVersion(
                resource_id=resource_id, version=new_version, content=content, changes=diff, agent_id=agent_id,
                action_id=action_id, event_id=event.event_id, created_at=now,
            ))
        return self._require(resource_id), diff, event

    def _require(self, resource_id: str) -> SharedResource:
        resource = self.get(resource_id)
        if resource is None:
            raise ResourceNotFoundError(resource_id)
        return resource
