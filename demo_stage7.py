"""Stage 7 demonstration with real, separate processes on loopback (no containers required).

Processes: protected executor (uvicorn), capability gateway (uvicorn), untrusted agent runtime (separate
Python process with a scrubbed environment holding only its own credential), and this trusted host,
which plays administrator and the simulated human approver. Mock tools only; no real email.

    python demo_stage7.py [--database postgresql://...] [--keep]
"""
from __future__ import annotations
import argparse
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any
import httpx

ROOT = Path(__file__).resolve().parent
PY = sys.executable
APPROVER = "operator_alice"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def cli(*args: str, env: dict[str, str] | None = None) -> str:
    out = subprocess.run([PY, str(ROOT / "stage7_cli.py"), *args], cwd=ROOT, capture_output=True, text=True,
                         env=env or os.environ.copy(), timeout=120)
    if out.returncode:
        raise RuntimeError(f"stage7_cli {args[0]} failed: {out.stderr[-800:]}")
    return out.stdout


def start_service(module: str, port: int, env: dict[str, str], log: Path) -> subprocess.Popen:
    handle = log.open("w")
    return subprocess.Popen([PY, "-m", "uvicorn", f"{module}:app", "--host", "127.0.0.1", "--port", str(port),
                             "--workers", "1", "--no-access-log"], cwd=ROOT, env=env, stdout=handle, stderr=subprocess.STDOUT)


