"""No-cost final laboratory CLI. Network providers are never imported by this command."""
from __future__ import annotations
import argparse
import asyncio
import json
import subprocess
from pathlib import Path
from swarmguard.datasets.registry import DatasetDefinition
from swarmguard.research.experiment_designer import ExperimentConfig
from swarmguard.research.export import export_bundle, verify_bundle
from swarmguard.research.laboratory import Laboratory
from swarmguard.storage.database import Database


def revision() -> str:
    try:
        sha = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL).strip()
        dirty = subprocess.check_output(["git", "status", "--porcelain"], text=True)
        return sha + ("-dirty" if dirty else "")
    except (OSError, subprocess.CalledProcessError):
        return "unavailable"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=["mock"], default="mock")
    parser.add_argument("--question", default="RQ1")
    parser.add_argument("--dataset", default="swarmguard_adaptive_search")
    parser.add_argument("--dataset-version", default="1.0")
    parser.add_argument("--monitor", default="GLOBAL_MONITOR")
    parser.add_argument("--framework", default="native")
    parser.add_argument("--mode", default="SCRIPTED")
    parser.add_argument("--trials", type=int, default=1)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--max-steps", type=int, default=60)
    parser.add_argument("--max-requests", type=int, default=500)
    parser.add_argument("--max-cost", type=float, default=0.0)
    parser.add_argument("--output", type=Path, default=Path("results/stage8"))
    parser.add_argument("--config", type=Path)
    parser.add_argument("--import-dataset", type=Path)
    parser.add_argument("--verify-bundle", type=Path)
    args = parser.parse_args()
    if args.verify_bundle:
        result = verify_bundle(args.verify_bundle)
        print(json.dumps(result, indent=2))
        return 0 if result["valid"] else 1
    cfg = json.loads(args.config.read_text()) if args.config else {
        k: getattr(args, k) for k in ("question", "dataset", "dataset_version", "monitor", "framework", "mode",
                                      "trials", "seed", "max_steps", "max_requests", "max_cost")}
    config = ExperimentConfig.model_validate(cfg)
    args.output.mkdir(parents=True, exist_ok=True)
    db = Database(args.output / "laboratory.db")
    try:
        lab = Laboratory(db, revision=revision())
        if args.import_dataset:
            raw = json.loads(args.import_dataset.read_text())
            definition = DatasetDefinition.model_validate(raw.get("definition", raw))
            if not lab.repo.get("dataset", f"{definition.dataset_id}@{definition.version}"):
                imported = lab.datasets.register(definition, "local_researcher")
                if raw.get("content_hash") and imported["content_hash"] != raw["content_hash"]:
                    raise ValueError("dataset hash differs from recorded source; use the recorded code revision")
        lab.datasets.seed()
        record = lab.prepare(config)
        result = asyncio.run(lab.run(record["experiment_id"]))
        destination = export_bundle(result, args.output / result["experiment_id"])
        print(json.dumps({"experiment_id": result["experiment_id"], "status": result["status"],
            "execution_mode": config.mode, "validation": result["validation"], "metrics": result["metrics"]["detection"],
            "accounting": result["metrics"]["accounting"], "bundle": str(destination),
            "synthetic": True, "live_model_calls": 0}, indent=2))
        return 0 if result["status"] == "COMPLETED" and verify_bundle(destination)["valid"] else 2
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
