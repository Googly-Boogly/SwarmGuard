"""Read-only pre-registered hypothesis verdicts for stored RQ1 experiments (researcher console only).

GET only. Verdicts are recomputed from the stored record and result on each request via the read-only database
connection; nothing is written, rerun or cached across result changes (results are write-once).
"""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi import Path as PathParam

from swarmguard.console.auth import OperatorAuth
from swarmguard.research.hypotheses import evaluate_experiment
from swarmguard.research.rq1_lab import EXPERIMENT_KIND, RESULT_KIND
from swarmguard.storage.repositories import PlatformRepository

EID = r"^rq1_experiment_\d{3,9}$"


def register_hypothesis_routes(app: FastAPI, auth: OperatorAuth) -> None:
    @app.get("/api/research/rq1/experiments/{eid}/hypotheses")
    async def hypotheses(request: Request, eid: str = PathParam(pattern=EID)) -> dict[str, Any]:
        auth.require_read(request, "RESEARCHER")
        repo = PlatformRepository(app.state.reader)
        record = repo.get(EXPERIMENT_KIND, eid)
        if not record:
            raise HTTPException(404, "RQ1 experiment not found")
        result = repo.get(RESULT_KIND, eid)
        if not result:
            raise HTTPException(409, f"results are not available for this experiment (status {record[0]['status']})")
        try:
            return evaluate_experiment(record[0], result[0])
        except Exception as exc:  # fail closed: never present a verdict that could not be computed
            raise HTTPException(500, f"hypothesis verdicts could not be computed: {type(exc).__name__}") from None