def wait_ready(url: str, timeout: float = 30) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last: Any = None
    while time.monotonic() < deadline:
        try:
            r = httpx.get(url, timeout=2)
            last = r.json()
            if r.status_code == 200:
                return last
        except (httpx.TransportError, ValueError):
            pass
        time.sleep(0.3)
    raise RuntimeError(f"service not ready: {url} {last}")


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def run(args: argparse.Namespace) -> list[tuple[str, bool, str]]:
    work = Path(tempfile.mkdtemp(prefix="swarmguard-stage7-"))
    secrets_dir, agent_dir = work / "secrets", work / "agent"
    agent_dir.mkdir()
    database = args.database or str(work / "stage7.db")
    checks: list[tuple[str, bool, str]] = []
    def check(name: str, ok: bool, detail: str) -> None:
        checks.append((name, bool(ok), detail))
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {detail}")
    base_env = {k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "LANG", "PYTHONPATH", "VIRTUAL_ENV",
                                                              "SWARMGUARD_DB_PASSWORD_FILE")}
    base_env["PYTHONPATH"] = str(ROOT)
    procs: dict[str, subprocess.Popen] = {}
    try:
        section("Trusted setup (host administrator)")
        cli("init-secrets", "--dir", str(secrets_dir))
        print(" ", cli("migrate", "--database", database).strip())
        trusted = {**base_env, "SWARMGUARD_DATABASE_URL": database, "SWARMGUARD_APPROVER_IDS": APPROVER,
                   "SWARMGUARD_APPROVAL_KEY_FILE": str(secrets_dir / "approval_signing.key"), "SWARMGUARD_HEARTBEAT_SECONDS": "2",
                   "SWARMGUARD_DB_BUSY_TIMEOUT_MS": "2000"}
        print(" ", cli("bootstrap", "--database", database, "--approval-key", str(secrets_dir / "approval_signing.key"),
                       "--approvers", APPROVER, env=trusted).strip())
        ex_port, gw_port = free_port(), free_port()
        executor_env = {**trusted, "SWARMGUARD_GRANT_PUBLIC_KEY_FILE": str(secrets_dir / "executor_grant.pub")}
        gateway_env = {**trusted, "SWARMGUARD_IDENTITY_PUBLIC_KEY_FILE": str(secrets_dir / "identity_issuer.pub"),
                       "SWARMGUARD_GRANT_PRIVATE_KEY_FILE": str(secrets_dir / "executor_grant.key"),
                       "SWARMGUARD_EXECUTOR_URL": f"http://127.0.0.1:{ex_port}", "SWARMGUARD_EXECUTOR_TIMEOUT": "2"}
        procs["executor"] = start_service("swarmguard_services.executor_main", ex_port, executor_env, work / "executor.log")
        procs["gateway"] = start_service("swarmguard_services.gateway_main", gw_port, gateway_env, work / "gateway.log")
        print("  executor ready:", wait_ready(f"http://127.0.0.1:{ex_port}/health/ready")["checks"])
        print("  gateway ready: ", wait_ready(f"http://127.0.0.1:{gw_port}/health/ready")["checks"])
        token_file = agent_dir / "token"
        cli("issue-token", "--issuer-key", str(secrets_dir / "identity_issuer.key"), "--agent", "operations_agent",
            "--runtime", "runtime_alpha", "--ttl-seconds", "900", "--out", str(token_file))

        section("Demo A/B/C/D: untrusted agent runtime (separate process, scrubbed environment)")
        agent_env = {"PATH": base_env["PATH"], "PYTHONPATH": str(ROOT), "SWARMGUARD_GATEWAY_URL": f"http://127.0.0.1:{gw_port}",
                     "SWARMGUARD_AGENT_TOKEN_FILE": str(token_file), "SWARMGUARD_WORKFLOW_ID": "wf_remote",
                     "SWARMGUARD_EXECUTOR_PROBE_URL": f"http://127.0.0.1:{ex_port}", "SWARMGUARD_APPROVAL_WAIT": "30"}
        agent = subprocess.Popen([PY, "-m", "swarmguard_agent.runtime"], cwd=ROOT, env=agent_env, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True)
        steps: dict[str, dict[str, Any]] = {}
        assert agent.stdout is not None
        for line in agent.stdout:
            step = json.loads(line)
            steps[step["step"]] = step
            print("  agent>", line.strip())
            if step["step"] == "awaiting_human_approval" and step.get("approval_id"):
                out = cli("approve", "--database", database, "--approval-key", str(secrets_dir / "approval_signing.key"),
                          "--approvers", APPROVER, "--approval", step["approval_id"], "--approver", APPROVER, env=trusted)
                print("  host> simulated human reviewed exact operation and approved:", out.strip().splitlines()[-1])
        agent.wait(timeout=60)
        s = steps
        check("A authorized execution", s["send_with_approval"]["decision"] == "ALLOW" and s["send_with_approval"]["executed"],
              f"{s['send_with_approval']['decision']} executed={s['send_with_approval']['executed']} "
              f"state={s['send_with_approval']['execution_state']}")
        b = s["send_without_approval"]
        check("B missing approval not executed", not b["executed"] and b["reason_code"] == "APPROVAL_REQUIRED",
              f"Decision: {b['decision']} Reason: {b['reason_code']} Executed: {b['executed']}")
        f = s["send_with_forged_approval"]
        check("B forged approval blocked", f["decision"] == "BLOCK" and not f["executed"], f"{f['decision']} {f['reason_code']}")
        c = s["direct_executor_access"]
        check("C direct executor access denied", c.get("http") == 401 or not c.get("reachable"),
              f"ACCESS DENIED (HTTP {c.get('http')}, {c.get('reason_code') or c.get('error')}); "
              "loopback demo: rejected by grant verification; compose: also network-unreachable")
        d1, d2 = s["send_with_approval"], s["approval_replay_new_request"]
        check("D approval replay", d1["decision"] == "ALLOW" and d2["decision"] == "BLOCK" and not d2["executed"],
              f"first: {d1['decision']}; second: {d2['decision']} {d2['reason_code']}")
        r = s["idempotent_retry_same_request"]
        check("D idempotent retry not re-executed", r["idempotent_replay"] and r["executed"], "stored result returned")
        sp = s["identity_spoof_in_body"]
        check("identity field in body rejected", sp["http"] == 422 and not sp["executed"], f"HTTP {sp['http']} {sp['reason_code']}")
        env_report = s["agent_environment"]
        check("agent environment holds no trusted secrets", env_report["secret_like_variables"] == [],
              f"secret-like env vars: {env_report['secret_like_variables']}")

        section("Demo E: audit integrity")
        from swarmguard.audit.verification import verify_chain
        from swarmguard.storage.database import Database
        db = Database(database, migrate=False)
        report = verify_chain(db)
        check("E chain verifies", report.status == "PASS", f"{report.chained_events} chained events, head {report.head_hash[:16]}…")
        emails = db.fetchall("SELECT recipient, status FROM sent_emails")
        check("exactly one simulated email, no real delivery", len(emails) == 1 and emails[0][1] == "SIMULATED_EMAIL_SENT",
              f"{[tuple(e) for e in emails]}")
        if db.engine == "sqlite":
            isolated = work / "isolated_copy.db"
            shutil.copy(database, isolated)
            copy = Database(isolated, migrate=False)
            copy.execute("UPDATE events SET summary = 'tampered: nothing was sent' WHERE event_type = 'EMAIL_SENT_SIMULATED'")
            tampered = verify_chain(copy)
            check("E tampering detected on isolated copy", tampered.status == "FAIL",
                  f"{tampered.status}: {tampered.failures[0]['kind']} at seq {tampered.failures[0]['seq']}")
            copy.close()

        section("Demo F: service failures fail closed")
        token = token_file.read_text().strip()
        def request(rid: str) -> tuple[int, dict[str, Any]]:
            try:
                resp = httpx.post(f"http://127.0.0.1:{gw_port}/api/v1/capabilities/execute", timeout=15,
                                  headers={"Authorization": f"Bearer {token}"},
                                  json={"request_id": rid, "workflow_id": "wf_remote", "tool": "read_document",
                                        "resource_id": "doc_service_faq"})
                return resp.status_code, resp.json()
            except httpx.TransportError as exc:
                return 0, {"error": type(exc).__name__}
        if db.engine == "sqlite":
            blocker = sqlite3.connect(database, isolation_level=None)
            blocker.execute("BEGIN EXCLUSIVE")   # authorization state store becomes unavailable
            code, body = request("f_db")
            blocker.execute("ROLLBACK"); blocker.close()
            check("F authorization store unavailable", code == 503 and body.get("reason_code") == "AUTHORIZATION_SERVICE_UNAVAILABLE"
                  and body.get("executed") is False, f"HTTP {code} {body.get('reason_code')} executed={body.get('executed')}")
        procs["executor"].terminate(); procs["executor"].wait(timeout=10)
        code, body = request("f_exec")
        check("F executor down", body.get("decision") == "BLOCK" and body.get("reason_code") == "EXECUTOR_UNAVAILABLE"
              and body.get("executed") is False, f"HTTP {code} {body.get('decision')} {body.get('reason_code')}")
        procs["gateway"].terminate(); procs["gateway"].wait(timeout=10)
        code, body = request("f_gw")
        check("F gateway down: no alternate path", code == 0, f"agent request failed: {body.get('error')}")
        count = len(Database(database, migrate=False).fetchall("SELECT * FROM sent_emails"))
        check("no additional execution during failures", count == 1, f"simulated emails: {count}")
    finally:
        for proc in procs.values():
            if proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=10)
        if not args.keep:
            shutil.rmtree(work, ignore_errors=True)
        else:
            print(f"\nArtifacts kept in {work}")
    return checks


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--database", help="PostgreSQL URL (password via SWARMGUARD_DB_PASSWORD_FILE); default temp SQLite")
    p.add_argument("--keep", action="store_true", help="keep logs, secrets and databases of the run")
    checks = run(p.parse_args())
    ok = all(c[1] for c in checks)
    print(f"\n{sum(c[1] for c in checks)}/{len(checks)} checks passed")
    print(f"Stage 7 demonstration check: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
