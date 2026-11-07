"""Final laboratory: exact datasets, bounded execution, durable progress, validated results."""
from __future__ import annotations
import asyncio
import copy
from typing import Any
from swarmguard.clock import Clock, SystemClock
from swarmguard.datasets.registry import DatasetRegistry
from swarmguard.ethics.prompts import prompt_manifest
from swarmguard.evaluation.adaptive_runner import evaluate_candidate, intervention_comparison
from swarmguard.evaluation.failure_analysis import analyze_failures
from swarmguard.llm.mock_provider import MockLLMProvider
from swarmguard.llm.usage import BudgetPolicy, GuardedProvider
from swarmguard.models.adversarial import ScenarioDefinition
from swarmguard.models.risk import RULESET_VERSION
from swarmguard.research.autonomous import MockAutonomousScenario, responder
from swarmguard.research.experiment_designer import ExperimentConfig, validate_config
from swarmguard.research.experiment_registry import ExperimentRegistry, TERMINAL
from swarmguard.research.reproducibility import digest, environment, source_hash
from swarmguard.research.statistical_analysis import analyze
from swarmguard.research.validation import validate_result
from swarmguard.security.policy_engine import DEFAULT_POLICY
from swarmguard.storage.database import Database
from swarmguard.storage.repositories import PlatformRepository


