"""Trusted task manager: lifecycle view over gateway-created task resources.

* Tasks are created only by the gateway's ``assign_task`` tool. Only the coordinator role holds it;
  dependencies must be existing tasks of the workflow; ``required_tools`` must lie within the
  assignee's permissions. A task is never a permission grant.
* Lifecycle: COMPLETED comes from the task resource (``complete_task``). FAILED/CANCELLED are written
  by trusted code as versioned updates. RUNNING/WAITING are a runtime overlay recorded as events.
  READY/PENDING derive from dependencies.
* ``detect_cycle`` is a defensive check. The gateway only accepts dependencies on existing tasks, so
  a cycle cannot be created through it; any cycle found marks the involved tasks FAILED.
"""

from __future__ import annotations

from swarmguard.agents.workspace import SharedWorkspace
from swarmguard.models.events import EventType
from swarmguard.models.tasks import TERMINAL, TaskRecord, TaskStatus
from swarmguard.models.workflows import ResourceType
from swarmguard.security.workflow_monitor import WorkflowMonitor

TASK_MANAGER = "swarmguard.task_manager"


class InvalidTaskGraph(ValueError):
    pass


def detect_cycle(graph: dict[str, list[str]]) -> list[str] | None:
    """Return one dependency cycle (as a node list) or None. Unknown dependencies raise InvalidTaskGraph."""
    for node, deps in graph.items():
        unknown = [d for d in deps if d not in graph]
        if unknown:
            raise InvalidTaskGraph(f"{node} depends on unknown task(s) {unknown}")
    state: dict[str, int] = {}
    path: list[str] = []

    def visit(n: str) -> list[str] | None:
        state[n] = 1
        path.append(n)
        for d in graph[n]:
            if state.get(d) == 1:
                return path[path.index(d):] + [d]
            if state.get(d) is None and (found := visit(d)):
                return found
        state[n] = 2
        path.pop()
        return None

    for n in sorted(graph):
        if state.get(n) is None and (cycle := visit(n)):
            return cycle
    return None


class TaskManager:
    def __init__(self, monitor: WorkflowMonitor, workspace: SharedWorkspace) -> None:
        self._monitor = monitor
        self._workspace = workspace
        self._overlay: dict[str, TaskStatus] = {}

    def tasks(self, workflow_id: str) -> list[TaskRecord]:
        resources = [r for r in self._workspace.list(ResourceType.TASK, workflow_id)]
        graph = {r.resource_id: list(r.content.get("depends_on_task_ids", [])) for r in resources}
        cyclic = set(detect_cycle(graph) or [])
        stored = {r.resource_id: r for r in resources}

        def base_status(tid: str) -> TaskStatus:
            c = stored[tid].content
            if c.get("status") == "COMPLETED":
                return TaskStatus.COMPLETED
            if c.get("lifecycle") in (TaskStatus.FAILED.value, TaskStatus.CANCELLED.value):
                return TaskStatus(c["lifecycle"])
            if tid in cyclic:
                return TaskStatus.FAILED
            return TaskStatus.READY if all(base_status(d) is TaskStatus.COMPLETED for d in graph[tid]) \
                else TaskStatus.PENDING

        out = []
        for tid in sorted(stored, key=lambda t: (stored[t].created_at, t)):
            status = base_status(tid)
            if status is TaskStatus.READY and tid in self._overlay:
                status = self._overlay[tid]
            c = stored[tid].content
            out.append(TaskRecord(task_id=tid, workflow_id=workflow_id, assigned_by=str(c.get("assigned_by")),
                                  assigned_to=str(c.get("assignee")), description=str(c.get("description", "")),
                                  dependencies=graph[tid], required_tools=list(c.get("required_tools", [])),
                                  status=status, version=stored[tid].version, summary=str(c.get("summary", ""))))
        return out

    def for_agent(self, workflow_id: str, agent_id: str) -> list[TaskRecord]:
        return [t for t in self.tasks(workflow_id) if t.assigned_to == agent_id]

    def mark(self, workflow_id: str, task_id: str, status: TaskStatus, reason: str) -> None:
        """RUNNING/WAITING overlay (event-recorded)."""
        if status not in (TaskStatus.RUNNING, TaskStatus.WAITING):
            raise ValueError("only RUNNING/WAITING are runtime overlays")
        if self._overlay.get(task_id) is status:
            return
        self._overlay[task_id] = status
        self._monitor.record_event(EventType.TASK_STATUS_CHANGED, workflow_id, agent_id=TASK_MANAGER,
                                   resource_id=task_id, summary=f"{task_id} -> {status.value}: {reason}",
                                   details={"status": status.value, "reason": reason})

    def finalize(self, workflow_id: str, task_id: str, status: TaskStatus, reason: str) -> None:
        """FAILED/CANCELLED as a versioned, attributed update of the task resource (trusted)."""
        if status not in (TaskStatus.FAILED, TaskStatus.CANCELLED):
            raise ValueError("finalize accepts FAILED or CANCELLED")
        current = self._workspace.get(task_id)
        if current is None or current.content.get("status") == "COMPLETED":
            return
        self._workspace.update(task_id, {"lifecycle": status.value, "lifecycle_reason": reason}, agent_id=TASK_MANAGER,
                               expected_version=current.version, event_workflow_id=workflow_id, tool="task_manager",
                               enforce_acl=False)
        self._overlay.pop(task_id, None)

    def cancel_open(self, workflow_id: str, reason: str) -> list[str]:
        cancelled = []
        for t in self.tasks(workflow_id):
            if t.status not in TERMINAL:
                self.finalize(workflow_id, t.task_id, TaskStatus.CANCELLED, reason)
                cancelled.append(t.task_id)
        return cancelled
