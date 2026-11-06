"""Structural equivalence of executed traces, with identifiers alpha-normalized."""
from __future__ import annotations

import hashlib
import json
from typing import Any


def workflow_signature(actions: list[dict[str, Any]]) -> dict[str, Any]:
    agents: dict[str, int] = {}
    resources: dict[str, int] = {}
    ids = {a["action_id"]: i for i, a in enumerate(actions)}
    nodes = []
    for a in actions:
        aid, rid = a["agent_id"], a.get("resource_id")
        agents.setdefault(aid, len(agents))
        if rid:
            resources.setdefault(rid, len(resources))
        nodes.append((agents[aid], a["tool"], resources.get(rid),
                      sorted(ids[p] for p in a.get("parent_action_ids", []) if p in ids)))
    return {"agents": len(agents), "nodes": nodes}


class NoveltyDetector:
    def __init__(self) -> None:
        self._full: set[str] = set()
        self._structure: set[str] = set()

    def classify(self, actions: list[dict[str, Any]], invariant_categories: list[str]) -> dict[str, str]:
        structure = workflow_signature(actions)
        full = {**structure, "invariants": sorted(invariant_categories),
                "parameters": [a.get("parameters", {}) for a in actions]}
        digest = lambda value: hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
        structural, complete = digest(structure), digest(full)
        level = "DUPLICATE" if complete in self._full else (
            "MINOR_VARIATION" if structural in self._structure else "STRUCTURALLY_DISTINCT")
        self._full.add(complete)
        self._structure.add(structural)
        return {"classification": level, "signature": structural}