class Laboratory:
    def __init__(self, db: Database, *, clock: Clock | None = None, revision: str = "unavailable") -> None:
        self.db, self.clock, self.revision = db, clock or SystemClock(), revision
        self.repo = PlatformRepository(db)
        self.datasets = DatasetRegistry(self.repo, self.clock)
        self.experiments = ExperimentRegistry(self.repo, self.clock)
        self.cancelled: set[str] = set()

    def prepare(self, config: ExperimentConfig, actor: str = "local_researcher") -> dict[str, Any]:
        config = ExperimentConfig.model_validate(config.model_dump())
        dataset = validate_config(config, self.datasets)
        if config.replay_from:
            source = self.experiments.get(config.replay_from)[0]
            if source["status"] != "COMPLETED" or not validate_result(source)["valid"]:
                raise ValueError("replay source must be completed and valid")
            keys = ("dataset", "dataset_version", "monitor", "compare_all", "trials", "seed", "max_steps",
                    "aggregation", "analysis", "framework", "intervention", "max_total_trials")
            if any(source["config"].get(k) != config.model_dump()[k] for k in keys):
                raise ValueError("recorded replay cannot change the source configuration")
        eid = self.db.next_id("experiment")
        from swarmguard.agents.prompts import prompt_hash
        manifest = {"experiment_id": eid, "dataset_hash": dataset["content_hash"], "code_revision": self.revision,
            "source_tree_hash": source_hash(), "environment": environment(), "prompts": prompt_manifest(),
            "agent_prompt_hashes": {r: prompt_hash(r) for r in ("coordinator", "research", "operations")},
            "authorization_policy_version": DEFAULT_POLICY.version, "ruleset_version": RULESET_VERSION,
            "model": "mock-deterministic-v1", "provider": "mock", "execution_mode": config.mode,
            "agent_behavior": "deterministic observation-driven mock policy" if config.mode == "MOCK_AUTONOMOUS" else "scripted templates",
            "aggregation": config.aggregation, "sampling": {"seed": config.seed, "temperature": None, "max_output_tokens": 800},
            "created_at": self.clock.now().isoformat(), "replay_from": config.replay_from,
            "is_research_result": False, "human_oversight": "Trusted simulated approval harness; no real external operations",
            "limits": config.model_dump(include={"max_steps", "max_requests", "max_runtime_seconds", "concurrency", "max_cost", "max_dataset_size", "max_total_trials"})}
        record = {"experiment_id": eid, "status": "CREATED", "config": config.model_dump(),
            "dataset": dataset, "manifest": manifest, "created_by": actor, "rows": [], "metrics": {},
            "started_at": None, "completed_at": None, "error": None, "usage": {}, "results_hash": None,
            "validation": None, "is_research_result": False}
        self.repo.create("experiment", eid, record, actor=actor, occurred_at=self.clock.now().isoformat())
        record["status"] = "VALIDATED"
        version = self.experiments.save(record, 1, actor)
        record["status"] = "QUEUED"
        self.experiments.save(record, version, actor)
        return record

    def cancel(self, experiment_id: str, actor: str) -> None:
        record, version = self.experiments.get(experiment_id)
        if record["status"] in TERMINAL:
            raise ValueError("terminal experiments cannot be cancelled")
        self.cancelled.add(experiment_id)
        record["cancel_requested_by"] = actor
        if record["status"] == "QUEUED":
            record["status"] = "CANCELLED"
            record["completed_at"] = self.clock.now().isoformat()
        self.experiments.save(record, version, actor)

    def recover(self) -> None:
        for record in self.experiments.list(10000):
            if record["status"] in ("QUEUED", "RUNNING"):
                _, version = self.experiments.get(record["experiment_id"])
                record.update(status="INCONCLUSIVE", error="Interrupted by process restart; execution was not resumed",
                              completed_at=self.clock.now().isoformat())
                self.experiments.save(record, version, "swarmguard.recovery")

    async def run(self, experiment_id: str) -> dict[str, Any]:
        record, version = self.experiments.get(experiment_id)
        if record["status"] == "CANCELLED":
            return record
        if record["status"] != "QUEUED":
            raise ValueError("experiments cannot be resumed or overwritten")
        config = ExperimentConfig.model_validate(record["config"])
        self.datasets.validate_current(config.dataset, config.dataset_version)
        provider = GuardedProvider(MockLLMProvider(responder=responder), BudgetPolicy(max_requests=config.max_requests),
                                   max_concurrency=config.concurrency)
        runner = config.runner_config(record["dataset"]["definition"]["partition"])
        definitions = [ScenarioDefinition.model_validate(s) for s in record["dataset"]["definition"]["scenarios"]]
        schedule = config.trial_schedule(definitions)
        planned = len(schedule)
        record.update(status="RUNNING", started_at=self.clock.now().isoformat())
        in_flight: dict[str, Any] | None = None

        def save() -> None:
            nonlocal version
            current, version = self.experiments.get(experiment_id)
            if current.get("cancel_requested_by"):
                record["cancel_requested_by"] = current["cancel_requested_by"]
            record["metrics"] = analyze(record["rows"], planned)
            record["usage"] = {**provider.ledger.snapshot(), "is_synthetic": True, "estimated_cost_usd": 0.0}
            version = self.experiments.save(record, version, "swarmguard.laboratory")

        save()
        try:
            async with asyncio.timeout(config.max_runtime_seconds):
                if config.mode == "RECORDED_REPLAY":
                    source = self.experiments.get(config.replay_from)[0]
                    record["rows"] = copy.deepcopy(source["rows"])
                    record["replay_source_hash"] = source["results_hash"]
                    record["status"] = "COMPLETED"
                else:
                    for index, (trial, definition) in enumerate(schedule):
                        await asyncio.sleep(0)
                        if experiment_id in self.cancelled:
                            record["status"] = "CANCELLED"
                            break
                        definition = ScenarioDefinition.model_validate({**definition.model_dump(), "max_steps": min(definition.max_steps, config.max_steps)})
                        in_flight = {"trial_id": f"{definition.scenario_id}:t{trial}", "dataset_scenario_id": definition.scenario_id,
                                     "trial": trial, "definition": definition.model_dump(), "evaluation_valid": False}
                        scenario = MockAutonomousScenario(definition, provider, config.seed) if config.mode == "MOCK_AUTONOMOUS" else None
                        row = await evaluate_candidate(definition, runner, provider, f"{experiment_id}_wf_{index:03}", scenario=scenario)
                        row.update({k: in_flight[k] for k in ("trial_id", "dataset_scenario_id", "trial")})
                        row["failures"] = analyze_failures(row)
                        # These are explicitly scripted counterfactual replays, never autonomous outcomes.
                        row["interventions"] = await intervention_comparison(definition) if config.mode == "SCRIPTED" and row["evaluation_valid"] else []
                        record["rows"].append(row)
                        in_flight = None
                        save()
                        if provider.exhausted:
                            record.update(status="INCONCLUSIVE", error="Request budget exhausted; missing assessments remain unknown")
                            break
                    if record["status"] == "RUNNING":
                        record["status"] = "COMPLETED" if all(r["evaluation_valid"] and all(v is not None for v in r["predictions"].values())
                            for r in record["rows"]) else "INCONCLUSIVE"
        except TimeoutError:
            record.update(status="INCONCLUSIVE", error="Runtime limit reached")
        except asyncio.CancelledError:
            record.update(status="CANCELLED", error="Worker stopped; no automatic resume")
        except Exception as exc:
            record.update(status="FAILED", error=f"{type(exc).__name__}: {str(exc)[:300]}")
        finally:
            if in_flight:
                record["rows"].append({**in_flight, "error": record["error"] or record["status"]})
            record["metrics"] = analyze(record["rows"], planned)
            record["results_hash"] = digest(record["rows"])
            record["completed_at"] = self.clock.now().isoformat()
            record["validation"] = validate_result(record)
            if not record["validation"]["valid"]:
                record["status"] = "FAILED"
            save()
            self.cancelled.discard(experiment_id)
        return record
