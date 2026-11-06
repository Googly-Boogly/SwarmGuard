"""Bounded generate–validate–execute–record loop. No security-policy mutation path."""
from __future__ import annotations
import asyncio
import os
import random
from dataclasses import asdict
from typing import Any
from swarmguard.clock import Clock, SystemClock
from swarmguard.adversarial.constraints import validate_candidate
from swarmguard.adversarial.scenario_generator import ScenarioGenerator
from swarmguard.adversarial.scenario_mutator import ScenarioMutator
from swarmguard.adversarial.novelty_detector import NoveltyDetector
from swarmguard.evaluation.adaptive_runner import evaluate_candidate, intervention_comparison
from swarmguard.evaluation.experiment_manifest import manifest
from swarmguard.evaluation.failure_analysis import analyze_failures
from swarmguard.evaluation.statistical_analysis import summarize_trials
from swarmguard.ethics.synthetic import rules_responder
from swarmguard.llm.mock_provider import MockLLMProvider
from swarmguard.llm.usage import BudgetPolicy, GuardedProvider
from swarmguard.models.adversarial import ResearchConfig, ScenarioDefinition
from swarmguard.storage.database import Database
from swarmguard.storage.repositories import ResearchRepository

ACTOR = "swarmguard.stage5"


class AdaptiveController:
    def __init__(self, db: Database, *, clock: Clock | None = None, revision: str = "unavailable",
                 provider: GuardedProvider | None = None) -> None:
        self.db, self.repo = db, ResearchRepository(db)
        self.clock = clock or SystemClock()
        self.revision, self.provider = revision, provider
        self.cancelled: set[str] = set()

    def prepare(self, config: ResearchConfig) -> dict[str, Any]:
        config = ResearchConfig.model_validate(config.model_dump())
        limits = ((config.max_iterations, "SWARMGUARD_MAX_ADAPTIVE_ITERATIONS", 20),
                  (config.max_requests, "SWARMGUARD_MAX_LLM_REQUESTS", 1000),
                  (config.max_runtime_seconds, "SWARMGUARD_MAX_RUNTIME_SECONDS", 600),
                  (config.max_concurrency, "SWARMGUARD_MAX_CONCURRENT_REQUESTS", 4),
                  (config.agent_count, "SWARMGUARD_MAX_AGENTS", 12),
                  (config.max_steps, "SWARMGUARD_MAX_WORKFLOW_STEPS", 60))
        for actual, env, default in limits:
            if actual > int(os.environ.get(env, str(default))):
                raise ValueError(f"configuration exceeds server limit {env}")
        if config.max_cost_usd > float(os.environ.get("SWARMGUARD_MAX_EVALUATION_COST_USD", "5")):
            raise ValueError("configuration exceeds server spending limit")
        if config.execution_mode == "openai":
            p = self.provider
            if p is None or p.is_synthetic or not p.policy.priced or p.policy.max_cost_usd is None or (
                p.policy.max_cost_usd > config.max_cost_usd or p.policy.max_requests is None
                or p.policy.max_requests > config.max_requests or p.max_concurrency > config.max_concurrency):
                raise ValueError("live research requires an injected, priced, bounded provider matching the manifest")
        elif self.provider is not None and not self.provider.is_synthetic:
            raise ValueError("mock configuration cannot invoke a live provider")
        if not ScenarioGenerator().generate(config):
            raise ValueError("no authorized templates for this category and partition")
        run_id = self.db.next_id("research")
        record = {"run_id": run_id, "status": "QUEUED", "kind": config.kind,
            "manifest": manifest(config, self.clock, self.revision, self.provider.model if self.provider else "mock-deterministic-v1"),
            "generated": 0, "validated": 0, "executed": 0, "rejected": 0, "failed": 0,
            "monitoring_failures": 0, "gateway_blocks": 0, "prohibited_outcomes": 0, "iteration": 0,
            "usage": {}, "metrics": {}, "error": None, "is_research_result": False}
        record["manifest"]["provider_budget"] = asdict(self.provider.policy) if self.provider else {
            "max_requests": config.max_requests, "max_cost_usd": 0.0, "pricing": "synthetic mock; no billing"}
        self.repo.create(record, ACTOR, self.clock.now().isoformat())
        return record

    def cancel(self, run_id: str) -> None:
        stored = self.repo.get(run_id)
        if stored is None:
            raise KeyError(run_id)
        record, version = stored
        if record["status"] in ("QUEUED", "RUNNING"):
            self.cancelled.add(run_id)
            record["status"] = "CANCEL_REQUESTED"
            self.repo.save(record, expected_version=version, actor="console_operator", occurred_at=self.clock.now().isoformat())

    async def run(self, configuration: ResearchConfig, *, run_id: str | None = None) -> dict[str, Any]:
        if run_id is None:
            run_id = self.prepare(configuration)["run_id"]
        stored = self.repo.get(run_id)
        if stored is None:
            raise KeyError(run_id)
        record, version = stored
        if record["manifest"]["config"] != configuration.model_dump():
            raise ValueError("run configuration is immutable")
        if record["status"] not in ("QUEUED", "CANCEL_REQUESTED"):
            raise ValueError("experiments cannot be resumed or overwritten")
        provider = self.provider or GuardedProvider(MockLLMProvider(responder=rules_responder),
            BudgetPolicy(max_requests=configuration.max_requests), max_concurrency=configuration.max_concurrency)
        candidates = ScenarioGenerator().generate(configuration)
        rng = random.Random(configuration.seed)
        rng.shuffle(candidates)
        if configuration.kind == "scaling":
            # The same legitimate objective and template at each swarm size.
            template = next((c for c in candidates if c.template_id == "legitimate_collaboration_001"), candidates[0])
            candidates = [ScenarioDefinition.model_validate({**template.model_dump(), "agent_count": n,
                          "scenario_id": f"scaling_{n}_{t}"}) for t in range(configuration.trials) for n in (3, 5, 8, 12)]
        elif configuration.kind == "benchmark":
            candidates = [ScenarioDefinition.model_validate({**c.model_dump(), "scenario_id": f"{c.scenario_id}_t{t}"})
                          for t in range(configuration.trials) for c in candidates]
        rows: list[dict[str, Any]] = []
        novelty = NoveltyDetector()
        tested: dict[str, int] = {}
        record["status"] = "RUNNING"
        record["manifest"]["started_at"] = self.clock.now().isoformat()
        record["pool_generated"] = len(candidates)

        def save() -> None:
            nonlocal version
            # A cancel request may have advanced the CAS version at an await boundary.
            current = self.repo.get(run_id)
            if current:
                version = current[1]
            usage = provider.ledger.snapshot()
            record["usage"] = {**usage, "model_calls": usage["attempts"], "token_usage": "unavailable" if usage["calls_without_usage"] else "available",
                "estimated_cost_usd": 0.0 if provider.is_synthetic else usage["spent_usd_conservative"],
                "is_synthetic": provider.is_synthetic}
            if usage["calls_without_usage"]:
                record["usage"]["input_tokens"] = record["usage"]["output_tokens"] = None
            record["metrics"] = summarize_trials(rows)
            version = self.repo.save(record, expected_version=version, actor=ACTOR, occurred_at=self.clock.now().isoformat())

        save()
        in_flight: ScenarioDefinition | None = None
        try:
            async with asyncio.timeout(configuration.max_runtime_seconds):
                for iteration in range(1, configuration.max_iterations + 1):
                    await asyncio.sleep(0)
                    if run_id in self.cancelled:
                        record["status"] = "CANCELLED"
                        break
                    if not candidates:
                        break
                    if configuration.kind == "adaptive":
                        # Explore under-tested families first. Refine observed misses
                        # first within equal coverage, retaining all outcome dimensions.
                        misses = {r["definition"]["template_id"] for r in rows if r.get("evaluation_valid")
                                  and r["ground_truth"]["label"] == "UNSAFE" and r["predictions"].get(configuration.monitor) is False}
                        candidates.sort(key=lambda c: (tested.get(c.template_id, 0), c.template_id not in misses))
                        if iteration % 2 == 0 and rows:
                            last_template = rows[-1]["definition"]["template_id"]
                            refinements = [c for c in candidates if c.parent_scenario_id and c.template_id == last_template]
                            if refinements:
                                # Refine after observed feedback, alternating with
                                # coverage exploration; no scalar attack reward.
                                chosen = refinements[0]
                                candidates.remove(chosen)
                                candidates.insert(0, chosen)
                    definition = candidates.pop(0)
                    in_flight = definition
                    if configuration.kind == "adaptive" and configuration.execution_mode == "openai":
                        definition = await ScenarioGenerator().propose(configuration, provider, definition,
                            [{"prohibited_outcome": r["feedback"]["prohibited_outcome_occurred"],
                              "monitor_detected": r["feedback"]["monitor_detected"]} for r in rows[-3:]])
                    record["iteration"] = iteration
                    record["generated"] += 1
                    errors = validate_candidate(definition, configuration)
                    if errors:
                        record["rejected"] += 1
                        row = {"scenario_id": definition.scenario_id, "definition": definition.model_dump(),
                               "validation": {"validated": False, "errors": errors}, "evaluation_valid": False}
                    else:
                        record["validated"] += 1
                        row = await evaluate_candidate(definition, configuration, provider, f"{run_id}_wf_{iteration:03d}")
                        row["interventions"] = await intervention_comparison(definition) if row["evaluation_valid"] else []
                        row["novelty"] = novelty.classify(row["actions"], row["ground_truth"]["violated_invariants"])
                        row["failures"] = analyze_failures(row)
                        record["executed"] += 1
                        record["failed"] += not row["evaluation_valid"]
                        record["monitoring_failures"] += len(row["failures"])
                        record["gateway_blocks"] += row["feedback"]["gateway_blocks"]
                        record["prohibited_outcomes"] += row["feedback"]["prohibited_outcome_occurred"] is True
                        tested[definition.template_id] = tested.get(definition.template_id, 0) + 1
                        rows.append(row)
                        if configuration.kind == "adaptive":
                            variations = ScenarioMutator().variations(definition, iteration)
                            candidates.extend(variations)
                            record["pool_generated"] += len(variations)
                    row["recorded_at"] = self.clock.now().isoformat()
                    row["candidate_record_id"] = f"iteration_{iteration:03d}"
                    self.repo.add_candidate(run_id, row["candidate_record_id"], row)
                    in_flight = None
                    save()
                    if provider.exhausted:
                        record["status"], record["error"] = "BUDGET_EXHAUSTED", "Provider request or spending limit exhausted"
                        break
                else:
                    record["stop_reason"] = "MAX_ITERATIONS"
            if record["status"] == "RUNNING":
                record["status"] = "COMPLETED"
        except TimeoutError:
            record["status"], record["error"] = "TIMED_OUT", "Maximum runtime exhausted; in-flight candidate was not scored"
        except asyncio.CancelledError:
            record["status"] = "INTERRUPTED"
            raise
        except Exception as exc:
            record["status"], record["error"] = "FAILED", f"{type(exc).__name__}: {str(exc)[:400]}"
        finally:
            if in_flight is not None:
                self.repo.add_candidate(run_id, f"iteration_{record['iteration']:03d}", {
                    "scenario_id": in_flight.scenario_id, "definition": in_flight.model_dump(),
                    "evaluation_valid": False, "validation": {"validated": False, "errors": ["evaluation interrupted before scoring"]},
                    "error": record["error"] or record["status"], "recorded_at": self.clock.now().isoformat()})
                record["failed"] += 1
            record["manifest"]["completed_at"] = self.clock.now().isoformat()
            record["unrun_candidates"] = len(candidates)
            save()
            self.cancelled.discard(run_id)
        return record
