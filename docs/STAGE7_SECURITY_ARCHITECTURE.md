# Stage 7 Security Architecture

Goal: an agent running in a separate, untrusted runtime can *request* a protected operation but cannot
execute it directly or modify trusted authorization state. **This is a hardened research deployment, not
a production-secure system.** See [PRODUCTION_READINESS.md](PRODUCTION_READINESS.md).

## Services and trust zones

| Service (container) | Trust | Holds | Network |
|---|---|---|---|
| `agent-runtime` | **untrusted** | its own short-lived agent credential (one file) | `agent_net` only |
| `gateway` | trusted | identity **public** key, executor-grant **private** key, approval verification key, `sg_gateway` DB credential | `agent_net`, `exec_net`, `data` |
| `executor` | trusted | executor-grant **public** key, approval verification key, `sg_executor` DB credential | `exec_net`, `data` |
| `api` (control service = console + control plane) | trusted | operator password hashes, approver credentials (in memory), approval key, `sg_control` DB credential | `edge`, `data` |
| `database` (PostgreSQL 17) | trusted | data; per-service roles | `data` |
| `frontend` (Caddy + static bundle) | public edge | nothing secret | `edge` (published on 127.0.0.1:8080 only) |
| `migrate` (one-shot) | trusted | owner DB credential | `data` |

The **identity issuer private key never enters any container**: credentials are issued on the host
(`stage7_cli.py issue-token`). `agent_net`, `exec_net` and `data` are Docker `internal` networks (no
egress). Isolation was verified from inside containers (`deployment/scripts/verify_deployment.py`): the
agent cannot resolve or reach `executor`, `database`, `api` or the internet; the gateway reaches the
executor; no trusted service reaches the internet.

## Request path

```
agent-runtime --(HTTPS-less HTTP on internal agent_net, Bearer EdDSA credential)--> gateway
  1 verify credential: signature (Ed25519), iss, aud=swarmguard-gateway, exp/nbf, lifetime <= 24h, typ
  2 revocation: credential id, runtime, agent binding; binding.runtime == token.rt
  3 per-agent rate limit, concurrency limit, body size limit
  4 strict schema (agent_id / role / authorization fields => 422 + persisted BLOCK)
  5 binding scope: workflow in binding.workflows, tool in binding.capabilities (least privilege)
  6 emergency stop / disabled capability
  7 idempotency: UNIQUE(agent_id, request_id); same digest => stored result; different => BLOCK
  8 unchanged CapabilityGateway: membership, role permissions, resource ACL/version, approvals, policy,
    optional council / WorkflowGuard (ALLOW -> ESCALATE only)
  9 PENDING -> AUTHORIZED (durable), sign 30 s grant {op, act, digest}
gateway --(exec_net, Bearer grant)--> executor
 10 verify grant (Ed25519, aud=swarmguard-executor, lifetime <= 120 s); load operation; must be AUTHORIZED,
    unexpired, digest matches stored action + expectations, persisted decision is ALLOW (record and column),
    workflow ACTIVE, no emergency stop, capability enabled, tool in static registry
 11 CAS AUTHORIZED -> EXECUTING (commit) ; then one transaction: tool (re-validates version, content,
    approval; consumes approval by conditional UPDATE) + CAS EXECUTING -> SUCCEEDED
```

Identity is never read from the request body. Role, workflows and capabilities come from the
`agent_bindings` row, never from token claims (extra claims such as `role` are ignored).

## Execution guarantees (what is and is not claimed)

* **At-most-once.** Redelivery of a grant is idempotent; replays return the stored state. Retries never
  create a second authorization. No automatic retry after an ambiguous outcome.
* For the simulated tools, effects and `SUCCEEDED` commit in one database transaction, so an operation's
  effect exists iff it is `SUCCEEDED`. For real external side effects this would **not** hold; the state
  after a crash between `EXECUTING` and completion is reported `UNKNOWN` and requires operator review.
