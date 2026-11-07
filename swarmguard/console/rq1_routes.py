"""Authenticated RQ1 research laboratory routes (researcher console only).

Read routes require a RESEARCHER session; state-changing routes additionally require the exact local Origin,
JSON and the session CSRF token (``OperatorAuth.require``). Ground truth is exposed only here, to authenticated
researchers, after an experiment has fixed its predictions. No route accepts identities, policies, tool calls or
SQL, and nothing here reaches the Capability Gateway or an executor: experiments run in isolated in-memory
simulators through the unchanged gateway.
"""
from __future__ import annotations

import asyncio
import csv
import io
import json
from collections import OrderedDict
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi import Path as PathParam

from swarmguard.console.auth import OperatorAuth
from swarmguard.research import rq1_lab, rq1_llm
from swarmguard.research.hypothesis_registry import questions
from swarmguard.research.rq1_lab import (EXPERIMENT_KIND, RESULT_KIND, RQ1ExperimentConfig, RQ1Laboratory,
                                         context_difference, context_sections, load_evidence, query_rows)
from swarmguard.storage.repositories import PlatformRepository

EID = r"^rq1_experiment_\d{3,9}$"
WF = r"^wf_\d{3}$"
SID = r"^[a-z0-9_]{1,64}$"
CONFIGS = ("local_action", "global_workflow")
SUMMARY_FIELDS = ("experiment_id", "status", "config", "created_by", "created_at", "started_at", "completed_at",
                  "progress", "error", "headline", "preregistered_procedure", "execution_mode", "integrity_problems",
                  "llm", "llm_usage", "model_requests", "estimated_cost_usd")


class RQ1Jobs:
    def __init__(self, lab: RQ1Laboratory, worker: asyncio.Semaphore) -> None:
        self.lab, self.worker = lab, worker
        self.tasks: list[asyncio.Task[None]] = []
        lab.recover()

    async def start(self, config: RQ1ExperimentConfig, actor: str) -> dict[str, Any]:
        self.tasks = [t for t in self.tasks if not t.done()]
        if len(self.tasks) >= 2:
            raise HTTPException(429, "RQ1 experiment queue full")
        validation = await self.lab.validate(config)
        record = self.lab.prepare(config, validation, actor)

        async def execute() -> None:
            async with self.worker:
                await self.lab.run(record["experiment_id"])
        self.tasks.append(asyncio.create_task(execute()))
        return record

    async def close(self) -> None:
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)


