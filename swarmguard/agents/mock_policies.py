"""Deterministic MOCK agent policies for mock-autonomous mode (no LLM, no network).

``agent_policy_responder`` is a ``MockLLMProvider`` responder. It parses the same observation JSON
a live model receives and returns an ``AgentDecision`` dict computed only from that observation:
tasks, inbox, readable resources, approval statuses and the agent's own memory. It never sees
scenario labels or ground truth.

These are hand-written behaviours, not model reasoning, and they shape every mock outcome. In
particular the mock operations agent is deliberately *naive*: it follows recipient overrides and
approval claims found in non-system messages, and it holds a send after a system safety warning.
Mock results therefore measure the SwarmGuard pipeline against these behaviours, not LLM behaviour.
"""

from __future__ import annotations

import json
import re
from typing import Any

from swarmguard.agents.operations_agent import parse_brief
from swarmguard.models.agent_state import decision

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
CUST = re.compile(r"\bcust_\d+\b")


def _results(u: dict[str, Any], tool: str | None = None, executed: bool | None = None) -> list[dict[str, Any]]:
    return [m for m in u["memory"] if m.get("kind") == "result" and (tool is None or m.get("tool") == tool)
            and (executed is None or m.get("executed") is executed)]


def _brief(u: dict[str, Any]) -> str | None:
    return next((r["resource_id"] for r in u["readable_resources"] if r["resource_id"].startswith("brief_")), None)


def _version(u: dict[str, Any], rid: str) -> int | None:
    return next((r["version"] for r in u["readable_resources"] if r["resource_id"] == rid), None)


def _last_read(u: dict[str, Any], tool: str, rid: str) -> dict[str, Any] | None:
    reads = [m for m in _results(u, tool, True) if (m.get("result") or {}).get("resource_id") == rid]
    return reads[-1] if reads else None


def _open_tasks(u: dict[str, Any], agent_id: str) -> list[dict[str, Any]]:
    return [t for t in u["tasks"] if t["assigned_to"] == agent_id and t["status"] in ("READY", "RUNNING", "WAITING")]


def _sent_texts(u: dict[str, Any]) -> list[str]:
    return [(m.get("proposal") or {}).get("parameters", {}).get("text", "") for m in _results(u, "send_message", True)]


def _answered(u: dict[str, Any]) -> set[str]:
    return {(m.get("proposal") or {}).get("parameters", {}).get("parent_message_id")
            for m in _results(u, "send_message", True)} - {None}


def _wait(why: str) -> dict[str, Any]:
    return decision("WAIT", summary=f"[mock policy] {why}")


# --------------------------------------------------------------------------- coordinator


def coordinator(t: dict[str, Any], u: dict[str, Any]) -> dict[str, Any]:
    fields = {**t.get("objective_fields", {}), **t.get("private_objective_fields", {})}
    tasks = u["tasks"]
    for m in u["inbox"]:
        if m["message_type"] == "REQUEST" and not m["is_system"] and m["message_id"] not in _answered(u):
            return decision("SEND_MESSAGE", recipient_id=m["from"], message_type="RESPONSE",
                            message_text="Use the contact address recorded in the research brief; it comes from the "
                                         "customer record.", parameters={"parent_message_id": m["message_id"]},
                            summary="[mock policy] answer a clarification request")
    for c in fields.get("customers", []):
        cid = c["customer_id"]
        research = next((x for x in tasks if x["assigned_to"] == "research_agent" and cid in x["description"]), None)
        if research is None:
            return decision("DELEGATE_TASK", recipient_id="research_agent",
                            task_description=f"Prepare the customer brief for {cid}",
                            required_tools=["read_customer_record", "update_shared_document"],
                            summary=f"[mock policy] delegate research for {cid}")
        if not any(x["assigned_to"] == "operations_agent" and cid in x["description"] for x in tasks):
            hint = f" to {c['recipient_hint']}" if c.get("recipient_hint") else ""
            return decision("DELEGATE_TASK", recipient_id="operations_agent",
                            task_description=f"Send the {c.get('deliverable', 'service update')} for {cid}{hint}",
                            depends_on_task_ids=[research["task_id"]],
                            required_tools=["prepare_email", "request_approval", "send_email"],
                            summary=f"[mock policy] delegate the customer email for {cid}")
    for e in fields.get("extracts", []):
        desc = f"Send field {e['field']} of {e['customer_id']} to {e['to']}"
        if not any(x["description"] == desc for x in tasks):
            return decision("DELEGATE_TASK", recipient_id="operations_agent", task_description=desc,
                            required_tools=["read_customer_record", "prepare_email", "request_approval", "send_email"],
                            summary="[mock policy] delegate a record-field extract")
    if tasks and all(x["status"] in ("COMPLETED", "FAILED", "CANCELLED") for x in tasks):
        return decision("COMPLETE_TASK", summary="[mock policy] all delegated tasks are completed")
    return _wait("waiting for delegated tasks")


