# Production Readiness Assessment (Stage 7)

**Verdict: not production-ready.** Stage 7 passes its own tests and a disposable local deployment
verification. That establishes that the implemented controls behave as tested under the tested
conditions, not that the system is secure against unknown attacks. Independent security review,
penetration testing and deployment validation in the target environment are required first.

## Implemented and tested

| Control | Evidence |
|---|---|
| Separate untrusted agent runtime, gateway, executor, control service, database | Compose deployment, 40/40 deployment checks |
| Network isolation (agent ↛ executor/database/api/internet) | probes from inside containers |
| EdDSA agent credentials: signature, audience, issuer, expiry, lifetime, type, revocation, binding | `tests/test_stage7.py` |
| Server-side role/workflow/capability resolution; spoofing and escalation rejected | tests + demo |
| Gateway-signed, durably bound execution grants; direct executor access denied | tests, demo, deployment |
| Idempotency, duplicate suppression, concurrent single-use approval consumption | tests (SQLite, PostgreSQL, multi-connection) |
| Ambiguous outcomes → UNKNOWN, never retried; restart recovery without replay | fault-injection tests |
| Fail closed: DB down/locked, policy exception, executor down, gateway down | tests, demo, deployment |
| Approval revocation, expiry, integrity tamper detection, cross-workflow misuse | tests |
| Operator roles (RESEARCHER/OPERATOR/ADMINISTRATOR/AUDITOR), CSRF, Origin, sessions, rate limits, audit of logins | `tests/test_stage7_console.py`, Playwright |
| Emergency stop (gateway + executor) with confirmed recovery | tests, Playwright, deployment |
| Tamper-evident audit chain + external checkpoints | tests, `verify_stage7.py --audit`, deployment |
| PostgreSQL backend, forward migrations, least-privilege roles | opt-in tests against PostgreSQL 17.6 |
| Hardened containers (non-root, read-only, cap_drop ALL, no-new-privileges, limits, health checks) | `docker inspect` checks |

## Implemented, partially verified or unverified

* `.github/workflows/swarmguard-safety.yml` Stage 7 steps — not executed on GitHub.
* Connection pooling — **not implemented** (one connection per service process, single worker).
* MFA — **not implemented**. Password login with scrypt hashes only.
* mTLS / TLS on internal channels — **not implemented**.
* Credential refresh flow — not implemented (issue a new short-lived token from the host CLI).
* Secret rotation — procedure documented (DEPLOYMENT.md); not automated or tested end-to-end.
* Backup/restore — documented, not tested.
* Multi-replica operation — not supported (per-process rate limits, process-local sessions).

## Scan results (Stage 7 run)

* Bandit: 0 high; medium findings are dynamic SQL with allowlisted identifiers and bound values (triaged);
  low findings are asserts/pseudo-random in non-security code.
* pip-audit: no known vulnerabilities in the pinned service dependencies; the development integration venv
  contains `chromadb` 1.5.9 advisories (CrewAI transitive, not in any image, no fix version listed).
* npm audit: 0 vulnerabilities. Gitleaks: no leaks in git history; one false positive (type annotation).
* Trivy images: backend and agent 0 CRITICAL, 49 HIGH Debian packages with no available fix; frontend 2 HIGH
  in the Caddy binary (one fixable only by an upstream rebuild). Trivy config: 0 Dockerfile misconfigurations.
* A clean scan does not establish security.

## Required before production

1. Independent security review and penetration test of gateway, executor, control service and deployment.
2. Asymmetric approval signatures; HSM/KMS-held signing keys; key rotation.
3. mTLS (or a service mesh) on all internal channels; TLS at the edge.
4. Orchestrator secret store with per-service file ownership; no host bind-mounted secrets.
5. Real executors with their own credentials, outbox/transactional-messaging for external effects.
6. Shared rate limiting and sessions for multiple replicas; connection pooling.
7. MFA through an established identity provider (OIDC) for operators.
8. Off-host, append-only audit storage or a transparency log for checkpoints.
9. Monitoring/alerting integration and tested incident runbooks; backup/restore drills.
10. Image patch pipeline (the remaining HIGH OS findings) and dependency update policy.
