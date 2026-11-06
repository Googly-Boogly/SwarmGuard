"""Stage 7 verification.

  python verify_stage7.py --audit --database URL [--checkpoints DIR]   # audit chain only (read-only)
  python verify_stage7.py                                              # multi-process security checks
  python verify_stage7.py --deployment                                 # + hardened Docker deployment checks

Exit code 0 only when every executed check passed. Checks that could not run are reported, never passed.
"""
from __future__ import annotations
import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def audit(database: str, checkpoints: Path | None) -> int:
    from swarmguard.audit.verification import load_checkpoints, verify_chain
    from swarmguard.storage.database import Database
    db = Database(database, read_only=not database.startswith("postgres"), migrate=False)
    report = verify_chain(db, load_checkpoints(checkpoints))
    print(json.dumps({k: v for k, v in report.as_dict().items() if k != "failures"}, indent=2))
    for failure in report.failures:
        print(f"  {failure['kind']} at seq {failure['seq']}: {failure['detail']}")
    print(f"Audit verification: {report.status}")
    return 0 if report.status == "PASS" else 1


def tamper_self_test() -> bool:
    """Proves the verifier reports real failures (isolated temporary database only)."""
    from swarmguard.agents.simulator import SwarmSimulator
    from swarmguard.audit.verification import verify_chain
    with tempfile.TemporaryDirectory() as tmp:
        sim = SwarmSimulator(str(Path(tmp) / "audit_selftest.db"))
        sim.start_workflow("wf_selftest", "audit self-test")
        before = verify_chain(sim.db).status
        sim.db.execute("UPDATE events SET summary = 'tampered' WHERE seq = 2")
        after = verify_chain(sim.db).status
        sim.close()
    print(f"  audit self-test: untouched={before}, tampered={after}")
    return before == "PASS" and after == "FAIL"


def main() -> int:
    p = argparse.ArgumentParser(description="SwarmGuard Stage 7 verification")
    p.add_argument("--audit", action="store_true")
    p.add_argument("--database")
    p.add_argument("--checkpoints", type=Path)
    p.add_argument("--deployment", action="store_true", help="also verify the hardened Docker Compose deployment")
    args = p.parse_args()
    if args.audit:
        if not args.database:
            p.error("--audit requires --database")
        return audit(args.database, args.checkpoints)
    import demo_stage7
    results = demo_stage7.run(argparse.Namespace(database=args.database, keep=False))
    results.append(("audit verifier reports actual tampering", tamper_self_test(), "isolated database"))
    if args.deployment:
        proc = subprocess.run([sys.executable, str(ROOT / "deployment" / "scripts" / "verify_deployment.py")], cwd=ROOT)
        results.append(("hardened deployment verification", proc.returncode == 0, f"exit {proc.returncode}"))
    else:
        print("\n  NOT RUN: container isolation checks (pass --deployment; requires Docker)")
    ok = all(r[1] for r in results)
    print(f"\n{sum(r[1] for r in results)}/{len(results)} verification checks passed")
    print(f"Stage 7 verification: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