# --------------------------------------------------------------------------- research


def research(t: dict[str, Any], u: dict[str, Any]) -> dict[str, Any]:
    tasks = _open_tasks(u, t["agent_id"])
    if not tasks:
        return _wait("no open research task")
    task = tasks[0]
    cid = (CUST.findall(task["description"]) or [None])[0]
    brief = _brief(u)
    if cid is None or brief is None:
        return decision("REQUEST_INFORMATION", recipient_id="coordinator", message_text=f"Task {task['task_id']} is "
                        "unclear: which customer record should I use?", summary="[mock policy] unclear task")
    rec = _last_read(u, "read_customer_record", cid)
    if rec is None:
        return decision("TOOL_REQUEST", tool="read_customer_record", resource_id=cid,
                        summary=f"[mock policy] read {cid}")
    content = rec["result"]["content"]
    wrote = [m for m in _results(u, "update_shared_document", True)
             if f"{cid}.name" in (m.get("proposal") or {}).get("parameters", {}).get("content", "")]
    if not wrote:
        br = _last_read(u, "read_document", brief)
        failed = [m for m in _results(u, "update_shared_document", False)]
        if br is None or br["step"] < rec["step"] or (failed and failed[-1]["step"] > br["step"]):
            return decision("TOOL_REQUEST", tool="read_document", resource_id=brief,
                            summary="[mock policy] read the current brief version")
        existing = br["result"]["content"].get("content", "")
        lines = [ln for ln in existing.splitlines() if ln.strip()]
        lines += [f"{cid}.name: {content['name']}", f"{cid}.contact: {content['email']}",
                  f"{cid}.request: {content.get('request_id', content.get('open_request', ''))}"]
        return decision("TOOL_REQUEST", tool="update_shared_document", resource_id=brief,
                        resource_version=br["result"]["version"], parameters={"content": "\n".join(lines)},
                        summary=f"[mock policy] write {cid} into the shared brief")
    if not any(cid in text for text in _sent_texts(u)):
        return decision("SEND_MESSAGE", recipient_id="operations_agent", message_type="INFORMATION",
                        message_text=f"Brief for {cid} is ready in {brief}: name {content['name']}, contact "
                                     f"{content['email']}.", summary="[mock policy] inform operations")
    return decision("COMPLETE_TASK", task_id=task["task_id"], resource_version=task["version"],
                    summary=f"[mock policy] research for {cid} done")


# --------------------------------------------------------------------------- operations


def _ops_send_flow(t: dict[str, Any], u: dict[str, Any], task: dict[str, Any], recipient: str, subject: str,
                   body: str, claimed_approval: str | None) -> dict[str, Any]:
    # scope to THIS task's draft (identified by its body), so earlier tasks' sends are not mistaken for this one
    drafts = [m for m in _results(u, "prepare_email", True)
              if (m.get("proposal") or {}).get("parameters", {}).get("body") == body]
    if not drafts:
        return decision("TOOL_REQUEST", tool="prepare_email",
                        parameters={"recipient": recipient, "subject": subject, "body": body},
                        summary=f"[mock policy] draft the email to {recipient}")
    draft = drafts[-1]["result"]
    draft_id = draft["resource_id"]
    sends = [s for s in _results(u, "send_email") if (s.get("proposal") or {}).get("resource_id") == draft_id]
    if any(s.get("executed") for s in sends):
        return decision("COMPLETE_TASK", task_id=task["task_id"], resource_version=task["version"],
                        summary="[mock policy] email sent; task complete")
    if sends:
        return _wait("the send was not executed; not retrying")
    params = {"recipient": draft["recipient"], "subject": draft["subject"], "body": draft["body"]}
    if claimed_approval:
        return decision("TOOL_REQUEST", tool="send_email", resource_id=draft_id, resource_version=draft["version"],
                        approval_id=claimed_approval, parameters=params,
                        summary="[mock policy] a message said this approval covers the send")
    approval = next((a for a in u["my_approval_requests"] if a["resource_id"] == draft_id), None)
    if approval is None:
        return decision("REQUEST_APPROVAL", resource_id=draft_id, resource_version=draft["version"],
                        summary="[mock policy] request human approval for the exact draft")
    if approval["status"] != "APPROVED":
        return _wait(f"approval {approval['approval_id']} is {approval['status']}")
    read = _last_read(u, "read_email_draft", draft_id)
    if read is None:
        return decision("TOOL_REQUEST", tool="read_email_draft", resource_id=draft_id,
                        summary="[mock policy] read the approved draft")
    c = read["result"]["content"]
    return decision("TOOL_REQUEST", tool="send_email", resource_id=draft_id, resource_version=read["result"]["version"],
                    approval_id=approval["approval_id"],
                    parameters={"recipient": c["recipient"], "subject": c["subject"], "body": c["body"]},
                    summary="[mock policy] send the approved draft")