def register_rq1_routes(app: FastAPI, auth: OperatorAuth) -> None:
    cache: OrderedDict[str, dict[str, Any]] = OrderedDict()

    def repo() -> PlatformRepository:
        return PlatformRepository(app.state.reader)

    def experiment(eid: str) -> dict[str, Any]:
        found = repo().get(EXPERIMENT_KIND, eid)
        if not found:
            raise HTTPException(404, "RQ1 experiment not found")
        return found[0]

    def result(eid: str) -> dict[str, Any]:
        if eid in cache:
            cache.move_to_end(eid)
            return cache[eid]
        record = experiment(eid)
        found = repo().get(RESULT_KIND, eid)
        if not found:
            raise HTTPException(409, f"results are not available for this experiment (status {record['status']})")
        cache[eid] = found[0]           # results are written once and never modified
        while len(cache) > 4:
            cache.popitem(last=False)
        return found[0]

    def evidence(eid: str, wf: str) -> dict[str, Any]:
        try:
            found = load_evidence(repo(), eid, wf)
        except ValueError as exc:
            raise HTTPException(500, str(exc)) from None
        if found is None:
            raise HTTPException(404, "no stored evidence for this workflow")
        return found

    @app.get("/api/research/rq1")
    async def overview(request: Request) -> dict[str, Any]:
        auth.require_read(request, "RESEARCHER")
        records = repo().list(EXPERIMENT_KIND, 200)
        question = next(q for q in questions() if q["research_question_id"] == "RQ1")
        llm_status = app.state.rq1.lab.llm_status()
        return {"question": question, "datasets": rq1_lab.dataset_summaries(), "llm_status": llm_status,
                "llm_policies": rq1_llm.POLICY_TEXT, "llm_protocol_version": rq1_llm.LLM_PROTOCOL_VERSION,
                "recommended_config": rq1_lab.recommended_config(bool(llm_status.get("live", {}).get("available"))),
                "pilot_config": RQ1ExperimentConfig(partition="development",
                                                    scenario_ids=list(rq1_lab.rq1.PILOT_SCENARIOS)).model_dump(),
                "unavailable_options": rq1_lab.UNAVAILABLE_OPTIONS, "known_limitations": rq1_lab.KNOWN_LIMITATIONS,
                "protocol_version": rq1_lab.rq1.PROTOCOL_VERSION, "frozen_dataset_hash": rq1_lab.FROZEN_DATASET_HASH,
                "experiments": [{k: r.get(k) for k in SUMMARY_FIELDS} for r in records],
                "execution_note": "Scripted agents; deterministic rule-based monitors plus an optional LLM arm "
                                  "(mock or live provider, capped and confirmed)."}

    @app.post("/api/research/rq1/validate")
    async def validate(body: RQ1ExperimentConfig, request: Request) -> dict[str, Any]:
        auth.require(request, "RESEARCHER")
        return await app.state.rq1.lab.validate(body)

    @app.post("/api/research/rq1/experiments", status_code=202)
    async def start(body: RQ1ExperimentConfig, request: Request) -> dict[str, Any]:
        session = auth.require(request, "RESEARCHER")
        try:
            record = await app.state.rq1.start(body, session.username)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        return {k: record.get(k) for k in SUMMARY_FIELDS} | {"validation": record["validation"]}

    @app.get("/api/research/rq1/experiments")
    async def experiments(request: Request) -> list[dict[str, Any]]:
        auth.require_read(request, "RESEARCHER")
        return [{k: r.get(k) for k in SUMMARY_FIELDS} for r in repo().list(EXPERIMENT_KIND, 200)]

    @app.get("/api/research/rq1/experiments/{eid}")
    async def detail(request: Request, eid: str = PathParam(pattern=EID)) -> dict[str, Any]:
        auth.require_read(request, "RESEARCHER")
        return experiment(eid)

    @app.post("/api/research/rq1/experiments/{eid}/cancel")
    async def cancel(request: Request, eid: str = PathParam(pattern=EID)) -> dict[str, bool]:
        session = auth.require(request, "RESEARCHER")
        experiment(eid)
        try:
            app.state.rq1.lab.cancel(eid, session.username)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None
        return {"cancel_requested": True}

    @app.get("/api/research/rq1/experiments/{eid}/results")
    async def results(request: Request, eid: str = PathParam(pattern=EID)) -> dict[str, Any]:
        auth.require_read(request, "RESEARCHER")
        r = result(eid)
        a = r["analysis"]
        return {"experiment": {k: experiment(eid).get(k) for k in SUMMARY_FIELDS},
                "analysis": {k: v for k, v in a.items()}, "metrics": r["metrics_view"],
                "verification": {k: v for k, v in r["verification"].items() if k != "ablation_diagnostic"},
                "ablations": r["verification"].get("ablation_diagnostic", []),
                "enforcement": r["results"]["enforcement"], "crosscheck": r["crosscheck"],
                "reproducibility": r["reproducibility"], "failure_case_counts": r["failure_case_counts"],
                "llm": r.get("llm"), "llm_usage": r.get("llm_usage"),
                "llm_analysis": {k: v for k, v in (r.get("llm_analysis") or {}).items() if k != "rows"} or None,
                "llm_failure_case_counts": r.get("llm_failure_case_counts"),
                "findings": r["findings"], "counts": {"checkpoint_evaluations": len(r["checkpoint_rows"]),
                                                      "workflow_predictions": len(r["workflow_rows"]),
                                                      "scenarios": len(r["scenarios"])}}

    @app.get("/api/research/rq1/experiments/{eid}/raw")
    async def raw(request: Request, eid: str = PathParam(pattern=EID),
                  level: Literal["checkpoint", "workflow"] = "checkpoint",
                  q: str | None = Query(None, max_length=100), sort: str | None = Query(None, pattern=r"^[a-z_]{1,40}$"),
                  order: Literal["asc", "desc"] = "asc", page: int = Query(1, ge=1, le=100000),
                  page_size: int = Query(25, ge=5, le=200), scenario_id: str | None = None,
                  agent_id: str | None = None, monitor: str | None = None, recommendation: str | None = None,
                  ground_truth: str | None = None, workflow_prediction: str | None = None,
                  workflow_correct: str | None = None, risk_level: str | None = None, status: str | None = None,
                  category: str | None = None, tool: str | None = None, position: str | None = None,
                  outcome: str | None = None, workflow_id: str | None = None,
                  alert: Literal["true", "false"] | None = None) -> dict[str, Any]:
        auth.require_read(request, "RESEARCHER")
        r = result(eid)
        rows = r["checkpoint_rows"] if level == "checkpoint" else r["workflow_rows"]
        filters = {"scenario_id": scenario_id, "agent_id": agent_id, "monitor": monitor, "recommendation": recommendation,
                   "ground_truth": ground_truth, "workflow_prediction": workflow_prediction,
                   "workflow_correct": workflow_correct, "risk_level": risk_level, "status": status,
                   "category": category, "tool": tool, "position": position, "outcome": outcome,
                   "workflow_id": workflow_id, "alert": alert}
        return {"level": level, **query_rows(rows, filters=filters, query=q, sort=sort, descending=order == "desc",
                                             page=page, page_size=page_size)}

    @app.get("/api/research/rq1/experiments/{eid}/raw/{wf}/{step}/{configuration}")
    async def raw_detail(request: Request, eid: str = PathParam(pattern=EID), wf: str = PathParam(pattern=WF),
                         step: int = PathParam(ge=1, le=1000),
                         configuration: Literal["local_action", "global_workflow", "llm_local", "llm_global"] = PathParam()
                         ) -> dict[str, Any]:
        auth.require_read(request, "RESEARCHER")
        r = result(eid)
        ev = evidence(eid, wf)
        cp = next((c for c in ev["checkpoints"] if c["step"] == step), None)
        if cp is None:
            raise HTTPException(404, "no checkpoint at this step")
        if configuration in rq1_llm.LLM_CONFIGS:
            return _llm_detail(eid, wf, step, configuration, r, ev, cp)
        e = cp["evaluations"][configuration]
        other = cp["evaluations"][CONFIGS[1 - CONFIGS.index(configuration)]]
        row = next(x for x in r["checkpoint_rows"] if x["row_id"] == f"{wf}:{step}:{configuration}")
        wrow = next(x for x in r["workflow_rows"] if x["row_id"] == f"{wf}:{configuration}")
        truth = next(t for t in r["results"]["ground_truth"] if t["workflow_id"] == wf)
        invariants = {i["invariant_id"]: i for i in e["input"].get("invariants", [])}
        decision = next((d for d in ev["decisions"] if d["action_id"] == cp["action_id"]), None)
        event = next((x for x in ev["events"] if x["seq"] == cp["checkpoint_seq"]), None)
        return {
            "basic": {"experiment_id": eid, "scenario_id": ev["scenario_id"], "workflow_id": wf, "trial": 1,
                      "step": step, "checkpoint_seq": cp["checkpoint_seq"], "agent_id": row["agent_id"],
                      "action": e["input"]["action"], "timestamp": event["created_at"] if event else None,
                      "timestamp_note": "Synthetic simulator clock", "monitor": row["monitor"],
                      "position": row["position"], "critical_step": row["critical_step"]},
            "ground_truth": {"label": truth["label"], "safety_property": truth["safety_property"],
                             "violated_invariants": [invariants.get(i, {"invariant_id": i}) for i in truth["violated_invariants"]],
                             "description": truth["description"], "rationale": truth["rationale"],
                             "expected_authorization": truth["expected_authorization"],
                             "prohibited_outcome": truth.get("prohibited_outcome"), "category": row["category"],
                             "workflow_prediction": wrow["workflow_prediction"], "workflow_outcome": wrow["outcome"],
                             "note": "Researcher view only. Labels were joined after predictions were fixed and never "
                                     "reached the monitor."},
            "monitor_input": context_sections(e["input"]), "input_bytes": e["input_bytes"],
            "monitor_output": {**e["output"], "alert": e["alert"], "latency_ms": e["latency_ms"]},
            "paired_monitor": {"monitor": "Global" if configuration == "local_action" else "Local",
                               "recommendation": other["output"]["recommendation"], "risk_level": other["output"]["risk_level"],
                               "alert": other["alert"], "detected_patterns": other["output"]["detected_patterns"]},
            "authorization": decision,
            "outcome": {"executed": decision["executed"] if decision else None,
                        "execution_status": decision["execution_status"] if decision else None,
                        "sent_emails": [m for m in ev["sent_emails"] if m.get("action_id") == cp["action_id"]],
                        "workflow_prohibited_outcome_occurred": wrow.get("prohibited_outcome_occurred")},
            "original": {"monitor_input": e["input"], "monitor_output": e["output"], "gateway_decision": decision}}

    @app.get("/api/research/rq1/experiments/{eid}/scenarios")
    async def scenarios(request: Request, eid: str = PathParam(pattern=EID)) -> list[dict[str, Any]]:
        auth.require_read(request, "RESEARCHER")
        return result(eid)["scenarios"]

    @app.get("/api/research/rq1/experiments/{eid}/scenarios/{sid}")
    async def scenario(request: Request, eid: str = PathParam(pattern=EID), sid: str = PathParam(pattern=SID)) -> dict[str, Any]:
        auth.require_read(request, "RESEARCHER")
        r = result(eid)
        summary = next((s for s in r["scenarios"] if s["scenario_id"] == sid), None)
        if summary is None:
            raise HTTPException(404, "scenario not in this experiment")
        meta = next(s for s in r["manifest"]["scenarios"] if s["scenario_id"] == sid)
        truth = next((t for t in r["results"]["ground_truth"] if t["scenario_id"] == sid), None)
        ev = evidence(eid, summary["workflow_id"]) if summary["workflow_id"] else None
        decisions = {d["action_id"]: d for d in (ev or {}).get("decisions", [])}
        initial = {rid: vs[0] for rid, vs in (ev or {}).get("resource_versions", {}).items() if vs}
        return {
            "summary": summary, "definition": meta, "ground_truth": truth,
            "agents": (ev or {}).get("agents", []),
            "permissions": _permissions((ev or {}).get("agents", [])),
            "initial_resources": initial, "resource_versions": (ev or {}).get("resource_versions", {}),
            "actions": [{**a, "decision": decisions.get(a["action_id"])} for a in (ev or {}).get("actions", [])],
            "events": (ev or {}).get("events", []), "messages": (ev or {}).get("messages", []),
            "approvals": (ev or {}).get("approvals", []), "sent_emails": (ev or {}).get("sent_emails", []),
            "critical_action_id": (ev or {}).get("critical_action_id"),
            "checkpoints": _checkpoint_summaries(ev) if ev else [],
            "predictions": [w for w in r["workflow_rows"] if w["scenario_id"] == sid],
            "enforcement": [e for e in r["results"]["enforcement"] if e["scenario_id"] == sid]}

    @app.get("/api/research/rq1/experiments/{eid}/workflows/{wf}")
    async def workflow(request: Request, eid: str = PathParam(pattern=EID), wf: str = PathParam(pattern=WF)) -> dict[str, Any]:
        auth.require_read(request, "RESEARCHER")
        experiment(eid)                 # evidence is readable as soon as a workflow is captured (live view)
        ev = evidence(eid, wf)
        return {k: ev[k] for k in ("workflow_id", "scenario_id", "agents", "events", "actions", "decisions", "messages",
                                   "approvals", "sent_emails", "graph", "critical_action_id", "resource_versions")} | {
            "checkpoints": _checkpoint_summaries(ev)}

    @app.get("/api/research/rq1/experiments/{eid}/comparisons/{wf}")
    async def comparison(request: Request, eid: str = PathParam(pattern=EID), wf: str = PathParam(pattern=WF)) -> dict[str, Any]:
        auth.require_read(request, "RESEARCHER")
        r = result(eid)
        ev = evidence(eid, wf)
        rows = {w["configuration"]: w for w in r["workflow_rows"] if w["workflow_id"] == wf and w["status"] == "COMPLETE"}
        truth = next(t for t in r["results"]["ground_truth"] if t["workflow_id"] == wf)
        steps = []
        llm_rows = {w["configuration"]: w for w in r["workflow_rows"] if w["workflow_id"] == wf and w["configuration"] in rq1_llm.LLM_CONFIGS}
        for cp in ev["checkpoints"]:
            lo, gl = cp["evaluations"]["local_action"], cp["evaluations"]["global_workflow"]
            steps.append({"step": cp["step"], "action_id": cp["action_id"], "tool": lo["input"]["action"]["tool"],
                          "agent_id": lo["input"]["action"]["agent_id"], "snapshot_digest": cp["snapshot_digest"],
                          "local": _side(lo), "global": _side(gl), "difference": context_difference(lo["input"], gl["input"]),
                          "differs": {"prediction": lo["alert"] != gl["alert"],
                                      "recommendation": lo["output"]["recommendation"] != gl["output"]["recommendation"],
                                      "risk": lo["output"]["risk_level"] != gl["output"]["risk_level"],
                                      "evidence": sorted(lo["output"]["evidence_event_ids"]) != sorted(gl["output"]["evidence_event_ids"])},
                          "llm": {cfg: _llm_side(cp, cfg) for cfg in rq1_llm.LLM_CONFIGS} if "llm" in cp else None})
        return {"workflow_id": wf, "scenario_id": ev["scenario_id"], "ground_truth": truth["label"],
                "description": truth["description"], "critical_action_id": ev["critical_action_id"],
                "local": rows.get("local_action"), "global": rows.get("global_workflow"), "steps": steps,
                "llm_local": llm_rows.get("llm_local"), "llm_global": llm_rows.get("llm_global"), "llm": r.get("llm"),
                "ablations": [a for a in r["verification"].get("ablation_diagnostic", []) if a["workflow_id"] == wf]}

    @app.get("/api/research/rq1/experiments/{eid}/failures")
    async def failures(request: Request, eid: str = PathParam(pattern=EID)) -> dict[str, Any]:
        auth.require_read(request, "RESEARCHER")
        r = result(eid)
        return {"counts": r["failure_case_counts"], "cases": r["failure_cases"],
                "llm_counts": r.get("llm_failure_case_counts"), "llm_cases": r.get("llm_failure_cases", []),
                "llm_failures": (r.get("llm_analysis") or {}).get("failures", []),
                "integrity": {"crosscheck": r["crosscheck"], "verification_failures": r["verification"]["failures"],
                              "missing_or_failed": r["analysis"]["missing_or_failed"]}}

    @app.get("/api/research/rq1/experiments/{eid}/findings")
    async def findings(request: Request, eid: str = PathParam(pattern=EID)) -> dict[str, Any]:
        auth.require_read(request, "RESEARCHER")
        return result(eid)["findings"]

    @app.get("/api/research/rq1/experiments/{eid}/metadata")
    async def metadata(request: Request, eid: str = PathParam(pattern=EID)) -> dict[str, Any]:
        auth.require_read(request, "RESEARCHER")
        record = experiment(eid)
        found = repo().get(RESULT_KIND, eid)
        r = found[0] if found else None
        return {"experiment": {k: v for k, v in record.items() if k != "activity"},
                "results_metadata": r["results"]["metadata"] if r else None,
                "scoring": {"protocol_version": rq1_lab.rq1.PROTOCOL_VERSION,
                            "positive_mapping": r["results"]["config"]["positive_mapping"] if r else None,
                            "primary_metric": r["analysis"]["primary_metric_definition"] if r else None,
                            "ruleset_version": r["results"]["config"]["ruleset_version"] if r else None},
                "dataset": {k: v for k, v in r["manifest"].items() if k != "scenarios"} if r else None,
                "verification": {k: v for k, v in r["verification"].items() if k != "ablation_diagnostic"} if r else None,
                "crosscheck": r["crosscheck"] if r else None, "reproducibility": r["reproducibility"] if r else None,
                "methodology": r["findings"]["experiment_summary"] if r else None,
                "llm": record.get("llm"), "llm_usage": record.get("llm_usage"),
                "llm_metric_definition": (r.get("llm_analysis") or {}).get("metric_definition") if r else None,
                "limitations": r["findings"]["limitations"] if r else rq1_lab.KNOWN_LIMITATIONS}

    @app.get("/api/research/rq1/experiments/{eid}/download")
    async def download(request: Request, eid: str = PathParam(pattern=EID),
                       level: Literal["checkpoint", "workflow"] = "workflow",
                       format: Literal["csv", "json"] = "csv") -> Response:
        auth.require_read(request, "RESEARCHER")
        rows = result(eid)["checkpoint_rows" if level == "checkpoint" else "workflow_rows"]
        name = f"{eid}_{level}_rows.{format}"
        if format == "json":
            body = json.dumps(rows, indent=2, sort_keys=True).encode()
            media = "application/json"
        else:
            stream = io.StringIO(newline="")
            fields = sorted({k for r in rows for k in r})
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            for row in rows:
                writer.writerow({k: ";".join(map(str, v)) if isinstance(v, list) else v for k, v in row.items()})
            body, media = stream.getvalue().encode(), "text/csv"
        return Response(body, media_type=media, headers={"Content-Disposition": f'attachment; filename="{name}"'})


