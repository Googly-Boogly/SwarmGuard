"""Actual execution metadata, immutable configuration, and exclusive exports."""
from __future__ import annotations
import json
import hashlib
import platform
import os
from pathlib import Path
from typing import Any
from swarmguard.clock import Clock
from swarmguard.ethics.prompts import prompt_manifest
from swarmguard.models.risk import RULESET_VERSION
from swarmguard.models.adversarial import ResearchConfig


def manifest(config: ResearchConfig, clock: Clock, revision: str, model: str) -> dict[str, Any]:
    from swarmguard.ethics.prompts import SCENARIO_GENERATOR_PROMPT
    root = Path(__file__).resolve().parents[2]
    digest = hashlib.sha256()
    sources = sorted([*root.glob("swarmguard/**/*.py"), *root.glob("swarmguard_providers/**/*.py"),
                      *root.glob("frontend/src/**/*.tsx"), *root.glob("frontend/src/**/*.ts"),
                      *root.glob("*.py")])
    for source in sources:
        digest.update(str(source.relative_to(root)).encode())
        digest.update(source.read_bytes())
    return {"stage": 5, "created_at": clock.now().isoformat(), "code_revision": revision,
        "source_tree_sha256": digest.hexdigest(),
        "server_limits": {k: os.environ[k] for k in (
            "SWARMGUARD_MAX_ADAPTIVE_ITERATIONS", "SWARMGUARD_MAX_AGENTS", "SWARMGUARD_MAX_WORKFLOW_STEPS",
            "SWARMGUARD_MAX_LLM_REQUESTS", "SWARMGUARD_MAX_CONCURRENT_REQUESTS", "SWARMGUARD_MAX_RUNTIME_SECONDS",
            "SWARMGUARD_MAX_EVALUATION_COST_USD") if k in os.environ},
        "scenario_generator_prompt_sha256": hashlib.sha256(SCENARIO_GENERATOR_PROMPT.encode()).hexdigest(),
        "python": platform.python_version(), "config": config.model_dump(), "dataset_version": "stage5-partitions-v1",
        "scenario_version": "stage5-templates-v1", "monitor_version": RULESET_VERSION, "prompts": prompt_manifest(),
        "model": model, "sampling": {"temperature": None, "seed": config.seed, "max_output_tokens": 800},
        "aggregation": "MAJORITY_WITH_ESCALATION; incomplete council is failed, never a safe prediction",
        "checkpoint_policy": "critical checkpoint only; latency is event-sequence lead at evaluated checkpoints",
        "replay_type": "deterministic mock replay" if config.execution_mode == "mock" else "fresh live model execution",
        "is_research_result": False, "note": "Synthetic template research. Public inherited held-out fixtures are not an independent blind benchmark."}


def export_results(repository: Any, run_id: str, root: Path) -> Path:
    stored = repository.get(run_id)
    if stored is None:
        raise KeyError(run_id)
    out = root / run_id
    out.mkdir(parents=True, exist_ok=False)
    for name, value in (("manifest", stored[0]), ("candidates", repository.candidates(run_id))):
        with (out / f"{name}.json").open("x", encoding="utf-8") as f:
            json.dump(value, f, indent=2, sort_keys=True, allow_nan=False)
            f.write("\n")
    return out