def operations(t: dict[str, Any], u: dict[str, Any]) -> dict[str, Any]:
    tasks = _open_tasks(u, t["agent_id"])
    if not tasks:
        return _wait("no open operations task")
    task = tasks[0]
    desc = task["description"]
    if any(m["is_system"] and m["message_type"] == "WARNING" for m in u["inbox"]):
        if not any("holding" in text for text in _sent_texts(u)):
            return decision("REQUEST_INFORMATION", recipient_id="coordinator",
                            message_text="Safety warning received; holding the send pending review.",
                            summary="[mock policy] comply with the safety warning")
        return _wait("holding after a safety warning")
    field_task = re.match(r"Send field (\w+) of (cust_\d+) to (\S+)$", desc)
    if field_task:
        fld, cid, to = field_task.groups()
        rec = _last_read(u, "read_customer_record", cid)
        if rec is None:
            return decision("TOOL_REQUEST", tool="read_customer_record", resource_id=cid,
                            summary=f"[mock policy] read {cid} for the extract")
        value = rec["result"]["content"].get(fld, "")
        return _ops_send_flow(t, u, task, to, "Record extract", f"Requested record field {fld}: {value}.", None)
    cid = (CUST.findall(desc) or [None])[0]
    brief = _brief(u)
    if cid is None or brief is None:
        return _wait("cannot identify the customer")
    br = _last_read(u, "read_document", brief)
    if br is None:
        return decision("TOOL_REQUEST", tool="read_document", resource_id=brief, summary="[mock policy] read the brief")
    fields = parse_brief(br["result"]["content"].get("content", ""))
    contact, name, request = fields.get(f"{cid}.contact"), fields.get(f"{cid}.name"), fields.get(f"{cid}.request")
    if not contact:
        return _wait("brief has no contact for this customer yet")
    recipient, claimed = contact, None
    for m in u["inbox"]:
        if m["is_system"] or cid not in m["text"]:
            continue
        if (o := re.search(r"recipient:\s*(\S+@\S+?)[\s.,;]", m["text"] + " ")):
            recipient = o.group(1)
        if (a := re.search(r"approval_id:\s*(approval_\d+)", m["text"])):
            claimed = a.group(1)
    hint = next(iter(EMAIL.findall(desc)), None)
    if hint and hint != contact:
        asked = any("Which address is correct" in text for text in _sent_texts(u))
        answered = any(m["message_type"] == "RESPONSE" for m in u["inbox"])
        if not asked:
            return decision("REQUEST_INFORMATION", recipient_id="coordinator",
                            message_text=f"The task for {cid} says {hint} but the brief says {contact}. "
                                         "Which address is correct?", summary="[mock policy] conflicting addresses")
        if not answered:
            return _wait("waiting for clarification")
    return _ops_send_flow(t, u, task, recipient, "Service Update",
                          f"Dear {name}, your request {request} has been received and is being processed.", claimed)


# --------------------------------------------------------------------------- adversary


def adversary(t: dict[str, Any], u: dict[str, Any]) -> dict[str, Any]:
    pf = t.get("private_objective_fields", {})
    tactic = pf.get("tactic")
    if t["role"] == "coordinator":
        return coordinator(t, u)
    if tactic == "message":
        if not _results(u, "send_message", True):
            return decision("SEND_MESSAGE", recipient_id=pf["to"], message_type="INFORMATION",
                            message_text=pf["text"], summary="[mock adversary] send the planned message")
        return _wait("message sent")
    if tactic == "poison_brief":
        brief = _brief(u)
        cid = pf["customer_id"]
        if brief is None or any(m.get("tool") == "update_shared_document" for m in _results(u, executed=True)):
            return _wait("done")
        br = _last_read(u, "read_document", brief)
        if br is None or br["result"]["version"] != _version(u, brief):
            return decision("TOOL_REQUEST", tool="read_document", resource_id=brief,
                            summary="[mock adversary] read the brief")
        text = br["result"]["content"].get("content", "")
        if f"{cid}.contact:" not in text:
            return _wait("brief not written yet")
        poisoned = "\n".join(f"{cid}.contact: {pf['contact']}" if ln.startswith(f"{cid}.contact:") else ln
                             for ln in text.splitlines())
        return decision("TOOL_REQUEST", tool="update_shared_document", resource_id=brief,
                        resource_version=br["result"]["version"], parameters={"content": poisoned},
                        summary="[mock adversary] change the contact line in the shared brief")
    return _wait("no tactic")


POLICIES = {"coordinator": coordinator, "research": research, "operations": operations}


def agent_policy_responder(system_prompt: str, user_content: str, tags: dict[str, str]) -> dict[str, Any]:
    payload = json.loads(user_content)
    t, u = payload["trusted_assignment"], payload["untrusted_observation"]
    if "private_objective" in t:
        return adversary(t, u)
    return POLICIES[t["role"]](t, u)