def _side(e: dict[str, Any]) -> dict[str, Any]:
    o, i = e["output"], e["input"]
    return {"recommendation": o["recommendation"], "risk_level": o["risk_level"], "uncertainty": o["uncertainty"],
            "alert": e["alert"], "detected_patterns": o["detected_patterns"], "evidence_event_ids": o["evidence_event_ids"],
            "explanation": o["explanation"], "missing_information": o["missing_information"],
            "findings": o.get("findings", []), "latency_ms": e["latency_ms"], "input_bytes": e["input_bytes"],
            "scope": {"events": len(i.get("events", [])), "prior_actions": len(i.get("prior_actions", [])),
                      "resources": len(i.get("resources", [])), "approvals": len(i.get("approvals", [])),
                      "workflow_purpose": i.get("workflow_purpose") is not None, "outside_window": i.get("outside_window", [])}}


def _checkpoint_summaries(ev: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for cp in ev["checkpoints"]:
        lo, gl = cp["evaluations"]["local_action"], cp["evaluations"]["global_workflow"]
        out.append({"step": cp["step"], "action_id": cp["action_id"], "checkpoint_seq": cp["checkpoint_seq"],
                    "tool": lo["input"]["action"]["tool"], "agent_id": lo["input"]["action"]["agent_id"],
                    "local": {"recommendation": lo["output"]["recommendation"], "risk_level": lo["output"]["risk_level"], "alert": lo["alert"]},
                    "global": {"recommendation": gl["output"]["recommendation"], "risk_level": gl["output"]["risk_level"], "alert": gl["alert"]}})
    return out


def _permissions(agents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from swarmguard.security.identity import AgentRole
    from swarmguard.security.policy_engine import DEFAULT_POLICY
    out = []
    for a in agents:
        try:
            role = AgentRole(a["role"])
        except ValueError:
            out.append({**a, "permitted_tools": []})
            continue
        out.append({**a, "permitted_tools": sorted(DEFAULT_POLICY.role_permissions.get(role, []))})
    return out


def _llm_side(cp: dict[str, Any], cfg: str) -> dict[str, Any]:
    e, stored = cp["llm"][cfg], cp["llm_inputs"][cfg]
    a = e.get("assessment") or {}
    obs = stored["context"]["observation"]
    return {"status": e["status"], "recommendation": e.get("recommendation"), "risk_level": e.get("risk_level"),
            "uncertainty": e.get("uncertainty"), "alert": e["alert"], "concerns": a.get("concerns", []),
            "evidence_event_ids": a.get("evidence_event_ids", []), "explanation": a.get("explanation"),
            "insufficient_evidence": a.get("insufficient_evidence"), "error": e.get("error"),
            "latency_ms": e.get("latency_ms"), "usage": (e.get("call") or {}).get("usage"),
            "input_bytes": stored["user_content_bytes"],
            "scope": {"events": len(obs.get("events", [])), "prior_actions": len(obs.get("prior_actions", [])),
                      "resources": len(obs.get("resources", [])), "approvals": len(obs.get("approvals", [])),
                      "workflow_purpose": obs.get("workflow_purpose") is not None}}


def _llm_detail(eid: str, wf: str, step: int, cfg: str, r: dict[str, Any], ev: dict[str, Any], cp: dict[str, Any]) -> dict[str, Any]:
    if "llm" not in cp:
        raise HTTPException(404, "the LLM arm did not evaluate this checkpoint (checkpoint policy)")
    e, stored = cp["llm"][cfg], cp["llm_inputs"][cfg]
    try:
        content = rq1_llm.exact_user_content(stored)
    except ValueError as exc:
        raise HTTPException(500, str(exc)) from None
    row = next(x for x in r["checkpoint_rows"] if x["row_id"] == f"{wf}:{step}:{cfg}")
    wrow = next(x for x in r["workflow_rows"] if x["row_id"] == f"{wf}:{cfg}")
    truth = next(t for t in r["results"]["ground_truth"] if t["workflow_id"] == wf)
    obs = stored["context"]["observation"]
    invariants = {i["invariant_id"]: i for i in obs.get("invariants", [])}
    decision = next((d for d in ev["decisions"] if d["action_id"] == cp["action_id"]), None)
    event = next((x for x in ev["events"] if x["seq"] == cp["checkpoint_seq"]), None)
    other_cfg = rq1_llm.LLM_LOCAL if cfg == rq1_llm.LLM_GLOBAL else rq1_llm.LLM_GLOBAL
    other = cp["llm"][other_cfg]
    a = e.get("assessment") or {}
    llm = r.get("llm") or {}
    return {
        "basic": {"experiment_id": eid, "scenario_id": ev["scenario_id"], "workflow_id": wf, "trial": 1, "step": step,
                  "checkpoint_seq": cp["checkpoint_seq"], "agent_id": row["agent_id"], "action": obs["action"],
                  "timestamp": event["created_at"] if event else None, "timestamp_note": "Synthetic simulator clock",
                  "monitor": row["monitor"], "position": row["position"], "critical_step": row["critical_step"]},
        "ground_truth": {"label": truth["label"], "safety_property": truth["safety_property"],
                         "violated_invariants": [invariants.get(i, {"invariant_id": i}) for i in truth["violated_invariants"]],
                         "description": truth["description"], "rationale": truth["rationale"],
                         "expected_authorization": truth["expected_authorization"],
                         "prohibited_outcome": truth.get("prohibited_outcome"), "category": row["category"],
                         "workflow_prediction": wrow["workflow_prediction"], "workflow_outcome": wrow["outcome"],
                         "note": "Researcher view only. Labels were joined after LLM predictions were stored and never "
                                 "reached the model."},
        "monitor_input": context_sections(obs), "input_bytes": stored["user_content_bytes"],
        "llm_prompt": {"provider": llm.get("provider"), "model": llm.get("model"), "is_synthetic": llm.get("is_synthetic"),
                       "prompt_version": llm.get("prompt_version"), "prompt_hash": llm.get("prompt_hash"),
                       "system_prompt_sha256": llm.get("system_prompt_sha256"), "user_content": content,
                       "user_content_sha256": stored["user_content_sha256"],
                       "note": "The system prompt is a trusted constant; workflow data is sent only in this JSON user message."},
        "monitor_output": {"recommendation": e.get("recommendation") or e["status"], "risk_level": e.get("risk_level") or "UNKNOWN",
                           "uncertainty": e.get("uncertainty") or "UNKNOWN", "alert": e["alert"], "status": e["status"],
                           "detected_patterns": [], "findings": [], "evidence_event_ids": a.get("evidence_event_ids", []),
                           "explanation": a.get("explanation") or (e.get("error") or {}).get("message"),
                           "missing_information": ["insufficient evidence reported by the model"] if a.get("insufficient_evidence") else [],
                           "concerns": a.get("concerns", []), "legitimate_interpretations": a.get("legitimate_interpretations", []),
                           "error": e.get("error"), "usage": (e.get("call") or {}).get("usage"),
                           "latency_ms": e.get("latency_ms"), "method": f"LLM · {llm.get('provider')} · {llm.get('model')}",
                           "ruleset_version": llm.get("prompt_version"), "is_synthetic": llm.get("is_synthetic")},
        "paired_monitor": {"monitor": rq1_llm.LLM_LABEL[other_cfg], "recommendation": other.get("recommendation") or other["status"],
                           "risk_level": other.get("risk_level") or "UNKNOWN", "alert": other["alert"], "detected_patterns": []},
        "authorization": decision,
        "outcome": {"executed": decision["executed"] if decision else None,
                    "execution_status": decision["execution_status"] if decision else None,
                    "sent_emails": [m for m in ev["sent_emails"] if m.get("action_id") == cp["action_id"]],
                    "workflow_prohibited_outcome_occurred": None},
        "original": {"llm_input_context": stored["context"], "llm_result": e, "gateway_decision": decision}}
