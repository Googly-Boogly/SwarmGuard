"""Measured adaptive, paired robustness and 3/5/8/12 participant demonstration."""
from __future__ import annotations
import asyncio
import argparse
import json
from stage5_cli import parser, execute


async def demo(args: argparse.Namespace) -> bool:
    adaptive = await execute("adaptive", args)
    # Scaling exercises the same public service objective, not more privilege.
    scaling = await execute("scaling", args)
    passed = all(r["status"] == "COMPLETED" and r["executed"] > 0 and r["failed"] == 0 for r in (adaptive, scaling))
    adaptive_rows = json.loads((args.output / adaptive["run_id"] / "candidates.json").read_text())
    scaling_rows = json.loads((args.output / scaling["run_id"] / "candidates.json").read_text())
    print("Measured original versus incomplete-context global-monitor comparisons:")
    for candidate in adaptive_rows:
        variants = {v["metadata"]["variant"]: v["assessment"]["recommendation"]
                    for v in candidate.get("robustness", []) if v["monitor"] == "GLOBAL_MONITOR"}
        print(json.dumps({"scenario_id": candidate["scenario_id"], "original": variants.get("original"),
                          "incomplete": variants.get("incomplete"),
                          "recommendation_changed": variants.get("original") != variants.get("incomplete")}))
    print("Measured scaling workload and communication:")
    for candidate in scaling_rows:
        print(json.dumps(candidate.get("scaling", {})))
    print(f"Stage 5 demonstration check: {'PASS' if passed else 'FAIL'}")
    return passed


if __name__ == "__main__":
    args = parser("demo").parse_args()
    if not asyncio.run(demo(args)):
        raise SystemExit(1)
