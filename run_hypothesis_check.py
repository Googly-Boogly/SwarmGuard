"""Compute pre-registered hypothesis verdicts for a stored RQ1 experiment, read-only.

Examples:
  python run_hypothesis_check.py --snapshot research/rq1/experiment_006_snapshot.json
  python run_hypothesis_check.py --db backup_of_console.db --experiment rq1_experiment_008
  python run_hypothesis_check.py --db backup_of_console.db --all

The database is opened with ``mode=ro``. Nothing is rerun and no record is changed. ``--output`` is an exclusive
write: an existing file is never overwritten. Exit status: 0 = verdicts computed, 1 = input error.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from swarmguard.research.hypotheses import evaluate_experiment


def _from_db(path: Path, eid: str | None) -> list[dict[str, Any]]:
    from swarmguard.research.rq1_lab import EXPERIMENT_KIND, RESULT_KIND
    from swarmguard.storage.database import Database
    from swarmguard.storage.repositories import PlatformRepository
    if not path.exists():
        raise SystemExit(f"database not found: {path}")
    db = Database(path, read_only=True)
    try:
        repo = PlatformRepository(db)
        ids = [eid] if eid else sorted(r["experiment_id"] for r in repo.list(EXPERIMENT_KIND, 1000))
        out = []
        for i in ids:
            record, result = repo.get(EXPERIMENT_KIND, i), repo.get(RESULT_KIND, i)
            if not record:
                raise SystemExit(f"experiment not found: {i}")
            if not result:
                out.append({"experiment_id": i, "status": record[0]["status"], "verdicts": None,
                            "primary_summary": "no stored result; nothing to evaluate"})
                continue
            out.append(evaluate_experiment(record[0], result[0]))
        return out
    finally:
        db.close()


def _print(report: dict[str, Any]) -> None:
    print(f"\n{report['experiment_id']}: {report['primary_summary']}")
    for v in report.get("verdicts") or []:
        print(f"  [{v['status']}] {v['hypothesis_id']} ({v['role']})"
              + ("" if v["source_document_unchanged"] else "  ! source protocol document changed since transcription"))
        for r in v["applicability_reasons"]:
            print(f"      not applicable: {r}")
        for c in v["conditions"]:
            print(f"      {c['id']} {c['status']:<9} {c['text']}: {c['detail']}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--snapshot", type=Path, help="public snapshot JSON with 'experiment' and 'result'")
    src.add_argument("--db", type=Path, help="console SQLite database (opened read-only)")
    ap.add_argument("--experiment", help="experiment ID (with --db)")
    ap.add_argument("--all", action="store_true", help="every experiment in the database (with --db)")
    ap.add_argument("--output", type=Path, help="write the full JSON report here (must not exist)")
    args = ap.parse_args(argv)
    if args.db and not (args.experiment or args.all):
        ap.error("--db needs --experiment or --all")
    if args.snapshot:
        snap = json.loads(args.snapshot.read_text())
        reports = [evaluate_experiment(snap["experiment"], snap["result"])]
    else:
        reports = _from_db(args.db, None if args.all else args.experiment)
    for r in reports:
        _print(r)
    if args.output:
        with args.output.open("x") as fh:
            json.dump(reports if len(reports) > 1 else reports[0], fh, indent=2, sort_keys=True)
        print(f"\nwrote {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