* Gateway arbitration after a lost reply: `AUTHORIZED -> FAILED` (provably unconsumed) or
  `EXECUTING -> UNKNOWN`; a late success is reported as executed. Distributed exactly-once is not claimed.
* Restart recovery (gateway): `PENDING`/`AUTHORIZED` -> `FAILED` by CAS (an old grant can no longer be
  consumed). Executor: `EXECUTING` -> `UNKNOWN`. Nothing is re-executed.
* Cancellation policy: a paused/halted workflow's unstarted operations never start (`WORKFLOW_NOT_ACTIVE`);
  operations already `EXECUTING` run to completion.
* Emergency stop: checked at the gateway (before authorization) and at the executor (before consumption).
  It cannot undo completed effects.

## Fail-closed behaviour (tested)

| Failure | Result |
|---|---|
| authorization store unavailable / locked | HTTP 503, `AUTHORIZATION_SERVICE_UNAVAILABLE`, nothing executed (decision cannot be persisted; logged) |
| policy engine exception | `BLOCK INTERNAL_ERROR` |
| executor unreachable | `BLOCK EXECUTOR_UNAVAILABLE` (authorization cancelled by CAS) |
| executor crash mid-execution / lost reply | `UNKNOWN` (never `FAILED` unless provably unconsumed) |
| gateway down | no alternate path; the agent cannot reach the executor |
| revoked credential/runtime/agent | HTTP 401 |

## Database separation (PostgreSQL)

Roles: `sg_gateway`, `sg_executor`, `sg_control` (login, no superuser/DDL), owner (migrations only).
Grants (`swarmguard/storage/postgres_roles.py`): every role SELECTs; **no role may DELETE, run DDL or
UPDATE** `events`, `audit_chain`, `actions`, `resource_versions`, `operation_transitions`, `sent_emails`,
`revoked_credentials`; **only `sg_executor` may INSERT `sent_emails`**; only `sg_control` may write
identities and control state. Verified against a real PostgreSQL server (tests and deployment).

## Human approval

Approvals bind workflow, operation, exact protected parameters, resource ID and version, expiry, trusted
approver and approval ID; consumption is single-use by conditional UPDATE. Stage 7 adds `REVOKED` (from
`PENDING`/`APPROVED`; policy reason `APPROVAL_REVOKED`). In the deployment each OPERATOR account is its own
trusted approver identity; approver IDs must never coincide with agent identities (enforced at start-up and
binding). The control service has no in-process executor (`inprocess_execution=False`).

## Audit integrity

Every `events` row is chained (`audit_chain`: SHA-256 over canonical JSON of the stored row; link hash over
seq, event id, payload hash and previous hash) in the same transaction. Verification detects altered,
missing, reordered or unchained events, broken links, gaps and a truncated start. **A writer with database
access can recompute the whole chain**; only external checkpoints (files outside the audited database,
`stage7_cli.py checkpoint` or the console) detect that. Mutable state tables (decisions, approvals,
operations) are not chained; their changes are mirrored by chained events and versioned transitions.

## Known bypass risks

* **Approval integrity uses a symmetric HMAC key** shared by control, gateway and executor (all trusted). A
  compromised gateway or executor could forge an approval record. Asymmetric approval signatures (control
  signs, others verify) are the recommended next step.
* A runtime holding several agents' credentials can act as any of them (documented identity boundary).
* Internal channels are plain HTTP on isolated Docker networks; no mTLS. Grants are short-lived and bound
  to durable records, but a network attacker inside `exec_net` could observe them.
* In-process hardening (slotted clients etc.) is irrelevant against code running inside trusted services.
* Rate limits are per process; single worker per service.
* Database container starts as root with five capabilities and drops to the `postgres` user (image entrypoint).
* Secret files are bind-mounted with mode 0644 inside a 0700 host directory (non-root containers must read
  them); a real deployment should use an orchestrator secret store with per-service ownership.
