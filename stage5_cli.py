"""Local Stage 5 entry-point wiring; live networking remains outside swarmguard/."""
from __future__ import annotations
import argparse
import asyncio
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import os
from typing import Any
from swarmguard.adversarial.adaptive_controller import AdaptiveController
from swarmguard.evaluation.experiment_manifest import export_results
from swarmguard.llm.configuration import LLMSettings, load_env_file
from swarmguard.models.adversarial import ResearchConfig, MONITORS
from swarmguard.storage.database import Database


def revision() -> str:
    try:
        head = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL).strip()
        dirty = subprocess.check_output(["git", "status", "--porcelain"], text=True)
        return head + ("-dirty" if dirty else "")
    except (OSError, subprocess.CalledProcessError):
        return "unavailable"


def parser(kind: str) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=f"Stage 5 {kind}: synthetic research, mock by default")
    p.add_argument("--provider", choices=["mock", "openai"], default=os.environ.get("SWARMGUARD_STAGE5_MODE", "mock"))
    p.add_argument("--authorize-live", action="store_true")
    p.add_argument("--suite", choices=["development", "adaptive_search", "held_out"], default="adaptive_search")
    p.add_argument("--monitor", choices=MONITORS)
    p.add_argument("--agent-count", type=int, choices=[3, 5, 8, 12], default=3)
    p.add_argument("--trials", type=int, default=1)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--max-iterations", type=int, default=8 if kind == "benchmark" else 4)
    p.add_argument("--max-steps", type=int, default=60)
    p.add_argument("--output", type=Path, default=Path("results/stage5"))
    p.add_argument("--max-requests", type=int, default=200 if kind == "benchmark" else 100)
    p.add_argument("--max-cost", type=float, default=0.0)
    return p


async def execute(kind: str, args: argparse.Namespace) -> dict[str, Any]:
    config = ResearchConfig(kind=kind, execution_mode=args.provider, live_authorized=args.authorize_live,
        partition=args.suite, monitor=args.monitor or "GLOBAL_MONITOR", compare_all=args.monitor is None,
        agent_count=args.agent_count, trials=args.trials, seed=args.seed, max_iterations=args.max_iterations,
        max_steps=args.max_steps, max_requests=args.max_requests, max_cost_usd=args.max_cost)
    provider = None
    if args.provider == "openai":
        # ResearchConfig requires explicit opt-in before a provider can be built.
        load_env_file()
        from swarmguard_providers.factory import build_provider
        settings = replace(LLMSettings.from_env(provider="openai"), max_requests=config.max_requests,
                           max_cost_usd=config.max_cost_usd, max_concurrency=config.max_concurrency, max_retries=0)
        if not settings.has_pricing:
            raise ValueError("Stage 5 live runs require explicit input/output prices for pre-request cost reservation")
        _, provider = build_provider(settings)
    args.output.mkdir(parents=True, exist_ok=True)
    db = Database(args.output / "research.db")
    try:
        controller = AdaptiveController(db, revision=revision(), provider=provider)
        result = await controller.run(config)
        out = export_results(controller.repo, result["run_id"], args.output)
        print("SYNTHETIC MOCK PIPELINE VALIDATION" if args.provider == "mock" else "LIVE MODEL EVALUATION ON SYNTHETIC TEMPLATES")
        print(json.dumps({k: result[k] for k in ("run_id", "status", "generated", "validated", "executed", "rejected",
                         "failed", "monitoring_failures", "gateway_blocks", "prohibited_outcomes", "usage")}, indent=2))
        print(json.dumps(result["metrics"]["detection"], indent=2))
        print(f"Results: {out}")
        return result
    finally:
        db.close()


def main(kind: str) -> None:
    args = parser(kind).parse_args()
    try:
        result = asyncio.run(execute(kind, args))
    except (ValueError, OSError) as exc:
        raise SystemExit(str(exc)) from None
    if result["status"] != "COMPLETED" or result["failed"]:
        raise SystemExit(1)
